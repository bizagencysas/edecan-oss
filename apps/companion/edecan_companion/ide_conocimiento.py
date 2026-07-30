"""Conocimiento verificado del agente del IDE: hechos técnicos ya comprobados
en internet, con fecha y fuente, para no volver a salir a buscarlos cada
turno.

Qué problema resuelve: al agente se le exige en su prompt que NUNCA asuma y
que compruebe en internet antes de escribir código que depende de algo
externo ("¿cuál es la versión estable de X?", "¿cómo se autentica hoy contra
Y?", "¿sigue vigente el parámetro Z?"). Bien -- pero hoy lo que comprueba se
pierde al cerrar el turno, así que mañana lo vuelve a buscar de cero, gasta
la misma llamada de red y el mismo tiempo otra vez. Este módulo es el
almacén de esos hechos ya comprobados.

Este módulo NO es ``ide_memoria.py`` (léase ese docstring primero -- este
archivo asume ese criterio como base y solo agrega lo que memoria
deliberadamente no cubre). La distinción que importa:

    MEMORIA       = una norma, decisión o hallazgo propio de ESTE repo
                    ("este equipo tutea", "el router X es un cuello de
                    botella"). No caduca por sí sola: sigue siendo cierta
                    mientras nadie decida lo contrario.
    CONOCIMIENTO  = un hecho del mundo EXTERIOR al repo, verificable en
                    internet, que puede quedar desactualizado con el tiempo
                    solo porque pasó el tiempo ("la versión estable de
                    Node.js es la 22.x", "el parámetro `foo` de esta API
                    quedó deprecado"). Por eso SIEMPRE lleva una fuente (URL)
                    y SIEMPRE caduca -- lo contrario de memoria.

Deliberadamente un módulo aparte (no una quinta ``MemoryKind``) por dos
razones que memoria no necesita resolver:

1. Caducidad por antigüedad real. Un hecho técnico envejece aunque nadie
   haga nada: la versión estable de hoy no es la de dentro de seis meses.
   Cada hecho guarda su fecha de verificación (``verified_at``) y, al
   recuperarlo, se calcula si sigue "vigente" contra un plazo (``TTL``)
   específico de su tipo -- ver ``TTL_DIAS_POR_TIPO`` más abajo para el
   plazo elegido y su justificación por tipo. Un hecho vencido NUNCA se
   borra en silencio ni se sirve como si fuera fresco: se entrega igual,
   pero con ``"vigente": False`` y la edad exacta, para que quien lo reciba
   decida si le alcanza tal cual o si conviene reverificarlo. Callar un
   hecho vencido sería peor que no tener nada -- el agente asumiría que
   nunca se comprobó, cuando en realidad hay una pista (aunque vieja) de
   por dónde seguir.
2. Fuente obligatoria. Un hecho sin URL no es "conocimiento verificado", es
   exactamente lo que el prompt del agente le prohíbe: recordar sin haber
   comprobado. ``verificar()`` rechaza de plano cualquier intento de
   guardar sin una fuente que tenga forma de URL http(s) real -- no un
   texto libre tipo "lo vi en un tutorial" o "me lo dijeron".

Qué SÍ entra: hechos comprobables del mundo exterior, del tipo "la versión
estable/LTS de X es N.N", "la forma recomendada hoy de autenticar contra Y es
Z", "el parámetro/endpoint W quedó deprecado (y por qué se reemplaza)", "el
límite/cuota gratuito de un servicio es N". Qué NUNCA entra, aunque alguien
lo intente: opiniones, preferencias de estilo, decisiones internas del
equipo (eso es ``ide_memoria.py``, tipo ``"decision"`` o ``"convencion"``),
o cualquier afirmación sin URL de respaldo -- ``verificar()`` la rechaza.

Recuperación: ``obtener()`` busca un hecho puntual por su tema exacto (p. ej.
"versión estable de Node.js") y devuelve ``None`` si nunca se verificó --
esa ausencia es la señal correcta para que el agente salga a comprobarlo,
nunca se rellena con una suposición. ``buscar()`` hace lo mismo que
``recall()`` de memoria pero sobre conocimiento: coincidencia léxica simple
contra el tema y el contenido, sin volcar todo el almacén (mismo espíritu
documentado en ``ide_memoria.py``). Ninguna de las dos funciones de lectura
toca ``verified_at`` ni cuenta como reverificación -- a propósito: leer un
hecho no lo hace más fresco, solo salir a comprobarlo de nuevo (llamando otra
vez a ``verificar()``) lo hace. Eso es lo que ``veces_reverificado`` cuenta.

Igual que ``ide_memoria.py``, este archivo deliberadamente NO importa nada
de ``ide_sessions.py``, ``ide_workers_agent.py`` ni de ``routers/ide.py``
(cuellos de botella de esta tanda, en desarrollo en paralelo). La única
entrada externa es un ``workspace_id`` ya autorizado en ``WorkspaceStore``,
igual que en memoria. Integración prevista (no se implementa desde este
archivo): antes de que el agente confíe en un hecho externo para escribir
código, llamaría a ``ConocimientoStore.obtener(workspace_id, kind, tema)``;
si devuelve ``None`` o ``"vigente": False``, el prompt del agente (que ya le
exige comprobar en internet) tiene la señal de que debe buscarlo de nuevo, y
al terminar llamaría a ``verificar()`` una vez con la fuente encontrada --
la misma llamada sirve tanto para guardar un hecho nuevo como para
reverificar uno vencido, ver el docstring de ``verificar()``.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from edecan_companion.ide_workspaces import IDEWorkspaceError, WorkspaceStore

ConocimientoKind = Literal[
    "version_vigente",
    "practica_recomendada",
    "cambio_deprecado",
    "limite_o_cuota",
]

CONOCIMIENTO_KINDS: tuple[ConocimientoKind, ...] = (
    "version_vigente",
    "practica_recomendada",
    "cambio_deprecado",
    "limite_o_cuota",
)

# -- Caducidad por tipo de hecho, en días -------------------------------- #
# Cada tipo envejece a un ritmo distinto en el mundo real; un solo TTL para
# todo el módulo trataría igual a "la versión estable de una librería" (que
# puede cambiar en semanas) y a "este servicio quedó deprecado" (que rara
# vez se revierte). Justificación de cada plazo:
#
# - "version_vigente" (30 días): números de versión son lo más volátil de
#   este almacén -- librerías y runtimes activos sacan releases estables
#   nuevas cada pocas semanas. Un mes es suficiente para que valga la pena
#   reconfirmar antes de fijar una versión en un `package.json` o similar.
# - "limite_o_cuota" (45 días): límites, cuotas y precios de servicios de
#   terceros cambian con anuncios de producto, más espaciados que releases
#   de versión pero no tan estables como una recomendación de arquitectura.
# - "practica_recomendada" (120 días): "la forma recomendada de autenticar
#   contra Y" cambia con ciclos de deprecación de APIs, que suelen avisarse
#   con meses de anticipación -- cuatro meses es razonable antes de asumir
#   que la guía pudo haber cambiado.
# - "cambio_deprecado" (180 días): que algo YA quedó deprecado rara vez se
#   revierte (no vuelve a estar recomendado), pero sí conviene reconfirmar
#   cada tanto si ya fue removido del todo (deprecado -> eliminado). Medio
#   año balancea eso sin pedir reverificar un hecho que probablemente sigue
#   describiendo la realidad tal cual.
TTL_DIAS_POR_TIPO: dict[str, int] = {
    "version_vigente": 30,
    "limite_o_cuota": 45,
    "practica_recomendada": 120,
    "cambio_deprecado": 180,
}

# -- Topes de tamaño ------------------------------------------------------ #
# Mismo espíritu que ``ide_memoria.MIN/MAX_CONTENT_CHARS``: un hecho es una
# afirmación compacta y verificable, no una frase suelta ni un ensayo con
# todo el contexto de cómo se comprobó.
MIN_TEMA_CHARS = 4
MAX_TEMA_CHARS = 160
MIN_CONTENT_CHARS = 12
MAX_CONTENT_CHARS = 400
MAX_FUENTE_CHARS = 500

# Cuántos hechos como máximo devuelve ``buscar()`` por defecto.
DEFAULT_BUSCAR_K = 6

_ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_WHITESPACE_RE = re.compile(r"\s+")
_TRAILING_PUNCT_RE = re.compile(r"[\s.!?¡¿]+$")
_TOKEN_RE = re.compile(r"[a-záéíóúñü0-9_]+")
_STOPWORDS = frozenset(
    {
        "el", "la", "los", "las", "de", "del", "en", "un", "una", "unos", "unas",
        "y", "o", "u", "que", "es", "son", "se", "por", "para", "con", "sin",
        "no", "sí", "lo", "al", "su", "sus", "este", "esta", "esto", "estos",
        "estas", "más", "pero", "como", "ya", "muy", "fue", "ser", "hay",
        "the", "and", "for", "with",
    }
)


class IDEConocimientoError(ValueError):
    """Solicitud de conocimiento verificado inválida: workspace inexistente,
    tipo de hecho fuera de ``CONOCIMIENTO_KINDS``, contenido con forma de
    ruido, fuente ausente o sin forma de URL, o hecho no encontrado."""


def _now_iso() -> str:
    return time.strftime(_ISO_FORMAT, time.gmtime())


def _parse_iso(raw: str) -> datetime:
    try:
        return datetime.strptime(raw, _ISO_FORMAT).replace(tzinfo=UTC)
    except (ValueError, TypeError) as exc:
        raise IDEConocimientoError(
            f"Fecha inválida, se espera formato ISO {_ISO_FORMAT!r}: {raw!r}."
        ) from exc


def _edad_dias(verified_at_iso: str) -> float:
    entonces = _parse_iso(verified_at_iso)
    ahora = datetime.now(UTC)
    return max(0.0, (ahora - entonces).total_seconds() / 86400.0)


def _clean_text(raw: Any, minimo: int, maximo: int, campo: str) -> str:
    if not isinstance(raw, str):
        raise IDEConocimientoError(f"El campo {campo!r} debe ser texto.")
    text = _WHITESPACE_RE.sub(" ", raw).strip()
    if len(text) < minimo:
        raise IDEConocimientoError(
            f"El campo {campo!r} es demasiado corto (mínimo {minimo} caracteres)."
        )
    if len(text) > maximo:
        raise IDEConocimientoError(
            f"El campo {campo!r} es demasiado largo (máximo {maximo} caracteres) -- "
            "un hecho verificado es una afirmación compacta, no un párrafo de contexto."
        )
    return text


def _validar_fuente(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise IDEConocimientoError(
            "Todo hecho verificado necesita una fuente (URL) -- sin fuente esto es "
            "memoria (o una suposición), no conocimiento comprobado. Ver ide_memoria.py "
            "si lo que se quiere guardar es una convención o decisión propia del repo."
        )
    fuente = raw.strip()
    if len(fuente) > MAX_FUENTE_CHARS:
        raise IDEConocimientoError(f"La fuente es demasiado larga (máximo {MAX_FUENTE_CHARS}).")
    parsed = urlparse(fuente)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise IDEConocimientoError(
            "La fuente debe ser una URL http(s) real (p. ej. 'https://...'), no una "
            "referencia genérica como 'lo vi en un tutorial' o 'me lo dijeron'."
        )
    return fuente


def _tema_key(kind: str, tema: str) -> str:
    """Clave de identidad de un hecho: mismo ``kind`` + mismo tema (tras
    normalizar mayúsculas/espacios/puntuación final) es EL MISMO hecho, así
    haya cambiado su contenido -- justo el caso de reverificar una versión
    que subió de N.N a N.N+1 (ver ``verificar``). El ``kind`` forma parte de
    la clave a propósito: "Node.js" como ``version_vigente`` y "Node.js"
    como ``practica_recomendada`` son hechos distintos, no el mismo."""

    return f"{kind}::{_TRAILING_PUNCT_RE.sub('', tema.casefold()).strip()}"


def _tokenize(text: str) -> set[str]:
    return {
        token
        for token in _TOKEN_RE.findall(text.casefold())
        if len(token) >= 3 and token not in _STOPWORDS
    }


@dataclass
class HechoVerificado:
    """Un hecho técnico comprobado, con su fuente y fecha de verificación."""

    id: str
    workspace_id: str
    kind: ConocimientoKind
    tema: str
    contenido: str
    fuente_url: str
    verified_at: str
    created_at: str
    veces_reverificado: int

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "kind": self.kind,
            "tema": self.tema,
            "contenido": self.contenido,
            "fuente_url": self.fuente_url,
            "verified_at": self.verified_at,
            "created_at": self.created_at,
            "veces_reverificado": self.veces_reverificado,
        }

    @staticmethod
    def from_json(raw: dict[str, Any]) -> HechoVerificado:
        kind = raw.get("kind")
        if kind not in CONOCIMIENTO_KINDS:
            # A diferencia de ``ide_memoria.MemoryNote.from_json`` (que
            # degrada una fila corrupta a un tipo por defecto), acá NO se
            # degrada: reinterpretar el tipo cambiaría en silencio el TTL
            # aplicado y podría mostrar como "vigente" un hecho que en
            # realidad ya venció bajo su tipo real. Más seguro descartar la
            # fila (ver ``_load``, que ya atrapa este ``ValueError``) que
            # servir un hecho con la caducidad equivocada.
            raise ValueError(f"kind desconocido en fila de conocimiento: {kind!r}")
        return HechoVerificado(
            id=str(raw["id"]),
            workspace_id=str(raw["workspace_id"]),
            kind=kind,
            tema=str(raw.get("tema") or ""),
            contenido=str(raw.get("contenido") or ""),
            fuente_url=str(raw.get("fuente_url") or ""),
            verified_at=str(raw.get("verified_at") or _now_iso()),
            created_at=str(raw.get("created_at") or raw.get("verified_at") or _now_iso()),
            veces_reverificado=int(raw.get("veces_reverificado") or 0),
        )


class ConocimientoStore:
    """Registro JSON local, atómico y privado de conocimiento verificado por
    workspace. Mismo patrón de persistencia que ``MemoriaStore``
    (``ide_memoria.py``) y ``ProjectRegistry``: un archivo, un lock,
    escritura atómica con ``os.replace`` y permisos ``0o600``."""

    def __init__(
        self,
        state_dir: Path,
        workspaces: WorkspaceStore,
        *,
        ttl_dias_por_tipo: dict[str, int] | None = None,
    ) -> None:
        self.workspaces = workspaces
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / "ide-conocimiento.json"
        self.ttl_dias_por_tipo = dict(ttl_dias_por_tipo or TTL_DIAS_POR_TIPO)
        self._lock = threading.RLock()
        self._hechos: dict[str, HechoVerificado] = {}
        self._load()

    # -- persistencia --------------------------------------------------- #

    def _load(self) -> None:
        with self._lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
            rows = data.get("hechos", []) if isinstance(data, dict) else []
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                    continue
                try:
                    hecho = HechoVerificado.from_json(row)
                except (KeyError, ValueError, TypeError):
                    continue
                self._hechos[hecho.id] = hecho

    def _save(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        payload = {"version": 1, "hechos": [hecho.to_json() for hecho in self._hechos.values()]}
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            temp_path.chmod(0o600)
        except OSError:
            pass
        os.replace(temp_path, self.path)

    def _validate_workspace(self, workspace_id: Any) -> str:
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise IDEConocimientoError("workspace_id debe ser texto no vacío.")
        try:
            self.workspaces.get(workspace_id)
        except IDEWorkspaceError as exc:
            raise IDEConocimientoError(str(exc)) from exc
        return workspace_id

    def _hechos_de(self, workspace_id: str) -> list[HechoVerificado]:
        """TODOS los hechos verificados, vengan del proyecto que vengan.

        Este almacén es GLOBAL a propósito, y es la diferencia de fondo con `ide_memoria.py`:
        allí se guarda lo que es cierto de UN repo ("aquí los tests necesitan Postgres real"),
        y aquí lo que es cierto del MUNDO ("la versión estable de esa librería es N.N.").

        Un hecho del mundo no cambia al abrir otro proyecto. Filtrarlo por workspace obligaba
        a reverificar en cada repo lo mismo que ya se había comprobado ayer en el de al lado,
        que es justo el trabajo que este módulo existe para evitar.

        Compartirlo es seguro por CÓMO se guarda, no por confianza: `verificar` exige una URL
        de fuente real (`_validar_fuente` rechaza sin ella) y cada hecho caduca según su tipo,
        entregándose marcado como vencido en vez de servirse como fresco. Eso son hechos
        comprobables con su evidencia y su fecha, no opiniones acumuladas -- por eso pueden
        cruzar de proyecto sin contaminar nada.

        `workspace_id` se conserva en cada hecho como PROCEDENCIA (dónde se comprobó), útil
        para auditar de dónde salió algo, pero ya no restringe quién lo ve.
        """
        del workspace_id
        return list(self._hechos.values())

    def _ttl_dias(self, kind: str) -> int:
        return self.ttl_dias_por_tipo.get(kind, TTL_DIAS_POR_TIPO[kind])

    def _con_frescura(self, hecho: HechoVerificado) -> dict[str, Any]:
        """Adjunta al hecho la marca de vigencia -- ver el docstring del
        módulo: un hecho vencido se entrega igual, nunca se calla, pero
        siempre marcado. ``ttl_dias`` viaja en la respuesta para que quien
        lo consuma entienda por qué se considera vigente o no, sin tener que
        conocer ``TTL_DIAS_POR_TIPO`` de memoria."""

        edad = _edad_dias(hecho.verified_at)
        ttl = self._ttl_dias(hecho.kind)
        row = hecho.to_json()
        row["edad_dias"] = round(edad, 2)
        row["ttl_dias"] = ttl
        row["vigente"] = edad <= ttl
        return row

    # -- escritura -------------------------------------------------------- #

    def verificar(
        self,
        workspace_id: str,
        kind: ConocimientoKind,
        tema: str,
        contenido: str,
        fuente_url: str,
        *,
        verified_at: str | None = None,
    ) -> dict[str, Any]:
        """Guarda un hecho técnico comprobado, o reverifica uno existente.

        ``tema`` identifica DE QUÉ es el hecho (p. ej. "versión estable de
        Node.js"); ``contenido`` es la afirmación en sí (p. ej. "la versión
        estable es la 22.x"). Esta separación es lo que permite reverificar:
        si ya existe un hecho con el mismo ``kind`` + mismo ``tema`` (ver
        ``_tema_key``), esta llamada NO crea una fila nueva -- actualiza
        ``contenido``, ``fuente_url`` y ``verified_at`` sobre la fila
        existente y sube ``veces_reverificado``. Eso es exactamente lo que
        pasa cuando una versión sube de N.N a N.N+1: el hecho es "el mismo"
        (misma pregunta, "¿cuál es la versión estable de Node.js?"), solo
        cambió la respuesta y hay que refrescar su fecha de verificación.

        Rechaza sin guardar nada si falta la fuente (``_validar_fuente``),
        si el contenido tiene forma de ruido (``_clean_text``), o si
        ``kind`` no es uno de ``CONOCIMIENTO_KINDS``.
        """

        workspace_id = self._validate_workspace(workspace_id)
        if kind not in CONOCIMIENTO_KINDS:
            raise IDEConocimientoError(
                f"kind debe ser uno de {CONOCIMIENTO_KINDS}, no {kind!r}."
            )
        tema_limpio = _clean_text(tema, MIN_TEMA_CHARS, MAX_TEMA_CHARS, "tema")
        contenido_limpio = _clean_text(contenido, MIN_CONTENT_CHARS, MAX_CONTENT_CHARS, "contenido")
        fuente_limpia = _validar_fuente(fuente_url)
        verified_iso = _parse_iso(verified_at).strftime(_ISO_FORMAT) if verified_at else _now_iso()

        with self._lock:
            key = _tema_key(kind, tema_limpio)
            for existing in self._hechos_de(workspace_id):
                if _tema_key(existing.kind, existing.tema) == key:
                    existing.contenido = contenido_limpio
                    existing.fuente_url = fuente_limpia
                    existing.verified_at = verified_iso
                    existing.veces_reverificado += 1
                    self._save()
                    return self._con_frescura(existing)

            hecho = HechoVerificado(
                id=str(uuid.uuid4()),
                workspace_id=workspace_id,
                kind=kind,
                tema=tema_limpio,
                contenido=contenido_limpio,
                fuente_url=fuente_limpia,
                verified_at=verified_iso,
                created_at=verified_iso,
                veces_reverificado=0,
            )
            self._hechos[hecho.id] = hecho
            self._save()
            return self._con_frescura(hecho)

    # -- lectura ------------------------------------------------------------

    def obtener(
        self, workspace_id: str, kind: ConocimientoKind, tema: str
    ) -> dict[str, Any] | None:
        """Busca el hecho exacto (mismo ``kind`` + mismo ``tema``, ver
        ``_tema_key``). Devuelve ``None`` si nunca se verificó -- esa
        ausencia ES la señal correcta para el agente: no lo asuma, salga a
        comprobarlo. No cuenta como lectura de "recuerdo usado": a
        diferencia de ``MemoriaStore.recall``, aquí leer NUNCA toca
        ``verified_at`` ni ``veces_reverificado`` -- ver el docstring del
        módulo sobre por qué leer un hecho no debe hacerlo parecer más
        fresco de lo que es."""

        workspace_id = self._validate_workspace(workspace_id)
        if kind not in CONOCIMIENTO_KINDS:
            raise IDEConocimientoError(f"kind debe ser uno de {CONOCIMIENTO_KINDS}, no {kind!r}.")
        if not isinstance(tema, str) or not tema.strip():
            raise IDEConocimientoError("tema debe ser texto no vacío.")
        key = _tema_key(kind, tema.strip())
        with self._lock:
            for hecho in self._hechos_de(workspace_id):
                if _tema_key(hecho.kind, hecho.tema) == key:
                    return self._con_frescura(hecho)
            return None

    def buscar(
        self, workspace_id: str, query: str, *, k: int = DEFAULT_BUSCAR_K
    ) -> list[dict[str, Any]]:
        """Los ``k`` hechos más relevantes para ``query`` en este workspace.

        Mismo método que ``MemoriaStore.recall`` (coincidencia léxica sobre
        tema + contenido, no embeddings; ver esa docstring para la
        limitación documentada) pero sin volcar todo el almacén ni mutar
        nada al leer -- ver ``obtener`` sobre por qué. Un hecho sin ninguna
        palabra en común con ``query`` no se devuelve."""

        workspace_id = self._validate_workspace(workspace_id)
        if not isinstance(query, str):
            raise IDEConocimientoError("query debe ser texto.")
        if k <= 0:
            raise IDEConocimientoError("k debe ser mayor que cero.")

        query_tokens = _tokenize(query)
        with self._lock:
            candidatos = []
            for hecho in self._hechos_de(workspace_id):
                hecho_tokens = _tokenize(f"{hecho.tema} {hecho.contenido}")
                if not query_tokens or not hecho_tokens:
                    continue
                overlap = len(query_tokens & hecho_tokens)
                if overlap == 0:
                    continue
                score = overlap / len(query_tokens)
                candidatos.append((score, hecho))
            candidatos.sort(key=lambda par: par[0], reverse=True)
            resultados = []
            for score, hecho in candidatos[:k]:
                row = self._con_frescura(hecho)
                row["score"] = round(score, 4)
                resultados.append(row)
            return resultados

    def listar(self, workspace_id: str) -> list[dict[str, Any]]:
        """Todo el conocimiento verificado de este workspace, sin filtrar
        por vigencia -- para una vista de "qué tiene comprobado Edecán de
        este proyecto" en la UI, igual que ``MemoriaStore.list_notes``."""

        workspace_id = self._validate_workspace(workspace_id)
        with self._lock:
            rows = [self._con_frescura(hecho) for hecho in self._hechos_de(workspace_id)]
            return sorted(rows, key=lambda row: row["verified_at"], reverse=True)

    def olvidar(self, workspace_id: str, hecho_id: str) -> dict[str, Any]:
        """Borra un hecho puntual a pedido explícito (p. ej. resultó ser
        incorrecto, o ya no aplica)."""

        # Se valida que quien pide sea un workspace real, pero NO se exige que el hecho haya
        # nacido ahí: como el almacén es global (ver `_hechos_de`), un hecho equivocado se ve
        # desde todos los proyectos y tiene que poder corregirse desde cualquiera. Obligar a
        # volver al repo donde se comprobó dejaría un dato erróneo circulando por el resto.
        self._validate_workspace(workspace_id)
        with self._lock:
            hecho = self._hechos.get(hecho_id)
            if hecho is None:
                raise IDEConocimientoError("Hecho no encontrado.")
            del self._hechos[hecho_id]
            self._save()
            return {"deleted_hecho_id": hecho_id}
