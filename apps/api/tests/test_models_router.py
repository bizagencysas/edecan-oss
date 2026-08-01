"""`GET /v1/models/chat` — catálogo único del selector de modelos del chat.

Se comprueba la FORMA del contrato (las tres UIs dependen de estas claves en
snake_case) y las invariantes que el dueño pidió: los cuatro principales en
orden, los cuatro ven imágenes, y los secundarios van etiquetados como ciegos.
"""

from __future__ import annotations

import uuid

from conftest import auth_headers
from edecan_llm.task_router import modelo_chat_por_defecto


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


async def test_los_cuatro_principales_van_en_orden_y_todos_ven_imagenes(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())

    body = (await client.get("/v1/models/chat", headers=headers)).json()

    principales = [m for m in body["modelos"] if m["principal"]]
    assert [m["orden"] for m in principales] == [1, 2, 3, 4]
    assert [m["nombre"] for m in principales] == ["Copla", "Silva", "Soneto", "Oda"]
    # Restricción dura: el dueño manda capturas constantemente.
    assert all(m["ve_imagenes"] for m in principales)
    # El primero es el default, y no razona: su fila de Esfuerzo no se muestra.
    assert principales[0]["id"] == body["default"]
    assert principales[0]["soporta_esfuerzo"] is False
    assert [m["soporta_esfuerzo"] for m in principales[1:]] == [True, True, True]


async def test_los_secundarios_van_etiquetados_como_ciegos(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())

    body = (await client.get("/v1/models/chat", headers=headers)).json()

    secundarios = [m for m in body["modelos"] if not m["principal"]]
    assert len(secundarios) == 3
    assert not any(m["ve_imagenes"] for m in secundarios)
    # La UI no tiene que inventar el aviso: viene en la descripción.
    assert all("No ve imágenes" in m["descripcion"] for m in secundarios)
    # Los principal:false van después de los principal:true en la lista.
    assert [m["principal"] for m in body["modelos"]] == [True] * 4 + [False] * 3


async def test_el_catalogo_exige_autenticacion(client) -> None:
    assert (await client.get("/v1/models/chat")).status_code == 401
