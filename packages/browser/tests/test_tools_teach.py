"""Tests del cableado "enseñar-haciendo" (product design): cuando
`navegar_web_interactivo` corre con un `teach_session_id`, registra su
resultado como paso estructurado de la sesión de enseñanza activa.

Sin Playwright real ni `edecan_companion`: se monkeypatchea `_intentar_accion`
(la ejecución del navegador) y `_recorder_teach` (el recorder diferido) con
dobles locales — se prueba el CONTRATO del cableado (qué se registra en éxito,
qué en fracaso, y que un fallo de la captura jamás tumba la acción), no el
navegador ni la red.
"""

from __future__ import annotations

from typing import Any

import httpx
from edecan_browser import NavegarWebInteractivoTool
from edecan_browser import tools as tools_mod
from edecan_browser.tools import ToolResult


async def test_interactivo_con_teach_registra_accion_en_exito(make_ctx, monkeypatch) -> None:
    llamadas: list[dict[str, Any]] = []

    async def _recorder(session_id: str, *, accion, url, selector, decision, output):
        llamadas.append(
            {
                "session_id": session_id,
                "accion": accion,
                "url": url,
                "selector": selector,
                "decision": decision,
                "output": output,
            }
        )
        return {"id": session_id}

    async def _intentar(ctx, url, accion, *, selector, texto, opcion):
        return ToolResult(content="Hice click en «#x»."), True, ""

    monkeypatch.setattr(tools_mod, "_recorder_teach", lambda: _recorder)
    monkeypatch.setattr(tools_mod, "_intentar_accion", _intentar)

    resultado = await NavegarWebInteractivoTool().run(
        make_ctx(),
        {
            "url": "https://tienda.ejemplo.com/producto/1",
            "accion": "click",
            "selector": "#x",
            "teach_session_id": "sesion-1",
        },
    )

    assert resultado.content == "Hice click en «#x»."
    assert llamadas == [
        {
            "session_id": "sesion-1",
            "accion": "click",
            "url": "https://tienda.ejemplo.com/producto/1",
            "selector": "#x",
            "decision": "",
            "output": "Hice click en «#x».",
        }
    ]


async def test_interactivo_con_teach_registra_decision_en_fracaso(make_ctx, monkeypatch) -> None:
    """Si la acción falló, el paso se registra como DECISIÓN (`accion=""`,
    `decision=causa`), nunca como una acción de éxito que no ocurrió."""
    llamadas: list[dict[str, Any]] = []
    causa = "No pude navegar «https://tienda.ejemplo.com/producto/1»: boom."

    async def _recorder(session_id: str, *, accion, url, selector, decision, output):
        llamadas.append(
            {
                "session_id": session_id,
                "accion": accion,
                "url": url,
                "selector": selector,
                "decision": decision,
                "output": output,
            }
        )
        return {"id": session_id}

    async def _intentar(ctx, url, accion, *, selector, texto, opcion):
        return ToolResult(content=causa), False, causa

    monkeypatch.setattr(tools_mod, "_recorder_teach", lambda: _recorder)
    monkeypatch.setattr(tools_mod, "_intentar_accion", _intentar)

    resultado = await NavegarWebInteractivoTool().run(
        make_ctx(),
        {
            "url": "https://tienda.ejemplo.com/producto/1",
            "accion": "click",
            "selector": "#x",
            "teach_session_id": "sesion-1",
        },
    )

    assert resultado.content == causa
    assert llamadas == [
        {
            "session_id": "sesion-1",
            "accion": "",
            "url": "https://tienda.ejemplo.com/producto/1",
            "selector": "",
            "decision": causa,
            "output": "",
        }
    ]


async def test_interactivo_teach_capture_falla_no_rompe_la_accion(make_ctx, monkeypatch) -> None:
    """La captura del paso es best-effort: si el POST falla, la acción del
    navegador YA terminó y su resultado se devuelve igual."""

    async def _recorder(session_id: str, *, accion, url, selector, decision, output):
        raise httpx.ConnectError("API caída")

    async def _intentar(ctx, url, accion, *, selector, texto, opcion):
        return ToolResult(content="Captura de pantalla tomada."), True, ""

    monkeypatch.setattr(tools_mod, "_recorder_teach", lambda: _recorder)
    monkeypatch.setattr(tools_mod, "_intentar_accion", _intentar)

    resultado = await NavegarWebInteractivoTool().run(
        make_ctx(),
        {
            "url": "https://tienda.ejemplo.com/producto/1",
            "accion": "screenshot",
            "teach_session_id": "sesion-1",
        },
    )

    assert resultado.content == "Captura de pantalla tomada."


async def test_interactivo_sin_teach_session_no_intenta_capturar(make_ctx, monkeypatch) -> None:
    """Sin `teach_session_id` no hay sesión de enseñanza: no se llama al recorder."""
    invocado = False

    async def _recorder(session_id: str, *, accion, url, selector, decision, output):
        nonlocal invocado
        invocado = True
        return {"id": session_id}

    async def _intentar(ctx, url, accion, *, selector, texto, opcion):
        return ToolResult(content="Hice click en «#x»."), True, ""

    monkeypatch.setattr(tools_mod, "_recorder_teach", lambda: _recorder)
    monkeypatch.setattr(tools_mod, "_intentar_accion", _intentar)

    await NavegarWebInteractivoTool().run(
        make_ctx(),
        {"url": "https://tienda.ejemplo.com/producto/1", "accion": "click", "selector": "#x"},
    )

    assert invocado is False


def test_recorder_teach_devuelve_none_si_no_hay_companion(monkeypatch) -> None:
    """`_recorder_teach` es diferido y best-effort: si `edecan_companion` no es
    importable (el navegador corriendo fuera del companion), devuelve `None` en
    vez de tumbar la acción."""
    import builtins

    real_import = builtins.__import__

    def _sin_companion(name, *args, **kwargs):
        if name == "edecan_companion" or name.startswith("edecan_companion."):
            raise ImportError("edecan_companion no está disponible")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _sin_companion)
    assert tools_mod._recorder_teach() is None
