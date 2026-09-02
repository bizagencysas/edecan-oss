"""Tests de `edecan_companion.teach_capture.registrar_paso_teach` — el puente
desde una captura de pasos hacia `POST /v1/skills/teach/{id}/step`.

Sin red real: `respx` intercepta la petición; `monkeypatch` fija
`EDECAN_API_URL`/`EDECAN_API_TOKEN` para no depender del entorno.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from edecan_companion.teach_capture import (
    _base_url,
    capturar_paso_navegacion,
    registrar_paso_teach,
)


@pytest.fixture(autouse=True)
def _sin_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EDECAN_API_URL", raising=False)
    monkeypatch.delenv("EDECAN_API_TOKEN", raising=False)


@respx.mock
async def test_registrar_paso_publica_paso_estructurado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDECAN_API_URL", "http://api.test:9000")
    respx.post("http://api.test:9000/v1/skills/teach/sesion-1/step").mock(
        return_value=httpx.Response(200, json={"id": "sesion-1", "pasos": [{"action": "click"}]})
    )

    resultado = await registrar_paso_teach(
        "sesion-1", accion="click", selector="#exportar", decision="si", output="vista"
    )

    assert resultado["id"] == "sesion-1"
    request = respx.calls.last.request
    assert request.method == "POST"
    assert request.url.path == "/v1/skills/teach/sesion-1/step"
    assert json.loads(request.read().decode()) == {
        "accion": "click",
        "selector": "#exportar",
        "decision": "si",
        "input": "",
        "output": "vista",
    }


@respx.mock
async def test_registrar_paso_manda_token_si_existe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDECAN_API_URL", "http://api.test:9000")
    monkeypatch.setenv("EDECAN_API_TOKEN", "tok-secreto")
    respx.post("http://api.test:9000/v1/skills/teach/sesion-1/step").mock(
        return_value=httpx.Response(200, json={"id": "sesion-1"})
    )

    await registrar_paso_teach("sesion-1", accion="screenshot")

    assert respx.calls.last.request.headers["authorization"] == "Bearer tok-secreto"


@respx.mock
async def test_registrar_paso_sin_token_no_manda_cabecera_auth() -> None:
    respx.post("http://127.0.0.1:8000/v1/skills/teach/sesion-1/step").mock(
        return_value=httpx.Response(200, json={"id": "sesion-1"})
    )

    await registrar_paso_teach("sesion-1", accion="scroll")

    assert "authorization" not in respx.calls.last.request.headers


def test_base_url_default_y_desde_env(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _base_url() == "http://127.0.0.1:8000"
    monkeypatch.setenv("EDECAN_API_URL", "http://api.test:9000/")
    assert _base_url() == "http://api.test:9000"


@respx.mock
async def test_registrar_paso_propaga_error_http_sin_inventar_exito() -> None:
    respx.post("http://127.0.0.1:8000/v1/skills/teach/sesion-1/step").mock(
        return_value=httpx.Response(404, json={"detail": "no encontrada"})
    )

    with pytest.raises(httpx.HTTPStatusError):
        await registrar_paso_teach("sesion-1", accion="click", selector="#x")


# --- `capturar_paso_navegacion` — grabadora real, sin fabricar éxito ---------


@respx.mock
async def test_capturar_paso_navegacion_registra_accion_realizada(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EDECAN_API_URL", "http://api.test:9000")
    respx.post("http://api.test:9000/v1/skills/teach/sesion-1/step").mock(
        return_value=httpx.Response(200, json={"id": "sesion-1", "pasos": []})
    )

    resultado = await capturar_paso_navegacion(
        "sesion-1",
        accion="click",
        url="https://tienda.ejemplo.com/producto/1",
        selector="#exportar",
        decision="si",
        output="se abrió el detalle",
    )

    assert resultado["id"] == "sesion-1"
    assert json.loads(respx.calls.last.request.read().decode()) == {
        "accion": "click",
        "selector": "#exportar",
        "decision": "si",
        "input": "https://tienda.ejemplo.com/producto/1",
        "output": "se abrió el detalle",
    }


@respx.mock
async def test_capturar_paso_navegacion_fracaso_se_registra_como_decision_no_como_exito(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si `navegar_web_interactivo` falló, el paso se registra con `action`
    vacío y la causa en `decision` — nunca como una acción que «salió bien»."""
    monkeypatch.setenv("EDECAN_API_URL", "http://api.test:9000")
    respx.post("http://api.test:9000/v1/skills/teach/sesion-1/step").mock(
        return_value=httpx.Response(200, json={"id": "sesion-1", "pasos": []})
    )

    await capturar_paso_navegacion(
        "sesion-1",
        accion="",
        url="https://tienda.ejemplo.com/producto/1",
        decision="No pude navegar: la captura devolvió 0 bytes",
        output="",
    )

    cuerpo = json.loads(respx.calls.last.request.read().decode())
    assert cuerpo["accion"] == ""  # sin éxito fabricado
    assert cuerpo["decision"] == "No pude navegar: la captura devolvió 0 bytes"
    assert cuerpo["input"] == "https://tienda.ejemplo.com/producto/1"


@respx.mock
async def test_capturar_paso_navegacion_rehusa_publicar_paso_vacio() -> None:
    # Sin acción ni decisión no hay nada real: ValueError antes de tocar la red.
    with pytest.raises(ValueError):
        await capturar_paso_navegacion("sesion-1", accion="", url="", decision="", output="")
    assert not respx.calls


@respx.mock
async def test_capturar_paso_navegacion_fracaso_propaga_error_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EDECAN_API_URL", "http://api.test:9000")
    respx.post("http://api.test:9000/v1/skills/teach/sesion-1/step").mock(
        return_value=httpx.Response(401, json={"detail": "sin token"})
    )

    with pytest.raises(httpx.HTTPStatusError):
        await capturar_paso_navegacion(
            "sesion-1", accion="", url="https://x.com", decision="falló"
        )
