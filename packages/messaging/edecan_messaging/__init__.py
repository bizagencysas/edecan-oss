"""`edecan_messaging` — herramientas de mensajería oficial del agente:
Telegram, Discord, Slack (`ARCHITECTURE.md` §10.7; ROADMAP_V2.md §7.7,
WP-V2-05) y WhatsApp Business Platform (WP-V3-13, `ARCHITECTURE.md` §12.b).

Entry point `edecan.tools` (ver `pyproject.toml`): `edecan_core.ToolRegistry
.load_entry_points(group="edecan.tools")` descubre `get_all_tools()` y
registra `enviar_mensaje` (dangerous=True) y `leer_mensajes`, ambas gateadas
por el flag de plan `connectors.messaging`.

- `clients.py`: `TelegramClient`, `DiscordClient`, `SlackClient` — httpx puro
  contra las APIs oficiales de cada plataforma.
- `whatsapp.py`: `WhatsAppClient` — httpx puro contra la Cloud API oficial de
  Meta (`graph.facebook.com`). Solo ENVÍO en v3 (leer exige webhooks
  entrantes con URL pública, fuera de alcance de este work package).
- `_creds.py`: resuelve la credencial del tenant (`TokenVault` +
  `connector_accounts`) para `"telegram"`/`"discord"`/`"slack"`/`"whatsapp"`.
- `tools.py`: `EnviarMensajeTool`, `LeerMensajesTool`.
"""

from __future__ import annotations

from .tools import EnviarMensajeTool, LeerMensajesTool, get_all_tools

__all__ = ["EnviarMensajeTool", "LeerMensajesTool", "get_all_tools"]
