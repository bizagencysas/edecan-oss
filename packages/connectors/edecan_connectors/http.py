"""Factory del cliente HTTP compartido por los conectores."""

from __future__ import annotations

import httpx

DEFAULT_TIMEOUT = httpx.Timeout(30.0)


def build_http_client(**overrides: object) -> httpx.AsyncClient:
    """Crea el `httpx.AsyncClient` que deben usar los conectores.

    - Timeout total de 30s (conexión, lectura, escritura y espera de pool).
    - `follow_redirects=False`: los endpoints de OAuth y de las APIs de Google
      y Microsoft no deberían redirigir; seguir un redirect "sorpresa" podría
      reenviar el header `Authorization` (o el `code`/`client_secret` del POST
      de token) a un host distinto del esperado.

    Los `overrides` permiten a quien llama (tests con `respx`, `apps/api` con
    su propia configuración) ajustar cualquier parámetro de `httpx.AsyncClient`
    sin duplicar esta factory.
    """
    options: dict[str, object] = {
        "timeout": DEFAULT_TIMEOUT,
        "follow_redirects": False,
    }
    options.update(overrides)
    return httpx.AsyncClient(**options)
