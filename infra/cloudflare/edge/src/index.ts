import { hiddenNotFound, jsonResponse, securityHeaders } from "./http";
import {
  handleIdeContinuityRequest,
  ideObjectName
} from "./ide_continuity_route";

const API_PREFIX = "/v1/";
const EDGE_PREFIX = "/v1/edge/";
const IDE_CLOUD_PREFIX = "/v1/edge/ide/";
const CHAT_MESSAGE_PATH = /^\/v1\/conversations\/([0-9a-f-]{36})\/messages$/i;
const PUBLIC_PAIRING_PATHS = new Set([
  "/v1/devices/pairing/claim",
  "/v1/devices/pairing/refresh"
]);
const ALLOWED_METHODS = new Set(["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]);
// El molde acepta el hexadecimal en cualquier caja porque hay clientes que
// escriben el UUID en mayúsculas, pero el molde y no la bandera `i` del patrón:
// esa bandera también aflojaría el literal `jobs`, y entonces `/v1/edge/JOBS/…`
// saldría hacia AWS a preguntar por una ruta que la lista blanca nunca abrió.
const JOB_ID = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";

/**
 * Lo único que un token de dispositivo puede pedirle a AWS por este Worker.
 *
 * Un teléfono emparejado y la computadora del dueño no son la misma cosa y no
 * pueden abrir las mismas puertas. Bajo `/v1/edge/` el Worker reemplaza la
 * autorización de quien llama por `AWS_ORIGIN_KEY`, así que todo lo que deje
 * pasar llega a AWS con los permisos de la instalación entera: el teléfono solo
 * puede pedir cosas PARA SÍ —conversar y mirar su propio trabajo—. El plano de
 * control (latido, despedida, encolar, reclamar y completar) es de la
 * computadora, que habla directo con API Gateway con su propio secreto
 * compartido y nunca pasa por aquí.
 *
 * Es lista blanca y no lista negra a propósito: una ruta que alguien agregue
 * mañana en AWS queda cerrada mientras nadie decida de qué lado va, que es el
 * lado correcto del error.
 */
const DEVICE_EDGE_ROUTES: ReadonlyArray<{
  method: string;
  pattern: RegExp;
  purpose: string;
}> = [
  {
    method: "POST",
    pattern: /^\/v1\/edge\/chat$/,
    purpose: "conversar cuando la computadora no contesta"
  },
  {
    method: "GET",
    pattern: new RegExp(`^/v1/edge/jobs/${JOB_ID}$`),
    purpose: "seguir el trabajo que este Worker le devolvió al encolar"
  }
];

function deviceMayReachEdgeRoute(method: string, pathname: string): boolean {
  return DEVICE_EDGE_ROUTES.some(
    (route) => route.method === method && route.pattern.test(pathname)
  );
}

export interface AccessClaims {
  exp: number;
  iat: number;
  typ: "access";
  sub: string;
  ten: string;
  sid: string;
  jti: string;
}

function bearerToken(request: Request): string | null {
  const authorization = request.headers.get("authorization")?.trim() ?? "";
  if (!authorization.toLowerCase().startsWith("bearer ")) {
    return null;
  }
  const value = authorization.slice(7).trim();
  return value.length >= 20 && value.length <= 8192 ? value : null;
}

function decodeBase64Url(value: string): ArrayBuffer {
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "=");
  const decoded = atob(padded);
  return Uint8Array.from(
    decoded,
    (character) => character.charCodeAt(0)
  ).buffer as ArrayBuffer;
}

async function authenticateAccessToken(
  token: string,
  secret: string,
  now = Date.now()
): Promise<AccessClaims | null> {
  try {
    const parts = token.split(".");
    if (parts.length !== 3 || !secret || secret.length < 32) {
      return null;
    }
    const header = JSON.parse(new TextDecoder().decode(decodeBase64Url(parts[0]))) as {
      alg?: unknown;
      typ?: unknown;
    };
    const claims = JSON.parse(new TextDecoder().decode(decodeBase64Url(parts[1]))) as {
      exp?: unknown;
      iat?: unknown;
      typ?: unknown;
      sub?: unknown;
      ten?: unknown;
      sid?: unknown;
      jti?: unknown;
    };
    if (
      header.alg !== "HS256"
      || claims.typ !== "access"
      || typeof claims.exp !== "number"
      || typeof claims.iat !== "number"
      || claims.exp <= Math.floor(now / 1000)
      || claims.iat > Math.floor(now / 1000) + 60
      || typeof claims.sub !== "string"
      || typeof claims.ten !== "string"
      || typeof claims.sid !== "string"
      || typeof claims.jti !== "string"
    ) {
      return null;
    }
    const key = await crypto.subtle.importKey(
      "raw",
      new TextEncoder().encode(secret),
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["verify"]
    );
    const valid = await crypto.subtle.verify(
      "HMAC",
      key,
      decodeBase64Url(parts[2]),
      new TextEncoder().encode(`${parts[0]}.${parts[1]}`)
    );
    return valid ? claims as AccessClaims : null;
  } catch {
    return null;
  }
}

async function verifyAccessToken(token: string, secret: string, now = Date.now()): Promise<boolean> {
  return await authenticateAccessToken(token, secret, now) !== null;
}

function targetFor(url: URL, env: Env): { url: URL; kind: "aws" | "local" } | null {
  const useAws = url.pathname.startsWith(EDGE_PREFIX);
  const rawOrigin = useAws ? env.AWS_ORIGIN_URL : env.LOCAL_ORIGIN_URL;
  if (!rawOrigin) {
    return null;
  }
  const target = new URL(rawOrigin);
  target.pathname = `${target.pathname.replace(/\/$/, "")}${url.pathname}`;
  target.search = url.search;
  return { url: target, kind: useAws ? "aws" : "local" };
}

function continuitySseResponse(
  message: string,
  requestId: string,
  options: { mode: string; jobId?: string } = { mode: "offline_continuity" }
): Response {
  const headers = securityHeaders(requestId);
  headers.set("content-type", "text/event-stream; charset=utf-8");
  headers.set("x-accel-buffering", "no");
  headers.set("x-edecan-continuity-mode", options.mode);
  if (options.jobId) {
    headers.set("x-edecan-continuity-job-id", options.jobId);
  }
  const delta = `event: message.delta\ndata: ${JSON.stringify({
    type: "text_delta",
    text: message
  })}\n\n`;
  const done = `event: message.done\ndata: ${JSON.stringify({
    type: "done",
    usage: {}
  })}\n\n`;
  return new Response(`${delta}${done}`, { status: 200, headers });
}

function awsTarget(path: string, env: Env): URL | null {
  if (!env.AWS_ORIGIN_URL || !env.AWS_ORIGIN_KEY) {
    return null;
  }
  const target = new URL(env.AWS_ORIGIN_URL);
  target.pathname = `${target.pathname.replace(/\/$/, "")}${path}`;
  target.search = "";
  return target;
}

function awsHeaders(request: Request, env: Env, requestId: string): Headers {
  const headers = new Headers({
    accept: "application/json",
    "content-type": "application/json",
    authorization: `Bearer ${env.AWS_ORIGIN_KEY}`,
    "x-edecan-edge-request-id": requestId,
    "x-edecan-edge-environment": env.ENVIRONMENT
  });
  const deviceAuthorization = request.headers.get("authorization");
  if (deviceAuthorization) {
    headers.set("x-edecan-device-authorization", deviceAuthorization);
  }
  return headers;
}

async function pollContinuityJob(
  request: Request,
  env: Env,
  requestId: string,
  jobId: string,
  maximumWaitMs = 25_000
): Promise<{ status: string; result?: Record<string, unknown> } | null> {
  const target = awsTarget(`/v1/edge/jobs/${jobId}`, env);
  if (!target) {
    return null;
  }
  const deadline = Date.now() + maximumWaitMs;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    let response: Response;
    try {
      response = await fetch(target, {
        method: "GET",
        headers: awsHeaders(request, env, requestId),
        redirect: "manual"
      });
    } catch {
      return null;
    }
    if (!response.ok) {
      return null;
    }
    const payload = await response.json() as {
      job?: { status?: unknown; result?: unknown };
    };
    const status = typeof payload.job?.status === "string"
      ? payload.job.status
      : "";
    if (status === "done" || status === "error") {
      const result = payload.job?.result;
      return {
        status,
        result: result && typeof result === "object"
          ? result as Record<string, unknown>
          : undefined
      };
    }
  }
  return { status: "queued" };
}

async function fallbackChatToContinuity(
  request: Request,
  env: Env,
  requestId: string,
  requestBody: ArrayBuffer
): Promise<Response | null> {
  const match = new URL(request.url).pathname.match(CHAT_MESSAGE_PATH);
  const target = awsTarget("/v1/edge/chat", env);
  if (!match || !target || request.method !== "POST") {
    return null;
  }
  let body: { text?: unknown; attachments?: unknown };
  try {
    body = JSON.parse(new TextDecoder().decode(requestBody)) as typeof body;
  } catch {
    return null;
  }
  const text = typeof body.text === "string" ? body.text.trim() : "";
  const attachments = Array.isArray(body.attachments) ? body.attachments : [];
  if (!text || text.length > 50_000 || attachments.length > 0) {
    return null;
  }

  const idempotencyKey = request.headers.get("idempotency-key");
  let response: Response;
  try {
    response = await fetch(target, {
      method: "POST",
      headers: awsHeaders(request, env, requestId),
      body: JSON.stringify({
        message: text,
        conversation_id: match[1],
        ...(idempotencyKey ? { idempotency_key: idempotencyKey } : {})
      }),
      redirect: "manual"
    });
  } catch {
    return null;
  }
  if (response.status === 401 || response.status === 403) {
    return hiddenNotFound(requestId);
  }
  if (!response.ok && response.status !== 202) {
    return null;
  }
  const payload = await response.json() as {
    mode?: unknown;
    message?: unknown;
    job_id?: unknown;
  };
  if (typeof payload.message === "string") {
    return continuitySseResponse(payload.message, requestId, {
      mode: typeof payload.mode === "string" ? payload.mode : "offline_continuity"
    });
  }
  if (response.status === 202 && typeof payload.job_id === "string") {
    const completed = await pollContinuityJob(
      request,
      env,
      requestId,
      payload.job_id
    );
    const completedMessage = completed?.result?.message;
    if (completed?.status === "done" && typeof completedMessage === "string") {
      return continuitySseResponse(completedMessage, requestId, {
        mode: "queued_local",
        jobId: payload.job_id
      });
    }
    const message = completed?.status === "error"
      ? "La computadora recibió el trabajo, pero no pudo completarlo."
      : "El trabajo quedó seguro en la cola y Edecán lo continuará en tu computadora.";
    return continuitySseResponse(message, requestId, {
      mode: completed?.status === "error" ? "queued_error" : "queued",
      jobId: payload.job_id
    });
  }
  return null;
}

async function proxyAuthenticatedRequest(
  request: Request,
  env: Env,
  requestId: string
): Promise<Response> {
  const incomingUrl = new URL(request.url);
  // La puerta se cierra antes de resolver el origen y antes de tocar la red:
  // lo que no está en la lista blanca no llega a AWS ni deja rastro allá.
  // Mismo 404 genérico que ven los anónimos, porque un 403 confirmaría que la
  // ruta existe.
  if (
    incomingUrl.pathname.startsWith(EDGE_PREFIX)
    && !deviceMayReachEdgeRoute(request.method, incomingUrl.pathname)
  ) {
    return hiddenNotFound(requestId);
  }
  const target = targetFor(incomingUrl, env);
  if (!target) {
    return hiddenNotFound(requestId);
  }

  const headers = new Headers(request.headers);
  headers.delete("cf-connecting-ip");
  headers.delete("cf-ray");
  headers.delete("x-forwarded-for");
  // Las cabeceras de confianza las escribe el Worker o no van. Se borran para
  // TODO destino y no solo para el que hoy las lee: un origen no puede
  // distinguir la que puso el Worker de la que llegó de afuera, y el que
  // empiece a leerlas mañana heredaría el agujero sin enterarse.
  headers.delete("x-edecan-edge-key");
  headers.delete("x-edecan-installation");
  headers.delete("x-edecan-device-authorization");
  headers.set("x-edecan-edge-request-id", requestId);
  headers.set("x-edecan-edge-environment", env.ENVIRONMENT);

  if (target.kind === "local" && env.LOCAL_ORIGIN_KEY) {
    headers.set("x-edecan-edge-key", env.LOCAL_ORIGIN_KEY);
  }
  if (target.kind === "aws" && env.AWS_ORIGIN_KEY) {
    // AWS parte sus datos por instalación con `x-edecan-installation`. Al no
    // mandarla, el que llama no elige de qué instalación lee su trabajo y AWS
    // usa la suya, igual que en las llamadas que este Worker arma solo
    // (`awsHeaders`). Cubre la cabecera, no el cuerpo: ver el README.
    const deviceAuthorization = headers.get("authorization");
    if (deviceAuthorization) {
      headers.set("x-edecan-device-authorization", deviceAuthorization);
    }
    headers.set("authorization", `Bearer ${env.AWS_ORIGIN_KEY}`);
  }

  const canFallbackChat = (
    target.kind === "local"
    && request.method === "POST"
    && CHAT_MESSAGE_PATH.test(incomingUrl.pathname)
  );
  const fallbackBody = canFallbackChat
    ? await request.clone().arrayBuffer()
    : null;

  let response: Response;
  try {
    response = await fetch(target.url, {
      method: request.method,
      headers,
      body: request.body,
      redirect: "manual"
    });
  } catch {
    if (fallbackBody) {
      const fallback = await fallbackChatToContinuity(
        request,
        env,
        requestId,
        fallbackBody
      );
      if (fallback) {
        return fallback;
      }
    }
    return jsonResponse(
      503,
      { error: "origin_unavailable", request_id: requestId },
      requestId
    );
  }
  if (
    fallbackBody
    && (response.status === 502 || response.status === 503 || response.status === 504)
  ) {
    const fallback = await fallbackChatToContinuity(
      request,
      env,
      requestId,
      fallbackBody
    );
    if (fallback) {
      return fallback;
    }
  }

  const responseHeaders = new Headers(response.headers);
  if (response.status === 401 || response.status === 403) {
    return hiddenNotFound(requestId);
  }
  for (const [name, value] of securityHeaders(requestId)) {
    responseHeaders.set(name, value);
  }
  responseHeaders.delete("server");
  responseHeaders.delete("x-powered-by");
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers: responseHeaders
  });
}

async function handleEdgeRequest(request: Request, env: Env): Promise<Response> {
  const requestId = request.headers.get("x-request-id")?.slice(0, 128)
    || crypto.randomUUID();
  const url = new URL(request.url);

  if (!ALLOWED_METHODS.has(request.method)) {
    return hiddenNotFound(requestId);
  }

  if (request.method === "OPTIONS") {
    return hiddenNotFound(requestId);
  }

  if (!url.pathname.startsWith(API_PREFIX)) {
    return hiddenNotFound(requestId);
  }

  // El callback OAuth de un conector (`/v1/connectors/<key>/callback`) lo abre
  // el NAVEGADOR al volver del proveedor (LinkedIn, etc.): no trae token de
  // dispositivo. Se auto-autentica con el `state` corto y firmado que emitió
  // `authorize`, así que el edge lo deja pasar al origen local (donde el gate
  // del backend también lo exime, ver `TunnelDeviceGuardMiddleware`). Sin esto
  // el edge lo tumba con 404 y la conexión de conectores nunca completa.
  const isConnectorCallback = /^\/v1\/connectors\/[^/]+\/callback$/.test(url.pathname);

  let claims: AccessClaims | null = null;
  if (isConnectorCallback) {
    // Pasa sin token; el origen local valida el `state`.
  } else if (!PUBLIC_PAIRING_PATHS.has(url.pathname)) {
    const token = bearerToken(request);
    claims = token
      ? await authenticateAccessToken(token, env.JWT_VERIFICATION_SECRET)
      : null;
    if (!claims) {
      return hiddenNotFound(requestId);
    }
  } else if (request.method !== "POST") {
    return hiddenNotFound(requestId);
  }

  if (url.pathname.startsWith(IDE_CLOUD_PREFIX)) {
    if (!claims) {
      return hiddenNotFound(requestId);
    }
    return handleIdeContinuityRequest(request, env, requestId, claims);
  }

  return proxyAuthenticatedRequest(request, env, requestId);
}

export default {
  async fetch(request, env): Promise<Response> {
    return handleEdgeRequest(request, env);
  }
} satisfies ExportedHandler<Env>;

export {
  DEVICE_EDGE_ROUTES,
  authenticateAccessToken,
  bearerToken,
  continuitySseResponse,
  deviceMayReachEdgeRoute,
  fallbackChatToContinuity,
  handleEdgeRequest,
  handleIdeContinuityRequest,
  hiddenNotFound,
  ideObjectName,
  targetFor,
  verifyAccessToken
};

export { IdeSessionContinuity } from "./ide_session";
