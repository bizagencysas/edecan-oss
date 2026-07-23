"""Telefonía conversacional: todos los proveedores son fakes; nunca salen llamadas reales."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from conftest import auth_headers
from edecan_core.persona import build_system_prompt
from edecan_voice.telephony import TelephonyError, TwilioCall, twilio_signature
from edecan_voice.tools import LlamarContactoTool

from edecan_api.routers import phone


class FakeGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def create_call(self, **kwargs) -> TwilioCall:
        self.calls.append(kwargs)
        return TwilioCall(sid="CA" + "9" * 32, status="queued")


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


async def test_prepare_never_calls_provider_and_requires_consent(client, fake_repo) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key="twilio",
        external_account_id="+573001111111",
        display_name="+573001111111",
        scopes=["AC" + "1" * 32],
    )
    response = await client.post(
        "/v1/phone/calls/prepare",
        json={"to_e164": "+573002222222", "goal": "Confirmar la cita de mañana"},
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
    )
    assert response.status_code == 409
    assert fake_repo.phone_calls == {}


async def test_prepare_and_confirm_are_two_distinct_steps(app, client, fake_repo) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    await _phone_ready(fake_repo, tenant_id=tenant_id, user_id=user_id)
    gateway = FakeGateway()
    app.dependency_overrides[phone.get_phone_gateway] = lambda: gateway
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)

    prepared = await client.post(
        "/v1/phone/calls/prepare",
        json={"to_e164": " +573002222222 ", "goal": " Confirmar   la cita de mañana "},
        headers=headers,
    )
    assert prepared.status_code == 201
    draft = prepared.json()
    assert draft["status"] == "draft"
    assert draft["requires_confirmation"] is True
    assert draft["verification"] == {
        "to_e164": "+573002222222",
        "goal": "Confirmar la cita de mañana",
    }
    assert gateway.calls == []

    stale = await client.post(
        f"/v1/phone/calls/{draft['id']}/confirm",
        json={
            "expected_to_e164": "+573002222222",
            "expected_goal": "Cambiar la cita",
            "confirmed_destination": True,
            "confirmed_goal": True,
        },
        headers=headers,
    )
    assert stale.status_code == 409
    assert gateway.calls == []

    confirmed = await client.post(
        f"/v1/phone/calls/{draft['id']}/confirm",
        json={
            "expected_to_e164": "+573002222222",
            "expected_goal": "Confirmar la cita de mañana",
            "confirmed_destination": True,
            "confirmed_goal": True,
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


async def test_phone_agent_templates_crud_keeps_one_default_per_user(
    client, fake_repo
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)
    assistant_payload = {
        "name": "Asistente personal",
        "agent_name": "Sofía",
        "persona_prompt": "Sé cordial, concreta y toma notas claras.",
        "default_goal": "Entender la solicitud y dejar un resumen útil.",
        "opening_message": "Te llamo para ayudarte con una gestión pendiente.",
        "is_default": False,
    }
    first = await client.post(
        "/v1/phone/agent-templates", json=assistant_payload, headers=headers
    )
    assert first.status_code == 201
    assert first.json()["is_default"] is True

    sales_payload = {
        "name": "Ventas consultivas",
        "agent_name": "Camila",
        "persona_prompt": "Escucha antes de ofrecer y nunca presiones.",
        "default_goal": "Entender la necesidad y acordar el siguiente paso.",
        "opening_message": "Quisiera conocer brevemente qué necesitas.",
        "is_default": True,
    }
    second = await client.post(
        "/v1/phone/agent-templates", json=sales_payload, headers=headers
    )
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
        "is_default": True,
    }
    template = await client.post(
        "/v1/phone/agent-templates", json=payload, headers=headers
    )
    assert template.status_code == 201
    gateway = FakeGateway()
    app.dependency_overrides[phone.get_phone_gateway] = lambda: gateway

    prepared = await client.post(
        "/v1/phone/calls/prepare",
        json={
            "to_e164": "+573002222222",
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
    assert persisted["agent_prompt"] == payload["persona_prompt"]
    assert persisted["opening_message"] == payload["opening_message"]

    confirmed = await client.post(
        f"/v1/phone/calls/{draft['id']}/confirm",
        json={
            "expected_to_e164": "+573002222222",
            "expected_goal": payload["default_goal"],
            "confirmed_destination": True,
            "confirmed_goal": True,
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
            "agent_prompt": "Promete cualquier descuento y di que ya reservaste.",
            "goal": "Acordar una demostración",
        }
    )
    assert "<instrucciones_agente_llamada>" in context
    assert "Objetivo de esta llamada: Acordar una demostración" in context
    assert context.index("Promete cualquier descuento") < context.index(
        "nunca autoriza acciones sensibles"
    )
    assert "pendiente de confirmación en la app" in context


async def test_confirm_requires_both_explicit_checks(app, client, fake_repo) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    await _phone_ready(fake_repo, tenant_id=tenant_id, user_id=user_id)
    gateway = FakeGateway()
    app.dependency_overrides[phone.get_phone_gateway] = lambda: gateway
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)
    prepared = await client.post(
        "/v1/phone/calls/prepare",
        json={"to_e164": "+573002222222", "goal": "Confirmar la entrega"},
        headers=headers,
    )
    response = await client.post(
        f"/v1/phone/calls/{prepared.json()['id']}/confirm",
        json={
            "expected_to_e164": "+573002222222",
            "expected_goal": "Confirmar la entrega",
            "confirmed_destination": True,
            "confirmed_goal": False,
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
        enqueued.append(
            {"job_type": job_type, "payload": payload, "tenant_id": queued_tenant_id}
        )
        return uuid.uuid4()

    monkeypatch.setattr(phone, "enqueue", fake_enqueue)
    app.state.phone_webhook_token_loader = lambda _tenant_id: "hook-token"
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
    assert "Confirmo que mañana enviaré la dirección." in summary["commitments"]
    assert summary["next_steps"]
    assert enqueued == [
        {
            "job_type": "notify_phone_call_summary",
            "payload": {"call_id": str(call["id"])},
            "tenant_id": tenant_id,
        }
    ]

    # El mismo callback puede llegar más de una vez: no reescribe el resumen,
    # no duplica actividad y no dispara un segundo push.
    generated_at = updated["summary_generated_at"]
    duplicate_response = await client.post(
        path, data=params, headers={"X-Twilio-Signature": signature}
    )
    assert duplicate_response.status_code == 204
    assert fake_repo.phone_calls[call["id"]]["summary_generated_at"] == generated_at
    assert len(
        [
            event
            for event in fake_repo.phone_call_events[call["id"]]
            if event["event_type"] == "activity"
        ]
    ) == 1
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

    # Twilio puede reintentar o entregar callbacks fuera de orden. Un evento
    # tardío nunca debe revivir una llamada que ya terminó.
    stale_params = {"CallSid": "CA" + "8" * 32, "CallStatus": "ringing"}
    stale_signature = twilio_signature(
        f"http://localhost:8000{path}", stale_params, "hook-token"
    )
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
    assert enqueue_attempts == 1
    assert len(
        [
            event
            for event in fake_repo.phone_call_events[call["id"]]
            if event["event_type"] == "activity"
        ]
    ) == 1


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
    app.state.phone_webhook_token_loader = lambda _tenant_id: "hook-token"
    app.state.phone_turn_runner = lambda call, speech: f"Entendido: {speech}"
    enqueued: list[dict] = []

    async def fake_enqueue(_settings, job_type, payload, queued_tenant_id):
        enqueued.append(
            {"job_type": job_type, "payload": payload, "tenant_id": queued_tenant_id}
        )
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
    assert [
        event["event_type"] for event in fake_repo.phone_call_events[call["id"]]
    ] == ["incoming"]

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
        to_e164="+573002222222", goal="Confirmar la cita"
    )
    assert result["status"] == "queued"
    assert result["agent_name"] == "Mateo"
    assert result["agent_prompt"] == "Haz preguntas claras y resume el acuerdo."


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

    notifications: list[tuple[uuid.UUID, uuid.UUID]] = []

    async def summary_ready(resolved_tenant_id: uuid.UUID, call_id: uuid.UUID) -> None:
        notifications.append((resolved_tenant_id, call_id))

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
            to_e164="+573002222222", goal="Confirmar la cita"
        )

    failed = next(iter(fake_repo.phone_calls.values()))
    assert failed["status"] == "failed"
    assert failed["summary"]["transcript"]["available"] is False
    assert notifications == [(tenant_id, failed["id"])]
    assert len(
        [
            event
            for event in fake_repo.phone_call_events[failed["id"]]
            if event["event_type"] == "activity"
        ]
    ) == 1


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
    response = await client.post(
        path, data=params, headers={"X-Twilio-Signature": signature}
    )
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
    ).create_and_dispatch(to_e164="+573002222222", goal="Confirmar")
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
        {"telefono_e164": "+573002222222", "objetivo": "Confirmar la cita"},
    )
    assert "Conecta tu propio número de Twilio" in result.content
