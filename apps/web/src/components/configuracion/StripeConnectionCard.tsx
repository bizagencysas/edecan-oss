"use client";

import { useCallback, useEffect, useState } from "react";

import { Alert, Badge, Button, Card, CardBody, CardHeader, Field, Input, Spinner } from "@/components/ui";
import {
  deleteStripeCredentials,
  getStripeStatus,
  putStripeCredentials,
  syncStripeTransactions,
} from "@/lib/api";
import type { StripeStatus } from "@/lib/types";

export function StripeConnectionCard({ onSynced }: { onSynced?: () => void | Promise<void> }) {
  const [status, setStatus] = useState<StripeStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [apiKey, setApiKey] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await getStripeStatus());
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cargar Stripe.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadStatus();
  }, [loadStatus]);

  async function connect(event: React.FormEvent) {
    event.preventDefault();
    if (!apiKey.trim()) return;
    setConnecting(true);
    setMessage(null);
    setError(null);
    try {
      await putStripeCredentials(apiKey.trim());
      setApiKey("");
      await loadStatus();
      setMessage("Conectado y guardado de forma segura.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo conectar con Stripe.");
    } finally {
      setConnecting(false);
    }
  }

  async function disconnect() {
    setConnecting(true);
    setError(null);
    try {
      await deleteStripeCredentials();
      await loadStatus();
      setMessage(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo desconectar Stripe.");
    } finally {
      setConnecting(false);
    }
  }

  async function sync() {
    setSyncing(true);
    setMessage(null);
    setError(null);
    try {
      const result = await syncStripeTransactions();
      setMessage(result.sincronizadas > 0 ? `${result.sincronizadas} movimiento${result.sincronizadas === 1 ? "" : "s"} importado${result.sincronizadas === 1 ? "" : "s"}.` : "Ya estaba todo al día.");
      await onSynced?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo sincronizar con Stripe.");
    } finally {
      setSyncing(false);
    }
  }

  return (
    <Card>
      <CardHeader
        title="Stripe"
        description="Usa una Restricted key de solo lectura para importar movimientos."
        actions={
          loading ? (
            <Spinner className="h-4 w-4 text-slate-400" />
          ) : (
            <Badge variant={status?.connected ? "success" : "neutral"}>
              {status?.connected ? `Conectado ${status.masked ?? ""}`.trim() : "Falta conectar"}
            </Badge>
          )
        }
      />
      <CardBody className="space-y-3">
        {error && <Alert variant="error">{error}</Alert>}
        {message && <Alert variant="success">{message}</Alert>}
        {status?.connected ? (
          <div className="flex flex-wrap gap-2">
            <Button type="button" onClick={() => void sync()} loading={syncing}>Sincronizar ahora</Button>
            <Button type="button" variant="secondary" onClick={() => void disconnect()} loading={connecting}>Desconectar</Button>
          </div>
        ) : (
          <form onSubmit={connect} className="flex flex-wrap items-end gap-2">
            <Field label="Restricted key de Stripe" htmlFor="stripe_connection_key" className="min-w-0 basis-full sm:min-w-[260px] sm:flex-1">
              <Input id="stripe_connection_key" type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="rk_live_…" autoComplete="off" />
            </Field>
            <Button type="submit" loading={connecting} disabled={!apiKey.trim()}>Conectar Stripe</Button>
          </form>
        )}
      </CardBody>
    </Card>
  );
}
