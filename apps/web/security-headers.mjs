const DEFAULT_API_URL = "http://localhost:8000";

/**
 * Reduce una URL de API configurable a un origen CSP. Una cadena vacía o
 * una ruta relativa significan same-origin; cualquier otro esquema se
 * rechaza durante el arranque/build en vez de ampliar `connect-src`.
 */
export function apiOriginForCsp(rawApiUrl = process.env.NEXT_PUBLIC_API_URL) {
  const configured = rawApiUrl === undefined ? DEFAULT_API_URL : rawApiUrl.trim();
  if (configured === "" || configured.startsWith("/")) return null;

  let parsed;
  try {
    parsed = new URL(configured);
  } catch {
    throw new Error(
      "NEXT_PUBLIC_API_URL debe ser una URL http(s), una ruta relativa o una cadena vacía.",
    );
  }

  if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password) {
    throw new Error(
      "NEXT_PUBLIC_API_URL debe usar http(s) y no puede incluir credenciales.",
    );
  }
  return parsed.origin;
}

export function buildContentSecurityPolicy({
  apiUrl = process.env.NEXT_PUBLIC_API_URL,
  development = process.env.NODE_ENV === "development",
} = {}) {
  const connectSources = ["'self'"];
  const apiOrigin = apiOriginForCsp(apiUrl);
  if (apiOrigin) connectSources.push(apiOrigin);

  // Next Dev usa WebSocket para HMR y el puerto puede configurarse. Este es
  // el único wildcard de la política y jamás entra en un build productivo.
  if (development) {
    connectSources.push("ws://localhost:*", "ws://127.0.0.1:*");
  }

  const scriptSources = ["'self'", "'unsafe-inline'"];
  if (development) scriptSources.push("'unsafe-eval'");

  return [
    "default-src 'self'",
    "base-uri 'self'",
    "object-src 'none'",
    "frame-src 'none'",
    "frame-ancestors 'none'",
    "form-action 'self'",
    `script-src ${scriptSources.join(" ")}`,
    // Next y varios componentes usan estilos inline. Nonces por request no
    // son compatibles con el export estático consumido por Tauri.
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "media-src 'self' data: blob:",
    "worker-src 'self' blob:",
    `connect-src ${connectSources.join(" ")}`,
    "manifest-src 'self'",
  ].join("; ");
}

export function buildSecurityHeaders(options) {
  return [
    { key: "Content-Security-Policy", value: buildContentSecurityPolicy(options) },
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "X-Frame-Options", value: "DENY" },
    { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
    {
      key: "Permissions-Policy",
      value: "camera=(), geolocation=(), microphone=(self), payment=(), usb=()",
    },
  ];
}
