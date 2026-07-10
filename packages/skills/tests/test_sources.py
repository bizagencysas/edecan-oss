"""Tests de `edecan_skills.sources` (`OpenClawSource`/`HermesSource`): offline con
`respx` — cada test genera su propio tarball chico en memoria (`tarfile` + `io.BytesIO`,
como pide el paquete de trabajo) en vez de descargar nada real. Cubre: layout de dos
niveles bajo `skills/`, filtro por substring en nombre/descripción, `source` formado
correctamente para que `edecan_skills.installer.install_from_source` lo resuelva sin
cambios, fallos de red/tamaño/formato (siempre `[]`, best-effort, salvo el cap de tamaño
que sí propaga `SkillDemasiadoGrandeError`).
"""

from __future__ import annotations

import io
import tarfile

import httpx
import pytest
import respx
from edecan_skills.installer import SkillDemasiadoGrandeError, parse_source
from edecan_skills.sources import HermesSource, OpenClawSource


def _tarball(entries: dict[str, str], *, raiz: str = "skills-abc1234") -> bytes:
    """Arma un `.tar.gz` en memoria con `entries` (ruta relativa a la raíz -> contenido) —
    mismo layout que produce `codeload.github.com` (todo bajo `"<repo>-<ref>/"`)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for ruta, contenido in entries.items():
            datos = contenido.encode("utf-8")
            info = tarfile.TarInfo(name=f"{raiz}/{ruta}")
            info.size = len(datos)
            tar.addfile(info, io.BytesIO(datos))
    return buf.getvalue()


_OPENCLAW_TARBALL = _tarball(
    {
        "skills/acme/pdf-helper/SKILL.md": (
            "---\nname: PDF Helper\ndescription: Ayuda con PDFs.\n---\ncuerpo"
        ),
        "skills/acme/spreadsheet-wizard/SKILL.md": (
            "---\nname: Spreadsheet Wizard\ndescription: Hojas de cálculo.\n---\ncuerpo2"
        ),
        "README.md": "no es una skill",  # fuera de skills/ — se ignora
        "skills/acme/sin-md/notas.txt": "tampoco es SKILL.md",  # nombre de archivo distinto
    }
)


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


# ---------------------------------------------------------------------------
# OpenClawSource
# ---------------------------------------------------------------------------


@respx.mock
async def test_openclaw_search_encuentra_por_nombre(http):
    respx.get("https://codeload.github.com/openclaw/skills/tar.gz/HEAD").mock(
        return_value=httpx.Response(200, content=_OPENCLAW_TARBALL)
    )
    resultados = await OpenClawSource(http).search("pdf")
    assert len(resultados) == 1
    assert resultados[0].nombre == "PDF Helper"
    assert resultados[0].descripcion == "Ayuda con PDFs."


@respx.mock
async def test_openclaw_search_encuentra_por_descripcion(http):
    respx.get("https://codeload.github.com/openclaw/skills/tar.gz/HEAD").mock(
        return_value=httpx.Response(200, content=_OPENCLAW_TARBALL)
    )
    resultados = await OpenClawSource(http).search("cálculo")
    assert len(resultados) == 1
    assert resultados[0].nombre == "Spreadsheet Wizard"


@respx.mock
async def test_openclaw_search_query_vacia_devuelve_todo_el_indice(http):
    respx.get("https://codeload.github.com/openclaw/skills/tar.gz/HEAD").mock(
        return_value=httpx.Response(200, content=_OPENCLAW_TARBALL)
    )
    resultados = await OpenClawSource(http).search("")
    assert len(resultados) == 2


@respx.mock
async def test_openclaw_search_sin_match_devuelve_vacio(http):
    respx.get("https://codeload.github.com/openclaw/skills/tar.gz/HEAD").mock(
        return_value=httpx.Response(200, content=_OPENCLAW_TARBALL)
    )
    assert await OpenClawSource(http).search("no-existe-nada-parecido") == []


@respx.mock
async def test_openclaw_search_respeta_k(http):
    respx.get("https://codeload.github.com/openclaw/skills/tar.gz/HEAD").mock(
        return_value=httpx.Response(200, content=_OPENCLAW_TARBALL)
    )
    resultados = await OpenClawSource(http).search("", k=1)
    assert len(resultados) == 1


@respx.mock
async def test_openclaw_ignora_archivos_fuera_del_layout(http):
    respx.get("https://codeload.github.com/openclaw/skills/tar.gz/HEAD").mock(
        return_value=httpx.Response(200, content=_OPENCLAW_TARBALL)
    )
    resultados = await OpenClawSource(http).search("")
    fuentes = {r.source for r in resultados}
    assert not any("README" in f or "notas" in f for f in fuentes)


@respx.mock
async def test_openclaw_source_resuelve_via_install_from_source(http):
    """El `source` que arma `OpenClawSource` debe ser exactamente lo que
    `edecan_skills.installer` necesita para volver a encontrar el MISMO `SKILL.md` sin
    ningún cambio en el pipeline de instalación existente."""
    respx.get("https://codeload.github.com/openclaw/skills/tar.gz/HEAD").mock(
        return_value=httpx.Response(200, content=_OPENCLAW_TARBALL)
    )
    resultados = await OpenClawSource(http).search("pdf")
    owner, repo, subpath = parse_source(resultados[0].source)
    assert (owner, repo, subpath) == ("openclaw", "skills", "skills/acme/pdf-helper")


# ---------------------------------------------------------------------------
# HermesSource
# ---------------------------------------------------------------------------


@respx.mock
async def test_hermes_search_encuentra_por_nombre(http):
    tarball = _tarball(
        {
            "skills/productivity/apple-notes/SKILL.md": (
                "---\nname: Apple Notes\ndescription: Organiza notas.\n---\ncuerpo"
            ),
        }
    )
    respx.get("https://codeload.github.com/NousResearch/hermes-agent/tar.gz/HEAD").mock(
        return_value=httpx.Response(200, content=tarball)
    )
    resultados = await HermesSource(http).search("notes")
    assert len(resultados) == 1
    assert resultados[0].source == "NousResearch/hermes-agent/skills/productivity/apple-notes"


@respx.mock
async def test_hermes_search_ignora_description_md(http):
    tarball = _tarball(
        {
            "skills/productivity/DESCRIPTION.md": "descripción de la categoría, no una skill",
            "skills/productivity/apple-notes/SKILL.md": "---\nname: Apple Notes\n---\ncuerpo",
        }
    )
    respx.get("https://codeload.github.com/NousResearch/hermes-agent/tar.gz/HEAD").mock(
        return_value=httpx.Response(200, content=tarball)
    )
    resultados = await HermesSource(http).search("")
    assert len(resultados) == 1
    assert resultados[0].nombre == "Apple Notes"


# ---------------------------------------------------------------------------
# Fallos: red, tamaño, formato — best-effort (ver docstring del módulo)
# ---------------------------------------------------------------------------


@respx.mock
async def test_search_error_de_red_devuelve_vacio(http):
    respx.get("https://codeload.github.com/openclaw/skills/tar.gz/HEAD").mock(
        side_effect=httpx.ConnectError("caído")
    )
    assert await OpenClawSource(http).search("pdf") == []


@respx.mock
async def test_search_status_inesperado_devuelve_vacio(http):
    respx.get("https://codeload.github.com/openclaw/skills/tar.gz/HEAD").mock(
        return_value=httpx.Response(404)
    )
    assert await OpenClawSource(http).search("pdf") == []


@respx.mock
async def test_search_tarball_demasiado_grande_lanza_error_claro(http):
    respx.get("https://codeload.github.com/openclaw/skills/tar.gz/HEAD").mock(
        return_value=httpx.Response(200, content=b"x" * 50_000_001)
    )
    with pytest.raises(SkillDemasiadoGrandeError):
        await OpenClawSource(http).search("pdf")


@respx.mock
async def test_search_tarball_corrupto_devuelve_vacio(http):
    respx.get("https://codeload.github.com/openclaw/skills/tar.gz/HEAD").mock(
        return_value=httpx.Response(200, content=b"esto no es un tar.gz valido")
    )
    assert await OpenClawSource(http).search("pdf") == []


@respx.mock
async def test_search_tarball_vacio_de_skills_devuelve_vacio(http):
    tarball = _tarball({"README.md": "nada de skills acá"})
    respx.get("https://codeload.github.com/openclaw/skills/tar.gz/HEAD").mock(
        return_value=httpx.Response(200, content=tarball)
    )
    assert await OpenClawSource(http).search("") == []
