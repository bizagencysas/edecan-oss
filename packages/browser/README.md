# packages/browser — `edecan_browser`

Navegador de investigación headless del agente (`ROADMAP_V2.md` §4 WP-V2-03).
Cada tool implementa el contrato `Tool` de `edecan_core` (§10.7): `name`,
`description`, `input_schema`, `requires_flags`, `dangerous` y `async run(ctx, args)`.

**Solo investiga. Jamás compra, paga ni completa un formulario** — ver
[`../../docs/navegador.md`](../../docs/navegador.md) para el detalle completo
del guardrail y `edecan_browser/policy.py` para la implementación.

`get_all_tools() -> list[Tool]` (en `edecan_browser/__init__.py`) es el entry
point que consume `edecan_core.ToolRegistry.load_entry_points(group="edecan.tools")`,
declarado en `pyproject.toml` como `[project.entry-points."edecan.tools"]`.

## Las 3 herramientas (nombres exactos, pinned en `ROADMAP_V2.md` §7.7)

Las tres requieren el flag de plan `tools.browser` y ninguna es `dangerous`
(son de solo lectura, `GET`).

| Módulo | Tool | Qué hace |
|---|---|---|
| `tools.py` | `navegar_web` | Abre una URL, devuelve título/texto legible/enlaces. |
| `tools.py` | `extraer_datos_web` | Abre una URL y usa el LLM para extraer SOLO los campos pedidos como JSON. |
| `tools.py` | `comparar_precios` | Busca un producto (vía `edecan_toolkit.research.SearchProvider`), filtra URLs por política, extrae precio por página con el LLM y arma una tabla comparativa ordenada + aviso fijo de que Edecán no compra por su cuenta. |

`fetch.py`, `policy.py` y `extract.py` son los tres módulos internos que
`tools.py` encadena en cada llamada: **policy → fetch → extract**.

## Decisiones de implementación

- **`edecan-llm` como dependencia añadida**: `extraer_datos_web` y
  `comparar_precios` necesitan invocar de verdad
  `ctx.llm.complete("principal", tenant_flags, req)` (§10.6) para redactar
  JSON estructurado a partir del contenido de la página — no estaba en la
  lista original de dependencias del work package, se añadió por el mismo
  motivo (y con el mismo patrón) que documenta el README de
  `edecan_toolkit` para `generar_contenido`.
- **`comparar_precios` reutiliza `edecan_toolkit.research.get_search_provider`**
  (mismo `SearchProvider`/`StubSearch`/`BraveSearch`/`TavilySearch` que usa
  `buscar_web`, pinned en `ARCHITECTURE.md` §10.14) — importarlo en `tools.py`
  es código de producción, permitido explícitamente aunque los *tests* de
  este paquete no importen paquetes hermanos (§10.1).
- **`policy.py` es el único guardián**: toda URL pasa por
  `check_navigation()` (esquema → blocklist de rutas transaccionales → SSRF
  → robots.txt) *antes* de cualquier `fetch()` real. El orden va de más
  barato a más caro para no gastar una llamada de red en una URL que de
  todos modos se iba a rechazar.
- **SSRF**: si el host de la URL ya es una IP literal, se valida
  directamente contra rangos privados/loopback/link-local/reservados/
  multicast; si es un nombre de dominio, se resuelve por DNS
  (`policy.resolve_hostname_ips`, aislada en su propia función de módulo
  justamente para que los tests la reemplacen sin red real —
  `ARCHITECTURE.md` §10.15) y se valida cada IP resuelta. Un error o timeout
  de DNS se trata como bloqueo (fail closed): no se puede garantizar que el
  dominio no apunte a una red privada.
- **`robots.txt`** se descarga con `httpx` (mockeable con `respx`, a
  diferencia de `urllib.request` que usaría `RobotFileParser.read()`) y se
  parsea con `urllib.robotparser.RobotFileParser.parse(...)` — cacheado 10
  minutos por origen en `policy.RobotsCache`. Si no existe (404) o falla la
  descarga, se asume sin restricciones (convención estándar de robots.txt).
- **`HttpxFetcher`** crea un `httpx.AsyncClient` nuevo en cada `fetch()` (sin
  cookies persistentes entre navegaciones), nunca agrega credenciales, sigue
  hasta 5 redirects, y corta la descarga en streaming apenas se supera
  `BROWSER_MAX_FETCH_BYTES` — nunca baja la página completa para recortarla después.
- **`PlaywrightFetcher`** es 100% opcional (`[project.optional-dependencies].playwright`,
  `BROWSER_FETCH_PROVIDER=playwright`): import diferido y guardeado dentro de
  `__init__`, nunca se importa al cargar `edecan_browser` y ningún test de
  este paquete lo requiere ni lo instancia con Playwright real instalado
  (`ROADMAP_V2.md` §7.11).
- **Todos los settings se leen con `getattr(settings, "CAMPO", default)`**
  (convención dura de `ROADMAP_V2.md` §7.5): el paquete nunca revienta si
  `apps/api` todavía no declaró los campos `BROWSER_*` en su `Settings`.

## Tests

`tests/` usa `respx` para toda llamada HTTP (robots.txt, fetch de páginas) y
fakes locales por duck typing para `ctx.llm` (`FakeLLM`, con una cola de
respuestas para simular precios distintos por tienda) — offline y
deterministas (§10.15). Un fixture `autouse` en `conftest.py` reemplaza
`policy.resolve_hostname_ips` por un resolutor falso (IP pública fija) y
`policy._CACHE_GLOBAL` por una `RobotsCache` nueva antes de cada test, para
que ningún test dependa de DNS real ni herede caché de robots.txt de otro
test. No importan `edecan_toolkit`, `edecan_llm` ni `edecan_core` para
construir sus dobles.
