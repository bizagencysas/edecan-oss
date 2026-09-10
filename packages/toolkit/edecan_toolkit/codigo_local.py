"""Acceso local total al propio repo (`ARCHITECTURE.md` §10.14, 2026-07-09).

`AccederCodigoLocalTool` deja que Edecán lea/escriba archivos, corra
comandos y haga commits LOCALES directamente sobre el clon del repo en la
máquina donde corre esta instancia — pensado para uso de DESARROLLO (el
dueño trabajando en su propio producto), NUNCA para el hosted multi-tenant
compartido: a diferencia de `usar_computadora` (que pasa por el companion
emparejado, en LA COMPUTADORA DEL USUARIO), esta tool opera por filesystem/
subprocess DIRECTO sobre el proceso que corre el backend — en un servidor
hosted compartido por varios tenants eso significaría que cualquier tenant
con esta tool podría leer/escribir el filesystem del SERVIDOR, no el suyo
propio. Por eso el gate es doble y explícito, ambos en `ctx.settings`
(nunca en flags de plan, que hoy son iguales para todos los tenants — ver
`edecan_schemas.plans`):

1. `EDECAN_LOCAL_MODE` debe ser `True` (mismo flag que ya usa `Polly` en
   `routers/voice.py`/`routers/credentials.py` para el mismo motivo: solo
   tiene sentido en una instancia de un único dueño).
2. `EDECAN_LOCAL_REPO_PATH` debe estar configurado a un directorio real —
   el dueño lo fija a mano (variable de entorno del proceso; no hay UI de
   "pegar y validar" para esto, es una ruta de filesystem, no un secreto).

Edecán edita SU PROPIO clon local — nunca hace `git push` ni toca ningún
remoto por su cuenta: `git_commit` deja el commit LOCAL nada más y exige una
lista explícita de rutas; nunca hace ``git add --all`` ni captura cambios
previos del usuario por accidente. Empujar a
GitHub/un remoto compartido es una decisión que solo un humano toma
explícitamente desde su propia terminal. `ejecutar_comando` SÍ podría, en
teoría, correr `git push` igual que cualquier otro comando (no hay un
allowlist de comandos, mismo criterio que `usar_computadora.run_command`:
la defensa real es que `dangerous = True` exige confirmación humana ANTES de
cada ejecución, mostrando el comando exacto en `ConfirmationCard.tsx` —
código no puede distinguir "un push legítimo que el dueño pidió" de "uno
que no", así que la decisión queda, a propósito, del lado humano).

Todas las rutas se resuelven DENTRO de `EDECAN_LOCAL_REPO_PATH` (`../../etc/
passwd` o cualquier ruta absoluta fuera de la raíz se rechaza) — jaula de
paths, no de comandos.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

from edecan_core import Tool, ToolContext, ToolResult
from sqlalchemy import text

_TIMEOUT_SEGUNDOS = 60.0
_LIMITE_BYTES_LECTURA = 200_000  # ~200KB: suficiente para casi cualquier archivo de código
_LIMITE_RESULTADOS_BUSQUEDA = 100
_LIMITE_SALIDA_COMANDO = 8_000  # caracteres, para no inundar el contexto del modelo

_SIN_CONFIGURAR = (
    "El acceso local al repo no está configurado en esta instancia -- necesita "
    "EDECAN_LOCAL_MODE=true y EDECAN_LOCAL_REPO_PATH apuntando al clon local del repo."
)
_SOLO_DUENO = (
    "El acceso al código local pertenece al dueño de esta instalación de Edecán. "
    "La cuenta actual no puede usarlo."
)

_MAC_HOME_RE = re.compile(r"^(/Users/[^/]+)(?:/|$)")
_MAC_REPO_SUFIJO = "edecan"
_MAC_REPO_EJEMPLO = "/Users/example/edecan"


def _home_mac_desde_ruta(ruta: str) -> str | None:
    match = _MAC_HOME_RE.match(ruta.replace("\\", "/"))
    return match.group(1) if match else None


def _ruta_mac_fuera_repo_para_companion(ruta_pedida: str) -> tuple[str, str] | None:
    """Raíz y path relativo para el companion: el path absoluto se descarta.

    `edecan_companion.actions._resolve_in_sandbox` trata todo path como
    relativo a `workspace_root` (le quita el `/` inicial). Hay que mandar
    el home de la Mac como raíz y `Documents/...` relativo; si se manda el
    repo como raíz + path absoluto, el companion lo anida debajo del repo
    y no lee el archivo.
    """
    normalizada = str(ruta_pedida or "").strip().replace("\\", "/")
    home = _home_mac_desde_ruta(normalizada)
    if home is None:
        return None
    if normalizada == home:
        return home, "."
    if normalizada.startswith(home + "/"):
        return home, normalizada[len(home) + 1 :] or "."
    return home, normalizada.lstrip("/") or "."


def _remap_ruta_mac_a_local(ruta_pedida: str) -> tuple[str | None, bool]:
    """Mapea rutas del repo en la Mac al espejo local del box.

    Returns:
        (ruta_relativa_local, es_mac_fuera_del_repo)
        - Mapeable al espejo: (relativa, False)
        - /Users/<cuenta>/... pero fuera del repo Mac: (None, True)
        - No es ruta Mac: (None, False)
    """
    ruta = str(ruta_pedida or "").strip()
    if not ruta:
        return None, False

    normalizada = ruta.replace("\\", "/")
    if _MAC_REPO_SUFIJO in normalizada:
        idx = normalizada.index(_MAC_REPO_SUFIJO)
        relativa = normalizada[idx + len(_MAC_REPO_SUFIJO) :].lstrip("/") or "."
        return relativa, False

    if _home_mac_desde_ruta(normalizada):
        return None, True
    return None, False


def _raiz(ctx: ToolContext) -> Path | None:
    if not getattr(ctx.settings, "EDECAN_LOCAL_MODE", False):
        return None
    bruta = getattr(ctx.settings, "EDECAN_LOCAL_REPO_PATH", None)
    if not bruta:
        return None
    raiz = Path(str(bruta)).expanduser().resolve()
    if not raiz.is_dir():
        return None
    return raiz


async def _es_dueno_de_instalacion(ctx: ToolContext) -> bool:
    raw_owner = getattr(ctx.settings, "LOCAL_OWNER_USER_ID", None) or os.environ.get(
        "LOCAL_OWNER_USER_ID"
    )
    if raw_owner:
        try:
            return uuid.UUID(str(raw_owner).strip()) == ctx.user_id
        except (TypeError, ValueError):
            return False

    session = getattr(ctx, "session", None)
    if session is None:
        return False
    try:
        result = await session.execute(
            text(
                """
                SELECT owner_user_id AS user_id, owner_tenant_id AS tenant_id
                FROM local_installation
                WHERE installation_key = 'local'
                """
            )
        )
        owner = result.mappings().first()
        if owner is None:
            result = await session.execute(
                text(
                    """
                    SELECT m.user_id, m.tenant_id
                    FROM memberships m
                    JOIN tenants t ON t.id = m.tenant_id
                    WHERE m.role = 'owner' AND t.status = 'active'
                    ORDER BY m.created_at ASC, m.id ASC
                    LIMIT 1
                    """
                )
            )
            owner = result.mappings().first()
    except Exception:  # noqa: BLE001 - authorization lookup must fail closed
        return False

    # No authenticated production request exists before the first owner is
    # persisted; this branch only preserves first-run/isolated tool bootstrap.
    if owner is None:
        return True
    return owner.get("user_id") == ctx.user_id and owner.get("tenant_id") == ctx.tenant_id


def _resolver_dentro_de_raiz(raiz: Path, ruta_relativa: str) -> Path | None:
    """`None` si `ruta_relativa` (tal cual la pidió el modelo) escapa de `raiz`
    -- vía `..`, una ruta absoluta a otro lado, o un symlink que apunte afuera
    (`.resolve()` sigue symlinks antes de comprobar)."""
    candidata = (raiz / ruta_relativa).resolve()
    try:
        candidata.relative_to(raiz)
    except ValueError:
        return None
    return candidata


async def _correr(
    *argv: str, cwd: Path, timeout: float = _TIMEOUT_SEGUNDOS
) -> tuple[int, str]:
    """Ejecuta un proceso sin reinterpretar sus argumentos en un shell."""
    if not argv:
        raise ValueError("_correr requiere al menos un argumento")
    try:
        proceso = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        return 127, f"No se pudo iniciar {argv[0]!r}: {exc}"
    try:
        salida_bytes, _ = await asyncio.wait_for(proceso.communicate(), timeout=timeout)
    except TimeoutError:
        proceso.kill()
        await proceso.wait()
        return -1, f"(timeout tras {timeout:.0f}s, proceso terminado)"
    salida = salida_bytes.decode("utf-8", errors="replace")
    if len(salida) > _LIMITE_SALIDA_COMANDO:
        salida = salida[:_LIMITE_SALIDA_COMANDO] + "\n... (salida truncada)"
    return proceso.returncode or 0, salida


class AccederCodigoLocalTool(Tool):
    # Subprocess interno con timeout de 60s: el deadline de la tool debe
    # quedar por ENCIMA para que el kill interno corra antes (R-2).
    timeout_seconds = 130.0
    name = "acceder_codigo_local"
    description = (
        "Lee, escribe, busca y ejecuta git/shell sobre el repo del box (listar, "
        "leer_archivo, git_status, git_diff, git_commit). Úsala cuando el dueño "
        "pida arreglar login/código, explorar el repo («mira qué hay»), revisar "
        "cambios o editar archivos — sin pedirle que nombre esta herramienta. "
        "Solo en instancias con EDECAN_LOCAL_MODE + EDECAN_LOCAL_REPO_PATH; "
        "requiere confirmación porque actúa de verdad sobre el código."
    )
    category = "code"
    risk_level = "high"
    dangerous = True
    input_schema = {
        "type": "object",
        "properties": {
            "accion": {
                "type": "string",
                "enum": [
                    "leer_archivo",
                    "escribir_archivo",
                    "listar_directorio",
                    "buscar",
                    "ejecutar_comando",
                    "git_status",
                    "git_diff",
                    "git_commit",
                ],
            },
            "ruta": {
                "type": "string",
                "description": (
                    "Ruta relativa a la raíz del repo (para leer_archivo/escribir_archivo/"
                    "listar_directorio/buscar). '.' para la raíz misma."
                ),
            },
            "contenido": {
                "type": "string",
                "description": "Contenido nuevo del archivo (solo para escribir_archivo).",
            },
            "patron": {
                "type": "string",
                "description": "Texto o regex a buscar en el contenido de los archivos (solo "
                "para buscar).",
            },
            "comando": {
                "type": "string",
                "description": "Comando de shell a ejecutar en la raíz del repo (solo para "
                "ejecutar_comando).",
            },
            "mensaje": {
                "type": "string",
                "description": "Mensaje del commit (solo para git_commit).",
            },
            "rutas": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Rutas exactas a preparar en git_commit. Obligatorio: nunca se hace "
                    "git add --all ni se incorporan otros cambios del usuario."
                ),
            },
        },
        "required": ["accion"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        raiz = _raiz(ctx)
        if raiz is None:
            return ToolResult(content=_SIN_CONFIGURAR)
        if not await _es_dueno_de_instalacion(ctx):
            return ToolResult(
                content=_SOLO_DUENO,
                data={"authorized": False},
                is_error=True,
            )

        accion = str(args.get("accion", "")).strip()
        # Modo SOLO LECTURA (perfiles de misión read_only): las acciones que
        # mutan el repo se rechazan con error honesto — el guardrail de
        # dangerous queda intacto y el subagente puede leer código real.
        if ctx.extras.get("codigo_solo_lectura") and accion in (
            "escribir_archivo",
            "ejecutar_comando",
            "git_commit",
        ):
            return ToolResult(
                content=(
                    f"Acción '{accion}' bloqueada: este agente tiene el repo "
                    "en SOLO LECTURA (solo puede leer, listar, buscar y ver git)."
                ),
                data={"read_only": True},
                is_error=True,
            )
        # Rutas de la MAC del dueño: si apuntan al repo Edecán, el espejo del
        # box (/opt/edecan/app) sirve aunque el companion esté offline. Solo
        # rutas Mac fuera de ese repo van por el companion (solo lectura).
        ruta_pedida = str(args.get("ruta") or args.get("path") or "")
        remapeada, mac_fuera_repo = _remap_ruta_mac_a_local(ruta_pedida)
        if remapeada is not None:
            args = dict(args)
            args["ruta"] = remapeada
        elif mac_fuera_repo or bool(_home_mac_desde_ruta(ruta_pedida)):
            return await self._via_companion_mac(ctx, accion, args, raiz)

        handler = {
            "leer_archivo": self._leer_archivo,
            "escribir_archivo": self._escribir_archivo,
            "listar_directorio": self._listar_directorio,
            "buscar": self._buscar,
            "ejecutar_comando": self._ejecutar_comando,
            "git_status": self._git_status,
            "git_diff": self._git_diff,
            "git_commit": self._git_commit,
        }.get(accion)
        if handler is None:
            return ToolResult(content=f"Acción desconocida: {accion!r}.")
        return await handler(raiz, args)

    async def _via_companion_mac(
        self, ctx: ToolContext, accion: str, args: dict[str, Any], raiz: Path
    ) -> ToolResult:
        """Lee archivos/listados/búsquedas de la MAC del dueño por el
        companion (solo lectura). Rutas del repo Mac ya se remapean al box."""
        companion = ctx.extras.get("companion")
        if companion is None:
            return ToolResult(
                content=(
                    "Esa ruta está en tu Mac (fuera del espejo del box) y ahora "
                    "mismo no hay companion conectado. Para el repo Edecán usa "
                    f"rutas relativas al espejo local ({raiz}), por ejemplo "
                    "'packages/core/edecan_core/bot_persona.py' en lugar de "
                    f"'{_MAC_REPO_EJEMPLO}/packages/core/...'. Enciende la Mac si "
                    "necesitas archivos que no están en /opt/edecan/app."
                ),
                data={"mac_offline": True},
                is_error=True,
            )
        if accion in ("escribir_archivo", "ejecutar_comando", "git_commit"):
            return ToolResult(
                content=(
                    "Escribir o ejecutar en la Mac desde una misión no está "
                    "permitido (solo lectura). Para cambiar algo en tu Mac, "
                    "pídemelo en el chat y lo hago con tu aprobación."
                ),
                is_error=True,
            )
        mapeo = {
            "leer_archivo": "read_file",
            "listar_directorio": "list_tree",
            "buscar": "search_files",
        }
        accion_mac = mapeo.get(accion)
        if accion_mac is None:
            return ToolResult(
                content=(
                    f"La acción '{accion}' sobre una ruta de la Mac no está "
                    "soportada por el puente; usa leer_archivo, "
                    "listar_directorio o buscar."
                ),
                is_error=True,
            )
        # La MAC completa (home) como workspace_root: el companion confina las
        # lecturas a esa raíz y el `path` viaja RELATIVO a ella.
        ruta_pedida = str(args.get("ruta") or args.get("path") or "")
        par = _ruta_mac_fuera_repo_para_companion(ruta_pedida)
        if par is None:
            return ToolResult(
                content=(
                    "Esa ruta no es una ruta de Mac reconocible. Usa una ruta "
                    "bajo el home de la Mac o una ruta relativa al espejo "
                    f"del box ({raiz})."
                ),
                is_error=True,
            )
        raiz_mac, relativa = par
        parametros: dict[str, Any] = {
            "workspace_root": raiz_mac,
            "path": relativa,
        }
        if accion == "buscar":
            parametros["query"] = str(args.get("patron") or args.get("consulta") or "")
        try:
            resultado = await companion(accion_mac, parametros)
        except Exception as exc:
            return ToolResult(
                content=(
                    f"La Mac no respondió a {accion_mac} ({exc}). "
                    "Reintenta o usa el espejo del box: /opt/edecan/app."
                ),
                is_error=True,
            )
        contenido = (
            resultado.get("contenido")
            or resultado.get("content")
            or resultado.get("text")
            or json.dumps(resultado, default=str, ensure_ascii=False)
        )
        return ToolResult(content=str(contenido)[:20000])

    async def _leer_archivo(self, raiz: Path, args: dict[str, Any]) -> ToolResult:
        ruta = _resolver_dentro_de_raiz(raiz, str(args.get("ruta", "")))
        if ruta is None:
            return ToolResult(content="Esa ruta queda fuera del repo local -- no puedo leerla.")
        if not ruta.is_file():
            return ToolResult(content=f"'{args.get('ruta')}' no es un archivo.")
        datos = ruta.read_bytes()
        truncado = len(datos) > _LIMITE_BYTES_LECTURA
        texto = datos[:_LIMITE_BYTES_LECTURA].decode("utf-8", errors="replace")
        if truncado:
            texto += "\n... (archivo truncado, superó el límite de lectura)"
        return ToolResult(content=texto, data={"ruta": str(args.get("ruta")), "truncado": truncado})

    async def _escribir_archivo(self, raiz: Path, args: dict[str, Any]) -> ToolResult:
        ruta = _resolver_dentro_de_raiz(raiz, str(args.get("ruta", "")))
        if ruta is None:
            return ToolResult(content="Esa ruta queda fuera del repo local -- no puedo escribirla.")
        contenido = args.get("contenido")
        if contenido is None:
            return ToolResult(content="Falta 'contenido' para escribir_archivo.")
        ruta.parent.mkdir(parents=True, exist_ok=True)
        # newline="" evita que Windows traduzca cada "\n" a "\r\n" al escribir en
        # modo texto -- sin esto, cualquier archivo tocado en Windows terminaría
        # con fin de línea distinto al resto del repo (que usa LF), ensuciando
        # el diff de Git con cambios de línea completa en vez del cambio real.
        ruta.write_text(str(contenido), encoding="utf-8", newline="")
        return ToolResult(
            content=f"Escribí {args.get('ruta')} ({len(str(contenido))} caracteres).",
            data={"ruta": str(args.get("ruta"))},
        )

    async def _listar_directorio(self, raiz: Path, args: dict[str, Any]) -> ToolResult:
        ruta = _resolver_dentro_de_raiz(raiz, str(args.get("ruta", ".") or "."))
        if ruta is None:
            return ToolResult(content="Esa ruta queda fuera del repo local -- no puedo listarla.")
        if not ruta.is_dir():
            return ToolResult(content=f"'{args.get('ruta')}' no es un directorio.")
        entradas = sorted(
            f"{p.name}/" if p.is_dir() else p.name
            for p in ruta.iterdir()
            if not p.name.startswith(".git")
        )
        return ToolResult(content="\n".join(entradas) or "(vacío)", data={"entradas": entradas})

    async def _buscar(self, raiz: Path, args: dict[str, Any]) -> ToolResult:
        patron = str(args.get("patron", "")).strip()
        if not patron:
            return ToolResult(content="Falta 'patron' para buscar.")
        base = _resolver_dentro_de_raiz(raiz, str(args.get("ruta", ".") or "."))
        if base is None:
            return ToolResult(content="Esa ruta queda fuera del repo local -- no puedo buscar ahí.")

        try:
            expresion = re.compile(patron)
        except re.error as exc:
            return ToolResult(content=f"'{patron}' no es un patrón válido: {exc}")

        coincidencias: list[str] = []
        for archivo in base.rglob("*"):
            if len(coincidencias) >= _LIMITE_RESULTADOS_BUSQUEDA:
                break
            if not archivo.is_file() or ".git" in archivo.parts:
                continue
            try:
                texto = archivo.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for numero, linea in enumerate(texto.splitlines(), start=1):
                if expresion.search(linea):
                    relativa = archivo.relative_to(raiz)
                    coincidencias.append(f"{relativa}:{numero}: {linea.strip()[:200]}")
                    if len(coincidencias) >= _LIMITE_RESULTADOS_BUSQUEDA:
                        break

        if not coincidencias:
            return ToolResult(content=f"Sin coincidencias para '{patron}'.")
        return ToolResult(
            content="\n".join(coincidencias), data={"coincidencias": len(coincidencias)}
        )

    async def _ejecutar_comando(self, raiz: Path, args: dict[str, Any]) -> ToolResult:
        comando = str(args.get("comando", "")).strip()
        if not comando:
            return ToolResult(content="Falta 'comando' para ejecutar_comando.")
        # Esta acción representa explícitamente un comando de shell arbitrario
        # confirmado por el usuario. El ejecutable y sus flags sí son argv
        # fijos: `comando` ocupa un único argumento y nunca se concatena con
        # operaciones internas como Git.
        argv_shell = (
            ("cmd.exe", "/d", "/s", "/c", comando)
            if sys.platform == "win32"
            else ("/bin/sh", "-c", comando)
        )
        codigo, salida = await _correr(*argv_shell, cwd=raiz)
        prefijo = "OK" if codigo == 0 else f"código de salida {codigo}"
        return ToolResult(
            content=f"[{prefijo}]\n{salida}", data={"comando": comando, "codigo": codigo}
        )

    async def _git_status(self, raiz: Path, _args: dict[str, Any]) -> ToolResult:
        _codigo, salida = await _correr("git", "status", "--short", "--branch", cwd=raiz)
        return ToolResult(content=salida or "(sin cambios)")

    async def _git_diff(self, raiz: Path, _args: dict[str, Any]) -> ToolResult:
        _codigo, salida = await _correr("git", "diff", cwd=raiz)
        return ToolResult(content=salida or "(sin diferencias)")

    async def _git_commit(self, raiz: Path, args: dict[str, Any]) -> ToolResult:
        mensaje = str(args.get("mensaje", "")).strip()
        if not mensaje:
            return ToolResult(content="Falta 'mensaje' para git_commit.")
        rutas_raw = args.get("rutas")
        if (
            not isinstance(rutas_raw, list)
            or not rutas_raw
            or not all(isinstance(ruta, str) and ruta.strip() for ruta in rutas_raw)
        ):
            return ToolResult(
                content=(
                    "Falta 'rutas': git_commit exige una lista explícita y nunca prepara "
                    "todos los cambios del repositorio."
                )
            )
        rutas: list[str] = []
        for raw in rutas_raw:
            ruta = str(raw).strip()
            resuelta = _resolver_dentro_de_raiz(raiz, ruta)
            if resuelta is None or ".git" in resuelta.relative_to(raiz).parts:
                return ToolResult(content=f"Ruta inválida o fuera del repo: {ruta!r}.")
            rutas.append(ruta)

        codigo_staged, staged_previo = await _correr(
            "git", "diff", "--cached", "--name-only", cwd=raiz
        )
        if codigo_staged != 0:
            return ToolResult(content=f"No se pudo revisar el índice de Git:\n{staged_previo}")
        if staged_previo.strip():
            return ToolResult(
                content=(
                    "Ya hay cambios preparados por el usuario; no crearé un commit que los "
                    f"mezcle. Índice actual:\n{staged_previo}"
                )
            )

        codigo_add, salida_add = await _correr("git", "add", "--", *rutas, cwd=raiz)
        if codigo_add != 0:
            return ToolResult(content=f"No se pudieron preparar los cambios:\n{salida_add}")
        codigo, salida = await _correr("git", "commit", "--message", mensaje, cwd=raiz)
        if codigo != 0:
            # Solo des-prepara las rutas que esta llamada acaba de agregar; no
            # toca su contenido en el worktree.
            await _correr("git", "reset", "--", *rutas, cwd=raiz)
            return ToolResult(content=f"No se pudo hacer el commit:\n{salida}")
        return ToolResult(
            content=f"Commit local hecho (no se hizo push a ningún remoto):\n{salida}",
            data={"mensaje": mensaje, "rutas": rutas},
        )
