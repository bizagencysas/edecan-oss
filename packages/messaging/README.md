# packages/messaging — `edecan_messaging`

Mensajería oficial del agente: Telegram, Discord y Slack (`ARCHITECTURE.md` §10.7;
`ROADMAP_V2.md` §7.7, WP-V2-05). Dos herramientas del contrato `Tool` de
`edecan_core` (§10.7): `name`, `description`, `input_schema`, `requires_flags`,
`dangerous` y `async run(ctx, args)`.

`get_all_tools() -> list[Tool]` (en `edecan_messaging/__init__.py`) es el
entry point que consume `edecan_core.ToolRegistry.load_entry_points(group="edecan.tools")`,
declarado en `pyproject.toml` como `[project.entry-points."edecan.tools"]`.

## Las 2 herramientas

| Tool | dangerous | Notas |
|---|---|---|
| `enviar_mensaje` | sí | `{plataforma: telegram\|discord\|slack, destino, texto}` — registra `usage_events(kind="messages")` + `audit_log` tras un envío exitoso |
| `leer_mensajes` | no | `{plataforma, origen, limite<=20}`; `origen` es opcional solo para Telegram (usa `getUpdates`) |

Ambas requieren el flag de plan `connectors.messaging` (`requires_flags`).
Ese flag y su literal (`"connectors.messaging"`) están pinned en
`ROADMAP_V2.md` §7.2; la constante `edecan_schemas.plans.FLAG_CONNECTORS_MESSAGING`
la agrega WP-V2-01 — mientras tanto, `tools.py` usa el string directamente
(ver su docstring).

## Cómo se conecta cada plataforma (no vive en este paquete)

Este paquete NUNCA orquesta la conexión de una cuenta — solo consume
credenciales ya guardadas. Quién las guarda:

- **Telegram / Discord** (no son OAuth: token de bot del tenant) —
  `PUT /v1/connectors/{key}/credentials` con `{bot_token}`
  (`apps/api/edecan_api/routers/connectors.py`, `key` ∈ {`telegram`, `discord`}).
- **Slack** (sí es OAuth) — `edecan_connectors.messaging.slack.SlackConnector`
  (`packages/connectors/`) + el flujo genérico `GET /v1/connectors/slack/authorize`
  → `GET /v1/connectors/slack/callback`.

Ver `docs/mensajeria.md` para la guía completa (BotFather, Discord Developer
Portal, app de Slack del tenant, scopes mínimos, exclusiones).

## Decisiones de implementación

- **Acceso a datos**: `_creds.py` consulta `connector_accounts` con
  `sqlalchemy.text()` sobre `ctx.session` (mismo estilo que
  `edecan_premium.telephony.for_tenant` y `edecan_toolkit._conectores
  .buscar_cuenta_conectada`) y pide el `TokenBundle` a `ctx.vault.get(...)`.
  No importa `edecan_db.models`: los nombres de tabla/columna sí están
  pinned en `ARCHITECTURE.md` §10.3, la forma interna (ORM vs. SQL Core) no.
- **`clients.py` es httpx puro**: sin dependencia de `edecan_connectors` —
  Telegram/Discord no tienen conector OAuth (no son OAuth) y Slack solo lo
  necesita para el intercambio inicial de código→token (eso vive en
  `edecan_connectors.messaging.slack`, no aquí). Ningún cliente loguea el
  token: los mensajes de error pasan por `edecan_core.safety.redact`.
- **`enviar_mensaje` es `dangerous=True`**: pasa por el mismo gate de
  confirmación humana de `Agent.run_turn` (`ARCHITECTURE.md` §10.7) que
  `enviar_correo`/`enviar_sms`/`publicar_social` — un mensaje real y visible
  para terceros nunca sale sin que el usuario lo confirme.
- **`leer_mensajes` no es `dangerous`**: es de solo lectura, igual que
  `buscar_correo`.

## Tests

```bash
uv run pytest packages/messaging
```

Offline y deterministas (`respx` para las llamadas HTTP de `clients.py`,
fakes locales por duck typing para `ctx.session`/`ctx.vault` — mismo patrón
que `packages/toolkit/tests/conftest.py`, ver `ARCHITECTURE.md` §10.1).
`tests/conftest.py` agrega este paquete a `sys.path` a mano (mismo motivo y
técnica que `apps/api/tests/_stub_siblings.py`): todavía no está registrado
en `[tool.uv.workspace].members` del `pyproject.toml` raíz (lo hace
WP-V2-01), así que sin ese shim `import edecan_messaging` fallaría al correr
la suite de este paquete de forma aislada.
