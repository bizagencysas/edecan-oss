"""La regla que este paquete había perdido al reimplementar el motor de LinkedIn de Aria:
**reparar antes de rechazar**.

El motor de referencia (`aria/features/linkedin_content.py`) lleva meses publicando a diario
con los MISMOS detectores que hay aquí, pero con otro reparto de responsabilidades: lo mecánico
se arregla en código antes de que ningún gate lo vea, lo subjetivo alimenta una reescritura, y
sólo la primera persona mata el borrador. Edecán había convertido los trece hallazgos en
rechazos duros, y con un escritor más débil eso significaba que cada tic de estilo era un turno
perdido en potencia.

Cada test de aquí fija UNA de esas reglas sobre `editorial.py`, que es la capa determinista y
por lo tanto la única que se puede probar sin ningún modelo.
"""

from __future__ import annotations

from edecan_creative.editorial import (
    delatores_de_estilo,
    firma_estructura,
    firmas_prohibidas_recientes,
    instruccion_cooldown_estructura,
    normalizar,
    normalizar_visual,
    revisar,
)

_MENSAJE_PRIMERA_PERSONA = "Quita la primera persona"


# ---------------------------------------------------------------------------
# 1. Lo mecánico se REPARA en `normalizar`, no se rechaza en `revisar`.
# ---------------------------------------------------------------------------


def test_normalizar_borra_hashtags_y_emojis_en_linkedin():
    """Era la violación más trivialmente reparable de todas -- el mismo regex que los detecta
    los borra -- y en cambio quemaba un intento completo de escritura + editor + auditor.
    Aria no tiene siquiera el gate: `_normalizar_texto_post` repara antes de cualquier
    chequeo, así que es imposible que sea motivo de descarte."""
    crudo = "El banco ajustó su tasa 🚀 y el crédito se encarece. #fintech #credito"
    limpio = normalizar(crudo, "linkedin")

    assert "#" not in limpio
    assert "🚀" not in limpio
    assert "El banco ajustó su tasa y el crédito se encarece." in limpio
    # Y ya no queda nada que `revisar` pueda reportar.
    assert revisar(limpio, "linkedin") == []


def test_normalizar_conserva_los_hashtags_donde_son_parte_del_formato():
    """El error simétrico sería borrarlos en Instagram o TikTok, donde son normales. Por eso
    `plataforma` se pide explícita y por defecto no se toca nada."""
    crudo = "Mira esto #fintech"

    assert normalizar(crudo, "instagram") == crudo
    assert normalizar(crudo) == crudo


def test_normalizar_sigue_arreglando_el_guion_largo():
    assert normalizar("El banco cobra — y mucho.") == "El banco cobra, y mucho."


# ---------------------------------------------------------------------------
# 2. `permisivo` llega por fin a la batería determinista.
# ---------------------------------------------------------------------------


def test_en_permisivo_solo_sobrevive_la_primera_persona():
    """El bypass permisivo estaba a medias: `permisivo = bool(tema)` se le pasaba ÚNICAMENTE a
    `revisar_calidad`, y `editorial.revisar` corría dos veces con quince reglas bloqueantes sin
    mirar el flag. Los gates que de verdad mataban el turno no lo consultaban.

    En Aria (`_finalizar_post`, `linkedin_content.py:1572-1618`) en permisivo sobreviven duros
    exactamente dos criterios: cero primera persona y longitud mínima. La longitud la comprueba
    `redaccion`; aquí se fija el otro.
    """
    texto = (
        "El problema real es que nadie mira la letra chica. Aprendí que el banco cobra igual. "
        "¿Deberías revisarlo? ¿O no?"
    )

    duro = revisar(texto, "linkedin", permisivo=True)
    completo = revisar(texto, "linkedin", permisivo=False)

    assert len(duro) == 1
    assert duro[0].startswith(_MENSAJE_PRIMERA_PERSONA)
    # Fuera de permisivo se reportan los demás, que es lo correcto para un texto DICTADO por el
    # usuario en `crear_contenido_social`: ahí se le devuelve exactamente qué corregir.
    assert len(completo) > len(duro)


def test_sin_primera_persona_permisivo_no_reporta_nada():
    texto = "El problema real es que nadie mira la letra chica del contrato antes de firmarlo."

    assert revisar(texto, "linkedin", permisivo=True) == []
    assert revisar(texto, "linkedin") != []


# ---------------------------------------------------------------------------
# 3. Cerrar con pregunta dejó de ser un gate; la fórmula gastada sigue siéndolo.
# ---------------------------------------------------------------------------


def test_cerrar_con_pregunta_no_es_violacion():
    """Edecán era MUCHO más estricto que el motor que funciona, y encima se contradecía: su
    propio `PROMPT_EDITOR_HUMANIZADOR` pide que el cierre pueda ser "una sola pregunta
    discutible y específica", y `revisar` rechazaba cualquier texto cuya última línea terminara
    en "?". El escritor recibía instrucciones que garantizaban el rechazo. En Aria esto es una
    firma de estructura con cooldown, nunca un gate absoluto."""
    texto = (
        "El ajuste de tasas encarece el crédito revolvente. ¿Conviene liquidar el saldo aunque "
        "duela el flujo del mes?"
    )

    assert revisar(texto, "linkedin") == []


def test_la_pregunta_dentro_de_una_frase_normal_ya_no_dispara():
    """El regex viejo (`\\bla pregunta\\b` en los últimos 260 caracteres) disparaba con frases
    perfectamente legítimas que no son preguntas."""
    texto = "El costo sube con la mora. La pregunta la responde el regulador, no el mercado."

    assert revisar(texto, "linkedin") == []


def test_la_formula_gastada_de_cierre_sigue_marcada_pero_es_reparable():
    texto = "El costo sube con la mora. La pregunta que queda es si alguien va a mirarlo."

    violaciones = revisar(texto, "linkedin")
    assert any("fórmula gastada" in v for v in violaciones)
    # Reparable: no sobrevive a permisivo y entra en el insumo de la pasada de corrección.
    assert revisar(texto, "linkedin", permisivo=True) == []
    assert any("fórmula gastada" in v for v in delatores_de_estilo(texto))


# ---------------------------------------------------------------------------
# 4. Los delatores como insumo de reparación, no como sentencia.
# ---------------------------------------------------------------------------


def test_delatores_de_estilo_devuelve_mensajes_accionables_y_no_la_primera_persona():
    """Es el insumo de `auditoria.reparar_delatores`. La primera persona NO entra: esa no se
    repara con el corrector de delatores sino con su propio pase, y es la única dura."""
    texto = "Ahí está el punto. Hice la cuenta y no se trata de tasas, se trata de plazos."

    hallazgos = delatores_de_estilo(texto)

    assert hallazgos, "los delatores tienen que poder alimentar una reescritura"
    assert all(not h.startswith(_MENSAJE_PRIMERA_PERSONA) for h in hallazgos)
    # Cada mensaje dice QUÉ hacer, no sólo el nombre de la regla.
    assert all(len(h) > 30 for h in hallazgos)


# ---------------------------------------------------------------------------
# 5. `normalizar_visual`: el copy que se monta sobre la foto se repara, nunca se rechaza.
# ---------------------------------------------------------------------------


def test_normalizar_visual_rellena_kicker_y_headline_en_vez_de_devolver_nada():
    """El octavo fallo esperando: con un escritor al que se le pide un JSON con un objeto
    ANIDADO de cuatro campos, omitirlo es cuestión de tiempo, y entonces la persona recibía la
    foto pelada -- sin kicker, sin titular y sin subtítulo -- sin que nada lo explicara. Aria
    no puede quedar así jamás (`_normalizar_visual` degrada al tema)."""
    visual = normalizar_visual({}, tema="tasas de tarjetas", titular="Tasas más caras")

    assert visual["kicker"] == "TASAS DE TARJETAS"
    assert visual["headline"] == "Tasas más caras"


def test_normalizar_visual_vacia_el_accent_que_no_existe_en_el_headline():
    """Un acento que no está literalmente en el headline no resalta nada y nadie se entera."""
    visual = normalizar_visual(
        {"headline": "El crédito se encarece", "accent": "hipotecas"}, tema="credito"
    )

    assert visual["accent"] == ""


def test_normalizar_visual_recorta_el_accent_real_respetando_la_caja_del_headline():
    visual = normalizar_visual(
        {"headline": "El crédito se encarece", "accent": "CRÉDITO"}, tema="credito"
    )

    assert visual["accent"] == "crédito"


def test_normalizar_visual_borra_el_support_que_repite_el_titular():
    """El delator visual más frecuente. Antes `revisar_visual` lo detectaba pero sólo se
    aplicaba a `titular_visual`, un campo distinto del que se monta, así que el mismo texto
    salía impreso dos veces en la misma imagen."""
    visual = normalizar_visual(
        {
            "headline": "El crédito revolvente se encarece",
            "support": "El crédito revolvente se encarece más",
        },
        tema="credito",
    )

    assert visual["support"] == ""


def test_normalizar_visual_quita_el_prefijo_que_repite_el_kicker_y_recorta():
    """`quitar_prefijo_repetido` existía en el paquete y no la llamaba NADIE."""
    visual = normalizar_visual(
        {"kicker": "FINTECH · CAPITAL", "headline": "Fintech: el crédito sube"}, tema="x"
    )

    assert visual["headline"] == "El crédito sube"
    assert len(normalizar_visual({"kicker": "A" * 90}, tema="x")["kicker"]) <= 44


# ---------------------------------------------------------------------------
# 6. Cooldown estructural: no repetir la FORMA, no sólo el tema.
# ---------------------------------------------------------------------------


def test_firma_estructura_reconoce_apertura_con_porcentaje_y_cierre_en_pregunta():
    firmas = firma_estructura(
        "El 40% de los bancos ajustó tasas. Nadie avisó a los clientes. ¿Y ahora?",
        {"headline": "Tasas más caras"},
    )

    assert firmas == {"apertura_porcentaje", "cierre_pregunta"}


def test_firmas_prohibidas_solo_miran_los_ultimos_posts():
    historial = [
        {"firma": ["cierre_pregunta"]},
        {"firma": ["apertura_porcentaje"]},
    ]

    prohibidas = firmas_prohibidas_recientes(historial)

    assert "apertura_porcentaje" in prohibidas
    assert "titular_porcentaje" in prohibidas
    # El cierre en pregunta era del penúltimo, no del último: ya no está en cooldown.
    assert "cierre_pregunta" not in prohibidas


def test_una_entrada_de_historial_vieja_sin_firma_no_rompe_nada():
    """Las entradas guardadas antes de este port no tienen la clave `firma`."""
    assert firmas_prohibidas_recientes([{"tema": "algo"}, {}]) == set()
    assert firmas_prohibidas_recientes(None) == set()


def test_la_instruccion_de_cooldown_esta_vacia_cuando_no_hay_nada_prohibido():
    assert instruccion_cooldown_estructura(set()) == ""
    assert "PROHIBIDO cerrar con una pregunta" in instruccion_cooldown_estructura(
        {"cierre_pregunta"}
    )


# ---------------------------------------------------------------------------
# 7. La capa de oficio que se había perdido al condensar la voz.
# ---------------------------------------------------------------------------


def test_las_reglas_traen_la_seccion_de_espanol_con_el_voseo_prohibido():
    """No aparecía la palabra "voseo" ni "tuteo" en ningún archivo del paquete: el escritor no
    recibía NINGUNA instrucción de variante de español, y es una exigencia explícita y enfática
    del dueño de la cuenta."""
    from edecan_creative.editorial import REGLAS_LINKEDIN

    assert "TUTEO" in REGLAS_LINKEDIN
    assert "voseo" in REGLAS_LINKEDIN
    for prohibido in ("tenés", "querés", "sos", "podés", "sabés"):
        assert prohibido in REGLAS_LINKEDIN


def test_las_reglas_traen_la_disciplina_de_calidad_y_la_prueba_del_scroll():
    """`fable_calidad.txt` va PRIMERO en Aria y es la capa que evita el borrador
    correcto-pero-genérico, que es justo lo que el editor jefe rechaza después."""
    from edecan_creative.editorial import REGLAS_LINKEDIN

    assert "NO PROMEDIES" in REGLAS_LINKEDIN
    assert "SILENCIO > RUIDO" in REGLAS_LINKEDIN
    assert "PRUEBA DEL SCROLL" in REGLAS_LINKEDIN
    # La regla anti-noticiero, en concreto y medible en vez de en abstracto.
    assert "máximo 2 frases" in REGLAS_LINKEDIN
