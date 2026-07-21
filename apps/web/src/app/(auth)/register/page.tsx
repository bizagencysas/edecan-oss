"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { Alert, Button, Card, CardBody, Field, Input } from "@/components/ui";
import { getSetupStatus } from "@/lib/api-configuracion";
import { useAuth } from "@/lib/auth-context";

export default function RegisterPage() {
  const { register } = useAuth();
  const [tenantName, setTenantName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await register(email, password, tenantName);
      // Primer arranque: si este TENANT (no este navegador — `tenants.
      // onboarding_completed_at`, migración 0009) no completó todavía el
      // wizard de bienvenida, lo mandamos ahí en vez de directo al chat —
      // DIRECCION_ACTUAL.md "Wizard de primer arranque". Antes esto se
      // decidía con un flag de `localStorage` que no tenía ninguna relación
      // con el tenant: un tenant nuevo en un navegador que ya había pasado
      // el wizard con OTRA cuenta se lo saltaba entero.
      const setupStatus = await getSetupStatus();
      window.location.replace(
        setupStatus.onboarding_completed ? "/app/" : "/app/bienvenida/",
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo crear la cuenta.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardBody className="space-y-4">
        <div>
          <h1 className="text-lg font-semibold text-slate-900 dark:text-slate-50">Prepara Edecan</h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            Crea tu espacio privado y tu perfil. Podrás cambiarlo todo después desde Ajustes.
          </p>
        </div>
        {error && <Alert variant="error">{error}</Alert>}
        <form className="space-y-4" onSubmit={handleSubmit}>
          <Field label="Nombre de tu espacio" htmlFor="tenant_name">
            <Input
              id="tenant_name"
              required
              minLength={1}
              maxLength={200}
              value={tenantName}
              onChange={(e) => setTenantName(e.target.value)}
              placeholder="Mi espacio"
            />
          </Field>
          <Field label="Correo electrónico" htmlFor="email">
            <Input
              id="email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="tu@correo.com"
            />
          </Field>
          <Field label="Contraseña" htmlFor="password" hint="Mínimo 8 caracteres.">
            <Input
              id="password"
              type="password"
              autoComplete="new-password"
              required
              minLength={8}
              maxLength={256}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
            />
          </Field>
          <Button type="submit" className="w-full" loading={submitting}>
            Crear cuenta
          </Button>
        </form>
        <p className="text-center text-sm text-slate-500 dark:text-slate-400">
          ¿Ya tienes cuenta?{" "}
          <Link href="/login" className="font-medium text-brand-600 hover:text-brand-700 dark:text-brand-400">
            Inicia sesión
          </Link>
        </p>
      </CardBody>
    </Card>
  );
}
