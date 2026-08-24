import { env } from "cloudflare:workers";
import { evictDurableObject, runInDurableObject } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import { handleEdgeRequest, ideObjectName } from "../src/index";

const TEST_JWT_SECRET = "test-jwt-secret-that-is-long-enough-for-hs256";
const TEST_DESKTOP_CAPABILITY = "test-desktop-capability-that-is-long-enough";

function base64Url(value: string): string {
  return btoa(value).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

async function accessToken(
  identity: { sub?: string; ten?: string; sid?: string } = {}
): Promise<string> {
  const now = Math.floor(Date.now() / 1000);
  const header = base64Url(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const payload = base64Url(JSON.stringify({
    sub: identity.sub ?? crypto.randomUUID(),
    ten: identity.ten ?? crypto.randomUUID(),
    plan: "free",
    typ: "access",
    iat: now,
    exp: now + 300,
    jti: crypto.randomUUID(),
    sid: identity.sid ?? crypto.randomUUID()
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
  const encodedSignature = base64Url(
    String.fromCharCode(...new Uint8Array(signature))
  );
  return `${header}.${payload}.${encodedSignature}`;
}

function testEnvironment(): Env {
  return {
    ENVIRONMENT: "local",
    LOCAL_ORIGIN_URL: "http://127.0.0.1:8000",
    LOCAL_ORIGIN_KEY: TEST_DESKTOP_CAPABILITY,
    AWS_ORIGIN_URL: "http://127.0.0.1:9000",
    AWS_ORIGIN_KEY: "test-aws-capability-that-is-long-enough",
    JWT_VERIFICATION_SECRET: TEST_JWT_SECRET,
    IDE_SESSION_CONTINUITY: env.IDE_SESSION_CONTINUITY
  };
}

function ideRequest(
  path: string,
  token: string,
  init: RequestInit = {}
): Request {
  const headers = new Headers(init.headers);
  headers.set("authorization", `Bearer ${token}`);
  if (init.body) {
    headers.set("content-type", "application/json");
  }
  return new Request(`https://edge.example.test${path}`, {
    ...init,
    headers
  });
}

describe("continuidad durable del IDE", () => {
  it("persiste estado e historial y hace idempotentes los reintentos", async () => {
    const sessionId = crypto.randomUUID();
    const token = await accessToken();
    const path = `/v1/edge/ide/sessions/${sessionId}`;
    const updateId = crypto.randomUUID();
    const stateBody = JSON.stringify({
      update_id: updateId,
      status: "running",
      workspace_label: "Edecán OSS",
      active_file: "src/index.ts",
      branch: "main",
      summary: "Implementando continuidad segura del estudio.",
      progress: 0.35,
      desktop_connected: true
    });

    const withoutDesktop = await handleEdgeRequest(
      ideRequest(`${path}/state`, token, { method: "PUT", body: stateBody }),
      testEnvironment()
    );
    expect(withoutDesktop.status).toBe(404);

    const stateRequest = () => ideRequest(`${path}/state`, token, {
      method: "PUT",
      headers: { "x-edecan-desktop-capability": TEST_DESKTOP_CAPABILITY },
      body: stateBody
    });
    const firstState = await handleEdgeRequest(stateRequest(), testEnvironment());
    expect(firstState.status).toBe(200);
    await expect(firstState.json()).resolves.toMatchObject({
      revision: 1,
      status: "running",
      active_file: "src/index.ts",
      desktop_connected: true
    });
    const repeatedState = await handleEdgeRequest(stateRequest(), testEnvironment());
    await expect(repeatedState.json()).resolves.toMatchObject({ revision: 1 });

    const eventId = crypto.randomUUID();
    const eventRequest = () => ideRequest(`${path}/events`, token, {
      method: "POST",
      headers: { "x-edecan-desktop-capability": TEST_DESKTOP_CAPABILITY },
      body: JSON.stringify({
        event_id: eventId,
        type: "agent.progress",
        payload: {
          message: "Pruebas locales en curso.",
          progress: 0.5,
          phase: "verify"
        }
      })
    });
    const createdEvent = await handleEdgeRequest(eventRequest(), testEnvironment());
    expect(createdEvent.status).toBe(201);
    await expect(createdEvent.json()).resolves.toMatchObject({
      duplicate: false,
      event: { seq: 1, event_id: eventId, type: "agent.progress" }
    });
    const repeatedEvent = await handleEdgeRequest(eventRequest(), testEnvironment());
    expect(repeatedEvent.status).toBe(200);
    await expect(repeatedEvent.json()).resolves.toMatchObject({
      duplicate: true,
      event: { seq: 1 }
    });

    const snapshot = await handleEdgeRequest(
      ideRequest(path, token),
      testEnvironment()
    );
    await expect(snapshot.json()).resolves.toMatchObject({
      revision: 1,
      cursor: 1,
      summary: "Implementando continuidad segura del estudio."
    });
    const history = await handleEdgeRequest(
      ideRequest(`${path}/events?after=0&limit=20`, token),
      testEnvironment()
    );
    await expect(history.json()).resolves.toMatchObject({
      cursor: 1,
      has_more: false,
      events: [{ seq: 1, event_id: eventId }]
    });
  });

  it("reanuda SSE por Last-Event-ID sin repetir eventos ya recibidos", async () => {
    const sessionId = crypto.randomUUID();
    const token = await accessToken();
    const path = `/v1/edge/ide/sessions/${sessionId}`;
    for (const [index, message] of ["Primero", "Segundo"].entries()) {
      const response = await handleEdgeRequest(
        ideRequest(`${path}/events`, token, {
          method: "POST",
          headers: { "x-edecan-desktop-capability": TEST_DESKTOP_CAPABILITY },
          body: JSON.stringify({
            event_id: crypto.randomUUID(),
            type: "agent.message",
            payload: { message, progress: (index + 1) / 2 }
          })
        }),
        testEnvironment()
      );
      expect(response.status).toBe(201);
    }

    const response = await handleEdgeRequest(
      ideRequest(`${path}/stream?limit=20`, token, {
        headers: { "last-event-id": "1" }
      }),
      testEnvironment()
    );
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toContain("text/event-stream");
    const body = await response.text();
    expect(body).toContain("event: ide.snapshot");
    expect(body).not.toContain('"message":"Primero"');
    expect(body).toContain("id: 2");
    expect(body).toContain('"message":"Segundo"');
    expect(body).toContain('"cursor":2');
  });

  it("aísla dueños, sobrevive a evicción y agenda expiración", async () => {
    const sessionId = crypto.randomUUID();
    const tenantId = crypto.randomUUID();
    const ownerId = crypto.randomUUID();
    const otherOwnerId = crypto.randomUUID();
    const ownerToken = await accessToken({ ten: tenantId, sub: ownerId });
    const otherToken = await accessToken({ ten: tenantId, sub: otherOwnerId });
    const path = `/v1/edge/ide/sessions/${sessionId}`;
    const write = await handleEdgeRequest(
      ideRequest(`${path}/state`, ownerToken, {
        method: "PUT",
        headers: { "x-edecan-desktop-capability": TEST_DESKTOP_CAPABILITY },
        body: JSON.stringify({
          update_id: crypto.randomUUID(),
          status: "waiting",
          summary: "Esperando confirmación local.",
          progress: 0.75,
          desktop_connected: true
        })
      }),
      testEnvironment()
    );
    expect(write.status).toBe(200);

    const objectName = await ideObjectName({
      sub: ownerId,
      ten: tenantId
    }, sessionId);
    const stub = env.IDE_SESSION_CONTINUITY.getByName(objectName);
    const beforeEviction = await stub.snapshot();
    expect(beforeEviction.status).toBe("waiting");
    const expiration = await runInDurableObject(
      stub,
      async (_instance, state) => await state.storage.getAlarm()
    );
    expect(expiration).not.toBeNull();
    expect(expiration as number).toBeGreaterThan(
      Date.now() + 6 * 24 * 60 * 60 * 1_000
    );
    await evictDurableObject(stub);
    const restoredSnapshot = await handleEdgeRequest(
      ideRequest(path, ownerToken),
      testEnvironment()
    );
    await expect(restoredSnapshot.json()).resolves.toMatchObject({
      revision: 1,
      status: "waiting",
      summary: "Esperando confirmación local."
    });

    const otherSnapshot = await handleEdgeRequest(
      ideRequest(path, otherToken),
      testEnvironment()
    );
    await expect(otherSnapshot.json()).resolves.toMatchObject({
      revision: 0,
      status: "idle"
    });
  });

  it("jamás acepta terminal, comandos, rutas absolutas ni credenciales", async () => {
    const token = await accessToken();
    const path = `/v1/edge/ide/sessions/${crypto.randomUUID()}`;
    const authenticatedHeaders = {
      "x-edecan-desktop-capability": TEST_DESKTOP_CAPABILITY
    };

    const terminal = await handleEdgeRequest(
      ideRequest(`${path}/terminal`, token, {
        method: "POST",
        headers: authenticatedHeaders,
        body: JSON.stringify({ command: "whoami" })
      }),
      testEnvironment()
    );
    expect(terminal.status).toBe(404);
    const genericCloudTerminal = await handleEdgeRequest(
      ideRequest("/v1/edge/ide/terminal", token, {
        method: "POST",
        headers: authenticatedHeaders,
        body: JSON.stringify({ command: "whoami" })
      }),
      testEnvironment()
    );
    expect(genericCloudTerminal.status).toBe(404);

    const unsafeState = await handleEdgeRequest(
      ideRequest(`${path}/state`, token, {
        method: "PUT",
        headers: authenticatedHeaders,
        body: JSON.stringify({
          update_id: crypto.randomUUID(),
          status: "running",
          active_file: "/Users/example/secreto.txt",
          summary: "api_key=valor-que-no-debe-subir",
          desktop_connected: true
        })
      }),
      testEnvironment()
    );
    expect(unsafeState.status).toBe(404);

    const terminalOutput = await handleEdgeRequest(
      ideRequest(`${path}/events`, token, {
        method: "POST",
        headers: authenticatedHeaders,
        body: JSON.stringify({
          event_id: crypto.randomUUID(),
          type: "agent.message",
          payload: { stdout: "salida arbitraria" }
        })
      }),
      testEnvironment()
    );
    expect(terminalOutput.status).toBe(404);
  });
});
