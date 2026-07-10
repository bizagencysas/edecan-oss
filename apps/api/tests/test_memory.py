"""`/v1/memory` — CRUD manual de `memory_items` (ARCHITECTURE.md §10.12, §10.3),
más `/v1/memory/import/*` (importar memoria pegando texto de otra IA, ver
docstring de `edecan_api.routers.memory`).

`FakeLLMRouter` local (duplicada a propósito, ARCHITECTURE.md §10.1): el
`client`/`app` de `conftest.py` sobreescribe `get_llm_router` a `lambda:
None` por defecto, así que los tests de `/import/preview` (que sí llaman
`llm_router.complete(...)` de verdad) lo vuelven a sobreescribir con esto.
"""

from __future__ import annotations

import uuid
from typing import Any

from conftest import auth_headers
from edecan_llm.base import CompletionResponse, Usage

from edecan_api import deps as edecan_deps


class FakeLLMRouter:
    def __init__(self, texto_respuesta: str) -> None:
        self._texto_respuesta = texto_respuesta
        self.llamadas: list[tuple[str, dict[str, Any]]] = []

    async def complete(
        self, alias: str, tenant_flags: dict[str, Any], req: Any
    ) -> CompletionResponse:
        self.llamadas.append((alias, tenant_flags))
        return CompletionResponse(
            text=self._texto_respuesta,
            usage=Usage(input_tokens=10, output_tokens=5),
            stop_reason="end",
        )


def _override_llm_router(app, texto_respuesta: str) -> FakeLLMRouter:
    fake = FakeLLMRouter(texto_respuesta)
    app.dependency_overrides[edecan_deps.get_llm_router] = lambda: fake
    return fake


async def test_list_memory_starts_empty(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.get("/v1/memory", headers=headers)
    assert response.status_code == 200
    assert response.json() == []


async def test_add_memory_then_list_returns_it(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")

    created = await client.post(
        "/v1/memory",
        json={"kind": "fact", "content": "Le gusta el café sin azúcar", "importance": 0.8},
        headers=headers,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["content"] == "Le gusta el café sin azúcar"
    assert body["kind"] == "fact"

    listed = await client.get("/v1/memory", headers=headers)
    assert len(listed.json()) == 1
    assert listed.json()[0]["id"] == body["id"]


async def test_list_memory_filters_by_query(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    await client.post("/v1/memory", json={"content": "Cumpleaños el 5 de mayo"}, headers=headers)
    await client.post(
        "/v1/memory", json={"content": "Prefiere reuniones por la mañana"}, headers=headers
    )

    response = await client.get("/v1/memory", params={"q": "cumpleaños"}, headers=headers)
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert "Cumpleaños" in results[0]["content"]


async def test_add_memory_rejects_empty_content(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.post("/v1/memory", json={"content": ""}, headers=headers)
    assert response.status_code == 422


async def test_delete_memory_removes_it(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    created = await client.post("/v1/memory", json={"content": "Dato temporal"}, headers=headers)
    memory_id = created.json()["id"]

    deleted = await client.delete(f"/v1/memory/{memory_id}", headers=headers)
    assert deleted.status_code == 204

    listed = await client.get("/v1/memory", headers=headers)
    assert listed.json() == []


async def test_delete_unknown_memory_returns_404(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.delete(f"/v1/memory/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404


async def test_memory_is_scoped_per_tenant(client) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    headers_a = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_a, plan_key="hosted_basic")
    headers_b = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_b, plan_key="hosted_basic")

    await client.post("/v1/memory", json={"content": "Secreto de A"}, headers=headers_a)

    response_b = await client.get("/v1/memory", headers=headers_b)
    assert response_b.json() == []


# ---------------------------------------------------------------------------
# POST /v1/memory/import/preview + /confirm
# ---------------------------------------------------------------------------

_RESPUESTA_LLM_VALIDA = (
    '[{"kind": "preference", "content": "Prefiere reuniones por la mañana", '
    '"importance": 0.7, "source": "importado"}, '
    '{"kind": "fact", "content": "Trabaja en una agencia de diseño", "importance": 0.5}]'
)


async def test_preview_import_extrae_items_sin_guardar_nada(client, app) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    fake = _override_llm_router(app, _RESPUESTA_LLM_VALIDA)

    response = await client.post(
        "/v1/memory/import/preview", json={"texto": "texto pegado de otra IA"}, headers=headers
    )

    assert response.status_code == 200
    items = response.json()
    assert len(items) == 2
    assert items[0]["kind"] == "preference"
    assert items[0]["content"] == "Prefiere reuniones por la mañana"
    assert items[1]["source"] == "importado"  # default: el item no traía `source`
    assert len(fake.llamadas) == 1
    assert fake.llamadas[0][0] == "rapido"

    # Nada se guardó todavía.
    listed = await client.get("/v1/memory", headers=headers)
    assert listed.json() == []


async def test_preview_import_respuesta_vacia_devuelve_lista_vacia(client, app) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    _override_llm_router(app, "[]")

    response = await client.post(
        "/v1/memory/import/preview", json={"texto": "nada que extraer acá"}, headers=headers
    )

    assert response.status_code == 200
    assert response.json() == []


async def test_preview_import_respuesta_no_json_degrada_a_lista_vacia(client, app) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    _override_llm_router(app, "esto no es JSON")

    response = await client.post(
        "/v1/memory/import/preview", json={"texto": "algo"}, headers=headers
    )

    assert response.status_code == 200
    assert response.json() == []


async def test_preview_import_ignora_items_con_kind_invalido(client, app) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    _override_llm_router(
        app, '[{"kind": "no-existe", "content": "x"}, {"kind": "fact", "content": "válido"}]'
    )

    response = await client.post(
        "/v1/memory/import/preview", json={"texto": "algo"}, headers=headers
    )

    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["content"] == "válido"


async def test_preview_import_rejects_empty_texto(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.post("/v1/memory/import/preview", json={"texto": ""}, headers=headers)
    assert response.status_code == 422


async def test_confirm_import_guarda_los_items_elegidos(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")

    response = await client.post(
        "/v1/memory/import/confirm",
        json={
            "items": [
                {"kind": "fact", "content": "Vive en Bogotá", "importance": 0.6},
                {"kind": "preference", "content": "Le gusta el café sin azúcar"},
            ]
        },
        headers=headers,
    )

    assert response.status_code == 201
    created = response.json()
    assert len(created) == 2
    assert {item["content"] for item in created} == {
        "Vive en Bogotá",
        "Le gusta el café sin azúcar",
    }

    listed = await client.get("/v1/memory", headers=headers)
    assert len(listed.json()) == 2


async def test_confirm_import_rejects_empty_items(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.post("/v1/memory/import/confirm", json={"items": []}, headers=headers)
    assert response.status_code == 422
