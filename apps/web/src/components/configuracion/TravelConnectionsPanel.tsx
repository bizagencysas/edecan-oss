"use client";

import { useCallback, useEffect, useState } from "react";

import { ConectarAfterShip } from "@/components/viajes/ConectarAfterShip";
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
            title="Vuelos y hoteles"
            actions={loading ? <Spinner className="h-4 w-4 text-slate-400" /> : <Badge variant="success">Listo</Badge>}
          />
          <CardBody className="space-y-3">
            <p className="text-sm text-slate-600 dark:text-slate-300">
              Edecán busca ofertas reales con su capa de viajes. No necesitas crear una cuenta ni
              pegar API keys, y funciona igual con Claude, Codex, Ollama o cualquier otro modelo.
            </p>
            <p className="text-xs text-slate-400">
              Fuentes actuales: Kiwi, Trivago y Skiplagged. Las ofertas incluyen enlaces para
              verificarlas y continuar directamente con el proveedor.
            </p>
            {status?.travel.configured && (
              <div className="space-y-2 border-t border-slate-200 pt-3 dark:border-slate-800">
                <p className="text-xs text-slate-400">Conexión anterior conservada por compatibilidad:</p>
                <FilaConectada
                  onQuitar={() => void desconectarAmadeus()}
                  removing={removing === "amadeus"}
                >
                  Amadeus · {status.travel.environment === "production" ? "Production" : "Test"}
                </FilaConectada>
              </div>
            )}
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
