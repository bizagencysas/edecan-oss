"use client";

/**
 * Lista de mensajes recientes del canal seleccionado (`/app/mensajes`, WP-V4-11):
 * `origen` (chat/canal) + botón «Cargar»/«Actualizar», refrescable a mano — sin auto-polling
 * (mismo criterio simple que pide el paquete de trabajo). WhatsApp nunca intenta leer
 * (`puedeLeer=false`, ver `docs/mensajeria.md` "Limitación de lectura en v3"): se lo explica
 * al usuario en vez de dejarlo pedir un `400` predecible.
 *
 * `formatFecha` es un *best-effort* LOCAL de formateo por canal (Telegram: epoch en
 * segundos; Discord: ISO 8601; Slack: "segundos.microsegundos") — la API nunca normaliza
 * `fecha` (ver el router), así que cualquier interpretación vive aquí, con el valor crudo
 * como respaldo si no se puede parsear.
 */

import { useEffect, useState, type FormEvent } from "react";

import { Alert, Button, Field, Input, Spinner } from "@/components/ui";
import { ApiError, listMensajes, type CanalMensajeria, type MensajeItem } from "@/lib/api-mensajes";

const ORIGEN_HINT: Record<CanalMensajeria, string> = {
  telegram: "chat_id (opcional — vacío usa los últimos updates pendientes del bot)",
  discord: "id del canal de Discord (obligatorio)",
  slack: "id o #nombre del canal de Slack (obligatorio)",
  whatsapp: "",
};

function describeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Ocurrió un error inesperado.";
}

function formatFecha(canal: CanalMensajeria, fecha: string): string {
  if (!fecha) return "—";
  const formateador = new Intl.DateTimeFormat("es", { dateStyle: "medium", timeStyle: "short" });
  try {
    if (canal === "telegram") {
      const segundos = Number(fecha);
      return Number.isFinite(segundos) ? formateador.format(new Date(segundos * 1000)) : fecha;
    }
    if (canal === "slack") {
      const segundos = Number(fecha.split(".")[0]);
      return Number.isFinite(segundos) ? formateador.format(new Date(segundos * 1000)) : fecha;
    }
    const d = new Date(fecha); // discord: ISO 8601 tal cual
    return Number.isNaN(d.getTime()) ? fecha : formateador.format(d);
  } catch {
    return fecha;
  }
}

export function MensajesList({
  canal,
  conectado,
  puedeLeer,
}: {
  canal: CanalMensajeria;
  conectado: boolean;
  puedeLeer: boolean;
}) {
  const [origen, setOrigen] = useState("");
  const [mensajes, setMensajes] = useState<MensajeItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cargado, setCargado] = useState(false);

  // Cambiar de canal reinicia el estado propio de la lista — no tiene sentido mostrar
  // mensajes de Telegram mientras el usuario mira el panel de Slack.
  useEffect(() => {
    setOrigen("");
    setMensajes([]);
    setError(null);
    setCargado(false);
  }, [canal]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (canal !== "telegram" && !origen.trim()) {
      setError(`Falta el origen: ${ORIGEN_HINT[canal]}`);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const items = await listMensajes({ canal, origen: origen.trim() || undefined });
      setMensajes(items);
      setCargado(true);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setLoading(false);
    }
  }

  if (!conectado) {
    return (
      <p className="text-sm text-slate-500 dark:text-slate-400">
        Conecta este canal para ver sus mensajes recientes.
      </p>
    );
  }

  if (!puedeLeer) {
    return (
      <Alert variant="info">
        WhatsApp no soporta lectura de mensajes en Edecán: la Cloud API de Meta solo entrega
        mensajes entrantes por webhook. Puedes seguir enviando por WhatsApp normalmente.
      </Alert>
    );
  }

  return (
    <div className="space-y-3">
      <form onSubmit={handleSubmit}>
        <Field label="Origen" htmlFor="mensajes-origen" hint={ORIGEN_HINT[canal]}>
          <div className="flex gap-2">
            <Input
              id="mensajes-origen"
              value={origen}
              onChange={(e) => setOrigen(e.target.value)}
              placeholder={canal === "telegram" ? "opcional" : "obligatorio"}
              className="flex-1"
            />
            <Button type="submit" variant="secondary" loading={loading}>
              {cargado ? "Actualizar" : "Cargar"}
            </Button>
          </div>
        </Field>
      </form>

      {error && <Alert variant="error">{error}</Alert>}

      {loading ? (
        <div className="flex justify-center py-6">
          <Spinner className="h-5 w-5 text-slate-400" />
        </div>
      ) : cargado && mensajes.length === 0 ? (
        <p className="text-sm text-slate-500 dark:text-slate-400">Sin mensajes recientes.</p>
      ) : mensajes.length > 0 ? (
        <ul className="max-h-96 space-y-2 overflow-y-auto">
          {mensajes.map((m, i) => (
            <li
              key={`${m.chat_id}-${i}`}
              className="rounded-lg border border-slate-100 px-3 py-2 dark:border-slate-800"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm font-medium text-slate-700 dark:text-slate-200">
                  {m.remitente}
                </span>
                <span className="text-xs text-slate-400">{formatFecha(canal, m.fecha)}</span>
              </div>
              <p className="mt-1 whitespace-pre-wrap text-sm text-slate-600 dark:text-slate-300">
                {m.texto || "(sin texto)"}
              </p>
              <p className="mt-1 font-mono text-xs text-slate-400">chat/canal: {m.chat_id || "—"}</p>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
