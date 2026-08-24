"""Los lanzadores de servidores MCP en Windows son guiones, no ejecutables.

`npx`, `npm`, `yarn` y `pnpm` se instalan como `.CMD`. `CreateProcess` no los
sabe ejecutar, así que `asyncio.create_subprocess_exec("npx", ...)` falla con
`FileNotFoundError` aunque estén perfectamente instalados. Medido en Windows
Server 2022 el 30-07-2026: `node` arranca, `npx` y `npm` no.

Sin el arreglo, ningún servidor MCP basado en Node funciona en Windows.
"""

from __future__ import annotations

from edecan_mcp.transport import _argv_ejecutable


def test_en_posix_no_toca_nada(monkeypatch):
    monkeypatch.setattr("edecan_mcp.transport.os.name", "posix")
    assert _argv_ejecutable(["npx", "-y", "servidor"]) == ["npx", "-y", "servidor"]


def test_en_windows_un_cmd_se_lanza_por_comspec(monkeypatch):
    monkeypatch.setattr("edecan_mcp.transport.os.name", "nt")
    monkeypatch.setattr(
        "edecan_mcp.transport.shutil.which",
        lambda c: r"C:\Program Files\nodejs\npx.CMD" if c == "npx" else None,
    )
    monkeypatch.setenv("COMSPEC", r"C:\Windows\system32\cmd.exe")
    assert _argv_ejecutable(["npx", "-y", "servidor"]) == [
        r"C:\Windows\system32\cmd.exe",
        "/c",
        r"C:\Program Files\nodejs\npx.CMD",
        "-y",
        "servidor",
    ]


def test_en_windows_un_exe_usa_la_ruta_resuelta(monkeypatch):
    """Un ejecutable de verdad no necesita `cmd.exe` de por medio."""
    monkeypatch.setattr("edecan_mcp.transport.os.name", "nt")
    monkeypatch.setattr(
        "edecan_mcp.transport.shutil.which",
        lambda c: r"C:\Program Files\nodejs\node.EXE" if c == "node" else None,
    )
    assert _argv_ejecutable(["node", "s.js"]) == [r"C:\Program Files\nodejs\node.EXE", "s.js"]


def test_si_no_se_resuelve_se_deja_igual(monkeypatch):
    """Que falle en `create_subprocess_exec` con su mensaje habitual: tragarse
    el error aquí solo dificultaría el diagnóstico."""
    monkeypatch.setattr("edecan_mcp.transport.os.name", "nt")
    monkeypatch.setattr("edecan_mcp.transport.shutil.which", lambda c: None)
    assert _argv_ejecutable(["inexistente", "x"]) == ["inexistente", "x"]


def test_los_argumentos_nunca_se_concatenan_en_una_cadena(monkeypatch):
    """La defensa contra inyección: `cmd.exe /c` recibe argv separado, nunca
    una cadena que un shell vuelva a interpretar."""
    monkeypatch.setattr("edecan_mcp.transport.os.name", "nt")
    monkeypatch.setattr("edecan_mcp.transport.shutil.which", lambda c: r"C:\x\npx.CMD")
    monkeypatch.setenv("COMSPEC", r"C:\Windows\system32\cmd.exe")
    argv = _argv_ejecutable(["npx", "servidor & calc.exe"])
    assert argv[-1] == "servidor & calc.exe"
    assert not any("&" in a for a in argv[:-1])


def test_comando_vacio_no_revienta(monkeypatch):
    monkeypatch.setattr("edecan_mcp.transport.os.name", "nt")
    assert _argv_ejecutable([]) == []
