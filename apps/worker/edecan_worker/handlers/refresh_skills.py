"""Job `refresh_skills`: re-importa las skills de los catálogos remotos.

Se encola una vez por semana desde el scheduler local
(`edecan_local.worker_loop`, ver `JOBS_PERIODICOS_SEMANALES`) y re-instala en
la tabla `skills` el estado más reciente de cada catálogo, para TODOS los
tenants con skills habilitadas (`SELECT DISTINCT tenant_id, user_id FROM
skills WHERE enabled`). Si no hay destinos, el job termina sin insertar.

Idempotente por construcción: se inserta con `edecan_skills.store.insert_skill`
(upsert atómico por `(tenant_id, slug)` — re-instalar actualiza `contenido`/
`descripcion`/`source`/`updated_at` de la fila existente, jamás duplica),
mismo contrato que usa el instalador interactivo, incluido el escaneo
anti-inyección de `edecan_skills.security` (una skill nueva con hallazgos
queda `enabled=false`).

Fail-open SIEMPRE (el job nunca lanza): un catálogo caído se salta y el resto
sigue; un insert fallido se salta y el resto sigue. Cada fallo queda en `logger`.

Fuentes: la lista `_FUENTES` (nombre, fetch) queda lista para agregar
catálogos sin tocar el resto del handler. Hoy solo AWS
(`awslabs/agent-plugins` vía tarball de codeload); el índice de skills.sh se
omite a propósito: su API solo indexa metadatos de búsqueda (ver
`edecan_skills.client`), no los `SKILL.md` completos — re-importarlo exigiría
bajar cada repo por separado. Agregarlo es una tupla más en `_FUENTES`.

Límites (no descargar gigas): tarball ≤ `_TARBALL_MAX_BYTES` (50 MB), cada
SKILL.md ≤ `_SKILL_MAX_BYTES` (512 KB), timeout HTTP `_TIMEOUT_SEGUNDOS`
(120 s). El tarball NUNCA se extrae a disco: se parsea en memoria.

Job de sistema sin `tenant_id` propio (se encola con `tenant_id=None`): misma
excepción documentada que `send_reminder_scan` a "SIEMPRE filtrar por el
`tenant_id` del job" (ARCHITECTURE.md §2) — es un barrido global deliberado.
"""

from __future__ import annotations

import io
import logging
import re
import tarfile
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import httpx
from edecan_schemas import JobEnvelope
from edecan_skills.store import insert_skill
from sqlalchemy import text

from edecan_worker.deps import Deps

logger = logging.getLogger(__name__)

# --- catálogo AWS (awslabs/agent-plugins) ------------------------------------

TARBALL_AWS = "https://codeload.github.com/awslabs/agent-plugins/tar.gz/HEAD"
FUENTE_AWS = "https://github.com/awslabs/agent-plugins"

# --- límites -----------------------------------------------------------------

_TIMEOUT_SEGUNDOS = 120.0
_TARBALL_MAX_BYTES = 50 * 1024 * 1024
_SKILL_MAX_BYTES = 512 * 1024

_RE_NOMBRE = re.compile(r"^name:\s*(.+)$", re.M)
_RE_DESCRIPCION = re.compile(r"^description:\s*(.+)$", re.M)

# Skill extraída de un catálogo: `nombre`/`descripcion`/`contenido` del SKILL.md
# y `source` (URL del catálogo) sellado por la función de fetch.
SkillRemota = dict[str, str]
FetchCatalogo = Callable[[httpx.AsyncClient], Awaitable[list[SkillRemota]]]


async def _descargar_con_tope(http: httpx.AsyncClient, url: str) -> bytes:
    """Descarga `url` en memoria con tope de `_TARBALL_MAX_BYTES`."""
    partes: list[bytes] = []
    total = 0
    async with http.stream("GET", url) as respuesta:
        respuesta.raise_for_status()
        async for trozo in respuesta.aiter_bytes():
            total += len(trozo)
            if total > _TARBALL_MAX_BYTES:
                raise RuntimeError(
                    f"descarga de {url} excede el tope de {_TARBALL_MAX_BYTES} bytes"
                )
            partes.append(trozo)
    return b"".join(partes)


def _frontmatter(texto: str) -> tuple[str, str]:
    """`name`/`description` del frontmatter de un SKILL.md (mismo criterio que
    el importador de referencia del VPS)."""
    nombre = ""
    descripcion = ""
    m = _RE_NOMBRE.search(texto)
    if m:
        nombre = m.group(1).strip().strip("\"'")
    m = _RE_DESCRIPCION.search(texto)
    if m:
        descripcion = m.group(1).strip().strip("\"'")
    return nombre, descripcion


def _parsear_tarball(crudo: bytes) -> list[SkillRemota]:
    """Parse en memoria de `plugins/*/skills/*/SKILL.md` del tarball de AWS."""
    skills: list[SkillRemota] = []
    with tarfile.open(fileobj=io.BytesIO(crudo), mode="r:gz") as tf:
        for miembro in tf.getmembers():
            if not miembro.isfile() or not miembro.name.endswith("SKILL.md"):
                continue
            partes = miembro.name.split("/")
            if len(partes) < 4 or partes[1] != "plugins":
                continue
            archivo = tf.extractfile(miembro)
            if archivo is None:
                continue
            crudo_skill = archivo.read(_SKILL_MAX_BYTES + 1)
            if len(crudo_skill) > _SKILL_MAX_BYTES:
                logger.warning(
                    "refresh_skills: %s excede el tope por skill; se salta", miembro.name
                )
                continue
            texto = crudo_skill.decode("utf-8", errors="replace")
            nombre, descripcion = _frontmatter(texto)
            if not nombre:
                nombre = partes[-2]
            skills.append({"nombre": nombre, "descripcion": descripcion, "contenido": texto})
    return skills


async def _fetch_catalogo_aws(http: httpx.AsyncClient) -> list[SkillRemota]:
    skills = _parsear_tarball(await _descargar_con_tope(http, TARBALL_AWS))
    for skill in skills:
        skill["source"] = FUENTE_AWS
    return skills


# Lista de catálogos (nombre, fetch): estructura lista para agregar fuentes
# nuevas sin tocar el resto del handler. Ver docstring del módulo sobre por
# qué skills.sh se omite hoy.
_FUENTES: tuple[tuple[str, FetchCatalogo], ...] = (("aws-agent-plugins", _fetch_catalogo_aws),)


async def _listar_tenants_con_skills(session: Any) -> list[tuple[UUID, UUID]]:
    """`(tenant_id, user_id)` de TODOS los tenants con skills habilitadas."""
    result = await session.execute(
        text("SELECT DISTINCT tenant_id, user_id FROM skills WHERE enabled = true")
    )
    destinos: list[tuple[UUID, UUID]] = []
    for row in result.mappings().all():
        try:
            destinos.append((UUID(str(row["tenant_id"])), UUID(str(row["user_id"]))))
        except (TypeError, ValueError):
            logger.warning("refresh_skills: fila con ids ilegibles en skills; se salta")
    return destinos


async def _refrescar(deps: Deps) -> None:
    async with httpx.AsyncClient(timeout=_TIMEOUT_SEGUNDOS, follow_redirects=True) as http:
        lotes: list[list[SkillRemota]] = []
        for nombre_fuente, fetch in _FUENTES:
            try:
                lote = await fetch(http)
                lotes.append(lote)
                logger.info(
                    "refresh_skills: fuente %r -> %d skill(s)", nombre_fuente, len(lote)
                )
            except Exception:
                logger.exception(
                    "refresh_skills: fuente %r falló; se salta y el resto sigue (fail-open)",
                    nombre_fuente,
                )

    skills = [skill for lote in lotes for skill in lote]
    if not skills:
        logger.info("refresh_skills: nada que refrescar; job completo")
        return

    destinos: list[tuple[UUID, UUID]] = []
    try:
        async with deps.session_factory(None) as session:
            destinos = await _listar_tenants_con_skills(session)
    except Exception:
        logger.exception("refresh_skills: no se pudo listar tenants con skills")

    if not destinos:
        logger.info("refresh_skills: sin tenants con skills; no hay destinos")
        return

    insertadas = 0
    fallos = 0
    for tenant_id, user_id in destinos:
        for skill in skills:
            # Una transacción por insert (no un lote): `get_session` ya hace
            # commit al salir del bloque, y un insert fallido deja la sesión
            # intacta para el siguiente (jamás PendingRollbackError a mitad
            # de lote). ~50 skills × tenants es poco volumen para un job
            # semanal; la robustez vale más que el ahorro de round-trips.
            try:
                async with deps.session_factory(None) as session:
                    await insert_skill(
                        session,
                        tenant_id=tenant_id,
                        user_id=user_id,
                        nombre=skill["nombre"],
                        source=skill.get("source") or "",
                        contenido=skill["contenido"],
                        descripcion=skill.get("descripcion") or "",
                    )
                insertadas += 1
            except Exception:
                fallos += 1
                logger.exception(
                    "refresh_skills: fallo insertando %r (tenant=%s); se sigue",
                    skill.get("nombre"),
                    tenant_id,
                )

    logger.info("refresh_skills: listo — insertadas=%d fallos=%d", insertadas, fallos)


async def handle(env: JobEnvelope, deps: Deps) -> None:
    """Re-importa los catálogos para todos los tenants con skills. NUNCA lanza."""
    try:
        await _refrescar(deps)
    except Exception:
        logger.exception("refresh_skills: fallo global inesperado (fail-open)")
