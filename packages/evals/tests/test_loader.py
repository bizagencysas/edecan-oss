"""Tests de `edecan_evals.loader` — parsea las suites reales de
`packages/evals/suites/` y valida el manejo de errores, sin dependencias de
paquetes hermanos."""

from __future__ import annotations

from pathlib import Path

import pytest
from edecan_evals import loader
from edecan_evals.schema import NOMBRES_HERRAMIENTAS_TOOLKIT
from pydantic import ValidationError

SUITES_ESPERADAS = {
    "tool_choice",
    "persona_consistencia",
    "memoria",
    "seguridad_prompt_injection",
    "sin_linkedin",
    "perfil_vivo",  # WP-V2-13, ver packages/evals/suites/perfil_vivo.yaml
}


def test_listar_suites_incluye_todas_las_reales() -> None:
    assert set(loader.listar_suites()) == SUITES_ESPERADAS


@pytest.mark.parametrize("nombre", sorted(SUITES_ESPERADAS))
def test_cargar_cada_suite_real(nombre: str) -> None:
    suite = loader.cargar_suite(nombre)
    assert suite.nombre == nombre
    assert len(suite.casos) >= 1
    # Cada id de caso es único dentro de la suite.
    ids = [caso.id for caso in suite.casos]
    assert len(ids) == len(set(ids))


def test_tool_choice_tiene_8_casos_y_tools_pinned() -> None:
    suite = loader.cargar_suite("tool_choice")
    assert len(suite.casos) == 8
    for caso in suite.casos:
        assert caso.esperado.tools_llamadas is not None
        for nombre_tool in caso.esperado.tools_llamadas:
            assert nombre_tool in NOMBRES_HERRAMIENTAS_TOOLKIT


def test_memoria_es_multi_turno() -> None:
    suite = loader.cargar_suite("memoria")
    assert all(len(caso.mensajes) >= 2 for caso in suite.casos)


def test_sin_linkedin_todos_rechazan_y_no_llaman_tools() -> None:
    suite = loader.cargar_suite("sin_linkedin")
    for caso in suite.casos:
        assert caso.esperado.rechaza is True
        assert caso.esperado.tools_llamadas == []


def test_cargar_suite_inexistente_lanza_file_not_found() -> None:
    with pytest.raises(FileNotFoundError):
        loader.cargar_suite("no_existe_esta_suite")


def test_cargar_suite_malformada_lanza_validation_error(tmp_path: Path) -> None:
    (tmp_path / "rota.yaml").write_text("nombre: rota\ncasos: []\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        loader.cargar_suite("rota", directorio=tmp_path)


def test_cargar_suite_raiz_no_es_mapeo_lanza_value_error(tmp_path: Path) -> None:
    (tmp_path / "lista.yaml").write_text("- 1\n- 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapeo YAML"):
        loader.cargar_suite("lista", directorio=tmp_path)


def test_cargar_suite_directorio_alterno(tmp_path: Path) -> None:
    (tmp_path / "mini.yaml").write_text(
        'nombre: mini\ncasos:\n  - id: c1\n    mensajes: ["hola"]\n    esperado: {}\n',
        encoding="utf-8",
    )
    suite = loader.cargar_suite("mini", directorio=tmp_path)
    assert suite.nombre == "mini"
    assert suite.casos[0].id == "c1"


def test_cargar_todas_directorio_alterno(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text(
        'nombre: a\ncasos:\n  - id: c1\n    mensajes: ["hola"]\n    esperado: {}\n',
        encoding="utf-8",
    )
    (tmp_path / "b.yml").write_text(
        'nombre: b\ncasos:\n  - id: c1\n    mensajes: ["hola"]\n    esperado: {}\n',
        encoding="utf-8",
    )
    todas = loader.cargar_todas(directorio=tmp_path)
    assert set(todas) == {"a", "b"}
