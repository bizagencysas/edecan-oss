"""Auditoría anti-invención y control de calidad editorial (segunda pasada) para
contenido social generado por Edecán (`ARCHITECTURE.md` §10.14).

Este módulo es GENÉRICO: ninguna regla aquí menciona una persona, una empresa, un
país o una cuenta en particular. Todo lo específico de un tenant (su voz, su banco
de contexto real, sus temas prohibidos) vive en su perfil editorial por-tenant --
ver `edecan_creative.social.get_editorial_profile` -- nunca en este archivo.

Expone controles independientes, portados y generalizados de un pipeline
editorial propio. Los dos primeros REPARAN el borrador; los dos últimos lo
JUZGAN, y son los que había que dejar de usar como primera puerta:

- `pulir_borrador`: el editor jefe que REESCRIBE el borrador completo (texto,
  tema, ángulo, promesa y copy visual) con el prompt humanizador de
  `editorial.PROMPT_EDITOR_HUMANIZADOR`, y remata con una pasada dedicada a
  quitar los delatores de plantilla IA que hayan quedado. Es la pieza que
  convierte la mayoría de los rechazos de estilo en correcciones.
- `reparar_delatores`: esa segunda pasada, expuesta aparte por si un llamador la
  necesita sola.
- `reescribir_sin_primera_persona`: el último cinturón contra la autobiografía.
- `auditar_hechos`: verifica cada cifra, fecha y afirmación fechable de un texto
  contra las fuentes que se le pasaron, y lo corrige o lo veta si no se sostiene.
  Falla cerrado (fail-closed): ante la duda, se descarta antes que dejar pasar
  una invención.
- `revisar_calidad`: un editor jefe que sólo JUZGA -- devuelve `(publicable,
  motivo)` -- y puede rechazar un borrador correcto pero mediocre o genérico
  ("saltarse un slot protege mejor la cuenta que publicar algo artificial").
  Sigue siendo el control adecuado para un texto que el usuario DICTÓ y que la
  herramienta no puede reescribir en su nombre (`crear_contenido_social`); para
  un texto que el motor escribió él mismo, `pulir_borrador` es el correcto,
  porque ahí sí se puede arreglar en vez de devolver un turno perdido. Soporta
  un modo `permisivo` para cuando el propio usuario pidió explícitamente el
  tema: ahí se desactiva el rechazo por gusto editorial (interesante/soso, tema
  amplio, etc.) y solo sobreviven los rechazos por reglas duras (el texto no se
  sostiene, está vacío o es ilegible).

Ninguna de las dos llama a un proveedor de modelo concreto: ambas reciben
`llamar_modelo` por inyección de dependencia (mismo criterio que
`edecan_creative.providers.ImageProvider` para imágenes) -- quien las use decide
qué modelo real invocar, con qué credencial y bajo qué tenant.

--------------------------------------------------------------------------------
LECCIÓN CRÍTICA A PORTAR (aprendida a la mala en el pipeline original: un
borrador reintrodujo un rango de puntaje y cerró con una pregunta retórica
FORMULA porque el auditor factual reescribió el texto con un prompt genérico
que no conocía esas reglas de estilo):

`auditar_hechos` y, en menor medida, `revisar_calidad` piden al modelo que
REESCRIBA el texto. Cualquier gate determinista que ya se le aplicó a la
versión ANTERIOR a estas funciones (primera persona, cierre en pregunta,
plantillas delatoras de IA -- ver `edecan_creative.editorial.revisar`) NO
protege el texto que estas funciones devuelven: la reescritura puede
reintroducir, sin querer, una violación que ya se había limpiado. Un texto que
pasó los gates deterministas ANTES de pasar por este módulo puede perfectamente
NO pasarlos DESPUÉS.

Por eso los gates deterministas deben RE-APLICARSE sobre el texto FINAL,
después de este módulo, nunca solo una vez al principio. `finalizar_post` (más
abajo) es la orquestación de referencia que deja esto ya resuelto: encadena
`revisar_calidad` -> `auditar_hechos` -> vuelve a correr
`edecan_creative.editorial.revisar` sobre el resultado. Un llamador que arme su
propia orquestación en vez de usar `finalizar_post` debe reproducir ese
re-chequeo final él mismo.
--------------------------------------------------------------------------------
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from .editorial import (
    PROMPT_EDITOR_HUMANIZADOR,
    _tiene_primera_persona,
    delatores_de_estilo,
    normalizar_visual,
    revisar_visual,
)
from .editorial import normalizar as _normalizar_texto
from .editorial import revisar as _revisar_gates_deterministas

logger = logging.getLogger(__name__)

__all__ = [
    "Fuente",
    "LlamarModelo",
    "auditar_hechos",
    "pulir_borrador",
    "reparar_delatores",
    "reescribir_sin_primera_persona",
    "revisar_calidad",
    "finalizar_post",
]

LlamarModelo = Callable[[list[dict[str, str]]], Awaitable[str]]
"""Firma inyectada para hablar con un modelo de lenguaje.

Recibe una lista de mensajes de chat (``{"role": "system"|"user", "content": "..."}``,
el mismo shape mínimo que `edecan_llm.base.ChatMessage`) y devuelve el texto de la
respuesta ya extraído (sin streaming, sin tool calls, sin reintentos -- eso es
responsabilidad de quien implemente la función real). Este módulo nunca importa
un proveedor concreto (`edecan_llm`, OpenAI, Anthropic, Ollama...): quien lo use
decide qué modelo invocar, con qué credencial y bajo qué tenant.
"""

Fuente = Mapping[str, Any]
"""Una fuente citable: al menos ``title`` y ``url``; ``snippet`` opcional aporta el
fragmento de texto real que sostiene los hechos. Mismo shape que ya acepta
`edecan_creative.social.CrearContenidoSocialTool` (campo ``fuentes``), para que
el llamador no tenga que reformatear nada."""

# Límites defensivos sobre lo que se inyecta en un prompt: un caller no
# controlado (fuentes larguísimas, un texto pegado por error) nunca debe volar
# el contexto del modelo ni el costo de la llamada.
_MAX_CONTEXTO_CHARS = 3500
_MAX_TEXTO_CHARS = 1800
# Mínimo para que un texto sea EVALUABLE. No es el mínimo para ENTREGARLO, que es bastante más
# alto y lo fija quien orquesta (`redaccion.MIN_COPY_ENTREGABLE_CHARS`). Los dos números miden
# cosas distintas a propósito: acá basta con que haya frases que auditar; allá tiene que haber
# un post que una persona pueda publicar sin que la tarjeta se vea rota.
_MIN_TEXTO_CHARS = 50
_MAX_TEXTO_FINAL_CHARS = 1600

_SIN_FUENTES_MARCADOR = (
    "(SIN FUENTES: solo se permite razonamiento general; cero eventos, nombres, cifras o fechas)"
)


def _extraer_json(raw: str) -> Any:
    """Extrae el primer objeto JSON balanceado de una respuesta de modelo.

    Un modelo a veces envuelve el JSON en \\`\\`\\`json ... \\`\\`\\` o agrega una frase
    antes/después. Se busca la primera ``{`` y se cuentan llaves hasta balancear,
    en vez de un regex greedy (``\\{.*\\}``) que puede sobre-capturar cuando hay
    texto con llaves después del bloque real.
    """
    if not raw:
        return None
    inicio = raw.find("{")
    if inicio == -1:
        return None
    profundidad = 0
    for indice in range(inicio, len(raw)):
        caracter = raw[indice]
        if caracter == "{":
            profundidad += 1
        elif caracter == "}":
            profundidad -= 1
            if profundidad == 0:
                try:
                    # `strict=False` a propósito: un modelo pequeño (nemotron,
                    # gemma) suele emitir el post con saltos de línea LITERALES
                    # dentro del string `"texto"` (los párrafos), y `json.loads`
                    # estricto los rechaza como caracteres de control -> el JSON
                    # se daba por "roto" y el turno moría con "No devolviste el
                    # JSON". Con `strict=False` esos saltos internos se aceptan;
                    # sigue siendo el MISMO objeto balanceado, no captura de más.
                    return json.loads(raw[inicio : indice + 1], strict=False)
                except json.JSONDecodeError:
                    return None
    return None


def _formatear_fuentes(fuentes: Sequence[Fuente] | None) -> str:
    """Numera las fuentes como ``[F1]``, ``[F2]``... para que el auditor pueda
    aislar cuál sostiene cada afirmación. Sin fuentes, deja el marcador SIN
    FUENTES: en ese modo solo se permite razonamiento general, cero eventos,
    nombres propios, cifras o fechas (la "memoria" del modelo es vieja por
    definición y nunca es una fuente válida).
    """
    if not fuentes:
        return _SIN_FUENTES_MARCADOR
    bloques: list[str] = []
    for indice, fuente in enumerate(fuentes, start=1):
        titulo = str(fuente.get("title") or fuente.get("titulo") or "").strip()
        url = str(fuente.get("url") or "").strip()
        snippet = str(fuente.get("snippet") or fuente.get("contenido") or "").strip()
        encabezado = f"[F{indice}] {titulo}" + (f" ({url})" if url else "")
        bloques.append(f"{encabezado}\n{snippet}" if snippet else encabezado)
    return "\n\n".join(bloques)[:_MAX_CONTEXTO_CHARS]


async def reescribir_sin_primera_persona(
    texto: str,
    fuentes: Sequence[Fuente] | None,
    llamar_modelo: LlamarModelo,
) -> str:
    """Último cinturón: convierte la autobiografía en criterio impersonal.

    Portado del motor de LinkedIn de Aria (`_reescribir_sin_primera_persona`),
    que es donde se demostró que hace falta. La diferencia era exactamente esta:
    ante un borrador en primera persona, Aria lo REPARA y Edecán solo lo
    RECHAZABA. Con un escritor que insiste en "yo/mi/hice" —el fallo más común—
    todos los intentos morían en la misma puerta determinista y el turno
    terminaba sin post, que es justo lo que el usuario vivía como "no funciona".

    Devuelve el texto corregido, o `""` si no se pudo arreglar (el modelo no
    respondió, o lo que devolvió TODAVÍA tiene primera persona). Nunca lanza:
    quien llama decide qué hacer con un `""`.
    """
    if not texto:
        return ""
    if not _tiene_primera_persona(texto):
        return texto

    try:
        crudo = await llamar_modelo(
            [
                {
                    "role": "system",
                    "content": (
                        "Eres un editor literal. Elimina TODA primera persona y toda "
                        "experiencia atribuida al dueño del perfil. Conserva la tesis y "
                        "los hechos, expresando el criterio como afirmaciones directas "
                        "sobre el mundo. No agregues hechos nuevos ni cambies el sentido. "
                        "Responde SOLO con el texto final, sin comillas ni explicaciones."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"FUENTES PERMITIDAS:\n{_formatear_fuentes(fuentes) or '(sin fuentes)'}"
                        f"\n\nTEXTO A CORREGIR:\n{texto[:_MAX_CONTEXTO_CHARS]}"
                    ),
                },
            ]
        )
    except Exception:  # noqa: BLE001 - reparar es best-effort; el caller ya tiene un plan B
        logger.warning("no se pudo reescribir sin primera persona", exc_info=True)
        return ""

    corregido = _normalizar_texto(str(crudo or "").strip().strip('"').strip())
    # Si el editor dejó primera persona, no sirvió: mejor devolver vacío que
    # colar como "corregido" un texto que viola el gate igual.
    return corregido if corregido and not _tiene_primera_persona(corregido) else ""


_ESQUEMA_EDITOR = (
    'Devuelve SOLO JSON válido con esta forma exacta: {"publicable": true|false, '
    '"motivo": "una frase breve y específica", "tema": "2 a 6 palabras", '
    '"angulo": "la idea central concreta en una frase", '
    '"promesa_lector": "por qué le importa a alguien ajeno, en una frase", '
    '"texto": "el post completo ya reescrito", '
    '"visual": {"kicker": "...", "headline": "...", "accent": "...", "support": "..."}}. '
    "No expliques nada fuera del JSON."
)

_SISTEMA_EDITOR_JEFE = (
    "Eres el editor jefe de un perfil profesional en redes sociales. Tu trabajo es dejar el "
    "borrador PUBLICABLE reescribiéndolo, no juzgarlo desde la barrera. No simulas la voz "
    "personal del dueño de la cuenta, no agregas hechos y no inventas primera persona ni "
    "autobiografía: eso está prohibido sin excepciones. PULIR ES MEJORAR TONO Y PRECISIÓN, "
    "NUNCA UNA EXCUSA PARA ACORTAR: 'silencio > ruido' significa quitar frases vacías o "
    "redundantes, jamás recortar el desarrollo real del candidato ni resumirlo. Si el "
    "mensaje del usuario trae un largo obligatorio para esta cuenta, tu versión final tiene "
    "que seguir cumpliéndolo -- devolver un texto más corto que el candidato, cuando ese "
    "candidato ya cumplía el largo pedido, es un fallo tuyo, no una mejora.\n\n"
    + PROMPT_EDITOR_HUMANIZADOR
)

_SISTEMA_CORRECTOR_DELATORES = (
    "Eres un corrector de última pasada. Conservas EXACTAMENTE los hechos y eliminas señales "
    "de escritura generada por IA. Cero primera persona y cero autobiografía.\n\n"
    + PROMPT_EDITOR_HUMANIZADOR
)


def _headline_propio(datos: Any) -> bool:
    """¿Este objeto trae un `visual.headline` escrito por un modelo (y no degradado por
    nosotros)? Decide si vale la pena someter el copy visual al detector."""
    if not isinstance(datos, Mapping):
        return False
    visual = datos.get("visual")
    if not isinstance(visual, Mapping):
        return False
    return bool(str(visual.get("headline") or "").strip())


def _borrador_publicable(datos: Any, previo: Mapping[str, Any]) -> dict[str, Any] | None:
    """Toma la respuesta del editor y la deja lista, cayendo al borrador previo campo a campo.

    Devuelve ``None`` si el editor no entregó un texto usable: quien llama decide si eso es
    un descarte o (en permisivo) motivo para conservar el borrador que ya tenía.
    """
    if not isinstance(datos, dict):
        return None
    texto = _normalizar_texto(str(datos.get("texto") or ""))
    if not texto:
        return None
    nuevo = dict(previo)
    nuevo["texto"] = texto
    for campo in ("tema", "angulo", "promesa_lector", "motivo"):
        valor = str(datos.get(campo) or previo.get(campo) or "").strip()
        if valor:
            nuevo[campo] = valor
    visual_nuevo = datos.get("visual")
    visual_base = visual_nuevo if isinstance(visual_nuevo, Mapping) else previo.get("visual")
    nuevo["visual"] = normalizar_visual(
        visual_base if isinstance(visual_base, Mapping) else None,
        tema=str(nuevo.get("tema") or ""),
        titular=str(previo.get("titular_visual") or ""),
    )
    nuevo["publicable"] = datos.get("publicable") is not False
    return nuevo


async def pulir_borrador(
    borrador: Mapping[str, Any],
    contexto: str,
    llamar_modelo: LlamarModelo,
    *,
    permisivo: bool = False,
    plataforma: str = "linkedin",
    sin_fuentes: bool = False,
    instruccion_formato: str = "",
    cooldown_estructura: str = "",
    borradores_recientes: str = "",
    requisito_largo: str = "",
) -> dict[str, Any] | None:
    """Editor jefe que REESCRIBE el borrador completo (texto + tema + ángulo + visual).

    Port de ``_revisar_calidad_editorial`` (Aria, ``linkedin_content.py:1417-1548``), y la
    pieza de mayor palanca de todo el port. **La diferencia no era de umbral, era
    estructural**: :func:`revisar_calidad` sólo devuelve ``(bool, motivo)``, o sea sólo sabe
    decir sí o no, mientras el editor de Aria devuelve un borrador NUEVO. Por eso en Aria
    el escritor puede entregar algo imperfecto y el post sale igual, y en Edecán el escritor
    tenía que acertar de primera -- con el modelo más débil de los dos y trece gates
    esperándolo. Este pase convierte la mayoría de los rechazos actuales en correcciones.

    Encadena dos cosas, en el orden de Aria:

    1. El editor jefe reescribe con :data:`PROMPT_EDITOR_HUMANIZADOR` como system. Ese prompt
       existía en ``editorial.py`` y NADIE lo importaba: el editor juzgaba a ciegas respecto
       de las señales que el detector le iba a medir después.
    2. Si el detector encuentra delatores en el resultado, :func:`reparar_delatores` hace UNA
       llamada más para quitarlos. Si no puede y ``permisivo``, se conserva igual.

    ``permisivo=True`` (el usuario pidió el tema): ``publicable=false`` se IGNORA y, si el
    editor no devuelve texto usable, se conserva el borrador original en vez de descartarlo.

    ``requisito_largo``, cuando se pasa (típicamente el ``target_length`` del perfil del
    tenant), se inyecta como un requisito EXPLÍCITO y prominente, igual que
    ``instruccion_formato``. Sin esto medido, el editor jefe recortaba el candidato a la
    mitad o menos incluso cuando ya cumplía el largo que la cuenta pedía: nada en su prompt
    mencionaba el largo, así que "pulir" sólo tenía presión en una dirección (acortar, por
    "silencio > ruido") y ninguna en la otra. El `target_length` SÍ viajaba dentro de
    ``contexto`` (ver ``social._contexto_calidad``), pero mezclado con `purpose`/`audience`/
    `voice`/`avoid` sin ningún marco de "esto es un requisito, no una preferencia" -- un
    editor que recibe diez líneas de contexto y una sola instrucción de estilo ("cada frase
    se gana su lugar") sigue la instrucción que sí es una orden.

    Devuelve el borrador pulido, o ``None`` cuando hay que descartarlo (sólo posible fuera de
    permisivo). Nunca lanza.
    """
    texto_previo = _normalizar_texto(str(borrador.get("texto") or ""))
    if not texto_previo:
        return None
    previo = dict(borrador)
    previo["texto"] = texto_previo

    # `contexto` es el bloque de juicio que arma el llamador (plataforma, tema, perfil, títulos
    # de fuentes, respaldo), así que nunca llega vacío: si el modo sin fuentes se dedujera de
    # `not contexto` no se activaría jamás. Por eso el llamador lo declara explícito.
    modo_sin_fuentes = (
        "Este borrador es de criterio y deliberadamente no tiene fuentes citables. No lo "
        "rechaces sólo por eso: reescríbelo como un mecanismo, una restricción o un marco de "
        "decisión que no afirme hechos externos fechables. "
        if sin_fuentes
        else ""
    )
    modo_permisivo = (
        "IMPORTANTE: el usuario PIDIÓ EXPLÍCITAMENTE este tema, así que tu trabajo es PULIRLO "
        "hasta dejarlo publicable, NO descartarlo. PROHIBIDO usar publicable=false por 'poco "
        "interesante', 'demasiado amplio' o 'sectorial': si el tema es amplio, elige el ángulo "
        "más concreto que permita la fuente y escribe sobre ESO. Sólo podrías rechazarlo si "
        "fuera imposible sin inventar hechos, y aun así prefiere reescribir. Devuelve "
        "publicable=true. "
        if permisivo
        else ""
    )
    candidato = json.dumps(
        {
            "tema": str(previo.get("tema") or ""),
            "fuente_principal": str(previo.get("fuente_principal") or ""),
            "angulo": str(previo.get("angulo") or ""),
            "promesa_lector": str(previo.get("promesa_lector") or ""),
            "texto": previo["texto"],
            "visual": previo.get("visual") if isinstance(previo.get("visual"), Mapping) else {},
        },
        ensure_ascii=False,
    )

    requisito_largo_bloque = (
        f"LARGO OBLIGATORIO DE ESTA CUENTA (no lo recortes): {requisito_largo}. Pulir un post "
        "es mejorar tono, concreción y quitar plantillas de IA -- nunca acortar el desarrollo. "
        "Conserva todas las escenas, ejemplos y párrafos del candidato salvo que debas quitar "
        "un hecho inventado o una violación dura. Si el candidato ya cumple ese largo, tu "
        "versión final tiene que seguir cumpliéndolo; si el candidato quedó corto, DESARRÓLLALO "
        "más (otro momento concreto, una consecuencia distinta) en vez de resumirlo todavía "
        "más.\n\n"
        if requisito_largo
        else ""
    )

    mensajes = [
        {"role": "system", "content": _SISTEMA_EDITOR_JEFE},
        {
            "role": "user",
            "content": (
                (f"FORMA EXIGIDA: {instruccion_formato}\n\n" if instruccion_formato else "")
                + requisito_largo_bloque
                + "Reescribe el candidato si su interés depende de una falsa revelación, de un "
                "'no es X, es Y', de un grupo forzado de tres, de frases apiladas para fabricar "
                "drama o de una sentencia final. Aplica primero la PRUEBA DEL SCROLL: alguien "
                "que no conoce la empresa ni el sector debe entender en diez segundos por qué "
                "esto le afecta. La primera línea debe contener el detalle más específico y no "
                "anunciar que algo importante viene después. El hecho se cuenta UNA vez en "
                "máximo dos frases y no se re-narra: el resto aporta una restricción, un costo, "
                "una decisión o una explicación concreta. Las cifras no son una cuota: cero "
                "cuando la idea funciona sin ellas. El titular visual debe nombrar literalmente "
                "el hecho o la consecuencia, sin metáforas, y entenderse SIN leer el post. "
                "Piensa tres aperturas y tres titulares en silencio y elige los más concretos, "
                "no los más dramáticos. No agregues hechos, cifras, citas, empresas ni "
                "desenlaces.\n\n"
                f"{modo_sin_fuentes}"
                f"{modo_permisivo}"
                f"{cooldown_estructura}\n\n"
                "CONTEXTO Y MATERIAL PERMITIDO (no agregues ningún hecho que no esté aquí):\n"
                + (contexto or "(sin fuentes; no agregues hechos fechables)")[:_MAX_CONTEXTO_CHARS]
                + (
                    f"\n\nBORRADORES RECIENTES QUE NO DEBES IMITAR (ni en arquitectura, no sólo "
                    f"en tema):\n{borradores_recientes[:_MAX_RECIENTES_CHARS]}"
                    if borradores_recientes
                    else ""
                )
                + f"\n\nCANDIDATO:\n{candidato[:_MAX_CONTEXTO_CHARS]}\n\n{_ESQUEMA_EDITOR}"
            ),
        },
    ]

    try:
        raw = await llamar_modelo(mensajes)
    except Exception:  # noqa: BLE001 - pulir es best-effort; el caller decide el plan B
        logger.warning("pulir_borrador: llamar_modelo falló.", exc_info=True)
        return previo if permisivo else None

    datos = _extraer_json(raw)
    pulido = _borrador_publicable(datos, previo)
    if pulido is None:
        # El editor no devolvió texto usable. En permisivo se conserva el borrador original
        # (el usuario lo pidió y está esperando); fuera de permisivo se descarta.
        return previo if permisivo else None
    if not pulido.pop("publicable", True) and not permisivo:
        return None

    hallazgos = delatores_de_estilo(str(pulido["texto"]), plataforma)
    # El visual sólo se somete al detector si ALGÚN modelo escribió de verdad un titular propio.
    # Cuando `normalizar_visual` tuvo que degradarlo al tema, sus "defectos" (menos de tres
    # palabras, por ejemplo) son obra NUESTRA, no del modelo: gastar una llamada de corrección
    # pidiéndole arreglar algo que él no escribió es tirar tiempo del chat, y fuera de permisivo
    # además arriesga descartar el borrador por eso.
    if _headline_propio(datos) or _headline_propio(previo):
        visual_final = pulido["visual"]
        hallazgos += revisar_visual(
            kicker=str(visual_final.get("kicker", "")),
            headline=str(visual_final.get("headline", "")),
            support=str(visual_final.get("support", "")),
        )
    if hallazgos:
        reparado = await reparar_delatores(
            pulido,
            hallazgos,
            contexto,
            llamar_modelo,
            plataforma=plataforma,
            requisito_largo=requisito_largo,
        )
        if reparado is not None:
            pulido = reparado
        elif not permisivo:
            return None
        # permisivo: no se pudieron limpiar los delatores -> se conserva el texto pulido. El
        # usuario revisa antes de publicar, que es infinitamente mejor que no recibir nada.
    return pulido


# Cuánto texto real de borradores anteriores ve el editor jefe. Aria pasa hasta 8 posts de
# 700 caracteres (`_borradores_recientes_contexto`) para detectar clones de ARQUITECTURA, no
# sólo de tema: un post con tema nuevo y molde idéntico pasaba limpio.
_MAX_RECIENTES_CHARS = 5000


async def reparar_delatores(
    borrador: Mapping[str, Any],
    hallazgos: Sequence[str],
    contexto: str,
    llamar_modelo: LlamarModelo,
    *,
    plataforma: str = "linkedin",
    requisito_largo: str = "",
) -> dict[str, Any] | None:
    """Segunda pasada dedicada a QUITAR los delatores de plantilla IA que se encontraron.

    Port de la pasada de corrección de ``_revisar_calidad_editorial`` (Aria,
    ``linkedin_content.py:1511-1544``). El problema real que resuelve: los mismos diez
    patrones de ``editorial._TEXT_TELLS`` que en Aria disparan una reescritura de diez
    segundos, en Edecán sólo producían rechazo -- y como el único reparador cableado era
    :func:`reescribir_sin_primera_persona`, que ante un texto SIN primera persona devuelve el
    texto INTACTO, el intento se quemaba sin haber intentado nada. Un "ahí está" en una frase
    secundaria tiraba un post entero que por lo demás estaba bien.

    Se le pasan los mensajes accionables de ``editorial.delatores_de_estilo`` /
    ``revisar_visual``, que son mejor insumo que la lista de nombres de reglas de Aria.

    Devuelve el borrador corregido, o ``None`` si no se pudo (el modelo falló, no devolvió
    texto, marcó ``publicable=false``, o su "corrección" sigue trayendo delatores).
    """
    if not hallazgos:
        return dict(borrador)
    detalle = "\n".join(f"- {h}" for h in hallazgos)
    requisito_largo_linea = (
        f"LARGO OBLIGATORIO DE ESTA CUENTA (no lo recortes al corregir): {requisito_largo}.\n\n"
        if requisito_largo
        else ""
    )
    mensajes = [
        {"role": "system", "content": _SISTEMA_CORRECTOR_DELATORES},
        {
            "role": "user",
            "content": (
                "El detector encontró estas señales de escritura generada por IA:\n"
                f"{detalle}\n\n"
                "Reescribe el texto Y el titular visual para eliminarlas DE VERDAD, no para "
                "sustituirlas por otra fórmula equivalente. Conserva exactamente los mismos "
                "hechos y el mismo tema; no agregues nada. Si no puedes hacerlo sin perder el "
                "único punto interesante del post, usa publicable=false.\n\n"
                f"{requisito_largo_linea}"
                f"FUENTES PERMITIDAS:\n{(contexto or '(sin fuentes)')[:_MAX_CONTEXTO_CHARS]}\n\n"
                f"BORRADOR:\n{json.dumps(dict(borrador), ensure_ascii=False)[:4500]}\n\n"
                f"{_ESQUEMA_EDITOR}"
            ),
        },
    ]
    try:
        raw = await llamar_modelo(mensajes)
    except Exception:  # noqa: BLE001 - reparar es best-effort
        logger.warning("reparar_delatores: llamar_modelo falló.", exc_info=True)
        return None

    reparado = _borrador_publicable(_extraer_json(raw), borrador)
    if reparado is None or not reparado.pop("publicable", True):
        return None
    # Si la "corrección" volvió a caer en un delator, no sirvió: mejor devolver None y dejar
    # que el caller decida (en permisivo conserva el texto anterior) que colar como corregido
    # un texto que el detector va a marcar igual.
    if delatores_de_estilo(str(reparado["texto"]), plataforma):
        return None
    return reparado


async def auditar_hechos(
    texto: str,
    fuentes: Sequence[Fuente] | None,
    llamar_modelo: LlamarModelo,
    *,
    solo_titulares: bool = False,
) -> tuple[str, list[str]]:
    """Auditor anti-invención: verifica cada cifra, fecha y afirmación fechable de
    `texto` contra `fuentes`, la única fuente de verdad permitida. Falla cerrado:
    ante cualquier duda, corrige o veta antes que dejar pasar una invención.

    Reglas duras que aplica (generalizadas de un pipeline propio; nada aquí es
    específico de un tenant):

    - Sin fuentes, el texto no puede afirmar ningún evento, cifra, nombre propio
      o fecha concreta -- solo razonamiento general o mecanismos estables.
    - MEMORIA VIEJA POR DEFINICIÓN: un evento (lanzamiento, sanción, cifra,
      despido...) que el modelo "recuerda" de su entrenamiento no es actualidad;
      solo existe si aparece en `fuentes`. Presentarlo como reciente sin una
      fuente real es publicar con una fecha falsa.
    - CERO DESENLACES INVENTADOS: si una fuente describe algo SIN RESOLVER (una
      denuncia, demanda, investigación o reclamo), el texto corregido JAMÁS
      afirma que hubo sanción, fallo, multa, veredicto o confirmación que esa
      fuente no confirma -- sin importar cuán probable parezca el desenlace.

    Devuelve `(texto_final, problemas)`:

    - Si el texto ya cumplía o se pudo corregir sin perder la tesis central,
      `texto_final` es el texto (posiblemente reescrito) y `problemas` lista lo
      que se encontró y corrigió (vacía si no había nada que corregir).
    - Si no se puede sostener sin inventar, `texto_final` es `""` -- cadena
      vacía es la señal de veto total (fail-closed) -- y `problemas` siempre
      trae al menos un motivo.

    `solo_titulares=True` avisa que las "fuentes" son titulares + snippet de un
    buscador, nunca el cuerpo del artículo. Es el tercer modo del auditor de
    Aria (`linkedin_content.py:1380-1385`): un titular NO autoriza a deducir
    mecanismos, causas, efectos ni detalles que el titular no dice. Se porta
    como INSTRUCCIÓN, no como veto automático: Edecán es un atajo del chat y no
    puede pagar los segundos de leer el artículo completo, así que endurecer el
    veto aquí sólo produciría más turnos sin post sobre el mismo material.

    Esta función NO tiene un parámetro `permisivo`: el veto anti-invención es
    siempre estricto. Qué hacer ante un veto (descartar el borrador entero,
    o conservar el texto previo a este auditor porque el usuario pidió el
    tema explícitamente) es una decisión de orquestación -- ver `finalizar_post`
    y la lección crítica en el docstring del módulo.
    """
    texto = (texto or "").strip()
    if not texto:
        return "", ["El texto está vacío."]

    contexto = _formatear_fuentes(fuentes)
    sin_fuentes = contexto == _SIN_FUENTES_MARCADOR

    if sin_fuentes:
        instruccion_modo = (
            "No hay fuentes. Reconstruye cualquier afirmación empírica como un escenario "
            "condicional, una pregunta de decisión o una explicación puramente lógica. "
            "Elimina marcas, productos, cifras, tendencias y afirmaciones sobre cómo actúa "
            "una industria o una empresa concreta. Devuelve publicable=true si puedes "
            "conservar una idea útil sin esos elementos; publicable=false solo si al "
            "quitarlos no queda una idea concreta."
        )
    else:
        instruccion_modo = (
            "Si el texto contiene inferencias débiles, reconstrúyelo con 1 a 3 hechos "
            "explícitos de las fuentes, manteniendo el mismo tema, y devuelve "
            "publicable=true siempre que las fuentes alcancen para un texto claro. Usa "
            "publicable=false únicamente si las fuentes no alcanzan para sostener el tema "
            "central."
        )
        if solo_titulares:
            # Tercer modo del auditor de Aria (`linkedin_content.py:1380-1385`): con un
            # titular y un snippet no hay cuerpo de artículo contra el que verificar, así que
            # lo que hay que impedir es la EXTRAPOLACIÓN, no el post entero.
            instruccion_modo += (
                " AVISO IMPORTANTE SOBRE EL MATERIAL: lo que recibes son titulares y "
                "fragmentos de buscador, no el cuerpo de los artículos. Un titular no "
                "autoriza a deducir mecanismos, causas, efectos, motivaciones, plazos ni "
                "detalles que el titular no diga literalmente: recorta esas deducciones en "
                "vez de darlas por buenas. Lo que sí puede sostener el post es el hecho tal "
                "como el titular lo enuncia, más razonamiento general declarado como tal."
            )

    mensajes: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "Eres un auditor factual y lógico extremadamente estricto. Las fuentes "
                "incluidas son la ÚNICA verdad permitida; no completas nada desde tu propia "
                "memoria ni inventas un reemplazo cuando falta un dato. Rechazas cualquier "
                "cifra, fecha, nombre, motivación, plazo o comparación que las fuentes no "
                "digan literalmente. Regla dura no negociable: si una fuente describe algo "
                "SIN RESOLVER (una denuncia, demanda, investigación o reclamo), el texto "
                "corregido nunca puede afirmar que hubo sanción, fallo, multa, veredicto o "
                "confirmación -- eso es un desenlace inventado, sin importar cuán probable "
                "parezca."
            ),
        },
        {
            "role": "user",
            "content": (
                f"FUENTES (única fuente de verdad):\n{contexto}\n\n"
                f"TEXTO A AUDITAR:\n{texto[:_MAX_TEXTO_CHARS]}\n\n"
                "Devuelve SOLO JSON válido con esta forma exacta: "
                '{"publicable": true|false, "problemas": ["..."], "texto": "..."}. '
                "'problemas' lista cada cifra, fecha, cita o desenlace del borrador que no "
                "viene literalmente de las fuentes (vacía si el texto ya estaba limpio); "
                "'texto' es el texto completo corregido usando solo lo que las fuentes "
                "sostienen, sin agregar hechos nuevos. Usa publicable=false si, incluso "
                f"corrigiendo, el tema central deja de estar sostenido por las fuentes. "
                f"{instruccion_modo}"
            ),
        },
    ]

    try:
        raw = await llamar_modelo(mensajes)
    except Exception:
        logger.warning("auditar_hechos: llamar_modelo falló; se veta por seguridad.", exc_info=True)
        return "", ["No se pudo completar la auditoría factual (fallo al llamar al modelo)."]

    datos = _extraer_json(raw)
    if not isinstance(datos, dict):
        return "", [
            "El auditor no devolvió una respuesta interpretable; se descarta por seguridad."
        ]

    problemas = [str(p).strip() for p in (datos.get("problemas") or []) if str(p).strip()]

    if datos.get("publicable") is not True:
        return "", problemas or ["El auditor marcó el texto como no publicable."]

    corregido = _normalizar_texto(str(datos.get("texto") or ""))
    if not (_MIN_TEXTO_CHARS < len(corregido) <= _MAX_TEXTO_FINAL_CHARS):
        return "", problemas or ["El texto corregido quedó fuera del rango de longitud aceptable."]

    return corregido, problemas


async def revisar_calidad(
    texto: str,
    contexto: str,
    llamar_modelo: LlamarModelo,
    permisivo: bool = False,
) -> tuple[bool, str]:
    """Editor jefe de segunda pasada: juzga si `texto` merece publicarse, no
    solo si pasa los gates deterministas de estilo. Puede RECHAZAR el borrador
    completo (regla "no forzar un post": saltarse el turno protege mejor la
    cuenta que publicar contenido correcto pero mediocre o genérico).

    `contexto` es el bloque de contexto que arma el llamador (fuentes, perfil
    editorial del tenant, borradores recientes a no repetir, formato exigido,
    etc.); esta función no lo construye, solo lo usa para juzgar si el texto
    tiene un ángulo real o es plano/forzado/genérico.

    `permisivo=True` (el usuario pidió EXPLÍCITAMENTE este tema): desactiva el
    rechazo por criterio SUBJETIVO ("poco interesante", "tema demasiado
    amplio", "sectorial"). En ese modo el editor debe pulir el ángulo más
    concreto posible en vez de descartarlo -- el usuario revisa el resultado
    antes de publicar. Solo sobreviven los rechazos por reglas DURAS (el texto
    está vacío, es ilegible, o no se sostiene en absoluto). Un fallo técnico al
    interpretar la respuesta del editor tampoco cuenta como rechazo por regla
    dura: en modo permisivo se conserva el borrador (motivo explicado); fuera
    de modo permisivo se descarta por seguridad.

    Devuelve `(publicable, motivo)`. `motivo` explica el veredicto, tanto si es
    `True` (trazabilidad) como si es `False` (para decidir si vale la pena
    reintentar con otro ángulo en vez de simplemente saltar el turno).
    """
    texto = (texto or "").strip()
    if len(texto) < _MIN_TEXTO_CHARS:
        return False, "El texto está vacío o es demasiado corto para evaluarlo."

    instruccion_permisivo = (
        "El usuario PIDIÓ explícitamente este tema: tu trabajo es juzgar si el ángulo "
        "elegido es publicable, no rechazarlo por 'poco interesante', 'demasiado amplio' o "
        "'genérico'. Responde publicable=false SOLO si el texto viola una regla dura (no se "
        "sostiene en el contexto disponible, o es ilegible/incoherente), nunca por simple "
        "gusto editorial.\n\n"
        if permisivo
        else ""
    )

    mensajes: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "Eres el editor jefe de un perfil profesional en redes sociales. Tu trabajo "
                "es decidir si un borrador merece publicarse, no reescribirlo palabra por "
                "palabra. Un borrador correcto pero plano, genérico o sin un ángulo concreto "
                "no es publicable: es preferible saltarse el turno que publicar contenido "
                "artificial."
            ),
        },
        {
            "role": "user",
            "content": (
                f"{instruccion_permisivo}"
                "CONTEXTO DISPONIBLE:\n"
                f"{(contexto or '(sin contexto adicional)')[:_MAX_CONTEXTO_CHARS]}\n\n"
                f"BORRADOR:\n{texto[:_MAX_TEXTO_CHARS]}\n\n"
                "Evalúa: ¿alguien ajeno al tema entiende en diez segundos por qué le importa? "
                "¿hay un sujeto exacto, una persona o grupo afectado, y una decisión o "
                "desacuerdo concreto? ¿el cierre resuelve algo en vez de terminar en una "
                "pregunta retórica o un diagnóstico suelto? Devuelve SOLO JSON válido: "
                '{"publicable": true|false, "motivo": "una frase breve y específica"}.'
            ),
        },
    ]

    try:
        raw = await llamar_modelo(mensajes)
    except Exception:
        logger.warning("revisar_calidad: llamar_modelo falló.", exc_info=True)
        if permisivo:
            return True, (
                "No se pudo completar la revisión de calidad (fallo al llamar al modelo); "
                "se conserva el borrador porque el usuario pidió este tema explícitamente."
            )
        return False, "No se pudo completar la revisión de calidad (fallo al llamar al modelo)."

    datos = _extraer_json(raw)
    if not isinstance(datos, dict):
        if permisivo:
            return True, (
                "El editor no devolvió una respuesta interpretable; se conserva el borrador "
                "porque el usuario pidió este tema explícitamente (modo permisivo)."
            )
        return (
            False,
            "El editor no devolvió una respuesta interpretable; se descarta por seguridad.",
        )

    motivo = str(datos.get("motivo") or "").strip() or "Sin motivo explícito del editor."
    publicable = datos.get("publicable") is True
    return publicable, motivo


async def finalizar_post(
    texto: str,
    *,
    fuentes: Sequence[Fuente] | None,
    contexto: str,
    llamar_modelo: LlamarModelo,
    plataforma: str = "linkedin",
    permisivo: bool = False,
) -> tuple[str, list[str]]:
    """Orquestación de referencia: `revisar_calidad` -> `auditar_hechos` -> vuelve
    a correr `edecan_creative.editorial.revisar` sobre el resultado.

    Este es el mecanismo real detrás de la lección crítica documentada arriba:
    `auditar_hechos` reescribe el texto, y esa reescritura puede reintroducir
    una violación de estilo (primera persona, cierre en pregunta, una plantilla
    delatora de IA) que ya se había limpiado ANTES de que este texto llegara
    aquí. Por eso el paso final vuelve a correr los mismos gates deterministas
    sobre el texto que sobrevivió a las dos reescrituras -- nunca se confía en
    que un texto que pasó los gates una vez los sigue pasando después de que un
    modelo lo tocó de nuevo.

    Un llamador que necesite una orquestación distinta (por ejemplo, insertar
    pasos propios entre la revisión de calidad y la auditoría factual) puede
    usar `revisar_calidad` y `auditar_hechos` por separado -- pero debe
    reproducir ese re-chequeo final con `edecan_creative.editorial.revisar`
    (y, si el post tiene un titular visual aparte, con
    `edecan_creative.editorial.revisar_visual`) él mismo.

    Devuelve `(texto_final, problemas)`. `texto_final` es `""` cuando el
    borrador se descarta (regla "no forzar un post": el llamador debe saltarse
    el turno, no reintentar publicar con este mismo texto) y `problemas`
    explica por qué.
    """
    texto = (texto or "").strip()
    if not texto:
        return "", ["El texto está vacío."]

    publicable, motivo_calidad = await revisar_calidad(texto, contexto, llamar_modelo, permisivo)
    if not publicable and not permisivo:
        return "", [motivo_calidad]

    texto_auditado, problemas_hechos = await auditar_hechos(texto, fuentes, llamar_modelo)
    if texto_auditado:
        texto_final = texto_auditado
    elif permisivo:
        # El auditor vetó, pero el usuario pidió este tema explícitamente: se conserva el
        # texto PREVIO al auditor (ya evaluado por revisar_calidad) en vez de descartar todo
        # el borrador. El usuario revisa el resultado antes de publicar.
        texto_final = texto
    else:
        return "", problemas_hechos or [motivo_calidad]

    # REPARAR ANTES DE RE-GATEAR: `auditar_hechos` reescribe con un prompt que no conoce
    # ninguna regla de estilo, así que reintroducir un "nos", un "creo" o un "vimos" es lo
    # ESPERADO, no lo raro (ver la lección crítica del docstring del módulo). Aria, en ese
    # mismo punto (`linkedin_content.py:1601-1605`), no descarta: repara la primera persona y
    # RE-VERIFICA el resultado contra la fuente. Descartar aquí era tirar el mejor candidato
    # que existió en toda la corrida por un pronombre mecánicamente arreglable.
    if _tiene_primera_persona(texto_final):
        reparado = await reescribir_sin_primera_persona(texto_final, fuentes, llamar_modelo)
        if reparado:
            re_auditado, _ = await auditar_hechos(reparado, fuentes, llamar_modelo)
            texto_final = re_auditado or (reparado if permisivo else texto_final)

    # RE-GATE: los gates deterministas corren de nuevo sobre el texto FINAL, después de las
    # reescrituras anteriores -- ver la lección crítica en el docstring del módulo.
    problemas_gate = _revisar_gates_deterministas(texto_final, plataforma, permisivo=permisivo)
    if problemas_gate and not permisivo:
        return "", problemas_gate

    problemas_acumulados = [*problemas_hechos, *problemas_gate]
    return texto_final, problemas_acumulados
