"use client";

/**
 * Visor de la sesión remota activa: el frame más reciente, los controles
 * (Actualizar / Auto / Terminar) y el banner permanente que exige
 * `docs/control-remoto.md` ("visibilidad permanente" — la sesión nunca es
 * silenciosa, ni para el dueño del equipo ni para quien mira desde el panel).
 *
 * Modo "Control" (WP-V4-10, control remoto fase 2, `session.kind ===
 * "control"` Y `controlEnabled` — el flag de plan se revalida en cada
 * render, nunca basta con que la sesión ya exista como control): banner
 * ROJO en vez de ámbar, clic/doble clic/clic derecho sobre el frame mandan
 * `input_pointer` (mapeo de coordenadas de pantalla → resolución real del
 * frame en `./coords.ts`), y `RemoteControlPanel` debajo para teclado.
 */

import { useEffect, useRef, useState } from "react";

import { RemoteControlPanel } from "@/components/remoto/RemoteControlPanel";
import { mapClientPointToRemoteCoords } from "@/components/remoto/coords";
import { Badge, Button, Card, CardBody, CardHeader } from "@/components/ui";
import type { KeyInputPayload, PointerInputPayload, RemoteFrame, RemoteSession } from "@/lib/api-remoto";

const STATUS_LABEL: Record<string, string> = {
  pending: "Esperando aprobación del companion…",
  active: "Activa",
  ended: "Terminada",
  denied: "Denegada en el companion",
};

const STATUS_BADGE: Record<string, "brand" | "success" | "neutral" | "danger"> = {
  pending: "brand",
  active: "success",
  ended: "neutral",
  denied: "danger",
};

// Ventana para distinguir un clic simple de uno doble: el "click" se manda
// recién si no llegó un "dblclick" dentro de este margen -- si no, un doble
// clic terminaría mandando tres comandos (click + click + double_click) en
// vez de uno solo.
const DOUBLE_CLICK_WINDOW_MS = 220;

function formatDuration(totalSeconds: number): string {
  const clamped = Math.max(0, Math.floor(totalSeconds));
  const minutes = Math.floor(clamped / 60);
  const seconds = clamped % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

export function RemoteViewer({
  session,
  frame,
  auto,
  refreshing,
  onRefresh,
  onToggleAuto,
  onEnd,
  ending,
  controlEnabled = false,
  onPointerInput,
  onKeyInput,
  sendingInput = false,
}: {
  session: RemoteSession;
  frame: RemoteFrame | null;
  auto: boolean;
  refreshing: boolean;
  onRefresh: () => void;
  onToggleAuto: () => void;
  onEnd: () => void;
  ending: boolean;
  /** `session.kind === "control"` Y el tenant sigue teniendo el flag
   * `companion.remote_input` AHORA MISMO (no solo cuando se creó la sesión). */
  controlEnabled?: boolean;
  onPointerInput?: (payload: PointerInputPayload) => void;
  onKeyInput?: (payload: KeyInputPayload) => void;
  sendingInput?: boolean;
}) {
  const [nowTs, setNowTs] = useState<number>(() => Date.now());
  const imgRef = useRef<HTMLImageElement>(null);
  const pendingClickRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (session.status !== "active") return;
    const id = setInterval(() => setNowTs(Date.now()), 1000);
    return () => clearInterval(id);
  }, [session.status]);

  useEffect(() => {
    return () => {
      if (pendingClickRef.current) clearTimeout(pendingClickRef.current);
    };
  }, []);

  const elapsedSeconds = session.started_at
    ? (nowTs - new Date(session.started_at).getTime()) / 1000
    : 0;

  const isControl = session.kind === "control";
  const inputInteractive = isControl && controlEnabled && Boolean(onPointerInput) && Boolean(frame);
  const sessionDone = session.status === "ended" || session.status === "denied";

  function sendPointer(clientX: number, clientY: number, accion: PointerInputPayload["accion"]) {
    if (!inputInteractive || !frame || !imgRef.current || !onPointerInput) return;
    const point = mapClientPointToRemoteCoords(
      clientX,
      clientY,
      imgRef.current.getBoundingClientRect(),
      frame,
    );
    if (!point) return; // cayó en la franja vacía del letterbox
    onPointerInput({ tipo: "pointer", x: point.x, y: point.y, accion });
  }

  function handleImageClick(e: React.MouseEvent<HTMLImageElement>) {
    if (!inputInteractive) return;
    const { clientX, clientY } = e;
    if (pendingClickRef.current) clearTimeout(pendingClickRef.current);
    pendingClickRef.current = setTimeout(() => {
      sendPointer(clientX, clientY, "click");
      pendingClickRef.current = null;
    }, DOUBLE_CLICK_WINDOW_MS);
  }

  function handleImageDoubleClick(e: React.MouseEvent<HTMLImageElement>) {
    if (!inputInteractive) return;
    if (pendingClickRef.current) {
      clearTimeout(pendingClickRef.current);
      pendingClickRef.current = null;
    }
    sendPointer(e.clientX, e.clientY, "double_click");
  }

  function handleImageContextMenu(e: React.MouseEvent<HTMLImageElement>) {
    if (!inputInteractive) return;
    e.preventDefault();
    sendPointer(e.clientX, e.clientY, "right_click");
  }

  return (
    <div className="space-y-4">
      {isControl ? (
        <div
          role="status"
          className="space-y-2 rounded-lg border border-rose-400 bg-rose-50 px-3 py-2.5 text-sm font-medium text-rose-800 dark:border-rose-800 dark:bg-rose-950/40 dark:text-rose-300"
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <span className="relative flex h-2.5 w-2.5 shrink-0">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-rose-500 opacity-75" />
                <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-rose-600" />
              </span>
              Sesión de control activa — se está controlando tu equipo
            </div>
            <Button size="sm" variant="danger" onClick={onEnd} loading={ending}>
              Terminar sesión
            </Button>
          </div>
          {!controlEnabled && (
            <p className="text-xs font-normal text-rose-700 dark:text-rose-400">
              Tu plan ya no incluye control remoto de teclado/mouse — solo puedes ver la
              pantalla hasta que termines esta sesión.
            </p>
          )}
        </div>
      ) : (
        <div
          role="status"
          className="flex items-center gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm font-medium text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300"
        >
          <span className="relative flex h-2.5 w-2.5 shrink-0">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-500 opacity-75" />
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-amber-600" />
          </span>
          Sesión de visualización activa — solo lectura
        </div>
      )}

      <Card>
        <CardHeader
          title="Pantalla remota"
          description={
            inputInteractive
              ? "Clic, doble clic o clic derecho sobre la imagen para controlar el mouse."
              : "Se actualiza pidiéndole un frame nuevo al companion, no es video en vivo."
          }
          actions={<Badge variant={STATUS_BADGE[session.status] ?? "neutral"}>{STATUS_LABEL[session.status] ?? session.status}</Badge>}
        />
        <CardBody className="space-y-4">
          <div
            className="flex min-h-64 items-center justify-center overflow-hidden rounded-lg border border-slate-200 bg-slate-950 dark:border-slate-800"
          >
            {frame ? (
              <img
                ref={imgRef}
                src={`data:image/png;base64,${frame.image_b64}`}
                alt="Última captura de la pantalla remota"
                onClick={handleImageClick}
                onDoubleClick={handleImageDoubleClick}
                onContextMenu={handleImageContextMenu}
                className={`max-h-[70vh] w-full object-contain ${inputInteractive ? "cursor-crosshair" : ""}`}
              />
            ) : (
              <p className="px-6 py-16 text-center text-sm text-slate-400">
                {session.status === "denied"
                  ? "El companion denegó esta sesión: no va a haber ningún frame."
                  : "Todavía no hay ningún frame. Pulsa «Actualizar» para pedir el primero."}
              </p>
            )}
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-slate-500 dark:text-slate-400">
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              <span>Frames recibidos: {session.frames_count}</span>
              <span>Duración: {formatDuration(elapsedSeconds)}</span>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                variant="secondary"
                onClick={onRefresh}
                loading={refreshing}
                disabled={sessionDone}
              >
                Actualizar
              </Button>
              <Button
                size="sm"
                variant={auto ? "primary" : "secondary"}
                onClick={onToggleAuto}
                disabled={sessionDone}
              >
                {auto ? "Auto: activado (2s)" : "Auto: apagado"}
              </Button>
              {!isControl && (
                <Button size="sm" variant="danger" onClick={onEnd} loading={ending}>
                  Terminar sesión
                </Button>
              )}
            </div>
          </div>
        </CardBody>
      </Card>

      {isControl && controlEnabled && onKeyInput && (
        <RemoteControlPanel onSendKey={onKeyInput} sending={sendingInput} disabled={sessionDone} />
      )}
    </div>
  );
}
