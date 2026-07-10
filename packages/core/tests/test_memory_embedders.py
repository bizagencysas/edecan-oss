"""`HashEmbedder` (determinista, offline) y `OpenAICompatEmbedder` (vía HTTP,
mockeado con `respx` — sin red real, ARCHITECTURE.md §0.4)."""

from __future__ import annotations

import math

import httpx
import pytest
import respx
from edecan_core.memory.embedders import (
    DEFAULT_EMBEDDINGS_DIM,
    HashEmbedder,
    OpenAICompatEmbedder,
)


@pytest.mark.asyncio
async def test_hash_embedder_dim_default_es_1536():
    embedder = HashEmbedder()
    [vector] = await embedder.embed(["hola mundo"])
    assert len(vector) == DEFAULT_EMBEDDINGS_DIM == 1536


@pytest.mark.asyncio
async def test_hash_embedder_respeta_dim_custom():
    embedder = HashEmbedder(dim=32)
    [vector] = await embedder.embed(["cualquier texto"])
    assert len(vector) == 32


@pytest.mark.asyncio
async def test_hash_embedder_es_determinista():
    embedder = HashEmbedder(dim=64)
    [v1] = await embedder.embed(["Le gusta el café solo"])
    [v2] = await embedder.embed(["Le gusta el café solo"])
    assert v1 == v2


@pytest.mark.asyncio
async def test_hash_embedder_textos_distintos_dan_vectores_distintos():
    embedder = HashEmbedder(dim=64)
    [v1] = await embedder.embed(["texto A"])
    [v2] = await embedder.embed(["texto B"])
    assert v1 != v2


@pytest.mark.asyncio
async def test_hash_embedder_vector_normalizado():
    embedder = HashEmbedder(dim=64)
    [vector] = await embedder.embed(["normaliza esto por favor"])
    norma = math.sqrt(sum(v * v for v in vector))
    assert norma == pytest.approx(1.0, abs=1e-6)


@pytest.mark.asyncio
async def test_hash_embedder_texto_vacio_no_falla():
    embedder = HashEmbedder(dim=16)
    [vector] = await embedder.embed([""])
    assert len(vector) == 16
    assert any(v != 0.0 for v in vector)


@pytest.mark.asyncio
async def test_hash_embedder_dim_invalida_lanza_value_error():
    with pytest.raises(ValueError):
        HashEmbedder(dim=0)


@pytest.mark.asyncio
async def test_hash_embedder_procesa_varios_textos_en_orden():
    embedder = HashEmbedder(dim=8)
    vectores = await embedder.embed(["a", "b", "c"])
    assert len(vectores) == 3
    assert vectores[0] != vectores[1]


@pytest.mark.asyncio
@respx.mock
async def test_openai_compat_embedder_llama_al_endpoint_y_ordena_por_index():
    route = respx.post("https://embeddings.example.com/embeddings").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.4, 0.5]},
                    {"index": 0, "embedding": [0.1, 0.2]},
                ]
            },
        )
    )
    embedder = OpenAICompatEmbedder(
        base_url="https://embeddings.example.com", api_key="TU_API_KEY_AQUI"
    )

    vectores = await embedder.embed(["primero", "segundo"])

    assert vectores == [[0.1, 0.2], [0.4, 0.5]]
    assert route.called
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer TU_API_KEY_AQUI"
    await embedder.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_openai_compat_embedder_propaga_error_http():
    respx.post("https://embeddings.example.com/embeddings").mock(
        return_value=httpx.Response(500, json={"error": "boom"})
    )
    embedder = OpenAICompatEmbedder(base_url="https://embeddings.example.com")

    with pytest.raises(httpx.HTTPStatusError):
        await embedder.embed(["algo"])
    await embedder.aclose()
