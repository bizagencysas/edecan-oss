"""La escalera de profundidades deja de subir tras dos fallos seguidos.

Sin esto, medir hasta 256k cuesta ~82 USD aunque el contexto útil se rompa
en 48k: se paga el tramo caro solo para reconfirmar un fallo.
"""

from __future__ import annotations

import inspect

from edecan_forge_probe.probes import context as ctx


def test_la_constante_existe_y_exige_dos_fallos():
    assert ctx.FALLOS_PARA_PARAR == 2


def test_la_sonda_acepta_el_parametro():
    firma = inspect.signature(ctx.sondar_contexto)
    p = firma.parameters["fallos_para_parar"]
    assert p.default == ctx.FALLOS_PARA_PARAR
    assert p.kind is inspect.Parameter.KEYWORD_ONLY


def test_un_fallo_aislado_no_corta_la_escalera():
    """Una profundidad mala entre dos buenas es una anomalía, no el techo."""
    fallos = 0
    corto_en = None
    for pasa in [True, False, True, False, False, True]:
        fallos = 0 if pasa else fallos + 1
        if fallos >= ctx.FALLOS_PARA_PARAR:
            corto_en = pasa
            break
    assert corto_en is False  # cortó en el segundo fallo seguido, no en el aislado


def test_el_detalle_declara_la_parada():
    fuente = inspect.getsource(ctx.sondar_contexto)
    assert '"parada_temprana": parada_temprana' in fuente
    assert '"no_medidas"' in fuente  # queda constancia de lo que NO se midió
