"""Sesión y trabajo de fondo del IDE: ``/background``, ``/tasks``, ``/export``,
``/copy`` y ``/permissions``.

Autocontenido a propósito, igual que ``ide_equipo.py`` (ver su docstring):
NO importa ``ide_sessions`` ni ``ide_workers_agent``. Este módulo trata a
``SessionManager`` como un objeto con la forma pública que ya expone hoy
(``list(kind, workspace_id=None) -> {"sessions": [...]}``, cada fila con las
claves de ``Session.public()``) sin acoplarse a su clase concreta. Quien
integre esto en ``ide_sessions.py``/``ide_workers_agent.py``/el router
``ide.py`` decide el cableado real; aquí solo vive la parte que de verdad
hay que diseñar con cuidado en cada uno de los cinco comandos:

- **/background + /tasks**: el companion YA corre agentes y terminales en
  hilos que sobreviven a que se cierre la ventana (ver el docstring de
  ``ide_sessions.py``: "minimizar/cerrar la app móvil solo deja de leer
  eventos; el proceso continúa"). Lo que falta es la señal explícita
  "esto lo mandé a segundo plano a propósito" (``SegundoPlanoStore``) y una
  vista unificada de qué hay corriendo en TODOS los workspaces
  (``listar_tareas``) — hoy ``SessionManager.list`` obliga a elegir un
  ``kind`` y no junta agente+terminal en una sola lista con tiempo
  transcurrido.
- **/export**: un export que solo tiene la charla no sirve para retomar el
  trabajo — por eso ``exportar_markdown`` junta la conversación CON los
  comandos ejecutados y archivos tocados (eventos ``command``/``file`` que
  ``ide_workers_agent._execute``/``_run_command`` ya emite).
- **/copy**: la última respuesta del agente, aplanada a texto plano. Escribir
  en el portapapeles del sistema es del cliente (web/iOS); aquí solo se
  prepara el texto.
- **/permissions**: qué puede hacer el agente del IDE SIN preguntar. El
  precedente real más cercano en este mismo paquete es
  ``approval.py``/``CompanionConfig.auto_approve`` — una lista blanca
  explícita, vacía por defecto, con un subconjunto que NUNCA se salta la
  pregunta (``_ALWAYS_PROMPT_ACTIONS``) — no el ``Tool.dangerous`` de
  ``packages/core`` (ese es del servidor/API, un proceso distinto). Este
  módulo aplica el mismo principio a las tres categorías que sí pueden
  afectar algo fuera del turno: ejecutar comandos, escribir archivos y
  salir a la red. Por defecto NINGUNA corre sin preguntar; aflojar una es
  una llamada explícita y auditada, nunca el estado de fábrica. Las
  herramientas de solo lectura (``listar_archivos``/``leer_archivo``/
  ``buscar_en_archivos``) quedan deliberadamente FUERA del gate: exigirles
  confirmación también volvería inservible al IDE.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

MAX_HISTORIAL_PERMISOS = 200


class IDESesionExtrasError(ValueError):
    """Entrada inválida o estado inconsistente en estas extras del IDE."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Mismo patrón que ``ide_sessions.SessionManager._save``: archivo
    temporal + ``os.replace`` (nunca deja un JSON a medio escribir) y
    permisos ``0600`` (este estado puede incluir rutas/títulos de trabajo del
    usuario, igual que ``ide-sessions.json``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        temp.chmod(0o600)
    except OSError:
        pass
    os.replace(temp, path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


# ---------------------------------------------------------------------------
# /background + /tasks
# ---------------------------------------------------------------------------

_ESTADOS_ACTIVOS = frozenset({"starting", "running"})
_KINDS_CONOCIDOS = frozenset({"agent", "terminal"})


@dataclass(frozen=True, slots=True)
class Tarea:
    """Una fila de ``/tasks``: agente o terminal, de cualquier workspace."""

    id: str
    kind: str
    workspace_id: str
    workspace_name: str
    title: str
    status: str
    started_at: str | None
    ended_at: str | None
    en_segundo_plano: bool
    elapsed_s: float | None


class SegundoPlanoStore:
    """Persiste, por sesión, si el dueño la mandó explícitamente a seguir en
    segundo plano.

    El hilo de la sesión ya corre solo (ver docstring de módulo); lo único
    que faltaba era ESTA señal, para que ``/tasks`` pueda distinguir "el
    dueño sabe que esto sigue solo" de "simplemente no cerró la ventana
    todavía". Vive en su propio JSON (``ide-session-extras.json``) para no
    tocar ``ide-sessions.json`` ni la clase que lo escribe.
    """

    def __init__(self, state_dir: Path | str) -> None:
        self._path = Path(state_dir) / "ide-session-extras.json"
        self._data: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        payload = _read_json(self._path)
        crudo = payload.get("segundo_plano")
        if isinstance(crudo, dict):
            self._data = {
                str(session_id): row for session_id, row in crudo.items() if isinstance(row, dict)
            }

    def _save(self) -> None:
        _atomic_write_json(self._path, {"version": 1, "segundo_plano": self._data})

    def marcar(self, session_id: str) -> None:
        """Anota que ``session_id`` sigue trabajando sin ventana abierta."""
        self._data[session_id] = {"en_segundo_plano": True, "marcado_en": _now()}
        self._save()

    def desmarcar(self, session_id: str) -> None:
        """Vuelve a poner ``session_id`` en primer plano (o no-op si no estaba)."""
        if session_id in self._data:
            self._data.pop(session_id, None)
            self._save()

    def esta_en_segundo_plano(self, session_id: str) -> bool:
        row = self._data.get(session_id)
        return bool(row and row.get("en_segundo_plano"))


def _filas_de(manager: Any, kind: str) -> list[dict[str, Any]]:
    payload = manager.list(kind)
    filas = payload.get("sessions") if isinstance(payload, dict) else None
    return [row for row in (filas or []) if isinstance(row, dict)]


def _buscar_sesion(manager: Any, kind: str, session_id: str) -> dict[str, Any] | None:
    for row in _filas_de(manager, kind):
        if str(row.get("id")) == session_id:
            return row
    return None


def _elapsed_seconds(fila: dict[str, Any]) -> float | None:
    inicio = _parse_iso(fila.get("started_at"))
    if inicio is None:
        return None
    fin = _parse_iso(fila.get("ended_at")) or datetime.now(UTC)
    return max(0.0, (fin - inicio).total_seconds())


def _a_tarea(fila: dict[str, Any], *, en_segundo_plano: bool) -> Tarea:
    return Tarea(
        id=str(fila.get("id") or ""),
        kind=str(fila.get("kind") or ""),
        workspace_id=str(fila.get("workspace_id") or ""),
        workspace_name=str(fila.get("workspace_name") or ""),
        title=str(fila.get("title") or ""),
        status=str(fila.get("status") or ""),
        started_at=fila.get("started_at"),
        ended_at=fila.get("ended_at"),
        en_segundo_plano=en_segundo_plano,
        elapsed_s=_elapsed_seconds(fila),
    )


def enviar_a_segundo_plano(
    manager: Any, store: SegundoPlanoStore, session_id: str, kind: str
) -> Tarea:
    """``/background``: confirma que la sesión sigue en curso y la marca.

    Solo tiene sentido mandar a segundo plano algo que sigue corriendo — una
    sesión ya terminada no necesita "seguir sola", así que eso es un error
    de uso (``IDESesionExtrasError``), no un no-op silencioso.
    """
    if kind not in _KINDS_CONOCIDOS:
        raise IDESesionExtrasError(f"Tipo de sesión desconocido: {kind!r}.")
    fila = _buscar_sesion(manager, kind, session_id)
    if fila is None:
        raise IDESesionExtrasError("Sesión no encontrada.")
    if fila.get("status") not in _ESTADOS_ACTIVOS:
        raise IDESesionExtrasError(
            "Solo se puede mandar a segundo plano una sesión que sigue en curso."
        )
    store.marcar(session_id)
    return _a_tarea(fila, en_segundo_plano=True)


def traer_a_primer_plano(
    manager: Any, store: SegundoPlanoStore, session_id: str, kind: str
) -> Tarea:
    """Inverso de ``enviar_a_segundo_plano``: la sesión no tiene que haber
    seguido corriendo (puede haberse enviado a segundo plano y ya haber
    terminado sola) — por eso, a diferencia de ``enviar_a_segundo_plano``,
    aquí no se exige un estado activo."""
    if kind not in _KINDS_CONOCIDOS:
        raise IDESesionExtrasError(f"Tipo de sesión desconocido: {kind!r}.")
    fila = _buscar_sesion(manager, kind, session_id)
    if fila is None:
        raise IDESesionExtrasError("Sesión no encontrada.")
    store.desmarcar(session_id)
    return _a_tarea(fila, en_segundo_plano=False)


def listar_tareas(
    manager: Any,
    store: SegundoPlanoStore,
    *,
    solo_activas: bool = True,
) -> list[Tarea]:
    """``/tasks``: agentes + terminales de TODOS los workspaces en una sola
    lista, con tiempo transcurrido y si el dueño la mandó a segundo plano.

    Activas primero (``solo_activas=False`` para ver también lo que ya
    terminó); dentro de cada grupo, más reciente primero — mismo criterio
    de orden que ``SessionManager.list`` ya usa para una sola sesión.
    """
    tareas = [
        _a_tarea(fila, en_segundo_plano=store.esta_en_segundo_plano(str(fila.get("id") or "")))
        for kind in sorted(_KINDS_CONOCIDOS)
        for fila in _filas_de(manager, kind)
        if not solo_activas or fila.get("status") in _ESTADOS_ACTIVOS
    ]
    tareas.sort(key=lambda tarea: tarea.started_at or "", reverse=True)
    tareas.sort(key=lambda tarea: tarea.status not in _ESTADOS_ACTIVOS)
    return tareas


# ---------------------------------------------------------------------------
# /export
# ---------------------------------------------------------------------------

# "blocks" (tablas y gráficas, ver ``ide_bloques.py``) cuenta como parte de la
# conversación: es lo que la persona vio, y su texto de respaldo ya viene con
# forma de tabla de Markdown -- que es justo el formato de este export.
_TIPOS_CONVERSACION = frozenset({"user", "assistant", "assistant_final", "blocks"})


def exportar_markdown(sesion: dict[str, Any], eventos: list[dict[str, Any]]) -> str:
    """``/export``: Markdown legible con lo que se habló Y lo que se hizo.

    ``sesion`` es un ``Session.public()`` (o la fila equivalente de
    ``SessionManager.list``). ``eventos`` debe venir YA completo — todas las
    páginas de ``SessionManager.read`` concatenadas por quien llame — este
    módulo no pagina para no acoplarse a esa clase (ver docstring de
    arriba); ``MAX_EVENTS_PER_READ`` de ``ide_sessions.py`` obliga a pedir
    varias páginas para una sesión larga, y ese bucle es responsabilidad de
    quien integre esto.

    Un export sin las acciones no sirve para retomar el trabajo: junto a la
    conversación se arma un resumen de comandos ejecutados y archivos
    tocados (eventos ``command``/``file``), en el orden en que ocurrieron y
    sin duplicados.
    """
    titulo = str(sesion.get("title") or "Sesión de Edecán")
    lineas: list[str] = [f"# {titulo}", ""]
    workspace = sesion.get("workspace_name") or sesion.get("workspace_id") or "—"
    lineas.append(f"- Workspace: {workspace}")
    lineas.append(f"- Tipo: {sesion.get('kind') or '—'}")
    lineas.append(f"- Estado: {sesion.get('status') or '—'}")
    lineas.append(f"- Iniciada: {sesion.get('started_at') or '—'}")
    if sesion.get("ended_at"):
        lineas.append(f"- Finalizada: {sesion.get('ended_at')}")
    lineas.append("")

    lineas.append("## Conversación")
    lineas.append("")
    hubo_conversacion = False
    for evento in eventos:
        tipo = str(evento.get("type") or "")
        texto = str(evento.get("text") or "").strip()
        if tipo not in _TIPOS_CONVERSACION or not texto:
            continue
        hubo_conversacion = True
        etiqueta = "Tú" if tipo == "user" else "Edecán"
        if tipo == "blocks":
            # El texto de un bloque son varias líneas con forma de tabla de
            # Markdown; pegarle el prefijo a la primera rompería la fila de
            # encabezado y el export mostraría la tabla como texto suelto.
            lineas.append(f"**{etiqueta}:**")
            lineas.append("")
            lineas.append(texto)
        else:
            lineas.append(f"**{etiqueta}:** {texto}")
        lineas.append("")
    if not hubo_conversacion:
        lineas.append("_Sin mensajes registrados._")
        lineas.append("")

    lineas.append("## Qué se hizo")
    lineas.append("")
    comandos: list[str] = []
    archivos: list[str] = []
    for evento in eventos:
        tipo = str(evento.get("type") or "")
        texto = str(evento.get("text") or "").strip()
        if not texto:
            continue
        if tipo == "command" and texto not in comandos:
            comandos.append(texto)
        elif tipo == "file" and texto not in archivos:
            archivos.append(texto)
    if comandos:
        lineas.append("**Comandos ejecutados:**")
        lineas.extend(f"- `{comando}`" for comando in comandos)
        lineas.append("")
    if archivos:
        lineas.append("**Archivos tocados:**")
        lineas.extend(f"- `{archivo}`" for archivo in archivos)
        lineas.append("")
    if not comandos and not archivos:
        lineas.append("_No se registraron comandos ni archivos tocados._")
        lineas.append("")

    return "\n".join(lineas).rstrip() + "\n"


# ---------------------------------------------------------------------------
# /copy
# ---------------------------------------------------------------------------

_MD_BLOQUE_CODIGO = re.compile(r"```[a-zA-Z0-9_+-]*\n?")
_MD_ENCABEZADO = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_NEGRITA = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_MD_CURSIVA = re.compile(r"(?<!\*)\*(?!\*)([^*\n]+?)\*(?!\*)")
_MD_CODIGO_INLINE = re.compile(r"`([^`]+)`")
_MD_ENLACE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def texto_plano_desde_markdown(texto_markdown: str) -> str:
    """Quita las marcas de formato más comunes que el agente genera, sin
    intentar ser un parser de Markdown completo — alcanza con lo que el
    propio agente produce (encabezados, negrita/cursiva, código, enlaces)."""
    resultado = _MD_BLOQUE_CODIGO.sub("", texto_markdown)
    resultado = resultado.replace("```", "")
    resultado = _MD_ENCABEZADO.sub("", resultado)
    resultado = _MD_ENLACE.sub(r"\1", resultado)
    resultado = _MD_CODIGO_INLINE.sub(r"\1", resultado)
    resultado = _MD_NEGRITA.sub(lambda m: m.group(1) or m.group(2) or "", resultado)
    resultado = _MD_CURSIVA.sub(r"\1", resultado)
    return resultado.strip()


def copiar_ultima_respuesta(eventos: list[dict[str, Any]]) -> str:
    """``/copy``: la última respuesta del agente en texto plano, lista para
    portapapeles.

    Escribir en el portapapeles del sistema es del cliente (web tiene la API
    del navegador, iOS la suya) — igual que ``ide_equipo`` deja la ejecución
    real del sub-agente a quien lo conecta, esta función solo prepara el
    texto final.
    """
    for evento in reversed(eventos):
        if str(evento.get("type") or "") != "assistant_final":
            continue
        texto = str(evento.get("text") or "")
        if texto.strip():
            return texto_plano_desde_markdown(texto)
    raise IDESesionExtrasError("Esta sesión todavía no tiene una respuesta del agente.")


# ---------------------------------------------------------------------------
# /permissions
# ---------------------------------------------------------------------------

Categoria = Literal["ejecutar_comandos", "escribir_archivos", "acceder_red"]
CATEGORIAS: tuple[Categoria, ...] = ("ejecutar_comandos", "escribir_archivos", "acceder_red")

CATEGORIA_DESCRIPCION: dict[Categoria, str] = {
    "ejecutar_comandos": "Ejecutar programas en el workspace (ejecutar_comando).",
    "escribir_archivos": "Crear, reemplazar o editar archivos del workspace.",
    "acceder_red": "Salir a Internet a buscar información (buscar_web).",
}

# Herramienta local del agente (ver ``TOOLS`` en ``ide_workers_agent.py``) ->
# categoría que la gatea. Las de solo lectura (``listar_archivos``,
# ``leer_archivo``, ``buscar_en_archivos``) quedan FUERA a propósito: no
# escriben, no ejecutan nada y no salen del workspace, así que exigirles
# confirmación no protegería nada y sí volvería inservible al IDE.
HERRAMIENTA_A_CATEGORIA: dict[str, Categoria] = {
    "ejecutar_comando": "ejecutar_comandos",
    "escribir_archivo": "escribir_archivos",
    "editar_archivo": "escribir_archivos",
    "buscar_web": "acceder_red",
}


class PermisosIDEError(ValueError):
    """Categoría desconocida o payload de permisos inválido."""


@dataclass(frozen=True, slots=True)
class CambioPermiso:
    """Un cambio de política, tal cual queda en el historial auditado."""

    categoria: Categoria
    automatico: bool
    en: str


class PermissionsStore:
    """Qué puede hacer el agente del IDE SIN preguntar, por categoría.

    Extiende al IDE el mismo principio que ya aplica ``approval.py`` al
    resto de acciones del companion (lista blanca explícita, vacía por
    defecto — ver su docstring de módulo): por defecto NINGUNA de las tres
    categorías corre sin preguntar. Aflojar una es una llamada explícita a
    ``permitir_automatico`` — nunca el estado de fábrica — y queda anotada en
    ``historial()`` con fecha, para que aflojar algo sea una decisión
    rastreable del dueño, no un ajuste silencioso.
    """

    def __init__(self, state_dir: Path | str) -> None:
        self._path = Path(state_dir) / "ide-permisos.json"
        self._automatico: dict[Categoria, bool] = dict.fromkeys(CATEGORIAS, False)
        self._historial: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        payload = _read_json(self._path)
        crudo = payload.get("automatico")
        if isinstance(crudo, dict):
            for categoria in CATEGORIAS:
                valor = crudo.get(categoria)
                if isinstance(valor, bool):
                    self._automatico[categoria] = valor
        historial = payload.get("historial")
        if isinstance(historial, list):
            self._historial = [row for row in historial if isinstance(row, dict)]

    def _save(self) -> None:
        _atomic_write_json(
            self._path,
            {
                "version": 1,
                "automatico": dict(self._automatico),
                "historial": self._historial[-MAX_HISTORIAL_PERMISOS:],
            },
        )

    @staticmethod
    def _validar_categoria(categoria: str) -> Categoria:
        if categoria not in CATEGORIAS:
            raise PermisosIDEError(f"Categoría de permiso desconocida: {categoria!r}.")
        return categoria  # type: ignore[return-value]

    def politica_actual(self) -> dict[Categoria, bool]:
        """``True`` = corre automático (sin preguntar); ``False`` = pregunta siempre."""
        return dict(self._automatico)

    def requiere_confirmacion(self, categoria: str) -> bool:
        return not self._automatico[self._validar_categoria(categoria)]

    def requiere_confirmacion_para_herramienta(self, nombre_herramienta: str) -> bool:
        """``False`` para herramientas de solo lectura o desconocidas: el gate
        solo aplica a las tres categorías declaradas en ``HERRAMIENTA_A_CATEGORIA``."""
        categoria = HERRAMIENTA_A_CATEGORIA.get(nombre_herramienta)
        if categoria is None:
            return False
        return self.requiere_confirmacion(categoria)

    def _registrar_cambio(self, categoria: Categoria, automatico: bool) -> CambioPermiso:
        self._automatico[categoria] = automatico
        cambio = CambioPermiso(categoria=categoria, automatico=automatico, en=_now())
        self._historial.append(
            {"categoria": cambio.categoria, "automatico": cambio.automatico, "en": cambio.en}
        )
        self._save()
        return cambio

    def permitir_automatico(self, categoria: str) -> CambioPermiso:
        """Afloja UNA categoría: decisión explícita del dueño, nunca el default."""
        return self._registrar_cambio(self._validar_categoria(categoria), True)

    def exigir_confirmacion(self, categoria: str) -> CambioPermiso:
        """Revierte el aflojado: vuelve a pedir confirmación en esa categoría."""
        return self._registrar_cambio(self._validar_categoria(categoria), False)

    def historial(self) -> list[dict[str, Any]]:
        return list(self._historial)
