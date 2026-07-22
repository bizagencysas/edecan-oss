from edecan_api.secret_intents import detect_inline_credential_intent, redact_values


def test_detects_elevenlabs_key_and_redacts_it() -> None:
    secret = "sk_proj_example_1234567890abcdef"
    intent = detect_inline_credential_intent(
        f"Mira, mi API key de ElevenLabs es {secret}. Configúrala por favor."
    )

    assert intent is not None
    assert intent.provider == "elevenlabs"
    assert intent.tool_args["campos"]["api_key"] == secret
    assert intent.tool_args["campos"]["model_id"] == "eleven_v3"
    assert secret not in intent.redacted_text
    assert "[credencial protegida]" in intent.redacted_text


def test_does_not_capture_a_key_without_provider_or_instruction() -> None:
    assert detect_inline_credential_intent("Ejemplo de documentación: sk_fake_1234567890") is None
    assert detect_inline_credential_intent("Configura esta API key: sk_fake_1234567890") is None


def test_refuses_ambiguous_multi_provider_message() -> None:
    text = (
        "Configura mi API key de ElevenLabs sk_example_1234567890 y mi token "
        "de Tavily tvly-example-1234567890"
    )
    assert detect_inline_credential_intent(text) is None


def test_redact_values_removes_reflected_secret() -> None:
    secret = "sk_proj_secret_value"
    assert secret not in redact_values(f"El proveedor rechazó {secret}", (secret,))


def test_detects_and_redacts_both_alpaca_paper_credentials() -> None:
    key_id = "PKTEST1234567890"
    secret = "alpaca-secret-value-1234567890"
    intent = detect_inline_credential_intent(
        f"Configura Alpaca Paper. API Key ID: {key_id}. Secret Key: {secret}"
    )

    assert intent is not None
    assert intent.provider == "alpaca_paper"
    assert intent.tool_args["campos"] == {"api_key_id": key_id, "secret_key": secret}
    assert key_id not in intent.redacted_text
    assert secret not in intent.redacted_text
