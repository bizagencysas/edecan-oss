"use client";

import { useEffect, useState } from "react";

import { XIcon } from "@/components/icons";
import {
  Alert,
  Button,
  Card,
  CardBody,
  CardHeader,
  Field,
  Input,
  PageHeader,
  Select,
  Spinner,
  Switch,
  Textarea,
} from "@/components/ui";
import { getPersona, previewPersona, updatePersona } from "@/lib/api";
import { PERSONA_DEFAULT, type PersonaConfig } from "@/lib/types";

const FORMALIDAD_LABELS: Record<number, string> = {
  0: "0 — Tú, muy informal",
  1: "1 — Tú, profesional (default)",
  2: "2 — Usted, cordial",
  3: "3 — Usted, muy formal / protocolar",
};

function RasgosEditor({ rasgos, onChange }: { rasgos: string[]; onChange: (next: string[]) => void }) {
  const [draft, setDraft] = useState("");

  function addRasgo() {
    const value = draft.trim();
    if (!value || rasgos.includes(value)) {
      setDraft("");
      return;
    }
    onChange([...rasgos, value]);
    setDraft("");
  }

  function removeRasgo(value: string) {
    onChange(rasgos.filter((r) => r !== value));
  }

  return (
    <div>
      {rasgos.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {rasgos.map((r) => (
            <span
              key={r}
              className="inline-flex items-center gap-1 rounded-full bg-brand-50 px-2.5 py-1 text-xs font-medium text-brand-700 dark:bg-brand-900/40 dark:text-brand-300"
            >
              {r}
              <button
                type="button"
                onClick={() => removeRasgo(r)}
                aria-label={`Quitar rasgo ${r}`}
                className="text-brand-500 hover:text-brand-800 dark:text-brand-400 dark:hover:text-brand-100"
              >
                <XIcon className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="flex gap-2">
        <Input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              addRasgo();
            }
          }}
          placeholder="p. ej. directo, con humor seco…"
        />
        <Button type="button" variant="secondary" onClick={addRasgo}>
          Añadir
        </Button>
      </div>
    </div>
  );
}

export default function PersonaPage() {
  const [persona, setPersona] = useState<PersonaConfig>(PERSONA_DEFAULT);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const [preview, setPreview] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  useEffect(() => {
    void load();
  }, []);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setPersona(await getPersona());
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cargar la persona.");
    } finally {
      setLoading(false);
    }
  }

  async function loadPreview() {
    setPreviewLoading(true);
    try {
      const { system_prompt } = await previewPersona();
      setPreview(system_prompt);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo generar la vista previa.");
    } finally {
      setPreviewLoading(false);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const updated = await updatePersona(persona);
      setPersona(updated);
      setSaved(true);
      setPreview(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo guardar la persona.");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div className="flex justify-center py-16">
        <Spinner className="h-6 w-6 text-slate-400" />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Cómo te conoce Edecan"
        description="Define quién es tu asistente: nombre, tono, formalidad, instrucciones permanentes y memoria."
      />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Configuración" />
          <CardBody>
            {error && (
              <div className="mb-4">
                <Alert variant="error">{error}</Alert>
              </div>
            )}
            {saved && (
              <div className="mb-4">
                <Alert variant="success">Persona guardada.</Alert>
              </div>
            )}
            <form className="space-y-4" onSubmit={handleSubmit}>
              <Field label="Nombre del asistente" htmlFor="nombre_asistente">
                <Input
                  id="nombre_asistente"
                  value={persona.nombre_asistente}
                  onChange={(e) => setPersona({ ...persona, nombre_asistente: e.target.value })}
                  maxLength={80}
                />
              </Field>

              <Field label="Idioma" htmlFor="idioma">
                <Select
                  id="idioma"
                  value={persona.idioma}
                  onChange={(e) => setPersona({ ...persona, idioma: e.target.value })}
                >
                  <option value="es">Español</option>
                  <option value="en">English</option>
                </Select>
              </Field>

              <Field label="Tono" htmlFor="tono" hint="Descripción libre del carácter de las respuestas.">
                <Input
                  id="tono"
                  value={persona.tono}
                  onChange={(e) => setPersona({ ...persona, tono: e.target.value })}
                  placeholder="cálido y profesional"
                />
              </Field>

              <Field label="Formalidad" htmlFor="formalidad">
                <input
                  id="formalidad"
                  type="range"
                  min={0}
                  max={3}
                  step={1}
                  value={persona.formalidad}
                  onChange={(e) =>
                    setPersona({ ...persona, formalidad: Number(e.target.value) as PersonaConfig["formalidad"] })
                  }
                  className="w-full accent-brand-600"
                />
                <div className="mt-1 flex justify-between text-[11px] text-slate-400">
                  <span>0 · tú informal</span>
                  <span>1 · tú</span>
                  <span>2 · usted</span>
                  <span>3 · usted formal</span>
                </div>
                <p className="mt-1.5 text-xs text-slate-500 dark:text-slate-400">
                  {FORMALIDAD_LABELS[persona.formalidad]}
                </p>
              </Field>

              <Field
                label="Rasgos de personalidad"
                htmlFor="rasgos"
                hint="Escribe un rasgo y pulsa Enter o «Añadir»."
              >
                <RasgosEditor rasgos={persona.rasgos} onChange={(rasgos) => setPersona({ ...persona, rasgos })} />
              </Field>

              <Field
                label="Instrucciones permanentes"
                htmlFor="instrucciones"
                hint="Reglas de negocio o contexto fijo. Nunca pueden anular las reglas de seguridad del sistema."
              >
                <Textarea
                  id="instrucciones"
                  value={persona.instrucciones}
                  onChange={(e) => setPersona({ ...persona, instrucciones: e.target.value })}
                  rows={5}
                />
              </Field>

              <Field label="Voz (voice_id, opcional)" htmlFor="voice_id" hint="ElevenLabs/Polly. Vacío = voz por defecto de la plataforma.">
                <Input
                  id="voice_id"
                  value={persona.voice_id ?? ""}
                  onChange={(e) => setPersona({ ...persona, voice_id: e.target.value || null })}
                />
              </Field>

              <div className="flex flex-col gap-3 rounded-xl border border-slate-100 p-3 dark:border-slate-800">
                <Switch
                  id="emojis"
                  label="Permitir emojis en las respuestas"
                  checked={persona.emojis}
                  onChange={(checked) => setPersona({ ...persona, emojis: checked })}
                />
                <Switch
                  id="memoria_activada"
                  label="Memoria de largo plazo activada"
                  checked={persona.memoria_activada}
                  onChange={(checked) => setPersona({ ...persona, memoria_activada: checked })}
                />
              </div>

              <div className="flex gap-2 pt-2">
                <Button type="submit" loading={saving}>
                  Guardar
                </Button>
                <Button type="button" variant="secondary" onClick={loadPreview} loading={previewLoading}>
                  Ver system prompt
                </Button>
              </div>
            </form>
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Vista previa del system prompt"
            description="Exactamente lo que usará el agente (GET /v1/persona/preview), sin gastar un turno."
          />
          <CardBody>
            {preview ? (
              <pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-xs text-slate-700 dark:bg-slate-950 dark:text-slate-300">
                {preview}
              </pre>
            ) : (
              <p className="text-sm text-slate-400">
                Pulsa &quot;Ver system prompt&quot; para generar la vista previa con la configuración guardada.
              </p>
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
