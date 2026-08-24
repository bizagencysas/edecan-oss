/** Helpers de formato compartidos por las páginas de `(app)`. */

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat("es", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(d);
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(`${iso}T00:00:00`.slice(0, 19));
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat("es", { dateStyle: "medium" }).format(d);
}

export function formatMoney(value: string | number | null | undefined, currency = "USD"): string {
  if (value === null || value === undefined) return "—";
  const n = typeof value === "string" ? Number(value) : value;
  if (Number.isNaN(n)) return String(value);
  try {
    return new Intl.NumberFormat("es", { style: "currency", currency }).format(n);
  } catch {
    return `${n.toFixed(2)} ${currency}`;
  }
}

/**
 * Número agrupado sin símbolo de moneda — se usa para el resumen de
 * `GET /v1/finance/summary`, que suma `monto` de transacciones sin agrupar
 * por `moneda` (no hay conversión), así que mostrar un símbolo de moneda ahí
 * sería engañoso si el tenant registra montos en más de una moneda.
 */
export function formatNumber(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const n = typeof value === "string" ? Number(value) : value;
  if (Number.isNaN(n)) return String(value);
  return new Intl.NumberFormat("es", { maximumFractionDigits: 2 }).format(n);
}

export function currentMonth(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

export function limitLabel(value: number | undefined): string {
  if (value === undefined) return "—";
  return value === -1 ? "ilimitado" : String(value);
}

export function bytesToMb(bytes: number | undefined): number {
  if (!bytes) return 0;
  return Math.round((bytes / (1024 * 1024)) * 10) / 10;
}
