from __future__ import annotations

import tempfile
from pathlib import Path

import yaml
from edecan_llm.task_router import TaskRouter, modelo_para_perfil


def test_router_respects_yaml_profile_change() -> None:
    yaml_content = {
        "version": 1,
        "perfiles": {
            "chat_rapido": {"modelo": "@cf/custom/fast-model"},
            "ingenieria_software": {"modelo": "@cf/custom/forge-model"},
        },
    }

    with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as f:
        yaml.dump(yaml_content, f)
        temp_path = Path(f.name)

    try:
        model_fast = modelo_para_perfil("chat_rapido", temp_path)
        assert model_fast == "@cf/custom/fast-model"

        model_forge = modelo_para_perfil("ingenieria_software", temp_path)
        assert model_forge == "@cf/custom/forge-model"

        router = TaskRouter(config_path=temp_path)
        decision = router.decide(alias="ingenieria_software")
        assert decision.model == "@cf/custom/forge-model"
    finally:
        temp_path.unlink(missing_ok=True)


def test_no_literal_model_names_in_llm_package() -> None:
    pkg_dir = Path(__file__).resolve().parents[1] / "edecan_llm"
    forbidden_literals = ["@cf/moonshotai/", "@cf/zai-org/"]

    violations: list[str] = []
    for py_file in pkg_dir.glob("*.py"):
        # Ignore workers_ai.py constants or config fallback definitions
        if py_file.name in {"workers_ai.py", "task_router.py"}:
            continue
        content = py_file.read_text(encoding="utf-8")
        for forbidden in forbidden_literals:
            if forbidden in content:
                violations.append(f"{py_file.name} contiene el literal de modelo '{forbidden}'")

    assert not violations, f"Se encontraron nombres de modelo harcodeados: {violations}"
