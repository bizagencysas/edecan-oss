"""Barrido dedicado v7 (WP-V7-02) de la vertical Viajes — `apps/api/edecan_api/
routers/viajes.py` + `packages/travel/edecan_travel/` (`amadeus.py`, `tracking.py`,
`providers.py`, `tools.py`). Ver `docs/cumplimiento/barrido-v7-viajes.md` para la
tabla completa archivo→veredicto→evidencia. Este archivo consolida el lado
`apps/api` de los 4 barridos que pedía el paquete de trabajo:

- **BARRIDO A** (fuga bring-your-own): `test_settings_no_declara_ningun_campo_
  amadeus_ni_aftership` — el lado `packages/travel` (firma de constructores, request
  real con centinela, `ctx.settings` "veneno") vive en
  `packages/travel/tests/test_travel_byo.py`, que este archivo no repite.
- **BARRIDO B** (plan-flag): `test_todas_las_rutas_de_viajes_exigen_
  require_tools_travel` (estructural, vía introspección de `route.dependant`, no una
  lista de paths a mano — así una ruta NUEVA que olvide el gate se detecta sola) +
  `test_planes_matriz_tools_travel_coincide_con_architecture_14c` (contra el
  `PLANES` REAL, sin monkeypatch) + `test_ninguna_tool_de_travel_multiplexa_
  acciones_con_permisos_distintos` (documenta por qué el patrón `_bloqueo_por_plan`
  de `computadora.py` NO aplica acá: las 5 tools son 1 tool = 1 acción, todas con el
  mismo `requires_flags`, nunca un parámetro `accion`/`action` que dispatche a
  capacidades con permisos distintos).
- **BARRIDO C** (evidencia vs. rollback): re-verifica INTACTO, con aserciones AST
  (no solo lectura humana), el veredicto "Seguro" que ya dio
  `docs/cumplimiento/barrido-evidencia-v6.md` para las 4 credenciales de
  `viajes.py` — el ping de validación siempre antes de persistir, y
  `repo.add_audit_log(...)` siempre la ÚLTIMA sentencia de la función (nada
  alcanzable después que pudiera fallar y perder esa evidencia en un rollback,
  mismo patrón que causó el bug real de `_apply_optout`,
  `HOTFIXES_PENDIENTES.md`). Confirma además que `rastreo()` (el único endpoint de
  solo-tracking) no escribe evidencia alguna (nada que proteger) y que este router
  no tiene ningún endpoint de "confirmación" propio — los 2 sitios de escritura
  restantes que pedía el enunciado ("creación de borradores de reserva" y
  "confirmaciones") viven fuera de `apps/api/edecan_api/routers/viajes.py`:
  `preparar_reserva`/`_crear_reserva_draft` en `packages/travel/edecan_travel/
  tools.py` (cubierto en `packages/travel/tests/test_tools.py`, protegido por el
  aislamiento de excepción de `Agent._run_turn`/`_stream_approved_confirmation`,
  fuera del alcance de este WP) y la confirmación genérica de un `orders` en
  `commerce.py::confirm_order` (WP-V6-03, ya con su propio commit explícito antes
  del 501 de `kind` no manejado — ver `docs/cumplimiento/barrido-evidencia-v6.md`,
  sección "Verificación de los 4 fixes previos").
- **BARRIDO D** (esquema real de `orders`): `test_crear_reserva_draft_insert_
  columnas_existen_en_el_modelo_order_real` (ancla contra `edecan_db.models.Order`
  — el modelo/migración REAL, no un docstring) +
  `test_crear_reserva_draft_usa_valores_permitidos_por_los_check_constraints_
  reales` (`kind='purchase'`/`status='draft'` contra los `CheckConstraint` reales
  de la tabla). Verificado EMPÍRICAMENTE además (no solo estático) con un Postgres
  desechable real (`edecan-v7-viajes-pg`, migrado a `head`, `INSERT` ejecutado bajo
  `SET LOCAL ROLE app_user` — el mismo rol con RLS que usa producción — y releído):
  el INSERT de `_crear_reserva_draft` corre limpio contra el esquema real, sin
  `docker` corriendo ya al terminar (contenedor borrado, ver el propio log de la
  sesión de este WP). No se deja un test `@pytest.mark.integration` nuevo para
  esto porque `packages/travel` no depende de `edecan-db` (`ARCHITECTURE.md` §10.1,
  "los tests no importan paquetes hermanos") — la verificación estática de este
  archivo (columnas + CHECK constraints reales) es la que queda de guardia en CI.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

from edecan_api.routers import viajes as viajes_module

# ---------------------------------------------------------------------------
# BARRIDO A — sin campo de plataforma al que nada pueda caer.
# ---------------------------------------------------------------------------


def test_settings_no_declara_ningun_campo_amadeus_ni_aftership():
    """Confirma la premisa completa del veredicto "LIMPIO" de
    `packages/travel/tests/test_travel_byo.py`: no existe, en el `Settings` REAL de
    `apps/api`, ningún campo `AMADEUS_*`/`AFTERSHIP_*` al que un resolver pudiera
    verse tentado a caer como "credencial de plataforma" (el patrón de v4,
    `HOTFIXES_PENDIENTES.md`)."""
    from edecan_api.config import Settings

    campos = set(Settings.model_fields)
    ofensores = {c for c in campos if "AMADEUS" in c.upper() or "AFTERSHIP" in c.upper()}
    assert not ofensores, (
        f"Settings declara campo(s) de plataforma para viajes: {ofensores} -- si esto "
        "cambia alguna vez, algún resolver podría verse tentado a usarlos como "
        "fallback de plataforma, reintroduciendo el patrón de v4."
    )


# ---------------------------------------------------------------------------
# BARRIDO B — plan-flag.
# ---------------------------------------------------------------------------


def test_todas_las_rutas_de_viajes_exigen_require_tools_travel():
    """Estructural: recorre `viajes.router.routes` (no una lista de paths a mano,
    que una ruta nueva podría no actualizar) y confirma que TODAS dependen de
    `_require_tools_travel` -- si alguien agrega un endpoint nuevo a este router sin
    el gate de plan, este test lo detecta solo, sin depender de que
    `test_rejects_plan_without_travel_flag` (`test_viajes_router.py`) se acuerde de
    agregar ese path nuevo a su lista parametrizada."""
    rutas = viajes_module.router.routes
    assert len(rutas) == 8, (
        f"Se esperaban 8 rutas en viajes.router (4 credenciales + status + 3 "
        f"búsqueda/rastreo), hay {len(rutas)} -- si se agregó/quitó una ruta a "
        "propósito, actualiza este número junto con la verificación de abajo."
    )
    for ruta in rutas:
        llamadas = {dep.call for dep in ruta.dependant.dependencies}
        assert viajes_module._require_tools_travel in llamadas, (
            f"{list(ruta.methods)} {ruta.path} no depende de _require_tools_travel "
            "-- quedaría accesible sin el flag de plan tools.travel."
        )


def test_planes_matriz_tools_travel_coincide_con_architecture_14c():
    """Contra el `PLANES` REAL (sin `monkeypatch`, a diferencia de
    `test_viajes_router.py::_con_flag_travel`). Modelo de precio de pago único
    (2026-07-09, `edecan_schemas.plans` docstring): las 4 entradas de `PLANES`
    conceden `FLAG_TOOLS_TRAVEL` por igual -- ya no hay matriz distinta por
    plan que pinnear contra `ARCHITECTURE.md` §14.c. Si esto cambiara alguna
    vez sin querer, esta suite lo detecta aunque `test_viajes_router.py` siga
    pasando (su fixture fuerza `True` a propósito y no notaría un cambio
    real)."""
    from edecan_schemas.plans import FLAG_TOOLS_TRAVEL, PLANES

    assert PLANES["free_selfhost"].flags[FLAG_TOOLS_TRAVEL] is True
    assert PLANES["hosted_basic"].flags[FLAG_TOOLS_TRAVEL] is True
    assert PLANES["hosted_pro"].flags[FLAG_TOOLS_TRAVEL] is True
    assert PLANES["hosted_business"].flags[FLAG_TOOLS_TRAVEL] is True


def test_ninguna_tool_de_travel_multiplexa_acciones_con_permisos_distintos():
    """Documenta por qué el patrón `_bloqueo_por_plan` de
    `packages/toolkit/edecan_toolkit/computadora.py` (`HOTFIXES_PENDIENTES.md`,
    "usar_computadora se saltaba companion.remote_input/companion.ide") NO aplica a
    `edecan_travel`: ese patrón hace falta cuando UNA tool despacha, con un
    parámetro tipo `accion`/`action`, a sub-capacidades que exigen flags de plan
    MÁS FINOS que el `requires_flags` de la clase. Ninguna de las 5 tools de viajes
    tiene un parámetro así en su `input_schema` -- son 1 tool = 1 acción, las 5
    bajo el MISMO `requires_flags={"tools.travel"}` (ver
    `packages/travel/tests/test_catalogo.py::
    test_las_5_tools_requieren_el_flag_tools_travel`), así que no existe ninguna
    acción "escondida" que necesite un chequeo más fino."""
    from edecan_travel.tools import get_all_tools

    for tool in get_all_tools():
        propiedades = set((tool.input_schema or {}).get("properties", {}))
        assert "accion" not in propiedades
        assert "action" not in propiedades


# ---------------------------------------------------------------------------
# BARRIDO C — evidencia vs. rollback de sesión (re-verificación AST del veredicto
# "Seguro" de docs/cumplimiento/barrido-evidencia-v6.md).
# ---------------------------------------------------------------------------


def _cuerpo_ast(func) -> list[ast.stmt]:
    fuente = textwrap.dedent(inspect.getsource(func))
    arbol = ast.parse(fuente)
    definicion = arbol.body[0]
    assert isinstance(definicion, ast.AsyncFunctionDef | ast.FunctionDef)
    return definicion.body


def _es_llamada_a_atributo(nodo: ast.stmt, nombre_atributo: str) -> bool:
    if not isinstance(nodo, ast.Expr):
        return False
    valor = nodo.value
    if isinstance(valor, ast.Await):
        valor = valor.value
    if not isinstance(valor, ast.Call):
        return False
    return isinstance(valor.func, ast.Attribute) and valor.func.attr == nombre_atributo


def _lineno_de_llamada(
    func,
    *,
    nombre_funcion: str | None = None,
    nombre_atributo: str | None = None,
    objeto: str | None = None,
) -> int:
    """`objeto` (opcional) desambigua un `nombre_atributo` genérico como `"put"` —
    sin esto, `ast.walk` también encontraría el `.put(...)` del decorador
    `@router.put("/credentials", ...)` (que aparece ANTES que el cuerpo de la
    función en el árbol) en vez del `vault.put(...)` real."""
    fuente = textwrap.dedent(inspect.getsource(func))
    arbol = ast.parse(fuente)
    definicion = arbol.body[0]
    assert isinstance(definicion, ast.AsyncFunctionDef | ast.FunctionDef)
    for nodo in ast.walk(ast.Module(body=definicion.body, type_ignores=[])):
        if not isinstance(nodo, ast.Call):
            continue
        if nombre_funcion and isinstance(nodo.func, ast.Name) and nodo.func.id == nombre_funcion:
            return nodo.lineno
        es_atributo_buscado = (
            nombre_atributo
            and isinstance(nodo.func, ast.Attribute)
            and nodo.func.attr == nombre_atributo
        )
        if es_atributo_buscado:
            coincide_objeto = objeto is None or (
                isinstance(nodo.func.value, ast.Name) and nodo.func.value.id == objeto
            )
            if coincide_objeto:
                return nodo.lineno
    nombre_buscado = nombre_funcion or nombre_atributo
    raise AssertionError(f"No se encontró una llamada a {nombre_buscado!r} en {func.__name__}")


def test_put_credentials_ping_amadeus_corre_antes_de_persistir():
    linea_ping = _lineno_de_llamada(viajes_module.put_credentials, nombre_funcion="_ping_amadeus")
    linea_vault_put = _lineno_de_llamada(
        viajes_module.put_credentials, nombre_atributo="put", objeto="vault"
    )
    linea_audit = _lineno_de_llamada(
        viajes_module.put_credentials, nombre_atributo="add_audit_log", objeto="repo"
    )
    assert linea_ping < linea_vault_put < linea_audit


def test_put_rastreo_credentials_ping_aftership_corre_antes_de_persistir():
    linea_ping = _lineno_de_llamada(
        viajes_module.put_rastreo_credentials, nombre_funcion="_ping_aftership"
    )
    linea_vault_put = _lineno_de_llamada(
        viajes_module.put_rastreo_credentials, nombre_atributo="put", objeto="vault"
    )
    linea_audit = _lineno_de_llamada(
        viajes_module.put_rastreo_credentials, nombre_atributo="add_audit_log", objeto="repo"
    )
    assert linea_ping < linea_vault_put < linea_audit


_HANDLERS_DE_CREDENCIALES = (
    viajes_module.put_credentials,
    viajes_module.delete_credentials,
    viajes_module.put_rastreo_credentials,
    viajes_module.delete_rastreo_credentials,
)


def test_credenciales_add_audit_log_es_siempre_la_ultima_sentencia_de_la_funcion():
    """BARRIDO C: re-confirma INTACTO, con una aserción estructural (no solo
    lectura humana), el veredicto "Seguro" de
    `docs/cumplimiento/barrido-evidencia-v6.md` para los 4 sitios de escritura de
    `viajes.py` -- `repo.add_audit_log(...)` debe ser la ÚLTIMA sentencia top-level
    del cuerpo de la función en los 4 handlers. Mismo tipo de chequeo que, aplicado
    a tiempo, habría detectado directamente el bug real de `_apply_optout`
    (`HOTFIXES_PENDIENTES.md`): ahí la escritura de evidencia NO era la última
    sentencia -- un `UPDATE campaign_targets` sin relación corría después, en la
    misma sesión, sin commit de por medio."""
    for handler in _HANDLERS_DE_CREDENCIALES:
        ultima_sentencia = _cuerpo_ast(handler)[-1]
        assert _es_llamada_a_atributo(ultima_sentencia, "add_audit_log"), (
            f"{handler.__name__}: la última sentencia del cuerpo no es "
            "repo.add_audit_log(...) -- algo corre después que podría fallar y "
            "perder esa evidencia en un rollback de sesión."
        )


def test_delete_credentials_idempotente_no_llega_a_escribir_nada_si_no_hay_cuenta():
    """`delete_credentials`/`delete_rastreo_credentials` tienen un `return`
    temprano (`if account is None: return`) ANTES de tocar `add_audit_log` -- se
    confirma que ese `return` existe como una sentencia previa a la escritura, no
    solo por el comportamiento observado en `test_viajes_router.py::
    test_delete_credentials_es_idempotente` (que no inspecciona la sesión)."""
    for handler in (viajes_module.delete_credentials, viajes_module.delete_rastreo_credentials):
        cuerpo = _cuerpo_ast(handler)
        tiene_return_temprano = any(
            isinstance(nodo, ast.If) and any(isinstance(sub, ast.Return) for sub in ast.walk(nodo))
            for nodo in cuerpo
        )
        assert tiene_return_temprano, f"{handler.__name__}: no se encontró el return idempotente."


def test_rastreo_no_escribe_ninguna_evidencia():
    """El único endpoint de tracking (`GET /v1/viajes/rastreo/{numero}`) es de
    solo lectura de punta a punta -- no hay `repo.add_audit_log`/`session.execute`
    de escritura en su cuerpo, así que no hay NADA que proteger de un rollback acá
    (a diferencia de las 4 credenciales, que sí escriben)."""
    fuente = inspect.getsource(viajes_module.rastreo)
    assert "add_audit_log" not in fuente
    assert "INSERT" not in fuente.upper()
    assert "UPDATE" not in fuente.upper()


def test_viajes_router_no_tiene_ningun_endpoint_de_confirmacion_propio():
    """Documenta explícitamente (BARRIDO C) que este router NO tiene un segundo
    "push"/confirmación como `ads.py::confirmar_borrador` -- la única forma de que
    un borrador de `orders(kind='purchase')` avance de estado es fuera de este
    archivo (`commerce.py::confirm_order`, ya cubierto por WP-V6-03). Si algún día
    se agrega un endpoint de confirmación acá, este test lo obliga a una revisión
    consciente del guardrail de dinero."""
    paths = {ruta.path for ruta in viajes_module.router.routes}
    assert not any("confirm" in p for p in paths)
    assert not any("reserva" in p or "orders" in p or "book" in p for p in paths)


# ---------------------------------------------------------------------------
# BARRIDO D — esquema real de `orders` (edecan_db.models.Order), no un docstring.
# ---------------------------------------------------------------------------


def _columnas_del_insert(func) -> set[str]:
    fuente = inspect.getsource(func)
    inicio = fuente.index("INSERT INTO orders")
    inicio_parentesis = fuente.index("(", inicio)
    fin_parentesis = fuente.index(")", inicio_parentesis)
    columnas_texto = fuente[inicio_parentesis + 1 : fin_parentesis]
    return {c.strip() for c in columnas_texto.replace("\n", " ").split(",")}


def test_crear_reserva_draft_insert_columnas_existen_en_el_modelo_order_real():
    """BARRIDO D (WP-V7-02): extrae los nombres de columna del `INSERT INTO
    orders` de `edecan_travel.tools._crear_reserva_draft` y confirma que TODOS
    existen como columnas reales de `edecan_db.models.Order` -- ancla contra el
    modelo/migración que de verdad aterrizó (`0003_v2_expansion`), no contra
    ningún docstring/README propio del paquete. Mismo tipo de verificación que
    habría atrapado a tiempo el bug real de esquema de `reuniones.py`/
    `process_meeting.py` en v6 (`ARCHITECTURE.md`, "v6 completado"), donde el
    `FakeSession` de los tests mockeaba filas con el MISMO esquema equivocado que
    el código -- acá se compara contra el modelo SQLAlchemy real, no contra otro
    fake."""
    from edecan_db.models import Order
    from edecan_travel.tools import _crear_reserva_draft

    columnas_insert = _columnas_del_insert(_crear_reserva_draft)
    columnas_reales = set(Order.__table__.columns.keys())

    faltantes = columnas_insert - columnas_reales
    assert not faltantes, f"Columnas en el INSERT que NO existen en Order: {faltantes}"
    # Las columnas nullable que el INSERT omite a propósito (simbolo/lado/cantidad/
    # confirmed_at/executed_at) deben seguir siendo nullable -- si alguna pasara a
    # NOT NULL sin default, el INSERT real fallaría aunque las columnas "existan".
    omitidas = columnas_reales - columnas_insert - {"id", "created_at", "updated_at"}
    for columna in omitidas:
        col = Order.__table__.columns[columna]
        assert col.nullable or col.server_default is not None, (
            f"Order.{columna} es NOT NULL sin server_default y _crear_reserva_draft "
            "no la incluye en el INSERT -- reventaría contra Postgres real."
        )


def test_crear_reserva_draft_usa_valores_permitidos_por_los_check_constraints_reales():
    """`kind='purchase'` y `status='draft'` (los dos literales hardcodeados del
    INSERT) deben estar dentro de los `CheckConstraint` REALES de la tabla -- no
    basta con que las columnas existan, los valores también deben ser válidos."""
    import re

    from edecan_db.models import Order
    from sqlalchemy import CheckConstraint

    valores_por_columna: dict[str, set[str]] = {}
    for constraint in Order.__table__.constraints:
        if not isinstance(constraint, CheckConstraint):
            continue
        texto = str(constraint.sqltext)
        columna = texto.split(" IN ", 1)[0].strip()
        valores_por_columna[columna] = set(re.findall(r"'([^']+)'", texto))

    assert "purchase" in valores_por_columna.get("kind", set())
    assert "draft" in valores_por_columna.get("status", set())
