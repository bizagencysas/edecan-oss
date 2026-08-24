"""Conector Google: Gmail + Calendar.

Ver `edecan_connectors.google.connector.GoogleConnector` (OAuth) y los módulos
`gmail` / `gcal` para las llamadas de API autenticadas con un `TokenBundle`.
"""

from __future__ import annotations

from .connector import GoogleConnector

__all__ = ["GoogleConnector"]
