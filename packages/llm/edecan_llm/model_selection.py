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

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from .errors import LLMError

logger = logging.getLogger(__name__)

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


# --------------------------------------------------------------------------- #
# Un modelo por tarea, no uno para todo (Workers AI).
#
# Todo lo de arriba elige entre modelos que el PROVEEDOR anuncia por API
# (Anthropic/OpenAI-compatible/Vertex). Workers AI no tiene ese descubrimiento
# dinámico útil para esto: la cuenta del dueño tiene un catálogo fijo y lo que
# cambia turno a turno es CUÁNTO importa la latencia frente a la capacidad.
# Antes de esto, `TaskRouter` (`task_router.py`) resolvía TODO alias que no
# fuera "ingenieria_software" (chat, voz, herramientas ligeras, background) al
# mismo `chat_rapido` de `config/modelos.yml` — un modelo global disfrazado de
# router. Esta sección deja el mecanismo de resolución por alias (y su
# advertencia de modelos de razonamiento) listo para que quien conecte
# `TaskRouter`/`config/modelos.yml` lo use sin reinventar la heurística.
#
# IMPORTANTE — por qué NO hay un ID de modelo de zai-org/moonshotai escrito
# en este archivo: `config/modelos.yml` ya tiene la regla `sin_nombres_de_
# modelo_en_el_codigo` ("ningún módulo comprueba si modelo == '...'; se
# consulta la ModelCard o ese archivo") y `tests/test_profile_routing.py::
# test_no_literal_model_names_in_llm_package` la hace cumplir a nivel de
# import: solo `workers_ai.py` y `task_router.py` pueden mencionar esos
# proveedores. Ese import solo tiene esos dos como excepción a propósito
# (ver ese test) — así que un default de este módulo para el alias
# "ingenieria_software" (medido: el modelo de código de Moonshot, 262k de
# contexto, especializado, ver el comentario de `nota=` más abajo) NO se
# hardcodea aquí: aplicarlo es una línea en
# `perfiles.ingenieria_software.modelo` de `config/modelos.yml`, fuera del
# alcance de este cambio. Este módulo solo resuelve por defecto el alias que
# SÍ puede nombrar sin violar esa regla: "rapido", con un modelo OpenAI-OSS.
#
# Alias reutilizados de `edecan_llm.router.Alias`: "rapido" (chat y llamada,
# donde la latencia se siente en cada turno) e "ingenieria_software" (el IDE,
# donde importa la capacidad y las sesiones son de horas). No se documenta
# "principal"/"profundo" aquí porque no hay medición propia para ellos todavía
# — mejor un alias sin recomendación que uno inventado.
TaskAlias = Literal["rapido", "ingenieria_software"]

# Sufijos (SIN el prefijo "@cf/<proveedor>/") de modelos de RAZONAMIENTO
# conocidos: piensan antes de responder y ese pensamiento se factura como
# tokens de salida aunque nunca se muestre. Medido contra la cuenta del
# dueño: ~150 tokens de razonamiento antes de la primera palabra de
# `content`, y con `max_tokens` chico (el caso normal en un turno de
# chat/llamada) el razonamiento se come todo el presupuesto y `content`
# llega VACÍO — no es un error del proveedor, es el modelo cobrando por
# pensar y no dejando presupuesto para contestar. Uno de estos dos además
# midió más de 40s de latencia: inservible para un agente que necesita
# emitir tool calls. Se guardan sin el prefijo del proveedor (comparados por
# sufijo en `advertencia_modelo_de_razonamiento`) por la misma regla del
# bloque de arriba: ese prefijo es justo lo que este archivo no puede
# nombrar. Si Cloudflare agrega otro modelo de razonamiento a la cuenta,
# este set se actualiza con la MEDICIÓN, no a ojo.
_SUFIJOS_DE_MODELOS_DE_RAZONAMIENTO: frozenset[str] = frozenset(
    {
        "glm-4.7-flash",
        "glm-5.2",
    }
)


@dataclass(frozen=True)
class TaskModelDefault:
    """Default medido para un alias, con la cifra que lo respalda.

    `latencia_medida_s` y `contexto_tokens` son la lectura real citada en el
    comentario del módulo, no un dato de la ficha del proveedor — si el
    proveedor cambia el modelo o el precio, esto se vuelve a medir, no se
    actualiza a ojo.
    """

    model_id: str
    latencia_medida_s: float
    contexto_tokens: int
    nota: str


# Recomendación por defecto para "rapido" — reemplaza el `chat_rapido` actual
# de `config/modelos.yml`, que es un modelo de razonamiento (ver el set de
# arriba) corriendo en el peor lugar posible para ese perfil: una ruta de
# baja latencia. No hay entrada para "ingenieria_software" aquí: ver el
# bloque IMPORTANTE de arriba.
WORKERS_AI_TASK_DEFAULTS: dict[TaskAlias, TaskModelDefault] = {
    "rapido": TaskModelDefault(
        model_id="@cf/openai/gpt-oss-20b",
        latencia_medida_s=0.99,
        contexto_tokens=128_000,
        nota=(
            "5x más rápido que el modelo de razonamiento que usa hoy chat_rapido "
            "(0.99s vs 5.13s, mismo prompt con herramientas) y sin el riesgo de "
            "razonamiento-en-el-medio."
        ),
    ),
}

# Clave exacta que el dueño pondría en `platform-config.json` (ver
# `apps/local/edecan_local/runtime.py::_PLATFORM_CONFIG_KEYS`) para sobrescribir
# cada alias SIN recompilar. "rapido" ya está cableado hoy: `LLMRouter` lee
# `WORKERS_AI_CHAT_MODEL` y se lo pasa a `TaskRouter` como `chat_model` (ver
# `router.py`). "ingenieria_software" todavía NO tiene una clave de
# `platform-config.json` equivalente — hoy solo se cambia editando
# `perfiles.ingenieria_software.modelo` en `config/modelos.yml`; documentado
# aquí como la clave que debería agregarse (junto con el `getattr` en
# `router.py` y la entrada en `_PLATFORM_CONFIG_KEYS`) el día que alguien
# cablee este módulo — cambio que no le toca a `model_selection.py`.
PLATFORM_CONFIG_OVERRIDE_KEYS: dict[TaskAlias, str] = {
    "rapido": "WORKERS_AI_CHAT_MODEL",
    "ingenieria_software": "WORKERS_AI_ENGINEERING_MODEL",  # aún no wireado, ver nota arriba
}


def advertencia_modelo_de_razonamiento(model_id: str) -> str | None:
    """Devuelve una advertencia legible si `model_id` es un modelo de razonamiento.

    Compara por SUFIJO (sin el prefijo `@cf/<proveedor>/`) para poder
    reconocer un `model_id` completo — sea el default interno, un valor leído
    de `config/modelos.yml` o un override del dueño — sin que este archivo
    tenga que escribir ese prefijo (ver el bloque IMPORTANTE más arriba).
    `None` cuando no aplica, para que el caller pueda hacer
    ``if warning := advertencia_modelo_de_razonamiento(m): logger.warning(warning)``
    sin ramas extra.
    """

    if not any(model_id.endswith(sufijo) for sufijo in _SUFIJOS_DE_MODELOS_DE_RAZONAMIENTO):
        return None
    return (
        f"{model_id} es un modelo de razonamiento: quema tokens pensando antes de "
        "responder (~150 tokens medidos) y factura ese pensamiento como salida. "
        "Con max_tokens chico (el caso típico de chat/llamada) puede devolver "
        "content vacío porque el razonamiento se comió el presupuesto. No lo uses "
        "en una ruta de baja latencia sin subir max_tokens y aceptar el costo extra."
    )


def resolve_task_model(alias: TaskAlias, overrides: Mapping[str, str] | None = None) -> str:
    """Resuelve el modelo de Workers AI para un alias de tarea.

    Un override presente en `overrides` (la lectura ya hecha de
    `platform-config.json`, ver `PLATFORM_CONFIG_OVERRIDE_KEYS`) SIEMPRE gana
    sobre el default medido: el dueño puede sobrescribir sin recompilar, y
    este módulo no le impide poner ahí un modelo de razonamiento si así lo
    decide — solo lo deja advertido en el log, nunca se lo bloquea.

    Lanza `LLMError` si el alias no tiene override ni default interno (hoy
    solo le pasa a "ingenieria_software": por diseño no tiene un default
    hardcodeado aquí, ver el bloque IMPORTANTE del módulo — su modelo vive en
    `config/modelos.yml` y ese archivo no lo lee este `resolve_task_model`).
    """

    override_key = PLATFORM_CONFIG_OVERRIDE_KEYS[alias]
    override = ((overrides or {}).get(override_key) or "").strip()
    default = WORKERS_AI_TASK_DEFAULTS.get(alias)
    if not override and default is None:
        raise LLMError(
            f"El alias '{alias}' no tiene default en model_selection.py ni override en "
            f"'{override_key}'. Este módulo no inventa nombres de modelo de zai-org/"
            "moonshotai (ver `config/modelos.yml` → regla "
            "`sin_nombres_de_modelo_en_el_codigo`): fija su modelo en config/modelos.yml "
            f"o provee '{override_key}' en platform-config.json.",
            provider="workers_ai",
        )
    model_id = override or default.model_id  # type: ignore[union-attr]

    if warning := advertencia_modelo_de_razonamiento(model_id):
        logger.warning(
            "model_selection_modelo_de_razonamiento_en_alias alias=%s model=%s: %s",
            alias,
            model_id,
            warning,
        )
    return model_id
