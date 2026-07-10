"""Tests de `edecan_business.rrhh` — empleados (crear/editar/desactivar/listar), ausencias
(registrar/listar/resolver) y nómina en borrador (calcular/listar/obtener/aprobar/cancelar).
Offline y determinista: `FakeSession` programable (`conftest.py`), nunca toca Postgres real.

Contrato de columnas EXACTO (`ARCHITECTURE.md` §14.b, migración `0007_v5_expansion`, ya
aterrizada): `employees.salario_mensual` (no "salario"); `time_off` SIN `user_id` ni
`approved_at`, con `notas` (no "nota"); `payroll_runs.total` es una ÚNICA columna (el neto),
SIN `deducciones_pct` propia — ver el docstring de `edecan_business.rrhh` para el detalle.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from edecan_business.rrhh import (
    AusenciaSolapadaError,
    EstadoAusenciaError,
    EstadoNominaError,
    aprobar_nomina,
    calcular_nomina,
    cancelar_nomina,
    crear_empleado,
    desactivar_empleado,
    editar_empleado,
    listar_ausencias,
    listar_empleados,
    listar_nominas,
    obtener_empleado,
    obtener_empleado_por_nombre,
    obtener_nomina,
    registrar_ausencia,
    resolver_ausencia,
)


def _empleado_row(**overrides):
    base = {
        "id": uuid4(),
        "nombre": "Ana Pérez",
        "email": None,
        "puesto": "",
        "salario_mensual": None,
        "moneda": "USD",
        "fecha_ingreso": None,
        "status": "active",
    }
    base.update(overrides)
    return base


def _ausencia_row(**overrides):
    base = {
        "id": uuid4(),
        "employee_id": uuid4(),
        "kind": "vacaciones",
        "desde": "2026-08-01",
        "hasta": "2026-08-05",
        "status": "pending",
        "notas": "",
    }
    base.update(overrides)
    return base


def _payroll_run_row(**overrides):
    base = {
        "id": uuid4(),
        "periodo": "2026-07",
        "status": "draft",
        "total": Decimal("0.00"),
        "moneda": "USD",
        "notas": "",
        "approved_at": None,
    }
    base.update(overrides)
    return base


def _item_row(**overrides):
    base = {
        "id": uuid4(),
        "employee_id": uuid4(),
        "bruto": Decimal("0.00"),
        "deducciones": Decimal("0.00"),
        "neto": Decimal("0.00"),
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# crear_empleado
# ---------------------------------------------------------------------------


async def test_crear_empleado_happy_path(make_session):
    fila = _empleado_row(nombre="Ana Pérez")
    session = make_session([[fila]])
    empleado = await crear_empleado(
        session, tenant_id=uuid4(), user_id=uuid4(), nombre="  Ana Pérez  "
    )
    assert empleado["nombre"] == "Ana Pérez"
    _, params_insert = session.llamadas[0]
    assert params_insert["nombre"] == "Ana Pérez"
    assert params_insert["status"] == "active"
    assert params_insert["salario_mensual"] is None
    assert params_insert["email"] is None
    assert params_insert["moneda"] == "USD"
    assert params_insert["fecha_ingreso"] is None


async def test_crear_empleado_nombre_vacio_rechaza_sin_tocar_sesion(make_session):
    session = make_session()
    with pytest.raises(ValueError, match="nombre"):
        await crear_empleado(session, tenant_id=uuid4(), user_id=uuid4(), nombre="   ")
    assert session.llamadas == []


async def test_crear_empleado_email_malformado_rechaza_sin_tocar_sesion(make_session):
    session = make_session()
    with pytest.raises(ValueError, match="email"):
        await crear_empleado(
            session, tenant_id=uuid4(), user_id=uuid4(), nombre="Ana", email="no-es-un-email"
        )
    assert session.llamadas == []


async def test_crear_empleado_email_bien_formado_se_normaliza(make_session):
    fila = _empleado_row(email="ana@empresa.com")
    session = make_session([[fila]])
    await crear_empleado(
        session,
        tenant_id=uuid4(),
        user_id=uuid4(),
        nombre="Ana",
        email="  ANA@empresa.com  ".strip(),
    )
    _, params_insert = session.llamadas[0]
    assert params_insert["email"] == "ANA@empresa.com"


async def test_crear_empleado_salario_negativo_rechaza(make_session):
    session = make_session()
    with pytest.raises(ValueError, match="salario_mensual"):
        await crear_empleado(
            session, tenant_id=uuid4(), user_id=uuid4(), nombre="Ana", salario_mensual=-1
        )
    assert session.llamadas == []


async def test_crear_empleado_salario_no_numerico_rechaza(make_session):
    session = make_session()
    with pytest.raises(ValueError, match="salario_mensual"):
        await crear_empleado(
            session,
            tenant_id=uuid4(),
            user_id=uuid4(),
            nombre="Ana",
            salario_mensual="no-es-un-numero",
        )
    assert session.llamadas == []


async def test_crear_empleado_status_invalido_rechaza(make_session):
    session = make_session()
    with pytest.raises(ValueError, match="status"):
        await crear_empleado(
            session, tenant_id=uuid4(), user_id=uuid4(), nombre="Ana", status="jubilado"
        )
    assert session.llamadas == []


async def test_crear_empleado_salario_decimal_se_preserva(make_session):
    fila = _empleado_row(salario_mensual=Decimal("1500.50"))
    session = make_session([[fila]])
    empleado = await crear_empleado(
        session, tenant_id=uuid4(), user_id=uuid4(), nombre="Ana", salario_mensual="1500.50"
    )
    assert empleado["salario_mensual"] == Decimal("1500.50")
    _, params_insert = session.llamadas[0]
    assert params_insert["salario_mensual"] == Decimal("1500.50")


async def test_crear_empleado_moneda_invalida_cae_a_default(make_session):
    fila = _empleado_row(moneda="USD")
    session = make_session([[fila]])
    await crear_empleado(
        session, tenant_id=uuid4(), user_id=uuid4(), nombre="Ana", moneda="dólares"
    )
    _, params_insert = session.llamadas[0]
    assert params_insert["moneda"] == "USD"


async def test_crear_empleado_fecha_ingreso_se_parsea(make_session):
    fila = _empleado_row()
    session = make_session([[fila]])
    await crear_empleado(
        session, tenant_id=uuid4(), user_id=uuid4(), nombre="Ana", fecha_ingreso="2026-01-15"
    )
    _, params_insert = session.llamadas[0]
    from datetime import date

    assert params_insert["fecha_ingreso"] == date(2026, 1, 15)


async def test_crear_empleado_flush_una_vez(make_session):
    fila = _empleado_row()
    session = make_session([[fila]])
    await crear_empleado(session, tenant_id=uuid4(), user_id=uuid4(), nombre="Ana")
    assert session.flushes == 1


# ---------------------------------------------------------------------------
# editar_empleado
# ---------------------------------------------------------------------------


async def test_editar_empleado_solo_toca_los_campos_presentes(make_session):
    fila = _empleado_row(nombre="Ana Actualizada")
    session = make_session([[fila]])
    await editar_empleado(session, tenant_id=uuid4(), employee_id=uuid4(), nombre="Ana Actualizada")
    sql, params = session.llamadas[0]
    assert "nombre = :nombre" in sql
    assert "email = :email" not in sql
    assert "salario_mensual = :salario_mensual" not in sql
    assert "updated_at = now()" in sql
    assert params["nombre"] == "Ana Actualizada"


async def test_editar_empleado_sin_cambios_igual_ejecuta_un_update(make_session):
    fila = _empleado_row()
    session = make_session([[fila]])
    resultado = await editar_empleado(session, tenant_id=uuid4(), employee_id=uuid4())
    assert resultado is not None
    sql, _ = session.llamadas[0]
    assert sql.count("SET") == 1
    assert "updated_at = now()" in sql


async def test_editar_empleado_nombre_vacio_rechaza(make_session):
    session = make_session()
    with pytest.raises(ValueError, match="nombre"):
        await editar_empleado(session, tenant_id=uuid4(), employee_id=uuid4(), nombre="   ")
    assert session.llamadas == []


async def test_editar_empleado_email_malformado_rechaza(make_session):
    session = make_session()
    with pytest.raises(ValueError, match="email"):
        await editar_empleado(session, tenant_id=uuid4(), employee_id=uuid4(), email="malo@")
    assert session.llamadas == []


async def test_editar_empleado_email_none_limpia_el_campo(make_session):
    """`email` es nullable: pasar explícitamente `None` debe LIMPIAR el campo, no ser tratado
    como "campo no presente" — esa distinción ya la resuelve el llamador."""
    fila = _empleado_row(email=None)
    session = make_session([[fila]])
    await editar_empleado(session, tenant_id=uuid4(), employee_id=uuid4(), email=None)
    sql, params = session.llamadas[0]
    assert "email = :email" in sql
    assert params["email"] is None


async def test_editar_empleado_salario_negativo_rechaza(make_session):
    session = make_session()
    with pytest.raises(ValueError, match="salario_mensual"):
        await editar_empleado(session, tenant_id=uuid4(), employee_id=uuid4(), salario_mensual=-5)
    assert session.llamadas == []


async def test_editar_empleado_status_invalido_rechaza(make_session):
    session = make_session()
    with pytest.raises(ValueError, match="status"):
        await editar_empleado(session, tenant_id=uuid4(), employee_id=uuid4(), status="volando")
    assert session.llamadas == []


async def test_editar_empleado_fecha_ingreso_none_limpia_el_campo(make_session):
    fila = _empleado_row(fecha_ingreso=None)
    session = make_session([[fila]])
    await editar_empleado(session, tenant_id=uuid4(), employee_id=uuid4(), fecha_ingreso=None)
    sql, params = session.llamadas[0]
    assert "fecha_ingreso = :fecha_ingreso" in sql
    assert params["fecha_ingreso"] is None


async def test_editar_empleado_no_existente_retorna_none(make_session):
    session = make_session([[]])
    resultado = await editar_empleado(session, tenant_id=uuid4(), employee_id=uuid4(), nombre="X")
    assert resultado is None


async def test_editar_empleado_ignora_claves_desconocidas(make_session):
    fila = _empleado_row()
    session = make_session([[fila]])
    await editar_empleado(session, tenant_id=uuid4(), employee_id=uuid4(), sku="OTRO")
    sql, params = session.llamadas[0]
    assert "sku" not in params
    assert sql.count("SET") == 1  # solo tocó updated_at


# ---------------------------------------------------------------------------
# desactivar_empleado
# ---------------------------------------------------------------------------


async def test_desactivar_empleado_setea_status_inactive(make_session):
    fila = _empleado_row(status="inactive")
    session = make_session([[fila]])
    resultado = await desactivar_empleado(session, tenant_id=uuid4(), employee_id=uuid4())
    assert resultado["status"] == "inactive"
    _, params = session.llamadas[0]
    assert params["status"] == "inactive"


async def test_desactivar_empleado_no_existente_retorna_none(make_session):
    session = make_session([[]])
    resultado = await desactivar_empleado(session, tenant_id=uuid4(), employee_id=uuid4())
    assert resultado is None


# ---------------------------------------------------------------------------
# listar_empleados / obtener_empleado / obtener_empleado_por_nombre
# ---------------------------------------------------------------------------


async def test_listar_empleados_sin_filtro(make_session):
    filas = [_empleado_row(nombre="A"), _empleado_row(nombre="B")]
    session = make_session([filas])
    empleados = await listar_empleados(session, tenant_id=uuid4())
    assert len(empleados) == 2
    _, params = session.llamadas[0]
    assert "status" not in params
    assert "patron" not in params


async def test_listar_empleados_filtra_por_status(make_session):
    session = make_session([[_empleado_row(status="inactive")]])
    await listar_empleados(session, tenant_id=uuid4(), status="inactive")
    sql, params = session.llamadas[0]
    assert "status = :status" in sql
    assert params["status"] == "inactive"


async def test_listar_empleados_status_invalido_rechaza(make_session):
    session = make_session()
    with pytest.raises(ValueError, match="status"):
        await listar_empleados(session, tenant_id=uuid4(), status="jubilado")
    assert session.llamadas == []


async def test_listar_empleados_filtra_por_texto(make_session):
    session = make_session([[_empleado_row()]])
    await listar_empleados(session, tenant_id=uuid4(), q="ana")
    sql, params = session.llamadas[0]
    assert "nombre ILIKE :patron" in sql
    assert "email ILIKE :patron" in sql
    assert "puesto ILIKE :patron" in sql
    assert params["patron"] == "%ana%"


async def test_obtener_empleado_por_nombre_case_insensitive(make_session):
    fila = _empleado_row(nombre="Ana Pérez")
    session = make_session([[fila]])
    empleado = await obtener_empleado_por_nombre(session, tenant_id=uuid4(), nombre="  ana pérez ")
    assert empleado["nombre"] == "Ana Pérez"
    _, params = session.llamadas[0]
    assert params["nombre"] == "ana pérez"


async def test_obtener_empleado_por_nombre_no_encontrado_retorna_none(make_session):
    session = make_session([[]])
    empleado = await obtener_empleado_por_nombre(session, tenant_id=uuid4(), nombre="Nadie")
    assert empleado is None


async def test_obtener_empleado_no_encontrado_retorna_none(make_session):
    session = make_session([[]])
    empleado = await obtener_empleado(session, tenant_id=uuid4(), employee_id=uuid4())
    assert empleado is None


# ---------------------------------------------------------------------------
# registrar_ausencia
# ---------------------------------------------------------------------------


async def test_registrar_ausencia_happy_path(make_session):
    employee_id = uuid4()
    fila = _ausencia_row(employee_id=employee_id)
    # 1) SELECT empleado existe  2) SELECT solapamiento (vacío)  3) INSERT time_off
    session = make_session([[{"id": employee_id}], [], [fila]])
    ausencia = await registrar_ausencia(
        session,
        tenant_id=uuid4(),
        employee_id=employee_id,
        kind="vacaciones",
        desde="2026-08-01",
        hasta="2026-08-05",
    )
    assert ausencia["status"] == "pending"
    sql_insert, params_insert = session.llamadas[2]
    assert "INSERT INTO time_off" in sql_insert
    assert "user_id" not in params_insert  # `time_off` no tiene esa columna
    assert params_insert["kind"] == "vacaciones"
    assert params_insert["notas"] == ""


async def test_registrar_ausencia_notas_se_guardan(make_session):
    employee_id = uuid4()
    fila = _ausencia_row(employee_id=employee_id, notas="pidió salir temprano")
    session = make_session([[{"id": employee_id}], [], [fila]])
    await registrar_ausencia(
        session,
        tenant_id=uuid4(),
        employee_id=employee_id,
        kind="permiso",
        desde="2026-08-01",
        hasta="2026-08-01",
        notas="pidió salir temprano",
    )
    _, params_insert = session.llamadas[2]
    assert params_insert["notas"] == "pidió salir temprano"


async def test_registrar_ausencia_kind_invalido_rechaza_sin_tocar_sesion(make_session):
    session = make_session()
    with pytest.raises(ValueError, match="kind"):
        await registrar_ausencia(
            session,
            tenant_id=uuid4(),
            employee_id=uuid4(),
            kind="siesta",
            desde="2026-08-01",
            hasta="2026-08-05",
        )
    assert session.llamadas == []


async def test_registrar_ausencia_desde_posterior_a_hasta_rechaza(make_session):
    session = make_session()
    with pytest.raises(ValueError, match="desde"):
        await registrar_ausencia(
            session,
            tenant_id=uuid4(),
            employee_id=uuid4(),
            kind="vacaciones",
            desde="2026-08-10",
            hasta="2026-08-05",
        )
    assert session.llamadas == []


async def test_registrar_ausencia_fecha_invalida_rechaza(make_session):
    session = make_session()
    with pytest.raises(ValueError, match="fecha"):
        await registrar_ausencia(
            session,
            tenant_id=uuid4(),
            employee_id=uuid4(),
            kind="vacaciones",
            desde="no-es-una-fecha",
            hasta="2026-08-05",
        )
    assert session.llamadas == []


async def test_registrar_ausencia_empleado_inexistente_retorna_none(make_session):
    session = make_session([[]])  # SELECT empleado -> no encontrado
    ausencia = await registrar_ausencia(
        session,
        tenant_id=uuid4(),
        employee_id=uuid4(),
        kind="vacaciones",
        desde="2026-08-01",
        hasta="2026-08-05",
    )
    assert ausencia is None
    assert len(session.llamadas) == 1  # nunca llegó a chequear solapamiento ni insertar


async def test_registrar_ausencia_solapada_con_aprobada_rechaza(make_session):
    employee_id = uuid4()
    session = make_session([[{"id": employee_id}], [{"id": uuid4()}]])
    with pytest.raises(AusenciaSolapadaError, match="solapa"):
        await registrar_ausencia(
            session,
            tenant_id=uuid4(),
            employee_id=employee_id,
            kind="vacaciones",
            desde="2026-08-03",
            hasta="2026-08-04",
        )
    # nunca llegó al INSERT.
    assert len(session.llamadas) == 2
    sql_solapamiento, params_solapamiento = session.llamadas[1]
    assert "status = :status_aprobada" in sql_solapamiento
    assert params_solapamiento["status_aprobada"] == "approved"


async def test_registrar_ausencia_no_solapada_con_pending_pasa(make_session):
    """El chequeo de solapamiento solo mira ausencias `approved` — una `pending` que se
    solapa NO bloquea el registro (ver el docstring de `AusenciaSolapadaError`): el SELECT de
    solapamiento ya filtra por `status='approved'`, así que aunque en la base exista una
    `pending` solapada, esta consulta programada simplemente no la devuelve."""
    employee_id = uuid4()
    fila = _ausencia_row(employee_id=employee_id)
    session = make_session([[{"id": employee_id}], [], [fila]])
    ausencia = await registrar_ausencia(
        session,
        tenant_id=uuid4(),
        employee_id=employee_id,
        kind="vacaciones",
        desde="2026-08-01",
        hasta="2026-08-05",
    )
    assert ausencia is not None


# ---------------------------------------------------------------------------
# listar_ausencias
# ---------------------------------------------------------------------------


async def test_listar_ausencias_sin_filtro(make_session):
    filas = [_ausencia_row(), _ausencia_row()]
    session = make_session([filas])
    ausencias = await listar_ausencias(session, tenant_id=uuid4())
    assert len(ausencias) == 2


async def test_listar_ausencias_filtra_por_employee_id_y_status(make_session):
    employee_id = uuid4()
    session = make_session([[_ausencia_row(employee_id=employee_id)]])
    await listar_ausencias(session, tenant_id=uuid4(), employee_id=employee_id, status="pending")
    sql, params = session.llamadas[0]
    assert "employee_id = :employee_id" in sql
    assert "status = :status" in sql
    assert params["employee_id"] == str(employee_id)
    assert params["status"] == "pending"


async def test_listar_ausencias_acepta_status_cancelled(make_session):
    """`cancelled` es un valor válido del `CHECK` pinned aunque `resolver_ausencia` nunca lo
    produzca (ver el docstring del módulo) — filtrar por él no debe rechazarse."""
    session = make_session([[]])
    await listar_ausencias(session, tenant_id=uuid4(), status="cancelled")
    _, params = session.llamadas[0]
    assert params["status"] == "cancelled"


async def test_listar_ausencias_status_invalido_rechaza(make_session):
    session = make_session()
    with pytest.raises(ValueError, match="status"):
        await listar_ausencias(session, tenant_id=uuid4(), status="volando")
    assert session.llamadas == []


# ---------------------------------------------------------------------------
# resolver_ausencia
# ---------------------------------------------------------------------------


async def test_resolver_ausencia_aprobar(make_session):
    fila = _ausencia_row(status="approved")
    session = make_session([[{"status": "pending"}], [fila]])
    ausencia = await resolver_ausencia(
        session, tenant_id=uuid4(), time_off_id=uuid4(), aprobar=True
    )
    assert ausencia["status"] == "approved"
    sql_update, params_update = session.llamadas[1]
    assert "approved_at" not in sql_update  # `time_off` no tiene esa columna
    assert params_update["status"] == "approved"


async def test_resolver_ausencia_select_inicial_usa_for_update(make_session):
    """WP-V7-01: el `SELECT` que alimenta el chequeo de estado debe tomar el lock de fila
    (`FOR UPDATE`) ANTES de leer `status` — mismo criterio que
    `test_aprobar_nomina_select_inicial_usa_for_update` documenta para `_lock_payroll_run`.
    Sin este lock, dos llamadas concurrentes a `resolver_ausencia` sobre la MISMA ausencia
    (una `aprobar`, otra `rechazar`) podían ambas "tener éxito" sin que ninguna lanzara
    `EstadoAusenciaError` — carrera real reproducida y cerrada contra Postgres real (ver
    `packages/business/tests/test_rrhh_integration.py::
    test_resolver_ausencia_concurrente_se_serializa_con_for_update`)."""
    fila = _ausencia_row(status="approved")
    session = make_session([[{"status": "pending"}], [fila]])
    await resolver_ausencia(session, tenant_id=uuid4(), time_off_id=uuid4(), aprobar=True)
    sql_select, _ = session.llamadas[0]
    assert "FOR UPDATE" in sql_select


async def test_resolver_ausencia_rechazar(make_session):
    fila = _ausencia_row(status="rejected")
    session = make_session([[{"status": "pending"}], [fila]])
    ausencia = await resolver_ausencia(
        session, tenant_id=uuid4(), time_off_id=uuid4(), aprobar=False
    )
    assert ausencia["status"] == "rejected"
    sql_update, params_update = session.llamadas[1]
    assert params_update["status"] == "rejected"


async def test_resolver_ausencia_no_existente_retorna_none(make_session):
    session = make_session([[]])
    ausencia = await resolver_ausencia(
        session, tenant_id=uuid4(), time_off_id=uuid4(), aprobar=True
    )
    assert ausencia is None


async def test_resolver_ausencia_ya_resuelta_rechaza(make_session):
    session = make_session([[{"status": "approved"}]])
    with pytest.raises(EstadoAusenciaError, match="pending"):
        await resolver_ausencia(session, tenant_id=uuid4(), time_off_id=uuid4(), aprobar=True)
    assert len(session.llamadas) == 1  # nunca llegó al UPDATE


async def test_resolver_ausencia_cancelada_tambien_rechaza(make_session):
    """`cancelled` (fuera del alcance de `resolver_ausencia` en sí, ver el docstring del
    módulo) también cuenta como "ya no está pending" si de algún otro modo llegó a ese
    estado."""
    session = make_session([[{"status": "cancelled"}]])
    with pytest.raises(EstadoAusenciaError):
        await resolver_ausencia(session, tenant_id=uuid4(), time_off_id=uuid4(), aprobar=True)


# ---------------------------------------------------------------------------
# calcular_nomina
# ---------------------------------------------------------------------------


async def test_calcular_nomina_happy_path_sin_deducciones(make_session):
    e1, e2 = uuid4(), uuid4()
    empleados = [
        {"id": e1, "nombre": "Ana", "salario_mensual": Decimal("1000.00")},
        {"id": e2, "nombre": "Beto", "salario_mensual": Decimal("2000.00")},
    ]
    run_row = _payroll_run_row(total=Decimal("3000.00"))
    item1 = _item_row(employee_id=e1, bruto=Decimal("1000.00"), neto=Decimal("1000.00"))
    item2 = _item_row(employee_id=e2, bruto=Decimal("2000.00"), neto=Decimal("2000.00"))
    session = make_session([empleados, [run_row], [item1], [item2]])

    nomina = await calcular_nomina(session, tenant_id=uuid4(), user_id=uuid4(), periodo="2026-07")

    assert nomina["status"] == "draft"
    assert len(nomina["items"]) == 2
    assert nomina["items"][0]["empleado_nombre"] == "Ana"
    assert nomina["items"][1]["empleado_nombre"] == "Beto"
    assert nomina["total"] == Decimal("3000.00")
    assert nomina["total_bruto"] == Decimal("3000.00")
    assert nomina["total_deducciones"] == Decimal("0.00")

    _, params_run = session.llamadas[1]
    assert params_run["total"] == Decimal("3000.00")
    assert params_run["moneda"] == "USD"
    assert "deducciones_pct" not in params_run  # no existe esa columna en payroll_runs


async def test_calcular_nomina_aplica_deducciones_pct_global(make_session):
    e1 = uuid4()
    empleados = [{"id": e1, "nombre": "Ana", "salario_mensual": Decimal("1000.00")}]
    run_row = _payroll_run_row(total=Decimal("900.00"))
    item1 = _item_row(
        employee_id=e1,
        bruto=Decimal("1000.00"),
        deducciones=Decimal("100.00"),
        neto=Decimal("900.00"),
    )
    session = make_session([empleados, [run_row], [item1]])

    nomina = await calcular_nomina(
        session, tenant_id=uuid4(), user_id=uuid4(), periodo="2026-07", deducciones_pct=10
    )

    assert nomina["items"][0]["deducciones"] == Decimal("100.00")
    assert nomina["items"][0]["neto"] == Decimal("900.00")
    assert nomina["total"] == Decimal("900.00")
    assert nomina["total_deducciones"] == Decimal("100.00")


async def test_calcular_nomina_moneda_y_notas_se_guardan(make_session):
    run_row = _payroll_run_row(moneda="MXN", notas="quincena 2")
    session = make_session([[], [run_row]])
    nomina = await calcular_nomina(
        session,
        tenant_id=uuid4(),
        user_id=uuid4(),
        periodo="2026-07",
        moneda="mxn",
        notas="quincena 2",
    )
    assert nomina["moneda"] == "MXN"
    _, params_run = session.llamadas[1]
    assert params_run["moneda"] == "MXN"
    assert params_run["notas"] == "quincena 2"


async def test_calcular_nomina_sin_empleados_califican_crea_draft_vacio(make_session):
    run_row = _payroll_run_row()
    session = make_session([[], [run_row]])
    nomina = await calcular_nomina(session, tenant_id=uuid4(), user_id=uuid4(), periodo="2026-07")
    assert nomina["items"] == []
    assert nomina["status"] == "draft"
    assert nomina["total_bruto"] == Decimal("0")
    assert nomina["total_deducciones"] == Decimal("0")


async def test_calcular_nomina_periodo_invalido_rechaza_sin_tocar_sesion(make_session):
    session = make_session()
    with pytest.raises(ValueError, match="periodo"):
        await calcular_nomina(session, tenant_id=uuid4(), user_id=uuid4(), periodo="julio-2026")
    assert session.llamadas == []


async def test_calcular_nomina_deducciones_pct_fuera_de_rango_rechaza(make_session):
    session = make_session()
    with pytest.raises(ValueError, match="deducciones_pct"):
        await calcular_nomina(
            session, tenant_id=uuid4(), user_id=uuid4(), periodo="2026-07", deducciones_pct=75
        )
    assert session.llamadas == []


async def test_calcular_nomina_deducciones_pct_negativo_rechaza(make_session):
    session = make_session()
    with pytest.raises(ValueError, match="deducciones_pct"):
        await calcular_nomina(
            session, tenant_id=uuid4(), user_id=uuid4(), periodo="2026-07", deducciones_pct=-5
        )
    assert session.llamadas == []


async def test_calcular_nomina_query_empleados_filtra_activos_con_salario(make_session):
    run_row = _payroll_run_row()
    session = make_session([[], [run_row]])
    await calcular_nomina(session, tenant_id=uuid4(), user_id=uuid4(), periodo="2026-07")
    sql_empleados, _ = session.llamadas[0]
    assert "status = 'active'" in sql_empleados
    assert "salario_mensual IS NOT NULL" in sql_empleados


# ---------------------------------------------------------------------------
# listar_nominas / obtener_nomina
# ---------------------------------------------------------------------------


async def test_listar_nominas_sin_filtro(make_session):
    filas = [_payroll_run_row(), _payroll_run_row()]
    session = make_session([filas])
    nominas = await listar_nominas(session, tenant_id=uuid4())
    assert len(nominas) == 2


async def test_listar_nominas_filtra_por_status(make_session):
    session = make_session([[_payroll_run_row(status="approved")]])
    await listar_nominas(session, tenant_id=uuid4(), status="approved")
    sql, params = session.llamadas[0]
    assert "status = :status" in sql
    assert params["status"] == "approved"


async def test_listar_nominas_acepta_status_paid(make_session):
    """`paid` es un valor válido del `CHECK` pinned aunque este work package nunca lo escriba
    (ver el docstring del módulo) — filtrar por él no debe rechazarse."""
    session = make_session([[]])
    await listar_nominas(session, tenant_id=uuid4(), status="paid")
    _, params = session.llamadas[0]
    assert params["status"] == "paid"


async def test_listar_nominas_status_invalido_rechaza(make_session):
    session = make_session()
    with pytest.raises(ValueError, match="status"):
        await listar_nominas(session, tenant_id=uuid4(), status="pagada")
    assert session.llamadas == []


async def test_obtener_nomina_incluye_items_con_join_a_empleados(make_session):
    run_row = _payroll_run_row(total=Decimal("1000.00"))
    item_row = _item_row(
        bruto=Decimal("1000.00"), deducciones=Decimal("0.00"), neto=Decimal("1000.00")
    )
    item_row["empleado_nombre"] = "Ana"
    session = make_session([[run_row], [item_row]])
    nomina = await obtener_nomina(session, tenant_id=uuid4(), payroll_run_id=uuid4())
    assert nomina["items"][0]["empleado_nombre"] == "Ana"
    assert nomina["total_bruto"] == Decimal("1000.00")
    assert nomina["total_deducciones"] == Decimal("0.00")
    sql_items, _ = session.llamadas[1]
    assert "JOIN employees" in sql_items


async def test_obtener_nomina_no_existente_retorna_none(make_session):
    session = make_session([[]])
    nomina = await obtener_nomina(session, tenant_id=uuid4(), payroll_run_id=uuid4())
    assert nomina is None
    assert len(session.llamadas) == 1  # nunca llegó a buscar los items


# ---------------------------------------------------------------------------
# aprobar_nomina / cancelar_nomina
# ---------------------------------------------------------------------------


async def test_aprobar_nomina_happy_path(make_session):
    run_draft = _payroll_run_row(status="draft")
    run_approved = _payroll_run_row(status="approved")
    session = make_session([[run_draft], [run_approved]])
    nomina = await aprobar_nomina(session, tenant_id=uuid4(), payroll_run_id=uuid4())
    assert nomina["status"] == "approved"
    sql_update, _ = session.llamadas[1]
    assert "approved_at = now()" in sql_update


async def test_aprobar_nomina_select_inicial_usa_for_update(make_session):
    """El `SELECT` que alimenta el chequeo de estado debe tomar el lock de fila (`FOR
    UPDATE`) ANTES de leer `status` — si no, dos llamadas concurrentes podrían ambas leer
    `'draft'` y ambas aprobar (ver el docstring de `_lock_payroll_run`)."""
    run_draft = _payroll_run_row(status="draft")
    run_approved = _payroll_run_row(status="approved")
    session = make_session([[run_draft], [run_approved]])
    await aprobar_nomina(session, tenant_id=uuid4(), payroll_run_id=uuid4())
    sql_select, _ = session.llamadas[0]
    assert "FOR UPDATE" in sql_select


async def test_aprobar_nomina_no_existente_retorna_none(make_session):
    session = make_session([[]])
    nomina = await aprobar_nomina(session, tenant_id=uuid4(), payroll_run_id=uuid4())
    assert nomina is None


async def test_aprobar_nomina_doble_aprobacion_rechaza(make_session):
    """Idempotencia explícita: re-aprobar una nómina ya `'approved'` lanza `EstadoNominaError`
    en vez de aprobarla dos veces (mismo `FOR UPDATE` de arriba cierra la carrera real contra
    Postgres; esta prueba fija el comportamiento del segundo intento secuencial)."""
    run_approved = _payroll_run_row(status="approved")
    session = make_session([[run_approved]])
    with pytest.raises(EstadoNominaError, match="approved"):
        await aprobar_nomina(session, tenant_id=uuid4(), payroll_run_id=uuid4())
    assert len(session.llamadas) == 1  # nunca llegó al UPDATE


async def test_aprobar_nomina_cancelada_rechaza(make_session):
    run_cancelled = _payroll_run_row(status="cancelled")
    session = make_session([[run_cancelled]])
    with pytest.raises(EstadoNominaError):
        await aprobar_nomina(session, tenant_id=uuid4(), payroll_run_id=uuid4())


async def test_cancelar_nomina_happy_path(make_session):
    run_draft = _payroll_run_row(status="draft")
    run_cancelled = _payroll_run_row(status="cancelled")
    session = make_session([[run_draft], [run_cancelled]])
    nomina = await cancelar_nomina(session, tenant_id=uuid4(), payroll_run_id=uuid4())
    assert nomina["status"] == "cancelled"


async def test_cancelar_nomina_select_inicial_usa_for_update(make_session):
    run_draft = _payroll_run_row(status="draft")
    run_cancelled = _payroll_run_row(status="cancelled")
    session = make_session([[run_draft], [run_cancelled]])
    await cancelar_nomina(session, tenant_id=uuid4(), payroll_run_id=uuid4())
    sql_select, _ = session.llamadas[0]
    assert "FOR UPDATE" in sql_select


async def test_cancelar_nomina_ya_aprobada_rechaza(make_session):
    run_approved = _payroll_run_row(status="approved")
    session = make_session([[run_approved]])
    with pytest.raises(EstadoNominaError, match="approved"):
        await cancelar_nomina(session, tenant_id=uuid4(), payroll_run_id=uuid4())
    assert len(session.llamadas) == 1


async def test_cancelar_nomina_no_existente_retorna_none(make_session):
    session = make_session([[]])
    nomina = await cancelar_nomina(session, tenant_id=uuid4(), payroll_run_id=uuid4())
    assert nomina is None
