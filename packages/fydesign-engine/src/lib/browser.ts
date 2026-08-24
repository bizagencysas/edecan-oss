// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Shared browser launcher — single source of truth for headless Chromium   ║
// ║  Strategies (in order):                                                   ║
// ║    1. Playwright + Chromium incluido por Edecán                            ║
// ║    2. playwright-core + Chrome del sistema (desarrollo/fallback)           ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

/**
 * Launch a headless Chromium browser using the best available strategy.
 * @returns A Playwright `Browser` instance.
 */
export async function launchBrowser() {
  // Strategy 1: runtime y headless shell fijados dentro de la app de Edecán.
  try {
    const pw = await import('playwright');
    return await pw.chromium.launch({ headless: true });
  } catch { /* playwright not installed as full package */ }

  // Strategy 2: Chrome del sistema para desarrollo o recuperación explícita.
  try {
    const { chromium } = await import('playwright-core');
    const paths = [
      process.env.CHROMIUM_PATH,
      process.env.PUPPETEER_EXECUTABLE_PATH,
      '/usr/bin/chromium-browser',
      '/usr/bin/chromium',
      '/usr/lib/chromium/chromium',          // Debian Bookworm actual binary
      '/usr/lib/chromium-browser/chromium-browser', // Debian alternative
      '/usr/bin/google-chrome',
      '/usr/bin/google-chrome-stable',
      '/snap/bin/chromium',
      '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
      '/Applications/Chromium.app/Contents/MacOS/Chromium',
    ].filter(Boolean) as string[];
    for (const p of paths) {
      try {
        return await chromium.launch({
          executablePath: p,
          headless: true,
          args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-gpu'],
        });
      } catch { continue; }
    }
  } catch {}

  throw new Error(
    'No hay un navegador disponible. Reinstala Studio para recuperar Chromium o conecta Chrome.',
  );
}
