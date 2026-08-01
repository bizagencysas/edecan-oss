"""Seguridad de skills de terceros: trust tiers, capacidades peligrosas y un escáner
heurístico de prompt-injection sobre el CUERPO de un `SKILL.md` (`ARCHITECTURE.md` §10.7,
§12.e; `DIRECCION_ACTUAL.md` "Usar OpenJarvis más agresivamente..."; dueño WP-V5-04).

Adaptación de `openjarvis.skills.security` (trust tiers + capacidades) y de
`openjarvis.security.scanner.SecretScanner`/`openjarvis.security.injection_scanner`
(forma del escáner: lista de `(patrón, nombre, hallazgo)`) — Apache-2.0, ver
`docs/skills.md`. OpenJarvis es single-user y sus escáneres reales corren sobre un
backend en Rust (`openjarvis._rust_bridge`, no portable acá); este módulo es Python puro,
heurístico, pensado para el modelo multi-tenant de Edecán: nunca decide nada por sí solo,
solo informa (`store.insert_skill`/el router deciden qué hacer con el resultado).

## Trust tiers

`TRUST_TIERS` son EXACTAMENTE los dos que Edecán puede clasificar hoy — ver
`clasificar_trust_tier` para por qué los tiers `bundled`/`workspace` de OpenJarvis no
aplican todavía.

## Capacidades

Una skill declara qué herramientas del agente espera poder usar vía `allowed-tools` en su
frontmatter (`edecan_skills.installer.parse_capabilities`) — `CAPACIDADES_PELIGROSAS` es la
lista fija de nombres de tool `dangerous=True` del repo (`ARCHITECTURE.md` §10.7, §14):
mencionar una de estas en `allowed-tools` NO le da a la skill ningún poder real (Edecán
nunca ejecuta código de una skill, solo le entrega su texto al modelo — ver
`docs/skills.md`), pero SÍ es información de riesgo que la UI/el chat deben mostrar con
claridad antes de activarla (`tools.UsarSkillTool`, `routers.skills` PATCH de activación).

## Escaneo anti-inyección

`escanear_inyeccion` es heurístico y best-effort — regexes sobre texto plano, sin ningún
modelo de lenguaje ni backend nativo. Detecta los patrones MÁS comunes y documentados de
intento de anular instrucciones (`docs/skills.md` los enumera), pero un atacante con
suficiente esfuerzo SIEMPRE puede ofuscar texto para evadir un escáner basado en regex —
esto es una capa de defensa en profundidad, no una garantía. `store.insert_skill` lo corre
en cada instalación y desactiva automáticamente cualquier skill con hallazgos; el humano
puede reactivarla de todos modos tras revisar el contenido (`acknowledge`, ver
`routers.skills`) — Edecán nunca bloquea una instalación por esto, solo la marca.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Trust tiers
# ---------------------------------------------------------------------------

# Orden: más confiable primero (mismo criterio que `openjarvis.skills.security.TrustTier`,
# que ordena bundled > workspace > indexed > unreviewed). Edecán v5 solo tiene DOS: no hay
# todavía concepto de skill "bundled" (empaquetada con el propio producto) ni "workspace"
# (carpeta de skills del proyecto local del usuario) — v5 únicamente instala skills de
# terceros por red (marketplace indexado o `owner/repo` directo). Si esas dos fuentes se
# agregan en el futuro, se suman AL INICIO de esta tupla sin romper el contrato de
# `clasificar_trust_tier` (sigue devolviendo uno de los valores de `TRUST_TIERS`).
TRUST_TIERS: tuple[str, str] = ("indexada", "sin_revisar")

# Fuentes de `buscar_skills`/`instalar_skill` que cuentan como "índice curado" a efectos de
# `clasificar_trust_tier` — compartida entre `tools.py` y `routers.skills` para no declarar
# el mismo conjunto de nombres en dos sitios.
FUENTES_INDEXADAS: frozenset[str] = frozenset({"skills_sh", "openclaw", "hermes"})


def clasificar_trust_tier(en_indice: bool) -> str:
    """`"indexada"` si la skill se instaló a partir de un hit de un índice curado
    (skills.sh, OpenClaw o Hermes vía `buscar_skills`/`FUENTES_INDEXADAS`) — `"sin_revisar"`
    en cualquier otro caso (instalación directa por `owner/repo`, sin pasar por ningún
    índice). "Indexada" NO significa "auditada por Edecán": solo que el índice de origen
    la listó — sigue siendo contenido de un tercero (ver docstring del módulo)."""
    return TRUST_TIERS[0] if en_indice else TRUST_TIERS[1]


# ---------------------------------------------------------------------------
# Capacidades
# ---------------------------------------------------------------------------

# Nombres EXACTOS de las tools `dangerous=True` del repo (verificado con grep sobre el
# código real, no de memoria — ver `packages/*/edecan_*/tools.py` y `premium/edecan_premium/
# tools.py`). `preparar_nomina`/`preparar_reserva` son v5, pinned en `ARCHITECTURE.md` §14
# — declarar cualquiera de estos nombres en `allowed-tools` NO activa ningún poder real
# (ver docstring del módulo), es solo la señal de riesgo que la UI/el chat muestran.
CAPACIDADES_PELIGROSAS: frozenset[str] = frozenset(
    {
        "usar_computadora",
        "enviar_mensaje",
        "enviar_correo",
        "enviar_sms",
        "llamar_contacto",
        "lanzar_campana",
        "publicar_social",
        "preparar_pago",
        "preparar_orden",
        "gestionar_automatizacion",
        "preparar_nomina",
        "preparar_reserva",
    }
)


def validar_capacidades(
    capabilities: list[str], permitidas: frozenset[str] | set[str]
) -> list[str]:
    """Capacidades de `capabilities` que NO están en `permitidas` — `[]` significa que
    todas las capacidades declaradas están autorizadas. Infraestructura hacia adelante
    (mismo rol que `openjarvis.skills.security.validate_capabilities`): hoy Edecán no ata
    todavía un allowlist de capacidades por plan/tenant, pero el paso de "¿qué le falta
    autorizar a esta skill?" queda listo para cuando exista uno, sin tener que revisitar
    `edecan_skills`."""
    permitidas_set = permitidas if isinstance(permitidas, (frozenset, set)) else set(permitidas)
    return [cap for cap in capabilities if cap not in permitidas_set]


def capacidades_peligrosas(capabilities: list[str]) -> list[str]:
    """Subconjunto de `capabilities` que aparece en `CAPACIDADES_PELIGROSAS`, sin
    duplicados y preservando el orden de aparición en `capabilities`."""
    vistas: set[str] = set()
    resultado: list[str] = []
    for cap in capabilities:
        if cap in CAPACIDADES_PELIGROSAS and cap not in vistas:
            vistas.add(cap)
            resultado.append(cap)
    return resultado


# ---------------------------------------------------------------------------
# Escaneo anti-inyección
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HallazgoInyeccion:
    """Un hallazgo del escáner heurístico (ver docstring del módulo)."""

    patron: str  # nombre corto del heurístico que disparó (ej. "anulacion_imperativa")
    fragmento: str  # texto que matcheó, truncado a `_MAX_FRAGMENTO` chars
    posicion: int  # offset (en caracteres) donde empieza el match dentro del texto original


_MAX_FRAGMENTO = 80
_MAX_HALLAZGOS = 50  # cap defensivo: nunca construir un resultado de tool/HTTP gigante.

# Frases imperativas de anulación de instrucciones — inglés y español, los dos idiomas que
# de verdad circulan en SKILL.md de terceros y en las instrucciones de Edecán.
_RE_ANULACION_IMPERATIVA = re.compile(
    r"(?i)ignore (all )?previous instructions|disregard (your|all)"
    r"|olvida (todas )?tus instrucciones|ignora (todas )?las instrucciones"
)

# Suplantación de system prompt / jailbreak.
_RE_SUPLANTACION_SISTEMA = re.compile(r"(?i)you are now|system prompt|jailbreak|DAN mode")

# Exfiltración: una URL con una plantilla tipo {api_key}/{token}/{password} en vez de un
# valor literal (la skill le pide al modelo que RELLENE el secreto ahí), o un `data:` URI
# (forma común de embeber/exfiltrar un blob arbitrario inline).
_RE_EXFILTRACION = re.compile(
    r"https?://\S*\{(?:api_key|token|password)\}\S*" r"|data:[^\s,]+,"
)

# Caracteres de ancho cero: ZWSP..RLM (U+200B-U+200F), WORD JOINER..INVISIBLE PLUS
# (U+2060-U+2064), y ZERO WIDTH NO-BREAK SPACE/BOM (U+FEFF) — técnica clásica para esconder
# texto invisible dentro de un documento que sí se muestra "limpio" a simple vista. `+` para
# agrupar una racha contigua en un único hallazgo en vez de uno por carácter. Escapes
# `\uXXXX` explícitos a propósito (nunca los caracteres literales en el código fuente): un
# `.py` con caracteres invisibles de verdad incrustados es frágil y confuso de revisar.
_RE_ANCHO_CERO = re.compile("[\u200b-\u200f\u2060-\u2064\ufeff]+")

# Comentarios HTML — se revisan aparte (ver `_hallazgos_comentarios_html`) para marcar
# específicamente los que ESCONDEN una anulación/suplantación dentro de un comentario que
# muchos visores de Markdown ni siquiera renderizan.
_RE_COMENTARIO_HTML = re.compile(r"<!--.*?-->", re.DOTALL)

# Bloque base64 sospechosamente largo: contenido binario/codificado que no tiene lugar en
# instrucciones en lenguaje natural para un agente.
_RE_BASE64_LARGO = re.compile(r"[A-Za-z0-9+/=]{400,}")

_PATRONES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("anulacion_imperativa", _RE_ANULACION_IMPERATIVA),
    ("suplantacion_sistema", _RE_SUPLANTACION_SISTEMA),
    ("exfiltracion", _RE_EXFILTRACION),
    ("caracteres_ancho_cero", _RE_ANCHO_CERO),
    ("base64_sospechoso", _RE_BASE64_LARGO),
)


def _fragmento(texto: str, inicio: int, fin: int) -> str:
    trozo = texto[inicio:fin]
    return trozo if len(trozo) <= _MAX_FRAGMENTO else trozo[:_MAX_FRAGMENTO] + "…"


def _hallazgos_comentarios_html(texto: str) -> list[HallazgoInyeccion]:
    """Comentarios `<!--…-->` cuyo contenido incluye una anulación/suplantación — muchos
    renderizadores de Markdown no muestran comentarios HTML, así que esconder ahí una
    instrucción es un intento particularmente sigiloso."""
    hallazgos: list[HallazgoInyeccion] = []
    for match in _RE_COMENTARIO_HTML.finditer(texto):
        contenido = match.group(0)
        if _RE_ANULACION_IMPERATIVA.search(contenido) or _RE_SUPLANTACION_SISTEMA.search(contenido):
            hallazgos.append(
                HallazgoInyeccion(
                    patron="comentario_html_imperativo",
                    fragmento=_fragmento(texto, match.start(), match.end()),
                    posicion=match.start(),
                )
            )
    return hallazgos


def escanear_inyeccion(texto: str) -> list[HallazgoInyeccion]:
    """Escanea `texto` (pensado para el cuerpo de un `SKILL.md`) con las heurísticas de
    arriba y devuelve la lista de hallazgos, ordenada por posición y capada a
    `_MAX_HALLAZGOS`. `[]` significa "nada sospechoso encontrado" — NUNCA significa "texto
    garantizado seguro" (ver docstring del módulo: esto es heurístico best-effort, no un
    analizador semántico)."""
    cuerpo = texto or ""
    hallazgos: list[HallazgoInyeccion] = []

    for nombre, patron in _PATRONES:
        for match in patron.finditer(cuerpo):
            hallazgos.append(
                HallazgoInyeccion(
                    patron=nombre,
                    fragmento=_fragmento(cuerpo, match.start(), match.end()),
                    posicion=match.start(),
                )
            )

    hallazgos.extend(_hallazgos_comentarios_html(cuerpo))

    hallazgos.sort(key=lambda h: h.posicion)
    return hallazgos[:_MAX_HALLAZGOS]


__all__ = [
    "CAPACIDADES_PELIGROSAS",
    "FUENTES_INDEXADAS",
    "TRUST_TIERS",
    "HallazgoInyeccion",
    "capacidades_peligrosas",
    "clasificar_trust_tier",
    "escanear_inyeccion",
    "validar_capacidades",
]
