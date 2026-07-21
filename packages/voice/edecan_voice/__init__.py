"""edecan_voice — STT/TTS intercambiables para la voz web de Edecán, más voces
del tenant y clonación autorizada (WP-V5-10).

Ver `ARCHITECTURE.md` §4 (voz y telefonía) y §10.9 (contrato base de este
paquete). La telefonía (Twilio) no vive aquí: está en `premium/` (§10.10).

`get_all_tools()` es el entry point que consume
`edecan_core.ToolRegistry.load_entry_points(group="edecan.tools")` (§10.7),
declarado en `pyproject.toml` como `[project.entry-points."edecan.tools"]`:
expone `listar_voces`/`sintetizar_voz` (`edecan_voice.tools`) — la clonación
NUNCA es una tool del agente, ver el docstring de `edecan_voice.cloning`.
"""

from edecan_voice.base import STTProvider, Transcript, TTSProvider
from edecan_voice.cloning import (
    MuestraVoz,
    VoiceCloningError,
    VozDisponible,
    borrar_clon,
    crear_clon,
    generar_efecto,
)
from edecan_voice.cloning import (
    listar_voces as listar_voces_elevenlabs,
)
from edecan_voice.deepgram import DeepgramSTT
from edecan_voice.elevenlabs import ElevenLabsTTS
from edecan_voice.pipeline import voice_turn
from edecan_voice.polly import PollyTTS
from edecan_voice.registry import get_stt, get_tts
from edecan_voice.stubs import StubSTT, StubTTS
from edecan_voice.tenant import (
    VOICE_TTS_CONNECTOR_KEY,
    resolver_config_tts_del_tenant,
    resolver_tts_del_tenant,
)
from edecan_voice.tools import ListarVocesTool, LlamarContactoTool, SintetizarVozTool, get_all_tools

__all__ = [
    "STTProvider",
    "TTSProvider",
    "Transcript",
    "DeepgramSTT",
    "ElevenLabsTTS",
    "ListarVocesTool",
    "LlamarContactoTool",
    "MuestraVoz",
    "PollyTTS",
    "SintetizarVozTool",
    "StubSTT",
    "StubTTS",
    "VOICE_TTS_CONNECTOR_KEY",
    "VoiceCloningError",
    "VozDisponible",
    "borrar_clon",
    "crear_clon",
    "generar_efecto",
    "get_all_tools",
    "get_stt",
    "get_tts",
    "listar_voces_elevenlabs",
    "resolver_config_tts_del_tenant",
    "resolver_tts_del_tenant",
    "voice_turn",
]
