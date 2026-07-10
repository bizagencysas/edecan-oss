import type { MissionStepDetalle } from "@/lib/api-misiones";

export interface OlaInfo {
  /** 1-based: la primera ola detectada es la 1. */
  ola: number;
  /** Cuántos pasos comparten esta ola (incluido este). */
  tamano: number;
}

/**
 * Agrupa los pasos por "ola" de ejecución REAL, a partir de sus `started`/
 * `finished` (WP-V6-10): dos pasos cuyos intervalos `[started, finished]` se
 * solapan corrieron en paralelo dentro de la misma ola
 * (`Orchestrator._construir_olas`, `packages/agents/edecan_agents/
 * orchestrator.py`).
 *
 * No hay ninguna columna que guarde el número de ola en sí — `depende_de`
 * (con el que el Orchestrator arma las olas de verdad) vive escondido en
 * `agent_steps.usage` SOLO mientras el paso sigue `pending`, y desaparece en
 * cuanto corre (`docs/agentes.md` §4bis), así que no es recuperable después
 * del hecho. Esto es una reconstrucción aproximada del lado del cliente a
 * partir de timestamps reales — no una copia exacta del algoritmo del
 * Orchestrator, pero sí honesta: si dos pasos se solaparon en el tiempo, de
 * verdad corrieron en paralelo. Pasos sin `started`/`finished` (misiones de
 * antes de este WP, o pasos que todavía no terminaron) no aparecen en el
 * mapa devuelto.
 */
export function calcularOlas(steps: MissionStepDetalle[]): Map<number, OlaInfo> {
  const conTiempo = steps
    .map((step) => ({
      seq: step.seq,
      inicio: step.started ? Date.parse(step.started) : Number.NaN,
      fin: step.finished ? Date.parse(step.finished) : Number.NaN,
    }))
    .filter((s) => Number.isFinite(s.inicio) && Number.isFinite(s.fin))
    .sort((a, b) => a.inicio - b.inicio);

  const asignaciones: Array<{ seq: number; ola: number }> = [];
  let olaActual = 0;
  let finMaximoOlaActual = Number.NEGATIVE_INFINITY;
  for (const paso of conTiempo) {
    if (paso.inicio > finMaximoOlaActual) {
      olaActual += 1;
      finMaximoOlaActual = paso.fin;
    } else {
      finMaximoOlaActual = Math.max(finMaximoOlaActual, paso.fin);
    }
    asignaciones.push({ seq: paso.seq, ola: olaActual });
  }

  const tamanos = new Map<number, number>();
  for (const a of asignaciones) {
    tamanos.set(a.ola, (tamanos.get(a.ola) ?? 0) + 1);
  }

  const resultado = new Map<number, OlaInfo>();
  for (const a of asignaciones) {
    resultado.set(a.seq, { ola: a.ola, tamano: tamanos.get(a.ola) ?? 1 });
  }
  return resultado;
}
