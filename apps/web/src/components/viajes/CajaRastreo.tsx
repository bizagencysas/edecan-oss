"use client";

/**
 * Caja de rastreo de paquetes: número de guía (+ courier opcional) → timeline de
 * checkpoints (`GET /v1/viajes/rastreo/{numero}`, AfterShip bring-your-own,
 * `ARCHITECTURE.md` §14, `docs/viajes.md`).
 */

import { useState } from "react";

import { Alert, Badge, Button, Card, CardBody, CardHeader, Field, Input, Spinner } from "@/components/ui";
import { ApiError, rastrearPaquete, type RastreoPaqueteOut } from "@/lib/api-viajes";
import { formatDateTime } from "@/lib/format";

function mensajeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "No se pudo rastrear el paquete.";
}

function fechaLegible(fecha: string | null): string {
  return fecha ? formatDateTime(fecha) : "(sin fecha)";
}

export function CajaRastreo() {
  const [numero, setNumero] = useState("");
  const [courierSlug, setCourierSlug] = useState("");
  const [buscando, setBuscando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rastreo, setRastreo] = useState<RastreoPaqueteOut | null>(null);

  async function rastrear() {
    setBuscando(true);
    setError(null);
    setRastreo(null);
    try {
      const resultado = await rastrearPaquete(numero.trim(), courierSlug.trim() || undefined);
      setRastreo(resultado);
    } catch (err) {
      setError(mensajeError(err));
    } finally {
      setBuscando(false);
    }
  }

  const puedeRastrear = numero.trim().length > 0;

  return (
    <Card>
      <CardHeader
        title="Rastrear un paquete"
        description="Número de guía — la empresa de envío se detecta automáticamente si la dejas en blanco."
      />
      <CardBody className="space-y-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Field label="Número de guía" htmlFor="rastreo_numero" className="sm:col-span-2">
            <Input
              id="rastreo_numero"
              value={numero}
              onChange={(e) => setNumero(e.target.value)}
              placeholder="1234567890"
              disabled={buscando}
            />
          </Field>
          <Field label="Courier (opcional)" htmlFor="rastreo_courier" hint="p. ej. 'dhl', 'fedex', 'ups'.">
            <Input
              id="rastreo_courier"
              value={courierSlug}
              onChange={(e) => setCourierSlug(e.target.value)}
              placeholder="Auto-detectar"
              disabled={buscando}
            />
          </Field>
        </div>
        <Button size="sm" onClick={() => void rastrear()} loading={buscando} disabled={!puedeRastrear}>
          Rastrear
        </Button>

        {error && <Alert variant="error">{error}</Alert>}
        {buscando && (
          <div className="flex justify-center py-6">
            <Spinner className="h-5 w-5 text-slate-400" />
          </div>
        )}
        {rastreo && (
          <div className="space-y-3 border-t border-slate-100 pt-4 dark:border-slate-800">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="brand">{rastreo.estado}</Badge>
              {rastreo.courier && (
                <span className="text-xs text-slate-500 dark:text-slate-400">
                  Empresa: {rastreo.courier}
                </span>
              )}
              {rastreo.entrega_estimada && (
                <span className="text-xs text-slate-500 dark:text-slate-400">
                  Entrega estimada: {rastreo.entrega_estimada}
                </span>
              )}
            </div>
            {rastreo.checkpoints.length === 0 ? (
              <p className="text-sm text-slate-500 dark:text-slate-400">
                Todavía no hay checkpoints registrados para este envío.
              </p>
            ) : (
              <ol className="space-y-3 border-l-2 border-slate-200 pl-4 dark:border-slate-700">
                {rastreo.checkpoints.map((cp, i) => (
                  <li key={i} className="relative">
                    <span className="absolute -left-[1.35rem] top-1 h-2 w-2 rounded-full bg-brand-500" />
                    <p className="text-xs text-slate-400">{fechaLegible(cp.fecha)}</p>
                    <p className="text-sm text-slate-700 dark:text-slate-200">{cp.mensaje}</p>
                    {cp.lugar && <p className="text-xs text-slate-500 dark:text-slate-400">{cp.lugar}</p>}
                  </li>
                ))}
              </ol>
            )}
          </div>
        )}
      </CardBody>
    </Card>
  );
}
