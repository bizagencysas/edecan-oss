"""Auto-crítica del agente del IDE: la lección de un error ya resuelto vuelve
sola cuando ese error reaparece, aunque sea en otro proyecto.

Qué problema resuelve. ``ide_verificacion.ejecutar_hasta_que_pase`` ya sabe
insistir hasta que el comando pasa, pero lo aprendido en el camino muere con
el bucle: si el primer intento se cayó por un ``ModuleNotFoundError`` y el
agente tardó tres pasos en entender que faltaba declarar la dependencia, la
próxima vez -- mañana, en otro repo -- vuelve a tardar los mismos tres pasos.
``ide_memoria.py`` guarda algo parecido (``error_evitar``) pero atado a UN
workspace, así que no cruza de proyecto; y aun dentro del mismo repo depende
de que alguien se acuerde de consultarlo.

**El problema real no es guardar la lección, es recuperarla en el momento
correcto.** Una lista de "lecciones aprendidas" que el agente tiene que
acordarse de leer es un diario que nadie abre. Por eso este módulo no ofrece
una búsqueda por palabras: ofrece un índice por FIRMA DE ERROR, la que
``ide_verificacion._firma_de_resultado`` ya calcula para cada intento fallido
(corta, estable, sin tiempos de pytest ni ruido). El disparador es mecánico:

    intento falla -> tiene firma -> ``leccion_para(firma)`` -> si hay lección,
    se inyecta ANTES de que el modelo intente arreglarlo de nuevo.

Nadie tiene que acordarse de nada. El error trae su propia lección pegada.

Qué merece guardarse (el criterio, que es la parte pensada)
-----------------------------------------------------------
- Un bucle que aprobó tras MÁS de un intento deja lección: la firma del
  primer error + qué lo arregló. Es una lección con evidencia (el comando de
  verificación pasó después), no una opinión del modelo.
- Un bucle que aprobó al primer intento NO deja nada: salió bien a la
  primera, no hubo nada que aprender.
- Un bucle que nunca aprobó tampoco deja lección: no se sabe qué lo arregla,
  y guardar el intento fallido como si fuera receta es peor que no guardar.
- El ``arreglo`` lo aporta quien integra (sabe qué cambió el modelo); sin él
  no se guarda nada y se dice por qué (``motivo == "sin_arreglo"``). Una
  lección que solo dice "este error se pudo arreglar" no le sirve a nadie.

Confianza: una lección equivocada es peor que ninguna
-----------------------------------------------------
Una lección se inyecta justo en el momento en que el modelo iba a pensar, así
que si está mal, no solo no ayuda: da falsa seguridad exactamente donde hacía
falta pensar. Por eso cada lección se somete a la misma evidencia que la
creó. Si una firma con lección YA SERVIDA vuelve a fallar idéntica en el
intento siguiente (el mismo criterio de "está atascado" que usa
``ide_verificacion``), la lección se anota una falla y pierde confianza; a la
segunda falla se retira sola (``MAX_FALLAS_ANTES_DE_RETIRAR``).

Los aciertos suben ``confianza`` pero NO compran tolerancia a fallas: el tope
de fallas es absoluto. Una lección con historial que empezó a fallar es
justamente la más peligrosa, porque su confianza alta invita a confiar sin
mirar.

Alcance: global, entre proyectos
--------------------------------
Como ``ide_conocimiento.py`` y al revés de ``ide_memoria.py``: un
``ModuleNotFoundError`` mal resuelto en un repo es la misma trampa en el
siguiente. Es seguro compartirlo por CÓMO se guarda, no por confianza: cada
lección exige una firma de error real producida por una corrida real y un
arreglo que se verificó (el comando pasó después), no texto libre del modelo.
El ``workspace_id`` se conserva solo como procedencia.

Privacidad: el repo es público y esto se persiste
--------------------------------------------------
Una firma de pytest o de un compilador puede traer rutas absolutas de la
máquina de quien lo corrió (``/Users/<alguien>/...``), y esas rutas llevan
adentro el nombre de una persona. ``normalizar_firma`` las recorta ANTES de
guardar (ver esa función): además de no persistir un dato personal, hace la
firma comparable entre máquinas y proyectos, que es justo lo que este índice
global necesita.

Integración prevista (no se hace desde este archivo, ver plan §9.1): quien
cablee ``ide_verificacion`` con el agente antepone ``bloque_para(firma)`` al
prompt dentro de su callback ``arreglar`` -- UNA llamada, no dos:
``bloque_para`` ya sirve la lección por dentro, así que llamar además a
``leccion_para`` para lo mismo contaría dos usos por una sola inyección y
ensuciaría la estadística con la que se desaloja y se decide a quién creer.
``leccion_para`` es para cuando se quieren los campos crudos (una vista, una
decisión propia), no para acompañar a ``bloque_para``. Al terminar
``ejecutar_hasta_que_pase`` se llama una vez a
``registrar_bucle(resultado, arreglo=...)``. Este módulo NO importa nada de
``ide_sessions.py``, ``ide_workers_agent.py`` ni ``routers/ide.py``, y no
modifica ``ide_verificacion.py``: solo lee sus tipos.

Pendiente que NO se puede cerrar desde acá (toca ``ide_verificacion.py``): el
caso estrella del plan §9.1, el ``ModuleNotFoundError`` que se repite entre
repos, hoy NO deja lección. ``_detectar_dependencia_faltante`` devuelve el
nombre de la excepción y no el módulo ausente, así que la firma que llega es
``no_se_pudo_ejecutar:dependencia:ModuleNotFoundError``, idéntica para
cualquier paquete de cualquier proyecto. Este módulo la descarta a propósito
(ver ``_CUERPOS_DE_FIRMA_SIN_CONTENIDO``) porque servirla sería servir la
lección de otro repo. Para recuperar el caso, esa función tiene que devolver
la línea completa del error (la que nombra el módulo), no solo la etiqueta.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from edecan_companion.ide_verificacion import ResultadoBucle
from edecan_companion.ide_workspaces import IDEWorkspaceError, WorkspaceStore

# -- Topes de tamaño ------------------------------------------------------ #
# La firma es una CLAVE de índice, no un informe: lo que la identifica está
# en sus primeras líneas (el "exit=N" y los primeros fallos que lista
# pytest/el compilador). Se recorta por el final -- al revés que
# ``ide_verificacion._recortar``, que recorta por el principio porque ahí lo
# que importa es que el modelo LEA la causa, no que la clave sea estable.
MAX_FIRMA_CHARS = 600
# Una firma cuyo cuerpo (lo que sigue al primer ``:``) es vacío o casi
# -- p. ej. ``"exit=1:"`` de un comando que falló sin imprimir nada -- no
# sirve como clave: coincidiría con cualquier fallo mudo de cualquier
# proyecto y serviría la lección equivocada. Se descarta.
MIN_CUERPO_FIRMA_CHARS = 4

# Cuerpos de firma que no identifican NADA aunque sean largos: son etiquetas
# fijas que ``ide_verificacion`` emite igual para cualquier proyecto y
# cualquier caso. El tope de longitud no las atrapa, y son justo las firmas
# que más se repiten entre repos -- o sea, las que este índice global más
# tiende a acumular.
#
# El caso que obliga a esta lista: ``_detectar_dependencia_faltante`` devuelve
# el nombre de la EXCEPCIÓN, no el módulo que falta, así que un
# ``ModuleNotFoundError`` de un paquete acá y el de otro paquete distinto en
# otro repo producen la misma firma exacta. Indexar por ella haría que la
# lección "declara tal dependencia" se sirviera ante cualquier módulo ausente
# del mundo, con confianza 1.0 y subiendo (cada acierto ajeno la refuerza).
# Eso es exactamente la lección equivocada de la que este módulo se cuida.
# Se descartan hasta que la firma de origen traiga el dato que falta; ver la
# nota del docstring del módulo sobre qué hay que corregir en
# ``ide_verificacion`` para recuperar este caso, que es el del plan §9.1.
_CUERPOS_DE_FIRMA_SIN_CONTENIDO = frozenset(
    {
        "modulenotfounderror",
        "importerror",
        "no module named",
        "cannot find module",
        "module_not_found",
        "command not found",
        "is not recognized as an internal or external command",
        "timeout",
    }
)
# Mismo espíritu que ``ide_memoria.MIN/MAX_CONTENT_CHARS``: el arreglo es una
# instrucción concreta ("declara `pyyaml` en las dependencias del paquete"),
# ni una interjección ni el diff completo.
MIN_ARREGLO_CHARS = 12
MAX_ARREGLO_CHARS = 600

# Tope global de lecciones. No es por proyecto (este almacén es global) y no
# hace falta que sea grande: el valor de una lección está en que su firma
# reaparezca, y las firmas que reaparecen son pocas y repetidas.
MAX_LECCIONES = 300

# Cuántas fallas tolera una lección antes de retirarse sola. 2 = "pierde
# confianza, y si reincide se va": la primera puede ser mala suerte (el
# modelo la aplicó mal, o el caso se parecía sin ser el mismo); la segunda ya
# es un patrón, y una lección que engaña dos veces no merece una tercera.
MAX_FALLAS_ANTES_DE_RETIRAR = 2

# Cuánto pesa una falla frente a un acierto al calcular ``confianza``. Es
# asimétrico a propósito: acertar es lo esperable (la lección nació de un
# arreglo verificado), fallar es la señal informativa.
PESO_DE_UNA_FALLA = 3.0

# Cuántos segmentos finales de una ruta se conservan al anonimizarla. Dos
# ("tests/test_x.py") alcanzan para distinguir archivos homónimos sin
# arrastrar el árbol de directorios de la máquina de nadie.
SEGMENTOS_DE_RUTA_QUE_SE_CONSERVAN = 2

_ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# Directorio personal: es la única parte de una ruta que contiene un dato de
# una persona (su nombre de usuario). Se colapsa a "~" antes que nada.
_HOME_POSIX_RE = re.compile(r"/(?:Users|home)/[^/\s:'\"]+", re.IGNORECASE)
_HOME_WINDOWS_RE = re.compile(r"[A-Za-z]:\\Users\\[^\\\s:'\"]+", re.IGNORECASE)
# Rutas absolutas (ya sin el nombre de usuario) y rutas bajo "~": se dejan
# solo los últimos segmentos. Las rutas RELATIVAS no se tocan a propósito --
# son las mismas en cualquier máquina y son la parte estable de la firma.
# El punto en el lookbehind hace dos cosas de una: deja intactas las rutas
# relativas explícitas ("./src/x.py", que ya son iguales en toda máquina) y
# vuelve idempotente la normalización -- sin él, volver a normalizar una
# firma ya guardada volvería a morder el ".../" que dejó la vez anterior.
_RUTA_POSIX_RE = re.compile(r"(?<![\w~.])~?/(?:[\w.@+-]+/)*[\w.@+-]+")
# La variante de Windows EXIGE letra de unidad o "~" antes de la barra: sin
# eso, un mensaje de error con un escape adentro (``assert 'a\nb' == ...``)
# parecería una ruta y se rompería el texto al "acortarlo".
_RUTA_WINDOWS_RE = re.compile(r"(?<![\w])(?:[A-Za-z]:|~)\\(?:[\w.@+-]+\\)*[\w.@+-]+")


class IDEAutocriticaError(ValueError):
    """Solicitud de auto-crítica inválida: no es un ``ResultadoBucle``, el
    arreglo tiene forma de ruido, el workspace no existe, o la lección
    pedida no está."""


def _now_iso() -> str:
    return time.strftime(_ISO_FORMAT, time.gmtime())


def _acortar_ruta(match: re.Match[str]) -> str:
    ruta = match.group(0)
    partes = [parte for parte in ruta.replace("\\", "/").split("/") if parte and parte != "~"]
    if not partes:
        return "~"
    if len(partes) <= SEGMENTOS_DE_RUTA_QUE_SE_CONSERVAN:
        # Ya es corta: dejarla tal cual (sin la barra inicial, que era lo
        # único que la ataba a una máquina concreta).
        return "/".join(partes)
    return ".../" + "/".join(partes[-SEGMENTOS_DE_RUTA_QUE_SE_CONSERVAN:])


def _anonimizar_rutas(texto: str) -> str:
    """Quita de un texto cualquier ruta absoluta de la máquina donde corrió.

    Dos motivos, y el segundo es el que hace que esto no sea solo higiene:

    1. Privacidad: ``/Users/<alguien>/proyecto/tests/test_x.py`` lleva
       adentro el nombre de una persona, y esto se persiste en un archivo
       JSON de un repositorio público.
    2. Comparabilidad: el índice de este módulo es GLOBAL. Una firma con la
       ruta absoluta del Mac de quien la generó no vuelve a coincidir nunca
       -- ni en otro repo, ni en otra máquina, ni siquiera en el mismo repo
       clonado en otra carpeta. Recortarla es lo que le da a la firma una
       chance real de reaparecer.
    """

    texto = _HOME_POSIX_RE.sub("~", texto)
    texto = _HOME_WINDOWS_RE.sub("~", texto)
    hogar = str(Path.home())
    if len(hogar) > 1:
        # Por si el directorio personal no vive bajo /Users ni /home (pasa en
        # servidores y contenedores): se colapsa igual, por su valor real.
        texto = texto.replace(hogar, "~")
    texto = _RUTA_POSIX_RE.sub(_acortar_ruta, texto)
    return _RUTA_WINDOWS_RE.sub(_acortar_ruta, texto)


def _anonimizar_comando(argv: Sequence[str]) -> tuple[str, ...]:
    """Misma tijera que la firma, pero sobre el ``argv`` que se guarda.

    Hace falta aparte porque el ``argv`` NO pasa por ``normalizar_firma`` y es
    el lugar donde más seguido aparece una ruta personal completa: el bucle de
    verificación acepta ejecutables absolutos a propósito (``ejecutar_intento``
    lo permite, y ``detectar_comando_de_verificacion`` recomienda pasar el
    intérprete del venv del proyecto), así que un comando típico es
    ``/Users/<alguien>/proyecto/.venv/bin/python -m pytest -q``. Sin esto, el
    nombre de usuario terminaba escrito tal cual en el JSON.
    """

    return tuple(_anonimizar_rutas(str(parte)) for parte in argv)


def normalizar_firma(firma: Any) -> str:
    """Deja una firma de ``ide_verificacion`` lista para usarse como clave.

    Anonimiza rutas (ver ``_anonimizar_rutas``), normaliza espacios en blanco
    -- un salto de línea de Windows y uno de Unix son el mismo error -- y
    recorta. Es idempotente: normalizar una firma ya normalizada devuelve lo
    mismo, que es lo que permite comparar lo guardado contra lo que llega sin
    llevar dos formas del mismo dato.
    """

    if firma is None:
        return ""
    if not isinstance(firma, str):
        raise IDEAutocriticaError("La firma debe ser texto (o None).")
    texto = _anonimizar_rutas(firma.replace("\r\n", "\n").replace("\r", "\n"))
    lineas = [re.sub(r"[ \t]+", " ", linea).strip() for linea in texto.split("\n")]
    texto = "\n".join(linea for linea in lineas if linea)
    if len(texto) > MAX_FIRMA_CHARS:
        texto = texto[:MAX_FIRMA_CHARS]
    return texto


def _firma_utilizable(firma_normalizada: str) -> bool:
    """¿Esta firma identifica un error concreto, o coincidiría con cualquiera?

    Todas las firmas que produce ``ide_verificacion`` tienen forma
    ``prefijo:cuerpo`` (``exit=1:...``, ``no_se_pudo_ejecutar:...``). El
    prefijo solo no distingue nada: si el cuerpo viene vacío (un comando que
    falló sin imprimir una línea), usarlo como clave haría que la lección de
    un proyecto se sirviera en el fallo mudo de otro.

    La longitud no alcanza como criterio: hay cuerpos largos que tampoco
    identifican nada porque son etiquetas fijas (ver
    ``_CUERPOS_DE_FIRMA_SIN_CONTENIDO``).
    """

    cuerpo = (
        firma_normalizada.split(":", 1)[1] if ":" in firma_normalizada else firma_normalizada
    ).strip()
    if len(cuerpo) < MIN_CUERPO_FIRMA_CHARS:
        return False
    # ``no_se_pudo_ejecutar:dependencia:<X>`` deja "dependencia:" en el cuerpo:
    # lo que hay que juzgar es la etiqueta de atrás, que es la que pretende
    # identificar el error.
    if cuerpo.casefold().startswith("dependencia:"):
        cuerpo = cuerpo.split(":", 1)[1].strip()
    return cuerpo.casefold() not in _CUERPOS_DE_FIRMA_SIN_CONTENIDO


def _limpiar_arreglo(raw: Any) -> str:
    if not isinstance(raw, str):
        raise IDEAutocriticaError("El arreglo debe ser texto.")
    texto = re.sub(r"\s+", " ", raw).strip()
    if len(texto) < MIN_ARREGLO_CHARS:
        raise IDEAutocriticaError(
            f"El arreglo es demasiado corto (mínimo {MIN_ARREGLO_CHARS} caracteres) -- "
            "tiene que decir QUÉ se cambió, no solo que se arregló."
        )
    if len(texto) > MAX_ARREGLO_CHARS:
        raise IDEAutocriticaError(
            f"El arreglo es demasiado largo (máximo {MAX_ARREGLO_CHARS} caracteres) -- "
            "es una instrucción concreta, no el diff completo."
        )
    return _anonimizar_rutas(texto)


def _confianza(veces_util: int, veces_fallida: int) -> float:
    if veces_util <= 0:
        return 0.0
    return round(veces_util / (veces_util + PESO_DE_UNA_FALLA * max(0, veces_fallida)), 4)


@dataclass
class Leccion:
    """Lo aprendido de UN error concreto, indexado por su firma."""

    id: str
    firma: str
    arreglo: str
    comando: tuple[str, ...]
    veces_util: int
    veces_fallida: int
    veces_servida: int
    created_at: str
    updated_at: str
    last_served_at: str | None
    workspace_id_origen: str | None

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "firma": self.firma,
            "arreglo": self.arreglo,
            "comando": list(self.comando),
            "veces_util": self.veces_util,
            "veces_fallida": self.veces_fallida,
            "veces_servida": self.veces_servida,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_served_at": self.last_served_at,
            "workspace_id_origen": self.workspace_id_origen,
            "confianza": _confianza(self.veces_util, self.veces_fallida),
        }

    @staticmethod
    def from_json(raw: dict[str, Any]) -> Leccion:
        firma = normalizar_firma(str(raw.get("firma") or ""))
        if not _firma_utilizable(firma):
            # No se degrada una fila corrupta a algo servible, igual que en
            # ``ide_conocimiento.HechoVerificado.from_json``: una lección con
            # firma inservible se ofrecería ante errores que no son el suyo.
            raise ValueError(f"firma inservible en fila de lección: {firma!r}")
        comando = raw.get("comando")
        return Leccion(
            id=str(raw["id"]),
            firma=firma,
            arreglo=str(raw.get("arreglo") or ""),
            # Se vuelve a anonimizar al leer, no solo al escribir: un archivo
            # guardado antes de que el comando se limpiara trae la ruta
            # personal adentro, y así se cura solo en el primer guardado.
            comando=_anonimizar_comando(comando) if isinstance(comando, list) else (),
            veces_util=int(raw.get("veces_util") or 0),
            veces_fallida=int(raw.get("veces_fallida") or 0),
            veces_servida=int(raw.get("veces_servida") or 0),
            created_at=str(raw.get("created_at") or _now_iso()),
            updated_at=str(raw.get("updated_at") or raw.get("created_at") or _now_iso()),
            last_served_at=str(raw["last_served_at"]) if raw.get("last_served_at") else None,
            workspace_id_origen=(
                str(raw["workspace_id_origen"]) if raw.get("workspace_id_origen") else None
            ),
        )


class AutocriticaStore:
    """Registro JSON local, atómico y privado de lecciones por firma de error.

    Mismo patrón de persistencia que ``ConocimientoStore`` y ``MemoriaStore``:
    un archivo, un lock reentrante, escritura atómica con ``os.replace`` y
    permisos ``0o600``.

    ``workspaces`` es opcional a propósito, y es la diferencia con los otros
    dos almacenes: allí el ``workspace_id`` decide QUÉ se ve, así que tiene
    que existir de verdad. Aquí el alcance es global y el workspace es solo
    procedencia (dónde se aprendió), así que quien llame puede no tener uno a
    mano -- ``ejecutar_hasta_que_pase`` no lo necesita para nada. Si se pasa
    un ``WorkspaceStore``, entonces sí se valida lo que se declare.
    """

    def __init__(
        self,
        state_dir: Path,
        workspaces: WorkspaceStore | None = None,
        *,
        max_lecciones: int = MAX_LECCIONES,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.workspaces = workspaces
        self.path = self.state_dir / "ide-autocritica.json"
        self.max_lecciones = max(1, int(max_lecciones))
        self._lock = threading.RLock()
        self._lecciones: dict[str, Leccion] = {}
        self._load()

    # -- persistencia --------------------------------------------------- #

    def _load(self) -> None:
        with self._lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
            rows = data.get("lecciones", []) if isinstance(data, dict) else []
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                    continue
                try:
                    leccion = Leccion.from_json(row)
                except (KeyError, ValueError, TypeError):
                    continue
                self._lecciones[leccion.id] = leccion

    def _save(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        payload = {
            "version": 1,
            "lecciones": [leccion.to_json() for leccion in self._lecciones.values()],
        }
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            temp_path.chmod(0o600)
        except OSError:
            pass
        os.replace(temp_path, self.path)

    def _validar_workspace(self, workspace_id: Any) -> str | None:
        if workspace_id is None:
            return None
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise IDEAutocriticaError("workspace_id debe ser texto no vacío, o None.")
        if self.workspaces is None:
            return workspace_id
        try:
            self.workspaces.get(workspace_id)
        except IDEWorkspaceError as exc:
            raise IDEAutocriticaError(str(exc)) from exc
        return workspace_id

    def _buscar_por_firma(self, firma_normalizada: str) -> Leccion | None:
        for leccion in self._lecciones.values():
            if leccion.firma == firma_normalizada:
                return leccion
        return None

    def _desalojar_la_mas_debil_si_hace_falta(self) -> None:
        if len(self._lecciones) < self.max_lecciones:
            return
        # Se descarta primero la de menor confianza; en empate, la que menos
        # veces sirvió, y en empate la que hace más tiempo que no se usa. Así
        # el tope purga lo que menos rinde, no lo más nuevo -- misma idea que
        # ``ide_memoria._evict_weakest_if_needed``.
        mas_debil = min(
            self._lecciones.values(),
            key=lambda leccion: (
                _confianza(leccion.veces_util, leccion.veces_fallida),
                leccion.veces_servida,
                leccion.last_served_at or leccion.created_at,
            ),
        )
        del self._lecciones[mas_debil.id]

    # -- escritura -------------------------------------------------------- #

    def registrar_bucle(
        self,
        resultado: ResultadoBucle,
        *,
        arreglo: str | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        """Mira un bucle de verificación ya terminado y saca lo que aprendió.

        Hace dos cosas, en este orden y por esta razón:

        1. **Cobra las reincidencias.** Si una firma que YA tenía lección (y
           que ya se sirvió alguna vez, ver ``leccion_para``) se repitió
           idéntica en dos intentos seguidos, esa lección se aplicó y el
           error no se movió: se le anota una falla y, si llega al tope, se
           retira sola. Va PRIMERO para que una lección que acaba de fallar
           no se salve por lo que el mismo bucle termine logrando después.
        2. **Captura la lección nueva**, solo si el bucle APROBÓ tras más de
           un intento: la firma del PRIMER error + ``arreglo``. Del primero y
           no de los intermedios porque el primero es el que va a reaparecer
           (es el que produce el estado inicial del problema) y porque
           ``arreglo`` cuenta el camino completo hasta el verde, no un tramo.

        ``arreglo`` lo aporta quien integra, que es el único que sabe qué
        cambió el modelo entre intento e intento. Sin él NO se guarda nada y
        se devuelve ``motivo == "sin_arreglo"``: una lección que solo dice
        "esto se pudo arreglar" ocupa una firma sin ayudar a nadie.

        Devuelve siempre un diccionario con ``guardada``, ``motivo``,
        ``leccion`` y las listas ``degradadas``/``retiradas`` -- nunca lanza
        por "no había nada que aprender", que es el caso normal.
        """

        if not isinstance(resultado, ResultadoBucle):
            raise IDEAutocriticaError(
                "registrar_bucle espera un ResultadoBucle de ide_verificacion."
            )
        procedencia = self._validar_workspace(workspace_id)

        fallidos = [
            (item.intento, normalizar_firma(item.firma))
            for item in resultado.intentos
            if not item.aprobado and item.firma
        ]

        with self._lock:
            reincidentes = self._firmas_reincidentes(fallidos)
            degradadas, retiradas = self._penalizar(reincidentes)

            guardada = False
            fila: dict[str, Any] | None = None

            try:
                if not resultado.aprobado:
                    motivo = "no_aprobo"
                elif len(resultado.intentos) < 2 or not fallidos:
                    motivo = "aprobo_al_primer_intento"
                else:
                    firma = fallidos[0][1]
                    if not _firma_utilizable(firma):
                        motivo = "firma_sin_contenido"
                    elif firma in reincidentes:
                        # El mismo error se repitió idéntico durante este
                        # bucle: la lección que había no sirvió y algo más lo
                        # destrabó después. No es evidencia limpia de qué
                        # arregla ESTA firma, y guardarla ahora resucitaría con
                        # cara nueva a la lección que se acaba de castigar. Se
                        # espera un bucle limpio (falla -> arreglo -> verde)
                        # para volver a confiar.
                        motivo = "firma_reincidente_en_este_bucle"
                    elif arreglo is None:
                        motivo = "sin_arreglo"
                    else:
                        # El arreglo se valida acá dentro y no al entrar al
                        # método: es lo único que hace falta para GUARDAR, así
                        # que un arreglo con forma de ruido no puede cancelar
                        # el castigo de una lección que acaba de fallar en este
                        # mismo bucle -- que es justo cuando el integrador tiene
                        # más chance de mandar texto inservible (el modelo no
                        # arregló nada, no tiene qué contar). El ``finally``
                        # persiste las penalizaciones aunque esto lance.
                        fila = self._guardar(
                            firma,
                            _limpiar_arreglo(arreglo),
                            comando=tuple(resultado.intentos[0].argv),
                            procedencia=procedencia,
                        )
                        guardada = True
                        motivo = "leccion_reforzada" if fila["veces_util"] > 1 else "leccion_nueva"
            finally:
                if guardada or degradadas or retiradas:
                    self._save()

            return {
                "guardada": guardada,
                "motivo": motivo,
                "leccion": fila,
                "degradadas": degradadas,
                "retiradas": retiradas,
            }

    def _firmas_reincidentes(self, fallidos: list[tuple[int, str]]) -> set[str]:
        """Firmas que se repitieron idénticas en dos intentos CONSECUTIVOS.

        Es el mismo criterio con el que ``ide_verificacion`` declara que el
        agente está atascado: si entre un intento y el siguiente corrió un
        arreglo y el error no cambió un ápice, no hubo progreso. Aquí eso se
        usa como evidencia EN CONTRA de la lección que se había inyectado.
        """

        reincidentes: set[str] = set()
        for (intento_a, firma_a), (intento_b, firma_b) in zip(fallidos, fallidos[1:], strict=False):
            if firma_a == firma_b and intento_b == intento_a + 1:
                reincidentes.add(firma_a)
        return reincidentes

    def _penalizar(
        self, reincidentes: set[str]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        degradadas: list[dict[str, Any]] = []
        retiradas: list[dict[str, Any]] = []
        for firma in sorted(reincidentes):
            leccion = self._buscar_por_firma(firma)
            # ``veces_servida == 0`` significa que esta lección nunca llegó a
            # inyectarse: el error se repitió por su cuenta y castigarla sería
            # culparla de algo en lo que no participó.
            if leccion is None or leccion.veces_servida == 0:
                continue
            leccion.veces_fallida += 1
            leccion.updated_at = _now_iso()
            if leccion.veces_fallida >= MAX_FALLAS_ANTES_DE_RETIRAR:
                del self._lecciones[leccion.id]
                retiradas.append({"id": leccion.id, "firma": leccion.firma})
            else:
                degradadas.append(leccion.to_json())
        return degradadas, retiradas

    def _guardar(
        self,
        firma: str,
        arreglo: str,
        *,
        comando: tuple[str, ...],
        procedencia: str | None,
    ) -> dict[str, Any]:
        comando = _anonimizar_comando(comando)
        existente = self._buscar_por_firma(firma)
        if existente is not None:
            # El mismo error, resuelto otra vez: sube la evidencia a favor y
            # se queda con la redacción del arreglo que acaba de funcionar
            # (la vieja también funcionó, pero la nueva es la que se verificó
            # contra el estado actual del mundo).
            existente.veces_util += 1
            existente.arreglo = arreglo
            existente.comando = comando or existente.comando
            existente.updated_at = _now_iso()
            return existente.to_json()

        self._desalojar_la_mas_debil_si_hace_falta()
        ahora = _now_iso()
        leccion = Leccion(
            id=str(uuid.uuid4()),
            firma=firma,
            arreglo=arreglo,
            comando=comando,
            veces_util=1,
            veces_fallida=0,
            veces_servida=0,
            created_at=ahora,
            updated_at=ahora,
            last_served_at=None,
            workspace_id_origen=procedencia,
        )
        self._lecciones[leccion.id] = leccion
        return leccion.to_json()

    # -- lectura ------------------------------------------------------------

    def leccion_para(self, firma: Any, *, confianza_minima: float = 0.0) -> dict[str, Any] | None:
        """La lección de ESTA firma de error, si existe; si no, ``None``.

        Este es el punto de recuperación de todo el módulo: se llama cuando
        un intento ACABA de fallar, con la firma que trae
        ``ResultadoIntento.firma``, para inyectar lo aprendido antes de que
        el modelo vuelva a pensar el mismo problema desde cero.

        Servir una lección SÍ cuenta (sube ``veces_servida`` y refresca
        ``last_served_at``) -- lo contrario de ``ide_conocimiento.obtener``,
        donde leer no debía hacer parecer más fresco un hecho. Acá no se está
        midiendo frescura sino uso real, y ese registro es lo que después
        permite dos cosas: no castigar a una lección que nunca se inyectó
        (ver ``_penalizar``) y desalojar por lo que menos rinde (ver
        ``_desalojar_la_mas_debil_si_hace_falta``).
        """

        firma_normalizada = normalizar_firma(firma)
        if not firma_normalizada or not _firma_utilizable(firma_normalizada):
            return None
        with self._lock:
            leccion = self._buscar_por_firma(firma_normalizada)
            if leccion is None:
                return None
            if _confianza(leccion.veces_util, leccion.veces_fallida) < confianza_minima:
                return None
            leccion.veces_servida += 1
            leccion.last_served_at = _now_iso()
            self._save()
            return leccion.to_json()

    def bloque_para(self, firma: Any, *, confianza_minima: float = 0.0) -> str | None:
        """La lección de ``firma`` ya redactada para pegar en el prompt.

        Devuelve ``None`` cuando no hay nada que decir, para que quien integre
        haga ``if bloque: prompt += bloque`` sin lógica extra -- igual que
        ``ide_reglas.ProjectRules.as_prompt_block()``.

        El texto termina pidiendo explícitamente que no se fuerce la lección
        si no calza. Es la contrapartida de inyectarla justo cuando el modelo
        iba a razonar: sin ese permiso de descartarla, una lección parecida
        pero equivocada empuja hacia un arreglo que no era.
        """

        leccion = self.leccion_para(firma, confianza_minima=confianza_minima)
        if leccion is None:
            return None
        return (
            "[Ya viste este error antes]\n"
            f"La última vez se resolvió así: {leccion['arreglo']}\n"
            f"Evidencia: funcionó {leccion['veces_util']} vez/veces (fallas registradas: "
            f"{leccion['veces_fallida']}; confianza {leccion['confianza']}).\n"
            "Si esta vez el caso NO es el mismo, dilo y resuélvelo por tu cuenta: "
            "forzar una lección que no aplica es peor que no tenerla."
        )

    def listar(self) -> list[dict[str, Any]]:
        """Todas las lecciones vivas, de mayor a menor confianza -- para una
        vista de "qué aprendió Edecán a fuerza de errores" y para poder
        borrar a mano una que envejeció mal."""

        with self._lock:
            filas = [leccion.to_json() for leccion in self._lecciones.values()]
            return sorted(
                filas, key=lambda fila: (fila["confianza"], fila["updated_at"]), reverse=True
            )

    def olvidar(self, leccion_id: str) -> dict[str, Any]:
        """Borra una lección a pedido explícito (resultó estar mal, o el
        proyecto cambió y ya no aplica)."""

        with self._lock:
            leccion = self._lecciones.get(leccion_id)
            if leccion is None:
                raise IDEAutocriticaError("Lección no encontrada.")
            del self._lecciones[leccion_id]
            self._save()
            return {"deleted_leccion_id": leccion_id}
