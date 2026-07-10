"""Tests de `edecan_skills.client.SkillsIndexClient` — offline con `respx`.

El índice es best-effort: TODOS los casos de fallo (red, status inesperado, JSON
inválido/con forma inesperada, 404 en ambos endpoints candidatos) devuelven `[]`, nunca
lanzan — ver el docstring del módulo bajo prueba.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from edecan_skills.client import SkillsIndexClient


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


# --- happy path -------------------------------------------------------------


@respx.mock
async def test_search_happy_path_forma_envuelta(http):
    respx.get("https://skills.sh/api/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "skills": [
                    {
                        "name": "pdf-helper",
                        "source": "acme/pdf-helper",
                        "description": "Ayuda con PDFs.",
                        "installs": 42,
                    }
                ]
            },
        )
    )
    cliente = SkillsIndexClient("https://skills.sh", http)

    resultados = await cliente.search("pdf")

    assert len(resultados) == 1
    hit = resultados[0]
    assert hit.nombre == "pdf-helper"
    assert hit.source == "acme/pdf-helper"
    assert hit.descripcion == "Ayuda con PDFs."
    assert hit.installs == 42


@respx.mock
async def test_search_happy_path_lista_directa(http):
    respx.get("https://skills.sh/api/search").mock(
        return_value=httpx.Response(
            200,
            json=[{"name": "otra-skill", "repo": "acme/otra-skill", "description": "x"}],
        )
    )
    cliente = SkillsIndexClient("https://skills.sh", http)

    resultados = await cliente.search("otra")

    assert len(resultados) == 1
    assert resultados[0].nombre == "otra-skill"
    assert resultados[0].source == "acme/otra-skill"  # cae a "repo" cuando falta "source"
    assert resultados[0].installs is None  # sin "installs" en el item -> None


async def test_search_consulta_vacia_no_hace_red(http):
    # Sin ningún mock de respx: si la búsqueda hiciera una petición real, este test
    # fallaría con un error de conexión/`respx` en vez de devolver `[]` limpiamente.
    cliente = SkillsIndexClient("https://skills.sh", http)
    assert await cliente.search("   ") == []


# --- fallback 404 -------------------------------------------------------------


@respx.mock
async def test_search_404_en_search_reintenta_en_skills(http):
    respx.get("https://skills.sh/api/search").mock(return_value=httpx.Response(404))
    respx.get("https://skills.sh/api/skills").mock(
        return_value=httpx.Response(200, json={"skills": [{"name": "x", "source": "a/b"}]})
    )
    cliente = SkillsIndexClient("https://skills.sh", http)

    resultados = await cliente.search("x")

    assert len(resultados) == 1
    assert resultados[0].nombre == "x"


@respx.mock
async def test_search_404_en_ambos_devuelve_vacio(http):
    respx.get("https://skills.sh/api/search").mock(return_value=httpx.Response(404))
    respx.get("https://skills.sh/api/skills").mock(return_value=httpx.Response(404))
    cliente = SkillsIndexClient("https://skills.sh", http)

    assert await cliente.search("x") == []


# --- cualquier otro fallo: [] inmediato, sin reintentar el segundo endpoint --------


@respx.mock
async def test_search_error_500_no_reintenta_el_segundo_endpoint(http):
    ruta_search = respx.get("https://skills.sh/api/search").mock(
        return_value=httpx.Response(500)
    )
    ruta_skills = respx.get("https://skills.sh/api/skills").mock(
        return_value=httpx.Response(200, json={"skills": []})
    )
    cliente = SkillsIndexClient("https://skills.sh", http)

    assert await cliente.search("x") == []
    assert ruta_search.called
    assert not ruta_skills.called  # 500 no es 404: no hay reintento


@respx.mock
async def test_search_json_invalido_devuelve_vacio(http):
    respx.get("https://skills.sh/api/search").mock(
        return_value=httpx.Response(200, text="esto no es json")
    )
    cliente = SkillsIndexClient("https://skills.sh", http)

    assert await cliente.search("x") == []


@respx.mock
async def test_search_forma_json_inesperada_devuelve_vacio(http):
    respx.get("https://skills.sh/api/search").mock(
        return_value=httpx.Response(200, json={"algo": "que no es una lista ni tiene 'skills'"})
    )
    cliente = SkillsIndexClient("https://skills.sh", http)

    assert await cliente.search("x") == []


@respx.mock
async def test_search_fallo_de_red_devuelve_vacio(http):
    respx.get("https://skills.sh/api/search").mock(side_effect=httpx.ConnectError("caído"))
    cliente = SkillsIndexClient("https://skills.sh", http)

    assert await cliente.search("x") == []


# --- parse tolerante de items individuales -----------------------------------


@respx.mock
async def test_search_descarta_items_no_dict_y_sin_nombre_util(http):
    respx.get("https://skills.sh/api/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "skills": [
                    "esto-no-es-un-dict",
                    {"description": "sin name ni source ni repo"},
                    {"name": "buena", "source": "acme/buena"},
                ]
            },
        )
    )
    cliente = SkillsIndexClient("https://skills.sh", http)

    resultados = await cliente.search("x")

    assert len(resultados) == 1
    assert resultados[0].nombre == "buena"


@respx.mock
async def test_search_respeta_k(http):
    respx.get("https://skills.sh/api/search").mock(
        return_value=httpx.Response(
            200,
            json={"skills": [{"name": f"skill-{i}", "source": f"a/{i}"} for i in range(20)]},
        )
    )
    cliente = SkillsIndexClient("https://skills.sh", http)

    resultados = await cliente.search("x", k=3)

    assert len(resultados) == 3


@respx.mock
async def test_search_base_url_con_slash_final_no_duplica_slash(http):
    ruta = respx.get("https://skills.sh/api/search").mock(
        return_value=httpx.Response(200, json={"skills": []})
    )
    cliente = SkillsIndexClient("https://skills.sh/", http)

    await cliente.search("x")

    assert ruta.called
