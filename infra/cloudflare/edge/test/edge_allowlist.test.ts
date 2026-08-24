import { env } from "cloudflare:workers";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  DEVICE_EDGE_ROUTES,
  deviceMayReachEdgeRoute,
  handleEdgeRequest
} from "../src/index";

const TEST_JWT_SECRET = "test-jwt-secret-that-is-long-enough-for-hs256";
const TEST_DESKTOP_CAPABILITY = "test-desktop-capability-that-is-long-enough";
const TEST_AWS_KEY = "test-aws-capability-that-is-long-enough";
const AWS_ORIGIN = "http://127.0.0.1:9000";
const LOCAL_ORIGIN = "http://127.0.0.1:8000";

function base64Url(value: string): string {
  return btoa(value).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

async function accessToken(): Promise<string> {
  const now = Math.floor(Date.now() / 1000);
  const header = base64Url(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const payload = base64Url(JSON.stringify({
    sub: crypto.randomUUID(),
    ten: crypto.randomUUID(),
    plan: "free",
    typ: "access",
    iat: now,
    exp: now + 300,
    jti: crypto.randomUUID(),
    sid: crypto.randomUUID()
  }));
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(TEST_JWT_SECRET),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const signature = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(`${header}.${payload}`)
  );
  return `${header}.${payload}.${base64Url(
    String.fromCharCode(...new Uint8Array(signature))
  )}`;
}

function testEnvironment(): Env {
  return {
    ENVIRONMENT: "local",
    LOCAL_ORIGIN_URL: LOCAL_ORIGIN,
    LOCAL_ORIGIN_KEY: TEST_DESKTOP_CAPABILITY,
    AWS_ORIGIN_URL: AWS_ORIGIN,
    AWS_ORIGIN_KEY: TEST_AWS_KEY,
    JWT_VERIFICATION_SECRET: TEST_JWT_SECRET,
    IDE_SESSION_CONTINUITY: env.IDE_SESSION_CONTINUITY
  };
}

function deviceRequest(
  path: string,
  token: string,
  init: RequestInit = {}
): Request {
  const headers = new Headers(init.headers);
  headers.set("authorization", `Bearer ${token}`);
  if (init.body) {
    headers.set("content-type", "application/json");
  }
  return new Request(`https://edge.example.test${path}`, { ...init, headers });
}

// El plano de control completo que `/v1/edge/` expone hoy en AWS. Es de la
// computadora, que llega directo a API Gateway con su secreto compartido; un
// token de dispositivo no debe alcanzarlo ni siquiera para provocar un error.
const DESKTOP_CONTROL_PLANE: ReadonlyArray<{ method: string; path: string }> = [
  { method: "GET", path: "/v1/edge/status" },
  { method: "POST", path: "/v1/edge/heartbeat" },
  { method: "POST", path: "/v1/edge/goodbye" },
  { method: "POST", path: "/v1/edge/jobs" },
  { method: "POST", path: "/v1/edge/jobs/claim" },
  { method: "POST", path: "/v1/edge/jobs/complete" }
];

describe("puertas de /v1/edge/ para un token de dispositivo", () => {
  let outbound: ReturnType<typeof vi.spyOn>;
  let calls: Request[];

  beforeEach(() => {
    calls = [];
    outbound = vi.spyOn(globalThis, "fetch").mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        calls.push(new Request(input as RequestInfo, init));
        return Response.json({ ok: true }, { status: 200 });
      }
    );
  });

  afterEach(() => {
    outbound.mockRestore();
  });

  it("esconde el plano de control de la computadora tras el mismo 404 anónimo", async () => {
    const token = await accessToken();
    for (const route of DESKTOP_CONTROL_PLANE) {
      const response = await handleEdgeRequest(
        deviceRequest(route.path, token, {
          method: route.method,
          ...(route.method === "POST" ? { body: "{}" } : {})
        }),
        testEnvironment()
      );
      expect(response.status, `${route.method} ${route.path}`).toBe(404);
      await expect(response.json()).resolves.toEqual({ error: "not_found" });
    }
    // Ni un viaje a AWS: la ruta prohibida no deja rastro del otro lado.
    expect(calls).toHaveLength(0);
  });

  it("deja pasar el chat y el estado de un trabajo, sin dejar elegir la instalación", async () => {
    const token = await accessToken();
    const jobId = crypto.randomUUID();

    const chat = await handleEdgeRequest(
      deviceRequest("/v1/edge/chat", token, {
        method: "POST",
        body: JSON.stringify({ message: "¿Me lees?" }),
        headers: { "x-edecan-installation": "instalacion-ajena" }
      }),
      testEnvironment()
    );
    expect(chat.status).toBe(200);

    const status = await handleEdgeRequest(
      deviceRequest(`/v1/edge/jobs/${jobId}`, token, {
        headers: { "x-edecan-installation": "instalacion-ajena" }
      }),
      testEnvironment()
    );
    expect(status.status).toBe(200);

    expect(calls).toHaveLength(2);
    expect(calls[0].url).toBe(`${AWS_ORIGIN}/v1/edge/chat`);
    expect(calls[1].url).toBe(`${AWS_ORIGIN}/v1/edge/jobs/${jobId}`);
    for (const call of calls) {
      expect(call.headers.get("authorization")).toBe(`Bearer ${TEST_AWS_KEY}`);
      expect(call.headers.get("x-edecan-device-authorization")).toBe(`Bearer ${token}`);
      // El dispositivo no elige de qué instalación lee: AWS usa la suya.
      expect(call.headers.get("x-edecan-installation")).toBeNull();
    }
  });

  it("acepta el identificador de trabajo tal como lo publica el Worker", async () => {
    const token = await accessToken();
    const jobId = crypto.randomUUID().toUpperCase();
    const response = await handleEdgeRequest(
      deviceRequest(`/v1/edge/jobs/${jobId}`, token),
      testEnvironment()
    );
    expect(response.status).toBe(200);
    expect(calls).toHaveLength(1);
  });

  it("exige el método exacto y un identificador de trabajo real", async () => {
    const token = await accessToken();
    const jobId = crypto.randomUUID();
    const rejected: ReadonlyArray<{ method: string; path: string }> = [
      { method: "GET", path: "/v1/edge/chat" },
      { method: "PUT", path: "/v1/edge/chat" },
      { method: "DELETE", path: "/v1/edge/chat" },
      { method: "POST", path: `/v1/edge/jobs/${jobId}` },
      { method: "DELETE", path: `/v1/edge/jobs/${jobId}` },
      // Sin el molde exacto, "claim" y "complete" también parecen un job_id.
      { method: "GET", path: "/v1/edge/jobs/claim" },
      { method: "GET", path: "/v1/edge/jobs/complete" },
      { method: "GET", path: "/v1/edge/jobs" },
      { method: "GET", path: `/v1/edge/jobs/${jobId}/result` },
      { method: "GET", path: `/v1/edge/jobs/${jobId}%2f..%2fclaim` },
      { method: "POST", path: "/v1/edge/chat/../heartbeat" },
      // El molde tolera el UUID en mayúsculas, pero no el literal `jobs`: si lo
      // tolerara, una ruta que la lista blanca nunca abrió saldría igual hacia
      // AWS con la credencial de la instalación entera.
      { method: "GET", path: `/v1/edge/JOBS/${jobId}` },
      { method: "GET", path: `/v1/edge/Jobs/${jobId}` }
    ];
    for (const route of rejected) {
      const response = await handleEdgeRequest(
        deviceRequest(route.path, token, {
          method: route.method,
          ...(route.method === "POST" || route.method === "PUT"
            ? { body: "{}" }
            : {})
        }),
        testEnvironment()
      );
      expect(response.status, `${route.method} ${route.path}`).toBe(404);
    }
    expect(calls).toHaveLength(0);
  });

  it("cierra por omisión cualquier ruta que nadie haya decidido", async () => {
    const token = await accessToken();
    const inventadas = [
      "/v1/edge/",
      "/v1/edge/admin",
      "/v1/edge/jobs/claim-all",
      "/v1/edge/devices/revoke",
      "/v1/edge/secrets",
      "/v1/edge/chat/stream",
      `/v1/edge/${crypto.randomUUID()}`,
      `/v1/edge/${crypto.randomUUID()}/${crypto.randomUUID()}`
    ];
    for (const path of inventadas) {
      for (const method of ["GET", "POST", "PUT", "PATCH", "DELETE"]) {
        const response = await handleEdgeRequest(
          deviceRequest(path, token, {
            method,
            ...(method === "GET" || method === "DELETE" ? {} : { body: "{}" })
          }),
          testEnvironment()
        );
        expect(response.status, `${method} ${path}`).toBe(404);
      }
    }
    expect(calls).toHaveLength(0);
  });

  // Este test falla a propósito cuando alguien agrega una ruta a la lista
  // blanca: abrirle una puerta más a un token de dispositivo tiene que ser una
  // decisión escrita y revisada, no un efecto colateral.
  it("la lista blanca es lo único que abre, y su contenido es exactamente este", () => {
    expect(
      DEVICE_EDGE_ROUTES.map((route) => `${route.method} ${route.pattern.source}`)
    ).toEqual([
      "POST ^\\/v1\\/edge\\/chat$",
      "GET ^\\/v1\\/edge\\/jobs\\/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
        + "-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    ]);
    for (const route of DEVICE_EDGE_ROUTES) {
      expect(route.pattern.source.startsWith("^")).toBe(true);
      expect(route.pattern.source.endsWith("$")).toBe(true);
      // Sin banderas: `i` aflojaría también los literales de la ruta, y lo que
      // el molde tolere del identificador tiene que decirlo el molde.
      expect(route.pattern.flags).toBe("");
      expect(route.purpose.length).toBeGreaterThan(0);
    }
    for (const route of DESKTOP_CONTROL_PLANE) {
      expect(deviceMayReachEdgeRoute(route.method, route.path)).toBe(false);
    }
  });

  it("no le corta nada a lo que ya pasaba por el Worker", async () => {
    const token = await accessToken();
    const conversationId = crypto.randomUUID();

    // El turno de chat del teléfono sigue yendo al origen local con su propia
    // autorización intacta: ahí el origen vuelve a validar la sesión.
    const chat = await handleEdgeRequest(
      deviceRequest(`/v1/conversations/${conversationId}/messages`, token, {
        method: "POST",
        body: JSON.stringify({ text: "Hola" })
      }),
      testEnvironment()
    );
    expect(chat.status).toBe(200);
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe(
      `${LOCAL_ORIGIN}/v1/conversations/${conversationId}/messages`
    );
    expect(calls[0].headers.get("authorization")).toBe(`Bearer ${token}`);
    expect(calls[0].headers.get("x-edecan-edge-key")).toBe(TEST_DESKTOP_CAPABILITY);

    // Y la continuidad IDE, el único camino de la computadora que sí pasa por
    // aquí, se resuelve en el Durable Object sin tocar AWS.
    const sessionId = crypto.randomUUID();
    const state = await handleEdgeRequest(
      deviceRequest(`/v1/edge/ide/sessions/${sessionId}/state`, token, {
        method: "PUT",
        headers: { "x-edecan-desktop-capability": TEST_DESKTOP_CAPABILITY },
        body: JSON.stringify({
          update_id: crypto.randomUUID(),
          status: "running",
          summary: "La lista blanca no toca la continuidad IDE.",
          desktop_connected: true
        })
      }),
      testEnvironment()
    );
    expect(state.status).toBe(200);
    expect(calls).toHaveLength(1);
  });

  it("no deja que el que llama inyecte las credenciales de los orígenes", async () => {
    const token = await accessToken();
    const forjadas = {
      "x-edecan-edge-key": "clave-forjada",
      "x-edecan-device-authorization": "Bearer token-de-otro",
      "x-edecan-installation": "instalacion-ajena"
    };

    const local = await handleEdgeRequest(
      deviceRequest(`/v1/conversations/${crypto.randomUUID()}/messages`, token, {
        method: "POST",
        headers: forjadas,
        body: JSON.stringify({ text: "Hola" })
      }),
      testEnvironment()
    );
    expect(local.status).toBe(200);
    expect(calls[0].headers.get("x-edecan-edge-key")).toBe(TEST_DESKTOP_CAPABILITY);
    // También hacia el origen local. Hoy la computadora no lee estas dos, pero
    // el día que las lea tiene que poder confiar en que las escribió el Worker.
    expect(calls[0].headers.get("x-edecan-device-authorization")).toBeNull();
    expect(calls[0].headers.get("x-edecan-installation")).toBeNull();

    const aws = await handleEdgeRequest(
      deviceRequest("/v1/edge/chat", token, {
        method: "POST",
        headers: forjadas,
        body: JSON.stringify({ message: "Hola" })
      }),
      testEnvironment()
    );
    expect(aws.status).toBe(200);
    expect(calls[1].headers.get("x-edecan-device-authorization")).toBe(
      `Bearer ${token}`
    );
  });
});
