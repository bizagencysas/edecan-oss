"""Postura editorial (`editorial_stance`): el interruptor por-tenant que hace que el editor
jefe RECHACE el borrador neutral y exija un `angulo` que tome lado.

Espejo estructural de `fact_check_mode`/`escenas_ilustrativas` (ver `test_escenas_ilustrativas.py`
y el docstring de `social.perfil_autoriza_stance_polemica`): una señal DELIBERADAMENTE
explícita y verificable, nunca una adivinanza sobre `purpose`/`notes` en prosa libre. Se
activa si y solo si `editorial_stance` trae EXACTAMENTE `"polemica"`.

La diferencia clave con `escenas_ilustrativas`: ese afloja el AUDITOR de hechos (PASO 2);
este afloja al EDITOR JEFE (PASO 1) en sentido opuesto -- no lo afloja, lo ENDURECE: le
permite marcar `publicable=false` cuando el borrador es una reseña neutral sin juicio. El
auditor de hechos sigue intacto y veta cualquier invención: la polémica va anclada en la
fuente, nunca inventada.
"""

from __future__ import annotations

import json

from edecan_creative.auditoria import pulir_borrador
from edecan_creative.social import perfil_autoriza_stance_polemica

_TEXTO_NEUTRO = (
    "La Universidad Nacional de Asunción y Personal convocan a expertos en tecnología, "
    "academia y educación para un espacio de networking sobre inteligencia artificial y "
    "generación de talento. El evento es el martes a las 18:00 en el Aula Magna, con "
    "modalidad presencial y virtual. Habrá tres paneles sobre tecnologías disruptivas."
)

_TEXTO_OK = (
    "Casi el 40% del crédito argentino ya pasa por una fintech, y los bancos tradicionales "
    "todavía discuten si eso es una amenaza o una oportunidad. No es ninguna de las dos: es "
    "una sentencia. El que no decida si presta como los nuevos o se retira, pierde el turno."
)


# ---------------------------------------------------------------------------
# 1. `perfil_autoriza_stance_polemica`: la señal es un valor CANÓNICO exacto.
# ---------------------------------------------------------------------------


def test_perfil_vacio_no_autoriza() -> None:
    assert perfil_autoriza_stance_polemica({}) is False


def test_perfil_con_el_campo_vacio_no_autoriza() -> None:
    assert perfil_autoriza_stance_polemica({"editorial_stance": ""}) is False


def test_perfil_con_otro_valor_no_autoriza() -> None:
    assert perfil_autoriza_stance_polemica({"editorial_stance": "neutral"}) is False


def test_una_frase_que_contiene_el_valor_no_cuenta_como_activacion() -> None:
    """Si esto activara el modo, cualquier perfil que mencionara la palabra en prosa libre
    lo encendería por accidente -- el hueco que este campo deliberadamente no repite."""
    perfil = {"editorial_stance": "sí, quiero postura polemica para esta cuenta"}
    assert perfil_autoriza_stance_polemica(perfil) is False


def test_el_purpose_en_prosa_libre_nunca_activa_el_modo_por_si_solo() -> None:
    """El caso real del encargo: el perfil pide polémica en `purpose`, en prosa, y eso
    NUNCA alcanza para activar el gate -- hace falta el campo dedicado."""
    perfil = {"purpose": "CAUSAR POLÉMICA: tomar una posición que divida la sala."}
    assert perfil_autoriza_stance_polemica(perfil) is False


def test_el_valor_exacto_si_autoriza() -> None:
    assert perfil_autoriza_stance_polemica({"editorial_stance": "polemica"}) is True


def test_es_insensible_a_mayusculas_y_espacios_de_sobra() -> None:
    assert perfil_autoriza_stance_polemica({"editorial_stance": "  Polemica  "}) is True


# ---------------------------------------------------------------------------
# 2. `pulir_borrador`: qué cambia en el prompt, y qué NO cambia por defecto.
# ---------------------------------------------------------------------------


class _Captura:
    def __init__(self, respuesta: str) -> None:
        self.usuario = ""
        self.sistema = ""
        self._respuesta = respuesta

    async def __call__(self, mensajes: list[dict[str, str]]) -> str:  # noqa: D401
        self.sistema = mensajes[0]["content"]
        self.usuario = mensajes[1]["content"]
        return self._respuesta


_MARCA_POSTURA = "POSTURA EDITORIAL DE ESTA CUENTA: POLÉMICA"


async def test_por_defecto_el_prompt_no_menciona_la_postura_polemica() -> None:
    """Regresión: sin pasar `postura_polemica` (default `False`), el comportamiento es
    BIT A BIT el de antes de este cambio -- el editor jefe no recibe instrucción de
    postura."""
    captura = _Captura(json.dumps({"publicable": True, "texto": _TEXTO_NEUTRO}))

    await pulir_borrador({"texto": _TEXTO_NEUTRO}, "", captura, permisivo=True)

    assert _MARCA_POSTURA not in captura.usuario


async def test_con_el_modo_activo_el_prompt_exige_tomar_lado() -> None:
    captura = _Captura(json.dumps({"publicable": True, "texto": _TEXTO_OK}))

    await pulir_borrador(
        {"texto": _TEXTO_OK}, "", captura, permisivo=True, postura_polemica=True
    )

    assert _MARCA_POSTURA in captura.usuario
    assert "TOMA UN LADO" in captura.usuario


async def test_con_el_modo_activo_el_prompt_prohibe_inventar_hechos() -> None:
    """La mitad que no se puede aflojar: aunque la postura sea polémica, el prompt sigue
    atando el juicio a la fuente. La polémica no es licencia para inventar -- el auditor
    de hechos del PASO 2 sigue vetando cualquier invención, y el editor jefe lo sabe."""
    captura = _Captura(json.dumps({"publicable": True, "texto": _TEXTO_OK}))

    await pulir_borrador(
        {"texto": _TEXTO_OK}, "", captura, permisivo=True, postura_polemica=True
    )

    assert "NUNCA inventa" in captura.usuario


async def test_el_modo_polemica_permite_rechazar_lo_neutral_fuera_de_permisivo() -> None:
    """El gate real: con postura polémica y sin permisivo, el editor jefe puede marcar
    `publicable=false` sobre un texto neutral, y `pulir_borrador` lo descarta (None).
    Sin el modo, ese mismo rechazo sería el editor juzgando 'poco interesante' -- un
    comportamiento que el permiso de escenas y el modo permisivo ya modelan, pero que
    acá es deliberado: la cuenta declaró que lo neutral no le sirve."""
    async def _editor_rechaza_neutro(mensajes):  # noqa: ANN001
        assert _MARCA_POSTURA in mensajes[1]["content"]
        return json.dumps({"publicable": False, "motivo": "neutral, no toma lado"})

    descartado = await pulir_borrador(
        {"texto": _TEXTO_NEUTRO}, "", _editor_rechaza_neutro, postura_polemica=True
    )
    assert descartado is None


async def test_en_permisivo_el_modo_polemica_no_descarta_aunque_el_editor_diga_false() -> None:
    """`permisivo` (el usuario pidió el tema) le gana al gate de postura en la decisión
    final de ENTREGAR: el usuario está esperando y revisa antes de publicar. Pero el modo
    igual empuja al editor a reescribir tomando lado -- el `publicable=false` sólo decide no
    entregar de golpe, no anula la instrucción de postura. Si el editor articuló la postura,
    su reescritura se entrega tal cual."""
    async def _editor(mensajes):  # noqa: ANN001
        assert _MARCA_POSTURA in mensajes[1]["content"]
        return json.dumps(
            {"publicable": False, "texto": _TEXTO_OK, "postura": "un juicio que toma lado"}
        )

    entregado = await pulir_borrador(
        {"texto": _TEXTO_NEUTRO}, "", _editor, permisivo=True, postura_polemica=True
    )
    assert entregado is not None
    assert entregado["texto"] == _TEXTO_OK
    assert entregado["postura"] == "un juicio que toma lado"


async def test_gate_sin_postura_descarta_en_estricto() -> None:
    """El gate determinista: en modo polemico, si el editor no articuló `postura`, el
    borrador es neutral y se descarta (estricto). No se fía de que el modelo se
    autorrechace con publicable=false -- el campo vacío ES la prueba del incumplimiento."""
    async def _editor_neutral(mensajes):  # noqa: ANN001
        return json.dumps({"publicable": True, "texto": _TEXTO_NEUTRO})  # sin postura

    descartado = await pulir_borrador(
        {"texto": _TEXTO_NEUTRO}, "", _editor_neutral, postura_polemica=True
    )
    assert descartado is None


async def test_gate_sin_postura_en_permisivo_caen_al_previo() -> None:
    """En permisivo el gate no descarta (el usuario espera algo), pero el borrador neutral
    del editor no es aceptable: se cae al `previo` (el candidato pre-edición) en vez de
    entregar lo que el editor reescribió sin postura."""
    async def _editor_neutral(mensajes):  # noqa: ANN001
        return json.dumps({"publicable": True, "texto": _TEXTO_NEUTRO})  # sin postura

    entregado = await pulir_borrador(
        {"texto": _TEXTO_OK}, "", _editor_neutral, permisivo=True, postura_polemica=True
    )
    assert entregado is not None
    assert entregado["texto"] == _TEXTO_OK  # el previo, no la reescritura neutral

