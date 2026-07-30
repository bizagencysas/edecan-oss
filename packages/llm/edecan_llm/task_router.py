"""Automatic task routing for Edecán inference based on config/modelos.yml.

La decisión sigue centralizada aquí: ningún módulo compara ids de modelo. La
diferencia con la versión anterior es que el chat SÍ admite una elección del
usuario, y llega como dato por `metadata["modelo_elegido"]` — se honra solo si
el id está en el catálogo declarado (`modelos_chat` de `config/modelos.yml`), y
si no, se ignora con warning y decide la heurística de siempre. El resto de las
superficies (voz, background, Forge) siguen sin elegir nada.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from .base import CompletionRequest
from .errors import LLMError

logger = logging.getLogger(__name__)

RUTA_CONFIG_MODELOS = Path(__file__).resolve().parents[3] / "config" / "modelos.yml"

METADATA_MODELO_ELEGIDO = "modelo_elegido"
"""Clave de `CompletionRequest.metadata` con el modelo que fijó el usuario.

La pone `edecan_core.agent` a partir de la `SeleccionDeModelo` que le pasa la
API (columnas `conversations.chat_model` o el override del body del turno).
Ausente o `None` = automático.
"""

ESFUERZOS_CHAT: tuple[str, ...] = ("bajo", "medio", "alto")
"""Niveles de Esfuerzo del chat. Espejo del CHECK de `conversations.chat_effort`."""

ESFUERZO_CHAT_POR_DEFECTO = "medio"
"""`medio` == el comportamiento de hoy (`_MAX_TOKENS_POR_ITERACION` = 4096)."""

MODELOS_IDE_FALLBACK: list[dict[str, Any]] = [
    {
        "id": "@cf/zai-org/glm-5.2",
        "nombre": "GLM 5.2",
        "descripcion": "Modelo principal para ingeniería.",
        "insignia": "Principal",
        "contexto_ventana": 262144,
        "capacidades": ["codigo", "razonamiento", "herramientas", "contexto_largo"],
    },
    {
        "id": "@cf/moonshotai/kimi-k2.7-code",
        "nombre": "Kimi K2.7 Code",
        "descripcion": "Especialista de código para refactors y debugging.",
        "insignia": "Código",
        "contexto_ventana": 262144,
        "capacidades": ["codigo", "razonamiento", "herramientas", "vision", "contexto_largo"],
    },
    {
        "id": "@cf/nvidia/nemotron-3-120b-a12b",
        "nombre": "Nemotron 3 120B",
        "descripcion": "Modelo grande para análisis pesado y arquitectura.",
        "insignia": "Pesado",
        "contexto_ventana": 256000,
        "capacidades": ["razonamiento", "herramientas", "contexto_largo"],
    },
    {
        "id": "@cf/openai/gpt-oss-120b",
        "nombre": "GPT-OSS 120B",
        "descripcion": "Modelo abierto grande para análisis general y producto.",
        "insignia": "OSS",
        "contexto_ventana": 128000,
        "capacidades": ["razonamiento", "herramientas"],
    },
    {
        "id": "@cf/meta/llama-4-scout-17b-16e-instruct",
        "nombre": "Llama 4 Scout",
        "descripcion": "Modelo multimodal para visión y revisión de interfaces.",
        "insignia": "Visión",
        "contexto_ventana": 131000,
        "capacidades": ["vision", "herramientas"],
    },
]


# Catálogo del selector del chat. La autoridad declarada es `modelos_chat` de
# `config/modelos.yml` (ahí está la evidencia de cada número); esta constante es
# el fallback para instalaciones que no traigan el archivo, y tiene que quedar
# idéntica. Este módulo es uno de los dos que el test
# `test_no_literal_model_names_in_llm_package` exceptúa precisamente para poder
# escribir estos ids.
MODELOS_CHAT_FALLBACK: list[dict[str, Any]] = [
    {
        "id": "@cf/meta/llama-4-scout-17b-16e-instruct",
        "nombre": "Copla",
        "descripcion": "Ligero y al instante · ve imágenes",
        "orden": 1,
        "principal": True,
        "ve_imagenes": True,
        "soporta_esfuerzo": False,
        "contexto_ventana": 131072,
    },
    {
        "id": "@cf/moonshotai/kimi-k2.7-code",
        "nombre": "Silva",
        "descripcion": "Contexto enorme, fuerte en código",
        "orden": 2,
        "principal": True,
        "ve_imagenes": True,
        "soporta_esfuerzo": True,
        "contexto_ventana": 262144,
    },
    {
        "id": "@cf/google/gemma-4-26b-a4b-it",
        "nombre": "Soneto",
        "descripcion": "Equilibrado y con criterio",
        "orden": 3,
        "principal": True,
        "ve_imagenes": True,
        "soporta_esfuerzo": True,
        "contexto_ventana": 256000,
    },
    {
        "id": "@cf/moonshotai/kimi-k2.6",
        "nombre": "Oda",
        "descripcion": "El más profundo, para lo difícil",
        "orden": 4,
        "principal": True,
        "ve_imagenes": True,
        "soporta_esfuerzo": True,
        "contexto_ventana": 262144,
    },
    {
        "id": "@cf/openai/gpt-oss-20b",
        "nombre": "GPT-OSS 20B",
        "descripcion": "El más veloz del catálogo · No ve imágenes",
        "orden": 5,
        "principal": False,
        "ve_imagenes": False,
        "soporta_esfuerzo": False,
        "contexto_ventana": 128000,
    },
    {
        "id": "@cf/openai/gpt-oss-120b",
        "nombre": "GPT-OSS 120B",
        "descripcion": "Grande y rápido para análisis · No ve imágenes",
        "orden": 6,
        "principal": False,
        "ve_imagenes": False,
        "soporta_esfuerzo": False,
        "contexto_ventana": 128000,
    },
    {
        "id": "@cf/nvidia/nemotron-3-120b-a12b",
        "nombre": "Nemotron 3 120B",
        "descripcion": "Análisis pesado, contexto largo · No ve imágenes",
        "orden": 7,
        "principal": False,
        "ve_imagenes": False,
        "soporta_esfuerzo": False,
        "contexto_ventana": 256000,
    },
]


def cargar_configuracion_modelos(ruta_yaml: Path | str | None = None) -> dict[str, Any]:
    ruta = Path(ruta_yaml) if ruta_yaml else RUTA_CONFIG_MODELOS
    if not ruta.is_file():
        return {}
    try:
        with open(ruta, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def modelo_para_perfil(perfil: str, ruta_yaml: Path | str | None = None) -> str:
    config = cargar_configuracion_modelos(ruta_yaml)
    perfiles = config.get("perfiles") or {}
    if perfil in perfiles and isinstance(perfiles[perfil], dict):
        if m := perfiles[perfil].get("modelo"):
            return str(m)
    if perfil == "ingenieria_software":
        return "@cf/zai-org/glm-5.2"
    return "@cf/zai-org/glm-4.7-flash"


def modelos_ide_disponibles(
    ruta_yaml: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Devuelve las ModelCards configuradas para Forge Studio.

    El código no decide nombres de modelos. La lista vive en
    ``config/modelos.yml`` para que la cuenta pueda agregar o retirar modelos
    de Workers AI sin cambiar módulos de runtime.
    """

    config = cargar_configuracion_modelos(ruta_yaml)
    rows = config.get("modelos_ide")
    if isinstance(rows, list):
        clean: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            model_id = str(row.get("id") or "").strip()
            if not model_id:
                continue
            clean.append({**row, "id": model_id})
        if clean:
            return clean
    return [dict(row) for row in MODELOS_IDE_FALLBACK]


def modelo_ide_permitido(
    model_id: str | None,
    ruta_yaml: Path | str | None = None,
) -> bool:
    if model_id is None:
        return True
    normalized = model_id.strip()
    return any(row["id"] == normalized for row in modelos_ide_disponibles(ruta_yaml))


def modelos_chat_disponibles(
    ruta_yaml: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Devuelve el catálogo del selector del chat, ordenado por `orden`.

    Mismo patrón que `modelos_ide_disponibles`: la lista es DATO
    (`config/modelos.yml` -> `modelos_chat`) para que agregar o retirar un
    modelo del selector no sea un cambio de código ni una migración. La
    constante `MODELOS_CHAT_FALLBACK` solo cubre instalaciones sin el archivo.
    """

    config = cargar_configuracion_modelos(ruta_yaml)
    rows = config.get("modelos_chat")
    clean: list[dict[str, Any]] = []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            model_id = str(row.get("id") or "").strip()
            if not model_id:
                continue
            clean.append(
                {
                    **row,
                    "id": model_id,
                    "nombre": str(row.get("nombre") or model_id),
                    "descripcion": str(row.get("descripcion") or ""),
                    "orden": int(row.get("orden") or 0),
                    "principal": bool(row.get("principal") or False),
                    "ve_imagenes": bool(row.get("ve_imagenes") or False),
                    "soporta_esfuerzo": bool(row.get("soporta_esfuerzo") or False),
                    "contexto_ventana": int(row.get("contexto_ventana") or 0),
                }
            )
    if not clean:
        clean = [dict(row) for row in MODELOS_CHAT_FALLBACK]
    clean.sort(key=lambda row: (not row["principal"], row["orden"]))
    return clean


def modelo_chat_info(
    model_id: str | None,
    ruta_yaml: Path | str | None = None,
) -> dict[str, Any] | None:
    """Ficha del catálogo de un id, o `None` si no está declarado.

    Es lo que consulta la API para saber si un modelo ve imágenes o si le
    corresponde la fila de Esfuerzo, sin comparar ids a mano en ningún lado.
    """

    if model_id is None:
        return None
    normalized = model_id.strip()
    if not normalized:
        return None
    for row in modelos_chat_disponibles(ruta_yaml):
        if row["id"] == normalized:
            return dict(row)
    return None


def modelo_chat_permitido(
    model_id: str | None,
    ruta_yaml: Path | str | None = None,
) -> bool:
    """`None` = automático (siempre válido); un id, solo si está en el catálogo."""

    if model_id is None:
        return True
    return modelo_chat_info(model_id, ruta_yaml) is not None


def modelo_chat_por_defecto(ruta_yaml: Path | str | None = None) -> str:
    """El principal de `orden` más bajo — hoy Copla.

    Coincide a propósito con `MODELO_POR_DEFECTO`/`WORKERS_AI_CHAT_MODEL`: así
    "automático" y el primer modelo del selector son el mismo, y estrenar el
    selector no cambia el comportamiento de ninguna conversación existente.
    """

    catalogo = modelos_chat_disponibles(ruta_yaml)
    principales = [row for row in catalogo if row["principal"]]
    return str((principales or catalogo)[0]["id"])


def modelo_chat_con_vision_por_defecto(ruta_yaml: Path | str | None = None) -> str:
    """Modelo al que degrada un turno con imagen cuando el elegido es ciego.

    Es el primer principal con `ve_imagenes` — determinista, sin evento SSE
    nuevo (agregar un tipo de `AgentEvent` rompería los decoders de las tres
    UIs) y sin tocar la selección persistida: el próximo turno sin imagen
    vuelve al modelo que eligió la persona.
    """

    catalogo = modelos_chat_disponibles(ruta_yaml)
    con_vision = [row for row in catalogo if row["ve_imagenes"] and row["principal"]]
    if not con_vision:
        con_vision = [row for row in catalogo if row["ve_imagenes"]]
    if con_vision:
        return str(con_vision[0]["id"])
    return modelo_chat_por_defecto(ruta_yaml)


class TaskKind(StrEnum):
    CHAT = "chat"
    VOICE = "voice"
    LIGHT_TOOL_CALL = "light_tool_call"
    BACKGROUND = "background"
    ENGINEERING = "engineering"


@dataclass(frozen=True)
class TaskDecision:
    kind: TaskKind
    model: str
    reason: str


class TaskRouter:
    """Classifies inference and chooses its model automatically using config/modelos.yml."""

    def __init__(
        self,
        *,
        chat_model: str | None = None,
        config_path: Path | str | None = None,
    ) -> None:
        self._config_path = config_path
        self._chat_model = chat_model or modelo_para_perfil("chat_rapido", config_path)

    def decide(
        self,
        request: CompletionRequest | None = None,
        *,
        alias: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TaskDecision:
        combined = dict(metadata or {})
        if request is not None:
            combined = {**request.metadata, **combined}

        explicit = str(combined.get("task_type") or "").strip().lower()
        channel = str(combined.get("channel") or "").strip().lower()
        surface = str(combined.get("surface") or "").strip().lower()

        if alias == "ingenieria_software":
            model = modelo_para_perfil("ingenieria_software", self._config_path)
            return TaskDecision(
                kind=TaskKind.ENGINEERING,
                model=model,
                reason="perfil de ingeniería de software (Forge)",
            )

        if explicit in {"ide", "engineering", "code"} or surface in {"ide", "forge"}:
            raise LLMError(
                "La inferencia del IDE pertenece a su runtime de ingeniería separado; "
                "TaskRouter no la envía a Workers AI."
            )

        # Elección del usuario para ESTA conversación (selector del chat). Se
        # honra ANTES de la heurística, pero solo si el id está en el catálogo
        # declarado: la API ya devolvió 422 para uno inválido, así que llegar
        # acá con algo fuera de catálogo significa que el YAML cambió a mitad
        # de vuelo o que alguien inyectó metadata a mano. En los dos casos es
        # mejor decidir automáticamente que hablarle a un modelo que no
        # existe. La decisión sigue viviendo aquí y las tres autoridades
        # viejas quedan intactas como fallback documentado.
        elegido = combined.get(METADATA_MODELO_ELEGIDO)
        if elegido:
            elegido = str(elegido).strip()
            if modelo_chat_permitido(elegido, self._config_path):
                return TaskDecision(
                    kind=self._clasificar(
                        explicit=explicit, channel=channel, alias=alias, request=request
                    )[0],
                    model=elegido,
                    reason="modelo elegido por el usuario para esta conversación",
                )
            logger.warning(
                "Se ignoró un modelo de chat fuera del catálogo declarado; "
                "el enrutado automático decide este turno."
            )

        kind, reason = self._clasificar(
            explicit=explicit, channel=channel, alias=alias, request=request
        )
        model = self._chat_model or modelo_para_perfil("chat_rapido", self._config_path)
        return TaskDecision(kind=kind, model=model, reason=reason)

    def _clasificar(
        self,
        *,
        explicit: str,
        channel: str,
        alias: str | None,
        request: CompletionRequest | None,
    ) -> tuple[TaskKind, str]:
        """Clasifica el turno. Separado de `decide` porque el `kind` se calcula
        igual con modelo elegido o automático: lo que cambia es de dónde sale el
        modelo, no qué clase de trabajo es."""

        if explicit == TaskKind.VOICE or channel in {"voice", "phone", "call"}:
            kind = TaskKind.VOICE
            reason = "canal de voz o llamada"
        elif explicit == TaskKind.LIGHT_TOOL_CALL or (request is not None and request.tools):
            kind = TaskKind.LIGHT_TOOL_CALL
            reason = "turno con herramientas ligeras"
        elif explicit == TaskKind.BACKGROUND or alias in {"principal", "profundo"}:
            kind = TaskKind.BACKGROUND
            reason = "trabajo no interactivo fuera del IDE"
        else:
            kind = TaskKind.CHAT
            reason = "conversación normal"

        return kind, reason
