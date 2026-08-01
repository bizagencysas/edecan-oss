"""Sonda de rendimiento, salida estructurada, caché de prefijo y visión.

Mide las propiedades del modelo de las que dependen los bloques 3, 4 y 6 de
Forge y que hoy nadie ha medido: cuánto tarda en soltar el primer token, cuántos
tokens saca por segundo en régimen sostenido, si el proveedor cachea de verdad
un prefijo largo (y cuánto dinero ahorra eso), si respeta un JSON Schema no
trivial y si lee una imagen.

Tres decisiones de diseño que conviene entender antes de leer el código:

**El razonamiento siempre está activo y va en un canal aparte.** Medido contra
la API real de Workers AI el 27-07-2026: `message.reasoning_content` es distinto
de `message.content`, consume presupuesto de salida y se factura a precio de
salida (4,00 USD/M). Una respuesta de dos palabras gastó 65 tokens de salida,
~57 de razonamiento; con `max_tokens: 32` el `content` llegó VACÍO y se cobró
igual. Por eso esta sonda separa dos tiempos distintos —`ttft` (primer token de
cualquier canal) y `ttfc` (primer token *visible*, el que ve un humano)— y trata
`content` vacío como un modo de fallo con nombre propio, no como un cero.

**El precio de entrada cacheada es 5x menor** (0,19 frente a 0,95 USD/M): la
estabilidad de prefijo no es una optimización, es una decisión económica. La
sonda de caché exige DOS señales independientes —`cached_tokens` y latencia—
antes de afirmar `prefix_cache=True`, porque cualquiera de las dos por separado
se explica por ruido o por contabilidad del proveedor.

**Un JSON inválido y un JSON válido que incumple el esquema son fallos
distintos** y exigen shims distintos: el primero se arregla reintentando o
recortando cercos de código, el segundo exige reparación campo a campo. Se
cuentan por separado y nunca se suman.

Regla dura del módulo: lo que no se mide es `None`. Ninguna función de aquí
inventa un valor "razonable", ni copia un número del catálogo del proveedor, ni
convierte una ausencia de dato en un `False`. Cuando el adaptador no puede hacer
algo, el `ProbeResult` sale con `ok=False` y el motivo escrito.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
import struct
import time
import zlib
from base64 import b64encode
from collections.abc import AsyncIterator, Callable, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from edecan_llm.base import ChatMessage, CompletionRequest
from pydantic import BaseModel, ConfigDict, Field

from edecan_forge_probe.modelcard import (
    Capability,
    Latencia,
    ProbeResult,
    Reliability,
)

# --------------------------------------------------------------------------- #
# Precios reales, verificados contra la API el 27-07-2026
# --------------------------------------------------------------------------- #

PRECIO_ENTRADA_USD_MTOK: float = 0.95
"""USD por millón de tokens de entrada NO cacheados."""

PRECIO_ENTRADA_CACHEADA_USD_MTOK: float = 0.19
"""USD por millón de tokens de entrada servidos desde caché de prefijo. 5x más
barato que la entrada fría: es la razón económica de estabilizar el prefijo."""

PRECIO_SALIDA_USD_MTOK: float = 4.00
"""USD por millón de tokens de salida. El razonamiento se factura aquí."""

CHARS_POR_TOKEN_APROX: int = 4
"""Sólo para *estimar* coste antes de llamar y así respetar `max_usd`. Jamás se
usa para reportar una medición: para eso está `usage` del proveedor."""


# --------------------------------------------------------------------------- #
# Contrato mínimo con el adaptador
# --------------------------------------------------------------------------- #


class UsoLLM(BaseModel):
    """Contabilidad de una llamada, tal y como la reporta el proveedor.

    `cached_tokens` sale de `usage.prompt_tokens_details.cached_tokens` y
    `neurons` de `usage.neurons` (unidad de facturación de Cloudflare). Ambos se
    registran crudos: son la única forma de auditar el coste después.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    """Tokens de salida TOTALES: incluye los de razonamiento."""
    cached_tokens: int = 0
    reasoning_tokens: int | None = None
    """`None` = el proveedor no lo desglosa. No se sustituye por una estimación."""
    neurons: float | None = None


class PeticionPerf(BaseModel):
    """Lo que la sonda le pide al adaptador. Deliberadamente pobre.

    No hay historial ni herramientas: esta sonda mide rendimiento y formato, y
    cualquier estructura extra contaminaría la medición.
    """

    prompt: str
    system: str | None = None
    max_tokens: int = 1024
    temperature: float = 0.0
    reasoning_effort: Literal["low", "high"] | None = None
    esquema_json: dict[str, Any] | None = None
    """JSON Schema para salida estructurada nativa (`response_format`)."""
    imagen_png: bytes | None = None
    """PNG crudo. El adaptador lo codifica como corresponda a su wire API."""
    etiqueta: str = ""
    """Nombre del escenario. Sólo viaja a la evidencia, no a la API."""


class RespuestaLLM(BaseModel):
    """Respuesta ya normalizada de una llamada sin streaming."""

    content: str = ""
    reasoning_content: str = ""
    uso: UsoLLM = Field(default_factory=UsoLLM)
    stop_reason: str | None = None


class TipoEvento(StrEnum):
    """Canales de un stream. `reasoning` y `content` son canales SEPARADOS."""

    REASONING = "reasoning"
    CONTENT = "content"
    USAGE = "usage"


class EventoStream(BaseModel):
    """Un fragmento del stream."""

    tipo: TipoEvento
    texto: str = ""
    uso: UsoLLM | None = None


@runtime_checkable
class ProveedorPerf(Protocol):
    """Lo mínimo que la sonda necesita de un adaptador.

    Es un `Protocol` estructural a propósito: cualquier adaptador que exponga
    estos dos métodos sirve, sin heredar de nada. Los tres atributos opcionales
    (`soporta_imagenes`, `acepta_reasoning_effort`, `soporta_response_format`)
    se leen con `getattr`; si faltan, la sonda intenta la llamada y clasifica el
    fallo, que es más honesto que asumir.
    """

    modelo: str

    async def completar(self, peticion: PeticionPerf) -> RespuestaLLM:
        """Llamada sin streaming."""
        ...

    def transmitir(self, peticion: PeticionPerf) -> AsyncIterator[EventoStream]:
        """Llamada en streaming. Devuelve un iterador asíncrono de eventos."""
        ...


class CapacidadNoSoportada(Exception):
    """El adaptador declara que no puede hacer lo que la sonda le pide.

    Se traduce a `ProbeResult(ok=False, error=...)`, nunca a un `False` medido:
    "el adaptador no sabe mandar imágenes" y "el modelo no ve" son cosas
    distintas y confundirlas envenena la ModelCard.
    """


class PuenteLLMProvider:
    """Traduce un `LLMProvider` de `edecan_llm` al `Protocol` de esta sonda.

    La sonda habla `PeticionPerf`/`EventoStream` y no `CompletionRequest`, por
    dos razones: puede pedir cosas que el contrato común no modela
    (`response_format`, `reasoning_effort`) y necesita el razonamiento como un
    canal separado con su propio instante de llegada.

    El puente es deliberadamente pato: no importa `edecan_forge_probe.providers`
    ni ninguna subclase concreta, sólo `complete`/`stream`. Las señales extra
    —`reasoning_content`, `cached_tokens`, `reasoning_tokens`, `neurons`— se
    leen con `getattr` y valen `None` cuando el proveedor no las reporta, que es
    distinto de cero y así se propaga hasta la `ModelCard`.
    """

    def __init__(
        self,
        proveedor: Any,
        *,
        modelo: str | None = None,
        soporta_imagenes: bool = True,
        acepta_reasoning_effort: bool = True,
        soporta_response_format: bool = True,
        nombre_esquema: str = "informe_revision",
    ) -> None:
        self._proveedor = proveedor
        self.modelo = modelo or str(
            getattr(proveedor, "model", None) or getattr(proveedor, "name", "desconocido")
        )
        self.soporta_imagenes = soporta_imagenes
        self.acepta_reasoning_effort = acepta_reasoning_effort
        self.soporta_response_format = soporta_response_format
        self._nombre_esquema = nombre_esquema

    def _peticion_comun(self, peticion: PeticionPerf) -> CompletionRequest:
        if peticion.imagen_png is not None and not self.soporta_imagenes:
            raise CapacidadNoSoportada("este puente se construyó con soporta_imagenes=False")

        contenido: str | list[dict[str, Any]]
        if peticion.imagen_png is None:
            contenido = peticion.prompt
        else:
            contenido = [
                {"type": "text", "text": peticion.prompt},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": b64encode(peticion.imagen_png).decode("ascii"),
                    },
                },
            ]

        metadata: dict[str, Any] = {}
        if peticion.reasoning_effort is not None and self.acepta_reasoning_effort:
            metadata["reasoning_effort"] = peticion.reasoning_effort
        if peticion.esquema_json is not None:
            if not self.soporta_response_format:
                raise CapacidadNoSoportada("este puente se construyó sin response_format")
            metadata["extra_body"] = {
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": self._nombre_esquema,
                        "strict": True,
                        "schema": peticion.esquema_json,
                    },
                }
            }
        return CompletionRequest(
            model=self.modelo,
            system=peticion.system,
            messages=[ChatMessage(role="user", content=contenido)],
            max_tokens=peticion.max_tokens,
            temperature=peticion.temperature,
            metadata=metadata,
        )

    @staticmethod
    def _uso(fuente: Any, usage: Any) -> UsoLLM:
        return UsoLLM(
            prompt_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cached_tokens=int(getattr(fuente, "cached_tokens", None) or 0),
            reasoning_tokens=getattr(fuente, "reasoning_tokens", None),
            neurons=getattr(fuente, "neurons", None),
        )

    async def completar(self, peticion: PeticionPerf) -> RespuestaLLM:
        respuesta = await self._proveedor.complete(self._peticion_comun(peticion))
        return RespuestaLLM(
            content=respuesta.text or "",
            reasoning_content=getattr(respuesta, "reasoning_content", "") or "",
            uso=self._uso(respuesta, getattr(respuesta, "usage", None)),
            stop_reason=getattr(respuesta, "stop_reason", None),
        )

    async def transmitir(self, peticion: PeticionPerf) -> AsyncIterator[EventoStream]:
        comun = self._peticion_comun(peticion)
        async for trozo in self._proveedor.stream(comun):
            razonamiento = getattr(trozo, "reasoning_text", None)
            if razonamiento:
                yield EventoStream(tipo=TipoEvento.REASONING, texto=razonamiento)
            if trozo.text:
                yield EventoStream(tipo=TipoEvento.CONTENT, texto=trozo.text)
            if getattr(trozo, "usage", None) is not None:
                yield EventoStream(tipo=TipoEvento.USAGE, uso=self._uso(trozo, trozo.usage))


# --------------------------------------------------------------------------- #
# Presupuesto
# --------------------------------------------------------------------------- #


class PresupuestoAgotado(Exception):
    """Se alcanzó `max_usd`. Corta la sonda en curso; no es un error de medida."""


def coste_usd(uso: UsoLLM) -> float:
    """Coste real de una llamada con los precios verificados de Workers AI.

    Los tokens cacheados NO se cobran además de los de entrada: son un
    subconjunto de `prompt_tokens` que se factura a la tarifa barata.
    """
    frios = max(0, uso.prompt_tokens - uso.cached_tokens)
    return (
        frios * PRECIO_ENTRADA_USD_MTOK
        + uso.cached_tokens * PRECIO_ENTRADA_CACHEADA_USD_MTOK
        + uso.completion_tokens * PRECIO_SALIDA_USD_MTOK
    ) / 1_000_000


class Presupuesto:
    """Contador de gasto con tope duro.

    Se consulta ANTES de cada llamada con una estimación pesimista (todo el
    prompt frío, `max_tokens` de salida completos) para no pasarse por sorpresa.
    """

    def __init__(self, max_usd: float | None = None) -> None:
        self.max_usd = max_usd
        self.gastado_usd: float = 0.0
        self.llamadas: int = 0

    def estimar(self, peticion: PeticionPerf) -> float:
        """Cota superior barata del coste de una petición."""
        chars = len(peticion.prompt) + len(peticion.system or "")
        entrada = max(1, chars // CHARS_POR_TOKEN_APROX)
        return (
            entrada * PRECIO_ENTRADA_USD_MTOK + peticion.max_tokens * PRECIO_SALIDA_USD_MTOK
        ) / 1_000_000

    def reservar(self, peticion: PeticionPerf) -> None:
        """Lanza `PresupuestoAgotado` si la llamada no cabe en el tope."""
        if self.max_usd is None:
            return
        if self.gastado_usd + self.estimar(peticion) > self.max_usd:
            raise PresupuestoAgotado(
                f"tope de {self.max_usd:.4f} USD alcanzado tras {self.llamadas} llamadas "
                f"({self.gastado_usd:.4f} USD gastados)"
            )

    def cobrar(self, uso: UsoLLM) -> float:
        """Suma el coste real observado y lo devuelve."""
        c = coste_usd(uso)
        self.gastado_usd += c
        self.llamadas += 1
        return c


# --------------------------------------------------------------------------- #
# Utilidades numéricas y de texto
# --------------------------------------------------------------------------- #


def percentil(valores: Sequence[float], q: float) -> float:
    """Percentil por interpolación lineal. `q` en [0, 1]. Sin dependencias."""
    if not valores:
        raise ValueError("no hay muestras para calcular el percentil")
    xs = sorted(valores)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q
    bajo = math.floor(pos)
    alto = math.ceil(pos)
    if bajo == alto:
        return xs[bajo]
    return xs[bajo] + (xs[alto] - xs[bajo]) * (pos - bajo)


def _latencia(valores: Sequence[float]) -> Latencia | None:
    """`Latencia` a partir de muestras crudas, o `None` si no hay ninguna."""
    if not valores:
        return None
    return Latencia(
        p50=percentil(valores, 0.50), p95=percentil(valores, 0.95), muestras=len(valores)
    )


_PALABRAS = (
    "runtime agente contexto herramienta parche despliegue esquema token latencia "
    "cache prefijo modelo sonda umbral evidencia criterio banco tarea repositorio "
    "migracion contrato adaptador proveedor presupuesto medicion ventana ratio "
    "razonamiento adherencia streaming neurona factura auditoria veredicto"
).split()


def texto_de_tokens(n_tokens: int, semilla: int = 0) -> str:
    """Texto determinista de aproximadamente `n_tokens` tokens.

    Se usa para inflar prompts (prompt largo de TTFT, prefijo de la caché). Es
    pseudoaleatorio con semilla para que dos ejecuciones sean comparables y para
    que dos rondas de la sonda de caché NO compartan prefijo por accidente.
    """
    rng = random.Random(semilla)
    objetivo = max(1, n_tokens) * CHARS_POR_TOKEN_APROX
    partes: list[str] = []
    largo = 0
    i = 0
    while largo < objetivo:
        palabra = rng.choice(_PALABRAS)
        trozo = f"{i:06d}-{palabra}"
        partes.append(trozo)
        largo += len(trozo) + 1
        i += 1
    return " ".join(partes)


def tokens_aprox(texto: str) -> int:
    """Estimación de tokens SÓLO para presupuesto y para dimensionar prompts."""
    return max(1, len(texto) // CHARS_POR_TOKEN_APROX)


_RE_BEARER = re.compile(r"(?i)\b(bearer\s+)\S+")
_RE_CLAVE = re.compile(r"(?i)\b(api[_-]?key|token|authorization)\b\s*[:=]\s*\S+")
_RE_BASE64_LARGO = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")


def sanear(mensaje: str) -> str:
    """Quita del texto cualquier cosa con forma de credencial.

    Los mensajes de error de httpx arrastran a veces la cabecera `Authorization`.
    El token de Workers AI no puede aparecer nunca en un `ProbeResult`, que se
    escribe a disco como evidencia y se pega en informes.
    """
    limpio = _RE_BEARER.sub(r"\1<redactado>", mensaje)
    limpio = _RE_CLAVE.sub(r"\1=<redactado>", limpio)
    return _RE_BASE64_LARGO.sub("<redactado>", limpio)


def _huella(texto: str) -> str:
    """Hash corto de un prompt: identifica la petición sin volcar 200 KB."""
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()[:16]


def semilla_estable(*partes: object) -> int:
    """Semilla determinista entre procesos.

    `hash()` de Python está aleatorizado por `PYTHONHASHSEED`: usarlo aquí haría
    que dos ejecuciones de la sonda mandasen prompts distintos y dejasen de ser
    comparables, que es justo lo que la fase 0 no puede permitirse.
    """
    crudo = "|".join(str(p) for p in partes).encode("utf-8")
    return zlib.crc32(crudo) & 0xFFFFFFFF


# --------------------------------------------------------------------------- #
# PNG generado al vuelo (sin dependencias de imagen)
# --------------------------------------------------------------------------- #

_FUENTE_5X7: dict[str, tuple[str, ...]] = {
    "A": (".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "C": (".###.", "#...#", "#....", "#....", "#....", "#...#", ".###."),
    "E": ("#####", "#....", "#....", "####.", "#....", "#....", "#####"),
    "F": ("#####", "#....", "#....", "####.", "#....", "#....", "#...."),
    "H": ("#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "J": ("..###", "....#", "....#", "....#", "#...#", "#...#", ".###."),
    "K": ("#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"),
    "L": ("#....", "#....", "#....", "#....", "#....", "#....", "#####"),
    "P": ("####.", "#...#", "#...#", "####.", "#....", "#....", "#...."),
    "R": ("####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"),
    "T": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."),
    "U": ("#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "X": ("#...#", "#...#", ".#.#.", "..#..", ".#.#.", "#...#", "#...#"),
    "Y": ("#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#.."),
    "2": (".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"),
    "3": ("#####", "...#.", "..#..", "...#.", "....#", "#...#", ".###."),
    "4": ("...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."),
    "7": ("#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."),
    "8": (".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."),
    "9": (".###.", "#...#", "#...#", ".####", "....#", "#...#", ".###."),
}

ALFABETO_VISION: str = "".join(sorted(_FUENTE_5X7))
"""Sólo caracteres con glifo propio y sin parejas confundibles (nada de O/0)."""


def codigo_vision(semilla: int, largo: int = 5) -> str:
    """Código legible y determinista para la prueba de visión."""
    rng = random.Random(semilla)
    return "".join(rng.choice(ALFABETO_VISION) for _ in range(largo))


def _trozo_png(tipo: bytes, datos: bytes) -> bytes:
    return (
        struct.pack(">I", len(datos))
        + tipo
        + datos
        + struct.pack(">I", zlib.crc32(tipo + datos) & 0xFFFFFFFF)
    )


def png_con_texto(texto: str, escala: int = 10, margen: int = 8) -> bytes:
    """Renderiza `texto` en un PNG en escala de grises, con la stdlib.

    Existe para que la prueba de visión no dependa de Pillow ni de un archivo
    binario en el repo: la imagen se genera en el momento, el texto es conocido
    y el criterio de éxito es que el modelo lo devuelva. Cualquier carácter fuera
    de `ALFABETO_VISION` es un error del llamante, no un espacio en blanco.
    """
    if escala < 1:
        raise ValueError("la escala debe ser >= 1")
    glifos = []
    for ch in texto.upper():
        if ch not in _FUENTE_5X7:
            raise ValueError(f"carácter sin glifo: {ch!r}; usa {ALFABETO_VISION}")
        glifos.append(_FUENTE_5X7[ch])

    ancho_celdas = len(glifos) * 6 - 1 if glifos else 0
    ancho = ancho_celdas * escala + 2 * margen
    alto = 7 * escala + 2 * margen

    # Lienzo blanco (255) sobre el que se pintan los píxeles del glifo en negro.
    filas = [bytearray(b"\xff" * ancho) for _ in range(alto)]
    for i, glifo in enumerate(glifos):
        base_x = i * 6
        for y, fila in enumerate(glifo):
            for x, celda in enumerate(fila):
                if celda != "#":
                    continue
                for dy in range(escala):
                    py = margen + y * escala + dy
                    inicio = margen + (base_x + x) * escala
                    filas[py][inicio : inicio + escala] = b"\x00" * escala

    crudo = b"".join(b"\x00" + bytes(f) for f in filas)
    cabecera = struct.pack(">IIBBBBB", ancho, alto, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _trozo_png(b"IHDR", cabecera)
        + _trozo_png(b"IDAT", zlib.compress(crudo, 9))
        + _trozo_png(b"IEND", b"")
    )


# --------------------------------------------------------------------------- #
# Esquema no trivial para la sonda de salida estructurada
# --------------------------------------------------------------------------- #


class Severidad(StrEnum):
    """Enum del esquema: obliga al modelo a elegir de un conjunto cerrado."""

    BAJA = "baja"
    MEDIA = "media"
    ALTA = "alta"
    CRITICA = "critica"


class Ubicacion(BaseModel):
    """Objeto anidado con un opcional dentro."""

    model_config = ConfigDict(extra="forbid")

    archivo: str
    linea: int = Field(ge=1)
    columna: int | None = None


class Hallazgo(BaseModel):
    """Elemento del array de objetos."""

    model_config = ConfigDict(extra="forbid")

    id: str
    severidad: Severidad
    ubicacion: Ubicacion
    etiquetas: list[str] = Field(min_length=1)
    sugerencia: str | None = None


class InformeRevision(BaseModel):
    """Esquema objetivo: anidado + enum + opcional + array de objetos.

    `extra="forbid"` es intencionado y se propaga al JSON Schema como
    `additionalProperties: false`. Un modelo que añade campos de su cosecha rompe
    igual un parser estricto río abajo: contarlo como éxito sería mentirse.
    """

    model_config = ConfigDict(extra="forbid")

    repo: str
    commit: str
    resumen: str
    aprobado: bool
    hallazgos: list[Hallazgo] = Field(min_length=2)


ESQUEMA_ESTRUCTURADO: dict[str, Any] = InformeRevision.model_json_schema()


class FalloEstructura(StrEnum):
    """Taxonomía de fallos de salida estructurada. No se suman entre sí."""

    OK = "ok"
    SIN_CONTENIDO = "sin_contenido"
    """`content` vacío: el presupuesto de salida se lo comió el razonamiento."""
    JSON_INVALIDO = "json_invalido"
    """Ni siquiera parsea. Shim: recortar cercos, reintentar, reparar comillas."""
    ESQUEMA_INVALIDO = "esquema_invalido"
    """Parsea pero incumple el esquema. Shim: reparación campo a campo."""


def _extraer_json(texto: str) -> str:
    """Aplica el shim mínimo: quita cercos de código y recorta al objeto.

    Se usa SÓLO para la métrica secundaria `con_shim`. La métrica principal
    parsea el `content` tal cual llegó.
    """
    t = texto.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t.strip())
    ini = t.find("{")
    fin = t.rfind("}")
    if ini != -1 and fin > ini:
        t = t[ini : fin + 1]
    return t.strip()


def clasificar_estructura(content: str, *, con_shim: bool = False) -> FalloEstructura:
    """Clasifica una respuesta contra `InformeRevision`.

    Distinguir `JSON_INVALIDO` de `ESQUEMA_INVALIDO` es el objetivo entero de la
    función: son dos defectos con dos arreglos distintos y agregarlos en una
    sola tasa de éxito borra la información que hace falta para decidir el shim.
    """
    bruto = content.strip()
    if not bruto:
        return FalloEstructura.SIN_CONTENIDO
    candidato = _extraer_json(bruto) if con_shim else bruto
    try:
        obj = json.loads(candidato)
    except (json.JSONDecodeError, ValueError):
        return FalloEstructura.JSON_INVALIDO
    try:
        InformeRevision.model_validate(obj)
    except Exception:  # noqa: BLE001 - pydantic ValidationError y variantes
        return FalloEstructura.ESQUEMA_INVALIDO
    return FalloEstructura.OK


# --------------------------------------------------------------------------- #
# Configuración de la sonda
# --------------------------------------------------------------------------- #


class ConfigPerf(BaseModel):
    """Parámetros de la sonda. Los valores por defecto son los del encargo."""

    model_config = ConfigDict(frozen=True)

    muestras_ttft: int = Field(default=20, ge=1)
    """>=20 por escenario, según el encargo. Con menos, `Latencia.muestras` lo
    delata y el runner puede descartar la medición."""

    tokens_prompt_corto: int = Field(default=400, ge=1)
    tokens_prompt_largo: int = Field(default=52_000, ge=1)
    max_tokens_ttft: int = Field(default=192, ge=1)
    """Holgado a propósito: con `max_tokens` corto el razonamiento agota la
    salida y `content` llega vacío. Medido contra la API real."""

    tokens_throughput: int = Field(default=800, ge=1)
    muestras_throughput: int = Field(default=3, ge=1)

    tokens_prefijo_cache: int = Field(default=8_000, ge=1)
    rondas_cache: int = Field(default=3, ge=1)
    margen_latencia_cache: float = Field(default=0.10, ge=0.0)
    """Caída relativa mínima de latencia para contar como señal. 10 % separa un
    acierto de caché del ruido normal de red."""
    fraccion_cacheada_minima: float = Field(default=0.50, ge=0.0, le=1.0)

    muestras_estructurado: int = Field(default=12, ge=1)
    """Por modo (nativo y prompted): se miden los dos."""

    muestras_vision: int = Field(default=3, ge=1)
    escala_vision: int = Field(default=10, ge=1)

    muestras_razonamiento: int = Field(default=3, ge=1)
    longitudes_razonamiento: tuple[int, ...] = (24, 200, 800)

    esfuerzos: tuple[Literal["low", "high"], ...] = ("low", "high")


# --------------------------------------------------------------------------- #
# Muestras crudas
# --------------------------------------------------------------------------- #


class MuestraStream(BaseModel):
    """Una llamada en streaming, cronometrada evento a evento."""

    etiqueta: str
    ttft_s: float | None = None
    """Primer token de CUALQUIER canal (normalmente razonamiento)."""
    ttfc_s: float | None = None
    """Primer token de `content`: el primero que un humano ve."""
    fin_s: float = 0.0
    content: str = ""
    reasoning: str = ""
    uso: UsoLLM = Field(default_factory=UsoLLM)
    coste_usd: float = 0.0

    @property
    def duracion_sostenida_s(self) -> float:
        """Ventana desde el primer token hasta el último. Excluye el arranque."""
        if self.ttft_s is None:
            return 0.0
        return max(0.0, self.fin_s - self.ttft_s)


# --------------------------------------------------------------------------- #
# Evidencia en disco
# --------------------------------------------------------------------------- #


class Evidencia:
    """Escribe una traza JSONL por sonda y devuelve su ruta.

    Sin esto, ningún número de la ModelCard es auditable. Los prompts largos se
    guardan por huella y longitud, no en crudo: 52k tokens por muestra harían el
    archivo inservible.
    """

    def __init__(self, directorio: Path | None) -> None:
        self.directorio = directorio
        self._abiertos: dict[str, Path] = {}

    def registrar(self, probe: str, fila: dict[str, Any]) -> None:
        if self.directorio is None:
            return
        self.directorio.mkdir(parents=True, exist_ok=True)
        ruta = self._abiertos.get(probe)
        if ruta is None:
            ruta = self.directorio / f"{probe}.jsonl"
            ruta.write_text("", encoding="utf-8")
            self._abiertos[probe] = ruta
        with ruta.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(fila, ensure_ascii=False, default=str) + "\n")

    def rutas(self, probe: str) -> list[str]:
        ruta = self._abiertos.get(probe)
        return [str(ruta)] if ruta is not None else []


# --------------------------------------------------------------------------- #
# La sonda
# --------------------------------------------------------------------------- #


class SondaRendimiento:
    """Sondas de rendimiento, estructura, caché y visión sobre un proveedor.

    Cada método público devuelve un `ProbeResult` y jamás lanza: un fallo de red
    o de presupuesto sale como `ok=False` con el motivo, porque una sonda que
    revienta a mitad de una campaña de 80 llamadas pagadas es un defecto.
    """

    def __init__(
        self,
        proveedor: ProveedorPerf,
        *,
        config: ConfigPerf | None = None,
        max_usd: float | None = None,
        dir_evidencia: Path | str | None = None,
        reloj: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.proveedor = proveedor
        self.config = config or ConfigPerf()
        self.presupuesto = Presupuesto(max_usd)
        self.evidencia = Evidencia(Path(dir_evidencia) if dir_evidencia is not None else None)
        self.reloj = reloj

    # -- primitivas de llamada ---------------------------------------------- #

    async def _completar(self, probe: str, peticion: PeticionPerf) -> tuple[RespuestaLLM, float]:
        """Una llamada sin streaming, cobrada y registrada."""
        self.presupuesto.reservar(peticion)
        t0 = self.reloj()
        respuesta = await self.proveedor.completar(peticion)
        dt = self.reloj() - t0
        coste = self.presupuesto.cobrar(respuesta.uso)
        self.evidencia.registrar(
            probe,
            {
                "modo": "completar",
                "etiqueta": peticion.etiqueta,
                "prompt_huella": _huella(peticion.prompt),
                "prompt_chars": len(peticion.prompt),
                "max_tokens": peticion.max_tokens,
                "reasoning_effort": peticion.reasoning_effort,
                "latencia_s": dt,
                "content_chars": len(respuesta.content),
                "reasoning_chars": len(respuesta.reasoning_content),
                "content_muestra": respuesta.content[:600],
                "uso": respuesta.uso.model_dump(),
                "coste_usd": coste,
                "stop_reason": respuesta.stop_reason,
            },
        )
        return respuesta, dt

    async def _transmitir(self, probe: str, peticion: PeticionPerf) -> MuestraStream:
        """Una llamada en streaming, cronometrada por canal."""
        self.presupuesto.reservar(peticion)
        muestra = MuestraStream(etiqueta=peticion.etiqueta)
        partes_content: list[str] = []
        partes_reasoning: list[str] = []
        t0 = self.reloj()
        async for evento in self.proveedor.transmitir(peticion):
            ahora = self.reloj() - t0
            if evento.tipo is TipoEvento.USAGE:
                if evento.uso is not None:
                    muestra.uso = evento.uso
                continue
            if not evento.texto:
                continue
            if muestra.ttft_s is None:
                muestra.ttft_s = ahora
            if evento.tipo is TipoEvento.CONTENT:
                if muestra.ttfc_s is None:
                    muestra.ttfc_s = ahora
                partes_content.append(evento.texto)
            else:
                partes_reasoning.append(evento.texto)
            muestra.fin_s = ahora
        muestra.content = "".join(partes_content)
        muestra.reasoning = "".join(partes_reasoning)
        muestra.coste_usd = self.presupuesto.cobrar(muestra.uso)
        self.evidencia.registrar(
            probe,
            {
                "modo": "stream",
                "etiqueta": peticion.etiqueta,
                "prompt_huella": _huella(peticion.prompt),
                "prompt_chars": len(peticion.prompt),
                "max_tokens": peticion.max_tokens,
                "reasoning_effort": peticion.reasoning_effort,
                "ttft_s": muestra.ttft_s,
                "ttfc_s": muestra.ttfc_s,
                "fin_s": muestra.fin_s,
                "content_chars": len(muestra.content),
                "reasoning_chars": len(muestra.reasoning),
                "uso": muestra.uso.model_dump(),
                "coste_usd": muestra.coste_usd,
            },
        )
        return muestra

    # -- TTFT ---------------------------------------------------------------- #

    async def ttft(self) -> ProbeResult:
        """Tiempo hasta el primer token, por longitud de prompt y por esfuerzo.

        Cuatro escenarios (corto/largo × esfuerzo bajo/alto) con `muestras_ttft`
        muestras cada uno, en streaming. Se miden DOS tiempos por muestra:

        - `ttft`: primer token de cualquier canal.
        - `ttfc`: primer token de `content`. Con razonamiento siempre activo la
          diferencia entre ambos es el tiempo que el usuario pasa mirando una
          pantalla vacía, y es la cifra que de verdad importa para la UX.

        El `latencia` que se devuelve —el que alimenta el umbral `ttft_p95_s`—
        es el del escenario `corto|low`: el umbral de 2,5 s describe la
        respuesta interactiva, no una carga de 52k tokens. Todos los escenarios
        van completos en `detalle.escenarios`, incluido `p95_peor`.
        """
        cfg = self.config
        probe = "perf.ttft"
        t_ini = self.reloj()
        escenarios: dict[str, dict[str, Any]] = {}
        crudas: dict[str, list[float]] = {}
        agotado = False
        acepta_esfuerzo = bool(getattr(self.proveedor, "acepta_reasoning_effort", True))
        esfuerzos: tuple[Literal["low", "high"] | None, ...] = (
            cfg.esfuerzos if acepta_esfuerzo else (None,)
        )

        try:
            for longitud, n_tokens in (
                ("corto", cfg.tokens_prompt_corto),
                ("largo", cfg.tokens_prompt_largo),
            ):
                for esfuerzo in esfuerzos:
                    clave = f"{longitud}|{esfuerzo or 'defecto'}"
                    ttfts: list[float] = []
                    ttfcs: list[float] = []
                    vacios = 0
                    for i in range(cfg.muestras_ttft):
                        relleno = texto_de_tokens(n_tokens, semilla=semilla_estable(clave, i))
                        prompt = (
                            f"{relleno}\n\n"
                            "Basándote SOLO en el texto anterior, di en una frase corta "
                            f"qué palabra sigue al marcador {i:06d}."
                        )
                        muestra = await self._transmitir(
                            probe,
                            PeticionPerf(
                                prompt=prompt,
                                max_tokens=cfg.max_tokens_ttft,
                                reasoning_effort=esfuerzo,
                                etiqueta=f"{clave}#{i}",
                            ),
                        )
                        if muestra.ttft_s is not None:
                            ttfts.append(muestra.ttft_s)
                        if muestra.ttfc_s is not None:
                            ttfcs.append(muestra.ttfc_s)
                        else:
                            vacios += 1
                    crudas[clave] = ttfts
                    lat = _latencia(ttfts)
                    lat_c = _latencia(ttfcs)
                    escenarios[clave] = {
                        "tokens_prompt_aprox": n_tokens,
                        "reasoning_effort": esfuerzo,
                        "ttft": lat.model_dump() if lat else None,
                        "ttfc": lat_c.model_dump() if lat_c else None,
                        "sin_content": vacios,
                        "muestras": cfg.muestras_ttft,
                    }
        except PresupuestoAgotado as exc:
            agotado = True
            escenarios["_presupuesto"] = {"motivo": sanear(str(exc))}
        except Exception as exc:  # noqa: BLE001 - cualquier fallo del adaptador
            return ProbeResult(
                probe=probe,
                capability=Capability.STREAMING,
                ok=False,
                error=sanear(f"{type(exc).__name__}: {exc}"),
                detalle={"escenarios": escenarios},
                evidencia=self.evidencia.rutas(probe),
                duracion_s=self.reloj() - t_ini,
            )

        cabecera = next(
            (k for k in ("corto|low", "corto|defecto", "corto|high") if crudas.get(k)),
            None,
        )
        if cabecera is None:
            cabecera = next((k for k, v in crudas.items() if v), None)
        latencia = _latencia(crudas[cabecera]) if cabecera else None

        p95s = [
            e["ttft"]["p95"]
            for e in escenarios.values()
            if isinstance(e.get("ttft"), dict) and e["ttft"] is not None
        ]
        return ProbeResult(
            probe=probe,
            capability=Capability.STREAMING,
            ok=latencia is not None,
            valor=latencia.p95 if latencia else None,
            latencia=latencia,
            detalle={
                # `modelcard` es el canal explícito del runner: sin esto la
                # tarjeta no recibe la latencia, porque su heurística sólo mira
                # las sondas cuyo nombre empieza por "ttft".
                "modelcard": ({"ttft": latencia.model_dump()} if latencia else {}),
                "escenario_reportado": cabecera,
                "escenarios": escenarios,
                "p95_peor": max(p95s) if p95s else None,
                "presupuesto_agotado": agotado,
                "acepta_reasoning_effort": acepta_esfuerzo,
                "gastado_usd": self.presupuesto.gastado_usd,
            },
            evidencia=self.evidencia.rutas(probe),
            error=None if latencia is not None else "no se obtuvo ninguna muestra de TTFT",
            duracion_s=self.reloj() - t_ini,
        )

    # -- Throughput ---------------------------------------------------------- #

    async def throughput(self) -> ProbeResult:
        """Tokens de salida por segundo en régimen sostenido.

        Régimen sostenido = ventana entre el primer y el último token, sin el
        arranque: mezclar TTFT con throughput produce un número que no sirve ni
        para una cosa ni para la otra. Se exige >=800 tokens de salida para que
        la ventana sea larga frente al jitter.

        `valor` son tokens de salida TOTALES por segundo —razonamiento incluido,
        porque se factura igual y ocupa el mismo hueco—; `tps_contenido` sale
        aparte cuando el proveedor desglosa `reasoning_tokens`.
        """
        cfg = self.config
        probe = "perf.throughput"
        t_ini = self.reloj()
        tps_total: list[float] = []
        tps_contenido: list[float] = []
        muestras_ok = 0
        cortas = 0
        agotado = False

        try:
            for i in range(cfg.muestras_throughput):
                prompt = (
                    "Redacta una explicación técnica continua y sin listas sobre el diseño de "
                    "un runtime de agentes: aislamiento del workspace, contratos de aceptación "
                    "ejecutables y control de presupuesto. Extiéndete sin repetirte hasta "
                    f"agotar el espacio disponible. Variante {i}."
                )
                muestra = await self._transmitir(
                    probe,
                    PeticionPerf(
                        prompt=prompt,
                        max_tokens=int(cfg.tokens_throughput * 1.5),
                        etiqueta=f"sostenido#{i}",
                    ),
                )
                salida = muestra.uso.completion_tokens
                ventana = muestra.duracion_sostenida_s
                if salida < cfg.tokens_throughput:
                    cortas += 1
                if salida < 2 or ventana <= 0:
                    continue
                muestras_ok += 1
                tps_total.append((salida - 1) / ventana)
                if muestra.uso.reasoning_tokens is not None:
                    visibles = salida - muestra.uso.reasoning_tokens
                    if visibles > 1:
                        tps_contenido.append((visibles - 1) / ventana)
        except PresupuestoAgotado as exc:
            agotado = True
            if not tps_total:
                return ProbeResult(
                    probe=probe,
                    ok=False,
                    error=sanear(str(exc)),
                    evidencia=self.evidencia.rutas(probe),
                    duracion_s=self.reloj() - t_ini,
                )
        except Exception as exc:  # noqa: BLE001
            return ProbeResult(
                probe=probe,
                ok=False,
                error=sanear(f"{type(exc).__name__}: {exc}"),
                evidencia=self.evidencia.rutas(probe),
                duracion_s=self.reloj() - t_ini,
            )

        mediana = percentil(tps_total, 0.50) if tps_total else None
        return ProbeResult(
            probe=probe,
            ok=mediana is not None,
            valor=mediana,
            detalle={
                "modelcard": ({"throughput_tps": mediana} if mediana is not None else {}),
                "tps_por_muestra": tps_total,
                "tps_contenido_mediana": (
                    percentil(tps_contenido, 0.50) if tps_contenido else None
                ),
                "muestras_validas": muestras_ok,
                "muestras_por_debajo_del_minimo": cortas,
                "tokens_minimos_exigidos": cfg.tokens_throughput,
                "presupuesto_agotado": agotado,
                "gastado_usd": self.presupuesto.gastado_usd,
            },
            evidencia=self.evidencia.rutas(probe),
            error=None if mediana is not None else "ninguna muestra alcanzó régimen sostenido",
            duracion_s=self.reloj() - t_ini,
        )

    # -- Caché de prefijo ----------------------------------------------------- #

    async def cache_prefijo(self) -> ProbeResult:
        """¿Cachea el proveedor un prefijo largo, y cuánto dinero ahorra?

        Por ronda: mismo prefijo largo, dos sufijos distintos. La segunda llamada
        sólo cuenta como acierto si aparecen LAS DOS señales:

        1. `cached_tokens` crece y cubre al menos `fraccion_cacheada_minima` del
           prefijo. Por sí sola puede ser contabilidad optimista del proveedor.
        2. La latencia cae al menos `margen_latencia_cache`. Por sí sola es ruido
           de red perfectamente normal.

        Cada ronda usa una semilla distinta para que el prefijo de la ronda N no
        esté ya caliente por la ronda N-1. `prefix_cache` sale `True` sólo si
        TODAS las rondas medidas dan las dos señales; el ahorro se reporta en
        porcentaje de coste real, no de tokens.
        """
        cfg = self.config
        probe = "perf.prefix_cache"
        t_ini = self.reloj()
        rondas: list[dict[str, Any]] = []
        agotado = False

        try:
            for r in range(cfg.rondas_cache):
                prefijo = texto_de_tokens(cfg.tokens_prefijo_cache, semilla=9_000 + r)
                base = (
                    "Este es un expediente de referencia. Consúltalo y responde muy breve.\n\n"
                    f"{prefijo}\n\n"
                )
                medidas: list[tuple[RespuestaLLM, float]] = []
                for j, sufijo in enumerate(
                    (
                        "Pregunta A: responde únicamente con la palabra ALFA.",
                        "Pregunta B: responde únicamente con la palabra BRAVO.",
                    )
                ):
                    respuesta, dt = await self._completar(
                        probe,
                        PeticionPerf(
                            prompt=base + sufijo,
                            max_tokens=cfg.max_tokens_ttft,
                            etiqueta=f"ronda{r}|llamada{j}",
                        ),
                    )
                    medidas.append((respuesta, dt))

                (r1, t1), (r2, t2) = medidas
                prefijo_tokens = tokens_aprox(base)
                senal_tokens = (
                    r2.uso.cached_tokens > r1.uso.cached_tokens
                    and r2.uso.cached_tokens >= prefijo_tokens * cfg.fraccion_cacheada_minima
                )
                senal_latencia = t1 > 0 and (t1 - t2) / t1 >= cfg.margen_latencia_cache
                coste_frio = coste_usd(
                    UsoLLM(
                        prompt_tokens=r2.uso.prompt_tokens,
                        completion_tokens=r2.uso.completion_tokens,
                        cached_tokens=0,
                    )
                )
                coste_real = coste_usd(r2.uso)
                rondas.append(
                    {
                        "ronda": r,
                        "prefijo_tokens_aprox": prefijo_tokens,
                        "cached_1": r1.uso.cached_tokens,
                        "cached_2": r2.uso.cached_tokens,
                        "prompt_tokens_1": r1.uso.prompt_tokens,
                        "prompt_tokens_2": r2.uso.prompt_tokens,
                        "latencia_1_s": t1,
                        "latencia_2_s": t2,
                        "caida_latencia_pct": ((t1 - t2) / t1 * 100.0) if t1 > 0 else None,
                        "senal_tokens": senal_tokens,
                        "senal_latencia": senal_latencia,
                        "acierto": senal_tokens and senal_latencia,
                        "coste_sin_cache_usd": coste_frio,
                        "coste_real_usd": coste_real,
                        "ahorro_pct": (
                            (coste_frio - coste_real) / coste_frio * 100.0
                            if coste_frio > 0
                            else 0.0
                        ),
                    }
                )
        except PresupuestoAgotado as exc:
            agotado = True
            if not rondas:
                return ProbeResult(
                    probe=probe,
                    capability=Capability.PREFIX_CACHE,
                    ok=False,
                    error=sanear(str(exc)),
                    evidencia=self.evidencia.rutas(probe),
                    duracion_s=self.reloj() - t_ini,
                )
        except Exception as exc:  # noqa: BLE001
            return ProbeResult(
                probe=probe,
                capability=Capability.PREFIX_CACHE,
                ok=False,
                error=sanear(f"{type(exc).__name__}: {exc}"),
                evidencia=self.evidencia.rutas(probe),
                duracion_s=self.reloj() - t_ini,
            )

        aciertos = sum(1 for x in rondas if x["acierto"])
        hay_cache = bool(rondas) and aciertos == len(rondas)
        ahorros = [x["ahorro_pct"] for x in rondas if x["acierto"]]
        # El ahorro sólo se publica si la cache quedó CONFIRMADA en todas las
        # rondas. Un porcentaje sacado de una ronda suelta invitaría a diseñar
        # el prefijo contando con un descuento que no está demostrado.
        return ProbeResult(
            probe=probe,
            capability=Capability.PREFIX_CACHE,
            ok=bool(rondas),
            valor=percentil(ahorros, 0.50) if (hay_cache and ahorros) else None,
            reliability=Reliability(successes=aciertos, trials=len(rondas)),
            detalle={
                "modelcard": ({"prefix_cache": hay_cache} if rondas else {}),
                "prefix_cache": hay_cache,
                "rondas": rondas,
                "rondas_con_senal_tokens": sum(1 for x in rondas if x["senal_tokens"]),
                "rondas_con_senal_latencia": sum(1 for x in rondas if x["senal_latencia"]),
                "exige_dos_senales": True,
                "presupuesto_agotado": agotado,
                "precio_entrada_usd_mtok": PRECIO_ENTRADA_USD_MTOK,
                "precio_entrada_cacheada_usd_mtok": PRECIO_ENTRADA_CACHEADA_USD_MTOK,
                "gastado_usd": self.presupuesto.gastado_usd,
            },
            evidencia=self.evidencia.rutas(probe),
            error=None if rondas else "no se completó ninguna ronda",
            duracion_s=self.reloj() - t_ini,
        )

    # -- Salida estructurada --------------------------------------------------- #

    async def salida_estructurada(self) -> ProbeResult:
        """Adherencia a un JSON Schema no trivial, con los fallos separados.

        Se miden dos modos, porque son dos capacidades distintas y el shim que
        exigen es distinto:

        - `nativo`: el esquema viaja en `response_format`. Es lo que Forge quiere
          usar; si esto funciona, no hace falta shim.
        - `prompted`: el esquema viaja en el texto del prompt. Es el plan B.

        La `Reliability` que se devuelve es la del modo nativo (o la del
        prompted si el adaptador no soporta `response_format`) y se calcula sobre
        adherencia ESTRICTA: el `content` tal y como llegó. La variante con shim
        —recortar cercos de código y quedarse con el objeto— se cuenta aparte
        para saber cuánto arregla un parche de tres líneas.
        """
        cfg = self.config
        probe = "perf.structured_output"
        t_ini = self.reloj()
        soporta_rf = bool(getattr(self.proveedor, "soporta_response_format", True))
        esquema_texto = json.dumps(ESQUEMA_ESTRUCTURADO, ensure_ascii=False, indent=2)
        modos: dict[str, dict[str, Any]] = {}
        fiabilidades: dict[str, Reliability] = {}
        agotado = False

        try:
            for modo in ("nativo", "prompted"):
                if modo == "nativo" and not soporta_rf:
                    modos[modo] = {"omitido": "el adaptador no soporta response_format"}
                    continue
                cuentas = dict.fromkeys(FalloEstructura, 0)
                ok_con_shim = 0
                for i in range(cfg.muestras_estructurado):
                    tarea = (
                        "Eres un revisor de código. Emite el informe de la revisión del "
                        f"commit c0ffee{i:02d} del repositorio edecan, con al menos dos "
                        "hallazgos. Responde EXCLUSIVAMENTE con el objeto JSON, sin texto "
                        "alrededor."
                    )
                    if modo == "prompted":
                        tarea += f"\n\nDebe validar contra este JSON Schema:\n{esquema_texto}"
                    respuesta, _ = await self._completar(
                        probe,
                        PeticionPerf(
                            prompt=tarea,
                            max_tokens=1600,
                            esquema_json=ESQUEMA_ESTRUCTURADO if modo == "nativo" else None,
                            etiqueta=f"{modo}#{i}",
                        ),
                    )
                    veredicto = clasificar_estructura(respuesta.content)
                    cuentas[veredicto] += 1
                    if (
                        clasificar_estructura(respuesta.content, con_shim=True)
                        is FalloEstructura.OK
                    ):
                        ok_con_shim += 1
                fiabilidades[modo] = Reliability(
                    successes=cuentas[FalloEstructura.OK], trials=cfg.muestras_estructurado
                )
                modos[modo] = {
                    "trials": cfg.muestras_estructurado,
                    "ok_estricto": cuentas[FalloEstructura.OK],
                    "json_invalido": cuentas[FalloEstructura.JSON_INVALIDO],
                    "esquema_invalido": cuentas[FalloEstructura.ESQUEMA_INVALIDO],
                    "sin_contenido": cuentas[FalloEstructura.SIN_CONTENIDO],
                    "ok_con_shim": ok_con_shim,
                    "ganancia_del_shim": ok_con_shim - cuentas[FalloEstructura.OK],
                }
        except PresupuestoAgotado as exc:
            agotado = True
            if not fiabilidades:
                return ProbeResult(
                    probe=probe,
                    capability=Capability.STRUCTURED_OUTPUT,
                    ok=False,
                    error=sanear(str(exc)),
                    evidencia=self.evidencia.rutas(probe),
                    duracion_s=self.reloj() - t_ini,
                )
        except Exception as exc:  # noqa: BLE001
            return ProbeResult(
                probe=probe,
                capability=Capability.STRUCTURED_OUTPUT,
                ok=False,
                error=sanear(f"{type(exc).__name__}: {exc}"),
                evidencia=self.evidencia.rutas(probe),
                duracion_s=self.reloj() - t_ini,
            )

        cabecera = "nativo" if "nativo" in fiabilidades else "prompted"
        fiabilidad = fiabilidades.get(cabecera)
        return ProbeResult(
            probe=probe,
            capability=Capability.STRUCTURED_OUTPUT,
            ok=fiabilidad is not None,
            valor=fiabilidad.lower_95 if fiabilidad else None,
            reliability=fiabilidad,
            detalle={
                "modo_reportado": cabecera,
                "modos": modos,
                "soporta_response_format": soporta_rf,
                "esquema": ESQUEMA_ESTRUCTURADO,
                "presupuesto_agotado": agotado,
                "gastado_usd": self.presupuesto.gastado_usd,
            },
            evidencia=self.evidencia.rutas(probe),
            error=None if fiabilidad is not None else "no se completó ninguna muestra",
            duracion_s=self.reloj() - t_ini,
        )

    # -- Sobrecarga de razonamiento -------------------------------------------- #

    async def sobrecarga_razonamiento(self) -> ProbeResult:
        """Cuánto del presupuesto de salida se va en pensar y no en responder.

        El razonamiento está siempre activo, va en `reasoning_content` y se
        factura a precio de salida: en respuestas cortas puede ser el 90 % del
        gasto. Se mide el ratio por longitud de respuesta pedida y por
        `reasoning_effort`.

        Si el proveedor no desglosa `reasoning_tokens`, el ratio en tokens sale
        `None` —no se estima— y sólo se reporta `ratio_caracteres`, que es una
        medición real de caracteres y está etiquetada como tal.
        """
        cfg = self.config
        probe = "perf.reasoning_overhead"
        t_ini = self.reloj()
        acepta_esfuerzo = bool(getattr(self.proveedor, "acepta_reasoning_effort", True))
        esfuerzos: tuple[Literal["low", "high"] | None, ...] = (
            cfg.esfuerzos if acepta_esfuerzo else (None,)
        )
        celdas: dict[str, dict[str, Any]] = {}
        agotado = False

        try:
            for objetivo in cfg.longitudes_razonamiento:
                for esfuerzo in esfuerzos:
                    clave = f"{objetivo}tok|{esfuerzo or 'defecto'}"
                    ratios: list[float] = []
                    ratios_chars: list[float] = []
                    vacios = 0
                    for i in range(cfg.muestras_razonamiento):
                        respuesta, _ = await self._completar(
                            probe,
                            PeticionPerf(
                                prompt=(
                                    "Explica qué es un contrato de aceptación ejecutable en un "
                                    f"runtime de agentes, en unos {objetivo} tokens. "
                                    f"Variante {i}."
                                ),
                                max_tokens=max(64, int(objetivo * 2)),
                                reasoning_effort=esfuerzo,
                                etiqueta=f"{clave}#{i}",
                            ),
                        )
                        if not respuesta.content.strip():
                            vacios += 1
                        rt = respuesta.uso.reasoning_tokens
                        if rt is not None:
                            visibles = max(1, respuesta.uso.completion_tokens - rt)
                            ratios.append(rt / visibles)
                        if respuesta.content:
                            ratios_chars.append(
                                len(respuesta.reasoning_content) / max(1, len(respuesta.content))
                            )
                    celdas[clave] = {
                        "tokens_objetivo": objetivo,
                        "reasoning_effort": esfuerzo,
                        "muestras": cfg.muestras_razonamiento,
                        "ratio_tokens_mediana": percentil(ratios, 0.50) if ratios else None,
                        "ratio_caracteres_mediana": (
                            percentil(ratios_chars, 0.50) if ratios_chars else None
                        ),
                        "respuestas_sin_contenido": vacios,
                    }
        except PresupuestoAgotado as exc:
            agotado = True
            if not celdas:
                return ProbeResult(
                    probe=probe,
                    capability=Capability.REASONING,
                    ok=False,
                    error=sanear(str(exc)),
                    evidencia=self.evidencia.rutas(probe),
                    duracion_s=self.reloj() - t_ini,
                )
        except Exception as exc:  # noqa: BLE001
            return ProbeResult(
                probe=probe,
                capability=Capability.REASONING,
                ok=False,
                error=sanear(f"{type(exc).__name__}: {exc}"),
                evidencia=self.evidencia.rutas(probe),
                duracion_s=self.reloj() - t_ini,
            )

        globales = [
            c["ratio_tokens_mediana"]
            for c in celdas.values()
            if c["ratio_tokens_mediana"] is not None
        ]
        return ProbeResult(
            probe=probe,
            capability=Capability.REASONING,
            ok=bool(celdas),
            valor=percentil(globales, 0.50) if globales else None,
            detalle={
                "celdas": celdas,
                "ratio_en_tokens_disponible": bool(globales),
                "acepta_reasoning_effort": acepta_esfuerzo,
                "presupuesto_agotado": agotado,
                "gastado_usd": self.presupuesto.gastado_usd,
            },
            evidencia=self.evidencia.rutas(probe),
            error=None if celdas else "no se completó ninguna celda",
            duracion_s=self.reloj() - t_ini,
        )

    # -- Visión ----------------------------------------------------------------- #

    async def vision(self) -> ProbeResult:
        """¿Lee el modelo un texto renderizado en un PNG generado al vuelo?

        Comprobación mínima y sin ambigüedad: se dibuja un código de cinco
        caracteres de un alfabeto sin parejas confundibles y se pide que lo
        transcriba. La imagen se genera aquí, así que no hay forma de que el
        modelo la conociera de antes.

        Si el adaptador no sabe mandar imágenes, el resultado es `ok=False` con
        el motivo — NO `vision=False`. "No lo pudimos preguntar" y "no ve" son
        conclusiones distintas.
        """
        cfg = self.config
        probe = "perf.vision"
        t_ini = self.reloj()
        soporta = getattr(self.proveedor, "soporta_imagenes", None)
        if soporta is False:
            return ProbeResult(
                probe=probe,
                capability=Capability.VISION,
                ok=False,
                error="el adaptador declara que no soporta imágenes: capacidad no medida",
                detalle={"motivo": "adaptador_sin_soporte_de_imagen"},
                duracion_s=self.reloj() - t_ini,
            )

        aciertos = 0
        intentos = 0
        detalles: list[dict[str, Any]] = []
        try:
            for i in range(cfg.muestras_vision):
                codigo = codigo_vision(semilla=4_100 + i)
                png = png_con_texto(codigo, escala=cfg.escala_vision)
                respuesta, _ = await self._completar(
                    probe,
                    PeticionPerf(
                        prompt=(
                            "La imagen contiene un código de 5 caracteres en mayúsculas. "
                            "Responde EXCLUSIVAMENTE con ese código, sin explicar nada."
                        ),
                        max_tokens=cfg.max_tokens_ttft,
                        imagen_png=png,
                        etiqueta=f"vision#{i}",
                    ),
                )
                intentos += 1
                leido = re.sub(r"[^A-Z0-9]", "", respuesta.content.upper())
                acerto = codigo in leido
                aciertos += int(acerto)
                detalles.append(
                    {
                        "esperado": codigo,
                        "leido": leido[:32],
                        "acierto": acerto,
                        "png_bytes": len(png),
                        "content_vacio": not respuesta.content.strip(),
                    }
                )
        except (CapacidadNoSoportada, NotImplementedError) as exc:
            return ProbeResult(
                probe=probe,
                capability=Capability.VISION,
                ok=False,
                error=sanear(f"el adaptador no pudo enviar la imagen: {exc}"),
                detalle={"motivo": "adaptador_sin_soporte_de_imagen", "intentos": intentos},
                evidencia=self.evidencia.rutas(probe),
                duracion_s=self.reloj() - t_ini,
            )
        except PresupuestoAgotado as exc:
            if intentos == 0:
                return ProbeResult(
                    probe=probe,
                    capability=Capability.VISION,
                    ok=False,
                    error=sanear(str(exc)),
                    evidencia=self.evidencia.rutas(probe),
                    duracion_s=self.reloj() - t_ini,
                )
        except Exception as exc:  # noqa: BLE001
            return ProbeResult(
                probe=probe,
                capability=Capability.VISION,
                ok=False,
                error=sanear(f"{type(exc).__name__}: {exc}"),
                detalle={"intentos": intentos},
                evidencia=self.evidencia.rutas(probe),
                duracion_s=self.reloj() - t_ini,
            )

        fiabilidad = Reliability(successes=aciertos, trials=intentos)
        # `ModelCard.vision` responde "¿lee imágenes?", no "¿las lee siempre?":
        # basta una lectura correcta de un PNG generado en el momento para
        # demostrarlo. Con qué fiabilidad lo hace está en `reliability`, y que
        # las leyera TODAS, en `leyo_todas`. Nótese que `aciertos > 0` equivale
        # a `lower_95 > 0`, así que coincide con la regla de respaldo del runner
        # y no genera conflicto al componer la tarjeta.
        return ProbeResult(
            probe=probe,
            capability=Capability.VISION,
            ok=intentos > 0,
            valor=fiabilidad.lower_95 if intentos else None,
            reliability=fiabilidad if intentos else None,
            detalle={
                "modelcard": ({"vision": aciertos > 0} if intentos else {}),
                "vision": aciertos > 0,
                "leyo_todas": intentos > 0 and aciertos == intentos,
                "lecturas": detalles,
                "alfabeto": ALFABETO_VISION,
                "gastado_usd": self.presupuesto.gastado_usd,
            },
            evidencia=self.evidencia.rutas(probe),
            error=None if intentos else "no se realizó ninguna lectura",
            duracion_s=self.reloj() - t_ini,
        )

    # -- Orquestación ------------------------------------------------------------ #

    async def ejecutar_todo(self) -> list[ProbeResult]:
        """Corre las sondas en orden de coste creciente.

        Lo barato primero: si el presupuesto se agota, se agota en el TTFT largo
        y no antes de haber medido estructura y visión, que son las que cuestan
        cuatro céntimos y responden preguntas de diseño igual de grandes.
        """
        resultados: list[ProbeResult] = []
        for sonda in (
            self.salida_estructurada,
            self.vision,
            self.sobrecarga_razonamiento,
            self.throughput,
            self.cache_prefijo,
            self.ttft,
        ):
            resultados.append(await sonda())
        return resultados
