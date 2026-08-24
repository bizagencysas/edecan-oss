"""`GET /v1/models/chat` — catálogo único del selector de modelos del chat.

Se comprueba la FORMA del contrato (las tres UIs dependen de estas claves en
snake_case) y las invariantes que el dueño pidió: los cuatro principales en
orden, todos con visión, Scout a la vista. Los ciegos (Copla/GPT-OSS/Nemotron)
ya no salen en el selector.
"""

from __future__ import annotations

import uuid

from conftest import auth_headers
from edecan_llm.task_router import modelo_chat_con_vision_por_defecto, modelo_chat_por_defecto


async def test_catalogo_de_chat_tiene_la_forma_exacta_del_contrato(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())

    response = await client.get("/v1/models/chat", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"default", "esfuerzos", "esfuerzo_default", "modelos"}
    assert body["default"] == modelo_chat_por_defecto()
    assert body["esfuerzos"] == ["bajo", "medio", "alto"]
    assert body["esfuerzo_default"] == "medio"
    for modelo in body["modelos"]:
        assert set(modelo) == {
            "id",
            "nombre",
            "descripcion",
            "orden",
            "principal",
            "ve_imagenes",
            "soporta_esfuerzo",
            "contexto_ventana",
        }


async def test_los_principales_van_en_orden_y_el_catalogo_declara_la_ruta_de_vision(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())

    body = (await client.get("/v1/models/chat", headers=headers)).json()

    principales = [m for m in body["modelos"] if m["principal"]]
    assert [m["orden"] for m in principales] == [1, 2, 3, 4]
    assert [m["nombre"] for m in principales] == ["Scout", "Silva", "Soneto", "Oda"]
    modelo_vision = modelo_chat_con_vision_por_defecto()
    assert any(m["id"] == modelo_vision and m["ve_imagenes"] for m in principales)
    assert principales[0]["id"] == body["default"]
    assert principales[0]["soporta_esfuerzo"] is False
    assert [m["soporta_esfuerzo"] for m in principales[1:]] == [True, True, True]


async def test_el_catalogo_del_chat_no_esconde_scout_ni_trae_ciegos(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())

    body = (await client.get("/v1/models/chat", headers=headers)).json()

    assert all(m["ve_imagenes"] for m in body["modelos"])
    assert all(m["principal"] for m in body["modelos"])
    assert body["modelos"][0]["nombre"] == "Scout"
    assert not any(m["nombre"] == "Copla" for m in body["modelos"])
    assert not any("GPT-OSS" in m["nombre"] or "Nemotron" in m["nombre"] for m in body["modelos"])


async def test_el_catalogo_exige_autenticacion(client) -> None:
    assert (await client.get("/v1/models/chat")).status_code == 401
