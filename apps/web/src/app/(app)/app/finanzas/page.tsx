"use client";

import { useEffect, useState } from "react";

import { TrashIcon } from "@/components/icons";
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
  Spinner,
} from "@/components/ui";
import {
  createTransaction,
  deleteStripeCredentials,
  deleteTransaction,
  getFinanceSummary,
  getStripeStatus,
  listTransactions,
  putStripeCredentials,
  syncStripeTransactions,
} from "@/lib/api";
import { currentMonth, formatDate, formatMoney, formatNumber } from "@/lib/format";
import type { FinanceSummary, StripeStatus, Transaction } from "@/lib/types";

const emptyForm = { fecha: new Date().toISOString().slice(0, 10), monto: "", moneda: "USD", categoria: "", descripcion: "", cuenta: "" };

export default function FinanzasPage() {
  const [mes, setMes] = useState(currentMonth());
  const [items, setItems] = useState<Transaction[]>([]);
  const [summary, setSummary] = useState<FinanceSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [form, setForm] = useState(emptyForm);
  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  const [stripeStatus, setStripeStatus] = useState<StripeStatus | null>(null);
  const [stripeApiKey, setStripeApiKey] = useState("");
  const [connectingStripe, setConnectingStripe] = useState(false);
  const [syncingStripe, setSyncingStripe] = useState(false);
  const [stripeMessage, setStripeMessage] = useState<string | null>(null);

  useEffect(() => {
    void load(mes);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mes]);

  useEffect(() => {
    void loadStripeStatus();
  }, []);

  async function loadStripeStatus() {
    try {
      setStripeStatus(await getStripeStatus());
    } catch {
      // No bloquea el resto de la pantalla si esto falla.
    }
  }

  async function load(month: string) {
    setLoading(true);
    setError(null);
    try {
      const [tx, sum] = await Promise.all([listTransactions(month), getFinanceSummary(month)]);
      setItems(tx);
      setSummary(sum);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudieron cargar las finanzas.");
    } finally {
      setLoading(false);
    }
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    const monto = Number(form.monto);
    if (!form.fecha || Number.isNaN(monto) || monto === 0) return;
    setSaving(true);
    setError(null);
    try {
      await createTransaction({
        fecha: form.fecha,
        monto,
        moneda: form.moneda || "USD",
        categoria: form.categoria.trim() || null,
        descripcion: form.descripcion.trim() || null,
        cuenta: form.cuenta.trim() || null,
      });
      setForm({ ...emptyForm, fecha: form.fecha });
      await load(mes);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo registrar la transacción.");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: string) {
    setBusyId(id);
    setError(null);
    try {
      await deleteTransaction(id);
      await load(mes);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo borrar la transacción.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleConnectStripe(e: React.FormEvent) {
    e.preventDefault();
    if (!stripeApiKey.trim()) return;
    setConnectingStripe(true);
    setStripeMessage(null);
    setError(null);
    try {
      await putStripeCredentials(stripeApiKey.trim());
      setStripeApiKey("");
      await loadStripeStatus();
      setStripeMessage("Conectado ✓");
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo conectar con Stripe.");
    } finally {
      setConnectingStripe(false);
    }
  }

  async function handleDisconnectStripe() {
    setConnectingStripe(true);
    setError(null);
    try {
      await deleteStripeCredentials();
      await loadStripeStatus();
      setStripeMessage(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo desconectar Stripe.");
    } finally {
      setConnectingStripe(false);
    }
  }

  async function handleSyncStripe() {
    setSyncingStripe(true);
    setStripeMessage(null);
    setError(null);
    try {
      const result = await syncStripeTransactions();
      setStripeMessage(
        result.sincronizadas > 0
          ? `${result.sincronizadas} transacción${result.sincronizadas === 1 ? "" : "es"} nueva${result.sincronizadas === 1 ? "" : "s"} importada${result.sincronizadas === 1 ? "" : "s"}.`
          : "Ya estaba todo al día — nada nuevo que importar.",
      );
      await load(mes);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo sincronizar con Stripe.");
    } finally {
      setSyncingStripe(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Finanzas"
        description="Transacciones personales y resumen tipo «CFO personal» por mes."
        actions={
          <Input
            type="month"
            value={mes}
            onChange={(e) => setMes(e.target.value)}
            className="w-auto"
          />
        }
      />
      {error && (
        <div className="mb-4">
          <Alert variant="error">{error}</Alert>
        </div>
      )}

      {summary && (
        <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-3">
          <Card>
            <CardBody>
              <p className="text-xs font-medium uppercase tracking-wide text-slate-400">Ingresos</p>
              <p className="mt-1 text-xl font-semibold text-emerald-600 dark:text-emerald-400">
                {formatNumber(summary.ingresos)}
              </p>
            </CardBody>
          </Card>
          <Card>
            <CardBody>
              <p className="text-xs font-medium uppercase tracking-wide text-slate-400">Gastos</p>
              <p className="mt-1 text-xl font-semibold text-rose-600 dark:text-rose-400">
                {formatNumber(summary.gastos)}
              </p>
            </CardBody>
          </Card>
          <Card>
            <CardBody>
              <p className="text-xs font-medium uppercase tracking-wide text-slate-400">Neto</p>
              <p className="mt-1 text-xl font-semibold text-slate-800 dark:text-slate-100">{formatNumber(summary.neto)}</p>
            </CardBody>
          </Card>
        </div>
      )}

      <Card className="mb-6">
        <CardHeader
          title="Stripe"
          description="Trae los movimientos de tu propia cuenta de Stripe con una Restricted key de solo lectura — ver docs/finanzas-stripe.md."
          actions={
            stripeStatus?.connected ? (
              <Badge variant="success">Conectado {stripeStatus.masked}</Badge>
            ) : (
              <Badge variant="neutral">Sin conectar</Badge>
            )
          }
        />
        <CardBody className="space-y-3">
          {stripeMessage && <Alert variant="success">{stripeMessage}</Alert>}
          {stripeStatus?.connected ? (
            <div className="flex flex-wrap gap-2">
              <Button type="button" onClick={handleSyncStripe} loading={syncingStripe}>
                Sincronizar ahora
              </Button>
              <Button
                type="button"
                variant="secondary"
                onClick={handleDisconnectStripe}
                loading={connectingStripe}
              >
                Desconectar
              </Button>
            </div>
          ) : (
            <form onSubmit={handleConnectStripe} className="flex flex-wrap items-end gap-2">
              <Field label="Restricted key de Stripe" htmlFor="stripe_key" className="min-w-[280px] flex-1">
                <Input
                  id="stripe_key"
                  type="password"
                  value={stripeApiKey}
                  onChange={(e) => setStripeApiKey(e.target.value)}
                  placeholder="rk_live_…"
                />
              </Field>
              <Button type="submit" loading={connectingStripe} disabled={!stripeApiKey.trim()}>
                Conectar Stripe
              </Button>
            </form>
          )}
        </CardBody>
      </Card>

      {summary && summary.por_categoria.length > 0 && (
        <Card className="mb-6">
          <CardHeader title="Por categoría" description="Barras relativas al mayor total absoluto del mes." />
          <CardBody>
            <div className="space-y-3">
              {(() => {
                const maxAbs = Math.max(1, ...summary.por_categoria.map((row) => Math.abs(Number(row.total)) || 0));
                return summary.por_categoria.map((row) => {
                  const value = Number(row.total) || 0;
                  const pct = Math.min(100, (Math.abs(value) / maxAbs) * 100);
                  return (
                    <div key={row.categoria}>
                      <div className="mb-1 flex items-center justify-between text-sm">
                        <span className="text-slate-600 dark:text-slate-300">{row.categoria}</span>
                        <span className="font-medium text-slate-800 dark:text-slate-100">{formatNumber(row.total)}</span>
                      </div>
                      <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                        <div
                          className={`h-full rounded-full ${value < 0 ? "bg-rose-500" : "bg-emerald-500"}`}
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                    </div>
                  );
                });
              })()}
            </div>
          </CardBody>
        </Card>
      )}

      <Card className="mb-6">
        <CardHeader title="Nueva transacción" description="Monto positivo = ingreso, negativo = gasto." />
        <CardBody>
          <form onSubmit={handleCreate} className="grid grid-cols-2 gap-3 sm:grid-cols-6">
            <Field label="Fecha" htmlFor="fecha">
              <Input id="fecha" type="date" value={form.fecha} onChange={(e) => setForm({ ...form, fecha: e.target.value })} />
            </Field>
            <Field label="Monto" htmlFor="monto">
              <Input id="monto" type="number" step="0.01" value={form.monto} onChange={(e) => setForm({ ...form, monto: e.target.value })} placeholder="-152300" />
            </Field>
            <Field label="Moneda" htmlFor="moneda">
              <Input id="moneda" value={form.moneda} maxLength={3} onChange={(e) => setForm({ ...form, moneda: e.target.value.toUpperCase() })} />
            </Field>
            <Field label="Categoría" htmlFor="categoria">
              <Input id="categoria" value={form.categoria} onChange={(e) => setForm({ ...form, categoria: e.target.value })} />
            </Field>
            <Field label="Cuenta" htmlFor="cuenta">
              <Input id="cuenta" value={form.cuenta} onChange={(e) => setForm({ ...form, cuenta: e.target.value })} />
            </Field>
            <Field label="Descripción" htmlFor="descripcion">
              <Input id="descripcion" value={form.descripcion} onChange={(e) => setForm({ ...form, descripcion: e.target.value })} />
            </Field>
            <div className="col-span-2 flex items-end sm:col-span-6">
              <Button type="submit" loading={saving}>
                Registrar
              </Button>
            </div>
          </form>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title={`Transacciones de ${mes}`} />
        <CardBody>
          {loading ? (
            <div className="flex justify-center py-8">
              <Spinner className="h-5 w-5 text-slate-400" />
            </div>
          ) : items.length === 0 ? (
            <EmptyState title="Sin transacciones este mes" />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-slate-100 text-xs uppercase tracking-wide text-slate-400 dark:border-slate-800">
                    <th className="py-2 pr-3 font-medium">Fecha</th>
                    <th className="py-2 pr-3 font-medium">Descripción</th>
                    <th className="py-2 pr-3 font-medium">Categoría</th>
                    <th className="py-2 pr-3 font-medium">Cuenta</th>
                    <th className="py-2 pr-3 text-right font-medium">Monto</th>
                    <th className="py-2 pr-3" />
                  </tr>
                </thead>
                <tbody>
                  {items.map((t) => (
                    <tr key={t.id} className="border-b border-slate-50 dark:border-slate-800/60">
                      <td className="py-2 pr-3 whitespace-nowrap text-slate-500">{formatDate(t.fecha)}</td>
                      <td className="py-2 pr-3 text-slate-700 dark:text-slate-200">{t.descripcion || "—"}</td>
                      <td className="py-2 pr-3 text-slate-500">{t.categoria || "sin categoría"}</td>
                      <td className="py-2 pr-3 text-slate-500">{t.cuenta || "—"}</td>
                      <td
                        className={`py-2 pr-3 whitespace-nowrap text-right font-medium ${
                          Number(t.monto) < 0 ? "text-rose-600 dark:text-rose-400" : "text-emerald-600 dark:text-emerald-400"
                        }`}
                      >
                        {formatMoney(t.monto, t.moneda)}
                      </td>
                      <td className="py-2 pr-1 text-right">
                        <button
                          onClick={() => handleDelete(t.id)}
                          disabled={busyId === t.id}
                          className="rounded-md p-1.5 text-slate-400 hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-950/40"
                          aria-label="Borrar transacción"
                        >
                          {busyId === t.id ? <Spinner className="h-3.5 w-3.5" /> : <TrashIcon className="h-3.5 w-3.5" />}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
