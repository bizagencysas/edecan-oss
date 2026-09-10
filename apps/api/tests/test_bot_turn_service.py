"""Tests del chat de bots y su ciclo de vida (bug + delete + ack de identidad).

Cubre la familia de fallos que dejó los chats de bot mudos («Se perdió la
conexión con Edecán» con HTTP 200 y stream vacío):

1. `persist_chat_message` NO debe sombrear `sqlalchemy.text` con un parámetro
   `text` — el TypeError exacto que mató cada turno de bot antes del primer
   evento SSE.
2. `DELETE /v1/agents/workers/{id}` borra conversación + mensajes + worker.
3. Un PATCH que cambia identidad agenda el ack de identidad (turno real del
   bot), con el resumen de qué cambió.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import auth_headers
from httpx import ASGITransport, AsyncClient

from edecan_api.bot_turn_service import (
    clamp_message_limit,
    list_normalized_messages,
    persist_chat_message,
)


class _Filas:
    """Doble mínima del resultado de SQLAlchemy: `.mappings().first()`."""

    def __init__(self, fila: dict[str, Any] | None) -> None:
        self._fila = fila

    def mappings(self) -> _Filas:
        return self

    def all(self) -> list[dict[str, Any]]:
        if self._fila is None:
            return []
        return [self._fila]

    def first(self) -> dict[str, Any] | None:
        return self._fila


class _FakeSesion:
    """Sesión que graba cada statement; la primera consulta devuelve `fila`."""

    def __init__(self, fila: dict[str, Any] | None = None) -> None:
        self.statements: list[tuple[str, dict | None]] = []
        self._fila = fila

    async def execute(self, clause: Any, params: dict | None = None) -> Any:
        self.statements.append((str(clause), params))
        return _Filas(self._fila)

    @property
    def sqls(self) -> list[str]:
        return [sql for sql, _ in self.statements]


async def test_persist_chat_message_no_sombrea_sqlalchemy_text() -> None:
    """Regresión del bug del chat de bots: el parámetro se llama `texto` y el
    insert ejecuta. Con el shadowing (`text: str`) esto reventaba con
    `TypeError: 'str' object is not callable` ANTES de tocar la base."""
    sesion = _FakeSesion()
    await persist_chat_message(
        sesion,
        tenant_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        role="user",
        texto="Hola BotAlpha",
        sender_id="user",
        sender_name="Tú",
    )
    assert "INSERT INTO messages" in sesion.sqls[0]


def _fila_worker(worker_id: uuid.UUID, conversation_id: uuid.UUID | None) -> dict[str, Any]:
    return {
        "id": str(worker_id),
        "tenant_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "name": "BotAlpha",
        "display_name": "BotAlpha",
        "conversation_id": str(conversation_id) if conversation_id else None,
        "enabled": True,
        "status": "idle",
    }


async def test_delete_worker_borra_mensajes_conversacion_y_bot(
    app, fake_repo: Any, fake_redis: Any, test_settings: Any
) -> None:
    sesion = _FakeSesion(fila=_fila_worker(worker_id := uuid.uuid4(), conversation_id=uuid.uuid4()))
    import edecan_api.deps as edecan_deps

    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: sesion
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())

    resp = await client.delete(f"/v1/agents/workers/{worker_id}", headers=headers)
    assert resp.status_code == 204
    borras = [sql for sql in sesion.sqls if "DELETE FROM" in sql]
    assert len(borras) == 3
    assert "DELETE FROM messages" in borras[0]
    assert "DELETE FROM conversations" in borras[1]
    assert "DELETE FROM persistent_agents" in borras[2]


async def test_delete_worker_404_si_no_existe(app) -> None:
    sesion = _FakeSesion(fila=None)
    import edecan_api.deps as edecan_deps

    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: sesion
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())

    resp = await client.delete(f"/v1/agents/workers/{uuid.uuid4()}", headers=headers)
    assert resp.status_code == 404
    assert not any("DELETE FROM" in sql for sql in sesion.sqls)


async def test_clear_worker_messages_serializa_con_turno_en_curso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F4: `/clear` toma el lock de la conversación alrededor del borrado. Un
    turno en curso (que ya sostiene el lock) bloquea el DELETE; al liberar el
    lock, el borrado corre (no concurrente con el arranque de un turno nuevo)."""
    import asyncio

    import edecan_api.bot_turn_service as bts
    import edecan_api.routers.persistent_agents as pa

    tenant_id = uuid.uuid4()
    conversation_id = uuid.uuid4()

    async def _load_worker(session: Any, user: Any, worker_id: Any) -> dict[str, Any]:
        return {"id": str(worker_id), "conversation_id": str(conversation_id)}

    ensure_reached = asyncio.Event()

    async def _ensure(session: Any, user: Any, worker: Any) -> uuid.UUID:
        ensure_reached.set()
        return conversation_id

    async def _incr(redis_client: Any, *, tenant_id: Any, conversation_id: Any) -> int:
        return 1

    monkeypatch.setattr(bts, "load_worker", _load_worker)
    monkeypatch.setattr(bts, "ensure_worker_conversation", _ensure)
    monkeypatch.setattr(bts, "increment_conversation_epoch", _incr)

    deleted: list[dict[str, Any]] = []

    class _Sesion:
        async def execute(self, clause: Any, params: dict | None = None) -> Any:
            sql = str(clause)
            if "DELETE FROM messages" in sql:
                deleted.append(params or {})
            return SimpleNamespace(rowcount=1)

    sesion = _Sesion()

    lock = pa._turn_lock_for(tenant_id, conversation_id)
    await lock.acquire()

    tarea = asyncio.create_task(
        pa.clear_worker_messages(
            uuid.uuid4(),
            user=SimpleNamespace(tenant_id=tenant_id),
            session=sesion,
            redis_client=SimpleNamespace(),
        )
    )
    await asyncio.wait_for(ensure_reached.wait(), timeout=2)
    await asyncio.sleep(0.01)  # deja que intente tomar el lock ocupado
    assert deleted == []  # bloqueada por el turno en curso

    lock.release()
    await asyncio.wait_for(tarea, timeout=2)
    assert len(deleted) == 1


async def test_delete_worker_serializa_con_turno_en_curso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F4: `DELETE` también toma el lock de la conversación alrededor del borrado
    de mensajes/conversación (mismo mecanismo que `/clear`)."""
    import asyncio

    import edecan_api.bot_turn_service as bts
    import edecan_api.routers.persistent_agents as pa

    tenant_id = uuid.uuid4()
    conversation_id = uuid.uuid4()

    async def _load_worker(session: Any, user: Any, worker_id: Any) -> dict[str, Any]:
        return {"id": str(worker_id), "conversation_id": conversation_id}

    async def _incr(redis_client: Any, *, tenant_id: Any, conversation_id: Any) -> int:
        return 1

    monkeypatch.setattr(bts, "load_worker", _load_worker)
    monkeypatch.setattr(bts, "increment_conversation_epoch", _incr)

    deleted: list[dict[str, Any]] = []

    class _Sesion:
        async def execute(self, clause: Any, params: dict | None = None) -> Any:
            sql = str(clause)
            if "DELETE FROM messages" in sql:
                deleted.append(params or {})
            return SimpleNamespace(rowcount=1)

    sesion = _Sesion()

    lock = pa._turn_lock_for(tenant_id, conversation_id)
    await lock.acquire()

    tarea = asyncio.create_task(
        pa.delete_worker(
            uuid.uuid4(),
            user=SimpleNamespace(tenant_id=tenant_id, user_id=uuid.uuid4()),
            session=sesion,
            redis_client=SimpleNamespace(),
        )
    )
    await asyncio.sleep(0.01)
    await asyncio.sleep(0.01)
    assert deleted == []

    lock.release()
    await asyncio.wait_for(tarea, timeout=2)
    assert len(deleted) == 1


async def test_patch_identidad_agenda_ack_con_resumen(
    app, fake_repo: Any, fake_redis: Any, test_settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker_id = uuid.uuid4()
    sesion = _FakeSesion(fila=_fila_worker(worker_id, uuid.uuid4()))
    import edecan_api.deps as edecan_deps

    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: sesion
    acks: list[dict[str, Any]] = []

    async def _ack_falso(request: Any, **kwargs: Any) -> None:
        acks.append(kwargs)

    monkeypatch.setattr("edecan_api.bot_turn_service.ack_cambio_identidad", _ack_falso)
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())

    resp = await client.patch(
        f"/v1/agents/workers/{worker_id}",
        headers=headers,
        json={"name": "BotAlpha Pro", "purpose": "Experto en UI y diseño de producto"},
    )
    assert resp.status_code == 200
    update_sql = next(sql for sql in sesion.sqls if "UPDATE persistent_agents" in sql)
    assert "name = :name" in update_sql
    assert "purpose = :purpose" in update_sql
    assert len(acks) == 1
    resumen = acks[0]["resumen"]
    assert "BotAlpha Pro" in resumen
    assert "Experto en UI" in resumen


async def test_patch_sin_cambio_de_identidad_no_agenda_ack(
    app, fake_repo: Any, fake_redis: Any, test_settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker_id = uuid.uuid4()
    fila = _fila_worker(worker_id, uuid.uuid4())
    fila["name"] = "BotAlpha"
    sesion = _FakeSesion(fila=fila)
    import edecan_api.deps as edecan_deps

    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: sesion
    acks: list[dict[str, Any]] = []

    async def _ack_falso(request: Any, **kwargs: Any) -> None:
        acks.append(kwargs)

    monkeypatch.setattr("edecan_api.bot_turn_service.ack_cambio_identidad", _ack_falso)
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())

    resp = await client.patch(
        f"/v1/agents/workers/{worker_id}",
        headers=headers,
        json={"status": "paused"},
    )
    assert resp.status_code == 200
    assert acks == []


async def test_stream_worker_turn_con_router_no_rompe_y_no_compacta_sin_hilo_viejo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El turno del bot cablea `resumen_llm_hilo_anterior` (fail-open), pero con
    los límites actuales del bot (`recent == max == BOT_CONTEXT_MAX_MESSAGES`)
    nunca hay mensajes "viejos": el historial viaja verbatim y NO se paga la
    llamada al modelo barato — el contrato del bot no cambia."""
    from types import SimpleNamespace

    import edecan_api.bot_turn_service as servicio
    import edecan_api.deps as deps
    import edecan_api.routers.conversations as conversaciones
    import edecan_api.routers.perfil as perfil
    from edecan_api.deps import CurrentUser, TenantCtx

    capturado: list[list] = []

    class _Agent:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run_turn(self, **_kwargs):
            capturado.append(_kwargs.get("history"))
            yield {"type": "text_delta", "text": "ok"}
            yield {"type": "done", "usage": {}}

    class _Router:
        def __init__(self):
            self.llamadas: list[str] = []

        async def complete(self, alias, flags, req):
            self.llamadas.append(alias)
            return SimpleNamespace(text="RESUMEN BOT: el bot dejó pendiente el deploy.")

    class _RepoFalso:
        async def list_messages(self, **_kwargs):
            return [
                {
                    "role": "user" if i % 2 == 0 else "assistant",
                    "content": {"text": f"Bot turno {i}: " + ("deploy pendiente " * 40)},
                }
                for i in range(6)
            ]

    async def _persist_vacio(*_args, **_kwargs):
        return None

    async def _stream_vacio(**kwargs):
        # Consume los eventos del agente (así corre el cuerpo de `run_turn`,
        # que captura el history) sin emitir chunks: el test no valida el SSE.
        async for _evento in kwargs.get("events", ()):
            pass
        if False:  # pragma: no cover - lo convierte en async generator
            yield None

    async def _cargar_session_none(*_args, **_kwargs):
        return None

    async def _perfil_vacio(*_args, **_kwargs):
        return ""

    async def _get_repo(_session):
        return _RepoFalso()

    async def _get_vault(_session, _settings):
        return object()

    async def _get_llm_router(_request):
        return router

    router = _Router()
    monkeypatch.setattr(servicio, "persist_chat_message", _persist_vacio)
    monkeypatch.setattr(
        servicio,
        "persona_from_worker",
        lambda _worker: SimpleNamespace(instrucciones=""),
    )
    monkeypatch.setattr(servicio, "companion_para", lambda _tenant_id: None)

    async def _skills_vacio(*_a, **_k):
        return ""

    async def _extras_vacio(*_a, **_k):
        return []

    monkeypatch.setattr(servicio, "build_skills_context", _skills_vacio)
    monkeypatch.setattr(servicio, "_extra_bot_turn_tools", _extras_vacio)
    monkeypatch.setattr(servicio, "build_worker_registry", lambda *_a, **_k: object())
    monkeypatch.setattr(servicio, "load_unified_session", _cargar_session_none)
    monkeypatch.setattr(deps, "get_repo", _get_repo)
    monkeypatch.setattr(deps, "get_vault", _get_vault)
    monkeypatch.setattr(deps, "get_llm_router", _get_llm_router)
    monkeypatch.setattr(deps, "get_redis", lambda _settings: object())
    monkeypatch.setattr(conversaciones, "_agent_for_request", lambda *_a, **_k: _Agent())
    monkeypatch.setattr(conversaciones, "_build_ctx", lambda **_k: SimpleNamespace(extras={}))
    monkeypatch.setattr(conversaciones, "_tools_con_pregunta_pendiente", lambda _rows: [])
    monkeypatch.setattr(
        conversaciones,
        "_unified_session_for",
        lambda **_k: SimpleNamespace(user_id=None, visual_memory=None, touch=lambda **t: None),
    )
    monkeypatch.setattr(conversaciones, "_stream_agent_events", _stream_vacio)
    monkeypatch.setattr(conversaciones, "get_tool_registry", lambda _request: object())
    monkeypatch.setattr(perfil, "profile_context_for", _perfil_vacio)

    tenant_id = uuid.uuid4()
    user = CurrentUser(
        user_id=uuid.uuid4(),
        tenant=TenantCtx(tenant_id=tenant_id, plan_key="hosted_basic", flags={}),
    )
    worker = {
        "id": str(uuid.uuid4()),
        "tenant_id": str(tenant_id),
        "name": "BotAlpha",
        "display_name": "BotAlpha",
        "conversation_id": str(uuid.uuid4()),
    }
    settings = SimpleNamespace(
        BOT_CONTEXT_MAX_MESSAGES=50,
        BOT_CONTEXT_MAX_CHARS=2_000,
        EDECAN_LOCAL_MODE=False,
    )

    async for _chunk in servicio.stream_worker_turn(
        request=SimpleNamespace(),
        session=object(),
        user=user,
        settings=settings,
        worker=worker,
        conversation_id=uuid.uuid4(),
        user_text="hola",
    ):
        pass

    history = capturado[-1]
    assert len(history) == 6
    assert history[0].role == "user"
    assert "Bot turno 0:" in history[0].content
    assert router.llamadas == []


class _RepoStreamFalso:
    """Repo que captura add_message para verificar el split."""

    def __init__(self) -> None:
        self.mensajes: list[dict[str, Any]] = []

    async def add_message(self, **kwargs: Any) -> None:
        self.mensajes.append(kwargs)

    async def add_usage_event(self, **kwargs: Any) -> None:
        pass


async def _eventos_split() -> Any:
    yield {"type": "text_delta", "text": "Lo compruebo."}
    yield {"type": "tool_start", "name": "leer_archivo", "args": {}}
    yield {"type": "tool_end", "name": "leer_archivo", "result_preview": "…"}
    yield {"type": "text_delta", "text": "No pude confirmarlo."}
    yield {
        "type": "done",
        "usage": {"input_tokens": 5, "output_tokens": 4},
        "attribution": {},
    }


async def test_stream_agent_events_split_messages_persiste_burbuja_por_mensaje() -> None:
    """Con split_messages, cada mensaje del asistente (separado por tools) se
    persiste como fila propia y el SSE emite message_start/message_end."""

    from edecan_api.routers.conversations import _stream_agent_events

    repo = _RepoStreamFalso()
    chunks: list[tuple[str, dict[str, Any]]] = []
    async for sse in _stream_agent_events(
        events=_eventos_split(),
        repo=repo,  # type: ignore[arg-type]
        tenant_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        settings=SimpleNamespace(),  # type: ignore[arg-type]
        redis_client=object(),  # type: ignore[arg-type]
        split_messages=True,
    ):
        import json as _json

        lineas = sse.strip().split("\n")
        nombre = lineas[0].removeprefix("event: ")
        chunks.append((nombre, _json.loads(lineas[1].removeprefix("data: "))))

    nombres = [n for n, _ in chunks]
    assert nombres.count("message_start") == 2
    assert nombres.count("message_end") == 2
    start_ids = [d["message_id"] for n, d in chunks if n == "message_start"]
    end_ids = [d["message_id"] for n, d in chunks if n == "message_end"]
    assert start_ids == end_ids and len(set(start_ids)) == 2

    textos = [m["content"]["text"] for m in repo.mensajes]
    assert textos == ["Lo compruebo.", "No pude confirmarlo."]
    # La tool se atribuye al mensaje que la pidió (el primero).
    assert len(repo.mensajes[0]["tool_calls"]) == 2
    assert repo.mensajes[1]["tool_calls"] is None


async def test_build_worker_registry_quita_delegar_al_ide_sin_capability(monkeypatch) -> None:
    """Sin `LOCAL_DESKTOP_CAPABILITY` (VPS), `delegar_al_ide` NO se ofrece al
    bot ni con companion conectado — la tool no puede correr ahí y su fallo
    mataba el turno. Con la capability presente, sí se ofrece."""

    import edecan_core.bot_registry as reg_mod
    from edecan_core.bot_registry import build_worker_registry
    from edecan_core.tools.base import Tool, ToolContext, ToolResult
    from edecan_core.tools.registry import ToolRegistry
    from edecan_toolkit.avances import AvisarAvanceTool
    from edecan_toolkit.ide_delegacion import DelegarAlIDETool

    class _ToolFalsa(Tool):
        name = "leer_archivo"
        description = "lee"
        input_schema = {"type": "object", "properties": {}}

        async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
            return ToolResult(content="")

    def _registro() -> ToolRegistry:
        full = ToolRegistry()
        full.register(DelegarAlIDETool())
        full.register(AvisarAvanceTool())
        full.register(_ToolFalsa())
        return full

    worker = {"tenant_id": uuid.uuid4(), "tools": ["leer_archivo"]}
    monkeypatch.delenv("LOCAL_DESKTOP_CAPABILITY", raising=False)

    # Sin companion y sin capability: la tool se filtra (no hay vía al IDE).
    monkeypatch.setattr(reg_mod, "companion_para", lambda _t: None)
    sin_nada = build_worker_registry(_registro(), worker)
    nombres_sin = [t.name for t in sin_nada.all()]
    assert "leer_archivo" in nombres_sin
    assert "avisar_avance" in nombres_sin
    assert "delegar_al_ide" not in nombres_sin

    # CON companion (VPS→Mac por el bridge): la tool se ofrece — el IDE lo
    # ejecuta el companion, sin necesidad de la app de escritorio.
    monkeypatch.setattr(reg_mod, "companion_para", lambda _t: object())
    con_companion = build_worker_registry(_registro(), worker)
    assert "delegar_al_ide" in [t.name for t in con_companion.all()]

    monkeypatch.setenv("LOCAL_DESKTOP_CAPABILITY", "abc123")
    con_cap = build_worker_registry(_registro(), worker)
    assert "delegar_al_ide" in [t.name for t in con_cap.all()]


async def test_build_worker_registry_excluye_delegar_mision_por_defecto(monkeypatch) -> None:
    """Chat 1:1 Grok Bot: `delegar_mision`/`encargar_a_equipo` solo si el worker
    las declara en `tools` — no son el default con companion."""

    import edecan_core.bot_registry as reg_mod
    from edecan_core.bot_registry import build_worker_registry
    from edecan_core.tools.base import Tool, ToolContext, ToolResult
    from edecan_core.tools.registry import ToolRegistry
    from edecan_toolkit.avances import AvisarAvanceTool

    class _MisionTool(Tool):
        name = "delegar_mision"
        description = "mision"
        input_schema = {"type": "object", "properties": {}}

        async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
            return ToolResult(content="")

    class _EquipoTool(Tool):
        name = "encargar_a_equipo"
        description = "equipo"
        input_schema = {"type": "object", "properties": {}}

        async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
            return ToolResult(content="")

    def _registro() -> ToolRegistry:
        full = ToolRegistry()
        full.register(_MisionTool())
        full.register(_EquipoTool())
        full.register(AvisarAvanceTool())
        return full

    worker = {"tenant_id": uuid.uuid4(), "tools": []}
    monkeypatch.setattr(reg_mod, "companion_para", lambda _t: object())
    registry = build_worker_registry(_registro(), worker)
    nombres = [t.name for t in registry.all()]
    assert "avisar_avance" in nombres
    assert "delegar_mision" not in nombres
    assert "encargar_a_equipo" not in nombres

    worker_explicito = {
        "tenant_id": worker["tenant_id"],
        "tools": ["delegar_mision"],
    }
    registry2 = build_worker_registry(_registro(), worker_explicito)
    assert "delegar_mision" in [t.name for t in registry2.all()]


async def test_build_worker_registry_incluye_publicar_social_sin_companion(monkeypatch) -> None:
    """Deploy remoto: community manager incluye `publicar_social` (dangerous)
    porque la confirmación es durable en chat — no requiere Mac."""

    import edecan_core.bot_registry as reg
    from edecan_core.bot_registry import build_worker_registry
    from edecan_core.tools.base import Tool, ToolContext, ToolResult
    from edecan_core.tools.registry import ToolRegistry
    from edecan_toolkit.avances import AvisarAvanceTool
    from edecan_toolkit.contenido import PublicarSocialTool

    class _ToolSegura(Tool):
        name = "generar_contenido"
        description = "draft"
        input_schema = {"type": "object", "properties": {}}

        async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
            return ToolResult(content="")

    def _registro() -> ToolRegistry:
        full = ToolRegistry()
        full.register(AvisarAvanceTool())
        full.register(PublicarSocialTool())
        full.register(_ToolSegura())
        return full

    worker = {"tenant_id": uuid.uuid4(), "tools": []}
    monkeypatch.setattr(reg, "companion_para", lambda _t: None)
    monkeypatch.delenv("LOCAL_DESKTOP_CAPABILITY", raising=False)

    registry = build_worker_registry(_registro(), worker, local_mode=False)
    nombres = [t.name for t in registry.all()]
    assert "avisar_avance" in nombres
    assert "publicar_social" in nombres
    assert PublicarSocialTool().dangerous is True


def test_filter_extra_tools_read_only_descarta_crear_herramienta() -> None:
    """F1-ALTA: `crear_herramienta` es `dangerous` (write) → la matriz de
    autonomía la descarta en `read_only`; en `full`/`draft` sigue disponible."""
    from edecan_api.bot_turn_service import _filter_extra_tools_by_autonomy
    from edecan_api.persona_tools import CrearHerramientaTool

    tool = CrearHerramientaTool()
    assert tool.dangerous is True

    assert [t.name for t in _filter_extra_tools_by_autonomy([tool], "full")] == [
        "crear_herramienta"
    ]
    assert [t.name for t in _filter_extra_tools_by_autonomy([tool], "draft")] == [
        "crear_herramienta"
    ]
    assert _filter_extra_tools_by_autonomy([tool], "read_only") == []
    # ask = disponible (el freno es la confirmación dangerous, no la matriz).
    assert [t.name for t in _filter_extra_tools_by_autonomy([tool], "ask")] == [
        "crear_herramienta"
    ]


def test_filter_registry_read_only_descarta_enviar_mensaje_bot() -> None:
    """F1-ALTA (laundering): `enviar_mensaje_bot` es `send` → `read_only` no lo
    ofrece; `full` sí."""
    from edecan_core.tools.base import Tool, ToolContext, ToolResult
    from edecan_core.tools.registry import ToolRegistry

    from edecan_api.bot_turn_service import _filter_registry_by_autonomy

    class _MensajeBotTool(Tool):
        name = "enviar_mensaje_bot"
        description = "mensaje inter-agente"
        input_schema = {"type": "object", "properties": {}}

        async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
            return ToolResult(content="")

    registry = ToolRegistry()
    registry.register(_MensajeBotTool())

    assert "enviar_mensaje_bot" in [
        t.name for t in _filter_registry_by_autonomy(registry, "full").all()
    ]
    assert "enviar_mensaje_bot" not in [
        t.name for t in _filter_registry_by_autonomy(registry, "read_only").all()
    ]


def test_filter_registry_c1_category_es_senal_primaria() -> None:
    """C1: la matriz de autonomía clasifica por `category` (señal primaria) y
    por la lista SEND curada, no por `dangerous` + lista manual.

    - Escritura no peligrosa (category=write) NO corre en read_only.
    - Envío real (`enviar_mensaje`, lista SEND) NO corre en draft.
    - Lectura (category=read) SÍ corre en read_only.
    """
    from edecan_core.tools.base import Tool, ToolContext, ToolResult
    from edecan_core.tools.registry import ToolRegistry

    from edecan_api.bot_turn_service import _filter_registry_by_autonomy

    def _fake(nombre: str, categoria: str, *, dangerous: bool = False) -> type[Tool]:
        _nombre = nombre
        _categoria = categoria
        _dangerous = dangerous

        class _T(Tool):
            name = _nombre
            description = f"tool {_nombre}"
            input_schema = {"type": "object", "properties": {}}
            category = _categoria
            dangerous = _dangerous

            async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
                return ToolResult(content="")

        return _T

    registry = ToolRegistry()
    for nombre in ("registrar_transaccion", "crear_evento", "guardar_memoria"):
        registry.register(_fake(nombre, "write")())
    registry.register(_fake("enviar_mensaje", "utility", dangerous=True)())
    registry.register(_fake("leer_archivo", "read")())

    read_only = [t.name for t in _filter_registry_by_autonomy(registry, "read_only").all()]
    draft = [t.name for t in _filter_registry_by_autonomy(registry, "draft").all()]

    # Escritura no peligrosa NO en read_only (antes clasificaba `read`).
    assert "registrar_transaccion" not in read_only
    assert "crear_evento" not in read_only
    assert "guardar_memoria" not in read_only
    # Envío real NO en draft (antes clasificaba `write` por dangerous).
    assert "enviar_mensaje" not in draft
    assert "enviar_mensaje" not in read_only
    # Lectura SÍ en read_only.
    assert "leer_archivo" in read_only
    # draft permite lectura + escritura interna, sin envío externo.
    assert "registrar_transaccion" in draft
    assert "leer_archivo" in draft


def test_normalize_stored_message_incluye_asignacion_worker_id() -> None:
    from edecan_api.bot_turn_service import normalize_stored_message

    worker_id = str(uuid.uuid4())
    row = {
        "id": uuid.uuid4(),
        "conversation_id": uuid.uuid4(),
        "role": "assistant",
        "content": {
            "kind": "evento",
            "evento": "asignacion",
            "text": "Se lo pasé a Backend",
            "de": "Backend",
            "goal": "smoke / API",
            "assigned_worker_id": worker_id,
            "assigned_worker_name": "Backend",
            "motivo": "smoke / API",
            "cara": {"emoji": "⚙️"},
        },
        "created_at": "2026-09-07T00:00:00Z",
    }
    normalizado = normalize_stored_message(row)
    assert normalizado["kind"] == "evento"
    assert normalizado["evento"] == "asignacion"
    assert normalizado["assigned_worker_id"] == worker_id
    assert normalizado["assigned_worker_name"] == "Backend"
    assert normalizado["motivo"] == "smoke / API"
    assert normalizado["de"] == "Backend"


def test_normalize_stored_message_asignacion_aplana_aliases_legacy() -> None:
    from edecan_api.bot_turn_service import normalize_stored_message

    worker_id = str(uuid.uuid4())
    row = {
        "id": uuid.uuid4(),
        "conversation_id": uuid.uuid4(),
        "role": "assistant",
        "content": {
            "kind": "evento",
            "evento": "asignacion",
            "text": "Se lo pasé a Frontend",
            "asignado_id": worker_id,
            "asignado_nombre": "Frontend",
        },
        "created_at": "2026-09-07T00:00:00Z",
    }
    normalizado = normalize_stored_message(row)
    assert normalizado["assigned_worker_id"] == worker_id
    assert normalizado["assigned_worker_name"] == "Frontend"


def test_normalize_stored_message_incluye_tool_calls_con_blocks() -> None:
    from edecan_api.bot_turn_service import normalize_stored_message

    row = {
        "id": uuid.uuid4(),
        "conversation_id": uuid.uuid4(),
        "role": "assistant",
        "content": {
            "text": "¿Cuál prefieres?",
            "sender_id": "bot-1",
            "sender_name": "BotAlpha",
        },
        "tool_calls": [
            {
                "type": "tool_end",
                "tool_call_id": "call_q",
                "name": "preguntar_al_usuario",
                "ok": True,
                "blocks_version": 1,
                "blocks": [
                    {
                        "schema_version": 1,
                        "type": "question",
                        "question": "¿Cuál prefieres?",
                        "header": "Destino",
                        "options": [{"label": "A"}, {"label": "B"}],
                        "multi_select": False,
                        "allow_free_text": True,
                    }
                ],
            }
        ],
        "created_at": "2026-09-07T00:00:00Z",
    }
    normalizado = normalize_stored_message(row)
    assert normalizado["text"] == "¿Cuál prefieres?"
    assert len(normalizado["tool_calls"]) == 1
    assert normalizado["tool_calls"][0]["blocks"][0]["type"] == "question"
    assert normalizado["tool_calls"][0]["blocks"][0]["question"] == "¿Cuál prefieres?"
    assert "prompt" not in normalizado["tool_calls"][0]["blocks"][0]


async def test_stream_split_texto_tool_done_sin_texto_final_persiste_la_tool() -> None:
    """Caso borde de E-API-4: `texto → tool → done` (sin texto posterior).
    La tool NO debe quedar huérfana: se persiste con el mensaje que la pidió."""

    from edecan_api.routers.conversations import _stream_agent_events

    async def _eventos() -> Any:
        yield {"type": "text_delta", "text": "Reviso el archivo."}
        yield {"type": "tool_start", "name": "leer_archivo", "args": {}}
        yield {"type": "tool_end", "name": "leer_archivo", "result_preview": "…"}
        yield {"type": "done", "usage": {"input_tokens": 3, "output_tokens": 2}}

    repo = _RepoStreamFalso()
    async for _sse in _stream_agent_events(
        events=_eventos(),
        repo=repo,  # type: ignore[arg-type]
        tenant_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        settings=SimpleNamespace(),  # type: ignore[arg-type]
        redis_client=object(),  # type: ignore[arg-type]
        split_messages=True,
    ):
        pass

    assert len(repo.mensajes) == 1
    assert repo.mensajes[0]["content"]["text"] == "Reviso el archivo."
    assert len(repo.mensajes[0]["tool_calls"]) == 2


async def test_split_fusiona_tramo_diminuto_en_vez_de_romper_la_palabra() -> None:
    """«Ahora» + tool + « sí: creé…» NO debe producir dos burbujas (la
    palabra rota): el tramo diminuto se fusiona con el siguiente."""

    from edecan_api.routers.conversations import _stream_agent_events

    async def _eventos() -> Any:
        yield {"type": "text_delta", "text": "Ahora"}
        yield {"type": "tool_start", "name": "delegar_mision", "args": {}}
        yield {"type": "tool_end", "name": "delegar_mision", "result_preview": "…"}
        yield {"type": "text_delta", "text": " sí: creé la misión."}
        yield {"type": "done", "usage": {"input_tokens": 3, "output_tokens": 2}}

    repo = _RepoStreamFalso()
    chunks: list[str] = []
    async for _sse in _stream_agent_events(
        events=_eventos(),
        repo=repo,  # type: ignore[arg-type]
        tenant_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        settings=SimpleNamespace(),  # type: ignore[arg-type]
        redis_client=object(),  # type: ignore[arg-type]
        split_messages=True,
    ):
        chunks.append(_sse)

    nombres = [c.strip().split("\\n")[0] for c in chunks]
    starts = sum(1 for n in nombres if n.startswith("event: message_start"))
    assert starts == 1, f"esperaba UNA burbuja, hubo {starts}: {nombres}"
    assert len(repo.mensajes) == 1
    assert repo.mensajes[0]["content"]["text"] == "Ahora sí: creé la misión."
    assert len(repo.mensajes[0]["tool_calls"]) == 2


async def _async_return(value: Any):
    return value


async def test_stream_worker_turn_inyecta_extra_tools_y_preaprobaciones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    import edecan_api.bot_turn_service as servicio
    import edecan_api.deps as deps
    import edecan_api.routers.conversations as conversaciones
    import edecan_api.routers.perfil as perfil
    from edecan_api.deps import CurrentUser, TenantCtx

    class _Tool:
        name = "mcp_demo_buscar"

    capturado: dict[str, Any] = {}

    class _Agent:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run_turn(self, **kwargs):
            capturado["extra_tools"] = kwargs.get("extra_tools")
            yield {"type": "done", "usage": {}}

    class _RepoFalso:
        async def list_messages(self, **_kwargs):
            return []

    async def _persist_vacio(*_args, **_kwargs):
        return None

    async def _stream_vacio(**kwargs):
        async for _evento in kwargs.get("events", ()):
            pass
        if False:
            yield None

    def _build_ctx(**kwargs):
        capturado["approved"] = kwargs.get("approved_tool_calls")
        return SimpleNamespace(extras={})

    monkeypatch.setattr(servicio, "persist_chat_message", _persist_vacio)
    monkeypatch.setattr(
        servicio,
        "persona_from_worker",
        lambda _w: SimpleNamespace(instrucciones=""),
    )
    monkeypatch.setattr(servicio, "companion_para", lambda _t: None)
    monkeypatch.setattr(servicio, "build_worker_registry", lambda *_a, **_k: object())
    async def _load_unified_session(*_a, **_k):
        return None

    monkeypatch.setattr(servicio, "load_unified_session", _load_unified_session)
    monkeypatch.setattr(servicio, "build_skills_context", lambda *_a, **_k: _async_return(""))
    monkeypatch.setattr(
        servicio,
        "_extra_bot_turn_tools",
        lambda *_a, **_k: _async_return([_Tool()]),
    )
    async def _get_repo(_s):
        return _RepoFalso()

    async def _get_vault(_s, _st):
        return object()

    async def _get_llm_router(_r):
        return object()

    monkeypatch.setattr(deps, "get_repo", _get_repo)
    monkeypatch.setattr(deps, "get_vault", _get_vault)
    monkeypatch.setattr(deps, "get_llm_router", _get_llm_router)
    monkeypatch.setattr(deps, "get_redis", lambda _s: object())
    monkeypatch.setattr(conversaciones, "_agent_for_request", lambda *_a, **_k: _Agent())
    monkeypatch.setattr(conversaciones, "_build_ctx", _build_ctx)
    monkeypatch.setattr(conversaciones, "_tools_con_pregunta_pendiente", lambda _r: [])
    monkeypatch.setattr(
        conversaciones,
        "_unified_session_for",
        lambda **_k: SimpleNamespace(user_id=None, visual_memory=None, touch=lambda **t: None),
    )
    monkeypatch.setattr(conversaciones, "_stream_agent_events", _stream_vacio)
    monkeypatch.setattr(conversaciones, "get_tool_registry", lambda _r: object())
    monkeypatch.setattr(perfil, "profile_context_for", lambda *_a, **_k: _async_return(""))

    tenant_id = uuid.uuid4()
    user = CurrentUser(
        user_id=uuid.uuid4(),
        tenant=TenantCtx(tenant_id=tenant_id, plan_key="hosted_basic", flags={}),
    )
    worker = {
        "id": str(uuid.uuid4()),
        "tenant_id": str(tenant_id),
        "name": "BotAlpha",
        "display_name": "BotAlpha",
        "conversation_id": str(uuid.uuid4()),
    }
    settings = SimpleNamespace(
        BOT_CONTEXT_MAX_MESSAGES=50,
        BOT_CONTEXT_MAX_CHARS=2_000,
        EDECAN_LOCAL_MODE=False,
    )

    async for _chunk in servicio.stream_worker_turn(
        request=SimpleNamespace(),
        session=object(),
        user=user,
        settings=settings,
        worker=worker,
        conversation_id=uuid.uuid4(),
        user_text="crea un endpoint",
    ):
        pass

    assert len(capturado["extra_tools"]) == 1
    assert capturado["extra_tools"][0].name == "mcp_demo_buscar"
    assert "acceder_codigo_local" in capturado["approved"]
    # BOTS-06: sin grant explícito, una tool MCP NO se pre-aprueba por prefijo.
    assert "mcp_demo_buscar" not in capturado["approved"]
    assert not any(tok.startswith("mcp_grant:") for tok in capturado["approved"])
    assert "conectar_mcp" not in capturado["approved"]


def test_clamp_message_limit_acota_extremos() -> None:
    assert clamp_message_limit(None) == 50
    assert clamp_message_limit(1) == 1
    assert clamp_message_limit(200) == 200
    assert clamp_message_limit(0) == 1
    assert clamp_message_limit(9_999) == 200


async def test_list_normalized_messages_limita_historial() -> None:
    sesion = _FakeSesion()
    tenant_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    filas = await list_normalized_messages(
        sesion, tenant_id=tenant_id, conversation_id=conversation_id
    )
    assert filas == []
    sql, params = sesion.statements[0]
    assert "LIMIT" in sql.upper()
    assert params is not None
    assert params["limit"] == 50
    assert params["tenant_id"] == str(tenant_id)
    assert params["conversation_id"] == str(conversation_id)


async def test_list_normalized_messages_respeta_limit_pedido() -> None:
    sesion = _FakeSesion()
    await list_normalized_messages(
        sesion,
        tenant_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        limit=1,
    )
    assert sesion.statements[0][1]["limit"] == 1
    await list_normalized_messages(
        sesion,
        tenant_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        limit=5_000,
    )
    assert sesion.statements[-1][1]["limit"] == 200


async def test_stream_worker_turn_preaprueba_mcp_solo_con_grant_versionado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BOTS-06: una tool MCP entra pre-aprobada SOLO con un grant explícito cuya
    versión coincide con la definición actual; el `approved` lleva el token
    versionado (`mcp_grant:{name}:{version}`), no el nombre suelto."""
    from types import SimpleNamespace

    from edecan_core.bot_harness import (
        MCP_OPERATION_READ,
        mcp_grant_token,
        mcp_tool_definition_version,
    )

    import edecan_api.bot_turn_service as servicio
    import edecan_api.deps as deps
    import edecan_api.routers.conversations as conversaciones
    import edecan_api.routers.perfil as perfil
    from edecan_api.deps import CurrentUser, TenantCtx

    version = mcp_tool_definition_version(
        name="buscar", description="", input_schema={"type": "object"}, server_name="demo"
    )

    class _Tool:
        name = "mcp_demo_buscar"
        definition_version = version

    capturado: dict[str, Any] = {}

    class _Agent:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run_turn(self, **kwargs):
            yield {"type": "done", "usage": {}}

    class _RepoFalso:
        async def list_messages(self, **_kwargs):
            return []

    async def _persist_vacio(*_args, **_kwargs):
        return None

    async def _stream_vacio(**kwargs):
        async for _evento in kwargs.get("events", ()):
            pass
        if False:
            yield None

    def _build_ctx(**kwargs):
        capturado["approved"] = kwargs.get("approved_tool_calls")
        return SimpleNamespace(extras={})

    monkeypatch.setattr(servicio, "persist_chat_message", _persist_vacio)
    monkeypatch.setattr(
        servicio, "persona_from_worker", lambda _w: SimpleNamespace(instrucciones="")
    )
    monkeypatch.setattr(servicio, "companion_para", lambda _t: None)
    monkeypatch.setattr(servicio, "build_worker_registry", lambda *_a, **_k: object())

    async def _load_unified_session(*_a, **_k):
        return None

    monkeypatch.setattr(servicio, "load_unified_session", _load_unified_session)
    monkeypatch.setattr(servicio, "build_skills_context", lambda *_a, **_k: _async_return(""))
    monkeypatch.setattr(
        servicio, "_extra_bot_turn_tools", lambda *_a, **_k: _async_return([_Tool()])
    )

    async def _get_repo(_s):
        return _RepoFalso()

    async def _get_vault(_s, _st):
        return object()

    async def _get_llm_router(_r):
        return object()

    monkeypatch.setattr(deps, "get_repo", _get_repo)
    monkeypatch.setattr(deps, "get_vault", _get_vault)
    monkeypatch.setattr(deps, "get_llm_router", _get_llm_router)
    monkeypatch.setattr(deps, "get_redis", lambda _s: object())
    monkeypatch.setattr(conversaciones, "_agent_for_request", lambda *_a, **_k: _Agent())
    monkeypatch.setattr(conversaciones, "_build_ctx", _build_ctx)
    monkeypatch.setattr(conversaciones, "_tools_con_pregunta_pendiente", lambda _r: [])
    monkeypatch.setattr(
        conversaciones,
        "_unified_session_for",
        lambda **_k: SimpleNamespace(user_id=None, visual_memory=None, touch=lambda **t: None),
    )
    monkeypatch.setattr(conversaciones, "_stream_agent_events", _stream_vacio)
    monkeypatch.setattr(conversaciones, "get_tool_registry", lambda _r: object())
    monkeypatch.setattr(perfil, "profile_context_for", lambda *_a, **_k: _async_return(""))

    tenant_id = uuid.uuid4()
    user = CurrentUser(
        user_id=uuid.uuid4(),
        tenant=TenantCtx(tenant_id=tenant_id, plan_key="hosted_basic", flags={}),
    )
    worker = {
        "id": str(uuid.uuid4()),
        "tenant_id": str(tenant_id),
        "name": "BotAlpha",
        "display_name": "BotAlpha",
        "conversation_id": str(uuid.uuid4()),
        "approval_policy": {
            "mcp_grants": [
                {
                    "tool_name": "mcp_demo_buscar",
                    "operation": "read",
                    "definition_version": version,
                }
            ]
        },
    }
    settings = SimpleNamespace(
        BOT_CONTEXT_MAX_MESSAGES=50,
        BOT_CONTEXT_MAX_CHARS=2_000,
        EDECAN_LOCAL_MODE=False,
    )

    async for _chunk in servicio.stream_worker_turn(
        request=SimpleNamespace(),
        session=object(),
        user=user,
        settings=settings,
        worker=worker,
        conversation_id=uuid.uuid4(),
        user_text="busca algo",
    ):
        pass

    token = mcp_grant_token(
        tool_name="mcp_demo_buscar", operation=MCP_OPERATION_READ, definition_version=version
    )
    assert token in capturado["approved"]
    assert "mcp_demo_buscar" not in capturado["approved"]
