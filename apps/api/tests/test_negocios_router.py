"""Tests de `apps/api/edecan_api/routers/negocios.py` (`/v1/negocios/*`).

`edecan_business` ya tiene su propia suite exhaustiva de la orquestación SQL real
(`packages/business/tests/`, con un `FakeSession` programable que ejercita cada `INSERT`/
`UPDATE`/`SELECT`) — este archivo NO la repite. En vez de eso, sustituye con `monkeypatch`
los símbolos que el router importó directo de `edecan_business` (`crear_factura`,
`kpis_mes`, `listar_facturas`, `obtener_factura`, `cambiar_estado`) — exactamente el patrón
"módulos puros/fakeables" que describe el paquete de trabajo (mismo criterio que
`test_files.py` sustituye `aioboto3.Session` o `test_conversations.py` sustituye `Agent`) —
y solo verifica el contrato HTTP propio de ESTE router: status codes, forma de la
respuesta, qué argumentos recibe la capa de negocio, validación Pydantic (`422` en items
vacíos/negativos o un `status` fuera de vocabulario) y el mapeo de excepciones de negocio
(`ValueError` -> `422`, `EstadoInvalidoError` -> `409`, `None` -> `404`).

`conftest.app` deja `edecan_deps.get_tenant_session` apuntando a `None` (ver el docstring de
`test_consents.py`) — no hace falta sobreescribirlo aquí: al sustituir las funciones de
`edecan_business` completas, ninguna de ellas llega a tocar esa sesión de verdad.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from conftest import auth_headers

import edecan_api.routers.negocios as negocios_module


def _auth(*, plan_key: str = "hosted_basic"):
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key=plan_key)
    return headers, tenant_id, user_id


def _invoice_out(**overrides):
    base = {
        "id": str(uuid.uuid4()),
        "numero": "F-2026-0001",
        "cliente_nombre": "Acme SA",
        "cliente_email": None,
        "moneda": "USD",
        "subtotal": "100.00",
        "impuestos": "0.00",
        "total": "100.00",
        "status": "draft",
        "due_date": None,
        "notas": "",
        "pdf_file_id": str(uuid.uuid4()),
        "items": [],
    }
    base.update(overrides)
    return base


def _factura_payload(**overrides):
    payload = {
        "cliente_nombre": "Acme SA",
        "items": [{"descripcion": "Consultoría", "cantidad": 1, "precio_unitario": 100}],
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# GET /kpis
# ---------------------------------------------------------------------------


async def test_get_kpis_requires_auth(client) -> None:
    response = await client.get("/v1/negocios/kpis")
    assert response.status_code == 401


async def test_get_kpis_returns_business_layer_shape(client, monkeypatch) -> None:
    headers, tenant_id, user_id = _auth()
    kpis_fake = {
        "mes": "2026-07",
        "ingresos": 1000.0,
        "gastos": 200.0,
        "beneficio": 800.0,
        "nuevos_clientes": 2,
        "facturado": 500.0,
        "cobrado": 300.0,
        "por_canal": [{"canal": "web", "total": 1000.0}],
        "actividad": [],
    }
    calls = []

    async def fake_kpis_mes(session, *, tenant_id, user_id, mes):
        calls.append({"tenant_id": tenant_id, "user_id": user_id, "mes": mes})
        return kpis_fake

    monkeypatch.setattr(negocios_module, "kpis_mes", fake_kpis_mes)

    response = await client.get("/v1/negocios/kpis?mes=2026-07", headers=headers)

    assert response.status_code == 200
    assert response.json() == kpis_fake
    assert calls == [{"tenant_id": tenant_id, "user_id": user_id, "mes": "2026-07"}]


async def test_get_kpis_sin_mes_no_lo_calcula_el_router(client, monkeypatch) -> None:
    """El router reenvía `mes=None` tal cual — es `kpis_mes` quien decide el default del
    mes actual (`packages/business/tests/test_kpis.py::test_kpis_mes_default_es_mes_actual`
    ya lo cubre); este router no debe duplicar ese cálculo."""
    headers, _, _ = _auth()
    calls = []

    async def fake_kpis_mes(session, *, tenant_id, user_id, mes):
        calls.append(mes)
        return {"mes": "irrelevante"}

    monkeypatch.setattr(negocios_module, "kpis_mes", fake_kpis_mes)
    response = await client.get("/v1/negocios/kpis", headers=headers)
    assert response.status_code == 200
    assert calls == [None]


async def test_get_kpis_mes_invalido_mapea_a_422(client, monkeypatch) -> None:
    async def fake_kpis_mes(session, *, tenant_id, user_id, mes):
        raise ValueError("'no-es-un-mes' no es un mes válido (usa YYYY-MM).")

    monkeypatch.setattr(negocios_module, "kpis_mes", fake_kpis_mes)
    headers, _, _ = _auth()
    response = await client.get("/v1/negocios/kpis?mes=no-es-un-mes", headers=headers)
    assert response.status_code == 422
    assert "mes válido" in response.json()["detail"]


# ---------------------------------------------------------------------------
# POST /facturas
# ---------------------------------------------------------------------------


async def test_create_factura_requires_auth(client) -> None:
    response = await client.post("/v1/negocios/facturas", json=_factura_payload())
    assert response.status_code == 401


async def test_create_factura_success(client, monkeypatch) -> None:
    headers, tenant_id, user_id = _auth()
    invoice_out = _invoice_out()
    calls = []

    async def fake_crear_factura(session, *, tenant_id, user_id, settings, **kwargs):
        calls.append({"tenant_id": tenant_id, "user_id": user_id, "settings": settings, **kwargs})
        return invoice_out

    monkeypatch.setattr(negocios_module, "crear_factura", fake_crear_factura)

    response = await client.post(
        "/v1/negocios/facturas", json=_factura_payload(), headers=headers
    )

    assert response.status_code == 201
    assert response.json() == invoice_out
    assert len(calls) == 1
    assert calls[0]["tenant_id"] == tenant_id
    assert calls[0]["user_id"] == user_id
    assert calls[0]["settings"] is not None
    assert calls[0]["cliente_nombre"] == "Acme SA"
    assert calls[0]["items"] == [
        {"descripcion": "Consultoría", "cantidad": Decimal("1"), "precio_unitario": Decimal("100")}
    ]
    assert calls[0]["impuestos_pct"] == Decimal("0")
    assert calls[0]["moneda"] == "USD"


async def test_create_factura_pasa_impuestos_due_date_email_y_notas(client, monkeypatch) -> None:
    headers, _, _ = _auth()
    calls = []

    async def fake_crear_factura(session, *, tenant_id, user_id, settings, **kwargs):
        calls.append(kwargs)
        return _invoice_out()

    monkeypatch.setattr(negocios_module, "crear_factura", fake_crear_factura)

    payload = _factura_payload(
        impuestos_pct=16,
        due_date="2026-08-01",
        cliente_email="compras@acme.test",
        notas="Pago contra entrega.",
        moneda="mxn",
    )
    response = await client.post("/v1/negocios/facturas", json=payload, headers=headers)

    assert response.status_code == 201
    assert calls[0]["impuestos_pct"] == Decimal("16")
    assert str(calls[0]["due_date"]) == "2026-08-01"
    assert calls[0]["cliente_email"] == "compras@acme.test"
    assert calls[0]["notas"] == "Pago contra entrega."
    # el router no normaliza mayúsculas de moneda: eso es trabajo de edecan_business
    assert calls[0]["moneda"] == "mxn"


async def test_create_factura_rechaza_items_vacios_sin_llamar_al_negocio(
    client, monkeypatch
) -> None:
    async def fake_crear_factura(*args, **kwargs):
        raise AssertionError("crear_factura no debería llamarse con items vacíos")

    monkeypatch.setattr(negocios_module, "crear_factura", fake_crear_factura)
    headers, _, _ = _auth()

    response = await client.post(
        "/v1/negocios/facturas", json=_factura_payload(items=[]), headers=headers
    )
    assert response.status_code == 422


async def test_create_factura_rechaza_precio_negativo_sin_llamar_al_negocio(
    client, monkeypatch
) -> None:
    async def fake_crear_factura(*args, **kwargs):
        raise AssertionError("crear_factura no debería llamarse con precio negativo")

    monkeypatch.setattr(negocios_module, "crear_factura", fake_crear_factura)
    headers, _, _ = _auth()

    payload = _factura_payload(
        items=[{"descripcion": "X", "cantidad": 1, "precio_unitario": -5}]
    )
    response = await client.post("/v1/negocios/facturas", json=payload, headers=headers)
    assert response.status_code == 422


async def test_create_factura_rechaza_cantidad_negativa_sin_llamar_al_negocio(
    client, monkeypatch
) -> None:
    async def fake_crear_factura(*args, **kwargs):
        raise AssertionError("crear_factura no debería llamarse con cantidad negativa")

    monkeypatch.setattr(negocios_module, "crear_factura", fake_crear_factura)
    headers, _, _ = _auth()

    payload = _factura_payload(
        items=[{"descripcion": "X", "cantidad": -1, "precio_unitario": 5}]
    )
    response = await client.post("/v1/negocios/facturas", json=payload, headers=headers)
    assert response.status_code == 422


async def test_create_factura_rechaza_cliente_nombre_vacio_a_nivel_pydantic(
    client, monkeypatch
) -> None:
    async def fake_crear_factura(*args, **kwargs):
        raise AssertionError("crear_factura no debería llamarse con cliente_nombre=''")

    monkeypatch.setattr(negocios_module, "crear_factura", fake_crear_factura)
    headers, _, _ = _auth()

    response = await client.post(
        "/v1/negocios/facturas", json=_factura_payload(cliente_nombre=""), headers=headers
    )
    assert response.status_code == 422


async def test_create_factura_business_layer_valueerror_mapea_a_422(client, monkeypatch) -> None:
    """`cliente_nombre="   "` (solo espacios) pasa el `min_length=1` de Pydantic (cuenta
    caracteres, no los recorta) pero `edecan_business.invoices.crear_factura` sí lo
    rechaza tras `.strip()` — este test fija que ESE `ValueError` de la capa de negocio
    también se traduce a `422`, no solo los que detecta Pydantic."""

    async def fake_crear_factura(session, *, tenant_id, user_id, settings, **kwargs):
        raise ValueError("cliente_nombre es obligatorio.")

    monkeypatch.setattr(negocios_module, "crear_factura", fake_crear_factura)
    headers, _, _ = _auth()

    response = await client.post(
        "/v1/negocios/facturas", json=_factura_payload(cliente_nombre="   "), headers=headers
    )
    assert response.status_code == 422
    assert "cliente_nombre" in response.json()["detail"]


# ---------------------------------------------------------------------------
# GET /facturas
# ---------------------------------------------------------------------------


async def test_list_facturas_requires_auth(client) -> None:
    response = await client.get("/v1/negocios/facturas")
    assert response.status_code == 401


async def test_list_facturas_returns_list_sin_filtro(client, monkeypatch) -> None:
    headers, tenant_id, _ = _auth()
    facturas = [_invoice_out(numero="F-2026-0001"), _invoice_out(numero="F-2026-0002")]
    calls = []

    async def fake_listar_facturas(session, *, tenant_id, status):
        calls.append({"tenant_id": tenant_id, "status": status})
        return facturas

    monkeypatch.setattr(negocios_module, "listar_facturas", fake_listar_facturas)

    response = await client.get("/v1/negocios/facturas", headers=headers)

    assert response.status_code == 200
    assert response.json() == facturas
    assert calls == [{"tenant_id": tenant_id, "status": None}]


async def test_list_facturas_status_filter_se_reenvia(client, monkeypatch) -> None:
    headers, _, _ = _auth()
    calls = []

    async def fake_listar_facturas(session, *, tenant_id, status):
        calls.append(status)
        return []

    monkeypatch.setattr(negocios_module, "listar_facturas", fake_listar_facturas)

    response = await client.get("/v1/negocios/facturas?status=paid", headers=headers)
    assert response.status_code == 200
    assert response.json() == []
    assert calls == ["paid"]


async def test_list_facturas_status_invalido_mapea_a_422(client, monkeypatch) -> None:
    async def fake_listar_facturas(session, *, tenant_id, status):
        raise ValueError("status debe ser uno de ('draft', 'sent', 'paid', 'void').")

    monkeypatch.setattr(negocios_module, "listar_facturas", fake_listar_facturas)
    headers, _, _ = _auth()

    response = await client.get("/v1/negocios/facturas?status=bogus", headers=headers)
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /facturas/{id}
# ---------------------------------------------------------------------------


async def test_get_factura_requires_auth(client) -> None:
    response = await client.get(f"/v1/negocios/facturas/{uuid.uuid4()}")
    assert response.status_code == 401


async def test_get_factura_not_found_404(client, monkeypatch) -> None:
    async def fake_obtener_factura(session, *, tenant_id, invoice_id):
        return None

    monkeypatch.setattr(negocios_module, "obtener_factura", fake_obtener_factura)
    headers, _, _ = _auth()

    response = await client.get(f"/v1/negocios/facturas/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404


async def test_get_factura_found_200_incluye_items(client, monkeypatch) -> None:
    headers, tenant_id, _ = _auth()
    invoice_id = uuid.uuid4()
    invoice_out = _invoice_out(
        id=str(invoice_id),
        items=[
            {"descripcion": "X", "cantidad": "1", "precio_unitario": "10.00", "total": "10.00"}
        ],
    )
    calls = []

    async def fake_obtener_factura(session, *, tenant_id, invoice_id):
        calls.append({"tenant_id": tenant_id, "invoice_id": invoice_id})
        return invoice_out

    monkeypatch.setattr(negocios_module, "obtener_factura", fake_obtener_factura)

    response = await client.get(f"/v1/negocios/facturas/{invoice_id}", headers=headers)

    assert response.status_code == 200
    assert response.json() == invoice_out
    assert calls == [{"tenant_id": tenant_id, "invoice_id": invoice_id}]


# ---------------------------------------------------------------------------
# POST /facturas/{id}/estado
# ---------------------------------------------------------------------------


async def test_set_estado_requires_auth(client) -> None:
    response = await client.post(
        f"/v1/negocios/facturas/{uuid.uuid4()}/estado", json={"status": "sent"}
    )
    assert response.status_code == 401


async def test_set_estado_status_fuera_de_vocabulario_es_422_sin_llamar_al_negocio(
    client, monkeypatch
) -> None:
    async def fake_cambiar_estado(*args, **kwargs):
        raise AssertionError("cambiar_estado no debería llamarse")

    monkeypatch.setattr(negocios_module, "cambiar_estado", fake_cambiar_estado)
    headers, _, _ = _auth()

    for status_value in ("draft", "bogus"):
        response = await client.post(
            f"/v1/negocios/facturas/{uuid.uuid4()}/estado",
            json={"status": status_value},
            headers=headers,
        )
        assert response.status_code == 422, status_value


async def test_set_estado_transicion_ilegal_mapea_a_409(client, monkeypatch) -> None:
    async def fake_cambiar_estado(session, *, tenant_id, invoice_id, nuevo_status):
        raise negocios_module.EstadoInvalidoError(
            "No se puede pasar de 'draft' a 'paid' (permitido solo desde: sent)."
        )

    monkeypatch.setattr(negocios_module, "cambiar_estado", fake_cambiar_estado)
    headers, _, _ = _auth()

    response = await client.post(
        f"/v1/negocios/facturas/{uuid.uuid4()}/estado", json={"status": "paid"}, headers=headers
    )
    assert response.status_code == 409
    assert "draft" in response.json()["detail"]


async def test_set_estado_factura_inexistente_404(client, monkeypatch) -> None:
    async def fake_cambiar_estado(session, *, tenant_id, invoice_id, nuevo_status):
        return None

    monkeypatch.setattr(negocios_module, "cambiar_estado", fake_cambiar_estado)
    headers, _, _ = _auth()

    response = await client.post(
        f"/v1/negocios/facturas/{uuid.uuid4()}/estado", json={"status": "sent"}, headers=headers
    )
    assert response.status_code == 404


async def test_set_estado_success_200(client, monkeypatch) -> None:
    headers, tenant_id, _ = _auth()
    invoice_id = uuid.uuid4()
    invoice_out = _invoice_out(id=str(invoice_id), status="sent")
    calls = []

    async def fake_cambiar_estado(session, *, tenant_id, invoice_id, nuevo_status):
        calls.append(
            {"tenant_id": tenant_id, "invoice_id": invoice_id, "nuevo_status": nuevo_status}
        )
        return invoice_out

    monkeypatch.setattr(negocios_module, "cambiar_estado", fake_cambiar_estado)

    response = await client.post(
        f"/v1/negocios/facturas/{invoice_id}/estado", json={"status": "sent"}, headers=headers
    )

    assert response.status_code == 200
    assert response.json() == invoice_out
    assert calls == [{"tenant_id": tenant_id, "invoice_id": invoice_id, "nuevo_status": "sent"}]


async def test_set_estado_void_es_un_valor_aceptado(client, monkeypatch) -> None:
    async def fake_cambiar_estado(session, *, tenant_id, invoice_id, nuevo_status):
        return _invoice_out(status="void")

    monkeypatch.setattr(negocios_module, "cambiar_estado", fake_cambiar_estado)
    headers, _, _ = _auth()

    response = await client.post(
        f"/v1/negocios/facturas/{uuid.uuid4()}/estado", json={"status": "void"}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["status"] == "void"
