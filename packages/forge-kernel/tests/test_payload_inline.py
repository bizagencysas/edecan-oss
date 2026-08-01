from __future__ import annotations

import pytest
from edecan_forge_kernel.contracts import (
    PayloadInlineError,
    is_structural_value,
    validate_payload_inline,
)


def test_none_es_valido() -> None:
    validate_payload_inline(None)  # no lanza


@pytest.mark.parametrize(
    "payload",
    [
        {"call_id": "abc123", "attempt": 1, "ok": True},
        {"tool_id": "fs.read_file", "score": None},
        {"paths": ["a/b/c.py", "d/e.py"]},
        {"nested": {"a": 1, "b": ["x", "y"]}},
    ],
)
def test_payload_estructural_pasa(payload: dict) -> None:
    validate_payload_inline(payload)  # no lanza
    assert is_structural_value(payload)


def test_texto_libre_con_espacios_es_rechazado() -> None:
    with pytest.raises(PayloadInlineError, match="texto libre"):
        validate_payload_inline({"mensaje": "esto es texto libre con espacios"})


def test_ruta_absoluta_de_usuario_con_espacios_es_rechazada() -> None:
    with pytest.raises(PayloadInlineError):
        validate_payload_inline({"ruta": "/Users/alguien/Mi Carpeta/archivo.txt"})


def test_fragmento_de_codigo_es_rechazado() -> None:
    with pytest.raises(PayloadInlineError):
        validate_payload_inline({"parche": "def f(x):\n    return x + 1\n"})


def test_float_es_rechazado() -> None:
    """§1.2: 'Sin floats en ninguna parte del sistema'."""
    with pytest.raises(PayloadInlineError):
        validate_payload_inline({"precio": 1.5})


def test_texto_libre_anidado_es_rechazado() -> None:
    with pytest.raises(PayloadInlineError):
        validate_payload_inline({"nivel1": {"nivel2": ["identificador_ok", "pero esto no lo es"]}})


def test_payload_que_excede_el_maximo_es_rechazado() -> None:
    # Cada valor es, por separado, estructural (un identificador corto y válido); lo que
    # dispara el rechazo es el TAMAÑO agregado, no la forma de ningún campo individual.
    payload = {f"campo_{i}": "identificador_valido_de_relleno" for i in range(200)}
    with pytest.raises(PayloadInlineError, match="excede"):
        validate_payload_inline(payload)


def test_clave_con_espacios_es_rechazada() -> None:
    with pytest.raises(PayloadInlineError):
        validate_payload_inline({"clave con espacios": "valor_ok"})
