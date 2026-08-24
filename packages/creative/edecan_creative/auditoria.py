"""Auditoría anti-invención y control de calidad editorial (segunda pasada) para
contenido social generado por Edecán (`ARCHITECTURE.md` §10.14).

Este módulo es GENÉRICO: ninguna regla aquí menciona una persona, una empresa, un
país o una cuenta en particular. Todo lo específico de un tenant (su voz, su banco
de contexto real, sus temas prohibidos) vive en su perfil editorial por-tenant --
ver `edecan_creative.social.get_editorial_profile` -- nunca en este archivo.

Expone controles independientes, portados y generalizados de un pipeline
editorial propio. Los dos primeros REPARAN el borrador; los dos últimos lo
JUZGAN, y son los que había que dejar de usar como primera puerta:

- `pulir_borrador`: el editor jefe que REESCRIBE el borrador completo (texto,
  tema, ángulo, promesa y copy visual) con el prompt humanizador de
  `editorial.PROMPT_EDITOR_HUMANIZADOR`, y remata con una pasada dedicada a
  quitar los delatores de plantilla IA que hayan quedado. Es la pieza que
  convierte la mayoría de los rechazos de estilo en correcciones.
- `reparar_delatores`: esa segunda pasada, expuesta aparte por si un llamador la
  necesita sola.
- `reescribir_sin_primera_persona`: el último cinturón contra la autobiografía.
- `auditar_hechos`: verifica cada cifra, fecha y afirmación fechable de un texto
  contra las fuentes que se le pasaron, y lo corrige o lo veta si no se sostiene.
  Falla cerrado (fail-closed): ante la duda, se descarta antes que dejar pasar
  una invención. Soporta `escenas_ilustrativas_autorizadas` para el tenant que
  autorizó explícitamente en su perfil (`social.perfil_autoriza_escenas_ilustrativas`)
  ilustrar su argumento con una escena anónima y genérica -- sin fecha, cifra,
  nombre real ni cita -- sin exigirle el mismo anclaje literal que a un hecho
  verificable. Por defecto sigue igual de estricto que siempre.
- `revisar_calidad`: un editor jefe que sólo JUZGA -- devuelve `(publicable,
  motivo)` -- y puede rechazar un borrador correcto pero mediocre o genérico
  ("saltarse un slot protege mejor la cuenta que publicar algo artificial").
  Sigue siendo el control adecuado para un texto que el usuario DICTÓ y que la
  herramienta no puede reescribir en su nombre (`crear_contenido_social`); para
  un texto que el motor escribió él mismo, `pulir_borrador` es el correcto,
  porque ahí sí se puede arreglar en vez de devolver un turno perdido. Soporta
  un modo `permisivo` para cuando el propio usuario pidió explícitamente el
  tema: ahí se desactiva el rechazo por gusto editorial (interesante/soso, tema
  amplio, etc.) y solo sobreviven los rechazos por reglas duras (el texto no se
  sostiene, está vacío o es ilegible).

Ninguna de las dos llama a un proveedor de modelo concreto: ambas reciben
`llamar_modelo` por inyección de dependencia (mismo criterio que
`edecan_creative.providers.ImageProvider` para imágenes) -- quien las use decide
qué modelo real invocar, con qué credencial y bajo qué tenant.

--------------------------------------------------------------------------------
LECCIÓN CRÍTICA A PORTAR (aprendida a la mala en el pipeline original: un
borrador reintrodujo un rango de puntaje y cerró con una pregunta retórica
FORMULA porque el auditor factual reescribió el texto con un prompt genérico
que no conocía esas reglas de estilo):

`auditar_hechos` y, en menor medida, `revisar_calidad` piden al modelo que
REESCRIBA el texto. Cualquier gate determinista que ya se le aplicó a la
versión ANTERIOR a estas funciones (primera persona, cierre en pregunta,
plantillas delatoras de IA -- ver `edecan_creative.editorial.revisar`) NO
protege el texto que estas funciones devuelven: la reescritura puede
reintroducir, sin querer, una violación que ya se había limpiado. Un texto que
pasó los gates deterministas ANTES de pasar por este módulo puede perfectamente
NO pasarlos DESPUÉS.

Por eso los gates deterministas deben RE-APLICARSE sobre el texto FINAL,
después de este módulo, nunca solo una vez al principio. `finalizar_post` (más
abajo) es la orquestación de referencia que deja esto ya resuelto: encadena
`revisar_calidad` -> `auditar_hechos` -> vuelve a correr
`edecan_creative.editorial.revisar` sobre el resultado. Un llamador que arme su
propia orquestación en vez de usar `finalizar_post` debe reproducir ese
re-chequeo final él mismo.
--------------------------------------------------------------------------------
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from .editorial import (
    PROMPT_EDITOR_HUMANIZADOR,
    _tiene_primera_persona,
    delatores_de_estilo,
    normalizar_visual,
    revisar_visual,
)
from .editorial import normalizar as _normalizar_texto
from .editorial import revisar as _revisar_gates_deterministas

logger = logging.getLogger(__name__)

__all__ = [
    "Fuente",
    "LlamarModelo",
    "auditar_hechos",
    "marcas_de_hecho_duro",
    "pulir_borrador",
    "reparar_delatores",
    "reescribir_sin_primera_persona",
    "revisar_calidad",
    "finalizar_post",
]

LlamarModelo = Callable[[list[dict[str, str]]], Awaitable[str]]
"""Firma inyectada para hablar con un modelo de lenguaje.

Recibe una lista de mensajes de chat (``{"role": "system"|"user", "content": "..."}``,
el mismo shape mínimo que `edecan_llm.base.ChatMessage`) y devuelve el texto de la
respuesta ya extraído (sin streaming, sin tool calls, sin reintentos -- eso es
responsabilidad de quien implemente la función real). Este módulo nunca importa
un proveedor concreto (`edecan_llm`, OpenAI, Anthropic, Ollama...): quien lo use
decide qué modelo invocar, con qué credencial y bajo qué tenant.
"""

Fuente = Mapping[str, Any]
"""Una fuente citable: al menos ``title`` y ``url``; ``snippet`` opcional aporta el
fragmento de texto real que sostiene los hechos. Mismo shape que ya acepta
`edecan_creative.social.CrearContenidoSocialTool` (campo ``fuentes``), para que
el llamador no tenga que reformatear nada."""

# Límites defensivos sobre lo que se inyecta en un prompt: un caller no
# controlado (fuentes larguísimas, un texto pegado por error) nunca debe volar
# el contexto del modelo ni el costo de la llamada.
_MAX_CONTEXTO_CHARS = 3500
# Presupuesto propio del AUDITOR, mayor que el general. Se midió el defecto: con un banco de
# contexto de 5.789 caracteres (24 escenas enumeradas), el tope compartido de 3.500 cortaba el
# material a mitad de la escena 5 -- las escenas 6 a 24 no le llegaban NUNCA, y una afirmación
# salida de ahí se vetaba como invención con el motivo "no se menciona en las fuentes". El
# escritor, en cambio, ve el banco entero (`social._CONTEXT_BANK_PROMPT_LIMIT`, 12.000). Auditar
# con menos material del que tuvo el escritor es auditar a ciegas; este número cierra esa
# asimetría sin volverse ilimitado.
# 14000 desde el 02-ago-2026 (era 9000): los jueces corren en el modelo "profundo"
# (contexto de 262k), así que el tope ya no protege a un modelo chico -- solo decidía
# qué parte del material se le ESCONDÍA al auditor. Con el banco editorial v5 de una
# cuenta real (7.9k) más el artículo leído (2.8k), 9000 volvía a cortar la cola del
# banco: el mismo defecto medido que ya obligó a subirlo una vez.
_MAX_CONTEXTO_AUDITOR_CHARS = 14000
_MAX_TEXTO_CHARS = 1800
# Mínimo para que un texto sea EVALUABLE. No es el mínimo para ENTREGARLO, que es bastante más
# alto y lo fija quien orquesta (`redaccion.MIN_COPY_ENTREGABLE_CHARS`). Los dos números miden
# cosas distintas a propósito: acá basta con que haya frases que auditar; allá tiene que haber
# un post que una persona pueda publicar sin que la tarjeta se vea rota.
_MIN_TEXTO_CHARS = 50
_MAX_TEXTO_FINAL_CHARS = 1600

_SIN_FUENTES_MARCADOR = (
    "(SIN FUENTES: solo se permite razonamiento general; cero eventos, nombres, cifras o fechas)"
)


def _extraer_json(raw: str) -> Any:
    """Extrae el primer objeto JSON balanceado de una respuesta de modelo.

    Un modelo a veces envuelve el JSON en \\`\\`\\`json ... \\`\\`\\` o agrega una frase
    antes/después. Se busca la primera ``{`` y se cuentan llaves hasta balancear,
    en vez de un regex greedy (``\\{.*\\}``) que puede sobre-capturar cuando hay
    texto con llaves después del bloque real.
    """
    if not raw:
        return None
    inicio = raw.find("{")
    if inicio == -1:
        return None
    profundidad = 0
    for indice in range(inicio, len(raw)):
        caracter = raw[indice]
        if caracter == "{":
            profundidad += 1
        elif caracter == "}":
            profundidad -= 1
            if profundidad == 0:
                try:
                    # `strict=False` a propósito: un modelo pequeño (nemotron,
                    # gemma) suele emitir el post con saltos de línea LITERALES
                    # dentro del string `"texto"` (los párrafos), y `json.loads`
                    # estricto los rechaza como caracteres de control -> el JSON
                    # se daba por "roto" y el turno moría con "No devolviste el
                    # JSON". Con `strict=False` esos saltos internos se aceptan;
                    # sigue siendo el MISMO objeto balanceado, no captura de más.
                    return json.loads(raw[inicio : indice + 1], strict=False)
                except json.JSONDecodeError:
                    return None
    return None


def _formatear_fuentes(fuentes: Sequence[Fuente] | None, limite: int | None = None) -> str:
    """Numera las fuentes como ``[F1]``, ``[F2]``... para que el auditor pueda
    aislar cuál sostiene cada afirmación. Sin fuentes, deja el marcador SIN
    FUENTES: en ese modo solo se permite razonamiento general, cero eventos,
    nombres propios, cifras o fechas (la "memoria" del modelo es vieja por
    definición y nunca es una fuente válida).

    `limite` permite darle al AUDITOR un presupuesto propio, más grande que el de los
    llamadores que sólo necesitan contexto de estilo (ver `_MAX_CONTEXTO_AUDITOR_CHARS`):
    el auditor es el único que tiene que comprobar frase por frase, así que es el único al
    que recortarle el material lo vuelve ciego -- una afirmación que sí estaba respaldada,
    pero en la parte que se cortó, se veta como invención.
    """
    if not fuentes:
        return _SIN_FUENTES_MARCADOR
    bloques: list[str] = []
    for indice, fuente in enumerate(fuentes, start=1):
        titulo = str(fuente.get("title") or fuente.get("titulo") or "").strip()
        url = str(fuente.get("url") or "").strip()
        snippet = str(fuente.get("snippet") or fuente.get("contenido") or "").strip()
        encabezado = f"[F{indice}] {titulo}" + (f" ({url})" if url else "")
        bloques.append(f"{encabezado}\n{snippet}" if snippet else encabezado)
    return "\n\n".join(bloques)[: limite or _MAX_CONTEXTO_CHARS]


# ---------------------------------------------------------------------------
# LA LÍNEA QUE EL MODO "ESCENAS ILUSTRATIVAS" NO PUEDE CRUZAR, ESCRITA EN CÓDIGO
# ---------------------------------------------------------------------------
#
# Aflojar un auditor de hechos con una FRASE EN EL PROMPT no es aflojarlo de forma
# verificable: no se puede demostrar qué deja pasar un modelo de 17B, sólo observarlo. Y
# esto publica en la página de una empresa real. Así que la excepción no vive en el prompt:
# vive acá, en una función determinista que se puede leer, probar y auditar sin encender un
# modelo. `marcas_de_hecho_duro` responde una sola pregunta -- "¿este texto tiene la FORMA de
# algo que se pueda inventar?" -- y el modo permisivo se aplica ÚNICAMENTE cuando la
# respuesta es "no hay ni una marca". Cualquier marca, aunque sea un solo dígito, devuelve el
# auditor a su comportamiento estricto de siempre.
#
# Cada categoría de abajo mapea 1:1 con la lista de invenciones que siguen siendo imposibles:
#
#   cifra/porcentaje/estadística/estudio  -> "cifra" + "cantidad"
#   cita textual                          -> "cita"
#   noticia/fecha/ronda/alianza           -> "fecha", "suceso" y "verbo de suceso"
#   persona o entidad REAL y nombrada     -> "nombre propio"
#   afirmación falsa sobre la empresa     -> "promesa/regulación" + "nombre propio"
#
# Todas se detectan sobre el texto plano (sin tildes, en minúsculas) con listas CERRADAS de
# palabras, nunca con heurísticas semánticas: falso positivo (marca de más) sólo cuesta
# volver al modo estricto, que es exactamente el statu quo; falso negativo sería el daño
# real, y por eso las listas pecan de amplias a propósito.
_RE_DIGITO = re.compile(r"\d")

_RE_COMILLAS = re.compile('["«»“”„‟]')

# Números y magnitudes ESCRITOS EN LETRAS. Imprescindible, no cosmético: un perfil puede
# exigir (y el de la cuenta que motivó esto exige) "cero números de dos o más dígitos, todo
# en palabras", así que un `\d` solo no detecta ni la mitad de las cifras inventables.
_RE_CANTIDAD = re.compile(
    r"\b("
    # "un/una/unos" quedan FUERA: en español son el artículo indefinido ("una libreta"), no
    # una cantidad. Un numeral real siempre trae la magnitud detrás ("unos veinte") y esa sí
    # está en la lista.
    r"dos|tres|cuatro|cinco|seis|siete|ocho|nueve|die[zc]|once|doce|trece|catorce|"
    r"quince|dieci\w+|veinte|veinti\w+|treinta|cuarenta|cincuenta|sesenta|setenta|ochenta|"
    r"noventa|cien|ciento|cientos|mil|miles|millon|millones|millardo\w*|"
    r"mitad|tercio|doble|triple|cuadruple|docena\w*|decena\w*|centenar\w*|"
    r"porciento|porcentaje\w*|promedio\w*|media|mediana|tasa\w*|indice\w*|"
    r"estadistica\w*|encuesta\w*|sondeo\w*|censo\w*|muestreo\w*|estudio\w*|informe\w*|"
    # Cuantificadores vagos: "miles de personas", "la mayoría paga puntual". No traen dígitos
    # pero SÍ afirman una magnitud del mundo, y el encargo dice "sin cifras" sin distinguir si
    # van en número o en palabra. Se quedan del lado estricto a propósito.
    r"mayoria|minoria|dos tercios|tres cuartos"
    r")\b"
)

_RE_POR_CIENTO = re.compile(r"\bpor\s+ciento\b|%")

# Fechas y actualidad. "hoy"/"mañana" quedan FUERA a propósito: son adverbios de uso diario
# en prosa genérica ("hoy en día no hay cómo mostrarlo") y no fechan nada por sí solos --
# lo que sí fecha es un mes, un año o un anclaje al pasado reciente, y eso sí está acá.
_RE_FECHA = re.compile(
    r"\b("
    r"enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|"
    r"noviembre|diciembre|"
    r"ayer|anteayer|anoche|"
    r"recientemente|ultimamente|acaba de|acaban de|acaba n de|"
    r"la semana pasada|el mes pasado|el ano pasado|este ano|el ano que viene|"
    r"en los ultimos|desde el ano|hasta el ano|trimestre\w*|semestre\w*"
    r")\b"
)

# Sucesos que sólo existen si una fuente los dice: noticias, rondas, alianzas, procesos
# legales, actos regulatorios. Ninguno de estos puede aparecer en una escena cotidiana sin
# estar afirmando un hecho del mundo, así que se miden sobre el TEXTO entero.
_RE_SUCESO = re.compile(
    r"\b("
    r"anunci\w*|lanzamiento\w*|comunicado\w*|rueda de prensa|"
    r"ronda\w*|inversion\w*|inversor\w*|adquisicion\w*|fusion\w*|alianza\w*|convenio\w*|"
    r"demanda\w*|denuncia\w*|investigacion\w*|sancion\w*|multa\w*|veredicto\w*|fallo\w*|"
    r"sentencia\w*|"
    r"ley|leyes|decreto\w*|gaceta|resolucion\w*|normativa\w*|reglament\w*"
    r")\b"
)

# Palabras con las que se afirma algo falso SOBRE UNA EMPRESA: que es un banco, que está
# regulada, que garantiza una aprobación. Éstas NO se miden sobre el texto entero, sino por
# frase y sólo si la frase además nombra a alguien -- porque para afirmar algo falso sobre una
# empresa hay que nombrarla. "Los bancos no ven ese historial" es una observación genérica de
# cómo funciona el crédito y es justo de lo que esta clase de cuenta habla todo el tiempo;
# "Acme es un banco" es la afirmación prohibida. Medirlas sobre el texto entero mataba la
# segunda a costa de la primera, y con ella casi todos los posts reales.
_RE_AFIRMACION_SOBRE_ENTIDAD = re.compile(
    r"\b("
    r"regulad\w*|regulacion\w*|supervisad\w*|licencia\w*|autorizad\w*|habilitad\w*|"
    r"banco\w*|bancari\w*|institucion financiera|entidad financiera|prestamista\w*|"
    r"garantiz\w*|garantia\w*|aprobacion\w*|aprueb\w*|aprobad\w*"
    r")\b"
)

# Verbos que casi siempre encierran una afirmación sobre lo que alguien HIZO o DIJO. Un
# pretérito de estos con un nombre propio en la misma frase es la forma canónica de una
# noticia inventada ("Cashea cambió sus condiciones", "el Banco Central subió la tasa").
_RE_VERBO_SUCESO = re.compile(
    r"\b("
    r"dijo|dijeron|afirmo|afirmaron|aseguro|aseguraron|declaro|declararon|sostuvo|"
    r"senalo|indico|explico|revelo|admitio|nego|negaron|confirmo|confirmaron|"
    r"anuncio|anunciaron|lanzo|lanzaron|presento|presentaron|firmo|firmaron|"
    r"adquirio|compro|compraron|vendio|vendieron|invirtio|invirtieron|cerro|cerraron|"
    r"acuso|acusaron|prohibio|prohibieron|autorizo|autorizaron|aprobo|aprobaron|"
    r"sanciono|sancionaron|multo|multaron|demando|demandaron|renuncio|renunciaron|"
    r"quebro|quebraron|fallecio|murio|gano|ganaron|perdio|perdieron"
    r")\b"
)

# Pretérito perfecto simple de 3ª persona: `-ó`, `-aron`, `-ieron`. Una escena ilustrativa se
# escribe en presente genérico ("el bodeguero fía", "quien paga puntual"); un hecho del mundo
# se cuenta en pasado puntual. No basta por sí solo -- se exige que en la MISMA frase haya un
# nombre propio --, porque "cuando llegó la quincena" es escena, no noticia.
_RE_PRETERITO = re.compile(r"\b\w+(?:ó|aron|ieron)\b", re.IGNORECASE)

_RE_PALABRA = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ]*")

# Conectores en minúscula que pueden ir DENTRO de un nombre propio compuesto ("Banco de
# Venezuela", "Cámara de Comercio", "Pago Móvil" no los necesita).
_CONECTORES_NOMBRE = frozenset({"de", "del", "la", "las", "los", "el", "y", "e"})

# Palabras que abren una frase en español SIN ser un nombre propio. Sirven para una sola
# cosa: decidir si la mayúscula del principio de una frase es ortografía o es un nombre. Sólo
# se consulta en la regla de "pasado + nombre propio", donde equivocarse hacia el lado
# estricto no cuesta nada (se vuelve al auditor de siempre) y equivocarse hacia el otro lado
# dejaría pasar "Cashea cambió sus condiciones" como si fuera una escena. Por eso la lista es
# de función pura (artículos, preposiciones, conjunciones, pronombres, demostrativos): si una
# frase empieza con algo que no está acá, se asume nombre propio.
_APERTURAS_COMUNES = frozenset(
    """
    el la los las un una unos unas lo al del
    en con por para sin sobre entre desde hasta hacia tras segun durante
    y e o u ni pero aunque porque pues como cuando donde mientras si sino
    que quien quienes cual cuales cuanto cuanta cuantos cuantas
    este esta estos estas ese esa esos esas aquel aquella aquellos aquellas esto eso aquello
    su sus tu tus mi mis nuestro nuestra su suyo
    hay habia hubo es son era eran ser estar hacer
    no nada nadie ningun ninguna alguno alguna algo alguien todo toda todos todas cada
    ahora antes despues luego siempre nunca casi tambien tampoco solo solamente
    aqui alli alla asi ademas incluso apenas mientras
    a de
    """.split()
)

_FIN_DE_FRASE = ".!?:;\n\r"


def _plano(texto: str) -> str:
    """Minúsculas sin tildes ni diéresis, para comparar contra listas cerradas."""
    descompuesto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


def _frases(texto: str) -> list[str]:
    return [f for f in re.split(r"[.!?\n\r]+", texto) if f.strip()]


def _nombres_propios_no_anclados(
    texto: str, vocabulario: frozenset[str], fuentes_plano: str
) -> list[str]:
    """Nombres propios del texto que las fuentes NO respaldan.

    Dos reglas, y las dos hacen falta:

    - Una palabra capitalizada A MITAD DE FRASE es un nombre propio en español (la posición
      inicial no dice nada: cualquier palabra se capitaliza ahí). Si su forma plana no
      aparece en las fuentes, nadie la respalda.
    - Un nombre COMPUESTO -- dos o más palabras capitalizadas seguidas, con conectores en
      minúscula permitidos en el medio -- se revisa ENTERO, porque es la forma de un nombre y
      apellido ("Juan Pérez") o de una institución ("Banco Central de Venezuela"). Que sus
      piezas sueltas estén en las fuentes no basta: se exige que la secuencia completa
      aparezca ahí. Un nombre así que empiece la frase se detecta igual, porque la revisión
      arranca en la segunda palabra ("Juan Pérez" -> se revisa "Pérez"; "El Banco Central de
      Venezuela" -> se revisa "Banco Central de Venezuela").
    """
    encontrados: list[str] = []
    for frase in _frases(texto):
        tokens = list(_RE_PALABRA.finditer(frase))
        for indice, token in enumerate(tokens):
            palabra = token.group(0)
            if not palabra[0].isupper():
                continue
            if indice == 0:
                # Inicio de frase: la mayúscula es ortografía, no un nombre propio. No se
                # revisa ni suelta ni como cabeza de un compuesto -- si no, "Con Acme"
                # se leería como una institución llamada "Con Acme".
                continue
            plano = _plano(palabra)
            # Nombre compuesto: mira hacia adelante saltando conectores en minúscula.
            piezas = [palabra]
            siguiente = indice + 1
            while siguiente < len(tokens):
                candidata = tokens[siguiente].group(0)
                if candidata[0].isupper():
                    piezas.append(candidata)
                    siguiente += 1
                    continue
                if _plano(candidata) in _CONECTORES_NOMBRE and siguiente + 1 < len(tokens):
                    if tokens[siguiente + 1].group(0)[0].isupper():
                        piezas.append(candidata)
                        siguiente += 1
                        continue
                break
            if len(piezas) > 1:
                compuesto = _plano(" ".join(piezas))
                if compuesto not in fuentes_plano:
                    encontrados.append(" ".join(piezas))
                continue
            if plano not in vocabulario:
                encontrados.append(palabra)
    return encontrados


def marcas_de_hecho_duro(texto: str, fuentes_formateadas: str = "") -> list[str]:
    """Marcas de HECHO DURO presentes en `texto`: lo que haría falta para poder inventar algo.

    Devuelve la lista (posiblemente vacía) de motivos por los que este texto NO es una escena
    ilustrativa pura. **Lista vacía significa que el texto es estructuralmente incapaz de
    contener ninguna de las invenciones prohibidas**: sin cifras (ni en dígitos ni en
    palabras), sin comillas, sin fechas, sin verbos de suceso, sin promesas ni afirmaciones
    regulatorias, y sin un solo nombre propio que las fuentes no respalden. Lo que queda
    cuando no hay nada de eso es exactamente lo que un perfil puede autorizar: una escena
    cotidiana, anónima y genérica, en presente, que ilustra un argumento.

    `fuentes_formateadas` es el bloque de fuentes tal como lo ve el auditor (`_formatear_fuentes`):
    su vocabulario es lo que "ancla" un nombre propio. Sin fuentes, cualquier nombre propio a
    mitad de frase cuenta como no anclado.

    Determinista y sin modelo a propósito: es la única forma de poder AFIRMAR qué deja pasar
    el modo permisivo en vez de esperar que un modelo obedezca una frase del prompt.
    """
    texto = texto or ""
    if not texto.strip():
        return ["texto vacío"]
    plano = _plano(texto)
    marcas: list[str] = []

    if _RE_DIGITO.search(texto):
        marcas.append("cifra en dígitos")
    if _RE_POR_CIENTO.search(plano):
        marcas.append("porcentaje")
    coincidencia = _RE_CANTIDAD.search(plano)
    if coincidencia:
        marcas.append(f"cantidad o medida en palabras ('{coincidencia.group(0)}')")
    if _RE_COMILLAS.search(texto):
        marcas.append("comillas (posible cita textual)")
    coincidencia = _RE_FECHA.search(plano)
    if coincidencia:
        marcas.append(f"fecha o actualidad ('{coincidencia.group(0)}')")
    coincidencia = _RE_SUCESO.search(plano)
    if coincidencia:
        marcas.append(f"suceso o acto regulatorio ('{coincidencia.group(0)}')")
    for frase in _frases(texto):
        coincidencia = _RE_AFIRMACION_SOBRE_ENTIDAD.search(_plano(frase))
        if not coincidencia:
            continue
        tokens = [t.group(0) for t in _RE_PALABRA.finditer(frase)]
        nombra_a_alguien = any(t[0].isupper() for t in tokens[1:]) or (
            bool(tokens) and tokens[0][0].isupper() and _plano(tokens[0]) not in _APERTURAS_COMUNES
        )
        if nombra_a_alguien:
            marcas.append(
                f"afirmación sobre una entidad nombrada ('{coincidencia.group(0)}')",
            )
            break
    coincidencia = _RE_VERBO_SUCESO.search(plano)
    if coincidencia:
        marcas.append(f"verbo de declaración o suceso ('{coincidencia.group(0)}')")

    fuentes_plano = _plano(fuentes_formateadas or "")
    vocabulario = frozenset(_RE_PALABRA.findall(fuentes_plano))
    nombres = _nombres_propios_no_anclados(texto, vocabulario, fuentes_plano)
    if nombres:
        marcas.append(f"nombre propio que las fuentes no respaldan ({', '.join(nombres[:3])})")

    # Pretérito puntual + nombre propio en la MISMA frase = la forma de una noticia. Se mide
    # por frase para no castigar "cuando llegó la quincena" en un post que además nombra al
    # país en otra línea. El nombre cuenta esté donde esté, incluida la primera palabra: la
    # forma canónica de una noticia inventada es justo esa ("Cashea cambió sus condiciones"),
    # y ahí la mayúscula inicial sí es un nombre. Para distinguirla de la mayúscula ortográfica
    # se usa `_APERTURAS_COMUNES`, que peca de corta a propósito.
    for frase in _frases(texto):
        if not _RE_PRETERITO.search(frase):
            continue
        tokens = [t.group(0) for t in _RE_PALABRA.finditer(frase)]
        for posicion, token in enumerate(tokens):
            if not token[0].isupper():
                continue
            if posicion == 0 and _plano(token) in _APERTURAS_COMUNES:
                continue
            # Sujeto + verbo: el pretérito tiene que venir PEGADO al nombre, no en cualquier
            # parte de la frase. Sin esta ventana, "Anota el nombre y lo que se llevaron" (una
            # escena en presente con una subordinada en pasado) contaría como noticia.
            if any(_RE_PRETERITO.fullmatch(t) for t in tokens[posicion + 1 : posicion + 4]):
                marcas.append(f"hecho en pasado atribuido a un nombre propio ('{token}')")
                break
        if marcas and marcas[-1].startswith("hecho en pasado"):
            break

    return marcas


async def reescribir_sin_primera_persona(
    texto: str,
    fuentes: Sequence[Fuente] | None,
    llamar_modelo: LlamarModelo,
) -> str:
    """Último cinturón: convierte la autobiografía en criterio impersonal.

    Portado del motor de LinkedIn de REFERENCIA (`_reescribir_sin_primera_persona`),
    que es donde se demostró que hace falta. La diferencia era exactamente esta:
    ante un borrador en primera persona, REFERENCIA lo REPARA y Edecán solo lo
    RECHAZABA. Con un escritor que insiste en "yo/mi/hice" —el fallo más común—
    todos los intentos morían en la misma puerta determinista y el turno
    terminaba sin post, que es justo lo que el usuario vivía como "no funciona".

    Devuelve el texto corregido, o `""` si no se pudo arreglar (el modelo no
    respondió, o lo que devolvió TODAVÍA tiene primera persona). Nunca lanza:
    quien llama decide qué hacer con un `""`.
    """
    if not texto:
        return ""
    if not _tiene_primera_persona(texto):
        return texto

    try:
        crudo = await llamar_modelo(
            [
                {
                    "role": "system",
                    "content": (
                        "Eres un editor literal. Elimina TODA primera persona y toda "
                        "experiencia atribuida al dueño del perfil. Conserva la tesis y "
                        "los hechos, expresando el criterio como afirmaciones directas "
                        "sobre el mundo. No agregues hechos nuevos ni cambies el sentido. "
                        "Responde SOLO con el texto final, sin comillas ni explicaciones."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"FUENTES PERMITIDAS:\n{_formatear_fuentes(fuentes) or '(sin fuentes)'}"
                        f"\n\nTEXTO A CORREGIR:\n{texto[:_MAX_CONTEXTO_CHARS]}"
                    ),
                },
            ]
        )
    except Exception:  # noqa: BLE001 - reparar es best-effort; el caller ya tiene un plan B
        logger.warning("no se pudo reescribir sin primera persona", exc_info=True)
        return ""

    corregido = _normalizar_texto(str(crudo or "").strip().strip('"').strip())
    # Si el editor dejó primera persona, no sirvió: mejor devolver vacío que
    # colar como "corregido" un texto que viola el gate igual.
    return corregido if corregido and not _tiene_primera_persona(corregido) else ""


_ESQUEMA_EDITOR = (
    'Devuelve SOLO JSON válido con esta forma exacta: {"publicable": true|false, '
    '"motivo": "una frase breve y específica", "tema": "2 a 6 palabras", '
    '"angulo": "la idea central concreta en una frase", '
    '"promesa_lector": "por qué le importa a alguien ajeno, en una frase", '
    '"texto": "el post completo ya reescrito", '
    '"visual": {"kicker": "...", "headline": "...", "accent": "...", "support": "..."}}. '
    "No expliques nada fuera del JSON."
)

# Variante POLEMICA: exige un campo `postura` -- la frase de una línea que TOMA LADO. Es la
# pieza que convierte la postura de "sugerencia en prosa que el modelo ignora" a "deliverable
# obligatorio que el modelo TIENE que articular para que el post sea válido". El gate
# determinista de `pulir_borrador` rechaza el borrador si esta field viene vacía: así no
# queda al arbitrio del modelo decidir si "reescribe tomando lado" o no.
_ESQUEMA_EDITOR_POLEMICO = (
    'Devuelve SOLO JSON válido con esta forma exacta: {"publicable": true|false, '
    '"motivo": "una frase breve y específica", "tema": "2 a 6 palabras", '
    '"angulo": "la idea central concreta en una frase", '
    '"postura": "la frase de UNA línea que TOMA LADO: el juicio que la mitad del gremio '
    "disputaría, anclado en lo que la fuente SÍ dice pero que ella no afirma por sí sola -- "
    'nunca un resumen ni una descripción", '
    '"promesa_lector": "por qué le importa a alguien ajeno, en una frase", '
    '"texto": "el post completo ya reescrito, que ENTREGA esa postura (típicamente en el '
    'último párrafo, como juicio de cierre)", '
    '"visual": {"kicker": "...", "headline": "...", "accent": "...", "support": "..."}}. '
    "No expliques nada fuera del JSON."
)


def _esquema_editor(postura_polemica: bool = False) -> str:
    """El esquema JSON que el editor jefe debe devolver. En modo polemico exige el campo
    `postura` -- ver `_ESQUEMA_EDITOR_POLEMICO` y el gate determinista de `pulir_borrador`."""
    return _ESQUEMA_EDITOR_POLEMICO if postura_polemica else _ESQUEMA_EDITOR


_SISTEMA_EDITOR_JEFE = (
    "Eres el editor jefe de un perfil profesional en redes sociales. Tu trabajo es dejar el "
    "borrador PUBLICABLE reescribiéndolo, no juzgarlo desde la barrera. No simulas la voz "
    "personal del dueño de la cuenta, no agregas hechos y no inventas primera persona ni "
    "autobiografía: eso está prohibido sin excepciones. PULIR ES MEJORAR TONO Y PRECISIÓN, "
    "NUNCA UNA EXCUSA PARA ACORTAR: 'silencio > ruido' significa quitar frases vacías o "
    "redundantes, jamás recortar el desarrollo real del candidato ni resumirlo. Si el "
    "mensaje del usuario trae un largo obligatorio para esta cuenta, tu versión final tiene "
    "que seguir cumpliéndolo -- devolver un texto más corto que el candidato, cuando ese "
    "candidato ya cumplía el largo pedido, es un fallo tuyo, no una mejora.\n\n"
    + PROMPT_EDITOR_HUMANIZADOR
)

_SISTEMA_CORRECTOR_DELATORES = (
    "Eres un corrector de última pasada. Conservas EXACTAMENTE los hechos y eliminas señales "
    "de escritura generada por IA. Cero primera persona y cero autobiografía.\n\n"
    + PROMPT_EDITOR_HUMANIZADOR
)


def _headline_propio(datos: Any) -> bool:
    """¿Este objeto trae un `visual.headline` escrito por un modelo (y no degradado por
    nosotros)? Decide si vale la pena someter el copy visual al detector."""
    if not isinstance(datos, Mapping):
        return False
    visual = datos.get("visual")
    if not isinstance(visual, Mapping):
        return False
    return bool(str(visual.get("headline") or "").strip())


def _borrador_publicable(datos: Any, previo: Mapping[str, Any]) -> dict[str, Any] | None:
    """Toma la respuesta del editor y la deja lista, cayendo al borrador previo campo a campo.

    Devuelve ``None`` si el editor no entregó un texto usable: quien llama decide si eso es
    un descarte o (en permisivo) motivo para conservar el borrador que ya tenía.
    """
    if not isinstance(datos, dict):
        return None
    texto = _normalizar_texto(str(datos.get("texto") or ""))
    if not texto:
        return None
    nuevo = dict(previo)
    nuevo["texto"] = texto
    for campo in ("tema", "angulo", "postura", "promesa_lector", "motivo"):
        valor = str(datos.get(campo) or previo.get(campo) or "").strip()
        if valor:
            nuevo[campo] = valor
    visual_nuevo = datos.get("visual")
    visual_base = visual_nuevo if isinstance(visual_nuevo, Mapping) else previo.get("visual")
    nuevo["visual"] = normalizar_visual(
        visual_base if isinstance(visual_base, Mapping) else None,
        tema=str(nuevo.get("tema") or ""),
        titular=str(previo.get("titular_visual") or ""),
    )
    nuevo["publicable"] = datos.get("publicable") is not False
    return nuevo


async def pulir_borrador(
    borrador: Mapping[str, Any],
    contexto: str,
    llamar_modelo: LlamarModelo,
    *,
    permisivo: bool = False,
    plataforma: str = "linkedin",
    sin_fuentes: bool = False,
    instruccion_formato: str = "",
    cooldown_estructura: str = "",
    borradores_recientes: str = "",
    requisito_largo: str = "",
    postura_polemica: bool = False,
) -> dict[str, Any] | None:
    """Editor jefe que REESCRIBE el borrador completo (texto + tema + ángulo + visual).

    Port de ``_revisar_calidad_editorial`` (REFERENCIA, ``linkedin_content.py:1417-1548``), y la
    pieza de mayor palanca de todo el port. **La diferencia no era de umbral, era
    estructural**: :func:`revisar_calidad` sólo devuelve ``(bool, motivo)``, o sea sólo sabe
    decir sí o no, mientras el editor de REFERENCIA devuelve un borrador NUEVO. Por eso en REFERENCIA
    el escritor puede entregar algo imperfecto y el post sale igual, y en Edecán el escritor
    tenía que acertar de primera -- con el modelo más débil de los dos y trece gates
    esperándolo. Este pase convierte la mayoría de los rechazos actuales en correcciones.

    Encadena dos cosas, en el orden de REFERENCIA:

    1. El editor jefe reescribe con :data:`PROMPT_EDITOR_HUMANIZADOR` como system. Ese prompt
       existía en ``editorial.py`` y NADIE lo importaba: el editor juzgaba a ciegas respecto
       de las señales que el detector le iba a medir después.
    2. Si el detector encuentra delatores en el resultado, :func:`reparar_delatores` hace UNA
       llamada más para quitarlos. Si no puede y ``permisivo``, se conserva igual.

    ``permisivo=True`` (el usuario pidió el tema): ``publicable=false`` se IGNORA y, si el
    editor no devuelve texto usable, se conserva el borrador original en vez de descartarlo.

    ``requisito_largo``, cuando se pasa (típicamente el ``target_length`` del perfil del
    tenant), se inyecta como un requisito EXPLÍCITO y prominente, igual que
    ``instruccion_formato``. Sin esto medido, el editor jefe recortaba el candidato a la
    mitad o menos incluso cuando ya cumplía el largo que la cuenta pedía: nada en su prompt
    mencionaba el largo, así que "pulir" sólo tenía presión en una dirección (acortar, por
    "silencio > ruido") y ninguna en la otra. El `target_length` SÍ viajaba dentro de
    ``contexto`` (ver ``social._contexto_calidad``), pero mezclado con `purpose`/`audience`/
    `voice`/`avoid` sin ningún marco de "esto es un requisito, no una preferencia" -- un
    editor que recibe diez líneas de contexto y una sola instrucción de estilo ("cada frase
    se gana su lugar") sigue la instrucción que sí es una orden.

    Devuelve el borrador pulido, o ``None`` cuando hay que descartarlo (sólo posible fuera de
    permisivo). Nunca lanza.
    """
    texto_previo = _normalizar_texto(str(borrador.get("texto") or ""))
    if not texto_previo:
        return None
    previo = dict(borrador)
    previo["texto"] = texto_previo

    # `contexto` es el bloque de juicio que arma el llamador (plataforma, tema, perfil, títulos
    # de fuentes, respaldo), así que nunca llega vacío: si el modo sin fuentes se dedujera de
    # `not contexto` no se activaría jamás. Por eso el llamador lo declara explícito.
    modo_sin_fuentes = (
        "Este borrador es de criterio y deliberadamente no tiene fuentes citables. No lo "
        "rechaces sólo por eso: reescríbelo como un mecanismo, una restricción o un marco de "
        "decisión que no afirme hechos externos fechables. "
        if sin_fuentes
        else ""
    )
    modo_permisivo = (
        "IMPORTANTE: el usuario PIDIÓ EXPLÍCITAMENTE este tema, así que tu trabajo es PULIRLO "
        "hasta dejarlo publicable, NO descartarlo. PROHIBIDO usar publicable=false por 'poco "
        "interesante', 'demasiado amplio' o 'sectorial': si el tema es amplio, elige el ángulo "
        "más concreto que permita la fuente y escribe sobre ESO. Sólo podrías rechazarlo si "
        "fuera imposible sin inventar hechos, y aun así prefiere reescribir. Devuelve "
        "publicable=true. "
        if permisivo
        else ""
    )
    # Postura editorial declarada por la cuenta (ver `social.perfil_autoriza_stance_polemica`).
    # A diferencia de `modo_permisivo`, esto SÍ puede llevar a publicable=false. Y a diferencia
    # de una instrucción en prosa que el modelo ignora, aquí hay DOS palancas: (1) el campo
    # `postura` es OBLIGATORIO en el JSON de salida -- el modelo TIENE que articular un stance
    # para que el post sea válido; (2) un gate determinista en `pulir_borrador` rechaza el
    # borrador si `postura` viene vacía. La polémica va anclada en la fuente (el auditor de
    # hechos sigue intacto y veta cualquier invención); el editor jefe sólo exige que el
    # `angulo` sea una postura, no un resumen. Medido en vivo: con sólo la instrucción en
    # prosa, el editor jefe reescribía quedándose neutral y marcaba publicable=true; con el
    # campo obligatorio + el gate, no puede entregar sin articular el stance.
    if postura_polemica:
        modo_postura = (
            "POSTURA EDITORIAL DE ESTA CUENTA: POLÉMICA. Esta cuenta TOMA UN LADO. Un post que "
            "sólo describe el paisaje sin juzgarlo VIOLA la postura y debe ser publicable=false.\n"
            "ENTREGABLE OBLIGATORIO: tu JSON incluye un campo `postura` -- UNA frase que toma "
            "posición, el juicio que la mitad del gremio disputaría, anclado en lo que la "
            "fuente SÍ dice pero que ella no afirma por sí sola. El `texto` ENTREGA esa postura "
            "(típicamente como el último párrafo, un juicio de cierre que deja al lector con un "
            "lado que escoger). Si no puedes articular una postura que la fuente respalde sin "
            "inventar, entrega la lectura MÁS afilada que la fuente sí soporta -- pero nunca un "
            "resumen neutral: un post tibio es un fallo de esta cuenta.\n"
            "EJEMPLO del estándar (sobre un tema suave, 'la IA desplaza empleo'):\n"
            "  NEUTRO (prohibido, publicable=false): 'Las nuevas tecnologías transforman el "
            "empleo y exigen reentrenar a la fuerza laboral.'  -- describe, no toma lado.\n"
            "  POLÉMICO (lo que pide esta cuenta): postura='No hay escasez de talento: hay "
            "pereza corporativa para pagar lo que cuesta formarlo.' -- el texto entrega ESE "
            "juicio, anclado en la cifra de vacantes que la fuente sí trae.\n"
            "La postura NUNCA inventa hechos (el auditor de hechos los veta igual): ancla el "
            "juicio en lo que la fuente SÍ dice y elige el lado más incómodo que el dato permita. "
        )
    else:
        modo_postura = ""
    candidato = json.dumps(
        {
            "tema": str(previo.get("tema") or ""),
            "fuente_principal": str(previo.get("fuente_principal") or ""),
            "angulo": str(previo.get("angulo") or ""),
            "promesa_lector": str(previo.get("promesa_lector") or ""),
            "texto": previo["texto"],
            "visual": previo.get("visual") if isinstance(previo.get("visual"), Mapping) else {},
        },
        ensure_ascii=False,
    )

    requisito_largo_bloque = (
        f"LARGO OBLIGATORIO DE ESTA CUENTA (no lo recortes): {requisito_largo}. Pulir un post "
        "es mejorar tono, concreción y quitar plantillas de IA -- nunca acortar el desarrollo. "
        "Conserva todas las escenas, ejemplos y párrafos del candidato salvo que debas quitar "
        "un hecho inventado o una violación dura. Si el candidato ya cumple ese largo, tu "
        "versión final tiene que seguir cumpliéndolo; si el candidato quedó corto, DESARRÓLLALO "
        "más (otro momento concreto, una consecuencia distinta) en vez de resumirlo todavía "
        "más.\n\n"
        if requisito_largo
        else ""
    )

    mensajes = [
        {"role": "system", "content": _SISTEMA_EDITOR_JEFE},
        {
            "role": "user",
            "content": (
                f"{modo_postura}"
                + (f"FORMA EXIGIDA: {instruccion_formato}\n\n" if instruccion_formato else "")
                + requisito_largo_bloque
                + "Reescribe el candidato si su interés depende de una falsa revelación, de un "
                "'no es X, es Y', de un grupo forzado de tres, de frases apiladas para fabricar "
                "drama o de una sentencia final. Aplica primero la PRUEBA DEL SCROLL: alguien "
                "que no conoce la empresa ni el sector debe entender en diez segundos por qué "
                "esto le afecta. La primera línea debe contener el detalle más específico y no "
                "anunciar que algo importante viene después. El hecho se cuenta UNA vez en "
                "máximo dos frases y no se re-narra: el resto aporta una restricción, un costo, "
                "una decisión o una explicación concreta. Las cifras no son una cuota: cero "
                "cuando la idea funciona sin ellas. El titular visual debe nombrar literalmente "
                "el hecho o la consecuencia, sin metáforas, y entenderse SIN leer el post. "
                "Piensa tres aperturas y tres titulares en silencio y elige los más concretos, "
                "no los más dramáticos. No agregues hechos, cifras, citas, empresas ni "
                "desenlaces.\n\n"
                f"{modo_sin_fuentes}"
                f"{modo_permisivo}"
                f"{cooldown_estructura}\n\n"
                "CONTEXTO Y MATERIAL PERMITIDO (no agregues ningún hecho que no esté aquí):\n"
                + (contexto or "(sin fuentes; no agregues hechos fechables)")[:_MAX_CONTEXTO_CHARS]
                + (
                    f"\n\nBORRADORES RECIENTES QUE NO DEBES IMITAR (ni en arquitectura, no sólo "
                    f"en tema):\n{borradores_recientes[:_MAX_RECIENTES_CHARS]}"
                    if borradores_recientes
                    else ""
                )
                + (
                    f"\n\nCANDIDATO:\n{candidato[:_MAX_CONTEXTO_CHARS]}\n\n"
                    f"{_esquema_editor(postura_polemica)}"
                )
            ),
        },
    ]

    try:
        raw = await llamar_modelo(mensajes)
    except Exception:  # noqa: BLE001 - pulir es best-effort; el caller decide el plan B
        logger.warning("pulir_borrador: llamar_modelo falló.", exc_info=True)
        return previo if permisivo else None

    datos = _extraer_json(raw)
    pulido = _borrador_publicable(datos, previo)
    if pulido is None:
        # El editor no devolvió texto usable. En permisivo se conserva el borrador original
        # (el usuario lo pidió y está esperando); fuera de permisivo se descarta.
        return previo if permisivo else None
    if not pulido.pop("publicable", True) and not permisivo:
        return None
    # GATE DETERMINISTA DE POSTURA (ver `social.perfil_autoriza_stance_polemica`). En modo
    # polemico el editor jefe DEBE devolver un campo `postura` no vacío: es la prueba de que
    # articuló un stance en vez de reescribir neutral. Sin esa field, el borrador es un post
    # tibio y se rechaza igual que si el editor hubiera dicho publicable=false -- la cuenta
    # declaró que lo neutral no le sirve. Medido en vivo: sin este gate, el editor reescribía
    # quedándose neutral y marcaba publicable=true; el campo obligatorio lo forcea a decidir
    # un lado, y el gate lo hace cumplir sin fiarse de que el modelo se autorrechace.
    if postura_polemica and not str(pulido.get("postura") or "").strip():
        logger.info(
            "pulir_borrador: modo polemico activo pero el editor devolvió `postura` vacía "
            "-- se rechaza el borrador neutral."
        )
        return previo if permisivo else None

    hallazgos = delatores_de_estilo(str(pulido["texto"]), plataforma)
    # El visual sólo se somete al detector si ALGÚN modelo escribió de verdad un titular propio.
    # Cuando `normalizar_visual` tuvo que degradarlo al tema, sus "defectos" (menos de tres
    # palabras, por ejemplo) son obra NUESTRA, no del modelo: gastar una llamada de corrección
    # pidiéndole arreglar algo que él no escribió es tirar tiempo del chat, y fuera de permisivo
    # además arriesga descartar el borrador por eso.
    if _headline_propio(datos) or _headline_propio(previo):
        visual_final = pulido["visual"]
        hallazgos += revisar_visual(
            kicker=str(visual_final.get("kicker", "")),
            headline=str(visual_final.get("headline", "")),
            support=str(visual_final.get("support", "")),
        )
    if hallazgos:
        reparado = await reparar_delatores(
            pulido,
            hallazgos,
            contexto,
            llamar_modelo,
            plataforma=plataforma,
            requisito_largo=requisito_largo,
        )
        if reparado is not None:
            pulido = reparado
        elif not permisivo:
            return None
        # permisivo: no se pudieron limpiar los delatores -> se conserva el texto pulido. El
        # usuario revisa antes de publicar, que es infinitamente mejor que no recibir nada.
    return pulido


# Cuánto texto real de borradores anteriores ve el editor jefe. REFERENCIA pasa hasta 8 posts de
# 700 caracteres (`_borradores_recientes_contexto`) para detectar clones de ARQUITECTURA, no
# sólo de tema: un post con tema nuevo y molde idéntico pasaba limpio.
_MAX_RECIENTES_CHARS = 5000


async def reparar_delatores(
    borrador: Mapping[str, Any],
    hallazgos: Sequence[str],
    contexto: str,
    llamar_modelo: LlamarModelo,
    *,
    plataforma: str = "linkedin",
    requisito_largo: str = "",
) -> dict[str, Any] | None:
    """Segunda pasada dedicada a QUITAR los delatores de plantilla IA que se encontraron.

    Port de la pasada de corrección de ``_revisar_calidad_editorial`` (REFERENCIA,
    ``linkedin_content.py:1511-1544``). El problema real que resuelve: los mismos diez
    patrones de ``editorial._TEXT_TELLS`` que en REFERENCIA disparan una reescritura de diez
    segundos, en Edecán sólo producían rechazo -- y como el único reparador cableado era
    :func:`reescribir_sin_primera_persona`, que ante un texto SIN primera persona devuelve el
    texto INTACTO, el intento se quemaba sin haber intentado nada. Un "ahí está" en una frase
    secundaria tiraba un post entero que por lo demás estaba bien.

    Se le pasan los mensajes accionables de ``editorial.delatores_de_estilo`` /
    ``revisar_visual``, que son mejor insumo que la lista de nombres de reglas de REFERENCIA.

    Devuelve el borrador corregido, o ``None`` si no se pudo (el modelo falló, no devolvió
    texto, marcó ``publicable=false``, o su "corrección" sigue trayendo delatores).
    """
    if not hallazgos:
        return dict(borrador)
    detalle = "\n".join(f"- {h}" for h in hallazgos)
    requisito_largo_linea = (
        f"LARGO OBLIGATORIO DE ESTA CUENTA (no lo recortes al corregir): {requisito_largo}.\n\n"
        if requisito_largo
        else ""
    )
    mensajes = [
        {"role": "system", "content": _SISTEMA_CORRECTOR_DELATORES},
        {
            "role": "user",
            "content": (
                "El detector encontró estas señales de escritura generada por IA:\n"
                f"{detalle}\n\n"
                "Reescribe el texto Y el titular visual para eliminarlas DE VERDAD, no para "
                "sustituirlas por otra fórmula equivalente. Conserva exactamente los mismos "
                "hechos y el mismo tema; no agregues nada. Si no puedes hacerlo sin perder el "
                "único punto interesante del post, usa publicable=false.\n\n"
                f"{requisito_largo_linea}"
                f"FUENTES PERMITIDAS:\n{(contexto or '(sin fuentes)')[:_MAX_CONTEXTO_CHARS]}\n\n"
                f"BORRADOR:\n{json.dumps(dict(borrador), ensure_ascii=False)[:4500]}\n\n"
                f"{_ESQUEMA_EDITOR}"
            ),
        },
    ]
    try:
        raw = await llamar_modelo(mensajes)
    except Exception:  # noqa: BLE001 - reparar es best-effort
        logger.warning("reparar_delatores: llamar_modelo falló.", exc_info=True)
        return None

    reparado = _borrador_publicable(_extraer_json(raw), borrador)
    if reparado is None or not reparado.pop("publicable", True):
        return None
    # Si la "corrección" volvió a caer en un delator, no sirvió: mejor devolver None y dejar
    # que el caller decida (en permisivo conserva el texto anterior) que colar como corregido
    # un texto que el detector va a marcar igual.
    if delatores_de_estilo(str(reparado["texto"]), plataforma):
        return None
    return reparado


async def auditar_hechos(
    texto: str,
    fuentes: Sequence[Fuente] | None,
    llamar_modelo: LlamarModelo,
    *,
    solo_titulares: bool = False,
    escenas_ilustrativas_autorizadas: bool = False,
) -> tuple[str, list[str]]:
    """Auditor anti-invención: verifica cada cifra, fecha y afirmación fechable de
    `texto` contra `fuentes`, la única fuente de verdad permitida. Falla cerrado:
    ante cualquier duda, corrige o veta antes que dejar pasar una invención.

    Reglas duras que aplica (generalizadas de un pipeline propio; nada aquí es
    específico de un tenant):

    - Sin fuentes, el texto no puede afirmar ningún evento, cifra, nombre propio
      o fecha concreta -- solo razonamiento general o mecanismos estables.
    - MEMORIA VIEJA POR DEFINICIÓN: un evento (lanzamiento, sanción, cifra,
      despido...) que el modelo "recuerda" de su entrenamiento no es actualidad;
      solo existe si aparece en `fuentes`. Presentarlo como reciente sin una
      fuente real es publicar con una fecha falsa.
    - CERO DESENLACES INVENTADOS: si una fuente describe algo SIN RESOLVER (una
      denuncia, demanda, investigación o reclamo), el texto corregido JAMÁS
      afirma que hubo sanción, fallo, multa, veredicto o confirmación que esa
      fuente no confirma -- sin importar cuán probable parezca el desenlace.

    Devuelve `(texto_final, problemas)`:

    - Si el texto ya cumplía o se pudo corregir sin perder la tesis central,
      `texto_final` es el texto (posiblemente reescrito) y `problemas` lista lo
      que se encontró y corrigió (vacía si no había nada que corregir).
    - Si no se puede sostener sin inventar, `texto_final` es `""` -- cadena
      vacía es la señal de veto total (fail-closed) -- y `problemas` siempre
      trae al menos un motivo.

    `solo_titulares=True` avisa que las "fuentes" son titulares + snippet de un
    buscador, nunca el cuerpo del artículo. Es el tercer modo del auditor de
    REFERENCIA (`linkedin_content.py:1380-1385`): un titular NO autoriza a deducir
    mecanismos, causas, efectos ni detalles que el titular no dice. Se porta
    como INSTRUCCIÓN, no como veto automático: Edecán es un atajo del chat y no
    puede pagar los segundos de leer el artículo completo, así que endurecer el
    veto aquí sólo produciría más turnos sin post sobre el mismo material.

    `escenas_ilustrativas_autorizadas=True` (por defecto `False`, mismo
    comportamiento fail-closed de siempre) declara que el PERFIL de esta cuenta
    autorizó ilustrar el argumento con escenas cotidianas anónimas (ver
    `social.perfil_autoriza_escenas_ilustrativas`). Por sí solo no afloja nada:
    es una de DOS condiciones, y la segunda la decide el código, no el modelo.

    - **Condición 1 (el perfil).** Este parámetro, que viene de un campo
      dedicado del perfil editorial del tenant.
    - **Condición 2 (el texto).** `marcas_de_hecho_duro(texto, fuentes)` tiene
      que devolver la lista VACÍA: ni un dígito, ni una cantidad escrita en
      palabras, ni un porcentaje, ni una comilla, ni un mes o un anclaje al
      pasado reciente, ni una palabra de suceso/regulación/promesa, ni un verbo
      de declaración, ni un nombre propio que las fuentes no respalden, ni un
      hecho en pasado atribuido a un nombre propio.

    Sólo con las dos se entra en el modo escena pura. Lo que cambia ahí es
    exactamente esto y nada más:

    - El prompt pasa de "reconstruye el texto con hechos de las fuentes" a un
      VEREDICTO: publicable=false sólo ante una afirmación falsa sobre la
      empresa o sus productos, una acción puesta en boca de alguien real y
      nombrado, o un hecho del mundo con fecha/cifra/noticia.
    - **La reescritura del modelo se ignora y se publica el texto original.**
      Sin esto el modo no servía de nada en la práctica: contra el modelo real
      el auditor "aprobaba" devolviendo una paráfrasis del banco con el 24% del
      texto, y el anti-muñón de `redaccion` tiraba el intento igual.
    - **El veto sigue intacto.** Si el modelo dice que no, es que no.

    Con una sola marca de hecho duro -- un dígito, una comilla, un mes, un
    nombre sin respaldo -- no se entra al modo, el permiso ni siquiera aparece
    en el prompt y el auditor es EXACTAMENTE el de siempre. Por eso lo que sigue
    siendo imposible no depende de que un modelo obedezca una frase: cifras,
    porcentajes, estadísticas o estudios inventados; una cita textual o una
    acción en boca de una persona REAL y nombrada; una noticia, fecha, ronda de
    inversión o alianza inventada; una afirmación regulatoria o una promesa de
    aprobación -- todas necesitan, para poder escribirse, al menos una marca que
    `marcas_de_hecho_duro` detecta antes de llamar al modelo.

    Esta función sigue sin tener un parámetro `permisivo`: son dos ejes
    distintos y no se confunden -- `permisivo` es "alguien pidió este tema, no
    rechaces por gusto"; esto es "esta cuenta autorizó este TIPO de escena, no
    la trates como invención". El veto anti-invención sigue siendo estricto en
    todo lo demás, en los dos modos.

    Qué hacer ante un veto (descartar el borrador entero, o conservar el texto
    previo a este auditor porque el usuario pidió el tema explícitamente) es una
    decisión de orquestación -- ver `finalizar_post` y la lección crítica en el
    docstring del módulo.
    """
    texto = (texto or "").strip()
    if not texto:
        return "", ["El texto está vacío."]

    contexto = _formatear_fuentes(fuentes, _MAX_CONTEXTO_AUDITOR_CHARS)
    sin_fuentes = contexto == _SIN_FUENTES_MARCADOR

    # ¿Este texto CONCRETO califica como escena ilustrativa pura? La autorización del perfil
    # no alcanza por sí sola: además el texto tiene que ser estructuralmente incapaz de
    # contener una invención prohibida, y eso lo decide `marcas_de_hecho_duro`, no el modelo.
    # Con una sola marca -- un dígito, una comilla, un mes, un nombre propio sin respaldo --
    # el auditor es exactamente el de siempre y el permiso ni se menciona en el prompt.
    escena_pura = False
    if escenas_ilustrativas_autorizadas and not sin_fuentes:
        marcas = marcas_de_hecho_duro(texto, contexto)
        escena_pura = not marcas
        if marcas:
            logger.info(
                "auditar_hechos: el perfil autoriza escenas ilustrativas, pero este texto "
                "trae marcas de hecho duro (%s): se audita con el modo estricto de siempre.",
                marcas,
            )

    if sin_fuentes:
        instruccion_modo = (
            "No hay fuentes. Reconstruye cualquier afirmación empírica como un escenario "
            "condicional, una pregunta de decisión o una explicación puramente lógica. "
            "Elimina marcas, productos, cifras, tendencias y afirmaciones sobre cómo actúa "
            "una industria o una empresa concreta. Devuelve publicable=true si puedes "
            "conservar una idea útil sin esos elementos; publicable=false solo si al "
            "quitarlos no queda una idea concreta."
        )
    else:
        instruccion_modo = (
            "Si el texto contiene inferencias débiles, reconstrúyelo con 1 a 3 hechos "
            "explícitos de las fuentes, manteniendo el mismo tema, y devuelve "
            "publicable=true siempre que las fuentes alcancen para un texto claro. Usa "
            "publicable=false únicamente si las fuentes no alcanzan para sostener el tema "
            "central."
        )
        if solo_titulares:
            # Tercer modo del auditor de REFERENCIA (`linkedin_content.py:1380-1385`): con un
            # titular y un snippet no hay cuerpo de artículo contra el que verificar, así que
            # lo que hay que impedir es la EXTRAPOLACIÓN, no el post entero.
            instruccion_modo += (
                " AVISO IMPORTANTE SOBRE EL MATERIAL: lo que recibes son titulares y "
                "fragmentos de buscador, no el cuerpo de los artículos. Un titular no "
                "autoriza a deducir mecanismos, causas, efectos, motivaciones, plazos ni "
                "detalles que el titular no diga literalmente: recorta esas deducciones en "
                "vez de darlas por buenas. Lo que sí puede sostener el post es el hecho tal "
                "como el titular lo enuncia, más razonamiento general declarado como tal."
            )
        if escena_pura:
            # MODO VEREDICTO, NO REESCRITURA. Se midió contra el modelo real: pedirle a un
            # modelo pequeño que "reconstruya el texto con 1 a 3 hechos de las fuentes" sobre
            # una escena ilustrativa lo convierte en un resumidor -- devolvía `publicable=true`
            # con el 24% del texto, una paráfrasis plana del banco de contexto, y el
            # anti-muñón de `redaccion` tenía que tirar el intento. Acá no hay nada que
            # reconstruir: `marcas_de_hecho_duro` ya demostró que este texto no tiene ni una
            # cifra, ni una comilla, ni una fecha, ni un nombre propio sin respaldo. Así que
            # el trabajo del modelo se reduce a lo único que el código no puede hacer solo:
            # decir si el texto afirma algo FALSO sobre la marca o sus productos según las
            # fuentes. Juzgar es mucho más fácil que reescribir con restricciones, y el
            # resultado (`texto`) ya no se usa: lo ignora `auditar_hechos` más abajo.
            instruccion_modo = (
                "MODO VEREDICTO (esta cuenta autorizó ESCENAS ILUSTRATIVAS en su perfil "
                "editorial y este texto ya fue verificado mecánicamente: no contiene ni una "
                "cifra, ni un porcentaje, ni una fecha, ni una comilla, ni un nombre propio "
                "que las fuentes no respalden). NO REESCRIBAS NADA: copia el texto recibido "
                "TAL CUAL, palabra por palabra, en el campo 'texto'. Tu único trabajo es el "
                "veredicto. Una escena cotidiana con personajes anónimos y genéricos ('un "
                "bodeguero que fía', 'quien paga la renta el primero') que ILUSTRA el "
                "argumento NO es una invención, NO es una 'cita no verificable' y NO es un "
                "'desenlace inventado': es el recurso que esta cuenta usa a propósito, y no "
                "necesita venir anclada palabra por palabra en las fuentes. Devuelve "
                "publicable=false SOLO si encuentras una de estas cosas concretas: (a) el "
                "texto afirma algo sobre la empresa o sus productos que las fuentes "
                "contradicen o no describen -- una función que no existe, que es un banco, "
                "que garantiza o asegura una aprobación, que está regulada o supervisada; "
                "(b) atribuye una acción o unas palabras a una persona o institución real y "
                "nombrada; (c) afirma un hecho del mundo con fecha, cifra o noticia. En "
                "cualquier otro caso devuelve publicable=true y problemas=[]."
            )

    sistema_auditor = (
        "Eres un auditor factual y lógico extremadamente estricto. Las fuentes "
        "incluidas son la ÚNICA verdad permitida; no completas nada desde tu propia "
        "memoria ni inventas un reemplazo cuando falta un dato. Rechazas cualquier "
        "cifra, fecha, nombre, motivación, plazo o comparación que las fuentes no "
        "digan literalmente. Regla dura no negociable: si una fuente describe algo "
        "SIN RESOLVER (una denuncia, demanda, investigación o reclamo), el texto "
        "corregido nunca puede afirmar que hubo sanción, fallo, multa, veredicto o "
        "confirmación -- eso es un desenlace inventado, sin importar cuán probable "
        "parezca."
    )
    if escena_pura:
        sistema_auditor += (
            " Única excepción, acotada y explícita: esta cuenta autorizó escenas "
            "ilustrativas anónimas y genéricas (sin fecha, sin cifra, sin nombre propio "
            "real, sin cita textual) como recurso legítimo para explicar una idea -- esas "
            "no las rechazas ni las recortas solo por no venir citadas literalmente. Todo "
            "lo demás de esta instrucción sigue exacto, sin excepciones: cifras, citas, "
            "nombres reales, fechas, noticias, alianzas y desenlaces inventados siguen "
            "prohibidos sin importar este permiso."
        )

    mensajes: list[dict[str, str]] = [
        {
            "role": "system",
            "content": sistema_auditor,
        },
        {
            "role": "user",
            "content": (
                f"FUENTES (única fuente de verdad):\n{contexto}\n\n"
                f"TEXTO A AUDITAR:\n{texto[:_MAX_TEXTO_CHARS]}\n\n"
                "Devuelve SOLO JSON válido con esta forma exacta: "
                '{"publicable": true|false, "problemas": ["..."], "texto": "..."}. '
                "'problemas' lista cada cifra, fecha, cita o desenlace del borrador que no "
                "viene literalmente de las fuentes (vacía si el texto ya estaba limpio); "
                "'texto' es el texto completo corregido usando solo lo que las fuentes "
                "sostienen, sin agregar hechos nuevos. Usa publicable=false si, incluso "
                f"corrigiendo, el tema central deja de estar sostenido por las fuentes. "
                f"{instruccion_modo}"
            ),
        },
    ]

    try:
        raw = await llamar_modelo(mensajes)
    except Exception:
        logger.warning("auditar_hechos: llamar_modelo falló; se veta por seguridad.", exc_info=True)
        return "", ["No se pudo completar la auditoría factual (fallo al llamar al modelo)."]

    datos = _extraer_json(raw)
    if not isinstance(datos, dict):
        return "", [
            "El auditor no devolvió una respuesta interpretable; se descarta por seguridad."
        ]

    problemas = [str(p).strip() for p in (datos.get("problemas") or []) if str(p).strip()]

    if datos.get("publicable") is not True:
        # El veto sigue intacto en modo escena pura: lo único que se le quitó al modelo es la
        # tijera, nunca el derecho a decir que no.
        return "", problemas or ["El auditor marcó el texto como no publicable."]

    if escena_pura:
        # LA REESCRITURA DEL MODELO SE IGNORA, POR CONSTRUCCIÓN. No es confianza ciega: es
        # que no hay nada que reescribir. `marcas_de_hecho_duro` ya demostró -- sin modelo,
        # de forma reproducible -- que este texto no contiene ninguna de las cosas que el
        # auditor existe para corregir, así que cualquier diferencia entre lo que entró y lo
        # que devolvió el modelo sólo puede ser pérdida: la amputación del 76% que se midió
        # en producción. El modelo ya ejerció lo único que tenía sentido pedirle (el
        # veredicto de arriba); pasado ese punto se publica el texto del escritor, que además
        # es el único de los dos que vio el banco de contexto completo.
        devuelto = _normalizar_texto(str(datos.get("texto") or ""))
        if devuelto and devuelto != texto:
            logger.info(
                "auditar_hechos (escena ilustrativa): el modelo aprobó pero reescribió de %d "
                "a %d caracteres; se conserva el texto original. Problemas que reportó: %s",
                len(texto),
                len(devuelto),
                problemas,
            )
        return texto, []

    corregido = _normalizar_texto(str(datos.get("texto") or ""))
    if not (_MIN_TEXTO_CHARS < len(corregido) <= _MAX_TEXTO_FINAL_CHARS):
        return "", problemas or ["El texto corregido quedó fuera del rango de longitud aceptable."]

    return corregido, problemas


async def revisar_calidad(
    texto: str,
    contexto: str,
    llamar_modelo: LlamarModelo,
    permisivo: bool = False,
) -> tuple[bool, str]:
    """Editor jefe de segunda pasada: juzga si `texto` merece publicarse, no
    solo si pasa los gates deterministas de estilo. Puede RECHAZAR el borrador
    completo (regla "no forzar un post": saltarse el turno protege mejor la
    cuenta que publicar contenido correcto pero mediocre o genérico).

    `contexto` es el bloque de contexto que arma el llamador (fuentes, perfil
    editorial del tenant, borradores recientes a no repetir, formato exigido,
    etc.); esta función no lo construye, solo lo usa para juzgar si el texto
    tiene un ángulo real o es plano/forzado/genérico.

    `permisivo=True` (el usuario pidió EXPLÍCITAMENTE este tema): desactiva el
    rechazo por criterio SUBJETIVO ("poco interesante", "tema demasiado
    amplio", "sectorial"). En ese modo el editor debe pulir el ángulo más
    concreto posible en vez de descartarlo -- el usuario revisa el resultado
    antes de publicar. Solo sobreviven los rechazos por reglas DURAS (el texto
    está vacío, es ilegible, o no se sostiene en absoluto). Un fallo técnico al
    interpretar la respuesta del editor tampoco cuenta como rechazo por regla
    dura: en modo permisivo se conserva el borrador (motivo explicado); fuera
    de modo permisivo se descarta por seguridad.

    Devuelve `(publicable, motivo)`. `motivo` explica el veredicto, tanto si es
    `True` (trazabilidad) como si es `False` (para decidir si vale la pena
    reintentar con otro ángulo en vez de simplemente saltar el turno).
    """
    texto = (texto or "").strip()
    if len(texto) < _MIN_TEXTO_CHARS:
        return False, "El texto está vacío o es demasiado corto para evaluarlo."

    instruccion_permisivo = (
        "El usuario PIDIÓ explícitamente este tema: tu trabajo es juzgar si el ángulo "
        "elegido es publicable, no rechazarlo por 'poco interesante', 'demasiado amplio' o "
        "'genérico'. Responde publicable=false SOLO si el texto viola una regla dura (no se "
        "sostiene en el contexto disponible, o es ilegible/incoherente), nunca por simple "
        "gusto editorial.\n\n"
        if permisivo
        else ""
    )

    mensajes: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "Eres el editor jefe de un perfil profesional en redes sociales. Tu trabajo "
                "es decidir si un borrador merece publicarse, no reescribirlo palabra por "
                "palabra. Un borrador correcto pero plano, genérico o sin un ángulo concreto "
                "no es publicable: es preferible saltarse el turno que publicar contenido "
                "artificial."
            ),
        },
        {
            "role": "user",
            "content": (
                f"{instruccion_permisivo}"
                "CONTEXTO DISPONIBLE:\n"
                f"{(contexto or '(sin contexto adicional)')[:_MAX_CONTEXTO_CHARS]}\n\n"
                f"BORRADOR:\n{texto[:_MAX_TEXTO_CHARS]}\n\n"
                "Evalúa: ¿alguien ajeno al tema entiende en diez segundos por qué le importa? "
                "¿hay un sujeto exacto, una persona o grupo afectado, y una decisión o "
                "desacuerdo concreto? ¿el cierre resuelve algo en vez de terminar en una "
                "pregunta retórica o un diagnóstico suelto? Devuelve SOLO JSON válido: "
                '{"publicable": true|false, "motivo": "una frase breve y específica"}.'
            ),
        },
    ]

    try:
        raw = await llamar_modelo(mensajes)
    except Exception:
        logger.warning("revisar_calidad: llamar_modelo falló.", exc_info=True)
        if permisivo:
            return True, (
                "No se pudo completar la revisión de calidad (fallo al llamar al modelo); "
                "se conserva el borrador porque el usuario pidió este tema explícitamente."
            )
        return False, "No se pudo completar la revisión de calidad (fallo al llamar al modelo)."

    datos = _extraer_json(raw)
    if not isinstance(datos, dict):
        if permisivo:
            return True, (
                "El editor no devolvió una respuesta interpretable; se conserva el borrador "
                "porque el usuario pidió este tema explícitamente (modo permisivo)."
            )
        return (
            False,
            "El editor no devolvió una respuesta interpretable; se descarta por seguridad.",
        )

    motivo = str(datos.get("motivo") or "").strip() or "Sin motivo explícito del editor."
    publicable = datos.get("publicable") is True
    return publicable, motivo


async def finalizar_post(
    texto: str,
    *,
    fuentes: Sequence[Fuente] | None,
    contexto: str,
    llamar_modelo: LlamarModelo,
    plataforma: str = "linkedin",
    permisivo: bool = False,
    escenas_ilustrativas_autorizadas: bool = False,
) -> tuple[str, list[str]]:
    """Orquestación de referencia: `revisar_calidad` -> `auditar_hechos` -> vuelve
    a correr `edecan_creative.editorial.revisar` sobre el resultado.

    `escenas_ilustrativas_autorizadas` se reenvía tal cual a las dos llamadas de
    `auditar_hechos` de abajo (la principal y la re-auditoría tras reparar la
    primera persona) -- ver el docstring de esa función para lo que activa y la
    línea que sigue sin poder cruzar.

    Este es el mecanismo real detrás de la lección crítica documentada arriba:
    `auditar_hechos` reescribe el texto, y esa reescritura puede reintroducir
    una violación de estilo (primera persona, cierre en pregunta, una plantilla
    delatora de IA) que ya se había limpiado ANTES de que este texto llegara
    aquí. Por eso el paso final vuelve a correr los mismos gates deterministas
    sobre el texto que sobrevivió a las dos reescrituras -- nunca se confía en
    que un texto que pasó los gates una vez los sigue pasando después de que un
    modelo lo tocó de nuevo.

    Un llamador que necesite una orquestación distinta (por ejemplo, insertar
    pasos propios entre la revisión de calidad y la auditoría factual) puede
    usar `revisar_calidad` y `auditar_hechos` por separado -- pero debe
    reproducir ese re-chequeo final con `edecan_creative.editorial.revisar`
    (y, si el post tiene un titular visual aparte, con
    `edecan_creative.editorial.revisar_visual`) él mismo.

    Devuelve `(texto_final, problemas)`. `texto_final` es `""` cuando el
    borrador se descarta (regla "no forzar un post": el llamador debe saltarse
    el turno, no reintentar publicar con este mismo texto) y `problemas`
    explica por qué.
    """
    texto = (texto or "").strip()
    if not texto:
        return "", ["El texto está vacío."]

    publicable, motivo_calidad = await revisar_calidad(texto, contexto, llamar_modelo, permisivo)
    if not publicable and not permisivo:
        return "", [motivo_calidad]

    texto_auditado, problemas_hechos = await auditar_hechos(
        texto,
        fuentes,
        llamar_modelo,
        escenas_ilustrativas_autorizadas=escenas_ilustrativas_autorizadas,
    )
    if texto_auditado:
        texto_final = texto_auditado
    elif permisivo:
        # El auditor vetó, pero el usuario pidió este tema explícitamente: se conserva el
        # texto PREVIO al auditor (ya evaluado por revisar_calidad) en vez de descartar todo
        # el borrador. El usuario revisa el resultado antes de publicar.
        texto_final = texto
    else:
        return "", problemas_hechos or [motivo_calidad]

    # REPARAR ANTES DE RE-GATEAR: `auditar_hechos` reescribe con un prompt que no conoce
    # ninguna regla de estilo, así que reintroducir un "nos", un "creo" o un "vimos" es lo
    # ESPERADO, no lo raro (ver la lección crítica del docstring del módulo). REFERENCIA, en ese
    # mismo punto (`linkedin_content.py:1601-1605`), no descarta: repara la primera persona y
    # RE-VERIFICA el resultado contra la fuente. Descartar aquí era tirar el mejor candidato
    # que existió en toda la corrida por un pronombre mecánicamente arreglable.
    if _tiene_primera_persona(texto_final):
        reparado = await reescribir_sin_primera_persona(texto_final, fuentes, llamar_modelo)
        if reparado:
            re_auditado, _ = await auditar_hechos(
                reparado,
                fuentes,
                llamar_modelo,
                escenas_ilustrativas_autorizadas=escenas_ilustrativas_autorizadas,
            )
            texto_final = re_auditado or (reparado if permisivo else texto_final)

    # RE-GATE: los gates deterministas corren de nuevo sobre el texto FINAL, después de las
    # reescrituras anteriores -- ver la lección crítica en el docstring del módulo.
    problemas_gate = _revisar_gates_deterministas(texto_final, plataforma, permisivo=permisivo)
    if problemas_gate and not permisivo:
        return "", problemas_gate

    problemas_acumulados = [*problemas_hechos, *problemas_gate]
    return texto_final, problemas_acumulados
