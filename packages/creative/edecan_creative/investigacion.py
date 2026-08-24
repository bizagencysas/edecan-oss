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
import base64
import json
import logging
import re
import urllib.parse as _urlparse
import xml.etree.ElementTree as _ET
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

import httpx

logger = logging.getLogger(__name__)

__all__ = [
    "Titular",
    "leer_articulo",
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


# ---------------------------------------------------------------------------
# Lectura del artículo completo (puerto del hábito de REFERENCIA: "lectura del
# artículo completo cuando es posible", `linkedin_content.py`).
#
# El defecto medido que esto cierra (01-ago-2026, con capturas del dueño): el
# escritor recibía como ÚNICO material el título del RSS más un snippet que ni
# siquiera es un resumen -- es "fuente · hace N h" (ver `Titular.snippet`). Con
# eso, cualquier modelo o inventa o escribe humo: el post real salió con 461
# caracteres de deducciones, el auditor (correctamente) las recortó a 98, y el
# rescate entregó el crudo con el sello "Sin revisar". REFERENCIA, con el MISMO
# gpt-image-2 y la misma noticia, produjo un post con la fecha, los dos
# problemas concretos que resuelve el producto y a quién le sirven -- porque su
# escritor había LEÍDO el artículo. El material manda más que el modelo.
# ---------------------------------------------------------------------------

# Tope del texto extraído de un artículo. Dimensionado contra el presupuesto del
# AUDITOR (`auditoria._MAX_CONTEXTO_AUDITOR_CHARS`, 9000): la fuente elegida con
# su cuerpo (~2.800) más el banco de una cuenta real (hasta 6.000) caben juntos,
# y lo que se recorta es la cola del banco -- fail-closed, igual que siempre.
_MAX_CUERPO_ARTICULO_CHARS = 2800

# Google News y muchos medios sirven HTML distinto (o un 403) a un cliente sin
# User-Agent de navegador. El UA no personifica a nadie: es el formato estándar.
_UA_NAVEGADOR = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_TIMEOUT_ARTICULO = 12.0


def _url_real_de_google_news(url: str) -> str | None:
    """La URL del medio real escondida en un enlace `news.google.com/rss/articles/...`.

    Los enlaces del RSS de Google News no apuntan al medio: apuntan a un id
    base64 de Google. En los ids antiguos ese base64 CONTIENE la URL real y se
    puede sacar sin red; en los nuevos (cifrados) no, y el llamador cae al plan
    B (pedir la página de Google y leer el enlace de salida del HTML). Devuelve
    `None` si no se pudo, nunca lanza.
    """
    match = re.search(r"news\.google\.com/rss/articles/([^?/]+)", url)
    if not match:
        return None
    token = match.group(1)
    try:
        crudo = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except Exception:  # noqa: BLE001 - token cifrado o corrupto: plan B
        return None
    for encontrado in re.findall(rb'https?://[^\x00-\x20\x7f-\xff"\'<>\\]+', crudo):
        candidata = encontrado.decode("utf-8", "ignore")
        if "google.com" not in candidata:
            return candidata
    return None


# Enlaces de salida que NO son el artículo cuando se lee la página intermedia de
# Google News: cuentas, políticas, la propia Google.
_RE_ENLACE_DE_SALIDA = re.compile(
    r'href="(https?://(?!(?:[a-z0-9.-]*\.)?google\.com|accounts\.|policies\.|support\.|play\.)'
    r'[^"]{12,400})"'
)

# Los atributos que la página del artículo de Google News trae para su propio
# decodificador interno (`batchexecute`). Ver `_url_real_via_batchexecute`.
_RE_BATCH_SG = re.compile(r'data-n-a-sg="([^"]+)"')
_RE_BATCH_TS = re.compile(r'data-n-a-ts="([^"]+)"')

_BATCHEXECUTE_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"


async def _url_real_via_batchexecute(
    http: httpx.AsyncClient, token: str, headers: dict[str, str]
) -> str | None:
    """El decodificador REAL de los enlaces modernos de Google News.

    Los tokens nuevos (los que arrancan en "AU_yqL...") van cifrados: el base64 no
    contiene la URL (`_url_real_de_google_news` devuelve `None`) y la página intermedia
    es una app de JavaScript SIN el enlace de salida en el HTML -- el plan B de leer el
    `href` tampoco encuentra nada. Ése es exactamente el fallo que se vio en producción
    el 01-ago-2026: el post de Seedance salió otra vez con puro titular porque la
    fuente era un enlace cifrado de DiarioBitcoin vía Google News.

    Lo que SÍ trae esa página son dos atributos (`data-n-a-sg`, la firma, y
    `data-n-a-ts`, el timestamp) que alimentan el endpoint interno `batchexecute` de la
    propia Google -- el mismo mecanismo que usan los decodificadores públicos
    (googlenewsdecoder y compañía). Con firma + timestamp + el id del artículo, el
    endpoint responde la URL real del medio. Mejor esfuerzo como todo este módulo: si
    Google cambia el formato mañana, esto devuelve `None` y se degrada a titular, nunca
    lanza.
    """
    try:
        pagina = await http.get(
            f"https://news.google.com/articles/{token}",
            timeout=_TIMEOUT_ARTICULO,
            headers=headers,
            follow_redirects=True,
        )
        pagina.raise_for_status()
        sg = _RE_BATCH_SG.search(pagina.text)
        ts = _RE_BATCH_TS.search(pagina.text)
        if not sg or not ts:
            return None
        articulo = json.dumps(
            [
                "garturlreq",
                [
                    [
                        "X",
                        "X",
                        ["X", "X"],
                        None,
                        None,
                        1,
                        1,
                        "US:en",
                        None,
                        1,
                        None,
                        None,
                        None,
                        None,
                        None,
                        0,
                        1,
                    ],
                    "X",
                    "X",
                    1,
                    [1, 1, 1],
                    1,
                    1,
                    None,
                    0,
                    0,
                    None,
                    0,
                ],
                token,
                int(ts.group(1)),
                sg.group(1),
            ]
        )
        payload = {"f.req": json.dumps([[["Fbv4je", articulo, None, "generic"]]])}
        respuesta = await http.post(
            _BATCHEXECUTE_URL,
            data=payload,
            timeout=_TIMEOUT_ARTICULO,
            headers={**headers, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        )
        respuesta.raise_for_status()
        # La respuesta es un protocolo propio: líneas con arrays JSON anidados; la URL
        # real viaja dentro de un string JSON del segundo nivel. Se extrae por regex en
        # vez de parsear el protocolo entero: menos superficie que se rompa.
        match = re.search(r'\\"(https?://[^\\"]+)\\"', respuesta.text) or re.search(
            r'"(https?://(?!(?:[a-z0-9.-]*\.)?google\.com)[^"]{12,400})"', respuesta.text
        )
        if not match:
            return None
        candidata = match.group(1)
        return None if "google.com" in candidata else candidata
    except Exception:  # noqa: BLE001 - decodificador opcional: sin URL se sigue con titular
        return None


class _ExtractorDeParrafos(HTMLParser):
    """Junta el texto de los `<p>` del artículo, ignorando script/style/nav/etc.

    Parser de la librería estándar a propósito: este paquete no depende de
    BeautifulSoup y un extractor "suficiente" que viaja siempre le gana a uno
    perfecto que hay que instalar. Igual que REFERENCIA: mejor esfuerzo -- si el
    HTML es raro, se pierde el cuerpo y el escritor sigue con el titular, que
    es exactamente lo que había antes.
    """

    _IGNORAR = {"script", "style", "noscript", "svg", "nav", "footer", "aside", "form", "figure"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parrafos: list[str] = []
        self._en_parrafo = False
        self._ignorando = 0
        self._actual: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._IGNORAR:
            self._ignorando += 1
        elif tag == "p" and not self._ignorando:
            self._en_parrafo = True
            self._actual = []

    def handle_endtag(self, tag: str) -> None:
        if tag in self._IGNORAR and self._ignorando:
            self._ignorando -= 1
        elif tag == "p" and self._en_parrafo:
            self._en_parrafo = False
            texto = " ".join("".join(self._actual).split())
            # Párrafos cortos = menús, créditos de foto, "suscríbete". El cuerpo
            # de un artículo real casi nunca baja de esta medida.
            if len(texto) >= 60:
                self.parrafos.append(texto)

    def handle_data(self, data: str) -> None:
        if self._en_parrafo and not self._ignorando:
            self._actual.append(data)


def _texto_de_articulo(html: str, max_chars: int) -> str:
    extractor = _ExtractorDeParrafos()
    try:
        extractor.feed(html)
    except Exception:  # noqa: BLE001 - HTML roto: se usa lo que alcanzó a juntar
        pass
    texto = "\n".join(extractor.parrafos)
    if len(texto) <= max_chars:
        return texto
    recortado = texto[:max_chars]
    corte = recortado.rfind("\n")
    return (recortado[:corte] if corte > max_chars // 2 else recortado).rstrip()


async def leer_articulo(
    http: httpx.AsyncClient,
    url: str,
    *,
    max_chars: int = _MAX_CUERPO_ARTICULO_CHARS,
) -> str:
    """El cuerpo del artículo en texto plano, o `""` si no se pudo leer.

    Mejor esfuerzo y sin excepciones, como todo este módulo: una noticia que no
    se deja leer degrada al comportamiento anterior (solo titular), nunca tumba
    la generación. El orden de resolución para los enlaces de Google News:
    (1) la URL real dentro del base64 del enlace, sin red; (2) el decodificador
    `batchexecute` para los tokens cifrados modernos (el caso REAL de producción,
    ver `_url_real_via_batchexecute`); (3) seguir redirecciones HTTP; (4) leer la
    página intermedia de Google y sacar el enlace de salida del HTML.
    """
    try:
        headers = {"User-Agent": _UA_NAVEGADOR, "Accept-Language": "es-419,es;q=0.9"}
        destino = _url_real_de_google_news(url)
        if destino is None:
            match_token = re.search(r"news\.google\.com/rss/articles/([^?/]+)", url)
            if match_token:
                destino = await _url_real_via_batchexecute(http, match_token.group(1), headers)
        respuesta = await http.get(
            destino or url, timeout=_TIMEOUT_ARTICULO, headers=headers, follow_redirects=True
        )
        respuesta.raise_for_status()
        if "news.google.com" in str(respuesta.url):
            match = _RE_ENLACE_DE_SALIDA.search(respuesta.text)
            if not match:
                return ""
            respuesta = await http.get(
                match.group(1), timeout=_TIMEOUT_ARTICULO, headers=headers, follow_redirects=True
            )
            respuesta.raise_for_status()
        return _texto_de_articulo(respuesta.text, max_chars)
    except Exception:  # noqa: BLE001 - la lectura es opcional; el titular ya se tiene
        logger.info("No se pudo leer el artículo %r; se sigue solo con el titular.", url)
        return ""


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
