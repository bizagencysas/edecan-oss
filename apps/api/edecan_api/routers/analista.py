"""`/v1/analista` — pantalla Analista: estadística, pronóstico/anomalías y gráficos SVG de
`edecan_docanalysis` expuestos por REST de solo lectura (WP-V6-06). Ver `docs/analista.md`
sección "Pantalla Analista" para el contrato completo (flujo de UI, ejemplos de respuesta).

## Por qué SIN flag de plan

`ARCHITECTURE.md` §15 pinnea el prefix `/v1/analista` sin exigir ningún flag de plan — paridad
DELIBERADA con las 8 tools de `edecan_docanalysis` (`analizar_tabla`, `predecir_serie`,
`detectar_anomalias`, `generar_grafico`, entre otras), que tampoco declaran `requires_flags`
(ver `edecan_docanalysis/__init__.py` y `docs/analista.md`: "ninguna es `dangerous` ni requiere
un flag de plan... están disponibles siempre, en todos los planes"). Esta pantalla es la MISMA
capacidad de análisis que ya ofrece el chat, solo con una superficie determinista de solo
lectura en vez de un turno de agente — no tendría sentido gatearla distinto a las tools que
reutiliza. El barrido de seguridad WP-V6-02 (mismo criterio que WP-V5-02/WP-V4-* antes: releer
los dominios bring-your-own/gated buscando fugas de flags finos, ver `HOTFIXES_PENDIENTES.md`)
puede verificar esta paridad releyendo este docstring + `edecan_docanalysis/__init__.py` +
`docs/analista.md` — los tres coinciden en "sin flag" para esta misma capacidad.

## Nunca llama al LLM — 100% determinista y offline

Los 4 endpoints de abajo SOLO usan la superficie pública PURA de `edecan_docanalysis` (WP-V6-06
la expuso a propósito para este router — ver el docstring de `edecan_docanalysis/__init__.py`):
descarga S3 (`descargar_archivo_de_tenant`), estadística/outliers (`analizar_tabla_bytes`),
filas crudas por columna (`extraer_columnas_bytes`), pronóstico/anomalías (`predecir`/
`detectar_anomalias`) y render de gráfico (`generar_svg`). Ninguna de esas cinco funciones
toca `ctx.llm`/`edecan_llm` — la vía CON LLM (preguntas en lenguaje natural sobre una tabla,
`analizar_tabla(pregunta=...)`) sigue existiendo exclusivamente por chat, nunca por esta
pantalla (ver `docs/analista.md`).

`edecan-docanalysis` YA es una dependencia declarada de `apps/api/pyproject.toml` desde
WP-V2-02 (`"edecan-docanalysis"` en `dependencies`, `edecan-docanalysis = { workspace = true }`
en `[tool.uv.sources]`) — este router no necesita agregar ninguna línea nueva ahí.

## Rutas (las 4 del contrato del paquete de trabajo — ninguna ruta extra)

- `GET /archivos` — lista los archivos del tenant filtrados a mimes tabulares (CSV/XLSX), vía
  `Repo.list_files` (mismo repo que ya usa `edecan_api.routers.files`, sin editar ese archivo).
- `POST /{file_id}/resumen` — `{hoja?}` → descarga el archivo → `analizar_tabla_bytes` →
  `{columnas, stats, outliers, filas_leidas, total_filas_archivo}` (la forma exacta que ya
  devuelve `ToolResult.data` de `AnalizarTablaTool`, sin inventar otra).
- `POST /{file_id}/forecast` — `{columna_fecha?, columna_valor?, horizonte?<=24}` → extrae la
  columna numérica (autodetectada si falta `columna_valor`) y llama `predecir` +
  `detectar_anomalias` sobre la MISMA serie — `{forecast, anomalias}` con el disclaimer de
  `predecir` intacto.
- `POST /{file_id}/grafico` — `{tipo, columna_x?, columna_y?}` → extrae dos columnas
  (autodetectadas si faltan) y llama `generar_svg` → `{"svg": "<svg ...>...</svg>"}`.

Errores: `404` si el archivo no existe o es de otro tenant (`descargar_archivo_de_tenant`
devuelve `None` — la misma fila `files` con `WHERE tenant_id = ...` que usa cualquier otro
router). `400` con el mensaje EXACTO de la función pura si el archivo no parsea o una columna
pedida no existe — nunca se reformula ese texto. Límite de tamaño: `edecan_docanalysis.tablas`
no trae un tope en bytes propio (solo topes de filas/columnas, ver su docstring) — este router
agrega uno de `_MAX_BYTES` (10 MB) con un mensaje claro ANTES de intentar parsear nada.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from edecan_docanalysis import (
    ArchivoDescargado,
    analizar_tabla_bytes,
    descargar_archivo_de_tenant,
    detectar_anomalias,
    extraer_columnas_bytes,
    generar_svg,
    predecir,
)
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.config import Settings, get_settings
from edecan_api.deps import (
    CurrentUser,
    get_current_user,
    get_repo,
    get_tenant_session,
    rate_limit,
)
from edecan_api.repo import Repo

router = APIRouter(prefix="/v1/analista", tags=["analista"], dependencies=[Depends(rate_limit)])

# CSV/XLSX — mismos dos mimes que soporta `analizar_tabla` (`tablas.py::_CSV_MIMES`/
# `_XLSX_MIMES`), más `.csv`/`.xlsx` por nombre de archivo como respaldo (varios navegadores/
# SO reportan un mime genérico como `application/octet-stream` al subir un CSV).
_MIMES_TABULARES = {
    "text/csv",
    "application/csv",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

# `edecan_docanalysis.tablas` no trae un límite de bytes propio (solo de filas/columnas ya
# leídas, ver su docstring) — 10 MB con comentario, tal como pide el enunciado del WP.
_MAX_BYTES = 10 * 1024 * 1024

# Mismo valor que `edecan_docanalysis.graficos._MAX_CATEGORIAS` (privado, no se importa entre
# paquetes) — capa el número de filas que este router intenta graficar de una tabla, para no
# reventar contra el propio límite de `generar_svg` con un mensaje confuso.
_MAX_PUNTOS_GRAFICO = 20

_HORIZONTE_MIN = 1
_HORIZONTE_MAX = 24
_HORIZONTE_DEFECTO = 6


def _es_tabular(mime: str | None, filename: str | None) -> bool:
    m = (mime or "").split(";")[0].strip().lower()
    nombre = (filename or "").lower()
    return m in _MIMES_TABULARES or nombre.endswith(".csv") or nombre.endswith(".xlsx")


def _a_numero(valor: Any) -> float | None:
    """Copia mínima de `edecan_docanalysis.tablas._to_number` (paquete hermano, no se importa
    — ARCHITECTURE.md §10.1: cada paquete lleva su propia copia). Solo se usa para la
    auto-detección de columnas numéricas y la extracción de series de este router."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    if isinstance(valor, str):
        s = valor.strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _es_vacio(valor: Any) -> bool:
    return valor is None or (isinstance(valor, str) and not valor.strip())


# ---------------------------------------------------------------------------
# Esquemas de entrada
# ---------------------------------------------------------------------------


class ResumenIn(BaseModel):
    hoja: str | None = None


class ForecastIn(BaseModel):
    columna_fecha: str | None = None
    columna_valor: str | None = None
    horizonte: int = Field(default=_HORIZONTE_DEFECTO, ge=_HORIZONTE_MIN, le=_HORIZONTE_MAX)


class GraficoIn(BaseModel):
    tipo: Literal["barras", "lineas", "dona"]
    columna_x: str | None = None
    columna_y: str | None = None


# ---------------------------------------------------------------------------
# Helpers compartidos
# ---------------------------------------------------------------------------


async def _descargar_o_404(
    session: AsyncSession, settings: Settings, tenant_id: uuid.UUID, file_id: uuid.UUID
) -> ArchivoDescargado:
    archivo = await descargar_archivo_de_tenant(session, settings, tenant_id, file_id)
    if archivo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Archivo no encontrado.")
    if len(archivo.contenido) > _MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"'{archivo.filename}' pesa más de {_MAX_BYTES // (1024 * 1024)} MB — es "
                "demasiado grande para analizar desde esta pantalla."
            ),
        )
    return archivo


def _validar_columna(nombre: str, encabezados: list[str]) -> None:
    if nombre not in encabezados:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"La columna «{nombre}» no existe en este archivo. Columnas disponibles: "
                f"{', '.join(encabezados)}."
            ),
        )


def _es_columna_numerica(filas: list[list[Any]], idx: int) -> bool:
    hay_datos = False
    for fila in filas:
        crudo = fila[idx] if idx < len(fila) else None
        if _es_vacio(crudo):
            continue
        hay_datos = True
        if _a_numero(crudo) is None:
            return False
    return hay_datos


def _autodetectar_columna(
    encabezados: list[str], filas: list[list[Any]], *, numerica: bool, excluir: str | None = None
) -> str | None:
    for i, nombre in enumerate(encabezados):
        if nombre == excluir:
            continue
        if _es_columna_numerica(filas, i) == numerica:
            return nombre
    return None


def _extraer_serie_y_etiquetas(
    encabezados: list[str],
    filas: list[list[Any]],
    columna_valor: str,
    columna_fecha: str | None,
) -> tuple[list[float], list[str] | None]:
    """Valores numéricos de `columna_valor` en orden de fila, saltando vacíos/no-numéricos
    (mismo criterio permisivo que `edecan_docanalysis.forecast._normalizar_valores` aplica a
    nulos/NaN — acá además se saltan strings no numéricos en vez de rechazar toda la serie,
    más forgiving para una columna real de un CSV con alguna celda sucia), junto con las
    etiquetas de `columna_fecha` (o `None` si no se pidió) extraídas en el MISMO recorrido —
    y por eso SIEMPRE alineadas índice a índice con `valores`. `predecir`/`detectar_anomalias`
    reciben este `valores` ya filtrado y devuelven posiciones (`outliers[].indice`) relativas
    a esa lista filtrada; si `etiquetas` se recorriera aparte sobre TODAS las filas sin
    filtrar, `etiquetas[o.indice]` quedaría desalineada en cuanto una fila anterior tuviera
    `columna_valor` vacío/no numérico. Una fila con `columna_fecha` vacía pero `columna_valor`
    válido SÍ se conserva (etiqueta `""`, igual que antes); solo se descarta la fila cuando
    `columna_valor` no parsea, nunca por causa de la fecha."""
    idx_valor = encabezados.index(columna_valor)
    idx_fecha = encabezados.index(columna_fecha) if columna_fecha else None

    valores: list[float] = []
    etiquetas: list[str] = []
    for fila in filas:
        crudo_valor = fila[idx_valor] if idx_valor < len(fila) else None
        numero = _a_numero(crudo_valor)
        if numero is None:
            continue
        valores.append(numero)
        if idx_fecha is not None:
            crudo_fecha = fila[idx_fecha] if idx_fecha < len(fila) else None
            etiquetas.append("" if _es_vacio(crudo_fecha) else str(crudo_fecha))
    return valores, etiquetas if columna_fecha else None


def _extraer_par_alineado(
    encabezados: list[str], filas: list[list[Any]], columna_x: str, columna_y: str
) -> tuple[list[str], list[float]]:
    """Etiquetas (`columna_x`) + valores numéricos (`columna_y`) alineados fila a fila,
    descartando cualquier fila donde CUALQUIERA de las dos esté vacía o `columna_y` no
    parsee como número — `generar_svg` (a diferencia de `predecir`/`detectar_anomalias`) no
    filtra nulos por su cuenta, exige `len(etiquetas) == len(valores)` exacto."""
    idx_x = encabezados.index(columna_x)
    idx_y = encabezados.index(columna_y)
    etiquetas: list[str] = []
    valores: list[float] = []
    for fila in filas:
        crudo_x = fila[idx_x] if idx_x < len(fila) else None
        crudo_y = fila[idx_y] if idx_y < len(fila) else None
        numero_y = _a_numero(crudo_y)
        if _es_vacio(crudo_x) or numero_y is None:
            continue
        etiquetas.append(str(crudo_x))
        valores.append(numero_y)
    return etiquetas, valores


# ---------------------------------------------------------------------------
# GET /archivos
# ---------------------------------------------------------------------------


@router.get("/archivos")
async def list_archivos(
    current_user: CurrentUser = Depends(get_current_user), repo: Repo = Depends(get_repo)
) -> list[dict[str, Any]]:
    rows = await repo.list_files(tenant_id=current_user.tenant_id)
    return [
        {
            "id": row["id"],
            "filename": row.get("filename"),
            "mime": row.get("mime"),
            "size_bytes": row.get("size_bytes"),
            "created_at": row.get("created_at"),
        }
        for row in rows
        if _es_tabular(row.get("mime"), row.get("filename"))
    ]


# ---------------------------------------------------------------------------
# POST /{file_id}/resumen
# ---------------------------------------------------------------------------


@router.post("/{file_id}/resumen")
async def resumen(
    file_id: uuid.UUID,
    body: ResumenIn = ResumenIn(),
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    archivo = await _descargar_o_404(session, settings, current_user.tenant_id, file_id)
    try:
        return analizar_tabla_bytes(
            archivo.contenido, mime=archivo.mime, filename=archivo.filename, hoja=body.hoja
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# POST /{file_id}/forecast
# ---------------------------------------------------------------------------


@router.post("/{file_id}/forecast")
async def forecast(
    file_id: uuid.UUID,
    body: ForecastIn = ForecastIn(),
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    archivo = await _descargar_o_404(session, settings, current_user.tenant_id, file_id)
    try:
        extraido = extraer_columnas_bytes(
            archivo.contenido, mime=archivo.mime, filename=archivo.filename
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    encabezados, filas = extraido["encabezados"], extraido["filas"]

    columna_valor = body.columna_valor
    if columna_valor:
        _validar_columna(columna_valor, encabezados)
    else:
        columna_valor = _autodetectar_columna(encabezados, filas, numerica=True)
        if columna_valor is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "No encontré ninguna columna numérica en este archivo para pronosticar; "
                    "indica 'columna_valor' explícitamente."
                ),
            )

    if body.columna_fecha:
        _validar_columna(body.columna_fecha, encabezados)

    valores, etiquetas = _extraer_serie_y_etiquetas(
        encabezados, filas, columna_valor, body.columna_fecha
    )

    try:
        forecast_resultado = predecir(valores, body.horizonte)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    anomalias_resultado: dict[str, Any] | None
    anomalias_error: str | None
    try:
        anomalias_resultado = detectar_anomalias(valores)
        anomalias_error = None
    except ValueError as exc:
        # La detección de anomalías es un "extra" sobre la misma serie — si la serie es
        # demasiado corta para `detectar_anomalias` (mínimo 4 valores) pero sí alcanzó para
        # `predecir` (mínimo 3), el pronóstico igual se devuelve completo; solo se pierde el
        # bloque de anomalías, con el motivo explícito en vez de tumbar todo el endpoint.
        anomalias_resultado = None
        anomalias_error = str(exc)

    return {
        "columna_valor": columna_valor,
        "columna_fecha": body.columna_fecha,
        "etiquetas": etiquetas,
        "forecast": forecast_resultado,
        "anomalias": anomalias_resultado,
        "anomalias_error": anomalias_error,
    }


# ---------------------------------------------------------------------------
# POST /{file_id}/grafico
# ---------------------------------------------------------------------------


@router.post("/{file_id}/grafico")
async def grafico(
    file_id: uuid.UUID,
    body: GraficoIn,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    archivo = await _descargar_o_404(session, settings, current_user.tenant_id, file_id)
    try:
        extraido = extraer_columnas_bytes(
            archivo.contenido, mime=archivo.mime, filename=archivo.filename
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    encabezados, filas = extraido["encabezados"], extraido["filas"]

    columna_y = body.columna_y
    if columna_y:
        _validar_columna(columna_y, encabezados)
    else:
        columna_y = _autodetectar_columna(encabezados, filas, numerica=True)
        if columna_y is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "No encontré ninguna columna numérica en este archivo para graficar; "
                    "indica 'columna_y' explícitamente."
                ),
            )

    columna_x = body.columna_x
    if columna_x:
        _validar_columna(columna_x, encabezados)
    else:
        columna_x = _autodetectar_columna(
            encabezados, filas, numerica=False, excluir=columna_y
        ) or _autodetectar_columna(encabezados, filas, numerica=True, excluir=columna_y)
        if columna_x is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Este archivo no tiene ninguna otra columna para usar como etiquetas.",
            )

    etiquetas, valores = _extraer_par_alineado(encabezados, filas, columna_x, columna_y)
    if not valores:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"No encontré ninguna fila con «{columna_x}» y «{columna_y}» completos a la "
                "vez — revisa que ambas columnas tengan datos."
            ),
        )

    puntos_totales = len(valores)
    truncado = puntos_totales > _MAX_PUNTOS_GRAFICO
    if truncado:
        etiquetas = etiquetas[:_MAX_PUNTOS_GRAFICO]
        valores = valores[:_MAX_PUNTOS_GRAFICO]

    titulo = f"{archivo.filename}: {columna_y} por {columna_x}"
    try:
        svg = generar_svg(body.tipo, titulo, etiquetas, valores=valores)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {
        "svg": svg,
        "tipo": body.tipo,
        "columna_x": columna_x,
        "columna_y": columna_y,
        "puntos_graficados": len(valores),
        "puntos_totales": puntos_totales,
        "truncado": truncado,
    }
