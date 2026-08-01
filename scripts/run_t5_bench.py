"""Ejecutor de la línea base T5 para el banco de pruebas de Edecán (`packages/forge-probe/bench/edecan.py`).

Ejecuta las tareas triviales y standard de Edecán contra el agente actual (`edecan_core.agent.Agent`),
usando Workers AI o el proveedor configurado, midiendo tasa de éxito, coste en USD y tokens.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

# Asegurar import de paquetes del workspace
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "forge-probe"))
sys.path.insert(0, str(ROOT / "packages" / "forge-probe" / "edecan_forge_probe"))
sys.path.insert(0, str(ROOT / "packages" / "core"))
sys.path.insert(0, str(ROOT / "packages" / "llm"))
sys.path.insert(0, str(ROOT / "packages" / "schemas"))
sys.path.insert(0, str(ROOT / "packages" / "agents"))
sys.path.insert(0, str(ROOT / "packages" / "toolkit"))

from edecan_core.agent import Agent
from edecan_core.persona import PersonaConfig
from edecan_core.tools.base import ToolContext
from edecan_core.tools.registry import ToolRegistry
from bench.edecan import _STANDARD, _TRIVIAL
from edecan_llm.router import LLMRouter, SettingsLike
from edecan_llm.workers_ai import WorkersAIProvider

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("run_t5_bench")


class EnvSettings:
    CLOUDFLARE_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    CLOUDFLARE_API_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    WORKERS_AI_CHAT_MODEL = os.environ.get("WORKERS_AI_CHAT_MODEL", "@cf/zai-org/glm-4.7-flash")
    WORKERS_AI_TIMEOUT_SECONDS = 120.0


async def check_criterio(criterio: Any) -> tuple[bool, str]:
    """Ejecuta un Criterio de prueba."""
    if criterio.kind == "command":
        cmd = list(criterio.comando)
        logger.info(f"  Ejecutando comando: {' '.join(cmd)}")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(ROOT),
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=criterio.timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            return False, f"Timeout ({criterio.timeout_s}s)"

        out_str = stdout.decode("utf-8", errors="replace") + "\n" + stderr.decode("utf-8", errors="replace")
        paso = (proc.returncode == 0)
        return paso, out_str[:500]

    elif criterio.kind == "file_exists":
        path = ROOT / criterio.ruta
        exists = path.is_file()
        return exists, f"Archivo {criterio.ruta} existe={exists}"

    return False, f"Tipo de criterio desconocido: {criterio.kind}"


async def main():
    logger.info("=== INICIANDO LÍNEA BASE T5 BENCHMARK ===")

    settings = EnvSettings()
    router = LLMRouter(settings=settings)
    registry = ToolRegistry()
    agent = Agent(llm_router=router, registry=registry, model_alias="rapido")

    # Selección de tareas
    tareas = list(_TRIVIAL) + list(_STANDARD)
    logger.info(f"Total de tareas a evaluar: {len(tareas)}")

    resultados = []
    exitos = 0

    for idx, tarea in enumerate(tareas, 1):
        logger.info(f"\n--- [{idx}/{len(tareas)}] Evaluando tarea: {tarea.id} ({tarea.clase}) ---")
        logger.info(f"Título: {tarea.titulo}")

        # Comprobar estado inicial del criterio
        criterio_principal = tarea.criterios[0]
        pasa_inicial, detalle_inicial = await check_criterio(criterio_principal)
        logger.info(f"Estado inicial de criterio principal: pasa={pasa_inicial}")

        # Ejecutar turno con el agente
        from uuid import uuid4
        persona = PersonaConfig(nombre="Edecán Bench", descripcion="Evaluador de benchmark")
        tool_ctx = ToolContext(
            tenant_id=uuid4(),
            user_id=uuid4(),
            session=None,
            settings=settings,
            llm=router,
            vault=None,
            extras={},
        )

        start_time = time.monotonic()
        usage_total = {"input_tokens": 0, "output_tokens": 0}

        try:
            async for event in agent.run_turn(
                ctx=tool_ctx,
                persona=persona,
                history=[],
                user_text=f"Instrucción de tarea benchmark:\n{tarea.enunciado}",
                flags={"all": True},
            ):
                if hasattr(event, "usage") and event.usage:
                    usage_total["input_tokens"] += event.usage.get("input_tokens", 0)
                    usage_total["output_tokens"] += event.usage.get("output_tokens", 0)
        except Exception as e:
            logger.error(f"Error durante ejecución del turno en tarea {tarea.id}: {e}")

        elapsed = time.monotonic() - start_time

        # Comprobar criterios post-ejecución
        criterios_evaluacion = []
        tarea_paso = True
        for crit in tarea.criterios:
            pasa, det = await check_criterio(crit)
            criterios_evaluacion.append({"descripcion": crit.descripcion, "pasa": pasa, "detalle": det})
            if not pasa:
                tarea_paso = False

        if tarea_paso:
            exitos += 1

        res_tarea = {
            "id": tarea.id,
            "clase": tarea.clase,
            "titulo": tarea.titulo,
            "exito": tarea_paso,
            "tiempo_s": round(elapsed, 2),
            "usage": usage_total,
            "criterios": criterios_evaluacion,
        }
        resultados.append(res_tarea)
        logger.info(f"Resultado tarea {tarea.id}: {'ÉXITO' if tarea_paso else 'FALLO'}")

    reporte = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_tareas": len(tareas),
        "exitos": exitos,
        "tasa_exito": f"{(exitos / len(tareas)) * 100:.1f}%",
        "resultados": resultados,
    }

    out_file = ROOT / "tmp" / "t5_bench_report.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(reporte, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"\nReporte guardado en {out_file}")
    logger.info(f"Tasa de éxito general: {reporte['tasa_exito']}")

    await router.aclose()


if __name__ == "__main__":
    asyncio.run(main())
