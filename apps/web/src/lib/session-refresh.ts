import { createSingleFlight } from "./single-flight";
import {
  clearTokensIfSessionCurrent,
  getDesktopCapability,
  getSessionSnapshot,
  isDesktopApp,
  isSessionSnapshotCurrent,
  setTokensIfSessionCurrent,
  type SessionSnapshot,
} from "./tokens";

const TOTP_REQUIRED_DETAIL = "Se requiere un código TOTP válido para esta cuenta.";

export type RefreshFailureReason =
  | "invalid"
  | "totp_required"
  | "transient"
  | "superseded"
  | "cancelled";

export type RefreshResult =
  | { ok: true; session: SessionSnapshot }
  | { ok: false; reason: RefreshFailureReason };

const runRefresh = createSingleFlight<RefreshResult>();
const runTotpPrompt = createSingleFlight<RefreshResult>();
const runLocalDesktopSession = createSingleFlight<RefreshResult>();

async function reopenLocalDesktopSession(apiBaseUrl: string): Promise<RefreshResult> {
  return runLocalDesktopSession(async () => {
    if (!isDesktopApp()) return { ok: false, reason: "invalid" };
    const capability = getDesktopCapability();
    if (!capability) return { ok: false, reason: "transient" };

    const snapshot = getSessionSnapshot();
    try {
      const response = await fetch(`${apiBaseUrl}/v1/auth/local`, {
        method: "POST",
        headers: { "X-Edecan-Desktop-Capability": capability },
      });
      if (!isSessionSnapshotCurrent(snapshot)) {
        return { ok: false, reason: "superseded" };
      }
      if (!response.ok) return { ok: false, reason: "transient" };

      const pair = (await response.json()) as {
        access_token?: unknown;
        refresh_token?: unknown;
      };
      if (
        typeof pair.access_token !== "string" ||
        typeof pair.refresh_token !== "string" ||
        !pair.access_token ||
        !pair.refresh_token
      ) {
        return { ok: false, reason: "transient" };
      }
      if (!setTokensIfSessionCurrent(snapshot, pair.access_token, pair.refresh_token)) {
        return { ok: false, reason: "superseded" };
      }
      return { ok: true, session: getSessionSnapshot() };
    } catch {
      return isSessionSnapshotCurrent(snapshot)
        ? { ok: false, reason: "transient" }
        : { ok: false, reason: "superseded" };
    }
  });
}

async function refreshRequest(
  apiBaseUrl: string,
  snapshot: SessionSnapshot,
  totpCode?: string,
): Promise<RefreshResult> {
  if (!isSessionSnapshotCurrent(snapshot)) {
    return { ok: false, reason: "superseded" };
  }
  if (!snapshot.refreshToken) {
    clearTokensIfSessionCurrent(snapshot);
    return { ok: false, reason: "invalid" };
  }

  try {
    const response = await fetch(`${apiBaseUrl}/v1/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        refresh_token: snapshot.refreshToken,
        totp_code: totpCode || undefined,
      }),
    });
    if (!isSessionSnapshotCurrent(snapshot)) {
      return { ok: false, reason: "superseded" };
    }
    if (!response.ok) {
      if (response.status === 401) {
        const payload = (await response.clone().json().catch(() => null)) as {
          detail?: unknown;
        } | null;
        if (!isSessionSnapshotCurrent(snapshot)) {
          return { ok: false, reason: "superseded" };
        }
        const totpRequired = payload?.detail === TOTP_REQUIRED_DETAIL;
        if (!totpRequired && !clearTokensIfSessionCurrent(snapshot)) {
          return { ok: false, reason: "superseded" };
        }
        return {
          ok: false,
          reason: totpRequired ? "totp_required" : "invalid",
        };
      }
      // Rate limits, upstream failures and unexpected non-401 responses do
      // not prove that the refresh credential is invalid. Keep the local
      // session so a later request can recover when the service/network does.
      return { ok: false, reason: "transient" };
    }

    const pair = (await response.json()) as {
      access_token: string;
      refresh_token: string;
    };
    if (
      typeof pair.access_token !== "string" ||
      typeof pair.refresh_token !== "string" ||
      !pair.access_token ||
      !pair.refresh_token
    ) {
      return { ok: false, reason: "transient" };
    }
    if (!setTokensIfSessionCurrent(snapshot, pair.access_token, pair.refresh_token)) {
      return { ok: false, reason: "superseded" };
    }
    return { ok: true, session: getSessionSnapshot() };
  } catch {
    if (!isSessionSnapshotCurrent(snapshot)) {
      return { ok: false, reason: "superseded" };
    }
    return { ok: false, reason: "transient" };
  }
}

/** Único refresh en vuelo para todos los vertical slices de `lib/api-*.ts`.
 * Es obligatorio porque el backend rota el token con GETDEL atómico. */
export function refreshSession(
  apiBaseUrl: string,
  totpCode?: string,
  snapshot: SessionSnapshot = getSessionSnapshot(),
): Promise<RefreshResult> {
  return runRefresh(() => refreshRequest(apiBaseUrl, snapshot, totpCode));
}

export function refreshSessionWithTotpPrompt(
  apiBaseUrl: string,
  snapshot: SessionSnapshot = getSessionSnapshot(),
): Promise<RefreshResult> {
  return runTotpPrompt(async () => {
    if (!isSessionSnapshotCurrent(snapshot)) {
      return { ok: false, reason: "superseded" };
    }
    if (typeof window === "undefined") {
      return { ok: false, reason: "cancelled" };
    }
    const code = window.prompt(
      "Tu sesión expiró. Ingresa tu código de verificación en dos pasos (2FA) para continuar:",
    );
    if (!code?.trim()) {
      return { ok: false, reason: "cancelled" };
    }
    if (!isSessionSnapshotCurrent(snapshot)) {
      return { ok: false, reason: "superseded" };
    }
    return refreshSession(apiBaseUrl, code.trim(), snapshot);
  });
}

/** Resolves the full silent-refresh + optional TOTP flow once per tab. */
export async function recoverSessionAfterUnauthorized(apiBaseUrl: string): Promise<RefreshResult> {
  const snapshot = getSessionSnapshot();
  const result = await refreshSession(apiBaseUrl, undefined, snapshot);
  // Success advances the generation by committing the rotated pair. Invalid
  // advances it by clearing exactly the old session. Both are terminal and
  // must retain their classification.
  if (result.ok) return result;
  // El refresh de fakeredis puede desaparecer si el backend local se
  // reinicia con la WebView todavía abierta. La identidad no se perdió: está
  // en Postgres. Reabre la sesión loopback y deja que el cliente repita la
  // petición original con el nuevo access token.
  if (result.reason === "invalid") {
    return isDesktopApp() ? reopenLocalDesktopSession(apiBaseUrl) : result;
  }
  if (result.reason === "totp_required" && isDesktopApp()) {
    return reopenLocalDesktopSession(apiBaseUrl);
  }
  if (!isSessionSnapshotCurrent(snapshot)) {
    return { ok: false, reason: "superseded" };
  }
  if (result.reason === "totp_required") {
    return refreshSessionWithTotpPrompt(apiBaseUrl, snapshot);
  }
  return result;
}

/** Prevents replaying the original request under a login that changed later. */
export function isRefreshResultCurrent(
  result: RefreshResult,
): result is Extract<RefreshResult, { ok: true }> {
  return result.ok && isSessionSnapshotCurrent(result.session);
}

/**
 * Access y refresh permanecen en `sessionStorage`. La app Tauri no usa un JWT
 * como identidad durable: reabre la sesión de su dueño local cuando arranca o
 * cuando fakeredis se reinicia. Cada rotación mantiene las defensas contra
 * respuestas tardías.
 */
