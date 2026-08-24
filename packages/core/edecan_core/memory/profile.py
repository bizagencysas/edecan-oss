"""`build_profile` — construcción pura del «perfil vivo» del usuario (WP-V2-13,
ROADMAP_V2.md §21/§7.4, ARCHITECTURE.md §10.7).

## Qué es el perfil vivo

No es solo "recordar hechos sueltos" (eso ya lo hace `memory_items` desde v1):
es un resumen ESTRUCTURADO y ACUMULATIVO del usuario — gustos, proyectos,
metas, relaciones, empresas, hábitos — que se reconstruye cada vez que corre
`memory_consolidate` (`apps/worker/edecan_worker/handlers/memory_consolidate.py`)
y que ese mismo job **espeja** como memoria de alta importancia para que
`edecan_core.agent.Agent`/`MemoryStore.search` lo inyecte en el system prompt
de CADA turno sin tocar `agent.py` (ver el docstring de `memory_consolidate.py`
para el mecanismo de inyección completo — el "truco" vive ahí, no aquí).

Este módulo NO habla con la base de datos, NO conoce `edecan_llm` ni
`edecan_db`, y NO decide qué modelo usar: es la función pura que arma el
prompt, llama a `llm_complete` (inyectado) y hace el MERGE del resultado
contra el perfil previo. Eso la hace trivialmente testeable con un
`llm_complete` fake determinista (ver `packages/core/tests/test_profile_builder.py`)
y reutilizable desde cualquier caller (hoy solo `memory_consolidate`, pero
nada la ata a un job de cola en particular).

## Contrato de entrada/salida

`build_profile(memories, previous, llm_complete) -> dict` — deliberadamente
trabaja con `dict`s planos (la forma exacta de `edecan_schemas.profile.
LiveProfile.model_dump()` SIN `version`, que es responsabilidad de quien
persiste, no de esta función pura) en vez de objetos `LiveProfile`/`ProfileData`,
para no acoplar la firma pública a Pydantic ni al esquema de la tabla
`user_profiles` — igual que `memory_consolidate._validar_item_extraido` recibe
y devuelve `dict`s sueltos en vez de un modelo.

- `memories`: strings sueltos (memorias recientes/top del usuario — quien
  llama decide el orden/tope, ver `_LIMITE_MEMORIAS_PERFIL` en
  `memory_consolidate.py`).
- `previous`: `{"resumen": str, "datos": {6 listas}}` o `None` (primera vez).
  Cualquier forma parcial/inesperada se normaliza tolerantemente (ver
  `_perfil_previo_normalizado`) — nunca lanza por un `previous` "raro".
- `llm_complete`: `async (prompt: str) -> str`, INYECTADO — esta función arma
  el prompt completo (perfil previo + memorias + instrucciones de formato) y
  se lo pasa tal cual. Quien llama decide qué proveedor/alias/modelo resolver
  y cómo registrar el consumo (`usage_events`) — `build_profile` no sabe nada
  de eso, a propósito (no acoplarse a `edecan_llm.router.LLMRouter`).
- Retorno: SIEMPRE `{"resumen": str, "datos": {6 listas}}`, nunca lanza. Si
  `llm_complete` falla o responde algo sin un bloque JSON reconocible, se
  devuelve el `previous` normalizado tal cual (o el esqueleto vacío si no
  había `previous`) — ver "Parseo tolerante" abajo.

## Merge conservador (política de fusión)

El requisito es: "nunca elimina entradas del previo salvo contradicción
explícita (entonces la reemplaza)". Confiar ciegamente en que el LLM
devuelva, categoría por categoría, la lista COMPLETA y correcta ya fusionada
sería frágil (un modelo que "olvida" repetir una entrada vieja en su
respuesta la borraría sin que haya habido ninguna contradicción real) — así
que el merge real lo hace ESTE módulo, en Python, de forma determinista:

1. El prompt (`_construir_prompt`) le pide al LLM SOLO lo NUEVO por
   categoría (`datos.<categoria>`: entradas a agregar) más, opcionalmente,
   un bloque separado `reemplaza.<categoria>` con el texto EXACTO de
   entradas viejas que una memoria reciente contradice explícitamente.
   Este bloque `reemplaza` es una convención propia de este prompt (no
   viene de ARCHITECTURE.md/ROADMAP_V2.md, que no bajan a ese nivel de
   detalle) — documentado aquí porque si alguien reescribe el prompt sin
   saber esto, la señal de "contradicción → reemplazo" deja de funcionar en
   silencio (exactamente el tipo de diseño no-obvio que ROADMAP_V2.md §2.5
   pide documentar).
2. `_fusionar_campo` (por categoría) construye el resultado así, siempre
   determinista:
   - Arranca de `previous[categoria]`, quitando (case-insensitive) cualquier
     entrada listada en `reemplaza[categoria]`.
   - Agrega las entradas nuevas de `datos[categoria]` que no estén ya
     presentes (dedup case-insensitive).
   - Recorta a `LISTA_MAX_ITEMS` (20), priorizando lo antiguo sobre lo nuevo
     si hay que recortar (sesgo conservador: el perfil ya construido pesa
     más que una extracción reciente sin vetar).
3. `resumen`: si el LLM devolvió un string no vacío, se usa (recortado a
   `RESUMEN_MAX_CHARS`); si no, se conserva el `resumen` previo.

## Parseo tolerante

`_extraer_primer_bloque_json` usa `json.JSONDecoder.raw_decode` a partir del
primer `{` que encuentra (tras despojar un posible bloque ```json ... ```),
en vez de contar llaves a mano — así soporta tanto una respuesta 100% JSON
como una con preámbulo/cola de prosa alrededor, y no se confunde con `{`/`}`
literales dentro de un valor string (algo que un conteo ingenuo de llaves sí
rompería). Cualquier fallo de parseo, o un bloque que no sea un objeto JSON,
hace que `build_profile` devuelva el perfil previo sin cambios — jamás
lanza.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

LlmComplete = Callable[[str], Awaitable[str]]
"""Callable async inyectado: `(prompt: str) -> str`. Deliberadamente NO
acoplado a `edecan_llm.router.LLMRouter`/`CompletionRequest` (ver docstring
del módulo): quien llama arma el prompt->texto como quiera (hoy,
`memory_consolidate._llm_complete_perfil` resuelve el alias `"rapido"` y
registra `usage_events`)."""

CAMPOS_DATOS: tuple[str, ...] = (
    "gustos",
    "proyectos",
    "metas",
    "relaciones",
    "empresas",
    "habitos",
)
"""Mismas 6 categorías que `edecan_schemas.profile.ProfileData` — repetidas
aquí como tupla plana porque este módulo trabaja con `dict`s a propósito
(ver docstring del módulo), no con el modelo Pydantic directamente."""

RESUMEN_MAX_CHARS = 500
LISTA_MAX_ITEMS = 20


def _esqueleto_vacio() -> dict[str, Any]:
    return {"resumen": "", "datos": {campo: [] for campo in CAMPOS_DATOS}}


def _normalizar(item: Any) -> str:
    return str(item).strip().casefold()


def _lista_de_strings(value: Any) -> list[str]:
    """Coerción tolerante: acepta una lista de `str`/`int`/`float`, descarta
    cualquier otra cosa (`dict`, `None`, listas anidadas, etc.) sin lanzar."""
    if not isinstance(value, list):
        return []
    return [
        texto
        for v in value
        if isinstance(v, (str, int, float))
        and not isinstance(v, bool)
        and (texto := str(v).strip())
    ]


def _perfil_previo_normalizado(previous: dict[str, Any] | None) -> dict[str, Any]:
    """Normaliza `previous` (fila `user_profiles` ya deserializada, o `None`
    la primera vez) a la forma interna canónica `{resumen, datos{6 listas}}`.
    Tolerante a cualquier forma parcial/inesperada — nunca lanza."""
    if not isinstance(previous, dict):
        return _esqueleto_vacio()
    resumen = previous.get("resumen")
    resumen = resumen.strip()[:RESUMEN_MAX_CHARS] if isinstance(resumen, str) else ""
    datos_crudo = previous.get("datos")
    datos_crudo = datos_crudo if isinstance(datos_crudo, dict) else {}
    datos = {
        campo: _lista_de_strings(datos_crudo.get(campo))[:LISTA_MAX_ITEMS] for campo in CAMPOS_DATOS
    }
    return {"resumen": resumen, "datos": datos}


_decoder = json.JSONDecoder()


def _extraer_primer_bloque_json(texto: str) -> dict[str, Any] | None:
    """Extrae el primer objeto JSON de `texto` (ver "Parseo tolerante" en el
    docstring del módulo). `None` si no hay ninguno reconocible."""
    limpio = (texto or "").strip()
    if limpio.startswith("```"):
        limpio = limpio.strip("`")
        if limpio[:4].lower() == "json":
            limpio = limpio[4:]
        limpio = limpio.strip()

    inicio = limpio.find("{")
    if inicio == -1:
        return None
    try:
        data, _fin = _decoder.raw_decode(limpio, inicio)
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _formatear_memorias(memories: list[str]) -> str:
    limpias = [m.strip() for m in memories if isinstance(m, str) and m.strip()]
    if not limpias:
        return "(sin memorias nuevas en esta consolidación)"
    return "\n".join(f"- {m}" for m in limpias)


def _construir_prompt(previo: dict[str, Any], memories: list[str]) -> str:
    perfil_previo_json = json.dumps(previo, ensure_ascii=False, indent=2)
    return (
        "Eres el módulo que mantiene el «perfil vivo» de un usuario dentro de un asistente "
        "personal de IA. Tu tarea es proponer ACTUALIZACIONES a su perfil a partir de memorias "
        "recientes. No converses, no saludes, no expliques nada: tu única salida es un objeto "
        "JSON.\n\n"
        f"Perfil previo (JSON):\n{perfil_previo_json}\n\n"
        f"Memorias recientes a incorporar:\n{_formatear_memorias(memories)}\n\n"
        "Devuelve EXCLUSIVAMENTE un objeto JSON (sin texto antes ni después, sin bloque de "
        "código) con esta forma exacta:\n"
        "{\n"
        "  \"resumen\": \"1-2 frases en SEGUNDA PERSONA ('Prefieres...', 'Trabajas en...', "
        "'Tu meta es...') que resuman lo más importante de este usuario, como si le hablaras a "
        "él. Máximo 500 caracteres. Si el perfil previo ya tenía un buen resumen y nada "
        'relevante cambió, puedes repetirlo.",\n'
        '  "datos": {\n'
        '    "gustos": ["..."],\n'
        '    "proyectos": ["..."],\n'
        '    "metas": ["..."],\n'
        '    "relaciones": ["..."],\n'
        '    "empresas": ["..."],\n'
        '    "habitos": ["..."]\n'
        "  },\n"
        '  "reemplaza": {\n'
        '    "<una de las 6 categorías de arriba>": ["texto EXACTO de una entrada del perfil '
        'previo que una memoria reciente CONTRADICE y ya no aplica"]\n'
        "  }\n"
        "}\n\n"
        "Reglas OBLIGATORIAS:\n"
        "- En `datos.<categoria>` escribe SOLO entradas NUEVAS que quieras agregar. NO repitas "
        "ahí lo que ya estaba en el perfil previo: esas entradas se conservan automáticamente, "
        "tú no tienes que reescribirlas.\n"
        "- `reemplaza` es opcional y casi siempre debe quedar vacío (`{}`). Úsalo SOLO cuando "
        "una memoria reciente contradice EXPLÍCITAMENTE una entrada anterior (p. ej. 'ya no "
        "trabajo en Acme' contradice la entrada 'Trabaja en Acme'). Nunca lo uses solo porque "
        "algo no se volvió a mencionar.\n"
        "- No inventes datos que no estén respaldados por el perfil previo o las memorias "
        "recientes.\n"
        "- Si no hay nada nuevo que agregar en una categoría, devuélvela como lista vacía `[]`."
    )


def _fusionar_campo(*, previos: list[str], nuevos: list[str], contradichos: list[str]) -> list[str]:
    """Merge conservador de UNA categoría (ver "Merge conservador" en el
    docstring del módulo). Determinista: mismo input -> mismo output."""
    contradichos_norm = {_normalizar(x) for x in contradichos}
    resultado: list[str] = []
    vistos: set[str] = set()

    for item in previos:
        norm = _normalizar(item)
        if not norm or norm in contradichos_norm or norm in vistos:
            continue
        vistos.add(norm)
        resultado.append(item)

    for item in nuevos:
        norm = _normalizar(item)
        if not norm or norm in vistos:
            continue
        vistos.add(norm)
        resultado.append(item)

    return resultado[:LISTA_MAX_ITEMS]


async def build_profile(
    memories: list[str],
    previous: dict[str, Any] | None,
    llm_complete: LlmComplete,
) -> dict[str, Any]:
    """Construye/actualiza el perfil vivo. Ver el docstring del módulo para
    el contrato completo. Firma pinned por WP-V2-13 (ROADMAP_V2.md §21):
    `async build_profile(memories, previous, llm_complete) -> dict`.
    """
    previo = _perfil_previo_normalizado(previous)

    prompt = _construir_prompt(previo, memories)
    try:
        respuesta = await llm_complete(prompt)
    except Exception:
        logger.warning(
            "build_profile: llm_complete falló, se conserva el perfil previo tal cual",
            exc_info=True,
        )
        return previo

    bloque = _extraer_primer_bloque_json(respuesta)
    if bloque is None:
        logger.warning(
            "build_profile: la respuesta del LLM no trae un JSON reconocible; "
            "se conserva el perfil previo."
        )
        return previo

    datos_llm = bloque.get("datos")
    datos_llm = datos_llm if isinstance(datos_llm, dict) else {}
    reemplaza = bloque.get("reemplaza")
    reemplaza = reemplaza if isinstance(reemplaza, dict) else {}

    datos_fusionados = {
        campo: _fusionar_campo(
            previos=previo["datos"][campo],
            nuevos=_lista_de_strings(datos_llm.get(campo)),
            contradichos=_lista_de_strings(reemplaza.get(campo)),
        )
        for campo in CAMPOS_DATOS
    }

    resumen_llm = bloque.get("resumen")
    resumen = (
        resumen_llm.strip()
        if isinstance(resumen_llm, str) and resumen_llm.strip()
        else previo["resumen"]
    )

    return {"resumen": resumen[:RESUMEN_MAX_CHARS], "datos": datos_fusionados}
