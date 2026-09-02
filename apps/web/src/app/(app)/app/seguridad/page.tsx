"use client";

import { useEffect, useState } from "react";

import { AgentPermissionsSection } from "@/components/seguridad/AgentPermissionsSection";
import { ComputerSessionsSection } from "@/components/seguridad/ComputerSessionsSection";
import { EmergencyStopCard } from "@/components/seguridad/EmergencyStopCard";
import { ApprovalsSection } from "@/components/workers/ApprovalsSection";
import { PlugIcon } from "@/components/icons";
import { Alert, Badge, Card, CardBody, CardHeader, PageHeader, Spinner } from "@/components/ui";
import { listConnectors } from "@/lib/api";
import { connectionStatusLabel } from "@/lib/connector-guides";
import type { ConnectorListItem } from "@/lib/types";

export default function SeguridadPage() {
  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <PageHeader
        title="Seguridad"
        description="Cuentas conectadas, permisos de tus agentes y control de emergencia. Todo en un solo lugar."
      />

      <EmergencyStopCard />

      <AgentPermissionsSection />

      <ApprovalsSection />

      <ConnectedAccountsSection />

      <ComputerSessionsSection />
    </div>
  );
}

function ConnectedAccountsSection() {
  const [connectors, setConnectors] = useState<ConnectorListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listConnectors()
      .then((next) => {
        if (!cancelled) setConnectors(next);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "No se pudieron cargar los conectores.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const connected = (connectors ?? []).filter((connector) => connector.accounts.length > 0);

  return (
    <Card>
      <CardHeader
        title="Cuentas conectadas"
        description="Qué servicios externos tienen acceso y qué permisos otorgaron."
      />
      <CardBody>
        {error ? (
          <Alert variant="error">{error}</Alert>
        ) : connectors === null ? (
          <div className="flex justify-center py-6">
            <Spinner className="h-4 w-4 text-slate-400" />
          </div>
        ) : connected.length === 0 ? (
          <p className="flex items-center gap-2 text-xs text-slate-400 dark:text-slate-500">
            <PlugIcon className="h-3.5 w-3.5" />
            No hay cuentas externas conectadas.
          </p>
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {connected.map((connector) => {
              const status = connectionStatusLabel(connector.accounts);
              const scopes = Array.from(
                new Set(connector.accounts.flatMap((account) => account.scopes ?? [])),
              );
              return (
                <li key={connector.key} className="py-2.5 first:pt-0 last:pb-0">
                  <div className="flex items-center justify-between gap-3">
                    <p className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">
                      {connector.display_name}
                    </p>
                    <Badge variant={status.variant}>{status.label}</Badge>
                  </div>
                  <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                    {connector.accounts.length}{" "}
                    {connector.accounts.length === 1 ? "cuenta" : "cuentas"}
                    {scopes.length > 0
                      ? ` · permisos: ${scopes.map((scope) => scope).join(", ")}`
                      : ""}
                  </p>
                </li>
              );
            })}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}