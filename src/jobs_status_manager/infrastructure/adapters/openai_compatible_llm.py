"""Synchronous provider-neutral OpenAI-compatible LLM adapter."""

from __future__ import annotations

import json
from typing import Final, Unpack

import httpx2 as httpx
from pydantic import AnyHttpUrl, SecretStr, TypeAdapter, ValidationError

from jobs_status_manager.agent.contracts import (
    ConversationPrompt,
    ConversationResponse,
    PromptToolCall,
    PromptToolResult,
    ToolCallRequest,
)
from jobs_status_manager.agent.tools import TOOL_ARGUMENT_MODELS, definitions
from jobs_status_manager.infrastructure.adapters._openai_compatible_llm_types import (
    MAX_PROVIDER_CODE_CHARS as _MAX_PROVIDER_CODE_CHARS,
)
from jobs_status_manager.infrastructure.adapters._openai_compatible_llm_types import (
    JsonValue,
    LLMAdapterOptions,
    LLMAuthenticationError,
    LLMClientOptions,
    LLMConfigurationError,
    LLMContractError,
    LLMError,
    LLMMalformedResponseError,
    LLMProviderUnavailableError,
    LLMRateLimitError,
    LLMRequestRejectedError,
    LLMTimeoutError,
    LLMTransportError,
    ToolArguments,
    _Choice,
    _ErrorResponse,
    _Message,
    _Response,
)
from jobs_status_manager.mail import JobMailAnalysisInput

LLM_MODEL: Final = "deepseek-flash"
MAX_PROVIDER_CODE_CHARS: Final = _MAX_PROVIDER_CODE_CHARS
CONFIGURATION_KIND: Final = "configuration"
MAIL_ANALYSIS_KIND: Final = "mail_analysis"
CONVERSATION_KIND: Final = "conversation"
MAX_RETRY_AFTER_SECONDS: Final = 3600.0
HTTP_AUTH_STATUSES: Final = (401, 403)
HTTP_RATE_LIMIT_STATUS: Final = 429
HTTP_PROVIDER_FAILURE_MIN: Final = 500


def _endpoint(base_url: AnyHttpUrl | str) -> str:
    try:
        parsed = TypeAdapter(AnyHttpUrl).validate_python(base_url)
    except ValidationError:
        raise LLMConfigurationError(CONFIGURATION_KIND) from None
    if parsed.scheme.casefold() != "https" or any(
        (parsed.query, parsed.fragment, parsed.username, parsed.password)
    ):
        raise LLMConfigurationError(CONFIGURATION_KIND)
    normalized = str(parsed).rstrip("/")
    return (
        normalized if normalized.endswith("/chat/completions") else normalized + "/chat/completions"
    )


def _provider_error(response: httpx.Response) -> tuple[str | None, float | None]:
    try:
        parsed = _ErrorResponse.model_validate(response.json()).error
    except (TypeError, ValueError, ValidationError):
        parsed = None
    provider_code = None if parsed is None else parsed.code
    retry_after = None
    raw_retry_after = response.headers.get("retry-after")
    if raw_retry_after is not None:
        try:
            candidate = float(raw_retry_after)
        except ValueError:
            candidate = -1
        if 0 <= candidate <= MAX_RETRY_AFTER_SECONDS:
            retry_after = candidate
    return provider_code, retry_after


def _status_error(response: httpx.Response, request_kind: str) -> LLMError:
    provider_code, retry_after = _provider_error(response)
    if response.status_code in HTTP_AUTH_STATUSES:
        error_type = LLMAuthenticationError
    elif response.status_code == HTTP_RATE_LIMIT_STATUS:
        error_type = LLMRateLimitError
    elif response.status_code >= HTTP_PROVIDER_FAILURE_MIN:
        error_type = LLMProviderUnavailableError
    else:
        error_type = LLMRequestRejectedError
    return error_type(request_kind, response.status_code, provider_code, retry_after)


def _conversation_messages(prompt: ConversationPrompt) -> list[JsonValue]:
    messages: list[JsonValue] = [{"role": "system", "content": prompt.system_prompt[:12000]}]
    context = (
        ("session_summary", prompt.session_summary),
        ("active_application_id", prompt.active_application_id),
        ("active_mail_id", prompt.active_mail_id),
        ("active_knowledge_document_id", prompt.active_knowledge_document_id),
    )
    messages.extend(
        {"role": "system", "content": f"{label}: {value[:12000]}"}
        for label, value in context
        if value
    )
    for item in prompt.recent_messages:
        if item.role not in ("user", "assistant"):
            raise LLMContractError(CONVERSATION_KIND)
        messages.append({"role": item.role, "content": item.content[:12000]})
    messages.append({"role": "user", "content": prompt.user_message[:12000]})
    messages.extend(_continuation_messages(prompt.tool_calls, prompt.tool_results))
    return messages


def _continuation_messages(
    calls: tuple[PromptToolCall, ...],
    results: tuple[PromptToolResult, ...],
) -> list[JsonValue]:
    if not calls and not results:
        return []
    internal_ids = [call.internal_tool_call_id for call in calls]
    provider_ids = [call.provider_call_id for call in calls]
    sequences = [call.sequence for call in calls]
    assistant_sequences = list(dict.fromkeys(call.assistant_sequence for call in calls))
    result_ids = [result.internal_tool_call_id for result in results]
    if (
        len(set(internal_ids)) != len(internal_ids)
        or len(set(provider_ids)) != len(provider_ids)
        or len(set(sequences)) != len(sequences)
        or len(set(result_ids)) != len(result_ids)
        or set(result_ids) != set(internal_ids)
        or sequences != list(range(1, len(calls) + 1))
        or assistant_sequences != list(range(1, len(assistant_sequences) + 1))
    ):
        raise LLMContractError(CONVERSATION_KIND)
    result_by_call_id = {result.internal_tool_call_id: result for result in results}
    messages: list[JsonValue] = []
    group: list[PromptToolCall] = []
    assistant_sequence: int | None = None
    for call in calls:
        try:
            decoded = json.loads(call.arguments_json)
        except (TypeError, ValueError):
            raise LLMContractError(CONVERSATION_KIND) from None
        if (
            not isinstance(decoded, dict)
            or call.name not in TOOL_ARGUMENT_MODELS
            or not call.provider_call_id.strip()
        ):
            raise LLMContractError(CONVERSATION_KIND)
        if assistant_sequence is not None and call.assistant_sequence != assistant_sequence:
            messages.extend(_assistant_tool_messages(group, result_by_call_id))
            group = []
        assistant_sequence = call.assistant_sequence
        group.append(call)
    messages.extend(_assistant_tool_messages(group, result_by_call_id))
    return messages


def _assistant_tool_messages(
    calls: list[PromptToolCall],
    result_by_call_id: dict[str, PromptToolResult],
) -> list[JsonValue]:
    if not calls:
        return []
    messages: list[JsonValue] = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": call.provider_call_id,
                    "type": call.provider_type,
                    "function": {"name": call.name, "arguments": call.arguments_json},
                }
                for call in calls
            ],
        }
    ]
    messages.extend(
        {
            "role": "tool",
            "tool_call_id": call.provider_call_id,
            "content": result_by_call_id[call.internal_tool_call_id].data,
        }
        for call in calls
    )
    return messages


def _conversation_tools() -> list[JsonValue]:
    return [
        {
            "type": "function",
            "function": {
                "name": item.name,
                "description": item.description,
                "parameters": item.input_schema,
            },
        }
        for item in definitions()
        if item.name in TOOL_ARGUMENT_MODELS
    ]


def _parse_tool_response(message: _Message) -> ConversationResponse:
    if message.content is not None and message.content.strip():
        if message.tool_calls:
            raise LLMContractError(CONVERSATION_KIND)
        return ConversationResponse(answer=message.content)
    if not message.tool_calls:
        raise LLMContractError(CONVERSATION_KIND)
    provider_ids: set[str] = set()
    tool_calls: list[ToolCallRequest] = []
    for call in message.tool_calls:
        if (
            not call.id.strip()
            or call.id in provider_ids
            or call.type != "function"
            or call.function.name not in TOOL_ARGUMENT_MODELS
        ):
            raise LLMContractError(CONVERSATION_KIND)
        try:
            decoded = json.loads(call.function.arguments)
        except (TypeError, ValueError):
            raise LLMContractError(CONVERSATION_KIND) from None
        if not isinstance(decoded, dict):
            raise LLMContractError(CONVERSATION_KIND)
        try:
            arguments = TypeAdapter(ToolArguments).validate_python(decoded, strict=True)
            tool_call = ToolCallRequest.model_validate(
                {
                    "provider_call_id": call.id,
                    "provider_type": call.type,
                    "name": call.function.name,
                    "arguments": arguments,
                    "arguments_json": call.function.arguments,
                },
                strict=True,
            )
        except (TypeError, ValueError, ValidationError):
            raise LLMContractError(CONVERSATION_KIND) from None
        provider_ids.add(call.id)
        tool_calls.append(tool_call)
    return ConversationResponse(tool_calls=tuple(tool_calls))


class OpenAICompatibleLLM:
    """Shared synchronous Chat Completions client for mail and Conversation."""

    def __init__(
        self,
        base_url: AnyHttpUrl | str,
        api_key: SecretStr,
        *,
        client: httpx.Client | None = None,
        **provided: Unpack[LLMAdapterOptions],
    ) -> None:
        """Initialize one reusable synchronous provider client."""
        options: LLMClientOptions = provided.get("options", {})
        model = provided.get("model", options.get("model", LLM_MODEL))
        if model != LLM_MODEL:
            raise LLMConfigurationError(CONFIGURATION_KIND)
        self._endpoint = _endpoint(base_url)
        self._api_key = api_key
        self._owns_client = client is None
        self._closed = False
        selected_timeout = provided.get("timeout", options.get("timeout")) or httpx.Timeout(
            connect=5.0, read=60.0, write=15.0, pool=10.0
        )
        selected_limits = provided.get("limits", options.get("limits")) or httpx.Limits(
            max_connections=20, max_keepalive_connections=10
        )
        self._client = (
            client
            if client is not None
            else httpx.Client(
                timeout=selected_timeout,
                limits=selected_limits,
                transport=httpx.HTTPTransport(retries=0, http2=True, limits=selected_limits),
                follow_redirects=False,
            )
        )

    @property
    def model_name(self) -> str:
        """Return the fixed provider model identity persisted with analyses."""
        return LLM_MODEL

    def _request(
        self,
        request_kind: str,
        messages: list[JsonValue],
        *,
        tools: list[JsonValue] | None = None,
        json_mode: bool = False,
    ) -> _Choice:
        payload: dict[str, JsonValue] = {"model": LLM_MODEL, "messages": messages, "stream": False}
        if tools is not None:
            payload.update({"tools": tools, "tool_choice": "auto"})
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            response = self._client.post(
                self._endpoint,
                headers={
                    "Authorization": f"Bearer {self._api_key.get_secret_value()}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        except httpx.TimeoutException:
            raise LLMTimeoutError(request_kind) from None
        except httpx.TransportError:
            raise LLMTransportError(request_kind) from None
        if not response.is_success:
            raise _status_error(response, request_kind)
        try:
            parsed = _Response.model_validate(response.json())
        except (TypeError, ValueError, ValidationError):
            raise LLMMalformedResponseError(request_kind) from None
        if len(parsed.choices) != 1:
            raise LLMMalformedResponseError(request_kind)
        return parsed.choices[0]

    def analyze_job_mail(self, prompt: str) -> JobMailAnalysisInput:
        """Analyze one bounded mail prompt into the strict mail schema."""
        choice = self._request(
            MAIL_ANALYSIS_KIND,
            [
                {
                    "role": "system",
                    "content": (
                        "Return exactly one JSON object matching JobMailAnalysisInput. "
                        "JSON only: no Markdown, commentary, tools, or tool calls. "
                        "Use the declared enum values exactly and include every required field. "
                        "Schema: "
                        + json.dumps(
                            JobMailAnalysisInput.model_json_schema(),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    ),
                },
                {"role": "user", "content": prompt[:12000]},
            ],
            json_mode=True,
        )
        message = choice.message
        if choice.finish_reason == "length":
            raise LLMContractError(MAIL_ANALYSIS_KIND, contract_reason="truncated")
        if message.tool_calls:
            raise LLMContractError(
                MAIL_ANALYSIS_KIND,
                contract_reason="unexpected_tool_calls",
            )
        if message.content is None or not message.content.strip():
            raise LLMContractError(MAIL_ANALYSIS_KIND, contract_reason="empty_content")
        try:
            decoded = json.loads(message.content)
        except (TypeError, ValueError):
            raise LLMContractError(MAIL_ANALYSIS_KIND, contract_reason="invalid_json") from None
        if not isinstance(decoded, dict):
            raise LLMContractError(MAIL_ANALYSIS_KIND, contract_reason="non_object")
        try:
            return JobMailAnalysisInput.model_validate_json(message.content, strict=True)
        except ValidationError:
            raise LLMContractError(MAIL_ANALYSIS_KIND, contract_reason="schema_failure") from None

    def converse(self, prompt: ConversationPrompt) -> ConversationResponse:
        """Run one bounded Conversation turn and return one application outcome."""
        choice = self._request(
            CONVERSATION_KIND,
            _conversation_messages(prompt),
            tools=_conversation_tools(),
        )
        return _parse_tool_response(choice.message)

    def close(self) -> None:
        """Close the owned HTTP client exactly once."""
        if self._owns_client and not self._closed:
            self._client.close()
            self._closed = True
