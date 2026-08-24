"""Pipeline editorial de imagen: dirección de arte + crítica automática + composición del
titular sobre la foto (`ARCHITECTURE.md` §10.14, mismo paquete que `social.py`/`providers.py`).

Migrado desde REFERENCIA (`referencia/features/linkedin_content.py` funciones `_generar_imagen_libre`,
`_revisar_imagen`, `_generar_con_iteracion`, `_componer_pieza`; y `referencia/scripts/li_compose.py`)
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
layout que `referencia/features/li_templates/foto.html` (foto a lienzo completo, scrim degradado
inferior, kicker con barra de acento, headline serif grande con una palabra resaltada, apoyo en
monoespaciada) usando `PIL.ImageDraw`/`ImageFont`, con la misma estrategia defensiva de búsqueda
de fuentes del sistema que ya usa `social.py::_font()` (nunca falla: cae al font por defecto de
Pillow si no encuentra ninguna fuente instalada).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import statistics
from collections.abc import Awaitable, Callable
from functools import lru_cache
from pathlib import Path

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
    REFERENCIA, `_generar_imagen_libre`: pasarle el contenido real y dejar que el modelo de imagen
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
    `_revisar_imagen` de REFERENCIA: controla costo/latencia de la llamada de crítica). Si algo
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
    una falla de la crítica en sí misma (mismo criterio de `_revisar_imagen` en REFERENCIA).

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
# REFERENCIA, renombrado porque este módulo no es específico de LinkedIn.
_N_INTENTOS = int(os.getenv("EDECAN_IMG_INTENTOS", "3"))


def _franja_vacia(imagen_bytes: bytes) -> str:
    """Detecta con pura aritmética de píxeles (sin LLM, prácticamente gratis) una escena que
    termina a media altura y deja la mitad inferior casi sin detalle — el patrón típico de una
    imagen que "se rindió" antes de llenar el lienzo. Corre ANTES de gastar una llamada de
    crítica con visión. Ported de `_detectar_franja_vacia` (REFERENCIA,
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
    """Genera SECUENCIAL, no en paralelo (mismo criterio que `_generar_con_iteracion` de REFERENCIA:
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
        # aritmética de píxeles: gratis, y corre ANTES de gastar la llamada de visión. REFERENCIA
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

# Factor de resolución de la composición. REFERENCIA renderiza con Playwright a
# `device_scale_factor=2` sobre un `#card` de 1080x1350, o sea un PNG real de 2160x2700 con el
# texto trazado a 2x; Edecán componía con PIL directamente a 1x y el titular se veía blando en
# cualquier pantalla retina. Configurable por si un tenant necesita bajar el peso del PNG.
_ESCALA_COMPOSICION = int(os.getenv("EDECAN_IMG_ESCALA", "2"))

_COLOR_ACENTO = (232, 163, 61)  # #E8A33D — barra del kicker (foto.html)
_COLOR_HEADLINE_ACENTO = (240, 185, 90)  # #F0B95A — palabra resaltada del headline (<em>)
_COLOR_TEXTO = (255, 255, 255, 255)
_COLOR_APOYO = (255, 255, 255, 217)  # rgba(255,255,255,0.85)
_COLOR_SCRIM = (10, 8, 6)

# Gradiente Aurora de la marca Acme (Brand Guidelines v1.0), mismas paradas que
# `.headline em` en `foto_organization.html`: `linear-gradient(110deg, #6E62FF 0%, #56D2E6 100%)`.
# Se aproxima a un degradado horizontal puro (izquierda→derecha): a 110° la diferencia contra
# el ángulo real es imperceptible sobre el ancho de una sola palabra/frase corta como es
# siempre `accent`, y evita tener que rotar el recorte de texto para un efecto invisible.
_AURORA_INICIO = (0x6E, 0x62, 0xFF)  # #6E62FF
_AURORA_FIN = (0x56, 0xD2, 0xE6)  # #56D2E6

# Asset del logo D de Acme (silueta blanca, canal alfa real) empaquetado con este módulo
# — ver `componer_titular_organization`. Equivalente al `{{LOGO}}` de `foto_organization.html`
# (REFERENCIA lo resuelve por `logo_path` en `features/organization_linkedin_content.py`).
# Logo OPCIONAL de la marca de la organización. El archivo puede no existir (un
# despliegue limpio no trae el logo de nadie): el compositor lo omite y sigue.
_LOGO_ORGANIZATION_PATH = Path(__file__).resolve().parent / "assets" / "d-symbol-white.png"


@lru_cache(maxsize=1)
def _logo_organization_bundled() -> bytes | None:
    """Carga el PNG del logo D empaquetado, una sola vez. `None` si no está presente —
    `componer_titular_organization` nunca debe reventar por un asset faltante, solo omite el
    logo (mismo criterio defensivo que `_fuente`: degradar, no bloquear)."""
    try:
        return _LOGO_ORGANIZATION_PATH.read_bytes()
    except OSError:
        return None


# (posición, alpha) del degradado inferior — mismas paradas que `.scrim` en foto.html, con el
# ángulo de 178° aproximado a vertical puro (diferencia imperceptible en este aspecto de lienzo).
_PARADAS_SCRIM = ((0.0, 0.32), (0.32, 0.05), (0.60, 0.48), (1.0, 0.92))

# Carpeta de fuentes de Windows (`%WINDIR%\Fonts`, con fallback literal a `C:\Windows\Fonts`
# si la variable de entorno no está definida). Sin esto, `_fuente()` no tenía NINGÚN
# candidato válido en Windows -- ni macOS ni `/usr/share/fonts` existen ahí -- y caía
# siempre al bitmap diminuto de `ImageFont.load_default()`, degradando toda imagen
# editorial (headline, kicker, acento itálico) en cualquier instalación de Windows.
_WINDIR = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows"
_WINDOWS_FONTS_DIR = Path(_WINDIR) / "Fonts"


def _win(nombre: str) -> str:
    return str(_WINDOWS_FONTS_DIR / nombre)


# Rutas de fuentes del sistema por (familia, negrita), en orden de preferencia — mismo patrón
# defensivo que `social.py::_font()` (macOS primero, luego Windows, luego Linux/DejaVu/
# Liberation, y si ninguna existe, `_fuente()` cae a la fuente por defecto de Pillow: nunca
# revienta).
_FUENTES: dict[tuple[str, bool], tuple[str, ...]] = {
    ("serif", True): (
        "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
        _win("georgiab.ttf"),
        _win("timesbd.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
    ),
    ("serif", False): (
        "/System/Library/Fonts/Supplemental/Georgia.ttf",
        _win("georgia.ttf"),
        _win("times.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    ),
    # Itálica REAL para el acento del headline. En `foto.html` de REFERENCIA el acento es
    # `.headline em { font-style: italic; color: #F0B95A }`: itálica Y color, y eso es la mitad
    # del carácter editorial de la pieza. Aquí sólo se coloreaba, con la misma fuente recta.
    # Si no hay itálica instalada, `_fuente` cae a la recta y el acento sigue resaltado por
    # color -- nunca se pierde la composición por esto.
    ("serif_italica", True): (
        "/System/Library/Fonts/Supplemental/Georgia Bold Italic.ttf",
        _win("georgiaz.ttf"),
        _win("timesbi.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-BoldItalic.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-BoldItalic.ttf",
    ),
    ("mono", True): (
        _win("consolab.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/System/Library/Fonts/SFNSMono.ttf",  # macOS no trae variante bold real de SF Mono
    ),
    ("mono", False): (
        "/System/Library/Fonts/SFNSMono.ttf",
        _win("consola.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    ),
    ("sans", True): (
        "/System/Library/Fonts/SFNSDisplay-Bold.otf",
        _win("arialbd.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
    ("sans", False): (
        "/System/Library/Fonts/SFNS.ttf",
        _win("arial.ttf"),
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


def _pintar_scrim(im: Image.Image, width: int, height: int, *, invertir: bool = False) -> None:
    """Superpone el degradado oscuro inferior (mismas paradas que `.scrim` en `foto.html`) para
    que el titular se lea encima de la foto. Muta `im` (RGB) en el lugar.

    `invertir=True` voltea el degradado a la mitad SUPERIOR (oscuro arriba, transparente
    abajo) — lo usa el layout "encabezado", que ancla el titular arriba en vez de abajo.
    Mismas paradas, solo espejadas: el aspecto es el mismo que el scrim inferior rotado."""
    columna = Image.new("L", (1, height))
    for y in range(height):
        t = y / max(height - 1, 1)
        if invertir:
            t = 1.0 - t
        columna.putpixel((0, y), round(_interp_scrim(t) * 255))
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
    familia: str = "serif",
) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """Reduce el tamaño del headline (negrita) hasta que quepa en `max_alto`, igual que
    el ajuste de `_offline_social_card` en `social.py` pero preservando saltos de línea
    explícitos vía `_envolver`. `familia`: 'serif' (plantilla personal, recta) o
    'serif_italica' (plantilla Acme: titular COMPLETO en itálica, `foto_organization.html`
    `.headline { font-style: italic }` — ahí la itálica es del headline entero, no solo del
    acento)."""
    tam_min = min(tam_min, tam_max)
    fuente = lineas = None
    for tam in range(tam_max, tam_min - 1, -4):
        fuente = _fuente(tam, familia, True)
        lineas = _envolver(draw, headline, fuente, max_ancho)
        alto_total = round(tam * 0.99) * len(lineas)
        if alto_total <= max_alto or tam <= tam_min:
            break
    return fuente, lineas


def _texto_con_gradiente(
    canvas: Image.Image,
    texto: str,
    fuente: ImageFont.FreeTypeFont,
    x: int,
    y: int,
    color_inicio: tuple[int, int, int],
    color_fin: tuple[int, int, int],
) -> None:
    """Pinta `texto` en `(x, y)` con un degradado horizontal izquierda→derecha entre
    `color_inicio` y `color_fin`, recortado a la silueta de las letras — equivalente a
    `background-clip: text` en `foto_organization.html` (`.headline em`, gradiente Aurora de la
    marca). `(x, y)` es la misma esquina superior-izquierda que usa `draw.text(...)` (ancla
    por defecto 'la'), así que sustituye un `draw.text(..., fill=color)` sin desplazar nada.

    Estrategia: renderiza `texto` en blanco sobre un lienzo transparente para obtener SOLO
    la máscara alfa de las letras (glifos), arma una tira de 1px de alto con el degradado y
    la estira a la altura de línea (mismo truco que `_pintar_scrim`: una columna/fila de 1px
    escalada, en vez de recorrer cada píxel), y pega el degradado sobre `canvas` usando esa
    máscara — solo quedan pintados los píxeles que son parte de una letra. Muta `canvas`
    (debe ser RGBA) en el lugar."""
    ancho = max(1, round(ImageDraw.Draw(canvas).textlength(texto, font=fuente)))
    # Alto generoso (2x el tamaño de fuente) para no recortar ascendentes/descendentes al
    # renderizar la máscara — el propio `bbox` de la máscara no importa, solo su alfa.
    alto = max(1, fuente.size * 2)
    mascara = Image.new("L", (ancho, alto), 0)
    ImageDraw.Draw(mascara).text((0, 0), texto, font=fuente, fill=255)
    fila = Image.new("RGB", (ancho, 1))
    for gx in range(ancho):
        t = gx / max(ancho - 1, 1)
        fila.putpixel(
            (gx, 0),
            tuple(round(color_inicio[i] + (color_fin[i] - color_inicio[i]) * t) for i in range(3)),
        )
    gradiente = fila.resize((ancho, alto))
    canvas.paste(gradiente, (x, y), mascara)


def _dibujar_headline(
    draw: ImageDraw.ImageDraw,
    canvas: Image.Image,
    lineas: list[str],
    fuente: ImageFont.FreeTypeFont,
    accent: str,
    x: int,
    y_inicio: int,
    alto_linea: int,
    *,
    gradiente_acento: tuple[tuple[int, int, int], tuple[int, int, int]] | None = None,
    color_acento: tuple[int, int, int] = _COLOR_HEADLINE_ACENTO,
) -> int:
    """Dibuja las líneas del headline, resaltando SOLO la primera aparición de `accent` (mismo
    criterio que `_wrap_accent` en `li_compose.py`: `.replace(..., 1)`). `accent` debe caer
    dentro de una sola línea ya envuelta para resaltarse — limitación heredada de operar sobre
    texto ya wrapeado, aceptable porque `accent` es pensado como una palabra/frase corta.

    El acento va en ITÁLICA y en color, como `.headline em` en `foto.html`/`foto_organization.html`
    de REFERENCIA, y se busca SIN distinguir mayúsculas: antes era `if accent in linea`, sensible a
    la capitalización, así que un acento devuelto con otra caja simplemente no resaltaba nada y
    nadie se enteraba. (`editorial.normalizar_visual` ya fuerza que `accent` sea una subcadena
    real del headline; esto es el segundo cinturón, sobre el texto ya envuelto.)

    `gradiente_acento`: si se da un par (color_inicio, color_fin), el acento se pinta con ese
    degradado horizontal recortado al texto (`_texto_con_gradiente`) en vez del color sólido
    `_COLOR_HEADLINE_ACENTO` — la plantilla Acme (`foto_organization.html`) usa el gradiente
    Aurora de la marca ahí en vez del ámbar de la plantilla personal. `canvas` es la MISMA
    imagen RGBA que respalda `draw`; el degradado se compone directamente sobre ella.

    `color_acento`: color sólido del acento cuando NO hay `gradiente_acento`. Por defecto el
    ámbar de la plantilla personal; el layout "banda" lo cambia a oscuro porque ahí el fondo
    de la palabra resaltada es la banda ámbar, no la foto (ver `_componer_layout_banda`).

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
            if gradiente_acento is not None:
                _texto_con_gradiente(
                    canvas, resaltado, fuente_acento, round(cursor_x), y, *gradiente_acento
                )
            else:
                draw.text((cursor_x, y), resaltado, font=fuente_acento, fill=color_acento)
            cursor_x += draw.textlength(resaltado, font=fuente_acento)
            if post:
                draw.text((cursor_x, y), post, font=fuente, fill=_COLOR_TEXTO)
            ya_marcado = True
        else:
            draw.text((x, y), linea, font=fuente, fill=_COLOR_TEXTO)
        y += alto_linea
    return y


def _componer_titular_interno(
    foto_bytes: bytes,
    *,
    kicker: str,
    headline: str,
    accent: str,
    support: str,
    width: int,
    height: int,
    escala: int,
    familia_headline: str,
    gradiente_acento: tuple[tuple[int, int, int], tuple[int, int, int]] | None,
    logo: Image.Image | None,
) -> bytes:
    """Motor compartido de `componer_titular` (plantilla personal) y
    `componer_titular_organization` (plantilla de la página de LinkedIn de Acme) — mismo
    layout base (foto cover-fit, scrim, kicker, headline, apoyo), con tres ejes que varían
    entre plantillas: la familia del headline (recta vs. itálica completa), el relleno del
    acento (color sólido vs. degradado recortado al texto) y qué va en la esquina del kicker
    (barra de color vs. logo con canal alfa). Ver docstrings públicos para el detalle de cada
    plantilla. Devuelve PNG en bytes."""
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
    if logo is not None:
        # Layout de `foto_organization.html` `.brand`: logo D (canal alfa real, `Image.paste`
        # con la propia imagen como máscara) + kicker al lado, SIN la barra de acento de la
        # plantilla personal (`top: 52px; left: 60px; gap: 18px` sobre el lienzo de
        # referencia 1080x1350 -> fracciones del lienzo real, igual que el resto de esta
        # función).
        y_logo = round(height * 0.0385)  # 52/1350
        lado_logo = round(width * 0.0593)  # 64/1080
        logo_ajustado = logo.resize((lado_logo, lado_logo), Image.LANCZOS)
        lienzo.paste(logo_ajustado, (margen, y_logo), logo_ajustado)
        if kicker:
            fuente_kicker = _fuente(max(14, round(height * 0.0170)), "mono", True)  # 23/1350
            y_kicker = y_logo + (lado_logo - fuente_kicker.size) // 2
            x_kicker = margen + lado_logo + round(width * 0.0167)  # gap 18/1080
            draw.text((x_kicker, y_kicker), kicker.upper(), font=fuente_kicker, fill=_COLOR_TEXTO)
        alto_kicker_bloque = y_logo + lado_logo + round(height * 0.03)
    elif kicker:
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
    # de pocas palabras nunca se achicaba y salía enorme (Alex: "el titular
    # debería ser más pequeño y más minimalista"). Se limita a ~40% y el tamaño
    # máximo baja proporcionalmente. `tam_max` se ata al ANCHO, no al alto, que es
    # lo que de verdad manda cuánto texto cabe por línea: 0.072 del ancho es más
    # cercano al 100px/1080 de la plantilla `foto.html` de REFERENCIA que el 0.074 del
    # alto, que sobre un lienzo 1024x1536 se inflaba a 113px.
    max_alto_headline = max(height * 0.22, height * 0.42 - alto_kicker_bloque)
    fuente_headline, lineas_headline = _ajustar_headline(
        draw,
        headline or "",
        ancho_contenido,
        max_alto_headline,
        tam_max=max(26, round(width * 0.072)),
        tam_min=max(22, round(width * 0.036)),
        familia=familia_headline,
    )
    alto_linea_headline = round(fuente_headline.size * 0.99)
    alto_bloque_headline = alto_linea_headline * len(lineas_headline)

    y_bloque = height - pad_inferior - alto_bloque_apoyo - alto_bloque_headline
    y_bloque = max(y_bloque, alto_kicker_bloque or margen)

    y_fin = _dibujar_headline(
        draw,
        lienzo,
        lineas_headline,
        fuente_headline,
        accent,
        margen,
        y_bloque,
        alto_linea_headline,
        gradiente_acento=gradiente_acento,
    )

    if lineas_apoyo:
        y = y_fin + round(height * 0.0222)
        for linea in lineas_apoyo:
            draw.text((margen, y), linea, font=fuente_apoyo, fill=_COLOR_APOYO)
            y += alto_linea_apoyo

    salida = io.BytesIO()
    lienzo.convert("RGB").save(salida, format="PNG", optimize=True)
    return salida.getvalue()


# ---------------------------------------------------------------------------
# 6. Variedad de layout — que posts consecutivos no se vean idénticos
# ---------------------------------------------------------------------------
# El dueño: "mismo titular, mismo subtitulo, todo eso aburre". Hasta aquí
# `componer_titular` producía SIEMPRE el mismo layout (foto a lienzo completo,
# scrim inferior, kicker+headline+apoyo abajo). Aquí se definen 4 variantes
# VISUALMENTE distintas y se rota entre ellas. La rotación es DETERMINISTA:
# el llamador puede pasar `layout` (índice entero o nombre) para rotar por
# post, y si no lo pasa, se deriva de un hash ESTABLE del headline — mismo
# post siempre da el mismo layout, posts distintos varían. `hash()` de Python
# no sirve (es randomizado por `PYTHONHASHSEED`), se usa `hashlib`.

LAYOUT_CLASICO = "clasico"
LAYOUT_PURO = "puro"
LAYOUT_ENCABEZADO = "encabezado"
LAYOUTS: tuple[str, ...] = (LAYOUT_CLASICO, LAYOUT_PURO, LAYOUT_ENCABEZADO)
_N_LAYOUTS = len(LAYOUTS)


def _resolver_layout(layout: int | str | None, headline: str) -> int:
    """Devuelve el índice (0..N-1) del layout a usar. Reglas:

    - `None`: hash ESTABLE del headline (`hashlib.blake2b`, no `hash()` que es
      randomizado por proceso) módulo N — reproducible post a post, distinto
      entre posts. Es el default para no obligar al llamador a tocar nada.
    - `int`: módulo N (rotación explícita por contador de draft).
    - `str`: nombre del layout (ver `LAYOUTS`) o un entero como texto.
    - Cualquier valor no reconocido cae al clásico (0) — nunca revienta."""
    if layout is None:
        digest = hashlib.blake2b((headline or "").encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big") % _N_LAYOUTS
    if isinstance(layout, bool):  # bool es int en Python — no tiene sentido aquí
        return 0
    if isinstance(layout, int):
        return layout % _N_LAYOUTS
    nombre = str(layout).strip().lower()
    if nombre in LAYOUTS:
        return LAYOUTS.index(nombre)
    try:
        return int(nombre) % _N_LAYOUTS
    except ValueError:
        return 0


def _componer_layout_puro(foto_bytes: bytes, *, width: int, height: int, escala: int) -> bytes:
    """Layout "puro": la foto a lienzo completo, SIN ningún texto encima. Para posts donde la
    imagen ya es fuerte por sí sola y montarle titular la estorba. Mismo `cover-fit` que el
    resto para que la foto no se deforme."""
    escala = max(1, int(escala))
    width, height = width * escala, height * escala
    with Image.open(io.BytesIO(foto_bytes)) as _im:
        lienzo = _cover_fit(_im.convert("RGB"), width, height)
    salida = io.BytesIO()
    lienzo.save(salida, format="PNG", optimize=True)
    return salida.getvalue()


def _componer_layout_encabezado(
    foto_bytes: bytes,
    *,
    kicker: str,
    headline: str,
    accent: str,
    support: str,
    width: int,
    height: int,
    escala: int,
) -> bytes:
    """Layout "encabezado": scrim en la mitad SUPERIOR (no inferior) y el titular anclado
    ARRIBA — kicker con barra de acento, headline serif, apoyo — en vez de abajo. Mismo
    bloque de texto y mismas proporciones que el clásico, solo espejado verticalmente en
    cuanto a dónde vive el scrim y dónde se ancla el texto. Rompe el reflejo de "siempre
    abajo" sin inventar un lenguaje visual nuevo."""
    escala = max(1, int(escala))
    width, height = width * escala, height * escala
    with Image.open(io.BytesIO(foto_bytes)) as _im:
        lienzo = _cover_fit(_im.convert("RGB"), width, height)
    _pintar_scrim(lienzo, width, height, invertir=True)
    lienzo = lienzo.convert("RGBA")
    draw = ImageDraw.Draw(lienzo)

    margen = round(width * 0.0556)
    pad_superior = round(height * 0.0563)
    ancho_contenido = width - 2 * margen

    alto_kicker_bloque = 0
    if kicker:
        fuente_kicker = _fuente(max(14, round(height * 0.0178)), "mono", True)
        y_kicker = pad_superior
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
        alto_kicker_bloque = alto_barra + round(height * 0.03)

    lineas_apoyo: list[str] = []
    fuente_apoyo = None
    alto_linea_apoyo = 0
    if support:
        fuente_apoyo = _fuente(max(14, round(height * 0.0215)), "mono")
        lineas_apoyo = _envolver(draw, support, fuente_apoyo, ancho_contenido)
        alto_linea_apoyo = round(fuente_apoyo.size * 1.42)

    max_alto_headline = max(height * 0.22, height * 0.42 - alto_kicker_bloque)
    fuente_headline, lineas_headline = _ajustar_headline(
        draw,
        headline or "",
        ancho_contenido,
        max_alto_headline,
        tam_max=max(26, round(width * 0.072)),
        tam_min=max(22, round(width * 0.036)),
        familia="serif",
    )
    alto_linea_headline = round(fuente_headline.size * 0.99)

    y_inicio = pad_superior + alto_kicker_bloque
    y_fin = _dibujar_headline(
        draw,
        lienzo,
        lineas_headline,
        fuente_headline,
        accent,
        margen,
        y_inicio,
        alto_linea_headline,
        gradiente_acento=None,
    )
    if lineas_apoyo:
        y = y_fin + round(height * 0.0222)
        for linea in lineas_apoyo:
            draw.text((margen, y), linea, font=fuente_apoyo, fill=_COLOR_APOYO)
            y += alto_linea_apoyo

    salida = io.BytesIO()
    lienzo.convert("RGB").save(salida, format="PNG", optimize=True)
    return salida.getvalue()


def _componer_layout_banda(
    foto_bytes: bytes,
    *,
    kicker: str,
    headline: str,
    accent: str,
    support: str,
    width: int,
    height: int,
    escala: int,
) -> bytes:
    """Layout "banda": foto a lienzo completo con una BANDA sólida de color de acento
    (`_COLOR_ACENTO`, ámbar) a la DERECHA ocupando ~38% del ancho, a toda altura, que
    sostiene el titular (asimétrico). El headline vive dentro de la banda en blanco, y la
    palabra `accent` se resalta en OSCURO (`_COLOR_SCRIM`) en vez de ámbar — sobre la banda
    ámbar el ámbar no se vería, así que se invierte el contraste. El kicker va en mono blanco
    SIN barra (la banda YA es el acento). La foto queda visible en el ~62% izquierdo."""
    escala = max(1, int(escala))
    width, height = width * escala, height * escala
    with Image.open(io.BytesIO(foto_bytes)) as _im:
        lienzo = _cover_fit(_im.convert("RGB"), width, height)
    lienzo = lienzo.convert("RGBA")

    band_ancho = round(width * 0.38)
    x_band = width - band_ancho
    banda = Image.new("RGBA", (band_ancho, height), _COLOR_ACENTO + (255,))
    lienzo.paste(banda, (x_band, 0), banda)
    draw = ImageDraw.Draw(lienzo)

    margen_band = round(band_ancho * 0.10)
    ancho_contenido = band_ancho - 2 * margen_band
    x_texto = x_band + margen_band
    pad_superior = round(height * 0.06)

    alto_kicker_bloque = 0
    if kicker:
        fuente_kicker = _fuente(max(14, round(height * 0.0178)), "mono", True)
        draw.text((x_texto, pad_superior), kicker.upper(), font=fuente_kicker, fill=_COLOR_TEXTO)
        alto_kicker_bloque = fuente_kicker.size + round(height * 0.03)

    lineas_apoyo: list[str] = []
    fuente_apoyo = None
    alto_linea_apoyo = 0
    if support:
        fuente_apoyo = _fuente(max(14, round(height * 0.0215)), "mono")
        lineas_apoyo = _envolver(draw, support, fuente_apoyo, ancho_contenido)
        alto_linea_apoyo = round(fuente_apoyo.size * 1.42)

    max_alto_headline = max(height * 0.22, height * 0.46 - alto_kicker_bloque)
    fuente_headline, lineas_headline = _ajustar_headline(
        draw,
        headline or "",
        ancho_contenido,
        max_alto_headline,
        tam_max=max(26, round(width * 0.06)),
        tam_min=max(22, round(width * 0.032)),
        familia="serif",
    )
    alto_linea_headline = round(fuente_headline.size * 0.99)

    y_inicio = pad_superior + alto_kicker_bloque
    y_fin = _dibujar_headline(
        draw,
        lienzo,
        lineas_headline,
        fuente_headline,
        accent,
        x_texto,
        y_inicio,
        alto_linea_headline,
        gradiente_acento=None,
        color_acento=_COLOR_SCRIM,
    )
    if lineas_apoyo:
        y = y_fin + round(height * 0.0222)
        for linea in lineas_apoyo:
            draw.text((x_texto, y), linea, font=fuente_apoyo, fill=_COLOR_TEXTO)
            y += alto_linea_apoyo

    salida = io.BytesIO()
    lienzo.convert("RGB").save(salida, format="PNG", optimize=True)
    return salida.getvalue()


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
    layout: int | str | None = None,
) -> bytes:
    """Compone el titular editorial sobre la foto ya generada/aprobada (migración de
    `scripts/li_compose.py` + `features/li_templates/foto.html` de REFERENCIA, de Playwright/HTML a
    PIL puro — ver docstring del módulo para el porqué). `width`/`height` son ajustables por si
    el llamador necesita otra relación de aspecto que la vertical 1080x1350 de LinkedIn que
    usaba REFERENCIA (p. ej. el 1200x627 apaisado de `PlatformSpec` en `social.py` para otras
    plataformas).

    LAYOUTS — antes siempre se entregaba el mismo (foto a lienzo completo, scrim inferior,
    kicker con barra de acento, headline serif, apoyo abajo); el dueño: "mismo titular, mismo
    subtitulo, todo eso aburre". Ahora `componer_titular` rota entre 3 variantes
    visualmente distintas (ver `LAYOUTS`):

    - `"clasico"` (0): el de siempre — full-bleed, scrim inferior, kicker+headline+apoyo
      abajo. Sin regresión: idéntico a `_componer_titular_interno` tal cual existía.
    - `"puro"` (1): la foto sola, SIN texto encima (para piezas donde la imagen basta).
    - `"encabezado"` (2): scrim SUPERIOR y titular anclado ARRIBA en vez de abajo.

    Rotación DETERMINISTA para que posts consecutivos no se vean idénticos: `layout` acepta un
    índice entero (rotación por contador de draft/post) o un nombre. Si se omite (`None`), se
    deriva de un hash ESTABLE del headline (`hashlib`, no `hash()` que es randomizado por
    proceso): el mismo post siempre da el mismo layout, posts distintos varían. El llamador
    de `social.py` no pasa `layout` hoy (ver `_resolver_layout`), así que por defecto entra el
    hash del headline — no hace falta tocar otro paquete para tener variedad.

    `escala` multiplica el lienzo antes de dibujar: con 2 se compone a 2160x2700 y el texto se
    traza al doble de resolución, igual que REFERENCIA, que renderiza con Playwright a
    `device_scale_factor=2` sobre un `#card` de 1080x1350. Componer a 1x era la diferencia de
    calidad percibida más grande de las dos piezas -- en LinkedIn y en cualquier pantalla
    retina el titular a 1x se ve blando -- y la más barata de arreglar, porque todos los
    tamaños de esta función ya se derivan de `width`/`height`.

    Plantilla PERSONAL (sin logo, headline recto, acento ámbar sólido). Para la plantilla de
    la página de LinkedIn de Acme (logo D + headline itálico + acento con el gradiente
    Aurora de la marca) usar `componer_titular_organization` — esa NO rota layouts, mantiene su
    plantilla de marca fija.

    Devuelve PNG en bytes."""
    idx = _resolver_layout(layout, headline)
    if idx == 1:
        return _componer_layout_puro(foto_bytes, width=width, height=height, escala=escala)
    if idx == 2:
        return _componer_layout_encabezado(
            foto_bytes,
            kicker=kicker,
            headline=headline,
            accent=accent,
            support=support,
            width=width,
            height=height,
            escala=escala,
        )
    # idx == 0 (clásico): layout original, sin regresión — mismo motor de siempre.
    return _componer_titular_interno(
        foto_bytes,
        kicker=kicker,
        headline=headline,
        accent=accent,
        support=support,
        width=width,
        height=height,
        escala=escala,
        familia_headline="serif",
        gradiente_acento=None,
        logo=None,
    )


def componer_titular_organization(
    foto_bytes: bytes,
    *,
    logo_bytes: bytes | None = None,
    kicker: str = "",
    headline: str = "",
    accent: str = "",
    support: str = "",
    width: int = 1080,
    height: int = 1350,
    escala: int = _ESCALA_COMPOSICION,
) -> bytes:
    """Variante de `componer_titular` para la plantilla de la página de LinkedIn de Acme
    (migración de `features/li_templates/foto_organization.html` de REFERENCIA — ver también
    `scripts/li_compose.py::_build_html`, que selecciona esta plantilla con `tipo ==
    "foto_organization"`). Mismo layout base que `componer_titular`, con tres diferencias:

    1. Logo D en la esquina superior izquierda (en vez de la barra de acento), pegado con
       `Image.paste(logo, pos, logo)` usando el propio canal alfa de la imagen como máscara.
       `logo_bytes` es opcional: si no se da, cae al asset empaquetado con este módulo
       (`assets/d-symbol-white.png`, ver `_logo_organization_bundled`); si ninguno de los dos
       está disponible, sigue componiendo TODO lo demás sin logo — igual que el resto de este
       módulo, un asset faltante nunca debe tumbar la composición completa.
    2. Titular COMPLETO en serif itálica (`.headline { font-style: italic }` en
       `foto_organization.html` — ahí la itálica es del headline entero, no solo del acento).
    3. La palabra `accent` con el gradiente Aurora de la marca (`#6E62FF` → `#56D2E6`)
       recortado al texto, en vez del ámbar sólido de la plantilla personal.

    Devuelve PNG en bytes."""
    logo_bytes = logo_bytes or _logo_organization_bundled()
    logo_img: Image.Image | None = None
    if logo_bytes:
        try:
            logo_img = Image.open(io.BytesIO(logo_bytes)).convert("RGBA")
        except Exception:
            logo_img = None
    return _componer_titular_interno(
        foto_bytes,
        kicker=kicker,
        headline=headline,
        accent=accent,
        support=support,
        width=width,
        height=height,
        escala=escala,
        familia_headline="serif_italica",
        gradiente_acento=(_AURORA_INICIO, _AURORA_FIN),
        logo=logo_img,
    )


__all__ = [
    "GenerarImagen",
    "LlamarModelo",
    "REGLAS_ARTE",
    "LAYOUTS",
    "LAYOUT_CLASICO",
    "LAYOUT_PURO",
    "LAYOUT_ENCABEZADO",
    "construir_prompt_imagen",
    "criticar",
    "generar_con_critica",
    "componer_titular",
    "componer_titular_organization",
]
