# apps/companion — `edecan_companion`

Agente local de escritorio, **opt-in**, que le da al asistente acceso controlado y auditable al equipo del usuario (automatizaciones locales) bajo consentimiento explícito.

## ⚠️ Advertencia — léela antes de instalar

- **Es 100% opt-in.** El companion nunca se activa solo. El asistente no puede hacer nada en tu equipo hasta que tú lo instales, lo emparejes a mano y lo dejes corriendo en tu terminal.
- **Todo pide aprobación por defecto.** Recién instalado, `allowed_apps`, `allowed_commands` y `auto_approve` están **vacíos**. Cada acción que el asistente pida te la pregunta en la terminal (`¿Permitir «acción» con {...}? [y/N]`) y, si no contestas en 60 segundos, se rechaza sola.
- **Nunca lo corras con permisos elevados.** No uses `sudo`, no lo corras como administrador/root, no lo instales como servicio con más privilegios de los que tiene tu usuario normal. El companion hereda los permisos de quien lo ejecuta — si lo corres como root, cualquier acción aprobada (¡o auto-aprobada por error!) corre como root.
- **El acceso a archivos está encerrado en un sandbox** (`sandbox_dir`, por defecto `~/EdecanSandbox`). No puede leer ni escribir nada fuera de esa carpeta, ni siquiera con `..` o enlaces simbólicos.
- **`run_command` solo corre ejecutables que tú listaste explícitamente** en `allowed_commands`, siempre sin shell (nunca interpreta `;`, `&&`, tuberías, etc.).
- Revisa `~/.edecan/companion.log` de vez en cuando: ahí queda una línea por cada acción que se pidió, se haya aprobado o no.
- Si algo te resulta sospechoso (una acción que no reconoces, un `auto_approve` que no configuraste tú), **cierra el proceso con Ctrl+C** y revisa `~/.edecan/companion.yaml`.

## Qué es

Se empareja con tu cuenta mediante un código de un solo uso: `POST /v1/companion/pair-code` genera el código (Redis, TTL 600s) y el companion abre `WS /v1/companion/ws?code=...`. La API expone `ConnectionManager.send_command(tenant_id, action, params, timeout=30)` e inyecta el canal en `ToolContext.extras["companion"]` para que las herramientas del agente (p. ej. `usar_computadora`) puedan invocarlo (ver `ARCHITECTURE.md` §10.7 y §10.12).

Disponible en todos los planes (`companion` = ✔ en la matriz de flags, `ARCHITECTURE.md` §10.13), pero siempre requiere que el usuario lo instale y empareje manualmente — nunca se activa por defecto.

## Instalación

Requiere Python ≥3.12.

```bash
cd apps/companion
uv sync              # o: pip install -e .
```

## Emparejamiento y uso

1. En la web del asistente, entra a **Ajustes → Companion** y genera un código de emparejamiento (`POST /v1/companion/pair-code`, válido 10 minutos).
2. Corre el companion apuntando al servidor y con ese código:

   ```bash
   python -m edecan_companion --server http://localhost:8000 --code TU-CODIGO
   ```

   (en producción, `--server` es la URL pública de tu instancia, p. ej. `https://api.tu-dominio.com`).
3. La primera vez, el companion crea `~/.edecan/companion.yaml` con todo vacío/deshabilitado y te avisa dónde quedó el sandbox (`~/EdecanSandbox` por defecto).
4. Déjalo corriendo en esa terminal. Cuando el asistente pida usar tu equipo, vas a ver la pregunta de aprobación ahí mismo.
5. `Ctrl+C` para cerrarlo en cualquier momento — no queda nada corriendo en segundo plano.

Si se cae la conexión (red, servidor reiniciando, etc.), el companion reintenta solo con backoff exponencial (hasta 60s entre intentos) — no hace falta reiniciarlo a mano.

## Configuración — `~/.edecan/companion.yaml`

```yaml
sandbox_dir: "~/EdecanSandbox"   # única carpeta a la que se restringe read_dir/read_file/write_file/list_tree/search_files/apply_edit
allowed_apps: []                  # apps que "open_app" puede abrir (open -a / xdg-open)
allowed_commands: []              # ejecutables que "run_command" puede correr (sin shell)
auto_approve: []                  # nombres de acción que se aprueban SIN preguntar (¡ojo!)
remember_approvals_minutes: 0     # minutos que se recuerda un "sí" para la MISMA acción (0 = siempre pregunta)
ide_enabled: true                 # activa/desactiva de un tirón las 4 acciones del IDE embebido
```

Todas las listas empiezan vacías a propósito. Edítalas a mano, con cuidado, solo con lo que de verdad quieras habilitar. `auto_approve` es la más delicada: cualquier acción ahí corre sin que te pregunten nada — solo agrégala si confías plenamente en cómo tu asistente va a usarla. `allowed_commands` también es lo que decide qué puede correr `POST /v1/ide/run` (la terminal del IDE embebido, ver `docs/ide.md`) — **tú** decides qué binarios permitir ahí, incluidos `git`/`docker` si quieres: el companion no los trae permitidos por defecto.

## Acciones soportadas

| Acción | Qué hace | Restricción |
|---|---|---|
| `open_app` | Abre una app (`open -a` en macOS, `xdg-open` en Linux) | `app` ∈ `allowed_apps` |
| `read_dir` | Lista una carpeta | dentro de `sandbox_dir` |
| `read_file` | Lee un archivo (texto o base64 si es binario) | dentro de `sandbox_dir`, máx. 256 KB |
| `write_file` | Escribe un archivo (crea carpetas padre si hacen falta) | dentro de `sandbox_dir` |
| `clipboard_get` / `clipboard_set` | Lee/escribe el portapapeles (`pbpaste`/`pbcopy`, o `xclip` en Linux) | — |
| `run_command` | Corre un ejecutable con argumentos, sin shell, timeout 30s | ejecutable base ∈ `allowed_commands` |
| `list_tree` | Árbol recursivo de una carpeta (`{path?, max_depth≤5, max_entries≤500}`) | dentro de `sandbox_dir`; ignora `.git`/`node_modules`/`__pycache__`/`.venv`; `ide_enabled` |
| `search_files` | Busca texto línea por línea (`{query, path?}`), máx. 2000 archivos / 200 coincidencias | dentro de `sandbox_dir`, solo archivos de texto < 256 KB; `ide_enabled` |
| `apply_edit` | Reemplaza `old_string` por `new_string` en un archivo (`{path, old_string, new_string, replace_all?}`), escritura atómica | dentro de `sandbox_dir`; `old_string` único salvo `replace_all`; `ide_enabled` |
| `screenshot` | Captura la pantalla a PNG en base64 (`{display?}`) vía `screencapture` | **solo macOS**; exige permiso de Grabación de Pantalla; `ide_enabled` |

Cualquier otra acción responde `"acción no soportada"` sin pedir aprobación (no existe, así que no hay nada que aprobar). Las 4 acciones del IDE embebido (`list_tree`, `search_files`, `apply_edit`, `screenshot`) además respetan `ide_enabled`: si está en `false`, se rechazan ANTES de pedir aprobación (ver `docs/ide.md` en la raíz del repo para el flujo completo vía `/v1/ide/*` y la página web).

### Recordar aprobaciones (`remember_approvals_minutes`)

Con el valor por defecto (`0`), cada acción pide aprobación SIEMPRE, sin excepción — igual que hasta ahora. Si le pones un número > 0, la próxima vez que digas que sí a una acción, esa MISMA acción (por nombre, no por parámetros) queda recordada en memoria (nunca en disco) durante esos minutos: durante ese tiempo se auto-aprueba sin volver a preguntar. Un **no** nunca se recuerda — decir que no siempre vuelve a preguntar la próxima vez. Reiniciar el companion olvida todo lo recordado.

## Auditoría

Cada acción —se haya aprobado o no, haya salido bien o no— deja una línea en `~/.edecan/companion.log` (JSONL): marca de tiempo, acción, parámetros (el contenido de archivos/portapapeles se reemplaza por su tamaño, nunca se guarda en claro), si se aprobó y si terminó bien.

## Desarrollo

```bash
cd apps/companion
uv run pytest
```

Los tests no abren sockets, no tocan `~/.edecan/` real, y usan `tmp_path` + `monkeypatch` para `subprocess`/`input`/el sandbox (ver `tests/conftest.py`).
