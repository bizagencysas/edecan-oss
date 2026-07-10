"""`Embedder` deterministas y offline (`HashEmbedder`) o vía API OpenAI-compatible
(`OpenAICompatEmbedder`) — ARCHITECTURE.md §10.2, §10.7."""

from __future__ import annotations

import hashlib
import math

import httpx

DEFAULT_EMBEDDINGS_DIM = 1536
"""Default de `EMBEDDINGS_DIM` (ARCHITECTURE.md §10.2)."""

_BYTES_PER_BUCKET = 4
_DEFAULT_EMBEDDINGS_MODEL = "text-embedding-3-small"
_DEFAULT_TIMEOUT = 30.0


class HashEmbedder:
    """Embedder determinista y 100% offline basado en *feature hashing*.

    Cada token del texto (en minúsculas, separado por espacios) se hashea con
    SHA-256; los bytes del hash deciden en qué "cubeta" del vector (de
    dimensión `dim`) y con qué signo se acumula. El resultado se normaliza a
    norma L2 = 1, como un embedding real. Es determinista — el mismo texto
    produce siempre el mismo vector, sin red ni claves — así que sirve de
    valor por defecto para self-host sin proveedor de embeddings configurado
    (`EMBEDDINGS_MODEL` vacío).
    """

    def __init__(self, dim: int = DEFAULT_EMBEDDINGS_DIM) -> None:
        if dim <= 0:
            raise ValueError("dim debe ser positivo")
        self._dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self._dim
        tokens = text.lower().split() or [""]
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            for offset in range(0, len(digest), _BYTES_PER_BUCKET):
                chunk = digest[offset : offset + _BYTES_PER_BUCKET]
                bucket = int.from_bytes(chunk, "big") % self._dim
                sign = 1.0 if chunk[0] % 2 == 0 else -1.0
                vector[bucket] += sign

        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0.0:
            # Caso extremo (requeriría que todas las cubetas se cancelen
            # exactamente): se evita la división por cero de forma igual de
            # determinista, derivando la cubeta de respaldo del propio texto
            # en vez de usar el `hash()` builtin (que Python "salpimenta" por
            # proceso y rompería el determinismo).
            fallback = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:4], "big")
            vector[fallback % self._dim] = 1.0
            norm = 1.0
        return [v / norm for v in vector]


class OpenAICompatEmbedder:
    """Embedder vía cualquier endpoint compatible con `POST {base}/embeddings`
    (formato de OpenAI: `{"model", "input": [...]}` → `{"data": [{"index", "embedding"}, ...]}`)."""

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        model: str = _DEFAULT_EMBEDDINGS_MODEL,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._client = http_client or httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        """Cierra el cliente HTTP subyacente (pool de conexiones)."""
        await self._client.aclose()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        headers = {"content-type": "application/json"}
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"
        response = await self._client.post(
            f"{self._base_url}/embeddings",
            json={"model": self._model, "input": texts},
            headers=headers,
        )
        response.raise_for_status()
        data = response.json()
        items = sorted(data.get("data", []), key=lambda item: item.get("index", 0))
        return [item["embedding"] for item in items]
