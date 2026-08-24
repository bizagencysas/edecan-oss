"""`edecan_creative.agenda.territorios_desde_pilares`: los `content_pillars` de un tenant
convertidos en territorios rotables.

Bug real que este módulo cierra (ver `redaccion._territorios_del_perfil`): sin esta
conversión, la rotación "sin tema pedido" de `crear_post_linkedin` SIEMPRE caía en
`TERRITORIOS_POR_DEFECTO` -- un catálogo genérico (tecnología, finanzas...) sin ninguna
relación con la voz de la cuenta que el tenant configuró -- así que "escríbeme el post de
hoy" nunca tocaba en verdad los pilares propios de esa cuenta. Alex, 31-jul-2026: "siento
que es lo mismo, es repetitivo... debería crear otro para ver su creatividad".
"""

from __future__ import annotations

from edecan_creative import agenda


def test_cada_pilar_se_vuelve_su_propio_territorio_con_el_texto_del_tenant():
    pilares = ["el mostrador", "pagos a plazos", "primer crédito"]

    territorios = agenda.territorios_desde_pilares(pilares)

    assert len(territorios) == 3
    for pilar, territorio in zip(pilares, territorios, strict=True):
        # Ni ejemplo de post ni instrucción de estructura añadidos: el texto del tenant
        # viaja IDÉNTICO como etiqueta, pilar y query (ver el docstring de la función sobre
        # por qué mezclar un texto modelo en el prompt de rotación produce clones).
        assert territorio["etiqueta"] == pilar
        assert territorio["pilar"] == pilar
        assert territorio["query"] == pilar
        assert territorio["id"]  # nunca vacío


def test_ids_deterministas_legibles_y_sin_acentos():
    territorios = agenda.territorios_desde_pilares(["Día de quincena", "El que vuelve al país"])

    assert territorios[0]["id"] == "dia_de_quincena"
    assert territorios[1]["id"] == "el_que_vuelve_al_pais"


def test_pilares_duplicados_o_vacios_no_rompen_ni_colisionan_ids():
    territorios = agenda.territorios_desde_pilares(["el mostrador", "", "  ", "el mostrador"])

    # Las cadenas vacías/blancas no producen territorio.
    assert len(territorios) == 2
    # El duplicado exacto no colisiona: el segundo id se desambigua.
    assert territorios[0]["id"] == "el_mostrador"
    assert territorios[1]["id"] == "el_mostrador_2"


def test_lista_vacia_devuelve_vacio_para_que_el_llamador_decida_el_fallback():
    assert agenda.territorios_desde_pilares([]) == []


def test_siguiente_paso_rota_sobre_los_pilares_del_tenant_en_vez_del_catalogo_generico():
    pilares = ["el mostrador", "pagos a plazos", "primer crédito"]
    territorios = agenda.territorios_desde_pilares(pilares)
    estado = agenda.estado_inicial()

    paso1 = agenda.siguiente_paso(estado, territorios=territorios)
    assert paso1.territorio["etiqueta"] == "el mostrador"

    paso2 = agenda.siguiente_paso(paso1.estado, territorios=territorios)
    assert paso2.territorio["etiqueta"] == "pagos a plazos"

    paso3 = agenda.siguiente_paso(paso2.estado, territorios=territorios)
    assert paso3.territorio["etiqueta"] == "primer crédito"

    # Round-robin: agotados los 3, vuelve al primero -- nunca al catálogo genérico.
    paso4 = agenda.siguiente_paso(paso3.estado, territorios=territorios)
    assert paso4.territorio["etiqueta"] == "el mostrador"
