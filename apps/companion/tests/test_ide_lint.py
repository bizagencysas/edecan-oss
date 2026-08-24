"""Pruebas de ``ide_lint.py``: el linter primero, el modelo solo si sobra algo.

Los proyectos de prueba son reales (carpeta temporal con ``pyproject.toml`` o
``package.json``, archivos con problemas de verdad) y los linters también se
ejecutan de verdad, como subprocesos. Lo único simulado es el binario: en vez
de exigir que la máquina que corre las pruebas tenga ruff y eslint instalados
-- y de quedar a merced de la versión que tenga --, cada proyecto trae su
linter dentro (``.venv/bin/ruff``, ``node_modules/.bin/eslint``), que es
exactamente donde ``ide_lint`` busca primero. Así se ejercita el camino real
completo: descubrimiento del binario del proyecto, ejecución, parseo de la
salida, arreglo automático y verificación de alcance sobre archivos que
cambian en disco.
"""

from __future__ import annotations

import hashlib
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from edecan_companion.ide_lint import (
    IDELintError,
    SolicitudDeArreglo,
    detectar_linters,
    reparar_lint,
    reparar_todo_el_lint,
    revisar_lint,
)

sin_git = pytest.mark.skipif(shutil.which("git") is None, reason="hace falta git")

# --------------------------------------------------------------------- #
# Linters de juguete, con el comportamiento que importa: qué marcan, qué
# saben arreglar solos y con qué código de salida terminan.
# --------------------------------------------------------------------- #

_RUFF = """
import json
import os
import sys
from pathlib import Path

ACEPTA_OUTPUT_FORMAT = {acepta_output_format}
ROMPE_AL_ARREGLAR = {rompe_al_arreglar}

args = sys.argv[1:]
if not args or args[0] != "check":
    sys.exit(2)

formato = "texto"
arreglar = False
rutas = []
i = 1
while i < len(args):
    actual = args[i]
    if actual in ("--output-format", "--format"):
        moderna = actual == "--output-format"
        if moderna != ACEPTA_OUTPUT_FORMAT:
            print(f"error: unexpected argument '{{actual}}' found", file=sys.stderr)
            sys.exit(2)
        formato = args[i + 1]
        i += 2
        continue
    if actual == "--fix":
        arreglar = True
        i += 1
        continue
    rutas.append(actual)
    i += 1

# Un `--fix` puede dejar el proyecto en un estado que la revisión SIGUIENTE ya
# no sabe leer (una config a medio escribir, un archivo sin parsear). El
# centinela reproduce eso: una vez creado, toda revisión posterior falla.
CENTINELA = Path("ROTO_A_PROPOSITO")
if CENTINELA.exists() and not arreglar:
    print("error: Failed to parse the configuration file: expected a table", file=sys.stderr)
    sys.exit(2)


def archivos():
    bases = [Path(r) for r in rutas] or [Path(".")]
    for base in bases:
        if base.is_file():
            yield base
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in {{".venv", "node_modules"}}]
            for nombre in sorted(filenames):
                if nombre.endswith(".py"):
                    yield Path(dirpath) / nombre


def problemas():
    encontrados = []
    for archivo in sorted(archivos()):
        for numero, linea in enumerate(
            archivo.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if linea.strip() == "import os":
                encontrados.append((archivo, numero, 1, "F401", "`os` imported but unused"))
            if "variable_indefinida" in linea:
                columna = linea.index("variable_indefinida") + 1
                encontrados.append(
                    (archivo, numero, columna, "F821", "undefined name `variable_indefinida`")
                )
    return encontrados


if arreglar:
    for archivo in sorted(archivos()):
        lineas = archivo.read_text(encoding="utf-8").splitlines(keepends=True)
        quedan = [linea for linea in lineas if linea.strip() != "import os"]
        if quedan != lineas:
            archivo.write_text("".join(quedan), encoding="utf-8")
    # El arreglo sí se aplicó; lo que rompe es el estado que deja atrás.
    if ROMPE_AL_ARREGLAR:
        CENTINELA.write_text("roto", encoding="utf-8")

encontrados = problemas()
if formato == "json":
    print(
        json.dumps(
            [
                {{
                    "code": codigo,
                    "message": mensaje,
                    "filename": str(archivo.resolve()),
                    "location": {{"row": fila, "column": columna}},
                }}
                for archivo, fila, columna, codigo, mensaje in encontrados
            ]
        )
    )
else:
    for archivo, fila, columna, codigo, mensaje in encontrados:
        print(f"{{archivo}}:{{fila}}:{{columna}}: {{codigo}} {{mensaje}}")
sys.exit(1 if encontrados else 0)
"""

# Imita a eslint 8 en los tres detalles que a este módulo le cuestan caro: sin
# patrón de archivos no revisa NADA (y sale con 0, que se ve igual que "todo
# limpio"), los avisos no cambian el código de salida salvo con
# `--max-warnings`, y el formato por defecto no es "archivo:línea:columna".
_ESLINT = """
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
formato = "stylish"
arreglar = False
max_avisos = None
rutas = []
i = 0
while i < len(args):
    if args[i] == "--format":
        formato = args[i + 1]
        i += 2
        continue
    if args[i] == "--max-warnings":
        max_avisos = int(args[i + 1])
        i += 2
        continue
    if args[i] == "--fix":
        arreglar = True
        i += 1
        continue
    rutas.append(args[i])
    i += 1

EXTENSIONES = (".js", ".jsx", ".ts", ".tsx")


def archivos():
    # Sin rutas no se revisa nada: eslint 8 exige el patrón de archivos.
    for base in [Path(r) for r in rutas]:
        if base.is_file():
            yield base
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in {".venv", "node_modules"}]
            for nombre in sorted(filenames):
                if nombre.endswith(EXTENSIONES):
                    yield Path(dirpath) / nombre


def problemas():
    encontrados = []
    for archivo in sorted(archivos()):
        for numero, linea in enumerate(
            archivo.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if " == " in linea:
                encontrados.append(
                    (archivo, numero, linea.index(" == ") + 2, "eqeqeq", "Expected '===' ", 2)
                )
            if "debugger" in linea:
                encontrados.append(
                    (archivo, numero, 1, "no-debugger", "Unexpected 'debugger' statement.", 2)
                )
            if "<img" in linea:
                encontrados.append(
                    (archivo, numero, linea.index("<img") + 1, "jsx-a11y/alt-text",
                     "img elements must have an alt prop.", 1)
                )
    return encontrados


if arreglar:
    for archivo in sorted(archivos()):
        original = archivo.read_text(encoding="utf-8")
        corregido = original.replace(" == ", " === ")
        if corregido != original:
            archivo.write_text(corregido, encoding="utf-8")

encontrados = problemas()
errores = [p for p in encontrados if p[5] == 2]
avisos = [p for p in encontrados if p[5] == 1]
if formato == "json":
    por_archivo = {}
    for archivo, fila, columna, regla, mensaje, severidad in encontrados:
        clave = str(archivo.resolve())
        por_archivo.setdefault(clave, []).append(
            {"ruleId": regla, "severity": severidad, "line": fila,
             "column": columna, "message": mensaje}
        )
    print(json.dumps([
        {"filePath": ruta, "messages": mensajes} for ruta, mensajes in por_archivo.items()
    ]))
elif formato == "unix":
    for archivo, fila, columna, regla, mensaje, severidad in encontrados:
        etiqueta = "Error" if severidad == 2 else "Warning"
        print(f"{archivo}:{fila}:{columna}: {mensaje} [{etiqueta}/{regla}]")
else:
    print()
    for archivo, fila, columna, regla, mensaje, severidad in encontrados:
        etiqueta = "error" if severidad == 2 else "warning"
        print(f"  {fila}:{columna}  {etiqueta}  {mensaje}  {regla}")

demasiados_avisos = max_avisos is not None and len(avisos) > max_avisos
sys.exit(1 if errores or demasiados_avisos else 0)
"""

_FLAKE8 = """
import os
import sys
from pathlib import Path

rutas = [a for a in sys.argv[1:] if not a.startswith("-")]


def archivos():
    bases = [Path(r) for r in rutas] or [Path(".")]
    for base in bases:
        if base.is_file():
            yield base
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in {".venv", "node_modules"}]
            for nombre in sorted(filenames):
                if nombre.endswith(".py"):
                    yield Path(dirpath) / nombre


encontrados = []
for archivo in sorted(archivos()):
    for numero, linea in enumerate(archivo.read_text(encoding="utf-8").splitlines(), start=1):
        if len(linea) > 40:
            encontrados.append((archivo, numero, 41))

for archivo, fila, columna in encontrados:
    print(f"{archivo}:{fila}:{columna}: E501 line too long")
sys.exit(1 if encontrados else 0)
"""

_LINTER_ROTO = """
import sys

print("error: Failed to parse the configuration file: expected a table", file=sys.stderr)
sys.exit(2)
"""


def _instalar_binario(destino: Path, cuerpo: str) -> Path:
    """Escribe un ejecutable de verdad donde ``ide_lint`` va a buscarlo."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(f"#!{sys.executable}\n{textwrap.dedent(cuerpo)}", encoding="utf-8")
    destino.chmod(destino.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return destino


def _proyecto_python(
    tmp_path: Path,
    *,
    con_ruff: bool = True,
    acepta_output_format: bool = True,
    rompe_al_arreglar: bool = False,
) -> Path:
    raiz = tmp_path / "proyecto_py"
    raiz.mkdir()
    (raiz / "pyproject.toml").write_text(
        '[project]\nname = "demo"\n\n[tool.ruff]\nline-length = 100\n', encoding="utf-8"
    )
    if con_ruff:
        _instalar_binario(
            raiz / ".venv" / "bin" / "ruff",
            _RUFF.format(
                acepta_output_format=acepta_output_format,
                rompe_al_arreglar=rompe_al_arreglar,
            ),
        )
    return raiz


def _repo_git(raiz: Path) -> None:
    """Convierte el proyecto en un repo con un commit: sin base, todo es "cambiado"."""
    (raiz / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(raiz), "init", "-q"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(raiz), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(raiz),
            "-c",
            "user.email=pruebas@example.com",
            "-c",
            "user.name=Pruebas",
            "commit",
            "-q",
            "-m",
            "base",
        ],
        check=True,
        capture_output=True,
    )


def _proyecto_js(tmp_path: Path, *, con_eslint: bool = True) -> Path:
    raiz = tmp_path / "proyecto_js"
    raiz.mkdir()
    (raiz / "package.json").write_text(
        '{"name": "demo", "devDependencies": {"eslint": "^9.0.0"}}', encoding="utf-8"
    )
    (raiz / "eslint.config.js").write_text("export default [];\n", encoding="utf-8")
    if con_eslint:
        _instalar_binario(raiz / "node_modules" / ".bin" / "eslint", _ESLINT)
    return raiz


def _hash(archivo: Path) -> str:
    return hashlib.sha256(archivo.read_bytes()).hexdigest()


# --------------------------------------------------------------------- #
# Detección.
# --------------------------------------------------------------------- #


def test_detecta_ruff_por_la_configuracion_del_proyecto(tmp_path: Path):
    raiz = _proyecto_python(tmp_path)

    detectados = detectar_linters(raiz)

    assert [linter.nombre for linter in detectados] == ["ruff"]
    assert detectados[0].evidencia == "pyproject.toml"
    assert detectados[0].disponible is True
    # El binario que gana es el del propio proyecto, no uno global.
    assert detectados[0].ejecutable is not None
    assert detectados[0].ejecutable.endswith(".venv/bin/ruff")


def test_detecta_eslint_en_un_proyecto_js(tmp_path: Path):
    raiz = _proyecto_js(tmp_path)

    detectados = detectar_linters(raiz)

    assert [linter.nombre for linter in detectados] == ["eslint"]
    assert detectados[0].evidencia == "eslint.config.js"
    assert detectados[0].arregla_solo is True


def _proyecto_swift_sin_swiftlint_instalado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """`.swiftlint.yml` presente pero el binario NUNCA se encuentra -- ni en
    el proyecto (swiftlint no tiene `busqueda_local`) ni en el PATH."""
    raiz = tmp_path / "app-swift"
    raiz.mkdir()
    (raiz / ".swiftlint.yml").write_text("---\n", encoding="utf-8")
    (raiz / "Archivo.swift").write_text("let x = 1\n", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda binario: None)
    return raiz


# ---------------------------------------------------------------------------
# `como_instalar` correcto SEGÚN LA PLATAFORMA -- swiftlint/ktlint/golangci-lint
# citaban `brew install X` incluso corriendo en Linux (confirmado en vivo,
# edecan-prod, 1-ago-2026: `brew: NO EXISTE`), una instrucción falsa de la
# misma familia que la de `open_app`/portapapeles con `xdg-open`/`xclip`.
# ---------------------------------------------------------------------------


def test_motivo_no_disponible_cites_brew_on_darwin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    raiz = _proyecto_swift_sin_swiftlint_instalado(tmp_path, monkeypatch)

    detectados = detectar_linters(raiz)

    assert [linter.nombre for linter in detectados] == ["swiftlint"]
    assert detectados[0].disponible is False
    assert "brew install swiftlint" in detectados[0].motivo_no_disponible


def test_motivo_no_disponible_does_not_cite_brew_on_linux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(sys, "platform", "linux")
    raiz = _proyecto_swift_sin_swiftlint_instalado(tmp_path, monkeypatch)

    detectados = detectar_linters(raiz)

    motivo = detectados[0].motivo_no_disponible
    assert motivo is not None
    # La instrucción vieja y falsa era EXACTAMENTE esta frase, sin nada más --
    # `brew` puede seguir mencionado como alternativa, pero no como la única
    # vía (que es justo lo que hacía mentir en Linux).
    assert "Instálalo con `brew install swiftlint`. No se descarga" not in motivo
    assert "no hay `brew` por defecto" in motivo
    assert "swift build" in motivo  # instrucción real para Linux


def test_sin_configuracion_no_se_inventa_un_linter(tmp_path: Path):
    raiz = tmp_path / "vacio"
    raiz.mkdir()
    (raiz / "algo.py").write_text("x = 1\n", encoding="utf-8")

    assert detectar_linters(raiz) == ()
    resultado = reparar_lint(raiz)
    assert resultado.estado == "sin_linter"
    assert resultado.archivos_modificados == ()


def test_las_rutas_de_python_no_invocan_al_linter_de_js(tmp_path: Path):
    raiz = tmp_path / "mixto"
    raiz.mkdir()
    (raiz / "pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")
    (raiz / "package.json").write_text('{"devDependencies": {"eslint": "^9"}}', encoding="utf-8")

    solo_python = detectar_linters(raiz, rutas=["app/main.py"])
    solo_js = detectar_linters(raiz, rutas=["web/index.ts"])

    assert [linter.nombre for linter in solo_python] == ["ruff"]
    assert [linter.nombre for linter in solo_js] == ["eslint"]


def test_en_un_monorepo_la_config_de_la_raiz_vale_para_el_subpaquete(tmp_path: Path):
    """El paquete no repite la config del linter: vive arriba, como en este repo."""
    raiz = _proyecto_python(tmp_path)
    paquete = raiz / "apps" / "servicio"
    paquete.mkdir(parents=True)
    (paquete / "pyproject.toml").write_text('[project]\nname = "servicio"\n', encoding="utf-8")
    (paquete / "modulo.py").write_text("import os\n", encoding="utf-8")

    detectados = detectar_linters(paquete)

    assert [linter.nombre for linter in detectados] == ["ruff"]
    # Y se dice de dónde salió, para que nadie busque la config donde no está.
    assert detectados[0].evidencia == "../../pyproject.toml"
    assert detectados[0].disponible is True

    resultado = reparar_lint(paquete, rutas=["modulo.py"])

    assert resultado.estado == "arreglado_por_el_linter"
    assert (paquete / "modulo.py").read_text(encoding="utf-8") == ""


def test_linter_configurado_pero_no_instalado_se_dice_claro_y_no_revienta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Que falte ``node_modules`` no es un error del usuario ni una excepción."""
    raiz = _proyecto_js(tmp_path, con_eslint=False)
    # Sin esto, la prueba dependería de si quien la corre tiene eslint global.
    monkeypatch.setattr("edecan_companion.ide_lint.shutil.which", lambda _nombre: None)
    (raiz / "index.js").write_text("if (a == b) {}\n", encoding="utf-8")

    detectados = detectar_linters(raiz)
    resultado = reparar_lint(raiz)

    assert detectados[0].disponible is False
    assert resultado.estado == "linter_no_instalado"
    assert "npm install" in resultado.mensaje
    # Y se dice explícitamente que no se descarga nada por su cuenta.
    assert "No se descarga nada solo." in resultado.mensaje
    assert (raiz / "index.js").read_text(encoding="utf-8") == "if (a == b) {}\n"


# --------------------------------------------------------------------- #
# El corazón: que el arreglo automático se use antes que el modelo.
# --------------------------------------------------------------------- #


def test_lo_que_ruff_arregla_solo_no_llega_al_modelo(tmp_path: Path):
    raiz = _proyecto_python(tmp_path)
    archivo = raiz / "modulo.py"
    archivo.write_text("import os\n\n\ndef suma(a, b):\n    return a + b\n", encoding="utf-8")

    llamadas: list[SolicitudDeArreglo] = []
    resultado = reparar_lint(raiz, rutas=["modulo.py"], arreglar=llamadas.append)

    assert resultado.estado == "arreglado_por_el_linter"
    assert resultado.limpio is True
    assert resultado.resueltos_por_el_linter == 1
    assert resultado.intentos_del_modelo == 0
    assert llamadas == []  # el modelo ni se enteró: eso es todo el punto
    assert archivo.read_text(encoding="utf-8") == "\n\ndef suma(a, b):\n    return a + b\n"
    assert resultado.archivos_modificados == ("modulo.py",)
    assert resultado.alcance_respetado is True


def test_solo_lo_que_fix_no_pudo_llega_al_modelo(tmp_path: Path):
    raiz = _proyecto_python(tmp_path)
    archivo = raiz / "modulo.py"
    archivo.write_text(
        "import os\n\n\ndef f():\n    return variable_indefinida\n", encoding="utf-8"
    )

    solicitudes: list[SolicitudDeArreglo] = []

    def arreglar(solicitud: SolicitudDeArreglo) -> None:
        solicitudes.append(solicitud)
        contenido = archivo.read_text(encoding="utf-8")
        archivo.write_text(contenido.replace("variable_indefinida", "42"), encoding="utf-8")

    resultado = reparar_lint(raiz, rutas=["modulo.py"], arreglar=arreglar)

    assert resultado.estado == "arreglado_con_ayuda_del_modelo"
    assert resultado.resueltos_por_el_linter == 1  # el import lo quitó ruff
    assert resultado.intentos_del_modelo == 1
    assert len(solicitudes) == 1
    # Al modelo se le pasa SOLO lo que sobrevivió al --fix.
    codigos = {problema.codigo for problema in solicitudes[0].problemas}
    assert codigos == {"F821"}
    assert solicitudes[0].archivos == ("modulo.py",)
    assert "ÚNICAMENTE" in solicitudes[0].instrucciones
    assert "modulo.py" in solicitudes[0].instrucciones


def test_sin_funcion_de_arreglo_se_reporta_lo_que_queda_sin_gastar_el_modelo(tmp_path: Path):
    raiz = _proyecto_python(tmp_path)
    archivo = raiz / "modulo.py"
    archivo.write_text("import os\nx = variable_indefinida\n", encoding="utf-8")

    resultado = reparar_lint(raiz, rutas=["modulo.py"])

    assert resultado.estado == "quedan_problemas"
    assert resultado.limpio is False
    assert resultado.detenido_por == "sin_funcion_de_arreglo"
    assert resultado.resueltos_por_el_linter == 1
    assert [problema.codigo for problema in resultado.problemas_restantes] == ["F821"]
    # El arreglo gratuito SÍ se aplicó igual: no hay razón para no hacerlo.
    assert "import os" not in archivo.read_text(encoding="utf-8")


def test_un_modelo_que_no_arregla_nada_corta_por_error_repetido(tmp_path: Path):
    raiz = _proyecto_python(tmp_path)
    (raiz / "modulo.py").write_text("x = variable_indefinida\n", encoding="utf-8")

    llamadas: list[SolicitudDeArreglo] = []
    resultado = reparar_lint(
        raiz, rutas=["modulo.py"], arreglar=llamadas.append, max_intentos_del_modelo=10
    )

    assert resultado.estado == "quedan_problemas"
    # El freno lo pone `ide_verificacion`: dos corridas con el mismo error y
    # se detiene, mucho antes del tope de 10.
    assert resultado.detenido_por == "error_repetido"
    assert len(llamadas) == 1
    assert resultado.intentos_del_modelo == 1


def test_el_tope_de_intentos_del_modelo_se_respeta(tmp_path: Path):
    raiz = _proyecto_python(tmp_path)
    archivo = raiz / "modulo.py"
    archivo.write_text("x = variable_indefinida\n", encoding="utf-8")

    llamadas: list[SolicitudDeArreglo] = []

    def arreglar_a_medias(solicitud: SolicitudDeArreglo) -> None:
        # Cada vuelta cambia la columna del problema, así que el error nunca
        # es idéntico y la única frenada posible es el tope.
        llamadas.append(solicitud)
        archivo.write_text(" " * len(llamadas) + "x = variable_indefinida\n", encoding="utf-8")

    resultado = reparar_lint(
        raiz, rutas=["modulo.py"], arreglar=arreglar_a_medias, max_intentos_del_modelo=2
    )

    assert resultado.estado == "quedan_problemas"
    assert resultado.detenido_por == "tope_de_intentos"
    assert len(llamadas) == 2


def test_max_intentos_del_modelo_en_cero_no_llama_al_modelo(tmp_path: Path):
    raiz = _proyecto_python(tmp_path)
    (raiz / "modulo.py").write_text("x = variable_indefinida\n", encoding="utf-8")

    llamadas: list[SolicitudDeArreglo] = []
    resultado = reparar_lint(
        raiz, rutas=["modulo.py"], arreglar=llamadas.append, max_intentos_del_modelo=0
    )

    assert resultado.estado == "quedan_problemas"
    assert llamadas == []


# --------------------------------------------------------------------- #
# Alcance: nada de "de paso te reordené todo".
# --------------------------------------------------------------------- #


def test_sin_rutas_y_sin_git_no_se_arregla_el_proyecto_entero(tmp_path: Path):
    """Sin saber qué tocó la sesión, `--fix` sobre todo el repo es un diff que nadie pidió."""
    raiz = _proyecto_python(tmp_path)
    (raiz / "tocado.py").write_text("import os\nVALOR = 1\n", encoding="utf-8")
    ajeno = raiz / "ajeno.py"
    ajeno.write_text("import os\nOTRO = 2\n", encoding="utf-8")
    antes = _hash(ajeno)

    resultado = reparar_lint(raiz, arreglar=lambda _s: pytest.fail("no debía llamarse"))

    assert resultado.estado == "sin_alcance"
    assert resultado.limpio is False
    assert resultado.archivos_modificados == ()
    # Ni un byte: ni siquiera del archivo que el linter sí habría arreglado.
    assert _hash(ajeno) == antes
    assert "rutas" in resultado.mensaje
    assert "todo_el_proyecto" in resultado.mensaje


@sin_git
def test_sin_rutas_el_alcance_sale_de_lo_que_cambio_en_git(tmp_path: Path):
    """Lo que la sesión tocó ya está escrito en el working tree: se lee de ahí."""
    raiz = _proyecto_python(tmp_path)
    tocado = raiz / "tocado.py"
    tocado.write_text("VALOR = 1\n", encoding="utf-8")
    intacto = raiz / "intacto.py"
    intacto.write_text("import os\nOTRO = 2\n", encoding="utf-8")
    _repo_git(raiz)
    # Recién ahora se ensucia uno solo: ese es el alcance de "esta sesión".
    tocado.write_text("import os\nVALOR = 1\n", encoding="utf-8")
    antes = _hash(intacto)

    resultado = reparar_lint(raiz)

    assert resultado.estado == "arreglado_por_el_linter"
    assert resultado.alcance_origen == "git"
    assert resultado.archivos_del_alcance == ("tocado.py",)
    assert resultado.archivos_modificados == ("tocado.py",)
    assert _hash(intacto) == antes


@sin_git
def test_desde_un_subpaquete_el_alcance_no_se_lleva_lo_de_los_vecinos(tmp_path: Path):
    """git imprime rutas desde la raíz del repo; el linter corre desde el paquete."""
    raiz = _proyecto_python(tmp_path)
    paquete = raiz / "apps" / "servicio"
    paquete.mkdir(parents=True)
    (paquete / "modulo.py").write_text("VALOR = 1\n", encoding="utf-8")
    vecino = raiz / "apps" / "web" / "aparte.py"
    vecino.parent.mkdir(parents=True)
    vecino.write_text("OTRO = 2\n", encoding="utf-8")
    _repo_git(raiz)
    # Se ensucian los dos, pero solo uno vive bajo el paquete que se está reparando.
    (paquete / "modulo.py").write_text("import os\nVALOR = 1\n", encoding="utf-8")
    vecino.write_text("import os\nOTRO = 2\n", encoding="utf-8")
    antes = _hash(vecino)

    resultado = reparar_lint(paquete)

    assert resultado.alcance_origen == "git"
    # Relativa al paquete, no al repo: es lo que entiende el linter desde ahí.
    assert resultado.archivos_del_alcance == ("modulo.py",)
    assert resultado.estado == "arreglado_por_el_linter"
    assert _hash(vecino) == antes


@sin_git
def test_si_la_sesion_no_toco_nada_de_ese_lenguaje_no_hay_nada_que_reparar(tmp_path: Path):
    raiz = _proyecto_python(tmp_path)
    (raiz / "modulo.py").write_text("import os\nVALOR = 1\n", encoding="utf-8")
    _repo_git(raiz)
    (raiz / "notas.md").write_text("nada de python\n", encoding="utf-8")

    resultado = reparar_lint(raiz)

    assert resultado.estado == "sin_problemas"
    assert resultado.archivos_modificados == ()
    # El .py sucio sigue sucio a propósito: nadie lo tocó en esta sesión.
    assert "import os" in (raiz / "modulo.py").read_text(encoding="utf-8")


def test_arreglar_el_proyecto_entero_es_una_decision_explicita(tmp_path: Path):
    raiz = _proyecto_python(tmp_path)
    uno = raiz / "uno.py"
    uno.write_text("import os\nVALOR = 1\n", encoding="utf-8")
    otro = raiz / "otro.py"
    otro.write_text("import os\nOTRO = 2\n", encoding="utf-8")

    resultado = reparar_lint(raiz, todo_el_proyecto=True)

    assert resultado.estado == "arreglado_por_el_linter"
    assert resultado.alcance_origen == "proyecto"
    assert resultado.archivos_modificados == ("otro.py", "uno.py")
    assert "import os" not in uno.read_text(encoding="utf-8")
    assert "import os" not in otro.read_text(encoding="utf-8")


def test_tocar_un_archivo_fuera_de_las_rutas_pedidas_queda_denunciado(tmp_path: Path):
    """El alcance es lo que la SESIÓN declaró, no lo que el linter marcó.

    Antes la foto previa se sacaba solo de ``rutas``, así que una edición
    fuera de ``rutas`` no aparecía en ningún lado y ``alcance_respetado``
    decía que sí, que todo bien.
    """
    raiz = _proyecto_python(tmp_path)
    (raiz / "modulo.py").write_text("x = variable_indefinida\n", encoding="utf-8")
    ajeno = raiz / "nada_que_ver.py"
    ajeno.write_text("VALOR = 1\n", encoding="utf-8")

    def arreglar_de_mas(_solicitud: SolicitudDeArreglo) -> None:
        (raiz / "modulo.py").write_text("x = 1\n", encoding="utf-8")
        ajeno.write_text("VALOR = 1  # ya que estaba\n", encoding="utf-8")

    resultado = reparar_lint(raiz, rutas=["modulo.py"], arreglar=arreglar_de_mas)

    assert resultado.alcance_verificado is True
    assert resultado.alcance_respetado is False
    assert resultado.archivos_fuera_de_alcance == ("nada_que_ver.py",)
    assert "nada_que_ver.py" in resultado.mensaje


def test_si_no_se_pudo_verificar_el_alcance_no_se_dice_que_se_respeto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Que no se haya podido mirar no es lo mismo que "está bien"."""
    raiz = _proyecto_python(tmp_path)
    (raiz / "modulo.py").write_text("import os\nVALOR = 1\n", encoding="utf-8")
    monkeypatch.setattr("edecan_companion.ide_lint.MAX_ARCHIVOS_FOTOGRAFIA", 0)

    resultado = reparar_lint(raiz, rutas=["modulo.py"])

    assert resultado.alcance_verificado is False
    assert resultado.alcance_respetado is False
    assert resultado.resumen()["alcance_respetado"] is False


def test_un_archivo_tocado_que_el_linter_no_marco_queda_denunciado(tmp_path: Path):
    """La otra mitad de la regla: estaba en `rutas`, pero el linter nunca lo marcó."""
    raiz = _proyecto_python(tmp_path)
    (raiz / "modulo.py").write_text("x = variable_indefinida\n", encoding="utf-8")
    ajeno = raiz / "nada_que_ver.py"
    ajeno.write_text("VALOR = 1\n", encoding="utf-8")

    def arreglar_de_mas(solicitud: SolicitudDeArreglo) -> None:
        (raiz / "modulo.py").write_text("x = 1\n", encoding="utf-8")
        # El clásico "ya que estaba, aproveché y reordené esto otro".
        ajeno.write_text("VALOR = 1  # reordenado sin que nadie lo pidiera\n", encoding="utf-8")

    resultado = reparar_lint(
        raiz, rutas=["modulo.py", "nada_que_ver.py"], arreglar=arreglar_de_mas
    )

    assert resultado.estado == "arreglado_con_ayuda_del_modelo"
    assert resultado.alcance_verificado is True
    assert resultado.alcance_respetado is False
    assert resultado.archivos_fuera_de_alcance == ("nada_que_ver.py",)
    assert "nada_que_ver.py" in resultado.mensaje


def test_revisar_lint_no_modifica_ni_un_byte(tmp_path: Path):
    raiz = _proyecto_python(tmp_path)
    archivo = raiz / "modulo.py"
    archivo.write_text("import os\nx = variable_indefinida\n", encoding="utf-8")
    antes = _hash(archivo)

    informe = revisar_lint(raiz)

    assert informe is not None
    assert informe.limpio is False
    assert [problema.codigo for problema in informe.problemas] == ["F401", "F821"]
    assert informe.archivos == ("modulo.py",)
    assert informe.problemas[0].linea == 1
    assert _hash(archivo) == antes


def test_un_proyecto_limpio_no_se_toca(tmp_path: Path):
    raiz = _proyecto_python(tmp_path)
    archivo = raiz / "modulo.py"
    archivo.write_text("VALOR = 1\n", encoding="utf-8")
    antes = _hash(archivo)

    resultado = reparar_lint(
        raiz, rutas=["modulo.py"], arreglar=lambda _s: pytest.fail("no debía llamarse")
    )

    assert resultado.estado == "sin_problemas"
    assert resultado.archivos_modificados == ()
    assert _hash(archivo) == antes


# --------------------------------------------------------------------- #
# JS/TS y linters sin arreglo automático.
# --------------------------------------------------------------------- #


def test_eslint_arregla_solo_en_un_proyecto_js(tmp_path: Path):
    raiz = _proyecto_js(tmp_path)
    archivo = raiz / "index.js"
    archivo.write_text("if (a == b) {\n  hacer();\n}\n", encoding="utf-8")

    llamadas: list[SolicitudDeArreglo] = []
    resultado = reparar_lint(raiz, rutas=["index.js"], arreglar=llamadas.append)

    assert resultado.linter == "eslint"
    assert resultado.estado == "arreglado_por_el_linter"
    assert llamadas == []
    assert archivo.read_text(encoding="utf-8").startswith("if (a === b)")
    assert resultado.archivos_modificados == ("index.js",)


def test_eslint_separa_errores_de_avisos_al_parsear_su_json(tmp_path: Path):
    raiz = _proyecto_js(tmp_path)
    (raiz / "index.js").write_text("debugger;\n", encoding="utf-8")

    informe = revisar_lint(raiz)

    assert informe is not None
    assert [problema.codigo for problema in informe.problemas] == ["no-debugger"]
    assert informe.problemas[0].severidad == "error"
    assert informe.problemas[0].archivo == "index.js"


def test_un_linter_que_exige_ruta_no_devuelve_un_visto_bueno_falso(tmp_path: Path):
    """eslint 8 sin patrón de archivos no revisa nada y sale con 0: eso es mentira.

    Es el peor fallo posible para esta pieza -- un "todo limpio" que nadie va
    a cuestionar -- así que la invocación siempre lleva objetivo.
    """
    raiz = _proyecto_js(tmp_path)
    (raiz / "index.js").write_text("debugger;\n", encoding="utf-8")

    informe = revisar_lint(raiz)
    # `todo_el_proyecto` es el único camino que deja la invocación sin `rutas`,
    # que es justo el que necesita el objetivo de respaldo.
    resultado = reparar_lint(raiz, todo_el_proyecto=True)

    assert informe is not None
    assert informe.argv[-1] == "."
    assert informe.limpio is False
    assert [problema.codigo for problema in informe.problemas] == ["no-debugger"]
    assert resultado.estado == "quedan_problemas"


def test_los_avisos_de_eslint_tambien_llegan_al_modelo(tmp_path: Path):
    """Un aviso no cambia el código de salida; sin cuidarlo el modelo nunca se entera.

    Y es el caso normal, no el raro: en ``next/core-web-vitals`` casi todas
    las reglas son avisos.
    """
    raiz = _proyecto_js(tmp_path)
    archivo = raiz / "vista.jsx"
    archivo.write_text("export const V = () => <img src='/a.png' />;\n", encoding="utf-8")

    solicitudes: list[SolicitudDeArreglo] = []

    def arreglar(solicitud: SolicitudDeArreglo) -> None:
        solicitudes.append(solicitud)
        if len(solicitudes) == 1:
            # Todavía no lo arregla: solo lo corre de línea, para que el error
            # no sea idéntico y haya una segunda vuelta -- la que ya no lee el
            # JSON de la revisión sino la salida de texto del bucle.
            archivo.write_text("\n" + archivo.read_text(encoding="utf-8"), encoding="utf-8")
            return
        archivo.write_text("export const V = () => <b>ok</b>;\n", encoding="utf-8")

    resultado = reparar_lint(raiz, rutas=["vista.jsx"], arreglar=arreglar)

    assert resultado.estado == "arreglado_con_ayuda_del_modelo"
    assert len(solicitudes) == 2
    assert solicitudes[0].problemas[0].severidad == "aviso"
    # La segunda vuelta sale del formato de texto y trae lo mismo de útil: la
    # regla de verdad (no la primera palabra del mensaje) y el archivo.
    assert solicitudes[1].problemas[0].codigo == "jsx-a11y/alt-text"
    assert solicitudes[1].problemas[0].severidad == "aviso"
    assert solicitudes[1].archivos == ("vista.jsx",)
    assert "vista.jsx" in solicitudes[1].instrucciones


def test_un_linter_sin_fix_va_directo_al_modelo(tmp_path: Path):
    """flake8 no arregla nada: acá el modelo no es un desperdicio, es la única vía."""
    raiz = tmp_path / "proyecto_flake8"
    raiz.mkdir()
    (raiz / ".flake8").write_text("[flake8]\nmax-line-length = 40\n", encoding="utf-8")
    _instalar_binario(raiz / ".venv" / "bin" / "flake8", _FLAKE8)
    archivo = raiz / "largo.py"
    archivo.write_text("VALOR = 'x' * 100  # " + "y" * 40 + "\n", encoding="utf-8")

    solicitudes: list[SolicitudDeArreglo] = []

    def arreglar(solicitud: SolicitudDeArreglo) -> None:
        solicitudes.append(solicitud)
        archivo.write_text("VALOR = 'x' * 100\n", encoding="utf-8")

    resultado = reparar_lint(raiz, rutas=["largo.py"], arreglar=arreglar)

    assert resultado.linter == "flake8"
    assert resultado.estado == "arreglado_con_ayuda_del_modelo"
    assert resultado.resueltos_por_el_linter == 0
    assert resultado.intentos_del_modelo == 1
    # El parser genérico de "archivo:línea:columna: CÓDIGO mensaje" hizo su trabajo.
    assert solicitudes[0].problemas[0].codigo == "E501"
    assert solicitudes[0].problemas[0].archivo == "largo.py"


def _monorepo(tmp_path: Path) -> Path:
    raiz = tmp_path / "monorepo"
    raiz.mkdir()
    (raiz / "pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")
    (raiz / "package.json").write_text('{"devDependencies": {"eslint": "^9"}}', encoding="utf-8")
    _instalar_binario(
        raiz / ".venv" / "bin" / "ruff",
        _RUFF.format(acepta_output_format=True, rompe_al_arreglar=False),
    )
    _instalar_binario(raiz / "node_modules" / ".bin" / "eslint", _ESLINT)
    (raiz / "modulo.py").write_text("import os\nVALOR = 1\n", encoding="utf-8")
    (raiz / "index.js").write_text("if (a == b) {}\n", encoding="utf-8")
    return raiz


def test_reparar_todo_el_lint_cubre_los_dos_lenguajes(tmp_path: Path):
    """Con el permiso explícito, los dos linters trabajan sobre todo el repo."""
    raiz = _monorepo(tmp_path)

    resultados = reparar_todo_el_lint(raiz, todo_el_proyecto=True)

    assert [resultado.linter for resultado in resultados] == ["ruff", "eslint"]
    assert all(resultado.estado == "arreglado_por_el_linter" for resultado in resultados)
    assert "import os" not in (raiz / "modulo.py").read_text(encoding="utf-8")
    assert "===" in (raiz / "index.js").read_text(encoding="utf-8")


@sin_git
def test_reparar_todo_el_lint_le_da_a_cada_linter_su_parte_del_alcance(tmp_path: Path):
    """El de Python trabaja porque cambió un .py; el de JS no, porque no cambió ninguno."""
    raiz = _monorepo(tmp_path)
    _repo_git(raiz)
    (raiz / "modulo.py").write_text("import os\nVALOR = 2\n", encoding="utf-8")
    js_antes = (raiz / "index.js").read_text(encoding="utf-8")

    resultados = reparar_todo_el_lint(raiz)

    por_linter = {resultado.linter: resultado for resultado in resultados}
    assert por_linter["ruff"].estado == "arreglado_por_el_linter"
    assert por_linter["ruff"].archivos_del_alcance == ("modulo.py",)
    assert por_linter["eslint"].estado == "sin_problemas"
    assert (raiz / "index.js").read_text(encoding="utf-8") == js_antes


def test_reparar_todo_el_lint_sin_alcance_no_toca_nada(tmp_path: Path):
    raiz = _monorepo(tmp_path)
    antes = {archivo: _hash(archivo) for archivo in (raiz / "modulo.py", raiz / "index.js")}

    resultados = reparar_todo_el_lint(raiz)

    assert [resultado.estado for resultado in resultados] == ["sin_alcance", "sin_alcance"]
    assert all(_hash(archivo) == hash_ for archivo, hash_ in antes.items())


# --------------------------------------------------------------------- #
# Bordes.
# --------------------------------------------------------------------- #


def test_un_linter_de_version_vieja_cae_a_la_bandera_anterior(tmp_path: Path):
    """La bandera moderna no existe en esa versión: se prueba la de antes."""
    raiz = _proyecto_python(tmp_path, acepta_output_format=False)
    (raiz / "modulo.py").write_text("import os\n", encoding="utf-8")

    informe = revisar_lint(raiz)

    assert informe is not None
    assert "--format" in informe.argv
    assert [problema.codigo for problema in informe.problemas] == ["F401"]


def test_un_linter_roto_no_se_confunde_con_codigo_sucio(tmp_path: Path):
    raiz = _proyecto_python(tmp_path, con_ruff=False)
    _instalar_binario(raiz / ".venv" / "bin" / "ruff", _LINTER_ROTO)
    archivo = raiz / "modulo.py"
    archivo.write_text("import os\n", encoding="utf-8")

    resultado = reparar_lint(
        raiz, rutas=["modulo.py"], arreglar=lambda _s: pytest.fail("no debía llamarse")
    )

    assert resultado.estado == "el_linter_fallo"
    assert "configuration" in resultado.mensaje
    # No se tocó nada: mandar al modelo a arreglar código sano por una
    # configuración rota es peor que no hacer nada.
    assert archivo.read_text(encoding="utf-8") == "import os\n"


def test_un_linter_que_se_rompe_despues_del_fix_tampoco_llega_al_modelo(tmp_path: Path):
    """El `--fix` corrió y la revisión siguiente ya no pudo leer nada.

    Ese estado se veía como "quedan problemas" con la lista vacía, y con eso
    se mandaba al modelo a arreglar una lista de cero problemas.
    """
    raiz = _proyecto_python(tmp_path, rompe_al_arreglar=True)
    archivo = raiz / "modulo.py"
    archivo.write_text("import os\nx = variable_indefinida\n", encoding="utf-8")

    resultado = reparar_lint(
        raiz, rutas=["modulo.py"], arreglar=lambda _s: pytest.fail("no debía llamarse")
    )

    assert resultado.estado == "el_linter_fallo"
    assert resultado.limpio is False
    assert resultado.intentos_del_modelo == 0
    assert "configuration" in resultado.mensaje
    # El `--fix` alcanzó a tocar el archivo, y eso se reporta igual: el
    # informe tiene que decir qué quedó movido en el disco.
    assert resultado.archivos_modificados == ("modulo.py",)


def test_un_linter_que_se_rompe_mientras_el_modelo_trabaja_se_reporta_como_fallo(
    tmp_path: Path,
):
    raiz = _proyecto_python(tmp_path)
    archivo = raiz / "modulo.py"
    archivo.write_text("x = variable_indefinida\n", encoding="utf-8")

    def arreglar_rompiendo(_solicitud: SolicitudDeArreglo) -> None:
        # El "arreglo" deja la configuración del linter inservible.
        (raiz / "ROTO_A_PROPOSITO").write_text("roto", encoding="utf-8")

    resultado = reparar_lint(raiz, rutas=["modulo.py"], arreglar=arreglar_rompiendo)

    assert resultado.estado == "el_linter_fallo"
    assert resultado.limpio is False
    # Y no se inventa un "quedan 0 problemas" que sonaría a casi limpio.
    assert "0 problema" not in resultado.mensaje


def test_rutas_acotan_el_arreglo_a_lo_que_se_edito(tmp_path: Path):
    raiz = _proyecto_python(tmp_path)
    tocado = raiz / "tocado.py"
    tocado.write_text("import os\nVALOR = 1\n", encoding="utf-8")
    intacto = raiz / "intacto.py"
    intacto.write_text("import os\nOTRO = 2\n", encoding="utf-8")
    antes = _hash(intacto)

    resultado = reparar_lint(raiz, rutas=["tocado.py"])

    assert resultado.estado == "arreglado_por_el_linter"
    assert resultado.archivos_modificados == ("tocado.py",)
    # El archivo que nadie editó en esta sesión sigue igual, aunque tenga el
    # mismo problema: `rutas` es el límite del cambio.
    assert _hash(intacto) == antes


def test_un_directorio_inexistente_es_un_error_de_uso(tmp_path: Path):
    with pytest.raises(IDELintError):
        reparar_lint(tmp_path / "no_existe")


def test_pedir_un_linter_que_el_proyecto_no_usa_no_hace_nada(tmp_path: Path):
    raiz = _proyecto_python(tmp_path)
    (raiz / "modulo.py").write_text("import os\n", encoding="utf-8")

    resultado = reparar_lint(raiz, linter="eslint")

    assert resultado.estado == "sin_linter"
    assert "eslint" in resultado.mensaje
    assert (raiz / "modulo.py").read_text(encoding="utf-8") == "import os\n"


def test_el_resumen_es_serializable(tmp_path: Path):
    raiz = _proyecto_python(tmp_path)
    (raiz / "modulo.py").write_text("import os\n", encoding="utf-8")

    resumen = reparar_lint(raiz, rutas=["modulo.py"]).resumen()

    assert resumen["estado"] == "arreglado_por_el_linter"
    assert resumen["linter"] == "ruff"
    assert resumen["alcance_respetado"] is True
    assert resumen["problemas_iniciales"][0]["codigo"] == "F401"
