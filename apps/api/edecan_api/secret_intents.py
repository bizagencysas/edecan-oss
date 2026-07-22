"""Detección determinista de credenciales pegadas en el chat.

El LLM nunca debe recibir una API key para decidir dónde guardarla. Este
módulo reconoce únicamente proveedores conocidos y frases inequívocas del
dueño, extrae el secreto en memoria y produce una versión redactada para el
historial. Si la frase es ambigua, devuelve ``None`` y el chat continúa por
el camino normal.

El valor secreto solo existe en ``InlineCredentialIntent.tool_args`` durante
la petición. ``redacted_text`` es la única representación apta para base de
datos, logs, SSE y títulos de conversación.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class InlineCredentialIntent:
    """Una configuración suficientemente explícita para ejecutarse sin LLM."""

    provider: str
    display_name: str
    tool_args: dict[str, Any]
    redacted_text: str
    secret_values: tuple[str, ...]


_ACTION_RE = re.compile(
    r"\b(?:configur(?:a|ar|ame)|conect(?:a|ar|ame)|activ(?:a|ar|ame)|"
    r"guard(?:a|ar|ame)|agreg(?:a|ar|ame)|pon(?:la|lo|er)|usa(?:r)?|"
    r"mi\s+(?:api[\s_-]*key|clave|token)|aqu[ií]\s+(?:est[aá]|tienes)|mira)\b",
    re.IGNORECASE,
)
_CREDENTIAL_WORD_RE = re.compile(
    r"\b(?:api[\s_-]*key|clave(?:\s+api)?|token|credencial)\b", re.IGNORECASE
)
_TOKEN_AFTER_CREDENTIAL_RE = re.compile(
    r"(?:api[\s_-]*key|clave(?:\s+api)?|token|credencial)"
    r"(?:\s+(?:de|para)\s+[\w.-]+)?\s*(?:(?:es|vale)\s*)?(?:[:=]\s*)?"
    r"[\"']?(?P<secret>[A-Za-z0-9][A-Za-z0-9_.:/+-]{9,})[\"']?",
    re.IGNORECASE,
)
_TOKEN_AFTER_PROVIDER_RE_TEMPLATE = (
    r"(?:{provider})\b(?:\s+(?:api[\s_-]*key|clave|token))?"
    r"\s*(?:(?:es|vale)\s*)?(?:[:=]\s*)?[\"']?"
    r"(?P<secret>[A-Za-z0-9][A-Za-z0-9_.:/+-]{{9,}})[\"']?"
)
_ALPACA_KEY_ID_RE = re.compile(
    r"(?:api\s*key(?:\s*id)?|key\s*id)\s*(?:(?:es|vale)\s*)?(?:[:=]\s*)?"
    r"[\"']?(?P<value>[A-Za-z0-9_-]{8,})[\"']?",
    re.IGNORECASE,
)
_ALPACA_SECRET_RE = re.compile(
    r"(?:secret\s*key|api\s*secret|secret)\s*(?:(?:es|vale)\s*)?(?:[:=]\s*)?"
    r"[\"']?(?P<value>[A-Za-z0-9_.:/+-]{16,})[\"']?",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class _ProviderRule:
    key: str
    display_name: str
    aliases: tuple[str, ...]
    preferred_prefixes: tuple[str, ...]
    tool_args_factory: Any


def _voice_tts(secret: str) -> dict[str, Any]:
    return {
        "tipo": "voice_tts",
        "campos": {
            "provider": "elevenlabs",
            "api_key": secret,
            "model_id": "eleven_v3",
            "expressive": True,
        },
    }


def _voice_stt(secret: str) -> dict[str, Any]:
    return {"tipo": "voice_stt", "campos": {"api_key": secret}}


def _search(provider: str, secret: str) -> dict[str, Any]:
    return {"tipo": "search", "campos": {"provider": provider, "api_key": secret}}


def _llm(provider: str, secret: str) -> dict[str, Any]:
    return {"tipo": "llm", "campos": {"provider": provider, "api_key": secret}}


_PROVIDERS: tuple[_ProviderRule, ...] = (
    _ProviderRule(
        key="anthropic",
        display_name="Anthropic",
        aliases=(r"anthropic", r"claude(?:\s+api)?"),
        preferred_prefixes=("sk-ant-",),
        tool_args_factory=lambda secret: _llm("anthropic", secret),
    ),
    _ProviderRule(
        key="openai",
        display_name="OpenAI",
        aliases=(r"openai", r"chatgpt(?:\s+api)?"),
        preferred_prefixes=("sk-",),
        tool_args_factory=lambda secret: _llm("openai", secret),
    ),
    _ProviderRule(
        key="gemini",
        display_name="Gemini",
        aliases=(r"gemini", r"google\s+ai"),
        preferred_prefixes=("AIza",),
        tool_args_factory=lambda secret: _llm("gemini", secret),
    ),
    _ProviderRule(
        key="deepseek",
        display_name="DeepSeek",
        aliases=(r"deepseek",),
        preferred_prefixes=("sk-",),
        tool_args_factory=lambda secret: _llm("deepseek", secret),
    ),
    _ProviderRule(
        key="groq",
        display_name="Groq",
        aliases=(r"groq",),
        preferred_prefixes=("gsk_",),
        tool_args_factory=lambda secret: _llm("groq", secret),
    ),
    _ProviderRule(
        key="openrouter",
        display_name="OpenRouter",
        aliases=(r"openrouter", r"open\s+router"),
        preferred_prefixes=("sk-or-",),
        tool_args_factory=lambda secret: _llm("openrouter", secret),
    ),
    _ProviderRule(
        key="xai",
        display_name="xAI",
        aliases=(r"xai", r"x\.ai", r"grok"),
        preferred_prefixes=("xai-",),
        tool_args_factory=lambda secret: _llm("xai", secret),
    ),
    _ProviderRule(
        key="mistral",
        display_name="Mistral",
        aliases=(r"mistral",),
        preferred_prefixes=(),
        tool_args_factory=lambda secret: _llm("mistral", secret),
    ),
    _ProviderRule(
        key="kimi",
        display_name="Kimi",
        aliases=(r"kimi", r"moonshot"),
        preferred_prefixes=(),
        tool_args_factory=lambda secret: _llm("kimi", secret),
    ),
    _ProviderRule(
        key="elevenlabs",
        display_name="ElevenLabs",
        aliases=(r"elevenlabs", r"eleven\s+labs"),
        preferred_prefixes=("sk_",),
        tool_args_factory=_voice_tts,
    ),
    _ProviderRule(
        key="deepgram",
        display_name="Deepgram",
        aliases=(r"deepgram",),
        preferred_prefixes=("dg_",),
        tool_args_factory=_voice_stt,
    ),
    _ProviderRule(
        key="brave",
        display_name="Brave Search",
        aliases=(r"brave(?:\s+search)?",),
        preferred_prefixes=("BSA",),
        tool_args_factory=lambda secret: _search("brave", secret),
    ),
    _ProviderRule(
        key="tavily",
        display_name="Tavily",
        aliases=(r"tavily",),
        preferred_prefixes=("tvly-", "tvly_"),
        tool_args_factory=lambda secret: _search("tavily", secret),
    ),
)


def _trim_token(value: str) -> str:
    return value.rstrip('.,;!?)]}"')


def _candidate_for_rule(text: str, rule: _ProviderRule) -> str | None:
    for prefix in rule.preferred_prefixes:
        match = re.search(rf"(?<![A-Za-z0-9])({re.escape(prefix)}[A-Za-z0-9_.:+/-]{{8,}})", text)
        if match:
            return _trim_token(match.group(1))

    aliases = "|".join(rule.aliases)
    provider_match = re.search(rf"\b(?:{aliases})\b", text, re.IGNORECASE)
    if provider_match is None:
        return None

    for match in _TOKEN_AFTER_CREDENTIAL_RE.finditer(text):
        candidate = _trim_token(match.group("secret"))
        if candidate.lower() not in {
            rule.key,
            "elevenlabs",
            "deepgram",
            "brave",
            "tavily",
            "anthropic",
            "openai",
            "gemini",
            "deepseek",
            "groq",
            "openrouter",
            "xai",
            "mistral",
            "kimi",
        }:
            return candidate

    provider_pattern = re.compile(
        _TOKEN_AFTER_PROVIDER_RE_TEMPLATE.format(provider=aliases), re.IGNORECASE
    )
    match = provider_pattern.search(text)
    if match:
        candidate = _trim_token(match.group("secret"))
        if candidate.lower() not in {"api", "key", "clave", "token"}:
            return candidate
    return None


def detect_inline_credential_intent(text: str) -> InlineCredentialIntent | None:
    """Reconoce una sola credencial explícita y devuelve su versión segura.

    Se exige proveedor conocido, vocabulario de credencial y una señal de
    intención. Una cadena suelta o un ejemplo dentro de documentación no se
    configura automáticamente.
    """

    clean = text.strip()
    if not clean or not _ACTION_RE.search(clean) or not _CREDENTIAL_WORD_RE.search(clean):
        return None

    if re.search(r"\balpaca(?:\s+trading)?\b", clean, re.IGNORECASE):
        key_match = _ALPACA_KEY_ID_RE.search(clean)
        secret_match = _ALPACA_SECRET_RE.search(clean)
        if key_match is None or secret_match is None:
            return None
        api_key_id = _trim_token(key_match.group("value"))
        secret_key = _trim_token(secret_match.group("value"))
        redacted = clean.replace(api_key_id, "[API Key ID protegida]")
        redacted = redacted.replace(secret_key, "[Secret Key protegida]")
        return InlineCredentialIntent(
            provider="alpaca_paper",
            display_name="Alpaca Paper",
            tool_args={
                "tipo": "alpaca_paper",
                "campos": {"api_key_id": api_key_id, "secret_key": secret_key},
            },
            redacted_text=redacted,
            secret_values=(api_key_id, secret_key),
        )

    matches: list[tuple[_ProviderRule, str]] = []
    for rule in _PROVIDERS:
        aliases = "|".join(rule.aliases)
        if re.search(rf"\b(?:{aliases})\b", clean, re.IGNORECASE) is None:
            continue
        candidate = _candidate_for_rule(clean, rule)
        if candidate:
            matches.append((rule, candidate))

    # Un mensaje con dos proveedores o dos interpretaciones no es apto para
    # auto-configuración. El usuario puede enviarlos de uno en uno.
    if len(matches) != 1:
        return None

    rule, secret = matches[0]
    redacted = clean.replace(secret, "[credencial protegida]")
    tool_args = rule.tool_args_factory(secret)
    if rule.key == "openai" and re.search(
        r"\b(?:imagen|im[aá]genes|generaci[oó]n\s+visual|crear\s+fotos?)\b",
        clean,
        re.IGNORECASE,
    ):
        tool_args = {
            "tipo": "images",
            "campos": {"provider": "openai", "api_key": secret},
        }
    return InlineCredentialIntent(
        provider=rule.key,
        display_name=rule.display_name,
        tool_args=tool_args,
        redacted_text=redacted,
        secret_values=(secret,),
    )


def redact_values(text: str, values: tuple[str, ...]) -> str:
    """Defensa final para que una excepción de proveedor no refleje la key."""

    result = text
    for value in values:
        if value:
            result = result.replace(value, "[credencial protegida]")
    return result
