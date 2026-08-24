"""CLI del companion: `python -m edecan_companion --server ... --code ...`.

Se conecta por WebSocket a `{server}/v1/companion/ws?code={code}` (ver
ARCHITECTURE.md §10.12), recibe comandos `{"request_id", "action", "params"}`
del asistente, los pasa por `actions.execute` (que exige aprobación salvo que
la acción esté en `auto_approve`) y responde `{"request_id", "ok", "result"}`
o `{"request_id", "ok": false, "error"}`.

Si se cae la conexión, reconecta solo con backoff exponencial (hasta 60s);
Ctrl+C cierra el proceso de forma limpia en cualquier momento.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import websockets
from websockets.exceptions import WebSocketException

from edecan_companion import actions
from edecan_companion.approval import default_approver
from edecan_companion.config import CompanionConfig, load_config
from edecan_companion.ide_runtime import IDE_ACTIONS, execute_ide_action

logger = logging.getLogger("edecan_companion")

WS_PATH = "/v1/companion/ws"
INITIAL_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 60.0
# El default de `websockets` para mensajes ENTRANTES es 1 MiB, muy por debajo
# de un `transfer_push` (archivo en base64: hasta ~13.3 MiB para el tope de
# 10 MiB de `actions.MAX_TRANSFER_BYTES`). Sin subirlo, la librería cerraría la
# conexión con `PayloadTooBig` y tumbaría TODA la sesión del companion (no solo
# esa transferencia). 16 MiB deja margen sobre ese peor caso.
WS_MAX_MESSAGE_BYTES = 16 * 1024 * 1024
_SCHEME_MAP = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}


def _build_ws_url(server: str, code: str) -> str:
    """Convierte la URL http(s) del API en `ws(s)://.../v1/companion/ws?code=...`."""
    parsed = urlsplit(server)
    scheme = _SCHEME_MAP.get(parsed.scheme.lower())
    if not parsed.netloc or scheme is None:
        raise ValueError(f"--server inválido: {server!r} (usa algo como http://localhost:8000)")
    query = urlencode({"code": code})
    return urlunsplit((scheme, parsed.netloc, WS_PATH, query, ""))


async def _handle_message(
    ws: Any,
    raw_message: str | bytes,
    config: CompanionConfig,
    approver: actions.Approver,
) -> None:
    request_id: Any = None
    try:
        envelope = json.loads(raw_message)
        if not isinstance(envelope, dict):
            raise ValueError("el mensaje no es un objeto JSON")
        request_id = envelope.get("request_id")
        action = envelope.get("action")
        params = envelope.get("params")
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Mensaje del servidor ilegible, se ignora: %s", exc)
        return

    if not isinstance(action, str) or not action:
        response: dict[str, Any] = {
            "request_id": request_id,
            "ok": False,
            "error": "mensaje sin 'action' válida",
        }
    else:
        logger.info("Comando recibido: %s (request_id=%s)", action, request_id)
        if action in IDE_ACTIONS:
            result = await execute_ide_action(action, params, config, approver)
        else:
            result = await actions.execute(action, params, config, approver)
        response = {"request_id": request_id, **result}

    await ws.send(json.dumps(response, ensure_ascii=False))


async def _run_session(uri: str, config: CompanionConfig, approver: actions.Approver) -> None:
    logger.info("Conectando a %s ...", uri)
    async with websockets.connect(
        uri,
        open_timeout=15,
        ping_interval=20,
        ping_timeout=20,
        max_size=WS_MAX_MESSAGE_BYTES,
    ) as ws:
        print("Conectado y emparejado. Esperando comandos del asistente (Ctrl+C para salir)...")
        logger.info("Conexión establecida.")
        async for raw_message in ws:
            await _handle_message(ws, raw_message, config, approver)


async def run_forever(
    server: str,
    code: str,
    config: CompanionConfig,
    approver: actions.Approver = default_approver,
) -> None:
    """Mantiene la sesión viva: reconecta con backoff exponencial (máx 60s) ante cualquier corte."""
    uri = _build_ws_url(server, code)
    backoff = INITIAL_BACKOFF_SECONDS

    while True:
        try:
            await _run_session(uri, config, approver)
            logger.info("El servidor cerró la conexión.")
            backoff = INITIAL_BACKOFF_SECONDS
        except (WebSocketException, OSError) as exc:
            logger.warning("Conexión perdida (%s: %s).", type(exc).__name__, exc)

        logger.info("Reintentando en %.0fs...", backoff)
        await asyncio.sleep(backoff)
        backoff = min(MAX_BACKOFF_SECONDS, backoff * 2)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m edecan_companion",
        description=(
            "Companion local de Edecán: dale al asistente acceso controlado y "
            "auditable a este equipo. Opt-in: siempre pide tu aprobación."
        ),
    )
    parser.add_argument(
        "--server", required=True, help="URL del API de Edecán, ej. http://localhost:8000"
    )
    parser.add_argument(
        "--code",
        required=True,
        help="Código de emparejamiento (Ajustes → Companion en la web del asistente)",
    )
    parser.add_argument("--log-level", default="INFO", help="Nivel de logging (default: INFO)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    config = load_config()
    print(f"Edecán Companion — sandbox: {config.sandbox_dir}")
    print(f"Configuración: {config.config_path}")
    if not config.allowed_apps and not config.allowed_commands:
        print(
            "Nota: allowed_apps y allowed_commands están vacíos — toda acción "
            "pedirá tu aprobación explícita en esta terminal."
        )
    if config.remote_input_enabled:
        print(
            "Control remoto de teclado/mouse ACTIVADO (remote_input_enabled: true). "
            "Cada acción sigue pidiendo tu aprobación local (máx. "
            f"{config.remote_input_remember_minutes} min recordada, nunca entre sesiones)."
        )

    try:
        asyncio.run(run_forever(args.server, args.code, config))
    except KeyboardInterrupt:
        print("\nCerrando el companion. Hasta luego.")
        return 0
    return 0
