from __future__ import annotations

import re

import pytest
from edecan_forge_kernel.contracts import derive_ulid

_ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def test_derive_ulid_tiene_la_forma_correcta() -> None:
    ulid = derive_ulid(ts_physical_us=1_800_000_000_000_000, id_seed=b"0" * 16, seq=1)
    assert _ULID_RE.match(ulid), ulid


def test_derive_ulid_es_determinista() -> None:
    a = derive_ulid(ts_physical_us=123, id_seed=b"z" * 16, seq=7)
    b = derive_ulid(ts_physical_us=123, id_seed=b"z" * 16, seq=7)
    assert a == b


def test_derive_ulid_cambia_con_seq() -> None:
    a = derive_ulid(ts_physical_us=123, id_seed=b"z" * 16, seq=1)
    b = derive_ulid(ts_physical_us=123, id_seed=b"z" * 16, seq=2)
    assert a != b


def test_derive_ulid_cambia_con_id_seed() -> None:
    a = derive_ulid(ts_physical_us=123, id_seed=b"a" * 16, seq=1)
    b = derive_ulid(ts_physical_us=123, id_seed=b"b" * 16, seq=1)
    assert a != b


def test_derive_ulid_rechaza_semilla_de_tamano_incorrecto() -> None:
    with pytest.raises(ValueError, match="16 bytes"):
        derive_ulid(ts_physical_us=1, id_seed=b"corta", seq=1)
