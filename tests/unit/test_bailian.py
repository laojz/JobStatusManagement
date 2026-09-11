"""Wire and safety tests for the Bailian embedding boundary."""

import json
import traceback
from collections.abc import Callable

import httpx2
import pytest
from pydantic import SecretStr

from jobs_status_manager.infrastructure.adapters.bailian import (
    BAILIAN_BASE_URL,
    MAX_PROVIDER_CODE_CHARS,
    BailianEmbedding,
    BailianError,
)


def _client(handler: Callable[[httpx2.Request], httpx2.Response]) -> httpx2.Client:
    return httpx2.Client(
        base_url=BAILIAN_BASE_URL,
        transport=httpx2.MockTransport(handler),
    )


def test_bailian_exact_wire_request_and_vector() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"data": [{"embedding": [0.5] * 1024}]})

    adapter = BailianEmbedding(SecretStr("secret-value"), _client(handler))
    result = adapter.embed("input text")

    assert len(result) == 1024
    assert seen[0].method == "POST"
    assert str(seen[0].url) == f"{BAILIAN_BASE_URL}/embeddings"
    assert seen[0].headers["authorization"] == "Bearer secret-value"
    assert seen[0].headers["content-type"] == "application/json"
    assert json.loads(seen[0].content) == {
        "model": "text-embedding-v4",
        "input": "input text",
        "dimensions": 1024,
        "encoding_format": "float",
    }


@pytest.mark.parametrize(
    ("payload", "kind"),
    [
        ({"data": [{"embedding": [0.0] * 1023}]}, "invalid_embedding_shape"),
        ({"data": [{"embedding": [float("nan")] * 1024}]}, "invalid_embedding_values"),
        ({"data": []}, "invalid_embedding_shape"),
        ({"wrong": []}, "malformed_response"),
    ],
)
def test_bailian_rejects_invalid_responses(
    payload: dict[str, list[dict[str, list[float]]]], kind: str
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if kind == "invalid_embedding_values":
            return httpx2.Response(
                200,
                content=b'{"data":[{"embedding":[' + b"NaN," * 1023 + b"NaN]}]}",
            )
        return httpx2.Response(200, json=payload)

    adapter = BailianEmbedding(SecretStr("secret-value"), _client(handler))
    with pytest.raises(BailianError, match=kind):
        adapter.embed("private input")


@pytest.mark.parametrize("status", [400, 500])
def test_bailian_provider_error_is_safe(status: int) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            status,
            json={"error": {"code": "InvalidApiKey", "message": "private body"}},
        )

    adapter = BailianEmbedding(SecretStr("secret-value"), _client(handler))
    with pytest.raises(BailianError) as raised:
        adapter.embed("private input")
    assert str(raised.value) == f"provider_failure status={status} code=InvalidApiKey"
    assert "secret-value" not in str(raised.value)
    assert "private input" not in str(raised.value)
    assert "private body" not in str(raised.value)


def test_bailian_provider_code_accepts_valid_max_length() -> None:
    valid_code = "x" * MAX_PROVIDER_CODE_CHARS

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(400, json={"error": {"code": valid_code}})

    adapter = BailianEmbedding(SecretStr("secret-value"), _client(handler))
    with pytest.raises(BailianError) as raised:
        adapter.embed("private input")

    assert raised.value.provider_code == valid_code
    assert raised.value.provider_code is not None
    assert len(raised.value.provider_code) == MAX_PROVIDER_CODE_CHARS


@pytest.mark.parametrize(
    "invalid_code",
    [
        "secret-value\nleak",
        "\x1b[31msecret-value",
        "x" * (MAX_PROVIDER_CODE_CHARS + 1),
        "-InvalidApiKey",
        "",
    ],
)
def test_bailian_error_omits_invalid_provider_code(invalid_code: str) -> None:
    error = BailianError("provider_failure", 400, invalid_code)

    assert error.provider_code is None
    assert str(error) == "provider_failure status=400"
    assert "secret-value" not in str(error)


def test_bailian_timeout_traceback_does_not_expose_transport_message() -> None:
    sensitive_text = "secret-value private input"

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout(sensitive_text)

    adapter = BailianEmbedding(SecretStr("secret-value"), _client(handler))
    caught_error: BailianError | None = None
    formatted = ""
    try:
        adapter.embed("private input")
    except BailianError as error:
        caught_error = error
        formatted = traceback.format_exc()

    assert caught_error is not None
    assert caught_error.__cause__ is None
    assert sensitive_text not in formatted


def test_bailian_transport_traceback_does_not_expose_transport_message() -> None:
    sensitive_text = "secret-value private input"

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError(sensitive_text)

    adapter = BailianEmbedding(SecretStr("secret-value"), _client(handler))
    caught_error: BailianError | None = None
    formatted = ""
    try:
        adapter.embed("private input")
    except BailianError as error:
        caught_error = error
        formatted = traceback.format_exc()

    assert caught_error is not None
    assert caught_error.__cause__ is None
    assert sensitive_text not in formatted


def test_bailian_malformed_traceback_does_not_expose_response_message() -> None:
    sensitive_text = "secret-value private input"

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"wrong": sensitive_text})

    adapter = BailianEmbedding(SecretStr("secret-value"), _client(handler))
    caught_error: BailianError | None = None
    formatted = ""
    try:
        adapter.embed("private input")
    except BailianError as error:
        caught_error = error
        formatted = traceback.format_exc()

    assert caught_error is not None
    assert caught_error.__cause__ is None
    assert sensitive_text not in formatted


def test_bailian_timeout_is_typed_and_safe() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        message = "secret-value private input"
        raise httpx2.ReadTimeout(message)

    adapter = BailianEmbedding(SecretStr("secret-value"), _client(handler))
    with pytest.raises(BailianError, match="transport_timeout"):
        adapter.embed("private input")


def test_bailian_does_not_close_injected_client() -> None:
    client = _client(
        lambda request: httpx2.Response(200, json={"data": [{"embedding": [0.0] * 1024}]})
    )
    adapter = BailianEmbedding(SecretStr("secret-value"), client)
    adapter.close()
    assert not client.is_closed
    client.close()
