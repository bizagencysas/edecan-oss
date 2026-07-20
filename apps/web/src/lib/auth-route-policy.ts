/**
 * Endpoints de autenticación que deliberadamente NO llevan access token.
 * Las rutas TOTP viven bajo `/v1/auth/` pero son protegidas, por eso una
 * comprobación por prefijo sería demasiado amplia y las rompería.
 */
const PUBLIC_AUTH_ROUTES = new Set([
  "/v1/auth/register",
  "/v1/auth/login",
  "/v1/auth/refresh",
  "/v1/auth/logout",
]);

export function isPublicAuthRoute(path: string): boolean {
  return PUBLIC_AUTH_ROUTES.has(path);
}
