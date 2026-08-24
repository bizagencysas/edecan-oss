"""Gate live y acotado de frescura para búsqueda pública.

No escribe en Edecán ni usa credenciales: consulta DuckDuckGo en varias rondas,
verifica que cada ronda produzca fuentes y emite timestamps/proveedor para que
la evidencia no se confunda con una sola corrida aislada. No pretende sustituir
una prueba de 24 horas; ese límite queda explícito en la documentación.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import UTC, datetime
from typing import Any

import httpx
from edecan_toolkit.research import DuckDuckGoSearch


async def run_gate(
    query: str, rounds: int, k: int, official_package: str = "fastapi"
) -> dict[str, Any]:
    provider = DuckDuckGoSearch()
    results: list[dict[str, Any]] = []
    for round_number in range(1, rounds + 1):
        hits = await provider.search(query, k=k)
        if not hits:
            raise RuntimeError(f"La ronda {round_number} no devolvió fuentes.")
        retrieved_at = datetime.now(UTC).isoformat()
        results.append(
            {
                "round": round_number,
                "provider": provider.name,
                "retrieved_at": retrieved_at,
                "result_count": len(hits),
                "domains": sorted({hit.url.split('/')[2] for hit in hits if '//' in hit.url}),
            }
        )
    package = official_package.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", package):
        raise ValueError("nombre de paquete oficial inválido")
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as http:
        response = await http.get(f"https://pypi.org/pypi/{package}/json")
    response.raise_for_status()
    official_version = str(response.json().get("info", {}).get("version") or "").strip()
    if not official_version:
        raise RuntimeError(f"PyPI no devolvió versión para {package}.")
    return {
        "ok": True,
        "query": query,
        "official_source": "https://pypi.org",
        "official_package": package,
        "official_version": official_version,
        "rounds": results,
        "round_count": len(results),
        "all_rounds_non_empty": all(row["result_count"] > 0 for row in results),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", required=True)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--official-package", default="fastapi")
    args = parser.parse_args()
    if not 2 <= args.rounds <= 8:
        parser.error("--rounds debe estar entre 2 y 8")
    if not 1 <= args.k <= 10:
        parser.error("--k debe estar entre 1 y 10")
    report = asyncio.run(
        run_gate(args.query.strip(), args.rounds, args.k, args.official_package)
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
