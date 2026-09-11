"""Alibaba Cloud Bailian OpenAI-compatible embedding adapter."""

from __future__ import annotations

import math
import re
from typing import Final

import httpx2 as httpx
from pydantic import BaseModel, ConfigDict, SecretStr

BAILIAN_BASE_URL: Final = (
    "https://llm-1xfh8lf3wc08fyqi.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
)
BAILIAN_MODEL: Final = "text-embedding-v4"
EMBEDDING_DIMENSIONS: Final = 1024
HTTP_SUCCESS_MIN: Final = 200
HTTP_SUCCESS_MAX: Final = 300
MAX_PROVIDER_CODE_CHARS: Final = 64
PROVIDER_CODE_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")


class BailianError(RuntimeError):
    """Safe failure from the Bailian embedding boundary."""

    def __init__(
        self,
        kind: str,
        status: int | None = None,
        provider_code: str | None = None,
    ) -> None:
        """Store only safe provider classification fields."""
        bounded_provider_code = (
            provider_code
            if provider_code is not None
            and PROVIDER_CODE_PATTERN.fullmatch(provider_code) is not None
            else None
        )
        self.kind = kind
        self.status = status
        self.provider_code = bounded_provider_code
        details = [kind]
        if status is not None:
            details.append(f"status={status}")
        if bounded_provider_code is not None:
            details.append(f"code={bounded_provider_code}")
        super().__init__(" ".join(details))


class _EmbeddingItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    embedding: list[float]


class _EmbeddingResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    data: list[_EmbeddingItem]


class _ProviderError(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str | None = None


class _ProviderErrorResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    error: _ProviderError | None = None


class BailianEmbedding:
    """Synchronous, fixed-contract Bailian embedding client."""

    def __init__(self, api_key: SecretStr, client: httpx.Client | None = None) -> None:
        """Use an injected client without taking ownership of it."""
        self._api_key = api_key
        self._owns_client = client is None
        self._client = client if client is not None else self._new_client(api_key)

    @staticmethod
    def _new_client(api_key: SecretStr) -> httpx.Client:
        timeout = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0)
        limits = httpx.Limits(
            max_connections=200,
            max_keepalive_connections=40,
            keepalive_expiry=30.0,
        )
        transport = httpx.HTTPTransport(retries=3)
        return httpx.Client(
            base_url=BAILIAN_BASE_URL,
            headers={
                "Authorization": f"Bearer {api_key.get_secret_value()}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
            limits=limits,
            transport=transport,
            http2=True,
            follow_redirects=True,
        )

    def embed(self, text: str) -> list[float]:
        """Create one 1024-dimensional finite embedding."""
        try:
            response = self._client.post(
                "/embeddings",
                headers={
                    "Authorization": f"Bearer {self._api_key.get_secret_value()}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": BAILIAN_MODEL,
                    "input": text,
                    "dimensions": EMBEDDING_DIMENSIONS,
                    "encoding_format": "float",
                },
            )
        except httpx.TimeoutException:
            kind = "transport_timeout"
            raise BailianError(kind) from None
        except httpx.TransportError:
            kind = "transport_failure"
            raise BailianError(kind) from None
        if not HTTP_SUCCESS_MIN <= response.status_code < HTTP_SUCCESS_MAX:
            provider_code = None
            try:
                provider_error = _ProviderErrorResponse.model_validate(response.json()).error
            except (ValueError, TypeError):
                provider_error = None
            if provider_error is not None and provider_error.code is not None:
                provider_code = provider_error.code
            kind = "provider_failure"
            raise BailianError(kind, response.status_code, provider_code)
        try:
            payload = _EmbeddingResponse.model_validate(response.json())
        except (ValueError, TypeError):
            kind = "malformed_response"
            raise BailianError(kind) from None
        if len(payload.data) != 1 or len(payload.data[0].embedding) != EMBEDDING_DIMENSIONS:
            kind = "invalid_embedding_shape"
            raise BailianError(kind)
        if not all(math.isfinite(value) for value in payload.data[0].embedding):
            kind = "invalid_embedding_values"
            raise BailianError(kind)
        return payload.data[0].embedding

    def close(self) -> None:
        """Close only the HTTP client owned by this adapter."""
        if self._owns_client:
            self._client.close()
