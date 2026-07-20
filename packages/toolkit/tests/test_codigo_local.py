"""Tests de `edecan_toolkit.codigo_local.AccederCodigoLocalTool`."""

from __future__ import annotations

from pathlib import Path

import pytest
from edecan_toolkit.codigo_local import AccederCodigoLocalTool


@pytest.fixture
def repo_local(tmp_path: Path) -> Path:
    (tmp_path / "archivo.txt").write_text("hola mundo\nsegunda línea\n", encoding="utf-8")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "otro.txt").write_text("contenido anidado\n", encoding="utf-8")
    return tmp_path


def _settings_local(repo_local: Path, *, modo_local: bool = True) -> object:
    from types import SimpleNamespace

    return SimpleNamespace(
        EDECAN_LOCAL_MODE=modo_local,
        EDECAN_LOCAL_REPO_PATH=str(repo_local),
    )


def test_dangerous_es_true():
    assert AccederCodigoLocalTool().dangerous is True


async def test_sin_edecan_local_mode_devuelve_error(make_ctx, repo_local):
    ctx = make_ctx(settings=_settings_local(repo_local, modo_local=False))
    resultado = await AccederCodigoLocalTool().run(ctx, {"accion": "listar_directorio"})
    assert "no está configurado" in resultado.content.lower()


async def test_sin_repo_path_configurado_devuelve_error(make_ctx):
    from types import SimpleNamespace

    ctx = make_ctx(settings=SimpleNamespace(EDECAN_LOCAL_MODE=True, EDECAN_LOCAL_REPO_PATH=None))
    resultado = await AccederCodigoLocalTool().run(ctx, {"accion": "listar_directorio"})
    assert "no está configurado" in resultado.content.lower()


async def test_accion_desconocida(make_ctx, repo_local):
    ctx = make_ctx(settings=_settings_local(repo_local))
    resultado = await AccederCodigoLocalTool().run(ctx, {"accion": "volar"})
    assert "desconocida" in resultado.content.lower()


async def test_leer_archivo(make_ctx, repo_local):
    ctx = make_ctx(settings=_settings_local(repo_local))
    resultado = await AccederCodigoLocalTool().run(
        ctx, {"accion": "leer_archivo", "ruta": "archivo.txt"}
    )
    assert "hola mundo" in resultado.content


async def test_leer_archivo_inexistente(make_ctx, repo_local):
    ctx = make_ctx(settings=_settings_local(repo_local))
    resultado = await AccederCodigoLocalTool().run(
        ctx, {"accion": "leer_archivo", "ruta": "no-existe.txt"}
    )
    assert "no es un archivo" in resultado.content.lower()


async def test_leer_archivo_escapa_la_raiz_se_rechaza(make_ctx, repo_local):
    ctx = make_ctx(settings=_settings_local(repo_local))
    resultado = await AccederCodigoLocalTool().run(
        ctx, {"accion": "leer_archivo", "ruta": "../../../../etc/passwd"}
    )
    assert "fuera del repo" in resultado.content.lower()


async def test_escribir_archivo_crea_contenido_nuevo(make_ctx, repo_local):
    ctx = make_ctx(settings=_settings_local(repo_local))
    resultado = await AccederCodigoLocalTool().run(
        ctx,
        {"accion": "escribir_archivo", "ruta": "nuevo.txt", "contenido": "contenido de prueba"},
    )
    assert "nuevo.txt" in resultado.content
    assert (repo_local / "nuevo.txt").read_text(encoding="utf-8") == "contenido de prueba"


async def test_escribir_archivo_escapa_la_raiz_se_rechaza(make_ctx, repo_local):
    ctx = make_ctx(settings=_settings_local(repo_local))
    resultado = await AccederCodigoLocalTool().run(
        ctx,
        {"accion": "escribir_archivo", "ruta": "../fuera.txt", "contenido": "x"},
    )
    assert "fuera del repo" in resultado.content.lower()
    assert not (repo_local.parent / "fuera.txt").exists()


async def test_escribir_archivo_sin_contenido(make_ctx, repo_local):
    ctx = make_ctx(settings=_settings_local(repo_local))
    resultado = await AccederCodigoLocalTool().run(
        ctx, {"accion": "escribir_archivo", "ruta": "x.txt"}
    )
    assert "contenido" in resultado.content.lower()


async def test_listar_directorio_raiz(make_ctx, repo_local):
    ctx = make_ctx(settings=_settings_local(repo_local))
    resultado = await AccederCodigoLocalTool().run(ctx, {"accion": "listar_directorio"})
    assert "archivo.txt" in resultado.content
    assert "subdir/" in resultado.content


async def test_listar_directorio_subdirectorio(make_ctx, repo_local):
    ctx = make_ctx(settings=_settings_local(repo_local))
    resultado = await AccederCodigoLocalTool().run(
        ctx, {"accion": "listar_directorio", "ruta": "subdir"}
    )
    assert "otro.txt" in resultado.content


async def test_buscar_encuentra_coincidencias(make_ctx, repo_local):
    ctx = make_ctx(settings=_settings_local(repo_local))
    resultado = await AccederCodigoLocalTool().run(
        ctx, {"accion": "buscar", "patron": "segunda"}
    )
    assert "archivo.txt:2" in resultado.content


async def test_buscar_sin_coincidencias(make_ctx, repo_local):
    ctx = make_ctx(settings=_settings_local(repo_local))
    resultado = await AccederCodigoLocalTool().run(
        ctx, {"accion": "buscar", "patron": "esto-no-existe-en-ningun-lado"}
    )
    assert "sin coincidencias" in resultado.content.lower()


async def test_buscar_patron_invalido(make_ctx, repo_local):
    ctx = make_ctx(settings=_settings_local(repo_local))
    resultado = await AccederCodigoLocalTool().run(
        ctx, {"accion": "buscar", "patron": "("}
    )
    assert "patrón válido" in resultado.content.lower()


async def test_ejecutar_comando_ok(make_ctx, repo_local):
    ctx = make_ctx(settings=_settings_local(repo_local))
    resultado = await AccederCodigoLocalTool().run(
        ctx, {"accion": "ejecutar_comando", "comando": "echo hola-desde-el-comando"}
    )
    assert "hola-desde-el-comando" in resultado.content
    assert resultado.data["codigo"] == 0


async def test_ejecutar_comando_codigo_de_error(make_ctx, repo_local):
    ctx = make_ctx(settings=_settings_local(repo_local))
    resultado = await AccederCodigoLocalTool().run(
        ctx, {"accion": "ejecutar_comando", "comando": "exit 3"}
    )
    assert resultado.data["codigo"] == 3


async def test_ejecutar_comando_corre_en_la_raiz_del_repo(make_ctx, repo_local):
    ctx = make_ctx(settings=_settings_local(repo_local))
    resultado = await AccederCodigoLocalTool().run(
        ctx, {"accion": "ejecutar_comando", "comando": "pwd"}
    )
    assert str(repo_local) in resultado.content


async def test_git_status_diff_commit_flujo_completo(make_ctx, repo_local):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=repo_local, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_local, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_local, check=True)

    ctx = make_ctx(settings=_settings_local(repo_local))
    tool = AccederCodigoLocalTool()

    estado = await tool.run(ctx, {"accion": "git_status"})
    assert "archivo.txt" in estado.content or "??" in estado.content

    commit = await tool.run(ctx, {"accion": "git_commit", "mensaje": "primer commit de prueba"})
    assert "commit local" in commit.content.lower()
    assert "push" in commit.content.lower()

    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=repo_local, check=True, capture_output=True, text=True
    )
    assert "primer commit de prueba" in log.stdout


async def test_git_commit_sin_mensaje(make_ctx, repo_local):
    ctx = make_ctx(settings=_settings_local(repo_local))
    resultado = await AccederCodigoLocalTool().run(ctx, {"accion": "git_commit"})
    assert "mensaje" in resultado.content.lower()


@pytest.mark.parametrize(
    ("mensaje", "archivos_inyectados"),
    [
        ("literal $(touch inyeccion_dolar)", ["inyeccion_dolar"]),
        ("literal `touch inyeccion_backtick`", ["inyeccion_backtick"]),
        ("primera línea\nsegunda línea", []),
        ('comillas "dobles" y \'simples\'', []),
        ("--amend", []),
    ],
)
async def test_git_commit_trata_mensaje_como_dato_literal(
    make_ctx,
    repo_local,
    mensaje: str,
    archivos_inyectados: list[str],
):
    """El mensaje completo ocupa un argv: nunca es código ni opciones de Git."""
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=repo_local, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=repo_local, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_local, check=True)

    ctx = make_ctx(settings=_settings_local(repo_local))
    resultado = await AccederCodigoLocalTool().run(
        ctx,
        {"accion": "git_commit", "mensaje": mensaje},
    )

    assert "commit local" in resultado.content.lower()
    commit_message = subprocess.run(
        ["git", "log", "-1", "--format=%B"],
        cwd=repo_local,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert commit_message == mensaje
    assert all(not (repo_local / nombre).exists() for nombre in archivos_inyectados)
