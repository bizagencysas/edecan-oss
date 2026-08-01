"""Verifica la estimación de tokens de razonamiento (Workers AI no los desglosa)."""

from __future__ import annotations

from edecan_forge_probe.providers import ProbeCompletionResponse
from edecan_llm.base import Usage


def _resp(**kw) -> ProbeCompletionResponse:
    base = dict(text="listo", usage=Usage(input_tokens=33, output_tokens=129), stop_reason="end")
    base.update(kw)
    return ProbeCompletionResponse(**base)


def test_estima_desde_el_reparto_de_caracteres():
    r = _resp(reasoning_content="x" * 1000)  # 1000 vs 5 caracteres de contenido
    assert r.reasoning_tokens is None  # no reportado, y se queda así
    assert r.reasoning_tokens_estimados == round(129 * 1000 / 1005) == 128


def test_prefiere_el_dato_real_sobre_la_estimacion():
    r = _resp(reasoning_content="x" * 1000, reasoning_tokens=77)
    assert r.reasoning_tokens_estimados == 77


def test_sin_razonamiento_no_estima():
    assert _resp().reasoning_tokens_estimados is None


def test_sin_tokens_de_salida_no_estima():
    r = _resp(reasoning_content="x" * 100, usage=Usage(input_tokens=10, output_tokens=0))
    assert r.reasoning_tokens_estimados is None
