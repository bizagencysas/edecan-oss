"use client";

/**
 * Tarjeta "Conectar" genérica de la pantalla de Configuración
 * (DIRECCION_ACTUAL.md "Pantalla de Configuración"): header con ícono +
 * título + badge de estado + botón que abre/cierra el panel de conexión, y
 * body con el resumen de lo ya conectado (con botón "Quitar") más el panel
 * expandible (`children`, normalmente un `SelectorLLM`/`SelectorVoz`).
 *
 * `onQuitar` cubre el caso de UNA sola credencial por tarjeta (LLM). Cuando
 * una tarjeta agrupa varias credenciales independientes (Voz = STT + TTS,
 * cada una con su propio "Quitar"), pasa `resumen` ya armado con
 * `FilaCredencialConectada` por cada una y deja `onQuitar` sin definir — ver
 * `app/(app)/app/configuracion/page.tsx`.
 */

import { useState, type ReactNode } from "react";

import { CheckIcon, TrashIcon } from "@/components/icons";
import { Badge, Button, Card, CardBody, CardHeader, Spinner } from "@/components/ui";

export type EstadoCredencial = "conectado" | "sin_conectar";

export function FilaCredencialConectada({
  children,
  onQuitar,
  quitando,
}: {
  children: ReactNode;
  onQuitar?: () => void | Promise<void>;
  quitando?: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-slate-50 px-3 py-2 text-sm dark:bg-slate-950/40">
      <span className="flex items-center gap-1.5 text-slate-700 dark:text-slate-200">
        <CheckIcon className="h-3.5 w-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" />
        {children}
      </span>
      {onQuitar && (
        <button
          type="button"
          onClick={() => void onQuitar()}
          disabled={quitando}
          className="inline-flex shrink-0 items-center gap-1 text-xs font-medium text-rose-600 hover:text-rose-700 disabled:opacity-50 dark:text-rose-400"
        >
          {quitando ? <Spinner className="h-3 w-3" /> : <TrashIcon className="h-3 w-3" />}
          Quitar
        </button>
      )}
    </div>
  );
}

interface CardCredencialProps {
  icon: ReactNode;
  titulo: string;
  descripcion?: string;
  estado: EstadoCredencial;
  /** Contenido del estado "conectado". String u otro nodo simple → se envuelve solo si `onQuitar` viene definido; nodo ya compuesto (varias `FilaCredencialConectada`) → se deja tal cual. */
  resumen?: ReactNode;
  onQuitar?: () => void | Promise<void>;
  quitando?: boolean;
  children: ReactNode;
  defaultExpanded?: boolean;
  /** Acento visual de borde izquierdo — reservado para la tarjeta LLM ("la primera, destacada" en la instrucción de este WP): es la única credencial de la que depende poder chatear, el resto es siempre opcional. */
  destacado?: boolean;
}

export function CardCredencial({
  icon,
  titulo,
  descripcion,
  estado,
  resumen,
  onQuitar,
  quitando,
  children,
  defaultExpanded = false,
  destacado = false,
}: CardCredencialProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  return (
    <Card className={destacado ? "border-l-4 border-l-brand-500 dark:border-l-brand-400" : undefined}>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            {icon}
            {titulo}
          </span>
        }
        description={descripcion}
        actions={
          <div className="flex items-center gap-2">
            <Badge variant={estado === "conectado" ? "success" : "neutral"}>
              {estado === "conectado" ? "Conectado" : "Falta conectar"}
            </Badge>
            <Button
              size="sm"
              variant={estado === "conectado" ? "secondary" : "primary"}
              onClick={() => setExpanded((e) => !e)}
            >
              {estado === "conectado" ? "Cambiar" : "Conectar"}
            </Button>
          </div>
        }
      />
      <CardBody className="space-y-4">
        {estado === "conectado" &&
          resumen &&
          (onQuitar ? (
            <FilaCredencialConectada onQuitar={onQuitar} quitando={quitando}>
              {resumen}
            </FilaCredencialConectada>
          ) : (
            <div className="space-y-2">{resumen}</div>
          ))}
        {estado === "sin_conectar" && !expanded && (
          <p className="text-sm text-slate-400">Aún no está conectado.</p>
        )}
        {expanded && (
          <div className={resumen || estado === "conectado" ? "border-t border-slate-100 pt-4 dark:border-slate-800" : ""}>
            {children}
          </div>
        )}
      </CardBody>
    </Card>
  );
}
