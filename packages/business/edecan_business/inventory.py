"""Inventario (ERP básico) — `products`/`stock_moves` (`ARCHITECTURE.md` §12/§10.3 vía el
work package v4 WP-V4-06; migración `0006` dueña de WP-V4-01, en paralelo — este módulo
codea contra el contrato pinned aunque la migración todavía no haya aterrizado, mismo
criterio que el resto del árbol v2/v3/v4).

Mismo criterio EXACTO que `invoices.py` (ver su docstring): funciones puras o con `session`
explícito (nunca `ToolContext`), SQL parametrizado con `sqlalchemy.text` (el contrato pinnea
tabla/columna, no un ORM), para que las use tanto `tools.py` (con `ctx.session`/
`ctx.tenant_id`/...) como `apps/api/edecan_api/routers/erp.py` (con su propia sesión de
tenant) sin duplicar la lógica de negocio en dos sitios.

Contrato pinned de las dos tablas (WP-V4-01, migración `0006`):

- `products(tenant_id, user_id, sku UNIQUE(tenant_id, sku), nombre, descripcion default '',
  unidad default 'unidad', precio numeric(14,2) null, costo numeric(14,2) null,
  stock numeric(14,3) default 0, stock_minimo numeric(14,3) default 0, activo bool
  default true)`.
- `stock_moves(tenant_id, product_id FK -> products, delta numeric(14,3), motivo in
  ('compra','venta','ajuste','merma','devolucion'), nota default '', ref null)` — tabla
  "hija" igual que `invoice_items`: también carga `tenant_id` (RLS + scoping directo, sin
  tener que pasar por un `JOIN` a `products` en cada consulta).

**Un producto nace SIEMPRE con `stock=0`** (el default de la columna — `crear_producto` no
acepta un `stock` inicial como argumento): la única forma de que `products.stock` cambie es
`registrar_movimiento`, que dentro de la MISMA transacción inserta la fila de auditoría en
`stock_moves` y actualiza `products.stock` — así nunca existe un stock "de la nada" sin su
movimiento correspondiente. Si un producto arranca con existencias reales, el llamador hace
`crear_producto` y luego un `registrar_movimiento(..., motivo="ajuste")` para dejarlas
asentadas (mismo criterio que `crear_factura` nace `draft` y una factura pagada requiere un
segundo paso explícito — nunca se infiere un estado "de arranque" con efectos que no dejaron
su propio rastro).

**`activo` se edita a través de `editar_producto`** (no hay una columna especial fuera de las
"editables" normales): `desactivar_producto` es un atajo de conveniencia sobre
`editar_producto(..., activo=False)` para que la tool `gestionar_inventario` (acción
`desactivar_producto`, ver `tools.py`) tenga una función dedicada sin tener que armar un
dict de un solo campo. El router HTTP en cambio NO expone una ruta dedicada de
desactivación — el contrato pinned del paquete de trabajo solo lista `PATCH /productos/{id}`
como la única ruta de edición, así que esa MISMA ruta acepta `{"activo": false}` en el
cuerpo (`edecan_api.routers.erp.ProductoUpdateIn`) para lograr el mismo efecto sin una ruta
HTTP nueva.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_UNIDAD_DEFECTO = "unidad"
_MOTIVOS_VALIDOS: tuple[str, ...] = ("compra", "venta", "ajuste", "merma", "devolucion")
_MOTIVO_AJUSTE = "ajuste"
_DOS_DECIMALES = Decimal("0.01")


class SkuDuplicadoError(ValueError):
    """`sku` ya usado por otro producto de este tenant (activo o no — `UNIQUE(tenant_id,
    sku)` es incondicional, así que un producto desactivado sigue "dueño" de su sku).
    Subclase de `ValueError` para que un llamador que solo distingue "error de validación"
    siga funcionando, pero `apps/api/edecan_api/routers/erp.py` la distingue explícitamente
    para responder `409 Conflict` (hay un recurso real en conflicto) en vez de `422`.

    **Carrera teórica, aceptada igual que `invoices.next_numero` (ver su docstring)**: el
    `SELECT` de chequeo y el `INSERT` posterior no están envueltos en un bloqueo — dos
    creaciones concurrentes con el mismo `sku` podrían ambas pasar el chequeo antes de que la
    primera confirme su `INSERT`. A diferencia de `invoices.numero`, aquí SÍ hay una
    restricción `UNIQUE(tenant_id, sku)` en el esquema pinned (§ arriba): contra Postgres
    real, la segunda inserción fallaría con una violación de esa restricción (no un `sku`
    duplicado silencioso) — un caso que este chequeo previo no cubre pero que la base de
    datos sí, como última línea de defensa. El patrón de uso esperado (una persona, o pocas,
    dando de alta productos desde la UI/el chat) hace este caso extremadamente improbable en
    la práctica.
    """


class StockInsuficienteError(ValueError):
    """`registrar_movimiento` dejaría `products.stock` negativo y `motivo` no es `'ajuste'`.
    Subclase de `ValueError` (mismo criterio que `EstadoInvalidoError` en `invoices.py`) para
    que `edecan_api.routers.erp` la distinga y responda `400 Bad Request` en vez de `422`."""


def _to_decimal(valor: Any, campo: str) -> Decimal:
    if valor is None or valor == "":
        raise ValueError(f"{campo} es obligatorio.")
    try:
        return Decimal(str(valor))
    except InvalidOperation as exc:
        raise ValueError(f"'{valor}' no es un valor numérico válido para {campo}.") from exc


def _to_decimal_opcional(valor: Any, campo: str) -> Decimal | None:
    """Como `_to_decimal`, pero `None`/`''` es válido (columna nullable: `precio`/`costo`) —
    en vez de "obligatorio", significa "sin valor asignado todavía"."""
    if valor is None or valor == "":
        return None
    return _to_decimal(valor, campo)


def _normalizar_sku(sku: str | None) -> str:
    """`sku` normalizado: recorta espacios y sube a mayúsculas (mismo criterio que
    `invoices._normalizar_moneda` con los códigos ISO de 3 letras) — dos capturas de
    `"abc-001"`/`"ABC-001"` deben resolver al MISMO producto, nunca crear dos filas."""
    sku_norm = (sku or "").strip().upper()
    if not sku_norm:
        raise ValueError("sku es obligatorio.")
    return sku_norm


def _round2(valor: Decimal) -> Decimal:
    return valor.quantize(_DOS_DECIMALES, rounding=ROUND_HALF_EVEN)


# ---------------------------------------------------------------------------
# Productos: crear / editar (+ desactivar) / listar
# ---------------------------------------------------------------------------


async def crear_producto(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    sku: str,
    nombre: str,
    descripcion: str = "",
    unidad: str = _UNIDAD_DEFECTO,
    precio: Decimal | float | int | str | None = None,
    costo: Decimal | float | int | str | None = None,
    stock_minimo: Decimal | float | int | str | None = 0,
) -> dict[str, Any]:
    """Crea un producto nuevo (`stock=0` — ver el docstring del módulo). Rechaza con
    `SkuDuplicadoError` si `sku` (normalizado) ya existe para este tenant, y con
    `ValueError` ante cualquier otro dato inválido (nombre vacío, montos negativos).
    """
    sku_norm = _normalizar_sku(sku)
    nombre_norm = str(nombre or "").strip()
    if not nombre_norm:
        raise ValueError("nombre es obligatorio.")
    unidad_norm = str(unidad or "").strip() or _UNIDAD_DEFECTO

    precio_dec = _to_decimal_opcional(precio, "precio")
    if precio_dec is not None and precio_dec < 0:
        raise ValueError("precio no puede ser negativo.")
    costo_dec = _to_decimal_opcional(costo, "costo")
    if costo_dec is not None and costo_dec < 0:
        raise ValueError("costo no puede ser negativo.")
    stock_minimo_dec = _to_decimal(stock_minimo if stock_minimo is not None else 0, "stock_minimo")
    if stock_minimo_dec < 0:
        raise ValueError("stock_minimo no puede ser negativo.")

    existente = (
        await session.execute(
            text("SELECT id FROM products WHERE tenant_id = :tenant_id ::uuid AND sku = :sku"),
            {"tenant_id": str(tenant_id), "sku": sku_norm},
        )
    ).mappings().first()
    if existente is not None:
        raise SkuDuplicadoError(f"Ya existe un producto con sku «{sku_norm}» en este negocio.")

    row = (
        await session.execute(
            text(
                "INSERT INTO products "
                "(tenant_id, user_id, sku, nombre, descripcion, unidad, precio, costo, "
                "stock_minimo) "
                "VALUES (:tenant_id ::uuid, :user_id ::uuid, :sku, :nombre, :descripcion, "
                ":unidad, :precio, :costo, :stock_minimo) RETURNING *"
            ),
            {
                "tenant_id": str(tenant_id),
                "user_id": str(user_id),
                "sku": sku_norm,
                "nombre": nombre_norm,
                "descripcion": descripcion or "",
                "unidad": unidad_norm,
                "precio": precio_dec,
                "costo": costo_dec,
                "stock_minimo": stock_minimo_dec,
            },
        )
    ).mappings().first()
    if row is None:  # defensivo: no debería pasar nunca (acabamos de insertar).
        raise RuntimeError("No se pudo crear el producto (fila no devuelta por Postgres).")
    await session.flush()
    return dict(row)


async def editar_producto(
    session: AsyncSession, *, tenant_id: UUID, product_id: UUID, **cambios: Any
) -> dict[str, Any] | None:
    """Actualiza parcialmente un producto existente (por `id`). Solo toca los campos
    EXPLÍCITAMENTE presentes en `cambios` (mismo criterio que
    `edecan_toolkit.contactos.GestionarContactoTool`: una clave ausente significa "no la
    toques", nunca "bórrala") — los únicos campos editables son `nombre`/`descripcion`/
    `unidad`/`precio`/`costo`/`stock_minimo`/`activo`; cualquier otra clave (p. ej. `sku`,
    `stock`, `id`) se ignora en silencio, nunca revienta ni se aplica. `sku` queda fuera a
    propósito (identidad del producto, protegida por `UNIQUE(tenant_id, sku)`) y `stock`
    también (solo cambia vía `registrar_movimiento`, para que todo cambio de existencias
    deje su rastro en `stock_moves`).

    Siempre ejecuta un único `UPDATE` (aunque `cambios` esté vacío: en ese caso solo toca
    `updated_at`) en vez de ramificar a un `SELECT` cuando no hay nada que cambiar — mantiene
    el conteo de sentencias SQL uniforme y predecible sin importar cuántos campos vengan.

    Devuelve `None` si el producto no existe (o no es de este tenant) — el llamador HTTP lo
    traduce a `404`. Lanza `ValueError` ante un dato inválido (nombre vacío, montos
    negativos, `activo=None`).
    """
    set_parts: list[str] = []
    params: dict[str, Any] = {"id": str(product_id), "tenant_id": str(tenant_id)}

    if "nombre" in cambios:
        nombre_norm = str(cambios["nombre"] or "").strip()
        if not nombre_norm:
            raise ValueError("nombre no puede quedar vacío.")
        set_parts.append("nombre = :nombre")
        params["nombre"] = nombre_norm
    if "descripcion" in cambios:
        set_parts.append("descripcion = :descripcion")
        params["descripcion"] = cambios["descripcion"] or ""
    if "unidad" in cambios:
        set_parts.append("unidad = :unidad")
        params["unidad"] = str(cambios["unidad"] or "").strip() or _UNIDAD_DEFECTO
    if "precio" in cambios:
        precio_dec = _to_decimal_opcional(cambios["precio"], "precio")
        if precio_dec is not None and precio_dec < 0:
            raise ValueError("precio no puede ser negativo.")
        set_parts.append("precio = :precio")
        params["precio"] = precio_dec
    if "costo" in cambios:
        costo_dec = _to_decimal_opcional(cambios["costo"], "costo")
        if costo_dec is not None and costo_dec < 0:
            raise ValueError("costo no puede ser negativo.")
        set_parts.append("costo = :costo")
        params["costo"] = costo_dec
    if "stock_minimo" in cambios:
        stock_minimo_dec = _to_decimal(cambios["stock_minimo"], "stock_minimo")
        if stock_minimo_dec < 0:
            raise ValueError("stock_minimo no puede ser negativo.")
        set_parts.append("stock_minimo = :stock_minimo")
        params["stock_minimo"] = stock_minimo_dec
    if "activo" in cambios:
        if cambios["activo"] is None:
            raise ValueError("activo no puede ser nulo.")
        set_parts.append("activo = :activo")
        params["activo"] = bool(cambios["activo"])

    set_sql = ", ".join([*set_parts, "updated_at = now()"])
    row = (
        await session.execute(
            text(
                f"UPDATE products SET {set_sql} "
                "WHERE id = :id ::uuid AND tenant_id = :tenant_id ::uuid RETURNING *"
            ),
            params,
        )
    ).mappings().first()
    await session.flush()
    return dict(row) if row is not None else None


async def desactivar_producto(
    session: AsyncSession, *, tenant_id: UUID, product_id: UUID
) -> dict[str, Any] | None:
    """`activo = false` (soft-delete: nunca se borra un producto que puede tener movimientos
    históricos en `stock_moves`) — atajo sobre `editar_producto(..., activo=False)`. Ver el
    docstring del módulo para por qué el router HTTP no tiene una ruta dedicada equivalente.
    Idempotente: desactivar un producto ya inactivo simplemente lo deja igual."""
    return await editar_producto(session, tenant_id=tenant_id, product_id=product_id, activo=False)


async def listar_productos(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    activo: bool | None = None,
    q: str | None = None,
) -> list[dict[str, Any]]:
    """Productos del tenant, alfabético por nombre. `activo` filtra por activo/inactivo (por
    defecto, ambos); `q` busca por `sku` o `nombre` (`ILIKE`, sin distinguir mayúsculas)."""
    params: dict[str, Any] = {"tenant_id": str(tenant_id)}
    filtro_sql = ""
    if activo is not None:
        filtro_sql += " AND activo = :activo"
        params["activo"] = activo
    if q:
        filtro_sql += " AND (sku ILIKE :patron OR nombre ILIKE :patron)"
        params["patron"] = f"%{q}%"

    result = await session.execute(
        text(
            "SELECT * FROM products WHERE tenant_id = :tenant_id ::uuid"
            f"{filtro_sql} ORDER BY nombre ASC"
        ),
        params,
    )
    return [dict(r) for r in result.mappings().all()]


async def obtener_producto_por_sku(
    session: AsyncSession, *, tenant_id: UUID, sku: str
) -> dict[str, Any] | None:
    """Un producto por `sku` normalizado, o `None` si no existe (o no es de este tenant) —
    usado por `tools.py` para que la tool `gestionar_inventario` identifique productos por
    `sku` (nunca le pide un `id`/UUID al modelo, mismo criterio que el resto del toolkit:
    ninguna otra tool de este repo le pide un UUID crudo al LLM)."""
    sku_norm = _normalizar_sku(sku)
    row = (
        await session.execute(
            text("SELECT * FROM products WHERE tenant_id = :tenant_id ::uuid AND sku = :sku"),
            {"tenant_id": str(tenant_id), "sku": sku_norm},
        )
    ).mappings().first()
    return dict(row) if row is not None else None


# ---------------------------------------------------------------------------
# Movimientos de stock
# ---------------------------------------------------------------------------


async def registrar_movimiento(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    product_id: UUID,
    delta: Decimal | float | int | str,
    motivo: str,
    nota: str = "",
    ref: str | None = None,
) -> dict[str, Any] | None:
    """Registra un movimiento de stock (`INSERT` en `stock_moves`, con `user_id` = quién lo
    registró — columna `NOT NULL` del contrato pinned, `ARCHITECTURE.md` §13.b) y actualiza
    `products.stock = stock + :delta` en la MISMA transacción — sin commit intermedio, así
    que si algo falla después de este punto, ambas escrituras se revierten juntas (mismo
    principio que `crear_factura`: varias escrituras relacionadas, una sola transacción de
    request).

    `delta` es positivo para entradas (`compra`, `devolucion`, o un `ajuste` al alza) y
    negativo para salidas (`venta`, `merma`, o un `ajuste` a la baja) — nunca cero (un
    movimiento de cero no cambia nada y no deja ningún rastro útil que auditar).

    Con cualquier `motivo` salvo `'ajuste'`, la operación se RECHAZA (`StockInsuficienteError`)
    si dejaría `stock` resultante negativo — refleja la realidad física de un inventario (no
    se puede vender/mermar lo que no se tiene). Con `motivo='ajuste'` SÍ se permite dejar
    stock negativo: es la vía explícita de corrección administrativa (p. ej. reconciliar un
    conteo físico, o corregir un movimiento mal capturado antes) — el usuario asumió esa
    decisión a propósito al elegir ese motivo.

    El `SELECT` inicial de `products` usa `FOR UPDATE`: toma el lock de fila ANTES de calcular
    `nuevo_stock`, para que dos llamadas concurrentes sobre el MISMO producto se serialicen
    (la segunda espera a que la primera confirme) en vez de correr la carrera check-then-act
    de comparar contra un `stock` que ya quedó obsoleto. A diferencia de `SkuDuplicadoError`/
    `invoices.next_numero` (ver sus docstrings, carreras teóricas ACEPTADAS a propósito),
    aquí la carrera se cierra del todo y sin migración nueva: `products` ya es una fila que
    se puede bloquear (antes este `SELECT` no la bloqueaba y el lock real solo llegaba en el
    `UPDATE` de más abajo, cuando el cálculo de `nuevo_stock` ya había pasado). Bajo Postgres,
    un `SELECT ... FOR UPDATE` que esperó un lock relee el valor YA confirmado por la
    transacción anterior al desbloquearse (no la foto vieja de antes de esperar), así que
    `nuevo_stock` siempre se calcula contra el stock real más reciente.

    Devuelve `None` si el producto no existe (o no es de este tenant) — el llamador HTTP lo
    traduce a `404`. El dict devuelto es la fila de `stock_moves` más una clave extra
    `"producto"` con el producto YA actualizado (para que el llamador no tenga que volver a
    leerlo)."""
    motivo_norm = str(motivo or "").strip().lower()
    if motivo_norm not in _MOTIVOS_VALIDOS:
        raise ValueError(f"motivo debe ser uno de {_MOTIVOS_VALIDOS}.")
    delta_dec = _to_decimal(delta, "delta")
    if delta_dec == 0:
        raise ValueError("delta no puede ser cero.")

    producto = (
        await session.execute(
            text(
                "SELECT * FROM products WHERE id = :id ::uuid AND tenant_id = :tenant_id ::uuid "
                "FOR UPDATE"
            ),
            {"id": str(product_id), "tenant_id": str(tenant_id)},
        )
    ).mappings().first()
    if producto is None:
        return None

    stock_actual = Decimal(str(producto["stock"]))
    nuevo_stock = stock_actual + delta_dec
    if nuevo_stock < 0 and motivo_norm != _MOTIVO_AJUSTE:
        raise StockInsuficienteError(
            f"Ese movimiento dejaría el stock en {nuevo_stock} (negativo); solo se permite "
            "quedar en negativo con motivo='ajuste'."
        )

    move_row = (
        await session.execute(
            text(
                "INSERT INTO stock_moves "
                "(tenant_id, user_id, product_id, delta, motivo, nota, ref) "
                "VALUES (:tenant_id ::uuid, :user_id ::uuid, :product_id ::uuid, :delta, "
                ":motivo, :nota, :ref) RETURNING *"
            ),
            {
                "tenant_id": str(tenant_id),
                "user_id": str(user_id),
                "product_id": str(product_id),
                "delta": delta_dec,
                "motivo": motivo_norm,
                "nota": nota or "",
                "ref": ref,
            },
        )
    ).mappings().first()
    if move_row is None:  # defensivo: no debería pasar nunca (acabamos de insertar).
        raise RuntimeError("No se pudo registrar el movimiento (fila no devuelta por Postgres).")

    actualizado = (
        await session.execute(
            text(
                "UPDATE products SET stock = stock + :delta, updated_at = now() "
                "WHERE id = :id ::uuid AND tenant_id = :tenant_id ::uuid RETURNING *"
            ),
            {"delta": delta_dec, "id": str(product_id), "tenant_id": str(tenant_id)},
        )
    ).mappings().first()
    if actualizado is None:  # defensivo: ya confirmamos arriba que el producto existe.
        raise RuntimeError("No se pudo actualizar el stock del producto.")
    await session.flush()

    resultado = dict(move_row)
    resultado["producto"] = dict(actualizado)
    return resultado


# ---------------------------------------------------------------------------
# Resumen
# ---------------------------------------------------------------------------


async def resumen_inventario(session: AsyncSession, *, tenant_id: UUID) -> dict[str, Any]:
    """Resumen del inventario ACTIVO del tenant: total de SKUs, valor total a costo y a
    precio de venta (`Σ stock·costo` / `Σ stock·precio`, ignorando productos sin costo/precio
    fijado — no se puede valorar lo que no tiene un monto asignado), y la lista de productos
    con stock bajo (`stock <= stock_minimo`: quedar EXACTO en el mínimo ya cuenta como alerta,
    no hace falta cruzarlo). Productos desactivados no cuentan para nada de esto — un producto
    que ya no se vende no debería inflar (ni el valor, ni una alerta de stock bajo) el
    inventario "vivo" del negocio."""
    result = await session.execute(
        text(
            "SELECT id, sku, nombre, stock, stock_minimo, precio, costo FROM products "
            "WHERE tenant_id = :tenant_id ::uuid AND activo = true"
        ),
        {"tenant_id": str(tenant_id)},
    )
    filas = result.mappings().all()

    valor_costo = Decimal("0")
    valor_precio = Decimal("0")
    stock_bajo: list[dict[str, Any]] = []
    for fila in filas:
        stock = Decimal(str(fila["stock"]))
        if fila["costo"] is not None:
            valor_costo += stock * Decimal(str(fila["costo"]))
        if fila["precio"] is not None:
            valor_precio += stock * Decimal(str(fila["precio"]))

        stock_minimo = Decimal(str(fila["stock_minimo"]))
        if stock <= stock_minimo:
            stock_bajo.append(
                {
                    "id": str(fila["id"]),
                    "sku": fila["sku"],
                    "nombre": fila["nombre"],
                    "stock": float(stock),
                    "stock_minimo": float(stock_minimo),
                }
            )

    return {
        "total_skus": len(filas),
        "valor_costo": float(_round2(valor_costo)),
        "valor_precio": float(_round2(valor_precio)),
        "stock_bajo": stock_bajo,
    }
