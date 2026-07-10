"use client";

import { useState } from "react";

import { Button, Field, Input } from "@/components/ui";

import {
  MISSION_TEMPLATES,
  plantillaCompleta,
  renderMissionTemplate,
  type MissionTemplate,
} from "./plantillas";

/**
 * Grid de plantillas de misión (WP-V6-10, `plantillas.ts`): un click abre el
 * mini-formulario de placeholders de esa plantilla; al completarlo, "Usar
 * esta plantilla" arma el `objetivo` (`renderMissionTemplate`) y se lo pasa a
 * `onUseTemplate` — el padre (`app/misiones/page.tsx`) lo deja listo en el
 * campo de objetivo de "Nueva misión" para que el usuario lo revise/edite
 * antes de crear la misión con el `POST /v1/missions` EXISTENTE (esta pieza
 * nunca llama a la API directamente).
 */
export function PlantillasMisiones({
  onUseTemplate,
}: {
  onUseTemplate: (objetivo: string) => void;
}) {
  const [abiertaId, setAbiertaId] = useState<string | null>(null);
  const [valores, setValores] = useState<Record<string, string>>({});

  function alternar(template: MissionTemplate) {
    if (abiertaId === template.id) {
      setAbiertaId(null);
      return;
    }
    setAbiertaId(template.id);
    setValores({});
  }

  function usar(template: MissionTemplate) {
    onUseTemplate(renderMissionTemplate(template, valores));
    setAbiertaId(null);
    setValores({});
  }

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {MISSION_TEMPLATES.map((template) => {
        const abierta = abiertaId === template.id;
        return (
          <div
            key={template.id}
            className={`rounded-xl border p-3.5 transition-colors ${
              abierta
                ? "border-brand-300 bg-brand-50 dark:border-brand-800 dark:bg-brand-950/30"
                : "border-slate-200 dark:border-slate-800"
            }`}
          >
            <p className="text-sm font-medium text-slate-800 dark:text-slate-100">{template.titulo}</p>
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{template.descripcion}</p>

            {abierta ? (
              <div className="mt-3 space-y-2.5">
                {template.campos.map((campo) => (
                  <Field key={campo.key} label={campo.label}>
                    <Input
                      value={valores[campo.key] ?? ""}
                      onChange={(e) =>
                        setValores((prev) => ({ ...prev, [campo.key]: e.target.value }))
                      }
                      placeholder={campo.placeholder}
                    />
                  </Field>
                ))}
                <div className="flex gap-2 pt-1">
                  <Button
                    size="sm"
                    onClick={() => usar(template)}
                    disabled={!plantillaCompleta(template, valores)}
                  >
                    Usar esta plantilla
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setAbiertaId(null)}>
                    Cancelar
                  </Button>
                </div>
              </div>
            ) : (
              <Button
                size="sm"
                variant="secondary"
                className="mt-3"
                aria-expanded={abierta}
                onClick={() => alternar(template)}
              >
                Usar plantilla
              </Button>
            )}
          </div>
        );
      })}
    </div>
  );
}
