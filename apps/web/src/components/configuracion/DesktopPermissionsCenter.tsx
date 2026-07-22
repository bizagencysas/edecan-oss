"use client";

import { useCallback, useEffect, useState } from "react";

import { Alert, Button, Card, CardBody, CardHeader } from "@/components/ui";
import {
  mergePermissionAction,
  PERMISSION_STATUS_COPY,
  readyPermissionCount,
  type DesktopPermission,
  type DesktopPermissionsState,
  type PermissionActionResult,
} from "@/lib/desktop-permissions";
import { isTauriApp, tauriInvoke } from "@/lib/tauriListen";

const LEVEL_COPY: Record<DesktopPermission["level"], string> = {
  essential: "Necesario para esta función",
  recommended: "Recomendado",
  on_demand: "Se solicita cuando haga falta",
  optional: "Opcional",
};

export function DesktopPermissionsCenter() {
  if (!isTauriApp()) return null;
  return <DesktopPermissionsCenterNative />;
}

function DesktopPermissionsCenterNative() {
  const [state, setState] = useState<DesktopPermissionsState | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyPermission, setBusyPermission] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const loadState = useCallback(async (showLoading = true) => {
    if (showLoading) setLoading(true);
    setError(null);
    try {
      const next = await tauriInvoke<DesktopPermissionsState>(
        "desktop_permissions_get_state",
      );
      setState(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudieron comprobar los permisos.");
    } finally {
      if (showLoading) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadState();
    const refreshAfterSettings = () => void loadState(false);
    window.addEventListener("focus", refreshAfterSettings);
    return () => window.removeEventListener("focus", refreshAfterSettings);
  }, [loadState]);

  async function handlePermission(permission: DesktopPermission) {
    if (!permission.action_label) return;
    setBusyPermission(permission.id);
    setError(null);
    setMessage(null);
    try {
      const result = await tauriInvoke<PermissionActionResult>(
        "desktop_permission_request",
        { permissionId: permission.id },
      );
      setState((current) => (current ? mergePermissionAction(current, result) : current));
      setMessage(result.message);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo abrir este permiso.");
    } finally {
      setBusyPermission(null);
    }
  }

  const ready = state ? readyPermissionCount(state) : 0;
  const platform = state?.platform === "windows" ? "Windows" : "macOS";

  return (
    <Card className="lg:col-span-2">
      <CardHeader
        title="Permisos de esta computadora"
        description={`Todo en un solo lugar. Edecán comprueba ${platform}, abre el diálogo nativo cuando existe y te lleva a la sección exacta cuando el sistema exige activarlo manualmente.`}
      />
      <CardBody className="space-y-4">
        {error && <Alert variant="error">{error}</Alert>}
        {message && <Alert variant="info">{message}</Alert>}

        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-sm font-medium text-slate-800 dark:text-slate-100">
              {loading || !state
                ? "Comprobando permisos…"
                : `${ready} de ${state.permissions.length} listos o no requeridos`}
            </p>
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
              Edecán nunca puede concederse permisos por sí mismo; la decisión final siempre aparece en {platform}.
            </p>
          </div>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            loading={loading}
            onClick={() => void loadState()}
          >
            Actualizar estados
          </Button>
        </div>

        {state && (
          <div className="divide-y divide-slate-100 overflow-hidden rounded-xl border border-slate-200 dark:divide-slate-800 dark:border-slate-700">
            {state.permissions.map((permission) => (
              <PermissionRow
                key={permission.id}
                permission={permission}
                busy={busyPermission === permission.id}
                onAction={() => void handlePermission(permission)}
              />
            ))}
          </div>
        )}
      </CardBody>
    </Card>
  );
}

function PermissionRow({
  permission,
  busy,
  onAction,
}: {
  permission: DesktopPermission;
  busy: boolean;
  onAction: () => void;
}) {
  const status = PERMISSION_STATUS_COPY[permission.status];
  const toneClass =
    status.tone === "success"
      ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
      : status.tone === "warning"
        ? "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-300"
        : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300";

  return (
    <div className="flex flex-col gap-3 bg-white p-4 sm:flex-row sm:items-center sm:justify-between dark:bg-slate-900">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <p className="font-medium text-slate-900 dark:text-slate-100">{permission.title}</p>
          <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${toneClass}`}>
            {status.label}
          </span>
        </div>
        <p className="mt-1 text-sm leading-5 text-slate-500 dark:text-slate-400">
          {permission.description}
        </p>
        <p className="mt-1 text-[11px] font-medium uppercase tracking-wide text-slate-400">
          {LEVEL_COPY[permission.level]}
        </p>
      </div>
      {permission.action_label && (
        <Button
          type="button"
          variant={permission.status === "needs_action" ? "primary" : "secondary"}
          size="sm"
          loading={busy}
          onClick={onAction}
          className="shrink-0 sm:min-w-40"
        >
          {permission.action_label}
        </Button>
      )}
    </div>
  );
}
