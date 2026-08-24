"""Conectores de mensajería oficiales: Slack (OAuth) — y por qué Telegram y
Discord NO viven aquí (`ARCHITECTURE.md` §10.8; ROADMAP_V2.md §7.7, WP-V2-05;
`docs/mensajeria.md`).

`edecan_connectors.registry` importa este submódulo dentro de un
`try/except ImportError` (igual que `edecan_connectors.social`) y mezcla
`MESSAGING_CONNECTORS` dentro de `CONNECTORS`.

Telegram y Discord NO son OAuth 2.0: cada tenant crea su propio bot
(BotFather / Discord Developer Portal) y pega el TOKEN DEL BOT directamente
vía `PUT /v1/connectors/{key}/credentials` para `key` ∈ {"telegram",
"discord"} (`edecan_api.routers.connectors`) — el mismo patrón no-OAuth que
ya usa Twilio (`ARCHITECTURE.md` §10.10, `PUT /v1/connectors/twilio/credentials`).
Por eso NO implementan el contrato `Connector` (que asume un flujo
`authorize`/`callback` con `auth_url`/`exchange_code`/`refresh`) y NO entran
en este registro, aunque SÍ comparten `connector_accounts`/`TokenVault` con
los conectores que sí son OAuth.

Los clientes reales de Telegram, Discord y Slack (los que de verdad envían y
leen mensajes) viven en `edecan_messaging` (`packages/messaging/`), que
resuelve sus credenciales del mismo `TokenVault` por `connector_key`
("telegram"|"discord"|"slack") sin pasar por el contrato `Connector` de este
paquete — Slack sí pasa por aquí (`SlackConnector`, abajo) únicamente para el
intercambio OAuth inicial (`authorize`/`callback`); una vez conectado,
`edecan_messaging.clients.SlackClient` habla directo con la Web API usando el
access token ya resuelto.
"""

from __future__ import annotations

from .slack import SlackConnector

MESSAGING_CONNECTORS = {"slack": SlackConnector()}

__all__ = ["MESSAGING_CONNECTORS", "SlackConnector"]
