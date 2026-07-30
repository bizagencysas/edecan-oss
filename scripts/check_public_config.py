#!/usr/bin/env python3
"""Evita publicar configuración privada de una instalación de Edecán.

El control revisa archivos rastreados y archivos nuevos no ignorados por Git.
No abre `.env`, configuraciones privadas ignoradas ni almacenes de credenciales
locales.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DENYLIST_ENV = "EDECAN_PRIVATE_DENYLIST_FILE"

SENSITIVE_INFRA_PATHS = (
    "infra/aws/edge/",
    "infra/cloudflare/edge/",
)
SENSITIVE_INFRA_EXCLUSIONS = (
    "package-lock.json",
    "worker-configuration.d.ts",
)
SENSITIVE_INFRA_PATTERNS = {
    "id de cuenta AWS de 12 dígitos": re.compile(r"(?<!\d)\d{12}(?!\d)"),
    "id de cuenta Cloudflare de 32 hexadecimales": re.compile(
        r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])",
        re.IGNORECASE,
    ),
    "correo personal en configuración de infraestructura": re.compile(
        r"(?<![A-Z0-9._%+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![A-Z0-9.-])",
        re.IGNORECASE,
    ),
}
GENERIC_PUBLIC_PATTERNS = {
    "ruta absoluta de una cuenta macOS": re.compile(
        r"/Users/(?!example(?:/|$)|usuario(?:/|$)|tu-?usuario(?:/|$))[A-Za-z0-9._-]+"
    ),
    "ruta absoluta de una cuenta Linux": re.compile(
        r"/home/(?!example(?:/|$)|usuario(?:/|$)|tu-?usuario(?:/|$))[A-Za-z0-9._-]+"
    ),
}


def public_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    return [
        REPO_ROOT / item.decode("utf-8", errors="surrogateescape")
        for item in result.stdout.split(b"\0")
        if item
    ]


def is_sensitive_infra(relative_path: str) -> bool:
    return relative_path.startswith(SENSITIVE_INFRA_PATHS) and not relative_path.endswith(
        SENSITIVE_INFRA_EXCLUSIONS
    )


def private_denylist() -> tuple[str, ...]:
    """Carga valores privados desde fuera del repositorio, cuando se configure.

    La lista es deliberadamente externa: el control público no debe filtrar los
    mismos dominios, nombres o rutas cuya publicación pretende impedir.
    """

    configured = os.environ.get(PRIVATE_DENYLIST_ENV, "").strip()
    if not configured:
        return ()
    path = Path(configured).expanduser().resolve()
    if REPO_ROOT == path or REPO_ROOT in path.parents:
        raise ValueError(
            f"{PRIVATE_DENYLIST_ENV} debe apuntar a un archivo privado fuera del repositorio"
        )
    values = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        value = raw_line.strip()
        if value and not value.startswith("#"):
            values.append(value.casefold())
    return tuple(dict.fromkeys(values))


def main() -> int:
    findings: list[str] = []
    try:
        denied_values = private_denylist()
    except (OSError, ValueError) as exc:
        print(f"No se pudo cargar la lista privada: {exc}", file=sys.stderr)
        return 2

    for path in public_files():
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        relative_path = path.relative_to(REPO_ROOT).as_posix()
        folded = content.casefold()
        for value in denied_values:
            if value in folded:
                findings.append(
                    f"{relative_path}: coincide con la lista privada externa"
                )

        for description, pattern in GENERIC_PUBLIC_PATTERNS.items():
            if pattern.search(content):
                findings.append(f"{relative_path}: contiene {description}")

        if is_sensitive_infra(relative_path):
            for description, pattern in SENSITIVE_INFRA_PATTERNS.items():
                if pattern.search(content):
                    findings.append(f"{relative_path}: contiene {description}")

    if findings:
        print(
            "La configuración pública contiene datos específicos de una instalación:",
            file=sys.stderr,
        )
        for finding in sorted(set(findings)):
            print(f"  - {finding}", file=sys.stderr)
        return 1

    print("Configuración pública neutral: sin dominios ni identificadores personales.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
