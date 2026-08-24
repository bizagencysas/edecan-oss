"use client";

import { useEffect, useState } from "react";

import { Alert, Button, Card, CardBody, CardHeader } from "@/components/ui";
import {
  eraseAllMemory,
  deleteMyAccount,
  exportMyData,
  getPrivacyCenter,
  getAccountDeletionPreflight,
  type AccountDeletionPreflight,
  type PrivacyCenterStatus,
} from "@/lib/api";

function describeError(error: unknown): string {
  return error instanceof Error ? error.message : "No se pudo actualizar Privacidad.";
}

function downloadJson(value: Record<string, unknown>): void {
  const blob = new Blob([JSON.stringify(value, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `edecan-datos-${new Date().toISOString().slice(0, 10)}.json`;
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function PrivacyCenter() {
  const [status, setStatus] = useState<PrivacyCenterStatus | null>(null);
  const [preflight, setPreflight] = useState<AccountDeletionPreflight | null>(null);
  const [busy, setBusy] = useState<"export" | "memory" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [confirmingMemory, setConfirmingMemory] = useState(false);
  const [accountPassword, setAccountPassword] = useState("");
  const [accountConfirmation, setAccountConfirmation] = useState("");
  const [deletingAccount, setDeletingAccount] = useState(false);

  useEffect(() => {
    let active = true;
    getPrivacyCenter()
      .then((next) => { if (active) setStatus(next); })
      .catch((reason) => { if (active) setError(describeError(reason)); });
    getAccountDeletionPreflight()
      .then((next) => { if (active) setPreflight(next); })
      .catch((reason) => { if (active) setError(describeError(reason)); });
    return () => { active = false; };
  }, []);

  async function handleExport() {
    setBusy("export");
    setError(null);
    setInfo(null);
    try {
      downloadJson(await exportMyData());
      setInfo("Tu exportación se descargó en este dispositivo.");
    } catch (reason) {
      setError(describeError(reason));
    } finally {
      setBusy(null);
    }
  }

  async function handleEraseMemory() {
    setBusy("memory");
    setError(null);
    setInfo(null);
    try {
      const result = await eraseAllMemory();
      setInfo(`Memoria eliminada: ${result.deleted} ${result.deleted === 1 ? "registro" : "registros"}.`);
      setConfirmingMemory(false);
    } catch (reason) {
      setError(describeError(reason));
    } finally {
      setBusy(null);
    }
  }

  async function handleDeleteAccount() {
    setDeletingAccount(true);
    setError(null);
    setInfo(null);
    try {
      await deleteMyAccount({ password: accountPassword, confirmation: accountConfirmation });
      setInfo("La cuenta fue eliminada. Cerrando sesión…");
      window.setTimeout(() => window.location.assign("/login/"), 250);
    } catch (reason) {
      setError(describeError(reason));
    } finally {
      setDeletingAccount(false);
    }
  }

  return (
    <Card className="lg:col-span-2">
      <CardHeader
        title="Privacidad y tus datos"
        description="Exporta tus datos o elimina la memoria que Edecán usa para personalizar respuestas."
      />
      <CardBody className="space-y-4">
        {error && <Alert variant="error">{error}</Alert>}
        {info && <Alert variant="success">{info}</Alert>}
        <div className="grid gap-3 md:grid-cols-2">
          <div className="rounded-xl border border-slate-200 p-4 dark:border-slate-700">
            <p className="font-medium text-slate-900 dark:text-slate-100">Exportar mis datos</p>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
              Descarga un JSON con tus conversaciones, memoria, perfil, recordatorios, contactos,
              transacciones y archivos. No incluye credenciales ni claves operativas.
            </p>
            <Button className="mt-3" variant="secondary" onClick={() => void handleExport()} loading={busy === "export"}>
              Descargar exportación
            </Button>
          </div>
          <div className="rounded-xl border border-rose-200 p-4 dark:border-rose-900">
            <p className="font-medium text-slate-900 dark:text-slate-100">Eliminar mi memoria</p>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
              Borra la memoria personal guardada. Tus conversaciones y la cuenta no se eliminan con
              este control.
            </p>
            {!confirmingMemory ? (
              <Button className="mt-3" variant="danger" onClick={() => setConfirmingMemory(true)}>
                Eliminar memoria
              </Button>
            ) : (
              <div className="mt-3 flex flex-wrap gap-2">
                <Button variant="danger" onClick={() => void handleEraseMemory()} loading={busy === "memory"}>
                  Sí, eliminarla
                </Button>
                <Button variant="secondary" onClick={() => setConfirmingMemory(false)} disabled={busy !== null}>
                  Cancelar
                </Button>
              </div>
            )}
          </div>
        </div>
        {status?.controls.erase_account.available && (
          <div className="rounded-xl border border-rose-200 p-4 dark:border-rose-900">
            <p className="font-medium text-slate-900 dark:text-slate-100">Eliminar mi cuenta</p>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
              Esta acción elimina tu identidad y datos personales. Puede bloquearse si todavía
              existen archivos, conectores o una suscripción que requieren limpieza externa.
            </p>
            {preflight && !preflight.ready && (
              <Alert variant="error">
                No puedes eliminar la cuenta todavía:
                <ul className="mt-1 list-disc pl-5">
                  {preflight.blockers.map((blocker) => <li key={blocker.code}>{blocker.message}</li>)}
                </ul>
              </Alert>
            )}
            {!preflight && <p className="mt-2 text-xs text-slate-500">Comprobando dependencias externas…</p>}
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              <input
                type="password"
                value={accountPassword}
                onChange={(event) => setAccountPassword(event.target.value)}
                placeholder="Contraseña actual"
                autoComplete="current-password"
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-900"
              />
              <input
                value={accountConfirmation}
                onChange={(event) => setAccountConfirmation(event.target.value)}
                placeholder="ELIMINAR MI CUENTA"
                aria-label="Confirmación de borrado de cuenta"
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-900"
              />
            </div>
            <Button
              className="mt-3"
              variant="danger"
              onClick={() => void handleDeleteAccount()}
              loading={deletingAccount}
              disabled={
                !preflight?.ready || !accountPassword || accountConfirmation.trim().toUpperCase() !== "ELIMINAR MI CUENTA"
              }
            >
              Eliminar cuenta definitivamente
            </Button>
          </div>
        )}
      </CardBody>
    </Card>
  );
}
