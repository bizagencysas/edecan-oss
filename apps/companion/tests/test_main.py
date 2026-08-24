"""Tests de utilidades puras de `edecan_companion.main` (nunca abren un socket real)."""

from __future__ import annotations

from urllib.parse import urlencode

import pytest
from edecan_companion.main import _build_ws_url, _parse_args


def test_build_ws_url_converts_http_to_ws():
    url = _build_ws_url("http://localhost:8000", "ABCD-1234")
    assert url == "ws://localhost:8000/v1/companion/ws?code=ABCD-1234"


def test_build_ws_url_converts_https_to_wss():
    url = _build_ws_url("https://api.edecan.example", "XYZ")
    assert url == "wss://api.edecan.example/v1/companion/ws?code=XYZ"


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
    expected_query = urlencode({"code": "a b/c"})
    assert url.endswith(f"?{expected_query}")


def test_parse_args_requires_server_and_code():
    args = _parse_args(["--server", "http://localhost:8000", "--code", "ABCD"])
    assert args.server == "http://localhost:8000"
    assert args.code == "ABCD"
    assert args.log_level == "INFO"


def test_parse_args_missing_required_flag_exits():
    with pytest.raises(SystemExit):
        _parse_args(["--server", "http://localhost:8000"])
