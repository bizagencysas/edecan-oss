# Runbook: rotación de claves del `TokenVault`

Cubre la rotación de las dos capas de clave del cifrado envolvente (`ARCHITECTURE.md` §10.4): la **clave envolvente** (`KMS_KEY_ID` en prod, `LOCAL_MASTER_KEY` en dev/self-host) y la **data key por tenant** (`tenant_keys.encrypted_data_key`). Son operaciones distintas, con costo y alcance muy diferentes — no las confundas.

## Por qué son operaciones distintas

El cifrado envolvente existe exactamente para que rotar la clave "de arriba" sea barato:

- Cada tenant tiene su propia **data key** AES-256-GCM, que es la que cifra/descifra directamente el contenido de `oauth_tokens.ciphertext`.
- Esa data key nunca se guarda en claro: se guarda **envuelta** (`tenant_keys.encrypted_data_key`) por la clave maestra vigente (KMS o Fernet).
- **Rotar la clave maestra** (KMS o `LOCAL_MASTER_KEY`) solo requiere volver a envolver cada data key existente con la clave nueva — un objeto pequeño por tenant, **no** hace falta tocar ningún `oauth_tokens.ciphertext`.
- **Rotar la data key de un tenant** sí requiere re-cifrar todos sus `oauth_tokens` (des-cifrar con la data key vieja, cifrar con la nueva) — una operación más cara, que solo se justifica ante sospecha de compromiso de **esa** data key específica.

## Cuándo rotar

| Situación | Qué rotar |
|---|---|
| Rotación preventiva programada (recomendado cada 90 días en producción hospedada) | Clave maestra (KMS o `LOCAL_MASTER_KEY`) |
| Sospecha de compromiso de la clave maestra (filtración de `.env`, acceso no autorizado a KMS) | Clave maestra — **inmediato**, no programado |
| Sospecha de compromiso de un tenant específico (cuenta de operador comprometida, hallazgo de un pentest sobre ese tenant) | Data key de ese tenant únicamente |
| Rotación nativa de KMS (AWS gestiona la rotación anual del material de la CMK bajo el mismo `KMS_KEY_ID`) | Nada que hacer manualmente — KMS conserva material anterior para poder seguir descifrando lo ya envuelto; es transparente para la app |

## A. Rotar la clave maestra (KMS `KMS_KEY_ID` completo, o `LOCAL_MASTER_KEY`)

Aplica cuando cambias de **CMK** por completo (no la rotación anual automática de AWS, que no requiere acción) o cuando cambias `LOCAL_MASTER_KEY` en dev/self-host.

1. **Genera la nueva clave maestra** sin desactivar la anterior todavía:
   - KMS: crea una nueva CMK (o un nuevo alias) en AWS KMS. No la apliques como `KMS_KEY_ID` en producción aún.
   - Fernet (dev/self-host): genera un valor nuevo (p. ej. `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`) sin sobreescribir todavía el `LOCAL_MASTER_KEY` actual en `.env`.
2. **Recorre todos los tenants** y, para cada uno:
   a. Desenvuelve su data key actual con la clave maestra **vieja** (`KeyProvider.unwrap`).
   b. Vuelve a envolverla con la clave maestra **nueva** (`KeyProvider.wrap`).
   c. Actualiza `tenant_keys.encrypted_data_key` (y `kms_key_id` si cambiaste de CMK) en una transacción por tenant — si falla a la mitad, ese tenant debe quedar con la envoltura vieja consistente, nunca en un estado mixto.
   d. Incrementa `tenant_keys.version`.
   Este recorrido **no toca `oauth_tokens`** — la data key en sí no cambió, solo cómo está envuelta.
3. **Verifica** sobre un subconjunto de tenants que el desenvolver-con-clave-nueva + descifrar un `oauth_tokens.ciphertext` real produce el mismo resultado que antes de la rotación.
4. **Actualiza la configuración** (`KMS_KEY_ID` o `LOCAL_MASTER_KEY`) al valor nuevo en todos los procesos (API, worker) — en producción, vía Secrets Manager y un redeploy de las task definitions de ECS, nunca editando un `.env` en un servidor vivo.
5. **Retén la clave maestra vieja** de forma segura por un período de gracia corto (p. ej. 24–48 horas) por si algún tenant quedó sin recorrer por un fallo parcial del paso 2 — después, destrúyela (KMS: *schedule key deletion*; Fernet: bórrala de donde la hayas guardado temporalmente).
6. Registra la rotación en `audit_log` (acción a nivel de plataforma, `tenant_id` nulo).

## B. Rotar la data key de un tenant específico

Aplica solo ante sospecha de compromiso de **ese** tenant — es una operación más invasiva.

1. Genera una nueva data key aleatoria de 256 bits para el tenant.
2. Envuélvela con la clave maestra vigente → nuevo `tenant_keys.encrypted_data_key`.
3. Para cada fila de `oauth_tokens` de ese tenant: descifra con la data key vieja (nonce + ciphertext actuales), vuelve a cifrar con la data key nueva (nonce nuevo), actualiza `ciphertext`, `nonce` y `key_version`.
4. Solo después de re-cifrar **todas** las filas de ese tenant, actualiza `tenant_keys.encrypted_data_key` y `version`. Si el proceso se interrumpe a la mitad, algunas filas quedarían con `key_version` apuntando a una data key que ya no coincide con `tenant_keys` — hazlo dentro de una transacción, o con un mecanismo de reintento idempotente que pueda retomarse sabiendo qué filas ya se re-cifraron (compara `key_version` de cada fila contra `tenant_keys.version` antes de tocarla).
5. Si el compromiso pudo haber expuesto ya la data key **vieja** en claro (no solo envuelta), considera esa rotación insuficiente por sí sola: el atacante ya pudo haber usado la data key vieja para descifrar tokens **antes** de que corrieras este runbook. En ese caso, trata la incidencia también como posible robo de credenciales de terceros: notifica al tenant para que revoque/reautorice sus conectores (`DELETE /v1/connectors/{key}/{account_id}` seguido de una nueva autorización) — la rotación de la data key protege hacia adelante, no deshace un acceso ya ocurrido.
6. Registra en `audit_log` con el `tenant_id` afectado.

## Verificación post-rotación (ambos casos)

- Un tenant de prueba puede seguir usando normalmente sus conectores (p. ej. `GET /v1/connectors` responde con las cuentas conectadas y sus operaciones siguen funcionando) sin que el usuario perciba nada.
- No quedan referencias a la clave maestra vieja en ningún proceso corriendo (confirma que el redeploy tomó la config nueva).
- `tenant_keys.version` (y `oauth_tokens.key_version` si rotaste una data key específica) reflejan el incremento esperado.

## Prevención

- Automatiza la rotación preventiva de la clave maestra como una tarea programada, no solo como respuesta a incidentes.
- En producción, usa siempre KMS (nunca `LOCAL_MASTER_KEY`) — la rotación anual de material de KMS es transparente y no requiere este runbook para el caso más común.
- Este runbook, igual que el resto de `infra/`, se **ejecuta manualmente por un operador humano** — ninguna parte de la rotación de claves de producción se automatiza para correr sola desde este repositorio (`ARCHITECTURE.md` §0.4).
