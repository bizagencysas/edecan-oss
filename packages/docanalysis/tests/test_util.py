"""Tests de `edecan_docanalysis._util`."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from edecan_docanalysis._util import clamp_int, parse_uuid, slugify, tenant_flags


def test_parse_uuid_valido():
    valor = uuid4()
    assert parse_uuid(str(valor)) == valor


def test_parse_uuid_invalido_devuelve_none():
    assert parse_uuid("no-es-un-uuid") is None
    assert parse_uuid(None) is None
    assert parse_uuid("") is None
    assert parse_uuid(123) is None


def test_clamp_int_acota_al_rango():
    assert clamp_int(5, default=1, minimo=0, maximo=10) == 5
    assert clamp_int(50, default=1, minimo=0, maximo=10) == 10
    assert clamp_int(-5, default=1, minimo=0, maximo=10) == 0
    assert clamp_int(None, default=3, minimo=0, maximo=10) == 3
    assert clamp_int("no-es-numero", default=3, minimo=0, maximo=10) == 3


def test_slugify_normaliza_y_recorta():
    assert slugify("Reporte de Ventas Q1 2026!") == "reporte-de-ventas-q1-2026"
    assert slugify("   ") == "archivo"
    assert slugify("", default="grafico") == "grafico"
    assert slugify("a" * 100, max_len=10) == "a" * 10


def test_tenant_flags_lee_extras_flags_o_cae_a_vacio():
    ctx_con_flags = SimpleNamespace(extras={"flags": {"models.premium": True}})
    assert tenant_flags(ctx_con_flags) == {"models.premium": True}

    ctx_sin_flags = SimpleNamespace(extras={})
    assert tenant_flags(ctx_sin_flags) == {}

    ctx_extras_no_dict = SimpleNamespace(extras=None)
    assert tenant_flags(ctx_extras_no_dict) == {}
