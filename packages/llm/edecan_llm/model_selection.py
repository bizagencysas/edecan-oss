"""Selección reproducible de modelos descubiertos en el proveedor del usuario.

Edecan no debe congelar la experiencia en un nombre de modelo que envejece
rápido ni apuntar silenciosamente a un alias mutable. Al conectar una cuenta,
los endpoints oficiales de Anthropic, OpenAI-compatible y Gemini ya devuelven
los modelos realmente disponibles para ESA credencial. Este módulo elige una
pareja de calidad/rapidez y el router guarda los IDs exactos en la configuración
del tenant.

La heurística es conservadora: descarta modelos de embeddings, imagen, audio y
moderación; prefiere aliases estables cuando existen y nunca vuelve a consultar
la red durante un turno. Un endpoint compatible desconocido conserva su propio
orden/fecha y el usuario siempre puede fijar ambos IDs manualmente.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

ProviderKind = Literal["anthropic", "openai_compat", "vertex"]

_SNAPSHOT_SUFFIX = re.compile(r"-\d{4}-\d{2}-\d{2}$")
_VERSION_PART = re.compile(r"\d+")
_PARAMETER_BILLIONS = re.compile(r"(?:^|[-_.])(\d+(?:\.\d+)?)b(?:$|[-_.])", re.IGNORECASE)
_NON_CHAT_MARKERS = (
    "audio",
    "diarize",
    "embedding",
    "image",
    "moderation",
    "realtime",
    "search",
    "transcribe",
    "translation",
    "tts",
    "video",
    "whisper",
)


@dataclass(frozen=True)
class ModelChoice:
    principal: str
    rapido: str


@dataclass(frozen=True)
class _Model:
    model_id: str
    created: int
    position: int
    stable: bool


def choose_discovered_models(kind: ProviderKind, payload: Any) -> ModelChoice | None:
    """Elige modelos de texto/tool-use a partir de un payload ``/models``.

    Devuelve ``None`` cuando el proveedor no anunció ningún candidato seguro;
    el caller decide si puede usar un default propio (Anthropic/Gemini) o debe
    pedir al usuario un ID explícito (OpenAI-compatible genérico).
    """

    models = _parse_models(kind, payload)
    if not models:
        return None
    if kind == "anthropic":
        principal = max(models, key=_anthropic_principal_key)
        rapido = max(models, key=_anthropic_fast_key)
    elif kind == "vertex":
        principal = max(models, key=_vertex_principal_key)
        rapido = max(models, key=_vertex_fast_key)
    else:
        principal = max(models, key=_openai_principal_key)
        rapido = max(models, key=_openai_fast_key)
    return ModelChoice(principal=principal.model_id, rapido=rapido.model_id)


def discovered_model_ids(kind: ProviderKind, payload: Any) -> list[str]:
    """Devuelve los IDs de chat/tool-use anunciados por el proveedor.

    Conserva el orden del catálogo remoto para que los clientes puedan mostrar
    un selector sin mantener una lista hardcodeada que envejezca. Comparte el
    mismo filtro seguro que ``choose_discovered_models``.
    """

    return [model.model_id for model in _parse_models(kind, payload)]


def _parse_models(kind: ProviderKind, payload: Any) -> list[_Model]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get("models" if kind == "vertex" else "data")
    if not isinstance(raw, list):
        return []

    parsed: list[_Model] = []
    for position, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        raw_id = item.get("name" if kind == "vertex" else "id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            continue
        model_id = raw_id.strip().removeprefix("models/")
        lowered = model_id.lower()
        if any(marker in lowered for marker in _NON_CHAT_MARKERS):
            continue
        if kind == "anthropic" and not lowered.startswith("claude-"):
            continue
        if kind == "vertex":
            methods = item.get("supportedGenerationMethods")
            if isinstance(methods, list) and "generateContent" not in methods:
                continue
            if not lowered.startswith("gemini-"):
                continue
        if kind == "openai_compat" and lowered.startswith(("text-", "omni-")):
            continue
        created_raw = item.get("created") or item.get("created_at") or 0
        try:
            created = int(created_raw)
        except (TypeError, ValueError):
            created = 0
        stable = not any(token in lowered for token in ("preview", "experimental", "exp"))
        parsed.append(_Model(model_id=model_id, created=created, position=position, stable=stable))
    return parsed


def _version(model_id: str) -> tuple[int, ...]:
    # Las fechas snapshot tienen números enormes y no representan capacidad;
    # se quitan antes de comparar la generación del modelo.
    base = _SNAPSHOT_SUFFIX.sub("", model_id.lower())
    return tuple(int(part) for part in _VERSION_PART.findall(base))


def _alias_score(model: _Model) -> int:
    return int(not bool(_SNAPSHOT_SUFFIX.search(model.model_id)))


def _anthropic_family(model_id: str, *, fast: bool) -> int:
    lowered = model_id.lower()
    order = ("haiku", "sonnet", "opus") if fast else ("opus", "sonnet", "haiku")
    for score, family in zip((3, 2, 1), order, strict=True):
        if family in lowered:
            return score
    return 0


def _anthropic_principal_key(model: _Model) -> tuple[Any, ...]:
    return (
        _anthropic_family(model.model_id, fast=False),
        _version(model.model_id),
        model.stable,
        _alias_score(model),
        model.created,
        -model.position,
    )


def _anthropic_fast_key(model: _Model) -> tuple[Any, ...]:
    return (
        _anthropic_family(model.model_id, fast=True),
        _version(model.model_id),
        model.stable,
        _alias_score(model),
        model.created,
        -model.position,
    )


def _openai_general_score(model_id: str) -> int:
    lowered = model_id.lower()
    if not lowered.startswith("gpt-"):
        return 0
    if any(marker in lowered for marker in ("codex", "mini", "nano", "chat")):
        return 1
    # Los modelos "pro" suelen usar contratos distintos a chat/completions;
    # OpenAICompatProvider necesita un modelo general de chat/tool-use.
    if "pro" in lowered:
        return 2
    return 3


def _parameter_billions(model_id: str) -> float:
    """Extrae tamaños como ``8b``/``70b`` sin confundirlos con versiones."""

    match = _PARAMETER_BILLIONS.search(model_id)
    return float(match.group(1)) if match else 0.0


def _generic_fast_score(model_id: str) -> tuple[int, float]:
    lowered = model_id.lower()
    explicit = int(
        any(marker in lowered for marker in ("flash", "instant", "lite", "mini", "small", "turbo"))
    )
    parameters = _parameter_billions(lowered)
    if 7 <= parameters <= 14:
        size_band = 3
    elif 0 < parameters < 7:
        size_band = 2
    elif parameters > 14:
        size_band = 1
    else:
        size_band = 0
    # Dentro de la banda rápida, el menor modelo gana; el marcador explícito
    # prevalece porque endpoints compatibles suelen anunciarlo como intención.
    return explicit * 10 + size_band, -parameters


def _openai_principal_key(model: _Model) -> tuple[Any, ...]:
    return (
        _openai_general_score(model.model_id),
        _parameter_billions(model.model_id),
        model.stable,
        _version(model.model_id),
        _alias_score(model),
        model.created,
        -model.position,
    )


def _openai_fast_key(model: _Model) -> tuple[Any, ...]:
    lowered = model.model_id.lower()
    fast_family = 3 if "mini" in lowered else 2 if "nano" in lowered else 1
    return (
        int(lowered.startswith("gpt-")),
        fast_family if lowered.startswith("gpt-") else _generic_fast_score(lowered),
        model.stable,
        _version(model.model_id),
        _alias_score(model),
        model.created,
        -model.position,
    )


def _vertex_family(model_id: str, *, fast: bool) -> int:
    lowered = model_id.lower()
    if fast:
        if "flash-lite" in lowered:
            return 3
        if "flash" in lowered:
            return 2
        return 1
    if "pro" in lowered:
        return 3
    if "flash" in lowered and "lite" not in lowered:
        return 2
    return 1


def _vertex_principal_key(model: _Model) -> tuple[Any, ...]:
    # Para producción la generación estable más reciente gana primero; dentro
    # de una misma generación se prefiere Pro sobre Flash.
    return (
        model.stable,
        _version(model.model_id),
        _vertex_family(model.model_id, fast=False),
        _alias_score(model),
        -model.position,
    )


def _vertex_fast_key(model: _Model) -> tuple[Any, ...]:
    return (
        model.stable,
        _vertex_family(model.model_id, fast=True),
        _version(model.model_id),
        _alias_score(model),
        -model.position,
    )
