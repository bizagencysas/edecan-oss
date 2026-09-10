"""Community-manager helpers for Edecán Bots: OAuth status and social drafts.

Draft/research tools stay non-dangerous; external publish uses `publicar_social`
(`dangerous=True`) with durable approval in bot chat.
"""

from __future__ import annotations

from typing import Any

from edecan_core import Tool, ToolContext, ToolResult
from sqlalchemy import text

from ._conectores import RUTA_CONECTORES, RUTA_CONECTORES_UI, token_bundle_operativo

_REDES_COMUNIDAD: tuple[str, ...] = ("linkedin", "x", "meta", "youtube")
_NOMBRES_LEGIBLES = {
    "linkedin": "LinkedIn",
    "x": "X (Twitter)",
    "meta": "Meta (Facebook/Instagram)",
    "youtube": "YouTube",
}


class EstadoConectoresSocialesTool(Tool):
    name = "estado_conectores_sociales"
    description = (
        "Consulta qué redes sociales oficiales (linkedin, x, meta, youtube) tienen "
        "OAuth conectado para este tenant. OBLIGATORIO antes de prometer publicar "
        "o decir «ya estás conectado»: si falta una red, indica al dueño que puede "
        f"conectarla en {RUTA_CONECTORES_UI} (sin presión)."
    )
    category = "external_comm"
    risk_level = "low"
    input_schema = {"type": "object", "properties": {}}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        placeholders = ", ".join(f":red{i}" for i in range(len(_REDES_COMUNIDAD)))
        params: dict[str, Any] = {
            f"red{i}": red for i, red in enumerate(_REDES_COMUNIDAD)
        }
        params["tenant_id"] = str(ctx.tenant_id)

        resultado = await ctx.session.execute(
            text(
                "SELECT connector_key, id, created_at FROM connector_accounts "
                f"WHERE tenant_id = :tenant_id AND connector_key IN ({placeholders}) "
                "ORDER BY connector_key, created_at DESC"
            ),
            params,
        )
        filas = resultado.mappings().all()
        conectadas: dict[str, str] = {}
        for fila in filas:
            clave = str(fila["connector_key"])
            if clave in conectadas:
                continue
            account_id = fila["id"]
            if await token_bundle_operativo(ctx, account_id):
                conectadas[clave] = str(account_id)

        lineas: list[str] = []
        for red in _REDES_COMUNIDAD:
            nombre = _NOMBRES_LEGIBLES.get(red, red)
            if red in conectadas:
                lineas.append(f"- {nombre}: conectada (cuenta {conectadas[red][:8]}…)")
            else:
                lineas.append(
                    f"- {nombre}: NO conectada — cuando quieras, {RUTA_CONECTORES_UI} "
                    f"(o {RUTA_CONECTORES})"
                )

        if not any(red in conectadas for red in _REDES_COMUNIDAD):
            resumen = (
                "Ninguna red social está conectada por OAuth. "
                f"El dueño puede conectar cuando quiera en {RUTA_CONECTORES_UI} "
                f"(o {RUTA_CONECTORES} en el navegador) — sin prisa. "
                "LinkedIn y X son el mínimo útil para community manager."
            )
        else:
            faltantes = [r for r in _REDES_COMUNIDAD if r not in conectadas]
            if faltantes:
                resumen = (
                    "Hay al menos una red conectada; faltan: "
                    + ", ".join(_NOMBRES_LEGIBLES.get(r, r) for r in faltantes)
                    + f". Puede conectar en {RUTA_CONECTORES_UI} cuando quiera."
                )
            else:
                resumen = "Las cuatro redes soportadas tienen OAuth conectado."

        return ToolResult(
            content=resumen + "\n\n" + "\n".join(lineas),
            data={"conectadas": list(conectadas.keys()), "detalle": conectadas},
        )


class ListarBorradoresSocialesTool(Tool):
    name = "listar_borradores_sociales"
    description = (
        "Lista borradores sociales pendientes (status borrador) del tenant: "
        "draft_id, plataforma, destino y un extracto del texto. No publica nada."
    )
    category = "creative"
    risk_level = "low"
    input_schema = {
        "type": "object",
        "properties": {
            "limite": {
                "type": "integer",
                "description": "Máximo de borradores a devolver (default 10).",
                "default": 10,
            },
            "plataforma": {
                "type": "string",
                "description": "Filtrar por plataforma (linkedin, x, meta, youtube). Opcional.",
            },
        },
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        limite = max(1, min(int(args.get("limite") or 10), 50))
        plataforma = str(args.get("plataforma") or "").strip().lower()
        params: dict[str, Any] = {
            "tenant_id": str(ctx.tenant_id),
            "limite": limite,
        }
        filtro_plataforma = ""
        if plataforma:
            filtro_plataforma = " AND platform = :plataforma"
            params["plataforma"] = plataforma

        resultado = await ctx.session.execute(
            text(
                "SELECT draft_id, platform, target, status, "
                'LEFT("text", 280) AS excerpt, image_file_id, updated_at '
                "FROM social_drafts "
                "WHERE tenant_id = :tenant_id ::uuid AND status = 'borrador'"
                f"{filtro_plataforma} "
                "ORDER BY updated_at DESC LIMIT :limite"
            ),
            params,
        )
        filas = [dict(f) for f in resultado.mappings().all()]
        if not filas:
            msg = "No hay borradores sociales pendientes"
            if plataforma:
                msg += f" para {plataforma}"
            msg += "."
            return ToolResult(content=msg, data={"borradores": []})

        lineas = [f"{len(filas)} borrador(es) pendiente(s):\n"]
        for i, fila in enumerate(filas, 1):
            excerpt = str(fila.get("excerpt") or "").replace("\n", " ").strip()
            if len(excerpt) >= 280:
                excerpt += "…"
            img = "con imagen" if fila.get("image_file_id") else "solo texto"
            lineas.append(
                f"{i}. [{fila['platform']}/{fila['target']}] id={fila['draft_id']} "
                f"({img}): {excerpt or '(vacío)'}"
            )
        return ToolResult(content="\n".join(lineas), data={"borradores": filas})
