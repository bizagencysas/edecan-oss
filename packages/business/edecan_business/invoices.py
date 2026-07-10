"""Facturación (`invoices`/`invoice_items`, `ARCHITECTURE.md` §10.3 vía `ROADMAP_V2.md`
§7.4; WP-V2-12).

Funciones puras o con `session` explícito (nunca `ToolContext`) para que las use tanto
`tools.py` (con `ctx.session`/`ctx.tenant_id`/...) como
`apps/api/edecan_api/routers/negocios.py` (con su propia sesión de tenant vía
`edecan_api.deps.get_tenant_session`) sin duplicar la lógica de negocio en dos sitios —
mismo criterio que documenta `edecan_commerce.budgets` (`ROADMAP_V2.md` §7.7, WP-V2-10).

`crear_factura` es la ÚNICA fuente de verdad para "crear una factura completa": calcula
totales, numera, inserta `invoices` + `invoice_items`, genera el PDF y lo sube. Tanto
`tools.CrearFacturaTool` como `POST /v1/negocios/facturas` la llaman — nunca reimplementan
el flujo por su cuenta.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ._files import Uploader, subir_pdf

TWO_PLACES = Decimal("0.01")
_MONEDA_DEFECTO = "USD"
_ESTADOS: tuple[str, ...] = ("draft", "sent", "paid", "void")

# Transiciones permitidas: destino -> conjunto de estados de origen válidos. `draft` nunca
# es un destino (no se puede "des-enviar" ni "des-anular" una factura) — mismo criterio que
# `edecan_commerce`: los estados de dinero/documentos solo avanzan. `sent`/`paid` son
# estrictamente secuenciales (`draft`→`sent`→`paid`, ROADMAP_V2.md §7.7); `void` está
# permitido "desde cualquier" estado no-anulado todavía (draft, sent o paid), tal como pide
# el paquete de trabajo.
_TRANSICIONES_VALIDAS: dict[str, frozenset[str]] = {
    "sent": frozenset({"draft"}),
    "paid": frozenset({"sent"}),
    "void": frozenset({"draft", "sent", "paid"}),
}


class EstadoInvalidoError(ValueError):
    """Transición de estado no permitida sobre una factura existente (`draft→sent→paid`;
    `void` desde cualquier estado no-`void`). Subclase de `ValueError` para que un llamador
    que solo distingue "error de validación" siga funcionando, pero
    `apps/api/edecan_api/routers/negocios.py` la distingue explícitamente para responder
    `409 Conflict` en vez de `422` (es un conflicto de estado, no un cuerpo mal formado)."""


# ---------------------------------------------------------------------------
# Totales (Decimal + redondeo bancario)
# ---------------------------------------------------------------------------


def _round2(valor: Decimal) -> Decimal:
    """Redondeo bancario (half-even) a 2 decimales. Evita el sesgo sistemático hacia arriba
    del redondeo "half-up": en una serie larga de facturas, half-even reparte los empates
    (`x.xx5`) entre redondear arriba y abajo según la paridad del dígito anterior, en vez de
    siempre favorecer un lado — el criterio contable estándar ("banker's rounding")."""
    return valor.quantize(TWO_PLACES, rounding=ROUND_HALF_EVEN)


def _to_decimal(valor: Any, campo: str) -> Decimal:
    if valor is None or valor == "":
        raise ValueError(f"{campo} es obligatorio.")
    try:
        return Decimal(str(valor))
    except InvalidOperation as exc:
        raise ValueError(f"'{valor}' no es un valor numérico válido para {campo}.") from exc


def _line_total(cantidad: Decimal, precio_unitario: Decimal) -> Decimal:
    return _round2(cantidad * precio_unitario)


def compute_totals(
    items: list[dict[str, Any]], impuestos_pct: Decimal | float | int | str = Decimal("0")
) -> tuple[Decimal, Decimal, Decimal]:
    """`(subtotal, impuestos, total)` de `items` (cada uno con `cantidad`/`precio_unitario`),
    con `Decimal` y redondeo bancario a 2 decimales en cada paso (línea, subtotal,
    impuestos, total). Redondear cada línea ANTES de sumar (en vez de sumar en precisión
    completa y redondear solo el subtotal final) es lo que garantiza que la suma de los
    totales de línea que ve el usuario en el PDF siempre cuadre exactamente con el subtotal
    mostrado — nunca hay un centavo de diferencia por acumulación de redondeo.

    No valida signos ni campos faltantes (eso es trabajo de `_normalizar_items`, que ya deja
    `items` con `Decimal`s limpios antes de llegar aquí) — `compute_totals` en sí es pura
    aritmética, así que también puede llamarse directamente desde el frontend/tests con
    datos ya validados.
    """
    pct = (
        _to_decimal(impuestos_pct, "impuestos_pct")
        if impuestos_pct not in (None, "")
        else Decimal("0")
    )
    subtotal = Decimal("0.00")
    for item in items:
        cantidad = Decimal(str(item["cantidad"]))
        precio = Decimal(str(item["precio_unitario"]))
        subtotal += _line_total(cantidad, precio)
    subtotal = _round2(subtotal)
    impuestos = _round2(subtotal * pct / Decimal("100"))
    total = _round2(subtotal + impuestos)
    return subtotal, impuestos, total


def _normalizar_items(items: Any) -> list[dict[str, Any]]:
    """Valida y normaliza `items` a una lista de dicts con `descripcion`/`cantidad`
    (`Decimal`)/`precio_unitario` (`Decimal`)/`total` (`Decimal`, línea ya redondeada).

    Deliberadamente NO descarta items inválidos en silencio (a diferencia de
    `edecan_creative.tools._normalizar_secciones`, que sí recorta listas "mejor esfuerzo"):
    esto es dinero — un item con cantidad/precio negativo o sin descripción debe rechazar
    TODA la factura con un mensaje claro, nunca crear un documento parcialmente distinto de
    lo que pidió el usuario/modelo.
    """
    if not isinstance(items, list) or not items:
        raise ValueError("La factura necesita al menos un item.")
    normalizados: list[dict[str, Any]] = []
    for idx, raw in enumerate(items, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"El item #{idx} no es válido.")
        descripcion = str(raw.get("descripcion") or "").strip()
        if not descripcion:
            raise ValueError(f"El item #{idx} necesita una descripción.")
        cantidad = _to_decimal(raw.get("cantidad"), f"la cantidad del item #{idx}")
        precio = _to_decimal(raw.get("precio_unitario"), f"el precio_unitario del item #{idx}")
        if cantidad < 0:
            raise ValueError(f"La cantidad del item #{idx} no puede ser negativa.")
        if precio < 0:
            raise ValueError(f"El precio_unitario del item #{idx} no puede ser negativo.")
        normalizados.append(
            {
                "descripcion": descripcion,
                "cantidad": cantidad,
                "precio_unitario": precio,
                "total": _line_total(cantidad, precio),
            }
        )
    return normalizados


def _normalizar_moneda(moneda: str | None) -> str:
    moneda = (moneda or _MONEDA_DEFECTO).strip().upper()
    return moneda if len(moneda) == 3 and moneda.isalpha() else _MONEDA_DEFECTO


def _parse_due_date(value: date | str | None) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"'{value}' no es una fecha válida (usa YYYY-MM-DD).") from exc


# ---------------------------------------------------------------------------
# Numeración
# ---------------------------------------------------------------------------


async def next_numero(session: AsyncSession, *, tenant_id: UUID, year: int | None = None) -> str:
    """Siguiente número de factura del tenant para `year` (por defecto, el año actual UTC):
    `"F-{YYYY}-{n:04d}"` con `n` = cantidad de facturas YA creadas por ese tenant en ese año,
    más uno.

    La numeración es por TENANT (no por usuario, aunque `invoices` sí tiene `user_id`): un
    negocio es del tenant, y dos usuarios del mismo tenant facturando el mismo día deben
    compartir la misma secuencia — igual que dos cajeros de un mismo comercio no pueden emitir
    cada uno su propio "F-2026-0001".

    **Carrera teórica, aceptada en P1**: este `SELECT COUNT(*)` y el `INSERT` posterior
    (`crear_factura`) NO están envueltos en una transacción con bloqueo (`SELECT ... FOR
    UPDATE` o una secuencia dedicada por tenant) — dos llamadas concurrentes desde el mismo
    tenant podrían leer el mismo `COUNT` antes de que la primera confirme su `INSERT`, y
    ambas facturas terminarían con el mismo `numero`. Es aceptable ahora porque: (a) no hay
    una restricción `UNIQUE` sobre `invoices.numero` en el esquema pinned (`ROADMAP_V2.md`
    §7.4) — una colisión no rompe ningún `INSERT`, solo produce un número duplicado visible
    en el PDF; (b) el patrón de uso esperado en P1 es un único usuario (o unos pocos) creando
    facturas una a la vez desde la UI, no un sistema de punto de venta de alto volumen
    disparando creaciones en paralelo; y (c) el arreglo correcto — un `SELECT ... FOR UPDATE`
    sobre una fila de contador por `(tenant_id, year)`, o una secuencia de Postgres por
    tenant — es una migración de esquema nueva, fuera del alcance de este paquete de trabajo
    (no puede tocar `packages/db/alembic/`). Documentado también en `docs/negocios.md`.
    """
    year = year or datetime.now(UTC).year
    result = await session.execute(
        text(
            "SELECT COUNT(*) AS n FROM invoices "
            "WHERE tenant_id = :tenant_id ::uuid AND EXTRACT(YEAR FROM created_at) = :year"
        ),
        {"tenant_id": str(tenant_id), "year": year},
    )
    row = result.mappings().first()
    count = int(row["n"]) if row and row.get("n") is not None else 0
    return f"F-{year}-{count + 1:04d}"


# ---------------------------------------------------------------------------
# PDF (fpdf2, fuente core — mismo límite latin-1 que `edecan_creative`)
# ---------------------------------------------------------------------------


def _sanitizar_pdf(texto: str) -> str:
    """Satura `texto` al charset latin-1 (ISO-8859-1) que soportan las fuentes core de
    fpdf2 (Helvetica), sustituyendo cualquier carácter fuera de rango por '?' — mismo
    criterio y mismo motivo (regla dura de la plataforma: nada de red al generar un archivo,
    así que no se descarga una fuente TrueType) que `edecan_creative.tools._sanitizar_pdf`.
    """
    return texto.encode("latin-1", errors="replace").decode("latin-1")


def _fmt_fecha(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    return str(value)


def _fmt_monto(value: Any) -> str:
    try:
        return f"{Decimal(str(value)):,.2f}"
    except InvalidOperation:
        return str(value)


def render_pdf(invoice: dict[str, Any], items: list[dict[str, Any]]) -> bytes:
    """PDF de una factura: encabezado (número, fecha de emisión, cliente, vencimiento),
    tabla de items (descripción/cantidad/precio/total), subtotal/impuestos/total, y el pie
    "Generado por Edecán". Layout limpio, fuente core Helvetica (sin fuentes externas: mismo
    límite latin-1 documentado en `docs/negocios.md`, igual que `edecan_creative`).

    Acepta cualquier `Mapping`-like con `.get(...)` (una fila de Postgres, o un `dict` de
    test) — nunca revienta por una clave ausente, siempre usa un default razonable.
    """
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    moneda = invoice.get("moneda") or _MONEDA_DEFECTO
    numero = invoice.get("numero") or ""

    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 10, _sanitizar_pdf(f"Factura {numero}"))
    pdf.ln(12)

    pdf.set_font("Helvetica", size=10)
    emitida = _fmt_fecha(invoice.get("created_at"))
    if emitida:
        pdf.cell(0, 6, _sanitizar_pdf(f"Emitida: {emitida}"))
        pdf.ln(6)
    vence = _fmt_fecha(invoice.get("due_date"))
    if vence:
        pdf.cell(0, 6, _sanitizar_pdf(f"Vence: {vence}"))
        pdf.ln(6)
    pdf.cell(0, 6, _sanitizar_pdf(f"Cliente: {invoice.get('cliente_nombre', '')}"))
    pdf.ln(6)
    if invoice.get("cliente_email"):
        pdf.cell(0, 6, _sanitizar_pdf(f"Email: {invoice['cliente_email']}"))
        pdf.ln(6)
    pdf.ln(4)

    col_widths = (95, 20, 35, 30)
    headers = ("Descripcion", "Cant.", "Precio", "Total")
    pdf.set_font("Helvetica", "B", 10)
    for width, header in zip(col_widths, headers, strict=True):
        pdf.cell(width, 8, header, border=1)
    pdf.ln(8)

    pdf.set_font("Helvetica", size=10)
    for item in items:
        descripcion = _sanitizar_pdf(str(item.get("descripcion", ""))[:70])
        pdf.cell(col_widths[0], 8, descripcion, border=1)
        pdf.cell(col_widths[1], 8, _fmt_monto(item.get("cantidad", 0)), border=1, align="R")
        pdf.cell(col_widths[2], 8, _fmt_monto(item.get("precio_unitario", 0)), border=1, align="R")
        pdf.cell(col_widths[3], 8, _fmt_monto(item.get("total", 0)), border=1, align="R")
        pdf.ln(8)

    pdf.ln(4)
    pdf.set_font("Helvetica", size=10)
    subtotal_txt = _sanitizar_pdf(f"Subtotal: {moneda} {_fmt_monto(invoice.get('subtotal', 0))}")
    pdf.cell(0, 6, subtotal_txt, align="R")
    pdf.ln(6)
    impuestos_txt = _sanitizar_pdf(f"Impuestos: {moneda} {_fmt_monto(invoice.get('impuestos', 0))}")
    pdf.cell(0, 6, impuestos_txt, align="R")
    pdf.ln(7)
    pdf.set_font("Helvetica", "B", 12)
    total_txt = _sanitizar_pdf(f"Total: {moneda} {_fmt_monto(invoice.get('total', 0))}")
    pdf.cell(0, 8, total_txt, align="R")
    pdf.ln(10)

    if invoice.get("notas"):
        pdf.set_font("Helvetica", size=9)
        pdf.multi_cell(0, 5, _sanitizar_pdf(f"Notas: {invoice['notas']}"))

    # `set_auto_page_break(False)` ANTES del pie: es lo último que se dibuja, así que un
    # salto de página aquí nunca es deseable — pero sin desactivarlo, `set_y(-20)` (20mm
    # del borde inferior) más una celda de 10mm de alto termina 10mm del borde, DENTRO de
    # la franja de `margin=15` reservada arriba, y fpdf2 interpreta eso como "no cabe" y
    # agrega una página 2 en blanco solo para el pie — bug real detectado generando un PDF
    # de verdad contra Postgres/S3 real (`docs/negocios.md`), no algo que un test con bytes
    # `startswith(b"%PDF")` pueda atrapar.
    pdf.set_auto_page_break(auto=False)
    pdf.set_y(-20)
    pdf.set_font("Helvetica", "I", 8)
    pdf.cell(0, 10, "Generado por Edecan", align="C")

    return bytes(pdf.output())


# ---------------------------------------------------------------------------
# Orquestador: crear factura completa (draft + items + PDF + subida)
# ---------------------------------------------------------------------------


async def crear_factura(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    settings: Any,
    cliente_nombre: str,
    items: list[dict[str, Any]],
    impuestos_pct: Decimal | float | int | str = Decimal("0"),
    due_date: date | str | None = None,
    cliente_email: str | None = None,
    notas: str = "",
    moneda: str = _MONEDA_DEFECTO,
    uploader: Uploader | None = None,
) -> dict[str, Any]:
    """Crea una factura completa en un solo paso: valida cliente/items, calcula totales
    (`compute_totals`), numera (`next_numero`), inserta `invoices` (status=`draft`) +
    `invoice_items`, genera el PDF (`render_pdf`) y lo sube (`subir_pdf` por defecto — S3 +
    fila `files`), y enlaza `invoices.pdf_file_id`. Nunca envía nada ni mueve dinero: la
    factura nace `draft`.

    Única fuente de verdad para "crear una factura" — la usan tanto `tools.CrearFacturaTool`
    (pasando `ctx.session/.tenant_id/.user_id/.settings`) como
    `POST /v1/negocios/facturas` (con su propia sesión de tenant), para no duplicar esta
    orquestación en dos lugares. Lanza `ValueError` con un mensaje en español ante cualquier
    dato inválido (cliente vacío, items vacíos/negativos, fecha mal formada) — el llamador
    decide cómo mostrarlo (texto de chat vs. `422`).
    """
    cliente_nombre = (cliente_nombre or "").strip()
    if not cliente_nombre:
        raise ValueError("cliente_nombre es obligatorio.")
    items_normalizados = _normalizar_items(items)
    subtotal, impuestos, total = compute_totals(items_normalizados, impuestos_pct)
    moneda_norm = _normalizar_moneda(moneda)
    due_date_parsed = _parse_due_date(due_date)
    cliente_email = (cliente_email or "").strip() or None

    numero = await next_numero(session, tenant_id=tenant_id)

    row = (
        await session.execute(
            text(
                "INSERT INTO invoices "
                "(tenant_id, user_id, numero, cliente_nombre, cliente_email, moneda, "
                "subtotal, impuestos, total, status, due_date, notas) "
                "VALUES (:tenant_id ::uuid, :user_id ::uuid, :numero, :cliente_nombre, "
                ":cliente_email, :moneda, :subtotal, :impuestos, :total, 'draft', "
                ":due_date, :notas) RETURNING *"
            ),
            {
                "tenant_id": str(tenant_id),
                "user_id": str(user_id),
                "numero": numero,
                "cliente_nombre": cliente_nombre,
                "cliente_email": cliente_email,
                "moneda": moneda_norm,
                "subtotal": subtotal,
                "impuestos": impuestos,
                "total": total,
                "due_date": due_date_parsed,
                "notas": notas or "",
            },
        )
    ).mappings().first()
    if row is None:  # defensivo: no debería pasar nunca (acabamos de insertar).
        raise RuntimeError("No se pudo crear la factura (fila no devuelta por Postgres).")
    invoice = dict(row)
    invoice_id = invoice["id"]

    items_rows: list[dict[str, Any]] = []
    for item in items_normalizados:
        item_row = (
            await session.execute(
                text(
                    "INSERT INTO invoice_items "
                    "(tenant_id, invoice_id, descripcion, cantidad, precio_unitario, total) "
                    "VALUES (:tenant_id ::uuid, :invoice_id ::uuid, :descripcion, :cantidad, "
                    ":precio_unitario, :total) RETURNING *"
                ),
                {
                    "tenant_id": str(tenant_id),
                    "invoice_id": str(invoice_id),
                    "descripcion": item["descripcion"],
                    "cantidad": item["cantidad"],
                    "precio_unitario": item["precio_unitario"],
                    "total": item["total"],
                },
            )
        ).mappings().first()
        if item_row is None:  # defensivo: idem arriba.
            raise RuntimeError("No se pudo crear una línea de la factura.")
        items_rows.append(dict(item_row))

    pdf_bytes = render_pdf(invoice, items_rows)
    upload = uploader or subir_pdf
    file_id, filename = await upload(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        settings=settings,
        data=pdf_bytes,
        filename=f"{numero}.pdf",
        mime="application/pdf",
    )

    await session.execute(
        text(
            "UPDATE invoices SET pdf_file_id = :file_id ::uuid, updated_at = now() "
            "WHERE id = :id ::uuid"
        ),
        {"file_id": str(file_id), "id": str(invoice_id)},
    )
    await session.flush()

    invoice["pdf_file_id"] = file_id
    invoice["items"] = items_rows
    invoice["file_id"] = file_id
    invoice["filename"] = filename
    return invoice


# ---------------------------------------------------------------------------
# Lectura / transición de estado
# ---------------------------------------------------------------------------


async def listar_facturas(
    session: AsyncSession, *, tenant_id: UUID, status: str | None = None
) -> list[dict[str, Any]]:
    """Facturas del tenant, más recientes primero. `status`, si viene, filtra por ese
    estado exacto (`draft`/`sent`/`paid`/`void`) — `ValueError` si no es uno de esos cuatro."""
    if status is not None and status not in _ESTADOS:
        raise ValueError(f"status debe ser uno de {_ESTADOS}.")
    if status:
        result = await session.execute(
            text(
                "SELECT * FROM invoices WHERE tenant_id = :tenant_id ::uuid "
                "AND status = :status ORDER BY created_at DESC"
            ),
            {"tenant_id": str(tenant_id), "status": status},
        )
    else:
        result = await session.execute(
            text(
                "SELECT * FROM invoices WHERE tenant_id = :tenant_id ::uuid "
                "ORDER BY created_at DESC"
            ),
            {"tenant_id": str(tenant_id)},
        )
    return [dict(r) for r in result.mappings().all()]


async def obtener_factura(
    session: AsyncSession, *, tenant_id: UUID, invoice_id: UUID
) -> dict[str, Any] | None:
    """Una factura con sus `items` anidados, o `None` si no existe (o no es de este
    tenant — el `WHERE tenant_id = ...` hace que un id de otro tenant sea indistinguible de
    "no existe", igual que el resto de los recursos de la API)."""
    row = (
        await session.execute(
            text("SELECT * FROM invoices WHERE tenant_id = :tenant_id ::uuid AND id = :id ::uuid"),
            {"tenant_id": str(tenant_id), "id": str(invoice_id)},
        )
    ).mappings().first()
    if row is None:
        return None

    items_result = await session.execute(
        text(
            "SELECT * FROM invoice_items WHERE tenant_id = :tenant_id ::uuid "
            "AND invoice_id = :invoice_id ::uuid ORDER BY created_at"
        ),
        {"tenant_id": str(tenant_id), "invoice_id": str(invoice_id)},
    )
    invoice = dict(row)
    invoice["items"] = [dict(r) for r in items_result.mappings().all()]
    return invoice


async def cambiar_estado(
    session: AsyncSession, *, tenant_id: UUID, invoice_id: UUID, nuevo_status: str
) -> dict[str, Any] | None:
    """Transiciona el `status` de una factura (`draft→sent→paid`; `void` desde cualquier
    estado no-`void`, `ROADMAP_V2.md` §7.7). Solo toca `status`/`updated_at` — se mantiene
    deliberadamente simple, sin ningún metadato adicional de cobro (sin `paid_at` propio:
    `kpis.kpis_mes` usa `updated_at` como aproximación de "cuándo se cobró", ver su
    docstring; una columna dedicada queda como mejora P2 en `docs/negocios.md`).

    Devuelve `None` si la factura no existe (o no es de este tenant) — el llamador HTTP lo
    traduce a `404`. Lanza `ValueError` si `nuevo_status` no es uno de
    `sent`/`paid`/`void`, o `EstadoInvalidoError` (subclase de `ValueError`) si la
    transición concreta no está permitida desde el estado actual — el router distingue
    ambos casos para responder `422` vs `409` respectivamente.
    """
    if nuevo_status not in _TRANSICIONES_VALIDAS:
        raise ValueError(
            "status debe ser uno de "
            f"{sorted(_TRANSICIONES_VALIDAS)} (no se puede volver a 'draft')."
        )

    current = (
        await session.execute(
            text(
                "SELECT status FROM invoices "
                "WHERE tenant_id = :tenant_id ::uuid AND id = :id ::uuid"
            ),
            {"tenant_id": str(tenant_id), "id": str(invoice_id)},
        )
    ).mappings().first()
    if current is None:
        return None

    estado_actual = current["status"]
    permitidos = _TRANSICIONES_VALIDAS[nuevo_status]
    if estado_actual not in permitidos:
        raise EstadoInvalidoError(
            f"No se puede pasar de '{estado_actual}' a '{nuevo_status}' "
            f"(permitido solo desde: {', '.join(sorted(permitidos))})."
        )

    row = (
        await session.execute(
            text(
                "UPDATE invoices SET status = :status, updated_at = now() "
                "WHERE tenant_id = :tenant_id ::uuid AND id = :id ::uuid RETURNING *"
            ),
            {"status": nuevo_status, "tenant_id": str(tenant_id), "id": str(invoice_id)},
        )
    ).mappings().first()
    await session.flush()
    return dict(row) if row is not None else None
