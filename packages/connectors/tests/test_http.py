"""Tests de `edecan_connectors.http.build_http_client`."""

from __future__ import annotations

import httpx
from edecan_connectors.http import build_http_client


async def test_build_http_client_defaults() -> None:
    client = build_http_client()
    try:
        assert client.follow_redirects is False
        assert client.timeout == httpx.Timeout(30.0)
    finally:
        await client.aclose()


async def test_build_http_client_allows_overrides() -> None:
    client = build_http_client(follow_redirects=True)
    try:
        assert client.follow_redirects is True
    finally:
        await client.aclose()
