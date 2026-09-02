"""`edecan_agents.tools.DelegarMisionTool` — crea la fila `agent_missions` y
encola el job `run_mission` (`ROADMAP_V2.md` §7.7, §7.9).

`enqueue` se monkeypatchea sobre el NOMBRE importado en `edecan_agents.tools`
(mismo patrón que `edecan_worker.handlers.*` con `SqlRepo`): así el test
nunca abre una conexión SQS real ni importa `edecan_core` él mismo.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import edecan_agents.tools as tools_module
import pytest
from edecan_agents.tools import DelegarMisionTool, get_all_tools
from edecan_toolkit.recordatorios import CrearRecordatorioTool


def _install_fake_enqueue(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, str, dict, Any]]:
    llamadas: list[tuple[Any, str, dict, Any]] = []

    async def fake_enqueue(settings, job_type, payload, tenant_id, **kwargs):
        llamadas.append((settings, job_type, payload, tenant_id))
        return uuid4()

    monkeypatch.setattr(tools_module, "enqueue", fake_enqueue)
    return llamadas


def test_metadatos_de_la_tool():
    tool = DelegarMisionTool()
    assert tool.name == "delegar_mision"
    assert tool.dangerous is False
    assert tool.requires_flags == frozenset({"agents.missions"})
    assert "objetivo" in tool.input_schema["properties"]
    assert tool.input_schema["required"] == ["objetivo"]


def test_get_all_tools_expone_delegacion_y_mensajeria_entre_bots():
    tools = get_all_tools()
    nombres = [type(t).__name__ for t in tools]
    assert nombres == [
        "DelegarMisionTool",
        "EnviarMensajeBotTool",
        "ListarBotsTool",
        "EncargarAEquipoTool",
    ]


async def test_rechaza_objetivo_vacio_sin_tocar_sesion_ni_encolar(
    make_ctx, make_session, monkeypatch: pytest.MonkeyPatch
):
    llamadas = _install_fake_enqueue(monkeypatch)
    session = make_session()
    ctx = make_ctx(session=session)

    resultado = await DelegarMisionTool().run(ctx, {"objetivo": "   "})

    assert "objetivo" in resultado.content.lower()
    assert session.llamadas == []
    assert llamadas == []


async def test_rechaza_objetivo_faltante(make_ctx, make_session, monkeypatch: pytest.MonkeyPatch):
    llamadas = _install_fake_enqueue(monkeypatch)
    ctx = make_ctx(session=make_session())

    resultado = await DelegarMisionTool().run(ctx, {})

    assert "objetivo" in resultado.content.lower()
    assert llamadas == []


def _flags_cupo_ilimitado() -> dict[str, Any]:
    return {"flags": {tools_module.LIMIT_MISSIONS_PER_DAY: tools_module.UNLIMITED}}


async def test_crea_la_mision_y_encola_run_mission(
    make_ctx, make_session, monkeypatch: pytest.MonkeyPatch
):
    llamadas = _install_fake_enqueue(monkeypatch)
    session = make_session()
    tenant_id = uuid4()
    user_id = uuid4()
    ctx = make_ctx(
        session=session, tenant_id=tenant_id, user_id=user_id, extras=_flags_cupo_ilimitado()
    )

    resultado = await DelegarMisionTool().run(ctx, {"objetivo": "Investiga el mercado de CRMs"})

    assert "misión" in resultado.content.lower()
    assert "mission_id" in resultado.data

    # 1) la misión + 2) el side-record TASK en agent_messages.
    assert len(session.llamadas) == 2
    sql, params = session.llamadas[0]
    assert "INSERT INTO agent_missions" in sql
    assert "'planning'" in sql
    assert params["tenant_id"] == str(tenant_id)
    assert params["user_id"] == str(user_id)
    assert params["objetivo"] == "Investiga el mercado de CRMs"
    assert params["id"] == resultado.data["mission_id"]

    sql_msg, params_msg = session.llamadas[1]
    assert "INSERT INTO agent_messages" in sql_msg
    assert params_msg["tenant_id"] == str(tenant_id)
    assert params_msg["sender"] is None
    assert params_msg["receiver"] is None
    assert params_msg["message_type"] == "task"
    assert params_msg["task_id"] == resultado.data["mission_id"]
    assert params_msg["goal"] == "Investiga el mercado de CRMs"

    assert len(llamadas) == 1


async def test_mision_encolada_queda_asociada_a_la_sesion_unificada(
    make_ctx, make_session, monkeypatch: pytest.MonkeyPatch
):
    llamadas = _install_fake_enqueue(monkeypatch)
    session = make_session()
    tenant_id = uuid4()
    unified = SimpleNamespace(active_task=None)
    unified.attach_task = lambda task_id: setattr(unified, "active_task", task_id)
    ctx = make_ctx(
        session=session,
        tenant_id=tenant_id,
        extras={
            **_flags_cupo_ilimitado(),
            "unified_session": unified,
        },
    )

    resultado = await DelegarMisionTool().run(ctx, {"objetivo": "Prepara un informe"})

    assert llamadas
    assert unified.active_task == resultado.data["mission_id"]
    _settings, job_type, payload, enq_tenant_id = llamadas[0]
    assert job_type == "run_mission"
    assert payload == {"mission_id": resultado.data["mission_id"]}
    assert enq_tenant_id == tenant_id


async def test_mision_y_recordatorio_encadenados_comparten_contexto_real(
    make_ctx, monkeypatch: pytest.MonkeyPatch
):
    """P0-D: la misión se crea/encola y el recordatorio posterior aterriza
    con el mismo tenant/usuario, sin convertir la cadena en dos fakes aislados.

    La ejecución posterior de `run_mission` sigue siendo un gate separado;
    esta prueba fija el contrato del encadenamiento que el turno sí controla.
    """

    llamadas_enqueue = _install_fake_enqueue(monkeypatch)
    tenant_id, user_id = uuid4(), uuid4()
    reminder_id = "11111111-1111-4111-1111-111111111111"

    class Result:
        def mappings(self):
            return self

        def first(self):
            return {"id": reminder_id}

    class Session:
        def __init__(self):
            self.llamadas: list[tuple[str, dict[str, Any]]] = []

        async def execute(self, statement, params=None):
            self.llamadas.append((str(statement), dict(params or {})))
            return Result()

    session = Session()
    unified = SimpleNamespace(active_task=None)
    unified.attach_task = lambda task_id: setattr(unified, "active_task", task_id)
    ctx = make_ctx(
        session=session,
        tenant_id=tenant_id,
        user_id=user_id,
        extras={
            **_flags_cupo_ilimitado(),
            "unified_session": unified,
        },
    )

    mission = await DelegarMisionTool().run(ctx, {"objetivo": "Preparar despliegue"})
    reminder = await CrearRecordatorioTool().run(
        ctx,
        {"mensaje": "Desplegar la solución preparada", "due_at": "2026-08-22T09:00:00Z"},
    )

    assert mission.data["mission_id"] == unified.active_task
    assert reminder.data["id"] == reminder_id
    assert len(llamadas_enqueue) == 1
    assert llamadas_enqueue[0][1] == "run_mission"
    # 1) misión + 2) side-record TASK (agent_messages) + 3) recordatorio.
    assert len(session.llamadas) == 3
    # El side-record TASK no lleva `user_id` (protocolo agente→agente, solo
    # tenant); la misión y el recordatorio sí comparten el mismo tenant/usuario.
    for sql, params in session.llamadas:
        assert params["tenant_id"] == str(tenant_id)
        if "INSERT INTO agent_missions" in sql or "INSERT INTO reminders" in sql:
            assert params["user_id"] == str(user_id)


async def test_presupuesto_usa_missions_max_steps_de_settings(
    make_ctx, make_session, monkeypatch: pytest.MonkeyPatch
):
    _install_fake_enqueue(monkeypatch)
    session = make_session()
    ctx = make_ctx(
        session=session,
        settings=SimpleNamespace(MISSIONS_MAX_STEPS=3),
        extras=_flags_cupo_ilimitado(),
    )

    await DelegarMisionTool().run(ctx, {"objetivo": "x"})

    _sql, params = session.llamadas[0]
    assert json.loads(params["presupuesto"]) == {"max_steps": 3}


async def test_presupuesto_usa_default_8_si_settings_no_trae_missions_max_steps(
    make_ctx, make_session, monkeypatch: pytest.MonkeyPatch
):
    _install_fake_enqueue(monkeypatch)
    session = make_session()
    ctx = make_ctx(session=session, extras=_flags_cupo_ilimitado())

    await DelegarMisionTool().run(ctx, {"objetivo": "x"})

    _sql, params = session.llamadas[0]
    assert json.loads(params["presupuesto"]) == {"max_steps": 8}


# ---------------------------------------------------------------------------
# `limits.missions_per_day` — Hallazgo 2 de `docs/seguridad-modelo-amenazas.md`
# (RESUELTO): `delegar_mision` debe respetar el mismo cupo diario que
# `POST /v1/missions` (`missions.py::_check_missions_quota`), no solo el flag
# booleano `agents.missions`.
# ---------------------------------------------------------------------------


async def test_rechaza_objetivo_vacio_antes_de_revisar_cupo(
    make_ctx, make_session, monkeypatch: pytest.MonkeyPatch
):
    """La validación de `objetivo` corre ANTES del chequeo de cupo (barato,
    sin I/O) — un objetivo vacío nunca debería gastar una consulta de cuota."""
    llamadas = _install_fake_enqueue(monkeypatch)
    session = make_session()
    ctx = make_ctx(session=session)  # sin flags: si el orden fuera al revés, bloquearía igual

    resultado = await DelegarMisionTool().run(ctx, {"objetivo": ""})

    assert "objetivo" in resultado.content.lower()
    assert session.llamadas == []
    assert llamadas == []


async def test_bloquea_si_cupo_diario_agotado_sin_insertar_ni_encolar(
    make_ctx, make_session, monkeypatch: pytest.MonkeyPatch
):
    llamadas = _install_fake_enqueue(monkeypatch)
    session = make_session()
    session.scalar_results = [2]  # ya hay 2 misiones creadas hoy
    tenant_id = uuid4()
    ctx = make_ctx(
        session=session,
        tenant_id=tenant_id,
        extras={"flags": {tools_module.LIMIT_MISSIONS_PER_DAY: 2}},
    )

    resultado = await DelegarMisionTool().run(ctx, {"objetivo": "Investiga el mercado de CRMs"})

    assert "límite" in resultado.content.lower()
    assert resultado.data is None

    # Solo se ejecutó el SELECT COUNT de cuota -- el INSERT nunca corrió.
    assert len(session.llamadas) == 1
    sql, params = session.llamadas[0]
    assert "SELECT COUNT(*) FROM agent_missions" in sql
    assert params["tenant_id"] == str(tenant_id)
    assert llamadas == []


async def test_bloquea_si_limite_es_cero_sin_consultar_la_bd(
    make_ctx, make_session, monkeypatch: pytest.MonkeyPatch
):
    """`limite == 0` (plan sin esta capacidad en absoluto, mismo criterio que
    `_check_missions_quota`) bloquea sin gastar ni siquiera el SELECT COUNT."""
    llamadas = _install_fake_enqueue(monkeypatch)
    session = make_session()
    ctx = make_ctx(session=session, extras={"flags": {tools_module.LIMIT_MISSIONS_PER_DAY: 0}})

    resultado = await DelegarMisionTool().run(ctx, {"objetivo": "x"})

    assert "límite" in resultado.content.lower()
    assert session.llamadas == []
    assert llamadas == []


async def test_bloquea_por_defecto_si_ctx_extras_no_trae_flags(
    make_ctx, make_session, monkeypatch: pytest.MonkeyPatch
):
    """Sin `ctx.extras["flags"]` en absoluto (nunca debería pasar en
    producción -- `conversations._build_ctx` siempre lo llena -- pero el
    default debe ser fail-closed, igual que `_tenant_flags` en el resto del
    repo), el límite efectivo es `0`: se bloquea, no se asume ilimitado."""
    llamadas = _install_fake_enqueue(monkeypatch)
    session = make_session()
    ctx = make_ctx(session=session)  # extras={} por default de la fixture

    resultado = await DelegarMisionTool().run(ctx, {"objetivo": "x"})

    assert "límite" in resultado.content.lower()
    assert session.llamadas == []
    assert llamadas == []


async def test_procede_si_hay_cupo_disponible_bajo_el_limite(
    make_ctx, make_session, monkeypatch: pytest.MonkeyPatch
):
    llamadas = _install_fake_enqueue(monkeypatch)
    session = make_session()
    session.scalar_results = [3]  # 3 misiones hoy, límite 5 -> queda cupo
    ctx = make_ctx(session=session, extras={"flags": {tools_module.LIMIT_MISSIONS_PER_DAY: 5}})

    resultado = await DelegarMisionTool().run(ctx, {"objetivo": "Investiga el mercado de CRMs"})

    assert "mission_id" in resultado.data
    assert len(session.llamadas) == 3
    sql_cupo, _params_cupo = session.llamadas[0]
    assert "SELECT COUNT(*) FROM agent_missions" in sql_cupo
    sql_insert, _params_insert = session.llamadas[1]
    assert "INSERT INTO agent_missions" in sql_insert
    sql_msg, _params_msg = session.llamadas[2]
    assert "INSERT INTO agent_messages" in sql_msg
    assert len(llamadas) == 1


async def test_procede_sin_consultar_la_bd_si_el_limite_es_ilimitado(
    make_ctx, make_session, monkeypatch: pytest.MonkeyPatch
):
    """`-1` (`UNLIMITED`) se salta el `SELECT COUNT` por completo -- mismo
    atajo que `_check_missions_quota` en el router."""
    llamadas = _install_fake_enqueue(monkeypatch)
    session = make_session()
    ctx = make_ctx(session=session, extras=_flags_cupo_ilimitado())

    resultado = await DelegarMisionTool().run(ctx, {"objetivo": "Investiga el mercado de CRMs"})

    assert "mission_id" in resultado.data
    assert len(session.llamadas) == 2
    sql, _params = session.llamadas[0]
    assert "INSERT INTO agent_missions" in sql
    sql_msg, _params_msg = session.llamadas[1]
    assert "INSERT INTO agent_messages" in sql_msg
    assert len(llamadas) == 1


# ---------------------------------------------------------------------------
# Delegación a otro worker (directiva §11-13): `destino_worker_id` escribe un
# `persistent_agent_handoffs` pendiente y enlaza la misión (`owner_agent_id`).
# ---------------------------------------------------------------------------


def _extras_con_worker(worker_id: str) -> dict[str, Any]:
    return {**_flags_cupo_ilimitado(), "worker_id": worker_id}


async def test_delega_a_worker_escribe_handoff_y_enlaza_owner_agent_id(
    make_ctx, make_session, monkeypatch: pytest.MonkeyPatch
):
    llamadas = _install_fake_enqueue(monkeypatch)
    session = make_session()
    session.row_results = [{"1": 1}]  # validación "el destino es tuyo" (SELECT 1)
    tenant_id = uuid4()
    user_id = uuid4()
    source = str(uuid4())
    destino = str(uuid4())
    ctx = make_ctx(
        session=session,
        tenant_id=tenant_id,
        user_id=user_id,
        extras=_extras_con_worker(source),
    )

    resultado = await DelegarMisionTool().run(
        ctx,
        {
            "objetivo": "Redacta el informe trimestral",
            "destino_worker_id": destino,
            "expected_output": "Un documento PDF de 3 páginas",
            "priority": "alta",
            "allowed_tools": ["leer_archivos", "crear_contenido_social"],
            "approval_boundary": "Publicar requiere aprobación",
        },
    )

    assert "mission_id" in resultado.data
    # validación + nombres + handoff + hilo + side-record + misión = 6 llamadas
    assert len(session.llamadas) == 6
    _sqls = [str(st) for st, _ in session.llamadas]
    _idx_handoff = next(i for i, st in enumerate(_sqls) if "INSERT INTO persistent_agent_handoffs" in st)
    _idx_chat = next(i for i, st in enumerate(_sqls) if "agent_direct_chats" in st)
    _idx_msg = next(i for i, st in enumerate(_sqls) if "INSERT INTO agent_messages" in st)
    _idx_mission = next(i for i, st in enumerate(_sqls) if "INSERT INTO agent_missions" in st)
    assert _idx_handoff != _idx_chat != _idx_msg != _idx_mission

    # 1) el handoff pendiente
    sql_handoff, params_handoff = session.llamadas[_idx_handoff]
    assert "INSERT INTO persistent_agent_handoffs" in sql_handoff
    assert params_handoff["source"] == source
    assert params_handoff["destination"] == destino
    assert params_handoff["task_id"] == resultado.data["mission_id"]
    assert params_handoff["tenant_id"] == str(tenant_id)
    assert params_handoff["depth"] == 1
    assert source in params_handoff["visitados"]
    envelope = json.loads(params_handoff["envelope"])
    assert envelope["goal"] == "Redacta el informe trimestral"
    assert envelope["expected_output"] == "Un documento PDF de 3 páginas"
    assert envelope["priority"] == "alta"
    assert envelope["allowed_tools"] == ["leer_archivos", "crear_contenido_social"]
    assert envelope["approval_boundary"] == "Publicar requiere aprobación"
    assert "Un documento PDF de 3 páginas" in envelope["instruction"]
    assert envelope["requires_human_approval"] is True

    # 2) el hilo directo que hace visible la conversación entre bots
    assert "INSERT INTO agent_direct_chats" in _sqls[_idx_chat]
    # 3) el side-record HANDOFF en agent_messages (protocolo §12)
    sql_msg, params_msg = session.llamadas[_idx_msg]
    assert "INSERT INTO agent_messages" in sql_msg
    assert params_msg["tenant_id"] == str(tenant_id)
    assert params_msg["sender"] == source
    assert params_msg["receiver"] == destino
    assert params_msg["message_type"] == "handoff"
    assert params_msg["task_id"] == resultado.data["mission_id"]
    assert params_msg["goal"] == "Redacta el informe trimestral"
    assert params_msg["expected_output"] == "Un documento PDF de 3 páginas"
    assert params_msg["priority"] == "alta"
    assert json.loads(params_msg["allowed_tools"]) == ["leer_archivos", "crear_contenido_social"]
    assert json.loads(params_msg["approval_boundary"]) == "Publicar requiere aprobación"

    # 4) la misión queda enlazada al worker dueño
    sql_mission, params_mission = session.llamadas[_idx_mission]
    assert "INSERT INTO agent_missions" in sql_mission
    assert "owner_agent_id" in sql_mission
    assert params_mission["owner_agent_id"] == destino
    assert params_mission["tenant_id"] == str(tenant_id)

    assert len(llamadas) == 1
    assert llamadas[0][1] == "run_mission"


async def test_delegar_sin_contexto_de_worker_devuelve_error_y_no_escribe(
    make_ctx, make_session, monkeypatch: pytest.MonkeyPatch
):
    llamadas = _install_fake_enqueue(monkeypatch)
    session = make_session()
    ctx = make_ctx(
        session=session, tenant_id=uuid4(), extras=_flags_cupo_ilimitado()
    )  # sin worker_id

    resultado = await DelegarMisionTool().run(
        ctx, {"objetivo": "Redacta el informe", "destino_worker_id": str(uuid4())}
    )

    assert "worker" in resultado.content.lower()
    assert resultado.data is None
    assert session.llamadas == []
    assert llamadas == []


async def test_un_worker_no_puede_delegarse_a_si_mismo(
    make_ctx, make_session, monkeypatch: pytest.MonkeyPatch
):
    llamadas = _install_fake_enqueue(monkeypatch)
    session = make_session()
    worker_id = str(uuid4())
    ctx = make_ctx(session=session, tenant_id=uuid4(), extras=_extras_con_worker(worker_id))

    resultado = await DelegarMisionTool().run(
        ctx, {"objetivo": "Redacta el informe", "destino_worker_id": worker_id}
    )

    assert "sí mismo" in resultado.content
    assert session.llamadas == []
    assert llamadas == []
