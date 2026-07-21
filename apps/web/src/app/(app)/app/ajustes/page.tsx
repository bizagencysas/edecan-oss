"use client";

import Link from "next/link";
import { useState } from "react";

import { Alert, Button, Card, CardBody, CardHeader, Field, Input, PageHeader } from "@/components/ui";
import { ADVANCED_NAV_GROUPS } from "@/components/layout/nav-items";
import { API_BASE_URL, disableTotp, enableTotp, getCompanionPairCode, verifyTotp } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { formatDateTime } from "@/lib/format";

export default function AjustesPage() {
  const { me, signOut } = useAuth();

  const [totpSecret, setTotpSecret] = useState<string | null>(null);
  const [totpUri, setTotpUri] = useState<string | null>(null);
  const [totpCode, setTotpCode] = useState("");
  const [totpEnabled, setTotpEnabled] = useState(false);
  const [totpBusy, setTotpBusy] = useState(false);
  const [totpError, setTotpError] = useState<string | null>(null);

  const [disablePassword, setDisablePassword] = useState("");
  const [disableBusy, setDisableBusy] = useState(false);
  const [disableError, setDisableError] = useState<string | null>(null);
  const [disableSuccess, setDisableSuccess] = useState(false);

  const [pairCode, setPairCode] = useState<string | null>(null);
  const [pairBusy, setPairBusy] = useState(false);
  const [pairError, setPairError] = useState<string | null>(null);

  async function handleEnableTotp() {
    setTotpBusy(true);
    setTotpError(null);
    try {
      const { secret, provisioning_uri } = await enableTotp();
      setTotpSecret(secret);
      setTotpUri(provisioning_uri);
    } catch (err) {
      setTotpError(err instanceof Error ? err.message : "No se pudo generar el secreto TOTP.");
    } finally {
      setTotpBusy(false);
    }
  }

  async function handleVerifyTotp(e: React.FormEvent) {
    e.preventDefault();
    setTotpBusy(true);
    setTotpError(null);
    try {
      const { verified } = await verifyTotp(totpCode);
      setTotpEnabled(verified);
    } catch (err) {
      setTotpError(err instanceof Error ? err.message : "Código inválido.");
    } finally {
      setTotpBusy(false);
    }
  }

  async function handleDisableTotp(e: React.FormEvent) {
    e.preventDefault();
    setDisableBusy(true);
    setDisableError(null);
    setDisableSuccess(false);
    try {
      await disableTotp(disablePassword);
      setDisableSuccess(true);
      setDisablePassword("");
      // La cuenta ya no tiene 2FA: si quedaba abierto el flujo de activación
      // de esta misma sesión, se limpia para que la tarjeta vuelva a su
      // estado inicial ("Generar secreto TOTP") en vez de mostrar datos
      // obsoletos de un secreto que ya no aplica.
      setTotpEnabled(false);
      setTotpSecret(null);
      setTotpUri(null);
      setTotpCode("");
    } catch (err) {
      setDisableError(err instanceof Error ? err.message : "No se pudo desactivar el 2FA.");
    } finally {
      setDisableBusy(false);
    }
  }

  async function handlePairCode() {
    setPairBusy(true);
    setPairError(null);
    try {
      const { code } = await getCompanionPairCode();
      setPairCode(code);
    } catch (err) {
      setPairError(err instanceof Error ? err.message : "No se pudo generar el código de emparejamiento.");
    } finally {
      setPairBusy(false);
    }
  }

  return (
    <div>
      <PageHeader title="Ajustes" description="Cuenta, seguridad y dispositivos conectados." />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Cuenta" />
          <CardBody className="space-y-2 text-sm">
            <Row label="Correo" value={me?.user.email ?? "—"} />
            <Row label="Cuenta creada" value={formatDateTime(me?.tenant.created_at)} />
            <div className="pt-2">
              <Button variant="secondary" onClick={signOut}>
                Cerrar sesión
              </Button>
            </div>
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Protege tu cuenta"
            description="Añade un segundo código al iniciar sesión. Es opcional y puedes quitarlo cuando quieras."
          />
          <CardBody className="space-y-3">
            {totpError && <Alert variant="error">{totpError}</Alert>}
            {totpEnabled ? (
              <Alert variant="success">La verificación adicional está activa.</Alert>
            ) : totpSecret ? (
              <div className="space-y-3">
                <div>
                  <p className="text-xs text-slate-500 dark:text-slate-400">Secreto (agrégalo manualmente si no puedes escanear un QR):</p>
                  <code className="mt-1 block break-all rounded-lg bg-slate-50 p-2 text-xs dark:bg-slate-950">{totpSecret}</code>
                </div>
                <div>
                  <p className="text-xs text-slate-500 dark:text-slate-400">URI de aprovisionamiento:</p>
                  <code className="mt-1 block break-all rounded-lg bg-slate-50 p-2 text-xs dark:bg-slate-950">{totpUri}</code>
                </div>
                <form onSubmit={handleVerifyTotp} className="flex items-end gap-2">
                  <Field label="Código de 6 dígitos" htmlFor="totp_code" className="flex-1">
                    <Input
                      id="totp_code"
                      value={totpCode}
                      onChange={(e) => setTotpCode(e.target.value)}
                      inputMode="numeric"
                      placeholder="123456"
                    />
                  </Field>
                  <Button type="submit" loading={totpBusy}>
                    Verificar y activar
                  </Button>
                </form>
              </div>
            ) : (
              <Button onClick={handleEnableTotp} loading={totpBusy}>
                Configurar verificación
              </Button>
            )}

            {/*
              Siempre visible (no depende de `totpEnabled`, que solo refleja
              el flujo de activación de esta sesión): si el usuario ya tenía
              2FA activado de una sesión anterior y perdió el dispositivo/app
              autenticadora, esta es la ÚNICA ruta de recuperación —sin ella
              /login y /refresh exigen totp_code para siempre (ver docstring
              de POST /v1/auth/totp/disable).
            */}
            <form
              onSubmit={handleDisableTotp}
              className="space-y-3 border-t border-slate-100 pt-4 dark:border-slate-800"
            >
              <div>
                <p className="text-xs font-medium text-slate-700 dark:text-slate-200">Quitar verificación</p>
                <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                  ¿Perdiste el dispositivo o la app de autenticación? Confirma tu contraseña para desactivar la
                  verificación en dos pasos.
                </p>
              </div>
              {disableError && <Alert variant="error">{disableError}</Alert>}
              {disableSuccess && <Alert variant="success">La verificación adicional fue desactivada.</Alert>}
              <div className="flex items-end gap-2">
                <Field label="Contraseña" htmlFor="totp_disable_password" className="flex-1">
                  <Input
                    id="totp_disable_password"
                    type="password"
                    value={disablePassword}
                    onChange={(e) => setDisablePassword(e.target.value)}
                    autoComplete="current-password"
                    placeholder="••••••••"
                    required
                  />
                </Field>
                <Button type="submit" variant="danger" loading={disableBusy}>
                  Quitar verificación
                </Button>
              </div>
            </form>
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Edecan en tu computadora"
            description="Conecta la aplicación local para que Edecan pueda ayudarte con archivos y apps de este equipo."
          />
          <CardBody className="space-y-3">
            {pairError && <Alert variant="error">{pairError}</Alert>}
            {pairCode ? (
              <div>
                <p className="text-xs text-slate-500 dark:text-slate-400">Código (expira en 10 minutos):</p>
                <p className="mt-1 text-2xl font-mono font-semibold tracking-widest text-brand-600 dark:text-brand-400">
                  {pairCode}
                </p>
              </div>
            ) : (
              <p className="text-sm text-slate-500 dark:text-slate-400">
                Genera un código temporal e ingrésalo en la aplicación de Edecan instalada en tu computadora.
              </p>
            )}
            <Button variant="secondary" onClick={handlePairCode} loading={pairBusy}>
              Conectar computadora
            </Button>
          </CardBody>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader
            title="Capacidades"
            description="Edecan las usa por ti desde el chat. Abre el modo avanzado solo si quieres configurarlas directamente."
          />
          <CardBody>
            <details className="group">
              <summary className="cursor-pointer select-none text-sm font-medium text-brand-700 dark:text-brand-300">
                Mostrar modo avanzado
              </summary>
              <div className="mt-4 grid grid-cols-1 gap-5 border-t border-slate-100 pt-4 sm:grid-cols-2 lg:grid-cols-4 dark:border-slate-800">
                {ADVANCED_NAV_GROUPS.map((group) => (
                  <div key={group.label}>
                    <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">{group.label}</p>
                    <ul className="space-y-1.5">
                      {group.items.map((item) => (
                        <li key={item.href}>
                          <Link href={item.href} className="text-sm text-slate-600 hover:text-brand-700 dark:text-slate-300 dark:hover:text-brand-300">
                            {item.label}
                          </Link>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
              <div className="mt-5 border-t border-slate-100 pt-4 dark:border-slate-800">
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Información técnica</p>
                <div className="max-w-xl text-sm">
                  <Row label="Espacio" value={me?.tenant.name ?? "—"} />
                  <Row label="Identificador" value={me?.tenant.slug ?? "—"} />
                  <Row label="Estado" value={me?.tenant.status ?? "—"} />
                  <Row label="API" value={<code className="text-xs">{API_BASE_URL}</code>} />
                </div>
              </div>
            </details>
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-slate-50 py-1.5 last:border-0 dark:border-slate-800/60">
      <span className="text-slate-500 dark:text-slate-400">{label}</span>
      <span className="text-right font-medium text-slate-800 dark:text-slate-100">{value}</span>
    </div>
  );
}
