"""`redact` — enmascara secretos evidentes (ARCHITECTURE.md §0.1, SECURITY.md)."""

from __future__ import annotations

from edecan_core.safety import redact


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
