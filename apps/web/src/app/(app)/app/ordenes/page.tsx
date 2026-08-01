"use client";

/**
 * `/app/ordenes` — presupuestos, holdings (paper) y órdenes draft con gate permanente
 * (ROADMAP_V2.md §7.10 y §7 WP-V2-10; ver `apps/api/edecan_api/routers/commerce.py` y
 * `docs/dinero-real.md` para la política completa).
 *
 * No hay ningún formulario para CREAR una orden aquí a propósito: las órdenes solo nacen
 * como borrador desde el chat (`preparar_pago`/`preparar_orden`, herramientas `dangerous`
 * del agente) — esta página únicamente lista lo que ya existe y deja confirmar/cancelar,
 * con un segundo gate explícito (el modal de abajo) antes de tocar `POST .../confirm`.
 */

import { useCallback, useEffect, useState } from "react";

import {
  Alert,
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  Field,
  Input,
  PageHeader,
  Select,
  Spinner,
} from "@/components/ui";
import {
  cancelOrden,
  confirmOrden,
  fijarPresupuesto,
  listHoldings,
  listOrdenes,
  listPresupuestos,
  type Holding,
  type Orden,
  type OrdenAccionResultado,
  type OrdenStatus,
  type PresupuestoEstado,
} from "@/lib/api-ordenes";
import { formatDateTime, formatMoney, formatNumber } from "@/lib/format";

const STATUS_LABEL: Record<OrdenStatus, string> = {
  draft: "Borrador",
  confirmed: "Confirmada",
  executed_paper: "Ejecutada (paper)",
  cancelled: "Cancelada",
  expired: "Expirada",
};

const STATUS_BADGE: Record<OrdenStatus, "neutral" | "brand" | "success" | "warning" | "danger"> = {
  draft: "neutral",
  confirmed: "brand",
  executed_paper: "success",
  cancelled: "danger",
  expired: "warning",
};

const KIND_LABEL: Record<Orden["kind"], string> = {
  payment: "Pago",
  purchase: "Compra",
  trade: "Trading",
};

const AVISO_DOBLE_CONFIRMACION =
  "Esto NO mueve dinero real. Las operaciones de trading se ejecutan en modo simulado " +
  "(paper). Los pagos generan un enlace que TÚ debes abrir y aprobar.";

function resumenOrden(orden: Orden): string {
  if (orden.kind === "trade" && orden.simbolo && orden.lado && orden.cantidad != null) {
    const accion = orden.lado === "buy" ? "Compra" : "Venta";
    const cotizacion = orden.meta?.cotizacion;
    const precio = cotizacion ? ` a ${formatMoney(cotizacion.precio, cotizacion.moneda)} (${cotizacion.fuente})` : "";
    return `${accion} de ${formatNumber(orden.cantidad)} ${orden.simbolo}${precio}`;
  }
  return orden.descripcion;
}

export default function OrdenesPage() {
  const [orders, setOrders] = useState<Orden[]>([]);
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [presupuestos, setPresupuestos] = useState<PresupuestoEstado[]>([]);
  const [statusFiltro, setStatusFiltro] = useState<OrdenStatus | "">("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [confirmTarget, setConfirmTarget] = useState<Orden | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async (estado: OrdenStatus | "") => {
    setLoading(true);
    setError(null);
    try {
      const [ord, hold, pres] = await Promise.all([
        listOrdenes(estado || undefined),
        listHoldings(),
        listPresupuestos(),
      ]);
      setOrders(ord);
      setHoldings(hold);
      setPresupuestos(pres);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cargar la información de comercio.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(statusFiltro);
  }, [load, statusFiltro]);

  async function handleCancelar(orden: Orden) {
    setBusyId(orden.id);
    setError(null);
    try {
      await cancelOrden(orden.id);
      await load(statusFiltro);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cancelar la orden.");
    } finally {
      setBusyId(null);
    }
  }

  function handleConfirmado(_resultado: OrdenAccionResultado) {
    setConfirmTarget(null);
    void load(statusFiltro);
  }

  return (
    <div>
      <PageHeader
        title="Órdenes"
        description="Borradores de pago/trading creados por el chat. Nada se ejecuta sin tu confirmación aquí."
        actions={
          <Select
            value={statusFiltro}
            onChange={(e) => setStatusFiltro(e.target.value as OrdenStatus | "")}
            className="w-auto"
          >
            <option value="">Todos los estados</option>
            {Object.entries(STATUS_LABEL).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
        }
      />

      {error && (
        <div className="mb-4">
          <Alert variant="error">{error}</Alert>
        </div>
      )}

      <Card className="mb-6">
        <CardHeader
          title="Órdenes"
          description="Draft → confirmar/cancelar. Confirmar ejecuta en modo paper (trading) o genera un enlace de pago que tú abres."
        />
        <CardBody>
          {loading ? (
            <div className="flex justify-center py-8">
              <Spinner className="h-5 w-5 text-slate-400" />
            </div>
          ) : orders.length === 0 ? (
            <EmptyState
              title="No hay órdenes todavía"
              description="Pídele al chat que prepare un pago o una orden de trading — aparecerá aquí como borrador."
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-slate-100 text-xs uppercase tracking-wide text-slate-400 dark:border-slate-800">
                    <th className="py-2 pr-3 font-medium">Creada</th>
                    <th className="py-2 pr-3 font-medium">Tipo</th>
                    <th className="py-2 pr-3 font-medium">Resumen</th>
                    <th className="py-2 pr-3 text-right font-medium">Monto</th>
                    <th className="py-2 pr-3 font-medium">Estado</th>
                    <th className="py-2 pr-3" />
                  </tr>
                </thead>
                <tbody>
                  {orders.flatMap((orden) => {
                    const filaPrincipal = (
                      <tr
                        key={orden.id}
                        className="cursor-pointer border-b border-slate-50 hover:bg-slate-50 dark:border-slate-800/60 dark:hover:bg-slate-800/40"
                        onClick={() => setSelectedId(selectedId === orden.id ? null : orden.id)}
                      >
                        <td className="py-2 pr-3 whitespace-nowrap text-slate-500">
                          {formatDateTime(orden.created_at)}
                        </td>
                        <td className="py-2 pr-3 text-slate-600 dark:text-slate-300">{KIND_LABEL[orden.kind]}</td>
                        <td className="py-2 pr-3 text-slate-700 dark:text-slate-200">{resumenOrden(orden)}</td>
                        <td className="py-2 pr-3 whitespace-nowrap text-right font-medium text-slate-800 dark:text-slate-100">
                          {formatMoney(orden.monto, orden.moneda)}
                        </td>
                        <td className="py-2 pr-3">
                          <Badge variant={STATUS_BADGE[orden.status]}>{STATUS_LABEL[orden.status]}</Badge>
                        </td>
                        <td className="py-2 pr-1 text-right whitespace-nowrap">
                          {orden.status === "draft" && (
                            <Button
                              size="sm"
                              onClick={(e) => {
                                e.stopPropagation();
                                setConfirmTarget(orden);
                              }}
                            >
                              Confirmar
                            </Button>
                          )}
                          {(orden.status === "draft" || orden.status === "confirmed") && (
                            <Button
                              size="sm"
                              variant="secondary"
                              className="ml-2"
                              loading={busyId === orden.id}
                              onClick={(e) => {
                                e.stopPropagation();
                                void handleCancelar(orden);
                              }}
                            >
                              Cancelar
                            </Button>
                          )}
                        </td>
                      </tr>
                    );
                    if (selectedId !== orden.id) return [filaPrincipal];
                    return [
                      filaPrincipal,
                      <tr key={`${orden.id}-detalle`} className="border-b border-slate-50 dark:border-slate-800/60">
                        <td colSpan={6} className="bg-slate-50 px-3 py-3 dark:bg-slate-800/30">
                          <OrderDetail orden={orden} />
                        </td>
                      </tr>,
                    ];
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardBody>
      </Card>

      <Card className="mb-6">
        <CardHeader title="Holdings (paper)" description="Posiciones simuladas — nunca representan dinero real." />
        <CardBody>
          {holdings.length === 0 ? (
            <EmptyState title="Sin holdings todavía" description="Se crean al confirmar la primera orden de trading." />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-slate-100 text-xs uppercase tracking-wide text-slate-400 dark:border-slate-800">
                    <th className="py-2 pr-3 font-medium">Símbolo</th>
                    <th className="py-2 pr-3 text-right font-medium">Cantidad</th>
                    <th className="py-2 pr-3 text-right font-medium">Costo promedio</th>
                  </tr>
                </thead>
                <tbody>
                  {holdings.map((h) => (
                    <tr key={h.id} className="border-b border-slate-50 dark:border-slate-800/60">
                      <td className="py-2 pr-3 font-medium text-slate-800 dark:text-slate-100">{h.simbolo}</td>
                      <td className="py-2 pr-3 text-right text-slate-600 dark:text-slate-300">{formatNumber(h.cantidad)}</td>
                      <td className="py-2 pr-3 text-right text-slate-600 dark:text-slate-300">
                        {formatMoney(h.costo_promedio, h.moneda)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardBody>
      </Card>

      <PresupuestosSection presupuestos={presupuestos} onChange={() => load(statusFiltro)} setError={setError} />

      {confirmTarget && (
        <ConfirmModal orden={confirmTarget} onClose={() => setConfirmTarget(null)} onConfirmed={handleConfirmado} />
      )}
    </div>
  );
}

function OrderDetail({ orden }: { orden: Orden }) {
  const cotizacion = orden.meta?.cotizacion;
  return (
    <div className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2 lg:grid-cols-3">
      <DetailRow label="Descripción" value={orden.descripcion} />
      <DetailRow label="Monto" value={formatMoney(orden.monto, orden.moneda)} />
      {orden.simbolo && <DetailRow label="Símbolo" value={orden.simbolo} />}
      {orden.lado && <DetailRow label="Lado" value={orden.lado === "buy" ? "Compra" : "Venta"} />}
      {orden.cantidad != null && <DetailRow label="Cantidad" value={formatNumber(orden.cantidad)} />}
      {cotizacion && (
        <DetailRow
          label="Cotización"
          value={`${formatMoney(cotizacion.precio, cotizacion.moneda)} (fuente: ${cotizacion.fuente})`}
        />
      )}
      {orden.meta?.beneficiario && <DetailRow label="Beneficiario" value={orden.meta.beneficiario} />}
      {orden.meta?.payment_link && (
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-slate-400">Enlace de pago</p>
          <a
            href={orden.meta.payment_link}
            target="_blank"
            rel="noreferrer noopener"
            className="text-brand-600 underline dark:text-brand-400"
          >
            Abrir enlace de pago
          </a>
        </div>
      )}
      {orden.meta?.error && (
        <div className="sm:col-span-2 lg:col-span-3">
          <Alert variant="error">{orden.meta.error}</Alert>
        </div>
      )}
      <DetailRow label="Creada" value={formatDateTime(orden.created_at)} />
      {orden.confirmed_at && <DetailRow label="Confirmada" value={formatDateTime(orden.confirmed_at)} />}
      {orden.executed_at && <DetailRow label="Ejecutada" value={formatDateTime(orden.executed_at)} />}
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs font-medium uppercase tracking-wide text-slate-400">{label}</p>
      <p className="text-slate-700 dark:text-slate-200">{value}</p>
    </div>
  );
}

function ConfirmModal({
  orden,
  onClose,
  onConfirmed,
}: {
  orden: Orden;
  onClose: () => void;
  onConfirmed: (resultado: OrdenAccionResultado) => void;
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleConfirmar() {
    setLoading(true);
    setError(null);
    try {
      const resultado = await confirmOrden(orden.id);
      onConfirmed(resultado);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo confirmar la orden.");
      setLoading(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirmar-orden-titulo"
    >
      <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-5 shadow-xl dark:border-slate-800 dark:bg-slate-900">
        <h3 id="confirmar-orden-titulo" className="text-sm font-semibold text-slate-900 dark:text-slate-100">
          Confirmar orden
        </h3>
        <p className="mt-2 text-sm text-slate-600 dark:text-slate-300">{resumenOrden(orden)}</p>

        <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300">
          {AVISO_DOBLE_CONFIRMACION}
        </div>

        {error && (
          <div className="mt-3">
            <Alert variant="error">{error}</Alert>
          </div>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose} disabled={loading}>
            Cancelar
          </Button>
          <Button onClick={handleConfirmar} loading={loading}>
            Sí, confirmar
          </Button>
        </div>
      </div>
    </div>
  );
}

function PresupuestosSection({
  presupuestos,
  onChange,
  setError,
}: {
  presupuestos: PresupuestoEstado[];
  onChange: () => void;
  setError: (msg: string | null) => void;
}) {
  const [categoria, setCategoria] = useState("");
  const [monto, setMonto] = useState("");
  const [moneda, setMoneda] = useState("USD");
  const [saving, setSaving] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const montoNum = Number(monto);
    if (!categoria.trim() || Number.isNaN(montoNum) || montoNum <= 0) return;
    setSaving(true);
    setError(null);
    try {
      await fijarPresupuesto({ categoria: categoria.trim(), monto_mensual: montoNum, moneda: moneda || "USD" });
      setCategoria("");
      setMonto("");
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo fijar el presupuesto.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <CardHeader title="Presupuestos" description="% gastado este mes vs. lo presupuestado por categoría." />
      <CardBody>
        <form onSubmit={handleSubmit} className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Field label="Categoría" htmlFor="categoria-presupuesto">
            <Input id="categoria-presupuesto" value={categoria} onChange={(e) => setCategoria(e.target.value)} placeholder="comida" />
          </Field>
          <Field label="Monto mensual" htmlFor="monto-presupuesto">
            <Input
              id="monto-presupuesto"
              type="number"
              step="0.01"
              min="0"
              value={monto}
              onChange={(e) => setMonto(e.target.value)}
              placeholder="500"
            />
          </Field>
          <Field label="Moneda" htmlFor="moneda-presupuesto">
            <Input
              id="moneda-presupuesto"
              value={moneda}
              maxLength={3}
              onChange={(e) => setMoneda(e.target.value.toUpperCase())}
            />
          </Field>
          <div className="col-span-2 flex items-end sm:col-span-1">
            <Button type="submit" loading={saving}>
              Fijar / actualizar
            </Button>
          </div>
        </form>

        {presupuestos.length === 0 ? (
          <EmptyState title="Sin presupuestos configurados" description="Fija uno arriba, o pídeselo al chat." />
        ) : (
          <div className="space-y-4">
            {presupuestos.map((p) => {
              const pct = Math.min(100, Math.max(0, p.pct));
              return (
                <div key={p.id}>
                  <div className="mb-1 flex items-center justify-between text-sm">
                    <span className="text-slate-600 dark:text-slate-300">{p.categoria}</span>
                    <span className="font-medium text-slate-800 dark:text-slate-100">
                      {formatMoney(p.gastado, p.moneda)} / {formatMoney(p.monto_mensual, p.moneda)} ({p.pct.toFixed(0)}%)
                    </span>
                  </div>
                  <svg viewBox="0 0 100 8" className="h-2 w-full overflow-hidden rounded-full" preserveAspectRatio="none">
                    <rect x="0" y="0" width="100" height="8" className="fill-slate-100 dark:fill-slate-800" />
                    <rect
                      x="0"
                      y="0"
                      width={pct}
                      height="8"
                      className={p.alerta ? "fill-rose-500" : "fill-emerald-500"}
                    />
                  </svg>
                  {p.alerta && (
                    <p className="mt-1 text-xs text-rose-600 dark:text-rose-400">Cerca del límite mensual.</p>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </CardBody>
    </Card>
  );
}
