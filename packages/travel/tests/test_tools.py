"""Tests de `edecan_travel.tools`: las 5 herramientas.

`test_preparar_reserva_nunca_llama_a_amadeus_solo_crea_draft` es el test explícito que
pide el paquete de trabajo: prueba, mirando exactamente qué SQL se ejecutó, que la tool
`dangerous=True` no hace NADA más que insertar un borrador en `orders` — nunca resuelve
ningún proveedor ni llama a Amadeus.
"""

from __future__ import annotations

import types
from decimal import Decimal
from uuid import uuid4

import pytest
from edecan_travel.amadeus import EstadoVuelo, HotelOferta, VueloOferta
from edecan_travel.tools import (
    _MENSAJE_RESERVA_CREADA,
    BuscarHotelesTool,
    BuscarVuelosTool,
    EstadoVueloTool,
    PrepararReservaTool,
    RastrearPaqueteTool,
    _crear_reserva_draft,
)
from edecan_travel.tracking import CheckpointRastreo, RastreoPaquete


def _resolver_fijo(provider):
    """Factory de `provider_resolver` inyectable: siempre devuelve `provider`, sin
    tocar `ctx.vault`/`ctx.session` (mismo patrón que `packages/ads/tests/test_tools.py`)."""

    async def _resolve(ctx):
        return provider

    return _resolve


class _FakeTravelProvider:
    def __init__(
        self, *, vuelos=None, hoteles=None, estado=None, error: Exception | None = None
    ) -> None:
        self._vuelos = vuelos if vuelos is not None else []
        self._hoteles = hoteles if hoteles is not None else []
        self._estado = estado
        self._error = error
        self.llamadas_vuelos: list[tuple] = []
        self.llamadas_hoteles: list[tuple] = []

    async def buscar_vuelos(self, origen, destino, fecha, *, adultos=1, max_resultados=10):
        self.llamadas_vuelos.append((origen, destino, fecha, adultos, max_resultados))
        if self._error:
            raise self._error
        return self._vuelos

    async def buscar_hoteles(self, ciudad, checkin, checkout, *, adultos=1):
        self.llamadas_hoteles.append((ciudad, checkin, checkout, adultos))
        if self._error:
            raise self._error
        return self._hoteles

    async def estado_vuelo(self, carrier, numero, fecha):
        if self._error:
            raise self._error
        return self._estado


class _FakeTrackingProvider:
    def __init__(self, *, rastreo=None, error: Exception | None = None) -> None:
        self._rastreo = rastreo
        self._error = error
        self.llamadas: list[tuple] = []

    async def rastrear(self, tracking_number, courier_slug=None):
        self.llamadas.append((tracking_number, courier_slug))
        if self._error:
            raise self._error
        return self._rastreo


_OFERTA_VUELO = VueloOferta(
    id="off-1",
    aerolinea="AV",
    salida="2026-08-01T08:00:00",
    llegada="2026-08-01T10:00:00",
    origen="BOG",
    destino="MIA",
    escalas=0,
    precio_total="199.00",
    moneda="USD",
    booking_url="https://kiwi.com/u/prueba",
)

_OFERTA_HOTEL = HotelOferta(
    id="hot-1",
    nombre="Hotel Centro",
    rating="4",
    precio_total="89.00",
    moneda="EUR",
    checkin="2026-09-01",
    checkout="2026-09-05",
    booking_url="https://www.trivago.com/hotel/prueba",
)

_ESTADO = EstadoVuelo(
    carrier="AV",
    numero="123",
    fecha="2026-08-01",
    origen="BOG",
    destino="MIA",
    salida_programada="2026-08-01T08:00:00",
    llegada_programada="2026-08-01T10:00:00",
    terminal_salida="1",
    puerta_salida="B12",
)

_RASTREO = RastreoPaquete(
    estado="InTransit",
    courier="dhl",
    checkpoints=[
        CheckpointRastreo(fecha="2026-08-01T09:00:00", mensaje="Recogido", lugar="Bogotá")
    ],
    entrega_estimada="2026-08-10",
)


# ---------------------------------------------------------------------------
# buscar_vuelos
# ---------------------------------------------------------------------------


async def test_buscar_vuelos_sin_origen_no_resuelve_proveedor(make_ctx):
    provider = _FakeTravelProvider()
    tool = BuscarVuelosTool(provider_resolver=_resolver_fijo(provider))

    resultado = await tool.run(make_ctx(), {"origen": "", "destino": "MIA", "fecha": "2026-08-01"})

    assert "origen" in resultado.content.lower()
    assert provider.llamadas_vuelos == []


async def test_buscar_vuelos_sin_fecha_no_resuelve_proveedor(make_ctx):
    provider = _FakeTravelProvider()
    tool = BuscarVuelosTool(provider_resolver=_resolver_fijo(provider))

    resultado = await tool.run(make_ctx(), {"origen": "BOG", "destino": "MIA", "fecha": ""})

    assert "fecha" in resultado.content.lower()
    assert provider.llamadas_vuelos == []


async def test_buscar_vuelos_ok_lista_la_oferta_y_normaliza_codigos(make_ctx):
    provider = _FakeTravelProvider(vuelos=[_OFERTA_VUELO])
    tool = BuscarVuelosTool(provider_resolver=_resolver_fijo(provider))

    resultado = await tool.run(
        make_ctx(), {"origen": "bog", "destino": "mia", "fecha": "2026-08-01"}
    )

    assert "AV" in resultado.content
    assert "199.00" in resultado.content
    assert resultado.data["ofertas"][0]["id"] == "off-1"
    assert resultado.presentation[0]["type"] == "flight"
    assert resultado.presentation[0]["source_mode"] == "unknown"
    assert resultado.presentation[0]["actions"][0]["action"] == "open_url"
    assert resultado.presentation[0]["actions"][1]["action"] == "prefill_message"
    assert provider.llamadas_vuelos == [("BOG", "MIA", "2026-08-01", 1, 10)]


async def test_buscar_vuelos_sin_resultados_no_revienta(make_ctx):
    provider = _FakeTravelProvider(vuelos=[])
    tool = BuscarVuelosTool(provider_resolver=_resolver_fijo(provider))

    resultado = await tool.run(
        make_ctx(), {"origen": "BOG", "destino": "MIA", "fecha": "2026-08-01"}
    )

    assert "no encontré" in resultado.content.lower()
    assert resultado.data["ofertas"] == []


async def test_buscar_vuelos_error_del_proveedor_no_revienta(make_ctx):
    provider = _FakeTravelProvider(error=RuntimeError("Amadeus caído"))
    tool = BuscarVuelosTool(provider_resolver=_resolver_fijo(provider))

    resultado = await tool.run(
        make_ctx(), {"origen": "BOG", "destino": "MIA", "fecha": "2026-08-01"}
    )

    assert "no pude buscar vuelos" in resultado.content.lower()


async def test_buscar_vuelos_respeta_adultos_y_max_resultados(make_ctx):
    provider = _FakeTravelProvider(vuelos=[_OFERTA_VUELO])
    tool = BuscarVuelosTool(provider_resolver=_resolver_fijo(provider))

    await tool.run(
        make_ctx(),
        {
            "origen": "BOG",
            "destino": "MIA",
            "fecha": "2026-08-01",
            "adultos": 3,
            "max_resultados": 5,
        },
    )

    assert provider.llamadas_vuelos == [("BOG", "MIA", "2026-08-01", 3, 5)]


# ---------------------------------------------------------------------------
# buscar_hoteles
# ---------------------------------------------------------------------------


async def test_buscar_hoteles_sin_ciudad_no_resuelve_proveedor(make_ctx):
    provider = _FakeTravelProvider()
    tool = BuscarHotelesTool(provider_resolver=_resolver_fijo(provider))

    resultado = await tool.run(
        make_ctx(), {"ciudad": "", "checkin": "2026-09-01", "checkout": "2026-09-05"}
    )

    assert "ciudad" in resultado.content.lower()
    assert provider.llamadas_hoteles == []


async def test_buscar_hoteles_sin_fechas_no_resuelve_proveedor(make_ctx):
    provider = _FakeTravelProvider()
    tool = BuscarHotelesTool(provider_resolver=_resolver_fijo(provider))

    resultado = await tool.run(make_ctx(), {"ciudad": "PAR", "checkin": "", "checkout": ""})

    assert "fecha" in resultado.content.lower()
    assert provider.llamadas_hoteles == []


async def test_buscar_hoteles_ok(make_ctx):
    provider = _FakeTravelProvider(hoteles=[_OFERTA_HOTEL])
    tool = BuscarHotelesTool(provider_resolver=_resolver_fijo(provider))

    resultado = await tool.run(
        make_ctx(), {"ciudad": "par", "checkin": "2026-09-01", "checkout": "2026-09-05"}
    )

    assert "Hotel Centro" in resultado.content
    assert resultado.data["ofertas"][0]["nombre"] == "Hotel Centro"
    assert resultado.presentation[0]["actions"][0]["action"] == "open_url"
    assert provider.llamadas_hoteles == [("PAR", "2026-09-01", "2026-09-05", 1)]


async def test_buscar_hoteles_error_del_proveedor_no_revienta(make_ctx):
    provider = _FakeTravelProvider(error=RuntimeError("Amadeus caído"))
    tool = BuscarHotelesTool(provider_resolver=_resolver_fijo(provider))

    resultado = await tool.run(
        make_ctx(), {"ciudad": "PAR", "checkin": "2026-09-01", "checkout": "2026-09-05"}
    )

    assert "no pude buscar hoteles" in resultado.content.lower()


# ---------------------------------------------------------------------------
# estado_vuelo
# ---------------------------------------------------------------------------


async def test_estado_vuelo_sin_carrier_no_resuelve_proveedor(make_ctx):
    provider = _FakeTravelProvider(estado=_ESTADO)
    tool = EstadoVueloTool(provider_resolver=_resolver_fijo(provider))

    resultado = await tool.run(make_ctx(), {"carrier": "", "numero": "123", "fecha": "2026-08-01"})

    assert "aerolínea" in resultado.content.lower()


async def test_estado_vuelo_ok_incluye_terminal_y_puerta(make_ctx):
    provider = _FakeTravelProvider(estado=_ESTADO)
    tool = EstadoVueloTool(provider_resolver=_resolver_fijo(provider))

    resultado = await tool.run(
        make_ctx(), {"carrier": "av", "numero": "123", "fecha": "2026-08-01"}
    )

    assert "Terminal de salida: 1" in resultado.content
    assert "Puerta: B12" in resultado.content
    assert resultado.data["carrier"] == "AV"


async def test_estado_vuelo_error_del_proveedor_no_revienta(make_ctx):
    provider = _FakeTravelProvider(error=RuntimeError("no encontrado"))
    tool = EstadoVueloTool(provider_resolver=_resolver_fijo(provider))

    resultado = await tool.run(
        make_ctx(), {"carrier": "AV", "numero": "999", "fecha": "2026-08-01"}
    )

    assert "no pude consultar" in resultado.content.lower()


# ---------------------------------------------------------------------------
# rastrear_paquete
# ---------------------------------------------------------------------------


async def test_rastrear_paquete_sin_tracking_number_no_resuelve_proveedor(make_ctx):
    provider = _FakeTrackingProvider(rastreo=_RASTREO)
    tool = RastrearPaqueteTool(provider_resolver=_resolver_fijo(provider))

    resultado = await tool.run(make_ctx(), {"tracking_number": ""})

    assert "número de guía" in resultado.content.lower()
    assert provider.llamadas == []


async def test_rastrear_paquete_ok_incluye_checkpoints(make_ctx):
    provider = _FakeTrackingProvider(rastreo=_RASTREO)
    tool = RastrearPaqueteTool(provider_resolver=_resolver_fijo(provider))

    resultado = await tool.run(make_ctx(), {"tracking_number": "999", "courier_slug": "dhl"})

    assert "InTransit" in resultado.content
    assert "Recogido" in resultado.content
    assert provider.llamadas == [("999", "dhl")]
    assert resultado.data["estado"] == "InTransit"


async def test_rastrear_paquete_courier_slug_opcional(make_ctx):
    provider = _FakeTrackingProvider(rastreo=_RASTREO)
    tool = RastrearPaqueteTool(provider_resolver=_resolver_fijo(provider))

    await tool.run(make_ctx(), {"tracking_number": "999"})

    assert provider.llamadas == [("999", None)]


async def test_rastrear_paquete_error_del_proveedor_no_revienta(make_ctx):
    provider = _FakeTrackingProvider(error=RuntimeError("AfterShip caído"))
    tool = RastrearPaqueteTool(provider_resolver=_resolver_fijo(provider))

    resultado = await tool.run(make_ctx(), {"tracking_number": "999"})

    assert "no pude rastrear" in resultado.content.lower()


# ---------------------------------------------------------------------------
# preparar_reserva — validaciones (nunca tocan la sesión si fallan)
# ---------------------------------------------------------------------------


async def test_preparar_reserva_tipo_invalido_no_toca_la_sesion(make_ctx, make_session):
    session = make_session([])
    resultado = await PrepararReservaTool().run(
        make_ctx(session=session), {"tipo": "auto", "descripcion": "X", "monto": 10}
    )
    assert "'tipo'" in resultado.content
    assert session.llamadas == []


async def test_preparar_reserva_sin_descripcion_no_toca_la_sesion(make_ctx, make_session):
    session = make_session([])
    resultado = await PrepararReservaTool().run(
        make_ctx(session=session), {"tipo": "vuelo", "descripcion": "   ", "monto": 10}
    )
    assert "descripción" in resultado.content.lower()
    assert session.llamadas == []


async def test_preparar_reserva_monto_invalido_no_toca_la_sesion(make_ctx, make_session):
    session = make_session([])
    resultado = await PrepararReservaTool().run(
        make_ctx(session=session),
        {"tipo": "vuelo", "descripcion": "Vuelo X", "monto": "no-es-numero"},
    )
    assert "monto válido" in resultado.content
    assert session.llamadas == []


async def test_preparar_reserva_monto_no_positivo_no_toca_la_sesion(make_ctx, make_session):
    session = make_session([])
    resultado = await PrepararReservaTool().run(
        make_ctx(session=session), {"tipo": "hotel", "descripcion": "Hotel X", "monto": -5}
    )
    assert "mayor que cero" in resultado.content
    assert session.llamadas == []


async def test_preparar_reserva_moneda_invalida_cae_a_usd(make_ctx, make_session):
    session = make_session([[{"id": "draft-1"}]])
    resultado = await PrepararReservaTool().run(
        make_ctx(session=session),
        {"tipo": "vuelo", "descripcion": "Vuelo X", "monto": 100, "moneda": "dolares"},
    )
    assert resultado.data["order_id"] == "draft-1"
    assert session.llamadas[0][1]["moneda"] == "USD"


# ---------------------------------------------------------------------------
# El test explícito que pide el paquete de trabajo.
# ---------------------------------------------------------------------------


async def test_preparar_reserva_nunca_llama_a_amadeus_solo_crea_draft(make_ctx, make_session):
    session = make_session([[{"id": "draft-42"}]])
    ctx = make_ctx(session=session)

    resultado = await PrepararReservaTool().run(
        ctx,
        {
            "tipo": "vuelo",
            "descripcion": "Vuelo BOG-MIA 2026-08-01, AV",
            "monto": 199.0,
            "moneda": "usd",
            "oferta_id": "off-1",
        },
    )

    assert resultado.data["order_id"] == "draft-42"
    assert resultado.content == _MENSAJE_RESERVA_CREADA
    assert "Nada está reservado ni pagado" in resultado.content

    # La ÚNICA acción sobre la base de datos es un INSERT con kind='purchase' y
    # status='draft' — ninguna llamada de red, ningún TravelProvider involucrado.
    assert len(session.llamadas) == 1
    sql, params = session.llamadas[0]
    assert "INSERT INTO orders" in sql
    assert "'purchase'" in sql
    assert "'draft'" in sql
    assert params["tenant_id"] == str(ctx.tenant_id)
    assert params["user_id"] == str(ctx.user_id)
    assert params["descripcion"] == "Vuelo BOG-MIA 2026-08-01, AV"
    assert params["monto"] == Decimal("199.0")
    assert params["moneda"] == "USD"
    assert '"tipo": "vuelo"' in params["meta"]
    assert "off-1" in params["meta"]
    assert session.flushes == 1


# ---------------------------------------------------------------------------
# BARRIDO C (WP-V7-02, docs/cumplimiento/barrido-v7-viajes.md): evidencia vs.
# rollback de sesión. `preparar_reserva` es el ÚNICO sitio de escritura de este
# paquete además de las 4 credenciales de `viajes.py` (ya verificadas "Seguro" en
# `docs/cumplimiento/barrido-evidencia-v6.md`) — igual que `LanzarCampanaTool`
# (`premium/edecan_premium/tools.py`, ver ese mismo informe), una `Tool` nunca es
# dueña de `ctx.session`: la sesión vive y comitea a nivel de la REQUEST/turno
# completo, compartida con más tool calls y la persistencia de mensajes. La
# garantía real de que un `raise` posterior no se lleva puesto el INSERT ya
# ejecutado vive en `edecan_core.agent.Agent._run_turn` y
# `edecan_api.routers.conversations._stream_approved_confirmation`, que envuelven
# CADA `tool.run(ctx, args)` en su propio `try/except Exception` sin dejarlo
# escapar hacia el límite de la transacción HTTP -- por eso ninguna `Tool`, esta
# incluida, debe llamar `session.commit()` por su cuenta.
# ---------------------------------------------------------------------------


async def test_preparar_reserva_nunca_comitea_su_propia_sesion(make_ctx, make_session):
    """`FakeSession` (`conftest.py`) no expone `.commit()` a propósito -- si
    `PrepararReservaTool`/`_crear_reserva_draft` intentaran comitear, este test
    fallaría con `AttributeError` en vez de pasar en silencio. Se obtiene la
    instancia SIEMPRE vía la fixture `make_session` (nunca importando la clase
    `FakeSession` por nombre desde `conftest`): este paquete y `apps/api/tests`
    tienen cada uno su propio `conftest.py` SIN `__init__.py` -- cuando ambos
    directorios se recolectan en la MISMA sesión de pytest (como hace
    `docs/cumplimiento/barrido-v7-viajes.md` en su comando de verificación),
    `sys.modules["conftest"]` solo puede apuntar a UNO de los dos módulos, y
    `from conftest import FakeSession` aquí resolvería silenciosamente al
    `conftest` de `apps/api/tests` (sin esa clase) en vez del de este paquete."""
    sesion_vacia = make_session([])
    assert not hasattr(sesion_vacia, "commit")

    session = make_session([[{"id": "draft-1"}]])
    resultado = await PrepararReservaTool().run(
        make_ctx(session=session), {"tipo": "vuelo", "descripcion": "Vuelo X", "monto": 100}
    )

    assert resultado.data["order_id"] == "draft-1"


async def test_preparar_reserva_con_session_que_revienta_si_comitea_sigue_ok(
    make_ctx, make_session
):
    """Variante explícita del test de arriba: agrega, sobre la MISMA instancia que
    devuelve la fixture `make_session` (ver nota arriba sobre por qué no se
    subclasea `FakeSession` importada por nombre), un `.commit()` que SÍ existe
    pero revienta si se llama -- confirma, no solo por ausencia de atributo, que el
    camino feliz de `preparar_reserva` nunca intenta comitear."""
    session = make_session([[{"id": "draft-2"}]])

    async def _commit_que_revienta(self) -> None:  # pragma: no cover - solo si hay un bug
        raise AssertionError(
            "PrepararReservaTool/_crear_reserva_draft comiteó su propia sesión -- una "
            "Tool nunca es dueña de ctx.session (ver docs/cumplimiento/"
            "barrido-evidencia-v6.md, hallazgo LanzarCampanaTool)."
        )

    session.commit = types.MethodType(_commit_que_revienta, session)

    resultado = await PrepararReservaTool().run(
        make_ctx(session=session), {"tipo": "hotel", "descripcion": "Hotel Y", "monto": 50}
    )
    assert resultado.data["order_id"] == "draft-2"


async def test_crear_reserva_draft_row_none_lanza_runtimeerror_tras_el_insert_ya_ejecutado(
    make_session,
):
    """Caso defensivo (Postgres no devuelve la fila del `RETURNING id`, en la
    práctica inalcanzable para un INSERT simple sin `WHERE`): el propio `execute()`
    del INSERT ya corrió ANTES de este chequeo -- el `raise` nunca decide si hubo o
    no escritura, solo detecta la anomalía. El aislamiento real de esta excepción
    (que nunca deshaga un INSERT ya comiteado por otro camino) lo da el nivel de
    `Agent`/`conversations` descrito arriba, fuera de este paquete -- acá solo se
    ancla que la función SIGUE lanzando `RuntimeError` como documenta su propio
    docstring, y que lo hace DESPUÉS de flush(), nunca antes."""
    session = make_session([[]])  # RETURNING sin filas -> row is None

    with pytest.raises(RuntimeError, match="No se pudo crear el borrador de reserva"):
        await _crear_reserva_draft(
            session,
            tenant_id=uuid4(),
            user_id=uuid4(),
            descripcion="X",
            monto=Decimal("10"),
            moneda="USD",
            meta={},
        )

    assert len(session.llamadas) == 1
    assert session.flushes == 1  # el INSERT ya se ejecutó/flusheó antes del raise
