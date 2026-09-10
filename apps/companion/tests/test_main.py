"""Tests de `edecan_companion.main` que nunca abren un socket real: utilidades
puras y el loop de reconexión de `run_forever` con `_run_session` sustituida."""

from __future__ import annotations

import asyncio
import types
from collections.abc import Callable
from urllib.parse import urlencode

import pytest
from edecan_companion.main import _build_ws_url, _parse_args
from websockets.exceptions import InvalidStatus


def test_build_ws_url_converts_http_to_ws():
    url = _build_ws_url("http://localhost:8000", "ABCD-1234")
    assert url == "ws://localhost:8000/v1/companion/ws?code=ABCD-1234&name=Mac"


def test_build_ws_url_converts_https_to_wss():
    url = _build_ws_url("https://api.edecan.example", "XYZ")
    assert url == "wss://api.edecan.example/v1/companion/ws?code=XYZ&name=Mac"


def test_build_ws_url_passes_through_ws_scheme():
    url = _build_ws_url("ws://localhost:8000", "XYZ")
    assert url.startswith("ws://localhost:8000/v1/companion/ws")


def test_build_ws_url_rejects_unsupported_scheme():
    with pytest.raises(ValueError):
        _build_ws_url("ftp://example.com", "XYZ")


def test_build_ws_url_rejects_missing_host():
    with pytest.raises(ValueError):
        _build_ws_url("not-a-url", "XYZ")


def test_build_ws_url_encodes_the_pairing_code():
    url = _build_ws_url("http://localhost:8000", "a b/c")
    expected_query = urlencode({"code": "a b/c", "name": "Mac"})
    assert url.endswith(f"?{expected_query}")


def test_parse_args_requires_server_and_code():
    args = _parse_args(["--server", "http://localhost:8000", "--code", "ABCD"])
    assert args.server == "http://localhost:8000"
    assert args.code == "ABCD"
    assert args.log_level == "INFO"


def test_parse_args_missing_required_flag_exits():
    with pytest.raises(SystemExit):
        _parse_args(["--server", "http://localhost:8000"])


# -- run_forever: persistencia de la conexión con el MISMO pair-code ---------
#
# La sesión real nunca se abre acá: `_run_session` se sustituye con un doble
# que registra el URI con el que se llamó. Así se verifica el CONTRATO del
# loop de reconexión: mismo código tras cortes de red, EXIT solo en 403.


async def _wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("la condición no se cumplió a tiempo")
        await asyncio.sleep(0.005)


def _patch_backoff(monkeypatch: pytest.MonkeyPatch, main_module) -> None:
    """Backoff a cero: el test verifica el contrato del loop, no la espera."""
    monkeypatch.setattr(main_module, "INITIAL_BACKOFF_SECONDS", 0.0)
    monkeypatch.setattr(main_module, "MAX_BACKOFF_SECONDS", 0.0)


async def test_run_forever_retries_with_the_same_pair_code_after_network_drop(
    monkeypatch: pytest.MonkeyPatch, companion_config
) -> None:
    """Una caída de red (OSError) NO debe gastar el par: el loop reintenta con
    el MISMO código hasta reconectar (antes, el par era de un solo uso y la
    reconexión moría en un 403)."""
    import edecan_companion.main as main_module

    attempts: list[str] = []

    async def fake_run_session(uri, _config, _approver) -> None:
        attempts.append(uri)
        if len(attempts) < 3:
            raise OSError("red caída")
        # A partir del 3er intento la sesión queda estable; el loop sigue
        # vivo, así que el test la cancela al verificar.

    monkeypatch.setattr(main_module, "_run_session", fake_run_session)
    _patch_backoff(monkeypatch, main_module)

    task = asyncio.create_task(
        main_module.run_forever("http://localhost:8000", "CODIGO-24", companion_config)
    )
    try:
        await _wait_until(lambda: len(attempts) >= 3)
        # TODOS los intentos usan el MISMO código (mismo URI, nunca se pidió otro).
        assert all(uri == attempts[0] for uri in attempts)
        assert "code=CODIGO-24" in attempts[0]
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_run_forever_exits_on_403_pair_code_rejected(
    monkeypatch: pytest.MonkeyPatch, companion_config
) -> None:
    """Un 403 del handshake (par expirado/inválido) hace EXIT al primer
    intento: reintentar sería un bucle de 403 eterno; quien lanza el proceso
    (el loop vivo) pide un código nuevo."""
    import edecan_companion.main as main_module

    attempts: list[str] = []

    async def fake_run_session(uri, _config, _approver) -> None:
        attempts.append(uri)
        raise InvalidStatus(types.SimpleNamespace(status_code=403))

    monkeypatch.setattr(main_module, "_run_session", fake_run_session)
    _patch_backoff(monkeypatch, main_module)

    await asyncio.wait_for(
        main_module.run_forever("http://localhost:8000", "CODIGO-VENCIDO", companion_config),
        timeout=5,
    )
    assert len(attempts) == 1


async def test_run_forever_retries_on_non_403_handshake_rejection(
    monkeypatch: pytest.MonkeyPatch, companion_config
) -> None:
    """Un rechazo de handshake que NO es 403 (p. ej. 502 del proxy) es
    transitorio: se reintenta con el mismo código, no se sale."""
    import edecan_companion.main as main_module

    attempts: list[str] = []

    async def fake_run_session(uri, _config, _approver) -> None:
        attempts.append(uri)
        raise InvalidStatus(types.SimpleNamespace(status_code=502))

    monkeypatch.setattr(main_module, "_run_session", fake_run_session)
    _patch_backoff(monkeypatch, main_module)

    task = asyncio.create_task(
        main_module.run_forever("http://localhost:8000", "CODIGO-24", companion_config)
    )
    try:
        await _wait_until(lambda: len(attempts) >= 3)
        assert all(uri == attempts[0] for uri in attempts)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
