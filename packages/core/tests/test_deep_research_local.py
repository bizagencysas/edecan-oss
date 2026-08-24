from edecan_core.deep_research import is_local_search_question, local_search_subquestions


def test_busqueda_local_exige_plan_comparativo() -> None:
    question = "Busca una barbería cerca de Chacao"

    assert is_local_search_question(question)
    plan = local_search_subquestions(question)
    assert len(plan) == 4
    assert any("Horarios" in item for item in plan)
    assert any("Comparación" in item for item in plan)


def test_pregunta_general_no_se_clasifica_como_local() -> None:
    assert not is_local_search_question("Explica cómo funciona la memoria semántica")
