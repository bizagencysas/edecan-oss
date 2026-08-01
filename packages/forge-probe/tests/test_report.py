"""Tests del informe: el veredicto tiene que ser imposible de malinterpretar.

Lo que se comprueba aquí no es el formato por el formato. Es que un NO-GO diga
POR QUÉ y QUÉ REDISEÑAR citando los bloques 3, 4 y 6 de la arquitectura, que un
hueco de medición se vea tan rojo como un fallo, y que un coste que nadie pudo
calcular no aparezca nunca como 0,00 USD.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from edecan_forge_probe.modelcard import ArgProfile, Latencia, ModelCard, ProbeResult, Reliability
from edecan_forge_probe.report import (
    REDISENOS,
    escribir_informe,
    escribir_modelcard,
    render_markdown,
)
from edecan_forge_probe.runner import Contabilidad, Uso

CARD_MINIMA = ModelCard(
    modelo="@cf/moonshotai/kimi-k2.7-code",
    proveedor="workers-ai",
    medido_en=datetime(2026, 7, 27, tzinfo=UTC),
    revision_sonda="abc1234",
)


def card_completa(**cambios: object) -> ModelCard:
    """Una tarjeta que pasa todos los umbrales, para poder romper uno a uno."""
    base = CARD_MINIMA.model_copy(
        update={
            "ventana_anunciada": 262_144,
            "usable_context_tokens": 96_000,
            "native_tools": {ArgProfile.CODE_BLOB: Reliability(successes=200, trials=200)},
            "throughput_tps": 45.0,
            "ttft": Latencia(p50=0.6, p95=1.2, muestras=30),
            "bench_success": Reliability(successes=28, trials=30),
            "max_tools_effective": 24,
        }
    )
    return base.model_copy(update=cambios)


# --------------------------------------------------------------------------- #
# Veredicto
# --------------------------------------------------------------------------- #


def test_una_tarjeta_vacia_es_no_go_y_lo_dice_grande() -> None:
    md = render_markdown(CARD_MINIMA)
    assert "N O - G O" in md
    assert "G O" in md
    assert "SIN DATO" in md


def test_go_cuando_todo_pasa() -> None:
    md = render_markdown(card_completa())
    assert "N O - G O" not in md
    assert "Ningún umbral bloqueante falla" in md


def test_el_no_go_no_dice_que_el_modelo_sea_malo() -> None:
    md = render_markdown(CARD_MINIMA)
    assert "no** dice que el modelo sea malo" in md or "no dice que el modelo sea malo" in md


def test_un_hueco_de_medicion_se_marca_igual_de_rojo_que_un_fallo() -> None:
    md = render_markdown(card_completa(usable_context_tokens=None))
    assert "SIN DATO" in md
    assert "Sin dato." in md
    assert "nunca se interpreta como" in md


# --------------------------------------------------------------------------- #
# La sección que justifica la fase 0
# --------------------------------------------------------------------------- #


def test_contexto_insuficiente_manda_al_bloque_3() -> None:
    md = render_markdown(card_completa(usable_context_tokens=12_000))
    assert "Bloque 3 — Context Engine" in md
    assert "recuperación selectiva" in md


def test_code_blob_flojo_manda_al_bloque_4() -> None:
    md = render_markdown(
        card_completa(native_tools={ArgProfile.CODE_BLOB: Reliability(successes=12, trials=20)})
    )
    assert "Bloque 4 — Tool ABI" in md
    assert "apply_patch" in md


def test_banco_flojo_manda_al_bloque_6() -> None:
    md = render_markdown(card_completa(bench_success=Reliability(successes=5, trials=30)))
    assert "Bloque 6 — Agent Runtime" in md
    assert "sub-tarea corta" in md


def test_todos_los_umbrales_tienen_consecuencia_de_diseno() -> None:
    """Un umbral sin rediseño asociado es un umbral que no sabe decidir nada."""
    from edecan_forge_probe.modelcard import UMBRALES_FASE_0

    faltan = [u.clave for u in UMBRALES_FASE_0 if u.clave not in REDISENOS]
    assert faltan == []
    assert {r.bloque for r in REDISENOS.values()} == {3, 4, 6}


def test_un_umbral_justo_se_reporta_como_riesgo_sin_bloquear() -> None:
    md = render_markdown(card_completa(throughput_tps=26.0))
    assert "JUSTO (riesgo)" in md
    assert "Riesgos a vigilar" in md


# --------------------------------------------------------------------------- #
# Contenido medido
# --------------------------------------------------------------------------- #


def test_la_curva_de_contexto_se_dibuja_desde_la_evidencia() -> None:
    card = card_completa(
        resultados=[
            ProbeResult(
                probe="contexto.aguja",
                ok=True,
                detalle={
                    "curva": [
                        {"tokens": 128_000, "successes": 6, "trials": 20},
                        {"tokens": 8_000, "successes": 20, "trials": 20},
                        {"tokens": 64_000, "lower_95": 0.71},
                    ]
                },
            )
        ]
    )
    md = render_markdown(card)
    assert "Curva de contexto útil" in md
    assert "8,000" in md and "128,000" in md
    # Ordenada de menos a más profundidad, no en el orden en que la publicó la sonda.
    assert md.index("8,000") < md.index("128,000")


def test_sin_curva_se_dice_que_falta_no_se_dibuja_una_plana() -> None:
    md = render_markdown(card_completa())
    assert "Ninguna sonda publicó" in md


def test_la_ventana_anunciada_se_contrasta_con_la_util() -> None:
    md = render_markdown(card_completa())
    assert "262,144" in md
    assert "Útil / anunciada" in md
    assert "37%" in md


def test_desglose_de_tool_calling_por_perfil() -> None:
    md = render_markdown(card_completa())
    assert "`code_blob`" in md
    assert "el que decide" in md
    assert "`scalar`" in md  # los perfiles sin medir aparecen como huecos


# --------------------------------------------------------------------------- #
# Coste y razonamiento
# --------------------------------------------------------------------------- #


def test_sin_precios_el_informe_no_finge_un_coste() -> None:
    conta = Contabilidad()
    conta.anotar("a", Uso(entrada=1000, salida=500))
    md = render_markdown(CARD_MINIMA, contabilidad=conta)
    assert "Coste: sin calcular" in md
    assert "0.0000 USD" not in md


def test_con_precios_se_reporta_el_gasto_y_el_acierto_de_cache() -> None:
    conta = Contabilidad(
        precio_entrada_usd_mtok=0.95,
        precio_salida_usd_mtok=4.00,
        precio_cacheado_usd_mtok=0.19,
    )
    conta.anotar("a", Uso(entrada=1_000_000, cacheados=800_000, salida=100_000))
    md = render_markdown(CARD_MINIMA, contabilidad=conta)
    assert "USD**" in md
    assert "Acierto de caché de prefijo: 80%" in md


def test_la_sobrecarga_de_razonamiento_es_una_metrica_visible() -> None:
    conta = Contabilidad()
    conta.anotar("humo", Uso(salida=65, razonamiento=57))
    md = render_markdown(CARD_MINIMA, contabilidad=conta)
    assert "Sobrecarga de razonamiento" in md
    assert "ratio razonamiento/contenido" in md


def test_la_evidencia_se_lista_para_poder_auditar() -> None:
    card = card_completa(
        resultados=[
            ProbeResult(probe="a", ok=True, valor=1.0, evidencia=["/tmp/a/peticion.json"]),
            ProbeResult(probe="b", ok=False, error="timeout"),
        ]
    )
    md = render_markdown(card)
    assert "/tmp/a/peticion.json" in md
    assert "FALLÓ — timeout" in md


# --------------------------------------------------------------------------- #
# Escritura
# --------------------------------------------------------------------------- #


def test_escribir_informe_deja_las_dos_salidas(tmp_path: Path) -> None:
    ruta_json, ruta_md = escribir_informe(card_completa(), tmp_path / "salida")
    assert ruta_json.name == "modelcard.json"
    assert ruta_md.name == "informe.md"
    recargada = ModelCard.model_validate_json(ruta_json.read_text(encoding="utf-8"))
    assert recargada.go() is True


def test_la_modelcard_serializada_se_puede_releer(tmp_path: Path) -> None:
    ruta = escribir_modelcard(CARD_MINIMA, tmp_path / "sub" / "modelcard.json")
    assert ModelCard.model_validate_json(ruta.read_text(encoding="utf-8")).modelo == (
        CARD_MINIMA.modelo
    )
