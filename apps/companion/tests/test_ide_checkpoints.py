"""Puntos de control (deshacer) del workspace — ``ide_checkpoints.CheckpointStore``.

Cubre el ciclo completo que pide el encargo: crear punto de control,
modificar, restaurar; restaurar un archivo suelto; archivo nuevo creado por
el agente; archivo borrado por el agente; superar el tope de tamaño. Suma
además las dos piezas de diseño que el encargo pedía razonar explícitamente:
que deshacer nunca pise trabajo manual posterior, y que los checkpoints
caduquen sin llenar el disco.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from edecan_companion.ide_checkpoints import (
    MAX_TRACKED_FILE_BYTES,
    CheckpointStore,
    IDECheckpointError,
)
from edecan_companion.ide_workspaces import WorkspaceStore


def _make_store(tmp_path: Path, **kwargs) -> tuple[CheckpointStore, WorkspaceStore, Path]:
    state_dir = tmp_path / "state"
    project = tmp_path / "proyecto"
    project.mkdir()
    workspaces = WorkspaceStore(state_dir)
    workspaces.authorize(str(project))
    checkpoints = CheckpointStore(state_dir, workspaces, **kwargs)
    return checkpoints, workspaces, project


# --------------------------------------------------------------------- #
# Ciclo básico: crear, modificar, restaurar.
# --------------------------------------------------------------------- #


def test_create_track_modify_restore_recovers_original_content(tmp_path: Path):
    checkpoints, workspaces, project = _make_store(tmp_path)
    workspace_id = workspaces.list()[0]["id"]
    target = project / "main.py"
    target.write_text("version 1\n", encoding="utf-8")

    checkpoint = checkpoints.create(workspace_id, label="turno de prueba")
    checkpoints.track(checkpoint["id"], "main.py")

    # El agente "edita" el archivo.
    target.write_text("version 2 (roto)\n", encoding="utf-8")

    result = checkpoints.restore(checkpoint["id"])

    assert result["restored"] == ["main.py"]
    assert target.read_text(encoding="utf-8") == "version 1\n"


def test_track_is_idempotent_keeps_first_captured_state(tmp_path: Path):
    checkpoints, workspaces, project = _make_store(tmp_path)
    workspace_id = workspaces.list()[0]["id"]
    target = project / "main.py"
    target.write_text("original\n", encoding="utf-8")

    checkpoint = checkpoints.create(workspace_id)
    checkpoints.track(checkpoint["id"], "main.py")
    target.write_text("edicion intermedia del agente\n", encoding="utf-8")
    # Segunda llamada a track() sobre el mismo archivo en el mismo
    # checkpoint: NO debe pisar la captura original con este estado
    # intermedio, porque "antes" significa antes de TODO el turno.
    checkpoints.track(checkpoint["id"], "main.py")
    target.write_text("edicion final del agente\n", encoding="utf-8")

    result = checkpoints.restore(checkpoint["id"])

    assert result["restored"] == ["main.py"]
    assert target.read_text(encoding="utf-8") == "original\n"


# --------------------------------------------------------------------- #
# Restaurar un archivo suelto.
# --------------------------------------------------------------------- #


def test_restore_single_file_leaves_others_untouched(tmp_path: Path):
    checkpoints, workspaces, project = _make_store(tmp_path)
    workspace_id = workspaces.list()[0]["id"]
    (project / "a.py").write_text("a original\n", encoding="utf-8")
    (project / "b.py").write_text("b original\n", encoding="utf-8")

    checkpoint = checkpoints.create(workspace_id)
    checkpoints.track(checkpoint["id"], "a.py")
    checkpoints.track(checkpoint["id"], "b.py")

    (project / "a.py").write_text("a modificado\n", encoding="utf-8")
    (project / "b.py").write_text("b modificado\n", encoding="utf-8")

    result = checkpoints.restore_file(checkpoint["id"], "a.py")

    assert result["restored"] == ["a.py"]
    assert (project / "a.py").read_text(encoding="utf-8") == "a original\n"
    # b.py no estaba en la lista de restauración: sigue como lo dejó el agente.
    assert (project / "b.py").read_text(encoding="utf-8") == "b modificado\n"


def test_restore_unknown_path_raises(tmp_path: Path):
    checkpoints, workspaces, project = _make_store(tmp_path)
    workspace_id = workspaces.list()[0]["id"]
    (project / "a.py").write_text("a\n", encoding="utf-8")
    checkpoint = checkpoints.create(workspace_id)
    checkpoints.track(checkpoint["id"], "a.py")

    with pytest.raises(IDECheckpointError):
        checkpoints.restore_file(checkpoint["id"], "nunca_trackeado.py")


# --------------------------------------------------------------------- #
# Archivo nuevo creado por el agente / archivo borrado por el agente.
# --------------------------------------------------------------------- #


def test_restore_deletes_file_the_agent_created(tmp_path: Path):
    checkpoints, workspaces, project = _make_store(tmp_path)
    workspace_id = workspaces.list()[0]["id"]

    checkpoint = checkpoints.create(workspace_id)
    # El archivo no existe todavía: el agente está a punto de crearlo.
    checkpoints.track(checkpoint["id"], "nuevo.py")
    (project / "nuevo.py").write_text("contenido creado por el agente\n", encoding="utf-8")

    result = checkpoints.restore(checkpoint["id"])

    assert result["deleted"] == ["nuevo.py"]
    assert not (project / "nuevo.py").exists()


def test_restore_recreates_file_the_agent_deleted(tmp_path: Path):
    checkpoints, workspaces, project = _make_store(tmp_path)
    workspace_id = workspaces.list()[0]["id"]
    target = project / "importante.py"
    target.write_text("no me borres\n", encoding="utf-8")

    checkpoint = checkpoints.create(workspace_id)
    checkpoints.track(checkpoint["id"], "importante.py")
    target.unlink()  # el agente lo borró

    result = checkpoints.restore(checkpoint["id"])

    assert result["restored"] == ["importante.py"]
    assert target.read_text(encoding="utf-8") == "no me borres\n"


def test_restore_of_untouched_file_is_a_noop(tmp_path: Path):
    checkpoints, workspaces, project = _make_store(tmp_path)
    workspace_id = workspaces.list()[0]["id"]
    target = project / "quieto.py"
    target.write_text("sin cambios\n", encoding="utf-8")

    checkpoint = checkpoints.create(workspace_id)
    checkpoints.track(checkpoint["id"], "quieto.py")
    # nadie lo tocó

    result = checkpoints.restore(checkpoint["id"])

    assert result["unchanged"] == ["quieto.py"]
    assert result["restored"] == []


# --------------------------------------------------------------------- #
# Tope de tamaño.
# --------------------------------------------------------------------- #


def test_track_over_size_cap_does_not_crash_and_is_unrestorable(tmp_path: Path):
    checkpoints, workspaces, project = _make_store(tmp_path, max_tracked_file_bytes=1024)
    workspace_id = workspaces.list()[0]["id"]
    huge = project / "gigante.bin"
    huge.write_bytes(b"x" * 4096)

    checkpoint = checkpoints.create(workspace_id)
    tracked = checkpoints.track(checkpoint["id"], "gigante.bin")

    assert tracked["status"] == "skipped_too_large"
    assert tracked["digest"] is None

    huge.write_bytes(b"y" * 4096)  # el agente lo "arruina" igual
    result = checkpoints.restore(checkpoint["id"])

    assert result["restored"] == []
    assert result["unrestorable"] == [{"path": "gigante.bin", "reason": "skipped_too_large"}]
    # El contenido roto se queda: no había nada que restaurar.
    assert huge.read_bytes() == b"y" * 4096


def test_default_size_cap_constant_is_exposed(tmp_path: Path):
    # El límite por defecto es parte de la API pública (documentado, no un
    # número mágico interno) para que quien integre pueda razonar sobre él.
    assert MAX_TRACKED_FILE_BYTES > 0


def test_track_over_total_budget_skips_new_files_but_keeps_earlier_ones(tmp_path: Path):
    checkpoints, workspaces, project = _make_store(
        tmp_path, max_tracked_file_bytes=10_000, max_checkpoint_total_bytes=1500
    )
    workspace_id = workspaces.list()[0]["id"]
    first = project / "first.bin"
    second = project / "second.bin"
    first.write_bytes(b"a" * 1000)
    second.write_bytes(b"b" * 1000)

    checkpoint = checkpoints.create(workspace_id)
    first_tracked = checkpoints.track(checkpoint["id"], "first.bin")
    second_tracked = checkpoints.track(checkpoint["id"], "second.bin")

    assert first_tracked["status"] == "captured"
    assert second_tracked["status"] == "skipped_budget"

    first.write_bytes(b"z" * 1000)
    result = checkpoints.restore(checkpoint["id"])
    assert result["restored"] == ["first.bin"]
    assert first.read_bytes() == b"a" * 1000


# --------------------------------------------------------------------- #
# Nunca perder trabajo manual escrito DESPUÉS del turno del agente.
# --------------------------------------------------------------------- #


def test_restore_blocks_on_conflict_after_seal_without_force(tmp_path: Path):
    checkpoints, workspaces, project = _make_store(tmp_path)
    workspace_id = workspaces.list()[0]["id"]
    target = project / "compartido.py"
    target.write_text("estado original\n", encoding="utf-8")

    checkpoint = checkpoints.create(workspace_id)
    checkpoints.track(checkpoint["id"], "compartido.py")
    target.write_text("lo dejo el agente\n", encoding="utf-8")
    checkpoints.seal(checkpoint["id"])

    # El usuario edita a mano DESPUÉS de que el agente terminó su turno.
    target.write_text("edicion manual del usuario, no tocar\n", encoding="utf-8")

    result = checkpoints.restore(checkpoint["id"])

    assert result["conflicts"] == ["compartido.py"]
    assert result["restored"] == []
    # El archivo queda intacto: la restauración se negó a pisarlo.
    assert target.read_text(encoding="utf-8") == "edicion manual del usuario, no tocar\n"


def test_restore_force_overwrites_but_rescues_the_manual_edit_first(tmp_path: Path):
    checkpoints, workspaces, project = _make_store(tmp_path)
    workspace_id = workspaces.list()[0]["id"]
    target = project / "compartido.py"
    target.write_text("estado original\n", encoding="utf-8")

    checkpoint = checkpoints.create(workspace_id)
    checkpoints.track(checkpoint["id"], "compartido.py")
    target.write_text("lo dejo el agente\n", encoding="utf-8")
    checkpoints.seal(checkpoint["id"])

    target.write_text("edicion manual valiosa\n", encoding="utf-8")

    result = checkpoints.restore(checkpoint["id"], force=True)

    assert result["restored"] == ["compartido.py"]
    assert target.read_text(encoding="utf-8") == "estado original\n"
    rescue_id = result["rescue_checkpoint_id"]
    assert rescue_id is not None

    # La edición manual "perdida" sigue siendo recuperable desde el rescate
    # (con force: el propio rescate quedó sellado a su vez, y el disco ya
    # no coincide con lo que guardó, así que restaurarlo es a su vez un
    # conflicto real que hay que confirmar explícitamente).
    rescue_result = checkpoints.restore(rescue_id, force=True)
    assert rescue_result["restored"] == ["compartido.py"]
    assert target.read_text(encoding="utf-8") == "edicion manual valiosa\n"


def test_restore_without_seal_never_reports_conflict(tmp_path: Path):
    """Decisión documentada: un checkpoint sin sellar (turno todavía en
    curso, o interrumpido antes de sellar) nunca bloquea la restauración.
    Sin un hash "después" de referencia no hay forma de distinguir "esto lo
    dejó el agente" de "esto lo tocó una persona", y bloquear por defecto
    ahí rompería el caso de uso principal: deshacer un turno recién
    terminado sin tener que pasar ``force=True`` cada vez."""

    checkpoints, workspaces, project = _make_store(tmp_path)
    workspace_id = workspaces.list()[0]["id"]
    target = project / "interrumpido.py"
    target.write_text("estado original\n", encoding="utf-8")

    checkpoint = checkpoints.create(workspace_id)
    checkpoints.track(checkpoint["id"], "interrumpido.py")
    # Nunca se llama seal(): el turno "se interrumpió" aquí.
    target.write_text("cambio sin sellar, origen desconocido\n", encoding="utf-8")

    result = checkpoints.restore(checkpoint["id"])

    assert result["conflicts"] == []
    assert result["restored"] == ["interrumpido.py"]
    assert target.read_text(encoding="utf-8") == "estado original\n"
    assert result["rescue_checkpoint_id"] is None


def test_seal_matching_after_state_restores_without_conflict(tmp_path: Path):
    """Camino feliz: nadie tocó el archivo tras el sello del agente, así que
    restaurar no debería reportarlo como conflicto ni pedir ``force``."""

    checkpoints, workspaces, project = _make_store(tmp_path)
    workspace_id = workspaces.list()[0]["id"]
    target = project / "tranquilo.py"
    target.write_text("original\n", encoding="utf-8")

    checkpoint = checkpoints.create(workspace_id)
    checkpoints.track(checkpoint["id"], "tranquilo.py")
    target.write_text("lo del agente\n", encoding="utf-8")
    checkpoints.seal(checkpoint["id"])
    # Nadie más lo toca antes de deshacer.

    result = checkpoints.restore(checkpoint["id"])

    assert result["conflicts"] == []
    assert result["restored"] == ["tranquilo.py"]
    assert target.read_text(encoding="utf-8") == "original\n"


def test_track_after_seal_raises(tmp_path: Path):
    checkpoints, workspaces, project = _make_store(tmp_path)
    workspace_id = workspaces.list()[0]["id"]
    (project / "a.py").write_text("a\n", encoding="utf-8")
    checkpoint = checkpoints.create(workspace_id)
    checkpoints.track(checkpoint["id"], "a.py")
    checkpoints.seal(checkpoint["id"])

    with pytest.raises(IDECheckpointError):
        checkpoints.track(checkpoint["id"], "a.py")


# --------------------------------------------------------------------- #
# Expiración / límites de disco.
# --------------------------------------------------------------------- #


def test_prune_expired_removes_old_checkpoints_and_frees_orphan_blobs(tmp_path: Path):
    checkpoints, workspaces, project = _make_store(tmp_path)
    workspace_id = workspaces.list()[0]["id"]
    target = project / "vencido.py"
    target.write_text("contenido\n", encoding="utf-8")

    checkpoint = checkpoints.create(workspace_id)
    checkpoints.track(checkpoint["id"], "vencido.py")
    assert any(c["id"] == checkpoint["id"] for c in checkpoints.list(workspace_id))

    blob_files_before = list(checkpoints.blobs_dir.rglob("*"))
    assert any(p.is_file() for p in blob_files_before)

    # En vez de una fecha "ya vencida" al crear (carrera contra el reloj: con
    # ttl_hours=0 el checkpoint podría verse expirado incluso antes de la
    # aserción de arriba), se edita el manifiesto directamente para fijar
    # una expiración determinística en el pasado.
    manifest_path = checkpoints._manifest_path(checkpoint["id"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["expires_at_us"] = 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = checkpoints.prune_expired()

    assert result["checkpoints_removed"] == 1
    assert checkpoints.list(workspace_id) == []
    blob_files_after = [p for p in checkpoints.blobs_dir.rglob("*") if p.is_file()]
    assert blob_files_after == []


def test_prune_expired_keeps_blobs_still_referenced_by_live_checkpoints(tmp_path: Path):
    checkpoints, workspaces, project = _make_store(tmp_path)
    workspace_id = workspaces.list()[0]["id"]
    (project / "vivo.py").write_text("contenido compartido\n", encoding="utf-8")

    live = checkpoints.create(workspace_id)
    checkpoints.track(live["id"], "vivo.py")

    result = checkpoints.prune_expired()

    assert result["checkpoints_removed"] == 0
    blob_files = [p for p in checkpoints.blobs_dir.rglob("*") if p.is_file()]
    assert len(blob_files) == 1


def test_workspace_cap_evicts_oldest_checkpoint(tmp_path: Path):
    checkpoints, workspaces, project = _make_store(tmp_path, max_checkpoints_per_workspace=2)
    workspace_id = workspaces.list()[0]["id"]

    first = checkpoints.create(workspace_id, label="primero")
    second = checkpoints.create(workspace_id, label="segundo")
    third = checkpoints.create(workspace_id, label="tercero")

    ids = {row["id"] for row in checkpoints.list(workspace_id)}
    assert first["id"] not in ids
    assert second["id"] in ids
    assert third["id"] in ids


# --------------------------------------------------------------------- #
# Listado / seguridad de rutas.
# --------------------------------------------------------------------- #


def test_list_filters_by_workspace(tmp_path: Path):
    checkpoints, workspaces, project = _make_store(tmp_path)
    workspace_id = workspaces.list()[0]["id"]
    other_project = tmp_path / "otro"
    other_project.mkdir()
    other_workspace = workspaces.authorize(str(other_project))["id"]

    cp_a = checkpoints.create(workspace_id)
    cp_b = checkpoints.create(other_workspace)

    ids_a = {row["id"] for row in checkpoints.list(workspace_id)}
    ids_b = {row["id"] for row in checkpoints.list(other_workspace)}
    assert ids_a == {cp_a["id"]}
    assert ids_b == {cp_b["id"]}


def test_track_rejects_path_traversal_outside_workspace(tmp_path: Path):
    checkpoints, workspaces, project = _make_store(tmp_path)
    workspace_id = workspaces.list()[0]["id"]
    checkpoint = checkpoints.create(workspace_id)

    with pytest.raises(IDECheckpointError):
        checkpoints.track(checkpoint["id"], "../fuera_del_workspace.py")


def test_dedup_shares_blob_across_checkpoints(tmp_path: Path):
    checkpoints, workspaces, project = _make_store(tmp_path)
    workspace_id = workspaces.list()[0]["id"]
    (project / "a.py").write_text("mismo contenido\n", encoding="utf-8")
    (project / "b.py").write_text("mismo contenido\n", encoding="utf-8")

    checkpoint = checkpoints.create(workspace_id)
    checkpoints.track(checkpoint["id"], "a.py")
    checkpoints.track(checkpoint["id"], "b.py")

    blob_files = [p for p in checkpoints.blobs_dir.rglob("*") if p.is_file()]
    # Mismo contenido -> mismo digest -> un solo blob en disco para ambos.
    assert len(blob_files) == 1
