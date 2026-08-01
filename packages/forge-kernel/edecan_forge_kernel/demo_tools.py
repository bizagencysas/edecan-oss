"""Prueba y Demostración de la Fase D: ABI de Tools y Inyección de JSON Schema."""

from __future__ import annotations

import tempfile
from pathlib import Path

from edecan_forge_kernel.cas import Cas
from edecan_forge_kernel.tools import ToolCall, ToolRegistry
from edecan_forge_kernel.vfs import Vfs


def main() -> None:
    print("============================================================")
    print("       DEMO DE LA FASE D: ABI DE TOOLS Y DISPATCHER        ")
    print("============================================================\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        cas = Cas(root=Path(tmpdir) / "cas")
        vfs = Vfs(cas=cas)
        registry = ToolRegistry(vfs=vfs)

        # 1. Inspeccionar JSON Schemas inyectables para el modelo LLM
        schemas = registry.get_tool_schemas()
        print(f"1. Schemas de herramientas generadas ({len(schemas)} registradas):")
        for s in schemas:
            print(f"   - {s['function']['name']}: {s['function']['description']}")
        print("   [OK] Schemas JSON válidos e inyectables.\n")

        # 2. Ejecutar write_file
        print("2. Ejecutando llamada a 'write_file'...")
        call_write = ToolCall(
            call_id="call_001",
            tool_name="write_file",
            arguments={"path": "src/app.py", "content": "def run():\n    print('Hello Forge')\n"},
        )
        res_write = registry.dispatch(call_write)
        print(f"   Resultado: status={res_write.status!r}, output={res_write.output!r}")
        assert res_write.status == "ok"
        assert vfs.exists("src/app.py")
        print("   [OK] Archivo escrito en VFS mediante ABI.\n")

        # 3. Ejecutar read_file_window
        print("3. Ejecutando llamada a 'read_file_window'...")
        call_read = ToolCall(
            call_id="call_002",
            tool_name="read_file_window",
            arguments={"path": "src/app.py", "start_line": 1, "max_lines": 10},
        )
        res_read = registry.dispatch(call_read)
        print("   Salida paginada:")
        print(res_read.output)
        assert "Hello Forge" in res_read.output
        print("   [OK] Lectura por ventana correcta.\n")

        # 4. Ejecutar apply_text_edit
        print("4. Ejecutando llamada a 'apply_text_edit'...")
        call_edit = ToolCall(
            call_id="call_003",
            tool_name="apply_text_edit",
            arguments={
                "path": "src/app.py",
                "target_content": "print('Hello Forge')",
                "replacement_content": "print('Hello Forge ABI Phase D')",
            },
        )
        res_edit = registry.dispatch(call_edit)
        print(f"   Resultado: status={res_edit.status!r}, output={res_edit.output!r}")
        assert res_edit.status == "ok"
        assert "Phase D" in vfs.read_text("src/app.py")
        print("   [OK] Edición de texto anclada exitosa.\n")

        # 5. Ejecutar run_command con ExecWindow
        print("5. Ejecutando llamada a 'run_command' (echo 'Forge OK')...")
        call_cmd = ToolCall(
            call_id="call_004",
            tool_name="run_command",
            arguments={"command": "echo 'Forge OK'"},
        )
        res_cmd = registry.dispatch(call_cmd)
        print(f"   Resultado: status={res_cmd.status!r}")
        print(f"   Salida:\n{res_cmd.output}")
        assert res_cmd.status == "ok"
        assert "Forge OK" in res_cmd.output
        print("   [OK] Ejecución de comando aislada completada.\n")

        print("============================================================")
        print("          VEREDICTO FASE D: ABI DE TOOLS EN VERDE           ")
        print("============================================================")


if __name__ == "__main__":
    main()
