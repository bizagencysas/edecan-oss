"use client";

/**
 * `/app/remoto` — vista remota (`ROADMAP_V2.md` §5 WP-V2-09) + control
 * remoto de teclado/mouse, `kind="control"` (WP-V4-10, "fase 2" — diseño
 * completo en `docs/control-remoto.md`).
 *
 * Flujo de pantallas: sin el flag de plan `companion.remote_view`, un aviso
 * claro (nada de intentar la petición para luego mostrar el 403 del
 * servidor); con el flag pero sin sesión activa, `ConsentGate` (que además
 * ofrece el checkbox de control remoto SOLO si también hay
 * `companion.remote_input`); con sesión activa, `RemoteViewer` (que se
 * encarga de todo el modo "Control": banner rojo, clics sobre el frame,
 * `RemoteControlPanel` de teclado) + el historial (`SessionHistory`) debajo.
 * Los errores de cada acción (503 sin companion, 501 companion
 * desactualizado/deshabilitado/plataforma sin soporte, 403 denegado/sin
 * flag/no es de control, 409 sesión ya terminada o todavía no activa, 429
 * pediste algo demasiado pronto) se traducen al mensaje que ya trae
 * `ApiError.message` desde el backend (`edecan_api.routers.remote`, ver ese
 * módulo para el texto exacto de cada caso) — esta página no reinventa el
 * copy, solo decide DÓNDE mostrarlo y qué hacer con el estado local en cada
 * caso (ver `handleFrame`/`handlePointerInput`/`handleKeyInput`).
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ConsentGate } from "@/components/remoto/ConsentGate";
import { RemoteViewer } from "@/components/remoto/RemoteViewer";
import { SessionHistory } from "@/components/remoto/SessionHistory";
import { Alert, Card, CardBody, PageHeader, Spinner } from "@/components/ui";
import {
  ApiError,
  createRemoteSession,
  endRemoteSession,
  getRemoteFrame,
  listRemoteSessions,
  sendRemoteInput,
  FLAG_COMPANION_REMOTE_INPUT,
  FLAG_COMPANION_REMOTE_VIEW,
  type KeyInputPayload,
  type PointerInputPayload,
  type RemoteFrame,
  type RemoteSession,
  type RemoteSessionKind,
} from "@/lib/api-remoto";
import { useAuth } from "@/lib/auth-context";

const AUTO_REFRESH_INTERVAL_MS = 350;

function describeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Ocurrió un error inesperado.";
}

export default function RemotoPage() {
  const { me, loading: authLoading } = useAuth();
  const hasRemoteViewFlag = Boolean(me?.flags?.[FLAG_COMPANION_REMOTE_VIEW]);
  const hasRemoteInputFlag = Boolean(me?.flags?.[FLAG_COMPANION_REMOTE_INPUT]);

  const [session, setSession] = useState<RemoteSession | null>(null);
  const [frame, setFrame] = useState<RemoteFrame | null>(null);
  const [history, setHistory] = useState<RemoteSession[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);

  const [starting, setStarting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [ending, setEnding] = useState(false);
  const [sendingInput, setSendingInput] = useState(false);
  const [auto, setAuto] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const frameInFlightRef = useRef(false);

  // `session` cambia en cada frame (frames_count/status) pero el intervalo de
  // abajo no debe reiniciarse por eso — solo le interesa el `id`. Un ref
  // evita que el efecto de auto-refresh dependa del objeto `session` entero.
  const sessionIdRef = useRef<string | null>(null);
  sessionIdRef.current = session?.id ?? null;

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      setHistory(await listRemoteSessions());
    } catch {
      // El historial es secundario: si falla no bloquea el flujo principal.
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    if (hasRemoteViewFlag) void loadHistory();
    else setHistoryLoading(false);
  }, [hasRemoteViewFlag, loadHistory]);

  const handleFrame = useCallback(async (): Promise<void> => {
    const sessionId = sessionIdRef.current;
    if (!sessionId || frameInFlightRef.current) return;
    frameInFlightRef.current = true;
    setRefreshing(true);
    try {
      const next = await getRemoteFrame(sessionId);
      setFrame(next);
      setError(null);
      setSession((prev) =>
        prev && prev.id === sessionId
          ? {
              ...prev,
              status: "active",
              frames_count: next.seq,
              started_at: prev.started_at ?? new Date().toISOString(),
            }
          : prev,
      );
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) {
        return; // silencioso: el auto-refresh pisó el límite, ya se reintenta en el próximo tick.
      }
      setError(describeError(err));
      if (err instanceof ApiError && (err.status === 403 || err.status === 409)) {
        setAuto(false);
        // El 403 (denegado) y el 409 (ya terminó) implican que el `status`
        // cambió en el servidor: refresca la sesión puntual para reflejarlo
        // en la UI (el banner/badge de RemoteViewer lee `session.status`).
        setSession((prev) => {
          if (!prev || prev.id !== sessionId) return prev;
          return { ...prev, status: err.status === 403 ? "denied" : "ended" };
        });
        void loadHistory();
      }
    } finally {
      frameInFlightRef.current = false;
      setRefreshing(false);
    }
  }, [loadHistory]);

  useEffect(() => {
    if (!auto || !session || session.status === "ended" || session.status === "denied") return;
    const id = setInterval(() => void handleFrame(), AUTO_REFRESH_INTERVAL_MS);
    return () => clearInterval(id);
  }, [auto, session, handleFrame]);

  async function handleStart(kind: RemoteSessionKind) {
    setStarting(true);
    setError(null);
    try {
      const created = await createRemoteSession(true, kind);
      setSession(created);
      setFrame(null);
      void loadHistory();
      // Pide el primer frame de inmediato: es lo que dispara la aprobación
      // LOCAL en el companion (ver docstring del módulo y ConsentGate). Una
      // sesión de control también necesita esto — el input exige
      // status="active", igual que la vista.
      sessionIdRef.current = created.id;
      await handleFrame();
    } catch (err) {
      setError(describeError(err));
    } finally {
      setStarting(false);
    }
  }

  async function handleEnd() {
    const current = session;
    if (!current) return;
    setEnding(true);
    setError(null);
    try {
      await endRemoteSession(current.id);
    } catch (err) {
      // Aunque falle el POST /end en el servidor, igual soltamos la vista
      // localmente: no tiene sentido dejar al usuario atrapado en el visor
      // por un error de red al cerrar.
      setError(describeError(err));
    } finally {
      setSession(null);
      setFrame(null);
      setAuto(false);
      setEnding(false);
      void loadHistory();
    }
  }

  async function handleInput(payload: PointerInputPayload | KeyInputPayload) {
    const sessionId = sessionIdRef.current;
    if (!sessionId) return;
    setSendingInput(true);
    setError(null);
    try {
      await sendRemoteInput(sessionId, payload);
    } catch (err) {
      setError(describeError(err));
      if (err instanceof ApiError && (err.status === 403 || err.status === 409)) {
        // Mismo criterio que handleFrame: el usuario denegó el comando (403,
        // la sesión entera queda "denied") o la sesión ya no está activa —
        // en ambos casos el estado del servidor cambió, hay que reflejarlo.
        setSession((prev) => {
          if (!prev || prev.id !== sessionId) return prev;
          return err.status === 403 ? { ...prev, status: "denied" } : prev;
        });
        void loadHistory();
      }
    } finally {
      setSendingInput(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Control remoto"
        description="Maneja desde tu teléfono la computadora que vinculaste con el QR."
      />

      {error && (
        <div className="mb-4">
          <Alert variant="error">{error}</Alert>
        </div>
      )}

      {authLoading ? (
        <div className="flex justify-center py-16">
          <Spinner className="h-6 w-6 text-slate-400" />
        </div>
      ) : !hasRemoteViewFlag ? (
        <Card>
          <CardBody>
            <Alert variant="info">
              La vista remota no está disponible en tu plan actual
              {me ? ` (${me.tenant.plan_key})` : ""}. Mejora tu plan desde Ajustes → Facturación
              para activarla.
            </Alert>
          </CardBody>
        </Card>
      ) : session ? (
        <div className="space-y-6">
          <RemoteViewer
            session={session}
            frame={frame}
            auto={auto}
            refreshing={refreshing}
            onRefresh={() => void handleFrame()}
            onToggleAuto={() => setAuto((a) => !a)}
            onEnd={() => void handleEnd()}
            ending={ending}
            controlEnabled={hasRemoteInputFlag}
            onPointerInput={(payload) => void handleInput(payload)}
            onKeyInput={(payload) => void handleInput(payload)}
            sendingInput={sendingInput}
          />
          {!historyLoading && <SessionHistory items={history} />}
        </div>
      ) : (
        <div className="space-y-6">
          <ConsentGate starting={starting} onStart={(kind) => void handleStart(kind)} allowControl={hasRemoteInputFlag} />
          {!historyLoading && <SessionHistory items={history} />}
        </div>
      )}
    </div>
  );
}
