"""Tests de `edecan_ads.tools`: `ads_resumen` y `ads_preparar_campana`.

`test_ads_preparar_campana_nunca_llama_a_meta_solo_crea_draft` es el test
explícito que pide el paquete de trabajo: prueba, mirando exactamente qué SQL
se ejecutó, que la tool `dangerous=True` no hace NADA más que insertar un
borrador — nunca resuelve un `AdsProvider` ni llama a Meta.
"""

from __future__ import annotations

from decimal import Decimal

from edecan_ads.tools import AdsPrepararCampanaTool, AdsResumenTool


class _FakeProvider:
    def __init__(self, campanas=None, metricas=None, error: Exception | None = None) -> None:
        self._campanas = campanas if campanas is not None else []
        self._metricas = metricas or {"spend": "0", "impressions": "0", "clicks": "0"}
        self._error = error
        self.periodos_pedidos: list[str] = []

    async def list_campaigns(self):
        if self._error:
            raise self._error
        return self._campanas

    async def insights(self, date_preset: str = "last_30d"):
        self.periodos_pedidos.append(date_preset)
        if self._error:
            raise self._error
        return self._metricas

    async def create_campaign_paused(self, *args, **kwargs):  # pragma: no cover - no se usa aquí
        raise AssertionError("ads_resumen nunca debe crear campañas")


def _resolver_fijo(provider: _FakeProvider):
    """Factory de `provider_resolver` inyectable de `AdsResumenTool`: siempre
    devuelve `provider`, sin tocar `ctx.vault`/`ctx.session`."""

    async def _resolve(ctx):
        return provider

    return _resolve


# ---------------------------------------------------------------------------
# ads_resumen
# ---------------------------------------------------------------------------


async def test_ads_resumen_sin_campanas(make_ctx):
    provider = _FakeProvider(campanas=[])
    tool = AdsResumenTool(provider_resolver=_resolver_fijo(provider))
    resultado = await tool.run(make_ctx(), {})
    assert "no tienes campañas" in resultado.content.lower()
    assert resultado.data["campanas"] == []


async def test_ads_resumen_con_campanas_lista_nombres_y_metricas(make_ctx):
    provider = _FakeProvider(
        campanas=[{"name": "Campaña A", "status": "PAUSED", "objective": "OUTCOME_TRAFFIC"}],
        metricas={"spend": "12.50", "impressions": "1000", "clicks": "20"},
    )
    tool = AdsResumenTool(provider_resolver=_resolver_fijo(provider))

    resultado = await tool.run(make_ctx(), {"periodo": "last_7d"})

    assert "Campaña A" in resultado.content
    assert "12.50" in resultado.content
    assert provider.periodos_pedidos == ["last_7d"]
    assert resultado.data["metricas"]["spend"] == "12.50"


async def test_ads_resumen_periodo_por_defecto_es_last_30d(make_ctx):
    provider = _FakeProvider()
    tool = AdsResumenTool(provider_resolver=_resolver_fijo(provider))

    await tool.run(make_ctx(), {})

    assert provider.periodos_pedidos == ["last_30d"]


async def test_ads_resumen_error_del_proveedor_no_revienta(make_ctx):
    provider = _FakeProvider(error=RuntimeError("Meta caído"))
    tool = AdsResumenTool(provider_resolver=_resolver_fijo(provider))

    resultado = await tool.run(make_ctx(), {})

    assert "no pude consultar" in resultado.content.lower()
    assert resultado.data is None


# ---------------------------------------------------------------------------
# ads_preparar_campana — validaciones (nunca tocan la sesión si fallan)
# ---------------------------------------------------------------------------


async def test_preparar_campana_sin_nombre_no_toca_la_sesion(make_ctx, make_session):
    session = make_session([])
    resultado = await AdsPrepararCampanaTool().run(
        make_ctx(session=session), {"nombre": "  ", "objetivo": "OUTCOME_TRAFFIC"}
    )
    assert "nombre" in resultado.content.lower()
    assert session.llamadas == []


async def test_preparar_campana_sin_objetivo_no_toca_la_sesion(make_ctx, make_session):
    session = make_session([])
    resultado = await AdsPrepararCampanaTool().run(
        make_ctx(session=session), {"nombre": "Mi campaña", "objetivo": ""}
    )
    assert "objetivo" in resultado.content.lower()
    assert session.llamadas == []


async def test_preparar_campana_presupuesto_invalido_no_toca_la_sesion(make_ctx, make_session):
    session = make_session([])
    resultado = await AdsPrepararCampanaTool().run(
        make_ctx(session=session),
        {"nombre": "X", "objetivo": "OUTCOME_TRAFFIC", "presupuesto_diario": "no-es-numero"},
    )
    assert "presupuesto válido" in resultado.content
    assert session.llamadas == []


async def test_preparar_campana_presupuesto_no_positivo_no_toca_la_sesion(make_ctx, make_session):
    session = make_session([])
    resultado = await AdsPrepararCampanaTool().run(
        make_ctx(session=session),
        {"nombre": "X", "objetivo": "OUTCOME_TRAFFIC", "presupuesto_diario": -5},
    )
    assert "mayor que cero" in resultado.content
    assert session.llamadas == []


async def test_preparar_campana_moneda_invalida_cae_a_usd(make_ctx, make_session):
    session = make_session([[{"id": "draft-1"}]])
    resultado = await AdsPrepararCampanaTool().run(
        make_ctx(session=session),
        {"nombre": "X", "objetivo": "OUTCOME_TRAFFIC", "moneda": "us-dollars"},
    )
    assert resultado.data["draft_id"] == "draft-1"
    assert session.llamadas[0][1]["moneda"] == "USD"


# ---------------------------------------------------------------------------
# El test explícito que pide el paquete de trabajo.
# ---------------------------------------------------------------------------


async def test_preparar_campana_nunca_llama_a_meta_solo_crea_draft(make_ctx, make_session):
    session = make_session([[{"id": "draft-42"}]])
    ctx = make_ctx(session=session)

    resultado = await AdsPrepararCampanaTool().run(
        ctx,
        {
            "nombre": "Campaña de lanzamiento",
            "objetivo": "OUTCOME_TRAFFIC",
            "presupuesto_diario": 50,
            "moneda": "usd",
        },
    )

    assert resultado.data["draft_id"] == "draft-42"
    assert "NO se ha creado nada en Meta" in resultado.content

    # La ÚNICA acción sobre la base de datos es un INSERT con status='draft' —
    # ninguna llamada de red, ningún AdsProvider involucrado.
    assert len(session.llamadas) == 1
    sql, params = session.llamadas[0]
    assert "INSERT INTO ad_drafts" in sql
    assert "'draft'" in sql
    assert params["tenant_id"] == str(ctx.tenant_id)
    assert params["user_id"] == str(ctx.user_id)
    assert params["nombre"] == "Campaña de lanzamiento"
    assert params["objetivo"] == "OUTCOME_TRAFFIC"
    assert params["presupuesto_diario"] == Decimal("50")
    assert params["moneda"] == "USD"
    assert params["provider"] == "meta"
    assert session.flushes == 1
