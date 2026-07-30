"""Tests de las acciones de transferencia de archivos (`transfer_*`).

Buzón compartido `config.transfer_dir` (aislado en `tmp_path` por el fixture
`companion_config`): el teléfono empuja archivos, los lista y los recupera —
sin que un `name` malicioso escape jamás de esa carpeta.
"""

from __future__ import annotations

import base64

import pytest
from edecan_companion import actions
from edecan_companion.actions import ActionError


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def test_push_then_pull_roundtrip(companion_config):
    contenido = b"hola desde el telefono \x00\x01\x02"
    pushed = actions._transfer_push(
        {"name": "nota.bin", "content_b64": _b64(contenido)}, companion_config
    )
    assert pushed["name"] == "nota.bin"
    assert pushed["bytes"] == len(contenido)
    assert (companion_config.transfer_dir / "nota.bin").read_bytes() == contenido

    pulled = actions._transfer_pull({"name": "nota.bin"}, companion_config)
    assert base64.b64decode(pulled["content_b64"]) == contenido
    assert pulled["bytes"] == len(contenido)
    assert pulled["name"] == "nota.bin"


def test_push_never_overwrites_dedupes_name(companion_config):
    first = actions._transfer_push(
        {"name": "foto.png", "content_b64": _b64(b"a")}, companion_config
    )
    second = actions._transfer_push(
        {"name": "foto.png", "content_b64": _b64(b"b")}, companion_config
    )
    assert first["name"] == "foto.png"
    assert second["name"] == "foto (2).png"
    assert (companion_config.transfer_dir / "foto.png").read_bytes() == b"a"
    assert (companion_config.transfer_dir / "foto (2).png").read_bytes() == b"b"


@pytest.mark.parametrize(
    "malicious",
    ["../escape.txt", "../../etc/passwd", "sub/dir/file.txt", "..", ".", "", "a/../../b"],
)
def test_push_rejects_or_flattens_path_traversal(companion_config, malicious):
    # O bien se rechaza (nombre inválido), o el basename colapsa a algo que
    # queda DENTRO del buzón — nunca se escribe fuera de transfer_dir.
    try:
        result = actions._transfer_push(
            {"name": malicious, "content_b64": _b64(b"x")}, companion_config
        )
    except ActionError:
        return
    written = companion_config.transfer_dir / result["name"]
    assert written.resolve().parent == companion_config.transfer_dir.resolve()


def test_pull_rejects_path_traversal(companion_config):
    # Sembramos un archivo fuera del buzón y confirmamos que no se puede leer.
    secreto = companion_config.transfer_dir.parent / "secreto.txt"
    companion_config.transfer_dir.mkdir(parents=True, exist_ok=True)
    secreto.write_text("no me deberías poder leer")
    with pytest.raises(ActionError):
        actions._transfer_pull({"name": "../secreto.txt"}, companion_config)


def test_pull_missing_file_raises(companion_config):
    with pytest.raises(ActionError, match="no existe"):
        actions._transfer_pull({"name": "fantasma.txt"}, companion_config)


def test_list_reports_only_files_newest_first(companion_config):
    actions._transfer_push({"name": "viejo.txt", "content_b64": _b64(b"1")}, companion_config)
    actions._transfer_push({"name": "nuevo.txt", "content_b64": _b64(b"22")}, companion_config)
    (companion_config.transfer_dir / "una_carpeta").mkdir()

    listado = actions._transfer_list({}, companion_config)
    nombres = [item["name"] for item in listado["files"]]
    assert "una_carpeta" not in nombres
    assert set(nombres) == {"viejo.txt", "nuevo.txt"}
    # Más reciente primero (nuevo.txt se creó después).
    assert nombres[0] == "nuevo.txt"
    assert listado["files"][0]["bytes"] == 2


def test_push_rejects_oversize_before_decoding(companion_config):
    # Un cuerpo base64 más grande que el tope infla-corregido debe rechazarse
    # sin intentar decodificar el contenido completo.
    enorme = "A" * ((actions.MAX_TRANSFER_BYTES // 3) * 4 + 100)
    with pytest.raises(ActionError, match="máximo"):
        actions._transfer_push({"name": "grande.bin", "content_b64": enorme}, companion_config)


def test_push_requires_valid_base64(companion_config):
    with pytest.raises(ActionError, match="base64"):
        actions._transfer_push(
            {"name": "malo.bin", "content_b64": "esto no es base64 válido !!!"}, companion_config
        )


def test_push_requires_name_and_content(companion_config):
    with pytest.raises(ActionError, match="name"):
        actions._transfer_push({"content_b64": _b64(b"x")}, companion_config)
    with pytest.raises(ActionError, match="content_b64"):
        actions._transfer_push({"name": "x.txt"}, companion_config)
