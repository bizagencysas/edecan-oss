"""Tests de ``ide_opencode_lsp.py`` -- integración real contra ``opencode serve``.

Qué se prueba de verdad, sin dobles: se arranca un ``opencode serve`` real
(mismo patrón que ``test_ide_opencode.py``) contra repos de juguete PROPIOS
(``tmp_path``, nunca un repo del dueño -- regla 7 del encargo) con TypeScript
y Python, y se ejercitan ``GET /find/symbol`` / ``GET /lsp`` de verdad.

El hallazgo central documentado en el docstring de ``ide_opencode_lsp.py``
(estas dos rutas devuelven ``[]`` contra la superficie HTTP pública de
opencode, incluso con un servidor de lenguaje real instalado y funcionando)
NO se simula acá -- se REPRODUCE como aserción explícita, con un comentario
que explica por qué un ``[]`` en estos tests es el resultado correcto y
esperado, no una prueba que "no alcanzó a terminar de escribirse". Lo que sí
se prueba sin ambigüedad, con evidencia end-to-end:

- Sin ``"lsp": true`` en el ``opencode.json`` del workspace, ambas rutas
  responden ``200`` con lista vacía -- nunca un error, nunca un colgado.
- Con ``"lsp": true``, siguen respondiendo ``200`` con lista vacía (mismo
  comportamiento, documentado como el hallazgo del módulo).
- Un error real de opencode (``400`` por parámetro requerido faltante) se
  propaga con el mensaje REAL del servidor, en la forma legada
  (``name``/``data.message``) -- no la forma ``_tag`` que usa la superficie
  ``/api/``.
- Un fallo de conexión (servidor caído) se propaga como ``ErrorOpencode``,
  nunca se traga (regla 6 del encargo).
- Los tres métodos que documentan capacidades ausentes de la API
  (``definicion``/``referencias``/``diagnosticos``) fallan con un mensaje
  explicativo, no con un ``AttributeError`` mudo ni con un valor fabricado.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from edecan_companion.ide_opencode import ErrorOpencode, ServidorOpencode
from edecan_companion.ide_opencode_lsp import (
    ClienteLspOpencode,
    EstadoServidorLsp,
    LspOpencodeNoDisponibleError,
    SimboloLsp,
    _error_desde_cuerpo_legado,
)

# NOTA: a diferencia de ``test_ide_opencode.py``, este archivo mezcla tests
# async (contra un ``opencode serve`` real) con tests sync (parseo de error,
# validación de modelos) -- por eso NO hay ``pytestmark = pytest.mark.
# asyncio`` a nivel de módulo (marcaría también las funciones sync, que
# pytest-asyncio rechaza con un warning). ``asyncio_mode = "auto"`` en
# ``pyproject.toml`` ya detecta las funciones ``async def test_...`` solas.


# --------------------------------------------------------------------------- #
# Repos de juguete -- PROPIOS, en tmp_path, nunca un repo del dueño (regla 7)
# --------------------------------------------------------------------------- #


def _workspace_typescript(tmp_path: Path, *, lsp_habilitado: bool) -> Path:
    """Repo de juguete TypeScript con un símbolo conocido: la función
    ``saludar`` en ``util.ts``, importada y usada desde ``main.ts`` -- la
    "referencia conocida" que pedía el encargo verificar."""

    workspace = tmp_path / "ts_workspace"
    workspace.mkdir()
    (workspace / "tsconfig.json").write_text(
        '{"compilerOptions": {"target": "ES2020", "module": "commonjs", "strict": true}}',
        encoding="utf-8",
    )
    (workspace / "util.ts").write_text(
        'export function saludar(nombre: string): string {\n  return "Hola, " + nombre;\n}\n',
        encoding="utf-8",
    )
    (workspace / "main.ts").write_text(
        'import { saludar } from "./util";\n\nconsole.log(saludar("Mundo"));\n',
        encoding="utf-8",
    )
    config = '{"$schema": "https://opencode.ai/config.json"' + (
        ', "lsp": true}' if lsp_habilitado else "}"
    )
    (workspace / "opencode.json").write_text(config, encoding="utf-8")
    return workspace


def _workspace_python(tmp_path: Path, *, lsp_habilitado: bool) -> Path:
    """Repo de juguete Python con el mismo patrón: ``saludar`` en
    ``util.py``, importada desde ``main.py``."""

    workspace = tmp_path / "py_workspace"
    workspace.mkdir()
    (workspace / "util.py").write_text(
        'def saludar(nombre: str) -> str:\n    return f"Hola, {nombre}"\n',
        encoding="utf-8",
    )
    (workspace / "main.py").write_text(
        "from util import saludar\n\nprint(saludar(\"Mundo\"))\n",
        encoding="utf-8",
    )
    config = '{"$schema": "https://opencode.ai/config.json"' + (
        ', "lsp": true}' if lsp_habilitado else "}"
    )
    (workspace / "opencode.json").write_text(config, encoding="utf-8")
    return workspace


# --------------------------------------------------------------------------- #
# Parseo de error -- unitario sobre un cuerpo REAL capturado (ver docstring
# del módulo): no hace falta un servidor vivo para probar el parser, y así
# queda un test rápido que no depende de red/proceso para esta parte.
# --------------------------------------------------------------------------- #


def test_error_legado_se_parsea_con_la_forma_real_de_badrequest() -> None:
    """Cuerpo capturado de verdad contra un ``opencode serve`` real (``GET
    /find/symbol`` sin ``query``): ``{"name": "BadRequest", "data":
    {"message": "Missing key\\n  at [\\"query\\"]", "kind": "Query"}}`` --
    forma DISTINTA de la que usa ``ide_opencode._error_desde_cuerpo`` para
    la superficie ``/api/`` (que lleva ``_tag`` en la raíz)."""

    cuerpo = (
        b'{"name":"BadRequest","data":{"message":"Missing key\\n  at '
        b'[\\"query\\"]","kind":"Query"}}'
    )
    error = _error_desde_cuerpo_legado(400, cuerpo, "GET", "/find/symbol")
    assert isinstance(error, ErrorOpencode)
    assert error.status == 400
    assert error.tag == "BadRequest"
    assert "Missing key" in str(error)
    assert "query" in str(error)


# --------------------------------------------------------------------------- #
# Ciclo de vida real -- arranca opencode de verdad, sin gastar tokens de
# proveedor (nada de esto necesita un modelo)
# --------------------------------------------------------------------------- #


async def test_sin_lsp_habilitado_ambas_rutas_dan_lista_vacia_sin_error(tmp_path: Path) -> None:
    """Regla 6: esto NO debe ser un error ni un colgado -- es el
    comportamiento real de opencode (log real: ``"all LSPs are disabled"``)
    cuando el ``opencode.json`` del workspace no trae ``"lsp": true``."""

    workspace = _workspace_typescript(tmp_path, lsp_habilitado=False)
    async with await ServidorOpencode.iniciar(workspace, timeout_arranque=30.0) as servidor:
        async with ClienteLspOpencode.creado_desde(servidor) as lsp:
            simbolos = await lsp.buscar_simbolos("saludar", directorio=str(workspace))
            estado = await lsp.estado_lsp(directorio=str(workspace))
    assert simbolos == []
    assert estado == []


async def test_lsp_habilitado_sigue_dando_lista_vacia_por_http_typescript(tmp_path: Path) -> None:
    """El hallazgo central del módulo, reproducido: con ``"lsp": true`` (el
    log real pasa a mostrar ``"enabled LSP servers"`` con ~35 IDs, incluido
    ``typescript``) opencode SIGUE sin popular ``/find/symbol``/``/lsp`` por
    HTTP -- porque nada en la superficie pública abre/toca ``util.ts`` para
    su servidor de lenguaje. Ver el docstring de ``ide_opencode_lsp.py``
    ("El hallazgo que manda") para la cadena completa de evidencia
    (incluida la prueba de que SÍ funciona vía la CLI de depuración de
    opencode, fuera del alcance HTTP de este cliente)."""

    workspace = _workspace_typescript(tmp_path, lsp_habilitado=True)
    async with await ServidorOpencode.iniciar(workspace, timeout_arranque=30.0) as servidor:
        async with ClienteLspOpencode.creado_desde(servidor) as lsp:
            simbolos = await lsp.buscar_simbolos("saludar", directorio=str(workspace))
            estado = await lsp.estado_lsp(directorio=str(workspace))
    # Estas listas vacías son el resultado CORRECTO hoy -- no una prueba sin
    # terminar. Si opencode alguna vez cablea `read`/`edit` (o publica un
    # endpoint de "abrir archivo") a su LSP.touchFile() interno, este mismo
    # assert empezará a fallar -- y ESO será la señal de que ya se puede
    # completar `find/symbol` de verdad desde el HTTP público.
    assert simbolos == []
    assert estado == []


async def test_lsp_habilitado_sigue_dando_lista_vacia_por_http_python(tmp_path: Path) -> None:
    """Mismo hallazgo que el test anterior, con Python -- deliberadamente en
    un repo separado porque ``pyright`` (el LSP de Python que opencode
    integra) SÍ se comprobó, en esta misma ronda, que funciona de verdad
    (se instaló y devolvió diagnósticos reales vía ``opencode debug lsp
    diagnostics``) -- así que esto no es "no hay servidor de lenguaje para
    Python", es específicamente "HTTP no tiene cómo pedir que se use"."""

    workspace = _workspace_python(tmp_path, lsp_habilitado=True)
    async with await ServidorOpencode.iniciar(workspace, timeout_arranque=30.0) as servidor:
        async with ClienteLspOpencode.creado_desde(servidor) as lsp:
            simbolos = await lsp.buscar_simbolos("saludar", directorio=str(workspace))
            estado = await lsp.estado_lsp(directorio=str(workspace))
    assert simbolos == []
    assert estado == []


async def test_parametro_requerido_faltante_da_error_real_no_generico(tmp_path: Path) -> None:
    """Regla 6 contra un servidor de verdad: ``query`` es obligatorio en
    ``GET /find/symbol`` (ver ``/doc``) -- si se pide sin él (vía el método
    interno, ya que la firma pública de ``buscar_simbolos`` no permite
    omitirlo -- a propósito, para no poder construir esta petición inválida
    por accidente desde el resto del código), opencode responde 400 real y
    ese mensaje real llega intacto hasta acá."""

    workspace = _workspace_typescript(tmp_path, lsp_habilitado=False)
    async with await ServidorOpencode.iniciar(workspace, timeout_arranque=30.0) as servidor:
        async with ClienteLspOpencode.creado_desde(servidor) as lsp:
            with pytest.raises(ErrorOpencode) as exc_info:
                await lsp._solicitar("GET", "/find/symbol")  # sin 'query' a propósito
    error = exc_info.value
    assert error.status == 400
    assert error.tag == "BadRequest"
    assert "query" in str(error).lower()


async def test_error_de_conexion_se_propaga_como_erroropencode(tmp_path: Path) -> None:
    """Regla 6: si el proceso ``opencode serve`` ya no está, el fallo de
    transporte se propaga como ``ErrorOpencode`` -- nunca un ``[]``
    silencioso que se confunda con "no hay símbolos"."""

    workspace = _workspace_typescript(tmp_path, lsp_habilitado=False)
    servidor = await ServidorOpencode.iniciar(workspace, timeout_arranque=30.0)
    lsp = ClienteLspOpencode.creado_desde(servidor)
    await servidor.detener()
    try:
        with pytest.raises(ErrorOpencode):
            await lsp.estado_lsp()
    finally:
        await lsp.cerrar()


# --------------------------------------------------------------------------- #
# Capacidades ausentes -- fallan con explicación, no con vacío ni con
# AttributeError mudo (ver docstring del módulo, puntos 3-5)
# --------------------------------------------------------------------------- #


async def test_definicion_no_fabrica_vacio_explica_por_que_no_existe(tmp_path: Path) -> None:
    workspace = _workspace_typescript(tmp_path, lsp_habilitado=False)
    async with await ServidorOpencode.iniciar(workspace, timeout_arranque=30.0) as servidor:
        async with ClienteLspOpencode.creado_desde(servidor) as lsp:
            with pytest.raises(LspOpencodeNoDisponibleError, match="definición"):
                await lsp.definicion()


async def test_referencias_no_fabrica_vacio_explica_por_que_no_existe(tmp_path: Path) -> None:
    workspace = _workspace_typescript(tmp_path, lsp_habilitado=False)
    async with await ServidorOpencode.iniciar(workspace, timeout_arranque=30.0) as servidor:
        async with ClienteLspOpencode.creado_desde(servidor) as lsp:
            with pytest.raises(LspOpencodeNoDisponibleError, match="api/reference"):
                await lsp.referencias()


async def test_diagnosticos_no_fabrica_vacio_explica_por_que_no_existe(tmp_path: Path) -> None:
    workspace = _workspace_typescript(tmp_path, lsp_habilitado=False)
    async with await ServidorOpencode.iniciar(workspace, timeout_arranque=30.0) as servidor:
        async with ClienteLspOpencode.creado_desde(servidor) as lsp:
            with pytest.raises(LspOpencodeNoDisponibleError, match="debug lsp diagnostics"):
                await lsp.diagnosticos()


# --------------------------------------------------------------------------- #
# Modelos -- validación de forma contra las respuestas reales documentadas
# --------------------------------------------------------------------------- #


def test_modelo_simbolo_valida_una_respuesta_real_de_find_symbol() -> None:
    """Forma real de ``Symbol`` según ``/doc`` (``#/components/schemas/
    Symbol``) -- no se pudo capturar un elemento no vacío en esta ronda (ver
    el hallazgo del módulo), así que este test valida el MODELO contra la
    forma exacta que ``/doc`` documenta, construida a mano desde el schema,
    no contra un ejemplo inventado con campos que no aparecen en el spec."""

    crudo = {
        "name": "saludar",
        "kind": 12,  # SymbolKind.Function según LSP
        "location": {
            "uri": "file:///workspace/util.ts",
            "range": {"start": {"line": 0, "character": 0}, "end": {"line": 2, "character": 1}},
        },
    }
    simbolo = SimboloLsp.model_validate(crudo)
    assert simbolo.name == "saludar"
    assert simbolo.kind == 12
    assert simbolo.location.uri == "file:///workspace/util.ts"
    assert simbolo.location.range.start.line == 0


def test_modelo_estado_lsp_valida_una_respuesta_real() -> None:
    """Forma real de ``LSPStatus`` según ``/doc``."""

    crudo = {"id": "pyright", "name": "Pyright", "root": "/workspace", "status": "connected"}
    estado = EstadoServidorLsp.model_validate(crudo)
    assert estado.status == "connected"

    crudo_error = {
        "id": "typescript",
        "name": "TypeScript",
        "root": "/workspace",
        "status": "error",
    }
    estado_error = EstadoServidorLsp.model_validate(crudo_error)
    assert estado_error.status == "error"
