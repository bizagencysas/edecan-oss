"""Las 3 herramientas del navegador (`ROADMAP_V2.md` §7.7, nombres exactos):
`navegar_web`, `extraer_datos_web`, `comparar_precios`.

Las tres exigen el flag de plan `tools.browser` y ninguna es `dangerous`:
son de solo lectura (`GET`) — nunca completan un formulario, inician sesión
ni compran nada (`edecan_browser.policy` es quien impone ese guardrail antes
de cualquier fetch real).

`comparar_precios` reutiliza `edecan_toolkit.research.get_tenant_search_provider`
(mismo resolver bring-your-own que usa `buscar_web`, "tenant → stub" SIN paso
intermedio de plataforma — ver `ARCHITECTURE.md` §10.14 y el docstring de
`edecan_toolkit.research`) — importarlo aquí es código de producción,
permitido por `ARCHITECTURE.md` §10.1 aunque los *tests* de este paquete no
importen paquetes hermanos.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any
from urllib.parse import urlsplit

import httpx
from edecan_core import Tool, ToolContext, ToolResult
from edecan_llm.base import ChatMessage, CompletionRequest
from edecan_toolkit.research import get_tenant_search_provider

from ._chromium_bootstrap import asegurar_chromium_instalado
from ._driver_macos import asegurar_driver_playwright_macos
from .extract import ExtractedPage, extract_page, render_markdown
from .fetch import (
    _cadena_de_redirects,
    _error_navegacion_bloqueada,
    _manejar_ruta_playwright,
    _validar_navegacion,
    get_fetcher,
)
from .policy import check_navigation

logger = logging.getLogger(__name__)

_FLAG_BROWSER = "tools.browser"

_CAMPOS_PRECIO = ("tienda", "producto", "precio", "moneda", "disponible")
_AVISO_PRECIOS = (
    "Precios informativos; pueden variar. Edecán no realiza compras — la decisión y el "
    "pago son siempre tuyos."
)
_MAX_FUENTES_DEFECTO = 5
_MAX_FUENTES_TOPE = 5

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def _flags_del_tenant(ctx: ToolContext) -> dict[str, Any]:
    """Lee `ctx.extras["flags"]` si el agente los dejó ahí (mismo patrón que
    `edecan_toolkit.contenido._tenant_flags`, no importado por nombre porque
    es un helper privado con guion bajo de un paquete hermano — se
    reimplementa aquí, son 3 líneas). Sin flags, `{}` no degrada el alias
    `"principal"` (ver `LLMRouter._resolve_model`).
    """
    extras = ctx.extras if isinstance(ctx.extras, dict) else {}
    flags = extras.get("flags")
    return flags if isinstance(flags, dict) else {}


def _clamp_int(valor: Any, *, default: int, minimo: int, maximo: int) -> int:
    try:
        n = int(valor) if valor is not None else default
    except (TypeError, ValueError):
        n = default
    return max(minimo, min(maximo, n))


def _parsear_json(texto: str) -> Any | None:
    """Quita las cercas de código ```json ... ``` (frecuentes en salidas de
    LLM) y parsea JSON; `None` si no es JSON válido.
    """
    limpio = _CODE_FENCE_RE.sub("", texto or "").strip()
    try:
        return json.loads(limpio)
    except (json.JSONDecodeError, TypeError):
        return None


async def _fetch_y_extraer(ctx: ToolContext, url: str) -> tuple[Any, ExtractedPage] | ToolResult:
    """Encadena policy→fetch→extract para `url`. Devuelve `(FetchedPage,
    ExtractedPage)` si todo sale bien, o un `ToolResult` de error ya armado
    (para que el caller solo tenga que hacer `if isinstance(resultado,
    ToolResult): return resultado`) — cada tool decide desde ahí si necesita
    el markdown completo (`render_markdown`) o solo campos sueltos, sin
    parsear la página dos veces.
    """
    veredicto = await check_navigation(url, ctx.settings)
    if not veredicto.allowed:
        return ToolResult(content=veredicto.reason or f"No puedo navegar «{url}».")

    try:
        pagina = await get_fetcher(ctx.settings).fetch(url)
    except httpx.HTTPError as exc:
        return ToolResult(content=f"No pude abrir «{url}»: {exc}.")

    extraida = extract_page(pagina.html or pagina.text or "", pagina.url_final)
    return pagina, extraida


class NavegarWebTool(Tool):
    name = "navegar_web"
    description = (
        "Abre una URL pública y devuelve su título, texto legible y enlaces. Solo lee "
        "(GET) — nunca completa formularios, inicia sesión ni compra nada."
    )
    category = "browser"
    risk_level = "none"
    latency_class = "slow"
    requires_flags = frozenset({_FLAG_BROWSER})
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL http(s) a abrir."},
        },
        "required": ["url"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        url = str(args.get("url", "")).strip()
        if not url:
            return ToolResult(content="Dime qué URL quieres que abra.")

        resultado = await _fetch_y_extraer(ctx, url)
        if isinstance(resultado, ToolResult):
            return resultado
        pagina, extraida = resultado

        return ToolResult(
            content=render_markdown(extraida),
            data={
                "titulo": extraida.titulo,
                "url_final": pagina.url_final,
                "enlaces": extraida.enlaces,
            },
            presentation=[
                {
                    "type": "link_preview",
                    "fallback_text": extraida.titulo or pagina.url_final,
                    "url": pagina.url_final,
                    "title": extraida.titulo or urlsplit(pagina.url_final).hostname or "Enlace",
                    "description": extraida.meta_description or None,
                    "site_name": urlsplit(pagina.url_final).hostname,
                    "actions": [
                        {
                            "id": "browser.open-result",
                            "label": "Abrir enlace",
                            "action": "open_url",
                            "url": pagina.url_final,
                        }
                    ],
                }
            ],
        )


class ExtraerDatosWebTool(Tool):
    name = "extraer_datos_web"
    description = (
        "Abre una URL pública y usa el modelo para extraer SOLO los campos pedidos, "
        "como un objeto JSON. Solo lee (GET) — nunca completa formularios ni compra nada."
    )
    category = "browser"
    risk_level = "none"
    latency_class = "slow"
    requires_flags = frozenset({_FLAG_BROWSER})
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL http(s) a abrir."},
            "campos": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Nombres de los campos a extraer (ej. ['precio', 'autor']).",
            },
        },
        "required": ["url", "campos"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        url = str(args.get("url", "")).strip()
        if not url:
            return ToolResult(content="Dime qué URL quieres que abra.")
        campos_in = args.get("campos")
        if not isinstance(campos_in, list) or not campos_in:
            return ToolResult(
                content="Dime qué campos quieres que extraiga de la página (una lista de nombres)."
            )
        campos = [str(c).strip() for c in campos_in if str(c).strip()]
        if not campos:
            return ToolResult(content="Ningún campo válido en la lista — dame al menos uno.")

        resultado = await _fetch_y_extraer(ctx, url)
        if isinstance(resultado, ToolResult):
            return resultado
        pagina, extraida = resultado
        contenido = render_markdown(extraida)

        datos = await _extraer_campos_via_llm(ctx, contenido, campos)
        if datos is None:
            return ToolResult(
                content=(
                    f"No logré extraer esos campos en formato válido de «{url}»; "
                    "intenta reformular los campos."
                )
            )

        return ToolResult(
            content=json.dumps(datos, ensure_ascii=False, indent=2),
            data={"url_final": pagina.url_final, "campos": datos},
        )


async def _extraer_campos_via_llm(
    ctx: ToolContext, contenido_markdown: str, campos: list[str]
) -> dict[str, Any] | None:
    system = (
        "Extraes datos estructurados del contenido de una página web. Devuelves SOLO un "
        f"objeto JSON con EXACTAMENTE estas claves: {', '.join(campos)}. Si un dato no "
        "aparece en la página, usa null en esa clave. No agregues texto fuera del JSON."
    )
    respuesta = await ctx.llm.complete(
        "principal",
        _flags_del_tenant(ctx),
        CompletionRequest(
            model="principal",
            system=system,
            messages=[ChatMessage(role="user", content=f"Página:\n\n{contenido_markdown}")],
            max_tokens=1024,
        ),
    )
    datos = _parsear_json(respuesta.text)
    if not isinstance(datos, dict):
        return None
    # "SOLO esos campos": se filtra a EXACTAMENTE las claves pedidas, sin
    # importar qué más haya alucinado el modelo — es la "validación de que la
    # respuesta es JSON con esos keys" pedida por el work package.
    return {campo: datos.get(campo) for campo in campos}


class CompararPreciosTool(Tool):
    name = "comparar_precios"
    description = (
        "Busca un producto en la web y compara el precio anunciado en varias tiendas, en "
        "una tabla ordenada de menor a mayor precio. Solo informa — nunca agrega al "
        "carrito, paga ni completa ningún checkout."
    )
    category = "browser"
    risk_level = "none"
    latency_class = "slow"
    requires_flags = frozenset({_FLAG_BROWSER})
    input_schema = {
        "type": "object",
        "properties": {
            "producto": {"type": "string", "description": "Qué producto buscar y comparar."},
            "max_fuentes": {
                "type": "integer",
                "description": "Máximo de tiendas/fuentes a comparar (1-5).",
                "default": _MAX_FUENTES_DEFECTO,
            },
        },
        "required": ["producto"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        producto = str(args.get("producto", "")).strip()
        if not producto:
            return ToolResult(content="Dime qué producto quieres que compare.")
        max_fuentes = _clamp_int(
            args.get("max_fuentes"),
            default=_MAX_FUENTES_DEFECTO,
            minimo=1,
            maximo=_MAX_FUENTES_TOPE,
        )

        proveedor = await get_tenant_search_provider(ctx)
        # Se pide el doble de resultados de los que hacen falta: algunas URLs
        # se van a caer por policy (checkout/SSRF/robots.txt) o por no traer
        # un precio parseable, y aun así se quiere llegar a `max_fuentes` filas.
        hits = await proveedor.search(f"precio {producto} comprar", k=min(max_fuentes * 2, 10))

        fetcher = get_fetcher(ctx.settings)
        flags = _flags_del_tenant(ctx)
        filas: list[dict[str, Any]] = []
        for hit in hits:
            if len(filas) >= max_fuentes:
                break
            veredicto = await check_navigation(hit.url, ctx.settings)
            if not veredicto.allowed:
                continue
            try:
                pagina = await fetcher.fetch(hit.url)
            except httpx.HTTPError:
                continue
            contenido = render_markdown(
                extract_page(pagina.html or pagina.text or "", pagina.url_final)
            )
            fila = await _extraer_precio_via_llm(ctx, contenido, producto, flags)
            if fila is None:
                continue
            fila["url"] = pagina.url_final
            filas.append(fila)

        if not filas:
            return ToolResult(
                content=f"No encontré precios comparables para «{producto}». {_AVISO_PRECIOS}",
                data={"producto": producto, "resultados": [], "aviso": _AVISO_PRECIOS},
            )

        filas_ordenadas = _ordenar_por_precio(filas)
        mejor = next((f for f in filas_ordenadas if isinstance(f.get("precio"), int | float)), None)

        lineas = [
            f"Comparación de precios para «{producto}»:",
            "",
            _tabla_markdown(filas_ordenadas),
        ]
        if mejor is not None:
            lineas += [
                "",
                f"Mejor oferta: {mejor['tienda']} — {mejor['moneda']} {mejor['precio']:,.2f} "
                f"({mejor['url']})",
            ]
        lineas += ["", _AVISO_PRECIOS]

        return ToolResult(
            content="\n".join(lineas),
            data={"producto": producto, "resultados": filas_ordenadas, "aviso": _AVISO_PRECIOS},
        )


async def _extraer_precio_via_llm(
    ctx: ToolContext, contenido_markdown: str, producto: str, flags: dict[str, Any]
) -> dict[str, Any] | None:
    system = (
        "Extraes datos de precio de una página de tienda en línea. Devuelves SOLO un "
        f"objeto JSON con EXACTAMENTE estas claves: {', '.join(_CAMPOS_PRECIO)}. "
        "'precio' es un número sin símbolo de moneda (o null si no aparece). 'moneda' es "
        "un código de 3 letras (ej. USD). 'disponible' es true/false. No agregues texto "
        "fuera del JSON."
    )
    user = f"Producto buscado: {producto}\n\nPágina:\n\n{contenido_markdown}"
    respuesta = await ctx.llm.complete(
        "principal",
        flags,
        CompletionRequest(
            model="principal",
            system=system,
            messages=[ChatMessage(role="user", content=user)],
            max_tokens=512,
        ),
    )
    datos = _parsear_json(respuesta.text)
    if not isinstance(datos, dict):
        return None
    return {
        "tienda": str(datos.get("tienda") or "(tienda desconocida)"),
        "producto": str(datos.get("producto") or producto),
        "precio": _a_numero(datos.get("precio")),
        "moneda": str(datos.get("moneda") or "").upper(),
        "disponible": bool(datos.get("disponible", True)),
    }


def _a_numero(valor: Any) -> float | None:
    if isinstance(valor, int | float) and not isinstance(valor, bool):
        return float(valor)
    if isinstance(valor, str):
        try:
            return float(valor.replace(",", "").strip())
        except ValueError:
            return None
    return None


def _ordenar_por_precio(filas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    con_precio = [f for f in filas if isinstance(f.get("precio"), int | float)]
    sin_precio = [f for f in filas if not isinstance(f.get("precio"), int | float)]
    con_precio.sort(key=lambda f: f["precio"])
    return con_precio + sin_precio


def _tabla_markdown(filas: list[dict[str, Any]]) -> str:
    lineas = ["| Tienda | Precio | Disponible | URL |", "|---|---|---|---|"]
    for f in filas:
        precio = (
            f"{f['moneda']} {f['precio']:,.2f}".strip()
            if isinstance(f.get("precio"), int | float)
            else "N/D"
        )
        disponible = "Sí" if f.get("disponible") else "No"
        lineas.append(f"| {f['tienda']} | {precio} | {disponible} | {f['url']} |")
    return "\n".join(lineas)


# ---------------------------------------------------------------------------
# `navegar_web_interactivo` (product design"BROWSER USE")
# ---------------------------------------------------------------------------
#
# La única herramienta del navegador que ESCRIBE (clics/teclado/selección)
# sobre una página real, vía Playwright/Chromium. Es `dangerous=True` y exige
# el flag `tools.browser` (igual que las de solo lectura), pero además:
#
# - Respeta el MISMO guardrail de navegación que las tools de solo lectura:
#   `check_navigation` sobre la URL pedida (checkout/pago/login y SSRF se
#   rechazan antes de levantar nada) y, durante la ejecución, revalida CADA
#   navegación del frame principal con el handler de `page.route`
#   (`_manejar_ruta_playwright`) más la revalidación final de
#   `_cadena_de_redirects` — exactamente la misma defensa que
#   `PlaywrightFetcher.fetch()`.
# - Si Playwright (extra opcional) no está instalado, devuelve un error claro
#   — nunca un éxito falso (`AGENTS.md` §13.1: sin fabricar resultados).
#
# Cada `run()` levanta su propio Chromium y ejecuta UNA acción, con el mismo
# criterio de "sin sesión persistente" que `HttpxFetcher`: el navegador se
# cierra al final del turno, no queda estado entre llamadas.

_ACCIONES_INTERACTIVAS = frozenset(
    {"click", "type", "select", "scroll", "screenshot", "search_page"}
)

_MAX_RESULTADOS_SEARCH_PAGE = 20


def _validar_args_accion(
    accion: str, *, selector: str, texto: str, opcion: str
) -> str | None:
    """Valida los argumentos de una acción interactiva ANTES de tocar
    Playwright. Devuelve `None` si están completos, o un mensaje de error
    listo para el usuario (mismo criterio "problema de negocio → resultado,
    no excepción" del resto del módulo)."""
    if accion == "click" and not selector:
        return "La acción «click» necesita un `selector`."
    if accion == "type":
        if not selector:
            return "La acción «type» necesita un `selector`."
        if not texto:
            return "La acción «type» necesita `texto` (qué escribir)."
    if accion == "select":
        if not selector:
            return "La acción «select» necesita un `selector`."
        if not opcion:
            return "La acción «select» necesita `opcion` (valor a elegir)."
    if accion == "search_page" and not selector and not texto:
        return "La acción «search_page» necesita `selector` o `texto` (qué buscar)."
    return None


async def _accion_playwright(
    page: Any, accion: str, *, selector: str, texto: str, opcion: str
) -> dict[str, Any]:  # pragma: no cover - requiere el extra opcional Playwright
    """Ejecuta UNA acción sobre `page` ya navegada y devuelve `{content, data}`."""
    if accion == "click":
        await page.click(selector)
        return {"content": f"Hice click en «{selector}».", "data": {}}
    if accion == "type":
        await page.fill(selector, texto)
        return {"content": f"Escribí en «{selector}».", "data": {}}
    if accion == "select":
        await page.select_option(selector, opcion)
        return {"content": f"Seleccioné «{opcion}» en «{selector}».", "data": {}}
    if accion == "scroll":
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        return {"content": "Hice scroll al final de la página.", "data": {}}
    if accion == "screenshot":
        png = await page.screenshot(full_page=True)
        if not png:
            # La Ley de la Sección 5 de AGENTS.md: una captura de 0 bytes NO es
            # una captura — se reporta el fallo, nunca un éxito vacío.
            raise RuntimeError("la captura devolvió 0 bytes")
        return {
            "content": "Captura de pantalla tomada.",
            "data": {
                "screenshot_b64": base64.b64encode(png).decode("ascii"),
                "mime": "image/png",
            },
        }
    # search_page
    if selector:
        elementos = await page.query_selector_all(selector)
        textos: list[str] = []
        for elemento in elementos[:_MAX_RESULTADOS_SEARCH_PAGE]:
            textos.append((await elemento.inner_text()).strip())
        return {
            "content": f"Encontré {len(elementos)} elemento(s) para «{selector}».",
            "data": {"coincidencias": len(elementos), "textos": textos},
        }
    cuerpo = await page.inner_text("body")
    presente = texto in cuerpo
    return {
        "content": f"El texto «{texto}» {'aparece' if presente else 'NO aparece'} en la página.",
        "data": {"encontrado": presente},
    }


async def _ejecutar_interaccion(
    ctx: ToolContext,
    async_playwright: Any,
    url: str,
    accion: str,
    *,
    selector: str,
    texto: str,
    opcion: str,
) -> dict[str, Any]:  # pragma: no cover - requiere el extra opcional Playwright
    """Navega a `url` y ejecuta `accion` con Playwright/Chromium real.

    Replica el guardrail SSRF/checkout de `PlaywrightFetcher.fetch()`: registra
    `page.route("**/*", ...)` ANTES de `goto()`, revalida cada navegación del
    frame principal vía `_manejar_ruta_playwright`, y como defensa en
    profundidad revalida `page.url` + `_cadena_de_redirects` tras `goto()` y
    tras la acción. Cualquier bloqueo lanza `httpx.HTTPError`
    (`_error_navegacion_bloqueada`), el mismo tipo que atrapa `run()`.
    """
    user_agent = str(getattr(ctx.settings, "BROWSER_USER_AGENT", "EdecanBot/1.0"))
    timeout_seg = float(getattr(ctx.settings, "BROWSER_TIMEOUT_SECONDS", 20.0))
    motivos_bloqueo: list[str] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page(user_agent=user_agent)

            async def _handler(route: Any) -> None:
                motivo = await _manejar_ruta_playwright(
                    route, main_frame=page.main_frame, settings=ctx.settings
                )
                if motivo is not None:
                    motivos_bloqueo.append(motivo)

            await page.route("**/*", _handler)

            try:
                response = await page.goto(url, timeout=timeout_seg * 1000)
            except Exception as exc:
                if motivos_bloqueo:
                    raise _error_navegacion_bloqueada(url, motivos_bloqueo[0]) from exc
                raise

            if motivos_bloqueo:
                raise _error_navegacion_bloqueada(url, motivos_bloqueo[0])

            for candidata in _cadena_de_redirects(page.url, response):
                motivo = await _validar_navegacion(candidata, ctx.settings)
                if motivo is not None:
                    raise _error_navegacion_bloqueada(candidata, motivo)

            resultado = await _accion_playwright(
                page, accion, selector=selector, texto=texto, opcion=opcion
            )

            if motivos_bloqueo:
                raise _error_navegacion_bloqueada(page.url, motivos_bloqueo[-1])

            resultado["data"]["url_final"] = page.url
            return resultado
        finally:
            await browser.close()


async def _intentar_accion(
    ctx: ToolContext,
    url: str,
    accion: str,
    *,
    selector: str,
    texto: str,
    opcion: str,
) -> tuple[ToolResult, bool, str]:
    """Ejecuta la acción interactiva y devuelve `(resultado, exito, causa)`.

    Extraído de `NavegarWebInteractivoTool.run` para que el capturador de
    enseñanza decida, con el MISMO veredicto, si registra una «Acción» o una
    «Decisión» — nunca se fabrica éxito (AGENTS.md §13.1). `exito` es `True`
    solo si la acción se ejecutó; `causa` (no vacía si `exito` es `False`) es
    el motivo del fallo, listo para mostrar o persistir.
    """
    # Guardrail de navegación ANTES de levantar Chromium (mismo portero que
    # las tools de solo lectura): checkout/pago/login y SSRF se rechazan acá.
    veredicto = await check_navigation(url, ctx.settings)
    if not veredicto.allowed:
        causa = veredicto.reason or f"No puedo navegar «{url}»."
        return ToolResult(content=causa), False, causa

    # Import diferido y honesto: sin el extra opcional de Playwright se
    # devuelve un error claro — nunca un éxito falso.
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        causa = (
            "La navegación interactiva necesita Playwright, que no está instalado. "
            "Instálalo con `uv pip install 'edecan-browser[playwright]'` y luego "
            "`playwright install chromium` (ver docs/navegador.md)."
        )
        return ToolResult(content=causa), False, causa

    # En la app congelada el driver node extraído pierde sus entitlements de
    # JIT y V8 muere antes de arrancar; se re-firma una vez, best-effort
    # (`_driver_macos` documenta la causa completa).
    asegurar_driver_playwright_macos()
    asegurar_chromium_instalado()

    try:
        resultado = await _ejecutar_interaccion(
            ctx,
            async_playwright,
            url,
            accion,
            selector=selector,
            texto=texto,
            opcion=opcion,
        )
    except httpx.HTTPError as exc:
        causa = f"No pude navegar «{url}»: {exc}."
        return ToolResult(content=causa), False, causa
    except Exception as exc:  # pragma: no cover - fallas de Playwright/red
        logger.exception("Falla en navegación interactiva de %s", url)
        causa = f"La acción «{accion}» sobre «{url}» falló: {exc}."
        return ToolResult(content=causa), False, causa

    return ToolResult(content=resultado["content"], data=resultado["data"]), True, ""


def _recorder_teach() -> Any:
    """Resuelve `capturar_paso_navegacion` de forma diferida y best-effort.

    `edecan_companion` (la app que captura pasos de enseñanza) NO es una
    dependencia de `edecan_browser`; el recorder solo existe cuando el
    navegador corre dentro del companion. Import diferido y guardeado: si el
    paquete no es importable (p. ej. `navegar_web_interactivo` hospedado en la
    API), se devuelve `None` y la captura se omite sin tocar la acción.
    """
    try:
        from edecan_companion.teach_capture import capturar_paso_navegacion

        return capturar_paso_navegacion
    except ImportError:
        logger.debug("Recorder de enseñanza no disponible (edecan_companion no importable).")
        return None


async def _capturar_teach_step(
    teach_session_id: str,
    *,
    accion: str,
    url: str,
    selector: str,
    decision: str,
    output: str,
) -> None:
    """Registra el resultado de `navegar_web_interactivo` en la sesión de
    enseñanza activa (product design), best-effort: si el recorder no está
    disponible o el POST falla, la acción del navegador YA terminó y su
    resultado se devuelve igual — la captura jamás falla la navegación.
    """
    recorder = _recorder_teach()
    if recorder is None:
        return
    try:
        await recorder(
            teach_session_id,
            accion=accion,
            url=url,
            selector=selector,
            decision=decision,
            output=output,
        )
    except Exception as exc:  # pragma: no cover - red/API abajo
        logger.warning(
            "No se pudo registrar el paso de enseñanza en «%s»: %s", teach_session_id, exc
        )


class NavegarWebInteractivoTool(Tool):
    name = "navegar_web_interactivo"
    description = (
        "Abre una URL pública con un navegador real (Playwright/Chromium) y ejecuta UNA "
        "acción interactiva: click, type, select, scroll, screenshot o search_page. "
        "Escribe/clics sobre la página, por eso es peligrosa y exige confirmación. "
        "Respeta los mismos guardrails que las tools de solo lectura: NUNCA navega "
        "checkouts, pagos, inicios de sesión ni direcciones privadas (SSRF)."
    )
    category = "browser"
    risk_level = "high"
    latency_class = "slow"
    dangerous = True
    requires_flags = frozenset({_FLAG_BROWSER})
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL http(s) a abrir antes de la acción."},
            "accion": {
                "type": "string",
                "enum": sorted(_ACCIONES_INTERACTIVAS),
                "description": "Acción a ejecutar sobre la página ya abierta.",
            },
            "selector": {
                "type": "string",
                "description": "Selector CSS del elemento (click/type/select/search_page).",
            },
            "texto": {
                "type": "string",
                "description": "Texto a escribir (type) o a buscar en la página (search_page).",
            },
            "opcion": {
                "type": "string",
                "description": "Valor de la opción a elegir (select).",
            },
            "teach_session_id": {
                "type": "string",
                "description": (
                    "Opcional: si estás enseñando una tarea (product design), el id de la "
                    "sesión de enseñanza donde registrar este paso al terminar la acción."
                ),
            },
        },
        "required": ["url", "accion"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        url = str(args.get("url", "")).strip()
        teach_session_id = str(args.get("teach_session_id", "") or "").strip()
        if not url:
            return ToolResult(content="Dime qué URL quieres que abra.")

        accion = str(args.get("accion", "")).strip().lower()
        if accion not in _ACCIONES_INTERACTIVAS:
            return ToolResult(
                content=(
                    f"Acción «{accion}» no soportada. Acciones disponibles: "
                    f"{', '.join(sorted(_ACCIONES_INTERACTIVAS))}."
                )
            )

        selector = str(args.get("selector", "") or "").strip()
        texto = str(args.get("texto", "") or "").strip()
        opcion = str(args.get("opcion", "") or "").strip()

        error_args = _validar_args_accion(accion, selector=selector, texto=texto, opcion=opcion)
        if error_args is not None:
            return ToolResult(content=error_args)

        resultado, exito, causa = await _intentar_accion(
            ctx, url, accion, selector=selector, texto=texto, opcion=opcion
        )

        # Enseñar-haciendo (product design): si la acción corre dentro de una
        # sesión de enseñanza activa, se registra su resultado como paso
        # estructurado. Éxito → «Acción»; fracaso → «Decisión» (`accion=""`,
        # `decision=causa`) — nunca se fabrica un éxito. La captura es
        # best-effort: si falla, la acción del navegador ya terminó y su
        # resultado se devuelve igual.
        if teach_session_id:
            await _capturar_teach_step(
                teach_session_id,
                accion=accion if exito else "",
                url=url,
                selector=selector if exito else "",
                decision="" if exito else causa,
                output=resultado.content if exito else "",
            )

        return resultado
