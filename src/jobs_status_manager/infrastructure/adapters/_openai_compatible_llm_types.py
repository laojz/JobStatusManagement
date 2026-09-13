"""Safe errors and strict wire models for the OpenAI-compatible adapter."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar, Final, Literal, TypedDict

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    import httpx2 as httpx

MAX_PROVIDER_CODE_CHARS = 64
_PROVIDER_CODE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
type JsonValue = str | int | float | bool | list["JsonValue"] | dict[str, "JsonValue"] | None
type ToolArguments = dict[str, str | int | bool | None]
type ContractReason = Literal[
    "empty_content",
    "invalid_json",
    "non_object",
    "schema_failure",
    "truncated",
    "unexpected_tool_calls",
]
_CONTRACT_REASONS: Final[tuple[ContractReason, ...]] = (
    "empty_content",
    "invalid_json",
    "non_object",
    "schema_failure",
    "truncated",
    "unexpected_tool_calls",
)


class LLMClientOptions(TypedDict, total=False):
    model: str
    timeout: httpx.Timeout
    limits: httpx.Limits


class LLMAdapterOptions(TypedDict, total=False):
    model: str
    timeout: httpx.Timeout
    limits: httpx.Limits
    options: LLMClientOptions


class LLMError(RuntimeError):
    """Base class for safe, classified LLM boundary failures."""

    kind = "llm_failure"
    retryable: ClassVar[bool] = True

    def __init__(
        self,
        request_kind: str,
        http_status: int | None = None,
        provider_code: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        self.request_kind = request_kind
        self.http_status = http_status
        self.provider_code = (
            provider_code
            if provider_code is not None and _PROVIDER_CODE_PATTERN.fullmatch(provider_code)
            else None
        )
        self.retry_after_seconds = retry_after_seconds
        details = [self.kind, f"request_kind={request_kind}"]
        if http_status is not None:
            details.append(f"http_status={http_status}")
        if self.provider_code is not None:
            details.append(f"provider_code={self.provider_code}")
        if retry_after_seconds is not None:
            details.append(f"retry_after_seconds={retry_after_seconds:g}")
        super().__init__(" ".join(details))


class LLMConfigurationError(LLMError):
    kind = "configuration_error"
    retryable = False


class LLMAuthenticationError(LLMError):
    kind = "authentication_failure"
    retryable = False


class LLMRequestRejectedError(LLMError):
    kind = "request_rejected"
    retryable = False


class LLMRateLimitError(LLMError):
    kind = "rate_limited"


class LLMProviderUnavailableError(LLMError):
    kind = "provider_unavailable"


class LLMTimeoutError(LLMError):
    kind = "timeout"


class LLMTransportError(LLMError):
    kind = "transport_failure"


class LLMMalformedResponseError(LLMError):
    kind = "malformed_response"


class LLMContractError(LLMError):
    kind = "contract_error"

    def __init__(
        self,
        request_kind: str,
        http_status: int | None = None,
        provider_code: str | None = None,
        retry_after_seconds: float | None = None,
        contract_reason: ContractReason | None = None,
    ) -> None:
        self.contract_reason = contract_reason if contract_reason in _CONTRACT_REASONS else None
        super().__init__(request_kind, http_status, provider_code, retry_after_seconds)
        if self.contract_reason is not None:
            self.args = (f"{self.args[0]} contract_reason={self.contract_reason}",)


class _ResponseModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class _Function(_ResponseModel):
    name: str
    arguments: str


class _ToolCall(_ResponseModel):
    id: str
    type: str
    function: _Function


class _Message(_ResponseModel):
    content: str | None = None
    tool_calls: tuple[_ToolCall, ...] = ()


class _Choice(_ResponseModel):
    message: _Message
    finish_reason: str | None = None


class _Response(_ResponseModel):
    choices: tuple[_Choice, ...]


class _ProviderError(_ResponseModel):
    code: str | None = None


class _ErrorResponse(_ResponseModel):
    error: _ProviderError | None = None
