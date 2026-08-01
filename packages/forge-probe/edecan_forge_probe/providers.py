"""Adaptadores de proveedor de la fase 0 de Forge.

Este módulo es la **única** capa que habla con un modelo. Todo lo que mide la
fase 0 —contexto útil, fiabilidad de tool-calling, throughput, cache de
prefijo, sobrecarga de razonamiento— pasa por aquí, así que su trabajo no es
"llamar a la API": es **no perder ninguna señal medible** por el camino.

Dos proveedores:

- `WorkersAIProvider`: `POST /accounts/{cuenta}/ai/run/{modelo}` de Cloudflare
  Workers AI. El modelo se lee de `FORGE_PROBE_MODEL` precisamente para poder
  saltar a Kimi K3 el día que la cuenta lo tenga habilitado sin tocar código
  (hoy la cuenta solo sirve `@cf/moonshotai/kimi-k2.7-code` y `kimi-k2.6`).
- `OllamaProbeAdapter`: envuelve el `OllamaProvider` que ya existe en
  `packages/llm` para correr las mismas sondas contra el modelo local. Es la
  referencia de "proveedor más débil" del proyecto: si una sonda no distingue
  a Ollama de Workers AI, la sonda no mide nada.

Tres señales que la forma común de `edecan_llm` no contempla y que aquí NO se
pueden tirar a la basura:

1. `usage.prompt_tokens_details.cached_tokens` — la cache de prefijo. La
   entrada cacheada cuesta 0,19 USD/M frente a 0,95 USD/M: 5 veces menos. Sin
   este número no se puede decidir nada sobre estabilidad de prefijo.
2. `message.reasoning_content` — el razonamiento, que en este modelo está
   SIEMPRE activo, viaja en un campo distinto de `message.content` y se factura
   como salida a 4,00 USD/M. Medido contra la API real: una respuesta de dos
   palabras gastó 65 tokens de salida, ~57 de razonamiento; con
   `max_tokens: 32` el `content` llegó VACÍO y aun así se cobró. Por eso el
   razonamiento se expone separado y nunca mezclado con el texto: la sonda de
   sobrecarga de razonamiento necesita los dos por separado, y cualquier sonda
   que reserve presupuesto de salida necesita saber que este modo de fallo
   existe.
3. `usage.neurons` — la unidad de facturación real de Cloudflare.

Nada de esto cabe en `CompletionResponse`/`StreamChunk`, así que se exponen en
`ProbeCompletionResponse` y `ProbeStreamChunk`, subclases estrictas: un
consumidor escrito contra el contrato común sigue funcionando sin cambios.
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
from edecan_llm.base import (
    ChatMessage,
    CompletionRequest,
    CompletionResponse,
    LLMProvider,
    StreamChunk,
    ToolCall,
    ToolSpec,
    Usage,
)
from edecan_llm.errors import LLMError, ProviderDownError, RateLimitedError
from edecan_llm.multimodal import image_source, text_blocks
from edecan_llm.ollama import DEFAULT_BASE_URL as OLLAMA_BASE_URL
from edecan_llm.ollama import OllamaProvider
from pydantic import BaseModel, Field, computed_field

__all__ = [
    "BASE_URL_WORKERS_AI",
    "CredencialInvalidaError",
    "FalloTransitorioError",
    "LimiteDeTasaError",
    "MODELO_POR_DEFECTO",
    "OllamaProbeAdapter",
    "PeticionInvalidaError",
    "PresupuestoAgotadoError",
    "ProbeCompletionResponse",
    "ProbeStreamChunk",
    "ProveedorInalcanzableError",
    "RUTA_ENV_POR_DEFECTO",
    "SmokeResult",
    "TiempoAgotadoError",
    "WorkersAIProvider",
    "cargar_dotenv",
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

MODELO_POR_DEFECTO = "@cf/moonshotai/kimi-k2.7-code"
"""Único modelo Kimi con acceso confirmado en esta cuenta (27-07-2026).

`FORGE_PROBE_MODEL` lo sobrescribe. K3 devuelve 403 con `code: 5018` aquí; el
día que se habilite basta cambiar la variable, no el código."""

RUTA_ENV_POR_DEFECTO = Path(__file__).resolve().parents[3] / ".env"
"""`.env` de la raíz del monorepo. Se lee, nunca se exporta a `os.environ`."""

TIMEOUT_POR_DEFECTO = 120.0
"""Timeout de UNA petición HTTP. No confundir con el `deadline` de la llamada."""

DEADLINE_POR_DEFECTO = 300.0
"""Presupuesto total, reintentos incluidos, de una llamada lógica."""

MAX_INTENTOS = 4

MAX_TOKENS_SMOKE = 256
"""Deliberadamente holgado para dos frases.

El razonamiento come de este mismo presupuesto: con `max_tokens: 32` medido
contra la API real el `content` volvió vacío y la llamada se cobró igual. Un
smoke que fallara por eso diría "modelo caído" cuando el modelo está vivo."""

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
"""Recorte del cuerpo de error que se incrusta en el mensaje de excepción."""


# --------------------------------------------------------------------------- #
# Errores tipados
# --------------------------------------------------------------------------- #


class CredencialInvalidaError(LLMError):
    """401/403: el token no vale, no tiene permiso, o el modelo no está en la cuenta.

    NO se reintenta: reintentar una credencial mala solo gasta tiempo. El
    `code` de Cloudflare (p. ej. `5018` = modelo no disponible para la cuenta)
    se incrusta en el mensaje porque es lo que distingue "token mal" de
    "modelo mal".
    """


class PeticionInvalidaError(LLMError):
    """400/404/422: la petición está mal formada. Tampoco se reintenta.

    En una sonda esto casi siempre es un defecto propio (un esquema de
    herramienta ilegal, un `max_tokens` fuera de rango), no un fallo del
    proveedor. Reintentarlo enmascararía el bug.
    """


class LimiteDeTasaError(RateLimitedError):
    """429 tras agotar los reintentos. Lleva `retry_after` si el servidor lo dio."""

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
    """Se acabó el `deadline` absoluto de la llamada, con o sin reintentos por delante.

    Distinta de `TiempoAgotadoError` a propósito: "una petición tardó
    demasiado" y "la llamada entera, reintentos incluidos, no cabe en su
    presupuesto" son dos hechos distintos, y una sonda de latencia que los
    confunda produce un número falso.
    """


class ProveedorInalcanzableError(ProviderDownError):
    """No se pudo abrir la conexión (DNS, TLS, red caída)."""


# --------------------------------------------------------------------------- #
# Resultados enriquecidos
# --------------------------------------------------------------------------- #


class ProbeCompletionResponse(CompletionResponse):
    """`CompletionResponse` más lo que la fase 0 tiene que medir.

    Es subclase estricta: donde el contrato común pide un `CompletionResponse`,
    esto encaja sin adaptador.
    """

    cached_tokens: int | None = None
    """`usage.prompt_tokens_details.cached_tokens`. `None` = el proveedor no lo
    reportó, que NO es lo mismo que `0` (= reportó cero acierto de cache)."""

    reasoning_content: str = ""
    """`message.reasoning_content`, jamás concatenado a `text`."""

    reasoning_tokens: int | None = None
    """`usage.completion_tokens_details.reasoning_tokens` si viene.

    Contra Workers AI viene SIEMPRE `None`: su `usage` solo trae
    `prompt_tokens`, `completion_tokens`, `prompt_tokens_details.cached_tokens`
    y `neurons` — no desglosa el razonamiento. Se deja en `None` a propósito
    (`None` = no reportado) y la estimación va aparte, en
    `reasoning_tokens_estimados`."""

    neurons: float | None = None
    """`usage.neurons`: unidad de facturación real de Cloudflare."""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def reasoning_tokens_estimados(self) -> int | None:
        """Tokens de razonamiento estimados por reparto de caracteres.

        Necesario porque Workers AI no los desglosa y el sobrecoste del
        razonamiento es una métrica de primer orden de la fase 0: medido, el
        88 % de los tokens de salida de GLM-5.2 son razonamiento, y se facturan
        igual que el contenido.

        Es una ESTIMACIÓN y vive en un campo distinto de `reasoning_tokens` a
        propósito: `modelcard.py` prohíbe rellenar una medición con un valor
        inventado, y confundir "no reportado" con "estimado" es exactamente eso.

        Devuelve `None` si no hay con qué estimar. Si el proveedor sí reportó
        el desglose real, se devuelve ese, no la estimación.
        """
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
    """Cuántas peticiones HTTP costó. >1 significa que hubo 429/5xx."""

    latencia_s: float = 0.0

    raw_usage: dict[str, Any] = Field(default_factory=dict)
    """`usage` tal cual llegó. Evidencia auditable de cualquier número de arriba."""

    tool_calls_crudos: list[dict[str, Any]] = Field(default_factory=list)
    """`tool_calls` sin traducir, con `function.arguments` todavía como STRING.

    La sonda de `ArgProfile.CODE_BLOB` lo necesita: cuando el modelo emite JSON
    inválido al meter un bloque de código en un campo, `tool_calls` sale vacío
    o incompleto y la única forma de distinguir "no llamó" de "llamó mal" es
    mirar la cadena original."""


class ProbeStreamChunk(StreamChunk):
    """`StreamChunk` con las señales extra del streaming.

    El razonamiento viaja en `reasoning_text` con `text=None` **a propósito**:
    un consumidor que concatene `chunk.text` obtiene solo el contenido real, y
    un consumidor que quiera medir la sobrecarga de razonamiento lee el otro
    campo. Aun así se emite como chunk propio, con su instante de llegada
    intacto, porque el TTFT del modelo es el del primer token que produce — y
    en este modelo ese token es de razonamiento.
    """

    reasoning_text: str | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None
    neurons: float | None = None


class SmokeResult(BaseModel):
    """Resultado de `smoke()`: ¿credencial viva y modelo vivo?"""

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
# Entorno
# --------------------------------------------------------------------------- #


def cargar_dotenv(ruta: Path | str = RUTA_ENV_POR_DEFECTO) -> dict[str, str]:
    """Lee un `.env` a un diccionario. NO toca `os.environ`.

    Parser mínimo a propósito (sin dependencia nueva): `CLAVE=valor`, comilla
    simple o doble opcional, `export` opcional, `#` como comentario. No
    interpola variables ni admite valores multilínea; si el `.env` del repo
    llega a necesitarlo, se cambia aquí y no en cada llamante.
    """
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
    """`os.environ` manda; el `.env` solo rellena huecos."""
    return (os.environ.get(clave) or respaldo.get(clave) or "").strip()


# --------------------------------------------------------------------------- #
# Traducción: forma común de edecan_llm  <->  forma de Workers AI (estilo OpenAI)
# --------------------------------------------------------------------------- #


def herramienta_a_workers_ai(spec: ToolSpec) -> dict[str, Any]:
    """`ToolSpec` -> `{"type": "function", "function": {...}}`."""
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.input_schema,
        },
    }


def herramienta_desde_workers_ai(datos: dict[str, Any]) -> ToolSpec:
    """Inversa de `herramienta_a_workers_ai`.

    Existe para poder afirmar en un test que la traducción es de ida y vuelta;
    sin la inversa, un error de nombres de campo pasaría desapercibido.
    """
    funcion = datos.get("function") or datos
    return ToolSpec(
        name=str(funcion.get("name") or ""),
        description=str(funcion.get("description") or ""),
        input_schema=dict(funcion.get("parameters") or {}),
    )


def tool_call_a_workers_ai(llamada: ToolCall) -> dict[str, Any]:
    """`ToolCall` -> forma de cable, con `arguments` serializado a STRING.

    Este es el punto exacto donde se pierde información si uno se descuida:
    Workers AI (como OpenAI) NO acepta un objeto en `function.arguments`,
    exige una cadena JSON. `ensure_ascii=False` para que un bloque de código
    con acentos no se hinche a `\\uXXXX` y desplace el conteo de tokens.
    """
    return {
        "id": llamada.id,
        "type": "function",
        "function": {
            "name": llamada.name,
            "arguments": json.dumps(llamada.arguments, ensure_ascii=False),
        },
    }


def tool_call_desde_workers_ai(datos: dict[str, Any]) -> ToolCall:
    """Forma de cable -> `ToolCall`, deshaciendo el string JSON de `arguments`.

    Si `arguments` no es JSON válido devuelve `{}`: el crudo sigue disponible
    en `ProbeCompletionResponse.tool_calls_crudos` para diagnosticar.
    """
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
    """Traduce los `ChatMessage` comunes (bloques estilo Anthropic) a estilo OpenAI."""
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
    """Un `role="tool"` común puede llevar VARIOS `tool_result` (tool use paralelo).

    La API de chat completions exige un mensaje por `tool_call_id`; leer solo
    el primer bloque perdería en silencio el resto.
    """
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


# --------------------------------------------------------------------------- #
# Parseo de respuesta
# --------------------------------------------------------------------------- #


def _desenvolver(payload: dict[str, Any]) -> dict[str, Any]:
    """Quita el sobre `{"result": ..., "success": true}` de la REST API de Cloudflare.

    Los modelos servidos en forma OpenAI a veces llegan envueltos y a veces no,
    según la ruta. Se aceptan las dos formas en vez de apostar por una.
    """
    resultado = payload.get("result")
    if isinstance(resultado, dict) and ("choices" in resultado or "response" in resultado):
        return resultado
    return payload


def _entero(valor: object) -> int | None:
    """`None` cuando el proveedor no dijo nada. Nunca un 0 inventado."""
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
        # Forma antigua de Workers AI (`{"response": "..."}`). Se acepta para no
        # quedarse ciego si el modelo se sirve por esa ruta.
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
# Workers AI
# --------------------------------------------------------------------------- #


class WorkersAIProvider(LLMProvider):
    """Proveedor Cloudflare Workers AI sobre `POST /accounts/{id}/ai/run/{modelo}`."""

    name = "workers-ai"

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
        backoff_base_s: float = 0.5,
        backoff_max_s: float = 20.0,
        http_client: httpx.AsyncClient | None = None,
        env_file: Path | str | None = RUTA_ENV_POR_DEFECTO,
        rng: random.Random | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        respaldo = cargar_dotenv(env_file) if env_file is not None else {}
        self.account_id = account_id or _valor_entorno("CLOUDFLARE_ACCOUNT_ID", respaldo)
        self.model = model or _valor_entorno("FORGE_PROBE_MODEL", respaldo) or MODELO_POR_DEFECTO
        self._api_token = api_token or _valor_entorno("CLOUDFLARE_API_TOKEN", respaldo)
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._deadline_s = deadline_s
        self._max_intentos = max(1, max_intentos)
        self._backoff_base_s = max(0.0, backoff_base_s)
        self._backoff_max_s = max(0.0, backoff_max_s)
        self._rng = rng or random.Random()
        self._dormir = sleeper or asyncio.sleep
        self._propio = http_client is None
        self._client = http_client or httpx.AsyncClient(timeout=timeout)

    def __repr__(self) -> str:  # pragma: no cover - presentación
        # Sin token, ni truncado: un token truncado en un log sigue siendo una fuga.
        credencial = "sí" if self.account_id and self._api_token else "no"
        return f"WorkersAIProvider(model={self.model!r}, credencial={credencial})"

    async def aclose(self) -> None:
        """Cierra el cliente HTTP solo si lo creó este objeto."""
        if self._propio:
            await self._client.aclose()

    # -- infraestructura ---------------------------------------------------- #

    def _url(self) -> str:
        # El id de modelo lleva `@` y `/` (`@cf/moonshotai/...`) y va crudo en la
        # ruta: percent-encodearlo produce un 404.
        return f"{self._base_url}/accounts/{self.account_id}/ai/run/{self.model}"

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
        """Nunca dejar salir el token en un mensaje de error, venga de donde venga."""
        if self._api_token and self._api_token in texto:
            return texto.replace(self._api_token, "***")
        return texto

    def _presupuesto(self, req: CompletionRequest) -> float:
        """`metadata["deadline_s"]` = presupuesto total de la llamada, reintentos incluidos."""
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
            # Sin esto no llega ningún `usage` en el SSE y la sonda de coste y
            # la de cache de prefijo se quedan a cero para ese turno.
            cuerpo["stream_options"] = {"include_usage": True}
        if req.tools:
            cuerpo["tools"] = [herramienta_a_workers_ai(t) for t in req.tools]
            if (eleccion := req.metadata.get("tool_choice")) is not None:
                cuerpo["tool_choice"] = eleccion
        if (esfuerzo := req.metadata.get("reasoning_effort")) is not None:
            # El razonamiento no se puede apagar en este modelo; lo único
            # negociable es cuánto. Si el parámetro no se acepta, la API
            # responde 400 y `PeticionInvalidaError` lo hace visible en vez de
            # dejar creer que se aplicó.
            cuerpo["reasoning_effort"] = str(esfuerzo)
        if isinstance(extra := req.metadata.get("extra_body"), dict):
            cuerpo.update(extra)
        return cuerpo

    def _espera(self, intento: int, respuesta: httpx.Response | None) -> float:
        """Backoff exponencial con jitter completo, y `Retry-After` como suelo."""
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
        """Extrae `errors[].code` del cuerpo para no perder p. ej. el 5018."""
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
        """Cloudflare puede devolver 200 con `success: false`. No es un éxito."""
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
                f"El reintento {intento + 1} no cabe en el presupuesto de la llamada "
                f"a {self.model}",
                provider=self.name,
            )
        await self._dormir(espera)

    # -- completion --------------------------------------------------------- #

    async def complete(self, req: CompletionRequest) -> ProbeCompletionResponse:
        """Completion sin streaming, con todas las señales de medición intactas."""
        self._verificar_credencial()
        cuerpo = self._build_body(req, stream=False)
        inicio = time.monotonic()
        limite = inicio + self._presupuesto(req)
        respuesta, intentos = await self._post_con_reintento(cuerpo, limite)
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
        return _parsear_respuesta(
            _desenvolver(payload), intentos=intentos, latencia_s=time.monotonic() - inicio
        )

    async def _post_con_reintento(
        self, cuerpo: dict[str, Any], limite: float
    ) -> tuple[httpx.Response, int]:
        intento = 0
        while True:
            intento += 1
            restante = self._restante(limite)
            try:
                respuesta = await self._client.post(
                    self._url(),
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

    # -- streaming ---------------------------------------------------------- #

    async def stream(self, req: CompletionRequest) -> AsyncIterator[ProbeStreamChunk]:
        """Streaming SSE. Los reintentos solo ocurren antes del primer chunk emitido."""
        self._verificar_credencial()
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
                    self._url(),
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
            # El sleep va fuera del `async with` para no retener la conexión.
            await self._esperar_o_rendirse(espera or 0.0, limite, intento)

    # -- smoke -------------------------------------------------------------- #

    async def smoke(self) -> SmokeResult:
        """Llamada mínima que confirma credencial y modelo vivos.

        Devuelve `SmokeResult(ok=False, error=...)` en vez de lanzar: es lo
        primero que se ejecuta cuando llega el token y su trabajo es
        *informar* de qué falla, no abortar el proceso.
        """
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


# --------------------------------------------------------------------------- #
# SSE
# --------------------------------------------------------------------------- #


async def _iterar_sse(respuesta: httpx.Response) -> AsyncIterator[ProbeStreamChunk]:
    """Traduce el SSE (`data: {...}`, cierre con `data: [DONE]`) a `ProbeStreamChunk`."""
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
            # `text=None` a propósito: el razonamiento no es contenido.
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
                # Los argumentos llegan troceados: un bloque de código se parte
                # en decenas de deltas y solo la concatenación es JSON válido.
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


# --------------------------------------------------------------------------- #
# Ollama
# --------------------------------------------------------------------------- #


class OllamaProbeAdapter(LLMProvider):
    """Envuelve el `OllamaProvider` de `packages/llm` para que las sondas corran igual.

    Es el "proveedor más débil" de referencia: modelo local, sin cache de
    prefijo reportada y sin campo de razonamiento separado. Por eso
    `cached_tokens`, `reasoning_tokens` y `neurons` salen SIEMPRE en `None`
    aquí — no medido, no cero. Una sonda que dé el mismo veredicto para este
    adaptador y para Workers AI está midiendo su propio código.
    """

    name = "ollama"

    def __init__(
        self,
        *,
        model: str | None = None,
        base_url: str | None = None,
        provider: LLMProvider | None = None,
        timeout: float = 300.0,
        http_client: httpx.AsyncClient | None = None,
        env_file: Path | str | None = RUTA_ENV_POR_DEFECTO,
    ) -> None:
        respaldo = cargar_dotenv(env_file) if env_file is not None else {}
        self.model = model or _valor_entorno("FORGE_PROBE_OLLAMA_MODEL", respaldo)
        self.base_url = base_url or _valor_entorno("OLLAMA_BASE_URL", respaldo) or OLLAMA_BASE_URL
        self._inner = provider or OllamaProvider(
            self.base_url, self.model or None, timeout=timeout, http_client=http_client
        )

    async def aclose(self) -> None:
        cerrar = getattr(self._inner, "aclose", None)
        if cerrar is not None:
            await cerrar()

    def _con_modelo(self, req: CompletionRequest) -> CompletionRequest:
        if req.model:
            return req
        return req.model_copy(update={"model": self.model})

    async def complete(self, req: CompletionRequest) -> ProbeCompletionResponse:
        inicio = time.monotonic()
        base = await self._inner.complete(self._con_modelo(req))
        return ProbeCompletionResponse(
            text=base.text,
            tool_calls=list(base.tool_calls),
            usage=base.usage,
            stop_reason=base.stop_reason,
            latencia_s=time.monotonic() - inicio,
            tool_calls_crudos=[tool_call_a_workers_ai(tc) for tc in base.tool_calls],
        )

    async def stream(self, req: CompletionRequest) -> AsyncIterator[ProbeStreamChunk]:
        async for trozo in self._inner.stream(self._con_modelo(req)):
            yield ProbeStreamChunk(
                type=trozo.type,
                text=trozo.text,
                tool_call=trozo.tool_call,
                usage=trozo.usage,
            )

    async def smoke(self) -> SmokeResult:
        """Misma llamada mínima que `WorkersAIProvider.smoke`, misma forma de resultado."""
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
                error=str(exc),
            )
        return SmokeResult(
            ok=bool(salida.text.strip()),
            proveedor=self.name,
            modelo=self.model,
            latencia_s=salida.latencia_s,
            texto=salida.text,
            prompt_tokens=salida.usage.input_tokens,
            completion_tokens=salida.usage.output_tokens,
        )
