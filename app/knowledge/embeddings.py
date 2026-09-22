from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx


class EmbeddingProvider(Protocol):
    @property
    def identity(self) -> str: ...

    @property
    def dimensions(self) -> int | None: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class OpenAICompatibleEmbeddingProvider:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout
        self._dimensions: int | None = None

    @staticmethod
    def _normalize_endpoint(base_url: str) -> str:
        parsed = urlsplit(base_url.strip())
        scheme = parsed.scheme.casefold()
        hostname = (parsed.hostname or "").casefold()
        port = parsed.port
        if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
            port = None
        authority = hostname if port is None else f"{hostname}:{port}"
        path = parsed.path.rstrip("/")
        return urlunsplit((scheme, authority, path, "", ""))

    @property
    def identity(self) -> str:
        return f"openai_compatible:{self.endpoint_identity}:{self._model}"

    @property
    def endpoint_identity(self) -> str:
        return self._normalize_endpoint(self._base_url)

    @property
    def dimensions(self) -> int | None:
        return self._dimensions

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        with httpx.Client(timeout=self._timeout) as client:
            response = client.post(
                f"{self._base_url}/embeddings",
                json={"model": self._model, "input": list(texts)},
                headers=headers,
            )
            response.raise_for_status()
        data = response.json().get("data", [])
        vectors = [item["embedding"] for item in sorted(data, key=lambda item: item["index"])]
        if len(vectors) != len(texts) or any(not vector for vector in vectors):
            raise RuntimeError("Embedding provider returned an incomplete response")
        dimensions = len(vectors[0])
        if any(len(vector) != dimensions for vector in vectors):
            raise RuntimeError("Embedding provider returned inconsistent dimensions")
        self._dimensions = dimensions
        return [[float(value) for value in vector] for vector in vectors]


def build_embedding_provider(settings: object) -> EmbeddingProvider:
    return OpenAICompatibleEmbeddingProvider(
        settings.local_openai_base_url,
        settings.local_embedding_model,
        settings.local_openai_api_key,
        settings.request_timeout_seconds,
    )
