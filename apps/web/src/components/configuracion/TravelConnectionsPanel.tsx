"use client";

import { useCallback, useEffect, useState } from "react";

import { ConectarAfterShip } from "@/components/viajes/ConectarAfterShip";
import { ConectarAmadeus } from "@/components/viajes/ConectarAmadeus";
import { Alert, Badge, Card, CardBody, CardHeader, Spinner } from "@/components/ui";
import {
  ApiError,
  deleteRastreoCredentials,
  deleteViajesCredentials,
  getViajesStatus,
  type ViajesStatus,
} from "@/lib/api-viajes";

function EstadoBadge({ configurado }: { configurado: boolean }) {
  return <Badge variant={configurado ? "success" : "neutral"}>{configurado ? "Conectado" : "Falta conectar"}</Badge>;
}

function FilaConectada({
  children,
  onQuitar,
  removing,
}: {
  children: React.ReactNode;
  onQuitar: () => void;
  removing: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-slate-50 px-3 py-2 text-sm dark:bg-slate-950/40">
      <span className="text-slate-700 dark:text-slate-200">{children}</span>
      <button
        type="button"
        onClick={onQuitar}
        disabled={removing}
        className="text-xs font-medium text-rose-600 hover:text-rose-700 disabled:opacity-50 dark:text-rose-400"
      >
        {removing ? "Quitando…" : "Quitar"}
      </button>
    </div>
  );
}

export function TravelConnectionsPanel() {
  const [status, setStatus] = useState<ViajesStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [removing, setRemoving] = useState<"amadeus" | "aftership" | null>(null);

  const cargarStatus = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setStatus(await getViajesStatus());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo cargar el estado de conexión.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void cargarStatus();
  }, [cargarStatus]);

  async function desconectarAmadeus() {
    setRemoving("amadeus");
    setError(null);
    let removeError: string | null = null;
    try {
      await deleteViajesCredentials();
    } catch (err) {
      removeError = err instanceof ApiError ? err.message : "No se pudo desconectar Amadeus.";
    } finally {
      await cargarStatus();
      if (removeError) setError(removeError);
      setRemoving(null);
    }
  }

  async function desconectarAfterShip() {
    setRemoving("aftership");
    setError(null);
    let removeError: string | null = null;
    try {
      await deleteRastreoCredentials();
    } catch (err) {
      removeError = err instanceof ApiError ? err.message : "No se pudo desconectar AfterShip.";
    } finally {
      await cargarStatus();
      if (removeError) setError(removeError);
      setRemoving(null);
    }
  }

  return (
    <div>
      {error && <div className="mb-4"><Alert variant="error">{error}</Alert></div>}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader
            title="Amadeus (vuelos y hoteles)"
            actions={loading ? <Spinner className="h-4 w-4 text-slate-400" /> : <EstadoBadge configurado={status?.travel.configured ?? false} />}
          />
          <CardBody className="space-y-3">
            {status?.travel.configured && (
              <FilaConectada
                onQuitar={() => void desconectarAmadeus()}
                removing={removing === "amadeus"}
              >
                Entorno: {status.travel.environment === "production" ? "Production" : "Test"}
              </FilaConectada>
            )}
            <ConectarAmadeus onConnected={cargarStatus} />
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="AfterShip (rastreo de paquetes)"
            actions={loading ? <Spinner className="h-4 w-4 text-slate-400" /> : <EstadoBadge configurado={status?.tracking.configured ?? false} />}
          />
          <CardBody className="space-y-3">
            {status?.tracking.configured && (
              <FilaConectada
                onQuitar={() => void desconectarAfterShip()}
                removing={removing === "aftership"}
              >
                Conectado
              </FilaConectada>
            )}
            <ConectarAfterShip onConnected={cargarStatus} />
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
