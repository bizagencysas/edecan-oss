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

import json
import logging
import re
from typing import Any

import httpx
from edecan_core import Tool, ToolContext, ToolResult
from edecan_llm.base import ChatMessage, CompletionRequest
from edecan_toolkit.research import get_tenant_search_provider

from .extract import ExtractedPage, extract_page, render_markdown
from .fetch import get_fetcher
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
        )


class ExtraerDatosWebTool(Tool):
    name = "extraer_datos_web"
    description = (
        "Abre una URL pública y usa el modelo para extraer SOLO los campos pedidos, "
        "como un objeto JSON. Solo lee (GET) — nunca completa formularios ni compra nada."
    )
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
