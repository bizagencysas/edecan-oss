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

type StartupState = { enabled: boolean };

export function DesktopPermissionsCenter() {
  if (!isTauriApp()) return null;
  return <DesktopPermissionsCenterNative />;
}

function DesktopPermissionsCenterNative() {
  const [state, setState] = useState<DesktopPermissionsState | null>(null);
  const [startup, setStartup] = useState<StartupState | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyPermission, setBusyPermission] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const loadState = useCallback(async (showLoading = true) => {
    if (showLoading) setLoading(true);
    setError(null);
    try {
      const [next, startupState] = await Promise.all([
        tauriInvoke<DesktopPermissionsState>("desktop_permissions_get_state"),
        tauriInvoke<StartupState>("startup_get_state"),
      ]);
      setState(next);
      setStartup(startupState);
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

  async function revealApplication() {
    setError(null);
    setMessage(null);
    try {
      const result = await tauriInvoke<PermissionActionResult>(
        "desktop_permission_request",
        { permissionId: "reveal_application" },
      );
      setMessage(result.message);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo localizar Edecán.");
    }
  }

  async function toggleStartup() {
    if (!startup) return;
    setBusyPermission("startup");
    setError(null);
    setMessage(null);
    try {
      const next = await tauriInvoke<StartupState>("startup_set_enabled", {
        enabled: !startup.enabled,
      });
      setStartup(next);
      setMessage(
        next.enabled
          ? "Edecán se abrirá oculto en la barra al iniciar sesión."
          : "Edecán ya no se abrirá automáticamente al iniciar sesión.",
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cambiar el inicio automático.");
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

        {state?.application_path && (
          <div className="rounded-xl border border-brand-200 bg-brand-50 p-4 dark:border-brand-900 dark:bg-brand-950/40">
            <p className="text-sm font-semibold text-slate-900 dark:text-white">
              El archivo que debes permitir es {state.application_name}
            </p>
            <p className="mt-1 text-xs leading-5 text-slate-600 dark:text-slate-300">
              Si macOS muestra un botón +, selecciona exactamente esta aplicación. No elijas Python, Terminal ni Jarvis.
            </p>
            <code className="mt-2 block break-all rounded-lg bg-white/80 px-3 py-2 text-[11px] text-slate-700 dark:bg-slate-900/80 dark:text-slate-200">
              {state.application_path}
            </code>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => void revealApplication()}
              className="mt-3"
            >
              Mostrar Edecán en Finder
            </Button>
          </div>
        )}

        {startup && (
          <div className="flex flex-col gap-3 rounded-xl border border-slate-200 bg-white p-4 sm:flex-row sm:items-center sm:justify-between dark:border-slate-700 dark:bg-slate-900">
            <div>
              <p className="font-medium text-slate-900 dark:text-slate-100">
                Edecán residente
              </p>
              <p className="mt-1 text-sm leading-5 text-slate-500 dark:text-slate-400">
                {startup.enabled
                  ? "Se inicia oculto con tu sesión y queda disponible en la barra de menú para el iPhone."
                  : "Solo estará disponible después de abrirlo manualmente."}
              </p>
            </div>
            <Button
              type="button"
              variant={startup.enabled ? "secondary" : "primary"}
              size="sm"
              loading={busyPermission === "startup"}
              onClick={() => void toggleStartup()}
              className="shrink-0 sm:min-w-40"
            >
              {startup.enabled ? "Desactivar al iniciar" : "Activar al iniciar"}
            </Button>
          </div>
        )}

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
