"""Tests de ``ide_opencode_permisos.py`` -- los cuatro modos, contra la API
de permisos real de opencode.

Dos grupos, mismo criterio que ``test_ide_opencode.py``:

1. Puros (``test_decidir_*``, ``test_clasificar_accion_*``): la tabla de
   decisión y la clasificación son funciones deterministas, no hace falta un
   servidor para probarlas.
2. Reales (todo lo demás): arrancan un ``opencode serve`` de verdad, con
   Workers AI real, y comprueban el EFECTO EN DISCO -- no lo que diga el
   modelo en el chat. Por encargo explícito: "que en Manual una escritura de
   verdad no ocurre hasta que se concede, y que en Auto ocurre sola pero un
   borrado para. Contra el servidor real y un workspace temporal,
   comprobando el disco." Se saltan solos si faltan las credenciales de
   Cloudflare (mismo criterio que ``test_ide_opencode.py``: se salta, no se
   simula).
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from edecan_companion.ide_opencode import ServidorOpencode
from edecan_companion.ide_opencode_permisos import (
    ClaseAccion,
    ModoAgente,
    NoSePuedeRecordarError,
    PuenteDePermisos,
    ResultadoPermiso,
    SolicitudPermiso,
    clasificar_accion,
    decidir,
)

# --------------------------------------------------------------------------- #
# Credenciales -- mismo criterio que test_ide_opencode.py: se leen, nunca se
# imprimen ni se copian a ningún archivo versionado.
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
        "Sin CLOUDFLARE_ACCOUNT_ID/CLOUDFLARE_API_TOKEN no hay proveedor real "
        "contra el que mandar un prompt -- se salta, no se simula."
    ),
)

_MODELO_PRUEBA = "@cf/moonshotai/kimi-k2.7-code"


def _workspace_con_proveedor(tmp_path: Path) -> Path:
    """Workspace TEMPORAL propio (nunca un repo del dueño) con Workers AI
    registrado y ``permission: ask`` para edit/bash/webfetch -- exactamente
    la config con la que se comprobaron en vivo las formas de
    ``PermissionV2Request`` que usa ``ide_opencode_permisos.py`` (ver su
    docstring). Sin esto, opencode resolvería esas acciones solo, sin crear
    ninguna solicitud que este puente pueda interceptar."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            "workersai": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Cloudflare Workers AI",
                "options": {
                    "baseURL": (
                        f"https://api.cloudflare.com/client/v4/accounts/{_CUENTA_CLOUDFLARE}/ai/v1"
                    ),
                    "apiKey": _TOKEN_CLOUDFLARE,
                },
                "models": {_MODELO_PRUEBA: {"name": "Kimi K2.7 Code"}},
            }
        },
        "permission": {"edit": "ask", "bash": "ask", "webfetch": "ask"},
    }
    (workspace / "opencode.json").write_text(json.dumps(config), encoding="utf-8")
    return workspace


async def _crear_sesion(servidor: ServidorOpencode, directorio: Path):
    return await servidor.crear_sesion(
        directorio=directorio, agente="build", proveedor="workersai", modelo=_MODELO_PRUEBA
    )


async def _disparar_hasta_permiso(
    servidor: ServidorOpencode,
    bridge: PuenteDePermisos,
    session_id: str,
    prompts: list[str],
    coincide: Callable[[SolicitudPermiso], bool],
    *,
    intentos_poll: int = 20,
    espera_poll: float = 1.0,
) -> SolicitudPermiso:
    """Manda prompts reales (con reintento -- ver el porqué en
    ``test_ide_opencode.py``: un modelo real no siempre llama a la misma
    herramienta con el mismo prompt) hasta que aparece una
    ``SolicitudPermiso`` real que cumple ``coincide``. Contra el servidor de
    verdad -- nada de esto se simula."""

    ultimo_visto: list[SolicitudPermiso] = []
    for texto in prompts:
        await servidor.enviar_prompt(session_id, texto)
        for _ in range(intentos_poll):
            pendientes = await bridge.permisos_pendientes(session_id)
            ultimo_visto = pendientes
            coincidentes = [p for p in pendientes if coincide(p)]
            if coincidentes:
                return coincidentes[0]
            await asyncio.sleep(espera_poll)
    raise AssertionError(
        f"tras {len(prompts)} intentos reales contra el proveedor, ninguna solicitud de "
        f"permiso coincidente apareció -- última lista de pendientes vista: {ultimo_visto!r}"
    )


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
    """Sondea el DISCO (no lo que diga el modelo) hasta que ``ruta`` deje de
    tener ``contenido_original``, o se acaben los intentos."""

    for _ in range(intentos):
        if ruta.read_text(encoding="utf-8") != contenido_original:
            return True
        await asyncio.sleep(espera)
    return False


# --------------------------------------------------------------------------- #
# Puros -- la tabla de decisión y la clasificación, sin servidor
# --------------------------------------------------------------------------- #


def test_decidir_cubre_los_16_casos_con_manual_el_mas_restrictivo_y_auto_el_mas_permisivo() -> None:
    for modo in ModoAgente:
        for clase in ClaseAccion:
            assert decidir(modo, clase) in ("permitir", "pedir_aprobacion", "bloquear")

    # Manual: nada que mute se ejecuta sin una persona.
    assert decidir(ModoAgente.MANUAL, ClaseAccion.LECTURA) == "permitir"
    assert decidir(ModoAgente.MANUAL, ClaseAccion.EDICION) == "pedir_aprobacion"
    assert decidir(ModoAgente.MANUAL, ClaseAccion.COMANDO) == "pedir_aprobacion"
    assert decidir(ModoAgente.MANUAL, ClaseAccion.PELIGROSA) == "pedir_aprobacion"

    # Aceptar ediciones: escribir solo, comandos piden permiso.
    assert decidir(ModoAgente.ACEPTAR_EDICIONES, ClaseAccion.EDICION) == "permitir"
    assert decidir(ModoAgente.ACEPTAR_EDICIONES, ClaseAccion.COMANDO) == "pedir_aprobacion"

    # Plan: no toca archivos -- se bloquea, no se pregunta.
    assert decidir(ModoAgente.PLAN, ClaseAccion.EDICION) == "bloquear"
    assert decidir(ModoAgente.PLAN, ClaseAccion.COMANDO) == "bloquear"
    assert decidir(ModoAgente.PLAN, ClaseAccion.PELIGROSA) == "pedir_aprobacion"

    # Auto: todo permitido salvo lo irreversible, SIEMPRE.
    assert decidir(ModoAgente.AUTO, ClaseAccion.EDICION) == "permitir"
    assert decidir(ModoAgente.AUTO, ClaseAccion.COMANDO) == "permitir"
    assert decidir(ModoAgente.AUTO, ClaseAccion.PELIGROSA) == "pedir_aprobacion"


def test_clasificar_accion_usa_los_nombres_reales_de_permissionconfig() -> None:
    assert clasificar_accion("read") == ClaseAccion.LECTURA
    assert clasificar_accion("glob") == ClaseAccion.LECTURA
    assert clasificar_accion("list") == ClaseAccion.LECTURA
    assert clasificar_accion("edit") == ClaseAccion.EDICION
    assert clasificar_accion("bash", ["npm test"]) == ClaseAccion.COMANDO
    assert clasificar_accion("task") == ClaseAccion.COMANDO
    assert clasificar_accion("webfetch") == ClaseAccion.PELIGROSA
    assert clasificar_accion("websearch") == ClaseAccion.PELIGROSA
    assert clasificar_accion("doom_loop") == ClaseAccion.PELIGROSA
    # Falla cerrado: una acción que no está en ninguna lista cae en PELIGROSA.
    assert clasificar_accion("una_herramienta_mcp_no_clasificada") == ClaseAccion.PELIGROSA


def test_clasificar_accion_bash_detecta_patrones_destructivos_propios() -> None:
    # Banderas combinadas y separadas -- ambas cuentan.
    assert clasificar_accion("bash", ["rm -rf build/"]) == ClaseAccion.PELIGROSA
    assert clasificar_accion("bash", ["rm -r -f build/"]) == ClaseAccion.PELIGROSA
    assert clasificar_accion("bash", ["rm --recursive --force build/"]) == ClaseAccion.PELIGROSA
    # rm sin ambas banderas no es PELIGROSA -- sigue siendo COMANDO normal.
    assert clasificar_accion("bash", ["rm archivo.txt"]) == ClaseAccion.COMANDO
    assert clasificar_accion("bash", ["git push origin main"]) == ClaseAccion.PELIGROSA
    assert clasificar_accion("bash", ["git status"]) == ClaseAccion.COMANDO
    assert clasificar_accion("bash", ["sudo reboot"]) == ClaseAccion.PELIGROSA
    assert clasificar_accion("bash", ["ls -la && echo hola"]) == ClaseAccion.COMANDO


# --------------------------------------------------------------------------- #
# Reales -- uno por modo, contra el servidor y el disco de verdad
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@requiere_credenciales
async def test_modo_manual_la_escritura_no_ocurre_hasta_que_se_concede(tmp_path: Path) -> None:
    workspace = _workspace_con_proveedor(tmp_path)
    archivo = workspace / "manual.txt"
    archivo.write_text("original\n", encoding="utf-8")

    async with await ServidorOpencode.iniciar(workspace, timeout_arranque=30.0) as servidor:
        sesion = await _crear_sesion(servidor, workspace)
        async with PuenteDePermisos(
            servidor, obtener_modo=lambda _sid: ModoAgente.MANUAL
        ) as bridge:
            solicitud = await _disparar_hasta_permiso(
                servidor,
                bridge,
                sesion.id,
                [
                    'Sobreescribe manual.txt para que diga exactamente "modificado-manual". '
                    "Usa tu herramienta de escritura de archivos, ejecútalo ya.",
                    'manual.txt sigue sin cambiar. Usa YA la herramienta write sobre manual.txt '
                    'con el contenido "modificado-manual", sin explicaciones previas.',
                ],
                lambda p: p.action == "edit",
            )
            assert solicitud.action == "edit"

            # El corazón de la prueba: en modo Manual, procesar_solicitud NO
            # responde sola -- se queda en pedir_aprobacion.
            resultado = await bridge.procesar_solicitud(solicitud)
            assert resultado.decision == "pedir_aprobacion"
            assert resultado.respondido is False

            # Dale tiempo a opencode: si esto fuera a escribir solo, ya lo
            # habría hecho. No lo hizo -- el archivo sigue como estaba.
            await asyncio.sleep(2.0)
            assert archivo.read_text(encoding="utf-8") == "original\n", (
                "el archivo cambió SIN que nadie lo concediera -- "
                "el modo Manual no está frenando nada"
            )

            # Ahora la persona real concede.
            concedido = await bridge.responder_permiso(sesion.id, solicitud.id, conceder=True)
            assert concedido.respondido is True
            assert concedido.respuesta == "once"

            escribio = await _esperar_archivo_cambie(archivo, "original\n")
            assert escribio, (
                "tras conceder el permiso, el archivo debía cambiar en disco y no cambió"
            )
            assert "modificado-manual" in archivo.read_text(encoding="utf-8")


@pytest.mark.asyncio
@requiere_credenciales
async def test_modo_plan_bloquea_la_edicion_sin_pedir_aprobacion(tmp_path: Path) -> None:
    workspace = _workspace_con_proveedor(tmp_path)
    archivo = workspace / "plan.txt"
    archivo.write_text("original\n", encoding="utf-8")

    async with await ServidorOpencode.iniciar(workspace, timeout_arranque=30.0) as servidor:
        sesion = await _crear_sesion(servidor, workspace)
        async with PuenteDePermisos(servidor, obtener_modo=lambda _sid: ModoAgente.PLAN) as bridge:
            solicitud = await _disparar_hasta_permiso(
                servidor,
                bridge,
                sesion.id,
                [
                    'Sobreescribe plan.txt para que diga "modificado-plan". Usa tu herramienta '
                    "de escritura de archivos ya mismo.",
                    'plan.txt sigue igual. Usa la herramienta write sobre plan.txt con el '
                    'contenido "modificado-plan" ahora.',
                ],
                lambda p: p.action == "edit",
            )

            resultado = await bridge.procesar_solicitud(solicitud)
            # Plan: se BLOQUEA de inmediato, no se pausa a esperar a nadie.
            assert resultado.decision == "bloquear"
            assert resultado.respondido is True
            assert resultado.respuesta == "reject"

            # opencode ya no debe tener esto pendiente -- se respondió sola.
            pendientes = await bridge.permisos_pendientes(sesion.id)
            assert all(p.id != solicitud.id for p in pendientes)

    assert archivo.read_text(encoding="utf-8") == "original\n", (
        "modo plan: el archivo no debía tocarse nunca"
    )


@pytest.mark.asyncio
@requiere_credenciales
async def test_modo_aceptar_ediciones_edita_solo_pero_el_comando_pide_aprobacion(
    tmp_path: Path,
) -> None:
    workspace = _workspace_con_proveedor(tmp_path)
    archivo = workspace / "aceptar.txt"
    archivo.write_text("original\n", encoding="utf-8")
    (workspace / "no_debe_crearse_por_bash.txt").unlink(missing_ok=True)

    async with await ServidorOpencode.iniciar(workspace, timeout_arranque=30.0) as servidor:
        async with PuenteDePermisos(
            servidor, obtener_modo=lambda _sid: ModoAgente.ACEPTAR_EDICIONES
        ) as bridge:
            # 1) La edición va sola.
            sesion_edicion = await _crear_sesion(servidor, workspace)
            solicitud_edicion = await _disparar_hasta_permiso(
                servidor,
                bridge,
                sesion_edicion.id,
                [
                    'Sobreescribe aceptar.txt para que diga "modificado-aceptar". Usa tu '
                    "herramienta de escritura ya.",
                    'aceptar.txt sigue igual. Usa write sobre aceptar.txt con '
                    '"modificado-aceptar" ahora.',
                ],
                lambda p: p.action == "edit",
            )
            resultado_edicion = await bridge.procesar_solicitud(solicitud_edicion)
            assert resultado_edicion.decision == "permitir"
            assert resultado_edicion.respondido is True

            escribio = await _esperar_archivo_cambie(archivo, "original\n")
            assert escribio, "aceptar_ediciones: la edición debía ocurrir sola y no ocurrió"

            # 2) El comando NO va solo -- se queda pidiendo aprobación.
            sesion_comando = await _crear_sesion(servidor, workspace)
            solicitud_comando = await _disparar_hasta_permiso(
                servidor,
                bridge,
                sesion_comando.id,
                [
                    "Ejecuta con tu herramienta de bash el comando: "
                    "touch no_debe_crearse_por_bash.txt -- hazlo ya, sin explicar nada antes.",
                    "Aún no veo el archivo. Usa la herramienta bash YA para correr: "
                    "touch no_debe_crearse_por_bash.txt",
                ],
                lambda p: p.action == "bash",
            )
            resultado_comando = await bridge.procesar_solicitud(solicitud_comando)
            assert resultado_comando.decision == "pedir_aprobacion"
            assert resultado_comando.respondido is False

            await asyncio.sleep(2.0)
            assert not (workspace / "no_debe_crearse_por_bash.txt").exists(), (
                "aceptar_ediciones: un comando de shell corrió solo -- ese modo NO debe permitirlo"
            )

            # Limpieza: rechazar lo que quedó pendiente para no dejar el
            # turno del modelo colgado esperando.
            await bridge.responder_permiso(sesion_comando.id, solicitud_comando.id, conceder=False)


@pytest.mark.asyncio
@requiere_credenciales
async def test_modo_auto_trabaja_solo_pero_para_en_un_borrado_irreversible(tmp_path: Path) -> None:
    workspace = _workspace_con_proveedor(tmp_path)
    archivo = workspace / "auto.txt"
    archivo.write_text("original\n", encoding="utf-8")
    carpeta_a_borrar = workspace / "carpeta_importante"
    carpeta_a_borrar.mkdir()
    (carpeta_a_borrar / "no_se_debe_perder.txt").write_text("dato importante\n", encoding="utf-8")

    async with await ServidorOpencode.iniciar(workspace, timeout_arranque=30.0) as servidor:
        async with PuenteDePermisos(servidor, obtener_modo=lambda _sid: ModoAgente.AUTO) as bridge:
            # 1) La edición ocurre sola -- Auto trabaja sin pedir permiso.
            sesion_edicion = await _crear_sesion(servidor, workspace)
            solicitud_edicion = await _disparar_hasta_permiso(
                servidor,
                bridge,
                sesion_edicion.id,
                [
                    'Sobreescribe auto.txt para que diga "modificado-auto". Usa tu herramienta '
                    "de escritura ya.",
                    'auto.txt sigue igual. Usa write sobre auto.txt con "modificado-auto" ahora.',
                ],
                lambda p: p.action == "edit",
            )
            resultado_edicion = await bridge.procesar_solicitud(solicitud_edicion)
            assert resultado_edicion.decision == "permitir"

            escribio = await _esperar_archivo_cambie(archivo, "original\n")
            assert escribio, "modo auto: la edición debía ocurrir sola y no ocurrió"

            # 2) Un borrado recursivo forzado PARA -- ni siquiera en Auto se
            #    hace solo (regla del encargo: "para en lo irreversible").
            sesion_borrado = await _crear_sesion(servidor, workspace)
            solicitud_borrado = await _disparar_hasta_permiso(
                servidor,
                bridge,
                sesion_borrado.id,
                [
                    "Usa tu herramienta de bash para borrar por completo la carpeta "
                    "carpeta_importante con: rm -rf carpeta_importante -- ejecútalo ya, "
                    "sin preguntar nada antes.",
                    "La carpeta carpeta_importante sigue ahí. Usa la herramienta bash YA para "
                    "correr exactamente: rm -rf carpeta_importante",
                ],
                lambda p: p.action == "bash",
            )
            clase, decision = bridge.clasificar_y_decidir(solicitud_borrado)
            assert clase == ClaseAccion.PELIGROSA, (
                f"el comando real que pidió el modelo no se clasificó como irreversible: "
                f"{solicitud_borrado.resources!r}"
            )
            resultado_borrado = await bridge.procesar_solicitud(solicitud_borrado)
            assert resultado_borrado.decision == "pedir_aprobacion"
            assert resultado_borrado.respondido is False

            await asyncio.sleep(2.0)
            assert carpeta_a_borrar.is_dir(), (
                "modo auto: un borrado recursivo corrió SOLO -- eso es justo lo que debía parar"
            )
            assert (carpeta_a_borrar / "no_se_debe_perder.txt").exists()

            # Limpieza.
            await bridge.responder_permiso(sesion_borrado.id, solicitud_borrado.id, conceder=False)


@pytest.mark.asyncio
@requiere_credenciales
async def test_no_se_puede_recordar_un_si_para_algo_irreversible(tmp_path: Path) -> None:
    """Regla 3 del encargo, con un caso real: intentar ``recordar=True``
    sobre un ``webfetch`` (clase PELIGROSA fija) debe rechazarse -- nunca
    degradarse en silencio a "once"."""

    workspace = _workspace_con_proveedor(tmp_path)

    async with await ServidorOpencode.iniciar(workspace, timeout_arranque=30.0) as servidor:
        sesion = await _crear_sesion(servidor, workspace)
        async with PuenteDePermisos(
            servidor, obtener_modo=lambda _sid: ModoAgente.MANUAL
        ) as bridge:
            solicitud = await _disparar_hasta_permiso(
                servidor,
                bridge,
                sesion.id,
                [
                    "Usa tu herramienta webfetch para obtener el contenido de "
                    "https://example.com y resume el título. Hazlo ya.",
                    "Aún no llamaste a webfetch. Llama YA a la herramienta webfetch con "
                    "https://example.com",
                ],
                lambda p: p.action == "webfetch",
            )
            clase, decision = bridge.clasificar_y_decidir(solicitud)
            assert clase == ClaseAccion.PELIGROSA
            assert decision == "pedir_aprobacion"

            with pytest.raises(NoSePuedeRecordarError):
                await bridge.responder_permiso(
                    sesion.id, solicitud.id, conceder=True, recordar=True
                )

            # La solicitud sigue pendiente -- el intento de recordar no la
            # tocó (ni la concedió ni la guardó).
            pendientes = await bridge.permisos_pendientes(sesion.id)
            assert any(p.id == solicitud.id for p in pendientes)

            # Limpieza: rechazarla de verdad, sin recordar.
            await bridge.responder_permiso(sesion.id, solicitud.id, conceder=False)


@pytest.mark.asyncio
@requiere_credenciales
async def test_la_pregunta_del_agente_se_puede_leer_y_responder(tmp_path: Path) -> None:
    """Encargo punto 5: "el dueño quiere que la IA le hable y le pregunte" --
    este puente expone la pregunta real, no la contesta por su cuenta."""

    workspace = _workspace_con_proveedor(tmp_path)

    async with await ServidorOpencode.iniciar(workspace, timeout_arranque=30.0) as servidor:
        sesion = await _crear_sesion(servidor, workspace)
        async with PuenteDePermisos(servidor, obtener_modo=lambda _sid: ModoAgente.AUTO) as bridge:
            preguntas: list = []
            for texto in (
                "Antes de continuar, usa tu herramienta de preguntar al usuario "
                "(question) para preguntarme: ¿qué nombre le pongo al proyecto? "
                "Dame dos opciones: A) edecan B) opencode. No hagas nada más, solo pregunta.",
                "Todavía no me preguntaste nada. Usa YA la herramienta question con "
                "esa pregunta y esas dos opciones.",
            ):
                await servidor.enviar_prompt(sesion.id, texto)
                encontro = await _esperar(
                    lambda: _hay_preguntas(bridge, sesion.id, preguntas), intentos=20, espera=1.0
                )
                if encontro:
                    break

            assert preguntas, "el modelo real nunca llamó a la herramienta de preguntar"
            pregunta = preguntas[0]
            assert len(pregunta.questions) >= 1
            opciones = [o.label for o in pregunta.questions[0].options]
            assert opciones, "la pregunta real no trajo ninguna opción"

            await bridge.responder_pregunta(sesion.id, pregunta.id, [[opciones[0]]])

            restantes = await bridge.preguntas_pendientes(sesion.id)
            assert all(p.id != pregunta.id for p in restantes), (
                "la pregunta seguía pendiente tras responderla"
            )


async def _hay_preguntas(bridge: PuenteDePermisos, session_id: str, acumulador: list) -> bool:
    pendientes = await bridge.preguntas_pendientes(session_id)
    if pendientes:
        acumulador.extend(pendientes)
        return True
    return False


@pytest.mark.asyncio
@requiere_credenciales
async def test_recuperar_pendientes_procesa_lo_que_ya_estaba_pendiente_antes_de_escuchar(
    tmp_path: Path,
) -> None:
    """Hallazgo 3 del docstring del módulo: el bus vivo (``escuchar``) no
    repite lo que se pidió antes de conectarse. ``recuperar_pendientes`` es
    el catch-up -- se prueba disparando una escritura real SIN ningún puente
    escuchando, y comprobando que un puente nuevo, creado después, la
    encuentra y la resuelve (en modo Auto: sola, en disco)."""

    workspace = _workspace_con_proveedor(tmp_path)
    archivo = workspace / "recuperar.txt"
    archivo.write_text("original\n", encoding="utf-8")

    async with await ServidorOpencode.iniciar(workspace, timeout_arranque=30.0) as servidor:
        sesion = await _crear_sesion(servidor, workspace)

        # Un puente "de sondeo" nada más para confirmar que quedó pendiente
        # -- no se usa para procesarla, a propósito: se quiere probar que
        # UN PUENTE NUEVO (sin haber visto nada antes) la recupera solo.
        async with PuenteDePermisos(
            servidor, obtener_modo=lambda _sid: ModoAgente.MANUAL
        ) as sondeo:
            solicitud = await _disparar_hasta_permiso(
                servidor,
                sondeo,
                sesion.id,
                [
                    'Sobreescribe recuperar.txt para que diga "modificado-recuperar". Usa tu '
                    "herramienta de escritura ya.",
                    'recuperar.txt sigue igual. Usa write sobre recuperar.txt con '
                    '"modificado-recuperar" ahora.',
                ],
                lambda p: p.action == "edit",
            )
            assert solicitud.action == "edit"
        # El "with" de arriba cierra el cliente HTTP de sondeo, pero la
        # solicitud sigue pendiente EN OPENCODE (no se respondió) -- eso es
        # justo lo que se quiere probar.

        async with PuenteDePermisos(
            servidor, obtener_modo=lambda _sid: ModoAgente.AUTO
        ) as bridge_nuevo:
            resultados: list[ResultadoPermiso] = await bridge_nuevo.recuperar_pendientes(
                str(workspace)
            )
            assert any(r.solicitud.id == solicitud.id for r in resultados), (
                "recuperar_pendientes no encontró la solicitud que ya estaba pendiente"
            )
            coincidente = next(r for r in resultados if r.solicitud.id == solicitud.id)
            assert coincidente.decision == "permitir"
            assert coincidente.respondido is True

            escribio = await _esperar_archivo_cambie(archivo, "original\n")
            assert escribio, (
                "recuperar_pendientes resolvió la solicitud pero el archivo no cambió en disco"
            )


@pytest.mark.asyncio
@requiere_credenciales
async def test_escuchar_recibe_permission_asked_por_el_bus_global_en_vivo(tmp_path: Path) -> None:
    """Prueba dedicada del hallazgo central del módulo: el stream por sesión
    de ``ide_opencode.py`` NUNCA trae esto -- tiene que ser el bus global
    ``GET /event``. Se arranca ``escuchar()`` como tarea de fondo ANTES de
    mandar el prompt (para no repetir el problema de "el bus es en vivo, no
    un log") y se comprueba que el permiso llega y el archivo cambia en
    disco -- sin ninguna llamada a ``permisos_pendientes`` de por medio."""

    workspace = _workspace_con_proveedor(tmp_path)
    archivo = workspace / "bus.txt"
    archivo.write_text("original\n", encoding="utf-8")

    async with await ServidorOpencode.iniciar(workspace, timeout_arranque=30.0) as servidor:
        sesion = await _crear_sesion(servidor, workspace)
        async with PuenteDePermisos(servidor, obtener_modo=lambda _sid: ModoAgente.AUTO) as bridge:
            resultados: list[ResultadoPermiso] = []

            async def _consumir() -> None:
                async for evento in bridge.escuchar(tiempo_maximo_sin_eventos=25.0):
                    if (
                        isinstance(evento, ResultadoPermiso)
                        and evento.solicitud.sessionID == sesion.id
                    ):
                        resultados.append(evento)
                        return

            tarea = asyncio.create_task(_consumir())
            # Pequeña espera para que la conexión SSE quede abierta antes de
            # disparar el prompt -- si se conecta después de que opencode ya
            # creó la solicitud, el hallazgo 3 dice que NO la vería.
            await asyncio.sleep(0.5)

            for texto in (
                'Sobreescribe bus.txt para que diga "modificado-bus". Usa tu herramienta '
                "de escritura ya.",
                'bus.txt sigue igual. Usa write sobre bus.txt con "modificado-bus" ahora.',
            ):
                await servidor.enviar_prompt(sesion.id, texto)
                try:
                    await asyncio.wait_for(asyncio.shield(tarea), timeout=15.0)
                except TimeoutError:
                    continue
                break

            if not tarea.done():
                tarea.cancel()
            else:
                await tarea

            assert resultados, (
                "escuchar() nunca recibió un permission.v2.asked real por el bus global"
            )
            assert resultados[0].decision == "permitir"

            escribio = await _esperar_archivo_cambie(archivo, "original\n")
            assert escribio, (
                "el bus global resolvió el permiso pero el archivo no cambió en disco"
            )


@pytest.mark.asyncio
@requiere_credenciales
async def test_sin_puente_una_edicion_se_cuelga_y_se_resuelve_solo_cuando_el_puente_escucha(
    tmp_path: Path,
) -> None:
    """Reproduce, con timeout ACOTADO (no un cuelgue real de la prueba), el
    hallazgo exacto de un verificador independiente sobre este puente: con un
    ``opencode.json`` que trae ``permission: ask`` (ver la sección "TRAMPA"
    del docstring de ``ide_opencode_config.py``), seguir SOLO
    ``ServidorOpencode.eventos`` -- sin ``PuenteDePermisos`` escuchando --
    deja el turno mudo para siempre después de ``tool.called(edit)``: nunca
    llega ``tool.success``, ni ``tool.error``, ni nada. Es el comportamiento
    REAL de opencode (el stream por sesión nunca trae ``permission.v2.*``,
    ver hallazgo 1 del docstring del módulo), no un bug de ``ide_opencode.py``
    -- y esta misma prueba comprueba la otra mitad: un puente que arranca
    DESPUÉS del cuelgue y llama a ``recuperar_pendientes`` desatasca el MISMO
    turno sin reiniciar sesión ni reenviar el prompt."""

    workspace = _workspace_con_proveedor(tmp_path)
    archivo = workspace / "colgado.txt"
    archivo.write_text("original\n", encoding="utf-8")

    async with await ServidorOpencode.iniciar(workspace, timeout_arranque=30.0) as servidor:
        sesion = await _crear_sesion(servidor, workspace)

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
                        # Sin puente, opencode debe quedarse mudo desde aquí --
                        # se sigue esperando (mismo timeout acotado) para
                        # comprobarlo, no se corta apenas se ve tool.called.
            except TimeoutError:
                colgado = True
            if vio_tool_called:
                break

        assert vio_tool_called, "el modelo real nunca llamó a su herramienta de edición"
        assert colgado, (
            "sin PuenteDePermisos escuchando, el stream de sesión debía quedarse mudo tras "
            "tool.called(edit) -- si esto falla, algo más está respondiendo la solicitud de "
            "permiso, o opencode dejó de pausar sin uno concedido"
        )
        assert archivo.read_text(encoding="utf-8") == "original\n", (
            "el archivo cambió sin que nadie concediera el permiso -- 'permission: ask' no "
            "estaba frenando nada, contradice la premisa de esta prueba"
        )

        # La otra mitad: un puente NUEVO, arrancado después del cuelgue,
        # recupera lo pendiente (modo Auto: se autoconcede) y desatasca el
        # MISMO turno -- sin tocar la sesión ni reenviar el prompt.
        async with PuenteDePermisos(servidor, obtener_modo=lambda _sid: ModoAgente.AUTO) as bridge:
            resultados = await bridge.recuperar_pendientes(str(workspace))
            assert any(r.solicitud.sessionID == sesion.id for r in resultados), (
                "recuperar_pendientes no encontró la solicitud que dejó colgado el turno"
            )

            escribio = await _esperar_archivo_cambie(archivo, "original\n")
            assert escribio, (
                "el puente resolvió el permiso pendiente pero el archivo nunca cambió en disco"
            )
            assert "modificado-colgado" in archivo.read_text(encoding="utf-8")
