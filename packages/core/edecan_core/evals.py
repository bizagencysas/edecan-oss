"""Evaluation framework: golden tasks, regression evals, agent evals (§126-130).

Permite evaluar el harness del agente con un conjunto de tareas reales
que Edecán debe superar. Los evals son funciones que verifican que
el agente produce el comportamiento esperado, no solo que responde 200.

Uso::

    from edecan_core.evals import run_evals, GOLDEN_TASKS

    results = run_evals()
    for r in results:
        print(f"{r.name}: {'PASS' if r.passed else 'FAIL'}")
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_GOLDEN_DATASET_PATH = Path(__file__).with_name("evals_golden.json")


def load_golden_dataset() -> tuple[str, list[dict[str, Any]]]:
    """Carga el dataset versionado y valida su forma antes de ejecutarlo."""
    with _GOLDEN_DATASET_PATH.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    version = payload.get("version")
    cases = payload.get("cases")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("El dataset golden requiere una versión no vacía")
    if not isinstance(cases, list) or not cases:
        raise ValueError("El dataset golden requiere al menos un caso")
    for case in cases:
        if not isinstance(case, dict) or not case.get("name") or not case.get("category"):
            raise ValueError("Cada caso golden requiere name y category")
    return version, cases


GOLDEN_DATASET_VERSION, GOLDEN_TASKS = load_golden_dataset()

"""LEGACY_GOLDEN_TASKS = [
    {
        "name": "tool_selection_web",
        "category": "tool_selection",
        "description": "Una pregunta temporal debe seleccionar buscar_web",
        "input": "¿Qué pasó hoy con Apple?",
        "expect_tools": ["buscar_web"],
        "expect_no_tools": ["enviar_correo", "publicar_social"],
    },
    {
        "name": "tool_selection_code",
        "category": "tool_selection",
        "description": "Una pregunta de código no debe seleccionar enviar_correo",
        "input": "Arregla el login de Example App",
        "expect_tools": [],
        "expect_no_tools": ["enviar_correo", "crear_evento", "agenda_eventos"],
    },
    {
        "name": "memory_retrieval",
        "category": "memory_retrieval",
        "description": "Una pregunta sobre algo previo debe buscar en memoria",
        "input": "¿Qué decidimos sobre el proyecto?",
        "expect_behavior": "uses_memory",
    },
    {
        "name": "vision_inline",
        "category": "vision",
        "description": "Una imagen inline debe ser vista directamente, no por tool",
        "input": "[image] ¿Qué hay en esta foto?",
        "expect_no_tools": ["analizar_imagen"],
    },
    {
        "name": "hallucination_check",
        "category": "hallucination",
        "description": "No debe afirmar 'ya revisé' sin usar una herramienta",
        "input": "Revisa la política de privacidad de mi sitio web",
        "expect_behavior": "uses_tool_before_claiming",
    },
    {
        "name": "citation_generation",
        "category": "citations",
        "description": "Resultados de buscar_web deben generar citations",
        "input": "Busca las últimas noticias de tecnología",
        "expect_tool": "buscar_web",
        "expect_citations": True,
    },
    {
        "name": "latency_simple",
        "category": "latency",
        "description": "Un saludo debe responder en menos de 3 segundos",
        "input": "Hola",
        "max_latency_seconds": 3.0,
    },
    {
        "name": "no_fake_actions",
        "category": "hallucination",
        "description": "No debe decir 'ya envié' sin haber llamado enviar_correo",
        "input": "Envíale un correo a mi mamá",
        "expect_tool": "enviar_correo",
        "expect_behavior": "no_claim_without_tool",
    },
]"""


@dataclass
class EvalResult:
    name: str
    category: str
    passed: bool
    duration_seconds: float = 0.0
    details: str = ""
    error: str | None = None


def run_evals(
    check_fn: Callable[[dict[str, Any]], EvalResult] | None = None,
) -> list[EvalResult]:
    """Ejecuta todos los golden tasks y devuelve los resultados.

    `check_fn` es la función que ejecuta cada eval contra el agente real.
    Si no se proporciona, devuelve resultados placeholder.
    """
    if check_fn is None:
        return [
            EvalResult(
                name=task["name"],
                category=task["category"],
                passed=False,
                details="No check_fn provided",
            )
            for task in GOLDEN_TASKS
        ]
    results: list[EvalResult] = []
    for task in GOLDEN_TASKS:
        start = time.monotonic()
        try:
            result = check_fn(task)
            result.duration_seconds = time.monotonic() - start
            results.append(result)
        except Exception as exc:
            results.append(
                EvalResult(
                    name=task["name"],
                    category=task["category"],
                    passed=False,
                    duration_seconds=time.monotonic() - start,
                    error=str(exc),
                )
            )
    return results


def eval_summary(results: list[EvalResult]) -> dict[str, Any]:
    """Genera un resumen de los evals para telemetría."""
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    by_category: dict[str, dict[str, int]] = {}
    for r in results:
        cat = r.category
        if cat not in by_category:
            by_category[cat] = {"total": 0, "passed": 0}
        by_category[cat]["total"] += 1
        if r.passed:
            by_category[cat]["passed"] += 1
    return {
        "dataset_version": GOLDEN_DATASET_VERSION,
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": passed / total if total else 0.0,
        "by_category": by_category,
    }
