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
from edecan_skills import sources
from edecan_skills.installer import (
    FuenteInvalidaError,
    SkillDemasiadoGrandeError,
    SkillNoEncontradaError,
    parse_source,
)
from edecan_skills.sources import HermesSource, OpenClawSource, UrlSkillSource


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


# ---------------------------------------------------------------------------
# UrlSkillSource — skill instalada pegando directo la URL de su SKILL.md
# ---------------------------------------------------------------------------

_SKILL_MD_VALIDO = "---\nname: PDF Helper\ndescription: Ayuda con PDFs.\n---\ncuerpo"


@respx.mock
async def test_url_source_url_valida_devuelve_skillhit(http):
    respx.get("https://ejemplo.com/skills/pdf/SKILL.md").mock(
        return_value=httpx.Response(200, content=_SKILL_MD_VALIDO.encode())
    )
    hit = await UrlSkillSource(http).fetch("https://ejemplo.com/skills/pdf/SKILL.md")
    assert hit.nombre == "PDF Helper"
    assert hit.descripcion == "Ayuda con PDFs."
    # `source` es la URL ORIGINAL pegada por el usuario (ver docstring de `fetch`), no la
    # de un eventual redirect final.
    assert hit.source == "https://ejemplo.com/skills/pdf/SKILL.md"


@respx.mock
async def test_url_source_sin_frontmatter_usa_el_path_como_nombre(http):
    respx.get("https://ejemplo.com/skills/pdf-helper/SKILL.md").mock(
        return_value=httpx.Response(200, content=b"cuerpo sin frontmatter")
    )
    hit = await UrlSkillSource(http).fetch("https://ejemplo.com/skills/pdf-helper/SKILL.md")
    assert hit.nombre == "pdf-helper"


@respx.mock
async def test_url_source_recorta_espacios_de_la_url(http):
    respx.get("https://ejemplo.com/SKILL.md").mock(
        return_value=httpx.Response(200, content=_SKILL_MD_VALIDO.encode())
    )
    hit = await UrlSkillSource(http).fetch("  https://ejemplo.com/SKILL.md  ")
    assert hit.source == "https://ejemplo.com/SKILL.md"


async def test_url_source_url_vacia_lanza_fuente_invalida(http):
    with pytest.raises(FuenteInvalidaError):
        await UrlSkillSource(http).fetch("   ")


# --- Esquema no permitido -----------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "ftp://ejemplo.com/SKILL.md",
        "file:///etc/passwd",
        "javascript:alert(1)",
    ],
)
async def test_url_source_esquema_no_permitido_lanza_fuente_invalida(http, url):
    with pytest.raises(FuenteInvalidaError):
        await UrlSkillSource(http).fetch(url)


async def test_url_source_url_con_credenciales_embebidas_lanza_fuente_invalida(http):
    with pytest.raises(FuenteInvalidaError):
        await UrlSkillSource(http).fetch("https://usuario:clave@ejemplo.com/SKILL.md")


# --- Host privado/reservado (SSRF) --------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/SKILL.md",
        "http://169.254.169.254/latest/meta-data/",  # metadata AWS/GCP
        "http://10.1.2.3/SKILL.md",
        "http://192.168.1.1/SKILL.md",
        "http://172.16.0.5/SKILL.md",
        "http://[::1]/SKILL.md",
        "http://localhost:5000/SKILL.md",
        "http://algo.localhost/SKILL.md",
        "http://intranet.local/SKILL.md",
        "http://servicio.internal/SKILL.md",
    ],
)
async def test_url_source_host_privado_literal_lanza_fuente_invalida(http, url):
    # Sin ninguna ruta registrada en respx: si el guardrail no bloqueara esto ANTES de la
    # red, este test fallaría con un error de respx en vez de aprobar por accidente.
    with pytest.raises(FuenteInvalidaError):
        await UrlSkillSource(http).fetch(url)


async def test_url_source_dominio_que_resuelve_a_ip_privada_lanza_fuente_invalida(
    http, monkeypatch
):
    async def _resolver_privado(hostname: str) -> list[str]:
        return ["10.1.2.3"]

    monkeypatch.setattr(sources, "resolve_hostname_ips", _resolver_privado)
    with pytest.raises(FuenteInvalidaError):
        await UrlSkillSource(http).fetch("https://interno.corp.ejemplo.com/SKILL.md")


async def test_url_source_fallo_de_dns_bloquea_por_seguridad(http, monkeypatch):
    async def _resolver_roto(hostname: str) -> list[str]:
        raise OSError("simulated DNS failure")

    monkeypatch.setattr(sources, "resolve_hostname_ips", _resolver_roto)
    with pytest.raises(FuenteInvalidaError):
        await UrlSkillSource(http).fetch("https://dominio-raro.ejemplo.com/SKILL.md")


# --- Redirección hacia host privado --------------------------------------------


@respx.mock
async def test_url_source_sigue_redirect_hacia_host_publico(http):
    respx.get("https://ejemplo.com/viejo").mock(
        return_value=httpx.Response(
            301, headers={"location": "https://ejemplo.com/skills/pdf/SKILL.md"}
        )
    )
    respx.get("https://ejemplo.com/skills/pdf/SKILL.md").mock(
        return_value=httpx.Response(200, content=_SKILL_MD_VALIDO.encode())
    )
    hit = await UrlSkillSource(http).fetch("https://ejemplo.com/viejo")
    assert hit.nombre == "PDF Helper"


@respx.mock
async def test_url_source_no_sigue_redirect_hacia_ip_privada(http):
    """Repro del guardrail SSRF-vía-redirect (mismo hallazgo que
    `edecan_browser.fetch.HttpxFetcher`): un host público ya validado no puede usar un
    redirect para escapar hacia una IP privada."""
    respx.get("https://ejemplo.com/oferta").mock(
        return_value=httpx.Response(
            302,
            headers={
                "location": "http://169.254.169.254/latest/meta-data/iam/security-credentials/"
            },
        )
    )
    # A propósito NO se mockea la URL de metadata: si `UrlSkillSource` la siguiera pese a
    # todo, este test fallaría con un error de respx en vez de devolver contenido falso
    # que oculte la regresión.
    with pytest.raises(FuenteInvalidaError):
        await UrlSkillSource(http).fetch("https://ejemplo.com/oferta")


@respx.mock
async def test_url_source_demasiados_redirects_lanza_fuente_invalida(http):
    for i in range(6):
        respx.get(f"https://ejemplo.com/paso{i}").mock(
            return_value=httpx.Response(
                302, headers={"location": f"https://ejemplo.com/paso{i + 1}"}
            )
        )
    with pytest.raises(FuenteInvalidaError):
        await UrlSkillSource(http).fetch("https://ejemplo.com/paso0")


# --- Tope de tamaño -------------------------------------------------------------


@respx.mock
async def test_url_source_archivo_demasiado_grande_lanza_error_claro(http):
    respx.get("https://ejemplo.com/SKILL.md").mock(
        return_value=httpx.Response(200, content=b"x" * 200_001)
    )
    with pytest.raises(SkillDemasiadoGrandeError):
        await UrlSkillSource(http).fetch("https://ejemplo.com/SKILL.md")


# --- Otros fallos de red ---------------------------------------------------------


@respx.mock
async def test_url_source_404_lanza_skill_no_encontrada(http):
    respx.get("https://ejemplo.com/SKILL.md").mock(return_value=httpx.Response(404))
    with pytest.raises(SkillNoEncontradaError):
        await UrlSkillSource(http).fetch("https://ejemplo.com/SKILL.md")


@respx.mock
async def test_url_source_error_de_red_lanza_skill_no_encontrada(http):
    respx.get("https://ejemplo.com/SKILL.md").mock(side_effect=httpx.ConnectError("caído"))
    with pytest.raises(SkillNoEncontradaError):
        await UrlSkillSource(http).fetch("https://ejemplo.com/SKILL.md")
