# Estudio de código de Edecán

El Estudio convierte el teléfono en una consola segura para trabajar con los
proyectos de la computadora maestra. No depende de ninguna otra aplicación:
es una capacidad nativa de Edecán disponible con la misma arquitectura en
iOS y Android.

La app móvil no descarga una copia silenciosa del proyecto ni ejecuta código
por su cuenta. Cada operación viaja por el canal autenticado de Edecán y se
ejecuta en la computadora donde viven los archivos:

```text
iOS / Android
      ↓ HTTPS autenticado
API de Edecán
      ↓ bridge local o companion emparejado
proyecto autorizado en la computadora
```

Cerrar o minimizar la app móvil no termina una terminal ni un agente. Los
procesos viven en el runtime de escritorio, guardan eventos con cursores y se
pueden volver a abrir sin reenviar la instrucción.

## Experiencia móvil

El Estudio tiene cuatro superficies equivalentes en iOS y Android:

1. **Archivos**: explora carpetas, abre texto UTF-8, edita y guarda.
2. **Agente**: inicia Codex CLI o Claude CLI dentro del proyecto, muestra
   acciones y resultados en vivo y permite volver a una sesión anterior.
3. **Terminal**: abre una shell interactiva real con estado, entrada, salida y
   sesiones recuperables.
4. **Git**: muestra rama, upstream, cambios, diff e historial; permite preparar,
   retirar del índice, confirmar, crear/cambiar rama y publicar.

El selector superior cambia entre proyectos autorizados. La app conserva solo
identificadores opacos y cursores de sesión; no persiste el contenido de los
archivos ni la salida completa de la terminal en el teléfono.

## Proyectos autorizados

Un proyecto se registra una sola vez mediante su ruta absoluta en la
computadora. Desde ese momento el móvil usa un `workspace_id`; las operaciones
de archivos, terminal, agente y Git ya no aceptan una ruta raíz arbitraria.

El runtime rechaza:

- la raíz completa del sistema;
- la carpeta personal completa;
- carpetas de credenciales como `.ssh`, `.gnupg`, `.aws`, `.kube` y
  `Library/Keychains`;
- rutas absolutas en operaciones internas;
- `..` y enlaces simbólicos que escapen del proyecto.

El registro se guarda de forma atómica y privada dentro de los datos locales de
Edecán. Autorizar un proyecto no lo publica ni lo sube a ningún servidor.

## Sesiones persistentes

### Terminal

En macOS y Linux la terminal usa una PTY; en Windows usa el equivalente con
tuberías del proceso. La sesión mantiene su directorio de trabajo y continúa
mientras el runtime de escritorio siga abierto. Entrada y salida se transportan
por fragmentos incrementales.

### Agente

El usuario puede elegir detección automática, Codex CLI o Claude CLI. Edecán
inicia el proveedor dentro del workspace autorizado y transforma su stream en
una cronología legible:

- inicio y estado;
- comandos y herramientas;
- archivos modificados;
- mensajes y resultado final;
- errores y código de salida.

Los eventos de razonamiento interno no se muestran. El objetivo es hacer
visible el trabajo, no volcar trazas privadas del modelo.

El prompt no se guarda en la auditoría ni en los metadatos de la sesión. Los
eventos se almacenan localmente con tamaño y cantidad limitados.

## Git seguro

Git no se implementa concatenando texto en una shell. Cada acción construye un
`argv` tipado y ejecuta `git` con `shell=false` dentro del workspace:

- `status`
- `diff` normal o staged
- `log`
- `stage` y `unstage` por rutas relativas
- `commit`
- crear o cambiar de rama
- `push` a un remoto y rama validados

No hay botones para `reset --hard`, limpieza destructiva ni borrado de ramas.
Publicar desde el móvil exige una confirmación visible antes de ejecutar la
acción.

## API

Todas las rutas requieren autenticación y el flag `companion.ide`.

### Workspaces y archivos

| Ruta | Resultado |
|---|---|
| `GET /v1/ide/workspaces` | Proyectos autorizados |
| `POST /v1/ide/workspaces` | Autoriza `{path, name?}` |
| `POST /v1/ide/workspaces/{id}/activate` | Selecciona proyecto |
| `GET /v1/ide/workspaces/{id}/tree` | Árbol relativo y acotado |
| `GET /v1/ide/workspaces/{id}/file?path=` | Lee texto UTF-8 |
| `PUT /v1/ide/workspaces/{id}/file` | Guarda `{path, content}` atómicamente |
| `POST /v1/ide/workspaces/{id}/edit` | Reemplazo quirúrgico |
| `POST /v1/ide/workspaces/{id}/search` | Búsqueda textual |

### Terminal y agente

| Ruta | Resultado |
|---|---|
| `GET/POST /v1/ide/terminals` | Lista o inicia una terminal |
| `GET /v1/ide/terminals/{id}?cursor=` | Sesión y eventos nuevos |
| `POST /v1/ide/terminals/{id}/input` | Envía entrada |
| `DELETE /v1/ide/terminals/{id}` | Cierra la terminal |
| `GET/POST /v1/ide/agents` | Lista o inicia un agente |
| `GET /v1/ide/agents/{id}?cursor=` | Sesión y progreso nuevo |
| `DELETE /v1/ide/agents/{id}` | Cancela el agente |

### Git

Las rutas viven bajo
`/v1/ide/workspaces/{workspace_id}/git/{status,diff,log,stage,unstage,commit,branch,checkout,push}`.

Las rutas históricas `/v1/ide/tree`, `/file`, `/edit`, `/run` y `/search`
continúan disponibles para no romper el panel web ni clientes anteriores. No
reciben la credencial durable del móvil.

## Seguridad y privacidad

- El bridge solo acepta una lista cerrada de acciones IDE.
- Las rutas avanzadas exigen, además del Bearer, el ID y el secreto durable
  del móvil emparejado. El backend comprueba que el dispositivo siga activo y
  pertenezca al mismo usuario y tenant.
- Esa credencial solo viaja por HTTPS. HTTP se acepta únicamente en loopback
  para el panel local; una conexión HTTP plana desde la LAN se rechaza antes
  de consultar el secreto. `X-Forwarded-Proto` solo se confía si el proxy
  inmediato es local, como cloudflared.
- Lecturas y escrituras están encerradas en el workspace.
- Escrituras usan archivo temporal y reemplazo atómico.
- Git y los agentes usan `argv` fijo con `shell=false`. La Terminal sí es,
  deliberadamente, una shell interactiva: después de que la persona inicia
  esa sesión, la entrada remota se interpreta dentro de ella hasta cerrarla.
- Prompts, contenido, entrada de terminal y mensajes de commit se redactan de
  la auditoría.
- Edecán no lee ni sube los archivos de credenciales de los CLI. Sin embargo,
  un comando o agente puede imprimir datos sensibles en su propia salida; esa
  salida será visible en el teléfono emparejado y se conservará localmente en
  el historial acotado de la sesión. No imprimas secretos en una terminal
  remota.
- `ide_enabled: false` desactiva toda la superficie desde la configuración
  local.

En el companion standalone, autorizar un workspace, iniciar una
terminal/agente, editar archivos y mutar Git pide aprobación local. En la app
de escritorio instalada, la acción explícita dentro del IDE autenticado y
emparejado (botón/formulario, más la confirmación visible adicional para
`push`) es la aprobación humana. El bridge rechaza cualquier acción fuera de
su allowlist cerrada.

## Continuidad y límites honestos

- Una pérdida temporal de red o minimizar el móvil no cancela el trabajo.
- Reiniciar únicamente la app móvil rehidrata las sesiones.
- Si se apaga o reinicia la computadora maestra, los procesos activos terminan
  y las sesiones se marcan como interrumpidas; Edecán conserva su historial,
  pero no puede seguir editando archivos que ya no están en línea.
- Un modo cloud opcional puede mantener chat y trabajos alojados, pero nunca
  debe fingir acceso a un proyecto local cuando la computadora está apagada.
- La versión OSS usa las credenciales y firmas de cada persona. Edecán no
  comparte cuentas de Codex, Claude, Git ni Apple Developer.

## Código relevante

- Runtime: `apps/companion/edecan_companion/ide_*.py`
- Bridge instalado: `apps/local/edecan_local/companion_bridge.py`
- API: `apps/api/edecan_api/routers/ide.py`
- iOS: `apps/mobile/ios/EdecanApp/Screens/IDEView.swift`
- Android: `apps/mobile/android/androidApp/src/main/kotlin/cc/edecan/app/ui/IdeScreen.kt`
