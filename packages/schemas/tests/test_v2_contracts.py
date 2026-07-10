"""Contratos v2 de `edecan_schemas` — ROADMAP_V2.md §7 (dueño WP-V2-01).

Cubre: la matriz de flags/límites v2 EXACTA de §7.2, `JOB_TYPES` con los 3
tipos nuevos de §7.3, y que los modelos Pydantic nuevos (§7.4: misiones,
automatizaciones, comercio, dispositivos/sesión remota, perfil vivo) validan.
No importa paquetes hermanos (ARCHITECTURE.md §10.1): solo `edecan_schemas`.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from edecan_schemas.automations import (
    AccionDef,
    AccionDefAdapter,
    AgentInstructionAccion,
    ScheduleTrigger,
    TriggerDefAdapter,
    WebhookTrigger,
)
from edecan_schemas.commerce import ORDER_KINDS, ORDER_STATUSES, OrderOut
from edecan_schemas.devices import (
    DEVICE_KINDS,
    DEVICE_STATUSES,
    REMOTE_SESSION_STATUSES,
    DeviceOut,
    RemoteSessionOut,
)
from edecan_schemas.missions import (
    MISSION_STATUSES,
    MISSION_STEP_STATUSES,
    MissionOut,
    MissionStepOut,
)
from edecan_schemas.plans import (
    BOOL_FLAGS,
    FLAG_AGENTS_MISSIONS,
    FLAG_AUTOMATIONS_RULES,
    FLAG_COMMERCE_ORDERS,
    FLAG_COMPANION_IDE,
    FLAG_COMPANION_REMOTE_VIEW,
    FLAG_CONNECTORS_MESSAGING,
    FLAG_TOOLS_BROWSER,
    FLAG_TOOLS_IMAGES,
    INT_LIMITS,
    LIMIT_AUTOMATIONS_ACTIVE,
    LIMIT_MISSIONS_PER_DAY,
    PLANES,
    UNLIMITED,
)
from edecan_schemas.profile import LiveProfile, ProfileData
from edecan_schemas.queue import JOB_TYPES
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# §7.2 — flags y límites nuevos
# ---------------------------------------------------------------------------

V2_BOOL_FLAGS = (
    FLAG_AGENTS_MISSIONS,
    FLAG_AUTOMATIONS_RULES,
    FLAG_TOOLS_BROWSER,
    FLAG_TOOLS_IMAGES,
    FLAG_COMPANION_IDE,
    FLAG_COMPANION_REMOTE_VIEW,
    FLAG_COMMERCE_ORDERS,
    FLAG_CONNECTORS_MESSAGING,
)

# Modelo de precio de pago único (2026-07-09, `edecan_schemas.plans`
# docstring): ya no hay matriz distinta por plan — las 4 entradas conceden
# todo por igual. (missions, automations, browser, images, ide,
# remote_view, commerce, messaging, misiones/día, autom. activas), idéntica
# en los 4 planes.
_TODO_TRUE_ILIMITADO = (True, True, True, True, True, True, True, True, UNLIMITED, UNLIMITED)
EXPECTED_MATRIX: dict[str, tuple[bool, bool, bool, bool, bool, bool, bool, bool, int, int]] = {
    "free_selfhost": _TODO_TRUE_ILIMITADO,
    "hosted_basic": _TODO_TRUE_ILIMITADO,
    "hosted_pro": _TODO_TRUE_ILIMITADO,
    "hosted_business": _TODO_TRUE_ILIMITADO,
}


def test_v2_bool_flags_estan_en_bool_flags():
    for flag in V2_BOOL_FLAGS:
        assert flag in BOOL_FLAGS


def test_v2_limits_estan_en_int_limits():
    assert LIMIT_MISSIONS_PER_DAY in INT_LIMITS
    assert LIMIT_AUTOMATIONS_ACTIVE in INT_LIMITS


@pytest.mark.parametrize(
    "plan_key", ["free_selfhost", "hosted_basic", "hosted_pro", "hosted_business"]
)
def test_matriz_v2_exacta_por_plan(plan_key: str):
    flags = PLANES[plan_key].flags
    esperado = EXPECTED_MATRIX[plan_key]
    real = (
        flags[FLAG_AGENTS_MISSIONS],
        flags[FLAG_AUTOMATIONS_RULES],
        flags[FLAG_TOOLS_BROWSER],
        flags[FLAG_TOOLS_IMAGES],
        flags[FLAG_COMPANION_IDE],
        flags[FLAG_COMPANION_REMOTE_VIEW],
        flags[FLAG_COMMERCE_ORDERS],
        flags[FLAG_CONNECTORS_MESSAGING],
        flags[LIMIT_MISSIONS_PER_DAY],
        flags[LIMIT_AUTOMATIONS_ACTIVE],
    )
    assert real == esperado


def test_matriz_v2_cubre_los_4_planes():
    assert set(EXPECTED_MATRIX) == set(PLANES)


# ---------------------------------------------------------------------------
# §7.3 — JOB_TYPES
# ---------------------------------------------------------------------------


def test_job_types_v1_y_v2_intactos_mas_v5_al_final():
    # Los primeros 10 (7 de v1 + 3 de v2) deben seguir intactos y en el mismo
    # orden — la comparación es por *slice*, no por igualdad exacta de toda
    # la tupla, porque v5 (ARCHITECTURE.md §14, WP-V5-01) suma un 11º tipo
    # (`generate_podcast`) al final; ver
    # `test_queue.py::test_job_types_incluye_generate_podcast_v5` para la
    # cobertura dedicada de ese 11º valor.
    assert JOB_TYPES[:10] == (
        "ingest_file",
        "sync_connector",
        "send_reminder",
        "send_reminder_scan",
        "run_campaign_step",
        "generate_content",
        "memory_consolidate",
        "run_mission",
        "run_automation",
        "automation_scan",
    )


# ---------------------------------------------------------------------------
# §7.4 — misiones
# ---------------------------------------------------------------------------


def test_mission_statuses_pinned():
    assert MISSION_STATUSES == (
        "planning",
        "running",
        "waiting_confirmation",
        "done",
        "error",
        "cancelled",
    )
    assert MISSION_STEP_STATUSES == (
        "pending",
        "running",
        "waiting_confirmation",
        "done",
        "error",
        "skipped",
    )


def test_mission_out_valida_con_defaults():
    mission = MissionOut(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        objetivo="Investigar competidores",
        presupuesto={"max_steps": 8},
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    assert mission.status == "planning"
    assert mission.plan is None
    assert mission.resultado is None


def test_mission_out_status_invalido_falla():
    with pytest.raises(ValidationError):
        MissionOut(
            id=uuid4(),
            tenant_id=uuid4(),
            user_id=uuid4(),
            objetivo="x",
            status="borrado",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )


def test_mission_step_out_valida():
    step = MissionStepOut(
        id=uuid4(),
        tenant_id=uuid4(),
        mission_id=uuid4(),
        seq=1,
        agente="research",
        instruccion="Buscar precios",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    assert step.status == "pending"


# ---------------------------------------------------------------------------
# §7.4/§7.7 — automatizaciones (forma de trigger/accion)
# ---------------------------------------------------------------------------


def test_trigger_def_discrimina_schedule_y_webhook():
    schedule = TriggerDefAdapter.validate_python(
        {"kind": "schedule", "rrule": "FREQ=DAILY;BYHOUR=9"}
    )
    assert isinstance(schedule, ScheduleTrigger)

    webhook = TriggerDefAdapter.validate_python({"kind": "webhook", "hook_secret": "s3cr3t"})
    assert isinstance(webhook, WebhookTrigger)


def test_trigger_def_kind_desconocido_falla():
    with pytest.raises(ValidationError):
        TriggerDefAdapter.validate_python({"kind": "cron", "rrule": "x"})


def test_accion_def_es_agent_instruction():
    accion = AccionDefAdapter.validate_python({"kind": "agent_instruction", "instruccion": "haz X"})
    assert isinstance(accion, AgentInstructionAccion)
    assert accion.agente is None
    # `AccionDef` es hoy un alias directo (no unión discriminada, ver docstring
    # del módulo): confirma que sigue apuntando al mismo tipo.
    assert AccionDef is AgentInstructionAccion


# ---------------------------------------------------------------------------
# §7.4/§7.7/§8.1 — comercio (guardrail: toda orden nace draft)
# ---------------------------------------------------------------------------


def test_order_kinds_y_statuses_pinned():
    assert ORDER_KINDS == ("payment", "purchase", "trade")
    assert ORDER_STATUSES == ("draft", "confirmed", "executed_paper", "cancelled", "expired")


def test_order_out_nace_draft_por_default():
    order = OrderOut(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        kind="trade",
        descripcion="Comprar 0.01 BTC",
        simbolo="BTC",
        lado="buy",
        cantidad="0.01",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    assert order.status == "draft"
    assert order.confirmed_at is None
    assert order.executed_at is None


def test_order_out_kind_invalido_falla():
    with pytest.raises(ValidationError):
        OrderOut(
            id=uuid4(),
            tenant_id=uuid4(),
            user_id=uuid4(),
            kind="invertir_todo",
            descripcion="x",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )


# ---------------------------------------------------------------------------
# §7.4 — dispositivos / sesión remota
# ---------------------------------------------------------------------------


def test_device_y_remote_session_vocab_pinned():
    assert DEVICE_KINDS == ("companion", "mobile")
    assert DEVICE_STATUSES == ("active", "revoked")
    assert REMOTE_SESSION_STATUSES == ("pending", "active", "ended", "denied")


def test_device_out_valida():
    device = DeviceOut(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        nombre="MacBook de Ana",
        plataforma="macos",
        kind="companion",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    assert device.status == "active"


def test_remote_session_out_nace_pending_por_default():
    session = RemoteSessionOut(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    assert session.status == "pending"
    assert session.kind == "view"
    assert session.frames_count == 0


# ---------------------------------------------------------------------------
# §7.4/§21 — perfil vivo
# ---------------------------------------------------------------------------


def test_live_profile_defaults_vacios():
    profile = LiveProfile()
    assert profile.resumen == ""
    assert profile.version == 1
    assert profile.datos == ProfileData()


def test_live_profile_datos_tiene_las_6_listas_pinned():
    datos = ProfileData(
        gustos=["café"],
        proyectos=["Edecán v2"],
        metas=["lanzar v2"],
        relaciones=["socio: X"],
        empresas=["Acme"],
        habitos=["correr"],
    )
    profile = LiveProfile(resumen="Ana construye Edecán.", datos=datos, version=2)
    dumped = profile.model_dump()
    assert set(dumped["datos"]) == {
        "gustos",
        "proyectos",
        "metas",
        "relaciones",
        "empresas",
        "habitos",
    }
