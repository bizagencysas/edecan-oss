"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";

import { Alert, Badge, Button, Card, CardBody, CardHeader, FullPageSpinner, PageHeader, Spinner } from "@/components/ui";
import { getBillingPortalUrl, getUsage } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { LIMIT_PHONE_NUMBERS, LIMIT_SEATS, PLAN_LABELS, UNLIMITED, type UsageOut } from "@/lib/types";

export default function FacturacionPage() {
  return (
    <Suspense fallback={<FullPageSpinner />}>
      <FacturacionContent />
    </Suspense>
  );
}

function FacturacionContent() {
  const { me } = useAuth();
  const searchParams = useSearchParams();
  const pendingStripe = searchParams.get("portal") === "pendiente-configurar-stripe";

  const [usage, setUsage] = useState<UsageOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getUsage()
      .then(setUsage)
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, []);

  const planKey = usage?.plan_key ?? me?.tenant.plan_key ?? "";
  const isSelfHost = planKey === "free_selfhost";

  async function handleOpenPortal() {
    setOpening(true);
    setError(null);
    try {
      const { url } = await getBillingPortalUrl();
      window.location.href = url;
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo abrir el portal de facturación.");
      setOpening(false);
    }
  }

  return (
    <div>
      <PageHeader title="Facturación" description="Tu plan y la gestión de tu suscripción." />

      {pendingStripe && (
        <div className="mb-4">
          <Alert variant="info">
            La integración con el portal de Stripe todavía no está configurada en este entorno — en producción este
            botón abriría el portal real de gestión de suscripción.
          </Alert>
        </div>
      )}
      {error && (
        <div className="mb-4">
          <Alert variant="error">{error}</Alert>
        </div>
      )}

      <Card className="max-w-md">
        <CardHeader title="Tu plan" />
        <CardBody className="space-y-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-xs uppercase tracking-wide text-slate-400">Plan actual</p>
              <p className="text-lg font-semibold text-slate-800 dark:text-slate-100">
                {PLAN_LABELS[planKey] ?? (planKey || "—")}
              </p>
            </div>
            {me && <Badge variant={me.tenant.status === "active" ? "success" : "neutral"}>{me.tenant.status}</Badge>}
          </div>

          {loading ? (
            <div className="flex justify-center py-4">
              <Spinner className="h-5 w-5 text-slate-400" />
            </div>
          ) : (
            usage && (
              <div className="grid grid-cols-2 gap-2 border-t border-slate-100 pt-3 text-sm dark:border-slate-800">
                <span className="text-slate-500 dark:text-slate-400">Números telefónicos</span>
                <span className="text-right text-slate-700 dark:text-slate-200">
                  {usage.limits[LIMIT_PHONE_NUMBERS] === UNLIMITED ? "ilimitado" : usage.limits[LIMIT_PHONE_NUMBERS]}
                </span>
                <span className="text-slate-500 dark:text-slate-400">Asientos</span>
                <span className="text-right text-slate-700 dark:text-slate-200">
                  {usage.limits[LIMIT_SEATS] === UNLIMITED ? "ilimitado" : usage.limits[LIMIT_SEATS]}
                </span>
              </div>
            )
          )}

          {isSelfHost ? (
            <p className="border-t border-slate-100 pt-3 text-xs text-slate-400 dark:border-slate-800">
              Estás en el plan gratuito de self-host: no hay suscripción de Stripe que gestionar. Trae tus propias
              API keys y aloja la plataforma en tu propia infraestructura.
            </p>
          ) : (
            <div className="border-t border-slate-100 pt-3 dark:border-slate-800">
              <Button className="w-full" onClick={handleOpenPortal} loading={opening}>
                Abrir portal de facturación
              </Button>
            </div>
          )}

          <p className="text-xs text-slate-400">
            Revisa tu consumo del mes y las capacidades activas en{" "}
            <Link href="/app/panel" className="font-medium text-brand-600 hover:text-brand-700 dark:text-brand-400">
              Panel
            </Link>
            .
          </p>
        </CardBody>
      </Card>
    </div>
  );
}
