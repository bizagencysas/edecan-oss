"""Validación y pruebas de una skill ANTES de instalarla (PHASE2.md §210): "no instalar
una skill rota".

Complementa a `edecan_skills.compiler` (que produce/lee el `SKILL.md` estructurado) con
dos cosas que ese módulo no hace a propósito:

- `validate_skill_structure` — comprobaciones estructurales Y de contenido sobre el texto
  crudo del `SKILL.md`, reusando las constantes privadas de `installer` (el regex de slug,
  el regex de caracteres de control y el split del frontmatter) para NO duplicar el mismo
  criterio en dos sitios. El instalador SÍ es permisivo (un frontmatter inválido degrada a
  cuerpo plano en vez de lanzar); este módulo es el gate estricto que decide "¿esta skill
  está en condiciones de instalarse?" — por eso acá un frontmatter sin `name`/`description`
  SÍ es un problema, aunque el instalador lo toleraría.

- `test_skill_triggers`/`smoke_test_skill` — pruebas deterministas y sin red: nada de
  llamar a un modelo ni a un runtime; regexes y substrings sobre el texto. Una prueba que
  dependiera de un LLM no podría fallar de forma reproducible, y "un test que no puede
  fallar no es un test".
"""

from __future__ import annotations

import re

from . import compiler
from .installer import (
    _CONTROL_CHARS_RE,
    _NOMBRE_SLUG_RE,
    _SLUG_COLAPSA_RE,
    _sanitizar,
    _split_frontmatter,
)

# Línea "demasiado larga" a efectos de validación: un SKILL.md es prosa legible, no un
# volcado binario — una línea de más de esto casi siempre es un blob/base64 que no pinta
# nada en instrucciones para un agente (mismo olfato que `security._RE_BASE64_LARGO`).
_MAX_LINEA = 200

# Palabras vacías (español + inglés) a ignorar al extraer palabras clave del trigger: no
# discriminan ("cuando", "diga", "the", "for"...). Que "cuando diga revisar repo" produzca
# claves `["revisar", "repo"]` y no `["cuando", "diga", ...]`.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "are",
        "was",
        "your",
        "you",
        "el",
        "la",
        "los",
        "las",
        "un",
        "una",
        "unos",
        "unas",
        "de",
        "del",
        "y",
        "o",
        "u",
        "que",
        "en",
        "con",
        "por",
        "para",
        "cuando",
        "diga",
        "di",
        "como",
        "al",
        "a",
        "se",
        "si",
        "es",
        "son",
        "ser",
    }
)


def _nombre_produce_slug(nombre: str) -> bool:
    """Réplica exacta del criterio de `installer._validar_nombre_produce_slug` (sin lanzar):
    un `nombre` es válido si YA es un slug (`_NOMBRE_SLUG_RE`) o si, colapsado con el mismo
    algoritmo que `store.slugify` (sin su fallback `"skill"`), produce algo no vacío."""
    if _NOMBRE_SLUG_RE.match(nombre):
        return True
    colapsado = _SLUG_COLAPSA_RE.sub("-", (nombre or "").strip().lower()).strip("-")
    return bool(colapsado)


def validate_skill_structure(skill_md: str) -> list[str]:
    """Lista de problemas estructurales de `skill_md` (`[]` = válido).

    Comprueba: frontmatter válido con `name` y `description` presentes (y `name` que
    produzca un slug no vacío, reusando el regex de `installer`); cuerpo no vacío; ausencia
    de caracteres de control; y ausencia de líneas excesivamente largas. Los mensajes van en
    español para que un humano (o el agente) los entienda directamente.
    """
    problemas: list[str] = []
    texto = skill_md or ""

    # Sobre el texto CRUDO (antes de sanitizar): los caracteres de control son un problema
    # en sí, no algo que haya que "limpiar" silenciosamente al validar.
    if _CONTROL_CHARS_RE.search(texto):
        problemas.append("contiene caracteres de control no permitidos")

    for i, linea in enumerate(texto.splitlines(), start=1):
        if len(linea) > _MAX_LINEA:
            problemas.append(f"línea {i} demasiado larga ({len(linea)} caracteres)")
            break  # basta el primer hallazgo para marcar la skill, no hace falta enumerar todas

    frontmatter, cuerpo = _split_frontmatter(_sanitizar(texto))
    if frontmatter is None:
        problemas.append("falta el frontmatter (se exige `name` y `description`)")
    else:
        nombre = str(frontmatter.get("name") or "").strip()
        descripcion = str(frontmatter.get("description") or "").strip()
        if not nombre:
            problemas.append("falta `name` en el frontmatter")
        elif not _nombre_produce_slug(nombre):
            problemas.append("el `name` no produce un slug válido")
        if not descripcion:
            problemas.append("falta `description` en el frontmatter")

    if not (cuerpo or "").strip():
        problemas.append("el cuerpo está vacío")

    return problemas


def _palabras_clave_trigger(trigger: str) -> list[str]:
    """Palabras clave del `trigger` (la sección de disparo): tokens alfanuméricos en
    minúsculas, de longitud >= 3, sin stopwords y sin duplicados, en orden de aparición."""
    tokens = re.findall(r"[a-z0-9áéíóúüñ]+", (trigger or "").lower())
    vistas: set[str] = set()
    claves: list[str] = []
    for token in tokens:
        if len(token) >= 3 and token not in _STOPWORDS and token not in vistas:
            vistas.add(token)
            claves.append(token)
    return claves


def test_skill_triggers(skill_md: str, sample_inputs: list[str]) -> list[bool]:
    """Prueba de disparo determinista: por cada entrada de `sample_inputs`, `True` si AL
    MENOS UNA palabra clave de la sección `trigger` aparece en ella (substring,
    case-insensitive).

    Se usa "alguna" en vez de "todas" a propósito: un disparo en lenguaje natural se
    satisface con que la entrada mencione el tema, no con que reproduzca cada palabra del
    trigger — exigir TODAS las claves haría que el test casi siempre diera `False` y, por
    tanto, que no discriminara nada real. Sin trigger (o sin palabras clave extraíbles),
    todas las entradas dan `False`.
    """
    trigger = compiler.parse_compiled_skill(skill_md).get("trigger", "")
    claves = _palabras_clave_trigger(str(trigger))
    if not claves:
        return [False] * len(sample_inputs)

    resultados: list[bool] = []
    for entrada in sample_inputs:
        normalizada = (entrada or "").lower()
        resultados.append(any(clave in normalizada for clave in claves))
    return resultados


def smoke_test_skill(skill_md: str) -> dict:
    """Smoke test de instalación: `validate_skill_structure` + verificación de que las seis
    secciones requeridas del compilador están presentes. Devuelve `{"ok": bool, "problems":
    [...]}` — `ok` es `True` solo si `problems` quedó vacío."""
    problemas = validate_skill_structure(skill_md)
    for nombre in compiler.secciones_faltantes(skill_md):
        problemas.append(f"falta la sección «{nombre}»")
    return {"ok": not problemas, "problems": problemas}


__all__ = ["smoke_test_skill", "test_skill_triggers", "validate_skill_structure"]
