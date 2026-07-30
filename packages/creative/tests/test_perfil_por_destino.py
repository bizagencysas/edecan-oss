"""`get_editorial_profile_for_destination`: el DESTINO decide qué perfil editorial se carga.

Regresión de un bug real: `crear_contenido_social` cargaba siempre la clave legacy
(`"linkedin"`) aunque el tenant ya tuviera `"linkedin_personal"` y
`"linkedin_<organizacion>"` guardados aparte. Como la clave legacy es la que quedó de antes
de que existieran los destinos, traía la voz de la organización mezclada -- así que pedir "un
post de LinkedIn" sin marca devolvía un post escrito con la voz de la marca.
"""

from __future__ import annotations

from typing import Any

from edecan_creative.social import get_editorial_profile_for_destination


def _platforms_consultadas(session: Any) -> list[str]:
    return [params.get("platform") for _sql, params in session.llamadas]


async def test_sin_destino_carga_el_perfil_personal_y_no_el_legacy(make_ctx, make_session):
    session = make_session()
    # Primera consulta (`linkedin_personal`): configurada -> no debe mirar el legacy.
    session.respuestas = [[{"config": {"voice": "voz personal"}, "version": 3}]]
    ctx = make_ctx(session=session)

    profile = await get_editorial_profile_for_destination(ctx, "linkedin", None)

    assert _platforms_consultadas(session) == ["linkedin_personal"]
    assert profile["voice"] == "voz personal"


async def test_destino_de_organizacion_carga_su_propio_perfil(make_ctx, make_session):
    session = make_session()
    session.respuestas = [[{"config": {"voice": "voz de la marca"}, "version": 1}]]
    ctx = make_ctx(session=session)

    profile = await get_editorial_profile_for_destination(ctx, "linkedin", "acme")

    assert _platforms_consultadas(session) == ["linkedin_acme"]
    assert profile["voice"] == "voz de la marca"


async def test_cae_al_legacy_solo_si_el_perfil_del_destino_no_esta_configurado(
    make_ctx, make_session
):
    session = make_session()
    # `linkedin_personal` vacío -> recién ahí se consulta la clave legacy `linkedin`.
    session.respuestas = [[], [{"config": {"voice": "voz vieja"}, "version": 9}]]
    ctx = make_ctx(session=session)

    profile = await get_editorial_profile_for_destination(ctx, "linkedin", None)

    assert _platforms_consultadas(session) == ["linkedin_personal", "linkedin"]
    assert profile["voice"] == "voz vieja"


async def test_sin_perfil_en_ningun_lado_devuelve_el_del_destino_vacio(make_ctx, make_session):
    session = make_session()
    ctx = make_ctx(session=session)

    profile = await get_editorial_profile_for_destination(ctx, "linkedin", None)

    assert profile["configured"] is False
    assert profile["platform"] == "linkedin_personal"
