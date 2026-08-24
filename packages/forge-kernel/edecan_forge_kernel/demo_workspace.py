"""Demo de la Fase C — Virtual File System (VFS), Copy-on-Write y Reconciliación.

Criterios de aceptación comprobados:
1. `fork()` de un árbol masivo (200.000 ficheros sintéticos) en < 200 ms.
2. Escrituras concurrentes en conflicto detectadas limpiamente con VfsConflictError.
3. Reconciliación de ExecWindow tras modificación externa sin pérdida de datos.
4. Rechazo estricto de PathTraversalError ('..', rutas absolutas, etc.).
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from edecan_forge_kernel.cas import Cas
from edecan_forge_kernel.vfs import (
    ExecWindow,
    PathTraversalError,
    Vfs,
    VfsConflictError,
    VfsDirEntry,
    VfsFileEntry,
    classify_file,
)


def main() -> None:
    print("============================================================")
    print("       DEMO DE LA FASE C: VFS Y WORKSPACE COPY-ON-WRITE     ")
    print("============================================================\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        cas = Cas(root=Path(tmpdir) / "cas")

        # 1. benchmark de fork() con 200.000 archivos sintéticos
        print("1. Generando árbol sintético con 200,000 entradas...")
        vfs_base = Vfs(cas=cas)

        # Usar la misma referencia de contenido para simular duplicación masiva CAS
        content_ref = cas.poner(b"import os\nprint('hello world')\n")
        dummy_class = classify_file("dummy.py", b"import os\nprint('hello world')\n")
        dummy_file_entry = VfsFileEntry(
            cas_ref=content_ref,
            size_bytes=28,
            executable=False,
            classification=dummy_class,
        )

        # Construir árbol directamente a nivel de estructuras de datos inmutables
        # 200 directorios x 1000 archivos
        t0 = time.perf_counter()
        pkg_entries: dict[str, VfsDirEntry] = {}
        for d in range(200):
            dir_name = f"pkg_{d:03d}"
            module_entries: dict[str, VfsFileEntry] = {
                f"module_{f:03d}.py": dummy_file_entry for f in range(1000)
            }
            pkg_entries[dir_name] = VfsDirEntry(entries=module_entries)

        vfs_base.root_tree = VfsDirEntry(entries=pkg_entries)
        vfs_base.version = 2
        t_gen = (time.perf_counter() - t0) * 1000
        print(f"   Árbol base creado con {len(vfs_base.list_files())} archivos en {t_gen:.2f} ms.")

        print("   Midiendo tiempo de workspace.fork()...")
        t_fork_start = time.perf_counter()
        _ = vfs_base.fork()
        t_fork_ms = (time.perf_counter() - t_fork_start) * 1000

        print(f"   [RESULTADO] fork() completado en {t_fork_ms:.2f} ms. (Criterio: < 200 ms)")
        assert t_fork_ms < 200.0, f"fork() fue demasiado lento: {t_fork_ms} ms"
        print("   [OK] Criterio 1 APROBADO.\n")

        # 2. Escrituras concurrentes en conflicto
        print("2. Verificando detección de conflictos de concurrencia...")
        vfs_shared = Vfs(cas=cas)
        vfs_shared.begin_txn().write_text("config.py", "VERSION = 1\n").commit()

        txn1 = vfs_shared.begin_txn()
        txn2 = vfs_shared.begin_txn()

        txn1.write_text("config.py", "VERSION = 2  # Agente 1\n")
        txn1.commit()
        print("   Agente 1 escribió versión 2 exitosamente.")

        txn2.write_text("config.py", "VERSION = 3  # Agente 2\n")
        conflicto_detectado = False
        try:
            txn2.commit()
        except VfsConflictError as err:
            conflicto_detectado = True
            print(f"   Agente 2 bloqueado por conflicto tipado: {err}")

        assert conflicto_detectado, "Se esperaba un VfsConflictError"
        print("   [OK] Criterio 2 APROBADO.\n")

        # 3. ExecWindow y Reconciliación sin pérdida de datos
        print("3. Probando ExecWindow con reconciliación externa...")
        vfs_exec = Vfs(cas=cas)
        txn_exec = vfs_exec.begin_txn()
        txn_exec.write_text("src/main.py", "def main():\n    print('agente')\n")
        txn_exec.commit()

        exec_dir = Path(tmpdir) / "exec_build"
        window = ExecWindow(vfs=vfs_exec, target_dir=exec_dir)
        window.sync_vfs_to_disk()
        print("   Archivos sincronizados a disco para build externo.")

        # Proceso externo añade un binario/artefacto
        (exec_dir / "build.log").write_text("Build succeeded with status 0\n")
        (exec_dir / "src/main.py").write_text("def main():\n    print('agente + build')\n")

        window.reconcile_from_disk()
        print("   Reconciliación desde disco completada.")
        contenido_reconciliado = vfs_exec.read_text("src/main.py")
        assert "agente + build" in contenido_reconciliado
        assert vfs_exec.exists("build.log")
        print(f"   [RESULTADO] Contenido reconciliado: {contenido_reconciliado.strip()!r}")
        print("   [OK] Criterio 3 APROBADO.\n")

        # 4. Prueba de seguridad PathTraversalError
        print("4. Verificando protección contra Path Traversal...")
        vfs_sec = Vfs(cas=cas)
        txn_sec = vfs_sec.begin_txn()
        traversal_bloqueado = False
        try:
            txn_sec.write_bytes("../../../etc/passwd", b"hacked")
        except PathTraversalError as err:
            traversal_bloqueado = True
            print(f"   Path traversal denegado correctamente: {err}")

        assert traversal_bloqueado, "PathTraversalError debía haber sido lanzado"
        print("   [OK] Criterio 4 APROBADO.\n")

        print("============================================================")
        print("          VEREDICTO FASE C: TODAS LAS PRUEBAS EN VERDE      ")
        print("============================================================")


if __name__ == "__main__":
    main()
