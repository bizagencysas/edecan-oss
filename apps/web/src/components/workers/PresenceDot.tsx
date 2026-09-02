/**
 * Indicador de presencia de un compañero persistente. Traduce `status` +
 * `enabled` a uno de cuatro estados y muestra un punto con aro. El pulso usa
 * `motion-safe:` para respetar `prefers-reduced-motion`.
 */

"use client";

function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

export type PresenceState = "active" | "idle" | "paused" | "off";

export function presenceState(status: string, enabled: boolean): PresenceState {
  if (!enabled || status === "disabled") return "off";
  if (status === "running") return "active";
  if (status === "paused") return "paused";
  return "idle";
}

export const PRESENCE_LABELS: Record<PresenceState, string> = {
  active: "Trabajando",
  idle: "Disponible",
  paused: "En pausa",
  off: "Desactivado",
};

const DOT_COLORS: Record<PresenceState, string> = {
  active: "bg-emerald-500",
  idle: "bg-emerald-500/70",
  paused: "bg-amber-500",
  off: "bg-slate-400 dark:bg-slate-600",
};

export function PresenceDot({
  state,
  className,
}: {
  state: PresenceState;
  className?: string;
}) {
  const color = DOT_COLORS[state];
  return (
    <span className={cx("relative inline-flex h-2 w-2 shrink-0", className)} aria-hidden="true">
      {state === "active" && (
        <span
          className={cx(
            "absolute inline-flex h-full w-full rounded-full opacity-75 motion-safe:animate-ping",
            color,
          )}
        />
      )}
      <span
        className={cx(
          "relative inline-flex h-2 w-2 rounded-full ring-2 ring-white dark:ring-slate-900",
          color,
        )}
      />
    </span>
  );
}