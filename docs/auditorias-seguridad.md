# Auditorías de seguridad

Edecán ofrece dos niveles separados para proyectos propios o sistemas sobre
los que la persona tenga autorización explícita.

## 1. Revisión estática local

`auditar_seguridad_proyecto` recorre en modo de solo lectura el proyecto local
seleccionado. Busca archivos sensibles, posibles credenciales fijas y patrones
de código inseguros. Los hallazgos incluyen regla, severidad, ruta y línea,
pero nunca el valor que parece un secreto.

Esta revisión funciona sin PentestGPT y con cualquier modelo conectado. Es
heurística: encontrar cero alertas no demuestra ausencia de vulnerabilidades.
Sus resultados pueden alimentar la autorreparación aislada de Edecán, que
mantiene sus confirmaciones y verificaciones normales antes de cambiar código.

## 2. PentestGPT autorizado

`ejecutar_pentestgpt_autorizado` integra la CLI mantenida por
GreyDGL/PentestGPT. Es una herramienta peligrosa y se bloquea salvo que se
cumplan todos estos requisitos:

1. Edecán está en modo local.
2. El binario de PentestGPT está instalado y fijado por el dueño.
3. El objetivo y el alcance declarado coinciden exactamente.
4. La persona declara que tiene autorización.
5. La persona confirma la tarjeta de ejecución que muestra el objetivo real.

Edecán nunca descarga ni actualiza PentestGPT automáticamente. La integración
prefiere `pentestgpt-agent`, que actualmente usa Claude Code o Codex como
backend autónomo. También conserva compatibilidad con el comando clásico
`pentestgpt` si el dueño lo proporciona. En ambos casos se ejecutan argumentos
exactos sin shell y se fija `LANGFUSE_ENABLED=false`; el comando clásico añade
además `--no-telemetry`.

Configuración opcional:

```text
PENTESTGPT_BINARY=/ruta/fijada/a/pentestgpt-agent
PENTESTGPT_BACKEND=claude
PENTESTGPT_TIMEOUT_SECONDS=3600
```

El reporte saneado queda en `$DATA_DIR/security-reports`. No se publica, no se
sube a un remoto y no se aplican cambios automáticamente.

## Modelo independiente

La revisión estática, el análisis de los hallazgos y la corrección local usan
las capacidades normales de Edecán, por lo que no dependen de una marca de
modelo. La ejecución autónoma específica de PentestGPT sí hereda la limitación
actual del proyecto original: sus backends no interactivos mantenidos son
Claude Code y Codex. Los modelos restantes siguen pudiendo ejecutar el flujo
de auditoría y reparación nativo de Edecán.

## Límites

- Solo se admiten objetivos HTTP/HTTPS con un host explícito.
- Se rechazan URLs con credenciales o fragmentos.
- Un cambio de puerto, ruta o protocolo requiere una nueva declaración exacta.
- La telemetría queda desactivada.
- La salida se sanea antes de guardarse y tiene límites de tamaño y tiempo.
- La autorización de un objetivo no autoriza ningún tercero relacionado.
