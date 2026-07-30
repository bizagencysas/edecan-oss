"""Flujo seguro de autorreparación local, siempre sobre repos temporales."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from edecan_toolkit.autorreparacion import (
    DiagnosticarAutorreparacionLocalTool,
    GestionarAutorreparacionLocalTool,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "source"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "repair@example.com")
    _git(repo, "config", "user.name", "Repair Test")
    (repo / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", "--", "feature.py")
    _git(repo, "commit", "-qm", "baseline")
    return repo


def _settings(repo: Path, data_dir: Path, **overrides) -> SimpleNamespace:
    values = {
        "EDECAN_LOCAL_MODE": True,
        "EDECAN_LOCAL_REPO_PATH": str(repo),
        "EDECAN_SELF_REPAIR_ENABLED": True,
        "EDECAN_SELF_REPAIR_TEST_COMMANDS_JSON": json.dumps(
            [
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; "
                    "assert 'VALUE = 2' in Path('feature.py').read_text()",
                ],
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; "
                    "assert 'VALUE = 3' in Path('feature.py').read_text()",
                ],
            ]
        ),
        "EDECAN_SELF_REPAIR_INSTALL_COMMANDS_JSON": "[]",
        "EDECAN_SELF_REPAIR_COMMAND_TIMEOUT_SECONDS": 30,
        "DATA_DIR": str(data_dir),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


async def _start(tool, ctx, *, intent="haz que funcione"):
    return await tool.run(
        ctx,
        {
            "accion": "iniciar",
            "intencion_original": intent,
            "fallo_reportado": "la capacidad respondió que no podía",
        },
    )


async def test_diagnostico_prioriza_skill_para_capacidad_faltante(make_ctx, tmp_path):
    repo = _repo(tmp_path)
    ctx = make_ctx(settings=_settings(repo, tmp_path / "state"))

    result = await DiagnosticarAutorreparacionLocalTool().run(
        ctx,
        {
            "intencion_original": "resume mis facturas",
            "fallo_reportado": "no existe esa capacidad",
            "categoria": "capacidad_faltante",
        },
    )

    assert result.data["route"] == "crear_o_actualizar_skill_local"
    assert result.data["source_repair_ready"] is True
    assert "skill local" in result.content.lower()


async def test_gestion_es_dangerous_y_apagada_por_defecto(make_ctx, tmp_path):
    repo = _repo(tmp_path)
    tool = GestionarAutorreparacionLocalTool()
    ctx = make_ctx(settings=_settings(repo, tmp_path / "state", EDECAN_SELF_REPAIR_ENABLED=False))

    result = await _start(tool, ctx)

    assert tool.dangerous is True
    assert "apagada" in result.content.lower()
    assert _git(repo, "status", "--porcelain") == ""


async def test_iniciar_rechaza_repo_con_cambios_del_usuario(make_ctx, tmp_path):
    repo = _repo(tmp_path)
    (repo / "feature.py").write_text("CAMBIO DEL USUARIO\n", encoding="utf-8")
    ctx = make_ctx(settings=_settings(repo, tmp_path / "state"))

    result = await _start(GestionarAutorreparacionLocalTool(), ctx)

    assert "cambios previos" in result.content.lower()
    assert (repo / "feature.py").read_text(encoding="utf-8") == "CAMBIO DEL USUARIO\n"


async def test_flujo_aislado_pruebas_commit_integracion_y_reversion(make_ctx, tmp_path):
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "origin", "HEAD:refs/heads/main")
    ctx = make_ctx(settings=_settings(repo, tmp_path / "state"))
    tool = GestionarAutorreparacionLocalTool()

    started = await _start(tool, ctx, intent="vuelve a intentar mi tarea")
    repair_id = started.data["repair_id"]
    assert _git(repo, "rev-parse", "HEAD") == baseline
    assert (repo / "feature.py").read_text(encoding="utf-8") == "VALUE = 1\n"

    original_sha = hashlib.sha256(b"VALUE = 1\n").hexdigest()
    changed = await tool.run(
        ctx,
        {
            "accion": "aplicar_cambios",
            "repair_id": repair_id,
            "cambios": [
                {
                    "ruta": "feature.py",
                    "sha256_esperado": original_sha,
                    "contenido": "VALUE = 2\n",
                }
            ],
        },
    )
    assert changed.data["changed_paths"] == ["feature.py"]
    assert (repo / "feature.py").read_text(encoding="utf-8") == "VALUE = 1\n"

    premature = await tool.run(
        ctx,
        {
            "accion": "crear_commit",
            "repair_id": repair_id,
            "mensaje": "sin pruebas",
            "rutas_esperadas": ["feature.py"],
        },
    )
    assert "hasta que las pruebas pasen" in premature.content

    command = json.loads(ctx.settings.EDECAN_SELF_REPAIR_TEST_COMMANDS_JSON)[0]
    tested = await tool.run(
        ctx,
        {"accion": "ejecutar_pruebas", "repair_id": repair_id, "comando": command},
    )
    assert tested.data["status"] == "tests_passed"

    manifest_path = tmp_path / "state" / "self-repair" / repair_id / "manifest.json"
    worktree = Path(json.loads(manifest_path.read_text())["worktree"])
    surprise = worktree / "cambio-inesperado.txt"
    surprise.write_text("no aprobado", encoding="utf-8")
    mismatched = await tool.run(
        ctx,
        {
            "accion": "crear_commit",
            "repair_id": repair_id,
            "mensaje": "no debe ocurrir",
            "rutas_esperadas": ["feature.py"],
        },
    )
    assert "no coinciden" in mismatched.content
    surprise.unlink()

    committed = await tool.run(
        ctx,
        {
            "accion": "crear_commit",
            "repair_id": repair_id,
            "mensaje": "fix: repair feature",
            "rutas_esperadas": ["feature.py"],
        },
    )
    assert committed.data["status"] == "committed"
    assert _git(repo, "rev-parse", "HEAD") == baseline

    integrated = await tool.run(ctx, {"accion": "integrar", "repair_id": repair_id})
    assert integrated.data == {
        "repair_id": repair_id,
        "status": "ready_to_retry",
        "retry_intent": "vuelve a intentar mi tarea",
    }
    assert (repo / "feature.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert _git(repo, "rev-parse", "HEAD") == committed.data["commit"]

    reverted = await tool.run(ctx, {"accion": "revertir", "repair_id": repair_id})
    assert reverted.data["status"] == "reverted"
    assert (repo / "feature.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert _git(repo, "status", "--porcelain") == ""
    remote_head = subprocess.run(
        ["git", "--git-dir", str(remote), "rev-parse", "refs/heads/main"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert remote_head == baseline, "la herramienta nunca debe hacer push"


async def test_hash_optimista_impide_pisar_cambio_concurrente(make_ctx, tmp_path):
    repo = _repo(tmp_path)
    ctx = make_ctx(settings=_settings(repo, tmp_path / "state"))
    tool = GestionarAutorreparacionLocalTool()
    repair_id = (await _start(tool, ctx)).data["repair_id"]

    result = await tool.run(
        ctx,
        {
            "accion": "aplicar_cambios",
            "repair_id": repair_id,
            "cambios": [
                {
                    "ruta": "feature.py",
                    "sha256_esperado": "0" * 64,
                    "contenido": "VALUE = 999\n",
                }
            ],
        },
    )

    assert "cambió desde el diagnóstico" in result.content
    manifest = json.loads(
        (tmp_path / "state" / "self-repair" / repair_id / "manifest.json").read_text()
    )
    assert Path(manifest["worktree"]).joinpath("feature.py").read_text() == "VALUE = 1\n"


async def test_comando_no_allowlisted_no_se_ejecuta(make_ctx, tmp_path):
    repo = _repo(tmp_path)
    marker = tmp_path / "owned"
    ctx = make_ctx(settings=_settings(repo, tmp_path / "state"))
    tool = GestionarAutorreparacionLocalTool()
    repair_id = (await _start(tool, ctx)).data["repair_id"]
    original_sha = hashlib.sha256(b"VALUE = 1\n").hexdigest()
    await tool.run(
        ctx,
        {
            "accion": "aplicar_cambios",
            "repair_id": repair_id,
            "cambios": [
                {
                    "ruta": "feature.py",
                    "sha256_esperado": original_sha,
                    "contenido": "VALUE = 2\n",
                }
            ],
        },
    )

    result = await tool.run(
        ctx,
        {
            "accion": "ejecutar_pruebas",
            "repair_id": repair_id,
            "comando": [sys.executable, "-c", f"open({str(marker)!r}, 'w').write('x')"],
        },
    )

    assert "no está autorizado" in result.content
    assert not marker.exists()


async def test_revertir_antes_de_integrar_descarta_solo_worktree(make_ctx, tmp_path):
    repo = _repo(tmp_path)
    ctx = make_ctx(settings=_settings(repo, tmp_path / "state"))
    tool = GestionarAutorreparacionLocalTool()
    repair_id = (await _start(tool, ctx)).data["repair_id"]

    result = await tool.run(ctx, {"accion": "revertir", "repair_id": repair_id})

    assert result.data["status"] == "reverted"
    assert (repo / "feature.py").read_text() == "VALUE = 1\n"
    assert _git(repo, "status", "--porcelain") == ""


async def test_reintento_exitoso_cierra_worktree_y_rama_temporal(make_ctx, tmp_path):
    repo = _repo(tmp_path)
    ctx = make_ctx(settings=_settings(repo, tmp_path / "state"))
    tool = GestionarAutorreparacionLocalTool()
    repair_id = (await _start(tool, ctx)).data["repair_id"]
    await tool.run(
        ctx,
        {
            "accion": "aplicar_cambios",
            "repair_id": repair_id,
            "cambios": [
                {
                    "ruta": "feature.py",
                    "sha256_esperado": hashlib.sha256(b"VALUE = 1\n").hexdigest(),
                    "contenido": "VALUE = 2\n",
                }
            ],
        },
    )
    command = json.loads(ctx.settings.EDECAN_SELF_REPAIR_TEST_COMMANDS_JSON)[0]
    await tool.run(ctx, {"accion": "ejecutar_pruebas", "repair_id": repair_id, "comando": command})
    await tool.run(
        ctx,
        {
            "accion": "crear_commit",
            "repair_id": repair_id,
            "mensaje": "repair",
            "rutas_esperadas": ["feature.py"],
        },
    )
    await tool.run(ctx, {"accion": "integrar", "repair_id": repair_id})
    manifest_path = tmp_path / "state" / "self-repair" / repair_id / "manifest.json"
    before = json.loads(manifest_path.read_text())
    worktree = Path(before["worktree"])
    branch = before["branch"]
    assert worktree.exists()

    result = await tool.run(
        ctx,
        {
            "accion": "registrar_reintento",
            "repair_id": repair_id,
            "reintento_exitoso": True,
            "evidencia": "la intención terminó correctamente",
        },
    )

    assert result.data["status"] == "completed"
    assert not worktree.exists()
    assert branch not in _git(repo, "branch", "--list", branch)
    after = json.loads(manifest_path.read_text())
    assert after["retry_runs"] == [
        {"success": True, "evidence": "la intención terminó correctamente"}
    ]


async def test_rollback_despues_de_dos_ciclos_restaura_checkpoint_completo(make_ctx, tmp_path):
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    ctx = make_ctx(settings=_settings(repo, tmp_path / "state"))
    tool = GestionarAutorreparacionLocalTool()
    repair_id = (await _start(tool, ctx)).data["repair_id"]
    commands = json.loads(ctx.settings.EDECAN_SELF_REPAIR_TEST_COMMANDS_JSON)

    for cycle, value in enumerate((2, 3)):
        current = f"VALUE = {value - 1}\n".encode()
        await tool.run(
            ctx,
            {
                "accion": "aplicar_cambios",
                "repair_id": repair_id,
                "cambios": [
                    {
                        "ruta": "feature.py",
                        "sha256_esperado": hashlib.sha256(current).hexdigest(),
                        "contenido": f"VALUE = {value}\n",
                    }
                ],
            },
        )
        tested = await tool.run(
            ctx,
            {
                "accion": "ejecutar_pruebas",
                "repair_id": repair_id,
                "comando": commands[cycle],
            },
        )
        assert tested.data["status"] == "tests_passed"
        await tool.run(
            ctx,
            {
                "accion": "crear_commit",
                "repair_id": repair_id,
                "mensaje": f"repair cycle {cycle + 1}",
                "rutas_esperadas": ["feature.py"],
            },
        )
        integrated = await tool.run(ctx, {"accion": "integrar", "repair_id": repair_id})
        assert integrated.data["status"] == "ready_to_retry"
        assert (repo / "feature.py").read_text() == f"VALUE = {value}\n"
        if cycle == 0:
            retry = await tool.run(
                ctx,
                {
                    "accion": "registrar_reintento",
                    "repair_id": repair_id,
                    "reintento_exitoso": False,
                    "evidencia": "la primera corrección no alcanzó",
                },
            )
            assert retry.data["status"] == "prepared"

    manifest_path = tmp_path / "state" / "self-repair" / repair_id / "manifest.json"
    before = json.loads(manifest_path.read_text())
    assert before["original_baseline"] == baseline
    assert len(before["integrated_commits"]) == 2
    worktree = Path(before["worktree"])
    branch = before["branch"]

    result = await tool.run(ctx, {"accion": "revertir", "repair_id": repair_id})

    assert result.data["status"] == "reverted"
    assert (repo / "feature.py").read_text() == "VALUE = 1\n"
    assert _git(repo, "status", "--porcelain") == ""
    assert not worktree.exists()
    assert branch not in _git(repo, "branch", "--list", branch)
    after = json.loads(manifest_path.read_text())
    assert after["reverted_commits"] == before["integrated_commits"]
    assert after["revert_commit"] == _git(repo, "rev-parse", "HEAD")
    assert "revert: self-repair" in _git(repo, "log", "-1", "--format=%s")


async def test_rollback_tras_reintento_fallido_restaura_el_primer_ciclo(make_ctx, tmp_path):
    repo = _repo(tmp_path)
    ctx = make_ctx(settings=_settings(repo, tmp_path / "state"))
    tool = GestionarAutorreparacionLocalTool()
    repair_id = (await _start(tool, ctx)).data["repair_id"]
    await tool.run(
        ctx,
        {
            "accion": "aplicar_cambios",
            "repair_id": repair_id,
            "cambios": [
                {
                    "ruta": "feature.py",
                    "sha256_esperado": hashlib.sha256(b"VALUE = 1\n").hexdigest(),
                    "contenido": "VALUE = 2\n",
                }
            ],
        },
    )
    command = json.loads(ctx.settings.EDECAN_SELF_REPAIR_TEST_COMMANDS_JSON)[0]
    await tool.run(ctx, {"accion": "ejecutar_pruebas", "repair_id": repair_id, "comando": command})
    await tool.run(
        ctx,
        {
            "accion": "crear_commit",
            "repair_id": repair_id,
            "mensaje": "primer intento",
            "rutas_esperadas": ["feature.py"],
        },
    )
    await tool.run(ctx, {"accion": "integrar", "repair_id": repair_id})
    retry = await tool.run(
        ctx,
        {
            "accion": "registrar_reintento",
            "repair_id": repair_id,
            "reintento_exitoso": False,
            "evidencia": "sigue fallando",
        },
    )
    assert retry.data["status"] == "prepared"

    reverted = await tool.run(ctx, {"accion": "revertir", "repair_id": repair_id})

    assert reverted.data["status"] == "reverted"
    assert (repo / "feature.py").read_text() == "VALUE = 1\n"
    assert _git(repo, "status", "--porcelain") == ""
