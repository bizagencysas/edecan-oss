"""`edecan_core.memory.profile.build_profile` — merge conservador del perfil
vivo (WP-V2-13, ver el docstring de `edecan_core/memory/profile.py`).

Todo con un `llm_complete` fake determinista — sin red, sin `edecan_llm`.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from edecan_core.memory.profile import (
    CAMPOS_DATOS,
    LISTA_MAX_ITEMS,
    RESUMEN_MAX_CHARS,
    build_profile,
)


def _llm_fijo(texto: str):
    """`LlmComplete` fake que ignora el prompt y siempre responde `texto`."""

    async def _complete(prompt: str) -> str:
        return texto

    return _complete


def _llm_que_falla(exc: Exception | None = None):
    error = exc if exc is not None else RuntimeError("proveedor caído")

    async def _complete(prompt: str) -> str:
        raise error

    return _complete


def _llm_capturador(texto: str, prompts: list[str]):
    """Como `_llm_fijo` pero además registra el prompt recibido, para poder
    afirmar qué se le pidió al modelo."""

    async def _complete(prompt: str) -> str:
        prompts.append(prompt)
        return texto

    return _complete


def _respuesta(
    *,
    resumen: str = "",
    datos: dict[str, list[str]] | None = None,
    reemplaza: dict[str, list[str]] | None = None,
) -> str:
    payload: dict[str, Any] = {"resumen": resumen, "datos": datos or {}}
    if reemplaza is not None:
        payload["reemplaza"] = reemplaza
    return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Forma del resultado: siempre las 6 categorías, siempre `dict`
# ---------------------------------------------------------------------------


async def test_previous_none_produce_esqueleto_vacio_si_el_llm_no_aporta_nada() -> None:
    resultado = await build_profile([], None, _llm_fijo(_respuesta()))
    assert resultado == {"resumen": "", "datos": {campo: [] for campo in CAMPOS_DATOS}}


async def test_resultado_siempre_trae_las_6_categorias_aunque_el_llm_solo_mencione_una() -> None:
    resultado = await build_profile(
        ["le gusta el café"],
        None,
        _llm_fijo(_respuesta(datos={"gustos": ["Le gusta el café"]})),
    )
    assert set(resultado["datos"].keys()) == set(CAMPOS_DATOS)
    assert resultado["datos"]["gustos"] == ["Le gusta el café"]
    for campo in CAMPOS_DATOS:
        if campo != "gustos":
            assert resultado["datos"][campo] == []


# ---------------------------------------------------------------------------
# Merge conservador: agrega sin borrar
# ---------------------------------------------------------------------------


async def test_agrega_entradas_nuevas_sin_borrar_las_previas() -> None:
    previo = {
        "resumen": "",
        "datos": {**{c: [] for c in CAMPOS_DATOS}, "gustos": ["Le gusta el café"]},
    }
    resultado = await build_profile(
        ["ahora también le gusta el té"],
        previo,
        _llm_fijo(_respuesta(datos={"gustos": ["Le gusta el té"]})),
    )
    assert resultado["datos"]["gustos"] == ["Le gusta el café", "Le gusta el té"]


async def test_llm_que_no_menciona_una_categoria_previa_no_la_borra() -> None:
    """Si el LLM devuelve `datos.proyectos: []` (u omite la clave), las
    entradas previas de esa categoría sobreviven intactas — ver "Merge
    conservador" en el docstring del módulo."""
    previo = {
        "resumen": "",
        "datos": {**{c: [] for c in CAMPOS_DATOS}, "proyectos": ["Lanzamiento de Acme v2"]},
    }
    resultado = await build_profile(
        ["prefiere respuestas breves"],
        previo,
        _llm_fijo(_respuesta(datos={"habitos": ["Prefiere respuestas breves"]})),
    )
    assert resultado["datos"]["proyectos"] == ["Lanzamiento de Acme v2"]
    assert resultado["datos"]["habitos"] == ["Prefiere respuestas breves"]


# ---------------------------------------------------------------------------
# Dedup case-insensitive
# ---------------------------------------------------------------------------


async def test_dedup_case_insensitive_no_duplica_una_entrada_repetida() -> None:
    previo = {
        "resumen": "",
        "datos": {**{c: [] for c in CAMPOS_DATOS}, "gustos": ["Le gusta el café"]},
    }
    resultado = await build_profile(
        [],
        previo,
        _llm_fijo(_respuesta(datos={"gustos": ["le gusta el CAFÉ"]})),
    )
    assert resultado["datos"]["gustos"] == ["Le gusta el café"]


async def test_dedup_case_insensitive_dentro_de_las_entradas_nuevas() -> None:
    resultado = await build_profile(
        [],
        None,
        _llm_fijo(_respuesta(datos={"gustos": ["Café", "café", "CAFÉ", "Té"]})),
    )
    assert resultado["datos"]["gustos"] == ["Café", "Té"]


# ---------------------------------------------------------------------------
# Contradicción explícita -> reemplaza
# ---------------------------------------------------------------------------


async def test_contradiccion_explicita_reemplaza_la_entrada_vieja() -> None:
    previo = {
        "resumen": "",
        "datos": {**{c: [] for c in CAMPOS_DATOS}, "empresas": ["Trabaja en Acme"]},
    }
    resultado = await build_profile(
        ["ya no trabaja en Acme, ahora está en Globex"],
        previo,
        _llm_fijo(
            _respuesta(
                datos={"empresas": ["Trabaja en Globex"]},
                reemplaza={"empresas": ["Trabaja en Acme"]},
            )
        ),
    )
    assert resultado["datos"]["empresas"] == ["Trabaja en Globex"]


async def test_reemplaza_es_case_insensitive() -> None:
    previo = {
        "resumen": "",
        "datos": {**{c: [] for c in CAMPOS_DATOS}, "gustos": ["Le gusta el café"]},
    }
    resultado = await build_profile(
        [],
        previo,
        _llm_fijo(_respuesta(datos={}, reemplaza={"gustos": ["le gusta el café"]})),
    )
    assert resultado["datos"]["gustos"] == []


async def test_reemplaza_ausente_no_borra_nada() -> None:
    previo = {
        "resumen": "",
        "datos": {**{c: [] for c in CAMPOS_DATOS}, "gustos": ["Le gusta el café"]},
    }
    resultado = await build_profile([], previo, _llm_fijo(_respuesta(datos={"gustos": []})))
    assert resultado["datos"]["gustos"] == ["Le gusta el café"]


async def test_reemplaza_solo_afecta_su_propia_categoria() -> None:
    previo = {
        "resumen": "",
        "datos": {
            **{c: [] for c in CAMPOS_DATOS},
            "gustos": ["Le gusta el café"],
            "habitos": ["Le gusta el café"],
        },
    }
    resultado = await build_profile(
        [],
        previo,
        _llm_fijo(_respuesta(datos={}, reemplaza={"gustos": ["Le gusta el café"]})),
    )
    assert resultado["datos"]["gustos"] == []
    assert resultado["datos"]["habitos"] == ["Le gusta el café"]  # otra categoría, intacta


# ---------------------------------------------------------------------------
# Caps a 20 items
# ---------------------------------------------------------------------------


async def test_cap_a_20_items_prioriza_lo_previo_sobre_lo_nuevo() -> None:
    previos = [f"gusto previo {i}" for i in range(LISTA_MAX_ITEMS)]
    previo = {"resumen": "", "datos": {**{c: [] for c in CAMPOS_DATOS}, "gustos": previos}}
    resultado = await build_profile(
        [],
        previo,
        _llm_fijo(_respuesta(datos={"gustos": ["algo completamente nuevo"]})),
    )
    assert resultado["datos"]["gustos"] == previos
    assert len(resultado["datos"]["gustos"]) == LISTA_MAX_ITEMS
    assert "algo completamente nuevo" not in resultado["datos"]["gustos"]


async def test_cap_a_20_items_cuando_todo_es_nuevo() -> None:
    nuevos = [f"gusto {i}" for i in range(30)]
    resultado = await build_profile([], None, _llm_fijo(_respuesta(datos={"gustos": nuevos})))
    assert resultado["datos"]["gustos"] == nuevos[:LISTA_MAX_ITEMS]


# ---------------------------------------------------------------------------
# Parseo tolerante: JSON malformado / envuelto / con preámbulo -> nunca lanza
# ---------------------------------------------------------------------------


async def test_json_invalido_devuelve_el_perfil_previo_sin_cambios() -> None:
    previo = {
        "resumen": "Prefieres respuestas breves.",
        "datos": {**{c: [] for c in CAMPOS_DATOS}, "gustos": ["Le gusta el café"]},
    }
    resultado = await build_profile([], previo, _llm_fijo("esto no es JSON en absoluto"))
    assert resultado == previo


async def test_sin_previous_y_json_invalido_devuelve_esqueleto_vacio() -> None:
    resultado = await build_profile(["algo"], None, _llm_fijo("<< no json >>"))
    assert resultado == {"resumen": "", "datos": {campo: [] for campo in CAMPOS_DATOS}}


async def test_json_envuelto_en_bloque_de_codigo_se_parsea_igual() -> None:
    texto = "```json\n" + _respuesta(resumen="Prefieres respuestas breves.") + "\n```"
    resultado = await build_profile([], None, _llm_fijo(texto))
    assert resultado["resumen"] == "Prefieres respuestas breves."


async def test_json_con_preambulo_y_cola_de_prosa_se_extrae() -> None:
    texto = (
        "Claro, aquí está el perfil actualizado:\n"
        + _respuesta(datos={"gustos": ["Le gusta el café"]})
        + "\n\nEspero que te sirva."
    )
    resultado = await build_profile([], None, _llm_fijo(texto))
    assert resultado["datos"]["gustos"] == ["Le gusta el café"]


async def test_json_valido_pero_no_es_un_objeto_devuelve_previous() -> None:
    previo = {"resumen": "x", "datos": {c: [] for c in CAMPOS_DATOS}}
    resultado = await build_profile([], previo, _llm_fijo("[1, 2, 3]"))
    assert resultado == previo


async def test_datos_con_forma_inesperada_no_lanza_y_se_ignora() -> None:
    previo = {
        "resumen": "",
        "datos": {**{c: [] for c in CAMPOS_DATOS}, "gustos": ["Le gusta el café"]},
    }
    texto = json.dumps({"resumen": "", "datos": "no soy un objeto"})
    resultado = await build_profile([], previo, _llm_fijo(texto))
    assert resultado["datos"]["gustos"] == ["Le gusta el café"]


# ---------------------------------------------------------------------------
# `llm_complete` que lanza -> jamás propaga
# ---------------------------------------------------------------------------


async def test_llm_complete_que_lanza_devuelve_el_perfil_previo() -> None:
    previo = {"resumen": "Prefieres respuestas breves.", "datos": {c: [] for c in CAMPOS_DATOS}}
    resultado = await build_profile(["algo"], previo, _llm_que_falla())
    assert resultado == previo


async def test_llm_complete_que_lanza_sin_previous_devuelve_esqueleto_vacio() -> None:
    resultado = await build_profile(["algo"], None, _llm_que_falla(ValueError("boom")))
    assert resultado == {"resumen": "", "datos": {campo: [] for campo in CAMPOS_DATOS}}


# ---------------------------------------------------------------------------
# Resumen: 2ª persona, tope de caracteres, conserva si el LLM no aporta uno
# ---------------------------------------------------------------------------


async def test_resumen_del_llm_se_usa_cuando_no_esta_vacio() -> None:
    resultado = await build_profile(
        [], None, _llm_fijo(_respuesta(resumen="Prefieres respuestas breves y directas."))
    )
    assert resultado["resumen"] == "Prefieres respuestas breves y directas."


async def test_resumen_vacio_del_llm_conserva_el_resumen_previo() -> None:
    previo = {"resumen": "Prefieres respuestas breves.", "datos": {c: [] for c in CAMPOS_DATOS}}
    resultado = await build_profile([], previo, _llm_fijo(_respuesta(resumen="")))
    assert resultado["resumen"] == "Prefieres respuestas breves."


async def test_resumen_se_recorta_a_500_caracteres() -> None:
    resumen_largo = "x" * 900
    resultado = await build_profile([], None, _llm_fijo(_respuesta(resumen=resumen_largo)))
    assert len(resultado["resumen"]) == RESUMEN_MAX_CHARS
    assert resultado["resumen"] == "x" * RESUMEN_MAX_CHARS


async def test_previous_con_resumen_demasiado_largo_tambien_se_recorta_al_normalizar() -> None:
    previo = {"resumen": "y" * 900, "datos": {c: [] for c in CAMPOS_DATOS}}
    resultado = await build_profile([], previo, _llm_que_falla())
    assert len(resultado["resumen"]) == RESUMEN_MAX_CHARS


# ---------------------------------------------------------------------------
# `previous` con forma inesperada nunca lanza
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "previous_raro", [{}, {"datos": None}, {"datos": {"gustos": "no es lista"}}, {"resumen": 123}]
)
async def test_previous_con_forma_inesperada_no_lanza(previous_raro: dict) -> None:
    resultado = await build_profile([], previous_raro, _llm_que_falla())
    assert set(resultado["datos"].keys()) == set(CAMPOS_DATOS)
    assert all(isinstance(v, list) for v in resultado["datos"].values())


async def test_previous_con_items_no_string_en_una_lista_se_coacciona_o_descarta() -> None:
    previo = {
        "resumen": "",
        "datos": {**{c: [] for c in CAMPOS_DATOS}, "metas": ["Meta real", 42, None, {"x": 1}]},
    }
    resultado = await build_profile([], previo, _llm_que_falla())
    assert resultado["datos"]["metas"] == ["Meta real", "42"]


# ---------------------------------------------------------------------------
# El prompt que arma `build_profile` incluye el perfil previo y las memorias
# ---------------------------------------------------------------------------


async def test_el_prompt_incluye_el_perfil_previo_y_las_memorias_recientes() -> None:
    prompts: list[str] = []
    previo = {"resumen": "Prefieres respuestas breves.", "datos": {c: [] for c in CAMPOS_DATOS}}
    await build_profile(
        ["Su empresa se llama Acme"], previo, _llm_capturador(_respuesta(), prompts)
    )
    assert len(prompts) == 1
    assert "Prefieres respuestas breves." in prompts[0]
    assert "Su empresa se llama Acme" in prompts[0]


async def test_sin_memorias_el_prompt_lo_deja_explicito() -> None:
    prompts: list[str] = []
    await build_profile([], None, _llm_capturador(_respuesta(), prompts))
    assert "sin memorias nuevas" in prompts[0]
