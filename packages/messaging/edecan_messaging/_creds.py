"""Resuelve las credenciales de mensajería del tenant desde su `TokenVault`
(`ARCHITECTURE.md` §10.4).

Calca el patrón de `edecan_premium.telephony.for_tenant` (Twilio) — busca la
fila más reciente de `connector_accounts` para el `connector_key` pedido y le
pide el `TokenBundle` correspondiente a `ctx.vault` — usando el estilo de
consulta (`.mappings().first()` sobre filas-dict) de
`edecan_toolkit._conectores.buscar_cuenta_conectada`, el helper equivalente
para los conectores OAuth del toolkit.

Los cuatro `connector_key` posibles son `"telegram"`, `"discord"`, `"slack"`
y `"whatsapp"`:

- `"telegram"` / `"discord"`: NO son OAuth (ver `edecan_connectors.messaging`,
  docstring, y `docs/mensajeria.md`). `TokenBundle.access_token` guarda
  directamente el token del bot que el tenant pegó en
  `PUT /v1/connectors/{key}/credentials` (`edecan_api.routers.connectors`).
- `"slack"`: SÍ es OAuth (`edecan_connectors.messaging.slack.SlackConnector`);
  `TokenBundle.access_token` es el token de bot (`xoxb-...`) que devolvió el
  intercambio OAuth.
- `"whatsapp"` (WP-V3-13, `ARCHITECTURE.md` §12.b): NO es OAuth. El tenant
  trae el access token PERMANENTE de su propia app de Meta (vía system user)
  más el `phone_number_id` de su número de WhatsApp Business ya verificado,
  pegados en `PUT /v1/connectors/whatsapp/credentials` (ruta fija, dos
  campos — mismo patrón no-OAuth que Twilio, ver
  `edecan_api.routers.connectors`). `TokenBundle.access_token` guarda el
  access token tal cual; `TokenBundle.scopes` guarda `[phone_number_id]`
  (mismo patrón que Twilio, que guarda `[ACCOUNT_SID]` ahí) — por eso
  `CredencialMensajeria.scopes` existe: WhatsApp es el único de los cuatro
  conectores que necesita un segundo dato además del token para poder hablar
  con su API (`WhatsAppClient`, en `whatsapp.py`, exige `phone_number_id` en
  el constructor).

No importa `edecan_db` ni `edecan_connectors`: `ctx.vault`/`ctx.session`
llegan ya construidos por quien arma el `ToolContext` (duck typing), igual
que en `edecan_premium.telephony` y en `edecan_toolkit._conectores`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from edecan_core import ToolContext
from sqlalchemy import text

# Claves EXACTAS de conector que entiende este paquete — ver docstring.
CONNECTOR_KEYS: tuple[str, ...] = ("telegram", "discord", "slack", "whatsapp")

_DISPLAY_NAMES: dict[str, str] = {
    "telegram": "Telegram",
    "discord": "Discord",
    "slack": "Slack",
    "whatsapp": "WhatsApp",
}


def display_name(plataforma: str) -> str:
    """Nombre legible de `plataforma` para mensajes al usuario; cae al valor
    crudo si no es una de las tres plataformas soportadas."""
    return _DISPLAY_NAMES.get(plataforma, plataforma)


class MessagingNotConnectedError(Exception):
    """El tenant no tiene `plataforma` conectada (o sin credenciales válidas)
    en el vault todavía."""

    def __init__(self, plataforma: str) -> None:
        self.plataforma = plataforma
        super().__init__(
            f"Todavía no tienes {display_name(plataforma)} conectado. Conéctalo en "
            "/app/conectores y vuelve a pedírmelo."
        )


@dataclass(frozen=True)
class CredencialMensajeria:
    """Lo mínimo que necesitan `tools.py`/`clients.py`/`whatsapp.py` de una
    cuenta conectada. `scopes` viene vacío para telegram/discord/slack (no lo
    usan); para `"whatsapp"` trae `(phone_number_id,)` — ver docstring del
    módulo."""

    connector_account_id: Any
    access_token: str
    scopes: tuple[str, ...] = ()


async def resolver_credenciales(ctx: ToolContext, plataforma: str) -> CredencialMensajeria:
    """Credenciales vigentes de `plataforma` (∈ `CONNECTOR_KEYS`) para el
    tenant de `ctx`.

    Lanza `ValueError` si `plataforma` no es una de las tres soportadas (error
    de programación de quien llama, no de datos del tenant), y
    `MessagingNotConnectedError` si no hay cuenta conectada o el vault no
    devuelve un `TokenBundle` utilizable.
    """
    if plataforma not in CONNECTOR_KEYS:
        raise ValueError(f"connector_key de mensajería desconocido: {plataforma!r}")

    resultado = await ctx.session.execute(
        text(
            """
            SELECT id FROM connector_accounts
            WHERE tenant_id = :tenant_id ::uuid AND connector_key = :connector_key
              AND status NOT IN ('revoked', 'disconnected', 'error')
            ORDER BY created_at DESC
            LIMIT 1
            """
        ),
        {"tenant_id": str(ctx.tenant_id), "connector_key": plataforma},
    )
    fila = resultado.mappings().first()
    if fila is None:
        raise MessagingNotConnectedError(plataforma)
    connector_account_id = fila["id"]

    bundle = await ctx.vault.get(ctx.tenant_id, connector_account_id)
    if bundle is None or not getattr(bundle, "access_token", None):
        raise MessagingNotConnectedError(plataforma)

    return CredencialMensajeria(
        connector_account_id=connector_account_id,
        access_token=bundle.access_token,
        scopes=tuple(getattr(bundle, "scopes", None) or ()),
    )
