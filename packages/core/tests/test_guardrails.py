"""`edecan_core.guardrails` — guardrails de seguridad y calidad (PHASE2.md §186-200).

Ver el docstring del módulo bajo prueba para el contexto de cada pieza:
redacción de secretos (§194-195), router de factualidad (§190), validador
post-generación (§189) y reconocimiento de corrección (§187).
"""

from __future__ import annotations

from edecan_core.guardrails import (
    classify_factuality,
    contains_secret,
    correction_acknowledgment,
    detect_potential_secret,
    redact_secrets,
    validate_output,
)

# ---------------------------------------------------------------------------
# `detect_potential_secret` — detección de secretos (PHASE2.md §194)
# ---------------------------------------------------------------------------


def test_deteccion_texto_vacio_no_devuelve_nada():
    assert detect_potential_secret("") == []


def test_deteccion_texto_normal_no_devuelve_nada():
    texto = "Recuérdame comprar café mañana a las 9am, por favor."
    assert detect_potential_secret(texto) == []


def test_deteccion_clave_sk():
    texto = "Usa esta clave: sk-ant-api03-abcdefghijklmnop"
    tramos = detect_potential_secret(texto)
    assert len(tramos) == 1
    assert tramos[0]["type"] == "sk_key"
    assert texto[tramos[0]["start"] : tramos[0]["end"]] == "sk-ant-api03-abcdefghijklmnop"


def test_deteccion_bearer_token():
    texto = "Authorization: Bearer abcDEF123456789xyz"
    tramos = detect_potential_secret(texto)
    assert len(tramos) == 1
    assert tramos[0]["type"] == "bearer_token"


def test_deteccion_aws_access_key():
    texto = "AWS_ACCESS_KEY_ID=AKIAABCDEFGHIJKLMNOP"
    tramos = detect_potential_secret(texto)
    assert len(tramos) == 1
    assert tramos[0]["type"] == "aws_access_key"


def test_deteccion_stripe_key():
    texto = "STRIPE_WEBHOOK_SECRET=whsec_abcdef123456"
    tramos = detect_potential_secret(texto)
    assert len(tramos) == 1
    assert tramos[0]["type"] == "stripe_key"


def test_deteccion_jwt():
    texto = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf"
    )
    tramos = detect_potential_secret(texto)
    assert len(tramos) == 1
    assert tramos[0]["type"] == "jwt"


def test_deteccion_jwt_detras_de_etiqueta_redacta_el_valor_entero():
    # Aunque la etiqueta "token=" capture el valor, el JWT queda igualmente
    # redactado por completo (sin filtrar sus tres segmentos).
    texto = "token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.abc"
    resultado = redact_secrets(texto)
    assert "eyJ" not in resultado


def test_deteccion_clave_privada():
    texto = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA..."
    tramos = detect_potential_secret(texto)
    assert any(t["type"] == "private_key" for t in tramos)


def test_deteccion_password_con_valor():
    texto = "La contraseña es password=hunter2 para entrar."
    tramos = detect_potential_secret(texto)
    assert any(t["type"] == "password" for t in tramos)


def test_deteccion_api_key_con_dos_puntos():
    texto = "api_key: 1234567890abcdef"
    tramos = detect_potential_secret(texto)
    assert any(t["type"] == "api_key" for t in tramos)


def test_deteccion_es_insensible_a_mayusculas():
    texto = "PASSWORD=Secreto123"
    assert any(t["type"] == "password" for t in detect_potential_secret(texto))


def test_deteccion_no_confunde_subcadenas_con_frontera_de_palabra():
    # "secretaria" o "tokenizer" NO deben disparar la etiqueta "secret"/"token".
    assert detect_potential_secret("La secretaria del jefe") == []
    assert detect_potential_secret("el tokenizer de huggingface") == []


def test_deteccion_varios_secretos_distintos():
    texto = "clave=sk-abcdefgh12345 y luego Bearer abcDEF123456789"
    tramos = detect_potential_secret(texto)
    tipos = {t["type"] for t in tramos}
    assert "sk_key" in tipos and "bearer_token" in tipos
    assert len(tramos) >= 2


def test_deteccion_sin_solapamientos_bearer():
    # "Bearer <token>" debe producir UN solo tramo (el largo, que cubre el
    # valor), no un tramo "bearer_token" más otro "bearer" superpuestos.
    texto = "Bearer abcDEF123456789"
    tramos = detect_potential_secret(texto)
    assert len(tramos) == 1
    assert tramos[0]["type"] == "bearer_token"


def test_deteccion_indices_semiabiertos_correctos():
    texto = "x sk-abcdefgh12345 y"
    (tramo,) = detect_potential_secret(texto)
    assert texto[tramo["start"] : tramo["end"]] == "sk-abcdefgh12345"


# ---------------------------------------------------------------------------
# `redact_secrets` y `contains_secret` (PHASE2.md §195)
# ---------------------------------------------------------------------------


def test_redact_no_toca_texto_sin_secretos():
    texto = "Hola, ¿me recuerdas comprar pan?"
    assert redact_secrets(texto) == texto


def test_redact_texto_vacio():
    assert redact_secrets("") == ""


def test_redact_enmascara_clave_sk_parcialmente():
    texto = "sk-ant-api03-abcdefghijklmnop"
    resultado = redact_secrets(texto)
    assert texto not in resultado
    assert "..." in resultado
    # Forma enmascarada conservando prefijo y sufijo (SECURITY.md §2.5).
    assert resultado == "sk-a...mnop"


def test_redact_enmascara_aws_key_parcialmente():
    resultado = redact_secrets("AKIAABCDEFGHIJKLMNOP")
    assert "AKIAABCDEFGHIJKLMNOP" not in resultado
    assert resultado == "AKIA...MNOP"


def test_redact_enmascara_etiqueta_y_valor():
    resultado = redact_secrets("La contraseña es password=hunter2")
    assert "hunter2" not in resultado
    assert "[REDACTADO]" in resultado


def test_redact_enmascara_varias_ocurrencias():
    texto = "sk-abcdefgh12345 y sk-zyxwvuts98765"
    resultado = redact_secrets(texto)
    assert "sk-abcdefgh12345" not in resultado
    assert "sk-zyxwvuts98765" not in resultado
    assert resultado.count("...") == 2


def test_redact_es_consistente_con_deteccion():
    # Todo lo que `contains_secret` avisa debe quedar redactado por
    # `redact_secrets` (mismo conjunto de tramos).
    texto = "api_key=abc123 password=hunter2 Bearer tok123456789"
    assert contains_secret(texto)
    resultado = redact_secrets(texto)
    for secreto in ("abc123", "hunter2", "tok123456789"):
        assert secreto not in resultado


def test_contains_secret_true_y_false():
    assert contains_secret("mi token de acceso es sk-abcdefgh12345")
    assert not contains_secret("¿qué hora es en Caracas?")


def test_contains_secret_etiqueta_bare():
    # Una etiqueta sola ("password" sin valor) ya es señal de potencial secreto.
    assert contains_secret("anota mi password")


# ---------------------------------------------------------------------------
# `classify_factuality` — router de factualidad (PHASE2.md §190)
# ---------------------------------------------------------------------------


def test_factualidad_segun_y_fuentes():
    assert classify_factuality("¿Qué dicen los informes según la ONU?") == "factual"


def test_factualidad_confirmado():
    assert classify_factuality("confirmado: el dólar subió") == "factual"


def test_factualidad_fecha_concreta():
    assert classify_factuality("¿Qué pasó el 12/03/2024?") == "factual"


def test_factualidad_ano():
    assert classify_factuality("la guerra de 1939") == "factual"


def test_factualidad_interrogativo_de_hecho():
    assert classify_factuality("¿cuánto cuesta un café en Madrid?") == "factual"
    assert classify_factuality("¿quién es el presidente de Francia?") == "factual"


def test_factualidad_tolera_falta_de_acento():
    assert classify_factuality("cuando se creo internet") == "factual"


def test_factualidad_creativo_basico():
    assert classify_factuality("imagina un cuento de dragones") == "creative"


def test_factualidad_creativo_con_numero_de_cantidad():
    # Un número de cantidad NO convierte un pedido creativo en factual.
    assert classify_factuality("escríbeme 3 poemas sobre el mar") == "creative"


def test_factualidad_creativo_lluvia_de_ideas():
    assert classify_factuality("haz una lluvia de ideas para nombres") == "creative"


def test_factualidad_default_sin_marcadores_es_factual():
    # Sin señales claras, el default seguro es factual (verificar antes de publicar).
    assert classify_factuality("") == "factual"
    assert classify_factuality("ayúdame con algo") == "factual"


def test_factualidad_ano_inclina_hacia_factual_aun_con_historia():
    # "historia" es ambiguo, pero el año concreto lo resuelve a factual.
    assert classify_factuality("la historia de México en 1810") == "factual"


# ---------------------------------------------------------------------------
# `validate_output` — validador post-generación (PHASE2.md §189)
# ---------------------------------------------------------------------------


def test_validacion_texto_vacio_no_es_ok():
    resultado = validate_output("", "factual")
    assert resultado["ok"] is False
    assert "salida vacía" in resultado["issues"]


def test_validacion_solo_espacios_no_es_ok():
    resultado = validate_output("   \n\t  ", "legal")
    assert resultado["ok"] is False


def test_validacion_texto_normal_no_critico_es_ok():
    resultado = validate_output("hola, aquí tienes tu respuesta", "creative")
    assert resultado == {"ok": True, "issues": []}


def test_validacion_placeholder_en_categoria_critica():
    resultado = validate_output("TODO: completar esta parte", "medical")
    assert resultado["ok"] is False
    assert "placeholder" in resultado["issues"][0]


def test_validacion_lorem_ipsum_en_categoria_critica():
    resultado = validate_output("Lorem ipsum dolor sit amet", "finance")
    assert resultado["ok"] is False


def test_validacion_fabricado_en_categoria_critica():
    resultado = validate_output("no estoy seguro de este dato", "legal")
    assert resultado["ok"] is False


def test_validacion_placeholder_ignorado_en_categoria_no_critica():
    # Fuera de categorías críticas, el placeholder no se marca (solo el vacío).
    resultado = validate_output("TODO: borrador", "creative")
    assert resultado == {"ok": True, "issues": []}


def test_validacion_categoria_insensible_a_mayusculas():
    resultado = validate_output("no estoy seguro", "MEDICAL")
    assert resultado["ok"] is False


def test_validacion_texto_real_critico_es_ok():
    resultado = validate_output(
        "Según la OMS, la dosis recomendada es de 500 mg al día.", "medical"
    )
    assert resultado == {"ok": True, "issues": []}


# ---------------------------------------------------------------------------
# `correction_acknowledgment` — reconocimiento de corrección (PHASE2.md §187)
# ---------------------------------------------------------------------------


def test_correccion_plantilla_completa():
    resultado = correction_acknowledgment("tu cumpleaños", "el 3 de mayo")
    assert resultado == "Tienes razón. Me equivoqué en tu cumpleaños; lo correcto es el 3 de mayo."


def test_correccion_original_vacio_usa_sustituto():
    resultado = correction_acknowledgment("", "el 3 de mayo")
    assert "lo que dije antes" in resultado
    assert "el 3 de mayo" in resultado


def test_correccion_correccion_vacia_usa_sustituto():
    resultado = correction_acknowledgment("tu cumpleaños", "   ")
    assert "tu cumpleaños" in resultado
    assert "lo que me indicas" in resultado


def test_correccion_ambos_vacios():
    resultado = correction_acknowledgment("", "")
    assert resultado.startswith("Tienes razón.")
    assert "lo que dije antes" in resultado
    assert "lo que me indicas" in resultado