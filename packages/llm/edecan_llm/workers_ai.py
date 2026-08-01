"""Cloudflare Workers AI provider.

Este módulo implementa el contrato generic de ``LLMProvider`` sobre la API de
Cloudflare Workers AI (``POST /accounts/{cuenta}/ai/run/{modelo}``).
Preserva todas las señales necesarias para la medición y la ingeniería de código
(razonamiento separado, caché de prefijo, cómputo de neuronas y tool calls seguros).
"""

from __future__ import annotations

import asyncio
import json
import os
import random
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
    funcion = datos.get("function") or {}
    return ToolCall(
        id=str(datos.get("id") or uuid4()),
        name=str(funcion.get("name") or ""),
        arguments=_argumentos(funcion.get("arguments")),
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


def mensajes_a_workers_ai(req: CompletionRequest) -> list[dict[str, Any]]:
    mensajes: list[dict[str, Any]] = []
    if req.system:
        mensajes.append({"role": "system", "content": req.system})
    for mensaje in req.messages:
        mensajes.extend(_traducir_mensaje(mensaje))
    return mensajes


def _traducir_mensaje(mensaje: ChatMessage) -> list[dict[str, Any]]:
    if mensaje.role == "tool":
        return _resultados_de_herramienta(mensaje.content)
    if mensaje.role == "system":
        return [{"role": "system", "content": text_blocks(mensaje.content)}]
    if mensaje.role == "assistant" and isinstance(mensaje.content, list):
        return [_bloques_assistant(mensaje.content)]
    if mensaje.role == "user" and isinstance(mensaje.content, list):
        return [{"role": "user", "content": _bloques_user(mensaje.content)}]
    contenido = (
        mensaje.content if isinstance(mensaje.content, str) else text_blocks(mensaje.content)
    )
    return [{"role": mensaje.role, "content": contenido}]


def _bloques_user(bloques: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contenido: list[dict[str, Any]] = []
    for bloque in bloques:
        if bloque.get("type") == "text" and bloque.get("text"):
            contenido.append({"type": "text", "text": str(bloque["text"])})
            continue
        fuente = image_source(bloque)
        if fuente is not None:
            mime, datos = fuente
            contenido.append(
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{datos}"}}
            )
    return contenido or [{"type": "text", "text": text_blocks(bloques)}]


def _bloques_assistant(bloques: list[dict[str, Any]]) -> dict[str, Any]:
    texto = "".join(str(b.get("text") or "") for b in bloques if b.get("type") == "text")
    usos = [b for b in bloques if b.get("type") == "tool_use"]
    entrada: dict[str, Any] = {"role": "assistant", "content": texto or None}
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


def _parsear_respuesta(
    datos: dict[str, Any], *, intentos: int, latencia_s: float
) -> ProbeCompletionResponse:
    opciones = datos.get("choices") or []
    opcion = opciones[0] if opciones and isinstance(opciones[0], dict) else {}
    mensaje = opcion.get("message") or {}

    if not opciones and "response" in datos:
        mensaje = {"content": datos.get("response") or "", "tool_calls": datos.get("tool_calls")}

    crudos = [tc for tc in (mensaje.get("tool_calls") or []) if isinstance(tc, dict)]
    llamadas = [tool_call_desde_workers_ai(tc) for tc in crudos]

    uso = datos.get("usage") or {}
    detalle_entrada = uso.get("prompt_tokens_details") or {}
    detalle_salida = uso.get("completion_tokens_details") or {}

    razon = _MAPA_FINISH.get(str(opcion.get("finish_reason") or ""), "end")
    if llamadas and razon == "end":
        razon = "tool_use"

    return ProbeCompletionResponse(
        text=mensaje.get("content") or "",
        tool_calls=llamadas,
        usage=Usage(
            input_tokens=_entero(uso.get("prompt_tokens")) or 0,
            output_tokens=_entero(uso.get("completion_tokens")) or 0,
        ),
        stop_reason=razon,  # type: ignore[arg-type]
        cached_tokens=_entero(detalle_entrada.get("cached_tokens")),
        reasoning_content=mensaje.get("reasoning_content") or "",
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
                        raise self._error_http(respuesta.status_code, detalle)
                    else:
                        async for trozo in _iterar_sse(respuesta):
                            yield trozo
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
        if not opciones:
            continue
        delta = opciones[0].get("delta") or {}
        if razonamiento := delta.get("reasoning_content"):
            yield ProbeStreamChunk(type="text", text=None, reasoning_text=razonamiento)
        if contenido := delta.get("content"):
            yield ProbeStreamChunk(type="text", text=contenido)
        for parcial in delta.get("tool_calls") or []:
            indice = parcial.get("index", 0)
            entrada = pendientes.setdefault(indice, {"id": "", "name": "", "partes": []})
            if parcial.get("id"):
                entrada["id"] = parcial["id"]
            funcion = parcial.get("function") or {}
            if funcion.get("name"):
                entrada["name"] = funcion["name"]
            if funcion.get("arguments"):
                entrada["partes"].append(funcion["arguments"])

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
