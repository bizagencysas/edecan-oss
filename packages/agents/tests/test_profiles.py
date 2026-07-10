"""`edecan_agents.profiles` — las 16 claves pinned (`ROADMAP_V2.md` §7.9), los
15 perfiles activados por `WP-V4-05` más `voice` activado por `WP-V5-05`
(16/16 disponibles), y el guardrail de `permite_dangerous_con_confirmacion`
(campo WP-V4-05)."""

from __future__ import annotations

from edecan_agents.profiles import IMPLEMENTED_AGENT_KEYS, PROFILES, AgentProfile

EXPECTED_KEYS = {
    "research",
    "data_analyst",
    "content",
    "ceo",
    "developer",
    "marketing",
    "finance",
    "sales",
    "design",
    "legal",
    "video",
    "voice",
    "social_media",
    "qa",
    "security",
    "devops",
}

P0_KEYS = {"research", "data_analyst", "content"}

# Los 12 perfiles que WP-V4-05 activa (`disponible=False` -> `True`).
ACTIVADOS_WP_V4_05 = {
    "ceo",
    "design",
    "legal",
    "video",
    "finance",
    "marketing",
    "sales",
    "social_media",
    "developer",
    "qa",
    "security",
    "devops",
}

# Herramientas `dangerous=True` verificadas en el código real (grep
# `dangerous` en `packages/toolkit`/`packages/messaging`, ver el comentario
# por perfil en `profiles.py` y `docs/agentes.md`) que además aparecen hoy en
# el `allowed_tools` de algún perfil — no es TODA tool `dangerous=True` del
# workspace (p. ej. `preparar_pago`/`preparar_orden` de `edecan_commerce`,
# `casa_controlar` de `edecan_smarthome`, la de `edecan_skills` — ninguna la
# referencia ningún perfil hoy), solo las que importan para este guardrail.
_TOOLS_DANGEROUS_CONOCIDAS = {
    "enviar_correo",
    "publicar_social",
    "enviar_mensaje",
    "usar_computadora",
}

# Perfiles `disponible=True` cuyo `allowed_tools` SÍ toca la lista negra de
# arriba -> deben declarar `permite_dangerous_con_confirmacion=True`.
_PERFILES_CON_CONFIRMACION_DANGEROUS = frozenset(
    {"marketing", "sales", "social_media", "developer", "qa", "security", "devops"}
)

# Perfiles `disponible=True` cuyo `allowed_tools` NO toca la lista negra hoy
# (incluye `finance`, cuyas 4 tools son todas de solo lectura/escritura
# informativa pese a que la intención de diseño original lo agrupaba junto a
# los de arriba — ver el comentario de `finance` en `profiles.py` — y
# `voice`, cuyas 2 tools, WP-V5-05, tampoco son `dangerous=True`).
_PERFILES_SIN_CONFIRMACION_DANGEROUS = frozenset(
    {"research", "data_analyst", "content", "ceo", "design", "legal", "video", "finance", "voice"}
)


def test_profiles_trae_las_16_claves_pinned():
    assert set(PROFILES.keys()) == EXPECTED_KEYS
    assert len(PROFILES) == 16


def test_cada_perfil_tiene_su_key_como_atributo_key():
    for key, perfil in PROFILES.items():
        assert isinstance(perfil, AgentProfile)
        assert perfil.key == key


def test_los_tres_perfiles_p0_estan_disponibles():
    for key in P0_KEYS:
        assert PROFILES[key].disponible is True


def test_los_doce_perfiles_de_wp_v4_05_estan_disponibles():
    for key in ACTIVADOS_WP_V4_05:
        assert PROFILES[key].disponible is True, key


def test_ningun_perfil_queda_declarado_no_disponible_tras_wp_v5_05():
    """Tras `WP-V5-05`, `voice` (la única de las 16 claves que seguía
    `disponible=False` después de `WP-V4-05`) también se activa: las 16
    quedan `disponible=True`, ver `docs/agentes.md`."""
    no_disponibles = {key for key, perfil in PROFILES.items() if not perfil.disponible}
    assert no_disponibles == set()


def test_voice_esta_disponible():
    assert PROFILES["voice"].disponible is True


def test_implemented_agent_keys_coincide_con_disponible_true():
    assert IMPLEMENTED_AGENT_KEYS == EXPECTED_KEYS
    assert len(IMPLEMENTED_AGENT_KEYS) == 16
    assert IMPLEMENTED_AGENT_KEYS == frozenset(k for k, p in PROFILES.items() if p.disponible)


def test_todos_los_perfiles_tienen_nombre_descripcion_y_system_prompt_no_vacios():
    for key, perfil in PROFILES.items():
        assert perfil.nombre.strip(), key
        assert perfil.descripcion.strip(), key
        assert perfil.system_prompt_extra.strip(), key


def test_model_alias_por_defecto_es_principal():
    for perfil in PROFILES.values():
        assert perfil.model_alias == "principal"


def test_research_incluye_las_herramientas_exactas_del_wp():
    assert PROFILES["research"].allowed_tools == frozenset(
        {"buscar_web", "navegar_web", "extraer_datos_web", "consultar_documentos", "hora_actual"}
    )


def test_data_analyst_incluye_las_herramientas_exactas_del_wp():
    assert PROFILES["data_analyst"].allowed_tools == frozenset(
        {
            "analizar_tabla",
            "extraer_tablas_pdf",
            "generar_grafico",
            "exportar_analisis",
            "calculadora",
            "consultar_documentos",
            "predecir_serie",
            "detectar_anomalias",
        }
    )


def test_content_incluye_las_herramientas_exactas_del_wp():
    assert PROFILES["content"].allowed_tools == frozenset(
        {"generar_contenido", "crear_documento", "crear_presentacion", "crear_pdf"}
    )


def test_ceo_incluye_las_herramientas_exactas_del_wp():
    assert PROFILES["ceo"].allowed_tools == frozenset(
        {"resumen_finanzas", "estado_negocio", "consultar_documentos"}
    )


def test_design_incluye_las_herramientas_exactas_del_wp():
    assert PROFILES["design"].allowed_tools == frozenset(
        {"generar_imagen", "crear_presentacion", "crear_documento"}
    )


def test_legal_incluye_las_herramientas_exactas_del_wp():
    assert PROFILES["legal"].allowed_tools == frozenset(
        {
            "analizar_contrato",
            "comparar_contratos",
            "generar_borrador_legal",
            "consultar_documentos",
        }
    )


def test_video_ahora_incluye_analizar_video_ademas_de_analizar_imagen():
    """`video` gana `analizar_video` (existe en `edecan_docanalysis`,
    ROADMAP_V2.md §12/WP-V3-14) además de la `analizar_imagen` original."""
    assert PROFILES["video"].allowed_tools == frozenset({"analizar_imagen", "analizar_video"})


def test_voice_incluye_las_herramientas_exactas_del_wp():
    """WP-V5-05: `voice` pasa de `frozenset()`/`disponible=False` a estas 2
    tools pinned (nombres de WP-V5-10, ARCHITECTURE.md §14)."""
    assert PROFILES["voice"].allowed_tools == frozenset({"sintetizar_voz", "listar_voces"})
    assert PROFILES["voice"].permite_dangerous_con_confirmacion is False


def test_finance_marketing_sales_social_media_developer_qa_security_devops_mantienen_sus_tools():
    """El WP pide "mantén sus allowed_tools declaradas" para estos 8 —
    ninguno cambia su conjunto de herramientas, solo `disponible`/
    `permite_dangerous_con_confirmacion`."""
    assert PROFILES["finance"].allowed_tools == frozenset(
        {"resumen_finanzas", "registrar_transaccion", "cotizar_activo", "gestionar_presupuesto"}
    )
    assert PROFILES["marketing"].allowed_tools == frozenset(
        {"generar_contenido", "publicar_social", "generar_imagen", "buscar_web"}
    )
    assert PROFILES["sales"].allowed_tools == frozenset(
        {"buscar_contactos", "gestionar_contacto", "enviar_correo"}
    )
    assert PROFILES["social_media"].allowed_tools == frozenset(
        {"publicar_social", "generar_contenido", "leer_mensajes", "enviar_mensaje"}
    )
    assert PROFILES["developer"].allowed_tools == frozenset(
        {"usar_computadora", "consultar_documentos", "buscar_web"}
    )
    assert PROFILES["qa"].allowed_tools == frozenset({"usar_computadora", "consultar_documentos"})
    assert PROFILES["security"].allowed_tools == frozenset({"usar_computadora", "buscar_web"})
    assert PROFILES["devops"].allowed_tools == frozenset({"usar_computadora"})


# ---------------------------------------------------------------------------
# Guardrail: `permite_dangerous_con_confirmacion` (WP-V4-05)
# ---------------------------------------------------------------------------


def test_perfiles_p0_no_incluyen_tools_de_efectos_externos_ni_dangerous_conocidas():
    """Guardrail central del ecosistema (ver docstring de `profiles.py`): los
    tres perfiles P0 no referencian NINGUNA tool de la lista negra conocida,
    ni siquiera detrás de una confirmación."""
    for key in P0_KEYS:
        perfil = PROFILES[key]
        interseccion = perfil.allowed_tools & _TOOLS_DANGEROUS_CONOCIDAS
        assert not interseccion, f"perfil {key!r} referencia tools peligrosas: {interseccion}"


def test_perfiles_p0_tienen_allowed_tools_no_vacio():
    for key in P0_KEYS:
        assert PROFILES[key].allowed_tools, f"perfil {key!r} no tiene allowed_tools"


def test_los_tres_perfiles_p0_conservan_permite_dangerous_con_confirmacion_false():
    """(b) del WP: los tres P0 originales conservan `False` — su
    comportamiento (guardrail "nada peligroso, nunca") queda byte-a-byte
    idéntico al de antes de `WP-V4-05`."""
    for key in P0_KEYS:
        assert PROFILES[key].permite_dangerous_con_confirmacion is False


def test_todo_perfil_disponible_con_tool_dangerous_conocida_exige_confirmacion():
    """(a) del WP: cualquier perfil `disponible=True` cuyo `allowed_tools`
    toque la lista negra de tools `dangerous` conocidas DEBE declarar
    `permite_dangerous_con_confirmacion=True` — si no,
    `RestrictedRegistry` seguiría ocultando esa tool para siempre y el gate
    de confirmación de `Agent.run_turn` nunca dispararía para ella (ver
    docstring de `profiles.py`, sección "El guardrail evolucionó")."""
    for key, perfil in PROFILES.items():
        if not perfil.disponible:
            continue
        interseccion = perfil.allowed_tools & _TOOLS_DANGEROUS_CONOCIDAS
        if interseccion:
            assert perfil.permite_dangerous_con_confirmacion is True, (
                f"perfil {key!r} referencia tools dangerous {interseccion} pero no "
                "declara permite_dangerous_con_confirmacion=True"
            )


def test_permite_dangerous_con_confirmacion_pinned_por_perfil():
    """Fija el valor EXACTO de `permite_dangerous_con_confirmacion` para las
    16 claves — más estricto que el invariante general de arriba: también
    verifica que ningún perfil lo declare `True` "por si acaso" cuando
    ninguna de sus tools es realmente `dangerous=True` hoy (p. ej. `finance`,
    ver su comentario en `profiles.py`)."""
    assert _PERFILES_CON_CONFIRMACION_DANGEROUS | _PERFILES_SIN_CONFIRMACION_DANGEROUS == (
        EXPECTED_KEYS
    )
    for key in _PERFILES_CON_CONFIRMACION_DANGEROUS:
        assert PROFILES[key].permite_dangerous_con_confirmacion is True, key
    for key in _PERFILES_SIN_CONFIRMACION_DANGEROUS:
        assert PROFILES[key].permite_dangerous_con_confirmacion is False, key


def test_permite_dangerous_con_confirmacion_por_defecto_es_false():
    """El campo del dataclass trae default `False` (comportamiento idéntico
    al de antes de que existiera este campo) — se verifica instanciando un
    `AgentProfile` sin pasarlo explícito."""
    perfil = AgentProfile(
        key="prueba",
        nombre="Prueba",
        descripcion="perfil de prueba",
        system_prompt_extra="prueba",
        allowed_tools=frozenset(),
        disponible=False,
    )
    assert perfil.permite_dangerous_con_confirmacion is False
