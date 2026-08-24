"""Búsqueda de mensajes + auto-título + segmentación de temas (PHASE2 §223-228).

Lógica pura, determinista y sin dependencias externas (solo stdlib), pensada
para que el orquestador pueda decidir «¿dónde hablamos de X?», ponerle un título
que de verdad describa la conversación y trocear un hilo largo en subtemas sin
pagar una llamada a un modelo por cada mensaje.

Contrato de tokenización (el mismo en todas las funciones): texto -> minúsculas
(`casefold`), se quitan las tildes y diacríticos (NFKD + eliminar marcas de
combinación), y se trocea por caracteres no alfanuméricos. «Música», «musica» y
«MÚSICA» colapsan al mismo token; por eso la búsqueda funciona aunque la persona
escriba sin acentos o en mayúsculas.

POR QUÉ un índice invertido en memoria y no embeddings: la búsqueda «semántica»
de este módulo es, a propósito, solapamiento léxico determinista. Cubre el caso
real más frecuente (encontrar el hilo donde se mencionó «migración», «factura»,
«token») sin los costos, la no-reproducibilidad y la infraestructura de un store
vectorial. Cuando haya que escalar o recuperar significado puro, se reemplazan
`_POSTINGS`/`_METADATA` por Postgres (columna `tsvector` con índice GIN, o
`pgvector`) y una tabla `messages(id, conversation_id, body, metadata)`: la firma
de las funciones no cambia, solo su fuente de datos. `clear_index()` es la costura
para reconstruir el índice desde la base de datos.

El estado es global de módulo (un único índice por proceso). Es deliberado: la
búsqueda opera sobre la conversación viva en memoria. Si en el futuro conviven
varias conversaciones en el mismo proceso, se puede aislar pasando una instancia
o particionando por `conversation_id` dentro de `metadata`.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable

# ---------------------------------------------------------------------------
# Estado del índice invertido (fuente de datos reemplazable por Postgres).
# ---------------------------------------------------------------------------

# token -> ids de mensaje que lo contienen. La clave está normalizada
# (minúsculas + sin tildes), igual que la salida de `_tokenize`.
_POSTINGS: dict[str, set[str]] = {}

# msg_id -> tokens del mensaje (con repeticiones, para poder contar frecuencia).
_TOKENS: dict[str, list[str]] = {}

# msg_id -> metadata libre. Ver `filter_messages` para el esquema que consume.
_METADATA: dict[str, dict] = {}

# Orden de inserción de los msg_id; desempata rankings de forma estable.
_ORDER: list[str] = []


# ---------------------------------------------------------------------------
# Normalización y tokenización
# ---------------------------------------------------------------------------


def _strip_accents(text: str) -> str:
    """Quita tildes/diacríticos vía descomposición canónica NFKD.

    «á» -> «a», «ñ» -> «n». Usa `casefold` (no `lower`) para que también
    colapsen variantes no-ASCII como la ß.
    """

    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _tokenize(text: str) -> list[str]:
    """Trocea `text` en tokens normalizados, conservando repeticiones.

    Por qué conserva duplicados: `search_messages` usa la frecuencia del token
    dentro de cada mensaje como peso (scoring «tf-like»), así que un mensaje que
    menciona «api» tres veces debe puntuar más que uno que la menciona una.
    """

    return re.findall(r"[a-z0-9]+", _strip_accents(text))


def _norm_value(value: object) -> str:
    """Normaliza un valor de metadata para comparación case/accent-insensitive."""

    return _strip_accents(str(value)).strip()


# ---------------------------------------------------------------------------
# Stopwords de búsqueda (SOLO se aplican a la consulta, nunca al indexado)
# ---------------------------------------------------------------------------

# Artículos, preposiciones, conectores y pronombres interrogativos. Filtrarlos
# de la CONSULTA evita que «¿dónde hablamos de la api?» puntúe cada mensaje por
# la palabra «de» o «la». El índice conserva todos los tokens: no se pierde
# cobertura, solo se evita ranquear por ruido conectivo.
_STOPWORDS = frozenset(
    {
        "a",
        "al",
        "and",
        "ante",
        "cabe",
        "como",
        "con",
        "contra",
        "cuando",
        "cuanta",
        "cuantas",
        "cuanto",
        "cuantos",
        "cual",
        "cuales",
        "de",
        "del",
        "donde",
        "el",
        "en",
        "entre",
        "es",
        "esta",
        "este",
        "hacia",
        "hasta",
        "hay",
        "is",
        "la",
        "las",
        "lo",
        "los",
        "me",
        "ni",
        "no",
        "o",
        "of",
        "para",
        "pero",
        "por",
        "que",
        "quien",
        "quienes",
        "se",
        "si",
        "sin",
        "sobre",
        "te",
        "the",
        "tras",
        "un",
        "una",
        "unas",
        "unos",
        "what",
        "y",
        "ya",
        "yo",
    }
)


# ---------------------------------------------------------------------------
# 1. Búsqueda (PHASE2 §223)
# ---------------------------------------------------------------------------


def index_message(msg_id: str, text: str, metadata: dict | None = None) -> None:
    """Indexa un mensaje en el índice invertido en memoria.

    Si `msg_id` ya estaba indexado, se reemplaza (borra sus postings viejos antes
    de añadir los nuevos) para que una corrección del texto no deje tokens
    huérfanos apuntando a contenido que ya no existe. `metadata` se copia, no se
    conserva la referencia del llamador.
    """

    tokens = _tokenize(text)

    if msg_id in _TOKENS:
        for token in set(_TOKENS[msg_id]):
            postings = _POSTINGS.get(token)
            if postings is not None:
                postings.discard(msg_id)
                if not postings:
                    _POSTINGS.pop(token, None)

    _TOKENS[msg_id] = tokens
    for token in set(tokens):
        _POSTINGS.setdefault(token, set()).add(msg_id)
    _METADATA[msg_id] = dict(metadata or {})

    if msg_id not in _ORDER:
        _ORDER.append(msg_id)


def search_messages(query: str, *, limit: int = 10) -> list[str]:
    """Devuelve ids de mensaje ranqueados por solapamiento de tokens con `query`.

    Scoring «tf-like»: por cada token de la consulta (sin stopwords) se suma su
    frecuencia dentro del mensaje candidato. Un mensaje que repite el término
    gana a uno que lo menciona una vez, y tocar más términos de la consulta gana
    a tocar uno solo. Los empates se deshacen por orden de indexado, de modo que
    el resultado es idéntico entre ejecuciones. `limit <= 0` devuelve lista vacía.
    """

    query_tokens = [t for t in _tokenize(query) if t not in _STOPWORDS]
    if not query_tokens:
        return []

    candidates: set[str] = set()
    for token in query_tokens:
        candidates.update(_POSTINGS.get(token, ()))

    if not candidates:
        return []

    scores: dict[str, int] = {}
    for msg_id in candidates:
        freq = Counter(_TOKENS[msg_id])
        scores[msg_id] = sum(freq.get(t, 0) for t in query_tokens)

    position = {mid: i for i, mid in enumerate(_ORDER)}
    ranked = sorted(candidates, key=lambda mid: (-scores[mid], position.get(mid, len(_ORDER))))

    if limit <= 0:
        return []
    return ranked[:limit]


def filter_messages(
    results: Iterable[str],
    *,
    project: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    file: str | None = None,
    person: str | None = None,
    tool: str | None = None,
    artifact: str | None = None,
) -> list[str]:
    """Filtra una lista de ids por metadata (PHASE2 §224), preservando el orden.

    Esquema de metadata que consume (los valores string pueden ser también una
    lista, p. ej. varios archivos o personas):

        {
            "project": str,
            "date" | "created_at" | "timestamp" | "fecha": "YYYY-MM-DD" o ISO-8601,
            "file" | "files": str | list[str],
            "person" | "people": str | list[str],
            "tool": str,
            "artifact" | "artifacts": str | list[str],
        }

    Semántica: un filtro solo se aplica si no es `None`. Para `project`/`file`/
    `person`/`tool`/`artifact` se exige igualdad normalizada (case y acentos
    insensibles); para `date_from`/`date_to` se compara el día lexicográficamente
    (inclusivo). Un mensaje sin la clave pedida NO pasa el filtro: ante la duda
    se descarta, no se deja pasar. Si `results` trae un id desconocido (no está
    en el índice) y hay filtros activos, se descarta por no poder verificar su
    metadata.
    """

    wanted = [
        ("project", project, ("project",)),
        ("file", file, ("file", "files")),
        ("person", person, ("person", "people")),
        ("tool", tool, ("tool",)),
        ("artifact", artifact, ("artifact", "artifacts")),
    ]

    def passes(meta: dict) -> bool:
        for _name, wanted_value, keys in wanted:
            if wanted_value is None:
                continue
            stored = _first_present(meta, keys)
            if not _matches_value(stored, wanted_value):
                return False

        date = _extract_date(meta)
        if date_from is not None and (date is None or date < _norm_date(date_from)):
            return False
        if date_to is not None and (date is None or date > _norm_date(date_to)):
            return False
        return True

    out: list[str] = []
    for msg_id in results:
        if passes(_METADATA.get(msg_id, {})):
            out.append(msg_id)
    return out


def clear_index() -> None:
    """Vacía el índice en memoria.

    Además de los tests, es la costura para reconstruir el índice desde Postgres:
    `clear_index()` + re-indexar todos los mensajes traídos de la tabla.
    """

    _POSTINGS.clear()
    _TOKENS.clear()
    _METADATA.clear()
    _ORDER.clear()


def _first_present(meta: dict, keys: tuple[str, ...]) -> object:
    for key in keys:
        if key in meta and meta[key] is not None:
            return meta[key]
    return None


def _matches_value(stored: object, wanted_value: str) -> bool:
    if stored is None:
        return False
    if isinstance(stored, (list, tuple, set, frozenset)):
        return any(_matches_value(item, wanted_value) for item in stored)
    if isinstance(stored, bool):
        return False
    return _norm_value(stored) == _norm_value(wanted_value)


def _extract_date(meta: dict) -> str | None:
    for key in ("date", "created_at", "timestamp", "fecha"):
        value = meta.get(key)
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            continue
        text = str(value).strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text[:10]):
            return text[:10]
    return None


def _norm_date(value: str) -> str:
    return str(value).strip()[:10]


# ---------------------------------------------------------------------------
# 2. Auto-título (PHASE2 §227)
# ---------------------------------------------------------------------------

# Palabras con las que una persona SUELTA una petición en vez de describir el
# tema: interrogativos («¿cómo...?») y verbos de orden («busca», «dime»). Un
# título debe decir QUÉ se trató, no el imperativo con el que se pidió.
_LEADING_WORDS = frozenset(
    {
        "como",
        "cuando",
        "cuanto",
        "cuanta",
        "cuantos",
        "cuantas",
        "cual",
        "cuales",
        "que",
        "quien",
        "quienes",
        "donde",
        "dime",
        "decime",
        "cuentame",
        "mostrame",
        "muestrame",
        "busca",
        "buscalo",
        "buscala",
        "buscar",
        "explica",
        "explicame",
        "genera",
        "generar",
        "crea",
        "crear",
        "haz",
        "haceme",
        "ayudame",
        "resume",
        "resumime",
        "escribe",
        "escribime",
        "necesito",
        "quiero",
        "puedes",
        "podrias",
        "dame",
        "pasame",
        "mandame",
        "envia",
        "enviame",
        "me",
    }
)

# Frases de dos palabras que hay que saltar juntas, no por separado: «por qué»
# (interrogativo) y «por favor» (fórmula de cortesía). Saltar solo «por» dejaría
# un título que empieza en «qué»/«favor» y no representa el tema.
_LEADING_TWO_WORD = frozenset(
    {
        ("por", "que"),
        ("por", "favor"),
    }
)

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")

# Saludos que abren una conversación pero no describen su tema. Se saltan al
# buscar la primera oración con contenido: «hola. Vamos a diseñar el dashboard»
# debe titular «Vamos a diseñar el dashboard», no «Hola».
_GREETINGS = frozenset(
    {
        "hola",
        "buenas",
        "buen dia",
        "buenos dias",
        "buenas tardes",
        "buenas noches",
        "que tal",
        "hey",
        "hello",
        "hi",
        "saludos",
        "hola a todos",
    }
)


def suggest_title(text: str) -> str:
    """Propone un título corto y representativo de `text` (heurística pura).

    Por qué este orden: (1) se toma la PRIMERA oración con contenido (la
    conversación suele abrir con el tema, no con el saludo); (2) se le quitan las
    palabras interrogativas y los verbos de orden del inicio («¿cómo hago X?» ->
    «hago X»); (3) se recorta a ~60 caracteres por un límite de palabra (nunca se
    corta a mitad de palabra); (4) se capitaliza solo la primera letra (el resto
    conserva mayúsculas de siglas como «API»). Si tras todo queda vacío, se
    devuelve «Conversación»: el título nunca puede ser una cadena vacía.
    """

    for sentence in _sentences(text):
        core = _strip_leading(sentence).strip()
        core = core.rstrip(".,;:!?¿¡…").strip()
        if not core:
            continue
        if _strip_accents(core).strip() in _GREETINGS:
            continue
        core = _truncate_words(core, 60)
        if not core:
            continue
        return core[0].upper() + core[1:]
    return "Conversación"


def title_changed_significantly(old: str, new: str, *, threshold: float = 0.3) -> bool:
    """True si el solapamiento de tokens entre `old` y `new` es demasiado bajo.

    Mide el coeficiente de Jaccard sobre los conjuntos de tokens. Con `threshold`
    por defecto 0.3, dos títulos que no comparten al menos el 30% de su vocabulario
    se consideran «cambio radical» y justifican actualizar el título (PHASE2 §227:
    «actualizar cuando el tema cambie radicalmente»). Casos borde: ambos vacíos ->
    False (no hubo cambio); solo uno vacío -> True (sí lo hubo).
    """

    old_tokens = set(_tokenize(old))
    new_tokens = set(_tokenize(new))

    if not old_tokens and not new_tokens:
        return False
    if not old_tokens or not new_tokens:
        return True

    overlap = len(old_tokens & new_tokens) / len(old_tokens | new_tokens)
    return overlap < threshold


# ---------------------------------------------------------------------------
# 3. Segmentación de temas (PHASE2 §228)
# ---------------------------------------------------------------------------


def segment_topics(messages: list[str], *, threshold: float = 0.3) -> list[dict]:
    """Trocea una conversación larga en subtemas por caída de solapamiento.

    Por qué funciona: cuando dos mensajes consecutivos cambian de tema, su
    vocabulario deja de solaparse. Se calcula el Jaccard entre cada par
    consecutivo; si baja de `threshold`, se abre un segmento nuevo. Cada segmento
    devuelve `{"start", "end", "summary"}` con índices inclusivos sobre la lista
    y un `summary` con las palabras clave más frecuentes del tramo (sin
    stopwords). Es determinista: ante empate de frecuencia, gana la palabra que
    apareció antes.

    Casos borde: lista vacía -> `[]`; un solo mensaje -> un segmento `[0, 0]`;
    un mensaje vacío intercalado produce solapamiento 0 (es un corte natural).
    """

    if not messages:
        return []

    n = len(messages)
    boundaries = [0]
    previous: set[str] | None = None

    for i in range(n):
        current = set(_tokenize(messages[i]))
        if previous is not None and _jaccard(previous, current) < threshold:
            boundaries.append(i)
        previous = current

    segments: list[dict] = []
    for j, start in enumerate(boundaries):
        end = boundaries[j + 1] - 1 if j + 1 < len(boundaries) else n - 1
        segments.append(
            {
                "start": start,
                "end": end,
                "summary": _top_keywords(messages[start : end + 1]),
            }
        )
    return segments


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def _top_keywords(messages: Iterable[str], top_n: int = 5) -> str:
    counter: Counter[str] = Counter()
    first_seen: dict[str, int] = {}

    for message in messages:
        for token in _tokenize(message):
            if token in _STOPWORDS:
                continue
            if token not in first_seen:
                first_seen[token] = len(first_seen)
            counter[token] += 1

    ranked = sorted(counter, key=lambda t: (-counter[t], first_seen[t]))
    return " ".join(ranked[:top_n])


# ---------------------------------------------------------------------------
# 4. Tarjeta de resumen de conversación (PHASE2 §226)
# ---------------------------------------------------------------------------

_DECISION_PATTERNS = (
    r"\bdecidi\b",
    r"\bdecidimos\b",
    r"\bdecidido\b",
    r"\bvamos a\b",
    r"\bvoy a\b",
    r"\bhemos decidido\b",
    r"\bacordamos\b",
    r"\bquedamos en\b",
    r"\bharemos\b",
    r"\bse hara\b",
    r"\bqueda decidido\b",
)

_PENDING_PATTERNS = (
    r"\bpendiente\b",
    r"\bpendientes\b",
    r"\bfalta\b",
    r"\bfaltan\b",
    r"\bhay que\b",
    r"\bqueda por\b",
    r"\bpor hacer\b",
    r"\bfalta por\b",
    r"\btodavia falta\b",
    r"\baun falta\b",
)

_FILE_PATTERNS = (
    r"\barchivo\b",
    r"\barchivos\b",
    r"\bfichero\b",
    r"\bficheros\b",
    r"\bdocumento\b",
    r"\bdocumentos\b",
    r"\badjunto\b",
    r"\badjuntos\b",
)


def summarize_conversation(messages: list[str]) -> dict:
    """Extrae decisiones, pendientes y archivos por coincidencia de palabras clave.

    Devuelve `{"decisions": [...], "pending": [...], "files": [...]}` con las
    ORACIONES (no los mensajes enteros) que contienen el disparador, limpias de
    signos de cierre y sin duplicados, en orden de aparición. El matching corre
    sobre texto normalizado (minúsculas + sin tildes), así que «decidí» se detecta
    aunque se escriba «decidi». Una misma oración puede aparecer en varias
    categorías: los tres conjuntos son independientes, no excluyentes.
    """

    decisions: list[str] = []
    pending: list[str] = []
    files: list[str] = []

    for message in messages:
        for sentence in _sentences(message):
            normalized = _strip_accents(sentence)
            clean = sentence.strip().rstrip(".,;:!?¿¡…").strip()
            if not clean:
                continue
            if any(re.search(p, normalized) for p in _DECISION_PATTERNS):
                decisions.append(clean)
            if any(re.search(p, normalized) for p in _PENDING_PATTERNS):
                pending.append(clean)
            if any(re.search(p, normalized) for p in _FILE_PATTERNS):
                files.append(clean)

    return {
        "decisions": _dedupe(decisions),
        "pending": _dedupe(pending),
        "files": _dedupe(files),
    }


# ---------------------------------------------------------------------------
# Helpers de oraciones y texto
# ---------------------------------------------------------------------------


def _sentences(text: str) -> list[str]:
    """Trocea en oraciones por `.`/`!`/`?` seguido de espacio, o por salto de línea."""

    return [s for s in _SENTENCE_END.split(text) if s.strip()]


def _strip_leading(sentence: str) -> str:
    """Quita del inicio puntuación y palabras interrogativas/de orden.

    Se recorta primero la puntuación («¿», «¡», comas...), y después palabras
    sueltas o pares («por qué», «por favor») que no describen el tema.
    """

    stripped = sentence.strip()
    while stripped and not stripped[0].isalnum():
        stripped = stripped[1:].lstrip()

    while stripped:
        match = re.match(r"\w+", stripped)
        if not match:
            break
        word = match.group()
        rest = stripped[match.end() :]
        normalized = _strip_accents(word)

        next_match = re.match(r"\s*(\w+)", rest)
        if next_match and (normalized, _strip_accents(next_match.group(1))) in _LEADING_TWO_WORD:
            stripped = rest[next_match.end() :].lstrip()
            continue

        if normalized in _LEADING_WORDS:
            stripped = rest.lstrip()
            continue
        break

    return stripped


def _truncate_words(text: str, max_len: int) -> str:
    """Recorta `text` a `max_len` caracteres sin partir una palabra."""

    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    space = cut.rfind(" ")
    if space > 0:
        cut = cut[:space]
    return cut.rstrip()


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))