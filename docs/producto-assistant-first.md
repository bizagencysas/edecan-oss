# Edecan: contrato de producto assistant-first

Este documento es la fuente de verdad de la experiencia de Edecan. La
arquitectura puede crecer por dentro; la persona no debe cargar con esa
complejidad.

## Promesa

Edecan es un asistente personal local-first. Una frase escrita o hablada debe
ser suficiente para expresar una intención completa:

> Organiza mis pendientes, responde este correo, revisa el documento y
> recuérdame pagar mañana.

Edecan decide qué memoria, herramienta, conector o flujo necesita. No obliga a
la persona a escoger módulos, agentes, APIs o pantallas técnicas antes de pedir
el resultado.

## Superficie visible

La navegación primaria tiene tres destinos:

1. **Edecan**: conversación por texto o voz, archivos y resultados.
2. **Actividad**: trabajo en curso, resultados, aprobaciones y errores que
   necesitan atención.
3. **Ajustes**: identidad, privacidad, proveedores y conexiones. Las opciones
   técnicas viven en un modo avanzado, cerrado por defecto.

Negocios, correo, calendario, reuniones, documentos, recordatorios, viajes,
anuncios, inventario, RRHH, código y demás dominios son capacidades del
asistente, no productos que la persona deba aprender por separado.

## Escalera de acción

Ante cada petición, Edecan sigue esta escalera sin exponerla como un menú:

1. Usa una capacidad ya disponible.
2. Si falta configuración, explica en lenguaje llano la única conexión o el
   único permiso necesario.
3. Si falta la capacidad, propone crear o adaptar una **skill local** aislada,
   reversible y comprobable.
4. Solo cuando el defecto pertenece al núcleo de Edecan, y la instalación está
   administrada desde código fuente, propone una reparación local del núcleo.

No puede afirmar que hizo algo si una herramienta real no lo confirmó.

## Reparación guiada por la persona

Una frase como esta debe iniciar el flujo de reparación:

> Te pedí que hicieras esto y dijiste que no podías. Haz que se pueda.

Edecan debe:

1. Conservar la intención original y el error observado.
2. Reproducir o diagnosticar el límite antes de editar.
3. Explicar qué necesita cambiar y pedir una confirmación comprensible si la
   acción puede modificar archivos, instalar software, ejecutar comandos o
   afectar cuentas externas.
4. Crear un punto de retorno y preservar cualquier trabajo previo de la
   persona.
5. Aplicar el cambio en el menor alcance posible.
6. Ejecutar pruebas relevantes y registrar evidencia del resultado.
7. Reintentar automáticamente la intención original.
8. Informar el resultado, no una lista de detalles internos. Si falla, restaurar
   el estado seguro o continuar diagnosticando sin ocultar el fallo.

Las reparaciones nunca hacen `push`, publican, compran, envían mensajes ni
alteran servicios externos sin una autorización específica para ese efecto.

## Significado de “cualquier cosa”

“Cualquier cosa” significa cualquier resultado que el equipo o un servicio
conectado pueda ejecutar de forma legítima y que la persona haya autorizado.
No significa evadir permisos del sistema operativo, términos de un servicio,
controles de seguridad ni limitaciones físicas. Cuando algo no sea posible,
Edecan debe identificar el límite concreto y la vía válida para superarlo; un
“no puedo” genérico no es una respuesta final.

## Criterios de aceptación

Una capacidad no está terminada porque exista una ruta o compile. Está
terminada cuando una persona puede:

1. Pedir el resultado en lenguaje natural desde Edecan.
2. Comprender y aprobar los efectos sensibles sin jerga.
3. Obtener evidencia de que la acción real ocurrió.
4. Corregir un fallo desde la misma conversación.
5. Repetir el recorrido después de reiniciar la aplicación.

Si una persona necesita entender la arquitectura para usar Edecan, el producto
falló aunque el código sea correcto.
