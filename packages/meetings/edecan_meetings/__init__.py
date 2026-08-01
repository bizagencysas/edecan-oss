"""edecan_meetings — reuniones: transcripción con el STT del tenant + minutas
por LLM (`ARCHITECTURE.md` §15, WP-V6-05). Ver README.md de este paquete.
"""

from __future__ import annotations

from .minutas import AccionMinuta, Minutas, construir_prompt_minutas, parsear_minutas
from .tools import DISCLAIMER_CONSENTIMIENTO, ResumirReunionTool, get_all_tools

__all__ = [
    "AccionMinuta",
    "DISCLAIMER_CONSENTIMIENTO",
    "Minutas",
    "ResumirReunionTool",
    "construir_prompt_minutas",
    "get_all_tools",
    "parsear_minutas",
]
