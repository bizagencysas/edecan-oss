"""La lectura del artículo completo (`investigacion.leer_articulo`) y su viaje al prompt.

El defecto que estos tests fijan (01-ago-2026, con capturas del dueño): el escritor
recibía como único material el titular del RSS más "fuente · hace N h" -- con eso
cualquier modelo especula, el auditor recorta la especulación entera (461 -> 98
caracteres en el caso real) y el rescate entrega el crudo "Sin revisar". REFERENCIA, con la
misma noticia y el mismo modelo de imagen, produce un post con hechos porque su escritor
LEYÓ el artículo. El material manda más que el modelo.
"""

from __future__ import annotations

import base64

import httpx
from edecan_creative.investigacion import (
    _texto_de_articulo,
    _url_real_de_google_news,
    leer_articulo,
)
from edecan_creative.redaccion import _bloque_fuentes, _con_cuerpo

_PARRAFO = (
    "La actualización, presentada este primero de agosto, corrige la deformación de "
    "personajes y objetos entre planos, el defecto más reportado por los editores."
)

_HTML = f"""
<html><head><script>var x = "no soy contenido";</script></head>
<body>
<nav><p>Inicio · Secciones · Suscríbete a nuestro boletín diario de noticias hoy</p></nav>
<p>Foto: agencia.</p>
<article><p>{_PARRAFO}</p>
<p>El segundo párrafo agrega el contexto de mercado y a quién le cambia el flujo de
trabajo, con el detalle que un titular jamás trae consigo.</p></article>
<footer><p>Todos los derechos reservados por el medio y sus redactores del mundo.</p></footer>
</body></html>
"""


def test_url_real_dentro_del_base64_del_enlace_de_google_news() -> None:
    real = "https://diario.example/nota-seedance"
    token = (
        base64.urlsafe_b64encode(b"\x08\x13\x22" + real.encode() + b"\xd2\x01\x00")
        .decode()
        .rstrip("=")
    )
    url = f"https://news.google.com/rss/articles/{token}?oc=5"
    assert _url_real_de_google_news(url) == real


def test_url_real_devuelve_none_para_token_cifrado_o_enlace_normal() -> None:
    # Token moderno (cifrado): el base64 no contiene ninguna URL -> plan B del llamador.
    cifrado = base64.urlsafe_b64encode(b"\x01\x02\x03sin urls aqui\x04").decode().rstrip("=")
    assert _url_real_de_google_news(f"https://news.google.com/rss/articles/{cifrado}") is None
    # Un enlace que no es de Google News no tiene nada que decodificar.
    assert _url_real_de_google_news("https://diario.example/nota") is None


def test_texto_de_articulo_extrae_los_parrafos_del_cuerpo_y_nada_mas() -> None:
    texto = _texto_de_articulo(_HTML, max_chars=4000)
    assert _PARRAFO in texto
    assert "segundo párrafo" in texto
    # Ni el script, ni el nav, ni el footer, ni el crédito corto de la foto.
    assert "no soy contenido" not in texto
    assert "Suscríbete" not in texto
    assert "derechos reservados" not in texto
    assert "Foto: agencia" not in texto


def test_texto_de_articulo_respeta_el_tope_sin_partir_lineas() -> None:
    texto = _texto_de_articulo(_HTML, max_chars=len(_PARRAFO) + 10)
    assert texto == _PARRAFO


async def test_leer_articulo_directo_y_via_pagina_intermedia_de_google() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "medio.example":
            return httpx.Response(200, text=_HTML)
        if request.url.host == "news.google.com":
            # La página intermedia: solo trae el enlace de salida al medio real.
            return httpx.Response(
                200, text='<a href="https://medio.example/nota">Leer el artículo</a>'
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        # Camino directo.
        assert _PARRAFO in await leer_articulo(http, "https://medio.example/nota")
        # Camino Google News con token cifrado: se lee la página intermedia y se sigue
        # el enlace de salida.
        cifrado = base64.urlsafe_b64encode(b"\x01sin url\x02").decode().rstrip("=")
        via_google = await leer_articulo(
            http, f"https://news.google.com/rss/articles/{cifrado}?oc=5"
        )
        assert _PARRAFO in via_google
        # Un 404 degrada a cadena vacía, jamás a excepción.
        assert await leer_articulo(http, "https://caido.example/x") == ""


def test_bloque_fuentes_lleva_el_extracto_debajo_de_su_fuente() -> None:
    fuentes = [
        {"title": "Titular A", "snippet": "Medio · hace 2 h", "url": "https://a.example/1"},
        {"title": "Titular B", "snippet": "Medio · hace 5 h", "url": "https://b.example/2"},
    ]
    bloque = _bloque_fuentes(fuentes, {"https://a.example/1": _PARRAFO})
    assert "EXTRACTO REAL DEL ARTÍCULO [F1]" in bloque
    assert _PARRAFO in bloque
    # La fuente sin cuerpo queda como siempre: una sola línea.
    assert "EXTRACTO REAL DEL ARTÍCULO [F2]" not in bloque
    # Sin cuerpos, el formato es EXACTAMENTE el de antes.
    assert "EXTRACTO" not in _bloque_fuentes(fuentes)


def test_con_cuerpo_devuelve_copias_y_no_toca_los_originales() -> None:
    fuentes = [{"title": "T", "snippet": "Medio · hace 1 h", "url": "https://a.example/1"}]
    enriquecidas = _con_cuerpo(fuentes, {"https://a.example/1": _PARRAFO})
    assert _PARRAFO in enriquecidas[0]["snippet"]
    # El original -- el que viaja a `sources`/manifiesto -- queda intacto: el cuerpo
    # jamás se publica.
    assert fuentes[0]["snippet"] == "Medio · hace 1 h"


async def test_leer_articulo_descifra_el_token_moderno_via_batchexecute() -> None:
    """El caso REAL de producción: token cifrado ("AU_yqL...") cuyo base64 no trae la
    URL y cuya página intermedia es una app JS sin enlace de salida en el HTML. La
    resolución correcta pasa por el endpoint interno `batchexecute` de Google, con la
    firma y el timestamp que la propia página publica."""
    import json as _json

    token = "AU_yqLtokencifradodeprueba"
    peticiones: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        peticiones.append(str(request.url))
        if request.url.host == "medio.example":
            return httpx.Response(200, text=_HTML)
        if request.url.path == f"/articles/{token}":
            # La página del artículo: solo trae la firma y el timestamp.
            return httpx.Response(
                200, text='<c-wiz data-n-a-sg="FIRMA123" data-n-a-ts="99123"></c-wiz>'
            )
        if request.url.path.endswith("/batchexecute"):
            cuerpo = _json.dumps(["x", _json.dumps([[1, "https://medio.example/nota"]])])
            return httpx.Response(200, text=")]}'\n\n" + cuerpo)
        return httpx.Response(404, text="app de js sin enlaces")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        cuerpo = await leer_articulo(http, f"https://news.google.com/rss/articles/{token}?oc=5")
    assert _PARRAFO in cuerpo
    assert any("batchexecute" in p for p in peticiones)
