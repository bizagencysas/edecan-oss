"""La verificación de posts de LinkedIn — qué puede y qué NO puede saberse.

Historia en dos actos, ambos medidos en vivo:

02-ago-2026: LinkedIn respondió 201 + `x-restli-id` y el post no aparecía; se cableó
`organizationalEntityShareStatistics` como "oráculo de existencia" (post viejo → 200 con
elements; post fresco → 400).

06-ago-2026: un post de página VISIBLE en el feed, con 18+ horas de edad, seguía dando
400 "Unable to get activityIds" en ese mismo endpoint — la calibración del 02-ago
comparaba VIEJO contra FRESCO, no REAL contra FANTASMA. El oráculo convertía
publicaciones reales en errores "no se publicó nada", dejaba el borrador re-aprobable y
produjo posts DUPLICADOS en la página pública de Acme.

La verdad que estos tests fijan desde entonces:

* La relectura directa (`GET /rest/posts/{id}`) es la única señal: 200 → "confirmed",
  404 → "not_found" (real), 401/403 → "unknown" (para páginas es el estado NORMAL,
  la app no tiene el permiso partner de lectura).
* "unknown" JAMÁS se asciende a "confirmed" ni se degrada a fracaso: viaja tal cual y
  el llamador lo comunica como "enviado, no confirmable".
* `create_post` solo revienta con el 404 directo; con "unknown" devuelve el resultado.
* Ninguna llamada toca `organizationalEntityShareStatistics`: el oráculo no existe más.
"""

from __future__ import annotations

import httpx
import pytest
from edecan_connectors.base import ConnectorError
from edecan_connectors.social.linkedin import create_post, verify_post
from edecan_schemas.tokens import TokenBundle

_ORG = "urn:li:organization:123456789"
_SHARE = "urn:li:share:7491160666981023744"


def _bundle() -> TokenBundle:
    return TokenBundle(access_token="token-de-prueba")


def _cliente(handler) -> httpx.AsyncClient:  # noqa: ANN001
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_lectura_directa_200_confirma() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": _SHARE})

    async with _cliente(handler) as http:
        estado, nota = await verify_post(http, _bundle(), _SHARE, org_urn=_ORG)
    assert estado == "confirmed" and nota == ""


async def test_404_directo_es_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "not found"})

    async with _cliente(handler) as http:
        estado, nota = await verify_post(http, _bundle(), _SHARE, org_urn=_ORG)
    assert estado == "not_found"
    assert nota


async def test_403_es_unknown_y_no_consulta_estadisticas() -> None:
    """El caso normal de una PÁGINA: sin permiso de lectura, el único estado honesto
    es "unknown" — y NADIE llama al endpoint de estadísticas (medido el 06-ago: dice
    400 hasta para posts visibles; como detector de existencia es una moneda cargada)."""
    llamadas: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append(str(request.url))
        return httpx.Response(403, json={"message": "partnerApiPostsExternal.GET"})

    async with _cliente(handler) as http:
        estado, nota = await verify_post(http, _bundle(), _SHARE, org_urn=_ORG)
    assert estado == "unknown"
    assert nota
    assert all("ShareStatistics" not in u for u in llamadas)


async def test_403_sin_organizacion_tambien_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "sin permiso"})

    async with _cliente(handler) as http:
        estado, _ = await verify_post(http, _bundle(), _SHARE)
    assert estado == "unknown"


async def test_create_post_con_403_devuelve_unknown_sin_reventar() -> None:
    """El flujo real de la página de Acme: 201 + id, relectura 403 →
    `create_post` DEVUELVE el resultado con verified="unknown" (el post casi seguro
    existe; el 06-ago reventar aquí produjo duplicados al dejar el borrador
    re-aprobable). El "unknown" no se maquilla como "confirmed"."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and str(request.url).endswith("/rest/posts"):
            return httpx.Response(201, headers={"x-restli-id": _SHARE}, json={})
        return httpx.Response(403, json={"message": "partnerApiPostsExternal.GET"})

    async with _cliente(handler) as http:
        result = await create_post(
            http,
            _bundle(),
            text="Un post de prueba con contenido suficiente para publicarse.",
            org_urn=_ORG,
        )
    assert result["id"] == _SHARE
    assert result["verified"] == "unknown"
    assert result["verification_note"]


async def test_create_post_404_directo_si_revienta_honesto() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and str(request.url).endswith("/rest/posts"):
            return httpx.Response(201, headers={"x-restli-id": _SHARE}, json={})
        return httpx.Response(404, json={"message": "not found"})

    async with _cliente(handler) as http:
        with pytest.raises(ConnectorError) as exc:
            await create_post(
                http,
                _bundle(),
                text="Un post de prueba con contenido suficiente para publicarse.",
                org_urn=_ORG,
            )
    assert "404" in str(exc.value)
    assert "borrador sigue intacto" in str(exc.value)


async def test_upload_espera_la_imagen_hasta_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """La carrera medida el 31-jul y re-medida el 06-ago (~1.7s a AVAILABLE): adjuntar
    antes de que la imagen esté lista produce el 201-fantasma. `upload_image` sondea el
    estado y solo devuelve el urn cuando LinkedIn dice AVAILABLE."""
    from edecan_connectors.social import linkedin as mod

    pausas: list[float] = []

    async def sin_dormir(segundos: float) -> None:
        pausas.append(segundos)

    monkeypatch.setattr(mod.asyncio, "sleep", sin_dormir)
    estados = iter(["WAITING_UPLOAD", "WAITING_UPLOAD", "AVAILABLE"])

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "initializeUpload" in url:
            return httpx.Response(
                200,
                json={
                    "value": {"uploadUrl": "https://api.linkedin.com/up", "image": "urn:li:image:9"}
                },
            )
        if request.method == "PUT":
            return httpx.Response(201)
        return httpx.Response(200, json={"status": next(estados)})

    async with _cliente(handler) as http:
        urn = await mod.upload_image(
            http, _bundle(), owner=_ORG, content=b"png", content_type="image/png"
        )
    assert urn == "urn:li:image:9"
    assert len(pausas) == 2  # dos sondeos antes del AVAILABLE


async def test_upload_con_estado_ilegible_pausa_fijo_en_vez_de_saltar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El bug de la versión del 31-jul: si el estado no se podía leer (403), seguía SIN
    esperar — publicaba en plena carrera. Ahora: pausa fija y después continúa."""
    from edecan_connectors.social import linkedin as mod

    pausas: list[float] = []

    async def sin_dormir(segundos: float) -> None:
        pausas.append(segundos)

    monkeypatch.setattr(mod.asyncio, "sleep", sin_dormir)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "initializeUpload" in url:
            return httpx.Response(
                200,
                json={
                    "value": {"uploadUrl": "https://api.linkedin.com/up", "image": "urn:li:image:9"}
                },
            )
        if request.method == "PUT":
            return httpx.Response(201)
        return httpx.Response(403, json={"message": "sin permiso"})

    async with _cliente(handler) as http:
        urn = await mod.upload_image(
            http, _bundle(), owner=_ORG, content=b"png", content_type="image/png"
        )
    assert urn == "urn:li:image:9"
    assert pausas == [mod._IMAGEN_PAUSA_CIEGA_SEGUNDOS]
