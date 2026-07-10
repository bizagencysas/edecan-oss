"""Conectores sociales oficiales: Meta, X y YouTube.

Ver `ARCHITECTURE.md` §5 y §10.8. `edecan_connectors.registry` importa este
submódulo dentro de un `try/except ImportError` (es opcional: solo se necesita
si el tenant usa conectores sociales) y mezcla `SOCIAL_CONNECTORS` dentro de
`CONNECTORS`.

La red social vetada por la regla dura de `ARCHITECTURE.md` §0.2 NUNCA se
integra aquí: ningún conector, scope, URL ni texto de este submódulo debe
nombrarla. Ver el test de guardia correspondiente en `social/tests/`.
"""

from __future__ import annotations

from .meta import MetaConnector
from .x import XConnector
from .youtube import YouTubeConnector

SOCIAL_CONNECTORS = {
    "meta": MetaConnector(),
    "x": XConnector(),
    "youtube": YouTubeConnector(),
}

__all__ = ["SOCIAL_CONNECTORS", "MetaConnector", "XConnector", "YouTubeConnector"]
