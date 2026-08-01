# Edecán Edge en Cloudflare

Puerta privada de continuidad para las instalaciones OSS de Edecán.

## Responsabilidad

- El dominio privado elegido por cada instalación no sirve una web ni un panel.
- Las solicitudes anónimas reciben el mismo `404` genérico.
- Las sesiones de los dispositivos siguen siendo individuales y revocables.
- Cloudflare enruta trabajo local a la computadora maestra y continuidad a AWS.
- Si el origen local cae durante un turno de chat, adapta automáticamente la
  respuesta AWS al mismo SSE que ya consumen iOS y Android.
- Las sesiones IDE pueden conservar una proyección privada en Durable Objects:
  estado, progreso e historial reanudable por cursor, nunca archivos ni terminal.
- Las claves de los orígenes viven como secretos de Cloudflare, nunca en Git.

El Worker valida primero la firma, vigencia y tipo del access token de esa
instalación. El origen vuelve a validar la sesión y su revocación.
`pairing/claim` y `pairing/refresh` son las únicas excepciones: aceptan solo
POST y el backend valida sus secretos efímeros de un solo uso.

## Qué alcanza un token de dispositivo bajo `/v1/edge/`

Hacia el origen local, el Worker conserva la autorización de quien llama y la
computadora vuelve a validarla. Hacia AWS no: ahí la reemplaza por
`AWS_ORIGIN_KEY`, que vale por la instalación entera. Por eso `/v1/edge/` tiene
una lista blanca explícita —`DEVICE_EDGE_ROUTES` en `src/index.ts`— y un
teléfono emparejado solo alcanza lo que pide para sí:

| Método | Ruta | Para qué |
| --- | --- | --- |
| `POST` | `/v1/edge/chat` | conversar cuando la computadora no contesta |
| `GET` | `/v1/edge/jobs/{job_id}` | seguir el trabajo que el Worker le devolvió |

Todo lo demás bajo `/v1/edge/` —latido, despedida, encolar, reclamar y
completar— es plano de control de la computadora, que habla directo con API
Gateway usando su secreto compartido y nunca pasa por este Worker. Un
dispositivo recibe ahí el mismo `404` genérico que un anónimo: un `403`
confirmaría que la ruta existe.

La lista es blanca y no negra a propósito: una ruta nueva en AWS queda cerrada
hasta que alguien decida de qué lado va. Abrirle una más a los dispositivos
obliga a actualizar `test/edge_allowlist.test.ts`, que fija el contenido exacto
de la lista para que sea una decisión revisada y no un descuido.

Las cabeceras de confianza las escribe el Worker y solo el Worker: descarta
`x-edecan-installation`, `x-edecan-device-authorization` y `x-edecan-edge-key`
de toda solicitud entrante —hacia cualquier origen— antes de reenviarla. Así
quien llama no elige de qué instalación lee su trabajo ni se hace pasar por un
origen.

Eso cubre las cabeceras, no el cuerpo: `/v1/edge/chat` se reenvía tal cual. La
instalación de un turno tiene que quedar sellada del lado de AWS, atada a la
sesión del token y no a lo que traiga el JSON.

## Entornos

- `staging`: usa el hostname privado definido en
  `EDECAN_EDGE_STAGING_DOMAIN`.
- `production`: usa el hostname privado definido en
  `EDECAN_EDGE_PRODUCTION_DOMAIN`.

El despliegue de producción no debe hacerse hasta configurar y probar ambos
orígenes, sus secretos y la revocación de dispositivos.

`wrangler.jsonc` es deliberadamente neutral y no contiene rutas de ninguna
instalación. Antes de desplegar, los scripts generan
`wrangler.private.<entorno>.jsonc` con permisos `0600`. Esos archivos están
ignorados por Git y conservan el dominio de la instalación únicamente en el
equipo que despliega.

```bash
EDECAN_EDGE_STAGING_DOMAIN=staging-edge.example.net npm run deploy:staging
EDECAN_EDGE_PRODUCTION_DOMAIN=edge.example.net npm run deploy:production
```

Las variables reciben solo el hostname, sin `https://`, puerto ni ruta. El
generador rechaza valores ambiguos antes de invocar Wrangler. Para revisar el
bundle sin configurar una ruta ni tocar Cloudflare:

```bash
npm run dry-run
```

## Verificación local

```bash
npm install
npm run cf:types
npm run check
npm test
npm run dry-run
```

## Secretos esperados

- `LOCAL_ORIGIN_URL`
- `LOCAL_ORIGIN_KEY`
- `AWS_ORIGIN_URL`
- `AWS_ORIGIN_KEY`
- `JWT_VERIFICATION_SECRET`

Se cargan con `wrangler secret put` en cada entorno. No deben escribirse en
`wrangler.jsonc`, `.dev.vars`, documentación ni comandos con argumentos.

Ejemplo seguro, con entrada interactiva:

```bash
npx wrangler secret put LOCAL_ORIGIN_KEY --env staging
npx wrangler secret put JWT_VERIFICATION_SECRET --env staging
```

El fallback solo aplica a chat de texto sin adjuntos. Si AWS encola el turno,
el Worker espera su resultado hasta 25 segundos y publica el `job_id` en
`X-Edecan-Continuity-Job-Id`. Consulta
[`docs/continuidad-hibrida.md`](../../../docs/continuidad-hibrida.md) para el
flujo completo y los límites que todavía requieren la computadora.

La continuidad IDE vive en un Durable Object SQLite independiente por
dueño/sesión. Solo la computadora autenticada con `LOCAL_ORIGIN_KEY` puede
publicar actualizaciones; los dispositivos con el JWT del mismo dueño pueden
leer y reanudar SSE mediante `Last-Event-ID`. Cloudflare no expone ninguna ruta
de terminal, comandos, archivos o Git, incluso para el escritorio. El contrato,
retención y payloads admitidos están en
[`IDE-CONTINUITY.md`](./IDE-CONTINUITY.md).
