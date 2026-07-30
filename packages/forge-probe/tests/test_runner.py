"""Tests del runner con sondas falsas: presupuesto, cancelación y reanudación.

Ninguno de estos tests habla con un modelo. Un runner sólo es creíble si se
puede demostrar que corta cuando tiene que cortar, y eso se demuestra con dobles
deterministas, no pagando tokens para ver qué pasa.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from edecan_forge_probe.modelcard import (
    ArgProfile,
    Capability,
    Latencia,
    ProbeResult,
    Reliability,
    Veredicto,
)
from edecan_forge_probe.runner import (
    Contabilidad,
    ContextoSonda,
    OpcionesRunner,
    PresupuestoAgotado,
    Sonda,
    Uso,
    componer_modelcard,
    descubrir_sondas,
    ejecutar,
    extraer_uso,
    filtrar_sondas,
    guardar_resultado,
    leer_resultados_guardados,
)

# --------------------------------------------------------------------------- #
# Dobles
# --------------------------------------------------------------------------- #


def sonda_trivial(nombre: str, *, valor: float = 1.0, registro: list[str] | None = None) -> Sonda:
    """Sonda que no gasta nada y anota que corrió."""

    async def _ejecutar(ctx: ContextoSonda) -> ProbeResult:
        if registro is not None:
            registro.append(nombre)
        return ProbeResult(probe=nombre, ok=True, valor=valor)

    return Sonda(nombre=nombre, ejecutar=_ejecutar)


def sonda_cara(
    nombre: str, entrada: int, salida: int, *, registro: list[str] | None = None
) -> Sonda:
    """Sonda que consume tokens de verdad a través de `registrar_uso`."""

    async def _ejecutar(ctx: ContextoSonda) -> ProbeResult:
        if registro is not None:
            registro.append(nombre)
        ctx.registrar_uso(entrada=entrada, salida=salida)
        return ProbeResult(probe=nombre, ok=True, valor=1.0)

    return Sonda(nombre=nombre, ejecutar=_ejecutar)


def opciones(tmp_path: Path, **extra: object) -> OpcionesRunner:
    base: dict[str, object] = {
        "modelo": "modelo-de-prueba",
        "proveedor": "falso",
        "revision_sonda": "rev1",
        "directorio_evidencia": tmp_path / "evidencia",
    }
    base.update(extra)
    return OpcionesRunner(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Contabilidad
# --------------------------------------------------------------------------- #


def test_sin_precios_el_coste_es_none_no_cero() -> None:
    """No inventar un precio es tan importante como no inventar una medición."""
    conta = Contabilidad()
    conta.anotar("x", Uso(entrada=1000, salida=500))
    assert conta.tokens_entrada == 1000
    assert conta.coste_usd is None


def test_coste_descuenta_la_entrada_cacheada() -> None:
    conta = Contabilidad(
        precio_entrada_usd_mtok=0.95,
        precio_salida_usd_mtok=4.00,
        precio_cacheado_usd_mtok=0.19,
    )
    conta.anotar("x", Uso(entrada=1_000_000, cacheados=500_000, salida=1_000_000))
    esperado = 500_000 * 0.95 / 1e6 + 500_000 * 0.19 / 1e6 + 4.00
    assert conta.coste_usd == pytest.approx(esperado)
    assert conta.coste_es_cota_superior is False


def test_sin_precio_cacheado_el_coste_es_cota_superior() -> None:
    conta = Contabilidad(precio_entrada_usd_mtok=0.95, precio_salida_usd_mtok=4.00)
    conta.anotar("x", Uso(entrada=1_000_000, cacheados=1_000_000, salida=0))
    assert conta.coste_usd == pytest.approx(0.95)
    assert conta.coste_es_cota_superior is True


def test_ratio_de_razonamiento_sobre_el_contenido() -> None:
    """65 tokens de salida, 57 de razonamiento: el caso medido contra la API real."""
    conta = Contabilidad()
    conta.anotar("humo", Uso(salida=65, razonamiento=57))
    assert conta.ratio_razonamiento == pytest.approx(57 / 8)


def test_extraer_uso_lee_la_forma_real_de_workers_ai() -> None:
    cuerpo = {
        "result": {
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 65,
                "prompt_tokens_details": {"cached_tokens": 100},
                "completion_tokens_details": {"reasoning_tokens": 57},
                "neurons": 12.5,
            }
        }
    }
    uso = extraer_uso(cuerpo)
    assert (uso.entrada, uso.cacheados, uso.salida, uso.razonamiento) == (120, 100, 65, 57)
    assert uso.neurons == 12.5


def test_extraer_uso_tolera_respuestas_sin_usage() -> None:
    assert extraer_uso({"result": {}}).entrada == 0
    assert extraer_uso("no soy un dict").llamadas == 1


# --------------------------------------------------------------------------- #
# Selección y descubrimiento
# --------------------------------------------------------------------------- #


def test_solo_filtra_por_grupo_y_por_nombre() -> None:
    sondas = [
        sonda_trivial("contexto.aguja"),
        sonda_trivial("contexto.pajar"),
        sonda_trivial("tools.code_blob"),
        sonda_trivial("latencia.ttft"),
    ]
    assert [s.nombre for s in filtrar_sondas(sondas, ["contexto"])] == [
        "contexto.aguja",
        "contexto.pajar",
    ]
    assert [s.nombre for s in filtrar_sondas(sondas, ["tools.code_blob"])] == ["tools.code_blob"]
    assert len(filtrar_sondas(sondas, [])) == 4


def test_descubrir_sondas_no_revienta_si_no_hay_modulo() -> None:
    assert descubrir_sondas("edecan_forge_probe.modulo_que_no_existe") == ()


# --------------------------------------------------------------------------- #
# Ejecución feliz y persistencia
# --------------------------------------------------------------------------- #


async def test_ejecucion_completa_persiste_cada_resultado(tmp_path: Path) -> None:
    registro: list[str] = []
    sondas = [sonda_trivial("a", registro=registro), sonda_trivial("b", registro=registro)]
    res = await ejecutar(sondas, opciones(tmp_path), capturar_sigint=False)

    assert registro == ["a", "b"]
    assert res.ejecutadas == ["a", "b"]
    assert res.corte is None
    assert res.completa is True
    guardados = leer_resultados_guardados(tmp_path / "evidencia")
    assert set(guardados) == {"a", "b"}


async def test_una_sonda_que_revienta_no_tumba_el_barrido(tmp_path: Path) -> None:
    async def _explota(ctx: ContextoSonda) -> ProbeResult:
        raise RuntimeError("el proveedor devolvió basura")

    sondas = [Sonda(nombre="rota", ejecutar=_explota), sonda_trivial("b")]
    res = await ejecutar(sondas, opciones(tmp_path), capturar_sigint=False)

    assert res.ejecutadas == ["rota", "b"]
    assert res.corte is None
    rota = next(r for r in res.modelcard.resultados if r.probe == "rota")
    assert rota.ok is False
    assert "RuntimeError" in (rota.error or "")
    # Un fallo no es una medición: no se reutiliza mañana.
    assert "rota" not in leer_resultados_guardados(tmp_path / "evidencia")


async def test_parar_en_error_corta_el_barrido(tmp_path: Path) -> None:
    async def _explota(ctx: ContextoSonda) -> ProbeResult:
        raise RuntimeError("boom")

    registro: list[str] = []
    sondas = [Sonda(nombre="rota", ejecutar=_explota), sonda_trivial("b", registro=registro)]
    res = await ejecutar(sondas, opciones(tmp_path, parar_en_error=True), capturar_sigint=False)
    assert res.corte == "error"
    assert res.pendientes == ["b"]
    assert registro == []


# --------------------------------------------------------------------------- #
# Presupuesto
# --------------------------------------------------------------------------- #


async def test_presupuesto_sin_precios_es_un_error_de_uso(tmp_path: Path) -> None:
    """Un tope de gasto que no se puede calcular es un tope que no existe."""
    with pytest.raises(ValueError, match="precio-entrada"):
        await ejecutar(
            [sonda_trivial("a")],
            opciones(tmp_path, presupuesto_usd=1.0),
            capturar_sigint=False,
        )


async def test_presupuesto_agotado_a_mitad_corta_y_deja_lo_medido(tmp_path: Path) -> None:
    registro: list[str] = []
    sondas = [
        sonda_cara("barata", 100, 100, registro=registro),
        sonda_cara("cara", 900_000, 0, registro=registro),
        sonda_trivial("nunca", registro=registro),
    ]
    res = await ejecutar(
        sondas,
        opciones(
            tmp_path,
            presupuesto_usd=0.5,
            precio_entrada_usd_mtok=1.0,
            precio_salida_usd_mtok=1.0,
        ),
        capturar_sigint=False,
    )

    assert registro == ["barata", "cara"]
    assert res.corte == "presupuesto"
    assert res.pendientes == ["nunca"]
    # El token consumido antes de reventar se factura igual: la sonda ya lo gastó.
    assert res.contabilidad.tokens_entrada == 900_100
    cara = next(r for r in res.modelcard.resultados if r.probe == "cara")
    assert cara.ok is False and "presupuesto" in (cara.error or "")
    # Lo medido antes del corte sobrevive y es reutilizable.
    assert set(leer_resultados_guardados(tmp_path / "evidencia")) == {"barata"}


async def test_presupuesto_agotado_se_comprueba_tambien_entre_sondas(tmp_path: Path) -> None:
    """Una sonda que gestiona su propio `PresupuestoAgotado` no engaña al runner."""

    async def _traga_el_error(ctx: ContextoSonda) -> ProbeResult:
        try:
            ctx.registrar_uso(entrada=600_000, salida=0)
        except PresupuestoAgotado:
            pass
        return ProbeResult(probe="tragona", ok=True, valor=1.0)

    registro: list[str] = []
    sondas = [
        Sonda(nombre="tragona", ejecutar=_traga_el_error),
        sonda_trivial("siguiente", registro=registro),
    ]
    res = await ejecutar(
        sondas,
        opciones(
            tmp_path,
            presupuesto_usd=0.5,
            precio_entrada_usd_mtok=1.0,
            precio_salida_usd_mtok=1.0,
        ),
        capturar_sigint=False,
    )
    assert registro == []
    assert res.corte == "presupuesto"
    assert res.pendientes == ["siguiente"]


# --------------------------------------------------------------------------- #
# Cancelación y reloj
# --------------------------------------------------------------------------- #


async def test_cancelacion_interrumpe_la_sonda_en_curso(tmp_path: Path) -> None:
    cancelacion = asyncio.Event()
    registro: list[str] = []

    async def _lenta(ctx: ContextoSonda) -> ProbeResult:
        registro.append("lenta")
        cancelacion.set()  # equivale al Ctrl-C del operador
        await asyncio.sleep(30)
        return ProbeResult(probe="lenta", ok=True, valor=1.0)

    sondas = [Sonda(nombre="lenta", ejecutar=_lenta), sonda_trivial("nunca", registro=registro)]
    res = await ejecutar(sondas, opciones(tmp_path), cancelacion=cancelacion, capturar_sigint=False)

    assert registro == ["lenta"]
    assert res.corte == "cancelacion"
    assert res.pendientes == ["nunca"]
    lenta = next(r for r in res.modelcard.resultados if r.probe == "lenta")
    assert lenta.ok is False


async def test_cancelacion_previa_no_ejecuta_nada(tmp_path: Path) -> None:
    cancelacion = asyncio.Event()
    cancelacion.set()
    registro: list[str] = []
    res = await ejecutar(
        [sonda_trivial("a", registro=registro)],
        opciones(tmp_path),
        cancelacion=cancelacion,
        capturar_sigint=False,
    )
    assert registro == []
    assert res.corte == "cancelacion"


async def test_capturar_sigint_no_estorba_una_ejecucion_normal(tmp_path: Path) -> None:
    """El manejador de Ctrl-C se instala y se retira sin efectos colaterales."""
    res = await ejecutar([sonda_trivial("a")], opciones(tmp_path), capturar_sigint=True)
    assert res.completa is True


async def test_deadline_corta_antes_de_arrancar_la_siguiente(tmp_path: Path) -> None:
    registro: list[str] = []

    async def _tarda(ctx: ContextoSonda) -> ProbeResult:
        registro.append("tarda")
        await asyncio.sleep(0.05)
        return ProbeResult(probe="tarda", ok=True, valor=1.0)

    sondas = [Sonda(nombre="tarda", ejecutar=_tarda), sonda_trivial("nunca", registro=registro)]
    res = await ejecutar(sondas, opciones(tmp_path, deadline_s=0.04), capturar_sigint=False)
    assert res.corte == "deadline"
    assert res.pendientes == ["nunca"]
    assert registro == ["tarda"]


async def test_timeout_de_sonda_marca_fallo_y_corta(tmp_path: Path) -> None:
    async def _colgada(ctx: ContextoSonda) -> ProbeResult:
        await asyncio.sleep(30)
        return ProbeResult(probe="colgada", ok=True)

    res = await ejecutar(
        [Sonda(nombre="colgada", ejecutar=_colgada)],
        opciones(tmp_path, timeout_sonda_s=0.05),
        capturar_sigint=False,
    )
    assert res.corte == "deadline"
    colgada = res.modelcard.resultados[0]
    assert colgada.ok is False and "expiró" in (colgada.error or "")


# --------------------------------------------------------------------------- #
# Reanudación
# --------------------------------------------------------------------------- #


async def test_reanudar_no_repite_lo_ya_medido(tmp_path: Path) -> None:
    registro: list[str] = []
    sondas = [sonda_trivial("a", registro=registro), sonda_trivial("b", registro=registro)]
    await ejecutar(sondas, opciones(tmp_path), capturar_sigint=False)
    registro.clear()

    res = await ejecutar(sondas, opciones(tmp_path), capturar_sigint=False)
    assert registro == []
    assert res.reutilizadas == ["a", "b"]
    assert res.ejecutadas == []
    assert res.modelcard.resultados[0].valor == 1.0


async def test_rehacer_vuelve_a_medir(tmp_path: Path) -> None:
    registro: list[str] = []
    sondas = [sonda_trivial("a", registro=registro)]
    await ejecutar(sondas, opciones(tmp_path), capturar_sigint=False)
    registro.clear()

    res = await ejecutar(sondas, opciones(tmp_path, rehacer=True), capturar_sigint=False)
    assert registro == ["a"]
    assert res.reutilizadas == []


async def test_reanudar_ignora_evidencia_de_otra_revision_o_modelo(tmp_path: Path) -> None:
    """Dos ModelCards de revisiones distintas no son comparables: no se mezclan."""
    registro: list[str] = []
    sondas = [sonda_trivial("a", registro=registro)]
    await ejecutar(sondas, opciones(tmp_path), capturar_sigint=False)

    registro.clear()
    res = await ejecutar(sondas, opciones(tmp_path, revision_sonda="rev2"), capturar_sigint=False)
    assert registro == ["a"], "una revisión distinta obliga a volver a medir"

    registro.clear()
    res = await ejecutar(sondas, opciones(tmp_path, modelo="otro-modelo"), capturar_sigint=False)
    assert registro == ["a"], "otro modelo obliga a volver a medir"
    assert res.reutilizadas == []


def test_evidencia_corrupta_se_ignora_sin_reventar(tmp_path: Path) -> None:
    carpeta = tmp_path / "evidencia" / "resultados"
    carpeta.mkdir(parents=True)
    (carpeta / "roto.json").write_text("{no soy json", encoding="utf-8")
    assert leer_resultados_guardados(tmp_path / "evidencia") == {}


def test_guardar_resultado_sanea_el_nombre_de_archivo(tmp_path: Path) -> None:
    ruta = guardar_resultado(
        tmp_path,
        ProbeResult(probe="tools/code blob", ok=True),
        modelo="m",
        proveedor="p",
        revision="r",
    )
    assert ruta.name == "tools_code_blob.json"
    assert "tools/code blob" in leer_resultados_guardados(tmp_path)


async def test_la_evidencia_de_la_sonda_va_a_su_carpeta(tmp_path: Path) -> None:
    async def _con_traza(ctx: ContextoSonda) -> ProbeResult:
        ruta = ctx.guardar_evidencia("peticion.json", {"messages": []})
        return ProbeResult(probe="con_traza", ok=True, valor=1.0, evidencia=[ruta])

    res = await ejecutar(
        [Sonda(nombre="con_traza", ejecutar=_con_traza)], opciones(tmp_path), capturar_sigint=False
    )
    traza = Path(res.modelcard.resultados[0].evidencia[0])
    assert traza.is_file()
    assert traza.parent.name == "con_traza"


# --------------------------------------------------------------------------- #
# Composición de la ModelCard
# --------------------------------------------------------------------------- #


def _card(resultados: list[ProbeResult]) -> object:
    return componer_modelcard(
        resultados,
        modelo="m",
        proveedor="p",
        revision_sonda="r",
        medido_en=datetime(2026, 7, 27, tzinfo=UTC),
    )


def test_lo_que_no_se_mide_queda_en_none() -> None:
    card = _card([ProbeResult(probe="a", ok=True, valor=1.0)])
    assert card.usable_context_tokens is None
    assert card.throughput_tps is None
    assert card.native_tools == {}
    evaluaciones = card.evaluar_umbrales()
    assert all(e.veredicto is Veredicto.SIN_DATO for e in evaluaciones)
    assert card.go() is False


def test_una_sonda_fallida_no_aporta_nada() -> None:
    card = _card(
        [
            ProbeResult(
                probe="contexto",
                ok=False,
                error="timeout",
                detalle={"usable_context_tokens": 200_000},
            )
        ]
    )
    assert card.usable_context_tokens is None


def test_composicion_por_capability_y_por_detalle() -> None:
    card = _card(
        [
            ProbeResult(
                probe="tools.code_blob",
                ok=True,
                capability=Capability.NATIVE_TOOLS,
                reliability=Reliability(successes=19, trials=20),
                detalle={"arg_profile": "code_blob"},
            ),
            ProbeResult(probe="contexto", ok=True, detalle={"usable_context_tokens": 60_000}),
            ProbeResult(
                probe="ttft.frio", ok=True, latencia=Latencia(p50=0.8, p95=1.4, muestras=20)
            ),
            ProbeResult(
                probe="cache",
                ok=True,
                capability=Capability.PREFIX_CACHE,
                valor=1,
            ),
        ]
    )
    assert card.native_tools[ArgProfile.CODE_BLOB].trials == 20
    assert card.usable_context_tokens == 60_000
    assert card.ttft is not None and card.ttft.p95 == 1.4
    assert card.prefix_cache is True


def test_publicacion_explicita_via_detalle_modelcard() -> None:
    card = _card(
        [
            ProbeResult(
                probe="banco",
                ok=True,
                detalle={
                    "modelcard": {
                        "bench_success": {"successes": 12, "trials": 20},
                        "throughput_tps": 31.5,
                        "native_tools": {"scalar": {"successes": 20, "trials": 20}},
                    }
                },
            )
        ]
    )
    assert card.bench_success is not None and card.bench_success.trials == 20
    assert card.throughput_tps == 31.5
    assert card.native_tools[ArgProfile.SCALAR].mean == 1.0


def test_los_conflictos_se_anotan_en_vez_de_resolverse_a_escondidas() -> None:
    card = _card(
        [
            ProbeResult(probe="uno", ok=True, detalle={"throughput_tps": 30.0}),
            ProbeResult(probe="dos", ok=True, detalle={"throughput_tps": 12.0}),
        ]
    )
    assert card.throughput_tps == 30.0
    assert any("throughput_tps" in n for n in card.notas)


def test_un_arg_profile_desconocido_no_se_traga_en_silencio() -> None:
    card = _card(
        [
            ProbeResult(
                probe="tools.raro",
                ok=True,
                capability=Capability.NATIVE_TOOLS,
                reliability=Reliability(successes=1, trials=1),
                detalle={"arg_profile": "inventado"},
            )
        ]
    )
    assert card.native_tools == {}
    assert any("inventado" in n for n in card.notas)


def test_go_exige_todos_los_umbrales_bloqueantes() -> None:
    card = _card(
        [
            ProbeResult(
                probe="todo",
                ok=True,
                detalle={
                    "modelcard": {
                        "usable_context_tokens": 120_000,
                        "throughput_tps": 40.0,
                        "max_tools_effective": 16,
                        "bench_success": {"successes": 19, "trials": 20},
                        "native_tools": {"code_blob": {"successes": 200, "trials": 200}},
                        "ttft": {"p50": 0.5, "p95": 1.0, "muestras": 20},
                    }
                },
            )
        ]
    )
    assert card.go() is True
