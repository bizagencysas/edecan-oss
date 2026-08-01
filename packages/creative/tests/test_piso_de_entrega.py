"""El PISO DE ENTREGA del motor de posts, y por qué existe.

Estos tests fijan una sola promesa, la que faltaba el día del fallo:

    si `crear_post_linkedin` devuelve `data["copy"]`, ese copy es publicable
    (mide al menos `MIN_COPY_ENTREGABLE_CHARS`); si no, devuelve `data["fallo"]`
    con el motivo y NINGÚN borrador.

El fallo real que los originó: el chat contestó "me pongo a escribir tu post" y no llegó
nada nunca. El trabajo terminó en `done`, sin mensaje y sin tarjeta. Por dentro había dos
pisos distintos que no se hablaban -- el motor daba por bueno todo lo que pasara de 80
caracteres, y el job que lo consume exigía 150 por su cuenta --, así que un copy de 118
caracteres PASABA aquí (el motor hasta lo anotaba en su historial editorial como post
publicado) y MORÍA allá, sin que nadie le dijera nada a la persona. La franja 80-150 era un
agujero silencioso, y este archivo es el que ya no la deja existir.

Complementa a `test_crear_post_linkedin.py` (el ciclo completo, eslabón por eslabón) y a
`test_port_motor_linkedin.py` (las piezas portadas de Aria): aquí solo se mide el largo de
lo que sale y qué pasa cuando no alcanza.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import edecan_creative.redaccion as redaccion
import pytest
from edecan_creative.investigacion import Titular
from edecan_creative.redaccion import MIN_COPY_ENTREGABLE_CHARS, CrearPostLinkedInTool

# Un post de verdad: pasa `editorial.revisar` y pasa el piso con holgura.
_POST_COMPLETO = (
    "El ajuste de tasas encarece el crédito revolvente para quien ya arrastra saldo del mes "
    "anterior. La diferencia no se ve en la publicidad, se ve en el estado de cuenta, cuando "
    "el pago mínimo cubre menos capital que antes y el saldo empieza a crecer solo."
)

# EL MUÑÓN. 118 caracteres: el largo exacto del copy que el motor entregó como bueno el día
# del fallo. Cae justo en la franja que antes nadie vigilaba -- pasaba el piso viejo de 80 y
# no llegaba al que exige quien lo entrega.
_MUNON = (
    "El ajuste de tasas encarece el crédito revolvente y el pago mínimo deja de cubrir lo "
    "que cubría el mes pasado."
)

# Exactamente el piso, ni un carácter más. Fija el borde por el lado de abajo: un umbral que
# rechaza lo que cumple es un umbral roto, y el síntoma sería otra vez alguien esperando un
# post que estaba bien.
_JUSTO_EN_EL_PISO = (
    "El ajuste de tasas encarece el crédito revolvente para quien arrastra saldo del mes "
    "anterior, porque el pago mínimo cubre hoy menos capital que antes."
)


def test_el_munon_del_fallo_real_cae_en_la_franja_que_nadie_vigilaba() -> None:
    """Guarda de los propios fixtures: si esto deja de cumplirse, los tests de abajo miden
    otra cosa sin avisar. 80 era el piso viejo del motor; 150 es el que exige la entrega."""
    assert 80 <= len(_MUNON) < MIN_COPY_ENTREGABLE_CHARS
    assert len(_POST_COMPLETO) >= MIN_COPY_ENTREGABLE_CHARS


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


def _borrador(texto: str) -> str:
    return json.dumps(
        {
            "tema": "Tasas de tarjetas",
            "fuente_principal": "F1",
            "texto": texto,
            "titular_visual": "Tasas más caras para tarjetas",
            "alt_text": "Una tarjeta de crédito sobre un mostrador.",
        }
    )


def _pulido(texto: str) -> str:
    return json.dumps({"publicable": True, "motivo": "Ok, con ángulo concreto.", "texto": texto})


def _auditado(texto: str) -> str:
    return json.dumps({"publicable": True, "problemas": [], "texto": texto})


def _args() -> dict[str, Any]:
    return {"tema": "tasas de tarjetas en México", "destino": "personal", "con_imagen": False}


def _respuestas() -> list[list[dict[str, Any]]]:
    return [[], [], [], [], [], []]


@pytest.fixture(autouse=True)
def _una_noticia(monkeypatch):
    async def _fake(http, consulta, **kwargs):  # noqa: ANN001
        return [
            Titular(
                titulo="Un banco mexicano ajusta tasas",
                snippet="Reforma · hace 3 h",
                url="https://example.com/nota-1",
                fuente="Reforma",
                fuente_url="https://reforma.com",
                publicado_en="2026-07-28T00:00:00+00:00",
                antiguedad_horas=3.0,
            )
        ]

    monkeypatch.setattr(redaccion, "titulares_frescos", _fake)


# ---------------------------------------------------------------------------
# 1. La franja 80-150: lo que pasaba el motor y descartaba quien lo entrega.
# ---------------------------------------------------------------------------


async def test_un_munon_de_la_franja_no_sale_como_post(make_ctx, make_session):
    """LA REGRESIÓN. Un copy de 118 caracteres no es un post, y no se entrega como si lo
    fuera -- ni siquiera por el camino de rescate, que es donde más tienta aflojar el listón.

    Lo que se rompía antes: esto devolvía `data["copy"]` con el muñón adentro, el motor lo
    daba por publicado en su historial editorial, y el job que arma la tarjeta lo descartaba
    en silencio. La persona no recibía ni el post ni una explicación.
    """
    # Los tres intentos entregan el mismo muñón, ya pulido y auditado: nadie más va a
    # alargarlo, así que el turno tiene que terminar sin borrador y diciéndolo.
    llm = FakeLLM([_borrador(_MUNON), _pulido(_MUNON), _auditado(_MUNON)] * redaccion._MAX_INTENTOS)
    ctx = make_ctx(session=make_session(_respuestas()))
    ctx.llm = llm
    uploader = UniqueUploader()

    result = await CrearPostLinkedInTool(uploader=uploader).run(ctx, _args())

    assert result.data == {"fallo": redaccion.FALLO_NO_PUBLICABLE}
    # Y no se subió nada: sin post no hay manifiesto, ni imagen, ni artefactos sueltos que
    # después le aparezcan a alguien como si hubiera trabajo hecho.
    assert uploader.calls == []


async def test_al_escritor_se_le_dice_que_el_texto_quedo_corto(make_ctx, make_session):
    """El reintento no repite el mismo pedido tal cual: lleva el largo que falta.

    Es la diferencia entre un motor que insiste y uno que corrige. Sin esta corrección
    explícita, los tres intentos producen el mismo muñón y el turno se pierde igual.
    """
    llm = FakeLLM(
        [
            _borrador(_MUNON),
            _pulido(_MUNON),
            _auditado(_MUNON),
            _borrador(_POST_COMPLETO),
            _pulido(_POST_COMPLETO),
            _auditado(_POST_COMPLETO),
        ]
    )
    ctx = make_ctx(session=make_session(_respuestas()))
    ctx.llm = llm

    result = await CrearPostLinkedInTool(uploader=UniqueUploader()).run(ctx, _args())

    assert result.data["copy"] == _POST_COMPLETO
    # El segundo pase del escritor es la llamada 3 (escritor, editor, auditor, escritor).
    reintento = llm.prompt(3)
    assert "CORRIGE" in reintento
    assert str(MIN_COPY_ENTREGABLE_CHARS) in reintento
    # Y SIN acusar al auditor, que acá no tocó nada: el escritor entregó 118 caracteres y el
    # auditor devolvió los mismos 118. La corrección que había decía "el auditor recortó el
    # post de 118 a 118 caracteres" -- dos mentiras en una frase -- y mandaba al escritor a
    # anclar afirmaciones en la fuente cuando lo único que hacía falta era que escribiera más.
    # Una corrección equivocada no es un detalle de redacción: es un intento quemado.
    assert "El auditor recortó" not in reintento
    assert "demasiado corto" in reintento


async def test_un_post_justo_en_el_piso_si_se_entrega(make_ctx, make_session):
    """El piso es un mínimo, no un gusto: lo que lo alcanza pasa.

    Fija el borde por el otro lado. Un umbral que además rechaza lo que cumple es un umbral
    roto, y el síntoma sería el mismo de siempre: la persona esperando un post que sí estaba
    bien.
    """
    justo = _JUSTO_EN_EL_PISO
    assert len(justo) == MIN_COPY_ENTREGABLE_CHARS, "el fixture dejó de medir el borde exacto"
    llm = FakeLLM([_borrador(justo), _pulido(justo), _auditado(justo)])
    ctx = make_ctx(session=make_session(_respuestas()))
    ctx.llm = llm

    result = await CrearPostLinkedInTool(uploader=UniqueUploader()).run(ctx, _args())

    assert result.data["copy"] == justo
    assert len(result.data["copy"]) == MIN_COPY_ENTREGABLE_CHARS


# ---------------------------------------------------------------------------
# 2. El auditor amputa: se reintenta, no se entrega el recorte.
# ---------------------------------------------------------------------------


async def test_si_el_auditor_deja_un_munon_se_reescribe_en_vez_de_entregarlo(
    make_ctx, make_session
):
    """La segunda causa del fallo, y la más difícil de ver.

    El auditor de hechos no solo veta: REESCRIBE, y con un perfil editorial exigente ("pide
    fuente antes de afirmar") tumba casi todas las afirmaciones de un tema sin noticia
    fresca. Lo que devuelve entonces es un gancho pelado.

    La protección que había era RELATIVA (¿quedó menos del 45% de lo que entró?) y solo se
    medía si el texto previo pasaba de 400 caracteres, así que con un borrador mediano no
    protegía nada: exactamente el caso de este test. Ahora el piso absoluto la acompaña, y la
    consecuencia es reintentar con el motivo en el prompt -- nunca devolver el recorte.
    """
    llm = FakeLLM(
        [
            _borrador(_POST_COMPLETO),
            _pulido(_POST_COMPLETO),
            _auditado(_MUNON),  # el auditor amputa el cuerpo entero
            _borrador(_POST_COMPLETO),
            _pulido(_POST_COMPLETO),
            _auditado(_POST_COMPLETO),  # el segundo intento sí se sostiene
        ]
    )
    ctx = make_ctx(session=make_session(_respuestas()))
    ctx.llm = llm

    result = await CrearPostLinkedInTool(uploader=UniqueUploader()).run(ctx, _args())

    assert result.data["copy"] == _POST_COMPLETO
    reintento = llm.prompt(3)
    assert "CORRIGE" in reintento
    assert "El auditor recortó el post" in reintento


async def test_si_el_auditor_amputa_siempre_se_rescata_el_cuerpo_no_el_recorte(
    make_ctx, make_session
):
    """Y si amputa los tres intentos, lo que se rescata es el CUERPO, nunca el recorte.

    Éste es el reparto correcto de las dos malas noticias posibles. Cuando el auditor deja un
    muñón, el texto bueno sigue existiendo: es el que aprobó el editor jefe, y ése es el que
    se guarda como candidato. Lo que se entrega entonces es un post completo con el aviso de
    que no se pudo verificar del todo -- información suficiente para que la persona decida --,
    y jamás las dos líneas que quedaron después del recorte.
    """
    llm = FakeLLM(
        [_borrador(_POST_COMPLETO), _pulido(_POST_COMPLETO), _auditado(_MUNON)]
        * redaccion._MAX_INTENTOS
    )
    ctx = make_ctx(session=make_session(_respuestas()))
    ctx.llm = llm

    result = await CrearPostLinkedInTool(uploader=UniqueUploader()).run(ctx, _args())

    assert result.data["copy"] == _POST_COMPLETO
    assert result.data["copy"] != _MUNON
    # Y se le dice a la persona que ese borrador no pasó limpio, en vez de venderlo como listo.
    assert "no pasó el control editorial automático" in result.content


async def test_la_reauditoria_tras_reparar_la_primera_persona_tampoco_puede_amputar(
    make_ctx, make_session
):
    """El mismo anti-muñón, un paso más adelante en el pipeline.

    `auditar_hechos` corre DOS veces cuando el texto vuelve con primera persona: una sobre el
    borrador y otra sobre el texto reparado. Sin el piso también en la segunda, la reparación
    de primera persona era una puerta trasera -- el texto entraba completo y salía en dos
    líneas, ya sin ningún control detrás que pudiera rescatar el cuerpo bueno.
    """
    con_primera_persona = (
        "Vimos que el ajuste de tasas encarece el crédito para muchos clientes. En la práctica "
        "el pago mínimo cubre menos capital, y el saldo que parecía estable empieza a crecer "
        "solo."
    )
    llm = FakeLLM(
        [
            _borrador(_POST_COMPLETO),
            _pulido(_POST_COMPLETO),
            _auditado(con_primera_persona),  # el auditor reintroduce "Vimos"
            _MUNON,  # el reparador de primera persona devuelve un muñón
            _auditado(_MUNON),  # y la RE-auditoría lo confirma como muñón
            _borrador(_POST_COMPLETO),
            _pulido(_POST_COMPLETO),
            _auditado(_POST_COMPLETO),
        ]
    )
    ctx = make_ctx(session=make_session(_respuestas()))
    ctx.llm = llm

    result = await CrearPostLinkedInTool(uploader=UniqueUploader()).run(ctx, _args())

    assert result.data["copy"] == _POST_COMPLETO


# ---------------------------------------------------------------------------
# 3. El motivo viaja en código, no en prosa.
# ---------------------------------------------------------------------------


async def test_sin_fuente_devuelve_el_codigo_de_fallo(make_ctx, make_session, monkeypatch):
    """Quien entrega el trabajo en segundo plano tiene que poder explicarle a la PERSONA qué
    pasó, y el `content` de estos caminos está escrito para el modelo del chat (nombra
    herramientas y parámetros). El código estable es lo que permite traducirlo sin parsear
    prosa que escribió un modelo -- que es como se construye el próximo silencio.
    """

    async def _sin_noticias(http, consulta, **kwargs):  # noqa: ANN001
        return []

    monkeypatch.setattr(redaccion, "titulares_frescos", _sin_noticias)
    ctx = make_ctx(session=make_session(_respuestas()))
    ctx.llm = FakeLLM([])

    result = await CrearPostLinkedInTool(uploader=UniqueUploader()).run(
        ctx, {"destino": "personal", "con_imagen": False}
    )

    assert result.data == {"fallo": redaccion.FALLO_SIN_FUENTE}


async def test_sin_modelo_devuelve_el_codigo_de_fallo(make_ctx, make_session):
    ctx = make_ctx(session=make_session(_respuestas()))
    ctx.llm = None

    result = await CrearPostLinkedInTool(uploader=UniqueUploader()).run(ctx, _args())

    assert result.data == {"fallo": redaccion.FALLO_SIN_MODELO}


async def test_un_post_que_pasa_el_limite_de_la_plataforma_trae_su_propio_codigo(
    make_ctx, make_session
):
    """"Te quedó largo" es el motivo MÁS accionable que puede dar este motor, y era el único
    que salía sin código.

    El empaquetado devuelve ese error duro sin `data`, así que quien entrega el trabajo en
    segundo plano no tenía forma de distinguirlo y le decía a la persona "no me salió nada que
    valiera la pena entregarte" -- falso: salió entero, sólo hay que cortarlo. Un código de
    fallo convierte esa mentira en una orden de una línea.
    """
    from edecan_creative.social import PLATFORMS

    largo = (_POST_COMPLETO + "\n\n") * 15
    assert len(largo) > PLATFORMS["linkedin"].max_chars
    llm = FakeLLM([_borrador(largo), _pulido(largo), _auditado(largo)])
    ctx = make_ctx(session=make_session(_respuestas()))
    ctx.llm = llm

    result = await CrearPostLinkedInTool(uploader=UniqueUploader()).run(ctx, _args())

    assert result.data == {"fallo": redaccion.FALLO_COPY_LARGO}


# ---------------------------------------------------------------------------
# 4. Lo que el motor SABE tiene que viajar en `data`, no sólo en `content`.
# ---------------------------------------------------------------------------
#
# `content` está escrito para el modelo del chat: nombra herramientas y parámetros. Quien
# entrega el post en segundo plano lo ignora a propósito (entregarlo como si fuera el post es
# un bug que ya se vio). Así que todo aviso que viva sólo ahí, por ese camino, NO EXISTE.


async def test_el_aviso_de_rescate_viaja_en_data_y_dice_si_el_auditor_no_valido(
    make_ctx, make_session
):
    """El agujero más serio que dejó el arreglo anterior del silencio.

    El camino de rescate entrega el cuerpo PREVIO a la auditoría con un aviso pegado. Ese
    aviso vivía únicamente en `content`, así que por el camino del trabajo en segundo plano se
    perdía entero: a la página de una empresa le llegaba un borrador que el auditor de hechos
    había vetado, con botón de publicar y sin una palabra de advertencia. Cambiar un fallo
    silencioso por un riesgo editorial silencioso no es arreglarlo.
    """
    llm = FakeLLM(
        [_borrador(_POST_COMPLETO), _pulido(_POST_COMPLETO), _auditado(_MUNON)]
        * redaccion._MAX_INTENTOS
    )
    ctx = make_ctx(session=make_session(_respuestas()))
    ctx.llm = llm

    result = await CrearPostLinkedInTool(uploader=UniqueUploader()).run(ctx, _args())

    assert result.data["copy"] == _POST_COMPLETO
    assert "no pasó el control editorial automático" in result.data["aviso"]
    # Y la bandera que de verdad decide qué botones puede llevar la tarjeta: acá el auditor
    # amputó los tres intentos, así que lo que se rescató nunca pasó por su juicio.
    assert result.data["sin_auditar"] is True


async def test_un_post_que_paso_limpio_no_lleva_aviso_ni_bandera(make_ctx, make_session):
    """El contrapeso: sin aviso no hay claves de aviso.

    Una bandera que aparece siempre no informa nada, y en este caso además le quitaría al
    dueño el botón de publicar en todos los posts buenos.
    """
    llm = FakeLLM([_borrador(_POST_COMPLETO), _pulido(_POST_COMPLETO), _auditado(_POST_COMPLETO)])
    ctx = make_ctx(session=make_session(_respuestas()))
    ctx.llm = llm

    result = await CrearPostLinkedInTool(uploader=UniqueUploader()).run(ctx, _args())

    assert result.data["copy"] == _POST_COMPLETO
    assert "aviso" not in result.data
    assert "sin_auditar" not in result.data


async def test_una_respuesta_en_prosa_corta_no_se_diagnostica_como_JSON_roto(
    make_ctx, make_session
):
    """El escritor mandó prosa perfecta, sólo que corta. Decirle "no devolviste el JSON" gasta
    un intento arreglando un formato que no estaba roto, y no le pide nunca lo único que hacía
    falta: que escribiera más largo.

    Con el piso subido a 150, esta franja se dispara más seguido que antes, así que el
    diagnóstico equivocado sale más caro.
    """
    corta = "El ajuste de tasas encarece el crédito revolvente para quien arrastra saldo."
    assert len(corta) < MIN_COPY_ENTREGABLE_CHARS
    llm = FakeLLM(
        [corta, _borrador(_POST_COMPLETO), _pulido(_POST_COMPLETO), _auditado(_POST_COMPLETO)]
    )
    ctx = make_ctx(session=make_session(_respuestas()))
    ctx.llm = llm

    result = await CrearPostLinkedInTool(uploader=UniqueUploader()).run(ctx, _args())

    assert result.data["copy"] == _POST_COMPLETO
    reintento = llm.prompt(1)  # el segundo pase del escritor
    assert "demasiado corto" in reintento
    assert "No devolviste el JSON" not in reintento
