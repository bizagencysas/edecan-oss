"""Tests de `edecan_toolkit.documentos`: `consultar_documentos`."""

from __future__ import annotations

from edecan_toolkit.documentos import ConsultarDocumentosTool


class _FakeEmbedder:
    """Doble local del protocolo `Embedder` de `edecan_core` (§10.7):
    `async embed(texts: list[str]) -> list[list[float]]`.
    """

    def __init__(self, vector: list[float] | None = None) -> None:
        self.vector = vector if vector is not None else [0.1, 0.2, 0.3]
        self.consultas: list[list[str]] = []

    async def embed(self, textos: list[str]) -> list[list[float]]:
        self.consultas.append(textos)
        return [self.vector for _ in textos]


async def test_consultar_documentos_sin_embedder_usa_ilike(make_ctx, make_session):
    fila = {"archivo": "contrato.pdf", "seq": 2, "texto": "cláusula de confidencialidad..."}
    session = make_session([[fila]])
    ctx = make_ctx(session=session, extras={})

    resultado = await ConsultarDocumentosTool().run(ctx, {"consulta": "confidencialidad"})

    assert "contrato.pdf" in resultado.content
    assert resultado.data["fragmentos"][0]["archivo"] == "contrato.pdf"
    sql, params = session.llamadas[0]
    assert "ILIKE" in sql
    assert params["patron"] == "%confidencialidad%"


async def test_consultar_documentos_con_embedder_usa_distancia_coseno(make_ctx, make_session):
    fila = {"archivo": "manual.pdf", "seq": 1, "texto": "instrucciones de instalación..."}
    session = make_session([[fila]])
    embedder = _FakeEmbedder(vector=[0.5, 0.25])
    ctx = make_ctx(session=session, extras={"memory_embedder": embedder})

    resultado = await ConsultarDocumentosTool().run(ctx, {"consulta": "cómo instalar"})

    assert embedder.consultas == [["cómo instalar"]]
    assert resultado.data["fragmentos"][0]["archivo"] == "manual.pdf"
    sql, params = session.llamadas[0]
    assert "<=>" in sql
    assert params["vector"] == "[0.5,0.25]"


async def test_consultar_documentos_sin_resultados(make_ctx, make_session):
    ctx = make_ctx(session=make_session([[]]), extras={})
    resultado = await ConsultarDocumentosTool().run(ctx, {"consulta": "no existe"})
    assert resultado.data["fragmentos"] == []
    assert "no encontré" in resultado.content.lower()


async def test_consultar_documentos_limite_se_acota_a_cinco(make_ctx, make_session):
    session = make_session([[]])
    ctx = make_ctx(session=session, extras={})
    await ConsultarDocumentosTool().run(ctx, {"consulta": "x", "limite": 50})
    _sql, params = session.llamadas[0]
    assert params["limite"] == 5


async def test_consultar_documentos_sin_consulta_no_toca_la_sesion(make_ctx, make_session):
    session = make_session([])
    resultado = await ConsultarDocumentosTool().run(make_ctx(session=session), {"consulta": "  "})
    assert session.llamadas == []
    assert "buscar" in resultado.content.lower()
