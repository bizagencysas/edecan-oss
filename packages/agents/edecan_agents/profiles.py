"""Perfiles del ecosistema de agentes (`ROADMAP_V2.md` §7.9, dueño WP-V2-06;
activación masiva + gate de confirmación real en `WP-V4-05`; perfil `voice`
activado en `WP-V5-05`).

`PROFILES` trae las 16 claves EXACTAS pinned en §7.9, y las **16 están
implementadas** (`disponible=True`, `allowed_tools` reales apuntando a
herramientas que ya existen en el workspace): los tres P0 originales
(`research`, `data_analyst`, `content`), los doce activados por `WP-V4-05`
(`ceo`, `design`, `legal`, `video`, `finance`, `marketing`, `sales`,
`social_media`, `developer`, `qa`, `security`, `devops`), y `voice`
(`WP-V5-05`, ver su propio comentario más abajo — sintetiza audio y lista
voces del tenant, ya no es "canal solamente"). Ninguna clave queda
`disponible=False` hoy — `IMPLEMENTED_AGENT_KEYS` coincide con las 16 claves
de `PROFILES`. Si en el futuro se sumara un 17º perfil todavía sin
herramientas reales, el patrón para declararlo (`disponible=False`,
`allowed_tools=frozenset()`) sigue siendo el mismo que usó `voice` hasta
`WP-V5-05` — el `Orchestrator` nunca le asigna un paso a un perfil
`disponible=False` (`plan()` solo elige entre `IMPLEMENTED_AGENT_KEYS`, ver
`orchestrator.py`).

## El guardrail evolucionó: de "ningún perfil toca una tool dangerous" a
## "toda tool dangerous pausa la misión para aprobación humana"

Hasta `WP-V4-05`, el único guardrail posible era que NINGÚN perfil activo
pudiera referenciar una tool `dangerous=True` — la única defensa disponible
era ocultarla para siempre (`RestrictedRegistry` la filtraba sin excepción).
Eso mantenía los tres perfiles P0 seguros, pero también significaba que
`marketing`/`sales`/`developer`/... nunca podrían activarse de verdad
mientras alguna de sus tools tentativas fuera `dangerous` — y la mayoría lo
son (publicar en redes, enviar correo, operar el companion del usuario).

`AgentProfile.permite_dangerous_con_confirmacion` (nuevo campo) resuelve
esto SIN debilitar el guardrail: cuando es `True`, `RestrictedRegistry`
(`registry_view.py`) deja de ocultar las tools `dangerous` de
`allowed_tools` — el sub-agente SÍ puede pedirlas, pero
`edecan_core.agent.Agent.run_turn` las intercepta igual que en el chat
normal (ARCHITECTURE.md §10.7): emite `confirmation_required` y detiene el
turno sin ejecutar nada, `Orchestrator.run` lo traduce en
`waiting_confirmation` (misión Y paso, `pending_tool_call` persistido), y
SOLO una aprobación humana explícita vía `POST /v1/missions/{id}/confirm`
hace que `Orchestrator._run_resumed_step` la ejecute — una vez, contra el
registro completo, nunca reinvocando al LLM (ver docstring de
`orchestrator.py`). Es decir: la defensa pasa de **"ocultar"** a **"pausar +
aprobación humana explícita"** — el mismo principio no negociable de
`ROADMAP_V2.md` §7.9/§8 ("nada peligroso se auto-aprueba"), aplicado con un
punto de control más útil que un booleano estático que nunca se puede
levantar.

## Guardrail vigente: ningún perfil P0 incluye tools `dangerous` ni de
## efectos externos, y NINGÚN perfil declara el flag sin necesitarlo

`research`/`data_analyst`/`content` — los tres perfiles P0, ver
`orchestrator.FALLBACK_AGENT_KEY` — solo referencian herramientas de
**lectura, análisis o generación de archivos nuevos** y mantienen
`permite_dangerous_con_confirmacion=False` (el default, comportamiento
IDÉNTICO al de antes de este campo): nada de `enviar_correo`,
`publicar_social`, `enviar_mensaje`, `usar_computadora` ni ninguna otra
herramienta `dangerous=True`. Esto sigue siendo deliberado: un sub-agente
corre una instrucción SINTÉTICA (generada por otro LLM, el planificador) sin
que un humano supervise cada turno intermedio, así que estos tres perfiles
—los que el planificador elige con más frecuencia, para las tareas más
genéricas— se quedan sin NINGÚN camino hacia una tool peligrosa, ni siquiera
detrás de una confirmación.

Para el resto de perfiles `disponible=True`, `permite_dangerous_con_confirmacion`
se fija en código a partir de una verificación real (grep `dangerous` en el
paquete de cada tool, documentado con un comentario por perfil más abajo):
`True` únicamente si `allowed_tools` referencia al menos una tool
`dangerous=True` hoy — nunca "por si acaso": un perfil sin ninguna tool
peligrosa se queda en `False` aunque la intención de diseño original lo
imaginara junto a otros que sí la tienen (ver el comentario de `finance` más
abajo para el caso concreto). `tests/test_profiles.py` verifica esta
correspondencia exacta en código, perfil por perfil.

`RestrictedRegistry` (`registry_view.py`) sigue siendo la "defensa en
profundidad": si algún perfil futuro sumara una tool `dangerous` a
`allowed_tools` sin marcar el flag, esa tool se queda invisible (falla
cerrado) en vez de ejecutarse sin aprobación.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class AgentProfile:
    """Definición de un sub-agente del ecosistema.

    `kw_only=True` (en vez de dejar `model_alias`/`disponible` en el orden
    posicional del enunciado del WP) porque `model_alias` trae default
    (`"principal"`) y `disponible` NO — con campos posicionales, Python
    exige que todo campo con default vaya después de todos los que no lo
    tienen, y aquí es al revés (`disponible` se declara después de
    `model_alias` en la lista de atributos "natural" de arriba abajo). Con
    `kw_only=True` el orden de declaración deja de importar para eso, y de
    paso cada instanciación de `PROFILES` (abajo) queda auto-documentada por
    los nombres de los argumentos.
    """

    key: str
    nombre: str
    descripcion: str
    system_prompt_extra: str
    allowed_tools: frozenset[str]
    model_alias: str = "principal"
    permite_dangerous_con_confirmacion: bool = False
    """`True` habilita, para este perfil, el gate de "pausar + aprobación
    humana explícita" sobre sus tools `dangerous` (ver docstring del módulo,
    sección "El guardrail evolucionó"): `Orchestrator._run_step` lo pasa tal
    cual a `RestrictedRegistry(..., permite_dangerous_con_confirmacion=...)`.
    Con el default `False` (comportamiento EXACTO de antes de este campo),
    cualquier tool `dangerous` de `allowed_tools` queda invisible para el
    sub-agente, como si no existiera — nunca se ejecuta, nunca se pide
    confirmación, simplemente no está. `False` es también la opción correcta
    (no solo la default) para cualquier perfil cuyas `allowed_tools` no
    incluyan hoy ninguna tool `dangerous=True` — activar el flag sin una tool
    peligrosa real detrás no cambia el comportamiento en nada (ver
    `registry_view.RestrictedRegistry.get`), así que dejarlo en `False` es
    más honesto que encenderlo "por si acaso"."""
    disponible: bool


PROFILES: dict[str, AgentProfile] = {
    # -----------------------------------------------------------------
    # Implementados (P0): allowed_tools apunta a herramientas reales que
    # YA existen en el workspace (edecan_toolkit / edecan_browser /
    # edecan_docanalysis / edecan_creative).
    # -----------------------------------------------------------------
    "research": AgentProfile(
        key="research",
        nombre="Investigación",
        descripcion=(
            "Busca y sintetiza información en la web y en los documentos del "
            "usuario; no opina ni inventa, solo reporta lo que encuentra."
        ),
        system_prompt_extra=(
            "Eres el sub-agente de INVESTIGACIÓN de una misión de Edecán. Tu "
            "instrucción viene de un planificador, no directamente del "
            "usuario: interprétala con el contexto que trae, sin pedir "
            "aclaraciones (no hay nadie que pueda responderte en este turno). "
            "Busca en la web y en los documentos del usuario, contrasta más "
            "de una fuente cuando sea posible, y entrega un resumen claro y "
            "verificable de lo que encontraste — nunca inventes datos ni "
            "cites una fuente que no consultaste. Si no encontraste nada "
            "útil, dilo explícitamente en vez de rellenar con suposiciones."
        ),
        allowed_tools=frozenset(
            {
                "buscar_web",
                "navegar_web",
                "extraer_datos_web",
                "consultar_documentos",
                "hora_actual",
            }
        ),
        disponible=True,
    ),
    "data_analyst": AgentProfile(
        key="data_analyst",
        nombre="Análisis de datos",
        descripcion=(
            "Analiza tablas, PDFs y documentos del usuario: estadística "
            "descriptiva, gráficos y reportes exportables."
        ),
        system_prompt_extra=(
            "Eres el sub-agente de ANÁLISIS DE DATOS de una misión de Edecán. "
            "Tu instrucción viene de un planificador: interprétala con el "
            "contexto que trae, sin pedir aclaraciones. Analiza las tablas, "
            "PDFs o documentos relevantes con las herramientas disponibles, "
            "usa la calculadora para cualquier cifra que reportes (nunca "
            "calcules de memoria un resultado que puedas verificar), y "
            "resume tus hallazgos con números concretos, no vaguedades. Si "
            "generas un gráfico o un reporte, menciona explícitamente que "
            "quedó disponible como archivo."
        ),
        allowed_tools=frozenset(
            {
                "analizar_tabla",
                "extraer_tablas_pdf",
                "generar_grafico",
                "exportar_analisis",
                "calculadora",
                "consultar_documentos",
                "predecir_serie",
                "detectar_anomalias",
            }
        ),
        disponible=True,
    ),
    "content": AgentProfile(
        key="content",
        nombre="Contenido",
        descripcion=(
            "Redacta y produce contenido y documentos de oficina (Word, "
            "PPT, PDF) a partir de un brief."
        ),
        system_prompt_extra=(
            "Eres el sub-agente de CONTENIDO de una misión de Edecán. Tu "
            "instrucción viene de un planificador: interprétala con el "
            "contexto que trae, sin pedir aclaraciones. Redacta el contenido "
            "solicitado (o el documento de oficina, si la instrucción pide "
            "un archivo) listo para entregar, en español salvo que se pida "
            "otro idioma explícitamente. No repitas literalmente la "
            "instrucción como si fuera tu respuesta: produce el contenido en "
            "sí."
        ),
        allowed_tools=frozenset(
            {"generar_contenido", "crear_documento", "crear_presentacion", "crear_pdf"}
        ),
        disponible=True,
    ),
    # -----------------------------------------------------------------
    # Activados por WP-V4-05: `disponible=True`, `allowed_tools` apunta a
    # herramientas reales que ya existen en el workspace. Cada perfil trae
    # un comentario listando cuáles de sus `allowed_tools` son
    # `dangerous=True` HOY (verificado con grep en el código real, no de
    # memoria) y por qué `permite_dangerous_con_confirmacion` quedó en el
    # valor que quedó — no todos los perfiles con tools de efectos externos
    # en su nombre tienen una tool realmente `dangerous` todavía (ver
    # `finance` más abajo).
    # -----------------------------------------------------------------
    "ceo": AgentProfile(
        key="ceo",
        nombre="Dirección general",
        descripcion=(
            "Visión de conjunto del negocio: sintetiza finanzas, facturación "
            "y operación para apoyar decisiones estratégicas."
        ),
        system_prompt_extra=(
            "Eres el sub-agente de DIRECCIÓN GENERAL: sintetizas el estado "
            "del negocio (finanzas, facturación, operación) para apoyar "
            "decisiones, sin ejecutar ninguna acción por tu cuenta."
        ),
        allowed_tools=frozenset({"resumen_finanzas", "estado_negocio", "consultar_documentos"}),
        # Ninguna de las 3 es `dangerous=True` hoy (`ResumenFinanzasTool` en
        # edecan_toolkit/finanzas.py, `EstadoNegocioTool` en
        # edecan_business/tools.py, `ConsultarDocumentosTool` en
        # edecan_toolkit/documentos.py — las tres de solo lectura/síntesis):
        # `permite_dangerous_con_confirmacion` se queda en `False`.
        disponible=True,
    ),
    "developer": AgentProfile(
        key="developer",
        nombre="Desarrollo",
        descripcion=(
            "Apoya tareas de programación: lee código y documentación "
            "técnica, y opera el companion del usuario cuando hace falta "
            "ejecutar algo (requiere aprobación humana, `usar_computadora` "
            "es `dangerous`)."
        ),
        system_prompt_extra=(
            "Eres el sub-agente de DESARROLLO: investigas documentación "
            "técnica y, cuando la instrucción lo requiere, operas el "
            "companion del usuario para leer/ejecutar código bajo su "
            "sandbox y aprobación."
        ),
        # `usar_computadora` (edecan_toolkit/computadora.py) es
        # `dangerous=True`: con `permite_dangerous_con_confirmacion=True`,
        # `Agent.run_turn` la detiene con `confirmation_required` la primera
        # vez que este perfil intenta usarla, y solo se ejecuta tras
        # aprobación humana explícita vía `POST /v1/missions/{id}/confirm`
        # (ver orchestrator.py, sección "Confirmación pendiente").
        # `consultar_documentos`/`buscar_web` no son `dangerous`.
        allowed_tools=frozenset({"usar_computadora", "consultar_documentos", "buscar_web"}),
        permite_dangerous_con_confirmacion=True,
        disponible=True,
    ),
    "marketing": AgentProfile(
        key="marketing",
        nombre="Marketing",
        descripcion="Genera y publica contenido de marketing, investiga tendencias.",
        system_prompt_extra=(
            "Eres el sub-agente de MARKETING: generas contenido y piezas "
            "visuales, investigas tendencias y, cuando se aprueba, publicas "
            "en los canales sociales conectados."
        ),
        # `publicar_social` (edecan_toolkit/contenido.py) es `dangerous=True`
        # — efecto externo real (publica en un canal conectado del tenant).
        # `generar_contenido`/`generar_imagen`/`buscar_web` no son
        # `dangerous`.
        allowed_tools=frozenset(
            {"generar_contenido", "publicar_social", "generar_imagen", "buscar_web"}
        ),
        permite_dangerous_con_confirmacion=True,
        disponible=True,
    ),
    "finance": AgentProfile(
        key="finance",
        nombre="Finanzas",
        descripcion="Analiza finanzas, cotiza activos y gestiona presupuestos.",
        system_prompt_extra=(
            "Eres el sub-agente de FINANZAS: analizas transacciones, cotizas "
            "activos y llevas presupuestos, siempre en modo informativo — "
            "cualquier orden real de pago/compra exige confirmación explícita "
            "del usuario fuera de este flujo (ROADMAP_V2.md §8, guardrail de "
            "dinero)."
        ),
        allowed_tools=frozenset(
            {"resumen_finanzas", "registrar_transaccion", "cotizar_activo", "gestionar_presupuesto"}
        ),
        # Verificado con grep: NINGUNA de estas 4 es `dangerous=True` hoy
        # (`ResumenFinanzasTool`/`RegistrarTransaccionTool` en
        # edecan_toolkit/finanzas.py, `CotizarActivoTool`/
        # `GestionarPresupuestoTool` en edecan_commerce/tools.py — las
        # tools `dangerous=True` de edecan_commerce son otras dos,
        # `preparar_pago`/`preparar_orden`, que este perfil NO tiene en su
        # `allowed_tools`). Por eso `permite_dangerous_con_confirmacion`
        # se queda en `False` pese a que la intención de diseño original
        # agrupaba a `finance` junto a otros perfiles con efectos externos:
        # activar el flag sin una tool `dangerous` real detrás no cambiaría
        # nada en tiempo de ejecución (ver `registry_view.py`), así que
        # dejarlo en `False` documenta con precisión el estado real del
        # código. Si en el futuro este perfil suma `preparar_pago`/
        # `preparar_orden` a `allowed_tools`, el flag debe pasar a `True`
        # en ese mismo cambio.
        disponible=True,
    ),
    "sales": AgentProfile(
        key="sales",
        nombre="Ventas",
        descripcion="Gestiona contactos y prospectos, redacta seguimientos.",
        system_prompt_extra=(
            "Eres el sub-agente de VENTAS: das seguimiento a contactos y "
            "prospectos y redactas comunicación de seguimiento."
        ),
        # `enviar_correo` (edecan_toolkit/correo.py) es `dangerous=True` —
        # efecto externo real. `buscar_contactos`/`gestionar_contacto` no lo
        # son.
        allowed_tools=frozenset({"buscar_contactos", "gestionar_contacto", "enviar_correo"}),
        permite_dangerous_con_confirmacion=True,
        disponible=True,
    ),
    "design": AgentProfile(
        key="design",
        nombre="Diseño",
        descripcion="Genera imágenes y piezas visuales, arma documentos de presentación.",
        system_prompt_extra=(
            "Eres el sub-agente de DISEÑO: generas imágenes y armas "
            "presentaciones/documentos visuales a partir de un brief."
        ),
        # Ninguna de las 3 es `dangerous=True` hoy (`GenerarImagenTool`/
        # `CrearPresentacionTool`/`CrearDocumentoTool`, todas en
        # edecan_creative/tools.py — generan un archivo nuevo, no publican
        # ni notifican a nadie): `permite_dangerous_con_confirmacion` se
        # queda en `False`.
        allowed_tools=frozenset({"generar_imagen", "crear_presentacion", "crear_documento"}),
        disponible=True,
    ),
    "legal": AgentProfile(
        key="legal",
        nombre="Legal",
        descripcion=(
            "Analiza y compara contratos, redacta borradores — SIEMPRE "
            "informativo, nunca sustituye asesoría legal profesional."
        ),
        system_prompt_extra=(
            "Eres el sub-agente LEGAL: analizas/comparas contratos y "
            "redactas borradores. Tu respuesta SIEMPRE es informativa, "
            "nunca asesoría legal vinculante — cada herramienta de "
            "`edecan_advisory` embebe el disclaimer obligatorio "
            "(ROADMAP_V2.md §8)."
        ),
        # Ninguna de las 4 es `dangerous=True` hoy (las tres de
        # edecan_advisory/legal.py más `consultar_documentos`): son
        # informativas/de lectura, nunca envían ni publican nada — el
        # disclaimer legal obligatorio (ROADMAP_V2.md §8) va embebido en el
        # `content` de cada resultado, no depende de este flag.
        allowed_tools=frozenset(
            {
                "analizar_contrato",
                "comparar_contratos",
                "generar_borrador_legal",
                "consultar_documentos",
            }
        ),
        disponible=True,
    ),
    "video": AgentProfile(
        key="video",
        nombre="Video",
        descripcion=(
            "Analiza y describe contenido audiovisual: imágenes sueltas "
            "(fotogramas) y video real (frames + visión por lotes, "
            "WP-V3-14). Producción/edición de video sigue P2 "
            "(ROADMAP_V2.md §6.3)."
        ),
        system_prompt_extra=(
            "Eres el sub-agente de VIDEO: analizas imágenes sueltas y "
            "archivos de video (extrayendo una muestra de fotogramas); no "
            "tienes herramientas de edición ni generación de video."
        ),
        # Ninguna de las 2 es `dangerous=True` hoy (`AnalizarImagenTool` en
        # edecan_docanalysis/vision.py, `AnalizarVideoTool` en
        # edecan_docanalysis/video.py — ambas de solo lectura/análisis).
        allowed_tools=frozenset({"analizar_imagen", "analizar_video"}),
        disponible=True,
    ),
    "voice": AgentProfile(
        key="voice",
        nombre="Voz",
        descripcion=(
            "Lista las voces disponibles del tenant y sintetiza audio a "
            "partir de texto, guardándolo en Archivos. Voz avanzada "
            "(interrupciones naturales, clonación autorizada) sigue siendo "
            "P2 (ROADMAP_V2.md §6.3); la voz web/telefónica EN VIVO sigue "
            "resolviéndose como CANAL (§4 de ARCHITECTURE.md), no dentro de "
            "una misión — este perfil cubre el caso async/por lotes (p. ej. "
            "un paso de una misión que necesita dejar un audio listo)."
        ),
        system_prompt_extra=(
            "Eres el sub-agente de VOZ de una misión de Edecán: puedes "
            "listar las voces disponibles del tenant y sintetizar audio a "
            "partir de un texto, dejándolo guardado en Archivos. No "
            "gestionas llamadas ni conversación de voz en vivo — eso sigue "
            "siendo un canal de entrada/salida (ARCHITECTURE.md §4), no una "
            "herramienta de este perfil."
        ),
        # `sintetizar_voz`/`listar_voces` — nombres pinned de WP-V5-10
        # (ARCHITECTURE.md §14). Ninguna de las 2 es `dangerous=True`: ambas
        # son de solo lectura (listar voces) o generan un archivo nuevo
        # (sintetizar audio a Archivos) sin publicar/notificar/gastar nada
        # real — mismo criterio que `design`/`content` más arriba. Si esas
        # tools de voz todavía no están instaladas en runtime (paquete que
        # las aporta aún no aterrizó), `RestrictedRegistry.get()` simplemente
        # no las ofrece (`self._wrapped.get(name)` devuelve `None`) y el paso
        # degrada exactamente igual que cualquier otro perfil cuyas tools
        # reales todavía no existen — seguro por diseño, no hace falta un
        # caso especial aquí (mismo criterio documentado para "browser"/
        # otros paquetes opcionales en `registry_view.py`).
        allowed_tools=frozenset({"sintetizar_voz", "listar_voces"}),
        disponible=True,
    ),
    "social_media": AgentProfile(
        key="social_media",
        nombre="Redes sociales",
        descripcion="Publica y programa contenido en redes, lee mensajes entrantes.",
        system_prompt_extra=(
            "Eres el sub-agente de REDES SOCIALES: generas y publicas "
            "contenido en los canales conectados, y lees/gestionas mensajes "
            "entrantes de mensajería oficial."
        ),
        # `publicar_social` (edecan_toolkit/contenido.py) y `enviar_mensaje`
        # (edecan_messaging/tools.py) son `dangerous=True` — dos efectos
        # externos reales en este perfil. `generar_contenido`/
        # `leer_mensajes` no lo son.
        allowed_tools=frozenset(
            {"publicar_social", "generar_contenido", "leer_mensajes", "enviar_mensaje"}
        ),
        permite_dangerous_con_confirmacion=True,
        disponible=True,
    ),
    "qa": AgentProfile(
        key="qa",
        nombre="Calidad (QA)",
        descripcion="Prueba software: ejecuta comandos/tests en el companion, investiga fallos.",
        system_prompt_extra=(
            "Eres el sub-agente de QA: ejecutas pruebas en el companion del "
            "usuario (bajo su sandbox y aprobación) e investigas fallos "
            "reportados."
        ),
        # `usar_computadora` (edecan_toolkit/computadora.py) es
        # `dangerous=True`. `consultar_documentos` no lo es.
        allowed_tools=frozenset({"usar_computadora", "consultar_documentos"}),
        permite_dangerous_con_confirmacion=True,
        disponible=True,
    ),
    "security": AgentProfile(
        key="security",
        nombre="Seguridad",
        descripcion=(
            "Revisión de seguridad: analiza configuración, investiga vulnerabilidades conocidas."
        ),
        system_prompt_extra=(
            "Eres el sub-agente de SEGURIDAD: revisas configuración vía el "
            "companion del usuario e investigas vulnerabilidades conocidas, "
            "siempre en modo informativo — nunca aplicas un cambio sin "
            "aprobación humana explícita."
        ),
        # `usar_computadora` (edecan_toolkit/computadora.py) es
        # `dangerous=True`. `buscar_web` no lo es.
        allowed_tools=frozenset({"usar_computadora", "buscar_web"}),
        permite_dangerous_con_confirmacion=True,
        disponible=True,
    ),
    "devops": AgentProfile(
        key="devops",
        nombre="DevOps",
        descripcion=(
            "Automatización de despliegue e infraestructura. Docker/K8s/"
            "deploys reales son P2 (ROADMAP_V2.md §6.3): hoy limitado a "
            "acciones allowlisted del companion."
        ),
        system_prompt_extra=(
            "Eres el sub-agente de DEVOPS: hoy solo puedes operar binarios "
            "allowlisted del companion del usuario, bajo su sandbox y "
            "aprobación — no tienes integración directa con proveedores de "
            "nube ni orquestadores."
        ),
        # `usar_computadora` (edecan_toolkit/computadora.py) es
        # `dangerous=True` — la única tool de este perfil, y por eso también
        # el más restringido de los "CON dangerous": cada acción pasa por
        # confirmación humana explícita.
        allowed_tools=frozenset({"usar_computadora"}),
        permite_dangerous_con_confirmacion=True,
        disponible=True,
    ),
}

IMPLEMENTED_AGENT_KEYS: frozenset[str] = frozenset(
    key for key, p in PROFILES.items() if p.disponible
)
"""Claves con `disponible=True` — el único conjunto entre el que
`Orchestrator.plan()` puede elegir (`orchestrator.py`)."""
