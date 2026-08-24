"""Pruebas de ``ide_acciones_codigo``: /diff, /simplify, /review,
/security-review e /init -- la capa que conecta cada comando con su
capacidad real (``ide_checkpoints``, ``ide_equipo``, ``ide_git``,
``edecan_toolkit.seguridad`` e ``ide_reglas``), con dobles donde hace falta
evitar red o dependencias pesadas reales.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from edecan_companion.ide_acciones_codigo import (
    IDEAccionesCodigoError,
    analizar_proyecto,
    auditar_seguridad,
    diff_de_turno,
    escribir_agents_md,
    generar_agents_md,
    preparar_revision,
    preparar_simplificacion,
)
from edecan_companion.ide_checkpoints import CheckpointStore
from edecan_companion.ide_git import GitService
from edecan_companion.ide_workspaces import WorkspaceStore


def _make_checkpoints(tmp_path: Path) -> tuple[CheckpointStore, WorkspaceStore, Path, str]:
    state_dir = tmp_path / "state"
    project = tmp_path / "proyecto"
    project.mkdir()
    workspaces = WorkspaceStore(state_dir)
    workspaces.authorize(str(project))
    checkpoints = CheckpointStore(state_dir, workspaces)
    workspace_id = workspaces.list()[0]["id"]
    return checkpoints, workspaces, project, workspace_id


# --------------------------------------------------------------------------- #
# /diff
# --------------------------------------------------------------------------- #


def test_diff_archivo_modificado_incluye_texto_unificado(tmp_path: Path):
    checkpoints, _workspaces, project, workspace_id = _make_checkpoints(tmp_path)
    target = project / "main.py"
    target.write_text("version 1\n", encoding="utf-8")

    checkpoint = checkpoints.create(workspace_id)
    checkpoints.track(checkpoint["id"], "main.py")
    target.write_text("version 2\n", encoding="utf-8")

    resultado = diff_de_turno(checkpoints, checkpoint["id"])

    assert len(resultado.archivos) == 1
    archivo = resultado.archivos[0]
    assert archivo.path == "main.py"
    assert archivo.status == "modificado"
    assert "-version 1" in archivo.diff_texto
    assert "+version 2" in archivo.diff_texto
    assert "1 archivo(s)" in resultado.resumen()


def test_diff_archivo_nuevo_creado_por_el_agente(tmp_path: Path):
    checkpoints, _workspaces, project, workspace_id = _make_checkpoints(tmp_path)
    checkpoint = checkpoints.create(workspace_id)
    checkpoints.track(checkpoint["id"], "nuevo.py")  # no existía todavía
    (project / "nuevo.py").write_text("contenido\n", encoding="utf-8")

    resultado = diff_de_turno(checkpoints, checkpoint["id"])

    assert resultado.archivos[0].status == "nuevo"
    assert "+contenido" in resultado.archivos[0].diff_texto


def test_diff_archivo_eliminado_por_el_agente(tmp_path: Path):
    checkpoints, _workspaces, project, workspace_id = _make_checkpoints(tmp_path)
    (project / "borrar.py").write_text("adios\n", encoding="utf-8")
    checkpoint = checkpoints.create(workspace_id)
    checkpoints.track(checkpoint["id"], "borrar.py")
    (project / "borrar.py").unlink()

    resultado = diff_de_turno(checkpoints, checkpoint["id"])

    assert resultado.archivos[0].status == "eliminado"
    assert "-adios" in resultado.archivos[0].diff_texto


def test_diff_archivo_sin_cambios_no_trae_texto(tmp_path: Path):
    checkpoints, _workspaces, project, workspace_id = _make_checkpoints(tmp_path)
    (project / "igual.py").write_text("sin cambios\n", encoding="utf-8")
    checkpoint = checkpoints.create(workspace_id)
    checkpoints.track(checkpoint["id"], "igual.py")

    resultado = diff_de_turno(checkpoints, checkpoint["id"])

    assert resultado.archivos[0].status == "sin_cambios"
    assert resultado.archivos[0].diff_texto is None


def test_diff_archivo_omitido_por_tamano_es_no_restaurable(tmp_path: Path):
    checkpoints, _workspaces, project, workspace_id = _make_checkpoints(
        tmp_path
    )
    checkpoints.max_tracked_file_bytes = 10
    (project / "grande.py").write_text("x" * 1000, encoding="utf-8")
    checkpoint = checkpoints.create(workspace_id)
    checkpoints.track(checkpoint["id"], "grande.py")

    resultado = diff_de_turno(checkpoints, checkpoint["id"])

    assert resultado.archivos[0].status == "no_restaurable"
    assert resultado.archivos[0].diff_texto is None


def test_diff_checkpoint_inexistente_lanza_error_propio(tmp_path: Path):
    checkpoints, *_ = _make_checkpoints(tmp_path)
    with pytest.raises(IDEAccionesCodigoError):
        diff_de_turno(checkpoints, "no-existe")


def test_diff_turno_sin_archivos_da_resumen_vacio(tmp_path: Path):
    checkpoints, _workspaces, _project, workspace_id = _make_checkpoints(tmp_path)
    checkpoint = checkpoints.create(workspace_id)

    resultado = diff_de_turno(checkpoints, checkpoint["id"])

    assert resultado.archivos == ()
    assert "no registró ningún archivo" in resultado.resumen()


# --------------------------------------------------------------------------- #
# /simplify
# --------------------------------------------------------------------------- #


def test_simplify_arma_plan_con_las_rutas_del_checkpoint(tmp_path: Path):
    checkpoints, _workspaces, project, workspace_id = _make_checkpoints(tmp_path)
    (project / "a.py").write_text("a", encoding="utf-8")
    (project / "b.py").write_text("b", encoding="utf-8")
    checkpoint = checkpoints.create(workspace_id)
    checkpoints.track(checkpoint["id"], "a.py")
    checkpoints.track(checkpoint["id"], "b.py")

    plan = preparar_simplificacion(checkpoints, checkpoint["id"])

    assert len(plan.subtareas) == 1
    subtarea = plan.subtareas[0]
    assert set(subtarea.rutas) == {"a.py", "b.py"}
    assert "Revisa SOLO los archivos listados" in subtarea.instrucciones


def test_simplify_acepta_instrucciones_personalizadas(tmp_path: Path):
    checkpoints, _workspaces, project, workspace_id = _make_checkpoints(tmp_path)
    (project / "a.py").write_text("a", encoding="utf-8")
    checkpoint = checkpoints.create(workspace_id)
    checkpoints.track(checkpoint["id"], "a.py")

    plan = preparar_simplificacion(checkpoints, checkpoint["id"], instrucciones="hazlo distinto")

    assert plan.subtareas[0].instrucciones == "hazlo distinto"


def test_simplify_checkpoint_vacio_no_tiene_nada_que_simplificar(tmp_path: Path):
    checkpoints, _workspaces, _project, workspace_id = _make_checkpoints(tmp_path)
    checkpoint = checkpoints.create(workspace_id)

    with pytest.raises(IDEAccionesCodigoError, match="no hay nada que simplificar"):
        preparar_simplificacion(checkpoints, checkpoint["id"])


def test_simplify_checkpoint_inexistente_lanza_error_propio(tmp_path: Path):
    checkpoints, *_ = _make_checkpoints(tmp_path)
    with pytest.raises(IDEAccionesCodigoError):
        preparar_simplificacion(checkpoints, "no-existe")


# --------------------------------------------------------------------------- #
# /review
# --------------------------------------------------------------------------- #


def _init_git_repo(project: Path) -> None:
    subprocess.run(["git", "init", "-q", str(project)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(project), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(project), "config", "user.name", "Test"], check=True)
    (project / "README.md").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(project), "add", "."], check=True)
    subprocess.run(["git", "-C", str(project), "commit", "-q", "-m", "inicial"], check=True)


def test_review_es_siempre_de_solo_lectura_y_trae_el_diff(tmp_path: Path):
    project = tmp_path / "proyecto"
    project.mkdir()
    _init_git_repo(project)
    (project / "README.md").write_text("v2\n", encoding="utf-8")

    workspaces = WorkspaceStore(tmp_path / "state")
    workspaces.authorize(str(project))
    workspace_id = workspaces.list()[0]["id"]
    git = GitService(workspaces)

    revision = preparar_revision(git, workspace_id)

    assert revision.solo_lectura is True
    assert "-v1" in revision.diff_texto
    assert "+v2" in revision.diff_texto
    bloque = revision.as_prompt_block()
    assert "SOLO LECTURA" in bloque
    assert "<diff_pendiente>" in bloque


def test_review_sin_cambios_pendientes_es_error_propio(tmp_path: Path):
    project = tmp_path / "proyecto"
    project.mkdir()
    _init_git_repo(project)

    workspaces = WorkspaceStore(tmp_path / "state")
    workspaces.authorize(str(project))
    workspace_id = workspaces.list()[0]["id"]
    git = GitService(workspaces)

    with pytest.raises(IDEAccionesCodigoError, match="No hay cambios pendientes"):
        preparar_revision(git, workspace_id)


# --------------------------------------------------------------------------- #
# /security-review
# --------------------------------------------------------------------------- #


async def test_security_review_conecta_con_la_tool_real_y_encuentra_secreto(tmp_path: Path):
    project = tmp_path / "proyecto"
    project.mkdir()
    (project / "config.py").write_text('API_KEY = "abcd1234efgh5678ijkl"\n', encoding="utf-8")

    workspaces = WorkspaceStore(tmp_path / "state")
    workspaces.authorize(str(project))
    workspace_id = workspaces.list()[0]["id"]

    resultado = await auditar_seguridad(workspaces, workspace_id)

    assert resultado["data"]["summary"]["findings"] >= 1
    assert "abcd1234efgh5678ijkl" not in resultado["content"]
    assert "hallazgos" in resultado["content"]


async def test_security_review_workspace_invalido_es_error_propio(tmp_path: Path):
    workspaces = WorkspaceStore(tmp_path / "state")
    with pytest.raises(IDEAccionesCodigoError):
        await auditar_seguridad(workspaces, "no-existe")


# --------------------------------------------------------------------------- #
# /init
# --------------------------------------------------------------------------- #


def test_analizar_proyecto_detecta_python_con_pytest_y_ruff(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'x'\n[dependency-groups]\ndev = ['pytest', 'ruff']\n",
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    (tmp_path / "src").mkdir()

    analisis = analizar_proyecto(tmp_path)

    assert "Python" in analisis.lenguajes
    assert analisis.gestor_paquetes == "uv"
    assert analisis.comando_tests == "pytest -q"
    assert analisis.comando_lint == "ruff check ."
    assert "src" in analisis.carpetas_principales


def test_analizar_proyecto_detecta_node_con_scripts_de_package_json(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        '{"scripts": {"test": "vitest", "lint": "eslint ."}}', encoding="utf-8"
    )
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")

    analisis = analizar_proyecto(tmp_path)

    assert "Node/JavaScript o TypeScript" in analisis.lenguajes
    assert analisis.gestor_paquetes == "pnpm"
    assert analisis.comando_tests == "pnpm run test"
    assert analisis.comando_lint == "pnpm run lint"


def test_analizar_proyecto_sin_evidencia_no_inventa_nada(tmp_path: Path):
    analisis = analizar_proyecto(tmp_path)

    assert analisis.lenguajes == ("No identificado automáticamente",)
    assert analisis.gestor_paquetes is None
    assert analisis.comando_tests is None
    assert analisis.comando_lint is None


def test_generar_agents_md_incluye_las_secciones_clave(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    analisis = analizar_proyecto(tmp_path)

    contenido = generar_agents_md(analisis)

    assert "# AGENTS.md" in contenido
    assert "## Cómo correr los tests" in contenido
    assert "## Estructura del proyecto" in contenido


def test_escribir_agents_md_crea_el_archivo_si_no_hay_reglas_previas(tmp_path: Path):
    project = tmp_path / "proyecto"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    workspaces = WorkspaceStore(tmp_path / "state")
    workspaces.authorize(str(project))
    workspace_id = workspaces.list()[0]["id"]

    resultado = escribir_agents_md(workspaces, workspace_id)

    assert resultado["escrito"] is True
    assert (project / "AGENTS.md").is_file()
    assert "Python" in resultado["lenguajes"]


def test_escribir_agents_md_no_pisa_reglas_existentes_sin_overwrite(tmp_path: Path):
    project = tmp_path / "proyecto"
    project.mkdir()
    (project / "AGENTS.md").write_text("reglas manuales\n", encoding="utf-8")
    workspaces = WorkspaceStore(tmp_path / "state")
    workspaces.authorize(str(project))
    workspace_id = workspaces.list()[0]["id"]

    resultado = escribir_agents_md(workspaces, workspace_id)

    assert resultado["escrito"] is False
    assert resultado["archivo_existente"] == "AGENTS.md"
    assert (project / "AGENTS.md").read_text(encoding="utf-8") == "reglas manuales\n"


def test_escribir_agents_md_con_overwrite_si_pisa(tmp_path: Path):
    project = tmp_path / "proyecto"
    project.mkdir()
    (project / "AGENTS.md").write_text("reglas manuales\n", encoding="utf-8")
    workspaces = WorkspaceStore(tmp_path / "state")
    workspaces.authorize(str(project))
    workspace_id = workspaces.list()[0]["id"]

    resultado = escribir_agents_md(workspaces, workspace_id, overwrite=True)

    assert resultado["escrito"] is True
    assert "reglas manuales" not in (project / "AGENTS.md").read_text(encoding="utf-8")


def test_init_workspace_invalido_es_error_propio(tmp_path: Path):
    workspaces = WorkspaceStore(tmp_path / "state")
    with pytest.raises(IDEAccionesCodigoError):
        escribir_agents_md(workspaces, "no-existe")
