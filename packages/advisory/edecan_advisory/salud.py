"""Asesor de salud informativo: `registrar_salud`, `resumen_salud`,
`analizar_laboratorio` (ROADMAP_V2.md §7.4/§7.7, WP-V2-11).

GUARDRAIL no negociable (ROADMAP_V2.md §8.3): tracking informativo de
hábitos/medicamentos y lectura de laboratorio, NUNCA diagnóstico ni
sustituto de un profesional — cada respuesta en el camino feliz termina con
`_disclaimers.DISCLAIMER_SALUD` (vía `with_disclaimer("salud", ...)`).
`analizar_laboratorio` además antepone una advertencia reforzada explícita
(`_ADVERTENCIA_LABORATORIO`) ANTES de ese disclaimer final — ver su docstring
más abajo.

Tabla `health_logs` (ARCHITECTURE.md §10.3 / ROADMAP_V2.md §7.4): tenant-scoped
con RLS igual que el resto del esquema (ver `docs/asesores.md`, sección
"Privacidad"), así que ningún dato de salud de un tenant es visible desde
otro tenant a nivel de base de datos.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from edecan_core import Tool, ToolContext, ToolResult
from edecan_llm.base import ChatMessage, CompletionRequest
from sqlalchemy import text

from . import _texto
from ._disclaimers import with_disclaimer
from ._util import clamp_int, parse_uuid, tenant_flags

#: Vocabulario EXACTO de `health_logs.kind` (mismo `CheckConstraint` que
#: `edecan_db.models.HealthLog`, ARCHITECTURE.md §10.3 / ROADMAP_V2.md §7.4).
KINDS_SALUD: tuple[str, ...] = ("medicamento", "ejercicio", "sueno", "agua", "habito", "medida")

_DESDE_DIAS_DEFECTO = 7
_DESDE_DIAS_MAXIMO = 90

_MAX_ANALITOS = 60

# Heurística "nombre + número + unidad por línea" (ROADMAP_V2.md §7.7):
# nombre = letras/espacios/puntuación común de nombres de analitos (sin
# dígitos ni ':'), separado por ':' u espacio(s) de un número (con '.' o ','
# decimal) y, opcionalmente, una unidad alfanumérica al final de la línea.
# Una línea que no calza exactamente este patrón (p. ej. un rango "70-100
# mg/dL", o una línea de texto libre) simplemente no matchea — es una
# heurística, no un parser de PDF de laboratorio real (mismo criterio que
# `edecan_docanalysis.pdf._detectar_tablas`, que también documenta sus
# límites conocidos en vez de perseguir un 100% de recall).
_LINEA_ANALITO_RE = re.compile(
    r"^(?P<nombre>[A-Za-zÁÉÍÓÚÜÑáéíóúüñ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9 ./%()-]{1,60}?)"
    r"\s*:?\s+"
    r"(?P<valor>-?\d+(?:[.,]\d+)?)"
    r"\s*(?P<unidad>[A-Za-zµμ%][A-Za-zµμ/%]*)?\s*$"
)

_SYSTEM_PROMPT_LABORATORIO = (
    "Eres un asistente informativo de salud. Te doy una lista de analitos "
    "detectados automáticamente en un examen de laboratorio (nombre, valor, "
    "unidad). Para cada uno, explica en 1-2 frases QUÉ MIDE ese analito en "
    "términos generales (qué órgano o función del cuerpo refleja). "
    "PROHIBIDO TERMINANTEMENTE: no des ningún diagnóstico, no digas si un "
    "valor está 'normal'/'alto'/'bajo' respecto a rangos clínicos, no "
    "recomiendes ningún medicamento ni tratamiento, no sugieras qué hacer. "
    "Responde solo con información educativa general, en español."
)

#: Advertencia REFORZADA de `analizar_laboratorio` (ROADMAP_V2.md §7.7:
#: "disclaimer salud reforzado") — se antepone al `DISCLAIMER_SALUD` estándar
#: (que sigue yendo al final vía `with_disclaimer`, nunca se reemplaza): esta
#: tool interpreta un documento médico real, así que además del disclaimer
#: genérico necesita dejar explícito que la detección es automática/textual
#: y que los rangos de referencia varían.
_ADVERTENCIA_LABORATORIO = (
    "IMPORTANTE: estos son valores detectados automáticamente por texto, no una "
    "interpretación clínica. Los rangos de referencia varían según el laboratorio, "
    "tu edad, sexo o condición — solo tu médico, con el reporte original, puede "
    "interpretarlos correctamente."
)


def _ahora() -> datetime:
    """Punto de extensión para tests con reloj fijo (`monkeypatch.setattr`)."""
    return datetime.now(UTC)


class RegistrarSaludTool(Tool):
    name = "registrar_salud"
    description = (
        "Registra un dato de salud del usuario (medicamento tomado, ejercicio, sueño, "
        "agua, hábito o medida corporal) con la hora actual. Informativo: no reemplaza "
        "a un profesional de la salud."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": list(KINDS_SALUD),
                "description": "Tipo de registro.",
            },
            "valor": {
                "type": "object",
                "description": (
                    "Datos libres del registro (ej. {'nombre': 'ibuprofeno', 'dosis': "
                    "'400mg'} o {'cantidad': 30, 'unidad': 'min'}). No puede estar vacío."
                ),
            },
            "notas": {"type": "string", "description": "Notas libres, opcional."},
        },
        "required": ["kind", "valor"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        kind = str(args.get("kind") or "").strip().lower()
        if kind not in KINDS_SALUD:
            opciones = ", ".join(KINDS_SALUD)
            return ToolResult(
                content=f"'{kind}' no es un tipo de registro válido (usa: {opciones})."
            )
        valor = args.get("valor")
        if not isinstance(valor, dict) or not valor:
            return ToolResult(
                content="'valor' debe ser un objeto con al menos un dato (no puede estar vacío)."
            )
        notas_raw = args.get("notas")
        notas = str(notas_raw).strip() if notas_raw else ""
        notas = notas or None

        registrado_en = _ahora()
        resultado = await ctx.session.execute(
            text(
                "INSERT INTO health_logs "
                "(tenant_id, user_id, kind, valor, notas, registrado_en) "
                "VALUES (:tenant_id, :user_id, :kind, CAST(:valor AS jsonb), "
                ":notas, :registrado_en) "
                "RETURNING id"
            ),
            {
                "tenant_id": str(ctx.tenant_id),
                "user_id": str(ctx.user_id),
                "kind": kind,
                "valor": json.dumps(valor),
                "notas": notas,
                "registrado_en": registrado_en,
            },
        )
        fila = resultado.mappings().first()

        resumen_valor = ", ".join(f"{k}={v}" for k, v in valor.items())
        contenido = with_disclaimer(
            "salud",
            f"Registré «{kind}» ({resumen_valor}) el {registrado_en.date().isoformat()}.",
        )
        return ToolResult(
            content=contenido,
            data={
                "id": str(fila["id"]) if fila else None,
                "kind": kind,
                "valor": valor,
                "registrado_en": registrado_en.isoformat(),
            },
        )


class ResumenSaludTool(Tool):
    name = "resumen_salud"
    description = (
        "Resume los registros de salud del usuario en los últimos N días: conteos, "
        "sumas de cantidad y rachas de días consecutivos por tipo de registro. "
        "Informativo: no reemplaza a un profesional de la salud."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "desde_dias": {
                "type": "integer",
                "description": f"Ventana de días hacia atrás a resumir (1-{_DESDE_DIAS_MAXIMO}).",
                "default": _DESDE_DIAS_DEFECTO,
            },
        },
        "required": [],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        desde_dias = clamp_int(
            args.get("desde_dias"),
            default=_DESDE_DIAS_DEFECTO,
            minimo=1,
            maximo=_DESDE_DIAS_MAXIMO,
        )
        hasta = _ahora()
        desde = hasta - timedelta(days=desde_dias)

        resultado = await ctx.session.execute(
            text(
                "SELECT kind, valor, registrado_en FROM health_logs "
                "WHERE tenant_id = :tenant_id AND user_id = :user_id "
                "AND registrado_en >= :desde AND registrado_en <= :hasta "
                "ORDER BY registrado_en ASC"
            ),
            {
                "tenant_id": str(ctx.tenant_id),
                "user_id": str(ctx.user_id),
                "desde": desde,
                "hasta": hasta,
            },
        )
        filas = resultado.mappings().all()

        if not filas:
            contenido = with_disclaimer(
                "salud", f"No hay registros de salud en los últimos {desde_dias} día(s)."
            )
            return ToolResult(content=contenido, data={"desde_dias": desde_dias, "por_kind": {}})

        agregados = _agregar_por_kind(filas)

        lineas = [f"Resumen de salud — últimos {desde_dias} día(s):"]
        for kind in sorted(agregados):
            agg = agregados[kind]
            linea = f"- {kind}: {agg['conteo']} registro(s)"
            if agg["suma_cantidad"] is not None:
                linea += f", total cantidad={agg['suma_cantidad']:g}"
            if agg["racha_dias"] >= 2:
                linea += f", racha de {agg['racha_dias']} día(s) consecutivos"
            lineas.append(linea)

        contenido = with_disclaimer("salud", "\n".join(lineas))
        return ToolResult(content=contenido, data={"desde_dias": desde_dias, "por_kind": agregados})


def _valor_dict(valor: Any) -> dict[str, Any]:
    """Normaliza la columna `valor` (jsonb) tal como la devuelva el driver:
    ya como `dict` (caso típico vía SQLAlchemy/asyncpg), o como `str` cruda
    (defensivo, mismo criterio que `edecan_toolkit.contactos._desde_jsonb`)."""
    if isinstance(valor, dict):
        return valor
    if isinstance(valor, str):
        try:
            cargado = json.loads(valor)
        except json.JSONDecodeError:
            return {}
        return cargado if isinstance(cargado, dict) else {}
    return {}


def _a_fecha(valor: Any) -> date:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    if isinstance(valor, str):
        return datetime.fromisoformat(valor).date()
    raise TypeError(f"registrado_en con tipo inesperado: {type(valor)!r}")


def _racha_dias(dias: list[date]) -> int:
    """Días consecutivos (sin huecos) contando hacia atrás desde la fecha más
    reciente de `dias` — no depende de "hoy": un hábito cuyo último registro
    fue ayer sigue teniendo su racha completa aunque hoy todavía no se haya
    registrado (el corte de si la racha sigue "viva" queda para quien lea el
    resumen, no para este cálculo)."""
    if not dias:
        return 0
    ordenados = sorted(set(dias), reverse=True)
    racha = 1
    # Longitudes distintas A PROPÓSITO (desfase de 1 para comparar vecinos
    # consecutivos) — nunca deben tener la misma longitud, así que `strict=False`.
    for actual, siguiente in zip(ordenados, ordenados[1:], strict=False):
        if (actual - siguiente).days == 1:
            racha += 1
        else:
            break
    return racha


def _agregar_por_kind(filas: list[Any]) -> dict[str, dict[str, Any]]:
    por_kind: dict[str, list[Any]] = {}
    for fila in filas:
        por_kind.setdefault(fila["kind"], []).append(fila)

    agregados: dict[str, dict[str, Any]] = {}
    for kind, regs in por_kind.items():
        cantidades: list[float] = []
        for r in regs:
            cantidad = _valor_dict(r["valor"]).get("cantidad")
            if isinstance(cantidad, (int, float)) and not isinstance(cantidad, bool):
                cantidades.append(float(cantidad))
        dias = [_a_fecha(r["registrado_en"]) for r in regs]
        agregados[kind] = {
            "conteo": len(regs),
            "suma_cantidad": sum(cantidades) if cantidades else None,
            "racha_dias": _racha_dias(dias),
        }
    return agregados


class AnalizarLaboratorioTool(Tool):
    name = "analizar_laboratorio"
    description = (
        "Extrae analitos (nombre, valor, unidad) de un resultado de laboratorio ya "
        "subido y explica en general qué mide cada uno. NO diagnostica ni interpreta "
        "si los valores están normales/altos/bajos — eso lo hace tu médico."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_id": {
                "type": "string",
                "description": "id del archivo de laboratorio ya subido.",
            },
        },
        "required": ["file_id"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        file_id = parse_uuid(args.get("file_id"))
        if file_id is None:
            return ToolResult(content="'file_id' no es un identificador válido.")

        try:
            extraido = await _texto.extraer_texto_de_file_id(ctx, file_id)
        except _texto.FormatoNoSoportado as exc:
            return ToolResult(content=str(exc))
        if extraido is None:
            return ToolResult(content="No encontré ese archivo.")

        analitos = _detectar_analitos(extraido.texto)
        if not analitos:
            return ToolResult(
                content=(
                    f"No detecté analitos con formato reconocible en «{extraido.archivo.filename}» "
                    "(busco líneas del tipo 'Nombre  valor  unidad')."
                ),
                data={"analitos": []},
            )

        explicacion = await _explicar_analitos(ctx, analitos)
        tabla = _tabla_analitos(analitos)

        cuerpo = (
            f"Detecté {len(analitos)} analito(s) en «{extraido.archivo.filename}»:\n\n"
            f"{tabla}\n\n{explicacion}\n\n{_ADVERTENCIA_LABORATORIO}"
        )
        contenido = with_disclaimer("salud", cuerpo)
        return ToolResult(content=contenido, data={"analitos": analitos})


def _detectar_analitos(texto: str) -> list[dict[str, Any]]:
    analitos: list[dict[str, Any]] = []
    for linea in texto.splitlines():
        if len(analitos) >= _MAX_ANALITOS:
            break
        limpia = linea.strip()
        if not limpia:
            continue
        m = _LINEA_ANALITO_RE.match(limpia)
        if not m:
            continue
        nombre = m.group("nombre").strip(" :-")
        if not nombre:
            continue
        try:
            valor = float(m.group("valor").replace(",", "."))
        except ValueError:
            continue
        unidad = (m.group("unidad") or "").strip()
        analitos.append({"nombre": nombre, "valor": valor, "unidad": unidad})
    return analitos


def _tabla_analitos(analitos: list[dict[str, Any]]) -> str:
    lineas = ["| Analito | Valor | Unidad |", "|---|---|---|"]
    lineas.extend(f"| {a['nombre']} | {a['valor']:g} | {a['unidad'] or '—'} |" for a in analitos)
    return "\n".join(lineas)


async def _explicar_analitos(ctx: ToolContext, analitos: list[dict[str, Any]]) -> str:
    listado = "\n".join(f"- {a['nombre']}: {a['valor']:g} {a['unidad']}".strip() for a in analitos)
    respuesta = await ctx.llm.complete(
        "principal",
        tenant_flags(ctx),
        CompletionRequest(
            model="principal",
            system=_SYSTEM_PROMPT_LABORATORIO,
            messages=[ChatMessage(role="user", content=listado)],
            max_tokens=1536,
        ),
    )
    return respuesta.text.strip() or "No pude generar una explicación para estos analitos."
