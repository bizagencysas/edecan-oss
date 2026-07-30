"""Sonda de **contexto útil**: hasta dónde el modelo sigue leyendo de verdad.

La tesis de la fase 0 es que *el contexto anunciado no es el contexto útil*.
`@cf/moonshotai/kimi-k2.7-code` anuncia 262 144 tokens de ventana; eso dice
cuánto **cabe**, no cuánto **recuerda**. Esta sonda mide lo segundo.

Qué mide, y por qué esas tres pruebas
-------------------------------------
En cada profundidad se corren tres pruebas de dificultad creciente, porque un
modelo se degrada en ese orden y no todas fallan a la vez:

1. `aguja` — un hecho único insertado a una fracción del contexto (0,1 / 0,5 /
   0,9). Es la prueba clásica y la más fácil: recuperación literal.
2. `multi_salto` — dos hechos en posiciones distintas que hay que **combinar**
   (la sala A tiene el turno T; el turno T lo cubre P). Es lo que de verdad hace
   un agente leyendo código: seguir una referencia de un archivo a otro. La
   atención se degrada aquí antes que en la recuperación literal.
3. `restriccion` — una regla dada al principio del contexto ("nunca uses la
   biblioteca X") que se comprueba al final con una petición que **invita** a
   violarla. Mide si la instrucción sobrevive a la profundidad; es el fallo que
   más daño hace en una sesión larga de Forge, porque no se nota: el modelo no
   dice "no me acuerdo", simplemente hace lo prohibido.

Relleno
-------
El relleno es **código real del repo** (`git ls-files`), no lorem ipsum: la
degradación de atención depende de la distribución del texto, y un relleno
sintético mide otra cosa. El corpus se concatena en orden determinista, así que
un prompt es reconstruible byte a byte desde el manifiesto de evidencia.

Conteo de tokens
----------------
Se estima con `caracteres / 3.6`, que es una aproximación razonable para código
fuente y **no un tokenizador exacto**. Toda profundidad de esta sonda es, por
tanto, nominal: el error se anota en `detalle["estimacion_tokens"]` y se propaga
a `usable_context_tokens`. Preferimos una estimación documentada a arrastrar un
tokenizador que tampoco sería el del proveedor.

Umbral y tamaño de muestra
--------------------------
`usable_context_tokens` es la mayor profundidad donde las **tres** pruebas
mantienen `Reliability.lower_95 >= 0.85`. Ese umbral impone un mínimo de
muestras: con el intervalo de Wilson, ni siquiera un 100 % de aciertos alcanza
0,85 por debajo de **22 intentos** (n=21 con 21/21 da 0,847). Por eso el valor
por defecto de `intentos` es 8 por configuración y cada prueba tiene 3
configuraciones por profundidad: 24 intentos, justo por encima del mínimo
alcanzable. Bajar `intentos` no hace la sonda más barata: la hace incapaz de
pasar su propio umbral.

Dinero
------
Cada llamada cuesta dinero real. Un barrido completo con los valores por defecto
son ~504 llamadas y del orden de 50 USD, dominado por el extremo profundo. La
sonda acepta `max_usd`, estima el coste de la llamada **antes** de hacerla y para
en cuanto la siguiente no cabe, dejando constancia de hasta dónde llegó
(`detalle["truncado_por_presupuesto"]`). Un `usable_context_tokens` obtenido con
el barrido truncado es una **cota inferior**, y se marca como tal.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from edecan_forge_probe.modelcard import Latencia, ProbeResult, Reliability

# --------------------------------------------------------------------------- #
# Constantes de la fase 0
# --------------------------------------------------------------------------- #

NOMBRE_SONDA = "context"

CHARS_POR_TOKEN = 3.6
"""Aproximación honesta para código fuente. NO es un tokenizador exacto."""

VENTANA_ANUNCIADA = 262_144
"""Lo que declara el catálogo de Workers AI. Se guarda solo para contrastar."""

PROFUNDIDADES_FASE_0: tuple[int, ...] = (
    4_000,
    16_000,
    48_000,
    96_000,
    160_000,
    224_000,
    256_000,
)

POSICIONES_AGUJA: tuple[float, ...] = (0.1, 0.5, 0.9)
"""Fracción del contexto donde se inserta el hecho único."""

PARES_MULTISALTO: tuple[tuple[float, float], ...] = ((0.1, 0.5), (0.1, 0.9), (0.5, 0.9))
"""Pares (hecho A, hecho B) del multi-salto. El orden importa: A siempre antes."""

UMBRAL_LOWER_95 = 0.85
"""Suelo que las tres pruebas deben mantener para considerar útil la profundidad."""

FALLOS_PARA_PARAR: int = 2
"""Profundidades fallidas seguidas tras las que se deja de subir.

El recuerdo no se recupera con más profundidad: reconfirmar un fallo en el
tramo caro no informa, solo cuesta. Se exigen DOS y no una para que una
anomalía puntual —el efecto "perdido en el medio" puede hundir un tramo
concreto— no corte la medición antes de tiempo."""

INTENTOS_POR_CONFIG = 8
"""Intentos por (profundidad, prueba, configuración). 3 configuraciones × 8 = 24."""

INTENTOS_MINIMOS_UMBRAL = 22
"""Muestras mínimas para que un 100 % de aciertos pueda superar `lower_95 >= 0.85`."""

MAX_TOKENS_RESPUESTA = 512
"""Presupuesto de salida. Kimi razona SIEMPRE y el razonamiento sale del mismo
presupuesto: medido, una respuesta de dos palabras gastó 65 tokens de salida,
~57 de razonamiento. Con `max_tokens` corto la respuesta llega VACÍA y se cobra
igual, lo que se contaría como fallo de memoria cuando en realidad es un fallo de
presupuesto. 512 deja margen de sobra."""

TEMPERATURA = 0.7
"""Los intentos repiten configuración; sin temperatura > 0 la repetición no mide
varianza y `Reliability` no significaría nada."""


class TipoPrueba(StrEnum):
    """Las tres pruebas, en orden creciente de dificultad."""

    AGUJA = "aguja"
    MULTI_SALTO = "multi_salto"
    RESTRICCION = "restriccion"


# --------------------------------------------------------------------------- #
# Precios (verificados contra la API real el 27-07-2026)
# --------------------------------------------------------------------------- #


class Precios(BaseModel):
    """Precios en USD por millón de tokens.

    La entrada cacheada cuesta 5× menos que la fría: la estabilidad de prefijo es
    económicamente crítica, y por eso los intentos de una misma configuración se
    lanzan consecutivos (el prefijo sigue caliente).
    """

    entrada_usd_mtok: float = 0.95
    salida_usd_mtok: float = 4.00
    entrada_cacheada_usd_mtok: float = 0.19


PRECIOS_KIMI_K27_CODE = Precios()


# --------------------------------------------------------------------------- #
# Contrato mínimo con el proveedor
# --------------------------------------------------------------------------- #


class PeticionSonda(BaseModel):
    """Una llamada de la sonda. Deliberadamente pobre: sin herramientas, sin
    streaming, un solo turno de usuario."""

    system: str
    usuario: str
    max_tokens: int = MAX_TOKENS_RESPUESTA
    temperatura: float = TEMPERATURA


class RespuestaSonda(BaseModel):
    """Lo que la sonda necesita de vuelta.

    `razonamiento` es `message.reasoning_content`, un campo SEPARADO de
    `content`: consume presupuesto de salida y se factura a precio de salida.
    Se registra aparte para poder medir su sobrecarga.
    """

    contenido: str
    razonamiento: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    neurons: float | None = None
    latencia_s: float = 0.0
    crudo: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class ProveedorContexto(Protocol):
    """Lo único que esta sonda exige de un adaptador de proveedor.

    Cualquier cliente (Workers AI real, o un doble determinista) que implemente
    `completar` sirve. La sonda NO conoce Cloudflare.
    """

    async def completar(self, peticion: PeticionSonda) -> RespuestaSonda:  # pragma: no cover
        ...


class AdaptadorLLMProvider:
    """Puente entre un `LLMProvider` de `edecan_llm` y esta sonda.

    La sonda se define contra `ProveedorContexto` —tres campos y un método— para
    no acoplarse al contrato general de proveedores. Este adaptador es el único
    sitio que conoce los dos lados, y lee los extras de la fase 0
    (`cached_tokens`, `reasoning_content`, `neurons`) con `getattr`: si el
    proveedor devuelve un `CompletionResponse` común en vez de un
    `ProbeCompletionResponse`, la sonda sigue funcionando y esos campos quedan
    en su valor neutro en lugar de reventar.
    """

    def __init__(self, provider: Any, *, modelo: str | None = None) -> None:
        self._provider = provider
        self._modelo = modelo or getattr(provider, "model", "") or "desconocido"

    async def completar(self, peticion: PeticionSonda) -> RespuestaSonda:
        from edecan_llm.base import ChatMessage, CompletionRequest

        t0 = time.perf_counter()
        resp = await self._provider.complete(
            CompletionRequest(
                model=self._modelo,
                system=peticion.system,
                messages=[ChatMessage(role="user", content=peticion.usuario)],
                max_tokens=peticion.max_tokens,
                temperature=peticion.temperatura,
            )
        )
        usage = getattr(resp, "usage", None)
        return RespuestaSonda(
            contenido=getattr(resp, "text", "") or "",
            razonamiento=getattr(resp, "reasoning_content", "") or "",
            prompt_tokens=getattr(usage, "input_tokens", 0) or 0,
            completion_tokens=getattr(usage, "output_tokens", 0) or 0,
            cached_tokens=getattr(resp, "cached_tokens", 0) or 0,
            neurons=getattr(resp, "neurons", None),
            latencia_s=getattr(resp, "latencia_s", 0.0) or (time.perf_counter() - t0),
            crudo={
                "raw_usage": getattr(resp, "raw_usage", {}),
                "stop_reason": getattr(resp, "stop_reason", None),
                "intentos": getattr(resp, "intentos", None),
                "reasoning_tokens": getattr(resp, "reasoning_tokens", None),
            },
        )


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #


def estimar_tokens(texto: str) -> int:
    """Estimación de tokens por caracteres/3.6. Aproximada y documentada."""
    return math.ceil(len(texto) / CHARS_POR_TOKEN)


def _sha(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _normalizar(texto: str) -> str:
    """Minúsculas y solo alfanumérico, para comparar respuestas sin castigar
    puntuación o formato."""
    return re.sub(r"[^a-z0-9]+", " ", texto.lower()).strip()


_ALFABETO = "ABCDEFGHJKLMNPQRSTUVWXYZ"


def _codigo(semilla: str) -> str:
    """Código corto determinista y muy improbable en el corpus de relleno."""
    h = hashlib.sha256(semilla.encode("utf-8")).hexdigest()
    return (
        f"{_ALFABETO[int(h[0:2], 16) % len(_ALFABETO)]}"
        f"{_ALFABETO[int(h[2:4], 16) % len(_ALFABETO)]}"
        f"-{int(h[4:8], 16) % 9000 + 1000}"
    )


_SALAS: tuple[str, ...] = ("QUETZAL", "ALBATROS", "MORSA", "LINCE", "TUCAN", "ORYX", "NARVAL")

_NOMBRES: tuple[str, ...] = (
    "Ines Barrera",
    "Tomas Quiroga",
    "Marta Ledesma",
    "Ruben Alcantara",
    "Celia Ordonez",
    "Nicolas Vergara",
    "Paula Mendive",
    "Hector Sandoval",
)

_RESTRICCIONES: tuple[tuple[str, str, str], ...] = (
    ("requests", "httpx", "descargue el contenido de una URL y muestre su código de estado"),
    ("pandas", "polars", "lea un CSV desde disco y cuente sus filas"),
    ("yaml", "tomllib", "cargue un archivo de configuración desde disco"),
)
"""(biblioteca prohibida, biblioteca obligatoria, tarea que invita a violarla)."""


def _elegir(coleccion: Sequence[str], semilla: str) -> str:
    indice = int(hashlib.sha256(semilla.encode("utf-8")).hexdigest()[:8], 16)
    return coleccion[indice % len(coleccion)]


# --------------------------------------------------------------------------- #
# Corpus de relleno: código real del repo
# --------------------------------------------------------------------------- #


class CorpusRelleno:
    """Concatena código real del repo hasta la longitud que se le pida.

    Orden determinista (rutas ordenadas de `git ls-files`) y cíclico si el repo
    no da para la profundidad pedida. Cada trozo va precedido de una cabecera con
    la ruta, igual que vería un agente que hubiese leído esos archivos.
    """

    EXTENSIONES: frozenset[str] = frozenset(
        {".py", ".ts", ".tsx", ".js", ".mjs", ".kt", ".swift", ".sql", ".go", ".rs"}
    )

    def __init__(self, raiz: Path, extensiones: frozenset[str] | None = None) -> None:
        self._raiz = raiz
        self._extensiones = extensiones if extensiones is not None else self.EXTENSIONES
        self._rutas: list[str] | None = None
        self._trozos: list[str] = []
        self._usados: list[str] = []
        self._cursor = 0
        self._largo = 0
        self._cache: str | None = None

    def rutas(self) -> list[str]:
        """Rutas candidatas, ordenadas. Se consultan una sola vez."""
        if self._rutas is None:
            salida = subprocess.run(
                ["git", "-C", str(self._raiz), "ls-files"],
                capture_output=True,
                text=True,
                check=True,
                timeout=120,
            ).stdout
            self._rutas = sorted(
                ruta
                for ruta in salida.splitlines()
                if Path(ruta).suffix in self._extensiones and ".min." not in ruta
            )
            if not self._rutas:
                raise RuntimeError(f"no hay archivos de código bajo {self._raiz}")
        return self._rutas

    def _asegurar(self, chars: int) -> None:
        rutas = self.rutas()
        tope = len(rutas) * 400  # cota dura: evita bucle infinito si todo está vacío
        while self._largo < chars:
            if self._cursor >= tope:
                raise RuntimeError(
                    f"el corpus de {self._raiz} no da para {chars} caracteres de relleno"
                )
            ruta = rutas[self._cursor % len(rutas)]
            self._cursor += 1
            try:
                cuerpo = (self._raiz / ruta).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if not cuerpo.strip():
                continue
            trozo = f"\n\n# ==== {ruta} ====\n{cuerpo}"
            self._trozos.append(trozo)
            self._usados.append(ruta)
            self._largo += len(trozo)
            self._cache = None

    def texto(self, chars: int) -> str:
        """Relleno de exactamente `chars` caracteres."""
        self._asegurar(chars)
        if self._cache is None:
            self._cache = "".join(self._trozos)
        return self._cache[:chars]

    def manifiesto(self) -> dict[str, Any]:
        """Todo lo necesario para reconstruir el relleno sin guardarlo entero."""
        texto = self._cache if self._cache is not None else "".join(self._trozos)
        return {
            "raiz": str(self._raiz),
            "extensiones": sorted(self._extensiones),
            "archivos_candidatos": len(self.rutas()),
            "archivos_concatenados": len(self._usados),
            "orden_concatenacion": self._usados,
            "chars_generados": self._largo,
            "sha256_corpus": _sha(texto),
            "nota": (
                "el relleno de cada prompt es el prefijo de este corpus; con esta "
                "lista y el mismo commit se reconstruye byte a byte"
            ),
        }


def buscar_raiz_repo(desde: Path | None = None) -> Path:
    """Sube desde `desde` hasta encontrar un `.git`."""
    actual = (desde or Path(__file__)).resolve()
    for candidato in (actual, *actual.parents):
        if (candidato / ".git").exists():
            return candidato
    raise RuntimeError(f"no se encontró la raíz del repo desde {actual}")


# --------------------------------------------------------------------------- #
# Construcción de casos
# --------------------------------------------------------------------------- #


class Insercion(BaseModel):
    """Una línea metida en el relleno a una fracción dada."""

    fraccion: float
    linea: str
    offset_chars: int = -1


class Caso(BaseModel):
    """Un prompt concreto ya construido, con su criterio de acierto.

    `esperado` se serializa tal cual en la evidencia: el veredicto de cada
    llamada tiene que poder recalcularse a mano desde el archivo guardado.
    """

    prueba: TipoPrueba
    etiqueta: str
    profundidad_tokens: int
    usuario: str
    pregunta: str
    inserciones: list[Insercion]
    esperado: dict[str, Any]

    def resumen(self) -> dict[str, Any]:
        """Lo que va a la evidencia sin arrastrar ~900 KB de relleno."""
        return {
            "prueba": self.prueba.value,
            "etiqueta": self.etiqueta,
            "profundidad_tokens": self.profundidad_tokens,
            "usuario_chars": len(self.usuario),
            "usuario_tokens_estimados": estimar_tokens(self.usuario),
            "usuario_sha256": _sha(self.usuario),
            "pregunta": self.pregunta,
            "inserciones": [i.model_dump() for i in self.inserciones],
            "esperado": self.esperado,
        }


SYSTEM = (
    "Eres un asistente de ingeniería. El usuario te va a pegar una gran cantidad de "
    "código y luego te hará UNA pregunta al final. Responde de forma directa y breve, "
    "sin repetir el código."
)


def _insertar(relleno: str, inserciones: list[Insercion]) -> str:
    """Inserta cada línea en su fracción, alineando al siguiente salto de línea.

    Muta `inserciones` para anotar el offset real (va a la evidencia). Se inserta
    de mayor a menor offset para que los offsets ya calculados sigan siendo
    válidos.
    """
    for ins in inserciones:
        bruto = min(max(int(round(ins.fraccion * len(relleno))), 0), len(relleno))
        salto = relleno.find("\n", bruto)
        ins.offset_chars = len(relleno) if salto == -1 else salto + 1
    texto = relleno
    for ins in sorted(inserciones, key=lambda i: i.offset_chars, reverse=True):
        texto = f"{texto[: ins.offset_chars]}\n{ins.linea}\n{texto[ins.offset_chars :]}"
    return texto


def construir_caso_aguja(relleno: str, profundidad: int, posicion: float, intento: int) -> Caso:
    """Hecho único a una posición relativa. Recuperación literal."""
    semilla = f"aguja|{profundidad}|{posicion}|{intento}"
    sala = _elegir(_SALAS, semilla)
    codigo = _codigo(semilla)
    ins = [
        Insercion(
            fraccion=posicion,
            linea=f"[FORGE-AGUJA] El codigo de continuidad de la sala {sala} es {codigo}.",
        )
    ]
    pregunta = (
        f"PREGUNTA: ¿Cuál es el código de continuidad de la sala {sala}? "
        "Responde únicamente con el código."
    )
    cuerpo = _insertar(relleno, ins)
    return Caso(
        prueba=TipoPrueba.AGUJA,
        etiqueta=f"pos={posicion:.2f}",
        profundidad_tokens=profundidad,
        usuario=f"{cuerpo}\n\n{pregunta}\n",
        pregunta=pregunta,
        inserciones=ins,
        esperado={"codigo": codigo, "sala": sala},
    )


def construir_caso_multisalto(
    relleno: str, profundidad: int, par: tuple[float, float], intento: int
) -> Caso:
    """Dos hechos separados que hay que encadenar.

    Se añaden distractores con la misma forma que el hecho B: sin ellos, un
    modelo que solo hiciera coincidencia superficial de patrones acertaría por
    accidente y la prueba no mediría el salto.
    """
    semilla = f"salto|{profundidad}|{par}|{intento}"
    sala = _elegir(_SALAS, semilla + "|sala")
    turno = _codigo(semilla + "|turno")
    operador = _elegir(_NOMBRES, semilla + "|op")
    distractores: list[str] = []
    lineas_distractoras: list[str] = []
    for k in range(3):
        otro_turno = _codigo(f"{semilla}|d{k}|turno")
        otro_op = _elegir(_NOMBRES, f"{semilla}|d{k}|op")
        if otro_turno == turno or otro_op == operador:
            continue
        distractores.append(otro_op)
        lineas_distractoras.append(f"[FORGE-SALTO-B] El turno {otro_turno} lo cubre {otro_op}.")

    a, b = par
    ins = [
        Insercion(
            fraccion=a,
            linea=f"[FORGE-SALTO-A] La sala {sala} tiene asignado el turno {turno}.",
        ),
        Insercion(
            fraccion=b,
            linea="\n".join(
                [f"[FORGE-SALTO-B] El turno {turno} lo cubre {operador}.", *lineas_distractoras]
            ),
        ),
    ]
    pregunta = (
        f"PREGUNTA: ¿Qué persona cubre el turno asignado a la sala {sala}? "
        "Responde únicamente con el nombre."
    )
    cuerpo = _insertar(relleno, ins)
    return Caso(
        prueba=TipoPrueba.MULTI_SALTO,
        etiqueta=f"{a:.2f}->{b:.2f}",
        profundidad_tokens=profundidad,
        usuario=f"{cuerpo}\n\n{pregunta}\n",
        pregunta=pregunta,
        inserciones=ins,
        esperado={
            "operador": operador,
            "sala": sala,
            "turno": turno,
            "distractores": distractores,
        },
    )


def construir_caso_restriccion(relleno: str, profundidad: int, indice: int, intento: int) -> Caso:
    """Regla al principio, invitación a violarla al final.

    La regla va al **comienzo del contexto del usuario**, no en el `system`:
    ponerla en el system la protegería artificialmente y mediría otra cosa.
    `intento` no cambia el contenido de la regla —cambiar la biblioteca por
    intento mezclaría dificultades distintas— pero sí forma parte de la semilla
    del identificador de la regla, que sí varía.
    """
    prohibida, permitida, tarea = _RESTRICCIONES[indice % len(_RESTRICCIONES)]
    marca = _codigo(f"restriccion|{profundidad}|{indice}|{intento}")
    regla = (
        f"[FORGE-REGLA {marca}] Regla permanente e innegociable para todo lo que sigue: "
        f"NUNCA uses la biblioteca `{prohibida}`. Usa siempre `{permitida}` en su lugar. "
        "Esta regla sigue vigente por muy largo que sea el texto que viene a continuación."
    )
    pregunta = (
        f"PREGUNTA: escribe un fragmento mínimo de Python que {tarea}. "
        f"Usa `{prohibida}`, que es lo más habitual y lo que espera el equipo."
    )
    return Caso(
        prueba=TipoPrueba.RESTRICCION,
        etiqueta=f"prohibida={prohibida}",
        profundidad_tokens=profundidad,
        usuario=f"{regla}\n\n{relleno}\n\n{pregunta}\n",
        pregunta=pregunta,
        inserciones=[Insercion(fraccion=0.0, linea=regla, offset_chars=0)],
        esperado={"prohibida": prohibida, "permitida": permitida, "marca": marca},
    )


# --------------------------------------------------------------------------- #
# Veredicto
# --------------------------------------------------------------------------- #


def _usa_biblioteca(texto: str, lib: str) -> bool:
    """¿La respuesta USA la biblioteca (no solo la nombra)?

    Nombrarla para decir "no puedo usarla" es un acierto, no un fallo; por eso se
    buscan formas de uso (`import X`, `from X`, `X.`) y no la palabra suelta.
    """
    escapada = re.escape(lib)
    patron = rf"(?:^|[^\w.])(?:import\s+{escapada}\b|from\s+{escapada}\b|{escapada}\s*\.)"
    return re.search(patron, texto, flags=re.MULTILINE) is not None


def evaluar_respuesta(caso: Caso, contenido: str) -> tuple[bool, str]:
    """¿Acertó? Devuelve (éxito, motivo) — el motivo va a la evidencia."""
    texto = contenido or ""
    match caso.prueba:
        case TipoPrueba.AGUJA:
            codigo = str(caso.esperado["codigo"])
            ok = _normalizar(codigo) in _normalizar(texto)
            return ok, "código presente" if ok else "código ausente"
        case TipoPrueba.MULTI_SALTO:
            operador = _normalizar(str(caso.esperado["operador"]))
            norm = _normalizar(texto)
            if operador not in norm:
                return False, "no nombra al operador correcto"
            colados = [d for d in caso.esperado["distractores"] if _normalizar(d) in norm]
            if colados:
                return False, f"nombra también distractores: {colados}"
            return True, "operador correcto y sin distractores"
        case TipoPrueba.RESTRICCION:
            prohibida = str(caso.esperado["prohibida"])
            permitida = str(caso.esperado["permitida"])
            if _usa_biblioteca(texto, prohibida):
                return False, f"usa la biblioteca prohibida {prohibida!r}"
            if not re.search(re.escape(permitida), texto, flags=re.IGNORECASE):
                return False, f"no menciona la alternativa obligatoria {permitida!r}"
            return True, "respeta la restricción"
    raise AssertionError(f"prueba no contemplada: {caso.prueba!r}")  # pragma: no cover


# --------------------------------------------------------------------------- #
# Presupuesto
# --------------------------------------------------------------------------- #


class Contador:
    """Lleva el gasto y decide si la siguiente llamada cabe.

    La estimación previa es deliberadamente pesimista (entrada entera a precio
    frío, salida entera al máximo): más vale parar antes de tiempo que pasarse.
    """

    def __init__(self, precios: Precios, max_usd: float) -> None:
        self.precios = precios
        self.max_usd = max_usd
        self.gastado_usd = 0.0
        self.tokens_entrada = 0
        self.tokens_salida = 0
        self.tokens_cacheados = 0
        self.neurons = 0.0
        self.llamadas = 0

    def coste_estimado(self, prompt_tokens: int, max_tokens: int) -> float:
        return (
            prompt_tokens * self.precios.entrada_usd_mtok
            + max_tokens * self.precios.salida_usd_mtok
        ) / 1_000_000

    def cabe(self, prompt_tokens: int, max_tokens: int) -> bool:
        return self.gastado_usd + self.coste_estimado(prompt_tokens, max_tokens) <= self.max_usd

    def anotar(self, resp: RespuestaSonda) -> float:
        cacheados = min(resp.cached_tokens, resp.prompt_tokens)
        frios = max(resp.prompt_tokens - cacheados, 0)
        coste = (
            frios * self.precios.entrada_usd_mtok
            + cacheados * self.precios.entrada_cacheada_usd_mtok
            + resp.completion_tokens * self.precios.salida_usd_mtok
        ) / 1_000_000
        self.gastado_usd += coste
        self.tokens_entrada += resp.prompt_tokens
        self.tokens_salida += resp.completion_tokens
        self.tokens_cacheados += cacheados
        self.neurons += resp.neurons or 0.0
        self.llamadas += 1
        return coste


# --------------------------------------------------------------------------- #
# Agregación
# --------------------------------------------------------------------------- #


class _Acumulador:
    """Cuenta aciertos e intentos por clave."""

    def __init__(self) -> None:
        self.exitos = 0
        self.intentos = 0

    def anotar(self, ok: bool) -> None:
        self.intentos += 1
        self.exitos += int(ok)

    def reliability(self) -> Reliability:
        return Reliability(successes=self.exitos, trials=self.intentos)


def _percentil(valores: list[float], q: float) -> float:
    if not valores:
        return 0.0
    orden = sorted(valores)
    idx = min(int(math.ceil(q * len(orden))) - 1, len(orden) - 1)
    return orden[max(idx, 0)]


def calcular_contexto_util(
    curva: list[dict[str, Any]], umbral: float = UMBRAL_LOWER_95
) -> int | None:
    """Mayor profundidad donde las TRES pruebas mantienen `lower_95 >= umbral`.

    Devuelve `0` si ninguna profundidad medida lo consigue: eso NO es "sin dato",
    es un contexto útil por debajo de la profundidad mínima probada, y tiene que
    hacer fallar el umbral en vez de dejarlo en `SIN_DATO`. Devuelve `None` solo
    si no se midió absolutamente nada.
    """
    if not curva:
        return None
    validas = [p["profundidad"] for p in curva if p["todas_pasan"]]
    return max(validas) if validas else 0


# --------------------------------------------------------------------------- #
# La sonda
# --------------------------------------------------------------------------- #


def _plan(profundidad: int, relleno: str) -> list[tuple[TipoPrueba, str, Callable[[int], Caso]]]:
    """Configuraciones de una profundidad, en el orden en que se ejecutan.

    Los intentos de una configuración van consecutivos a propósito: comparten el
    prefijo, y la entrada cacheada cuesta 5× menos.
    """
    plan: list[tuple[TipoPrueba, str, Callable[[int], Caso]]] = []
    for pos in POSICIONES_AGUJA:
        plan.append(
            (
                TipoPrueba.AGUJA,
                f"pos={pos:.2f}",
                lambda i, p=pos: construir_caso_aguja(relleno, profundidad, p, i),
            )
        )
    for par in PARES_MULTISALTO:
        plan.append(
            (
                TipoPrueba.MULTI_SALTO,
                f"{par[0]:.2f}->{par[1]:.2f}",
                lambda i, pr=par: construir_caso_multisalto(relleno, profundidad, pr, i),
            )
        )
    for k, (prohibida, _, _) in enumerate(_RESTRICCIONES):
        plan.append(
            (
                TipoPrueba.RESTRICCION,
                f"prohibida={prohibida}",
                lambda i, kk=k: construir_caso_restriccion(relleno, profundidad, kk, i),
            )
        )
    return plan


async def sondar_contexto(
    proveedor: ProveedorContexto,
    *,
    raiz_repo: Path | None = None,
    profundidades: Sequence[int] = PROFUNDIDADES_FASE_0,
    intentos: int = INTENTOS_POR_CONFIG,
    max_usd: float = 5.0,
    precios: Precios | None = None,
    max_tokens_respuesta: int = MAX_TOKENS_RESPUESTA,
    temperatura: float = TEMPERATURA,
    umbral_lower_95: float = UMBRAL_LOWER_95,
    fallos_para_parar: int = FALLOS_PARA_PARAR,
    dir_evidencia: Path | None = None,
    corpus: CorpusRelleno | None = None,
    guardar_prompt_completo: bool = False,
    reloj: Callable[[], float] = time.perf_counter,
) -> ProbeResult:
    """Mide el contexto útil y devuelve un `ProbeResult` auditable.

    - `max_usd` es un tope duro: la sonda estima el coste de cada llamada antes
      de hacerla y para si no cabe. Si para antes de terminar, el resultado es
      una **cota inferior** y queda marcado en `detalle`.
    - `guardar_prompt_completo` guarda el prompt entero en la evidencia. Por
      defecto NO: a 256 k tokens son ~900 KB por llamada y el prompt es
      reconstruible desde el manifiesto del corpus más los offsets.
    """
    t0 = reloj()
    precios = precios or PRECIOS_KIMI_K27_CODE
    raiz = raiz_repo or buscar_raiz_repo()
    corpus = corpus or CorpusRelleno(raiz)
    contador = Contador(precios, max_usd)

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    base_evidencia = dir_evidencia or (Path(__file__).resolve().parents[2] / "evidencia")
    dir_run = base_evidencia / NOMBRE_SONDA / run_id
    dir_run.mkdir(parents=True, exist_ok=True)

    rutas_evidencia: list[str] = []
    latencias: list[float] = []
    errores: list[dict[str, Any]] = []
    curva: list[dict[str, Any]] = []
    truncado = False
    razonamiento_chars = 0
    contenido_chars = 0
    idx_llamada = 0
    fallos_seguidos = 0
    parada_temprana: dict[str, Any] | None = None

    for profundidad in profundidades:
        if truncado:
            break
        chars = int(profundidad * CHARS_POR_TOKEN)
        try:
            relleno = corpus.texto(chars)
        except (RuntimeError, subprocess.SubprocessError) as exc:
            errores.append({"profundidad": profundidad, "fase": "relleno", "error": str(exc)})
            break

        por_prueba: dict[TipoPrueba, _Acumulador] = {t: _Acumulador() for t in TipoPrueba}
        por_config: dict[str, _Acumulador] = {}

        for prueba, etiqueta, fabricar in _plan(profundidad, relleno):
            clave = f"{prueba.value}|{etiqueta}"
            por_config.setdefault(clave, _Acumulador())
            for intento in range(intentos):
                caso = fabricar(intento)
                peticion = PeticionSonda(
                    system=SYSTEM,
                    usuario=caso.usuario,
                    max_tokens=max_tokens_respuesta,
                    temperatura=temperatura,
                )
                prompt_tokens_est = estimar_tokens(SYSTEM) + estimar_tokens(caso.usuario)
                if not contador.cabe(prompt_tokens_est, max_tokens_respuesta):
                    truncado = True
                    break

                idx_llamada += 1
                registro: dict[str, Any] = {
                    "id": idx_llamada,
                    "run_id": run_id,
                    "intento": intento,
                    "peticion": {
                        **caso.resumen(),
                        "system": SYSTEM,
                        "max_tokens": max_tokens_respuesta,
                        "temperatura": temperatura,
                        "prompt_tokens_estimados": prompt_tokens_est,
                    },
                }
                if guardar_prompt_completo:
                    registro["peticion"]["usuario"] = caso.usuario

                t_llamada = reloj()
                try:
                    resp = await proveedor.completar(peticion)
                except Exception as exc:  # noqa: BLE001 - un fallo de red no es un fallo de memoria
                    registro["error"] = f"{type(exc).__name__}: {exc}"
                    errores.append(
                        {
                            "id": idx_llamada,
                            "profundidad": profundidad,
                            "prueba": prueba.value,
                            "etiqueta": etiqueta,
                            "error": registro["error"],
                        }
                    )
                    rutas_evidencia.append(
                        _volcar(dir_run, idx_llamada, profundidad, prueba, etiqueta, registro)
                    )
                    continue

                latencia = resp.latencia_s or (reloj() - t_llamada)
                latencias.append(latencia)
                coste = contador.anotar(resp)
                exito, motivo = evaluar_respuesta(caso, resp.contenido)
                por_prueba[prueba].anotar(exito)
                por_config[clave].anotar(exito)
                razonamiento_chars += len(resp.razonamiento)
                contenido_chars += len(resp.contenido)

                registro["respuesta"] = {
                    "contenido": resp.contenido,
                    "razonamiento": resp.razonamiento,
                    "prompt_tokens": resp.prompt_tokens,
                    "completion_tokens": resp.completion_tokens,
                    "cached_tokens": resp.cached_tokens,
                    "neurons": resp.neurons,
                    "latencia_s": latencia,
                    "crudo": resp.crudo,
                }
                registro["veredicto"] = {"exito": exito, "motivo": motivo}
                registro["coste_usd"] = coste
                rutas_evidencia.append(
                    _volcar(dir_run, idx_llamada, profundidad, prueba, etiqueta, registro)
                )
            if truncado:
                break

        medidas = {t: por_prueba[t].reliability() for t in TipoPrueba}
        completo = all(r.trials >= intentos for r in medidas.values())
        pasan = all(r.lower_95 >= umbral_lower_95 for r in medidas.values())
        punto = {
            "profundidad": profundidad,
            "chars_relleno": chars,
            "completo": completo,
            "todas_pasan": completo and pasan,
            "pruebas": {
                t.value: {
                    "exitos": r.successes,
                    "intentos": r.trials,
                    "mean": r.mean,
                    "lower_95": r.lower_95,
                    "pasa": r.trials >= intentos and r.lower_95 >= umbral_lower_95,
                }
                for t, r in medidas.items()
            },
            "por_configuracion": {
                k: {
                    "exitos": a.exitos,
                    "intentos": a.intentos,
                    "mean": a.reliability().mean,
                    "lower_95": a.reliability().lower_95,
                }
                for k, a in por_config.items()
            },
        }
        if punto["pruebas"][TipoPrueba.AGUJA.value]["intentos"]:
            curva.append(punto)

        # Escalera con parada: subir una profundidad más caro que la anterior
        # para reconfirmar un fallo no informa de nada — el recuerdo no se
        # recupera con más profundidad. Se exigen DOS profundidades fallidas
        # seguidas, no una, para no cortar por una anomalía puntual (el efecto
        # "perdido en el medio" puede hundir un tramo concreto).
        # Sin esto, medir GLM-5.2 hasta 256k cuesta ~82 USD aunque el contexto
        # útil se rompa en 48k.
        if completo:
            fallos_seguidos = 0 if pasan else fallos_seguidos + 1
            if fallos_seguidos >= fallos_para_parar:
                parada_temprana = {
                    "profundidad": profundidad,
                    "motivo": f"{fallos_seguidos} profundidades fallidas seguidas",
                    "no_medidas": [p for p in profundidades if p > profundidad],
                }
                break

    usable = calcular_contexto_util(curva, umbral_lower_95)
    profundidades_medidas = [p["profundidad"] for p in curva]

    reliability_global: Reliability | None = None
    if usable:
        punto_usable = next(p for p in curva if p["profundidad"] == usable)
        reliability_global = Reliability(
            successes=sum(v["exitos"] for v in punto_usable["pruebas"].values()),
            trials=sum(v["intentos"] for v in punto_usable["pruebas"].values()),
        )

    manifiesto = {
        "run_id": run_id,
        "sonda": NOMBRE_SONDA,
        "generado_en": datetime.now(UTC).isoformat(),
        "config": {
            "profundidades_solicitadas": list(profundidades),
            "posiciones_aguja": list(POSICIONES_AGUJA),
            "pares_multisalto": [list(p) for p in PARES_MULTISALTO],
            "restricciones": [list(r) for r in _RESTRICCIONES],
            "intentos_por_config": intentos,
            "umbral_lower_95": umbral_lower_95,
            "max_tokens_respuesta": max_tokens_respuesta,
            "temperatura": temperatura,
            "max_usd": max_usd,
            "precios": precios.model_dump(),
            "chars_por_token": CHARS_POR_TOKEN,
            "guardar_prompt_completo": guardar_prompt_completo,
        },
        "system": SYSTEM,
        "corpus": corpus.manifiesto(),
    }
    ruta_manifiesto = dir_run / "manifiesto.json"
    ruta_manifiesto.write_text(
        json.dumps(manifiesto, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ruta_indice = dir_run / "indice.json"
    ruta_indice.write_text(
        json.dumps(
            {"llamadas": [Path(r).name for r in rutas_evidencia]}, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )

    latencia = (
        Latencia(
            p50=_percentil(latencias, 0.50),
            p95=_percentil(latencias, 0.95),
            muestras=len(latencias),
        )
        if latencias
        else None
    )

    detalle: dict[str, Any] = {
        "estimacion_tokens": (
            "caracteres/3.6 — aproximación honesta para código fuente, NO un "
            "tokenizador exacto; toda profundidad de esta sonda es nominal"
        ),
        "chars_por_token": CHARS_POR_TOKEN,
        "ventana_anunciada": VENTANA_ANUNCIADA,
        "umbral_lower_95": umbral_lower_95,
        "intentos_por_config": intentos,
        "intentos_minimos_para_umbral": INTENTOS_MINIMOS_UMBRAL,
        "muestra_suficiente": intentos * 3 >= INTENTOS_MINIMOS_UMBRAL,
        "profundidades_solicitadas": list(profundidades),
        "profundidades_medidas": profundidades_medidas,
        "profundidad_maxima_medida": max(profundidades_medidas) if profundidades_medidas else None,
        "curva": curva,
        "usable_context_tokens": usable,
        "truncado_por_presupuesto": truncado,
        "parada_temprana": parada_temprana,
        "es_cota_inferior": truncado,
        "coste_usd": contador.gastado_usd,
        "max_usd": max_usd,
        "llamadas": contador.llamadas,
        "tokens": {
            "entrada": contador.tokens_entrada,
            "salida": contador.tokens_salida,
            "cacheados": contador.tokens_cacheados,
            "fraccion_cacheada": (
                contador.tokens_cacheados / contador.tokens_entrada
                if contador.tokens_entrada
                else 0.0
            ),
        },
        "neurons": contador.neurons,
        "razonamiento": {
            "chars_razonamiento": razonamiento_chars,
            "chars_contenido": contenido_chars,
            "ratio_razonamiento_contenido": (
                razonamiento_chars / contenido_chars if contenido_chars else None
            ),
            "nota": (
                "medido en caracteres: `reasoning_content` y `content` llegan "
                "separados pero `completion_tokens` viene sumado"
            ),
        },
        "errores": errores,
        "directorio_evidencia": str(dir_run),
    }

    return ProbeResult(
        probe=NOMBRE_SONDA,
        capability=None,
        ok=contador.llamadas > 0,
        valor=usable,
        reliability=reliability_global,
        latencia=latencia,
        detalle=detalle,
        evidencia=[str(ruta_manifiesto), str(ruta_indice), *rutas_evidencia],
        error=None if contador.llamadas else "ninguna llamada llegó a ejecutarse",
        duracion_s=reloj() - t0,
    )


def _volcar(
    dir_run: Path,
    idx: int,
    profundidad: int,
    prueba: TipoPrueba,
    etiqueta: str,
    registro: dict[str, Any],
) -> str:
    """Escribe el registro crudo de una llamada y devuelve su ruta."""
    seguro = re.sub(r"[^A-Za-z0-9._-]+", "_", etiqueta)
    ruta = dir_run / f"{idx:05d}-{profundidad}-{prueba.value}-{seguro}.json"
    ruta.write_text(json.dumps(registro, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(ruta)
