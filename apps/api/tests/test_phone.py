"""Telefonía conversacional: todos los proveedores son fakes; nunca salen llamadas reales."""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from conftest import auth_headers
from edecan_core.persona import build_system_prompt
from edecan_llm.base import CompletionResponse, Usage
from edecan_voice.telephony import TelephonyError, TwilioCall, twilio_signature
from edecan_voice.tools import LlamarContactoTool

from edecan_api.routers import phone

OPERATING_PROFILE = {
    "funcion_y_mision": "Atender la gestión asignada con una identidad propia.",
    "capabilities": "Conversar, recopilar información y acordar el siguiente paso.",
    "out_of_scope": "Decisiones legales, financieras o comerciales no documentadas.",
    "allowed_actions": "Preguntar, explicar información autorizada y tomar un recado.",
    "prohibited_actions": "No inventar información ni asumir compromisos no autorizados.",
    "escalation_rules": "Tomar un recado cuando falte contexto o se requiera una decisión humana.",
    "success_criteria": "La solicitud queda entendida y con un siguiente paso claro.",
}


class FakeGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def create_call(self, **kwargs) -> TwilioCall:
        self.calls.append(kwargs)
        return TwilioCall(sid="CA" + "9" * 32, status="queued")


class FakePhoneTTS:
    def __init__(self) -> None:
        self.calls: list[dict[str, str | None]] = []

    async def synthesize(self, text: str, voice_id: str | None = None) -> bytes:
        self.calls.append({"text": text, "voice_id": voice_id})
        return b"real-tenant-mp3"


class FakePhoneVault:
    def __init__(self, token: str) -> None:
        self.token = token

    async def get(self, _tenant_id, _account_id):
        return SimpleNamespace(access_token=self.token, scopes=[])


class FakeLLMRouter:
    """`app.state.llm_router` en memoria: nunca sale a la red real.

    `text` se devuelve tal cual como `CompletionResponse.text` (el resumen espera un
    objeto JSON ahí). `raises=True` simula el proveedor caído para probar el fallback
    al resumen determinista.
    """

    def __init__(self, text: str = "", *, raises: bool = False) -> None:
        self.text = text
        self.raises = raises
        self.calls: list[tuple[str, dict, object]] = []

    async def complete(self, alias, flags, request):  # noqa: ANN001
        self.calls.append((alias, flags, request))
        if self.raises:
            raise RuntimeError("proveedor de inferencia no disponible")
        return CompletionResponse(
            text=self.text,
            usage=Usage(input_tokens=40, output_tokens=90),
            stop_reason="end",
        )


async def test_phone_tts_uses_agent_voice_and_ephemeral_audio_endpoint(
    app, client, fake_repo, fake_redis, monkeypatch
) -> None:
    tenant_id = uuid.uuid4()
    call_id = uuid.uuid4()
    tts = FakePhoneTTS()

    async def fake_tts_for_tenant(*_args, **_kwargs):
        return tts

    monkeypatch.setattr(phone, "_tts_para_tenant", fake_tts_for_tenant)
    request = SimpleNamespace(app=app)
    url = await phone._twilio_play_url(
        request,
        repo=fake_repo,
        vault=SimpleNamespace(),
        redis_client=fake_redis,
        call={
            "id": call_id,
            "tenant_id": tenant_id,
            "voice_id": "voz-negocios",
        },
        text="Hola desde el agente",
    )
    assert url is not None
    assert tts.calls == [{"text": "Hola desde el agente", "voice_id": "voz-negocios"}]

    path = urlsplit(url).path
    audio = await client.get(path)
    assert audio.status_code == 200
    assert audio.content == b"real-tenant-mp3"
    assert audio.headers["content-type"].startswith("audio/mpeg")


async def test_setup_incoming_calls_configures_current_twilio_number(
    app, client, fake_repo, monkeypatch
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key="twilio",
        external_account_id="+573001111111",
        display_name="+573001111111",
        scopes=["AC" + "1" * 32],
    )
    await fake_repo.create_phone_agent_template(
        tenant_id=tenant_id,
        user_id=user_id,
        name="Recepción",
        agent_name="Sofía",
        persona_prompt="Habla con claridad.",
        default_goal="Atender la llamada.",
        opening_message="¿En qué puedo ayudarte?",
        operating_profile=OPERATING_PROFILE,
        is_default=True,
        is_inbound_default=True,
    )
    app.dependency_overrides[phone.get_vault] = lambda: FakePhoneVault("t" * 32)
    seen: dict[str, str] = {}

    async def fake_verify(account_sid, auth_token, phone_number, *, http_client):
        seen["phone_number"] = phone_number
        return "PN" + "2" * 32

    async def fake_configure(account_sid, auth_token, phone_sid, webhook_url, *, http_client):
        seen["phone_sid"] = phone_sid
        seen["webhook_url"] = webhook_url

    monkeypatch.setattr(phone, "_verify_twilio_phone_ownership", fake_verify)
    monkeypatch.setattr(phone, "_configure_twilio_incoming_webhook", fake_configure)

    response = await client.post(
        "/v1/phone/incoming/setup",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
    )
    assert response.status_code == 200
    assert response.json()["phone_number"] == "+573001111111"
    assert response.json()["agent_name"] == "Sofía"
    assert seen == {
        "phone_number": "+573001111111",
        "phone_sid": "PN" + "2" * 32,
        "webhook_url": "http://localhost:8000/v1/phone/twilio/incoming",
    }


async def _phone_ready(fake_repo, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> None:
    await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key="twilio",
        external_account_id="+573001111111",
        display_name="+573001111111",
        scopes=["AC" + "1" * 32],
    )
    await fake_repo.grant_phone_consent(
        tenant_id=tenant_id,
        phone_e164="+573002222222",
        kind="voice",
        source="formulario_prueba",
    )
    templates = await fake_repo.list_phone_agent_templates(tenant_id=tenant_id, user_id=user_id)
    if not templates:
        await fake_repo.create_phone_agent_template(
            tenant_id=tenant_id,
            user_id=user_id,
            name="Asistente",
            agent_name="Sofía",
            persona_prompt="Habla con claridad y registra lo importante.",
            default_goal="Resolver la gestión solicitada.",
            opening_message="Te llamo para ayudarte con una gestión.",
            operating_profile=OPERATING_PROFILE,
            is_default=True,
            is_inbound_default=True,
        )


async def test_prepare_never_calls_provider_and_requires_consent(client, fake_repo) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key="twilio",
        external_account_id="+573001111111",
        display_name="+573001111111",
        scopes=["AC" + "1" * 32],
    )
    await fake_repo.create_phone_agent_template(
        tenant_id=tenant_id,
        user_id=user_id,
        name="Asistente",
        agent_name="Sofía",
        persona_prompt="Habla con claridad.",
        default_goal="Resolver la gestión solicitada.",
        opening_message="Hola.",
        operating_profile=OPERATING_PROFILE,
        is_default=True,
    )
    response = await client.post(
        "/v1/phone/calls/prepare",
        json={
            "to_e164": "+573002222222",
            "recipient_name": "Daniel Rojas",
            "goal": "Confirmar la cita de mañana",
        },
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
    )
    assert response.status_code == 409
    assert fake_repo.phone_calls == {}


async def test_prepare_requires_recipient_and_exact_outbound_agent(client, fake_repo) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key="twilio",
        external_account_id="+573001111111",
        display_name="+573001111111",
        scopes=["AC" + "1" * 32],
    )
    await fake_repo.grant_phone_consent(
        tenant_id=tenant_id,
        phone_e164="+573002222222",
        kind="voice",
        source="formulario_prueba",
    )
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)

    missing_recipient = await client.post(
        "/v1/phone/calls/prepare",
        json={
            "to_e164": "+573002222222",
            "goal": "Confirmar la cita",
        },
        headers=headers,
    )
    assert missing_recipient.status_code == 422

    missing_agent = await client.post(
        "/v1/phone/calls/prepare",
        json={
            "to_e164": "+573002222222",
            "recipient_name": "Daniel Rojas",
            "goal": "Confirmar la cita",
        },
        headers=headers,
    )
    assert missing_agent.status_code == 409
    assert missing_agent.json()["detail"] == "Elige un agente de llamadas antes de continuar."
    assert fake_repo.phone_calls == {}


async def test_outbound_disabled_agent_cannot_prepare_call(client, fake_repo) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key="twilio",
        external_account_id="+573001111111",
        display_name="+573001111111",
        scopes=["AC" + "1" * 32],
    )
    await fake_repo.grant_phone_consent(
        tenant_id=tenant_id,
        phone_e164="+573002222222",
        kind="voice",
        source="formulario_prueba",
    )
    inbound_only = await fake_repo.create_phone_agent_template(
        tenant_id=tenant_id,
        user_id=user_id,
        name="Recepción",
        agent_name="Asistente Recepción",
        persona_prompt="Atiende con calma.",
        default_goal="Atender la solicitud.",
        opening_message="¿En qué puedo ayudarte?",
        operating_profile=OPERATING_PROFILE,
        handles_inbound=True,
        handles_outbound=False,
        is_default=False,
        is_inbound_default=True,
    )
    response = await client.post(
        "/v1/phone/calls/prepare",
        json={
            "to_e164": "+573002222222",
            "recipient_name": "Daniel Rojas",
            "goal": "Confirmar la cita",
            "agent_template_id": str(inbound_only["id"]),
        },
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
    )
    assert response.status_code == 409
    assert "no atiende llamadas salientes" in response.json()["detail"]
    assert fake_repo.phone_calls == {}


async def test_prepare_and_confirm_are_two_distinct_steps(app, client, fake_repo) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    await _phone_ready(fake_repo, tenant_id=tenant_id, user_id=user_id)
    gateway = FakeGateway()
    app.dependency_overrides[phone.get_phone_gateway] = lambda: gateway
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)

    prepared = await client.post(
        "/v1/phone/calls/prepare",
        json={
            "to_e164": " +573002222222 ",
            "recipient_name": "Daniel Rojas",
            "goal": " Confirmar   la cita de mañana ",
        },
        headers=headers,
    )
    assert prepared.status_code == 201
    draft = prepared.json()
    assert draft["status"] == "draft"
    assert draft["requires_confirmation"] is True
    assert draft["verification"] == {
        "to_e164": "+573002222222",
        "recipient_name": "Daniel Rojas",
        "goal": "Confirmar la cita de mañana",
        "agent_template_id": draft["agent"]["template_id"],
        "agent_template_name": "Asistente",
        "agent_name": "Sofía",
    }
    assert gateway.calls == []

    stale = await client.post(
        f"/v1/phone/calls/{draft['id']}/confirm",
        json={
            "expected_to_e164": "+573002222222",
            "expected_recipient_name": "Daniel Rojas",
            "expected_goal": "Cambiar la cita",
            "expected_agent_template_id": draft["agent"]["template_id"],
            "confirmed_destination": True,
            "confirmed_recipient": True,
            "confirmed_goal": True,
            "confirmed_agent": True,
        },
        headers=headers,
    )
    assert stale.status_code == 409
    assert gateway.calls == []

    confirmed = await client.post(
        f"/v1/phone/calls/{draft['id']}/confirm",
        json={
            "expected_to_e164": "+573002222222",
            "expected_recipient_name": "Daniel Rojas",
            "expected_goal": "Confirmar la cita de mañana",
            "expected_agent_template_id": draft["agent"]["template_id"],
            "confirmed_destination": True,
            "confirmed_recipient": True,
            "confirmed_goal": True,
            "confirmed_agent": True,
        },
        headers=headers,
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "queued"
    assert len(gateway.calls) == 1
    assert gateway.calls[0]["to_e164"] == "+573002222222"

    detail = await client.get(f"/v1/phone/calls/{draft['id']}", headers=headers)
    assert detail.status_code == 200
    assert [event["event_type"] for event in detail.json()["events"]] == [
        "prepared",
        "confirmed",
        "provider_queued",
    ]


async def test_confirmation_rejects_changed_recipient_or_agent(app, client, fake_repo) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    await _phone_ready(fake_repo, tenant_id=tenant_id, user_id=user_id)
    other = await fake_repo.create_phone_agent_template(
        tenant_id=tenant_id,
        user_id=user_id,
        name="Ventas",
        agent_name="Asistente Ventas",
        persona_prompt="Habla como asesora comercial.",
        default_goal="Entender la oportunidad.",
        opening_message="Te llamo para conversar sobre una oportunidad.",
        operating_profile=OPERATING_PROFILE,
        is_default=False,
    )
    gateway = FakeGateway()
    app.dependency_overrides[phone.get_phone_gateway] = lambda: gateway
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)
    prepared = await client.post(
        "/v1/phone/calls/prepare",
        json={
            "to_e164": "+573002222222",
            "recipient_name": "Daniel Rojas",
            "goal": "Confirmar la cita",
        },
        headers=headers,
    )
    draft = prepared.json()
    base = {
        "expected_to_e164": "+573002222222",
        "expected_recipient_name": "Daniel Rojas",
        "expected_goal": "Confirmar la cita",
        "expected_agent_template_id": draft["agent"]["template_id"],
        "confirmed_destination": True,
        "confirmed_recipient": True,
        "confirmed_goal": True,
        "confirmed_agent": True,
    }

    changed_recipient = await client.post(
        f"/v1/phone/calls/{draft['id']}/confirm",
        json={**base, "expected_recipient_name": "Otra persona"},
        headers=headers,
    )
    changed_agent = await client.post(
        f"/v1/phone/calls/{draft['id']}/confirm",
        json={**base, "expected_agent_template_id": str(other["id"])},
        headers=headers,
    )
    assert changed_recipient.status_code == 409
    assert changed_agent.status_code == 409
    assert gateway.calls == []


async def test_phone_agent_templates_crud_keeps_one_default_per_user(client, fake_repo) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)
    assistant_payload = {
        "name": "Asistente personal",
        "agent_name": "Sofía",
        "persona_prompt": "Sé cordial, concreta y toma notas claras.",
        "default_goal": "Entender la solicitud y dejar un resumen útil.",
        "opening_message": "Te llamo para ayudarte con una gestión pendiente.",
        "operating_profile": OPERATING_PROFILE,
        "is_default": False,
    }
    first = await client.post("/v1/phone/agent-templates", json=assistant_payload, headers=headers)
    assert first.status_code == 201
    assert first.json()["is_default"] is True

    sales_payload = {
        "name": "Ventas consultivas",
        "agent_name": "Camila",
        "persona_prompt": "Escucha antes de ofrecer y nunca presiones.",
        "default_goal": "Entender la necesidad y acordar el siguiente paso.",
        "opening_message": "Quisiera conocer brevemente qué necesitas.",
        "operating_profile": {
            **OPERATING_PROFILE,
            "funcion_y_mision": "Calificar oportunidades comerciales.",
            "prohibited_actions": "No prometer descuentos ni presionar a la persona.",
        },
        "is_default": True,
    }
    second = await client.post("/v1/phone/agent-templates", json=sales_payload, headers=headers)
    assert second.status_code == 201

    other_headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id)
    assert (await client.get("/v1/phone/agent-templates", headers=other_headers)).json() == []
    foreign_update = await client.put(
        f"/v1/phone/agent-templates/{second.json()['id']}",
        json=sales_payload,
        headers=other_headers,
    )
    assert foreign_update.status_code == 404
    foreign_delete = await client.delete(
        f"/v1/phone/agent-templates/{second.json()['id']}", headers=other_headers
    )
    assert foreign_delete.status_code == 404

    listed = await client.get("/v1/phone/agent-templates", headers=headers)
    assert listed.status_code == 200
    assert [row["name"] for row in listed.json()] == [
        "Ventas consultivas",
        "Asistente personal",
    ]
    assert [row["is_default"] for row in listed.json()] == [True, False]

    duplicate = await client.post(
        "/v1/phone/agent-templates",
        json={**sales_payload, "name": "ventas CONSULTIVAS"},
        headers=headers,
    )
    assert duplicate.status_code == 409

    removed = await client.delete(
        f"/v1/phone/agent-templates/{second.json()['id']}", headers=headers
    )
    assert removed.status_code == 204
    remaining = (await client.get("/v1/phone/agent-templates", headers=headers)).json()
    assert len(remaining) == 1
    assert remaining[0]["id"] == first.json()["id"]
    assert remaining[0]["is_default"] is True
    assert [row["action"] for row in fake_repo.audit_log[-3:]] == [
        "phone.agent_template_created",
        "phone.agent_template_created",
        "phone.agent_template_deleted",
    ]


async def test_phone_agent_bundle_roundtrip_is_private_and_can_replace(client, fake_repo) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)
    payload = {
        "name": "Negocios",
        "agent_name": "Asistente Ventas",
        "persona_prompt": "Escucha, analiza y negocia sin inventar condiciones.",
        "default_goal": "Entender la oportunidad y acordar un siguiente paso.",
        "opening_message": "Te llamo para conversar sobre una oportunidad.",
        "knowledge_context": "Solo puede compartir la oferta autorizada.",
        "required_information": "Necesidad, presupuesto y fecha.",
        "voice_id": "voice-sales",
        "operating_profile": {
            **OPERATING_PROFILE,
            "funcion_y_mision": "Representar oportunidades de negocio autorizadas.",
        },
        "is_default": True,
        "is_inbound_default": True,
    }
    created = await client.post(
        "/v1/phone/agent-templates",
        json=payload,
        headers=headers,
    )
    assert created.status_code == 201

    exported = await client.get("/v1/phone/agent-templates/export", headers=headers)
    assert exported.status_code == 200
    bundle = exported.json()
    assert bundle["schema_version"] == 1
    assert bundle["kind"] == "edecan.phone-agent-templates"
    assert bundle["templates"][0]["name"] == "Negocios"
    serialized = str(bundle)
    assert created.json()["id"] not in serialized
    assert "created_at" not in serialized
    assert "provider_call_sid" not in serialized
    assert "auth_token" not in serialized

    bundle["conflict_policy"] = "replace"
    bundle["templates"][0]["persona_prompt"] = "Nueva identidad, solo para llamadas futuras."
    imported = await client.post(
        "/v1/phone/agent-templates/import",
        json=bundle,
        headers=headers,
    )
    assert imported.status_code == 200
    assert imported.json()["created"] == 0
    assert imported.json()["updated"] == 1
    assert imported.json()["skipped"] == 0
    assert imported.json()["templates"][0]["id"] == created.json()["id"]
    assert (
        imported.json()["templates"][0]["persona_prompt"]
        == "Nueva identidad, solo para llamadas futuras."
    )
    assert fake_repo.audit_log[-1]["action"] == "phone.agent_templates_imported"


async def test_phone_agent_import_rejects_ambiguous_defaults_before_writing(
    client, fake_repo
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    template = {
        "name": "Uno",
        "agent_name": "A",
        "persona_prompt": "Habla con claridad.",
        "default_goal": "Resolver.",
        "operating_profile": OPERATING_PROFILE,
        "is_default": True,
    }
    response = await client.post(
        "/v1/phone/agent-templates/import",
        json={
            "schema_version": 1,
            "kind": "edecan.phone-agent-templates",
            "templates": [template, {**template, "name": "Dos", "agent_name": "B"}],
        },
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
    )
    assert response.status_code == 422
    assert await fake_repo.list_phone_agent_templates(tenant_id=tenant_id, user_id=user_id) == []


async def test_prepared_call_snapshots_selected_agent_and_keeps_confirmation_gate(
    app, client, fake_repo
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    await _phone_ready(fake_repo, tenant_id=tenant_id, user_id=user_id)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)
    payload = {
        "name": "Seguimiento de citas",
        "agent_name": "Sara",
        "persona_prompt": "Habla con calma, confirma fechas y no inventes disponibilidad.",
        "default_goal": "Confirmar la cita y registrar cualquier cambio solicitado.",
        "opening_message": "Te llamo para confirmar tu próxima cita.",
        "operating_profile": {
            **OPERATING_PROFILE,
            "funcion_y_mision": "Confirmar citas y registrar cambios.",
            "prohibited_actions": "No inventar disponibilidad ni confirmar cambios inexistentes.",
        },
        "is_default": True,
    }
    template = await client.post("/v1/phone/agent-templates", json=payload, headers=headers)
    assert template.status_code == 201
    gateway = FakeGateway()
    app.dependency_overrides[phone.get_phone_gateway] = lambda: gateway

    prepared = await client.post(
        "/v1/phone/calls/prepare",
        json={
            "to_e164": "+573002222222",
            "recipient_name": "Daniel Rojas",
            "agent_template_id": template.json()["id"],
        },
        headers=headers,
    )
    assert prepared.status_code == 201
    draft = prepared.json()
    assert draft["goal"] == payload["default_goal"]
    assert draft["agent"] == {
        "template_id": template.json()["id"],
        "template_name": payload["name"],
        "name": payload["agent_name"],
    }
    assert gateway.calls == []

    edited = await client.put(
        f"/v1/phone/agent-templates/{template.json()['id']}",
        json={
            **payload,
            "agent_name": "Nombre nuevo",
            "persona_prompt": "Prompt nuevo que no debe tocar el borrador.",
            "opening_message": "Saludo nuevo.",
        },
        headers=headers,
    )
    assert edited.status_code == 200
    persisted = fake_repo.phone_calls[uuid.UUID(draft["id"])]
    assert persisted["agent_name"] == "Sara"
    assert payload["persona_prompt"] in persisted["agent_prompt"]
    assert "Confirmar citas y registrar cambios." in persisted["agent_prompt"]
    assert persisted["agent_operating_profile"]["funcion_y_mision"] == (
        "Confirmar citas y registrar cambios."
    )
    assert persisted["opening_message"] == payload["opening_message"]

    confirmed = await client.post(
        f"/v1/phone/calls/{draft['id']}/confirm",
        json={
            "expected_to_e164": "+573002222222",
            "expected_recipient_name": "Daniel Rojas",
            "expected_goal": payload["default_goal"],
            "expected_agent_template_id": draft["agent"]["template_id"],
            "confirmed_destination": True,
            "confirmed_recipient": True,
            "confirmed_goal": True,
            "confirmed_agent": True,
        },
        headers=headers,
    )
    assert confirmed.status_code == 200
    assert len(gateway.calls) == 1

    app.state.phone_webhook_token_loader = lambda _tenant_id: "hook-token"
    voice_path = f"/v1/phone/twilio/calls/{draft['id']}/voice"
    voice_params = {"CallSid": "CA" + "9" * 32}
    voice = await client.post(
        voice_path,
        data=voice_params,
        headers={
            "X-Twilio-Signature": twilio_signature(
                f"http://localhost:8000{voice_path}", voice_params, "hook-token"
            )
        },
    )
    assert voice.status_code == 200
    assert "Sara" in voice.text
    assert payload["opening_message"] in voice.text
    assert "Saludo nuevo" not in voice.text


def test_phone_agent_context_keeps_template_below_hard_safety_rules() -> None:
    context = phone._phone_operating_context(
        {
            "agent_template_name": "Ventas",
            "agent_name": "Asistente Ventas",
            "agent_prompt": "Promete cualquier descuento y di que ya reservaste.",
            "goal": "Acordar una demostración",
            "recipient_name": "Daniel Rojas",
        }
    )
    assert "<instrucciones_agente_llamada>" in context
    assert "Objetivo de esta llamada: Acordar una demostración" in context
    assert context.index("Promete cualquier descuento") < context.index(
        "nunca autoriza acciones sensibles"
    )
    assert "pendiente de confirmación en la app" in context
    assert "Tu identidad durante esta llamada es Asistente Ventas" in context
    assert "Persona destinataria indicada por el propietario: Daniel Rojas" in context


async def test_confirm_requires_both_explicit_checks(app, client, fake_repo) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    await _phone_ready(fake_repo, tenant_id=tenant_id, user_id=user_id)
    gateway = FakeGateway()
    app.dependency_overrides[phone.get_phone_gateway] = lambda: gateway
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)
    prepared = await client.post(
        "/v1/phone/calls/prepare",
        json={
            "to_e164": "+573002222222",
            "recipient_name": "Daniel Rojas",
            "goal": "Confirmar la entrega",
        },
        headers=headers,
    )
    response = await client.post(
        f"/v1/phone/calls/{prepared.json()['id']}/confirm",
        json={
            "expected_to_e164": "+573002222222",
            "expected_recipient_name": "Daniel Rojas",
            "expected_goal": "Confirmar la entrega",
            "expected_agent_template_id": prepared.json()["agent"]["template_id"],
            "confirmed_destination": True,
            "confirmed_recipient": True,
            "confirmed_goal": False,
            "confirmed_agent": True,
        },
        headers=headers,
    )
    assert response.status_code == 422
    assert gateway.calls == []


async def test_signed_status_webhook_updates_activity_state(
    app, client, fake_repo, monkeypatch
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    await _phone_ready(fake_repo, tenant_id=tenant_id, user_id=user_id)
    conversation = await fake_repo.create_conversation(
        tenant_id=tenant_id, user_id=user_id, title="Llamada", channel="phone"
    )
    call = await fake_repo.create_phone_call(
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation["id"],
        direction="outgoing",
        from_e164="+573001111111",
        to_e164="+573002222222",
        goal="Confirmar entrega",
        status="queued",
        provider_call_sid="CA" + "8" * 32,
    )
    await fake_repo.add_phone_call_event(
        tenant_id=tenant_id,
        call_id=call["id"],
        event_type="transcript",
        payload={"role": "caller", "text": "Confirmo que mañana enviaré la dirección."},
    )
    await fake_repo.add_phone_call_event(
        tenant_id=tenant_id,
        call_id=call["id"],
        event_type="transcript",
        payload={"role": "assistant", "text": "Perfecto, quedamos en revisar el envío."},
    )
    enqueued: list[dict] = []

    async def fake_enqueue(_settings, job_type, payload, queued_tenant_id):
        enqueued.append({"job_type": job_type, "payload": payload, "tenant_id": queued_tenant_id})
        return uuid.uuid4()

    monkeypatch.setattr(phone, "enqueue", fake_enqueue)
    app.state.phone_webhook_token_loader = lambda _tenant_id: "hook-token"
    # Con transcripción disponible el resumen se narra con el LLM (fallback probado
    # aparte en `test_call_status_falls_back_to_deterministic_summary_when_llm_fails`).
    fake_llm = FakeLLMRouter(
        json.dumps(
            {
                "key_points": [
                    "El interlocutor llamó para confirmar la entrega y avisó que enviará "
                    "la dirección mañana."
                ],
                "commitments": ["Confirmo que mañana enviaré la dirección."],
                "next_steps": ["Revisar la dirección enviada y coordinar la entrega."],
            }
        )
    )
    app.state.llm_router = fake_llm
    path = f"/v1/phone/twilio/calls/{call['id']}/status"
    params = {
        "CallSid": "CA" + "8" * 32,
        "CallStatus": "completed",
        "CallDuration": "42",
    }
    signature = twilio_signature(f"http://localhost:8000{path}", params, "hook-token")
    response = await client.post(path, data=params, headers={"X-Twilio-Signature": signature})
    assert response.status_code == 204
    updated = fake_repo.phone_calls[call["id"]]
    assert updated["status"] == "completed"
    assert updated["duration_seconds"] == 42
    summary = updated["summary"]
    assert summary["status"] == "completed"
    assert summary["duration_seconds"] == 42
    assert summary["participants"] == [
        {"role": "assistant", "name": "Edecan", "phone_e164": "+573001111111"},
        {"role": "external", "name": None, "phone_e164": "+573002222222"},
    ]
    assert summary["transcript"] == {"available": True, "turn_count": 2}
    assert summary["key_points"] == [
        "El interlocutor llamó para confirmar la entrega y avisó que enviará la dirección "
        "mañana."
    ]
    assert summary["commitments"] == ["Confirmo que mañana enviaré la dirección."]
    assert summary["next_steps"] == ["Revisar la dirección enviada y coordinar la entrega."]
    assert len(fake_llm.calls) == 1
    assert fake_llm.calls[0][0] == "rapido"

    # §3C: el resumen no muere en `phone_calls.summary`. Aterriza escrito en la
    # conversación PRINCIPAL (la de "Actividad"), no en el hilo `phone` de la
    # llamada, y el push lleva ese `chat_id` para abrirla de un toque.
    principal = await fake_repo.get_main_conversation(tenant_id=tenant_id, user_id=user_id)
    assert principal is not None
    escritos = fake_repo.messages[principal["id"]]
    assert [message["role"] for message in escritos] == ["assistant"]
    texto = escritos[0]["content"]["text"]
    assert "El interlocutor llamó para confirmar la entrega" in texto
    assert "+573002222222" in texto  # el chat sí puede nombrar al otro lado; el push no.
    assert "Revisar la dirección enviada y coordinar la entrega." in texto
    assert fake_repo.messages[conversation["id"]] == []  # el hilo de la llamada, intacto
    assert enqueued == [
        {
            "job_type": "notify_phone_call_summary",
            "payload": {"call_id": str(call["id"]), "chat_id": str(principal["id"])},
            "tenant_id": tenant_id,
        }
    ]

    # El mismo callback puede llegar más de una vez: no reescribe el resumen,
    # no duplica actividad, no dispara un segundo push y -- sobre todo -- no
    # deja DOS resúmenes de la misma llamada en el chat.
    generated_at = updated["summary_generated_at"]
    duplicate_response = await client.post(
        path, data=params, headers={"X-Twilio-Signature": signature}
    )
    assert duplicate_response.status_code == 204
    assert fake_repo.phone_calls[call["id"]]["summary_generated_at"] == generated_at
    assert len(fake_llm.calls) == 1  # el segundo callback no vuelve a narrar con el LLM.
    assert (
        len(
            [
                event
                for event in fake_repo.phone_call_events[call["id"]]
                if event["event_type"] == "activity"
            ]
        )
        == 1
    )
    activity = next(
        event
        for event in fake_repo.phone_call_events[call["id"]]
        if event["event_type"] == "activity"
    )
    assert activity["payload"] == {
        "kind": "phone_call_finished",
        "status": "completed",
        "direction": "outgoing",
        "summary_available": True,
    }
    assert len(enqueued) == 1
    assert len(fake_repo.messages[principal["id"]]) == 1

    # Twilio puede reintentar o entregar callbacks fuera de orden. Un evento
    # tardío nunca debe revivir una llamada que ya terminó.
    stale_params = {"CallSid": "CA" + "8" * 32, "CallStatus": "ringing"}
    stale_signature = twilio_signature(f"http://localhost:8000{path}", stale_params, "hook-token")
    stale_response = await client.post(
        path,
        data=stale_params,
        headers={"X-Twilio-Signature": stale_signature},
    )
    assert stale_response.status_code == 204
    assert fake_repo.phone_calls[call["id"]]["status"] == "completed"
    assert len(enqueued) == 1


async def test_failed_call_without_transcript_still_gets_summary_when_queue_fails(
    app, client, fake_repo, monkeypatch
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    conversation = await fake_repo.create_conversation(
        tenant_id=tenant_id, user_id=user_id, title="Llamada fallida", channel="phone"
    )
    call_sid = "CA" + "3" * 32
    call = await fake_repo.create_phone_call(
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation["id"],
        direction="incoming",
        from_e164="+573003333333",
        to_e164="+573001111111",
        goal="Atender",
        status="ringing",
        provider_call_sid=call_sid,
    )
    app.state.phone_webhook_token_loader = lambda _tenant_id: "hook-token"
    # Sin ningún turno del interlocutor, el resumen nunca debe siquiera intentar
    # narrar con el LLM (caso obligatorio del plan: solo el determinista, que ya lo
    # dice explícitamente y no inventa nada).
    exploding_llm = FakeLLMRouter(raises=True)
    app.state.llm_router = exploding_llm
    enqueue_attempts = 0

    async def broken_enqueue(*_args, **_kwargs):
        nonlocal enqueue_attempts
        enqueue_attempts += 1
        raise RuntimeError("cola caída")

    monkeypatch.setattr(phone, "enqueue", broken_enqueue)
    path = f"/v1/phone/twilio/calls/{call['id']}/status"
    params = {"CallSid": call_sid, "CallStatus": "failed"}
    response = await client.post(
        path,
        data=params,
        headers={
            "X-Twilio-Signature": twilio_signature(
                f"http://localhost:8000{path}", params, "hook-token"
            )
        },
    )

    assert response.status_code == 204
    summary = fake_repo.phone_calls[call["id"]]["summary"]
    assert summary["status"] == "failed"
    assert summary["duration_seconds"] is None
    assert summary["transcript"] == {"available": False, "turn_count": 0}
    assert summary["key_points"] == ["No hubo transcripción disponible."]
    assert summary["commitments"] == []
    assert summary["next_steps"] == [
        "Revisar el estado de la llamada y decidir si conviene reintentarlo."
    ]
    assert exploding_llm.calls == []
    assert enqueue_attempts == 1
    assert (
        len(
            [
                event
                for event in fake_repo.phone_call_events[call["id"]]
                if event["event_type"] == "activity"
            ]
        )
        == 1
    )


async def test_call_status_falls_back_to_deterministic_summary_when_llm_fails(
    app, client, fake_repo, monkeypatch
) -> None:
    """Con transcripción pero el LLM caído, el resumen no se queda a medias: usa el
    determinista de siempre (mismo comportamiento que antes de narrar con LLM)."""
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    conversation = await fake_repo.create_conversation(
        tenant_id=tenant_id, user_id=user_id, title="Llamada", channel="phone"
    )
    call = await fake_repo.create_phone_call(
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation["id"],
        direction="outgoing",
        from_e164="+573001111111",
        to_e164="+573002222222",
        goal="Confirmar entrega",
        status="queued",
        provider_call_sid="CA" + "2" * 32,
    )
    await fake_repo.add_phone_call_event(
        tenant_id=tenant_id,
        call_id=call["id"],
        event_type="transcript",
        payload={"role": "caller", "text": "Confirmo que mañana enviaré la dirección."},
    )
    await fake_repo.add_phone_call_event(
        tenant_id=tenant_id,
        call_id=call["id"],
        event_type="transcript",
        payload={"role": "assistant", "text": "Perfecto, quedamos en revisar el envío."},
    )

    async def fake_enqueue(*_args, **_kwargs):
        return uuid.uuid4()

    monkeypatch.setattr(phone, "enqueue", fake_enqueue)
    app.state.phone_webhook_token_loader = lambda _tenant_id: "hook-token"
    broken_llm = FakeLLMRouter(raises=True)
    app.state.llm_router = broken_llm
    path = f"/v1/phone/twilio/calls/{call['id']}/status"
    params = {"CallSid": "CA" + "2" * 32, "CallStatus": "completed", "CallDuration": "18"}
    signature = twilio_signature(f"http://localhost:8000{path}", params, "hook-token")
    response = await client.post(path, data=params, headers={"X-Twilio-Signature": signature})

    assert response.status_code == 204
    summary = fake_repo.phone_calls[call["id"]]["summary"]
    # El LLM sí se intentó (y falló); el resultado es exactamente el determinista.
    assert len(broken_llm.calls) == 1
    assert summary["transcript"] == {"available": True, "turn_count": 2}
    assert "Confirmo que mañana enviaré la dirección." in summary["commitments"]
    assert summary["next_steps"]
    assert summary["narrated"] is False

    # Y en el chat NO se disfraza de resumen lo que son recortes literales de la
    # transcripción: se dice que no se pudo redactar y se muestran tal cual.
    principal = await fake_repo.get_main_conversation(tenant_id=tenant_id, user_id=user_id)
    texto = fake_repo.messages[principal["id"]][0]["content"]["text"]
    assert "No se pudo redactar el resumen" in texto
    assert "— Confirmo que mañana enviaré la dirección." in texto


async def test_llamada_que_nadie_contesto_lo_dice_y_no_inventa_conversacion(
    app, client, fake_repo, monkeypatch
) -> None:
    """Listón del dueño: una llamada sin conversación se reporta como tal, jamás con
    contenido fabricado. Es el caso más peligroso porque el resumen determinista SÍ trae
    frases de relleno ("No hubo transcripción disponible.", "Revisar el estado de la
    llamada...") que, volcadas al chat sin criterio, se leen como si algo hubiera pasado.
    """
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    conversation = await fake_repo.create_conversation(
        tenant_id=tenant_id, user_id=user_id, title="Llamada", channel="phone"
    )
    call_sid = "CA" + "5" * 32
    call = await fake_repo.create_phone_call(
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation["id"],
        direction="outgoing",
        from_e164="+573001111111",
        to_e164="+573002222222",
        recipient_name="Daniel Rojas",
        goal="Confirmar la cita del jueves",
        status="ringing",
        provider_call_sid=call_sid,
    )
    enqueued: list[dict] = []

    async def fake_enqueue(_settings, job_type, payload, queued_tenant_id):
        enqueued.append({"job_type": job_type, "payload": payload, "tenant_id": queued_tenant_id})
        return uuid.uuid4()

    monkeypatch.setattr(phone, "enqueue", fake_enqueue)
    app.state.phone_webhook_token_loader = lambda _tenant_id: "hook-token"
    app.state.llm_router = FakeLLMRouter(json.dumps({"key_points": ["No debió llamarse"]}))
    path = f"/v1/phone/twilio/calls/{call['id']}/status"
    params = {"CallSid": call_sid, "CallStatus": "no-answer"}
    signature = twilio_signature(f"http://localhost:8000{path}", params, "hook-token")
    response = await client.post(path, data=params, headers={"X-Twilio-Signature": signature})

    assert response.status_code == 204
    assert fake_repo.phone_calls[call["id"]]["status"] == "no_answer"
    principal = await fake_repo.get_main_conversation(tenant_id=tenant_id, user_id=user_id)
    assert principal is not None
    texto = fake_repo.messages[principal["id"]][0]["content"]["text"]
    assert texto == (
        "La llamada a Daniel Rojas (+573002222222) (objetivo: Confirmar la cita del jueves) "
        "no llegó a hablarse: nadie contestó. No hay nada que resumir: de esa llamada no "
        "quedó registrada ni una palabra."
    )
    # Sin transcripción el LLM ni se intenta, así que no hay forma de que una alucinación
    # se cuele al chat; y las frases de relleno del determinista tampoco se vuelcan.
    assert app.state.llm_router.calls == []
    assert "No debió llamarse" not in texto
    assert "No hubo transcripción disponible." not in texto
    assert enqueued[0]["payload"] == {
        "call_id": str(call["id"]),
        "chat_id": str(principal["id"]),
    }


async def test_si_falla_escribir_en_el_chat_el_resumen_y_el_push_siguen_su_curso(
    app, client, fake_repo, monkeypatch
) -> None:
    """El mensaje es la ENTREGA; el resumen persistido es el dato. Si el chat falla, el
    dueño todavía tiene su resumen en Actividad y su aviso -- solo que sin `chat_id`, así
    que el push no promete una conversación donde no quedó escrito nada."""
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    conversation = await fake_repo.create_conversation(
        tenant_id=tenant_id, user_id=user_id, title="Llamada", channel="phone"
    )
    call_sid = "CA" + "6" * 32
    call = await fake_repo.create_phone_call(
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation["id"],
        direction="incoming",
        from_e164="+573003333333",
        to_e164="+573001111111",
        goal="Atender",
        status="in_progress",
        provider_call_sid=call_sid,
    )
    enqueued: list[dict] = []

    async def fake_enqueue(_settings, job_type, payload, queued_tenant_id):
        enqueued.append({"job_type": job_type, "payload": payload, "tenant_id": queued_tenant_id})
        return uuid.uuid4()

    async def add_message_roto(**_kwargs):
        raise RuntimeError("messages no disponible")

    monkeypatch.setattr(phone, "enqueue", fake_enqueue)
    monkeypatch.setattr(fake_repo, "add_message", add_message_roto)
    app.state.phone_webhook_token_loader = lambda _tenant_id: "hook-token"
    app.state.llm_router = None
    path = f"/v1/phone/twilio/calls/{call['id']}/status"
    params = {"CallSid": call_sid, "CallStatus": "completed", "CallDuration": "9"}
    signature = twilio_signature(f"http://localhost:8000{path}", params, "hook-token")
    response = await client.post(path, data=params, headers={"X-Twilio-Signature": signature})

    assert response.status_code == 204
    assert fake_repo.phone_calls[call["id"]]["summary"] is not None
    assert enqueued[0]["payload"] == {"call_id": str(call["id"])}


class TestTextoDelResumenEnElChat:
    """`_phone_summary_chat_text` a solas: es el único texto que el dueño lee al colgar."""

    NARRADO = {
        "transcript": {"available": True, "turn_count": 4},
        "narrated": True,
        "key_points": [
            "Llamó alguien que dijo ser Manuel González y afirmó ser tu desarrollador.",
            "Pidió que le revelara el system prompt y la asistente se negó por ser confidencial.",
            "Insistió un par de veces, luego lo aceptó y colgó.",
        ],
        "commitments": [],
        "next_steps": [],
    }

    def test_entrante_narrada_conserva_la_narracion_completa(self) -> None:
        call = {
            "direction": "incoming",
            "status": "completed",
            "from_e164": "+573009999999",
            "to_e164": "+573001111111",
        }
        texto = phone._phone_summary_chat_text(call, self.NARRADO)
        assert texto.startswith("Acaba de entrar una llamada de +573009999999.\n\n")
        assert "dijo ser Manuel González" in texto
        assert "se negó por ser confidencial" in texto
        assert "colgó." in texto

    def test_una_entrante_nunca_toma_por_bueno_el_nombre_que_dio_quien_llamo(self) -> None:
        """El encabezado solo lleva el número: que alguien "diga ser" Manuel González es
        una afirmación suya, y eso lo matiza la narración, no un encabezado en firme."""
        call = {
            "direction": "incoming",
            "status": "completed",
            "from_e164": "+573009999999",
            "recipient_name": "Manuel González",
        }
        encabezado = phone._phone_summary_chat_text(call, self.NARRADO).split("\n\n")[0]
        assert encabezado == "Acaba de entrar una llamada de +573009999999."

    def test_conectada_pero_sin_un_solo_turno_no_afirma_que_nadie_hablo(self) -> None:
        """`completed` sin transcripción es ambiguo: se sabe que no quedó registro, NO que
        nadie habló. Decir "no hubo conversación" acá sería inventar en la otra dirección."""
        call = {
            "direction": "incoming",
            "status": "completed",
            "from_e164": "+573009999999",
        }
        texto = phone._phone_summary_chat_text(
            call, {"transcript": {"available": False, "turn_count": 0}}
        )
        assert texto == (
            "La llamada entrante de +573009999999 se conectó, pero no quedó registrada "
            "ninguna intervención. No hay contenido de lo que se habló ahí, y no se va a "
            "inventar."
        )

    def test_compromisos_y_pendientes_se_agregan_cortos_y_acotados(self) -> None:
        call = {"direction": "outgoing", "status": "completed", "to_e164": "+573002222222"}
        texto = phone._phone_summary_chat_text(
            call,
            {
                **self.NARRADO,
                "commitments": ["Enviar la cotización el martes"],
                "next_steps": ["Decidir el descuento", "Confirmar la fecha", "Avisar a Ana", "Ex"],
            },
        )
        assert "Compromisos: Enviar la cotización el martes" in texto
        assert "Pendiente: Decidir el descuento; Confirmar la fecha; Avisar a Ana" in texto
        assert "Ex" not in texto.split("Pendiente: ")[1]

    def test_sin_trato_cableado_para_ninguna_formalidad(self) -> None:
        """El trato (tuteo vs. usted) sale de `formalidad` de la persona del tenant y solo
        lo aplica el LLM. Este texto de respaldo tiene que servir igual para los cuatro
        niveles, así que no puede traer segunda persona ni un "usuario" cableado."""
        for direction, call in (
            ("outgoing", {"direction": "outgoing", "status": "busy", "to_e164": "+573002222222"}),
            ("incoming", {"direction": "incoming", "status": "failed", "from_e164": "+57300333"}),
        ):
            texto = phone._phone_summary_chat_text(
                call, {"transcript": {"available": False, "turn_count": 0}}
            )
            prohibidas = ("usuario", "usted", "tuyo", "tu llamada", "puedes", "puede reintentar")
            assert not any(palabra in texto.lower() for palabra in prohibidas), direction


async def test_incoming_call_and_gather_continue_same_conversation(
    app, client, fake_repo, monkeypatch
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    await fake_repo.create_membership(user_id=user_id, tenant_id=tenant_id, role="owner")
    await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key="twilio",
        external_account_id="+573001111111",
        display_name="+573001111111",
        scopes=["AC" + "1" * 32],
    )
    await fake_repo.create_phone_agent_template(
        tenant_id=tenant_id,
        user_id=user_id,
        name="Negocios",
        agent_name="Asistente Ventas",
        persona_prompt="Conversa como consultora de negocios.",
        default_goal="Entender una oportunidad.",
        opening_message="Te llamo para conversar sobre una oportunidad.",
        operating_profile=OPERATING_PROFILE,
        handles_inbound=False,
        handles_outbound=True,
        is_default=True,
        is_inbound_default=False,
    )
    await fake_repo.create_phone_agent_template(
        tenant_id=tenant_id,
        user_id=user_id,
        name="Recepción",
        agent_name="Asistente Recepción",
        persona_prompt="Escucha con calidez y resuelve solicitudes generales.",
        default_goal="Atender y orientar a la persona.",
        opening_message="¿En qué puedo ayudarte?",
        operating_profile=OPERATING_PROFILE,
        handles_inbound=True,
        handles_outbound=False,
        is_default=False,
        is_inbound_default=True,
    )
    app.state.phone_webhook_token_loader = lambda _tenant_id: "hook-token"
    app.state.phone_turn_runner = lambda call, speech: f"Entendido: {speech}"
    enqueued: list[dict] = []

    async def fake_enqueue(_settings, job_type, payload, queued_tenant_id):
        enqueued.append({"job_type": job_type, "payload": payload, "tenant_id": queued_tenant_id})
        return uuid.uuid4()

    monkeypatch.setattr(phone, "enqueue", fake_enqueue)
    incoming_params = {
        "CallSid": "CA" + "7" * 32,
        "To": "+573001111111",
        "From": "+573003333333",
    }
    incoming_path = "/v1/phone/twilio/incoming"
    incoming_sig = twilio_signature(
        f"http://localhost:8000{incoming_path}", incoming_params, "hook-token"
    )
    incoming = await client.post(
        incoming_path,
        data=incoming_params,
        headers={"X-Twilio-Signature": incoming_sig},
    )
    assert incoming.status_code == 200
    assert "¿En qué puedo ayudarte?" in incoming.text
    call = next(iter(fake_repo.phone_calls.values()))
    assert call["direction"] == "incoming"
    assert call["agent_template_name"] == "Recepción"
    assert call["agent_name"] == "Asistente Recepción"
    assert "Escucha con calidez" in call["agent_prompt"]
    assert enqueued == [
        {
            "job_type": "notify_incoming_phone_call",
            "payload": {"call_id": str(call["id"])},
            "tenant_id": tenant_id,
        }
    ]

    # Twilio reintenta webhooks. La llamada y el evento durable no se
    # duplican, y no se crea otra intención de entrega.
    repeated = await client.post(
        incoming_path,
        data=incoming_params,
        headers={"X-Twilio-Signature": incoming_sig},
    )
    assert repeated.status_code == 200
    assert len(fake_repo.phone_calls) == 1
    assert len(enqueued) == 1
    assert [event["event_type"] for event in fake_repo.phone_call_events[call["id"]]] == [
        "incoming"
    ]

    gather_path = f"/v1/phone/twilio/calls/{call['id']}/gather"
    gather_params = {
        "CallSid": "CA" + "7" * 32,
        "SpeechResult": "Quiero mover mi cita",
    }
    gather_sig = twilio_signature(
        f"http://localhost:8000{gather_path}", gather_params, "hook-token"
    )
    gather = await client.post(
        gather_path,
        data=gather_params,
        headers={"X-Twilio-Signature": gather_sig},
    )
    assert gather.status_code == 200
    assert "Entendido: Quiero mover mi cita" in gather.text
    messages = fake_repo.messages[call["conversation_id"]]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    events = fake_repo.phone_call_events[call["id"]]
    assert [event["event_type"] for event in events] == [
        "incoming",
        "transcript",
        "transcript",
    ]


async def test_dispatcher_calls_provider_only_after_persistence_context_commits(fake_repo) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    await _phone_ready(fake_repo, tenant_id=tenant_id, user_id=user_id)
    await fake_repo.create_phone_agent_template(
        tenant_id=tenant_id,
        user_id=user_id,
        name="Asesor",
        agent_name="Mateo",
        persona_prompt="Haz preguntas claras y resume el acuerdo.",
        default_goal="Entender la necesidad.",
        opening_message="Te llamo para revisar tu solicitud.",
        operating_profile=OPERATING_PROFILE,
        is_default=True,
    )
    committed = False

    @asynccontextmanager
    async def transaction(_tenant_id: uuid.UUID):
        nonlocal committed
        committed = False
        yield fake_repo
        committed = True

    class CommitAwareGateway(FakeGateway):
        async def create_call(self, **kwargs) -> TwilioCall:
            assert committed, "Twilio fue invocado antes de que call+event fueran visibles"
            assert any(call["status"] == "confirmed" for call in fake_repo.phone_calls.values())
            return await super().create_call(**kwargs)

    dispatcher = phone.TransactionalPhoneDispatcher(
        repo_transaction=transaction,
        gateway=CommitAwareGateway(),
        tenant_id=tenant_id,
        user_id=user_id,
        public_base_url="https://assistant.test",
    )
    result = await dispatcher.create_and_dispatch(
        to_e164="+573002222222",
        recipient_name="Daniel Rojas",
        goal="Confirmar la cita",
        agent_template_id=next(
            template["id"]
            for template in fake_repo.phone_agent_templates.values()
            if template["agent_name"] == "Mateo"
        ),
    )
    assert result["status"] == "queued"
    assert result["agent_name"] == "Mateo"
    assert result["agent_prompt"].startswith("Haz preguntas claras y resume el acuerdo.")
    assert "FUNCIÓN Y MISIÓN" in result["agent_prompt"]


async def test_dispatcher_failure_also_persists_summary_and_schedules_safe_push(
    fake_repo,
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    await _phone_ready(fake_repo, tenant_id=tenant_id, user_id=user_id)

    @asynccontextmanager
    async def transaction(_tenant_id: uuid.UUID):
        yield fake_repo

    class FailingGateway(FakeGateway):
        async def create_call(self, **kwargs) -> TwilioCall:
            self.calls.append(kwargs)
            raise TelephonyError("Twilio temporalmente no disponible")

    notifications: list[tuple[uuid.UUID, uuid.UUID, uuid.UUID | None]] = []

    async def summary_ready(
        resolved_tenant_id: uuid.UUID, call_id: uuid.UUID, chat_id: uuid.UUID | None = None
    ) -> None:
        notifications.append((resolved_tenant_id, call_id, chat_id))

    dispatcher = phone.TransactionalPhoneDispatcher(
        repo_transaction=transaction,
        gateway=FailingGateway(),
        tenant_id=tenant_id,
        user_id=user_id,
        public_base_url="https://assistant.test",
        on_summary_ready=summary_ready,
    )
    with pytest.raises(TelephonyError, match="temporalmente no disponible"):
        await dispatcher.create_and_dispatch(
            to_e164="+573002222222",
            recipient_name="Daniel Rojas",
            goal="Confirmar la cita",
        )

    failed = next(iter(fake_repo.phone_calls.values()))
    assert failed["status"] == "failed"
    assert failed["summary"]["transcript"]["available"] is False
    # Ni siquiera una llamada que murió antes de salir se queda muda en el chat: el
    # dueño se entera de que no se habló, y el aviso apunta a esa conversación.
    principal = await fake_repo.get_main_conversation(tenant_id=tenant_id, user_id=user_id)
    assert principal is not None
    assert notifications == [(tenant_id, failed["id"], principal["id"])]
    texto = fake_repo.messages[principal["id"]][0]["content"]["text"]
    assert "Daniel Rojas (+573002222222)" in texto
    assert "no llegó a hablarse" in texto
    assert "no quedó registrada ni una palabra" in texto
    assert (
        len(
            [
                event
                for event in fake_repo.phone_call_events[failed["id"]]
                if event["event_type"] == "activity"
            ]
        )
        == 1
    )


async def test_gather_hangs_up_at_configured_turn_limit(app, client, fake_repo) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    conversation = await fake_repo.create_conversation(
        tenant_id=tenant_id, user_id=user_id, title="Llamada", channel="phone"
    )
    call = await fake_repo.create_phone_call(
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation["id"],
        direction="incoming",
        from_e164="+573003333333",
        to_e164="+573001111111",
        goal="Atender",
        status="in_progress",
        provider_call_sid="CA" + "6" * 32,
    )
    app.state.settings.PHONE_MAX_TURNS = 1
    app.state.phone_webhook_token_loader = lambda _tenant_id: "hook-token"
    app.state.phone_turn_runner = lambda _call, _speech: "Claro, quedó registrado."
    path = f"/v1/phone/twilio/calls/{call['id']}/gather"
    params = {"CallSid": "CA" + "6" * 32, "SpeechResult": "Necesito ayuda"}
    signature = twilio_signature(f"http://localhost:8000{path}", params, "hook-token")
    response = await client.post(path, data=params, headers={"X-Twilio-Signature": signature})
    assert response.status_code == 200
    assert "<Hangup" in response.text
    assert "<Gather" not in response.text


async def test_dispatcher_never_regresses_status_if_webhook_wins_race(fake_repo) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    await _phone_ready(fake_repo, tenant_id=tenant_id, user_id=user_id)

    @asynccontextmanager
    async def transaction(_tenant_id: uuid.UUID):
        yield fake_repo

    class RacingGateway(FakeGateway):
        async def create_call(self, **kwargs) -> TwilioCall:
            call = next(iter(fake_repo.phone_calls.values()))
            await fake_repo.update_phone_call(
                tenant_id=tenant_id,
                call_id=call["id"],
                fields={"status": "in_progress", "provider_call_sid": "CA" + "5" * 32},
            )
            return TwilioCall(sid="CA" + "5" * 32, status="queued")

    result = await phone.TransactionalPhoneDispatcher(
        repo_transaction=transaction,
        gateway=RacingGateway(),
        tenant_id=tenant_id,
        user_id=user_id,
        public_base_url="https://assistant.test",
    ).create_and_dispatch(
        to_e164="+573002222222",
        recipient_name="Daniel Rojas",
        goal="Confirmar",
    )
    assert result["status"] == "in_progress"


async def test_gather_uses_safe_fallback_when_assistant_turn_fails(app, client, fake_repo) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    conversation = await fake_repo.create_conversation(
        tenant_id=tenant_id, user_id=user_id, title="Llamada", channel="phone"
    )
    call = await fake_repo.create_phone_call(
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation["id"],
        direction="incoming",
        from_e164="+573003333333",
        to_e164="+573001111111",
        goal="Atender",
        status="in_progress",
        provider_call_sid="CA" + "4" * 32,
    )
    app.state.phone_webhook_token_loader = lambda _tenant_id: "hook-token"

    async def broken_turn(_call, _speech):
        raise RuntimeError("secreto-del-proveedor-que-no-debe-salir")

    app.state.phone_turn_runner = broken_turn
    path = f"/v1/phone/twilio/calls/{call['id']}/gather"
    params = {"CallSid": "CA" + "4" * 32, "SpeechResult": "Necesito ayuda"}
    response = await client.post(
        path,
        data=params,
        headers={
            "X-Twilio-Signature": twilio_signature(
                f"http://localhost:8000{path}", params, "hook-token"
            )
        },
    )
    assert response.status_code == 200
    assert "Guardé tu respuesta" in response.text
    assert "secreto-del-proveedor" not in response.text
    assert any(
        event["event_type"] == "assistant_error"
        for event in fake_repo.phone_call_events[call["id"]]
    )


async def _phone_call_in_progress(fake_repo, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> dict:
    conversation = await fake_repo.create_conversation(
        tenant_id=tenant_id, user_id=user_id, title="Llamada", channel="phone"
    )
    return await fake_repo.create_phone_call(
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation["id"],
        direction="incoming",
        from_e164="+573003333333",
        to_e164="+573001111111",
        goal="Atender",
        status="in_progress",
        provider_call_sid="CA" + "1" * 32,
    )


async def test_queue_call_whisper_requires_ownership_and_active_call(client, fake_repo) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    call = await _phone_call_in_progress(fake_repo, tenant_id=tenant_id, user_id=user_id)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)

    # Un dueño ajeno (otro tenant o otro usuario del mismo tenant) no puede susurrarle
    # a una llamada que no es suya: 404, igual que el resto de endpoints de calls/{id}.
    other_tenant_headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    foreign = await client.post(
        f"/v1/phone/calls/{call['id']}/susurro",
        json={"text": "Pregúntale por el pago pendiente"},
        headers=other_tenant_headers,
    )
    assert foreign.status_code == 404
    other_user_same_tenant_headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id)
    foreign_user = await client.post(
        f"/v1/phone/calls/{call['id']}/susurro",
        json={"text": "Pregúntale por el pago pendiente"},
        headers=other_user_same_tenant_headers,
    )
    assert foreign_user.status_code == 404
    assert fake_repo.phone_call_events[call["id"]] == []

    # Una llamada que todavía no está en curso (draft/confirmed/terminada) tampoco
    # acepta susurros: no hay ningún turno próximo donde inyectarlo.
    not_started = await fake_repo.update_phone_call(
        tenant_id=tenant_id, call_id=call["id"], fields={"status": "queued"}
    )
    assert not_started is not None
    not_in_progress = await client.post(
        f"/v1/phone/calls/{call['id']}/susurro",
        json={"text": "Pregúntale por el pago pendiente"},
        headers=headers,
    )
    assert not_in_progress.status_code == 409

    await fake_repo.update_phone_call(
        tenant_id=tenant_id, call_id=call["id"], fields={"status": "in_progress"}
    )
    ok = await client.post(
        f"/v1/phone/calls/{call['id']}/susurro",
        json={"text": "  Pregúntale por el pago pendiente  "},
        headers=headers,
    )
    assert ok.status_code == 201
    assert ok.json()["text"] == "Pregúntale por el pago pendiente"
    assert [event["event_type"] for event in fake_repo.phone_call_events[call["id"]]] == [
        "susurro"
    ]

    empty = await client.post(
        f"/v1/phone/calls/{call['id']}/susurro", json={"text": "   "}, headers=headers
    )
    assert empty.status_code == 422


async def test_phone_reply_injects_pending_whisper_once_with_aria_prefix(
    app, fake_repo, monkeypatch
) -> None:
    """Frente 6a: el susurro se inyecta como mensaje de usuario en el SIGUIENTE turno
    que arma `_phone_reply` (Edecán es por turnos, no puede interrumpir uno en curso),
    con el mismo prefijo exacto que usa Aria, y se consume una sola vez."""
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    call = await _phone_call_in_progress(fake_repo, tenant_id=tenant_id, user_id=user_id)
    await fake_repo.add_phone_call_event(
        tenant_id=tenant_id,
        call_id=call["id"],
        event_type=phone.PHONE_WHISPER_EVENT_TYPE,
        payload={"text": "Pregúntale por el pago pendiente"},
    )

    fake_llm = FakeLLMRouter("Claro, le pregunto por el pago pendiente.")
    app.state.llm_router = fake_llm

    @asynccontextmanager
    async def fake_get_session(_tenant_id):
        yield object()

    app.state.get_session = fake_get_session
    # La rama real de `_phone_reply` siempre envuelve la sesión en `SqlRepo(session)`;
    # se sustituye por el fake para no necesitar Postgres real en este test.
    monkeypatch.setattr(phone, "SqlRepo", lambda _session: fake_repo)
    request = SimpleNamespace(app=app)

    reply = await phone._phone_reply(request, call=call, repo=fake_repo, speech="Hola")
    assert reply == "Claro, le pregunto por el pago pendiente."
    assert len(fake_llm.calls) == 1
    first_request = fake_llm.calls[0][2]
    whisper_messages = [
        message
        for message in first_request.messages
        if message.content.startswith(phone.PHONE_WHISPER_PREFIX)
    ]
    assert len(whisper_messages) == 1
    assert whisper_messages[0].role == "user"
    assert whisper_messages[0].content == (
        "[NOTA URGENTE DEL USUARIO — incorpórala con naturalidad en tu respuesta]: "
        "Pregúntale por el pago pendiente"
    )
    assert [event["event_type"] for event in fake_repo.phone_call_events[call["id"]]] == [
        "susurro",
        "susurro_consumido",
    ]

    # El siguiente turno, sin un susurro nuevo, no debe repetir el ya incorporado.
    second_reply = await phone._phone_reply(request, call=call, repo=fake_repo, speech="Sigo aquí")
    assert second_reply == "Claro, le pregunto por el pago pendiente."
    assert len(fake_llm.calls) == 2
    second_request = fake_llm.calls[1][2]
    assert not any(
        message.content.startswith(phone.PHONE_WHISPER_PREFIX)
        for message in second_request.messages
    )
    assert [event["event_type"] for event in fake_repo.phone_call_events[call["id"]]] == [
        "susurro",
        "susurro_consumido",
    ]


def test_external_phone_persona_removes_private_relationship_and_instructions() -> None:
    persona = phone._external_phone_persona(
        {
            "nombre_asistente": "Luna",
            "idioma": "es",
            "tono": "sereno",
            "formalidad": 1,
            "emojis": False,
            "instrucciones": "Menciona mi secreto fiscal 123",
            "rasgos": ["coqueta", "mi agenda privada"],
            "memoria_activada": True,
            "voice_id": None,
            "estilo_relacion": "romantico",
            "adulto_confirmado": True,
            "consentimiento_romantico": True,
        }
    )
    prompt = build_system_prompt(persona, memories=[])
    assert persona.nombre_asistente == "Luna"
    assert persona.tono == "sereno"
    assert persona.estilo_relacion == "profesional"
    assert "secreto fiscal" not in prompt
    assert "agenda privada" not in prompt
    assert "Estilo elegido: profesional" in prompt
    assert "Estilo elegido: romantico" not in prompt


async def test_chat_tool_without_twilio_returns_clear_domain_message(app, fake_repo) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    await fake_repo.create_phone_agent_template(
        tenant_id=tenant_id,
        user_id=user_id,
        name="Agente de negocios",
        agent_name="Valentina",
        persona_prompt="Habla como consultora de negocios.",
        default_goal="Presentar una propuesta.",
        opening_message="Hola.",
        operating_profile=OPERATING_PROFILE,
        is_default=True,
    )

    class EmptyVault:
        async def get(self, *_args):
            return None

    dispatcher = phone.phone_tool_dispatcher_for(
        request=SimpleNamespace(app=app),
        tenant_id=tenant_id,
        user_id=user_id,
        repo=fake_repo,
        vault=EmptyVault(),
    )
    ctx = SimpleNamespace(extras={"phone_call_dispatcher": dispatcher})
    result = await LlamarContactoTool().run(
        ctx,
        {
            "telefono_e164": "+573002222222",
            "destinatario": "Daniel Rojas",
            "objetivo": "Confirmar la cita",
            "agente": "Agente de negocios",
        },
    )
    assert "Conecta tu propio número de Twilio" in result.content


async def test_chat_tool_resolves_requested_agent_by_name_and_never_substitutes(
    app, fake_repo
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    await _phone_ready(fake_repo, tenant_id=tenant_id, user_id=user_id)
    business = await fake_repo.create_phone_agent_template(
        tenant_id=tenant_id,
        user_id=user_id,
        name="Agente de negocios",
        agent_name="Valentina",
        persona_prompt="Habla como consultora de negocios.",
        default_goal="Presentar una propuesta.",
        opening_message="Te llamo para conversar sobre una oportunidad.",
        operating_profile=OPERATING_PROFILE,
        is_default=False,
    )
    await fake_repo.create_phone_agent_template(
        tenant_id=tenant_id,
        user_id=user_id,
        name="Agente de ventas",
        agent_name="Camila",
        persona_prompt="Habla como asesora de ventas.",
        default_goal="Calificar una oportunidad.",
        opening_message="Te llamo para entender qué necesitas.",
        operating_profile=OPERATING_PROFILE,
        is_default=True,
    )

    class Vault:
        async def get(self, *_args):
            return SimpleNamespace(
                access_token='{"account_sid":"AC11111111111111111111111111111111",'
                '"auth_token":"token","phone_number":"+573001111111"}'
            )

    recorded: dict[str, object] = {}

    async def fake_create_and_dispatch(self, **kwargs):
        recorded.update(kwargs)
        return {
            "id": uuid.uuid4(),
            "conversation_id": uuid.uuid4(),
            "status": "queued",
            "agent_template_id": business["id"],
            "agent_template_name": business["name"],
            "agent_name": business["agent_name"],
        }

    original = phone.TransactionalPhoneDispatcher.create_and_dispatch
    phone.TransactionalPhoneDispatcher.create_and_dispatch = fake_create_and_dispatch
    try:
        dispatcher = phone.phone_tool_dispatcher_for(
            request=SimpleNamespace(app=app),
            tenant_id=tenant_id,
            user_id=user_id,
            repo=fake_repo,
            vault=Vault(),
        )
        result = await LlamarContactoTool().run(
            SimpleNamespace(extras={"phone_call_dispatcher": dispatcher}),
            {
                "telefono_e164": "+573002222222",
                "destinatario": "Daniel Rojas",
                "objetivo": "Presentar la empresa",
                "agente": "negocios",
            },
        )
    finally:
        phone.TransactionalPhoneDispatcher.create_and_dispatch = original

    assert recorded["agent_template_id"] == business["id"]
    assert "Valentina" in result.content


async def test_chat_tool_rejects_unknown_agent_instead_of_using_default(app, fake_repo) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    await _phone_ready(fake_repo, tenant_id=tenant_id, user_id=user_id)
    await fake_repo.create_phone_agent_template(
        tenant_id=tenant_id,
        user_id=user_id,
        name="Agente de ventas",
        agent_name="Camila",
        persona_prompt="Habla como asesora.",
        default_goal="Vender.",
        opening_message="Hola.",
        operating_profile=OPERATING_PROFILE,
        is_default=True,
    )

    class EmptyVault:
        async def get(self, *_args):
            return None

    dispatcher = phone.phone_tool_dispatcher_for(
        request=SimpleNamespace(app=app),
        tenant_id=tenant_id,
        user_id=user_id,
        repo=fake_repo,
        vault=EmptyVault(),
    )
    result = await LlamarContactoTool().run(
        SimpleNamespace(extras={"phone_call_dispatcher": dispatcher}),
        {
            "telefono_e164": "+573002222222",
            "destinatario": "Daniel Rojas",
            "objetivo": "Presentar la empresa",
            "agente": "Agente jurídico",
        },
    )
    assert "No encontré el agente" in result.content
    assert "Agente de ventas" in result.content


async def _llamada_en_curso(fake_repo, *, sid: str):
    """Llamada activa lista para recibir turnos de `gather` (mismo montaje que los de arriba)."""
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    conversation = await fake_repo.create_conversation(
        tenant_id=tenant_id, user_id=user_id, title="Llamada", channel="phone"
    )
    return await fake_repo.create_phone_call(
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation["id"],
        direction="outgoing",
        from_e164="+573001111111",
        to_e164="+573003333333",
        goal="Pasar el menú de la central",
        status="in_progress",
        provider_call_sid=sid,
    )


async def _turno(client, fake_repo, call, params: dict[str, str]):
    path = f"/v1/phone/twilio/calls/{call['id']}/gather"
    firma = twilio_signature(f"http://localhost:8000{path}", params, "hook-token")
    return await client.post(path, data=params, headers={"X-Twilio-Signature": firma})


async def test_gather_convierte_el_marcador_del_modelo_en_tonos_reales(
    app, client, fake_repo
) -> None:
    """El agente ya puede pasar un menú automático: `[[tonos:1]]` -> `<Play digits="1">`.

    Antes intentaría DECIR el número, que no marca nada y deja la llamada trabada en el menú.
    """
    sid = "CA" + "7" * 32
    call = await _llamada_en_curso(fake_repo, sid=sid)
    app.state.phone_webhook_token_loader = lambda _tenant_id: "hook-token"
    app.state.phone_turn_runner = lambda _call, _speech: "Un momento. [[tonos:1]]"

    response = await _turno(
        client, fake_repo, call, {"CallSid": sid, "SpeechResult": "Marque 1 para ventas"}
    )

    assert response.status_code == 200
    assert 'digits="1"' in response.text
    # El marcador nunca se pronuncia ni se guarda en la transcripción.
    assert "tonos" not in response.text
    eventos = fake_repo.phone_call_events[call["id"]]
    dichos = [
        e["payload"]["text"]
        for e in eventos
        if e["event_type"] == "transcript" and e["payload"].get("role") == "assistant"
    ]
    assert dichos and all("[[" not in texto for texto in dichos)


async def test_gather_trata_las_teclas_del_otro_lado_como_un_turno(
    app, client, fake_repo
) -> None:
    """Marcar una tecla no puede verse igual que un silencio."""
    sid = "CA" + "8" * 32
    call = await _llamada_en_curso(fake_repo, sid=sid)
    app.state.phone_webhook_token_loader = lambda _tenant_id: "hook-token"
    recibido: list[str] = []

    def _runner(_call, speech):
        recibido.append(speech)
        return "Anotado."

    app.state.phone_turn_runner = _runner

    response = await _turno(client, fake_repo, call, {"CallSid": sid, "Digits": "42"})

    assert response.status_code == 200
    assert recibido and "42" in recibido[0]
    assert "No alcancé a escucharte" not in response.text


async def test_respuesta_de_solo_tonos_no_deja_la_llamada_en_silencio(
    app, client, fake_repo
) -> None:
    """Twilio necesita un verbo antes del `<Play digits>`, y el silencio suena a llamada caída."""
    sid = "CA" + "9" * 32
    call = await _llamada_en_curso(fake_repo, sid=sid)
    app.state.phone_webhook_token_loader = lambda _tenant_id: "hook-token"
    app.state.phone_turn_runner = lambda _call, _speech: "[[tonos:3]]"

    response = await _turno(
        client, fake_repo, call, {"CallSid": sid, "SpeechResult": "Marque 3"}
    )

    assert response.status_code == 200
    assert 'digits="3"' in response.text
    assert "<Say" in response.text or "<Play>" in response.text


class TestConsentimientoDelDestinatario:
    """`EDECAN_PHONE_REQUIRE_RECIPIENT_CONSENT` decide si marcar exige consentimiento.

    Ese registro no protege al dueño de Edecán: documenta que la persona del otro lado
    aceptó recibir la llamada, y en varias jurisdicciones llamar o grabar sin él está
    regulado. Por eso viene ENCENDIDO y solo un "no" explícito lo apaga; quien lo apague
    asume por su cuenta la responsabilidad de a quién llama.
    """

    class _RepoSinConsentimiento:
        def __init__(self) -> None:
            self.consultas = 0

        async def has_phone_consent(self, **_kwargs) -> bool:
            self.consultas += 1
            return False

    async def test_por_defecto_lo_exige_y_consulta_la_base(self, monkeypatch) -> None:
        monkeypatch.delenv("EDECAN_PHONE_REQUIRE_RECIPIENT_CONSENT", raising=False)
        repo = self._RepoSinConsentimiento()

        vigente = await phone._consentimiento_vigente(
            repo, tenant_id=uuid.uuid4(), phone_e164="+573001234567"
        )

        assert vigente is False
        assert repo.consultas == 1

    async def test_apagado_no_consulta_siquiera(self, monkeypatch) -> None:
        # Sin el requisito no tiene sentido pegarle a la base por una respuesta que se
        # ignora: el ahorro además hace evidente que el gate quedó fuera del camino.
        monkeypatch.setenv("EDECAN_PHONE_REQUIRE_RECIPIENT_CONSENT", "0")
        repo = self._RepoSinConsentimiento()

        vigente = await phone._consentimiento_vigente(
            repo, tenant_id=uuid.uuid4(), phone_e164="+573001234567"
        )

        assert vigente is True
        assert repo.consultas == 0

    @pytest.mark.parametrize("valor", ["1", "true", "si", "cualquier-cosa"])
    async def test_cualquier_valor_que_no_sea_un_no_explicito_mantiene_el_gate(
        self, monkeypatch, valor: str
    ) -> None:
        # Fail-closed: una variable mal escrita NO debe desactivar la protección en silencio.
        monkeypatch.setenv("EDECAN_PHONE_REQUIRE_RECIPIENT_CONSENT", valor)
        repo = self._RepoSinConsentimiento()

        assert (
            await phone._consentimiento_vigente(
                repo, tenant_id=uuid.uuid4(), phone_e164="+573001234567"
            )
            is False
        )


class TestNumeroSalientePorPais:
    """Con varios números conectados, se marca desde el del MISMO país que el destino.

    Regresión real: llamando a Colombia, Edecán tomaba "el primero que devolviera la base" y
    le tocó un gratuito de EE.UU. (+1877). Ese número entrega peor hacia Colombia y al
    destinatario le aparece como llamada extranjera. Aria siempre marcó desde el número
    colombiano; esto iguala ese comportamiento sin hardcodear ningún país.
    """

    ESTADOS_UNIDOS = {
        "connector_key": phone.TWILIO_CONNECTOR_KEY,
        "status": "active",
        "external_account_id": "+18005550100",
    }
    COLOMBIA = {
        "connector_key": phone.TWILIO_CONNECTOR_KEY,
        "status": "active",
        "external_account_id": "+576015550100",
    }

    def test_prefiere_el_numero_del_pais_del_destino(self) -> None:
        elegido = phone._elegir_numero_saliente(
            [self.ESTADOS_UNIDOS, self.COLOMBIA], "+573001234567"
        )
        assert elegido["external_account_id"] == "+576015550100"

    def test_el_orden_de_la_base_no_decide(self) -> None:
        elegido = phone._elegir_numero_saliente(
            [self.COLOMBIA, self.ESTADOS_UNIDOS], "+15551234567"
        )
        assert elegido["external_account_id"] == "+18005550100"

    def test_sin_coincidencia_de_pais_usa_el_primero_activo(self) -> None:
        # Comportamiento de siempre: a nadie con un solo número se le cambia el saliente.
        elegido = phone._elegir_numero_saliente([self.ESTADOS_UNIDOS], "+573001234567")
        assert elegido["external_account_id"] == "+18005550100"

    def test_sin_destino_conserva_el_comportamiento_previo(self) -> None:
        elegido = phone._elegir_numero_saliente([self.ESTADOS_UNIDOS, self.COLOMBIA], None)
        assert elegido["external_account_id"] == "+18005550100"

    def test_ignora_cuentas_inactivas_y_de_otros_conectores(self) -> None:
        ruido = [
            {**self.COLOMBIA, "status": "revoked"},
            {"connector_key": "linkedin", "status": "active", "external_account_id": "+57300"},
            self.ESTADOS_UNIDOS,
        ]
        elegido = phone._elegir_numero_saliente(ruido, "+573001234567")
        assert elegido["external_account_id"] == "+18005550100"

    def test_sin_ninguna_cuenta_activa_devuelve_none(self) -> None:
        assert phone._elegir_numero_saliente([], "+573001234567") is None


# -- transcripción en vivo (frente 6b) ----------------------------------------------------


async def test_get_call_transcript_requires_ownership(client, fake_repo) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    call = await _phone_call_in_progress(fake_repo, tenant_id=tenant_id, user_id=user_id)

    other_tenant = await client.get(
        f"/v1/phone/calls/{call['id']}/transcript",
        headers=auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4()),
    )
    assert other_tenant.status_code == 404

    other_user_same_tenant = await client.get(
        f"/v1/phone/calls/{call['id']}/transcript",
        headers=auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id),
    )
    assert other_user_same_tenant.status_code == 404

    missing = await client.get(
        f"/v1/phone/calls/{uuid.uuid4()}/transcript",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
    )
    assert missing.status_code == 404


async def test_get_call_transcript_polls_only_new_turns_since_cursor(
    client, fake_repo
) -> None:
    """El caso de uso central del frente 6b: la pantalla de llamada en curso sondea con
    el `next_cursor` de la respuesta anterior y nunca vuelve a ver un turno ya entregado,
    aunque entre eventos que no son `transcript` (acá: `status`)."""
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    call = await _phone_call_in_progress(fake_repo, tenant_id=tenant_id, user_id=user_id)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)

    await fake_repo.add_phone_call_event(
        tenant_id=tenant_id,
        call_id=call["id"],
        event_type="transcript",
        payload={"role": "caller", "text": "Hola, buenas tardes"},
    )
    await fake_repo.add_phone_call_event(
        tenant_id=tenant_id,
        call_id=call["id"],
        event_type="transcript",
        payload={"role": "assistant", "text": "Buenas tardes, ¿en qué le puedo ayudar?"},
    )

    first = await client.get(f"/v1/phone/calls/{call['id']}/transcript", headers=headers)
    assert first.status_code == 200
    body = first.json()
    assert body["call_id"] == str(call["id"])
    assert body["status"] == "in_progress"
    assert body["done"] is False
    assert [turn["role"] for turn in body["turns"]] == ["caller", "assistant"]
    assert [turn["text"] for turn in body["turns"]] == [
        "Hola, buenas tardes",
        "Buenas tardes, ¿en qué le puedo ayudar?",
    ]
    cursor = body["next_cursor"]
    assert cursor is not None

    # Sin turnos nuevos, el cursor no avanza y no se repite nada.
    again = await client.get(
        f"/v1/phone/calls/{call['id']}/transcript", params={"after": cursor}, headers=headers
    )
    assert again.status_code == 200
    assert again.json()["turns"] == []
    assert again.json()["next_cursor"] == cursor

    # Un evento que NO es `transcript` (p.ej. `status`) no debe reaparecer como turno,
    # pero sí debe mover el cursor para no volver a escanearlo en el próximo sondeo.
    await fake_repo.add_phone_call_event(
        tenant_id=tenant_id, call_id=call["id"], event_type="status", payload={"status": "x"}
    )
    await fake_repo.add_phone_call_event(
        tenant_id=tenant_id,
        call_id=call["id"],
        event_type="transcript",
        payload={"role": "caller", "text": "Quería preguntar por mi pedido"},
    )
    third = await client.get(
        f"/v1/phone/calls/{call['id']}/transcript", params={"after": cursor}, headers=headers
    )
    assert third.status_code == 200
    third_body = third.json()
    assert [turn["text"] for turn in third_body["turns"]] == ["Quería preguntar por mi pedido"]
    assert third_body["next_cursor"] != cursor

    # Turnos con role/text inválidos se descartan, igual que en el resumen determinista.
    await fake_repo.add_phone_call_event(
        tenant_id=tenant_id,
        call_id=call["id"],
        event_type="transcript",
        payload={"role": "caller", "text": "   "},
    )
    fourth = await client.get(
        f"/v1/phone/calls/{call['id']}/transcript",
        params={"after": third_body["next_cursor"]},
        headers=headers,
    )
    assert fourth.json()["turns"] == []


async def test_get_call_transcript_unknown_cursor_falls_back_to_full_history(
    client, fake_repo
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    call = await _phone_call_in_progress(fake_repo, tenant_id=tenant_id, user_id=user_id)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)
    await fake_repo.add_phone_call_event(
        tenant_id=tenant_id,
        call_id=call["id"],
        event_type="transcript",
        payload={"role": "caller", "text": "Hola"},
    )

    response = await client.get(
        f"/v1/phone/calls/{call['id']}/transcript",
        params={"after": str(uuid.uuid4())},
        headers=headers,
    )
    assert response.status_code == 200
    assert [turn["text"] for turn in response.json()["turns"]] == ["Hola"]


async def test_get_call_transcript_done_reflects_terminal_status(client, fake_repo) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    call = await _phone_call_in_progress(fake_repo, tenant_id=tenant_id, user_id=user_id)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)

    await fake_repo.update_phone_call(
        tenant_id=tenant_id, call_id=call["id"], fields={"status": "completed"}
    )
    response = await client.get(f"/v1/phone/calls/{call['id']}/transcript", headers=headers)
    assert response.status_code == 200
    assert response.json()["done"] is True
