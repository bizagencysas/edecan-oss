"""Documentos (`ARCHITECTURE.md` §10.3, tablas `files` / `file_chunks`).

Si `ctx.extras["memory_embedder"]` existe (implementa el protocolo `Embedder`
de `edecan_core`, §10.7: `async embed(texts: list[str]) -> list[list[float]]`),
se usa para embeber la consulta y buscar por distancia coseno con pgvector
(`<=>`). Si no está presente (self-host sin `EMBEDDINGS_MODEL` configurado),
se cae a una búsqueda de texto simple (`ILIKE`) — la tool sigue siendo útil.
"""

from __future__ import annotations

from typing import Any

from edecan_core import Tool, ToolContext, ToolResult
from sqlalchemy import text

from ._util import clamp_int

_LIMITE_FRAGMENTOS = 5


class ConsultarDocumentosTool(Tool):
    name = "consultar_documentos"
    description = (
        "Busca en los documentos que el usuario ha subido y devuelve los fragmentos "
        "más relevantes para la consulta, con el nombre del archivo de origen."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "consulta": {"type": "string", "description": "Qué buscar dentro de los documentos."},
            "limite": {
                "type": "integer",
                "description": "Máximo de fragmentos a devolver (1-5).",
                "default": _LIMITE_FRAGMENTOS,
            },
        },
        "required": ["consulta"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        consulta = str(args.get("consulta", "")).strip()
        if not consulta:
            return ToolResult(content="Dime qué quieres buscar en tus documentos.")
        limite = clamp_int(
            args.get("limite"), default=_LIMITE_FRAGMENTOS, minimo=1, maximo=_LIMITE_FRAGMENTOS
        )

        extras = ctx.extras if isinstance(ctx.extras, dict) else {}
        embedder = extras.get("memory_embedder")
        if embedder is not None:
            fragmentos = await _buscar_por_similitud(ctx, consulta, limite, embedder)
        else:
            fragmentos = await _buscar_por_texto(ctx, consulta, limite)

        if not fragmentos:
            return ToolResult(
                content=f"No encontré nada relevante en tus documentos para «{consulta}».",
                data={"fragmentos": []},
            )

        lineas = [f"[{f['archivo']} · fragmento {f['seq']}]\n{f['texto']}" for f in fragmentos]
        return ToolResult(content="\n\n".join(lineas), data={"fragmentos": fragmentos})


async def _buscar_por_similitud(
    ctx: ToolContext, consulta: str, limite: int, embedder: Any
) -> list[dict[str, Any]]:
    vectores = await embedder.embed([consulta])
    vector = vectores[0] if vectores else []
    if not vector:
        return []
    # pgvector acepta un literal `'[0.1,0.2,...]'::vector`; no hay forma nativa
    # de bindear un `list[float]` con asyncpg + `text()`, así que se serializa
    # a string y se castea explícitamente en el SQL.
    literal_vector = "[" + ",".join(repr(float(x)) for x in vector) + "]"
    resultado = await ctx.session.execute(
        text(
            "SELECT f.filename AS archivo, fc.seq AS seq, fc.text AS texto "
            "FROM file_chunks fc JOIN files f ON f.id = fc.file_id AND f.tenant_id = :tenant_id "
            "WHERE fc.tenant_id = :tenant_id AND fc.embedding IS NOT NULL "
            "ORDER BY fc.embedding <=> CAST(:vector AS vector) ASC "
            "LIMIT :limite"
        ),
        {"tenant_id": str(ctx.tenant_id), "vector": literal_vector, "limite": limite},
    )
    return [dict(f) for f in resultado.mappings().all()]


async def _buscar_por_texto(ctx: ToolContext, consulta: str, limite: int) -> list[dict[str, Any]]:
    resultado = await ctx.session.execute(
        text(
            "SELECT f.filename AS archivo, fc.seq AS seq, fc.text AS texto "
            "FROM file_chunks fc JOIN files f ON f.id = fc.file_id AND f.tenant_id = :tenant_id "
            "WHERE fc.tenant_id = :tenant_id AND fc.text ILIKE :patron "
            "ORDER BY fc.seq ASC LIMIT :limite"
        ),
        {"tenant_id": str(ctx.tenant_id), "patron": f"%{consulta}%", "limite": limite},
    )
    return [dict(f) for f in resultado.mappings().all()]
