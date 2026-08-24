from edecan_toolkit.research import BuscarWebTool, SearchHit


async def test_buscar_web_compara_consultas_locales(make_ctx, monkeypatch) -> None:
    consultas: list[str] = []

    class Provider:
        name = "stub"

        async def search(self, query: str, k: int = 5) -> list[SearchHit]:
            consultas.append(query)
            return [SearchHit(title=query, url=f"https://{len(consultas)}.example", snippet="ok")]

    async def provider(_ctx):
        return Provider()

    monkeypatch.setattr("edecan_toolkit.research.get_tenant_search_provider", provider)
    result = await BuscarWebTool().run(make_ctx(), {"consulta": "barbería cerca de Chacao"})

    assert len(consultas) == 4
    assert result.data["local_search"]["distinct_sources"] == 4
    assert "horarios" in result.data["local_search"]["freshness_checks"]
    assert "cómo_llegar" in result.data["local_search"]["freshness_checks"]
    assert result.data["local_search"]["comparison_complete"] is True
    assert "cómo llegar mapa direcciones" in consultas[-1]
    provenance = result.data["search_provenance"]
    assert provenance["provider"] == "stub"
    assert provenance["mode"] == "demo"
    assert provenance["retrieved_at"].endswith("+00:00")
    assert len(provenance["queries"]) == 4


async def test_buscar_web_no_incluye_dominios_nulos_en_metadata_local(
    make_ctx, monkeypatch
) -> None:
    class Provider:
        name = "stub"

        async def search(self, query: str, k: int = 5) -> list[SearchHit]:
            del query, k
            return [SearchHit(title="sin URL", url="", snippet="ok")]

    async def provider(_ctx):
        return Provider()

    monkeypatch.setattr("edecan_toolkit.research.get_tenant_search_provider", provider)
    result = await BuscarWebTool().run(make_ctx(), {"consulta": "hotel cerca de Chacao"})

    assert result.data["local_search"]["source_domains"] == []
    assert result.data["local_search"]["distinct_sources"] == 0


async def test_busqueda_local_live_publica_checks_disponibilidad_sin_red_en_test(
    make_ctx, monkeypatch
) -> None:
    class Provider:
        name = "brave"

        async def search(self, query: str, k: int = 5) -> list[SearchHit]:
            del query, k
            return [SearchHit(title="Fuente", url="https://source.example/place", snippet="ok")]

    async def provider(_ctx):
        return Provider()

    async def check(url: str) -> dict[str, object]:
        return {"status": "reachable", "http_status": 200, "url_seen": url}

    monkeypatch.setattr("edecan_toolkit.research.get_tenant_search_provider", provider)
    monkeypatch.setattr("edecan_toolkit.research._check_source_availability", check)
    result = await BuscarWebTool().run(make_ctx(), {"consulta": "barbería en Caracas"})

    checks = result.data["local_search"]["availability_checks"]
    assert checks[0]["status"] == "reachable"
    assert result.data["local_search"]["availability_checks_complete"] is True


async def test_availability_bloquea_loopback(make_ctx) -> None:
    from edecan_toolkit.research import _check_source_availability

    del make_ctx
    result = await _check_source_availability("http://127.0.0.1:8000/private")
    assert result == {"status": "blocked", "reason": "private_or_non_global_host"}
