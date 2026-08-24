"""Cloudflare Workers AI provider.

Este módulo implementa el contrato generic de ``LLMProvider`` sobre la API de
Cloudflare Workers AI (``POST /accounts/{cuenta}/ai/run/{modelo}``).
Preserva todas las señales necesarias para la medición y la ingeniería de código
(razonamiento separado, caché de prefijo, cómputo de neuronas y tool calls seguros).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field, computed_field

from .base import (
    ChatMessage,
    CompletionRequest,
    CompletionResponse,
    LLMProvider,
    StreamChunk,
    ToolCall,
    ToolSpec,
    Usage,
)
from .errors import LLMError, ProviderDownError, RateLimitedError
from .multimodal import image_source, text_blocks

logger = logging.getLogger(__name__)

__all__ = [
    "BASE_URL_WORKERS_AI",
    "CredencialInvalidaError",
    "FalloTransitorioError",
    "LimiteDeTasaError",
    "MODELO_POR_DEFECTO",
    "MODELO_IDE_VISION_POR_DEFECTO",
    "PeticionInvalidaError",
    "PresupuestoAgotadoError",
    "ProbeCompletionResponse",
    "ProbeStreamChunk",
    "ProveedorInalcanzableError",
    "SmokeResult",
    "TiempoAgotadoError",
    "WorkersAIProvider",
    "cargar_dotenv",
    "construir_solicitud_estable",
    "herramienta_a_workers_ai",
    "herramienta_desde_workers_ai",
    "mensajes_a_workers_ai",
    "tool_call_a_workers_ai",
    "tool_call_desde_workers_ai",
]

# --------------------------------------------------------------------------- #
# Constantes
# --------------------------------------------------------------------------- #

BASE_URL_WORKERS_AI = "https://api.cloudflare.com/client/v4"

# Chat. Medido el 29-07-2026 contra esta cuenta, mismas herramientas, 3 corridas por caso:
#
#                        crear un post      contestar una pregunta     charla suelta
#   llama-4-scout        3/3   4.04s        3/3   3.74s                3/3   0.99s
#   glm-4.7-flash        3/3  12.38s        1/3   4.49s                3/3   5.14s (peor: 34.25s)
#
# La columna del medio es la que decide: contestar una pregunta con la herramienta correcta.
# glm-4.7-flash acierta 1 de cada 3 veces, y es un modelo de RAZONAMIENTO (quema ~150 tokens
# pensando antes de la primera palabra, y con `max_tokens` chico devuelve `content` vacío).
# En un turno interactivo eso se ve como una pausa; en una llamada telefónica se oye.
MODELO_POR_DEFECTO = "@cf/meta/llama-4-scout-17b-16e-instruct"

MODELO_IDE_POR_DEFECTO = "@cf/zai-org/glm-5.2"
MODELO_IDE_VISION_POR_DEFECTO = "@cf/moonshotai/kimi-k2.7-code"

RUTA_ENV_POR_DEFECTO = Path(__file__).resolve().parents[3] / ".env"
TIMEOUT_POR_DEFECTO = 120.0
DEADLINE_POR_DEFECTO = 300.0
MAX_INTENTOS = 4

MAX_TOKENS_SMOKE = 256
PROMPT_SMOKE_SISTEMA = "Eres un verificador de conectividad. Responde en español, sin preámbulo."
PROMPT_SMOKE_USUARIO = (
    "Escribe exactamente dos frases. La primera confirma que estás respondiendo. "
    "La segunda dice qué modelo eres."
)

_MAPA_FINISH: dict[str, str] = {
    "stop": "end",
    "tool_calls": "tool_use",
    "length": "max_tokens",
}

_MAX_DETALLE_ERROR = 600


# --------------------------------------------------------------------------- #
# Orden Estable de Prompt
# --------------------------------------------------------------------------- #


def construir_solicitud_estable(
    *,
    model: str,
    system: str | None = None,
    tools: list[ToolSpec] | None = None,
    contexto_estable: str | None = None,
    historial: list[ChatMessage] | None = None,
    turno_actual: ChatMessage | str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.7,
) -> CompletionRequest:
    """Construye un CompletionRequest en el orden canónico estricto:
    Sistema -> Herramientas -> Contexto Estable -> Historial -> Turno Actual.
    Garantiza estabilidad de prefijo byte a byte entre turnos consecutivos para
    maximizar la tasa de acierto de la caché de Cloudflare (5x más económico).
    """
    mensajes: list[ChatMessage] = []

    if contexto_estable:
        mensajes.append(ChatMessage(role="system", content=contexto_estable))

    if historial:
        mensajes.extend(historial)

    if turno_actual:
        if isinstance(turno_actual, str):
            mensajes.append(ChatMessage(role="user", content=turno_actual))
        else:
            mensajes.append(turno_actual)

    herramientas_ordenadas = sorted(tools or [], key=lambda t: t.name)

    return CompletionRequest(
        model=model,
        system=system,
        messages=mensajes,
        tools=herramientas_ordenadas,
        max_tokens=max_tokens,
        temperature=temperature,
    )


# --------------------------------------------------------------------------- #
# Errores tipados
# --------------------------------------------------------------------------- #


class CredencialInvalidaError(LLMError):
    """401/403: el token no vale, no tiene permiso, o el modelo no está en la cuenta."""


class PeticionInvalidaError(LLMError):
    """400/404/422: la petición está mal formada."""


class LimiteDeTasaError(RateLimitedError):
    """429 tras agotar los reintentos."""

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        status_code: int | None = None,
        retry_after: float | None = None,
        intentos: int = 0,
    ) -> None:
        super().__init__(message, provider=provider, status_code=status_code)
        self.retry_after = retry_after
        self.intentos = intentos


class FalloTransitorioError(RateLimitedError):
    """5xx tras agotar los reintentos. El proveedor respondió, pero mal."""

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        status_code: int | None = None,
        intentos: int = 0,
    ) -> None:
        super().__init__(message, provider=provider, status_code=status_code)
        self.intentos = intentos


class TiempoAgotadoError(ProviderDownError):
    """La petición HTTP no respondió dentro de su timeout."""


class PresupuestoAgotadoError(TiempoAgotadoError):
    """Se acabó el deadline absoluto de la llamada."""


class ProveedorInalcanzableError(ProviderDownError):
    """No se pudo abrir la conexión (DNS, TLS, red caída)."""


# --------------------------------------------------------------------------- #
# Respuestas enriquecidas
# --------------------------------------------------------------------------- #


class ProbeCompletionResponse(CompletionResponse):
    """CompletionResponse enriquecida con métricas de costo, razonamiento y caché."""

    cached_tokens: int | None = None
    reasoning_content: str = ""
    reasoning_tokens: int | None = None
    neurons: float | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def reasoning_tokens_estimados(self) -> int | None:
        if self.reasoning_tokens is not None:
            return self.reasoning_tokens
        if not self.reasoning_content:
            return None
        salida = self.usage.output_tokens
        if salida <= 0:
            return None
        total_caracteres = len(self.reasoning_content) + len(self.text or "")
        if total_caracteres == 0:
            return None
        proporcion = len(self.reasoning_content) / total_caracteres
        return round(salida * proporcion)

    intentos: int = 1
    latencia_s: float = 0.0
    raw_usage: dict[str, Any] = Field(default_factory=dict)
    tool_calls_crudos: list[dict[str, Any]] = Field(default_factory=list)


class ProbeStreamChunk(StreamChunk):
    """StreamChunk con métricas detalladas y razonamiento."""

    reasoning_text: str | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None
    neurons: float | None = None


class SmokeResult(BaseModel):
    """Resultado de smoke check."""

    ok: bool
    proveedor: str
    modelo: str
    latencia_s: float
    texto: str = ""
    reasoning_content: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cached_tokens: int | None = None
    neurons: float | None = None
    error: str | None = None


# --------------------------------------------------------------------------- #
# Entorno y traducción
# --------------------------------------------------------------------------- #


def cargar_dotenv(ruta: Path | str = RUTA_ENV_POR_DEFECTO) -> dict[str, str]:
    valores: dict[str, str] = {}
    archivo = Path(ruta)
    if not archivo.is_file():
        return valores
    for linea in archivo.read_text(encoding="utf-8", errors="replace").splitlines():
        limpia = linea.strip()
        if not limpia or limpia.startswith("#") or "=" not in limpia:
            continue
        clave, _, valor = limpia.partition("=")
        clave = clave.removeprefix("export ").strip()
        valor = valor.strip()
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
            valor = valor[1:-1]
        if clave:
            valores[clave] = valor
    return valores


def _valor_entorno(clave: str, respaldo: dict[str, str]) -> str:
    return (os.environ.get(clave) or respaldo.get(clave) or "").strip()


def herramienta_a_workers_ai(spec: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.input_schema,
        },
    }


def herramienta_desde_workers_ai(datos: dict[str, Any]) -> ToolSpec:
    funcion = datos.get("function") or datos
    return ToolSpec(
        name=str(funcion.get("name") or ""),
        description=str(funcion.get("description") or ""),
        input_schema=dict(funcion.get("parameters") or {}),
    )


def tool_call_a_workers_ai(llamada: ToolCall) -> dict[str, Any]:
    return {
        "id": llamada.id,
        "type": "function",
        "function": {
            "name": llamada.name,
            "arguments": json.dumps(llamada.arguments, ensure_ascii=False),
        },
    }


def tool_call_desde_workers_ai(datos: dict[str, Any]) -> ToolCall:
    # El esquema nativo de `/ai/run` (Llama 4 Scout y otros) pone `name` y
    # `arguments` en la raíz. El OpenAI-compatible los anida en `function`.
    funcion = datos.get("function")
    if not isinstance(funcion, dict):
        funcion = datos
    return ToolCall(
        id=str(datos.get("id") or uuid4()),
        name=str(funcion.get("name") or ""),
        arguments=_argumentos(funcion.get("arguments") or funcion.get("parameters")),
    )


def _argumentos(valor: object) -> dict[str, Any]:
    if isinstance(valor, dict):
        return valor
    if isinstance(valor, str) and valor:
        try:
            analizado = json.loads(valor)
        except json.JSONDecodeError:
            return {}
        return analizado if isinstance(analizado, dict) else {}
    return {}


def _trozos_de_escritura(texto: str, palabras: int = 4) -> list[str]:
    """Parte una respuesta ya completa para que el chat la vaya escribiendo."""
    if not texto:
        return []
    partes = re.findall(r"\S+\s*", texto)
    if not partes:
        return [texto]
    return ["".join(partes[i : i + palabras]) for i in range(0, len(partes), max(1, palabras))]


def _ultimo_mensaje_pide_rescate_sin_tools(req: CompletionRequest) -> bool:
    """Scout deja el SSE vacío tras tools, y también tras una foto de la Mac.

    El stream nativo (sin tools) sí escribe. Si el último turno es la captura
    (role=user con imagen), hay que rescatar igual: si no, Scout inventa un
    escritorio genérico porque 'vio' el texto 'así se ve la Mac' sin píxeles.
    """
    if not req.tools or not req.messages:
        return False
    ultimo = req.messages[-1]
    if ultimo.role == "tool":
        return True
    if ultimo.role == "user" and isinstance(ultimo.content, list):
        return any(
            isinstance(bloque, dict) and bloque.get("type") == "image"
            for bloque in ultimo.content
        )
    return False


def _max_tokens_tras_desborde(detalle: str, pedido: int) -> int | None:
    """Si Cloudflare recorta por ventana (~24k), pide menos salida."""
    ventana = re.search(r"maximum context length is (\d+)", detalle, re.I)
    entrada = re.search(r"prompt contains at least (\d+)", detalle, re.I)
    if not ventana or not entrada:
        return None
    cabida = int(ventana.group(1)) - int(entrada.group(1)) - 64
    nuevo = min(pedido, max(256, cabida))
    return nuevo if nuevo < pedido else None


def mensajes_a_workers_ai(req: CompletionRequest) -> list[dict[str, Any]]:
    mensajes: list[dict[str, Any]] = []
    if req.system:
        mensajes.append({"role": "system", "content": req.system})
    acepta_imagenes = _modelo_acepta_imagenes(req.model)
    for mensaje in req.messages:
        mensajes.extend(_traducir_mensaje(mensaje, acepta_imagenes=acepta_imagenes))
    return mensajes


def _modelo_acepta_imagenes(modelo: str) -> bool:
    """Lee `ve_imagenes` del catálogo. Sin id conocido, no asume visión.

    Llama 3.3 (Copla) declara `content: string`. Mandarle un array — aunque
    sea solo `[{type:text}]` — es HTTP 400 code=5006. Scout sí acepta array
    porque es multimodal. El dato vive en `modelos.yml`, no en un `if modelo`.
    """
    if not modelo:
        return False
    try:
        from .task_router import modelos_chat_disponibles, modelos_ide_disponibles
    except Exception:  # noqa: BLE001 - el catálogo no puede tumbar una llamada
        return False
    for fila in (*modelos_chat_disponibles(), *modelos_ide_disponibles()):
        if fila.get("id") == modelo:
            if fila.get("ve_imagenes"):
                return True
            caps = fila.get("capacidades") or []
            return "vision" in caps
    return False


def _traducir_mensaje(
    mensaje: ChatMessage, *, acepta_imagenes: bool = False
) -> list[dict[str, Any]]:
    if mensaje.role == "tool":
        return _resultados_de_herramienta(mensaje.content)
    if mensaje.role == "system":
        return [{"role": "system", "content": text_blocks(mensaje.content)}]
    if mensaje.role == "assistant" and isinstance(mensaje.content, list):
        return [_bloques_assistant(mensaje.content)]
    if mensaje.role == "user" and isinstance(mensaje.content, list):
        return [
            {
                "role": "user",
                "content": _contenido_usuario(mensaje.content, acepta_imagenes=acepta_imagenes),
            }
        ]
    contenido = (
        mensaje.content if isinstance(mensaje.content, str) else text_blocks(mensaje.content)
    )
    return [{"role": mensaje.role, "content": contenido}]


def _contenido_usuario(
    bloques: list[dict[str, Any]], *, acepta_imagenes: bool
) -> str | list[dict[str, Any]]:
    """Array solo si hay imagen Y el modelo las ve. Si no, string.

    El esquema nativo de `/ai/run` para Llama 3 es `content: string`. Un
    array de partes de texto — el formato multimodal — lo tumba con 400.
    """
    partes_imagen: list[dict[str, Any]] = []
    if acepta_imagenes:
        for bloque in bloques:
            fuente = image_source(bloque)
            if fuente is None:
                continue
            mime, datos = fuente
            partes_imagen.append(
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{datos}"}}
            )
    texto = text_blocks(bloques)
    if not partes_imagen:
        return texto
    contenido: list[dict[str, Any]] = []
    if texto:
        contenido.append({"type": "text", "text": texto})
    contenido.extend(partes_imagen)
    return contenido


def _bloques_assistant(bloques: list[dict[str, Any]]) -> dict[str, Any]:
    texto = "".join(str(b.get("text") or "") for b in bloques if b.get("type") == "text")
    usos = [b for b in bloques if b.get("type") == "tool_use"]
    # `content: null` también es 400 en Llama 3 (`'string' not in 'null'`).
    entrada: dict[str, Any] = {"role": "assistant", "content": texto}
    if usos:
        entrada["tool_calls"] = [
            tool_call_a_workers_ai(
                ToolCall(
                    id=str(b.get("id") or uuid4()),
                    name=str(b.get("name") or ""),
                    arguments=dict(b.get("input") or {}),
                )
            )
            for b in usos
        ]
    return entrada


def _resultados_de_herramienta(contenido: str | list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(contenido, str):
        return [{"role": "tool", "tool_call_id": "", "content": contenido}]
    if not contenido:
        return [{"role": "tool", "tool_call_id": "", "content": ""}]
    mensajes: list[dict[str, Any]] = []
    for bloque in contenido:
        identificador = (
            bloque.get("tool_use_id") or bloque.get("tool_call_id") or bloque.get("id") or ""
        )
        interno = bloque.get("content", "")
        texto = interno if isinstance(interno, str) else text_blocks(interno)
        mensajes.append({"role": "tool", "tool_call_id": str(identificador), "content": texto})
    return mensajes


def _desenvolver(payload: dict[str, Any]) -> dict[str, Any]:
    resultado = payload.get("result")
    if isinstance(resultado, dict) and ("choices" in resultado or "response" in resultado):
        return resultado
    return payload


def _entero(valor: object) -> int | None:
    if isinstance(valor, bool) or valor is None:
        return None
    if isinstance(valor, int | float):
        return int(valor)
    return None


def _flotante(valor: object) -> float | None:
    if isinstance(valor, bool) or valor is None:
        return None
    if isinstance(valor, int | float):
        return float(valor)
    return None


def _texto_de_contenido(valor: object) -> str:
    """Normaliza `content` / `response` de Workers AI a un string.

    El esquema nativo de `/ai/run` (documentado para Scout) manda el texto en
    `response`. Llama 4 a veces manda `content` como lista de partes
    `{"type": "text", "text": "..."}` en vez de un string. Si se deja la lista
    cruda, Pydantic rechaza el chunk y el stream muere; si se ignora
    `response` porque no hay `choices`, el modelo genera tokens que nunca
    llegan al agente.
    """
    if valor is None:
        return ""
    if isinstance(valor, str):
        return valor
    if isinstance(valor, list):
        partes: list[str] = []
        for item in valor:
            if isinstance(item, str) and item:
                partes.append(item)
            elif isinstance(item, dict):
                texto = item.get("text") or item.get("content") or ""
                if texto:
                    partes.append(str(texto))
        return "".join(partes)
    return ""


def _acumular_tool_call_delta(
    pendientes: dict[int, dict[str, Any]],
    parcial: dict[str, Any],
    indice_default: int = 0,
) -> None:
    """Acumula un delta OpenAI o una tool call nativa `{name, arguments}`."""
    indice = parcial.get("index", indice_default)
    try:
        indice = int(indice)
    except (TypeError, ValueError):
        indice = indice_default
    entrada = pendientes.setdefault(indice, {"id": "", "name": "", "partes": []})
    if parcial.get("id"):
        entrada["id"] = parcial["id"]
    funcion = parcial.get("function") if isinstance(parcial.get("function"), dict) else {}
    nombre = funcion.get("name") or parcial.get("name")
    if nombre:
        entrada["name"] = nombre
    argumentos = (
        funcion.get("arguments")
        if funcion.get("arguments") not in (None, "")
        else parcial.get("arguments") or parcial.get("parameters")
    )
    if argumentos in (None, ""):
        return
    if isinstance(argumentos, dict):
        entrada["partes"].append(json.dumps(argumentos, ensure_ascii=False))
    else:
        entrada["partes"].append(argumentos)


def _parsear_respuesta(
    datos: dict[str, Any], *, intentos: int, latencia_s: float
) -> ProbeCompletionResponse:
    opciones = datos.get("choices") or []
    opcion = opciones[0] if opciones and isinstance(opciones[0], dict) else {}
    mensaje = opcion.get("message") or {}

    contenido = _texto_de_contenido(mensaje.get("content"))
    if not contenido:
        contenido = _texto_de_contenido(datos.get("response"))
    razonamiento = _texto_de_contenido(
        mensaje.get("reasoning_content") or datos.get("reasoning_content")
    )
    crudos_fuente = mensaje.get("tool_calls") or datos.get("tool_calls") or []
    crudos = [tc for tc in crudos_fuente if isinstance(tc, dict)]
    llamadas = [tool_call_desde_workers_ai(tc) for tc in crudos]
    if not contenido and not llamadas and razonamiento:
        # Scout a veces deja el texto útil solo en reasoning_content.
        contenido = razonamiento

    uso = datos.get("usage") or {}
    detalle_entrada = uso.get("prompt_tokens_details") or {}
    detalle_salida = uso.get("completion_tokens_details") or {}

    razon = _MAPA_FINISH.get(str(opcion.get("finish_reason") or ""), "end")
    if llamadas and razon == "end":
        razon = "tool_use"

    return ProbeCompletionResponse(
        text=contenido,
        tool_calls=llamadas,
        usage=Usage(
            input_tokens=_entero(uso.get("prompt_tokens")) or 0,
            output_tokens=_entero(uso.get("completion_tokens")) or 0,
        ),
        stop_reason=razon,  # type: ignore[arg-type]
        cached_tokens=_entero(detalle_entrada.get("cached_tokens")),
        reasoning_content=razonamiento,
        reasoning_tokens=_entero(detalle_salida.get("reasoning_tokens")),
        neurons=_flotante(uso.get("neurons")),
        intentos=intentos,
        latencia_s=latencia_s,
        raw_usage=dict(uso),
        tool_calls_crudos=crudos,
    )


# --------------------------------------------------------------------------- #
# Workers AI Provider Class
# --------------------------------------------------------------------------- #


class WorkersAIProvider(LLMProvider):
    """Proveedor Cloudflare Workers AI directo."""

    name = "workers_ai"

    def __init__(
        self,
        *,
        account_id: str | None = None,
        api_token: str | None = None,
        model: str | None = None,
        base_url: str = BASE_URL_WORKERS_AI,
        timeout: float = TIMEOUT_POR_DEFECTO,
        deadline_s: float = DEADLINE_POR_DEFECTO,
        max_intentos: int = MAX_INTENTOS,
        max_retries: int | None = None,
        backoff_base_s: float = 0.5,
        backoff_max_s: float = 20.0,
        http_client: httpx.AsyncClient | None = None,
        env_file: Path | str | None = RUTA_ENV_POR_DEFECTO,
        rng: random.Random | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        respaldo = cargar_dotenv(env_file) if env_file is not None else {}
        self.account_id = account_id or _valor_entorno("CLOUDFLARE_ACCOUNT_ID", respaldo)
        # `FORGE_PROBE_MODEL` estuvo en esta cadena y NO debe volver: es la variable del banco
        # de pruebas del IDE (forge-probe), donde se apunta a propósito a modelos lentos y
        # potentes para medirlos. Como `WORKERS_AI_CHAT_MODEL` no suele estar puesta, el chat
        # terminaba heredando ese apuntador en silencio: con `FORGE_PROBE_MODEL=@cf/zai-org/
        # glm-5.2` (42 s por vuelta del ciclo agente↔herramientas) un turno de 8 vueltas se iba
        # a más de cinco minutos girando, que es justo lo que el dueño reportó.
        #
        # La lección es más general que el modelo: una variable que existe para MEDIR nunca
        # debe poder decidir lo que el usuario EJECUTA. Si el chat necesita otro modelo, se
        # dice explícitamente con `WORKERS_AI_CHAT_MODEL`.
        self.model = (
            model
            or _valor_entorno("WORKERS_AI_CHAT_MODEL", respaldo)
            or MODELO_POR_DEFECTO
        )
        self._api_token = api_token or _valor_entorno("CLOUDFLARE_API_TOKEN", respaldo)
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._deadline_s = deadline_s
        effective_max = max_retries + 1 if max_retries is not None else max_intentos
        self._max_intentos = max(1, effective_max)
        self._backoff_base_s = max(0.0, backoff_base_s)
        self._backoff_max_s = max(0.0, backoff_max_s)
        self._rng = rng or random.Random()
        self._dormir = sleeper or asyncio.sleep
        self._propio = http_client is None
        self._client = http_client or httpx.AsyncClient(timeout=timeout)

    def __repr__(self) -> str:
        credencial = "sí" if self.account_id and self._api_token else "no"
        return f"WorkersAIProvider(model={self.model!r}, credencial={credencial})"

    async def aclose(self) -> None:
        if self._propio:
            await self._client.aclose()

    def _url_para_modelo(self, modelo: str) -> str:
        m = modelo or self.model
        return f"{self._base_url}/accounts/{self.account_id}/ai/run/{m}"

    def _headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self._api_token}",
            "content-type": "application/json",
        }

    def _verificar_credencial(self) -> None:
        faltan = [
            nombre
            for nombre, valor in (
                ("CLOUDFLARE_ACCOUNT_ID", self.account_id),
                ("CLOUDFLARE_API_TOKEN", self._api_token),
            )
            if not valor
        ]
        if faltan:
            raise CredencialInvalidaError(
                f"Faltan credenciales de Workers AI: {', '.join(faltan)}", provider=self.name
            )

    def _redactar(self, texto: str) -> str:
        if self._api_token and self._api_token in texto:
            return texto.replace(self._api_token, "***")
        return texto

    def _presupuesto(self, req: CompletionRequest) -> float:
        valor = req.metadata.get("deadline_s")
        try:
            return max(0.0, float(valor)) if valor is not None else self._deadline_s
        except (TypeError, ValueError):
            return self._deadline_s

    def _build_body(self, req: CompletionRequest, *, stream: bool) -> dict[str, Any]:
        cuerpo: dict[str, Any] = {
            "messages": mensajes_a_workers_ai(req),
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
            "stream": stream,
        }
        if stream:
            cuerpo["stream_options"] = {"include_usage": True}
        if req.tools:
            cuerpo["tools"] = [herramienta_a_workers_ai(t) for t in req.tools]
            if (eleccion := req.metadata.get("tool_choice")) is not None:
                cuerpo["tool_choice"] = eleccion
        # ``edecan_core`` habla con los proveedores por contrato estructural.
        # Mantener ``getattr`` aquí permite que adaptadores externos antiguos,
        # que todavía no exponen ``reasoning_effort``, sigan siendo válidos.
        esfuerzo = req.metadata.get("reasoning_effort") or getattr(
            req, "reasoning_effort", None
        )
        if esfuerzo is not None:
            cuerpo["reasoning_effort"] = str(esfuerzo)
        if isinstance(extra := req.metadata.get("extra_body"), dict):
            cuerpo.update(extra)
        return cuerpo

    def _espera(self, intento: int, respuesta: httpx.Response | None) -> float:
        techo = min(self._backoff_base_s * (2 ** (intento - 1)), self._backoff_max_s)
        espera = techo * (0.5 + 0.5 * self._rng.random())
        if respuesta is not None and (cabecera := respuesta.headers.get("retry-after")):
            try:
                espera = max(espera, float(cabecera))
            except ValueError:
                pass
        return espera

    @staticmethod
    def _retry_after(respuesta: httpx.Response) -> float | None:
        cabecera = respuesta.headers.get("retry-after")
        if not cabecera:
            return None
        try:
            return float(cabecera)
        except ValueError:
            return None

    def _codigos_cloudflare(self, cuerpo: str) -> str:
        try:
            datos = json.loads(cuerpo)
        except json.JSONDecodeError:
            return ""
        errores = datos.get("errors") if isinstance(datos, dict) else None
        if not isinstance(errores, list):
            return ""
        codigos = [str(e.get("code")) for e in errores if isinstance(e, dict) and e.get("code")]
        return ",".join(codigos)

    def _error_http(self, estado: int, cuerpo: str) -> LLMError:
        detalle = self._redactar(cuerpo)[:_MAX_DETALLE_ERROR]
        codigos = self._codigos_cloudflare(cuerpo)
        sufijo = f" (code={codigos})" if codigos else ""
        mensaje = f"Workers AI devolvió {estado}{sufijo}: {detalle}"
        if estado in (401, 403):
            return CredencialInvalidaError(mensaje, provider=self.name, status_code=estado)
        if estado in (400, 404, 422):
            return PeticionInvalidaError(mensaje, provider=self.name, status_code=estado)
        return LLMError(mensaje, provider=self.name, status_code=estado)

    def _error_agotado(self, respuesta: httpx.Response, cuerpo: str, intentos: int) -> LLMError:
        detalle = self._redactar(cuerpo)[:_MAX_DETALLE_ERROR]
        estado = respuesta.status_code
        if estado == 429:
            return LimiteDeTasaError(
                f"Workers AI devolvió 429 tras {intentos} intento(s): {detalle}",
                provider=self.name,
                status_code=estado,
                retry_after=self._retry_after(respuesta),
                intentos=intentos,
            )
        return FalloTransitorioError(
            f"Workers AI devolvió {estado} tras {intentos} intento(s): {detalle}",
            provider=self.name,
            status_code=estado,
            intentos=intentos,
        )

    def _verificar_sobre(self, payload: dict[str, Any]) -> None:
        if payload.get("success") is False:
            errores = self._redactar(json.dumps(payload.get("errors") or []))
            raise PeticionInvalidaError(
                f"Workers AI respondió success=false: {errores[:_MAX_DETALLE_ERROR]}",
                provider=self.name,
            )

    def _restante(self, limite: float) -> float:
        restante = limite - time.monotonic()
        if restante <= 0:
            raise PresupuestoAgotadoError(
                f"Se agotó el presupuesto de la llamada a {self.model}", provider=self.name
            )
        return restante

    async def _esperar_o_rendirse(self, espera: float, limite: float, intento: int) -> None:
        if time.monotonic() + espera > limite:
            raise PresupuestoAgotadoError(
                f"El reintento {intento + 1} no cabe en el presupuesto de la "
                f"llamada a {self.model}",
                provider=self.name,
            )
        await self._dormir(espera)

    async def complete(self, req: CompletionRequest) -> ProbeCompletionResponse:
        self._verificar_credencial()
        modelo = req.model or self.model
        cuerpo = self._build_body(req, stream=False)
        inicio = time.monotonic()
        limite = inicio + self._presupuesto(req)
        try:
            respuesta, intentos = await self._post_con_reintento(modelo, cuerpo, limite)
        except PeticionInvalidaError as exc:
            recorte = _max_tokens_tras_desborde(str(exc), req.max_tokens)
            if recorte is None:
                raise
            logger.warning(
                "Workers AI 400 por contexto; reintento max_tokens=%s→%s",
                req.max_tokens,
                recorte,
            )
            req = req.model_copy(update={"max_tokens": recorte})
            cuerpo = self._build_body(req, stream=False)
            respuesta, intentos = await self._post_con_reintento(modelo, cuerpo, limite)
        try:
            payload = respuesta.json()
        except ValueError as exc:
            raise PeticionInvalidaError(
                f"Workers AI devolvió un cuerpo que no es JSON: "
                f"{self._redactar(respuesta.text)[:_MAX_DETALLE_ERROR]}",
                provider=self.name,
                status_code=respuesta.status_code,
            ) from exc
        if not isinstance(payload, dict):
            raise PeticionInvalidaError(
                "Workers AI devolvió un JSON que no es un objeto", provider=self.name
            )
        self._verificar_sobre(payload)
        res = _parsear_respuesta(
            _desenvolver(payload), intentos=intentos, latencia_s=time.monotonic() - inicio
        )

        # Reintento de razonamiento si la respuesta vuelve vacía por max_tokens.
        # Crece por el MAYOR entre duplicar y sumar 512: desde un presupuesto
        # grande (el loop del agente pide 4096) sumar 512 se volvía a quedar
        # corto y el único reintento se gastaba sin arreglar nada, pero desde uno
        # chico duplicar da menos que ese piso fijo. El tope de 16384 lo impone
        # la guarda de arriba.
        if not res.text and res.stop_reason == "max_tokens" and req.max_tokens < 16384:
            ampliado = min(max(req.max_tokens * 2, req.max_tokens + 512), 16384)
            nuevo_req = req.model_copy(update={"max_tokens": ampliado})
            cuerpo_reintento = self._build_body(nuevo_req, stream=False)
            respuesta_r, intentos_r = await self._post_con_reintento(
                modelo, cuerpo_reintento, limite
            )
            payload_r = respuesta_r.json()
            if isinstance(payload_r, dict):
                self._verificar_sobre(payload_r)
                return _parsear_respuesta(
                    _desenvolver(payload_r),
                    intentos=intentos + intentos_r,
                    latencia_s=time.monotonic() - inicio,
                )

        return res

    async def _post_con_reintento(
        self, modelo: str, cuerpo: dict[str, Any], limite: float
    ) -> tuple[httpx.Response, int]:
        intento = 0
        while True:
            intento += 1
            restante = self._restante(limite)
            try:
                respuesta = await self._client.post(
                    self._url_para_modelo(modelo),
                    json=cuerpo,
                    headers=self._headers(),
                    timeout=min(self._timeout, restante),
                )
            except httpx.TimeoutException as exc:
                raise TiempoAgotadoError(
                    f"Workers AI no respondió en {min(self._timeout, restante):.1f}s",
                    provider=self.name,
                ) from exc
            except httpx.TransportError as exc:
                raise ProveedorInalcanzableError(
                    f"No se pudo conectar con Workers AI: {self._redactar(str(exc))}",
                    provider=self.name,
                ) from exc

            if respuesta.status_code == 429 or respuesta.status_code >= 500:
                if intento >= self._max_intentos:
                    raise self._error_agotado(respuesta, respuesta.text, intento)
                await self._esperar_o_rendirse(self._espera(intento, respuesta), limite, intento)
                continue
            if respuesta.status_code >= 400:
                raise self._error_http(respuesta.status_code, respuesta.text)
            return respuesta, intento

    async def stream(self, req: CompletionRequest) -> AsyncIterator[ProbeStreamChunk]:
        self._verificar_credencial()
        modelo = req.model or self.model
        cuerpo = self._build_body(req, stream=True)
        limite = time.monotonic() + self._presupuesto(req)
        intento = 0
        while True:
            intento += 1
            restante = self._restante(limite)
            espera: float | None = None
            try:
                async with self._client.stream(
                    "POST",
                    self._url_para_modelo(modelo),
                    json=cuerpo,
                    headers=self._headers(),
                    timeout=min(self._timeout, restante),
                ) as respuesta:
                    if respuesta.status_code == 429 or respuesta.status_code >= 500:
                        detalle = (await respuesta.aread()).decode("utf-8", errors="replace")
                        if intento >= self._max_intentos:
                            raise self._error_agotado(respuesta, detalle, intento)
                        espera = self._espera(intento, respuesta)
                    elif respuesta.status_code >= 400:
                        detalle = (await respuesta.aread()).decode("utf-8", errors="replace")
                        if respuesta.status_code == 400:
                            recorte = _max_tokens_tras_desborde(detalle, req.max_tokens)
                            if recorte is not None:
                                logger.warning(
                                    "Workers AI 400 por contexto; reintento max_tokens=%s→%s",
                                    req.max_tokens,
                                    recorte,
                                )
                                req = req.model_copy(update={"max_tokens": recorte})
                                cuerpo = self._build_body(req, stream=True)
                                espera = 0.0
                                continue
                        raise self._error_http(respuesta.status_code, detalle)
                    else:
                        yielded_text = False
                        yielded_tools = False
                        async for trozo in _iterar_sse(respuesta):
                            if trozo.type == "stop":
                                break
                            if trozo.type == "text" and trozo.text:
                                yielded_text = True
                            if trozo.type == "tool_call" and trozo.tool_call is not None:
                                yielded_tools = True
                            yield trozo
                        # Scout + tools: el SSE cobra `completion_tokens` y
                        # manda `delta.content=""`. El mismo turno por
                        # `complete()` (sin stream) sí trae el texto. Sin
                        # este rescate el chat dispara "Me quedé sin respuesta".
                        if not yielded_text and not yielded_tools:
                            # Scout + tools deja `delta.content=""`. Si el
                            # último turno ya es el resultado de una tool,
                            # el stream NATIVO (sin tools) sí manda
                            # `{response: token}` como antes.
                            if req.tools and _ultimo_mensaje_pide_rescate_sin_tools(req):
                                logger.warning(
                                    "Workers AI stream vacío tras tools; "
                                    "stream nativo sin tools model=%s",
                                    modelo,
                                )
                                cuerpo_nativo = self._build_body(
                                    req.model_copy(update={"tools": None}),
                                    stream=True,
                                )
                                async with self._client.stream(
                                    "POST",
                                    self._url_para_modelo(modelo),
                                    json=cuerpo_nativo,
                                    headers=self._headers(),
                                    timeout=min(self._timeout, self._restante(limite)),
                                ) as nativo:
                                    if nativo.status_code < 400:
                                        async for trozo in _iterar_sse(nativo):
                                            if trozo.type == "stop":
                                                break
                                            if trozo.type == "text" and trozo.text:
                                                yielded_text = True
                                            yield trozo
                                if yielded_text:
                                    yield ProbeStreamChunk(type="stop")
                                    return
                            logger.warning(
                                "Workers AI stream vacío; reintento sin stream model=%s",
                                modelo,
                            )
                            salida = await self.complete(req)
                            if salida.text:
                                for trozo in _trozos_de_escritura(salida.text, palabras=1):
                                    yield ProbeStreamChunk(type="text", text=trozo)
                                    await asyncio.sleep(0.03)
                            for llamada in salida.tool_calls:
                                yield ProbeStreamChunk(type="tool_call", tool_call=llamada)
                            if salida.usage is not None:
                                yield ProbeStreamChunk(type="usage", usage=salida.usage)
                        yield ProbeStreamChunk(type="stop")
                        return
            except httpx.TimeoutException as exc:
                raise TiempoAgotadoError(
                    f"Workers AI no respondió en {min(self._timeout, restante):.1f}s",
                    provider=self.name,
                ) from exc
            except httpx.TransportError as exc:
                raise ProveedorInalcanzableError(
                    f"No se pudo conectar con Workers AI: {self._redactar(str(exc))}",
                    provider=self.name,
                ) from exc
            await self._esperar_o_rendirse(espera or 0.0, limite, intento)

    async def smoke(self) -> SmokeResult:
        req = CompletionRequest(
            model=self.model,
            system=PROMPT_SMOKE_SISTEMA,
            messages=[ChatMessage(role="user", content=PROMPT_SMOKE_USUARIO)],
            max_tokens=MAX_TOKENS_SMOKE,
            temperature=0.0,
        )
        inicio = time.monotonic()
        try:
            salida = await self.complete(req)
        except LLMError as exc:
            return SmokeResult(
                ok=False,
                proveedor=self.name,
                modelo=self.model,
                latencia_s=time.monotonic() - inicio,
                error=self._redactar(str(exc)),
            )
        return SmokeResult(
            ok=bool(salida.text.strip()),
            proveedor=self.name,
            modelo=self.model,
            latencia_s=salida.latencia_s,
            texto=salida.text,
            reasoning_content=salida.reasoning_content,
            prompt_tokens=salida.usage.input_tokens,
            completion_tokens=salida.usage.output_tokens,
            cached_tokens=salida.cached_tokens,
            neurons=salida.neurons,
        )


async def _iterar_sse(respuesta: httpx.Response) -> AsyncIterator[ProbeStreamChunk]:
    pendientes: dict[int, dict[str, Any]] = {}
    _raw_chunks: list[str] = []
    _saw_content = False
    _saw_reasoning = False
    _saw_tool_call = False
    razonamiento_acumulado: list[str] = []

    async for linea in respuesta.aiter_lines():
        if not linea.startswith("data:"):
            continue
        carga = linea[len("data:") :].strip()
        if not carga:
            continue
        if carga == "[DONE]":
            break
        try:
            datos = _desenvolver(json.loads(carga))
        except json.JSONDecodeError:
            _raw_chunks.append(f"JSON_ERROR: {carga[:200]}")
            continue
        if not isinstance(datos, dict):
            continue

        if uso := datos.get("usage"):
            detalle_entrada = uso.get("prompt_tokens_details") or {}
            detalle_salida = uso.get("completion_tokens_details") or {}
            yield ProbeStreamChunk(
                type="usage",
                usage=Usage(
                    input_tokens=_entero(uso.get("prompt_tokens")) or 0,
                    output_tokens=_entero(uso.get("completion_tokens")) or 0,
                ),
                cached_tokens=_entero(detalle_entrada.get("cached_tokens")),
                reasoning_tokens=_entero(detalle_salida.get("reasoning_tokens")),
                neurons=_flotante(uso.get("neurons")),
            )

        opciones = datos.get("choices") or []
        opcion = opciones[0] if opciones and isinstance(opciones[0], dict) else {}
        delta = opcion.get("delta") or {}
        mensaje = opcion.get("message") or {}

        razonamiento = (
            delta.get("reasoning_content")
            or mensaje.get("reasoning_content")
            or datos.get("reasoning_content")
            or ""
        )
        if razonamiento:
            _saw_reasoning = True
            razonamiento_acumulado.append(razonamiento)
            yield ProbeStreamChunk(type="text", text=None, reasoning_text=razonamiento)

        # Scout (y el esquema nativo de `/ai/run`) streaméa `{response: "token"}`
        # SIN `choices`. El fallback a `response` solo servía cuando ya había
        # choices con `delta.content` vacío; sin choices se descartaba el
        # texto y solo sobrevivía el `usage` — exactamente "output_tokens > 0
        # y content vacío".
        contenido = _texto_de_contenido(delta.get("content") or delta.get("text"))
        if not contenido:
            contenido = _texto_de_contenido(mensaje.get("content"))
        if not contenido:
            contenido = _texto_de_contenido(datos.get("response"))
        if contenido:
            _saw_content = True
            yield ProbeStreamChunk(type="text", text=contenido)

        tool_calls_delta = list(delta.get("tool_calls") or mensaje.get("tool_calls") or [])
        if not tool_calls_delta:
            nativos = datos.get("tool_calls") or []
            if isinstance(nativos, list):
                tool_calls_delta = [tc for tc in nativos if isinstance(tc, dict)]
        for indice_default, parcial in enumerate(tool_calls_delta):
            if not isinstance(parcial, dict):
                continue
            _saw_tool_call = True
            _acumular_tool_call_delta(pendientes, parcial, indice_default)
        if not opciones and not contenido and not razonamiento and not tool_calls_delta:
            _raw_chunks.append(f"NO_CHOICES: {json.dumps(datos)[:300]}")
        elif opciones and not contenido and not razonamiento and not tool_calls_delta:
            finish = opcion.get("finish_reason")
            if finish:
                _raw_chunks.append(
                    "FINISH_ONLY: "
                    f"finish_reason={finish} delta_keys={list(delta.keys())} "
                    f"response_field={datos.get('response', '')[:100]!r}"
                )

    if not _saw_content and not _saw_reasoning and not _saw_tool_call and _raw_chunks:
        logger.warning(
            "Workers AI stream: respuesta vacía. %d chunks anómalos: %s",
            len(_raw_chunks),
            " | ".join(_raw_chunks[:5]),
        )

    if not _saw_content and not _saw_tool_call and razonamiento_acumulado:
        # Último recurso: Scout (y a veces un Kimi que se comió el presupuesto
        # pensando) deja el texto solo en reasoning_content. Sin esto el
        # agente ve output_tokens > 0 y content vacío, y dispara el fallback.
        yield ProbeStreamChunk(type="text", text="".join(razonamiento_acumulado))
        _saw_content = True

    for indice in sorted(pendientes):
        entrada = pendientes[indice]
        yield ProbeStreamChunk(
            type="tool_call",
            tool_call=ToolCall(
                id=entrada["id"] or str(uuid4()),
                name=entrada["name"],
                arguments=_argumentos("".join(entrada["partes"])),
            ),
        )
    yield ProbeStreamChunk(type="stop")
