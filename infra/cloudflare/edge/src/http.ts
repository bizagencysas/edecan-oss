export function securityHeaders(requestId: string): Headers {
  return new Headers({
    "cache-control": "no-store, max-age=0",
    "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
    "cross-origin-resource-policy": "same-origin",
    "permissions-policy": "camera=(), geolocation=(), microphone=(), payment=(), usb=()",
    "referrer-policy": "no-referrer",
    "strict-transport-security": "max-age=31536000; includeSubDomains",
    "x-content-type-options": "nosniff",
    "x-edecan-request-id": requestId,
    "x-frame-options": "DENY"
  });
}

export function jsonResponse(
  status: number,
  payload: unknown,
  requestId: string
): Response {
  const headers = securityHeaders(requestId);
  headers.set("content-type", "application/json; charset=utf-8");
  return Response.json(payload, { status, headers });
}

export function hiddenNotFound(requestId: string): Response {
  return jsonResponse(404, { error: "not_found" }, requestId);
}
