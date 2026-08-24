"""Acciones "de código" del IDE: ``/simplify``, ``/review``, ``/security-review``,
``/init`` y ``/diff``.

Como casi todo lo demás en ``ide_comandos.py``, ninguna de estas es una
funcionalidad nueva de cero -- cada una es la capa que CONECTA un comando con
una capacidad que ya existe en otro módulo, no una reimplementación:

- ``/diff``  -> ``ide_checkpoints.CheckpointStore`` (el checkpoint del turno ya
  sabe qué archivos se tocaron y su contenido "antes"; aquí solo se arma el
  diff unificado contra el contenido actual, de solo lectura).
- ``/simplify`` -> ``ide_equipo.EquipoDeAgentes`` (el "sub-agente revisor" ya
  existe; lo que faltaba era saber A QUÉ archivos limitarlo, y eso también
  sale del checkpoint del turno).
- ``/review`` -> ``ide_git.GitService.diff`` (el diff pendiente del repo).
  Deliberadamente NO reutiliza ``/simplify`` ni dispara ningún cambio: la
  característica de este comando es ser de solo lectura, así que
  ``RevisionCodigo.solo_lectura`` es una propiedad fija en ``True`` (no un
  parámetro que alguien pueda apagar).
- ``/security-review`` -> ``edecan_toolkit.seguridad.AuditarSeguridadProyectoTool``
  (la tool "auditar_seguridad_proyecto" ya existe). Esa tool se escribió para
  el agente multi-tenant del servidor y espera un ``ToolContext`` completo;
  aquí se arma el mínimo que en verdad usa (``ctx.settings.EDECAN_LOCAL_MODE``
  y ``ctx.settings.EDECAN_LOCAL_REPO_PATH``), apuntando su "modo local" a la
  raíz del workspace que el IDE ya autorizó -- nunca a una variable de entorno
  global del servidor.
- ``/init`` es la única pieza genuinamente nueva: analiza el proyecto (qué
  lenguaje, gestor de paquetes, cómo se corren tests/lint, estructura) y
  escribe un ``AGENTS.md`` inicial. Se complementa con
  ``ide_reglas.load_project_rules``, que es quien LEE ese archivo en cada
  turno posterior -- por eso ``/init`` reusa esa misma función para saber si
  ya existe un archivo de reglas antes de escribir uno nuevo encima.

Este módulo, igual que ``ide_equipo.py`` y ``ide_checkpoints.py``, no importa
``ide_sessions.py`` ni ``routers/ide.py`` (archivos que otra corrida en
paralelo tiene en uso). Los imports de ``edecan_toolkit``/``edecan_core`` para
``/security-review`` son perezosos (dentro de la función), igual que
``ide_workers_agent._run_tool`` hace con ``buscar_web`` -- son dependencias
pesadas (SQLAlchemy, aioboto3, ...) que este módulo no debe pagar al
importarse si el comando nunca se usa.
"""

from __future__ import annotations

import difflib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from edecan_companion.ide_checkpoints import (
    MAX_TRACKED_FILE_BYTES,
    CheckpointStore,
    IDECheckpointError,
)
from edecan_companion.ide_equipo import EquipoError, PlanEquipo, Subtarea, construir_plan
from edecan_companion.ide_git import GitService, IDEGitError
from edecan_companion.ide_reglas import load_project_rules
from edecan_companion.ide_workspaces import IDEWorkspaceError, WorkspaceStore


class IDEAccionesCodigoError(ValueError):
    """Entrada inválida o fallo al conectar con la capacidad real de un comando."""


# --------------------------------------------------------------------------- #
# /diff -- diff unificado del turno, a partir de ide_checkpoints.
# --------------------------------------------------------------------------- #

EstadoDiff = str
"""``"modificado" | "nuevo" | "eliminado" | "sin_cambios" | "no_restaurable"``."""


@dataclass(frozen=True, slots=True)
class DiffArchivo:
    """Un archivo del checkpoint del turno, con su diff ya armado."""

    path: str
    status: EstadoDiff
    diff_texto: str | None


@dataclass(frozen=True, slots=True)
class DiffTurno:
    """Diff completo de un turno (todos los archivos que tocó su checkpoint)."""

    checkpoint_id: str
    archivos: tuple[DiffArchivo, ...]

    def resumen(self) -> str:
        """Texto corto en español listo para mostrar antes del diff completo."""
        if not self.archivos:
            return "Este turno no registró ningún archivo tocado."
        conteos: dict[str, int] = {}
        for archivo in self.archivos:
            conteos[archivo.status] = conteos.get(archivo.status, 0) + 1
        detalle = ", ".join(f"{cantidad} {estado}" for estado, cantidad in conteos.items())
        return f"{len(self.archivos)} archivo(s) en este turno: {detalle}."


def _diff_unificado(path: str, antes: str, despues: str) -> str:
    lineas = difflib.unified_diff(
        antes.splitlines(keepends=True),
        despues.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
    )
    return "".join(lineas)


def _estado_actual(absolute: Path) -> tuple[bool, str | None]:
    """``(demasiado_grande, contenido)`` del archivo EN DISCO ahora mismo.

    Usa el mismo tope que ``ide_checkpoints`` aplica al contenido "antes"
    (``MAX_TRACKED_FILE_BYTES``): diffear un archivo de varios MB no es legible
    y sí puede tumbar la UI. Si no existe, ``contenido`` es ``None`` y
    ``demasiado_grande`` es ``False`` -- quien llama distingue "no existe" de
    "existe pero es enorme" por el primer elemento de la tupla.
    """
    if not absolute.is_file():
        return False, None
    try:
        if absolute.stat().st_size > MAX_TRACKED_FILE_BYTES:
            return True, None
    except OSError:
        return False, None
    try:
        return False, absolute.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False, None


def diff_de_turno(checkpoints: CheckpointStore, checkpoint_id: str) -> DiffTurno:
    """``/diff``: arma el diff unificado de cada archivo que el checkpoint del
    turno capturó, comparando su contenido "antes" (``read_before``, de solo
    lectura) contra el contenido actual en disco. No restaura ni escribe
    nada -- es exactamente la vista que necesita un botón "Aceptar/Rechazar
    archivo por archivo" sin tocar el workspace todavía.
    """
    try:
        resumen = checkpoints.get(checkpoint_id)
    except IDECheckpointError as exc:
        raise IDEAccionesCodigoError(str(exc)) from exc

    workspace_id = resumen["workspace_id"]
    archivos: list[DiffArchivo] = []
    for path in resumen["paths"]:
        antes = checkpoints.read_before(checkpoint_id, path)
        if antes["status"] in ("skipped_too_large", "skipped_budget"):
            archivos.append(DiffArchivo(path=path, status="no_restaurable", diff_texto=None))
            continue

        absolute = checkpoints.workspaces.resolve(workspace_id, path)
        demasiado_grande, contenido_actual = _estado_actual(absolute)
        if demasiado_grande:
            archivos.append(DiffArchivo(path=path, status="no_restaurable", diff_texto=None))
            continue

        existia_antes = antes["status"] == "captured"
        contenido_antes = antes["content"]
        existe_ahora = contenido_actual is not None

        if not existia_antes and not existe_ahora:
            archivos.append(DiffArchivo(path=path, status="sin_cambios", diff_texto=None))
            continue
        if not existia_antes:
            status: EstadoDiff = "nuevo"
        elif not existe_ahora:
            status = "eliminado"
        elif contenido_antes == contenido_actual:
            status = "sin_cambios"
        else:
            status = "modificado"

        texto = (
            None
            if status == "sin_cambios"
            else _diff_unificado(path, contenido_antes or "", contenido_actual or "")
        )
        archivos.append(DiffArchivo(path=path, status=status, diff_texto=texto))

    return DiffTurno(checkpoint_id=checkpoint_id, archivos=tuple(archivos))


# --------------------------------------------------------------------------- #
# /simplify -- reparte el código recién tocado (según el checkpoint) al
# sub-agente revisor de ide_equipo.
# --------------------------------------------------------------------------- #

INSTRUCCIONES_SIMPLIFY_POR_DEFECTO = (
    "Revisa SOLO los archivos listados (los que se acaban de tocar en este "
    "turno) en busca de reutilización, simplificación, eficiencia y limpieza "
    "de alto nivel. No es una cacería de bugs -- eso es /review -- así que no "
    "reportes nada que no sea calidad/legibilidad del código. Aplica los "
    "cambios que valgan la pena directamente."
)


def preparar_simplificacion(
    checkpoints: CheckpointStore,
    checkpoint_id: str,
    *,
    instrucciones: str | None = None,
) -> PlanEquipo:
    """``/simplify``: arma un ``PlanEquipo`` de una sola sub-tarea, dueña
    exclusiva de EXACTAMENTE los archivos que tocó el turno según su
    checkpoint. No ejecuta nada por sí mismo -- devuelve el plan ya validado
    por ``construir_plan``, listo para ``EquipoDeAgentes.ejecutar``; conectar
    qué agente/prompt real corre esa sub-tarea es tarea de quien integre esto
    en ``ide_sessions``/``ide_runtime`` (fuera de este archivo).
    """
    try:
        resumen = checkpoints.get(checkpoint_id)
    except IDECheckpointError as exc:
        raise IDEAccionesCodigoError(str(exc)) from exc

    rutas = tuple(resumen["paths"])
    if not rutas:
        raise IDEAccionesCodigoError(
            "El checkpoint de este turno no registró ningún archivo tocado; "
            "no hay nada que simplificar."
        )

    try:
        subtarea = Subtarea(
            id=f"simplify-{checkpoint_id[:8]}",
            titulo="Simplificar el código recién cambiado",
            instrucciones=instrucciones or INSTRUCCIONES_SIMPLIFY_POR_DEFECTO,
            rutas=rutas,
        )
        return construir_plan([subtarea])
    except EquipoError as exc:
        raise IDEAccionesCodigoError(str(exc)) from exc


# --------------------------------------------------------------------------- #
# /review -- diff pendiente (ide_git), estrictamente de solo lectura.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class RevisionCodigo:
    """Resultado de ``/review``: el diff pendiente, listo para que el modelo
    lo COMENTE -- nunca para que lo aplique.

    ``solo_lectura`` es una propiedad fija en ``True``, no un campo del
    constructor: la característica de ``/review`` es justamente que sea de
    solo lectura (pedido explícito del encargo, no un detalle), así que no
    existe forma de instanciar este resultado con ese valor apagado.
    """

    diff_texto: str
    truncado: bool

    @property
    def solo_lectura(self) -> bool:
        return True

    def as_prompt_block(self) -> str:
        """Texto listo para el prompt del turno de ``/review``, con el aviso
        de solo-lectura siempre presente -- mismo principio que
        ``ide_reglas.ProjectRules.as_prompt_block``: el contenido del diff no
        es una instrucción, y aquí además se deja explícito que el turno no
        debe escribir nada."""
        aviso = (
            "A continuación está el diff pendiente del workspace. Esta es una "
            "revisión de SOLO LECTURA: coméntalo (errores, riesgos, mejoras) "
            "pero no apliques ningún cambio ni modifiques archivos como parte "
            "de este comando."
        )
        recorte = (
            "\n[Aviso: el diff es más largo; se muestra recortado.]" if self.truncado else ""
        )
        return f"{aviso}{recorte}\n<diff_pendiente>\n{self.diff_texto}\n</diff_pendiente>"


def preparar_revision(
    git: GitService,
    workspace_id: str,
    *,
    staged: bool = False,
    paths: list[str] | None = None,
) -> RevisionCodigo:
    """``/review``: obtiene el diff pendiente (``ide_git.GitService.diff``,
    de solo lectura por naturaleza -- es ``git diff``) y lo empaqueta como una
    revisión que el modelo puede comentar pero no ejecutar como cambio."""
    try:
        resultado = git.diff(workspace_id, staged=staged, paths=paths)
    except IDEGitError as exc:
        raise IDEAccionesCodigoError(str(exc)) from exc

    if not resultado["text"].strip():
        raise IDEAccionesCodigoError("No hay cambios pendientes que revisar.")
    return RevisionCodigo(diff_texto=resultado["text"], truncado=resultado["truncated"])


# --------------------------------------------------------------------------- #
# /security-review -- conecta con la tool real auditar_seguridad_proyecto.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _SettingsSoloAuditoriaLocal:
    """``settings`` mínimo para satisfacer el contrato de
    ``AuditarSeguridadProyectoTool`` sin traer nada del servidor multi-tenant.

    La tool real lee dos atributos de un objeto ``settings`` pensado para
    ``edecan_api`` (``EDECAN_LOCAL_MODE``/``EDECAN_LOCAL_REPO_PATH``, ver
    ``packages/toolkit/edecan_toolkit/seguridad.py``). Aquí no hay tenant ni
    servidor: el IDE ya autorizó este workspace por su cuenta
    (``WorkspaceStore``), así que se satisface el contrato apuntando esos dos
    atributos exactamente a la raíz YA autorizada de este workspace -- nunca a
    una variable de entorno global que pudiera apuntar a otro repo.
    """

    _root_repo: str

    @property
    def EDECAN_LOCAL_MODE(self) -> bool:  # noqa: N802 - nombre fijado por el contrato de la tool
        return True

    @property
    def EDECAN_LOCAL_REPO_PATH(self) -> str:  # noqa: N802 - idem
        return self._root_repo


async def auditar_seguridad(
    workspaces: WorkspaceStore, workspace_id: str, *, ruta: str = "."
) -> dict[str, Any]:
    """``/security-review``: conecta con la tool ``auditar_seguridad_proyecto``
    que YA EXISTE en ``edecan_toolkit.seguridad`` en vez de reimplementar el
    escaneo. Devuelve ``{"content": ..., "data": ...}``, el mismo par que
    produce ``ToolResult`` -- quien integre esto puede mostrar ``content``
    directo y guardar ``data`` (hallazgos estructurados) aparte.
    """
    try:
        root = workspaces.root(workspace_id)
    except IDEWorkspaceError as exc:
        raise IDEAccionesCodigoError(str(exc)) from exc

    # Perezoso a propósito: ver docstring del módulo.
    from edecan_core.tools.base import ToolContext
    from edecan_toolkit.seguridad import AuditarSeguridadProyectoTool

    contexto = ToolContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        session=None,
        settings=_SettingsSoloAuditoriaLocal(str(root)),
        llm=None,
        vault=None,
        extras={},
    )
    resultado = await AuditarSeguridadProyectoTool().run(contexto, {"ruta": ruta})
    return {"content": resultado.content, "data": resultado.data}


# --------------------------------------------------------------------------- #
# /init -- la única pieza genuinamente nueva: analizar el proyecto y escribir
# un AGENTS.md inicial. Se complementa con ide_reglas.load_project_rules
# (quien LEE ese archivo en cada turno posterior).
# --------------------------------------------------------------------------- #

_IGNORAR_EN_ESTRUCTURA = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".next",
        ".turbo",
    }
)


@dataclass(frozen=True, slots=True)
class AnalisisProyecto:
    """Convenciones reales detectadas en la raíz de un proyecto -- nunca un
    stack supuesto por defecto: cada campo queda ``None``/vacío si no se
    encontró evidencia concreta en archivos ya presentes en el repo."""

    lenguajes: tuple[str, ...]
    gestor_paquetes: str | None
    comando_tests: str | None
    comando_lint: str | None
    carpetas_principales: tuple[str, ...]


def _scripts_de_package_json(root: Path) -> dict[str, Any]:
    try:
        datos = json.loads((root / "package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    scripts = datos.get("scripts") if isinstance(datos, dict) else None
    return scripts if isinstance(scripts, dict) else {}


def analizar_proyecto(workspace_root: Path) -> AnalisisProyecto:
    """Detecta lenguaje(s), gestor de paquetes y comandos de tests/lint
    mirando SOLO archivos que ya existen en la raíz del proyecto -- nunca
    adivina un stack que no esté declarado ahí.
    """
    try:
        nombres_raiz = {p.name for p in workspace_root.iterdir() if p.is_file()}
        carpetas_raiz = [p for p in workspace_root.iterdir() if p.is_dir()]
    except OSError as exc:
        raise IDEAccionesCodigoError(f"No se pudo leer la raíz del proyecto: {exc}") from exc

    lenguajes: list[str] = []
    gestor: str | None = None
    comando_tests: str | None = None
    comando_lint: str | None = None

    if {"pyproject.toml", "setup.py", "requirements.txt"} & nombres_raiz:
        lenguajes.append("Python")
        texto_pyproject = ""
        if "pyproject.toml" in nombres_raiz:
            try:
                texto_pyproject = (workspace_root / "pyproject.toml").read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError:
                texto_pyproject = ""
        if "uv.lock" in nombres_raiz:
            gestor = "uv"
        elif "poetry.lock" in nombres_raiz:
            gestor = "poetry"
        elif "Pipfile.lock" in nombres_raiz:
            gestor = "pipenv"
        else:
            gestor = "pip"
        comando_tests = "pytest -q" if "pytest" in texto_pyproject else "python -m unittest"
        comando_lint = "ruff check ." if "ruff" in texto_pyproject else None

    if "package.json" in nombres_raiz:
        lenguajes.append("Node/JavaScript o TypeScript")
        if "pnpm-lock.yaml" in nombres_raiz:
            gestor = gestor or "pnpm"
        elif "yarn.lock" in nombres_raiz:
            gestor = gestor or "yarn"
        elif "bun.lockb" in nombres_raiz:
            gestor = gestor or "bun"
        else:
            gestor = gestor or "npm"
        scripts = _scripts_de_package_json(workspace_root)
        if "test" in scripts and comando_tests is None:
            comando_tests = f"{gestor} run test"
        if "lint" in scripts and comando_lint is None:
            comando_lint = f"{gestor} run lint"

    if "Cargo.toml" in nombres_raiz:
        lenguajes.append("Rust")
        gestor = gestor or "cargo"
        comando_tests = comando_tests or "cargo test"
        comando_lint = comando_lint or "cargo clippy"

    if "go.mod" in nombres_raiz:
        lenguajes.append("Go")
        gestor = gestor or "go modules"
        comando_tests = comando_tests or "go test ./..."
        comando_lint = comando_lint or "go vet ./..."

    if "Gemfile" in nombres_raiz:
        lenguajes.append("Ruby")
        gestor = gestor or "bundler"
        comando_tests = comando_tests or "bundle exec rspec"

    if not lenguajes:
        lenguajes.append("No identificado automáticamente")

    carpetas_principales = tuple(
        sorted(
            p.name
            for p in carpetas_raiz
            if not p.name.startswith(".") and p.name not in _IGNORAR_EN_ESTRUCTURA
        )[:20]
    )

    return AnalisisProyecto(
        lenguajes=tuple(lenguajes),
        gestor_paquetes=gestor,
        comando_tests=comando_tests,
        comando_lint=comando_lint,
        carpetas_principales=carpetas_principales,
    )


def generar_agents_md(analisis: AnalisisProyecto) -> str:
    """Texto Markdown de un ``AGENTS.md`` inicial a partir de ``analisis``."""
    lineas = [
        "# AGENTS.md",
        "",
        "Generado automáticamente por `/init` a partir de las convenciones",
        "detectadas en este proyecto. Ajusta lo que no sea exacto.",
        "",
        "## Lenguaje(s)",
        ", ".join(analisis.lenguajes),
        "",
        "## Gestor de paquetes",
        analisis.gestor_paquetes or "No identificado.",
        "",
        "## Cómo correr los tests",
        f"`{analisis.comando_tests}`"
        if analisis.comando_tests
        else "No se detectó un comando de tests.",
        "",
        "## Lint",
        f"`{analisis.comando_lint}`"
        if analisis.comando_lint
        else "No se detectó un comando de lint.",
        "",
        "## Estructura del proyecto",
    ]
    if analisis.carpetas_principales:
        lineas.extend(f"- `{carpeta}/`" for carpeta in analisis.carpetas_principales)
    else:
        lineas.append("Sin carpetas de primer nivel relevantes detectadas.")
    lineas.append("")
    return "\n".join(lineas)


def escribir_agents_md(
    workspaces: WorkspaceStore, workspace_id: str, *, overwrite: bool = False
) -> dict[str, Any]:
    """``/init``: analiza el proyecto y escribe ``AGENTS.md`` en su raíz.

    Reusa ``ide_reglas.load_project_rules`` para saber si YA existe un
    archivo de reglas (``AGENTS.md`` u otro de sus candidatos) -- si lo hay y
    ``overwrite=False``, no se toca nada y se informa cuál es, para que la UI
    pueda preguntar antes de pisarlo. Escritura atómica (tmp + ``os.replace``),
    mismo criterio que ``ide_checkpoints``/``ide_workspaces``.
    """
    try:
        root = workspaces.root(workspace_id)
    except IDEWorkspaceError as exc:
        raise IDEAccionesCodigoError(str(exc)) from exc

    existentes = load_project_rules(root)
    if existentes.found and not overwrite:
        return {"escrito": False, "archivo_existente": existentes.source_path}

    analisis = analizar_proyecto(root)
    contenido = generar_agents_md(analisis)
    destino = root / "AGENTS.md"
    tmp_path = destino.with_suffix(".md.tmp")
    tmp_path.write_text(contenido, encoding="utf-8")
    os.replace(tmp_path, destino)

    return {
        "escrito": True,
        "ruta": "AGENTS.md",
        "lenguajes": list(analisis.lenguajes),
        "gestor_paquetes": analisis.gestor_paquetes,
        "comando_tests": analisis.comando_tests,
        "comando_lint": analisis.comando_lint,
    }
