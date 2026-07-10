"use client";

import { Suspense, useEffect, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { CheckIcon, ChevronDownIcon, KeyIcon, PlugIcon, TrashIcon } from "@/components/icons";
import {
  Alert,
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  Field,
  FullPageSpinner,
  Input,
  PageHeader,
  Select,
  Spinner,
} from "@/components/ui";
import {
  connectBotTokenCredentials,
  connectTwilioCredentials,
  connectWhatsappCredentials,
  deleteConnectorAppCredentials,
  disconnectConnector,
  getConnectorAuthorizeUrl,
  grantConsent,
  listConnectors,
  putConnectorAppCredentials,
} from "@/lib/api";
import type { ConnectorListItem } from "@/lib/types";

function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

const TWILIO_CONNECTOR_KEY = "twilio";

// Mismas claves reservadas no-OAuth que `BOT_TOKEN_CONNECTOR_KEYS` /
// `WHATSAPP_CONNECTOR_KEY` en `edecan_api/routers/connectors.py`: ninguna
// vive en `CONNECTORS` (registry OAuth), así que no pueden pasar por el botón
// genérico "Conectar" → `GET /{key}/authorize` (ese endpoint devuelve 404
// para ellas a propósito).
const BOT_TOKEN_CONNECTOR_KEYS = ["telegram", "discord"];
const WHATSAPP_CONNECTOR_KEY = "whatsapp";

const TWILIO_FORM_INITIAL = { account_sid: "", auth_token: "", phone_number: "" };
const WHATSAPP_FORM_INITIAL = { access_token: "", phone_number_id: "" };

const CONSENT_FORM_INITIAL: { phone_e164: string; kind: "sms" | "voice"; source: string } = {
  phone_e164: "",
  kind: "sms",
  source: "",
};

function ConnectedAccountsList({
  connectorKey,
  accounts,
  disconnectingId,
  onDisconnect,
  emptyLabel,
}: {
  connectorKey: string;
  accounts: ConnectorListItem["accounts"];
  disconnectingId: string | null;
  onDisconnect: (key: string, accountId: string) => void;
  emptyLabel: string;
}) {
  if (accounts.length === 0) {
    return <p className="text-sm text-slate-400">{emptyLabel}</p>;
  }
  return (
    <ul className="space-y-2">
      {accounts.map((account) => (
        <li
          key={account.id}
          className="flex items-center justify-between gap-2 rounded-lg border border-slate-100 px-3 py-2 dark:border-slate-800"
        >
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-slate-700 dark:text-slate-200">
              {account.display_name || account.external_account_id || "Cuenta conectada"}
            </p>
            <Badge variant={account.status === "active" ? "success" : "neutral"}>{account.status}</Badge>
          </div>
          <button
            onClick={() => onDisconnect(connectorKey, account.id)}
            disabled={disconnectingId === account.id}
            className="shrink-0 rounded-md p-1.5 text-slate-400 hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-950/40"
            aria-label="Desconectar"
          >
            {disconnectingId === account.id ? (
              <Spinner className="h-3.5 w-3.5" />
            ) : (
              <TrashIcon className="h-3.5 w-3.5" />
            )}
          </button>
        </li>
      ))}
    </ul>
  );
}

/** Tarjeta no-OAuth para Telegram/Discord: token del bot pegado a mano
 * (`PUT /v1/connectors/{key}/credentials`), mismo patrón que la tarjeta de
 * Twilio de abajo — nunca pasa por `getConnectorAuthorizeUrl`.
 */
function BotTokenConnectorCard({
  connector,
  disconnectingId,
  onDisconnect,
  onConnected,
}: {
  connector: ConnectorListItem;
  disconnectingId: string | null;
  onDisconnect: (key: string, accountId: string) => void;
  onConnected: () => Promise<void>;
}) {
  const [botToken, setBotToken] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await connectBotTokenCredentials(connector.key, { bot_token: botToken });
      setBotToken("");
      await onConnected();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : `No se pudo conectar el bot de ${connector.display_name}.`,
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <PlugIcon className="h-4 w-4 text-brand-600" />
            {connector.display_name}
          </span>
        }
        description="Token del bot del tenant (no OAuth): se cifra en el TokenVault, nunca en variables de entorno."
      />
      <CardBody className="space-y-4">
        <ConnectedAccountsList
          connectorKey={connector.key}
          accounts={connector.accounts}
          disconnectingId={disconnectingId}
          onDisconnect={onDisconnect}
          emptyLabel="Sin bot conectado."
        />
        <form onSubmit={handleSubmit} className="space-y-3 border-t border-slate-100 pt-4 dark:border-slate-800">
          {error && <Alert variant="error">{error}</Alert>}
          <Field label="Token del bot" htmlFor={`bot_token_${connector.key}`}>
            <Input
              id={`bot_token_${connector.key}`}
              type="password"
              value={botToken}
              onChange={(e) => setBotToken(e.target.value)}
              placeholder={`Token del bot de ${connector.display_name}`}
              autoComplete="off"
              required
            />
          </Field>
          <Button type="submit" size="sm" loading={submitting}>
            Conectar bot
          </Button>
        </form>
      </CardBody>
    </Card>
  );
}

/** Tarjeta no-OAuth para WhatsApp Business Platform (WP-V3-13): access token
 * permanente + phone_number_id pegados a mano
 * (`PUT /v1/connectors/whatsapp/credentials`), mismo patrón que Twilio —
 * nunca pasa por `getConnectorAuthorizeUrl`.
 */
function WhatsAppConnectorCard({
  connector,
  disconnectingId,
  onDisconnect,
  onConnected,
}: {
  connector: ConnectorListItem;
  disconnectingId: string | null;
  onDisconnect: (key: string, accountId: string) => void;
  onConnected: () => Promise<void>;
}) {
  const [form, setForm] = useState(WHATSAPP_FORM_INITIAL);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await connectWhatsappCredentials(form);
      setForm(WHATSAPP_FORM_INITIAL);
      await onConnected();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo conectar la cuenta de WhatsApp.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <PlugIcon className="h-4 w-4 text-brand-600" />
            {connector.display_name}
          </span>
        }
        description="Access token permanente + phone_number_id de tu app de Meta (no OAuth): se cifran en el TokenVault."
      />
      <CardBody className="space-y-4">
        <ConnectedAccountsList
          connectorKey={connector.key}
          accounts={connector.accounts}
          disconnectingId={disconnectingId}
          onDisconnect={onDisconnect}
          emptyLabel="Sin número de WhatsApp conectado."
        />
        <form onSubmit={handleSubmit} className="space-y-3 border-t border-slate-100 pt-4 dark:border-slate-800">
          {error && <Alert variant="error">{error}</Alert>}
          <Field label="Access token" htmlFor="whatsapp_access_token">
            <Input
              id="whatsapp_access_token"
              type="password"
              value={form.access_token}
              onChange={(e) => setForm({ ...form, access_token: e.target.value })}
              placeholder="Access token permanente de tu app de Meta"
              autoComplete="off"
              required
            />
          </Field>
          <Field label="Phone number ID" htmlFor="whatsapp_phone_number_id">
            <Input
              id="whatsapp_phone_number_id"
              value={form.phone_number_id}
              onChange={(e) => setForm({ ...form, phone_number_id: e.target.value })}
              placeholder="ID numérico del número de WhatsApp Business"
              autoComplete="off"
              required
            />
          </Field>
          <Button type="submit" size="sm" loading={submitting}>
            Conectar WhatsApp
          </Button>
        </form>
      </CardBody>
    </Card>
  );
}

/** Formulario "trae tu propia app OAuth" (§10.2): requisito PREVIO a poder
 * pulsar "Conectar" en cualquier conector OAuth — antes de esto no existía
 * ningún lugar en la UI donde pegar el client_id/client_secret que el
 * backend (`PUT /{key}/app-credentials`) ya esperaba, así que "Conectar"
 * siempre fallaba con 400 sin que el tenant pudiera resolverlo.
 */
function OAuthAppCredentialsSection({
  connector,
  onChanged,
}: {
  connector: ConnectorListItem;
  onChanged: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await putConnectorAppCredentials(connector.key, {
        client_id: clientId,
        client_secret: clientSecret || undefined,
      });
      setClientId("");
      setClientSecret("");
      setOpen(false);
      await onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo guardar la app OAuth.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleRemove() {
    setRemoving(true);
    setError(null);
    try {
      await deleteConnectorAppCredentials(connector.key);
      await onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo quitar la app OAuth.");
    } finally {
      setRemoving(false);
    }
  }

  async function handleCopyRedirectUri() {
    if (!connector.oauth_redirect_uri) return;
    try {
      await navigator.clipboard.writeText(connector.oauth_redirect_uri);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // portapapeles no disponible (p. ej. sin permiso) — sin acción, el
      // valor sigue visible para copiar a mano.
    }
  }

  return (
    <div className="space-y-2 border-t border-slate-100 pt-3 dark:border-slate-800">
      {error && <Alert variant="error">{error}</Alert>}
      {connector.app_configured ? (
        <div className="flex items-center justify-between gap-2">
          <p className="flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400">
            <KeyIcon className="h-3.5 w-3.5 shrink-0" />
            App OAuth configurada — client_id {connector.app_client_id_masked}
          </p>
          <div className="flex shrink-0 items-center gap-2">
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              className="text-xs text-brand-600 hover:underline dark:text-brand-400"
            >
              Editar
            </button>
            <button
              type="button"
              onClick={handleRemove}
              disabled={removing}
              className="text-xs text-rose-600 hover:underline disabled:opacity-50 dark:text-rose-400"
            >
              {removing ? "Quitando…" : "Quitar"}
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex w-full items-center justify-between gap-2 text-xs font-medium text-brand-600 hover:underline dark:text-brand-400"
        >
          <span className="flex items-center gap-1.5">
            <KeyIcon className="h-3.5 w-3.5" />
            Configurar app OAuth (requerido antes de conectar)
          </span>
          <ChevronDownIcon className={cx("h-3.5 w-3.5 transition-transform", open && "rotate-180")} />
        </button>
      )}
      {open && (
        <form onSubmit={handleSubmit} className="space-y-3 rounded-lg bg-slate-50 p-3 dark:bg-slate-800/50">
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Registra tu propia app OAuth de {connector.display_name} en la consola del proveedor y pega
            aquí el client_id (y client_secret, si aplica). Usa esta URL de redirección al registrarla:
          </p>
          {connector.oauth_redirect_uri && (
            <div className="flex items-center gap-2">
              <code className="min-w-0 flex-1 truncate rounded bg-white px-2 py-1 text-[0.7rem] text-slate-600 dark:bg-slate-900 dark:text-slate-300">
                {connector.oauth_redirect_uri}
              </code>
              <Button type="button" size="sm" variant="secondary" onClick={handleCopyRedirectUri}>
                {copied ? "¡Copiado!" : "Copiar"}
              </Button>
            </div>
          )}
          <Field label="Client ID" htmlFor={`app_client_id_${connector.key}`}>
            <Input
              id={`app_client_id_${connector.key}`}
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
              placeholder="Client ID de tu app OAuth"
              autoComplete="off"
              required
            />
          </Field>
          <Field
            label="Client secret"
            htmlFor={`app_client_secret_${connector.key}`}
            hint="Déjalo vacío si tu app es pública / solo usa PKCE (p. ej. algunas apps de X)."
          >
            <Input
              id={`app_client_secret_${connector.key}`}
              type="password"
              value={clientSecret}
              onChange={(e) => setClientSecret(e.target.value)}
              placeholder="Client secret (opcional según el proveedor)"
              autoComplete="off"
            />
          </Field>
          <div className="flex items-center gap-2">
            <Button type="submit" size="sm" loading={submitting}>
              Guardar app OAuth
            </Button>
            <Button type="button" size="sm" variant="secondary" onClick={() => setOpen(false)}>
              Cancelar
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}

/** Tarjeta para conectores OAuth genéricos (Google/Microsoft/Meta/X/YouTube/
 * Slack): "Conectar" queda deshabilitado hasta que `OAuthAppCredentialsSection`
 * reporte `app_configured`, para no repetir el mismo 400 documentado en
 * `edecan_api/routers/connectors.py::authorize`.
 */
function OAuthConnectorCard({
  connector,
  connecting,
  disconnectingId,
  onConnect,
  onDisconnect,
  onChanged,
}: {
  connector: ConnectorListItem;
  connecting: boolean;
  disconnectingId: string | null;
  onConnect: (key: string) => void;
  onDisconnect: (key: string, accountId: string) => void;
  onChanged: () => Promise<void>;
}) {
  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <PlugIcon className="h-4 w-4 text-brand-600" />
            {connector.display_name}
          </span>
        }
        actions={
          <Button
            size="sm"
            onClick={() => onConnect(connector.key)}
            loading={connecting}
            disabled={!connector.app_configured}
            title={connector.app_configured ? undefined : "Configura tu app OAuth primero"}
          >
            Conectar
          </Button>
        }
      />
      <CardBody className="space-y-3">
        <ConnectedAccountsList
          connectorKey={connector.key}
          accounts={connector.accounts}
          disconnectingId={disconnectingId}
          onDisconnect={onDisconnect}
          emptyLabel="Sin cuentas conectadas."
        />
        <OAuthAppCredentialsSection connector={connector} onChanged={onChanged} />
      </CardBody>
    </Card>
  );
}

export default function ConectoresPage() {
  return (
    <Suspense fallback={<FullPageSpinner />}>
      <ConectoresContent />
    </Suspense>
  );
}

function ConectoresContent() {
  const [connectors, setConnectors] = useState<ConnectorListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [connectingKey, setConnectingKey] = useState<string | null>(null);
  const [disconnectingId, setDisconnectingId] = useState<string | null>(null);
  const [twilioForm, setTwilioForm] = useState(TWILIO_FORM_INITIAL);
  const [twilioSubmitting, setTwilioSubmitting] = useState(false);
  const [twilioError, setTwilioError] = useState<string | null>(null);
  const [consentForm, setConsentForm] = useState(CONSENT_FORM_INITIAL);
  const [consentSubmitting, setConsentSubmitting] = useState(false);
  const [consentError, setConsentError] = useState<string | null>(null);
  const [consentSuccess, setConsentSuccess] = useState<string | null>(null);

  const router = useRouter();
  const searchParams = useSearchParams();
  const ok = searchParams.get("ok");

  useEffect(() => {
    void load();
    if (ok) {
      router.replace("/app/conectores");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setConnectors(await listConnectors());
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudieron cargar los conectores.");
    } finally {
      setLoading(false);
    }
  }

  async function handleConnect(key: string) {
    setConnectingKey(key);
    setError(null);
    try {
      const { url } = await getConnectorAuthorizeUrl(key);
      window.location.href = url;
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo iniciar la conexión.");
      setConnectingKey(null);
    }
  }

  async function handleDisconnect(key: string, accountId: string) {
    setDisconnectingId(accountId);
    setError(null);
    try {
      await disconnectConnector(key, accountId);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo desconectar la cuenta.");
    } finally {
      setDisconnectingId(null);
    }
  }

  async function handleConnectTwilio(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setTwilioSubmitting(true);
    setTwilioError(null);
    try {
      await connectTwilioCredentials(twilioForm);
      setTwilioForm(TWILIO_FORM_INITIAL);
      await load();
    } catch (err) {
      setTwilioError(err instanceof Error ? err.message : "No se pudo conectar la cuenta de Twilio.");
    } finally {
      setTwilioSubmitting(false);
    }
  }

  async function handleGrantConsent(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setConsentSubmitting(true);
    setConsentError(null);
    setConsentSuccess(null);
    try {
      const granted = await grantConsent(consentForm);
      setConsentSuccess(
        `Consentimiento de ${granted.kind === "sms" ? "SMS" : "llamada"} registrado para ${granted.phone_e164}.`,
      );
      setConsentForm({ ...CONSENT_FORM_INITIAL, kind: consentForm.kind });
    } catch (err) {
      setConsentError(err instanceof Error ? err.message : "No se pudo registrar el consentimiento.");
    } finally {
      setConsentSubmitting(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Conectores"
        description="Google, Microsoft y sociales (Meta, X, YouTube) vía OAuth 2.0, y Twilio (Account SID + Auth Token) para telefonía — cada tenant conecta su propia cuenta."
      />
      {ok === "1" && (
        <div className="mb-4">
          <Alert variant="success">Cuenta conectada correctamente.</Alert>
        </div>
      )}
      {error && (
        <div className="mb-4">
          <Alert variant="error">{error}</Alert>
        </div>
      )}
      {loading ? (
        <div className="flex justify-center py-16">
          <Spinner className="h-6 w-6 text-slate-400" />
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {connectors.map((connector) => {
            if (BOT_TOKEN_CONNECTOR_KEYS.includes(connector.key)) {
              return (
                <BotTokenConnectorCard
                  key={connector.key}
                  connector={connector}
                  disconnectingId={disconnectingId}
                  onDisconnect={handleDisconnect}
                  onConnected={load}
                />
              );
            }
            if (connector.key === WHATSAPP_CONNECTOR_KEY) {
              return (
                <WhatsAppConnectorCard
                  key={connector.key}
                  connector={connector}
                  disconnectingId={disconnectingId}
                  onDisconnect={handleDisconnect}
                  onConnected={load}
                />
              );
            }
            return connector.key === TWILIO_CONNECTOR_KEY ? (
              <Card key={connector.key}>
                <CardHeader
                  title={
                    <span className="flex items-center gap-2">
                      <PlugIcon className="h-4 w-4 text-brand-600" />
                      {connector.display_name}
                    </span>
                  }
                  description="Account SID + Auth Token del tenant (no OAuth): se cifran en el TokenVault, nunca en variables de entorno."
                />
                <CardBody className="space-y-4">
                  <ConnectedAccountsList
                    connectorKey={connector.key}
                    accounts={connector.accounts}
                    disconnectingId={disconnectingId}
                    onDisconnect={handleDisconnect}
                    emptyLabel="Sin números conectados."
                  />
                  <form
                    onSubmit={handleConnectTwilio}
                    className="space-y-3 border-t border-slate-100 pt-4 dark:border-slate-800"
                  >
                    {twilioError && <Alert variant="error">{twilioError}</Alert>}
                    <Field label="Account SID" htmlFor="twilio_account_sid">
                      <Input
                        id="twilio_account_sid"
                        value={twilioForm.account_sid}
                        onChange={(e) => setTwilioForm({ ...twilioForm, account_sid: e.target.value })}
                        placeholder="ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                        autoComplete="off"
                        required
                      />
                    </Field>
                    <Field label="Auth Token" htmlFor="twilio_auth_token">
                      <Input
                        id="twilio_auth_token"
                        type="password"
                        value={twilioForm.auth_token}
                        onChange={(e) => setTwilioForm({ ...twilioForm, auth_token: e.target.value })}
                        placeholder="Auth Token de Twilio"
                        autoComplete="off"
                        required
                      />
                    </Field>
                    <Field label="Número de teléfono" htmlFor="twilio_phone_number" hint="Formato E.164, p. ej. +525512345678.">
                      <Input
                        id="twilio_phone_number"
                        value={twilioForm.phone_number}
                        onChange={(e) => setTwilioForm({ ...twilioForm, phone_number: e.target.value })}
                        placeholder="+525512345678"
                        autoComplete="off"
                        required
                      />
                    </Field>
                    <Button type="submit" size="sm" loading={twilioSubmitting}>
                      Conectar número
                    </Button>
                  </form>
                </CardBody>
              </Card>
            ) : (
              <OAuthConnectorCard
                key={connector.key}
                connector={connector}
                connecting={connectingKey === connector.key}
                disconnectingId={disconnectingId}
                onConnect={handleConnect}
                onDisconnect={handleDisconnect}
                onChanged={load}
              />
            );
          })}
        </div>
      )}
      <Card className="mt-4">
        <CardHeader
          title={
            <span className="flex items-center gap-2">
              <CheckIcon className="h-4 w-4 text-brand-600" />
              Consentimiento de contacto
            </span>
          }
          description="Registra el consentimiento verificable de un número antes de contactarlo por SMS o llamada — sin esto, tu asistente bloquea cualquier mensaje o llamada a ese número."
        />
        <CardBody>
          <form onSubmit={handleGrantConsent} className="space-y-3">
            {consentError && <Alert variant="error">{consentError}</Alert>}
            {consentSuccess && <Alert variant="success">{consentSuccess}</Alert>}
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="Número de teléfono" htmlFor="consent_phone" hint="Formato E.164, p. ej. +525512345678.">
                <Input
                  id="consent_phone"
                  value={consentForm.phone_e164}
                  onChange={(e) => setConsentForm({ ...consentForm, phone_e164: e.target.value })}
                  placeholder="+525512345678"
                  autoComplete="off"
                  required
                />
              </Field>
              <Field label="Tipo de contacto" htmlFor="consent_kind">
                <Select
                  id="consent_kind"
                  value={consentForm.kind}
                  onChange={(e) => setConsentForm({ ...consentForm, kind: e.target.value as "sms" | "voice" })}
                >
                  <option value="sms">SMS</option>
                  <option value="voice">Llamada de voz</option>
                </Select>
              </Field>
            </div>
            <Field
              label="Origen del consentimiento"
              htmlFor="consent_source"
              hint='Evidencia verificable de cómo lo obtuviste, p. ej. "formulario_web" o "respuesta_sms:SI".'
            >
              <Input
                id="consent_source"
                value={consentForm.source}
                onChange={(e) => setConsentForm({ ...consentForm, source: e.target.value })}
                placeholder="formulario_web"
                autoComplete="off"
                required
              />
            </Field>
            <Button type="submit" size="sm" loading={consentSubmitting}>
              Registrar consentimiento
            </Button>
          </form>
        </CardBody>
      </Card>
    </div>
  );
}
