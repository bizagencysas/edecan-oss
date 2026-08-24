"""Conector Microsoft: Outlook Mail + Calendar vía Microsoft Graph.

Ver `edecan_connectors.microsoft.connector.MicrosoftConnector` (OAuth) y el
módulo `graph` para las llamadas de API autenticadas con un `TokenBundle`.
"""

from __future__ import annotations

from .connector import MicrosoftConnector

__all__ = ["MicrosoftConnector"]
