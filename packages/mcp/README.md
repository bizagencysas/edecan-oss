# packages/mcp — `edecan_mcp`

Cliente **MCP (Model Context Protocol)** bring-your-own: cada tenant conecta SUS PROPIOS
servidores MCP (con SUS PROPIAS credenciales) y, si el plan trae el flag `tools.mcp`, las
tools que ese servidor expone aparecen en el chat/misiones/automatizaciones del tenant como
herramientas más del agente (`ARCHITECTURE.md` §15, `DIRECCION_ACTUAL.md` "Modelo de
credenciales: TODO lo trae el cliente, siempre"). Ver [`docs/mcp.md`](../../docs/mcp.md)
para el flujo completo desde la UI.

Adaptado (async, multi-tenant, español) del cliente MCP de referencia de
[OpenJarvis](https://github.com/open-jarvis/OpenJarvis) (Apache-2.0, single-user/síncrono
en origen) — ver `NOTICE` en la raíz del repo para la atribución completa. Ningún archivo
se copió tal cual: el protocolo JSON-RPC 2.0/los tres transportes (in-process, stdio,
Streamable HTTP) se reescribieron async y multi-tenant.

## Qué NO es este paquete

- No es un servidor MCP (no expone tools de Edecán a un cliente externo) — solo *consume*
  servidores MCP de terceros que el propio tenant configura.
- No decide flags de plan ni persiste nada — eso vive en
  `apps/api/edecan_api/routers/mcp.py` (el router HTTP) y en el `TokenVault` del tenant.
- No importa `edecan_browser` (dominio distinto, `ARCHITECTURE.md` §10.1) — `seguridad.py`
  duplica a propósito el chequeo de host privado de `edecan_browser/edecan_browser/
  policy.py`, con su propio docstring explicando por qué.

## Módulos

- **`protocol.py`** — `MCPRequest`/`MCPResponse`/`MCPError`, JSON-RPC 2.0 puro (sin I/O).
  `id=None` en un `MCPRequest` es una *notificación* (nunca espera respuesta).
- **`transport.py`** — `MCPTransport` (ABC async) + 3 implementaciones:
  - `InProcessTransport` — llama directo a un `server.handle(request)` en memoria (tests).
  - `HTTPTransport` — MCP *Streamable HTTP*: `httpx.AsyncClient` persistente, header
    `Mcp-Session-Id`, acepta respuesta `application/json` o `text/event-stream`.
  - `StdioTransport` — subprocess vía `asyncio.create_subprocess_exec` (JAMÁS
    `shell=True`), JSON delimitado por líneas, drena `stderr` en una tarea de fondo
    normalizada con el mismo criterio que `apps/local/edecan_local/runtime.py::
    _run_background` (`SystemExit`/`KeyboardInterrupt` nunca escapan crudos hacia el
    `asyncio.Task`), y cierre limpio con `SIGTERM` + timeout antes de escalar a matar el
    proceso.
- **`client.py`** — `MCPClient(transport)`: `initialize()` (handshake completo +
  `notifications/initialized`), `list_tools()`, `call_tool(name, args)` (concatena bloques
  `type=text`, trunca a 8000 caracteres). Errores del protocolo/transporte se traducen a
  `MCPClientError` con mensaje legible — nunca un traceback crudo hacia el modelo.
- **`seguridad.py`** — `validar_url_mcp(url, *, local_mode)` (https obligatorio salvo modo
  local; SSRF: bloquea IP literal o resuelta contra rangos privados/loopback/link-local/
  metadata de nube) y `validar_comando_mcp(comando, *, local_mode)` (stdio SOLO en modo
  local). A diferencia de `edecan_smarthome`/`routers/smarthome.py` (SSRF deliberadamente
  INVERTIDA porque Home Assistant vive en la LAN del usuario por diseño), aquí el criterio
  es el de `edecan_browser.policy` (SSRF SIEMPRE bloqueada): un servidor MCP ejecuta tools
  arbitrarias, una superficie de mayor privilegio que leer el estado de una casa.
- **`provider_config.py`** — `serializar_config_mcp`/`deserializar_config_mcp`: la config
  COMPLETA de un servidor (`{nombre, transporte, url, comando, headers}`, secretos incluidos)
  como un único blob para el `TokenVault` (`ARCHITECTURE.md` §15.g, pinned) — `connector_accounts`
  en sí queda como identidad pura (`connector_key`, `external_account_id`, `display_name`), sin
  ninguna columna de config.
- **`tool_adapter.py`** — `construir_tools_mcp(configs, vault_headers_por_slug, *,
  local_mode)`: descubre en vivo las tools de cada servidor (`list_tools()`) y genera una
  subclase dinámica de `edecan_core.Tool` por tool remota, `name=mcp_{slug_servidor}_
  {slug_tool}` (saneado `[a-z0-9_]`, truncado con hash de desambiguación si supera 64
  caracteres), `description` prefijada `"[MCP:{nombre}] "`, `dangerous=True` SIEMPRE
  (decisión v1 conservadora: cada llamada exige confirmación humana en el chat — nunca se
  ejecuta una tool remota de terceros sin que el usuario la vea primero) y
  `requires_flags={"tools.mcp"}`. **v1 sin caché de sesión**: cada `Tool.run()` abre su
  propio transporte → `initialize` → `call_tool` → cierra, por cada llamada — ver el
  docstring de `_MCPRemoteTool.run` para el costo y el `TODO` de cachear una sesión por
  `(tenant, servidor)`.

## Cómo lo usan `apps/api`/`apps/worker`

Ninguno de los dos importa `edecan_mcp` sin red de seguridad: ambos usan
`try/except ImportError` (mismo criterio que `edecan_llm.config.LLMProviderConfig` en
`apps/api/edecan_api/deps.py`) y fallan ABIERTO a "sin tools MCP" ante cualquier error —
un servidor MCP caído, mal configurado, o el propio paquete todavía no instalado, NUNCA
rompe el chat, una misión ni una automatización. Ver `apps/api/edecan_api/deps.py::
get_mcp_tools_for_tenant` (caché débil TTL 60s por tenant) y
`apps/worker/edecan_worker/deps.py::Deps.mcp_tools_para`.

## Tests

```
uv run pytest packages/mcp
```

100% offline: `InProcessTransport`+un servidor JSON-RPC falso para el protocolo/cliente,
`respx` para `HTTPTransport`, y un subprocess real de Python (`sys.executable -c "..."`,
sin red, determinista) para `StdioTransport` — nunca un servidor MCP de terceros de verdad.
