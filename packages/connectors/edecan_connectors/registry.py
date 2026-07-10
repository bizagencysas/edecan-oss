"""Registro de conectores disponibles (ARCHITECTURE.md §10.8)."""

from __future__ import annotations

from .base import Connector
from .google.connector import GoogleConnector
from .microsoft.connector import MicrosoftConnector

try:
    from .social import SOCIAL_CONNECTORS
except ImportError:
    SOCIAL_CONNECTORS: dict[str, Connector] = {}

try:
    from .messaging import MESSAGING_CONNECTORS
except ImportError:
    MESSAGING_CONNECTORS: dict[str, Connector] = {}

CONNECTORS: dict[str, Connector] = {
    "google": GoogleConnector(),
    "microsoft": MicrosoftConnector(),
    **SOCIAL_CONNECTORS,
    **MESSAGING_CONNECTORS,
}
