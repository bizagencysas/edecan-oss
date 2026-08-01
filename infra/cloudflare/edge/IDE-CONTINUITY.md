# Continuidad privada de sesiones IDE

Esta capa permite que iOS y Android vuelvan a una sesión del estudio después de
minimizar la app, cambiar de red o perder temporalmente el enlace con la
computadora maestra.

No convierte Cloudflare en un IDE remoto. La computadora conserva en exclusiva:

- los archivos y sus contenidos;
- el terminal, entrada, salida y variables de entorno;
- Git y las credenciales del repositorio;
- el proceso del agente y sus herramientas;
- las decisiones que requieren aprobación local.

Cloudflare guarda solamente una proyección pequeña y explícitamente permitida:
estado, progreso, nombres relativos, resumen e historial de eventos del agente.

## Arquitectura

Cada combinación autenticada de `tenant + usuario + sesión IDE` se deriva con
SHA-256 y obtiene un Durable Object SQLite distinto. Ni los UUID del dueño ni el
identificador de sesión aparecen en el nombre del objeto.

```text
iOS / Android
  │ access JWT
  ├── GET snapshot, history, SSE replay
  ▼
Cloudflare Worker
  │ verifies HS256 access JWT
  ▼
IdeSessionContinuity (one SQLite Durable Object per owner/session)

Desktop master
  │ access JWT + x-edecan-desktop-capability
  ├── PUT state
  └── POST safe events
```

El header de escritorio usa el secreto ya existente `LOCAL_ORIGIN_KEY`. Solo la
app maestra debe conocerlo. Nunca se envía a un teléfono ni se guarda en el
repositorio.

## Rutas

`{session_id}` admite de 8 a 128 caracteres alfanuméricos, guion o guion bajo.
Todas las rutas requieren un access JWT vigente de la instalación.

| Método | Ruta | Quién puede usarla | Resultado |
| --- | --- | --- | --- |
| `GET` | `/v1/edge/ide/sessions/{session_id}` | móvil o escritorio | snapshot actual y cursor |
| `GET` | `/v1/edge/ide/sessions/{session_id}/events?after=0&limit=100` | móvil o escritorio | historial ordenado |
| `GET` | `/v1/edge/ide/sessions/{session_id}/stream` | móvil o escritorio | SSE reanudable |
| `PUT` | `/v1/edge/ide/sessions/{session_id}/state` | solo escritorio autenticado | reemplaza la proyección |
| `POST` | `/v1/edge/ide/sessions/{session_id}/events` | solo escritorio autenticado | añade un evento idempotente |

Una ruta desconocida, un método incorrecto, autenticación inválida o un payload
fuera del contrato reciben el mismo `404 {"error":"not_found"}`. El borde no
revela qué parte falló.

### Estado

El escritorio manda un `update_id` único por actualización. Un reintento con el
mismo identificador no incrementa la revisión otra vez.

```json
{
  "update_id": "01JEXAMPLEUNIQUEUPDATE",
  "status": "running",
  "workspace_label": "Mi proyecto",
  "active_file": "src/app.ts",
  "branch": "main",
  "summary": "Ejecutando las pruebas del cambio.",
  "progress": 0.65,
  "desktop_connected": true
}
```

Estados admitidos:

`idle`, `queued`, `running`, `waiting`, `completed`, `failed`, `cancelled` y
`disconnected`.

`active_file` debe ser una ruta relativa. No se aceptan rutas absolutas ni
recorridos con `..`.

### Eventos

Cada evento lleva un `event_id` estable. Si el escritorio reintenta el mismo
evento, recibe el evento original y `duplicate: true`.

```json
{
  "event_id": "01JEXAMPLEUNIQUEEVENT0",
  "type": "agent.progress",
  "payload": {
    "message": "Verificando el build local.",
    "progress": 0.8,
    "phase": "verify"
  }
}
```

Los payloads solo aceptan `message`, `progress`, `status`, `path`, `tool` y
`phase`. No aceptan campos de terminal, comandos, argumentos, salida estándar,
variables, credenciales ni contenido parecido a una clave.

## Reanudación del stream

El stream usa identificadores SSE monotónicos:

```text
id: 42
event: ide.event
data: {"seq":42,...}
```

El móvil conserva el último `id` procesado y vuelve a solicitar el stream con:

```http
Last-Event-ID: 42
```

También puede usar `?after=42`. El Durable Object reproduce únicamente los
eventos posteriores desde SQLite, por lo que una desconexión no crea huecos ni
reinicia el trabajo local. La respuesta termina con `ide.sync`; el cliente puede
reconectarse usando el `retry: 1500` anunciado por SSE. Este diseño no mantiene
un Worker activo mientras no hay novedades y conserva el costo mínimo.

## Retención

- Máximo 1.000 eventos por sesión.
- Máximo 7 días de historial.
- Un snapshot actual por sesión, eliminado tras 7 días sin escrituras del
  escritorio.
- Sin archivos, imágenes, diffs, prompts completos, salida de terminal ni
  secretos.

La poda ocurre al escribir. Cada sesión agenda una sola alarma de expiración y
la desplaza con nueva actividad; no existe un cron periódico que despierte
objetos inactivos.

## Límite de ejecución

No existe ninguna ruta Cloudflare para:

- iniciar un terminal;
- ejecutar un comando;
- escribir `stdin`;
- leer `stdout` o `stderr`;
- mutar archivos o Git;
- lanzar el agente.

Incluso una solicitud que posea JWT y capacidad de escritorio recibe `404` para
esas rutas. Las acciones siguen pasando por `/v1/ide/*` hacia la computadora
emparejada y por sus aprobaciones locales.

## Integración pendiente en clientes

Este paquete implementa y prueba el contrato de borde. Para activarlo en una
instalación, la app maestra debe publicar los snapshots/eventos seguros y el
móvil debe conservar el cursor SSE. Esa conexión requiere cambios deliberados
en escritorio y móvil; no se simula desde este paquete y no forma parte de un
despliegue de Cloudflare.

No se ejecutó ningún despliegue al crear esta capa.
