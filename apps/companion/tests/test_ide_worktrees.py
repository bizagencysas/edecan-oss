"""Contrato de ``ide_worktrees``: aislamiento real, fusión honesta, cero basura.

Todo corre contra repositorios Git DE VERDAD creados en ``tmp_path``. No hay
mocks a propósito: la pieza entera consiste en que git haga exactamente lo que
creemos que hace (``stash create`` sin tocar el working tree, ``merge-tree``
sin índice, ``apply`` todo-o-nada). Un mock probaría nuestra idea de git, que
es justo lo que no queremos probar.

Lo que estas pruebas fijan:
- crear/destruir deja el repo del dueño exactamente como estaba;
- dos sub-agentes en worktrees distintos NO pueden pisarse;
- cambios compatibles (incluso en el mismo archivo) se fusionan solos;
- cambios en conflicto NO se resuelven: se reportan y no se aplica nada;
- la limpieza ocurre aunque el agente falle, lo cancelen o el proceso muera;
- sin git (o sin commits) no se rompe: se responde por qué;
- el trabajo sin commitear del dueño viaja al worktree y sobrevive a la vuelta.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from edecan_companion import ide_worktrees as ide_worktrees_module
from edecan_companion.ide_equipo import EquipoDeAgentes, Subtarea, construir_plan
from edecan_companion.ide_worktrees import (
    GestorWorktrees,
    TopesWorktrees,
    WorktreeError,
    barrer_huerfanos,
    envolver_runner,
    evaluar_repo,
)

# ---------------------------------------------------------------------------
# Utilidades: un repo git real, mínimo y desechable.
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    completado = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=True,
        shell=False,
    )
    return completado.stdout.decode("utf-8", errors="replace").strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    raiz = tmp_path / "repo"
    raiz.mkdir()
    _git(raiz, "init", "--quiet", "--initial-branch=main")
    _git(raiz, "config", "user.email", "prueba@localhost")
    _git(raiz, "config", "user.name", "Prueba")
    (raiz / "a.txt").write_text("uno\ndos\ntres\n", encoding="utf-8")
    (raiz / "b.txt").write_text("bes\n", encoding="utf-8")
    (raiz / ".gitignore").write_text("salida/\n", encoding="utf-8")
    _git(raiz, "add", "--all")
    _git(raiz, "commit", "--quiet", "--message", "base")
    return raiz


@pytest.fixture
def base_dir(tmp_path: Path) -> Path:
    """Las copias viven en ``tmp_path``, nunca en el temporal real del sistema."""
    return tmp_path / "worktrees"


def _gestor(repo: Path, base_dir: Path, **topes: float) -> GestorWorktrees:
    return GestorWorktrees(repo, base_dir=base_dir, topes=TopesWorktrees(**topes))


# ---------------------------------------------------------------------------
# Diagnóstico: sin git, sin repo, sin commits -> no se rompe, se explica.
# ---------------------------------------------------------------------------


def test_carpeta_sin_git_no_es_usable_y_dice_por_que(tmp_path: Path) -> None:
    suelta = tmp_path / "proyecto-suelto"
    suelta.mkdir()
    diagnostico = evaluar_repo(suelta, base_dir=tmp_path / "wt")
    assert not diagnostico.usable
    assert diagnostico.codigo == "no_es_repo"
    assert "no es un repositorio" in diagnostico.motivo.lower()
    assert "reparten por archivos" in diagnostico.motivo


def test_repo_sin_commits_no_es_usable(tmp_path: Path) -> None:
    vacio = tmp_path / "vacio"
    vacio.mkdir()
    _git(vacio, "init", "--quiet")
    diagnostico = evaluar_repo(vacio, base_dir=tmp_path / "wt")
    assert not diagnostico.usable
    assert diagnostico.codigo == "sin_commits"


def test_gestor_rechaza_construirse_sobre_algo_que_no_es_repo(tmp_path: Path) -> None:
    suelta = tmp_path / "suelta"
    suelta.mkdir()
    with pytest.raises(WorktreeError, match="repositorio Git"):
        GestorWorktrees(suelta, base_dir=tmp_path / "wt")


def test_repo_mas_pesado_que_el_tope_no_es_usable(repo: Path, base_dir: Path) -> None:
    diagnostico = evaluar_repo(
        repo, base_dir=base_dir, topes=TopesWorktrees(max_mb_por_worktree=0.000001)
    )
    assert not diagnostico.usable
    assert diagnostico.codigo == "repo_enorme"
    assert "disco" in diagnostico.motivo


def test_disco_insuficiente_no_es_usable(repo: Path, base_dir: Path) -> None:
    # margen_disco gigante: fuerza el mismo camino que un disco lleno de verdad
    # sin tener que llenar el disco de la máquina que corre las pruebas.
    diagnostico = evaluar_repo(
        repo, base_dir=base_dir, topes=TopesWorktrees(margen_disco=10.0**12)
    )
    assert not diagnostico.usable
    assert diagnostico.codigo == "poco_disco"


def test_repo_normal_si_es_usable(repo: Path, base_dir: Path) -> None:
    diagnostico = evaluar_repo(repo, base_dir=base_dir)
    assert diagnostico.usable
    assert diagnostico.codigo == "ok"
    assert diagnostico.raiz_repo == repo.resolve()
    assert diagnostico.mb_por_worktree is not None


# ---------------------------------------------------------------------------
# Crear y destruir.
# ---------------------------------------------------------------------------


def test_crear_da_una_copia_completa_y_destruir_no_deja_rastro(
    repo: Path, base_dir: Path
) -> None:
    estado_antes = _git(repo, "status", "--porcelain")
    with _gestor(repo, base_dir) as gestor:
        worktree = gestor.crear("uno")
        assert (worktree.ruta / "a.txt").read_text(encoding="utf-8") == "uno\ndos\ntres\n"
        assert worktree.ruta.is_dir()
        assert str(worktree.ruta) in _git(repo, "worktree", "list", "--porcelain")
        gestor.destruir(worktree)
        assert not worktree.ruta.exists()
        assert str(worktree.ruta) not in _git(repo, "worktree", "list", "--porcelain")
    assert _git(repo, "status", "--porcelain") == estado_antes


def test_el_worktree_vive_fuera_del_repo(repo: Path, base_dir: Path) -> None:
    # Dentro del árbol de trabajo aparecería como basura sin rastrear en el
    # ``git status`` del dueño, y los buscadores indexarían el repo dos veces.
    with _gestor(repo, base_dir) as gestor:
        worktree = gestor.crear("uno")
        assert repo.resolve() not in worktree.ruta.resolve().parents
        assert _git(repo, "status", "--porcelain") == ""


def test_dos_sub_agentes_no_pueden_pisarse(repo: Path, base_dir: Path) -> None:
    with _gestor(repo, base_dir) as gestor:
        uno = gestor.crear("uno")
        dos = gestor.crear("dos")
        assert uno.ruta != dos.ruta
        (uno.ruta / "a.txt").write_text("LO_ESCRIBIO_UNO\n", encoding="utf-8")
        # Lo que escribió "uno" no existe para "dos" ni para el dueño.
        assert (dos.ruta / "a.txt").read_text(encoding="utf-8") == "uno\ndos\ntres\n"
        assert (repo / "a.txt").read_text(encoding="utf-8") == "uno\ndos\ntres\n"


def test_ids_distintos_nunca_comparten_carpeta(repo: Path, base_dir: Path) -> None:
    # "api/paso 1" y "api-paso-1" colapsarían al mismo nombre saneado; el
    # sufijo con hash es lo que impide que dos worktrees ocupen la misma ruta.
    with _gestor(repo, base_dir) as gestor:
        primero = gestor.crear("api/paso 1")
        segundo = gestor.crear("api-paso-1")
        assert primero.ruta != segundo.ruta


def test_el_tope_de_copias_se_respeta(repo: Path, base_dir: Path) -> None:
    with _gestor(repo, base_dir, max_worktrees=2) as gestor:
        gestor.crear("uno")
        gestor.crear("dos")
        with pytest.raises(WorktreeError, match="tope"):
            gestor.crear("tres")


def test_tope_de_tiempo_corta_despues_de_la_primera_copia(repo: Path, base_dir: Path) -> None:
    # Con el tope en un valor imposible de cumplir, la primera copia se crea
    # (hay que medirla para saberlo) y la segunda ya se rechaza.
    with _gestor(repo, base_dir, max_segundos_creacion=0.000001) as gestor:
        gestor.crear("uno")
        with pytest.raises(WorktreeError, match="No compensa"):
            gestor.crear("dos")


# ---------------------------------------------------------------------------
# Cosecha.
# ---------------------------------------------------------------------------


def test_cosechar_congela_lo_que_dejo_el_sub_agente(repo: Path, base_dir: Path) -> None:
    with _gestor(repo, base_dir) as gestor:
        worktree = gestor.crear("uno")
        (worktree.ruta / "nuevo.txt").write_text("recien nacido\n", encoding="utf-8")
        (worktree.ruta / "b.txt").unlink()
        cosecha = gestor.cosechar(worktree)
        assert not cosecha.sin_cambios
        assert set(cosecha.archivos) == {"nuevo.txt", "b.txt"}
        # El commit sobrevive a la destrucción de la carpeta: vive en un ref.
        gestor.destruir(worktree)
        assert _git(repo, "cat-file", "-t", str(cosecha.commit)) == "commit"


def test_un_sub_agente_que_no_toco_nada_no_aporta(repo: Path, base_dir: Path) -> None:
    with _gestor(repo, base_dir) as gestor:
        worktree = gestor.crear("uno")
        cosecha = gestor.cosechar(worktree)
        assert cosecha.sin_cambios
        assert cosecha.commit is None
        assert gestor.fusionar().sin_cambios == ("uno",)


def test_lo_que_gitignore_excluye_no_vuelve_pero_se_avisa(repo: Path, base_dir: Path) -> None:
    with _gestor(repo, base_dir) as gestor:
        worktree = gestor.crear("uno")
        (worktree.ruta / "salida").mkdir()
        (worktree.ruta / "salida" / "build.js").write_text("artefacto\n", encoding="utf-8")
        (worktree.ruta / "a.txt").write_text("cambiado\n", encoding="utf-8")
        cosecha = gestor.cosechar(worktree)
        assert cosecha.archivos == ("a.txt",)
        assert any("salida" in ruta for ruta in cosecha.ignorados)


def test_un_agente_que_solo_escribio_en_zonas_ignoradas_igual_avisa(
    repo: Path, base_dir: Path
) -> None:
    # El caso peor de todos: TODO su trabajo cayó en una ruta ignorada. Sin
    # este aviso la cosecha diría "no tocó nada" y la pérdida sería silenciosa.
    with _gestor(repo, base_dir) as gestor:
        worktree = gestor.crear("uno")
        (worktree.ruta / "salida").mkdir()
        (worktree.ruta / "salida" / "todo.js").write_text("lo único\n", encoding="utf-8")
        cosecha = gestor.cosechar(worktree)
        assert cosecha.sin_cambios
        assert any("salida" in ruta for ruta in cosecha.ignorados)


def test_cosechas_simultaneas_no_se_disputan_el_indice(repo: Path, base_dir: Path) -> None:
    # Cada worktree tiene su propio index; si compartieran uno, esto pelearía
    # por ``index.lock`` y fallaría de forma intermitente.
    with _gestor(repo, base_dir, max_worktrees=4) as gestor:
        worktrees = [gestor.crear(f"w{i}") for i in range(4)]
        for i, worktree in enumerate(worktrees):
            (worktree.ruta / f"f{i}.txt").write_text(f"{i}\n", encoding="utf-8")
        with ThreadPoolExecutor(max_workers=4) as pool:
            cosechas = list(pool.map(gestor.cosechar, worktrees))
        assert all(not c.sin_cambios for c in cosechas)
        assert len({c.commit for c in cosechas}) == 4


# ---------------------------------------------------------------------------
# Fusión limpia.
# ---------------------------------------------------------------------------


def test_cambios_en_archivos_distintos_se_fusionan_y_se_aplican(
    repo: Path, base_dir: Path
) -> None:
    with _gestor(repo, base_dir) as gestor:
        uno = gestor.crear("uno")
        dos = gestor.crear("dos")
        (uno.ruta / "a.txt").write_text("uno\nDOS_DE_UNO\ntres\n", encoding="utf-8")
        (dos.ruta / "b.txt").write_text("bes de dos\n", encoding="utf-8")
        gestor.cosechar(uno)
        gestor.cosechar(dos)

        fusion = gestor.fusionar()
        assert fusion.ok
        assert set(fusion.aportaron) == {"uno", "dos"}
        assert set(fusion.archivos) == {"a.txt", "b.txt"}
        # Fusionar es cálculo puro: todavía no tocó nada del dueño.
        assert (repo / "b.txt").read_text(encoding="utf-8") == "bes\n"

        aplicacion = gestor.aplicar(fusion)
        assert aplicacion.aplicado
        assert (repo / "a.txt").read_text(encoding="utf-8") == "uno\nDOS_DE_UNO\ntres\n"
        assert (repo / "b.txt").read_text(encoding="utf-8") == "bes de dos\n"
        # Sin commitear: el dueño revisa antes.
        assert _git(repo, "status", "--porcelain") != ""


def test_dos_sub_agentes_en_el_mismo_archivo_pero_distintas_lineas_se_juntan(
    repo: Path, base_dir: Path
) -> None:
    # Este es el caso que justifica la pieza: hoy ``ide_reparto`` mandaría
    # estos dos pasos a oleadas separadas por precaución. Con worktrees corren
    # juntos y git los junta bien.
    with _gestor(repo, base_dir) as gestor:
        uno = gestor.crear("uno")
        dos = gestor.crear("dos")
        (uno.ruta / "a.txt").write_text("PRIMERA\ndos\ntres\n", encoding="utf-8")
        (dos.ruta / "a.txt").write_text("uno\ndos\nULTIMA\n", encoding="utf-8")
        gestor.cosechar(uno)
        gestor.cosechar(dos)

        fusion = gestor.fusionar()
        assert fusion.ok
        assert gestor.aplicar(fusion).aplicado
        assert (repo / "a.txt").read_text(encoding="utf-8") == "PRIMERA\ndos\nULTIMA\n"


def test_sin_cambios_de_nadie_no_hay_nada_que_aplicar(repo: Path, base_dir: Path) -> None:
    with _gestor(repo, base_dir) as gestor:
        gestor.cosechar(gestor.crear("uno"))
        fusion = gestor.fusionar()
        assert fusion.ok
        assert fusion.commit_integrado is None
        aplicacion = gestor.aplicar(fusion)
        assert aplicacion.aplicado
        assert aplicacion.archivos == ()
        assert _git(repo, "status", "--porcelain") == ""


# ---------------------------------------------------------------------------
# Fusión en conflicto: lo más importante de todo el módulo.
# ---------------------------------------------------------------------------


def test_conflicto_no_se_resuelve_solo_y_no_se_aplica_nada(
    repo: Path, base_dir: Path
) -> None:
    antes = (repo / "a.txt").read_text(encoding="utf-8")
    with _gestor(repo, base_dir) as gestor:
        uno = gestor.crear("uno")
        dos = gestor.crear("dos")
        # Los dos reescriben LA MISMA línea con cosas distintas.
        (uno.ruta / "a.txt").write_text("VERSION_DE_UNO\ndos\ntres\n", encoding="utf-8")
        (dos.ruta / "a.txt").write_text("VERSION_DE_DOS\ndos\ntres\n", encoding="utf-8")
        gestor.cosechar(uno)
        gestor.cosechar(dos)

        fusion = gestor.fusionar(["uno", "dos"])
        assert not fusion.ok
        assert fusion.commit_integrado is None
        assert [c.worktree_id for c in fusion.conflictos] == ["dos"]
        assert fusion.conflictos[0].archivos == ("a.txt",)
        assert "decidas tú" in fusion.resumen()

        # Nada tocado, ni siquiera el aporte que sí encajaba.
        assert (repo / "a.txt").read_text(encoding="utf-8") == antes
        aplicacion = gestor.aplicar(fusion)
        assert not aplicacion.aplicado
        assert "conflictos" in aplicacion.motivo
        assert (repo / "a.txt").read_text(encoding="utf-8") == antes


def test_en_conflicto_el_trabajo_de_cada_uno_queda_disponible_para_el_humano(
    repo: Path, base_dir: Path
) -> None:
    gestor = _gestor(repo, base_dir)
    try:
        uno = gestor.crear("uno")
        dos = gestor.crear("dos")
        (uno.ruta / "a.txt").write_text("DE_UNO\ndos\ntres\n", encoding="utf-8")
        (dos.ruta / "a.txt").write_text("DE_DOS\ndos\ntres\n", encoding="utf-8")
        gestor.cosechar(uno)
        gestor.cosechar(dos)
        fusion = gestor.fusionar(["uno", "dos"])
        assert not fusion.ok
        refs = fusion.refs_conservadas
        assert set(refs) >= {"uno", "dos", "base"}
    finally:
        gestor.cerrar()
    # Cerrar NO borra material que una persona todavía tiene que revisar.
    for ref in refs.values():
        assert _git(repo, "rev-parse", "--verify", ref)
    assert "DE_DOS" in _git(repo, "show", f"{refs['dos']}:a.txt")
    # Y los worktrees sí se fueron: lo que se conserva son commits, no carpetas.
    assert not any(base_dir.rglob("a.txt"))


def test_una_corrida_limpia_no_deja_basura_visible_en_el_repo(
    repo: Path, base_dir: Path
) -> None:
    # Lo que no puede quedar es lo que el dueño ve y le estorba: ramas, copias
    # del árbol, entradas de worktree. Los refs de andamiaje sí sobreviven, y a
    # propósito: viven fuera de ``refs/heads`` (ni ``git branch`` ni ``git
    # status`` los muestran) y son el único "antes" mientras alguien pueda
    # querer deshacer. Los recoge ``barrer_huerfanos`` por vejez.
    gestor = _gestor(repo, base_dir)
    uno = gestor.crear("uno")
    (uno.ruta / "a.txt").write_text("solo yo\n", encoding="utf-8")
    gestor.cosechar(uno)
    assert gestor.fusionar().ok
    gestor.cerrar()
    assert _git(repo, "branch", "--list") == "* main"
    assert _git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert not any(base_dir.rglob("a.txt"))


# ---------------------------------------------------------------------------
# El trabajo sin commitear del dueño.
# ---------------------------------------------------------------------------


def test_los_cambios_sin_commitear_del_dueno_viajan_al_worktree_y_sobreviven(
    repo: Path, base_dir: Path
) -> None:
    (repo / "a.txt").write_text("uno\ndos\ntres\nMIO_SIN_COMMITEAR\n", encoding="utf-8")
    with _gestor(repo, base_dir) as gestor:
        worktree = gestor.crear("uno")
        # El sub-agente NO trabaja sobre una versión vieja del archivo.
        assert "MIO_SIN_COMMITEAR" in (worktree.ruta / "a.txt").read_text(encoding="utf-8")
        (worktree.ruta / "a.txt").write_text(
            "uno\nDEL_AGENTE\ntres\nMIO_SIN_COMMITEAR\n", encoding="utf-8"
        )
        gestor.cosechar(worktree)
        fusion = gestor.fusionar()
        assert gestor.aplicar(fusion).aplicado
    final = (repo / "a.txt").read_text(encoding="utf-8")
    assert "DEL_AGENTE" in final
    assert "MIO_SIN_COMMITEAR" in final


def test_tomar_el_snapshot_no_toca_el_working_tree_ni_la_pila_de_stash(
    repo: Path, base_dir: Path
) -> None:
    (repo / "a.txt").write_text("sucio\n", encoding="utf-8")
    (repo / "sin_rastrear.txt").write_text("nuevo\n", encoding="utf-8")
    estado_antes = _git(repo, "status", "--porcelain")
    with _gestor(repo, base_dir) as gestor:
        worktree = gestor.crear("uno")
        assert _git(repo, "status", "--porcelain") == estado_antes
        assert (repo / "a.txt").read_text(encoding="utf-8") == "sucio\n"
        assert _git(repo, "stash", "list") == ""
        # Documentado y verificado: lo que no está rastreado NO viaja.
        assert not (worktree.ruta / "sin_rastrear.txt").exists()


def test_si_el_dueno_edita_mientras_los_agentes_trabajan_no_se_le_pisa_nada(
    repo: Path, base_dir: Path
) -> None:
    with _gestor(repo, base_dir) as gestor:
        worktree = gestor.crear("uno")
        (worktree.ruta / "a.txt").write_text("DEL_AGENTE\ndos\ntres\n", encoding="utf-8")
        gestor.cosechar(worktree)
        # El dueño toca la misma línea después de que se tomó el snapshot.
        (repo / "a.txt").write_text("LO_CAMBIE_YO\ndos\ntres\n", encoding="utf-8")
        aplicacion = gestor.aplicar(gestor.fusionar())
        assert not aplicacion.aplicado
        assert "no se aplicó nada" in aplicacion.motivo
        assert (repo / "a.txt").read_text(encoding="utf-8") == "LO_CAMBIE_YO\ndos\ntres\n"


# ---------------------------------------------------------------------------
# Limpieza garantizada.
# ---------------------------------------------------------------------------


def test_la_sesion_destruye_el_worktree_aunque_el_agente_reviente(
    repo: Path, base_dir: Path
) -> None:
    with _gestor(repo, base_dir) as gestor:
        ruta: Path | None = None
        with pytest.raises(RuntimeError, match="el agente reventó"):
            with gestor.sesion("uno") as worktree:
                ruta = worktree.ruta
                assert ruta.is_dir()
                raise RuntimeError("el agente reventó")
        assert ruta is not None
        assert not ruta.exists()
        assert str(ruta) not in _git(repo, "worktree", "list", "--porcelain")


def test_cerrar_barre_los_worktrees_que_quedaron_abiertos(
    repo: Path, base_dir: Path
) -> None:
    gestor = _gestor(repo, base_dir)
    rutas = [gestor.crear(f"w{i}").ruta for i in range(3)]
    gestor.cerrar()
    assert all(not ruta.exists() for ruta in rutas)
    assert _git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1
    gestor.cerrar()  # idempotente


def test_destruir_sobrevive_a_que_alguien_haya_borrado_la_carpeta_a_mano(
    repo: Path, base_dir: Path
) -> None:
    with _gestor(repo, base_dir) as gestor:
        worktree = gestor.crear("uno")
        shutil.rmtree(worktree.ruta)
        gestor.destruir(worktree)  # no debe lanzar
        assert str(worktree.ruta) not in _git(repo, "worktree", "list", "--porcelain")


# ---------------------------------------------------------------------------
# Cierre de Windows -- ``_pid_vivo`` en Windows NO puede usar
# ``os.kill(pid, 0)``: la propia documentación de Python advierte que en
# Windows eso llama a TerminateProcess() y MATA el proceso de verdad. Estos
# tests fuerzan ``os.name = "nt"`` y sustituyen ``ctypes.WinDLL`` (que en
# esta Mac ni siquiera existe -- ``raising=False``) para probar la LÓGICA de
# ``_pid_vivo_windows`` sin depender de Windows real. Se deja como red de
# seguridad explícita que ``os.kill`` JAMÁS se llama por esta rama.
# ---------------------------------------------------------------------------


def test_pid_vivo_en_windows_usa_openprocess_y_nunca_os_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ide_worktrees_module.os, "name", "nt")

    def _os_kill_no_deberia_llamarse(pid: int, sig: int) -> None:
        raise AssertionError(
            "en Windows _pid_vivo no debe llamar a os.kill -- TerminateProcess() mataría "
            "de verdad al proceso que solo había que consultar"
        )

    monkeypatch.setattr(ide_worktrees_module.os, "kill", _os_kill_no_deberia_llamarse)

    cerrados: list[int] = []

    class _Kernel32Falso:
        def OpenProcess(self, acceso: int, heredar: bool, pid: int) -> int:  # noqa: N802
            assert acceso == 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION
            assert heredar is False
            return 42  # handle "válido" cualquiera, distinto de cero

        def CloseHandle(self, handle: int) -> None:  # noqa: N802
            cerrados.append(handle)

    monkeypatch.setattr(
        ide_worktrees_module.ctypes,
        "WinDLL",
        lambda *args, **kwargs: _Kernel32Falso(),
        raising=False,
    )

    assert ide_worktrees_module._pid_vivo(4242) is True
    assert cerrados == [42]  # el handle abierto se cierra siempre


def test_pid_vivo_en_windows_pid_inexistente_devuelve_falso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El caso real que rompía antes del arreglo: un PID que no existe hacía
    que ``os.kill`` en Windows lanzara un ``OSError`` genérico (no
    ``ProcessLookupError``), y la rama ``except OSError: return True`` lo
    clasificaba como VIVO -- justo al revés de lo necesario para barrer
    huérfanos de verdad."""

    monkeypatch.setattr(ide_worktrees_module.os, "name", "nt")

    class _Kernel32Falso:
        def OpenProcess(self, acceso: int, heredar: bool, pid: int) -> int:  # noqa: N802
            return 0  # NULL: OpenProcess falló, el pid no existe

        def CloseHandle(self, handle: int) -> None:  # noqa: N802
            raise AssertionError("no debería cerrarse un handle que nunca se abrió")

    monkeypatch.setattr(
        ide_worktrees_module.ctypes,
        "WinDLL",
        lambda *args, **kwargs: _Kernel32Falso(),
        raising=False,
    )
    # ERROR_INVALID_PARAMETER (87): el caso normal de un pid que no existe.
    monkeypatch.setattr(ide_worktrees_module.ctypes, "get_last_error", lambda: 87, raising=False)

    assert ide_worktrees_module._pid_vivo(2**22) is False


def test_pid_vivo_en_windows_acceso_denegado_asume_vivo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mismo criterio conservador que la rama POSIX: si el proceso existe
    pero es de otro usuario (o está protegido), mejor no borrarle el
    trabajo a una corrida que quizás sigue viva."""

    monkeypatch.setattr(ide_worktrees_module.os, "name", "nt")

    class _Kernel32Falso:
        def OpenProcess(self, acceso: int, heredar: bool, pid: int) -> int:  # noqa: N802
            return 0

        def CloseHandle(self, handle: int) -> None:  # noqa: N802
            raise AssertionError("no debería cerrarse un handle que nunca se abrió")

    monkeypatch.setattr(
        ide_worktrees_module.ctypes,
        "WinDLL",
        lambda *args, **kwargs: _Kernel32Falso(),
        raising=False,
    )
    # ERROR_ACCESS_DENIED (5): el proceso existe, pero no se puede consultar.
    monkeypatch.setattr(ide_worktrees_module.ctypes, "get_last_error", lambda: 5, raising=False)

    assert ide_worktrees_module._pid_vivo(999) is True


def test_barrer_huerfanos_limpia_lo_que_dejo_un_proceso_muerto(
    repo: Path, base_dir: Path
) -> None:
    gestor = _gestor(repo, base_dir)
    worktree = gestor.crear("uno")
    (worktree.ruta / "a.txt").write_text("trabajo perdido\n", encoding="utf-8")
    gestor.cosechar(worktree)
    # Simula la muerte del proceso: nadie corre ``finally``, así que la
    # carpeta, la entrada en .git/worktrees y los refs quedan vivos.
    dir_corrida = worktree.ruta.parent
    del gestor
    assert worktree.ruta.is_dir()
    assert str(worktree.ruta) in _git(repo, "worktree", "list", "--porcelain")

    metadatos = json.loads((dir_corrida / "edecan-run.json").read_text(encoding="utf-8"))
    # Un pid que no existe. 2**22 está por encima del máximo de cualquier
    # sistema real, así que ``os.kill(pid, 0)`` no puede confundirse.
    metadatos["pid"] = 2**22
    (dir_corrida / "edecan-run.json").write_text(json.dumps(metadatos), encoding="utf-8")

    barridos = barrer_huerfanos(repo, base_dir=base_dir)
    assert barridos == [dir_corrida.name]
    assert not worktree.ruta.exists()
    assert not dir_corrida.exists()
    assert _git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert _git(repo, "for-each-ref", "--format=%(refname)", "refs/edecan") == ""


def test_barrer_huerfanos_no_borra_el_trabajo_que_falta_revisar(
    repo: Path, base_dir: Path
) -> None:
    # Reiniciar el companion (pid muerto) es lo normal, no una señal de que el
    # conflicto ya no importe: si el barrido se llevara esos refs, el trabajo
    # que se guardó "para que decidas tú" moriría antes de que nadie lo mirara.
    gestor = _gestor(repo, base_dir)
    uno = gestor.crear("uno")
    dos = gestor.crear("dos")
    (uno.ruta / "a.txt").write_text("DE_UNO\ndos\ntres\n", encoding="utf-8")
    (dos.ruta / "a.txt").write_text("DE_DOS\ndos\ntres\n", encoding="utf-8")
    gestor.cosechar(uno)
    gestor.cosechar(dos)
    fusion = gestor.fusionar(["uno", "dos"])
    assert not fusion.ok
    dir_corrida = uno.ruta.parent
    gestor.cerrar()

    metadatos = json.loads((dir_corrida / "edecan-run.json").read_text(encoding="utf-8"))
    metadatos["pid"] = 2**22  # el proceso que la creó ya no existe
    (dir_corrida / "edecan-run.json").write_text(json.dumps(metadatos), encoding="utf-8")

    assert barrer_huerfanos(repo, base_dir=base_dir) == []
    assert "DE_DOS" in _git(repo, "show", f"{fusion.refs_conservadas['dos']}:a.txt")

    # Pero no es para siempre: al envejecer sí se barre.
    barridos = barrer_huerfanos(
        repo, base_dir=base_dir, topes=TopesWorktrees(max_edad_huerfano_s=0.0)
    )
    assert barridos == [dir_corrida.name]
    assert _git(repo, "for-each-ref", "--format=%(refname)", "refs/edecan") == ""


def test_barrer_huerfanos_no_le_toca_nada_a_una_corrida_viva(
    repo: Path, base_dir: Path
) -> None:
    with _gestor(repo, base_dir) as gestor:
        worktree = gestor.crear("uno")
        # Otro proceso barriendo el mismo repo no puede llevarse por delante
        # el trabajo de esta corrida, que sigue con su pid vivo.
        assert barrer_huerfanos(repo, base_dir=base_dir) == []
        assert worktree.ruta.is_dir()
        assert (worktree.ruta / "a.txt").is_file()


def test_barrer_huerfanos_ignora_repos_sin_git(tmp_path: Path) -> None:
    suelta = tmp_path / "suelta"
    suelta.mkdir()
    assert barrer_huerfanos(suelta, base_dir=tmp_path / "wt") == []


# ---------------------------------------------------------------------------
# Integración con ide_equipo a través de envolver_runner.
# ---------------------------------------------------------------------------


def _subtarea(id_: str, ruta: str) -> Subtarea:
    return Subtarea(id=id_, titulo=f"Sub {id_}", instrucciones="trabaja", rutas=(ruta,))


async def test_envolver_runner_aisla_cada_subtarea_y_fusiona_al_final(
    repo: Path, base_dir: Path
) -> None:
    with _gestor(repo, base_dir) as gestor:

        async def runner(sub, control, worktree):  # type: ignore[no-untyped-def]
            destino = worktree.ruta / sub.rutas[0]
            destino.write_text(f"hecho por {sub.id}\n", encoding="utf-8")
            return f"{sub.id} listo"

        plan = construir_plan([_subtarea("uno", "a.txt"), _subtarea("dos", "b.txt")])
        equipo = EquipoDeAgentes(runner=envolver_runner(gestor, runner), max_concurrencia=2)
        resultado = await equipo.ejecutar(plan)
        assert resultado.exito_total

        fusion = gestor.fusionar()
        assert fusion.ok
        assert gestor.aplicar(fusion).aplicado
        assert (repo / "a.txt").read_text(encoding="utf-8") == "hecho por uno\n"
        assert (repo / "b.txt").read_text(encoding="utf-8") == "hecho por dos\n"
        # Las copias ya no están: el envoltorio las destruye al terminar.
        assert _git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1


async def test_una_subtarea_que_falla_no_deja_worktree_ni_aporta_codigo(
    repo: Path, base_dir: Path
) -> None:
    with _gestor(repo, base_dir) as gestor:
        rutas: dict[str, Path] = {}

        async def runner(sub, control, worktree):  # type: ignore[no-untyped-def]
            rutas[sub.id] = worktree.ruta
            (worktree.ruta / sub.rutas[0]).write_text(f"de {sub.id}\n", encoding="utf-8")
            if sub.id == "dos":
                raise RuntimeError("se cayó a la mitad")
            return "ok"

        plan = construir_plan([_subtarea("uno", "a.txt"), _subtarea("dos", "b.txt")])
        equipo = EquipoDeAgentes(runner=envolver_runner(gestor, runner), max_concurrencia=2)
        resultado = await equipo.ejecutar(plan)
        assert resultado.completadas == ["uno"]
        assert resultado.fallidas == ["dos"]

        assert all(not ruta.exists() for ruta in rutas.values())
        fusion = gestor.fusionar()
        # El trabajo a medias del que reventó no viaja de vuelta.
        assert fusion.aportaron == ("uno",)
        assert fusion.archivos == ("a.txt",)
        assert gestor.aplicar(fusion).aplicado
        assert (repo / "b.txt").read_text(encoding="utf-8") == "bes\n"


async def test_cancelar_a_la_mitad_no_deja_worktrees_huerfanos(
    repo: Path, base_dir: Path
) -> None:
    with _gestor(repo, base_dir) as gestor:
        arranco = asyncio.Event()
        rutas: dict[str, Path] = {}

        async def runner(sub, control, worktree):  # type: ignore[no-untyped-def]
            rutas[sub.id] = worktree.ruta
            arranco.set()
            await asyncio.sleep(30)  # se corta por cancelación, nunca termina
            return "no llega"

        plan = construir_plan([_subtarea("uno", "a.txt")])
        equipo = EquipoDeAgentes(runner=envolver_runner(gestor, runner), max_concurrencia=1)
        tarea = asyncio.create_task(equipo.ejecutar(plan))
        await asyncio.wait_for(arranco.wait(), timeout=5)
        equipo.cancelar_todo(forzado=True)
        resultado = await asyncio.wait_for(tarea, timeout=5)

        assert resultado.canceladas == ["uno"]
        assert not rutas["uno"].exists()
        assert _git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1


async def test_un_fallo_de_limpieza_no_tapa_el_error_real_del_agente(
    repo: Path, base_dir: Path
) -> None:
    # Lo que el dueño necesita ver es por qué falló SU tarea. Si la limpieza
    # (que corre en el ``finally``) dejara subir su propio error, sustituiría
    # esa causa por un "git tardó demasiado" y borraría la única pista útil.
    gestor = _gestor(repo, base_dir)
    try:

        async def runner(sub, control, worktree):  # type: ignore[no-untyped-def]
            # git deja de responder justo cuando toca limpiar: timeout real,
            # no simulado, sobre los subprocesos de ``destruir``.
            gestor.topes = TopesWorktrees(timeout_git_s=0.000001)
            raise RuntimeError("lo que de verdad falló")

        plan = construir_plan([_subtarea("uno", "a.txt")])
        equipo = EquipoDeAgentes(runner=envolver_runner(gestor, runner), max_concurrencia=1)
        resultado = await equipo.ejecutar(plan)
        assert resultado.fallidas == ["uno"]
        assert "lo que de verdad falló" in (resultado.estados["uno"].error or "")
    finally:
        gestor.topes = TopesWorktrees()
        gestor.cerrar()


# ---------------------------------------------------------------------------
# Que "no se aplicó nada" no signifique "no quedó nada". Estos casos son los
# que decidían, sin decírselo a nadie, que el trabajo de la corrida entera se
# fuera a la basura al salir del ``with``.
# ---------------------------------------------------------------------------


def test_si_aplicar_se_niega_el_trabajo_de_los_agentes_sobrevive_a_cerrar(
    repo: Path, base_dir: Path
) -> None:
    # El camino más común de todos: el dueño toca un archivo mientras los
    # sub-agentes trabajan. ``aplicar`` hace lo correcto y no le pisa nada,
    # pero si al cerrar se borraran los refs, lo que hicieron los sub-agentes
    # dejaría de existir: los worktrees ya no están, esos commits son la única
    # copia. Silencioso, irreversible y en el camino feliz.
    gestor = _gestor(repo, base_dir)
    try:
        uno = gestor.crear("uno")
        (uno.ruta / "a.txt").write_text("horas de trabajo del sub-agente\n", encoding="utf-8")
        gestor.cosechar(uno)
        (repo / "a.txt").write_text("lo cambie yo mientras tanto\n", encoding="utf-8")
        aplicacion = gestor.aplicar(gestor.fusionar())
        assert not aplicacion.aplicado
        assert aplicacion.refs["uno"]
    finally:
        gestor.cerrar()
    ref = aplicacion.refs["uno"]
    assert _git(repo, "rev-parse", "--verify", ref)
    assert "horas de trabajo del sub-agente" in _git(repo, "show", f"{ref}:a.txt")


def test_tras_aplicar_queda_como_deshacer_el_arbol_previo_del_dueno(
    repo: Path, base_dir: Path
) -> None:
    # ``aplicar`` escribe sobre el árbol del dueño, y ahí puede haber trabajo
    # suyo sin commitear: si el sub-agente reescribe ese archivo, el snapshot
    # ``base`` es el único registro de cómo estaba antes. Borrarlo al cerrar
    # dejaba la única copia en objetos sueltos, sin ningún ref que los nombre.
    (repo / "a.txt").write_text("MI TRABAJO SIN COMMITEAR\n", encoding="utf-8")
    gestor = _gestor(repo, base_dir)
    try:
        uno = gestor.crear("uno")
        (uno.ruta / "a.txt").write_text("lo reescribio entero el agente\n", encoding="utf-8")
        gestor.cosechar(uno)
        aplicacion = gestor.aplicar(gestor.fusionar())
        assert aplicacion.aplicado
    finally:
        gestor.cerrar()
    base = aplicacion.refs["base"]
    assert "MI TRABAJO SIN COMMITEAR" in _git(repo, "show", f"{base}:a.txt")
    # Y el deshacer que se le ofrece funciona de verdad.
    _git(repo, "checkout", base, "--", "a.txt")
    assert (repo / "a.txt").read_text(encoding="utf-8") == "MI TRABAJO SIN COMMITEAR\n"


def test_quien_no_quiera_dejar_nada_lo_pide_explicito(repo: Path, base_dir: Path) -> None:
    gestor = _gestor(repo, base_dir)
    uno = gestor.crear("uno")
    (uno.ruta / "a.txt").write_text("algo\n", encoding="utf-8")
    gestor.cosechar(uno)
    gestor.aplicar(gestor.fusionar())
    gestor.cerrar(conservar_refs=False)
    assert _git(repo, "for-each-ref", "--format=%(refname)", "refs/edecan") == ""


# ---------------------------------------------------------------------------
# La limpieza no puede alcanzar worktrees que no son nuestros.
# ---------------------------------------------------------------------------


def test_la_limpieza_no_toca_los_worktrees_propios_del_dueno(
    repo: Path, base_dir: Path, tmp_path: Path
) -> None:
    # ``git worktree prune`` es global y, sin ``--expire``, no perdona: barre
    # toda entrada cuya carpeta no exista EN ESE INSTANTE. Un worktree del dueño
    # en un volumen desmontado (o una carpeta sincronizada desalojada) queda
    # desconectado del repo -- ``fatal: not a git repository`` -- y pierde su
    # índice, su HEAD y sus reflogs. Nuestra limpieza no tiene por qué llegar
    # ahí.
    suyo = tmp_path / "volumen" / "mi-rama"
    suyo.parent.mkdir()
    _git(repo, "worktree", "add", "--quiet", "-b", "mi-rama", str(suyo))
    (suyo / "sin_commitear.txt").write_text("trabajo del dueño\n", encoding="utf-8")
    (tmp_path / "volumen").rename(tmp_path / "volumen-desmontado")

    with _gestor(repo, base_dir) as gestor:
        gestor.destruir(gestor.crear("uno"))

    (tmp_path / "volumen-desmontado").rename(tmp_path / "volumen")
    assert _git(suyo, "status", "--porcelain") == "?? sin_commitear.txt"
    assert str(suyo) in _git(repo, "worktree", "list", "--porcelain")


def test_la_limpieza_si_deja_consistente_lo_nuestro(repo: Path, base_dir: Path) -> None:
    # La contracara: acotar la poda no puede significar dejar entradas muertas
    # nuestras en ``.git/worktrees``.
    with _gestor(repo, base_dir) as gestor:
        worktree = gestor.crear("uno")
        shutil.rmtree(worktree.ruta)  # git ya no puede removerlo por su cuenta
        gestor.destruir(worktree)
        assert str(worktree.ruta) not in _git(repo, "worktree", "list", "--porcelain")


def test_barrer_no_le_arranca_los_worktrees_a_una_corrida_sin_metadatos(
    repo: Path, base_dir: Path
) -> None:
    # Con dos ventanas de Edecán sobre el mismo repo, una barre mientras la
    # otra arranca. Si el archivo de metadatos todavía no se escribió (o quedó
    # a medias), no se sabe de quién es la corrida: decidir por un pid
    # inexistente -- que responde "muerto" -- le arrancaría los worktrees a
    # sub-agentes que están escribiendo en ellos ahora mismo.
    with _gestor(repo, base_dir) as gestor:
        worktree = gestor.crear("uno")
        (worktree.ruta / "a.txt").write_text("trabajo en curso\n", encoding="utf-8")
        (worktree.ruta.parent / "edecan-run.json").write_text("{ roto", encoding="utf-8")

        assert barrer_huerfanos(repo, base_dir=base_dir) == []
        assert (worktree.ruta / "a.txt").read_text(encoding="utf-8") == "trabajo en curso\n"
        assert str(worktree.ruta) in _git(repo, "worktree", "list", "--porcelain")
