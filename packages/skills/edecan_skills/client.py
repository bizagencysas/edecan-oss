"""Cliente HTTP best-effort contra el índice de skills.sh (`ARCHITECTURE.md` §10.7,
`DIRECCION_ACTUAL.md` "Confirmado: agregar Ollama + integrar el marketplace de skills.sh").

El índice (búsqueda por palabra clave) es SOLO un descubrimiento conveniente — el
mecanismo real de instalación (`edecan_skills.installer.fetch_skill`, directo contra
`raw.githubusercontent.com` por `owner/repo`) nunca depende de que skills.sh esté arriba
ni de que su API tenga la forma esperada. Por eso `search()` nunca lanza: ante CUALQUIER
fallo (red, status inesperado, JSON inválido/con forma inesperada) devuelve `[]` con
`logger.warning` y dice ahí mismo por qué — instalar por `owner/repo` directo SIEMPRE
sigue funcionando.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_K_DEFECTO = 10

# Endpoints candidatos, en orden: se prueba `/api/search` primero y, SOLO si esa ruta
# responde 404 (no existe), se reintenta con `/api/skills` — cualquier otro fallo (red,
# 5xx, JSON inválido) corta la búsqueda ahí mismo, sin seguir probando (ver docstring del
# módulo: es best-effort, no vale la pena una segunda llamada de red ante un fallo que no
# sea "esta ruta en particular no existe").
_ENDPOINTS: tuple[str, ...] = ("/api/search", "/api/skills")


@dataclass(frozen=True)
class SkillHit:
    """Un resultado de búsqueda del índice de skills.sh, ya normalizado."""

    nombre: str
    source: str  # "owner/repo" (o lo que el índice haya reportado como fuente/repo)
    descripcion: str
    installs: int | None = None


class SkillsIndexClient:
    """Cliente del índice de skills.sh (o cualquier índice compatible en `base_url`)."""

    def __init__(self, base_url: str, http: httpx.AsyncClient) -> None:
        self._base_url = (base_url or "").rstrip("/")
        self._http = http

    async def search(self, q: str, k: int = _K_DEFECTO) -> list[SkillHit]:
        """Busca `q` en el índice; `[]` ante cualquier fallo (ver docstring del módulo)."""
        consulta = (q or "").strip()
        if not consulta:
            return []

        for i, path in enumerate(_ENDPOINTS):
            url = f"{self._base_url}{path}"
            try:
                respuesta = await self._http.get(url, params={"q": consulta})
            except httpx.HTTPError as exc:
                logger.warning(
                    "Fallo de red buscando %r en el índice de skills (%s): %s", consulta, url, exc
                )
                return []

            if respuesta.status_code == 404:
                es_ultimo = i == len(_ENDPOINTS) - 1
                if es_ultimo:
                    logger.warning(
                        "Índice de skills: ningún endpoint conocido respondió para %r "
                        "(probados: %s)",
                        consulta,
                        ", ".join(_ENDPOINTS),
                    )
                    return []
                continue  # 404: reintenta con el siguiente endpoint candidato

            if respuesta.status_code != 200:
                logger.warning(
                    "Índice de skills respondió %d (inesperado) buscando %r en %s",
                    respuesta.status_code,
                    consulta,
                    url,
                )
                return []

            try:
                cuerpo = respuesta.json()
            except ValueError:
                logger.warning(
                    "Índice de skills devolvió JSON inválido buscando %r en %s", consulta, url
                )
                return []

            return _parsear_resultados(cuerpo, k)

        return []  # defensivo: inalcanzable con _ENDPOINTS no vacío, ver el for de arriba


def _parsear_resultados(cuerpo: Any, k: int) -> list[SkillHit]:
    """Parse TOLERANTE del cuerpo de la respuesta: acepta `{"skills": [...]}` o una lista
    JSON directa; cada item se lee con `.get` defensivo (nunca asume que una clave existe).
    Items que no son `dict`, o sin nombre/fuente utilizables, se descartan en silencio —
    una fila rara en la respuesta del índice no debe tumbar el resto de resultados buenos.
    """
    items = cuerpo.get("skills") if isinstance(cuerpo, dict) else cuerpo
    if not isinstance(items, list):
        return []

    limite = max(1, k)
    resultados: list[SkillHit] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        # skills.sh separa la ubicación instalable en dos campos: `source`
        # identifica el repositorio y `id` incluye además la carpeta exacta
        # de la skill (p. ej. source="anthropics/skills",
        # id="anthropics/skills/pdf"). Usar solo `source` hacía que el
        # instalador buscara SKILL.md en la raíz equivocada de prácticamente
        # todos los monorepos del índice.
        source = str(item.get("id") or item.get("source") or item.get("repo") or "").strip()
        nombre = str(item.get("name") or "").strip() or source
        if not nombre or not source:
            continue
        installs_raw = item.get("installs")
        es_entero = isinstance(installs_raw, int) and not isinstance(installs_raw, bool)
        installs = installs_raw if es_entero else None
        resultados.append(
            SkillHit(
                nombre=nombre,
                source=source,
                descripcion=str(item.get("description") or "").strip(),
                installs=installs,
            )
        )
        if len(resultados) >= limite:
            break
    return resultados
