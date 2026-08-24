import type {
  AgentEvent,
  ArtifactRef,
  ChatAction,
  ChatBlock,
  ChatScreen,
  FlightCardBlock,
  HotelCardBlock,
  LinkPreviewBlock,
  MediaBlock,
  QuestionBlock,
  QuestionOption,
  SourceCitation,
  ChartBlock,
  ChartSeries,
  SourcesBlock,
} from "./types";

const CHAT_SCREEN_PATHS: Record<ChatScreen, string> = {
  assistant: "/app",
  create: "/app/misiones",
  remote: "/app/remoto",
  activity: "/app/actividad",
  settings: "/app/ajustes",
  travel: "/app/viajes",
  orders: "/app/ordenes",
  files: "/app/archivos",
  skills: "/app/skills",
};

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const ACTION_ID_RE = /^[a-zA-Z0-9._:-]+$/;

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function stringValue(value: unknown, maxLength: number): string | null {
  if (typeof value !== "string") return null;
  const clean = value.trim();
  return clean && clean.length <= maxLength ? clean : null;
}

function optionalString(value: unknown, maxLength: number): string | null {
  if (value === null || value === undefined || value === "") return null;
  return stringValue(value, maxLength);
}

function nullableText(value: unknown, maxLength: number): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string" || value.length > maxLength) return null;
  return value;
}

function isPrivateOrReservedIpv4(hostname: string): boolean {
  const parts = hostname.split(".");
  if (parts.length !== 4 || parts.some((part) => !/^\d{1,3}$/.test(part))) return false;
  const octets = parts.map(Number);
  if (octets.some((octet) => octet > 255)) return true;
  const [a, b, c] = octets;
  return (
    a === 0 ||
    a === 10 ||
    a === 127 ||
    (a === 100 && b >= 64 && b <= 127) ||
    (a === 169 && b === 254) ||
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 0) ||
    (a === 192 && b === 168) ||
    (a === 198 && (b === 18 || b === 19)) ||
    (a === 192 && b === 0 && c === 2) ||
    (a === 198 && b === 51 && c === 100) ||
    (a === 203 && b === 0 && c === 113) ||
    a >= 224
  );
}

function isPrivateOrReservedIpv6(hostname: string): boolean {
  const clean = hostname.replace(/^\[|\]$/g, "").toLowerCase();
  if (!clean.includes(":")) return false;
  return (
    clean === "::" ||
    clean === "::1" ||
    clean.startsWith("fc") ||
    clean.startsWith("fd") ||
    /^fe[89ab]/.test(clean) ||
    clean.startsWith("::ffff:0:") ||
    clean.startsWith("2001:db8:")
  );
}

/** Defensa cliente adicional. El backend vuelve a validar y resolver URLs antes de usarlas. */
export function publicHttpUrl(value: unknown): string | null {
  const candidate = stringValue(value, 2048);
  if (!candidate) return null;
  try {
    const url = new URL(candidate);
    const hostname = url.hostname.replace(/\.$/, "").toLowerCase();
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    if (url.username || url.password || !hostname) return null;
    if (
      hostname === "localhost" ||
      hostname.endsWith(".localhost") ||
      hostname.endsWith(".local") ||
      hostname.endsWith(".internal") ||
      isPrivateOrReservedIpv4(hostname) ||
      isPrivateOrReservedIpv6(hostname)
    ) {
      return null;
    }
    return url.toString();
  } catch {
    return null;
  }
}

export function chatScreenPath(screen: ChatScreen): string {
  return CHAT_SCREEN_PATHS[screen];
}

function parseScreen(value: unknown): ChatScreen | null {
  return typeof value === "string" && value in CHAT_SCREEN_PATHS ? (value as ChatScreen) : null;
}

export function parseChatAction(value: unknown): ChatAction | null {
  const raw = record(value);
  if (!raw) return null;
  const id = stringValue(raw.id, 80);
  const label = stringValue(raw.label, 80);
  if (!id || !ACTION_ID_RE.test(id) || !label) return null;
  if (raw.action === "open_url") {
    const url = publicHttpUrl(raw.url);
    return url ? { id, label, action: "open_url", url } : null;
  }
  if (raw.action === "open_screen") {
    const screen = parseScreen(raw.screen);
    return screen ? { id, label, action: "open_screen", screen } : null;
  }
  if (raw.action === "prefill_message") {
    const message = stringValue(raw.message, 2000);
    return message ? { id, label, action: "prefill_message", message } : null;
  }
  return null;
}

function parseActions(value: unknown): ChatAction[] {
  if (!Array.isArray(value)) return [];
  return value.slice(0, 3).flatMap((item) => {
    const action = parseChatAction(item);
    return action ? [action] : [];
  });
}

export function parseArtifactRef(value: unknown): ArtifactRef | null {
  const raw = record(value);
  if (!raw) return null;
  const fileId = stringValue(raw.file_id, 64);
  const rawFilename = stringValue(raw.filename, 255);
  if (!fileId || !UUID_RE.test(fileId) || !rawFilename) return null;
  const filename = rawFilename.replace(/\\/g, "/").split("/").at(-1)?.trim();
  if (!filename) return null;
  return {
    file_id: fileId,
    filename,
    mime: optionalString(raw.mime, 255),
  };
}

export function parseArtifactRefs(value: unknown): ArtifactRef[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  return value.slice(0, 20).flatMap((item) => {
    const artifact = parseArtifactRef(item);
    if (!artifact || seen.has(artifact.file_id)) return [];
    seen.add(artifact.file_id);
    return [artifact];
  });
}

function baseFields(raw: Record<string, unknown>) {
  if (raw.schema_version !== 1) return null;
  return { schema_version: 1 as const, fallback_text: nullableText(raw.fallback_text, 1000) };
}

function sourceMode(value: unknown): "demo" | "live" | "unknown" {
  return value === "demo" || value === "live" ? value : "unknown";
}

export function parseChatBlock(value: unknown): ChatBlock | null {
  const raw = record(value);
  if (!raw) return null;
  const base = baseFields(raw);
  if (!base) return null;

  if (raw.type === "media") {
    const artifact = parseArtifactRef(raw.artifact);
    if (!artifact || (raw.media_kind !== "image" && raw.media_kind !== "video" && raw.media_kind !== "audio")) {
      return null;
    }
    const block: MediaBlock = {
      ...base,
      type: "media",
      media_kind: raw.media_kind,
      artifact,
      alt: nullableText(raw.alt, 1000) ?? "",
      caption: nullableText(raw.caption, 500),
    };
    return block;
  }

  if (raw.type === "link_preview") {
    const url = publicHttpUrl(raw.url);
    const title = stringValue(raw.title, 300);
    if (!url || !title) return null;
    const block: LinkPreviewBlock = {
      ...base,
      type: "link_preview",
      url,
      title,
      description: nullableText(raw.description, 1000),
      site_name: optionalString(raw.site_name, 120),
      observed_at: optionalString(raw.observed_at, 80),
      source_mode: sourceMode(raw.source_mode),
      actions: parseActions(raw.actions),
    };
    return block;
  }

  if (raw.type === "question") {
    const question = stringValue(raw.question, 500);
    if (!question) return null;
    const options: QuestionOption[] = [];
    const vistas = new Set<string>();
    for (const item of Array.isArray(raw.options) ? raw.options : []) {
      const opt = record(item);
      if (!opt) continue;
      const label = stringValue(opt.label, 80);
      // Dos botones con el mismo texto son indistinguibles al tocarlos.
      if (!label || vistas.has(label.toLowerCase())) continue;
      vistas.add(label.toLowerCase());
      options.push({
        label,
        description: nullableText(opt.description, 200),
        value: nullableText(opt.value, 500),
      });
      if (options.length === 4) break;
    }
    // Menos de dos opciones no es una pregunta que se pueda contestar tocando:
    // se descarta el bloque y queda el `fallback_text` del mensaje.
    if (options.length < 2) return null;
    const block: QuestionBlock = {
      ...base,
      type: "question",
      question,
      header: optionalString(raw.header, 40),
      options,
      multi_select: raw.multi_select === true,
      allow_free_text: raw.allow_free_text !== false,
    };
    return block;
  }

  if (raw.type === "sources") {
    const citations: SourceCitation[] = [];
    const seen = new Set<string>();
    for (const item of Array.isArray(raw.citations) ? raw.citations : []) {
      const citation = record(item);
      if (!citation) continue;
      const url = publicHttpUrl(citation.url);
      const title = stringValue(citation.title, 300);
      const id = stringValue(citation.id, 80);
      if (!url || !title || !id || seen.has(url)) continue;
      seen.add(url);
      citations.push({
        id,
        title,
        url,
        source: stringValue(citation.source, 200) ?? "",
        author: optionalString(citation.author, 200),
        date: optionalString(citation.date, 40),
        excerpt: optionalString(citation.excerpt, 1000),
        retrieved_at: optionalString(citation.retrieved_at, 40),
      });
      if (citations.length === 20) break;
    }
    if (citations.length === 0) return null;
    const block: SourcesBlock = { ...base, type: "sources", citations };
    return block;
  }

  if (raw.type === "chart") {
    const title = stringValue(raw.title, 200);
    const series: ChartSeries[] = [];
    for (const item of Array.isArray(raw.series) ? raw.series : []) {
      const point = record(item);
      if (!point) continue;
      const label = stringValue(point.label, 80);
      const value = typeof point.value === "number" && Number.isFinite(point.value) ? point.value : null;
      if (!label || value === null) continue;
      series.push({ label, value });
      if (series.length === 24) break;
    }
    if (!title || series.length === 0) return null;
    const block: ChartBlock = { ...base, type: "chart", title, series };
    return block;
  }

  if (raw.type === "flight") {
    const offerId = stringValue(raw.offer_id, 200);
    const airline = stringValue(raw.airline, 200);
    const origin = stringValue(raw.origin, 12);
    const destination = stringValue(raw.destination, 12);
    const price = stringValue(raw.price, 40);
    const currency = stringValue(raw.currency, 3);
    const stops = Number.isInteger(raw.stops) && Number(raw.stops) >= 0 && Number(raw.stops) <= 12
      ? Number(raw.stops)
      : 0;
    if (!offerId || !airline || !origin || origin.length < 2 || !destination || destination.length < 2 || !price || !currency || !/^[A-Z]{3}$/.test(currency)) {
      return null;
    }
    const block: FlightCardBlock = {
      ...base,
      type: "flight",
      offer_id: offerId,
      airline,
      origin,
      destination,
      departure: optionalString(raw.departure, 80),
      arrival: optionalString(raw.arrival, 80),
      stops,
      price,
      currency,
      source_mode: sourceMode(raw.source_mode),
      provider: optionalString(raw.provider, 120),
      observed_at: optionalString(raw.observed_at, 80),
      expires_at: optionalString(raw.expires_at, 80),
      taxes: optionalString(raw.taxes, 40),
      cancellation: optionalString(raw.cancellation, 500),
      actions: parseActions(raw.actions),
    };
    return block;
  }

  if (raw.type === "hotel") {
    const offerId = stringValue(raw.offer_id, 200);
    const name = stringValue(raw.name, 300);
    const city = stringValue(raw.city, 120);
    const price = stringValue(raw.price, 40);
    const currency = stringValue(raw.currency, 3);
    if (!offerId || !name || !city || !price || !currency || !/^[A-Z]{3}$/.test(currency)) return null;
    const block: HotelCardBlock = {
      ...base,
      type: "hotel",
      offer_id: offerId,
      name,
      city,
      checkin: optionalString(raw.checkin, 40),
      checkout: optionalString(raw.checkout, 40),
      rating: optionalString(raw.rating, 20),
      price,
      currency,
      address: optionalString(raw.address, 300),
      image_url: publicHttpUrl(raw.image_url),
      source_mode: sourceMode(raw.source_mode),
      provider: optionalString(raw.provider, 120),
      observed_at: optionalString(raw.observed_at, 80),
      expires_at: optionalString(raw.expires_at, 80),
      taxes: optionalString(raw.taxes, 40),
      cancellation: optionalString(raw.cancellation, 500),
      actions: parseActions(raw.actions),
    };
    return block;
  }

  return null;
}

export function parseChatBlocks(value: unknown): ChatBlock[] {
  if (!Array.isArray(value)) return [];
  return value.slice(0, 30).flatMap((item) => {
    const block = parseChatBlock(item);
    return block ? [block] : [];
  });
}

function blockIdentity(block: ChatBlock): string {
  switch (block.type) {
    case "media":
      return `media:${block.media_kind}:${block.artifact.file_id}`;
    case "link_preview":
      return `link:${block.url}`;
    case "flight":
      return `flight:${block.offer_id}`;
    case "hotel":
      return `hotel:${block.offer_id}`;
    case "question":
      // La pregunta en sí identifica el bloque: el mismo texto dos veces en un
      // mismo mensaje sería el mismo modal duplicado, y se debe deduplicar.
      return `question:${block.question}`;
    case "sources":
      return `sources:${block.citations.map((citation) => citation.url).join("|")}`;
    case "chart":
      return `chart:${block.title}:${block.series.map((point) => point.label).join("|")}`;
  }
}

/** Recupera resultados ricos persistidos en `messages.tool_calls` tras recargar el chat. */
export function messageBlocks(toolCalls: unknown[] | null): ChatBlock[] {
  if (!Array.isArray(toolCalls)) return [];
  const blocks: ChatBlock[] = [];
  const seen = new Set<string>();
  for (const item of toolCalls) {
    const call = record(item);
    if (!call || call.type !== "tool_end") continue;
    // Una versión futura no se interpreta como v1 por accidente.
    if (call.blocks_version !== undefined && call.blocks_version !== 1) continue;
    for (const block of parseChatBlocks(call.blocks)) {
      const key = blockIdentity(block);
      if (seen.has(key)) continue;
      seen.add(key);
      blocks.push(block);
      if (blocks.length === 60) return blocks;
    }
  }
  return blocks;
}

function parseUsage(value: unknown): Record<string, number> {
  const raw = record(value);
  if (!raw) return {};
  return Object.fromEntries(
    Object.entries(raw).filter((entry): entry is [string, number] => typeof entry[1] === "number" && Number.isFinite(entry[1])),
  );
}

/** Valida cada frame SSE; un frame ajeno o malformado se ignora sin romper el turno. */
export function parseAgentEvent(value: unknown): AgentEvent | null {
  const raw = record(value);
  if (!raw) return null;
  if (raw.type === "text_delta") {
    return typeof raw.text === "string" ? { type: "text_delta", text: raw.text } : null;
  }
  if (raw.type === "tool_start") {
    const name = stringValue(raw.name, 255);
    const args = record(raw.args);
    if (!name || !args) return null;
    return {
      type: "tool_start",
      tool_call_id: optionalString(raw.tool_call_id, 255),
      name,
      args,
    };
  }
  if (raw.type === "tool_progress") {
    const name = stringValue(raw.name, 255);
    const message = stringValue(raw.message, 255);
    const elapsed = typeof raw.elapsed_seconds === "number" && Number.isFinite(raw.elapsed_seconds)
      ? Math.max(0, Math.floor(raw.elapsed_seconds))
      : 0;
    if (!name || !message) return null;
    return {
      type: "tool_progress",
      tool_call_id: optionalString(raw.tool_call_id, 255),
      name,
      elapsed_seconds: elapsed,
      message,
    };
  }
  if (raw.type === "tool_end") {
    const name = stringValue(raw.name, 255);
    if (!name || typeof raw.result_preview !== "string") return null;
    const supportedBlocks = raw.blocks_version === undefined || raw.blocks_version === 1;
    return {
      type: "tool_end",
      tool_call_id: optionalString(raw.tool_call_id, 255),
      name,
      result_preview: raw.result_preview,
      artifacts: parseArtifactRefs(raw.artifacts),
      blocks_version: 1,
      blocks: supportedBlocks ? parseChatBlocks(raw.blocks) : [],
      mission_id: (() => {
        const value = optionalString(raw.mission_id, 64);
        return value && UUID_RE.test(value) ? value : null;
      })(),
    };
  }
  if (raw.type === "confirmation_required") {
    const toolCallId = stringValue(raw.tool_call_id, 255);
    const name = stringValue(raw.name, 255);
    const args = record(raw.args);
    return toolCallId && name && args
      ? { type: "confirmation_required", tool_call_id: toolCallId, name, args }
      : null;
  }
  if (raw.type === "done") {
    const explanation = typeof raw.explanation === "string" ? raw.explanation : undefined;
    return explanation
      ? { type: "done", usage: parseUsage(raw.usage), explanation }
      : { type: "done", usage: parseUsage(raw.usage) };
  }
  if (raw.type === "error") {
    return typeof raw.message === "string" ? { type: "error", message: raw.message } : null;
  }
  return null;
}

export interface ToolTimelineEntry {
  callKey: string;
  toolCallId: string | null;
  name: string;
  args: Record<string, unknown>;
  status: "running" | "done";
  resultPreview?: string;
  elapsedSeconds?: number;
  progressMessage?: string;
  artifacts?: ArtifactRef[];
  missionId?: string;
}

/**
 * La salida de una tool puede contener Markdown porque también alimenta al
 * modelo. La timeline es una traza compacta, no otro mensaje del chat: quita
 * los delimitadores de formato para que nunca muestre `**texto**` literal.
 */
export function plainToolResultPreview(value: string): string {
  return value
    .replace(/```[\s\S]*?```/g, "código generado")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/(\*\*|__|~~)(.*?)\1/g, "$2")
    .replace(/(^|\s)[*_]([^*_\n]+)[*_](?=\s|$|[.,:;!?])/g, "$1$2")
    .replace(/\s+/g, " ")
    .trim();
}

/** Reduce eventos por identidad; el nombre solo se usa como fallback para logs antiguos sin ID. */
export function reduceToolTimeline(
  previous: ToolTimelineEntry[],
  event: Extract<AgentEvent, { type: "tool_start" | "tool_progress" | "tool_end" }>,
): ToolTimelineEntry[] {
  const next = [...previous];
  if (event.type === "tool_start") {
    const existing = event.tool_call_id
      ? next.findIndex((item) => item.toolCallId === event.tool_call_id)
      : -1;
    const entry: ToolTimelineEntry = {
      callKey: event.tool_call_id ? `tool:${event.tool_call_id}` : `legacy:${event.name}:${next.length}`,
      toolCallId: event.tool_call_id,
      name: event.name,
      args: event.args,
      status: "running",
    };
    if (existing >= 0) next[existing] = entry;
    else next.push(entry);
    return next;
  }

  if (event.type === "tool_progress") {
    const index = event.tool_call_id
      ? next.findIndex((item) => item.toolCallId === event.tool_call_id)
      : next.findLastIndex((item) => item.name === event.name && item.status === "running");
    if (index >= 0) {
      next[index] = {
        ...next[index],
        elapsedSeconds: event.elapsed_seconds,
        progressMessage: event.message,
      };
    } else {
      next.push({
        callKey: event.tool_call_id ? `tool:${event.tool_call_id}` : `legacy:${event.name}:${next.length}`,
        toolCallId: event.tool_call_id,
        name: event.name,
        args: {},
        status: "running",
        elapsedSeconds: event.elapsed_seconds,
        progressMessage: event.message,
      });
    }
    return next;
  }

  const index = event.tool_call_id
    ? next.findIndex((item) => item.toolCallId === event.tool_call_id)
    : next.findLastIndex((item) => item.name === event.name && item.status === "running");
  const completed: ToolTimelineEntry = {
    callKey: event.tool_call_id ? `tool:${event.tool_call_id}` : `legacy:${event.name}:${Math.max(index, next.length)}`,
    toolCallId: event.tool_call_id,
    name: event.name,
    args: index >= 0 ? next[index].args : {},
    status: "done",
    resultPreview: event.result_preview,
    artifacts: event.artifacts,
    missionId: event.mission_id ?? undefined,
  };
  if (index >= 0) next[index] = completed;
  else next.push(completed);
  return next;
}

/** Reconstruye la traza persistida de una respuesta al volver a abrir el chat. */
export function toolTimelineFromCalls(calls: unknown[] | null): ToolTimelineEntry[] {
  return (calls ?? []).reduce<ToolTimelineEntry[]>((timeline, raw) => {
    const event = parseAgentEvent(raw);
    if (
      event?.type !== "tool_start" &&
      event?.type !== "tool_progress" &&
      event?.type !== "tool_end"
    ) {
      return timeline;
    }
    return reduceToolTimeline(timeline, event);
  }, []);
}

export function sourceModeLabel(mode: "demo" | "live" | "unknown"): string {
  if (mode === "live") return "Datos en vivo";
  if (mode === "demo") return "Demostración";
  return "Fuente no verificada";
}

export function flightStopsLabel(stops: number): string {
  if (stops === 0) return "Directo";
  return `${stops} ${stops === 1 ? "escala" : "escalas"}`;
}
