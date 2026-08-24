from __future__ import annotations

import os
from pathlib import Path

import pytest
from edecan_forge_kernel.cas import BlobNoEncontradoError, Cas
from edecan_forge_kernel.contracts import CasRef


@pytest.fixture
def cas(tmp_path: Path) -> Cas:
    almacen = Cas(tmp_path / "cas")
    yield almacen
    almacen.close()


# --------------------------------------------------------------------------------------- #
# Ida y vuelta
# --------------------------------------------------------------------------------------- #


def test_poner_bytes_y_obtener_da_el_mismo_contenido(cas: Cas) -> None:
    ref = cas.poner(b"hola mundo")
    assert cas.obtener(ref) == b"hola mundo"


def test_poner_ruta_y_obtener_da_el_mismo_contenido(cas: Cas, tmp_path: Path) -> None:
    origen = tmp_path / "origen.txt"
    origen.write_bytes(b"contenido desde archivo")
    ref = cas.poner(origen)
    assert cas.obtener(ref) == b"contenido desde archivo"


def test_poner_bytes_y_poner_ruta_del_mismo_contenido_dan_el_mismo_ref(
    cas: Cas, tmp_path: Path
) -> None:
    origen = tmp_path / "origen.txt"
    origen.write_bytes(b"mismo contenido")
    ref_bytes = cas.poner(b"mismo contenido")
    ref_ruta = cas.poner(origen)
    assert ref_bytes == ref_ruta


def test_ref_devuelto_coincide_con_el_hash_calculado_directamente(cas: Cas) -> None:
    contenido = b"verificable"
    ref = cas.poner(contenido)
    assert ref == CasRef.from_bytes(contenido)


def test_contenido_vacio_es_valido(cas: Cas) -> None:
    ref = cas.poner(b"")
    assert cas.obtener(ref) == b""
    assert cas.existe(ref)


def test_obtener_de_ref_inexistente_lanza(cas: Cas) -> None:
    ref = CasRef.from_bytes(b"nunca puesto")
    with pytest.raises(BlobNoEncontradoError):
        cas.obtener(ref)


def test_abrir_de_ref_inexistente_lanza(cas: Cas) -> None:
    ref = CasRef.from_bytes(b"nunca puesto")
    with pytest.raises(BlobNoEncontradoError):
        cas.abrir(ref)


# --------------------------------------------------------------------------------------- #
# Metadatos
# --------------------------------------------------------------------------------------- #


def test_metadatos_registra_tamano_y_tipo_declarado(cas: Cas) -> None:
    ref = cas.poner(b"1234567890", tipo_contenido="text/plain")
    meta = cas.metadatos(ref)
    assert meta is not None
    assert meta.size_bytes == 10
    assert meta.content_type == "text/plain"
    assert meta.created_at_us > 0


def test_metadatos_de_ref_inexistente_es_none(cas: Cas) -> None:
    ref = CasRef.from_bytes(b"nunca puesto")
    assert cas.metadatos(ref) is None


# --------------------------------------------------------------------------------------- #
# Deduplicación
# --------------------------------------------------------------------------------------- #


def test_poner_dos_veces_lo_mismo_no_duplica_bytes_en_disco(cas: Cas) -> None:
    ref1 = cas.poner(b"contenido repetido")
    ref2 = cas.poner(b"contenido repetido")
    assert ref1 == ref2
    ruta = cas._ruta_para(ref1)  # inspección interna deliberada: comprobar que hay UN archivo
    assert ruta.is_file()
    # El directorio del prefijo de 2 hex contiene exactamente un blob con este digest.
    coincidencias = [p for p in ruta.parent.iterdir() if p.name == ref1.digest]
    assert len(coincidencias) == 1


def test_poner_dos_veces_no_deja_temporales_huerfanos(cas: Cas) -> None:
    cas.poner(b"contenido repetido otra vez")
    cas.poner(b"contenido repetido otra vez")
    assert list(cas._dir_tmp.iterdir()) == []


# --------------------------------------------------------------------------------------- #
# Atomicidad ante fallo a mitad de escritura
# --------------------------------------------------------------------------------------- #


def test_fallo_antes_del_rename_no_deja_basura_visible(
    cas: Cas, monkeypatch: pytest.MonkeyPatch
) -> None:
    def replace_que_revienta(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulación de crash justo antes del rename")

    monkeypatch.setattr(os, "replace", replace_que_revienta)

    with pytest.raises(OSError, match="simulación de crash"):
        cas.poner(b"esto nunca debe quedar visible")

    ref = CasRef.from_bytes(b"esto nunca debe quedar visible")
    assert not cas.existe(ref)
    assert cas.metadatos(ref) is None
    # Ni un archivo de staging huérfano en tmp/.
    assert list(cas._dir_tmp.iterdir()) == []
    # Ni un archivo parcial visible bajo blobs/ — puede haberse creado el subdirectorio de
    # prefijo vacío (mkdir es idempotente y se hace antes del rename), pero eso no es un blob.
    assert [p for p in cas._dir_blobs.rglob("*") if p.is_file()] == []


def test_fallo_durante_fsync_no_deja_basura_visible(
    cas: Cas, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fsync_que_revienta(_fd: int) -> None:
        raise OSError("simulación de crash durante fsync")

    monkeypatch.setattr(os, "fsync", fsync_que_revienta)

    with pytest.raises(OSError, match="simulación de crash"):
        cas.poner(b"tampoco esto debe quedar visible")

    assert list(cas._dir_tmp.iterdir()) == []
    assert not any(cas._dir_blobs.rglob("*"))


def test_escritura_exitosa_tras_el_fallo_funciona_normalmente(
    cas: Cas, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El fallo simulado no debe dejar el `Cas` en un estado roto: una escritura posterior,
    sin el mock, tiene que funcionar exactamente igual que si el fallo nunca hubiera ocurrido."""
    original_replace = os.replace
    llamadas = {"n": 0}

    def replace_que_revienta_una_vez(*args: object, **kwargs: object) -> None:
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            raise OSError("crash simulado en el primer intento")
        return original_replace(*args, **kwargs)

    monkeypatch.setattr(os, "replace", replace_que_revienta_una_vez)

    with pytest.raises(OSError):
        cas.poner(b"primer intento falla")

    ref = cas.poner(b"primer intento falla")  # segundo intento, ya sin fallar de nuevo
    assert cas.obtener(ref) == b"primer intento falla"


# --------------------------------------------------------------------------------------- #
# Streaming de blobs grandes
# --------------------------------------------------------------------------------------- #


def test_poner_y_abrir_un_blob_grande_en_streaming(cas: Cas, tmp_path: Path) -> None:
    origen = tmp_path / "grande.bin"
    trozo = os.urandom(1024 * 1024)  # 1 MiB de datos pseudoaleatorios
    with origen.open("wb") as f:
        for _ in range(5):  # 5 MiB en total
            f.write(trozo)

    ref = cas.poner(origen)
    assert cas.metadatos(ref).size_bytes == 5 * 1024 * 1024  # type: ignore[union-attr]

    reconstituido = bytearray()
    with cas.abrir(ref) as f:
        while True:
            leido = f.read(1024 * 1024)
            if not leido:
                break
            reconstituido.extend(leido)
    assert bytes(reconstituido) == origen.read_bytes()


def test_abrir_no_carga_el_archivo_entero_de_una_vez(cas: Cas, tmp_path: Path) -> None:
    """No es una prueba de memoria real (difícil de medir de forma fiable en CI), pero sí
    verifica el contrato observable de streaming: leer en trozos pequeños funciona y produce
    exactamente el contenido esperado sin que `abrir()` haya materializado nada por su cuenta."""
    origen = tmp_path / "grande.bin"
    contenido = os.urandom(3 * 1024 * 1024)
    origen.write_bytes(contenido)
    ref = cas.poner(origen)

    with cas.abrir(ref) as f:
        primer_trozo = f.read(1024)
        assert len(primer_trozo) == 1024
        assert primer_trozo == contenido[:1024]
        resto = f.read()
    assert primer_trozo + resto == contenido


# --------------------------------------------------------------------------------------- #
# Destrucción puntual
# --------------------------------------------------------------------------------------- #


def test_destruir_borra_el_blob_y_es_idempotente(cas: Cas) -> None:
    ref = cas.poner(b"secreto a redactar")
    assert cas.destruir(ref) is True
    assert not cas.existe(ref)
    assert cas.metadatos(ref) is None
    with pytest.raises(BlobNoEncontradoError):
        cas.obtener(ref)
    # Segunda llamada: no existía, no hace nada, no lanza.
    assert cas.destruir(ref) is False


def test_destruir_de_ref_nunca_puesto_devuelve_false(cas: Cas) -> None:
    ref = CasRef.from_bytes(b"nunca existio")
    assert cas.destruir(ref) is False


# --------------------------------------------------------------------------------------- #
# GC: marca y barre, respetando pines
# --------------------------------------------------------------------------------------- #


def test_gc_borra_lo_no_pineado_y_respeta_lo_pineado(cas: Cas) -> None:
    ref_pineado = cas.poner(b"vivo: referenciado por el journal")
    ref_huerfano_1 = cas.poner(b"huerfano uno")
    ref_huerfano_2 = cas.poner(b"huerfano dos")

    resultado = cas.gc(pines=[ref_pineado])

    assert cas.existe(ref_pineado)
    assert cas.obtener(ref_pineado) == b"vivo: referenciado por el journal"
    assert not cas.existe(ref_huerfano_1)
    assert not cas.existe(ref_huerfano_2)
    assert set(resultado.destruidos) == {ref_huerfano_1, ref_huerfano_2}
    assert resultado.bytes_liberados == len(b"huerfano uno") + len(b"huerfano dos")


def test_gc_sin_pines_borra_todo(cas: Cas) -> None:
    cas.poner(b"a")
    cas.poner(b"bb")
    resultado = cas.gc(pines=[])
    assert len(resultado.destruidos) == 2
    assert cas._listar_refs_en_disco() == []


def test_gc_no_borra_nada_si_todo_esta_pineado(cas: Cas) -> None:
    ref1 = cas.poner(b"uno")
    ref2 = cas.poner(b"dos")
    resultado = cas.gc(pines=[ref1, ref2])
    assert resultado.destruidos == ()
    assert resultado.bytes_liberados == 0
    assert cas.existe(ref1)
    assert cas.existe(ref2)


def test_gc_es_marca_y_barre_no_conteo_de_referencias(cas: Cas) -> None:
    """Pinear el mismo ref varias veces (como si dos eventos distintos del journal apuntaran al
    mismo blob) no debe comportarse distinto a pinearlo una sola vez — no hay contador que
    decrementar, solo pertenencia al conjunto."""
    ref = cas.poner(b"referenciado dos veces")
    resultado = cas.gc(pines=[ref, ref, ref])
    assert resultado.destruidos == ()
    assert cas.existe(ref)


def test_gc_es_repetible_sin_efectos_extra(cas: Cas) -> None:
    ref_pineado = cas.poner(b"vivo")
    cas.poner(b"huerfano")
    primera = cas.gc(pines=[ref_pineado])
    segunda = cas.gc(pines=[ref_pineado])
    assert len(primera.destruidos) == 1
    assert segunda.destruidos == ()
    assert segunda.bytes_liberados == 0
