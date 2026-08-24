"""La skill Fable existe, es vasta, y va montada en el ORDEN que la hace funcionar.

El orden no es estético: es el mismo de REFERENCIA (`_cargar_voz`, `linkedin_content.py:536`)
-- la metodología abre, las reglas duras cierran "para que ganen", y el formato de salida
va al final de todo porque lo último que se lee es lo que menos se olvida. Si alguien
invierte ese orden por descuido, las reglas dejan de mandar sobre el método y el contrato
de salida queda enterrado en el medio del prompt.
"""

from __future__ import annotations

from edecan_creative.editorial import REGLAS_LINKEDIN
from edecan_creative.fable import FABLE_ESCRITURA
from edecan_creative.redaccion import _SISTEMA_ESCRITOR


def test_la_skill_es_vasta_y_tiene_sus_piezas() -> None:
    # "No me refiero a 30 palabras: una skill completa desde cero... que a un humano le
    # dé flojera porque es demasiado" fue el pedido literal (01-ago-2026): si un refactor
    # la reduce, este número lo delata. No fija el contenido exacto -- la skill puede
    # mejorar -- pero sí su escala y sus secciones estructurales.
    assert len(FABLE_ESCRITURA) > 15000
    for seccion in (
        "DIRECTIVAS PRIMARIAS",
        "PROTOCOLO DE LECTURA DEL ENCARGO",
        "LAS NUEVE PASADAS",
        "LAS CINCO PREGUNTAS",
        "CÓMO ATACAR EL PROBLEMA",
        "LA VOZ",
        "AUTORREFUTACIÓN",
        "MODOS DE FALLO CONOCIDOS",
        "ESCALADA DE PROFUNDIDAD",
        "LISTAS DE CONTROL",
        "ANTI-PATRONES",
        "MICRO-EJEMPLO TRABAJADO",
        "LA VERSIÓN COMPRIMIDA",
        "AUTO-TEST FINAL",
    ):
        assert seccion in FABLE_ESCRITURA, f"la skill perdió la sección {seccion!r}"


def test_el_escritor_la_recibe_en_el_orden_de_jarvis() -> None:
    # Método primero, reglas duras después (las reglas ganan), salida JSON al final.
    pos_fable = _SISTEMA_ESCRITOR.index("MÉTODO FABLE DE ESCRITURA")
    pos_reglas = _SISTEMA_ESCRITOR.index("REGLAS DURAS DE VOZ PARA LINKEDIN")
    pos_salida = _SISTEMA_ESCRITOR.index("SALIDA (obligatorio)")
    assert pos_fable < pos_reglas < pos_salida

    # Y las reglas viajan completas, no un extracto: el bloque literal sigue ahí.
    assert REGLAS_LINKEDIN in _SISTEMA_ESCRITOR


def test_la_skill_declara_su_jerarquia() -> None:
    # El preámbulo de la skill (antes de la primera sección) le dice al modelo que las
    # REGLAS DURAS mandan si algo choca y que el formato de SALIDA manda sobre la forma.
    # Sin esa frase, un método que dice "reconócelo en el texto" podría leerse como
    # permiso para publicar un dato dudoso "reconocido" -- las reglas lo prohíben.
    preambulo = FABLE_ESCRITURA.split("SECCIÓN 0")[0]
    assert "REGLAS DURAS" in preambulo
    assert "SALIDA" in preambulo
