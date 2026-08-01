"""Control de calidad editorial para contenido social: reglas duras de voz, detectores de
"delatores de IA" y normalización determinista de texto.

Este módulo es el MOTOR: reglas genéricas de buena escritura profesional que sirven a
cualquier tenant, no solo a un usuario en particular. Nada aquí menciona una persona, una
empresa o una cuenta específica. El contexto personal de cada usuario (su voz concreta, su
banco de material, sus temas prohibidos) vive en el perfil editorial por tenant — ver
``get_editorial_profile`` / ``save_editorial_profile`` en :mod:`edecan_creative.social`,
respaldado por la tabla ``social_editorial_profiles``. Ese es el lugar correcto para lo
privado; este archivo no debe crecer con nada específico de un usuario.

Las reglas se embeben como cadenas dentro de este ``.py`` (no como un ``.txt`` suelto)
porque el backend se empaqueta con PyInstaller y un archivo de datos adicional podría no
quedar incluido en el bundle final.

API pública:

- ``REGLAS_LINKEDIN``: texto listo para inyectar en el prompt de un modelo que escribe
  posts de LinkedIn.
- ``PROMPT_EDITOR_HUMANIZADOR``: prompt para una segunda pasada editorial (LLM) que quita
  señales de escritura generada por IA sin inventar primera persona.
- ``revisar(texto, plataforma, permisivo=False)``: gates deterministas sobre el texto final.
  Lista vacía significa que el texto pasa el control de calidad.
- ``delatores_de_estilo(texto)``: los MISMOS hallazgos que ``revisar`` clasifica como
  reparables, para alimentar una pasada de corrección en vez de tirar el borrador.
- ``revisar_visual(kicker, headline, support)``: mismos gates, para el titular/copy visual.
- ``normalizar(texto, plataforma)``: limpieza determinista y segura (no cambia el sentido).
- ``normalizar_visual(visual, tema, texto)``: repara mecánicamente el diccionario visual.
- ``firma_estructura`` / ``firmas_prohibidas_recientes`` / ``instruccion_cooldown_estructura``:
  cooldown ESTRUCTURAL (no de tema) contra los últimos posts de la cuenta.
- ``quitar_prefijo_repetido(kicker, headline)``: utilidad mecánica para no repetir la
  categoría del kicker al inicio del titular.
- ``tiene_numero_extenso(texto)``: helper opcional, no aplicado por defecto (ver docstring).

REPARAR ANTES DE RECHAZAR
-------------------------
Este módulo se escribió como una batería de gates que sólo saben decir "no". El motor de
LinkedIn de Aria (``features/linkedin_content.py``), que es el que lleva meses publicando
a diario, hace lo contrario con los mismos patrones: ``_normalizar_texto_post`` y
``_normalizar_visual`` REPARAN todo lo mecánico antes de que ningún gate lo vea, y los
detectores de delatores no rechazan por sí solos -- alimentan una reescritura, y sólo matan
el borrador si esa reescritura falla Y el usuario no pidió el tema. Por eso aquí:

- lo mecánico (guion largo, hashtags, emojis) se arregla en :func:`normalizar`, no se
  reporta como violación que quema un turno;
- lo subjetivo (plantillas de IA, ritmo, apertura de informe) se clasifica como reparable y
  :func:`revisar` lo omite cuando ``permisivo=True`` -- el usuario pidió el post y lo revisa
  antes de publicar;
- lo único DURO, incluso en permisivo, es la primera persona (regla explícita del dueño de
  la cuenta: el sistema nunca le atribuye experiencias).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

__all__ = [
    "REGLAS_LINKEDIN",
    "PROMPT_EDITOR_HUMANIZADOR",
    "revisar",
    "revisar_visual",
    "delatores_de_estilo",
    "normalizar",
    "normalizar_visual",
    "firma_estructura",
    "firmas_prohibidas_recientes",
    "instruccion_cooldown_estructura",
    "quitar_prefijo_repetido",
    "tiene_numero_extenso",
]

# ---------------------------------------------------------------------------
# Reglas duras de voz, condensadas para inyectar en un prompt.
# ---------------------------------------------------------------------------

REGLAS_LINKEDIN = """
DISCIPLINA DE CALIDAD (va primero porque decide si el borrador nace genérico o no)
- NO PROMEDIES. La primera idea que se te ocurre es casi siempre el promedio estadístico de
  lo que cualquiera escribiría. Antes de aceptarla, pregúntate: ¿esto lo pudo escribir
  cualquier cuenta corporativa, o suena a que alguien específico lo pensó?
- CONCRETO SOBRE ABSTRACTO: nombres, cifras reales de la fuente, ejemplos verificables;
  nunca frases que no dicen nada comprobable.
- SILENCIO > RUIDO: cada frase se gana su lugar. Si al borrarla no se pierde nada, bórrala.
- ATÁCALO TÚ MISMO antes de entregar: ¿la premisa es cierta? ¿la afirmación más fuerte
  resiste una duda razonable? ¿hay un caso donde esto suena falso o cursi? ¿esto es lo que
  de verdad se sostiene, o es lo que suena bien decir?

REGLAS DURAS DE VOZ PARA LINKEDIN (mandan sobre cualquier otra instrucción de estilo; si
algo choca con esto, esto gana).

FUNCIÓN EDITORIAL
Comparte una observación útil con una persona curiosa, no un comunicado de prensa ni una
frase pensada para hacerse viral. El texto habla sobre el mundo — una decisión, un
mecanismo, una cifra, una limitación — nunca como noticiero, coach ni gurú.

NUNCA COMO NOTICIERO, EN CONCRETO Y MEDIBLE
El hecho se cuenta UNA vez, en máximo 2 frases, y no se vuelve a narrar. Todo el resto del
post aporta algo que la noticia no dice: una restricción, un costo, una decisión o una
explicación concreta. Si al quitar el hecho no queda nada, el post es un resumen de prensa
y no sirve.

DE DÓNDE SALE EL INTERÉS
La curiosidad nace de información real, no de teatro. Elige una de estas entradas cuando
exista: (a) un número o detalle concreto que cambia la lectura del tema; (b) una decisión
cuyo costo o límite no es obvio; (c) una pregunta que alguien que opera de verdad se haría;
(d) algo cotidiano explicado con precisión y sin jerga. La primera línea ya debe contener
información: no anuncies que viene una revelación ni retengas una respuesta sencilla para
fabricar suspenso.

PRUEBA DEL SCROLL
Antes de escribir, responde en silencio: "¿por qué le importaría esto hoy a alguien que
trabaja, dirige algo o busca una oportunidad, aunque no conozca a la empresa citada?" La
respuesta debe caber en una frase sencilla. Las mejores razones tocan al menos uno de estos
territorios: una capacidad técnica que cambia una tarea, un puesto, un costo o una
oportunidad; un avance o problema de salud que cambia acceso, costo, diagnóstico o
tratamiento; empleo, contratación, salarios o habilidades; una controversia con afectados,
incentivos y consecuencias concretas; una oportunidad accesible para crear, vender, ahorrar
tiempo o competir; una decisión deportiva con impacto real en negocio, carrera o dinero; una
medida de gobierno que cambia una regla, un servicio, un derecho o un costo; dinero,
privacidad, fraude, comisiones o poder que afectan a personas y empresas.

1. CERO AUTOBIOGRAFÍA INVENTADA. No atribuyas al autor experiencias, decisiones o escenas
   que no vengan explícitamente de sus datos reales. Prohibido fabricar "me pasó", "hice",
   "construí", "aprendí" o "decidí" como recurso retórico. La autoridad sale de la
   precisión del argumento, no de una anécdota inventada.

2. PRIMERA PERSONA PROHIBIDA POR DEFECTO. La posición se afirma directamente sobre el
   mundo ("El debate está mal planteado", "La regla cambia el incentivo"), sin yo, mi,
   nosotros, ni verbos que atribuyan una acción al autor — salvo que el perfil editorial
   del usuario autorice explícitamente una voz en primera persona.

3. CLARIDAD ANTES QUE FILO. El punto se entiende en la primera leída, por alguien cansado
   y en el celular. Una frase bonita que obliga a releer pierde contra una frase clara.

4. CERO CIFRAS INVENTADAS. Ningún número, porcentaje, ranking o fecha que no venga de una
   fuente real entregada para ese borrador. Ante la duda, sin número: un post sin cifra es
   normal, uno con una cifra falsa daña la credibilidad de toda la cuenta.

5. LA MEMORIA DEL MODELO ES VIEJA POR DEFINICIÓN. Un evento (lanzamiento, despido, ronda,
   cifra) solo entra al post si viene en las fuentes entregadas para ese borrador en
   concreto. Sin fuentes con fecha, el post es tesis o mecanismo puro, sin ningún evento
   fechable inventado de memoria.

6. CERO DESENLACES INVENTADOS. Si la fuente describe algo sin resolver (denuncia,
   demanda, investigación, reclamo), el post nunca afirma que hubo sanción, fallo, multa
   o veredicto que la fuente no confirma. El giro sale de un mecanismo real y verificable,
   nunca de un final ficticio.

7. ENSEÑAR, NUNCA VENDER. No uses la marca, el cargo o la operación del autor como
   credencial ni cierres con una invitación comercial. Si el post terminó oliendo a pitch,
   reescríbelo como una explicación neutral.

8. DIVERSIDAD DE ESTRUCTURA Y DE TEMA. No repitas la misma arquitectura de post (gancho +
   tres viñetas + cierre con moraleja) en cada entrega, ni el mismo país, empresa o
   ejemplo si el tema puede ilustrarse con otro. Compara contra los borradores recientes
   antes de reutilizar una forma.

9. SEGUNDA PASADA OBLIGATORIA. Antes de entregar, actúa como editor hostil: elimina
   resumen de noticia, frases intercambiables, revelaciones anunciadas, contrastes
   prefabricados, ritmo entrecortado y cierres diseñados como aforismos. Conserva los
   hechos, la explicación y el criterio concreto.

10. NO FORZAR UN POST. Si las fuentes no contienen un ángulo capaz de sostener interés sin
    muletillas, marca el borrador como no publicable y no lo entregues. Saltarse un slot
    protege mejor la cuenta que enviar contenido correcto pero artificial.

11. RELEVANCIA ANTES QUE ACTUALIDAD. Ser noticia no basta. Prioriza una decisión,
    lanzamiento, conflicto, oportunidad, descubrimiento o cambio regulatorio que afecte a
    alguien de forma concreta. Una ronda, un fondo o una métrica sectorial sin consecuencia
    clara para el lector se descarta.

12. PRUEBA DEL LECTOR AJENO. El post se entiende sin conocer la empresa, la sigla o la
    industria. Si usa un actor poco conocido, explica qué hace en palabras comunes. Si la
    razón para seguir leyendo no cabe en una frase sin jerga, no es publicable.

13. UNA HISTORIA, UNA FUENTE. Prohibido unir titulares independientes para fabricar una
    tendencia. Cada post reactivo elige una sola fuente principal; todo hecho, comparación
    o relación causal debe estar sostenido por ella.

14. ETIQUETA Y TITULAR NO SE REPITEN. Si la etiqueta visual ya nombra la categoría (por
    ejemplo "FINTECH · CAPITAL"), el titular no vuelve a empezar por "Fintech:".

CÓMO SUENA UNA PERSONA
- Palabras sencillas y sustantivos concretos: "el banco cobra" gana a "el sistema captura
  valor".
- Varía la longitud de las frases sin apilar fragmentos cortos para fabricar drama.
- No fuerces grupos de tres. Usa la cantidad natural de puntos que tenga el argumento.
- Permite una conclusión parcial cuando las fuentes no justifican una certeza limpia.
- Lee el texto en voz alta: si nadie hablaría así en una conversación profesional,
  reescribe.

ESPAÑOL
Español latinoamericano natural, con TUTEO. Prohibido el voseo: nunca "vos", "tenés",
"querés", "sos", "podés", "sabés" ni la conjugación rioplatense, y nunca giros regionales de
una región que no sea la de la cuenta. Tampoco fuerces muletillas de cercanía falsa
("resulta que", "fíjate", "lo que pasa es que", "al final del día"). Si el perfil editorial
de la cuenta define otro registro o otra variante, ese perfil manda sobre esta línea, pero
el voseo sigue prohibido salvo que el perfil lo pida con esas palabras.

ESTRUCTURA
- Una sola idea por post, pero desarrollada con profundidad. Entre 240 y 340 palabras; la
  longitud cambia según el tema, nunca rellenes para llegar a un mínimo.
- Párrafos cortos, legibles en un teléfono.
- Máximo una pregunta retórica en todo el texto. Cero es normal. Nunca cierres con una
  pregunta para generar comentarios.
- Viñetas solo cuando aclaran una enumeración real, nunca por decoración.
- Sin hashtags y sin emojis por defecto.
- Sin guion largo (—) ni doble guion (--); usa coma o punto.
- El dos puntos solo abre una enumeración real, nunca una revelación dramática.

PROHIBIDO COMO PLANTILLA (delatores de escritura generada por IA)
- "la consecuencia incómoda", "la respuesta es/no es", "cuando pasa esto";
- "ahí está", "ahí es donde", "lo raro no es", "el problema real", "la verdad incómoda";
- "lo que nadie cuenta" o cualquier promesa de secreto genérico;
- "no es X, es Y" repetido como giro automático;
- abrir citando "según un informe" o cerrar con moraleja, predicción o advertencia de
  manual.

ANTES DE ENTREGAR
1. ¿La primera línea contiene información concreta?
2. ¿Cero primera persona y cero autobiografía inventada?
3. ¿Cada hecho y cada cifra viene de una fuente entregada?
4. ¿El ritmo parece conversación profesional y no una sucesión de frases virales?
5. ¿Alguien ajeno al tema entiende en diez segundos por qué debería importarle?
""".strip()

PROMPT_EDITOR_HUMANIZADOR = """
Editas publicaciones profesionales para quitar señales de escritura generada por IA. No
inventas primera persona ni atribuyes al autor una experiencia que las fuentes no
sostienen. Conservas todos los hechos permitidos.

Reescribe cuando aparezca cualquiera de estas señales: una revelación anunciada ("la
consecuencia incómoda", "ahí está", "la respuesta es", "lo raro no es", "el problema
real", "lo que nadie cuenta"); el contraste automático "no es X, es Y"; una pregunta
fabricada seguida de una respuesta de manual; tres ideas forzadas para completar una
lista; frases cortas apiladas para fabricar drama; un cierre escrito como aforismo,
moraleja, predicción o advertencia que el argumento no necesita; o palabras abstractas que
aparentan profundidad cuando un sustantivo o verbo concreto diría más.

El interés debe salir de un dato, una decisión, una limitación o una contradicción
verificable, nunca de retener artificialmente la respuesta. Varía el ritmo, permite
transiciones sencillas o ninguna, y deja alguna ambigüedad cuando los hechos no permiten
una conclusión limpia. No abras citando "según un informe" ni firmando con "X afirma /
señala" cuando las fuentes incluyen un hecho concreto que puede abrir el texto
directamente.

Antes de aprobar, responde en silencio: ¿cuál es el sujeto exacto o el hecho reciente, sin
comodines como "un modelo" o "las empresas"? ¿Qué persona concreta resulta afectada y qué
cambia para ella? ¿Qué decisión, desacuerdo o experiencia verificable podría hacer que
alguien responda? Si una respuesta no está en el texto, reescribe o marca el borrador como
no publicable. El último párrafo completa la idea con una decisión concreta o, cuando
nazca naturalmente del tema, una sola pregunta discutible y específica. Un diagnóstico que
no conduce a nada no es un cierre.
""".strip()

# ---------------------------------------------------------------------------
# Detectores de texto ("delatores de IA"), portados de linkedin_humanizer.py.
# Cada patrón trae su propio mensaje accionable para que el modelo sepa qué corregir.
# ---------------------------------------------------------------------------

_TEXT_TELLS: tuple[tuple[str, str, str], ...] = (
    (
        "consecuencia_incomoda",
        r"\bla consecuencia inc[oó]moda\b",
        "Quita la plantilla 'la consecuencia incómoda' y nombra directamente cuál es la "
        "consecuencia.",
    ),
    (
        "respuesta_prefabricada",
        r"\bla respuesta (?:es|no es|pasa por|llega)\b",
        "Quita la fórmula 'la respuesta es/no es/pasa por/llega' y responde de forma "
        "directa sin anunciar que viene una respuesta.",
    ),
    (
        "cuando_pasa_esto",
        r"\bcuando pasa esto\b",
        "Quita 'cuando pasa esto' y describe el hecho concreto que ocurre.",
    ),
    (
        "revelacion_con_ahi",
        r"\b(?:y\s+)?ah[ií] (?:est[aá]|es donde|aparece|queda|llega|empieza|se nota)\b",
        "Quita la revelación anunciada con 'ahí está / ahí es donde...' y ve directo al "
        "hecho, sin anunciarlo primero.",
    ),
    (
        "lo_raro_no_es",
        r"\blo raro no es\b",
        "Quita 'lo raro no es' y expón la observación directamente, sin ese giro.",
    ),
    (
        "autoridad_fingida",
        r"\b(?:el problema real|la pregunta real|la verdadera pregunta|la verdad "
        r"inc[oó]moda|lo que realmente importa)\b",
        "Quita las frases de falsa autoridad ('el problema real', 'la verdad incómoda', "
        "'lo que realmente importa') y nombra el problema o el dato directamente.",
    ),
    (
        "secreto_generico",
        r"\blo que (?:nadie|casi nadie) (?:cuenta|dice|ve|menciona|explica)\b",
        "Quita la promesa de secreto genérico ('lo que nadie cuenta/dice/ve') y expón el "
        "dato sin dramatizarlo.",
    ),
    (
        "contraste_prefabricado",
        r"\b(?:la respuesta\s+)?no es que\b.{0,140}\bes que\b",
        "Elimina el contraste automático 'no es que... es que...' y explica la idea de "
        "forma directa.",
    ),
    (
        "no_se_trata",
        r"\bno se trata de\b.{0,140}\bse trata de\b",
        "Elimina el giro 'no se trata de... se trata de...' y afirma la idea directamente.",
    ),
    (
        "prediccion_en_un_ano",
        r"\bsi (?:esto|eso|este|esta|ese|esa)\b.{0,180}\ben (?:un|1) a[ñn]o\b",
        "Quita la predicción genérica a futuro ('si esto sigue así, en un año...') salvo "
        "que una fuente real y verificable la sostenga.",
    ),
)

_VISUAL_TELLS: tuple[tuple[str, str, str], ...] = (
    (
        "titular_verdad_incomoda",
        r"\b(?:la verdad inc[oó]moda|el problema real)\b",
        "Quita 'la verdad incómoda' / 'el problema real' del titular; nombra el hecho "
        "directamente.",
    ),
    (
        "titular_secreto",
        r"\b(?:lo que nadie|detr[aá]s de)\b",
        "Quita la promesa de secreto ('lo que nadie...', 'detrás de...') del titular.",
    ),
    (
        "titular_cambio_generico",
        r"\b(?:todo cambia|nada vuelve a ser igual|el futuro ya lleg[oó])\b",
        "Quita la frase genérica de cambio ('todo cambia', 'nada vuelve a ser igual') y "
        "nombra el cambio concreto.",
    ),
    (
        "titular_metafora_abstracta",
        r"\b(?:el error|el riesgo|el dinero|el mercado|el algoritmo)\b.{0,24}\b(?:habla|"
        r"calla|grita|duerme|despierta|baja la voz)\b",
        "Quita la personificación abstracta (el mercado 'habla', el algoritmo 'duerme') y "
        "usa el sujeto y el verbo reales.",
    ),
)

_STOPWORDS = {
    "a",
    "al",
    "ante",
    "con",
    "de",
    "del",
    "el",
    "en",
    "es",
    "la",
    "las",
    "lo",
    "los",
    "no",
    "para",
    "por",
    "que",
    "se",
    "sin",
    "su",
    "sus",
    "un",
    "una",
    "y",
}

_PRIMERA_PERSONA_RE = re.compile(
    r"\b(?:yo|me|mí|mi|mis|mío|mía|míos|mías|conmigo|nos|nosotros|nosotras|nuestro|"
    r"nuestra|nuestros|nuestras|hice|hicimos|construí|construimos|aprendí|aprendimos|"
    r"viví|vivimos|vi|vimos|decidí|decidimos|cambié|cambiamos|publiqué|publicamos|creé|"
    r"creamos|fundé|fundamos|mandé|mandamos|operé|operamos|descubrí|descubrimos|probé|"
    r"probamos|cometí|cometimos|llegué|llegamos|empecé|empezamos|soy|somos|estoy|"
    r"estamos|tengo|tenemos|tuve|tuvimos|hago|hacemos|dirijo|dirigimos|lidero|"
    r"lideramos|pienso|pensamos|considero|consideramos|apuesto|apostamos|recuerdo|"
    r"recordamos|nací|nacimos|migré|migramos|sé|sabemos|quiero|queremos|puedo|podemos|"
    r"fui|fuimos|he|hemos|creo)\b",
    re.IGNORECASE,
)

# Guion largo/medio (—/–) o doble guion usado como sustituto de puntuación normal.
_GUION_LARGO_RE = re.compile(r"[—–]|\s--\s")

# Un hashtag real empieza tras un espacio o al inicio de línea (evita falsos positivos
# como "C#" o "issue#123").
_HASHTAG_RE = re.compile(r"(?<!\S)#\w[\w-]*", re.UNICODE)

# Rangos unicode de los bloques de emoji más comunes (pictogramas, emoticonos, banderas).
_EMOJI_RE = re.compile("[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f1e6-\U0001f1ff]")

# El cierre en fórmula mira los últimos ~260 caracteres del texto buscando las fórmulas
# GASTADAS de cierre, no cualquier pregunta.
#
# Dos correcciones respecto de la versión anterior, ambas contra el motor de Aria
# (`linkedin_content.py`), que es el que funciona:
#
# 1. Antes bastaba `\bla pregunta\b` en la cola para marcar violación, y eso disparaba con
#    frases perfectamente legítimas que no son preguntas ("la pregunta la responde el
#    regulador, no el mercado"). Ahora se exige la fórmula completa ("la pregunta que
#    queda/te dejo/de fondo es").
# 2. Antes, cualquier texto cuya última línea terminara en "?" era violación DURA. En Aria
#    cerrar con pregunta NO es un gate: es una FIRMA DE ESTRUCTURA con cooldown
#    (`_firma_estructura`/`_firmas_prohibidas_recientes`), o sea se prohíbe sólo si el post
#    anterior ya cerró igual. Y el propio PROMPT_EDITOR_HUMANIZADOR de este archivo permite
#    "una sola pregunta discutible y específica" como cierre: el escritor recibía
#    instrucciones que garantizaban el rechazo. Ese caso salió de `revisar` y vive ahora en
#    `firma_estructura` (firma "cierre_pregunta").
_CIERRE_FORMULA_RE = re.compile(
    r"\bla\s+pregunta\s+(?:que\s+(?:queda|te\s+dejo|nos\s+queda)|de\s+fondo|real\s+es)\b|"
    r"¿\s*vas\s+a\b|¿\s*deber[ií]a\w*\b|¿\s*qu[eé]\s+(?:vas|prefieres|esperas)\b",
    re.IGNORECASE,
)

# Cualquier número de 2+ dígitos (rangos, porcentajes, cifras). No se aplica por defecto
# en `revisar` — ver el docstring de `tiene_numero_extenso`.
_NUMERO_EXTENSO_RE = re.compile(r"\d{2,}")

# Plataformas donde hashtags/emojis SÍ son parte normal del formato y por lo tanto NO se
# marcan como violación. LinkedIn es la única plataforma con este gate activo hoy; se deja
# como conjunto (no un simple "== linkedin") para poder sumar plataformas formales más
# adelante sin tocar la lógica de `revisar`.
_PLATAFORMAS_SIN_HASHTAGS = frozenset({"linkedin"})
_PLATAFORMAS_SIN_EMOJIS = frozenset({"linkedin"})


def _sin_acentos(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto or "") if unicodedata.category(c) != "Mn"
    )


def _tokens_utiles(texto: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", _sin_acentos(texto).lower())
    return {t for t in tokens if len(t) > 2 and t not in _STOPWORDS}


def _tokens_categoria(texto: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", _sin_acentos(texto).lower()))


def _frases(texto: str) -> list[str]:
    return [f.strip() for f in re.split(r"(?<=[.!?])\s+", texto or "") if f.strip()]


def _tiene_primera_persona(texto: str) -> bool:
    return bool(_PRIMERA_PERSONA_RE.search(texto or ""))


# Palabras al inicio de una oración o de una viñeta que serían falsos positivos si
# se tomaran como nombre propio: la mayúscula no las hace un nombre.
_ARRANQUES_FALSOS_NOMBRE = frozenset(
    {
        "el", "la", "los", "las", "un", "una", "unos", "unas",
        "este", "esta", "estos", "estas", "ese", "esa", "esos", "esas",
        "puede", "vivió", "dirige", "opera", "usa", "hace", "tiene", "sabe",
        "nunca", "siempre", "según", "cuando", "aunque", "porque", "pero",
        "y", "o", "a", "de", "para", "por", "con", "sin", "en", "al",
        "identidad", "cicatrices", "terreno", "notas", "reglas", "regla",
        "org", "linkedin",
    }
)


def _nombres_propios_del_banco(banco: str) -> frozenset[str]:
    """Nombres propios que el propio banco menciona 2+ veces.

    El banco tiene una regla sagrada escrita en su cabecera: los hechos personales
    y la identidad "no son material publicable ni siquiera cuando el tema se
    parece". Confiar en que el escritor la respete no basta — se midió una
    corrida donde el post decía "La máquina de Ada incluye..." nombrando al
    dueño directamente. Este helper convierte esa regla en un gate objetivo, y
    lo hace sin config extra: cualquier nombre propio que el banco use varias
    veces (Ada, Acme, cualquier otra marca personal del tenant) queda
    prohibido en el cuerpo del post, con la misma dureza que la primera persona.
    """
    if not banco:
        return frozenset()
    encontrados: dict[str, int] = {}
    # Se admite CamelCase para atrapar marcas como "Acme" que arrancan con
    # mayúscula y llevan otra en medio: dos veces en el banco = protegida.
    for token in re.findall(r"\b[A-ZÁÉÍÓÚÑ][a-zA-ZáéíóúñÁÉÍÓÚÑ]{2,}\b", banco):
        if token.lower() in _ARRANQUES_FALSOS_NOMBRE:
            continue
        encontrados[token] = encontrados.get(token, 0) + 1
    return frozenset(n for n, veces in encontrados.items() if veces >= 2)


def _menciona_nombre_del_banco(texto: str, nombres: frozenset[str]) -> str | None:
    """Devuelve el primer nombre propio del banco que aparece en `texto`, o None."""
    if not nombres:
        return None
    for match in re.finditer(r"\b[A-ZÁÉÍÓÚÑ][a-zA-ZáéíóúñÁÉÍÓÚÑ]{2,}\b", texto or ""):
        candidato = match.group(0)
        if candidato in nombres:
            return candidato
    return None


def _tiene_guion_largo(texto: str) -> bool:
    return bool(_GUION_LARGO_RE.search(texto or ""))


def _tiene_hashtags(texto: str) -> bool:
    return bool(_HASHTAG_RE.search(texto or ""))


def _tiene_emojis(texto: str) -> bool:
    return bool(_EMOJI_RE.search(texto or ""))


def _cierre_de_formula(texto: str) -> bool:
    """True si el cierre usa una de las fórmulas GASTADAS de cierre.

    Ojo: cerrar con una pregunta ya NO cuenta aquí (ver el comentario de
    :data:`_CIERRE_FORMULA_RE`). Eso es una firma de estructura con cooldown, no una
    violación: :func:`firma_estructura` la reporta como ``cierre_pregunta``.

    Si el llamador siempre agrega un cierre fijo (una URL, una firma) después de lo que
    escribe el modelo, evalúa esta función ANTES de agregar ese cierre fijo — de lo
    contrario la "última línea" real del argumento queda escondida antes del cierre fijo y
    el gate no la ve.
    """
    t = (texto or "").strip()
    if not t:
        return False
    return bool(_CIERRE_FORMULA_RE.search(t[-260:]))


def _drama_entrecortado(texto: str) -> bool:
    cortas_seguidas = 0
    max_cortas = 0
    for frase in _frases(texto):
        if len(re.findall(r"\b\w+\b", frase)) <= 4:
            cortas_seguidas += 1
            max_cortas = max(max_cortas, cortas_seguidas)
        else:
            cortas_seguidas = 0
    return max_cortas >= 4


def _apertura_de_informe(texto: str) -> bool:
    frases = _frases(texto)
    apertura = frases[0] if frases else (texto or "").strip()
    return bool(
        re.search(
            r"^(?:seg[uú]n\b|de acuerdo con\b|un (?:informe|estudio)\b|.{1,45}\b"
            r"(?:recomienda|afirma|señala|advierte|reporta|report[oó]|dice)\b)",
            apertura,
            re.I,
        )
    )


def _cierre_de_informe(texto: str) -> bool:
    frases = _frases(texto)
    cierre = frases[-1] if frases else (texto or "").strip()
    return bool(re.match(r"^(?:seg[uú]n|de acuerdo con|el informe|el estudio)\b", cierre, re.I))


def _sobrecarga_de_cifras(texto: str) -> bool:
    return len(re.findall(r"\b\d+(?:[.,]\d+)?\s*%|\b\d+\s+de\s+cada\s+\d+\b", texto or "")) > 3


def tiene_numero_extenso(texto: str) -> bool:
    """Detecta cualquier número de 2 o más dígitos (rangos, porcentajes, cifras).

    No se aplica por defecto dentro de :func:`revisar`: los números concretos suelen ser
    justo el detalle que sostiene un post, y la regla 4 de :data:`REGLAS_LINKEDIN` ya cubre
    lo que importa ("cero cifras inventadas", no "cero cifras"). Se expone como helper
    independiente para un tenant que necesite prohibir cifras por completo en su propio
    dominio (por ejemplo, para no confundir un rango de puntaje con una cifra editorial);
    ese tenant puede llamar esta función desde su propia validación y sumar el resultado a
    la lista que devuelve ``revisar``.
    """
    return bool(_NUMERO_EXTENSO_RE.search(texto or ""))


# ---------------------------------------------------------------------------
# API pública de control de calidad.
# ---------------------------------------------------------------------------


_MENSAJE_PRIMERA_PERSONA = (
    "Quita la primera persona (yo, mi, nosotros, hice, aprendí, decidí, construí...). "
    "Reescribe la idea como una afirmación directa sobre el mundo, no como una experiencia "
    "atribuida al autor."
)


def _violaciones_clasificadas(texto: str, plataforma: str) -> list[tuple[str, str]]:
    """Todas las violaciones con su CLASE, que es lo que decide qué se hace con cada una.

    Tres clases, copiando el reparto que hace ``_finalizar_post`` en el motor de Aria
    (``linkedin_content.py:1572-1618``), donde en modo permisivo sobreviven duros
    exactamente dos criterios (cero primera persona y longitud mínima) y todo lo demás pasa
    a blando:

    - ``"dura"``: nunca se perdona, ni con ``permisivo``. Sólo la primera persona.
    - ``"mecanica"``: :func:`normalizar` ya la arregla sola, así que si aparece aquí es que
      el llamador no normalizó. Se reporta fuera de permisivo (para que un texto DICTADO por
      el usuario reciba el aviso), nunca dentro.
    - ``"estilo"``: juicio de gusto reparable con una pasada de editor. Es la clase que
      convertía cada tic en un turno perdido: en Aria estos hallazgos disparan una
      reescritura y sólo matan el borrador si la reescritura falla y no es permisivo.
    """
    texto = texto or ""
    plataforma_norm = (plataforma or "linkedin").strip().lower()
    encontradas: list[tuple[str, str]] = []

    if _tiene_primera_persona(texto):
        encontradas.append(("dura", _MENSAJE_PRIMERA_PERSONA))

    if plataforma_norm in _PLATAFORMAS_SIN_HASHTAGS and _tiene_hashtags(texto):
        encontradas.append(
            (
                "mecanica",
                f"Quita los hashtags: en {plataforma_norm} el texto no lleva hashtags por "
                "defecto.",
            )
        )

    if plataforma_norm in _PLATAFORMAS_SIN_EMOJIS and _tiene_emojis(texto):
        encontradas.append(
            (
                "mecanica",
                f"Quita los emojis: en {plataforma_norm} el texto no lleva emojis por defecto.",
            )
        )

    if _tiene_guion_largo(texto):
        encontradas.append(
            (
                "mecanica",
                "Reemplaza el guion largo o medio (— / –) y el doble guion (--) por una coma "
                "o un punto; reescribe la frase sin ese signo.",
            )
        )

    if _cierre_de_formula(texto):
        encontradas.append(
            (
                "estilo",
                "El cierre usa una fórmula gastada ('la pregunta que queda es...', '¿vas "
                "a...?', '¿deberías...?'). Reescribe el último párrafo como una idea que "
                "resuelve o una decisión concreta.",
            )
        )

    if texto.count("?") > 1:
        encontradas.append(
            (
                "estilo",
                "Hay más de una pregunta en el texto. Deja como máximo una pregunta retórica "
                "(cero también es válido) y convierte el resto en afirmaciones.",
            )
        )

    for _nombre, patron, mensaje in _TEXT_TELLS:
        if re.search(patron, texto, re.I | re.S):
            encontradas.append(("estilo", mensaje))

    if _apertura_de_informe(texto):
        encontradas.append(
            (
                "estilo",
                "La primera frase abre como un informe ('según...', 'un estudio "
                "recomienda...', 'X afirma/señala/advierte...'). Empieza directamente con el "
                "dato o el hecho, sin la firma que lo reporta.",
            )
        )

    if _cierre_de_informe(texto):
        encontradas.append(
            (
                "estilo",
                "La última frase cierra citando la fuente ('según...', 'el informe...', 'el "
                "estudio...'). Cierra con la idea o la consecuencia, no con la atribución.",
            )
        )

    if _drama_entrecortado(texto):
        encontradas.append(
            (
                "estilo",
                "Hay una racha de frases muy cortas seguidas (fragmentos de 4 palabras o "
                "menos) usada para fabricar tensión dramática. Combínalas en frases completas "
                "con ritmo de conversación profesional.",
            )
        )

    if _sobrecarga_de_cifras(texto):
        encontradas.append(
            (
                "estilo",
                "Hay demasiadas cifras y porcentajes apilados en el mismo texto. Deja solo "
                "los que sostienen el argumento principal y quita el resto.",
            )
        )

    return encontradas


def revisar(
    texto: str,
    plataforma: str = "linkedin",
    *,
    permisivo: bool = False,
    banco_privado: str = "",
    banco_es_identidad_privada: bool = True,
    prohibir_numeros_extensos: bool = False,
) -> list[str]:
    """Devuelve las violaciones encontradas en ``texto``; vacía significa que pasa.

    Cada elemento es un mensaje accionable que le dice a un modelo exactamente qué
    corregir (nunca solo el nombre de la regla). ``plataforma`` decide qué gates aplican:
    hashtags y emojis solo se penalizan en LinkedIn — en Instagram o TikTok son parte
    normal del formato.

    ``permisivo=True`` significa que el usuario PIDIÓ este post en concreto y lo está
    esperando: se reportan únicamente las violaciones DURAS (hoy, la primera persona), igual
    que hace ``_finalizar_post`` en el motor de Aria. Sin esto, el bypass permisivo de
    Edecán llegaba a un solo sitio (``auditoria.revisar_calidad``) y los gates que de verdad
    mataban el turno no lo consultaban: un tic de estilo en una frase secundaria tumbaba un
    post que la persona había pedido con todas sus letras.

    ``banco_es_identidad_privada`` distingue DE QUIÉN habla el banco de contexto, y solo
    importa para el gate de nombres propios (``_nombres_propios_del_banco``):

    * **Perfil personal** (``True``, el default): el banco son hechos privados de una
      PERSONA. Que su nombre se cuele en un post es una fuga, y el gate lo impide.
    * **Página de organización** (``False``): el banco es información PÚBLICA de la marca
      —qué hace la empresa, cómo se llaman sus productos, en qué país opera—. Ahí ese
      mismo gate se vuelve absurdo y bloqueante: prohíbe justo aquello de lo que la página
      existe para hablar.

    El defecto que esto arregla, medido en la instalación del dueño: el banco de su página
    menciona su marca y su país decenas de veces (es el punto del banco), así que AMBAS
    palabras quedaban "protegidas" como si fueran el nombre privado de una persona. Como la
    violación es clase ``"dura"``, sobrevivía al modo permisivo: todo post de esa página
    chocaba contra una regla imposible de satisfacer, quemaba los tres reintentos del
    escritor y entregaba el candidato menos malo con una advertencia sin sentido pegada.
    El síntoma visible era un post que sonaba a boletín corporativo mientras el titular de
    la imagen —que no pasa por este gate— sí conservaba la voz de la marca. Esa asimetría
    fue la pista.

    ``prohibir_numeros_extensos=True`` activa :func:`tiene_numero_extenso` como gate DURO.
    Por defecto está apagado (``False``) porque la regla 4 de :data:`REGLAS_LINKEDIN` ya
    cubre lo que importa para la mayoría de cuentas ("cero cifras INVENTADAS", no "cero
    cifras") y un número real que sostiene el post no debería tirarlo. Pero un tenant puede
    pedir en su propio perfil ("cero números de dos o más dígitos: todo en palabras") una
    regla más dura que esa, y sin un gate determinista esa instrucción vivía SÓLO en el
    prompt: un post real llegó a publicar "$100MM" tal cual porque el modelo, apurado por
    encajar un titular de fuente en un ángulo que no le calzaba, no la respetó. Es clase
    "dura" (sobrevive a `permisivo`) porque, igual que el nombre del banco, es una regla que
    el propio tenant declaró no negociable para su cuenta -- no un gusto editorial de esta
    función.
    """
    clases_visibles = {"dura"} if permisivo else {"dura", "mecanica", "estilo"}
    encontradas = list(_violaciones_clasificadas(texto, plataforma))
    nombre_filtrado = (
        _menciona_nombre_del_banco(texto, _nombres_propios_del_banco(banco_privado))
        if banco_es_identidad_privada
        else None
    )
    if nombre_filtrado is not None:
        # Es clase "dura" a propósito: sobrevive al modo permisivo. El banco lo
        # dice él mismo en su cabecera ("no son material publicable ni siquiera
        # cuando el tema se parece"), y confiar en que un modelo respete un
        # párrafo del prompt no basta.
        encontradas.insert(
            0,
            (
                "dura",
                f"Quita el nombre «{nombre_filtrado}» del texto: el banco de contexto "
                "es material interno del sistema, nunca se cuenta como historia ni se "
                "menciona a su dueño por nombre. Reescribe la idea como una afirmación "
                "sobre el mundo, sin atribuirla a nadie.",
            ),
        )
    if prohibir_numeros_extensos and tiene_numero_extenso(texto):
        encontradas.insert(
            0,
            (
                "dura",
                "Hay una cifra de dos o más dígitos (un monto, un porcentaje, un año...): "
                "esta cuenta pide escribir TODA cifra en palabras, sin dígitos. Reescribe "
                "cada número como palabra ('cien millones', 'la mayoría', 'este año') y "
                "quita el símbolo o el dígito.",
            ),
        )
    return list(
        dict.fromkeys(
            mensaje for clase, mensaje in encontradas if clase in clases_visibles
        )
    )


def delatores_de_estilo(texto: str, plataforma: str = "linkedin") -> list[str]:
    """Sólo los hallazgos REPARABLES de :func:`revisar` (clase ``"estilo"``).

    Existe para alimentar la pasada de corrección de delatores
    (``auditoria.reparar_delatores``, port de ``linkedin_content.py:1511-1547``): en Aria
    el detector no rechaza por sí solo, le dice al modelo qué señales encontró para que las
    elimine. Estos mensajes son mejor insumo que la lista de nombres de reglas que usa
    Aria, porque cada uno ya trae la instrucción concreta de qué hacer.
    """
    return list(
        dict.fromkeys(
            mensaje
            for clase, mensaje in _violaciones_clasificadas(texto, plataforma)
            if clase == "estilo"
        )
    )


def revisar_visual(kicker: str = "", headline: str = "", support: str = "") -> list[str]:
    """Mismos gates que :func:`revisar`, aplicados al titular visual (kicker/headline/
    support) en vez de al cuerpo del post. Vacía significa que pasa.
    """
    kicker = (kicker or "").strip()
    headline = (headline or "").strip()
    support = (support or "").strip()
    violaciones: list[str] = []

    for _nombre, patron, mensaje in _VISUAL_TELLS:
        if re.search(patron, headline, re.I | re.S):
            violaciones.append(mensaje)

    prefijo = re.match(r"^\s*([\wÁÉÍÓÚÜÑáéíóúüñ.-]+)\s*:\s+", headline)
    if prefijo and _sin_acentos(prefijo.group(1)).lower() in _tokens_categoria(kicker):
        violaciones.append(
            f"El titular repite la categoría que ya nombra la etiqueta ('{kicker}'); "
            "quita el prefijo antes de los dos puntos."
        )

    palabras = re.findall(r"\b[\w$%.-]+\b", headline)
    if len(palabras) < 3:
        violaciones.append(
            "El titular es demasiado corto o vago; usa entre 3 y 8 palabras concretas."
        )
    if len(palabras) > 8 or len(headline) > 58:
        violaciones.append(
            "El titular es demasiado largo; recórtalo a 3-8 palabras y máximo 58 caracteres."
        )

    if len(re.findall(r"\b[\w$%.-]+\b", support)) > 12:
        violaciones.append("El texto de apoyo excede 12 palabras; recórtalo a un solo dato nuevo.")

    if _apoyo_repite_titular(headline, support):
        violaciones.append(
            "El texto de apoyo repite casi las mismas palabras del titular; añade un "
            "dato distinto o bórralo."
        )

    return list(dict.fromkeys(violaciones))


def _apoyo_repite_titular(headline: str, support: str) -> bool:
    """¿El subtítulo dice lo mismo que el titular? (>=60% de tokens útiles compartidos).

    Se extrajo de :func:`revisar_visual` para que :func:`normalizar_visual` pueda usar el
    mismo criterio y VACIAR el support, en vez de reportar una violación que mataría otro
    borrador -- que es lo que hace ``_normalizar_visual`` en Aria.
    """
    h_tokens, s_tokens = _tokens_utiles(headline), _tokens_utiles(support)
    if len(h_tokens) < 2 or len(s_tokens) < 2:
        return False
    return len(h_tokens & s_tokens) / min(len(h_tokens), len(s_tokens)) >= 0.6


def quitar_prefijo_repetido(kicker: str, headline: str) -> str:
    """Quita el prefijo de categoría del titular si ya está en la etiqueta (``kicker``).

    Ejemplo: ``kicker="FINTECH · CAPITAL"``, ``headline="Fintech: el crédito..."`` produce
    ``"El crédito..."``. No reescribe el resto del titular ni cambia el sentido.
    """
    headline = (headline or "").strip()
    prefijo = re.match(r"^\s*([\wÁÉÍÓÚÜÑáéíóúüñ.-]+)\s*:\s*(.+)$", headline, re.S)
    if not prefijo:
        return headline
    categoria = _sin_acentos(prefijo.group(1)).lower()
    if categoria not in _tokens_categoria(kicker):
        return headline
    restante = prefijo.group(2).strip()
    return restante[:1].upper() + restante[1:] if restante else headline


def normalizar(texto: str, plataforma: str = "") -> str:
    """Limpieza determinista y segura: no cambia el sentido del texto.

    - Guion largo/medio (—/–) se reemplaza por una coma.
    - Doble guion usado como separador (" -- ") se reemplaza por un punto y espacio.
    - Con ``plataforma`` puesta y formal (LinkedIn): se BORRAN los hashtags y los emojis.
    - Espacios sueltos antes de un salto de línea se recortan.
    - Tres o más saltos de línea seguidos se colapsan en uno solo en blanco (dos ``\\n``).

    Los hashtags y los emojis se limpian aquí, y no se rechazan en :func:`revisar`, por el
    mismo motivo por el que el guion largo ya se arreglaba aquí: son la violación más
    trivialmente reparable de todas (el mismo regex que los detecta los borra) y en cambio
    quemaban un intento completo de escritura + editor jefe + auditor. Aria no tiene
    siquiera el gate: ``_normalizar_texto_post`` (``linkedin_content.py:266``) repara lo
    mecánico ANTES de cualquier chequeo, de modo que es imposible que sea motivo de descarte.
    ``plataforma`` se pide explícita porque en Instagram o TikTok los hashtags son parte
    normal del formato y borrarlos sería el error opuesto.
    """
    texto = (texto or "").strip().replace("—", ",").replace("–", ",")
    texto = re.sub(r"\s+--\s+", ". ", texto)
    plataforma_norm = (plataforma or "").strip().lower()
    if plataforma_norm in _PLATAFORMAS_SIN_HASHTAGS:
        texto = _HASHTAG_RE.sub("", texto)
    if plataforma_norm in _PLATAFORMAS_SIN_EMOJIS:
        texto = _EMOJI_RE.sub("", texto)
    if plataforma_norm in _PLATAFORMAS_SIN_HASHTAGS | _PLATAFORMAS_SIN_EMOJIS:
        # Borrar un hashtag o un emoji deja dos espacios seguidos donde estaba.
        texto = re.sub(r"[ \t]{2,}", " ", texto)
    # Espacio antes de un signo de puntuación: lo produce el propio reemplazo del guion largo
    # ("cobra — y mucho" -> "cobra , y mucho") y también queda al borrar un hashtag final. Es
    # inequívocamente un error tipográfico en español, así que se limpia siempre.
    texto = re.sub(r"[ \t]+([.,;:!?)])", r"\1", texto)
    texto = re.sub(r"[ \t]+\n", "\n", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


# Topes mecánicos del copy visual, calcados de `_normalizar_visual` (Aria,
# `linkedin_content.py:274-290`). No son gates: son recortes.
_MAX_KICKER_CHARS = 44
_MAX_HEADLINE_CHARS = 100
_MAX_SUPPORT_CHARS = 140


def normalizar_visual(
    visual: Mapping[str, Any] | None, tema: str = "", titular: str = ""
) -> dict[str, str]:
    """Repara mecánicamente el diccionario visual. NUNCA rechaza, nunca devuelve vacío.

    Port de ``_normalizar_visual`` (Aria, ``linkedin_content.py:274-290``). Lo que resuelve,
    en orden de lo que se VE en la imagen:

    - **Kicker y headline nunca faltan.** Degradan a ``titular`` y a ``tema``. Sin esto, un
      escritor que omite el objeto anidado ``visual`` (probable: es un JSON con campos
      anidados) producía una foto pelada, sin titular ni subtítulo, y nadie sabía por qué.
      Aria no puede quedar así jamás: rellena aquí y vuelve a defenderse al componer.
    - **``accent`` forzado a ser una subcadena EXACTA del headline**, buscándola sin
      distinguir mayúsculas y recortando el trozo real. Si no está, se vacía. Un acento que
      no existe literalmente en el headline no resalta nada y nadie se entera.
    - **``support`` vaciado cuando repite el titular** (el delator visual más frecuente): si
      no, el mismo texto sale montado dos veces en la misma imagen.
    - Recortes duros de largo, porque el kicker no se envuelve y un kicker largo se sale del
      lienzo.

    Todo esto se REPARA en vez de reportarse porque el destino de estos campos es una imagen:
    otra puerta donde muera el borrador es lo último que hace falta.
    """
    datos = dict(visual) if isinstance(visual, Mapping) else {}
    tema = (tema or "").strip()
    titular = (titular or "").strip()

    kicker = normalizar(str(datos.get("kicker") or tema).upper())[:_MAX_KICKER_CHARS]
    headline = normalizar(str(datos.get("headline") or titular or tema))[:_MAX_HEADLINE_CHARS]
    headline = quitar_prefijo_repetido(kicker, headline)
    accent = str(datos.get("accent") or "").strip()
    if accent:
        indice = headline.lower().find(accent.lower())
        accent = headline[indice : indice + len(accent)] if indice >= 0 else ""
    support = normalizar(str(datos.get("support") or ""))[:_MAX_SUPPORT_CHARS]
    if support and _apoyo_repite_titular(headline, support):
        support = ""
    return {"kicker": kicker, "headline": headline, "accent": accent, "support": support}


# ---------------------------------------------------------------------------
# Cooldown ESTRUCTURAL: no repetir la misma ARQUITECTURA de post.
# ---------------------------------------------------------------------------

_PORCENTAJE_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*%|\b\d+\s+de\s+cada\s+\d+\b", re.I)


def firma_estructura(texto: str, visual: Mapping[str, Any] | None = None) -> set[str]:
    """La "forma" de un post, para poder prohibir repetirla en el siguiente.

    Port de ``_firma_estructura`` (Aria, ``linkedin_content.py:313-330``). El cooldown de
    :mod:`edecan_creative.agenda` es de TEMA y de geografía; esto ataca lo otro, que el feed
    se sienta igual: tres posts seguidos abriendo con un porcentaje, o tres cerrando con una
    pregunta. Y es el lugar CORRECTO donde vive la prohibición de cerrar con pregunta:
    condicionada al historial, no absoluta (ver :data:`_CIERRE_FORMULA_RE`).
    """
    texto = (texto or "").strip()
    campos = visual if isinstance(visual, Mapping) else {}
    primera = re.split(r"(?<=[.!?])\s+", texto, maxsplit=1)[0] if texto else ""
    headline = str(campos.get("headline") or "")
    firmas: set[str] = set()
    if _PORCENTAJE_RE.search(primera):
        firmas.add("apertura_porcentaje")
    if _PORCENTAJE_RE.search(headline):
        firmas.add("titular_porcentaje")
    if len(_PORCENTAJE_RE.findall(texto)) >= 2 and re.search(
        r"\b(?:informe|estudio|reporte|encuesta|datos)\b", texto, re.I
    ):
        firmas.add("reporte_de_cifras")
    if texto.endswith("?"):
        firmas.add("cierre_pregunta")
    return firmas


def firmas_prohibidas_recientes(historial: Sequence[Mapping[str, Any]] | None) -> set[str]:
    """Qué formas están vetadas en este turno, según los últimos posts que SÍ salieron.

    Port de ``_firmas_prohibidas_recientes`` (Aria, ``linkedin_content.py:337-355``). Lee
    la clave ``firma`` de cada entrada del historial de :mod:`edecan_creative.agenda` (la
    escribe ``social.empaquetar_borrador_social``); una entrada sin ``firma`` -- las que ya
    estaban guardadas antes de este port -- simplemente no aporta nada.
    """
    recientes = [dict(entrada) for entrada in list(historial or [])[-3:]]
    firmas = [
        {str(f) for f in (entrada.get("firma") or []) if str(f).strip()} for entrada in recientes
    ]
    prohibidas: set[str] = set()
    if any({"apertura_porcentaje", "titular_porcentaje"} & f for f in firmas[-2:]):
        prohibidas.update(("apertura_porcentaje", "titular_porcentaje"))
    if firmas and "reporte_de_cifras" in firmas[-1]:
        prohibidas.add("reporte_de_cifras")
    if firmas and "cierre_pregunta" in firmas[-1]:
        prohibidas.add("cierre_pregunta")
    return prohibidas


def instruccion_cooldown_estructura(prohibidas: Iterable[str]) -> str:
    """El bloque de prompt que prohíbe las formas ya usadas. Vacío si no hay ninguna.

    Port de ``_instruccion_cooldown_estructura`` (Aria, ``linkedin_content.py:358-370``).
    """
    prohibidas = set(prohibidas or ())
    reglas: list[str] = []
    if {"apertura_porcentaje", "titular_porcentaje"} & prohibidas:
        reglas.append(
            "PROHIBIDO abrir con un porcentaje y PROHIBIDO poner porcentajes en el titular "
            "de la imagen"
        )
    if "reporte_de_cifras" in prohibidas:
        reglas.append("PROHIBIDO estructurar el post como una enumeración de cifras de un informe")
    if "cierre_pregunta" in prohibidas:
        reglas.append(
            "PROHIBIDO cerrar con una pregunta; termina con una consecuencia o una decisión "
            "concreta"
        )
    if not reglas:
        return ""
    return "\n\nCOOLDOWN ESTRUCTURAL OBLIGATORIO (el post anterior ya usó esta forma):\n- " + (
        "\n- ".join(reglas)
    )
