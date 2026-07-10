"use client";

/**
 * `/app/rrhh` — RRHH: empleados, ausencias y nómina en borrador (`ARCHITECTURE.md` §14,
 * WP-V5-08; ver `docs/rrhh.md` para el contrato completo). Tres pestañas, mismo patrón de
 * tab-bar que `/app/ide` (sin un componente `Tabs` compartido en `components/ui.tsx`, cada
 * página arma el suyo con botones + `border-b-2`).
 *
 * La pestaña Nómina es la única con un guardrail de dinero visible: generar un borrador
 * nunca mueve nada, pero "Aprobar" pasa por un segundo modal de confirmación explícita
 * (`AprobarNominaModal`) que repite el aviso — ver `docs/rrhh.md`.
 */

import { useState } from "react";

import { PageHeader } from "@/components/ui";
import { AusenciasTab } from "@/components/rrhh/AusenciasTab";
import { EmpleadosTab } from "@/components/rrhh/EmpleadosTab";
import { NominaTab } from "@/components/rrhh/NominaTab";

type Tab = "empleados" | "ausencias" | "nomina";

const TABS: { key: Tab; label: string }[] = [
  { key: "empleados", label: "Empleados" },
  { key: "ausencias", label: "Ausencias" },
  { key: "nomina", label: "Nómina" },
];

export default function RrhhPage() {
  const [tab, setTab] = useState<Tab>("empleados");

  return (
    <div>
      <PageHeader
        title="RRHH"
        description="Empleados, ausencias y nómina en borrador. La nómina nunca mueve dinero: solo genera un registro que apruebas tú."
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

      {tab === "empleados" && <EmpleadosTab />}
      {tab === "ausencias" && <AusenciasTab />}
      {tab === "nomina" && <NominaTab />}
    </div>
  );
}
