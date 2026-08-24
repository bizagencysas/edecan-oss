# Migrar una instalación privada de otro asistente

Edecán incluye un importador **local, explícito e idempotente** para que la
persona dueña de una instalación pueda traer continuidad desde un asistente
anterior. No es una migración global ni forma parte de la configuración
pública del proyecto.

El importador:

- funciona en `dry-run` hasta que se agrega `--apply`;
- exige el `tenant`, el usuario y el correo exactos de la persona dueña;
- conserva el Core Identity público de Edecán y agrega el estilo privado como
  una capa editable de Persona;
- genera embeddings compatibles con la memoria local;
- importa conversaciones con identificadores deterministas para no duplicarlas
  al repetir el proceso;
- crea perfiles independientes para agentes de llamadas y de contenido;
- importa horarios **desactivados** para que la persona los revise antes de
  ejecutarlos;
- guarda el corpus privado fuera del repositorio con permisos `0700/0600`;
- no publica, no llama, no envía mensajes y no activa automatizaciones.

## Qué puede migrar

La lista blanca reconoce estos datos:

| Datos | Clave de `paths` | Ruta canónica |
|---|---|---|
| Identidad y contexto | `identity_context` | `identity/context.txt` |
| Persona anterior | `assistant_persona` | `persona/instructions.md` |
| Perfil estructurado | `structured_profile` | `memory/profile.json` |
| Memoria semántica | `semantic_memory` | `memory/items.jsonl` |
| Estilo de escritura | `writing_style` | `writing/style.md` |
| Corpus de voz o escritura | `writing_corpus` | `writing/corpus.txt` |
| Índice de conversaciones | `conversation_index` | `conversations/index.json` |
| Archivos de conversaciones | `conversation_directory` | `conversations/items/*.jsonl` |
| Historial global opcional | `global_history` | `conversations/history.jsonl` |
| Horarios | `schedules` | `automations/schedules.json` |
| Agente de asistencia | `assistant_call_prompt` | `calls/assistant.md` |
| Agente comercial | `sales_call_prompt` | `calls/sales.md` |
| Voces y apertura | `voice_config` | `calls/voice.json` |
| Perfil editorial de LinkedIn | `linkedin_editorial` | `social/linkedin.md` |
| Perfil editorial de X | `x_editorial` | `social/x.md` |

El archivo `calls/voice.json` puede contener `assistant_voice_id`,
`assistant_opening_message` y `sales_voice_id`. Los documentos que el
importador escribe en Edecán siempre usan nombres neutrales y nunca copian la
carpeta fuente al repositorio.

### Estructuras heredadas: `--source-map`

Si la instalación anterior usa rutas distintas, crea un mapa JSON privado
**fuera del repositorio**. El mapa traduce solo claves canónicas a rutas
relativas dentro de `--source`:

```json
{
  "schema_version": 1,
  "paths": {
    "identity_context": "custom/context.txt",
    "assistant_persona": "custom/persona.txt",
    "assistant_call_prompt": "custom/reception.txt",
    "voice_config": "custom/voice-source.py"
  },
  "python_constants": {
    "assistant_voice_id": "PRIMARY_VOICE",
    "assistant_opening_message": "GREETING"
  }
}
```

`python_constants` es opcional y sirve únicamente cuando la configuración de
voz heredada es un módulo Python. Edecán analiza asignaciones literales con
AST; no importa ni ejecuta ese módulo. Las rutas absolutas, los `..`, los
enlaces simbólicos que salgan de `--source`, las claves desconocidas y los
mapas guardados dentro del checkout se rechazan.

## Antes de empezar

1. Cierra Edecán o evita escribir en el perfil mientras dura la importación.
2. Haz una copia verificable de la carpeta privada de datos y de la base local.
3. Arranca la misma versión de Edecán contra la que ejecutarás el importador y
   comprueba `GET /healthz`.
4. Inicia sesión y consulta `GET /v1/me`. Copia:
   - `user.id` como `USER_ID`;
   - `tenant.id` como `TENANT_ID`;
   - `user.email` como confirmación de propiedad.
5. Elige una carpeta de destino privada **fuera del checkout Git**. En una app
   de escritorio normal debe ser su directorio de datos, no `docs/`, `infra/`
   ni otra ruta dentro del repositorio.

No pegues esos valores en documentación, issues o commits. Son parámetros de
tu instalación.

## 1. Simular la importación

El siguiente comando solo inspecciona y devuelve conteos. No escribe:

```bash
uv run --frozen --all-packages python -m edecan_local.private_assistant_import \
  --source "/ruta/privada/al/asistente-anterior" \
  --source-map "/ruta/privada/mapa-de-fuente.json" \
  --data-dir "/ruta/privada/a/los-datos-de-edecan" \
  --tenant-id "00000000-0000-0000-0000-000000000001" \
  --user-id "00000000-0000-0000-0000-000000000002" \
  --confirm-owner-email "persona@example.com" \
  --pg-socket "/ruta/privada/al/socket-de-postgres"
```

También puedes usar `--database-url` en lugar de `--pg-socket`. No escribas una
URL con contraseña en el historial del shell: usa `EDECAN_DATABASE_URL` desde
el entorno privado del proceso.

Omite `--source-map` cuando la fuente ya usa todas las rutas canónicas.

La salida es JSON y separa `mode: "dry-run"` de los conteos planeados. Revisa
especialmente conversaciones, mensajes, memorias, agentes, perfiles sociales y
automatizaciones desactivadas.

Los nombres visibles de los agentes se configuran, no se codifican:

```bash
uv run --frozen --all-packages python -m edecan_local.private_assistant_import \
  --source "/ruta/privada/al/asistente-anterior" \
  --source-map "/ruta/privada/mapa-de-fuente.json" \
  --data-dir "/ruta/privada/a/los-datos-de-edecan" \
  --tenant-id "00000000-0000-0000-0000-000000000001" \
  --user-id "00000000-0000-0000-0000-000000000002" \
  --confirm-owner-email "persona@example.com" \
  --assistant-agent-name "Recepción" \
  --sales-agent-name "Ventas" \
  --pg-socket "/ruta/privada/al/socket-de-postgres"
```

## 2. Aplicar

Repite exactamente el `dry-run` aprobado y agrega `--apply`:

```bash
uv run --frozen --all-packages python -m edecan_local.private_assistant_import \
  --source "/ruta/privada/al/asistente-anterior" \
  --source-map "/ruta/privada/mapa-de-fuente.json" \
  --data-dir "/ruta/privada/a/los-datos-de-edecan" \
  --tenant-id "00000000-0000-0000-0000-000000000001" \
  --user-id "00000000-0000-0000-0000-000000000002" \
  --confirm-owner-email "persona@example.com" \
  --pg-socket "/ruta/privada/al/socket-de-postgres" \
  --apply
```

La transacción de base de datos se revierte si falla. Los documentos privados
solo se escriben después de confirmar esa transacción y quedan bajo:

```text
<data-dir>/private-imports/legacy-assistant/
```

El manifiesto contiene nombres de documentos y fecha de actualización, no
secretos.

## 3. Credenciales, solo si la persona lo decide

Por defecto no se importa ninguna credencial. Si necesitas conservar
integraciones, crea dos archivos fuera del repositorio:

1. un archivo de entorno privado con los valores;
2. un mapa JSON que solo referencia nombres de variables.

Ejemplo de mapa sin secretos:

```json
{
  "schema_version": 1,
  "credentials": [
    {
      "required_env": ["VOICE_API_KEY"],
      "connector_key": "voice_tts",
      "external_account_id": "voice_tts",
      "display_name": "Voz",
      "access_token": {
        "provider": "example",
        "api_key": {"env": "VOICE_API_KEY"}
      },
      "scopes": ["tts"],
      "token_type": "config"
    }
  ]
}
```

Después agrega al comando:

```text
--import-credentials
--credentials-env /ruta/privada/credenciales.env
--credential-map /ruta/privada/mapa-credenciales.json
```

Los secretos literales dentro del mapa se rechazan. Los metadatos descriptivos
pueden ser literales. Las credenciales se cifran
con el vault local del tenant. Las existentes se conservan; reemplazarlas
requiere además `--replace-existing-credentials`.

## 4. Verificar

Después de aplicar:

1. abre Perfil, Memoria, Llamadas y Contenido;
2. confirma que la identidad y los agentes pertenecen a la persona correcta;
3. busca varias memorias desde conversaciones nuevas;
4. revisa que todas las automatizaciones importadas sigan desactivadas;
5. prueba cada conector sin publicar, llamar o enviar;
6. vuelve a ejecutar el mismo comando sin `--apply`.

La segunda simulación debe producir el mismo plan. Si aplicas otra vez, los
identificadores deterministas actualizan o ignoran lo ya importado en lugar de
crear copias.

## Privacidad y Git

- La carpeta fuente y `<data-dir>` deben estar fuera del repositorio.
- No se importan configuraciones privadas a `infra/aws`, `infra/cloudflare`,
  `.env.example`, fixtures o documentación.
- Los nombres, correos, teléfonos, dominios, voces, tokens y cuentas de una
  persona no son defaults públicos.
- `scripts/check_public_config.py` revisa archivos rastreados y archivos nuevos
  no ignorados antes de publicar.
- Actualizar el código sustituye binarios, no la base local, el vault ni
  `private-imports`.

Si una migración necesita un formato que no aparece en la lista blanca, amplía
el importador con una prueba y un nombre neutral. No agregues la información de
la persona al código para resolverlo.
