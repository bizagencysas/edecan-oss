/**
 * Tipos TypeScript que reflejan los contratos de `edecan_schemas` y las
 * formas reales devueltas por los routers de `edecan_api` (ARCHITECTURE.md
 * §10.5, §10.12). Se toman del código real de los routers en
 * `apps/api/edecan_api/routers/*.py` como fuente de verdad, en vez de
 * duplicarlos a mano desde `docs/api.md`.
 */

// --- Auth --------------------------------------------------------------

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

// --- Perfil / tenant -----------------------------------------------------

export interface UserOut {
  id: string;
  email: string;
  is_superadmin: boolean;
  created_at: string;
}

export interface TenantOut {
  id: string;
  name: string;
  slug: string;
  plan_key: string;
  status: string;
  created_at: string;
}

export interface MeOut {
  user: UserOut;
  tenant: TenantOut;
  flags: Record<string, boolean | number>;
}

// --- Persona "nivel Dios" (§10.5) ----------------------------------------

export type RelationshipStyle = "profesional" | "coach" | "amigo" | "romantico";

export interface PersonaConfig {
  nombre_asistente: string;
  idioma: string;
  tono: string;
  formalidad: 0 | 1 | 2 | 3;
  emojis: boolean;
  instrucciones: string;
  rasgos: string[];
  memoria_activada: boolean;
  voice_id: string | null;
  estilo_relacion: RelationshipStyle;
  adulto_confirmado: boolean;
  consentimiento_romantico: boolean;
}

export const PERSONA_DEFAULT: PersonaConfig = {
  nombre_asistente: "Edecán",
  idioma: "es",
  tono: "cálido y profesional",
  formalidad: 1,
  emojis: false,
  instrucciones: "",
  rasgos: [],
  memoria_activada: true,
  voice_id: null,
  estilo_relacion: "profesional",
  adulto_confirmado: false,
  consentimiento_romantico: false,
};

// --- Conversaciones y chat (SSE, §10.7, §9) -------------------------------

export interface ConversationOut {
  id: string;
  title: string | null;
  channel: string;
  created_at: string;
  updated_at: string;
  messages?: MessageOut[];
}

export interface MessageOut {
  id: string;
  role: "system" | "user" | "assistant" | "tool";
  content: { text?: string } | string | null;
  tool_calls: unknown[] | null;
  tokens_in: number;
  tokens_out: number;
  created_at: string;
}

export interface ArtifactRef {
  file_id: string;
  filename: string;
  mime: string | null;
}

export type AgentEvent =
  | { type: "text_delta"; text: string }
  | { type: "tool_start"; name: string; args: Record<string, unknown> }
  | { type: "tool_end"; name: string; result_preview: string; artifacts?: ArtifactRef[] }
  | {
      type: "confirmation_required";
      tool_call_id: string;
      name: string;
      args: Record<string, unknown>;
    }
  | { type: "done"; usage: Record<string, number> }
  | { type: "error"; message: string };

/** Nombre de evento SSE -> se ignora, el `type` embebido en `data` ya basta. */
export const SSE_EVENT_NAMES = [
  "message.delta",
  "tool.start",
  "tool.end",
  "confirmation.required",
  "message.done",
  "error",
] as const;

// --- Memoria (§10.7, §10.3) ------------------------------------------------

export interface MemoryItem {
  id: string;
  kind: string;
  content: string;
  importance: number;
  source: string;
  created_at: string;
}

/** Ítem propuesto por `POST /v1/memory/import/preview` — todavía sin `id`/
 * `created_at` porque no se guardó nada aún. */
export interface MemoryImportItem {
  kind: string;
  content: string;
  importance: number;
  source: string;
}

// --- Conectores (§10.8) ---------------------------------------------------

export interface ConnectorAccount {
  id: string;
  connector_key: string;
  external_account_id: string | null;
  display_name: string | null;
  status: string;
  scopes: string[];
  created_at: string;
}

export interface ConnectorListItem {
  key: string;
  display_name: string;
  accounts: ConnectorAccount[];
  /** Solo presentes en conectores OAuth (ver `CONNECTORS` en el backend) —
   * Twilio/Telegram/Discord/WhatsApp no los traen. */
  app_configured?: boolean;
  app_client_id_masked?: string | null;
  oauth_redirect_uri?: string;
}

// --- Consentimientos y llamadas de telefonía OSS ----------------------------

export interface ConsentOut {
  phone_e164: string;
  kind: "sms" | "voice";
  source: string;
}

export type PhoneCallStatus =
  | "draft"
  | "confirmed"
  | "queued"
  | "ringing"
  | "in_progress"
  | "completed"
  | "failed"
  | "busy"
  | "no_answer"
  | "cancelled";

export interface PhoneCall {
  id: string;
  conversation_id: string;
  direction: "incoming" | "outgoing";
  from_e164: string;
  to_e164: string;
  goal: string;
  status: PhoneCallStatus;
  confirmed_at: string | null;
  started_at: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

// --- Archivos (§10.14) -----------------------------------------------------

export interface FileOut {
  id: string;
  filename: string;
  mime: string;
  size_bytes: number;
  status: "uploaded" | "processing" | "ready" | "error" | string;
  s3_key: string;
  created_at: string;
}

// --- Recordatorios -----------------------------------------------------

export interface Reminder {
  id: string;
  tenant_id: string;
  user_id: string;
  due_at: string;
  rrule: string | null;
  message: string;
  channel: string;
  status: "pending" | "sent" | "cancelled" | string;
  created_at: string;
  updated_at: string;
}

// --- Contactos -----------------------------------------------------------

export interface Contact {
  id: string;
  tenant_id: string;
  user_id: string;
  nombre: string;
  emails: string[];
  phones: string[];
  empresa: string | null;
  notas: string | null;
  tags: string[];
  created_at: string;
  updated_at: string;
}

// --- Importar contactos (Google / iCloud) -----------------------------------

/** `POST /v1/contacts/import/google` — reusa el conector OAuth `google` ya
 * conectado para Gmail/Calendar (ver routers/contacts.py). */
export interface ContactsImportResult {
  importados: number;
  total_google: number;
}

export interface ICloudStatus {
  connected: boolean;
  apple_id: string | null;
}

/** `POST /v1/contacts/import/icloud` — CardDAV, no OAuth. */
export interface ICloudContactsImportResult {
  importados: number;
  total_icloud: number;
}

// --- Finanzas --------------------------------------------------------------

export interface Transaction {
  id: string;
  tenant_id: string;
  user_id: string;
  fecha: string;
  monto: string | number;
  moneda: string;
  categoria: string | null;
  descripcion: string | null;
  cuenta: string | null;
  created_at: string;
  updated_at: string;
}

export interface FinanceSummary {
  ingresos: string | number;
  gastos: string | number;
  neto: string | number;
  num_transacciones: number;
  por_categoria: { categoria: string; total: string | number }[];
  mes: string;
}

export interface StripeStatus {
  connected: boolean;
  masked: string | null;
}

export interface StripeSyncResult {
  sincronizadas: number;
  total_stripe: number;
}

// --- Uso y planes (§10.13) --------------------------------------------------

export interface UsageOut {
  plan_key: string;
  period_start: string;
  usage: Record<string, number>;
  limits: Record<string, number>;
  flags: Record<string, boolean>;
}

export const FLAG_VOICE_WEB = "voice.web";
export const FLAG_VOICE_TELEPHONY = "voice.telephony";
export const FLAG_CONNECTORS_SOCIAL = "connectors.social";
export const FLAG_CAMPAIGNS = "campaigns";
export const FLAG_COMPANION = "companion";
export const FLAG_MODELS_PREMIUM = "models.premium";

export const LIMIT_MESSAGES_PER_DAY = "limits.messages_per_day";
export const LIMIT_VOICE_MINUTES_MONTH = "limits.voice_minutes_month";
export const LIMIT_STORAGE_MB = "limits.storage_mb";
export const LIMIT_PHONE_NUMBERS = "limits.phone_numbers";
export const LIMIT_SEATS = "limits.seats";

export const UNLIMITED = -1;

export const PLAN_LABELS: Record<string, string> = {
  free_selfhost: "Core self-host",
  hosted_basic: "Hosted Básico",
  hosted_pro: "Hosted Pro",
  hosted_business: "Hosted Business",
};
