# Navegador de investigación

`edecan_browser` (`packages/browser/`) le da al agente un navegador headless de **solo lectura**: puede abrir páginas públicas, resumirlas, extraer datos puntuales y comparar precios entre tiendas. Su contrato técnico vive en `ARCHITECTURE.md` §11.

> Este documento describe qué hace el navegador de Edecán hoy, con código real y probado — no es un plan a futuro. Las partes que están deliberadamente fuera de este paquete (compras, pagos, formularios) se explican al final, junto con dónde vive esa conversación en la hoja de ruta.

## Qué hace

Tres herramientas, las tres gateadas por el flag de plan `tools.browser` y ninguna marcada `dangerous` (son de solo lectura — `GET`, nunca escriben nada en un sitio de terceros):

| Herramienta | Qué hace |
|---|---|
| `navegar_web` | Abre una URL pública y devuelve título, texto legible y hasta 40 enlaces. |
| `extraer_datos_web` | Abre una URL y usa el modelo para extraer **solo** los campos que le pidas (ej. `["precio", "autor", "fecha"]`), como un objeto JSON con exactamente esas claves. |
| `comparar_precios` | Busca un producto en la web (reutilizando el mismo `SearchProvider` de `buscar_web`, ver `ARCHITECTURE.md` §10.14), visita hasta 5 fuentes, le pide al modelo el precio/disponibilidad de cada una, y arma una tabla ordenada de menor a mayor precio con la mejor oferta destacada. |

Cada llamada encadena tres pasos, siempre en este orden: **política de seguridad → fetch → extracción**. Si la política rechaza la URL, no se hace ninguna llamada de red.

## Qué NUNCA hace

Esto es un guardrail permanente de producto (`docs/roadmap.md`, guardrails 1 y 4), no una limitación temporal de esta primera versión:

- **Nunca compra, paga ni reserva nada.** No existe, en ningún módulo de `edecan_browser`, un camino de código que envíe `POST`, rellene un formulario o complete un checkout. `comparar_precios` es puramente informativo: cada respuesta termina con el aviso fijo *"Precios informativos; pueden variar. Edecán no realiza compras — la decisión y el pago son siempre tuyos."*
- **Nunca inicia sesión en nada.** No maneja credenciales, no llena campos de usuario/contraseña, no mantiene una sesión autenticada entre llamadas.
- **Nunca sigue una URL de checkout/carrito/pago/login** aunque la encuentre entre los resultados de búsqueda o los enlaces de una página: `edecan_browser.policy` la descarta antes de intentar abrirla (ver más abajo).
- **No hace scraping agresivo ni ignora `robots.txt`.** Cada dominio se consulta antes de navegarlo.

Si en el futuro Edecán ofrece compras o reservas reales, esa será una pieza completamente distinta: órdenes que nacen como borrador, exigen confirmación explícita del usuario en la UI y solo se ejecutan en modo `paper` (simulado), nunca de forma automática. Llenar formularios en general queda fuera del alcance actual. `edecan_browser` no importa nada de esa pieza ni la anticipa en código: es una prohibición dura, no un "todavía no".

## Cómo decide qué puede navegar (`edecan_browser.policy`)

`check_navigation(url, settings)` es el portero: se ejecuta antes de cualquier `fetch()` real sobre la URL pedida, y **también sobre cada salto de redirect** que responda esa URL — tanto `HttpxFetcher` (que sigue redirects a mano, nunca con el auto-redirect de `httpx`) como `PlaywrightFetcher` (que revalida cada navegación dentro del navegador, ver abajo) re-evalúan cada destino antes de pedirlo, nunca solo la URL inicial. Encadena cuatro chequeos en orden de más barato a más caro (para no gastar una llamada de red en una URL que de todos modos se iba a rechazar):

1. **Esquema**: solo `http`/`https`. `file://`, `ftp://`, `javascript:`, etc. se rechazan de inmediato.
2. **Blocklist de rutas transaccionales**: si la URL completa contiene `checkout`, `cart`, `carrito`, `payment`, `pago`, `login`, `signin` o `account`, se rechaza con un mensaje explicando que Edecán no navega flujos de compra o de sesión. Es una regex deliberadamente amplia — prefiere rechazar de más antes que dejar pasar un checkout real.
3. **Protección SSRF** (Server-Side Request Forgery): si el host de la URL es una IP literal, se valida directamente contra rangos privados/loopback/link-local/reservados/multicast. Si es un nombre de dominio, se resuelve por DNS y se valida cada IP resultante con el mismo criterio — así una URL como `http://169.254.169.254/` (metadata de AWS/GCP) o `http://10.0.0.5/` (red interna) nunca llega a hacerse fetch, ni directamente, ni a través de un dominio que resuelva ahí, ni a través de un redirect HTTP que una página pública devuelva hacia ahí. Un fallo o timeout de DNS se trata como bloqueo: si no se puede confirmar que el destino es público, no se navega.
4. **`robots.txt` del origen**: se descarga (con `httpx`, cacheado 10 minutos en memoria por dominio) y se parsea con `urllib.robotparser`. Si el sitio lo prohíbe para la ruta pedida, se rechaza. Si no existe o falla la descarga, se asume sin restricciones (convención estándar de `robots.txt`).

Cualquier rechazo devuelve un mensaje listo para mostrarle al usuario — la herramienta nunca revienta con una excepción por una URL inválida o prohibida, simplemente explica por qué no puede continuar.

## El fetcher: `httpx` por defecto, Playwright opcional

- **`HttpxFetcher`** (default, `BROWSER_FETCH_PROVIDER=httpx`): `GET` puro con `httpx`. Un cliente nuevo por cada llamada (nunca hay cookies persistentes entre navegaciones ni entre tenants), nunca agrega credenciales, sigue hasta 5 redirects **a mano** (re-validando cada destino contra `check_navigation` antes de pedirlo, nunca con el auto-redirect de `httpx` — así ningún redirect puede escapar al guardrail SSRF), y corta la descarga en streaming apenas se supera `BROWSER_MAX_FETCH_BYTES` — nunca descarga una página completa solo para recortarla después.
- **`PlaywrightFetcher`** (opcional, renderiza JavaScript con Chromium): se activa únicamente con `BROWSER_FETCH_PROVIDER=playwright`. Requiere instalar el extra del paquete:

  ```bash
  uv pip install 'edecan-browser[playwright]'
  playwright install chromium
  ```

  Sin ese extra instalado, pedir `BROWSER_FETCH_PROVIDER=playwright` da un `ImportError` con instrucciones claras — nunca un traceback críptico. Playwright **no** es una dependencia dura del paquete ni de sus tests (`docs/roadmap.md`): ningún test de `packages/browser/tests/` lo requiere instalado.

  A diferencia de `HttpxFetcher`, aquí es Chromium quien maneja la navegación (incluyendo redirects HTTP y de JavaScript), no código propio — así que la revalidación de `check_navigation` en cada salto no puede hacerse "a mano" del mismo modo. `PlaywrightFetcher` lo resuelve en dos capas: (a) intercepta toda petición de navegación del frame principal con `page.route("**/*", ...)` y la revalida contra `check_navigation` **antes** de dejarla continuar (`_manejar_ruta_playwright`), y (b) como defensa en profundidad tras `page.goto()`, revalida de nuevo `page.url` (el destino final) y toda la cadena de `response.request.redirected_from` (`_cadena_de_redirects`), por si algún salto de redirect no hubiera calificado como "navigation request" ante el handler de la capa (a). Cualquier bloqueo lanza el mismo tipo de excepción que `HttpxFetcher` (`httpx.HTTPError`), así que `edecan_browser.tools` atrapa el rechazo de cualquiera de los dos fetchers exactamente igual — el guardrail SSRF/checkout/pago/login es idéntico para los dos proveedores, solo cambia el mecanismo interno de interceptar la navegación.

## Configuración (`BROWSER_*`)

Todas se leen de forma defensiva (`getattr(settings, "CAMPO", default)`) — el paquete nunca falla si alguna todavía no está declarada en `apps/api/edecan_api/config.py`/`.env.example` (convención dura de `docs/roadmap.md`).

| Variable | Default | Qué controla |
|---|---|---|
| `BROWSER_FETCH_PROVIDER` | `httpx` | `httpx` o `playwright` (ver arriba). |
| `BROWSER_USER_AGENT` | `EdecanBot/1.0` | User-Agent enviado en cada fetch y usado para evaluar `robots.txt`. |
| `BROWSER_MAX_FETCH_BYTES` | `2000000` (≈2 MB) | Tamaño máximo descargado por página antes de cortar. |
| `BROWSER_TIMEOUT_SECONDS` | `20` | Timeout de red por fetch (y para la descarga de `robots.txt`). |

`comparar_precios` usa el mismo proveedor por tenant que `buscar_web`. Sin credencial consulta DuckDuckGo de forma real; Brave y Tavily son opciones bring-your-own.

## Respeto de `robots.txt` y ToS

Edecán navega como un bot educado: se identifica con un `User-Agent` propio (`EdecanBot/1.0` por defecto, configurable), respeta `robots.txt` de cada sitio antes de visitarlo, y solo hace `GET` de páginas públicas — nunca elude un paywall, nunca falsea su identidad, nunca automatiza un flujo que el sitio no ofrezca para lectura pública. Si necesitas navegar contenido detrás de autenticación de un servicio con API oficial, esa integración es un conector aparte (`ARCHITECTURE.md` §5) con OAuth y credenciales del propio tenant — nunca scraping con sesión robada. Ver también [`seguridad-modelo-amenazas.md`](./seguridad-modelo-amenazas.md) para el modelo de amenazas general de la plataforma y [`cumplimiento/tos-redes.md`](./cumplimiento/tos-redes.md) para la postura de cumplimiento frente a términos de servicio de terceros.

## Arquitectura interna (`packages/browser/edecan_browser/`)

| Módulo | Responsabilidad |
|---|---|
| `policy.py` | `check_navigation()` — el portero descrito arriba: esquema, blocklist, SSRF, `robots.txt` (`RobotsCache`). |
| `fetch.py` | `PageFetcher` (protocolo), `HttpxFetcher`, `PlaywrightFetcher` (opcional), `get_fetcher(settings)`. |
| `extract.py` | `extract_page()` (BeautifulSoup → título/texto legible/enlaces/meta description) y `render_markdown()`. |
| `tools.py` | Las 3 herramientas (`navegar_web`, `extraer_datos_web`, `comparar_precios`), que encadenan los tres módulos anteriores. |

Detalle de decisiones de implementación (por qué se agregó `edecan-llm` como dependencia, por qué la caché de `robots.txt` vive en una clase inyectable, etc.) en [`../packages/browser/README.md`](../packages/browser/README.md).

## Tests

`packages/browser/tests/` es 100% offline y determinista (`respx` para toda llamada HTTP, `FakeLLM` local para `ctx.llm`, un resolutor DNS falso inyectado para los casos de SSRF por dominio): robots.txt, SSRF, rutas transaccionales, extracción HTML, límite de descarga y `comparar_precios` con una respuesta DuckDuckGo simulada. Corre con `uv run --package edecan-browser pytest packages/browser/tests` desde la raíz.
