from jobs_status_manager.infrastructure.safe_errors import (
    MAX_SAFE_ERROR_CHARS,
    safe_external_error,
)


def test_safe_external_error_redacts_secret_patterns_and_bounds_message() -> None:
    error = RuntimeError(
        "POST https://api.example.test/push?token=query-secret&safe=yes "
        "Authorization=header-secret api_key=key-secret "
        "Bearer bearer-secret body=body-secret " + "x" * (MAX_SAFE_ERROR_CHARS + 100)
    )

    result = safe_external_error(error)

    assert len(result) <= MAX_SAFE_ERROR_CHARS
    assert "query-secret" not in result
    assert "header-secret" not in result
    assert "key-secret" not in result
    assert "bearer-secret" not in result
    assert "body-secret" not in result
    assert "https://api.example.test/push?[REDACTED]" in result
    assert result.startswith("RuntimeError: ")


def test_safe_external_error_redacts_signed_and_arbitrary_query_values() -> None:
    error = RuntimeError(
        "GET https://storage.example.test/object?X-Amz-Signature=signed-secret&"
        "arbitrary=arbitrary-secret#fragment"
    )

    result = safe_external_error(error)

    assert result == ("RuntimeError: GET https://storage.example.test/object?[REDACTED]#fragment")
    assert "signed-secret" not in result
    assert "arbitrary-secret" not in result


def test_safe_external_error_uses_exception_type_for_empty_message() -> None:
    result = safe_external_error(RuntimeError())

    assert result == "RuntimeError: no provider message"


def test_safe_external_error_redacts_json_style_secret_values() -> None:
    error = RuntimeError('{"token":"secret value", "api_key": "secret"}')

    result = safe_external_error(error)

    assert result == 'RuntimeError: {"token":"[REDACTED]", "api_key": "[REDACTED]"}'
    assert "secret value" not in result
    assert "secret" not in result


def test_safe_external_error_redacts_quoted_and_whitespace_secret_values() -> None:
    error = RuntimeError('token="secret value" password=secret phrase; request failed')

    result = safe_external_error(error)

    assert result == 'RuntimeError: token="[REDACTED]" password=[REDACTED]; request failed'
    assert "secret value" not in result
    assert "secret phrase" not in result
    assert "request failed" in result
