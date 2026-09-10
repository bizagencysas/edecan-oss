"""Tests de `edecan_toolkit.avances.AvisarAvanceTool`."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from uuid import uuid4

from edecan_toolkit.avances import AvisarAvanceTool, _payload_push_avance_proactivo


async def test_avisar_avance_sin_worker_chat_devuelve_error(make_ctx):
    ctx = make_ctx(extras={})
    resultado = await AvisarAvanceTool().run(ctx, {"mensaje": "Voy a revisar el repo."})
    assert "no tiene chat propio" in resultado.content.lower()


async def test_avisar_avance_mensaje_vacio(make_ctx):
    ctx = make_ctx(
        extras={
            "worker_chat": {
                "conversation_id": str(uuid4()),
                "worker_id": str(uuid4()),
                "worker_name": "Botsito",
            }
        }
    )
    resultado = await AvisarAvanceTool().run(ctx, {"mensaje": "   "})
    assert "texto no vacío" in resultado.content.lower()


async def test_avisar_avance_inserta_en_sesion_propia_sin_tocar_la_del_turno(
    make_ctx, make_session, monkeypatch
):
    """El aviso se persiste en una SESIÓN DE CORTA VIDA separada y se confirma
    ahí mismo: la transacción del turno (`ctx.session`, abierta por
    `get_tenant_session` con `session.begin()`) NUNCA se commitea a mitad del
    turno — hacerlo la cerraba y todo lo posterior (el gate del dueño de la
    tool siguiente, el add_message final) reventaba con "closed transaction"."""
    sesion_turno = make_session()
    sesion_aviso = make_session()
    conv_id = str(uuid4())
    worker_id = str(uuid4())
    ctx = make_ctx(
        session=sesion_turno,
        extras={
            "worker_chat": {
                "conversation_id": conv_id,
                "worker_id": worker_id,
                "worker_name": "BotAlpha",
            }
        },
    )

    @asynccontextmanager
    async def fake_get_session(_tenant_id: object):
        yield sesion_aviso

    monkeypatch.setattr("edecan_db.session.get_session", fake_get_session)

    mensaje = "Encontré el bug: era un doble envío."
    resultado = await AvisarAvanceTool().run(ctx, {"mensaje": mensaje})
    assert resultado.content == mensaje

    # El INSERT del aviso fue a la sesión de aviso, NO a la del turno.
    assert len(sesion_aviso.llamadas) == 1
    sql, params = sesion_aviso.llamadas[0]
    assert "INSERT INTO messages" in sql
    assert params["cid"] == conv_id
    payload = json.loads(params["content"])
    assert payload["text"] == mensaje
    assert payload["sender_id"] == worker_id
    assert payload["sender_name"] == "BotAlpha"
    assert payload["kind"] == "aviso"

    # La sesión del turno queda INTACTA: sin writes ni commits ajenos.
    assert sesion_turno.llamadas == []
    assert sesion_turno.commits == 0


async def test_avisar_avance_encola_push_proactivo(make_ctx, make_session, monkeypatch):
    sesion_turno = make_session()
    sesion_aviso = make_session()
    conv_id = str(uuid4())
    worker_id = str(uuid4())
    owner_id = uuid4()
    encolados: list[dict] = []

    async def fake_enqueue(settings, job_type, payload, tenant_id):
        encolados.append(
            {"job_type": job_type, "payload": payload, "tenant_id": tenant_id}
        )
        return uuid4()

    monkeypatch.setattr("edecan_toolkit.avances.enqueue", fake_enqueue)

    @asynccontextmanager
    async def fake_get_session(_tenant_id: object):
        yield sesion_aviso

    monkeypatch.setattr("edecan_db.session.get_session", fake_get_session)

    ctx = make_ctx(
        session=sesion_turno,
        user_id=owner_id,
        extras={
            "worker_chat": {
                "conversation_id": conv_id,
                "worker_id": worker_id,
                "worker_name": "BotAlpha",
                "avatar_shape": "circle",
                "avatar_fill": "#6366f1",
                "avatar_accent": "#22c55e",
            }
        },
    )
    mensaje = "Voy a revisar tu LinkedIn ahora."
    await AvisarAvanceTool().run(ctx, {"mensaje": mensaje})

    assert len(encolados) == 1
    job = encolados[0]
    assert job["job_type"] == "notify_important_event"
    payload = job["payload"]
    assert payload["user_id"] == str(owner_id)
    assert payload["kind"] == "agent_bot_message"
    assert payload["chat_id"] == conv_id
    assert payload["worker_id"] == worker_id
    assert payload["apns_title"] == "BotAlpha"
    assert payload["apns_body"] == mensaje
    assert payload["sender_display_name"] == "BotAlpha"
    assert payload["avatar_shape"] == "circle"
    assert payload["avatar_fill"] == "#6366f1"
    assert payload["avatar_accent"] == "#22c55e"
    assert payload["event_id"]


def test_payload_push_avance_proactivo_trunca_y_normaliza(make_ctx):
    ctx = make_ctx(user_id=uuid4())
    chat = {
        "conversation_id": str(uuid4()),
        "worker_id": str(uuid4()),
        "worker_name": "  BotBeta  ",
    }
    cuerpo_largo = "x" * 300
    payload = _payload_push_avance_proactivo(ctx, chat, mensaje=f"  {cuerpo_largo}  ")
    assert payload["apns_title"] == "BotBeta"
    assert len(payload["apns_body"]) == 200
    assert payload["sender_display_name"] == "BotBeta"
