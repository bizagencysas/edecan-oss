/**
 * Sección "Pendientes de tu OK" (`GET /v1/approvals`): aprobaciones durables de
 * acciones peligrosas del chat que esperan decisión. Aprobar reanuda el turno
 * (SSE, ver `api-approvals.ts`); rechazar marca `denied`. Ambos recargan la lista.
 */

"use client";

import { useEffect, useState } from "react";

import { CheckIcon, XIcon } from "@/components/icons";
import { Alert, Badge, Button, Card, CardBody, CardHeader, Spinner } from "@/components/ui";
import { approveApproval, denyApproval, listApprovals, type Approval } from "@/lib/api-approvals";
import { formatDateTime } from "@/lib/format";

function argsResumen(args: Record<string, unknown>): string | null {
  const keys = Object.keys(args ?? {});
  if (keys.length === 0) return null;
  try {
    const text = JSON.stringify(args);
    return text.length > 160 ? `${text.slice(0, 160)}…` : text;
  } catch {
    return null;
  }
}

export function ApprovalsSection() {
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setApprovals(await listApprovals());
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudieron cargar las aprobaciones.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function handleApprove(id: string) {
    setBusyId(id);
    setError(null);
    try {
      await approveApproval(id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo aprobar.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleDeny(id: string) {
    setBusyId(id);
    setError(null);
    try {
      await denyApproval(id);
      setApprovals((prev) => prev.filter((a) => a.id !== id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo rechazar.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <Card>
      <CardHeader
        title="Pendientes de tu OK"
        description="Acciones del chat que quedaron esperando tu decisión. Aprobar reanuda ese turno."
      />
      <CardBody>
        {error && (
          <div className="mb-3">
            <Alert variant="error">{error}</Alert>
          </div>
        )}
        {loading ? (
          <div className="flex justify-center py-6">
            <Spinner className="h-4 w-4 text-slate-400" />
          </div>
        ) : approvals.length === 0 ? (
          <p className="text-xs text-slate-400 dark:text-slate-500">
            No hay nada esperando tu decisión.
          </p>
        ) : (
          <ul className="space-y-2">
            {approvals.map((approval) => {
              const resumen = argsResumen(approval.args);
              const busy = busyId === approval.id;
              return (
                <li
                  key={approval.id}
                  className="flex items-start justify-between gap-3 rounded-lg border border-slate-100 px-3 py-2.5 dark:border-slate-800"
                >
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="warning">{approval.name || "herramienta"}</Badge>
                      <span className="text-[11px] text-slate-400 dark:text-slate-500">
                        {formatDateTime(approval.created_at)}
                      </span>
                    </div>
                    {resumen && (
                      <p className="mt-1 truncate font-mono text-[11px] text-slate-500 dark:text-slate-400">
                        {resumen}
                      </p>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    <Button
                      size="sm"
                      loading={busy}
                      onClick={() => handleApprove(approval.id)}
                    >
                      <CheckIcon className="h-3.5 w-3.5" />
                      Aprobar
                    </Button>
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={busy}
                      onClick={() => handleDeny(approval.id)}
                      aria-label="Rechazar"
                    >
                      <XIcon className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}