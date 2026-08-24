"""`edecan_agents.branching` — bifurcación de misiones + planificación
contrafactual (PHASE2 §72-§74): `Branch`/`branch_from` (ramas sin destruir el
plan base), `merge_branches` (comparación defensiva de dos ramas),
`counterfactual_options` (comparación determinista de estrategias) y
`eval_plan_quality` (puntuación heurística de un plan antes de ejecutarlo).

Tests unitarios y directos, sin LLM ni I/O: todo el módulo es puro y
determinista, así que estos tests corren sin fakes (ver `conftest.py`)."""

from __future__ import annotations

from edecan_agents.branching import (
    Branch,
    branch_from,
    counterfactual_options,
    eval_plan_quality,
    merge_branches,
)

# ---------------------------------------------------------------------------
# `Branch` (dataclass)
# ---------------------------------------------------------------------------


def test_branch_se_construye_con_name_steps_y_state():
    branch = Branch(name="enfoque A", steps=[{"seq": 1}], state={"presupuesto": 3})
    assert branch.name == "enfoque A"
    assert branch.steps == [{"seq": 1}]
    assert branch.state == {"presupuesto": 3}


def test_branch_state_tiene_default_vacio():
    branch = Branch(name="enfoque B", steps=[])
    assert branch.state == {}


# ---------------------------------------------------------------------------
# `branch_from`
# ---------------------------------------------------------------------------


def _base() -> list[dict]:
    return [
        {"seq": 1, "agente": "research", "instruccion": "investigar el problema"},
        {"seq": 2, "agente": "developer", "instruccion": "implementar la solución A"},
        {"seq": 3, "agente": "qa", "instruccion": "probar", "depende_de": [0, 1]},
    ]


def test_branch_from_no_muta_la_lista_original():
    base = _base()
    resultado = branch_from(base, name="B", replace_step_idx=1, new_instruction="solución B")
    assert resultado is not base
    # la original conserva la instrucción A.
    assert base[1]["instruccion"] == "implementar la solución A"
    assert resultado[1]["instruccion"] == "solución B"


def test_branch_from_cambia_solo_la_instruccion_del_indice_indicado():
    base = _base()
    resultado = branch_from(base, name="B", replace_step_idx=1, new_instruction="solución B")
    assert resultado[0] == base[0]
    assert resultado[1]["agente"] == "developer"  # el resto del paso no se toca
    assert resultado[2] == base[2]


def test_branch_from_conserva_claves_vecinas_del_paso_reemplazado():
    base = _base()
    resultado = branch_from(base, name="B", replace_step_idx=2, new_instruction="probar B")
    assert resultado[2]["agente"] == "qa"
    assert resultado[2]["depende_de"] == [0, 1]
    assert resultado[2]["instruccion"] == "probar B"


def test_branch_from_hace_deep_copy_de_dicts_anidados():
    base = [{"agente": "research", "instruccion": "uno", "depende_de": [[1, 2]]}]
    resultado = branch_from(base, name="B", replace_step_idx=0, new_instruction="dos")
    resultado[0]["depende_de"].append(3)
    assert base[0]["depende_de"] == [[1, 2]]  # la original no ve el append


def test_branch_from_indice_fuera_de_rango_devuelve_copia_sin_cambios():
    base = _base()
    resultado = branch_from(base, name="B", replace_step_idx=99, new_instruction="x")
    assert resultado == base
    assert resultado is not base


def test_branch_from_indice_negativo_devuelve_copia_sin_cambios():
    base = _base()
    resultado = branch_from(base, name="B", replace_step_idx=-1, new_instruction="x")
    assert resultado == base


def test_branch_from_indice_no_numerico_no_lanza():
    base = _base()
    resultado = branch_from(base, name="B", replace_step_idx="no_num", new_instruction="x")
    assert resultado == base


def test_branch_from_lista_vacia_devuelve_lista_vacia():
    assert branch_from([], name="B", replace_step_idx=0, new_instruction="x") == []


def test_branch_from_base_no_lista_devuelve_lista_vacia():
    assert branch_from(None, name="B", replace_step_idx=0, new_instruction="x") == []


def test_branch_from_coerce_instruccion_a_str_y_recorta():
    base = _base()
    resultado = branch_from(base, name="B", replace_step_idx=0, new_instruction="  nueva  ")
    assert resultado[0]["instruccion"] == "nueva"


def test_branch_from_paso_no_dict_se_convierte_en_dict():
    base = ["no soy dict"]
    resultado = branch_from(base, name="B", replace_step_idx=0, new_instruction="x")
    assert resultado == [{"instruccion": "x"}]


# ---------------------------------------------------------------------------
# `merge_branches`
# ---------------------------------------------------------------------------


def test_merge_branches_devuelve_las_tres_claves():
    resultado = merge_branches([], [])
    assert set(resultado.keys()) == {"branch_a", "branch_b", "conflicts"}
    assert resultado["branch_a"] == []
    assert resultado["branch_b"] == []
    assert resultado["conflicts"] == []


def test_merge_branches_sin_conflictos_cuando_son_identicas():
    a = [{"idx": 0, "resultado": "el precio es 100 USD"}]
    resultado = merge_branches(a, list(a))
    assert resultado["branch_a"] == a
    assert resultado["branch_b"] == a
    assert resultado["conflicts"] == []


def test_merge_branches_detecta_distinto_numero_de_pasos():
    resultado = merge_branches([{"resultado": "a"}], [{"resultado": "a"}, {"resultado": "b"}])
    assert resultado["conflicts"] == [
        {
            "kind": "step_count",
            "branch_a_steps": 1,
            "branch_b_steps": 2,
            "description": "Las ramas tienen distinto número de pasos: A=1, B=2.",
        }
    ]


def test_merge_branches_detecta_discrepancia_numerica():
    a = [{"idx": 0, "resultado": "el costo es 100 USD"}]
    b = [{"idx": 0, "resultado": "el costo es 150 USD"}]
    resultado = merge_branches(a, b)
    assert len(resultado["conflicts"]) == 1
    assert resultado["conflicts"][0]["kind"] == "numeric_discrepancy"
    assert resultado["conflicts"][0]["step_a_idx"] == 0
    assert resultado["conflicts"][0]["step_b_idx"] == 0


def test_merge_branches_ignora_diferencia_pequena():
    a = [{"resultado": "100"}]
    b = [{"resultado": "105"}]  # 5% y <10 abs -> no es conflicto.
    resultado = merge_branches(a, b)
    assert resultado["conflicts"] == []


def test_merge_branches_ignora_resultados_sin_numeros():
    a = [{"resultado": "sin cifras aquí"}]
    b = [{"resultado": "tampoco cifras"}]
    resultado = merge_branches(a, b)
    assert resultado["conflicts"] == []


def test_merge_branches_es_defensiva_con_input_roto():
    resultado = merge_branches(None, "no lista")
    assert resultado == {"branch_a": [], "branch_b": [], "conflicts": []}


def test_merge_branches_normaliza_entradas_no_dict():
    a = [{"resultado": "100"}, "no dict"]
    b = [{"resultado": "100"}, {"resultado": "100"}]
    resultado = merge_branches(a, b)
    assert resultado["branch_a"] == [{"resultado": "100"}, {}]
    assert resultado["conflicts"] == []


def test_merge_branches_compara_posicionalmente():
    """El conflicto se reporta contra el índice posicional (paso i+1)."""
    a = [{"resultado": "uno"}, {"resultado": "50"}]
    b = [{"resultado": "uno"}, {"resultado": "90"}]
    resultado = merge_branches(a, b)
    assert resultado["conflicts"] == [
        {
            "kind": "numeric_discrepancy",
            "step_a_idx": 1,
            "step_b_idx": 1,
            "description": (
                "El paso 2 reporta ~50 en la rama A y ~90 en la rama B; "
                "difieren (44% relativo, 40 absoluto)."
            ),
        }
    ]


# ---------------------------------------------------------------------------
# `counterfactual_options`
# ---------------------------------------------------------------------------


def test_migrate_se_clasifica_como_alto_irreversible_caro():
    opciones = counterfactual_options("objetivo", ["migrate service"])
    assert opciones == [
        {
            "strategy": "migrate service",
            "risks": "alta",
            "reversibility": "irreversible",
            "cost_class": "alta",
        }
    ]


def test_wrap_se_clasifica_como_bajo_reversible_barato():
    opciones = counterfactual_options("objetivo", ["wrap existing service"])
    assert opciones == [
        {
            "strategy": "wrap existing service",
            "risks": "baja",
            "reversibility": "reversible",
            "cost_class": "baja",
        }
    ]


def test_adapt_se_clasifica_como_punto_medio():
    opciones = counterfactual_options("objetivo", ["adapt the service"])
    assert opciones[0] == {
        "strategy": "adapt the service",
        "risks": "media",
        "reversibility": "parcial",
        "cost_class": "media",
    }


def test_sin_senal_se_rellenan_defaults_neutros():
    opciones = counterfactual_options("objetivo", ["hacer algo sin palabras clave"])
    assert opciones[0] == {
        "strategy": "hacer algo sin palabras clave",
        "risks": "media",
        "reversibility": "desconocida",
        "cost_class": "media",
    }


def test_las_claves_son_exactamente_las_esperadas():
    opciones = counterfactual_options("objetivo", ["migrate service"])
    assert set(opciones[0].keys()) == {"strategy", "risks", "reversibility", "cost_class"}


def test_senal_migracion_gana_a_senal_segura_mezclada():
    """Si una estrategia mezcla "migrar" y "wrap" (envolver mientras se migra),
    la lectura conservadora (riesgo alto) gana."""
    opciones = counterfactual_options("objetivo", ["wrap while we migrate"])
    assert opciones[0]["risks"] == "alta"
    assert opciones[0]["reversibility"] == "irreversible"


def test_adaptador_se_clasifica_bajo_no_medio():
    """ "adaptador" contiene "adapt" (señal media) pero es una envoltura: la
    señal baja debe ganar porque se evalúa antes."""
    opciones = counterfactual_options("objetivo", ["build an adapter"])
    assert opciones[0]["risks"] == "baja"
    assert opciones[0]["cost_class"] == "baja"


def test_es_determinista_e_insensible_a_mayusculas():
    a = counterfactual_options("x", ["MIGRATE Service"])
    b = counterfactual_options("y", ["migrate service"])
    # La clasificación es insensible a mayúsculas; el texto `strategy` se
    # conserva tal cual lo dio el caller (no se reescribe).
    for i in range(len(a)):
        assert a[i]["risks"] == b[i]["risks"]
        assert a[i]["reversibility"] == b[i]["reversibility"]
        assert a[i]["cost_class"] == b[i]["cost_class"]


def test_strategies_vacia_o_no_lista_devuelve_lista_vacia():
    assert counterfactual_options("objetivo", []) == []
    assert counterfactual_options("objetivo", None) == []
    assert counterfactual_options("objetivo", "no lista") == []


def test_entrada_no_string_se_coerce_a_str():
    opciones = counterfactual_options("objetivo", ["migrate", 42])
    assert opciones[1]["strategy"] == "42"
    assert opciones[1]["risks"] == "media"  # "42" no tiene señal -> defaults.


def test_preserva_el_orden_de_las_estrategias():
    opciones = counterfactual_options("objetivo", ["migrate", "wrap", "adapt"])
    assert [o["strategy"] for o in opciones] == ["migrate", "wrap", "adapt"]


# ---------------------------------------------------------------------------
# `eval_plan_quality`
# ---------------------------------------------------------------------------


def _plan_limpio() -> list[dict]:
    return [
        {"seq": 1, "agente": "research", "instruccion": "investigar", "depende_de": []},
        {"seq": 2, "agente": "developer", "instruccion": "implementar", "depende_de": [0]},
        {"seq": 3, "agente": "qa", "instruccion": "probar", "depende_de": [0, 1]},
    ]


def test_plan_limpio_puntua_1_sin_issues():
    resultado = eval_plan_quality(_plan_limpio(), max_steps=3)
    assert resultado == {"score": 1.0, "issues": []}


def test_plan_vacio_puntua_0():
    assert eval_plan_quality([], max_steps=3) == {
        "score": 0.0,
        "issues": ["el plan no tiene pasos"],
    }


def test_plan_no_lista_se_trata_como_vacio():
    resultado = eval_plan_quality(None, max_steps=3)
    assert resultado["score"] == 0.0
    assert resultado["issues"] == ["el plan no tiene pasos"]


def test_exceder_max_steps_penaliza():
    resultado = eval_plan_quality(_plan_limpio(), max_steps=2)
    assert resultado["score"] == 0.8
    assert resultado["issues"] == ["el plan tiene 3 pasos, más que max_steps=2"]


def test_instruccion_vacia_penaliza():
    plan = _plan_limpio()
    plan[1]["instruccion"] = "   "
    resultado = eval_plan_quality(plan, max_steps=3)
    assert resultado["score"] == 0.9
    assert resultado["issues"] == ["el paso 2 no tiene instrucción"]


def test_agente_faltante_penaliza():
    plan = [{"seq": 1, "instruccion": "investigar"}]
    resultado = eval_plan_quality(plan, max_steps=1)
    assert resultado["score"] == 0.9
    assert resultado["issues"] == ["el paso 1 no tiene agente"]


def test_depende_de_valido_no_genera_issue():
    resultado = eval_plan_quality(_plan_limpio(), max_steps=3)
    assert resultado["issues"] == []


def test_depende_de_auto_referencia_penaliza():
    plan = [
        {"agente": "a", "instruccion": "uno", "depende_de": [0]},
    ]
    resultado = eval_plan_quality(plan, max_steps=1)
    assert resultado["score"] == 0.95
    assert resultado["issues"] == ["el paso 1 referencia un índice inválido 0 en depende_de"]


def test_depende_de_referencia_a_futuro_penaliza():
    plan = [
        {"agente": "a", "instruccion": "uno", "depende_de": [1]},
        {"agente": "b", "instruccion": "dos", "depende_de": []},
    ]
    resultado = eval_plan_quality(plan, max_steps=2)
    assert resultado["score"] == 0.95
    assert resultado["issues"] == ["el paso 1 referencia un índice inválido 1 en depende_de"]


def test_depende_de_indice_negativo_o_no_entero_penaliza():
    plan = [
        {"agente": "a", "instruccion": "uno"},
        {"agente": "b", "instruccion": "dos", "depende_de": [-1]},
        {"agente": "c", "instruccion": "tres", "depende_de": [0, "x"]},
    ]
    resultado = eval_plan_quality(plan, max_steps=3)
    assert resultado["score"] == 0.9  # -0.05 -0.05
    assert "el paso 2 referencia un índice inválido -1 en depende_de" in resultado["issues"]
    assert "el paso 3 referencia un índice inválido 'x' en depende_de" in resultado["issues"]


def test_depende_de_bool_se_rechaza():
    plan = [
        {"agente": "a", "instruccion": "uno"},
        {"agente": "b", "instruccion": "dos", "depende_de": [True]},
    ]
    resultado = eval_plan_quality(plan, max_steps=2)
    assert "el paso 2 referencia un índice inválido True en depende_de" in resultado["issues"]


def test_depende_de_no_lista_penaliza():
    plan = [{"agente": "a", "instruccion": "uno", "depende_de": "0"}]
    resultado = eval_plan_quality(plan, max_steps=1)
    assert resultado["score"] == 0.95
    assert resultado["issues"] == ["el paso 1 tiene un depende_de inválido (no es lista)"]


def test_depende_de_None_se_trata_como_ausente():
    plan = [{"agente": "a", "instruccion": "uno", "depende_de": None}]
    resultado = eval_plan_quality(plan, max_steps=1)
    assert resultado["score"] == 1.0
    assert resultado["issues"] == []


def test_instrucciones_duplicadas_penalizan_por_repeticion():
    plan = [
        {"agente": "a", "instruccion": "investigar"},
        {"agente": "b", "instruccion": "investigar"},
        {"agente": "c", "instruccion": "investigar"},
    ]
    resultado = eval_plan_quality(plan, max_steps=3)
    assert resultado["score"] == 0.9  # 1.0 - 0.05*(3-1)
    assert len(resultado["issues"]) == 1
    assert resultado["issues"][0].startswith("instrucción duplicada entre los pasos 1, 2, 3")


def test_duplicados_son_insensibles_a_mayusculas_y_espacios():
    plan = [
        {"agente": "a", "instruccion": "Investigar"},
        {"agente": "b", "instruccion": "  investigar  "},
    ]
    resultado = eval_plan_quality(plan, max_steps=2)
    assert resultado["score"] == 0.95
    assert len(resultado["issues"]) == 1


def test_score_nunca_baja_de_cero():
    plan = [{"instruccion": ""} for _ in range(20)]
    resultado = eval_plan_quality(plan, max_steps=1)
    assert resultado["score"] == 0.0


def test_paso_no_dict_penaliza_fuerte():
    plan = ["no soy dict"]
    resultado = eval_plan_quality(plan, max_steps=1)
    assert resultado["score"] == 0.7
    assert resultado["issues"] == ["el paso 1 no es un dict"]


def test_max_steps_no_numerico_se_ignora_sin_lanzar():
    resultado = eval_plan_quality(_plan_limpio(), max_steps="x")
    assert resultado["score"] == 1.0
    assert resultado["issues"] == []
