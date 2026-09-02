"""Terminal COMPARTIDO por proyecto (`terminal_compartido`).

Los bots y el IDE trabajan sobre UN MISMO shell por carpeta: heredan cwd y
entorno (cooperación real), sin abrir Terminal.app (es un `Popen` con pipes,
salida limpia sin eco/ANSI). Un comando a la vez (lock). Estos tests fijan el
contrato: salida aislada, `cd` persistente y paralelismo sin pisarse.
"""

from __future__ import annotations

import asyncio
import pathlib

from edecan_companion.ide_sessions import SessionManager
from edecan_companion.ide_workspaces import WorkspaceStore


def _manager(tmp_path: pathlib.Path) -> SessionManager:
    return SessionManager(tmp_path, WorkspaceStore(tmp_path))


async def test_salida_aislada_del_comando(tmp_path):
    proyecto = tmp_path / "repo"; proyecto.mkdir(); (proyecto / "app.txt").write_text("x")
    mgr = _manager(tmp_path)
    r = await mgr.terminal_compartido(str(proyecto), "ls")
    assert r["ok"] is True
    assert "app.txt" in r["result"]["stdout"]


async def test_cd_persiste_entre_comandos(tmp_path):
    proyecto = tmp_path / "repo"; proyecto.mkdir(); (proyecto / "sub").mkdir()
    mgr = _manager(tmp_path)
    await mgr.terminal_compartido(str(proyecto), "cd sub")
    r = await mgr.terminal_compartido(str(proyecto), "pwd")
    assert r["ok"] is True
    assert "sub" in r["result"]["stdout"]


async def test_dos_comandos_comparten_shell(tmp_path):
    proyecto = tmp_path / "repo"; proyecto.mkdir()
    mgr = _manager(tmp_path)
    await mgr.terminal_compartido(str(proyecto), "echo hola > creado.txt")
    r = await mgr.terminal_compartido(str(proyecto), "cat creado.txt")
    assert "hola" in r["result"]["stdout"]


async def test_paralelo_no_se_ensucia(tmp_path):
    proyecto = tmp_path / "repo"; proyecto.mkdir()
    mgr = _manager(tmp_path)

    async def a():
        return await mgr.terminal_compartido(str(proyecto), "sleep 0.2 && echo TAREA_A")

    async def b():
        return await mgr.terminal_compartido(str(proyecto), "sleep 0.05 && echo TAREA_B")

    ra, rb = await asyncio.gather(a(), b())
    assert "TAREA_A" in ra["result"]["stdout"]
    assert "TAREA_B" in rb["result"]["stdout"]
