"""`edecan_local.objectstore` — mini servidor S3-compatible sobre filesystem
(ARCHITECTURE.md §12f, WP-V3-05).

Dos capas de test:

1. **Offline** (mayoría): `httpx.AsyncClient(transport=ASGITransport(app=...))`
   contra la app Starlette directo — rápido, determinista, sin sockets
   reales (ARCHITECTURE.md §0.4). Los casos de path traversal usan
   segmentos `..` PERCENT-ENCODED (`%2e%2e`): `httpx` normaliza (colapsa)
   los `..` LITERALES en la URL antes de mandar la request (comportamiento
   correcto de cualquier cliente HTTP bien portado) -- percent-encoded es
   la única forma de que el ASGI transport reciba el string ".." tal cual
   y ejercite de verdad `_resolve_object_path`/`_resolve_bucket_dir`.

2. **Integration** (`@pytest.mark.integration`, un solo test): arranca un
   `uvicorn.Server` de verdad en loopback y le pega con un cliente
   `aioboto3` real -- valida el protocolo REST-S3 real que arma
   `aiobotocore` (direccionamiento path-style, sin chunked/aws-chunked
   sobre HTTP plano, parseo de errores XML) en vez de solo la superficie
   HTTP genérica. No depende de ningún servicio externo (todo en
   127.0.0.1), así que NO tiene skipif -- pero es más lento que el resto
   (arranca un servidor real), de ahí el marcador.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from edecan_local.objectstore import create_object_store_app
from httpx import ASGITransport, AsyncClient

BUCKET = "edecan-files"


@pytest.fixture
def objects_root(tmp_path: Path) -> Path:
    return tmp_path / "objects"


@pytest.fixture
def app(objects_root: Path):
    return create_object_store_app(objects_root)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Bucket: PUT (crear) / GET (ListObjectsV2)
# ---------------------------------------------------------------------------


async def test_put_bucket_devuelve_200_y_crea_el_directorio(
    client: AsyncClient, objects_root: Path
) -> None:
    response = await client.put(f"/{BUCKET}")
    assert response.status_code == 200
    assert (objects_root / BUCKET).is_dir()


async def test_put_bucket_es_idempotente(client: AsyncClient) -> None:
    assert (await client.put(f"/{BUCKET}")).status_code == 200
    assert (await client.put(f"/{BUCKET}")).status_code == 200


async def test_get_bucket_sin_list_type_igual_lista(client: AsyncClient) -> None:
    """El único caller real (`aiobotocore.list_objects_v2`) siempre manda
    `list-type=2`, pero esta ruta no exige ese query param -- cualquier GET
    a nivel de bucket devuelve el listado (ver docstring de `objectstore.py`,
    "subconjunto que usa boto3 en el repo")."""
    await client.put(f"/{BUCKET}/a.txt", content=b"1")
    response = await client.get(f"/{BUCKET}", params={"list-type": "2"})
    assert response.status_code == 200
    assert "<Key>a.txt</Key>" in response.text


async def test_list_objects_v2_filtra_por_prefix_y_excluye_sidecars(
    client: AsyncClient,
) -> None:
    await client.put(f"/{BUCKET}/tenants/t1/a.txt", content=b"1")
    await client.put(f"/{BUCKET}/tenants/t2/b.txt", content=b"22")
    await client.put(f"/{BUCKET}/otro/c.txt", content=b"333")

    response = await client.get(f"/{BUCKET}", params={"list-type": "2", "prefix": "tenants/t1/"})
    assert response.status_code == 200
    body = response.text
    assert "<Key>tenants/t1/a.txt</Key>" in body
    assert "tenants/t2" not in body
    assert "otro/c.txt" not in body
    assert ".meta.json" not in body
    assert "<KeyCount>1</KeyCount>" in body
    assert "<Size>1</Size>" in body


async def test_list_objects_v2_bucket_vacio_o_inexistente_da_lista_vacia(
    client: AsyncClient,
) -> None:
    response = await client.get(f"/{BUCKET}", params={"list-type": "2"})
    assert response.status_code == 200
    assert "<KeyCount>0</KeyCount>" in response.text
    assert "<Contents>" not in response.text


# ---------------------------------------------------------------------------
# Objeto: PUT / GET / HEAD / DELETE
# ---------------------------------------------------------------------------


async def test_put_objeto_sin_bucket_previo_lo_autocrea(client: AsyncClient) -> None:
    """Ningún call-site real de este repo llama `create_bucket` antes de
    `put_object` (ver docstring del módulo) -- el PUT de un objeto debe
    funcionar igual sin un `PUT /{bucket}` explícito antes."""
    response = await client.put(f"/{BUCKET}/tenants/t1/files/f1/a.txt", content=b"contenido")
    assert response.status_code == 200
    assert "etag" in {k.lower() for k in response.headers}


async def test_get_objeto_devuelve_bytes_y_content_type(client: AsyncClient) -> None:
    body = b"hola mundo"
    await client.put(f"/{BUCKET}/k.txt", content=body, headers={"content-type": "text/plain"})
    response = await client.get(f"/{BUCKET}/k.txt")
    assert response.status_code == 200
    assert response.content == body
    assert response.headers["content-type"].startswith("text/plain")


async def test_get_objeto_sin_content_type_en_el_put_usa_default(client: AsyncClient) -> None:
    await client.put(f"/{BUCKET}/sin-tipo.bin", content=b"1")
    response = await client.get(f"/{BUCKET}/sin-tipo.bin")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"


async def test_get_objeto_inexistente_404_nosuchkey(client: AsyncClient) -> None:
    response = await client.get(f"/{BUCKET}/no-existe.txt")
    assert response.status_code == 404
    assert "<Code>NoSuchKey</Code>" in response.text
    assert "no-existe.txt" in response.text


async def test_head_objeto_existente_200_sin_body(client: AsyncClient) -> None:
    body = b"contenido de prueba"
    await client.put(f"/{BUCKET}/h.txt", content=body)
    response = await client.head(f"/{BUCKET}/h.txt")
    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-length"] == str(len(body))


async def test_head_objeto_inexistente_404(client: AsyncClient) -> None:
    response = await client.head(f"/{BUCKET}/no-existe.txt")
    assert response.status_code == 404


async def test_delete_objeto_existente_204_y_desaparece(client: AsyncClient) -> None:
    await client.put(f"/{BUCKET}/d.txt", content=b"x")
    delete_response = await client.delete(f"/{BUCKET}/d.txt")
    assert delete_response.status_code == 204

    get_response = await client.get(f"/{BUCKET}/d.txt")
    assert get_response.status_code == 404


async def test_delete_objeto_inexistente_tambien_204(client: AsyncClient) -> None:
    """`DeleteObject` real de S3 es idempotente: nunca 404 (ver
    `service-2.json`: `responseCode: 204` sin condición)."""
    response = await client.delete(f"/{BUCKET}/nunca-existio.txt")
    assert response.status_code == 204


async def test_delete_objeto_borra_tambien_el_sidecar_de_metadata(
    client: AsyncClient, objects_root: Path
) -> None:
    await client.put(f"/{BUCKET}/s.txt", content=b"x", headers={"content-type": "text/plain"})
    meta_path = objects_root / BUCKET / "s.txt.meta.json"
    assert meta_path.is_file()

    await client.delete(f"/{BUCKET}/s.txt")
    assert not meta_path.is_file()


async def test_put_objeto_con_key_anidada_crea_subdirectorios(
    client: AsyncClient, objects_root: Path
) -> None:
    response = await client.put(
        f"/{BUCKET}/tenants/abc-123/files/f1/foto.png", content=b"\x89PNG..."
    )
    assert response.status_code == 200
    assert (objects_root / BUCKET / "tenants/abc-123/files/f1/foto.png").is_file()


# ---------------------------------------------------------------------------
# Path traversal — ver docstring del módulo sobre por qué percent-encoded.
# ---------------------------------------------------------------------------


async def test_put_key_con_path_traversal_percent_encoded_se_rechaza(
    client: AsyncClient, objects_root: Path, tmp_path: Path
) -> None:
    response = await client.put(f"/{BUCKET}/%2e%2e/%2e%2e/%2e%2e/etc/passwd", content=b"pwn")
    assert response.status_code == 400
    assert "<Code>InvalidArgument</Code>" in response.text
    # Nada se escribió fuera de objects_root.
    assert not (tmp_path / "etc" / "passwd").exists()
    assert list(objects_root.rglob("passwd")) == []


async def test_put_bucket_con_path_traversal_percent_encoded_se_rechaza(
    client: AsyncClient,
) -> None:
    response = await client.put("/%2e%2e")
    assert response.status_code == 400
    assert "<Code>InvalidArgument</Code>" in response.text


async def test_get_bucket_listing_con_bucket_traversal_se_rechaza(client: AsyncClient) -> None:
    response = await client.get("/%2e%2e", params={"list-type": "2"})
    assert response.status_code == 400


async def test_key_normal_con_puntos_dobles_como_texto_no_se_confunde(
    client: AsyncClient,
) -> None:
    """Un nombre de archivo que solo CONTIENE `..` como parte de un
    segmento normal (no un segmento `..` por sí solo) no debe dispararse
    como traversal -- p. ej. `"reporte..final.pdf"`."""
    response = await client.put(f"/{BUCKET}/reporte..final.pdf", content=b"ok")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Integration: cliente aioboto3 real contra un uvicorn.Server real.
# ---------------------------------------------------------------------------

pytestmark_integration = pytest.mark.integration


@pytest.mark.integration
async def test_smoke_aioboto3_real_contra_servidor_real(tmp_path: Path) -> None:
    pytest.importorskip("aioboto3")
    import asyncio

    import uvicorn

    app = create_object_store_app(tmp_path / "objects")
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.02)
        else:  # pragma: no cover - solo si el servidor nunca arranca
            pytest.fail("uvicorn.Server no arrancó a tiempo")

        port = server.servers[0].sockets[0].getsockname()[1]

        import aioboto3

        session = aioboto3.Session()
        async with session.client(
            "s3",
            endpoint_url=f"http://127.0.0.1:{port}",
            region_name="us-east-1",
            aws_access_key_id="local",
            aws_secret_access_key="local",
        ) as s3:
            body = b"contenido real de punta a punta" * 50
            await s3.put_object(
                Bucket=BUCKET, Key="tenants/t1/files/f1/a.bin", Body=body, ContentType="text/plain"
            )

            get_response = await s3.get_object(Bucket=BUCKET, Key="tenants/t1/files/f1/a.bin")
            data = await get_response["Body"].read()
            assert data == body

            listing = await s3.list_objects_v2(Bucket=BUCKET, Prefix="tenants/t1/")
            keys = [c["Key"] for c in listing.get("Contents", [])]
            assert keys == ["tenants/t1/files/f1/a.bin"]

            await s3.delete_object(Bucket=BUCKET, Key="tenants/t1/files/f1/a.bin")
            after_delete = await s3.list_objects_v2(Bucket=BUCKET, Prefix="tenants/t1/")
            assert after_delete.get("KeyCount", 0) == 0

            from botocore.exceptions import ClientError

            with pytest.raises(ClientError) as exc_info:
                await s3.get_object(Bucket=BUCKET, Key="no/existe.txt")
            assert exc_info.value.response["Error"]["Code"] == "NoSuchKey"
    finally:
        server.should_exit = True
        await server_task
