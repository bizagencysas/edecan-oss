"""Auto-configuración de credenciales por chat (`ARCHITECTURE.md` §10.14).

`ConfigurarCredencialTool` deja que el propio agente, cuando el dueño del
tenant le pega una credencial en el chat ("activa ElevenLabs, acá tienes la
key"), la guarde él mismo — mismo storage (`connector_accounts` + `TokenVault`)
que ya usan los endpoints REST `PUT /v1/credentials/voice/{stt,tts}`,
`PUT /v1/finance/stripe/credentials`, `PUT /v1/connectors/twilio/credentials`,
`PUT /v1/connectors/{key}/credentials` (bots Telegram/Discord),
`PUT /v1/connectors/whatsapp/credentials` y `PUT /v1/connectors/{key}/
app-credentials` (apps OAuth propias, `edecan_api.oauth_app_credentials`) —
para que la Settings UI y el chat sean dos caminos hacia el MISMO dato, nunca
dos fuentes de verdad distintas.

Por qué está DUPLICADA la lógica de guardado en vez de importar `apps/api`:
`ARCHITECTURE.md` §10.1 — los paquetes (`packages/toolkit`) no importan apps,
cada capa es desplegable por separado (mismo criterio ya aplicado entre
`apps/api`/`apps/worker` para la extracción de memoria y las credenciales de
apps OAuth). Se replica el mismo patrón "buscar-o-crear cuenta + vault.put"
de cada router, adaptado a lo que `ToolContext` expone (`ctx.session`/
`ctx.vault`, sin un objeto `Repo` con métodos propios) — SQL parametrizado
directo contra `connector_accounts`, mismo criterio que `recordatorios.py`/
`finanzas.py` de este mismo paquete.

Validación (trade-off deliberado): para cubrir las 7 familias de credencial
soportadas en una sola tool sin duplicar cada `_ping_*` de red distinto de
cada router, esta tool SOLO valida FORMATO (prefijos, longitudes, regex) —
NO hace un ping en vivo al proveedor antes de guardar (a diferencia de, p.
ej., `PUT /v1/finance/stripe/credentials`, que sí llama a Stripe). Un typo se
descubre en el primer uso real, no al guardar — se lo advierte al usuario en
el `content` de la respuesta. `LLM` (credencial "cerebro" del tenant) queda
fuera a propósito: tiene su propio asistente de conexión dedicado en el
wizard de onboarding (`PUT /v1/credentials/llm`), con más formas de proveedor
(Anthropic directo, OpenAI-compatible, CLIs locales) de las que vale la pena
sumar a esta tool genérica en su primera versión.

`dangerous = True`: como cualquier escritura de un secreto, exige
confirmación humana explícita antes de ejecutar (`edecan_core.agent.Agent.
run_turn`) — mismo criterio que `usar_computadora`/`enviar_correo`. Esto
protege también contra un intento de inyección de instrucciones (p. ej. un
correo que el agente lea con `buscar_correo` y que le "pida" reconfigurar una
credencial): sin aprobación humana explícita en el chat, la tool nunca corre.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from edecan_core import Tool, ToolContext, ToolResult
from edecan_schemas import TokenBundle
from sqlalchemy import text

# Mismos connector_keys que usan los routers reales — ver docstring del
# módulo. `VOICE_STT_CONNECTOR_KEY`/`VOICE_TTS_CONNECTOR_KEY` en
# `apps/api/edecan_api/deps.py`; el resto son literales pinned por
# `ARCHITECTURE.md`/los routers correspondientes.
_VOICE_STT_KEY = "voice_stt"
_VOICE_TTS_KEY = "voice_tts"
_STRIPE_KEY = "stripe"
_TWILIO_KEY = "twilio"
_WHATSAPP_KEY = "whatsapp"
_BOT_TOKEN_CONECTORES = ("telegram", "discord")
_OAUTH_APP_CONECTORES = ("google", "microsoft", "meta", "x", "youtube", "slack")
_OAUTH_APP_SUFFIX = "__app_config"  # ver edecan_api.oauth_app_credentials

_TWILIO_SID_RE = re.compile(r"^AC[0-9a-fA-F]{32}$")
_TWILIO_AUTH_TOKEN_RE = re.compile(r"^[0-9a-zA-Z]{32}$")
_E164_RE = re.compile(r"^\+[1-9]\d{1,14}$")
_WHATSAPP_PHONE_NUMBER_ID_RE = re.compile(r"^\d+$")
_WHATSAPP_TOKEN_MIN_LEN = 20
_MIN_BOT_TOKEN_LEN = 10
_POLLY_DEFAULT_VOICE = "Lupe"

_TIPOS_VALIDOS = (
    "voice_stt",
    "voice_tts",
    "stripe",
    "twilio",
    "whatsapp",
    "bot_token",
    "oauth_app",
)


async def _find_connector_account(
    ctx: ToolContext, connector_key: str
) -> dict[str, Any] | None:
    resultado = await ctx.session.execute(
        text(
            "SELECT id, external_account_id FROM connector_accounts "
            "WHERE tenant_id = :tenant_id AND connector_key = :connector_key "
            "ORDER BY created_at ASC LIMIT 1"
        ),
        {"tenant_id": str(ctx.tenant_id), "connector_key": connector_key},
    )
    fila = resultado.mappings().first()
    return dict(fila) if fila else None


async def _find_or_create_connector_account(
    ctx: ToolContext, connector_key: str, external_account_id: str, display_name: str
) -> dict[str, Any]:
    existente = await _find_connector_account(ctx, connector_key)
    if existente is not None:
        return existente
    now = datetime.now(UTC)
    resultado = await ctx.session.execute(
        text(
            "INSERT INTO connector_accounts "
            "(id, tenant_id, connector_key, external_account_id, display_name, status, "
            "scopes, created_at, updated_at) "
            "VALUES (:id, :tenant_id, :connector_key, :external_account_id, :display_name, "
            "'active', '[]'::jsonb, :now, :now) "
            "RETURNING id, external_account_id"
        ),
        {
            "id": str(uuid.uuid4()),
            "tenant_id": str(ctx.tenant_id),
            "connector_key": connector_key,
            "external_account_id": external_account_id,
            "display_name": display_name,
            "now": now,
        },
    )
    fila = resultado.mappings().first()
    return dict(fila)


async def _replace_connector_account(
    ctx: ToolContext, connector_key: str, external_account_id: str, display_name: str
) -> dict[str, Any]:
    """A diferencia de `_find_or_create_connector_account` (reusa la fila si
    ya existe), esta variante SIEMPRE deja `external_account_id` al día —
    necesario para `oauth_app`, donde ese campo guarda el `client_id` en
    claro y reconfigurar con uno distinto no debe dejar el viejo pisado (ver
    `edecan_api.oauth_app_credentials.put_oauth_app_credentials`, mismo
    criterio)."""
    existente = await _find_connector_account(ctx, connector_key)
    if existente is not None:
        await ctx.session.execute(
            text("DELETE FROM connector_accounts WHERE id = :id"), {"id": existente["id"]}
        )
    return await _find_or_create_connector_account(
        ctx, connector_key, external_account_id, display_name
    )


class ConfigurarCredencialTool(Tool):
    name = "configurar_credencial"
    description = (
        "Guarda una credencial que el dueño del tenant pegó directamente en el chat "
        "(p. ej. 'activa ElevenLabs, esta es mi api_key: ...') sin que tenga que ir a "
        "Ajustes/Conectores a pegarla él mismo. Cubre: voz (voice_stt=Deepgram, "
        "voice_tts=ElevenLabs/Polly), Stripe, Twilio, WhatsApp Business, bots de "
        "Telegram/Discord, y apps OAuth propias (Google/Microsoft/Meta/X/YouTube/Slack). "
        "Requiere confirmación porque persiste un secreto de verdad."
    )
    dangerous = True
    input_schema = {
        "type": "object",
        "properties": {
            "tipo": {
                "type": "string",
                "enum": list(_TIPOS_VALIDOS),
                "description": "Familia de credencial a configurar.",
            },
            "conector": {
                "type": "string",
                "description": (
                    "Solo para tipo='bot_token' ('telegram'|'discord') o "
                    "tipo='oauth_app' ('google'|'microsoft'|'meta'|'x'|'youtube'|'slack'). "
                    "Se ignora para los demás tipos."
                ),
            },
            "campos": {
                "type": "object",
                "description": (
                    "Campos específicos del tipo. voice_stt: {api_key}. voice_tts: "
                    "{provider: 'elevenlabs'|'polly', api_key?, voice_id?}. stripe: "
                    "{api_key} (debe empezar con 'rk_', nunca 'sk_'). twilio: "
                    "{account_sid, auth_token, phone_number}. whatsapp: {access_token, "
                    "phone_number_id}. bot_token: {bot_token}. oauth_app: {client_id, "
                    "client_secret?}."
                ),
            },
        },
        "required": ["tipo", "campos"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        if ctx.vault is None:
            return ToolResult(
                content="No tengo acceso al vault de credenciales en este contexto; "
                "pídele al dueño que la pegue desde Ajustes."
            )

        tipo = str(args.get("tipo", "")).strip()
        if tipo not in _TIPOS_VALIDOS:
            return ToolResult(
                content=f"Tipo de credencial desconocido: {tipo!r}. "
                f"Debe ser uno de: {', '.join(_TIPOS_VALIDOS)}."
            )
        campos = args.get("campos")
        if not isinstance(campos, dict):
            campos = {}
        conector = str(args.get("conector", "")).strip().lower()

        handler = {
            "voice_stt": self._voice_stt,
            "voice_tts": self._voice_tts,
            "stripe": self._stripe,
            "twilio": self._twilio,
            "whatsapp": self._whatsapp,
            "bot_token": self._bot_token,
            "oauth_app": self._oauth_app,
        }[tipo]
        try:
            return await handler(ctx, campos, conector)
        except _CampoInvalido as exc:
            return ToolResult(content=str(exc))

    async def _voice_stt(
        self, ctx: ToolContext, campos: dict[str, Any], _conector: str
    ) -> ToolResult:
        api_key = _requerido(campos, "api_key")
        account = await _find_or_create_connector_account(
            ctx, _VOICE_STT_KEY, "deepgram", "Deepgram (voz → texto)"
        )
        await ctx.vault.put(
            ctx.tenant_id,
            account["id"],
            TokenBundle(
                access_token=_json({"provider": "deepgram", "api_key": api_key}),
                token_type="config",
                scopes=["deepgram"],
            ),
        )
        return ToolResult(
            content="Listo, conecté tu propia cuenta de Deepgram para transcripción de voz "
            "(no verifiqué la key contra su API en vivo — si algo falla, revisa que sea "
            "correcta desde Ajustes → Conectores).",
            data={"tipo": "voice_stt", "provider": "deepgram"},
        )

    async def _voice_tts(
        self, ctx: ToolContext, campos: dict[str, Any], _conector: str
    ) -> ToolResult:
        provider = str(campos.get("provider", "elevenlabs")).strip().lower()
        if provider not in ("elevenlabs", "polly"):
            raise _CampoInvalido(
                f"provider de voz_tts desconocido: {provider!r} (usa 'elevenlabs' o 'polly')."
            )
        voice_id = (campos.get("voice_id") or "").strip() or None

        if provider == "elevenlabs":
            api_key = _requerido(campos, "api_key")
            config: dict[str, Any] = {
                "provider": "elevenlabs",
                "api_key": api_key,
                "voice_id": voice_id,
            }
            scopes = ["elevenlabs"]
        else:
            if not getattr(ctx.settings, "EDECAN_LOCAL_MODE", False):
                raise _CampoInvalido(
                    "'polly' solo funciona en la app de escritorio (modo local): usa la "
                    "identidad AWS del proceso, no una api_key propia. Usa 'elevenlabs' en "
                    "su lugar."
                )
            config = {"provider": "polly", "voice": voice_id or _POLLY_DEFAULT_VOICE}
            scopes = ["polly"]

        account = await _find_or_create_connector_account(
            ctx, _VOICE_TTS_KEY, provider, f"{provider.title()} (texto → voz)"
        )
        await ctx.vault.put(
            ctx.tenant_id,
            account["id"],
            TokenBundle(access_token=_json(config), token_type="config", scopes=scopes),
        )
        return ToolResult(
            content=f"Listo, conecté tu propia cuenta de {provider.title()} para texto-a-voz "
            "(no verifiqué la key contra su API en vivo — pídeme que hable en voz alta para "
            "probarla).",
            data={"tipo": "voice_tts", "provider": provider},
        )

    async def _stripe(
        self, ctx: ToolContext, campos: dict[str, Any], _conector: str
    ) -> ToolResult:
        api_key = _requerido(campos, "api_key")
        if not api_key.startswith("rk_"):
            raise _CampoInvalido(
                "Usa una Restricted key de Stripe (empieza con 'rk_'), nunca tu Secret key "
                "completa ('sk_') — puede mover dinero. Ver docs/finanzas-stripe.md."
            )
        account = await _find_or_create_connector_account(ctx, _STRIPE_KEY, _STRIPE_KEY, "Stripe")
        await ctx.vault.put(
            ctx.tenant_id,
            account["id"],
            TokenBundle(access_token=api_key, token_type="restricted_key", scopes=["stripe"]),
        )
        return ToolResult(
            content="Listo, conecté tu cuenta de Stripe (no verifiqué la key contra la API en "
            "vivo — usa 'sincronizar Stripe' para confirmar que funciona).",
            data={"tipo": "stripe"},
        )

    async def _twilio(
        self, ctx: ToolContext, campos: dict[str, Any], _conector: str
    ) -> ToolResult:
        account_sid = _requerido(campos, "account_sid")
        auth_token = _requerido(campos, "auth_token")
        phone_number = _requerido(campos, "phone_number")
        if not _TWILIO_SID_RE.match(account_sid):
            raise _CampoInvalido("account_sid inválido: debe empezar con 'AC' + 32 caracteres hex.")
        if not _TWILIO_AUTH_TOKEN_RE.match(auth_token):
            raise _CampoInvalido("auth_token inválido: deben ser 32 caracteres alfanuméricos.")
        if not _E164_RE.match(phone_number):
            raise _CampoInvalido("phone_number inválido: usa formato E.164, p. ej. +525512345678.")

        account = await _find_or_create_connector_account(
            ctx, _TWILIO_KEY, phone_number, phone_number
        )
        await ctx.vault.put(
            ctx.tenant_id,
            account["id"],
            TokenBundle(access_token=auth_token, token_type="twilio", scopes=[account_sid]),
        )
        return ToolResult(
            content=f"Listo, conecté tu número de Twilio {phone_number} (no verifiqué en vivo "
            "que el número pertenezca a esa cuenta -- si algo falla, revisa desde Ajustes → "
            "Conectores).",
            data={"tipo": "twilio", "phone_number": phone_number},
        )

    async def _whatsapp(
        self, ctx: ToolContext, campos: dict[str, Any], _conector: str
    ) -> ToolResult:
        access_token = _requerido(campos, "access_token")
        phone_number_id = _requerido(campos, "phone_number_id")
        if len(access_token) < _WHATSAPP_TOKEN_MIN_LEN:
            raise _CampoInvalido(
                f"access_token demasiado corto (mínimo {_WHATSAPP_TOKEN_MIN_LEN} caracteres)."
            )
        if not _WHATSAPP_PHONE_NUMBER_ID_RE.match(phone_number_id):
            raise _CampoInvalido("phone_number_id inválido: debe ser solo dígitos.")

        account = await _replace_connector_account(
            ctx, _WHATSAPP_KEY, phone_number_id, "WhatsApp Business Platform"
        )
        await ctx.vault.put(
            ctx.tenant_id,
            account["id"],
            TokenBundle(access_token=access_token, token_type="whatsapp", scopes=[phone_number_id]),
        )
        return ToolResult(
            content="Listo, conecté tu número de WhatsApp Business (no verifiqué en vivo contra "
            "la Graph API de Meta -- si algo falla, revisa desde Ajustes → Conectores).",
            data={"tipo": "whatsapp"},
        )

    async def _bot_token(
        self, ctx: ToolContext, campos: dict[str, Any], conector: str
    ) -> ToolResult:
        if conector not in _BOT_TOKEN_CONECTORES:
            raise _CampoInvalido(
                "Para tipo='bot_token' necesito 'conector' = 'telegram' o 'discord' "
                f"(recibí {conector!r})."
            )
        bot_token = _requerido(campos, "bot_token")
        if len(bot_token) < _MIN_BOT_TOKEN_LEN:
            raise _CampoInvalido(
                f"bot_token demasiado corto (mínimo {_MIN_BOT_TOKEN_LEN} caracteres)."
            )

        account = await _replace_connector_account(
            ctx, conector, conector, conector.title()
        )
        await ctx.vault.put(
            ctx.tenant_id,
            account["id"],
            TokenBundle(access_token=bot_token, token_type="bot_token", scopes=[conector]),
        )
        return ToolResult(
            content=f"Listo, conecté tu bot de {conector.title()} (no verifiqué el token en vivo "
            "-- se comprobará solo en el primer envío/lectura real).",
            data={"tipo": "bot_token", "conector": conector},
        )

    async def _oauth_app(
        self, ctx: ToolContext, campos: dict[str, Any], conector: str
    ) -> ToolResult:
        if conector not in _OAUTH_APP_CONECTORES:
            raise _CampoInvalido(
                "Para tipo='oauth_app' necesito 'conector' uno de: "
                f"{', '.join(_OAUTH_APP_CONECTORES)} (recibí {conector!r})."
            )
        client_id = _requerido(campos, "client_id")
        client_secret = (campos.get("client_secret") or "").strip() or ""

        account = await _replace_connector_account(
            ctx,
            f"{conector}{_OAUTH_APP_SUFFIX}",
            client_id,
            f"App OAuth propia de {conector.title()}",
        )
        await ctx.vault.put(
            ctx.tenant_id,
            account["id"],
            TokenBundle(
                access_token=client_secret, token_type="oauth_app_secret", scopes=[conector]
            ),
        )
        return ToolResult(
            content=f"Listo, conecté tu propia app OAuth de {conector.title()}. Ahora podés "
            f"presionar 'Conectar' en Conectores → {conector.title()} para autorizar tu cuenta.",
            data={"tipo": "oauth_app", "conector": conector},
        )


class _CampoInvalido(Exception):
    """Error de validación de negocio -- `run()` lo convierte en un `ToolResult`
    con el mensaje tal cual, sin tumbar el turno (ver `Tool.run` en `edecan_core.tools.base`)."""


def _requerido(campos: dict[str, Any], nombre: str) -> str:
    valor = str(campos.get(nombre, "")).strip()
    if not valor:
        raise _CampoInvalido(f"Falta el campo obligatorio '{nombre}'.")
    return valor


def _json(config: dict[str, Any]) -> str:
    return json.dumps(config)
