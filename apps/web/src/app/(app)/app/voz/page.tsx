"use client";

/**
 * `/app/voz` — Voces/clonación, Podcasts y Escucha siempre (`ARCHITECTURE.md`
 * §14, WP-V5-10 + WP-V6-04). Tres pestañas, mismo patrón de tab-bar que
 * `/app/rrhh` (sin un componente `Tabs` compartido en `components/ui.tsx`:
 * botones + `border-b-2`, cada página arma el suyo — ver
 * `components/rrhh/page.tsx`).
 */

import { useState } from "react";

import { PageHeader } from "@/components/ui";
import { EscuchaSiempreTab } from "@/components/voz/EscuchaSiempreTab";
import { PodcastsTab } from "@/components/voz/PodcastsTab";
import { VocesTab } from "@/components/voz/VocesTab";

type Tab = "voces" | "podcasts" | "escucha";

const TABS: { key: Tab; label: string }[] = [
  { key: "voces", label: "Voces" },
  { key: "podcasts", label: "Podcasts" },
  { key: "escucha", label: "Escucha siempre" },
];

export default function VozPage() {
  const [tab, setTab] = useState<Tab>("voces");

  return (
    <div>
      <PageHeader
        title="Voz"
        description="Voces disponibles para síntesis (TTS), clonación con consentimiento verificado, y podcasts generados con tu voz."
      />

      <div className="mb-6 flex gap-1 border-b border-slate-200 dark:border-slate-800">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setTab(t.key)}
            className={`border-b-2 px-3 py-2 text-sm font-medium transition-colors ${
              tab === t.key
                ? "border-brand-600 text-brand-700 dark:text-brand-400"
                : "border-transparent text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "voces" && <VocesTab />}
      {tab === "podcasts" && <PodcastsTab />}
      {tab === "escucha" && <EscuchaSiempreTab />}
    </div>
  );
}
