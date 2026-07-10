"""Companion — control de la computadora local del usuario (`ARCHITECTURE.md`
§10.7, §10.12).

`ctx.extras["companion"]` es la clave reservada por el contrato: `None`, o un
callable `async (action: str, params: dict) -> dict` inyectado por la API
cuando hay un companion emparejado (`POST /v1/companion/pair-code` +
`WS /v1/companion/ws?code=`).

Guardrail de cumplimiento (riesgo-legal-tos) — por qué esta tool NO tiene un
`check_navigation` propio: a diferencia de `edecan_browser`
(`edecan_browser/policy.py::check_navigation`, que bloquea LinkedIn/checkout/
SSRF por dominio ANTES de cualquier fetch real, ver su docstring), esta tool
nunca recibe una URL — `accion`/`parametros` son coordenadas de pantalla,
texto a escribir o comandos de bajo nivel (`apps/companion/edecan_companion/
actions.py`: `input_pointer`, `input_key`, `screenshot`, ...), así que no hay
ningún dominio que un guardrail de código pueda inspeccionar aquí. La
exclusión de LinkedIn para este camino (incluida una sesión que el usuario ya
tenga abierta en pantalla) vive, en cambio, en la regla 3 del system prompt
(`edecan_core.persona._REGLAS_SEGURIDAD_ES`/`_SAFETY_RULES_EN`,
`prompts/persona_v2.md`) y en la advertencia específica que ve quien aprueba
en `apps/web/src/components/chat/ConfirmationCard.tsx` — ambas se SUMAN a
(no reemplazan) las capas ya reales de esta tool: `dangerous = True` +
confirmación humana en el chat, `remote_input_enabled` apagado por defecto en
el companion, aprobación local por acción, y el permiso de Accesibilidad de
macOS que solo un clic humano puede conceder.
"""

from __future__ import annotations

from typing import Any

from edecan_core import Tool, ToolContext, ToolResult
from edecan_schemas.plans import (
    FLAG_COMPANION_IDE,
    FLAG_COMPANION_REMOTE_INPUT,
    FLAG_COMPANION_REMOTE_VIEW,
)

_MENSAJE_SIN_EMPAREJAR = (
    "No tienes un companion (la app de escritorio de Edecán) emparejado todavía. "
    "Instálalo, genera un código de emparejamiento en /app/ajustes y vuelve a pedírmelo."
)

# `edecan_companion.actions.ACTIONS` es un ÚNICO dispatch table compartido por
# TRES superficies distintas: esta tool de chat, el IDE embebido
# (`routers/ide.py`) y el control remoto (`routers/remote.py`). Esas dos
# últimas SÍ filtran por el flag de plan más fino antes de reenviar la acción
# (`ide._require_companion_ide`, `remote._require_remote_view`/
# `_require_remote_control`) — esta tool, en cambio, solo exigía el flag base
# `companion` (`requires_flags` de la clase), así que un tenant cuyo plan
# niega `companion.ide`/`companion.remote_input` podía alcanzar la MISMA
# acción igual, con tal de pedírselo al modelo por chat (hallazgo de
# seguridad, riesgo-legal-tos: `hosted_basic` tiene `companion=True` pero
# `companion.remote_input=False`). `_bloqueo_por_plan` replica, acción por
# acción, los mismos flags que ya exige el router HTTP dedicado que sirve esa
# acción.
#
# `_ACCIONES_IDE` == el conjunto COMPLETO de acciones que
# `ide._require_companion_ide` protege HOY: las SEIS rutas de
# `routers/ide.py` (`GET /tree` -> `list_tree`, `GET /file` -> `read_file`,
# `PUT /file` -> `write_file`, `POST /edit` -> `apply_edit`, `POST /run` ->
# `run_command`, `POST /search` -> `search_files`), no las CUATRO de
# `edecan_companion.actions._IDE_ACTIONS` (`list_tree`/`search_files`/
# `apply_edit`/`screenshot`). Ese `_IDE_ACTIONS` es un gate DISTINTO y más
# angosto — local al companion, vía `ide_enabled` en `~/.edecan/
# companion.yaml` — que NO incluye `read_file`/`write_file`/`run_command`
# (son acciones "v1", anteriores al IDE embebido, así que el companion no las
# trata como "de IDE" localmente) y SÍ incluye `screenshot` (que en el
# servidor exige `companion.remote_view`, no `companion.ide` — ver
# `_ACCION_CAPTURA_PANTALLA` abajo). No reduzcas esta lista a `_IDE_ACTIONS`
# pensando que son el mismo concepto: `read_file`/`write_file`/`run_command`
# SÍ están servidas bajo `/v1/ide/*` en el servidor (el docstring de
# `routers/ide.py` las llama "dos ya existentes en v1"), así que ese router
# SÍ exige `companion.ide` para ellas, aunque el companion no las considere
# acciones de IDE puertas adentro — el flag de plan es una decisión de
# producto sobre el ROUTER/panel que las expone, no sobre la acción interna
# del companion. Bug histórico (medium, plan-flag-bypass): antes de este
# comentario `_ACCIONES_IDE` solo tenía tres de las seis, así que un tenant
# con `companion=True` y `companion.ide=False` podía leer/escribir archivos y
# correr comandos en su companion por chat aunque el panel IDE se lo negara
# con 403 — no explotable con la matriz de planes vigente (`companion.ide` es
# siempre `True` cuando `companion` lo es, ver `edecan_schemas.plans.PLANES`)
# pero sí una inconsistencia real de este mismo dispatch table.
_ACCIONES_IDE = frozenset(
    {"list_tree", "search_files", "apply_edit", "read_file", "write_file", "run_command"}
)
_ACCION_CAPTURA_PANTALLA = "screenshot"
_ACCIONES_INPUT_REMOTO = frozenset({"input_pointer", "input_key"})

_SIN_IDE = "El IDE embebido no está disponible en tu plan."
_SIN_VISTA_REMOTA = "La vista remota no está disponible en tu plan."
_SIN_CONTROL_REMOTO = "El control remoto (teclado/mouse) no está disponible en tu plan."


def _bloqueo_por_plan(accion: str, flags: dict[str, Any]) -> str | None:
    """`None` si `accion` está permitida por `flags` (`ctx.extras["flags"]`,
    los flags de plan del tenant); si no, el mensaje que se le devuelve al
    modelo en vez de reenviar la acción al companion. `flags` ausente o no
    -`dict` se trata como "ningún flag fino activo" — fail-closed, nunca
    fail-open — igual que `tenant.flags.get(..., False)` en los routers
    dedicados."""
    if accion in _ACCIONES_IDE and not flags.get(FLAG_COMPANION_IDE, False):
        return _SIN_IDE
    if accion == _ACCION_CAPTURA_PANTALLA and not flags.get(FLAG_COMPANION_REMOTE_VIEW, False):
        return _SIN_VISTA_REMOTA
    if accion in _ACCIONES_INPUT_REMOTO and not (
        flags.get(FLAG_COMPANION_REMOTE_VIEW, False)
        and flags.get(FLAG_COMPANION_REMOTE_INPUT, False)
    ):
        return _SIN_CONTROL_REMOTO
    return None


class UsarComputadoraTool(Tool):
    name = "usar_computadora"
    description = (
        "Controla la computadora local del usuario a través del companion emparejado: "
        "abrir aplicaciones permitidas, leer/listar/escribir archivos dentro de un sandbox, "
        "leer/escribir el portapapeles, o correr comandos permitidos. Requiere "
        "confirmación porque actúa de verdad sobre el equipo del usuario."
    )
    requires_flags = frozenset({"companion"})
    dangerous = True
    input_schema = {
        "type": "object",
        "properties": {
            "accion": {
                "type": "string",
                "description": (
                    "Acción a ejecutar en el companion. Una de: 'open_app', 'read_dir', "
                    "'read_file', 'write_file', 'clipboard_get', 'clipboard_set', 'run_command'."
                ),
            },
            "parametros": {
                "type": "object",
                "description": "Parámetros específicos de la acción.",
                "default": {},
            },
        },
        "required": ["accion"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        extras = ctx.extras if isinstance(ctx.extras, dict) else {}
        companion = extras.get("companion")
        if companion is None or not callable(companion):
            return ToolResult(content=_MENSAJE_SIN_EMPAREJAR)

        accion = str(args.get("accion", "")).strip()
        if not accion:
            return ToolResult(content="Necesito saber qué acción ejecutar en la computadora.")

        flags = extras.get("flags")
        bloqueo = _bloqueo_por_plan(accion, flags if isinstance(flags, dict) else {})
        if bloqueo is not None:
            return ToolResult(content=bloqueo)

        parametros = args.get("parametros")
        if not isinstance(parametros, dict):
            parametros = {}

        resultado = await companion(accion, parametros)
        ok = isinstance(resultado, dict) and bool(resultado.get("ok"))
        if ok:
            content = f"Ejecuté «{accion}» en tu computadora."
        else:
            error = resultado.get("error") if isinstance(resultado, dict) else None
            detalle = f": {error}" if error else " (el companion no confirmó el éxito)."
            content = f"No pude ejecutar «{accion}» en tu computadora{detalle}"
        return ToolResult(
            content=content,
            data={"accion": accion, "resultado": resultado},
        )
