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


def test_detects_openai_as_llm_without_image_intent() -> None:
    secret = "sk-openai-example-1234567890"
    intent = detect_inline_credential_intent(
        f"Configura mi API key de OpenAI: {secret}"
    )

    assert intent is not None
    assert intent.tool_args == {
        "tipo": "llm",
        "campos": {"provider": "openai", "api_key": secret},
    }
    assert secret not in intent.redacted_text


def test_detects_openai_image_key_without_confusing_it_with_llm() -> None:
    secret = "sk-openai-images-1234567890"
    intent = detect_inline_credential_intent(
        f"Mira, mi API key de OpenAI para crear imágenes es {secret}. Configúrala."
    )

    assert intent is not None
    assert intent.tool_args == {
        "tipo": "images",
        "campos": {"provider": "openai", "api_key": secret},
    }


def test_detects_multiple_llm_provider_families() -> None:
    examples = [
        ("Anthropic", "sk-ant-api03-example-1234567890", "anthropic"),
        ("Gemini", "AIzaExampleKey1234567890", "gemini"),
        ("DeepSeek", "sk-deepseek-example-1234567890", "deepseek"),
        ("Groq", "gsk_example_1234567890", "groq"),
        ("OpenRouter", "sk-or-example-1234567890", "openrouter"),
        ("Grok", "xai-example-1234567890", "xai"),
        ("Mistral", "mistral-example-key-1234567890", "mistral"),
        ("Kimi", "kimi-example-key-1234567890", "kimi"),
    ]

    for label, secret, provider in examples:
        intent = detect_inline_credential_intent(
            f"Configura mi API key de {label}: {secret}"
        )
        assert intent is not None, label
        assert intent.tool_args["tipo"] == "llm"
        assert intent.tool_args["campos"]["provider"] == provider
        assert secret not in intent.redacted_text
