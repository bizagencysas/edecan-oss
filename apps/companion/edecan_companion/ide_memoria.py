"""Memoria persistente del proyecto para el agente del IDE.

Qué problema resuelve: hoy cada sesión de agente arranca en blanco sobre el
repo en el que está parado. Si en la sesión de ayer el agente descubrió que
"los tests de `packages/db` necesitan Postgres real, no dobles" o que
"`routers/ide.py` es cuello de botella y no se toca sin avisar", esa
información se pierde al cerrar la sesión -- la siguiente la vuelve a
descubrir a los golpes, o el usuario tiene que repetirla a mano otra vez.
Esto es justo lo que Antigravity llama "memoria de agentes": lo que hace que
la décima sesión sobre un repo sea mejor que la primera.

Este módulo NO es historial de conversación -- ese ya existe (`ide_projects.py`
guarda conversaciones completas, y `ide_sessions.py` reconstruye el contexto de
una conversación a partir de sus turnos). La distinción que importa y que este
diseño respeta activamente:

    HISTORIAL = qué se dijo, turno por turno, en ESTA conversación.
    MEMORIA   = lo poco que vale la pena que el agente recuerde de ESTE repo
                dentro de seis meses, venga de la conversación que venga.

Criterio explícito de qué entra (y todo lo demás, por defecto, NO entra --
una memoria que guarda todo por si acaso es ruido y empeora las respuestas,
no las mejora): un recuerdo válido es siempre uno de estos cuatro tipos
(`MemoryKind`), y `remember()` obliga a declarar cuál:

- ``"convencion"``   -- una norma propia de ESTE repo que no es obvia desde
  afuera (estilo, proceso, una regla que el equipo decidió). Ejemplo real:
  "español LATAM con tú, nunca voseo".
- ``"ubicacion"``     -- dónde vive algo que costó encontrar. Ejemplo real:
  "el intérprete de tests vive en `.venv/bin/python`, no en el `python` del
  PATH (pyenv falla)".
- ``"error_evitar"``  -- un error ya cometido en este repo y cómo se evitó.
  Ejemplo real: "`tsc --noEmit` pasa limpio pero el build igual falla por
  comillas sin escapar en JSX -- correr también `next lint`".
- ``"decision"``      -- una decisión explícita, tomada con el usuario, que
  aplica hacia adelante (no una opinión de paso). Ejemplo real: "no ser un
  fork de VS Code: decidido, no pendiente". Es el único tipo que además
  puede guardar su porqué -- ver la sección de ADR más abajo.

Lo que NUNCA debe guardarse aquí, aunque alguien lo intente: pasos
intermedios de una tarea ("leí el archivo X"), el contenido de un archivo,
la salida de un comando, o cualquier cosa que solo tenga sentido dentro de
la conversación que la generó -- eso es historial, ya vive en otro lado.
Para frenar el "por si acaso" incluso dentro de los cuatro tipos válidos,
``remember()`` aplica tres filtros deliberados (ver docstrings de cada
función): tamaño (ni una frase suelta ni un ensayo), una lista de frases
triviales que la gente teclea sin querer que se recuerden ("ok", "listo",
"gracias"), y deduplicación (repetir el mismo hecho refuerza su importancia
en vez de crear una fila nueva).

Recuperación por relevancia, no volcado completo: ``recall()`` no devuelve
"todo lo que sabe del repo" -- eso satura el prompt exactamente como
`ide_reglas.py` evita hacerlo con `AGENTS.md`. Puntúa cada recuerdo contra
las palabras del turno actual y devuelve solo los que de verdad se tocan con
lo que se está preguntando ahora mismo (ver docstring de ``recall`` para el
método exacto y su límite documentado: es coincidencia léxica, no semántica
-- el plan de paridad (2.2) ya prevé un buscador semántico aparte sobre
pgvector para el codebase; este módulo es deliberadamente más simple y
100% local porque `edecan_companion` no depende de `edecan_core` (ver
``pyproject.toml`` de este paquete: es el único paquete pensado para
instalarse solo, en la máquina del usuario, sin Postgres).

Igual que ``ide_projects.py`` y ``ide_checkpoints.py``, deliberadamente NO
importa nada de ``ide_sessions.py`` ni de ``routers/ide.py`` (cuellos de
botella de esta tanda). La única entrada externa es un ``workspace_id`` ya
autorizado en ``WorkspaceStore`` -- toda la validación de que ese workspace
existe vive ahí, no se reimplementa aquí. Integración prevista (no se hace
desde este archivo): antes de armar el prompt de sistema de un turno,
``ide_sessions.py`` llamaría a ``MemoriaStore.recall_as_prompt_block(
workspace_id, texto_del_turno)`` y agregaría el resultado (si no es
``None``) igual que ya hace con ``ProjectRules.as_prompt_block()``; al
terminar un turno en el que el agente descubrió algo que vale la pena
recordar, llamaría a ``MemoriaStore.remember(...)`` una vez por hecho.

Una decisión guarda su porqué, no solo su conclusión (ADR)
----------------------------------------------------------
Un recuerdo de tipo ``"decision"`` acepta además tres campos opcionales que
ningún otro tipo tiene: ``alternativas`` (qué otras opciones se evaluaron),
``por_que_no`` (por qué se descartaron) y ``se_invalida_si`` (qué tendría
que cambiar para volver a considerarlas). Guardar solo la conclusión ("no
ser un fork de VS Code") deja la decisión indefensa: a los tres meses
alguien que no sabe qué se descartó la revierte de buena fe -- y ese alguien
casi siempre es el propio agente en otra sesión, que no estuvo en la
conversación donde se decidió.

Tres detalles del diseño que no son accidentales:

- Los tres campos son EXCLUSIVOS de ``"decision"``: con cualquier otro tipo,
  ``remember()`` los rechaza. Una convención o una ubicación no descartan
  alternativas -- enuncian algo que ya es así, sin nada que reconsiderar.
  Aceptarlos "por si acaso" convertiría el criterio de los cuatro tipos en
  cuatro cajones con los mismos campos, que es exactamente lo que este
  módulo evita.
- ``recall()`` busca también DENTRO de esos campos, no solo en la
  conclusión. Quien está por revertir una decisión no escribe la conclusión
  ("no somos un fork"), escribe la alternativa que está a punto de retomar
  ("¿y si forkeamos VS Code?"). Si la alternativa descartada no fuera
  buscable, el recuerdo llegaría tarde, que para el caso es igual a no
  llegar.
- La deduplicación (``_dedupe_key``) sigue mirando solo la conclusión. Así,
  volver a guardar la misma decisión más adelante -- ya con las
  alternativas que en su momento nadie anotó -- ENRIQUECE la fila que ya
  existe en vez de crear una segunda ADR que la contradiga a medias. Y
  enriquecer es literal: las alternativas que llegan se suman a las
  guardadas en vez de reemplazarlas, porque cada sesión anota las opciones
  que ella evaluó y ninguna vuelve a escribir la lista entera.

Compatibilidad: los recuerdos guardados antes de que estos campos
existieran se leen igual, sin migración ni paso de conversión, y una fila
sin ADR se sigue escribiendo en disco exactamente como antes (los campos
vacíos no se serializan).

Alcance: solo PROYECTO, nunca global
------------------------------------
Toda la memoria de este módulo vive atada a un ``workspace_id`` -- no existe
(ni debe agregarse) un alcance "global" compartido entre todos los
workspaces, ni un quinto ``MemoryKind`` para "hechos verificados sobre el
mundo exterior al repo" (p. ej. "la versión estable de Node.js es la
22.x"). Ese es, a propósito, el trabajo de un módulo hermano y separado,
``ide_conocimiento.py``: exige una fuente (URL) real antes de guardar algo
así y hace caducar el hecho con el tiempo -- ver su docstring, que explica
por qué eligió ser "un módulo aparte, no una quinta MemoryKind". Meter esa
clase de hecho acá, sin fuente obligatoria ni caducidad, reabriría
exactamente el agujero que ``ide_conocimiento.py`` fue diseñado para cerrar:
el agente podría guardar una suposición propia como si fuera un hecho
comprobado, y -- peor todavía, si ese tipo tuviera alcance global -- esa
suposición sin verificar contaminaría de una sola vez la memoria de TODOS
los repos del usuario, no solo la de este. Si en el futuro se agrega un tipo
de recuerdo que no sea 100% específico de este repo, la pregunta correcta no
es "¿qué alcance le pongo?" sino "¿esto es en realidad conocimiento
verificable, y por lo tanto pertenece a ``ide_conocimiento.py``?".
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from edecan_companion.ide_workspaces import IDEWorkspaceError, WorkspaceStore

MemoryKind = Literal["convencion", "ubicacion", "error_evitar", "decision"]

MEMORY_KINDS: tuple[MemoryKind, ...] = (
    "convencion",
    "ubicacion",
    "error_evitar",
    "decision",
)

# -- Topes de tamaño de un recuerdo individual --------------------------- #
# Ver punto "criterio explícito" del docstring del módulo: un recuerdo es un
# hecho compacto, no una frase suelta (mínimo) ni un ensayo (máximo). El
# máximo también evita que alguien use esto como un segundo historial
# completo pegando párrafos enteros de contexto.
MIN_CONTENT_CHARS = 12
MAX_CONTENT_CHARS = 500

# -- Topes de los campos de ADR (solo kind="decision") ------------------- #
# Una alternativa se nombra, no se explica ("Postgres", "fork de VS Code"),
# así que su mínimo NO puede ser el de un hecho completo: exigirle 12
# caracteres rechazaría entradas legítimas como "SQLite". El filtro de
# frases triviales sigue aplicando igual, que es lo que de verdad frena el
# ruido acá. El máximo, en cambio, es más corto que el de un recuerdo a
# propósito: si una alternativa necesita 500 caracteres, eso es el
# `por_que_no`, no el nombre de la opción.
MIN_ALTERNATIVA_CHARS = 2
MAX_ALTERNATIVA_CHARS = 120

# Una ADR registra las opciones que se evaluaron de verdad. Más de un puñado
# no es una decisión documentada, es una lluvia de ideas -- y encima le come
# al prompt el espacio que necesita la conclusión.
MAX_ALTERNATIVAS = 6

# Tope duro de recuerdos por workspace. Al superarlo, `remember()` purga
# primero el de menor `importance` (y, en empate, el usado hace más tiempo)
# antes de agregar el nuevo -- ver `_evict_weakest`. Un repo con memoria
# ilimitada termina en el mismo problema que "guardar todo por si acaso":
# ruido que le gana espacio de prompt a lo que sí importa.
MAX_NOTES_PER_WORKSPACE = 200

# Cuántos recuerdos como máximo devuelve `recall()` por defecto -- suficiente
# para dar contexto real sin acaparar el prompt del turno.
DEFAULT_RECALL_K = 6

# Tope de caracteres del bloque que arma `recall_as_prompt_block()`. Mismo
# espíritu que `ide_reglas.MAX_RULES_CHARS`: nunca dejar que el contexto
# recordado se coma el presupuesto de la tarea real.
MAX_PROMPT_BLOCK_CHARS = 4_000

# Frases que la gente teclea como acuse de recibo, no como un hecho que
# valga la pena recordar. Comparación exacta tras normalizar (minúsculas,
# espacios colapsados, sin puntuación final) -- deliberadamente NO es una
# lista de "palabras prohibidas" dentro de un texto más largo, porque eso
# rechazaría de más (un recuerdo real puede perfectamente contener la
# palabra "listo").
_TRIVIAL_PHRASES = frozenset(
    {
        "ok",
        "okay",
        "vale",
        "listo",
        "gracias",
        "de acuerdo",
        "entendido",
        "perfecto",
        "genial",
        "bien",
        "correcto",
        "hola",
        "chao",
        "adios",
        "si",
        "sí",
        "no",
        "ninguno",
        "nada",
        "buen trabajo",
        "gracias totales",
    }
)

# Palabras vacías del español para tokenizar con algo de señal real. Corta,
# a propósito: esto no es un tokenizador lingüístico completo, solo lo
# suficiente para que "el" o "de" no infle la coincidencia entre un
# recuerdo y el turno actual.
_STOPWORDS = frozenset(
    {
        "el",
        "la",
        "los",
        "las",
        "de",
        "del",
        "en",
        "un",
        "una",
        "unos",
        "unas",
        "y",
        "o",
        "u",
        "que",
        "es",
        "son",
        "se",
        "por",
        "para",
        "con",
        "sin",
        "no",
        "sí",
        "lo",
        "al",
        "su",
        "sus",
        "este",
        "esta",
        "esto",
        "estos",
        "estas",
        "más",
        "pero",
        "como",
        "ya",
        "muy",
        "fue",
        "ser",
        "hay",
        "the",
        "and",
        "for",
        "with",
    }
)

_TOKEN_RE = re.compile(r"[a-záéíóúñü0-9_]+")
_WHITESPACE_RE = re.compile(r"\s+")
_TRAILING_PUNCT_RE = re.compile(r"[\s.!?¡¿]+$")


class IDEMemoriaError(ValueError):
    """Solicitud de memoria inválida: workspace inexistente, tipo de recuerdo
    fuera de `MEMORY_KINDS`, contenido con forma de ruido, o recuerdo no
    encontrado."""


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _clean_content(
    raw: Any,
    *,
    campo: str = "El contenido del recuerdo",
    min_chars: int = MIN_CONTENT_CHARS,
    max_chars: int = MAX_CONTENT_CHARS,
) -> str:
    """Normaliza y valida un texto que va a quedar guardado.

    Los campos de ADR pasan por acá y no por una validación paralela: si el
    filtro de ruido viviera en dos lugares, el segundo terminaría aceptando
    "ok" como razón de una decisión. Solo se parametriza el mínimo/máximo
    (una alternativa se nombra, un hecho se enuncia -- ver los topes arriba)
    y la etiqueta del campo, para que el error diga cuál de los tres textos
    está mal en vez de un "el recuerdo" ambiguo.
    """

    if not isinstance(raw, str):
        raise IDEMemoriaError(f"{campo} debe ser texto.")
    text = _WHITESPACE_RE.sub(" ", raw).strip()
    if len(text) < min_chars:
        raise IDEMemoriaError(
            f"{campo} es demasiado corto para ser útil (mínimo {min_chars} "
            "caracteres) -- esto es ruido, no un hecho que valga la pena recordar."
        )
    if len(text) > max_chars:
        raise IDEMemoriaError(
            f"{campo} es demasiado largo (máximo {max_chars} caracteres) -- "
            "la memoria guarda hechos compactos, no párrafos enteros de contexto."
        )
    normalized_for_trivia = _TRAILING_PUNCT_RE.sub("", text.casefold()).strip()
    if normalized_for_trivia in _TRIVIAL_PHRASES:
        raise IDEMemoriaError(
            "Eso es una frase de trámite ('ok', 'gracias', 'listo', ...), no un hecho "
            "sobre el proyecto -- no se guarda."
        )
    return text


def _error_adr_solo_decision(kind: str) -> IDEMemoriaError:
    return IDEMemoriaError(
        "Los campos alternativas / por_que_no / se_invalida_si solo existen en un "
        f"recuerdo de tipo 'decision', no en uno de tipo {kind!r}: describen qué "
        "otras opciones se evaluaron, por qué se descartaron y qué tendría que "
        f"cambiar para reconsiderarlas. Un recuerdo de tipo {kind!r} no descarta "
        "nada -- enuncia algo que ya es así en este repo. Si esto sí fue una "
        "decisión tomada con la persona, guárdalo con kind='decision'."
    )


def _hay_campos_adr(
    alternativas: Any = None, por_que_no: Any = None, se_invalida_si: Any = None
) -> bool:
    """¿Quien llama pidió guardar algo de ADR?

    Un valor vacío (``None``, ``[]``, ``""`` o solo espacios) cuenta como "no
    lo especifiqué", nunca como "bórralo": ver ``remember`` sobre por qué
    omitir un campo jamás debe destruir el porqué que otra sesión ya
    escribió. Esa lectura tiene que ser la misma que hacen
    ``_limpiar_campo_adr`` y ``_limpiar_alternativas``, o un recuerdo que no
    es decisión fallaría por traer un campo que, una línea después, se
    descarta por vacío.

    Por eso una lista se mira por dentro y no solo por su ``bool``: quien
    llena siempre los tres parámetros -- una tool con esquema fijo, típicamente
    -- manda ``alternativas=["", ""]`` cuando no descartó nada, y esa lista no
    nombra ninguna opción.
    """

    def presente(valor: Any) -> bool:
        if isinstance(valor, str):
            return bool(valor.strip())
        if isinstance(valor, (list, tuple, set, frozenset)):
            return any(presente(item) for item in valor)
        return bool(valor)

    return any(presente(valor) for valor in (alternativas, por_que_no, se_invalida_si))


def _limpiar_alternativas(alternativas: Any) -> tuple[str, ...] | None:
    if isinstance(alternativas, str):
        # Un texto es iterable, así que sin esta guarda "Postgres" se
        # guardaría como ocho alternativas de una letra cada una.
        raise IDEMemoriaError(
            "alternativas es una lista de opciones, no un texto: pasa "
            "['Postgres', 'SQLite'], no 'Postgres, SQLite' -- cada opción descartada "
            "se guarda por separado para poder buscarla por separado."
        )
    try:
        crudas = list(alternativas)
    except TypeError as exc:
        raise IDEMemoriaError("alternativas debe ser una lista de textos.") from exc
    # Un hueco no es una opción descartada: se ignora igual que un `por_que_no`
    # en blanco (ver `_limpiar_campo_adr`), y por lo tanto tampoco cuenta para
    # el tope. Si en vez de eso reventara, guardar una convención mandando la
    # lista vacía que exige un esquema fijo fallaría por un campo que ni
    # siquiera se va a guardar.
    crudas = [cruda for cruda in crudas if not (isinstance(cruda, str) and not cruda.strip())]
    if len(crudas) > MAX_ALTERNATIVAS:
        raise IDEMemoriaError(
            f"Demasiadas alternativas (máximo {MAX_ALTERNATIVAS}) -- una decisión "
            "registra las opciones que de verdad se evaluaron, no una lluvia de ideas."
        )
    limpias: list[str] = []
    vistas: set[str] = set()
    for cruda in crudas:
        texto = _clean_content(
            cruda,
            campo="El nombre de una alternativa",
            min_chars=MIN_ALTERNATIVA_CHARS,
            max_chars=MAX_ALTERNATIVA_CHARS,
        )
        clave = _dedupe_key(texto)
        if clave in vistas:
            continue  # mismo descarte escrito dos veces: no es un descarte más
        vistas.add(clave)
        limpias.append(texto)
    return tuple(limpias) or None


def _fusionar_alternativas(guardadas: tuple[str, ...], nuevas: tuple[str, ...]) -> tuple[str, ...]:
    """Une las alternativas ya guardadas con las que llegan al reforzar.

    Suman, no pisan, porque cada sesión anota las opciones que ELLA evaluó y
    ninguna vuelve a escribir la lista completa: si la de junio reemplazara a
    la de marzo, el descarte de marzo dejaría de existir -- y como ``recall``
    encuentra la decisión POR sus alternativas, dejaría también de aparecerle
    a quien está por proponer justo esa opción otra vez.

    El tope sigue valiendo sobre la lista fusionada, y pasarlo se avisa en vez
    de recortar: recortar en silencio borraría exactamente el descarte que
    esta pieza existe para conservar.
    """

    fusionadas = list(guardadas)
    vistas = {_dedupe_key(texto) for texto in guardadas}
    for nueva in nuevas:
        clave = _dedupe_key(nueva)
        if clave in vistas:
            continue  # ya estaba anotada: repetirla no la convierte en otra
        vistas.add(clave)
        fusionadas.append(nueva)
    if len(fusionadas) > MAX_ALTERNATIVAS:
        raise IDEMemoriaError(
            f"Esta decisión ya tiene {len(guardadas)} alternativas guardadas y sumarle las "
            f"nuevas pasaría el máximo de {MAX_ALTERNATIVAS}: al reforzar, las alternativas "
            "se suman a las que ya estaban, no las reemplazan. Si la lista vieja quedó "
            "obsoleta, olvida el recuerdo (forget) y vuelve a guardarlo con la lista "
            "definitiva."
        )
    return tuple(fusionadas)


def _texto_o_none(valor: Any) -> str | None:
    """Texto ya guardado en disco, o ``None`` si venía vacío o corrupto. NO
    revalida largo ni trivialidad: una fila que ya está escrita se lee tal
    cual, igual que ``content`` (revalidar al leer haría desaparecer datos
    del usuario en silencio)."""

    if not isinstance(valor, str):
        return None
    return valor.strip() or None


def _limpiar_campo_adr(valor: Any, *, campo: str) -> str | None:
    if valor is None:
        return None
    if isinstance(valor, str) and not valor.strip():
        return None
    return _clean_content(valor, campo=campo)


def _dedupe_key(content: str) -> str:
    """Clave de deduplicación: mismo hecho escrito con distinto espaciado o
    mayúsculas cuenta como el mismo recuerdo (ver `remember`, refuerzo en vez
    de fila nueva). Es una coincidencia exacta tras normalizar -- una
    paráfrasis distinta del mismo hecho NO se detecta aquí a propósito; es
    una limitación documentada, no una promesa de deduplicación semántica."""

    return _TRAILING_PUNCT_RE.sub("", content.casefold()).strip()


def _tiene_adr(hit: dict[str, Any]) -> bool:
    return bool(hit.get("alternativas") or hit.get("por_que_no") or hit.get("se_invalida_si"))


def _formatear_recuerdo(hit: dict[str, Any]) -> str:
    """Un recuerdo tal como se ve dentro del bloque de prompt.

    Trabaja sobre la fila serializada (``to_json``), no sobre el
    ``MemoryNote``, porque las claves de ADR pueden simplemente no estar
    -- en un recuerdo que no es decisión y en toda fila guardada antes de
    que existieran. Un ``.get`` vacío no imprime nada, así que el caso
    normal queda exactamente en la línea de siempre.
    """

    lineas = [f"- ({hit['kind']}) {hit['content']}"]
    alternativas = hit.get("alternativas") or ()
    if alternativas:
        lineas.append("    · alternativas descartadas: " + "; ".join(alternativas))
    if hit.get("por_que_no"):
        lineas.append(f"    · por qué no: {hit['por_que_no']}")
    if hit.get("se_invalida_si"):
        lineas.append(f"    · se invalida si: {hit['se_invalida_si']}")
    return "\n".join(lineas)


def _tokenize(text: str) -> set[str]:
    return {
        token
        for token in _TOKEN_RE.findall(text.casefold())
        if len(token) >= 3 and token not in _STOPWORDS
    }


def _relevance_score(query_tokens: set[str], note_tokens: set[str], importance: float) -> float:
    """Relevancia de un recuerdo para el turno actual.

    Deliberadamente simple (superposición léxica, no embeddings -- ver
    docstring del módulo sobre por qué este paquete no habla con pgvector):
    ``coverage`` es la fracción de palabras de la PREGUNTA ACTUAL que este
    recuerdo toca; un recuerdo sin ninguna palabra en común da ``0.0`` y
    ``recall()`` lo descarta -- nunca se devuelve un recuerdo que no se
    conecte con nada de lo que se está preguntando ahora. Entre dos
    recuerdos con la misma cobertura, gana el más importante: por eso el
    factor de importancia solo escala el resultado (0.6-1.0), nunca decide
    por sí solo si algo es relevante.
    """

    if not query_tokens or not note_tokens:
        return 0.0
    overlap = len(query_tokens & note_tokens)
    if overlap == 0:
        return 0.0
    coverage = overlap / len(query_tokens)
    return coverage * (0.6 + 0.4 * max(0.0, min(1.0, importance)))


@dataclass
class MemoryNote:
    """Un recuerdo guardado sobre un workspace."""

    id: str
    workspace_id: str
    kind: MemoryKind
    content: str
    importance: float
    created_at: str
    last_used_at: str
    use_count: int
    # -- Campos de ADR: solo se llenan cuando kind == "decision" ----------- #
    # Van con valor por defecto para que un `MemoryNote` de cualquier otro
    # tipo se construya exactamente igual que antes de que existieran, acá y
    # en `from_json` (ver la sección de ADR del docstring del módulo).
    alternativas: tuple[str, ...] = ()
    por_que_no: str | None = None
    se_invalida_si: str | None = None

    def to_json(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "kind": self.kind,
            "content": self.content,
            "importance": self.importance,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "use_count": self.use_count,
        }
        # Las claves vacías no se serializan: así una fila sin ADR se escribe
        # en disco byte por byte como antes de esta extensión, y un recuerdo
        # guardado por una versión anterior no se distingue de uno nuevo. El
        # que lee ya tiene que tolerar la ausencia de estos campos (las filas
        # viejas no los traen), así que omitirlos no le agrega ningún caso.
        if self.alternativas:
            row["alternativas"] = list(self.alternativas)
        if self.por_que_no:
            row["por_que_no"] = self.por_que_no
        if self.se_invalida_si:
            row["se_invalida_si"] = self.se_invalida_si
        return row

    @staticmethod
    def from_json(raw: dict[str, Any]) -> MemoryNote:
        kind = raw.get("kind")
        if kind not in MEMORY_KINDS:
            kind = "convencion"  # fila corrupta/de otra versión: no se descarta, se degrada
        # Los campos de ADR se leen SOLO si la fila es una decisión. Un
        # archivo editado a mano (o degradado por la línea de arriba) podría
        # traerlos en otro tipo; en ese caso se ignoran en vez de reventar la
        # carga, igual que el resto de la tolerancia de este `from_json`. Así
        # la regla "ADR solo en decision" también vale para lo que ya está en
        # disco, no solo para lo que entra por `remember`.
        alternativas: tuple[str, ...] = ()
        por_que_no: str | None = None
        se_invalida_si: str | None = None
        if kind == "decision":
            crudas = raw.get("alternativas")
            if isinstance(crudas, list):
                alternativas = tuple(str(item).strip() for item in crudas if str(item).strip())
            por_que_no = _texto_o_none(raw.get("por_que_no"))
            se_invalida_si = _texto_o_none(raw.get("se_invalida_si"))
        return MemoryNote(
            id=str(raw["id"]),
            workspace_id=str(raw["workspace_id"]),
            kind=kind,
            content=str(raw.get("content") or ""),
            importance=float(raw.get("importance", 0.5)),
            created_at=str(raw.get("created_at") or _now_iso()),
            last_used_at=str(raw.get("last_used_at") or raw.get("created_at") or _now_iso()),
            use_count=int(raw.get("use_count") or 0),
            alternativas=alternativas,
            por_que_no=por_que_no,
            se_invalida_si=se_invalida_si,
        )

    def texto_indexable(self) -> str:
        """Todo el texto por el que este recuerdo se puede encontrar.

        Para un recuerdo normal es su contenido y nada más. Para una decisión
        incluye además su ADR, y esa diferencia es el punto: quien está por
        revertir una decisión escribe la alternativa que quiere retomar, no
        la conclusión que ya se tomó (ver la sección de ADR del docstring del
        módulo). Un recuerdo que solo se encuentra por su conclusión llega
        después de que el daño ya se hizo.
        """

        partes = [self.content, *self.alternativas]
        if self.por_que_no:
            partes.append(self.por_que_no)
        if self.se_invalida_si:
            partes.append(self.se_invalida_si)
        return " ".join(partes)


class MemoriaStore:
    """Registro JSON local, atómico y privado de memoria del proyecto por
    workspace. Mismo patrón de persistencia que ``ProjectRegistry``
    (``ide_projects.py``): un archivo, un lock, escritura atómica con
    ``os.replace`` y permisos ``0o600``."""

    def __init__(
        self,
        state_dir: Path,
        workspaces: WorkspaceStore,
        *,
        max_notes_per_workspace: int = MAX_NOTES_PER_WORKSPACE,
    ) -> None:
        self.workspaces = workspaces
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / "ide-memoria.json"
        self.max_notes_per_workspace = max_notes_per_workspace
        self._lock = threading.RLock()
        self._notes: dict[str, MemoryNote] = {}
        self._load()

    # -- persistencia ------------------------------------------------------

    def _load(self) -> None:
        with self._lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
            rows = data.get("notes", []) if isinstance(data, dict) else []
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                    continue
                try:
                    note = MemoryNote.from_json(row)
                except (KeyError, ValueError, TypeError):
                    continue
                self._notes[note.id] = note

    def _save(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        payload = {"version": 1, "notes": [note.to_json() for note in self._notes.values()]}
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            temp_path.chmod(0o600)
        except OSError:
            pass
        os.replace(temp_path, self.path)

    def _validate_workspace(self, workspace_id: Any) -> str:
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise IDEMemoriaError("workspace_id debe ser texto no vacío.")
        try:
            self.workspaces.get(workspace_id)
        except IDEWorkspaceError as exc:
            raise IDEMemoriaError(str(exc)) from exc
        return workspace_id

    def _notes_for(self, workspace_id: str) -> list[MemoryNote]:
        """Recuerdos de este workspace en particular -- nunca los de otro
        (ver docstring del módulo: aislamiento entre proyectos, sin ningún
        alcance "global" que los mezcle)."""

        return [note for note in self._notes.values() if note.workspace_id == workspace_id]

    # -- escritura -----------------------------------------------------------

    def remember(
        self,
        workspace_id: str,
        content: str,
        kind: MemoryKind,
        *,
        importance: float = 0.5,
        alternativas: Sequence[str] | None = None,
        por_que_no: str | None = None,
        se_invalida_si: str | None = None,
    ) -> dict[str, Any]:
        """Guarda (o refuerza) un recuerdo sobre ``workspace_id``.

        Aplica, en orden, los tres filtros contra ruido documentados en el
        módulo: tamaño (``_clean_content``), frase trivial
        (``_clean_content``) y deduplicación (esta función). Si el mismo
        hecho ya estaba guardado (ver ``_dedupe_key``), NO crea una fila
        nueva -- refuerza la existente: sube ``use_count``, actualiza
        ``last_used_at`` y sube ``importance`` hasta el máximo entre la
        vieja y la nueva. Repetir el mismo hecho en varias sesiones es la
        señal más fuerte de que sí vale la pena recordarlo, y este diseño
        lo premia sin duplicar espacio.

        La deduplicación y el tope duro se aplican solo contra los
        recuerdos de este mismo ``workspace_id`` -- nunca contra los de
        otro proyecto (ver docstring del módulo sobre por qué este módulo
        no tiene alcance global).

        ``alternativas``, ``por_que_no`` y ``se_invalida_si`` son el porqué
        de una decisión (ver la sección de ADR del docstring del módulo) y
        SOLO se aceptan con ``kind="decision"``; con cualquier otro tipo
        esto falla con ``IDEMemoriaError`` en vez de guardarlos en un
        recuerdo donde no significan nada. Los tres son opcionales: una
        decisión sin su porqué se sigue guardando igual que siempre, porque
        obligar a documentarla completa o nada terminaría en que no se
        guarda ninguna.

        Al reforzar, un campo de ADR que se omite deja intacto el que ya
        estaba: la segunda vez que se guarda una decisión suele ser justo
        cuando alguien por fin anota el porqué que la primera vez no se
        anotó, y omitir un campo nunca debe borrar lo que otra sesión sí se
        tomó el trabajo de escribir.

        Los campos que sí llegan se aplican distinto según lo que son, y la
        diferencia no es un descuido. ``por_que_no`` y ``se_invalida_si`` son
        UNA razón redactada: la versión nueva es una mejor redacción de la
        misma cosa, así que reemplaza a la anterior. ``alternativas`` es una
        lista de hechos acumulados -- cada sesión anota las opciones que ELLA
        evaluó, ninguna reescribe la lista completa --, así que se SUMA a la
        que ya estaba (ver ``_fusionar_alternativas``). Pisarla haría
        desaparecer el descarte más viejo, que es precisamente el que ya nadie
        recuerda y el que alguien está por revivir.
        """

        workspace_id = self._validate_workspace(workspace_id)
        if kind not in MEMORY_KINDS:
            raise IDEMemoriaError(
                f"kind debe ser uno de {MEMORY_KINDS}, no {kind!r} -- ver criterio del "
                "módulo sobre qué cuenta como memoria."
            )
        pide_adr = _hay_campos_adr(alternativas, por_que_no, se_invalida_si)
        if pide_adr and kind != "decision":
            raise _error_adr_solo_decision(kind)
        adr_alternativas = _limpiar_alternativas(alternativas) if alternativas else None
        adr_por_que_no = _limpiar_campo_adr(por_que_no, campo="El 'por qué no' de la decisión")
        adr_se_invalida_si = _limpiar_campo_adr(
            se_invalida_si, campo="El 'se invalida si' de la decisión"
        )
        clean = _clean_content(content)
        clamped_importance = max(0.0, min(1.0, float(importance)))

        with self._lock:
            pool = self._notes_for(workspace_id)
            key = _dedupe_key(clean)
            for existing in pool:
                if _dedupe_key(existing.content) == key:
                    # El hecho ya está guardado, pero con otro tipo: pegarle el
                    # ADR igual dejaría en disco una 'convencion' con
                    # alternativas descartadas, que es la incoherencia que la
                    # validación de arriba existe para impedir. Se avisa en vez
                    # de cambiarle el tipo por debajo a un recuerdo que alguien
                    # ya clasificó.
                    if pide_adr and existing.kind != "decision":
                        raise IDEMemoriaError(
                            "Ese mismo hecho ya está guardado en este proyecto como "
                            f"recuerdo de tipo {existing.kind!r}, y el porqué (alternativas, "
                            "por_que_no, se_invalida_si) solo existe en los de tipo "
                            "'decision'. Si de verdad es una decisión, olvida el recuerdo "
                            "viejo (forget) y vuelve a guardarlo como 'decision'."
                        )
                    # La fusión se calcula ANTES de tocar la fila: si pasa el
                    # tope, el intento falla entero en vez de dejar el recuerdo
                    # reforzado a medias, igual que el aviso de acá arriba.
                    fusionadas = (
                        _fusionar_alternativas(existing.alternativas, adr_alternativas)
                        if adr_alternativas is not None
                        else None
                    )
                    existing.use_count += 1
                    existing.last_used_at = _now_iso()
                    existing.importance = max(existing.importance, clamped_importance)
                    if fusionadas is not None:
                        existing.alternativas = fusionadas
                    if adr_por_que_no is not None:
                        existing.por_que_no = adr_por_que_no
                    if adr_se_invalida_si is not None:
                        existing.se_invalida_si = adr_se_invalida_si
                    self._save()
                    return existing.to_json()

            self._evict_weakest_if_needed(pool, self.max_notes_per_workspace)
            now = _now_iso()
            note = MemoryNote(
                id=str(uuid.uuid4()),
                workspace_id=workspace_id,
                kind=kind,
                content=clean,
                importance=clamped_importance,
                created_at=now,
                last_used_at=now,
                use_count=1,
                alternativas=adr_alternativas or (),
                por_que_no=adr_por_que_no,
                se_invalida_si=adr_se_invalida_si,
            )
            self._notes[note.id] = note
            self._save()
            return note.to_json()

    def _evict_weakest_if_needed(self, pool: list[MemoryNote], cap: int) -> None:
        if len(pool) < cap:
            return
        # Se descarta primero el de menor `importance`; en empate, el que
        # hace más tiempo que no se recupera (`recall` refresca
        # `last_used_at` -- ver su docstring). Así el tope duro de este
        # workspace purga lo menos útil, no lo más nuevo ni lo más viejo per
        # se.
        weakest = min(pool, key=lambda note: (note.importance, note.last_used_at))
        del self._notes[weakest.id]

    # -- lectura ------------------------------------------------------------

    def recall(
        self, workspace_id: str, query: str, *, k: int = DEFAULT_RECALL_K
    ) -> list[dict[str, Any]]:
        """Los ``k`` recuerdos más relevantes para ``query`` de ESTE
        workspace -- nunca de otro (ver docstring del módulo: aislamiento
        entre proyectos).

        NO es un volcado de toda la memoria del proyecto -- ver el docstring
        del módulo sobre por qué eso sería ruido. Puntúa cada recuerdo con
        ``_relevance_score`` (superposición léxica con ``query``, escalada
        por importancia) y descarta los que dan ``0.0``: si nada de lo
        recordado toca ninguna palabra de lo que se pregunta ahora, no se
        devuelve nada, no se rellena con "lo más importante en general".
        Limitación documentada: coincidencia léxica, no semántica -- una
        pregunta parafraseada distinta puede no encontrar un recuerdo real
        (ver 2.2 del plan de paridad para el buscador semántico aparte).

        Una decisión se puntúa también por su ADR (``texto_indexable``), no
        solo por su conclusión: la pregunta que la pone en peligro nombra la
        alternativa descartada, no la conclusión. El efecto secundario es
        que una decisión con su porqué escrito matchea más preguntas que una
        sin él, y está bien que así sea -- es la que más caro sale revertir
        a ciegas.

        Cada recuerdo devuelto se considera "usado": se le sube
        ``use_count`` y se refresca ``last_used_at``, la misma señal que
        protege a un recuerdo de ``_evict_weakest_if_needed`` cuando este
        workspace llega al tope.
        """

        workspace_id = self._validate_workspace(workspace_id)
        if not isinstance(query, str):
            raise IDEMemoriaError("query debe ser texto.")
        if k <= 0:
            raise IDEMemoriaError("k debe ser mayor que cero.")

        query_tokens = _tokenize(query)
        with self._lock:
            candidatos = self._notes_for(workspace_id)
            scored = [
                (
                    note,
                    _relevance_score(
                        query_tokens, _tokenize(note.texto_indexable()), note.importance
                    ),
                )
                for note in candidatos
            ]
            relevant = [(note, score) for note, score in scored if score > 0.0]
            relevant.sort(key=lambda pair: (pair[1], pair[0].importance), reverse=True)
            selected = relevant[:k]
            now = _now_iso()
            results: list[dict[str, Any]] = []
            for note, score in selected:
                note.use_count += 1
                note.last_used_at = now
                row = note.to_json()
                row["score"] = round(score, 4)
                results.append(row)
            if selected:
                self._save()
            return results

    def recall_as_prompt_block(
        self, workspace_id: str, query: str, *, k: int = DEFAULT_RECALL_K
    ) -> str | None:
        """Azúcar sobre ``recall`` para quien arma el prompt de sistema del
        turno (integración prevista en ``ide_sessions.py``, ver docstring del
        módulo). Devuelve ``None`` cuando no hay nada relevante -- igual que
        ``ProjectRules.as_prompt_block()`` en ``ide_reglas.py``, para que
        quien integre solo tenga que hacer
        ``if bloque: prompt += bloque`` sin lógica extra.

        Una decisión se muestra con su porqué completo (alternativas ya
        descartadas, por qué, y qué la invalidaría): mostrar solo la
        conclusión sería mostrarle al agente exactamente lo que no le
        alcanza para no revertirla. Un recuerdo sin ADR se ve igual que
        antes de que estos campos existieran -- una sola línea, sin viñetas
        vacías."""

        hits = self.recall(workspace_id, query, k=k)
        if not hits:
            return None
        encabezado = (
            "Memoria de sesiones anteriores sobre este proyecto (lo que ya se "
            "descubrió y vale la pena no repetir):"
        )
        if any(_tiene_adr(hit) for hit in hits):
            # Solo cuando hay una decisión con su porqué: si no, esta línea
            # sería una instrucción sobre algo que el bloque no contiene.
            encabezado += (
                "\nLas decisiones vienen con las alternativas que ya se descartaron; no las "
                "revivas sin decirlo, salvo que se cumpla su 'se invalida si'."
            )
        partes = [encabezado]
        restante = MAX_PROMPT_BLOCK_CHARS - len(encabezado)
        for hit in hits:
            entrada = _formatear_recuerdo(hit)
            # Se corta por recuerdo entero, no a mitad de una frase. Una ADR
            # partida al medio ("...se descartó porque") es peor que
            # ausente: deja media razón con toda la apariencia de estar
            # completa, y sobre esa media razón el modelo decide igual.
            if len(partes) > 1 and len(entrada) + 1 > restante:
                break
            partes.append(entrada)
            restante -= len(entrada) + 1
        # El recorte final solo puede llegar a actuar si el primer recuerdo
        # ya no entra solo: nunca se devuelve un bloque vacío por presupuesto.
        return "\n".join(partes)[:MAX_PROMPT_BLOCK_CHARS]

    def list_notes(self, workspace_id: str) -> list[dict[str, Any]]:
        """Memoria guardada en este workspace, sin filtrar por relevancia --
        para una vista de "qué recuerda Edecán de este proyecto" en la UI
        (transparencia: el usuario debe poder ver, y eventualmente borrar,
        lo que el agente guardó de su repo)."""

        workspace_id = self._validate_workspace(workspace_id)
        with self._lock:
            rows = [note.to_json() for note in self._notes_for(workspace_id)]
            return sorted(rows, key=lambda row: row["last_used_at"], reverse=True)

    def forget(self, workspace_id: str, note_id: str) -> dict[str, Any]:
        """Borra un recuerdo puntual, a pedido explícito (p. ej. el usuario
        lo marca como incorrecto o ya obsoleto desde la vista de
        transparencia de ``list_notes``)."""

        workspace_id = self._validate_workspace(workspace_id)
        with self._lock:
            note = self._notes.get(note_id)
            if note is None or note.workspace_id != workspace_id:
                raise IDEMemoriaError("Recuerdo no encontrado en este workspace.")
            del self._notes[note_id]
            self._save()
            return {"deleted_note_id": note_id}
