"""Pruebas de ``ide_navegador`` (OJOS 1: navegador controlable con capturas).

Todo corre SIN red y SIN abrir un navegador real:

- ``evaluar_navegacion`` se ejercita con un ``resolver`` inyectado (nunca DNS
  real, salvo el propio test de ``resolver_ips`` con "localhost", que no sale
  a la red -- resuelve por loopback/hosts local).
- ``AlmacenCapturas`` opera sobre ``tmp_path`` real, sin ninguna imagen ni
  Playwright de por medio.
- ``SesionNavegador`` se ejercita con dobles (``_PaginaFalsa``/``_MotorFalso``)
  inyectados vía ``motor_factory=`` -- ningún test de este archivo instancia
  Playwright real. El único test que toca ``_motor_playwright`` de verdad
  confirma exactamente el caso "no está instalado" (real en este entorno),
  que es justo el comportamiento que el encargo pide asegurar.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any

import pytest
from edecan_companion.ide_navegador import (
    MAX_TEXTO_CONSOLA_CHARS,
    AlmacenCapturas,
    IDENavegadorError,
    NavegadorNoDisponibleError,
    PoliticaResultado,
    SesionNavegador,
    _mensaje_de_consola,
    _mensaje_de_error_pagina,
    _recortar_texto,
    evaluar_navegacion,
    resolver_ips,
)
from PIL import Image

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _png(color: tuple[int, int, int] = (10, 20, 30), size: tuple[int, int] = (4, 3)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color=color).save(buffer, format="PNG")
    return buffer.getvalue()


class _RespuestaFalsa:
    def __init__(self, status: int) -> None:
        self.status = status


class _PaginaFalsa:
    """Doble mínimo de ``playwright.sync_api.Page``: solo lo que
    ``SesionNavegador`` usa. Registra cada llamada en ``llamadas`` para que
    los tests verifiquen argumentos sin depender de Playwright real."""

    def __init__(
        self,
        *,
        captura: bytes | None = None,
        fallar_goto: bool = False,
        fallar_screenshot: bool = False,
        fallar_click: bool = False,
        fallar_fill: bool = False,
    ) -> None:
        self.url = "about:blank"
        self._titulo = "Sin título"
        self._handlers: dict[str, Any] = {}
        self.llamadas: list[tuple[str, tuple, dict]] = []
        self._captura = captura if captura is not None else _png()
        self._fallar_goto = fallar_goto
        self._fallar_screenshot = fallar_screenshot
        self._fallar_click = fallar_click
        self._fallar_fill = fallar_fill

    def on(self, event: str, handler: Any) -> None:
        self._handlers[event] = handler

    def emitir(self, event: str, payload: Any) -> None:
        self._handlers[event](payload)

    def goto(self, url: str, *, timeout: float, wait_until: str) -> Any:
        self.llamadas.append(("goto", (url,), {"timeout": timeout, "wait_until": wait_until}))
        if self._fallar_goto:
            raise RuntimeError("goto explotó")
        self.url = url
        self._titulo = "Página de prueba"
        return _RespuestaFalsa(200)

    def screenshot(self, *, full_page: bool) -> bytes:
        self.llamadas.append(("screenshot", (), {"full_page": full_page}))
        if self._fallar_screenshot:
            raise RuntimeError("screenshot explotó")
        return self._captura

    def click(self, selector: str, *, timeout: float) -> None:
        self.llamadas.append(("click", (selector,), {"timeout": timeout}))
        if self._fallar_click:
            raise RuntimeError("selector no encontrado")

    def fill(self, selector: str, value: str, *, timeout: float) -> None:
        self.llamadas.append(("fill", (selector, value), {"timeout": timeout}))
        if self._fallar_fill:
            raise RuntimeError("selector no encontrado")

    def press(self, selector: str, key: str) -> None:
        self.llamadas.append(("press", (selector, key), {}))

    def title(self) -> str:
        return self._titulo


class _MotorFalso:
    """Doble de ``MotorNavegador``: cuenta cuántas veces se pidió página
    nueva y cuántas veces se cerró, para verificar reuso/idempotencia."""

    def __init__(self, pagina: _PaginaFalsa) -> None:
        self._pagina = pagina
        self.veces_nueva_pagina = 0
        self.veces_cerrado = 0

    def nueva_pagina(self) -> _PaginaFalsa:
        self.veces_nueva_pagina += 1
        return self._pagina

    def cerrar(self) -> None:
        self.veces_cerrado += 1


def _sesion(pagina: _PaginaFalsa, *, capturas_dir: Path) -> tuple[SesionNavegador, _MotorFalso]:
    motor = _MotorFalso(pagina)
    sesion = SesionNavegador(
        capturas=AlmacenCapturas(capturas_dir),
        motor_factory=lambda: motor,
    )
    return sesion, motor


# --------------------------------------------------------------------------- #
# 1. evaluar_navegacion -- localhost SÍ, LAN/metadata NO
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("url", ["http://localhost:5173", "http://localhost", "http://foo.localhost:3000"])
def test_localhost_por_nombre_se_permite_sin_resolver_dns(url):
    def resolver_que_no_debe_llamarse(hostname: str) -> list[str]:
        raise AssertionError("localhost/*.localhost no debería llegar a resolverse por DNS")

    resultado = evaluar_navegacion(url, resolver=resolver_que_no_debe_llamarse)
    assert resultado == PoliticaResultado(True)


@pytest.mark.parametrize("url", ["http://127.0.0.1:8080", "http://[::1]:3000/app"])
def test_ip_literal_loopback_se_permite(url):
    resultado = evaluar_navegacion(url, resolver=lambda h: [])
    assert resultado.permitido is True


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.5:3000",
        "http://10.0.0.1/panel",
        "http://172.16.0.9/x",
        "http://169.254.169.254/latest/meta-data/",
    ],
)
def test_ip_literal_privada_no_loopback_se_bloquea(url):
    resultado = evaluar_navegacion(url, resolver=lambda h: [])
    assert resultado.permitido is False
    assert (
        "localhost" in resultado.motivo
        or "SSRF" in resultado.motivo
        or "privada" in resultado.motivo
    )


def test_dominio_que_resuelve_a_ip_publica_se_permite():
    resultado = evaluar_navegacion(
        "https://ejemplo.com/pagina", resolver=lambda h: ["93.184.216.34"]
    )
    assert resultado.permitido is True


def test_dominio_que_resuelve_a_lan_se_bloquea_aunque_el_nombre_sea_publico():
    # El nombre no es "localhost", pero resuelve a una IP privada -- SSRF real:
    # esto es exactamente lo que la excepción de localhost NO debe cubrir.
    resultado = evaluar_navegacion(
        "https://interno.ejemplo.com/panel", resolver=lambda h: ["10.1.2.3"]
    )
    assert resultado.permitido is False
    assert "SSRF" in resultado.motivo


def test_dominio_que_resuelve_a_metadata_de_nube_se_bloquea():
    resultado = evaluar_navegacion(
        "https://parece-normal.ejemplo.com/", resolver=lambda h: ["169.254.169.254"]
    )
    assert resultado.permitido is False


def test_dominio_cuya_resolucion_falla_se_bloquea_por_seguridad():
    def resolver_que_falla(hostname: str) -> list[str]:
        raise OSError("DNS caído")

    resultado = evaluar_navegacion("https://no-resuelve.ejemplo.com/", resolver=resolver_que_falla)
    assert resultado.permitido is False
    assert "resolver" in resultado.motivo.lower()


def test_dominio_sin_ninguna_ip_resuelta_se_bloquea():
    resultado = evaluar_navegacion("https://vacio.ejemplo.com/", resolver=lambda h: [])
    assert resultado.permitido is False


@pytest.mark.parametrize(
    "url",
    ["ftp://ejemplo.com/archivo", "file:///etc/passwd", "javascript:alert(1)", "sin-esquema"],
)
def test_esquemas_no_http_se_rechazan(url):
    resultado = evaluar_navegacion(url, resolver=lambda h: [])
    assert resultado.permitido is False


def test_localhost_no_bloquea_rutas_de_login_a_diferencia_de_edecan_browser():
    # Decisión explícita del módulo (ver su docstring, punto 2): a diferencia de
    # `edecan_browser.policy`, aquí NO hay blocklist de checkout/login -- el
    # propósito de este navegador es justo poder llegar a cualquier pantalla del
    # propio dev server, incluida su pantalla de login en desarrollo.
    resultado = evaluar_navegacion("http://localhost:3000/login", resolver=lambda h: [])
    assert resultado.permitido is True


def test_resolver_ips_localhost_incluye_una_direccion_loopback():
    # No sale a la red real -- "localhost" se resuelve localmente (hosts/loopback).
    ips = resolver_ips("localhost")
    assert ips
    assert any(ip in ("127.0.0.1", "::1") for ip in ips)


# --------------------------------------------------------------------------- #
# 2. AlmacenCapturas -- tope de cantidad y de bytes, nunca se borra a sí misma
# --------------------------------------------------------------------------- #


def test_guardar_escribe_el_archivo_y_devuelve_metadatos_consistentes(tmp_path):
    almacen = AlmacenCapturas(tmp_path / "capturas")
    datos = b"contenido-de-prueba-1234"

    guardado = almacen.guardar(datos, ancho=4, alto=3)

    assert guardado.ruta.exists()
    assert guardado.ruta.read_bytes() == datos
    assert guardado.tamano_bytes == len(datos)
    assert (guardado.ancho, guardado.alto) == (4, 3)
    assert guardado.id and guardado.id in guardado.ruta.name
    # lo journal-safe nunca trae bytes/base64, solo metadatos + ruta:
    assert set(guardado.public().keys()) == {
        "id",
        "ruta",
        "ancho",
        "alto",
        "tamano_bytes",
        "creada_en",
    }


def test_tope_de_cantidad_purga_las_capturas_mas_viejas(tmp_path):
    almacen = AlmacenCapturas(tmp_path / "capturas", max_capturas=2, max_bytes_totales=10_000)

    primera = almacen.guardar(b"a", ancho=1, alto=1)
    segunda = almacen.guardar(b"b", ancho=1, alto=1)
    tercera = almacen.guardar(b"c", ancho=1, alto=1)

    assert not primera.ruta.exists()  # la más vieja se purgó
    assert segunda.ruta.exists()
    assert tercera.ruta.exists()
    assert almacen.cantidad == 2


def test_tope_de_bytes_totales_purga_las_capturas_mas_viejas(tmp_path):
    almacen = AlmacenCapturas(tmp_path / "capturas", max_capturas=100, max_bytes_totales=15)

    primera = almacen.guardar(b"1234567890", ancho=1, alto=1)  # 10 bytes
    segunda = almacen.guardar(b"1234567890", ancho=1, alto=1)  # total 20 > 15 -> purga la primera

    assert not primera.ruta.exists()
    assert segunda.ruta.exists()
    assert almacen.bytes_totales <= 15


def test_no_se_borra_a_si_misma_aunque_una_sola_captura_supere_el_presupuesto(tmp_path):
    almacen = AlmacenCapturas(tmp_path / "capturas", max_capturas=1, max_bytes_totales=1)
    guardado = almacen.guardar(b"esto-pesa-mas-de-1-byte", ancho=1, alto=1)
    assert guardado.ruta.exists()  # nunca se purga la que se acaba de devolver


def test_limpiar_todas_borra_todo_y_devuelve_cuantas(tmp_path):
    almacen = AlmacenCapturas(tmp_path / "capturas", max_capturas=100, max_bytes_totales=10_000)
    almacen.guardar(b"x", ancho=1, alto=1)
    almacen.guardar(b"y", ancho=1, alto=1)

    borradas = almacen.limpiar_todas()

    assert borradas == 2
    assert almacen.cantidad == 0


# --------------------------------------------------------------------------- #
# 3. Mensajes de consola -- funciones puras
# --------------------------------------------------------------------------- #


class _MsgConsolaFalso:
    def __init__(self, tipo: str, texto: str, location: dict | None = None) -> None:
        self.type = tipo
        self.text = texto
        self.location = location


class _ErrorPaginaFalso:
    def __init__(self, message: str) -> None:
        self.message = message


def test_mensaje_de_consola_incluye_ubicacion_con_linea():
    location = {"url": "http://localhost:3000/app.js", "lineNumber": 42}
    msg = _MsgConsolaFalso("error", "algo falló", location)
    convertido = _mensaje_de_consola(msg)
    assert convertido.tipo == "error"
    assert convertido.texto == "algo falló"
    assert convertido.ubicacion == "http://localhost:3000/app.js:42"


def test_mensaje_de_consola_sin_ubicacion():
    msg = _MsgConsolaFalso("log", "hola")
    convertido = _mensaje_de_consola(msg)
    assert convertido.ubicacion is None


def test_mensaje_de_consola_recorta_texto_largo():
    largo = "x" * (MAX_TEXTO_CONSOLA_CHARS + 500)
    msg = _MsgConsolaFalso("log", largo)
    convertido = _mensaje_de_consola(msg)
    assert len(convertido.texto) <= MAX_TEXTO_CONSOLA_CHARS
    assert convertido.texto.endswith("recortado)")


def test_recortar_texto_deja_intacto_lo_que_ya_cabe():
    assert _recortar_texto("corto") == "corto"


def test_mensaje_de_error_pagina_usa_message():
    convertido = _mensaje_de_error_pagina(_ErrorPaginaFalso("TypeError: x is not a function"))
    assert convertido.tipo == "pageerror"
    assert "TypeError" in convertido.texto


def test_mensaje_de_error_pagina_sin_message_usa_str():
    convertido = _mensaje_de_error_pagina(RuntimeError("boom"))
    assert convertido.tipo == "pageerror"
    assert "boom" in convertido.texto


# --------------------------------------------------------------------------- #
# 4. SesionNavegador -- con dobles, sin Playwright real
# --------------------------------------------------------------------------- #


def test_abrir_url_bloqueada_nunca_construye_el_motor(tmp_path):
    def factory_que_no_debe_llamarse() -> Any:
        raise AssertionError("no debería construirse el motor para una URL bloqueada")

    sesion = SesionNavegador(
        capturas=AlmacenCapturas(tmp_path / "capturas"),
        motor_factory=factory_que_no_debe_llamarse,
    )
    with pytest.raises(IDENavegadorError):
        sesion.abrir("http://192.168.1.5:3000")


def test_abrir_url_permitida_navega_y_devuelve_estado(tmp_path):
    pagina = _PaginaFalsa()
    sesion, motor = _sesion(pagina, capturas_dir=tmp_path / "capturas")

    resultado = sesion.abrir("http://localhost:5173/app")

    assert resultado["url"] == "http://localhost:5173/app"
    assert resultado["titulo"] == "Página de prueba"
    assert resultado["estado_http"] == 200
    assert pagina.llamadas[0][0] == "goto"


def test_abrir_reusa_la_misma_pagina_entre_llamadas(tmp_path):
    pagina = _PaginaFalsa()
    sesion, motor = _sesion(pagina, capturas_dir=tmp_path / "capturas")

    sesion.abrir("http://localhost:3000/uno")
    sesion.abrir("http://localhost:3000/dos")

    assert motor.veces_nueva_pagina == 1


def test_abrir_reinicia_los_mensajes_de_consola_acumulados(tmp_path):
    pagina = _PaginaFalsa()
    sesion, _ = _sesion(pagina, capturas_dir=tmp_path / "capturas")

    sesion.abrir("http://localhost:3000/uno")
    pagina.emitir("console", _MsgConsolaFalso("error", "primer error"))
    assert sesion.consola()["mensajes"]

    sesion.abrir("http://localhost:3000/dos")
    assert sesion.consola()["mensajes"] == []


def test_abrir_traduce_el_fallo_de_goto_en_error_del_modulo(tmp_path):
    pagina = _PaginaFalsa(fallar_goto=True)
    sesion, _ = _sesion(pagina, capturas_dir=tmp_path / "capturas")
    with pytest.raises(IDENavegadorError):
        sesion.abrir("http://localhost:3000/uno")


def test_capturar_guarda_en_disco_y_arma_el_bloque_para_el_modelo(tmp_path):
    datos_png = _png()
    pagina = _PaginaFalsa(captura=datos_png)
    sesion, _ = _sesion(pagina, capturas_dir=tmp_path / "capturas")
    sesion.abrir("http://localhost:3000/uno")

    resultado = sesion.capturar()

    assert resultado.imagen.bloque["type"] == "image"
    assert resultado.guardado.ruta.exists()
    assert resultado.guardado.ruta.read_bytes() == base64.b64decode(
        resultado.imagen.bloque["source"]["data"]
    )
    assert resultado.guardado.ancho == 4 and resultado.guardado.alto == 3
    # lo journal-safe nunca expone el bloque de imagen ni sus bytes:
    assert "imagen" not in resultado.public()
    assert resultado.public() == resultado.guardado.public()


def test_capturar_pantalla_completa_se_lo_pasa_a_screenshot(tmp_path):
    pagina = _PaginaFalsa()
    sesion, _ = _sesion(pagina, capturas_dir=tmp_path / "capturas")
    sesion.abrir("http://localhost:3000/uno")

    sesion.capturar(pantalla_completa=True)

    llamada_screenshot = next(item for item in pagina.llamadas if item[0] == "screenshot")
    assert llamada_screenshot[2] == {"full_page": True}


def test_capturar_cuando_screenshot_falla_se_traduce_a_error_del_modulo(tmp_path):
    pagina = _PaginaFalsa(fallar_screenshot=True)
    sesion, _ = _sesion(pagina, capturas_dir=tmp_path / "capturas")
    sesion.abrir("http://localhost:3000/uno")
    with pytest.raises(IDENavegadorError):
        sesion.capturar()


def test_capturar_con_bytes_que_no_son_una_imagen_real_falla_claro(tmp_path):
    pagina = _PaginaFalsa(captura=b"esto no es un png de verdad")
    sesion, _ = _sesion(pagina, capturas_dir=tmp_path / "capturas")
    sesion.abrir("http://localhost:3000/uno")
    with pytest.raises(IDENavegadorError):
        sesion.capturar()


def test_consola_solo_errores_filtra_log_e_info(tmp_path):
    pagina = _PaginaFalsa()
    sesion, _ = _sesion(pagina, capturas_dir=tmp_path / "capturas")
    sesion.abrir("http://localhost:3000/uno")

    pagina.emitir("console", _MsgConsolaFalso("log", "todo bien"))
    pagina.emitir("console", _MsgConsolaFalso("error", "algo falló"))
    pagina.emitir("pageerror", _ErrorPaginaFalso("TypeError: boom"))

    todos = sesion.consola()["mensajes"]
    solo_errores = sesion.consola(solo_errores=True)["mensajes"]

    assert len(todos) == 3
    assert len(solo_errores) == 2
    assert {m["tipo"] for m in solo_errores} == {"error", "pageerror"}


def test_consola_marca_truncado_al_superar_el_tope(tmp_path, monkeypatch):
    import edecan_companion.ide_navegador as modulo

    monkeypatch.setattr(modulo, "MAX_MENSAJES_CONSOLA", 2)
    pagina = _PaginaFalsa()
    sesion, _ = _sesion(pagina, capturas_dir=tmp_path / "capturas")
    sesion.abrir("http://localhost:3000/uno")

    for i in range(5):
        pagina.emitir("console", _MsgConsolaFalso("log", f"mensaje {i}"))

    estado = sesion.consola()
    assert len(estado["mensajes"]) == 2
    assert estado["truncado"] is True


def test_clic_llama_click_de_la_pagina_con_el_selector(tmp_path):
    pagina = _PaginaFalsa()
    sesion, _ = _sesion(pagina, capturas_dir=tmp_path / "capturas")
    sesion.abrir("http://localhost:3000/uno")

    resultado = sesion.clic("#boton-guardar")

    assert resultado["selector"] == "#boton-guardar"
    assert ("click", ("#boton-guardar",), {"timeout": sesion._timeout_ms}) in pagina.llamadas


def test_clic_selector_inexistente_se_traduce_a_error_del_modulo(tmp_path):
    pagina = _PaginaFalsa(fallar_click=True)
    sesion, _ = _sesion(pagina, capturas_dir=tmp_path / "capturas")
    sesion.abrir("http://localhost:3000/uno")
    with pytest.raises(IDENavegadorError, match="clic"):
        sesion.clic("#no-existe")


def test_escribir_llama_fill_y_opcionalmente_enter(tmp_path):
    pagina = _PaginaFalsa()
    sesion, _ = _sesion(pagina, capturas_dir=tmp_path / "capturas")
    sesion.abrir("http://localhost:3000/uno")

    sesion.escribir("#buscar", "hola mundo", enter=True)

    nombres = [item[0] for item in pagina.llamadas if item[0] in ("fill", "press")]
    assert nombres == ["fill", "press"]


def test_escribir_sin_enter_no_llama_press(tmp_path):
    pagina = _PaginaFalsa()
    sesion, _ = _sesion(pagina, capturas_dir=tmp_path / "capturas")
    sesion.abrir("http://localhost:3000/uno")

    sesion.escribir("#buscar", "hola")

    assert all(item[0] != "press" for item in pagina.llamadas)


def test_escribir_selector_inexistente_se_traduce_a_error_del_modulo(tmp_path):
    pagina = _PaginaFalsa(fallar_fill=True)
    sesion, _ = _sesion(pagina, capturas_dir=tmp_path / "capturas")
    sesion.abrir("http://localhost:3000/uno")
    with pytest.raises(IDENavegadorError, match="escribir"):
        sesion.escribir("#no-existe", "x")


def test_cerrar_es_idempotente_y_delega_en_el_motor(tmp_path):
    pagina = _PaginaFalsa()
    sesion, motor = _sesion(pagina, capturas_dir=tmp_path / "capturas")
    sesion.abrir("http://localhost:3000/uno")

    sesion.cerrar()
    sesion.cerrar()  # segunda vez: no debe volver a llamar al motor

    assert motor.veces_cerrado == 1


def test_cerrar_sin_haber_abierto_nunca_no_falla(tmp_path):
    sesion = SesionNavegador(capturas=AlmacenCapturas(tmp_path / "capturas"))
    sesion.cerrar()  # no debe lanzar ni intentar construir el motor


def test_context_manager_cierra_incluso_si_el_cuerpo_lanza(tmp_path):
    pagina = _PaginaFalsa()
    motor = _MotorFalso(pagina)

    with pytest.raises(RuntimeError):
        with SesionNavegador(
            capturas=AlmacenCapturas(tmp_path / "capturas"),
            motor_factory=lambda: motor,
        ) as sesion:
            sesion.abrir("http://localhost:3000/uno")
            raise RuntimeError("algo salió mal dentro del with")

    assert motor.veces_cerrado == 1


# --------------------------------------------------------------------------- #
# 5. Ausencia de Playwright -- aviso claro, nunca un crash (punto 1 del módulo)
# --------------------------------------------------------------------------- #


def test_construir_sesion_sin_motor_factory_nunca_falla_aunque_playwright_no_este(tmp_path):
    # Construir la sesión NO debe fallar aunque Playwright no esté instalado en
    # este entorno de pruebas -- el import perezoso ocurre recién al navegar.
    sesion = SesionNavegador(capturas=AlmacenCapturas(tmp_path / "capturas"))
    assert sesion is not None


def test_abrir_sin_playwright_instalado_da_error_claro_no_traceback_criptico(tmp_path):
    sesion = SesionNavegador(capturas=AlmacenCapturas(tmp_path / "capturas"))
    with pytest.raises(NavegadorNoDisponibleError, match="playwright"):
        sesion.abrir("http://localhost:3000")
