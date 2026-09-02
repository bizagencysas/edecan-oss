/**
 * Avatar de un bot/compañero persistente. Renderiza caras estilo Grok Bot
 * (`grok_face`: forma + relleno sólido + ojos inclinados) desde el JSONB
 * `avatar`, o el estilo legacy de iniciales sobre degradado.
 */

"use client";

import type { WorkerAvatar, WorkerAvatarEye } from "@/lib/api";

function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

export interface AgentAccent {
  key: string;
  label: string;
  gradient: string;
  text: string;
  swatch: string;
}

/** Acentos legacy (crear/editar compañeros workforce). */
export const AGENT_ACCENTS: AgentAccent[] = [
  {
    key: "stone",
    label: "Piedra",
    gradient: "from-stone-200 to-stone-300 dark:from-stone-700 dark:to-stone-800",
    text: "text-stone-700 dark:text-stone-200",
    swatch: "bg-stone-400",
  },
  {
    key: "sand",
    label: "Arena",
    gradient: "from-amber-100 to-orange-200 dark:from-amber-800 dark:to-orange-900",
    text: "text-amber-900 dark:text-amber-100",
    swatch: "bg-amber-400",
  },
  {
    key: "sage",
    label: "Salvia",
    gradient: "from-emerald-100 to-teal-200 dark:from-emerald-800 dark:to-teal-900",
    text: "text-emerald-900 dark:text-emerald-100",
    swatch: "bg-emerald-400",
  },
  {
    key: "sky",
    label: "Cielo",
    gradient: "from-sky-100 to-indigo-200 dark:from-sky-800 dark:to-indigo-900",
    text: "text-indigo-900 dark:text-sky-100",
    swatch: "bg-sky-400",
  },
  {
    key: "clay",
    label: "Arcilla",
    gradient: "from-rose-100 to-red-200 dark:from-rose-800 dark:to-red-900",
    text: "text-rose-900 dark:text-rose-100",
    swatch: "bg-rose-400",
  },
  {
    key: "graphite",
    label: "Grafito",
    gradient: "from-slate-300 to-slate-400 dark:from-slate-600 dark:to-slate-700",
    text: "text-slate-800 dark:text-slate-100",
    swatch: "bg-slate-500",
  },
];

export const ACCENT_KEYS = AGENT_ACCENTS.map((a) => a.key);

export const HEX_ACCENTS: { value: string; label: string }[] = [
  { value: "#78716c", label: "Piedra" },
  { value: "#b45309", label: "Ámbar" },
  { value: "#4d7c0f", label: "Oliva" },
  { value: "#047857", label: "Esmeralda" },
  { value: "#0369a1", label: "Cielo" },
  { value: "#6d28d9", label: "Violeta" },
  { value: "#be123c", label: "Rosa" },
  { value: "#334155", label: "Grafito" },
];

function accentByKey(key: string | null | undefined): AgentAccent | undefined {
  if (!key) return undefined;
  return AGENT_ACCENTS.find((a) => a.key === key);
}

function isHexColor(value: string | null | undefined): value is string {
  return !!value && /^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i.test(value.trim());
}

function normalizeHex(value: string): string {
  const h = value.trim().replace("#", "");
  if (h.length === 3) return `#${h.split("").map((c) => c + c).join("")}`;
  return `#${h}`;
}

function hexToRgb(hex: string): { r: number; g: number; b: number } {
  const n = parseInt(hex.replace("#", ""), 16);
  return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
}

function mixHex(a: string, b: string, t: number): string {
  const ca = hexToRgb(a);
  const cb = hexToRgb(b);
  const mix = (x: number, y: number) => Math.round(x + (y - x) * t);
  const r = mix(ca.r, cb.r);
  const g = mix(ca.g, cb.g);
  const bl = mix(ca.b, cb.b);
  return `#${((1 << 24) + (r << 16) + (g << 8) + bl).toString(16).slice(1)}`;
}

function luminance({ r, g, b }: { r: number; g: number; b: number }): number {
  const lin = (v: number) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

function hashString(value: string): number {
  let h = 0;
  for (let i = 0; i < value.length; i += 1) {
    h = (h * 31 + value.charCodeAt(i)) >>> 0;
  }
  return h;
}

export function resolveAccent(accent: string | null | undefined, seed: string): AgentAccent {
  const explicit = accentByKey(accent);
  if (explicit) return explicit;
  return AGENT_ACCENTS[hashString(seed || "?") % AGENT_ACCENTS.length] ?? AGENT_ACCENTS[0]!;
}

function initialsFrom(value: string): string {
  const parts = value.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return (parts[0] ?? "").slice(0, 2).toUpperCase();
  return `${(parts[0] ?? "").charAt(0)}${(parts[1] ?? "").charAt(0)}`.toUpperCase();
}

export function resolveInitials(
  initials: string | null | undefined,
  fallback: string,
): string {
  const explicit = initials?.trim();
  if (explicit) return initialsFrom(explicit);
  return initialsFrom(fallback);
}

export type AvatarSize = "sm" | "md" | "lg" | "xl";

const SIZES: Record<AvatarSize, string> = {
  sm: "h-8 w-8",
  md: "h-10 w-10",
  lg: "h-12 w-12",
  xl: "h-16 w-16",
};

const ONLINE_DOT: Record<AvatarSize, string> = {
  sm: "h-2 w-2 border",
  md: "h-2.5 w-2.5 border-2",
  lg: "h-3 w-3 border-2",
  xl: "h-3.5 w-3.5 border-2",
};

function hexagonPoints(): string {
  const cx = 50;
  const cy = 50;
  const r = 46;
  const pts: string[] = [];
  for (let i = 0; i < 6; i += 1) {
    const angle = (Math.PI / 180) * (60 * i - 90);
    pts.push(`${cx + r * Math.cos(angle)},${cy + r * Math.sin(angle)}`);
  }
  return pts.join(" ");
}

function ShapeMask({ shape }: { shape: string }) {
  const id = `grok-shape-${shape}`;
  switch (shape) {
    case "rounded_square":
      return (
        <clipPath id={id}>
          <rect x="4" y="4" width="92" height="92" rx="22" />
        </clipPath>
      );
    case "oval":
      return (
        <clipPath id={id}>
          <ellipse cx="50" cy="50" rx="38" ry="46" />
        </clipPath>
      );
    case "hexagon":
      return (
        <clipPath id={id}>
          <polygon points={hexagonPoints()} />
        </clipPath>
      );
    case "squircle":
      return (
        <clipPath id={id}>
          <rect x="6" y="6" width="88" height="88" rx="32" />
        </clipPath>
      );
    case "circle":
    default:
      return (
        <clipPath id={id}>
          <circle cx="50" cy="50" r="46" />
        </clipPath>
      );
  }
}

function clipId(shape: string): string {
  return `grok-shape-${shape}`;
}

function SlantedEye({ eye, color }: { eye: WorkerAvatarEye; color: string }) {
  const cx = (eye.x ?? 0.5) * 100;
  const cy = (eye.y ?? 0.4) * 100;
  const rx = (eye.rx ?? 0.055) * 100;
  const ry = (eye.ry ?? 0.075) * 100;
  const rot = eye.rotation ?? -22;
  return (
    <ellipse
      cx={cx}
      cy={cy}
      rx={rx}
      ry={ry}
      fill={color}
      transform={`rotate(${rot} ${cx} ${cy})`}
    />
  );
}

function GrokFaceAvatar({
  avatar,
  size,
  className,
  showOnline,
}: {
  avatar: WorkerAvatar;
  size: AvatarSize;
  className?: string;
  showOnline?: boolean;
}) {
  const shape = avatar.shape ?? "circle";
  const fill = isHexColor(avatar.fill) ? normalizeHex(avatar.fill) : "#3b82f6";
  const eyeColor = avatar.eyes?.color ?? "#ffffff";
  const left = avatar.eyes?.left;
  const right = avatar.eyes?.right;

  return (
    <span className={cx("relative inline-flex shrink-0", SIZES[size], className)}>
      <svg
        viewBox="0 0 100 100"
        aria-hidden="true"
        className="h-full w-full drop-shadow-sm"
      >
        <defs>
          <ShapeMask shape={shape} />
        </defs>
        <rect width="100" height="100" fill={fill} clipPath={`url(#${clipId(shape)})`} />
        {left ? <SlantedEye eye={left} color={eyeColor} /> : null}
        {right ? <SlantedEye eye={right} color={eyeColor} /> : null}
      </svg>
      {showOnline ? (
        <span
          aria-hidden="true"
          className={cx(
            "absolute bottom-0 right-0 rounded-full border-white bg-emerald-500 dark:border-slate-900",
            ONLINE_DOT[size],
          )}
        />
      ) : null}
    </span>
  );
}

export function AgentAvatar({
  name,
  displayName,
  avatar,
  size = "md",
  className,
  showOnline,
}: {
  name: string;
  displayName?: string | null;
  avatar?: WorkerAvatar | null;
  size?: AvatarSize;
  className?: string;
  /** Punto verde de estado en línea (p. ej. bot seleccionado en el sidebar). */
  showOnline?: boolean;
}) {
  if (avatar?.style === "grok_face") {
    return (
      <GrokFaceAvatar avatar={avatar} size={size} className={className} showOnline={showOnline} />
    );
  }

  const accentValue = avatar?.accent ?? avatar?.fill ?? null;
  const seed = (avatar?.seed ?? name) || "?";
  const initials = resolveInitials(avatar?.initials ?? null, displayName ?? name);
  const textSizes: Record<AvatarSize, string> = {
    sm: "text-[11px]",
    md: "text-sm",
    lg: "text-base",
    xl: "text-xl",
  };

  if (isHexColor(accentValue)) {
    const hex = normalizeHex(accentValue);
    const toneA = mixHex(hex, "#ffffff", 0.82);
    const toneB = hex;
    const dark = luminance(hexToRgb(hex)) < 0.5;
    const backgroundImage =
      avatar?.style === "geometric"
        ? `linear-gradient(135deg, ${toneA} 0%, ${toneA} 50%, ${toneB} 50%, ${toneB} 100%)`
        : `linear-gradient(135deg, ${toneA} 0%, ${toneB} 100%)`;
    return (
      <span className={cx("relative inline-flex shrink-0", SIZES[size], className)}>
        <span
          aria-hidden="true"
          style={{ backgroundImage, color: dark ? "#f8fafc" : "#334155" }}
          className={cx(
            "inline-flex h-full w-full select-none items-center justify-center rounded-xl font-semibold leading-none",
            "ring-1 ring-black/5 dark:ring-white/10",
            textSizes[size],
          )}
        >
          {initials}
        </span>
        {showOnline ? (
          <span
            aria-hidden="true"
            className={cx(
              "absolute bottom-0 right-0 rounded-full border-white bg-emerald-500 dark:border-slate-900",
              ONLINE_DOT[size],
            )}
          />
        ) : null}
      </span>
    );
  }

  const accent = resolveAccent(accentValue, seed);

  return (
    <span className={cx("relative inline-flex shrink-0", SIZES[size], className)}>
      <span
        aria-hidden="true"
        className={cx(
          "inline-flex h-full w-full select-none items-center justify-center rounded-xl font-semibold leading-none",
          "bg-gradient-to-br ring-1 ring-black/5 dark:ring-white/10",
          accent.gradient,
          accent.text,
          textSizes[size],
        )}
      >
        {initials}
      </span>
      {showOnline ? (
        <span
          aria-hidden="true"
          className={cx(
            "absolute bottom-0 right-0 rounded-full border-white bg-emerald-500 dark:border-slate-900",
            ONLINE_DOT[size],
          )}
        />
      ) : null}
    </span>
  );
}
