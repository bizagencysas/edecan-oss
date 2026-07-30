"""Pipeline editorial de imagen: dirección de arte + crítica automática + composición del
titular sobre la foto (`ARCHITECTURE.md` §10.14, mismo paquete que `social.py`/`providers.py`).

Migrado desde Aria (`aria/features/linkedin_content.py` funciones `_generar_imagen_libre`,
`_revisar_imagen`, `_generar_con_iteracion`, `_componer_pieza`; y `aria/scripts/li_compose.py`)
para resolver un defecto real que ocurrió sin este módulo: se pidió un post sobre tres modelos
de IA llamados "Luna, Terra y Sol" y la imagen generada fueron literalmente la Luna, la Tierra
y el Sol en tres paneles verticales — cero valor editorial, y nadie la revisó antes de
publicarla. Este módulo existe para que ESO nunca vuelva a pasar: reglas de dirección de arte
que prohíben explícitamente ilustrar nombres propios al pie de la letra, y un crítico con visión
que juzga la imagen contra el "claim" — la idea concreta que debe comunicar, no solo si es
bonita — antes de que un humano la vea.

Diseño: este módulo es puro y no importa ningún SDK de proveedor concreto (ni de imágenes ni de
LLM). Dos puntos de inyección de dependencia:

- `generar_imagen: Callable[[str], Awaitable[bytes]]` — genera una imagen a partir de un prompt
  y devuelve PNG/JPEG crudo. Compatible tal cual con `ImageProvider.generate` de
  `edecan_creative.providers` (`provider.generate` ya es `(prompt: str, size: str = ...) -> bytes`,
  y un simple `lambda p: provider.generate(p, size=...)` cierra la diferencia de firma).
- `llamar_modelo: Callable[[bytes, str], Awaitable[str]]` — manda la imagen + una pregunta a un
  modelo con visión y devuelve su respuesta cruda en texto. Quien cablee este módulo arma el
  `ChatMessage` con el bloque de imagen en el formato común de `edecan_llm.base` (ver
  `edecan_docanalysis/vision.py` para el patrón exacto:
  `{"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}}` junto a un
  bloque `{"type": "text", "text": pregunta}`) y devuelve `response.text`.

Composición del titular: PIL, NO Playwright. `edecan_creative` ya depende de Pillow
(`social.py::_offline_social_card` la usa para su propia tarjeta local) y ningún paquete de
contenido social de Edecán usa Playwright hoy — solo lo usan `packages/design-studio` y
`packages/browser`, para renderizado de proyectos de diseño y scraping, un propósito distinto.
Arrastrar un Chromium completo a este paquete solo para pintar un titular sobre una foto habría
sido una dependencia nueva, pesada y sin necesidad real: `componer_titular()` reproduce el mismo
layout que `aria/features/li_templates/foto.html` (foto a lienzo completo, scrim degradado
inferior, kicker con barra de acento, headline serif grande con una palabra resaltada, apoyo en
monoespaciada) usando `PIL.ImageDraw`/`ImageFont`, con la misma estrategia defensiva de búsqueda
de fuentes del sistema que ya usa `social.py::_font()` (nunca falla: cae al font por defecto de
Pillow si no encuentra ninguna fuente instalada).
"""

from __future__ import annotations

import io
import json
import os
import re
import statistics
from collections.abc import Awaitable, Callable

from PIL import Image, ImageChops, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Tipos de inyección de dependencia (ver docstring del módulo)
# ---------------------------------------------------------------------------

GenerarImagen = Callable[[str], Awaitable[bytes]]
LlamarModelo = Callable[[bytes, str], Awaitable[str]]

# ---------------------------------------------------------------------------
# 1. Reglas de dirección de arte — listas para inyectar en el prompt de imagen
# ---------------------------------------------------------------------------

REGLAS_ARTE: str = (
    "Eres el director de arte de una pieza editorial. Crea la imagen que MEJOR represente la "
    "idea concreta indicada, no un mood genérico intercambiable. Tienes libertad para decidir "
    "la forma — una escena, un objeto, una comparación visual, una ilustración editorial o una "
    "composición de datos — pero elige UNA idea visual fuerte, no intentes explicar todo el "
    "texto dentro de la imagen. PRUEBA: si esta misma imagen serviría igual para un titular "
    "completamente distinto, está mal — hazla específica a esta idea.\n\n"
    "PROHIBIDO explícito, sin excepciones:\n"
    "1) Ilustrar literalmente un nombre propio. Si el texto menciona algo llamado 'Luna', "
    "'Sol', 'Atlas', 'Fénix' o cualquier otro nombre que coincida con un objeto, fenómeno o "
    "figura real, la imagen NO debe representar ese objeto o fenómeno real tomado al pie de la "
    "letra — representa la idea de negocio o el concepto detrás del nombre, nunca el nombre en "
    "sí. (Este es el defecto real que motivó esta regla: un post sobre tres modelos de IA "
    "llamados 'Luna, Terra y Sol' generó literalmente la Luna, la Tierra y el Sol en tres "
    "paneles verticales — cero valor editorial, nadie la revisó antes de publicarla.)\n"
    "2) Escenas espaciales o astronómicas genéricas (planetas, galaxias, constelaciones, "
    "cohetes, agujeros negros) como atajo visual por defecto para hablar de 'futuro', 'escala' "
    "o 'tecnología'.\n"
    "3) Collages: prohibido dividir el lienzo en tarjetas, paneles, cuadrantes, viñetas o "
    "franjas separadas entre sí — una sola escena continua y coherente, de borde a borde.\n"
    "4) Robots genéricos, iconografía de IA gastada (cerebros de circuitos, manos tocando "
    "pantallas holográficas) o composiciones intercambiables de banco de imágenes.\n\n"
    "Reglas de ejecución:\n"
    "- Logos y marcas reales: libres de usar, con el nombre/logo que corresponda, siempre que "
    "lo que muestren sea VERDAD (la empresa que el texto menciona, su competencia real). Máximo "
    "un logo protagonista, solo cuando sea indispensable para identificar el caso. Nunca uses el "
    "logo o la cabecera de un MEDIO real (Bloomberg, Reuters, etc.) dando a entender que ellos "
    "publicaron esto.\n"
    "- Si el texto involucra a una persona real, famosa y nombrada: esto es contenido generado, "
    "así que JAMÁS fabriques su rostro fotorrealista exacto como si fuera una foto auténtica de "
    "ella — represéntala con un estilo ilustrado/animado propio, o sin mostrar su cara (un "
    "objeto, su logo, un entorno). Cualquier otra persona en la imagen es genérica y anónima.\n"
    "- Si hay una pantalla o teléfono con texto visible, ese texto debe leerse normal, JAMÁS en "
    "espejo o invertido, y el ángulo debe ser físicamente posible.\n"
    "- Sin marcas de agua."
)


# ---------------------------------------------------------------------------
# 2. Construcción del prompt de imagen
# ---------------------------------------------------------------------------


def construir_prompt_imagen(
    texto: str,
    claim: str = "",
    plataforma: str = "linkedin",
    *,
    limpio: bool = False,
    feedback: str = "",
) -> str:
    """Arma el prompt final para el proveedor de generación de imágenes a partir del post/pieza
    REAL — nunca de una escena pre-guionada a distancia del texto verdadero (mismo criterio que
    Aria, `_generar_imagen_libre`: pasarle el contenido real y dejar que el modelo de imagen
    decida composición y estilo).

    texto: el post/copy completo que la imagen debe acompañar.
    claim: la idea concreta que la imagen DEBE comunicar (ver `criticar()` — es contra esto que
      se juzga, no solo contra "se ve bien"). Opcional pero muy recomendado.
    plataforma: dónde se publica (solo entra en la instrucción como contexto, ej. "linkedin",
      "x", "instagram").
    limpio: True cuando NO habrá un compositor montando texto encima después (p. ej. una
      encuesta cuya pregunta/opciones ya van en el copy) — la imagen debe usar todo el lienzo
      sin reservar una franja vacía para un titular que nunca llega, y puede llevar texto que
      pertenezca de verdad a la escena (un logo, un letrero), pero nunca un titular/cifra.
    feedback: motivo de rechazo de un intento anterior (ver `generar_con_critica`), para que la
      regeneración corrija el problema puntual en vez de repetirlo.
    """
    texto = (texto or "").strip()
    claim = (claim or "").strip()

    if limpio:
        zona_texto = (
            "Esta imagen se publica TAL CUAL, sin ningún compositor montando texto encima "
            "después: usa TODO el lienzo de borde a borde con contenido real en toda la "
            "superficie, sin reservar ninguna franja, degradado o panel vacío para un texto "
            "que nunca va a llegar."
        )
        texto_prohibido = (
            "Puedes incluir texto corto que pertenezca NATURALMENTE a la escena (el nombre en "
            "un logo, una etiqueta real de producto, un letrero) — pero JAMÁS un titular, "
            "párrafo, cifra inventada o interfaz de pantalla que parezca puesto encima como "
            "capa aparte; todo texto debe ser parte física y legible de la escena, nunca "
            "decorativo ni superpuesto."
        )
    else:
        zona_texto = (
            "La franja inferior del lienzo sigue siendo parte visible del mismo entorno, con "
            "textura y detalle real, aunque sin otro sujeto protagonista, porque ahí se montará "
            "el titular con un degradado oscuro encima después. PROHIBIDO terminar la escena a "
            "media altura, agregar un panel vacío, dejar una gran superficie lisa sin "
            "contenido, o separar foto y espacio de texto con una línea horizontal."
        )
        texto_prohibido = (
            "NO escribas titulares, párrafos, etiquetas, cifras, pantallas, recibos ni "
            "interfaces legibles dentro de la imagen base. El compositor añadirá todo el texto "
            "después."
        )

    claim_txt = (
        (
            f"La idea concreta que la imagen debe comunicar: {claim}. PRUEBA: si esta misma imagen "
            "serviría igual para un titular completamente distinto, está mal, hazla específica a "
            "esta idea. "
        )
        if claim
        else ""
    )

    prompt = (
        f"Esta es la pieza para {plataforma} (en español, salvo que el texto indique otro "
        f'idioma) que la imagen debe acompañar:\n\n"{texto}"\n\n'
        + claim_txt
        + REGLAS_ARTE
        + "\n\n"
        + "La escena debe ocupar TODO el lienzo, de borde a borde. "
        + zona_texto
        + " "
        + texto_prohibido
        + (f"\n\nCORRIGE este problema de un intento anterior: {feedback}" if feedback else "")
    )
    return prompt


# ---------------------------------------------------------------------------
# 3. Crítico — juzga la imagen contra el claim, no solo contra la estética
# ---------------------------------------------------------------------------


def _miniatura(imagen_bytes: bytes, lado_max: int = 1024) -> bytes:
    """Reduce la imagen antes de mandarla a un modelo con visión (mismo criterio que
    `_revisar_imagen` de Aria: controla costo/latencia de la llamada de crítica). Si algo
    falla, devuelve los bytes originales tal cual — nunca bloquea la crítica por esto."""
    try:
        im = Image.open(io.BytesIO(imagen_bytes)).convert("RGB")
        im.thumbnail((lado_max, lado_max))
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception:
        return imagen_bytes


async def criticar(
    imagen_bytes: bytes,
    claim: str,
    llamar_modelo: LlamarModelo,
    *,
    tema: str = "",
    limpio: bool = False,
) -> tuple[bool, str]:
    """Autocrítica de UNA sola imagen ya generada: ¿sirve tal cual, o qué le falta? Director de
    arte hostil — squint test, brand-swap test, señales de AI slop, y (el eje más importante) si
    de verdad COMUNICA `claim` — la idea específica de la pieza — o es solo un mood genérico
    intercambiable que serviría igual para cualquier otro titular.

    Devuelve (aprobada, motivo). Si la llamada al modelo falla o no devuelve un JSON parseable,
    NO bloquea: aprobada=True con motivo vacío, para que una pieza nunca se quede sin imagen por
    una falla de la crítica en sí misma (mismo criterio de `_revisar_imagen` en Aria).

    limpio: True cuando no hay compositor detrás (ver `construir_prompt_imagen`) — cambia el
    criterio de los ejes de texto y de encuadre."""
    eje_4 = (
        (
            "4) TEXTO — esta imagen se publica TAL CUAL, sin compositor después. Un texto que "
            "pertenece de verdad a la escena (un logo real, un letrero, una etiqueta) está bien SI "
            "se lee limpio y correcto; un titular, párrafo, cifra inventada o cualquier texto que "
            "parezca pegado encima como capa aparte NO sirve.\n"
        )
        if limpio
        else (
            "4) TEXTO/PANTALLAS — la imagen base debe venir SIN titulares, párrafos, etiquetas, "
            "cifras, recibos, gráficas rotuladas ni interfaces legibles, porque un compositor "
            "agrega el texto después. Si aparece cualquiera, NO sirve. Un único logo real y "
            "correcto sí puede aparecer.\n"
        )
    )
    eje_8 = (
        (
            "8) ENCUADRE COMPLETO — no hay compositor detrás: ¿usa TODO el lienzo de borde a "
            "borde, sin franjas vacías, degradados reservados, paneles lisos, ni una línea "
            "horizontal que corte la escena a media altura?"
        )
        if limpio
        else (
            "8) ESPACIO PARA COPY — ¿la franja inferior tiene una zona visualmente estable "
            "donde el titular superpuesto se leerá sin tapar el sujeto principal? Esa zona "
            "debe seguir mostrando el mismo entorno de borde a borde: si la escena termina a "
            "media altura, hay una línea horizontal o la mitad inferior es un panel casi "
            "vacío, NO sirve."
        )
    )
    chequeo_claim = (
        (
            f"\n\nADEMÁS, el criterio MÁS IMPORTANTE de todos: la idea específica que esta imagen "
            f"debe comunicar es '{claim}'. Sé exigente — pregúntate: si un desconocido viera SOLO "
            "esta imagen (sin el texto), ¿reconocería de qué trata específicamente, o solo "
            "capta un mood genérico (alivio, tensión, éxito) que serviría igual para "
            "cualquier otro titular? Si es solo mood genérico sin ningún ancla visual "
            "concreta a esta idea puntual, NO sirve, aunque la ejecución técnica sea "
            "perfecta."
        )
        if claim
        else ""
    )
    sobre_tema = f" sobre '{tema}'" if tema else ""

    pregunta = (
        f"Esta es una imagen generada para una pieza{sobre_tema}. Actúa como director de arte "
        "hostil y revísala eje por eje, sin saltarte ninguno:\n"
        "1) CLARIDAD — squint test: ¿la idea se lee en miniatura, en un segundo?\n"
        "2) ORIGINALIDAD — brand-swap test: ¿tiene una decisión visual propia, o es "
        "intercambiable?\n"
        "3) JERARQUÍA/COMPOSICIÓN — ¿hay un elemento protagonista claro, o todo compite igual?\n"
        + eje_4
        + "5) GEOMETRÍA FÍSICA — si una persona sostiene o mira una pantalla/teléfono que el "
        "espectador también puede leer: ¿el ángulo es físicamente posible, o la persona en "
        "realidad estaría viendo la parte de atrás del dispositivo dado cómo está orientada la "
        "pantalla hacia la cámara? Esto es un error real y común — revísalo con cuidado.\n"
        "6) REALISMO/AI SLOP — manos o dedos deformes, brillo plástico de render, bokeh de "
        "portada de stock, composición genérica de banco de imágenes.\n"
        "7) ATRIBUCIÓN FALSA — ¿aparece el logo/cabecera de un MEDIO real (Bloomberg, Reuters, "
        "The Economist, etc.) dando a entender que ELLOS publicaron esto? Eso es falso y "
        "reprueba, aunque un único logo de la empresa central del tema sí está bien.\n"
        + eje_8
        + chequeo_claim
        + "\n\nSi CUALQUIERA de estos ejes falla, sirve=false. Responde ÚNICAMENTE con este "
        'JSON: {"sirve": true o false, "problema": "si no sirve, cuál eje falló y qué falla en '
        'una frase corta y concreta; si sirve, vacio"}'
    )

    try:
        respuesta = await llamar_modelo(_miniatura(imagen_bytes), pregunta) or ""
    except Exception:
        return True, ""
    m = re.search(r"\{.*\}", respuesta, re.S)
    if not m:
        return True, ""
    try:
        js = json.loads(m.group())
        return bool(js.get("sirve")), str(js.get("problema") or "")[:200]
    except Exception:
        return True, ""


# ---------------------------------------------------------------------------
# 4. Bucle genera → critica → regenera con el feedback (máximo N intentos)
# ---------------------------------------------------------------------------

# Tope de intentos configurable — mismo criterio que `_N_INTENTOS`/`LINKEDIN_IMG_VARIANTES` de
# Aria, renombrado porque este módulo no es específico de LinkedIn.
_N_INTENTOS = int(os.getenv("EDECAN_IMG_INTENTOS", "3"))


def _franja_vacia(imagen_bytes: bytes) -> str:
    """Detecta con pura aritmética de píxeles (sin LLM, prácticamente gratis) una escena que
    termina a media altura y deja la mitad inferior casi sin detalle — el patrón típico de una
    imagen que "se rindió" antes de llenar el lienzo. Corre ANTES de gastar una llamada de
    crítica con visión. Ported de `_detectar_franja_vacia` (Aria,
    `features/linkedin_content.py:799-824`), adaptada a bytes en memoria en vez de ruta de
    archivo. Devuelve el feedback a inyectar en el próximo intento, o "" si no aplica."""
    try:
        im = Image.open(io.BytesIO(imagen_bytes)).convert("L").resize((96, 144))
        dx = ImageChops.difference(
            im, im.transform(im.size, Image.Transform.AFFINE, (1, 0, 1, 0, 1, 0))
        )
        dy = ImageChops.difference(
            im, im.transform(im.size, Image.Transform.AFFINE, (1, 0, 0, 0, 1, 1))
        )
        detalle_filas = []
        for y in range(2, 142):
            valores = [dx.getpixel((x, y)) + dy.getpixel((x, y)) for x in range(2, 94)]
            detalle_filas.append(sum(valores) / len(valores))
        detalle_superior = statistics.median(detalle_filas[6:58])
        detalle_inferior = statistics.median(detalle_filas[98:134])
        umbral_vacio = max(2.2, detalle_superior * 0.25)
        filas_inferiores = detalle_filas[58:134]
        proporcion_vacia = sum(v < umbral_vacio for v in filas_inferiores) / len(filas_inferiores)
        if detalle_superior >= 8 and detalle_inferior < umbral_vacio and proporcion_vacia >= 0.62:
            return (
                "La escena termina aproximadamente a media altura y la mitad inferior queda "
                "como un panel casi vacío. Extiende el mismo entorno visual hasta el borde "
                "inferior, sin franjas lisas."
            )
    except Exception:
        pass
    return ""


async def generar_con_critica(
    generar_imagen: GenerarImagen,
    *,
    texto: str,
    claim: str = "",
    llamar_modelo: LlamarModelo,
    plataforma: str = "linkedin",
    tema: str = "",
    limpio: bool = False,
    max_intentos: int = _N_INTENTOS,
) -> tuple[bytes | None, str]:
    """Genera SECUENCIAL, no en paralelo (mismo criterio que `_generar_con_iteracion` de Aria:
    varias generaciones en paralelo saturan de golpe la misma cuota de visión que usa la crítica
    de cada una). Una por una: construye el prompt (con el feedback del intento anterior si lo
    hay) → genera → chequeo barato de píxeles → crítica con visión → si sirve, listo; si no, el
    siguiente intento arranca con el problema puntual como corrección. Si se acaban los intentos
    sin un "sí" claro, devuelve la ÚLTIMA imagen generada (la más corregida) en vez de la
    primera, junto con el motivo del último rechazo.

    Devuelve (imagen_bytes o None si nunca se logró generar nada, motivo_del_último_rechazo).
    motivo == "" si la última imagen fue aprobada."""
    ultima_imagen: bytes | None = None
    feedback = ""
    motivo = ""
    for _intento in range(max(1, max_intentos)):
        prompt = construir_prompt_imagen(texto, claim, plataforma, limpio=limpio, feedback=feedback)
        try:
            imagen = await generar_imagen(prompt)
        except Exception as e:
            motivo = f"generación falló: {str(e)[:150]}"
            feedback = ""
            continue
        if not imagen:
            motivo = "el proveedor de imágenes no devolvió nada"
            continue

        # El chequeo barato de píxeles corre SIEMPRE, también en modo limpio. Antes era
        # `"" if limpio else ...`, y como `limpio = not reservar_titular`, el camino por
        # defecto de `crear_contenido_social` (sin `visual`) era justo el que se quedaba sin el
        # gate. Una escena que se rinde a media altura es mala en los dos modos, y esto es
        # aritmética de píxeles: gratis, y corre ANTES de gastar la llamada de visión. Aria
        # lo ejecuta siempre (`_detectar_franja_vacia`, `linkedin_content.py:799-824`).
        problema_pixels = _franja_vacia(imagen)
        if problema_pixels:
            feedback = motivo = problema_pixels
            continue

        ultima_imagen = imagen
        aprobada, problema = await criticar(imagen, claim, llamar_modelo, tema=tema, limpio=limpio)
        if aprobada:
            return imagen, ""
        feedback = motivo = problema

    return ultima_imagen, motivo


# ---------------------------------------------------------------------------
# 5. Composición del titular sobre la foto (PIL — ver docstring del módulo)
# ---------------------------------------------------------------------------

# Factor de resolución de la composición. Aria renderiza con Playwright a
# `device_scale_factor=2` sobre un `#card` de 1080x1350, o sea un PNG real de 2160x2700 con el
# texto trazado a 2x; Edecán componía con PIL directamente a 1x y el titular se veía blando en
# cualquier pantalla retina. Configurable por si un tenant necesita bajar el peso del PNG.
_ESCALA_COMPOSICION = int(os.getenv("EDECAN_IMG_ESCALA", "2"))

_COLOR_ACENTO = (232, 163, 61)  # #E8A33D — barra del kicker (foto.html)
_COLOR_HEADLINE_ACENTO = (240, 185, 90)  # #F0B95A — palabra resaltada del headline (<em>)
_COLOR_TEXTO = (255, 255, 255, 255)
_COLOR_APOYO = (255, 255, 255, 217)  # rgba(255,255,255,0.85)
_COLOR_SCRIM = (10, 8, 6)

# (posición, alpha) del degradado inferior — mismas paradas que `.scrim` en foto.html, con el
# ángulo de 178° aproximado a vertical puro (diferencia imperceptible en este aspecto de lienzo).
_PARADAS_SCRIM = ((0.0, 0.32), (0.32, 0.05), (0.60, 0.48), (1.0, 0.92))

# Rutas de fuentes del sistema por (familia, negrita), en orden de preferencia — mismo patrón
# defensivo que `social.py::_font()` (macOS primero, luego Linux/DejaVu/Liberation, y si ninguna
# existe, `_fuente()` cae a la fuente por defecto de Pillow: nunca revienta).
_FUENTES: dict[tuple[str, bool], tuple[str, ...]] = {
    ("serif", True): (
        "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
    ),
    ("serif", False): (
        "/System/Library/Fonts/Supplemental/Georgia.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    ),
    # Itálica REAL para el acento del headline. En `foto.html` de Aria el acento es
    # `.headline em { font-style: italic; color: #F0B95A }`: itálica Y color, y eso es la mitad
    # del carácter editorial de la pieza. Aquí sólo se coloreaba, con la misma fuente recta.
    # Si no hay itálica instalada, `_fuente` cae a la recta y el acento sigue resaltado por
    # color -- nunca se pierde la composición por esto.
    ("serif_italica", True): (
        "/System/Library/Fonts/Supplemental/Georgia Bold Italic.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-BoldItalic.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-BoldItalic.ttf",
    ),
    ("mono", True): (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/System/Library/Fonts/SFNSMono.ttf",  # macOS no trae variante bold real de SF Mono
    ),
    ("mono", False): (
        "/System/Library/Fonts/SFNSMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    ),
    ("sans", True): (
        "/System/Library/Fonts/SFNSDisplay-Bold.otf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
    ("sans", False): (
        "/System/Library/Fonts/SFNS.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ),
}


def _fuente(size: int, familia: str = "sans", negrita: bool = False) -> ImageFont.FreeTypeFont:
    """Localiza una fuente instalada del sistema; nunca falla. 'serif' para el headline
    editorial (equivalente a Fraunces en `foto.html`), 'serif_italica' para el acento, 'mono'
    para kicker/apoyo (equivalente a Geist Mono). Si la familia pedida no existe, cae a la
    'serif' recta antes que al bitmap por defecto de Pillow."""
    rutas = list(_FUENTES.get((familia, negrita), ()))
    if familia == "serif_italica":
        rutas += list(_FUENTES.get(("serif", True), ()))
    for ruta in rutas:
        try:
            return ImageFont.truetype(ruta, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _cover_fit(im: Image.Image, width: int, height: int) -> Image.Image:
    """Redimensiona `im` para cubrir width×height sin deformar (equivalente a `object-fit:
    cover` de `foto.html`) y recorta centrado con un sesgo hacia arriba (`object-position:
    center 20%`, para no cortar cabezas)."""
    src_w, src_h = im.size
    escala = max(width / src_w, height / src_h)
    nueva_w, nueva_h = max(1, round(src_w * escala)), max(1, round(src_h * escala))
    im = im.resize((nueva_w, nueva_h), Image.LANCZOS)
    x0 = (nueva_w - width) // 2
    y0 = max(0, min(round((nueva_h - height) * 0.20), nueva_h - height))
    return im.crop((x0, y0, x0 + width, y0 + height))


def _interp_scrim(t: float) -> float:
    for (t0, a0), (t1, a1) in zip(_PARADAS_SCRIM, _PARADAS_SCRIM[1:], strict=False):
        if t0 <= t <= t1:
            f = (t - t0) / (t1 - t0) if t1 != t0 else 1.0
            return a0 + (a1 - a0) * f
    return _PARADAS_SCRIM[-1][1]


def _pintar_scrim(im: Image.Image, width: int, height: int) -> None:
    """Superpone el degradado oscuro inferior (mismas paradas que `.scrim` en `foto.html`) para
    que el titular se lea encima de la foto. Muta `im` (RGB) en el lugar."""
    columna = Image.new("L", (1, height))
    for y in range(height):
        columna.putpixel((0, y), round(_interp_scrim(y / max(height - 1, 1)) * 255))
    mascara = columna.resize((width, height))
    overlay = Image.new("RGB", (width, height), _COLOR_SCRIM)
    im.paste(overlay, (0, 0), mascara)


def _envolver(
    draw: ImageDraw.ImageDraw, texto: str, fuente: ImageFont.FreeTypeFont, max_ancho: float
) -> list[str]:
    """Envuelve `texto` a `max_ancho` píxeles, respetando saltos de línea explícitos ("\\n") como
    cortes forzados y sub-envolviendo cada segmento por palabras."""
    lineas: list[str] = []
    for parrafo in (texto or "").split("\n"):
        palabras = parrafo.split()
        if not palabras:
            lineas.append("")
            continue
        actual = palabras[0]
        for palabra in palabras[1:]:
            candidata = f"{actual} {palabra}"
            if draw.textlength(candidata, font=fuente) <= max_ancho:
                actual = candidata
            else:
                lineas.append(actual)
                actual = palabra
        lineas.append(actual)
    return lineas


def _ajustar_headline(
    draw: ImageDraw.ImageDraw,
    headline: str,
    max_ancho: float,
    max_alto: float,
    tam_max: int,
    tam_min: int,
) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """Reduce el tamaño del headline (serif, negrita) hasta que quepa en `max_alto`, igual que
    el ajuste de `_offline_social_card` en `social.py` pero preservando saltos de línea
    explícitos vía `_envolver`."""
    tam_min = min(tam_min, tam_max)
    fuente = lineas = None
    for tam in range(tam_max, tam_min - 1, -4):
        fuente = _fuente(tam, "serif", True)
        lineas = _envolver(draw, headline, fuente, max_ancho)
        alto_total = round(tam * 0.99) * len(lineas)
        if alto_total <= max_alto or tam <= tam_min:
            break
    return fuente, lineas


def _dibujar_headline(
    draw: ImageDraw.ImageDraw,
    lineas: list[str],
    fuente: ImageFont.FreeTypeFont,
    accent: str,
    x: int,
    y_inicio: int,
    alto_linea: int,
) -> int:
    """Dibuja las líneas del headline, resaltando SOLO la primera aparición de `accent` (mismo
    criterio que `_wrap_accent` en `li_compose.py`: `.replace(..., 1)`). `accent` debe caer
    dentro de una sola línea ya envuelta para resaltarse — limitación heredada de operar sobre
    texto ya wrapeado, aceptable porque `accent` es pensado como una palabra/frase corta.

    El acento va en ITÁLICA y en color, como `.headline em` en `foto.html` de Aria, y se
    busca SIN distinguir mayúsculas: antes era `if accent in linea`, sensible a la
    capitalización, así que un acento devuelto con otra caja simplemente no resaltaba nada y
    nadie se enteraba. (`editorial.normalizar_visual` ya fuerza que `accent` sea una subcadena
    real del headline; esto es el segundo cinturón, sobre el texto ya envuelto.)

    Devuelve la coordenada Y donde terminó el bloque (para anclar el apoyo debajo)."""
    accent = (accent or "").strip()
    fuente_acento = _fuente(fuente.size, "serif_italica", True) if accent else fuente
    ya_marcado = False
    y = y_inicio
    for linea in lineas:
        indice = linea.lower().find(accent.lower()) if accent and not ya_marcado else -1
        if indice >= 0:
            pre = linea[:indice]
            resaltado = linea[indice : indice + len(accent)]
            post = linea[indice + len(accent) :]
            cursor_x = x
            if pre:
                draw.text((cursor_x, y), pre, font=fuente, fill=_COLOR_TEXTO)
                cursor_x += draw.textlength(pre, font=fuente)
            draw.text((cursor_x, y), resaltado, font=fuente_acento, fill=_COLOR_HEADLINE_ACENTO)
            cursor_x += draw.textlength(resaltado, font=fuente_acento)
            if post:
                draw.text((cursor_x, y), post, font=fuente, fill=_COLOR_TEXTO)
            ya_marcado = True
        else:
            draw.text((x, y), linea, font=fuente, fill=_COLOR_TEXTO)
        y += alto_linea
    return y


def componer_titular(
    foto_bytes: bytes,
    *,
    kicker: str = "",
    headline: str = "",
    accent: str = "",
    support: str = "",
    width: int = 1080,
    height: int = 1350,
    escala: int = _ESCALA_COMPOSICION,
) -> bytes:
    """Compone el titular editorial sobre la foto ya generada/aprobada (migración de
    `scripts/li_compose.py` + `features/li_templates/foto.html` de Aria, de Playwright/HTML a
    PIL puro — ver docstring del módulo para el porqué). Layout: foto a lienzo completo
    (cover-fit, sesgo 20% hacia arriba), scrim degradado inferior, kicker con barra de acento a
    la izquierda, headline serif grande con la primera aparición de `accent` resaltada en color,
    y apoyo en monoespaciada debajo. `width`/`height` son ajustables por si el llamador necesita
    otra relación de aspecto que la vertical 1080x1350 de LinkedIn que usaba Aria (p. ej. el
    1200x627 apaisado de `PlatformSpec` en `social.py` para otras plataformas).

    `escala` multiplica el lienzo antes de dibujar: con 2 se compone a 2160x2700 y el texto se
    traza al doble de resolución, igual que Aria, que renderiza con Playwright a
    `device_scale_factor=2` sobre un `#card` de 1080x1350. Componer a 1x era la diferencia de
    calidad percibida más grande de las dos piezas -- en LinkedIn y en cualquier pantalla
    retina el titular a 1x se ve blando -- y la más barata de arreglar, porque todos los
    tamaños de esta función ya se derivan de `width`/`height`.

    Devuelve PNG en bytes."""
    escala = max(1, int(escala))
    width, height = width * escala, height * escala
    with Image.open(io.BytesIO(foto_bytes)) as _im:
        lienzo = _cover_fit(_im.convert("RGB"), width, height)
    _pintar_scrim(lienzo, width, height)
    lienzo = lienzo.convert("RGBA")
    draw = ImageDraw.Draw(lienzo)

    margen = round(width * 0.0556)  # 60/1080, igual que el padding lateral de foto.html
    pad_inferior = round(height * 0.0563)  # 76/1350
    ancho_contenido = width - 2 * margen

    alto_kicker_bloque = 0
    if kicker:
        fuente_kicker = _fuente(max(14, round(height * 0.0178)), "mono", True)
        y_kicker = round(height * 0.0415)
        barra_ancho = max(3, round(width * 0.0046))
        alto_barra = fuente_kicker.size + 8
        draw.rectangle(
            (margen, y_kicker, margen + barra_ancho, y_kicker + alto_barra),
            fill=_COLOR_ACENTO,
        )
        draw.text(
            (margen + barra_ancho + 20, y_kicker),
            kicker.upper(),
            font=fuente_kicker,
            fill=_COLOR_TEXTO,
        )
        alto_kicker_bloque = y_kicker + alto_barra + round(height * 0.03)

    lineas_apoyo: list[str] = []
    fuente_apoyo = None
    alto_linea_apoyo = 0
    alto_bloque_apoyo = 0
    if support:
        fuente_apoyo = _fuente(max(14, round(height * 0.0215)), "mono")
        lineas_apoyo = _envolver(draw, support, fuente_apoyo, ancho_contenido)
        alto_linea_apoyo = round(fuente_apoyo.size * 1.42)
        alto_bloque_apoyo = alto_linea_apoyo * len(lineas_apoyo) + round(height * 0.0222)

    # El titular ya no puede comerse el 62% de la altura: con ese techo un titular
    # de pocas palabras nunca se achicaba y salía enorme (Ada: "el titular
    # debería ser más pequeño y más minimalista"). Se limita a ~40% y el tamaño
    # máximo baja proporcionalmente. `tam_max` se ata al ANCHO, no al alto, que es
    # lo que de verdad manda cuánto texto cabe por línea: 0.072 del ancho es más
    # cercano al 100px/1080 de la plantilla `foto.html` de Aria que el 0.074 del
    # alto, que sobre un lienzo 1024x1536 se inflaba a 113px.
    max_alto_headline = max(height * 0.22, height * 0.42 - alto_kicker_bloque)
    fuente_headline, lineas_headline = _ajustar_headline(
        draw,
        headline or "",
        ancho_contenido,
        max_alto_headline,
        tam_max=max(26, round(width * 0.072)),
        tam_min=max(22, round(width * 0.036)),
    )
    alto_linea_headline = round(fuente_headline.size * 0.99)
    alto_bloque_headline = alto_linea_headline * len(lineas_headline)

    y_bloque = height - pad_inferior - alto_bloque_apoyo - alto_bloque_headline
    y_bloque = max(y_bloque, alto_kicker_bloque or margen)

    y_fin = _dibujar_headline(
        draw,
        lineas_headline,
        fuente_headline,
        accent,
        margen,
        y_bloque,
        alto_linea_headline,
    )

    if lineas_apoyo:
        y = y_fin + round(height * 0.0222)
        for linea in lineas_apoyo:
            draw.text((margen, y), linea, font=fuente_apoyo, fill=_COLOR_APOYO)
            y += alto_linea_apoyo

    salida = io.BytesIO()
    lienzo.convert("RGB").save(salida, format="PNG", optimize=True)
    return salida.getvalue()


__all__ = [
    "GenerarImagen",
    "LlamarModelo",
    "REGLAS_ARTE",
    "construir_prompt_imagen",
    "criticar",
    "generar_con_critica",
    "componer_titular",
]
