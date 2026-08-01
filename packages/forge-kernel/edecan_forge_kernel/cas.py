"""Almacén direccionado por contenido (CAS) — invariante 4 del proyecto.

El journal transporta `CasRef` (`edecan_forge_kernel.contracts.CasRef`), nunca payloads
grandes: un stdout de compilación, un razonamiento de 3 KB, un archivo de 2 MB viven aquí, no
en `payload_inline` (que además lo prohibiría: §1.2 limita eso a 4 KiB de datos puramente
estructurales). Este módulo es la única pieza del kernel que toca el disco de verdad para
guardar bytes; todo lo demás del paquete es puro.

Cuatro decisiones de diseño que conviene dejar explícitas, porque no están en el encargo
palabra por palabra:

1. **Layout en disco por prefijo de hash**, `blobs/<algoritmo>/<2 hex>/<2 hex>/<64 hex>` — evita
   meter cientos de miles de archivos en un único directorio (algunos sistemas de archivos
   degradan mucho por encima de ~10⁴ entradas por directorio).
2. **Metadatos en SQLite**, nunca codificados en el nombre de archivo — el nombre de archivo es
   exactamente el `digest`, así que el `content_type`/tamaño/instante de creación no tienen
   dónde vivir salvo una tabla aparte. `INSERT OR IGNORE` hace que declarar el mismo blob dos
   veces sea un no-op, no un error.
3. **GC barre el DISCO, no la tabla de metadatos.** La escritura atómica primero renombra el
   blob a su destino final y DESPUÉS inserta la fila de metadatos (`_escribir_atomico` /
   `_registrar_metadatos`) — si el proceso muere justo entre esas dos operaciones, el blob queda
   completo en disco pero sin fila. Si `gc()` mirara solo la tabla, ese blob sería invisible
   para siempre: ni se cuenta como vivo (nadie lo pinea porque nadie lo conoce) ni se destruye
   nunca (no aparece en el barrido). Recorrer el árbol de directorios real es la única forma de
   que el "marca y barre" sea honesto sobre lo que HAY, no sobre lo que el DB cree que hay.
4. **Ningún método de escritura carga un blob grande entero en memoria.** `poner()` acepta una
   ruta y la transmite en trozos de 1 MiB mientras calcula el hash incremental; `abrir()`
   devuelve un descriptor de archivo en modo binario para que el llamador itere sin materializar
   el contenido. `obtener()` sí carga todo en memoria a propósito: es la vía cómoda para blobs
   pequeños (la mayoría), y forzar streaming ahí solo movería la complejidad al 95 % de los
   llamadores que no la necesitan.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from pydantic import BaseModel, Field

from edecan_forge_kernel.contracts import CasRef

_TAMANO_TROZO = 1024 * 1024
"""1 MiB — tamaño de trozo para leer/hashear en streaming. No es una constante mágica: es lo
bastante grande para que la sobrecarga de la llamada a `read()` sea despreciable frente al I/O
real, y lo bastante pequeño para que un blob de varios GB nunca resida entero en memoria."""


def _reintentando_bloqueo(operacion) -> None:
    """Ejecuta ``operacion`` (un `Callable[[], None]`, típicamente `os.replace` o
    `Path.unlink`) reintentando ante `PermissionError` transitorio en Windows.

    Decisión del plan (docs/edecan-windows.md §"Archivos bloqueados"): en Windows un
    antivirus o un cliente de sincronización puede tener el blob abierto un instante
    justo cuando el CAS intenta reemplazarlo (rename atómico de staging) o destruirlo
    (`destruir()`/`gc()`) — en POSIX el mismo `rename`/`unlink` nunca falla así porque el
    inodo se puede reemplazar/borrar aunque otro proceso tenga el archivo abierto. 5
    intentos con backoff 50→800 ms; SOLO se reintenta en `os.name == "nt"` y solo
    `PermissionError` — en POSIX un `PermissionError` real no se arregla reintentando.
    """
    intentos = 5
    espera = 0.05
    for intento in range(intentos):
        try:
            operacion()
            return
        except PermissionError:
            if os.name != "nt" or intento == intentos - 1:
                raise
            time.sleep(espera)
            espera = min(espera * 2, 0.8)


class BlobNoEncontradoError(LookupError):
    """`obtener()` / `abrir()` sobre un `CasRef` que no existe en el almacén (nunca se puso, o
    ya se destruyó por `destruir()`/`gc()`). Es un `LookupError`, no un `ValueError`: el `CasRef`
    en sí es válido, lo que falta es el blob."""


class BlobMetadata(BaseModel, frozen=True):
    """Metadatos de un blob — §encargo: "tamaño, tipo de contenido declarado, instante de
    creación. En SQLite, no en nombres de archivo". `content_type` es lo que el PRODUCTOR
    declaró (p. ej. `"text/plain"`, `"application/x-compiler-stdout"`); el CAS no lo infiere ni
    lo valida, igual que `ModelCard` nunca rellena a mano lo que no midió — aquí nunca se adivina
    lo que el llamador no dijo."""

    ref: CasRef
    size_bytes: int = Field(ge=0)
    content_type: str | None = None
    created_at_us: int = Field(ge=0)


@dataclass(frozen=True, slots=True)
class ResultadoGc:
    """Resultado de una pasada de `gc()` — qué se destruyó y cuántos bytes liberó, para que el
    host pueda registrarlo (p. ej. como `kernel.snapshot_written` o una observación propia) sin
    que este módulo tenga que saber nada del journal."""

    destruidos: tuple[CasRef, ...]
    bytes_liberados: int


class Cas:
    """Almacén direccionado por contenido, con un directorio raíz propio.

    Estructura en disco bajo `root`:

    ```
    root/
      blobs/<algoritmo>/<2 hex>/<2 hex>/<64 hex>   # el blob, nombre = digest completo
      tmp/.cas-XXXXXXXX                             # staging de escritura atómica
      cas.sqlite3                                   # metadatos (tamaño, tipo, creación)
    ```

    `tmp/` vive DENTRO de `root/` a propósito: `os.replace` (la primitiva atómica de
    renombrado) solo es atómica si origen y destino están en el mismo sistema de archivos. Si el
    staging viviera en `/tmp` del sistema y `root` en otro punto de montaje, el "rename atómico"
    degradaría en silencio a una copia no atómica en algunas plataformas.
    """

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)
        self._dir_blobs = self._root / "blobs"
        self._dir_tmp = self._root / "tmp"
        self._dir_blobs.mkdir(parents=True, exist_ok=True)
        self._dir_tmp.mkdir(parents=True, exist_ok=True)
        self._db_path = self._root / "cas.sqlite3"
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS blobs (
                algorithm TEXT NOT NULL,
                digest TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                content_type TEXT,
                created_at_us INTEGER NOT NULL,
                PRIMARY KEY (algorithm, digest)
            )
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Cas:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ----------------------------------------------------------------------------------- #
    # Layout en disco
    # ----------------------------------------------------------------------------------- #

    def _ruta_para(self, ref: CasRef) -> Path:
        """`blobs/<algoritmo>/<2 hex>/<2 hex>/<64 hex>` — dos niveles de prefijo de 2 hex dan
        256 * 256 = 65 536 subdirectorios posibles, suficiente para repartir incluso cientos de
        miles de blobs sin acercarse al límite práctico de entradas por directorio."""
        d = ref.digest
        return self._dir_blobs / ref.algorithm / d[0:2] / d[2:4] / d

    def existe(self, ref: CasRef) -> bool:
        return self._ruta_para(ref).is_file()

    def metadatos(self, ref: CasRef) -> BlobMetadata | None:
        """`None` si el blob no está registrado — nunca inventa un valor por defecto para
        `size_bytes`/`created_at_us` cuando no hay fila, ni siquiera si el archivo existe en
        disco huérfano de metadatos (ver punto 3 del docstring del módulo)."""
        fila = self._conn.execute(
            "SELECT size_bytes, content_type, created_at_us FROM blobs "
            "WHERE algorithm = ? AND digest = ?",
            (ref.algorithm, ref.digest),
        ).fetchone()
        if fila is None:
            return None
        size_bytes, content_type, created_at_us = fila
        return BlobMetadata(
            ref=ref,
            size_bytes=size_bytes,
            content_type=content_type,
            created_at_us=created_at_us,
        )

    # ----------------------------------------------------------------------------------- #
    # Escritura
    # ----------------------------------------------------------------------------------- #

    def poner(self, contenido: bytes | Path | str, *, tipo_contenido: str | None = None) -> CasRef:
        """Guarda `contenido` (bytes ya en memoria, o una ruta a leer en streaming) y devuelve su
        `CasRef`. Deduplica por hash: si el blob ya existe, no se vuelve a escribir un byte —
        solo se descarta el archivo de staging y se confirma que el `CasRef` es el correcto.

        `contenido` puede ser:
        - `bytes`/`bytearray`: se hashea y escribe directamente, sin trocear (ya está entero en
          memoria; trocearlo no ahorraría nada).
        - `Path`/`str`: se abre y se lee en trozos de `_TAMANO_TROZO`, hasheando de forma
          incremental — un archivo de 2 GB nunca reside entero en memoria del proceso.
        """
        if isinstance(contenido, bytes | bytearray):
            trozos: Iterator[bytes] = iter((bytes(contenido),))
            return self._escribir_atomico(trozos, tipo_contenido=tipo_contenido)
        ruta_origen = Path(contenido)
        with ruta_origen.open("rb") as f:
            trozos = iter(lambda: f.read(_TAMANO_TROZO), b"")
            return self._escribir_atomico(trozos, tipo_contenido=tipo_contenido)

    def _escribir_atomico(self, trozos: Iterator[bytes], *, tipo_contenido: str | None) -> CasRef:
        """El único camino de escritura del CAS. Garantiza que NUNCA es visible un blob a
        medias: todo se escribe primero a un archivo de staging en `tmp/`, y el archivo final
        solo aparece mediante `os.replace` (rename atómico POSIX) cuando el contenido completo
        ya está en disco (`fsync` antes del rename). Si cualquier paso previo al rename revienta
        — incluido el propio `fsync` — el `except` limpia el archivo de staging y relanza; no
        queda ni un archivo temporal huérfano ni un destino parcial.
        """
        hasher = hashlib.blake2b(digest_size=32)
        fd, nombre_tmp = tempfile.mkstemp(dir=self._dir_tmp, prefix=".cas-")
        ruta_tmp = Path(nombre_tmp)
        tamano = 0
        try:
            with os.fdopen(fd, "wb") as tmp_f:
                for trozo in trozos:
                    hasher.update(trozo)
                    tmp_f.write(trozo)
                    tamano += len(trozo)
                tmp_f.flush()
                os.fsync(tmp_f.fileno())

            ref = CasRef(algorithm="b2b", digest=hasher.hexdigest())
            destino = self._ruta_para(ref)
            if destino.is_file():
                # Deduplicación: el contenido ya está guardado bajo este digest. El staging es
                # descartado sin tocar el blob existente — dos `poner()` del mismo contenido
                # nunca duplican bytes en disco.
                ruta_tmp.unlink(missing_ok=True)
            else:
                destino.parent.mkdir(parents=True, exist_ok=True)
                _reintentando_bloqueo(lambda: os.replace(ruta_tmp, destino))
                self._fsync_dir(destino.parent)
            self._registrar_metadatos(ref, tamano, tipo_contenido)
            return ref
        except BaseException:
            ruta_tmp.unlink(missing_ok=True)
            raise

    def _fsync_dir(self, ruta: Path) -> None:
        """`fsync` del directorio contenedor tras el rename. Sin esto, algunos sistemas de
        archivos pueden, ante un fallo justo después del rename, perder la entrada de directorio
        aunque el contenido del archivo ya esté durable — la mitad "atómica" del rename no cubre
        por sí sola la durabilidad del directorio que lo contiene. Es best-effort a propósito:
        una plataforma que no permita abrir un directorio con `os.open` (Windows) no debe tirar
        abajo una escritura que ya completó con éxito."""
        try:
            fd = os.open(ruta, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)

    def _registrar_metadatos(
        self, ref: CasRef, size_bytes: int, tipo_contenido: str | None
    ) -> None:
        """`INSERT OR IGNORE`: si la fila ya existe (dedup, o una segunda llamada concurrente),
        no se sobreescribe — el primer `content_type` declarado para un digest dado gana, porque
        el digest identifica el CONTENIDO, no la intención declarada de cada llamador."""
        ahora_us = int(time.time() * 1_000_000)
        self._conn.execute(
            "INSERT OR IGNORE INTO blobs "
            "(algorithm, digest, size_bytes, content_type, created_at_us) VALUES (?, ?, ?, ?, ?)",
            (ref.algorithm, ref.digest, size_bytes, tipo_contenido, ahora_us),
        )
        self._conn.commit()

    # ----------------------------------------------------------------------------------- #
    # Lectura
    # ----------------------------------------------------------------------------------- #

    def obtener(self, ref: CasRef) -> bytes:
        """Lee el blob entero en memoria. Vía cómoda para blobs pequeños; para algo de varios MB
        usar `abrir()`."""
        ruta = self._ruta_para(ref)
        try:
            return ruta.read_bytes()
        except FileNotFoundError as exc:
            raise BlobNoEncontradoError(f"blob no encontrado en CAS: {ref}") from exc

    def abrir(self, ref: CasRef) -> BinaryIO:
        """Abre el blob en modo binario para lectura en streaming — usar con `with cas.abrir(ref)
        as f: ...`. Es la vía obligatoria para blobs grandes: nunca materializa el contenido
        entero, el llamador decide su propio tamaño de trozo con `f.read(n)`."""
        ruta = self._ruta_para(ref)
        try:
            return ruta.open("rb")
        except FileNotFoundError as exc:
            raise BlobNoEncontradoError(f"blob no encontrado en CAS: {ref}") from exc

    # ----------------------------------------------------------------------------------- #
    # Borrado: destrucción puntual y GC de marca y barre
    # ----------------------------------------------------------------------------------- #

    def destruir(self, ref: CasRef) -> bool:
        """Borrado físico e IDEMPOTENTE de un blob concreto — para redacción de secretos y
        peticiones de borrado explícitas. Devuelve si el blob EXISTÍA (archivo o fila de
        metadatos) antes de esta llamada; una segunda llamada sobre el mismo `ref` es un no-op
        que devuelve `False`, nunca un error."""
        ruta = self._ruta_para(ref)
        existia_archivo = ruta.is_file()
        if existia_archivo:
            _reintentando_bloqueo(ruta.unlink)
        cursor = self._conn.execute(
            "DELETE FROM blobs WHERE algorithm = ? AND digest = ?", (ref.algorithm, ref.digest)
        )
        existia_fila = cursor.rowcount > 0
        self._conn.commit()
        return existia_archivo or existia_fila

    def _listar_refs_en_disco(self) -> list[CasRef]:
        """Recorre `blobs/` de verdad — no la tabla de metadatos — y devuelve el `CasRef` de
        cada archivo encontrado. Es la base de `gc()`: ver el punto 3 del docstring del módulo
        sobre por qué el barrido tiene que mirar el disco."""
        refs: list[CasRef] = []
        if not self._dir_blobs.is_dir():
            return refs
        for dir_algoritmo in self._dir_blobs.iterdir():
            if not dir_algoritmo.is_dir():
                continue
            algoritmo = dir_algoritmo.name
            for ruta in dir_algoritmo.rglob("*"):
                if ruta.is_file():
                    refs.append(CasRef(algorithm=algoritmo, digest=ruta.name))
        return refs

    def gc(self, pines: Iterable[CasRef]) -> ResultadoGc:
        """Marca y barre — SIN contar referencias, tal como exige el encargo. `pines` es el
        conjunto COMPLETO de `CasRef` vivos en este instante, ya resuelto por el llamador a
        partir de los `payload_ref` de todo evento del journal que sigue existiendo (un evento
        redactado, o un stream truncado, deja de pinear lo que solo él referenciaba). Este
        método no conoce el journal en absoluto: solo compara el conjunto de disco contra el
        conjunto recibido. Cualquier blob que no esté en `pines` se destruye; nada que esté en
        `pines` se toca jamás, sin importar cuántas veces (cero, una, muchas) lo pinee alguien —
        de ahí "marca y barre" en vez de un contador que se pueda decrementar de más por error.
        """
        marcados = {(p.algorithm, p.digest) for p in pines}
        destruidos: list[CasRef] = []
        bytes_liberados = 0
        for ref in self._listar_refs_en_disco():
            if (ref.algorithm, ref.digest) in marcados:
                continue
            ruta = self._ruta_para(ref)
            try:
                tamano = ruta.stat().st_size
            except FileNotFoundError:
                tamano = 0
            if self.destruir(ref):
                destruidos.append(ref)
                bytes_liberados += tamano
        return ResultadoGc(destruidos=tuple(destruidos), bytes_liberados=bytes_liberados)
