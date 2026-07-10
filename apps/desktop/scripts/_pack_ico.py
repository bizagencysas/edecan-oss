#!/usr/bin/env python3
"""Empaqueta uno o más PNG cuadrados en un único `.ico` válido.

Usa el formato "PNG-in-ICO" (soportado desde Windows Vista): cada entrada del
directorio ICO puede apuntar a datos PNG crudos en vez del BMP/DIB clásico.
Evita depender de Pillow/ImageMagick solo para este paso — únicamente usa la
stdlib (`struct`), así `scripts/make-icons.sh` no necesita más requisitos que
`sips`/`iconutil` (macOS) + `python3`.

Uso: _pack_ico.py <salida.ico> <img1.png> [img2.png ...]
Cada PNG debe ser cuadrado y medir 256px o menos por lado (el formato ICO
clásico codifica el tamaño en un byte; 256 se representa como 0, según el
estándar).
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path


def _png_size(data: bytes) -> tuple[int, int]:
    """Lee ancho/alto desde el chunk IHDR (bytes 16..24 de un PNG válido)."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("no es un PNG válido")
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def pack_ico(output_path: Path, png_paths: list[Path]) -> None:
    entries: list[tuple[int, int, bytes]] = []
    for png_path in png_paths:
        data = png_path.read_bytes()
        width, height = _png_size(data)
        if width != height:
            raise ValueError(f"{png_path} no es cuadrado ({width}x{height})")
        entries.append((width, height, data))

    # ICONDIR: reserved(2)=0, type(2)=1 (icono), count(2)
    header = struct.pack("<HHH", 0, 1, len(entries))

    directory = b""
    image_data = b""
    offset = len(header) + 16 * len(entries)  # cada ICONDIRENTRY mide 16 bytes
    for width, height, data in entries:
        w_byte = 0 if width >= 256 else width
        h_byte = 0 if height >= 256 else height
        # ICONDIRENTRY: width, height, colorCount, reserved, planes, bitcount,
        # bytesInRes, imageOffset
        directory += struct.pack(
            "<BBBBHHII", w_byte, h_byte, 0, 0, 1, 32, len(data), offset
        )
        image_data += data
        offset += len(data)

    output_path.write_bytes(header + directory + image_data)


def main() -> None:
    if len(sys.argv) < 3:
        print(f"uso: {sys.argv[0]} <salida.ico> <img1.png> [img2.png ...]", file=sys.stderr)
        raise SystemExit(2)
    output_path = Path(sys.argv[1])
    png_paths = [Path(p) for p in sys.argv[2:]]
    pack_ico(output_path, png_paths)
    print(f"escrito {output_path} ({len(png_paths)} tamaños)")


if __name__ == "__main__":
    main()
