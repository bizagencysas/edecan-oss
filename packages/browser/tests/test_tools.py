"""Tests de `edecan_browser.tools`: `navegar_web`, `extraer_datos_web`,
`comparar_precios` — encadenan policy→fetch→extract(→LLM) de punta a punta,
todo con `respx` (HTTP) y `FakeLLM`/`FakeSearch` (LLM y búsqueda) offline.

`comparar_precios` usa a propósito `edecan_toolkit.research.get_tenant_search_provider`
real (vía `ctx.session`/`ctx.vault`/`ctx.settings`, que sin credencial de
tenant conectada resuelve a `StubSearch` real de `edecan_toolkit` — "tenant →
stub", nunca un paso de plataforma, ver el docstring de ese módulo) — es
código de producción de `edecan_browser.tools` importando un hermano,
permitido por `ARCHITECTURE.md` §10.1; la mayoría de los tests de este
archivo no hacen ese import, solo dejan `ctx.session`/`ctx.vault` en `None`
(default de `make_ctx`) y dejan que la tool haga su trabajo — la sección
"bring-your-own" al final SÍ ejercita `ctx.session`/`ctx.vault` de verdad con
los dobles `FakeSession`/`FakeVault` de `conftest.py`.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import edecan_browser.tools as tools_mod
import httpx
import respx
from edecan_browser.tools import CompararPreciosTool, ExtraerDatosWebTool, NavegarWebTool


def test_tools_no_son_dangerous_y_requieren_flag_browser():
    for tool_cls in (NavegarWebTool, ExtraerDatosWebTool, CompararPreciosTool):
        tool = tool_cls()
        assert tool.dangerous is False
        assert tool.requires_flags == frozenset({"tools.browser"})


# --- navegar_web ---------------------------------------------------------------


@respx.mock
async def test_navegar_web_happy_path(make_ctx, fake_settings):
    respx.get("https://tienda.ejemplo.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://tienda.ejemplo.com/producto/1").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=(
                "<html><head><title>Producto 1</title></head>"
                "<body><p>Info del producto.</p><a href='/otro'>Otro</a></body></html>"
            ),
        )
    )
    ctx = make_ctx(settings=fake_settings())

    resultado = await NavegarWebTool().run(ctx, {"url": "https://tienda.ejemplo.com/producto/1"})

    assert resultado.data["titulo"] == "Producto 1"
    assert resultado.data["url_final"] == "https://tienda.ejemplo.com/producto/1"
    assert "https://tienda.ejemplo.com/otro" in resultado.data["enlaces"]
    assert "Producto 1" in resultado.content


async def test_navegar_web_sin_url(make_ctx):
    resultado = await NavegarWebTool().run(make_ctx(), {"url": "   "})
    assert "url" in resultado.content.lower()


@respx.mock
async def test_navegar_web_bloqueado_por_policy_no_intenta_fetch(make_ctx, fake_settings):
    # Sin ninguna ruta registrada en respx: si la tool intentara un fetch real
    # pese al rechazo de policy, este test fallaría con un error de respx.
    ctx = make_ctx(settings=fake_settings())

    resultado = await NavegarWebTool().run(
        ctx, {"url": "https://tienda.ejemplo.com/checkout/pagar"}
    )

    assert resultado.data is None
    assert "compra" in resultado.content.lower() or "pago" in resultado.content.lower()


@respx.mock
async def test_navegar_web_ssrf_no_intenta_fetch(make_ctx, fake_settings):
    ctx = make_ctx(settings=fake_settings())

    resultado = await NavegarWebTool().run(ctx, {"url": "http://127.0.0.1:9000/interno"})

    assert "SSRF" in resultado.content


@respx.mock
async def test_navegar_web_linkedin_no_intenta_fetch(make_ctx, fake_settings):
    """Repro de punta a punta de la auditoría "riesgo-legal-tos": antes del
    fix, `navegar_web` no tenía ningún guardrail de código para LinkedIn (solo
    la instrucción del system prompt), así que un pedido como '¿qué dice este
    perfil? https://www.linkedin.com/in/alguien' habría hecho el GET real y
    devuelto el contenido extraído. Sin ninguna ruta registrada en respx: si
    la tool intentara el fetch pese al rechazo de policy, este test fallaría
    con un error de respx en vez de devolver contenido real que oculte la
    regresión."""
    ctx = make_ctx(settings=fake_settings())

    resultado = await NavegarWebTool().run(ctx, {"url": "https://www.linkedin.com/in/alguien"})

    assert resultado.data is None
    assert "linkedin" in resultado.content.lower()


@respx.mock
async def test_navegar_web_redirect_hacia_metadata_de_nube_no_se_sigue(make_ctx, fake_settings):
    """Repro de punta a punta del hallazgo SSRF-vía-redirect: `check_navigation`
    aprueba la URL pública original (`_fetch_y_extraer` la valida), pero esa
    página responde con un 302 hacia la metadata de nube. Antes del fix,
    `HttpxFetcher` seguía el redirect solo y `navegar_web` devolvía ese
    contenido íntegro; ahora el salto se re-valida y se rechaza."""
    respx.get("https://tienda.ejemplo.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://tienda.ejemplo.com/oferta").mock(
        return_value=httpx.Response(
            302,
            headers={
                "location": "http://169.254.169.254/latest/meta-data/iam/security-credentials/"
            },
        )
    )
    # A propósito NO se mockea la URL de metadata: si la tool la siguiera pese a
    # todo, este test fallaría con un error de respx en vez de devolver
    # contenido falso que oculte la regresión.
    ctx = make_ctx(settings=fake_settings())

    resultado = await NavegarWebTool().run(ctx, {"url": "https://tienda.ejemplo.com/oferta"})

    # Sin datos de página (título/enlaces): la tool nunca llegó a extraer nada
    # del destino del redirect, solo devuelve el motivo del rechazo — que
    # SÍ puede nombrar la IP bloqueada (transparencia), pero no es contenido
    # real de la página de metadata (que nunca se llegó a pedir).
    assert resultado.data is None
    assert "SSRF" in resultado.content


# --- extraer_datos_web -----------------------------------------------------


@respx.mock
async def test_extraer_datos_web_happy_path_filtra_a_solo_los_campos_pedidos(
    make_ctx, make_llm, fake_settings
):
    respx.get("https://tienda.ejemplo.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://tienda.ejemplo.com/libro/1").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><head><title>Libro</title></head><body><p>Autor: Ana</p></body></html>",
        )
    )
    llm = make_llm([json.dumps({"autor": "Ana", "precio": 19.99, "ruido": "no debería quedar"})])
    ctx = make_ctx(llm=llm, settings=fake_settings())

    resultado = await ExtraerDatosWebTool().run(
        ctx, {"url": "https://tienda.ejemplo.com/libro/1", "campos": ["autor", "precio"]}
    )

    assert resultado.data["campos"] == {"autor": "Ana", "precio": 19.99}
    assert "ruido" not in resultado.data["campos"]
    assert json.loads(resultado.content) == {"autor": "Ana", "precio": 19.99}


@respx.mock
async def test_extraer_datos_web_completa_campos_faltantes_con_null(
    make_ctx, make_llm, fake_settings
):
    respx.get("https://tienda.ejemplo.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://tienda.ejemplo.com/libro/2").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/html"}, text="<p>sin datos</p>"
        )
    )
    llm = make_llm([json.dumps({"autor": "Ana"})])  # falta "precio"
    ctx = make_ctx(llm=llm, settings=fake_settings())

    resultado = await ExtraerDatosWebTool().run(
        ctx, {"url": "https://tienda.ejemplo.com/libro/2", "campos": ["autor", "precio"]}
    )

    assert resultado.data["campos"] == {"autor": "Ana", "precio": None}


@respx.mock
async def test_extraer_datos_web_llm_no_json_da_error_claro(make_ctx, make_llm, fake_settings):
    respx.get("https://tienda.ejemplo.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://tienda.ejemplo.com/libro/3").mock(
        return_value=httpx.Response(200, headers={"content-type": "text/html"}, text="<p>x</p>")
    )
    llm = make_llm(["esto no es json"])
    ctx = make_ctx(llm=llm, settings=fake_settings())

    resultado = await ExtraerDatosWebTool().run(
        ctx, {"url": "https://tienda.ejemplo.com/libro/3", "campos": ["autor"]}
    )

    assert resultado.data is None
    assert "no logré extraer" in resultado.content.lower()


async def test_extraer_datos_web_sin_url(make_ctx):
    resultado = await ExtraerDatosWebTool().run(make_ctx(), {"url": "", "campos": ["a"]})
    assert "url" in resultado.content.lower()


async def test_extraer_datos_web_sin_campos(make_ctx):
    resultado = await ExtraerDatosWebTool().run(
        make_ctx(), {"url": "https://ejemplo.com/x", "campos": []}
    )
    assert "campo" in resultado.content.lower()


# --- comparar_precios -----------------------------------------------------


async def test_comparar_precios_sin_producto(make_ctx):
    resultado = await CompararPreciosTool().run(make_ctx(), {"producto": "   "})
    assert "producto" in resultado.content.lower()


@respx.mock
async def test_comparar_precios_arma_tabla_ordenada_por_precio(make_ctx, make_llm, fake_settings):
    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(url__regex=r"^https://example\.com/search\?.*$").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><head><title>Resultado</title></head><body>Zapatillas X</body></html>",
        )
    )
    # A propósito NO vienen ya ordenadas por precio: si la tabla final queda
    # ordenada, es porque `comparar_precios` ordenó de verdad, no por suerte
    # del orden en que llegaron las páginas.
    respuestas = [
        json.dumps(
            {
                "tienda": "Tienda B",
                "producto": "Zapatillas X",
                "precio": 50,
                "moneda": "usd",
                "disponible": True,
            }
        ),
        json.dumps(
            {
                "tienda": "Tienda A",
                "producto": "Zapatillas X",
                "precio": 30,
                "moneda": "usd",
                "disponible": True,
            }
        ),
        json.dumps(
            {
                "tienda": "Tienda C",
                "producto": "Zapatillas X",
                "precio": 40,
                "moneda": "usd",
                "disponible": False,
            }
        ),
    ]
    llm = make_llm(respuestas)
    ctx = make_ctx(llm=llm, settings=fake_settings(SEARCH_PROVIDER="stub"))

    resultado = await CompararPreciosTool().run(ctx, {"producto": "Zapatillas X", "max_fuentes": 3})

    tiendas_en_orden = [f["tienda"] for f in resultado.data["resultados"]]
    assert tiendas_en_orden == ["Tienda A", "Tienda C", "Tienda B"]
    assert resultado.data["resultados"][0]["precio"] == 30.0
    assert "Mejor oferta: Tienda A" in resultado.content
    assert "Edecán no realiza compras" in resultado.content
    assert "Edecán no realiza compras" in resultado.data["aviso"]


@respx.mock
async def test_comparar_precios_respeta_tope_de_max_fuentes(make_ctx, make_llm, fake_settings):
    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(url__regex=r"^https://example\.com/search\?.*$").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><head><title>P</title></head></html>",
        )
    )
    respuestas = [
        json.dumps(
            {
                "tienda": f"Tienda {i}",
                "producto": "P",
                "precio": i,
                "moneda": "usd",
                "disponible": True,
            }
        )
        for i in range(1, 11)
    ]
    llm = make_llm(respuestas)
    ctx = make_ctx(llm=llm, settings=fake_settings(SEARCH_PROVIDER="stub"))

    resultado = await CompararPreciosTool().run(ctx, {"producto": "P", "max_fuentes": 999})

    assert len(resultado.data["resultados"]) == 5  # tope duro, aunque se pidan 999


@respx.mock
async def test_comparar_precios_sin_resultados_utiles_avisa_igual(
    make_ctx, make_llm, fake_settings
):
    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(url__regex=r"^https://example\.com/search\?.*$").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/html"}, text="<p>nada útil</p>"
        )
    )
    llm = make_llm(["no es json"])
    ctx = make_ctx(llm=llm, settings=fake_settings(SEARCH_PROVIDER="stub"))

    resultado = await CompararPreciosTool().run(ctx, {"producto": "Cosa Rara", "max_fuentes": 3})

    assert resultado.data["resultados"] == []
    assert "Cosa Rara" in resultado.content
    assert "Edecán no realiza compras" in resultado.content


class _BuscadorConCheckout:
    """`SearchProvider` falso que a propósito devuelve una URL de checkout
    junto con una válida, para probar que `comparar_precios` la descarta.
    """

    async def search(self, query: str, k: int = 5) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(title="Malo", url="https://tienda.ejemplo.com/checkout/x", snippet=""),
            SimpleNamespace(title="Bueno", url="https://tienda.ejemplo.com/producto/y", snippet=""),
        ]


class _BuscadorConLinkedIn:
    """`SearchProvider` falso que a propósito devuelve un perfil de LinkedIn
    junto con una URL válida (plausible en la vida real: un buscador general
    puede devolver un perfil de LinkedIn al buscar precio/consultoría de una
    persona), para probar que `comparar_precios` lo descarta igual que un
    checkout — auditoría "riesgo-legal-tos".
    """

    async def search(self, query: str, k: int = 5) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(title="Malo", url="https://www.linkedin.com/in/alguien", snippet=""),
            SimpleNamespace(title="Bueno", url="https://tienda.ejemplo.com/producto/y", snippet=""),
        ]


@respx.mock
async def test_comparar_precios_descarta_fuentes_bloqueadas_por_policy(
    make_ctx, make_llm, fake_settings, monkeypatch
):
    """Si el buscador devolviera una URL de checkout, comparar_precios la
    salta sin fetch — la tabla se arma solo con las fuentes permitidas."""

    async def _fake_get_tenant_search_provider(ctx: Any) -> _BuscadorConCheckout:
        return _BuscadorConCheckout()

    monkeypatch.setattr(tools_mod, "get_tenant_search_provider", _fake_get_tenant_search_provider)
    respx.get("https://tienda.ejemplo.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://tienda.ejemplo.com/producto/y").mock(
        return_value=httpx.Response(200, headers={"content-type": "text/html"}, text="<p>ok</p>")
    )
    llm = make_llm(
        [
            json.dumps(
                {
                    "tienda": "Bueno",
                    "producto": "P",
                    "precio": 10,
                    "moneda": "usd",
                    "disponible": True,
                }
            )
        ]
    )
    ctx = make_ctx(llm=llm, settings=fake_settings())

    resultado = await CompararPreciosTool().run(ctx, {"producto": "P", "max_fuentes": 5})

    tiendas = [f["tienda"] for f in resultado.data["resultados"]]
    assert tiendas == ["Bueno"]


@respx.mock
async def test_comparar_precios_descarta_fuentes_de_linkedin(
    make_ctx, make_llm, fake_settings, monkeypatch
):
    """Si el buscador devolviera un perfil de LinkedIn, comparar_precios lo
    salta sin fetch — mismo guardrail de policy que descarta un checkout,
    auditoría "riesgo-legal-tos". Sin mockear la URL de LinkedIn en respx: si
    la tool intentara el fetch pese al rechazo, este test fallaría con un
    error de respx en vez de aprobar por accidente."""

    async def _fake_get_tenant_search_provider(ctx: Any) -> _BuscadorConLinkedIn:
        return _BuscadorConLinkedIn()

    monkeypatch.setattr(tools_mod, "get_tenant_search_provider", _fake_get_tenant_search_provider)
    respx.get("https://tienda.ejemplo.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://tienda.ejemplo.com/producto/y").mock(
        return_value=httpx.Response(200, headers={"content-type": "text/html"}, text="<p>ok</p>")
    )
    llm = make_llm(
        [
            json.dumps(
                {
                    "tienda": "Bueno",
                    "producto": "P",
                    "precio": 10,
                    "moneda": "usd",
                    "disponible": True,
                }
            )
        ]
    )
    ctx = make_ctx(llm=llm, settings=fake_settings())

    resultado = await CompararPreciosTool().run(ctx, {"producto": "P", "max_fuentes": 5})

    tiendas = [f["tienda"] for f in resultado.data["resultados"]]
    assert tiendas == ["Bueno"]


# --- comparar_precios: bring-your-own (auditoría "riesgo-legal-tos") ------------


@respx.mock
async def test_comparar_precios_usa_la_credencial_de_busqueda_del_tenant(
    make_ctx, make_llm, make_session, make_vault, fake_settings
):
    """Extremo a extremo: si el tenant conectó su propia key de Brave (`PUT
    /v1/credentials/search`), `comparar_precios` la usa de verdad —
    ejercitando `edecan_toolkit.research.get_tenant_search_provider` a través
    de `ctx.session`/`ctx.vault` (dobles LOCALES de este paquete, ver
    `conftest.py`), sin nada mockeado a mano en `tools_mod`."""
    respx.get("https://api.search.brave.com/res/v1/web/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": "Bueno",
                            "url": "https://tienda.ejemplo.com/producto/y",
                            "description": "",
                        }
                    ]
                }
            },
        )
    )
    respx.get("https://tienda.ejemplo.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://tienda.ejemplo.com/producto/y").mock(
        return_value=httpx.Response(200, headers={"content-type": "text/html"}, text="<p>ok</p>")
    )
    bundle = SimpleNamespace(
        access_token=json.dumps({"provider": "brave", "api_key": "clave-brave-del-tenant"})
    )
    session = make_session([[{"id": "77777777-7777-7777-7777-777777777777"}]])
    vault = make_vault(bundle=bundle)
    llm = make_llm(
        [
            json.dumps(
                {
                    "tienda": "Bueno",
                    "producto": "P",
                    "precio": 10,
                    "moneda": "usd",
                    "disponible": True,
                }
            )
        ]
    )
    ctx = make_ctx(llm=llm, session=session, vault=vault, settings=fake_settings())

    resultado = await CompararPreciosTool().run(ctx, {"producto": "P", "max_fuentes": 5})

    tiendas = [f["tienda"] for f in resultado.data["resultados"]]
    assert tiendas == ["Bueno"]
    # Confirma que sí pasó por el resolver bring-your-own del tenant (no el stub).
    assert session.llamadas[0][1]["connector_key"] == "search"


@respx.mock
async def test_comparar_precios_sin_credencial_de_tenant_nunca_usa_la_config_de_plataforma(
    make_ctx, make_llm, make_session, make_vault, fake_settings
):
    """Regresión directa del fix (`DIRECCION_ACTUAL.md` "nunca una llave
    compartida de plataforma"): el tenant NO conectó `PUT
    /v1/credentials/search`, y `settings` trae `SEARCH_PROVIDER=brave` +
    `BRAVE_API_KEY` completos y válidos — `comparar_precios` debe caer al
    `StubSearch` (`StubSearch` pega a `https://example.com/...`, nunca a la
    API real de Brave). La API de Brave NO se mockea a propósito: si
    `comparar_precios` volviera a caer a la config de plataforma (el
    comportamiento viejo), este test fallaría con un error de `respx` en vez
    de pasar de casualidad."""
    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(url__regex=r"^https://example\.com/search\?.*$").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><head><title>Resultado</title></head><body>Producto</body></html>",
        )
    )
    llm = make_llm(
        [
            json.dumps(
                {"tienda": "T", "producto": "P", "precio": 5, "moneda": "usd", "disponible": True}
            )
        ]
    )
    ctx = make_ctx(
        llm=llm,
        session=make_session([[]]),
        vault=make_vault(),
        settings=fake_settings(SEARCH_PROVIDER="brave", BRAVE_API_KEY="clave-de-plataforma"),
    )

    resultado = await CompararPreciosTool().run(ctx, {"producto": "P", "max_fuentes": 3})

    # StubSearch (offline) devuelve URLs `example.com` repetidas hasta llenar
    # `max_fuentes`; lo importante no es la cantidad sino que llegó hasta acá
    # sin pegarle jamás a `https://api.search.brave.com` (sin mock -> respx
    # habría hecho fallar el test antes de esta línea).
    assert len(resultado.data["resultados"]) == 3
    assert all(f["tienda"] == "T" for f in resultado.data["resultados"])
