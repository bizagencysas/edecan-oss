"""Pruebas de ``ide_reglas_verificables``.

Cada regla del catálogo se prueba con los tres desenlaces que el diseño exige:
uno que cumple, uno que no, y uno donde la regla NO APLICA -- ese último es el
que evita que la pieza degenere en un reporte que nadie lee.

Los literales que las reglas buscan (marcadores de conflicto, ``debugger``,
credenciales, rutas del home) se arman por partes o desde el entorno. No es
manía: este archivo vive dentro del repo de Edecán, y escribirlos enteros haría
que Edecán se reportara a sí mismo la próxima vez que verifique su propio
workspace.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from edecan_companion import ide_reglas_verificables as modulo
from edecan_companion.ide_reglas_verificables import (
    CATALOGO,
    Alcance,
    AmbitoDeRegla,
    ContextoDeChequeo,
    EstadoRegla,
    Evidencia,
    IDEReglasVerificablesError,
    OrigenDelAlcance,
    ReglaVerificable,
    ReporteVerificacion,
    Veredicto,
    alcance_completo,
    alcance_de_archivos,
    alcance_de_la_sesion,
    punto_de_partida,
    verificar_todas,
)

# --- literales armados por partes ---------------------------------------

MARCADOR_INICIO = "<" * 7
MARCADOR_MEDIO = "=" * 7
MARCADOR_FIN = ">" * 7
PUNTO_DEPURACION_PY = "break" + "point()"
PUNTO_DEPURACION_TS = "debug" + "ger;"
CABECERA_CLAVE = "-----BEGIN " + "PRIVATE KEY-----"
# 16+ caracteres con dígitos y sin ninguna marca de "esto es de mentira".
VALOR_SECRETO = "ab12cd34ef56gh78ij90"


def regla(regla_id: str) -> ReglaVerificable:
    return next(item for item in CATALOGO if item.id == regla_id)


def veredicto_de(raiz: Path, regla_id: str):
    return regla(regla_id).verificar(raiz)


def git(raiz: Path, *argumentos: str) -> None:
    subprocess.run(["git", "-C", str(raiz), *argumentos], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git no está instalado en esta máquina.")
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "companion@example.com")
    git(tmp_path, "config", "user.name", "Companion")
    return tmp_path


@pytest.fixture
def repo_con_base(repo: Path) -> Path:
    """Un repo con un commit, que es lo que hace falta para acotar por diff."""

    (repo / "LEEME.md").write_text("proyecto\n", encoding="utf-8")
    git(repo, "add", "LEEME.md")
    git(repo, "commit", "-qm", "inicio")
    return repo


# --- forma del catálogo --------------------------------------------------


def test_el_catalogo_tiene_ids_unicos_y_descripciones_de_una_linea():
    ids = [item.id for item in CATALOGO]
    assert len(ids) == len(set(ids))
    for item in CATALOGO:
        assert "\n" not in item.descripcion
        assert item.descripcion.endswith(".")
        assert len(item.descripcion) <= 120


def test_el_catalogo_es_corto_a_proposito():
    # Diez que se verifican solas valen más que cien con metadatos. Si alguien
    # agrega la número quince, que sea una decisión y no un descuido.
    assert 4 <= len(CATALOGO) <= 8


def test_raiz_invalida_es_error():
    with pytest.raises(IDEReglasVerificablesError):
        verificar_todas(Path("/no/existe/de/verdad/edecan-test"))


def test_raiz_que_es_archivo_es_error(tmp_path: Path):
    archivo = tmp_path / "no_es_carpeta.txt"
    archivo.write_text("hola", encoding="utf-8")
    with pytest.raises(IDEReglasVerificablesError):
        verificar_todas(archivo)


# --- regla: sin-credenciales-versionadas ---------------------------------

REGLA_CREDENCIALES = "sin-credenciales-versionadas"


def test_credenciales_no_aplica_en_workspace_vacio(tmp_path: Path):
    assert veredicto_de(tmp_path, REGLA_CREDENCIALES).estado is EstadoRegla.NO_APLICA


def test_credenciales_cumple_con_codigo_limpio(repo: Path):
    (repo / "servicio.py").write_text("VALOR = 3\n", encoding="utf-8")
    git(repo, "add", "servicio.py")

    resultado = veredicto_de(repo, REGLA_CREDENCIALES)

    assert resultado.estado is EstadoRegla.CUMPLE
    assert resultado.evidencia == ()


def test_credenciales_no_cumple_con_secreto_en_el_codigo(tmp_path: Path):
    (tmp_path / "servicio.py").write_text(f'api_key = "{VALOR_SECRETO}"\n', encoding="utf-8")

    resultado = veredicto_de(tmp_path, REGLA_CREDENCIALES)

    assert resultado.estado is EstadoRegla.NO_CUMPLE
    assert resultado.evidencia[0].ruta == "servicio.py"
    assert resultado.evidencia[0].linea == 1
    # La evidencia ubica, nunca cita: el valor no puede terminar en el prompt.
    assert VALOR_SECRETO not in resultado.evidencia[0].detalle


def test_credenciales_no_cumple_con_env_versionado(repo: Path):
    (repo / ".env").write_text("TOKEN=lo-que-sea\n", encoding="utf-8")
    git(repo, "add", "-f", ".env")

    resultado = veredicto_de(repo, REGLA_CREDENCIALES)

    assert resultado.estado is EstadoRegla.NO_CUMPLE
    assert [item.ruta for item in resultado.evidencia] == [".env"]


def test_credenciales_ignora_el_env_que_git_si_ignora(repo: Path):
    # El caso que más importa no romper: el usuario hizo LO CORRECTO.
    (repo / ".gitignore").write_text(".env\n", encoding="utf-8")
    (repo / ".env").write_text(f'API_KEY="{VALOR_SECRETO}"\n', encoding="utf-8")
    (repo / "servicio.py").write_text("VALOR = 3\n", encoding="utf-8")
    git(repo, "add", ".gitignore", "servicio.py")

    assert veredicto_de(repo, REGLA_CREDENCIALES).estado is EstadoRegla.CUMPLE


def test_credenciales_ignora_un_archivo_de_codigo_que_git_ignora(repo: Path):
    """Este es el que de verdad ejercita el filtro de ``.gitignore``.

    El de arriba NO lo hace, aunque lo parezca: ``.env`` no tiene sufijo, así
    que el recorrido ni siquiera lo abre y el veredicto sale limpio aunque
    ``ignoradas_por_git`` devolviera vacío. Hace falta un archivo con extensión
    de código -- el caso real es un `config.local.js` con la clave de
    desarrollo de cada quien.
    """

    (repo / ".gitignore").write_text("config.local.js\n", encoding="utf-8")
    (repo / "config.local.js").write_text(
        f'export const apiKey = "{VALOR_SECRETO}";\n', encoding="utf-8"
    )
    git(repo, "add", ".gitignore")

    assert veredicto_de(repo, REGLA_CREDENCIALES).estado is EstadoRegla.CUMPLE

    # Y el mismo archivo, sin que git lo ignore, SÍ se reporta: si no, este
    # test pasaría igual por cualquier otra razón.
    (repo / ".gitignore").write_text("nada-que-ver\n", encoding="utf-8")
    assert veredicto_de(repo, REGLA_CREDENCIALES).estado is EstadoRegla.NO_CUMPLE


def test_credenciales_no_confunde_una_palabra_que_contiene_spec_o_demo(tmp_path: Path):
    # `especificaciones` contiene "spec" y `demografia` contiene "demo". Con el
    # filtro de archivos de prueba comparando por subcadena, los dos quedaban
    # fuera del escaneo y su credencial no se revisaba nunca.
    for nombre in ("especificaciones.py", "demografia.py", "latest_config.py"):
        (tmp_path / nombre).write_text(f'api_key = "{VALOR_SECRETO}"\n', encoding="utf-8")

    resultado = veredicto_de(tmp_path, REGLA_CREDENCIALES)

    assert resultado.estado is EstadoRegla.NO_CUMPLE
    assert {item.ruta for item in resultado.evidencia} == {
        "especificaciones.py",
        "demografia.py",
        "latest_config.py",
    }


def test_credenciales_sigue_saltandose_los_nombres_de_prueba_de_verdad(tmp_path: Path):
    for nombre in ("LlaveroTests.swift", "api.spec.ts", "conftest.py"):
        (tmp_path / nombre).write_text(f'api_key = "{VALOR_SECRETO}"\n', encoding="utf-8")

    assert veredicto_de(tmp_path, REGLA_CREDENCIALES).estado is EstadoRegla.CUMPLE


def test_credenciales_ve_el_secreto_de_un_json(tmp_path: Path):
    """El patrón heredado exige la clave SIN comillas, y en JSON siempre va CON.

    Un `credentials.json` con la llave de servicio adentro era invisible para
    esta regla, que es justo el archivo por el que más se filtran credenciales.
    """

    (tmp_path / "configuracion.json").write_text(
        json.dumps({"api_key": VALOR_SECRETO}), encoding="utf-8"
    )

    resultado = veredicto_de(tmp_path, REGLA_CREDENCIALES)

    assert resultado.estado is EstadoRegla.NO_CUMPLE
    assert resultado.evidencia[0].ruta == "configuracion.json"
    assert VALOR_SECRETO not in resultado.evidencia[0].detalle


def test_credenciales_ve_el_valor_sin_comillas_de_un_yaml(tmp_path: Path):
    (tmp_path / "ajustes.yml").write_text(
        f"servicio:\n  activo: true\naccess_token: {VALOR_SECRETO}\n", encoding="utf-8"
    )

    resultado = veredicto_de(tmp_path, REGLA_CREDENCIALES)

    assert resultado.estado is EstadoRegla.NO_CUMPLE
    assert resultado.evidencia[0].linea == 3


def test_credenciales_no_confunde_una_referencia_de_configuracion(tmp_path: Path):
    """La forma sin comillas es la que más falsos positivos puede dar.

    `password: settings.SECRET_KEY` calza con el patrón igual que un token: lo
    único que los separa es que un secreto de verdad casi siempre trae dígitos.
    """

    (tmp_path / "ajustes.yml").write_text(
        "password: settings.SECRET_KEY\napi_key: ${API_KEY_DEL_ENTORNO}\n", encoding="utf-8"
    )

    assert veredicto_de(tmp_path, REGLA_CREDENCIALES).estado is EstadoRegla.CUMPLE


def test_credenciales_no_lee_un_valor_suelto_como_secreto(tmp_path: Path):
    # En Python `secret = algo_largo_1` es una variable, no una credencial: la
    # forma sin comillas solo se busca en archivos de configuración.
    (tmp_path / "servicio.py").write_text(
        "secret = valor_calculado_en_arranque_1\n", encoding="utf-8"
    )

    assert veredicto_de(tmp_path, REGLA_CREDENCIALES).estado is EstadoRegla.CUMPLE


def test_credenciales_reporta_una_sola_vez_la_misma_linea(tmp_path: Path):
    # Tres formas de escribir lo mismo, una por archivo: tres hallazgos, no
    # seis. Una credencial repetida se lee como dos problemas donde hay uno.
    (tmp_path / "servicio.py").write_text(f'api_key = "{VALOR_SECRETO}"\n', encoding="utf-8")
    (tmp_path / "config.json").write_text(
        json.dumps({"client_secret": VALOR_SECRETO}), encoding="utf-8"
    )
    (tmp_path / "ajustes.yml").write_text(f"password: {VALOR_SECRETO}\n", encoding="utf-8")

    resultado = veredicto_de(tmp_path, REGLA_CREDENCIALES)

    assert resultado.total_hallazgos == 3
    assert len({(item.ruta, item.linea) for item in resultado.evidencia}) == 3


def test_las_claves_propias_no_se_separan_de_las_del_toolkit():
    """Los patrones nuevos heredan la lista de palabras clave de `seguridad`.

    Si alguien agrega ahí `bearer[_-]?token`, esta regla tiene que heredarlo.
    Comprobar que la alternación sigue siendo literalmente la misma es la forma
    barata de enterarse el día que dejen de estarlo.
    """

    from edecan_toolkit.seguridad import _CONTENT_RULES

    heredado = next(patron for _, regla, patron, _ in _CONTENT_RULES if regla == "hardcoded-secret")

    assert modulo._CLAVES_DE_SECRETO in heredado.pattern
    assert modulo._CUERPO_DE_SECRETO in heredado.pattern


def test_credenciales_ignora_la_plantilla_de_env(repo: Path):
    (repo / ".env.example").write_text("API_KEY=\n", encoding="utf-8")
    git(repo, "add", ".env.example")

    assert veredicto_de(repo, REGLA_CREDENCIALES).estado is EstadoRegla.CUMPLE


def test_credenciales_ignora_los_fixtures_de_tests(tmp_path: Path):
    carpeta = tmp_path / "tests"
    carpeta.mkdir()
    (carpeta / "test_api.py").write_text(f'api_key = "{VALOR_SECRETO}"\n', encoding="utf-8")

    assert veredicto_de(tmp_path, REGLA_CREDENCIALES).estado is EstadoRegla.CUMPLE


def test_credenciales_ignora_identificadores_sin_digitos(tmp_path: Path):
    # `accessToken = "cc.edecan.app.accessToken"` es el NOMBRE de una llave del
    # llavero, no su valor. Medido: era falso positivo en el repo real.
    (tmp_path / "Llavero.swift").write_text(
        'let accessToken = "cc.edecan.app.accessToken"\n', encoding="utf-8"
    )

    assert veredicto_de(tmp_path, REGLA_CREDENCIALES).estado is EstadoRegla.CUMPLE


def test_credenciales_ignora_la_cabecera_pem_sin_cuerpo(tmp_path: Path):
    (tmp_path / "validador.py").write_text(f'CABECERA = "{CABECERA_CLAVE}"\n', encoding="utf-8")

    assert veredicto_de(tmp_path, REGLA_CREDENCIALES).estado is EstadoRegla.CUMPLE


def test_credenciales_no_cumple_con_clave_privada_de_verdad(tmp_path: Path):
    cuerpo = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQ" * 3
    (tmp_path / "cliente.py").write_text(
        f'CLAVE = """{CABECERA_CLAVE}\n{cuerpo}\n"""\n', encoding="utf-8"
    )

    assert veredicto_de(tmp_path, REGLA_CREDENCIALES).estado is EstadoRegla.NO_CUMPLE


def test_credenciales_reporta_error_si_no_hay_detector(tmp_path: Path, monkeypatch):
    (tmp_path / "servicio.py").write_text("VALOR = 3\n", encoding="utf-8")
    import builtins

    original = builtins.__import__

    def sin_toolkit(nombre, *args, **kwargs):
        if nombre.startswith("edecan_toolkit"):
            raise ModuleNotFoundError(nombre)
        return original(nombre, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", sin_toolkit)

    resultado = veredicto_de(tmp_path, REGLA_CREDENCIALES)

    # Sin detector no se sabe; decir "cumple" sería mentir.
    assert resultado.estado is EstadoRegla.ERROR
    assert "detector de credenciales" in resultado.mensaje


# --- regla: sin-generados-versionados ------------------------------------

REGLA_GENERADOS = "sin-generados-versionados"


def test_generados_no_aplica_sin_git(tmp_path: Path):
    (tmp_path / "servicio.py").write_text("VALOR = 3\n", encoding="utf-8")

    resultado = veredicto_de(tmp_path, REGLA_GENERADOS)

    assert resultado.estado is EstadoRegla.NO_APLICA
    assert resultado.evidencia == ()


def test_generados_cumple_con_indice_limpio(repo: Path):
    (repo / "servicio.py").write_text("VALOR = 3\n", encoding="utf-8")
    git(repo, "add", "servicio.py")

    assert veredicto_de(repo, REGLA_GENERADOS).estado is EstadoRegla.CUMPLE


def test_generados_no_cumple_con_node_modules_versionado(repo: Path):
    dependencia = repo / "node_modules" / "izq"
    dependencia.mkdir(parents=True)
    (dependencia / "index.js").write_text("module.exports = 1;\n", encoding="utf-8")
    git(repo, "add", "-f", "node_modules/izq/index.js")

    resultado = veredicto_de(repo, REGLA_GENERADOS)

    assert resultado.estado is EstadoRegla.NO_CUMPLE
    assert resultado.evidencia[0].ruta == "node_modules/izq/index.js"
    assert resultado.evidencia[0].linea is None


def test_generados_no_cumple_con_pyc_versionado(repo: Path):
    (repo / "modulo.pyc").write_bytes(b"\x00\x01")
    git(repo, "add", "-f", "modulo.pyc")

    assert veredicto_de(repo, REGLA_GENERADOS).estado is EstadoRegla.NO_CUMPLE


# --- regla: sin-marcadores-de-conflicto ----------------------------------

REGLA_CONFLICTOS = "sin-marcadores-de-conflicto"


def test_conflictos_no_aplica_sin_codigo(tmp_path: Path):
    (tmp_path / "LEEME.md").write_text("solo documentación\n", encoding="utf-8")

    assert veredicto_de(tmp_path, REGLA_CONFLICTOS).estado is EstadoRegla.NO_APLICA


def test_conflictos_cumple_con_codigo_resuelto(tmp_path: Path):
    (tmp_path / "servicio.py").write_text("VALOR = 3\n", encoding="utf-8")

    assert veredicto_de(tmp_path, REGLA_CONFLICTOS).estado is EstadoRegla.CUMPLE


def test_conflictos_no_cumple_con_las_tres_marcas(tmp_path: Path):
    contenido = (
        "def total():\n"
        f"{MARCADOR_INICIO} HEAD\n"
        "    return 1\n"
        f"{MARCADOR_MEDIO}\n"
        "    return 2\n"
        f"{MARCADOR_FIN} rama\n"
    )
    (tmp_path / "servicio.py").write_text(contenido, encoding="utf-8")

    resultado = veredicto_de(tmp_path, REGLA_CONFLICTOS)

    assert resultado.estado is EstadoRegla.NO_CUMPLE
    assert resultado.evidencia[0].ruta == "servicio.py"
    assert resultado.evidencia[0].linea == 2


def test_conflictos_no_se_inventa_uno_con_una_sola_marca(tmp_path: Path):
    # Una línea de `=` separando secciones es estilo, no un conflicto.
    (tmp_path / "servicio.py").write_text(f"# {MARCADOR_MEDIO}\nVALOR = 3\n", encoding="utf-8")

    assert veredicto_de(tmp_path, REGLA_CONFLICTOS).estado is EstadoRegla.CUMPLE


def test_conflictos_no_mira_la_documentacion(tmp_path: Path):
    # Un README que EXPLICA cómo se ve un conflicto no es un conflicto.
    (tmp_path / "servicio.py").write_text("VALOR = 3\n", encoding="utf-8")
    (tmp_path / "LEEME.md").write_text(
        f"{MARCADOR_INICIO} HEAD\na\n{MARCADOR_MEDIO}\nb\n{MARCADOR_FIN} rama\n",
        encoding="utf-8",
    )

    assert veredicto_de(tmp_path, REGLA_CONFLICTOS).estado is EstadoRegla.CUMPLE


# --- regla: sin-puntos-de-depuracion -------------------------------------

REGLA_DEPURACION = "sin-puntos-de-depuracion"


def test_depuracion_no_aplica_en_repo_de_otro_lenguaje(tmp_path: Path):
    (tmp_path / "main.go").write_text("package main\n", encoding="utf-8")

    assert veredicto_de(tmp_path, REGLA_DEPURACION).estado is EstadoRegla.NO_APLICA


def test_depuracion_cumple_sin_puntos_olvidados(tmp_path: Path):
    (tmp_path / "servicio.py").write_text("def suma(a, b):\n    return a + b\n", encoding="utf-8")

    assert veredicto_de(tmp_path, REGLA_DEPURACION).estado is EstadoRegla.CUMPLE


def test_depuracion_no_cumple_en_python(tmp_path: Path):
    (tmp_path / "servicio.py").write_text(
        f"def suma(a, b):\n    {PUNTO_DEPURACION_PY}\n    return a + b\n", encoding="utf-8"
    )

    resultado = veredicto_de(tmp_path, REGLA_DEPURACION)

    assert resultado.estado is EstadoRegla.NO_CUMPLE
    assert resultado.evidencia[0].linea == 2


def test_depuracion_no_cumple_en_typescript(tmp_path: Path):
    (tmp_path / "vista.ts").write_text(
        f"export function ver() {{\n  {PUNTO_DEPURACION_TS}\n}}\n", encoding="utf-8"
    )

    assert veredicto_de(tmp_path, REGLA_DEPURACION).estado is EstadoRegla.NO_CUMPLE


def test_depuracion_no_confunde_una_palabra_parecida(tmp_path: Path):
    (tmp_path / "servicio.py").write_text(
        "def registrar_breakpoints(cuantos):\n    return cuantos\n", encoding="utf-8"
    )

    assert veredicto_de(tmp_path, REGLA_DEPURACION).estado is EstadoRegla.CUMPLE


# --- regla: sin-rutas-absolutas-de-esta-maquina --------------------------

REGLA_RUTAS = "sin-rutas-absolutas-de-esta-maquina"


def home_detectable() -> str:
    home = str(Path.home())
    calza = modulo._PATRON_RUTA_ABSOLUTA_UNIX.search(
        f'"{home}/x/"'
    ) or modulo._PATRON_RUTA_ABSOLUTA_WINDOWS.search(f'"{home}\\x\\"')
    if calza is None:
        pytest.skip(f"El home de esta máquina ({home}) no tiene la forma que la regla busca.")
    return home


def test_rutas_no_aplica_sin_codigo(tmp_path: Path):
    (tmp_path / "LEEME.md").write_text("documentación\n", encoding="utf-8")

    assert veredicto_de(tmp_path, REGLA_RUTAS).estado is EstadoRegla.NO_APLICA


def test_rutas_cumple_con_ruta_relativa(tmp_path: Path):
    (tmp_path / "servicio.py").write_text('RUTA = "datos/config.json"\n', encoding="utf-8")

    assert veredicto_de(tmp_path, REGLA_RUTAS).estado is EstadoRegla.CUMPLE


def test_rutas_no_cumple_con_el_home_real(tmp_path: Path):
    home = home_detectable()
    (tmp_path / "servicio.py").write_text(
        f'RUTA = "{home}/proyecto/datos.json"\n', encoding="utf-8"
    )

    resultado = veredicto_de(tmp_path, REGLA_RUTAS)

    assert resultado.estado is EstadoRegla.NO_CUMPLE
    assert resultado.evidencia[0].linea == 1
    # El nombre de usuario real NO puede viajar en el reporte.
    assert home not in resultado.evidencia[0].detalle


def test_rutas_cumple_con_un_placeholder_de_la_interfaz(tmp_path: Path):
    # Este es EL caso que hacía inservible a la regla: medida sin el filtro de
    # "la cuenta existe", daba 7 hallazgos en el repo real y los 7 eran así.
    (tmp_path / "Selector.tsx").write_text(
        'const ejemplo = "/Users/tu-usuario/Projects/mi-app";\n', encoding="utf-8"
    )

    assert veredicto_de(tmp_path, REGLA_RUTAS).estado is EstadoRegla.CUMPLE


# --- regla: verificacion-detectable --------------------------------------

REGLA_VERIFICACION = "verificacion-detectable"


def test_verificacion_no_aplica_en_ecosistema_desconocido(tmp_path: Path):
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "x"\n', encoding="utf-8")
    (tmp_path / "main.rs").write_text("fn main() {}\n", encoding="utf-8")

    resultado = veredicto_de(tmp_path, REGLA_VERIFICACION)

    # Un repo de Rust con `cargo test` no incumple una regla que no lo entiende.
    assert resultado.estado is EstadoRegla.NO_APLICA


def test_verificacion_cumple_con_script_de_test(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "vitest run"}}), encoding="utf-8"
    )

    resultado = veredicto_de(tmp_path, REGLA_VERIFICACION)

    assert resultado.estado is EstadoRegla.CUMPLE
    assert "npm test" in resultado.mensaje


def test_verificacion_no_cumple_con_manifiesto_sin_comando(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": 'echo "Error: no test specified" && exit 1'}}),
        encoding="utf-8",
    )

    resultado = veredicto_de(tmp_path, REGLA_VERIFICACION)

    assert resultado.estado is EstadoRegla.NO_CUMPLE
    assert [item.ruta for item in resultado.evidencia] == ["package.json"]


# --- la corrida completa -------------------------------------------------


def test_verificar_todas_devuelve_una_fila_por_regla(tmp_path: Path):
    reporte = verificar_todas(tmp_path)

    assert isinstance(reporte, ReporteVerificacion)
    assert [item.regla_id for item in reporte.resultados] == [item.id for item in CATALOGO]


def test_el_recorrido_se_hace_una_sola_vez_para_todas_las_reglas(tmp_path: Path, monkeypatch):
    (tmp_path / "servicio.py").write_text("VALOR = 3\n", encoding="utf-8")
    veces = {"n": 0}
    original = modulo._recorrer_archivos_de_texto

    def contando(raiz: Path):
        veces["n"] += 1
        return original(raiz)

    monkeypatch.setattr(modulo, "_recorrer_archivos_de_texto", contando)
    verificar_todas(tmp_path)

    assert veces["n"] == 1


def test_una_regla_que_revienta_no_tumba_a_las_demas(tmp_path: Path):
    def explota(contexto: ContextoDeChequeo) -> Veredicto:
        raise RuntimeError("algo salió muy mal")

    rota = ReglaVerificable(id="rota", descripcion="Falla siempre.", comprobar=explota)
    sana = ReglaVerificable(
        id="sana",
        descripcion="Cumple siempre.",
        comprobar=lambda contexto: Veredicto(estado=EstadoRegla.CUMPLE, mensaje="bien"),
    )

    reporte = verificar_todas(tmp_path, reglas=[rota, sana])

    assert reporte.resultados[0].estado is EstadoRegla.ERROR
    assert "algo salió muy mal" in reporte.resultados[0].mensaje
    assert reporte.resultados[1].estado is EstadoRegla.CUMPLE
    assert reporte.todo_en_orden is False


def test_una_regla_lenta_se_corta_en_su_tope(tmp_path: Path):
    def lenta(contexto: ContextoDeChequeo) -> Veredicto:
        time.sleep(1.0)
        return Veredicto(estado=EstadoRegla.CUMPLE, mensaje="tarde")

    dormilona = ReglaVerificable(id="lenta", descripcion="Tarda.", comprobar=lenta)

    inicio = time.monotonic()
    reporte = verificar_todas(tmp_path, reglas=[dormilona], tope_segundos_por_regla=0.05)
    transcurrido = time.monotonic() - inicio

    assert reporte.resultados[0].estado is EstadoRegla.ERROR
    # El punto del tope: el turno del agente sigue, no espera al chequeo.
    assert transcurrido < 0.9


def test_un_recorrido_lento_no_arrastra_a_las_reglas_que_solo_usan_git(repo: Path, monkeypatch):
    """Una regla que no mira archivos no puede caer por culpa de la que sí.

    ``sin-generados-versionados`` solo consulta el índice de git. Con un único
    candado para todos los cachés del contexto, se quedaba esperando el
    recorrido que otra regla había dejado a medias al pasarse de su tope, y se
    pasaba del suyo sin haber llegado a preguntarle nada a git.
    """

    (repo / "servicio.py").write_text("VALOR = 3\n", encoding="utf-8")
    git(repo, "add", "servicio.py")
    original = modulo._recorrer_archivos_de_texto

    def lento(raiz: Path):
        time.sleep(1.0)
        return original(raiz)

    monkeypatch.setattr(modulo, "_recorrer_archivos_de_texto", lento)
    reporte = verificar_todas(
        repo,
        reglas=[regla(REGLA_CONFLICTOS), regla(REGLA_GENERADOS)],
        tope_segundos_por_regla=0.2,
    )

    por_id = {item.regla_id: item for item in reporte.resultados}
    assert por_id[REGLA_CONFLICTOS].estado is EstadoRegla.ERROR
    assert por_id[REGLA_GENERADOS].estado is EstadoRegla.CUMPLE


def test_el_tope_total_deja_sin_correr_a_las_que_faltan(tmp_path: Path):
    def lenta(contexto: ContextoDeChequeo) -> Veredicto:
        time.sleep(0.3)
        return Veredicto(estado=EstadoRegla.CUMPLE, mensaje="tarde")

    primera = ReglaVerificable(id="primera", descripcion="Tarda.", comprobar=lenta)
    segunda = ReglaVerificable(
        id="segunda",
        descripcion="No alcanza a correr.",
        comprobar=lambda contexto: Veredicto(estado=EstadoRegla.CUMPLE, mensaje="bien"),
    )

    reporte = verificar_todas(
        tmp_path,
        reglas=[primera, segunda],
        tope_segundos_por_regla=0.4,
        tope_segundos_total=0.1,
    )

    assert reporte.resultados[1].estado is EstadoRegla.ERROR
    assert "tope de tiempo" in reporte.resultados[1].mensaje


def test_no_aplica_no_cuenta_como_incumplimiento(tmp_path: Path):
    solo_no_aplica = ReglaVerificable(
        id="ajena",
        descripcion="No aplica acá.",
        comprobar=lambda contexto: Veredicto(estado=EstadoRegla.NO_APLICA, mensaje="otro lenguaje"),
    )

    reporte = verificar_todas(tmp_path, reglas=[solo_no_aplica])

    assert reporte.incumplimientos == ()
    assert reporte.todo_en_orden is True
    assert reporte.as_prompt_block() is None


def test_el_bloque_de_prompt_solo_trae_lo_que_hay_que_arreglar(tmp_path: Path):
    (tmp_path / "servicio.py").write_text(
        f"def suma(a, b):\n    {PUNTO_DEPURACION_PY}\n    return a + b\n", encoding="utf-8"
    )

    reporte = verificar_todas(tmp_path, reglas=[regla(REGLA_DEPURACION)])
    bloque = reporte.as_prompt_block()

    assert bloque is not None
    assert REGLA_DEPURACION in bloque
    assert "servicio.py:2" in bloque
    assert "INCUMPLE" in bloque


def test_un_recorrido_recortado_dice_cumple_pero_avisa(tmp_path: Path, monkeypatch):
    # Punto 3 del diseño: incompleto y sin hallazgos NO es un error, es un
    # "cumple, hasta donde se miró". Marcarlo como error sería ruido en cada
    # corrida de un repo grande.
    (tmp_path / "uno.py").write_text("A = 1\n", encoding="utf-8")
    (tmp_path / "dos.py").write_text("B = 2\n", encoding="utf-8")
    monkeypatch.setattr(modulo, "_MAX_ARCHIVOS", 1)

    resultado = veredicto_de(tmp_path, REGLA_DEPURACION)

    assert resultado.estado is EstadoRegla.CUMPLE
    assert resultado.parcial is True
    assert "parcial" in resultado.mensaje


def test_el_reporte_es_serializable(tmp_path: Path):
    reporte = verificar_todas(tmp_path)
    crudo = json.dumps(reporte.resumen(), ensure_ascii=False)

    assert "resultados" in crudo
    assert reporte.resumen()["conteo"]["no_aplica"] >= 1


def test_evidencia_como_texto_con_y_sin_linea():
    assert Evidencia("a.py", 12, "x").como_texto().startswith("a.py:12")
    assert Evidencia("a.py", None, "x").como_texto().startswith("a.py —")


def test_una_regla_sola_se_puede_correr_desde_su_raiz(tmp_path: Path):
    (tmp_path / "servicio.py").write_text("VALOR = 3\n", encoding="utf-8")

    resultado = regla(REGLA_CONFLICTOS).verificar(tmp_path)

    assert resultado.regla_id == REGLA_CONFLICTOS
    assert resultado.estado is EstadoRegla.CUMPLE
    assert resultado.duracion_segundos >= 0


# --- el alcance: el reporte es de lo que el agente tocó ------------------
#
# Es lo que decide si esta pieza sirve. Un reporte del repo entero manda al
# agente a arreglar deuda ajena y le enseña que este bloque no habla de él.


def test_punto_de_partida_es_el_commit_actual(repo_con_base: Path):
    commit = punto_de_partida(repo_con_base)

    assert commit is not None and len(commit) == 40


def test_punto_de_partida_es_none_sin_repo(tmp_path: Path):
    assert punto_de_partida(tmp_path) is None


def test_el_alcance_de_la_sesion_trae_lo_modificado_y_lo_recien_creado(repo_con_base: Path):
    base = punto_de_partida(repo_con_base)
    (repo_con_base / "LEEME.md").write_text("proyecto, con cambios\n", encoding="utf-8")
    (repo_con_base / "nuevo.py").write_text("A = 1\n", encoding="utf-8")
    (repo_con_base / "ya_estaba.py").write_text("B = 2\n", encoding="utf-8")
    git(repo_con_base, "add", "ya_estaba.py")
    git(repo_con_base, "commit", "-qm", "commit del propio agente")

    alcance = alcance_de_la_sesion(repo_con_base, base)

    assert alcance.origen is OrigenDelAlcance.DIFF_DE_LA_SESION
    # Modificado, commiteado durante la sesión y sin versionar: los tres.
    assert alcance.archivos == frozenset({"LEEME.md", "nuevo.py", "ya_estaba.py"})


def test_el_alcance_de_la_sesion_no_trae_lo_que_el_gitignore_cubre(repo_con_base: Path):
    base = punto_de_partida(repo_con_base)
    (repo_con_base / ".gitignore").write_text("basura.log\n", encoding="utf-8")
    (repo_con_base / "basura.log").write_text("ruido\n", encoding="utf-8")

    alcance = alcance_de_la_sesion(repo_con_base, base)

    assert "basura.log" not in (alcance.archivos or frozenset())


@pytest.mark.parametrize(
    ("base", "esperado"),
    [
        (None, "punto de partida"),
        ("no-existe-este-commit", "no pudo comparar"),
        ("--upload-pack=cualquier-cosa", "referencia de git válida"),
    ],
)
def test_sin_punto_de_partida_utilizable_se_cae_al_workspace_completo(
    repo_con_base: Path, base: str | None, esperado: str
):
    alcance = alcance_de_la_sesion(repo_con_base, base)

    assert alcance.origen is OrigenDelAlcance.WORKSPACE_COMPLETO
    assert alcance.archivos is None
    # El motivo no es decorativo: termina escrito en el prompt del agente.
    assert esperado in alcance.motivo


def test_sin_git_se_cae_al_workspace_completo(tmp_path: Path):
    alcance = alcance_de_la_sesion(tmp_path, "0" * 40)

    assert alcance.origen is OrigenDelAlcance.WORKSPACE_COMPLETO
    assert "repositorio git" in alcance.motivo


def test_el_alcance_deja_fuera_los_hallazgos_que_no_son_del_agente(repo_con_base: Path):
    """El caso que motiva todo: deuda preexistente que el agente no tocó.

    El repo ya traía un punto de depuración de antes. El agente escribió otro
    archivo, limpio. Sin acotar, el reporte le exige arreglar algo que no
    escribió; acotado, no le dice nada.
    """

    (repo_con_base / "viejo.py").write_text(
        f"def viejo():\n    {PUNTO_DEPURACION_PY}\n", encoding="utf-8"
    )
    git(repo_con_base, "add", "viejo.py")
    git(repo_con_base, "commit", "-qm", "deuda que ya estaba")
    base = punto_de_partida(repo_con_base)
    (repo_con_base / "nuevo.py").write_text("def nuevo():\n    return 1\n", encoding="utf-8")

    acotado = verificar_todas(
        repo_con_base,
        alcance=alcance_de_la_sesion(repo_con_base, base),
        reglas=[regla(REGLA_DEPURACION)],
    )
    completo = verificar_todas(repo_con_base, reglas=[regla(REGLA_DEPURACION)])

    assert acotado.incumplimientos == ()
    assert acotado.as_prompt_block() is None
    # Y el mismo repo sin acotar sí lo trae: el test no pasa por casualidad.
    assert completo.incumplimientos != ()


def test_el_alcance_no_tapa_lo_que_el_agente_si_escribio(repo_con_base: Path):
    base = punto_de_partida(repo_con_base)
    (repo_con_base / "nuevo.py").write_text(
        f"def nuevo():\n    {PUNTO_DEPURACION_PY}\n", encoding="utf-8"
    )

    reporte = verificar_todas(
        repo_con_base,
        alcance=alcance_de_la_sesion(repo_con_base, base),
        reglas=[regla(REGLA_DEPURACION)],
    )

    assert [item.regla_id for item in reporte.incumplimientos] == [REGLA_DEPURACION]
    assert "nuevo.py:2" in (reporte.as_prompt_block() or "")


def test_las_reglas_de_git_tambien_se_acotan(repo_con_base: Path):
    """`node_modules` versionado hace dos años no es de este turno."""

    dependencia = repo_con_base / "node_modules" / "izq"
    dependencia.mkdir(parents=True)
    (dependencia / "index.js").write_text("module.exports = 1;\n", encoding="utf-8")
    git(repo_con_base, "add", "-f", "node_modules/izq/index.js")
    git(repo_con_base, "commit", "-qm", "dependencia versionada de antes")
    base = punto_de_partida(repo_con_base)
    (repo_con_base / "servicio.py").write_text("VALOR = 3\n", encoding="utf-8")
    git(repo_con_base, "add", "servicio.py")

    resultado = regla(REGLA_GENERADOS).verificar(
        repo_con_base, alcance=alcance_de_la_sesion(repo_con_base, base)
    )

    assert resultado.estado is EstadoRegla.CUMPLE


def test_un_alcance_vacio_no_inventa_nada_que_revisar(tmp_path: Path):
    (tmp_path / "servicio.py").write_text(
        f"def suma():\n    {PUNTO_DEPURACION_PY}\n", encoding="utf-8"
    )

    reporte = verificar_todas(tmp_path, alcance=alcance_de_archivos([]))

    estados = {
        item.estado for item in reporte.resultados if item.ambito is AmbitoDeRegla.POR_ARCHIVO
    }
    assert estados == {EstadoRegla.NO_APLICA}
    assert reporte.incumplimientos == ()


def test_acotar_no_recorre_el_workspace(tmp_path: Path, monkeypatch):
    """El tercer hallazgo se cierra solo si el primero está bien acotado.

    Revisar cinco archivos no puede costar lo que cuesta recorrer un monorepo,
    y la única forma de garantizarlo es no recorrerlo: con alcance acotado el
    recorrido completo NO se llama.
    """

    (tmp_path / "servicio.py").write_text("VALOR = 3\n", encoding="utf-8")

    def prohibido(raiz: Path):
        raise AssertionError("con alcance acotado no se recorre el workspace")

    monkeypatch.setattr(modulo, "_recorrer_archivos_de_texto", prohibido)
    reporte = verificar_todas(tmp_path, alcance=alcance_de_archivos(["servicio.py"]))

    assert reporte.no_concluyentes == ()


def test_el_alcance_solo_lee_los_archivos_que_le_dieron(tmp_path: Path):
    (tmp_path / "mio.py").write_text("A = 1\n", encoding="utf-8")
    (tmp_path / "ajeno.py").write_text("B = 2\n", encoding="utf-8")

    contexto = ContextoDeChequeo(tmp_path, alcance_de_archivos(["mio.py"]))

    assert [item.ruta for item in contexto.escaneo().archivos] == ["mio.py"]


def test_el_alcance_aguanta_rutas_borradas_y_carpetas_ignoradas(tmp_path: Path):
    # Un diff lista lo que se borró, y puede listar algo dentro de una carpeta
    # que este módulo nunca revisa. Ninguna de las dos puede tumbar la corrida.
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("1;\n", encoding="utf-8")

    contexto = ContextoDeChequeo(
        tmp_path, alcance_de_archivos(["se_borro.py", "node_modules/x.js"])
    )

    assert contexto.escaneo().archivos == ()


@pytest.mark.parametrize("ruta", ["/etc/passwd", "../vecino/secreto.py", "a/../../b.py", ""])
def test_el_alcance_explicito_rechaza_rutas_que_se_salen(ruta: str):
    with pytest.raises(IDEReglasVerificablesError):
        alcance_de_archivos([ruta])


def test_el_bloque_avisa_cuando_no_pudo_acotar(tmp_path: Path):
    (tmp_path / "servicio.py").write_text(
        f"def suma():\n    {PUNTO_DEPURACION_PY}\n", encoding="utf-8"
    )

    bloque = verificar_todas(tmp_path, reglas=[regla(REGLA_DEPURACION)]).as_prompt_block() or ""

    assert "COMPLETO" in bloque
    # Lo importante no es la palabra: es que el agente entienda que puede no
    # ser suyo, en vez de recibir un "arregla lo que aparezca acá" a secas.
    assert "ya estaba en el repo" in bloque
    assert "no lo cambies sin que te lo pidan" in bloque


def test_el_bloque_acotado_le_dice_que_es_su_propio_trabajo(repo_con_base: Path):
    base = punto_de_partida(repo_con_base)
    (repo_con_base / "nuevo.py").write_text(
        f"def nuevo():\n    {PUNTO_DEPURACION_PY}\n", encoding="utf-8"
    )

    reporte = verificar_todas(
        repo_con_base,
        alcance=alcance_de_la_sesion(repo_con_base, base),
        reglas=[regla(REGLA_DEPURACION)],
    )
    bloque = reporte.as_prompt_block() or ""

    assert "Es tu propio trabajo" in bloque
    assert "COMPLETO" not in bloque


def test_la_regla_del_proyecto_no_se_recorta_pero_se_marca_aparte(tmp_path: Path):
    """`verificacion-detectable` habla del repo, no del cambio.

    Se sigue reportando -- sin comando de tests el agente no puede comprobar lo
    que escribió -- pero el bloque no la presenta como algo que él rompió.
    """

    (tmp_path / "package.json").write_text(json.dumps({"name": "x"}), encoding="utf-8")

    reporte = verificar_todas(
        tmp_path,
        alcance=alcance_de_archivos(["otro.py"]),
        reglas=[regla(REGLA_VERIFICACION)],
    )
    bloque = reporte.as_prompt_block() or ""

    assert [item.regla_id for item in reporte.incumplimientos] == [REGLA_VERIFICACION]
    assert "DEL PROYECTO" in bloque
    assert "no lo rompiste tú" in bloque


def test_el_alcance_viaja_en_el_resumen(repo_con_base: Path):
    base = punto_de_partida(repo_con_base)
    (repo_con_base / "nuevo.py").write_text("A = 1\n", encoding="utf-8")

    resumen = verificar_todas(
        repo_con_base, alcance=alcance_de_la_sesion(repo_con_base, base)
    ).resumen()

    assert resumen["alcance"]["acotado"] is True
    assert resumen["alcance"]["cantidad_archivos"] == 1
    assert resumen["alcance"]["base"] == base
    # El resumen no repite el árbol del repo: solo dice cuántos.
    assert "archivos" not in resumen["alcance"]
    json.dumps(resumen, ensure_ascii=False)


def test_el_alcance_completo_dice_su_motivo_en_el_resumen(tmp_path: Path):
    resumen = verificar_todas(tmp_path, alcance=alcance_completo("no había git")).resumen()

    assert resumen["alcance"]["acotado"] is False
    assert resumen["alcance"]["motivo"] == "no había git"


def test_el_alcance_es_un_dato_del_reporte_no_una_variable_global():
    # Dos corridas distintas no pueden compartir alcance por accidente.
    uno = Alcance(origen=OrigenDelAlcance.LISTA_EXPLICITA, archivos=frozenset({"a.py"}))
    dos = alcance_completo("otra cosa")

    assert uno.contiene("a.py") and not uno.contiene("b.py")
    assert dos.contiene("lo-que-sea")
    assert uno.limitar(["a.py", "b.py"]) == ("a.py",)


def test_el_catalogo_no_se_reporta_a_si_mismo(tmp_path: Path):
    """El repo de Edecán es un workspace válido para su propio agente.

    Si las expresiones de este módulo calzaran contra su propio fuente, la
    primera corrida del dueño sobre su propio repo saldría llena de hallazgos
    inventados -- y a la segunda ya nadie leería el reporte.
    """

    # Se copian los dos archivos a un workspace aparte en vez de escanear la
    # carpeta del companion: así el test comprueba SOLO lo que promete (que
    # estas expresiones no calzan contra su propio fuente) y no se rompe
    # porque otro módulo del repo tenga algo que reportar.
    espejo = tmp_path / "espejo"
    espejo.mkdir()
    for origen in (Path(modulo.__file__), Path(__file__)):
        (espejo / origen.name).write_bytes(origen.read_bytes())

    reporte = verificar_todas(
        espejo,
        reglas=[
            regla(REGLA_CREDENCIALES),
            regla(REGLA_CONFLICTOS),
            regla(REGLA_DEPURACION),
            regla(REGLA_RUTAS),
        ],
    )

    assert reporte.incumplimientos == ()
