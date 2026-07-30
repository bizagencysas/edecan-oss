"""Navegador controlable con capturas para el agente del IDE ("OJOS 1").

Hoy el agente escribe código de UI y solo puede CREER que quedó bien: no hay
forma de que él mismo abra lo que acaba de levantar, lo vea y lea los errores
reales de la consola del navegador. Eso es justo la diferencia entre "creo
que la página quedó bien" y "la abrí, la vi, y aquí está la captura" -- lo que
ya hace Antigravity. Este módulo construye esa segunda mitad.

``ide_imagenes.py`` (T2.1, ya existente) resuelve la dirección usuario→modelo:
la persona pega una imagen y el modelo la ve. Este módulo resuelve la
dirección que faltaba, agente→ojos: el propio agente abre una URL (típicamente
el servidor de desarrollo local que él mismo acaba de levantar con
``ejecutar_comando``), toma una captura, la reusa como imagen vía
``ide_imagenes.validar_y_normalizar_imagen`` (mismo contrato de bloque que ya
entiende ``edecan_llm.multimodal``, sin reimplementar esa validación), lee los
mensajes de la consola del navegador, y puede hacer clic/escribir para llegar
a una pantalla concreta antes de la siguiente captura.

Decisiones de diseño explícitas (no vienen dadas palabra por palabra):

1. **Motor: Playwright, pero opcional y perezoso -- su ausencia es un aviso,
   nunca un crash.** Playwright es lo obvio (Chromium real, consola real,
   selectors reales), pero es una dependencia pesada, y el dueño de este
   proyecto ya sufrió que una instalación de Playwright se corrompiera y le
   tumbara otro sistema completo ese mismo día. Por eso, igual que
   ``edecan_browser.fetch.PlaywrightFetcher`` (mismo patrón, paquete
   distinto): el import de ``playwright`` es **perezoso** (ocurre recién
   dentro de ``_motor_playwright``, nunca al importar este módulo) y
   **guardeado** (``ImportError`` -> ``NavegadorNoDisponibleError`` con
   instrucciones de instalación explícitas, nunca un traceback críptico).
   Instalarlo es un paso manual y consciente del usuario -- extra opcional
   ``edecan-companion[playwright]`` + ``playwright install chromium`` -- este
   módulo JAMÁS lo instala solo. Construir una ``SesionNavegador()`` sin
   Playwright instalado NO falla; solo falla la primera acción real
   (``abrir``), con ese mensaje claro.

2. **Localhost SÍ se permite aquí -- al revés que ``edecan_browser.policy``,
   y por una razón concreta.** ``edecan_browser`` existe para que Edecán
   investigue la web pública en nombre del usuario, así que bloquea
   ``localhost``/redes privadas sin excepción (ver su docstring: "investigar
   sí, comprar jamás", SSRF es un riesgo real contra la propia red del
   usuario). Este módulo existe para un caso distinto: el agente quiere ver
   EL SERVIDOR QUE ÉL MISMO ACABA DE LEVANTAR en la máquina del usuario. Sin
   una excepción para loopback, la herramienta sería inútil para su propósito
   principal. La excepción se acota lo más posible (``evaluar_navegacion``,
   más abajo) para no reabrir el problema que ``edecan_browser`` cierra:
   - ``localhost``/``*.localhost`` y cualquier IP que resuelva a loopback
     (``127.0.0.0/8``, ``::1``) -> permitido, SIEMPRE.
   - Cualquier otra dirección privada/link-local/reservada/de metadata de
     nube (LAN del usuario, ``169.254.169.254``, etc.) -> bloqueado, SIN
     excepción -- esas NO son "mi propio servidor", son otros equipos de la
     red o metadata de nube, y el argumento de "es mi propia máquina" no
     aplica ahí.
   - Un dominio/IP pública normal -> permitido (el agente también puede
     querer abrir una página de referencia pública, no solo el dev server).
   No se reimplementa ``edecan_browser.policy`` ni se importa
   ``edecan_browser`` -- ``edecan_companion`` está construido a propósito SIN
   dependencias de otros paquetes del monorepo (ver ``pyproject.toml`` de
   este paquete), así que la política vive aquí, en miniatura, con su propia
   regla (distinta a propósito, ver arriba).
   Tampoco se reimplementa el bloqueo de rutas "checkout/login/pago" de
   ``edecan_browser``: el propósito explícito de ``clic``/``escribir`` es
   justamente poder llegar a CUALQUIER pantalla concreta de la app del propio
   usuario -- incluida su pantalla de login en desarrollo -- así que ese
   guardrail (pensado para que Edecán nunca compre/inicie sesión en nombre
   del usuario en la web pública) no aplica al dev server del propio usuario.
   Tampoco se consulta ``robots.txt``: no es un rastreador masivo, es un
   desarrollador (humano o agente) mirando su propia pantalla a mano.

3. **Las capturas ocupan disco y NUNCA viajan enteras en el prompt.**
   ``AlmacenCapturas`` las guarda en una carpeta dedicada y limpiable
   (``CAPTURAS_DIR_DEFECTO``, o la que indique quien la instancie), con tope
   de cantidad Y de bytes totales -- al superar cualquiera de los dos, se
   purga la más vieja (misma idea de presupuesto/expulsión que
   ``ide_checkpoints.CheckpointStore``, aplicada aquí a capturas en vez de a
   checkpoints). ``capturar()`` reusa ``ide_imagenes.validar_y_normalizar_imagen``
   para el bloque que de verdad ve el modelo (``CapturaAgente.imagen``) --
   nunca reimplementa esa validación -- y ``CapturaAgente.public()`` (lo que
   se journalea) devuelve SOLO metadatos + la ruta en disco de
   ``CapturaGuardada``, jamás los bytes/base64: "el modelo recibe la imagen,
   el journal recibe la referencia", tal como pide el encargo.

Integración prevista (NO se toca desde aquí, ver encargo -- lo cablea el
humano después): un tool nuevo del agente (p. ej. ``abrir_navegador`` /
``capturar_pantalla`` / ``leer_consola`` / ``clic_en_pantalla`` /
``escribir_en_pantalla``) en ``ide_workers_agent.TOOLS``, ejecutado en
``ide_workers_agent.WorkersAgent._execute`` con
``await asyncio.to_thread(...)`` -- exactamente el mismo patrón que ya usa
``ejecutar_comando`` para no bloquear el loop de eventos con una llamada
síncrona. Una ``SesionNavegador`` vive mientras dure el turno (o la sesión) y
se cierra siempre en un ``finally`` (o vía ``with SesionNavegador(...) as
sesion:``), igual que ``ide_checkpoints`` sella su checkpoint al terminar el
turno.
"""

from __future__ import annotations

import base64
import ipaddress
import socket
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from edecan_companion.config import DEFAULT_CONFIG_DIR
from edecan_companion.ide_imagenes import IDEImagenError, ImagenLista, validar_y_normalizar_imagen

# --------------------------------------------------------------------------- #
# Errores
# --------------------------------------------------------------------------- #


class IDENavegadorError(ValueError):
    """Navegación/acción de navegador inválida (URL bloqueada, selector no
    encontrado, captura fallida, etc.)."""


class NavegadorNoDisponibleError(IDENavegadorError):
    """Playwright no está instalado. Ver punto 1 del docstring del módulo:
    esto se lanza SOLO al intentar usar el navegador de verdad (``abrir``),
    nunca al construir ``SesionNavegador`` ni al importar este módulo."""


# --------------------------------------------------------------------------- #
# 1. Política de navegación -- localhost SÍ, LAN/metadata NO (ver punto 2).
# --------------------------------------------------------------------------- #

_ESQUEMAS_PERMITIDOS = frozenset({"http", "https"})
_HOSTS_LOOPBACK_POR_NOMBRE = frozenset({"localhost", "localhost."})
_TIMEOUT_RESOLUCION_DNS_SEGUNDOS = 5.0


@dataclass(frozen=True)
class PoliticaResultado:
    """``permitido=False`` siempre trae ``motivo`` listo para mostrarle al
    agente/usuario -- mismo contrato que ``edecan_browser.policy.PolicyResult``,
    reimplementado aquí en miniatura (ver punto 2 del docstring del módulo:
    la regla es DISTINTA a propósito, no vale importar la de otro paquete)."""

    permitido: bool
    motivo: str | None = None


def resolver_ips(
    hostname: str, *, timeout_segundos: float = _TIMEOUT_RESOLUCION_DNS_SEGUNDOS
) -> list[str]:
    """Resuelve ``hostname`` vía el resolutor del sistema, con timeout duro.

    Aislada en su propia función módulo-level -- igual criterio que
    ``edecan_browser.policy.resolve_hostname_ips`` -- para que
    ``evaluar_navegacion`` la reciba como parámetro inyectable (``resolver=``)
    y los tests la sustituyan sin depender de DNS real. ``socket.getaddrinfo``
    no acepta un timeout directo, así que se corre en un hilo aparte y se le
    pone límite ahí -- sin tocar ningún estado global de ``socket``.
    """
    with ThreadPoolExecutor(max_workers=1) as pool:
        futuro = pool.submit(socket.getaddrinfo, hostname, None)
        try:
            infos = futuro.result(timeout=timeout_segundos)
        except FutureTimeoutError as exc:
            raise IDENavegadorError(
                f"Resolver «{hostname}» tardó más de {timeout_segundos}s."
            ) from exc
    return sorted({info[4][0] for info in infos})


def _ip_literal(hostname: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(hostname)
    except ValueError:
        return None


def _bloqueada_por_red(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """``True`` si ``ip`` es una dirección que este módulo NUNCA permite,
    salvo que además sea loopback (eso se revisa aparte, antes, en
    ``evaluar_navegacion`` -- ver punto 2 del docstring del módulo: loopback
    es la única excepción, el resto de "privado" sigue bloqueado)."""
    return (
        ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def evaluar_navegacion(
    url: str,
    *,
    resolver: Callable[[str], list[str]] = resolver_ips,
) -> PoliticaResultado:
    """Portero de ``SesionNavegador.abrir`` -- ver punto 2 del docstring del
    módulo para el porqué de cada regla. Nunca lanza excepción por una URL
    inválida de negocio: devuelve ``PoliticaResultado(False, motivo=...)``.
    """
    partes = urlsplit(url)
    if partes.scheme.lower() not in _ESQUEMAS_PERMITIDOS or not partes.hostname:
        return PoliticaResultado(
            False,
            f"«{url}» no es una URL http/https válida -- el navegador del agente solo abre "
            "http(s).",
        )

    hostname = partes.hostname.lower()

    # Excepción explícita: nombres reservados a "esta misma máquina".
    if hostname in _HOSTS_LOOPBACK_POR_NOMBRE or hostname.endswith(".localhost"):
        return PoliticaResultado(True)

    ip_literal = _ip_literal(hostname)
    if ip_literal is not None:
        if ip_literal.is_loopback:
            return PoliticaResultado(True)
        if _bloqueada_por_red(ip_literal):
            return PoliticaResultado(
                False,
                f"«{hostname}» es una dirección de red privada/local/de metadata de nube. "
                "Este navegador solo hace una excepción para tu propia máquina "
                "(localhost/127.0.0.1/::1) -- usa esa si quieres ver tu servidor local.",
            )
        return PoliticaResultado(True)

    try:
        ips = resolver(hostname)
    except IDENavegadorError as exc:
        return PoliticaResultado(False, str(exc))
    except Exception:
        return PoliticaResultado(
            False, f"No se pudo resolver «{hostname}»; se bloquea por seguridad."
        )
    if not ips:
        return PoliticaResultado(
            False, f"«{hostname}» no resolvió a ninguna dirección; se bloquea por seguridad."
        )
    for ip_str in ips:
        ip = ipaddress.ip_address(ip_str)
        if ip.is_loopback:
            continue
        if _bloqueada_por_red(ip):
            return PoliticaResultado(
                False,
                f"«{hostname}» resuelve a «{ip_str}», una dirección de red privada/local/de "
                "metadata de nube -- bloqueada por seguridad (SSRF). Si quieres ver TU "
                "servidor local, navega a 'localhost' o '127.0.0.1' directamente.",
            )
    return PoliticaResultado(True)


# --------------------------------------------------------------------------- #
# 2. Almacén de capturas en disco -- con tope y limpieza (ver punto 3).
# --------------------------------------------------------------------------- #

CAPTURAS_DIR_DEFECTO = DEFAULT_CONFIG_DIR / "ide-navegador" / "capturas"
MAX_CAPTURAS_DEFECTO = 60
MAX_BYTES_TOTALES_DEFECTO = 40 * 1024 * 1024  # 40 MiB de capturas acumuladas


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class CapturaGuardada:
    """Lo que SÍ es seguro loguear/journalear de una captura: nunca sus bytes."""

    id: str
    ruta: Path
    ancho: int | None
    alto: int | None
    tamano_bytes: int
    creada_en: str

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ruta": str(self.ruta),
            "ancho": self.ancho,
            "alto": self.alto,
            "tamano_bytes": self.tamano_bytes,
            "creada_en": self.creada_en,
        }


class AlmacenCapturas:
    """Guarda PNGs de captura en ``directorio`` con tope de cantidad Y de
    bytes totales -- ver punto 3 del docstring del módulo. Cada
    ``guardar()`` purga primero las capturas más viejas si hace falta, así
    que el disco nunca crece sin límite aunque nadie llame ``limpiar_todas()``
    a mano.
    """

    def __init__(
        self,
        directorio: Path,
        *,
        max_capturas: int = MAX_CAPTURAS_DEFECTO,
        max_bytes_totales: int = MAX_BYTES_TOTALES_DEFECTO,
    ) -> None:
        self.directorio = Path(directorio)
        self.directorio.mkdir(parents=True, exist_ok=True)
        self._max_capturas = max_capturas
        self._max_bytes_totales = max_bytes_totales
        self._lock = threading.Lock()

    def guardar(self, datos: bytes, *, ancho: int | None, alto: int | None) -> CapturaGuardada:
        with self._lock:
            # El nombre empieza por `time_ns` a propósito: orden lexicográfico
            # == orden cronológico, sin tener que golpear `stat()` para
            # ordenar por fecha de modificación en `_purgar_si_excede`.
            nombre = f"{time.time_ns()}-{uuid.uuid4().hex[:8]}.png"
            ruta = self.directorio / nombre
            ruta.write_bytes(datos)
            guardado = CapturaGuardada(
                id=nombre[:-4],
                ruta=ruta,
                ancho=ancho,
                alto=alto,
                tamano_bytes=len(datos),
                creada_en=_utc_now_iso(),
            )
            self._purgar_si_excede(mantener=nombre)
            return guardado

    def _purgar_si_excede(self, *, mantener: str) -> None:
        archivos = sorted(self.directorio.glob("*.png"), key=lambda p: p.name)
        while True:
            total = sum(a.stat().st_size for a in archivos)
            if len(archivos) <= self._max_capturas and total <= self._max_bytes_totales:
                return
            candidatos = [a for a in archivos if a.name != mantener]
            if not candidatos:
                # Solo queda la captura recién guardada: no se borra a sí
                # misma aunque siga sobre el tope -- `guardar()` debe poder
                # devolver siempre una referencia válida a lo que acaba de
                # crear (ver punto 3 del docstring del módulo).
                return
            candidatos[0].unlink(missing_ok=True)
            archivos.remove(candidatos[0])

    def limpiar_todas(self) -> int:
        """Borra todas las capturas guardadas; devuelve cuántas borró. Punto
        de entrada explícito para una limpieza periódica del companion
        (mismo espíritu que ``ide_checkpoints.CheckpointStore.prune_expired``)."""
        with self._lock:
            archivos = list(self.directorio.glob("*.png"))
            for archivo in archivos:
                archivo.unlink(missing_ok=True)
            return len(archivos)

    @property
    def cantidad(self) -> int:
        return sum(1 for _ in self.directorio.glob("*.png"))

    @property
    def bytes_totales(self) -> int:
        return sum(a.stat().st_size for a in self.directorio.glob("*.png"))


@dataclass(frozen=True)
class CapturaAgente:
    """Resultado de ``SesionNavegador.capturar``: ``imagen`` es lo que ve el
    MODELO (bloque con los bytes en base64 -- nunca se journalea tal cual);
    ``guardado`` es lo que persiste el JOURNAL (solo metadatos + ruta en
    disco). Separar ambos en el tipo de retorno hace literalmente imposible
    journalear el bloque completo por accidente: quien journalea llama
    ``.public()``, quien arma el turno del modelo usa ``.imagen``.
    """

    imagen: ImagenLista
    guardado: CapturaGuardada

    def public(self) -> dict[str, Any]:
        return self.guardado.public()


# --------------------------------------------------------------------------- #
# 3. Mensajes de consola -- funciones puras, testeables sin Playwright real.
# --------------------------------------------------------------------------- #

MAX_MENSAJES_CONSOLA = 200
MAX_TEXTO_CONSOLA_CHARS = 4000
_MARCA_RECORTE = "… (recortado)"


@dataclass(frozen=True)
class MensajeConsola:
    tipo: str  # "log" | "info" | "warning" | "error" | "debug" | "pageerror"
    texto: str
    ubicacion: str | None = None

    def public(self) -> dict[str, Any]:
        return {"tipo": self.tipo, "texto": self.texto, "ubicacion": self.ubicacion}


def _recortar_texto(texto: str, *, max_chars: int = MAX_TEXTO_CONSOLA_CHARS) -> str:
    if len(texto) <= max_chars:
        return texto
    return texto[: max_chars - len(_MARCA_RECORTE)] + _MARCA_RECORTE


def _mensaje_de_consola(msg: Any, *, max_chars: int = MAX_TEXTO_CONSOLA_CHARS) -> MensajeConsola:
    """Convierte un ``playwright.sync_api.ConsoleMessage`` (o un doble de
    test con los mismos atributos) en ``MensajeConsola``. Función pura
    módulo-level para poder testearla con un objeto simple (``SimpleNamespace``
    o una clase mínima), sin Playwright real instalado."""
    tipo = str(getattr(msg, "type", None) or "log")
    texto = _recortar_texto(str(getattr(msg, "text", None) or ""), max_chars=max_chars)
    ubicacion_raw = getattr(msg, "location", None)
    ubicacion: str | None = None
    if isinstance(ubicacion_raw, dict) and ubicacion_raw.get("url"):
        url_ubicacion = str(ubicacion_raw["url"])
        linea = ubicacion_raw.get("lineNumber")
        ubicacion = f"{url_ubicacion}:{linea}" if linea is not None else url_ubicacion
    return MensajeConsola(tipo=tipo, texto=texto, ubicacion=ubicacion)


def _mensaje_de_error_pagina(
    error: Any, *, max_chars: int = MAX_TEXTO_CONSOLA_CHARS
) -> MensajeConsola:
    """Convierte un error no atrapado de la página (evento ``pageerror``) en
    ``MensajeConsola`` -- estos NUNCA aparecen en ``console.error`` porque
    saltaron por fuera de cualquier ``try/catch`` de la propia página, así que
    se registran con su propio tipo (``"pageerror"``) para no confundirlos
    con un ``console.error`` deliberado."""
    texto = str(getattr(error, "message", None) or error)
    return MensajeConsola(tipo="pageerror", texto=_recortar_texto(texto, max_chars=max_chars))


# --------------------------------------------------------------------------- #
# 4. El motor -- protocolo inyectable + Playwright real perezoso (ver punto 1).
# --------------------------------------------------------------------------- #

_TIMEOUT_MS_DEFECTO = 15_000.0


@runtime_checkable
class PaginaNavegador(Protocol):
    """Lo mínimo que ``SesionNavegador`` necesita de una página real
    (``playwright.sync_api.Page``) o de un doble de test."""

    url: str

    def goto(self, url: str, *, timeout: float, wait_until: str) -> Any: ...
    def screenshot(self, *, full_page: bool) -> bytes: ...
    def click(self, selector: str, *, timeout: float) -> None: ...
    def fill(self, selector: str, value: str, *, timeout: float) -> None: ...
    def press(self, selector: str, key: str) -> None: ...
    def title(self) -> str: ...
    def on(self, event: str, handler: Callable[[Any], None]) -> None: ...


@runtime_checkable
class MotorNavegador(Protocol):
    """Lo mínimo que ``SesionNavegador`` necesita de un motor completo:
    crear una página y cerrar TODO lo que abrió (browser + proceso de
    Playwright). ``_motor_playwright`` es la implementación real; los tests
    inyectan su propio doble vía ``motor_factory=``."""

    def nueva_pagina(self) -> PaginaNavegador: ...
    def cerrar(self) -> None: ...


def _motor_playwright(*, headless: bool, timeout_ms: float) -> MotorNavegador:
    """Motor real: Chromium vía Playwright, import perezoso y guardeado
    (ver punto 1 del docstring del módulo)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise NavegadorNoDisponibleError(
            "Esta acción necesita Playwright (Chromium) y no está instalado. Es una "
            "dependencia pesada que este proyecto NUNCA instala en automático -- "
            "instálala a mano cuando de verdad quieras que el agente controle un "
            "navegador real:\n\n"
            "  uv pip install 'edecan-companion[playwright]'\n"
            "  uv run playwright install chromium\n\n"
            "(el binario de Chromium lo descarga Playwright aparte; pip/uv no lo hacen). "
            "Sin este paso, abrir el navegador seguirá fallando con este mismo mensaje "
            "claro -- nunca con un traceback críptico."
        ) from exc

    class _MotorPlaywright:
        def __init__(self) -> None:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=headless)

        def nueva_pagina(self) -> Any:  # pragma: no cover - requiere el extra opcional
            pagina = self._browser.new_page()
            pagina.set_default_timeout(timeout_ms)
            return pagina

        def cerrar(self) -> None:  # pragma: no cover - requiere el extra opcional
            try:
                self._browser.close()
            finally:
                self._pw.stop()

    return _MotorPlaywright()  # pragma: no cover - requiere el extra opcional


# --------------------------------------------------------------------------- #
# 5. La sesión -- lo que de verdad usaría el tool del agente.
# --------------------------------------------------------------------------- #


class SesionNavegador:
    """Envoltorio síncrono sobre UNA página de navegador para el agente.

    Cada instancia posee a lo sumo un motor + una página: ``abrir()`` navega
    dentro de la MISMA página en cada llamada (no abre pestañas nuevas), así
    "la última captura"/"la consola actual" son siempre inequívocas. El motor
    se crea perezosamente en la primera acción real (``_pagina_activa``), no
    en ``__init__`` -- construir una ``SesionNavegador()`` sin Playwright
    instalado nunca falla (ver punto 1 del docstring del módulo).

    ``motor_factory`` es el punto de inyección para tests: por defecto usa
    Playwright real (``_motor_playwright``), pero cualquier callable que
    devuelva algo compatible con ``MotorNavegador`` sirve -- así los tests de
    este módulo ejercitan TODA la lógica (política, capturas, consola, clic,
    escribir, cierre) sin abrir un navegador real, tal como pide el encargo.

    ``cerrar()`` (o el ``with``) debe llamarse siempre para liberar el
    proceso del navegador; es idempotente -- llamarlo dos veces no falla.
    """

    def __init__(
        self,
        *,
        capturas: AlmacenCapturas,
        motor_factory: Callable[[], MotorNavegador] | None = None,
        resolver: Callable[[str], list[str]] = resolver_ips,
        timeout_ms: float = _TIMEOUT_MS_DEFECTO,
        headless: bool = True,
    ) -> None:
        self._capturas = capturas
        self._motor_factory = motor_factory or (
            lambda: _motor_playwright(headless=headless, timeout_ms=timeout_ms)
        )
        self._resolver = resolver
        self._timeout_ms = timeout_ms
        self._motor: MotorNavegador | None = None
        self._pagina: PaginaNavegador | None = None
        self._mensajes: list[MensajeConsola] = []
        self._mensajes_truncados = False

    def _pagina_activa(self) -> PaginaNavegador:
        if self._pagina is None:
            self._motor = self._motor_factory()
            pagina = self._motor.nueva_pagina()
            pagina.on("console", self._al_recibir_mensaje_consola)
            pagina.on("pageerror", self._al_recibir_error_pagina)
            self._pagina = pagina
        return self._pagina

    def _al_recibir_mensaje_consola(self, msg: Any) -> None:
        self._registrar(_mensaje_de_consola(msg))

    def _al_recibir_error_pagina(self, error: Any) -> None:
        self._registrar(_mensaje_de_error_pagina(error))

    def _registrar(self, mensaje: MensajeConsola) -> None:
        if len(self._mensajes) >= MAX_MENSAJES_CONSOLA:
            self._mensajes_truncados = True
            return
        self._mensajes.append(mensaje)

    def abrir(self, url: str, *, esperar: str = "load") -> dict[str, Any]:
        """Navega a ``url`` en la misma página. Bloquea ANTES de tocar el
        motor si ``evaluar_navegacion`` rechaza la URL -- así una URL
        prohibida nunca llega a lanzar Playwright ni gastar un import
        perezoso. Reinicia la consola acumulada: ``consola()`` refleja
        siempre la página actualmente cargada, no el historial completo de
        la sesión."""
        veredicto = evaluar_navegacion(url, resolver=self._resolver)
        if not veredicto.permitido:
            raise IDENavegadorError(veredicto.motivo)
        pagina = self._pagina_activa()
        self._mensajes.clear()
        self._mensajes_truncados = False
        try:
            respuesta = pagina.goto(url, timeout=self._timeout_ms, wait_until=esperar)
        except Exception as exc:
            raise IDENavegadorError(f"No se pudo abrir «{url}»: {exc}") from exc
        return {
            "url": pagina.url,
            "titulo": pagina.title(),
            "estado_http": getattr(respuesta, "status", None) if respuesta is not None else None,
        }

    def capturar(self, *, pantalla_completa: bool = False) -> CapturaAgente:
        """Toma una captura de la página actual y la deja lista en dos
        formas (ver ``CapturaAgente``): el bloque de imagen para el modelo,
        y la referencia en disco para el journal. Reusa
        ``ide_imagenes.validar_y_normalizar_imagen`` para el primero -- no
        reimplementa esa validación."""
        pagina = self._pagina_activa()
        try:
            datos = pagina.screenshot(full_page=pantalla_completa)
        except Exception as exc:
            raise IDENavegadorError(f"No se pudo tomar la captura: {exc}") from exc
        try:
            lista = validar_y_normalizar_imagen(datos)
        except IDEImagenError as exc:
            raise IDENavegadorError(f"La captura no se pudo procesar como imagen: {exc}") from exc
        datos_finales = base64.b64decode(lista.bloque["source"]["data"])
        guardado = self._capturas.guardar(
            datos_finales, ancho=lista.preparada.ancho, alto=lista.preparada.alto
        )
        return CapturaAgente(imagen=lista, guardado=guardado)

    def consola(self, *, solo_errores: bool = False) -> dict[str, Any]:
        """Mensajes de consola acumulados desde el último ``abrir()``.
        ``solo_errores=True`` filtra a ``error``/``pageerror`` -- el caso de
        uso más común: "¿la página que acabo de cargar tiene errores?"."""
        mensajes = self._mensajes
        if solo_errores:
            mensajes = [m for m in mensajes if m.tipo in ("error", "pageerror")]
        return {
            "mensajes": [m.public() for m in mensajes],
            "truncado": self._mensajes_truncados,
        }

    def clic(self, selector: str) -> dict[str, Any]:
        pagina = self._pagina_activa()
        try:
            pagina.click(selector, timeout=self._timeout_ms)
        except Exception as exc:
            raise IDENavegadorError(f"No se pudo hacer clic en «{selector}»: {exc}") from exc
        return {"selector": selector, "url": pagina.url}

    def escribir(self, selector: str, texto: str, *, enter: bool = False) -> dict[str, Any]:
        pagina = self._pagina_activa()
        try:
            pagina.fill(selector, texto, timeout=self._timeout_ms)
            if enter:
                pagina.press(selector, "Enter")
        except Exception as exc:
            raise IDENavegadorError(f"No se pudo escribir en «{selector}»: {exc}") from exc
        return {"selector": selector, "url": pagina.url}

    def cerrar(self) -> None:
        """Idempotente: llamarlo sin haber abierto nunca una página, o
        llamarlo dos veces seguidas, no falla."""
        if self._motor is not None:
            motor, self._motor, self._pagina = self._motor, None, None
            motor.cerrar()

    def __enter__(self) -> SesionNavegador:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.cerrar()
