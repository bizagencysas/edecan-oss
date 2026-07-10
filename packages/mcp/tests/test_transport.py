"""`edecan_mcp.transport` — los 3 transportes MCP, 100% offline.

`InProcessTransport` contra `fake_server` (`conftest.py`); `HTTPTransport`
con `respx` (nunca red real); `StdioTransport` contra un subprocess REAL de
`sys.executable -c "..."` (offline: no abre ningún socket, solo stdin/
stdout/stderr locales) que implementa un servidor MCP mínimo — la forma más
fiel de probar de verdad `asyncio.create_subprocess_exec`/el framing por
líneas sin depender de un servidor MCP de terceros.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx
import pytest
import respx
from edecan_mcp.protocol import MCPRequest
from edecan_mcp.transport import (
    HTTPTransport,
    InProcessTransport,
    MCPTransportError,
    StdioTransport,
)

# ---------------------------------------------------------------------------
# InProcessTransport
# ---------------------------------------------------------------------------


async def test_inprocess_transport_send_devuelve_lo_que_arma_el_servidor(fake_server) -> None:
    transport = InProcessTransport(fake_server)
    resp = await transport.send(MCPRequest(method="tools/list", params={}, id=1))
    assert resp.result["tools"][0]["name"] == "sumar"
    await transport.close()


async def test_inprocess_transport_send_notification_delega_en_send(fake_server) -> None:
    transport = InProcessTransport(fake_server)
    notificacion = MCPRequest(method="notifications/initialized", params={}, id=None)
    await transport.send_notification(notificacion)
    assert fake_server.recibidos[-1].method == "notifications/initialized"
    await transport.close()


# ---------------------------------------------------------------------------
# HTTPTransport (respx — cero red real)
# ---------------------------------------------------------------------------

_URL = "https://mcp.ejemplo.com/rpc"


@respx.mock
async def test_http_transport_send_json_response() -> None:
    ruta = respx.post(_URL).mock(
        return_value=httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
    )
    transport = HTTPTransport(_URL)
    try:
        resp = await transport.send(MCPRequest(method="initialize", params={}, id=1))
    finally:
        await transport.close()
    assert resp.result == {"ok": True}
    assert ruta.called


@respx.mock
async def test_http_transport_manda_headers_del_tenant() -> None:
    ruta = respx.post(_URL).mock(
        return_value=httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})
    )
    transport = HTTPTransport(_URL, headers={"Authorization": "Bearer xyz-del-tenant"})
    try:
        await transport.send(MCPRequest(method="initialize", params={}, id=1))
    finally:
        await transport.close()
    assert ruta.calls.last.request.headers["Authorization"] == "Bearer xyz-del-tenant"


@respx.mock
async def test_http_transport_rastrea_mcp_session_id_entre_llamadas() -> None:
    respx.post(_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                headers={"Mcp-Session-Id": "sesion-abc"},
                json={"jsonrpc": "2.0", "id": 1, "result": {}},
            ),
            httpx.Response(200, json={"jsonrpc": "2.0", "id": 2, "result": {}}),
        ]
    )
    transport = HTTPTransport(_URL)
    try:
        await transport.send(MCPRequest(method="initialize", params={}, id=1))
        await transport.send(MCPRequest(method="tools/list", params={}, id=2))
    finally:
        await transport.close()
    segunda_llamada = respx.calls[1].request
    assert segunda_llamada.headers["Mcp-Session-Id"] == "sesion-abc"


@respx.mock
async def test_http_transport_acepta_respuesta_event_stream() -> None:
    cuerpo = 'event: message\ndata: {"jsonrpc": "2.0", "id": 1, "result": {"ok": true}}\n\n'
    respx.post(_URL).mock(
        return_value=httpx.Response(200, headers={"content-type": "text/event-stream"}, text=cuerpo)
    )
    transport = HTTPTransport(_URL)
    try:
        resp = await transport.send(MCPRequest(method="initialize", params={}, id=1))
    finally:
        await transport.close()
    assert resp.result == {"ok": True}


@respx.mock
async def test_http_transport_event_stream_sin_datos_lanza_error() -> None:
    respx.post(_URL).mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/event-stream"}, text="event: ping\n\n"
        )
    )
    transport = HTTPTransport(_URL)
    try:
        with pytest.raises(MCPTransportError):
            await transport.send(MCPRequest(method="initialize", params={}, id=1))
    finally:
        await transport.close()


@respx.mock
async def test_http_transport_timeout_da_mcptransporterror() -> None:
    respx.post(_URL).mock(side_effect=httpx.ConnectTimeout("timeout simulado"))
    transport = HTTPTransport(_URL)
    try:
        with pytest.raises(MCPTransportError):
            await transport.send(MCPRequest(method="initialize", params={}, id=1))
    finally:
        await transport.close()


@respx.mock
async def test_http_transport_status_error_da_mcptransporterror() -> None:
    respx.post(_URL).mock(return_value=httpx.Response(500, text="boom"))
    transport = HTTPTransport(_URL)
    try:
        with pytest.raises(MCPTransportError):
            await transport.send(MCPRequest(method="initialize", params={}, id=1))
    finally:
        await transport.close()


@respx.mock
async def test_http_transport_notification_acepta_202_vacio() -> None:
    respx.post(_URL).mock(return_value=httpx.Response(202))
    transport = HTTPTransport(_URL)
    try:
        # No debe lanzar aunque el cuerpo esté vacío (no es JSON-RPC).
        notificacion = MCPRequest(method="notifications/initialized", params={}, id=None)
        await transport.send_notification(notificacion)
    finally:
        await transport.close()


# ---------------------------------------------------------------------------
# Redirects — WP-V7-05, BARRIDO A.3: `validar_url_mcp` (`seguridad.py`) solo
# valida la URL CONFIGURADA del servidor; si `HTTPTransport` siguiera un 3xx
# en automático, un servidor MCP remoto podría redirigir la request real a un
# host distinto sin volver a pasar por el guardrail SSRF (mismo vector que
# `HOTFIXES_PENDIENTES.md` punto 7, "el fetcher Playwright no revalida
# redirects", pero para el transporte MCP). Ver `edecan_mcp.seguridad`
# (docstring, sección "Redirects HTTP") para el razonamiento completo: httpx
# nunca sigue redirects sin `follow_redirects=True` explícito (que este
# transporte JAMÁS pasa), y `raise_for_status()` trata cualquier 3xx como
# error — este test es la prueba ejecutable de esa garantía.
# ---------------------------------------------------------------------------


@respx.mock
async def test_http_transport_no_sigue_redirects_automaticamente() -> None:
    otro_host = "https://otro-host.ejemplo.com/rpc"
    ruta_original = respx.post(_URL).mock(
        return_value=httpx.Response(307, headers={"location": otro_host})
    )
    ruta_otro_host = respx.post(otro_host).mock(
        return_value=httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
    )
    transport = HTTPTransport(_URL)
    try:
        with pytest.raises(MCPTransportError):
            await transport.send(MCPRequest(method="initialize", params={}, id=1))
    finally:
        await transport.close()
    assert ruta_original.called
    assert not ruta_otro_host.called  # el redirect NUNCA se siguió


# ---------------------------------------------------------------------------
# StdioTransport — subprocess real de sys.executable, offline (sin sockets)
# ---------------------------------------------------------------------------

_STDIO_FAKE_SERVER_SCRIPT = r"""
import json, sys

def responder(req):
    metodo = req.get("method")
    rid = req.get("id")
    if metodo == "initialize":
        result = {"protocolVersion": "2025-03-26", "capabilities": {}}
        return {"jsonrpc": "2.0", "id": rid, "result": result}
    if metodo == "tools/list":
        herramienta = {
            "name": "echo",
            "description": "Repite lo que le mandes.",
            "inputSchema": {"type": "object"},
        }
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": [herramienta]}}
    if metodo == "tools/call":
        args = req.get("params", {}).get("arguments", {})
        contenido = [{"type": "text", "text": json.dumps(args)}]
        result = {"content": contenido, "isError": False}
        return {"jsonrpc": "2.0", "id": rid, "result": result}
    error = {"code": -32601, "message": "método desconocido"}
    return {"jsonrpc": "2.0", "id": rid, "error": error}

for linea in sys.stdin:
    linea = linea.strip()
    if not linea:
        continue
    req = json.loads(linea)
    if req.get("id") is None:
        continue  # notificación: sin respuesta
    resp = responder(req)
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()
"""

_STDIO_IGNORA_SIGTERM_SCRIPT = (
    "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"
)


async def test_stdio_transport_handshake_completo_y_call_tool() -> None:
    transport = StdioTransport([sys.executable, "-c", _STDIO_FAKE_SERVER_SCRIPT])
    try:
        init = await transport.send(MCPRequest(method="initialize", params={}, id=1))
        assert init.result["protocolVersion"] == "2025-03-26"

        listado = await transport.send(MCPRequest(method="tools/list", params={}, id=2))
        assert listado.result["tools"][0]["name"] == "echo"

        llamada = await transport.send(
            MCPRequest(
                method="tools/call",
                params={"name": "echo", "arguments": {"a": 1}},
                id=3,
            )
        )
        assert json.loads(llamada.result["content"][0]["text"]) == {"a": 1}
    finally:
        await transport.close()


_STDIO_ENV_DUMP_SCRIPT = r"""
import json, os, sys

def responder(req):
    rid = req.get("id")
    if req.get("method") == "dump_env":
        return {"jsonrpc": "2.0", "id": rid, "result": {"env": dict(os.environ)}}
    return {"jsonrpc": "2.0", "id": rid, "result": {}}

for linea in sys.stdin:
    linea = linea.strip()
    if not linea:
        continue
    req = json.loads(linea)
    if req.get("id") is None:
        continue
    resp = responder(req)
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()
"""


async def test_stdio_transport_el_subproceso_solo_hereda_path_y_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WP-V7-05, BARRIDO A.2: `_STDIO_ENV_ALLOWLIST` (ver docstring del
    módulo) debe ser el ÚNICO filtro entre `os.environ` del proceso backend y
    el subprocess del servidor MCP del tenant — un servidor MCP `stdio` que
    heredara el ambiente completo vería credenciales de PLATAFORMA
    (`ANTHROPIC_API_KEY`, `AWS_ACCESS_KEY_ID`, `DATABASE_URL`, ...), la misma
    clase de fuga que `packages/voice/edecan_voice/polly.py` tenía con las
    credenciales AWS ambiente (`HOTFIXES_PENDIENTES.md`, v5). Este test
    INSPECCIONA el env real que llega al subprocess (no solo lee el código):
    el propio subprocess de prueba responde con su `os.environ` tal cual lo
    recibió."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secreta-de-plataforma-no-debe-filtrarse")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA-secreta-de-plataforma-no-debe-filtrarse")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secreta-de-plataforma-no-debe-filtrarse")
    monkeypatch.setenv("DATABASE_URL", "postgresql://usuario:secreta@host-de-plataforma/db")
    monkeypatch.setenv("JWT_SECRET", "secreta-de-plataforma-no-debe-filtrarse")
    monkeypatch.setenv("UNA_VARIABLE_CUALQUIERA_DEL_BACKEND", "tampoco-debe-filtrarse")

    transport = StdioTransport([sys.executable, "-c", _STDIO_ENV_DUMP_SCRIPT])
    try:
        respuesta = await transport.send(MCPRequest(method="dump_env", params={}, id=1))
    finally:
        await transport.close()

    env_recibido_por_el_subproceso = respuesta.result["env"]

    # Ninguna credencial/config de plataforma cruzó al servidor MCP del tenant.
    assert "ANTHROPIC_API_KEY" not in env_recibido_por_el_subproceso
    assert "AWS_ACCESS_KEY_ID" not in env_recibido_por_el_subproceso
    assert "AWS_SECRET_ACCESS_KEY" not in env_recibido_por_el_subproceso
    assert "DATABASE_URL" not in env_recibido_por_el_subproceso
    assert "JWT_SECRET" not in env_recibido_por_el_subproceso
    assert "UNA_VARIABLE_CUALQUIERA_DEL_BACKEND" not in env_recibido_por_el_subproceso

    # Más allá de PATH/HOME (el allowlist explícito de `_STDIO_ENV_ALLOWLIST`),
    # lo único que puede aparecer son un par de variables NO secretas que el
    # propio runtime del sistema operativo/CPython sintetiza en el proceso
    # HIJO — verificado empíricamente (ver `barrido-v7-mcp.md`) que aparecen
    # incluso pasando `env={}` totalmente vacío a `create_subprocess_exec`
    # (nunca leídas de `os.environ` del padre, así que NO son un canal de
    # fuga): `__CF_USER_TEXT_ENCODING` (Apple CoreFoundation, deriva del UID
    # del proceso, no de ninguna variable) y `LC_CTYPE` (coerción de locale
    # de CPython, PEP 538). Ninguna de las dos puede contener un secreto — el
    # chequeo fuerte de arriba (ninguna de las 6 variables sensibles cruzó)
    # es la garantía de seguridad real; esto solo evita que el test sea
    # frágil ante metadata de entorno inofensiva y específica del SO.
    variables_no_secretas_del_runtime = {"LC_CTYPE", "__CF_USER_TEXT_ENCODING"}
    extra = set(env_recibido_por_el_subproceso) - {"PATH", "HOME"}
    assert extra <= variables_no_secretas_del_runtime, (
        f"variable(s) inesperada(s) en el env del subproceso MCP: {extra}"
    )
    if "PATH" in os.environ:
        assert env_recibido_por_el_subproceso.get("PATH") == os.environ["PATH"]
    if "HOME" in os.environ:
        assert env_recibido_por_el_subproceso.get("HOME") == os.environ["HOME"]


async def test_stdio_transport_command_vacio_lanza_valueerror() -> None:
    with pytest.raises(ValueError):
        StdioTransport([])
    with pytest.raises(ValueError):
        StdioTransport(["   "])


async def test_stdio_transport_binario_inexistente_da_mcptransporterror() -> None:
    transport = StdioTransport(["/no/existe/un-binario-falso-edecan-mcp-xyz"])
    with pytest.raises(MCPTransportError):
        await transport.send(MCPRequest(method="initialize", params={}, id=1))
    await transport.close()


async def test_stdio_transport_close_escala_a_kill_si_ignora_sigterm() -> None:
    transport = StdioTransport(
        [sys.executable, "-c", _STDIO_IGNORA_SIGTERM_SCRIPT], close_timeout_seconds=0.3
    )
    # Solo escribe (arranca el proceso) — no espera respuesta, el script no lee stdin.
    await transport.send_notification(MCPRequest(method="noop", params={}, id=None))
    # Cede el control una vez al loop para que la tarea de drenado de stderr
    # alcance a arrancar antes de cancelarla en `close()` — si no, puede
    # cancelarse sin haber corrido nunca (0 iteraciones) y emitir un
    # `RuntimeWarning: coroutine ... was never awaited` al recolectarla
    # (cosmético, no afecta la corrección de `close()`, pero ensucia el
    # output de la suite).
    await asyncio.sleep(0)
    await transport.close()  # no debe colgarse: escala a kill() tras el timeout


async def test_stdio_transport_send_sin_respuesta_da_timeout() -> None:
    # Un script que arranca y nunca escribe nada a stdout.
    transport = StdioTransport(
        [sys.executable, "-c", "import time; time.sleep(30)"], timeout_seconds=0.3
    )
    try:
        with pytest.raises(MCPTransportError):
            await transport.send(MCPRequest(method="initialize", params={}, id=1))
    finally:
        await transport.close()
