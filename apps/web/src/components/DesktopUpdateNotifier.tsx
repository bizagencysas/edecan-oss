"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui";
import {
  DESKTOP_UPDATE_CHANNEL_KEY,
  DESKTOP_UPDATE_LAST_CHECK_KEY,
  normalizeDesktopUpdateChannel,
  shouldCheckForDesktopUpdate,
  type DesktopUpdateCheckResult,
  type DesktopUpdateMetadata,
} from "@/lib/desktop-updates";
import { isTauriApp, tauriInvoke } from "@/lib/tauriListen";

/**
 * Busca en silencio al abrir y al volver a Edecán. Solo ocupa espacio si
 * existe una versión nueva; un corte de internet jamás interrumpe el chat.
 */
export function DesktopUpdateNotifier() {
  const [update, setUpdate] = useState<DesktopUpdateMetadata | null>(null);
  const [installing, setInstalling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const checkIfDue = useCallback(async () => {
    if (!isTauriApp()) return;
    const lastCheck = window.localStorage.getItem(DESKTOP_UPDATE_LAST_CHECK_KEY);
    if (!shouldCheckForDesktopUpdate(lastCheck)) return;
    const channel = normalizeDesktopUpdateChannel(
      window.localStorage.getItem(DESKTOP_UPDATE_CHANNEL_KEY),
    );
    try {
      const result = await tauriInvoke<DesktopUpdateCheckResult>(
        "desktop_update_check",
        { channel },
      );
      setUpdate(result.update);
    } catch {
      // Una comprobación automática es oportunista. No mostramos una alarma
      // que compita con el trabajo de la persona por un corte de internet.
    } finally {
      // También se limita el reintento cuando no hay conexión. Sin esto,
      // cada vuelta a la ventana podría repetir inmediatamente la misma
      // petición fallida.
      window.localStorage.setItem(
        DESKTOP_UPDATE_LAST_CHECK_KEY,
        String(Date.now()),
      );
    }
  }, []);

  useEffect(() => {
    void checkIfDue();
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") void checkIfDue();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => document.removeEventListener("visibilitychange", onVisibilityChange);
  }, [checkIfDue]);

  if (!update) return null;

  async function install() {
    const pendingUpdate = update;
    if (!pendingUpdate) return;
    setInstalling(true);
    setError(null);
    try {
      await tauriInvoke<void>("desktop_update_install", {
        expectedVersion: pendingUpdate.version,
        channel: pendingUpdate.channel,
      });
    } catch (err) {
      setInstalling(false);
      setError(
        err instanceof Error ? err.message : "No se pudo instalar la actualización.",
      );
    }
  }

  return (
    <div className="fixed inset-x-3 top-3 z-[80] mx-auto max-w-2xl rounded-2xl border border-brand-200 bg-white p-4 shadow-2xl dark:border-brand-900 dark:bg-slate-900">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="min-w-0 flex-1">
          <p className="font-semibold text-slate-900 dark:text-white">
            Edecán {update.version} está listo
          </p>
          <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
            Se verificará la firma antes de instalar y Edecán se reiniciará.
          </p>
          {error && <p className="mt-1 text-sm text-red-600 dark:text-red-400">{error}</p>}
        </div>
        <div className="flex shrink-0 gap-2">
          <Button type="button" variant="secondary" onClick={() => setUpdate(null)}>
            Después
          </Button>
          <Button type="button" loading={installing} onClick={() => void install()}>
            Actualizar
          </Button>
        </div>
      </div>
    </div>
  );
}
