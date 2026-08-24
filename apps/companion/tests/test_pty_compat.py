"""Suite de contrato para la capa de terminal portable (``pty_compat``).

El plan (``docs/edecan-windows.md`` §1.3) pide una suite compartida, sin ramas
por plataforma en los ASSERTS: abrir, escribir/leer con eco, redimensionar y que
el hijo vea el tamaño nuevo, que ``\\x03`` interrumpa un hijo dormido, y que
``cerrar()``/``matar_arbol()`` no dejen descendientes vivos aunque el hijo haya
lanzado un nieto.

Las dos ramas de ``edecan_companion/pty_compat.py`` (POSIX y Windows/ConPTY vía
``pywinpty``) ya están escritas. Esta suite corre de verdad en macOS/Linux en
cada ``uv run pytest`` de este repo -- ejercita ``_PosixPTY``, ver
``pty_compat.py``. Contra ``_WindowsPTY`` corre exactamente igual (mismas
funciones, sin cambiar un carácter) cuando se ejecuta en el job de CI de
Windows o en la PC del dueño -- ES la evidencia que le falta a esa rama, y
hasta que no corra ahí, nada de lo que ``_WindowsPTY`` hace se declara
"funciona" (regla del plan, docs/edecan-windows.md, "Regla de evidencia").

La única rama por plataforma que existe en los tests de contrato (los que
llaman a ``abrir_pty``) es en ``_arbol_vivo``, y es sobre CÓMO se verifica un
hecho (qué comando preguntar), no sobre qué se espera que pase — el propio
plan lo autoriza explícitamente para este caso puntual (§1.3: "en Windows se
verifica con taskkill... en POSIX con os.kill").

Al final del archivo hay dos grupos más:
- Un test de ``_comando_taskkill`` sin ningún ``skipif``: es construcción de
  argv, Python puro sin syscalls, así que se puede verificar desde cualquier
  plataforma sin tocar ConPTY -- no es el mock que el plan prohíbe.
- Tests marcados ``@pytest.mark.skipif(sys.platform != "win32")`` para lo que
  es exclusivo de la rama Windows (el ``PYTHONUTF8``/``PYTHONIOENCODING`` que
  ``_WindowsPTY.abrir`` agrega al entorno del hijo) y que por eso no encaja en
  la suite de contrato compartida de arriba.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import time

import pytest
from edecan_companion.pty_compat import PTYError, abrir_pty

_PYTHON = sys.executable


def _env_sin_tamano_heredado() -> dict[str, str]:
    """Copia del entorno actual sin ``COLUMNS``/``LINES``.

    ``shutil.get_terminal_size()`` en el hijo mira primero esas variables de
    entorno antes de preguntarle al tty real; si el shell que corre pytest las
    dejó fijas, el test de ``redimensionar`` estaría verificando el entorno
    heredado en vez de la llamada ``TIOCSWINSZ`` real.
    """

    env = dict(os.environ)
    env.pop("COLUMNS", None)
    env.pop("LINES", None)
    return env


def _leer_hasta(term, *, contiene: bytes, timeout: float = 5.0) -> bytes:
    """Acumula ``leer()`` hasta ver ``contiene`` en lo leído o agotar el plazo."""

    acumulado = b""
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        chunk = term.leer(4096)
        if chunk:
            acumulado += chunk
            if contiene in acumulado:
                return acumulado
        else:
            time.sleep(0.02)
    return acumulado


def _esperar(condicion, *, timeout: float = 5.0, intervalo: float = 0.05) -> bool:
    """Sondea ``condicion()`` hasta que sea verdad o expire ``timeout``."""

    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        if condicion():
            return True
        time.sleep(intervalo)
    return condicion()


def _arbol_vivo(pid: int) -> bool:
    """¿Sigue vivo el proceso ``pid``? Rama de verificación, no de la capa
    bajo prueba (ver docstring del módulo)."""

    if os.name == "nt":  # pragma: no cover - esta rama solo corre en Windows real.
        resultado = subprocess.run(
            ["taskkill", "/PID", str(pid)],
            capture_output=True,
            check=False,
        )
        return resultado.returncode == 0
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.fixture
def cwd(tmp_path) -> str:
    return str(tmp_path)


def test_abrir_escribir_y_leer_hace_eco(cwd):
    term = abrir_pty(
        [_PYTHON, "-u", "-c", "import sys; sys.stdout.write(sys.stdin.readline())"],
        cwd=cwd,
        env=_env_sin_tamano_heredado(),
    )
    try:
        term.escribir(b"hola edecan\n")
        salida = _leer_hasta(term, contiene=b"hola edecan")
        assert b"hola edecan" in salida
        assert _esperar(lambda: term.codigo_salida is not None)
        assert term.codigo_salida == 0
    finally:
        term.cerrar()


def test_leer_devuelve_vacio_en_eof(cwd):
    term = abrir_pty([_PYTHON, "-c", "pass"], cwd=cwd, env=_env_sin_tamano_heredado())
    try:
        assert _esperar(lambda: term.codigo_salida is not None)
        # Una vez terminado el proceso, leer() tiene que terminar en b"" -- ese
        # es el EOF que el hilo lector de ide_sessions usa para cerrar la sesión.
        vio_eof = False
        for _ in range(200):
            if term.leer(4096) == b"":
                vio_eof = True
                break
        assert vio_eof
    finally:
        term.cerrar()


def test_redimensionar_lo_ve_el_proceso_hijo(cwd):
    script = (
        "import shutil, time\n"
        "time.sleep(0.5)\n"
        "tam = shutil.get_terminal_size()\n"
        "print(f'{tam.columns}x{tam.lines}', flush=True)\n"
    )
    term = abrir_pty(
        [_PYTHON, "-u", "-c", script],
        cwd=cwd,
        env=_env_sin_tamano_heredado(),
        cols=100,
        rows=40,
    )
    try:
        term.redimensionar(77, 24)
        salida = _leer_hasta(term, contiene=b"77x24")
        assert b"77x24" in salida
    finally:
        term.cerrar()


def test_ctrl_c_interrumpe_al_hijo(cwd):
    script = (
        "import time\n"
        "print('listo', flush=True)\n"
        "try:\n"
        "    time.sleep(30)\n"
        "except KeyboardInterrupt:\n"
        "    print('interrumpido', flush=True)\n"
    )
    term = abrir_pty([_PYTHON, "-u", "-c", script], cwd=cwd, env=_env_sin_tamano_heredado())
    try:
        assert b"listo" in _leer_hasta(term, contiene=b"listo")
        term.escribir(b"\x03")
        salida = _leer_hasta(term, contiene=b"interrumpido")
        assert b"interrumpido" in salida
        assert _esperar(lambda: term.codigo_salida is not None)
    finally:
        term.cerrar()


def test_cerrar_mata_el_arbol_completo_no_solo_la_raiz(cwd):
    # El hijo lanza un nieto que duerme mucho: cerrar() tiene que matar el
    # ÁRBOL entero, no solo el proceso raíz -- exactamente el bug que hoy
    # existe en la rama Windows de ide_sessions.py (ver docs/edecan-windows.md
    # §0, "Matar procesos").
    script = (
        "import subprocess, sys, time\n"
        "nieto = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(nieto.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    term = abrir_pty([_PYTHON, "-u", "-c", script], cwd=cwd, env=_env_sin_tamano_heredado())
    try:
        salida = _leer_hasta(term, contiene=b"\n")
        pid_nieto = int(salida.strip().splitlines()[0])
        assert _arbol_vivo(pid_nieto), "el nieto debía estar vivo antes de cerrar()"
        term.cerrar()
        assert _esperar(lambda: not _arbol_vivo(pid_nieto)), "el nieto siguió vivo tras cerrar()"
    finally:
        with contextlib.suppress(Exception):
            term.matar_arbol()


def test_matar_arbol_es_el_martillo_y_es_idempotente(cwd):
    script = (
        "import subprocess, sys, time\n"
        "nieto = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(nieto.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    term = abrir_pty([_PYTHON, "-u", "-c", script], cwd=cwd, env=_env_sin_tamano_heredado())
    salida = _leer_hasta(term, contiene=b"\n")
    pid_nieto = int(salida.strip().splitlines()[0])
    assert _arbol_vivo(pid_nieto)

    term.matar_arbol()
    assert _esperar(lambda: not _arbol_vivo(pid_nieto))
    assert term.codigo_salida is not None

    # Llamarlo de nuevo sobre un árbol ya muerto no debe lanzar.
    term.matar_arbol()


def test_pid_y_codigo_salida(cwd):
    term = abrir_pty(
        [_PYTHON, "-c", "import sys; sys.exit(7)"],
        cwd=cwd,
        env=_env_sin_tamano_heredado(),
    )
    try:
        assert isinstance(term.pid, int) and term.pid > 0
        assert _esperar(lambda: term.codigo_salida is not None)
        assert term.codigo_salida == 7
    finally:
        term.cerrar()


def test_escribir_exige_bytes_no_str(cwd):
    term = abrir_pty([_PYTHON, "-c", "pass"], cwd=cwd, env=_env_sin_tamano_heredado())
    try:
        with pytest.raises(TypeError):
            term.escribir("no son bytes")  # type: ignore[arg-type]
    finally:
        term.cerrar()


def test_abrir_con_ejecutable_inexistente_lanza_pty_error(cwd):
    with pytest.raises(PTYError):
        abrir_pty(
            ["/ruta/que/no/existe/en/ningun/lado"],
            cwd=cwd,
            env=_env_sin_tamano_heredado(),
        )


# ---------------------------------------------------------------------------
# _comando_taskkill: construcción de argv, sin syscalls -- corre en cualquier
# plataforma, no es un mock de ConPTY (ver docstring del módulo).
# ---------------------------------------------------------------------------


def test_comando_taskkill_cooperativo_no_lleva_f():
    from edecan_companion.pty_compat import _comando_taskkill

    assert _comando_taskkill(4321, force=False) == ["taskkill", "/T", "/PID", "4321"]


def test_comando_taskkill_forzado_lleva_f_antes_de_t():
    from edecan_companion.pty_compat import _comando_taskkill

    # ``/T`` (árbol) y ``/F`` (forzado) tienen que estar los dos: el orden
    # entre ellos no importa para taskkill, pero el PID sí tiene que quedar
    # último y acompañado de su propia bandera ``/PID``.
    comando = _comando_taskkill(4321, force=True)
    assert set(comando[:-2]) == {"taskkill", "/T", "/F"}
    assert comando[-2:] == ["/PID", "4321"]


def test_comando_taskkill_es_idempotente_en_su_propia_construccion():
    # Llamarla dos veces con el mismo pid da el mismo resultado -- no hay
    # estado oculto que dependa de cuántas veces se invoque (la idempotencia
    # real de matar un árbol ya muerto la prueba
    # test_matar_arbol_es_el_martillo_y_es_idempotente, que sí toca el
    # proceso real).
    from edecan_companion.pty_compat import _comando_taskkill

    assert _comando_taskkill(99, force=True) == _comando_taskkill(99, force=True)


# ---------------------------------------------------------------------------
# Rama Windows exclusiva: lo que no encaja en la suite de contrato compartida
# de arriba porque es un detalle propio de ``_WindowsPTY.abrir`` (el entorno
# que le agrega al hijo), no del contrato ``TerminalPTY`` en sí. Ejercita
# ConPTY de verdad -- nada mockeado -- así que solo corre en Windows real.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="ConPTY solo existe en Windows.")
def test_windows_abrir_fija_pythonutf8_en_el_entorno_del_hijo(cwd):
    # docs/edecan-windows.md §1.2 "Codificación": _WindowsPTY.abrir agrega
    # PYTHONUTF8=1 / PYTHONIOENCODING=utf-8 al entorno del hijo cuando el
    # llamador no los trae ya puestos, para que un hijo Python no escriba con
    # la página de códigos nativa del sistema.
    script = (
        "import os\n"
        "print(os.environ.get('PYTHONUTF8'), os.environ.get('PYTHONIOENCODING'), flush=True)\n"
    )
    env = _env_sin_tamano_heredado()
    env.pop("PYTHONUTF8", None)
    env.pop("PYTHONIOENCODING", None)
    term = abrir_pty([_PYTHON, "-u", "-c", script], cwd=cwd, env=env)
    try:
        salida = _leer_hasta(term, contiene=b"utf-8")
        assert b"1 utf-8" in salida
    finally:
        term.cerrar()


@pytest.mark.skipif(sys.platform != "win32", reason="ConPTY solo existe en Windows.")
def test_windows_abrir_respeta_pythonutf8_ya_puesto_por_el_llamador(cwd):
    # setdefault(), no una asignación directa: si ide_sessions.py (o quien
    # llame a abrir_pty) ya trae un valor propio, esta capa no lo pisa.
    script = "import os\nprint(os.environ.get('PYTHONIOENCODING'), flush=True)\n"
    env = _env_sin_tamano_heredado()
    env["PYTHONIOENCODING"] = "cp1252"
    term = abrir_pty([_PYTHON, "-u", "-c", script], cwd=cwd, env=env)
    try:
        salida = _leer_hasta(term, contiene=b"cp1252")
        assert b"cp1252" in salida
    finally:
        term.cerrar()
