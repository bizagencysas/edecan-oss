"""Tests de `edecan_browser.policy`: `check_navigation` y `RobotsCache`.

Todas las llamadas HTTP van por `respx` (incluso los casos que no deberían
disparar ninguna: envolver también esos en `@respx.mock` sin registrar
rutas hace que cualquier llamada de red real/accidental falle de inmediato
con un error de `respx` en vez de intentar salir a internet o colgarse).
"""

from __future__ import annotations

import httpx
import pytest
import respx
from edecan_browser import policy

# --- Esquema -----------------------------------------------------------------


@respx.mock
async def test_esquema_no_http_rechaza(fake_settings):
    for url in ("ftp://tienda.ejemplo.com/archivo", "file:///etc/passwd", "javascript:alert(1)"):
        resultado = await policy.check_navigation(url, fake_settings())
        assert resultado.allowed is False


# --- Exclusión de cumplimiento: LinkedIn --------------------------------------


@respx.mock
@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com/in/alguien",
        "https://linkedin.com/in/alguien",
        "http://linkedin.com/",
        "https://mobile.linkedin.com/in/alguien",
        "https://LinkedIn.com/in/Alguien",  # host case-insensitive
    ],
)
async def test_linkedin_excluido_por_cumplimiento_rechaza(fake_settings, url):
    # Sin ninguna ruta registrada en respx: si `check_navigation` no bloqueara
    # esto ANTES de la red (robots.txt incluido), este test fallaría con un
    # error de respx en vez de aprobar por accidente.
    resultado = await policy.check_navigation(url, fake_settings())
    assert resultado.allowed is False
    assert "linkedin" in resultado.reason.lower()


@respx.mock
async def test_linkedin_excluido_gana_sobre_mensaje_transaccional(fake_settings):
    """Una URL de login de LinkedIn matchea también la regex transaccional —
    el motivo devuelto debe ser el de cumplimiento (más específico y
    correcto), no el genérico de checkout/login."""
    resultado = await policy.check_navigation("https://www.linkedin.com/login", fake_settings())
    assert resultado.allowed is False
    assert "linkedin" in resultado.reason.lower()


@respx.mock
async def test_dominio_que_solo_menciona_linkedin_en_la_url_no_se_bloquea_por_error(
    fake_settings,
):
    """El guardrail es por HOST, no por substring de la URL completa: un
    artículo de noticias ajeno que solo mencione "linkedin" en el path no
    debe bloquearse (evita falsos positivos amplios)."""
    respx.get("https://noticias.ejemplo.com/robots.txt").mock(return_value=httpx.Response(404))
    resultado = await policy.check_navigation(
        "https://noticias.ejemplo.com/articulo-sobre-linkedin-ipo", fake_settings()
    )
    assert resultado.allowed is True


# --- Blocklist de rutas transaccionales --------------------------------------


@respx.mock
@pytest.mark.parametrize(
    "url",
    [
        "https://tienda.ejemplo.com/checkout/pagar",
        "https://tienda.ejemplo.com/cart/123",
        "https://tienda.ejemplo.com/carrito",
        "https://banco.ejemplo.com/payment/confirmar",
        "https://tienda.ejemplo.com/pago/tarjeta",
        "https://app.ejemplo.com/login",
        "https://app.ejemplo.com/signin?next=/home",
        "https://app.ejemplo.com/account/ajustes",
    ],
)
async def test_blocklist_rutas_transaccionales_rechaza(fake_settings, url):
    resultado = await policy.check_navigation(url, fake_settings())
    assert resultado.allowed is False
    assert "compra" in resultado.reason.lower() or "login" in resultado.reason.lower()


@respx.mock
async def test_blocklist_no_afecta_rutas_normales(fake_settings):
    respx.get("https://tienda.ejemplo.com/robots.txt").mock(return_value=httpx.Response(404))
    resultado = await policy.check_navigation(
        "https://tienda.ejemplo.com/producto/42", fake_settings()
    )
    assert resultado.allowed is True


# --- SSRF ---------------------------------------------------------------------


@respx.mock
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/interno",
        "http://169.254.169.254/latest/meta-data/",  # metadata AWS/GCP
        "http://10.1.2.3/panel",
        "http://192.168.1.1/router",
        "http://[::1]/interno",
        "http://localhost:5000/admin",
        "http://metadata.google.internal/computeMetadata/v1/",
    ],
)
async def test_ssrf_ip_o_host_privado_literal_rechaza(fake_settings, url):
    resultado = await policy.check_navigation(url, fake_settings())
    assert resultado.allowed is False
    assert "SSRF" in resultado.reason


@respx.mock
async def test_ssrf_dominio_que_resuelve_a_ip_privada_rechaza(fake_settings, monkeypatch):
    async def _resolver_privado(hostname: str) -> list[str]:
        return ["10.1.2.3"]

    monkeypatch.setattr(policy, "resolve_hostname_ips", _resolver_privado)
    resultado = await policy.check_navigation(
        "https://interno.corp.ejemplo.com/panel", fake_settings()
    )
    assert resultado.allowed is False
    assert "SSRF" in resultado.reason


@respx.mock
async def test_ssrf_fallo_de_dns_bloquea_por_seguridad(fake_settings, monkeypatch):
    async def _resolver_roto(hostname: str) -> list[str]:
        raise OSError("simulated DNS failure")

    monkeypatch.setattr(policy, "resolve_hostname_ips", _resolver_roto)
    resultado = await policy.check_navigation("https://dominio-raro.ejemplo.com/x", fake_settings())
    assert resultado.allowed is False


@respx.mock
async def test_dominio_publico_normal_no_lo_bloquea_ssrf(fake_settings):
    respx.get("https://tienda.ejemplo.com/robots.txt").mock(return_value=httpx.Response(404))
    resultado = await policy.check_navigation(
        "https://tienda.ejemplo.com/producto/1", fake_settings()
    )
    assert resultado.allowed is True


# --- robots.txt -----------------------------------------------------------


@respx.mock
async def test_robots_disallow_rechaza(fake_settings):
    respx.get("https://tienda.ejemplo.com/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /privado\n")
    )
    resultado = await policy.check_navigation(
        "https://tienda.ejemplo.com/privado/pagina", fake_settings()
    )
    assert resultado.allowed is False
    assert "robots.txt" in resultado.reason


@respx.mock
async def test_robots_permite_ruta_fuera_de_disallow(fake_settings):
    respx.get("https://tienda.ejemplo.com/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /privado\n")
    )
    resultado = await policy.check_navigation("https://tienda.ejemplo.com/publico", fake_settings())
    assert resultado.allowed is True


@respx.mock
async def test_robots_ausente_o_con_error_permite_todo(fake_settings):
    respx.get("https://a.ejemplo.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://b.ejemplo.com/robots.txt").mock(side_effect=httpx.ConnectError("no llega"))

    resultado_a = await policy.check_navigation("https://a.ejemplo.com/x", fake_settings())
    resultado_b = await policy.check_navigation("https://b.ejemplo.com/x", fake_settings())

    assert resultado_a.allowed is True
    assert resultado_b.allowed is True


@respx.mock
async def test_robots_txt_se_cachea_por_origen(fake_settings):
    ruta = respx.get("https://tienda.ejemplo.com/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow:\n")
    )

    await policy.check_navigation("https://tienda.ejemplo.com/a", fake_settings())
    await policy.check_navigation("https://tienda.ejemplo.com/b", fake_settings())

    assert ruta.call_count == 1


@respx.mock
async def test_robots_cache_respeta_ttl(fake_settings):
    ruta = respx.get("https://tienda.ejemplo.com/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow:\n")
    )
    cache = policy.RobotsCache(ttl_seconds=0)  # TTL 0 = nunca reutiliza

    await cache.permite(
        origin="https://tienda.ejemplo.com",
        url="https://tienda.ejemplo.com/a",
        user_agent="EdecanBot/1.0",
        timeout=5.0,
    )
    await cache.permite(
        origin="https://tienda.ejemplo.com",
        url="https://tienda.ejemplo.com/b",
        user_agent="EdecanBot/1.0",
        timeout=5.0,
    )

    assert ruta.call_count == 2


# --- GET only (propiedad estructural) -----------------------------------------


def test_no_hay_codigo_de_post_en_el_paquete():
    """ "GET only": ningún módulo de `edecan_browser` usa `.post(`/`client.post`
    ni envía formularios — guardrail estructural pedido por el work package.
    """
    import pathlib

    raiz = pathlib.Path(__file__).resolve().parent.parent / "edecan_browser"
    for archivo in raiz.glob("*.py"):
        contenido = archivo.read_text(encoding="utf-8")
        assert ".post(" not in contenido, f"{archivo} parece invocar un POST"
