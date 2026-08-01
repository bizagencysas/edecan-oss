"""Tests de ``ide_opencode_binario.py`` -- los tres caminos de resolución y
el fallo cuando ninguno aplica.

Qué queda probado END-TO-END en esta Mac (POSIX): resolución por bundle
simulado (``sys._MEIPASS`` apuntado a un directorio temporal real, con un
ejecutable real ahí), por variable de entorno, por ``PATH``
(``shutil.which`` real), la prioridad entre los tres, el chequeo de versión
contra scripts ejecutables reales (y contra el binario REAL de opencode
instalado en esta máquina, sin mocks -- se salta solo si no está), y el
mensaje de error cuando no se encuentra nada.

Qué queda probado solo como LÓGICA PURA: la rama Windows de
``_argv_para_ejecutar`` (``os.name`` no se puede volver "nt" de verdad en
esta Mac; se fuerza con ``monkeypatch.setattr(os, "name", "nt")`` y se
comprueba el argv que arma la función, sin ejecutar nada -- no hay manera
honesta de probar contra un ``cmd.exe`` real desde acá, ver el docstring del
módulo, sección Windows).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest
from edecan_companion.ide_opencode_binario import (
    VARIABLE_ENTORNO_BINARIO,
    VERSION_MINIMA_ESPERADA,
    BinarioOpencodeNoEncontrado,
    _argv_para_ejecutar,
    resolver_binario_opencode,
)

# --------------------------------------------------------------------------- #
# Helpers de test
# --------------------------------------------------------------------------- #


def _crear_ejecutable_falso(
    directorio: Path, nombre: str, salida_version: str, *, ejecutable: bool = True
) -> Path:
    """Crea un guion ``sh`` real que responde ``salida_version`` a
    ``--version`` (y a cualquier otro arg -- no le importa cuál). Se usa en
    vez de un binario real para no depender de que opencode esté instalado
    en cada test que solo quiere probar el parseo de versión."""
    ruta = directorio / nombre
    ruta.write_text(f'#!/bin/sh\necho "{salida_version}"\n', encoding="utf-8")
    modo = 0o755 if ejecutable else 0o644
    ruta.chmod(modo)
    return ruta


def _limpiar_entorno_de_resolucion(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dado que :func:`resolver_binario_opencode` mira el proceso real
    (``sys._MEIPASS``, ``PATH``, la variable de entorno), cada test
    arranca de un estado conocido: sin bundle, sin variable, y con un
    ``PATH`` vacío -- así ningún resto del entorno de quien corre la
    suite (por ejemplo, que ESTA Mac sí tenga opencode real en su PATH,
    ver MEMORY.md de la migración) contamina los tests que prueban un
    escalón puntual."""
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.delenv(VARIABLE_ENTORNO_BINARIO, raising=False)
    monkeypatch.setenv("PATH", "")


# --------------------------------------------------------------------------- #
# Los tres caminos, y su prioridad
# --------------------------------------------------------------------------- #


def test_resuelve_desde_bundle_empaquetado_y_repara_el_bit_ejecutable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _limpiar_entorno_de_resolucion(monkeypatch)
    bin_dir = tmp_path / "opencode" / "bin"
    bin_dir.mkdir(parents=True)
    candidato = _crear_ejecutable_falso(bin_dir, "opencode", "1.17.18", ejecutable=False)
    assert not os.access(
        candidato, os.X_OK
    )  # se guarda SIN el bit -- lo tiene que poner el propio módulo

    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    resultado = resolver_binario_opencode()

    assert resultado.origen == "empaquetado"
    assert resultado.ruta == candidato
    assert os.access(candidato, os.X_OK)  # _asegurar_bit_ejecutable lo reparó
    assert resultado.version == "1.17.18"
    assert resultado.version_suficiente is True
    assert resultado.advertencia is None


def test_bundle_tiene_prioridad_sobre_variable_de_entorno_y_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _limpiar_entorno_de_resolucion(monkeypatch)

    bin_dir = tmp_path / "opencode" / "bin"
    bin_dir.mkdir(parents=True)
    candidato_bundle = _crear_ejecutable_falso(bin_dir, "opencode", "1.17.18")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    env_dir = tmp_path / "env"
    env_dir.mkdir()
    candidato_env = _crear_ejecutable_falso(env_dir, "opencode-env", "1.17.18")
    monkeypatch.setenv(VARIABLE_ENTORNO_BINARIO, str(candidato_env))

    path_dir = tmp_path / "path"
    path_dir.mkdir()
    _crear_ejecutable_falso(path_dir, "opencode", "1.17.18")
    monkeypatch.setenv("PATH", str(path_dir))

    resultado = resolver_binario_opencode(verificar_version=False)

    assert resultado.origen == "empaquetado"
    assert resultado.ruta == candidato_bundle


def test_variable_de_entorno_tiene_prioridad_sobre_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _limpiar_entorno_de_resolucion(monkeypatch)

    env_dir = tmp_path / "env"
    env_dir.mkdir()
    candidato_env = _crear_ejecutable_falso(env_dir, "opencode-env", "1.17.18")
    monkeypatch.setenv(VARIABLE_ENTORNO_BINARIO, str(candidato_env))

    path_dir = tmp_path / "path"
    path_dir.mkdir()
    _crear_ejecutable_falso(path_dir, "opencode", "1.17.18")
    monkeypatch.setenv("PATH", str(path_dir))

    resultado = resolver_binario_opencode(verificar_version=False)

    assert resultado.origen == "variable_entorno"
    assert resultado.ruta == candidato_env


def test_ruta_sistema_como_ultimo_recurso(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _limpiar_entorno_de_resolucion(monkeypatch)

    path_dir = tmp_path / "path"
    path_dir.mkdir()
    candidato = _crear_ejecutable_falso(path_dir, "opencode", "1.17.18")
    monkeypatch.setenv("PATH", str(path_dir))

    resultado = resolver_binario_opencode(verificar_version=False)

    assert resultado.origen == "ruta_sistema"
    assert resultado.ruta == candidato


# --------------------------------------------------------------------------- #
# Fallos
# --------------------------------------------------------------------------- #


def test_variable_de_entorno_invalida_falla_fuerte_sin_probar_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Una declaración explícita rota debe fallar fuerte -- ver "por qué CADA
    paso se comporta distinto" en el docstring del módulo. Se deja además un
    binario perfectamente válido en el PATH para probar que NO se usa como
    respaldo silencioso."""
    _limpiar_entorno_de_resolucion(monkeypatch)

    monkeypatch.setenv(VARIABLE_ENTORNO_BINARIO, str(tmp_path / "no-existe" / "opencode"))

    path_dir = tmp_path / "path"
    path_dir.mkdir()
    _crear_ejecutable_falso(path_dir, "opencode", "1.17.18")
    monkeypatch.setenv("PATH", str(path_dir))

    with pytest.raises(BinarioOpencodeNoEncontrado) as excinfo:
        resolver_binario_opencode()

    assert VARIABLE_ENTORNO_BINARIO in str(excinfo.value)
    assert "no existe" in str(excinfo.value)


def test_variable_de_entorno_sin_permiso_de_ejecucion_falla_fuerte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _limpiar_entorno_de_resolucion(monkeypatch)

    candidato = _crear_ejecutable_falso(
        tmp_path, "opencode-sin-permiso", "1.17.18", ejecutable=False
    )
    monkeypatch.setenv(VARIABLE_ENTORNO_BINARIO, str(candidato))

    with pytest.raises(BinarioOpencodeNoEncontrado) as excinfo:
        resolver_binario_opencode()

    assert "permiso de ejecución" in str(excinfo.value)


def test_ninguno_encontrado_da_error_claro_con_que_hacer(monkeypatch: pytest.MonkeyPatch) -> None:
    _limpiar_entorno_de_resolucion(monkeypatch)

    with pytest.raises(BinarioOpencodeNoEncontrado) as excinfo:
        resolver_binario_opencode()

    mensaje = str(excinfo.value)
    # El mensaje tiene que decir QUÉ se probó...
    assert "PATH" in mensaje
    assert VARIABLE_ENTORNO_BINARIO in mensaje
    # ...y QUÉ hacer -- nunca un FileNotFoundError pelado (regla 2 del encargo).
    assert "docs/opencode-empaquetado.md" in mensaje
    assert "opencode" in mensaje.lower()


# --------------------------------------------------------------------------- #
# Versión
# --------------------------------------------------------------------------- #


def test_version_antigua_genera_advertencia_pero_no_falla(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _limpiar_entorno_de_resolucion(monkeypatch)
    path_dir = tmp_path / "path"
    path_dir.mkdir()
    _crear_ejecutable_falso(path_dir, "opencode", "0.1.0")
    monkeypatch.setenv("PATH", str(path_dir))

    resultado = resolver_binario_opencode()

    assert resultado.version == "0.1.0"
    assert resultado.version_suficiente is False
    assert resultado.advertencia is not None
    assert "0.1.0" in resultado.advertencia
    assert ".".join(map(str, VERSION_MINIMA_ESPERADA)) in resultado.advertencia


def test_version_no_reconocible_da_advertencia_pero_no_falla(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _limpiar_entorno_de_resolucion(monkeypatch)
    path_dir = tmp_path / "path"
    path_dir.mkdir()
    _crear_ejecutable_falso(path_dir, "opencode", "no-soy-un-numero-de-version")
    monkeypatch.setenv("PATH", str(path_dir))

    resultado = resolver_binario_opencode()

    assert resultado.version is None
    assert resultado.version_suficiente is None
    assert resultado.advertencia is not None


def test_verificar_version_false_no_ejecuta_el_binario(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Con ``verificar_version=False`` no debería importar que el script
    ni siquiera responda -- no se lo llama."""
    _limpiar_entorno_de_resolucion(monkeypatch)
    path_dir = tmp_path / "path"
    path_dir.mkdir()
    ruta = path_dir / "opencode"
    ruta.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    ruta.chmod(0o755)
    monkeypatch.setenv("PATH", str(path_dir))

    resultado = resolver_binario_opencode(verificar_version=False)

    assert resultado.version is None
    assert resultado.version_suficiente is None
    assert resultado.advertencia is None


def test_version_real_contra_el_binario_de_opencode_instalado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin mocks: si esta máquina tiene un opencode real instalado (lo tiene
    esta Mac, ver MEMORY.md de la migración -- ``~/.opencode/bin/opencode``,
    versión 1.17.18), lo resuelve por PATH de verdad y le pregunta su
    versión de verdad. Se salta solo si la máquina que corre esto no tiene
    opencode -- nunca se inventa un resultado."""
    real = shutil.which("opencode")
    if real is None:
        pytest.skip("Esta máquina no tiene 'opencode' en el PATH.")

    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.delenv(VARIABLE_ENTORNO_BINARIO, raising=False)
    # El PATH real se deja intacto a propósito -- es justo lo que se prueba.

    resultado = resolver_binario_opencode()

    assert resultado.origen == "ruta_sistema"
    assert resultado.ruta == Path(real)
    assert resultado.version is not None
    # No se fija un valor exacto: si el dueño actualiza opencode en esta Mac,
    # este test no debe romperse por eso -- solo importa que SÍ haya podido
    # leer una versión real y decidir si alcanza.
    assert resultado.version_suficiente is not None


# --------------------------------------------------------------------------- #
# Armado de argv -- POSIX end-to-end, Windows como lógica pura (ver docstring
# del módulo de test).
# --------------------------------------------------------------------------- #


def test_argv_posix_no_envuelve_en_cmd(tmp_path: Path) -> None:
    ruta = tmp_path / "opencode"
    assert _argv_para_ejecutar(ruta, "--version") == [str(ruta), "--version"]


def test_argv_windows_exe_no_se_envuelve_en_cmd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(os, "name", "nt")
    ruta = tmp_path / "opencode.exe"
    assert _argv_para_ejecutar(ruta, "--version") == [str(ruta), "--version"]


def test_argv_windows_cmd_se_envuelve_con_comspec(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    ruta = tmp_path / "opencode.cmd"
    assert _argv_para_ejecutar(ruta, "--version") == [
        r"C:\Windows\System32\cmd.exe",
        "/c",
        str(ruta),
        "--version",
    ]


def test_argv_windows_bat_sin_comspec_usa_cmd_exe_por_defecto(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.delenv("COMSPEC", raising=False)
    ruta = tmp_path / "opencode.bat"
    assert _argv_para_ejecutar(ruta) == ["cmd.exe", "/c", str(ruta)]


def test_es_ejecutable_no_revienta_con_ruta_inexistente() -> None:
    # Cobertura mínima del helper interno: una ruta que ni existe no debe
    # lanzar nada, solo devolver False (lo que hace que el escalón que la
    # use siga probando el siguiente, en vez de reventar).
    from edecan_companion.ide_opencode_binario import _es_ejecutable

    assert _es_ejecutable(Path("/ruta/que/no/existe/en/ningun/lado")) is False
