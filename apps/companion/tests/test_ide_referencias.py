"""Pruebas de ``ide_referencias.ReferenceService``: prefijo que casa varios,
carpeta, símbolo, repo grande dentro del tope, e intento de salir del
workspace (symlink que escapa)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from edecan_companion.ide_referencias import IDEReferenceError, ReferenceService
from edecan_companion.ide_workspaces import IDEWorkspaceError, WorkspaceStore


def _make_service(tmp_path: Path, **kwargs) -> tuple[ReferenceService, WorkspaceStore, Path, str]:
    state_dir = tmp_path / "state"
    project = tmp_path / "proyecto"
    project.mkdir()
    workspaces = WorkspaceStore(state_dir)
    workspaces.authorize(str(project))
    workspace_id = workspaces.list()[0]["id"]
    service = ReferenceService(workspaces, **kwargs)
    return service, workspaces, project, workspace_id


# --------------------------------------------------------------------- #
# Prefijo que casa varios archivos.
# --------------------------------------------------------------------- #


def test_prefijo_casa_varios_archivos_por_nombre(tmp_path: Path):
    service, _workspaces, project, workspace_id = _make_service(tmp_path)
    (project / "componente_boton.tsx").write_text("export const x = 1;\n", encoding="utf-8")
    (project / "componente_tarjeta.tsx").write_text("export const y = 1;\n", encoding="utf-8")
    (project / "otra_cosa.py").write_text("x = 1\n", encoding="utf-8")

    resultado = service.search(workspace_id, "compo", kinds=("file",))

    rutas = {fila["path"] for fila in resultado["matches"]}
    assert rutas == {"componente_boton.tsx", "componente_tarjeta.tsx"}
    assert all(fila["type"] == "file" for fila in resultado["matches"])
    assert resultado["index_truncated"] is False


def test_sin_prefijo_devuelve_todos_los_archivos_y_carpetas(tmp_path: Path):
    service, _workspaces, project, workspace_id = _make_service(tmp_path)
    (project / "a.py").write_text("a = 1\n", encoding="utf-8")
    carpeta = project / "sub"
    carpeta.mkdir()
    (carpeta / "b.py").write_text("b = 1\n", encoding="utf-8")

    resultado = service.search(workspace_id, "")

    rutas = {(fila["type"], fila["path"]) for fila in resultado["matches"]}
    assert ("file", "a.py") in rutas
    assert ("file", "sub/b.py") in rutas
    assert ("folder", "sub") in rutas
    # Sin prefijo no se listan símbolos: sería ruido en un proyecto real.
    assert not any(fila["type"] == "symbol" for fila in resultado["matches"])


# --------------------------------------------------------------------- #
# Carpeta.
# --------------------------------------------------------------------- #


def test_encuentra_carpeta_anidada_por_prefijo(tmp_path: Path):
    service, _workspaces, project, workspace_id = _make_service(tmp_path)
    anidada = project / "apps" / "companion"
    anidada.mkdir(parents=True)
    (anidada / "modulo.py").write_text("pass\n", encoding="utf-8")

    resultado = service.search(workspace_id, "compan", kinds=("folder",))

    rutas = [fila["path"] for fila in resultado["matches"]]
    assert "apps/companion" in rutas
    fila = next(f for f in resultado["matches"] if f["path"] == "apps/companion")
    assert fila["type"] == "folder"
    assert fila["name"] == "companion"


# --------------------------------------------------------------------- #
# Símbolo.
# --------------------------------------------------------------------- #


def test_encuentra_funcion_y_clase_python_por_prefijo(tmp_path: Path):
    service, _workspaces, project, workspace_id = _make_service(tmp_path)
    (project / "servicio.py").write_text(
        "def procesar_pago(monto):\n"
        "    return monto\n"
        "\n"
        "class ProcesadorDePagos:\n"
        "    pass\n",
        encoding="utf-8",
    )

    resultado = service.search(workspace_id, "procesa", kinds=("symbol",))

    nombres = {(fila["name"], fila["symbol_kind"]) for fila in resultado["matches"]}
    assert ("procesar_pago", "function") in nombres
    assert ("ProcesadorDePagos", "class") in nombres
    fila_funcion = next(f for f in resultado["matches"] if f["name"] == "procesar_pago")
    assert fila_funcion["path"] == "servicio.py"
    assert fila_funcion["line"] == 1


def test_encuentra_funcion_flecha_y_clase_typescript(tmp_path: Path):
    service, _workspaces, project, workspace_id = _make_service(tmp_path)
    (project / "utilidades.ts").write_text(
        "export const calcularTotal = (items) => items.length;\n"
        "export class ServicioDeFacturas {}\n",
        encoding="utf-8",
    )

    resultado = service.search(workspace_id, "calcular", kinds=("symbol",))
    nombres_funcion = {f["name"] for f in resultado["matches"] if f["symbol_kind"] == "function"}
    assert "calcularTotal" in nombres_funcion

    resultado_clase = service.search(workspace_id, "facturas", kinds=("symbol",))
    nombres_clase = {f["name"] for f in resultado_clase["matches"] if f["symbol_kind"] == "class"}
    assert "ServicioDeFacturas" in nombres_clase


def test_simbolo_vacio_no_se_evalua_sin_prefijo(tmp_path: Path):
    service, _workspaces, project, workspace_id = _make_service(tmp_path)
    (project / "modulo.py").write_text("def f():\n    pass\n", encoding="utf-8")

    resultado = service.search(workspace_id, "", kinds=("symbol",))

    assert resultado["matches"] == []


# --------------------------------------------------------------------- #
# Orden: recientes primero, luego más cerca de la raíz.
# --------------------------------------------------------------------- #


def test_recientes_y_cercania_a_la_raiz_ordenan_resultados(tmp_path: Path):
    service, _workspaces, project, workspace_id = _make_service(tmp_path)
    (project / "config.py").write_text("x = 1\n", encoding="utf-8")
    hondo = project / "a" / "b" / "c"
    hondo.mkdir(parents=True)
    (hondo / "config.py").write_text("y = 1\n", encoding="utf-8")

    sin_recientes = service.search(workspace_id, "config", kinds=("file",))
    assert sin_recientes["matches"][0]["path"] == "config.py"

    con_recientes = service.search(
        workspace_id, "config", kinds=("file",), recently_opened=["a/b/c/config.py"]
    )
    assert con_recientes["matches"][0]["path"] == "a/b/c/config.py"


# --------------------------------------------------------------------- #
# Repo grande dentro del tope (repos enormes deben responder rápido).
# --------------------------------------------------------------------- #


def test_repo_grande_se_trunca_en_el_tope_configurado(tmp_path: Path):
    service, _workspaces, project, workspace_id = _make_service(tmp_path, max_indexed_files=5)
    for i in range(10):
        (project / f"archivo_{i}.py").write_text("x = 1\n", encoding="utf-8")

    resultado = service.search(workspace_id, "archivo")

    assert resultado["index_truncated"] is True
    # El tope aplica sobre archivos indexados, no sobre cuántos matchean.
    assert len({f["path"] for f in resultado["matches"] if f["type"] == "file"}) <= 5


def test_repo_dentro_del_tope_no_se_marca_truncado(tmp_path: Path):
    service, _workspaces, project, workspace_id = _make_service(tmp_path, max_indexed_files=50)
    for i in range(5):
        (project / f"archivo_{i}.py").write_text("x = 1\n", encoding="utf-8")

    resultado = service.search(workspace_id, "archivo")
    assert resultado["index_truncated"] is False


# --------------------------------------------------------------------- #
# Intento de salir del workspace.
# --------------------------------------------------------------------- #


def test_symlink_que_escapa_del_workspace_no_aparece(tmp_path: Path):
    service, _workspaces, project, workspace_id = _make_service(tmp_path)
    afuera = tmp_path / "afuera_del_workspace.py"
    afuera.write_text("secreto = 1\n", encoding="utf-8")
    enlace = project / "enlace_externo.py"
    try:
        enlace.symlink_to(afuera)
    except OSError:
        pytest.skip("El sistema de archivos no soporta symlinks en este entorno.")

    resultado = service.search(workspace_id, "enlace")
    assert resultado["matches"] == []

    resultado_afuera = service.search(workspace_id, "afuera")
    assert resultado_afuera["matches"] == []


def test_workspace_inexistente_lanza_error(tmp_path: Path):
    service, _workspaces, _project, _workspace_id = _make_service(tmp_path)
    with pytest.raises(IDEWorkspaceError):
        service.search("workspace-que-no-existe", "algo")


def test_prefijo_no_texto_lanza_error_de_referencias(tmp_path: Path):
    service, _workspaces, _project, workspace_id = _make_service(tmp_path)
    with pytest.raises(IDEReferenceError):
        service.search(workspace_id, 123)  # type: ignore[arg-type]


def test_limite_fuera_de_rango_lanza_error(tmp_path: Path):
    service, _workspaces, _project, workspace_id = _make_service(tmp_path)
    with pytest.raises(IDEReferenceError):
        service.search(workspace_id, "a", limit=0)


def test_kind_desconocido_lanza_error(tmp_path: Path):
    service, _workspaces, _project, workspace_id = _make_service(tmp_path)
    with pytest.raises(IDEReferenceError):
        service.search(workspace_id, "a", kinds=("archivo",))  # type: ignore[arg-type]


# --------------------------------------------------------------------- #
# Binarios y carpetas ignoradas fuera de resultados.
# --------------------------------------------------------------------- #


def test_binarios_y_carpetas_ignoradas_quedan_fuera(tmp_path: Path):
    service, _workspaces, project, workspace_id = _make_service(tmp_path)
    (project / "imagen.png").write_bytes(b"\x89PNG\r\n")
    node_modules = project / "node_modules"
    node_modules.mkdir()
    (node_modules / "paquete.js").write_text("module.exports = {};\n", encoding="utf-8")

    resultado = service.search(workspace_id, "")
    rutas = {fila["path"] for fila in resultado["matches"]}
    assert "imagen.png" not in rutas
    assert not any(ruta.startswith("node_modules") for ruta in rutas)


# --------------------------------------------------------------------- #
# invalidate() fuerza reconstruir el índice antes del TTL.
# --------------------------------------------------------------------- #


def test_invalidate_hace_visible_un_archivo_nuevo_de_inmediato(tmp_path: Path):
    service, _workspaces, project, workspace_id = _make_service(tmp_path, index_ttl_seconds=999)
    service.search(workspace_id, "")  # construye y cachea el índice vacío-ish

    (project / "recien_creado.py").write_text("x = 1\n", encoding="utf-8")
    resultado_cacheado = service.search(workspace_id, "recien")
    assert resultado_cacheado["matches"] == []

    service.invalidate(workspace_id)
    resultado_fresco = service.search(workspace_id, "recien")
    assert any(f["path"] == "recien_creado.py" for f in resultado_fresco["matches"])


# --------------------------------------------------------------------- #
# Funciona igual si el workspace es un repo git de verdad (usa git ls-files).
# --------------------------------------------------------------------- #


def test_funciona_con_repo_git_y_respeta_gitignore(tmp_path: Path):
    service, _workspaces, project, workspace_id = _make_service(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=project, check=True)
    (project / ".gitignore").write_text("ignorado.py\n", encoding="utf-8")
    (project / "visible.py").write_text("x = 1\n", encoding="utf-8")
    (project / "ignorado.py").write_text("y = 1\n", encoding="utf-8")

    resultado = service.search(workspace_id, "", kinds=("file",))
    rutas = {fila["path"] for fila in resultado["matches"]}
    assert "visible.py" in rutas
    assert "ignorado.py" not in rutas
    assert ".gitignore" in rutas
