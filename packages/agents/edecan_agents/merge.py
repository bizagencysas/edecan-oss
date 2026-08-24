"""Detección de conflictos entre resultados de pasos de una misión (PHASE2 §78).

Antes de la síntesis final, el orquestador pasa los resultados de los pasos
por :func:`detect_conflicts` para encontrar contradicciones numéricas
evidentes (dos pasos que reportan cifras incompatibles sobre el mismo tema).
La función es deliberadamente heurística y defensiva: nunca lanza; si no
puede parsear algo, simplemente no reporta conflicto para ese par.

El sintetizador (LLM) recibe los conflictos detectados como parte del prompt
para que pueda reconciliarlos en vez de promediarlos en silencio.
"""

from __future__ import annotations

import re
from typing import Any

_NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?")
# Tokens de 4+ letras (alfabéticos, incluyendo acentos/ñ) que sirven como
# "tema" aproximado de un paso. Cortos a propósito para no unir pasos que
# solo comparten conectores ("para", "como") y para que la intersección sea
# significativa.
_TOKEN_RE = re.compile(r"[A-Za-zÁÉÍÓÚáéíóúÑñ]{4,}")

# Umbral relativo para considerar que dos cifras difieren "significativamente".
# 0.2 = al menos 20% de diferencia sobre la mayor. Se complementa con un
# umbral absoluto para no marcar ruido alrededor de cero.
_RELATIVO_MIN = 0.2
# Umbral absoluto: una diferencia >=10 unidades ya es material aunque la
# relativa sea pequeña (p. ej. 105 vs 115). Evita ignorar desacuerdos reales
# sobre magnitudes grandes que el relativo aplasta.
_ABSOLUTO_MIN = 10.0


def _numeros(texto: str) -> list[float]:
    """Extrae todos los números de un texto, normalizando coma decimal."""
    if not texto:
        return []
    out: list[float] = []
    for match in _NUM_RE.findall(texto):
        try:
            out.append(float(match.replace(",", ".")))
        except ValueError:
            continue
    return out


def _tokens(texto: str) -> set[str]:
    """Tokens alfabéticos de 4+ letras (en minúsculas) que sirven de tema."""
    if not texto:
        return set()
    return {m.lower() for m in _TOKEN_RE.findall(texto)}


def detect_conflicts(step_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Devuelve conflictos numéricos entre pares de resultados de pasos.

    Cada conflicto es un dict con las claves ``step_a_idx``, ``step_b_idx``,
    ``topic`` y ``conflict_description`` (los índices son los del paso tal
    cual llegan en ``step_results[i]["idx"]``, 0-based).

    Heurística simple: dos pasos cuyas instrucciones comparten un tema (token
    de 4+ letras) y cuyos resultados contienen cifras que difieren de forma
    significativa (>=20% relativo sobre la mayor, o diferencia absoluta >=10)
    se marcan. Nunca lanza: cualquier error de parseo descarta el par en
    silencio en vez de romper la síntesis.
    """
    try:
        items: list[tuple[int, list[float], set[str]]] = []
        for sr in step_results or []:
            idx = sr.get("idx")
            if idx is None:
                continue
            resultado = str(sr.get("resultado") or "")
            instruccion = str(sr.get("instruccion") or "")
            items.append((int(idx), _numeros(resultado), _tokens(instruccion)))
    except Exception:
        return []

    conflicts: list[dict[str, Any]] = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            idx_a, nums_a, topic_a = items[i]
            idx_b, nums_b, topic_b = items[j]
            shared = topic_a & topic_b
            if not shared or not nums_a or not nums_b:
                continue
            # Cifra más prominente de cada resultado (el mayor valor absoluto).
            a = max(abs(n) for n in nums_a)
            b = max(abs(n) for n in nums_b)
            if a == 0 and b == 0:
                continue
            mayor = max(a, b)
            diff = abs(a - b)
            if diff == 0:
                continue
            relativo = diff / mayor if mayor else 0.0
            if relativo < _RELATIVO_MIN and diff < _ABSOLUTO_MIN:
                continue
            topic = sorted(shared)[0]
            conflicts.append(
                {
                    "step_a_idx": idx_a,
                    "step_b_idx": idx_b,
                    "topic": topic,
                    "conflict_description": (
                        f"El paso {idx_a + 1} reporta ~{a:g} y el paso "
                        f"{idx_b + 1} reporta ~{b:g} sobre '{topic}'; difieren "
                        f"({relativo:.0%} relativo, {diff:g} absoluto)."
                    ),
                }
            )
    return conflicts


__all__ = ["detect_conflicts"]
