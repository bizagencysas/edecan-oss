"""Tests de `edecan_toolkit.autoconfiguracion.ConfigurarCredencialTool`."""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import respx
from edecan_toolkit.autoconfiguracion import ConfigurarCredencialTool

_NUEVA_FILA = [{"id": "acc-nueva", "external_account_id": "x"}]


def test_dangerous_es_true():
    assert ConfigurarCredencialTool().dangerous is True


async def test_sin_vault_devuelve_error_sin_tocar_la_sesion(make_session, fake_settings):
    # `make_ctx` no permite `vault=None` (cae a un `FakeVault()` por
    # defecto) -- se arma el `SimpleNamespace` a mano para probar ese caso
    # límite, que sí es real en producción (`voice.py`: `vault: TokenVault |
    # None = Depends(get_vault)`).
    session = make_session([])
    ctx = SimpleNamespace(
        tenant_id=uuid4(),
        user_id=uuid4(),
        session=session,
        settings=fake_settings(),
        llm=None,
        vault=None,
        extras={},
    )
    resultado = await ConfigurarCredencialTool().run(
        ctx, {"tipo": "stripe", "campos": {"api_key": "rk_live_x"}}
    )
    assert "vault" in resultado.content.lower()
    assert session.llamadas == []


async def test_tipo_desconocido_devuelve_error(make_ctx, make_session):
    ctx = make_ctx(session=make_session([]))
    resultado = await ConfigurarCredencialTool().run(ctx, {"tipo": "bitcoin", "campos": {}})
    assert "desconocido" in resultado.content.lower()


@respx.mock
async def test_llm_openai_valida_descubre_modelos_y_guarda(
    make_ctx, make_session, make_vault
):
    respx.get("https://api.openai.com/v1/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"id": "gpt-example", "created": 2},
                    {"id": "gpt-example-mini", "created": 3},
                    {"id": "text-embedding-example", "created": 4},
                ]
            },
        )
    )
    session = make_session([[], _NUEVA_FILA])
    vault = make_vault()
    ctx = make_ctx(session=session, vault=vault)

    resultado = await ConfigurarCredencialTool().run(
        ctx,
        {"tipo": "llm", "campos": {"provider": "openai", "api_key": "sk-test"}},
    )

    assert "verifiqué OpenAI" in resultado.content
    config = json.loads(vault.puts[0][2].access_token)
    assert config == {
        "kind": "openai_compat",
        "api_key": "sk-test",
        "base_url": "https://api.openai.com/v1",
        "model_principal": "gpt-example",
        "model_rapido": "gpt-example-mini",
        "extra": {"provider_label": "openai"},
    }
    assert session.llamadas[-1][1]["connector_key"] == "llm"


@respx.mock
async def test_llm_rechazado_no_guarda_clave(make_ctx, make_session, make_vault):
    respx.get("https://api.deepseek.com/models").mock(
        return_value=httpx.Response(401, json={"error": "invalid"})
    )
    session = make_session([])
    vault = make_vault()
    ctx = make_ctx(session=session, vault=vault)

    resultado = await ConfigurarCredencialTool().run(
        ctx,
        {"tipo": "llm", "campos": {"provider": "deepseek", "api_key": "sk-test"}},
    )

    assert "No guardé nada" in resultado.content
    assert vault.puts == []
    assert session.llamadas == []


@respx.mock
async def test_images_openai_descubre_modelo_y_guarda(make_ctx, make_session, make_vault):
    respx.get("https://api.openai.com/v1/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"id": "gpt-text", "created": 10},
                    {"id": "gpt-image-2-2026-04-21", "created": 30},
                    {"id": "gpt-image-2", "created": 20},
                    {"id": "gpt-image-1", "created": 40},
                ]
            },
        )
    )
    session = make_session([[], _NUEVA_FILA])
    vault = make_vault()
    ctx = make_ctx(session=session, vault=vault)

    resultado = await ConfigurarCredencialTool().run(
        ctx,
        {"tipo": "images", "campos": {"provider": "openai", "api_key": "sk-test"}},
    )

    assert "crear imágenes" in resultado.content
    config = json.loads(vault.puts[0][2].access_token)
    assert config == {
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-test",
        "model": "gpt-image-2",
    }
    assert session.llamadas[-1][1]["connector_key"] == "images"


@respx.mock
async def test_images_openai_no_guarda_un_modelo_solicitado_que_no_existe(
    make_ctx, make_session, make_vault
):
    respx.get("https://api.openai.com/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "gpt-image-2"}]})
    )
    session = make_session([])
    vault = make_vault()
    ctx = make_ctx(session=session, vault=vault)

    resultado = await ConfigurarCredencialTool().run(
        ctx,
        {
            "tipo": "images",
            "campos": {
                "provider": "openai",
                "api_key": "sk-test",
                "model": "modelo-inventado",
            },
        },
    )

    assert "no anunció" in resultado.content
    assert vault.puts == []


async def test_voice_stt_guarda_deepgram(make_ctx, make_session, make_vault):
    session = make_session([[], _NUEVA_FILA])
    vault = make_vault()
    ctx = make_ctx(session=session, vault=vault)

    resultado = await ConfigurarCredencialTool().run(
        ctx, {"tipo": "voice_stt", "campos": {"api_key": "dg_key_123"}}
    )

    assert "deepgram" in resultado.content.lower()
    assert len(vault.puts) == 1
    _tenant, _account, bundle = vault.puts[0]
    config = json.loads(bundle.access_token)
    assert config == {"provider": "deepgram", "api_key": "dg_key_123"}


async def test_voice_stt_sin_api_key_no_toca_vault(make_ctx, make_session, make_vault):
    session = make_session([])
    vault = make_vault()
    ctx = make_ctx(session=session, vault=vault)
    resultado = await ConfigurarCredencialTool().run(ctx, {"tipo": "voice_stt", "campos": {}})
    assert "api_key" in resultado.content.lower()
    assert vault.puts == []
    assert session.llamadas == []


async def test_voice_tts_elevenlabs_guarda_provider_y_voice_id(make_ctx, make_session, make_vault):
    session = make_session([[], _NUEVA_FILA])
    vault = make_vault()
    ctx = make_ctx(session=session, vault=vault)

    resultado = await ConfigurarCredencialTool().run(
        ctx,
        {
            "tipo": "voice_tts",
            "campos": {"provider": "elevenlabs", "api_key": "el_key", "voice_id": "voz-1"},
        },
    )

    assert "elevenlabs" in resultado.content.lower()
    _tenant, _account, bundle = vault.puts[0]
    config = json.loads(bundle.access_token)
    assert config == {"provider": "elevenlabs", "api_key": "el_key", "voice_id": "voz-1"}


async def test_voice_tts_polly_fuera_de_modo_local_se_rechaza(
    make_ctx, make_session, make_vault, fake_settings
):
    ctx = make_ctx(
        session=make_session([]),
        vault=make_vault(),
        settings=fake_settings(EDECAN_LOCAL_MODE=False),
    )
    resultado = await ConfigurarCredencialTool().run(
        ctx, {"tipo": "voice_tts", "campos": {"provider": "polly"}}
    )
    assert "modo local" in resultado.content.lower()


async def test_voice_tts_polly_en_modo_local_se_acepta(
    make_ctx, make_session, make_vault, fake_settings
):
    session = make_session([[], _NUEVA_FILA])
    vault = make_vault()
    ctx = make_ctx(session=session, vault=vault, settings=fake_settings(EDECAN_LOCAL_MODE=True))
    resultado = await ConfigurarCredencialTool().run(
        ctx, {"tipo": "voice_tts", "campos": {"provider": "polly"}}
    )
    assert "polly" in resultado.content.lower()
    _tenant, _account, bundle = vault.puts[0]
    assert json.loads(bundle.access_token)["provider"] == "polly"


async def test_studio_guarda_fal_en_vault_local(
    make_ctx, make_session, make_vault, fake_settings
):
    session = make_session([[], _NUEVA_FILA])
    vault = make_vault()
    ctx = make_ctx(
        session=session,
        vault=vault,
        settings=fake_settings(EDECAN_LOCAL_MODE=True),
    )

    resultado = await ConfigurarCredencialTool().run(
        ctx,
        {
            "tipo": "studio",
            "campos": {"provider": "fal", "api_key": "fal-test-secret"},
        },
    )

    assert "conecté fal" in resultado.content
    assert session.llamadas[-1][1]["connector_key"] == "fydesign"
    config = json.loads(vault.puts[0][2].access_token)
    assert config == {"env": {"FAL_KEY": "fal-test-secret"}}
    assert vault.puts[0][2].token_type == "studio_config"


async def test_studio_mezcla_proveedores_sin_borrar_el_anterior(
    make_ctx, make_session, make_vault, fake_settings
):
    previous = SimpleNamespace(
        access_token=json.dumps({"env": {"FAL_KEY": "fal-existing"}})
    )
    session = make_session([[{"id": "acc-studio", "external_account_id": "fydesign"}]])
    vault = make_vault(bundle=previous)
    ctx = make_ctx(
        session=session,
        vault=vault,
        settings=fake_settings(EDECAN_LOCAL_MODE=True),
    )

    await ConfigurarCredencialTool().run(
        ctx,
        {
            "tipo": "studio",
            "campos": {"provider": "muapi", "api_key": "muapi-test-secret"},
        },
    )

    config = json.loads(vault.puts[0][2].access_token)
    assert config == {
        "env": {
            "FAL_KEY": "fal-existing",
            "MUAPI_API_KEY": "muapi-test-secret",
        }
    }


async def test_studio_guarda_github_para_aprender_repositorios(
    make_ctx, make_session, make_vault, fake_settings
):
    session = make_session([[], _NUEVA_FILA])
    vault = make_vault()
    ctx = make_ctx(
        session=session,
        vault=vault,
        settings=fake_settings(EDECAN_LOCAL_MODE=True),
    )

    await ConfigurarCredencialTool().run(
        ctx,
        {
            "tipo": "studio",
            "campos": {"provider": "github", "api_key": "github-test-secret"},
        },
    )

    config = json.loads(vault.puts[0][2].access_token)
    assert config == {"env": {"GITHUB_TOKEN": "github-test-secret"}}


async def test_studio_no_se_configura_fuera_de_la_app_local(
    make_ctx, make_session, make_vault, fake_settings
):
    session = make_session([])
    vault = make_vault()
    ctx = make_ctx(
        session=session,
        vault=vault,
        settings=fake_settings(EDECAN_LOCAL_MODE=False),
    )

    resultado = await ConfigurarCredencialTool().run(
        ctx,
        {
            "tipo": "studio",
            "campos": {"provider": "openai", "api_key": "sk-test-secret"},
        },
    )

    assert "app local" in resultado.content
    assert vault.puts == []
    assert session.llamadas == []


async def test_stripe_rechaza_secret_key(make_ctx, make_session, make_vault):
    session = make_session([])
    vault = make_vault()
    ctx = make_ctx(session=session, vault=vault)
    resultado = await ConfigurarCredencialTool().run(
        ctx, {"tipo": "stripe", "campos": {"api_key": "sk_live_muy_peligrosa"}}
    )
    assert "rk_" in resultado.content
    assert vault.puts == []
    assert session.llamadas == []


async def test_stripe_acepta_restricted_key(make_ctx, make_session, make_vault):
    session = make_session([[], _NUEVA_FILA])
    vault = make_vault()
    ctx = make_ctx(session=session, vault=vault)
    resultado = await ConfigurarCredencialTool().run(
        ctx, {"tipo": "stripe", "campos": {"api_key": "rk_live_ok"}}
    )
    assert "stripe" in resultado.content.lower()
    _tenant, _account, bundle = vault.puts[0]
    assert bundle.access_token == "rk_live_ok"


async def test_twilio_valida_formato_de_los_tres_campos(make_ctx, make_session, make_vault):
    ctx = make_ctx(session=make_session([]), vault=make_vault())
    resultado = await ConfigurarCredencialTool().run(
        ctx,
        {
            "tipo": "twilio",
            "campos": {
                "account_sid": "no-empieza-con-AC",
                "auth_token": "b" * 32,
                "phone_number": "+525512345678",
            },
        },
    )
    assert "account_sid" in resultado.content.lower()


async def test_twilio_guarda_credenciales_validas(make_ctx, make_session, make_vault):
    session = make_session([[], _NUEVA_FILA])
    vault = make_vault()
    ctx = make_ctx(session=session, vault=vault)
    resultado = await ConfigurarCredencialTool().run(
        ctx,
        {
            "tipo": "twilio",
            "campos": {
                "account_sid": "AC" + "a" * 32,
                "auth_token": "b" * 32,
                "phone_number": "+525512345678",
            },
        },
    )
    assert "twilio" in resultado.content.lower()
    _tenant, _account, bundle = vault.puts[0]
    assert bundle.access_token == "b" * 32
    assert bundle.scopes == ["AC" + "a" * 32]


async def test_whatsapp_guarda_access_token_y_phone_number_id(make_ctx, make_session, make_vault):
    # `_replace_connector_account`: SELECT (sin existente) + SELECT (dentro de
    # find_or_create) + INSERT.
    session = make_session([[], [], _NUEVA_FILA])
    vault = make_vault()
    ctx = make_ctx(session=session, vault=vault)
    resultado = await ConfigurarCredencialTool().run(
        ctx,
        {
            "tipo": "whatsapp",
            "campos": {"access_token": "a" * 25, "phone_number_id": "1234567890"},
        },
    )
    assert "whatsapp" in resultado.content.lower()
    _tenant, _account, bundle = vault.puts[0]
    assert bundle.access_token == "a" * 25


async def test_bot_token_requiere_conector_valido(make_ctx, make_session, make_vault):
    ctx = make_ctx(session=make_session([]), vault=make_vault())
    resultado = await ConfigurarCredencialTool().run(
        ctx, {"tipo": "bot_token", "conector": "whatsapp", "campos": {"bot_token": "x" * 20}}
    )
    assert "telegram" in resultado.content.lower() or "discord" in resultado.content.lower()


async def test_bot_token_guarda_token_de_telegram(make_ctx, make_session, make_vault):
    session = make_session([[], [], _NUEVA_FILA])
    vault = make_vault()
    ctx = make_ctx(session=session, vault=vault)
    resultado = await ConfigurarCredencialTool().run(
        ctx, {"tipo": "bot_token", "conector": "telegram", "campos": {"bot_token": "x" * 20}}
    )
    assert "telegram" in resultado.content.lower()
    _tenant, _account, bundle = vault.puts[0]
    assert bundle.access_token == "x" * 20


async def test_oauth_app_guarda_client_id_y_secret(make_ctx, make_session, make_vault):
    session = make_session([[], [], _NUEVA_FILA])
    vault = make_vault()
    ctx = make_ctx(session=session, vault=vault)
    resultado = await ConfigurarCredencialTool().run(
        ctx,
        {
            "tipo": "oauth_app",
            "conector": "google",
            "campos": {"client_id": "gid-123", "client_secret": "gsecret-456"},
        },
    )
    assert "google" in resultado.content.lower()
    sql_insert, params = session.llamadas[-1]
    assert "connector_accounts" in sql_insert
    assert params["connector_key"] == "google__app_config"
    assert params["external_account_id"] == "gid-123"
    _tenant, _account, bundle = vault.puts[0]
    assert bundle.access_token == "gsecret-456"


async def test_oauth_app_admite_linkedin(make_ctx, make_session, make_vault):
    session = make_session([[], [], _NUEVA_FILA])
    vault = make_vault()
    ctx = make_ctx(session=session, vault=vault)
    resultado = await ConfigurarCredencialTool().run(
        ctx,
        {
            "tipo": "oauth_app",
            "conector": "linkedin",
            "campos": {"client_id": "linkedin-id", "client_secret": "linkedin-secret"},
        },
    )
    assert "linkedin" in resultado.content.lower()
    assert session.llamadas[-1][1]["connector_key"] == "linkedin__app_config"


async def test_oauth_app_conector_invalido(make_ctx, make_session, make_vault):
    ctx = make_ctx(session=make_session([]), vault=make_vault())
    resultado = await ConfigurarCredencialTool().run(
        ctx, {"tipo": "oauth_app", "conector": "tiktok", "campos": {"client_id": "x"}}
    )
    assert "conector" in resultado.content.lower()


async def test_oauth_app_reconfigurar_reemplaza_la_fila_existente(
    make_ctx, make_session, make_vault
):
    # `_replace_connector_account` con una fila existente: SELECT (encuentra),
    # DELETE, SELECT (dentro de find_or_create, ya vacío), INSERT.
    fila_vieja = [{"id": "acc-vieja", "external_account_id": "gid-viejo"}]
    fila_nueva = [{"id": "acc-nueva", "external_account_id": "gid-nuevo"}]
    session = make_session([fila_vieja, [], [], fila_nueva])
    vault = make_vault()
    ctx = make_ctx(session=session, vault=vault)

    resultado = await ConfigurarCredencialTool().run(
        ctx,
        {"tipo": "oauth_app", "conector": "google", "campos": {"client_id": "gid-nuevo"}},
    )

    assert "google" in resultado.content.lower()
    sqls = [sql for sql, _params in session.llamadas]
    assert any("DELETE" in sql for sql in sqls)
    insert_params = session.llamadas[-1][1]
    assert insert_params["external_account_id"] == "gid-nuevo"


@respx.mock
async def test_alpaca_paper_valida_y_guarda_ambas_claves(make_ctx, make_session, make_vault):
    respx.get("https://paper-api.alpaca.markets/v2/account").mock(
        return_value=httpx.Response(200, json={"id": "paper-account"})
    )
    session = make_session([[], _NUEVA_FILA])
    vault = make_vault()
    ctx = make_ctx(session=session, vault=vault)

    resultado = await ConfigurarCredencialTool().run(
        ctx,
        {
            "tipo": "alpaca_paper",
            "campos": {
                "api_key_id": "PKTEST1234567890",
                "secret_key": "secret-value-1234567890",
            },
        },
    )

    assert "verifiqué" in resultado.content
    config = json.loads(vault.puts[0][2].access_token)
    assert config == {
        "environment": "paper",
        "api_key_id": "PKTEST1234567890",
        "secret_key": "secret-value-1234567890",
    }
    assert session.llamadas[-1][1]["connector_key"] == "alpaca_paper"


@respx.mock
async def test_alpaca_paper_rechazada_no_se_guarda(make_ctx, make_session, make_vault):
    respx.get("https://paper-api.alpaca.markets/v2/account").mock(
        return_value=httpx.Response(401, json={"message": "unauthorized"})
    )
    session = make_session([])
    vault = make_vault()
    ctx = make_ctx(session=session, vault=vault)

    resultado = await ConfigurarCredencialTool().run(
        ctx,
        {
            "tipo": "alpaca_paper",
            "campos": {
                "api_key_id": "PKTEST1234567890",
                "secret_key": "secret-value-1234567890",
            },
        },
    )

    assert "No guardé nada" in resultado.content
    assert vault.puts == []
    assert session.llamadas == []
