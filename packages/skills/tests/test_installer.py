"""Tests de `edecan_skills.installer`: `parse_source` (casos válidos + inyecciones
rechazadas), `fetch_skill` (fallbacks entre rutas candidatas + cap de tamaño),
`parse_skill_md` (frontmatter con/sin YAML), `parse_capabilities` (campo `allowed-tools`,
WP-V5-04), la validación nombre->slug, y `install_from_source` (pipeline completo).
"""

from __future__ import annotations

import httpx
import pytest
import respx
from edecan_skills.installer import (
    MAX_DESCRIPTION_LENGTH,
    MAX_NAME_LENGTH,
    FuenteInvalidaError,
    SkillDemasiadoGrandeError,
    SkillNoEncontradaError,
    fetch_skill,
    install_from_source,
    parse_capabilities,
    parse_skill_md,
    parse_source,
)


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


# ---------------------------------------------------------------------------
# parse_source — casos válidos
# ---------------------------------------------------------------------------


def test_parse_source_owner_repo():
    assert parse_source("acme/pdf-helper") == ("acme", "pdf-helper", None)


def test_parse_source_owner_repo_subpath():
    assert parse_source("acme/repo/sub/path") == ("acme", "repo", "sub/path")


def test_parse_source_ignora_barras_extra():
    assert parse_source("acme/repo/") == ("acme", "repo", None)
    assert parse_source("  acme/repo  ") == ("acme", "repo", None)


def test_parse_source_url_github_simple():
    assert parse_source("https://github.com/acme/repo") == ("acme", "repo", None)


def test_parse_source_url_github_con_tree_branch_y_path():
    owner, repo, subpath = parse_source("https://github.com/acme/repo/tree/main/skills/foo")
    assert (owner, repo) == ("acme", "repo")
    assert subpath == "skills/foo"


def test_parse_source_url_github_con_tree_sin_path_extra():
    owner, repo, subpath = parse_source("https://github.com/acme/repo/tree/main")
    assert (owner, repo, subpath) == ("acme", "repo", None)


def test_parse_source_url_skills_sh():
    assert parse_source("https://skills.sh/acme/repo") == ("acme", "repo", None)


def test_parse_source_url_con_query_o_fragment_se_ignora():
    assert parse_source("https://github.com/acme/repo?tab=readme") == ("acme", "repo", None)


def test_parse_source_nombres_con_puntos_y_guiones_validos():
    assert parse_source("acme-corp/my.repo_name") == ("acme-corp", "my.repo_name", None)


# ---------------------------------------------------------------------------
# parse_source — inyecciones/formas inválidas rechazadas
# ---------------------------------------------------------------------------


def test_parse_source_vacio_rechazado():
    with pytest.raises(FuenteInvalidaError):
        parse_source("")


def test_parse_source_sin_slash_rechazado():
    with pytest.raises(FuenteInvalidaError):
        parse_source("solo-un-nombre")


@pytest.mark.parametrize(
    "source",
    [
        "../etc/passwd",
        "acme/..",
        "../../acme/repo",
        "acme/repo/../../../etc/passwd",
        "./acme/repo",
    ],
)
def test_parse_source_rechaza_path_traversal(source: str):
    with pytest.raises(FuenteInvalidaError):
        parse_source(source)


@pytest.mark.parametrize(
    "source",
    [
        "acme repo/x",  # espacio
        "acme/re po",  # espacio
        "acme;rm -rf/repo",  # carácter de shell
        "acme/re\npo",  # salto de línea embebido (uno solo al final se recorta con .strip())
    ],
)
def test_parse_source_rechaza_caracteres_invalidos(source: str):
    with pytest.raises(FuenteInvalidaError):
        parse_source(source)


def test_parse_source_rechaza_host_no_permitido():
    with pytest.raises(FuenteInvalidaError):
        parse_source("https://evil.example.com/acme/repo")


def test_parse_source_rechaza_host_con_subdominio_similar():
    # anti-suffix-spoofing: "github.com.evil.com" NO es "github.com".
    with pytest.raises(FuenteInvalidaError):
        parse_source("https://github.com.evil.com/acme/repo")


def test_parse_source_rechaza_userinfo_spoofing():
    # `urlparse` ya descarta el userinfo antes del '@': el host real es evil.com.
    with pytest.raises(FuenteInvalidaError):
        parse_source("https://github.com@evil.com/acme/repo")


def test_parse_source_rechaza_esquema_no_http():
    with pytest.raises(FuenteInvalidaError):
        parse_source("ftp://github.com/acme/repo")


def test_parse_source_url_sin_owner_repo_rechazada():
    with pytest.raises(FuenteInvalidaError):
        parse_source("https://github.com/acme")


def test_parse_source_nunca_devuelve_una_url_para_pedir():
    # Documenta la garantía anti-SSRF central: el resultado son 3 strings simples
    # (owner, repo, subpath), nunca algo que un caller pudiera pasar a httpx directo.
    resultado = parse_source("https://skills.sh/acme/repo")
    assert all(isinstance(v, str) or v is None for v in resultado)
    assert "://" not in "".join(v or "" for v in resultado)


# ---------------------------------------------------------------------------
# fetch_skill — fallbacks entre rutas candidatas
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_skill_primera_ruta_200(http):
    respx.get("https://raw.githubusercontent.com/acme/repo/HEAD/SKILL.md").mock(
        return_value=httpx.Response(200, text="---\nname: x\n---\ncuerpo")
    )
    archivo = await fetch_skill("acme", "repo", None, http)
    assert archivo.texto == "---\nname: x\n---\ncuerpo"
    assert archivo.url.endswith("/acme/repo/HEAD/SKILL.md")


@respx.mock
async def test_fetch_skill_con_subpath_prueba_prefijo_subpath_primero(http):
    respx.get("https://raw.githubusercontent.com/acme/repo/HEAD/sub/path/SKILL.md").mock(
        return_value=httpx.Response(200, text="contenido")
    )
    archivo = await fetch_skill("acme", "repo", "sub/path", http)
    assert archivo.url == "https://raw.githubusercontent.com/acme/repo/HEAD/sub/path/SKILL.md"


@respx.mock
async def test_fetch_skill_id_de_indice_resuelve_monorepo_skills(http):
    respx.get("https://raw.githubusercontent.com/anthropics/skills/HEAD/pdf/SKILL.md").mock(
        return_value=httpx.Response(404)
    )
    respx.get("https://raw.githubusercontent.com/anthropics/skills/HEAD/skills/pdf/SKILL.md").mock(
        return_value=httpx.Response(200, text="---\nname: pdf\n---\ncontenido")
    )

    archivo = await fetch_skill("anthropics", "skills", "pdf", http)

    assert archivo.url.endswith("/skills/pdf/SKILL.md")


@respx.mock
async def test_fetch_skill_id_openai_resuelve_coleccion_curada(http):
    base = "https://raw.githubusercontent.com/openai/skills/HEAD"
    for ruta in (
        "pdf/SKILL.md",
        "skills/pdf/SKILL.md",
        ".claude/skills/pdf/SKILL.md",
        ".agents/skills/pdf/SKILL.md",
    ):
        respx.get(f"{base}/{ruta}").mock(return_value=httpx.Response(404))
    respx.get(f"{base}/skills/.curated/pdf/SKILL.md").mock(
        return_value=httpx.Response(200, text="---\nname: pdf\n---\ncontenido")
    )

    archivo = await fetch_skill("openai", "skills", "pdf", http)

    assert archivo.url.endswith("/skills/.curated/pdf/SKILL.md")


@respx.mock
async def test_fetch_skill_404_en_primera_cae_a_skills_repo(http):
    respx.get("https://raw.githubusercontent.com/acme/repo/HEAD/SKILL.md").mock(
        return_value=httpx.Response(404)
    )
    respx.get("https://raw.githubusercontent.com/acme/repo/HEAD/skills/repo/SKILL.md").mock(
        return_value=httpx.Response(200, text="contenido 2")
    )
    archivo = await fetch_skill("acme", "repo", None, http)
    assert archivo.texto == "contenido 2"
    assert archivo.url.endswith("/skills/repo/SKILL.md")


@respx.mock
async def test_fetch_skill_404_en_las_dos_primeras_cae_a_skill(http):
    respx.get("https://raw.githubusercontent.com/acme/repo/HEAD/SKILL.md").mock(
        return_value=httpx.Response(404)
    )
    respx.get("https://raw.githubusercontent.com/acme/repo/HEAD/skills/repo/SKILL.md").mock(
        return_value=httpx.Response(404)
    )
    respx.get("https://raw.githubusercontent.com/acme/repo/HEAD/skill/SKILL.md").mock(
        return_value=httpx.Response(200, text="contenido 3")
    )
    archivo = await fetch_skill("acme", "repo", None, http)
    assert archivo.texto == "contenido 3"
    assert archivo.url.endswith("/skill/SKILL.md")


@respx.mock
async def test_fetch_skill_404_en_las_tres_lanza_no_encontrada(http):
    for ruta in ("SKILL.md", "skills/repo/SKILL.md", "skill/SKILL.md"):
        respx.get(f"https://raw.githubusercontent.com/acme/repo/HEAD/{ruta}").mock(
            return_value=httpx.Response(404)
        )
    with pytest.raises(SkillNoEncontradaError):
        await fetch_skill("acme", "repo", None, http)


@respx.mock
async def test_fetch_skill_error_de_red_en_primera_continua_a_la_siguiente(http):
    respx.get("https://raw.githubusercontent.com/acme/repo/HEAD/SKILL.md").mock(
        side_effect=httpx.ConnectError("caído")
    )
    respx.get("https://raw.githubusercontent.com/acme/repo/HEAD/skills/repo/SKILL.md").mock(
        return_value=httpx.Response(200, text="ok")
    )
    archivo = await fetch_skill("acme", "repo", None, http)
    assert archivo.texto == "ok"


# --- cap de tamaño ----------------------------------------------------------


@respx.mock
async def test_fetch_skill_cap_de_tamano_lanza_error_claro(http):
    demasiado_grande = "x" * 200_001
    respx.get("https://raw.githubusercontent.com/acme/repo/HEAD/SKILL.md").mock(
        return_value=httpx.Response(200, text=demasiado_grande)
    )
    with pytest.raises(SkillDemasiadoGrandeError):
        await fetch_skill("acme", "repo", None, http)


@respx.mock
async def test_fetch_skill_justo_en_el_limite_no_lanza(http):
    justo = "x" * 200_000
    respx.get("https://raw.githubusercontent.com/acme/repo/HEAD/SKILL.md").mock(
        return_value=httpx.Response(200, text=justo)
    )
    archivo = await fetch_skill("acme", "repo", None, http)
    assert len(archivo.texto) == 200_000


# ---------------------------------------------------------------------------
# parse_skill_md
# ---------------------------------------------------------------------------


def test_parse_skill_md_con_frontmatter_completo():
    texto = (
        "---\n"
        "name: Mi Skill\n"
        "description: Hace cosas útiles.\n"
        "version: 1.2.3\n"
        "license: MIT\n"
        "---\n"
        "# Instrucciones\n"
        "Haz esto y aquello.\n"
    )
    nombre, descripcion, version, cuerpo = parse_skill_md(texto)
    assert nombre == "Mi Skill"
    assert descripcion == "Hace cosas útiles."
    assert version == "1.2.3"
    assert cuerpo.startswith("# Instrucciones")


def test_parse_skill_md_sin_frontmatter_usa_primera_linea_como_descripcion():
    texto = "\n\nEsta es la primera línea real.\nSegunda línea.\n"
    nombre, descripcion, version, cuerpo = parse_skill_md(texto)
    assert nombre == ""  # el llamador (install_from_source) hace nombre=repo
    assert descripcion == "Esta es la primera línea real."
    assert version is None
    assert "Segunda línea." in cuerpo


def test_parse_skill_md_frontmatter_sin_description_completa_con_primera_linea_del_cuerpo():
    texto = "---\nname: X\n---\nPrimera línea del cuerpo.\n"
    nombre, descripcion, version, cuerpo = parse_skill_md(texto)
    assert nombre == "X"
    assert descripcion == "Primera línea del cuerpo."


def test_parse_skill_md_frontmatter_yaml_invalido_degrada_a_cuerpo_plano():
    texto = "---\nname: [sin cerrar\n---\ncuerpo\n"
    nombre, descripcion, version, cuerpo = parse_skill_md(texto)
    assert nombre == ""
    assert version is None
    # Todo el texto original (incluido el delimitador) queda como cuerpo plano.
    assert "cuerpo" in cuerpo


def test_parse_skill_md_frontmatter_que_no_es_un_dict_se_ignora():
    texto = "---\n- solo\n- una\n- lista\n---\ncuerpo real\n"
    nombre, descripcion, version, cuerpo = parse_skill_md(texto)
    assert nombre == ""
    assert descripcion == "cuerpo real"


def test_parse_skill_md_frontmatter_nunca_cerrado_se_trata_como_cuerpo_plano():
    texto = "---\nname: X\nsin cerrar el bloque\nmás texto\n"
    nombre, descripcion, version, cuerpo = parse_skill_md(texto)
    assert nombre == ""


def test_parse_skill_md_sin_version_es_none():
    texto = "---\nname: X\ndescription: Y\n---\ncuerpo\n"
    _, _, version, _ = parse_skill_md(texto)
    assert version is None


def test_parse_skill_md_sanitiza_caracteres_de_control():
    texto = "---\nname: X\n---\ncuerpo\x00con\x07basura\ty\nsalto\n"
    _, _, _, cuerpo = parse_skill_md(texto)
    assert "\x00" not in cuerpo
    assert "\x07" not in cuerpo
    assert "\t" in cuerpo  # tab se preserva
    assert "\n" in cuerpo  # newline se preserva


# ---------------------------------------------------------------------------
# parse_capabilities — campo `allowed-tools` (WP-V5-04)
# ---------------------------------------------------------------------------


def test_parse_capabilities_lista_yaml():
    texto = "---\nname: x\nallowed-tools: [enviar_correo, usar_computadora]\n---\ncuerpo"
    assert parse_capabilities(texto) == ["enviar_correo", "usar_computadora"]


def test_parse_capabilities_string_separado_por_comas():
    texto = '---\nname: x\nallowed-tools: "enviar_correo, usar_computadora"\n---\ncuerpo'
    assert parse_capabilities(texto) == ["enviar_correo", "usar_computadora"]


def test_parse_capabilities_normaliza_a_snake_case_minusculas():
    texto = "---\nname: x\nallowed-tools: [Enviar Correo, usar-computadora]\n---\ncuerpo"
    assert parse_capabilities(texto) == ["enviar_correo", "usar_computadora"]


def test_parse_capabilities_deduplica_preservando_orden():
    texto = "---\nname: x\nallowed-tools: [a, b, a]\n---\ncuerpo"
    assert parse_capabilities(texto) == ["a", "b"]


def test_parse_capabilities_campo_ausente_es_vacio():
    assert parse_capabilities("---\nname: x\n---\ncuerpo") == []


def test_parse_capabilities_sin_frontmatter_es_vacio():
    assert parse_capabilities("texto plano sin frontmatter") == []


def test_parse_capabilities_frontmatter_invalido_es_vacio():
    assert parse_capabilities("---\nname: [sin cerrar\n---\ncuerpo") == []


def test_parse_capabilities_tipo_inesperado_es_vacio():
    # `allowed-tools` como número (ni lista ni string) — se ignora, no lanza.
    assert parse_capabilities("---\nname: x\nallowed-tools: 42\n---\ncuerpo") == []


def test_parse_capabilities_items_vacios_se_descartan():
    texto = '---\nname: x\nallowed-tools: "enviar_correo, , usar_computadora"\n---\ncuerpo'
    assert parse_capabilities(texto) == ["enviar_correo", "usar_computadora"]


# ---------------------------------------------------------------------------
# Límites de nombre/descripción y validación nombre -> slug (WP-V5-04)
# ---------------------------------------------------------------------------


def test_max_name_length_es_64():
    assert MAX_NAME_LENGTH == 64


def test_max_description_length_es_1024():
    assert MAX_DESCRIPTION_LENGTH == 1024


@respx.mock
async def test_install_from_source_trunca_nombre_demasiado_largo(http):
    nombre_largo = "x" * 100
    respx.get("https://raw.githubusercontent.com/acme/repo/HEAD/SKILL.md").mock(
        return_value=httpx.Response(200, text=f"---\nname: {nombre_largo}\n---\ncuerpo")
    )
    instalada = await install_from_source("acme/repo", http=http)
    assert len(instalada.nombre) == MAX_NAME_LENGTH


@respx.mock
async def test_install_from_source_trunca_descripcion_demasiado_larga(http):
    descripcion_larga = "y" * 2000
    respx.get("https://raw.githubusercontent.com/acme/repo/HEAD/SKILL.md").mock(
        return_value=httpx.Response(
            200, text=f"---\nname: x\ndescription: {descripcion_larga}\n---\ncuerpo"
        )
    )
    instalada = await install_from_source("acme/repo", http=http)
    assert len(instalada.descripcion) == MAX_DESCRIPTION_LENGTH


@respx.mock
async def test_install_from_source_nombre_ya_slug_se_preserva_tal_cual(http):
    respx.get("https://raw.githubusercontent.com/acme/pdf-helper/HEAD/SKILL.md").mock(
        return_value=httpx.Response(200, text="---\nname: pdf-helper\n---\ncuerpo")
    )
    instalada = await install_from_source("acme/pdf-helper", http=http)
    assert instalada.nombre == "pdf-helper"


@respx.mock
async def test_install_from_source_nombre_humano_se_acepta_sin_slugificar_todavia(http):
    # `install_from_source` NO slugifica `nombre` (eso lo hace `store.slugify` al
    # persistir) — solo valida que SÍ produciría un slug no vacío.
    respx.get("https://raw.githubusercontent.com/acme/repo/HEAD/SKILL.md").mock(
        return_value=httpx.Response(200, text="---\nname: PDF Helper\n---\ncuerpo")
    )
    instalada = await install_from_source("acme/repo", http=http)
    assert instalada.nombre == "PDF Helper"


@respx.mock
async def test_install_from_source_nombre_solo_simbolos_rechazado(http):
    respx.get("https://raw.githubusercontent.com/acme/repo/HEAD/SKILL.md").mock(
        return_value=httpx.Response(200, text='---\nname: "!!! ??? ..."\n---\ncuerpo')
    )
    with pytest.raises(FuenteInvalidaError):
        await install_from_source("acme/repo", http=http)


# ---------------------------------------------------------------------------
# install_from_source — pipeline completo
# ---------------------------------------------------------------------------


@respx.mock
async def test_install_from_source_pipeline_completo(http):
    respx.get("https://raw.githubusercontent.com/acme/pdf-helper/HEAD/SKILL.md").mock(
        return_value=httpx.Response(
            200, text="---\nname: PDF Helper\ndescription: Ayuda con PDFs.\n---\ncuerpo\n"
        )
    )
    instalada = await install_from_source("acme/pdf-helper", http=http)

    assert instalada.owner == "acme"
    assert instalada.repo == "pdf-helper"
    assert instalada.subpath is None
    assert instalada.source == "acme/pdf-helper"
    assert instalada.nombre == "PDF Helper"
    assert instalada.descripcion == "Ayuda con PDFs."
    assert instalada.contenido == "cuerpo"


@respx.mock
async def test_install_from_source_sin_name_en_frontmatter_usa_repo(http):
    respx.get("https://raw.githubusercontent.com/acme/pdf-helper/HEAD/SKILL.md").mock(
        return_value=httpx.Response(200, text="Sin frontmatter, solo texto plano.")
    )
    instalada = await install_from_source("acme/pdf-helper", http=http)
    assert instalada.nombre == "pdf-helper"  # fallback: nombre=repo


@respx.mock
async def test_install_from_source_con_subpath_normaliza_source(http):
    respx.get("https://raw.githubusercontent.com/acme/repo/HEAD/sub/path/SKILL.md").mock(
        return_value=httpx.Response(200, text="---\nname: X\n---\nc")
    )
    instalada = await install_from_source("acme/repo/sub/path", http=http)
    assert instalada.source == "acme/repo/sub/path"


async def test_install_from_source_propaga_fuente_invalida(http):
    with pytest.raises(FuenteInvalidaError):
        await install_from_source("no-tiene-slash", http=http)


@respx.mock
async def test_install_from_source_propaga_no_encontrada(http):
    for ruta in ("SKILL.md", "skills/repo/SKILL.md", "skill/SKILL.md"):
        respx.get(f"https://raw.githubusercontent.com/acme/repo/HEAD/{ruta}").mock(
            return_value=httpx.Response(404)
        )
    with pytest.raises(SkillNoEncontradaError):
        await install_from_source("acme/repo", http=http)
