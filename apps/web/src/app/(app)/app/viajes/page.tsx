"use client";

/**
 * `/app/viajes` — Vuelos/hoteles reales vía Amadeus (bring-your-own) + rastreo de
 * paquetes vía AfterShip (`ARCHITECTURE.md` §14, WP-V5-09; ver `docs/viajes.md`).
 *
 * Nota de navegación: `components/layout/nav-items.ts` está fuera de las rutas que
 * este paquete de trabajo puede tocar — el enlace del menú lateral lo agrega el
 * linchpin de v5 (WP-V5-01), mismo criterio que dejaron WP-V2-01/WP-V4-01 para sus
 * propias páginas nuevas ("un enlace puede dar 404 hasta entonces, es esperado").
 * Esta página funciona igual navegando directo a `/app/viajes`.
 */

import { useCallback, useEffect, useState } from "react";

import { Alert, Badge, Card, CardBody, CardHeader, PageHeader, Spinner } from "@/components/ui";
import { BuscadorHoteles } from "@/components/viajes/BuscadorHoteles";
import { BuscadorVuelos } from "@/components/viajes/BuscadorVuelos";
import { CajaRastreo } from "@/components/viajes/CajaRastreo";
import { ConectarAfterShip } from "@/components/viajes/ConectarAfterShip";
import { ConectarAmadeus } from "@/components/viajes/ConectarAmadeus";
import {
  ApiError,
  deleteRastreoCredentials,
  deleteViajesCredentials,
  getViajesStatus,
  type ViajesStatus,
} from "@/lib/api-viajes";

function EstadoBadge({ configurado }: { configurado: boolean }) {
  return (
    <Badge variant={configurado ? "success" : "neutral"}>
      {configurado ? "Conectado" : "Sin conectar"}
    </Badge>
  );
}

function FilaConectada({ children, onQuitar }: { children: React.ReactNode; onQuitar: () => void }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-slate-50 px-3 py-2 text-sm dark:bg-slate-950/40">
      <span className="text-slate-700 dark:text-slate-200">{children}</span>
      <button
        type="button"
        onClick={onQuitar}
        className="text-xs font-medium text-rose-600 hover:text-rose-700 dark:text-rose-400"
      >
        Quitar
      </button>
    </div>
  );
}

export default function ViajesPage() {
  const [status, setStatus] = useState<ViajesStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const cargarStatus = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const s = await getViajesStatus();
      setStatus(s);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "No se pudo cargar el estado de conexión.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void cargarStatus();
  }, [cargarStatus]);

  async function desconectarAmadeus() {
    try {
      await deleteViajesCredentials();
    } finally {
      await cargarStatus();
    }
  }

  async function desconectarAfterShip() {
    try {
      await deleteRastreoCredentials();
    } finally {
      await cargarStatus();
    }
  }

  return (
    <div>
      <PageHeader
        title="Viajes"
        description="Busca vuelos y hoteles reales con tu propia cuenta de Amadeus, y rastrea paquetes con AfterShip. Nunca reserva ni paga nada por su cuenta — cualquier borrador queda pendiente de tu confirmación."
      />

      {error && (
        <div className="mb-4">
          <Alert variant="error">{error}</Alert>
        </div>
      )}

      <div className="mb-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader
            title="Amadeus (vuelos y hoteles)"
            actions={
              loading ? (
                <Spinner className="h-4 w-4 text-slate-400" />
              ) : (
                <EstadoBadge configurado={status?.travel.configured ?? false} />
              )
            }
          />
          <CardBody className="space-y-3">
            {status?.travel.configured && (
              <FilaConectada onQuitar={() => void desconectarAmadeus()}>
                Entorno: {status.travel.environment === "production" ? "Production" : "Test"}
              </FilaConectada>
            )}
            <ConectarAmadeus onConnected={cargarStatus} />
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="AfterShip (rastreo de paquetes)"
            actions={
              loading ? (
                <Spinner className="h-4 w-4 text-slate-400" />
              ) : (
                <EstadoBadge configurado={status?.tracking.configured ?? false} />
              )
            }
          />
          <CardBody className="space-y-3">
            {status?.tracking.configured && (
              <FilaConectada onQuitar={() => void desconectarAfterShip()}>Conectado</FilaConectada>
            )}
            <ConectarAfterShip onConnected={cargarStatus} />
          </CardBody>
        </Card>
      </div>

      <div className="space-y-6">
        <BuscadorVuelos />
        <BuscadorHoteles />
        <CajaRastreo />
      </div>
    </div>
  );
}
