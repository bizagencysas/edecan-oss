"""Entrada de chat y `AgentEvent` — unión discriminada del loop del agente (§10.7).

`AgentEvent` es el contrato exacto de los eventos que emite
`edecan_core.agent.Agent.run_turn` y que `edecan_api` traduce 1:1 a eventos
SSE (`message.delta`, `tool.start`, `tool.end`, `confirmation.required`,
`message.done`, `error`).
"""

from __future__ import annotations

import ipaddress
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    TypeAdapter,
    model_validator,
)


class ChatMessageIn(BaseModel):
    """Cuerpo de `POST /v1/conversations/{id}/messages`.

    `model`/`effort` son el override por turno del selector de modelos: elegir
    y enviar en el mismo gesto sin depender de una carrera entre el PUT
    `/model` y este POST. Ausentes = usar lo que la conversación ya tiene
    persistido (y si tampoco hay nada, automático). Cuando vienen, la API los
    persiste en la conversación además de aplicarlos a este turno.

    `model` NO se valida contra el catálogo aquí: el catálogo vive en
    `edecan_llm` (`config/modelos.yml` -> `modelos_chat`) y este paquete no
    depende de él. La API devuelve 422 con un id fuera de catálogo.
    """

    text: str = Field(default="", max_length=50_000)
    attachments: list[UUID] = Field(default_factory=list, max_length=10)
    model: str | None = Field(default=None, max_length=200)
    effort: Literal["bajo", "medio", "alto"] | None = None

    @model_validator(mode="after")
    def validate_content(self) -> ChatMessageIn:
        if not self.text.strip() and not self.attachments:
            raise ValueError("el mensaje necesita texto o al menos un archivo")
        if len(self.attachments) != len(set(self.attachments)):
            raise ValueError("los archivos adjuntos no pueden repetirse")
        return self


class PendingConfirmationOut(BaseModel):
    """Acción peligrosa que la UI puede reconstruir al reabrir un chat.

    Deliberadamente contiene solo el mismo contrato público emitido por
    ``confirmation.required``. El estado serializado usado para reanudar el
    agente nunca forma parte de esta respuesta.
    """

    tool_call_id: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    args: dict[str, Any] = Field(default_factory=dict)


class TextDeltaEvent(BaseModel):
    type: Literal["text_delta"] = "text_delta"
    text: str


class ToolStartEvent(BaseModel):
    type: Literal["tool_start"] = "tool_start"
    tool_call_id: str | None = Field(default=None, min_length=1, max_length=255)
    name: str
    args: dict


class ToolProgressEvent(BaseModel):
    """Latido público mientras una herramienta continúa trabajando.

    No contiene razonamiento interno ni argumentos potencialmente sensibles:
    solo identidad de la ejecución, segundos transcurridos y un estado humano
    que los clientes pueden mostrar sin inventar progreso.
    """

    type: Literal["tool_progress"] = "tool_progress"
    tool_call_id: str | None = Field(default=None, min_length=1, max_length=255)
    name: str
    elapsed_seconds: int = Field(default=0, ge=0)
    message: str = Field(default="Trabajando", max_length=255)


class ArtifactRef(BaseModel):
    """Archivo privado creado por una tool y descargable por su dueño."""

    file_id: UUID
    filename: str = Field(min_length=1, max_length=255)
    mime: str | None = Field(default=None, max_length=255)


def _safe_public_url(value: str) -> str:
    """Acepta solo HTTP(S) absoluto y sin credenciales embebidas."""

    clean = value.strip()
    parsed = urlsplit(clean)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("la URL debe ser http(s) absoluta")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("la URL no puede contener credenciales")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        raise ValueError("la URL debe apuntar a un host público")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError("la URL no puede apuntar a una red privada o reservada")
    return clean


class OpenURLAction(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9._:-]+$")
    label: str = Field(min_length=1, max_length=80)
    action: Literal["open_url"] = "open_url"
    url: str = Field(min_length=1, max_length=2048)

    @model_validator(mode="after")
    def validate_url(self) -> OpenURLAction:
        self.url = _safe_public_url(self.url)
        return self


class OpenScreenAction(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9._:-]+$")
    label: str = Field(min_length=1, max_length=80)
    action: Literal["open_screen"] = "open_screen"
    screen: Literal[
        "assistant",
        "create",
        "remote",
        "activity",
        "settings",
        "travel",
        "orders",
        "files",
        "skills",
    ]


class PrefillMessageAction(BaseModel):
    """Sugerencia visible: rellena el compositor, nunca ejecuta por sí sola."""

    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9._:-]+$")
    label: str = Field(min_length=1, max_length=80)
    action: Literal["prefill_message"] = "prefill_message"
    message: str = Field(min_length=1, max_length=2000)


class CopyTextAction(BaseModel):
    """Copia un texto literal al portapapeles. Sin red, sin efectos remotos.

    `text` viaja completo dentro de la propia acción (no es una referencia):
    el mismo tope que `SocialDraftBlock.copy` (64000 chars) porque el caso de
    uso es el mismo -- copiar un borrador largo -- y no tiene sentido un
    límite distinto para el mismo dato según de qué bloque salió.
    """

    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9._:-]+$")
    label: str = Field(min_length=1, max_length=80)
    action: Literal["copy_text"] = "copy_text"
    text: str = Field(min_length=1, max_length=64_000)


class SaveArtifactAction(BaseModel):
    """Guarda en Fotos/Archivos un artifact ya mostrado en la card.

    `file_id` DEBE pertenecer a los artifacts que la MISMA tool call
    devolvió -- la misma regla que ya protege a `MediaBlock` hoy. Ese cruce
    vive en `rich_blocks_from_tool_data` (`edecan_core.agent`, paso 2 del
    plan): este módulo no conoce el resto de artifacts del turno y por eso
    no puede validarlo aquí. El sistema operativo pide su propio permiso de
    Fotos al guardar -- esta acción no puede exfiltrar nada que la persona no
    esté viendo ya en pantalla.
    """

    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9._:-]+$")
    label: str = Field(min_length=1, max_length=80)
    action: Literal["save_artifact"] = "save_artifact"
    file_id: UUID


class SendMessageAction(BaseModel):
    """Manda `message` como si la persona lo hubiera escrito: turno normal.

    Mismo contrato que `QuestionOption.value` (que ya hace esto desde el
    modal de `QuestionBlock`): el texto es VISIBLE en el hilo, nunca se
    manda a ciegas, y el turno resultante pasa por todas las salvaguardas
    normales del agente -- esta acción no invoca tools directamente.
    """

    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9._:-]+$")
    label: str = Field(min_length=1, max_length=80)
    action: Literal["send_message"] = "send_message"
    message: str = Field(min_length=1, max_length=2000)


class ApproveDraftAction(BaseModel):
    """Privilegiada: dispara `POST /v1/content/social/publish` tras el modal
    nativo de confirmación (`TarjetaConfirmacion`), nunca sola.

    Doble candado (el segundo vive fuera de este módulo, en el paso 2 del
    plan): (1) `rich_blocks_from_tool_data` solo deja acuñar esta acción a
    tools de una allowlist first-party -- una card inyectada por un MCP de
    terceros con un botón "Aprobar" se descarta ENTERA, no solo el botón;
    (2) `draft_id` se revalida server-side contra el tenant al publicar. Es
    la generalización por dato del botón que hoy está cableado a mano en
    `SocialDraftCardView`.
    """

    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9._:-]+$")
    label: str = Field(min_length=1, max_length=80)
    action: Literal["approve_draft"] = "approve_draft"
    draft_id: str = Field(min_length=1, max_length=200)


class OpenConversationAction(BaseModel):
    """Abre una conversación existente por id.

    Navegación pura, sin efectos: el servidor valida pertenencia al tenant
    al resolver el id, la misma autorización que ya aplica al abrirla a
    mano desde la lista de conversaciones.
    """

    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9._:-]+$")
    label: str = Field(min_length=1, max_length=80)
    action: Literal["open_conversation"] = "open_conversation"
    conversation_id: UUID


class UnsupportedAction(BaseModel):
    """Acción con un `action` que este contrato todavía no conoce.

    Espejo de `NodoDesconocido` (más abajo): existe para que un botón de una
    versión futura del contrato no tumbe el bloque que lo contiene. Se
    decodifica -- no se descarta la card entera -- pero nunca se ejecuta ni
    se pinta; ver la regla de nivel de nodo/campo en el forward-compat del
    plan de SDUI. `extra="allow"` porque no sabemos qué campos trae una
    acción que no existe todavía; no los validamos, no los usamos.
    """

    model_config = ConfigDict(extra="allow")

    action: str


_ACCIONES_CONOCIDAS = {
    "open_url",
    "open_screen",
    "prefill_message",
    "copy_text",
    "save_artifact",
    "send_message",
    "approve_draft",
    "open_conversation",
}


def _accion_discriminador(valor: Any) -> str:
    """Tag de routing de `ChatAction`: cualquier `action` fuera del allowlist
    cae en `UnsupportedAction` en vez de reventar toda la unión.
    """

    if isinstance(valor, dict):
        accion = valor.get("action")
    else:
        accion = getattr(valor, "action", None)
    return accion if accion in _ACCIONES_CONOCIDAS else "desconocida"


ChatAction = Annotated[
    Annotated[OpenURLAction, Tag("open_url")]
    | Annotated[OpenScreenAction, Tag("open_screen")]
    | Annotated[PrefillMessageAction, Tag("prefill_message")]
    | Annotated[CopyTextAction, Tag("copy_text")]
    | Annotated[SaveArtifactAction, Tag("save_artifact")]
    | Annotated[SendMessageAction, Tag("send_message")]
    | Annotated[ApproveDraftAction, Tag("approve_draft")]
    | Annotated[OpenConversationAction, Tag("open_conversation")]
    | Annotated[UnsupportedAction, Tag("desconocida")],
    Discriminator(_accion_discriminador),
]
"""Allowlist discriminada de acciones de botón. `UnsupportedAction` es el
caso tolerante: una acción con `action` desconocido decodifica ahí en vez de
invalidar el nodo o el bloque que la contiene (ver `NodoCard` y
`GenericCardBlock` más abajo, y el nivel 1 de forward-compat del plan de
SDUI)."""


class ChatBlockBase(BaseModel):
    """Campos comunes para evolución y fallback de clientes antiguos."""

    schema_version: Literal[1] = 1
    fallback_text: str | None = Field(default=None, max_length=1000)


class MediaBlock(ChatBlockBase):
    type: Literal["media"] = "media"
    media_kind: Literal["image", "video", "audio"]
    artifact: ArtifactRef
    alt: str = Field(default="", max_length=1000)
    caption: str | None = Field(default=None, max_length=500)


class LinkPreviewBlock(ChatBlockBase):
    type: Literal["link_preview"] = "link_preview"
    url: str = Field(min_length=1, max_length=2048)
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=1000)
    site_name: str | None = Field(default=None, max_length=120)
    observed_at: str | None = Field(default=None, max_length=80)
    source_mode: Literal["demo", "live", "unknown"] = "unknown"
    actions: list[ChatAction] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def validate_url(self) -> LinkPreviewBlock:
        self.url = _safe_public_url(self.url)
        return self


class FlightCardBlock(ChatBlockBase):
    type: Literal["flight"] = "flight"
    offer_id: str = Field(min_length=1, max_length=200)
    airline: str = Field(min_length=1, max_length=200)
    origin: str = Field(min_length=2, max_length=12)
    destination: str = Field(min_length=2, max_length=12)
    departure: str | None = Field(default=None, max_length=80)
    arrival: str | None = Field(default=None, max_length=80)
    stops: int = Field(default=0, ge=0, le=12)
    price: str = Field(min_length=1, max_length=40)
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    source_mode: Literal["demo", "live", "unknown"] = "unknown"
    provider: str | None = Field(default=None, max_length=120)
    observed_at: str | None = Field(default=None, max_length=80)
    expires_at: str | None = Field(default=None, max_length=80)
    taxes: str | None = Field(default=None, max_length=40)
    cancellation: str | None = Field(default=None, max_length=500)
    actions: list[ChatAction] = Field(default_factory=list, max_length=3)


class HotelCardBlock(ChatBlockBase):
    type: Literal["hotel"] = "hotel"
    offer_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=300)
    city: str = Field(min_length=1, max_length=120)
    checkin: str | None = Field(default=None, max_length=40)
    checkout: str | None = Field(default=None, max_length=40)
    rating: str | None = Field(default=None, max_length=20)
    price: str = Field(min_length=1, max_length=40)
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    address: str | None = Field(default=None, max_length=300)
    image_url: str | None = Field(default=None, max_length=2048)
    source_mode: Literal["demo", "live", "unknown"] = "unknown"
    provider: str | None = Field(default=None, max_length=120)
    observed_at: str | None = Field(default=None, max_length=80)
    expires_at: str | None = Field(default=None, max_length=80)
    taxes: str | None = Field(default=None, max_length=40)
    cancellation: str | None = Field(default=None, max_length=500)
    actions: list[ChatAction] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def validate_image_url(self) -> HotelCardBlock:
        if not self.image_url:
            return self
        try:
            self.image_url = _safe_public_url(self.image_url)
        except ValueError:
            self.image_url = None
        return self


class SocialDraftBlock(ChatBlockBase):
    """Borrador de post social armado por `crear_contenido_social` (`edecan_creative.social`).

    El payload es deliberadamente compatible con `SocialContentOut`/
    `SocialContentDraft` (`POST /v1/content/social`,
    `ContentStudioModels.swift`) para que iOS decodifique el mismo tipo
    (`SocialContentDraft`) tanto desde el endpoint dedicado como desde esta
    card de chat, sin duplicar un segundo modelo. `target` es `None` cuando
    la tool corrió sin un destino LinkedIn conocido (otra plataforma, o el
    chat no lo indicó): el cliente entonces solo ofrece copiar el texto y
    guardar la imagen, nunca publicar de verdad. `target` es un identificador
    de destino ABIERTO (no un enum cerrado): `"personal"` es el único id
    reservado -- cualquier otro valor es el id que el propio tenant le puso a
    su destino de organización (ver `edecan_creative.marcas.BrandDestination`,
    que nunca vive en este paquete de esquemas para no invertir la
    dependencia). Publicar solo se ofrece cuando `target` no es `None` ni
    `"personal"`, y siempre exige una confirmación puntual del usuario contra
    `POST /v1/content/social/publish` (nunca automático).
    """

    type: Literal["social_draft"] = "social_draft"
    status: Literal["ready"] = "ready"
    platform: Literal["linkedin", "x"]
    # Mismo patrón de forma que `edecan_creative.marcas.DESTINATION_ID_PATTERN`
    # (`BrandDestination.id`), duplicado aquí a propósito: este paquete no
    # tiene dependencias más allá de Pydantic (es la base del monorepo) y no
    # puede importar `edecan_creative` sin invertir esa dependencia.
    target: str | None = Field(
        default=None,
        max_length=60,
        pattern=r"^[a-z][a-z0-9_-]{0,59}$",
    )
    # Nombre elegido a propósito (en vez de un alias): todo el resto de este
    # módulo se serializa con `model_dump(mode="json")` SIN `by_alias=True`
    # (ver `edecan_api.routers.conversations`), así que un alias aquí saldría
    # con el nombre del campo Python en el JSON del chat, no con "copy", y
    # rompería el contrato con iOS. Pydantic advierte que "copy" tapa el
    # método heredado `BaseModel.copy()` (deprecado en v2); es un aviso
    # inofensivo, no un error, y el mismo trade-off que ya acepta
    # `edecan_creative.social` en `ToolResult.data["copy"]`.
    copy: str = Field(min_length=1, max_length=64_000)
    parts: list[str] = Field(default_factory=list, max_length=200)
    alt_text: str = Field(default="", max_length=1000)
    offline_visual: bool = False
    visual_warning: str = Field(default="", max_length=500)
    artifacts: list[ArtifactRef] = Field(default_factory=list, max_length=10)
    requires_human_confirmation: bool = True


class QuestionOption(BaseModel):
    """Una opción del modal de `QuestionBlock`.

    `value` es lo que se manda de vuelta como mensaje del usuario si toca esta opción; si no
    se especifica, el cliente usa `label`. Separarlos permite mostrar "Personal" y mandar
    "Publícalo en mi cuenta personal", que le da al modelo una instrucción sin ambigüedad.
    """

    label: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=200)
    value: str | None = Field(default=None, max_length=500)


class QuestionBlock(ChatBlockBase):
    """Pregunta con opciones que el cliente muestra como modal antes de seguir trabajando.

    Existe para cerrar la brecha que causa la mayoría de los resultados malos: el modelo
    ADIVINA un dato que solo el usuario sabe (¿a qué cuenta va este post? ¿qué tono?) y se
    equivoca en silencio. Con esto pregunta una sola vez, barato, en vez de producir algo
    entero con la suposición equivocada.

    Contrato de flujo (a propósito el simple): emitir este bloque TERMINA el turno. La
    respuesta del usuario llega como un mensaje normal en el turno siguiente, así que no hay
    estado suspendido en el servidor ni un turno bloqueado esperando -- si el usuario cierra
    la app, ignora el modal o responde otra cosa distinta, no queda nada colgado.

    `multi_select` deja marcar varias opciones; el cliente las manda unidas en un solo
    mensaje. `allow_free_text` deja escribir una respuesta propia además de las opciones
    (equivalente al "Otro" de un formulario) -- nunca hay que obligar a elegir entre opciones
    que no incluyen la respuesta correcta.
    """

    type: Literal["question"] = "question"
    question: str = Field(min_length=1, max_length=500)
    # Etiqueta corta para el encabezado del modal ("Destino", "Tono"), no una oración.
    header: str | None = Field(default=None, max_length=40)
    options: list[QuestionOption] = Field(min_length=2, max_length=4)
    multi_select: bool = False
    allow_free_text: bool = True


# --- Card genérica (MVP de Server-Driven UI) --------------------------------
#
# `GenericCardBlock` es el ÚNICO tipo de bloque nuevo de este MVP: en vez de
# horneadar cada UI nueva en Swift (como los 6 bloques de arriba), la card es
# un árbol de 6 primitivas con estilo SEMÁNTICO (roles, nunca CSS/px/hex) que
# el cliente compone con sus propias vistas nativas. Las cards futuras nacen
# como instancias de `card`; los 6 bloques especializados de arriba NO se
# migran (evita doble render y ya están compilados y funcionando).
#
# Gobernanza de `schema_version` (vale para TODO este módulo, no solo para la
# card): un cambio ADITIVO -- un campo opcional nuevo en un nodo o bloque
# conocido, o un valor nuevo de un enum semántico que el cliente puede
# degradar a un default razonable -- JAMÁS sube `schema_version`. Solo sube
# en un cambio BREAKING: quitar o renombrar un campo, o cambiar el
# significado de uno que ya existía. Mientras un cambio sea aditivo, un
# cliente viejo que no reconoce el campo nuevo lo ignora (`decodeIfPresent` +
# default en Swift; Pydantic simplemente no lo pide) y sigue funcionando sin
# caer a `fallback_text`.
#
# Límites duros (DoS visual, no solo estética): una card no es un lienzo
# libre, es una pantalla de teléfono. Los topes están validados en Pydantic
# -- un payload patológico no llega nunca al cliente a intentar componerlo.
MAX_PROFUNDIDAD_CARD = 4
MAX_NODOS_CARD = 40
MAX_BOTONES_CARD = 4


class StackNode(BaseModel):
    """Contenedor: el VStack/HStack que hoy está horneado en Swift, como dato.

    Con stacks verticales y horizontales anidados se reconstruye el layout
    de cualquiera de los 6 bloques especializados de arriba. `fondo` mapea a
    algo como `EdecanTheme.morado.opacity(0.08)` en el cliente -- el backend
    elige el ROL, nunca un color concreto.
    """

    nodo: Literal["stack"] = "stack"
    eje: Literal["vertical", "horizontal"] = "vertical"
    espaciado: Literal["xs", "s", "m", "l"] = "m"
    relleno: Literal["ninguno", "compacto", "tarjeta"] = "ninguno"
    fondo: Literal["ninguno", "tarjeta", "acento_suave", "advertencia_suave", "exito_suave"] = (
        "ninguno"
    )
    alineacion: Literal["inicio", "centro", "fin"] = "inicio"
    # Tope local redundante con el conteo global de `GenericCardBlock`
    # (≤ MAX_NODOS_CARD), pero barato de declarar y evita construir en
    # memoria una lista absurda antes de que el validador del árbol la
    # rechace.
    hijos: list[NodoCard] = Field(default_factory=list, max_length=MAX_NODOS_CARD)


class TextoNode(BaseModel):
    """Resuelve la clase de bug 'titular muy grande': el tamaño deja de ser
    una decisión de Swift y pasa a ser un ROL (`rol`) que el backend elige de
    un enum cerrado -- un restyle global sigue siendo decisión del tema
    nativo, el backend no puede romper la coherencia visual.
    """

    nodo: Literal["texto"] = "texto"
    contenido: str = Field(min_length=1, max_length=4000)
    rol: Literal["titulo", "subtitulo", "cuerpo", "pie", "etiqueta"] = "cuerpo"
    color: Literal["primario", "secundario", "acento", "advertencia", "exito"] = "primario"
    enfasis: Literal["normal", "fuerte"] = "normal"
    max_lineas: int | None = Field(default=None, ge=1, le=20)


class ImagenNode(BaseModel):
    """Imagen dentro de una card compuesta (hoy `MediaBlock` solo existe
    suelto). `artifact` DEBE pertenecer a los artifacts que la misma tool
    call devolvió -- misma regla que `MediaBlock`, validada en
    `rich_blocks_from_tool_data` (paso 2 del plan), no aquí.
    """

    nodo: Literal["imagen"] = "imagen"
    artifact: ArtifactRef
    alt: str = Field(default="", max_length=1000)
    aspecto: Literal["cuadrada", "panoramica", "libre"] = "libre"
    esquinas: Literal["ninguna", "s", "m"] = "ninguna"


class VideoNode(BaseModel):
    """Video dentro de una card compuesta. Espejo de ``ImagenNode``: el
    ``artifact`` apunta a los bytes privados del MP4 (subidos por el worker
    con ``subir_archivo``), y el cliente lo reproduce en el chat con
    ``AVPlayer`` en vez de pintar una imagen estática. Misma invariante que
    ``ImagenNode``/``MediaBlock``: el ``artifact`` DEBE pertenecer a los
    artifacts que la misma tool call devolvió (validado en
    ``rich_blocks_from_tool_data``, no aquí).
    """

    nodo: Literal["video"] = "video"
    artifact: ArtifactRef
    alt: str = Field(default="", max_length=1000)
    aspecto: Literal["cuadrada", "panoramica", "libre"] = "libre"
    esquinas: Literal["ninguna", "s", "m"] = "ninguna"


class BotonNode(BaseModel):
    """Convierte en dato lo que hoy está cableado en Swift (los botones de
    `SocialDraftCardView` elegidos por `target.isPersonal`). `accion` es UNA
    `ChatAction` del allowlist discriminado -- incluye `UnsupportedAction`
    como caso tolerante, así que una `accion` de una versión futura no
    tumba el botón ni el nodo que lo contiene, solo lo deja sin pintar.
    """

    nodo: Literal["boton"] = "boton"
    estilo: Literal["primario", "secundario", "discreto", "destructivo"] = "primario"
    accion: ChatAction


class BadgeNode(BaseModel):
    """El badge demo/live verde/naranja/gris que hoy está fijo en Swift,
    como dato: `tono` mapea a los colores de `FuenteBadge`.
    """

    nodo: Literal["badge"] = "badge"
    contenido: str = Field(min_length=1, max_length=80)
    tono: Literal["neutro", "acento", "advertencia", "exito"] = "neutro"


class DivisorNode(BaseModel):
    """Separador visual. Sin campos: no hay nada semántico que decidir."""

    nodo: Literal["divisor"] = "divisor"


class NodoDesconocido(BaseModel):
    """Nodo de una versión futura del contrato (nivel 1 de forward-compat).

    Es el caso TOLERANTE por nodo (no por bloque): un stack con 5 hijos
    donde 1 trae un `nodo` que este backend no reconoce sigue pintando los
    otros 4 -- no se descarta la card entera. `extra="allow"` porque no
    sabemos qué forma tiene un nodo que no existe todavía; no lo validamos,
    no lo interpretamos, el renderer simplemente lo salta.
    """

    model_config = ConfigDict(extra="allow")

    nodo: str


_NODOS_CONOCIDOS = {"stack", "texto", "imagen", "video", "boton", "badge", "divisor"}


def _nodo_discriminador(valor: Any) -> str:
    """Tag de routing de `NodoCard`: un `nodo` fuera del allowlist cae en
    `NodoDesconocido` en vez de invalidar el stack que lo contiene.
    """

    if isinstance(valor, dict):
        nodo = valor.get("nodo")
    else:
        nodo = getattr(valor, "nodo", None)
    return nodo if nodo in _NODOS_CONOCIDOS else "desconocido"


NodoCard = Annotated[
    Annotated[StackNode, Tag("stack")]
    | Annotated[TextoNode, Tag("texto")]
    | Annotated[ImagenNode, Tag("imagen")]
    | Annotated[VideoNode, Tag("video")]
    | Annotated[BotonNode, Tag("boton")]
    | Annotated[BadgeNode, Tag("badge")]
    | Annotated[DivisorNode, Tag("divisor")]
    | Annotated[NodoDesconocido, Tag("desconocido")],
    Discriminator(_nodo_discriminador),
]
"""Una de las 7 primitivas, o `NodoDesconocido` como caso tolerante. `hijos`
de `StackNode` referencia este alias -- recursivo a propósito, se resuelve
con `StackNode.model_rebuild()` justo debajo."""

# `StackNode.hijos` quedó con una referencia adelantada a `NodoCard` (que
# todavía no existía cuando se definió la clase). Pydantic detecta el modelo
# como incompleto y lo reintenta solo la próxima vez que hace falta, pero se
# fuerza aquí para que un error de definición truene en el import y no en la
# primera card real que llegue en producción.
StackNode.model_rebuild()


class GenericCardBlock(ChatBlockBase):
    """MVP de Server-Driven UI del chat: un árbol de hasta `MAX_PROFUNDIDAD_CARD`
    niveles y `MAX_NODOS_CARD` nodos, compuesto con las 6 primitivas de
    arriba.

    `fallback_text` es OBLIGATORIO aquí (a diferencia de `ChatBlockBase`,
    donde es opcional): es la red final de un cliente que todavía no tiene
    el renderer genérico (nivel 3 de forward-compat del plan) -- toda card
    emitida DEBE poder degradarse a una frase útil.
    """

    type: Literal["card"] = "card"
    # Corto, no un UUID: pensado para telemetría/logs legibles ("card
    # linkedin-draft-1"), no como identidad fuerte -- esa la da el
    # `tool_call_id` del evento que la contiene.
    card_id: str = Field(min_length=1, max_length=120)
    raiz: StackNode
    fallback_text: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_limites(self) -> GenericCardBlock:
        """Cuenta nodos, profundidad y botones del árbol COMPLETO.

        Recorrido manual (no un helper genérico) a propósito: son 3
        invariantes del mismo árbol y calcularlas en una sola pasada evita
        recorrerlo 3 veces por cada card que llega.
        """

        total_nodos = 0
        total_botones = 0

        def recorrer(nodo: object, profundidad: int) -> None:
            nonlocal total_nodos, total_botones
            total_nodos += 1
            if total_nodos > MAX_NODOS_CARD:
                raise ValueError(
                    f"una card no puede tener más de {MAX_NODOS_CARD} nodos en total"
                )
            if profundidad > MAX_PROFUNDIDAD_CARD:
                raise ValueError(
                    f"una card no puede anidar stacks más de {MAX_PROFUNDIDAD_CARD} niveles"
                )
            if isinstance(nodo, BotonNode):
                total_botones += 1
                if total_botones > MAX_BOTONES_CARD:
                    raise ValueError(f"una card no puede tener más de {MAX_BOTONES_CARD} botones")
            if isinstance(nodo, StackNode):
                for hijo in nodo.hijos:
                    recorrer(hijo, profundidad + 1)

        recorrer(self.raiz, 1)
        return self


class Citation(BaseModel):
    """Una fuente citable usada en una respuesta (§18, §100).

    Permite que herramientas como ``buscar_web`` devuelvan fuentes
    estructuradas que el cliente puede mostrar como chips tocables.
    """

    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=1, max_length=2048)
    source: str = Field(default="", max_length=200)
    author: str | None = Field(default=None, max_length=200)
    date: str | None = Field(default=None, max_length=40)
    excerpt: str | None = Field(default=None, max_length=1000)
    retrieved_at: str | None = Field(default=None, max_length=40)


class ChartSeries(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    value: float


class ChartBlock(ChatBlockBase):
    type: Literal["chart"] = "chart"
    title: str = Field(min_length=1, max_length=200)
    series: list[ChartSeries] = Field(min_length=1, max_length=24)


class SourcesBlock(ChatBlockBase):
    """Bloque de fuentes/citations para mostrar debajo de respuestas (§18, §90).

    El cliente muestra ``"Fuentes · N"`` y al tocar expande la lista.
    Cada fuente es tocable y abre el navegador in-app.
    """

    type: Literal["sources"] = "sources"
    citations: list[Citation] = Field(min_length=1, max_length=20)


ChatBlock = Annotated[
    MediaBlock
    | LinkPreviewBlock
    | FlightCardBlock
    | HotelCardBlock
    | SocialDraftBlock
    | QuestionBlock
    | GenericCardBlock
    | SourcesBlock
    | ChartBlock,
    Field(discriminator="type"),
]
"""Bloque rico allowlisted que web, iOS y Android renderizan con el mismo contrato."""

ChatBlockAdapter: TypeAdapter[ChatBlock] = TypeAdapter(ChatBlock)


class ToolEndEvent(BaseModel):
    type: Literal["tool_end"] = "tool_end"
    tool_call_id: str | None = Field(default=None, min_length=1, max_length=255)
    name: str
    result_preview: str
    artifacts: list[ArtifactRef] = Field(default_factory=list, max_length=20)
    blocks_version: Literal[1] = 1
    blocks: list[ChatBlock] = Field(default_factory=list, max_length=30)
    citations: list[Citation] = Field(default_factory=list, max_length=20)
    mission_id: UUID | None = None
    """Misión asíncrona creada por la herramienta, si existe.

    Permite que el mismo chat siga mostrando un trabajo delegado después de
    cerrar el stream HTTP, sin exponer datos internos de ``ToolResult.data``.
    """
    fidelidad: Literal["demo", "live"] | None = None
    """Si el resultado de esta herramienta salió de un proveedor SIMULADO
    (``"demo"``) o REAL (``"live"``) — ver ``edecan_core.veracidad``.
    ``None`` cuando la tool no depende de un ``ProveedorDeclarado`` externo
    (la mayoría de las 112 herramientas del agente). Viaja por el mismo
    stream SSE que el resto del turno: a diferencia de un ``logging.warning``
    (que en la Mac del dueño muere solo con el sidecar), esto SÍ llega a la
    app.
    """
    motivo_simulado: str | None = Field(default=None, max_length=500)
    """Qué falta para que el resultado sea real, solo cuando
    ``fidelidad == "demo"`` (p. ej. ``"falta ELEVENLABS_API_KEY"``) — texto
    accionable, nunca solo "es un stub".
    """


class PendingToolCall(BaseModel):
    """Tool call original de un lote suspendido por confirmación humana."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class PendingChatMessage(BaseModel):
    """Mensaje LLM serializable que permite continuar sin relanzar el prompt."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]]


class PendingAgentTurn(BaseModel):
    """Foto serializable de un turno suspendido antes de ejecutar un lote.

    Se guarda temporalmente en Redis. Incluye todo lo necesario para ejecutar
    los ``tool_call`` originales una sola vez y continuar el mismo loop LLM,
    sin volver a enviar la orden del usuario como si fuera un turno nuevo.
    """

    version: Literal[1] = 1
    messages: list[PendingChatMessage]
    tool_calls: list[PendingToolCall] = Field(min_length=1)
    operational_tool_names: list[str]
    usage: dict[str, int] = Field(default_factory=dict)
    iteration: int = Field(ge=0)
    accumulated_text: str = ""
    tool_log: list[dict[str, Any]] = Field(default_factory=list)
    system_prompt: str | None = None
    approved_tool_call_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_call_identity(self) -> PendingAgentTurn:
        call_ids = [call.id for call in self.tool_calls]
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("Los tool_call_id de un lote pendiente deben ser únicos.")
        if len(self.operational_tool_names) != len(set(self.operational_tool_names)):
            raise ValueError("Los nombres operativos de un turno deben ser únicos.")
        if not {call.name for call in self.tool_calls}.issubset(set(self.operational_tool_names)):
            raise ValueError("Cada tool del lote debe haber sido ofrecida en el turno original.")
        if len(self.approved_tool_call_ids) != len(set(self.approved_tool_call_ids)):
            raise ValueError("Una aprobación pendiente no puede estar duplicada.")
        if not set(self.approved_tool_call_ids).issubset(set(call_ids)):
            raise ValueError("Una aprobación pendiente debe pertenecer al lote original.")
        return self


class ConfirmationRequiredEvent(BaseModel):
    type: Literal["confirmation_required"] = "confirmation_required"
    tool_call_id: str
    name: str
    args: dict
    pending_turn: PendingAgentTurn | None = None


class DoneEvent(BaseModel):
    type: Literal["done"] = "done"
    usage: dict[str, int] = Field(default_factory=dict)
    attribution: dict[str, str] = Field(default_factory=dict)
    # Explicación pública y redactada de la ejecución; nunca contiene el
    # razonamiento privado del modelo.
    explanation: str | None = None


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    message: str


AgentEvent = Annotated[
    TextDeltaEvent
    | ToolStartEvent
    | ToolProgressEvent
    | ToolEndEvent
    | ConfirmationRequiredEvent
    | DoneEvent
    | ErrorEvent,
    Field(discriminator="type"),
]
"""Unión discriminada por `type` del contrato público de ejecución."""

AgentEventAdapter: TypeAdapter[AgentEvent] = TypeAdapter(AgentEvent)
"""`TypeAdapter` listo para validar/parsear un `dict` (p. ej. JSON de un evento SSE)
hacia la variante concreta de `AgentEvent` que corresponda."""
