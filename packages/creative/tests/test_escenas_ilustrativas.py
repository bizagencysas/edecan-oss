"""Modo "escenas ilustrativas autorizadas" de `auditoria.auditar_hechos`.

**El defecto real, medido con corridas reales contra Workers AI.** El perfil editorial de
una cuenta (tabla `social_editorial_profiles`, campo `purpose`) puede autorizar
explícitamente que cada post ilustre su argumento con una escena venezolana concreta y
anónima -- "el fiao, la quincena, el alquiler" -- sin que esa escena venga de un artículo con
fecha. El auditor de hechos, por defecto, no distingue esa escena AUTORIZADA de una invención
sobre un hecho fechable, y la rechazaba como "desenlace inventado" o "cita no verificable":
un falso positivo, no el auditor haciendo su trabajo.

**El diseño.** La activación es una señal EXPLÍCITA y verificable por igualdad exacta -- el
campo dedicado `fact_check_mode` del perfil editorial, valor único `"escenas_ilustrativas"`
(`social.perfil_autoriza_escenas_ilustrativas`) -- nunca una expresión regular adivinando la
intención dentro de `purpose`/`notes` en prosa libre. Un perfil que no declara el campo (la
enorme mayoría, y cualquier cuenta de otro tenant) se comporta exactamente igual que antes:
fail-closed. Este archivo fija dos mitades, cada una con su propio bloque de tests:

1. La señal del perfil es exacta (Sección 1) y sólo afloja la propiedad concreta que debía
   aflojar en el prompt que de verdad ve el modelo (Sección 2) -- nunca la lista de
   invenciones que sigue prohibida sin excepción.
2. La cadena real (`redaccion.CrearPostLinkedInTool`, `social.CrearContenidoSocialTool`) sólo
   activa el modo cuando el perfil de ESE destino lo declaró, cableado de punta a punta
   (Secciones 3 y 4).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import edecan_creative.redaccion as redaccion
from edecan_creative.auditoria import auditar_hechos, marcas_de_hecho_duro
from edecan_creative.redaccion import CrearPostLinkedInTool
from edecan_creative.social import CrearContenidoSocialTool, perfil_autoriza_escenas_ilustrativas

# ---------------------------------------------------------------------------
# Fixtures locales (mismo criterio de auto-contención que el resto de `tests/`: cada archivo
# trae sus propios dobles, sin importarlos de otro archivo de test).
# ---------------------------------------------------------------------------


class UniqueUploader:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, ctx, *, data: bytes, filename: str, mime: str):  # noqa: ANN001
        file_id = uuid4()
        self.calls.append({"id": file_id, "data": data, "filename": filename, "mime": mime})
        return file_id, filename


class FakeLLM:
    """Doble de `LLMRouter`: devuelve las respuestas programadas en orden, una por llamada."""

    def __init__(self, textos: list[str]) -> None:
        self._pendientes = list(textos)
        self.calls: list[tuple] = []

    async def complete(self, alias, flags, request):  # noqa: ANN001
        self.calls.append((alias, flags, request))
        from edecan_llm.base import CompletionResponse, Usage

        texto = self._pendientes.pop(0) if self._pendientes else "{}"
        return CompletionResponse(
            text=texto, usage=Usage(input_tokens=10, output_tokens=10), stop_reason="end"
        )

    def prompt(self, indice: int) -> str:
        return self.calls[indice][2].messages[0].content

    def sistema(self, indice: int) -> str:
        return self.calls[indice][2].system or ""


class CapturaLlamarModelo:
    """Doble MÍNIMO de `auditoria.LlamarModelo` (lista de mensajes -> texto), para probar
    `auditar_hechos` directamente, sin pasar por `ctx.llm`/`redaccion`/`social`."""

    def __init__(self, respuesta: str) -> None:
        self._respuesta = respuesta
        self.mensajes: list[list[dict[str, str]]] = []

    async def __call__(self, mensajes: list[dict[str, str]]) -> str:
        self.mensajes.append(mensajes)
        return self._respuesta

    def _rol(self, rol: str) -> str:
        return next(m["content"] for m in self.mensajes[-1] if m["role"] == rol)

    @property
    def sistema(self) -> str:
        return self._rol("system")

    @property
    def usuario(self) -> str:
        return self._rol("user")


def _fila(config: dict[str, Any]) -> dict[str, Any]:
    return {"config": config, "version": 1}


def _borrador(texto: str, fuente: str = "") -> str:
    return json.dumps(
        {
            "tema": "Historial crediticio informal",
            "fuente_principal": fuente,
            "texto": texto,
            "titular_visual": "El cuaderno del bodeguero",
            "alt_text": "Un cuaderno de anotaciones sobre un mostrador.",
        }
    )


def _aprobado(motivo: str = "Tiene un ángulo concreto.") -> str:
    return json.dumps({"publicable": True, "motivo": motivo})


def _auditado(texto: str) -> str:
    return json.dumps({"publicable": True, "problemas": [], "texto": texto})


_BANCO = (
    "Acme es un buró de crédito alternativo para Venezuela. PRIMER CRÉDITO: la primera "
    "vez que alguien pide fiado, el bodeguero no lo conoce -- por eso empieza pidiendo poco."
)

# Escena anónima y genérica, sin fecha, sin cifra, sin nombre propio real y sin cita textual:
# exactamente el tipo de texto que el falso positivo medido rechazaba. Sin " -- " a propósito
# (`editorial.normalizar` lo convierte en un punto): el texto tiene que sobrevivir intacto a la
# normalización para que las comparaciones de este archivo midan el auditor, no ese detalle.
_ESCENA = (
    "Cuando un bodeguero empieza a fiar, no lleva la cuenta en la cabeza: la anota en un "
    "cuaderno. Esa anotación es, en el fondo, un historial crediticio informal, solo que "
    "nadie fuera de ese cuaderno puede verlo ni usarlo para decidir nada."
)

# Invención prohibida SIN excepción, disfrazada de la misma escena: trae una cifra inventada.
_INVENCION_CON_CIFRA = (
    "El 68% de los bodegueros del país usa un cuaderno para fiar, según un estudio reciente."
)

# Marca del prompt que sólo aparece cuando se entró de verdad al modo escena pura (las DOS
# condiciones: el perfil lo autorizó Y `marcas_de_hecho_duro` no encontró ni una marca).
_MARCA_MODO = "MODO VEREDICTO"


# ---------------------------------------------------------------------------
# 1. `perfil_autoriza_escenas_ilustrativas`: la señal es un valor CANÓNICO exacto, nunca una
#    adivinanza de expresión regular sobre `purpose`/`notes` en prosa libre.
# ---------------------------------------------------------------------------


def test_perfil_vacio_no_autoriza() -> None:
    assert perfil_autoriza_escenas_ilustrativas({}) is False


def test_perfil_con_el_campo_vacio_no_autoriza() -> None:
    assert perfil_autoriza_escenas_ilustrativas({"fact_check_mode": ""}) is False


def test_perfil_con_otro_valor_no_autoriza() -> None:
    assert perfil_autoriza_escenas_ilustrativas({"fact_check_mode": "estricto"}) is False


def test_una_frase_que_contiene_el_valor_no_cuenta_como_activacion() -> None:
    """Si esto activara el modo, cualquier perfil que mencionara la palabra en prosa libre
    (sin haberla puesto EXACTA en el campo dedicado) lo encendería por accidente -- justo el
    hueco que tienen las heurísticas de expresión regular que ya existen en el código
    (`redaccion._perfil_prohibe_numeros_extensos`, `_marca_para_una_sola_mencion`) y que este
    campo deliberadamente no repite."""
    perfil = {"fact_check_mode": "sí, quiero activar escenas_ilustrativas para esta cuenta"}
    assert perfil_autoriza_escenas_ilustrativas(perfil) is False


def test_el_purpose_en_prosa_libre_nunca_activa_el_modo_por_si_solo() -> None:
    """El caso real del encargo: el perfil autoriza la escena en `purpose`, en prosa, y eso
    NUNCA debe alcanzar para activar el modo -- hace falta el campo dedicado."""
    perfil = {
        "purpose": (
            "Cada post cuenta una escena venezolana concreta (el fiao, la quincena, el "
            "alquiler, la remesa, Cashea) donde Acme entra una sola vez, de forma "
            "natural."
        ),
    }
    assert perfil_autoriza_escenas_ilustrativas(perfil) is False


def test_el_valor_exacto_si_autoriza() -> None:
    assert perfil_autoriza_escenas_ilustrativas({"fact_check_mode": "escenas_ilustrativas"}) is True


def test_es_insensible_a_mayusculas_y_espacios_de_sobra() -> None:
    perfil = {"fact_check_mode": "  Escenas_Ilustrativas  "}
    assert perfil_autoriza_escenas_ilustrativas(perfil) is True


# ---------------------------------------------------------------------------
# 1b. `auditoria.marcas_de_hecho_duro`: LA LÍNEA, sin modelo de por medio.
#
# Esta sección es la que permite AFIRMAR (no esperar) que una invención prohibida sigue
# siendo imposible con el modo activo. Cada caso de abajo es una de las cinco invenciones que
# el encargo declaró intocables, y ninguna puede escribirse sin dejar al menos una marca que
# esta función encuentra ANTES de llamar al modelo. Si un caso de estos empezara a devolver
# lista vacía, el permiso se habría abierto de más -- por eso viven acá y no en un prompt.
# ---------------------------------------------------------------------------

_BANCO_FUENTE_PLANA = _BANCO


def test_la_escena_ilustrativa_legitima_no_tiene_marcas() -> None:
    assert marcas_de_hecho_duro(_ESCENA, _BANCO_FUENTE_PLANA) == []


def test_cifra_en_digitos_deja_marca() -> None:
    assert marcas_de_hecho_duro(_INVENCION_CON_CIFRA, _BANCO_FUENTE_PLANA)


def test_cifra_escrita_en_palabras_deja_marca() -> None:
    """Imprescindible, no cosmético: el perfil de la cuenta que motivó esto EXIGE que los
    números vayan en palabras ('cero números de dos o más dígitos'), así que buscar `\\d`
    solo no detectaría ni la mitad de las cifras inventables."""
    texto = "Ocho de cada diez personas que fían pagan puntual en Venezuela."
    assert marcas_de_hecho_duro(texto, _BANCO_FUENTE_PLANA)


def test_estadistica_sin_numero_deja_marca() -> None:
    texto = "Un estudio muestra que la mayoría paga puntual en Venezuela."
    assert marcas_de_hecho_duro(texto, _BANCO_FUENTE_PLANA)


def test_cita_textual_deja_marca() -> None:
    texto = 'El bodeguero lo dice claro: "yo fío porque conozco a mi gente".'
    assert marcas_de_hecho_duro(texto, _BANCO_FUENTE_PLANA)


def test_persona_real_y_nombrada_deja_marca() -> None:
    texto = "En Venezuela el ministro Delcy Rodríguez habla del crédito y el bodeguero fía igual."
    assert marcas_de_hecho_duro(texto, _BANCO_FUENTE_PLANA)


def test_institucion_real_compuesta_deja_marca_aunque_empiece_la_frase() -> None:
    """El nombre compuesto se revisa entero y desde la segunda palabra, así que un nombre al
    principio de la frase (donde cualquier palabra va en mayúscula) no se cuela."""
    texto = "El Banco Central de Venezuela decide y el bodeguero sigue fiando con su cuaderno."
    assert marcas_de_hecho_duro(texto, _BANCO_FUENTE_PLANA)


def test_noticia_inventada_deja_marca() -> None:
    """El caso duro: una marca REAL (está en las fuentes, así que ancla) haciendo algo en
    pasado, y al principio de la frase, donde la mayúscula podría pasar por ortografía. Lo
    agarra la regla de sujeto+pretérito, no la de nombres sin respaldo."""
    texto = "Cashea cambió sus condiciones y el bodeguero sigue fiando con su cuaderno."
    assert marcas_de_hecho_duro(texto, _BANCO_FUENTE_PLANA + " Cashea es una fuente de pago.")


def test_un_pasado_dentro_de_la_escena_no_deja_marca() -> None:
    """La otra cara de la misma regla: el pretérito tiene que venir PEGADO al nombre propio.
    Una subordinada en pasado dentro de una escena en presente no es una noticia, y si contara
    como tal el modo no serviría para casi ningún post real."""
    texto = "Anota el nombre, la cuenta y lo que se llevaron. Así lleva el bodeguero su libreta."
    assert marcas_de_hecho_duro(texto, _BANCO_FUENTE_PLANA) == []


def test_ronda_de_inversion_inventada_deja_marca() -> None:
    texto = "Acme cerró una ronda de inversión para llevar el buró a todo el país."
    assert marcas_de_hecho_duro(texto, _BANCO_FUENTE_PLANA)


def test_alianza_inventada_deja_marca() -> None:
    texto = "Acme firmó una alianza con las bodegas del país para anotar quién paga."
    assert marcas_de_hecho_duro(texto, _BANCO_FUENTE_PLANA)


def test_afirmacion_falsa_sobre_la_empresa_deja_marca() -> None:
    texto = "Acme es un banco regulado y garantiza la aprobación de tu crédito."
    assert marcas_de_hecho_duro(texto, _BANCO_FUENTE_PLANA)


def test_hablar_de_los_bancos_en_general_no_deja_marca() -> None:
    """La otra cara: 'los bancos' sin nombrar a nadie es una observación genérica sobre cómo
    funciona el crédito, que es de lo que esta clase de cuenta habla todo el tiempo. Medir la
    palabra sobre el texto entero mataba esa frase junto con la prohibida, y con ella casi
    todos los posts reales (medido: 1 de 3 borradores reales perdía el camino limpio por acá)."""
    texto = "Para los bancos ese historial no existe. El cuaderno del bodeguero no se puede ver."
    assert marcas_de_hecho_duro(texto, _BANCO_FUENTE_PLANA) == []


def test_el_verbo_mostrar_no_es_una_estadistica() -> None:
    """Regresión de un falso positivo medido en una corrida real: 'muestra' es el verbo, no una
    muestra estadística, y bloqueaba un borrador perfectamente limpio."""
    texto = "Pagar puntual muestra responsabilidad y el cuaderno del bodeguero no lo puede probar."
    assert marcas_de_hecho_duro(texto, _BANCO_FUENTE_PLANA) == []


def test_fecha_deja_marca() -> None:
    texto = "Desde marzo el bodeguero dejó de fiar y nadie sabe quién paga."
    assert marcas_de_hecho_duro(texto, _BANCO_FUENTE_PLANA)


def test_el_articulo_indefinido_no_cuenta_como_cantidad() -> None:
    """'un/una' es el artículo, no un numeral: si contara, ninguna escena en español pasaría
    nunca y el modo sería inútil por construcción."""
    assert marcas_de_hecho_duro("El bodeguero fía con una libreta.", _BANCO_FUENTE_PLANA) == []


def test_la_marca_propia_pasa_porque_las_fuentes_la_respaldan() -> None:
    """Un nombre propio no está prohibido: está prohibido uno que las fuentes NO respalden.
    El banco de contexto nombra a la marca, así que la marca ancla."""
    assert marcas_de_hecho_duro("Con Acme eso se puede mostrar.", _BANCO_FUENTE_PLANA) == []


def test_sin_fuentes_cualquier_nombre_propio_queda_sin_anclar() -> None:
    assert marcas_de_hecho_duro("Con Acme eso se puede mostrar.", "")


# ---------------------------------------------------------------------------
# 2. `auditoria.auditar_hechos`: qué cambia en el prompt, y qué NO cambia nunca.
# ---------------------------------------------------------------------------

_FUENTE_BANCO = [{"title": "Banco de contexto real de esta cuenta", "url": "", "snippet": _BANCO}]


async def test_por_defecto_el_prompt_no_menciona_el_permiso_de_escenas() -> None:
    """Regresión: sin pasar el parámetro nuevo (default `False`), el comportamiento es
    BIT A BIT el de antes de este cambio."""
    captura = CapturaLlamarModelo(
        json.dumps({"publicable": True, "problemas": [], "texto": _ESCENA})
    )

    await auditar_hechos(_ESCENA, _FUENTE_BANCO, captura)

    assert _MARCA_MODO not in captura.usuario
    assert "escena" not in captura.sistema.lower()


async def test_con_el_modo_activo_el_prompt_autoriza_la_escena() -> None:
    captura = CapturaLlamarModelo(
        json.dumps({"publicable": True, "problemas": [], "texto": _ESCENA})
    )

    await auditar_hechos(_ESCENA, _FUENTE_BANCO, captura, escenas_ilustrativas_autorizadas=True)

    assert _MARCA_MODO in captura.usuario
    assert "escenas ilustrativas" in captura.sistema.lower()


async def test_con_el_modo_activo_la_linea_prohibida_sigue_explicita_en_el_prompt() -> None:
    """La mitad que no se puede aflojar: aunque el modo esté activo, el prompt que recibe el
    modelo sigue nombrando, sin ambigüedad, cada cosa de la lista roja del encargo."""
    captura = CapturaLlamarModelo(
        json.dumps({"publicable": True, "problemas": [], "texto": _ESCENA})
    )

    await auditar_hechos(_ESCENA, _FUENTE_BANCO, captura, escenas_ilustrativas_autorizadas=True)

    prompt_completo = f"{captura.sistema}\n{captura.usuario}".lower()
    for prohibido in (
        "cifra",
        "cita",
        "persona",
        "institución real",
        "noticia",
        "banco",
        "regulada",
        "garantiza",
    ):
        assert prohibido in prompt_completo, f"falta la prohibición de {prohibido!r} en el prompt"


async def test_un_texto_con_una_sola_marca_de_hecho_duro_no_entra_al_modo() -> None:
    """LA SEGUNDA CONDICIÓN, que es la que hace verificable a la primera. El perfil autoriza,
    pero el texto trae una cifra: no se entra al modo, el permiso ni se menciona en el prompt
    y el auditor es exactamente el estricto de siempre."""
    captura = CapturaLlamarModelo(
        json.dumps({"publicable": True, "problemas": [], "texto": "x" * 300})
    )

    await auditar_hechos(
        _INVENCION_CON_CIFRA, _FUENTE_BANCO, captura, escenas_ilustrativas_autorizadas=True
    )

    assert _MARCA_MODO not in captura.usuario
    assert "escenas ilustrativas" not in captura.sistema.lower()


async def test_el_modo_no_se_activa_sin_ninguna_fuente_ni_banco() -> None:
    """Sin fuentes ni banco, `auditar_hechos` entra en su modo SIN FUENTES (más estricto
    todavía, elimina marcas y cifras sin excepción) y la autorización de escenas no aplica
    ahí -- fuera del defecto real medido, donde las dos corridas SIEMPRE tenían banco."""
    captura = CapturaLlamarModelo(json.dumps({"publicable": True, "texto": "..."}))

    await auditar_hechos("cualquier texto", None, captura, escenas_ilustrativas_autorizadas=True)

    assert _MARCA_MODO not in captura.usuario


# ---------------------------------------------------------------------------
# 3. Las dos mitades pedidas por el encargo, a nivel mecánico: la escena PASA cuando el
#    auditor (ya instruido, sección 2) la aprueba, y una invención prohibida SIGUE siendo
#    rechazada aunque el modo esté activo.
# ---------------------------------------------------------------------------


async def test_la_escena_ilustrativa_pasa_con_el_modo_activo() -> None:
    captura = CapturaLlamarModelo(
        json.dumps({"publicable": True, "problemas": [], "texto": _ESCENA})
    )

    texto_final, problemas = await auditar_hechos(
        _ESCENA, _FUENTE_BANCO, captura, escenas_ilustrativas_autorizadas=True
    )

    assert texto_final == _ESCENA
    assert problemas == []


async def test_una_invencion_prohibida_sigue_rechazada_aunque_el_modo_este_activo() -> None:
    """El modo no desarma el veto: si el auditor detecta una cifra inventada (o, por el mismo
    mecanismo, una cita textual, una persona real nombrada o una noticia falsa), sigue
    pudiendo vetar el texto entero."""
    captura = CapturaLlamarModelo(
        json.dumps(
            {
                "publicable": False,
                "problemas": ["Cifra inventada: '68%' no aparece en ninguna fuente."],
            }
        )
    )

    texto_final, problemas = await auditar_hechos(
        _INVENCION_CON_CIFRA,
        _FUENTE_BANCO,
        captura,
        escenas_ilustrativas_autorizadas=True,
    )

    assert texto_final == ""
    assert problemas == ["Cifra inventada: '68%' no aparece en ninguna fuente."]


async def test_el_veto_sigue_intacto_incluso_sobre_una_escena_pura() -> None:
    """Lo único que el modo le quita al modelo es la tijera, nunca el derecho a decir que no:
    si el auditor veta una escena que pasó el gate determinista (p. ej. porque afirma una
    función de producto que las fuentes no describen), el veto manda."""
    captura = CapturaLlamarModelo(
        json.dumps({"publicable": False, "problemas": ["Ese producto no existe en las fuentes."]})
    )

    texto_final, problemas = await auditar_hechos(
        _ESCENA, _FUENTE_BANCO, captura, escenas_ilustrativas_autorizadas=True
    )

    assert texto_final == ""
    assert problemas == ["Ese producto no existe en las fuentes."]


async def test_la_amputacion_del_auditor_se_ignora_sobre_una_escena_pura() -> None:
    """EL DEFECTO QUE DE VERDAD ROMPÍA EL MOTOR, medido contra el modelo real: el auditor
    "aprobaba" devolviendo una paráfrasis con el 24% del texto, y el anti-muñón de
    `redaccion` tiraba el intento igual. Sobre una escena que el gate determinista ya declaró
    incapaz de contener una invención, no hay nada que reescribir: se publica el original."""
    captura = CapturaLlamarModelo(
        json.dumps(
            {
                "publicable": True,
                "problemas": ["cita", "desenlace"],
                "texto": "Un bodeguero puede empezar a fiar.",
            }
        )
    )

    texto_final, problemas = await auditar_hechos(
        _ESCENA, _FUENTE_BANCO, captura, escenas_ilustrativas_autorizadas=True
    )

    assert texto_final == _ESCENA
    assert problemas == []


async def test_fuera_del_modo_la_reescritura_del_auditor_se_respeta_igual_que_siempre() -> None:
    """Regresión simétrica de la anterior: sin el modo, el auditor sigue mandando sobre el
    texto -- su reescritura es la que se devuelve, exactamente como antes de este cambio."""
    corregido = "Lo que las fuentes sí sostienen, reescrito por el auditor. " * 4
    captura = CapturaLlamarModelo(
        json.dumps({"publicable": True, "problemas": ["una deducción"], "texto": corregido})
    )

    texto_final, problemas = await auditar_hechos(_ESCENA, _FUENTE_BANCO, captura)

    assert texto_final == corregido.strip()
    assert problemas == ["una deducción"]


# ---------------------------------------------------------------------------
# 4. Cableado real: `redaccion.CrearPostLinkedInTool` solo activa el modo cuando el perfil de
#    ESE destino lo declaró -- nunca por defecto, nunca por otro tenant.
# ---------------------------------------------------------------------------


def _respuestas_perfil(perfil: dict[str, Any]) -> list[list[dict[str, Any]]]:
    """Mismo orden que `test_crear_post_linkedin._respuestas`: con `destino` puesto no se
    consulta la lista de destinos, así que solo hace falta el perfil y la rotación."""
    return [
        [_fila(perfil)],  # perfil del destino
        [],  # get_agenda_state
        [],  # save_agenda_state
        [],  # get_agenda_state dentro de empaquetar_borrador_social
        [],  # save_agenda_state final
    ]


async def test_crear_post_linkedin_activa_el_permiso_si_el_perfil_del_destino_lo_declaro(
    make_ctx, make_session, monkeypatch
):
    async def _sin_noticias(http, consulta, **kwargs):  # noqa: ANN001
        return []

    monkeypatch.setattr(redaccion, "titulares_frescos", _sin_noticias)
    llm = FakeLLM([_borrador(_ESCENA), _aprobado(), _auditado(_ESCENA)])
    perfil = {
        "voice": "Directo",
        "context_bank": _BANCO,
        "fact_check_mode": "escenas_ilustrativas",
    }
    ctx = make_ctx(session=make_session(_respuestas_perfil(perfil)))
    ctx.llm = llm

    result = await CrearPostLinkedInTool(uploader=UniqueUploader()).run(
        ctx, {"tema": "el fiao en la bodega", "destino": "organization", "con_imagen": False}
    )

    assert result.data["copy"] == _ESCENA
    # El auditor es la tercera llamada (escritor -> editor jefe -> auditor).
    assert _MARCA_MODO in llm.prompt(2)


async def test_crear_post_linkedin_no_activa_el_permiso_si_el_perfil_no_lo_declaro(
    make_ctx, make_session, monkeypatch
):
    """Mismo escenario exacto que el test anterior, sin `fact_check_mode`: el comportamiento
    tiene que ser el de siempre, sin el permiso nuevo en el prompt del auditor."""

    async def _sin_noticias(http, consulta, **kwargs):  # noqa: ANN001
        return []

    monkeypatch.setattr(redaccion, "titulares_frescos", _sin_noticias)
    llm = FakeLLM([_borrador(_ESCENA), _aprobado(), _auditado(_ESCENA)])
    perfil = {"voice": "Directo", "context_bank": _BANCO}
    ctx = make_ctx(session=make_session(_respuestas_perfil(perfil)))
    ctx.llm = llm

    result = await CrearPostLinkedInTool(uploader=UniqueUploader()).run(
        ctx, {"tema": "el fiao en la bodega", "destino": "organization", "con_imagen": False}
    )

    assert result.data["copy"] == _ESCENA
    assert _MARCA_MODO not in llm.prompt(2)


# ---------------------------------------------------------------------------
# 5. Mismo cableado, para `social.CrearContenidoSocialTool` (el texto que el usuario YA
#    dictó, no el que escribe el motor) -- el auditor de hechos también corre ahí.
# ---------------------------------------------------------------------------


async def test_crear_contenido_social_activa_el_permiso_si_el_perfil_lo_declaro(
    make_ctx, make_session
):
    llm = FakeLLM(
        [
            _aprobado("Tiene un ángulo concreto."),
            _auditado(_ESCENA),
        ]
    )
    perfil = {"voice": "Directo", "fact_check_mode": "escenas_ilustrativas"}
    ctx = make_ctx(session=make_session([[_fila(perfil)]]))
    ctx.llm = llm

    result = await CrearContenidoSocialTool(uploader=UniqueUploader()).run(
        ctx,
        {
            "plataforma": "linkedin",
            "tema": "El cuaderno del bodeguero",
            "texto": _ESCENA,
            "destino": "organization",
            "con_imagen": False,
            "fuentes": [{"title": "Nota sobre crédito informal", "url": "https://example.com/n"}],
        },
    )

    assert result.data["copy"] == _ESCENA
    assert _MARCA_MODO in llm.prompt(1)


async def test_crear_contenido_social_no_activa_el_permiso_sin_declararlo(make_ctx, make_session):
    llm = FakeLLM(
        [
            _aprobado("Tiene un ángulo concreto."),
            _auditado(_ESCENA),
        ]
    )
    perfil = {"voice": "Directo"}
    ctx = make_ctx(session=make_session([[_fila(perfil)]]))
    ctx.llm = llm

    result = await CrearContenidoSocialTool(uploader=UniqueUploader()).run(
        ctx,
        {
            "plataforma": "linkedin",
            "tema": "El cuaderno del bodeguero",
            "texto": _ESCENA,
            "destino": "organization",
            "con_imagen": False,
            "fuentes": [{"title": "Nota sobre crédito informal", "url": "https://example.com/n"}],
        },
    )

    assert result.data["copy"] == _ESCENA
    assert _MARCA_MODO not in llm.prompt(1)


# ---------------------------------------------------------------------------
# 6. El lazo de vuelta al escritor: cuando un borrador pierde el camino limpio por traer un
#    dato duro que no hacía falta, el reintento tiene que enterarse de CUÁL.
# ---------------------------------------------------------------------------


def test_la_pista_nombra_la_marca_concreta_que_costo_el_camino_limpio() -> None:
    pistas = redaccion._pista_escena_ilustrativa(
        True,
        "En Venezuela miles de personas cobran por Pago Móvil y nadie lleva la cuenta.",
        [{"title": "Banco", "url": "", "snippet": _BANCO}],
    )

    assert len(pistas) == 1
    assert "miles" in pistas[0]


def test_no_hay_pista_para_un_destino_que_no_declaro_el_modo() -> None:
    assert redaccion._pista_escena_ilustrativa(False, "En Venezuela miles de personas.", []) == []


def test_no_hay_pista_cuando_el_borrador_ya_era_una_escena_pura() -> None:
    """Si el texto no traía ni una marca, el auditor falló por otra cosa: mandarle al escritor
    una lista vacía de datos duros sería ruido que empeora el reintento."""
    assert (
        redaccion._pista_escena_ilustrativa(
            True, _ESCENA, [{"title": "Banco", "url": "", "snippet": _BANCO}]
        )
        == []
    )
