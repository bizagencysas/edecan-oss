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

**Lo que este módulo le PROMETE a quien lo llama.** Una sola frase, y está probada: *si `run`
devuelve `data["copy"]`, ese copy es publicable -- mide al menos
`MIN_COPY_ENTREGABLE_CHARS`; si no, devuelve `data["fallo"]` con el motivo y ningún borrador.*
No hay tercera opción, y sobre todo no existe la franja intermedia donde el motor decía
"listo" y su consumidor descartaba el texto por corto: esa franja se tragó TODAS las entregas
de una de las cuentas de una instalación real -- ni una sola llegó nunca, sin post y sin
explicación. Quien entregue este trabajo en segundo plano puede confiar en la promesa y,
cuando venga `fallo`, tiene la obligación de contárselo a la persona.

Y una promesa más, del mismo tipo y por el mismo motivo: *todo lo que quien entrega necesita
saber viaja en `data`, nunca sólo en `content`.* `content` está escrito para el modelo del
chat -- nombra herramientas y parámetros -- y el job que arma la tarjeta lo ignora a propósito.
Por eso el aviso de "esto no pasó limpio" va en `data["aviso"]`, y `data["sin_auditar"]` dice
si el auditor de HECHOS llegó a validar el texto. Cuando esa bandera está encendida, ofrecer
"publicar de un toque" en la página de una empresa es exactamente lo que no se debe hacer.

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
    _sin_acentos,
    _tiene_primera_persona,
    firmas_prohibidas_recientes,
    instruccion_cooldown_estructura,
    normalizar,
    normalizar_visual,
    revisar,
    revisar_visual,
    tiene_numero_extenso,
)
from .investigacion import _acotar, titulares_frescos
from .marcas import (
    DESTINATION_ID_PATTERN,
    PERSONAL_DESTINATION_ID,
    editorial_profile_key,
    is_valid_destination_id,
)
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

__all__ = [
    "FALLO_COPY_LARGO",
    "FALLO_NO_PUBLICABLE",
    "FALLO_SIN_FUENTE",
    "FALLO_SIN_MODELO",
    "MIN_COPY_ENTREGABLE_CHARS",
    "ContextoDeEscritura",
    "CrearPostLinkedInTool",
]

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

# ---------------------------------------------------------------------------
# EL PISO DE ENTREGA: un solo número, y vive aquí.
# ---------------------------------------------------------------------------
# Cuántos caracteres tiene que medir el texto para que se pueda ENTREGAR como post (incluido
# el camino de rescate, donde más tienta bajarlo).
#
# Este número es PÚBLICO y es un CONTRATO, no un detalle interno, y lo es por un fallo que se
# vivió en producción: el motor daba por bueno cualquier texto de 80 caracteres para arriba y
# el job que lo consume (`edecan_worker.handlers.create_linkedin_post`) exigía 150 por su
# cuenta, con su propia constante privada. La franja 80-150 era un agujero SILENCIOSO: un copy
# de 118 caracteres pasaba el motor -- que lo empaquetaba, lo anotaba en su historial editorial
# y decía "listo" -- y moría en el job, que se quedaba sin nada que entregar. Al usuario no le
# llegaba ni el post ni una explicación: le habíamos dicho "me pongo a escribir" y después
# silencio. En el historial completo de esa instalación no hay UNA SOLA entrega para esa
# cuenta, contra las otras que sí funcionaban -- y nadie lo vio, porque no fallaba ruidoso.
#
# La decisión, escrita aquí para que nadie la deshaga por descuido: **el motor garantiza el
# piso que su consumidor exige**, no al revés. Manda el número ALTO -- el que de verdad hace
# falta para que una card no salga rota -- y lo cumple quien escribe, que es el único que
# puede REINTENTAR. La alternativa (que el job importara el piso bajo del motor) también
# cerraba la discrepancia, pero por el lado malo: pasaría a entregar muñones de 90 caracteres,
# justo lo que ninguna de las dos capas quería. Por eso el job ya no declara ningún número:
# importa ÉSTE.
#
# La garantía concreta que este módulo cumple, y que los tests fijan: **si `run` devuelve
# `data["copy"]`, ese copy mide al menos `MIN_COPY_ENTREGABLE_CHARS`.** Si no se llega, se
# devuelve un fallo explícito (`data["fallo"]`) y CERO borrador; nunca un texto a medias.
#
# Por qué 150 y no los 80 de Aria (`linkedin_content.py:1610`): allá el piso solo separa
# "texto" de "nada" en un cron que puede saltarse el turno sin que nadie se entere. Acá el
# texto se pinta en una tarjeta con imagen, título y botón de publicar; por debajo de ~150 lo
# que se ve es una tarjeta rota, no un post corto.
MIN_COPY_ENTREGABLE_CHARS = 150

# Códigos de fallo que viajan en `data["fallo"]` cuando NO hay borrador. Existen porque el
# `content` de esos caminos está escrito para el MODELO del chat (lleva nombres de
# herramientas y de parámetros), mientras que quien entrega el trabajo en segundo plano tiene
# que contarle a la PERSONA qué pasó, en su idioma y sin jerga interna. Un código estable es
# lo único que permite traducir el motivo sin parsear prosa -- y parsear prosa que escribe un
# modelo es exactamente como se construye el próximo silencio.
FALLO_SIN_FUENTE = "sin_fuente"
FALLO_NO_PUBLICABLE = "no_publicable"
FALLO_SIN_MODELO = "sin_modelo"
# El post se escribió entero y quedó más largo de lo que la plataforma acepta. Es un fallo
# distinto de "no me salió nada" y merece código propio: el motivo más accionable que existe
# ("te quedó largo, ¿lo corto?") se volvía el menos accionable cuando caía en el genérico.
FALLO_COPY_LARGO = "copy_largo"

# Anti-destripe del auditor: el auditor de hechos REESCRIBE, y en modo `solo_titulares` puede
# recortar legítimamente las deducciones que el titular no sostiene. Pero si de un borrador
# SUSTANCIAL (≥ `_MIN_LARGO_PARA_MEDIR_RECORTE`) deja MENOS de `_FRACCION_MIN_TRAS_AUDITOR`,
# no es una corrección menor: casi nada se pudo anclar y lo que queda es un gancho pelado.
#
# Esta medida es RELATIVA a propósito -- no penaliza un post legítimamente corto que el auditor
# respetó entero --, pero por sí sola tiene un hueco que costó otro silencio: **si el escritor
# produjo poco, no protege nada**. Con un borrador de 260 caracteres el `>= 400` no llega a
# medirse y un muñón de 118 salía como si fuera un post. Por eso ya no está sola: el piso
# ABSOLUTO (`MIN_COPY_ENTREGABLE_CHARS`) corre en el mismo sitio y con la misma consecuencia
# (reintentar con corrección explícita, nunca devolver el muñón). Las dos juntas cubren los dos
# casos: el destripe de un texto grande y la amputación de uno mediano.
_MIN_LARGO_PARA_MEDIR_RECORTE = 400
_FRACCION_MIN_TRAS_AUDITOR = 0.45

# A partir de qué punto se puede DECIR que el recorte lo hizo el auditor. Sin esta medida, el
# anti-muñón acusaba al auditor de todo lo que llegara corto a su salida, incluido el texto que
# ya venía corto del escritor -- y el reintento salía con una corrección literalmente falsa:
# "El auditor recortó el post de 110 a 110 caracteres". Al escritor se le estaba pidiendo que
# arreglara algo que él no hizo, en vez de lo único que sí puede arreglar: escribir más largo.
# Una corrección equivocada no es un detalle de redacción; es un intento quemado.
_FRACCION_RECORTE_ATRIBUIBLE = 0.9


def _correccion_texto_corto(largo: int) -> str:
    """Lo que se le dice al escritor cuando el texto simplemente no da el largo.

    Vive en una función y no suelto en el bucle porque hay DOS sitios que llegan a la misma
    conclusión (el anti-muñón del auditor y el re-gate final) y tenían dos textos distintos: el
    correcto era el de abajo y era inalcanzable, porque el de arriba hacía `continue` antes.
    """
    return (
        f"El texto quedó en {largo} caracteres, demasiado corto para un post: hacen falta al "
        f"menos {MIN_COPY_ENTREGABLE_CHARS}. Reescríbelo completo (240-340 palabras, 3 o 4 "
        "párrafos), no lo alargues con relleno."
    )


def _correccion_auditor_recorto(antes: int, despues: int) -> str:
    """Lo que se le dice al escritor cuando el auditor SÍ le amputó el cuerpo.

    Sólo se usa cuando el recorte es real y medible (`_FRACCION_RECORTE_ATRIBUIBLE`): el pedido
    concreto -- anclar cada afirmación en la fuente -- únicamente tiene sentido si lo que se
    perdió fue justamente lo que la fuente no sostenía.
    """
    return (
        f"El auditor recortó el post de {antes} a {despues} caracteres, por debajo de lo que se "
        f"puede publicar ({MIN_COPY_ENTREGABLE_CHARS} caracteres como mínimo): casi todo el "
        "cuerpo se apoyaba en deducciones que la fuente no sostiene. Reescribe un post COMPLETO "
        "(240-340 palabras) donde CADA afirmación se ancle en lo que el titular/snippet dice, "
        "sin especular sobre mecanismos, causas o comparaciones que la fuente no menciona."
    )


def _correccion_editor_recorto(antes: int, despues: int, piso_palabras: int) -> str:
    """Lo que se le dice al escritor cuando el EDITOR JEFE (`auditoria.pulir_borrador`) es
    quien amputó el cuerpo, medido en palabras contra el piso que esta cuenta declaró.

    Existe porque el defecto medido en producción no era del escritor: nueve de nueve corridas
    reales, el escritor entregaba 85-139 palabras (la mayoría sobre el piso de la cuenta) y el
    editor jefe -- que corre en un modelo distinto y más débil, y que hasta este arreglo no
    sabía que existía un largo objetivo -- lo recortaba a 47-74 palabras al "pulirlo". El
    escritor no puede arreglar el comportamiento del editor, pero SÍ puede darle más margen: un
    crudo más largo y más desarrollado sigue teniendo más probabilidad de sobrevivir un recorte
    proporcional que uno justo en el piso. Mismo criterio que `_correccion_auditor_recorto`,
    en palabras en vez de caracteres porque el piso de esta cuenta se declaró en palabras.
    """
    return (
        f"El editor jefe recortó el post de {antes} a {despues} palabras, por debajo del "
        f"mínimo de {piso_palabras} palabras que pide el perfil de esta cuenta. Escribe un post "
        "COMPLETO y bien desarrollado (otra escena, otro momento concreto, una consecuencia "
        "distinta -- nunca relleno) para que quede con margen de sobra sobre ese piso incluso "
        "después de que se pula el estilo."
    )


# Lo que se le dice al escritor cuando lo que devolvió no tiene forma de post. Es un motivo
# distinto de "quedó corto" y confundirlos cuesta un intento: pedirle el JSON a quien SÍ mandó
# prosa buena no le dice nada de lo único que estaba mal.
_CORRECCION_SIN_JSON = (
    "No devolviste el JSON pedido con un campo 'texto'. Responde SOLO el objeto JSON, "
    "empezando por la llave {."
)
_CORRECCION_SIN_RESPUESTA = (
    "No devolviste nada utilizable. Responde SOLO el objeto JSON pedido, empezando por la "
    "llave {, con el post completo en el campo 'texto'."
)

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
    # Genérico y por-tenant: si el destino configuró su propio largo objetivo (p. ej. una
    # página de marca que publica piezas cortas), ANULA el "entre 170 y 230 palabras" fijo
    # de `_SISTEMA_ESCRITOR` de abajo. Sin este campo (la mayoría de cuentas), no cambia
    # nada. Ver `_con_cierre_garantizado` para el otro anulador por-perfil, el del cierre.
    ("target_length", "Largo objetivo de ESTA cuenta (anula cualquier rango de palabras general)"),
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
    buscar_noticia: bool = True,
) -> ContextoDeEscritura | None:
    """La red de seguridad: baja de rung en rung hasta encontrar algo REAL sobre qué escribir.

    Devuelve `None` solo cuando no queda nada verificable -- y ese `None` significa "no
    escribas", nunca "improvisa". Esa es la regla que sostiene toda la cuenta: un post con un
    hecho inventado cuesta más credibilidad de la que gana un turno cumplido.

    `buscar_noticia=False` se salta el escalón de búsqueda por completo y va derecho al banco
    de contexto (o a tesis pura, si tampoco hay banco y `permisivo`). Existe por un bug real:
    en la rotación "sin tema pedido" sobre los `content_pillars` propios de un tenant
    (`agenda.territorios_desde_pilares`), CADA pilar se convertía en una búsqueda de noticias
    usando su propia etiqueta como consulta -- también los pilares que son ESCENAS de vida
    (escenas cotidianas del sector del tenant), no noticias. El día que la marca que nombra la escena
    tuvo un titular real y verificado ese mismo día (una ronda de inversión), la búsqueda lo
    encontró -- porque el titular SÍ mencionaba esa marca -- y el motor lo trató como "tu
    ÚNICA fuente de hechos" (`_bloque_fuentes`) sin que tuviera relación alguna con el ángulo
    de la escena. El post resultante pegaba dos hechos reales pero inconexos con el nombre de
    la marca como único pegamento: incoherente, aunque cada frase por separado fuera cierta.
    Aria evita esto marcando cada uno de sus pilares como `"producto"` (banco únicamente,
    jamás busca) o `"mixto"` (busca con una consulta propia, acotada al tema del pilar, nunca
    a su etiqueta libre) -- ver `acme_linkedin_content.py:69-169`. Edecán no tiene ese
    campo por pilar (`content_pillars` es una lista plana de texto libre, sin metadata), así
    que el llamador (`CrearPostLinkedInTool.run`) decide con la única señal que SÍ es genérica
    y ya vive en cualquier perfil: si la etiqueta del pilar se nombra a sí misma "noticia" (la
    convención que ya usa el propio perfil, "noticia de la semana", igual que el `pilar_id`
    "noticia_credito_ve" de Aria), se busca; el resto son escenas y van directo al banco.
    """
    # El banco acompaña a CUALQUIER escalón, no solo al último: el escritor lo recibe en su
    # prompt siempre (ver `run`), así que el auditor tiene que poder comprobar contra él
    # siempre. Un respaldo que el escritor puede usar y el auditor no puede ver es una
    # invención garantizada a ojos del auditor.
    respaldo = context_bank_text(perfil)

    if buscar_noticia:
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


def _mejor_candidato(candidatos: list[_Candidato], piso_palabras: int = 0) -> _Candidato | None:
    """El borrador más avanzado con texto entregable, o `None` si no hay ninguno.

    El filtro por `MIN_COPY_ENTREGABLE_CHARS` es la mitad de la garantía del módulo: el
    rescate es el camino por el que un muñón se colaba con más facilidad, porque su premisa
    ("mejor algo que nada") empuja a aflojar el listón justo cuando no hay que aflojarlo. Un
    rescate de dos líneas no es un borrador; es una tarjeta rota con el nombre de un post.

    `piso_palabras`, cuando esta cuenta declaró uno en su `target_length`, es la OTRA mitad de
    la garantía -- y la que faltaba. Medido en producción: de una corrida de tres intentos, el
    candidato con MÁS prioridad (pasó el editor jefe y el auditor) medía 47-74 palabras
    (recortado por el editor jefe), mientras el candidato CRUDO del escritor -- sin revisar,
    prioridad más baja -- medía 129-139 palabras, sí cumpliendo el piso de la cuenta. Elegir
    por prioridad sin mirar el largo entregaba sistemáticamente el más corto de los dos, que es
    justo lo contrario de lo que esta función existe para evitar. Por eso primero se filtra por
    quién SÍ cumple el piso de palabras (si alguno cumple) y sólo entre esos se decide por
    prioridad/recencia; si ninguno cumple, se cae al comportamiento de siempre (sólo el piso de
    caracteres) porque un post algo corto sigue siendo mejor que ningún post.
    """
    utiles = [
        candidato
        for candidato in candidatos
        if len(normalizar(str(candidato.borrador.get("texto") or ""), _PLATAFORMA))
        >= MIN_COPY_ENTREGABLE_CHARS
    ]
    if not utiles:
        return None
    if piso_palabras:
        con_largo = [
            candidato
            for candidato in utiles
            if len(
                normalizar(str(candidato.borrador.get("texto") or ""), _PLATAFORMA).split()
            )
            >= piso_palabras
        ]
        if con_largo:
            utiles = con_largo
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


def _territorios_del_perfil(perfil: dict[str, Any]) -> list[agenda.Territorio] | None:
    """Los territorios de ESTA cuenta para la rotación "sin tema pedido", o `None` para que
    `agenda.siguiente_paso` caiga a `agenda.TERRITORIOS_POR_DEFECTO`.

    Cierra el hueco real detrás de la queja "el post de hoy siempre es lo mismo": sin esto,
    la rotación ignoraba por completo los `content_pillars` que el tenant configuró en su
    perfil y SIEMPRE giraba sobre el catálogo genérico de `agenda.py` (tecnología, finanzas,
    salud...), que no tiene ninguna relación con la voz de ninguna cuenta real -- ver el
    docstring completo en `agenda.territorios_desde_pilares`.

    Perfil sin configurar o sin `content_pillars` (la mayoría de cuentas nuevas) devuelve
    `None`: comportamiento IDÉNTICO al de siempre.
    """
    pilares = perfil.get("content_pillars")
    if not isinstance(pilares, list) or not pilares:
        return None
    territorios = agenda.territorios_desde_pilares(pilares)
    return territorios or None


def _pilar_es_noticia(territorio: agenda.Territorio) -> bool:
    """¿Este territorio es el pilar que ancla en actualidad, o una escena de vida?

    Señal genérica (no depende de ninguna marca ni de ningún tenant en particular): un pilar
    que se nombra a sí mismo "noticia" -- la convención que ya usa cualquier perfil que copie
    el patrón de Aria ("noticia de la semana", `pilar_id` `noticia_credito_ve`) -- es el
    ÚNICO que debe forzar una búsqueda de titulares frescos en la rotación sin tema pedido.
    Ver el docstring de `_reunir_contexto` (parámetro `buscar_noticia`) para el bug real que
    esto evita: sin esta distinción, CUALQUIER pilar cuya etiqueta nombrara una marca real con
    noticias ese día (p. ej. una escena sobre una fintech concreta) se anclaba en lo primero
    que la búsqueda encontrara sobre esa marca, tuviera o no relación con el ángulo de la
    escena.
    """
    return "noticia" in _sin_acentos(str(territorio.get("pilar") or "")).lower()


_NUMEROS_EXTENSOS_PROHIBIDOS_RE = re.compile(
    r"\bnumeros?\b.{0,30}?\bdos\b.{0,25}?\bd[ií]gitos?\b", re.IGNORECASE
)


def _perfil_prohibe_numeros_extensos(perfil: dict[str, Any]) -> bool:
    """¿El propio perfil de esta cuenta pide "cero números de dos o más dígitos"?

    El perfil de Acme lo dice en dos campos con casi las mismas palabras ("avoid":
    "Cero números de dos o más dígitos: todo en palabras"; "voice": "Sin números de dos o más
    dígitos"), y esa instrucción SÍ llega al prompt del escritor (`_bloque_perfil` incluye
    ambos campos). El bug real es que nada la hacía cumplir: `editorial.tiene_numero_extenso`
    existe desde antes exactamente para esto, pero ningún llamador lo invocaba nunca -- un
    modelo apurado escribió "$100MM" y ningún gate lo atajó. Esta función detecta la
    instrucción en el texto LIBRE que el propio tenant configuró (sin acentos, para no fallar
    por una tilde) y activa el gate SOLO para las cuentas que de verdad lo pidieron: el resto
    conserva el comportamiento de siempre (números reales permitidos, regla 4 de
    `REGLAS_LINKEDIN` de sobra para ellas).
    """
    texto = " ".join(
        str(perfil.get(campo) or "") for campo in ("avoid", "voice", "notes")
    )
    return bool(_NUMEROS_EXTENSOS_PROHIBIDOS_RE.search(_sin_acentos(texto)))


_MARCA_UNA_VEZ_RE = re.compile(
    r"\b([A-ZÁÉÍÓÚÑ][\wÀ-ÿ]{2,})\b[^.]{0,40}?\b(?:entra|aparece|se menciona)\b[^.]{0,20}?"
    r"\buna\s+(?:sola\s+)?vez\b"
)


def _marca_para_una_sola_mencion(perfil: dict[str, Any]) -> str:
    """El nombre de marca que el perfil pide mencionar UNA sola vez, o `""` si no aplica.

    Doble señal, las dos genéricas y ninguna cableada a una marca en particular:

    1. El NOMBRE sale de `closing_url` -- el único lugar donde un perfil declara su propia
       marca de forma ESTRUCTURADA (un dominio), no en prosa libre que habría que adivinar.
    2. El LÍMITE ("una sola vez") sólo se activa si el propio perfil lo pide en su texto
       libre (`purpose`/`voice`/`avoid`/`notes`). Sin esta segunda condición, cualquier
       cuenta con `closing_url` configurado (la mayoría de páginas de organización) quedaría
       limitada a nombrarse a sí misma una sola vez aunque nunca lo hubiera pedido -- y
       mencionar la marca dos veces es perfectamente normal para otra cuenta cualquiera.

    El defecto real que esto ataca: el perfil de Acme dice literalmente "donde Acme
    entra una sola vez, de forma natural" (campo `purpose`), y un borrador real la mencionó
    dos ("Acme ofrece productos como el producto" ... "ofrece Acme Pass") sin que nada lo
    marcara. Devuelve la marca en minúsculas para comparar sin distinguir mayúsculas.
    """
    cierre = str(perfil.get("closing_url") or "").lower()
    dominio = re.search(r"([a-z0-9-]{3,})\.[a-z]{2,}(?:/|\s|$)", cierre)
    if not dominio or dominio.group(1) == "www":
        return ""
    texto_libre = " ".join(
        str(perfil.get(campo) or "") for campo in ("purpose", "voice", "avoid", "notes")
    )
    if not _MARCA_UNA_VEZ_RE.search(texto_libre):
        return ""
    return dominio.group(1)


def _piso_palabras_del_perfil(perfil: dict[str, Any]) -> int:
    """El mínimo de palabras que el propio `target_length` de esta cuenta declaró, o `0` si
    el campo no trae ninguna cifra (la mayoría de perfiles, que no fijan un largo propio).

    Existe porque el piso de entrega del motor (`MIN_COPY_ENTREGABLE_CHARS`, ~25-30 palabras
    en español) es un mínimo GENÉRICO para que la tarjeta no salga rota, no el objetivo real
    de ninguna cuenta en particular. Una cuenta cuyo `target_length` pide "entre 110 y 140
    palabras, nunca menos de 100" puede entregar un post de 37-74 palabras que pasa de sobra
    el piso genérico (le sobran caracteres) y aun así incumple lo que esa cuenta pidió por
    completo -- exactamente lo que le pasó a Acme. Se toma el MENOR número que aparezca
    en el texto (sirve tanto para "entre 110 y 140" como para un "nunca menos de 100"
    explícito): un piso conservador es preferible a uno que ya se pasó de la raya.
    """
    numeros = [int(n) for n in re.findall(r"\d+", str(perfil.get("target_length") or ""))]
    return min(numeros) if numeros else 0


# Piso genérico que ve el escritor cuando la cuenta no declaró un `target_length` propio (ver
# `_SISTEMA_ESCRITOR`). Se reutiliza aquí para que el editor jefe reciba la MISMA instrucción de
# largo que ya recibió el escritor -- antes el escritor apuntaba a 170-230 y el editor jefe no
# sabía que existía ningún número, así que "pulir" sólo tenía presión para acortar.
_REQUISITO_LARGO_GENERICO = "entre 170 y 230 palabras, nunca menos de 160"


def _requisito_largo_editor(perfil: dict[str, Any]) -> str:
    """El largo que se le exige al editor jefe (`auditoria.pulir_borrador`), en el mismo texto
    que ya recibe el escritor.

    Prioriza el `target_length` LITERAL del perfil (ya trae el rango completo redactado por el
    propio tenant, p. ej. "Entre 110 y 140 palabras... Nunca menos de 100") en vez de recalcularlo
    desde `_piso_palabras_del_perfil`: ese helper sólo extrae el mínimo para un gate determinista
    y perdería el máximo y el fraseo, que sí le sirven al editor para no sobrecorregir hacia
    arriba. Sin `target_length` configurado (la mayoría de cuentas), usa el mismo genérico que ya
    ve el escritor -- nunca deja al editor sin ninguna referencia de largo.
    """
    target = str(perfil.get("target_length") or "").strip()
    return target or _REQUISITO_LARGO_GENERICO


def _con_cierre_garantizado(copy: str, perfil: dict[str, Any]) -> str:
    """Aplica el cierre del DESTINO (p. ej. la URL de una página de organización) de forma
    DETERMINISTA, después de que el texto ya pasó todos los gates.

    Puerto del cierre garantizado de Aria para su motor de LinkedIn de Acme
    (`acme_linkedin_content.py`): "la URL se agrega DESPUÉS de todos los filtros
    (auditor, delatores, guardrails)... queda garantizada aunque el modelo la olvide". Acá
    el equivalente genérico y por-tenant es `perfil["closing_url"]` -- nunca una marca fija
    en este repo: un destino sin este campo configurado (la mayoría) no ve ningún cambio.

    No es solo una instrucción de prompt (`calls_to_action` ya podía pedirle al escritor
    "cierra con nuestra URL", y un modelo la olvida con la misma facilidad con la que
    olvida cualquier otra instrucción de una lista larga). Esto la garantiza sin depender
    de que el modelo se acuerde, y sin duplicarla si el propio texto ya la menciona.
    """
    cierre = str(perfil.get("closing_url") or "").strip()
    if not cierre:
        return copy
    if cierre.lower() in copy.lower():
        return copy
    return f"{copy.rstrip()}\n\n{cierre}"


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
    '"texto": "<el post completo, tal cual se publicaría. REQUISITO DE LARGO (obligatorio): '
    "entre 170 y 230 palabras, y NUNCA menos de 160 -- SALVO que el perfil de la cuenta (más "
    "abajo, 'Largo objetivo de ESTA cuenta') traiga su propio rango: ESE manda siempre sobre "
    "éste, incluido su propio mínimo, aunque sea más corto. 'No rellenes para llegar a un "
    "mínimo' (más arriba) nunca significa ignorar el mínimo que un perfil declaró explícito: "
    "significa no meter frases vacías para llegar ahí -- desarrolla la escena con otro "
    "momento o una consecuencia concreta, nunca relleno. Sin rango propio en el perfil, un "
    "post de ~120 palabras se siente incompleto y sin desarrollo, se rechaza. Repártelas en "
    "3 o 4 párrafos separados por "
    "una línea en blanco. La PRIMERA línea es un gancho concreto que da intriga y hace "
    "querer seguir leyendo (un hecho, una tensión, una cifra sorprendente), NUNCA un "
    "resumen del titular. El cuerpo desarrolla UNA idea con criterio en DOS párrafos "
    "completos (no una frase cada uno), y el cierre deja una pregunta o una consecuencia "
    "que invite a pensar. Un post de tres frases sueltas se siente vacío: dale desarrollo "
    'y ritmo, como escribiría una persona con oficio>", '
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
) -> tuple[dict[str, Any] | None, str]:
    """Una pasada del escritor. `correcciones` (vacío en el primer intento) trae el problema
    EXACTO que rechazó el intento anterior: decirle "corrige esto" con el motivo concreto
    funciona; volver a pedir lo mismo tal cual solo repite el mismo error.

    Devuelve `(borrador, motivo_del_rechazo)`. El motivo viaja de vuelta -- y no lo escribe el
    llamador -- porque sólo aquí se sabe QUÉ falló: el sobre (no vino JSON) o el contenido (vino
    prosa buena pero demasiado corta). El llamador escribía siempre "no devolviste el JSON",
    incluso cuando el modelo había devuelto prosa perfecta de 120 caracteres, y así se gastaba
    un intento arreglando un formato que no estaba roto sin pedirle nunca lo único que hacía
    falta: que escribiera más.
    """
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
        return None, _CORRECCION_SIN_RESPUESTA
    datos = _extraer_json(crudo)
    if isinstance(datos, dict) and str(datos.get("texto") or "").strip():
        return datos, ""
    # El sobre falló, no el contenido: acepta el post en PROSA.
    return _post_desde_prosa(crudo)


def _post_desde_prosa(crudo: str) -> tuple[dict[str, Any] | None, str]:
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
    # Un JSON no es prosa, y se mira ANTES que el largo. Si el modelo SÍ devolvió el sobre pero
    # con `texto` vacío o mal nombrado, tomar la respuesta cruda como post publicaría llaves y
    # comillas. El orden importa para el MOTIVO que se devuelve: un sobre roto y corto es un
    # problema de formato, no de longitud, y decirle "escribe más largo" a quien mandó JSON roto
    # no arregla nada.
    if texto.lstrip().startswith(("{", "[")) or '"texto"' in texto:
        return None, _CORRECCION_SIN_JSON
    # Un fragmento corto no es un post: probablemente sea una disculpa del modelo o una
    # pregunta de vuelta. Mejor reintentar que empaquetar eso. Se mide contra el MISMO piso de
    # entrega y no contra uno más laxo: aceptar aquí un texto que después no se va a poder
    # entregar solo cambia dónde muere el turno, y encima gasta el editor y el auditor encima
    # de algo condenado. Lo que sí cambió es lo que se le dice al escritor: aquí hubo prosa, y
    # el problema fue el LARGO -- pedirle el JSON habría sido mandarlo a arreglar lo que ya
    # estaba bien.
    if len(texto) < MIN_COPY_ENTREGABLE_CHARS:
        return None, _correccion_texto_corto(len(texto))
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
    }, ""


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


def _lo_pidio_una_persona(args: dict[str, Any]) -> bool:
    """`True` solo si el llamador DECLARA que hay alguien esperando este post.

    Fail-closed a propósito, y la asimetría del daño manda: si el autopiloto se colara como
    "alguien lo pidió", publicaría en la página de una empresa un post que el editor jefe
    había decidido saltarse — y ahí no hay nadie mirando para atajarlo. Al revés el costo es
    menor y visible: la persona ve un turno saltado y lo vuelve a pedir.

    Por eso quien afirma es el que sabe: el handler del job, que tiene el `origen` y el
    `conversation_id` del pedido (ver `create_linkedin_post._lo_pidio_una_persona`). Un
    llamador que no diga nada conserva el comportamiento de siempre: post recurrente, con
    derecho a saltarse el turno.
    """
    return bool(args.get("lo_pidio_una_persona"))


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
            return ToolResult(content=_SIN_MODELO, data={"fallo": FALLO_SIN_MODELO})

        perfil = await get_editorial_profile_for_destination(ctx, _PLATAFORMA, target)

        # Clave de storage del estado de rotación, propia de este DESTINO -- nunca la
        # plataforma pelada (`_PLATAFORMA`). Antes, `personal` y cualquier página de
        # organización (p. ej. `acme`) guardaban su territorio/formato/foco bajo la
        # MISMA clave ("linkedin"), así que el reloj de una cuenta avanzaba con cada post
        # que la OTRA generaba entremedio: un round-robin de 24 pilares de una página nunca
        # completaba las 24 posiciones antes de repetirse, porque el índice compartido
        # llegaba desincronizado. Mismo criterio que ya usa
        # `get_editorial_profile_for_destination` para el perfil (ver `marcas.
        # editorial_profile_key`); acá se aplica al estado de rotación, que vivía aparte.
        clave_agenda = editorial_profile_key(_PLATAFORMA, target)

        # Rotación editorial (`edecan_creative.agenda`): la FORMA argumental rota siempre, así
        # dos posts seguidos no salen con la misma arquitectura. El TERRITORIO solo manda
        # cuando el usuario no dijo tema -- si lo dijo, el tema es suyo y no se le cambia.
        #
        # `territorios_del_perfil` es lo que hace que ESE territorio, cuando manda, sea uno de
        # los `content_pillars` reales del tenant y no el catálogo genérico del módulo (ver
        # `_territorios_del_perfil`); `None` (perfil sin pilares configurados) preserva el
        # comportamiento de siempre.
        estado = await get_agenda_state(ctx, clave_agenda)
        territorios_del_perfil = _territorios_del_perfil(perfil)
        paso = agenda.siguiente_paso(estado, territorios=territorios_del_perfil)
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
            # El usuario pidió esto con todas sus letras: siempre vale la pena buscar algo
            # fresco y verificado sobre lo que pidió, sea cual sea el territorio de rotación.
            buscar_noticia = True
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
            # SÓLO EL PILAR "NOTICIA" ANCLA EN ACTUALIDAD; EL RESTO SON ESCENAS DE VIDA (ver
            # el docstring de `_reunir_contexto`, parámetro `buscar_noticia`, para el bug real
            # -- un post que pegó una ronda de inversión ajena con el argumento del producto
            # porque la rotación cayó en una escena cuyo NOMBRE coincidía con una marca, y la
            # búsqueda, literal, encontró la ronda de inversión de esa marca ese mismo día.
            # Sin `territorios_del_perfil` (catálogo genérico de `agenda.py`: tecnología,
            # finanzas...) el comportamiento no cambia -- esos territorios SÍ están pensados
            # para buscar siempre, con consultas temáticas amplias, no con el nombre propio de
            # una escena.
            buscar_noticia = territorios_del_perfil is None or _pilar_es_noticia(
                paso.territorio
            )

        # El reloj avanza aunque este turno termine sin post: si no, una combinación que falla
        # dejaría a la cuenta reintentándola para siempre. El historial de cooldown, en cambio,
        # solo lo escribe un borrador que de verdad salió (`empaquetar_borrador_social`).
        await save_agenda_state(ctx, clave_agenda, estado_siguiente)

        # `permisivo` solo cuando el tema lo pidió el usuario: ahí el editor jefe no puede
        # vetar por gusto ("poco interesante") porque la persona ya decidió que le interesa.
        # En el post recurrente sin tema pedido sí puede saltarse el turno -- regla "no forzar
        # un post" de `REGLAS_LINKEDIN`. El auditor de hechos es estricto en ambos casos.
        #
        # Con el port de los gates blandos, este flag ya llega a los TRES sitios que podían
        # matar el turno (la batería determinista, el editor jefe y el escalón sin fuente), no
        # solo a `revisar_calidad` como antes.
        # `permisivo` no significa "dio tema": significa QUE ALGUIEN ESTÁ ESPERANDO ESTE
        # POST. Atarlo solo a `tema` confundía dos cosas distintas y salía caro: alguien
        # pedía "un post para la página" sin nombrar tema, el editor jefe vetaba los tres
        # intentos por gusto ("poco interesante" es un veto legítimo en el post recurrente),
        # y en vez de un borrador llegaba el texto crudo del escritor con el sello "Sin
        # revisar" y sin botón de publicar. La regla "no forzar un post" de Aria existe
        # para el CRON, donde saltarse un turno no le cuesta nada a nadie porque nadie está
        # mirando; no para alguien que lo pidió y está esperando.
        #
        # `lo_pidio_una_persona` llega del handler, que lo deduce de `payload["origen"]`
        # (ver `create_linkedin_post`): el autopiloto se marca a sí mismo y conserva el
        # derecho a saltarse el turno; todo lo demás se considera pedido por alguien.
        permisivo = bool(tema) or _lo_pidio_una_persona(args)

        max_dias = _acotar(args.get("max_dias"), minimo=1, maximo=30, default=_MAX_DIAS_DEFECTO)
        contexto = await _reunir_contexto(
            perfil,
            consulta,
            max_dias=max_dias,
            excluir_terms=excluir,
            permisivo=permisivo,
            buscar_noticia=buscar_noticia,
        )
        if contexto is None:
            return ToolResult(
                content=_sin_fuente(consulta, max_dias), data={"fallo": FALLO_SIN_FUENTE}
            )

        llamar_modelo = _llamar_modelo_editorial(ctx)
        bloque_perfil = _bloque_perfil(perfil)
        # El banco de contexto viaja SIEMPRE, haya o no noticia: es lo único que autoriza a
        # atribuirle un hecho de vida o de operación al autor, y sin él en el prompt el
        # escritor solo tiene dos salidas malas -- inventarse una anécdota, o escribir sin
        # nada propio. Trae su propia regla anti-autobiografía
        # (`social.build_context_bank_prompt_block`), condicionada a si este destino es el
        # perfil personal o una página de organización: una organización ES la marca de su
        # propio banco, así que ahí no aplica la prohibición de nombrar "proyectos
        # personales" (pensada para que el perfil personal no se autopromocione).
        es_personal = target in (None, PERSONAL_DESTINATION_ID)
        bloque_banco = build_context_bank_prompt_block(perfil, es_personal=es_personal)
        temas_recientes = _bloque_temas_recientes(historial)
        # Cooldown ESTRUCTURAL (no de tema): qué FORMA de post no puede repetirse porque el
        # anterior ya la usó. Es también el lugar correcto donde vive la prohibición de cerrar
        # con pregunta -- condicionada al historial, como en Aria, y no absoluta.
        prohibidas = firmas_prohibidas_recientes(historial)
        cooldown_estructura = instruccion_cooldown_estructura(prohibidas)
        recientes_texto = _bloque_borradores_recientes(historial)
        # Tres reglas por-tenant que el perfil configuró en texto libre, no en código: se
        # calculan UNA vez (el perfil no cambia entre intentos) y se aplican en el re-gate
        # final (PASO 4). Ver los docstrings de cada helper para el bug real que arregla cada
        # una: números escritos en dígitos que la cuenta pidió en palabras, la marca propia
        # mencionada más de una vez, y un post que cumple el piso de caracteres genérico del
        # motor pero no el largo en palabras que esta cuenta en particular declaró.
        numeros_prohibidos = _perfil_prohibe_numeros_extensos(perfil)
        marca_una_vez = _marca_para_una_sola_mencion(perfil)
        piso_palabras = _piso_palabras_del_perfil(perfil)
        # El mismo largo que ve el escritor, ahora también para el editor jefe (PASO 1): sin
        # esto el editor "pulía" sin saber que existía un objetivo de largo y sólo tenía presión
        # para acortar. Ver el docstring de `_requisito_largo_editor` para el defecto medido.
        requisito_largo_editor = _requisito_largo_editor(perfil)

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
            escrito, motivo_escritura = await _escribir(
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
            borrador = escrito or {}
            texto = normalizar(str(borrador.get("texto") or ""), _PLATAFORMA)
            if not texto:
                # El motivo lo trae `_escribir`, que es quien sabe si falló el sobre o el largo.
                # Antes aquí había un literal fijo ("no devolviste el JSON") que se le mandaba
                # también a quien había devuelto prosa buena pero corta.
                correcciones = [motivo_escritura or _CORRECCION_SIN_JSON]
                continue
            borrador["texto"] = texto
            # Se guarda ANTES de que el editor jefe (PASO 1, abajo) reescriba `texto`: es la
            # única referencia real de cuánto escribió el escritor, y es lo que permite medir si
            # el recorte que viene después lo hizo el editor (ver el anti-recorte más abajo).
            texto_crudo_escritor = texto
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
                requisito_largo=requisito_largo_editor,
            )
            if pulido is None:
                correcciones = [
                    "El editor jefe descartó el borrador: no encontró un ángulo concreto que "
                    "sostener sin inventar hechos."
                ]
                continue
            borrador = pulido
            texto = normalizar(str(pulido["texto"]), _PLATAFORMA)

            # ANTI-RECORTE DEL EDITOR JEFE (backstop determinista). `requisito_largo_editor`
            # (arriba) ya le dice al editor explícitamente que no acorte, pero un modelo puede
            # ignorar una instrucción de prompt -- medido en producción: el editor jefe recortó
            # candidatos de 129-139 palabras a 47-74, muy por debajo del piso de la cuenta,
            # incluso cuando el crudo del escritor ya lo cumplía de sobra. Mismo criterio que el
            # anti-muñón del auditor (PASO 2, más abajo): si el crudo SÍ llegaba al piso y lo que
            # devolvió el editor NO, no se seguía adelante con un texto que la propia cuenta
            # rechazaría -- se guarda el pulido como candidato de rescate y se reintenta el ciclo
            # completo con la corrección explícita.
            if (
                piso_palabras
                and len(texto_crudo_escritor.split()) >= piso_palabras
                and len(texto.split()) < piso_palabras
            ):
                correcciones = [
                    _correccion_editor_recorto(
                        len(texto_crudo_escritor.split()), len(texto.split()), piso_palabras
                    )
                ]
                candidatos.append(_Candidato(_PRIORIDAD_PULIDO, dict(borrador), correcciones))
                continue

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
            # ANTI-MUÑÓN. El auditor no solo veta: también REESCRIBE, y su reescritura puede
            # dejar en pie mucho menos de lo que recibió. Eso a veces es correcto (recortar
            # deducciones que el titular no sostiene) y a veces es una amputación. Se distingue
            # con DOS medidas, porque cada una sola tiene un hueco por donde se coló un fallo
            # real:
            #
            # - RELATIVA: de un borrador sustancial quedó menos de la fracción mínima. Detecta
            #   el destripe de un post grande sin castigar a uno legítimamente corto que el
            #   auditor respetó entero. Su hueco: si el escritor produjo poco, no llega a
            #   medirse.
            # - ABSOLUTA: lo que quedó no llega al piso de ENTREGA. Esta es la que cierra el
            #   hueco anterior y la que faltaba el día del bug -- un muñón de 118 caracteres
            #   salido de un borrador de ~260 no disparaba la relativa (no llegaba a 400) y se
            #   entregaba como si fuera un post.
            #
            # En los dos casos la consecuencia es la misma y es la importante: NO se devuelve el
            # muñón. Se guarda como candidato el borrador PULIDO (el cuerpo completo que aprobó
            # el editor jefe, nunca el recorte) y se reintenta con la corrección explícita en el
            # prompt, que es el mecanismo que de verdad arregla esto -- el escritor vuelve a
            # escribir sabiendo qué falló, en vez de que alguien más abajo suba o baje un umbral.
            recorte_desproporcionado = (
                len(texto) >= _MIN_LARGO_PARA_MEDIR_RECORTE
                and len(auditado) < _FRACCION_MIN_TRAS_AUDITOR * len(texto)
            )
            munon = len(auditado) < MIN_COPY_ENTREGABLE_CHARS
            if auditado and (recorte_desproporcionado or munon):
                # A QUIÉN se le echa la culpa, que es lo que decide si el reintento sirve de
                # algo. Si el auditor devolvió prácticamente lo mismo que recibió, no recortó
                # nada: el texto ya venía corto del escritor, y pedirle que "ancle cada
                # afirmación en la fuente" no lo va a alargar. Lo único accionable ahí es
                # decirle que escriba un post completo.
                el_auditor_recorto = len(auditado) <= _FRACCION_RECORTE_ATRIBUIBLE * len(texto)
                correcciones = [
                    _correccion_auditor_recorto(len(texto), len(auditado))
                    if el_auditor_recorto
                    else _correccion_texto_corto(len(auditado))
                ]
                candidatos.append(_Candidato(_PRIORIDAD_PULIDO, dict(borrador), correcciones))
                continue
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
                    # Mismo anti-muñón que el PASO 2, en la RE-auditoría: una segunda pasada
                    # que devuelve un muñón no es una corrección, es una amputación, y se trata
                    # igual que si el auditor no hubiera devuelto nada. Sin esto, la reparación
                    # de primera persona era una puerta trasera al piso de entrega: el texto
                    # entraba completo y salía en dos líneas, y el gate de abajo lo único que
                    # podía hacer era tirar el intento sin quedarse con el cuerpo bueno.
                    if re_auditado and len(re_auditado) < MIN_COPY_ENTREGABLE_CHARS:
                        re_auditado = ""
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
                # En una PÁGINA DE ORGANIZACIÓN el banco no es la identidad privada de
                # nadie: es información pública de la marca. Prohibir sus nombres propios
                # ahí bloquea justo aquello de lo que la página existe para hablar — su
                # propio nombre y su país. Ver `editorial.revisar`.
                banco_es_identidad_privada=target in (None, PERSONAL_DESTINATION_ID),
                # Sólo se activa para la cuenta que de verdad lo pidió en su propio perfil
                # (ver `_perfil_prohibe_numeros_extensos`); el resto no cambia.
                prohibir_numeros_extensos=numeros_prohibidos,
            )
            if marca_una_vez:
                apariciones = len(re.findall(rf"\b{re.escape(marca_una_vez)}\b", texto, re.I))
                if apariciones > 1:
                    correcciones = [
                        *correcciones,
                        f"La marca aparece {apariciones} veces en el texto; el perfil de esta "
                        "cuenta pide que entre una sola vez, de forma natural. Deja la mención "
                        "en el momento donde de verdad cambia la escena y quita las demás (usa "
                        "'el buró', 'la plataforma' o simplemente no la repitas).",
                    ]
            if len(texto) < MIN_COPY_ENTREGABLE_CHARS:
                correcciones = [*correcciones, _correccion_texto_corto(len(texto))]
            elif piso_palabras and len(texto.split()) < piso_palabras:
                # El piso de CARACTERES ya se cumplió (si no, hubiera caído en la rama de
                # arriba); lo que falta es el piso de PALABRAS que esta cuenta en particular
                # declaró en `target_length` -- ver `_piso_palabras_del_perfil` para el bug
                # real (posts de 37-74 palabras que pasaban de sobra los ~150 caracteres
                # genéricos sin acercarse a las 100-140 palabras que pedía el perfil).
                correcciones = [
                    *correcciones,
                    f"El texto quedó en {len(texto.split())} palabras; el perfil de esta cuenta "
                    f"pide un mínimo de {piso_palabras}. Desarrolla más la escena -- otro "
                    "momento concreto, una consecuencia distinta -- en vez de rellenar con "
                    "frases vacías.",
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
                # El auditor de hechos VETÓ este borrador y seguimos igual porque el tema lo
                # pidió la persona. Que ese dato viaje en una bandera y no sólo dentro de una
                # frase es lo que permite que quien lo entregue actúe distinto (p. ej. no
                # ofrecer "publicar de un toque" en la página de una empresa).
                borrador["_sin_auditar"] = True
            break

        if not copy:
            # Todos los intentos rechazados. Se entrega el MEJOR que haya quedado, con aviso,
            # en vez de un mensaje vacío: un borrador con "revísalo antes de publicar" es
            # infinitamente más útil que "escribí 3 veces y ninguna versión pasó".
            elegido = _mejor_candidato(candidatos, piso_palabras)
            if elegido is not None:
                borrador = elegido.borrador
                copy = normalizar(str(borrador.get("texto") or ""), _PLATAFORMA)
                motivos = elegido.problemas
                borrador["_aviso_rescate"] = (
                    "Este borrador no pasó el control editorial automático "
                    f"({motivos[0] if motivos else 'motivo no reportado'}); "
                    "revísalo con calma antes de publicarlo."
                )
                # ¿Los HECHOS de este texto los llegó a mirar el auditor? Sólo si el candidato
                # llegó hasta el re-gate final (`_PRIORIDAD_CASI`). Los otros dos escalones son
                # justamente "el auditor lo vetó" y "el auditor ni lo vio", y ahí lo que se
                # entrega es el cuerpo previo a la auditoría. La distinción no es cosmética: de
                # ella depende que la card ofrezca publicar de un toque o sólo copiar.
                borrador["_sin_auditar"] = elegido.prioridad < _PRIORIDAD_CASI
            else:
                # Fin de la línea sin borrador. Se devuelve el código de fallo ADEMÁS del
                # texto: el `content` habla con el modelo del chat (nombra herramientas y
                # parámetros), y quien corre esto en segundo plano necesita traducirle el
                # motivo a la persona sin repetirle jerga interna. Ver `data["fallo"]`.
                return ToolResult(
                    content=_no_publicable(correcciones), data={"fallo": FALLO_NO_PUBLICABLE}
                )

        # LA GARANTÍA, escrita una vez y en el único punto por el que pasan TODOS los caminos
        # que entregan: de aquí para abajo se empaqueta un borrador, y nada que no llegue al
        # piso puede seguir. Hoy es redundante -- el gate del bucle y `_mejor_candidato` ya
        # filtran --, y esa redundancia es el punto: si mañana alguien agrega un quinto camino
        # de rescate y se olvida del piso (que es exactamente el tipo de descuido que produjo
        # el bug original), el turno termina con un fallo honesto en vez de con una tarjeta
        # rota y un usuario esperando algo que nunca llega.
        if len(copy) < MIN_COPY_ENTREGABLE_CHARS:
            logger.warning(
                "Un camino de entrega dejó pasar un copy de %d caracteres, por debajo del piso "
                "de %d. Se corta acá: es un fallo del motor, no del texto.",
                len(copy),
                MIN_COPY_ENTREGABLE_CHARS,
            )
            return ToolResult(
                content=_no_publicable(correcciones), data={"fallo": FALLO_NO_PUBLICABLE}
            )

        # El cierre del destino (p. ej. la URL de una página de organización) se aplica ACÁ:
        # después de todos los gates y del rescate, antes de empaquetar. Ver
        # `_con_cierre_garantizado` -- sin `closing_url` configurado (la mayoría de cuentas)
        # esto no cambia una sola letra.
        copy = _con_cierre_garantizado(copy, perfil)

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
            # Mismo destino, misma clave -- ver el comentario junto a `clave_agenda` más
            # arriba. Sin esto, este registro final (el que SÍ marca el tema como usado en
            # el historial, `social.empaquetar_borrador_social`) volvía a escribir bajo la
            # clave pelada y deshacía el aislamiento que se acaba de guardar arriba.
            agenda_key=clave_agenda,
        )
        # `empaquetar_borrador_social` también devuelve errores duros (un copy más largo que el
        # límite de la plataforma) sin `data`. A esos no se les pega una nota que hablaría de un
        # borrador que no existe -- pero SÍ se les pone código de fallo, porque sin él quien
        # entrega esto en segundo plano sólo puede decir "no me salió nada", que es falso y
        # además es el motivo menos accionable posible: lo que pasó es que el post está escrito
        # y quedó largo, y eso se arregla pidiendo que lo corte.
        if not isinstance(resultado.data, dict):
            fallo_duro = (
                FALLO_COPY_LARGO
                if len(copy) > PLATFORMS[_PLATAFORMA].max_chars
                else FALLO_NO_PUBLICABLE
            )
            logger.warning(
                "El empaquetado devolvió un error duro (%d chars, fallo=%s): %s",
                len(copy),
                fallo_duro,
                resultado.content,
            )
            return ToolResult(content=resultado.content, data={"fallo": fallo_duro})

        resultado.data["origen_fuente"] = contexto.origen
        resultado.content = f"{resultado.content} {_NOTA_ORIGEN[contexto.origen]}"
        # Rescate: si el borrador entró por el camino de "ningún intento pasó pero
        # entrego el mejor que sí pasó los gates deterministas", ese aviso va
        # ADELANTE del content, para que la persona lo vea antes que la nota
        # editorial.
        #
        # Y va TAMBIÉN en `data`, que es el arreglo de un agujero serio: quien entrega el post
        # en segundo plano ignora `content` a propósito (ahí el tool le habla al modelo del
        # chat, con nombres de herramientas adentro), así que el aviso se perdía entero por ese
        # camino. El resultado neto era el peor posible para la página de una empresa: llegaba
        # al teléfono un borrador que el auditor de hechos había vetado, con botón de publicar
        # y sin una sola palabra de advertencia. `sin_auditar` es la bandera que permite además
        # degradar ese botón.
        aviso = borrador.get("_aviso_rescate")
        if aviso:
            resultado.content = f"{aviso} {resultado.content}"
            resultado.data["aviso"] = aviso
            resultado.data["sin_auditar"] = bool(borrador.get("_sin_auditar"))
        return resultado
