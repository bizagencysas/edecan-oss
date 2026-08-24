"""`/v1/skills` — marketplace de "Agent Skills" (estándar abierto que indexa
skills.sh, `ARCHITECTURE.md` §12.a/§12.e; `DIRECCION_ACTUAL.md` "Confirmado:
agregar Ollama + integrar el marketplace de skills.sh"; dueño WP-V3-04). Ver
`docs/skills.md` para la documentación de producto completa (qué es, cómo
instalar, modelo de seguridad) y `packages/skills/README.md` para el paquete
que este router reutiliza sin duplicar lógica.

Este router NO se monta a sí mismo: `edecan_api.main.create_app()` lo monta
de forma defensiva junto al resto de routers v3 (`ARCHITECTURE.md` §12.a,
`importlib.import_module` + `try/except ImportError`) — este módulo solo
declara `router`.

## Reutiliza `edecan_skills`, no lo duplica

`POST /v1/skills/install` llama a `edecan_skills.installer.install_from_source`
(el MISMO pipeline `parse_source -> fetch_skill -> parse_skill_md` que usa
`edecan_skills.tools.InstalarSkillTool` desde el chat) y luego
`edecan_skills.store.insert_skill` (el mismo upsert-por-slug). `GET`/`PUT`/
`DELETE /v1/skills/{id}` y `POST /v1/skills/search` reutilizan igual
`edecan_skills.store`/`edecan_skills.client` — este archivo es deliberadamente
delgado: solo adapta esas funciones puras al protocolo HTTP (parseo del path/
body, mapeo de errores a códigos de estado, forma exacta de cada response).

## Sin flag de plan, sin segundo gate de confirmación

Las 5 herramientas de `edecan_skills.tools` no declaran `requires_flags`
(disponible en todos los planes, ver su docstring) — este router tampoco
gatea por flag: solo `get_current_user` + `rate_limit`, igual que cualquier
otro recurso "siempre disponible" de la API (p. ej. `/v1/reminders`).

`POST /v1/skills/install` NO exige el gate `confirmation_required` que sí
exige `InstalarSkillTool.dangerous=True` dentro de un turno de chat
(`ARCHITECTURE.md` §10.7): aquí el humano YA hizo clic explícito en un botón
"Instalar" de la UI autenticada — ese clic ES la confirmación humana. El
aviso de seguridad ("son instrucciones de un tercero, revísalas") vive en la
propia pantalla de `/app/skills` (`docs/skills.md`), no en el backend.

## Errores del pipeline de instalación

`edecan_skills.installer` distingue tres fallos posibles con excepciones
propias — este router los traduce a códigos de estado HTTP claros:
`FuenteInvalidaError` -> 400 (incluye los casos anti path-traversal/SSRF de
`parse_source`), `SkillNoEncontradaError` -> 404, `SkillDemasiadoGrandeError`
-> 413 (cap de 200_000 bytes, ver `edecan_skills.installer.fetch_skill`).

## `GET /v1/skills` nunca devuelve `contenido`

Mismo criterio que `edecan_skills.store._LIST_COLUMNS` (deliberadamente sin
`contenido`/`recursos`): la lista es liviana, el contenido completo del
`SKILL.md` solo viaja en `GET /v1/skills/{id}` (o al instalar) — evita mandar
potencialmente ~200KB por cada fila con cada `GET /v1/skills`.

## Seguridad de terceros (WP-V5-04, `edecan_skills.security`)

`trust_tier`/`capabilities` viajan en TODAS las respuestas (`_summary`,
reutilizado por `_detail`) — son livianos. `hallazgos` (resultado de
`security.escanear_inyeccion` sobre `contenido`) solo puede calcularse donde
`contenido` está disponible: `_detail` (`GET /v1/skills/{id}`, y por lo tanto
también la respuesta de `POST /v1/skills/install`), nunca en la lista — mismo
criterio de peso que ya aplicaba a `contenido` en sí.

`PUT /v1/skills/{id}` (activar/desactivar) exige `{"acknowledge": true}` en el
body SOLO para activar (`enabled: true`) una skill que declara alguna
capacidad `dangerous=True` o que tiene hallazgos de una posible inyección de
instrucciones — desactivar nunca necesita `acknowledge`, y activar una skill
sin ninguna de las dos señales tampoco. Sin ese campo, `400` con el `detail`
explicando EXACTAMENTE qué está aceptando el usuario (nunca un `403` genérico:
el usuario necesita saber qué revisar). Se mantiene el verbo `PUT` existente
(no se introduce un método HTTP nuevo) — el body ya era un update parcial
(`{"enabled": bool}`), agregar `acknowledge` no cambia esa forma.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from edecan_skills.client import SkillsIndexClient
from edecan_skills.installer import (
    FuenteInvalidaError,
    SkillDemasiadoGrandeError,
    SkillNoEncontradaError,
    install_from_source,
)
from edecan_skills.security import (
    FUENTES_INDEXADAS,
    HallazgoInyeccion,
    capacidades_peligrosas,
    clasificar_trust_tier,
    escanear_inyeccion,
)
from edecan_skills.store import delete_skill, get_by_id, insert_skill, list_skills, set_enabled
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.config import Settings, get_settings
from edecan_api.deps import CurrentUser, get_current_user, get_tenant_session, rate_limit

router = APIRouter(prefix="/v1/skills", tags=["skills"], dependencies=[Depends(rate_limit)])

_INDEX_URL_DEFECTO = "https://skills.sh"
_TIMEOUT_DEFECTO_SEGUNDOS = 20.0
_K_BUSQUEDA = 10
_FUENTE_DEFECTO = "directo"


def _index_url(settings: Settings) -> str:
    return str(getattr(settings, "SKILLS_INDEX_URL", None) or _INDEX_URL_DEFECTO)


def _timeout(settings: Settings) -> float:
    return float(getattr(settings, "BROWSER_TIMEOUT_SECONDS", _TIMEOUT_DEFECTO_SEGUNDOS))


# ---------------------------------------------------------------------------
# Bodies de entrada
# ---------------------------------------------------------------------------


class SkillSearchIn(BaseModel):
    q: str


class SkillInstallIn(BaseModel):
    source: str
    # "directo" (default) si el usuario pegó `source` a mano; "skills_sh"/"openclaw"/
    # "hermes" si la UI está instalando un resultado de búsqueda de ese índice —
    # decide `trust_tier` (ver `security.clasificar_trust_tier`), mismo criterio que
    # `edecan_skills.tools.InstalarSkillTool`.
    fuente: str = _FUENTE_DEFECTO


class SkillUpdateIn(BaseModel):
    enabled: bool
    # Exigido (ver docstring del módulo) SOLO para activar una skill con capacidades
    # peligrosas o hallazgos de inyección — `False` por defecto: nunca se asume que el
    # usuario ya revisó nada.
    acknowledge: bool = False


# ---------------------------------------------------------------------------
# Forma de salida — `_summary` (sin `contenido`) vs `_detail` (con `contenido`)
# ---------------------------------------------------------------------------


def _hallazgo_dict(hallazgo: HallazgoInyeccion) -> dict[str, Any]:
    return {
        "patron": hallazgo.patron,
        "fragmento": hallazgo.fragmento,
        "posicion": hallazgo.posicion,
    }


def _summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "nombre": row["nombre"],
        "slug": row["slug"],
        "source": row["source"],
        "descripcion": row["descripcion"],
        "version": row["version"],
        "enabled": row["enabled"],
        "trust_tier": row["trust_tier"],
        "capabilities": row.get("capabilities") or [],
        # Subconjunto derivado (nunca se persiste aparte) — evita que `apps/web` tenga que
        # duplicar `security.CAPACIDADES_PELIGROSAS` para pintar los chips en rojo
        # (`docs/skills.md` "Seguridad de skills de terceros").
        "capabilities_peligrosas": capacidades_peligrosas(row.get("capabilities") or []),
        "created_at": row["created_at"],
    }


def _detail(row: dict[str, Any]) -> dict[str, Any]:
    out = _summary(row)
    out["contenido"] = row["contenido"]
    out["recursos"] = row.get("recursos") or {}
    out["updated_at"] = row["updated_at"]
    # Solo calculable acá — necesita `contenido`, que `_summary`/la lista NUNCA traen
    # (ver docstring del módulo).
    out["hallazgos"] = [_hallazgo_dict(h) for h in escanear_inyeccion(row["contenido"])]
    return out


# ---------------------------------------------------------------------------
# GET /v1/skills, GET /v1/skills/{id}
# ---------------------------------------------------------------------------


@router.get("")
async def list_skills_endpoint(
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    filas = await list_skills(session, current_user.tenant_id, current_user.user_id)
    return {"skills": [_summary(f) for f in filas]}


@router.get("/{skill_id}")
async def get_skill_endpoint(
    skill_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    row = await get_by_id(session, current_user.tenant_id, skill_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill no encontrada.")
    return _detail(row)


# ---------------------------------------------------------------------------
# POST /v1/skills/search — descubrimiento best-effort en el índice
# ---------------------------------------------------------------------------


@router.post("/search")
async def search_skills_endpoint(
    body: SkillSearchIn,
    _current_user: CurrentUser = Depends(get_current_user),  # solo exige autenticación
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    consulta = body.q.strip()
    if not consulta:
        return {"resultados": []}

    async with httpx.AsyncClient(timeout=_timeout(settings)) as http:
        cliente = SkillsIndexClient(_index_url(settings), http)
        resultados = await cliente.search(consulta, k=_K_BUSQUEDA)

    return {
        "resultados": [
            {
                "nombre": h.nombre,
                "source": h.source,
                "descripcion": h.descripcion,
                "installs": h.installs,
            }
            for h in resultados
        ]
    }


# ---------------------------------------------------------------------------
# POST /v1/skills/install — pipeline completo (ver docstring del módulo)
# ---------------------------------------------------------------------------


@router.post("/install", status_code=status.HTTP_201_CREATED)
async def install_skill_endpoint(
    body: SkillInstallIn,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    source = body.source.strip()
    if not source:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="source es obligatorio."
        )

    async with httpx.AsyncClient(timeout=_timeout(settings)) as http:
        try:
            instalada = await install_from_source(source, http=http)
        except FuenteInvalidaError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        except SkillNoEncontradaError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except SkillDemasiadoGrandeError as exc:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(exc)
            ) from exc

    fuente = body.fuente.strip().lower() or _FUENTE_DEFECTO
    trust_tier = clasificar_trust_tier(fuente in FUENTES_INDEXADAS)

    fila = await insert_skill(
        session,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        nombre=instalada.nombre,
        source=instalada.source,
        contenido=instalada.contenido,
        descripcion=instalada.descripcion,
        version=instalada.version,
        capabilities=instalada.capabilities,
        trust_tier=trust_tier,
    )
    return _detail(fila)


# ---------------------------------------------------------------------------
# PUT /v1/skills/{id} — activar/desactivar
# ---------------------------------------------------------------------------


def _detalle_acknowledge(peligrosas: list[str], hallazgos: list[HallazgoInyeccion]) -> str:
    partes: list[str] = []
    if peligrosas:
        partes.append(f"capacidades peligrosas ({', '.join(peligrosas)})")
    if hallazgos:
        plural = "es" if len(hallazgos) != 1 else ""
        partes.append(
            f"{len(hallazgos)} hallazgo{plural} de una posible inyección de instrucciones "
            "en su contenido"
        )
    return (
        "Esta skill declara " + " y ".join(partes) + ". Revisa su contenido (GET "
        "/v1/skills/{id}) y reenvía la petición con \"acknowledge\": true confirmando que "
        "aceptas el riesgo antes de activarla."
    )


@router.put("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def update_skill_endpoint(
    skill_id: uuid.UUID,
    body: SkillUpdateIn,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    if body.enabled and not body.acknowledge:
        # Solo se paga esta consulta extra (necesita `contenido` para escanear) en el
        # camino que de verdad la necesita — desactivar, o activar con `acknowledge=true`,
        # van directo a `set_enabled` (ver más abajo), igual que antes de WP-V5-04.
        fila_actual = await get_by_id(session, current_user.tenant_id, skill_id)
        if fila_actual is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Skill no encontrada."
            )
        peligrosas = capacidades_peligrosas(fila_actual.get("capabilities") or [])
        hallazgos = escanear_inyeccion(fila_actual["contenido"])
        if peligrosas or hallazgos:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_detalle_acknowledge(peligrosas, hallazgos),
            )

    fila = await set_enabled(session, current_user.tenant_id, skill_id, body.enabled)
    if fila is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill no encontrada.")


# ---------------------------------------------------------------------------
# DELETE /v1/skills/{id} — desinstalar
# ---------------------------------------------------------------------------


@router.delete("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill_endpoint(
    skill_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    borrada = await delete_skill(session, current_user.tenant_id, skill_id)
    if not borrada:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill no encontrada.")
