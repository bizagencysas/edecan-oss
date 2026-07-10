/**
 * `NEXT_OUTPUT=export` activa el export estático que consume la app de
 * escritorio Tauri (DIRECCION_ACTUAL.md "Stack de la app de escritorio:
 * Tauri"): el frontend se empaqueta como HTML/CSS/JS estático servido por
 * el backend local, sin un servidor Next corriendo. El modo servidor normal
 * (`next dev` / `next start`, usado por `make web` y el hosted) no cambia —
 * este flag es opt-in, así que el build de siempre sigue igual sin tocarlo.
 * Ver `docs/primeros-pasos.md` para el paso a paso completo del build de
 * escritorio, incluyendo por qué ese build necesita `NEXT_PUBLIC_API_URL=''`.
 * @type {import('next').NextConfig}
 */
const isExport = process.env.NEXT_OUTPUT === "export";

const nextConfig = {
  reactStrictMode: true,
  ...(isExport ? { output: "export", images: { unoptimized: true }, trailingSlash: true } : {}),
};

export default nextConfig;
