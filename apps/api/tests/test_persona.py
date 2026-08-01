"""`GET|PUT /v1/persona`, `GET /v1/persona/preview` (ARCHITECTURE.md §10.12, §10.5)."""

from __future__ import annotations

import uuid

from conftest import auth_headers


async def test_get_persona_returns_defaults_when_never_configured(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.get("/v1/persona", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["nombre_asistente"] == "Edecán"
    assert body["idioma"] == "es"
    assert body["formalidad"] == 1
    assert body["memoria_activada"] is True
    assert body["estilo_relacion"] == "profesional"
    assert body["adulto_confirmado"] is False
    assert body["consentimiento_romantico"] is False


async def test_put_persona_applies_partial_patch_and_persists(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")

    response = await client.put(
        "/v1/persona",
        json={"nombre_asistente": "Ada", "formalidad": 3, "emojis": True},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["nombre_asistente"] == "Ada"
    assert body["formalidad"] == 3
    assert body["emojis"] is True
    # Campos no enviados conservan su default: el PATCH es parcial.
    assert body["idioma"] == "es"

    follow_up = await client.get("/v1/persona", headers=headers)
    assert follow_up.json()["nombre_asistente"] == "Ada"


async def test_put_persona_rejects_formalidad_out_of_range(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.put("/v1/persona", json={"formalidad": 9}, headers=headers)
    assert response.status_code == 422


async def test_put_persona_configura_estilo_no_romantico_y_limpia_consentimientos(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.put(
        "/v1/persona",
        json={"estilo_relacion": "coach"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["estilo_relacion"] == "coach"
    assert response.json()["adulto_confirmado"] is False
    assert response.json()["consentimiento_romantico"] is False


async def test_put_persona_rechaza_romantico_sin_adulto_y_consentimiento(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.put(
        "/v1/persona",
        json={"estilo_relacion": "romantico", "adulto_confirmado": True},
        headers=headers,
    )
    assert response.status_code == 422


async def test_put_persona_activa_y_sale_de_romantico_inmediatamente(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    activated = await client.put(
        "/v1/persona",
        json={
            "estilo_relacion": "romantico",
            "adulto_confirmado": True,
            "consentimiento_romantico": True,
        },
        headers=headers,
    )
    assert activated.status_code == 200
    assert activated.json()["estilo_relacion"] == "romantico"

    exited = await client.put(
        "/v1/persona",
        json={"consentimiento_romantico": False},
        headers=headers,
    )
    assert exited.status_code == 200
    assert exited.json()["estilo_relacion"] == "profesional"
    assert exited.json()["adulto_confirmado"] is False
    assert exited.json()["consentimiento_romantico"] is False


async def test_preview_persona_returns_system_prompt_with_no_memories(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    await client.put("/v1/persona", json={"nombre_asistente": "Nova"}, headers=headers)

    response = await client.get("/v1/persona/preview", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert "Nova" in body["system_prompt"]
    assert "No hay memorias relevantes" in body["system_prompt"]


async def test_preview_romantico_refleja_el_estilo_consentido(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    await client.put(
        "/v1/persona",
        json={
            "estilo_relacion": "romantico",
            "adulto_confirmado": True,
            "consentimiento_romantico": True,
        },
        headers=headers,
    )
    response = await client.get("/v1/persona/preview", headers=headers)
    prompt = response.json()["system_prompt"].lower()
    assert "acompaña como pareja virtual" in prompt
    assert "una persona adulta activó y consintió explícitamente" in prompt
    assert "no recites advertencias" in prompt
    assert "responde con honestidad que eres una ia" in prompt
    assert "puede cambiar el estilo o el rol" in prompt
