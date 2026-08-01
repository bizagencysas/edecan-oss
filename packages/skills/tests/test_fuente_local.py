"""Tests de `edecan_skills.fuente_local` (Fuente #2: skill desde una carpeta local del Mac
del dueño): todo con `tmp_path` (carpetas temporales de pytest), nunca toca el disco real
del usuario. Cubre: carpeta con `SKILL.md` directo, ruta directa a un `.md` suelto, carpeta
con varias skills en subcarpetas, ruta inexistente, enlace simbólico que escapa de la
carpeta indicada, y el mismo tope de tamaño que las fuentes remotas.
"""

from __future__ import annotations

import os

import pytest
from edecan_skills.fuente_local import (
    RutaSkillInvalidaError,
    leer_skill_local,
    listar_skills_locales,
)
from edecan_skills.installer import (
    _MAX_BYTES as _MAX_SKILL_BYTES,
)
from edecan_skills.installer import (
    SkillDemasiadoGrandeError,
    parse_capabilities,
    parse_skill_md,
)

_SKILL_MD_PDF = "---\nname: PDF Helper\ndescription: Ayuda con PDFs.\n---\ncuerpo de la skill"


# ---------------------------------------------------------------------------
# leer_skill_local — carpeta con SKILL.md directo
# ---------------------------------------------------------------------------


def test_leer_skill_local_carpeta_con_skill_md_directo(tmp_path):
    carpeta = tmp_path / "pdf-helper"
    carpeta.mkdir()
    (carpeta / "SKILL.md").write_text(_SKILL_MD_PDF, encoding="utf-8")

    archivo = leer_skill_local(str(carpeta))

    assert archivo.texto == _SKILL_MD_PDF
    assert archivo.url.startswith("file://")
    assert str(carpeta / "SKILL.md") in archivo.url


def test_leer_skill_local_produce_un_skillfile_que_el_instalador_procesa_igual(tmp_path):
    """El pipeline de parseo existente (`installer.parse_skill_md`/`parse_capabilities`)
    debe funcionar sin ningún cambio sobre lo que devuelve `leer_skill_local` — la MISMA
    forma que produce `installer.fetch_skill()` para una fuente remota."""
    carpeta = tmp_path / "pdf-helper"
    carpeta.mkdir()
    (carpeta / "SKILL.md").write_text(
        "---\nname: PDF Helper\ndescription: Ayuda con PDFs.\n"
        "allowed-tools: [enviar_correo, usar_computadora]\n---\ncuerpo",
        encoding="utf-8",
    )

    archivo = leer_skill_local(str(carpeta))
    nombre, descripcion, version, cuerpo = parse_skill_md(archivo.texto)
    capabilities = parse_capabilities(archivo.texto)

    assert nombre == "PDF Helper"
    assert descripcion == "Ayuda con PDFs."
    assert version is None
    assert cuerpo == "cuerpo"
    assert capabilities == ["enviar_correo", "usar_computadora"]


# ---------------------------------------------------------------------------
# leer_skill_local — ruta directa a un .md suelto
# ---------------------------------------------------------------------------


def test_leer_skill_local_ruta_directa_a_md(tmp_path):
    archivo_md = tmp_path / "mi-skill.md"
    archivo_md.write_text(_SKILL_MD_PDF, encoding="utf-8")

    archivo = leer_skill_local(str(archivo_md))

    assert archivo.texto == _SKILL_MD_PDF


def test_leer_skill_local_ruta_directa_a_archivo_no_md_falla(tmp_path):
    archivo_txt = tmp_path / "notas.txt"
    archivo_txt.write_text("no es una skill", encoding="utf-8")

    with pytest.raises(RutaSkillInvalidaError):
        leer_skill_local(str(archivo_txt))


# ---------------------------------------------------------------------------
# leer_skill_local — errores claros y accionables
# ---------------------------------------------------------------------------


def test_leer_skill_local_ruta_inexistente_lanza_error_claro(tmp_path):
    ruta = tmp_path / "no-existe"

    with pytest.raises(RutaSkillInvalidaError, match="no existe"):
        leer_skill_local(str(ruta))


def test_leer_skill_local_ruta_vacia_lanza_error_claro():
    with pytest.raises(RutaSkillInvalidaError):
        leer_skill_local("   ")


def test_leer_skill_local_carpeta_sin_skill_md_lanza_error_claro(tmp_path):
    carpeta = tmp_path / "carpeta-vacia"
    carpeta.mkdir()

    with pytest.raises(RutaSkillInvalidaError, match="SKILL.md"):
        leer_skill_local(str(carpeta))


def test_leer_skill_local_sin_permisos_lanza_error_claro(tmp_path):
    if os.getuid() == 0:
        pytest.skip("root ignora los permisos de archivo, no se puede probar esta ruta.")

    carpeta = tmp_path / "sin-permisos"
    carpeta.mkdir()
    skill_md = carpeta / "SKILL.md"
    skill_md.write_text(_SKILL_MD_PDF, encoding="utf-8")
    skill_md.chmod(0o000)

    try:
        with pytest.raises(RutaSkillInvalidaError, match="[Pp]ermiso"):
            leer_skill_local(str(carpeta))
    finally:
        skill_md.chmod(0o644)  # limpieza: pytest necesita poder borrar tmp_path después


def test_leer_skill_local_supera_el_tope_de_tamano(tmp_path):
    carpeta = tmp_path / "gigante"
    carpeta.mkdir()
    (carpeta / "SKILL.md").write_text("x" * (_MAX_SKILL_BYTES + 1), encoding="utf-8")

    with pytest.raises(SkillDemasiadoGrandeError):
        leer_skill_local(str(carpeta))


# ---------------------------------------------------------------------------
# leer_skill_local / listar_skills_locales — enlace simbólico fuera de la carpeta
# ---------------------------------------------------------------------------


def test_leer_skill_local_rechaza_symlink_a_skill_md_fuera_de_la_carpeta(tmp_path):
    afuera = tmp_path / "afuera"
    afuera.mkdir()
    (afuera / "SKILL.md").write_text(_SKILL_MD_PDF, encoding="utf-8")

    carpeta = tmp_path / "adentro"
    carpeta.mkdir()
    (carpeta / "SKILL.md").symlink_to(afuera / "SKILL.md")

    with pytest.raises(RutaSkillInvalidaError, match="enlace simbólico"):
        leer_skill_local(str(carpeta))


def test_listar_skills_locales_ignora_subcarpeta_symlink_fuera_de_la_base(tmp_path):
    afuera = tmp_path / "afuera"
    afuera.mkdir()
    (afuera / "SKILL.md").write_text(_SKILL_MD_PDF, encoding="utf-8")

    coleccion = tmp_path / "mis-skills"
    coleccion.mkdir()
    (coleccion / "escapada").symlink_to(afuera)

    buena = coleccion / "buena"
    buena.mkdir()
    (buena / "SKILL.md").write_text("---\nname: Buena\n---\ncuerpo", encoding="utf-8")

    resultados = listar_skills_locales(str(coleccion))

    assert len(resultados) == 1
    assert resultados[0].nombre == "Buena"


# ---------------------------------------------------------------------------
# listar_skills_locales
# ---------------------------------------------------------------------------


def test_listar_skills_locales_una_sola_skill_carpeta_directa(tmp_path):
    carpeta = tmp_path / "pdf-helper"
    carpeta.mkdir()
    (carpeta / "SKILL.md").write_text(_SKILL_MD_PDF, encoding="utf-8")

    resultados = listar_skills_locales(str(carpeta))

    assert len(resultados) == 1
    assert resultados[0].nombre == "PDF Helper"
    assert resultados[0].descripcion == "Ayuda con PDFs."
    assert resultados[0].source == str(carpeta)
    assert resultados[0].installs is None


def test_listar_skills_locales_una_sola_skill_md_suelto(tmp_path):
    archivo_md = tmp_path / "mi-skill.md"
    archivo_md.write_text(_SKILL_MD_PDF, encoding="utf-8")

    resultados = listar_skills_locales(str(archivo_md))

    assert len(resultados) == 1
    assert resultados[0].source == str(archivo_md)


def test_listar_skills_locales_varias_subcarpetas(tmp_path):
    coleccion = tmp_path / "mis-skills"
    coleccion.mkdir()

    carpeta1 = coleccion / "pdf-helper"
    carpeta1.mkdir()
    (carpeta1 / "SKILL.md").write_text(_SKILL_MD_PDF, encoding="utf-8")

    carpeta2 = coleccion / "spreadsheet-wizard"
    carpeta2.mkdir()
    (carpeta2 / "SKILL.md").write_text(
        "---\nname: Spreadsheet Wizard\ndescription: Hojas de cálculo.\n---\ncuerpo2",
        encoding="utf-8",
    )

    # Subcarpeta sin SKILL.md — se ignora, no cuenta como una tercera skill.
    (coleccion / "solo-notas").mkdir()
    (coleccion / "solo-notas" / "notas.txt").write_text("no es una skill", encoding="utf-8")

    resultados = listar_skills_locales(str(coleccion))

    nombres = {r.nombre for r in resultados}
    assert nombres == {"PDF Helper", "Spreadsheet Wizard"}
    fuentes = {r.source for r in resultados}
    assert fuentes == {str(carpeta1), str(carpeta2)}


def test_listar_skills_locales_respeta_k(tmp_path):
    coleccion = tmp_path / "mis-skills"
    coleccion.mkdir()
    for i in range(3):
        carpeta = coleccion / f"skill-{i}"
        carpeta.mkdir()
        (carpeta / "SKILL.md").write_text(f"---\nname: Skill {i}\n---\ncuerpo", encoding="utf-8")

    resultados = listar_skills_locales(str(coleccion), k=2)

    assert len(resultados) == 2


def test_listar_skills_locales_ruta_inexistente_lanza_error_claro(tmp_path):
    ruta = tmp_path / "no-existe"

    with pytest.raises(RutaSkillInvalidaError, match="no existe"):
        listar_skills_locales(str(ruta))


def test_listar_skills_locales_carpeta_sin_ninguna_skill_lanza_error_claro(tmp_path):
    coleccion = tmp_path / "vacia"
    coleccion.mkdir()
    (coleccion / "solo-notas").mkdir()
    (coleccion / "solo-notas" / "notas.txt").write_text("nada", encoding="utf-8")

    with pytest.raises(RutaSkillInvalidaError, match="SKILL.md"):
        listar_skills_locales(str(coleccion))


def test_listar_skills_locales_usa_nombre_de_carpeta_si_falta_frontmatter(tmp_path):
    carpeta = tmp_path / "sin-frontmatter"
    carpeta.mkdir()
    (carpeta / "SKILL.md").write_text("solo texto plano, sin frontmatter", encoding="utf-8")

    resultados = listar_skills_locales(str(carpeta))

    assert resultados[0].nombre == "sin-frontmatter"
