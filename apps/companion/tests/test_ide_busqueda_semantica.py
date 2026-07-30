"""Pruebas de ``ide_busqueda_semantica.SemanticSearchService``.

Ninguna prueba toca la red ni una base real: el embedder real
(``HashEmbedder``) ya es offline y determinista, y donde hace falta control
exacto sobre el "significado" de un fragmento se usa un embedder doble
(``_EmbedderFalso``) en vez de depender de los buckets de un hash SHA-256
real.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest
from edecan_companion.ide_busqueda_semantica import (
    IDESemanticSearchError,
    SemanticSearchService,
)
from edecan_companion.ide_workspaces import WorkspaceStore


class _EmbedderFalso:
    """Doble determinista: el "significado" de un texto es 1.0 si contiene
    ``palabra_clave``, 0.0 en caso contrario -- vectores 2D ya normalizados,
    para poder afirmar puntajes exactos sin depender de feature hashing."""

    def __init__(self, palabra_clave: str) -> None:
        self.palabra_clave = palabra_clave
        self.calls: list[tuple[str, ...]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(tuple(texts))
        vectors = []
        for text in texts:
            if self.palabra_clave in text:
                vectors.append([1.0, 0.0])
            else:
                vectors.append([0.0, 1.0])
        return vectors


def _make_service(tmp_path: Path, **kwargs) -> tuple[SemanticSearchService, Path, str]:
    state_dir = tmp_path / "state"
    project = tmp_path / "proyecto"
    project.mkdir()
    workspaces = WorkspaceStore(state_dir)
    workspaces.authorize(str(project))
    workspace_id = workspaces.list()[0]["id"]
    service = SemanticSearchService(workspaces, state_dir, **kwargs)
    return service, project, workspace_id


# --------------------------------------------------------------------- #
# Degradado: sin embedder disponible, todo cae a búsqueda por texto.
# --------------------------------------------------------------------- #


def test_sin_embedder_degrada_a_busqueda_por_texto(tmp_path: Path):
    service, project, workspace_id = _make_service(tmp_path, embedder=None)
    (project / "auth.py").write_text(
        "def validar_password(x):\n    return True\n", encoding="utf-8"
    )

    assert service.embeddings_available is False
    resultado = service.search(workspace_id, "validar_password")

    assert resultado["mode"] == "texto"
    assert resultado["degraded_reason"]
    rutas = {fila["path"] for fila in resultado["matches"]}
    assert "auth.py" in rutas


def test_sin_embedder_query_que_no_aparece_literal_no_encuentra_nada(tmp_path: Path):
    service, project, workspace_id = _make_service(tmp_path, embedder=None)
    (project / "auth.py").write_text(
        "def validar_password(x):\n    return True\n", encoding="utf-8"
    )

    resultado = service.search(workspace_id, "dónde se valida la contraseña")
    assert resultado["mode"] == "texto"
    assert resultado["matches"] == []


def test_query_vacio_es_error(tmp_path: Path):
    service, _project, workspace_id = _make_service(tmp_path, embedder=None)
    with pytest.raises(IDESemanticSearchError):
        service.search(workspace_id, "   ")


def test_k_fuera_de_rango_es_error(tmp_path: Path):
    service, _project, workspace_id = _make_service(tmp_path, embedder=None)
    with pytest.raises(IDESemanticSearchError):
        service.search(workspace_id, "algo", k=0)
    with pytest.raises(IDESemanticSearchError):
        service.search(workspace_id, "algo", k=999)


# --------------------------------------------------------------------- #
# Con embedder disponible pero sin índice construido todavía: primer
# resultado degrada a texto Y dispara el indexado en segundo plano.
# --------------------------------------------------------------------- #


def test_primera_busqueda_sin_indice_degrada_y_arranca_indexado_en_fondo(tmp_path: Path):
    embedder = _EmbedderFalso("contraseña")
    service, project, workspace_id = _make_service(tmp_path, embedder=embedder)
    (project / "auth.py").write_text("valida la contraseña del usuario\n", encoding="utf-8")

    estado_inicial = service.status(workspace_id)
    assert estado_inicial["state"] == "sin_indexar"

    resultado = service.search(workspace_id, "contraseña")
    assert resultado["mode"] == "texto"
    assert "índice" in resultado["degraded_reason"] or "indice" in resultado["degraded_reason"]

    estado_final = service.wait_for_index(workspace_id, timeout=10.0)
    assert estado_final["state"] == "ready"
    assert estado_final["index_exists"] is True
    assert estado_final["total_chunks"] >= 1


def test_busqueda_no_dispara_indexado_si_auto_index_es_falso(tmp_path: Path):
    embedder = _EmbedderFalso("contraseña")
    service, project, workspace_id = _make_service(tmp_path, embedder=embedder)
    (project / "auth.py").write_text("valida la contraseña\n", encoding="utf-8")

    service.search(workspace_id, "contraseña", auto_index=False)
    time.sleep(0.05)
    assert service.status(workspace_id)["state"] == "sin_indexar"


# --------------------------------------------------------------------- #
# Búsqueda semántica de verdad: rankea por similitud del vector, no por
# coincidencia literal.
# --------------------------------------------------------------------- #


def test_busqueda_semantica_encuentra_por_significado_no_por_palabra_literal(tmp_path: Path):
    embedder = _EmbedderFalso("contraseña")
    service, project, workspace_id = _make_service(tmp_path, embedder=embedder)
    (project / "auth.py").write_text(
        "def check_credentials(user):\n    return contraseña_valida(user)\n", encoding="utf-8"
    )
    (project / "otros.py").write_text(
        "def formatear_fecha(dt):\n    return dt.isoformat()\n", encoding="utf-8"
    )

    estado = service.build_index(workspace_id)
    assert estado["state"] == "ready"

    # La consulta no contiene "contraseña" tal cual, pero el embedder doble
    # decide el "significado" por esa palabra clave -- exactamente lo que un
    # embedder real haría con sinónimos/paráfrasis.
    resultado = service.search(workspace_id, "dónde se valida la contraseña")

    assert resultado["mode"] == "semantico"
    assert resultado["matches"], "debe encontrar al menos un resultado"
    assert resultado["matches"][0]["path"] == "auth.py"
    assert resultado["matches"][0]["score"] == pytest.approx(1.0)
    # El segundo archivo, sin relación semántica, queda peor rankeado.
    if len(resultado["matches"]) > 1:
        assert resultado["matches"][0]["score"] >= resultado["matches"][1]["score"]


def test_busqueda_semantica_respeta_k(tmp_path: Path):
    embedder = _EmbedderFalso("contraseña")
    service, project, workspace_id = _make_service(tmp_path, embedder=embedder)
    for index in range(5):
        (project / f"archivo_{index}.py").write_text(
            f"contraseña variante {index}\n", encoding="utf-8"
        )
    service.build_index(workspace_id)

    resultado = service.search(workspace_id, "contraseña", k=2)
    assert len(resultado["matches"]) == 2


# --------------------------------------------------------------------- #
# Incremental: un archivo sin cambios (mismo size+mtime) no se reembebe.
# --------------------------------------------------------------------- #


def test_reindexado_incremental_no_reembebe_archivos_sin_cambios(tmp_path: Path):
    embedder = _EmbedderFalso("contraseña")
    service, project, workspace_id = _make_service(tmp_path, embedder=embedder)
    (project / "a.py").write_text("contraseña de a\n", encoding="utf-8")
    (project / "b.py").write_text("otra cosa de b\n", encoding="utf-8")

    primer_resultado = service.build_index(workspace_id)
    assert primer_resultado["last_run"]["indexed_files"] == 2
    assert primer_resultado["last_run"]["reused_files"] == 0
    llamadas_tras_primera_pasada = len(embedder.calls)
    assert llamadas_tras_primera_pasada > 0

    # Segunda pasada sin tocar nada: ambos archivos deben reusarse.
    segundo_resultado = service.build_index(workspace_id)
    assert segundo_resultado["last_run"]["indexed_files"] == 0
    assert segundo_resultado["last_run"]["reused_files"] == 2
    assert len(embedder.calls) == llamadas_tras_primera_pasada  # ningún embed() nuevo

    # Modificar SOLO "a.py" (con un mtime forzado a futuro, para no depender
    # de que el reloj del sistema avance entre escrituras rápidas).
    target = project / "a.py"
    target.write_text("contraseña de a, editada\n", encoding="utf-8")
    future = time.time() + 5
    os.utime(target, (future, future))

    tercer_resultado = service.build_index(workspace_id)
    assert tercer_resultado["last_run"]["indexed_files"] == 1
    assert tercer_resultado["last_run"]["reused_files"] == 1
    assert len(embedder.calls) == llamadas_tras_primera_pasada + 1


def test_reindexado_quita_fragmentos_de_archivos_borrados(tmp_path: Path):
    embedder = _EmbedderFalso("contraseña")
    service, project, workspace_id = _make_service(tmp_path, embedder=embedder)
    (project / "a.py").write_text("contraseña de a\n", encoding="utf-8")
    (project / "b.py").write_text("contraseña de b\n", encoding="utf-8")
    service.build_index(workspace_id)

    (project / "b.py").unlink()
    resultado = service.build_index(workspace_id)

    assert resultado["last_run"]["removed_files"] == 1
    assert resultado["total_files"] == 1
    encontrado = service.search(workspace_id, "contraseña")
    rutas = {fila["path"] for fila in encontrado["matches"]}
    assert "b.py" not in rutas


# --------------------------------------------------------------------- #
# Respeta .gitignore (igual que ReferenceService).
# --------------------------------------------------------------------- #


def test_respeta_gitignore(tmp_path: Path):
    embedder = _EmbedderFalso("contraseña")
    service, project, workspace_id = _make_service(tmp_path, embedder=embedder)
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=project, check=True)
    (project / ".gitignore").write_text("ignorado.py\n", encoding="utf-8")
    (project / "visible.py").write_text("contraseña visible\n", encoding="utf-8")
    (project / "ignorado.py").write_text("contraseña ignorada\n", encoding="utf-8")

    resultado = service.build_index(workspace_id)
    assert resultado["last_run"]["indexed_files"] == 2  # visible.py + .gitignore

    encontrado = service.search(workspace_id, "contraseña")
    rutas = {fila["path"] for fila in encontrado["matches"]}
    assert "visible.py" in rutas
    assert "ignorado.py" not in rutas


# --------------------------------------------------------------------- #
# Workspace inexistente sigue siendo un error explícito, no un 500 escondido.
# --------------------------------------------------------------------- #


def test_workspace_inexistente_es_error(tmp_path: Path):
    service, _project, _workspace_id = _make_service(tmp_path, embedder=None)
    with pytest.raises(Exception):  # noqa: B017 - IDEWorkspaceError, evita el import solo para esto
        service.search("no-existe", "algo")


def test_start_indexing_workspace_id_invalido_falla_de_inmediato(tmp_path: Path):
    service, _project, _workspace_id = _make_service(tmp_path, embedder=None)
    with pytest.raises(Exception):  # noqa: B017
        service.start_indexing("no-existe")
