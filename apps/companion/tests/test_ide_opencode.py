"""Tests de ``ide_opencode.py`` -- incluye una integración REAL contra opencode.

Por encargo explícito: "si no lo puedes escribir, no has terminado". El test
``test_prompt_real_modifica_archivo_en_disco`` arranca un ``opencode serve``
de verdad, crea una sesión de verdad, le pide que reescriba un archivo, y
comprueba en disco (hash antes/después) que el cambio ocurrió -- nada de
dobles. Se salta solo si faltan credenciales de Cloudflare (regla del
encargo); en esta Mac SÍ están, así que corre y tiene que pasar de verdad.

Los demás tests son más baratos: arrancan el servidor real (no hay manera
honesta de evitarlo -- es lo que se está probando) pero no gastan tokens de
proveedor, solo ejercitan el ciclo de vida y el manejo de errores.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
from collections import deque
from pathlib import Path

import httpx
import pytest
from edecan_companion.ide_opencode import (
    BinarioOpencodeNoEncontrado,
    ErrorOpencode,
    ServidorOpencode,
    _argv_y_flags_lanzamiento,
)
from edecan_companion.pty_compat import _comando_taskkill

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- #
# Credenciales -- se leen, nunca se imprimen (ver MEMORY.md de la migración:
# el .env está en .gitignore y el token no debe volcarse a ningún lado).
# --------------------------------------------------------------------------- #


def _leer_env_raiz(nombre: str) -> str | None:
    """Lee UNA variable del ``.env`` de la raíz del monorepo, sin volcar el
    archivo entero. Devuelve ``None`` si el archivo o la variable no están."""

    ruta = Path(__file__).resolve().parents[3] / ".env"
    if not ruta.is_file():
        return None
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        if linea.startswith(f"{nombre}="):
            return linea.split("=", 1)[1].strip()
    return None


_CUENTA_CLOUDFLARE = os.environ.get("CLOUDFLARE_ACCOUNT_ID") or _leer_env_raiz(
    "CLOUDFLARE_ACCOUNT_ID"
)
_TOKEN_CLOUDFLARE = os.environ.get("CLOUDFLARE_API_TOKEN") or _leer_env_raiz("CLOUDFLARE_API_TOKEN")

requiere_credenciales = pytest.mark.skipif(
    not (_CUENTA_CLOUDFLARE and _TOKEN_CLOUDFLARE),
    reason=(
        "Sin CLOUDFLARE_ACCOUNT_ID/CLOUDFLARE_API_TOKEN en el entorno o en "
        "el .env de la raíz no hay proveedor real contra el que mandar un "
        "prompt -- ver regla del encargo: se salta, no se simula."
    ),
)

_MODELO_PRUEBA = "@cf/moonshotai/kimi-k2.7-code"


def _workspace_con_proveedor(tmp_path: Path) -> Path:
    """Workspace TEMPORAL propio (nunca un repo del dueño, regla 7) con un
    ``opencode.json`` que registra Workers AI -- exactamente lo que
    ``ServidorOpencode.iniciar`` necesita ver en su ``cwd`` para que el
    proveedor ``workersai`` exista (ver docstring del módulo, punto 2)."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            "workersai": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Cloudflare Workers AI",
                "options": {
                    "baseURL": f"https://api.cloudflare.com/client/v4/accounts/{_CUENTA_CLOUDFLARE}/ai/v1",
                    "apiKey": _TOKEN_CLOUDFLARE,
                },
                "models": {_MODELO_PRUEBA: {"name": "Kimi K2.7 Code"}},
            }
        },
    }
    (workspace / "opencode.json").write_text(json.dumps(config), encoding="utf-8")
    return workspace


# --------------------------------------------------------------------------- #
# Ciclo de vida y errores -- no gastan tokens de proveedor
# --------------------------------------------------------------------------- #


async def test_binario_inexistente_da_error_claro_no_cuelga(tmp_path: Path) -> None:
    """Regla 1: si el binario no está, error claro -- no un cuelgue ni un
    fingido éxito."""

    with pytest.raises(BinarioOpencodeNoEncontrado, match="no es un ejecutable válido"):
        await ServidorOpencode.iniciar(tmp_path, ruta_binario=tmp_path / "no-existe-opencode")


async def test_directorio_de_trabajo_inexistente_da_error_claro(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no existe"):
        await ServidorOpencode.iniciar(tmp_path / "carpeta-que-no-existe")


async def test_arranca_y_para_limpio_con_puerto_libre_real(tmp_path: Path) -> None:
    """El servidor arranca de verdad, contesta /health por debajo, usa un
    puerto que el propio sistema operativo eligió (nunca uno fijo -- regla
    del encargo), y se apaga sin dejar el proceso colgado."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    servidor = await ServidorOpencode.iniciar(workspace, timeout_arranque=30.0)
    try:
        assert servidor.puerto > 0
        # Puerto real de un servidor vivo: dos arranques sucesivos NO deberían
        # coincidir salvo coincidencia astronómica -- lo relevante es que no
        # se pasó ningún puerto fijo por parámetro (la firma de iniciar() ni
        # lo acepta).
        # ``directorio`` filtra: opencode guarda sesiones en un almacén GLOBAL
        # compartido por toda la máquina (se comprobó -- sin filtro aparecen
        # sesiones de corridas anteriores en otros workspaces), así que sin
        # filtrar esto no dice nada sobre ESTE servidor/workspace.
        pagina = await servidor.listar_sesiones(directorio=str(workspace))
        assert pagina.items == []  # este workspace es nuevo: cero sesiones propias
    finally:
        await servidor.detener()
    assert servidor._proceso.returncode is not None  # el subproceso quedó terminado de verdad


async def test_error_de_opencode_se_propaga_con_mensaje_real(tmp_path: Path) -> None:
    """Regla 6: un fallo de opencode (sesión inexistente) llega con el
    ``_tag``/``message`` reales de la API, no un error genérico inventado
    por este adaptador."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    async with await ServidorOpencode.iniciar(workspace, timeout_arranque=30.0) as servidor:
        with pytest.raises(ErrorOpencode) as exc_info:
            await servidor.obtener_sesion("ses_no_existe_de_verdad")
        error = exc_info.value
        assert error.status == 404
        assert error.tag == "SessionNotFoundError"
        assert "ses_no_existe_de_verdad" in str(error)


async def test_crear_sesion_sin_proveedor_o_sin_modelo_es_error_de_validacion(
    tmp_path: Path,
) -> None:
    """``ModelRef`` de opencode exige providerID + id juntos -- este adaptador
    lo valida ANTES de llamar a la red, con un mensaje que explica por qué."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    async with await ServidorOpencode.iniciar(workspace, timeout_arranque=30.0) as servidor:
        with pytest.raises(ValueError, match="proveedor y modelo van juntos"):
            await servidor.crear_sesion(proveedor="workersai")


# --------------------------------------------------------------------------- #
# Ciclo de vida en Windows -- lógica pura vía monkeypatch de ``os.name``.
#
# ADVERTENCIA HONESTA (regla 6, "nada simulado"): esta suite corre en macOS.
# Estos tests NO ejecutan opencode.exe/opencode.cmd real ni un taskkill real
# -- ``_argv_y_flags_lanzamiento`` se prueba como función pura (nada de
# filesystem ni subproceso) y ``detener()`` se prueba interceptando
# ``asyncio.create_subprocess_exec`` para comprobar el argv de ``taskkill``
# que ARMARÍA en Windows, sin depender de que este Mac sepa correr nada de
# eso. La prueba de verdad -- que ``opencode serve`` arranque sin consola
# negra y que ``detener()`` deje cero procesos huérfanos -- es la de la fase
# de validación contra la VM Windows real (ver MEMORY.md del encargo).
#
# Nota aparte sobre ``os.name`` y ``pathlib``: ``Path(...)`` decide su propia
# subclase (``PosixPath``/``WindowsPath``) mirando ``os.name`` en CADA
# construcción -- por eso los tests de ``_argv_y_flags_lanzamiento`` de abajo
# NUNCA reconstruyen un ``Path`` bajo ``os.name`` fingido (usan uno ya
# construido con ``tmp_path /`` ANTES del monkeypatch); ver el docstring de
# esa función para el porqué completo.
# --------------------------------------------------------------------------- #


class _ProcesoFalso:
    """Doble mínimo de ``asyncio.subprocess.Process`` -- sin stdout/stderr
    reales (``_leer`` de ``iniciar()`` los tolera como ``None``) y sin
    arrancar nada de verdad."""

    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.stdout = None
        self.stderr = None

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        while self.returncode is None:
            await asyncio.sleep(0.01)
        return self.returncode


async def test_argv_y_flags_lanzamiento_en_windows_envuelve_shim_cmd_y_pide_sin_consola(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Precedente del ``.cmd`` (ver docstring de ``_argv_y_flags_lanzamiento``
    y ``packages/mcp/edecan_mcp/transport.py``): si opencode se resolvió
    como un shim de npm (``opencode.cmd``), el argv que llegaría a
    ``create_subprocess_exec`` tiene que estar envuelto en ``cmd.exe /c`` --
    y en Windows nunca se pide ``start_new_session`` (no existe ahí) y SÍ se
    pide ``CREATE_NO_WINDOW`` (si no, cada arranque parpadea una consola
    negra).

    Sin ``pytest.mark.asyncio``: ``_argv_y_flags_lanzamiento`` es una
    función síncrona pura -- no hace falta un event loop para probarla
    (y evita a propósito el problema de mezclar ``os.name`` fingido con
    ``Path.resolve()``/``_ubicar_binario`` real, ver el docstring de la
    función bajo prueba)."""

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")

    binario = tmp_path / "opencode.cmd"

    argv, flags = _argv_y_flags_lanzamiento(binario)

    assert argv == [r"C:\Windows\System32\cmd.exe", "/c", str(binario), "serve", "--port", "0"]
    assert flags["start_new_session"] is False
    assert flags["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)


async def test_argv_y_flags_lanzamiento_en_windows_con_exe_nativo_no_envuelve_en_cmd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un ``opencode.exe`` nativo (el otro empaquetado posible en Windows,
    ver ``docs/opencode-empaquetado.md``) se lanza directo -- envolverlo en
    ``cmd.exe`` sería innecesario y ``_argv_para_ejecutar`` no lo hace."""

    monkeypatch.setattr(os, "name", "nt")

    binario = tmp_path / "opencode.exe"

    argv, flags = _argv_y_flags_lanzamiento(binario)

    assert argv == [str(binario), "serve", "--port", "0"]
    assert flags["start_new_session"] is False
    assert flags["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)


async def test_argv_y_flags_lanzamiento_en_posix_no_envuelve_y_usa_grupo_de_proceso(
    tmp_path: Path,
) -> None:
    """Contrato en la plataforma que SÍ corre esta suite de verdad: sin
    envolver, y con ``start_new_session=True`` (necesario para que
    ``os.killpg`` en :meth:`ServidorOpencode.detener` alcance el árbol)."""

    binario = tmp_path / "opencode"

    argv, flags = _argv_y_flags_lanzamiento(binario)

    assert argv == [str(binario), "serve", "--port", "0"]
    assert flags["start_new_session"] is True
    assert flags["creationflags"] == 0


def _servidor_falso_para_detener(
    monkeypatch: pytest.MonkeyPatch, proceso: _ProcesoFalso
) -> ServidorOpencode:
    """Arma un ``ServidorOpencode`` sin pasar por ``iniciar()`` -- construye
    la instancia directo con un ``_ProcesoFalso`` para probar ``detener()``
    aislado, sin arrancar nada real."""

    monkeypatch.setattr(os, "name", "nt")
    return ServidorOpencode(
        proceso=proceso,  # type: ignore[arg-type]
        puerto=0,
        cliente=httpx.AsyncClient(),
        directorio_trabajo=Path("."),
        ruta_binario=Path("opencode.exe"),
        buffer_salida=deque(maxlen=10),
        tareas_lectura=[],
    )


async def test_detener_en_windows_usa_taskkill_cooperativo_y_no_escala_si_alcanza(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hallazgo real medido en la VM (ver MEMORY.md): ``Process.terminate()``
    en Windows solo mata el PID raíz que asyncio rastrea, dejando huérfano
    cualquier nieto (p. ej. el ``opencode.exe`` real detrás de un shim
    ``opencode.cmd``). Este test comprueba que ``detener()`` llama
    ``taskkill /T`` (SIN ``/F`` -- fase cooperativa, igual que SIGTERM en
    POSIX) y, si eso ya deja el proceso muerto, NO escala al martillo."""

    proceso = _ProcesoFalso(pid=9999)
    servidor = _servidor_falso_para_detener(monkeypatch, proceso)

    llamadas: list[tuple[tuple, dict]] = []

    async def _create_subprocess_exec_falso(*args: object, **kwargs: object) -> object:
        llamadas.append((args, kwargs))
        proceso.returncode = 0  # taskkill "mató" el árbol real en el primer intento

        class _TaskkillFalso:
            async def wait(self) -> int:
                return 0

        return _TaskkillFalso()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _create_subprocess_exec_falso)

    await servidor.detener(timeout=2.0)

    assert len(llamadas) == 1  # nunca escaló al martillo -- no hizo falta
    args, kwargs = llamadas[0]
    assert tuple(args) == tuple(_comando_taskkill(9999, force=False))
    assert "/F" not in args
    assert kwargs["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)


async def test_detener_en_windows_escala_a_taskkill_forzado_si_no_muere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si la fase cooperativa (``taskkill /T`` sin ``/F``) no logró matar el
    proceso a tiempo, ``detener()`` tiene que escalar al martillo
    (``taskkill /T /F``) -- mismo patrón de dos fases que SIGTERM->SIGKILL
    en POSIX, con ``pty_compat._comando_taskkill`` como única fuente de
    verdad de cómo se arma cada comando."""

    proceso = _ProcesoFalso(pid=7777)
    servidor = _servidor_falso_para_detener(monkeypatch, proceso)

    llamadas: list[tuple[tuple, dict]] = []

    async def _create_subprocess_exec_falso(*args: object, **kwargs: object) -> object:
        llamadas.append((args, kwargs))
        if "/F" in args:
            proceso.returncode = 0  # solo el martillo lo mata de verdad

        class _TaskkillFalso:
            async def wait(self) -> int:
                return 0

        return _TaskkillFalso()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _create_subprocess_exec_falso)

    # timeout corto: la fase cooperativa nunca mata al proceso falso (arriba
    # no se le puso "/F" a esa llamada), así que tiene que agotar el
    # timeout y escalar -- se deja bajo para no alargar la suite.
    await servidor.detener(timeout=0.05)

    assert len(llamadas) == 2
    args_cooperativo, _ = llamadas[0]
    args_forzado, _ = llamadas[1]
    assert "/F" not in args_cooperativo
    assert "/F" in args_forzado
    assert tuple(args_cooperativo) == tuple(_comando_taskkill(7777, force=False))
    assert tuple(args_forzado) == tuple(_comando_taskkill(7777, force=True))


# --------------------------------------------------------------------------- #
# La prueba obligatoria: escribe de verdad en disco
# --------------------------------------------------------------------------- #


@requiere_credenciales
async def test_prompt_real_modifica_archivo_en_disco(tmp_path: Path) -> None:
    """La razón de ser de esta migración, verificada por este test:

    1. Arranca opencode de verdad, con Workers AI configurado de verdad.
    2. Crea un archivo con contenido conocido y calcula su hash.
    3. Manda un prompt real pidiendo una modificación puntual y verificable.
    4. Sigue los eventos SSE reales hasta ver la sesión terminar un paso.
    5. Comprueba EN DISCO -- no en lo que diga el modelo -- que el archivo
       cambió: hash distinto y la línea pedida presente.

    Si esto no pasa, opencode no sirve de motor y hay que saberlo aquí, no
    en producción.
    """

    workspace = _workspace_con_proveedor(tmp_path)
    archivo = workspace / "README.md"
    contenido_original = "Hola\nLinea 2\nLinea 3\n"
    archivo.write_text(contenido_original, encoding="utf-8")
    hash_antes = hashlib.sha256(archivo.read_bytes()).hexdigest()

    marca = "LINEA-DE-PRUEBA-IDE-OPENCODE-42"
    herramientas_escritura = {"write", "edit", "patch", "apply_patch"}

    # Reintentos DELIBERADOS: contra un modelo real (no un doble) se
    # comprobó en esta misma migración que, con idéntico prompt, unas veces
    # kimi-k2.7-code llama a sus herramientas de edición y otras solo
    # contesta con texto sin tocar nada -- es la misma variabilidad de
    # tool-calling que ya deja documentada `config/modelos.yml`
    # (`chat_rapido`, "Medición 2": corridas de 3 en 3 sobre el mismo caso
    # porque un modelo real no es determinista). Este test empuja hasta
    # 3 veces con un prompt cada vez más directo ANTES de declarar que
    # opencode no escribió -- eso sigue siendo una prueba real: cada
    # intento es una llamada de verdad al proveedor, nada se simula.
    prompts = [
        (
            f"Edita el archivo README.md: agrega una línea nueva al final que "
            f'diga EXACTAMENTE "{marca}". No cambies nada más del contenido '
            "existente. Usa tus herramientas de edición de archivos para "
            "hacerlo de verdad -- no describas el cambio, ejecútalo."
        ),
        (
            f'Aún no veo la línea "{marca}" en README.md. Llama YA a tu '
            "herramienta de edición de archivos (edit) sobre README.md y agrega "
            f'esa línea al final. No respondas con texto explicando qué harías: '
            "ejecuta la herramienta ahora mismo."
        ),
        (
            f'README.md sigue sin la línea "{marca}". Usa la herramienta bash '
            f'para correr: echo "{marca}" >> README.md. Ejecútalo de inmediato, '
            "sin explicaciones previas."
        ),
    ]

    async with await ServidorOpencode.iniciar(workspace, timeout_arranque=30.0) as servidor:
        sesion = await servidor.crear_sesion(proveedor="workersai", modelo=_MODELO_PRUEBA)
        assert sesion.model is not None
        assert sesion.model.providerID == "workersai"

        vio_escritura = False
        tipos_vistos: list[str] = []
        # ``session.next.tool.success`` NO trae el nombre de la herramienta
        # en su ``data`` (se comprobó: solo trae ``callID`` -- ver
        # ``SessionNextToolSuccess`` en /doc). El nombre vive en el
        # ``session.next.tool.called`` anterior con el MISMO ``callID`` --
        # por eso hace falta correlacionar ambos, no basta con mirar el
        # evento de éxito solo.
        herramienta_por_call_id: dict[str, str] = {}
        for _numero_intento, texto_prompt in enumerate(prompts, start=1):
            admitido = await servidor.enviar_prompt(sesion.id, texto_prompt)
            assert admitido.sessionID == sesion.id

            # Se reabre el stream SIN "desde": se comprobó en integración que
            # opencode reproduce los eventos durables desde el principio de
            # la sesión aunque uno se conecte después de que ya ocurrieron
            # -- así que releer desde el inicio en cada intento es
            # simplemente re-escanear el historial completo, no perder
            # eventos nuevos. ``tiempo_maximo_sin_eventos`` corta el
            # escaneo en cuanto el flujo se queda callado (la vuelta del
            # agente terminó) en vez de colgar el test.
            try:
                async for evento in servidor.eventos(sesion.id, tiempo_maximo_sin_eventos=20.0):
                    tipos_vistos.append(evento.type)
                    if evento.type == "session.next.tool.called":
                        call_id = evento.data.get("callID")
                        herramienta = evento.data.get("tool")
                        if call_id and herramienta:
                            herramienta_por_call_id[call_id] = herramienta
                    elif evento.type == "session.next.tool.success":
                        call_id = evento.data.get("callID")
                        if herramienta_por_call_id.get(call_id) in herramientas_escritura:
                            vio_escritura = True
                            break
            except TimeoutError:
                pass  # el flujo se quedó callado: la vuelta terminó, se evalúa abajo

            if vio_escritura:
                break

        assert vio_escritura, (
            f"tras {len(prompts)} intentos reales contra el proveedor, ningún evento de "
            f"éxito de herramienta de escritura -- tipos de evento vistos: {tipos_vistos}"
        )

    contenido_final = archivo.read_text(encoding="utf-8")
    hash_despues = hashlib.sha256(contenido_final.encode("utf-8")).hexdigest()

    # La prueba real es el archivo, no lo que haya dicho el modelo en el chat.
    assert hash_despues != hash_antes, (
        "el archivo NO cambió en disco -- esto es justo el bug que se busca evitar"
    )
    assert marca in contenido_final, (
        f"falta la línea pedida en el archivo final:\n{contenido_final}"
    )
    assert "Hola" in contenido_final and "Linea 2" in contenido_final, (
        "el contenido original se perdió -- se pidió agregar, no reescribir todo"
    )
    assert vio_escritura, (
        "no se vio ningún evento de escritura de archivo (write/edit/patch) en el stream SSE"
    )
