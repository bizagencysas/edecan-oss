import { hiddenNotFound, jsonResponse, securityHeaders } from "./http";
import {
  IDE_EVENT_TYPES,
  IDE_SESSION_STATUSES,
  type IdeEventInput,
  type IdeEventPayload,
  type IdeStateInput
} from "./ide_session";

const IDE_SESSION_PATH = /^\/v1\/edge\/ide\/sessions\/([A-Za-z0-9_-]{8,128})(?:\/(state|events|stream))?$/;
const IDE_EVENT_TYPE_SET = new Set<string>(IDE_EVENT_TYPES);
const IDE_SESSION_STATUS_SET = new Set<string>(IDE_SESSION_STATUSES);
const SAFE_IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:/ -]{0,159}$/;
const IDE_WRITE_HEADER = "x-edecan-desktop-capability";
const MAX_IDE_BODY_BYTES = 32 * 1024;
const MAX_IDE_EVENTS_PAGE = 200;

export interface IdeActor {
  sub: string;
  ten: string;
}

export async function ideObjectName(
  actor: IdeActor,
  sessionId: string
): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(`${actor.ten}:${actor.sub}:${sessionId}`)
  );
  return Array.from(new Uint8Array(digest), (value) => (
    value.toString(16).padStart(2, "0")
  )).join("");
}

function constantTimeEqual(left: string, right: string): boolean {
  if (left.length > 512 || right.length > 512) {
    return false;
  }
  const leftBytes = new TextEncoder().encode(left);
  const rightBytes = new TextEncoder().encode(right);
  const maximumLength = Math.max(leftBytes.length, rightBytes.length);
  let difference = leftBytes.length ^ rightBytes.length;
  for (let index = 0; index < maximumLength; index += 1) {
    difference |= (leftBytes[index] ?? 0) ^ (rightBytes[index] ?? 0);
  }
  return difference === 0;
}

function desktopIsAuthenticated(request: Request, env: Env): boolean {
  const candidate = request.headers.get(IDE_WRITE_HEADER) ?? "";
  return (
    candidate.length >= 20
    && env.LOCAL_ORIGIN_KEY.length >= 20
    && constantTimeEqual(candidate, env.LOCAL_ORIGIN_KEY)
  );
}

async function readBoundedJson(request: Request): Promise<unknown> {
  const contentLength = request.headers.get("content-length");
  if (
    contentLength
    && (!/^\d+$/.test(contentLength) || Number(contentLength) > MAX_IDE_BODY_BYTES)
  ) {
    throw new Error("invalid_body");
  }
  if (!request.body) {
    throw new Error("invalid_body");
  }
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    total += value.byteLength;
    if (total > MAX_IDE_BODY_BYTES) {
      await reader.cancel();
      throw new Error("invalid_body");
    }
    chunks.push(value);
  }
  const body = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    return JSON.parse(new TextDecoder().decode(body)) as unknown;
  } catch {
    throw new Error("invalid_body");
  }
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return (
    value !== null
    && typeof value === "object"
    && !Array.isArray(value)
    && Object.getPrototypeOf(value) === Object.prototype
  );
}

function hasOnlyKeys(value: Record<string, unknown>, keys: ReadonlySet<string>): boolean {
  return Object.keys(value).every((key) => keys.has(key));
}

function safeOptionalText(
  value: unknown,
  maximumLength: number,
  options: { relativePath?: boolean } = {}
): string | null | undefined {
  if (value === undefined || value === null) {
    return value;
  }
  if (typeof value !== "string") {
    throw new Error("invalid_body");
  }
  const normalized = value.trim();
  if (!normalized || normalized.length > maximumLength || /\0|\r|\n/.test(normalized)) {
    throw new Error("invalid_body");
  }
  if (
    options.relativePath
    && (
      normalized.startsWith("/")
      || normalized.startsWith("~")
      || normalized.split(/[\\/]/).includes("..")
      || /^[A-Za-z]:[\\/]/.test(normalized)
    )
  ) {
    throw new Error("invalid_body");
  }
  return normalized;
}

function containsCredentialLikeValue(value: string): boolean {
  return (
    /-----BEGIN [A-Z ]*PRIVATE KEY-----/.test(value)
    || /\bBearer\s+[A-Za-z0-9._~+/=-]{16,}/i.test(value)
    || /\b(?:sk|rk|pk)[_-](?:proj[_-])?[A-Za-z0-9_-]{16,}\b/.test(value)
    || /\b(?:ghp|github_pat)_[A-Za-z0-9_]{16,}\b/.test(value)
    || /\bAIza[A-Za-z0-9_-]{24,}\b/.test(value)
    || /\bAKIA[0-9A-Z]{16}\b/.test(value)
    || /\b(?:api[_ -]?key|secret|password|credential|authorization)\s*[:=]/i.test(value)
    || /\beyJ[A-Za-z0-9_-]{12,}\.eyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b/.test(value)
  );
}

function parseProgress(value: unknown): number | null | undefined {
  if (value === undefined || value === null) {
    return value;
  }
  if (
    typeof value !== "number"
    || !Number.isFinite(value)
    || value < 0
    || value > 1
  ) {
    throw new Error("invalid_body");
  }
  return value;
}

function parseStateInput(value: unknown): IdeStateInput {
  if (
    !isPlainObject(value)
    || !hasOnlyKeys(value, new Set([
      "update_id",
      "status",
      "workspace_label",
      "active_file",
      "branch",
      "summary",
      "progress",
      "desktop_connected"
    ]))
    || typeof value.update_id !== "string"
    || !/^[A-Za-z0-9_-]{16,128}$/.test(value.update_id)
    || typeof value.status !== "string"
    || !IDE_SESSION_STATUS_SET.has(value.status)
    || typeof value.desktop_connected !== "boolean"
  ) {
    throw new Error("invalid_body");
  }
  const workspaceLabel = safeOptionalText(value.workspace_label, 160);
  const activeFile = safeOptionalText(value.active_file, 500, { relativePath: true });
  const branch = safeOptionalText(value.branch, 160);
  const summary = safeOptionalText(value.summary, 4_000);
  if (
    [workspaceLabel, activeFile, branch, summary].some(
      (item) => typeof item === "string" && containsCredentialLikeValue(item)
    )
  ) {
    throw new Error("invalid_body");
  }
  return {
    update_id: value.update_id,
    status: value.status as IdeStateInput["status"],
    workspace_label: workspaceLabel,
    active_file: activeFile,
    branch,
    summary,
    progress: parseProgress(value.progress),
    desktop_connected: value.desktop_connected
  };
}

function parseEventPayload(value: unknown): IdeEventPayload {
  if (
    !isPlainObject(value)
    || !hasOnlyKeys(value, new Set([
      "message",
      "progress",
      "status",
      "path",
      "tool",
      "phase"
    ]))
  ) {
    throw new Error("invalid_body");
  }
  if (
    value.message === null
    || value.progress === null
    || value.status === null
    || value.path === null
    || value.tool === null
    || value.phase === null
  ) {
    throw new Error("invalid_body");
  }
  const message = safeOptionalText(value.message, 4_000);
  const status = safeOptionalText(value.status, 32);
  if (status && !IDE_SESSION_STATUS_SET.has(status)) {
    throw new Error("invalid_body");
  }
  const tool = safeOptionalText(value.tool, 120);
  const phase = safeOptionalText(value.phase, 120);
  const path = safeOptionalText(value.path, 500, { relativePath: true });
  if ((tool && !SAFE_IDENTIFIER.test(tool)) || (phase && !SAFE_IDENTIFIER.test(phase))) {
    throw new Error("invalid_body");
  }
  if (
    [message, status, path, tool, phase].some(
      (item) => typeof item === "string" && containsCredentialLikeValue(item)
    )
  ) {
    throw new Error("invalid_body");
  }
  const progress = value.progress === undefined
    ? undefined
    : parseProgress(value.progress);
  return {
    ...(message ? { message } : {}),
    ...(typeof progress === "number" ? { progress } : {}),
    ...(status ? { status: status as IdeEventPayload["status"] } : {}),
    ...(path ? { path } : {}),
    ...(tool ? { tool } : {}),
    ...(phase ? { phase } : {})
  };
}

function parseEventInput(value: unknown): IdeEventInput {
  if (
    !isPlainObject(value)
    || !hasOnlyKeys(value, new Set(["event_id", "type", "payload"]))
    || typeof value.event_id !== "string"
    || !/^[A-Za-z0-9_-]{16,128}$/.test(value.event_id)
    || typeof value.type !== "string"
    || !IDE_EVENT_TYPE_SET.has(value.type)
  ) {
    throw new Error("invalid_body");
  }
  return {
    event_id: value.event_id,
    type: value.type as IdeEventInput["type"],
    payload: parseEventPayload(value.payload)
  };
}

function cursorParameter(url: URL, request: Request): number {
  const raw = (
    request.headers.get("last-event-id")
    ?? url.searchParams.get("after")
    ?? "0"
  );
  if (!/^\d{1,15}$/.test(raw)) {
    throw new Error("invalid_cursor");
  }
  const cursor = Number(raw);
  if (!Number.isSafeInteger(cursor) || cursor < 0) {
    throw new Error("invalid_cursor");
  }
  return cursor;
}

function pageLimit(url: URL): number {
  const raw = url.searchParams.get("limit") ?? "100";
  if (!/^\d{1,3}$/.test(raw)) {
    throw new Error("invalid_limit");
  }
  const limit = Number(raw);
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > MAX_IDE_EVENTS_PAGE) {
    throw new Error("invalid_limit");
  }
  return limit;
}

export async function handleIdeContinuityRequest(
  request: Request,
  env: Env,
  requestId: string,
  actor: IdeActor
): Promise<Response> {
  const url = new URL(request.url);
  const match = url.pathname.match(IDE_SESSION_PATH);
  if (!match) {
    return hiddenNotFound(requestId);
  }
  const sessionId = match[1];
  const resource = match[2] ?? "";
  const objectName = await ideObjectName(actor, sessionId);
  const session = env.IDE_SESSION_CONTINUITY.getByName(objectName);

  try {
    if (!resource && request.method === "GET") {
      return jsonResponse(200, await session.snapshot(), requestId);
    }
    if (resource === "events" && request.method === "GET") {
      const after = cursorParameter(url, request);
      const limit = pageLimit(url);
      const events = await session.events(after, limit);
      return jsonResponse(200, {
        events,
        cursor: events.at(-1)?.seq ?? after,
        has_more: events.length === limit
      }, requestId);
    }
    if (resource === "stream" && request.method === "GET") {
      const response = await session.stream(
        cursorParameter(url, request),
        pageLimit(url)
      );
      const headers = new Headers(response.headers);
      for (const [name, value] of securityHeaders(requestId)) {
        headers.set(name, value);
      }
      return new Response(response.body, {
        status: response.status,
        headers
      });
    }
    if (
      (resource === "state" && request.method === "PUT")
      || (resource === "events" && request.method === "POST")
    ) {
      if (!desktopIsAuthenticated(request, env)) {
        return hiddenNotFound(requestId);
      }
      const body = await readBoundedJson(request);
      if (resource === "state") {
        return jsonResponse(
          200,
          await session.replaceState(parseStateInput(body)),
          requestId
        );
      }
      const result = await session.appendEvent(parseEventInput(body));
      return jsonResponse(result.duplicate ? 200 : 201, result, requestId);
    }
  } catch {
    return hiddenNotFound(requestId);
  }
  return hiddenNotFound(requestId);
}
