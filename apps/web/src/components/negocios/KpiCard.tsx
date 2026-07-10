import { Card, CardBody } from "@/components/ui";

type Tone = "neutral" | "positive" | "negative";

const TONE_CLASS: Record<Tone, string> = {
  neutral: "text-slate-800 dark:text-slate-100",
  positive: "text-emerald-600 dark:text-emerald-400",
  negative: "text-rose-600 dark:text-rose-400",
};

/** Tarjeta KPI simple (fila de 4 en `/app/negocios`: ingresos/gastos/beneficio/nuevos clientes). */
export function KpiCard({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: Tone;
}) {
  return (
    <Card>
      <CardBody>
        <p className="text-xs font-medium uppercase tracking-wide text-slate-400">{label}</p>
        <p className={`mt-1 text-xl font-semibold ${TONE_CLASS[tone]}`}>{value}</p>
      </CardBody>
    </Card>
  );
}
