"use client";

import { useEffect, useState } from "react";

import { Alert, Button, Field, Input, Select, Spinner } from "@/components/ui";
import { getLlmModels, updateLlmModels, type LlmModelsOut } from "@/lib/api-configuracion";

export function SelectorModeloLLM({ onUpdated }: { onUpdated: () => void }) {
  const [catalogo, setCatalogo] = useState<LlmModelsOut | null>(null);
  const [principal, setPrincipal] = useState("");
  const [rapido, setRapido] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let active = true;
    void getLlmModels()
      .then((value) => {
        if (!active) return;
        setCatalogo(value);
        setPrincipal(value.model_principal ?? value.models[0] ?? "");
        setRapido(value.model_rapido ?? value.model_principal ?? value.models[0] ?? "");
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "No se pudieron cargar los modelos.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  async function guardar() {
    if (!principal.trim()) return;
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      await updateLlmModels({
        model_principal: principal.trim(),
        model_rapido: rapido.trim() || principal.trim(),
      });
      setSaved(true);
      onUpdated();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo cambiar el modelo.");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <div className="flex items-center gap-2 text-xs text-slate-500"><Spinner className="h-3.5 w-3.5" /> Buscando modelos disponibles…</div>;
  }

  const modelos = catalogo?.models ?? [];
  return (
    <div className="space-y-3 rounded-lg border border-slate-200 p-3 dark:border-slate-800">
      <div>
        <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">Modelo activo</p>
        <p className="text-xs text-slate-500 dark:text-slate-400">
          Cambia la inteligencia, no los poderes: internet, archivos y herramientas pertenecen a Edecán.
        </p>
      </div>
      {modelos.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Principal" htmlFor="llm_model_principal_select">
            <Select id="llm_model_principal_select" value={principal} onChange={(event) => setPrincipal(event.target.value)}>
              {modelos.map((model) => <option key={model} value={model}>{model}</option>)}
            </Select>
          </Field>
          <Field label="Rápido" htmlFor="llm_model_fast_select">
            <Select id="llm_model_fast_select" value={rapido} onChange={(event) => setRapido(event.target.value)}>
              {modelos.map((model) => <option key={model} value={model}>{model}</option>)}
            </Select>
          </Field>
        </div>
      )}
      <details>
        <summary className="cursor-pointer text-xs font-medium text-brand-600 dark:text-brand-400">
          Escribir un modelo nuevo manualmente
        </summary>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <Field label="Modelo principal" htmlFor="llm_model_principal_manual">
            <Input id="llm_model_principal_manual" value={principal} onChange={(event) => setPrincipal(event.target.value)} autoComplete="off" />
          </Field>
          <Field label="Modelo rápido" htmlFor="llm_model_fast_manual">
            <Input id="llm_model_fast_manual" value={rapido} onChange={(event) => setRapido(event.target.value)} autoComplete="off" />
          </Field>
        </div>
      </details>
      {catalogo?.discovery_error && <Alert variant="info">No se pudo refrescar la lista; puedes escribir el modelo manualmente.</Alert>}
      {error && <Alert variant="error">{error}</Alert>}
      {saved && <Alert variant="success">Modelo actualizado.</Alert>}
      <Button size="sm" onClick={() => void guardar()} loading={saving} disabled={saving || !principal.trim()}>
        Usar este modelo
      </Button>
    </div>
  );
}
