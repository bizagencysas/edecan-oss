"use client";

import { useCallback, useEffect, useRef, useState, type ChangeEvent } from "react";

import {
  disconnectPushCredentials,
  getPushPreferences,
  getPushStatus,
  savePushCredentials,
  updatePushPreferences,
} from "@/lib/api";
import type { PushPreferences, PushStatus } from "@/lib/types";
import {
  Alert,
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  Checkbox,
  Field,
  Input,
  Select,
  Textarea,
} from "@/components/ui";

const DEFAULT_PREFERENCES: PushPreferences = {
  work: true,
  content: true,
  design: true,
  files: true,
  self_repair: true,
};

const PREFERENCE_LABELS: Array<{
  key: keyof PushPreferences;
  label: string;
}> = [
  { key: "work", label: "Trabajos y llamadas" },
  { key: "content", label: "Contenido creado o publicado" },
  { key: "design", label: "Diseños y exportaciones" },
  { key: "files", label: "Archivos y PDF listos" },
  { key: "self_repair", label: "Autorreparaciones terminadas" },
];

export function PushNotificationSettings() {
  const [status, setStatus] = useState<PushStatus | null>(null);
  const [preferences, setPreferences] =
    useState<PushPreferences>(DEFAULT_PREFERENCES);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const [teamId, setTeamId] = useState("");
  const [keyId, setKeyId] = useState("");
  const [bundleId, setBundleId] = useState("");
  const [p8Key, setP8Key] = useState("");
  const [apnsEnvironment, setApnsEnvironment] =
    useState<"production" | "sandbox">("production");
  const [fcmJson, setFcmJson] = useState("");
  const p8Input = useRef<HTMLInputElement>(null);
  const fcmInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [nextStatus, nextPreferences] = await Promise.all([
        getPushStatus(),
        getPushPreferences(),
      ]);
      setStatus(nextStatus);
      setPreferences(nextPreferences);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo revisar tus avisos.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function readSecretFile(
    event: ChangeEvent<HTMLInputElement>,
    setter: (value: string) => void,
  ) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    try {
      setter(await file.text());
      setError(null);
    } catch {
      setError("No se pudo leer ese archivo.");
    }
  }

  async function saveApns() {
    setBusy(true);
    setError(null);
    setSuccess(null);
    try {
      await savePushCredentials({
        apns: {
          team_id: teamId.trim(),
          key_id: keyId.trim(),
          bundle_id: bundleId.trim(),
          p8_key: p8Key.trim(),
          environment: apnsEnvironment,
        },
      });
      setP8Key("");
      await load();
      setSuccess("APNs quedó cifrado y conectado. La clave ya no se muestra aquí.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo conectar APNs.");
    } finally {
      setBusy(false);
    }
  }

  async function saveFcm() {
    setBusy(true);
    setError(null);
    setSuccess(null);
    try {
      await savePushCredentials({
        fcm: { service_account_json: fcmJson.trim() },
      });
      setFcmJson("");
      await load();
      setSuccess("FCM quedó cifrado y conectado. El JSON ya no se muestra aquí.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo conectar FCM.");
    } finally {
      setBusy(false);
    }
  }

  async function togglePreference(key: keyof PushPreferences, checked: boolean) {
    const previous = preferences;
    setPreferences({ ...preferences, [key]: checked });
    setError(null);
    try {
      setPreferences(await updatePushPreferences({ [key]: checked }));
    } catch (err) {
      setPreferences(previous);
      setError(err instanceof Error ? err.message : "No se pudo guardar esa preferencia.");
    }
  }

  async function disconnect() {
    if (!window.confirm("¿Desconectar APNs y FCM? Los avisos locales seguirán funcionando.")) {
      return;
    }
    setBusy(true);
    setError(null);
    setSuccess(null);
    try {
      await disconnectPushCredentials();
      await load();
      setSuccess("Push remoto desconectado. Los avisos locales y la Actividad siguen disponibles.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo desconectar el push remoto.");
    } finally {
      setBusy(false);
    }
  }

  const connected = Boolean(status?.apns || status?.fcm);

  return (
    <Card className="lg:col-span-2">
      <CardHeader
        title="Avisos en tus teléfonos"
        description="Conecta tu propia cuenta de Apple o Firebase. Edecan cifra las credenciales y sigue guardando cada resultado en Actividad aunque un push falle."
        actions={
          <Badge variant={connected ? "success" : "neutral"}>
            {connected ? "Push remoto activo" : "Avisos locales"}
          </Badge>
        }
      />
      <CardBody className="space-y-5">
        {error && <Alert variant="error">{error}</Alert>}
        {success && <Alert variant="success">{success}</Alert>}

        <div className="grid gap-3 sm:grid-cols-3">
          <StatusItem label="iPhone y iPad" ready={Boolean(status?.apns)} />
          <StatusItem label="Android" ready={Boolean(status?.fcm)} />
          <div className="rounded-xl border border-slate-200 p-3 dark:border-slate-700">
            <p className="text-xs text-slate-500 dark:text-slate-400">Teléfonos registrados</p>
            <p className="mt-1 text-lg font-semibold text-slate-900 dark:text-slate-100">
              {loading ? "…" : status?.devices_con_token ?? 0}
            </p>
          </div>
        </div>

        <div>
          <p className="text-sm font-semibold text-slate-900 dark:text-slate-100">
            Qué quieres recibir
          </p>
          <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {PREFERENCE_LABELS.map((item) => (
              <Checkbox
                key={item.key}
                checked={preferences[item.key]}
                disabled={loading}
                onChange={(event) =>
                  void togglePreference(item.key, event.target.checked)
                }
                label={item.label}
              />
            ))}
          </div>
        </div>

        <details className="rounded-xl border border-slate-200 p-4 dark:border-slate-700">
          <summary className="cursor-pointer text-sm font-semibold text-slate-900 dark:text-slate-100">
            Configurar iPhone o iPad con APNs
          </summary>
          <div className="mt-4 space-y-4">
            <p className="text-sm leading-6 text-slate-600 dark:text-slate-300">
              Activa Push Notifications para el Bundle ID de tu app y crea una key APNs en{" "}
              <a
                href="https://developer.apple.com/account/resources/authkeys/list"
                target="_blank"
                rel="noreferrer"
                className="text-brand-700 underline dark:text-brand-300"
              >
                Apple Developer
              </a>
              . Edecan no necesita tu contraseña ni tu cuenta completa.
            </p>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <Field label="Team ID" htmlFor="push_team_id">
                <Input
                  id="push_team_id"
                  value={teamId}
                  onChange={(event) => setTeamId(event.target.value)}
                  autoComplete="off"
                />
              </Field>
              <Field label="Key ID" htmlFor="push_key_id">
                <Input
                  id="push_key_id"
                  value={keyId}
                  onChange={(event) => setKeyId(event.target.value)}
                  autoComplete="off"
                />
              </Field>
              <Field label="Bundle ID exacto" htmlFor="push_bundle_id">
                <Input
                  id="push_bundle_id"
                  value={bundleId}
                  onChange={(event) => setBundleId(event.target.value)}
                  placeholder="com.tuempresa.edecan"
                  autoComplete="off"
                />
              </Field>
              <Field label="Entorno" htmlFor="push_apns_environment">
                <Select
                  id="push_apns_environment"
                  value={apnsEnvironment}
                  onChange={(event) =>
                    setApnsEnvironment(event.target.value as "production" | "sandbox")
                  }
                >
                  <option value="production">Producción</option>
                  <option value="sandbox">Desarrollo</option>
                </Select>
              </Field>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <Button
                type="button"
                variant="secondary"
                onClick={() => p8Input.current?.click()}
              >
                Elegir archivo .p8
              </Button>
              <span className="text-xs text-slate-500 dark:text-slate-400">
                {p8Key ? "Clave cargada, todavía no guardada" : "Ninguna clave cargada"}
              </span>
              <input
                ref={p8Input}
                type="file"
                accept=".p8,text/plain"
                className="hidden"
                onChange={(event) => void readSecretFile(event, setP8Key)}
              />
            </div>
            <Button
              type="button"
              onClick={() => void saveApns()}
              loading={busy}
              disabled={!teamId.trim() || !keyId.trim() || !bundleId.trim() || !p8Key.trim()}
            >
              Guardar APNs cifrado
            </Button>
          </div>
        </details>

        <details className="rounded-xl border border-slate-200 p-4 dark:border-slate-700">
          <summary className="cursor-pointer text-sm font-semibold text-slate-900 dark:text-slate-100">
            Configurar Android con Firebase
          </summary>
          <div className="mt-4 space-y-4">
            <p className="text-sm leading-6 text-slate-600 dark:text-slate-300">
              Descarga una clave privada de cuenta de servicio desde{" "}
              <a
                href="https://console.firebase.google.com/"
                target="_blank"
                rel="noreferrer"
                className="text-brand-700 underline dark:text-brand-300"
              >
                Firebase
              </a>
              . No subas este JSON al repositorio ni lo incluyas dentro del APK.
            </p>
            <div className="flex flex-wrap items-center gap-3">
              <Button
                type="button"
                variant="secondary"
                onClick={() => fcmInput.current?.click()}
              >
                Elegir service account JSON
              </Button>
              <span className="text-xs text-slate-500 dark:text-slate-400">
                {fcmJson ? "JSON cargado, todavía no guardado" : "Ningún JSON cargado"}
              </span>
              <input
                ref={fcmInput}
                type="file"
                accept="application/json,.json"
                className="hidden"
                onChange={(event) => void readSecretFile(event, setFcmJson)}
              />
            </div>
            <Textarea
              value={fcmJson}
              onChange={(event) => setFcmJson(event.target.value)}
              placeholder="También puedes pegar aquí el JSON completo."
              autoComplete="off"
              spellCheck={false}
              className="font-mono text-xs"
            />
            <Button
              type="button"
              onClick={() => void saveFcm()}
              loading={busy}
              disabled={!fcmJson.trim()}
            >
              Guardar FCM cifrado
            </Button>
          </div>
        </details>

        {connected && (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-slate-50 p-4 dark:bg-slate-900">
            <p className="text-xs leading-5 text-slate-500 dark:text-slate-400">
              Desconectar no elimina los avisos locales ni el historial de Actividad.
            </p>
            <Button
              type="button"
              variant="danger"
              size="sm"
              onClick={() => void disconnect()}
              disabled={busy}
            >
              Desconectar push remoto
            </Button>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

function StatusItem({ label, ready }: { label: string; ready: boolean }) {
  return (
    <div className="rounded-xl border border-slate-200 p-3 dark:border-slate-700">
      <p className="text-sm font-medium text-slate-900 dark:text-slate-100">{label}</p>
      <p
        className={`mt-1 text-xs ${
          ready
            ? "text-emerald-700 dark:text-emerald-300"
            : "text-slate-500 dark:text-slate-400"
        }`}
      >
        {ready ? "Conectado" : "No conectado"}
      </p>
    </div>
  );
}
