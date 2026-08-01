"""Tests de ``ide_opencode_motor.py`` -- el ciclo de vida del motor.

Dos grupos, mismo criterio que el resto de ``test_ide_opencode*.py``:

1. Puros (``modelo_para``, ``_diagnosticar_tarea_puente``, ``_correr_puente``
   con puentes falsos): funciones/coroutines deterministas, no hace falta un
   servidor real.
2. Reales (todo lo que arranca ``MotorOpencode.servidor_para``): opencode de
   verdad, Cloudflare de verdad, workspace temporal propio. Se saltan solos
   si faltan las credenciales (mismo criterio que el resto de la suite: se
   salta, nunca se simula). El grupo real incluye la prueba que el encargo
   pidió explícitamente: reproducir el cuelgue cuando el puente NO está
   escuchando, y comprobar que ``comprobar_y_reponer`` lo resuelve -- para
   que ese sea el fallo que quede fijado con un test, y no vuelva a colarse
   sin que algo lo grite (ver ``docs/opencode-motor.md`` §3).
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from edecan_companion import ide_opencode_motor as ide_opencode_motor_module
from edecan_companion.ide_opencode import ErrorOpencode
from edecan_companion.ide_opencode_motor import (
    NIVELES_ESFUERZO_MOTOR,
    MotorOpencode,
    MotorOpencodeError,
    PuenteDePermisosCaidoError,
    SaludMotor,
    _asegurar_permisos_restrictivos,
    _correr_puente,
    _cuenta_windows_actual,
    _cuentas_windows_candidatas,
    _diagnosticar_tarea_puente,
    _restringir_windows,
    modelo_para,
    referencia_para_modelo,
    variante_de_esfuerzo,
)
from edecan_companion.ide_opencode_permisos import ModoAgente

# --------------------------------------------------------------------------- #
# Credenciales -- mismo criterio que el resto de test_ide_opencode*.py
# --------------------------------------------------------------------------- #


def _leer_env_raiz(nombre: str) -> str | None:
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
        "Sin CLOUDFLARE_ACCOUNT_ID/CLOUDFLARE_API_TOKEN (entorno o .env de la raíz) no hay "
        "proveedor real contra el que arrancar opencode -- se salta, no se simula."
    ),
)

_MODELO_PRUEBA = "@cf/moonshotai/kimi-k2.7-code"
_RUTA_YAML_MODELOS_REAL = Path(__file__).resolve().parents[3] / "config" / "modelos.yml"


def _modo_fijo(modo: ModoAgente) -> Callable[[str], ModoAgente]:
    """``obtener_modo`` constante -- toda sesión, sea cual sea su id, está en
    ``modo``. Suficiente para estas pruebas: no necesitan un ``ModoAgenteStore``
    real, solo que la tabla de decisión de ``ide_opencode_permisos`` reciba
    algo válido."""

    def _obtener(_session_id: str) -> ModoAgente:
        return modo

    return _obtener


async def _esperar(
    condicion: Callable[[], Awaitable[bool]], *, intentos: int = 20, espera: float = 0.5
) -> bool:
    for _ in range(intentos):
        if await condicion():
            return True
        await asyncio.sleep(espera)
    return False


async def _esperar_archivo_cambie(
    ruta: Path, contenido_original: str, *, intentos: int = 20, espera: float = 0.5
) -> bool:
    for _ in range(intentos):
        if ruta.read_text(encoding="utf-8") != contenido_original:
            return True
        await asyncio.sleep(espera)
    return False


# --------------------------------------------------------------------------- #
# Puros -- modelo_para
# --------------------------------------------------------------------------- #


def test_modelo_para_bajo_usa_el_modelo_alternativo_del_yaml_real() -> None:
    """Encargo: "Bajo -> el modelo rápido". El YAML real declara
    ``perfiles.ingenieria_software.modelo_alternativo`` -- esta prueba lo lee
    de ahí, no lo copia a mano: si el YAML cambia ese valor, esta prueba
    sigue siendo verdad."""

    import yaml

    config = yaml.safe_load(_RUTA_YAML_MODELOS_REAL.read_text(encoding="utf-8"))
    esperado = config["perfiles"]["ingenieria_software"]["modelo_alternativo"]

    ref = modelo_para("ingenieria_software", "bajo")
    assert ref.model_id == esperado
    assert ref.provider_id == "workersai"
    assert ref.reasoning_effort == "low"
    assert ref.nivel == "bajo"


def test_modelo_para_alto_usa_el_modelo_fuerte_del_yaml_real() -> None:
    """Encargo: "Alto -> el fuerte" -- ``perfiles.ingenieria_software.modelo``."""

    import yaml

    config = yaml.safe_load(_RUTA_YAML_MODELOS_REAL.read_text(encoding="utf-8"))
    esperado = config["perfiles"]["ingenieria_software"]["modelo"]

    ref = modelo_para("ingenieria_software", "alto")
    assert ref.model_id == esperado
    assert ref.reasoning_effort == "high"


def test_modelo_para_medio_comparte_el_modelo_fuerte_con_alto() -> None:
    """Decisión propia documentada en el docstring del módulo: "medio" no
    degrada el modelo (el YAML no declara un tercer nivel), solo pide menos
    razonamiento."""

    alto = modelo_para("ingenieria_software", "alto")
    medio = modelo_para("ingenieria_software", "medio")
    assert medio.model_id == alto.model_id
    assert medio.reasoning_effort == "medium"
    assert medio.reasoning_effort != alto.reasoning_effort


def test_modelo_para_acepta_mayusculas_y_espacios() -> None:
    ref = modelo_para("ingenieria_software", "  ALTO  ")
    assert ref.nivel == "alto"


def test_modelo_para_nivel_desconocido_lanza_con_los_disponibles() -> None:
    with pytest.raises(MotorOpencodeError, match="bajo, medio, alto"):
        modelo_para("ingenieria_software", "extremo")


def test_modelo_para_perfil_desconocido_no_inventa_nada() -> None:
    with pytest.raises(MotorOpencodeError, match="no está declarado"):
        modelo_para("perfil-que-no-existe-en-el-yaml", "alto")


def test_niveles_esfuerzo_motor_son_exactamente_los_tres_del_encargo() -> None:
    assert NIVELES_ESFUERZO_MOTOR == ("bajo", "medio", "alto")


# --------------------------------------------------------------------------- #
# Puros -- referencia_para_modelo (el modelo YA elegido por la persona, ver
# la sección "selector vs. esfuerzo" del docstring del módulo)
# --------------------------------------------------------------------------- #


def test_referencia_para_modelo_usa_el_modelo_dado_no_el_del_perfil() -> None:
    """A diferencia de ``modelo_para``, esto NO consulta ningún perfil --
    el modelo elegido gana tal cual, sea o no el fuerte/alternativo de
    ``ingenieria_software``."""

    ref = referencia_para_modelo("@cf/nvidia/nemotron-3-120b-a12b", "bajo")
    assert ref.model_id == "@cf/nvidia/nemotron-3-120b-a12b"
    assert ref.provider_id == "workersai"
    assert ref.reasoning_effort == "low"
    assert ref.nivel == "bajo"
    assert ref.variante is None


def test_referencia_para_modelo_traduce_el_esfuerzo_igual_que_modelo_para() -> None:
    """La traducción nivel -> reasoning_effort es la MISMA tabla que usa
    ``modelo_para`` -- solo cambia de dónde sale el modelo."""

    for nivel, esperado in (("bajo", "low"), ("medio", "medium"), ("alto", "high")):
        ref = referencia_para_modelo("@cf/zai-org/glm-5.2", nivel)
        assert ref.reasoning_effort == esperado


def test_referencia_para_modelo_modelo_vacio_lanza() -> None:
    with pytest.raises(MotorOpencodeError, match="no puede estar vacío"):
        referencia_para_modelo("   ", "alto")


def test_referencia_para_modelo_nivel_desconocido_lanza() -> None:
    with pytest.raises(MotorOpencodeError, match="bajo, medio, alto"):
        referencia_para_modelo("@cf/zai-org/glm-5.2", "extremo")


# --------------------------------------------------------------------------- #
# Puros -- variante_de_esfuerzo, con el catálogo en vivo de opencode falso
# (``httpx.AsyncClient`` reemplazado -- ver el docstring del módulo, esta
# función NO tiene otra superficie que exponer para inyectar un transporte:
# hace su propio ``GET /model`` con el ``base_url`` de ``servidor``).
# --------------------------------------------------------------------------- #


class _RespuestaModeloFalsa:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _ClienteModeloFalso:
    """Reemplaza ``httpx.AsyncClient`` para que ``variante_de_esfuerzo`` lea
    ``payload`` en vez de pegarle a un servidor real."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def __aenter__(self) -> _ClienteModeloFalso:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def get(self, ruta: str) -> _RespuestaModeloFalsa:
        assert ruta == "/model"
        return _RespuestaModeloFalsa(self._payload)


class _ClienteModeloQueFalla:
    """Simula el servidor caído/inalcanzable a medio ``GET /model``."""

    async def __aenter__(self) -> _ClienteModeloQueFalla:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def get(self, ruta: str) -> _RespuestaModeloFalsa:
        raise httpx.TransportError("boom: el servidor no respondió")


async def test_variante_de_esfuerzo_encuentra_la_variante_declarada(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "data": [
            {
                "id": "@cf/moonshotai/kimi-k2.7-code",
                "providerID": "workersai",
                "variants": [{"id": "high", "headers": {}, "body": {}}],
            }
        ]
    }
    monkeypatch.setattr(
        ide_opencode_motor_module.httpx, "AsyncClient", lambda *a, **k: _ClienteModeloFalso(payload)
    )
    servidor = SimpleNamespace(base_url="http://127.0.0.1:9999/api")
    variante = await variante_de_esfuerzo(
        servidor,  # type: ignore[arg-type]
        proveedor="workersai",
        modelo="@cf/moonshotai/kimi-k2.7-code",
        reasoning_effort="high",
    )
    assert variante == "high"


async def test_variante_de_esfuerzo_sin_variante_declarada_devuelve_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El caso real de HOY para los cinco modelos de Workers AI del
    selector: ninguno declara ``variants`` -- ver la sección "selector vs.
    esfuerzo" del docstring del módulo."""

    payload = {
        "data": [{"id": "@cf/zai-org/glm-5.2", "providerID": "workersai", "variants": []}]
    }
    monkeypatch.setattr(
        ide_opencode_motor_module.httpx, "AsyncClient", lambda *a, **k: _ClienteModeloFalso(payload)
    )
    servidor = SimpleNamespace(base_url="http://127.0.0.1:9999/api")
    variante = await variante_de_esfuerzo(
        servidor,  # type: ignore[arg-type]
        proveedor="workersai",
        modelo="@cf/zai-org/glm-5.2",
        reasoning_effort="high",
    )
    assert variante is None


async def test_variante_de_esfuerzo_modelo_ausente_del_catalogo_devuelve_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {"data": []}
    monkeypatch.setattr(
        ide_opencode_motor_module.httpx, "AsyncClient", lambda *a, **k: _ClienteModeloFalso(payload)
    )
    servidor = SimpleNamespace(base_url="http://127.0.0.1:9999/api")
    variante = await variante_de_esfuerzo(
        servidor,  # type: ignore[arg-type]
        proveedor="workersai",
        modelo="@cf/no-existe/inventado",
        reasoning_effort="high",
    )
    assert variante is None


async def test_variante_de_esfuerzo_error_de_red_devuelve_none_sin_reventar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un servidor caído a medio ``GET /model`` no debe reventar
    ``fijar_esfuerzo``/``_turno_opencode`` -- se asume "sin variante" y
    quien llama decide qué avisar (ver ``ide_sessions.fijar_esfuerzo``)."""

    monkeypatch.setattr(
        ide_opencode_motor_module.httpx, "AsyncClient", lambda *a, **k: _ClienteModeloQueFalla()
    )
    servidor = SimpleNamespace(base_url="http://127.0.0.1:9999/api")
    variante = await variante_de_esfuerzo(
        servidor,  # type: ignore[arg-type]
        proveedor="workersai",
        modelo="@cf/zai-org/glm-5.2",
        reasoning_effort="high",
    )
    assert variante is None


# --------------------------------------------------------------------------- #
# Puros -- _diagnosticar_tarea_puente / _correr_puente, con puentes falsos
# --------------------------------------------------------------------------- #


class _PuenteFalsoTerminaSolo:
    """``escuchar()`` que agota su generador SIN ninguna excepción -- el caso
    más traicionero: no grita nada por sí solo."""

    async def escuchar(self):  # noqa: ANN201 - generador async de prueba
        if False:  # nunca produce nada, pero es un generador de verdad
            yield None


class _PuenteFalsoFalla:
    """``escuchar()`` que revienta con un ``ErrorOpencode`` real -- debe
    propagarse TAL CUAL, sin que ``_correr_puente`` lo envuelva."""

    async def escuchar(self):  # noqa: ANN201
        raise ErrorOpencode("boom: se perdió la conexión con el bus")
        yield None  # pragma: no cover - inalcanzable, hace de esto un generador


class _PuenteFalsoNuncaTermina:
    """``escuchar()`` que se queda escuchando para siempre -- el caso normal,
    usado para probar que cancelar la tarea se propaga limpio."""

    async def escuchar(self):  # noqa: ANN201
        while True:
            await asyncio.sleep(3600)
            yield None  # pragma: no cover - inalcanzable


async def test_correr_puente_convierte_un_final_silencioso_en_puentecaido_error() -> None:
    """Ver docstring de ``_correr_puente``: un ``escuchar()`` que termina
    solo, sin excepción, es EXACTAMENTE la trampa del §3 -- nadie queda
    escuchando y ningún permission.v2.asked futuro tiene quien lo atienda.
    Esto tiene que convertirse en una excepción real, nunca en un "terminó
    bien" silencioso."""

    with pytest.raises(PuenteDePermisosCaidoError, match="terminó por su cuenta"):
        await _correr_puente(_PuenteFalsoTerminaSolo())


async def test_correr_puente_propaga_el_error_real_de_opencode_sin_envolverlo() -> None:
    with pytest.raises(ErrorOpencode, match="boom"):
        await _correr_puente(_PuenteFalsoFalla())


async def test_correr_puente_propaga_la_cancelacion_sin_convertirla_en_error() -> None:
    tarea = asyncio.create_task(_correr_puente(_PuenteFalsoNuncaTermina()))
    await asyncio.sleep(0)
    assert not tarea.done()
    tarea.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tarea


async def test_diagnosticar_tarea_puente_vivo_mientras_no_termine() -> None:
    tarea = asyncio.create_task(_correr_puente(_PuenteFalsoNuncaTermina()))
    await asyncio.sleep(0)
    try:
        vivo, motivo = _diagnosticar_tarea_puente(tarea)
        assert vivo is True
        assert motivo is None
    finally:
        tarea.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarea


async def test_diagnosticar_tarea_puente_detecta_cancelacion() -> None:
    tarea = asyncio.create_task(_correr_puente(_PuenteFalsoNuncaTermina()))
    await asyncio.sleep(0)
    tarea.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tarea
    vivo, motivo = _diagnosticar_tarea_puente(tarea)
    assert vivo is False
    assert motivo is not None and "cancelada" in motivo


async def test_diagnosticar_tarea_puente_detecta_excepcion_real() -> None:
    tarea = asyncio.create_task(_correr_puente(_PuenteFalsoFalla()))
    with pytest.raises(ErrorOpencode):
        await tarea
    vivo, motivo = _diagnosticar_tarea_puente(tarea)
    assert vivo is False
    assert motivo is not None and "ErrorOpencode" in motivo


async def test_diagnosticar_tarea_puente_detecta_final_silencioso() -> None:
    tarea = asyncio.create_task(_correr_puente(_PuenteFalsoTerminaSolo()))
    with pytest.raises(PuenteDePermisosCaidoError):
        await tarea
    vivo, motivo = _diagnosticar_tarea_puente(tarea)
    assert vivo is False
    assert motivo is not None and "PuenteDePermisosCaidoError" in motivo


# --------------------------------------------------------------------------- #
# Windows -- icacls, la copia de ide_opencode_motor de la misma protección
# que ide_opencode_config._restringir_windows (decisión 5 del docstring del
# módulo: cinturón y tirantes, cada módulo con su propia copia).
#
# Mismo criterio honesto que test_ide_opencode_config.py (regla 6 y regla
# dura de esta ronda): esta Mac no tiene ``icacls``, así que estos tests
# prueban la LÓGICA (construcción del comando, resolución de cuenta, manejo
# de error) -- no el resultado real contra Windows, que queda pendiente de
# una fase con acceso a la VM.
# --------------------------------------------------------------------------- #


def test_cuenta_windows_actual_usa_userdomain_y_username(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USERDOMAIN", "MI-PC")
    monkeypatch.setenv("USERNAME", "ada")
    assert _cuenta_windows_actual() == "MI-PC\\ada"


def test_cuenta_windows_actual_cae_a_getpass_sin_userdomain_username(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("USERDOMAIN", raising=False)
    monkeypatch.delenv("USERNAME", raising=False)
    monkeypatch.setattr(
        ide_opencode_motor_module.getpass, "getuser", lambda: "ada-posix"
    )
    assert _cuenta_windows_actual() == "ada-posix"


def test_restringir_windows_arma_el_comando_icacls_esperado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("USERDOMAIN", "MI-PC")
    monkeypatch.setenv("USERNAME", "ada")
    ruta = tmp_path / "opencode.json"
    ruta.write_text("{}", encoding="utf-8")

    llamadas: list[tuple[list[str], dict]] = []

    def _run_falso(comando: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        llamadas.append((comando, kwargs))
        return subprocess.CompletedProcess(comando, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ide_opencode_motor_module.subprocess, "run", _run_falso)

    _restringir_windows(ruta)

    assert len(llamadas) == 1
    comando, kwargs = llamadas[0]
    assert comando[0] == "icacls"
    assert comando[1] == str(ruta)
    assert "/inheritance:r" in comando
    assert "MI-PC\\ada:F" in comando
    assert "SYSTEM:F" in comando
    assert kwargs.get("shell") is not True


def test_restringir_windows_codigo_de_salida_distinto_de_cero_no_revienta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("USERDOMAIN", "MI-PC")
    monkeypatch.setenv("USERNAME", "ada")
    ruta = tmp_path / "opencode.json"
    ruta.write_text("{}", encoding="utf-8")

    def _run_falla(comando: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            comando, returncode=5, stdout="", stderr="acceso denegado"
        )

    monkeypatch.setattr(ide_opencode_motor_module.subprocess, "run", _run_falla)

    _restringir_windows(ruta)  # best-effort: no debe lanzar nada


def test_restringir_windows_en_esta_mac_no_revienta_aunque_icacls_no_exista(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Sin mockear ``subprocess.run``: esta Mac de verdad no tiene ``icacls``
    -- rama real de "el binario no está", no simulada."""

    monkeypatch.setenv("USERDOMAIN", "MI-PC")
    monkeypatch.setenv("USERNAME", "ada")
    ruta = tmp_path / "opencode.json"
    ruta.write_text("{}", encoding="utf-8")

    with caplog.at_level("WARNING", logger=ide_opencode_motor_module.logger.name):
        _restringir_windows(ruta)  # no debe lanzar nada

    assert any("icacls" in registro.message for registro in caplog.records)


# --------------------------------------------------------------------------- #
# Cierre de Windows -- mismo hallazgo que test_ide_opencode_config.py: la
# VM real (EC2, standalone/workgroup) tiene USERDOMAIN=WORKGROUP, que
# icacls rechaza con el código 1332. Copia deliberada de esos tests (mismo
# criterio de "cinturón y tirantes" que el propio módulo).
# --------------------------------------------------------------------------- #


def test_candidatas_con_workgroup_prueba_cuenta_local_antes_que_el_workgroup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("USERDOMAIN", "WORKGROUP")
    monkeypatch.setenv("USERNAME", "administrator")
    assert _cuentas_windows_candidatas() == [".\\administrator", "WORKGROUP\\administrator"]


def test_restringir_windows_con_workgroup_reintenta_con_cuenta_local_y_tiene_exito(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduce el hallazgo real de la VM: el primer candidato local tiene
    éxito, sin necesidad de llegar al workgroup literal (el que falló en
    vivo con el código 1332)."""

    monkeypatch.setenv("USERDOMAIN", "WORKGROUP")
    monkeypatch.setenv("USERNAME", "administrator")
    ruta = tmp_path / "opencode.json"
    ruta.write_text("{}", encoding="utf-8")

    llamadas: list[list[str]] = []

    def _run_falso(comando: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        llamadas.append(comando)
        if comando[4] == ".\\administrator:F":
            return subprocess.CompletedProcess(comando, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(comando, returncode=1332, stdout="", stderr="")

    monkeypatch.setattr(ide_opencode_motor_module.subprocess, "run", _run_falso)

    _restringir_windows(ruta)

    assert len(llamadas) == 1
    assert llamadas[0][4] == ".\\administrator:F"


def test_asegurar_permisos_restrictivos_en_windows_llama_a_restringir_windows_no_a_chmod(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ruta = tmp_path / "opencode.json"
    ruta.write_text("{}", encoding="utf-8")

    llamadas_windows: list[Path] = []
    llamadas_chmod: list[tuple[Path, int]] = []
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(
        ide_opencode_motor_module, "_restringir_windows", llamadas_windows.append
    )
    monkeypatch.setattr(
        ide_opencode_motor_module.os,
        "chmod",
        lambda ruta, modo: llamadas_chmod.append((ruta, modo)),
    )

    _asegurar_permisos_restrictivos(ruta)

    assert llamadas_windows == [ruta]
    assert llamadas_chmod == []


# --------------------------------------------------------------------------- #
# Puro -- salud de un workspace que nunca se arrancó
# --------------------------------------------------------------------------- #


async def test_salud_de_workspace_nunca_arrancado_no_lanza_y_dice_arrancado_false() -> None:
    motor = MotorOpencode(obtener_modo=_modo_fijo(ModoAgente.AUTO))
    salud = await motor.salud("/no/existe/nunca/se/arranco")
    assert isinstance(salud, SaludMotor)
    assert salud.arrancado is False
    assert salud.servidor_vivo is False
    assert salud.puente_vivo is False


async def test_comprobar_y_reponer_de_workspace_nunca_arrancado_lanza() -> None:
    motor = MotorOpencode(obtener_modo=_modo_fijo(ModoAgente.AUTO))
    with pytest.raises(MotorOpencodeError, match="No hay ningún motor arrancado"):
        await motor.comprobar_y_reponer("/no/existe/nunca/se/arranco")


def test_max_reintentos_negativo_lanza_al_construir() -> None:
    with pytest.raises(MotorOpencodeError):
        MotorOpencode(obtener_modo=_modo_fijo(ModoAgente.AUTO), max_reintentos=-1)


# --------------------------------------------------------------------------- #
# Reales -- arrancan opencode y Cloudflare de verdad
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@requiere_credenciales
async def test_servidor_para_arranca_prepara_opencode_json_blinda_el_secreto_y_reutiliza(
    tmp_path: Path,
) -> None:
    """Cubre de una sola pasada (para no pagar tres arranques reales de
    opencode): decisión 3 (``opencode.json`` se genera solo, con
    ``permission: ask``), decisión 5 (el archivo queda en 0600 en POSIX), y
    la reutilización por workspace (dos llamadas devuelven el MISMO
    servidor)."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assert not (workspace / "opencode.json").exists()

    async with MotorOpencode(obtener_modo=_modo_fijo(ModoAgente.AUTO)) as motor:
        servidor1 = await motor.servidor_para(workspace)

        ruta_config = workspace / "opencode.json"
        assert ruta_config.is_file(), "MotorOpencode debía generar opencode.json solo"
        contenido = json.loads(ruta_config.read_text(encoding="utf-8"))
        assert "workersai" in contenido["provider"], "provider.workersai no quedó registrado"
        assert contenido["permission"] == {
            "edit": "ask",
            "bash": "ask",
            "webfetch": "ask",
        }, "sin 'permission: ask' el puente nunca tendría nada que interceptar"

        if os.name != "nt":
            modo_archivo = stat.S_IMODE(ruta_config.stat().st_mode)
            assert modo_archivo == 0o600, (
                f"opencode.json quedó con permisos {oct(modo_archivo)}, no 0600 -- el token "
                "de Cloudflare en claro queda expuesto a otros usuarios del sistema"
            )

        servidor2 = await motor.servidor_para(workspace)
        assert servidor1 is servidor2, (
            "un segundo servidor_para() para el MISMO workspace debía reutilizar, no "
            "arrancar otro proceso opencode"
        )

        salud = await motor.salud(workspace)
        assert salud.arrancado is True
        assert salud.servidor_vivo is True
        assert salud.puente_vivo is True
        assert salud.motivo_puente is None
        assert salud.reintentos_servidor == 0
        assert salud.reintentos_puente == 0


@pytest.mark.asyncio
@requiere_credenciales
async def test_servidor_para_concurrente_para_el_mismo_workspace_arranca_uno_solo(
    tmp_path: Path,
) -> None:
    """Encargo, punto 1: "Si dos sesiones piden el mismo workspace a la vez,
    una sola arranca (candado)." Se cuentan las llamadas reales a
    ``ServidorOpencode.iniciar`` con un contador propio -- sin tocar nada de
    ``ide_opencode.py`` (se envuelve desde afuera, vía monkeypatch de la
    prueba, no del módulo)."""

    import edecan_companion.ide_opencode as ide_opencode_module

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    llamadas = 0
    iniciar_original = ide_opencode_module.ServidorOpencode.iniciar.__func__

    async def iniciar_contando(cls, *args, **kwargs):
        nonlocal llamadas
        llamadas += 1
        return await iniciar_original(cls, *args, **kwargs)

    ide_opencode_module.ServidorOpencode.iniciar = classmethod(iniciar_contando)
    try:
        async with MotorOpencode(obtener_modo=_modo_fijo(ModoAgente.AUTO)) as motor:
            servidor1, servidor2 = await asyncio.gather(
                motor.servidor_para(workspace), motor.servidor_para(workspace)
            )
            assert servidor1 is servidor2
            assert llamadas == 1, (
                f"ServidorOpencode.iniciar se llamó {llamadas} veces para el mismo workspace "
                "pedido a la vez -- el candado por workspace no está funcionando"
            )
    finally:
        # ``__func__`` desenvuelve el descriptor de classmethod -- hay que
        # volver a envolverlo al restaurar, o queda una función suelta que
        # ya no recibe `cls` automáticamente al llamarse vía la clase.
        ide_opencode_module.ServidorOpencode.iniciar = classmethod(iniciar_original)


@pytest.mark.asyncio
@requiere_credenciales
async def test_puente_ya_esta_escuchando_antes_del_primer_prompt_modo_auto_resuelve_solo(
    tmp_path: Path,
) -> None:
    """Decisión 2 del docstring del módulo, de punta a punta: cuando
    ``servidor_para`` devuelve, el puente YA está escuchando -- así que un
    prompt mandado a través del ``ServidorOpencode`` que devuelve el motor
    se resuelve solo en modo Auto, sin que nadie más intervenga."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    archivo = workspace / "auto.txt"
    archivo.write_text("original\n", encoding="utf-8")

    async with MotorOpencode(obtener_modo=_modo_fijo(ModoAgente.AUTO)) as motor:
        servidor = await motor.servidor_para(workspace)
        sesion = await servidor.crear_sesion(
            directorio=workspace, agente="build", proveedor="workersai", modelo=_MODELO_PRUEBA
        )

        for texto in (
            'Sobreescribe auto.txt para que diga exactamente "modificado-auto". Usa tu '
            "herramienta de escritura ya, sin preguntar nada.",
            'auto.txt sigue igual. Usa tu herramienta de edición/escritura sobre auto.txt '
            'con "modificado-auto" ahora mismo.',
        ):
            await servidor.enviar_prompt(sesion.id, texto)
            cambio = await _esperar_archivo_cambie(archivo, "original\n", intentos=20, espera=1.0)
            if cambio:
                break

        assert cambio, (
            "con el puente del motor ya escuchando desde antes del primer prompt, el archivo "
            "debía cambiar solo en modo Auto -- si esto falla, el puente no estaba vivo a "
            "tiempo"
        )
        assert "modificado-auto" in archivo.read_text(encoding="utf-8")


@pytest.mark.asyncio
@requiere_credenciales
async def test_sin_puente_vivo_una_edicion_se_cuelga_y_comprobar_y_reponer_la_resuelve(
    tmp_path: Path,
) -> None:
    """LA prueba que el encargo pidió explícitamente: reproduce el cuelgue
    real cuando el puente del motor deja de escuchar (se mata la tarea de
    fondo a mano, simulando que murió sola), con timeout ACOTADO -- y
    comprueba que ``comprobar_y_reponer`` lo nota, repone el puente, corre
    ``recuperar_pendientes`` y desatasca el MISMO turno sin reenviar el
    prompt. Esto deja fijado en un test que ESE es el fallo (docs/
    opencode-motor.md §3), para que no se vuelva a colar sin que algo lo
    grite."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    archivo = workspace / "colgado.txt"
    archivo.write_text("original\n", encoding="utf-8")

    async with MotorOpencode(obtener_modo=_modo_fijo(ModoAgente.AUTO)) as motor:
        servidor = await motor.servidor_para(workspace)
        directorio = Path(workspace).resolve()
        entrada = motor._entradas[directorio]  # acceso de caja blanca: es el propio motor

        # Matar el puente A MANO, simulando exactamente lo que este módulo
        # existe para detectar: la tarea de fondo murió y nadie más está
        # respondiendo permission.v2.asked.
        entrada.tarea_puente.cancel()
        with pytest.raises(asyncio.CancelledError):
            await entrada.tarea_puente

        salud_tras_matar = await motor.salud(workspace)
        assert salud_tras_matar.puente_vivo is False, (
            "salud() debía notar que el puente ya no está vivo -- ver el requisito principal "
            "del encargo: el motor tiene que darse cuenta y decirlo, no quedarse mudo"
        )

        sesion = await servidor.crear_sesion(
            directorio=workspace, agente="build", proveedor="workersai", modelo=_MODELO_PRUEBA
        )

        _HERRAMIENTAS_DE_ESCRITURA = {"edit", "write", "patch"}
        vio_tool_called = False
        colgado = False
        for texto in (
            'Sobreescribe colgado.txt para que diga "modificado-colgado". Usa tu '
            "herramienta de escritura ya.",
            'colgado.txt sigue igual. Usa tu herramienta de edición/escritura sobre '
            'colgado.txt con "modificado-colgado" ahora.',
            'Sigue sin cambiar. Llama YA a tu herramienta de editar/escribir archivos '
            'sobre colgado.txt, sin explicar nada antes.',
        ):
            await servidor.enviar_prompt(sesion.id, texto)
            try:
                async for ev in servidor.eventos(sesion.id, tiempo_maximo_sin_eventos=30.0):
                    if (
                        ev.type == "session.next.tool.called"
                        and ev.data.get("tool") in _HERRAMIENTAS_DE_ESCRITURA
                    ):
                        vio_tool_called = True
            except TimeoutError:
                colgado = True
            if vio_tool_called:
                break

        assert vio_tool_called, "el modelo real nunca llamó a su herramienta de edición"
        assert colgado, (
            "sin el puente del motor vivo, el stream de sesión debía quedarse mudo tras "
            "tool.called(edit) -- si esto falla, algo más está respondiendo el permiso"
        )
        assert archivo.read_text(encoding="utf-8") == "original\n", (
            "el archivo cambió sin puente vivo -- la premisa de esta prueba (permission: ask "
            "frena de verdad) no se cumplió"
        )

        # La reposición: comprobar_y_reponer debe notar el puente muerto,
        # reponerlo, recuperar lo pendiente y desatascar el MISMO turno.
        salud_repuesta = await motor.comprobar_y_reponer(workspace)
        assert salud_repuesta.servidor_vivo is True
        assert salud_repuesta.puente_vivo is True
        assert salud_repuesta.reintentos_puente == 1

        escribio = await _esperar_archivo_cambie(archivo, "original\n", intentos=20, espera=1.0)
        assert escribio, (
            "comprobar_y_reponer repuso el puente pero el archivo nunca cambió en disco -- "
            "recuperar_pendientes no desatascó el turno colgado"
        )
        assert "modificado-colgado" in archivo.read_text(encoding="utf-8")


@pytest.mark.asyncio
@requiere_credenciales
async def test_comprobar_y_reponer_agota_reintentos_del_puente_y_deja_de_intentar(
    tmp_path: Path,
) -> None:
    """Encargo, requisito de salud: "con límite de reintentos, no un bucle
    infinito." Con ``max_reintentos=1``: la primera muerte del puente se
    repone; la segunda ya no -- ``comprobar_y_reponer`` lo dice en
    ``motivo_puente`` en vez de reintentar para siempre."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    async with MotorOpencode(
        obtener_modo=_modo_fijo(ModoAgente.AUTO), max_reintentos=1
    ) as motor:
        await motor.servidor_para(workspace)
        directorio = Path(workspace).resolve()

        # Primera muerte -- debe reponerse (reintentos_puente pasa a 1).
        entrada = motor._entradas[directorio]
        entrada.tarea_puente.cancel()
        with pytest.raises(asyncio.CancelledError):
            await entrada.tarea_puente
        salud_1 = await motor.comprobar_y_reponer(workspace)
        assert salud_1.puente_vivo is True
        assert salud_1.reintentos_puente == 1

        # Segunda muerte -- ya se agotó el único reintento permitido.
        entrada = motor._entradas[directorio]
        entrada.tarea_puente.cancel()
        with pytest.raises(asyncio.CancelledError):
            await entrada.tarea_puente
        salud_2 = await motor.comprobar_y_reponer(workspace)
        assert salud_2.puente_vivo is False
        assert salud_2.reintentos_puente == 1
        assert salud_2.motivo_puente is not None
        assert "agotaron" in salud_2.motivo_puente


@pytest.mark.asyncio
@requiere_credenciales
async def test_detener_es_idempotente_y_detener_todo_limpia_todos_los_workspaces(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    motor = MotorOpencode(obtener_modo=_modo_fijo(ModoAgente.AUTO))
    await motor.servidor_para(workspace)
    assert (await motor.salud(workspace)).arrancado is True

    await motor.detener(workspace)
    salud = await motor.salud(workspace)
    assert salud.arrancado is False

    # Detener un workspace que ya no está arrancado es un no-op, no un error.
    await motor.detener(workspace)

    await motor.servidor_para(workspace)
    await motor.detener_todo()
    assert (await motor.salud(workspace)).arrancado is False


@pytest.mark.asyncio
@requiere_credenciales
async def test_variante_de_esfuerzo_se_encuentra_nada_mas_arrancar_el_servidor(
    tmp_path: Path,
) -> None:
    """El nivel de esfuerzo llega al modelo, y llega desde el PRIMER turno.

    Esta prueba afirmaba lo contrario -- que ``variante_de_esfuerzo`` devuelve
    ``None`` porque ningún modelo de Workers AI declara ``variants``. Su propio
    docstring pedía actualizarla, no "arreglarla", si algún día eso cambiaba.
    Cambió dos veces:

    1. ``generar_opencode_json`` ahora **declara** las tres variantes
       (low/medium/high con su ``reasoningEffort``) para cada modelo. No hacía
       falta esperar a que Cloudflare las publicara: se declaran del lado del
       cliente y opencode las traduce a ``{"body": {"reasoning_effort": ...}}``.
    2. El catálogo ``GET /model`` de un proveedor propio tarda ~1-2 s en
       poblarse tras arrancar el servidor (medido: vacío a t+1,11 s, completo a
       t+2,22 s). ``variante_de_esfuerzo`` reintenta hasta que carga, porque sin
       eso la PRIMERA conversación de cada arranque perdía el nivel elegido en
       silencio -- y los siguientes turnos sí lo aplicaban, que es el peor tipo
       de fallo: intermitente y sin explicación.

    Por eso esta prueba consulta **inmediatamente** después de arrancar, sin
    esperas artificiales: la ventana de la carrera es justo ese momento.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    async with MotorOpencode(obtener_modo=_modo_fijo(ModoAgente.AUTO)) as motor:
        servidor = await motor.servidor_para(workspace)
        variante = await variante_de_esfuerzo(
            servidor,
            proveedor="workersai",
            modelo=_MODELO_PRUEBA,
            reasoning_effort="high",
        )
        assert variante == "high", (
            f"{_MODELO_PRUEBA} debería declarar la variante 'high' (la escribe "
            "generar_opencode_json) y encontrarse nada más arrancar. Un None aquí "
            "significa que se rompió la declaración de variantes o el reintento de "
            "la carrera de arranque."
        )

        # Un nivel que NO se declara tiene que seguir devolviendo None: el
        # reintento no puede convertirse en "inventar cualquier variante".
        assert (
            await variante_de_esfuerzo(
                servidor,
                proveedor="workersai",
                modelo=_MODELO_PRUEBA,
                reasoning_effort="ultra",
            )
            is None
        )
