"use client";

import { useCallback, useEffect, useState } from "react";

import { Alert, Button, Card, CardBody, CardHeader, Select } from "@/components/ui";
import {
  DESKTOP_UPDATE_CHANNEL_KEY,
  normalizeDesktopUpdateChannel,
  updateProgressPercent,
  type DesktopUpdateChannel,
  type DesktopUpdateCheckResult,
  type DesktopUpdateMetadata,
  type DesktopUpdateProgress,
} from "@/lib/desktop-updates";
import { isTauriApp, tauriInvoke, tauriListenEvent } from "@/lib/tauriListen";

type UpdateState = "idle" | "checking" | "available" | "current" | "installing";

export function DesktopUpdateCenter() {
  if (!isTauriApp()) return null;
  return <DesktopUpdateCenterNative />;
}

function DesktopUpdateCenterNative() {
  const [channel, setChannel] = useState<DesktopUpdateChannel>("stable");
  const [currentVersion, setCurrentVersion] = useState<string | null>(null);
  const [update, setUpdate] = useState<DesktopUpdateMetadata | null>(null);
  const [status, setStatus] = useState<UpdateState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [downloaded, setDownloaded] = useState(0);
  const [contentLength, setContentLength] = useState<number | null>(null);

  useEffect(() => {
    setChannel(
      normalizeDesktopUpdateChannel(
        window.localStorage.getItem(DESKTOP_UPDATE_CHANNEL_KEY),
      ),
    );
  }, []);

  useEffect(() => {
    let disposed = false;
    let unlisten: () => void = () => {};
    void tauriListenEvent<DesktopUpdateProgress>(
      "edecan://update-progress",
      (event) => {
        if (disposed) return;
        if (event.event === "started") {
          setContentLength(event.content_length);
          setDownloaded(0);
        } else if (event.event === "progress") {
          setDownloaded((value) => value + event.chunk_length);
        }
      },
    ).then((stop) => {
      if (disposed) stop();
      else unlisten = stop;
    });
    return () => {
      disposed = true;
      unlisten();
    };
  }, []);

  const checkNow = useCallback(async (selected: DesktopUpdateChannel) => {
    setStatus("checking");
    setError(null);
    try {
      const result = await tauriInvoke<DesktopUpdateCheckResult>(
        "desktop_update_check",
        { channel: selected },
      );
      setCurrentVersion(result.currentVersion);
      setUpdate(result.update);
      setStatus(result.update ? "available" : "current");
    } catch (err) {
      setStatus("idle");
      setError(
        err instanceof Error ? err.message : "No se pudo buscar una actualización.",
      );
    }
  }, []);

  function changeChannel(next: DesktopUpdateChannel) {
    setChannel(next);
    setUpdate(null);
    setStatus("idle");
    setError(null);
    window.localStorage.setItem(DESKTOP_UPDATE_CHANNEL_KEY, next);
  }

  async function install() {
    setStatus("installing");
    setError(null);
    setDownloaded(0);
    setContentLength(null);
    try {
      if (!update) throw new Error("Busca una actualización antes de instalarla.");
      await tauriInvoke<void>("desktop_update_install", {
        expectedVersion: update.version,
        channel: update.channel,
      });
    } catch (err) {
      setStatus("available");
      setError(
        err instanceof Error ? err.message : "No se pudo instalar la actualización.",
      );
    }
  }

  const progress = updateProgressPercent(downloaded, contentLength);

  return (
    <Card className="lg:col-span-2">
      <CardHeader
        title="Actualizaciones"
        description="Edecán descarga paquetes firmados para esta computadora. Tus conversaciones, credenciales y ajustes permanecen en tu carpeta de datos."
      />
      <CardBody className="space-y-4">
        {error && <Alert variant="error">{error}</Alert>}
        {status === "current" && (
          <Alert variant="success">
            Tienes la versión más reciente del canal seleccionado.
          </Alert>
        )}
        {update && (
          <Alert variant="info">
            <span className="font-semibold">Edecán {update.version} está disponible.</span>
            {update.notes ? <span className="mt-1 block whitespace-pre-line">{update.notes}</span> : null}
          </Alert>
        )}

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
          <label className="space-y-1.5">
            <span className="block text-sm font-medium text-slate-700 dark:text-slate-200">
              Canal
            </span>
            <Select
              value={channel}
              disabled={status === "checking" || status === "installing"}
              onChange={(event) =>
                changeChannel(event.target.value as DesktopUpdateChannel)
              }
            >
              <option value="stable">Estable, recomendado</option>
              <option value="preview">Vista previa, funciones antes</option>
            </Select>
            <span className="block text-xs text-slate-500 dark:text-slate-400">
              La vista previa puede cambiar con más frecuencia. Ambos canales verifican la misma firma oficial.
            </span>
          </label>

          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="secondary"
              loading={status === "checking"}
              disabled={status === "installing"}
              onClick={() => void checkNow(channel)}
            >
              Buscar actualización
            </Button>
            {update && (
              <Button
                type="button"
                loading={status === "installing"}
                onClick={() => void install()}
              >
                Actualizar y reiniciar
              </Button>
            )}
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500 dark:text-slate-400">
          <span>Versión instalada: {currentVersion ?? "se comprobará al buscar"}</span>
          <span>Las actualizaciones nunca incluyen tus datos personales.</span>
        </div>

        {status === "installing" && (
          <div aria-live="polite">
            <div className="mb-1 flex justify-between text-xs text-slate-600 dark:text-slate-300">
              <span>Descargando y verificando…</span>
              {progress !== null && <span>{progress}%</span>}
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
              <div
                className={`h-full rounded-full bg-brand-600 transition-[width] ${
                  progress === null ? "w-1/3 animate-pulse" : ""
                }`}
                style={progress === null ? undefined : { width: `${progress}%` }}
              />
            </div>
          </div>
        )}
      </CardBody>
    </Card>
  );
}
