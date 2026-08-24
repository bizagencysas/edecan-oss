"""Companion — control de la computadora local del usuario (`ARCHITECTURE.md`
§10.7, §10.12).

`ctx.extras["companion"]` es la clave reservada por el contrato: `None`, o un
callable `async (action: str, params: dict) -> dict` inyectado por la API
cuando hay un companion emparejado (`POST /v1/companion/pair-code` +
`WS /v1/companion/ws?code=`).

Guardrail de interacción — por qué esta tool NO tiene un `check_navigation`
propio: a diferencia de `edecan_browser`
(`edecan_browser/policy.py::check_navigation`, que bloquea scraping no
autorizado, checkout y SSRF antes de cualquier fetch real), esta tool
nunca recibe una URL — `accion`/`parametros` son coordenadas de pantalla,
texto a escribir o comandos de bajo nivel (`apps/companion/edecan_companion/
actions.py`: `input_pointer`, `input_key`, `screenshot`, ...), así que no hay
ningún dominio que un guardrail de código pueda inspeccionar aquí. Por eso
cada uso pasa por la advertencia específica que ve quien aprueba en
`apps/web/src/components/chat/ConfirmationCard.tsx`. Edecán puede continuar
una tarea puntual en una sesión local ya autorizada —incluida una publicación
aprobada—, pero no scraping, captura de credenciales, contacto masivo ni
acciones ocultas. Esa política se suma a las capas reales de esta tool:
`dangerous = True`, confirmación humana en el chat, `remote_input_enabled`
apagado por defecto, aprobación local por acción y el permiso de Accesibilidad
que solo una persona puede conceder.
"""

from __future__ import annotations

import base64
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
    {
        "list_tree", "search_files", "apply_edit", "read_file", "write_file",
        "trash_path", "run_command",
    }
)
_ACCION_CAPTURA_PANTALLA = "screenshot"
# Foto para el MODELO, no para el visor remoto del teléfono. PNG Retina crudo
# (~10 MB) lo ignora Workers AI y Scout inventa un escritorio genérico.
# WebP 2560 a calidad 90 cabe en cientos de KB y deja leer el chat de Cursor.
# `crop_frontmost` manda además el recorte de la ventana al frente (OCR).
# Si el sidecar no puede grabar WebP, el companion cae a JPEG solo.
PARAMS_CAPTURA_PARA_EL_MODELO: dict[str, Any] = {
    "format": "webp",
    "quality": 90,
    "max_width": 2560,
    "crop_frontmost": True,
}
_ACCIONES_INPUT_REMOTO = frozenset({"input_pointer", "input_key"})
_ACCIONES_QUE_CAMBIAN_PANTALLA = frozenset(
    {"input_pointer", "input_key", "open_app", "open_url"}
)

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
        "Controla ESTA Mac del dueño: abrir apps, capturar pantalla (la foto llega "
        "al iPhone), hacer clic, escribir y pulsar Return para enviar. "
        "Una acción por llamada. Después de clic, tecla o app te llega la captura: "
        "MÍRALA antes del siguiente movimiento. Si piden ver la pantalla o acabas "
        "de hacer un cambio, llama screenshot."
    )
    category = "code"
    risk_level = "high"
    latency_class = "slow"
    requires_flags = frozenset({"companion"})
    dangerous = True
    input_schema = {
        "type": "object",
        "properties": {
            "accion": {
                "type": "string",
                "description": (
                    "Una de: 'open_app', 'open_url', 'screenshot', 'input_pointer', "
                    "'input_key', 'read_dir', 'read_file', 'write_file', 'trash_path', "
                    "'clipboard_get', 'clipboard_set', 'run_command', 'list_tree', "
                    "'search_files', 'apply_edit'."
                ),
            },
            "parametros": {
                "type": "object",
                "description": (
                    "open_app: {app: 'Messages'|'Safari'|'Mail'|...}. "
                    "screenshot: {}. La foto llega al iPhone; el dueño la toca para ampliar. "
                    "input_pointer: {accion: 'click', nx: 0.0-1.0, ny: 0.0-1.0}. "
                    "nx/ny son la fracción DENTRO de la captura completa (0=izq/arriba, 1=der/abajo), "
                    "nunca del recorte de la ventana. "
                    "NO uses píxeles Retina. También admite x/y enteros de respaldo. "
                    "input_key: {texto: 'el mensaje'} para escribir, o {tecla: 'enter'} "
                    "para Return/enviar. Nunca juntas texto y tecla. "
                    "Para 've a X, toca el campo, escribe Y y mándalo': "
                    "open_app → screenshot → click nx/ny → texto → tecla enter → screenshot."
                ),
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
        parametros = _params_de_captura(accion, parametros)

        resultado = await companion(accion, parametros)
        ok = isinstance(resultado, dict) and bool(resultado.get("ok"))
        if ok and accion in _ACCIONES_QUE_CAMBIAN_PANTALLA:
            resultado = await _adjuntar_captura(companion, resultado)
        if ok:
            content = (
                f"Ejecuté «{accion}» en tu computadora."
                if not _imagen_de_resultado(resultado)
                else (
                    f"Ejecuté «{accion}» en tu computadora. "
                    "La captura de pantalla va adjunta: mírala antes del siguiente clic."
                )
            )
        else:
            error = resultado.get("error") if isinstance(resultado, dict) else None
            detalle = f": {error}" if error else " (el companion no confirmó el éxito)."
            content = f"No pude ejecutar «{accion}» en tu computadora{detalle}"
        data: dict[str, Any] = {"accion": accion, "resultado": resultado}
        if ok and accion == _ACCION_CAPTURA_PANTALLA:
            artefactos = await _artifact_de_captura(ctx, resultado)
            if artefactos:
                data["artifacts"] = artefactos
                data["file_id"] = artefactos[0]["file_id"]
                data["filename"] = artefactos[0]["filename"]
                data["mime"] = artefactos[0]["mime"]
                content = (
                    "Capturé la pantalla de tu Mac. La miniatura está en el chat: "
                    "tócala para abrirla y hacer zoom."
                )
        return ToolResult(content=content, data=data)


def _params_de_captura(accion: str, parametros: dict[str, Any]) -> dict[str, Any]:
    if accion != _ACCION_CAPTURA_PANTALLA:
        return parametros
    fusion = dict(parametros)
    fusion.update(PARAMS_CAPTURA_PARA_EL_MODELO)
    return fusion


def _imagen_de_resultado(resultado: Any) -> dict[str, str] | None:
    if not isinstance(resultado, dict):
        return None
    b64 = resultado.get("image_b64")
    if not isinstance(b64, str) or not b64.strip():
        return None
    mime = str(resultado.get("mime") or "image/jpeg").split(";", 1)[0].strip().lower()
    if mime not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
        mime = "image/jpeg"
    return {"mime": mime, "data": b64}


_MIME_A_EXT = {
    "image/webp": "webp",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
}


async def _artifact_de_captura(ctx: ToolContext, resultado: Any) -> list[dict[str, str]]:
    """Guarda la captura para el iPhone. Scout ya la ve en base64; el chat no."""
    imagen = _imagen_de_resultado(resultado)
    if imagen is None:
        return []
    extras = ctx.extras if isinstance(ctx.extras, dict) else {}
    uploader = extras.get("subir_archivo")
    if uploader is None:
        try:
            from edecan_creative import subir_archivo as uploader
        except ImportError:
            return []
    try:
        crudo = base64.b64decode(imagen["data"], validate=True)
    except (ValueError, base64.binascii.Error):
        return []
    if not crudo:
        return []
    ext = _MIME_A_EXT.get(imagen["mime"], "webp")
    try:
        file_id, filename = await uploader(
            ctx, data=crudo, filename=f"mac-pantalla.{ext}", mime=imagen["mime"]
        )
    except Exception:  # noqa: BLE001 - la visión del modelo no depende de esto
        return []
    return [
        {
            "file_id": str(file_id),
            "filename": str(filename),
            "mime": imagen["mime"],
            "alt": "Pantalla de tu Mac",
            "caption": "Toca para ampliar",
        }
    ]


async def _adjuntar_captura(companion: Any, resultado: dict[str, Any]) -> dict[str, Any]:
    """Tras un clic o tecla, saca foto. Sin esto el modelo trabaja a ciegas."""
    if _imagen_de_resultado(resultado) is not None:
        return resultado
    try:
        captura = await companion(_ACCION_CAPTURA_PANTALLA, dict(PARAMS_CAPTURA_PARA_EL_MODELO))
    except Exception:  # noqa: BLE001 - la acción ya salió; la foto es extra
        return resultado
    imagen = _imagen_de_resultado(captura)
    if imagen is None:
        return resultado
    enriquecido = dict(resultado)
    enriquecido["image_b64"] = imagen["data"]
    enriquecido["mime"] = imagen["mime"]
    ventanas = captura.get("ventanas") if isinstance(captura, dict) else None
    if isinstance(ventanas, list) and ventanas:
        enriquecido["ventanas"] = ventanas
    return enriquecido


def imagen_para_el_modelo(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Bloque multimodal que el agente le pasa a Scout/Silva/Oda."""
    if not isinstance(data, dict):
        return None
    crudo = data.get("resultado")
    imagen = _imagen_de_resultado(crudo)
    if imagen is None:
        return None
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": imagen["mime"], "data": imagen["data"]},
    }
