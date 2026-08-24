"use client";

/**
 * Lista de skills instaladas (`/app/skills`, WP-V3-04) — un `<InstalledSkillItem>` por
 * fila. `pendingAcknowledge`/`onConfirmEnable`/`onCancelAcknowledge` (WP-V5-04) enrutan el
 * flujo de confirmación al ÍTEM cuyo `id` coincide; ver el docstring de
 * `InstalledSkillItem.tsx` (incluye la nota de por qué WP-V5-04 toca `components/skills/`,
 * fuera de sus rutas asignadas).
 */

import { InstalledSkillItem } from "@/components/skills/InstalledSkillItem";
import { EmptyState, Spinner } from "@/components/ui";
import type { PendingAcknowledge, SkillSummary } from "@/lib/api-skills";

export function InstalledSkillsList({
  skills,
  loading,
  busyId,
  onToggle,
  onDelete,
  pendingAcknowledge,
  onConfirmEnable,
  onCancelAcknowledge,
}: {
  skills: SkillSummary[];
  loading: boolean;
  busyId: string | null;
  onToggle: (skill: SkillSummary, enabled: boolean) => void;
  onDelete: (skill: SkillSummary) => void;
  pendingAcknowledge: PendingAcknowledge | null;
  onConfirmEnable: (skill: SkillSummary) => void;
  onCancelAcknowledge: () => void;
}) {
  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <Spinner className="h-5 w-5 text-slate-400" />
      </div>
    );
  }

  if (skills.length === 0) {
    return (
      <EmptyState
        title="Sin skills instaladas todavía"
        description="Busca en el marketplace arriba, o instala directo si ya conoces el 'owner/repo'."
      />
    );
  }

  return (
    <ul className="space-y-3">
      {skills.map((skill) => (
        <InstalledSkillItem
          key={skill.id}
          skill={skill}
          busy={busyId === skill.id}
          onToggle={(enabled) => onToggle(skill, enabled)}
          onDelete={() => onDelete(skill)}
          acknowledgeMensaje={
            pendingAcknowledge?.skillId === skill.id ? pendingAcknowledge.mensaje : null
          }
          onConfirmEnable={() => onConfirmEnable(skill)}
          onCancelAcknowledge={onCancelAcknowledge}
        />
      ))}
    </ul>
  );
}
