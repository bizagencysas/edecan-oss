"""Titulares de noticias reales y frescos, verificados por fecha (`ARCHITECTURE.md` §10.14).

**El problema que resuelve.** La memoria de un modelo de lenguaje queda desactualizada por
definición: un hecho que el modelo "recuerda" de su entrenamiento (un despido, un lanzamiento,
una ronda de inversión) puede tener meses o años, y presentarlo como si fuera de esta semana
publica noticia vieja disfrazada de actualidad — cualquier lector que conozca el caso lo nota
al instante y el contenido pierde credibilidad.

**La regla que impone este módulo:** un evento con fecha (un hecho puntual, no una tesis o un
mecanismo general) solo puede citarse en el contenido generado si aparece en un titular real
devuelto por ``titulares_frescos``/``titulares_de_varias_consultas``. Si la búsqueda no
devuelve nada, quien redacta debe tratar el tema como una idea general sin ningún evento
fechable — nunca inventar una fecha ni reciclar un hecho de memoria como si fuera reciente.

**Cómo se verifica la frescura (dos capas independientes, no una).** Google News RSS ordena
por relevancia por defecto, y eso deja pasar artículos de semanas o meses atrás aunque la
consulta sea sobre "lo último" de un tema. Por eso:

1. El operador ``when:Nd`` de Google News (derivado de ``max_dias``) limita la ventana
   temporal *en la fuente*, antes de que la respuesta llegue a este proceso.
2. El código vuelve a comprobar la fecha ``pubDate`` de cada item y descarta cualquier
   resultado que la fuente haya colado fuera de esa ventana (o que no traiga una fecha
   verificable — sin fecha comprobable, nunca se presenta como actualidad).

**Sin API key.** Usa el RSS público de Google News (``news.google.com/rss/search``), igual
que cualquier búsqueda de noticias sin credenciales: no hace falta contratar nada para que un
tenant tenga esta capacidad.

**Por qué no reutiliza `edecan_toolkit.research` (`buscar_web`).** Ese módulo ya resuelve
búsqueda web genérica (páginas, no noticias) vía ``SearchProvider``
(``DuckDuckGoSearch``/``BraveSearch``/``TavilySearch``/``StubSearch``) y su ``SearchHit`` no
lleva fecha de publicación estructurada — ni el HTML accesible de DuckDuckGo ni las respuestas
que hoy consume ese módulo de Brave/Tavily la exponen. La verificación de frescura de este
módulo depende enteramente de tener una fecha por resultado, así que no hay nada equivalente
sobre lo que "añadir" el filtro: esta es una capacidad distinta (titulares de noticias con
fecha verificada) y complementaria a la búsqueda web general, no un duplicado suyo.

**Inyección de dependencias.** Ninguna función de este módulo crea su propio
``httpx.AsyncClient``: lo reciben como parámetro (mismo criterio que
``edecan_connectors``: ``base.py``, ``google/gcal.py``, ``social/linkedin.py``, etc.), así
quien llama controla el ciclo de vida y las pruebas pueden inyectar un cliente de prueba
(por ejemplo con ``respx``) sin tocar la red real.
"""

from __future__ import annotations

import asyncio
import logging
import re
import urllib.parse as _urlparse
import xml.etree.ElementTree as _ET
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

logger = logging.getLogger(__name__)

__all__ = [
    "Titular",
    "titulares_frescos",
    "titulares_de_varias_consultas",
]

_TIMEOUT = 15.0
_GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"

_MAXIMO_DEFECTO = 8
_MAXIMO_LIMITE = 30
_MAX_DIAS_DEFECTO = 3
_MAX_DIAS_LIMITE = 30

# Idioma/país por defecto de la búsqueda (parámetros `hl`/`gl`/`ceid` de Google News).
# Configurables por llamada: cada tenant puede pedir sus propios valores sin tocar código.
_IDIOMA_DEFECTO = "es-419"
_PAIS_DEFECTO = "US"


@dataclass(frozen=True, slots=True)
class Titular:
    """Un titular de noticias real con fecha de publicación ya verificada como fresca.

    ``antiguedad_horas`` y ``publicado_en`` quedan calculados una sola vez en el momento de
    la búsqueda: quien redacte contenido puede preferir lo más nuevo sin volver a parsear
    fechas. ``snippet`` es un resumen corto listo para mostrar (fuente + antigüedad legible),
    no el cuerpo de la noticia.
    """

    titulo: str
    snippet: str
    url: str
    fuente: str
    fuente_url: str
    publicado_en: str  # ISO 8601, UTC
    antiguedad_horas: float


def _acotar(valor: object, *, minimo: int, maximo: int, default: int) -> int:
    try:
        n = int(valor)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        n = default
    return max(minimo, min(maximo, n))


def _antiguedad_legible(horas: float) -> str:
    return "recién" if horas < 1 else f"hace {int(horas)} h"


def normalizar_para_dedupe(titulo: str) -> str:
    """Clave estable para detectar el mismo titular repetido entre varias consultas."""
    return re.sub(r"\W+", " ", titulo.casefold()).strip()


async def titulares_frescos(
    http: httpx.AsyncClient,
    consulta: str,
    *,
    maximo: int = _MAXIMO_DEFECTO,
    max_dias: int = _MAX_DIAS_DEFECTO,
    excluir_terms: Sequence[str] = (),
    idioma: str = _IDIOMA_DEFECTO,
    pais: str = _PAIS_DEFECTO,
    ahora: datetime | None = None,
) -> list[Titular]:
    """Titulares reales para ``consulta``, publicados dentro de los últimos ``max_dias`` días.

    Nunca lanza por fallos de red o de parseo: una fuente de noticias caída no debe tumbar la
    generación de contenido, así que ante cualquier error devuelve ``[]`` (y el llamador debe
    entenderlo como "no hay evento fechable disponible", no como error fatal). Cada resultado
    ya pasó por las dos capas de verificación de frescura descritas en el docstring del módulo.

    Args:
        http: cliente HTTP inyectado por quien llama (ver docstring del módulo).
        consulta: términos de búsqueda, en el idioma que se prefiera.
        maximo: cuántos titulares devolver como máximo (1-30).
        max_dias: ventana de frescura en días (1-30). Se aplica en la consulta a Google
            News (``when:Nd``) y otra vez en el código sobre ``pubDate``.
        excluir_terms: subcadenas (case-insensitive) que descartan un titular si aparecen en
            su título — por ejemplo, para no repetir una fuente ya usada recientemente
            (cooldown de temas, aplicado por quien orquesta la agenda de contenido).
        idioma: parámetro `hl` de Google News (idioma de la interfaz/resultados).
        pais: parámetro `gl` de Google News (país de referencia).
        ahora: instante de referencia para calcular antigüedad; por defecto el momento
            actual en UTC. Parametrizable para que las pruebas no dependan del reloj real.

    Returns:
        Lista de :class:`Titular`, más nuevos primero, hasta ``maximo`` elementos.
    """
    dias = _acotar(max_dias, minimo=1, maximo=_MAX_DIAS_LIMITE, default=_MAX_DIAS_DEFECTO)
    limite = _acotar(maximo, minimo=1, maximo=_MAXIMO_LIMITE, default=_MAXIMO_DEFECTO)
    momento_actual = ahora or datetime.now(UTC)
    excluidos = tuple(t.casefold() for t in excluir_terms if t)

    q = f"{consulta} when:{dias}d"
    url = f"{_GOOGLE_NEWS_RSS}?" + _urlparse.urlencode(
        {"q": q, "hl": idioma, "gl": pais, "ceid": f"{pais}:{idioma}"}
    )

    try:
        respuesta = await http.get(url, timeout=_TIMEOUT)
        respuesta.raise_for_status()
        raiz = _ET.fromstring(respuesta.text)
    except Exception:
        logger.warning(
            "No se pudieron obtener titulares frescos para %r; se trata como 'sin evento "
            "fechable disponible', nunca como noticia inventada.",
            consulta,
            exc_info=True,
        )
        return []

    titulares: list[Titular] = []
    for item in raiz.findall(".//item"):
        titulo = (item.findtext("title") or "").strip()
        if not titulo:
            continue
        if excluidos and any(termino in titulo.casefold() for termino in excluidos):
            continue

        pub_raw = (item.findtext("pubDate") or "").strip()
        try:
            publicado = parsedate_to_datetime(pub_raw)
        except (TypeError, ValueError, IndexError):
            continue  # sin fecha verificable: nunca se presenta como actualidad
        if publicado.tzinfo is None:
            publicado = publicado.replace(tzinfo=UTC)
        horas = (momento_actual - publicado).total_seconds() / 3600
        if horas < 0 or horas > dias * 24:
            continue  # descarta lo viejo (o con fecha corrupta) aunque la fuente lo haya colado

        fuente_nodo = item.find("source")
        fuente = ((fuente_nodo.text if fuente_nodo is not None else "") or "").strip()
        fuente_url = ((fuente_nodo.get("url") if fuente_nodo is not None else "") or "").strip()
        link = (item.findtext("link") or "").strip()

        titulares.append(
            Titular(
                titulo=titulo,
                snippet=" · ".join(p for p in (fuente, _antiguedad_legible(horas)) if p),
                url=link,
                fuente=fuente,
                fuente_url=fuente_url,
                publicado_en=publicado.isoformat(),
                antiguedad_horas=round(horas, 1),
            )
        )
        if len(titulares) >= limite:
            break

    return titulares


async def titulares_de_varias_consultas(
    http: httpx.AsyncClient,
    consultas: Sequence[str],
    *,
    maximo_por_consulta: int = 5,
    max_dias: int = _MAX_DIAS_DEFECTO,
    excluir_terms: Sequence[str] = (),
    idioma: str = _IDIOMA_DEFECTO,
    pais: str = _PAIS_DEFECTO,
    ahora: datetime | None = None,
) -> list[Titular]:
    """Lanza ``titulares_frescos`` en paralelo para cada consulta y mezcla los resultados.

    Útil cuando la agenda de contenido cubre varios temas/mercados a la vez: en vez de agotar
    un tema antes de pasar al siguiente, intercala por ronda (mejor resultado de cada consulta
    primero) y descarta duplicados — el mismo titular puede aparecer para más de una consulta.
    ``excluir_terms`` se aplica igual a todas las consultas (ver docstring de
    ``titulares_frescos``).
    """
    lotes = await asyncio.gather(
        *(
            titulares_frescos(
                http,
                consulta,
                maximo=maximo_por_consulta,
                max_dias=max_dias,
                excluir_terms=excluir_terms,
                idioma=idioma,
                pais=pais,
                ahora=ahora,
            )
            for consulta in consultas
        )
    )

    mezclados: list[Titular] = []
    vistos: set[str] = set()
    max_len = max((len(lote) for lote in lotes), default=0)
    for posicion in range(max_len):
        for lote in lotes:
            if posicion >= len(lote):
                continue
            item = lote[posicion]
            clave = normalizar_para_dedupe(item.titulo)
            if not clave or clave in vistos:
                continue
            vistos.add(clave)
            mezclados.append(item)
    return mezclados
