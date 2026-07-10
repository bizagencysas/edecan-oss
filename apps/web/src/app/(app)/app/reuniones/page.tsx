"use client";

/**
 * `/app/reuniones` — Reuniones: transcripción con el STT del tenant + minutas
 * por LLM del tenant (`ARCHITECTURE.md` §15, WP-V6-05; ver `docs/reuniones.md`).
 *
 * Nota de navegación: `components/layout/nav-items.ts` está fuera de las
 * rutas que este paquete de trabajo puede tocar — el enlace del menú lateral
 * lo agrega el linchpin de v6 (WP-V6-01), mismo criterio que dejaron
 * WP-V2-01/WP-V4-01/WP-V5-01 para sus propias páginas nuevas ("un enlace
 * puede dar 404 hasta entonces, es esperado"). Esta página funciona igual
 * navegando directo a `/app/reuniones`.
 *
 * Polling: mientras alguna reunión esté `pending`/`running`, se refresca la
 * lista cada `POLL_INTERVAL_MS` — se detiene solo cuando TODAS llegan a un
 * estado terminal (`done`/`error`), para no dejar un `setInterval` corriendo
 * para siempre en una pestaña abierta sin trabajo pendiente.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { DetalleReunion } from "@/components/reuniones/DetalleReunion";
import { FormularioReunion } from "@/components/reuniones/FormularioReunion";
import { ListaReuniones } from "@/components/reuniones/ListaReuniones";
import { Alert, Card, CardBody, CardHeader, EmptyState, PageHeader, Spinner } from "@/components/ui";
import {
  ApiError,
  borrarReunion,
  listarReuniones,
  type ReunionOut,
} from "@/lib/api-reuniones";

const POLL_INTERVAL_MS = 5000;

function hayTrabajoPendiente(reuniones: ReunionOut[]): boolean {
  return reuniones.some((r) => r.status === "pending" || r.status === "running");
}

export default function ReunionesPage() {
  const [reuniones, setReuniones] = useState<ReunionOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const cargar = useCallback(async (opts: { silencioso?: boolean } = {}) => {
    if (!opts.silencioso) setLoading(true);
    try {
      const lista = await listarReuniones();
      setReuniones(lista);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudieron cargar las reuniones.");
    } finally {
      if (!opts.silencioso) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  useEffect(() => {
    if (hayTrabajoPendiente(reuniones)) {
      if (pollRef.current === null) {
        pollRef.current = setInterval(() => void cargar({ silencioso: true }), POLL_INTERVAL_MS);
      }
    } else if (pollRef.current !== null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    return () => {
      if (pollRef.current !== null) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [reuniones, cargar]);

  function onCreated(reunion: ReunionOut) {
    setReuniones((prev) => [reunion, ...prev]);
    setSelectedId(reunion.id);
  }

  async function onDelete(id: string) {
    const previas = reuniones;
    setReuniones((prev) => prev.filter((r) => r.id !== id));
    if (selectedId === id) setSelectedId(null);
    try {
      await borrarReunion(id);
    } catch (err) {
      setReuniones(previas);
      setError(err instanceof ApiError ? err.message : "No se pudo borrar la reunión.");
    }
  }

  const reunionSeleccionada = reuniones.find((r) => r.id === selectedId) ?? null;

  return (
    <div>
      <PageHeader
        title="Reuniones"
        description="Transcribe grabaciones con el STT de tu cuenta y genera minutas (resumen, decisiones, acciones, temas) con tu modelo de lenguaje."
      />

      {error && (
        <div className="mb-4">
          <Alert variant="error">{error}</Alert>
        </div>
      )}

      <div className="mb-6">
        <Card>
          <CardHeader title="Nueva reunión" />
          <CardBody>
            <FormularioReunion onCreated={onCreated} />
          </CardBody>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Tus reuniones" />
          <CardBody>
            {loading ? (
              <div className="flex justify-center py-8">
                <Spinner className="h-5 w-5 text-slate-400" />
              </div>
            ) : reuniones.length === 0 ? (
              <EmptyState
                title="Sin reuniones todavía"
                description="Elegí un audio o video arriba para transcribirlo y generar minutas."
              />
            ) : (
              <ListaReuniones
                reuniones={reuniones}
                selectedId={selectedId}
                onSelect={setSelectedId}
                onDelete={(id) => void onDelete(id)}
              />
            )}
          </CardBody>
        </Card>

        <DetalleReunion reunion={reunionSeleccionada} />
      </div>
    </div>
  );
}
