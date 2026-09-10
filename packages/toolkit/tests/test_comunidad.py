"""Tests de `edecan_toolkit.comunidad` — estado OAuth y borradores sociales."""

from __future__ import annotations

from edecan_toolkit.comunidad import (
    EstadoConectoresSocialesTool,
    ListarBorradoresSocialesTool,
)


async def test_estado_conectores_sin_cuentas(make_ctx, make_session):
    ctx = make_ctx(session=make_session([[]]))
    result = await EstadoConectoresSocialesTool().run(ctx, {})
    assert "Ninguna red social está conectada" in result.content
    assert result.data["conectadas"] == []
    assert "Perfil → Conectores" in result.content


async def test_estado_conectores_linkedin_conectado(make_ctx, make_session, make_vault):
    from types import SimpleNamespace

    filas = [
        {"connector_key": "linkedin", "id": "acc-li-123", "created_at": "2026-01-01"},
    ]
    ctx = make_ctx(
        session=make_session([filas]),
        vault=make_vault(bundle=SimpleNamespace(access_token="token-oauth-real")),
    )
    result = await EstadoConectoresSocialesTool().run(ctx, {})
    assert "linkedin" in result.data["conectadas"]
    assert "LinkedIn: conectada" in result.content
    assert "NO conectada" in result.content  # x/meta/youtube faltan


async def test_listar_borradores_vacio(make_ctx, make_session):
    ctx = make_ctx(session=make_session([[]]))
    result = await ListarBorradoresSocialesTool().run(ctx, {})
    assert "No hay borradores" in result.content
    assert result.data["borradores"] == []


async def test_listar_borradores_con_datos(make_ctx, make_session):
    filas = [
        {
            "draft_id": "linkedin-abc",
            "platform": "linkedin",
            "target": "personal",
            "status": "borrador",
            "excerpt": "Hola mundo desde Edecán",
            "image_file_id": None,
            "updated_at": "2026-09-07",
        }
    ]
    ctx = make_ctx(session=make_session([filas]))
    result = await ListarBorradoresSocialesTool().run(ctx, {"limite": 5})
    assert "linkedin-abc" in result.content
    assert len(result.data["borradores"]) == 1
