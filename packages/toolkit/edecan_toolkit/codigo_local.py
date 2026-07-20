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
remoto por su cuenta: `git_commit` deja el commit LOCAL nada más. Empujar a
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
import re
import sys
from pathlib import Path
from typing import Any

from edecan_core import Tool, ToolContext, ToolResult

_TIMEOUT_SEGUNDOS = 60.0
_LIMITE_BYTES_LECTURA = 200_000  # ~200KB: suficiente para casi cualquier archivo de código
_LIMITE_RESULTADOS_BUSQUEDA = 100
_LIMITE_SALIDA_COMANDO = 8_000  # caracteres, para no inundar el contexto del modelo

_SIN_CONFIGURAR = (
    "El acceso local al repo no está configurado en esta instancia -- necesita "
    "EDECAN_LOCAL_MODE=true y EDECAN_LOCAL_REPO_PATH apuntando al clon local del repo."
)


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
    name = "acceder_codigo_local"
    description = (
        "Lee/escribe archivos, corre comandos y hace commits LOCALES (nunca push) directo "
        "sobre el clon del repo en esta máquina -- solo disponible en instancias de "
        "desarrollo configuradas explícitamente (EDECAN_LOCAL_MODE + EDECAN_LOCAL_REPO_PATH), "
        "nunca en el hosted compartido. Requiere confirmación porque actúa de verdad sobre "
        "el código fuente."
    )
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
        },
        "required": ["accion"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        raiz = _raiz(ctx)
        if raiz is None:
            return ToolResult(content=_SIN_CONFIGURAR)

        accion = str(args.get("accion", "")).strip()
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
        ruta.write_text(str(contenido), encoding="utf-8")
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
        codigo_add, salida_add = await _correr("git", "add", "--all", cwd=raiz)
        if codigo_add != 0:
            return ToolResult(content=f"No se pudieron preparar los cambios:\n{salida_add}")
        codigo, salida = await _correr("git", "commit", "--message", mensaje, cwd=raiz)
        if codigo != 0:
            return ToolResult(content=f"No se pudo hacer el commit:\n{salida}")
        return ToolResult(
            content=f"Commit local hecho (no se hizo push a ningún remoto):\n{salida}",
            data={"mensaje": mensaje},
        )
