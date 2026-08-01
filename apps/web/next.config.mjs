import { fileURLToPath } from "node:url";

import { buildSecurityHeaders } from "./security-headers.mjs";

/*
 * `NEXT_OUTPUT=export` activa el export estático que consume la app de
 * escritorio Tauri (DIRECCION_ACTUAL.md "Stack de la app de escritorio:
 * Tauri"): el frontend se empaqueta como HTML/CSS/JS estático servido por
 * el backend local, sin un servidor Next corriendo. El modo servidor normal
 * (`next dev` / `next start`, usado por `make web` y el hosted) no cambia —
 * este flag es opt-in, así que el build de siempre sigue igual sin tocarlo.
 * Ver `docs/primeros-pasos.md` para el paso a paso completo del build de
 * escritorio, incluyendo por qué ese build necesita `NEXT_PUBLIC_API_URL=''`.
 */
const isExport = process.env.NEXT_OUTPUT === "export";
const securityHeaders = buildSecurityHeaders();

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // This app owns its lockfile. Pinning the tracing root avoids Next.js
  // accidentally treating an unrelated lockfile higher in the filesystem
  // as the monorepo root on contributor machines.
  outputFileTracingRoot: fileURLToPath(new URL(".", import.meta.url)),
  // Un export estático no tiene una capa HTTP donde Next pueda emitir
  // headers. El sidecar local aplica la misma defensa al montar
  // SERVE_WEB_DIR; mantener `headers()` fuera de este modo también evita una
  // feature incompatible con `output: "export"`.
  ...(!isExport
    ? {
        async headers() {
          return [{ source: "/:path*", headers: securityHeaders }];
        },
      }
    : {}),
  ...(isExport ? { output: "export", images: { unoptimized: true }, trailingSlash: true } : {}),
};

export default nextConfig;
