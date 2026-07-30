"""Aprobación interactiva de acciones del companion (ARCHITECTURE.md §10.7, §10.12).

Por defecto, CADA acción pide confirmación explícita en la terminal antes de
ejecutarse — el companion nunca actúa en silencio. Hay dos formas de
saltarse la pregunta, ambas configuradas en `~/.edecan/companion.yaml` (ver
`config.py`, ambas vacías/apagadas por defecto):

1. `auto_approve`: lista de nombres de acción que NUNCA preguntan.
2. `remember_approvals_minutes` (> 0): tras aprobar una acción a mano, esa
   MISMA acción se auto-aprueba sin volver a preguntar durante N minutos
   (`CompanionConfig.approval_memory`, un `dict[clave, expiry monotónico]`
   que vive en memoria, nunca en disco). Un rechazo NUNCA se recuerda: decir
   que no siempre vuelve a preguntar la próxima vez.

`input_pointer`/`input_key` (control remoto de teclado/mouse, WP-V4-10) usan
una regla MÁS DURA que las dos de arriba — ver `_approve_input_action`: nunca
pasan por `auto_approve` (siempre preguntan al menos una vez), y su "recordado"
está acotado a la sesión de control activa (`params["session_id"]`) además de
a `remote_input_remember_minutes`, nunca se hereda entre sesiones.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
import time
from typing import Any

from edecan_companion.audit import sanitize_params
from edecan_companion.config import CompanionConfig

logger = logging.getLogger(__name__)

APPROVAL_TIMEOUT_SECONDS = 60.0
_YES_ANSWERS = frozenset({"y", "yes", "s", "si", "sí"})

# Duplicado a propósito de `actions._INPUT_ACTIONS` en vez de importarlo: este
# módulo no depende de `actions` (ni `actions` de `este`) -- `main.py` es el
# único lugar que conecta los dos. Mismo criterio que ya separa ambos módulos
# para el resto de acciones (el gate de `ide_enabled` vive solo en
# `actions.execute`, este módulo no necesita saber qué acciones son "de IDE").
_INPUT_ACTIONS = frozenset({"input_pointer", "input_key"})
_ALWAYS_PROMPT_ACTIONS = frozenset({"trash_path"})


async def _prompt_yes_no(prompt: str, *, timeout: float) -> bool:
    """Pide `prompt` por stdin; `False` si se agota `timeout` o hay EOF.

    Lee la respuesta con `input()` dentro de un hilo `daemon=True` propio
    (deliberadamente NO el executor por defecto de asyncio): si nadie
    contesta, ese hilo se queda bloqueado leyendo stdin, pero al ser daemon
    no impide que el proceso cierre limpio con Ctrl+C ni que `asyncio.run`
    termine — un hilo no-daemon bloqueado en `input()` sí lo impediría.
    """
    future: concurrent.futures.Future[str] = concurrent.futures.Future()

    def _read_line() -> None:
        try:
            result = input(prompt)
        except BaseException as exc:  # EOFError u otro error leyendo stdin
            if not future.done():
                future.set_exception(exc)
            return
        if not future.done():
            future.set_result(result)

    threading.Thread(
        target=_read_line, daemon=True, name="edecan-companion-approval-prompt"
    ).start()

    try:
        answer = await asyncio.wait_for(asyncio.wrap_future(future), timeout=timeout)
    except TimeoutError:
        print("\nSin respuesta a tiempo (60s): acción rechazada por defecto.")
        return False
    except EOFError:
        return False

    return answer.strip().lower() in _YES_ANSWERS


def _consume_remembered_approval(key: str, config: CompanionConfig) -> bool:
    """`True` si `key` tiene una aprobación recordada vigente (y la consume/revisa su reloj).

    `key` es el nombre de la acción para el mecanismo general
    (`remember_approvals_minutes`), o una clave compuesta `"acción:session_id"`
    para el de `input_pointer`/`input_key` (`_approve_input_action`) — a esta
    función le da igual, solo compara contra el reloj monotónico. Una entrada
    vencida se limpia aquí mismo (no espera a que otra la pise) para que
    `approval_memory` no acumule expirados indefinidamente.
    """
    expiry = config.approval_memory.get(key)
    if expiry is None:
        return False
    if time.monotonic() >= expiry:
        config.approval_memory.pop(key, None)
        return False
    return True


def _remember_approval(key: str, config: CompanionConfig, *, minutes: int | None = None) -> None:
    """Recuerda `key` por `minutes` (default: `config.remember_approvals_minutes`).

    `_approve_input_action` pasa `minutes=config.remote_input_remember_minutes`
    explícito -- el tope "más duro" de control remoto, independiente del
    general.
    """
    effective_minutes = config.remember_approvals_minutes if minutes is None else minutes
    config.approval_memory[key] = time.monotonic() + effective_minutes * 60


def _input_remember_key(action: str, session_id: str) -> str:
    """Clave de `approval_memory` para `input_pointer`/`input_key`: SIEMPRE
    incluye el `session_id` de la sesión de control remoto -- así una
    aprobación recordada nunca se reutiliza en otra sesión (ni siquiera para
    la misma acción), aunque no hayan pasado los `remote_input_remember_minutes`."""
    return f"{action}:{session_id}"


async def _approve_input_action(
    action: str, params: dict[str, Any], config: CompanionConfig, *, timeout: float
) -> bool:
    """Regla de aprobación "más dura" para `input_pointer`/`input_key`
    (control remoto de teclado/mouse, WP-V4-10 -- docs/control-remoto.md §7).

    A diferencia de `default_approver` para el resto de acciones:

    - **Nunca** consulta `auto_approve`: estas dos acciones SIEMPRE preguntan
      al menos una vez, sin excepción configurable -- son la capacidad de
      mayor impacto de todo el companion.
    - Su "recordado" usa `config.remote_input_remember_minutes` (nunca
      `remember_approvals_minutes`, el general) Y queda acotado a la sesión
      de control activa: la clave de `approval_memory` incluye
      `params["session_id"]` (`_input_remember_key`), así que una sesión de
      control nueva — o simplemente otra acción sin `session_id` — nunca
      hereda una aprobación recordada de una sesión anterior, ni siquiera
      dentro de la ventana de minutos. Sin `session_id` en `params` (un
      llamador que no lo mande) nunca se recuerda nada: cada llamada
      pregunta.
    """
    raw_session_id = params.get("session_id")
    memory_key = _input_remember_key(action, str(raw_session_id)) if raw_session_id else None

    if memory_key is not None and _consume_remembered_approval(memory_key, config):
        logger.info(
            "Acción de control remoto %r reutiliza una aprobación recordada de esta "
            "sesión (session_id=%s, remote_input_remember_minutes=%s).",
            action,
            raw_session_id,
            config.remote_input_remember_minutes,
        )
        return True

    shown_params = sanitize_params(params)
    prompt = f"¿Permitir CONTROL REMOTO «{action}» con {shown_params}? [y/N] "
    approved = await _prompt_yes_no(prompt, timeout=timeout)
    logger.info(
        "Acción de control remoto %r %s por el usuario.",
        action,
        "aprobada" if approved else "rechazada",
    )

    if approved and memory_key is not None and config.remote_input_remember_minutes > 0:
        _remember_approval(memory_key, config, minutes=config.remote_input_remember_minutes)

    return approved


async def default_approver(
    action: str,
    params: dict[str, Any],
    config: CompanionConfig,
    *,
    timeout: float = APPROVAL_TIMEOUT_SECONDS,
) -> bool:
    """Approver por defecto: `auto_approve` > aprobación recordada > pregunta.

    `timeout` es configurable solo para poder probar el camino de "se agotó
    el tiempo" sin esperar los 60s reales; `actions.execute` siempre lo llama
    con el default.

    `input_pointer`/`input_key` se desvían a `_approve_input_action` ANTES de
    llegar a `auto_approve` -- ver su docstring para la regla más dura.
    """
    if action in _INPUT_ACTIONS:
        return await _approve_input_action(action, params, config, timeout=timeout)

    if action in _ALWAYS_PROMPT_ACTIONS:
        shown_params = sanitize_params(params)
        return await _prompt_yes_no(
            f"¿Mover a la PAPELERA «{action}» con {shown_params}? [y/N] ", timeout=timeout
        )

    if action in config.auto_approve:
        logger.info("Acción %r auto-aprobada por configuración (auto_approve).", action)
        return True

    if _consume_remembered_approval(action, config):
        logger.info(
            "Acción %r reutiliza una aprobación recordada (remember_approvals_minutes=%s).",
            action,
            config.remember_approvals_minutes,
        )
        return True

    shown_params = sanitize_params(params)
    prompt = f"¿Permitir «{action}» con {shown_params}? [y/N] "
    approved = await _prompt_yes_no(prompt, timeout=timeout)
    logger.info("Acción %r %s por el usuario.", action, "aprobada" if approved else "rechazada")

    if approved and config.remember_approvals_minutes > 0:
        _remember_approval(action, config)

    return approved
