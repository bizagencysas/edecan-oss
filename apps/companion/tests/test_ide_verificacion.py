"""Pruebas de ``ide_verificacion.py``: el bucle que no se rinde.

Cubren, en orden, lo que el módulo promete:
- pasa a la primera (no hace falta ningún arreglo);
- falla y se arregla (el "arreglar" corrige de verdad, segundo intento pasa);
- el mismo error repetido dos veces seguidas corta el bucle en vez de
  seguir intentando lo mismo;
- el tope de intentos frena un "arreglar" que solo produce errores distintos
  entre sí (nunca dispara la detección de error repetido);
- un comando que no existe se clasifica como "no se pudo ejecutar", no como
  "fallo de verificación", y el bucle no insiste sin una función de arreglo;
- salidas enormes se recortan a lo accionable, no se vuelcan enteras;
- detección del comando de verificación a partir de archivos del proyecto.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest
from edecan_companion.ide_verificacion import (
    ResultadoIntento,
    _detectar_dependencia_faltante,
    detectar_comando_de_verificacion,
    ejecutar_hasta_que_pase,
    ejecutar_intento,
    extraer_resumen,
)


def _script_con_marcador(tmp_path: Path, *, marcador: str) -> Path:
    """Un "programa de verificación" de juguete: falla hasta que exista `marcador`.

    Se usa un script real de Python en vez de mockear subprocess para que
    las pruebas ejerzan el camino real (spawn de proceso, exit code, stdout)
    tal como lo haría un pytest/npm test real.
    """
    script = tmp_path / "verificar.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import sys
            from pathlib import Path

            marcador = Path({str(marcador)!r})
            if marcador.exists():
                print("1 passed in 0.01s")
                sys.exit(0)
            print("FAILED tests/test_x.py::test_x - AssertionError: esperaba 1, llegó 2")
            print("=" * 10 + " short test summary info " + "=" * 10)
            print("FAILED tests/test_x.py::test_x - AssertionError: esperaba 1, llegó 2")
            print("=" * 10 + " 1 failed in 0.02s " + "=" * 10)
            sys.exit(1)
            """
        ),
        encoding="utf-8",
    )
    return script


def test_pasa_a_la_primera_no_llama_arreglar(tmp_path: Path):
    marcador = tmp_path / "ya_arreglado"
    marcador.write_text("", encoding="utf-8")  # ya existe: el script pasa de una
    script = _script_con_marcador(tmp_path, marcador=str(marcador))

    llamadas_arreglar = []
    resultado = ejecutar_hasta_que_pase(
        [sys.executable, str(script)],
        cwd=tmp_path,
        arreglar=lambda intento: llamadas_arreglar.append(intento),
    )

    assert resultado.aprobado is True
    assert resultado.detenido_por == "aprobado"
    assert len(resultado.intentos) == 1
    assert llamadas_arreglar == []


def test_falla_y_se_arregla_pasa_en_el_segundo_intento(tmp_path: Path):
    marcador = tmp_path / "arreglado_por_el_callback"
    script = _script_con_marcador(tmp_path, marcador=str(marcador))
    assert not marcador.exists()

    def arreglar(intento: ResultadoIntento) -> None:
        assert intento.tipo_falla == "fallo_de_verificacion"
        assert intento.resumen_error is not None
        assert "AssertionError" in intento.resumen_error.texto
        marcador.write_text("", encoding="utf-8")

    resultado = ejecutar_hasta_que_pase(
        [sys.executable, str(script)], cwd=tmp_path, arreglar=arreglar
    )

    assert resultado.aprobado is True
    assert resultado.detenido_por == "aprobado"
    assert len(resultado.intentos) == 2
    assert resultado.intentos[0].aprobado is False
    assert resultado.intentos[1].aprobado is True


def test_error_identico_repetido_corta_el_bucle(tmp_path: Path):
    marcador = tmp_path / "nunca_existe"
    script = _script_con_marcador(tmp_path, marcador=str(marcador))

    # "arreglar" que en realidad no cambia nada que le importe al comando:
    # el error va a ser exactamente el mismo en cada intento.
    resultado = ejecutar_hasta_que_pase(
        [sys.executable, str(script)],
        cwd=tmp_path,
        arreglar=lambda intento: None,
        max_intentos=10,
    )

    assert resultado.aprobado is False
    assert resultado.detenido_por == "error_repetido"
    # Se corta apenas se confirma la repetición (2 intentos con la misma
    # firma), mucho antes del tope de 10.
    assert len(resultado.intentos) == 2
    assert resultado.intentos[0].firma == resultado.intentos[1].firma


def test_tope_de_intentos_frena_un_arreglar_que_siempre_falla_distinto(tmp_path: Path):
    contador = tmp_path / "contador"
    contador.write_text("0", encoding="utf-8")
    script = tmp_path / "verificar_variable.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import sys
            from pathlib import Path

            contador = Path({str(contador)!r})
            valor = contador.read_text().strip()
            print(f"fallo número {{valor}}")
            sys.exit(1)
            """
        ),
        encoding="utf-8",
    )

    def arreglar(intento: ResultadoIntento) -> None:
        # Cada "arreglo" cambia el contador -- el error de salida es
        # DISTINTO cada vez, así que nunca dispara "error_repetido"; el
        # único freno posible es el tope de intentos.
        valor = int(contador.read_text().strip())
        contador.write_text(str(valor + 1), encoding="utf-8")

    resultado = ejecutar_hasta_que_pase(
        [sys.executable, str(script)],
        cwd=tmp_path,
        arreglar=arreglar,
        max_intentos=4,
    )

    assert resultado.aprobado is False
    assert resultado.detenido_por == "tope_de_intentos"
    assert len(resultado.intentos) == 4
    firmas = {intento.firma for intento in resultado.intentos}
    assert len(firmas) == 4  # las cuatro firmas fueron distintas entre sí


def test_comando_inexistente_se_clasifica_como_no_se_pudo_ejecutar(tmp_path: Path):
    resultado = ejecutar_intento(
        ["este-comando-no-existe-en-ningun-path-9c3f"], cwd=tmp_path
    )

    assert resultado.aprobado is False
    assert resultado.tipo_falla == "no_se_pudo_ejecutar"
    assert resultado.motivo_no_ejecutable is not None
    assert "no se encontró" in resultado.motivo_no_ejecutable.casefold()
    assert resultado.exit_code is None


def test_bucle_con_comando_inexistente_y_sin_arreglar_se_detiene_tras_un_intento(
    tmp_path: Path,
):
    resultado = ejecutar_hasta_que_pase(
        ["este-comando-no-existe-en-ningun-path-9c3f"], cwd=tmp_path
    )

    assert resultado.aprobado is False
    assert resultado.detenido_por == "sin_funcion_de_arreglo"
    assert len(resultado.intentos) == 1
    assert resultado.intentos[0].tipo_falla == "no_se_pudo_ejecutar"


def test_dependencia_faltante_se_distingue_de_fallo_de_pruebas(tmp_path: Path):
    """Un ImportError real (proceso arrancó, no pudo ni recolectar) no es lo
    mismo que un assert fallido: el arreglo es instalar/crear algo, no
    corregir lógica de negocio."""
    script = tmp_path / "falta_dependencia.py"
    script.write_text(
        "raise ModuleNotFoundError(\"No module named 'paquete_que_no_existe'\")\n",
        encoding="utf-8",
    )

    resultado = ejecutar_intento([sys.executable, str(script)], cwd=tmp_path)

    assert resultado.aprobado is False
    assert resultado.tipo_falla == "no_se_pudo_ejecutar"
    assert "dependencia" in (resultado.motivo_no_ejecutable or "").casefold()


def test_salida_enorme_se_recorta_a_lo_accionable(tmp_path: Path):
    script = tmp_path / "salida_enorme.py"
    script.write_text(
        textwrap.dedent(
            """
            import sys

            for i in range(5000):
                print(f"línea de relleno sin valor número {i}")
            print("=" * 10 + " short test summary info " + "=" * 10)
            print("FAILED tests/test_grande.py::test_algo - ValueError: x invalido")
            print("=" * 10 + " 1 failed in 3.14s " + "=" * 10)
            sys.exit(1)
            """
        ),
        encoding="utf-8",
    )

    resultado = ejecutar_intento([sys.executable, str(script)], cwd=tmp_path)

    assert resultado.aprobado is False
    assert resultado.resumen_error is not None
    # El resumen nunca contiene las 5000 líneas de relleno...
    assert "línea de relleno" not in resultado.resumen_error.texto
    # ...pero sí lo accionable: el archivo y la causa concreta del fallo.
    assert "test_grande.py" in resultado.resumen_error.texto
    assert "ValueError" in resultado.resumen_error.texto
    assert len(resultado.resumen_error.texto) < 4500


def test_extraer_resumen_reconoce_error_de_compilador_con_archivo_y_linea():
    stdout = ""
    stderr = (
        "src/index.ts:42:5 - error TS2345: Argument of type 'string' is not "
        "assignable to parameter of type 'number'.\n"
        "Found 1 error.\n"
    )
    resumen = extraer_resumen(stdout, stderr)

    assert resumen.fuente == "compilador"
    assert "src/index.ts" in resumen.archivos_involucrados
    assert "TS2345" in resumen.texto


def test_extraer_resumen_de_salida_vacia_no_revienta():
    resumen = extraer_resumen("", "")
    assert resumen.fuente == "vacio"
    assert resumen.texto == ""
    assert resumen.truncado is False


def test_detectar_comando_prefiere_npm_test_si_existe_script_real(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        '{"scripts": {"test": "vitest run", "build": "tsc"}}', encoding="utf-8"
    )
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")

    assert detectar_comando_de_verificacion(tmp_path) == ["npm", "test"]


def test_detectar_comando_ignora_el_placeholder_de_npm_init(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        '{"scripts": {"test": "echo \\"Error: no test specified\\" && exit 1"}}',
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")

    assert detectar_comando_de_verificacion(tmp_path) == ["python", "-m", "pytest", "-q"]


def test_detectar_comando_pytest_por_pyproject(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")

    assert detectar_comando_de_verificacion(
        tmp_path, interprete_python=sys.executable
    ) == [sys.executable, "-m", "pytest", "-q"]


def test_detectar_comando_make_test_si_no_hay_nada_mas(tmp_path: Path):
    (tmp_path / "Makefile").write_text(
        "build:\n\techo build\ntest:\n\techo test\n", encoding="utf-8"
    )

    assert detectar_comando_de_verificacion(tmp_path) == ["make", "test"]


def test_detectar_comando_devuelve_none_si_no_reconoce_nada(tmp_path: Path):
    (tmp_path / "README.md").write_text("nada que reconocer aquí\n", encoding="utf-8")

    assert detectar_comando_de_verificacion(tmp_path) is None


def test_ejecutar_intento_rechaza_interprete_de_shell(tmp_path: Path):
    with pytest.raises(ValueError, match="shell"):
        ejecutar_intento(["bash", "-c", "echo hola"], cwd=tmp_path)


def test_dos_dependencias_distintas_no_comparten_firma() -> None:
    """La firma tiene que distinguir a qué paquete le falta qué.

    `_detectar_dependencia_faltante` devolvía `group(0)` de patrones que eran
    literales sin captura, así que a `yaml` y a `requests` les salía la misma
    cadena: "ModuleNotFoundError". Ese valor viaja a `firma` (ver
    `_firma_de_resultado`), y todo lo que indexe por firma trata entonces dos
    problemas distintos como el mismo. `ide_autocritica` guarda la lección de un
    error bajo su firma: con firmas colisionadas le servía a un repo al que le
    falta `requests` el arreglo aprendido en otro al que le faltaba `pyyaml` --
    con la confianza intacta, porque cada acierto ajeno reforzaba la misma clave.

    Es justo la familia de errores que más se repite ENTRE proyectos, o sea la
    que sostiene el alcance global de esa memoria.
    """
    falta_yaml = _detectar_dependencia_faltante(
        "Traceback (most recent call last):\nModuleNotFoundError: No module named 'yaml'"
    )
    falta_requests = _detectar_dependencia_faltante(
        "Traceback (most recent call last):\nModuleNotFoundError: No module named 'requests'"
    )

    assert falta_yaml == "yaml"
    assert falta_requests == "requests"
    assert falta_yaml != falta_requests


def test_el_shell_no_se_confunde_con_el_comando_que_falta() -> None:
    """zsh pone el faltante DESPUÉS (`zsh: command not found: rg`) y bash ANTES
    (`bash: rg: command not found`). Con el orden de patrones equivocado, la
    línea de zsh entrega "zsh" -- el shell -- como si fuera el programa ausente,
    y entonces todo comando faltante bajo zsh comparte una sola firma."""
    assert _detectar_dependencia_faltante("zsh: command not found: rg") == "rg"
    assert _detectar_dependencia_faltante("bash: rg: command not found") == "rg"
