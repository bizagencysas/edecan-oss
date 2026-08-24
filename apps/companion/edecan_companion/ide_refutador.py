"""Sub-agente REFUTADOR: revisión adversarial de un plan de ingeniería ya
ejecutado.

## Por qué existe

``ide_equipo.py`` evita que dos agentes se pisen los archivos. Nadie, hasta
este módulo, tenía el trabajo de REFUTAR la conclusión del agente que hizo
el cambio ("el reparador"). Un modelo revisándose a sí mismo se aprueba casi
siempre -- ve su propio razonamiento y lo confirma. La pieza que faltaba es
un segundo agente que reciba SOLO el encargo original y el resultado
reportado (nunca el razonamiento del primero -- si lo viera, lo adoptaría y
dejaría de ser independiente) con la instrucción explícita de tumbarlo.

## Qué NO es este módulo

No reimplementa ejecución de agentes: quien de verdad corre al refutador es
``WorkersIDEAgent.run()``, el mismo que corre cualquier otro turno. Este
archivo es autocontenido a propósito, igual que ``ide_equipo``/
``ide_reparto``: no importa ``ide_sessions`` ni ``ide_workers_agent`` -- solo
construye el prompt adversarial, decide si vale la pena correrlo (el "gate"
de costo) y parsea/valida el veredicto que el sub-agente devolvió como
texto. Quien lo conecta a un turno real es
``ide_sessions.SessionManager._ejecutar_refutador``, después de que
``PlanificadorReparto.ejecutar`` ya terminó un plan -- ver el docstring de
ese método para el ciclo completo (Cable 5 del cableado del IDE).

## La regla dura de este módulo

Ante la duda, "no demostrado", nunca "aprobado" -- eso se lo pide el propio
prompt al modelo, y este módulo lo hace cumplir incluso si el modelo no
obedece: un "APROBADO" que no vino acompañado de ninguna herramienta de
evidencia real (leer el archivo, correr el comando de verificación...) se
degrada automáticamente a "no demostrado" en ``VeredictoRefutador.verdict``
-- un veredicto que no midió nada es tan poco confiable como el reporte que
debía auditar. Ver ``HERRAMIENTAS_DE_EVIDENCIA``.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

__all__ = [
    "ArchivosPromptTruncado",
    "HERRAMIENTAS_DE_EVIDENCIA",
    "HERRAMIENTAS_PROHIBIDAS",
    "MAX_ARCHIVOS_EN_PROMPT",
    "MAX_PASOS_EN_PROMPT",
    "MODELO_REFUTADOR_POR_DEFECTO",
    "PasoParaRefutar",
    "REFUTADOR_HABILITADO_ENV",
    "REFUTADOR_MODELO_ENV",
    "VeredictoRefutador",
    "construir_prompt",
    "modelo_refutador",
    "motivo_para_omitir",
    "parsear_veredicto",
    "refutador_habilitado",
]

REFUTADOR_HABILITADO_ENV = "IDE_REFUTADOR_HABILITADO"
REFUTADOR_MODELO_ENV = "IDE_REFUTADOR_MODELO"

# Modelo DISTINTO al reparador por defecto (``ide_workers_agent`` usa
# ``MODELO_IDE_POR_DEFECTO`` == "@cf/zai-org/glm-5.2") a propósito: un
# refutador con el mismo modelo y los mismos parámetros que el reparador se
# aprueba a sí mismo -- ver "independencia barata" en el análisis de
# cableado. Catálogo completo en
# ``edecan_llm.task_router.MODELOS_IDE_FALLBACK``.
MODELO_REFUTADOR_POR_DEFECTO = "@cf/moonshotai/kimi-k2.7-code"

# Herramientas que cuentan como "de verdad miró algo" -- ver
# ``VeredictoRefutador.tuvo_evidencia``. Deliberadamente NO incluye
# 'escribir_archivo'/'editar_archivo' (el refutador audita, no arregla) ni
# 'proponer_plan'/'recordar_nota_proyecto' (no son evidencia sobre el
# cambio que está revisando).
HERRAMIENTAS_DE_EVIDENCIA = frozenset(
    {
        "leer_archivo",
        "verificar",
        "ejecutar_comando",
        "buscar_en_archivos",
        "buscar_semanticamente",
        "listar_archivos",
        "auditar_seguridad_proyecto",
    }
)

# El refutador es un auditor, no un segundo reparador: si usa cualquiera de
# estas, algo salió mal (o el modelo no obedeció el prompt). No se descarta
# su veredicto por eso -- se marca la advertencia y se deja que la persona
# lo vea (``VeredictoRefutador.uso_herramientas_prohibidas``).
HERRAMIENTAS_PROHIBIDAS = frozenset({"escribir_archivo", "editar_archivo"})

# Topes duros de lo que entra en el prompt -- el costo del refutador es
# tokens reales, y un plan con decenas de pasos/archivos no puede convertir
# esto en un prompt de tamaño ilimitado. Truncar es preferible a omitir el
# refutador entero: sigue viendo lo más reciente/relevante, con aviso
# explícito de que se recortó (así la persona sabe que la auditoría fue
# parcial, no que no hubo nada que auditar).
MAX_PASOS_EN_PROMPT = 20
MAX_ARCHIVOS_EN_PROMPT = 40


def refutador_habilitado() -> bool:
    """Interruptor de costo: ``IDE_REFUTADOR_HABILITADO=0`` (o "false"/"no"/
    "off") lo apaga por completo, sin importar qué tan grande fue el plan.
    Encendido por defecto -- es el comportamiento que el encargo pide: nadie
    refutaba antes de este módulo."""
    valor = os.environ.get(REFUTADOR_HABILITADO_ENV, "1").strip().lower()
    return valor not in ("0", "false", "no", "off")


def modelo_refutador() -> str:
    return os.environ.get(REFUTADOR_MODELO_ENV) or MODELO_REFUTADOR_POR_DEFECTO


def motivo_para_omitir(
    *, cancelado: bool, hay_pasos_completados: bool, archivos_tocados: int
) -> str | None:
    """``None`` = el refutador debe correr. Cualquier otra cosa es el motivo,
    en español, para que quede constancia VISIBLE de por qué no corrió esta
    vez -- el encargo pide que sea claro cuándo corre y cuándo no, y que no
    aparezca nada en el hilo no cumple con "claro"."""
    if not refutador_habilitado():
        return f"desactivado ({REFUTADOR_HABILITADO_ENV}=0)"
    if cancelado:
        return "el plan se canceló antes de terminar; no hay nada que auditar"
    if not hay_pasos_completados:
        return "ningún paso del plan terminó completado"
    if archivos_tocados == 0:
        return "el plan no modificó ningún archivo"
    return None


@dataclass(frozen=True, slots=True)
class PasoParaRefutar:
    """Lo único del reparador que le llega al refutador sobre UN paso: el
    encargo y lo que reportó -- nunca su razonamiento ni sus llamadas a
    herramientas intermedias (eso vive en un turno de ``WorkersIDEAgent``
    separado, con su propia lista de ``messages`` que nunca se comparte)."""

    titulo: str
    instrucciones: str
    resultado_reportado: str


@dataclass(frozen=True, slots=True)
class ArchivosPromptTruncado:
    lista: tuple[str, ...]
    truncado: bool


def _recortar_pasos(pasos: Sequence[PasoParaRefutar]) -> tuple[list[PasoParaRefutar], bool]:
    if len(pasos) <= MAX_PASOS_EN_PROMPT:
        return list(pasos), False
    return list(pasos[:MAX_PASOS_EN_PROMPT]), True


def _recortar_archivos(archivos: Sequence[str]) -> ArchivosPromptTruncado:
    if len(archivos) <= MAX_ARCHIVOS_EN_PROMPT:
        return ArchivosPromptTruncado(tuple(archivos), False)
    return ArchivosPromptTruncado(tuple(archivos[:MAX_ARCHIVOS_EN_PROMPT]), True)


def construir_prompt(
    *, meta: str, pasos: Sequence[PasoParaRefutar], archivos_tocados: Sequence[str]
) -> str:
    """Arma el prompt adversarial. Lanza ``ValueError`` sin ``pasos`` --
    quien llama (``motivo_para_omitir``) ya debió filtrar ese caso antes."""
    if not pasos:
        raise ValueError("construir_prompt necesita al menos un paso completado para auditar.")

    pasos_recortados, pasos_truncados = _recortar_pasos(pasos)
    lista_pasos = "\n\n".join(
        f"### Paso: {p.titulo}\nEncargo: {p.instrucciones}\n"
        f"Lo que el otro agente reportó haber hecho: {p.resultado_reportado}"
        for p in pasos_recortados
    )
    if pasos_truncados:
        lista_pasos += (
            f"\n\n(se omiten {len(pasos) - len(pasos_recortados)} paso(s) adicionales por "
            "espacio; audita a fondo los de arriba.)"
        )

    archivos = _recortar_archivos(archivos_tocados)
    lista_archivos = (
        "\n".join(f"- {ruta}" for ruta in archivos.lista)
        if archivos.lista
        else "(ningún archivo quedó registrado como modificado)"
    )
    if archivos.truncado:
        lista_archivos += (
            f"\n(hay {len(archivos_tocados) - len(archivos.lista)} archivo(s) más modificados "
            "que no entran en esta lista; usa 'listar_archivos'/'buscar_en_archivos' si "
            "necesitas verlos.)"
        )

    return f"""\
Eres el REFUTADOR de un plan de ingeniería que otro agente (el "reparador") ya
declaró terminado. Tu único trabajo es intentar TUMBARLO -- no revisarlo con
amabilidad, no coincidir por cortesía. Un veredicto de aprobación que no
resistió el intento real de tumbarlo no vale nada.

NO viste el razonamiento del reparador -- a propósito, para que tu revisión
sea independiente de la suya. Solo tienes lo que sigue: el encargo original,
lo que el reparador DICE que hizo, y acceso directo al mismo workspace.
Trátalo como una AFIRMACIÓN por comprobar, nunca como un hecho.

Meta del plan completo: {meta}

{lista_pasos}

Archivos que quedaron registrados como modificados en este plan:
{lista_archivos}

Reglas de esta auditoría (no son sugerencias):

1. MIDE, no opines. Un código de salida en 0, un "listo", un "generado con
   éxito" NO son prueba de nada por sí solos. Esta semana, en este mismo
   proyecto, un HTTP 200 devolvió: un cuadrado azul de 7 KB reportado como
   "imagen generada", un WAV de 0,5 segundos de silencio absoluto, una
   captura de pantalla de 0 bytes con las dimensiones correctas en la
   cabecera, un "open_app" que mentía sobre haber abierto algo, un motor de
   seguridad decapitado en silencio, y un conteo de tests inventado ("8
   tests pasan" cuando el propio log decía 7). En todos esos casos el paso
   "funcionó" según su propio reporte. Tu trabajo es no dejarte pasar eso.
2. ABRE el resultado. Si el encargo dice "genera/edita un archivo", usa
   'leer_archivo' y mira el contenido real -- no el nombre, no solo el
   tamaño en bytes. Si es binario (imagen, audio) usa 'ejecutar_comando'
   para inspeccionarlo de verdad (tamaño real, hash, dimensiones, duración)
   en vez de asumir por la extensión o el tamaño del archivo.
3. CORRE el comando de verificación TÚ MISMO con 'verificar' (o
   'ejecutar_comando' si hace falta algo más específico). Que el reparador
   diga que corrió los tests no significa que tú no tengas que correrlos de
   nuevo, ahora mismo, con tus propias herramientas.
4. Si el encargo pedía un efecto observable (un archivo que debía existir,
   un comportamiento que debía cambiar, un test que debía pasar), confirma
   que existe/pasa DE VERDAD antes de aprobar cualquier parte de él.
5. Ante la duda: NO_DEMOSTRADO, nunca APROBADO. Aprobar es la excepción, no
   el default -- solo lo haces cuando de verdad comprobaste algo con tus
   propias herramientas y no encontraste nada que lo contradiga. Un
   veredicto de APROBADO sin haber usado ninguna herramienta de
   verificación se descarta automáticamente, así que aprobar sin comprobar
   no ahorra nada.
6. No arregles nada. No uses 'escribir_archivo' ni 'editar_archivo': eres un
   auditor, no un segundo reparador. Si encuentras algo roto, repórtalo con
   precisión -- no lo corrijas tú.

Termina tu respuesta con una línea EXACTA, sola, con uno de estos tres
valores (nada más en esa línea, sin comillas ni texto adicional):

VEREDICTO: APROBADO
VEREDICTO: REFUTADO
VEREDICTO: NO_DEMOSTRADO

Antes de esa línea, en un párrafo corto, di QUÉ comprobaste con tus propias
herramientas (no lo que el reparador dijo) y por qué ese veredicto es el que
corresponde. Si refutas o marcas no demostrado, sé específico: qué mirabas,
qué esperabas encontrar y qué encontraste en su lugar.
"""


_VEREDICTO_RE = re.compile(r"VEREDICTO:\s*(APROBADO|REFUTADO|NO_DEMOSTRADO)", re.IGNORECASE)

Verdict = Literal["aprobado", "refutado", "no_demostrado"]


def parsear_veredicto(texto: str) -> Verdict:
    """Extrae el veredicto del texto final del refutador.

    Si no encuentra la línea exacta que se le pidió, o encuentra valores
    contradictorios (más de un valor distinto en el mismo texto), el
    resultado es "no_demostrado" -- la misma regla dura que el prompt le
    pide al modelo se le aplica también a este parseo: ante la duda, no
    demostrado, nunca aprobado.
    """
    hallazgos = {m.group(1).upper() for m in _VEREDICTO_RE.finditer(texto or "")}
    if len(hallazgos) != 1:
        return "no_demostrado"
    valor = hallazgos.pop()
    if valor == "APROBADO":
        return "aprobado"
    if valor == "REFUTADO":
        return "refutado"
    return "no_demostrado"


def _texto_sin_veredicto(texto: str) -> str:
    return _VEREDICTO_RE.sub("", texto or "").strip()


@dataclass(frozen=True, slots=True)
class VeredictoRefutador:
    """El resultado completo de una corrida del refutador, ya evaluado.

    ``verdict_bruto`` es lo que el texto del modelo decía; ``verdict`` (la
    property) es lo que el resto del sistema debe usar -- puede degradar un
    "aprobado" sin evidencia, ver el docstring del módulo.
    """

    verdict_bruto: Verdict
    texto: str
    modelo: str
    herramientas_usadas: tuple[str, ...]
    archivos_auditados: tuple[str, ...]

    @staticmethod
    def desde_respuesta(
        texto_bruto: str,
        *,
        modelo: str,
        herramientas_usadas: Sequence[str],
        archivos_auditados: Sequence[str],
    ) -> VeredictoRefutador:
        return VeredictoRefutador(
            verdict_bruto=parsear_veredicto(texto_bruto),
            texto=_texto_sin_veredicto(texto_bruto)[:2000],
            modelo=modelo,
            herramientas_usadas=tuple(herramientas_usadas),
            archivos_auditados=tuple(archivos_auditados),
        )

    @property
    def tuvo_evidencia(self) -> bool:
        return any(h in HERRAMIENTAS_DE_EVIDENCIA for h in self.herramientas_usadas)

    @property
    def uso_herramientas_prohibidas(self) -> bool:
        return any(h in HERRAMIENTAS_PROHIBIDAS for h in self.herramientas_usadas)

    @property
    def verdict(self) -> Verdict:
        """El veredicto EFECTIVO. Un "aprobado" sin ninguna herramienta de
        evidencia detrás se degrada aquí -- ver el docstring del módulo."""
        if self.verdict_bruto == "aprobado" and not self.tuvo_evidencia:
            return "no_demostrado"
        return self.verdict_bruto

    @property
    def degradado_por_falta_de_evidencia(self) -> bool:
        return self.verdict_bruto == "aprobado" and self.verdict == "no_demostrado"

    def bloque_para_persona(self) -> str:
        """Texto listo para sumarse al ``assistant_final`` del plan -- el
        encargo pide explícitamente que si el refutador tumba algo, no puede
        quedar enterrado. Se pega al MISMO mensaje final que ya lee la
        persona, no a un evento aparte que un cliente podría no mostrar."""
        etiqueta = {
            "aprobado": "El refutador revisó el trabajo con sus propias herramientas y no "
            "encontró nada que lo contradiga.",
            "refutado": "El refutador REVISÓ EL TRABAJO Y LO TUMBÓ.",
            "no_demostrado": "El refutador no pudo confirmar el trabajo (no demostrado).",
        }[self.verdict]
        icono = {"aprobado": "✅", "refutado": "⛔", "no_demostrado": "⚠️"}[self.verdict]
        partes = [f"\n\n---\n**Auditoría independiente ({self.modelo}):** {icono} {etiqueta}"]
        if self.degradado_por_falta_de_evidencia:
            partes.append(
                "(degradado de APROBADO a NO_DEMOSTRADO: no reportó haber usado ninguna "
                "herramienta de verificación real -- un veredicto sin evidencia no cuenta.)"
            )
        if self.uso_herramientas_prohibidas:
            partes.append(
                "(aviso: el refutador usó herramientas de escritura, que no debía usar como "
                "auditor -- revisa sus cambios con cuidado antes de confiar en el resto.)"
            )
        if self.texto:
            partes.append(self.texto)
        return "\n".join(partes)
