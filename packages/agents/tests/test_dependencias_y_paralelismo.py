"""`edecan_agents.orchestrator` — helpers puros de dependencias/olas
(WP-V5-05): `_resolver_depende_de`/`_validar_depende_de` (validación del
campo `depende_de`) y `_construir_olas` (agrupación topológica + aislamiento
de perfiles `permite_dangerous_con_confirmacion=True`).

Tests unitarios y directos sobre estas funciones (sin pasar por `Agent`/LLM
fakeados, ver `test_orchestrator_run.py` para la integración end-to-end) —
más rápidos y precisos para la parte combinatoria de esta lógica."""

from __future__ import annotations

from edecan_agents.orchestrator import (
    _construir_olas,
    _historial_de_dependencias,
    _resolver_depende_de,
    _validar_depende_de,
)

# ---------------------------------------------------------------------------
# `_validar_depende_de`
# ---------------------------------------------------------------------------


def test_validar_lista_vacia_es_valida():
    assert _validar_depende_de([], idx=3, total=5) == []


def test_validar_indices_dentro_de_rango_y_menores_al_propio():
    assert _validar_depende_de([0, 2], idx=3, total=5) == [0, 2]


def test_validar_ordena_y_deduplica():
    assert _validar_depende_de([2, 0, 2, 0], idx=3, total=5) == [0, 2]


def test_validar_none_si_no_es_lista():
    assert _validar_depende_de("no es una lista", idx=3, total=5) is None
    assert _validar_depende_de(2, idx=3, total=5) is None
    assert _validar_depende_de(None, idx=3, total=5) is None


def test_validar_none_si_algun_valor_no_es_entero():
    assert _validar_depende_de([0, "x"], idx=3, total=5) is None


def test_validar_none_si_algun_valor_es_bool():
    """`bool` es subclase de `int` en Python (`True == 1`) pero nunca es un
    índice válido viniendo de un JSON de LLM — se rechaza explícito."""
    assert _validar_depende_de([True], idx=3, total=5) is None
    assert _validar_depende_de([False], idx=3, total=5) is None


def test_validar_none_si_algun_indice_es_negativo():
    assert _validar_depende_de([-1], idx=3, total=5) is None


def test_validar_none_si_algun_indice_fuera_de_rango_total():
    assert _validar_depende_de([5], idx=3, total=5) is None  # total=5 -> índices válidos 0..4


def test_validar_none_si_algun_indice_es_igual_al_propio():
    """Auto-referencia — la forma más simple de ciclo."""
    assert _validar_depende_de([3], idx=3, total=5) is None


def test_validar_none_si_algun_indice_es_mayor_al_propio():
    """Referencia a un paso futuro — la otra forma en que `depende_de`
    podría crear un ciclo (combinado con que ESE paso futuro dependa, a su
    vez, del actual)."""
    assert _validar_depende_de([4], idx=3, total=5) is None


# ---------------------------------------------------------------------------
# `_resolver_depende_de`
# ---------------------------------------------------------------------------


def test_resolver_sin_clave_depende_de_todos_los_anteriores():
    """Retrocompatibilidad (plan viejo / misión guardada antes de
    WP-V5-05): cada paso SIN la clave `depende_de` depende de TODOS los
    pasos anteriores — reproduce byte a byte la acumulación total de
    historial que ya existía antes de este campo."""
    pasos = [
        {"agente": "research", "instruccion": "uno"},
        {"agente": "research", "instruccion": "dos"},
        {"agente": "research", "instruccion": "tres"},
    ]
    resueltos = _resolver_depende_de(pasos)
    assert [p["depende_de"] for p in resueltos] == [[], [0], [0, 1]]
    # el resto de claves no se toca.
    assert [p["instruccion"] for p in resueltos] == ["uno", "dos", "tres"]


def test_resolver_no_muta_la_lista_original():
    pasos = [{"agente": "research", "instruccion": "uno"}]
    resueltos = _resolver_depende_de(pasos)
    assert "depende_de" not in pasos[0]
    assert resueltos[0]["depende_de"] == []


def test_resolver_lista_vacia_explicita_se_conserva():
    pasos = [
        {"agente": "research", "instruccion": "uno", "depende_de": []},
        {"agente": "research", "instruccion": "dos", "depende_de": []},
    ]
    resueltos = _resolver_depende_de(pasos)
    # a diferencia de "sin la clave", `[]` explícito en el 2do paso NO
    # implica depender del 1ro.
    assert [p["depende_de"] for p in resueltos] == [[], []]


def test_resolver_depende_de_null_explicito_se_trata_como_ausente():
    pasos = [
        {"agente": "research", "instruccion": "uno"},
        {"agente": "research", "instruccion": "dos", "depende_de": None},
    ]
    resueltos = _resolver_depende_de(pasos)
    assert resueltos[1]["depende_de"] == [0]


def test_resolver_valida_dependencia_explicita_correcta():
    pasos = [
        {"agente": "research", "instruccion": "uno", "depende_de": []},
        {"agente": "research", "instruccion": "dos", "depende_de": []},
        {"agente": "research", "instruccion": "tres", "depende_de": [0, 1]},
    ]
    resueltos = _resolver_depende_de(pasos)
    assert resueltos[2]["depende_de"] == [0, 1]


def test_resolver_indice_fuera_de_rango_se_degrada_a_secuencial_tras_el_anterior():
    pasos = [
        {"agente": "research", "instruccion": "uno", "depende_de": []},
        {"agente": "research", "instruccion": "dos", "depende_de": []},
        {"agente": "research", "instruccion": "tres", "depende_de": [99]},
    ]
    resueltos = _resolver_depende_de(pasos)
    # se descarta la lista COMPLETA (no solo el 99) y cae a [idx - 1] = [1].
    assert resueltos[2]["depende_de"] == [1]


def test_resolver_primer_paso_con_dependencia_invalida_cae_a_lista_vacia():
    pasos = [{"agente": "research", "instruccion": "uno", "depende_de": [0]}]  # auto-referencia
    resueltos = _resolver_depende_de(pasos)
    assert resueltos[0]["depende_de"] == []  # idx=0 -> no hay "anterior"


def test_resolver_ciclo_de_dos_pasos_se_degrada_y_deja_de_ser_ciclo():
    """El caso "ciclo" explícito del WP: el paso 0 dice depender del 1
    (referencia hacia adelante, inválida) y el paso 1 dice depender del 0
    (referencia hacia atrás, VÁLIDA por sí sola). Validar cada paso de forma
    independiente contra su propio índice basta para romper el ciclo: solo
    la referencia hacia adelante se descarta, la hacia atrás se conserva —
    el grafo resultante ya no tiene ciclo (por construcción, ver docstring
    del módulo: un índice solo puede apuntar hacia atrás)."""
    pasos = [
        {"agente": "research", "instruccion": "A", "depende_de": [1]},  # inválido: 1 >= idx(0)
        {"agente": "research", "instruccion": "B", "depende_de": [0]},  # válido: 0 < idx(1)
    ]
    resueltos = _resolver_depende_de(pasos)
    assert resueltos[0]["depende_de"] == []  # degradado (idx=0 -> sin "anterior")
    assert resueltos[1]["depende_de"] == [0]  # se conserva tal cual, era válido


def test_resolver_es_idempotente():
    pasos = [
        {"agente": "research", "instruccion": "uno"},
        {"agente": "research", "instruccion": "dos", "depende_de": [0]},
    ]
    una_vez = _resolver_depende_de(pasos)
    dos_veces = _resolver_depende_de(una_vez)
    assert una_vez == dos_veces


# ---------------------------------------------------------------------------
# `_construir_olas`
# ---------------------------------------------------------------------------


def _paso(seq: int, agente: str, depende_de: list[int]) -> dict:
    return {"seq": seq, "agente": agente, "instruccion": f"paso {seq}", "depende_de": depende_de}


def test_olas_pasos_independientes_quedan_en_la_misma_ola():
    pasos = [_paso(1, "research", []), _paso(2, "data_analyst", [])]
    olas = _construir_olas(pasos, completados_idx=set())
    assert len(olas) == 1
    assert {p["seq"] for p in olas[0]} == {1, 2}


def test_olas_cadena_secuencial_produce_una_ola_por_paso():
    pasos = [_paso(1, "research", []), _paso(2, "research", [0]), _paso(3, "research", [0, 1])]
    olas = _construir_olas(pasos, completados_idx=set())
    assert [{p["seq"] for p in ola} for ola in olas] == [{1}, {2}, {3}]


def test_olas_respeta_dependencias_parciales():
    """4 pasos: el 3ro depende del 1ro y 2do (espera ambos), el 4to es
    independiente -> se agrupa con el 1ro/2do en la 1ra ola."""
    pasos = [
        _paso(1, "research", []),
        _paso(2, "research", []),
        _paso(3, "research", [0, 1]),
        _paso(4, "research", []),
    ]
    olas = _construir_olas(pasos, completados_idx=set())
    assert {p["seq"] for p in olas[0]} == {1, 2, 4}
    assert {p["seq"] for p in olas[1]} == {3}


def test_olas_usa_completados_idx_para_pasos_ya_terminados_en_una_pasada_anterior():
    """Simula una re-invocación de `_construir_olas` tras un replan: el
    índice 0 ya está en `completados_idx` (no viene en `pasos_pendientes`),
    así que el paso que depende de él queda elegible de inmediato."""
    pasos = [_paso(2, "research", [0])]  # seq=2 -> idx=1, depende del idx 0.
    olas = _construir_olas(pasos, completados_idx={0})
    assert len(olas) == 1
    assert olas[0][0]["seq"] == 2


def test_olas_perfil_dangerous_capable_nunca_comparte_ola():
    """`developer` tiene `permite_dangerous_con_confirmacion=True`
    (`profiles.py`) — aunque topológicamente sea elegible junto a otros 2
    pasos independientes, `_construir_olas` lo separa a su propia ola."""
    pasos = [_paso(1, "research", []), _paso(2, "developer", []), _paso(3, "research", [])]
    olas = _construir_olas(pasos, completados_idx=set())
    # el paso 2 (developer) queda solo en su propia ola; 1 y 3 pueden seguir
    # juntos (preserva el orden relativo: antes bloque [1], luego [2] solo,
    # luego [3]).
    assert [{p["seq"] for p in ola} for ola in olas] == [{1}, {2}, {3}]


def test_olas_dos_pasos_dangerous_capable_seguidos_quedan_cada_uno_solo():
    pasos = [_paso(1, "developer", []), _paso(2, "qa", [])]
    olas = _construir_olas(pasos, completados_idx=set())
    assert [{p["seq"] for p in ola} for ola in olas] == [{1}, {2}]


def test_olas_dangerous_capable_al_final_de_un_bloque_independiente():
    pasos = [_paso(1, "research", []), _paso(2, "research", []), _paso(3, "developer", [])]
    olas = _construir_olas(pasos, completados_idx=set())
    assert [{p["seq"] for p in ola} for ola in olas] == [{1, 2}, {3}]


def test_olas_agente_desconocido_cae_a_research_y_no_es_dangerous():
    """Un `agente` que no resuelve a ningún perfil (o a uno no disponible)
    cae a `research` (`_resolver_perfil`) — `research` no es
    `permite_dangerous_con_confirmacion`, así que sigue agrupable."""
    pasos = [_paso(1, "research", []), _paso(2, "clave_inventada", [])]
    olas = _construir_olas(pasos, completados_idx=set())
    assert len(olas) == 1
    assert {p["seq"] for p in olas[0]} == {1, 2}


def test_olas_ciclo_defensivo_no_entra_en_loop_infinito():
    """Salvaguarda defensiva: si de alguna forma llegara un plan con un
    ciclo real (p. ej. cargado a mano, sin pasar por `_resolver_depende_de`),
    `_construir_olas` no debe colgarse — el resto se fuerza en una ola
    final."""
    pasos = [_paso(1, "research", [1]), _paso(2, "research", [0])]  # ciclo directo entre sí.
    olas = _construir_olas(pasos, completados_idx=set())
    total_pasos = sum(len(ola) for ola in olas)
    assert total_pasos == 2  # ambos terminan en alguna ola, sin loop infinito.


# ---------------------------------------------------------------------------
# `_historial_de_dependencias`
# ---------------------------------------------------------------------------


def test_historial_de_dependencias_vacio_si_no_depende_de_nada():
    paso = {"depende_de": []}
    assert _historial_de_dependencias(paso, resultados={}, instrucciones={}) == []


def test_historial_de_dependencias_solo_las_declaradas_no_todo_lo_anterior():
    """Aunque `resultados`/`instrucciones` traigan más índices completados,
    solo los que este paso DECLARA en `depende_de` aparecen en su
    historial."""
    paso = {"depende_de": [1]}
    resultados = {0: "resultado 0", 1: "resultado 1", 2: "resultado 2"}
    instrucciones = {0: "instr 0", 1: "instr 1", 2: "instr 2"}
    historial = _historial_de_dependencias(paso, resultados, instrucciones)
    assert [(m.role, m.content) for m in historial] == [
        ("user", "instr 1"),
        ("assistant", "resultado 1"),
    ]


def test_historial_de_dependencias_en_orden_de_indice_ascendente():
    paso = {"depende_de": [2, 0]}  # declarado fuera de orden.
    resultados = {0: "r0", 2: "r2"}
    instrucciones = {0: "i0", 2: "i2"}
    historial = _historial_de_dependencias(paso, resultados, instrucciones)
    assert [m.content for m in historial] == ["i0", "r0", "i2", "r2"]


def test_historial_de_dependencias_omite_una_dependencia_sin_resultado_todavia():
    """Defensivo: no debería ocurrir en la práctica (las olas garantizan que
    toda dependencia ya terminó), pero si pasara, se omite en vez de
    reventar con un `KeyError`."""
    paso = {"depende_de": [0, 1]}
    historial = _historial_de_dependencias(paso, resultados={0: "r0"}, instrucciones={0: "i0"})
    assert [m.content for m in historial] == ["i0", "r0"]
