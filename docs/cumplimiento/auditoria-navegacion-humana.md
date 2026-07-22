# Auditoría de navegación humana

Fecha de corte: 2026-07-21.

Esta auditoría distingue una pantalla existente de una capacidad realmente
operativa. La navegación normal conserva solo **Edecan**, **Actividad** y
**Ajustes**; las superficies especializadas viven bajo **Modo avanzado** y se
ocultan cuando el plan no concede su flag. No se eliminaron rutas ni APIs.

| Superficie anterior | Estado real | Decisión de navegación |
| --- | --- | --- |
| Misiones | Operativa con API persistente, orquestador y worker; depende del modelo y del worker configurados. | Pantalla avanzada, visible solo con `agents.missions`. |
| Automatizaciones | Operativa con reglas persistentes y ejecución en worker. | Pantalla avanzada, visible solo con `automations.rules`. |
| Recordatorios | Operativa con CRUD persistente y entrega programada. | Pantalla avanzada. |
| Mensajes | Operativa cuando existe un conector del usuario; no puede enviar por una cuenta inexistente. | Pantalla avanzada, visible solo con `connectors.messaging`. |
| Reuniones | Operativa para ingesta, procesamiento y resumen; depende del worker/proveedor para procesar medios. | Pantalla avanzada, visible solo con `tools.meetings`. |
| Archivos | Operativa para carga, almacenamiento, extracción y descarga. | Pantalla avanzada. |
| Contactos | Operativa para CRUD, búsqueda e importación. | Pantalla avanzada. |
| Finanzas | Operativa para movimientos y resumen; la sincronización Stripe requiere credenciales propias. | Pantalla avanzada. |
| Panel | Es telemetría interna de consumo, límites y flags; no es una capacidad que una persona deba aprender. | Oculto del menú. La ruta se conserva para soporte. |
| Analista | Operativa para analizar documentos mediante el modelo conectado. | Pantalla avanzada. |
| Órdenes | Parcial: prepara borradores y ejecuta trading simulado (`paper`); el modo live devuelve `501` y el enlace de pago sigue siendo un marcador. | Se convierte en “Preparar una orden” y abre el chat con una solicitud editable y confirmación explícita. |
| Ads | Dependiente de proveedor: Meta es real con credenciales del usuario; sin ellas usa un proveedor offline de ejemplo. | Se convierte en “Mejorar campañas” y abre el chat, que debe verificar conexión, declarar ejemplos y confirmar antes de gastar. |
| Viajes | Búsqueda real mediante la capa MCP nativa; rastreo requiere AfterShip del usuario y las reservas siguen siendo preparatorias. | Pantalla avanzada, visible solo con `tools.travel`. |
| Negocios | Operativa para KPIs, facturas y registros persistentes. | Pantalla avanzada. |
| Inventario | Operativa con datos ERP persistentes. | Pantalla avanzada, visible solo con `erp.inventory`. |
| RRHH | Operativa para empleados, ausencias y borradores de nómina; las acciones sensibles conservan confirmación. | Pantalla avanzada, visible solo con `erp.hr`. |

## Guardrails de los accesos por chat

- La URL solo contiene una clave de intención incluida en una allowlist; nunca
  acepta una instrucción arbitraria desde query params.
- El texto llega prellenado al compositor y la persona puede corregirlo antes
  de enviarlo. No se ejecuta nada por navegar.
- Si la persona ya está en el chat, un evento local prellena el texto sin
  recargar. Desde otra pantalla, el chat consume la intención al montar y
  limpia el query param para no repetirla al refrescar.
- La orden exige autorización explícita antes de confirmar. Ads exige declarar
  datos de ejemplo, comprobar la conexión y confirmar antes de activar gasto.
