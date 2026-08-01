"use client";

/**
 * Las fichas de estado de los mensajes que van saliendo del compositor de
 * Forge Studio: se pueden mandar ideas mientras el agente trabaja, y esto es
 * lo que hace visible qué pasó con cada una.
 *
 * POR QUÉ IMPORTA QUE SE VEA: un mensaje que se manda y no aparece por ningún
 * lado se manda otra vez, y otra. La ficha "En cola → Entregado" es la que
 * evita ese triple envío.
 *
 * No hay botón de retirar: el motor no sabe retirar un mensaje encolado y a
 * propósito ("dirigir no es cancelar", ver el docstring de
 * `apps/companion/edecan_companion/ide_sessions.py`; lo que corta el trabajo
 * es Detener). La cola vive segundos (el agente la lee al cerrar la vuelta que
 * tiene en curso), así que ese botón prometería algo que el motor no cumple.
 *
 * Sin estado propio a propósito: la vida del mensaje vive en `lib/ide-cola.ts`
 * (puro, probado sin navegador) y este archivo solo la pinta.
 */

import { CheckIcon } from "@/components/icons";
import { Spinner } from "@/components/ui";
import type { IdeEstadoDeMensaje, IdeMensajeEnCola } from "@/lib/ide-cola";

function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

// Mismos tonos semánticos que ya usan AgentActivityCenter/CodeEditor/DiffReview
// (bg-*-50 + dark:bg-*-950/40) para que la ficha se vea igual de bien en los
// dos temas: el compositor SÍ hereda el tema oscuro real desde que se corrigió
// el contenedor raíz de Forge Studio (antes forzaba `bg-white` sin variante).
const ESTILO_POR_ESTADO: Record<IdeEstadoDeMensaje, string> = {
  enviando: "border-slate-200 bg-slate-50 text-slate-500 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-400",
  encolado: "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/40 dark:text-amber-300",
  entregado: "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900/60 dark:bg-emerald-950/40 dark:text-emerald-300",
  fallido: "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-300",
};

function etiquetaDeEstado(mensaje: IdeMensajeEnCola): string {
  switch (mensaje.estado) {
    case "enviando":
      return "Enviando…";
    case "encolado":
      // La posición solo se muestra cuando de verdad hay algo por delante:
      // "el próximo" es más claro que un número que empieza en cero.
      if (mensaje.posicion === null || mensaje.posicion <= 0) return "En cola · entra al terminar";
      return `En cola · ${mensaje.posicion} por delante`;
    case "entregado":
      return "Entregado al agente";
    case "fallido":
      return mensaje.error || "No se pudo enviar";
  }
}

function Marca({ estado }: { estado: IdeEstadoDeMensaje }) {
  if (estado === "enviando") return <Spinner className="h-3 w-3 shrink-0" />;
  if (estado === "entregado") return <CheckIcon className="h-3.5 w-3.5 shrink-0" />;
  if (estado === "encolado") {
    return (
      <span className="relative flex h-2 w-2 shrink-0" aria-hidden="true">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-70" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-amber-500" />
      </span>
    );
  }
  if (estado === "fallido") {
    return <span className="h-2 w-2 shrink-0 rounded-full bg-rose-500" aria-hidden="true" />;
  }
  return <span className="h-2 w-2 shrink-0 rounded-full bg-slate-300" aria-hidden="true" />;
}

export interface MessageQueueProps {
  mensajes: IdeMensajeEnCola[];
  /** Devuelve el texto al compositor cuando el mensaje no llegó, para no perderlo. */
  onRecuperarTexto: (texto: string) => void;
  className?: string;
}

export function MessageQueue({ mensajes, onRecuperarTexto, className }: MessageQueueProps) {
  if (!mensajes.length) return null;

  return (
    <div
      aria-live="polite"
      className={cx(
        "mb-2 space-y-1.5 border-t border-slate-200/70 px-1 pt-2 dark:border-slate-700/70",
        className,
      )}
    >
      {mensajes.map((mensaje) => (
        <div
          key={mensaje.localId}
          className={cx(
            "flex items-center gap-2.5 rounded-lg border px-2.5 py-1.5 text-xs",
            ESTILO_POR_ESTADO[mensaje.estado],
          )}
        >
          <Marca estado={mensaje.estado} />
          <span className="min-w-0 flex-1 truncate" title={mensaje.texto}>
            {mensaje.texto}
          </span>
          <span className="shrink-0 font-semibold tabular-nums">{etiquetaDeEstado(mensaje)}</span>
          {mensaje.estado === "fallido" && (
            <button
              type="button"
              onClick={() => onRecuperarTexto(mensaje.texto)}
              className="shrink-0 rounded px-1.5 py-0.5 font-semibold underline underline-offset-2 hover:bg-rose-100 dark:hover:bg-rose-950/40"
            >
              Recuperar texto
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

export default MessageQueue;
