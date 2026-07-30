"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { Alert, Button, Card, CardBody, Field, Input } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { getSetupStatus } from "@/lib/api-configuracion";
import { useAuth } from "@/lib/auth-context";

export default function LoginPage() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [needsTotp, setNeedsTotp] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(email, password, needsTotp ? totpCode : undefined);
      // Mismo criterio que `register/page.tsx`: si este tenant todavía no
      // completó el wizard de bienvenida (p. ej. lo creó desde otro
      // dispositivo/navegador y nunca llegó al final), lo mandamos ahí en
      // vez de directo al chat.
      const setupStatus = await getSetupStatus();
      window.location.replace(
        setupStatus.onboarding_completed ? "/app/" : "/app/bienvenida/",
      );
    } catch (err) {
      if (err instanceof ApiError && /totp/i.test(err.message)) {
        setNeedsTotp(true);
      }
      setError(err instanceof Error ? err.message : "No se pudo iniciar sesión.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardBody className="space-y-4">
        <div>
          <h1 className="text-lg font-semibold text-slate-900 dark:text-slate-50">Inicia sesión</h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            Continúa donde quedaste con tu asistente.
          </p>
        </div>
        {error && <Alert variant="error">{error}</Alert>}
        <form className="space-y-4" onSubmit={handleSubmit}>
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
          <Field label="Contraseña" htmlFor="password">
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
            />
          </Field>
          {needsTotp && (
            <Field label="Código de autenticación (2FA)" htmlFor="totp" hint="De tu app TOTP (Google Authenticator, etc.)">
              <Input
                id="totp"
                inputMode="numeric"
                autoComplete="one-time-code"
                required
                value={totpCode}
                onChange={(e) => setTotpCode(e.target.value)}
                placeholder="123456"
              />
            </Field>
          )}
          <Button type="submit" className="w-full" loading={submitting}>
            Entrar
          </Button>
        </form>
        <p className="text-center text-sm text-slate-500 dark:text-slate-400">
          ¿No tienes cuenta?{" "}
          <Link href="/register" className="font-medium text-brand-600 hover:text-brand-700 dark:text-brand-400">
            Prepara Edecan
          </Link>
        </p>
      </CardBody>
    </Card>
  );
}
