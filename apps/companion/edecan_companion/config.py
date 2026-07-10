"""Configuración local de `edecan_companion` (ARCHITECTURE.md §10.7, §10.12).

Lee (y crea si hace falta) `~/.edecan/companion.yaml`: la carpeta "sandbox" a
la que se restringe todo acceso a archivos, la lista blanca de apps/comandos
que el companion puede ejecutar, y las acciones que se auto-aprueban sin
preguntar.

**Los defaults son intencionalmente vacíos/seguros**: recién instalado, el
companion no puede abrir ninguna app, ejecutar ningún comando ni saltarse
ninguna aprobación — el usuario tiene que editar el YAML a mano (o aprobar
cada acción, una por una, en la terminal) para habilitar cada cosa.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_DIR = Path.home() / ".edecan"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "companion.yaml"
AUDIT_LOG_FILENAME = "companion.log"
DEFAULT_SANDBOX_DIR = "~/EdecanSandbox"

# Plantilla del archivo que se crea la primera vez que se corre el companion.
# Se escribe tal cual (no con yaml.dump) para conservar los comentarios en
# español que le explican al usuario qué controla cada clave.
_CONFIG_TEMPLATE = """\
# Configuración de Edecán Companion
#
# Este archivo controla qué puede hacer el companion en TU computadora.
# Todo empieza vacío/deshabilitado a propósito: cualquier acción que el
# asistente pida seguirá pidiéndote aprobación en la terminal ("[y/N]")
# hasta que la habilites explícitamente aquí abajo.
#
# No lo compartas ni lo edites a la ligera: quien controle este archivo
# controla lo que el companion puede hacer en tu equipo.

# Carpeta a la que se restringe TODO acceso a archivos (read_dir, read_file,
# write_file). Cualquier ruta que intente salir de esta carpeta —con "..",
# con una ruta absoluta, o con un enlace simbólico que apunte afuera— se
# rechaza automáticamente, sin excepción.
sandbox_dir: "{sandbox_dir}"

# Nombres de apps que el companion puede abrir con la acción "open_app"
# (macOS: `open -a <app>`; Linux: `xdg-open <app>`).
# Ejemplo: ["Safari", "Notas"]
allowed_apps: []

# Ejecutables que el companion puede correr con la acción "run_command".
# Se compara solo el primer token del comando (el ejecutable en sí), nunca
# sus argumentos, y siempre se corre sin shell (nada de "&&", ";", tuberías
# ni expansión de variables).
# Ejemplo: ["ls", "git"]
allowed_commands: []

# Nombres de acción (p. ej. "read_dir") que se aprueban automáticamente SIN
# preguntar en la terminal. Vacío por defecto = TODA acción pide aprobación
# explícita. Actívalo con cuidado: lee la advertencia en README.md primero.
auto_approve: []

# Minutos que se recuerda una aprobación después de decir que sí una vez,
# para no volver a preguntar la MISMA acción mientras no pase ese tiempo.
# 0 (default) = apagado: SIEMPRE se pregunta. Un rechazo nunca se recuerda:
# decir que no vuelve a preguntar la próxima vez, sin excepción.
remember_approvals_minutes: 0

# Activa las acciones del IDE embebido (list_tree, search_files, apply_edit,
# screenshot). true por defecto porque, igual que cualquier otra acción,
# cada una sigue pidiendo tu aprobación explícita (o pasando por
# remember_approvals_minutes/auto_approve como cualquier otra) -- ponlo en
# false solo si quieres bloquearlas del todo en esta máquina sin tener que
# tocar allowed_apps/allowed_commands una por una.
ide_enabled: true

# Activa el CONTROL remoto de teclado y mouse (input_pointer, input_key) --
# nivel TeamViewer. false por defecto A PROPÓSITO, a diferencia de
# ide_enabled: dejar que alguien mueva tu mouse o escriba por ti es la
# capacidad de mayor impacto de todo el companion (ver docs/control-remoto.md
# §7), así que -- distinto de todo lo demás en este archivo -- exige opt-in
# EXPLÍCITO del dueño de esta máquina, no basta con que el asistente lo pida
# y tú apruebes una vez en la terminal. Aunque lo actives aquí, cada acción
# input_* SIGUE pidiendo aprobación local (ver remote_input_remember_minutes
# abajo) y además requiere el permiso de Accesibilidad de macOS, que solo un
# clic humano en Ajustes del Sistema puede conceder -- este archivo nunca lo
# evade ni lo automatiza.
remote_input_enabled: false

# Minutos que se recuerda una aprobación de input_pointer/input_key después
# de decir que sí una vez -- SIEMPRE acotado, además, a que siga activa la
# MISMA sesión de control remoto (nunca se hereda a una sesión nueva, aunque
# no hayan pasado estos minutos). A diferencia de remember_approvals_minutes
# (que se puede apagar del todo, 0 = siempre pregunta), este tope existe
# siempre -- es la regla "más dura" que exige el control remoto de teclado/
# mouse frente a cualquier otra acción del companion.
remote_input_remember_minutes: 10
"""


@dataclass
class CompanionConfig:
    """Configuración ya resuelta y lista para usar por `actions.execute`.

    `sandbox_dir` DEBE llegar aquí ya como una ruta absoluta y "real" (sin
    symlinks sin resolver) — `load_config` se encarga de eso con
    `os.path.realpath`. Quien construya `CompanionConfig` a mano (p. ej. los
    tests) debe resolverlo de la misma forma antes de pasarlo, porque
    `actions._resolve_in_sandbox` confía en esa invariante para detectar
    fugas del sandbox por enlaces simbólicos.
    """

    sandbox_dir: Path
    allowed_apps: list[str] = field(default_factory=list)
    allowed_commands: list[str] = field(default_factory=list)
    auto_approve: list[str] = field(default_factory=list)
    remember_approvals_minutes: int = 0
    ide_enabled: bool = True
    # Control remoto de teclado/mouse (WP-V4-10, docs/control-remoto.md §7):
    # ambos APAGADOS/acotados por defecto -- ver el porqué en el comentario
    # de `remote_input_enabled` en `_CONFIG_TEMPLATE` arriba.
    remote_input_enabled: bool = False
    remote_input_remember_minutes: int = 10
    config_path: Path = DEFAULT_CONFIG_PATH
    audit_log_path: Path = DEFAULT_CONFIG_DIR / AUDIT_LOG_FILENAME
    # Estado en memoria de aprobaciones recordadas (`remember_approvals_minutes`):
    # `{acción: expiry monotónico}`. NO viene de companion.yaml y no se
    # persiste a disco -- lo administra por completo `approval.py`. Cada
    # instancia de `CompanionConfig` (p. ej. cada test) arranca con su propio
    # dict vacío gracias a `default_factory`.
    approval_memory: dict[str, float] = field(default_factory=dict, repr=False, compare=False)


def _coerce_str_list(raw: Any, *, field_name: str) -> list[str]:
    """Devuelve `raw` si es una lista de texto; si no, cae a `[]` (seguro) con warning."""
    if raw is None:
        return []
    if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        return list(raw)
    logger.warning(
        "companion.yaml: %r debería ser una lista de texto; se ignora el valor "
        "y se usa [] por seguridad.",
        field_name,
    )
    return []


def _coerce_bool(raw: Any, *, field_name: str, default: bool) -> bool:
    """Devuelve `raw` si ya es `bool`; si no (falta, o es otro tipo), cae a `default`."""
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    logger.warning(
        "companion.yaml: %r debería ser true/false; se ignora el valor y se usa %s por defecto.",
        field_name,
        default,
    )
    return default


def _coerce_non_negative_int(raw: Any, *, field_name: str, default: int) -> int:
    """Devuelve `raw` si es un entero >= 0 (excluye `bool`, subclase de `int`); si no, `default`."""
    if raw is None:
        return default
    if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
        return raw
    logger.warning(
        "companion.yaml: %r debería ser un entero >= 0; se usa el default (%s).",
        field_name,
        default,
    )
    return default


def _write_default_config(path: Path, *, sandbox_dir: str = DEFAULT_SANDBOX_DIR) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_CONFIG_TEMPLATE.format(sandbox_dir=sandbox_dir), encoding="utf-8")
    logger.info("Se creó una configuración nueva con defaults seguros en %s", path)


def load_config(path: Path | str | None = None) -> CompanionConfig:
    """Carga `~/.edecan/companion.yaml` (o `path`), creándolo con defaults seguros si no existe.

    Nunca lanza por un YAML ausente, vacío, mal formado o con claves de tipo
    incorrecto: cualquier problema cae a su default seguro (listas vacías) y
    queda constancia con `logging.warning`.
    """
    config_path = Path(path).expanduser() if path is not None else DEFAULT_CONFIG_PATH

    if not config_path.exists():
        _write_default_config(config_path)

    try:
        raw_text = config_path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw_text) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("No se pudo leer %s (%s); se usan defaults seguros.", config_path, exc)
        data = {}

    if not isinstance(data, dict):
        logger.warning("%s no es un mapeo YAML válido; se usan defaults seguros.", config_path)
        data = {}

    sandbox_raw = data.get("sandbox_dir") or DEFAULT_SANDBOX_DIR
    if not isinstance(sandbox_raw, str) or not sandbox_raw.strip():
        logger.warning("companion.yaml: 'sandbox_dir' debería ser texto no vacío; se usa default.")
        sandbox_raw = DEFAULT_SANDBOX_DIR

    sandbox_dir = Path(os.path.realpath(os.path.expanduser(sandbox_raw)))
    sandbox_dir.mkdir(parents=True, exist_ok=True)

    return CompanionConfig(
        sandbox_dir=sandbox_dir,
        allowed_apps=_coerce_str_list(data.get("allowed_apps"), field_name="allowed_apps"),
        allowed_commands=_coerce_str_list(
            data.get("allowed_commands"), field_name="allowed_commands"
        ),
        auto_approve=_coerce_str_list(data.get("auto_approve"), field_name="auto_approve"),
        remember_approvals_minutes=_coerce_non_negative_int(
            data.get("remember_approvals_minutes"),
            field_name="remember_approvals_minutes",
            default=0,
        ),
        ide_enabled=_coerce_bool(data.get("ide_enabled"), field_name="ide_enabled", default=True),
        remote_input_enabled=_coerce_bool(
            data.get("remote_input_enabled"), field_name="remote_input_enabled", default=False
        ),
        remote_input_remember_minutes=_coerce_non_negative_int(
            data.get("remote_input_remember_minutes"),
            field_name="remote_input_remember_minutes",
            default=10,
        ),
        config_path=config_path,
        audit_log_path=config_path.parent / AUDIT_LOG_FILENAME,
    )
