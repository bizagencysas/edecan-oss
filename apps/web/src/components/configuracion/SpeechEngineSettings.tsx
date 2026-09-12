"use client";

/**
 * Configuración de la voz GESTIONADA por Speech Engine de ElevenLabs
 * (`docs/speech-engine.md`). Se monta en Ajustes → Conexiones, junto a la voz
 * legacy, y es EXPLÍCITA en dos sentidos:
 *
 * 1. Es un proveedor PAGADO: el aviso va primero y nada se factura sin la API
 *    key del tenant + `enabled=true` + `paid_consent` por sesión.
 * 2. La voz legacy (STT/TTS web) NO se etiqueta como "voz gestionada": son
 *    tarjetas separadas con nombres distintos.
 */

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui";
import { CampoLlave } from "@/components/configuracion/CampoLlave";
import {
  ApiError,
  conectarSpeechEngine,
  desconectarSpeechEngine,
  getCredencialSpeechEngine,
  getPreferenciasVozGestionada,
  putPreferenciasVozGestionada,
  type PreferenciasVozGestionada,
  type PutPreferenciasVozGestionada,
} from "@/lib/api-speech-engine";

const ESFUERZOS = [
  { valor: "", etiqueta: "Automático" },
  { valor: "bajo", etiqueta: "Bajo" },
  { valor: "medio", etiqueta: "Medio" },
  { valor: "alto", etiqueta: "Alto" },
];

export function SpeechEngineSettings({ onChanged }: { onChanged?: () => void }) {
  const [credencial, setCredencial] = useState<{ provider: string; masked: string | null } | null>(null);
  const [preferencias, setPreferencias] = useState<PreferenciasVozGestionada | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [cargando, setCargando] = useState(true);
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Preferencias (formulario local; el servidor es la autoridad).
  const [enabled, setEnabled] = useState(false);
  const [voiceModelId, setVoiceModelId] = useState<string | null>(null);
  const [delegationModelId, setDelegationModelId] = useState<string | null>(null);
  const [delegationEffort, setDelegationEffort] = useState<string>("");
  const [voiceId, setVoiceId] = useState<string | null>(null);
  const [ttsModelId, setTtsModelId] = useState<string | null>(null);
  const [maxDurationSeconds, setMaxDurationSeconds] = useState(900);

  const cargar = useCallback(async () => {
    setCargando(true);
    setError(null);
    try {
      const [credencial, preferencias] = await Promise.all([
        getCredencialSpeechEngine(),
        getPreferenciasVozGestionada(),
      ]);
      setCredencial(credencial);
      setPreferencias(preferencias);
      setEnabled(preferencias.preference?.enabled ?? false);
      setVoiceModelId(preferencias.preference?.voice_model_id ?? null);
      setDelegationModelId(preferencias.preference?.delegation_model_id ?? null);
      setDelegationEffort(preferencias.preference?.delegation_effort ?? "");
      setVoiceId(preferencias.preference?.voice_id ?? null);
      setTtsModelId(preferencias.preference?.tts_model_id ?? null);
      setMaxDurationSeconds(
        preferencias.preference?.max_duration_seconds ??
          preferencias.defaults.max_duration_seconds,
      );
    } catch {
      setError("No se pudieron cargar las preferencias de voz gestionada.");
    } finally {
      setCargando(false);
    }
  }, []);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  const guardar = useCallback(async () => {
    if (!preferencias) return;
    setGuardando(true);
    setError(null);
    const body: PutPreferenciasVozGestionada = {
      enabled,
      provider: "elevenlabs",
      voice_model_id: voiceModelId,
      delegation_model_id: delegationModelId,
      delegation_effort: delegationEffort || null,
      voice_id: voiceId,
      tts_model_id: ttsModelId,
      max_duration_seconds: maxDurationSeconds,
    };
    try {
      await putPreferenciasVozGestionada(body);
      await cargar();
      onChanged?.();
    } catch (fallo) {
      setError(
        fallo instanceof ApiError ? fallo.message : "No se pudieron guardar las preferencias.",
      );
    } finally {
      setGuardando(false);
    }
  }, [
    enabled,
    voiceModelId,
    delegationModelId,
    delegationEffort,
    voiceId,
    ttsModelId,
    maxDurationSeconds,
    preferencias,
    cargar,
    onChanged,
  ]);

  const conectar = useCallback(async () => {
    setGuardando(true);
    setError(null);
    try {
      await conectarSpeechEngine(apiKey);
      setApiKey("");
      await cargar();
      onChanged?.();
    } catch (fallo) {
      setError(fallo instanceof ApiError ? fallo.message : "No se pudo conectar la API key.");
    } finally {
      setGuardando(false);
    }
  }, [apiKey, cargar, onChanged]);

  const quitar = useCallback(async () => {
    setGuardando(true);
    setError(null);
    try {
      await desconectarSpeechEngine();
      await cargar();
      onChanged?.();
    } catch (fallo) {
      setError(fallo instanceof ApiError ? fallo.message : "No se pudo quitar la credencial.");
    } finally {
      setGuardando(false);
    }
  }, [cargar, onChanged]);

  if (cargando) {
    return <p className="px-4 py-3 text-sm text-slate-500">Cargando…</p>;
  }

  return (
    <div className="space-y-4">
      <p className="text-xs text-amber-700 dark:text-amber-300">
        Proveedor PAGADO: los minutos de Speech Engine se facturan a tu API key de
        ElevenLabs. Conectar la key NO activa nada — la activación y el consentimiento
        son explícitos.
      </p>

      <CampoLlave
        id="speech-engine-api-key"
        label="API key de ElevenLabs"
        value={apiKey}
        onChange={setApiKey}
        placeholder="sk_…"
        linkHref="https://elevenlabs.io/app/settings/api-keys"
        hint={
          credencial
            ? `Conectada: ${credencial.provider}${credencial.masked ? ` · ${credencial.masked}` : ""}`
            : "Aún no hay credencial conectada."
        }
        disabled={guardando}
      />
      <div className="flex gap-2">
        <Button type="button" onClick={() => void conectar()} disabled={!apiKey.trim() || guardando}>
          Conectar
        </Button>
        {credencial && (
          <Button type="button" variant="secondary" onClick={() => void quitar()} disabled={guardando}>
            Quitar credencial
          </Button>
        )}
      </div>

      <div className="border-t border-slate-200 pt-3 dark:border-slate-800">
        <label className="flex items-center gap-2 text-sm font-medium text-slate-700 dark:text-slate-200">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(evento) => setEnabled(evento.target.checked)}
            disabled={!credencial}
          />
          Activar voz gestionada en el chat
        </label>
        {!credencial && (
          <p className="mt-1 text-xs text-slate-500">
            Conecta la API key de arriba para poder activarla.
          </p>
        )}

        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label className="block text-sm">
            <span className="text-xs font-medium text-slate-500">Interlocutor de voz</span>
            <select
              className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900"
              value={voiceModelId ?? ""}
              onChange={(evento) => setVoiceModelId(evento.target.value || null)}
            >
              {preferencias?.catalogs.voice_models.map((modelo) => (
                <option key={modelo.id} value={modelo.id}>
                  {modelo.nombre}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-sm">
            <span className="text-xs font-medium text-slate-500">
              Delegación (trabajo real)
            </span>
            <select
              className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900"
              value={delegationModelId ?? ""}
              onChange={(evento) => setDelegationModelId(evento.target.value || null)}
            >
              <option value="">Heredar del chat</option>
              {preferencias?.catalogs.voice_models.map((modelo) => (
                <option key={modelo.id} value={modelo.id}>
                  {modelo.nombre}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-sm">
            <span className="text-xs font-medium text-slate-500">Esfuerzo de delegación</span>
            <select
              className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900"
              value={delegationEffort}
              onChange={(evento) => setDelegationEffort(evento.target.value)}
            >
              {ESFUERZOS.map((esfuerzo) => (
                <option key={esfuerzo.valor} value={esfuerzo.valor}>
                  {esfuerzo.etiqueta}
                </option>
              ))}
            </select>
          </label>
          {preferencias && preferencias.catalogs.tts_voices.length > 0 && (
            <label className="block text-sm">
              <span className="text-xs font-medium text-slate-500">Voz</span>
              <select
                className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900"
                value={voiceId ?? ""}
                onChange={(evento) => setVoiceId(evento.target.value || null)}
              >
                {preferencias.catalogs.tts_voices.map((voz) => (
                  <option key={voz.id} value={voz.id}>
                    {voz.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          {preferencias && preferencias.catalogs.tts_models.length > 0 && (
            <label className="block text-sm">
              <span className="text-xs font-medium text-slate-500">Modelo TTS</span>
              <select
                className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900"
                value={ttsModelId ?? ""}
                onChange={(evento) => setTtsModelId(evento.target.value || null)}
              >
                {preferencias.catalogs.tts_models.map((modelo) => (
                  <option key={modelo.id} value={modelo.id}>
                    {modelo.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="block text-sm">
            <span className="text-xs font-medium text-slate-500">
              Duración máxima: {maxDurationSeconds / 60} min
            </span>
            <input
              type="range"
              min={60}
              max={3600}
              step={60}
              value={maxDurationSeconds}
              onChange={(evento) => setMaxDurationSeconds(Number(evento.target.value))}
              className="mt-2 w-full"
            />
          </label>
        </div>
        <Button type="button" className="mt-3" onClick={() => void guardar()} disabled={guardando}>
          Guardar preferencias
        </Button>
        {error && <p className="mt-2 text-xs text-rose-600 dark:text-rose-400">{error}</p>}
      </div>
    </div>
  );
}