"""Tests de ``ide_opencode_config.py`` -- generación de ``opencode.json`` a
partir de ``config/modelos.yml`` real, y comprobación REAL contra opencode y
contra Cloudflare (regla del encargo: nada simulado).

Los tests de generación usan workspaces temporales propios (regla 7: nunca los
repos del dueño) y el ``config/modelos.yml`` de verdad del monorepo -- no una
copia inventada, porque el encargo pide explícitamente "leela de ahi, no la
copies": si el YAML cambia, este test tiene que seguir siendo verdad.

Los tests que arrancan opencode o llaman a Cloudflare de verdad se saltan
solo si faltan credenciales en el entorno/``.env`` -- igual que
``test_ide_opencode.py``.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest
from edecan_companion import ide_opencode_config as ide_opencode_config_module
from edecan_companion.ide_opencode_config import (
    NIVELES_ESFUERZO_OPENCODE,
    PERMISOS_POR_DEFECTO,
    PROVEEDOR_WORKERS_AI_ID,
    CredencialCloudflareInvalidaError,
    CredencialesCloudflareFaltantesError,
    ErrorConfiguracionOpencode,
    _asegurar_permisos_restrictivos,
    _cuenta_windows_actual,
    _cuentas_windows_candidatas,
    _restringir_windows,
    comprobar_credencial_cloudflare,
    comprobar_proveedor_registrado,
    generar_opencode_json,
    perfil_a_referencia_modelo,
)

_REFERENCIA_ENV_TOKEN = "{env:CLOUDFLARE_API_TOKEN}"
"""Copiado del módulo bajo prueba a propósito -- si algún día ese texto
cambia sin que un test lo note, es justo el tipo de regresión silenciosa
que esta constante existe para atrapar (comparación por valor, no por
identidad de import)."""

# Sin `pytestmark = pytest.mark.asyncio` global a propósito: este archivo
# mezcla tests sync (generación de opencode.json, que no toca la red) y async
# (los que arrancan opencode o llaman a Cloudflare de verdad). Con
# `asyncio_mode = "auto"` (ver apps/companion/pyproject.toml) los `async def`
# ya corren solos -- un marcador global solo generaba warnings en los sync.

RUTA_CONFIG_MODELOS_REAL = Path(__file__).resolve().parents[3] / "config" / "modelos.yml"


# --------------------------------------------------------------------------- #
# Credenciales -- mismo patrón que test_ide_opencode.py: se leen, nunca se
# imprimen ni se vuelcan a ningún archivo.
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
        "Sin CLOUDFLARE_ACCOUNT_ID/CLOUDFLARE_API_TOKEN en el entorno o en el .env de la "
        "raíz no hay credencial real contra la que probar -- se salta, no se simula."
    ),
)


def _env_vacio(tmp_path: Path) -> Path:
    """Un ``.env`` que a propósito NO tiene credenciales -- para probar el
    camino de "faltan credenciales" sin depender de que el entorno real de
    quien corre el test las tenga o no puestas como variable de entorno."""

    ruta = tmp_path / ".env-vacio"
    ruta.write_text("# sin credenciales\n", encoding="utf-8")
    return ruta


# --------------------------------------------------------------------------- #
# Perfiles -- contra el config/modelos.yml real del monorepo
# --------------------------------------------------------------------------- #


def test_perfil_ingenieria_software_apunta_a_workersai() -> None:
    ref = perfil_a_referencia_modelo("ingenieria_software")
    assert ref.providerID == PROVEEDOR_WORKERS_AI_ID
    # No se compara contra un id fijo (eso duplicaría la autoridad del YAML,
    # justo lo que el encargo prohíbe) -- solo que el formato es el esperado
    # de un modelo de Workers AI, y que coincide con lo que ya usa
    # task_router.modelo_para_perfil para el mismo perfil.
    assert ref.id.startswith("@cf/")
    from edecan_llm.task_router import modelo_para_perfil

    assert ref.id == modelo_para_perfil("ingenieria_software")


def test_perfil_desconocido_no_revienta_cae_al_respaldo_de_task_router() -> None:
    # modelo_para_perfil() ya define su propio respaldo para perfiles no
    # declarados -- este módulo no le agrega ni le quita comportamiento, solo
    # lo envuelve con el providerID. Ver ese respaldo en task_router.py.
    ref = perfil_a_referencia_modelo("perfil-que-no-existe-en-el-yaml")
    assert ref.providerID == PROVEEDOR_WORKERS_AI_ID
    assert ref.id  # algo no vacío -- el respaldo de task_router, no un crash


# --------------------------------------------------------------------------- #
# Generación de opencode.json -- workspace vacío
# --------------------------------------------------------------------------- #


def test_genera_opencode_json_nuevo_con_catalogo_real(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    resultado = generar_opencode_json(
        workspace,
        cuenta_id="cuenta-de-prueba",
        api_token="token-de-prueba",
    )

    assert resultado.creado is True
    assert resultado.proveedor_escrito is True
    assert resultado.advertencia_proveedor is None
    assert resultado.aviso_credencial is not None and "texto claro" in resultado.aviso_credencial
    assert resultado.ruta == workspace / "opencode.json"
    assert resultado.ruta.is_file()

    # CRÍTICO (hallazgo del verificador): sin esto los cuatro modos del IDE
    # no frenan nada -- ver PERMISOS_POR_DEFECTO y la sección "CORRECCIÓN"
    # del docstring del módulo.
    assert resultado.permiso_escrito is True
    assert resultado.advertencia_permiso is None

    # Segundo hallazgo (la otra cara de la corrección, ver sección "TRAMPA"
    # del docstring del módulo): este campo SIEMPRE viene poblado, recordando
    # que 'permission: ask' sin PuenteDePermisos.escuchar() corriendo se
    # cuelga para siempre en el primer edit/bash/webfetch real.
    assert "PuenteDePermisos" in resultado.aviso_puente_requerido
    assert "cuelga" in resultado.aviso_puente_requerido

    contenido = json.loads(resultado.ruta.read_text(encoding="utf-8"))
    assert contenido["$schema"] == "https://opencode.ai/config.json"
    assert contenido["permission"] == PERMISOS_POR_DEFECTO
    bloque = contenido["provider"][PROVEEDOR_WORKERS_AI_ID]
    # NOTA (ver "TERCERA CORRECCIÓN" en el docstring del módulo): esto sigue
    # siendo el token EN CLARO, no la referencia '{env:CLOUDFLARE_API_TOKEN}'
    # -- se probó esa referencia contra opencode real haciendo una llamada
    # de verdad y NO se resuelve para un proveedor custom, así que escribirla
    # dejaría al agente sin poder completar ni un prompt.
    assert bloque["options"]["apiKey"] == "token-de-prueba"
    assert "cuenta-de-prueba" in bloque["options"]["baseURL"]

    # El catálogo escrito tiene que ser EXACTAMENTE el de modelos_ide del YAML
    # real -- ni más, ni menos, ni inventado.
    from edecan_llm.task_router import modelos_ide_disponibles

    ids_esperados = {fila["id"] for fila in modelos_ide_disponibles()}
    assert set(resultado.modelos) == ids_esperados
    assert set(bloque["models"].keys()) == ids_esperados

    # Encargo de esta ronda: CADA modelo trae sus tres variantes de esfuerzo,
    # con los nombres LITERALES low/medium/high (ver "CUARTA CORRECCIÓN" del
    # docstring del módulo) -- sin esto, ide_opencode_motor.variante_de_esfuerzo
    # nunca encuentra nada y el control Bajo/Medio/Alto del selector no llega
    # a Cloudflare.
    assert set(NIVELES_ESFUERZO_OPENCODE) == {"low", "medium", "high"}
    for modelo_id in ids_esperados:
        variantes = bloque["models"][modelo_id]["variants"]
        assert set(variantes.keys()) == set(NIVELES_ESFUERZO_OPENCODE)
        for nivel in NIVELES_ESFUERZO_OPENCODE:
            assert variantes[nivel] == {"reasoningEffort": nivel}
    # Nada migrado -- se escribió todo de cero, ya con variantes.
    assert resultado.variantes_migradas == ()


def test_generar_opencode_json_deja_el_token_puesto_en_el_entorno_del_proceso(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Efecto colateral que se conserva aunque el 'apiKey' escrito sea el
    token en claro (ver "TERCERA CORRECCIÓN" del docstring del módulo):
    higiene barata y deja el camino listo si una versión futura de opencode
    resuelve '{env:...}' para proveedores custom."""

    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    assert "CLOUDFLARE_API_TOKEN" not in os.environ
    generar_opencode_json(workspace, cuenta_id="c", api_token="token-para-el-entorno")
    assert os.environ["CLOUDFLARE_API_TOKEN"] == "token-para-el-entorno"


@pytest.mark.skipif(
    os.name == "nt", reason="chmod 0600 es POSIX puro -- no hay equivalente en Windows"
)
def test_opencode_json_generado_queda_en_0600(tmp_path: Path) -> None:
    """Encargo, punto 3: verificar el MODO REAL con stat, no confiar en que
    la llamada a chmod se hizo -- la ronda anterior 'lo aplicó' y el
    archivo salió en 0o644."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    resultado = generar_opencode_json(workspace, cuenta_id="c", api_token="t")

    modo = stat.S_IMODE(resultado.ruta.stat().st_mode)
    assert modo == 0o600, (
        f"opencode.json quedó con permisos {oct(modo)}, no 0600 -- lleva la ruta de la "
        "cuenta de Cloudflare y es legible por cualquier usuario del sistema"
    )


# --------------------------------------------------------------------------- #
# Windows -- icacls (encargo, punto 1: "chmod 0600 es una operación vacía en
# Windows").
#
# IMPORTANTE, honesto a propósito (regla 6 del encargo -- "nada simulado" --
# y regla dura de esta ronda -- "nunca dar por bueno algo en Windows sin
# haberlo ejecutado allí"): esta Mac NO tiene ``icacls``, así que ningún test
# de aquí abajo puede confirmar que el archivo queda de verdad inaccesible
# para ``BUILTIN\Users`` en Windows -- eso requiere la VM, fuera de alcance
# de este encargo ("no te conectes"). Lo que SÍ prueban estos tests, de
# verdad y sin mocks de más, es:
#
# 1. La construcción del comando (``_restringir_windows`` con
#    ``subprocess.run`` reemplazado, para inspeccionar el argv exacto).
# 2. El manejo de error cuando ``icacls`` no existe -- y en esta Mac
#    literalmente NO existe, así que ``test_restringir_windows_en_esta_mac_
#    no_revienta_aunque_icacls_no_exista`` ejercita la rama real de
#    "el binario no está" (``FileNotFoundError``), no una simulación de ella.
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
        ide_opencode_config_module.getpass, "getuser", lambda: "ada-posix"
    )
    assert _cuenta_windows_actual() == "ada-posix"


def test_cuenta_windows_actual_devuelve_none_si_nada_resuelve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("USERDOMAIN", raising=False)
    monkeypatch.delenv("USERNAME", raising=False)

    def _getuser_revienta() -> str:
        raise OSError("no hay un usuario que consultar")

    monkeypatch.setattr(ide_opencode_config_module.getpass, "getuser", _getuser_revienta)
    assert _cuenta_windows_actual() is None


def test_restringir_windows_arma_el_comando_icacls_esperado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ver el comando exacto que se verificó en vivo (con una cuenta fija)
    en MEMORY.md de la fase anterior: ``icacls <ruta> /inheritance:r
    /grant:r <cuenta>:F /grant:r SYSTEM:F``, nunca ``shell=True``."""

    monkeypatch.setenv("USERDOMAIN", "MI-PC")
    monkeypatch.setenv("USERNAME", "ada")
    ruta = tmp_path / "opencode.json"
    ruta.write_text("{}", encoding="utf-8")

    llamadas: list[tuple[list[str], dict]] = []

    def _run_falso(comando: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        llamadas.append((comando, kwargs))
        return subprocess.CompletedProcess(comando, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ide_opencode_config_module.subprocess, "run", _run_falso)

    _restringir_windows(ruta)

    assert len(llamadas) == 1
    comando, kwargs = llamadas[0]
    assert comando[0] == "icacls"
    assert comando[1] == str(ruta)
    assert "/inheritance:r" in comando
    assert "/grant:r" in comando
    assert "MI-PC\\ada:F" in comando
    assert "SYSTEM:F" in comando
    # Nunca shell=True -- mismo criterio que edecan_mcp.transport._argv_ejecutable.
    assert kwargs.get("shell") is not True


# --------------------------------------------------------------------------- #
# Cierre de Windows -- ronda de validación en vivo contra la VM real (EC2,
# Windows Server 2022, standalone/workgroup) confirmó que _cuenta_windows_actual
# sola NO alcanza: USERDOMAIN vale literalmente "WORKGROUP" ahí, e icacls
# rechaza "WORKGROUP\usuario" con el código 1332 ("No mapping between
# account names and security IDs was done") -- el opencode.json con el
# token de Cloudflare quedaba exactamente igual de legible por
# BUILTIN\Users que antes del arreglo original. Estos tests cubren el
# candidato de respaldo (".\usuario", cuenta local) que sí resuelve en
# ambos casos (workgroup y dominio real).
# --------------------------------------------------------------------------- #


def test_candidatas_con_dominio_real_prueba_dominio_primero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("USERDOMAIN", "MI-PC")
    monkeypatch.setenv("USERNAME", "ada")
    assert _cuentas_windows_candidatas() == ["MI-PC\\ada", ".\\ada"]


def test_candidatas_con_workgroup_prueba_cuenta_local_antes_que_el_workgroup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El caso medido en vivo contra la VM real: USERDOMAIN="WORKGROUP" no
    es un dominio resoluble por icacls -- el candidato local (".\\usuario")
    debe probarse ANTES, no después, para no perder tiempo con un intento
    que sabemos que va a fallar con el código 1332."""

    monkeypatch.setenv("USERDOMAIN", "WORKGROUP")
    monkeypatch.setenv("USERNAME", "administrator")
    assert _cuentas_windows_candidatas() == [".\\administrator", "WORKGROUP\\administrator"]


def test_candidatas_sin_username_ni_userdomain_cae_a_getpass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("USERDOMAIN", raising=False)
    monkeypatch.delenv("USERNAME", raising=False)
    monkeypatch.setattr(
        ide_opencode_config_module.getpass, "getuser", lambda: "ada-posix"
    )
    assert _cuentas_windows_candidatas() == ["ada-posix"]


def test_candidatas_sin_nada_resuelto_devuelve_lista_vacia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("USERDOMAIN", raising=False)
    monkeypatch.delenv("USERNAME", raising=False)

    def _getuser_revienta() -> str:
        raise OSError("no hay un usuario que consultar")

    monkeypatch.setattr(ide_opencode_config_module.getpass, "getuser", _getuser_revienta)
    assert _cuentas_windows_candidatas() == []


def test_restringir_windows_con_workgroup_reintenta_con_cuenta_local_y_tiene_exito(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduce el hallazgo REAL de la ronda de validación: con
    USERDOMAIN=WORKGROUP (la VM real, standalone), el primer candidato
    (".\\administrator", probado primero por la corrección de esta ronda)
    tiene éxito de inmediato -- WORKGROUP\\administrator, que fue el que
    falló en vivo con el código 1332, ni se llega a intentar."""

    monkeypatch.setenv("USERDOMAIN", "WORKGROUP")
    monkeypatch.setenv("USERNAME", "administrator")
    ruta = tmp_path / "opencode.json"
    ruta.write_text("{}", encoding="utf-8")

    llamadas: list[list[str]] = []

    def _run_falso(comando: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        llamadas.append(comando)
        if comando[4] == ".\\administrator:F":
            return subprocess.CompletedProcess(comando, returncode=0, stdout="", stderr="")
        # El candidato con el nombre de workgroup literal es exactamente el
        # que falló en vivo contra la VM real, con este mismo código.
        return subprocess.CompletedProcess(
            comando,
            returncode=1332,
            stdout="Successfully processed 0 files; Failed processing 1 files",
            stderr="WORKGROUP\\administrator: No mapping between account names and "
            "security IDs was done.",
        )

    monkeypatch.setattr(ide_opencode_config_module.subprocess, "run", _run_falso)

    _restringir_windows(ruta)

    assert len(llamadas) == 1
    assert llamadas[0][4] == ".\\administrator:F"


def test_restringir_windows_si_el_primer_candidato_falla_prueba_el_siguiente(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Con un dominio AD real que por lo que sea rechaza el primer intento
    (p.ej. cuenta de servicio sin permiso para resolver SIDs de dominio),
    la función debe seguir con el siguiente candidato en vez de darse por
    vencida en el primer fallo."""

    monkeypatch.setenv("USERDOMAIN", "MI-DOMINIO")
    monkeypatch.setenv("USERNAME", "ada")
    ruta = tmp_path / "opencode.json"
    ruta.write_text("{}", encoding="utf-8")

    llamadas: list[list[str]] = []

    def _run_falso(comando: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        llamadas.append(comando)
        if comando[4] == "MI-DOMINIO\\ada:F":
            return subprocess.CompletedProcess(comando, returncode=1332, stdout="", stderr="")
        return subprocess.CompletedProcess(comando, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ide_opencode_config_module.subprocess, "run", _run_falso)

    _restringir_windows(ruta)

    assert [c[4] for c in llamadas] == ["MI-DOMINIO\\ada:F", ".\\ada:F"]


def test_restringir_windows_sin_cuenta_resuelta_no_llama_a_icacls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ruta = tmp_path / "opencode.json"
    ruta.write_text("{}", encoding="utf-8")

    llamadas: list[list[str]] = []
    monkeypatch.setattr(
        ide_opencode_config_module,
        "_cuenta_windows_actual",
        lambda: None,
    )
    monkeypatch.setattr(
        ide_opencode_config_module.subprocess,
        "run",
        lambda comando, **kwargs: llamadas.append(comando),
    )

    _restringir_windows(ruta)  # no debe reventar

    assert llamadas == []


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

    monkeypatch.setattr(ide_opencode_config_module.subprocess, "run", _run_falla)

    _restringir_windows(ruta)  # best-effort: no debe lanzar nada


def test_restringir_windows_binario_no_encontrado_no_revienta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("USERDOMAIN", "MI-PC")
    monkeypatch.setenv("USERNAME", "ada")
    ruta = tmp_path / "opencode.json"
    ruta.write_text("{}", encoding="utf-8")

    def _run_sin_binario(comando: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        raise FileNotFoundError("icacls no está en el PATH")

    monkeypatch.setattr(ide_opencode_config_module.subprocess, "run", _run_sin_binario)

    _restringir_windows(ruta)  # best-effort: no debe lanzar nada


def test_restringir_windows_en_esta_mac_no_revienta_aunque_icacls_no_exista(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Sin mockear ``subprocess.run``: esta Mac de verdad no tiene ``icacls``,
    así que esto ejercita la rama real de "el binario no está" (no una
    simulación) y confirma que ``_restringir_windows`` no revienta y deja un
    aviso real en el log."""

    monkeypatch.setenv("USERDOMAIN", "MI-PC")
    monkeypatch.setenv("USERNAME", "ada")
    ruta = tmp_path / "opencode.json"
    ruta.write_text("{}", encoding="utf-8")

    with caplog.at_level("WARNING", logger=ide_opencode_config_module.logger.name):
        _restringir_windows(ruta)  # no debe lanzar nada

    assert any("icacls" in registro.message for registro in caplog.records)


def test_asegurar_permisos_restrictivos_en_windows_llama_a_restringir_windows_no_a_chmod(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ruta = tmp_path / "opencode.json"
    ruta.write_text("{}", encoding="utf-8")

    llamadas_windows: list[Path] = []
    llamadas_chmod: list[tuple[Path, int]] = []
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(
        ide_opencode_config_module, "_restringir_windows", llamadas_windows.append
    )
    monkeypatch.setattr(
        ide_opencode_config_module.os,
        "chmod",
        lambda ruta, modo: llamadas_chmod.append((ruta, modo)),
    )

    _asegurar_permisos_restrictivos(ruta)

    assert llamadas_windows == [ruta]
    assert llamadas_chmod == []


def test_workspace_inexistente_da_error_claro(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no existe"):
        generar_opencode_json(
            tmp_path / "no-existe",
            cuenta_id="c",
            api_token="t",
        )


def test_sin_credenciales_en_entorno_ni_env_da_error_claro(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Test frágil detectado por un verificador: sin este `delenv` explícito,
    # este test asume que quien lo corre no tiene CLOUDFLARE_ACCOUNT_ID/
    # CLOUDFLARE_API_TOKEN exportadas en su propio shell -- si las tiene
    # (p. ej. para poder correr los tests reales de opencode/Cloudflare de
    # este mismo archivo), el test fallaba en falso porque el entorno SÍ
    # traía credenciales, aunque el `.env` de prueba estuviera vacío. Se
    # limpia el entorno del proceso de test explícitamente, para que el
    # resultado dependa solo de lo que este test controla, nunca del shell
    # de quien lo invoque.
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(CredencialesCloudflareFaltantesError, match="CLOUDFLARE_ACCOUNT_ID"):
        generar_opencode_json(workspace, ruta_env=_env_vacio(tmp_path))


# --------------------------------------------------------------------------- #
# Nunca pisar lo que el dueño ya escribió
# --------------------------------------------------------------------------- #


def test_no_pisa_provider_workersai_preexistente(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_del_dueno = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            PROVEEDOR_WORKERS_AI_ID: {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Config a mano del dueño",
                "options": {
                    "baseURL": "https://algo-que-el-dueno-configuro",
                    "apiKey": "su-propio-token",
                },
                "models": {"@cf/algo/que-el-eligio": {"name": "Su modelo"}},
            }
        },
        # También trae su propio 'permission' -- si no lo pusiera, esta
        # función SÍ lo agregaría (es un bloque independiente de 'provider',
        # ver test_no_pisa_permission_preexistente_pero_si_agrega_provider),
        # y entonces el archivo no quedaría byte-por-byte igual, que es
        # justo lo que este test en particular quiere comprobar sobre
        # 'provider'.
        "permission": {"edit": "ask"},
    }
    ruta_json = workspace / "opencode.json"
    ruta_json.write_text(json.dumps(config_del_dueno), encoding="utf-8")
    texto_antes = ruta_json.read_text(encoding="utf-8")

    resultado = generar_opencode_json(workspace, cuenta_id="c", api_token="t")

    assert resultado.proveedor_escrito is False
    assert resultado.creado is False
    assert resultado.advertencia_proveedor is not None
    assert "se deja tal cual" in resultado.advertencia_proveedor
    assert resultado.permiso_escrito is False
    assert resultado.advertencia_permiso is not None
    assert "se deja tal cual" in resultado.advertencia_permiso
    assert resultado.aviso_credencial is None
    # El archivo queda BYTE POR BYTE igual -- la prueba dura de "nunca destruyas".
    assert ruta_json.read_text(encoding="utf-8") == texto_antes


def test_agrega_permission_aunque_provider_ya_exista(tmp_path: Path) -> None:
    """'provider' y 'permission' se combinan POR SEPARADO: si el dueño ya
    tenía su proveedor pero nunca puso 'permission', esta función SÍ agrega
    el bloque de permisos aunque no toque el proveedor -- son dos preguntas
    distintas, cada una con su propia respuesta (ver el docstring de
    ResultadoGeneracionOpencodeJson)."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_del_dueno = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            PROVEEDOR_WORKERS_AI_ID: {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Config a mano del dueño",
                "options": {
                    "baseURL": "https://algo-que-el-dueno-configuro",
                    "apiKey": "su-propio-token",
                },
                "models": {"@cf/algo/que-el-eligio": {"name": "Su modelo"}},
            }
        },
    }
    ruta_json = workspace / "opencode.json"
    ruta_json.write_text(json.dumps(config_del_dueno), encoding="utf-8")

    resultado = generar_opencode_json(workspace, cuenta_id="c", api_token="t")

    assert resultado.proveedor_escrito is False  # el proveedor del dueño no se toca
    assert resultado.permiso_escrito is True  # pero el bloque de permisos sí se agrega
    contenido = json.loads(ruta_json.read_text(encoding="utf-8"))
    assert contenido["provider"][PROVEEDOR_WORKERS_AI_ID]["name"] == "Config a mano del dueño"
    assert contenido["permission"] == PERMISOS_POR_DEFECTO


def test_migra_variantes_a_un_provider_generado_por_este_modulo_antes(tmp_path: Path) -> None:
    """El caso que motivó la migración (ver "Migración" en "CUARTA
    CORRECCIÓN" del docstring del módulo): un workspace con un
    ``opencode.json`` que ESTA MISMA función ya generó en una corrida
    anterior (mismo ``name``/``npm`` que ella siempre escribe), de antes de
    que declarara variantes. Una nueva llamada tiene que agregarle
    ``variants`` a los modelos que ya tenía, sin tocar nada más: ni el
    ``apiKey``/``baseURL`` que ya traía, ni un modelo agregado a mano fuera
    del catálogo, ni un modelo que ya tuviera su propio ``variants``
    (personalización real, no se pisa)."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_generada_antes = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            PROVEEDOR_WORKERS_AI_ID: {
                # Mismos "name"/"npm" que escribe generar_opencode_json --
                # es justo la heurística que la identifica como "nuestra".
                "npm": "@ai-sdk/openai-compatible",
                "name": "Cloudflare Workers AI",
                "options": {
                    "baseURL": "https://api.cloudflare.com/client/v4/accounts/vieja-cuenta/ai/v1",
                    "apiKey": "token-de-una-corrida-anterior",
                },
                "models": {
                    # Sin 'variants' -- así salía antes de esta ronda.
                    "@cf/moonshotai/kimi-k2.7-code": {"name": "Kimi K2.7 Code"},
                    # Ya trae su propio 'variants' -- personalización del
                    # dueño (o de una migración previa): no se debe tocar.
                    "@cf/zai-org/glm-5.2": {
                        "name": "GLM 5.2",
                        "variants": {"alto": {"algoCustom": True}},
                    },
                    # Modelo que el dueño agregó a mano y que ya no está (o
                    # nunca estuvo) en el catálogo actual: se deja intacto.
                    "@cf/algo/que-el-dueno-agrego-a-mano": {"name": "Su modelo"},
                },
            }
        },
        "permission": dict(PERMISOS_POR_DEFECTO),  # ya lo tenía -- no debe cambiar nada aquí
    }
    ruta_json = workspace / "opencode.json"
    ruta_json.write_text(json.dumps(config_generada_antes, indent=2), encoding="utf-8")

    resultado = generar_opencode_json(workspace, cuenta_id="c", api_token="t")

    # El bloque en sí NO se reescribió de cero -- sigue siendo "actualizar en
    # el sitio", no "regenerar".
    assert resultado.proveedor_escrito is False
    assert resultado.advertencia_proveedor is not None
    assert "generado por Edecán en una corrida anterior" in resultado.advertencia_proveedor
    assert "@cf/moonshotai/kimi-k2.7-code" in resultado.variantes_migradas
    # El modelo con 'variants' propio y el que no está en el catálogo actual
    # NUNCA se migran -- no hay nada suyo que agregar.
    assert "@cf/zai-org/glm-5.2" not in resultado.variantes_migradas
    assert "@cf/algo/que-el-dueno-agrego-a-mano" not in resultado.variantes_migradas

    contenido = json.loads(ruta_json.read_text(encoding="utf-8"))
    bloque = contenido["provider"][PROVEEDOR_WORKERS_AI_ID]
    # Lo que ya traía, intacto -- la migración solo AGREGA, nunca reemplaza.
    assert bloque["options"]["apiKey"] == "token-de-una-corrida-anterior"
    assert "vieja-cuenta" in bloque["options"]["baseURL"]
    assert bloque["models"]["@cf/algo/que-el-dueno-agrego-a-mano"] == {"name": "Su modelo"}
    assert bloque["models"]["@cf/zai-org/glm-5.2"]["variants"] == {"alto": {"algoCustom": True}}
    # Y lo que de verdad faltaba, agregado con los nombres literales correctos.
    variantes_kimi = bloque["models"]["@cf/moonshotai/kimi-k2.7-code"]["variants"]
    assert set(variantes_kimi.keys()) == set(NIVELES_ESFUERZO_OPENCODE)
    for nivel in NIVELES_ESFUERZO_OPENCODE:
        assert variantes_kimi[nivel] == {"reasoningEffort": nivel}


def test_migracion_no_reescribe_si_ya_tenia_todas_las_variantes(tmp_path: Path) -> None:
    """Si el provider generado antes por este módulo YA tiene 'variants' en
    todos sus modelos (p. ej. una corrida de esta misma ronda ya lo migró),
    una llamada más no debe reportar nada migrado ni reescribir el archivo
    sin necesidad."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    variantes = {nivel: {"reasoningEffort": nivel} for nivel in NIVELES_ESFUERZO_OPENCODE}
    config_ya_migrada = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            PROVEEDOR_WORKERS_AI_ID: {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Cloudflare Workers AI",
                "options": {"baseURL": "https://algo", "apiKey": "t"},
                "models": {
                    "@cf/moonshotai/kimi-k2.7-code": {"name": "Kimi", "variants": variantes}
                },
            }
        },
        "permission": dict(PERMISOS_POR_DEFECTO),
    }
    ruta_json = workspace / "opencode.json"
    ruta_json.write_text(json.dumps(config_ya_migrada), encoding="utf-8")

    resultado = generar_opencode_json(workspace, cuenta_id="c", api_token="t")

    assert resultado.variantes_migradas == ()
    assert resultado.proveedor_escrito is False
    assert resultado.permiso_escrito is False


def test_no_migra_variantes_a_un_provider_del_dueno_aunque_le_falten(tmp_path: Path) -> None:
    """La heurística de "esto lo generó este módulo" exige el MISMO
    ``name``/``npm`` que él siempre escribe -- un ``provider.workersai``
    real del dueño, con su propio ``name``, nunca se toca, aunque a sus
    modelos igual les falte ``variants``. Falso positivo aquí sería pisar
    configuración humana, justo lo que la regla 2 del encargo prohíbe."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_del_dueno = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            PROVEEDOR_WORKERS_AI_ID: {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Config a mano del dueño",  # distinto -- no es "nuestro"
                "options": {"baseURL": "https://algo-del-dueno", "apiKey": "su-token"},
                "models": {"@cf/moonshotai/kimi-k2.7-code": {"name": "Su Kimi"}},
            }
        },
    }
    ruta_json = workspace / "opencode.json"
    ruta_json.write_text(json.dumps(config_del_dueno), encoding="utf-8")

    resultado = generar_opencode_json(workspace, cuenta_id="c", api_token="t")

    assert resultado.variantes_migradas == ()
    assert resultado.proveedor_escrito is False
    contenido = json.loads(ruta_json.read_text(encoding="utf-8"))
    assert "variants" not in contenido["provider"][PROVEEDOR_WORKERS_AI_ID]["models"][
        "@cf/moonshotai/kimi-k2.7-code"
    ]


def test_no_pisa_permission_preexistente(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_del_dueno = {
        "$schema": "https://opencode.ai/config.json",
        "permission": {"edit": "allow"},  # el dueño decidió confiar sin preguntar
    }
    ruta_json = workspace / "opencode.json"
    ruta_json.write_text(json.dumps(config_del_dueno), encoding="utf-8")

    resultado = generar_opencode_json(workspace, cuenta_id="c", api_token="t")

    assert resultado.permiso_escrito is False
    assert resultado.advertencia_permiso is not None
    assert "se deja tal cual" in resultado.advertencia_permiso
    assert resultado.proveedor_escrito is True  # el proveedor sí faltaba y sí se agrega
    contenido = json.loads(ruta_json.read_text(encoding="utf-8"))
    assert contenido["permission"] == {"edit": "allow"}  # intacto, no se pisó


def test_combina_sin_tocar_otras_claves_del_dueno(tmp_path: Path) -> None:
    """Si el dueño ya tenía opencode.json pero SIN provider.workersai (p. ej.
    solo configuró 'agent'), este módulo agrega el proveedor sin tocar lo
    demás -- eso es "combinar", no "pisar"."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_del_dueno = {
        "$schema": "https://opencode.ai/config.json",
        "agent": {"build": {"model": "algo/que-el-dueno-eligio"}},
        "provider": {"otro-proveedor": {"npm": "algo", "name": "Otro", "models": {}}},
    }
    ruta_json = workspace / "opencode.json"
    ruta_json.write_text(json.dumps(config_del_dueno), encoding="utf-8")

    resultado = generar_opencode_json(workspace, cuenta_id="c", api_token="t")

    assert resultado.proveedor_escrito is True
    assert resultado.creado is False  # el archivo YA existía
    contenido = json.loads(ruta_json.read_text(encoding="utf-8"))
    # Lo del dueño sigue intacto.
    assert contenido["agent"] == {"build": {"model": "algo/que-el-dueno-eligio"}}
    assert contenido["provider"]["otro-proveedor"] == {"npm": "algo", "name": "Otro", "models": {}}
    # Y ahora también está lo nuestro, agregado.
    assert PROVEEDOR_WORKERS_AI_ID in contenido["provider"]


def test_opencode_json_existente_pero_invalido_no_se_toca(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ruta_json = workspace / "opencode.json"
    ruta_json.write_text("{ esto no es json valido", encoding="utf-8")
    texto_antes = ruta_json.read_text(encoding="utf-8")

    with pytest.raises(ErrorConfiguracionOpencode, match="no es JSON válido"):
        generar_opencode_json(workspace, cuenta_id="c", api_token="t")

    assert ruta_json.read_text(encoding="utf-8") == texto_antes


def test_provider_que_no_es_objeto_da_error_claro_sin_tocar_archivo(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ruta_json = workspace / "opencode.json"
    ruta_json.write_text(json.dumps({"provider": "esto-deberia-ser-un-objeto"}), encoding="utf-8")
    texto_antes = ruta_json.read_text(encoding="utf-8")

    with pytest.raises(ErrorConfiguracionOpencode, match="no es un objeto"):
        generar_opencode_json(workspace, cuenta_id="c", api_token="t")

    assert ruta_json.read_text(encoding="utf-8") == texto_antes


# --------------------------------------------------------------------------- #
# Comprobación REAL contra Cloudflare -- sin doble, sin mock: se llama a la
# API real de Cloudflare con un token real y con uno inventado.
# --------------------------------------------------------------------------- #


async def test_credencial_invalida_da_el_error_real_de_cloudflare() -> None:
    """No hace falta una cuenta real para este: Cloudflare rechaza CUALQUIER
    token que no autentique, así que esto es una llamada real a la API real
    de Cloudflare, y el error que se comprueba es el que ELLOS mandan."""

    with pytest.raises(CredencialCloudflareInvalidaError) as exc_info:
        await comprobar_credencial_cloudflare(
            "00000000000000000000000000000000", "esto-no-es-un-token-valido-de-verdad"
        )
    error = exc_info.value
    assert "Cloudflare rechazó la credencial" in str(error)
    # El código/mensaje son los que Cloudflare mandó de verdad -- no se
    # inventan aquí. Se comprobó a mano que hoy Cloudflare manda 9106
    # "Authentication failed", pero no se ata el test a ese número exacto por
    # si Cloudflare cambia el código algún día -- lo que importa es que HAY
    # un código real y un mensaje real, no un texto genérico de este módulo.
    assert error.codigo is not None


@requiere_credenciales
async def test_credencial_real_pasa_la_validacion() -> None:
    # No debe lanzar nada -- si esto falla, las credenciales de esta Mac ya
    # no sirven, que es información real y útil.
    await comprobar_credencial_cloudflare(_CUENTA_CLOUDFLARE, _TOKEN_CLOUDFLARE)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Comprobación REAL contra opencode -- arranca el servidor de verdad
# --------------------------------------------------------------------------- #


@requiere_credenciales
async def test_generar_y_comprobar_extremo_a_extremo(tmp_path: Path) -> None:
    """El camino completo del encargo: generar el opencode.json a partir de
    config/modelos.yml real y credenciales reales, arrancar opencode de
    verdad sobre ese workspace, y confirmar contra SU respuesta (no contra lo
    que nosotros escribimos) que el proveedor y sus modelos quedaron
    registrados."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    generado = generar_opencode_json(workspace)
    assert generado.proveedor_escrito is True

    info = await comprobar_proveedor_registrado(workspace, timeout_arranque=30.0)
    assert info.proveedor_id == PROVEEDOR_WORKERS_AI_ID
    assert info.puerto > 0
    assert set(info.modelos) == set(generado.modelos)


@requiere_credenciales
async def test_variante_de_esfuerzo_se_fija_en_vivo_y_el_modelo_sigue_respondiendo(
    tmp_path: Path,
) -> None:
    """El test que el encargo pide explícitamente ("sin ese test, no está
    hecho"): arranca opencode DE VERDAD sobre el ``opencode.json`` que
    genera esta ronda (ya con ``variants`` por modelo), fija
    ``variant: "high"`` con un ``POST /session/{id}/model`` real y
    comprueba dos cosas, ninguna simulada:

    1. La API responde **HTTP 204** -- el código exacto que documenta
       ``GET /doc`` para ``v2.session.switchModel`` (ver "CUARTA
       CORRECCIÓN" en el docstring del módulo). Se llama por httpx crudo,
       no vía ``ServidorOpencode.cambiar_modelo`` (que traga el código de
       estado y solo expone "no lanzó excepción") -- este test verifica el
       código, no una inferencia sobre él.
    2. El modelo sigue respondiendo DESPUÉS de fijar la variante -- un
       prompt real, leído de ``GET /session/{id}/message``, con
       ``model.variant == "high"`` en el mensaje del asistente. Confirma
       que la variante no solo se acepta, se usa: fijar un ``variant`` mal
       formado también puede devolver 204 y dejar la sesión muda en el
       primer turno, que es justo el tipo de "se ve bien pero no funciona"
       que este proyecto ya sufrió con la referencia ``{env:...}`` (ver
       "TERCERA CORRECCIÓN").
    """

    import httpx
    from edecan_companion.ide_opencode import ServidorOpencode

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # Credenciales EXPLÍCITAS a propósito, no las que resuelva del entorno:
    # varios tests de arriba en este mismo archivo llaman a
    # generar_opencode_json(..., cuenta_id="c", api_token="t") y ese "t"
    # queda puesto en os.environ["CLOUDFLARE_API_TOKEN"] como efecto
    # colateral documentado (ver "Lo que SÍ se queda de la idea original",
    # punto 2, en el docstring del módulo) -- sin pasar la credencial real
    # explícita aquí, este test heredaría ese token falso según el orden en
    # que pytest ejecute los tests del archivo, y fallaría con un 401 real
    # que NADA tiene que ver con las variantes que se están probando
    # (se reprodujo exactamente así antes de agregar estos dos parámetros).
    generado = generar_opencode_json(
        workspace, cuenta_id=_CUENTA_CLOUDFLARE, api_token=_TOKEN_CLOUDFLARE
    )
    assert generado.proveedor_escrito is True
    modelo_id = "@cf/moonshotai/kimi-k2.7-code"
    assert modelo_id in generado.modelos, (
        "este test fija la variante contra kimi-k2.7-code -- si algún día sale de "
        "modelos_ide en config/modelos.yml, cambia este id por otro de generado.modelos"
    )

    servidor = await ServidorOpencode.iniciar(workspace, timeout_arranque=30.0)
    try:
        sesion = await servidor.crear_sesion(
            directorio=workspace,
            agente="build",
            proveedor=PROVEEDOR_WORKERS_AI_ID,
            modelo=modelo_id,
        )

        # 1. POST /session/{id}/model crudo -- comprobar el CÓDIGO real, no
        # solo que no lance excepción.
        async with httpx.AsyncClient(
            base_url=servidor.base_url, timeout=httpx.Timeout(15.0)
        ) as cliente:
            resp = await cliente.post(
                f"/session/{sesion.id}/model",
                json={
                    "model": {
                        "providerID": PROVEEDOR_WORKERS_AI_ID,
                        "id": modelo_id,
                        "variant": "high",
                    }
                },
            )
        assert resp.status_code == 204, (
            f"opencode devolvió {resp.status_code} al fijar variant='high' -- se esperaba "
            f"204 (ver #/paths/~1session~1{{id}}~1model en GET /doc). Cuerpo: {resp.text[:300]!r}"
        )

        # 2. El modelo de verdad responde con esa variante activa -- no un
        # 401/cuelgue disfrazado de éxito.
        await servidor.enviar_prompt(sesion.id, "Responde solo con la palabra OK.")
        fallo_data: dict | None = None
        completo = False
        # Nombres de evento reales -- ver ide_opencode_eventos.py, la unión de
        # 27/28 variantes de SessionDurableEvent comprobada contra un
        # opencode serve real: "step.failed" es el terminal de error,
        # "step.ended" el de éxito (no existe "step.completed").
        async for ev in servidor.eventos(sesion.id, tiempo_maximo_sin_eventos=30.0):
            if ev.type == "session.next.step.failed":
                fallo_data = ev.data
                break
            if ev.type == "session.next.step.ended":
                completo = True
                break
        assert fallo_data is None, (
            f"el turno falló con variant='high' fijado -- evento real: {fallo_data}"
        )
        assert completo, "el turno nunca terminó -- ver tiempo_maximo_sin_eventos"

        async with httpx.AsyncClient(
            base_url=servidor.base_url, timeout=httpx.Timeout(15.0)
        ) as cliente:
            resp_mensajes = await cliente.get(f"/session/{sesion.id}/message")
        resp_mensajes.raise_for_status()
        mensajes = resp_mensajes.json()["data"]
        mensajes_asistente = [m for m in mensajes if m.get("type") == "assistant"]
        assert mensajes_asistente, "no llegó ningún mensaje del asistente"
        ultimo = mensajes_asistente[0]  # el listado viene en orden descendente
        assert ultimo["model"]["variant"] == "high"
        textos = [
            parte.get("text", "")
            for parte in ultimo.get("content", [])
            if parte.get("type") == "text"
        ]
        assert any(texto.strip() for texto in textos), (
            "el mensaje del asistente no trae texto -- la variante se fijó (204) pero el "
            "modelo no completó una respuesta real"
        )
    finally:
        await servidor.detener()


@requiere_credenciales
async def test_referencia_env_no_sirve_para_una_llamada_real_hallazgo_documentado(
    tmp_path: Path,
) -> None:
    """Prueba que fija en la suite el hallazgo de la sección "TERCERA
    CORRECCIÓN" del docstring del módulo: con
    ``apiKey: "{env:CLOUDFLARE_API_TOKEN}"`` (en vez del token en claro que
    escribe ``generar_opencode_json``), CLOUDFLARE_API_TOKEN correctamente
    puesta en el entorno de ESTE proceso, y un ``opencode serve`` real, la
    sesión NUNCA logra responder -- falla con HTTP 401 casi al instante. Se
    deja fijado como test (no solo como nota en el docstring) para que si
    una futura versión de opencode arregla esto, algo lo note y grite en vez
    de quedar como una suposición no verificada otra vez."""

    import os as _os

    from edecan_companion.ide_opencode import ServidorOpencode

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ruta_json = workspace / "opencode.json"
    ruta_json.write_text(
        json.dumps(
            {
                "$schema": "https://opencode.ai/config.json",
                "provider": {
                    PROVEEDOR_WORKERS_AI_ID: {
                        "npm": "@ai-sdk/openai-compatible",
                        "name": "Cloudflare Workers AI",
                        "options": {
                            "baseURL": (
                                f"https://api.cloudflare.com/client/v4/accounts/"
                                f"{_CUENTA_CLOUDFLARE}/ai/v1"
                            ),
                            "apiKey": _REFERENCIA_ENV_TOKEN,
                        },
                        "models": {"@cf/moonshotai/kimi-k2.7-code": {"name": "kimi"}},
                    }
                },
                "permission": {"edit": "ask", "bash": "ask", "webfetch": "ask"},
            }
        ),
        encoding="utf-8",
    )
    _os.environ["CLOUDFLARE_API_TOKEN"] = _TOKEN_CLOUDFLARE  # type: ignore[arg-type]

    servidor = await ServidorOpencode.iniciar(workspace)
    try:
        sesion = await servidor.crear_sesion(
            directorio=workspace,
            agente="build",
            proveedor=PROVEEDOR_WORKERS_AI_ID,
            modelo="@cf/moonshotai/kimi-k2.7-code",
        )
        await servidor.enviar_prompt(sesion.id, "di hola")
        fallo_401 = False
        async for ev in servidor.eventos(sesion.id, tiempo_maximo_sin_eventos=15.0):
            if ev.type == "session.next.step.failed":
                assert "401" in str(ev.data.get("error", {}))
                fallo_401 = True
                break
        assert fallo_401, (
            "la referencia {env:CLOUDFLARE_API_TOKEN} respondió con éxito -- si esto falla "
            "es BUENA noticia (una versión nueva de opencode lo arregló): avisa al dueño y "
            "retoma generar_opencode_json() con la referencia en vez del token en claro."
        )
    finally:
        await servidor.detener()


async def test_comprobar_proveedor_sin_opencode_json_da_error_claro(tmp_path: Path) -> None:
    """Workspace real, opencode arrancado de verdad, pero SIN opencode.json:
    tiene que decir con claridad que no hay proveedor registrado -- no
    colgarse ni fingir que sí lo hay."""

    workspace = tmp_path / "workspace-sin-config"
    workspace.mkdir()
    with pytest.raises(ErrorConfiguracionOpencode, match="no tiene registrado"):
        await comprobar_proveedor_registrado(workspace, timeout_arranque=30.0)
