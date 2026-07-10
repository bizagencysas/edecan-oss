"""Contactos / CRM ligero (`ARCHITECTURE.md` §10.3, tabla `contacts`).

`gestionar_contacto` hace upsert por `(tenant_id, user_id, lower(nombre))`: no
hay una restricción `UNIQUE` fijada por el contrato para usar `ON CONFLICT`,
así que se resuelve con un `SELECT` seguido de `UPDATE`/`INSERT` explícito.
"""

from __future__ import annotations

import json
from typing import Any

from edecan_core import Tool, ToolContext, ToolResult
from sqlalchemy import text

from ._util import clamp_int

_LIMITE_DEFECTO = 20
_LIMITE_MAXIMO = 50


def _a_jsonb(valor: Any) -> str:
    """Serializa un valor de entrada (lista, string suelto o `None`) a JSON
    para bindearlo como parámetro y castearlo `::jsonb` en el SQL."""
    if valor is None:
        return json.dumps([])
    if isinstance(valor, str):
        return json.dumps([valor]) if valor.strip() else json.dumps([])
    return json.dumps(list(valor))


def _desde_jsonb(valor: Any) -> list[Any]:
    """Convierte lo que devuelva el driver para una columna `jsonb` (típicamente
    un `str` con el JSON crudo) de vuelta a una lista de Python."""
    if valor is None:
        return []
    if isinstance(valor, str):
        try:
            cargado = json.loads(valor)
        except json.JSONDecodeError:
            return []
        return cargado if isinstance(cargado, list) else [cargado]
    return list(valor)


class BuscarContactosTool(Tool):
    name = "buscar_contactos"
    description = "Busca contactos del usuario por nombre, empresa, correo o teléfono."
    input_schema = {
        "type": "object",
        "properties": {
            "consulta": {
                "type": "string",
                "description": (
                    "Texto a buscar. Si se omite, devuelve los contactos más recientes."
                ),
            },
            "limite": {
                "type": "integer",
                "description": "Máximo de contactos a devolver (1-50).",
                "default": _LIMITE_DEFECTO,
            },
        },
        "required": [],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        limite = clamp_int(
            args.get("limite"), default=_LIMITE_DEFECTO, minimo=1, maximo=_LIMITE_MAXIMO
        )
        consulta = str(args.get("consulta") or "").strip()

        params: dict[str, Any] = {
            "tenant_id": str(ctx.tenant_id),
            "user_id": str(ctx.user_id),
            "limite": limite,
        }
        filtro = ""
        if consulta:
            filtro = (
                "AND (nombre ILIKE :patron OR empresa ILIKE :patron "
                "OR emails::text ILIKE :patron OR phones::text ILIKE :patron) "
            )
            params["patron"] = f"%{consulta}%"

        resultado = await ctx.session.execute(
            text(
                "SELECT id, nombre, emails, phones, empresa, notas, tags FROM contacts "
                "WHERE tenant_id = :tenant_id AND user_id = :user_id "
                f"{filtro}"
                "ORDER BY nombre ASC LIMIT :limite"
            ),
            params,
        )
        filas = resultado.mappings().all()

        if not filas:
            mensaje = (
                f"No encontré contactos para «{consulta}»."
                if consulta
                else "No tienes contactos guardados todavía."
            )
            return ToolResult(content=mensaje, data={"contactos": []})

        lineas: list[str] = []
        contactos: list[dict[str, Any]] = []
        for i, fila in enumerate(filas, start=1):
            emails = _desde_jsonb(fila["emails"])
            phones = _desde_jsonb(fila["phones"])
            empresa_txt = f" ({fila['empresa']})" if fila["empresa"] else ""
            texto = f"{fila['nombre']}{empresa_txt}"
            if emails:
                texto += f" — {', '.join(emails)}"
            if phones:
                texto += f" — {', '.join(phones)}"
            lineas.append(f"{i}. {texto}")
            contactos.append(
                {
                    "id": str(fila["id"]),
                    "nombre": fila["nombre"],
                    "emails": emails,
                    "phones": phones,
                    "empresa": fila["empresa"],
                    "notas": fila["notas"],
                    "tags": _desde_jsonb(fila["tags"]),
                }
            )

        return ToolResult(content="\n".join(lineas), data={"contactos": contactos})


class GestionarContactoTool(Tool):
    name = "gestionar_contacto"
    description = (
        "Crea o actualiza (upsert por nombre) un contacto del usuario: correos, "
        "teléfonos, empresa, notas y etiquetas."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "nombre": {"type": "string", "description": "Nombre del contacto (clave de upsert)."},
            "emails": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Correos del contacto.",
            },
            "phones": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Teléfonos del contacto.",
            },
            "empresa": {"type": "string", "description": "Empresa u organización."},
            "notas": {"type": "string", "description": "Notas libres sobre el contacto."},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Etiquetas libres.",
            },
        },
        "required": ["nombre"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        nombre = str(args.get("nombre", "")).strip()
        if not nombre:
            return ToolResult(content="El contacto necesita un nombre.")

        existente_resultado = await ctx.session.execute(
            text(
                "SELECT id FROM contacts WHERE tenant_id = :tenant_id AND user_id = :user_id "
                "AND lower(nombre) = lower(:nombre) LIMIT 1"
            ),
            {"tenant_id": str(ctx.tenant_id), "user_id": str(ctx.user_id), "nombre": nombre},
        )
        existente = existente_resultado.mappings().first()

        # (nombre_de_columna, es_lista_jsonb). Solo se incluyen en el UPDATE los
        # campos que de verdad vinieron en `args`: si el LLM manda un subconjunto
        # (p. ej. solo `tags` para agregar una etiqueta), los demás campos
        # (emails, phones, empresa, notas) deben preservarse tal cual estaban,
        # no sobreescribirse con vacío/`None`.
        campos_opcionales: tuple[tuple[str, bool], ...] = (
            ("emails", True),
            ("phones", True),
            ("empresa", False),
            ("notas", False),
            ("tags", True),
        )

        if existente is not None:
            params: dict[str, Any] = {
                "id": str(existente["id"]),
                "tenant_id": str(ctx.tenant_id),
            }
            set_partes: list[str] = []
            for campo, es_lista in campos_opcionales:
                valor = args.get(campo)
                if valor is None:
                    continue
                if es_lista:
                    set_partes.append(f"{campo} = CAST(:{campo} AS jsonb)")
                    params[campo] = _a_jsonb(valor)
                else:
                    set_partes.append(f"{campo} = :{campo}")
                    params[campo] = valor

            if set_partes:
                await ctx.session.execute(
                    text(
                        f"UPDATE contacts SET {', '.join(set_partes)}, updated_at = now() "
                        "WHERE id = :id AND tenant_id = :tenant_id"
                    ),
                    params,
                )
            contacto_id = existente["id"]
            verbo = "Actualicé"
        else:
            params = {
                "tenant_id": str(ctx.tenant_id),
                "user_id": str(ctx.user_id),
                "nombre": nombre,
                "emails": _a_jsonb(args.get("emails")),
                "phones": _a_jsonb(args.get("phones")),
                "empresa": args.get("empresa") or "",
                "notas": args.get("notas") or "",
                "tags": _a_jsonb(args.get("tags")),
            }
            fila = (
                await ctx.session.execute(
                    text(
                        "INSERT INTO contacts "
                        "(tenant_id, user_id, nombre, emails, phones, empresa, notas, tags) "
                        "VALUES (:tenant_id, :user_id, :nombre, CAST(:emails AS jsonb), "
                        "CAST(:phones AS jsonb), :empresa, :notas, CAST(:tags AS jsonb)) "
                        "RETURNING id"
                    ),
                    params,
                )
            ).mappings().first()
            contacto_id = fila["id"] if fila else None
            verbo = "Creé"

        return ToolResult(
            content=f"{verbo} el contacto «{nombre}».",
            data={"id": str(contacto_id) if contacto_id is not None else None, "nombre": nombre},
        )
