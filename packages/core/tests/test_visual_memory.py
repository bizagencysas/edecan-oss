"""`VisualMemory` y `MultimodalSessionState` — serialización, merge y sesión (PHASE2 §49-§50)."""

from __future__ import annotations

from edecan_core.multimodal_session import MultimodalSessionState
from edecan_core.visual_memory import VisualContext, VisualMemory


def _ctx(
    *, entities: list[str] | None = None, text: str = "", timestamp: float = 1000.0
) -> VisualContext:
    return VisualContext(
        entities=entities or [],
        scene="anime",
        environment="exterior",
        text=text,
        timestamp=timestamp,
    )


def test_visual_context_to_dict_roundtrip_conserva_timestamp():
    ctx = _ctx(entities=["Tanjiro"], text="hola", timestamp=1234.5)
    restaurado = VisualContext.from_dict(ctx.to_dict())
    assert restaurado.entities == ["Tanjiro"]
    assert restaurado.text == "hola"
    assert restaurado.timestamp == 1234.5


def test_visual_context_from_dict_con_datos_parciales_usa_defaults():
    restaurado = VisualContext.from_dict({})
    assert restaurado.entities == []
    assert restaurado.environment == ""
    assert restaurado.confidence == 0.5
    assert restaurado.summarized is False


def test_visual_memory_to_dict_from_dict_roundtrip():
    memoria = VisualMemory(context_key="conv-1")
    memoria.add(_ctx(entities=["Tanjiro"], text="hola"))
    restaurada = VisualMemory.from_dict(memoria.to_dict())
    assert restaurada.context_key == "conv-1"
    assert len(restaurada.all_contexts) == 1
    assert restaurada.all_contexts[0].entities == ["Tanjiro"]
    assert restaurada.all_contexts[0].text == "hola"
    assert restaurada.all_contexts[0].timestamp == 1000.0


def test_visual_memory_from_dict_con_dict_vacio():
    restaurada = VisualMemory.from_dict({})
    assert restaurada.context_key is None
    assert restaurada.all_contexts == []


def test_merge_combina_contextos_del_mismo_context_key():
    a = VisualMemory(context_key="conv-a")
    a.add(_ctx(text="uno"))
    b = VisualMemory(context_key="conv-a")
    b.add(_ctx(text="dos"))
    a.merge(b)
    assert len(a.all_contexts) == 2


def test_merge_no_mezcla_context_key_distintos():
    a = VisualMemory(context_key="conv-a")
    a.add(_ctx(text="a"))
    b = VisualMemory(context_key="conv-b")
    b.add(_ctx(text="b"))
    a.merge(b)
    assert len(a.all_contexts) == 1
    assert a.all_contexts[0].text == "a"


def test_merge_permite_cuando_ambos_sin_context_key():
    a = VisualMemory()
    a.add(_ctx(text="a"))
    b = VisualMemory()
    b.add(_ctx(text="b"))
    a.merge(b)
    assert len(a.all_contexts) == 2


def test_merge_copia_en_vez_de_aliasar():
    a = VisualMemory(context_key="conv-a")
    b = VisualMemory(context_key="conv-a")
    b.add(_ctx(text="original"))
    a.merge(b)
    b.add(_ctx(text="nuevo"))
    assert len(a.all_contexts) == 1
    assert a.all_contexts[0].text == "original"


def test_multimodal_session_state_roundtrip():
    estado = MultimodalSessionState(context_key="conv-9")
    estado.visual_memory.add(_ctx(entities=["Goku"]))
    estado.last_camera_frame_summary = "persona sonriendo"
    estado.last_screen_frame_summary = "pantalla de inicio"
    estado.active_media_refs = ["file-1"]
    estado.detected_entities = ["Goku", "Vegeta"]

    restaurado = MultimodalSessionState.from_dict(estado.to_dict())

    assert restaurado.visual_memory.context_key == "conv-9"
    assert restaurado.visual_memory.all_contexts[0].entities == ["Goku"]
    assert restaurado.last_camera_frame_summary == "persona sonriendo"
    assert restaurado.last_screen_frame_summary == "pantalla de inicio"
    assert restaurado.active_media_refs == ["file-1"]
    assert restaurado.detected_entities == ["Goku", "Vegeta"]


def test_multimodal_session_state_from_dict_vacio():
    restaurado = MultimodalSessionState.from_dict({})
    assert restaurado.visual_memory.context_key is None
    assert restaurado.visual_memory.all_contexts == []
    assert restaurado.active_media_refs == []
    assert restaurado.detected_entities == []
    assert restaurado.last_camera_frame_summary is None