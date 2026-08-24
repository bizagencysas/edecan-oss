"""Tests de `edecan_skills.compiler`: `compile_skill` (SKILL.md estructurado con las seis
secciones), `parse_compiled_skill` (round-trip), `secciones_faltantes` (validación de
secciones) y `bump_version` (versionado §209).
"""

from __future__ import annotations

from edecan_skills.compiler import (
    bump_version,
    compile_skill,
    parse_compiled_skill,
    secciones_faltantes,
)
from edecan_skills.installer import parse_skill_md

_NOMBRE = "Revisar Repo"
_DESCRIPCION = "Revisa un repositorio y resume su estado."
_INSTRUCCION = "Cuando diga revisar repo"
_HERRAMIENTAS = ["read", "grep", "bash"]
_PERMISOS = ["lectura_del_directorio"]
_SALIDA = "Un resumen en markdown con hallazgos y riesgos."


def _compilar(**overrides) -> str:
    args = {
        "instruccion": _INSTRUCCION,
        "nombre": _NOMBRE,
        "descripcion": _DESCRIPCION,
        "herramientas": _HERRAMIENTAS,
        "permisos": _PERMISOS,
        "output_format": _SALIDA,
    }
    args.update(overrides)
    return compile_skill(**args)


# ---------------------------------------------------------------------------
# compile_skill — estructura
# ---------------------------------------------------------------------------


def test_compile_skill_tiene_frontmatter_y_secciones():
    skill = _compilar()
    for titulo in (
        "## Trigger",
        "## Inputs",
        "## Workflow",
        "## Tools",
        "## Permissions",
        "## Output",
    ):
        assert titulo in skill
    assert skill.startswith("---\n")


def test_compile_skill_frontmatter_lo_lee_el_instalador():
    skill = _compilar()
    nombre, descripcion, version, _cuerpo = parse_skill_md(skill)
    assert nombre == _NOMBRE
    assert descripcion == _DESCRIPCION
    assert version == "1.0.0"


def test_compile_skill_no_deja_secciones_faltantes():
    assert secciones_faltantes(_compilar()) == []


def test_compile_skill_tools_y_permisos_como_bullets():
    skill = _compilar()
    assert "- read" in skill
    assert "- grep" in skill
    assert "- bash" in skill
    assert "- lectura_del_directorio" in skill


def test_compile_skill_sin_herramientas_ni_permisos():
    skill = _compilar(herramientas=[], permisos=[])
    assert "## Tools" in skill
    assert "## Permissions" in skill
    assert secciones_faltantes(skill) == []


def test_compile_skill_nombre_con_caracteres_yaml_se_cita():
    # Un `:` en el nombre rompería un frontmatter armado a mano; safe_dump debe citarlo.
    skill = _compilar(nombre="PDF: Ayuda")
    nombre, _, _, _ = parse_skill_md(skill)
    assert nombre == "PDF: Ayuda"


# ---------------------------------------------------------------------------
# parse_compiled_skill — round-trip
# ---------------------------------------------------------------------------


def test_parse_compiled_skill_round_trip():
    skill = _compilar()
    parsed = parse_compiled_skill(skill)
    assert parsed["nombre"] == _NOMBRE
    assert parsed["descripcion"] == _DESCRIPCION
    assert parsed["version"] == "1.0.0"
    assert parsed["trigger"] == _INSTRUCCION
    assert parsed["inputs"] == _DESCRIPCION
    assert parsed["tools"] == _HERRAMIENTAS
    assert parsed["permissions"] == _PERMISOS
    assert parsed["output"] == _SALIDA
    assert parsed["workflow"]  # no vacío, generado por el compilador


def test_parse_compiled_skill_es_permisivo_con_secciones_faltantes():
    skill = "---\nname: X\ndescription: Y\n---\n## Trigger\n\nalgo\n"
    parsed = parse_compiled_skill(skill)
    assert parsed["trigger"] == "algo"
    assert parsed["tools"] == []
    assert parsed["permissions"] == []
    assert parsed["output"] == ""
    assert set(secciones_faltantes(skill)) == {
        "inputs",
        "workflow",
        "tools",
        "permissions",
        "output",
    }


def test_parse_compiled_skill_tolera_bullets_sin_espacio():
    skill = (
        "---\nname: X\ndescription: Y\n---\n"
        "## Tools\n\n-a\n-b\n\n## Permissions\n\n-c\n\n## Output\n\nsalida\n"
    )
    parsed = parse_compiled_skill(skill)
    assert parsed["tools"] == ["a", "b"]
    assert parsed["permissions"] == ["c"]


def test_recompilar_lo_parseado_es_estable():
    # La salida del compilador, releída y vuelta a compilar con sus datos, es idéntica.
    skill = _compilar()
    parsed = parse_compiled_skill(skill)
    segunda = compile_skill(
        parsed["trigger"],
        nombre=parsed["nombre"],
        descripcion=parsed["descripcion"],
        herramientas=parsed["tools"],
        permisos=parsed["permissions"],
        output_format=parsed["output"],
    )
    assert segunda == skill


# ---------------------------------------------------------------------------
# bump_version (§209)
# ---------------------------------------------------------------------------


def test_bump_version_incrementa_parche():
    assert bump_version("1.0.0") == "1.0.1"
    assert bump_version("1.2.3") == "1.2.4"
    assert bump_version("0.9.9") == "0.9.10"
    assert bump_version("10.20.30") == "10.20.31"


def test_bump_version_none_da_1_0_0():
    assert bump_version(None) == "1.0.0"


def test_bump_version_no_semver_reinicia_a_1_0_0():
    assert bump_version("") == "1.0.0"
    assert bump_version("local-1") == "1.0.0"
    assert bump_version("1.0") == "1.0.0"
    assert bump_version("v1.0.0") == "1.0.0"
