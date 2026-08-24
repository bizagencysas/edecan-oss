"""Envoltorio del LSP que opencode ya trae -- ``GET /find/symbol`` y ``GET /lsp``.

# POR QUÉ existe este archivo

El encargo: "opencode expone ``/api/reference``, ``/find/symbol`` y ``/lsp``. Nadie
los usa." Este módulo los envuelve, con :class:`~edecan_companion.ide_opencode.
ServidorOpencode` como cliente (su ``.puerto`` público, nunca su cliente HTTP
privado -- ver "Por qué un ``httpx.AsyncClient`` propio" más abajo).

# Cómo se sacaron las formas -- NADA se inventó

Se arrancó un ``opencode serve`` real (1.17.18) en esta máquina y se leyó
``GET /doc`` completo (OpenAPI 3.1, ~480 KB, ~150 rutas enumeradas una por
una) para las CUATRO capacidades que pedía el encargo. Resultado, verificado
contra el servidor real, no supuesto:

1. **Buscar símbolos por nombre** -- SÍ existe: ``GET /find/symbol?query=...``
   (``operationId: find.symbols``). Devuelve ``Symbol[]`` (nombre, ``kind``
   numérico LSP, ``location`` con ``uri`` + rango). Wrapped como
   :meth:`ClienteLspOpencode.buscar_simbolos`.

2. **Estado de los servidores LSP** -- SÍ existe: ``GET /lsp``
   (``operationId: lsp.status``). Devuelve ``LSPStatus[]`` (``id``, ``name``,
   ``root``, ``status`` -- ``"connected"`` o ``"error"``). Wrapped como
   :meth:`ClienteLspOpencode.estado_lsp`.

3. **Definición de un símbolo en archivo+posición** (LSP
   ``textDocument/definition``) -- **NO EXISTE**. Se enumeraron las ~150
   rutas de ``/doc`` una por una (ver el bloque comentado al final de este
   docstring si hace falta reproducirlo) -- no hay ``/definition``,
   ``/lsp/definition`` ni nada equivalente bajo ningún prefijo. Lo más
   cercano es que un ``Symbol`` devuelto por ``buscar_simbolos`` YA trae su
   ``location`` (que es, de hecho, su definición) -- pero eso es resultado de
   una búsqueda por NOMBRE en el workspace, no de "dame la definición del
   símbolo que está en la posición X,Y del archivo Z", que es lo que pedía
   el encargo. No se fabrica un método para esto -- ver
   :meth:`ClienteLspOpencode.definicion`, que existe solo para lanzar un
   error explicativo si alguien lo intenta.

4. **Referencias de un símbolo** (LSP ``textDocument/references``) -- **NO
   EXISTE** tampoco. OJO con la trampa: ``GET /api/reference`` SÍ existe y
   suena a lo mismo, pero es un concepto TOTALMENTE DISTINTO -- devuelve
   ``ReferenceInfo[]`` (``name``, ``path``, ``description``, ``source``:
   ``ReferenceGitSource`` | ``ReferenceLocalSource``), que es el catálogo de
   *documentos de referencia* que opencode puede dar de contexto al agente
   (algo así como un índice de "reference docs" del proyecto), no
   referencias de código en el sentido LSP. Confundirlos habría sido
   exactamente "fabricar un envoltorio que devuelve vacío" con una etiqueta
   engañosa -- por eso ``/api/reference`` NO se envuelve aquí. Ver
   :meth:`ClienteLspOpencode.referencias`.

5. **Diagnósticos del archivo abierto** -- **NO EXISTE como ruta HTTP**,
   pero SÍ existe como comando de depuración interno del propio binario:
   ``opencode debug lsp diagnostics <archivo>`` (confirmado con
   ``opencode debug lsp --help``: ``diagnostics <file>``, ``symbols
   <query>``, ``document-symbols <uri>``). Esos tres subcomandos NO
   aparecen en ``/doc`` bajo ninguna forma -- son CLI pura, no API. Ver
   :meth:`ClienteLspOpencode.diagnosticos`.

# El hallazgo que manda: por qué ``buscar_simbolos``/``estado_lsp`` casi
# siempre devuelven ``[]`` contra un ``opencode serve`` real -- y por qué
# eso NO es un bug de este envoltorio

Esto es lo que más tiempo de esta ronda se llevó verificar, así que queda
documentado con evidencia real, no como sospecha:

**Paso 0 -- LSP está apagado por defecto.** Sin ``"lsp": true`` (u objeto
equivalente) en el ``opencode.json`` del directorio de trabajo, el log real
dice, siempre: ``message="all LSPs are disabled"``. Con ``"lsp": true`` pasa
a: ``message="enabled LSP servers" serverIds="zls, yaml-ls, ... typescript,
... pyright, ..."`` -- una lista de ~35 servidores integrados. Esto se
comprobó arrancando el mismo ``opencode serve`` dos veces, una sin la clave
y otra con ella, mirando
``~/.local/share/opencode/log/opencode.log`` en cada caso.

**Paso 1 -- "enabled" no es "corriendo".** Esa lista es solo la
configuración disponible -- ningún proceso de servidor de lenguaje se lanza
todavía. Se comprobó: tras arrancar con ``lsp: true`` y esperar minutos
mientras se llamaba repetidamente a ``GET /lsp`` y ``GET /find/symbol``, sin
tocar ningún archivo, ambos siguieron devolviendo ``[]`` sin excepción.

**Paso 2 -- ¿qué SÍ dispara un servidor de lenguaje real?** Solo una cosa,
encontrada explorando ``opencode debug lsp --help``:
``opencode debug lsp diagnostics <archivo>``. Se comprobó contra un
repo Python de juguete (``util.py``/``main.py``, sin ``pyright`` instalado
de antemano en el PATH): el comando tardó ~2.8s (tiempo real de instalar y
arrancar ``pyright`` la primera vez) y devolvió
``{"<ruta-absoluta>/main.py": []}`` -- diagnósticos reales (lista vacía
porque el archivo no tenía errores), con el log mostrando
``message="touching file" file=main.py`` justo antes. Eso prueba que la
integración LSP de opencode FUNCIONA de verdad -- pyright se instala solo y
responde.

**Paso 3 -- ese "touch" es inalcanzable desde ``opencode serve``.** Se
probaron, contra el MISMO servidor HTTP largo-vivo (no CLI efímero), estas
tres vías reales para intentar que ese mismo "touch" ocurra:

- Pedir a un modelo real (Kimi K2.7 vía Workers AI, sesión de verdad, sin
  simular) que llamara a su herramienta ``read`` sobre el archivo -- se
  comprobó en el stream SSE que la herramienta SÍ se ejecutó
  (``session.next.tool.success``) -- pero ningún ``"touching file"`` salió
  en el log del servidor para esa ruta.
- Pedirle que llamara a su herramienta ``edit`` -- el archivo CAMBIÓ en
  disco de verdad (se comprobó por contenido) -- tampoco disparó ningún
  ``"touching file"``.
- ``GET /file/content`` (la ruta de lectura cruda de archivo que expone
  ``opencode serve``) -- tampoco.

Es decir: el ``LSP.touchFile()`` interno que sí usa
``opencode debug lsp diagnostics`` NO está cableado a ninguna ruta HTTP de
``opencode serve`` -- ni al leer, ni al editar, ni a ningún endpoint de
``/doc``. Y aunque lo estuviera, cada invocación de la CLI de depuración crea
su propia "instance" efímera (``"creating instance"`` / ``"disposing
instance"`` en el log, unos milisegundos entre sí) -- un ``touch`` hecho por
un proceso CLI de depuración NUNCA compartiría estado con el proceso
``opencode serve`` largo-vivo que usa este adaptador, aunque el HTTP lo
expusiera.

**Conclusión verificada, no supuesta:** ``buscar_simbolos`` y ``estado_lsp``
son envoltorios CORRECTOS de rutas HTTP reales y documentadas -- pero contra
la superficie pública de ``opencode serve`` 1.17.18, hoy, siempre devuelven
``[]``, sin importar si el servidor de lenguaje del proyecto está instalado
o no (de hecho se comprobó CON pyright funcionando de verdad vía CLI y el
resultado por HTTP fue igual de vacío). Esto se deja en ``pendiente`` del
encargo: no hay, en la API pública documentada, ninguna manera de pedirle a
``opencode serve`` que abra/indexe un archivo para su LSP. Cuando opencode
cierre ese hueco (cablee ``read``/``edit`` al mismo ``touchFile`` que ya usa
su CLI, o publique un endpoint ``POST /file/open`` o similar), este mismo
envoltorio empezará a devolver datos reales sin cambiar una línea -- por eso
NO se fabricó un ``touch`` propio abriendo un segundo proceso CLI por fuera:
además de no compartir estado con el servidor vivo (punto anterior), sería
inventar comportamiento que la API pública no ofrece, justo lo que la regla
del encargo prohíbe.

# Qué SÍ es legible con esto tal como está

- ``estado_lsp()``/``buscar_simbolos()`` nunca fallan silenciosamente contra
  un servidor sano: cuando opencode responde 400 (probado de verdad: un
  ``GET /find/symbol`` sin ``query`` responde ``400`` con
  ``{"name": "BadRequest", "data": {"message": "Missing key\\n  at
  [\\"query\\"]", "kind": "Query"}}``), el error se propaga con ese mensaje
  real -- no un genérico. Esta forma de error es DISTINTA de la que usa
  ``ide_opencode.py`` para las rutas ``/api/`` (que llevan ``_tag``); aquí,
  fuera de ``/api/``, es la superficie vieja con ``name``/``data.message``
  -- por eso este módulo tiene su propio parseo de error, no reutiliza
  ``ide_opencode._error_desde_cuerpo``.
- Si el proceso ``opencode serve`` se cae o nunca arrancó, se propaga
  ``ErrorOpencode`` con el fallo de transporte real (mismo criterio que
  ``ide_opencode.py`` regla 6: nada se traga).

# Por qué un ``httpx.AsyncClient`` propio, no el de ``ServidorOpencode``

``ServidorOpencode._cliente`` (privado) tiene ``base_url=".../api"`` --
``/find/symbol`` y ``/lsp`` viven FUERA de ese prefijo (ver el punto 1 del
docstring de ``ide_opencode.py``: la superficie ``/api/`` es la única que
usa ese adaptador a propósito). En vez de tocar el atributo privado de otro
módulo (mala capa) o reconstruir el ``base_url`` a mano en cada llamada,
este módulo abre su PROPIO cliente contra ``http://127.0.0.1:{puerto}``
(sin ``/api``), usando solo el ``.puerto`` público de ``ServidorOpencode``.
Mismo proceso, mismo servidor -- dos clientes HTTP livianos apuntando a dos
prefijos distintos del mismo puerto.

# Dónde engancharlo (para la ronda que haga el cableado)

Este archivo NO toca ``ide_sessions.py``, ``ide_workers_agent.py``, ni
``ide_runtime.py`` -- ver la nota del encargo. Cuando se cablee:

- Construir un ``ClienteLspOpencode`` por sesión activa (o uno compartido
  por servidor, ya que no lleva estado de sesión -- las rutas que envuelve
  no son ``/session/{id}/...``, son globales al workspace) a partir del
  mismo ``ServidorOpencode`` que ya administra ``SessionManager``/
  ``MotorOpencode`` (ver ``ide_opencode_motor.py``).
  ``ClienteLspOpencode.creado_desde(servidor)`` deja explícito ese enganche.
- Antes de exponerlo en la UI, alguien tiene que decidir qué hacer con el
  hallazgo de este docstring: hoy esto va a devolver listas vacías salvo
  que opencode cambie su comportamiento. Un ``IDE_ACTIONS`` que llame a
  esto sin avisar al dueño de esa limitación repetiría el mismo patrón que
  ya delató el dueño ocho veces (ver Hecho 2 del encargo): una capacidad
  que "existe" pero no hace nada visible.
"""

from __future__ import annotations

import json
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict

from .ide_opencode import ErrorOpencode, ServidorOpencode

# --------------------------------------------------------------------------- #
# Modelos Pydantic v2 -- formas leídas de /doc, ver docstring del módulo
# --------------------------------------------------------------------------- #


class PosicionLsp(BaseModel):
    """``#/components/schemas/Range.start`` (o ``.end``) -- posición LSP 0-indexada."""

    model_config = ConfigDict(extra="forbid")

    line: int
    character: int


class RangoLsp(BaseModel):
    """``#/components/schemas/Range``."""

    model_config = ConfigDict(extra="forbid")

    start: PosicionLsp
    end: PosicionLsp


class UbicacionSimboloLsp(BaseModel):
    """``Symbol.location`` -- URI del archivo + rango dentro de él."""

    model_config = ConfigDict(extra="forbid")

    uri: str
    range: RangoLsp


class SimboloLsp(BaseModel):
    """``#/components/schemas/Symbol`` -- un elemento de ``GET /find/symbol``.

    ``kind`` es el entero de ``SymbolKind`` del protocolo LSP (1=File,
    5=Class, 12=Function, ... -- ver la especificación de LSP, opencode no
    lo traduce a texto). Se deja como ``int`` tal cual lo manda opencode en
    vez de mapearlo a un enum propio: mapear mal un ``kind`` que no se ha
    visto en la práctica (solo se comprobaron respuestas vacías, ver
    docstring del módulo) sería inventar sin evidencia.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    kind: int
    location: UbicacionSimboloLsp


class EstadoServidorLsp(BaseModel):
    """``#/components/schemas/LSPStatus`` -- un elemento de ``GET /lsp``."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    root: str
    status: Literal["connected", "error"]


# --------------------------------------------------------------------------- #
# Error -- forma DISTINTA a la de ide_opencode.py, ver docstring del módulo
# --------------------------------------------------------------------------- #


def _error_desde_cuerpo_legado(
    status: int, cuerpo_bytes: bytes, metodo: str, ruta: str
) -> ErrorOpencode:
    """``/find/symbol`` y ``/lsp`` NO viven bajo ``/api`` -- ver el punto
    "Por qué un httpx.AsyncClient propio" del docstring del módulo. Su forma
    de error, comprobada contra un ``opencode serve`` real (``GET
    /find/symbol`` sin ``query``), es ``BadRequestError``::

        {"name": "BadRequest", "data": {"message": "...", "kind": "Query"}}

    DISTINTA de la que usa ``ide_opencode.py::_error_desde_cuerpo`` para la
    superficie ``/api/`` (que lleva ``_tag``/``message`` al nivel raíz) --
    por eso este módulo no reutiliza esa función y tiene la suya, con el
    mismo espíritu de la regla 6 del encargo: el mensaje real de opencode
    siempre llega, nunca uno inventado.
    """

    mensaje = f"opencode devolvió {status} en {metodo} {ruta}"
    nombre: str | None = None
    cuerpo: Any = None
    texto = cuerpo_bytes.decode("utf-8", errors="replace")
    try:
        cuerpo = json.loads(texto) if texto else None
    except json.JSONDecodeError:
        cuerpo = texto
    if isinstance(cuerpo, dict):
        nombre = cuerpo.get("name")
        datos = cuerpo.get("data")
        mensaje_real = datos.get("message") if isinstance(datos, dict) else None
        if mensaje_real:
            mensaje = (
                f"{mensaje} ({nombre}): {mensaje_real}" if nombre else f"{mensaje}: {mensaje_real}"
            )
    elif isinstance(cuerpo, str) and cuerpo:
        mensaje = f"{mensaje}: {cuerpo[:500]}"
    return ErrorOpencode(mensaje, tag=nombre, status=status, cuerpo=cuerpo)


# --------------------------------------------------------------------------- #
# El envoltorio
# --------------------------------------------------------------------------- #


class LspOpencodeNoDisponibleError(NotImplementedError):
    """Se pidió una capacidad LSP que ``opencode serve`` 1.17.18 no expone
    por HTTP -- ver el docstring del módulo, puntos 3-5, para la evidencia
    de por qué no se fabricó un envoltorio que devolviera vacío en su
    lugar."""


class ClienteLspOpencode:
    """Cliente HTTP contra las rutas LSP de ``opencode serve`` (fuera de
    ``/api``): ``GET /find/symbol`` y ``GET /lsp``. Ver el docstring del
    módulo para qué SÍ y qué NO envuelve, y por qué.

    Se construye con :meth:`creado_desde`, a partir de un
    :class:`~edecan_companion.ide_opencode.ServidorOpencode` ya vivo (nunca
    directo -- necesita su puerto real). Es un gestor de contexto asíncrono::

        async with await ServidorOpencode.iniciar(directorio) as servidor:
            async with ClienteLspOpencode.creado_desde(servidor) as lsp:
                simbolos = await lsp.buscar_simbolos("saludar")
                estado = await lsp.estado_lsp()
    """

    def __init__(self, *, puerto: int, cliente: httpx.AsyncClient) -> None:
        self._puerto = puerto
        self._cliente = cliente
        self._cerrado = False

    @classmethod
    def creado_desde(
        cls, servidor: ServidorOpencode, *, timeout_lectura_http: float = 30.0
    ) -> ClienteLspOpencode:
        """A partir de un :class:`ServidorOpencode` ya arrancado -- usa solo
        su ``.puerto`` público (ver "Por qué un httpx.AsyncClient propio" en
        el docstring del módulo, nunca su cliente HTTP privado)."""

        cliente = httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{servidor.puerto}",
            timeout=httpx.Timeout(connect=10.0, read=timeout_lectura_http, write=10.0, pool=10.0),
        )
        return cls(puerto=servidor.puerto, cliente=cliente)

    async def cerrar(self) -> None:
        if self._cerrado:
            return
        self._cerrado = True
        await self._cliente.aclose()

    async def __aenter__(self) -> ClienteLspOpencode:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.cerrar()

    # ----------------------------------------------------------------- #
    # HTTP interno
    # ----------------------------------------------------------------- #

    async def _solicitar(
        self, metodo: str, ruta: str, *, params: dict[str, Any] | None = None
    ) -> httpx.Response:
        """Mismo criterio de honestidad que
        ``ide_opencode.ServidorOpencode._solicitar`` (regla 6 del encargo:
        nada se traga) pero SIN sus reintentos de ``ConnectError`` -- estas
        dos rutas son ``GET`` puros de solo lectura (a diferencia de
        ``POST /session/{id}/prompt``, reintentar un ``GET`` nunca duplica
        un efecto secundario), así que un fallo de conexión se propaga tal
        cual, de inmediato: quien llame decide si reintenta."""

        try:
            resp = await self._cliente.request(metodo, ruta, params=params)
        except httpx.TimeoutException as exc:
            raise ErrorOpencode(
                f"opencode no respondió a tiempo en {metodo} {ruta}: {exc}"
            ) from exc
        except httpx.TransportError as exc:
            raise ErrorOpencode(
                f"No se pudo conectar con opencode en {metodo} {ruta}: {exc}"
            ) from exc
        if resp.status_code >= 400:
            raise _error_desde_cuerpo_legado(resp.status_code, resp.content, metodo, ruta)
        return resp

    # ----------------------------------------------------------------- #
    # Lo que SÍ existe -- ver docstring del módulo, puntos 1-2
    # ----------------------------------------------------------------- #

    async def buscar_simbolos(
        self, query: str, *, directorio: str | None = None, workspace: str | None = None
    ) -> list[SimboloLsp]:
        """``GET /find/symbol`` -- busca símbolos del workspace (funciones,
        clases, variables) cuyo nombre coincide con ``query``, vía LSP.

        ADVERTENCIA verificada (ver el docstring del módulo, sección "El
        hallazgo que manda"): contra un ``opencode serve`` real, esto casi
        siempre devuelve ``[]`` -- no porque falte un servidor de lenguaje
        instalado, sino porque la superficie HTTP pública de opencode no
        tiene ninguna ruta que le pida "abrí/indexá este archivo" antes de
        buscar. Un ``[]`` de este método NO es evidencia de "no se encontró
        el símbolo" -- es, hoy, el resultado esperado casi siempre.
        """

        params: dict[str, Any] = {"query": query}
        if directorio is not None:
            params["directory"] = str(directorio)
        if workspace is not None:
            params["workspace"] = workspace
        resp = await self._solicitar("GET", "/find/symbol", params=params)
        return [SimboloLsp.model_validate(elemento) for elemento in resp.json()]

    async def estado_lsp(
        self, *, directorio: str | None = None, workspace: str | None = None
    ) -> list[EstadoServidorLsp]:
        """``GET /lsp`` -- estado de conexión de los servidores de lenguaje
        para el workspace pedido (``id``/``name``/``root``/``status``, con
        ``status`` en ``"connected"`` o ``"error"``).

        Misma advertencia que :meth:`buscar_simbolos`: se comprobó, con
        ``pyright`` funcionando de verdad (probado vía
        ``opencode debug lsp diagnostics``), que esta lista sigue vacía por
        HTTP -- porque ningún servidor llega a levantarse dentro del
        proceso ``opencode serve`` sin el "touch" que hoy solo dispara esa
        CLI de depuración, inalcanzable desde acá. Una lista vacía de este
        método NO distingue "no hay servidor de lenguaje instalado para
        este proyecto" de "opencode nunca intentó levantar ninguno" -- son
        indistinguibles con la API pública actual. Si opencode alguna vez
        puebla esta lista con una entrada ``status="error"``, este método sí
        la propaga tal cual (el modelo la tipa) -- el problema hoy es que
        nunca llega ni siquiera a intentarlo.
        """

        params: dict[str, Any] = {}
        if directorio is not None:
            params["directory"] = str(directorio)
        if workspace is not None:
            params["workspace"] = workspace
        resp = await self._solicitar("GET", "/lsp", params=params)
        return [EstadoServidorLsp.model_validate(elemento) for elemento in resp.json()]

    # ----------------------------------------------------------------- #
    # Lo que NO existe -- ver docstring del módulo, puntos 3-5. Estos
    # métodos existen a propósito, solo para fallar con una explicación
    # legible en vez de dejar un AttributeError mudo si alguien los busca.
    # ----------------------------------------------------------------- #

    async def definicion(self, *_args: Any, **_kwargs: Any) -> None:
        """NO EXISTE en ``opencode serve`` 1.17.18. Se leyó ``/doc`` completo
        (~150 rutas) buscando ``textDocument/definition`` o equivalente
        (``/definition``, ``/lsp/definition``, etc.) -- no hay ninguna ruta
        para "dame la definición del símbolo en archivo X, posición Y".
        :meth:`buscar_simbolos` sí trae la ubicación de un símbolo, pero
        como resultado de una búsqueda por NOMBRE en el workspace, no de una
        posición dada."""

        raise LspOpencodeNoDisponibleError(
            "opencode serve 1.17.18 no expone ningún endpoint de definición "
            "(LSP textDocument/definition) -- ver el docstring de "
            "ide_opencode_lsp.py, punto 3, para la verificación completa "
            "contra /doc real. Lo más cercano que SÍ existe es "
            "ClienteLspOpencode.buscar_simbolos(), que devuelve la ubicación "
            "de un símbolo encontrado por NOMBRE, no por posición."
        )

    async def referencias(self, *_args: Any, **_kwargs: Any) -> None:
        """NO EXISTE en ``opencode serve`` 1.17.18. ``GET /api/reference``
        existe pero es un concepto DISTINTO (catálogo de documentos de
        referencia para el agente, ``ReferenceInfo``: ``name``/``path``/
        ``description``/``source`` -- no referencias de código LSP). Ver el
        docstring del módulo, punto 4."""

        raise LspOpencodeNoDisponibleError(
            "opencode serve 1.17.18 no expone ningún endpoint de "
            "referencias de código (LSP textDocument/references). "
            "GET /api/reference EXISTE pero es un concepto no relacionado "
            "(documentos de referencia del proyecto para el agente, no "
            "referencias de un símbolo) -- ver el docstring de "
            "ide_opencode_lsp.py, punto 4, para la verificación completa "
            "contra /doc real."
        )

    async def diagnosticos(self, *_args: Any, **_kwargs: Any) -> None:
        """NO EXISTE como ruta HTTP en ``opencode serve`` 1.17.18. Existe
        como comando de depuración de la CLI (``opencode debug lsp
        diagnostics <archivo>``, confirmado funcionando de verdad contra un
        archivo Python real -- ver el docstring del módulo, punto 5 y "El
        hallazgo que manda") pero esa CLI no aparece en ``/doc`` bajo
        ninguna forma y crea su propia instancia efímera, sin compartir
        estado con el ``opencode serve`` que administra este adaptador."""

        raise LspOpencodeNoDisponibleError(
            "opencode serve 1.17.18 no expone diagnósticos por HTTP -- solo "
            "existen vía 'opencode debug lsp diagnostics <archivo>', un "
            "comando de CLI interno (no documentado en /doc, no reproducible "
            "desde este cliente HTTP sin abrir un proceso nuevo que no "
            "compartiría estado con el opencode serve vivo) -- ver el "
            "docstring de ide_opencode_lsp.py, punto 5, para la verificación "
            "completa."
        )
