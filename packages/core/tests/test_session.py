from uuid import UUID

from edecan_core.session import UnifiedSessionState
from edecan_core.session_store import load_unified_session, save_unified_session


class _Result:
    def __init__(self, row=None):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _DbSession:
    def __init__(self, row=None):
        self.row = row
        self.sql = []
        self.params = []
        self.commits = 0

    async def execute(self, statement, params):
        self.sql.append(str(statement))
        self.params.append(params)
        return _Result(self.row)

    async def commit(self):
        self.commits += 1


def test_unified_session_roundtrip_and_compatibilidad_multimodal():
    state = UnifiedSessionState(
        session_id="session-1",
        tenant_id="tenant-1",
        user_id="user-1",
        conversation_id="conversation-1",
        project_id="project-1",
    )
    state.touch(modality="screen")
    state.attach_task("task-1")
    state.register_agent("research")
    state.register_tool("buscar_web")
    state.multimodal.last_screen_frame_summary = "editor abierto"

    restored = UnifiedSessionState.from_dict(state.to_dict())

    assert restored.modality == "screen"
    assert restored.active_task == "task-1"
    assert restored.active_agents == ["research"]
    assert restored.connected_tools == ["buscar_web"]
    assert restored.visual_memory is restored.multimodal.visual_memory
    assert restored.multimodal.last_screen_frame_summary == "editor abierto"


def test_unified_session_rechaza_modalidad_y_nombres_vacios():
    state = UnifiedSessionState("s", "t", "u", "c")

    try:
        state.touch(modality="unknown")
    except ValueError as exc:
        assert "Modalidad" in str(exc)
    else:
        raise AssertionError("una modalidad desconocida debe fallar cerrado")

    try:
        state.register_agent(" ")
    except ValueError as exc:
        assert "agente" in str(exc)
    else:
        raise AssertionError("un agente vacío debe fallar cerrado")


def test_unified_session_no_duplica_agentes_ni_tools():
    state = UnifiedSessionState("s", "t", "u", "c")
    state.register_agent("coding")
    state.register_agent("coding")
    state.register_tool("leer_archivo")
    state.register_tool("leer_archivo")

    assert state.active_agents == ["coding"]
    assert state.connected_tools == ["leer_archivo"]


async def test_session_store_hace_load_y_upsert_parametrizados():
    state = UnifiedSessionState("s", "t", "u", "c")
    state.attach_task("mission-1")
    db = _DbSession({"state": state.to_dict()})
    tenant_id = UUID("00000000-0000-0000-0000-000000000001")
    user_id = UUID("00000000-0000-0000-0000-000000000002")
    conversation_id = UUID("00000000-0000-0000-0000-000000000003")

    restored = await load_unified_session(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation_id,
    )
    await save_unified_session(
        db,
        state,
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation_id,
    )

    assert restored is not None
    assert restored.active_task == "mission-1"
    assert "WHERE tenant_id = :tenant_id" in db.sql[0]
    assert "ON CONFLICT (tenant_id, user_id, conversation_id)" in db.sql[1]
    assert "unified_sessions.state->>'updated_at'" in db.sql[1]
    assert "EXCLUDED.state->>'updated_at'" in db.sql[1]
    assert db.params[1]["tenant_id"] == str(tenant_id)
    assert db.commits == 0
