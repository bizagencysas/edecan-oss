# apps/local — `edecan_local`

Esqueleto de la app v3 (`ARCHITECTURE.md` §12, fase v3) que empaqueta `api` + `worker` + `db` para correr LOCAL en la máquina del cliente — el backend de la app de escritorio Tauri (`docs/roadmap.md`). Lo completa fase v3: runner `python -m edecan_local`, bind solo `127.0.0.1`, puerto `LOCAL_API_PORT` (default `8765`), línea `EDECAN_LOCAL_READY port=<p>` al estar sano, flags `--port`/`--data-dir`/`--no-web`, apagado limpio en `SIGTERM`/`SIGINT` — contrato completo en `ARCHITECTURE.md` §12f.
