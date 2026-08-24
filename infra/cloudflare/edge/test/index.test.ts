import { env, exports } from "cloudflare:workers";
import { describe, expect, it } from "vitest";
import {
  continuitySseResponse,
  targetFor,
  verifyAccessToken
} from "../src/index";

function base64Url(value: string): string {
  return btoa(value).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

async function accessToken(secret: string, expirationOffsetSeconds = 300): Promise<string> {
  const now = Math.floor(Date.now() / 1000);
  const header = base64Url(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const payload = base64Url(JSON.stringify({
    sub: crypto.randomUUID(),
    ten: crypto.randomUUID(),
    plan: "free",
    typ: "access",
    iat: now,
    exp: now + expirationOffsetSeconds,
    jti: crypto.randomUUID(),
    sid: crypto.randomUUID()
  }));
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
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

describe("Edecán Edge", () => {
  it("oculta todas las rutas públicas", async () => {
    for (const path of ["/", "/healthz", "/v1/me", "/app/ajustes"]) {
      const response = await exports.default.fetch(`https://example.test${path}`);
      expect(response.status).toBe(404);
      expect(response.headers.get("cache-control")).toContain("no-store");
      await expect(response.json()).resolves.toEqual({ error: "not_found" });
    }
  });

  it("rechaza bearer vacío, corto o mal formado sin revelar autenticación", async () => {
    for (const authorization of ["", "Basic abc", "Bearer ", "Bearer too-short"]) {
      const response = await exports.default.fetch("https://example.test/v1/me", {
        headers: authorization ? { authorization } : undefined
      });
      expect(response.status).toBe(404);
    }
  });

  it("no revela configuración aunque el visitante invente un bearer", async () => {
    expect(env.ENVIRONMENT).toBe("local");
    const response = await exports.default.fetch("https://example.test/v1/me", {
      headers: { authorization: `Bearer ${"a".repeat(32)}` }
    });
    expect(response.status).toBe(404);
    await expect(response.json()).resolves.toEqual({ error: "not_found" });
  });

  it("valida únicamente access tokens HS256 vigentes de esta instalación", async () => {
    const secret = "test-jwt-secret-that-is-long-enough-for-hs256";
    const valid = await accessToken(secret);
    const expired = await accessToken(secret, -1);
    expect(await verifyAccessToken(valid, secret)).toBe(true);
    expect(await verifyAccessToken(valid, `${secret}-wrong`)).toBe(false);
    expect(await verifyAccessToken(expired, secret)).toBe(false);
  });

  it("solo deja pasar claim y refresh de emparejamiento por POST", async () => {
    for (const path of [
      "/v1/devices/pairing/claim",
      "/v1/devices/pairing/refresh"
    ]) {
      const getResponse = await exports.default.fetch(`https://example.test${path}`);
      expect(getResponse.status).toBe(404);
      const postResponse = await exports.default.fetch(`https://example.test${path}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: "{}"
      });
      expect(postResponse.status).toBe(404);
    }
  });

  it("no permite preflight anónimo que revele capacidades", async () => {
    const response = await exports.default.fetch("https://example.test/v1/me", {
      method: "OPTIONS",
      headers: {
        origin: "https://attacker.example",
        "access-control-request-method": "GET"
      }
    });
    expect(response.status).toBe(404);
    expect(response.headers.get("access-control-allow-origin")).toBeNull();
  });

  it("adapta la continuidad al mismo SSE que ya consumen iOS y Android", async () => {
    const response = continuitySseResponse("Respuesta segura", "request-id", {
      mode: "queued_local",
      jobId: "job-id"
    });
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toContain("text/event-stream");
    expect(response.headers.get("x-edecan-continuity-mode")).toBe("queued_local");
    expect(response.headers.get("x-edecan-continuity-job-id")).toBe("job-id");
    const body = await response.text();
    expect(body).toContain("event: message.delta");
    expect(body).toContain('"text":"Respuesta segura"');
    expect(body).toContain("event: message.done");
  });

  it("conserva un stage privado al construir el origen AWS", () => {
    const target = targetFor(
      new URL("https://edge.example.test/v1/edge/status"),
      {
        LOCAL_ORIGIN_URL: "https://local.example.test",
        AWS_ORIGIN_URL: "https://aws.example.test/private-stage"
      } as Env
    );
    expect(target?.kind).toBe("aws");
    expect(target?.url.toString()).toBe(
      "https://aws.example.test/private-stage/v1/edge/status"
    );
  });
});
