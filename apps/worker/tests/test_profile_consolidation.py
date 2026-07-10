"""Tests de la fase 3 de `memory_consolidate` — perfil vivo (WP-V2-13).

`test_memory_consolidate.py` cubre las fases 1 (extracción) y 2 (dedup) y
NO se toca aquí (paquete de trabajo separado, ver su docstring). Esos tests
usan el `FakeSession` "placeholder" de `tests/fakes.py` (sin `.execute`) — la
fase 3 SÍ habla con `session` directamente (`sqlalchemy.text`, ver el
docstring de `memory_consolidate.py`, "Fase 3"), así que este archivo trae su
PROPIO `FakeSession`/`FakeResult` locales (mismo patrón que
`apps/api/tests/test_commerce_router.py`) inyectados vía
`make_deps(session_factory=...)`, sin tocar `tests/fakes.py`.

`FakeSession.execute` especial-casa el `SELECT ... FROM connector_accounts`
que hace `Deps.llm_router_for` (WP-V3-02, bring-your-own LLM por tenant,
ARCHITECTURE.md §12.b): usa el MISMO `session_factory` que el resto del job,
pero es una preocupación aparte de la secuencia `user_profiles`/`memory_items`
que este fake modela para la fase 3, así que se responde "sin cuenta
conectada" sin registrarlo en `llamadas` ni consumir de `respuestas` — si
no, correría la numeración de los asserts que sí verifican esa secuencia.

CORRECCIÓN (2026-07-08): `llm_router_for` YA NO cae al `LLMRouter` de
plataforma sin cuenta conectada — lanza `TenantLLMNotConnectedError` (ver
`apps/worker/tests/test_llm_por_tenant.py`). Los tests de "camino feliz" de
este archivo (que sí necesitan que la fase 3 llame al LLM de verdad)
monkeypatchean `deps.llm_router_for` directo para que devuelva
`deps.llm_router` (el fake, sin red) — ver
`_use_platform_router_as_tenant_router` abajo — en vez de intentar simular
una cuenta conectada de verdad, que arriesgaría una llamada de red real
(`_actualizar_perfil_vivo` llama `provider.complete(...)` directo, sin
ninguna capa fakeada de por medio, a diferencia de `run_mission`/
`run_automation`).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import edecan_worker.handlers.memory_consolidate as consolidate_module
from edecan_schemas import JobEnvelope
from fakes import FakeRepo, make_deps


async def _use_platform_router_as_tenant_router(deps, monkeypatch) -> None:
    """Ver docstring del módulo, sección "CORRECCIÓN". Monkeypatchea
    `deps.llm_router_for` para que devuelva directo `deps.llm_router` (el
    fake en memoria de cada test), evitando tanto `TenantLLMNotConnectedError`
    como el riesgo de una llamada de red real."""

    async def _fake_llm_router_for(tenant_id):
        return deps.llm_router

    monkeypatch.setattr(deps, "llm_router_for", _fake_llm_router_for)

# ---------------------------------------------------------------------------
# `FakeSession`/`FakeResult` locales — ver docstring del módulo.
# ---------------------------------------------------------------------------


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> FakeResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)


@dataclass
class FakeSession:
    respuestas: list[list[dict[str, Any]]] = field(default_factory=list)
    llamadas: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> FakeResult:
        sql = str(stmt)
        if "connector_accounts" in sql:
            # Ver docstring del módulo: `Deps.llm_router_for` consulta
            # `connector_accounts` por su cuenta: "sin cuenta conectada" acá
            # (lista vacía) para que caiga al `LLMRouter` de plataforma, sin
            # tocar `llamadas`/`respuestas` (le pertenecen a la secuencia SQL
            # de la fase 3 que este fake modela).
            return FakeResult([])
        self.llamadas.append((sql, dict(params or {})))
        filas = self.respuestas.pop(0) if self.respuestas else []
        return FakeResult(filas)


def _session_factory_de(session: FakeSession):
    @asynccontextmanager
    async def _factory(tenant_id: uuid.UUID | None) -> AsyncIterator[FakeSession]:
        yield session

    return _factory


def _env(*, tenant_id: uuid.UUID, user_id: uuid.UUID) -> JobEnvelope:
    return JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="memory_consolidate",
        payload={"user_id": str(user_id)},
    )


def _agregar_memoria(
    fake_repo: FakeRepo, *, tenant_id: uuid.UUID, user_id: uuid.UUID, kind: str, content: str
) -> None:
    item_id = uuid.uuid4()
    fake_repo.memory_items[item_id] = {
        "id": item_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "kind": kind,
        "content": content,
        "importance": 0.5,
        "source": "user",
        "embedding": None,
        "created_at": datetime.now(UTC),
    }


def _fila_perfil(*, resumen: str, datos: dict[str, list[str]], version: int = 1) -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "resumen": resumen,
        "datos": dict(datos),
        "version": version,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }


# ---------------------------------------------------------------------------
# Sin memorias / memoria desactivada -> no-op (ni LLM ni SQL de user_profiles)
# ---------------------------------------------------------------------------


async def test_sin_memorias_no_llama_al_llm_ni_toca_user_profiles(monkeypatch) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(consolidate_module, "SqlRepo", lambda session: fake_repo)
    fake_session = FakeSession()

    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    deps = make_deps(session_factory=_session_factory_de(fake_session))
    await _use_platform_router_as_tenant_router(deps, monkeypatch)

    await consolidate_module.handle(_env(tenant_id=tenant_id, user_id=user_id), deps)

    assert deps.llm_router.provider.requests == []
    assert fake_session.llamadas == []


async def test_memoria_desactivada_no_construye_perfil(monkeypatch) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(consolidate_module, "SqlRepo", lambda session: fake_repo)
    fake_session = FakeSession()

    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    fake_repo.personas[(tenant_id, user_id)] = {"memoria_activada": False}
    _agregar_memoria(
        fake_repo, tenant_id=tenant_id, user_id=user_id, kind="fact", content="Vive en Bogotá."
    )

    deps = make_deps(session_factory=_session_factory_de(fake_session))
    await _use_platform_router_as_tenant_router(deps, monkeypatch)
    await consolidate_module.handle(_env(tenant_id=tenant_id, user_id=user_id), deps)

    assert deps.llm_router.provider.requests == []
    assert fake_session.llamadas == []


# ---------------------------------------------------------------------------
# Primera vez (sin fila previa): construye version=1, upsert + espejo
# ---------------------------------------------------------------------------


async def test_primera_vez_crea_perfil_version_1_y_espeja_el_resumen(monkeypatch) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(consolidate_module, "SqlRepo", lambda session: fake_repo)
    fake_session = FakeSession()
    fake_session.respuestas = [
        [],  # SELECT user_profiles -> no había fila previa
        [],  # INSERT ... ON CONFLICT (respuesta ignorada)
        [],  # DELETE espejo anterior (respuesta ignorada)
    ]

    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    fake_repo.tenants[tenant_id] = {"id": tenant_id, "plan_key": "hosted_pro"}
    _agregar_memoria(
        fake_repo,
        tenant_id=tenant_id,
        user_id=user_id,
        kind="preference",
        content="Prefiere respuestas breves.",
    )

    deps = make_deps(session_factory=_session_factory_de(fake_session))
    await _use_platform_router_as_tenant_router(deps, monkeypatch)
    deps.llm_router.provider.reply = json.dumps(
        {
            "resumen": "Prefieres respuestas breves y directas.",
            "datos": {"habitos": ["Prefiere respuestas breves."]},
        }
    )

    await consolidate_module.handle(_env(tenant_id=tenant_id, user_id=user_id), deps)

    # SELECT, INSERT/UPSERT, DELETE espejo -> 3 llamadas a `session.execute`.
    assert len(fake_session.llamadas) == 3
    sql_select, params_select = fake_session.llamadas[0]
    assert "SELECT * FROM user_profiles" in sql_select
    assert params_select["tenant_id"] == tenant_id
    assert params_select["user_id"] == user_id

    sql_upsert, params_upsert = fake_session.llamadas[1]
    assert "INSERT INTO user_profiles" in sql_upsert
    assert "ON CONFLICT (tenant_id, user_id) DO UPDATE" in sql_upsert
    assert params_upsert["version"] == 1
    assert params_upsert["resumen"] == "Prefieres respuestas breves y directas."
    datos_guardados = json.loads(params_upsert["datos"])
    assert datos_guardados["habitos"] == ["Prefiere respuestas breves."]

    sql_delete, params_delete = fake_session.llamadas[2]
    assert "DELETE FROM memory_items" in sql_delete
    assert params_delete["source"] == "perfil_vivo"

    # El espejo se inserta vía `repo.add_memory_item` (reutilizado, ver
    # docstring del módulo) -> aparece en `fake_repo.memory_items`.
    espejos = [row for row in fake_repo.memory_items.values() if row["source"] == "perfil_vivo"]
    assert len(espejos) == 1
    assert espejos[0]["content"] == "Prefieres respuestas breves y directas."
    assert espejos[0]["kind"] == "fact"
    assert espejos[0]["importance"] == 1.0
    assert espejos[0]["embedding"] is not None

    # El uso del LLM de la fase 3 quedó registrado (además del de la fase 1,
    # que en este test no corrió porque no hay mensajes recientes).
    metas_perfil = [
        evt["meta"] for evt in fake_repo.usage_events if evt["meta"].get("fase") == "perfil_vivo"
    ]
    assert len(metas_perfil) == 1
    assert metas_perfil[0]["alias"] == "rapido"


# ---------------------------------------------------------------------------
# Perfil existente: version++, merge conservador se respeta end-to-end
# ---------------------------------------------------------------------------


async def test_con_perfil_previo_incrementa_version_y_conserva_datos_previos(monkeypatch) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(consolidate_module, "SqlRepo", lambda session: fake_repo)
    fake_session = FakeSession()
    fila_previa = _fila_perfil(
        resumen="Prefieres respuestas breves.",
        datos={
            "gustos": ["Le gusta el café"],
            "proyectos": [],
            "metas": [],
            "relaciones": [],
            "empresas": [],
            "habitos": [],
        },
        version=3,
    )
    fake_session.respuestas = [[fila_previa], [], []]

    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    _agregar_memoria(
        fake_repo,
        tenant_id=tenant_id,
        user_id=user_id,
        kind="fact",
        content="Su empresa se llama Acme.",
    )

    deps = make_deps(session_factory=_session_factory_de(fake_session))
    await _use_platform_router_as_tenant_router(deps, monkeypatch)
    deps.llm_router.provider.reply = json.dumps(
        {
            "resumen": "Prefieres respuestas breves.",
            "datos": {"empresas": ["Su empresa se llama Acme."]},
        }
    )

    await consolidate_module.handle(_env(tenant_id=tenant_id, user_id=user_id), deps)

    _, params_upsert = fake_session.llamadas[1]
    assert params_upsert["version"] == 4  # 3 + 1
    datos_guardados = json.loads(params_upsert["datos"])
    # Conserva "gustos" (previo) Y agrega "empresas" (nuevo) — merge conservador.
    assert datos_guardados["gustos"] == ["Le gusta el café"]
    assert datos_guardados["empresas"] == ["Su empresa se llama Acme."]


async def test_datos_previos_llegan_como_texto_json_crudo_se_decodifican(monkeypatch) -> None:
    """`user_profiles.datos` puede volver como texto JSON crudo según el
    driver (`_from_jsonb`, ver docstring del módulo)."""
    fake_repo = FakeRepo()
    monkeypatch.setattr(consolidate_module, "SqlRepo", lambda session: fake_repo)
    fake_session = FakeSession()
    fila_previa = _fila_perfil(resumen="", datos={"gustos": ["Le gusta el café"]}, version=1)
    fila_previa["datos"] = json.dumps(fila_previa["datos"])  # texto crudo, no dict
    fake_session.respuestas = [[fila_previa], [], []]

    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    _agregar_memoria(fake_repo, tenant_id=tenant_id, user_id=user_id, kind="fact", content="algo")

    deps = make_deps(session_factory=_session_factory_de(fake_session))
    await _use_platform_router_as_tenant_router(deps, monkeypatch)
    deps.llm_router.provider.reply = json.dumps({"resumen": "", "datos": {}})

    await consolidate_module.handle(_env(tenant_id=tenant_id, user_id=user_id), deps)

    _, params_upsert = fake_session.llamadas[1]
    datos_guardados = json.loads(params_upsert["datos"])
    assert datos_guardados["gustos"] == ["Le gusta el café"]


# ---------------------------------------------------------------------------
# Resumen vacío: se borra el espejo previo pero no se inserta uno nuevo
# ---------------------------------------------------------------------------


async def test_resumen_vacio_borra_el_espejo_previo_sin_insertar_uno_nuevo(monkeypatch) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(consolidate_module, "SqlRepo", lambda session: fake_repo)
    fake_session = FakeSession()
    fake_session.respuestas = [[], [], []]

    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    espejo_viejo_id = uuid.uuid4()
    fake_repo.memory_items[espejo_viejo_id] = {
        "id": espejo_viejo_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "kind": "fact",
        "content": "resumen viejo",
        "importance": 1.0,
        "source": "perfil_vivo",
        "embedding": [0.1, 0.2],
        "created_at": datetime.now(UTC),
    }
    _agregar_memoria(fake_repo, tenant_id=tenant_id, user_id=user_id, kind="fact", content="algo")

    deps = make_deps(session_factory=_session_factory_de(fake_session))
    await _use_platform_router_as_tenant_router(deps, monkeypatch)
    deps.llm_router.provider.reply = json.dumps({"resumen": "", "datos": {}})

    await consolidate_module.handle(_env(tenant_id=tenant_id, user_id=user_id), deps)

    # El DELETE de `memory_items` (fake) es responsabilidad de `repo`, no de
    # `session` en este flujo (el espejo viejo lo maneja el DELETE crudo por
    # `source`, ver `_borrar_espejo_perfil`) — como `FakeRepo` no implementa
    # ese DELETE (va por `session.execute`, no por `repo`), lo que sí podemos
    # afirmar es que NO se agregó un segundo espejo nuevo.
    espejos = [row for row in fake_repo.memory_items.values() if row["source"] == "perfil_vivo"]
    assert len(espejos) == 1  # el viejo sigue en el FakeRepo (el DELETE fue vía `session`)
    sql_delete, params_delete = fake_session.llamadas[2]
    assert "DELETE FROM memory_items" in sql_delete
    assert params_delete["source"] == "perfil_vivo"


# ---------------------------------------------------------------------------
# JSON inválido del LLM -> no tumba el job, no altera `user_profiles`
# ---------------------------------------------------------------------------


async def test_json_invalido_del_llm_no_tumba_el_job(monkeypatch) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(consolidate_module, "SqlRepo", lambda session: fake_repo)
    fake_session = FakeSession()
    fila_previa = _fila_perfil(resumen="Perfil intacto.", datos={"gustos": ["x"]}, version=2)
    fake_session.respuestas = [[fila_previa], [], []]

    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    _agregar_memoria(fake_repo, tenant_id=tenant_id, user_id=user_id, kind="fact", content="algo")

    deps = make_deps(session_factory=_session_factory_de(fake_session))
    await _use_platform_router_as_tenant_router(deps, monkeypatch)
    deps.llm_router.provider.reply = "esto no es JSON en absoluto"

    await consolidate_module.handle(_env(tenant_id=tenant_id, user_id=user_id), deps)  # no lanza

    # `build_profile` cae al perfil previo tal cual: igual se hace upsert
    # (version++) porque `memory_consolidate` no distingue "cambió" de "no
    # cambió" — pero el CONTENIDO persistido es el previo, sin inventar nada.
    _, params_upsert = fake_session.llamadas[1]
    assert params_upsert["resumen"] == "Perfil intacto."
    datos_guardados = json.loads(params_upsert["datos"])
    assert datos_guardados["gustos"] == ["x"]


async def test_fallo_de_sql_en_fase_3_no_tumba_el_job_ni_afecta_fases_previas(monkeypatch) -> None:
    """Sin `session_factory` a medida (el default de `tests/fakes.py` da un
    `FakeSession` sin `.execute`), la fase 3 revienta al primer `SELECT` —
    pero queda contenida por el `try/except` amplio, y las fases 1/2 (que sí
    corrieron antes, con `repo`) no se ven afectadas."""
    fake_repo = FakeRepo()
    monkeypatch.setattr(consolidate_module, "SqlRepo", lambda session: fake_repo)

    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    conversation_id = uuid.uuid4()
    fake_repo.conversations[conversation_id] = {
        "id": conversation_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "title": "",
        "channel": "web",
    }
    fake_repo.messages.append(
        {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "conversation_id": conversation_id,
            "role": "user",
            "content": {"text": "Prefiero que me hables de tú."},
            "tool_calls": None,
            "tokens_in": 0,
            "tokens_out": 0,
        }
    )

    deps = make_deps()  # session_factory por defecto: FakeSession() sin .execute
    # Fase 1 (extracción) también resuelve `llm_router_for` ahora, y sin esto
    # fallaría en el mismo punto que la fase 3 (el `session.execute()` de
    # `_resolve_tenant_llm_router`) -- este test necesita que la fase 1 SÍ
    # tenga éxito (para poder comprobar que la fase 3 fallando no la afecta),
    # así que se monkeypatchea igual que el resto del archivo. La fase 3
    # sigue fallando igual que antes, solo que un paso más adelante: ya no en
    # la resolución del LLM (bypaseada acá) sino en su propio
    # `session.execute()` real contra `user_profiles` (`_obtener_perfil_previo`
    # /`_upsert_perfil_vivo`), que sigue sin existir en el `FakeSession` base.
    await _use_platform_router_as_tenant_router(deps, monkeypatch)
    deps.llm_router.provider.reply = (
        '[{"kind": "preference", "content": "Prefiere que le hablen de tú.", "importance": 0.6}]'
    )

    await consolidate_module.handle(_env(tenant_id=tenant_id, user_id=user_id), deps)  # no lanza

    # La fase 1 sí insertó su memoria (no depende de `session` directamente).
    nuevos = [
        row
        for row in fake_repo.memory_items.values()
        if row["content"] == "Prefiere que le hablen de tú."
    ]
    assert len(nuevos) == 1
    # Ningún espejo de perfil se creó (la fase 3 falló antes de llegar ahí).
    assert all(row["source"] != "perfil_vivo" for row in fake_repo.memory_items.values())
