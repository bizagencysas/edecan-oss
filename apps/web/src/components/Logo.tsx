/**
 * Marca de Edecán — bot con audífonos (mismo diseño que el ícono real de la
 * app de escritorio y que el sitio de marketing, `LogoMark`/`Wordmark` en
 * `Documents/edecan/src/components/Logo.tsx`), reimplementada acá sin
 * dependencias externas (este proyecto no trae `clsx`/`cva`, ver
 * `components/ui.tsx`).
 */

function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

/** El glifo solo, en `currentColor` — se espera pintarlo sobre un fondo
 * (p.ej. un `div` con `bg-brand-600`), igual que hacía el placeholder "E"
 * que reemplaza. */
export function LogoGlyph({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 64 64" className={className} role="img" aria-label="Edecán">
      <path
        d="M15 31 C15 15.5 49 15.5 49 31"
        fill="none"
        stroke="currentColor"
        strokeWidth="3.4"
        strokeLinecap="round"
      />
      <rect x="11" y="28" width="8.4" height="14.5" rx="4.2" fill="currentColor" />
      <rect x="44.6" y="28" width="8.4" height="14.5" rx="4.2" fill="currentColor" />
      <rect x="20" y="19.5" width="24" height="26.5" rx="9" fill="currentColor" />
      <circle cx="28" cy="31.5" r="3.6" fill="var(--logo-eye, #4f46e5)" />
      <circle cx="36" cy="31.5" r="3.6" fill="var(--logo-eye, #4f46e5)" />
    </svg>
  );
}

/** Glifo + fondo cuadrado redondeado ya incluidos (equivalente al ícono real
 * de la app / favicon), para usar suelto sin envolverlo en otro `div`. */
export function LogoMark({ className }: { className?: string }) {
  return (
    <div
      className={cx(
        "flex shrink-0 items-center justify-center rounded-xl bg-brand-600 text-white",
        className,
      )}
    >
      <LogoGlyph className="h-[60%] w-[60%]" />
    </div>
  );
}

export function Wordmark({ className }: { className?: string }) {
  return <span className={cx("wordmark lowercase leading-none", className)}>edecán</span>;
}

export function Logo({
  className,
  markClassName,
  wordClassName,
}: {
  className?: string;
  markClassName?: string;
  wordClassName?: string;
}) {
  return (
    <span className={cx("inline-flex items-center gap-2.5", className)}>
      <LogoMark className={cx("h-9 w-9", markClassName)} />
      <Wordmark className={cx("text-lg text-slate-900 dark:text-slate-50", wordClassName)} />
    </span>
  );
}
