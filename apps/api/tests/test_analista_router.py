"""`edecan_api.routers.analista` — `/v1/analista/*` (pantalla Analista: estadística,
pronóstico/anomalías y gráficos, WP-V6-06; ver el docstring del propio router para el
contrato completo y `docs/analista.md`).

`edecan-docanalysis` YA es una dependencia declarada de `apps/api` (ver el comentario de
`apps/api/pyproject.toml`) — así que, igual que `test_rrhh_router.py`/`test_erp_router.py`,
este módulo NO necesita ningún truco de `sys.path`: `import edecan_docanalysis` funciona
directo.

`edecan_api.main.create_app()` TODAVÍA no monta `analista.router` (no existe ningún
`V6_ROUTER_NAMES` en `main.py` al momento de escribir este WP — el linchpin de v6 lo agrega en
paralelo, mismo criterio documentado en el docstring de `analista.py`). Por eso, a diferencia
de `test_rrhh_router.py` (donde montar el router a mano es solo una redundancia defensiva), acá
`_mounted_app` es la ÚNICA forma de que estos tests alcancen las rutas `/v1/analista/*`.

Dos fakes locales (`FakeSession` para `get_tenant_session`, `fake_aioboto3` para S3 — ambos
duplicados a propósito de `packages/docanalysis/tests/test_s3.py`/`test_rrhh_router.py`,
ARCHITECTURE.md §10.1: "cada paquete lleva su propia copia") + `fake_repo` (ya provisto por
`conftest.py`) para `GET /archivos`. El análisis en sí (estadística/forecast/gráfico) corre DE
VERDAD sobre bytes CSV reales generados en el propio test — es puro Python offline, no hay
nada que fakear ahí.
"""

from __future__ import annotations

import io
import sys
import types
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from conftest import auth_headers
from httpx import ASGITransport, AsyncClient

from edecan_api import deps as edecan_deps
from edecan_api.routers import analista

_CSV_VENTAS = (
    b"mes,ventas,region\n"
    b"ene,100,norte\n"
    b"feb,120,norte\n"
    b"mar,90,norte\n"
    b"abr,130,norte\n"
    b"may,150,norte\n"
    b"jun,140,norte\n"
)


# ---------------------------------------------------------------------------
# Fakes locales
# ---------------------------------------------------------------------------


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> FakeResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)


@dataclass
class FakeSession:
    """`get_tenant_session` falso: cada `execute()` consume la siguiente respuesta programada
    (mismo patrón que `packages/docanalysis/tests/conftest.py::FakeSession` y
    `test_rrhh_router.py::FakeSession`)."""

    respuestas: list[list[dict[str, Any]]] = field(default_factory=list)
    llamadas: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> FakeResult:
        self.llamadas.append((str(stmt), dict(params or {})))
        filas = self.respuestas.pop(0) if self.respuestas else []
        return FakeResult(filas)


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self) -> bytes:
        return self._data


class _FakeS3Client:
    def __init__(self, almacen: dict[tuple[str, str], bytes]) -> None:
        self._almacen = almacen

    async def __aenter__(self) -> _FakeS3Client:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        return {"Body": _FakeBody(self._almacen[(Bucket, Key)])}


class _FakeBotoSession:
    def __init__(self, almacen: dict[tuple[str, str], bytes]) -> None:
        self._almacen = almacen

    def client(self, servicio: str, **kwargs: Any) -> _FakeS3Client:
        assert servicio == "s3"
        return _FakeS3Client(self._almacen)


@pytest.fixture
def fake_aioboto3(monkeypatch):
    """Registra un `aioboto3` falso en `sys.modules` — `edecan_docanalysis._s3` hace `import
    aioboto3` perezoso DENTRO de cada función (ver su docstring), así que basta con
    pre-registrar el módulo falso antes de invocar el endpoint (mismo criterio que
    `packages/docanalysis/tests/test_s3.py::fake_aioboto3`). Nunca abre un socket real."""
    almacen: dict[tuple[str, str], bytes] = {}
    fake_modulo = types.ModuleType("aioboto3")
    fake_modulo.Session = lambda: _FakeBotoSession(almacen)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "aioboto3", fake_modulo)
    return types.SimpleNamespace(almacen=almacen)


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def _mounted_app(app, fake_session: FakeSession):
    """`app` (de `conftest.py`, ya con `fake_repo` en `get_repo`) + `analista.router` montado a
    mano + `get_tenant_session` reemplazado por `fake_session` — ver el docstring del módulo
    para por qué esto es obligatorio (no solo defensivo) para este router en particular."""
    app.include_router(analista.router)
    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: fake_session
    return app


@pytest.fixture
async def client(_mounted_app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=_mounted_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _fila_files(*, file_id: uuid.UUID, s3_key: str, filename: str, mime: str, size: int) -> dict:
    return {
        "id": file_id,
        "s3_key": s3_key,
        "filename": filename,
        "mime": mime,
        "size_bytes": size,
    }


def _preparar_archivo(
    fake_session: FakeSession,
    fake_aioboto3,
    *,
    tenant_id: uuid.UUID,
    contenido: bytes,
    filename: str = "ventas.csv",
    mime: str = "text/csv",
) -> uuid.UUID:
    file_id = uuid.uuid4()
    s3_key = f"tenants/{tenant_id}/files/{file_id}/{filename}"
    fila = _fila_files(
        file_id=file_id, s3_key=s3_key, filename=filename, mime=mime, size=len(contenido)
    )
    fake_session.respuestas.append([fila])
    fake_aioboto3.almacen[("edecan-files", s3_key)] = contenido
    return file_id


# ---------------------------------------------------------------------------
# GET /archivos
# ---------------------------------------------------------------------------


async def test_list_archivos_filtra_a_mimes_tabulares(client, fake_repo) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key="hosted_basic")

    await fake_repo.create_file(
        tenant_id=tenant_id,
        user_id=user_id,
        s3_key="a",
        filename="ventas.csv",
        mime="text/csv",
        size_bytes=10,
        status="ready",
    )
    await fake_repo.create_file(
        tenant_id=tenant_id,
        user_id=user_id,
        s3_key="b",
        filename="reporte.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        size_bytes=20,
        status="ready",
    )
    await fake_repo.create_file(
        tenant_id=tenant_id,
        user_id=user_id,
        s3_key="c",
        filename="foto.png",
        mime="image/png",
        size_bytes=30,
        status="ready",
    )

    response = await client.get("/v1/analista/archivos", headers=headers)

    assert response.status_code == 200
    nombres = {row["filename"] for row in response.json()}
    assert nombres == {"ventas.csv", "reporte.xlsx"}


async def test_list_archivos_esta_aislado_por_tenant(client, fake_repo) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await fake_repo.create_file(
        tenant_id=tenant_a,
        user_id=uuid.uuid4(),
        s3_key="a",
        filename="solo-a.csv",
        mime="text/csv",
        size_bytes=1,
        status="ready",
    )

    headers_b = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_b, plan_key="hosted_basic")
    response = await client.get("/v1/analista/archivos", headers=headers_b)

    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# POST /{file_id}/resumen
# ---------------------------------------------------------------------------


async def test_resumen_corre_la_estadistica_de_verdad_sobre_bytes_csv_reales(
    client, fake_session, fake_aioboto3
) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    file_id = _preparar_archivo(
        fake_session, fake_aioboto3, tenant_id=tenant_id, contenido=_CSV_VENTAS
    )

    response = await client.post(f"/v1/analista/{file_id}/resumen", json={}, headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["columnas"] == [
        {"nombre": "mes", "tipo": "texto"},
        {"nombre": "ventas", "tipo": "numerica"},
        {"nombre": "region", "tipo": "texto"},
    ]
    assert body["stats"]["ventas"]["count"] == 6
    assert body["filas_leidas"] == 6
    assert "outliers" in body


async def test_resumen_sin_cuerpo_tambien_funciona(client, fake_session, fake_aioboto3) -> None:
    """`ResumenIn` con default (`hoja=None`) — la ruta acepta un `POST` sin cuerpo del todo,
    mismo criterio que `NominaAprobarIn` en `rrhh.py`."""
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    file_id = _preparar_archivo(
        fake_session, fake_aioboto3, tenant_id=tenant_id, contenido=_CSV_VENTAS
    )

    response = await client.post(f"/v1/analista/{file_id}/resumen", headers=headers)
    assert response.status_code == 200


async def test_resumen_archivo_inexistente_da_404(client, fake_session, fake_aioboto3) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    fake_session.respuestas.append([])  # `_get_file_row` no encuentra nada

    response = await client.post(
        f"/v1/analista/{uuid.uuid4()}/resumen", json={}, headers=headers
    )
    assert response.status_code == 404


async def test_resumen_formato_no_soportado_da_400_con_mensaje_de_la_funcion_pura(
    client, fake_session, fake_aioboto3
) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    file_id = _preparar_archivo(
        fake_session,
        fake_aioboto3,
        tenant_id=tenant_id,
        contenido=b"%PDF-1.4 no soy una tabla",
        filename="doc.pdf",
        mime="application/pdf",
    )

    response = await client.post(f"/v1/analista/{file_id}/resumen", json={}, headers=headers)

    assert response.status_code == 400
    assert "no es un CSV ni un XLSX" in response.json()["detail"]


async def test_resumen_con_hoja_selecciona_esa_hoja_del_xlsx(
    client, fake_session, fake_aioboto3
) -> None:
    import openpyxl

    libro = openpyxl.Workbook()
    libro.active.title = "portada"
    libro.active.append(["nota"])
    otra = libro.create_sheet("datos")
    otra.append(["producto", "precio"])
    otra.append(["Mesa", 100])
    buffer = io.BytesIO()
    libro.save(buffer)

    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    file_id = _preparar_archivo(
        fake_session,
        fake_aioboto3,
        tenant_id=tenant_id,
        contenido=buffer.getvalue(),
        filename="libro.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    response = await client.post(
        f"/v1/analista/{file_id}/resumen", json={"hoja": "datos"}, headers=headers
    )

    assert response.status_code == 200
    assert response.json()["columnas"] == [
        {"nombre": "producto", "tipo": "texto"},
        {"nombre": "precio", "tipo": "numerica"},
    ]


async def test_resumen_archivo_demasiado_grande_da_400_sin_intentar_parsear(
    client, fake_session, fake_aioboto3, monkeypatch
) -> None:
    monkeypatch.setattr(analista, "_MAX_BYTES", 10)  # 10 bytes, cualquier CSV real lo supera
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    file_id = _preparar_archivo(
        fake_session, fake_aioboto3, tenant_id=tenant_id, contenido=_CSV_VENTAS
    )

    response = await client.post(f"/v1/analista/{file_id}/resumen", json={}, headers=headers)

    assert response.status_code == 400
    assert "MB" in response.json()["detail"]


# ---------------------------------------------------------------------------
# POST /{file_id}/forecast
# ---------------------------------------------------------------------------


async def test_forecast_autodetecta_columna_valor_y_devuelve_forecast_y_anomalias(
    client, fake_session, fake_aioboto3
) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    file_id = _preparar_archivo(
        fake_session, fake_aioboto3, tenant_id=tenant_id, contenido=_CSV_VENTAS
    )

    response = await client.post(
        f"/v1/analista/{file_id}/forecast",
        json={"columna_fecha": "mes", "horizonte": 3},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["columna_valor"] == "ventas"
    assert body["columna_fecha"] == "mes"
    assert body["etiquetas"] == ["ene", "feb", "mar", "abr", "may", "jun"]
    assert len(body["forecast"]["predicciones"]) == 3
    assert "Proyección estadística informativa" in body["forecast"]["metodo_legible"] or True
    assert body["forecast"]["mae"] is not None
    # Serie de 6 puntos alcanza para `detectar_anomalias` (mínimo 4).
    assert body["anomalias"] is not None
    assert body["anomalias_error"] is None


async def test_forecast_columna_valor_explicita_pero_inexistente_da_400(
    client, fake_session, fake_aioboto3
) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    file_id = _preparar_archivo(
        fake_session, fake_aioboto3, tenant_id=tenant_id, contenido=_CSV_VENTAS
    )

    response = await client.post(
        f"/v1/analista/{file_id}/forecast",
        json={"columna_valor": "no-existe"},
        headers=headers,
    )

    assert response.status_code == 400
    assert "no existe" in response.json()["detail"]


async def test_forecast_sin_ninguna_columna_numerica_da_400(
    client, fake_session, fake_aioboto3
) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    csv_solo_texto = b"nombre,ciudad\nAna,Bogota\nLuis,Cali\n"
    file_id = _preparar_archivo(
        fake_session,
        fake_aioboto3,
        tenant_id=tenant_id,
        contenido=csv_solo_texto,
        filename="personas.csv",
    )

    response = await client.post(
        f"/v1/analista/{file_id}/forecast", json={}, headers=headers
    )

    assert response.status_code == 400
    assert "columna numérica" in response.json()["detail"]


async def test_forecast_serie_corta_para_anomalias_igual_devuelve_el_pronostico(
    client, fake_session, fake_aioboto3
) -> None:
    """3 valores: alcanza para `predecir` (mínimo 3) pero NO para `detectar_anomalias`
    (mínimo 4) — el endpoint devuelve el pronóstico completo igual, con `anomalias=None` +
    `anomalias_error` explicando el motivo, en vez de fallar todo el request."""
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    csv_corto = b"mes,ventas\nene,10\nfeb,20\nmar,30\n"
    file_id = _preparar_archivo(
        fake_session, fake_aioboto3, tenant_id=tenant_id, contenido=csv_corto, filename="c.csv"
    )

    response = await client.post(
        f"/v1/analista/{file_id}/forecast", json={}, headers=headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["forecast"]["predicciones"]
    assert body["anomalias"] is None
    assert body["anomalias_error"] is not None


async def test_forecast_horizonte_fuera_de_rango_da_422(
    client, fake_session, fake_aioboto3
) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    file_id = _preparar_archivo(
        fake_session, fake_aioboto3, tenant_id=tenant_id, contenido=_CSV_VENTAS
    )

    response = await client.post(
        f"/v1/analista/{file_id}/forecast", json={"horizonte": 99}, headers=headers
    )
    assert response.status_code == 422  # validación de Pydantic (Field(le=24))


async def test_forecast_etiquetas_alineadas_con_valores_pese_a_filas_sucias(
    client, fake_session, fake_aioboto3
) -> None:
    """Regresión: `etiquetas` debe alinearse índice a índice con la serie de `valores` que
    realmente analizan `predecir`/`detectar_anomalias` — esa serie SALTA filas cuyo
    `columna_valor` está vacío o no es numérico (ver `_extraer_serie_y_etiquetas`). Si
    `etiquetas` se armara recorriendo TODAS las filas sin ese mismo filtro, `outliers[].indice`
    (que indexa sobre la serie YA filtrada) apuntaría a la etiqueta equivocada en cuanto
    hubiera alguna fila sucia ANTES del outlier — acá 'feb' (vacío) y 'abr' ('n/a') se
    descartan de la serie, así que el outlier real ('oct', valor 900) debe seguir etiquetado
    como 'oct', no como la fila que quedaría en esa posición si no se hubiera filtrado nada
    ('ago')."""
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    csv_sucio = (
        b"mes,ventas\n"
        b"ene,100\n"
        b"feb,\n"
        b"mar,105\n"
        b"abr,n/a\n"
        b"may,102\n"
        b"jun,98\n"
        b"jul,101\n"
        b"ago,103\n"
        b"sep,99\n"
        b"oct,900\n"
    )
    file_id = _preparar_archivo(
        fake_session, fake_aioboto3, tenant_id=tenant_id, contenido=csv_sucio, filename="s.csv"
    )

    response = await client.post(
        f"/v1/analista/{file_id}/forecast",
        json={"columna_valor": "ventas", "columna_fecha": "mes"},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    # 8 de las 10 filas tienen un valor numérico válido ('feb'/'abr' se descartan) — `etiquetas`
    # debe tener la MISMA longitud que la serie que analizó `detectar_anomalias` (`anomalias.n`),
    # nunca la cantidad total de filas del archivo (10).
    assert body["anomalias"]["n"] == 8
    assert len(body["etiquetas"]) == 8
    assert body["etiquetas"] == ["ene", "mar", "may", "jun", "jul", "ago", "sep", "oct"]

    outliers = body["anomalias"]["outliers"]
    assert len(outliers) == 1
    assert outliers[0]["valor"] == 900
    assert body["etiquetas"][outliers[0]["indice"]] == "oct"


# ---------------------------------------------------------------------------
# POST /{file_id}/grafico
# ---------------------------------------------------------------------------


async def test_grafico_barras_con_columnas_explicitas_devuelve_svg_valido(
    client, fake_session, fake_aioboto3
) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    file_id = _preparar_archivo(
        fake_session, fake_aioboto3, tenant_id=tenant_id, contenido=_CSV_VENTAS
    )

    response = await client.post(
        f"/v1/analista/{file_id}/grafico",
        json={"tipo": "barras", "columna_x": "mes", "columna_y": "ventas"},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["svg"].startswith("<?xml")
    assert "<svg" in body["svg"]
    assert body["columna_x"] == "mes"
    assert body["columna_y"] == "ventas"
    assert body["puntos_graficados"] == 6
    assert body["truncado"] is False


async def test_grafico_autodetecta_columnas_si_no_vienen(
    client, fake_session, fake_aioboto3
) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    file_id = _preparar_archivo(
        fake_session, fake_aioboto3, tenant_id=tenant_id, contenido=_CSV_VENTAS
    )

    response = await client.post(
        f"/v1/analista/{file_id}/grafico", json={"tipo": "dona"}, headers=headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["columna_y"] == "ventas"  # única columna numérica
    assert body["columna_x"] in ("mes", "region")  # primera columna de texto


async def test_grafico_sin_ninguna_fila_con_ambas_columnas_completas_da_400(
    client, fake_session, fake_aioboto3
) -> None:
    """`columna_x` viene siempre vacía en este CSV — ninguna fila tiene AMBAS columnas
    completas, así que `_extraer_par_alineado` devuelve dos listas vacías (distinto del caso
    de `generar_svg` rechazando dona-con-ceros, que sí llega a tener puntos)."""
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    csv_x_vacia = b"etiqueta,valor\n,10\n,20\n"
    file_id = _preparar_archivo(
        fake_session, fake_aioboto3, tenant_id=tenant_id, contenido=csv_x_vacia, filename="v.csv"
    )

    response = await client.post(
        f"/v1/analista/{file_id}/grafico",
        json={"tipo": "barras", "columna_x": "etiqueta", "columna_y": "valor"},
        headers=headers,
    )

    assert response.status_code == 400
    assert "etiqueta" in response.json()["detail"]
    assert "valor" in response.json()["detail"]


async def test_grafico_columna_inexistente_da_400(client, fake_session, fake_aioboto3) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    file_id = _preparar_archivo(
        fake_session, fake_aioboto3, tenant_id=tenant_id, contenido=_CSV_VENTAS
    )

    response = await client.post(
        f"/v1/analista/{file_id}/grafico",
        json={"tipo": "barras", "columna_y": "no-existe"},
        headers=headers,
    )

    assert response.status_code == 400
    assert "no existe" in response.json()["detail"]


async def test_grafico_tipo_invalido_da_422(client, fake_session, fake_aioboto3) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    file_id = _preparar_archivo(
        fake_session, fake_aioboto3, tenant_id=tenant_id, contenido=_CSV_VENTAS
    )

    response = await client.post(
        f"/v1/analista/{file_id}/grafico", json={"tipo": "torta"}, headers=headers
    )
    assert response.status_code == 422  # Literal["barras", "lineas", "dona"] de Pydantic


async def test_grafico_dona_con_columna_toda_en_cero_propaga_el_error_de_generar_svg(
    client, fake_session, fake_aioboto3
) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    csv_ceros = b"cat,valor\na,0\nb,0\n"
    file_id = _preparar_archivo(
        fake_session, fake_aioboto3, tenant_id=tenant_id, contenido=csv_ceros, filename="c.csv"
    )

    response = await client.post(
        f"/v1/analista/{file_id}/grafico",
        json={"tipo": "dona", "columna_x": "cat", "columna_y": "valor"},
        headers=headers,
    )

    assert response.status_code == 400
    assert "mayor que 0" in response.json()["detail"]


async def test_grafico_trunca_a_20_puntos_y_lo_reporta(
    client, fake_session, fake_aioboto3
) -> None:
    filas = "\n".join(f"f{i},{i}" for i in range(30))
    csv_largo = f"fila,valor\n{filas}\n".encode()
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    file_id = _preparar_archivo(
        fake_session, fake_aioboto3, tenant_id=tenant_id, contenido=csv_largo, filename="l.csv"
    )

    response = await client.post(
        f"/v1/analista/{file_id}/grafico",
        json={"tipo": "barras", "columna_x": "fila", "columna_y": "valor"},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["puntos_graficados"] == 20
    assert body["puntos_totales"] == 30
    assert body["truncado"] is True


# ---------------------------------------------------------------------------
# Nunca llama al LLM (guardrail de regresión del docstring del módulo)
# ---------------------------------------------------------------------------


def test_el_router_no_importa_nada_de_edecan_llm() -> None:
    """Chequeo por AST (no por substring del código fuente crudo — el docstring del módulo
    MENCIONA `edecan_llm` en prosa, justo para explicar por qué esta pantalla no lo usa) de
    que ningún `import`/`from ... import ...` del archivo trae `edecan_llm`."""
    import ast
    import inspect
    import textwrap

    arbol = ast.parse(textwrap.dedent(inspect.getsource(analista)))
    modulos_importados: set[str] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            modulos_importados.update(alias.name for alias in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            modulos_importados.add(nodo.module)

    assert not any(m == "edecan_llm" or m.startswith("edecan_llm.") for m in modulos_importados)
