"""Tests de `edecan_skills.testing`: `validate_skill_structure` (gate estricto previo a
instalar), `test_skill_triggers` (prueba de disparo determinista) y `smoke_test_skill`
(smoke test de estructura + secciones).
"""

from __future__ import annotations

from edecan_skills import testing as testing_mod
from edecan_skills.compiler import compile_skill

# `test_skill_triggers` se importa vía el módulo (no el nombre suelto) a propósito: si se
# importara como `test_skill_triggers`, pytest lo recolectaría como un caso de test más
# (todo nombre `test_*` en el namespace del test es candidato), y su firma
# `(skill_md, sample_inputs)` no es una firma de test — se referencian como
# `testing_mod.<función>`.
smoke_test_skill = testing_mod.smoke_test_skill
validate_skill_structure = testing_mod.validate_skill_structure


def _skill_valida() -> str:
    return compile_skill(
        "Cuando diga revisar repo",
        nombre="Revisar Repo",
        descripcion="Revisa un repositorio y resume su estado.",
        herramientas=["read", "grep"],
        permisos=["lectura_del_directorio"],
        output_format="Un resumen en markdown.",
    )


# ---------------------------------------------------------------------------
# validate_skill_structure
# ---------------------------------------------------------------------------


def test_validate_skill_structure_valida_da_vacio():
    assert validate_skill_structure(_skill_valida()) == []


def test_validate_skill_structure_falta_name():
    skill = "---\ndescription: Solo descripción.\n---\ncuerpo\n"
    problemas = validate_skill_structure(skill)
    assert any("name" in p for p in problemas)


def test_validate_skill_structure_falta_description():
    skill = "---\nname: pdf-helper\n---\ncuerpo\n"
    problemas = validate_skill_structure(skill)
    assert any("description" in p for p in problemas)


def test_validate_skill_structure_falta_frontmatter():
    problemas = validate_skill_structure("texto plano sin frontmatter\n")
    assert any("frontmatter" in p for p in problemas)


def test_validate_skill_structure_cuerpo_vacio():
    skill = "---\nname: x\ndescription: y\n---\n"
    problemas = validate_skill_structure(skill)
    assert any("cuerpo" in p for p in problemas)


def test_validate_skill_structure_caracteres_de_control():
    skill = "---\nname: x\ndescription: y\n---\ncuerpo\x00con\x07basura\n"
    problemas = validate_skill_structure(skill)
    assert any("control" in p for p in problemas)


def test_validate_skill_structure_linea_demasiado_larga():
    skill = "---\nname: x\ndescription: y\n---\n" + ("a" * 300) + "\n"
    problemas = validate_skill_structure(skill)
    assert any("demasiado larga" in p for p in problemas)


def test_validate_skill_structure_nombre_solo_simbolos():
    skill = '---\nname: "!!! ???"\ndescription: y\n---\ncuerpo\n'
    problemas = validate_skill_structure(skill)
    assert any("slug" in p for p in problemas)


def test_validate_skill_structure_nombre_humano_es_valido():
    # "PDF Helper" no es un slug, pero produce uno no vacío → válido (mismo criterio
    # que `installer._validar_nombre_produce_slug`).
    skill = "---\nname: PDF Helper\ndescription: y\n---\ncuerpo\n"
    assert validate_skill_structure(skill) == []


# ---------------------------------------------------------------------------
# test_skill_triggers
# ---------------------------------------------------------------------------


def test_test_skill_triggers_activa_con_palabra_clave():
    resultados = testing_mod.test_skill_triggers(
        _skill_valida(), ["por favor revisa este repo", "otra cosa sin relación"]
    )
    assert resultados[0] is True
    assert resultados[1] is False


def test_test_skill_triggers_sin_trigger_todo_false():
    skill = "---\nname: x\ndescription: y\n---\n## Output\n\nsalida\n"
    assert testing_mod.test_skill_triggers(skill, ["revisar repo"]) == [False]


def test_test_skill_triggers_es_case_insensitive():
    assert testing_mod.test_skill_triggers(_skill_valida(), ["REVISAR REPO"]) == [True]


# ---------------------------------------------------------------------------
# smoke_test_skill
# ---------------------------------------------------------------------------


def test_smoke_test_skill_ok():
    assert smoke_test_skill(_skill_valida()) == {"ok": True, "problems": []}


def test_smoke_test_skill_rota_por_seccion_faltante():
    skill = "---\nname: x\ndescription: y\n---\n## Trigger\n\nalgo\n"
    resultado = smoke_test_skill(skill)
    assert resultado["ok"] is False
    assert any("sección" in p for p in resultado["problems"])


def test_smoke_test_skill_rota_por_frontmatter():
    resultado = smoke_test_skill("texto plano sin frontmatter\n")
    assert resultado["ok"] is False
    assert any("frontmatter" in p for p in resultado["problems"])