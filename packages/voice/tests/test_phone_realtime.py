from edecan_voice.phone_realtime import (
    _parece_voz,
    LiveCall,
    TranscriptTurn,
    chunk_mulaw,
    drop_live_call,
    enqueue_whisper,
    https_to_wss,
    live_call,
    parse_stream_start,
    pop_whispers,
    register_live_call,
    twilio_clear_message,
    twilio_media_message,
)


def test_https_to_wss() -> None:
    assert https_to_wss("https://edecan.test/") == "wss://edecan.test"
    assert https_to_wss("http://localhost:8765") == "ws://localhost:8765"


def test_parse_stream_start_lee_parametros_custom() -> None:
    parsed = parse_stream_start(
        {
            "event": "start",
            "start": {
                "streamSid": "MZ1",
                "callSid": "CA1",
                "customParameters": {
                    "call_id": "abc",
                    "from": "+57300",
                    "inbound": "1",
                },
            },
        }
    )
    assert parsed["stream_sid"] == "MZ1"
    assert parsed["call_sid"] == "CA1"
    assert parsed["call_id"] == "abc"
    assert parsed["from"] == "+57300"
    assert parsed["inbound"] == "1"


def test_live_call_susurros_y_snapshot() -> None:
    drop_live_call("c1")
    live = register_live_call(LiveCall(call_id="c1", numero="+57300", direccion="in"))
    assert enqueue_whisper("c1", "dile que sí")
    assert pop_whispers("c1") == ["dile que sí"]
    assert pop_whispers("c1") == []
    live.transcript.append(TranscriptTurn(quien="agente", texto="Hola"))
    snap = live.snapshot()
    assert snap["activa"] is True
    assert snap["numero"] == "+57300"
    assert snap["transcript"][0]["texto"] == "Hola"
    assert live_call("c1") is live
    drop_live_call("c1")
    assert live_call("c1") is None


def test_parece_voz_ignora_silencio_mulaw() -> None:
    silencio = bytes([0xFF] * 160)
    assert _parece_voz(silencio) is False
    ruido = bytes([0x80] * 160)
    assert _parece_voz(ruido, umbral=18) is True


def test_chunk_y_mensajes_twilio() -> None:
    frames = chunk_mulaw(b"abcdefghij", frame=4)
    assert frames == [b"abcd", b"efgh", b"ij"]
    media = twilio_media_message("MZ1", b"hi")
    assert media["event"] == "media"
    assert media["streamSid"] == "MZ1"
    assert twilio_clear_message("MZ1") == {"event": "clear", "streamSid": "MZ1"}
