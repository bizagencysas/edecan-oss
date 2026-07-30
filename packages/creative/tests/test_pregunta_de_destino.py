"""La regla de CUÁNDO preguntar el destino — lo que separa un modal útil de uno decorativo.

Se pregunta solo cuando el usuario tiene de verdad más de una cuenta donde publicar. Con una
sola (o ninguna) la respuesta ya se conoce, y un modal con una única respuesta posible es
justo el ruido que hay que evitar. Las opciones salen de SUS destinos reales
(`social_editorial_profiles`), nunca de nombres inventados por el modelo.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from edecan_creative.social import CrearContenidoSocialTool


class _Uploader:
    """Cuando NO se pregunta, la tool sigue de largo y sube artefactos: sin este doble
    los tests intentarían hablar con S3 de verdad (regla dura del proyecto: offline)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, ctx, *, data: bytes, filename: str, mime: str):  # noqa: ANN001
        self.calls.append(mime)
        return uuid4(), filename


def _fila(platform: str, *, voice: str = "una voz configurada") -> dict[str, Any]:
    return {"platform": platform, "config": {"voice": voice}, "version": 1}


def _post(**extra: Any) -> dict[str, Any]:
    args = {
        "plataforma": "linkedin",
        "tema": "Un tema cualquiera",
        "texto": "Un texto que por sí solo pasa los gates editoriales de LinkedIn.",
        "con_imagen": False,
    }
    args.update(extra)
    return args


def _bloque_pregunta(result: Any) -> dict[str, Any] | None:
    for bloque in result.presentation or []:
        if bloque.get("type") == "question":
            return bloque
    return None


def _borrador(result: Any) -> dict[str, Any] | None:
    for bloque in result.presentation or []:
        if bloque.get("type") == "social_draft":
            return bloque
    return None


def _platforms_consultadas(session: Any) -> list[str]:
    return [params["platform"] for _sql, params in session.llamadas if "platform" in params]


async def test_con_dos_destinos_pregunta_antes_de_escribir(make_ctx, make_session):
    session = make_session()
    session.respuestas = [[_fila("linkedin_personal"), _fila("linkedin_acme")]]
    ctx = make_ctx(session=session)

    result = await CrearContenidoSocialTool(uploader=_Uploader()).run(ctx, _post())

    bloque = _bloque_pregunta(result)
    assert bloque is not None
    assert bloque["header"] == "Destino"
    assert {o["label"] for o in bloque["options"]} == {"personal", "acme"}
    # No se escribió nada: el borrador con la voz equivocada es trabajo tirado.
    assert "no escribí el post" in result.content.lower()


async def test_con_un_solo_destino_no_pregunta_y_escribe(make_ctx, make_session):
    # Preguntar algo que tiene una única respuesta posible es el modal decorativo.
    session = make_session()
    session.respuestas = [[_fila("linkedin_personal")]]
    ctx = make_ctx(session=session)

    result = await CrearContenidoSocialTool(uploader=_Uploader()).run(ctx, _post())

    assert _bloque_pregunta(result) is None


async def test_con_un_solo_destino_de_organizacion_lo_usa_en_vez_de_ignorarlo(
    make_ctx, make_session
):
    """No preguntar NO es lo mismo que no tener destino.

    Regresión de un bug real: con una sola cuenta y esa cuenta siendo una organización, el
    gate callaba (correcto) pero además perdía el destino, así que el post se escribía con el
    perfil de `linkedin_personal` -- vacío en este tenant -- y la card salía con `target: null`,
    que en iOS es un borrador sin botón de publicar.
    """
    session = make_session()
    session.respuestas = [
        [_fila("linkedin_acme", voice="Voz de Acme")],  # el único destino configurado
        [_fila("linkedin_acme", voice="Voz de Acme")],  # y su perfil editorial
    ]
    ctx = make_ctx(session=session)

    result = await CrearContenidoSocialTool(uploader=_Uploader()).run(ctx, _post())

    assert _bloque_pregunta(result) is None
    # La voz que se cargó es la de ESA cuenta, no la del perfil personal ni la del legacy.
    consultadas = _platforms_consultadas(session)
    assert "linkedin_acme" in consultadas
    assert "linkedin_personal" not in consultadas
    # Y la card viaja con su destino: sin él el cliente solo ofrece acciones de solo lectura.
    borrador = _borrador(result)
    assert borrador is not None
    assert borrador["target"] == "acme"


async def test_sin_destinos_configurados_no_pregunta_y_no_inventa_uno(make_ctx, make_session):
    # La otra mitad del contrato: sin ninguna cuenta configurada no hay destino que resolver,
    # y el default de `get_editorial_profile_for_destination` (personal) es el correcto.
    session = make_session()
    ctx = make_ctx(session=session)

    result = await CrearContenidoSocialTool(uploader=_Uploader()).run(ctx, _post())

    assert _bloque_pregunta(result) is None
    borrador = _borrador(result)
    assert borrador is not None
    assert borrador["target"] is None


async def test_si_el_usuario_ya_dijo_el_destino_no_se_lo_vuelve_a_preguntar(
    make_ctx, make_session
):
    session = make_session()
    session.respuestas = [[_fila("linkedin_personal"), _fila("linkedin_acme")]]
    ctx = make_ctx(session=session)

    result = await CrearContenidoSocialTool(uploader=_Uploader()).run(
        ctx, _post(destino="acme")
    )

    assert _bloque_pregunta(result) is None


async def test_los_perfiles_vacios_no_cuentan_como_destinos(make_ctx, make_session):
    # Una fila sin contenido no escribe en ninguna voz: ofrecerla sería ofrecer una opción
    # que no hace nada. Con un solo destino REAL, no hay nada que preguntar.
    session = make_session()
    session.respuestas = [
        [
            _fila("linkedin_personal"),
            {"platform": "linkedin_vacio", "config": {}, "version": 1},
        ]
    ]
    ctx = make_ctx(session=session)

    result = await CrearContenidoSocialTool(uploader=_Uploader()).run(ctx, _post())

    assert _bloque_pregunta(result) is None


async def test_pregunta_el_destino_sin_haber_escrito_el_texto(make_ctx, make_session):
    """El caso que el gate existe para cubrir: preguntar ANTES de gastar el turno escribiendo.

    Regresión de un bug real: el chequeo de tema+texto corría primero, así que el destino se
    preguntaba con el post ya escrito y la respuesta obligaba a reescribirlo entero en otra voz.
    """
    session = make_session()
    session.respuestas = [[_fila("linkedin_personal"), _fila("linkedin_acme")]]
    ctx = make_ctx(session=session)

    args = _post()
    del args["texto"]
    result = await CrearContenidoSocialTool(uploader=_Uploader()).run(ctx, args)

    bloque = _bloque_pregunta(result)
    assert bloque is not None
    assert {o["label"] for o in bloque["options"]} == {"personal", "acme"}


async def test_sin_texto_y_sin_ambiguedad_pide_el_texto_en_vez_de_colgarse(make_ctx, make_session):
    """Ningún camino puede dejar al modelo sin saber qué hacer: si no hay nada que preguntar,
    la respuesta tiene que ser "escribe el post", no un silencio que lo manda a preguntar otra
    vez la cuenta (el bucle que se reportó: repreguntar y quedarse girando)."""
    session = make_session()
    session.respuestas = [[_fila("linkedin_personal")]]
    ctx = make_ctx(session=session)

    args = _post()
    del args["texto"]
    result = await CrearContenidoSocialTool(uploader=_Uploader()).run(ctx, args)

    assert _bloque_pregunta(result) is None
    assert "texto" in result.content.lower()
    assert "no lo preguntes otra vez" in result.content.lower()


async def test_si_ya_habia_texto_le_avisa_que_no_lo_uso(make_ctx, make_session):
    """Con el texto ya escrito y el destino sin resolver, el borrador se descarta. Hay que
    decírselo: si no, reenvía el mismo texto con 'destino' puesto y publica en la voz que la
    pregunta acababa de descartar."""
    session = make_session()
    session.respuestas = [[_fila("linkedin_personal"), _fila("linkedin_acme")]]
    ctx = make_ctx(session=session)

    result = await CrearContenidoSocialTool(uploader=_Uploader()).run(ctx, _post())

    assert "no usé el texto que mandaste" in result.content.lower()


async def test_el_texto_no_es_obligatorio_en_el_schema(make_ctx):
    # Si 'texto' fuera requerido, el modelo tendría que escribir el post entero solo para
    # enterarse de que la cuenta todavía estaba por decidir: el gate nunca correría a tiempo.
    schema = CrearContenidoSocialTool().input_schema
    assert "texto" not in schema["required"]
    assert schema["required"] == ["plataforma", "tema"]


async def test_en_plataformas_sin_voz_editorial_nunca_pregunta(make_ctx, make_session):
    # X no tiene destinos de organización en esta instalación: preguntar sería inventar.
    session = make_session()
    session.respuestas = [[_fila("linkedin_personal"), _fila("linkedin_acme")]]
    ctx = make_ctx(session=session)

    result = await CrearContenidoSocialTool(uploader=_Uploader()).run(
        ctx, _post(plataforma="threads", texto="Corto y directo.")
    )

    assert _bloque_pregunta(result) is None
