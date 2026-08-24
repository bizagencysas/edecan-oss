"""Benchmark local reproducible del overhead determinista de Edecán.

Mide fast-path, clasificación, rewrite y selección de tools bajo carga
concurrente. No llama LLM, red, DB ni herramientas con efectos. Sus números no
representan TTFA de proveedor: sirven para detectar regresiones del núcleo
antes de mezclar la latencia externa.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import time
from collections.abc import Iterable
from typing import Any

from edecan_core.capability_routing import select_tool_specs
from edecan_core.fast_path import classify_intent, is_trivial
from edecan_core.query_rewrite import rewrite_query
from edecan_schemas import ToolSpec

_ACTIONS = (
    "Revisa",
    "Investiga",
    "Compara",
    "Explica",
    "Organiza",
    "Crea un resumen de",
    "Analiza",
    "Busca información sobre",
)
_OBJECTS = (
    "el proyecto Edecán",
    "la versión actual de FastAPI",
    "el documento de arquitectura",
    "los pendientes del equipo",
    "una imagen adjunta",
    "el reporte financiero",
    "el repositorio de Example App",
    "un proveedor disponible",
)
_CONTEXTS = (
    "para hoy",
    "y cita las fuentes",
    "sin modificar archivos",
    "con prioridad alta",
    "para mi próximo turno",
    "y recuérdamelo mañana",
    "con una respuesta breve",
    "usando contexto reciente",
)
_CASES = tuple(
    f"{action} {subject} {context}"
    for action in _ACTIONS
    for subject in _OBJECTS
    for context in _CONTEXTS
)
_TOOLS = tuple(
    ToolSpec(
        name=name,
        description=f"Capacidad determinista {name}",
        input_schema={"type": "object", "properties": {}},
    )
    for name in (
        "buscar_web",
        "crear_recordatorio",
        "delegar_mision",
        "analizar_imagen",
        "crear_artefactos",
        "leer_archivo",
        "editar_pdf",
        "hora_actual",
    )
)


def percentile(values: Iterable[int], fraction: float) -> int:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile necesita al menos una medición")
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return ordered[index]


def _one(case: str) -> int:
    started = time.perf_counter_ns()
    is_trivial(case)
    classify_intent(case)
    rewrite_query(case, "Investiga el proyecto Edecán")
    select_tool_specs(_TOOLS, case, recent_user_texts=("Revisa el proyecto",))
    return time.perf_counter_ns() - started


def run(iterations: int, concurrency: int) -> dict[str, Any]:
    total = iterations * len(_CASES)
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        samples = list(pool.map(_one, (_CASES[i % len(_CASES)] for i in range(total))))
    elapsed = time.perf_counter() - started
    milliseconds = {
        "p50": percentile(samples, 0.50) / 1e6,
        "p95": percentile(samples, 0.95) / 1e6,
        "p99": percentile(samples, 0.99) / 1e6,
    }
    return {
        "format": "edecan-phase3-benchmark.v1",
        "source": "local_deterministic_routing",
        "iterations_per_case": iterations,
        "case_count": len(_CASES),
        "samples": total,
        "concurrency": concurrency,
        "elapsed_seconds": round(elapsed, 6),
        "throughput_samples_per_second": round(total / elapsed, 2) if elapsed else None,
        "latency_ms": {key: round(value, 6) for key, value in milliseconds.items()},
        "external_calls": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=2)
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args()
    if args.iterations < 1 or args.iterations > 10_000:
        parser.error("--iterations debe estar entre 1 y 10000")
    if args.concurrency < 1 or args.concurrency > 64:
        parser.error("--concurrency debe estar entre 1 y 64")
    print(json.dumps(run(args.iterations, args.concurrency), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
