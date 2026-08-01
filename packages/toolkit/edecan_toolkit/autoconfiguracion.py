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

Validación: LLM, imágenes y Alpaca se comprueban en vivo antes de guardar,
porque una conexión incompleta dejaría inutilizable el cerebro, el estudio o
las órdenes. Las demás familias conservan validación de formato y dejan una
explicación clara cuando la verificación completa ocurre en el primer uso.
Los CLIs locales y Ollama no necesitan copiar secretos por chat: el asistente
de conexión del master los detecta y ofrece directamente.

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

import httpx
from edecan_core import Tool, ToolContext, ToolResult
from edecan_llm import choose_discovered_models
from edecan_schemas import TokenBundle
from sqlalchemy import text

# Mismos connector_keys que usan los routers reales — ver docstring del
# módulo. `VOICE_STT_CONNECTOR_KEY`/`VOICE_TTS_CONNECTOR_KEY` en
# `apps/api/edecan_api/deps.py`; el resto son literales pinned por
# `ARCHITECTURE.md`/los routers correspondientes.
_VOICE_STT_KEY = "voice_stt"
_VOICE_TTS_KEY = "voice_tts"
_LLM_KEY = "llm"
_IMAGES_KEY = "images"
_SEARCH_KEY = "search"
_ALPACA_PAPER_KEY = "alpaca_paper"
_STUDIO_KEY = "fydesign"
_STRIPE_KEY = "stripe"
_TWILIO_KEY = "twilio"
_WHATSAPP_KEY = "whatsapp"
_BOT_TOKEN_CONECTORES = ("telegram", "discord")
_OAUTH_APP_CONECTORES = (
    "google",
    "microsoft",
    "linkedin",
    "meta",
    "x",
    "youtube",
    "slack",
)
_OAUTH_APP_SUFFIX = "__app_config"  # ver edecan_api.oauth_app_credentials

_TWILIO_SID_RE = re.compile(r"^AC[0-9a-fA-F]{32}$")
_TWILIO_AUTH_TOKEN_RE = re.compile(r"^[0-9a-zA-Z]{32}$")
_E164_RE = re.compile(r"^\+[1-9]\d{1,14}$")
_WHATSAPP_PHONE_NUMBER_ID_RE = re.compile(r"^\d+$")
_WHATSAPP_TOKEN_MIN_LEN = 20
_MIN_BOT_TOKEN_LEN = 10
_POLLY_DEFAULT_VOICE = "Lupe"
_SEARCH_PROVIDERS = ("brave", "tavily")
_LLM_PROVIDERS: dict[str, tuple[str, str | None]] = {
    "anthropic": ("anthropic", None),
    "gemini": ("vertex", None),
    "openai": ("openai_compat", "https://api.openai.com/v1"),
    "deepseek": ("openai_compat", "https://api.deepseek.com"),
    "groq": ("openai_compat", "https://api.groq.com/openai/v1"),
    "openrouter": ("openai_compat", "https://openrouter.ai/api/v1"),
    "xai": ("openai_compat", "https://api.x.ai/v1"),
    "mistral": ("openai_compat", "https://api.mistral.ai/v1"),
    "kimi": ("openai_compat", "https://api.moonshot.ai/v1"),
}
_PROVIDER_DISPLAY = {
    "anthropic": "Anthropic",
    "gemini": "Gemini",
    "openai": "OpenAI",
    "deepseek": "DeepSeek",
    "groq": "Groq",
    "openrouter": "OpenRouter",
    "xai": "xAI",
    "mistral": "Mistral",
    "kimi": "Kimi",
}
_ELEVENLABS_MODELS = (
    "eleven_v3",
    "eleven_flash_v2_5",
    "eleven_multilingual_v2",
)

_OPENAI_IMAGE_MODEL_PRIORITY = (
    "gpt-image-2",
    "gpt-image-1.5",
    "gpt-image-1",
    "gpt-image-1-mini",
)


def _choose_openai_image_model(items: Any) -> str | None:
    """Prefiere aliases estables actuales antes que snapshots o modelos viejos."""

    rows = [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    by_id = {str(item.get("id") or "").strip(): item for item in rows}
    for model in _OPENAI_IMAGE_MODEL_PRIORITY:
        if model in by_id:
            return model
    snapshots_v2 = [item for item in rows if str(item.get("id") or "").startswith("gpt-image-2-")]
    if snapshots_v2:
        selected = max(
            snapshots_v2,
            key=lambda item: (int(item.get("created") or 0), str(item.get("id") or "")),
        )
        return str(selected.get("id") or "").strip() or None
    candidates = [item for item in rows if "image" in str(item.get("id") or "").lower()]
    if not candidates:
        return None
    selected = max(
        candidates,
        key=lambda item: (int(item.get("created") or 0), str(item.get("id") or "")),
    )
    return str(selected.get("id") or "").strip() or None

_TIPOS_VALIDOS = (
    "llm",
    "images",
    "voice_stt",
    "voice_tts",
    "stripe",
    "twilio",
    "whatsapp",
    "bot_token",
    "oauth_app",
    "search",
    "alpaca_paper",
    "studio",
)


async def _find_connector_account(ctx: ToolContext, connector_key: str) -> dict[str, Any] | None:
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
        "Ajustes/Conectores a pegarla él mismo. Cubre: inteligencia (LLM), imágenes, "
        "voz (voice_stt=Deepgram, voice_tts=ElevenLabs/Polly), Stripe, Twilio, "
        "WhatsApp Business, bots de "
        "Telegram/Discord, búsqueda Brave/Tavily, proveedores de Studio y apps OAuth propias "
        "(Google/Microsoft/Meta/X/YouTube/Slack). "
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
                    "tipo='oauth_app' ('google'|'microsoft'|'linkedin'|'meta'|'x'|"
                    "'youtube'|'slack'). "
                    "Se ignora para los demás tipos."
                ),
            },
            "campos": {
                "type": "object",
                "description": (
                    "Campos específicos del tipo. voice_stt: {api_key}. voice_tts: "
                    "{provider: 'elevenlabs'|'polly', api_key?, voice_id?, model_id?, "
                    "expressive?}. stripe: "
                    "{api_key} (debe empezar con 'rk_', nunca 'sk_'). twilio: "
                    "{account_sid, auth_token, phone_number}. whatsapp: {access_token, "
                    "phone_number_id}. bot_token: {bot_token}. oauth_app: {client_id, "
                    "client_secret?}. search: {provider: 'brave'|'tavily', api_key}."
                    " alpaca_paper: {api_key_id, secret_key}. llm: {provider, api_key}. "
                    "images: {provider: 'openai', api_key, model?}. "
                    "studio: {provider: 'fal'|'muapi'|'openai'|'gemini'|'github', api_key}. "
                    "Las cuentas de servicio Google JSON solo se agregan desde Ajustes."
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
            "llm": self._llm,
            "images": self._images,
            "voice_stt": self._voice_stt,
            "voice_tts": self._voice_tts,
            "stripe": self._stripe,
            "twilio": self._twilio,
            "whatsapp": self._whatsapp,
            "bot_token": self._bot_token,
            "oauth_app": self._oauth_app,
            "search": self._search,
            "alpaca_paper": self._alpaca_paper,
            "studio": self._studio,
        }[tipo]
        try:
            return await handler(ctx, campos, conector)
        except _CampoInvalido as exc:
            return ToolResult(content=str(exc))

    async def _studio(
        self, ctx: ToolContext, campos: dict[str, Any], _conector: str
    ) -> ToolResult:
        if not getattr(ctx.settings, "EDECAN_LOCAL_MODE", False):
            raise _CampoInvalido("Las credenciales de Studio solo se configuran en la app local.")
        provider = str(campos.get("provider") or "").strip().lower()
        env_key = {
            "fal": "FAL_KEY",
            "muapi": "MUAPI_API_KEY",
            "openai": "OPENAI_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "github": "GITHUB_TOKEN",
        }.get(provider)
        if env_key is None:
            raise _CampoInvalido("Studio admite fal.ai, Muapi, OpenAI, Gemini o GitHub.")
        api_key = _requerido(campos, "api_key")
        account = await _find_or_create_connector_account(
            ctx, _STUDIO_KEY, _STUDIO_KEY, "Proveedores de Studio"
        )
        config: dict[str, Any] = {"env": {}}
        try:
            previous = await ctx.vault.get(ctx.tenant_id, account["id"])
            parsed = json.loads(previous.access_token) if previous is not None else {}
            if isinstance(parsed, dict) and isinstance(parsed.get("env"), dict):
                config["env"].update(
                    {
                        str(key): str(value)
                        for key, value in parsed["env"].items()
                        if isinstance(value, str)
                    }
                )
        except Exception:  # noqa: BLE001 - un registro previo roto se reemplaza sin reflejarlo
            config = {"env": {}}
        config["env"][env_key] = api_key
        await ctx.vault.put(
            ctx.tenant_id,
            account["id"],
            TokenBundle(
                access_token=_json(config),
                token_type="studio_config",
                scopes=sorted(config["env"]),
            ),
        )
        return ToolResult(
            content=(
                f"Listo, conecté {provider} a Studio. La clave quedó cifrada y no "
                "permanece visible en el chat. La validaré en su primera operación "
                "porque el proveedor no ofrece una comprobación gratuita estable."
            ),
            data={"tipo": "studio", "provider": provider},
        )

    async def _llm(
        self, ctx: ToolContext, campos: dict[str, Any], _conector: str
    ) -> ToolResult:
        provider = str(campos.get("provider") or "").strip().lower()
        if provider not in _LLM_PROVIDERS:
            raise _CampoInvalido(
                "Proveedor de inteligencia no reconocido. Puedo configurar Anthropic, "
                "Gemini, OpenAI, DeepSeek, Groq, OpenRouter, xAI, Mistral o Kimi."
            )
        api_key = _requerido(campos, "api_key")
        kind, base_url = _LLM_PROVIDERS[provider]
        display_name = _PROVIDER_DISPLAY[provider]
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                if provider == "anthropic":
                    response = await client.get(
                        "https://api.anthropic.com/v1/models",
                        headers={
                            "x-api-key": api_key,
                            "anthropic-version": "2023-06-01",
                        },
                    )
                elif provider == "gemini":
                    response = await client.get(
                        "https://generativelanguage.googleapis.com/v1beta/models",
                        params={"key": api_key},
                    )
                else:
                    response = await client.get(
                        f"{str(base_url).rstrip('/')}/models",
                        headers={"Authorization": f"Bearer {api_key}"},
                    )
        except httpx.HTTPError:
            return ToolResult(
                content=(
                    f"No pude contactar {display_name}, así que no guardé la clave. "
                    "Inténtalo de nuevo cuando tengas conexión."
                )
            )
        if not 200 <= response.status_code < 300:
            return ToolResult(
                content=(
                    f"{display_name} rechazó esa clave. No guardé nada. "
                    "Verifica la credencial y vuelve a enviarla."
                )
            )
        try:
            payload = response.json()
        except ValueError:
            return ToolResult(
                content=(
                    f"{display_name} respondió sin un catálogo de modelos válido. "
                    "No guardé la clave."
                )
            )
        choice = choose_discovered_models(kind, payload)
        if choice is None:
            return ToolResult(
                content=(
                    f"La clave de {display_name} respondió, pero no encontré un modelo "
                    "de conversación utilizable. No guardé nada para evitar dejar Edecán roto."
                )
            )
        config = {
            "kind": kind,
            "api_key": api_key,
            "base_url": base_url,
            "model_principal": choice.principal,
            "model_rapido": choice.rapido,
            "extra": {"provider_label": provider},
        }
        account = await _find_or_create_connector_account(
            ctx, _LLM_KEY, _LLM_KEY, "Proveedor de inteligencia"
        )
        await ctx.vault.put(
            ctx.tenant_id,
            account["id"],
            TokenBundle(
                access_token=_json(config),
                token_type="config",
                scopes=[kind, provider],
            ),
        )
        return ToolResult(
            content=(
                f"Listo, conecté y verifiqué {display_name}. Elegí "
                f"{choice.principal} como modelo principal y {choice.rapido} como rápido. "
                "La clave quedó cifrada y no permanece visible en el chat."
            ),
            data={
                "tipo": "llm",
                "provider": provider,
                "model_principal": choice.principal,
                "model_rapido": choice.rapido,
            },
        )

    async def _images(
        self, ctx: ToolContext, campos: dict[str, Any], _conector: str
    ) -> ToolResult:
        provider = str(campos.get("provider") or "openai").strip().lower()
        if provider != "openai":
            raise _CampoInvalido(
                "La configuración automática de imágenes admite OpenAI. Para un endpoint "
                "compatible personalizado necesito también su URL y modelo en Ajustes."
            )
        api_key = _requerido(campos, "api_key")
        base_url = "https://api.openai.com/v1"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    f"{base_url}/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
        except httpx.HTTPError:
            return ToolResult(
                content="No pude contactar OpenAI, así que no guardé la clave de imágenes."
            )
        if not 200 <= response.status_code < 300:
            return ToolResult(
                content="OpenAI rechazó esa clave. No guardé nada; verifica la credencial."
            )
        try:
            items = response.json().get("data", [])
        except (AttributeError, ValueError):
            items = []
        announced_models = {
            str(item.get("id") or "").strip()
            for item in items
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }
        model = str(campos.get("model") or "").strip()
        if model and model not in announced_models:
            return ToolResult(
                content=(
                    f"OpenAI aceptó la clave, pero no anunció el modelo {model}. "
                    "No guardé una configuración que no pude verificar."
                )
            )
        if not model:
            model = _choose_openai_image_model(items) or ""
        if not model:
            return ToolResult(
                content=(
                    "La clave de OpenAI respondió, pero la cuenta no anunció un modelo de "
                    "imágenes. No guardé nada para evitar una conexión incompleta."
                )
            )
        account = await _find_or_create_connector_account(
            ctx, _IMAGES_KEY, _IMAGES_KEY, "Generación de imágenes"
        )
        await ctx.vault.put(
            ctx.tenant_id,
            account["id"],
            TokenBundle(
                access_token=_json({"base_url": base_url, "api_key": api_key, "model": model}),
                token_type="config",
                scopes=["openai_compat"],
            ),
        )
        return ToolResult(
            content=(
                f"Listo, conecté OpenAI para crear imágenes con {model}. La clave quedó "
                "cifrada y no permanece visible en el chat."
            ),
            data={"tipo": "images", "provider": provider, "model": model},
        )

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
            requested_model = campos.get("model_id")
            model_id = str(requested_model or "eleven_multilingual_v2").strip()
            if model_id not in _ELEVENLABS_MODELS:
                raise _CampoInvalido(
                    "Modelo de ElevenLabs desconocido. Usa eleven_v3, "
                    "eleven_flash_v2_5 o eleven_multilingual_v2."
                )
            expressive = bool(campos.get("expressive", model_id == "eleven_v3"))
            config: dict[str, Any] = {
                "provider": "elevenlabs",
                "api_key": api_key,
                "voice_id": voice_id,
            }
            if requested_model is not None:
                config["model_id"] = model_id
                config["expressive"] = expressive and model_id == "eleven_v3"
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
            "(no verifiqué la key contra su API en vivo; pídeme que hable en voz alta para "
            "probarla).",
            data={"tipo": "voice_tts", "provider": provider},
        )

    async def _search(self, ctx: ToolContext, campos: dict[str, Any], _conector: str) -> ToolResult:
        provider = str(campos.get("provider") or "").strip().lower()
        if provider not in _SEARCH_PROVIDERS:
            raise _CampoInvalido("Búsqueda admite 'brave' o 'tavily'.")
        api_key = _requerido(campos, "api_key")
        account = await _find_or_create_connector_account(
            ctx, _SEARCH_KEY, _SEARCH_KEY, "Búsqueda web"
        )
        await ctx.vault.put(
            ctx.tenant_id,
            account["id"],
            TokenBundle(
                access_token=_json({"provider": provider, "api_key": api_key}),
                token_type="config",
                scopes=[provider],
            ),
        )
        return ToolResult(
            content=(
                f"Listo, conecté {provider.title()} como buscador web. La clave quedó "
                "cifrada y no permanece visible en el chat."
            ),
            data={"tipo": "search", "provider": provider},
        )

    async def _alpaca_paper(
        self, ctx: ToolContext, campos: dict[str, Any], _conector: str
    ) -> ToolResult:
        api_key_id = _requerido(campos, "api_key_id")
        secret_key = _requerido(campos, "secret_key")
        if len(api_key_id) < 8 or len(secret_key) < 16:
            raise _CampoInvalido("Las credenciales de Alpaca Paper parecen incompletas.")
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    "https://paper-api.alpaca.markets/v2/account",
                    headers={
                        "APCA-API-KEY-ID": api_key_id,
                        "APCA-API-SECRET-KEY": secret_key,
                    },
                )
        except httpx.HTTPError:
            return ToolResult(
                content=(
                    "No pude contactar Alpaca Paper, así que no guardé las credenciales. "
                    "Inténtalo de nuevo cuando tengas conexión."
                )
            )
        if not 200 <= response.status_code < 300:
            return ToolResult(
                content=(
                    "Alpaca Paper rechazó esas credenciales. No guardé nada. Verifica que "
                    "sean las claves de Paper Trading, no las de live."
                )
            )
        account = await _find_or_create_connector_account(
            ctx, _ALPACA_PAPER_KEY, _ALPACA_PAPER_KEY, "Alpaca Paper Trading"
        )
        await ctx.vault.put(
            ctx.tenant_id,
            account["id"],
            TokenBundle(
                access_token=_json(
                    {
                        "environment": "paper",
                        "api_key_id": api_key_id,
                        "secret_key": secret_key,
                    }
                ),
                token_type="config",
                scopes=["account:read", "trading:paper"],
            ),
        )
        return ToolResult(
            content=(
                "Listo, conecté y verifiqué tu cuenta Alpaca Paper. Edecán puede consultar "
                "la cartera y preparar órdenes simuladas; cada orden sigue requiriendo tu "
                "confirmación."
            ),
            data={"tipo": "alpaca_paper", "environment": "paper"},
        )

    async def _stripe(self, ctx: ToolContext, campos: dict[str, Any], _conector: str) -> ToolResult:
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

    async def _twilio(self, ctx: ToolContext, campos: dict[str, Any], _conector: str) -> ToolResult:
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

        account = await _replace_connector_account(ctx, conector, conector, conector.title())
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
