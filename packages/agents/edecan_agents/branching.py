"""Bifurcación de misiones + planificación contrafactual (PHASE2 §72-§74).

Cuatro piezas PURAS (sin I/O, deterministas, nunca lanzan) que el orquestador
puede usar ANTES de comprometerse a ejecutar un plan caro, largo o
destructivo:

- :class:`Branch` / :func:`branch_from` (§72): explorar "enfoque A" vs
  "enfoque B" bifurcando una COPIA de los pasos sin tocar el estado original.
  Un plan cuesta producir; reescribirlo en caliente para probar una variante
  destruiría la base que hay que conservar si la variante fracasa. Por eso
  `branch_from` hace deep copy y solo reemplaza la instrucción del índice
  indicado: todo lo demás (agente, `depende_de`, pasos vecinos) queda
  intacto y el original es inmutable.

- :func:`merge_branches` (§72): fusionar de forma defensiva los resultados de
  dos ramas en una comparación `{"branch_a", "branch_b", "conflicts"}` para
  que el usuario (o el sintetizador) vea dónde divergen cuantitativamente.
  Nunca lanza: si las ramas llegan mal formadas, se degrada a listas vacías y
  conflictos vacíos en vez de romper el flujo de síntesis.

- :func:`counterfactual_options` (§73): comparar estrategias antes de un
  cambio grande ("migrar el servicio" vs "envolver el servicio existente").
  La inferencia de riesgo/reversibilidad/costo es DETERMINISTA por señales de
  palabras clave en el texto de la estrategia — nunca un LLM — para que la
  comparación sea reproducible y barata, y para que las respuestas no cambien
  entre dos llamadas con el mismo input.

- :func:`eval_plan_quality` (§74): puntuar heurísticamente un plan ANTES de
  ejecutarlo. Revisa número de pasos vs `max_steps`, instrucciones vacías,
  sanidad de `depende_de` (referencias a índices anteriores válidos),
  instrucciones duplicadas y presencia de `agente`. Es el freno barato que
  evita gastar en una misión mal formada cuando el plan es caro/destructivo.

Ninguna de estas funciones toca el LLM, la base de datos ni el
`Orchestrator`: son helpers puros que `orchestrator.py` (u otro módulo) puede
invocar sin riesgo de efectos secundarios.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

# Umbrales para considerar que dos cifras "difieren significativamente"
# (mismos valores que `edecan_agents.merge`, duplicados a propósito para no
# importar un símbolo privado de un módulo hermano — mismo criterio que
# `orchestrator._flags_satisfechos`). Relativo: >=20% sobre la mayor.
# Absoluto: >=10 unidades (para no ignorar desacuerdos sobre magnitudes
# grandes que el relativo aplasta).
_RELATIVO_MIN = 0.2
_ABSOLUTO_MIN = 10.0

_NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


@dataclass
class Branch:
    """Una rama de ejecución de una misión (§72).

    `steps` es la lista de pasos DIVERGENTE (producida por `branch_from`);
    `state` es un dict libre para que el caller persista lo que necesite de
    esta rama (resultados parciales, notas, presupuesto) sin contaminar el
    plan base. Se declara como dataclass para que las ramas sean objetos de
    primera clase comparables e inspeccionables, no dicts anónimos.
    """

    name: str
    steps: list[dict[str, Any]]
    state: dict[str, Any] = field(default_factory=dict)


def branch_from(
    base_steps: list[dict[str, Any]],
    *,
    name: str,
    replace_step_idx: int,
    new_instruction: str,
) -> list[dict[str, Any]]:
    """Produce una nueva lista de pasos que diverge de `base_steps` en el paso
    `replace_step_idx` (le cambia la `instruccion`), SIN mutar el original.

    El porqué de la deep copy: explorar "enfoque A vs enfoque B" significa
    partir del MISMO plan y cambiar UN paso; si se mutara `base_steps` en el
    lugar, la rama A quedaría destruida en cuanto se probara la B, y no habría
    base a la que volver si la variante fracasa. La copia profunda aísla cada
    experimento del otro y del original.

    Comportamiento defensivo (nunca lanza):

    - `replace_step_idx` fuera de `[0, len-1]` (o no numérico) → devuelve la
      copia profunda SIN cambios: no hay divergencia posible, no es un error.
    - `base_steps` no-lista → devuelve lista vacía.
    - `new_instruction` se coerce a `str` y se recorta (`.strip()`).

    `name` es el identificador de la rama: lo consume quien envuelve el
    resultado en un `Branch` (`Branch(name=name, steps=branch_from(...))`);
    esta función solo produce la lista de pasos divergente, no lo incrusta en
    el resultado.
    """
    pasos: list[Any] = []
    try:
        if isinstance(base_steps, list):
            pasos = copy.deepcopy(base_steps)
    except Exception:  # noqa: BLE001 - deep copy nunca debe tumbar la bifurcación
        pasos = list(base_steps) if isinstance(base_steps, list) else []

    idx = _coerce_int(replace_step_idx, None)
    instruccion = str(new_instruction or "").strip()

    if idx is None or idx < 0 or idx >= len(pasos):
        return pasos

    paso = pasos[idx]
    if not isinstance(paso, dict):
        paso = {}
        pasos[idx] = paso
    paso["instruccion"] = instruccion
    return pasos


def merge_branches(
    results_a: list[dict[str, Any]], results_b: list[dict[str, Any]]
) -> dict[str, Any]:
    """Fusiona defensivamente los resultados de dos ramas en una comparación.

    Devuelve ``{"branch_a": [...], "branch_b": [...], "conflicts": [...]}``:

    - `branch_a`/`branch_b` son las dos listas normalizadas (entradas no-dict
      se degradan a `{}` para conservar el alineamiento posicional).
    - `conflicts` lista dónde las ramas divergen de forma que vale la pena
      mostrar: distinto número de pasos (`step_count`) o cifras que se
      contradicen en pasos correspondientes (`numeric_discrepancy`).

    El porqué de "defensiva": este helper se llama con resultados que vienen
    de agentes/LLM (formas que no controlamos) y puede correr en el camino de
    síntesis; un `KeyError`/`TypeError` aquí no debe tumbar la misión. Por eso
    nunca lanza y degrada a comparación vacía ante input roto.
    """
    a = _normalizar_resultados(results_a)
    b = _normalizar_resultados(results_b)
    conflicts: list[dict[str, Any]] = []

    if len(a) != len(b):
        conflicts.append(
            {
                "kind": "step_count",
                "branch_a_steps": len(a),
                "branch_b_steps": len(b),
                "description": (
                    f"Las ramas tienen distinto número de pasos: A={len(a)}, B={len(b)}."
                ),
            }
        )

    for i in range(min(len(a), len(b))):
        ra = a[i]
        rb = b[i]
        texto_a = str(ra.get("resultado") or "")
        texto_b = str(rb.get("resultado") or "")
        nums_a = _numeros(texto_a)
        nums_b = _numeros(texto_b)
        if not nums_a or not nums_b:
            continue
        va = max(abs(n) for n in nums_a)
        vb = max(abs(n) for n in nums_b)
        if va == 0 and vb == 0:
            continue
        mayor = max(va, vb)
        diff = abs(va - vb)
        if diff == 0:
            continue
        relativo = diff / mayor if mayor else 0.0
        if relativo < _RELATIVO_MIN and diff < _ABSOLUTO_MIN:
            continue
        conflicts.append(
            {
                "kind": "numeric_discrepancy",
                "step_a_idx": i,
                "step_b_idx": i,
                "description": (
                    f"El paso {i + 1} reporta ~{va:g} en la rama A y ~{vb:g} en la rama B; "
                    f"difieren ({relativo:.0%} relativo, {diff:g} absoluto)."
                ),
            }
        )

    return {"branch_a": a, "branch_b": b, "conflicts": conflicts}


def counterfactual_options(objective: str, strategies: list[str]) -> list[dict[str, Any]]:
    """Convierte una lista de descripciones de estrategia en candidatos
    comparables ``{"strategy", "risks", "reversibility", "cost_class"}``.

    La inferencia es DETERMINISTA por señales de palabras clave sobre el texto
    de la estrategia (§73): "migrar"/"reemplazar"/"reescribir" → riesgo alto,
    irreversible y costo alto; "envolver"/"adaptador"/"paralelo"/"piloto" →
    riesgo bajo, reversible y costo bajo; "adaptar"/"modificar"/"refactor" →
    punto medio. Cuando no hay señal reconocible, se rellenan defaults neutros
    (riesgo medio, reversibilidad desconocida, costo medio) en vez de inventar.

    `objective` se acepta por coherencia de API (quién llama ya tiene el
    objetivo en la mano), pero NO altera la inferencia: esta debe depender
    solo del texto de la estrategia para que dos llamadas con los mismos
    `strategies` produzcan exactamente la misma comparación.

    Nunca lanza: `strategies` no-lista o vacío → lista vacía; una entrada
    no-string se coerce a `str`.
    """
    try:
        if not isinstance(strategies, list):
            return []
        out: list[dict[str, Any]] = []
        for s in strategies:
            texto = str(s or "")
            try:
                clasificacion = _clasificar_estrategia(texto)
            except Exception:  # noqa: BLE001 - una estrategia rota no debe tumbar la lista
                clasificacion = _CLASIFICACION_DEFAULT
            out.append(
                {
                    "strategy": texto,
                    "risks": clasificacion["risks"],
                    "reversibility": clasificacion["reversibility"],
                    "cost_class": clasificacion["cost_class"],
                }
            )
        return out
    except Exception:  # noqa: BLE001 - ver docstring: nunca lanza
        return []


def eval_plan_quality(plan: list[dict[str, Any]], *, max_steps: int) -> dict[str, Any]:
    """Puntúa heurísticamente un plan ANTES de ejecutarlo (§74).

    Devuelve ``{"score": float, "issues": list[str]}`` con `score` en
    `[0.0, 1.0]` (1.0 = sin problemas) y una lista de issues legibles.

    Revisiones (todas deterministas, sin LLM):

    1. Plan vacío → `score 0.0`.
    2. Número de pasos > `max_steps` → penaliza (excede presupuesto).
    3. Paso sin `instruccion` (o en blanco) → penaliza.
    4. Paso sin `agente` (o en blanco) → penaliza.
    5. `depende_de` con un índice que no es un entero anterior válido
       (`0 <= i < índice propio`, rechazando también `bool`) → penaliza por
       cada referencia inválida.
    6. Instrucciones duplicadas (comparación `.strip()` + `casefold`) →
       penaliza por cada repetición.

    Penalidades (documentadas para que la puntuación sea predecible):
    exceso de pasos -0.2; instrucción vacía -0.1; agente ausente -0.1;
    referencia inválida -0.05; duplicado -0.05 por extra; paso no-dict -0.3.
    El porqué de existir: es el chequeo barato que evita disparar una misión
    larga/destructiva sobre un plan que de todos modos iba a fallar o a
    ejecutarse mal.
    """
    issues: list[str] = []
    score = 1.0

    pasos: list[Any] = plan if isinstance(plan, list) else []

    if not pasos:
        return {"score": 0.0, "issues": ["el plan no tiene pasos"]}

    max_int = _coerce_int(max_steps, None)
    if max_int is not None and len(pasos) > max_int:
        issues.append(f"el plan tiene {len(pasos)} pasos, más que max_steps={max_int}")
        score -= 0.2

    seen: dict[str, list[int]] = {}
    for idx, paso in enumerate(pasos):
        if not isinstance(paso, dict):
            issues.append(f"el paso {idx + 1} no es un dict")
            score -= 0.3
            continue

        instruccion = str(paso.get("instruccion") or "").strip()
        if not instruccion:
            issues.append(f"el paso {idx + 1} no tiene instrucción")
            score -= 0.1
        else:
            seen.setdefault(instruccion.casefold(), []).append(idx)

        agente = str(paso.get("agente") or "").strip()
        if not agente:
            issues.append(f"el paso {idx + 1} no tiene agente")
            score -= 0.1

        deps = paso.get("depende_de")
        if deps is not None:
            if not isinstance(deps, list):
                issues.append(f"el paso {idx + 1} tiene un depende_de inválido (no es lista)")
                score -= 0.05
            else:
                for d in deps:
                    if not _indice_valido(d, idx, len(pasos)):
                        issues.append(
                            f"el paso {idx + 1} referencia un índice inválido {d!r} en depende_de"
                        )
                        score -= 0.05

    for _clave, posiciones in seen.items():
        if len(posiciones) > 1:
            primer = pasos[posiciones[0]]
            texto = str(primer.get("instruccion") or "").strip() if isinstance(primer, dict) else ""
            issues.append(
                "instrucción duplicada entre los pasos "
                + ", ".join(str(p + 1) for p in posiciones)
                + f": {texto!r}"
            )
            score -= 0.05 * (len(posiciones) - 1)

    score = max(0.0, min(1.0, score))
    return {"score": round(score, 6), "issues": issues}


# ---------------------------------------------------------------------------
# Helpers privados
# ---------------------------------------------------------------------------

# Señales de palabras clave (minúsculas) → clasificación de riesgo de una
# estrategia (§73). Se evalúan en orden de severidad: primero las señales de
# MIGRACIÓN/destrucción (lo más conservador, gana si hay mezcla), luego las de
# ENVOLTURA/paralelo (lo más seguro), luego las de adaptación (punto medio).
_HIGH_SIGNALS = (
    "migrat",
    "reemplaz",
    "reescrib",
    "rewrite",
    "rehacer",
    "reconstruir",
    "reestructur",
    "desmantel",
    "destru",
    "elimin",
    "borr",
    "sustitu",
    "cambiar de",
)

_LOW_SIGNALS = (
    "wrap",
    "envolver",
    "envoltur",
    "adapter",
    "adaptador",
    "proxy",
    "fachada",
    "facade",
    "paralelo",
    "piloto",
    "pilot",
    "shadow",
    "sombra",
    "monitorear",
    "observar",
    "solo lectura",
    "read-only",
    "read only",
    "no invasiv",
    "non-invasiv",
    "incremental",
    "faseado",
    "phased",
    "strangler",
)

_MEDIUM_SIGNALS = (
    "adapt",
    "modific",
    "refactor",
    "cambiar",
    "ajust",
    "mejorar",
    "optimiz",
    "moderniz",
)

_CLASIFICACION_DEFAULT = {
    "risks": "media",
    "reversibility": "desconocida",
    "cost_class": "media",
}


def _clasificar_estrategia(texto: str) -> dict[str, str]:
    t = (texto or "").lower()
    if any(sig in t for sig in _HIGH_SIGNALS):
        return {"risks": "alta", "reversibility": "irreversible", "cost_class": "alta"}
    if any(sig in t for sig in _LOW_SIGNALS):
        return {"risks": "baja", "reversibility": "reversible", "cost_class": "baja"}
    if any(sig in t for sig in _MEDIUM_SIGNALS):
        return {"risks": "media", "reversibility": "parcial", "cost_class": "media"}
    return dict(_CLASIFICACION_DEFAULT)


def _numeros(texto: str) -> list[float]:
    """Extrae números de un texto (normalizando coma decimal)."""
    if not texto:
        return []
    out: list[float] = []
    for m in _NUM_RE.findall(texto):
        try:
            out.append(float(m.replace(",", ".")))
        except ValueError:
            continue
    return out


def _normalizar_resultados(results: Any) -> list[dict[str, Any]]:
    """Normaliza la lista de resultados de una rama: no-lista → `[]`; entradas
    no-dict → `{}` (para conservar el alineamiento posicional en la
    comparación)."""
    if not isinstance(results, list):
        return []
    return [r if isinstance(r, dict) else {} for r in results]


def _coerce_int(value: Any, default: int | None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _indice_valido(value: Any, propio_idx: int, total: int) -> bool:
    """Un índice de `depende_de` es válido si es un entero estricto anterior
    al paso propio (`0 <= value < propio_idx`) — mismo criterio que
    `orchestrator._validar_depende_de` (un índice solo puede apuntar hacia
    atrás, lo que por construcción descarta ciclos y referencias a futuro).
    `bool` se rechaza explícito: es subclase de `int` (`True == 1`) pero nunca
    es un índice válido viniendo de un JSON de LLM."""
    if isinstance(value, bool):
        return False
    if not isinstance(value, int):
        return False
    return 0 <= value < propio_idx


__all__ = [
    "Branch",
    "branch_from",
    "merge_branches",
    "counterfactual_options",
    "eval_plan_quality",
]
