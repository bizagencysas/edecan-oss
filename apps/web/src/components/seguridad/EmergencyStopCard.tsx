/**
 * Parada de emergencia: `POST /v1/agents/workers/pause-all`. Pone en pausa a
 * todos los compañeros persistentes a la vez. Confirmación inline de dos pasos
 * (nunca `window.confirm`): el dueño ve exactamente qué va a pasar antes de
 * confirmar, y un error del backend (p. ej. 404 mientras se construye) se
 * muestra tal cual — nunca se simula éxito.
 */

"use client";

import { useState } from "react";

import { PauseIcon, ShieldIcon } from "@/components/icons";
import { Alert, Button, Card, CardBody, CardHeader } from "@/components/ui";
import { pauseAllWorkers } from "@/lib/api-activity";

export function EmergencyStopCard({ onPaused }: { onPaused?: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);

  async function handleConfirm() {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const res = await pauseAllWorkers();
      const paused = typeof res.paused === "number" ? res.paused : null;
      setResult(
        paused === null
          ? "Todos los compañeros quedaron en pausa."
          : `Se pausaron ${paused} ${paused === 1 ? "compañero" : "compañeros"}.`,
      );
      setConfirming(false);
      onPaused?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo pausar a los compañeros.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="border-rose-200 dark:border-rose-900">
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <ShieldIcon className="h-4 w-4 text-rose-500" />
            Parada de emergencia
          </span>
        }
        description="Pon en pausa a todos los compañeros de una vez, sin apagarlos. Siguen existiendo y puedes reanudarlos desde su detalle."
      />
      <CardBody className="space-y-3">
        {error && <Alert variant="error">{error}</Alert>}
        {result && <Alert variant="success">{result}</Alert>}

        {confirming ? (
          <div className="rounded-xl border border-rose-200 bg-rose-50/60 p-3 dark:border-rose-900 dark:bg-rose-950/20">
            <p className="text-sm text-slate-700 dark:text-slate-200">
              ¿Pausar a todos los compañeros ahora? El trabajo en curso se detiene en el
              siguiente punto seguro.
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button variant="danger" size="sm" onClick={() => void handleConfirm()} loading={busy}>
                <PauseIcon className="h-3.5 w-3.5" />
                Pausar a todos
              </Button>
              <Button
                variant="secondary"
                size="sm"
                disabled={busy}
                onClick={() => setConfirming(false)}
              >
                Cancelar
              </Button>
            </div>
          </div>
        ) : (
          <Button variant="danger" onClick={() => setConfirming(true)}>
            <PauseIcon className="h-4 w-4" />
            Pausar a todos los compañeros
          </Button>
        )}
      </CardBody>
    </Card>
  );
}