#!/usr/bin/env python
"""Cambia el proveedor de LLM en `platform-config.json` (switch server-driven).

Uso (desde la raíz del repo):
    .venv/bin/python scripts/switch_llm_provider.py azure_openai
    .venv/bin/python scripts/switch_llm_provider.py workers_ai

El cambio surte efecto al reiniciar Edecan: el catálogo de modelos de la app
cambia solo (Azure: "Sol/Terra/Luna"; Workers AI: Copla/Silva/Soneto/Oda). Sin
`LLM_PROVIDER` (o `workers_ai`) el default sigue siendo Cloudflare Workers AI.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

CONFIG_PATH = (
    Path.home()
    / "Library"
    / "Application Support"
    / "cc.edecan.desktop"
    / "data"
    / "platform-config.json"
)

VALIDOS = ("workers_ai", "azure_openai", "openai_compat")


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in VALIDOS:
        print(f"Uso: {sys.argv[0]} <{'|'.join(VALIDOS)}>", file=sys.stderr)
        return 2

    objetivo = sys.argv[1]
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    anterior = data.get("LLM_PROVIDER") or "workers_ai"
    data["LLM_PROVIDER"] = objetivo
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if objetivo == "azure_openai":
        if not data.get("AZURE_AI_FOUNDRY_ENDPOINT") or not data.get("AZURE_AI_FOUNDRY_API_KEY"):
            print(
                "⚠️  Faltan AZURE_AI_FOUNDRY_ENDPOINT / AZURE_AI_FOUNDRY_API_KEY en "
                "platform-config.json; el proveedor Azure fallará al arrancar.",
                file=sys.stderr,
            )
    print(
        f"LLM_PROVIDER: {anterior} -> {objetivo}. "
        "Reinicia Edecan (Cmd+Q y ábrelo) para aplicarlo."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
