"""Tests de `edecan_browser.extract`: `extract_page` y `render_markdown` sobre
un fixture de HTML fijo (sin red — puro parseo de string).
"""

from __future__ import annotations

from edecan_browser.extract import extract_page, render_markdown

_HTML_FIXTURE = """
<html>
<head>
  <title>Zapatillas Modelo X</title>
  <meta name="description" content="Las mejores zapatillas para correr.">
  <style>.oculto { display: none; }</style>
  <script>console.log("no deberia aparecer en el texto");</script>
</head>
<body>
  <nav>Inicio | Productos | Contacto</nav>
  <main>
    <h1>Zapatillas Modelo X</h1>
    <p>Livianas, comodas y resistentes al agua.</p>
    <a href="/producto/x">Ver detalle</a>
    <a href="https://otra-tienda.ejemplo.com/relacionado">Producto relacionado</a>
    <a href="#arriba">Volver arriba</a>
    <a href="mailto:ventas@ejemplo.com">Escribenos</a>
    <a href="javascript:void(0)">No navegable</a>
  </main>
  <aside>Productos recomendados que no importan</aside>
  <footer>Copyright 2026 Tienda Ejemplo</footer>
</body>
</html>
"""

_BASE_URL = "https://tienda.ejemplo.com/producto/x"


def test_extract_page_titulo_y_meta_description():
    pagina = extract_page(_HTML_FIXTURE, base_url=_BASE_URL)
    assert pagina.titulo == "Zapatillas Modelo X"
    assert pagina.meta_description == "Las mejores zapatillas para correr."


def test_extract_page_descarta_script_style_nav_footer_aside():
    pagina = extract_page(_HTML_FIXTURE, base_url=_BASE_URL)
    texto = pagina.texto.lower()
    assert "no deberia aparecer" not in texto
    assert "inicio | productos | contacto" not in texto
    assert "recomendados que no importan" not in texto
    assert "copyright 2026 tienda ejemplo" not in texto
    assert "livianas, comodas y resistentes al agua." in texto


def test_extract_page_enlaces_absolutos_y_filtra_no_navegables():
    pagina = extract_page(_HTML_FIXTURE, base_url=_BASE_URL)
    assert "https://tienda.ejemplo.com/producto/x" in pagina.enlaces
    assert "https://otra-tienda.ejemplo.com/relacionado" in pagina.enlaces
    assert not any(e.startswith("mailto:") for e in pagina.enlaces)
    assert not any(e.startswith("javascript:") for e in pagina.enlaces)
    assert not any("#arriba" in e for e in pagina.enlaces)


def test_extract_page_cap_de_enlaces_en_40():
    html = (
        "<html><body>"
        + "".join(f'<a href="/pagina-{i}">link {i}</a>' for i in range(100))
        + "</body></html>"
    )
    pagina = extract_page(html, base_url="https://ejemplo.com/")
    assert len(pagina.enlaces) == 40


def test_extract_page_sin_html_no_revienta():
    pagina = extract_page("", base_url="https://ejemplo.com/")
    assert pagina.titulo == ""
    assert pagina.texto == ""
    assert pagina.enlaces == []


def test_render_markdown_incluye_titulo_meta_y_enlaces():
    pagina = extract_page(_HTML_FIXTURE, base_url=_BASE_URL)
    md = render_markdown(pagina)
    assert md.startswith("# Zapatillas Modelo X")
    assert "> Las mejores zapatillas para correr." in md
    assert "## Enlaces" in md
    assert "https://otra-tienda.ejemplo.com/relacionado" in md


def test_render_markdown_sin_titulo_usa_placeholder():
    pagina = extract_page(
        "<html><body><p>solo texto</p></body></html>", base_url="https://ejemplo.com/"
    )
    md = render_markdown(pagina)
    assert md.startswith("# (sin título)")


def test_render_markdown_cap_de_caracteres():
    html_largo = (
        "<html><head><title>T</title></head><body>" + ("hola mundo " * 2000) + "</body></html>"
    )
    pagina = extract_page(html_largo, base_url="https://ejemplo.com/")
    assert len(pagina.texto) > 100

    md = render_markdown(pagina, max_chars=100)

    assert "[... contenido recortado ...]" in md
