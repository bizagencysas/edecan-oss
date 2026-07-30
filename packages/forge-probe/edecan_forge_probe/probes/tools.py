"""Sonda de tool-calling: fiabilidad real de invocar herramientas, por perfil.

La fiabilidad del tool-calling **no es un número**. Un modelo puede acertar el
99 % con ``{"path": "a.py"}`` y el 60 % cuando tiene que meter cuarenta líneas
de código dentro de un campo JSON. Forge vive del segundo caso: cada edición es
un ``apply_patch(path, old_text, new_text)`` en el que ``old_text`` tiene que
llegar **byte a byte idéntico** o el parche no aplica. Por eso esta sonda mide
por `ArgProfile` y por eso el éxito de `code_blob` compara el contenido, no que
el JSON parsee.

Qué mide, todo con evidencia cruda en disco:

- `native_tools.<perfil>` — `Reliability` por perfil (`scalar`, `nested`,
  `code_blob`, `long_string`), con N ≥ 20 intentos —40 por defecto, ver
  `n_minimo_para`— y cada fallo clasificado.
- `native_tools.max_tools_effective` — hasta cuántas herramientas se pueden
  ofrecer antes de que la precisión de selección caiga por debajo de 0,90.
- `native_tools.max_schema_bytes` — hasta qué tamaño de JSON Schema aguanta.
- Tasa de **alucinación de herramienta** (invoca una que no se ofreció) y de
  **argumento inventado** (campo que no está en el esquema), como métricas
  propias y no como ruido dentro del error agregado.
- **Sobrecarga de razonamiento**: el modelo objetivo razona siempre y lo hace en
  un campo aparte que se factura a precio de salida. Un fallo de primera clase
  de esta sonda sería pedir `max_tokens` justos y recibir `content` vacío con la
  factura pagada. Por eso cada caso reserva presupuesto de razonamiento y la
  sonda reporta el ratio razonamiento/contenido observado.

Reglas que este módulo respeta y que no son negociables: no rellena la
`ModelCard` (eso es del runner), no inventa valores por defecto —lo que no se
mide sale como `None`— y no abre red por su cuenta: habla con un
`ProveedorHerramientas`, que en producción es el adaptador real y en los tests
es un doble.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from edecan_llm.base import ToolSpec
from pydantic import BaseModel, Field

from ..modelcard import ArgProfile, Capability, Latencia, ProbeResult, Reliability

# --------------------------------------------------------------------------- #
# Precios y presupuesto
# --------------------------------------------------------------------------- #


class Precios(BaseModel):
    """Tarifa del binding, en USD por millón de tokens.

    Los valores por defecto son los **medidos** contra la cuenta real de
    Cloudflare Workers AI el 27-07-2026 para `@cf/moonshotai/kimi-k2.7-code`.
    Son propiedad del binding, no del modelo: si cambia la factura, se pasan por
    constructor. No se copian a la `ModelCard` desde aquí.
    """

    entrada: float = 0.95
    entrada_cacheada: float = 0.19
    salida: float = 4.00

    def coste(self, *, entrada: int, cacheada: int, salida: int) -> float:
        """Coste en USD de una respuesta. `cacheada` va incluida en `entrada`."""
        frescos = max(0, entrada - cacheada)
        return (
            frescos * self.entrada + cacheada * self.entrada_cacheada + salida * self.salida
        ) / 1_000_000


class PresupuestoAgotado(RuntimeError):
    """El gasto acumulado alcanzó `max_usd`. Se corta y se reporta lo medido."""


# --------------------------------------------------------------------------- #
# Contrato con el proveedor
# --------------------------------------------------------------------------- #


class LlamadaCruda(BaseModel):
    """Una invocación tal y como la emitió el modelo, **sin parsear**.

    `argumentos_json` es el string crudo a propósito: si el adaptador lo
    parseara, `json_invalido` dejaría de ser observable y la sonda mediría el
    parser del adaptador en vez de al modelo.
    """

    nombre: str
    argumentos_json: str


class RespuestaSonda(BaseModel):
    """Respuesta normalizada de una única petición de la sonda."""

    llamadas: list[LlamadaCruda] = Field(default_factory=list)
    contenido: str = ""
    razonamiento: str = ""
    """`message.reasoning_content`: texto de razonamiento, facturado como salida."""

    tokens_entrada: int = 0
    tokens_salida: int = 0
    tokens_cacheados: int = 0
    """`usage.prompt_tokens_details.cached_tokens`: entrada servida desde caché."""

    tokens_razonamiento: int = 0
    neuronas: float | None = None
    error: str | None = None
    """Fallo de transporte o del proveedor. NO es un fallo del modelo: el intento
    se descarta del denominador en vez de contarse como error de tool-calling."""


class ProveedorHerramientas(Protocol):
    """Lo mínimo que la sonda necesita de un proveedor para medir tool-calling.

    Deliberadamente más estrecho que `edecan_llm.base.LLMProvider`: la sonda no
    necesita streaming ni historial, y sí necesita el string crudo de argumentos
    y el desglose de `usage` que `Usage` del contrato común no lleva.
    """

    async def invocar(
        self,
        *,
        sistema: str,
        prompt: str,
        herramientas: Sequence[ToolSpec],
        max_tokens: int,
    ) -> RespuestaSonda: ...


# --------------------------------------------------------------------------- #
# Modos de fallo
# --------------------------------------------------------------------------- #


class ModoFallo(StrEnum):
    """Por qué falló un intento. Un agregado sin desglose no acciona nada."""

    NO_LLAMO = "no_llamo"
    """Respondió en prosa sin invocar ninguna herramienta."""

    HERRAMIENTA_INEXISTENTE = "herramienta_inexistente"
    """Invocó un nombre que no se le ofreció. Alucinación de herramienta."""

    HERRAMIENTA_EQUIVOCADA = "herramienta_equivocada"
    """Eligió otra herramienta del catálogo ofrecido. Es el fallo que mide
    `max_tools_effective` y no coincide con `herramienta_inexistente`: aquí el
    ABI se respeta y lo que falla es la selección."""

    JSON_INVALIDO = "json_invalido"
    """`arguments` no decodifica, o decodifica a algo que no es un objeto."""

    CAMPO_FALTANTE = "campo_faltante"
    """Falta un campo requerido por el esquema o esperado por el caso."""

    TEXTO_ALTERADO = "texto_alterado"
    """El JSON es válido y están todos los campos, pero el valor no llegó
    idéntico. Es el fallo que rompe `apply_patch` en silencio."""

    ARGUMENTO_INVENTADO = "argumento_inventado"
    """Añadió un campo que no está en el esquema."""


@dataclass(slots=True)
class Veredicto:
    """Clasificación de un intento contra lo que se esperaba."""

    ok: bool
    modo: ModoFallo | None = None
    herramienta_llamada: str | None = None
    campos_faltantes: tuple[str, ...] = ()
    campos_alterados: tuple[str, ...] = ()
    campos_inventados: tuple[str, ...] = ()
    herramienta_alucinada: bool = False
    """Invocó un nombre fuera del catálogo ofrecido. Se cuenta como tasa propia."""

    def a_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "modo": None if self.modo is None else self.modo.value,
            "herramienta_llamada": self.herramienta_llamada,
            "campos_faltantes": list(self.campos_faltantes),
            "campos_alterados": list(self.campos_alterados),
            "campos_inventados": list(self.campos_inventados),
            "herramienta_alucinada": self.herramienta_alucinada,
        }


def clasificar(
    *,
    respuesta: RespuestaSonda,
    herramientas: Sequence[ToolSpec],
    esperada: str,
    argumentos_esperados: Mapping[str, Any] | None = None,
) -> Veredicto:
    """Clasifica un intento. Es la pieza que decide qué cuenta como éxito.

    Orden de precedencia, de la causa más externa a la más sutil: no llamó →
    herramienta inexistente → herramienta equivocada → JSON inválido → campo
    faltante → texto alterado → argumento inventado. El orden importa: cuando un
    intento falla por dos razones a la vez, se reporta la que un humano tendría
    que arreglar primero.

    `argumentos_esperados` a `None` significa "solo se mide la selección", que es
    el caso de `max_tools_effective`; aun así se sigue detectando la invención de
    campos, porque esa tasa se mide siempre.
    """
    por_nombre = {h.name: h for h in herramientas}

    if not respuesta.llamadas:
        return Veredicto(ok=False, modo=ModoFallo.NO_LLAMO)

    # Si hubo varias llamadas se juzga la que apunta a la herramienta esperada;
    # si ninguna lo hace, la primera. Así una respuesta correcta acompañada de
    # ruido no se clasifica por el ruido.
    llamada = next((c for c in respuesta.llamadas if c.nombre == esperada), respuesta.llamadas[0])
    nombre = llamada.nombre

    if nombre not in por_nombre:
        return Veredicto(
            ok=False,
            modo=ModoFallo.HERRAMIENTA_INEXISTENTE,
            herramienta_llamada=nombre,
            herramienta_alucinada=True,
        )
    if nombre != esperada:
        return Veredicto(
            ok=False, modo=ModoFallo.HERRAMIENTA_EQUIVOCADA, herramienta_llamada=nombre
        )

    try:
        argumentos = json.loads(llamada.argumentos_json)
    except (json.JSONDecodeError, TypeError):
        return Veredicto(ok=False, modo=ModoFallo.JSON_INVALIDO, herramienta_llamada=nombre)
    if not isinstance(argumentos, dict):
        return Veredicto(ok=False, modo=ModoFallo.JSON_INVALIDO, herramienta_llamada=nombre)

    esquema = por_nombre[nombre].input_schema
    propiedades: dict[str, Any] = esquema.get("properties", {})
    requeridos: list[str] = list(esquema.get("required", []))
    esperados: dict[str, Any] = dict(argumentos_esperados or {})

    faltantes = tuple(c for c in dict.fromkeys([*requeridos, *esperados]) if c not in argumentos)
    inventados = tuple(c for c in argumentos if propiedades and c not in propiedades)
    alterados = tuple(c for c, v in esperados.items() if c in argumentos and argumentos[c] != v)

    if faltantes:
        return Veredicto(
            ok=False,
            modo=ModoFallo.CAMPO_FALTANTE,
            herramienta_llamada=nombre,
            campos_faltantes=faltantes,
            campos_inventados=inventados,
        )
    if alterados:
        return Veredicto(
            ok=False,
            modo=ModoFallo.TEXTO_ALTERADO,
            herramienta_llamada=nombre,
            campos_alterados=alterados,
            campos_inventados=inventados,
        )
    if inventados:
        return Veredicto(
            ok=False,
            modo=ModoFallo.ARGUMENTO_INVENTADO,
            herramienta_llamada=nombre,
            campos_inventados=inventados,
        )
    return Veredicto(ok=True, herramienta_llamada=nombre)


# --------------------------------------------------------------------------- #
# Cuerpos de código reales para el perfil `code_blob`
# --------------------------------------------------------------------------- #

# Estos bloques existen para ser hostiles a un serializador JSON descuidado:
# comillas dobles y simples, llaves de f-string, barras invertidas, backticks,
# expresiones regulares y acentos. Si el modelo altera un solo byte, el
# `apply_patch` equivalente no aplicaría.

BLOQUE_PY_VIEJO = r'''def normaliza_ruta(cruda: str, *, raiz: str = "") -> str:
    """Normaliza una ruta del workspace a separador POSIX."""
    limpia = cruda.strip().replace("\\", "/")
    while limpia.startswith("./"):
        limpia = limpia[2:]
    if not limpia:
        raise ValueError(f"ruta vacía: {cruda!r}")
    if raiz and not limpia.startswith(raiz):
        limpia = f"{raiz.rstrip('/')}/{limpia}"
    partes: list[str] = []
    for parte in limpia.split("/"):
        if parte in ("", "."):
            continue
        if parte == "..":
            if not partes:
                raise ValueError(f"la ruta {cruda!r} se escapa de la raíz")
            partes.pop()
            continue
        partes.append(parte)
    return "/".join(partes)'''

BLOQUE_PY_NUEVO = r'''def normaliza_ruta(
    cruda: str, *, raiz: str = "", permitir_absoluta: bool = False
) -> str:
    """Normaliza una ruta del workspace a separador POSIX.

    `permitir_absoluta=False` es deliberado: una herramienta que acepta "/etc/passwd"
    porque "el usuario lo pidió" es una fuga, no una función de rutas.
    """
    limpia = cruda.strip().replace("\\", "/")
    if limpia.startswith("/") and not permitir_absoluta:
        raise ValueError(f"ruta absoluta no permitida: {cruda!r}")
    while limpia.startswith("./"):
        limpia = limpia[2:]
    if not limpia:
        raise ValueError(f"ruta vacía: {cruda!r}")
    if raiz and not limpia.startswith(raiz):
        limpia = f"{raiz.rstrip('/')}/{limpia}"
    partes: list[str] = []
    for parte in limpia.split("/"):
        if parte in ("", "."):
            continue
        if parte == "..":
            if not partes:
                raise ValueError(f"la ruta {cruda!r} se escapa de la raíz")
            partes.pop()
            continue
        partes.append(parte)
    return "/".join(partes)'''

BLOQUE_TS_VIEJO = r"""// Formatea un diff unificado mínimo. Ojo: la cabecera lleva «/» siempre,
// también en Windows, porque el índice de Git no conoce otra convención.
export function formatearDiff(archivo: string, lineas: string[]): string {
  const cabecera = `--- a/${archivo}\n+++ b/${archivo}`;
  const cuerpo = lineas
    .map((linea) => {
      if (/^[+-]/.test(linea)) return linea;
      return ` ${linea.replace(/\t/g, "    ")}`;
    })
    .join("\n");
  return `${cabecera}\n${cuerpo}\n`;
}"""

BLOQUE_TS_NUEVO = r"""export function formatearDiff(
  archivo: string,
  lineas: string[],
  opciones: { contexto?: number; tabulador?: string } = {},
): string {
  const { contexto = 3, tabulador = "    " } = opciones;
  const cabecera = `--- a/${archivo}\n+++ b/${archivo}`;
  const utiles = lineas.slice(0, Math.max(contexto, lineas.length));
  const cuerpo = utiles
    .map((linea) => {
      if (/^[+-]/.test(linea)) return linea;
      return ` ${linea.replace(/\t/g, tabulador)}`;
    })
    .join("\n");
  if (!cuerpo.trim()) throw new Error(`diff vacío para «${archivo}»`);
  return `${cabecera}\n${cuerpo}\n`;
}"""


def texto_largo(indice: int, *, minimo_bytes: int = 2048) -> str:
    """Genera >2 KiB de prosa determinista para el perfil `long_string`."""
    lineas: list[str] = []
    i = 0
    while len("\n".join(lineas).encode("utf-8")) < minimo_bytes:
        lineas.append(
            f"{i:03d}. Bitácora {indice}: la sesión anotó una decisión de diseño, su "
            f"motivo y el coste de revertirla; sin esta línea el relevo del agente "
            f"número {indice + i} tendría que releer el repositorio entero."
        )
        i += 1
    return "\n".join(lineas)


# --------------------------------------------------------------------------- #
# Catálogo de herramientas plausibles de Forge
# --------------------------------------------------------------------------- #

# (nombre, descripción, parámetro requerido, valor de ejemplo, plantilla de petición)
_CATALOGO: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "read_file",
        "Lee un archivo del workspace",
        "path",
        "apps/api/main.py",
        "Enséñame el contenido de {v}.",
    ),
    (
        "write_file",
        "Escribe un archivo completo",
        "path",
        "notas/plan.md",
        "Crea el archivo {v} desde cero.",
    ),
    (
        "apply_patch",
        "Aplica un parche exacto a un archivo",
        "path",
        "packages/core/agent.py",
        "Aplica el parche pendiente sobre {v}.",
    ),
    (
        "delete_file",
        "Borra un archivo del workspace",
        "path",
        "tmp/basura.log",
        "Elimina {v} del workspace.",
    ),
    (
        "move_file",
        "Mueve o renombra un archivo",
        "path",
        "docs/viejo.md",
        "Mueve el archivo {v} a otro sitio.",
    ),
    (
        "list_dir",
        "Lista el contenido de un directorio",
        "path",
        "packages/llm",
        "Dime qué hay dentro del directorio {v}.",
    ),
    (
        "grep_repo",
        "Busca un patrón por todo el repositorio",
        "pattern",
        "TODO\\(forge\\)",
        "Busca el patrón {v} en todo el repo.",
    ),
    (
        "find_symbol",
        "Localiza la definición de un símbolo",
        "symbol",
        "ModelCard",
        "¿Dónde está definido el símbolo {v}?",
    ),
    (
        "rename_symbol",
        "Renombra un símbolo en todo el repositorio",
        "symbol",
        "ProbeResult",
        "Renombra el símbolo {v} en todos sus usos.",
    ),
    (
        "outline_file",
        "Devuelve el esqueleto de un archivo",
        "path",
        "packages/core/loop.py",
        "Dame el esquema de funciones y clases de {v}.",
    ),
    (
        "run_command",
        "Ejecuta un comando en el workspace",
        "command",
        "uv sync",
        "Ejecuta el comando {v}.",
    ),
    (
        "run_tests",
        "Ejecuta la suite de pruebas",
        "target",
        "packages/forge-probe",
        "Corre las pruebas de {v}.",
    ),
    ("run_linter", "Ejecuta el linter", "target", "packages/llm", "Pasa el linter por {v}."),
    ("run_formatter", "Aplica el formateador", "target", "apps/api", "Formatea el código de {v}."),
    (
        "type_check",
        "Ejecuta el comprobador de tipos",
        "target",
        "packages/core",
        "Comprueba los tipos de {v}.",
    ),
    ("build_project", "Compila el proyecto", "target", "apps/web", "Compila {v}."),
    ("install_deps", "Instala dependencias", "manager", "uv", "Instala las dependencias con {v}."),
    ("clean_cache", "Limpia cachés de build", "scope", "pytest", "Limpia la caché de {v}."),
    (
        "git_status",
        "Muestra el estado del árbol de trabajo",
        "repo",
        "edecan",
        "¿Cómo está el árbol de trabajo del repo {v}?",
    ),
    (
        "git_diff",
        "Muestra el diff sin confirmar",
        "repo",
        "edecan",
        "Enséñame el diff pendiente del repo {v}.",
    ),
    (
        "git_log",
        "Muestra el historial de commits",
        "repo",
        "acme",
        "Dame el historial de commits de {v}.",
    ),
    (
        "git_commit",
        "Crea un commit",
        "message",
        "fix: cierra el bucle de relevo",
        "Haz un commit con el mensaje {v}.",
    ),
    ("git_branch", "Crea una rama", "name", "forge/fase-0", "Crea la rama {v}."),
    ("git_checkout", "Cambia de rama", "name", "main", "Cámbiate a la rama {v}."),
    (
        "git_stash",
        "Guarda los cambios en el stash",
        "repo",
        "edecan",
        "Guarda en el stash los cambios de {v}.",
    ),
    (
        "open_pull_request",
        "Abre una pull request",
        "title",
        "Sonda de tool-calling",
        "Abre una pull request titulada {v}.",
    ),
    (
        "review_pull_request",
        "Revisa una pull request",
        "number",
        "412",
        "Revisa la pull request número {v}.",
    ),
    ("merge_branch", "Fusiona una rama", "name", "forge/fase-0", "Fusiona la rama {v}."),
    (
        "read_logs",
        "Lee los logs de un servicio",
        "service",
        "edecan-api",
        "Léeme los logs del servicio {v}.",
    ),
    (
        "tail_service",
        "Sigue los logs en vivo",
        "service",
        "edecan-worker",
        "Sigue en vivo los logs del servicio {v}.",
    ),
    (
        "restart_service",
        "Reinicia un servicio",
        "service",
        "edecan-local",
        "Reinicia el servicio {v}.",
    ),
    (
        "deploy_preview",
        "Despliega un entorno de vista previa",
        "environment",
        "preview-412",
        "Despliega una vista previa en {v}.",
    ),
    (
        "rollback_deploy",
        "Revierte un despliegue",
        "environment",
        "produccion",
        "Revierte el último despliegue de {v}.",
    ),
    (
        "query_database",
        "Ejecuta una consulta de lectura",
        "sql",
        "select 1",
        "Ejecuta la consulta {v}.",
    ),
    (
        "run_migration",
        "Aplica una migración",
        "revision",
        "0042_perfil",
        "Aplica la migración {v}.",
    ),
    (
        "dump_schema",
        "Vuelca el esquema de la base de datos",
        "database",
        "edecan",
        "Vuelca el esquema de la base de datos {v}.",
    ),
    (
        "seed_fixtures",
        "Carga datos de prueba",
        "fixture",
        "contactos_demo",
        "Carga los datos de prueba {v}.",
    ),
    (
        "fetch_url",
        "Descarga una URL",
        "url",
        "https://example.com/spec.json",
        "Descarga el contenido de {v}.",
    ),
    (
        "search_docs",
        "Busca en la documentación interna",
        "query",
        "contexto útil",
        "Busca en la documentación interna {v}.",
    ),
    ("read_issue", "Lee una incidencia", "number", "77", "Léeme la incidencia número {v}."),
    (
        "comment_issue",
        "Comenta en una incidencia",
        "number",
        "78",
        "Escribe un comentario en la incidencia número {v}.",
    ),
    ("close_issue", "Cierra una incidencia", "number", "79", "Cierra la incidencia número {v}."),
    (
        "list_tasks",
        "Lista las tareas de la sesión",
        "session",
        "sesion-9",
        "Lista las tareas de la sesión {v}.",
    ),
    (
        "create_task",
        "Crea una tarea",
        "title",
        "medir el contexto útil",
        "Crea una tarea titulada {v}.",
    ),
    ("update_task", "Actualiza una tarea", "id", "T-31", "Actualiza la tarea {v}."),
    (
        "request_approval",
        "Pide aprobación humana",
        "reason",
        "toca produccion",
        "Pide aprobación humana porque {v}.",
    ),
    (
        "record_decision",
        "Registra una decisión de arquitectura",
        "title",
        "transporte XML",
        "Registra la decisión de arquitectura {v}.",
    ),
    (
        "take_screenshot",
        "Captura la pantalla del navegador",
        "url",
        "http://localhost:3000",
        "Haz una captura de {v}.",
    ),
)

ESCALONES_HERRAMIENTAS: tuple[int, ...] = (4, 8, 12, 20, 32, 48)
ESCALONES_SCHEMA_BYTES: tuple[int, ...] = (1_024, 4_096, 16_384, 65_536, 262_144)


def _tool_catalogo(indice: int) -> ToolSpec:
    nombre, descripcion, parametro, _valor, _plantilla = _CATALOGO[indice]
    return ToolSpec(
        name=nombre,
        description=descripcion,
        input_schema={
            "type": "object",
            "properties": {parametro: {"type": "string", "description": f"{parametro} objetivo"}},
            "required": [parametro],
            "additionalProperties": False,
        },
    )


# --------------------------------------------------------------------------- #
# Casos
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class CasoTool:
    """Un intento concreto: qué se ofrece, qué se pide y qué se espera exacto."""

    id: str
    etiqueta: str
    herramientas: list[ToolSpec]
    esperada: str
    prompt: str
    argumentos_esperados: dict[str, Any] | None
    max_tokens: int


SISTEMA = (
    "Eres el ejecutor de herramientas de Forge. Responde SIEMPRE invocando "
    "exactamente una herramienta, nunca en prosa. Los valores de texto se copian "
    "literalmente, byte a byte, sin reindentar, sin reescapar, sin traducir y sin "
    "añadir ni quitar campos que no estén en el esquema."
)

_RESERVA_RAZONAMIENTO = 2_048
"""El modelo objetivo razona siempre y el razonamiento se factura como salida.
Sin esta reserva, una respuesta correcta llega con `content` vacío por corte de
presupuesto y la sonda mediría su propio `max_tokens`, no al modelo."""


def _presupuesto_salida(argumentos: Mapping[str, Any] | None) -> int:
    """Tokens de salida a pedir: el contenido estimado más la reserva de razonamiento."""
    if not argumentos:
        return _RESERVA_RAZONAMIENTO + 256
    bytes_json = len(json.dumps(argumentos, ensure_ascii=False).encode("utf-8"))
    return _RESERVA_RAZONAMIENTO + 256 + int(bytes_json / 2.5)


def _tool_apply_patch(propiedades_extra: Mapping[str, Any] | None = None) -> ToolSpec:
    propiedades: dict[str, Any] = {
        "path": {"type": "string", "description": "Ruta POSIX del archivo a parchear"},
        "old_text": {"type": "string", "description": "Texto exacto que hay hoy en el archivo"},
        "new_text": {"type": "string", "description": "Texto exacto que debe quedar"},
    }
    propiedades.update(propiedades_extra or {})
    return ToolSpec(
        name="apply_patch",
        description="Sustituye old_text por new_text en path. El cotejo es exacto.",
        input_schema={
            "type": "object",
            "properties": propiedades,
            "required": ["path", "old_text", "new_text"],
            "additionalProperties": False,
        },
    )


def _caso_scalar(i: int) -> CasoTool:
    herramienta = ToolSpec(
        name="read_file",
        description="Lee un fragmento de un archivo del workspace.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Ruta POSIX relativa a la raíz"},
                "offset": {"type": "integer", "description": "Primera línea, 1-indexada"},
                "limit": {"type": "integer", "description": "Cuántas líneas leer"},
            },
            "required": ["path", "offset", "limit"],
            "additionalProperties": False,
        },
    )
    ruta = f"apps/api/edecan_api/routers/perfil_{i:02d}.py"
    offset, limit = 40 + i, 25 + i
    return CasoTool(
        id=f"scalar-{i:02d}",
        etiqueta=ArgProfile.SCALAR.value,
        herramientas=[herramienta],
        esperada="read_file",
        prompt=(
            f"Lee el archivo {ruta} empezando en la línea {offset} y trayendo "
            f"exactamente {limit} líneas."
        ),
        argumentos_esperados={"path": ruta, "offset": offset, "limit": limit},
        max_tokens=_presupuesto_salida({"path": ruta, "offset": offset, "limit": limit}),
    )


def _caso_nested(i: int) -> CasoTool:
    herramienta = ToolSpec(
        name="plan_edits",
        description="Registra el plan de edición de una tarea antes de tocar archivos.",
        input_schema={
            "type": "object",
            "properties": {
                "plan": {
                    "type": "object",
                    "properties": {
                        "objetivo": {"type": "string"},
                        "reversible": {"type": "boolean"},
                        "pasos": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "archivo": {"type": "string"},
                                    "accion": {
                                        "type": "string",
                                        "enum": ["crear", "editar", "borrar"],
                                    },
                                    "motivo": {"type": "string"},
                                },
                                "required": ["archivo", "accion", "motivo"],
                            },
                        },
                    },
                    "required": ["objetivo", "reversible", "pasos"],
                }
            },
            "required": ["plan"],
            "additionalProperties": False,
        },
    )
    objetivo = f"Aislar el router de perfil número {i}"
    plan = {
        "objetivo": objetivo,
        "reversible": i % 2 == 0,
        "pasos": [
            {
                "archivo": f"apps/api/edecan_api/routers/perfil_{i:02d}.py",
                "accion": "editar",
                "motivo": "extraer la validación a una función propia",
            },
            {
                "archivo": f"apps/api/tests/test_perfil_{i:02d}.py",
                "accion": "crear",
                "motivo": "cubrir la validación extraída",
            },
        ],
    }
    pasos = "\n".join(f'- {p["accion"]} {p["archivo"]}: "{p["motivo"]}"' for p in plan["pasos"])
    reversible = "sí" if plan["reversible"] else "no"
    return CasoTool(
        id=f"nested-{i:02d}",
        etiqueta=ArgProfile.NESTED.value,
        herramientas=[herramienta],
        esperada="plan_edits",
        # El objetivo y cada motivo van ENTRE COMILLAS y se pide copiarlos
        # literalmente. Sin ese delimitador, la puntuación de la frase que los
        # envuelve es ambigua: con «Objetivo: Aislar el router número 8.» el
        # modelo devuelve el punto final —razonablemente— y una comparación por
        # igualdad exacta lo cuenta como campo alterado. Medido: eso solo
        # producía 14 de 17 fallos de este perfil, y era un defecto del caso, no
        # del modelo. Un caso que no se puede acertar no mide nada.
        prompt=(
            f"Registra este plan. Copia los textos entrecomillados TAL CUAL, sin "
            f"añadir ni quitar puntuación.\n"
            f'Objetivo: "{objetivo}"\n'
            f"¿Es reversible?: {reversible}\n"
            f"Pasos, en este orden:\n{pasos}"
        ),
        argumentos_esperados={"plan": plan},
        max_tokens=_presupuesto_salida({"plan": plan}),
    )


def _caso_code_blob(i: int) -> CasoTool:
    if i % 2 == 0:
        ruta = f"packages/core/edecan_core/rutas_{i:02d}.py"
        viejo, nuevo, lenguaje = BLOQUE_PY_VIEJO, BLOQUE_PY_NUEVO, "Python"
    else:
        ruta = f"apps/web/src/lib/diff_{i:02d}.ts"
        viejo, nuevo, lenguaje = BLOQUE_TS_VIEJO, BLOQUE_TS_NUEVO, "TypeScript"
    esperados = {"path": ruta, "old_text": viejo, "new_text": nuevo}
    return CasoTool(
        id=f"code_blob-{i:02d}",
        etiqueta=ArgProfile.CODE_BLOB.value,
        herramientas=[_tool_apply_patch()],
        esperada="apply_patch",
        prompt=(
            f"En el archivo {ruta} ({lenguaje}) sustituye el bloque actual por el nuevo. "
            "Copia los dos bloques byte a byte, sin cambiar una sola comilla, barra "
            "invertida, tilde ni espacio.\n\n"
            f"=== BLOQUE ACTUAL ===\n{viejo}\n=== FIN BLOQUE ACTUAL ===\n\n"
            f"=== BLOQUE NUEVO ===\n{nuevo}\n=== FIN BLOQUE NUEVO ==="
        ),
        argumentos_esperados=esperados,
        max_tokens=_presupuesto_salida(esperados),
    )


def _caso_long_string(i: int) -> CasoTool:
    herramienta = ToolSpec(
        name="write_note",
        description="Guarda una nota de sesión en el workspace.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Ruta POSIX del archivo de nota"},
                "content": {"type": "string", "description": "Contenido literal de la nota"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    )
    ruta = f"notas/bitacora_{i:02d}.md"
    contenido = texto_largo(i)
    esperados = {"path": ruta, "content": contenido}
    return CasoTool(
        id=f"long_string-{i:02d}",
        etiqueta=ArgProfile.LONG_STRING.value,
        herramientas=[herramienta],
        esperada="write_note",
        prompt=(
            f"Guarda esta nota en {ruta}. El contenido va literal, completo y sin "
            "resumir ni reordenar ni una línea.\n\n"
            f"=== NOTA ===\n{contenido}\n=== FIN NOTA ==="
        ),
        argumentos_esperados=esperados,
        max_tokens=_presupuesto_salida(esperados),
    )


_CONSTRUCTORES = {
    ArgProfile.SCALAR: _caso_scalar,
    ArgProfile.NESTED: _caso_nested,
    ArgProfile.CODE_BLOB: _caso_code_blob,
    ArgProfile.LONG_STRING: _caso_long_string,
}


def casos_de_perfil(perfil: ArgProfile, n: int) -> list[CasoTool]:
    """Construye `n` casos del perfil. Deterministas: mismo n, mismos casos."""
    return [_CONSTRUCTORES[perfil](i) for i in range(n)]


def casos_de_seleccion(n_herramientas: int, n: int, *, semilla: int = 0) -> list[CasoTool]:
    """Casos para medir la precisión de selección con `n_herramientas` ofrecidas.

    El objetivo se sortea con semilla fija sobre TODA la superficie ofrecida: si
    el objetivo estuviera siempre entre las primeras herramientas, ofrecer 48
    saldría artificialmente barato y la medición no valdría nada.
    """
    ofrecidas = [_tool_catalogo(i) for i in range(n_herramientas)]
    rng = random.Random(1_000 + semilla + n_herramientas)
    casos: list[CasoTool] = []
    for i in range(n):
        idx = rng.randrange(n_herramientas)
        nombre, _desc, parametro, valor, plantilla = _CATALOGO[idx]
        casos.append(
            CasoTool(
                id=f"seleccion-{n_herramientas:02d}-{i:02d}",
                etiqueta=f"herramientas={n_herramientas}",
                herramientas=ofrecidas,
                esperada=nombre,
                prompt=plantilla.format(v=valor),
                argumentos_esperados={parametro: valor},
                max_tokens=_presupuesto_salida({parametro: valor}),
            )
        )
    return casos


def _relleno_schema(objetivo_bytes: int) -> dict[str, Any]:
    """Propiedades opcionales plausibles hasta que el esquema pese `objetivo_bytes`."""
    extra: dict[str, Any] = {}
    i = 0
    while len(json.dumps(_tool_apply_patch(extra).input_schema).encode("utf-8")) < objetivo_bytes:
        extra[f"opcion_{i:04d}"] = {
            "type": "string",
            "description": (
                f"Modificador opcional {i:04d} del parche: ajusta el cotejo de "
                "contexto, la política de reindentado y el tratamiento de finales "
                "de línea cuando el archivo destino mezcla convenciones."
            ),
        }
        i += 1
    return extra


def casos_de_schema(objetivo_bytes: int, n: int) -> tuple[list[CasoTool], int]:
    """Casos con el esquema inflado a ~`objetivo_bytes`. Devuelve el tamaño real.

    Se usa la carga `scalar` a propósito: si el perfil de argumentos fuese
    `code_blob`, un fallo no distinguiría "el esquema es demasiado grande" de "el
    bloque de código es difícil", y la sonda ya mide eso por separado.
    """
    herramienta = _tool_apply_patch(_relleno_schema(objetivo_bytes))
    bytes_reales = len(json.dumps(herramienta.input_schema).encode("utf-8"))
    casos: list[CasoTool] = []
    for i in range(n):
        ruta = f"packages/core/edecan_core/rutas_{i:02d}.py"
        esperados = {
            "path": ruta,
            "old_text": f"UMBRAL = {40 + i}",
            "new_text": f"UMBRAL = {41 + i}",
        }
        casos.append(
            CasoTool(
                id=f"schema-{bytes_reales}-{i:02d}",
                etiqueta=f"schema_bytes={bytes_reales}",
                herramientas=[herramienta],
                esperada="apply_patch",
                prompt=(
                    f"En {ruta} sustituye la línea «UMBRAL = {40 + i}» por «UMBRAL = "
                    f"{41 + i}». Copia ambos textos exactos."
                ),
                argumentos_esperados=esperados,
                max_tokens=_presupuesto_salida(esperados),
            )
        )
    return casos, bytes_reales


# --------------------------------------------------------------------------- #
# Acumulador de una serie
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Serie:
    """Estadísticos de una tanda de intentos comparables."""

    etiqueta: str
    exitos: int = 0
    intentos: int = 0
    errores_proveedor: int = 0
    modos: dict[str, int] = field(default_factory=dict)
    herramienta_alucinada: int = 0
    argumento_inventado: int = 0
    latencias: list[float] = field(default_factory=list)
    tokens_entrada: int = 0
    tokens_salida: int = 0
    tokens_cacheados: int = 0
    tokens_razonamiento: int = 0
    coste_usd: float = 0.0
    neuronas: float = 0.0

    def registrar(self, veredicto: Veredicto) -> None:
        self.intentos += 1
        if veredicto.ok:
            self.exitos += 1
        elif veredicto.modo is not None:
            self.modos[veredicto.modo.value] = self.modos.get(veredicto.modo.value, 0) + 1
        if veredicto.herramienta_alucinada:
            self.herramienta_alucinada += 1
        if veredicto.campos_inventados:
            self.argumento_inventado += 1

    @property
    def reliability(self) -> Reliability:
        return Reliability(successes=self.exitos, trials=self.intentos)

    @property
    def latencia(self) -> Latencia | None:
        if not self.latencias:
            return None
        ordenadas = sorted(self.latencias)
        return Latencia(
            p50=_percentil(ordenadas, 0.50),
            p95=_percentil(ordenadas, 0.95),
            muestras=len(ordenadas),
        )

    def detalle(self) -> dict[str, Any]:
        contenido = max(0, self.tokens_salida - self.tokens_razonamiento)
        return {
            "etiqueta": self.etiqueta,
            "intentos": self.intentos,
            "exitos": self.exitos,
            "modos_de_fallo": dict(sorted(self.modos.items())),
            "errores_proveedor": self.errores_proveedor,
            "tasa_herramienta_alucinada": (
                self.herramienta_alucinada / self.intentos if self.intentos else None
            ),
            "tasa_argumento_inventado": (
                self.argumento_inventado / self.intentos if self.intentos else None
            ),
            "tokens_entrada": self.tokens_entrada,
            "tokens_salida": self.tokens_salida,
            "tokens_cacheados": self.tokens_cacheados,
            "tokens_razonamiento": self.tokens_razonamiento,
            "sobrecarga_razonamiento": (
                self.tokens_razonamiento / contenido if contenido else None
            ),
            "neuronas": self.neuronas or None,
            "coste_usd": round(self.coste_usd, 6),
        }


def _percentil(ordenadas: Sequence[float], q: float) -> float:
    if not ordenadas:
        return 0.0
    if len(ordenadas) == 1:
        return float(ordenadas[0])
    pos = q * (len(ordenadas) - 1)
    bajo = int(pos)
    alto = min(bajo + 1, len(ordenadas) - 1)
    return float(ordenadas[bajo] + (ordenadas[alto] - ordenadas[bajo]) * (pos - bajo))


# --------------------------------------------------------------------------- #
# La sonda
# --------------------------------------------------------------------------- #

REVISION = "tools/1"
"""Versión del criterio de medición. Cambiarlo invalida la comparación entre
cards: si cambia qué cuenta como éxito, los números de ayer no son los de hoy."""

UMBRAL_SELECCION = 0.90

_Z2 = 1.959963984540054**2


def techo_lower_95(n: int) -> float:
    """Mayor `Reliability.lower_95` alcanzable con `n` intentos: el caso perfecto.

    Con `p = 1` el intervalo de Wilson colapsa a `n / (n + z²)`. Con n = 12 eso
    son 0,758: **una serie sin un solo fallo no llegaría a 0,90 jamás.** Medir un
    techo con un criterio inalcanzable no es ser exigente, es teatro.
    """
    return n / (n + _Z2) if n > 0 else 0.0


def n_minimo_para(umbral: float) -> int:
    """Intentos mínimos para que `umbral` sea alcanzable sobre `lower_95`.

    Para 0,90 son 35. Es la razón de que los valores por defecto de esta sonda
    sean 40 y no 20: 20 es el suelo que exige la fase 0, pero con 20 intentos
    perfectos el límite inferior se queda en 0,839 y el umbral
    `native_tools.code_blob.lower_95 ≥ 0.90` del contrato no se podría cumplir ni
    con un modelo impecable.
    """
    if not 0.0 < umbral < 1.0:
        raise ValueError(f"umbral fuera de rango: {umbral!r}")
    return math.ceil(_Z2 * umbral / (1.0 - umbral))


class SondaToolCalling:
    """Mide la fiabilidad de tool-calling de un modelo, desglosada por perfil.

    Todas las series se cortan en cuanto el gasto acumulado alcanza `max_usd`:
    lo medido hasta ahí se reporta, y lo que no se llegó a medir **no aparece**
    en vez de aparecer con un valor conservador inventado.
    """

    def __init__(
        self,
        proveedor: ProveedorHerramientas,
        *,
        dir_evidencia: Path,
        max_usd: float,
        intentos_por_perfil: int = 40,
        intentos_por_escalon: int = 40,
        perfiles: Sequence[ArgProfile] = tuple(ArgProfile),
        escalones_herramientas: Sequence[int] = ESCALONES_HERRAMIENTAS,
        escalones_schema: Sequence[int] = ESCALONES_SCHEMA_BYTES,
        precios: Precios | None = None,
        umbral_seleccion: float = UMBRAL_SELECCION,
    ) -> None:
        if intentos_por_perfil < 20:
            raise ValueError(
                "la fase 0 exige N >= 20 intentos por perfil: por debajo, el límite "
                "inferior de Wilson es tan ancho que la medición no decide nada"
            )
        minimo = n_minimo_para(umbral_seleccion)
        if intentos_por_escalon < minimo:
            raise ValueError(
                f"con {intentos_por_escalon} intentos por escalón el límite inferior "
                f"máximo es {techo_lower_95(intentos_por_escalon):.3f}: el umbral "
                f"{umbral_seleccion:.2f} sería inalcanzable incluso sin un solo fallo. "
                f"Hacen falta {minimo}."
            )
        self.proveedor = proveedor
        self.dir_evidencia = dir_evidencia
        self.max_usd = max_usd
        self.intentos_por_perfil = intentos_por_perfil
        self.intentos_por_escalon = intentos_por_escalon
        self.perfiles = tuple(perfiles)
        self.escalones_herramientas = tuple(escalones_herramientas)
        self.escalones_schema = tuple(escalones_schema)
        self.precios = precios or Precios()
        self.umbral_seleccion = umbral_seleccion
        self.gasto_usd = 0.0
        self.presupuesto_agotado = False

    # -- ejecución ------------------------------------------------------- #

    async def ejecutar(self) -> list[ProbeResult]:
        """Corre todas las series y devuelve un `ProbeResult` por medición.

        No toca la `ModelCard`: eso es del runner, que es quien sabe qué
        medición va a qué campo.
        """
        self.dir_evidencia.mkdir(parents=True, exist_ok=True)
        resultados: list[ProbeResult] = []
        for perfil in self.perfiles:
            resultados.append(await self._sondar_perfil(perfil))
            if self.presupuesto_agotado:
                return resultados
        if self.escalones_herramientas:
            resultados.append(await self._sondar_max_tools())
            if self.presupuesto_agotado:
                return resultados
        if self.escalones_schema:
            resultados.append(await self._sondar_max_schema())
        return resultados

    async def _sondar_perfil(self, perfil: ArgProfile) -> ProbeResult:
        inicio = time.perf_counter()
        ruta = self.dir_evidencia / f"tools_{perfil.value}.jsonl"
        serie = _Serie(etiqueta=perfil.value)
        agotado = False
        try:
            await self._correr(casos_de_perfil(perfil, self.intentos_por_perfil), serie, ruta)
        except PresupuestoAgotado:
            agotado = True
        detalle = serie.detalle()
        detalle["presupuesto_agotado"] = agotado
        detalle["lower_95_maximo_alcanzable"] = techo_lower_95(serie.intentos)
        medido = serie.intentos > 0
        if medido:
            # El runner necesita saber A QUÉ perfil pertenece esta fiabilidad:
            # `Capability.NATIVE_TOOLS` sola no lo dice, y sin esto la medición se
            # paga y luego se cae de la tarjeta en silencio, dejando el umbral en
            # SIN_DATO para siempre.
            detalle["arg_profile"] = perfil.value
            detalle["modelcard"] = {"native_tools": {perfil.value: serie.reliability.model_dump()}}
        return ProbeResult(
            probe=f"native_tools.{perfil.value}",
            capability=Capability.NATIVE_TOOLS,
            ok=medido,
            valor=serie.reliability.lower_95 if medido else None,
            reliability=serie.reliability if medido else None,
            latencia=serie.latencia,
            detalle=detalle,
            evidencia=[str(ruta)] if ruta.exists() else [],
            error=None if medido else self._motivo_sin_dato(serie, agotado),
            duracion_s=time.perf_counter() - inicio,
        )

    async def _sondar_max_tools(self) -> ProbeResult:
        """Mide dónde se derrumba la precisión de selección al crecer la superficie."""
        inicio = time.perf_counter()
        ruta = self.dir_evidencia / "tools_max_tools.jsonl"
        escalones: list[dict[str, Any]] = []
        ultimo_bueno: int | None = None
        rompio_en: int | None = None
        agotado = False
        for n_herramientas in self.escalones_herramientas:
            serie = _Serie(etiqueta=f"herramientas={n_herramientas}")
            try:
                await self._correr(
                    casos_de_seleccion(n_herramientas, self.intentos_por_escalon), serie, ruta
                )
            except PresupuestoAgotado:
                agotado = True
            if serie.intentos == 0:
                break
            fiabilidad = serie.reliability
            escalones.append(
                {"herramientas": n_herramientas, "lower_95": fiabilidad.lower_95, **serie.detalle()}
            )
            if fiabilidad.lower_95 >= self.umbral_seleccion:
                ultimo_bueno = n_herramientas
            else:
                rompio_en = n_herramientas
                break
            if agotado:
                break

        # Si ningún escalón bajó del umbral, el techo no se encontró: el valor es
        # el mayor escalón probado y queda dicho que no se llegó a romper.
        valor = ultimo_bueno if ultimo_bueno is not None else (0 if escalones else None)
        return ProbeResult(
            probe="native_tools.max_tools_effective",
            capability=Capability.NATIVE_TOOLS,
            ok=bool(escalones),
            valor=valor,
            reliability=None,
            latencia=None,
            detalle={
                "escalones": escalones,
                "umbral": self.umbral_seleccion,
                "rompio_en": rompio_en,
                "techo_no_encontrado": rompio_en is None and bool(escalones),
                "presupuesto_agotado": agotado,
                # Vía explícita a la tarjeta: `Capability.NATIVE_TOOLS` no dice a
                # qué campo va este número, así que sin esto se mide, se paga y
                # no llega.
                **({"modelcard": {"max_tools_effective": valor}} if valor is not None else {}),
            },
            evidencia=[str(ruta)] if ruta.exists() else [],
            error=None if escalones else "sin escalones medidos",
            duracion_s=time.perf_counter() - inicio,
        )

    async def _sondar_max_schema(self) -> ProbeResult:
        """Crece el JSON Schema ofrecido hasta que la fiabilidad se rompe."""
        inicio = time.perf_counter()
        ruta = self.dir_evidencia / "tools_max_schema.jsonl"
        escalones: list[dict[str, Any]] = []
        ultimo_bueno: int | None = None
        rompio_en: int | None = None
        agotado = False
        for objetivo in self.escalones_schema:
            casos, bytes_reales = casos_de_schema(objetivo, self.intentos_por_escalon)
            serie = _Serie(etiqueta=f"schema_bytes={bytes_reales}")
            try:
                await self._correr(casos, serie, ruta)
            except PresupuestoAgotado:
                agotado = True
            if serie.intentos == 0:
                break
            fiabilidad = serie.reliability
            escalones.append(
                {"schema_bytes": bytes_reales, "lower_95": fiabilidad.lower_95, **serie.detalle()}
            )
            if fiabilidad.lower_95 >= self.umbral_seleccion:
                ultimo_bueno = bytes_reales
            else:
                rompio_en = bytes_reales
                break
            if agotado:
                break

        valor = ultimo_bueno if ultimo_bueno is not None else (0 if escalones else None)
        return ProbeResult(
            probe="native_tools.max_schema_bytes",
            capability=Capability.NATIVE_TOOLS,
            ok=bool(escalones),
            valor=valor,
            reliability=None,
            latencia=None,
            detalle={
                "escalones": escalones,
                "umbral": self.umbral_seleccion,
                "rompio_en": rompio_en,
                "techo_no_encontrado": rompio_en is None and bool(escalones),
                "presupuesto_agotado": agotado,
                **({"modelcard": {"max_schema_bytes": valor}} if valor is not None else {}),
            },
            evidencia=[str(ruta)] if ruta.exists() else [],
            error=None if escalones else "sin escalones medidos",
            duracion_s=time.perf_counter() - inicio,
        )

    # -- motor ------------------------------------------------------------ #

    async def _correr(self, casos: Sequence[CasoTool], serie: _Serie, ruta: Path) -> None:
        """Ejecuta los casos de una serie, acumulando en `serie` y volcando evidencia."""
        with ruta.open("a", encoding="utf-8") as evidencia:
            for caso in casos:
                if self.gasto_usd >= self.max_usd:
                    self.presupuesto_agotado = True
                    raise PresupuestoAgotado(
                        f"gasto {self.gasto_usd:.4f} USD >= max_usd {self.max_usd:.4f} USD"
                    )
                t0 = time.perf_counter()
                try:
                    respuesta = await self.proveedor.invocar(
                        sistema=SISTEMA,
                        prompt=caso.prompt,
                        herramientas=caso.herramientas,
                        max_tokens=caso.max_tokens,
                    )
                except Exception as exc:  # noqa: BLE001 - un fallo de transporte no es del modelo
                    respuesta = RespuestaSonda(error=f"{type(exc).__name__}: {exc}")
                latencia_s = time.perf_counter() - t0

                coste = self.precios.coste(
                    entrada=respuesta.tokens_entrada,
                    cacheada=respuesta.tokens_cacheados,
                    salida=respuesta.tokens_salida,
                )
                self.gasto_usd += coste
                serie.coste_usd += coste
                serie.tokens_entrada += respuesta.tokens_entrada
                serie.tokens_salida += respuesta.tokens_salida
                serie.tokens_cacheados += respuesta.tokens_cacheados
                serie.tokens_razonamiento += respuesta.tokens_razonamiento
                serie.neuronas += respuesta.neuronas or 0.0

                if respuesta.error is not None:
                    # Que la red falle no dice nada del tool-calling del modelo:
                    # se registra aparte y NO entra en el denominador.
                    serie.errores_proveedor += 1
                    veredicto = None
                else:
                    serie.latencias.append(latencia_s)
                    veredicto = clasificar(
                        respuesta=respuesta,
                        herramientas=caso.herramientas,
                        esperada=caso.esperada,
                        argumentos_esperados=caso.argumentos_esperados,
                    )
                    serie.registrar(veredicto)

                evidencia.write(
                    json.dumps(
                        self._traza(caso, respuesta, veredicto, latencia_s, coste),
                        ensure_ascii=False,
                    )
                    + "\n"
                )

    def _traza(
        self,
        caso: CasoTool,
        respuesta: RespuestaSonda,
        veredicto: Veredicto | None,
        latencia_s: float,
        coste: float,
    ) -> dict[str, Any]:
        """Fila de evidencia cruda: todo lo que hace falta para auditar un número.

        No guarda credenciales de ningún tipo: la sonda nunca las ve. Los prompts
        y esquemas grandes se resumen por hash y tamaño para que el JSONL siga
        siendo legible; los argumentos devueltos se guardan enteros, porque son
        exactamente lo que hay que poder revisar a mano.
        """
        prompt_bytes = caso.prompt.encode("utf-8")
        esquemas = json.dumps(
            [h.input_schema for h in caso.herramientas], ensure_ascii=False
        ).encode("utf-8")
        return {
            "caso": caso.id,
            "etiqueta": caso.etiqueta,
            "revision": REVISION,
            "herramientas_ofrecidas": len(caso.herramientas),
            "herramienta_esperada": caso.esperada,
            "prompt_sha256": hashlib.sha256(prompt_bytes).hexdigest(),
            "prompt_bytes": len(prompt_bytes),
            "prompt": caso.prompt if len(prompt_bytes) <= 4_096 else None,
            "schema_bytes": len(esquemas),
            "schema_sha256": hashlib.sha256(esquemas).hexdigest(),
            "max_tokens": caso.max_tokens,
            "llamadas": [c.model_dump() for c in respuesta.llamadas],
            "contenido": respuesta.contenido,
            "razonamiento_bytes": len(respuesta.razonamiento.encode("utf-8")),
            "usage": {
                "tokens_entrada": respuesta.tokens_entrada,
                "tokens_salida": respuesta.tokens_salida,
                "tokens_cacheados": respuesta.tokens_cacheados,
                "tokens_razonamiento": respuesta.tokens_razonamiento,
                "neuronas": respuesta.neuronas,
            },
            "latencia_s": round(latencia_s, 6),
            "coste_usd": coste,
            "error_proveedor": respuesta.error,
            "veredicto": None if veredicto is None else veredicto.a_dict(),
        }

    @staticmethod
    def _motivo_sin_dato(serie: _Serie, agotado: bool) -> str:
        if agotado:
            return "presupuesto agotado antes del primer intento"
        if serie.errores_proveedor:
            return f"{serie.errores_proveedor} errores de proveedor y ningún intento válido"
        return "no se ejecutó ningún intento"


__all__ = [
    "ESCALONES_HERRAMIENTAS",
    "ESCALONES_SCHEMA_BYTES",
    "REVISION",
    "SISTEMA",
    "UMBRAL_SELECCION",
    "CasoTool",
    "LlamadaCruda",
    "ModoFallo",
    "Precios",
    "PresupuestoAgotado",
    "ProveedorHerramientas",
    "RespuestaSonda",
    "SondaToolCalling",
    "Veredicto",
    "casos_de_perfil",
    "casos_de_schema",
    "casos_de_seleccion",
    "clasificar",
    "n_minimo_para",
    "techo_lower_95",
    "texto_largo",
]
