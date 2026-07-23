from __future__ import annotations

import httpx
import pytest
from edecan_voice.telephony import (
    TelephonyError,
    TwilioCredentials,
    TwilioVoiceClient,
    conversation_twiml,
    normalize_e164,
    normalize_twilio_status,
    twilio_signature,
    verify_twilio_signature,
)


def _credentials() -> TwilioCredentials:
    return TwilioCredentials(
        account_sid="AC" + "1" * 32,
        auth_token="secret-token",
        phone_number="+573001111111",
    )


def test_e164_rejects_ambiguous_local_number() -> None:
    with pytest.raises(ValueError, match="E.164"):
        normalize_e164("3001234567")


def test_twilio_canceled_maps_to_internal_cancelled_status() -> None:
    assert normalize_twilio_status("canceled") == "cancelled"


def test_signature_round_trip_and_tampering() -> None:
    params = {"CallSid": "CA123", "SpeechResult": "sí, confirmo"}
    signature = twilio_signature("https://example.test/hook", params, "token")
    assert verify_twilio_signature(
        url="https://example.test/hook",
        params=params,
        auth_token="token",
        supplied_signature=signature,
    )
    assert not verify_twilio_signature(
        url="https://example.test/hook",
        params={**params, "SpeechResult": "alterado"},
        auth_token="token",
        supplied_signature=signature,
    )


async def test_create_call_uses_injected_transport_without_real_network() -> None:
    seen: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(201, json={"sid": "CA" + "2" * 32, "status": "queued"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        call = await TwilioVoiceClient(_credentials(), http_client=http_client).create_call(
            to_e164="+573002222222",
            voice_url="https://assistant.test/v1/phone/twilio/call/voice",
            status_callback_url="https://assistant.test/v1/phone/twilio/call/status",
        )

    assert call.sid == "CA" + "2" * 32
    assert call.status == "queued"
    assert seen["url"].endswith("/Accounts/AC" + "1" * 32 + "/Calls.json")
    assert "To=%2B573002222222" in seen["body"]
    assert "From=%2B573001111111" in seen["body"]


async def test_create_call_redacts_provider_body_from_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "secret-token"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = TwilioVoiceClient(_credentials(), http_client=http_client)
        with pytest.raises(TelephonyError) as exc:
            await client.create_call(
                to_e164="+573002222222",
                voice_url="https://assistant.test/voice",
                status_callback_url="https://assistant.test/status",
            )
    assert "secret-token" not in str(exc.value)


def test_twiml_escapes_goal_and_keeps_voice_inside_one_turn() -> None:
    xml = conversation_twiml(
        message="Confirma <cita> & hora",
        gather_url="https://assistant.test/gather?x=1&y=2",
    )
    assert "Confirma &lt;cita&gt; &amp; hora" in xml
    assert "<Gather" in xml
    assert "https://assistant.test/gather?x=1&amp;y=2" in xml
