"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { CheckIcon, XIcon } from "@/components/icons";
import { Alert, Badge, Card, CardBody, CardHeader, PageHeader, Spinner } from "@/components/ui";
import { getUsage } from "@/lib/api";
import { bytesToMb, formatDate } from "@/lib/format";
import {
  LIMIT_MESSAGES_PER_DAY,
  LIMIT_STORAGE_MB,
  LIMIT_VOICE_MINUTES_MONTH,
  UNLIMITED,
  type UsageOut,
} from "@/lib/types";

const FLAG_LABELS: Record<string, string> = {
  "voice.web": "Voz web (push-to-talk)",
  "voice.telephony": "Telefonía (Twilio)",
  "connectors.social": "Conectores sociales",
  campaigns: "Campañas",
  companion: "Companion de escritorio",
  "models.premium": "Modelos premium",
};

function UsageBar({ label, used, limit, unit }: { label: string; used: number; limit: number; unit: string }) {
  const unlimited = limit === UNLIMITED;
  const pct = unlimited || limit <= 0 ? 0 : Math.min(100, Math.round((used / limit) * 100));
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-sm">
        <span className="text-slate-600 dark:text-slate-300">{label}</span>
        <span className="text-slate-500 dark:text-slate-400">
          {used.toLocaleString("es")} {unit} {unlimited ? "· ilimitado" : `/ ${limit.toLocaleString("es")} ${unit}`}
        </span>
      </div>
      {!unlimited && (
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
          <div
            className={`h-full rounded-full ${pct >= 90 ? "bg-rose-500" : "bg-brand-500"}`}
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
    </div>
  );
}

export default function PanelPage() {
  const [usage, setUsage] = useState<UsageOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void load();
  }, []);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setUsage(await getUsage());
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cargar el uso.");
    } finally {
      setLoading(false);
    }
  }

  if (loading) {
    return (
      <div className="flex justify-center py-16">
        <Spinner className="h-6 w-6 text-slate-400" />
      </div>
    );
  }

  const missingFlags = usage ? Object.entries(usage.flags).filter(([, enabled]) => !enabled) : [];

  return (
    <div>
      <PageHeader title="Panel" description="Consumo del periodo actual frente a los límites de tu plan." />
      {error && (
        <div className="mb-4">
          <Alert variant="error">{error}</Alert>
        </div>
      )}

      {usage && (
        <div className="space-y-6">
          {missingFlags.length > 0 && (
            <div className="flex flex-col items-start justify-between gap-3 rounded-2xl border border-brand-200 bg-brand-50 px-5 py-4 dark:border-brand-900 dark:bg-brand-950/40 sm:flex-row sm:items-center">
              <div>
                <p className="text-sm font-medium text-brand-800 dark:text-brand-200">
                  Tu plan no tiene todas las capacidades activas.
                </p>
                <p className="mt-0.5 text-xs text-brand-700 dark:text-brand-300">
                  Sin activar: {missingFlags.map(([key]) => FLAG_LABELS[key] ?? key).join(", ")}. Mejora tu plan para
                  desbloquearlas.
                </p>
              </div>
              <Link
                href="/app/facturacion"
                className="inline-flex shrink-0 items-center rounded-lg bg-brand-600 px-3.5 py-2 text-sm font-medium text-white hover:bg-brand-700"
              >
                Ver planes
              </Link>
            </div>
          )}

          <Card>
            <CardHeader title="Consumo" description={`Desde ${formatDate(usage.period_start)}`} />
            <CardBody className="space-y-4">
              <UsageBar
                label="Mensajes"
                used={usage.usage["messages"] ?? 0}
                limit={usage.limits[LIMIT_MESSAGES_PER_DAY] ?? UNLIMITED}
                unit="msj"
              />
              <UsageBar
                label="Voz"
                used={Math.round((usage.usage["voice_seconds"] ?? 0) / 60)}
                limit={usage.limits[LIMIT_VOICE_MINUTES_MONTH] ?? UNLIMITED}
                unit="min"
              />
              <UsageBar
                label="Almacenamiento"
                used={bytesToMb(usage.usage["storage_bytes"] ?? 0)}
                limit={usage.limits[LIMIT_STORAGE_MB] ?? UNLIMITED}
                unit="MB"
              />
              <p className="pt-1 text-xs text-slate-400">
                El límite de mensajes es diario; el periodo mostrado arriba es mensual — úsalo como referencia de
                volumen, no como el contador exacto de hoy.
              </p>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Capacidades activas" description="Ver plan y facturación en Ajustes → Facturación." />
            <CardBody>
              <div className="flex flex-wrap gap-2">
                {Object.entries(usage.flags).map(([key, enabled]) => (
                  <Badge key={key} variant={enabled ? "brand" : "neutral"}>
                    <span className="mr-1 inline-flex">
                      {enabled ? <CheckIcon className="h-3 w-3" /> : <XIcon className="h-3 w-3" />}
                    </span>
                    {FLAG_LABELS[key] ?? key}
                  </Badge>
                ))}
              </div>
            </CardBody>
          </Card>
        </div>
      )}
    </div>
  );
}
