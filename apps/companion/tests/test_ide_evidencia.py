"""Evidencia de ejecución — ``ide_evidencia.AlmacenEvidencia``.

Cubre lo que el encargo pide razonar explícitamente: distinguir evidencia de
éxito de evidencia de fallo (y que el fallo domine el veredicto conjunto de
un paso), que el "motivo" salga de hechos medidos y no de una afirmación
libre, el recorte por volumen (guardar por referencia, presentar lo
accionable), y la integración real con ``ide_verificacion``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from edecan_companion.ide_evidencia import (
    MAX_CHARS_PREVIEW_HTTP,
    AlmacenEvidencia,
    EvidenciaError,
)
from edecan_companion.ide_verificacion import ejecutar_intento


def _make_store(tmp_path: Path, **kwargs) -> AlmacenEvidencia:
    return AlmacenEvidencia(tmp_path / "state", **kwargs)


# --------------------------------------------------------------------- #
# registrar_comando: éxito vs. fallo, veredicto por código de salida.
# --------------------------------------------------------------------- #


def test_registrar_comando_exito_queda_marcado_y_tiene_motivo_con_hechos(tmp_path: Path):
    store = _make_store(tmp_path)
    evidencia = store.registrar_comando(
        "paso-1",
        argv=["pytest", "-q"],
        stdout="82 passed in 3.45s\n",
        stderr="",
        exit_code=0,
        duracion_segundos=3.45,
    )
    assert evidencia["veredicto"] == "exito"
    assert evidencia["metadata"]["exit_code"] == 0
    # El motivo es una traducción literal del hecho medido, no una frase libre.
    assert "código de salida 0" in evidencia["motivo"]
    assert "3.45s" in evidencia["motivo"]


def test_registrar_comando_fallo_incluye_extracto_del_resumen_en_el_motivo(tmp_path: Path):
    store = _make_store(tmp_path)
    stdout = (
        "collected 3 items\n\nF..\n\n"
        "=================== FAILURES ===================\n"
        "___________________ test_algo ___________________\n"
        "assert 1 == 2\n\n"
        "=============== short test summary info ================\n"
        "FAILED tests/test_algo.py::test_algo - assert 1 == 2\n"
        "=================== 1 failed, 2 passed in 0.12s ==================\n"
    )
    evidencia = store.registrar_comando(
        "paso-1", argv=["pytest", "-q"], stdout=stdout, stderr="", exit_code=1
    )
    assert evidencia["veredicto"] == "fallo"
    assert evidencia["resumen"]["fuente"] == "pytest"
    assert "test_algo" in evidencia["motivo"]
    assert "código de salida 1" in evidencia["motivo"]


def test_registrar_comando_exit_code_none_es_indeterminado(tmp_path: Path):
    store = _make_store(tmp_path)
    evidencia = store.registrar_comando(
        "paso-1", argv=["algo"], stdout="", stderr="timeout", exit_code=None
    )
    assert evidencia["veredicto"] == "indeterminado"


def test_leer_blob_devuelve_la_salida_completa_no_solo_el_resumen(tmp_path: Path):
    store = _make_store(tmp_path)
    stdout_completo = "x" * 10_000 + "\nERROR real al final\n"
    evidencia = store.registrar_comando(
        "paso-1", argv=["build"], stdout=stdout_completo, stderr="", exit_code=1
    )
    crudo = store.leer_blob(evidencia["id"], "stdout")
    assert crudo.decode("utf-8") == stdout_completo
    # El resumen inline, en cambio, está acotado -- no es el texto entero.
    assert len(evidencia["resumen"]["texto"]) < len(stdout_completo)


def test_leer_blob_con_campo_inexistente_lanza_error_claro(tmp_path: Path):
    store = _make_store(tmp_path)
    evidencia = store.registrar_comando(
        "paso-1", argv=["build"], stdout="ok", stderr="", exit_code=0
    )
    with pytest.raises(EvidenciaError):
        store.leer_blob(evidencia["id"], "campo_que_no_existe")


# --------------------------------------------------------------------- #
# registrar_resultado_comando: integración real con ide_verificacion.
# --------------------------------------------------------------------- #


def test_registrar_resultado_comando_exito_via_ejecutar_intento(tmp_path: Path):
    store = _make_store(tmp_path)
    resultado = ejecutar_intento(
        [sys.executable, "-c", "print('hola'); import sys; sys.exit(0)"], cwd=tmp_path
    )
    assert resultado.aprobado
    evidencia = store.registrar_resultado_comando(
        "paso-2", resultado, stdout="hola\n", stderr=""
    )
    assert evidencia["veredicto"] == "exito"
    assert evidencia["metadata"]["exit_code"] == 0


def test_registrar_resultado_comando_fallo_de_verificacion(tmp_path: Path):
    store = _make_store(tmp_path)
    resultado = ejecutar_intento(
        [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(1)"], cwd=tmp_path
    )
    assert not resultado.aprobado
    assert resultado.tipo_falla == "fallo_de_verificacion"
    evidencia = store.registrar_resultado_comando(
        "paso-2", resultado, stdout="", stderr="boom"
    )
    assert evidencia["veredicto"] == "fallo"


def test_registrar_resultado_comando_ejecutable_ausente_es_indeterminado_no_fallo(tmp_path: Path):
    store = _make_store(tmp_path)
    resultado = ejecutar_intento(["este-ejecutable-no-existe-de-verdad"], cwd=tmp_path)
    assert resultado.tipo_falla == "no_se_pudo_ejecutar"
    evidencia = store.registrar_resultado_comando("paso-2", resultado, stdout="", stderr="")
    # No pudo ni correr: no es prueba de que el código esté roto.
    assert evidencia["veredicto"] == "indeterminado"


# --------------------------------------------------------------------- #
# registrar_captura.
# --------------------------------------------------------------------- #


def test_registrar_captura_siempre_indeterminada_y_recuperable(tmp_path: Path):
    store = _make_store(tmp_path)
    png_falso = b"\x89PNG\r\n\x1a\n" + b"contenido binario simulado"
    evidencia = store.registrar_captura(
        "paso-3", imagen=png_falso, mime_type="image/png", etiqueta="pantalla final"
    )
    assert evidencia["veredicto"] == "indeterminado"
    assert evidencia["etiqueta"] == "pantalla final"
    assert store.leer_blob(evidencia["id"], "imagen") == png_falso


def test_registrar_captura_vacia_lanza_error(tmp_path: Path):
    store = _make_store(tmp_path)
    with pytest.raises(EvidenciaError):
        store.registrar_captura("paso-3", imagen=b"", mime_type="image/png")


# --------------------------------------------------------------------- #
# registrar_http: veredicto por status code, redacción de cabeceras.
# --------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("status_code", "veredicto_esperado"),
    [
        (200, "exito"),
        (201, "exito"),
        (301, "exito"),
        (404, "fallo"),
        (500, "fallo"),
        (101, "indeterminado"),
    ],
)
def test_registrar_http_veredicto_por_status_code(tmp_path: Path, status_code, veredicto_esperado):
    store = _make_store(tmp_path)
    evidencia = store.registrar_http(
        "paso-4",
        method="get",
        url="http://localhost:8000/salud",
        status_code=status_code,
        cuerpo=b'{"ok": true}',
    )
    assert evidencia["veredicto"] == veredicto_esperado
    assert str(status_code) in evidencia["motivo"]


def test_registrar_http_redacta_cabeceras_sensibles(tmp_path: Path):
    store = _make_store(tmp_path)
    evidencia = store.registrar_http(
        "paso-4",
        method="post",
        url="http://localhost:8000/login",
        status_code=200,
        cuerpo=b"{}",
        headers={"Authorization": "Bearer secreto-real", "Content-Type": "application/json"},
    )
    cabeceras = evidencia["metadata"]["headers"]
    assert cabeceras["Authorization"] == "«redactado»"
    assert cabeceras["Content-Type"] == "application/json"


def test_registrar_http_cuerpo_grande_se_recorta_en_el_preview_pero_el_blob_completo_sigue(
    tmp_path: Path,
):
    store = _make_store(tmp_path)
    cuerpo_grande = ('{"mensaje": "error real"}' + ("z" * (MAX_CHARS_PREVIEW_HTTP * 2))).encode()
    evidencia = store.registrar_http(
        "paso-4", method="get", url="http://x/", status_code=500, cuerpo=cuerpo_grande
    )
    assert evidencia["resumen"]["truncado"] is True
    assert len(evidencia["resumen"]["preview"]) <= MAX_CHARS_PREVIEW_HTTP + 100
    crudo = store.leer_blob(evidencia["id"], "cuerpo")
    assert crudo == cuerpo_grande


# --------------------------------------------------------------------- #
# Recorte de blobs por tamaño (MAX_BYTES_BLOB_CRUDO configurable).
# --------------------------------------------------------------------- #


def test_blob_crudo_se_recorta_conservando_cabeza_y_cola(tmp_path: Path):
    store = _make_store(tmp_path, max_bytes_blob_crudo=200)
    stdout_enorme = "INICIO-" + ("a" * 10_000) + "-FIN"
    evidencia = store.registrar_comando(
        "paso-5", argv=["algo"], stdout=stdout_enorme, stderr="", exit_code=0
    )
    assert evidencia["truncado"]["stdout"] is True
    crudo = store.leer_blob(evidencia["id"], "stdout").decode("utf-8")
    assert crudo.startswith("INICIO-")
    assert crudo.endswith("-FIN")
    assert len(crudo) <= 200


# --------------------------------------------------------------------- #
# Deduplicación del CAS.
# --------------------------------------------------------------------- #


def test_dos_evidencias_con_el_mismo_stdout_comparten_blob(tmp_path: Path):
    store = _make_store(tmp_path)
    e1 = store.registrar_comando("paso-6", argv=["a"], stdout="igual\n", stderr="", exit_code=0)
    e2 = store.registrar_comando("paso-6", argv=["b"], stdout="igual\n", stderr="", exit_code=0)
    assert e1["refs"]["stdout"] == e2["refs"]["stdout"]
    # Solo debe existir un blob físico para ese contenido.
    blobs = list(store.blobs_dir.rglob("*"))
    archivos = [b for b in blobs if b.is_file()]
    digest = e1["refs"]["stdout"]
    coincidencias = [b for b in archivos if b.name == digest]
    assert len(coincidencias) == 1


# --------------------------------------------------------------------- #
# Presentación conjunta: paquete_para_paso y el veredicto global.
# --------------------------------------------------------------------- #


def test_paquete_para_paso_agrupa_y_el_fallo_domina_el_veredicto_global(tmp_path: Path):
    store = _make_store(tmp_path)
    store.registrar_comando("paso-7", argv=["a"], stdout="9 passed", stderr="", exit_code=0)
    store.registrar_comando("paso-7", argv=["b"], stdout="8 passed", stderr="", exit_code=0)
    store.registrar_comando("paso-7", argv=["c"], stdout="1 failed", stderr="", exit_code=1)

    paquete = store.paquete_para_paso("paso-7")

    assert paquete["conteo"] == {"exito": 2, "fallo": 1, "indeterminado": 0}
    assert paquete["veredicto_global"] == "fallo"
    assert len(paquete["items"]) == 3


def test_paquete_para_paso_todo_exito_da_veredicto_global_exito(tmp_path: Path):
    store = _make_store(tmp_path)
    store.registrar_comando("paso-8", argv=["a"], stdout="ok", stderr="", exit_code=0)
    paquete = store.paquete_para_paso("paso-8")
    assert paquete["veredicto_global"] == "exito"


def test_paquete_para_paso_solo_capturas_da_indeterminado(tmp_path: Path):
    store = _make_store(tmp_path)
    store.registrar_captura("paso-9", imagen=b"\x89PNG\r\n\x1a\nfoo", mime_type="image/png")
    paquete = store.paquete_para_paso("paso-9")
    assert paquete["veredicto_global"] == "indeterminado"


def test_paquete_para_paso_vacio_es_indeterminado_sin_items(tmp_path: Path):
    store = _make_store(tmp_path)
    paquete = store.paquete_para_paso("paso-inexistente")
    assert paquete["items"] == []
    assert paquete["veredicto_global"] == "indeterminado"


def test_listar_para_paso_no_mezcla_pasos_distintos(tmp_path: Path):
    store = _make_store(tmp_path)
    store.registrar_comando("paso-a", argv=["x"], stdout="ok", stderr="", exit_code=0)
    store.registrar_comando("paso-b", argv=["y"], stdout="ok", stderr="", exit_code=0)
    assert len(store.listar_para_paso("paso-a")) == 1
    assert len(store.listar_para_paso("paso-b")) == 1


def test_listar_para_paso_ordena_cronologicamente(tmp_path: Path):
    store = _make_store(tmp_path)
    store.registrar_comando("paso-c", argv=["1"], stdout="uno", stderr="", exit_code=0)
    store.registrar_comando("paso-c", argv=["2"], stdout="dos", stderr="", exit_code=1)
    items = store.listar_para_paso("paso-c")
    assert [item["created_at_us"] for item in items] == sorted(
        item["created_at_us"] for item in items
    )


# --------------------------------------------------------------------- #
# Persistencia entre instancias (reinicio del proceso).
# --------------------------------------------------------------------- #


def test_evidencia_sobrevive_a_reinstanciar_el_almacen(tmp_path: Path):
    state_dir = tmp_path / "state"
    store1 = AlmacenEvidencia(state_dir)
    evidencia = store1.registrar_comando(
        "paso-persistente", argv=["x"], stdout="ok", stderr="", exit_code=0
    )

    store2 = AlmacenEvidencia(state_dir)
    recargada = store2.obtener(evidencia["id"])
    assert recargada["veredicto"] == "exito"
    assert store2.leer_blob(evidencia["id"], "stdout") == b"ok"


# --------------------------------------------------------------------- #
# Validaciones básicas de entrada.
# --------------------------------------------------------------------- #


def test_step_id_vacio_lanza_error(tmp_path: Path):
    store = _make_store(tmp_path)
    with pytest.raises(EvidenciaError):
        store.registrar_comando("   ", argv=["x"], stdout="", stderr="", exit_code=0)


def test_obtener_evidencia_inexistente_lanza_error(tmp_path: Path):
    store = _make_store(tmp_path)
    with pytest.raises(EvidenciaError):
        store.obtener("no-existe")


# --------------------------------------------------------------------- #
# Mantenimiento: descartar_paso y purgar_vencidas.
# --------------------------------------------------------------------- #


def test_descartar_paso_borra_solo_ese_paso(tmp_path: Path):
    store = _make_store(tmp_path)
    store.registrar_comando("paso-d1", argv=["x"], stdout="ok", stderr="", exit_code=0)
    store.registrar_comando("paso-d2", argv=["y"], stdout="ok", stderr="", exit_code=0)

    borrados = store.descartar_paso("paso-d1")

    assert borrados == 1
    assert store.listar_para_paso("paso-d1") == []
    assert len(store.listar_para_paso("paso-d2")) == 1


def test_purgar_vencidas_borra_evidencia_expirada_y_su_blob(tmp_path: Path):
    store = _make_store(tmp_path, ttl_hours=-1)  # ya nace vencida
    evidencia = store.registrar_comando(
        "paso-e", argv=["x"], stdout="contenido-unico-para-gc", stderr="", exit_code=0
    )
    digest = evidencia["refs"]["stdout"]
    blob_path = store._blob_path(digest)
    assert blob_path.is_file()

    resultado = store.purgar_vencidas()

    assert resultado["evidencias_removidas"] == 1
    assert resultado["blobs_removidos"] >= 1
    assert not blob_path.is_file()
    with pytest.raises(EvidenciaError):
        store.obtener(evidencia["id"])


def test_purgar_vencidas_no_toca_blobs_compartidos_con_evidencia_viva(tmp_path: Path):
    store = _make_store(tmp_path)
    # Dos evidencias con el mismo stdout: una vence pronto (simulada
    # forzando expires_at_us hacia atrás a mano), la otra sigue viva.
    vivo = store.registrar_comando(
        "paso-f1", argv=["x"], stdout="compartido", stderr="", exit_code=0
    )
    vencido = store.registrar_comando(
        "paso-f2", argv=["y"], stdout="compartido", stderr="", exit_code=0
    )
    item_vencido = store._load(vencido["id"])
    item_vencido.expires_at_us = 0
    store._save(item_vencido)

    store.purgar_vencidas()

    assert store.leer_blob(vivo["id"], "stdout") == b"compartido"
