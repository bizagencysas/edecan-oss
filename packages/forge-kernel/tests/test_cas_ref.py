from __future__ import annotations

import pytest
from edecan_forge_kernel.contracts import CasRef


def test_from_bytes_y_parse_dan_el_mismo_ref() -> None:
    ref = CasRef.from_bytes(b"contenido de prueba")
    reconstituido = CasRef.parse(str(ref))
    assert ref == reconstituido


def test_str_tiene_la_forma_algoritmo_dos_puntos_hex() -> None:
    ref = CasRef.from_bytes(b"x")
    algoritmo, digest = str(ref).split(":", 1)
    assert algoritmo == "b2b"
    assert len(digest) == 64
    int(digest, 16)  # no lanza: es hex válido


def test_contenidos_distintos_dan_refs_distintos() -> None:
    assert CasRef.from_bytes(b"a") != CasRef.from_bytes(b"b")


def test_mismo_contenido_da_el_mismo_ref_siempre() -> None:
    assert CasRef.from_bytes(b"repetible") == CasRef.from_bytes(b"repetible")


def test_digest_invalido_rechazado() -> None:
    with pytest.raises(ValueError, match="digest de CasRef"):
        CasRef(digest="no-es-hex")


def test_parse_algoritmo_no_soportado_rechazado() -> None:
    with pytest.raises(ValueError, match="no soportado"):
        CasRef.parse("b3:" + "0" * 64)


def test_parse_mal_formado_rechazado() -> None:
    with pytest.raises(ValueError, match="mal formado"):
        CasRef.parse("sin-dos-puntos")
