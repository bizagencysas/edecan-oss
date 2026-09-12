"use client";

/**
 * Voz GESTIONADA dentro del composer del chat (`docs/speech-engine.md`).
 *
 * El navegador habla con ElevenLabs por WebRTC usando SOLO el SDK oficial
 * (`@elevenlabs/client` → `Conversation.startSession({conversationToken})`):
 * micrófono, eco, turnos e interrupciones los maneja el proveedor. El push-to-talk
 * legacy (MediaRecorder + `/v1/voice/transcribe`) NO se toca ni se disfraza de
 * "voz gestionada": son modos distintos y nunca corren a la vez (el composer
 * los excluye mutuamente).
 *
 * - Sin preferencias activadas → aviso + enlace a Ajustes (nunca se finge una
 *   conexión).
 * - La sesión se crea con `paid_consent=true` explícito POR activación: los
 *   minutos se facturan a la API key de ElevenLabs del tenant.
 * - La X detiene micrófono/audio y deja al usuario en el MISMO chat; lo
 *   hablado ya quedó en la conversación canónica.
 */

import { Conversation } from "@elevenlabs/client";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  crearSesionVozGestionada,
  getPreferenciasVozGestionada,
  terminarSesionVozGestionada,
} from "@/lib/api-speech-engine";

type SesionGestionada = {
  endSession: () => Promise<void>;
};

type Estado =
  | { fase: "inactivo" }
  | { fase: "activando" }
  | { fase: "activo" }
  | { fase: "error"; mensaje: string };

export function VozGestionadaComposer({
  disabled = false,
  conversationId = null,
}: {
  /** El composer está enviando/grabando en modo legacy: el orb se desactiva
   * para no mezclar transportes. */
  disabled?: boolean;
  /** Conversación canónica abierta: la voz gestionada se ata a ESTA. */
  conversationId?: string | null;
}) {
  const [estado, setEstado] = useState<Estado>({ fase: "inactivo" });
  const [textoUsuario, setTextoUsuario] = useState("");
  const [textoAgente, setTextoAgente] = useState("");
  const conversationRef = useRef<SesionGestionada | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  /// Ciclo de arranque: `detener` lo invalida para que una conexión que
  /// todavía estaba provisionándose no quede activa tras el cierre.
  const arranqueRef = useRef(0);
  const activo = estado.fase === "activo" || estado.fase === "activando";

  const detener = useCallback(async () => {
    arranqueRef.current += 1;
    const conversation = conversationRef.current;
    conversationRef.current = null;
    try {
      if (conversation) await conversation.endSession();
    } catch {
      // El transporte ya cayó: la limpieza remota sigue abajo.
    }
    const sessionId = sessionIdRef.current;
    sessionIdRef.current = null;
    if (sessionId) {
      try {
        await terminarSesionVozGestionada(sessionId);
      } catch {
        // Idempotente en el servidor; el `expires_at` limpia igual.
      }
    }
    setTextoUsuario("");
    setTextoAgente("");
    setEstado({ fase: "inactivo" });
  }, []);

  const iniciar = useCallback(async () => {
    if (activo || disabled) return;
    setEstado({ fase: "activando" });
    arranqueRef.current += 1;
    const ciclo = arranqueRef.current;
    try {
      const preferencias = await getPreferenciasVozGestionada();
      if (arranqueRef.current !== ciclo) return;
      if (!preferencias.preference?.enabled || !preferencias.credential_connected) {
        setEstado({
          fase: "error",
          mensaje:
            "La voz gestionada no está activada. Configúrala en Ajustes (proveedor pagado: ElevenLabs Speech Engine).",
        });
        return;
      }
      const sesion = await crearSesionVozGestionada(conversationId);
      if (arranqueRef.current !== ciclo) {
        try {
          await terminarSesionVozGestionada(sesion.session_id);
        } catch {
          // Idempotente.
        }
        return;
      }
      sessionIdRef.current = sesion.session_id;
      setTextoUsuario("");
      setTextoAgente("");
      const conversation = await Conversation.startSession({
        conversationToken: sesion.conversation_token,
        onConnect: () => {
          if (arranqueRef.current !== ciclo) return;
          setEstado({ fase: "activo" });
        },
        onDisconnect: () => {
          conversationRef.current = null;
          if (arranqueRef.current !== ciclo) return;
          setEstado({ fase: "inactivo" });
        },
        onError: () => {
          if (arranqueRef.current !== ciclo) return;
          setEstado({ fase: "inactivo" });
        },
        onMessage: (mensaje: { message: string; role: "user" | "agent" }) => {
          if (arranqueRef.current !== ciclo) return;
          if (mensaje.role === "user") {
            setTextoUsuario(mensaje.message);
            setTextoAgente("");
          } else {
            setTextoAgente((previo) => previo + mensaje.message);
          }
        },
      });
      if (arranqueRef.current !== ciclo) {
        void conversation.endSession().catch(() => undefined);
        return;
      }
      conversationRef.current = conversation;
    } catch (error) {
      if (arranqueRef.current !== ciclo) return;
      const mensaje =
        error instanceof ApiError && error.status === 403
          ? "La voz gestionada no está activada para tu cuenta. Configúrala en Ajustes."
          : "No se pudo iniciar la voz gestionada.";
      setEstado({ fase: "error", mensaje });
      const sessionId = sessionIdRef.current;
      sessionIdRef.current = null;
      if (sessionId) {
        try {
          await terminarSesionVozGestionada(sessionId);
        } catch {
          // Idempotente.
        }
      }
    }
  }, [activo, disabled, conversationId]);

  useEffect(() => {
    return () => {
      // Al desmontar el composer (cambio de página), el transporte se cierra
      // y la sesión remota se termina explícitamente (el proveedor cobra por
      // minuto: no se deja facturando).
      arranqueRef.current += 1;
      const conversation = conversationRef.current;
      conversationRef.current = null;
      if (conversation) {
        void conversation.endSession().catch(() => undefined);
      }
      const sessionId = sessionIdRef.current;
      sessionIdRef.current = null;
      if (sessionId) {
        void terminarSesionVozGestionada(sessionId).catch(() => undefined);
      }
    };
  }, []);

  return (
    <>
      <button
        type="button"
        onClick={() => void (activo ? detener() : iniciar())}
        disabled={disabled}
        title="Voz gestionada (Speech Engine de ElevenLabs)"
        aria-label={activo ? "Detener voz gestionada" : "Activar voz gestionada"}
        aria-pressed={activo}
        className="h-9 w-9 rounded-full px-0 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 disabled:cursor-not-allowed disabled:opacity-50"
        data-testid="voice-managed-orb"
      >
        <span
          className={`flex h-8 w-8 items-center justify-center rounded-full ${
            activo ? "bg-rose-600 text-white" : "bg-brand-600 text-white"
          }`}
        >
          {estado.fase === "activando" ? (
            <span className="h-3 w-3 animate-pulse rounded-full bg-white/80" />
          ) : activo ? (
            <span className="text-sm font-semibold leading-none">✕</span>
          ) : (
            <span className="text-sm font-semibold leading-none">🎙</span>
          )}
        </span>
      </button>
      {activo && (
        <div
          className="absolute bottom-14 left-2 z-20 w-72 rounded-xl border border-slate-200 bg-white p-2.5 text-sm shadow-sm dark:border-slate-700 dark:bg-slate-900"
          data-testid="voice-managed-panel"
          aria-live="polite"
        >
          <div className="flex items-center justify-between gap-2">
            <span className="font-medium text-brand-700 dark:text-brand-300">
              Voz gestionada activa
            </span>
            <button
              type="button"
              onClick={() => void detener()}
              className="rounded-full border border-slate-200 px-2 py-0.5 text-[11px] font-medium text-slate-600 hover:border-rose-300 hover:text-rose-700 dark:border-slate-700 dark:text-slate-300"
              aria-label="Terminar voz gestionada"
              data-testid="voice-managed-stop"
            >
              Terminar
            </button>
          </div>
          {textoUsuario && (
            <p className="mt-1 truncate text-xs text-slate-500 dark:text-slate-400">
              Tú: {textoUsuario}
            </p>
          )}
          {textoAgente && (
            <p className="mt-1 line-clamp-2 text-xs text-slate-500 dark:text-slate-400">
              {textoAgente}
            </p>
          )}
          {!textoUsuario && !textoAgente && (
            <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">
              Habla — lo que digas queda en este mismo chat.
            </p>
          )}
        </div>
      )}
      {estado.fase === "error" && (
        <div
          className="absolute bottom-14 left-2 z-20 w-72 rounded-xl border border-amber-200 bg-amber-50 p-2.5 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200"
          data-testid="voice-managed-error"
        >
          <p>{estado.mensaje}</p>
          <a
            href="/app/configuracion"
            className="mt-1 inline-block font-medium underline underline-offset-2"
          >
            Ir a Ajustes
          </a>
        </div>
      )}
    </>
  );
}