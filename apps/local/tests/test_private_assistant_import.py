from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from edecan_core.memory import DEFAULT_EMBEDDINGS_DIM, HashEmbedder
from edecan_local.private_assistant_import import (
    _previous_phone_agent_names,
    _vector_literal,
    _write_private_documents,
    load_private_credentials,
    redact_secrets,
    scan_legacy_assistant,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _legacy_source(tmp_path: Path) -> Path:
    source = tmp_path / "legacy"
    _write(
        source / "identity" / "context.txt",
        "Nombre preferido: Persona de prueba\n"
        "Idioma preferido: Español\n"
        "Forma de trato: cercana\n\n"
        "2. Contexto\n",
    )
    _write(
        source / "persona" / "instructions.md",
        "# Identidad anterior\nContinuidad privada.",
    )
    _write(source / "writing" / "style.md", "Responde con claridad y criterio.")
    _write(source / "writing" / "corpus.txt", "Ejemplo de estilo privado.")
    _write(
        source / "memory" / "profile.json",
        json.dumps(
            {
                "facts": [
                    {"text": "Prefiere respuestas breves."},
                    {"text": "Su empresa de prueba se llama Acme."},
                    {"text": "prefiere   respuestas breves."},
                ]
            }
        ),
    )
    semantic = [
        {"t": "2026-01-01T00:00:00Z", "role": "user", "content": "Su meta es lanzar Acme."},
        {
            "t": "2026-01-02T00:00:00Z",
            "role": "assistant",
            "content": "Recuerda revisar el proyecto principal cada semana.",
        },
        {"t": "2026-01-03T00:00:00Z", "role": "user", "content": "Su meta es lanzar Acme."},
    ]
    _write(
        source / "memory" / "items.jsonl",
        "\n".join(json.dumps(item) for item in semantic),
    )
    _write(
        source / "calls" / "assistant.md",
        "Eres el agente de recepción.\nNunca inventes datos.\nAyuda con las llamadas.",
    )
    _write(
        source / "calls" / "sales.md",
        "Eres una asesora comercial.\nNo prometas precios no autorizados.",
    )
    _write(
        source / "calls" / "voice.json",
        json.dumps(
            {
                "assistant_voice_id": "voice-assistant",
                "assistant_opening_message": "Hola, habla Recepción.",
                "sales_voice_id": "voice-commercial",
            }
        ),
    )
    _write(source / "social" / "linkedin.md", "# VOZ\nCálida y directa.")
    _write(source / "social" / "x.md", "Territorio: Producto, tecnología")
    _write(
        source / "automations" / "schedules.json",
        json.dumps(
            [
                {
                    "id": "resumen_diario",
                    "hora": "09:30",
                    "dow": [0, 2],
                    "orden": "Prepara un resumen; nunca publiques automáticamente.",
                }
            ]
        ),
    )
    _write(
        source / "conversations" / "index.json",
        json.dumps([{"id": "principal", "title": "Conversación principal"}]),
    )
    _write(
        source / "conversations" / "items" / "principal.jsonl",
        "\n".join(
            [
                json.dumps({"role": "user", "content": "Hola", "t": "2026-01-01T00:00:00Z"}),
                json.dumps(
                    {
                        "role": "assistant",
                        "content": "Hola, ¿en qué ayudo?",
                        "t": "2026-01-01T00:00:01Z",
                    }
                ),
            ]
        ),
    )
    return source


def test_scan_imports_private_continuity_without_credentials(tmp_path: Path) -> None:
    plan = scan_legacy_assistant(
        _legacy_source(tmp_path),
        assistant_agent_name="Recepción",
        sales_agent_name="Comercial",
    )

    assert len(plan.facts) == 3
    assert plan.identity["nombre_preferido"] == "Persona de prueba"
    assert len(plan.phone_agents) == 2
    assert [agent.name for agent in plan.phone_agents] == ["Recepción", "Comercial"]
    assert [agent.voice_id for agent in plan.phone_agents] == [
        "voice-assistant",
        "voice-commercial",
    ]
    assert len(plan.automations) == 1
    assert plan.automations[0].rrule == "FREQ=DAILY;BYHOUR=9;BYMINUTE=30;BYDAY=MO,WE"
    assert len(plan.conversations) == 1
    assert "sigues siendo Edecán" in plan.persona_style
    assert "legacy-persona.md" in plan.private_documents
    assert "semantic-memory-corpus.jsonl" in plan.private_documents
    assert "Recuerda revisar" in plan.private_documents["semantic-memory-corpus.jsonl"]
    assert plan.credentials == []


def test_private_source_map_translates_custom_paths_and_python_constants(
    tmp_path: Path,
) -> None:
    source = _legacy_source(tmp_path)
    _write(source / "custom" / "persona.txt", "Persona personalizada preferida.")
    _write(
        source / "custom" / "reception.txt",
        "Eres el agente principal.\nAyuda con las llamadas.",
    )
    _write(
        source / "custom" / "commercial.txt",
        "Eres el agente comercial.\nAsesora comercial.",
    )
    _write(
        source / "custom" / "voice-source.py",
        "PRIMARY_VOICE = (os.getenv('VOICE') or 'voice-primary').strip()\n"
        "GREETING = 'Hola desde recepción.'\n"
        "SECONDARY_VOICES = {'commercial': 'voice-secondary'}\n"
        "IGNORED_SECRET = load_secret()\n",
    )
    _write(
        source / "custom" / "network-editorial.txt",
        "# VOZ\nPerfil personalizado preferido.",
    )
    source_map = tmp_path / "private-source-map.json"
    _write(
        source_map,
        json.dumps(
            {
                "schema_version": 1,
                "paths": {
                    "assistant_persona": "custom/persona.txt",
                    "assistant_call_prompt": "custom/reception.txt",
                    "sales_call_prompt": "custom/commercial.txt",
                    "voice_config": "custom/voice-source.py",
                    "linkedin_editorial": "custom/network-editorial.txt",
                },
                "python_constants": {
                    "assistant_voice_id": "PRIMARY_VOICE",
                    "assistant_opening_message": "GREETING",
                    "sales_voice_map": "SECONDARY_VOICES",
                },
            }
        ),
    )

    plan = scan_legacy_assistant(
        source,
        source_map=source_map,
        assistant_agent_name="Recepción",
        sales_agent_name="Comercial",
    )

    assert [agent.name for agent in plan.phone_agents] == ["Recepción", "Comercial"]
    assert [agent.voice_id for agent in plan.phone_agents] == [
        "voice-primary",
        "voice-secondary",
    ]
    assert "Persona personalizada preferida." in plan.persona_style
    assert "Identidad anterior" not in plan.persona_style
    assert (
        "Perfil personalizado preferido."
        in plan.private_documents["linkedin-editorial.md"]
    )
    assert "Cálida y directa." not in plan.private_documents["linkedin-editorial.md"]


def test_source_map_rejects_paths_outside_source(tmp_path: Path) -> None:
    source = _legacy_source(tmp_path)
    source_map = tmp_path / "private-source-map.json"
    _write(
        source_map,
        json.dumps(
            {
                "schema_version": 1,
                "paths": {"assistant_persona": "../outside.md"},
            }
        ),
    )

    with pytest.raises(ValueError, match="dentro de --source"):
        scan_legacy_assistant(source, source_map=source_map)


def test_private_credentials_require_env_references_and_hide_values(tmp_path: Path) -> None:
    env_path = tmp_path / "legacy.env"
    secret = "secret-value-that-must-not-be-printed"
    _write(env_path, f"VOICE_KEY={secret}\nACCOUNT_ID=account-public\n")
    map_path = tmp_path / "map.json"
    _write(
        map_path,
        json.dumps(
            {
                "schema_version": 1,
                "credentials": [
                    {
                        "required_env": ["VOICE_KEY"],
                        "connector_key": "voice_stt",
                        "external_account_id": "voice_stt",
                        "display_name": "Voz",
                        "access_token": {
                            "provider": "example",
                            "api_key": {"env": "VOICE_KEY"},
                        },
                        "scopes": ["example"],
                        "token_type": "config",
                    }
                ],
            }
        ),
    )

    credentials = load_private_credentials(env_path, map_path)

    assert len(credentials) == 1
    assert secret in credentials[0].access_token
    assert secret not in repr(credentials[0])


def test_private_credentials_reject_literal_secret(tmp_path: Path) -> None:
    env_path = tmp_path / "legacy.env"
    _write(env_path, "UNRELATED=value\n")
    map_path = tmp_path / "map.json"
    _write(
        map_path,
        json.dumps(
            {
                "schema_version": 1,
                "credentials": [
                    {
                        "connector_key": "voice_stt",
                        "external_account_id": "voice_stt",
                        "access_token": "literal-secret-must-not-live-here",
                    }
                ],
            }
        ),
    )

    with pytest.raises(ValueError, match="referenciar"):
        load_private_credentials(env_path, map_path)


def test_private_credentials_reject_literal_secret_default(tmp_path: Path) -> None:
    env_path = tmp_path / "legacy.env"
    _write(env_path, "UNRELATED=value\n")
    map_path = tmp_path / "map.json"
    _write(
        map_path,
        json.dumps(
            {
                "schema_version": 1,
                "credentials": [
                    {
                        "connector_key": "voice_stt",
                        "external_account_id": "voice_stt",
                        "access_token": {
                            "api_key": {
                                "env": "MISSING_VOICE_KEY",
                                "default": "literal-secret-must-not-live-here",
                            }
                        },
                    }
                ],
            }
        ),
    )

    with pytest.raises(ValueError, match="literal por defecto"):
        load_private_credentials(env_path, map_path)


def test_private_documents_are_outside_repo_style_and_owner_only(tmp_path: Path) -> None:
    count = _write_private_documents(
        tmp_path / "private-data",
        {"context.md": "privado"},
        phone_agent_names=["Recepción", "Ventas"],
    )
    root = tmp_path / "private-data" / "private-imports" / "legacy-assistant"

    assert count == 1
    assert root.joinpath("context.md").read_text() == "privado"
    assert os.stat(root).st_mode & 0o777 == 0o700
    assert os.stat(root / "context.md").st_mode & 0o777 == 0o600
    assert os.stat(root / "manifest.json").st_mode & 0o777 == 0o600
    manifest = json.loads(root.joinpath("manifest.json").read_text())
    assert manifest["phone_agent_names"] == ["Recepción", "Ventas"]


def test_previous_phone_agent_names_preserve_approved_overrides(tmp_path: Path) -> None:
    data_dir = tmp_path / "private-data"
    _write_private_documents(
        data_dir,
        {"context.md": "privado"},
        phone_agent_names=["Recepción", "Ventas"],
    )

    assert _previous_phone_agent_names(data_dir) == ("Recepción", "Ventas")


def test_previous_phone_agent_names_accept_old_manifest(tmp_path: Path) -> None:
    root = tmp_path / "private-data" / "private-imports" / "legacy-assistant"
    _write(root / "manifest.json", json.dumps({"schema_version": 1, "documents": []}))

    assert _previous_phone_agent_names(tmp_path / "private-data") == (None, None)


def test_redaction_removes_common_secret_shapes() -> None:
    value = "api_key=super-secret-value token: another-secret-value"
    redacted = redact_secrets(value)

    assert "super-secret-value" not in redacted
    assert "another-secret-value" not in redacted


@pytest.mark.asyncio
async def test_memory_embeddings_match_the_offline_runtime_format() -> None:
    embedder = HashEmbedder(dim=DEFAULT_EMBEDDINGS_DIM)
    [embedding] = await embedder.embed(["Una memoria privada recuperable."])
    literal = _vector_literal(embedding)

    assert len(embedding) == 1536
    assert literal.startswith("[")
    assert literal.endswith("]")
    assert len(literal[1:-1].split(",")) == 1536
    assert any(value != 0.0 for value in embedding)
