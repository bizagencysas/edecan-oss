from __future__ import annotations

import uuid

import edecan_worker.handlers.notify_phone_call_summary as handler_module
import pytest
from edecan_schemas import JobEnvelope
from fakes import FakeRepo, make_deps


def _envelope(tenant_id: uuid.UUID, call_id: uuid.UUID) -> JobEnvelope:
    return JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="notify_phone_call_summary",
        payload={"call_id": str(call_id)},
    )


def _call(tenant_id: uuid.UUID, user_id: uuid.UUID, call_id: uuid.UUID) -> dict:
    return {
        "id": call_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "summary": {
            "key_points": ["Dato privado de la conversación"],
            "participants": [{"phone_e164": "+573009999999"}],
        },
        "summary_push_attempted_at": None,
    }


async def test_push_se_reclama_una_sola_vez_y_el_cuerpo_no_filtra_datos(monkeypatch) -> None:
    tenant_id, user_id, call_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    repo = FakeRepo()
    repo.phone_calls[call_id] = _call(tenant_id, user_id, call_id)
    monkeypatch.setattr(handler_module, "SqlRepo", lambda _session: repo)
    calls: list[dict] = []

    async def fake_push(_deps, **kwargs):
        calls.append(kwargs)
        return handler_module.push.ResultadoEnvioPush(enviados=1, fallidos=0)

    monkeypatch.setattr(handler_module.push, "enviar_push_a_usuario", fake_push)
    env = _envelope(tenant_id, call_id)
    deps = make_deps()

    await handler_module.handle(env, deps)
    await handler_module.handle(env, deps)

    assert len(calls) == 1
    assert calls[0]["tenant_id"] == tenant_id
    assert calls[0]["user_id"] == user_id
    visible = f"{calls[0]['titulo']} {calls[0]['cuerpo']}"
    assert "+573009999999" not in visible
    assert "Dato privado" not in visible
    assert "Actividad" in visible
    assert calls[0]["data"] == {
        "route": "activity",
        "kind": "call",
        "resource_id": str(call_id),
    }
    assert repo.phone_calls[call_id]["summary_push_attempted_at"] is not None


async def test_sin_resumen_no_reclama_ni_envia(monkeypatch) -> None:
    tenant_id, user_id, call_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    repo = FakeRepo()
    row = _call(tenant_id, user_id, call_id)
    row["summary"] = None
    repo.phone_calls[call_id] = row
    monkeypatch.setattr(handler_module, "SqlRepo", lambda _session: repo)

    async def forbidden_push(*_args, **_kwargs):
        raise AssertionError("No debe intentar push sin resumen persistido")

    monkeypatch.setattr(handler_module.push, "enviar_push_a_usuario", forbidden_push)
    await handler_module.handle(_envelope(tenant_id, call_id), make_deps())

    assert repo.phone_calls[call_id]["summary_push_attempted_at"] is None


async def test_fallo_de_push_es_best_effort_y_no_habilita_duplicados(monkeypatch) -> None:
    tenant_id, user_id, call_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    repo = FakeRepo()
    repo.phone_calls[call_id] = _call(tenant_id, user_id, call_id)
    monkeypatch.setattr(handler_module, "SqlRepo", lambda _session: repo)
    attempts = 0

    async def broken_push(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise RuntimeError("proveedor caído")

    monkeypatch.setattr(handler_module.push, "enviar_push_a_usuario", broken_push)
    env = _envelope(tenant_id, call_id)
    await handler_module.handle(env, make_deps())
    await handler_module.handle(env, make_deps())

    assert attempts == 1
    assert repo.phone_calls[call_id]["summary_push_attempted_at"] is not None


async def test_payload_sin_call_id_falla_claro() -> None:
    with pytest.raises(ValueError, match="call_id UUID"):
        await handler_module.handle(
            JobEnvelope(
                job_id=uuid.uuid4(),
                tenant_id=uuid.uuid4(),
                type="notify_phone_call_summary",
                payload={},
            ),
            make_deps(),
        )
