"""Pruebas de proyectos y conversaciones del IDE (`ide_projects.py`, `ide_runtime.py`).

Cimiento B: agrupar conversaciones bajo un proyecto (carpeta), estilo
Antigravity. Cubre el registro puro (`ProjectRegistry`) y el cableado de
acciones `ide_project_*`/`ide_conversation_*` en `IDERuntime.dispatch`,
incluyendo el mejor esfuerzo de cancelar sesiones de agente vivas al borrar
una conversación -- sin tocar `ide_sessions.py`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from edecan_companion.config import CompanionConfig
from edecan_companion.ide_projects import IDEProjectError, ProjectRegistry
from edecan_companion.ide_runtime import IDERuntime, execute_ide_action
from edecan_companion.ide_workspaces import WorkspaceStore


async def _approve(_action: str, _params: dict[str, Any], _config: CompanionConfig) -> bool:
    return True


def _project_folder(tmp_path: Path, name: str = "repo") -> Path:
    path = tmp_path / name
    path.mkdir()
    return path


async def _call(
    companion_config: CompanionConfig, action: str, params: dict[str, Any]
) -> dict[str, Any]:
    result = await execute_ide_action(action, params, companion_config, _approve)
    assert result["ok"], result
    return result["result"]


async def _authorize(companion_config: CompanionConfig, path: Path) -> dict[str, Any]:
    result = await _call(
        companion_config, "ide_workspace_authorize", {"path": str(path), "name": "Repo"}
    )
    return result["workspace"]


# ---------------------------------------------------------------------------
# ProjectRegistry en aislamiento (sin pasar por el dispatcher de acciones)
# ---------------------------------------------------------------------------


def _registry(tmp_path: Path) -> tuple[ProjectRegistry, WorkspaceStore, dict[str, Any]]:
    state_dir = tmp_path / "ide"
    workspaces = WorkspaceStore(state_dir)
    workspace = workspaces.authorize(str(_project_folder(tmp_path)))
    return ProjectRegistry(state_dir, workspaces), workspaces, workspace


def test_create_project_requires_an_already_authorized_workspace(tmp_path: Path):
    state_dir = tmp_path / "ide"
    workspaces = WorkspaceStore(state_dir)
    registry = ProjectRegistry(state_dir, workspaces)

    with pytest.raises(IDEProjectError):
        registry.create_project("Mi proyecto", "workspace-inexistente")


def test_create_list_rename_project_round_trip(tmp_path: Path):
    registry, _workspaces, workspace = _registry(tmp_path)

    created = registry.create_project("Edecán", workspace["id"])
    assert created["name"] == "Edecán"
    assert created["workspace_id"] == workspace["id"]
    assert created["workspace_name"] == workspace["name"]
    assert created["conversation_count"] == 0

    renamed = registry.rename_project(created["id"], "Edecán v2")
    assert renamed["name"] == "Edecán v2"

    listed = registry.list_projects()
    assert [row["id"] for row in listed] == [created["id"]]
    assert listed[0]["name"] == "Edecán v2"

    # Otra instancia reconstruye el registro persistido, como tras reiniciar
    # el companion.
    fresh = ProjectRegistry(registry.state_dir, _workspaces)
    assert fresh.get_project(created["id"])["name"] == "Edecán v2"


def test_project_name_validation_rejects_empty_and_control_characters(tmp_path: Path):
    registry, _workspaces, workspace = _registry(tmp_path)

    with pytest.raises(IDEProjectError):
        registry.create_project("", workspace["id"])
    with pytest.raises(IDEProjectError):
        registry.create_project("nombre\x00malo", workspace["id"])


def test_conversation_belongs_to_a_project_and_can_be_moved_or_detached(tmp_path: Path):
    registry, _workspaces, workspace = _registry(tmp_path)
    project = registry.create_project("Proyecto A", workspace["id"])
    other_project = registry.create_project("Proyecto B", workspace["id"])

    conversation = registry.create_conversation(project["id"], "Primera charla")
    assert conversation["project_id"] == project["id"]
    assert conversation["title"] == "Primera charla"

    default_titled = registry.create_conversation(None)
    assert default_titled["title"] == "Nueva conversación"
    assert default_titled["project_id"] is None

    moved = registry.move_conversation(conversation["id"], other_project["id"])
    assert moved["project_id"] == other_project["id"]

    detached = registry.move_conversation(conversation["id"], None)
    assert detached["project_id"] is None

    assert registry.list_conversations(project["id"]) == []
    unassigned = registry.list_conversations(only_unassigned=True)
    assert {row["id"] for row in unassigned} == {conversation["id"], default_titled["id"]}


def test_delete_project_default_detaches_conversations_without_deleting_them(tmp_path: Path):
    registry, _workspaces, workspace = _registry(tmp_path)
    project = registry.create_project("Proyecto", workspace["id"])
    conversation = registry.create_conversation(project["id"])

    result = registry.delete_project(project["id"])

    assert result["conversations_deleted"] is False
    assert result["affected_conversation_ids"] == [conversation["id"]]
    assert registry.get_conversation(conversation["id"])["project_id"] is None
    with pytest.raises(IDEProjectError):
        registry.get_project(project["id"])


def test_delete_project_with_delete_mode_also_deletes_its_conversations(tmp_path: Path):
    registry, _workspaces, workspace = _registry(tmp_path)
    project = registry.create_project("Proyecto", workspace["id"])
    conversation = registry.create_conversation(project["id"])

    result = registry.delete_project(project["id"], conversations="delete")

    assert result["conversations_deleted"] is True
    with pytest.raises(IDEProjectError):
        registry.get_conversation(conversation["id"])


def test_delete_project_never_touches_the_repo_folder_on_disk(tmp_path: Path):
    registry, _workspaces, workspace = _registry(tmp_path)
    project = registry.create_project("Proyecto", workspace["id"])

    registry.delete_project(project["id"], conversations="delete")

    assert Path(workspace["path"]).is_dir()


def test_delete_conversation_removes_it_from_listings(tmp_path: Path):
    registry, _workspaces, workspace = _registry(tmp_path)
    project = registry.create_project("Proyecto", workspace["id"])
    conversation = registry.create_conversation(project["id"])

    registry.delete_conversation(conversation["id"])

    assert registry.list_conversations(project["id"]) == []
    with pytest.raises(IDEProjectError):
        registry.get_conversation(conversation["id"])
    assert registry.delete_conversation(conversation["id"]) == {
        "deleted_conversation_id": conversation["id"]
    }


# ---------------------------------------------------------------------------
# Cableado end-to-end a través de execute_ide_action (como lo llama ide.py)
# ---------------------------------------------------------------------------


async def test_project_and_conversation_actions_round_trip_through_dispatch(
    companion_config: CompanionConfig, tmp_path: Path
):
    workspace = await _authorize(companion_config, _project_folder(tmp_path))

    created = await _call(
        companion_config,
        "ide_project_create",
        {"name": "Forge Studio", "workspace_id": workspace["id"]},
    )
    project = created["project"]
    assert project["name"] == "Forge Studio"

    renamed = await _call(
        companion_config,
        "ide_project_rename",
        {"project_id": project["id"], "name": "Forge Studio v2"},
    )
    assert renamed["project"]["name"] == "Forge Studio v2"

    listed_projects = await _call(companion_config, "ide_project_list", {})
    assert [row["id"] for row in listed_projects["projects"]] == [project["id"]]

    conversation = (
        await _call(
            companion_config,
            "ide_conversation_create",
            {"project_id": project["id"], "title": "Arreglar el README"},
        )
    )["conversation"]
    assert conversation["project_id"] == project["id"]

    listed_conversations = await _call(
        companion_config, "ide_conversation_list", {"project_id": project["id"]}
    )
    assert [row["id"] for row in listed_conversations["conversations"]] == [conversation["id"]]

    moved = await _call(
        companion_config,
        "ide_conversation_move",
        {"conversation_id": conversation["id"], "project_id": None},
    )
    assert moved["conversation"]["project_id"] is None

    deleted = await _call(
        companion_config, "ide_conversation_delete", {"conversation_id": conversation["id"]}
    )
    assert deleted["deleted_conversation_id"] == conversation["id"]
    assert deleted["closed_session_ids"] == []


async def test_deleting_a_conversation_cancels_its_still_running_agent_session(
    companion_config: CompanionConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """No purga el historial de `ide_sessions.py` (ese archivo no se toca),
    pero SÍ debe cortar cualquier proceso de agente que siga corriendo."""
    workspace = await _authorize(companion_config, _project_folder(tmp_path))
    conversation = (await _call(companion_config, "ide_conversation_create", {"project_id": None}))[
        "conversation"
    ]

    async def never_finishes(_self, **kwargs):
        cancelled = kwargs["cancelled"]
        while not cancelled():
            import asyncio

            await asyncio.sleep(0.01)

    monkeypatch.setattr("edecan_companion.ide_workers_agent.WorkersIDEAgent.run", never_finishes)
    started = await _call(
        companion_config,
        "ide_agent_start",
        {
            "workspace_id": workspace["id"],
            "prompt": "Trabaja",
            "conversation_id": conversation["id"],
        },
    )
    session_id = started["session"]["id"]

    deleted = await _call(
        companion_config, "ide_conversation_delete", {"conversation_id": conversation["id"]}
    )

    assert deleted["closed_session_ids"] == [session_id]
    read_back = await _call(
        companion_config, "ide_agent_read", {"session_id": session_id, "cursor": 0}
    )
    assert read_back["session"]["status"] == "cancelled"


async def test_deleting_a_project_in_delete_mode_cancels_running_sessions_of_its_conversations(
    companion_config: CompanionConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    workspace = await _authorize(companion_config, _project_folder(tmp_path))
    project = (
        await _call(
            companion_config,
            "ide_project_create",
            {"name": "Proyecto", "workspace_id": workspace["id"]},
        )
    )["project"]
    conversation = (
        await _call(companion_config, "ide_conversation_create", {"project_id": project["id"]})
    )["conversation"]

    async def never_finishes(_self, **kwargs):
        cancelled = kwargs["cancelled"]
        while not cancelled():
            import asyncio

            await asyncio.sleep(0.01)

    monkeypatch.setattr("edecan_companion.ide_workers_agent.WorkersIDEAgent.run", never_finishes)
    started = await _call(
        companion_config,
        "ide_agent_start",
        {
            "workspace_id": workspace["id"],
            "prompt": "Trabaja",
            "conversation_id": conversation["id"],
        },
    )
    session_id = started["session"]["id"]

    result = await _call(
        companion_config,
        "ide_project_delete",
        {"project_id": project["id"], "conversations": "delete"},
    )
    assert result["conversations_deleted"] is True

    read_back = await _call(
        companion_config, "ide_agent_read", {"session_id": session_id, "cursor": 0}
    )
    assert read_back["session"]["status"] == "cancelled"


async def test_project_delete_rejects_an_invalid_conversations_mode(
    companion_config: CompanionConfig, tmp_path: Path
):
    workspace = await _authorize(companion_config, _project_folder(tmp_path))
    project = (
        await _call(
            companion_config,
            "ide_project_create",
            {"name": "Proyecto", "workspace_id": workspace["id"]},
        )
    )["project"]

    result = await execute_ide_action(
        "ide_project_delete",
        {"project_id": project["id"], "conversations": "algo-invalido"},
        companion_config,
        _approve,
    )

    assert result["ok"] is False


def test_runtime_instantiates_project_registry(companion_config: CompanionConfig):
    runtime = IDERuntime(companion_config)
    assert isinstance(runtime.projects, ProjectRegistry)
