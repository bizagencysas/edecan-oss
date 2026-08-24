"""Reglas que un tenant declara en SU PROPIO perfil (texto libre, no código) y que antes no se
hacían cumplir: la queja real fue el post de Cashea de Acme (Alex, 31-jul-2026),
"no tiene sentido... Cashea levantó $100MM y Boost convierte los pagos en historial no tiene
coherencia en lo absoluto". Investigado el motor completo, salieron TRES violaciones
concretas del propio perfil de esa cuenta, ninguna de las cuales tenía un gate determinista
detrás -- sólo la instrucción en el prompt, que un modelo apurado no respetó:

1. "$100MM" viola "cero números de dos o más dígitos" (perfil, campo `avoid`/`voice`).
   `editorial.tiene_numero_extenso` ya existía para esto -- lo dice su propio docstring,
   "un tenant que necesite prohibir cifras... puede llamar esta función" -- pero ningún
   llamador lo hacía. Ver `editorial.revisar(prohibir_numeros_extensos=...)`.
2. "Acme ofrece productos como Boost" y, en otro borrador, "ofrece Acme Pass": la
   marca entró dos veces, violando "Acme entra UNA vez, natural" (perfil, campo
   `purpose`). Ver `redaccion._marca_para_una_sola_mencion`.
3. Posts de 37, 55 y 74 palabras contra un `target_length` de "entre 110 y 140... nunca
   menos de 100": muy por encima del piso de CARACTERES genérico del motor
   (`MIN_COPY_ENTREGABLE_CHARS`), muy por debajo del piso de PALABRAS que esta cuenta en
   particular pidió. Ver `redaccion._piso_palabras_del_perfil`.

Cada test de aquí fija UNA de esas reglas sobre la capa determinista, más el helper que
decide CUÁNDO activarla (todas son opt-in: sólo se aplican al perfil que las pidió, nunca a
una cuenta cualquiera) y el que decidió el bug de raíz, `_pilar_es_noticia`.
"""

from __future__ import annotations

from edecan_creative.editorial import revisar
from edecan_creative.redaccion import (
    _marca_para_una_sola_mencion,
    _perfil_prohibe_numeros_extensos,
    _pilar_es_noticia,
    _piso_palabras_del_perfil,
)

# El perfil real de Acme (recortado a lo que importa para estas reglas), tal como quedó
# guardado en `social_editorial_profiles` para el tenant que reportó el bug.
_PERFIL_ORGANIZATION = {
    "purpose": (
        "Escribir borradores para la página de LinkedIn de Acme, un buró de crédito "
        "alternativo para Venezuela. Cada post cuenta una escena venezolana concreta (el "
        "fiao, la quincena, el alquiler, la remesa, Cashea) donde Acme entra una sola "
        "vez, de forma natural. Nunca autopublicar sin aprobación humana."
    ),
    "voice": (
        "Alguien que sabe de crédito hablándole a un colega... Sin números de dos o más "
        "dígitos: todo en palabras."
    ),
    "avoid": (
        "No decir que Acme es banco... Cero números de dos o más dígitos: todo en "
        "palabras. Nunca voseo."
    ),
    "notes": "",
    "closing_url": ("Descarga el nuevo buró de crédito alternativo para Venezuela en: example.org"),
    "target_length": (
        "Entre 110 y 140 palabras (sin contar la línea de cierre). Nunca menos de 100."
    ),
}


# ---------------------------------------------------------------------------
# 1. Cero números de dos o más dígitos.
# ---------------------------------------------------------------------------


def test_perfil_prohibe_numeros_extensos_detecta_la_regla_del_avoid():
    assert _perfil_prohibe_numeros_extensos(_PERFIL_ORGANIZATION) is True


def test_un_perfil_sin_esa_regla_no_activa_el_gate():
    assert _perfil_prohibe_numeros_extensos({"avoid": "Sin hashtags, sin emojis."}) is False
    assert _perfil_prohibe_numeros_extensos({}) is False


def test_revisar_atrapa_el_100mm_cuando_el_gate_esta_activo():
    """La cifra exacta del post real que se publicó: "Cashea levantó $100M..."."""
    texto = "Cashea levantó $100M, la mayor ronda fintech de Venezuela este año."

    violaciones = revisar(texto, "linkedin", prohibir_numeros_extensos=True)

    assert any("cifra" in v.lower() for v in violaciones)


def test_revisar_no_atrapa_numeros_sin_pedirlo_explicitamente():
    """Por defecto (la mayoría de cuentas) un número real sigue siendo bienvenido -- la regla
    4 de REGLAS_LINKEDIN ya cubre lo que importa (cero cifras INVENTADAS)."""
    texto = "Cashea levantó $100M, la mayor ronda fintech de Venezuela este año."

    assert revisar(texto, "linkedin") == []


def test_el_gate_de_numeros_sobrevive_al_modo_permisivo():
    """Es una regla DURA que el propio tenant declaró para su cuenta -- igual que el nombre
    del banco, no debe desaparecer sólo porque el usuario pidió el tema."""
    texto = "Cashea levantó $100M este año."

    violaciones = revisar(texto, "linkedin", permisivo=True, prohibir_numeros_extensos=True)

    assert any("cifra" in v.lower() for v in violaciones)


def test_un_texto_todo_en_palabras_pasa_el_gate():
    texto = "Cashea levantó una cifra enorme de inversión este año, la mayor de una fintech."

    assert revisar(texto, "linkedin", prohibir_numeros_extensos=True) == []


# ---------------------------------------------------------------------------
# 2. La marca entra una sola vez.
# ---------------------------------------------------------------------------


def test_marca_para_una_sola_mencion_la_saca_del_dominio_de_cierre():
    assert _marca_para_una_sola_mencion(_PERFIL_ORGANIZATION) == "example"


def test_sin_la_frase_una_sola_vez_en_el_perfil_no_se_activa():
    """Doble señal a propósito: el dominio solo no basta. Un tenant con `closing_url`
    configurado pero que nunca pidió el límite de una sola mención no debe quedar atado a
    él -- mencionar la marca dos veces puede ser perfectamente normal para otra cuenta."""
    perfil = {"closing_url": "example.org", "purpose": "Una página cualquiera."}

    assert _marca_para_una_sola_mencion(perfil) == ""


def test_sin_closing_url_no_hay_marca_que_proteger():
    perfil = {"purpose": "Acme entra una sola vez, de forma natural."}

    assert _marca_para_una_sola_mencion(perfil) == ""


# ---------------------------------------------------------------------------
# 3. El piso de PALABRAS del perfil, distinto del piso de CARACTERES del motor.
# ---------------------------------------------------------------------------


def test_piso_de_palabras_toma_el_menor_numero_del_target_length():
    assert _piso_palabras_del_perfil(_PERFIL_ORGANIZATION) == 100


def test_sin_target_length_el_piso_es_cero():
    assert _piso_palabras_del_perfil({}) == 0
    assert _piso_palabras_del_perfil({"target_length": "Lo que haga falta."}) == 0


# ---------------------------------------------------------------------------
# 4. Sólo el pilar "noticia" ancla en actualidad; el resto son escenas.
# ---------------------------------------------------------------------------


def test_pilar_es_noticia_reconoce_la_convencion_del_perfil():
    assert _pilar_es_noticia({"pilar": "noticia de la semana"}) is True
    assert _pilar_es_noticia({"pilar": "LA NOTICIA DE LA SEMANA: toma un hecho..."}) is True


def test_una_escena_de_vida_no_es_el_pilar_noticia():
    """La escena real detrás del bug: "pagos a plazos" nombra una marca con noticias
    propias, pero no es -- ella misma -- la noticia de la semana."""
    assert _pilar_es_noticia({"pilar": "pagos a plazos"}) is False
    assert _pilar_es_noticia({"pilar": "el mostrador"}) is False
