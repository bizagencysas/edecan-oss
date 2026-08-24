"""Guardrails de seguridad y calidad para la salida del agente (PHASE2.md §186-200).

Este módulo reúne cuatro controles deterministas, sin I/O y sin llamadas al LLM:

1. **Redacción de secretos** (§194-195): detectar credenciales que el usuario
   pudo pegar por accidente (API keys, contraseñas, tokens, claves AWS, JWT,
   claves privadas) para advertirle y para no volver a imprimir el valor
   completo. Reutiliza los MISMOS patrones que ya usa el repo en
   `edecan_core.safety` (claves `sk-`, `Bearer`, Stripe, AWS) y el
   `_SECRET_PATTERN` de `apps/api/edecan_api/routers/memory.py`
   (`api_key`, `password`, `contraseña`, `secret`, `token`, `credencial`,
   `bearer`) — no inventa patrones nuevos para lo que ya está testeado; solo
   añade JWT y claves privadas, que el repo aún no cubría.

2. **Router de factualidad** (§190): separar pedidos `"factual"` (datos,
   fechas, cifras, verificación) de `"creative"` (invención, ficción, lluvia de
   ideas) para decidir si una respuesta necesita verificación antes de
   presentarse. Heurística determinista, sin LLM.

3. **Validador post-generación** (§189): para categorías críticas, comprobar
   que la salida no sea vacía ni traiga placeholders o marcadores de texto
   fabricado antes de presentarla al usuario.

4. **Reconocimiento de corrección** (§187): plantilla breve de "me equivoqué"
   cuando el usuario corrige un dato, sin ensayo defensivo.

Las funciones son PURAS: mismo texto de entrada → misma salida, sin estado
global, sin red, sin sistema de archivos. El cableado a `agent.py` se hace por
separado (este módulo no lo toca).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

_MASK_COMPLETO = "[REDACTADO]"

# ---------------------------------------------------------------------------
# 1. Detección y redacción de secretos (PHASE2.md §194-195)
# ---------------------------------------------------------------------------

# Tipos en los que el valor se enmascara PARCIALMENTE (`sk-...9F2A`) en vez de
# sustituirse entero: son credenciales con prefijo reconocible donde conservar
# los últimos caracteres ayuda a identificarla sin filtrarla (mismo criterio
# que `SECURITY.md` §2.5 "muestra solo identificadores mínimos").
_TIPOS_MASCARA_PARCIAL = frozenset(
    {"sk_key", "bearer_token", "stripe_key", "aws_access_key", "jwt"}
)

# Cada tupla es `(type, pattern)`. Los primeros seis patrones se copian tal cual
# de `edecan_core.safety._PATTERNS` (menos el nombre de tipo, que acá se separa
# por familia para poder etiquetar cada detección). `jwt` y `private_key` son
# los únicos patrones que el repo no tenía: se añaden aquí porque §194 menciona
# explícitamente "private token" y claves privadas entre lo que hay que detectar.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Claves "sk-"/"sk_" (OpenAI "sk-…", Anthropic "sk-ant-…", Stripe secretas).
    ("sk_key", re.compile(r"\bsk[-_][A-Za-z0-9_-]{8,}")),
    # Encabezado/valor "Bearer <token>" (Authorization header típico).
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)),
    # Claves restringidas/de webhook de Stripe (rk_live_…, rk_test_…, whsec_…).
    ("stripe_key", re.compile(r"\b(?:rk_live|rk_test|whsec)_[A-Za-z0-9]{8,}")),
    # Access key id de AWS (AKIA…/ASIA…).
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    # JWT: tres segmentos base64url separados por puntos, con cabecera "eyJ…".
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}(?![A-Za-z0-9_-])"),
    ),
    # Marcador de bloque de clave privada (RSA/EC/OpenSSH/encrypted/…).
    ("private_key", re.compile(r"-----BEGIN(?: [A-Z0-9]+)* ?PRIVATE KEY-----", re.IGNORECASE)),
    # Palabras-clave de credencial tomadas del `_SECRET_PATTERN` de
    # `apps/api/edecan_api/routers/memory.py`, separadas por tipo y con
    # fronteras de palabra (`\b`) que el patrón original no tenía (así "secret"
    # no se dispara dentro de "secretaría" ni "token" dentro de "tokenizer").
    # La parte `(?:\s*[:=]\s*\S+)?` captura el valor en la forma "clave=valor"
    # o "clave: valor" para poder redactarlo junto con su etiqueta.
    ("api_key", re.compile(r"\bapi[_ -]?key\b(?:\s*[:=]\s*\S+)?", re.IGNORECASE)),
    (
        "password",
        re.compile(r"\b(?:contrase(?:ña|na)|password)\b(?:\s*[:=]\s*\S+)?", re.IGNORECASE),
    ),
    ("secret", re.compile(r"\bsecret\b(?:\s*[:=]\s*\S+)?", re.IGNORECASE)),
    ("token", re.compile(r"\btoken\b(?:\s*[:=]\s*\S+)?", re.IGNORECASE)),
    ("credential", re.compile(r"\bcredencial\b(?:\s*[:=]\s*\S+)?", re.IGNORECASE)),
    ("bearer", re.compile(r"\bbearer\b", re.IGNORECASE)),
)


def _mascarar(valor: str, tipo: str) -> str:
    """Enmascara un secreto ya detectado según su tipo.

    Los tokens con prefijo reconocible se acortan a `prefijo...sufijo` para
    poder identificar la credencial sin filtrarla; el resto (etiquetas como
    `password`, cabeceras de clave privada) se sustituyen por completo porque
    no hay un "valor" con sentido que conservar.
    """
    if tipo in _TIPOS_MASCARA_PARCIAL and len(valor) > 8:
        return f"{valor[:4]}...{valor[-4:]}"
    return _MASK_COMPLETO


def detect_potential_secret(text: str) -> list[dict[str, Any]]:
    """Devuelve los tramos de `text` que parecen un secreto.

    Cada elemento es `{"type", "start", "end"}` donde `start`/`end` son índices
    de Python (semiabiertos, listos para `text[start:end]`). Los tramos se
    devuelven ordenados por posición y sin solapamientos: si un token concreto
    (p. ej. `Bearer abc…`) convive con una etiqueta genérica (p. ej. `bearer`),
    se conserva el tramo MÁS largo, que es el que cubre el valor real.
    """
    coincidencias: list[dict[str, Any]] = []
    for tipo, patron in _SECRET_PATTERNS:
        for m in patron.finditer(text):
            coincidencias.append({"type": tipo, "start": m.start(), "end": m.end()})

    # Orden estable: por comienzo ascendente y, a igual comienzo, el más largo
    # primero. Así el primer tramo de un grupo solapado es el más informativo.
    coincidencias.sort(key=lambda d: (d["start"], -d["end"]))

    resultado: list[dict[str, Any]] = []
    ultimo_fin = -1
    for tramo in coincidencias:
        if tramo["start"] < ultimo_fin:
            continue  # solapado con un tramo ya conservado (más largo)
        resultado.append(tramo)
        ultimo_fin = tramo["end"]
    return resultado


def redact_secrets(text: str) -> str:
    """Devuelve `text` con cualquier secreto detectado enmascarado.

    Usa los MISMOS tramos que `detect_potential_secret` (consistencia
    detectar=redactar): no puede pasar que `contains_secret` avise de algo que
    `redact_secrets` luego deja visible. Los tokens se acortan
    (`sk-...9F2A`); las etiquetas se sustituyen por `[REDACTADO]`.
    """
    tramos = detect_potential_secret(text)
    if not tramos:
        return text
    partes: list[str] = []
    cursor = 0
    for tramo in tramos:
        partes.append(text[cursor : tramo["start"]])
        partes.append(_mascarar(text[tramo["start"] : tramo["end"]], tramo["type"]))
        cursor = tramo["end"]
    partes.append(text[cursor:])
    return "".join(partes)


def contains_secret(text: str) -> bool:
    """`True` si `text` contiene algo que parece un secreto (PHASE2.md §194).

    Pensado como guarda barata antes de persistir/loguear/enviar: si devuelve
    `True`, el llamador puede advertir al usuario o redactar antes de continuar.
    """
    return bool(detect_potential_secret(text))


# ---------------------------------------------------------------------------
# 2. Router de factualidad (PHASE2.md §190)
# ---------------------------------------------------------------------------


def _sin_acentos(texto: str) -> str:
    """Normaliza a minúsculas y quita tildes/ñ-acentos para que el router sea
    tolerante a "cuándo" vs "cuando" o "métricas" vs "metricas" sin depender de
    que el usuario acentúe bien. Solo stdlib (`unicodedata`), sin deps externas."""
    descompuesto = unicodedata.normalize("NFD", texto.casefold())
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


# Marcadores (sin acentos) de una solicitud que pide información verificable:
# verbos de verificación, referencia a fuentes, interrogativos de hecho,
# vigencia/temporalidad y datos concretos. Coincidencia por subcadena sobre el
# texto ya normalizado.
_FACTUAL_MARKERS: tuple[str, ...] = (
    "segun",
    "confirmado",
    "confirmar",
    "verifica",
    "verificar",
    "fuente",
    "cita",
    "oficial",
    "actual",
    "actualizado",
    "noticia",
    "precio",
    "cuanto",
    "cuantos",
    "cuando",
    "quien",
    "quienes",
    "donde",
    "que es",
    "cual es",
    "fecha",
    "dato",
    "hecho",
    "real",
    "exacto",
    "correcto",
    "cierto",
    "verdad",
    "falso",
    "estadistica",
    "informe",
    "reporte",
    "documento",
    "investigacion",
    "reciente",
    "ultima",
    "costo",
    "calcula",
    "definicion",
    "significa",
    "como funciona",
    "evidencia",
    "comprobado",
    "estudio",
    "historico",
)

# Marcadores de una solicitud de invención/ficción/lluvia de ideas: no hay un
# hecho que verificar, hay una creación que producir.
_CREATIVE_MARKERS: tuple[str, ...] = (
    "imagina",
    "inventa",
    "crea",
    "cuento",
    "poema",
    "poesia",
    "historia",
    "ficcion",
    "fanfiction",
    "fanfic",
    "novela",
    "guion",
    "personaje",
    "dialogo",
    "escena",
    "chiste",
    "broma",
    "metafora",
    "analogia",
    "leyenda",
    "fabula",
    "relato",
    "brainstorm",
    "lluvia de ideas",
    "letra de cancion",
    "cancion",
    "storytelling",
    "roleplay",
    "juego de rol",
)

# Dato "concreto" = año o fecha (no números sueltos: "3 poemas" no es factual,
# pero "la guerra de 1939" o "México en 1810" sí lo son). Con eso un verbo
# creativo más una cifra de cantidad ("escríbeme 3 poemas") sigue siendo
# creativo, mientras que una cifra temporal inclina hacia factual. El rango de
# años abarca 1000-2099 a propósito: las preguntas históricas ("1492", "1810")
# son tan factuales como las contemporáneas.
_DATO_CONCRETO_RE = re.compile(
    r"\b(?:1\d{3}|20\d{2})\b"                     # año 1000-2099
    r"|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"         # fecha dd/mm/aaaa o mm/dd/aaaa
    r"|\b\d{4}[-/]\d{1,2}(?:[-/]\d{1,2})?\b"      # ISO aaaa-mm-dd
)


def classify_factuality(text: str) -> str:
    """Clasifica una solicitud como `"factual"` o `"creative"`.

    Heurística determinista (sin LLM), pensada para decidir si la respuesta
    necesita verificación antes de presentarse (§189-190):

    - `"creative"` solo cuando hay marcadores de invención/ficción Y no hay
      marcadores factuales ni dato concreto (año/fecha) que incline a factual.
    - Cualquier otro caso cae a `"factual"`: es el default seguro — ante la
      duda, verificar es más barato que publicar algo no comprobado.
    """
    normalizado = _sin_acentos(text)
    tiene_creative = any(marcador in normalizado for marcador in _CREATIVE_MARKERS)
    tiene_factual = any(marcador in normalizado for marcador in _FACTUAL_MARKERS)
    tiene_dato = _DATO_CONCRETO_RE.search(text) is not None

    if tiene_creative and not tiene_factual and not tiene_dato:
        return "creative"
    return "factual"


# ---------------------------------------------------------------------------
# 3. Validador post-generación (PHASE2.md §189)
# ---------------------------------------------------------------------------

# Categorías cuya salida se presenta como información en la que el usuario va a
# apoyarse para decidir. Para ellas, un placeholder o un texto fabricado no es
# un defecto cosmético: es información falsa con cara de información.
_CATEGORIAS_CRITICAS = frozenset(
    {
        "factual",
        "noticias",
        "news",
        "medical",
        "health",
        "salud",
        "medico",
        "medicina",
        "legal",
        "finance",
        "financial",
        "finanzas",
        "dinero",
        "impuestos",
        "taxes",
        "inversion",
        "investment",
        "seguridad",
        "security",
    }
)

# Marcas de texto-placeholder o de relleno: señal inequívoca de que la salida
# no se generó de verdad (o se truncó antes de completarse).
_PLACEHOLDER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bTODO\b", re.IGNORECASE),
    re.compile(r"\bFIXME\b", re.IGNORECASE),
    re.compile(r"\bTBD\b", re.IGNORECASE),
    re.compile(r"lorem\s+ipsum", re.IGNORECASE),
    re.compile(r"\bplaceholder\b", re.IGNORECASE),
    re.compile(r"\bfake\b", re.IGNORECASE),
    re.compile(
        r"\[[^\]]*(?:insert|inserte|rellenar|poner|valor|placeholder|aqu[ií])[^\]]*\]",
        re.IGNORECASE,
    ),
    re.compile(r"\bX{3,}\b"),
)

# Marcas de texto "fabricado": la salida declara su propia incertidumbre o su
# carácter inventado. En una categoría crítica eso debe bloquear la entrega,
# no pasar como respuesta confiable.
_FABRICATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"no estoy seguro", re.IGNORECASE),
    re.compile(r"no tengo (?:informaci[oó]n|datos|acceso)", re.IGNORECASE),
    re.compile(r"fuentes no verificadas", re.IGNORECASE),
    re.compile(r"esto es ficci[oó]n", re.IGNORECASE),
    re.compile(r"totalmente inventado", re.IGNORECASE),
    re.compile(r"alucinaci[oó]n", re.IGNORECASE),
)


def validate_output(text: str, category: str) -> dict[str, Any]:
    """Valida una salida generada antes de presentarla.

    Devuelve `{"ok": bool, "issues": list[str]}`. La comprobación de vacío se
    hace SIEMPRE (una salida en blanco nunca es válida). Las comprobaciones de
    placeholder/fabricación se aplican solo a categorías críticas
    (`_CATEGORIAS_CRITICAS`): son las únicas donde un texto de relleno o
    fabricado hace daño real al usuario.
    """
    issues: list[str] = []
    if not text or not text.strip():
        issues.append("salida vacía")

    categoria = category.casefold().strip()
    if categoria in _CATEGORIAS_CRITICAS:
        if any(p.search(text) for p in _PLACEHOLDER_PATTERNS):
            issues.append("texto placeholder detectado")
        if any(p.search(text) for p in _FABRICATION_PATTERNS):
            issues.append("marcador de texto no verificado/fabricado")

    return {"ok": not issues, "issues": issues}


# ---------------------------------------------------------------------------
# 4. Reconocimiento de corrección (PHASE2.md §187)
# ---------------------------------------------------------------------------


def correction_acknowledgment(original: str, correction: str) -> str:
    """Plantilla breve de "me equivoqué" cuando el usuario corrige un dato.

    §187: "Tienes razón. Me equivoqué en X; lo correcto es Y." — sin ensayo
    defensivo. Si alguno de los dos lados llega vacío se usa un sustituto
    neutro para que el texto resultante siga siendo una frase completa.
    """
    equivocado = original.strip() or "lo que dije antes"
    correcto = correction.strip() or "lo que me indicas"
    return f"Tienes razón. Me equivoqué en {equivocado}; lo correcto es {correcto}."