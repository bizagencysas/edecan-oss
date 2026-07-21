"""`/v1/perfil` — el «perfil vivo» del usuario (WP-V2-13, ROADMAP_V2.md §21/§7.4,
§7.6; ver también `apps/worker/edecan_worker/handlers/memory_consolidate.py`
"Fase 3" para cómo se CONSTRUYE, y `docs/perfil-vivo.md` para la explicación
completa de producto).

`edecan_api.main.create_app()` YA monta este router de forma defensiva
(`perfil` está en `V2_ROUTER_NAMES` desde WP-V2-01) — este módulo solo tiene
que existir y exportar `router`.

## Rutas

- `GET /v1/perfil` — perfil actual, o el esqueleto vacío (`version: 0`) si el
  usuario todavía no tiene fila en `user_profiles` (nunca corrió
  `memory_consolidate`, o corrió pero no había memorias que consolidar).
- `PUT /v1/perfil` — edición manual (`resumen`/`datos` opcionales, patch
  parcial; dentro de `datos`, cada una de las 6 categorías también es
  opcional). Válida shape (Pydantic) y caps (`LISTA_MAX_ITEMS`/
  `RESUMEN_MAX_CHARS`, reutilizados de `edecan_core.memory.profile` — misma
  fuente de verdad que usa `build_profile`). `version += 1`.
- `DELETE /v1/perfil` — borra la fila y el espejo en `memory_items`
  (`source="perfil_vivo"`): derecho a reset del usuario.
- `POST /v1/perfil/rebuild` — encola el job `memory_consolidate` (el MISMO
  que corre tras cada turno, no uno especial "solo perfil") para este
  tenant/usuario y responde `202` de inmediato; la reconstrucción real ocurre
  async en el worker, ver el docstring de ese handler.

## Por qué SQL directo (no `edecan_api.repo.Repo`)

Igual que `edecan_api.routers.commerce` (WP-V2-10) para `orders`/`holdings`/
`budgets`: `user_profiles` es una tabla nueva de v2 y `edecan_api.repo.Repo`/
`SqlRepo` no tienen (ni este paquete de trabajo puede agregarles, para no
chocar con los demás WPs que corren en paralelo) métodos para ella. A
diferencia de `commerce.py` cuando se escribió, la migración `0003_v2_expansion`
YA incluye `user_profiles` (con `UNIQUE(tenant_id, user_id)` y RLS) — este
router funciona contra Postgres real sin nada pendiente.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from edecan_core.memory.profile import CAMPOS_DATOS, LISTA_MAX_ITEMS, RESUMEN_MAX_CHARS
from edecan_core.queue import enqueue
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.config import Settings, get_settings
from edecan_api.deps import CurrentUser, get_current_user, get_tenant_session, rate_limit

router = APIRouter(prefix="/v1/perfil", tags=["perfil"], dependencies=[Depends(rate_limit)])

IDENTITY_FIELDS: tuple[str, ...] = (
    "nombre_preferido",
    "nombre_completo",
    "pronombres",
    "fecha_nacimiento",
    "pais",
    "ciudad",
    "zona_horaria",
    "ocupacion",
    "idioma_preferido",
    "forma_de_trato",
    "biografia",
)
IDENTITY_FIELD_MAX_CHARS = 160
BIOGRAPHY_MAX_CHARS = 1_000


# ---------------------------------------------------------------------------
# Esquemas de entrada — `PUT` es un patch parcial en dos niveles: el top
# (resumen/datos) Y, dentro de `datos`, cada categoría (ver docstring del
# módulo). `max_length` en cada lista reutiliza `LISTA_MAX_ITEMS` de
# `edecan_core.memory.profile` — la MISMA constante que aplica el merge
# automático de `build_profile`, para que la edición manual nunca permita
# algo que la consolidación automática rechazaría.
# ---------------------------------------------------------------------------


class IdentidadPerfilIn(BaseModel):
    """Patch de la identidad declarada por la persona.

    Todos los campos son opcionales para que iOS, Android y web puedan guardar
    una sección sin pisar el resto. La identidad nunca la reescribe el job de
    consolidación automática.
    """

    nombre_preferido: str | None = Field(default=None, max_length=IDENTITY_FIELD_MAX_CHARS)
    nombre_completo: str | None = Field(default=None, max_length=IDENTITY_FIELD_MAX_CHARS)
    pronombres: str | None = Field(default=None, max_length=IDENTITY_FIELD_MAX_CHARS)
    fecha_nacimiento: str | None = Field(default=None, max_length=IDENTITY_FIELD_MAX_CHARS)
    pais: str | None = Field(default=None, max_length=IDENTITY_FIELD_MAX_CHARS)
    ciudad: str | None = Field(default=None, max_length=IDENTITY_FIELD_MAX_CHARS)
    zona_horaria: str | None = Field(default=None, max_length=IDENTITY_FIELD_MAX_CHARS)
    ocupacion: str | None = Field(default=None, max_length=IDENTITY_FIELD_MAX_CHARS)
    idioma_preferido: str | None = Field(default=None, max_length=IDENTITY_FIELD_MAX_CHARS)
    forma_de_trato: str | None = Field(default=None, max_length=IDENTITY_FIELD_MAX_CHARS)
    biografia: str | None = Field(default=None, max_length=BIOGRAPHY_MAX_CHARS)


class DatosPerfilIn(BaseModel):
    """Sub-objeto de `PUT /v1/perfil`. Todas las categorías son opcionales:
    solo las que vengan en el body se sobreescriben (con la lista COMPLETA
    que mandes — no es "agregar un item", es "esta es la lista nueva"; el
    add/remove de un chip individual lo resuelve el cliente armando la lista
    resultante, ver `apps/web/src/app/(app)/app/perfil-vivo/page.tsx`)."""

    identidad: IdentidadPerfilIn | None = None
    gustos: list[str] | None = Field(default=None, max_length=LISTA_MAX_ITEMS)
    proyectos: list[str] | None = Field(default=None, max_length=LISTA_MAX_ITEMS)
    metas: list[str] | None = Field(default=None, max_length=LISTA_MAX_ITEMS)
    relaciones: list[str] | None = Field(default=None, max_length=LISTA_MAX_ITEMS)
    empresas: list[str] | None = Field(default=None, max_length=LISTA_MAX_ITEMS)
    habitos: list[str] | None = Field(default=None, max_length=LISTA_MAX_ITEMS)


class PerfilIn(BaseModel):
    resumen: str | None = Field(default=None, max_length=RESUMEN_MAX_CHARS)
    datos: DatosPerfilIn | None = None


# ---------------------------------------------------------------------------
# Helpers SQL — parametrizado directo (ver "Por qué SQL directo" arriba).
# ---------------------------------------------------------------------------


async def _first(session: AsyncSession, stmt: str, params: dict[str, Any]) -> dict[str, Any] | None:
    result = await session.execute(text(stmt), params)
    row = result.mappings().first()
    return dict(row) if row is not None else None


def _from_jsonb(value: Any) -> dict[str, Any]:
    """`datos` puede llegar como `dict` ya decodificado o como texto JSON
    crudo según el driver — mismo criterio defensivo que
    `edecan_api.routers.commerce._from_jsonb` (duplicado a propósito, ver
    ARCHITECTURE.md §10.1)."""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            cargado = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return cargado if isinstance(cargado, dict) else {}
    return {}


def _identidad_vacia() -> dict[str, str]:
    return {campo: "" for campo in IDENTITY_FIELDS}


def _completar_identidad(value: Any) -> dict[str, str]:
    raw = value if isinstance(value, dict) else {}
    identidad = _identidad_vacia()
    for campo in IDENTITY_FIELDS:
        value = raw.get(campo)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            limite = BIOGRAPHY_MAX_CHARS if campo == "biografia" else IDENTITY_FIELD_MAX_CHARS
            identidad[campo] = str(value).strip()[:limite]
    return identidad


def _completar_datos(datos: dict[str, Any]) -> dict[str, Any]:
    """Normaliza identidad explícita y las seis categorías aprendidas."""
    listas = {
        campo: [str(v) for v in (datos.get(campo) or []) if isinstance(v, (str, int, float))]
        for campo in CAMPOS_DATOS
    }
    return {"identidad": _completar_identidad(datos.get("identidad")), **listas}


def _fila_a_perfil(row: dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {
            "resumen": "",
            "datos": {"identidad": _identidad_vacia(), **{campo: [] for campo in CAMPOS_DATOS}},
            "version": 0,
            "updated_at": None,
        }
    return {
        "resumen": row.get("resumen") or "",
        "datos": _completar_datos(_from_jsonb(row.get("datos"))),
        "version": row.get("version", 0),
        "updated_at": row.get("updated_at"),
    }


async def _obtener_fila(
    session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> dict[str, Any] | None:
    return await _first(
        session,
        "SELECT * FROM user_profiles WHERE tenant_id = :tenant_id AND user_id = :user_id",
        {"tenant_id": tenant_id, "user_id": user_id},
    )


async def _upsert_fila(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    resumen: str,
    datos: dict[str, Any],
    version: int,
) -> dict[str, Any]:
    # Espacio antes de `::jsonb` obligatorio: el regex de bind params de
    # SQLAlchemy no reconoce ":datos" como parámetro si lo sigue otro ":"
    # pegado (mismo bug ya corregido en `edecan_api.repo`) — sin el espacio,
    # este INSERT queda con "datos" como texto literal y Postgres revienta
    # (nunca se vio porque los tests corren contra Postgres fake, no real).
    fila = await _first(
        session,
        """
        INSERT INTO user_profiles (
            id, tenant_id, user_id, resumen, datos, version, created_at, updated_at
        ) VALUES (
            :id, :tenant_id, :user_id, :resumen, :datos ::jsonb, :version, :now, :now
        )
        ON CONFLICT (tenant_id, user_id) DO UPDATE
        SET resumen = EXCLUDED.resumen,
            datos = EXCLUDED.datos,
            version = EXCLUDED.version,
            updated_at = EXCLUDED.updated_at
        RETURNING *
        """,
        {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "user_id": user_id,
            "resumen": resumen,
            "datos": json.dumps(datos),
            "version": version,
            "now": datetime.now(UTC),
        },
    )
    assert fila is not None
    return fila


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------


@router.get("")
async def get_perfil(
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    fila = await _obtener_fila(session, current_user.tenant_id, current_user.user_id)
    return _fila_a_perfil(fila)


@router.put("")
async def put_perfil(
    body: PerfilIn,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    fila_actual = await _obtener_fila(session, current_user.tenant_id, current_user.user_id)
    actual = _fila_a_perfil(fila_actual)

    nuevo_resumen = body.resumen if body.resumen is not None else actual["resumen"]

    nuevos_datos = dict(actual["datos"])
    if body.datos is not None:
        if body.datos.identidad is not None:
            identidad = dict(nuevos_datos.get("identidad") or _identidad_vacia())
            for campo in IDENTITY_FIELDS:
                valor = getattr(body.datos.identidad, campo)
                if valor is not None:
                    identidad[campo] = valor.strip()
            nuevos_datos["identidad"] = identidad
        for campo in CAMPOS_DATOS:
            valor = getattr(body.datos, campo)
            if valor is not None:
                nuevos_datos[campo] = list(valor)

    nueva_version = actual["version"] + 1
    fila_guardada = await _upsert_fila(
        session,
        current_user.tenant_id,
        current_user.user_id,
        resumen=nuevo_resumen,
        datos=nuevos_datos,
        version=nueva_version,
    )
    return _fila_a_perfil(fila_guardada)


async def profile_context_for(
    session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> str:
    """Contexto estable que se inyecta en cada turno del agente.

    No usa búsqueda semántica: el nombre, el trato y el contexto declarado
    nunca deben desaparecer solo porque el texto del mensaje actual no sea
    parecido al resumen del perfil.
    """

    perfil = _fila_a_perfil(await _obtener_fila(session, tenant_id, user_id))
    identidad = perfil["datos"]["identidad"]
    lineas = ["Perfil personal declarado por la persona (fuente de verdad):"]
    etiquetas = {
        "nombre_preferido": "Nombre preferido",
        "nombre_completo": "Nombre completo",
        "pronombres": "Pronombres",
        "fecha_nacimiento": "Fecha de nacimiento",
        "pais": "País",
        "ciudad": "Ciudad",
        "zona_horaria": "Zona horaria",
        "ocupacion": "Ocupación",
        "idioma_preferido": "Idioma preferido",
        "forma_de_trato": "Cómo quiere que le hables",
        "biografia": "Sobre la persona",
    }
    for campo in IDENTITY_FIELDS:
        valor = identidad.get(campo, "").strip()
        if valor:
            lineas.append(f"- {etiquetas[campo]}: {valor}")
    resumen = str(perfil.get("resumen") or "").strip()
    if resumen:
        lineas.append(f"- Síntesis del perfil vivo: {resumen}")
    for campo in CAMPOS_DATOS:
        items = perfil["datos"].get(campo) or []
        if items:
            lineas.append(f"- {campo.capitalize()}: " + "; ".join(items))
    return "\n".join(lineas) if len(lineas) > 1 else ""


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_perfil(
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    """Derecho a reset: borra la fila de `user_profiles` Y el espejo en
    `memory_items` (`source="perfil_vivo"`) — si solo se borrara la fila, el
    espejo seguiría inyectándose en cada turno como si el perfil aún
    existiera (ver `memory_consolidate.py`, "Fase 3")."""
    params = {"tenant_id": current_user.tenant_id, "user_id": current_user.user_id}
    await session.execute(
        text("DELETE FROM user_profiles WHERE tenant_id = :tenant_id AND user_id = :user_id"),
        params,
    )
    await session.execute(
        text(
            "DELETE FROM memory_items WHERE tenant_id = :tenant_id AND user_id = :user_id "
            "AND source = 'perfil_vivo'"
        ),
        params,
    )


@router.post("/rebuild", status_code=status.HTTP_202_ACCEPTED)
async def rebuild_perfil(
    current_user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Encola el MISMO job `memory_consolidate` que corre tras cada turno de
    chat (`edecan_api.routers.conversations._stream_agent_events`) — no un
    job especial "solo perfil": vuelve a extraer memorias nuevas de los
    mensajes recientes Y a reconstruir el perfil sobre el resultado. Puede
    tardar unos segundos; el cliente web lo comunica y ofrece recargar."""
    job_id = await enqueue(
        settings,
        "memory_consolidate",
        {"user_id": str(current_user.user_id)},
        current_user.tenant_id,
    )
    return {
        "job_id": str(job_id),
        "mensaje": (
            "Reconstrucción encolada. Puede tardar unos segundos; vuelve a cargar el perfil "
            "en un momento."
        ),
    }
