"""Tests de `edecan_advisory.salud`: `registrar_salud`, `resumen_salud` (con
rachas de hábitos y reloj fijo) y `analizar_laboratorio` (con regex de
analitos sobre un texto fixture)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import edecan_advisory.salud as salud_modulo
from edecan_advisory._disclaimers import DISCLAIMER_SALUD
from edecan_advisory.salud import AnalizarLaboratorioTool, RegistrarSaludTool, ResumenSaludTool

# ---------------------------------------------------------------------------
# registrar_salud
# ---------------------------------------------------------------------------


async def test_registrar_salud_kind_invalido(make_ctx):
    args = {"kind": "diagnostico", "valor": {"x": 1}}
    resultado = await RegistrarSaludTool().run(make_ctx(), args)
    assert "no es un tipo de registro válido" in resultado.content


async def test_registrar_salud_valor_vacio(make_ctx):
    resultado = await RegistrarSaludTool().run(make_ctx(), {"kind": "agua", "valor": {}})
    assert "no puede estar vacío" in resultado.content


async def test_registrar_salud_valor_no_es_dict(make_ctx):
    resultado = await RegistrarSaludTool().run(make_ctx(), {"kind": "agua", "valor": "500ml"})
    assert "debe ser un objeto" in resultado.content


async def test_registrar_salud_inserta_y_confirma(make_ctx, make_session):
    session = make_session([[{"id": uuid4()}]])
    ctx = make_ctx(session=session)

    resultado = await RegistrarSaludTool().run(
        ctx,
        {
            "kind": "ejercicio",
            "valor": {"cantidad": 30, "unidad": "min"},
            "notas": "corrí en el parque",
        },
    )

    assert "ejercicio" in resultado.content
    assert resultado.content.endswith(DISCLAIMER_SALUD)

    sql, params = session.llamadas[0]
    assert "INSERT INTO health_logs" in sql
    assert params["kind"] == "ejercicio"
    assert params["notas"] == "corrí en el parque"
    assert json.loads(params["valor"]) == {"cantidad": 30, "unidad": "min"}


# ---------------------------------------------------------------------------
# resumen_salud
# ---------------------------------------------------------------------------


async def test_resumen_salud_sin_registros(make_ctx, make_session):
    ctx = make_ctx(session=make_session([[]]))
    resultado = await ResumenSaludTool().run(ctx, {})
    assert "No hay registros de salud" in resultado.content
    assert resultado.data["por_kind"] == {}


async def test_resumen_salud_desde_dias_se_acota_al_maximo(make_ctx, make_session):
    session = make_session([[]])
    ctx = make_ctx(session=session)

    await ResumenSaludTool().run(ctx, {"desde_dias": 9999})

    _sql, params = session.llamadas[0]
    assert (params["hasta"] - params["desde"]) <= timedelta(days=90)


async def test_resumen_salud_racha_de_dias_consecutivos_con_reloj_fijo(
    make_ctx, make_session, monkeypatch
):
    monkeypatch.setattr(salud_modulo, "_ahora", lambda: datetime(2026, 1, 10, 9, 0, tzinfo=UTC))

    filas = [
        {"kind": "habito", "valor": {}, "registrado_en": datetime(2026, 1, 8, 7, 0, tzinfo=UTC)},
        {"kind": "habito", "valor": {}, "registrado_en": datetime(2026, 1, 9, 7, 0, tzinfo=UTC)},
        {"kind": "habito", "valor": {}, "registrado_en": datetime(2026, 1, 10, 7, 0, tzinfo=UTC)},
    ]
    ctx = make_ctx(session=make_session([filas]))

    resultado = await ResumenSaludTool().run(ctx, {"desde_dias": 7})

    assert resultado.data["por_kind"]["habito"]["racha_dias"] == 3
    assert resultado.data["por_kind"]["habito"]["conteo"] == 3
    assert "racha de 3 día(s) consecutivos" in resultado.content


async def test_resumen_salud_racha_se_corta_por_hueco_con_reloj_fijo(
    make_ctx, make_session, monkeypatch
):
    monkeypatch.setattr(salud_modulo, "_ahora", lambda: datetime(2026, 1, 10, 9, 0, tzinfo=UTC))

    filas = [
        # 5 de enero: hueco (no hay registro el 6, 7 ni 8) — la racha no
        # debe "saltar" por encima de él.
        {"kind": "habito", "valor": {}, "registrado_en": datetime(2026, 1, 5, 7, 0, tzinfo=UTC)},
        {"kind": "habito", "valor": {}, "registrado_en": datetime(2026, 1, 9, 7, 0, tzinfo=UTC)},
        {"kind": "habito", "valor": {}, "registrado_en": datetime(2026, 1, 10, 7, 0, tzinfo=UTC)},
    ]
    ctx = make_ctx(session=make_session([filas]))

    resultado = await ResumenSaludTool().run(ctx, {"desde_dias": 10})

    assert resultado.data["por_kind"]["habito"]["racha_dias"] == 2
    assert resultado.data["por_kind"]["habito"]["conteo"] == 3


async def test_resumen_salud_mismo_dia_cuenta_un_solo_dia_de_racha_y_suma_cantidad(
    make_ctx, make_session, monkeypatch
):
    monkeypatch.setattr(salud_modulo, "_ahora", lambda: datetime(2026, 1, 10, 20, 0, tzinfo=UTC))

    filas = [
        {
            "kind": "agua",
            "valor": {"cantidad": 250},
            "registrado_en": datetime(2026, 1, 10, 7, 0, tzinfo=UTC),
        },
        {
            "kind": "agua",
            "valor": {"cantidad": 250},
            "registrado_en": datetime(2026, 1, 10, 15, 0, tzinfo=UTC),
        },
    ]
    ctx = make_ctx(session=make_session([filas]))

    resultado = await ResumenSaludTool().run(ctx, {})

    agg = resultado.data["por_kind"]["agua"]
    assert agg["conteo"] == 2
    assert agg["racha_dias"] == 1
    assert agg["suma_cantidad"] == 500.0
    assert "total cantidad=500" in resultado.content


async def test_resumen_salud_sin_cantidad_numerica_suma_es_none(
    make_ctx, make_session, monkeypatch
):
    monkeypatch.setattr(salud_modulo, "_ahora", lambda: datetime(2026, 1, 10, 9, 0, tzinfo=UTC))
    filas = [
        {
            "kind": "medicamento",
            "valor": {"nombre": "ibuprofeno", "dosis": "400mg"},
            "registrado_en": datetime(2026, 1, 10, 7, 0, tzinfo=UTC),
        }
    ]
    ctx = make_ctx(session=make_session([filas]))

    resultado = await ResumenSaludTool().run(ctx, {})

    assert resultado.data["por_kind"]["medicamento"]["suma_cantidad"] is None


# ---------------------------------------------------------------------------
# analizar_laboratorio
# ---------------------------------------------------------------------------

_TEXTO_LABORATORIO = (
    "REPORTE DE LABORATORIO\n"
    "Paciente: Juan Perez\n"
    "\n"
    "Glucosa 95 mg/dL\n"
    "Colesterol total: 180 mg/dL\n"
    "Hemoglobina 14.2 g/dL\n"
    "Comentario: paciente en ayunas\n"
)


async def test_analizar_laboratorio_file_id_invalido(make_ctx):
    resultado = await AnalizarLaboratorioTool().run(make_ctx(), {"file_id": "no-es-uuid"})
    assert "identificador válido" in resultado.content


async def test_analizar_laboratorio_archivo_no_encontrado(make_ctx, fake_texto):
    resultado = await AnalizarLaboratorioTool().run(make_ctx(), {"file_id": str(uuid4())})
    assert "No encontré ese archivo" in resultado.content


async def test_analizar_laboratorio_formato_no_soportado(make_ctx, make_archivo, fake_texto):
    fake_texto.archivos = [
        make_archivo(contenido=b"\x89PNG\r\n", filename="foto.png", mime="image/png")
    ]
    resultado = await AnalizarLaboratorioTool().run(make_ctx(), {"file_id": str(uuid4())})
    assert "no es un formato soportado" in resultado.content


async def test_analizar_laboratorio_sin_analitos_detectados(make_ctx, make_archivo, fake_texto):
    contenido = b"Sin datos numericos en este documento.\n"
    fake_texto.archivos = [make_archivo(contenido=contenido, filename="lab.txt", mime="text/plain")]
    resultado = await AnalizarLaboratorioTool().run(make_ctx(), {"file_id": str(uuid4())})
    assert "No detecté analitos" in resultado.content
    assert resultado.data["analitos"] == []


async def test_analizar_laboratorio_detecta_analitos_por_regex_sobre_fixture(
    make_ctx, make_llm, make_archivo, fake_texto
):
    fake_texto.archivos = [
        make_archivo(contenido=_TEXTO_LABORATORIO.encode(), filename="lab.txt", mime="text/plain")
    ]
    explicacion = "La glucosa mide el azúcar en sangre; el colesterol, las grasas circulantes."
    ctx = make_ctx(llm=make_llm(texto=explicacion))

    resultado = await AnalizarLaboratorioTool().run(ctx, {"file_id": str(uuid4())})

    analitos = resultado.data["analitos"]
    nombres = [a["nombre"] for a in analitos]
    assert nombres == ["Glucosa", "Colesterol total", "Hemoglobina"]

    por_nombre = {a["nombre"]: a for a in analitos}
    assert por_nombre["Glucosa"] == {"nombre": "Glucosa", "valor": 95.0, "unidad": "mg/dL"}
    assert por_nombre["Hemoglobina"] == {"nombre": "Hemoglobina", "valor": 14.2, "unidad": "g/dL"}

    # Ni el título ni las líneas sin números (nombre de paciente, comentario)
    # se confunden con un analito — el regex exige un número en la línea.
    assert "Juan Perez" not in nombres
    assert not any("REPORTE" in n for n in nombres)

    # Advertencia reforzada ANTES del disclaimer estándar, que sigue yendo al final.
    assert "IMPORTANTE" in resultado.content
    assert resultado.content.index("IMPORTANTE") < resultado.content.index(DISCLAIMER_SALUD)
    assert resultado.content.endswith(DISCLAIMER_SALUD)
