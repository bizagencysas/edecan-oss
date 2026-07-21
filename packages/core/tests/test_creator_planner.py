from __future__ import annotations

import pytest
from edecan_core.creator_planner import (
    derive_creation_title,
    detect_artifact_kinds,
    normalize_artifact_kind,
    plan_creation,
)


def test_detecta_solicitud_compuesta_en_el_orden_del_usuario() -> None:
    kinds = detect_artifact_kinds(
        "Crea un post, un documento Word, un PDF, una presentación, "
        "una página web y una app completa."
    )
    assert kinds == ["post", "docx", "pdf", "pptx", "website", "app"]


def test_documento_pdf_es_un_solo_formato_pero_documento_y_pdf_son_dos() -> None:
    assert detect_artifact_kinds("Genera un documento PDF") == ["pdf"]
    assert detect_artifact_kinds("Genera un documento y un PDF") == ["docx", "pdf"]


def test_formatos_explicitos_aceptan_aliases_y_eliminan_duplicados() -> None:
    plan = plan_creation(
        "Material del lanzamiento",
        requested_formats=["Word", "docx", "PowerPoint", "sitio_web"],
        title="Lanzamiento 2027",
    )
    assert [item.kind for item in plan.deliverables] == ["docx", "pptx", "website"]
    assert plan.title == "Lanzamiento 2027"


def test_formato_desconocido_falla_en_vez_de_prometerlo() -> None:
    with pytest.raises(ValueError, match="Formato no soportado"):
        normalize_artifact_kind("archivo mágico")


def test_redaccion_sin_formato_cae_a_post_privado() -> None:
    plan = plan_creation("Escribe algo claro sobre el lanzamiento")
    assert [item.kind for item in plan.deliverables] == ["post"]


def test_titulo_derivado_es_estable_y_sin_imperativo() -> None:
    assert derive_creation_title("Por favor créame una página web para Café Norte") == (
        "Una página web para Café Norte"
    )
