"""Selección ligera de capacidades para cada turno del asistente.

El registro de Edecán contiene decenas de herramientas. Enviarlas todas al
modelo en cada frase aumenta costo y, más importante, hace menos probable que
elija la capacidad correcta. Este módulo convierte lenguaje cotidiano en un
conjunto pequeño de *familias* de herramientas. No ejecuta nada ni decide los
argumentos: esa sigue siendo responsabilidad del modelo y del gate de
confirmación de :class:`edecan_core.agent.Agent`.

La selección es deliberadamente conservadora:

* siempre conserva utilidades generales y la escalera de skills;
* une familias, por lo que una sola frase puede pedir varias cosas;
* solo ofrece ``acceder_codigo_local`` ante una petición explícita de reparar
  la instalación/código de Edecán;
* nunca atraviesa flags de plan: recibe specs ya filtradas por el registry;
* si no reconoce una intención, el chat sigue funcionando con conocimiento
  del modelo, búsqueda y descubrimiento de skills, sin volver a exponer todo.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence

from edecan_schemas import ToolSpec

_ALWAYS_AVAILABLE = frozenset(
    {
        "buscar_skills",
        "buscar_web",
        "calculadora",
        "hora_actual",
        "instalar_skill",
        "listar_skills",
        "usar_skill",
    }
)

# Un registry pequeño (un agente restringido, un test o una integración con
# pocas tools MCP) no tiene el problema de sobrecarga que motivó este router.
# Conservarlo completo evita esconder capacidades en esos contextos y deja la
# selección semántica para el registry general de Edecán (46+ tools).
_SMALL_CATALOG_LIMIT = 12

_LEXICAL_STOPWORDS = frozenset(
    {
        "algo",
        "assistant",
        "asistente",
        "con",
        "crear",
        "cuenta",
        "desde",
        "esta",
        "este",
        "herramienta",
        "para",
        "puede",
        "quiero",
        "the",
        "tool",
        "tools",
        "user",
        "usar",
        "use",
        "usuario",
    }
)

# Los nombres son contratos internos estables de tools existentes. Las
# palabras son resultados que diría una persona, no nombres de pantallas.
_FAMILIES: tuple[tuple[frozenset[str], frozenset[str]], ...] = (
    (
        frozenset(
            {"correo", "correos", "email", "emails", "gmail", "outlook", "responde", "reply"}
        ),
        frozenset({"buscar_correo", "enviar_correo"}),
    ),
    (
        frozenset({"mensaje", "mensajes", "whatsapp", "telegram", "discord", "slack", "sms"}),
        frozenset({"enviar_mensaje", "leer_mensajes"}),
    ),
    (
        frozenset({"contacto", "contactos", "telefono", "telefonos", "addressbook"}),
        frozenset({"buscar_contactos", "gestionar_contacto"}),
    ),
    (
        frozenset(
            {
                "pendiente",
                "pendientes",
                "recordatorio",
                "recordatorios",
                "recuerdame",
                "recuerda",
                "remind",
                "reminder",
                "tarea",
                "tareas",
            }
        ),
        frozenset({"crear_recordatorio", "listar_recordatorios"}),
    ),
    (
        frozenset(
            {"agenda", "calendario", "calendar", "cita", "citas", "evento", "eventos", "reunion"}
        ),
        frozenset({"agenda_eventos", "crear_evento"}),
    ),
    (
        frozenset(
            {
                "automatiza",
                "automatizar",
                "automatizacion",
                "automatizaciones",
                "rutina",
                "recurrente",
            }
        ),
        frozenset({"gestionar_automatizacion"}),
    ),
    (
        frozenset(
            {"delega", "delegar", "mision", "misiones", "proyecto", "planifica", "planificar"}
        ),
        frozenset({"delegar_mision"}),
    ),
    (
        frozenset(
            {
                "archivo",
                "archivos",
                "documento",
                "documentos",
                "adjunto",
                "adjuntos",
                "docx",
            }
        ),
        frozenset({"consultar_documentos"}),
    ),
    (
        frozenset({"csv", "excel", "xlsx", "tabla", "tablas"}),
        frozenset({"analizar_tabla"}),
    ),
    (
        frozenset({"pdf"}),
        frozenset({"consultar_documentos", "extraer_tablas_pdf"}),
    ),
    (
        frozenset({"imagen", "imagenes", "foto", "fotos"}),
        frozenset({"analizar_imagen", "generar_imagen", "crear_contenido_social"}),
    ),
    (
        frozenset(
            {
                "linkedin", "tweet", "tweets", "post", "posts", "instagram",
                "facebook", "threads", "tiktok", "contenido", "social",
            }
        ),
        frozenset({"crear_contenido_social", "generar_imagen"}),
    ),
    (
        frozenset({"video", "videos"}),
        frozenset({"analizar_video"}),
    ),
    (
        frozenset({"contrato", "contratos", "legal", "clausula", "clausulas"}),
        frozenset(
            {
                "analizar_contrato",
                "comparar_contratos",
                "consultar_documentos",
                "generar_borrador_legal",
            }
        ),
    ),
    (
        frozenset(
            {
                "word",
                "presentacion",
                "presentaciones",
                "powerpoint",
                "diapositiva",
                "reporte",
                "informe",
            }
        ),
        frozenset({"crear_documento", "crear_pdf", "crear_presentacion", "exportar_analisis"}),
    ),
    (
        frozenset(
            {
                "grafico",
                "graficos",
                "chart",
                "estadistica",
                "datos",
                "serie",
                "predice",
                "prediccion",
                "anomalia",
            }
        ),
        frozenset(
            {
                "analizar_tabla",
                "detectar_anomalias",
                "exportar_analisis",
                "generar_grafico",
                "predecir_serie",
            }
        ),
    ),
    (
        frozenset({"factura", "facturas", "facturacion", "negocio", "empresa", "kpi", "beneficio"}),
        frozenset({"crear_factura", "estado_negocio"}),
    ),
    (
        frozenset({"inventario", "stock", "producto", "productos", "almacen"}),
        frozenset({"estado_inventario", "gestionar_inventario"}),
    ),
    (
        frozenset({"empleado", "empleados", "nomina", "ausencia", "vacaciones", "rrhh"}),
        frozenset({"gestionar_empleado", "preparar_nomina", "registrar_ausencia"}),
    ),
    (
        frozenset(
            {
                "dinero",
                "finanzas",
                "financiero",
                "gasto",
                "gastos",
                "ingreso",
                "ingresos",
                "transaccion",
                "presupuesto",
            }
        ),
        frozenset({"gestionar_presupuesto", "registrar_transaccion", "resumen_finanzas"}),
    ),
    (
        frozenset(
            {
                "pago",
                "pagos",
                "comprar",
                "compra",
                "vender",
                "venta",
                "acciones",
                "cripto",
                "cotizacion",
            }
        ),
        frozenset({"cotizar_activo", "preparar_orden", "preparar_pago"}),
    ),
    (
        frozenset({"web", "internet", "pagina", "sitio", "navega", "navegar", "precio", "precios"}),
        frozenset({"buscar_web", "comparar_precios", "extraer_datos_web", "navegar_web"}),
    ),
    (
        frozenset(
            {
                "post",
                "contenido",
                "guion",
                "redacta",
                "redactar",
                "escribe",
                "copy",
                "publica",
                "publicar",
                "social",
            }
        ),
        frozenset({"generar_contenido", "publicar_social"}),
    ),
    (
        frozenset({"anuncio", "anuncios", "publicidad", "campana", "ads"}),
        frozenset({"ads_preparar_campana", "ads_resumen"}),
    ),
    (
        frozenset({"vuelo", "vuelos", "hotel", "hoteles", "viaje", "viajes", "reserva", "paquete"}),
        frozenset(
            {
                "buscar_hoteles",
                "buscar_vuelos",
                "estado_vuelo",
                "preparar_reserva",
                "rastrear_paquete",
            }
        ),
    ),
    (
        frozenset({"casa", "hogar", "luz", "luces", "enchufe", "termostato", "homeassistant"}),
        frozenset({"casa_controlar", "casa_dispositivos", "casa_estado"}),
    ),
    (
        frozenset({"auto", "carro", "coche", "vehiculo", "vehiculos", "puerta"}),
        frozenset({"vehiculo_controlar", "vehiculo_estado"}),
    ),
    (
        frozenset(
            {
                "salud",
                "medicamento",
                "medicamentos",
                "ejercicio",
                "sueno",
                "agua",
                "laboratorio",
                "analito",
            }
        ),
        frozenset({"analizar_laboratorio", "registrar_salud", "resumen_salud"}),
    ),
    (
        frozenset(
            {
                "aprende",
                "aprender",
                "ensena",
                "ensenar",
                "estudia",
                "estudiar",
                "leccion",
                "tutor",
                "ejercicio",
            }
        ),
        frozenset({"tutor_evaluar", "tutor_leccion"}),
    ),
    (
        frozenset({"voz", "audio", "habla", "locucion", "podcast", "sonido", "voice"}),
        frozenset({"crear_podcast", "generar_efecto_sonido", "listar_voces", "sintetizar_voz"}),
    ),
    (
        frozenset({"dibujo", "dibuja", "ilustracion", "ilustra"}),
        frozenset({"generar_imagen"}),
    ),
    (
        frozenset({"skill", "skills", "capacidad", "capacidades", "extension", "plugin"}),
        frozenset(
            {"buscar_skills", "desinstalar_skill", "instalar_skill", "listar_skills", "usar_skill"}
        ),
    ),
)

_CONNECTOR_TOOL_NAMES = frozenset(
    {
        "ads_preparar_campana",
        "ads_resumen",
        "agenda_eventos",
        "buscar_contactos",
        "buscar_correo",
        "casa_controlar",
        "casa_dispositivos",
        "crear_evento",
        "enviar_correo",
        "enviar_mensaje",
        "leer_mensajes",
        "publicar_social",
        "vehiculo_controlar",
        "vehiculo_estado",
    }
)

_SELF_REPAIR_PHRASES = (
    "accede al codigo",
    "arregla tu codigo",
    "corrige tu codigo",
    "edita el codigo",
    "edita tus archivos",
    "haz que se pueda",
    "implementa esa capacidad",
    "modifica el repositorio",
    "repara el codigo",
    "repara tu codigo",
    "fix your code",
    "modify your source",
    "repair yourself",
)

_SELF_REPAIR_TOOL_NAMES = frozenset(
    {
        "acceder_codigo_local",
        "diagnosticar_autorreparacion_local",
        "gestionar_autorreparacion_local",
        "reparar_con_skill_local",
    }
)

_CREATION_ACTION_WORDS = frozenset(
    {
        "arma",
        "construye",
        "crea",
        "creame",
        "crear",
        "genera",
        "generar",
        "haz",
        "hazme",
        "prepara",
        "redacta",
        "redactar",
        "escribe",
    }
)
_CREATION_FORMAT_WORDS = frozenset(
    {
        "app",
        "apps",
        "aplicacion",
        "aplicaciones",
        "copy",
        "diapositivas",
        "documento",
        "documentos",
        "docx",
        "landing",
        "pagina",
        "paginas",
        "pdf",
        "post",
        "posts",
        "powerpoint",
        "ppt",
        "pptx",
        "presentacion",
        "presentaciones",
        "scaffold",
        "sitio",
        "web",
        "website",
        "word",
    }
)
_LEGACY_CREATOR_TOOL_NAMES = frozenset(
    {"crear_documento", "crear_pdf", "crear_presentacion", "generar_contenido"}
)
_CREATION_READER_TOOL_NAMES = frozenset(
    {
        "comparar_precios",
        "consultar_documentos",
        "exportar_analisis",
        "extraer_datos_web",
        "extraer_tablas_pdf",
        "navegar_web",
    }
)

_ROUTED_TOOL_NAMES = frozenset().union(
    _ALWAYS_AVAILABLE,
    *(tool_names for _, tool_names in _FAMILIES),
    _SELF_REPAIR_TOOL_NAMES,
    {"crear_artefactos"},
    {"configurar_credencial"},
)


def select_tool_specs(
    specs: Sequence[ToolSpec],
    user_text: str,
    *,
    recent_user_texts: Iterable[str] = (),
) -> list[ToolSpec]:
    """Devuelve las tools pertinentes para el resultado pedido en el turno.

    ``recent_user_texts`` conserva la intención en respuestas cortas como
    "sí, hazlo"; el llamador debe pasar solo unos pocos turnos recientes.
    La salida mantiene el orden del registry para que requests y snapshots
    sean deterministas.
    """

    if len(specs) <= _SMALL_CATALOG_LIMIT:
        return list(specs)

    normalized_current = _normalize(user_text)
    current_tokens = set(normalized_current.split())
    # El historial ayuda a resolver elipsis ("sí, hazlo", "también para
    # mañana"), pero no debe contaminar una petición nueva y autosuficiente.
    # Solo se hereda en turnos cortos o con un marcador explícito de
    # continuación.
    inherits_recent_intent = len(current_tokens) <= 6 or bool(
        current_tokens.intersection({"ademas", "esa", "ese", "eso", "hazlo", "igual", "tambien"})
    )
    combined = " ".join([*recent_user_texts, user_text]) if inherits_recent_intent else user_text
    normalized = _normalize(combined)
    tokens = set(normalized.split())
    selected_names = set(_ALWAYS_AVAILABLE)

    for keywords, tool_names in _FAMILIES:
        if tokens.intersection(keywords):
            selected_names.update(tool_names)

    creation_intent = bool(
        tokens.intersection(_CREATION_ACTION_WORDS)
        and tokens.intersection(_CREATION_FORMAT_WORDS)
    )
    publish_intent = bool(tokens.intersection({"publica", "publicalo", "publicar"}))
    if creation_intent:
        # Un único contrato produce todos los formatos y el manifest. Evita
        # mezclar generadores legacy sin evidencia en una petición compuesta.
        selected_names.difference_update(_LEGACY_CREATOR_TOOL_NAMES)
        selected_names.difference_update(_CREATION_READER_TOOL_NAMES)
        selected_names.add("crear_artefactos")
        if not publish_intent:
            selected_names.discard("publicar_social")

    # LinkedIn ya tiene un creador multimodal, pero no un conector OAuth de
    # primera parte. Una publicación explícita debe continuar por la sesión
    # local que la persona ya abrió y aprobará mediante `usar_computadora`,
    # no caer en `publicar_social` (Meta/X/YouTube) ni pedir una API key que
    # Edecán todavía no consume.
    if publish_intent and "linkedin" in tokens:
        selected_names.discard("publicar_social")
        selected_names.update(
            {"crear_contenido_social", "generar_imagen", "usar_computadora"}
        )

    create_image = bool(
        tokens.intersection({"crea", "crear", "genera", "generar", "dibuja", "ilustra"})
        and tokens.intersection({"foto", "imagen", "ilustracion", "dibujo"})
    )
    if create_image:
        selected_names.add("generar_imagen")
        selected_names.discard("analizar_imagen")

    # Extensiones MCP y futuras tools no aparecen necesariamente en la tabla
    # anterior. Un match por nombre (una palabra distintiva) o por al menos
    # dos palabras de su descripción las hace alcanzables sin volver a mandar
    # el catálogo entero. Las palabras genéricas se excluyen explícitamente.
    lexical_query = {token for token in tokens if len(token) >= 4} - _LEXICAL_STOPWORDS
    for spec in specs:
        if spec.name in _ROUTED_TOOL_NAMES:
            continue
        name_tokens = set(_normalize(spec.name).split())
        description_tokens = set(_normalize(spec.description).split()) - _LEXICAL_STOPWORDS
        if (
            lexical_query.intersection(name_tokens)
            or len(lexical_query.intersection(description_tokens)) >= 2
        ):
            selected_names.add(spec.name)

    if _is_self_repair_intent(normalized, tokens):
        selected_names.update(_SELF_REPAIR_TOOL_NAMES)

    if selected_names.intersection(_CONNECTOR_TOOL_NAMES) or tokens.intersection(
        {"api", "conecta", "conectar", "conexion", "credencial", "credenciales", "token"}
    ):
        selected_names.add("configurar_credencial")

    return [spec for spec in specs if spec.name in selected_names]


def build_capability_guidance(
    *,
    selected_specs: Sequence[ToolSpec],
    all_specs: Sequence[ToolSpec],
    language: str,
) -> str:
    """Política de entrada universal que se añade al system prompt.

    El catálogo completo solo se usa para descubrimiento honesto; la lista
    seleccionada indica qué contratos puede invocar el modelo ahora mismo.
    """

    selected = ", ".join(spec.name for spec in selected_specs) or "(none)"
    catalog = ", ".join(spec.name for spec in all_specs) or "(none)"
    if language == "en":
        return _GUIDANCE_EN.format(selected=selected, catalog=catalog)
    return _GUIDANCE_ES.format(selected=selected, catalog=catalog)


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", without_marks).strip()


def _is_self_repair_intent(normalized: str, tokens: set[str]) -> bool:
    if any(phrase in normalized for phrase in _SELF_REPAIR_PHRASES):
        return True
    repair_words = {"arregla", "corrige", "edita", "modifica", "repara", "repair", "fix"}
    source_words = {"codigo", "code", "edecan", "repositorio", "repo", "source", "archivos"}
    return bool(tokens.intersection(repair_words) and tokens.intersection(source_words))


_GUIDANCE_ES = """## Entrada universal y capacidades
La persona controla Edecán con una frase normal, escrita o hablada. Convierte esa frase en el
resultado final: nunca le pidas escoger un módulo, una pantalla, un agente ni el nombre de una
herramienta. Los nombres internos de abajo no son lenguaje de interfaz.

Sigue esta escalera invisible, en orden y sin saltarte niveles:
0. Ante una petición explícita de autorreparación, primero diagnostica en modo solo lectura y
   fundamenta el siguiente paso en ese resultado. Diagnosticar no autoriza modificar nada.
1. Usa una o varias herramientas existentes si ya resuelven la petición.
2. Si una capacidad existe pero falta una conexión o credencial, explica solo ese requisito y usa
   la configuración conversacional disponible; nunca inventes que la conexión está lista.
3. Si falta la capacidad, busca una skill local adecuada. Instalar instrucciones de terceros
   requiere el gate oficial de confirmación y nunca se hace a escondidas. Si una skill puede
   reparar el problema, prefiere `reparar_con_skill_local` antes de editar el núcleo.
4. Solo si esta es una instalación local administrada desde su código Y la persona pidió
   explícitamente reparar o ampliar Edecán, y los niveles anteriores no bastan, puedes proponer
   la reparación del núcleo o la herramienta de código local. También requieren confirmación y
   nunca hacen push ni tocan otra máquina.

Para peticiones compuestas, usa todas las capacidades pertinentes y conserva las partes
independientes que sí puedas completar. No respondas "no puedo" antes de revisar esta escalera.
Si ningún nivel resuelve todavía el objetivo, conviértelo en un camino de habilitación concreto:
di qué capacidad, conexión o permiso falta y cuál es el siguiente paso. Nunca afirmes que una
acción ocurrió sin un resultado real de tool.
Al crear, no llames Word/PDF/PowerPoint/sitio/app a una respuesta de texto: usa el creador de
artefactos y menciona solo archivos que su manifest marque como creados. Crear es privado y local;
publicar o desplegar es un efecto externo separado y conserva su confirmación oficial.

Una tool sensible se invoca una sola vez y el gate oficial debe ser la única pregunta de
confirmación; no preguntes "¿quieres que lo haga?" justo antes de disparar ese mismo gate. Pide
datos adicionales solo cuando sean indispensables para ejecutar, no para decidir qué módulo usar.

Herramientas operativas seleccionadas para este turno: {selected}
Catálogo disponible para resumir capacidades cuando la persona pregunte qué puedes hacer:
{catalog}
Solo puedes ejecutar las herramientas operativas incluidas en el campo `tools` de esta petición.
"""

_GUIDANCE_EN = """## Universal input and capabilities
The person controls Edecan with one normal spoken or written request. Turn it into the final
outcome: never ask them to choose a module, screen, agent, or internal tool name.

Follow this invisible ladder in order:
0. For an explicit self-repair request, diagnose in read-only mode first and base the next step on
   that result. A diagnosis never authorizes a modification.
1. Use one or more existing tools when they already solve the request.
2. If the capability exists but a connection or credential is missing, explain only that concrete
   requirement and use conversational setup when available; never pretend it is connected.
3. If the capability is missing, look for a suitable local skill. Third-party instructions require
   the official confirmation gate and are never installed silently. Prefer skill-based repair over
   editing Edecan's core when a suitable skill exists.
4. Only in a source-managed local installation, and only after an explicit request to repair or
   extend Edecan, and only when earlier levels are insufficient, may you propose core or local-code
   repair. It also requires confirmation, never pushes, and never changes another machine.

For compound requests, use every relevant capability and preserve independent parts you can
complete. Do not say "I can't" before checking this ladder. If no level solves the objective yet,
turn it into a concrete enablement path: state the missing capability, connection, or permission
and the next step. Never claim an action happened without a real tool result.
For creation requests, never label plain text as Word, PDF, PowerPoint, a website, or an app. Use
the artifact creator and mention only files marked as created by its manifest. Creation is private;
publishing or deploying is a separate external effect that keeps its official confirmation gate.

Invoke a sensitive tool once and let the official gate be the only confirmation question; do not
ask "should I do it?" immediately before triggering the same gate. Ask for additional data only
when execution requires it, never to make the person choose a module.

Operational tools selected for this turn: {selected}
Available catalog for an honest capability summary when asked: {catalog}
You may execute only the operational tools present in this request's `tools` field.
"""
