"""edecan_connectors — conectores OAuth 2.0 a APIs oficiales (ARCHITECTURE.md §10.8).

Cada tenant autoriza su propia cuenta; los `TokenBundle` resultantes nunca se
almacenan en este paquete — los persiste cifrados `edecan_db.vault.TokenVault`.
"""

from __future__ import annotations

from .base import Connector, OAuthSpec
from .registry import CONNECTORS

__all__ = ["Connector", "OAuthSpec", "CONNECTORS"]
