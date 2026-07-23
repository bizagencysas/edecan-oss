const CHAT_SECRET_PATTERNS = [
  /\bsk[-_][A-Za-z0-9_-]{8,}/gi,
  /\bBearer\s+[A-Za-z0-9._~+/=-]{8,}/gi,
  /\b(?:rk_live|rk_test|whsec)_[A-Za-z0-9]{8,}/gi,
  /\b(?:AKIA|ASIA)[A-Z0-9]{16}\b/g,
];

/**
 * Defensa visual inmediata para texto que todavía no ha pasado por el
 * backend. La credencial real puede viajar una vez para configurarse, pero
 * nunca se pinta en el DOM ni se conserva en el historial.
 */
export function redactChatSecrets(text: string): string {
  return CHAT_SECRET_PATTERNS.reduce(
    (safe, pattern) => safe.replace(pattern, "[credencial protegida]"),
    text,
  );
}
