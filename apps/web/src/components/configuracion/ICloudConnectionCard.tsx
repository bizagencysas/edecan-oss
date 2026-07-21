"use client";

import { useCallback, useEffect, useState } from "react";

import { Alert, Badge, Button, Card, CardBody, CardHeader, Field, Input, Spinner } from "@/components/ui";
import {
  deleteICloudCredentials,
  getICloudContactsStatus,
  importContactsICloud,
  putICloudCredentials,
} from "@/lib/api";
import type { ICloudStatus } from "@/lib/types";

export function ICloudConnectionCard({ onImported }: { onImported?: () => void | Promise<void> }) {
  const [status, setStatus] = useState<ICloudStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [appleId, setAppleId] = useState("");
  const [password, setPassword] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [importing, setImporting] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await getICloudContactsStatus());
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cargar iCloud.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadStatus();
  }, [loadStatus]);

  async function connect(event: React.FormEvent) {
    event.preventDefault();
    if (!appleId.trim() || !password.trim()) return;
    setConnecting(true);
    setError(null);
    setMessage(null);
    try {
      await putICloudCredentials(appleId.trim(), password.trim());
      setAppleId("");
      setPassword("");
      await loadStatus();
      setMessage("Conectado y guardado de forma segura.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo conectar con iCloud.");
    } finally {
      setConnecting(false);
    }
  }

  async function disconnect() {
    setDisconnecting(true);
    setError(null);
    try {
      await deleteICloudCredentials();
      await loadStatus();
      setMessage(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo desconectar iCloud.");
    } finally {
      setDisconnecting(false);
    }
  }

  async function importContacts() {
    setImporting(true);
    setError(null);
    setMessage(null);
    try {
      const result = await importContactsICloud();
      setMessage(result.importados > 0 ? `${result.importados} contacto${result.importados === 1 ? "" : "s"} importado${result.importados === 1 ? "" : "s"}.` : "Ya estaba todo al día.");
      await onImported?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo importar desde iCloud.");
    } finally {
      setImporting(false);
    }
  }

  return (
    <Card>
      <CardHeader
        title="iCloud"
        description="Usa una contraseña específica de app; nunca escribas aquí la contraseña real de tu Apple ID."
        actions={
          loading ? (
            <Spinner className="h-4 w-4 text-slate-400" />
          ) : (
            <Badge variant={status?.connected ? "success" : "neutral"}>
              {status?.connected ? `Conectado (${status.apple_id})` : "Falta conectar"}
            </Badge>
          )
        }
      />
      <CardBody className="space-y-3">
        {error && <Alert variant="error">{error}</Alert>}
        {message && <Alert variant="success">{message}</Alert>}
        {status?.connected ? (
          <div className="flex flex-wrap gap-2">
            <Button type="button" onClick={() => void importContacts()} loading={importing}>Importar contactos</Button>
            <Button type="button" variant="secondary" onClick={() => void disconnect()} loading={disconnecting}>Desconectar</Button>
          </div>
        ) : (
          <form onSubmit={connect} className="grid grid-cols-1 items-end gap-3 sm:grid-cols-2">
            <Field label="Apple ID" htmlFor="icloud_connection_apple_id">
              <Input id="icloud_connection_apple_id" type="email" value={appleId} onChange={(event) => setAppleId(event.target.value)} placeholder="tucorreo@icloud.com" autoComplete="username" />
            </Field>
            <Field label="Contraseña específica de app" htmlFor="icloud_connection_password">
              <Input id="icloud_connection_password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="xxxx-xxxx-xxxx-xxxx" autoComplete="new-password" />
            </Field>
            <div className="sm:col-span-2"><Button type="submit" loading={connecting} disabled={!appleId.trim() || !password.trim()}>Conectar iCloud</Button></div>
          </form>
        )}
      </CardBody>
    </Card>
  );
}
