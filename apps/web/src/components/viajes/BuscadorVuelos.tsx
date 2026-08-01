"use client";

/**
 * Buscador de vuelos: formulario (origen/destino/fecha/adultos) + resultados en
 * tarjetas, cada una con `GuardarBorradorBoton` (`ARCHITECTURE.md` §14,
 * `docs/viajes.md`). Solo lectura contra `GET /v1/viajes/buscar/vuelos` — nunca
 * reserva ni paga nada.
 */

import { useState } from "react";

import { Alert, Button, Card, CardBody, CardHeader, Field, Input, Spinner } from "@/components/ui";
import { ApiError, buscarVuelos, type VueloOferta } from "@/lib/api-viajes";
import { formatMoney } from "@/lib/format";

import { GuardarBorradorBoton } from "./GuardarBorradorBoton";

function mensajeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "No se pudo buscar vuelos.";
}

function descripcionOferta(o: VueloOferta, origen: string, destino: string): string {
  return `Vuelo ${o.aerolinea} ${o.origen ?? origen} → ${o.destino ?? destino}, salida ${o.salida ?? "?"}`;
}

function TarjetaVuelo({ oferta, origen, destino }: { oferta: VueloOferta; origen: string; destino: string }) {
  return (
    <Card>
      <CardBody className="space-y-2">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <p className="text-sm font-semibold text-slate-900 dark:text-slate-100">
              {oferta.aerolinea}
            </p>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              {oferta.origen ?? origen} {oferta.salida ?? "?"} → {oferta.destino ?? destino}{" "}
              {oferta.llegada ?? "?"}
            </p>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              {oferta.escalas === 0 ? "Directo" : `${oferta.escalas} escala(s)`}
            </p>
          </div>
          <p className="text-sm font-semibold text-slate-900 dark:text-slate-100">
            {formatMoney(oferta.precio_total, oferta.moneda)}
          </p>
        </div>
        <GuardarBorradorBoton
          oferta={{
            tipo: "vuelo",
            descripcion: descripcionOferta(oferta, origen, destino),
            monto: Number(oferta.precio_total) || 0,
            moneda: oferta.moneda || "USD",
            ofertaId: oferta.id,
          }}
        />
      </CardBody>
    </Card>
  );
}

export function BuscadorVuelos() {
  const [origen, setOrigen] = useState("");
  const [destino, setDestino] = useState("");
  const [fecha, setFecha] = useState("");
  const [adultos, setAdultos] = useState(1);
  const [buscando, setBuscando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ofertas, setOfertas] = useState<VueloOferta[] | null>(null);
  const [buscados, setBuscados] = useState<{ origen: string; destino: string } | null>(null);

  async function buscar() {
    setBuscando(true);
    setError(null);
    try {
      const resultado = await buscarVuelos({
        origen: origen.trim().toUpperCase(),
        destino: destino.trim().toUpperCase(),
        fecha,
        adultos,
      });
      setOfertas(resultado.ofertas);
      setBuscados({ origen: origen.trim().toUpperCase(), destino: destino.trim().toUpperCase() });
    } catch (err) {
      setError(mensajeError(err));
      setOfertas(null);
    } finally {
      setBuscando(false);
    }
  }

  const puedeBuscar = origen.trim().length > 0 && destino.trim().length > 0 && fecha.length > 0;

  return (
    <Card>
      <CardHeader title="Buscar vuelos" description="Origen, destino y fecha — código IATA de 3 letras." />
      <CardBody className="space-y-4">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Field label="Origen" htmlFor="vuelos_origen">
            <Input
              id="vuelos_origen"
              value={origen}
              onChange={(e) => setOrigen(e.target.value)}
              placeholder="BOG"
              maxLength={3}
              disabled={buscando}
            />
          </Field>
          <Field label="Destino" htmlFor="vuelos_destino">
            <Input
              id="vuelos_destino"
              value={destino}
              onChange={(e) => setDestino(e.target.value)}
              placeholder="MIA"
              maxLength={3}
              disabled={buscando}
            />
          </Field>
          <Field label="Fecha" htmlFor="vuelos_fecha">
            <Input
              id="vuelos_fecha"
              type="date"
              value={fecha}
              onChange={(e) => setFecha(e.target.value)}
              disabled={buscando}
            />
          </Field>
          <Field label="Adultos" htmlFor="vuelos_adultos">
            <Input
              id="vuelos_adultos"
              type="number"
              min={1}
              max={9}
              value={adultos}
              onChange={(e) => setAdultos(Math.max(1, Number(e.target.value) || 1))}
              disabled={buscando}
            />
          </Field>
        </div>
        <Button size="sm" onClick={() => void buscar()} loading={buscando} disabled={!puedeBuscar}>
          Buscar vuelos
        </Button>

        {error && <Alert variant="error">{error}</Alert>}
        {buscando && !ofertas && (
          <div className="flex justify-center py-6">
            <Spinner className="h-5 w-5 text-slate-400" />
          </div>
        )}
        {ofertas && buscados && (
          <div className="space-y-3">
            {ofertas.length === 0 ? (
              <p className="text-sm text-slate-500 dark:text-slate-400">
                No encontré vuelos de {buscados.origen} a {buscados.destino} en esa fecha.
              </p>
            ) : (
              ofertas.map((o) => (
                <TarjetaVuelo key={o.id} oferta={o} origen={buscados.origen} destino={buscados.destino} />
              ))
            )}
          </div>
        )}
      </CardBody>
    </Card>
  );
}
