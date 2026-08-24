"""Pruebas puras (sin ``WorkersIDEAgent`` ni sesión real) de ``ide_refutador``:
el gate de costo, el prompt adversarial y el parseo/degradación del
veredicto. La integración con un turno real de plan vive en
``test_ide_sessions_refutador.py``.
"""

from __future__ import annotations

import pytest
from edecan_companion import ide_refutador as R

# --------------------------------------------------------------------- #
# El interruptor de costo
# --------------------------------------------------------------------- #


def test_refutador_habilitado_por_defecto(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(R.REFUTADOR_HABILITADO_ENV, raising=False)
    assert R.refutador_habilitado() is True


@pytest.mark.parametrize("valor", ["0", "false", "False", "no", "off", "OFF"])
def test_refutador_se_puede_apagar_por_env(monkeypatch: pytest.MonkeyPatch, valor: str):
    monkeypatch.setenv(R.REFUTADOR_HABILITADO_ENV, valor)
    assert R.refutador_habilitado() is False


def test_modelo_por_defecto_distinto_del_reparador(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(R.REFUTADOR_MODELO_ENV, raising=False)
    # No compara contra un import cruzado a propósito -- el punto central
    # de este test es que HAY un default fijo y no es None/"" ni depende de
    # ninguna variable de entorno del reparador.
    assert R.modelo_refutador() == R.MODELO_REFUTADOR_POR_DEFECTO
    assert R.MODELO_REFUTADOR_POR_DEFECTO != "@cf/zai-org/glm-5.2"


def test_modelo_se_puede_forzar_por_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(R.REFUTADOR_MODELO_ENV, "@cf/otro/modelo")
    assert R.modelo_refutador() == "@cf/otro/modelo"


# --------------------------------------------------------------------- #
# El gate: cuándo corre y cuándo no (con motivo explícito)
# --------------------------------------------------------------------- #


def test_motivo_para_omitir_none_cuando_debe_correr(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(R.REFUTADOR_HABILITADO_ENV, raising=False)
    assert (
        R.motivo_para_omitir(cancelado=False, hay_pasos_completados=True, archivos_tocados=2)
        is None
    )


def test_motivo_para_omitir_desactivado(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(R.REFUTADOR_HABILITADO_ENV, "0")
    motivo = R.motivo_para_omitir(cancelado=False, hay_pasos_completados=True, archivos_tocados=2)
    assert motivo is not None
    assert "desactivado" in motivo


def test_motivo_para_omitir_plan_cancelado(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(R.REFUTADOR_HABILITADO_ENV, raising=False)
    motivo = R.motivo_para_omitir(cancelado=True, hay_pasos_completados=True, archivos_tocados=2)
    assert motivo is not None
    assert "canceló" in motivo


def test_motivo_para_omitir_sin_pasos_completados(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(R.REFUTADOR_HABILITADO_ENV, raising=False)
    motivo = R.motivo_para_omitir(cancelado=False, hay_pasos_completados=False, archivos_tocados=0)
    assert motivo is not None
    assert "completado" in motivo


def test_motivo_para_omitir_sin_archivos_tocados(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(R.REFUTADOR_HABILITADO_ENV, raising=False)
    motivo = R.motivo_para_omitir(cancelado=False, hay_pasos_completados=True, archivos_tocados=0)
    assert motivo is not None
    assert "ningún archivo" in motivo


# --------------------------------------------------------------------- #
# El prompt: nunca lleva el razonamiento del reparador, solo su resultado
# --------------------------------------------------------------------- #


def test_construir_prompt_exige_al_menos_un_paso():
    with pytest.raises(ValueError, match="al menos un paso"):
        R.construir_prompt(meta="x", pasos=[], archivos_tocados=["a.py"])


def test_construir_prompt_incluye_encargo_y_resultado_no_razonamiento():
    paso = R.PasoParaRefutar(
        titulo="Agregar validación",
        instrucciones="Valida el email en el endpoint de registro.",
        resultado_reportado="Agregué una validación con regex y devolví 422 si falla.",
    )
    prompt = R.construir_prompt(
        meta="Endurecer el registro", pasos=[paso], archivos_tocados=["apps/api/registro.py"]
    )
    assert "Valida el email en el endpoint de registro." in prompt
    assert "Agregué una validación con regex y devolví 422 si falla." in prompt
    assert "apps/api/registro.py" in prompt
    # Vocabulario adversarial obligatorio del encargo.
    assert "REFUTADOR" in prompt
    assert "TUMBARLO" in prompt
    assert "NO_DEMOSTRADO" in prompt
    assert "cuadrado azul" in prompt
    assert "WAV" in prompt


def test_construir_prompt_prohibe_escribir_y_editar():
    paso = R.PasoParaRefutar(titulo="t", instrucciones="i", resultado_reportado="r")
    prompt = R.construir_prompt(meta="m", pasos=[paso], archivos_tocados=[])
    assert "'escribir_archivo'" in prompt
    assert "'editar_archivo'" in prompt
    assert "No arregles nada" in prompt


def test_construir_prompt_sin_archivos_dice_que_no_hubo():
    paso = R.PasoParaRefutar(titulo="t", instrucciones="i", resultado_reportado="r")
    prompt = R.construir_prompt(meta="m", pasos=[paso], archivos_tocados=[])
    assert "ningún archivo quedó registrado" in prompt


def test_construir_prompt_trunca_pasos_y_archivos_con_aviso():
    pasos = [
        R.PasoParaRefutar(titulo=f"paso {i}", instrucciones=f"i{i}", resultado_reportado=f"r{i}")
        for i in range(R.MAX_PASOS_EN_PROMPT + 5)
    ]
    archivos = [f"archivo_{i}.py" for i in range(R.MAX_ARCHIVOS_EN_PROMPT + 5)]
    prompt = R.construir_prompt(meta="m", pasos=pasos, archivos_tocados=archivos)
    assert "se omiten 5 paso(s) adicionales" in prompt
    assert "hay 5 archivo(s) más modificados" in prompt
    # El último paso/archivo generado NO debe aparecer -- confirma que sí se recortó.
    assert f"paso {R.MAX_PASOS_EN_PROMPT + 4}" not in prompt
    assert f"archivo_{R.MAX_ARCHIVOS_EN_PROMPT + 4}.py" not in prompt


# --------------------------------------------------------------------- #
# Parseo del veredicto: ante la duda, no_demostrado
# --------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "texto,esperado",
    [
        ("Todo bien.\n\nVEREDICTO: APROBADO", "aprobado"),
        ("Encontré un bug.\n\nVEREDICTO: REFUTADO", "refutado"),
        ("No pude confirmarlo.\n\nVEREDICTO: NO_DEMOSTRADO", "no_demostrado"),
        ("veredicto: aprobado", "aprobado"),  # minúsculas también cuentan
    ],
)
def test_parsear_veredicto_casos_normales(texto: str, esperado: str):
    assert R.parsear_veredicto(texto) == esperado


@pytest.mark.parametrize(
    "texto",
    [
        "",
        "No dije nada parseable.",
        "VEREDICTO: APROBADO\n...\nVEREDICTO: REFUTADO",  # contradictorio
        "Casi digo VEREDICTO pero no puse ningún valor válido.",
    ],
)
def test_parsear_veredicto_ambiguo_o_ausente_es_no_demostrado(texto: str):
    assert R.parsear_veredicto(texto) == "no_demostrado"


# --------------------------------------------------------------------- #
# VeredictoRefutador: la degradación por falta de evidencia es la garantía
# central del riesgo 6 del cableado ("un refutador que no mide es peor que
# ninguno").
# --------------------------------------------------------------------- #


def test_veredicto_aprobado_con_evidencia_se_mantiene():
    v = R.VeredictoRefutador.desde_respuesta(
        "Corrí los tests con verificar y pasaron.\n\nVEREDICTO: APROBADO",
        modelo="m",
        herramientas_usadas=["verificar"],
        archivos_auditados=["a.py"],
    )
    assert v.verdict == "aprobado"
    assert v.degradado_por_falta_de_evidencia is False


def test_veredicto_aprobado_sin_evidencia_se_degrada():
    v = R.VeredictoRefutador.desde_respuesta(
        "Se ve razonable.\n\nVEREDICTO: APROBADO",
        modelo="m",
        herramientas_usadas=[],
        archivos_auditados=["a.py"],
    )
    assert v.verdict_bruto == "aprobado"
    assert v.verdict == "no_demostrado"
    assert v.degradado_por_falta_de_evidencia is True
    assert "degradado" in v.bloque_para_persona()


def test_veredicto_refutado_no_se_degrada_aunque_no_haya_evidencia():
    # Refutar es una afirmación distinta: "encontré un problema" no necesita
    # el mismo umbral que "confirmo que todo está bien" -- degradar un
    # REFUTADO a NO_DEMOSTRADO escondería justo el caso que más importa
    # exponer.
    v = R.VeredictoRefutador.desde_respuesta(
        "Esto no puede funcionar.\n\nVEREDICTO: REFUTADO",
        modelo="m",
        herramientas_usadas=[],
        archivos_auditados=[],
    )
    assert v.verdict == "refutado"


def test_veredicto_detecta_herramientas_prohibidas():
    v = R.VeredictoRefutador.desde_respuesta(
        "Arreglé el bug yo mismo.\n\nVEREDICTO: APROBADO",
        modelo="m",
        herramientas_usadas=["leer_archivo", "escribir_archivo"],
        archivos_auditados=["a.py"],
    )
    assert v.uso_herramientas_prohibidas is True
    assert "no debía usar" in v.bloque_para_persona()


def test_bloque_para_persona_siempre_visible_y_nombra_el_modelo():
    for verdict_bruto, herramientas in (
        ("aprobado", ["verificar"]),
        ("refutado", ["leer_archivo"]),
        ("no_demostrado", []),
    ):
        v = R.VeredictoRefutador.desde_respuesta(
            f"texto\n\nVEREDICTO: {verdict_bruto.upper()}",
            modelo="@cf/moonshotai/kimi-k2.7-code",
            herramientas_usadas=herramientas,
            archivos_auditados=[],
        )
        bloque = v.bloque_para_persona()
        assert "@cf/moonshotai/kimi-k2.7-code" in bloque
        assert "Auditoría independiente" in bloque


def test_texto_sin_veredicto_no_repite_la_linea_de_veredicto():
    v = R.VeredictoRefutador.desde_respuesta(
        "Revisé el archivo con leer_archivo y está vacío.\n\nVEREDICTO: REFUTADO",
        modelo="m",
        herramientas_usadas=["leer_archivo"],
        archivos_auditados=["a.py"],
    )
    assert "VEREDICTO" not in v.texto
    assert "está vacío" in v.texto
