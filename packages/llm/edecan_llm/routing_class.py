"""Model routing by work CLASS (FAST/STANDARD/REASONING/CODING/VISION/…).

Two deterministic, LLM-free helpers:

- :func:`clasificar_tarea` maps a step (text + tools + modality) to a
  :class:`RoutingClass`. Pure heuristics: short/format/classification intents
  are FAST, code/diff/tool-heavy work is CODING, images are VISION, computer
  control is COMPUTER_USE, etc. No provider is consulted and nothing is sent
  over the network.
- :func:`elegir_modelo` maps a class to a concrete model id using the DECLARED
  provider capability catalog (``config/modelos.yml`` → ``modelos_ide``/
  ``modelos_chat``, read via ``task_router``). It matches capability tags
  (``codigo``, ``razonamiento``, ``vision``, ``herramientas``…) and never
  hardcodes a single provider or model id — changing the catalog changes the
  result without touching this module.

Where it plugs in (§61): the cheap deterministic steps of an agent loop. A
budget gate, a "clasifica este texto", a quick rewrite or a format/JSON intent
is :data:`RoutingClass.FAST` and should resolve to the fastest model in the
catalog. Steps carrying the real workload (code, deep reasoning, image work)
get their own class. Two notes measured against this repo:

- A pure budget check needs NO LLM at all (it is SQL/arithmetic), which is
  cheaper than any FAST model — see
  ``apps/worker/edecan_worker/budget.py``. So FAST is for steps that still need
  language but not depth, not for steps that need no language.
- ``EMBEDDING`` is recognized by :func:`clasificar_tarea` but
  :func:`elegir_modelo` returns ``None`` for it on purpose: embeddings use a
  dedicated provider (``edecan_core.memory.Embedder``), never a chat model.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from typing import Any


class RoutingClass(StrEnum):
    FAST = "fast"
    STANDARD = "standard"
    REASONING = "reasoning"
    CODING = "coding"
    VISION = "vision"
    COMPUTER_USE = "computer_use"
    VOICE = "voice"
    EMBEDDING = "embedding"
    REVIEW = "review"


# ---------------------------------------------------------------------------
# Clasificación — heurísticas puras, sin LLM.
# ---------------------------------------------------------------------------

_MODALIDADES_EMBEDDING = frozenset({"embedding", "embed", "vectorizar", "vector", "embeddings"})
_MODALIDADES_VOZ = frozenset({"voz", "voice", "audio", "llamada", "call", "phone", "telefono"})
_MODALIDADES_VISION = frozenset({"vision", "imagen", "image", "multimodal", "foto"})

_CODING_KEYWORDS = frozenset(
    {
        "bug",
        "clase",
        "class",
        "codigo",
        "code",
        "commit",
        "compila",
        "compile",
        "database",
        "db",
        "debug",
        "debugging",
        "diff",
        "endpoint",
        "esquema",
        "funcion",
        "function",
        "git",
        "implementa",
        "implementar",
        "javascript",
        "migracion",
        "migration",
        "patch",
        "python",
        "refactor",
        "refactoriza",
        "script",
        "sql",
        "test",
        "typescript",
    }
)
_REVIEW_KEYWORDS = frozenset(
    {
        "audita",
        "auditar",
        "auditoria",
        "critica",
        "criticar",
        "evalua",
        "evaluar",
        "revisa",
        "revisar",
        "review",
        "valida",
        "validar",
        "verifica",
        "verificar",
    }
)
_REASONING_KEYWORDS = frozenset(
    {
        "analiza",
        "analizar",
        "arquitectura",
        "disena",
        "disenar",
        "estrategia",
        "explica",
        "explicar",
        "fundamenta",
        "investiga",
        "investigar",
        "planifica",
        "planificar",
        "por que",
        "razona",
        "razonar",
    }
)
_VISION_KEYWORDS = frozenset(
    {
        "captura",
        "foto",
        "fotos",
        "imagen",
        "imagenes",
        "mira",
        "screenshot",
        "ve",
        "vision",
    }
)
_COMPUTER_KEYWORDS = frozenset(
    {
        "abre",
        "abrir",
        "chrome",
        "clic",
        "click",
        "computadora",
        "escritorio",
        "finder",
        "mac",
        "mouse",
        "ordenador",
        "pantalla",
        "safari",
        "teclado",
    }
)
_FAST_INTENT_KEYWORDS = frozenset(
    {
        "clasifica",
        "clasificar",
        "extrae",
        "extraer",
        "formatea",
        "formatear",
        "ortografia",
        "parafrasea",
        "parafrasear",
        "resume",
        "resumen",
        "resumir",
        "traduce",
        "traducir",
        "traduccion",
    }
)

_FAST_MAX_TOKENS = 12
_TOOL_HEAVY_THRESHOLD = 3


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", without_marks).strip()


def clasificar_tarea(
    text: str,
    tools: Sequence[str] = (),
    modalidad: str = "texto",
) -> RoutingClass:
    """Classifies a step deterministically into a :class:`RoutingClass`.

    ``tools`` is the list of tool NAMES the step may invoke (already filtered
    by the caller); ``modalidad`` is ``"texto"`` (default), ``"voz"``,
    ``"imagen"`` or ``"embedding"``. The returned class is stable for the same
    inputs — no randomness, no LLM.
    """

    modalidad = (modalidad or "texto").strip().lower()
    if modalidad in _MODALIDADES_EMBEDDING:
        return RoutingClass.EMBEDDING
    if modalidad in _MODALIDADES_VOZ:
        return RoutingClass.VOICE
    if modalidad in _MODALIDADES_VISION:
        return RoutingClass.VISION

    tool_names = {str(tool).strip().lower() for tool in tools if str(tool).strip()}
    normalized = _normalize(text)
    tokens = set(normalized.split())

    if "usar_computadora" in tool_names or tokens.intersection(_COMPUTER_KEYWORDS):
        return RoutingClass.COMPUTER_USE

    if tokens.intersection(_CODING_KEYWORDS) or len(tool_names) >= _TOOL_HEAVY_THRESHOLD:
        return RoutingClass.CODING

    if tokens.intersection(_REVIEW_KEYWORDS):
        return RoutingClass.REVIEW

    if tokens.intersection(_REASONING_KEYWORDS):
        return RoutingClass.REASONING

    if tokens.intersection(_VISION_KEYWORDS):
        return RoutingClass.VISION

    if len(tokens) <= _FAST_MAX_TOKENS or tokens.intersection(_FAST_INTENT_KEYWORDS):
        return RoutingClass.FAST

    return RoutingClass.STANDARD


# ---------------------------------------------------------------------------
# Elección de modelo por clase — sobre el catálogo declarado de capacidades.
# ---------------------------------------------------------------------------

# Tags de capacidad requeridos por clase (vocabulario de `modelos.yml`
# `modelos_ide.*.capacidades`: codigo, razonamiento, herramientas, vision,
# contexto_largo). `()` = sin requisito (el orden del catálogo decide).
_REQUIRED_CAPS: dict[RoutingClass, frozenset[str]] = {
    RoutingClass.FAST: frozenset(),
    RoutingClass.STANDARD: frozenset(),
    RoutingClass.REASONING: frozenset({"razonamiento"}),
    RoutingClass.CODING: frozenset({"codigo"}),
    RoutingClass.VISION: frozenset({"vision"}),
    RoutingClass.COMPUTER_USE: frozenset({"herramientas", "vision"}),
    RoutingClass.VOICE: frozenset(),
    RoutingClass.EMBEDDING: frozenset({"embedding"}),
    RoutingClass.REVIEW: frozenset({"razonamiento"}),
}

def _normalizar_tarjeta(tarjeta: Mapping[str, Any]) -> dict[str, Any] | None:
    """Acepta dos formas de tarjeta y las reduce a una sola:

    - ``modelos_ide``: ``{id, capacidades: [tags], ...}``.
    - ``modelos_chat``: ``{id, ve_imagenes: bool, orden: int, principal: bool}``.

    Devuelve ``{id, capacidades: set, orden}`` o ``None`` si la tarjeta no trae
    un ``id`` usable.
    """
    if not isinstance(tarjeta, Mapping):
        return None
    model_id = str(tarjeta.get("id") or "").strip()
    if not model_id:
        return None

    capacidades = set()
    crudas = tarjeta.get("capacidades")
    if isinstance(crudas, (list, tuple, set, frozenset)):
        capacidades.update(str(c) for c in crudas)
    if tarjeta.get("ve_imagenes"):
        capacidades.add("vision")
    # `soporta_esfuerzo` (razonan de verdad) y la insignia "Código" del
    # catálogo IDE se traducen a tags para que el matcher no dependa de un
    # vocabulario único de modelo.
    if tarjeta.get("soporta_esfuerzo"):
        capacidades.add("razonamiento")

    orden_raw = tarjeta.get("orden")
    try:
        orden: int | None = int(orden_raw) if orden_raw is not None else None
    except (TypeError, ValueError):
        orden = None

    return {"id": model_id, "capacidades": capacidades, "orden": orden}


def elegir_modelo(clase: RoutingClass, registry: Iterable[Mapping[str, Any]]) -> str | None:
    """Picks a model id for a routing class from a capability ``registry``.

    ``registry`` is any iterable of model cards (the output of
    ``task_router.modelos_ide_disponibles()`` or ``modelos_chat_disponibles()``,
    or a synthetic list in tests). The first card whose declared capabilities
    cover the class requirements wins; if none does, the first card is the
    fallback (a declared catalog never leaves a class without a model). FAST
    and VOICE prefer the lowest ``orden`` (speed proxy) among candidates.
    Returns ``None`` for an empty registry, and for ``EMBEDDING`` (embeddings
    use a dedicated provider, never a chat model).
    """

    tarjetas = [t for t in (_normalizar_tarjeta(t) for t in registry) if t is not None]
    if not tarjetas:
        return None
    if clase is RoutingClass.EMBEDDING:
        return None

    requeridas = _REQUIRED_CAPS.get(clase, frozenset())
    candidatas = [t for t in tarjetas if requeridas <= t["capacidades"]] or tarjetas

    if clase in (RoutingClass.FAST, RoutingClass.VOICE):
        candidatas.sort(key=lambda t: (t["orden"] is None, t["orden"] or 0))
    return candidatas[0]["id"]


__all__ = ["RoutingClass", "clasificar_tarea", "elegir_modelo"]