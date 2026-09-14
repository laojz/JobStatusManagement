import json
from collections.abc import Callable

import httpx2
import pytest
from pydantic import SecretStr

from jobs_status_manager.agent.contracts import ConversationPrompt
from jobs_status_manager.application_core.domain import ApplicationStatus
from jobs_status_manager.infrastructure.adapters.openai_compatible_llm import (
    JsonValue,
    LLMAuthenticationError,
    LLMContractError,
    LLMError,
    LLMMalformedResponseError,
    LLMProviderUnavailableError,
    LLMRateLimitError,
    LLMRequestRejectedError,
    LLMTimeoutError,
    LLMTransportError,
    OpenAICompatibleLLM,
)
from jobs_status_manager.mail import MailType


def _client(handler: Callable[[httpx2.Request], httpx2.Response]) -> httpx2.Client:
    return httpx2.Client(transport=httpx2.MockTransport(handler))


def _adapter(handler: Callable[[httpx2.Request], httpx2.Response]) -> OpenAICompatibleLLM:
    return OpenAICompatibleLLM(
        "https://llm.example.test", SecretStr("secret"), client=_client(handler)
    )


def _analysis_payload() -> dict[str, JsonValue]:
    return {
        "application": {"company": "Acme", "position": "Engineer"},
        "mail_type": "INTERVIEW",
        "status_suggestion": {"should_update": True, "status": "INTERVIEW", "interview_round": 1},
        "summary": "Interview invitation",
        "confidence": {"application_match": 0.9, "status_suggestion": 0.8},
    }


def test_mail_analysis_sends_standard_json_request_and_parses_result() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(_analysis_payload())}}]},
        )

    adapter = OpenAICompatibleLLM(
        "https://llm.example.test/v1/",
        SecretStr("secret-value"),
        client=_client(handler),
    )
    result = adapter.analyze_job_mail("bounded mail")

    assert result.application.company == "Acme"
    assert str(seen[0].url) == "https://llm.example.test/v1/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer secret-value"
    payload = json.loads(seen[0].content)
    assert payload["model"] == "deepseek-flash"
    assert payload["stream"] is False
    assert payload["response_format"] == {"type": "json_object"}
    assert "tools" not in payload


@pytest.mark.parametrize(
    ("content", "finish_reason", "contract_reason"),
    [
        (None, None, "empty_content"),
        ("not-json", None, "invalid_json"),
        ("[]", None, "non_object"),
        (json.dumps({"summary": "missing required fields"}), None, "schema_failure"),
        ("", "length", "truncated"),
        ('{"summary":', "length", "truncated"),
    ],
)
def test_mail_analysis_reports_safe_contract_reason_once(
    content: str | None, finish_reason: str | None, contract_reason: str
) -> None:
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        choice: dict[str, JsonValue] = {"message": {"content": content}}
        if finish_reason is not None:
            choice["finish_reason"] = finish_reason
        return httpx2.Response(200, json={"choices": [choice]})

    sensitive_prompt = "prompt-secret"
    adapter = _adapter(handler)
    with pytest.raises(LLMContractError) as raised:
        adapter.analyze_job_mail(sensitive_prompt)

    assert calls == 1
    assert raised.value.contract_reason == contract_reason
    error_text = str(raised.value)
    assert contract_reason in error_text
    assert sensitive_prompt not in error_text
    assert "content-secret" not in error_text
    assert "tool-argument-secret" not in error_text


def test_mail_analysis_rejects_unexpected_tool_calls_without_repair_request() -> None:
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(_analysis_payload()),
                            "tool_calls": [
                                {
                                    "id": "tool-call-secret",
                                    "type": "function",
                                    "function": {
                                        "name": "UnexpectedTool",
                                        "arguments": "tool-argument-secret",
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    adapter = _adapter(handler)
    with pytest.raises(LLMContractError) as raised:
        adapter.analyze_job_mail("prompt-secret")

    assert calls == 1
    assert raised.value.contract_reason == "unexpected_tool_calls"
    error_text = str(raised.value)
    assert "unexpected_tool_calls" in error_text
    assert "prompt-secret" not in error_text
    assert "tool-argument-secret" not in error_text
    assert "tool-call-secret" not in error_text


def test_mail_analysis_accepts_unknown_finish_reason_for_valid_object() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "provider-specific-future-reason",
                        "message": {"content": json.dumps(_analysis_payload())},
                    }
                ]
            },
        )

    result = _adapter(handler).analyze_job_mail("synthetic mail")

    assert result.summary == "Interview invitation"


def test_mail_analysis_system_prompt_names_schema_and_json_only_contract() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            200, json={"choices": [{"message": {"content": json.dumps(_analysis_payload())}}]}
        )

    _adapter(handler).analyze_job_mail("synthetic mail")
    payload = json.loads(seen[0].content)
    system_message = payload["messages"][0]["content"]

    for field_name in (
        "application",
        "mail_type",
        "status_suggestion",
        "summary",
        "confidence",
    ):
        assert field_name in system_message
    for enum_value in (*MailType, *ApplicationStatus):
        assert enum_value.value in system_message
    assert "JSON" in system_message
    assert "only" in system_message.casefold()


def test_mail_analysis_uses_strict_validation_for_confidence() -> None:
    payload = _analysis_payload()
    payload["confidence"] = {"application_match": "0.9", "status_suggestion": 0.8}

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200, json={"choices": [{"message": {"content": json.dumps(payload)}}]}
        )

    with pytest.raises(LLMContractError) as raised:
        _adapter(handler).analyze_job_mail("synthetic mail")

    assert raised.value.contract_reason == "schema_failure"


def test_conversation_preserves_tool_result_identity_and_registry_tools() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"choices": [{"message": {"content": "answer"}}]})

    prompt = ConversationPrompt.model_validate(
        {
            "session_summary": "summary",
            "active_application_id": "app-1",
            "user_message": "where?",
            "recent_messages": (),
            "tool_calls": (
                {
                    "internal_tool_call_id": "internal-call-1",
                    "provider_call_id": "provider-call-1",
                    "provider_type": "function",
                    "name": "SearchApplications",
                    "arguments_json": "{}",
                    "assistant_sequence": 1,
                    "sequence": 1,
                },
            ),
            "tool_results": (
                {
                    "internal_tool_call_id": "internal-call-1",
                    "data": "[]",
                },
            ),
        }
    )
    adapter = _adapter(handler)
    result = adapter.converse(prompt)

    assert result.answer == "answer"
    payload = json.loads(seen[0].content)
    assert payload["messages"] == [
        {"role": "system", "content": prompt.system_prompt},
        {"role": "system", "content": "session_summary: summary"},
        {"role": "system", "content": "active_application_id: app-1"},
        {"role": "user", "content": "where?"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "provider-call-1",
                    "type": "function",
                    "function": {"name": "SearchApplications", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "provider-call-1", "content": "[]"},
    ]
    assert payload["tools"][0]["type"] == "function"
    assert payload["tools"][0]["function"]["parameters"]["type"] == "object"
    assert payload["tool_choice"] == "auto"


def test_conversation_parses_one_function_call() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {"name": "GetRecentMails", "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ]
            },
        )

    adapter = _adapter(handler)
    result = adapter.converse(ConversationPrompt(user_message="recent mails"))

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "GetRecentMails"
    assert result.tool_calls[0].arguments == {}


def test_conversation_preserves_provider_tool_call_identity() -> None:
    arguments_json = '{ "company" : "Acme" }'

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "provider-call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "SearchApplications",
                                        "arguments": arguments_json,
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
        )

    result = _adapter(handler).converse(ConversationPrompt(user_message="applications"))

    assert result.tool_calls[0].provider_call_id == "provider-call-1"
    assert result.tool_calls[0].arguments_json == arguments_json


def test_conversation_parses_multiple_provider_tool_calls_in_order() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "provider-call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "SearchApplications",
                                        "arguments": "{}",
                                    },
                                },
                                {
                                    "id": "provider-call-2",
                                    "type": "function",
                                    "function": {"name": "GetRecentMails", "arguments": "{}"},
                                },
                            ]
                        }
                    }
                ]
            },
        )

    result = _adapter(handler).converse(ConversationPrompt(user_message="status"))

    assert [call.model_dump()["provider_call_id"] for call in result.tool_calls] == [
        "provider-call-1",
        "provider-call-2",
    ]


def test_conversation_reconstructs_ordered_multi_tool_batch_with_matching_results() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"choices": [{"message": {"content": "answer"}}]})

    prompt = ConversationPrompt.model_validate(
        {
            "user_message": "status",
            "tool_calls": (
                {
                    "internal_tool_call_id": "internal-1",
                    "provider_call_id": "provider-1",
                    "provider_type": "function",
                    "name": "SearchApplications",
                    "arguments_json": "{}",
                    "assistant_sequence": 1,
                    "sequence": 1,
                },
                {
                    "internal_tool_call_id": "internal-2",
                    "provider_call_id": "provider-2",
                    "provider_type": "function",
                    "name": "GetRecentMails",
                    "arguments_json": "{}",
                    "assistant_sequence": 1,
                    "sequence": 2,
                },
            ),
            "tool_results": (
                {"internal_tool_call_id": "internal-1", "data": "[]"},
                {"internal_tool_call_id": "internal-2", "data": '{"count":0}'},
            ),
        }
    )

    result = _adapter(handler).converse(prompt)

    assert result.answer == "answer"
    payload = json.loads(seen[0].content)
    assert payload["messages"][-3:] == [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "provider-1",
                    "type": "function",
                    "function": {"name": "SearchApplications", "arguments": "{}"},
                },
                {
                    "id": "provider-2",
                    "type": "function",
                    "function": {"name": "GetRecentMails", "arguments": "{}"},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "provider-1", "content": "[]"},
        {"role": "tool", "tool_call_id": "provider-2", "content": '{"count":0}'},
    ]


@pytest.mark.parametrize(
    "prompt_data",
    [
        {
            "user_message": "status",
            "tool_calls": (
                {
                    "internal_tool_call_id": "internal-1",
                    "provider_call_id": "duplicate",
                    "provider_type": "function",
                    "name": "SearchApplications",
                    "arguments_json": "{}",
                    "assistant_sequence": 1,
                    "sequence": 1,
                },
                {
                    "internal_tool_call_id": "internal-2",
                    "provider_call_id": "duplicate",
                    "provider_type": "function",
                    "name": "GetRecentMails",
                    "arguments_json": "{}",
                    "assistant_sequence": 1,
                    "sequence": 2,
                },
            ),
            "tool_results": (
                {"internal_tool_call_id": "internal-1", "data": "[]"},
                {"internal_tool_call_id": "internal-2", "data": "[]"},
            ),
        },
        {
            "user_message": "status",
            "tool_calls": (
                {
                    "internal_tool_call_id": "internal-1",
                    "provider_call_id": "provider-1",
                    "provider_type": "function",
                    "name": "SearchApplications",
                    "arguments_json": "{}",
                    "assistant_sequence": 1,
                    "sequence": 1,
                },
            ),
            "tool_results": ({"internal_tool_call_id": "unknown-internal", "data": "[]"},),
        },
        {
            "user_message": "status",
            "tool_calls": (
                {
                    "internal_tool_call_id": "internal-1",
                    "provider_call_id": "provider-1",
                    "provider_type": "function",
                    "name": "SearchApplications",
                    "arguments_json": "{}",
                    "assistant_sequence": 1,
                    "sequence": 2,
                },
            ),
            "tool_results": ({"internal_tool_call_id": "internal-1", "data": "[]"},),
        },
    ],
)
def test_conversation_rejects_unreconstructable_continuation(
    prompt_data: dict[str, JsonValue | tuple[JsonValue, ...]],
) -> None:
    adapter = _adapter(
        lambda request: httpx2.Response(
            200,
            json={"choices": [{"message": {"content": "must not be requested"}}]},
        )
    )

    with pytest.raises(LLMContractError):
        adapter.converse(ConversationPrompt.model_validate(prompt_data))


@pytest.mark.parametrize(
    "base_url",
    ["http://llm.example.test", "https://llm.example.test?secret=value"],
)
def test_invalid_base_url_is_rejected(base_url: str) -> None:
    with pytest.raises(LLMError):
        OpenAICompatibleLLM(
            base_url, SecretStr("secret"), client=_client(lambda request: httpx2.Response(200))
        )


def test_non_fixed_model_is_rejected() -> None:
    with pytest.raises(LLMError):
        OpenAICompatibleLLM("https://llm.example.test", SecretStr("secret"), model="other")


@pytest.mark.parametrize(
    "content",
    ["not json", "[]", json.dumps({"summary": "missing required fields"})],
)
def test_mail_analysis_rejects_invalid_structured_content(content: str) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"choices": [{"message": {"content": content}}]})

    adapter = _adapter(handler)
    with pytest.raises(LLMContractError):
        adapter.analyze_job_mail("bounded private mail")


def test_tool_arguments_must_be_scalar_object_values() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "GetRecentMails",
                                        "arguments": '{"nested": {}}',
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
        )

    adapter = _adapter(handler)
    with pytest.raises(LLMContractError):
        adapter.converse(ConversationPrompt(user_message="question"))


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (301, LLMRequestRejectedError),
        (400, LLMRequestRejectedError),
        (401, LLMAuthenticationError),
        (403, LLMAuthenticationError),
        (429, LLMRateLimitError),
        (500, LLMProviderUnavailableError),
    ],
)
def test_http_statuses_map_to_safe_typed_errors(status: int, error_type: type[LLMError]) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            status, json={"error": {"code": "bad-key", "message": "private body"}}
        )

    adapter = OpenAICompatibleLLM(
        "https://llm.example.test", SecretStr("secret"), client=_client(handler)
    )
    with pytest.raises(error_type) as raised:
        adapter.converse(ConversationPrompt(user_message="private prompt"))
    assert "secret" not in str(raised.value)
    assert "private" not in str(raised.value)


def test_transport_is_not_retried_and_errors_do_not_expose_input() -> None:
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        sensitive_message = "secret private prompt"
        raise httpx2.ConnectError(sensitive_message)

    adapter = OpenAICompatibleLLM(
        "https://llm.example.test", SecretStr("secret"), client=_client(handler)
    )
    with pytest.raises(LLMTransportError) as raised:
        adapter.converse(ConversationPrompt(user_message="private prompt"))
    assert calls == 1
    assert str(raised.value) == "transport_failure request_kind=conversation"


def test_malformed_and_ambiguous_responses_are_contract_errors() -> None:
    payloads = [
        ({"choices": []}, LLMMalformedResponseError),
        ({"choices": [{"message": {"content": "", "tool_calls": []}}]}, LLMContractError),
        (
            {
                "choices": [
                    {
                        "message": {
                            "content": "answer",
                            "tool_calls": [
                                {
                                    "id": "x",
                                    "type": "function",
                                    "function": {"name": "GetRecentMails", "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ]
            },
            LLMContractError,
        ),
    ]
    for payload, error_type in payloads:
        adapter = _adapter(lambda request, payload=payload: httpx2.Response(200, json=payload))
        with pytest.raises(error_type):
            adapter.converse(ConversationPrompt(user_message="question"))


def test_injected_client_stays_open_after_idempotent_close() -> None:
    injected = _client(
        lambda request: httpx2.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    )
    adapter = OpenAICompatibleLLM("https://llm.example.test", SecretStr("secret"), client=injected)
    adapter.close()
    adapter.close()
    assert not injected.is_closed
    injected.close()


def test_owned_client_closes_once(monkeypatch: pytest.MonkeyPatch) -> None:
    class TrackingClient(httpx2.Client):
        close_count = 0

        def close(self) -> None:
            TrackingClient.close_count += 1
            super().close()

    monkeypatch.setattr(
        "jobs_status_manager.infrastructure.adapters.openai_compatible_llm.httpx.Client",
        TrackingClient,
    )
    adapter = OpenAICompatibleLLM("https://llm.example.test", SecretStr("secret"))
    adapter.close()
    adapter.close()

    assert TrackingClient.close_count == 1


def test_timeout_is_classified() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        sensitive_message = "secret private prompt"
        raise httpx2.ReadTimeout(sensitive_message)

    adapter = _adapter(handler)
    with pytest.raises(LLMTimeoutError):
        adapter.converse(ConversationPrompt(user_message="question"))
