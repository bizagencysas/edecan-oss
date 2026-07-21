# Arquitectura cognitiva de Edecán

Edecán no depende de un único prompt gigantesco. La identidad es un núcleo estable y las capacidades viven en módulos independientes que se pueden versionar, probar y mejorar sin reescribir todo el sistema.

## Core Identity

Define qué es Edecán, su misión permanente, su forma de comunicar, sus criterios de calidad y el principio de optimizar la trayectoria de la persona, no solo la respuesta actual. Vive en `packages/core/edecan_core/cognitive_architecture.py` como `CoreIdentityEngine`.

## Módulos superiores

| Módulo | Responsabilidad cognitiva | Implementación operativa relacionada |
|---|---|---|
| Persona Engine | Tono, trato, estilo y rol | `PersonaConfig`, Perfil Vivo y perfil declarado por la persona |
| Memory Engine | Contexto estable, recuerdos y conexiones | memoria vectorial, consolidación en worker y `profile_context` garantizado |
| Planning Engine | Descomposición, dependencias, riesgo y replanteamiento | loop del agente, misiones y orquestador multiagente |
| Execution Engine | Convertir intención en resultado verificable | herramientas, eventos SSE, artefactos y confirmaciones |
| Tool Orchestrator | Elegir capacidades sin depender del modelo | registro de tools, MCP, skills, conectores y enrutamiento por intención |
| Computer Control | Operar el equipo emparejado | companion local, control remoto y permisos del sistema operativo |
| Learning Engine | Incorporar correcciones y ampliar capacidades | memoria, instalación de skills y autorreparación reversible |
| Proactive Engine | Detectar riesgos, oportunidades y automatizaciones | actividad, recordatorios, automatizaciones y misiones |
| Companion Layer | Adaptar la relación sin cambiar la identidad | estilos profesional, coach, amigo y romántico configurables |

`CognitiveArchitecture` mantiene el Core separado de `DEFAULT_COGNITIVE_MODULES`, verifica que cada módulo tenga una clave única y conserva un orden determinista. La versión inicial es `1.0`.

## Contrato entre modelo y producto

El modelo conectado aporta la inteligencia lingüística y de razonamiento. Edecán aporta memoria, Internet, herramientas, ejecución, archivos, control de computadora, confirmaciones, progreso y continuidad. Cambiar Claude por Codex, Ollama, Kimi, Qwen u otro proveedor no elimina esas capacidades: el mismo `Agent`, el mismo registro de herramientas y el mismo contrato de eventos se mantienen.

El prompt describe cómo deben colaborar los módulos, pero no finge que las capacidades existen solo por mencionarlas. Cada acción real debe venir de una implementación y un resultado verificable.

## Progreso y continuidad

Las herramientas síncronas emiten `tool.start`, latidos `tool.progress` y `tool.end`. Si una herramienta crea una misión asíncrona, `tool.end` incluye únicamente su `mission_id` público. Web, iOS y Android consultan esa misión y mantienen los pasos visibles dentro de la misma respuesta del chat hasta terminar, fallar o pedir confirmación.

El Chat ID aparece en cada cliente y permite identificar con precisión el hilo que originó el trabajo.
