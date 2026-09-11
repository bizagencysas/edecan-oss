import pytest
from edecan_voice.realtime import RealtimeVoiceSession


def test_barge_in_invalida_el_turno_en_vuelo():
    session = RealtimeVoiceSession()
    turn_id = session.begin_listening()
    session.append_audio(128)
    assert session.commit_audio(turn_id)
    assert session.begin_speaking(turn_id)

    assert session.interrupt("user_started_talking")
    assert session.state == "interrupted"
    assert session.buffered_audio_bytes == 0
    assert not session.is_current(turn_id)
    assert not session.finish(turn_id)


def test_reset_recupera_la_sesion_interrumpida_para_un_turno_nuevo():
    """Regresión del estado muerto: `interrupted` era el único estado sin
    salida — ni `begin_listening` ni `commit_audio` lo aceptaban, así que tras
    un barge-in el transporte rechazaba TODO el audio siguiente."""
    session = RealtimeVoiceSession()
    turn_id = session.begin_listening()
    session.commit_audio(turn_id)
    session.begin_speaking(turn_id)
    session.interrupt("user")
    assert session.state == "interrupted"

    # Antes del fix este begin_listening levantaba RuntimeError desde
    # `interrupted` (estado muerto). El `reset` lo devuelve a `idle`.
    assert session.reset()
    nuevo = session.begin_listening()
    assert nuevo > turn_id
    assert session.state == "listening"


def test_reset_solo_aplica_a_interrumpida():
    session = RealtimeVoiceSession()
    assert not session.reset()
    turn_id = session.begin_listening()
    session.append_audio(16)
    session.commit_audio(turn_id)
    assert session.state == "processing"
    assert not session.reset()
    session.interrupt("user")
    assert session.reset()
    assert session.state == "idle"


def test_turno_completo_vuelve_a_idle_y_el_siguiente_recibe_token_nuevo():
    session = RealtimeVoiceSession()
    first = session.begin_listening()
    session.append_audio(32)
    assert session.commit_audio(first)
    assert session.begin_speaking(first)
    assert session.finish(first)
    assert session.state == "idle"

    second = session.begin_listening()
    assert second > first


def test_input_de_stt_puede_cerrar_antes_de_la_respuesta_del_agente():
    session = RealtimeVoiceSession()
    turn_id = session.begin_listening()
    session.append_audio(64)
    assert session.commit_audio(turn_id)
    assert session.complete_input(turn_id)
    assert session.state == "idle"


def test_no_acepta_audio_fuera_de_listening_y_cierra_fatalmente():
    session = RealtimeVoiceSession()
    with pytest.raises(RuntimeError):
        session.append_audio(10)
    session.close()
    with pytest.raises(RuntimeError):
        session.begin_listening()
