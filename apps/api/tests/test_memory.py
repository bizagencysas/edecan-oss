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
from httpx import ASGITransport, AsyncClient

from edecan_api import deps as edecan_deps
from edecan_api.routers.memory import _parsear_items_extraidos


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


async def test_memory_expone_confidence_y_expiracion(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.post(
        "/v1/memory",
        json={
            "content": "Prefiere el modo oscuro",
            "confidence": 0.65,
            "expires_at": "2026-12-31T00:00:00Z",
        },
        headers=headers,
    )

    assert response.status_code == 201
    assert response.json()["confidence"] == 0.65
    assert response.json()["expires_at"].startswith("2026-12-31")


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


async def test_list_memory_oculta_versiones_reemplazadas(client, fake_repo) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key="hosted_basic")
    created = await client.post(
        "/v1/memory",
        json={"kind": "fact", "content": "Google Play pendiente"},
        headers=headers,
    )
    obsolete_id = uuid.UUID(created.json()["id"])
    fake_repo.memory_items[obsolete_id]["superseded_at"] = "2026-07-22T00:00:00Z"
    fake_repo.memory_items[obsolete_id]["superseded_by"] = uuid.uuid4()

    response = await client.get("/v1/memory", headers=headers)

    assert response.status_code == 200
    assert response.json() == []


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


async def test_delete_all_memory_only_borra_la_memoria_del_usuario(client) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    other_user_id = uuid.uuid4()
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key="hosted_basic")
    other_headers = auth_headers(
        user_id=other_user_id, tenant_id=tenant_id, plan_key="hosted_basic"
    )
    await client.post("/v1/memory", json={"content": "mío"}, headers=headers)
    await client.post("/v1/memory", json={"content": "de otra persona"}, headers=other_headers)

    response = await client.delete("/v1/memory", headers=headers)

    assert response.status_code == 204
    assert (await client.get("/v1/memory", headers=headers)).json() == []
    remaining = await client.get("/v1/memory", headers=other_headers)
    assert [item["content"] for item in remaining.json()] == ["de otra persona"]


async def test_memory_is_scoped_per_tenant(client) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    headers_a = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_a, plan_key="hosted_basic")
    headers_b = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_b, plan_key="hosted_basic")

    await client.post("/v1/memory", json={"content": "Secreto de A"}, headers=headers_a)

    response_b = await client.get("/v1/memory", headers=headers_b)
    assert response_b.json() == []


async def test_add_memory_expone_namespace_y_source_trust(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")

    created = await client.post(
        "/v1/memory", json={"content": "Dato con namespace"}, headers=headers
    )

    assert created.status_code == 201
    assert created.json()["namespace"] == "user"
    assert created.json()["source_trust"] == "trusted"


# ---------------------------------------------------------------------------
# GET /v1/memory/suggestions (correcciones repetidas, product design§172)
# ---------------------------------------------------------------------------


async def test_memory_suggestions_propone_preferencias_repetidas_sin_guardar(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    for _ in range(3):
        await client.post(
            "/v1/memory",
            json={"kind": "preference", "content": "Siempre incluye fuentes primarias"},
            headers=headers,
        )
    await client.post(
        "/v1/memory",
        json={"kind": "preference", "content": "Prefiere respuestas breves"},
        headers=headers,
    )

    response = await client.get("/v1/memory/suggestions", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["text"] == "Siempre incluye fuentes primarias"
    assert body[0]["source"] == "corrección repetida (3x)"
    assert body[0]["scope"] == "user"
    assert body[0]["confidence"] > 0.5

    # Solo propone: no se guardó nada nuevo.
    listed = await client.get("/v1/memory", headers=headers)
    assert len(listed.json()) == 4


async def test_memory_suggestions_vacio_sin_correcciones_repetidas(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    await client.post(
        "/v1/memory", json={"kind": "preference", "content": "Un gusto único"}, headers=headers
    )

    response = await client.get("/v1/memory/suggestions", headers=headers)

    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# GET /v1/memory/suggestions — también escanea patrones de corrección en
# mensajes del chat (product design), con `source` distinguible.
# ---------------------------------------------------------------------------


class _FakeMessagesSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, clause, params):
        return _FakeResult(self._rows)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)


async def test_suggestions_incluye_correcciones_desde_mensajes_con_source_distinto(
    app, fake_repo
) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key="hosted_basic")

    # Fuente 1: memory_items con preferencia repetida.
    for _ in range(3):
        await fake_repo.add_memory(
            tenant_id=tenant_id,
            user_id=user_id,
            kind="preference",
            content="Siempre incluye fuentes primarias",
            importance=0.6,
            source="user",
        )

    # Fuente 2: un mensaje de chat con patrón de corrección.
    fake_session = _FakeMessagesSession(
        [{"content": {"text": "No vuelvas a usar emojis"}, "role": "user", "created_at": None}]
    )
    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: fake_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/memory/suggestions", headers=headers)

    assert response.status_code == 200
    body = response.json()
    fuentes = {item["source"] for item in body}
    textos = {item["text"] for item in body}
    assert "corrección repetida (3x)" in fuentes
    assert "corrección en mensajes (1x)" in fuentes
    assert "Siempre incluye fuentes primarias" in textos
    assert "No vuelvas a usar emojis" in textos


async def test_suggestions_sin_sesion_no_escanea_mensajes_ni_falla(client) -> None:
    # El `client` fixture trae `get_tenant_session -> None`: el barrido de
    # mensajes se degrada a `[]` y solo cuenta la fuente de memory_items.
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    for _ in range(3):
        await client.post(
            "/v1/memory",
            json={"kind": "preference", "content": "Siempre incluye fuentes primarias"},
            headers=headers,
        )

    response = await client.get("/v1/memory/suggestions", headers=headers)

    assert response.status_code == 200
    assert response.json()[0]["source"] == "corrección repetida (3x)"


async def test_list_memory_agente_separa_memoria_del_worker(client, fake_repo) -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    fake_repo.persistent_agents[agent_id] = {
        "tenant_id": tenant_id,
        "memory": {"proyecto": "Edecán", "nota": "revisar deploys"},
    }

    response = await client.get(
        "/v1/memory", params={"namespace": f"agent:{agent_id}"}, headers=headers
    )

    assert response.status_code == 200
    body = response.json()
    assert {item["key"] for item in body} == {"proyecto", "nota"}


async def test_list_memory_agente_desconocido_devuelve_vacio(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")

    response = await client.get(
        "/v1/memory", params={"namespace": f"agent:{uuid.uuid4()}"}, headers=headers
    )

    assert response.status_code == 200
    assert response.json() == []


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


async def test_preview_import_rescata_hechos_si_el_modelo_responde_vacio(client, app) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    _override_llm_router(app, "[]")
    texto = """Quién eres
Nombre preferido: Alex Rivera.
Naciste el 15 de marzo de 1990.
Eres costarricense y has residido legalmente en España.

Cómo prefieres que te responda
Prefieres respuestas humanas, directas y con personalidad.
No soportas respuestas genéricas.
"""

    response = await client.post(
        "/v1/memory/import/preview", json={"texto": texto}, headers=headers
    )

    assert response.status_code == 200
    items = response.json()
    assert len(items) >= 4
    assert any("Nombre preferido" in item["content"] for item in items)
    assert any(item["kind"] == "preference" for item in items)


def test_parser_tolera_prosa_fence_y_objeto_con_aliases_en_espanol() -> None:
    respuesta = """Aquí está el resultado:
```json
{"recuerdos": [{"tipo": "preferencia", "contenido": "Prefiere respuestas breves"}]}
```
"""

    assert _parsear_items_extraidos(respuesta) == [
        {"tipo": "preferencia", "contenido": "Prefiere respuestas breves"}
    ]


def test_parser_recupera_objetos_completos_de_un_array_truncado() -> None:
    respuesta = (
        '[{"kind":"fact","content":"Vive en Medellín"},'
        '{"kind":"preference","content":"Prefiere respuestas breves"},'
        '{"kind":"fact","content":"incompleto'
    )

    assert _parsear_items_extraidos(respuesta) == [
        {"kind": "fact", "content": "Vive en Medellín"},
        {"kind": "preference", "content": "Prefiere respuestas breves"},
    ]


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
