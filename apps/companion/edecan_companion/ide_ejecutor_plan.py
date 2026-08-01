"""Ejecutor de un plan escrito (Markdown) por el agente del IDE.

Este es el flujo real del dueño: hoy le escribe un mensaje al IDE; lo que
necesita es pedirle un PLAN a un modelo grande por fuera (Antigravity, Codex,
lo que sea), pegarle ese documento al IDE de Edecán, y que el IDE lo
ejecute entero -- paso por paso, marcando cada uno conforme lo termina, sin
perder el hilo si la app se cierra a la mitad.

Los planes reales no vienen con formato garantizado. La misma idea ("primero
haz X, luego Y, después Z") puede llegar como:

- Casillas de tarea: ``- [ ] hacer X`` / ``- [x] ya hecho``.
- Lista numerada: ``1. hacer X``.
- Viñetas simples: ``- hacer X``.
- Encabezados con prosa debajo: ``## Paso 2: hacer X`` seguido de un párrafo.
- Prosa suelta sin ninguna marca de lista (último recurso: un párrafo por
  paso, separado por línea en blanco).

``interpretar_markdown()`` detecta cuál de estos formatos domina el
documento y produce una lista de pasos ya normalizada, generosa a propósito:
mejor reconocer de más (y dejar que el dueño corrija si algo no cuadra) que
rechazar un plan real solo porque no usa el formato "correcto". Lo que SÍ es
estricto es el reporte: ``InterpretacionPlan.resumen()`` devuelve
exactamente cómo se entendió cada paso y de qué depende, para mostrárselo al
dueño ANTES de que se ejecute nada -- confirma o corrige, y solo entonces se
persiste con ``EjecutorPlanStore.crear()``.

Dependencias: un plan real casi nunca llega en el orden correcto de
ejecución ("un plan mal ordenado es lo normal, no la excepción"). Por eso
este módulo NO asume que el paso 3 depende del 2 solo por venir después en
el texto -- las dependencias se declaran explícitas en el propio texto
("depende del paso 2", "requiere los pasos 1 y 3", "después del paso 4"...)
y ``iniciar_siguiente()`` elige el próximo paso EJECUTABLE según ese grafo,
no según el orden en que aparece escrito. Un plan con un ciclo de
dependencias, o que referencia un paso que no existe, se rechaza entero en
``crear()`` -- igual que ``ide_equipo.construir_plan`` rechaza un reparto de
archivos solapado: mejor no arrancar nada que arrancar con un estado
imposible de completar.

Estado por paso (persistente, no en memoria como ``ide_plan.PlanStore``):
``pendiente`` -> ``en_curso`` -> ``hecho`` | ``fallado`` | ``omitido``. Un
paso ``fallado`` no es el fin del plan: ``reintentar_paso()`` lo regresa a
``pendiente`` sin tocar los pasos ya ``hecho``/``omitido`` -- así se puede
arreglar el paso 4 y seguir desde ahí sin repetir el 1, 2 y 3. Todo esto se
guarda en disco (``EjecutorPlanStore``, mismo patrón atómico
tmp+``os.replace``+chmod 0o600 que ``ide_checkpoints.CheckpointStore``) para
que cerrar la app a la mitad de un plan largo no pierda el avance.

``ide_plan.py`` NO se duplica aquí: ese módulo modela una aprobación corta
en memoria (¿el usuario aprueba este puñado de pasos antes de que el agente
actúe?), pensado para una tarea de una sola conversación sin dependencias
entre pasos. Este módulo resuelve un problema distinto -- un DOCUMENTO de
plan, potencialmente largo, con dependencias no lineales, que debe
sobrevivir a cerrar la app -- y por eso tiene su propio modelo de estado en
vez de forzar el de ``ide_plan`` a hacer algo para lo que no fue pensado.

Integración prevista (no se toca ``ide_sessions.py`` desde aquí, ver
encargo): quien orqueste (hoy ``SessionManager``) llamaría, en un ciclo:

1. ``store.crear(markdown, session_id=...)`` una vez, tras mostrarle al
   dueño la ``InterpretacionPlan.resumen()`` y que confirme.
2. ``paso = store.iniciar_siguiente(plan_id)`` -- si es ``None``, el plan
   terminó (``estado == "completado"``) o está ``bloqueado`` esperando que
   alguien arregle un paso fallado (``reintentar_paso``) o lo omita
   (``omitir_paso``).
3. Usar ``paso["descripcion"]``/``paso["detalle"]`` como prompt de un turno
   de ``WorkersIDEAgent.run(...)`` (opcionalmente envuelto en un checkpoint
   de ``ide_checkpoints`` para poder deshacer ESE paso puntual).
4. Según el resultado: ``store.marcar_hecho(plan_id, paso["id"], nota=...)``
   o ``store.marcar_fallado(plan_id, paso["id"], motivo=...)``.
5. Volver a 2 hasta que devuelva ``None``.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------- #
# Topes explícitos -- ver el mismo criterio en ``ide_checkpoints.py``:
# nunca reventar con un documento raro, rechazarlo con un mensaje claro.
# --------------------------------------------------------------------- #

MAX_MARKDOWN_CHARS = 200_000
MAX_PASOS = 200
MAX_DESCRIPCION_CHARS = 400
MAX_DETALLE_CHARS = 4000
MAX_NOTA_CHARS = 500


class IDEEjecutorPlanError(ValueError):
    """Plan inválido (vacío, sin pasos reconocibles, ciclo de dependencias,
    referencia a un paso inexistente), plan/paso no encontrado, o transición
    de estado no permitida (p. ej. marcar hecho un paso que no está en curso)."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


# --------------------------------------------------------------------- #
# Reconocimiento de formato -- checkboxes, numeradas, viñetas, encabezados.
# --------------------------------------------------------------------- #

_RE_ENCABEZADO = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_RE_CHECKBOX = re.compile(r"^(\s*)[-*+]\s+\[([ xX])\]\s+(.*\S)\s*$")
_RE_NUMERADA = re.compile(r"^(\s*)(\d{1,4})[.\)]\s+(.*\S)\s*$")
_RE_VINETA = re.compile(r"^(\s*)[-*+]\s+(?!\[[ xX]\])(.*\S)\s*$")

_RE_PASO_LABEL = re.compile(
    r"^\s*(?:paso|step)\s*#?\s*(\d{1,4})\s*[:\-.)]?\s*(.*)$", re.IGNORECASE
)

# Conectores/artículos españoles habituales entre el verbo disparador
# ("depende", "requiere"...) y la palabra "paso(s)" -- "depende DEL paso",
# "requiere LOS pasos", "después DE LOS pasos"... Cubrir las combinaciones
# reales es más importante que la elegancia del regex.
_CONECTOR = (
    r"(?:del\s+|de\s+la\s+|de\s+las\s+|de\s+los\s+|de\s+el\s+|de\s+|el\s+|los\s+|las\s+|la\s+)?"
)

_RE_DEP_TRIGGER = re.compile(
    r"(?:"
    rf"depende(?:n)?\s+{_CONECTOR}pasos?|"
    rf"requiere(?:n)?\s+{_CONECTOR}pasos?|"
    rf"necesita(?:n)?\s+{_CONECTOR}pasos?|"
    rf"despu[eé]s\s+{_CONECTOR}pasos?|"
    rf"luego\s+{_CONECTOR}pasos?|"
    rf"bloquead[oa]\s+por\s+{_CONECTOR}pasos?"
    r")"
    r"\s*[:\-]?\s*((?:\d+[\s,y&e]*)+)",
    re.IGNORECASE,
)


def _nivel_indentacion(espacios: str) -> int:
    return len(espacios.expandtabs(4))


def _extraer_etiqueta(texto: str) -> tuple[int | None, str]:
    """Si ``texto`` empieza con "Paso N:" / "Step N -", separa el número
    explícito del resto. Si no, no hay etiqueta y el texto vuelve tal cual."""

    limpio = texto.strip()
    m = _RE_PASO_LABEL.match(limpio)
    if not m:
        return None, limpio
    resto = m.group(2).strip()
    return int(m.group(1)), (resto if resto else limpio)


def _dependencias_en_texto(texto: str) -> list[int]:
    """Todos los números de paso referenciados por una frase de dependencia
    en ``texto`` (cabecera + cuerpo de un paso), en orden de aparición y sin
    repetir."""

    numeros: list[int] = []
    for m in _RE_DEP_TRIGGER.finditer(texto):
        for crudo in re.findall(r"\d+", m.group(1)):
            valor = int(crudo)
            if valor not in numeros:
                numeros.append(valor)
    return numeros


def _quitar_clausula_dependencia(linea: str) -> str:
    """Quita la frase de dependencia (si la hay) de la cabecera de un paso,
    para que la descripción quede legible sin "depende del paso 2" incrustado.
    Si no hay frase de dependencia, devuelve la línea intacta -- no hay nada
    que limpiar y no vale la pena arriesgar puntuación de una frase normal."""

    original = linea.strip()
    limpio = _RE_DEP_TRIGGER.sub("", original)
    if limpio == original:
        return original
    limpio = re.sub(r"\(\s*\)", "", limpio)
    limpio = re.sub(r"\s{2,}", " ", limpio).strip()
    limpio = limpio.strip(".,;: -")
    return limpio or original


@dataclass
class _PasoBruto:
    """Un paso recién extraído del Markdown, antes de resolver dependencias
    ni asignar id -- estado intermedio interno de ``interpretar_markdown``."""

    cabecera: str
    cuerpo: list[str] = field(default_factory=list)
    etiqueta: int | None = None
    marcado: bool = False


def _detectar_formato(lineas: list[str]) -> str:
    """¿Cuál marca de lista domina el documento? Prioridad: casillas (la
    señal más explícita de "esto ya se pensó como lista de tareas") >
    numerada > viñetas > encabezados > prosa suelta (último recurso)."""

    conteo = {"checkbox": 0, "numerada": 0, "vinetas": 0, "encabezados": 0}
    for linea in lineas:
        m_cb = _RE_CHECKBOX.match(linea)
        if m_cb and _nivel_indentacion(m_cb.group(1)) < 2:
            conteo["checkbox"] += 1
            continue
        m_num = _RE_NUMERADA.match(linea)
        if m_num and _nivel_indentacion(m_num.group(1)) < 2:
            conteo["numerada"] += 1
            continue
        m_vi = _RE_VINETA.match(linea)
        if m_vi and _nivel_indentacion(m_vi.group(1)) < 2:
            conteo["vinetas"] += 1
            continue
        m_h = _RE_ENCABEZADO.match(linea)
        if m_h and len(m_h.group(1)) >= 2:
            conteo["encabezados"] += 1
    for formato in ("checkbox", "numerada", "vinetas", "encabezados"):
        if conteo[formato] > 0:
            return formato
    return "parrafos"


def _construir_desde_items(lineas: list[str], tipo: str) -> tuple[list[_PasoBruto], str | None]:
    """Extrae pasos de una lista (casillas/numerada/viñetas). Todo lo que no
    sea un ítem de tope (nivel < 2 de indentación) se pega como cuerpo del
    paso anterior -- una sub-línea con "depende de..." cuelga así del paso
    correcto sin que el llamador tenga que declarar su forma exacta."""

    patrones = {"checkbox": _RE_CHECKBOX, "numerada": _RE_NUMERADA, "vinetas": _RE_VINETA}
    patron = patrones[tipo]
    brutos: list[_PasoBruto] = []
    titulo: str | None = None
    actual: _PasoBruto | None = None
    for linea in lineas:
        m_titulo = _RE_ENCABEZADO.match(linea)
        if titulo is None and m_titulo and len(m_titulo.group(1)) == 1:
            titulo = m_titulo.group(2).strip()
            continue
        m = patron.match(linea)
        if m and _nivel_indentacion(m.group(1)) < 2:
            if tipo == "checkbox":
                marcado = m.group(2).lower() == "x"
                etiqueta, cabecera = _extraer_etiqueta(m.group(3))
            elif tipo == "numerada":
                marcado = False
                etiqueta = int(m.group(2))
                cabecera = m.group(3).strip()
            else:  # vinetas
                marcado = False
                etiqueta, cabecera = _extraer_etiqueta(m.group(2))
            actual = _PasoBruto(cabecera=cabecera, etiqueta=etiqueta, marcado=marcado)
            brutos.append(actual)
            continue
        if actual is not None and linea.strip():
            actual.cuerpo.append(linea.strip())
    return brutos, titulo


def _construir_desde_encabezados(lineas: list[str]) -> tuple[list[_PasoBruto], str | None]:
    """Cuando el plan es prosa organizada por encabezados: el primer título
    nivel 1 es el título del plan (no un paso); cada encabezado nivel >= 2 es
    un paso, y los párrafos debajo (hasta el próximo encabezado) son su
    cuerpo -- ahí es donde suele vivir "depende del paso X" en este formato."""

    brutos: list[_PasoBruto] = []
    titulo: str | None = None
    actual: _PasoBruto | None = None
    for linea in lineas:
        m = _RE_ENCABEZADO.match(linea)
        if m:
            nivel = len(m.group(1))
            texto = m.group(2).strip()
            if nivel == 1 and titulo is None:
                titulo = texto
                actual = None
                continue
            if nivel >= 2:
                etiqueta, cabecera = _extraer_etiqueta(texto)
                actual = _PasoBruto(cabecera=cabecera, etiqueta=etiqueta)
                brutos.append(actual)
                continue
            actual = None
            continue
        if actual is not None and linea.strip():
            actual.cuerpo.append(linea.strip())
    return brutos, titulo


def _construir_desde_parrafos(texto: str) -> tuple[list[_PasoBruto], str | None]:
    """Último recurso: sin casillas, sin listas, sin encabezados. Se asume
    que cada párrafo (separado por línea en blanco) describe un paso -- es
    generoso a propósito, para no dejar un plan de prosa suelta sin
    interpretar solo porque no usa ninguna marca de lista."""

    bloques = re.split(r"\n\s*\n", texto.strip())
    brutos: list[_PasoBruto] = []
    for bloque in bloques:
        lineas_bloque = [linea.strip() for linea in bloque.splitlines() if linea.strip()]
        if not lineas_bloque:
            continue
        etiqueta, cabecera = _extraer_etiqueta(lineas_bloque[0])
        brutos.append(_PasoBruto(cabecera=cabecera, cuerpo=lineas_bloque[1:], etiqueta=etiqueta))
    return brutos, None


def _detectar_ciclo(grafo: Mapping[int, tuple[int, ...]]) -> list[int] | None:
    """DFS clásico con tres colores. Devuelve la cadena de pasos que forma
    el ciclo (para poder mostrarla en el error), o ``None`` si el grafo de
    dependencias es un DAG válido."""

    color: dict[int, int] = dict.fromkeys(grafo, 0)  # 0=sin visitar, 1=en pila, 2=cerrado
    pila: list[int] = []

    def visitar(nodo: int) -> list[int] | None:
        color[nodo] = 1
        pila.append(nodo)
        for vecino in grafo.get(nodo, ()):
            if color.get(vecino, 0) == 1:
                inicio = pila.index(vecino)
                return [*pila[inicio:], vecino]
            if color.get(vecino, 0) == 0:
                encontrado = visitar(vecino)
                if encontrado:
                    return encontrado
        pila.pop()
        color[nodo] = 2
        return None

    for nodo in grafo:
        if color[nodo] == 0:
            encontrado = visitar(nodo)
            if encontrado:
                return encontrado
    return None


# --------------------------------------------------------------------- #
# Modelo público de datos.
# --------------------------------------------------------------------- #

_ESTADOS_PASO_VALIDOS = frozenset({"pendiente", "en_curso", "hecho", "fallado", "omitido"})
_ESTADOS_TERMINALES_PASO = frozenset({"hecho", "omitido"})


@dataclass
class PasoPlan:
    """Un paso ya normalizado: su texto, de qué otros pasos depende (por
    ``orden``, la posición 1-indexada del paso en el documento) y su estado."""

    id: str
    orden: int
    etiqueta: int | None
    descripcion: str
    detalle: str | None
    depende_de: tuple[int, ...]
    estado: str = "pendiente"
    nota: str | None = None
    motivo_fallo: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "orden": self.orden,
            "etiqueta": self.etiqueta,
            "descripcion": self.descripcion,
            "detalle": self.detalle,
            "depende_de": list(self.depende_de),
            "estado": self.estado,
            "nota": self.nota,
            "motivo_fallo": self.motivo_fallo,
        }

    to_json = public

    @staticmethod
    def from_json(raw: Mapping[str, Any]) -> PasoPlan:
        return PasoPlan(
            id=str(raw["id"]),
            orden=int(raw["orden"]),
            etiqueta=raw.get("etiqueta"),
            descripcion=str(raw["descripcion"]),
            detalle=raw.get("detalle"),
            depende_de=tuple(int(n) for n in raw.get("depende_de", [])),
            estado=str(raw.get("estado", "pendiente")),
            nota=raw.get("nota"),
            motivo_fallo=raw.get("motivo_fallo"),
        )


@dataclass
class InterpretacionPlan:
    """Lo que ``interpretar_markdown`` entendió, ANTES de persistir nada --
    para mostrárselo al dueño y que confirme. ``errores`` no vacío significa
    que el plan no se puede ejecutar tal como está (ver ``es_ejecutable``);
    ``advertencias`` son señales de "esto es raro pero se puede seguir"."""

    titulo: str
    formato_detectado: str
    pasos: list[PasoPlan]
    advertencias: list[str]
    errores: list[str]

    @property
    def es_ejecutable(self) -> bool:
        return not self.errores

    def resumen(self) -> dict[str, Any]:
        return {
            "titulo": self.titulo,
            "formato_detectado": self.formato_detectado,
            "total_pasos": len(self.pasos),
            "pasos": [paso.public() for paso in self.pasos],
            "advertencias": list(self.advertencias),
            "errores": list(self.errores),
            "es_ejecutable": self.es_ejecutable,
        }


def interpretar_markdown(
    markdown: str, *, titulo_por_defecto: str | None = None
) -> InterpretacionPlan:
    """Convierte un plan en Markdown (formato libre, ver docstring del
    módulo) en una lista de pasos con sus dependencias ya resueltas.

    Nunca lanza: entradas basura o planes rotos vuelven con ``errores`` no
    vacío en vez de una excepción, para que quien llame pueda mostrarle el
    problema al dueño en vez de que la app reviente a media conversación.
    """

    if not isinstance(markdown, str) or not markdown.strip():
        return InterpretacionPlan(
            titulo=(titulo_por_defecto or "Plan sin título").strip(),
            formato_detectado="vacio",
            pasos=[],
            advertencias=[],
            errores=["El plan está vacío; no hay nada que interpretar."],
        )
    if len(markdown) > MAX_MARKDOWN_CHARS:
        return InterpretacionPlan(
            titulo=(titulo_por_defecto or "Plan sin título").strip(),
            formato_detectado="demasiado_grande",
            pasos=[],
            advertencias=[],
            errores=[
                f"El plan supera los {MAX_MARKDOWN_CHARS} caracteres; "
                "divídelo en documentos más chicos."
            ],
        )

    lineas = markdown.splitlines()
    formato = _detectar_formato(lineas)
    if formato == "parrafos":
        brutos, titulo_detectado = _construir_desde_parrafos(markdown)
    elif formato == "encabezados":
        brutos, titulo_detectado = _construir_desde_encabezados(lineas)
    else:
        brutos, titulo_detectado = _construir_desde_items(lineas, formato)

    advertencias: list[str] = []
    errores: list[str] = []

    if not brutos:
        errores.append("No se encontraron pasos reconocibles en el plan.")
    if len(brutos) > MAX_PASOS:
        errores.append(
            f"El plan tiene {len(brutos)} pasos; el máximo soportado es {MAX_PASOS}."
        )

    # Mapa "número referenciado en una dependencia" -> "orden real del paso
    # dueño de ese número". Por defecto cada paso responde a su propia
    # posición; una etiqueta explícita ("Paso 5: ...") la sobrescribe -- así
    # un plan reordenado (heading "Paso 3" antes que "Paso 2" en el texto)
    # sigue resolviendo bien sus dependencias por el número que el propio
    # plan usa, no por dónde quedó escrito.
    numero_a_orden: dict[int, int] = {i: i for i in range(1, len(brutos) + 1)}
    etiquetas_vistas: set[int] = set()
    for i, bruto in enumerate(brutos, start=1):
        if bruto.etiqueta is None:
            continue
        if bruto.etiqueta in etiquetas_vistas:
            advertencias.append(
                f"El paso {i} declara la etiqueta 'paso {bruto.etiqueta}', ya usada por otro "
                f"paso anterior; se referencia solo por su posición ({i})."
            )
            continue
        etiquetas_vistas.add(bruto.etiqueta)
        numero_a_orden[bruto.etiqueta] = i

    pasos: list[PasoPlan] = []
    for i, bruto in enumerate(brutos, start=1):
        cuerpo_texto = "\n".join(bruto.cuerpo)
        texto_completo = f"{bruto.cabecera}\n{cuerpo_texto}" if cuerpo_texto else bruto.cabecera
        numeros_dep = _dependencias_en_texto(texto_completo)
        descripcion = _quitar_clausula_dependencia(bruto.cabecera)[:MAX_DESCRIPCION_CHARS]
        descripcion = descripcion or f"Paso {i}"
        detalle = cuerpo_texto[:MAX_DETALLE_CHARS] if cuerpo_texto else None

        depende_de: list[int] = []
        for n in numeros_dep:
            objetivo = numero_a_orden.get(n)
            if objetivo is None:
                errores.append(
                    f"El paso {i} ('{descripcion}') depende de un paso {n} que no existe "
                    "en el plan."
                )
                continue
            if objetivo == i:
                errores.append(f"El paso {i} ('{descripcion}') no puede depender de sí mismo.")
                continue
            if objetivo not in depende_de:
                depende_de.append(objetivo)

        pasos.append(
            PasoPlan(
                id=str(uuid.uuid4()),
                orden=i,
                etiqueta=bruto.etiqueta,
                descripcion=descripcion,
                detalle=detalle,
                depende_de=tuple(sorted(depende_de)),
                estado="hecho" if bruto.marcado else "pendiente",
            )
        )

    grafo = {paso.orden: paso.depende_de for paso in pasos}
    ciclo = _detectar_ciclo(grafo)
    if ciclo:
        cadena = " → ".join(f"paso {n}" for n in ciclo)
        errores.append(f"Ciclo de dependencias detectado: {cadena}.")

    return InterpretacionPlan(
        titulo=(titulo_detectado or titulo_por_defecto or "Plan sin título").strip(),
        formato_detectado=formato,
        pasos=pasos,
        advertencias=advertencias,
        errores=errores,
    )


@dataclass
class PlanEjecutable:
    """Un plan ya validado y persistido, con el estado de ejecución de cada
    paso. ``estado`` (propiedad) se calcula siempre a partir de los pasos --
    nunca se guarda como un campo aparte que pueda desincronizarse."""

    id: str
    session_id: str | None
    titulo: str
    formato_detectado: str
    pasos: list[PasoPlan]
    advertencias: list[str] = field(default_factory=list)
    cancelado: bool = False
    motivo_cancelacion: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    @property
    def pasos_por_orden(self) -> dict[int, PasoPlan]:
        return {paso.orden: paso for paso in self.pasos}

    @property
    def pasos_por_id(self) -> dict[str, PasoPlan]:
        return {paso.id: paso for paso in self.pasos}

    def _dependencias_satisfechas(self, paso: PasoPlan) -> bool:
        por_orden = self.pasos_por_orden
        return all(por_orden[d].estado in _ESTADOS_TERMINALES_PASO for d in paso.depende_de)

    def pasos_listos(self) -> list[PasoPlan]:
        """Pasos ``pendiente`` cuyas dependencias ya están ``hecho``/``omitido``
        -- candidatos reales a ejecutarse ahora, sin importar en qué orden
        los haya escrito el documento original."""

        return [
            paso
            for paso in self.pasos
            if paso.estado == "pendiente" and self._dependencias_satisfechas(paso)
        ]

    @property
    def estado(self) -> str:
        if self.cancelado:
            return "cancelado"
        estados = [paso.estado for paso in self.pasos]
        if any(e == "en_curso" for e in estados):
            return "en_curso"
        if all(e in _ESTADOS_TERMINALES_PASO for e in estados):
            return "completado"
        if any(e == "fallado" for e in estados):
            return "bloqueado"
        if all(e == "pendiente" for e in estados):
            return "pendiente"
        return "en_progreso"

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "titulo": self.titulo,
            "formato_detectado": self.formato_detectado,
            "estado": self.estado,
            "pasos": [paso.public() for paso in self.pasos],
            "advertencias": list(self.advertencias),
            "cancelado": self.cancelado,
            "motivo_cancelacion": self.motivo_cancelacion,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_json(self) -> dict[str, Any]:
        payload = self.public()
        payload["version"] = 1
        del payload["estado"]  # calculado; no se persiste para no desincronizarse
        return payload

    @staticmethod
    def from_json(raw: Mapping[str, Any]) -> PlanEjecutable:
        return PlanEjecutable(
            id=str(raw["id"]),
            session_id=raw.get("session_id"),
            titulo=str(raw.get("titulo") or ""),
            formato_detectado=str(raw.get("formato_detectado") or ""),
            pasos=[PasoPlan.from_json(p) for p in raw.get("pasos", [])],
            advertencias=list(raw.get("advertencias") or []),
            cancelado=bool(raw.get("cancelado", False)),
            motivo_cancelacion=raw.get("motivo_cancelacion"),
            created_at=str(raw.get("created_at") or _now()),
            updated_at=str(raw.get("updated_at") or _now()),
        )


class EjecutorPlanStore:
    """Crea, avanza y persiste planes de ejecución en disco.

    Layout bajo ``state_dir / "ide-plan-ejecucion"``::

        planes/<plan_id>.json   # un manifiesto por plan, con todos sus pasos
        tmp/                    # staging para escrituras atómicas

    Un ``RLock`` en memoria (mismo patrón que ``ide_plan.PlanStore``)
    serializa las transiciones dentro de ESTE proceso; el volumen esperado
    (un plan activo por sesión, transiciones humanas/de un agente, no un
    hot loop) hace innecesaria una solución multi-proceso.
    """

    def __init__(self, state_dir: Path) -> None:
        self.root = Path(state_dir) / "ide-plan-ejecucion"
        self.planes_dir = self.root / "planes"
        self.tmp_dir = self.root / "tmp"
        for directorio in (self.planes_dir, self.tmp_dir):
            directorio.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # -- persistencia --------------------------------------------------

    def _manifest_path(self, plan_id: str) -> Path:
        return self.planes_dir / f"{plan_id}.json"

    def _guardar(self, plan: PlanEjecutable) -> None:
        path = self._manifest_path(plan.id)
        tmp_path = self.tmp_dir / f".plan-{uuid.uuid4().hex}.tmp"
        tmp_path.write_text(
            json.dumps(plan.to_json(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        try:
            tmp_path.chmod(0o600)
        except OSError:
            pass
        os.replace(tmp_path, path)

    def _cargar(self, plan_id: str) -> PlanEjecutable:
        path = self._manifest_path(plan_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise IDEEjecutorPlanError(f"Plan no encontrado: {plan_id}.") from exc
        return PlanEjecutable.from_json(raw)

    def _obtener_paso(self, plan: PlanEjecutable, paso_id: str) -> PasoPlan:
        paso = plan.pasos_por_id.get(paso_id)
        if paso is None:
            raise IDEEjecutorPlanError(f"El plan no tiene un paso con id {paso_id}.")
        return paso

    # -- ciclo de vida ---------------------------------------------------

    def crear(
        self, markdown: str, *, session_id: str | None = None, titulo: str | None = None
    ) -> dict[str, Any]:
        """Interpreta ``markdown`` y, si es ejecutable (sin ``errores``),
        lo persiste como un plan nuevo con todos los pasos ``pendiente``
        (salvo los que llegaron con casilla ya marcada: esos nacen ``hecho``,
        para poder retomar un plan que el dueño ya avanzó a mano)."""

        interpretacion = interpretar_markdown(markdown, titulo_por_defecto=titulo)
        if interpretacion.errores:
            raise IDEEjecutorPlanError(
                "El plan no se puede ejecutar tal como está escrito: "
                + " ".join(interpretacion.errores)
            )
        with self._lock:
            plan = PlanEjecutable(
                id=str(uuid.uuid4()),
                session_id=session_id,
                titulo=interpretacion.titulo,
                formato_detectado=interpretacion.formato_detectado,
                pasos=interpretacion.pasos,
                advertencias=interpretacion.advertencias,
            )
            self._guardar(plan)
            return plan.public()

    def obtener(self, plan_id: str) -> dict[str, Any]:
        with self._lock:
            return self._cargar(plan_id).public()

    def listar(self, session_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            planes: list[PlanEjecutable] = []
            for manifest in self.planes_dir.glob("*.json"):
                try:
                    raw = json.loads(manifest.read_text(encoding="utf-8"))
                    plan = PlanEjecutable.from_json(raw)
                except (OSError, json.JSONDecodeError, KeyError):
                    continue
                if session_id is None or plan.session_id == session_id:
                    planes.append(plan)
            planes.sort(key=lambda p: p.created_at)
            return [plan.public() for plan in planes]

    def pasos_listos(self, plan_id: str) -> list[dict[str, Any]]:
        """Solo lectura: qué pasos podrían arrancar ahora mismo, sin
        reclamar ninguno. Útil para mostrarle al dueño el frente de
        ejecución antes de que ``iniciar_siguiente`` tome uno."""

        with self._lock:
            plan = self._cargar(plan_id)
            return [paso.public() for paso in plan.pasos_listos()]

    def iniciar_siguiente(self, plan_id: str) -> dict[str, Any] | None:
        """Reclama el próximo paso ejecutable (el de menor ``orden`` entre
        los que ya tienen sus dependencias satisfechas) y lo marca
        ``en_curso``. Devuelve ``None`` si no hay ninguno -- porque el plan
        ya terminó o porque está ``bloqueado`` por un paso fallado que
        alguien debe resolver primero con ``reintentar_paso``/``omitir_paso``.

        Solo admite UN paso ``en_curso`` a la vez: es la vía sencilla y
        segura para un ejecutor secuencial (el caso real de este módulo);
        repartir varios pasos en paralelo es tarea de ``ide_equipo``, no de
        este módulo.
        """

        with self._lock:
            plan = self._cargar(plan_id)
            if plan.cancelado:
                raise IDEEjecutorPlanError("Este plan fue cancelado; no admite más pasos.")
            en_curso = next((p for p in plan.pasos if p.estado == "en_curso"), None)
            if en_curso is not None:
                raise IDEEjecutorPlanError(
                    f"Ya hay un paso en curso ('{en_curso.descripcion}'); "
                    "resuélvelo con marcar_hecho/marcar_fallado antes de iniciar otro."
                )
            listos = plan.pasos_listos()
            if not listos:
                return None
            siguiente = min(listos, key=lambda p: p.orden)
            siguiente.estado = "en_curso"
            plan.updated_at = _now()
            self._guardar(plan)
            return siguiente.public()

    def marcar_hecho(
        self, plan_id: str, paso_id: str, *, nota: str | None = None
    ) -> dict[str, Any]:
        with self._lock:
            plan = self._cargar(plan_id)
            paso = self._obtener_paso(plan, paso_id)
            if paso.estado != "en_curso":
                raise IDEEjecutorPlanError(
                    f"El paso '{paso.descripcion}' está «{paso.estado}», no «en_curso»; "
                    "no se puede marcar como hecho."
                )
            paso.estado = "hecho"
            if isinstance(nota, str) and nota.strip():
                paso.nota = nota.strip()[:MAX_NOTA_CHARS]
            plan.updated_at = _now()
            self._guardar(plan)
            return plan.public()

    def marcar_fallado(self, plan_id: str, paso_id: str, *, motivo: str) -> dict[str, Any]:
        if not isinstance(motivo, str) or not motivo.strip():
            raise IDEEjecutorPlanError("El motivo del fallo no puede estar vacío.")
        with self._lock:
            plan = self._cargar(plan_id)
            paso = self._obtener_paso(plan, paso_id)
            if paso.estado != "en_curso":
                raise IDEEjecutorPlanError(
                    f"El paso '{paso.descripcion}' está «{paso.estado}», no «en_curso»; "
                    "no se puede marcar como fallado."
                )
            paso.estado = "fallado"
            paso.motivo_fallo = motivo.strip()[:MAX_NOTA_CHARS]
            plan.updated_at = _now()
            self._guardar(plan)
            return plan.public()

    def omitir_paso(
        self, plan_id: str, paso_id: str, *, nota: str | None = None
    ) -> dict[str, Any]:
        """El dueño decide que este paso ya no aplica (o que no importa que
        haya fallado): lo salta sin haberlo hecho. Los pasos que dependían de
        él quedan libres para avanzar -- ``omitido`` cuenta como resuelto."""

        with self._lock:
            plan = self._cargar(plan_id)
            paso = self._obtener_paso(plan, paso_id)
            if paso.estado not in ("pendiente", "en_curso", "fallado"):
                raise IDEEjecutorPlanError(
                    f"El paso '{paso.descripcion}' está «{paso.estado}»; no se puede omitir."
                )
            paso.estado = "omitido"
            paso.motivo_fallo = None
            if isinstance(nota, str) and nota.strip():
                paso.nota = nota.strip()[:MAX_NOTA_CHARS]
            plan.updated_at = _now()
            self._guardar(plan)
            return plan.public()

    def reintentar_paso(self, plan_id: str, paso_id: str) -> dict[str, Any]:
        """Retoma un paso ``fallado``: vuelve a ``pendiente`` sin tocar los
        pasos ya ``hecho``/``omitido``. Esta es la vía para "se rompió el
        paso 4, lo arreglo y sigo desde ahí" sin repetir el 1, 2 y 3."""

        with self._lock:
            plan = self._cargar(plan_id)
            paso = self._obtener_paso(plan, paso_id)
            if paso.estado != "fallado":
                raise IDEEjecutorPlanError(
                    f"El paso '{paso.descripcion}' está «{paso.estado}», no «fallado»; "
                    "no hay nada que reintentar."
                )
            paso.estado = "pendiente"
            paso.motivo_fallo = None
            plan.updated_at = _now()
            self._guardar(plan)
            return plan.public()

    def cancelar(self, plan_id: str, motivo: str | None = None) -> dict[str, Any]:
        """El dueño corta el plan a mitad de camino. Los pasos quedan como
        estaban (igual que ``ide_plan.PlanStore.cancel``): cancelar el plan
        es una decisión distinta de omitir un paso puntual."""

        with self._lock:
            plan = self._cargar(plan_id)
            if plan.cancelado:
                raise IDEEjecutorPlanError("Este plan ya está cancelado.")
            if plan.estado == "completado":
                raise IDEEjecutorPlanError("No se puede cancelar un plan ya completado.")
            plan.cancelado = True
            tiene_motivo = isinstance(motivo, str) and motivo.strip()
            plan.motivo_cancelacion = motivo.strip()[:MAX_NOTA_CHARS] if tiene_motivo else None
            plan.updated_at = _now()
            self._guardar(plan)
            return plan.public()

    def eliminar(self, plan_id: str) -> None:
        with self._lock:
            self._cargar(plan_id)  # valida que exista antes de borrar
            self._manifest_path(plan_id).unlink(missing_ok=True)
