"""Conectores sociales oficiales: LinkedIn, Meta, X y YouTube.

Ver `ARCHITECTURE.md` §5 y §10.8. `edecan_connectors.registry` importa este
submódulo dentro de un `try/except ImportError` (es opcional: solo se necesita
si el tenant usa conectores sociales) y mezcla `SOCIAL_CONNECTORS` dentro de
`CONNECTORS`.

Cada instalación usa sus propias apps OAuth y credenciales cifradas. Ningún
conector de este paquete contiene secretos compartidos.
"""

from __future__ import annotations

from .linkedin import LinkedInConnector
from .meta import MetaConnector
from .x import XConnector
from .youtube import YouTubeConnector

SOCIAL_CONNECTORS = {
    "linkedin": LinkedInConnector(),
    "meta": MetaConnector(),
    "x": XConnector(),
    "youtube": YouTubeConnector(),
}

__all__ = [
    "SOCIAL_CONNECTORS",
    "LinkedInConnector",
    "MetaConnector",
    "XConnector",
    "YouTubeConnector",
]
