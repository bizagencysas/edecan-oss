"""Tests de `proactive_scan` (product design): minería proactiva de fondo.

Se prueba la función de escaneo (`scan_proactive`), no el scheduling. El
`proactive_scan` habla SQL parametrizado contra `agent_missions`/`automations`
y usa `edecan_automations.proactive` (aritmética pura), así que se testea con
un doble de sesión en memoria, mismo criterio que `test_persistent_agent_scan.py`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import edecan_worker.handlers.proactive_scan as scan_module


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _Session:
    def __init__(self, missions=(), *, existing_suggestion: bool = False):
        self.missions = list(missions)
        self.existing_suggestion = existing_suggestion
        self.inserted: list[dict] = []
        self.queries: list[tuple[str, dict]] = []
        self.lock_keys: list[str] = []

    async def execute(self, statement, params):
        sql = str(statement)
        self.queries.append((sql, params))
        if "pg_advisory_xact_lock" in sql:
            self.lock_keys.append(params["lock_key"])
            return _Result()
        if "FROM agent_missions" in sql:
            return _Result(self.missions)
        if "FROM automations" in sql and "SELECT 1" in sql:
            if self.existing_suggestion:
                return _Result([{"1": 1}])
            return _Result()
        if "INSERT INTO automations" in sql:
            self.inserted.append(params)
            return _Result()
        return _Result()


def _mision(tenant_id, user_id, objetivo, *, days_ago=1, owner_agent_id=None):
    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "objetivo": objetivo,
        "owner_agent_id": owner_agent_id,
        "created_at": datetime.now(UTC) - timedelta(days=days_ago),
    }


async def test_scan_registra_sugerencia_deshabilitada_para_tarea_repetida():
    tenant_id, user_id = uuid4(), uuid4()
    session = _Session(
        missions=[_mision(tenant_id, user_id, "Publicar resumen semanal")] * 3
    )

    registradas = await scan_module.scan_proactive(session, now=datetime.now(UTC))

    assert len(registradas) == 1
    assert registradas[0]["tenant_id"] == str(tenant_id)
    assert registradas[0]["user_id"] == str(user_id)
    assert registradas[0]["task"] == "Publicar resumen semanal"
    assert registradas[0]["repetitions"] == 3

    assert len(session.inserted) == 1
    insert = session.inserted[0]
    assert json.loads(insert["trigger"]) == {"kind": "suggestion"}
    accion = json.loads(insert["accion"])
    assert accion["kind"] == "agent_instruction"
    assert accion["instruccion"] == "Publicar resumen semanal"


async def test_scan_inserta_fila_con_enabled_false_y_next_run_null_literales():
    tenant_id, user_id = uuid4(), uuid4()
    session = _Session(missions=[_mision(tenant_id, user_id, "Revisar métricas")] * 3)

    await scan_module.scan_proactive(session, now=datetime.now(UTC))

    sql_insert = next(sql for sql, _ in session.queries if "INSERT INTO automations" in sql)
    assert "false" in sql_insert
    assert "NULL" in sql_insert


async def test_scan_bajo_el_umbral_no_registra_nada():
    session = _Session(missions=[_mision(uuid4(), uuid4(), "Tarea repetida")] * 2)

    registradas = await scan_module.scan_proactive(session, now=datetime.now(UTC))

    assert registradas == []
    assert session.inserted == []


async def test_scan_no_duplica_sugerencia_existente():
    tenant_id, user_id = uuid4(), uuid4()
    session = _Session(
        missions=[_mision(tenant_id, user_id, "Publicar resumen semanal")] * 3,
        existing_suggestion=True,
    )

    registradas = await scan_module.scan_proactive(session, now=datetime.now(UTC))

    assert registradas == []
    assert session.inserted == []


async def test_scan_agrupa_por_tenant_usuario_de_forma_independiente():
    tenant_a, tenant_b = uuid4(), uuid4()
    user_a, user_b = uuid4(), uuid4()
    session = _Session(
        missions=(
            [_mision(tenant_a, user_a, "Tarea de A")] * 3
            + [_mision(tenant_b, user_b, "Tarea de B")] * 3
        )
    )

    registradas = await scan_module.scan_proactive(session, now=datetime.now(UTC))

    assert len(registradas) == 2
    assert {r["tenant_id"] for r in registradas} == {str(tenant_a), str(tenant_b)}
    assert len(session.inserted) == 2


async def test_record_suggestion_advisory_lock_antes_del_select():
    """BOTS-22: el dedup atómico serializa SELECT+INSERT con un advisory lock
    transaccional POR TENANT, y el lock se adquiere ANTES del SELECT de dedup
    (dos scans simultáneos del mismo tenant producen UNA fila)."""
    tenant_id, user_id = uuid4(), uuid4()
    session = _Session()

    await scan_module._record_suggestion(session, tenant_id, user_id, {"task": "Tarea X"})

    lock_idx = next(
        i for i, (sql, _) in enumerate(session.queries) if "pg_advisory_xact_lock" in sql
    )
    select_idx = next(
        i for i, (sql, _) in enumerate(session.queries) if "SELECT 1 FROM automations" in sql
    )
    assert lock_idx < select_idx
    assert session.lock_keys == [f"proactive_suggestion:{tenant_id}"]


async def test_record_suggestion_lock_key_es_por_tenant():
    """El lock es POR TENANT (no global): dos tenants distintos usan claves
    distintas y no se bloquean entre sí."""
    tenant_a, tenant_b = uuid4(), uuid4()
    user_id = uuid4()
    session_a, session_b = _Session(), _Session()

    await scan_module._record_suggestion(session_a, tenant_a, user_id, {"task": "Tarea A"})
    await scan_module._record_suggestion(session_b, tenant_b, user_id, {"task": "Tarea A"})

    assert session_a.lock_keys == [f"proactive_suggestion:{tenant_a}"]
    assert session_b.lock_keys == [f"proactive_suggestion:{tenant_b}"]


async def test_record_suggestion_no_duplica_si_ya_existe_bajo_lock():
    """La fila ya existente se detecta DESPUÉS del lock y NO se inserta de
    nuevo (regresión del dedup, ahora serializado)."""
    tenant_id, user_id = uuid4(), uuid4()
    session = _Session(existing_suggestion=True)

    resultado = await scan_module._record_suggestion(
        session, tenant_id, user_id, {"task": "Tarea repetida"}
    )

    assert resultado is False
    assert session.inserted == []
    assert session.lock_keys == [f"proactive_suggestion:{tenant_id}"]


async def test_record_suggestion_dedup_ignora_enabled():
    """AUD-22a: el SELECT de dedup considera sugerencias anteriores por
    nombre/identidad SIN importar `enabled` — una sugerencia ya ACEPTADA
    (enabled=true) bloquea la re-insertión (no reaparece una vez decidida)."""
    tenant_id, user_id = uuid4(), uuid4()
    session = _Session()

    await scan_module._record_suggestion(session, tenant_id, user_id, {"task": "Tarea X"})

    dedup_sql = next(
        sql for sql, _ in session.queries if "SELECT 1 FROM automations" in sql
    )
    assert "enabled" not in dedup_sql
