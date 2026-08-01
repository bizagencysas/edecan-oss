"""`redact` — enmascara secretos evidentes (ARCHITECTURE.md §0.1, SECURITY.md)."""

from __future__ import annotations

from edecan_core.safety import public_error_message, redact


def test_redact_enmascara_clave_sk():
    texto = "Usa esta clave: sk-ant-api03-abcdefghijklmnop para llamar al modelo."
    resultado = redact(texto)
    assert "sk-ant-api03-abcdefghijklmnop" not in resultado
    assert "[REDACTED]" in resultado


def test_redact_enmascara_bearer_token():
    texto = "curl -H 'Authorization: Bearer abcDEF123456789' https://api.example.com"
    resultado = redact(texto)
    assert "abcDEF123456789" not in resultado
    assert "[REDACTED]" in resultado


def test_redact_es_insensible_a_mayusculas_en_bearer():
    texto = "header: bearer abcDEF123456789xyz"
    resultado = redact(texto)
    assert "abcDEF123456789xyz" not in resultado


def test_redact_no_toca_texto_normal():
    texto = "Hola, ¿puedes recordarme comprar café mañana a las 9am?"
    assert redact(texto) == texto


def test_redact_enmascara_varias_ocurrencias():
    texto = "clave1=sk-abcdefgh12345 clave2=sk-zyxwvuts98765"
    resultado = redact(texto)
    assert resultado.count("[REDACTED]") == 2


def test_redact_enmascara_aws_access_key():
    texto = "AWS_ACCESS_KEY_ID=AKIAABCDEFGHIJKLMNOP"
    resultado = redact(texto)
    assert "AKIAABCDEFGHIJKLMNOP" not in resultado


def test_public_error_message_no_publica_sql_ni_parametros():
    exc = RuntimeError(
        "(sqlalchemy.dialects.postgresql.asyncpg.Error) could not access file "
        '"$libdir/vector" [SQL: SELECT * FROM memory_items] [parameters: secreto]'
    )

    message = public_error_message(exc)

    assert "almacenamiento local" in message
    assert "SELECT" not in message
    assert "parameters" not in message
    assert "$libdir" not in message


def test_public_error_message_conserva_error_util_de_proveedor():
    exc = RuntimeError("Codex CLI rechazó el modelo solicitado con HTTP 400")
    assert public_error_message(exc) == str(exc)
