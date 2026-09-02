/**
 * Opciones y helpers compartidos por las hojas de crear/editar compañeros.
 */

import type { AutonomyLevel } from "@/lib/api";

export const AUTONOMY_OPTIONS: {
  value: AutonomyLevel;
  label: string;
  description: string;
}[] = [
  {
    value: "ask",
    label: "Pregunta primero",
    description: "Siempre pide tu OK antes de actuar.",
  },
  {
    value: "read_only",
    label: "Solo lectura",
    description: "Consulta e informa; no ejecuta cambios.",
  },
  {
    value: "draft",
    label: "Redacta borradores",
    description: "Prepara borradores para que tú los apruebes.",
  },
  {
    value: "full",
    label: "Autonomía completa",
    description: "Actúa sin pedir aprobación.",
  },
];

export function autonomyLabel(level: AutonomyLevel | null | undefined): string {
  return AUTONOMY_OPTIONS.find((o) => o.value === level)?.label ?? "Pregunta primero";
}

/**
 * Relación del compañero con el dueño. Los valores son los que el backend
 * acepta (`persistent_agents.py` create/patch): "profesional", "amigo",
 * "coach" (además de "romantico", que no se expone en la UI).
 */
export const RELATION_OPTIONS: { value: string; label: string; description: string }[] = [
  {
    value: "profesional",
    label: "Socio",
    description: "Un colega de trabajo: foco en la tarea y resultados.",
  },
  {
    value: "amigo",
    label: "Amigo",
    description: "Cercano y relajado: conversa como un amigo.",
  },
  {
    value: "coach",
    label: "Coach",
    description: "Te empuja, te reta y te acompaña a mejorar.",
  },
];

export function relationLabel(relation: string | null | undefined): string {
  return RELATION_OPTIONS.find((o) => o.value === relation)?.label ?? "Socio";
}

/** Pretty-print de un JSONB para mostrar en el detalle; `null` si está vacío. */
export function jsonBlock(
  value: Record<string, unknown> | null | undefined,
): string | null {
  if (!value || Object.keys(value).length === 0) return null;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}