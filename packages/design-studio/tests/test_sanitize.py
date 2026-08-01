from __future__ import annotations

import pytest
from edecan_design_studio.sanitize import HtmlValidationError, extract_html, sanitize_html


def test_extract_html_uses_largest_fenced_document() -> None:
    raw = """Explicación
```html
<div>corto</div>
```
```html
<!doctype html><html><head><style>body{margin:0}</style></head>
<body><main><h1>Documento principal</h1></main></body></html>
```
"""
    assert "Documento principal" in extract_html(raw)


def test_sanitize_removes_active_content_and_external_network() -> None:
    raw = """<!doctype html><html><head>
<script>fetch('https://outside.invalid')</script>
<style>@import 'https://outside.invalid/x.css'; .hero{background:url(https://outside.invalid/a)}</style>
</head><body onload="steal()"><main>
<a href="https://outside.invalid">salir</a>
<img src="https://outside.invalid/a.png" onerror="steal()" alt="foto">
<h1 style="color:#123; background:url(javascript:bad)">Seguro</h1>
</main></body></html>"""
    clean = sanitize_html(raw)
    lowered = clean.lower()
    assert "content-security-policy" in lowered
    assert "<script" not in lowered
    assert "outside.invalid" not in lowered
    assert "onload=" not in lowered
    assert "onerror=" not in lowered
    assert "javascript:" not in lowered
    assert "@import" not in lowered
    assert "url(" not in lowered
    assert "color:#123" in lowered
    assert "Seguro" in clean


def test_sanitize_rejects_non_visual_or_oversized_input() -> None:
    with pytest.raises(HtmlValidationError):
        sanitize_html("solo texto")
    with pytest.raises(HtmlValidationError, match="límite"):
        sanitize_html("<html><body><main>" + ("x" * 500_100) + "</main></body></html>")
