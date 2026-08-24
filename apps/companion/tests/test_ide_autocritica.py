"""Pruebas de auto-mejora entre proyectos -- ``ide_autocritica.AutocriticaStore``.

Cubre lo que el encargo pide explícitamente: que un solo intento no deje
lección, que dos sí, que la lección se recupere por su firma, que una lección
que reincide se retire sola, y que ninguna ruta absoluta de la máquina llegue
al archivo persistido. Suma las decisiones de diseño que el módulo documenta:
que no se castiga una lección que nunca se inyectó, que el alcance es global
entre proyectos, y el desalojo de la más débil al llegar al tope.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from edecan_companion.ide_autocritica import (
    MAX_ARREGLO_CHARS,
    AutocriticaStore,
    IDEAutocriticaError,
    normalizar_firma,
)
from edecan_companion.ide_verificacion import ResultadoBucle, ResultadoIntento
from edecan_companion.ide_workspaces import WorkspaceStore

FIRMA_MODULO = "exit=1:ModuleNotFoundError: No module named 'yaml'"
ARREGLO_MODULO = "Declara `pyyaml` en las dependencias del paquete y reinstala el entorno."


def _intento(numero: int, *, firma: str | None = None, aprobado: bool = False) -> ResultadoIntento:
    return ResultadoIntento(
        intento=numero,
        argv=("pytest", "-q"),
        aprobado=aprobado,
        exit_code=0 if aprobado else 1,
        tipo_falla=None if aprobado else "fallo_de_verificacion",
        motivo_no_ejecutable=None,
        resumen_error=None,
        firma=None if aprobado else firma,
        duracion_segundos=0.1,
    )


def _bucle(*intentos: ResultadoIntento) -> ResultadoBucle:
    aprobado = bool(intentos) and intentos[-1].aprobado
    return ResultadoBucle(
        aprobado=aprobado,
        intentos=tuple(intentos),
        detenido_por="aprobado" if aprobado else "error_repetido",
        mensaje="bucle de prueba",
    )


def _store(tmp_path: Path, **kwargs) -> AutocriticaStore:
    return AutocriticaStore(tmp_path / "state", **kwargs)


def _bucle_que_falla_dos_veces(firma: str) -> ResultadoBucle:
    return _bucle(_intento(1, firma=firma), _intento(2, firma=firma))


# --------------------------------------------------------------------- #
# Qué merece guardarse.
# --------------------------------------------------------------------- #


def test_un_solo_intento_no_deja_leccion(tmp_path: Path):
    """Salió bien a la primera: no hubo nada que aprender."""

    autocritica = _store(tmp_path)

    reporte = autocritica.registrar_bucle(
        _bucle(_intento(1, aprobado=True)), arreglo=ARREGLO_MODULO
    )

    assert reporte["guardada"] is False
    assert reporte["motivo"] == "aprobo_al_primer_intento"
    assert autocritica.listar() == []


def test_dos_intentos_con_arreglo_dejan_leccion(tmp_path: Path):
    autocritica = _store(tmp_path)

    reporte = autocritica.registrar_bucle(
        _bucle(_intento(1, firma=FIRMA_MODULO), _intento(2, aprobado=True)),
        arreglo=ARREGLO_MODULO,
    )

    assert reporte["guardada"] is True
    assert reporte["motivo"] == "leccion_nueva"
    assert reporte["leccion"]["arreglo"] == ARREGLO_MODULO
    assert reporte["leccion"]["confianza"] == 1.0
    assert len(autocritica.listar()) == 1


def test_bucle_que_nunca_aprobo_no_deja_leccion(tmp_path: Path):
    """No se sabe qué lo arregla; guardar el intento fallido como receta
    sería peor que no guardar nada."""

    autocritica = _store(tmp_path)

    reporte = autocritica.registrar_bucle(
        _bucle(_intento(1, firma=FIRMA_MODULO), _intento(2, firma="exit=1:otro error distinto")),
        arreglo=ARREGLO_MODULO,
    )

    assert reporte["guardada"] is False
    assert reporte["motivo"] == "no_aprobo"
    assert autocritica.listar() == []


def test_sin_arreglo_no_se_guarda_y_se_dice_por_que(tmp_path: Path):
    autocritica = _store(tmp_path)

    reporte = autocritica.registrar_bucle(
        _bucle(_intento(1, firma=FIRMA_MODULO), _intento(2, aprobado=True))
    )

    assert reporte["guardada"] is False
    assert reporte["motivo"] == "sin_arreglo"
    assert autocritica.listar() == []


def test_firma_sin_cuerpo_no_sirve_como_clave(tmp_path: Path):
    """``exit=1:`` a secas coincidiría con cualquier fallo mudo de cualquier
    proyecto: serviría la lección de un repo en el error de otro."""

    autocritica = _store(tmp_path)

    reporte = autocritica.registrar_bucle(
        _bucle(_intento(1, firma="exit=1:"), _intento(2, aprobado=True)),
        arreglo=ARREGLO_MODULO,
    )

    assert reporte["guardada"] is False
    assert reporte["motivo"] == "firma_sin_contenido"
    assert autocritica.leccion_para("exit=1:") is None


@pytest.mark.parametrize(
    "firma_generica",
    [
        # La que emite hoy ide_verificacion ante CUALQUIER módulo ausente de
        # CUALQUIER repo: no nombra el módulo, solo la excepción.
        "no_se_pudo_ejecutar:dependencia:ModuleNotFoundError",
        "no_se_pudo_ejecutar:dependencia:Cannot find module",
        "no_se_pudo_ejecutar:timeout",
    ],
)
def test_una_etiqueta_fija_no_sirve_como_clave_aunque_sea_larga(tmp_path: Path, firma_generica):
    """Larga no es lo mismo que específica. Si se aceptara, la lección
    "declara tal dependencia" se serviría ante cualquier módulo ausente del
    mundo -- y con confianza 1.0, porque cada acierto ajeno la reforzaría."""

    autocritica = _store(tmp_path)

    reporte = autocritica.registrar_bucle(
        _bucle(_intento(1, firma=firma_generica), _intento(2, aprobado=True)),
        arreglo=ARREGLO_MODULO,
    )

    assert reporte["guardada"] is False
    assert reporte["motivo"] == "firma_sin_contenido"
    assert autocritica.bloque_para(firma_generica) is None


@pytest.mark.parametrize("arreglo", ["", "ok", "x" * (MAX_ARREGLO_CHARS + 1), 42])
def test_arreglo_con_forma_de_ruido_se_rechaza(tmp_path: Path, arreglo):
    autocritica = _store(tmp_path)

    with pytest.raises(IDEAutocriticaError):
        autocritica.registrar_bucle(
            _bucle(_intento(1, firma=FIRMA_MODULO), _intento(2, aprobado=True)),
            arreglo=arreglo,
        )


def test_registrar_bucle_exige_un_resultado_de_verificacion(tmp_path: Path):
    autocritica = _store(tmp_path)

    with pytest.raises(IDEAutocriticaError):
        autocritica.registrar_bucle({"aprobado": True}, arreglo=ARREGLO_MODULO)


# --------------------------------------------------------------------- #
# Recuperación por firma: el disparador mecánico.
# --------------------------------------------------------------------- #


def test_la_leccion_se_recupera_por_la_firma_del_error(tmp_path: Path):
    autocritica = _store(tmp_path)
    autocritica.registrar_bucle(
        _bucle(_intento(1, firma=FIRMA_MODULO), _intento(2, aprobado=True)),
        arreglo=ARREGLO_MODULO,
    )

    leccion = autocritica.leccion_para(FIRMA_MODULO)

    assert leccion is not None
    assert leccion["arreglo"] == ARREGLO_MODULO
    assert leccion["veces_servida"] == 1  # servirla cuenta como uso real
    assert autocritica.leccion_para("exit=1:un error que nunca se vio") is None


def test_bloque_para_devuelve_none_cuando_no_hay_nada_que_decir(tmp_path: Path):
    autocritica = _store(tmp_path)

    assert autocritica.bloque_para(FIRMA_MODULO) is None
    assert autocritica.bloque_para(None) is None


def test_bloque_para_incluye_el_arreglo_y_el_permiso_de_descartarlo(tmp_path: Path):
    autocritica = _store(tmp_path)
    autocritica.registrar_bucle(
        _bucle(_intento(1, firma=FIRMA_MODULO), _intento(2, aprobado=True)),
        arreglo=ARREGLO_MODULO,
    )

    bloque = autocritica.bloque_para(FIRMA_MODULO)

    assert ARREGLO_MODULO in bloque
    assert "no aplica" in bloque  # sin permiso de descartarla, una lección parecida empuja mal


def test_resolver_el_mismo_error_otra_vez_refuerza_la_leccion(tmp_path: Path):
    autocritica = _store(tmp_path)
    autocritica.registrar_bucle(
        _bucle(_intento(1, firma=FIRMA_MODULO), _intento(2, aprobado=True)),
        arreglo=ARREGLO_MODULO,
    )

    reporte = autocritica.registrar_bucle(
        _bucle(_intento(1, firma=FIRMA_MODULO), _intento(2, aprobado=True)),
        arreglo="Agrega `pyyaml` al grupo de dependencias correcto, no al de desarrollo.",
    )

    assert reporte["motivo"] == "leccion_reforzada"
    assert reporte["leccion"]["veces_util"] == 2
    assert len(autocritica.listar()) == 1  # no duplicó la fila


# --------------------------------------------------------------------- #
# Confianza: una lección equivocada es peor que ninguna.
# --------------------------------------------------------------------- #


def test_una_leccion_que_reincide_pierde_confianza_y_luego_se_retira(tmp_path: Path):
    autocritica = _store(tmp_path)
    autocritica.registrar_bucle(
        _bucle(_intento(1, firma=FIRMA_MODULO), _intento(2, aprobado=True)),
        arreglo=ARREGLO_MODULO,
    )

    # Primera reincidencia: se inyectó la lección y el error volvió idéntico.
    autocritica.leccion_para(FIRMA_MODULO)
    primera = autocritica.registrar_bucle(_bucle_que_falla_dos_veces(FIRMA_MODULO))

    assert primera["retiradas"] == []
    assert primera["degradadas"][0]["veces_fallida"] == 1
    assert primera["degradadas"][0]["confianza"] < 1.0
    assert autocritica.leccion_para(FIRMA_MODULO) is not None  # sigue ahí, pero valiendo menos

    # Segunda: ya es un patrón, no mala suerte. Se retira sola.
    segunda = autocritica.registrar_bucle(_bucle_que_falla_dos_veces(FIRMA_MODULO))

    assert segunda["retiradas"][0]["firma"] == normalizar_firma(FIRMA_MODULO)
    assert autocritica.leccion_para(FIRMA_MODULO) is None
    assert autocritica.listar() == []


def test_no_se_castiga_una_leccion_que_nunca_se_inyecto(tmp_path: Path):
    """Si el error se repitió sin que la lección llegara a servirse, culparla
    sería cobrarle algo en lo que no participó."""

    autocritica = _store(tmp_path)
    autocritica.registrar_bucle(
        _bucle(_intento(1, firma=FIRMA_MODULO), _intento(2, aprobado=True)),
        arreglo=ARREGLO_MODULO,
    )

    reporte = autocritica.registrar_bucle(_bucle_que_falla_dos_veces(FIRMA_MODULO))

    assert reporte["degradadas"] == []
    assert reporte["retiradas"] == []
    assert autocritica.listar()[0]["veces_fallida"] == 0


def test_un_bucle_con_reincidencia_no_reescribe_la_leccion_que_acaba_de_fallar(tmp_path: Path):
    """El bucle terminó en verde, pero por el camino el mismo error se repitió
    idéntico con la lección aplicada: no es evidencia limpia de qué arregla
    esa firma, y guardarla ahora resucitaría a la que se acaba de castigar."""

    autocritica = _store(tmp_path)
    autocritica.registrar_bucle(
        _bucle(_intento(1, firma=FIRMA_MODULO), _intento(2, aprobado=True)),
        arreglo=ARREGLO_MODULO,
    )
    autocritica.leccion_para(FIRMA_MODULO)

    reporte = autocritica.registrar_bucle(
        _bucle(
            _intento(1, firma=FIRMA_MODULO),
            _intento(2, firma=FIRMA_MODULO),
            _intento(3, aprobado=True),
        ),
        arreglo="Otra cosa completamente distinta que sí funcionó esta vez.",
    )

    assert reporte["guardada"] is False
    assert reporte["motivo"] == "firma_reincidente_en_este_bucle"
    assert reporte["degradadas"][0]["veces_fallida"] == 1
    assert autocritica.listar()[0]["arreglo"] == ARREGLO_MODULO


def test_un_arreglo_basura_no_le_perdona_la_falla_a_la_leccion(tmp_path: Path):
    """El bucle falló con la lección aplicada, y el integrador mandó como
    ``arreglo`` lo que escribió el modelo, que no fue nada. Que ese texto no
    se guarde está bien; que le perdone la falla a la lección, no: este es
    justo el bucle donde el modelo no arregla nada y por eso no tiene qué
    contar, o sea el bucle donde el castigo más importa."""

    autocritica = _store(tmp_path)
    autocritica.registrar_bucle(
        _bucle(_intento(1, firma=FIRMA_MODULO), _intento(2, aprobado=True)),
        arreglo=ARREGLO_MODULO,
    )
    autocritica.leccion_para(FIRMA_MODULO)

    reporte = autocritica.registrar_bucle(_bucle_que_falla_dos_veces(FIRMA_MODULO), arreglo="listo")

    assert reporte["motivo"] == "no_aprobo"
    assert reporte["degradadas"][0]["veces_fallida"] == 1
    # Y quedó en disco, no solo en memoria.
    assert _store(tmp_path).listar()[0]["veces_fallida"] == 1


def test_confianza_minima_filtra_una_leccion_ya_degradada(tmp_path: Path):
    autocritica = _store(tmp_path)
    autocritica.registrar_bucle(
        _bucle(_intento(1, firma=FIRMA_MODULO), _intento(2, aprobado=True)),
        arreglo=ARREGLO_MODULO,
    )
    autocritica.leccion_para(FIRMA_MODULO)
    autocritica.registrar_bucle(_bucle_que_falla_dos_veces(FIRMA_MODULO))

    assert autocritica.leccion_para(FIRMA_MODULO, confianza_minima=0.5) is None
    assert autocritica.leccion_para(FIRMA_MODULO, confianza_minima=0.1) is not None


# --------------------------------------------------------------------- #
# Privacidad: el repo es público y esto se persiste.
# --------------------------------------------------------------------- #


def test_las_rutas_absolutas_no_se_persisten(tmp_path: Path):
    """Una firma de pytest trae rutas de la máquina donde corrió, y ahí vive
    el nombre de una persona. Además de no guardarlo, recortarlo es lo que le
    da a la firma alguna chance de volver a coincidir en otra máquina."""

    autocritica = _store(tmp_path)
    firma_con_ruta = (
        "exit=1:FAILED /Users/example/repos/api/tests/test_pagos.py::test_cobro"
        " - AssertionError\n"
        "/Users/example/repos/api/src/pagos.py:41: TypeError"
    )

    autocritica.registrar_bucle(
        _bucle(_intento(1, firma=firma_con_ruta), _intento(2, aprobado=True)),
        arreglo="Convierte el monto a Decimal antes de sumarlo en "
        "/Users/example/repos/api/src/pagos.py.",
    )

    crudo = (tmp_path / "state" / "ide-autocritica.json").read_text(encoding="utf-8")
    assert "persona-de-prueba" not in crudo
    assert "/Users/" not in crudo
    # Y lo que queda sigue sirviendo para reconocer el error.
    guardada = json.loads(crudo)["lecciones"][0]
    assert "tests/test_pagos.py::test_cobro" in guardada["firma"]
    assert "src/pagos.py" in guardada["arreglo"]


def test_el_comando_guardado_tampoco_lleva_la_ruta_personal(tmp_path: Path):
    """El ``argv`` no pasa por ``normalizar_firma`` y es donde más seguido
    aparece una ruta personal entera: el bucle de verificación acepta
    ejecutables absolutos, y el intérprete de un venv lo es casi siempre."""

    autocritica = _store(tmp_path)
    intento_con_venv = ResultadoIntento(
        intento=1,
        argv=("/Users/example/repos/api/.venv/bin/python", "-m", "pytest", "-q"),
        aprobado=False,
        exit_code=1,
        tipo_falla="fallo_de_verificacion",
        motivo_no_ejecutable=None,
        resumen_error=None,
        firma=FIRMA_MODULO,
        duracion_segundos=0.1,
    )

    autocritica.registrar_bucle(
        _bucle(intento_con_venv, _intento(2, aprobado=True)), arreglo=ARREGLO_MODULO
    )

    crudo = (tmp_path / "state" / "ide-autocritica.json").read_text(encoding="utf-8")
    assert "persona-de-prueba" not in crudo
    assert "/Users/" not in crudo
    # Y el comando sigue siendo reconocible para quien lea la lección.
    assert autocritica.listar()[0]["comando"] == [".../bin/python", "-m", "pytest", "-q"]


def test_la_firma_se_recupera_venga_con_ruta_absoluta_o_ya_normalizada(tmp_path: Path):
    autocritica = _store(tmp_path)
    firma_con_ruta = "exit=1:/Users/example/repos/api/tests/test_a.py:9: SyntaxError"
    autocritica.registrar_bucle(
        _bucle(_intento(1, firma=firma_con_ruta), _intento(2, aprobado=True)),
        arreglo="Cierra el paréntesis abierto en la línea anterior.",
    )

    # La misma corrida en otra máquina produce otra ruta absoluta y la misma
    # cola: tiene que caer en la misma lección.
    firma_de_otra_maquina = "exit=1:/home/example/checkout/api/tests/test_a.py:9: SyntaxError"

    assert autocritica.leccion_para(firma_con_ruta) is not None
    assert autocritica.leccion_para(firma_de_otra_maquina) is not None
    assert normalizar_firma(normalizar_firma(firma_con_ruta)) == normalizar_firma(firma_con_ruta)


def test_normalizar_no_rompe_un_mensaje_con_barras_invertidas(tmp_path: Path):
    """Un ``assert 'a\\nb' == 'a\\nc'` no es una ruta de Windows; tocarlo
    cambiaría el error y con él la clave del índice."""

    firma = r"exit=1:AssertionError: assert 'a\nb' == 'a\nc'"

    assert normalizar_firma(firma) == firma


# --------------------------------------------------------------------- #
# Alcance global, tope y persistencia.
# --------------------------------------------------------------------- #


def test_lo_aprendido_en_un_proyecto_sirve_en_los_demas(tmp_path: Path):
    """Un ``ModuleNotFoundError`` mal resuelto en un repo es la misma trampa
    en el siguiente: por eso este almacén es global, como ide_conocimiento."""

    state_dir = tmp_path / "state"
    proyecto_a = tmp_path / "proyecto_a"
    proyecto_b = tmp_path / "proyecto_b"
    proyecto_a.mkdir()
    proyecto_b.mkdir()
    workspaces = WorkspaceStore(state_dir)
    workspace_a = workspaces.authorize(str(proyecto_a))["id"]
    workspaces.authorize(str(proyecto_b))
    autocritica = AutocriticaStore(state_dir, workspaces)

    autocritica.registrar_bucle(
        _bucle(_intento(1, firma=FIRMA_MODULO), _intento(2, aprobado=True)),
        arreglo=ARREGLO_MODULO,
        workspace_id=workspace_a,
    )

    # Se recupera desde cualquier lado, sin declarar proyecto...
    assert autocritica.leccion_para(FIRMA_MODULO)["arreglo"] == ARREGLO_MODULO
    # ...y se conserva la procedencia, para saber dónde se aprendió.
    assert autocritica.listar()[0]["workspace_id_origen"] == workspace_a


def test_workspace_inexistente_se_rechaza_si_hay_registro_de_workspaces(tmp_path: Path):
    state_dir = tmp_path / "state"
    workspaces = WorkspaceStore(state_dir)
    autocritica = AutocriticaStore(state_dir, workspaces)

    with pytest.raises(IDEAutocriticaError):
        autocritica.registrar_bucle(
            _bucle(_intento(1, firma=FIRMA_MODULO), _intento(2, aprobado=True)),
            arreglo=ARREGLO_MODULO,
            workspace_id="no-existe",
        )


def test_al_llegar_al_tope_se_desaloja_la_mas_debil(tmp_path: Path):
    autocritica = _store(tmp_path, max_lecciones=2)
    debil = "exit=1:ErrorDebil: algo que resultó estar mal explicado"
    fuerte = "exit=1:ErrorFuerte: algo que sí se entendió"

    for firma in (debil, fuerte):
        autocritica.registrar_bucle(
            _bucle(_intento(1, firma=firma), _intento(2, aprobado=True)),
            arreglo=f"Arreglo verificado para {firma}.",
        )
    # La primera se degrada: se aplicó y el error volvió igual.
    autocritica.leccion_para(debil)
    autocritica.registrar_bucle(_bucle_que_falla_dos_veces(debil))

    nueva = "exit=1:ErrorNuevo: recién aprendido"
    autocritica.registrar_bucle(
        _bucle(_intento(1, firma=nueva), _intento(2, aprobado=True)),
        arreglo="Arreglo verificado para el error nuevo.",
    )

    firmas = {fila["firma"] for fila in autocritica.listar()}
    assert firmas == {normalizar_firma(fuerte), normalizar_firma(nueva)}


def test_olvidar_borra_una_leccion_puntual(tmp_path: Path):
    autocritica = _store(tmp_path)
    reporte = autocritica.registrar_bucle(
        _bucle(_intento(1, firma=FIRMA_MODULO), _intento(2, aprobado=True)),
        arreglo=ARREGLO_MODULO,
    )

    autocritica.olvidar(reporte["leccion"]["id"])

    assert autocritica.listar() == []
    with pytest.raises(IDEAutocriticaError):
        autocritica.olvidar(reporte["leccion"]["id"])


def test_persiste_entre_instancias_del_store(tmp_path: Path):
    primero = _store(tmp_path)
    primero.registrar_bucle(
        _bucle(_intento(1, firma=FIRMA_MODULO), _intento(2, aprobado=True)),
        arreglo=ARREGLO_MODULO,
    )

    segundo = _store(tmp_path)

    assert segundo.leccion_para(FIRMA_MODULO)["arreglo"] == ARREGLO_MODULO
