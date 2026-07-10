"use client";

/**
 * Buscador de hoteles: formulario (ciudad/checkin/checkout/adultos) + resultados en
 * tarjetas, cada una con `GuardarBorradorBoton` (`ARCHITECTURE.md` §14,
 * `docs/viajes.md`). Solo lectura contra `GET /v1/viajes/buscar/hoteles` — nunca
 * reserva ni paga nada.
 */

import { useState } from "react";

import { Alert, Button, Card, CardBody, CardHeader, Field, Input, Spinner } from "@/components/ui";
import { ApiError, buscarHoteles, type HotelOferta } from "@/lib/api-viajes";
import { formatMoney } from "@/lib/format";

import { GuardarBorradorBoton } from "./GuardarBorradorBoton";

function mensajeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "No se pudo buscar hoteles.";
}

function TarjetaHotel({
  oferta,
  checkin,
  checkout,
}: {
  oferta: HotelOferta;
  checkin: string;
  checkout: string;
}) {
  return (
    <Card>
      <CardBody className="space-y-2">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <p className="text-sm font-semibold text-slate-900 dark:text-slate-100">
              {oferta.nombre}
              {oferta.rating && (
                <span className="ml-1 text-xs font-normal text-amber-500">{oferta.rating}★</span>
              )}
            </p>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              {oferta.checkin ?? checkin} → {oferta.checkout ?? checkout}
            </p>
          </div>
          <p className="text-sm font-semibold text-slate-900 dark:text-slate-100">
            {formatMoney(oferta.precio_total, oferta.moneda)}
          </p>
        </div>
        <GuardarBorradorBoton
          oferta={{
            tipo: "hotel",
            descripcion: `${oferta.nombre} (${oferta.checkin ?? checkin} → ${oferta.checkout ?? checkout})`,
            monto: Number(oferta.precio_total) || 0,
            moneda: oferta.moneda || "USD",
            ofertaId: oferta.id,
          }}
        />
      </CardBody>
    </Card>
  );
}

export function BuscadorHoteles() {
  const [ciudad, setCiudad] = useState("");
  const [checkin, setCheckin] = useState("");
  const [checkout, setCheckout] = useState("");
  const [adultos, setAdultos] = useState(1);
  const [buscando, setBuscando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ofertas, setOfertas] = useState<HotelOferta[] | null>(null);
  const [buscados, setBuscados] = useState<{ ciudad: string } | null>(null);

  async function buscar() {
    setBuscando(true);
    setError(null);
    try {
      const resultado = await buscarHoteles({
        ciudad: ciudad.trim().toUpperCase(),
        checkin,
        checkout,
        adultos,
      });
      setOfertas(resultado.ofertas);
      setBuscados({ ciudad: ciudad.trim().toUpperCase() });
    } catch (err) {
      setError(mensajeError(err));
      setOfertas(null);
    } finally {
      setBuscando(false);
    }
  }

  const puedeBuscar = ciudad.trim().length > 0 && checkin.length > 0 && checkout.length > 0;

  return (
    <Card>
      <CardHeader
        title="Buscar hoteles"
        description="Ciudad (código IATA de 3 letras) y fechas de entrada/salida."
      />
      <CardBody className="space-y-4">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Field label="Ciudad" htmlFor="hoteles_ciudad">
            <Input
              id="hoteles_ciudad"
              value={ciudad}
              onChange={(e) => setCiudad(e.target.value)}
              placeholder="PAR"
              maxLength={3}
              disabled={buscando}
            />
          </Field>
          <Field label="Check-in" htmlFor="hoteles_checkin">
            <Input
              id="hoteles_checkin"
              type="date"
              value={checkin}
              onChange={(e) => setCheckin(e.target.value)}
              disabled={buscando}
            />
          </Field>
          <Field label="Check-out" htmlFor="hoteles_checkout">
            <Input
              id="hoteles_checkout"
              type="date"
              value={checkout}
              onChange={(e) => setCheckout(e.target.value)}
              disabled={buscando}
            />
          </Field>
          <Field label="Adultos" htmlFor="hoteles_adultos">
            <Input
              id="hoteles_adultos"
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
          Buscar hoteles
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
                No encontré hoteles en {buscados.ciudad} para esas fechas.
              </p>
            ) : (
              ofertas.map((o) => (
                <TarjetaHotel key={o.id} oferta={o} checkin={checkin} checkout={checkout} />
              ))
            )}
          </div>
        )}
      </CardBody>
    </Card>
  );
}
