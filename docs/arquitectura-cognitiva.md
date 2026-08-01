# Arquitectura cognitiva de Edecán

Edecán no depende de un único prompt gigantesco. La identidad es un núcleo estable y las capacidades viven en módulos independientes que se pueden versionar, probar y mejorar sin reescribir todo el sistema.

## Core Identity

Define quién es Edecán y, sobre todo, cómo habla: alguien capaz hablándole normal a alguien que conoce. Vive en `packages/core/edecan_core/cognitive_architecture.py` como `CoreIdentityEngine`.

El tono se enseña con ejemplos del mismo mensaje escrito mal y bien, no con adjetivos. La versión anterior lo declaraba al revés —"Sistema Operativo Cognitivo Personal", ~20 virtudes y la orden de sonar "extremadamente competente"— y el modelo actuaba esa competencia en lugar de usar sus herramientas: llegó a resumir apartado por apartado unos documentos que nunca abrió, con la herramienta de búsqueda ofrecida en todas las llamadas de la conversación y pedida en ninguna. El comentario que encabeza `CORE_IDENTITY_ES` conserva la evidencia para que nadie devuelva ese texto creyendo que aporta personalidad.

El núcleo es corto a propósito: lo que está arriba pesa más, y todo lo que no cambia una respuesta concreta diluye la atención antes de llegar a lo que la persona pidió. Las capacidades y los criterios de trabajo viven una sola vez, en los módulos.

## Módulos superiores

| Módulo | Responsabilidad cognitiva | Implementación operativa relacionada |
|---|---|---|
| Grounding Engine | Leer antes de opinar y decir qué no está comprobado | `buscar_web`, `navegar_web`, lectura de archivos y evidencia inyectada |
| Persona Engine | Aplicar el tono, el trato, el estilo y el rol configurados | `PersonaConfig`, Perfil Vivo y perfil declarado por la persona |
| Memory Engine | Contexto estable, recuerdos y conexiones | memoria vectorial, consolidación en worker y `profile_context` garantizado |
| Planning Engine | Descomposición, dependencias, riesgo y replanteamiento | loop del agente, misiones y orquestador multiagente |
| Execution Engine | Convertir intención en resultado verificable | herramientas, eventos SSE, artefactos y confirmaciones |
| Tool Orchestrator | Elegir capacidades sin depender del modelo | registro de tools, MCP, skills, conectores y enrutamiento por intención |
| Computer Control | Operar el equipo emparejado | companion local, control remoto y permisos del sistema operativo |
| Learning Engine | Incorporar correcciones y ampliar capacidades | memoria, instalación de skills y autorreparación reversible |
| Proactive Engine | Detectar riesgos, oportunidades y automatizaciones | actividad, recordatorios, automatizaciones y misiones |
| Companion Layer | Adaptar la relación sin cambiar la identidad | estilos profesional, coach, amigo y romántico configurables |

El Grounding Engine va primero por la misma razón por la que el núcleo se acortó: lo que está arriba pesa más. Las reglas de honestidad ya existían repartidas por los módulos —"nunca inventes un recuerdo", "nunca afirmes que algo quedó hecho sin evidencia"— y aun así el modelo opinaba sobre documentos que nunca abrió, porque arriba se le pedía sonar competente y no enumerar limitaciones. El módulo no repite "sé honesto": define buscar como la conducta profesional y acota explícitamente esas reglas, que prohíben inventar límites inexistentes pero nunca ocultar uno real. El último punto es el contrapeso y pesa igual: la regla es para afirmaciones comprobables, no para conversar.

`CognitiveArchitecture` mantiene el Core separado de `DEFAULT_COGNITIVE_MODULES`, verifica que cada módulo tenga una clave única y conserva un orden determinista. La versión vigente es `2.1` (la `1.x` llevaba el núcleo largo con el manifiesto y los adjetivos; una evaluación de aquella no dice nada de esta).

## Contrato entre modelo y producto

El modelo conectado aporta la inteligencia lingüística y de razonamiento. Edecán aporta memoria, Internet, herramientas, ejecución, archivos, control de computadora, confirmaciones, progreso y continuidad. Cambiar Claude por Codex, Ollama, Kimi, Qwen u otro proveedor no elimina esas capacidades: el mismo `Agent`, el mismo registro de herramientas y el mismo contrato de eventos se mantienen.

El prompt describe cómo deben colaborar los módulos, pero no finge que las capacidades existen solo por mencionarlas. Cada acción real debe venir de una implementación y un resultado verificable.

## Progreso y continuidad

Las herramientas síncronas emiten `tool.start`, latidos `tool.progress` y `tool.end`. Si una herramienta crea una misión asíncrona, `tool.end` incluye únicamente su `mission_id` público. Web, iOS y Android consultan esa misión y mantienen los pasos visibles dentro de la misma respuesta del chat hasta terminar, fallar o pedir confirmación.

El Chat ID aparece en cada cliente y permite identificar con precisión el hilo que originó el trabajo.
