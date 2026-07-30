"""Un post de LinkedIn completo en UNA sola llamada: investigar, escribir y auditar, en código.

**El problema que resuelve.** Las piezas para escribir un buen post ya existían todas
(`investigacion.titulares_frescos`, `editorial.revisar`, `auditoria`, `agenda`), pero quien
las encadenaba era el modelo del chat: tenía que llamar `investigar_noticias_frescas`,
RECORDAR los titulares que le devolvieron, escribir el post él mismo, y volver a llamar
`crear_contenido_social` pasando texto Y fuentes. Cuatro pasos con un traspaso de datos en
el medio, y ahí es donde se cae. El síntoma exacto que reportó un usuario fue el propio
modelo avisando que había perdido la pelota en el paso tres: *"El investigador no devolvió
los datos concretos (títulos, URLs, snippets). No puedo pasarlos al creador de post."*

**Lo que cambia.** Este módulo hace ese ciclo entero en código. El modelo solo escribe prosa
-- que es lo único que hace bien de forma fiable -- y no orquesta nada: no decide cuándo
investigar, no transporta fuentes entre llamadas, no decide si auditar. Todo eso pasa aquí,
siempre, en el mismo orden:

1. **Fuente primero, y nunca se escribe al aire.** `_reunir_contexto` busca un titular
   fresco y verificado por fecha; si no hay, amplía la ventana una vez; si sigue sin haber,
   cae al banco de contexto real del tenant (hechos que él mismo configuró) y el post pasa a
   ser criterio sin ningún evento fechable. Si no hay ninguna de las dos cosas y el usuario
   PIDIÓ el tema, se escribe tesis pura (cero eventos, cero cifras) en vez de dejarlo sin
   nada; en el post recurrente, ahí sí se salta el turno y se devuelve qué hacer para que la
   próxima vez sí se pueda.
2. **El escritor recibe todo junto**: la fuente, el perfil editorial del destino correcto, la
   forma argumental que toca en este turno y los temas recientes que no debe repetir.
3. **Los controles corren siempre**, no cuando el modelo se acuerde, y en el orden del motor
   que sí funciona a diario (`aria/features/linkedin_content.py::_finalizar_post`):
   **primero los que REESCRIBEN, al final los que juzgan.**

   a. `auditoria.pulir_borrador` -- el editor jefe REESCRIBE texto, tema, ángulo y copy
      visual, y remata quitando los delatores de plantilla IA.
   b. `auditoria.auditar_hechos` -- el auditor anti-invención reescribe lo que la fuente no
      sostenga, o veta.
   c. `auditoria.reescribir_sin_primera_persona` -- repara la autobiografía que la
      reescritura del auditor haya reintroducido, y RE-AUDITA el resultado (la lección
      crítica de `auditoria.py`).
   d. `editorial.revisar` -- los gates deterministas, AL FINAL y blandos cuando el usuario
      pidió el tema: ahí sólo la primera persona y la longitud mínima siguen siendo duras.

   Este orden es lo contrario del que tenía este archivo, y ése era el fallo raíz: la batería
   determinista era la PRIMERA puerta y corría sobre el texto crudo del escritor, así que ese
   texto tenía que salir perfecto de una sola pasada contra doce reglas a la vez, con el
   modelo más débil de la cadena y sin que ningún editor lo hubiera tocado. Lo que la persona
   veía era "escribí el post N veces y ninguna versión pasó el control de calidad".

   Un rechazo no tira el turno: se reintenta con el problema concreto en el prompt y rotando
   de fuente. Los controles ven TODO lo que el escritor tenía permitido usar -- titulares y
   banco de contexto --, pero al manifiesto solo sale lo citable: verificable y publicable son
   dos cosas distintas, y confundirlas rompía el paso 1 (ver `_material_para_auditar`).

**Multi-tenant de verdad.** Nada de lo específico de una cuenta vive en este archivo: los
pilares, la voz, las marcas y los hechos verificables salen del perfil editorial que el
usuario configuró (`configurar_perfil_social`) y de `agenda.py`. Aquí solo vive el
mecanismo.

`crear_contenido_social` sigue existiendo y no cambia: es la puerta para cuando el usuario ya
dictó el texto y solo hay que empaquetarlo. Esta herramienta es para "escríbeme un post sobre
X", que es el camino que hoy se rompe.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx
from edecan_core import Tool, ToolContext, ToolResult
from edecan_llm.base import ChatMessage, CompletionRequest

from . import agenda
from ._files import Uploader, subir_archivo
from .auditoria import (
    _extraer_json,
    auditar_hechos,
    pulir_borrador,
    reescribir_sin_primera_persona,
)
from .editorial import (
    REGLAS_LINKEDIN,
    _tiene_primera_persona,
    firmas_prohibidas_recientes,
    instruccion_cooldown_estructura,
    normalizar,
    normalizar_visual,
    revisar,
    revisar_visual,
)
from .investigacion import _acotar, titulares_frescos
from .marcas import DESTINATION_ID_PATTERN, is_valid_destination_id
from .providers import ImageProvider
from .social import (
    PLATFORMS,
    _contexto_calidad,
    _decidir_destino,
    _llamar_modelo_editorial,
    _tenant_flags,
    build_context_bank_prompt_block,
    context_bank_text,
    empaquetar_borrador_social,
    get_agenda_state,
    get_editorial_profile_for_destination,
    save_agenda_state,
)
from .tools import _cap_str

logger = logging.getLogger(__name__)

__all__ = ["ContextoDeEscritura", "CrearPostLinkedInTool"]

_PLATAFORMA = "linkedin"

# Alias de escritura: el mismo criterio que ya usa el Studio creativo para esta tarea
# (`content_studio.create_social_content`). Escribir prosa publicable es el trabajo caro del
# ciclo; auditarla corre por "principal" (ver `social._llamar_modelo_editorial`) y la crítica
# de imagen por "rapido". Cada paso al modelo que le corresponde, no el más grande a todo.
_ALIAS_ESCRITURA = "profundo"
# Alias de respaldo cuando el de escritura no responde. Es el equivalente adaptado de la
# lista de modelos de Aria (`_LC_MODELOS`: Opus primero, Sonnet de red de seguridad,
# `linkedin_content.py:620-632`): un fallo de TRANSPORTE no puede leerse como un fallo
# editorial. Sin esto, un timeout de la red se le reportaba al usuario como "el post no pasó
# el control de calidad" -- que es mentira, y lo mandaba a depurar el pipeline editorial
# cuando el problema había sido la conexión.
_ALIAS_ESCRITURA_RESPALDO = "principal"
# Dos reintentos cortos, no las 6 rondas x 5 minutos de Aria: allá el motor es un cron y
# puede esperar media hora; acá hay una persona mirando el chat.
_REINTENTOS_ESCRITURA = 2
# 3000, no 1600, porque la salida no es solo el post: también trae `tema`,
# `fuente_principal`, `titular_visual` y `alt_text` en el mismo JSON. Con 1600
# el modelo alcanzaba a escribir la mayoría del post y el JSON quedaba
# truncado a mitad de `texto`, sin `}` de cierre; `_extraer_json` devolvía
# `None` y el motor reintentaba dos veces con el mismo tema, terminando en
# "No devolviste el JSON pedido" -- que le llegaba al usuario como que el
# motor entero está roto, cuando en realidad estaba escribiendo bien pero le
# faltaba espacio para cerrar el sobre. Un post LinkedIn largo + los campos
# accesorios entra cómodo en 3000.
_MAX_TOKENS_ESCRITURA = 3000
_TEMPERATURA_ESCRITURA = 0.6

_MAX_TEMA_CHARS = 300
_MAX_TITULAR_CHARS = 180
_MAX_ALT_CHARS = 1_000
_MAX_FUENTES = 6
_MAX_DIAS_DEFECTO = 3

# Segunda pasada de búsqueda cuando la ventana pedida no devolvió nada. Un hecho de hace diez
# días sigue sirviendo para un post de criterio (lo que no sirve es presentarlo como "hoy", y
# de eso se encarga el auditor); rendirse en el primer intento manda al usuario a escribir sin
# fuente, que es justo lo que este módulo existe para impedir.
_VENTANA_AMPLIA_DIAS = 14

# Cuántas veces se reescribe el post cuando un control lo rechaza. El reintento con el
# problema concreto en el prompt corrige la mayoría de los rechazos (una violación de
# estilo puntual, un dato que la fuente no sostiene).
#
# Eran DOS y no alcanzaba: con un escritor que insiste en primera persona —el fallo más
# común— los dos intentos morían en la MISMA puerta determinista y el turno terminaba sin
# post. Tres da margen a que la corrección entre sin volverse un gasto absurdo de tokens;
# el rescate de `_escribir_con_controles` cubre el caso de que aun así fallen todos.
_MAX_INTENTOS = 3

# Cuántos temas recientes se le muestran al escritor para que no repita. La queja real que
# originó esto en el motor del que se portó la forma fue "siempre es casi lo mismo".
_TEMAS_RECIENTES_EN_PROMPT = 12

# Longitud mínima del texto que se ENTREGA, incluido el camino de rescate. Aria lo mantiene
# duro incluso en permisivo (`linkedin_content.py:1610`: `len(texto) < 80` descarta). Antes,
# `_texto_util` solo comprobaba que `normalizar()` no devolviera vacío, así que un rescate de
# dos líneas se le entregaba al usuario como borrador.
_MIN_TEXTO_ENTREGABLE_CHARS = 80

# Cuántos borradores anteriores ve el editor jefe, con su TEXTO real y no solo el tema. Port
# de `_borradores_recientes_contexto` (Aria, `linkedin_content.py:293-307`): con la lista de
# temas solamente, un post con tema nuevo y molde idéntico pasaba limpio.
_BORRADORES_RECIENTES_EN_PROMPT = 6
_MAX_BORRADOR_RECIENTE_CHARS = 700

_ORIGEN_FRESCO = "noticias_frescas"
_ORIGEN_AMPLIADO = "noticias_ventana_ampliada"
_ORIGEN_BANCO = "banco_de_contexto"
_ORIGEN_TESIS = "tesis_sin_fuente"

_CAMPOS_PERFIL_EN_PROMPT: tuple[tuple[str, str], ...] = (
    ("purpose", "Objetivo de la cuenta"),
    ("audience", "Audiencia"),
    ("voice", "Voz"),
    ("content_pillars", "Pilares de contenido"),
    ("preferred_formats", "Formatos preferidos"),
    ("calls_to_action", "Llamadas a la acción"),
    ("avoid", "Prohibido para esta cuenta"),
    ("notes", "Notas del usuario"),
)
_MAX_CAMPO_PERFIL_CHARS = 700


@dataclass(frozen=True)
class ContextoDeEscritura:
    """Sobre qué se escribe este post, y de dónde salió.

    Los dos primeros campos son material de respaldo, pero NO son lo mismo y por eso viajan
    separados (la distinción que arregló el defecto que documenta `_material_para_auditar`):

    - `citables`: titulares reales con URL. Son los únicos que pueden publicarse -- van a
      `sources`, que se escribe al manifiesto y a la card que ve el usuario.
    - `respaldo`: el banco de contexto real de la cuenta. Es verificable pero PRIVADO
      (identidad, operación, cicatrices del dueño), así que sirve para auditar y nunca para
      citar. Viaja siempre que el perfil lo tenga configurado, haya noticia o no, porque el
      escritor siempre lo recibe en su prompt.

    `bloque` es el texto ya listo para el prompt del escritor: las fuentes numeradas, o la
    instrucción explícita de escribir sin ningún evento fechable. `origen` queda para poder
    decirle al usuario, sin adivinar, si su post se apoya en una noticia de esta semana o en
    su propio banco de contexto.
    """

    citables: list[dict[str, str]]
    respaldo: str
    bloque: str
    origen: str


def _fuentes_de_titulares(titulares: Any) -> list[dict[str, str]]:
    return [
        {
            "title": " ".join(titular.titulo.split())[:240],
            "url": titular.url,
            "snippet": " ".join(titular.snippet.split())[:600],
        }
        for titular in titulares
    ]


def _bloque_fuentes(fuentes: list[dict[str, str]]) -> str:
    """Numera las fuentes como [F1], [F2]... -- mismo etiquetado que usa
    `auditoria._formatear_fuentes`, para que el id que elija el escritor
    (`fuente_principal`) apunte a la misma fuente que después audita el auditor."""
    lineas = [
        f"[F{indice}] {fuente['title']} :: {fuente['snippet']} :: {fuente['url']}"
        for indice, fuente in enumerate(fuentes, start=1)
    ]
    return (
        "FUENTES REALES Y VERIFICADAS POR FECHA (tu ÚNICA fuente de hechos; cero cifras, "
        "fechas, nombres o desenlaces que no estén literalmente aquí):\n" + "\n".join(lineas)
    )


_SIN_EVENTO_FECHABLE = (
    "SIN FUENTES FECHABLES: no hay ninguna noticia reciente y verificable sobre este tema, así "
    "que el post NO puede mencionar ningún evento, lanzamiento, cifra, fecha ni actor concreto "
    "como si fuera actualidad -- tu memoria es vieja por definición y no cuenta como fuente. "
    "Escribe una pieza de criterio que se sostenga sola: un mecanismo, una restricción o una "
    "decisión explicada, apoyada únicamente en el banco de contexto de arriba."
)


_TESIS_PURA = (
    "SIN FUENTES Y SIN BANCO: no hay ninguna noticia reciente verificable sobre este tema y "
    "esta cuenta no tiene banco de contexto configurado. Escribe TESIS PURA: cero eventos "
    "concretos, cero cifras, cero fechas, cero nombres de productos o empresas presentados "
    "como actualidad -- tu memoria es vieja por definición y no cuenta como fuente. Lo que sí "
    "puedes escribir es criterio que se sostenga solo: un mecanismo, una restricción, un "
    "incentivo o un marco de decisión explicado con precisión, formulado como razonamiento "
    "general y no como reporte de algo que pasó."
)


async def _reunir_contexto(
    perfil: dict[str, Any],
    consulta: str,
    *,
    max_dias: int,
    excluir_terms: tuple[str, ...] = (),
    permisivo: bool = False,
) -> ContextoDeEscritura | None:
    """La red de seguridad: baja de rung en rung hasta encontrar algo REAL sobre qué escribir.

    Devuelve `None` solo cuando no queda nada verificable -- y ese `None` significa "no
    escribas", nunca "improvisa". Esa es la regla que sostiene toda la cuenta: un post con un
    hecho inventado cuesta más credibilidad de la que gana un turno cumplido.
    """
    # El banco acompaña a CUALQUIER escalón, no solo al último: el escritor lo recibe en su
    # prompt siempre (ver `run`), así que el auditor tiene que poder comprobar contra él
    # siempre. Un respaldo que el escritor puede usar y el auditor no puede ver es una
    # invención garantizada a ojos del auditor.
    respaldo = context_bank_text(perfil)

    ventanas = [max_dias]
    if max_dias < _VENTANA_AMPLIA_DIAS:
        ventanas.append(_VENTANA_AMPLIA_DIAS)

    for indice, dias in enumerate(ventanas):
        try:
            async with httpx.AsyncClient() as http:
                titulares = await titulares_frescos(
                    http,
                    consulta,
                    maximo=_MAX_FUENTES,
                    max_dias=dias,
                    excluir_terms=excluir_terms,
                )
        except Exception:
            logger.warning(
                "Falló la búsqueda de titulares para %r; se sigue bajando por la red de "
                "seguridad en vez de escribir sin fuente.",
                consulta,
                exc_info=True,
            )
            titulares = []
        if titulares:
            citables = _fuentes_de_titulares(titulares)
            return ContextoDeEscritura(
                citables=citables,
                respaldo=respaldo,
                bloque=_bloque_fuentes(citables),
                origen=_ORIGEN_FRESCO if indice == 0 else _ORIGEN_AMPLIADO,
            )

    # Sin noticia: queda el banco de contexto real que configuró el propio usuario
    # (`configurar_perfil_social`), que ya viaja en el prompt de todas formas. Lo que cambia
    # aquí es que pasa a ser el ÚNICO respaldo, así que el post no puede traer ningún evento
    # fechable. Sin nada citable, `citables` queda vacío -- y ese vacío es exactamente lo que
    # el manifiesto debe mostrar: este post no cita a nadie.
    if respaldo:
        return ContextoDeEscritura(
            citables=[], respaldo=respaldo, bloque=_SIN_EVENTO_FECHABLE, origen=_ORIGEN_BANCO
        )

    # ÚLTIMO ESCALÓN: ni noticia ni banco, pero el usuario PIDIÓ este post y está esperando.
    # Aria, en esa misma situación y en la misma ruta (tema pedido, `_escribir_sobre_tema`,
    # `linkedin_content.py:2251-2257`), no se rinde: degrada a TESIS PURA -- "cero eventos
    # concretos, cero cifras" -- y entrega. Antes, aquí se devolvía `None` y el usuario recibía
    # "No escribí nada" en segundos, sin que el escritor se hubiera llamado una sola vez, que
    # es lo que suena a que el motor no lo intentó. El auditor ya sabe manejar este modo (su
    # marcador SIN FUENTES), así que la pieza sigue sin poder afirmar ningún hecho fechable:
    # lo que cambia es que sí hay post.
    #
    # Sólo con tema pedido. En el post recurrente ("el post de hoy") sí vale saltarse el turno:
    # nadie está esperando y una tesis al aire sobre un territorio de rotación no aporta nada.
    if permisivo:
        return ContextoDeEscritura(
            citables=[], respaldo="", bloque=_TESIS_PURA, origen=_ORIGEN_TESIS
        )
    return None


# Prioridad de rescate: cuanto más lejos llegó un borrador en la cadena, mejor candidato es.
# El defecto que esto arregla: el texto que pasó el editor jefe Y el auditor y sólo falló el
# re-gate final se descartaba sin guardarse, aunque era el mejor candidato que existió en toda
# la corrida, y el usuario podía terminar recibiendo el crudo.
_PRIORIDAD_CRUDO = 0  # el texto tal como salió del escritor (o con primera persona al final)
_PRIORIDAD_PULIDO = 1  # pasó el editor jefe; el auditor lo vetó o no llegó a auditarse
_PRIORIDAD_CASI = 2  # pasó el editor jefe Y el auditor; sólo cayó en el re-gate final


@dataclass(frozen=True)
class _Candidato:
    prioridad: int
    borrador: dict[str, Any]
    problemas: list[str]


def _mejor_candidato(candidatos: list[_Candidato]) -> _Candidato | None:
    """El borrador más avanzado con texto entregable, o `None` si no hay ninguno.

    `_MIN_TEXTO_ENTREGABLE_CHARS` es el mismo mínimo duro que Aria mantiene incluso en
    permisivo: un rescate de dos líneas no es un borrador.
    """
    utiles = [
        candidato
        for candidato in candidatos
        if len(normalizar(str(candidato.borrador.get("texto") or ""), _PLATAFORMA))
        >= _MIN_TEXTO_ENTREGABLE_CHARS
    ]
    if not utiles:
        return None
    # Ante empate gana el ÚLTIMO, que es el intento más reciente y por lo tanto el que ya
    # incorporó las correcciones de los anteriores (de ahí el `reversed`: `max` devuelve el
    # primero de los máximos).
    return max(reversed(utiles), key=lambda candidato: candidato.prioridad)


def _fuente_sugerida(citables: list[dict[str, str]], intento: int) -> str:
    """Qué fuente proponerle al escritor en este intento, rotando entre las disponibles.

    Adaptación de la rotación de fuente/agenda de Aria (`news_idx++`,
    `linkedin_content.py:1973-1986`). El problema real: cuando la fuente es la que no da -- un
    snippet que no sostiene nada --, los tres intentos de Edecán morían por lo mismo, porque el
    bucle reintentaba con la MISMA fuente y el mismo tema, agregando sólo `correcciones`. Con
    un tema pedido no se puede cambiar de tema, pero sí de fuente. Es una sugerencia, no una
    orden: el escritor sigue eligiendo `fuente_principal`.
    """
    if len(citables) < 2 or intento <= 0:
        return ""
    return f"F{(intento % len(citables)) + 1}"


def _bloque_borradores_recientes(historial: list[dict[str, Any]]) -> str:
    """El TEXTO real de los últimos posts, para que el editor jefe detecte clones de molde.

    Port de `_borradores_recientes_contexto` (Aria, `linkedin_content.py:293-307`). El
    bloque de `_bloque_temas_recientes` solo lleva TEMAS, así que un post con tema nuevo y
    arquitectura idéntica pasaba limpio. `empaquetar_borrador_social` guarda el copy en el
    historial de agenda precisamente para esto.
    """
    bloques: list[str] = []
    for entrada in reversed(historial or []):
        if not isinstance(entrada, dict):
            continue
        texto = " ".join(str(entrada.get("copy") or "").split())
        if not texto:
            continue
        tema = str(entrada.get("tema") or "sin tema")
        bloques.append(f"- {tema}: {texto[:_MAX_BORRADOR_RECIENTE_CHARS]}")
        if len(bloques) >= _BORRADORES_RECIENTES_EN_PROMPT:
            break
    return "\n".join(bloques)


def _bloque_perfil(perfil: dict[str, Any]) -> str:
    """El perfil editorial del DESTINO, compactado para el prompt.

    Sin perfil configurado no se inventa una audiencia ni una voz: se dice explícitamente que
    no la hay, que es lo que evita que el modelo se fabrique una personalidad de marca.
    """
    if not perfil.get("configured"):
        return (
            "PERFIL EDITORIAL: esta cuenta todavía no configuró objetivo, audiencia ni voz. "
            "Escribe neutral y útil, y NO inventes audiencia, experiencia, clientes ni "
            "identidad de marca."
        )
    partes: list[str] = ["PERFIL EDITORIAL DE ESTA CUENTA (respétalo):"]
    for campo, etiqueta in _CAMPOS_PERFIL_EN_PROMPT:
        valor = perfil.get(campo)
        if isinstance(valor, list):
            valor = ", ".join(str(item) for item in valor if str(item).strip())
        texto = str(valor or "").strip()
        if texto:
            partes.append(f"- {etiqueta}: {texto[:_MAX_CAMPO_PERFIL_CHARS]}")
    return "\n".join(partes)


def _bloque_temas_recientes(historial: list[dict[str, Any]]) -> str:
    """Los últimos temas publicados, para que el escritor busque otra cosa.

    Sin esto el feed converge: el mismo tema con otro disfraz, turno tras turno. El cooldown
    de `agenda` ya bloquea los temas saturados en la BÚSQUEDA; esto ataca lo otro, que el
    ángulo se repita aunque la fuente sea nueva.
    """
    temas = [
        str(entrada.get("tema") or "").strip()
        for entrada in historial[-_TEMAS_RECIENTES_EN_PROMPT:]
        if isinstance(entrada, dict) and str(entrada.get("tema") or "").strip()
    ]
    if not temas:
        return ""
    return (
        "\n\nTEMAS RECIENTES DE ESTA CUENTA QUE NO DEBES REPETIR (ni con otro disfraz: busca "
        "una situación y un ángulo claramente distintos): " + "; ".join(temas)
    )


_SISTEMA_ESCRITOR = (
    "Eres el redactor editorial de una cuenta profesional de LinkedIn. Escribes un post "
    "verdadero, específico y listo para que una persona lo revise antes de publicarlo. No "
    "publicas nada, no dices que publicaste nada y no inventas ningún hecho.\n\n"
    + REGLAS_LINKEDIN
    + "\n\nLO ÚNICO QUE UN VALIDADOR DETERMINISTA RECHAZA SIN PIEDAD, cuídalo antes de "
    "entregar: CERO primera persona -- ni yo, mi, nosotros, ni verbos como hice, aprendí, "
    "decidí, construí, creo, vi; afirma sobre el mundo, no sobre el autor.\n\n"
    "El cierre completa la idea con una decisión o una consecuencia concreta. Puede ser una "
    "sola pregunta discutible y específica cuando nazca del tema, pero nunca una pregunta "
    "puesta para pedir comentarios ni fórmulas tipo 'la pregunta que queda es' o "
    "'¿deberías...?'.\n\n"
    "SALIDA (obligatorio): responde SOLO con un objeto JSON válido, sin markdown ni "
    "explicación, empezando por la llave {:\n"
    '{"tema": "<2 a 6 palabras que resuman el post>", '
    '"fuente_principal": "<el id exacto de la ÚNICA fuente que sostiene el post: F1, F2...; '
    'cadena vacía si no recibiste fuentes>", '
    # Campos de DISCIPLINA, portados de `_SALIDA_JSON` de Aria
    # (`linkedin_content.py:494-525`). Obligan al escritor a declarar el ángulo y a quién le
    # importa ANTES de escribir, y le dan al editor jefe con qué juzgar. El defecto que
    # arreglan es que a este escritor se le exigía en la SALIDA (el editor lo vetaba por
    # "genérico, sin ángulo") algo que nunca se le pidió en la ENTRADA.
    '"angulo": "<la idea central concreta del post en una frase: qué se afirma, no de qué se '
    'habla>", '
    '"promesa_lector": "<por qué le importa a alguien ajeno al tema, en una frase, sin '
    'jerga>", '
    '"texto": "<el post completo, tal cual se publicaría. Entre 90 y 170 palabras '
    "en 2 o 3 párrafos separados por una línea en blanco. La PRIMERA línea es un "
    "gancho concreto que da intriga y hace querer seguir leyendo (un hecho, una "
    "tensión, una cifra sorprendente), NUNCA un resumen del titular. El cuerpo "
    "desarrolla UNA idea con criterio, y el cierre deja una pregunta o una "
    "consecuencia que invite a pensar. Un post de tres frases sueltas se siente "
    'vacío: dale desarrollo y ritmo, como escribiría una persona con oficio>", '
    '"titular_visual": "<3 a 8 palabras, máximo 58 caracteres, que expresen el hecho o la '
    "decisión concreta y se entiendan SIN leer el post; nada de metáforas ni promesas de "
    'revelación>", '
    # Campos editoriales para la imagen — mismo formato que usa el motor de LinkedIn de
    # Aria (`features/linkedin_content.py::_normalizar_visual`), pensado para que un
    # compositor tipográfico monte el texto ENCIMA de la foto: KICKER es la categoría en
    # mayúsculas, HEADLINE es el titular grande, ACCENT es la subcadena del headline que
    # se destaca (típicamente lo más concreto: un nombre, una cifra, una decisión), y
    # SUPPORT es un subtítulo opcional que da contexto. Aunque el compositor con Playwright
    # todavía no está portado, pedirlos ya deja los datos listos: cuando llegue, no hay
    # que volver a molestar al escritor -- los borradores viejos ya los tienen.
    '"visual": {'
    '"kicker": "<2 a 4 palabras en MAYÚSCULAS que digan la categoría, ej: LA FED · MERCADOS, '
    'IA · PRIVACIDAD, LATINOAMÉRICA · REGULACIÓN>", '
    '"headline": "<3 a 8 palabras, máximo 58 caracteres, el titular editorial concreto — '
    'debe entenderse SIN leer el post>", '
    '"accent": "<la subcadena EXACTA del headline que quieres destacar visualmente (un '
    'nombre, una cifra, un verbo de acción); cadena vacía si no hay una parte que resalte '
    'sobre el resto>", '
    '"support": "<1 frase corta (máximo 12 palabras) que AÑADE un dato NUEVO: una '
    "consecuencia, una cifra, un matiz que el headline no dice. NUNCA repite ni "
    "parafrasea el headline ni el kicker con otras palabras. Si lo único que se te "
    'ocurre es repetir el titular, deja esto en cadena vacía>"'
    "}, "
    '"alt_text": "<descripción accesible de la imagen que acompañaría al post>"}'
    # Instrucción anti-titular-poético, con el fallo real de Aria metido dentro del prompt
    # (`linkedin_content.py:508-518`). El titular es lo único que se lee en miniatura, y
    # `titular_visual`/`visual.headline` van montados sobre la foto: un titular que necesita
    # el post para entenderse no comunica nada a quien pasa el scroll.
    "\n\nPRUEBA DEL TITULAR (aplica a 'titular_visual' y a 'visual.headline'): si el titular "
    "necesita el post para entenderse, escríbelo de nuevo. PROHIBIDAS las metáforas: 'no le "
    "pelea al agua, se deja levantar' llegó a salir montado sobre una imagen real y no "
    "significaba nada para quien la veía. PROHIBIDO el guion largo dentro de la imagen."
)


async def _escribir(
    ctx: ToolContext,
    *,
    encargo: str,
    instruccion_formato: str,
    instruccion_cooldown: str,
    temas_recientes: str,
    bloque_perfil: str,
    bloque_banco: str,
    contexto: ContextoDeEscritura,
    correcciones: list[str],
    fuente_sugerida: str = "",
) -> dict[str, Any] | None:
    """Una pasada del escritor. `correcciones` (vacío en el primer intento) trae el problema
    EXACTO que rechazó el intento anterior: decirle "corrige esto" con el motivo concreto
    funciona; volver a pedir lo mismo tal cual solo repite el mismo error."""
    correccion = ""
    if correcciones:
        detalle = "\n".join(f"- {problema}" for problema in correcciones)
        correccion = (
            f"\n\nCORRIGE: tu intento anterior fue rechazado por esto:\n{detalle}\n"
            "Reescribe el post COMPLETO resolviendo cada punto, sin perder el tema."
        )
    if fuente_sugerida:
        # Rotar de fuente entre intentos (ver `_fuente_sugerida`): si el intento anterior murió
        # porque el material no sostenía nada, insistir con la misma fuente lo mata igual.
        correccion += (
            f"\n\nCAMBIA DE FUENTE: el intento anterior se apoyó en otra y no funcionó. Usa "
            f"{fuente_sugerida} como fuente principal si sostiene un ángulo concreto; si no, "
            "elige cualquier otra distinta de la anterior."
        )

    usuario = (
        f"{encargo}\n"
        f"{instruccion_formato}"
        f"{instruccion_cooldown}"
        f"{temas_recientes}\n\n"
        f"{bloque_perfil}"
        f"{bloque_banco}\n\n"
        f"{contexto.bloque}"
        f"{correccion}"
    )

    # Reintento de TRANSPORTE, separado del reintento editorial. Adaptación de
    # `_max_con_reintento` (Aria, `linkedin_content.py:635-687`): allá son 6 rondas x 2
    # modelos con 5 minutos entre rondas porque el motor es un cron; acá son dos intentos
    # cortos y un alias de respaldo porque hay alguien esperando en el chat. Lo que se porta
    # es la SEPARACIÓN: antes, cualquier excepción de red consumía uno de los tres intentos
    # editoriales y el usuario terminaba leyendo "ninguna versión pasó el control de calidad"
    # por un timeout.
    crudo = ""
    for intento in range(_REINTENTOS_ESCRITURA):
        alias = _ALIAS_ESCRITURA if intento == 0 else _ALIAS_ESCRITURA_RESPALDO
        try:
            respuesta = await ctx.llm.complete(
                alias,
                _tenant_flags(ctx),
                CompletionRequest(
                    model=alias,
                    system=_SISTEMA_ESCRITOR,
                    messages=[ChatMessage(role="user", content=usuario)],
                    max_tokens=_MAX_TOKENS_ESCRITURA,
                    temperature=_TEMPERATURA_ESCRITURA,
                ),
            )
        except Exception:
            logger.warning(
                "Falló la llamada al modelo que escribe el post (alias=%s, intento %d/%d).",
                alias,
                intento + 1,
                _REINTENTOS_ESCRITURA,
                exc_info=True,
            )
            continue
        crudo = str(respuesta.text or "")
        if crudo.strip():
            break

    if not crudo.strip():
        return None
    datos = _extraer_json(crudo)
    if isinstance(datos, dict) and str(datos.get("texto") or "").strip():
        return datos
    # El sobre falló, no el contenido: acepta el post en PROSA.
    return _post_desde_prosa(crudo)


def _post_desde_prosa(crudo: str) -> dict[str, Any] | None:
    """El post en texto plano cuando el modelo no devolvió el JSON pedido.

    Port de `_post_desde_raw` (Aria, `linkedin_content.py:586-608`), que lo documenta como
    un bug real vivido: "así nunca perdemos un buen post por parsing". Con un modelo pequeño
    al que se le pide un JSON de nueve campos, uno anidado, esto pasa seguido -- y es un post
    perfectamente bueno tirado a la basura por el SOBRE, no por el contenido. Antes, esta
    situación quemaba uno de los tres intentos editoriales con la corrección "No devolviste el
    JSON pedido".

    Deriva `tema` y un `visual` tipográfico del propio texto, nunca inventando hechos: el
    kicker sale de las primeras palabras, el headline de la primera frase y el apoyo de la
    segunda.
    """
    texto = normalizar(str(crudo or "").strip().strip("`").strip().strip('"').strip(), _PLATAFORMA)
    # Un fragmento corto no es un post: probablemente sea una disculpa del modelo o una
    # pregunta de vuelta. Mejor reintentar que empaquetar eso.
    if len(texto) < _MIN_TEXTO_ENTREGABLE_CHARS:
        return None
    # Y un JSON no es prosa. Si el modelo SÍ devolvió el sobre pero con `texto` vacío o mal
    # nombrado, tomar la respuesta cruda como post publicaría llaves y comillas: un JSON largo
    # pasa el mínimo de longitud sin problema. Ante eso se reintenta, que es lo correcto.
    if texto.lstrip().startswith(("{", "[")) or '"texto"' in texto:
        return None
    frases = [f.strip() for f in re.split(r"(?<=[.!?])\s+", texto) if f.strip()]
    tema = " ".join(texto.split()[:4])
    headline = frases[0][:70] if frases else tema
    return {
        "tema": tema,
        "fuente_principal": "",
        "texto": texto,
        "titular_visual": headline,
        "visual": {
            "kicker": tema.upper()[:40],
            "headline": headline,
            "accent": "",
            "support": frases[1][:110] if len(frases) > 1 else "",
        },
        "alt_text": "",
        "_desde_prosa": True,
    }


def _fuente_elegida(fuentes: list[dict[str, str]], fuente_principal: str) -> list[dict[str, str]]:
    """La única fuente que el escritor dijo estar usando, o todas si no eligió una válida.

    Regla 13 de `REGLAS_LINKEDIN` ("una historia, una fuente"): auditar contra la fuente
    elegida es más estricto que auditar contra el montón, porque impide coser dos titulares
    independientes en una tendencia fabricada. Si el id no se entiende, se cae a todas las
    fuentes -- perder el filtro es aceptable, quedarse sin auditoría no.
    """
    identificador = str(fuente_principal or "").strip().upper()
    if not identificador.startswith("F"):
        return fuentes
    try:
        indice = int(identificador[1:])
    except ValueError:
        return fuentes
    if 1 <= indice <= len(fuentes):
        return [fuentes[indice - 1]]
    return fuentes


# Cuánto del banco alcanza a ver el auditor. `auditoria._formatear_fuentes` recorta el bloque
# entero de fuentes a 3500 caracteres y el banco va al final, así que este tope deja entrar la
# noticia completa (título + snippet + URL rondan los 900) antes de gastar el resto. Lo que se
# pierda es la COLA del banco, y una afirmación que solo esa cola sostenía se veta por no estar
# sostenida: fail-closed, igual que todo el auditor -- se pierde un turno, nunca se publica un
# invento.
_MAX_BANCO_AUDITABLE_CHARS = 2400

# El auditor solo sabe leer "fuentes", así que la regla de uso del banco tiene que viajar
# DENTRO de la fuente: sin ella, el modo con fuentes le pide "reconstruye el texto con 1 a 3
# hechos explícitos de las fuentes" y el auditor podría meter en el post una cicatriz personal
# que el escritor tuvo el cuidado de no contar.
_BANCO_AUDITABLE_TITULO = "Banco de contexto real de esta cuenta (verificable; NO citable)"
_BANCO_AUDITABLE_REGLA = (
    "Material de VERIFICACIÓN, no de publicación: sirve para comprobar lo que el texto ya "
    "afirma, nunca para agregarle datos nuevos ni para convertir estos hechos en una anécdota "
    "en primera persona."
)


def _material_para_auditar(citables: list[dict[str, str]], respaldo: str) -> list[dict[str, str]]:
    """Todo contra lo que el auditor puede comprobar: lo citable MÁS el banco de contexto.

    La regla que separa esto de `sources`: **el auditor tiene que ver TODO lo que el escritor
    tenía permitido usar; el manifiesto solo puede mostrar lo que además es publicable.** Son
    dos conjuntos distintos, y confundirlos rompe el módulo por lados opuestos:

    - Auditar con MENOS de lo que recibió el escritor es auditar a ciegas. El banco viaja
      siempre en el prompt de escritura, así que un hecho salido de ahí le llega al auditor
      sin respaldo visible y lo veta como invención. En el escalón sin noticia era todavía
      peor: `auditar_hechos` entraba en su modo SIN FUENTES, cuyo prompt ordena eliminar
      cifras, marcas y afirmaciones sobre cómo actúa una industria -- justo el material del
      banco --, y el editor jefe leía "sin fuentes citables" como la regla dura que lo
      autoriza a rechazar. La red de seguridad que existe para no escribir nunca sin respaldo
      terminaba vaciando o vetando el post que ella misma acababa de pedir: peor que no
      tenerla, porque además consume el turno.
    - Publicar el banco en `sources` es el error simétrico. Ahí viven la identidad y las
      cicatrices personales del dueño de la cuenta, y `sources` se escribe al manifiesto y a
      la card. Verificable no es sinónimo de citable.

    Por eso el banco entra aquí como una fuente más -- el auditor no necesita otra API para
    comprobar contra él -- y va al final de la lista, detrás de lo citable, que es lo que el
    lector del post sí puede ir a verificar por su cuenta.
    """
    if not respaldo:
        return citables
    return [
        *citables,
        {
            "title": _BANCO_AUDITABLE_TITULO,
            "url": "",
            "snippet": f"{_BANCO_AUDITABLE_REGLA}\n{respaldo[:_MAX_BANCO_AUDITABLE_CHARS]}",
        },
    ]


def _titular_visual(bruto: str, tema: str) -> str:
    """El titular que alimenta la imagen, o el tema si el titular no pasa `revisar_visual`.

    Un post bueno no se tira por un titular flojo: el titular solo alimenta la pieza visual,
    mientras que el texto es lo que se publica. Se degrada al tema, que es corto y concreto
    por construcción.
    """
    titular = _cap_str(bruto, _MAX_TITULAR_CHARS)
    if not titular or revisar_visual(headline=titular):
        return tema
    return titular


# Esta salida es la ÚNICA del módulo que le pide al modelo escribir el post por su cuenta, y
# sin `ctx.llm` la herramienta a la que lo manda tampoco puede auditar nada
# (`CrearContenidoSocialTool.run` salta `revisar_calidad`/`auditar_hechos` sin modelo). Sin la
# segunda frase, este mensaje es el único camino del módulo que deja publicar un hecho fechable
# que nadie verificó -- justo lo que el resto del archivo existe para impedir.
_SIN_MODELO = (
    "Esta instalación no tiene un modelo disponible, y esta herramienta escribe el post ella "
    "misma. Escribe tú el texto y entrégalo con 'crear_contenido_social' ('tema' y 'texto'). "
    "Sin modelo aquí tampoco corre la auditoría de hechos, así que ese texto no puede afirmar "
    "ningún evento, lanzamiento, cifra ni fecha de tu memoria: escríbelo como criterio o "
    "mecanismo, sin ningún hecho fechable."
)


def _sin_fuente(consulta: str, max_dias: int) -> str:
    ventana = (
        f"los últimos {max_dias} días"
        if max_dias >= _VENTANA_AMPLIA_DIAS
        else f"los últimos {max_dias} y luego {_VENTANA_AMPLIA_DIAS} días"
    )
    return (
        f"No escribí nada: no encontré ningún titular reciente y verificable sobre "
        f"'{consulta}' (busqué en {ventana}) y esta cuenta tampoco tiene un banco de contexto "
        "real configurado, así que no hay un solo hecho comprobable sobre el que apoyar el "
        "post. Sin fuente no se escribe: un dato inventado cuesta más credibilidad de la que "
        "gana un post publicado a tiempo.\n\n"
        "Haz UNA de estas tres cosas:\n"
        "1. Vuelve a llamar 'crear_post_linkedin' con un 'tema' más concreto o más noticioso "
        f"(o con 'max_dias' más alto, hasta 30, si el hecho es de hace semanas).\n"
        "2. Configura el banco de contexto real de esta cuenta con 'configurar_perfil_social' "
        "(accion='actualizar', campo 'context_bank'): con hechos verificables suyos puedo "
        "escribir una pieza de criterio sin ningún evento fechable.\n"
        "3. Si el usuario ya te dictó el texto, no investigues: pásalo a "
        "'crear_contenido_social' con 'tema' y 'texto'."
    )


def _no_publicable(problemas: list[str]) -> str:
    detalle = "\n".join(f"- {problema}" for problema in problemas) or (
        "- El modelo no devolvió un borrador utilizable."
    )
    return (
        f"Escribí el post {_MAX_INTENTOS} veces y ninguna versión pasó el control de calidad, "
        "así que no entrego un borrador que no se sostiene (saltarse un turno protege la "
        f"cuenta mejor que publicar algo artificial). Lo que falló la última vez:\n{detalle}"
        "\n\nQué hacer: pide un ángulo más concreto -- una decisión, alguien a quien le "
        "cambia algo, un hecho verificable -- y vuelve a llamar 'crear_post_linkedin' con ese "
        "'tema'. No repitas la llamada con el mismo tema tal cual: el resultado sería igual."
    )


def _reintento_con_destino(tema: str) -> str:
    """Cómo retomar después del modal de destino, con el tema ya DEVUELTO literal.

    Este es el único traspaso de datos que le queda al modelo en todo el ciclo, y por eso
    no se le pide de memoria: "vuelve con el mismo 'tema'" se cumple mal cuando el turno
    quedó cortado por una pregunta, y olvidarlo no falla ruidosamente -- la herramienta cae
    a la rotación editorial y escribe un post correcto sobre OTRA cosa. Repetir aquí el
    texto exacto que ya recibimos convierte "acordarse" en "copiar".
    """
    if not tema:
        return (
            "cuando conteste, vuelve a llamar 'crear_post_linkedin' con 'destino' puesto y "
            "sin 'tema' (el usuario no pidió uno). No escribas tú el post: la herramienta lo "
            "escribe con la voz de esa cuenta."
        )
    return (
        "cuando conteste, vuelve a llamar 'crear_post_linkedin' con 'destino' puesto y con "
        f"tema={tema!r} EXACTAMENTE así (es lo que pidió el usuario; si lo cambias u omites, "
        "la herramienta escribe sobre otra cosa). No escribas tú el post: la herramienta lo "
        "escribe con la voz de esa cuenta."
    )


_NOTA_ORIGEN = {
    _ORIGEN_FRESCO: "Lo anclé en una noticia real de estos días, verificada por fecha.",
    _ORIGEN_AMPLIADO: (
        "No había nada de los últimos días, así que lo anclé en una noticia real algo más "
        "antigua (verificada por fecha); no la presento como novedad de hoy."
    ),
    _ORIGEN_BANCO: (
        "No había ninguna noticia reciente verificable, así que lo escribí como pieza de "
        "criterio apoyada en el banco de contexto real de esta cuenta, sin ningún evento "
        "fechable."
    ),
    _ORIGEN_TESIS: (
        "No encontré ninguna noticia verificable sobre eso y esta cuenta todavía no tiene "
        "banco de contexto, así que en vez de dejarte sin nada lo escribí como criterio puro, "
        "sin un solo hecho fechable. Si querías comentar una noticia concreta, dime cuál o "
        "sube 'max_dias'; si quieres que pueda apoyarse en tus datos reales, configura el "
        "banco de contexto con 'configurar_perfil_social'."
    ),
}


class CrearPostLinkedInTool(Tool):
    name = "crear_post_linkedin"
    description = (
        "Escribe un post de LinkedIn de principio a fin cuando el usuario pide 'hazme un post "
        "sobre X' o 'escribe el post de hoy'. Hace TODO el ciclo por dentro, en una sola "
        "llamada: busca la fuente real y verificada por fecha, redacta el post con la voz de "
        "la cuenta correcta, lo audita contra esa fuente y entrega el borrador con su "
        "imagen.\n\n"
        "NO llames 'investigar_noticias_frescas' ni 'planificar_contenido_social' antes: esta "
        "herramienta ya hace las dos cosas por dentro, y no tienes que transportar titulares "
        "ni fuentes entre llamadas. NO la sigas con 'crear_contenido_social': el borrador ya "
        "queda entregado.\n\n"
        "Usa 'crear_contenido_social' en su lugar SOLO cuando el usuario ya te dictó el texto "
        "del post y únicamente hay que empaquetarlo, o cuando la red no es LinkedIn.\n\n"
        "Si el usuario tiene más de una cuenta de LinkedIn y no te dijo cuál, llama igual sin "
        "'destino': la herramienta le muestra la pregunta con sus cuentas reales. No preguntes "
        "tú por tu cuenta.\n\n"
        "Puede terminar sin post (sin fuente verificable, o ninguna versión pasó el control de "
        "calidad). En ese caso devuelve exactamente qué hacer: sigue esas instrucciones en vez "
        "de reintentar igual o de escribir el post tú mismo sin fuente.\n\n"
        "La app ya muestra el copy completo, la imagen y las acciones en una tarjeta dentro "
        "del chat: tu respuesta final NO debe repetir ni parafrasear el post, solo confirmar "
        "en una frase que el borrador quedó listo."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "tema": {
                "type": "string",
                "description": (
                    "Sobre qué quiere el post el usuario, con sus palabras. Omítelo solo "
                    "cuando pida 'el post de hoy' sin decir de qué: ahí la herramienta elige "
                    "el territorio que toca en la rotación editorial de la cuenta."
                ),
            },
            "destino": {
                "type": "string",
                "pattern": DESTINATION_ID_PATTERN,
                "description": (
                    "Id de la cuenta donde se publicaría ('personal' es el perfil personal; "
                    "una página de organización usa el id que el usuario le puso). Define la "
                    "voz del post. Si no lo sabes, omítelo."
                ),
            },
            "max_dias": {
                "type": "integer",
                "minimum": 1,
                "maximum": 30,
                "default": _MAX_DIAS_DEFECTO,
                "description": (
                    "Ventana de frescura de la búsqueda de noticias. Súbela si el hecho que "
                    "quiere comentar el usuario es de hace semanas."
                ),
            },
            "con_imagen": {"type": "boolean", "default": True},
        },
        "required": [],
    }

    def __init__(
        self,
        *,
        image_provider: ImageProvider | None = None,
        uploader: Uploader | None = None,
    ) -> None:
        self._image_provider = image_provider
        self._uploader = uploader or subir_archivo

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        tema = _cap_str(args.get("tema"), _MAX_TEMA_CHARS)
        destino_raw = str(args.get("destino") or "").strip().lower()
        target = destino_raw if is_valid_destination_id(destino_raw) else None

        # El gate de destino va PRIMERO, antes de gastar una sola llamada de red o de modelo:
        # la cuenta define la voz, y descubrirlo después de investigar y escribir tira el
        # turno entero. Mismo criterio (y mismo código) que `crear_contenido_social`.
        if target is None:
            decision = await _decidir_destino(
                ctx,
                _PLATAFORMA,
                texto_ya_escrito=False,
                instruccion_reintento=_reintento_con_destino(tema),
            )
            if decision.pregunta is not None:
                return decision.pregunta
            # Con una sola cuenta no se pregunta, pero el destino sí queda resuelto: es lo que
            # elige el perfil editorial de más abajo (la voz del post) y lo que viaja en la
            # card para que el cliente pueda ofrecer publicar.
            target = decision.destino

        if ctx.llm is None:
            return ToolResult(content=_SIN_MODELO)

        perfil = await get_editorial_profile_for_destination(ctx, _PLATAFORMA, target)

        # Rotación editorial (`edecan_creative.agenda`): la FORMA argumental rota siempre, así
        # dos posts seguidos no salen con la misma arquitectura. El TERRITORIO solo manda
        # cuando el usuario no dijo tema -- si lo dijo, el tema es suyo y no se le cambia.
        estado = await get_agenda_state(ctx, _PLATAFORMA)
        paso = agenda.siguiente_paso(estado)
        historial = estado.get("historial") or []
        bloqueados = agenda.temas_en_cooldown(agenda.temas_recientes_de_historial(historial))

        excluir: tuple[str, ...] = ()
        if tema:
            consulta = tema
            encargo = (
                f"Escribe el post de LinkedIn de este turno sobre lo que pidió el usuario: {tema}"
            )
            # El cooldown NO recorta la búsqueda de un tema que el usuario pidió con todas sus
            # letras (`agenda.instruccion_cooldown` documenta esa excepción): sería negarle
            # justo lo que acaba de pedir.
            instruccion_cooldown = ""
            # Solo avanza el reloj de la FORMA. El territorio de este turno no se usó -- mandó
            # el tema del usuario -- y quemarlo dejaría un hueco permanente en la rotación del
            # feed (ver el docstring de `agenda.siguiente_paso` sobre componer el estado a mano).
            estado_siguiente = agenda.avanzar_formato(estado)
        else:
            foco = agenda.foco_actual(estado)
            excluir = agenda.terminos_a_excluir(bloqueados)
            consulta = agenda.query_con_diversidad(
                paso.territorio["query"], foco["busqueda"], excluir
            )
            encargo = (
                "Escribe el post de LinkedIn de este turno. El usuario no pidió un tema "
                "concreto, así que toca este territorio editorial: "
                f"{paso.territorio['pilar']}"
            )
            instruccion_cooldown = agenda.instruccion_cooldown(bloqueados, foco["etiqueta"])
            estado_siguiente = agenda.avanzar_foco(paso.estado)

        # El reloj avanza aunque este turno termine sin post: si no, una combinación que falla
        # dejaría a la cuenta reintentándola para siempre. El historial de cooldown, en cambio,
        # solo lo escribe un borrador que de verdad salió (`empaquetar_borrador_social`).
        await save_agenda_state(ctx, _PLATAFORMA, estado_siguiente)

        # `permisivo` solo cuando el tema lo pidió el usuario: ahí el editor jefe no puede
        # vetar por gusto ("poco interesante") porque la persona ya decidió que le interesa.
        # En el post recurrente sin tema pedido sí puede saltarse el turno -- regla "no forzar
        # un post" de `REGLAS_LINKEDIN`. El auditor de hechos es estricto en ambos casos.
        #
        # Con el port de los gates blandos, este flag ya llega a los TRES sitios que podían
        # matar el turno (la batería determinista, el editor jefe y el escalón sin fuente), no
        # solo a `revisar_calidad` como antes.
        permisivo = bool(tema)

        max_dias = _acotar(args.get("max_dias"), minimo=1, maximo=30, default=_MAX_DIAS_DEFECTO)
        contexto = await _reunir_contexto(
            perfil, consulta, max_dias=max_dias, excluir_terms=excluir, permisivo=permisivo
        )
        if contexto is None:
            return ToolResult(content=_sin_fuente(consulta, max_dias))

        llamar_modelo = _llamar_modelo_editorial(ctx)
        bloque_perfil = _bloque_perfil(perfil)
        # El banco de contexto viaja SIEMPRE, haya o no noticia: es lo único que autoriza a
        # atribuirle un hecho de vida o de operación al autor, y sin él en el prompt el
        # escritor solo tiene dos salidas malas -- inventarse una anécdota, o escribir sin
        # nada propio. Trae su propia regla anti-autobiografía
        # (`social.build_context_bank_prompt_block`).
        bloque_banco = build_context_bank_prompt_block(perfil)
        temas_recientes = _bloque_temas_recientes(historial)
        # Cooldown ESTRUCTURAL (no de tema): qué FORMA de post no puede repetirse porque el
        # anterior ya la usó. Es también el lugar correcto donde vive la prohibición de cerrar
        # con pregunta -- condicionada al historial, como en Aria, y no absoluta.
        prohibidas = firmas_prohibidas_recientes(historial)
        cooldown_estructura = instruccion_cooldown_estructura(prohibidas)
        recientes_texto = _bloque_borradores_recientes(historial)

        correcciones: list[str] = []
        copy = ""
        borrador: dict[str, Any] = {}
        # Lo que se PUBLICA como fuente del post. Empieza en todo lo citable y se estrecha a
        # la que el escritor dijo usar; el banco nunca entra aquí (ver `_material_para_auditar`).
        citables_del_post: list[dict[str, str]] = contexto.citables
        # Los candidatos que se van cayendo, con su prioridad de rescate. Si TODOS los intentos
        # fallan, se entrega el de MAYOR prioridad con un aviso en vez del mensaje "no
        # publicable" (ver `_PRIORIDAD_*`). Diferencia deliberada con Aria: allá el motor es
        # cron y "saltarse un turno protege la cuenta"; acá es un ATAJO del chat, la persona lo
        # pidió, y es su decisión aprobar o descartar. Verla en blanco tras dos minutos es peor
        # que verla con "revísalo antes de publicar".
        candidatos: list[_Candidato] = []
        # Aviso que se pega al borrador ENTREGADO cuando el auditor vetó pero seguimos adelante
        # porque el usuario pidió el tema. Aria degrada en silencio (es un cron, nadie lee el
        # aviso); acá la persona está mirando y tiene derecho a saber qué le llegó sin verificar.
        aviso_auditor = ""
        for intento in range(_MAX_INTENTOS):
            # Se reinicia por intento: un aviso del intento 1 no puede pegarse a un borrador
            # del intento 2 que sí pasó limpio.
            aviso_auditor = ""
            borrador = (
                await _escribir(
                    ctx,
                    encargo=encargo,
                    instruccion_formato=paso.instruccion_formato,
                    instruccion_cooldown=instruccion_cooldown + cooldown_estructura,
                    temas_recientes=temas_recientes,
                    bloque_perfil=bloque_perfil,
                    bloque_banco=bloque_banco,
                    contexto=contexto,
                    correcciones=correcciones,
                    fuente_sugerida=_fuente_sugerida(contexto.citables, intento),
                )
                or {}
            )
            texto = normalizar(str(borrador.get("texto") or ""), _PLATAFORMA)
            if not texto:
                correcciones = [
                    "No devolviste el JSON pedido con un campo 'texto'. Responde SOLO el "
                    "objeto JSON, empezando por la llave {."
                ]
                continue
            borrador["texto"] = texto
            # El peor candidato posible, guardado desde ya: el texto crudo del escritor. Sólo
            # se entrega si ningún intento llega más lejos.
            candidatos.append(_Candidato(_PRIORIDAD_CRUDO, dict(borrador), ["Sin revisar."]))

            citables_del_post = _fuente_elegida(
                contexto.citables, str(borrador.get("fuente_principal") or "")
            )

            # PASO 1 -- EL EDITOR JEFE, QUE REESCRIBE. Éste es el cambio de ORDEN que arregla
            # la causa raíz: antes, la batería determinista era la PRIMERA puerta y corría
            # sobre el texto crudo del escritor, así que ese texto tenía que salir perfecto de
            # una sola pasada contra doce gates a la vez. En Aria los mismos gates son la
            # ÚLTIMA puerta y el texto que ven ya pasó por dos modelos que lo reescribieron
            # (`_finalizar_post`, `linkedin_content.py:1572-1618`). El comentario que había
            # aquí justificaba el pre-gate por costo ("antes de gastar el auditor"), pero el
            # costo real era el opuesto: se gastaban tres ciclos completos y a veces no salía
            # post.
            pulido = await pulir_borrador(
                borrador,
                _contexto_calidad(
                    plataforma=_PLATAFORMA,
                    tema=tema or paso.territorio["etiqueta"],
                    perfil=perfil,
                    fuentes=citables_del_post,
                    respaldo_no_citable=contexto.respaldo,
                ),
                llamar_modelo,
                permisivo=permisivo,
                plataforma=_PLATAFORMA,
                # Sin nada verificable detrás, el editor jefe no puede vetar "por no traer
                # fuentes": el post es de criterio a propósito (escalón de tesis pura).
                sin_fuentes=not citables_del_post and not contexto.respaldo,
                instruccion_formato=paso.instruccion_formato,
                cooldown_estructura=cooldown_estructura,
                borradores_recientes=recientes_texto,
            )
            if pulido is None:
                correcciones = [
                    "El editor jefe descartó el borrador: no encontró un ángulo concreto que "
                    "sostener sin inventar hechos."
                ]
                continue
            borrador = pulido
            texto = normalizar(str(pulido["texto"]), _PLATAFORMA)

            # PASO 2 -- EL AUDITOR DE HECHOS, que también reescribe.
            auditado, problemas = await auditar_hechos(
                texto,
                _material_para_auditar(citables_del_post, contexto.respaldo),
                llamar_modelo,
                # Las "fuentes" de Edecán son titulares + snippet de buscador, nunca el cuerpo
                # del artículo: el auditor tiene que saberlo para no dar por buenas las
                # deducciones que un titular no autoriza.
                solo_titulares=bool(citables_del_post),
            )
            if auditado:
                texto = auditado
            elif permisivo:
                # El auditor vetó, pero el usuario pidió el tema: se conserva el texto que el
                # editor jefe ya pulió y ancló en la fuente, y se SIGUE ADELANTE -- lo mismo
                # que hace `_finalizar_post` (`linkedin_content.py:1598-1600`). Antes se
                # quemaba el intento completo (escritor + editor + auditor, tres llamadas)
                # para volver a empezar de cero, y eso es la mitad de los dos minutos de
                # espera que la persona vive como "no funciona".
                logger.info(
                    "El auditor vetó el borrador pero el usuario pidió el tema: se conserva el "
                    "texto pulido por el editor. Motivos: %s",
                    problemas,
                )
                motivo_auditor = problemas[0] if problemas else "no reportado"
                aviso_auditor = (
                    "Ojo: el auditor de hechos no pudo sostener todo el borrador con la fuente "
                    f"({motivo_auditor}). Lo entrego igual porque lo pediste, pero verifica los "
                    "datos antes de publicarlo."
                )
            else:
                correcciones = problemas or ["El auditor de hechos descartó el borrador."]
                candidatos.append(_Candidato(_PRIORIDAD_PULIDO, dict(borrador), correcciones))
                continue

            # PASO 3 -- REPARAR LA PRIMERA PERSONA QUE HAYA REINTRODUCIDO EL AUDITOR, y
            # RE-AUDITAR el texto reparado. `auditar_hechos` reescribe con un prompt que no
            # conoce ninguna regla de estilo, así que un "nos", un "creo" o un "vimos" nuevos
            # son lo ESPERADO (su propio docstring lo documenta como lección aprendida a la
            # mala). Antes, este camino hacía `continue` sin intentar nada, sin respetar
            # `permisivo` y sin guardar nada en rescate: era el ÚNICO camino del bucle que no
            # dejaba candidato, así que tres intentos muriendo aquí dejaban a la persona con
            # cero post. Es el mismo patrón que ya se reparaba antes del editor, un paso más
            # adelante (`linkedin_content.py:1601-1605`).
            if _tiene_primera_persona(texto):
                reparado = await reescribir_sin_primera_persona(
                    texto, citables_del_post, llamar_modelo
                )
                if reparado:
                    re_auditado, _ = await auditar_hechos(
                        reparado,
                        _material_para_auditar(citables_del_post, contexto.respaldo),
                        llamar_modelo,
                        solo_titulares=bool(citables_del_post),
                    )
                    texto = re_auditado or (reparado if permisivo else texto)
            borrador["texto"] = texto

            # PASO 4 -- LOS GATES DETERMINISTAS, AL FINAL Y BLANDOS EN PERMISIVO. Con
            # `permisivo` sólo sobrevive la primera persona, igual que en Aria. Al reparador
            # de arriba se le exige únicamente lo que vino a arreglar: antes se medía su
            # resultado contra los doce gates completos, así que si el borrador traía "yo" Y
            # cerraba con pregunta, el reparador arreglaba el "yo", el otro gate seguía rojo y
            # el intento moría igual.
            # El banco de contexto se pasa como material PRIVADO para el gate:
            # si el texto nombra al dueño o a una marca personal que el propio
            # banco menciona varias veces, es una fuga y se rechaza (ver
            # `editorial._menciona_nombre_del_banco`). El banco jamás se
            # publica; sirve para no contradecir la realidad, no para narrarla.
            correcciones = revisar(
                texto,
                _PLATAFORMA,
                permisivo=permisivo,
                banco_privado=context_bank_text(perfil),
            )
            if len(texto) < _MIN_TEXTO_ENTREGABLE_CHARS:
                correcciones = [
                    *correcciones,
                    f"El texto quedó en {len(texto)} caracteres, demasiado corto para un post.",
                ]
            if correcciones:
                # Éste es el MEJOR candidato que existió en toda la corrida: pasó el editor
                # jefe Y el auditor, y sólo cayó en el re-gate final. Antes se descartaba sin
                # guardarse, de modo que el usuario podía terminar recibiendo el texto crudo
                # (el que ni el formato respetó) teniendo disponible uno que pasó los dos
                # juicios. Si lo que quedó mal es la primera persona, NO se prefiere: eso
                # rompería la regla del dueño de la cuenta (nunca atribuirle experiencias), así
                # que baja al fondo de la cola.
                prioridad = (
                    _PRIORIDAD_CRUDO if _tiene_primera_persona(texto) else _PRIORIDAD_CASI
                )
                candidatos.append(_Candidato(prioridad, dict(borrador), correcciones))
                continue

            copy = texto
            if aviso_auditor:
                borrador["_aviso_rescate"] = aviso_auditor
            break

        if not copy:
            # Todos los intentos rechazados. Se entrega el MEJOR que haya quedado, con aviso,
            # en vez de un mensaje vacío: un borrador con "revísalo antes de publicar" es
            # infinitamente más útil que "escribí 3 veces y ninguna versión pasó".
            elegido = _mejor_candidato(candidatos)
            if elegido is not None:
                borrador = elegido.borrador
                copy = normalizar(str(borrador.get("texto") or ""), _PLATAFORMA)
                motivos = elegido.problemas
                borrador["_aviso_rescate"] = (
                    "Este borrador no pasó el control editorial automático "
                    f"({motivos[0] if motivos else 'motivo no reportado'}); "
                    "revísalo con calma antes de publicarlo."
                )
            else:
                return ToolResult(content=_no_publicable(correcciones))

        tema_final = (
            _cap_str(borrador.get("tema"), _MAX_TEMA_CHARS) or tema or paso.territorio["etiqueta"]
        )
        titular_final = _titular_visual(str(borrador.get("titular_visual") or ""), tema_final)
        # El diccionario visual se REPARA antes de entregarlo (recortes, kicker en mayúsculas,
        # quitar el prefijo que repite el kicker, accent forzado a ser subcadena real del
        # headline, support vaciado si repite el titular) y degrada a `titular_final`/`tema_final`
        # cuando el escritor no lo llenó. Antes llegaba al compositor con `strip()` y nada más:
        # un accent inexistente no resaltaba nada y un support que repetía el titular salía
        # montado dos veces en la misma imagen. Es cosmético, pero se ve en CADA post.
        visual_final = normalizar_visual(
            borrador.get("visual") if isinstance(borrador.get("visual"), dict) else None,
            tema=tema_final,
            titular=titular_final,
        )
        resultado = await empaquetar_borrador_social(
            ctx,
            platform_key=_PLATAFORMA,
            spec=PLATFORMS[_PLATAFORMA],
            topic=tema_final,
            copy=copy,
            target=target,
            # Solo lo citable: el banco de contexto se auditó, pero no se publica.
            sources=citables_del_post,
            hashtags=[],  # `editorial.revisar` los prohíbe en LinkedIn.
            headline=titular_final,
            visual_prompt=tema_final,
            alt_text=_cap_str(borrador.get("alt_text"), _MAX_ALT_CHARS),
            con_imagen=args.get("con_imagen", True) is not False,
            uploader=self._uploader,
            image_provider=self._image_provider,
            # Diccionario editorial ya reparado y completo: el compositor tipográfico monta
            # ESTO encima de la foto (`social._visual_publicable` ->
            # `imagen_editorial.componer_titular`), y el crítico de imagen juzga contra ESTO.
            visual=visual_final,
        )
        # `empaquetar_borrador_social` también devuelve errores duros (un copy más largo que el
        # límite de la plataforma) sin `data`: a esos no se les pega una nota que hablaría de
        # un borrador que no existe.
        if isinstance(resultado.data, dict):
            resultado.data["origen_fuente"] = contexto.origen
            resultado.content = f"{resultado.content} {_NOTA_ORIGEN[contexto.origen]}"
            # Rescate: si el borrador entró por el camino de "ningún intento pasó pero
            # entrego el mejor que sí pasó los gates deterministas", ese aviso va
            # ADELANTE del content, para que la persona lo vea antes que la nota
            # editorial.
            aviso = borrador.get("_aviso_rescate")
            if aviso:
                resultado.content = f"{aviso} {resultado.content}"
        return resultado
