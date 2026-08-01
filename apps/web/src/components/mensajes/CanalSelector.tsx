"use client";

/**
 * Selector de canal de `/app/mensajes` (WP-V4-11): un chip por canal con badge de estado
 * (conectado / sin conectar / solo envío) — nunca inventa un estado "cargando" propio, usa el
 * `loading` que ya trae `GET /v1/mensajes/canales` desde la página. Si el canal seleccionado
 * no está conectado, deja un aviso inline con link directo a `/app/conectores` (mismo lugar
 * donde ya se conectan Telegram/Discord/Slack/WhatsApp, ver `docs/mensajeria.md`).
 */

import Link from "next/link";

import { Badge, Button } from "@/components/ui";
import type { CanalEstado, CanalMensajeria } from "@/lib/api-mensajes";

const NOMBRES: Record<CanalMensajeria, string> = {
  telegram: "Telegram",
  discord: "Discord",
  slack: "Slack",
  whatsapp: "WhatsApp",
};

export function CanalSelector({
  canales,
  loading,
  selected,
  onSelect,
  onRefresh,
}: {
  canales: CanalEstado[];
  loading: boolean;
  selected: CanalMensajeria;
  onSelect: (canal: CanalMensajeria) => void;
  onRefresh: () => void;
}) {
  const actual = canales.find((c) => c.canal === selected);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {canales.map((c) => (
          <button
            key={c.canal}
            type="button"
            onClick={() => onSelect(c.canal)}
            aria-pressed={selected === c.canal}
            className={`flex items-center gap-2 rounded-xl border px-3 py-2 text-sm transition-colors ${
              selected === c.canal
                ? "border-brand-500 bg-brand-50 dark:border-brand-500 dark:bg-brand-950/40"
                : "border-slate-200 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800"
            }`}
          >
            <span className="font-medium text-slate-700 dark:text-slate-200">{NOMBRES[c.canal]}</span>
            <Badge variant={c.conectado ? "success" : "neutral"}>
              {c.conectado ? "conectado" : "sin conectar"}
            </Badge>
            {!c.puede_leer && <Badge variant="warning">solo envío</Badge>}
          </button>
        ))}
        <Button variant="ghost" size="sm" loading={loading} onClick={onRefresh}>
          Actualizar estado
        </Button>
      </div>

      {actual && !actual.conectado && (
        <p className="text-xs text-slate-500 dark:text-slate-400">
          {NOMBRES[selected]} todavía no está conectado.{" "}
          <Link
            href="/app/conectores"
            className="font-medium text-brand-600 hover:underline dark:text-brand-400"
          >
            Conéctalo en Conectores
          </Link>{" "}
          para leer o enviar mensajes.
        </p>
      )}
    </div>
  );
}
