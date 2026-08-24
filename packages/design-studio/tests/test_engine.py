"""Contrato del adaptador local al motor creativo de FyDesign.

Estos tests usan procesos stdio reales, pero solo contra ejecutables falsos
creados dentro de ``tmp_path``. Así cubren el borde que importa (argv, cwd,
entorno, protocolo, timeout y limite de salida) sin necesitar Node, red,
credenciales ni el repositorio privado del mantenedor.
"""

from __future__ import annotations

import json
import stat
import sys
import textwrap
from pathlib import Path

import pytest
from edecan_design_studio.engine import (
    FYDESIGN_CAPABILITIES,
    FYDESIGN_SECRET_ENV_ALLOWLIST,
    StudioEngineClient,
    StudioEngineConfig,
)
from edecan_design_studio.project_engine import ProjectEngineClient, ProjectEngineConfig

REMOTE_CAPABILITIES = frozenset(
    {
        "fydesign_ad_engine",
        "fydesign_ambassador",
        "fydesign_analyze_video",
        "fydesign_angles",
        "fydesign_animate",
        "fydesign_autoroute",
        "fydesign_batch",
        "fydesign_brands",
        "fydesign_campaign",
        "fydesign_clipper",
        "fydesign_edit",
        "fydesign_generate",
        "fydesign_image",
        "fydesign_influencer",
        "fydesign_instadump",
        "fydesign_instant",
        "fydesign_marketplace_card",
        "fydesign_moodboard",
        "fydesign_photo_dump",
        "fydesign_photodump",
        "fydesign_post",
        "fydesign_product_ad",
        "fydesign_product_photoshoot",
        "fydesign_product_shots",
        "fydesign_refine",
        "fydesign_register_brand",
        "fydesign_studio",
        "fydesign_storyboard",
        "fydesign_strategy",
        "fydesign_svg",
        "fydesign_talking_head",
        "fydesign_train_face",
        "fydesign_upscale",
        "fydesign_video",
        "fydesign_video_ad",
        "fydesign_virality",
    }
)
ALL_CAPABILITIES = REMOTE_CAPABILITIES | {"fydesign_health"}

ROUTING_CASES = (
    ("fydesign_generate", {"prompt": "x"}, {"prompt": "x"}),
    ("fydesign_brands", {}, {"list": True}),
    ("fydesign_register_brand", {"brand": "Acme"}, {"media": "register"}),
    ("fydesign_image", {"prompt": "x"}, {"media": "image"}),
    ("fydesign_post", {"prompt": "x"}, {"media": "post"}),
    (
        "fydesign_edit",
        {"inputImage": "/tmp/in.png", "prompt": "x"},
        {"media": "edit"},
    ),
    ("fydesign_campaign", {"prompt": "x"}, {"media": "campaign"}),
    (
        "fydesign_strategy",
        {"brief": "x"},
        {"media": "campaign", "godMode": True, "prompt": "x"},
    ),
    ("fydesign_svg", {"prompt": "x"}, {"media": "svg"}),
    ("fydesign_video", {"prompt": "x"}, {"media": "video"}),
    ("fydesign_video_ad", {"prompt": "x"}, {"media": "video-ad"}),
    ("fydesign_analyze_video", {"url": "https://example.test/v"}, {"media": "analyze"}),
    ("fydesign_clipper", {"url": "https://example.test/v"}, {"media": "clip"}),
    ("fydesign_ad_engine", {"prompt": "x"}, {"media": "ad-engine"}),
    ("fydesign_product_ad", {"prompt": "x"}, {"media": "product-ad"}),
    ("fydesign_influencer", {"action": "list"}, {"media": "persona"}),
    (
        "fydesign_talking_head",
        {"personaName": "Ada", "prompt": "x"},
        {"media": "talking-head"},
    ),
    (
        "fydesign_photo_dump",
        {"prompt": "x", "refImages": ["/tmp/in.png"]},
        {"media": "photo-dump"},
    ),
    ("fydesign_batch", {"prompt": "x"}, {"media": "batch"}),
    (
        "fydesign_studio",
        {"inputImage": "/tmp/in.png", "op": "relight"},
        {"media": "edit-pro"},
    ),
    ("fydesign_photodump", {"personaName": "Ada"}, {"media": "photodump"}),
    ("fydesign_instadump", {"inputImage": "/tmp/in.png"}, {"media": "instadump"}),
    (
        "fydesign_ambassador",
        {"personaName": "Ada", "prompt": "x"},
        {"media": "ambassador"},
    ),
    (
        "fydesign_train_face",
        {"refImages": ["/tmp/in.png"]},
        {"media": "train-face"},
    ),
    ("fydesign_storyboard", {"prompt": "x"}, {"media": "storyboard"}),
    ("fydesign_upscale", {"inputImage": "/tmp/in.png"}, {"media": "upscale"}),
    ("fydesign_animate", {"op": "animate"}, {"media": "animate"}),
    ("fydesign_refine", {"prompt": "x"}, {"media": "refine"}),
    (
        "fydesign_moodboard",
        {"refImages": ["/tmp/in.png"]},
        {"media": "moodboard"},
    ),
    ("fydesign_autoroute", {"prompt": "x"}, {"media": "autoroute"}),
    ("fydesign_virality", {"prompt": "x"}, {"media": "virality"}),
    ("fydesign_angles", {"inputImage": "/tmp/in.png"}, {"media": "angles"}),
    (
        "fydesign_product_shots",
        {"productImage": "/tmp/product.png"},
        {"media": "product-shots"},
    ),
    (
        "fydesign_product_photoshoot",
        {"productImage": "/tmp/product.png"},
        {"media": "product-photoshoot"},
    ),
    (
        "fydesign_marketplace_card",
        {"productImage": "/tmp/product.png"},
        {"media": "marketplace-card"},
    ),
    (
        "fydesign_instant",
        {"siteUrl": "https://example.test"},
        {"media": "instant"},
    ),
)


def _write_executable(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = textwrap.dedent(source).lstrip().replace(
        "#!/usr/bin/env python3", f"#!{sys.executable}"
    )
    path.write_text(rendered, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _fake_install(
    tmp_path: Path,
    *,
    remote_capabilities: frozenset[str] = REMOTE_CAPABILITIES,
) -> tuple[Path, Path]:
    # Los metacaracteres/espacios son deliberados: una implementacion que
    # construya un string para ``shell=True`` no puede superar este smoke.
    root = tmp_path / "Fy Design;not-a-shell-command"
    (root / "mcp").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "package.json").write_text('{"name":"fydesign"}', encoding="utf-8")
    (root / "mcp/fydesign-mcp.mjs").write_text("// fake MCP entry\n", encoding="utf-8")
    (root / "scripts/fydesign-gen.ts").write_text("// fake generator entry\n", encoding="utf-8")
    (root / "fake-tools.json").write_text(
        json.dumps(sorted(remote_capabilities)), encoding="utf-8"
    )

    node = tmp_path / "fake node;still-not-a-shell"
    _write_executable(
        node,
        r'''
        #!/usr/bin/env python3
        import json
        import os
        from pathlib import Path
        import sys

        cwd = Path.cwd()
        (cwd / "discovery-env.json").write_text(json.dumps(dict(os.environ)))
        names = json.loads((cwd / "fake-tools.json").read_text())
        tools = [
            {
                "name": name,
                "description": f"Capacidad real falsa: {name}",
                "inputSchema": {
                    "type": "object",
                    "properties": {"prompt": {"type": "string"}},
                },
            }
            for name in names
        ]

        for raw in sys.stdin:
            message = json.loads(raw)
            method = message.get("method")
            if method == "initialize":
                result = {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "fake-fydesign", "version": "test"},
                }
            elif method == "tools/list":
                result = {"tools": tools}
            elif method in {"notifications/initialized", "initialized"}:
                continue
            else:
                print(json.dumps({
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "error": {"code": -32601, "message": "unknown"},
                }), flush=True)
                continue
            print(json.dumps({
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "result": result,
            }), flush=True)
        ''',
    )

    tsx = root / "node_modules/.bin/tsx"
    _write_executable(
        tsx,
        r'''
        #!/usr/bin/env python3
        import json
        import os
        from pathlib import Path
        import sys
        import time

        cwd = Path.cwd()
        argv = sys.argv[1:]
        raw = sys.stdin.read()
        payload = json.loads(raw or argv[-1])
        (cwd / "generator-invocation.json").write_text(json.dumps({
            "argv": argv,
            "payload": payload,
            "env": dict(os.environ),
        }))

        prompt = str(payload.get("prompt", ""))
        if prompt == "__HANG__":
            time.sleep(5)
        if prompt == "__HUGE__":
            print(json.dumps({"blob": "x" * 100_000}))
            raise SystemExit(0)

        leaked_env_file = None
        if any(arg.startswith("--env-file") for arg in argv):
            env_file = cwd / ".env.local"
            if env_file.exists():
                leaked_env_file = env_file.read_text()

        print(json.dumps({
            "ok": True,
            "engine": "fake-fydesign-generator",
            "received": payload,
            "argv": argv,
            "cwd": str(cwd),
            "env": dict(os.environ),
            "leakedEnvFile": leaked_env_file,
        }))
        ''',
    )
    return root, node


def _client(
    root: Path,
    node: Path,
    *,
    timeout_seconds: float = 30,
    max_output_bytes: int = 256_000,
) -> StudioEngineClient:
    return StudioEngineClient(
        StudioEngineConfig(
            root=root,
            node_binary=node,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
        )
    )


def _project_client(tmp_path: Path) -> tuple[ProjectEngineClient, Path]:
    root = tmp_path / "Project Engine;not-a-shell-command"
    (root / "scripts").mkdir(parents=True)
    (root / "node_modules/tsx/dist").mkdir(parents=True)
    (root / "scripts/fydesign-project.ts").write_text("// fake project entry\n")
    (root / "node_modules/tsx/dist/cli.mjs").write_text("// fake tsx entry\n")
    node = tmp_path / "fake project node"
    _write_executable(
        node,
        r'''
        #!/usr/bin/env python3
        import json
        import os
        from pathlib import Path
        import sys

        payload = json.loads(sys.stdin.read())
        if payload.get("prompt") == "__FAIL__":
            print(
                f"failed at {Path.cwd()} TOKEN=super-secret use a valid project brief",
                file=sys.stderr,
            )
            raise SystemExit(2)
        print(json.dumps({
            "ok": True,
            "argv": sys.argv[1:],
            "payload": payload,
            "env": dict(os.environ),
        }))
        ''',
    )
    client = ProjectEngineClient(
        ProjectEngineConfig(
            root=root,
            output_dir=tmp_path / "project-output",
            state_dir=tmp_path / "project-state",
            node_binary=node,
            runtime_env={
                "CHROMIUM_PATH": "/runtime/chromium",
                "PLAYWRIGHT_BROWSERS_PATH": "/runtime/playwright",
                "FFMPEG_PATH": "/runtime/ffmpeg",
                "FFPROBE_PATH": "/runtime/ffprobe",
                "YTDLP_PATH": "/runtime/yt-dlp",
                "NOT_ALLOWED": "must-not-cross",
            },
        )
    )
    return client, root


def test_capability_allowlist_is_the_complete_observed_fydesign_surface() -> None:
    assert frozenset(FYDESIGN_CAPABILITIES) == ALL_CAPABILITIES
    assert len(FYDESIGN_CAPABILITIES) == 37
    assert "fydesign_health" not in REMOTE_CAPABILITIES


def test_secret_allowlist_is_explicit_and_excludes_ambient_cloud_secrets() -> None:
    assert "ANTHROPIC_API_KEY" in FYDESIGN_SECRET_ENV_ALLOWLIST
    assert "CLAUDE_USE_MAX" in FYDESIGN_SECRET_ENV_ALLOWLIST
    assert "AWS_SECRET_ACCESS_KEY" not in FYDESIGN_SECRET_ENV_ALLOWLIST
    assert "EDECAN_MASTER_KEY" not in FYDESIGN_SECRET_ENV_ALLOWLIST


@pytest.mark.asyncio
async def test_discover_does_real_mcp_handshake_and_adds_local_health(tmp_path: Path) -> None:
    root, node = _fake_install(tmp_path)

    discovered = await _client(root, node).discover()

    assert {capability["name"] for capability in discovered} == ALL_CAPABILITIES
    post = next(item for item in discovered if item["name"] == "fydesign_post")
    assert post["description"].startswith("Capacidad real falsa")
    assert post["inputSchema"]["type"] == "object"
    health = next(item for item in discovered if item["name"] == "fydesign_health")
    assert health["inputSchema"]["type"] == "object"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "remote_capabilities",
    [
        REMOTE_CAPABILITIES - {"fydesign_post"},
        REMOTE_CAPABILITIES | {"fydesign_surprise"},
    ],
)
async def test_discover_fails_closed_when_remote_surface_drifts(
    tmp_path: Path,
    remote_capabilities: frozenset[str],
) -> None:
    root, node = _fake_install(tmp_path, remote_capabilities=remote_capabilities)

    with pytest.raises(Exception, match=r"(?i)(capabil|allowlist|tool|superficie)"):
        await _client(root, node).discover()


@pytest.mark.asyncio
async def test_execute_runs_generator_directly_and_maps_capability_input(tmp_path: Path) -> None:
    root, node = _fake_install(tmp_path)

    result = await _client(root, node).execute(
        "fydesign_post",
        {"brand": "Edecan", "prompt": "Una pieza humana", "platform": "instagram-feed"},
    )

    assert result["ok"] is True
    assert result["engine"] == "fake-fydesign-generator"
    assert result["received"] == {
        "media": "post",
        "brand": "Edecan",
        "prompt": "Una pieza humana",
        "platform": "instagram-feed",
    }
    assert result["cwd"] == str(root)
    assert (root / "generator-invocation.json").is_file()
    assert result["argv"][0] == "scripts/fydesign-gen.ts"
    assert len(result["argv"]) == 1
    assert all(not arg.startswith("--env-file") for arg in result["argv"])


@pytest.mark.asyncio
async def test_execute_sends_large_payload_over_stdin_not_argv(tmp_path: Path) -> None:
    root, node = _fake_install(tmp_path)
    large_prompt = "diseño " + ("x" * 70_000)

    result = await _client(root, node).execute(
        "fydesign_post", {"prompt": large_prompt}
    )

    assert result["received"]["prompt"] == large_prompt
    assert result["argv"] == ["scripts/fydesign-gen.ts"]


@pytest.mark.asyncio
async def test_project_engine_uses_stdin_and_forwards_only_runtime_allowlist(
    tmp_path: Path,
) -> None:
    client, root = _project_client(tmp_path)
    large_prompt = "proyecto " + ("z" * 70_000)

    result = await client.execute("create", {"prompt": large_prompt})

    assert result["payload"] == {"action": "create", "prompt": large_prompt}
    assert result["argv"] == [
        str(root / "node_modules/tsx/dist/cli.mjs"),
        "scripts/fydesign-project.ts",
    ]
    for key in (
        "CHROMIUM_PATH",
        "PLAYWRIGHT_BROWSERS_PATH",
        "FFMPEG_PATH",
        "FFPROBE_PATH",
        "YTDLP_PATH",
    ):
        assert key in result["env"]
    assert "NOT_ALLOWED" not in result["env"]


@pytest.mark.asyncio
async def test_project_engine_error_is_actionable_and_redacts_paths_and_secrets(
    tmp_path: Path,
) -> None:
    client, root = _project_client(tmp_path)

    with pytest.raises(Exception) as raised:
        await client.execute("create", {"prompt": "__FAIL__"})

    message = str(raised.value)
    assert "use a valid project brief" in message
    assert "super-secret" not in message
    assert str(root) not in message


@pytest.mark.asyncio
async def test_bundled_runtime_executes_tsx_cli_with_configured_node(tmp_path: Path) -> None:
    root, node = _fake_install(tmp_path)
    tsx_cli = root / "node_modules/tsx/dist/cli.mjs"
    tsx_cli.parent.mkdir(parents=True)
    tsx_cli.write_text("// bundled tsx CLI\n", encoding="utf-8")
    (root / "playwright-browsers/chromium-test").mkdir(parents=True)
    _write_executable(
        node,
        r'''
        #!/usr/bin/env python3
        import json
        import os
        import sys

        argv = sys.argv[1:]
        print(json.dumps({"ok": True, "argv": argv, "env": dict(os.environ)}))
        ''',
    )

    result = await _client(root, node).execute(
        "fydesign_post", {"prompt": "Bundle autosuficiente"}
    )

    assert result["argv"][0] == str(tsx_cli)
    assert result["argv"][1] == "scripts/fydesign-gen.ts"
    assert result["env"]["PLAYWRIGHT_BROWSERS_PATH"] == str(
        root / "playwright-browsers"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("capability", "arguments", "expected"), ROUTING_CASES)
async def test_every_advertised_remote_capability_reaches_the_generator(
    tmp_path: Path,
    capability: str,
    arguments: dict[str, object],
    expected: dict[str, object],
) -> None:
    root, node = _fake_install(tmp_path)

    result = await _client(root, node).execute(capability, arguments)

    assert result["engine"] == "fake-fydesign-generator"
    assert expected.items() <= result["received"].items()


@pytest.mark.asyncio
async def test_execute_uses_only_explicit_allowlisted_credentials(tmp_path: Path) -> None:
    root, node = _fake_install(tmp_path)
    (root / ".env.local").write_text(
        "ANTHROPIC_API_KEY=from-env-file\nEDECAN_MASTER_KEY=from-env-file\n",
        encoding="utf-8",
    )

    result = await _client(root, node).execute(
        "fydesign_post",
        {"prompt": "Credenciales aisladas"},
        credentials={
            "ANTHROPIC_API_KEY": "from-vault",
            "CLAUDE_USE_MAX": "1",
            "EDECAN_MASTER_KEY": "must-not-cross",
            "AWS_SECRET_ACCESS_KEY": "must-not-cross-either",
        },
    )

    assert result["env"]["ANTHROPIC_API_KEY"] == "from-vault"
    assert result["env"]["CLAUDE_USE_MAX"] == "1"
    assert "EDECAN_MASTER_KEY" not in result["env"]
    assert "AWS_SECRET_ACCESS_KEY" not in result["env"]
    assert result["leakedEnvFile"] is None
    assert all(not arg.startswith("--env-file") for arg in result["argv"])


@pytest.mark.asyncio
async def test_discovery_does_not_inherit_process_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, node = _fake_install(tmp_path)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "ambient-secret")
    monkeypatch.setenv("EDECAN_MASTER_KEY", "ambient-master-key")

    await _client(root, node).discover()

    child_env = json.loads((root / "discovery-env.json").read_text(encoding="utf-8"))
    assert "AWS_SECRET_ACCESS_KEY" not in child_env
    assert "EDECAN_MASTER_KEY" not in child_env


@pytest.mark.asyncio
async def test_execute_rejects_non_allowlisted_capability_before_spawning(tmp_path: Path) -> None:
    root, node = _fake_install(tmp_path)

    with pytest.raises(Exception, match=r"(?i)(capabil|allowlist|permit)"):
        await _client(root, node).execute("fydesign_run_arbitrary_command", {"command": "id"})

    assert not (root / "generator-invocation.json").exists()


@pytest.mark.asyncio
async def test_health_is_synthetic_and_does_not_run_the_generator(tmp_path: Path) -> None:
    root, node = _fake_install(tmp_path)

    result = await _client(root, node).execute("fydesign_health", {})

    assert result["ok"] is True
    assert result["capabilities"] == 37
    assert not (root / "generator-invocation.json").exists()


@pytest.mark.asyncio
async def test_execute_enforces_timeout_and_terminates_the_engine(tmp_path: Path) -> None:
    root, node = _fake_install(tmp_path)

    with pytest.raises(Exception, match=r"(?i)(timeout|tiempo|tard)"):
        await _client(root, node, timeout_seconds=0.05).execute(
            "fydesign_post", {"prompt": "__HANG__"}
        )


@pytest.mark.asyncio
async def test_execute_rejects_output_larger_than_configured_cap(tmp_path: Path) -> None:
    root, node = _fake_install(tmp_path)

    with pytest.raises(Exception, match=r"(?i)(output|salida|limit|grande)"):
        await _client(root, node, max_output_bytes=1_024).execute(
            "fydesign_post", {"prompt": "__HUGE__"}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["root", "mcp_script", "generator", "node", "tsx"])
async def test_runtime_validation_fails_before_use(tmp_path: Path, missing: str) -> None:
    root, node = _fake_install(tmp_path)
    if missing == "root":
        root = tmp_path / "missing-root"
    elif missing == "mcp_script":
        (root / "mcp/fydesign-mcp.mjs").unlink()
    elif missing == "generator":
        (root / "scripts/fydesign-gen.ts").unlink()
    elif missing == "node":
        node = tmp_path / "missing-node"
    elif missing == "tsx":
        (root / "node_modules/.bin/tsx").unlink()

    with pytest.raises(Exception, match=r"(?i)(fydesign|node|tsx|script|root|instal)"):
        client = _client(root, node)
        if missing in {"generator", "tsx"}:
            await client.execute("fydesign_post", {"prompt": "hola"})
        else:
            await client.discover()
