"""Destinos de publicación configurables por tenant (perfil personal + N páginas de organización).

Antes de este módulo, el motor de contenido social solo conocía dos destinos de LinkedIn
fijos en el código: `Literal["personal", "acme"]` (esquemas de API, modelos Swift/Kotlin,
prompts de redacción). Eso obligaba a CUALQUIER usuario de este repo OSS a tener -- o a
convivir con el nombre de -- una marca ajena llamada "Acme". Este módulo reemplaza ese
literal fijo por un modelo abierto: cada tenant define sus propios destinos de publicación
en su propia configuración (nunca en este repo) -- un perfil personal (siempre presente, es
el comportamiento por defecto) y cero o más páginas de organización, cada una con su propio
id, nombre visible, tipo de actor (persona vs. organización), voz/reglas editoriales propias,
y si el cliente puede ofrecerle un botón de publicación directa además de copiar/guardar.

`personal` sigue funcionando exactamente igual que antes (mismo id, mismo comportamiento por
defecto: sin restricción de tema, sin publicación directa). Un destino de organización que el
usuario configure ocupa el lugar que antes tenía el literal `"acme"`.

Este módulo es deliberadamente puro: no importa FastAPI, SQLAlchemy ni ningún proveedor
concreto, y no sabe de dónde viene la configuración de un tenant (fila de BD, archivo local
ignorado por git, variable de entorno). Quien lo use decide cómo cargar la lista de destinos
y se la pasa a `brand_destination_config_from_list` / `BrandDestinationConfig`.

No reemplaza (todavía) los sitios que hoy tienen el literal hardcodeado -- ver el inventario
entregado junto con esta tarea para la lista exacta archivo:línea que falta migrar.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

# El id reservado del destino implícito: SIEMPRE existe, incluso si el tenant no configuró
# nada. Reemplaza el antiguo literal fijo `"personal"` -- sigue siendo la cadena `"personal"`
# a propósito, para no romper configuración/perfiles ya guardados con esa clave.
PERSONAL_DESTINATION_ID = "personal"

# Igual que un `platform_key` de `edecan_creative.social`: minúsculas, dígitos, `_`/`-`,
# tiene que empezar con letra. Evita que un id raro rompa las claves de storage derivadas
# (`f"{platform}_{destination_id}"`) o algún path/URL futuro.
_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,59}$")

# Forma pública del patrón anterior: para que una capa de entrada (JSON schema de una
# tool, `Field(pattern=...)` de un modelo Pydantic) valide el FORMATO de un id de destino
# antes de intentar resolverlo contra la config de un tenant, sin duplicar la definición
# exacta del patrón en cada sitio que hoy tiene el literal hardcodeado (ver el inventario
# de la tarea que reemplaza `Literal["personal", "acme"]`).
DESTINATION_ID_PATTERN = _ID_RE.pattern
_MAX_LABEL_CHARS = 200
_MAX_TONE_CHARS = 300
_MAX_TOPIC_FOCUS_CHARS = 500
_MAX_MENTION_RULES_CHARS = 2000
_MAX_CONTEXT_BANK_KEY_CHARS = 120
_MAX_ACCOUNT_HINTS = 10
_MAX_ACCOUNT_HINT_CHARS = 120

BrandActor = Literal["person", "organization"]


def _cap(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


@dataclass(frozen=True, slots=True)
class BrandVoice:
    """Reglas editoriales propias de un destino: cómo debe sonar y de qué puede/no puede hablar.

    Reemplaza el bloque de prompt hardcodeado que antes distinguía "perfil personal" de
    "Acme" a mano (tono fijo, tema fijo a "finanzas", lista fija de marcas a
    evitar). Con campos vacíos (los defaults), un destino no impone ninguna restricción --
    ese es el caso del perfil personal genérico.
    """

    tone: str = ""
    # Ej. de un destino de organización con nicho fijo: "crédito y finanzas personales en
    # Latinoamérica". Vacío = sin restricción de tema (el caso normal de "persona").
    topic_focus: str = ""
    # Instrucción libre que el usuario puede escribir para su propio destino (ej. "nunca
    # prometas cifras de rendimiento", "siempre cierra con la URL oficial al final"). Se
    # inyecta tal cual en el prompt -- ver `voice_prompt_block`.
    mention_rules: str = ""
    # Referencia opcional a un banco de contexto propio de este destino (identidad, hechos
    # verificables -- ver `edecan_creative.social.build_context_bank_prompt_block`). El
    # contenido real vive por-tenant, nunca aquí; este campo solo guarda la clave/puntero.
    context_bank_key: str | None = None


@dataclass(frozen=True, slots=True)
class BrandDestination:
    """Un destino de publicación configurado por el usuario: un perfil o una página.

    Generaliza lo que antes eran los dos únicos valores posibles de
    `Literal["personal", "acme"]`. `id` es el identificador estable que viaja por la API
    (donde antes iba el literal `"personal"`/`"acme"`); `actor` decide si se publica como
    persona o como organización; `offer_direct_publish_action` decide si el cliente puede
    ofrecer un botón de "Aprobar y publicar" además de copiar/guardar (el perfil personal por
    defecto NO lo ofrece, igual que hoy; una página de organización configurada por el usuario
    puede activarlo). Publicar de verdad SIEMPRE exige una confirmación puntual del usuario en
    la capa de API (`confirmed=True`) sin importar este flag -- ese invariante de seguridad no
    vive en este módulo ni es configurable por destino.
    """

    id: str
    label: str = ""
    actor: BrandActor = "person"
    voice: BrandVoice = field(default_factory=BrandVoice)
    offer_direct_publish_action: bool = False
    # Palabras que ayudan a emparejar este destino con una cuenta conectada del conector
    # social (ej. el nombre de la organización, un alias). Reemplaza los ids de marca
    # concretos que antes venían fijos a mano para decidir el emparejamiento.
    account_hints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        normalized_id = str(self.id or "").strip().lower()
        if not _ID_RE.match(normalized_id):
            raise ValueError(
                f"Id de destino inválido: {self.id!r}. Debe empezar con una letra y usar solo "
                "minúsculas, dígitos, '_' o '-' (máximo 60 caracteres)."
            )
        object.__setattr__(self, "id", normalized_id)
        if not self.label:
            object.__setattr__(self, "label", normalized_id)

    def matches_connected_account(self, account: Mapping[str, Any]) -> bool:
        """Decide si una cuenta conectada del conector social corresponde a este destino.

        Reemplaza `_linkedin_account_matches_target` (antes con ids de marca concretos fijos
        a mano en `content_studio.py`). El emparejamiento por `account_hints` viene primero
        (configurado por el usuario, específico de su destino); si no hay match, cae a una
        heurística genérica por tipo de actor.
        """

        haystack = " ".join(
            str(account.get(key) or "") for key in ("display_name", "external_account_id", "meta")
        ).lower()
        if any(hint and hint.lower() in haystack for hint in self.account_hints):
            return True
        if self.actor == "organization":
            return "organization" in haystack or "organización" in haystack
        return "person" in haystack or "personal" in haystack


def is_valid_destination_id(value: Any) -> bool:
    """True si `value` tiene la forma válida de un id de destino (ver `BrandDestination.id`).

    Deja pasar `personal` y cualquier id de organización con la forma correcta; no
    consulta la config de ningún tenant -- solo valida el FORMATO, igual que hace
    `BrandDestination.__post_init__`. Pensado para capas de entrada (`input_schema` de una
    tool, un campo Pydantic) que hoy rechazan cualquier valor fuera de
    `Literal["personal", "acme"]` y necesitan aceptar un id de organización arbitrario
    configurado por el tenant sin abrir la puerta a basura.
    """

    return bool(_ID_RE.match(str(value or "").strip().lower()))


def default_personal_destination() -> BrandDestination:
    """El destino implícito de siempre: perfil personal, sin tema fijo, sin publicación
    directa -- el comportamiento exacto que ya tenía el literal `"personal"` antes de que
    existieran destinos configurables. Sirve de fallback cuando el tenant no configuró nada."""

    return BrandDestination(id=PERSONAL_DESTINATION_ID, label="Perfil personal")


@dataclass(frozen=True, slots=True)
class BrandDestinationConfig:
    """Configuración completa de destinos de un tenant.

    Siempre incluye `personal` (sintetizado con valores por defecto si el tenant no lo
    definió explícitamente) más los destinos de organización que el usuario haya configurado.
    Construir esto a mano rara vez hace falta -- normalmente se arma con
    `brand_destination_config_from_list` a partir de la config cruda del tenant.
    """

    destinations: tuple[BrandDestination, ...] = field(
        default_factory=lambda: (default_personal_destination(),)
    )

    def resolve(self, destination_id: str | None) -> BrandDestination | None:
        """Busca un destino por id, normalizando mayúsculas/espacios.

        `None` o un id que no existe en la config del tenant devuelven `None` -- igual que hoy
        hace `CrearContenidoSocialTool.run` cuando `destino` no es uno de los valores
        conocidos: el llamador trata `None` como "sin destino conocido" y limita el cliente a
        acciones de solo lectura (nunca ofrece publicar).
        """

        if not destination_id:
            return None
        key = destination_id.strip().lower()
        return next((d for d in self.destinations if d.id == key), None)

    def default(self) -> BrandDestination:
        """El destino por defecto: `personal`, siempre disponible."""

        return self.resolve(PERSONAL_DESTINATION_ID) or default_personal_destination()

    def resolve_or_default(self, destination_id: str | None) -> BrandDestination:
        """Como `resolve`, pero nunca devuelve `None`: cae a `personal` si no hay match."""

        return self.resolve(destination_id) or self.default()

    def organizations(self) -> tuple[BrandDestination, ...]:
        """Los destinos de organización configurados por el usuario (excluye `personal`)."""

        return tuple(d for d in self.destinations if d.actor == "organization")

    def others(self, destination_id: str) -> tuple[BrandDestination, ...]:
        """El resto de destinos del tenant, para armar guardarraíles de "no mezclar marcas"
        (ver `voice_prompt_block`) sin tener que enumerar nombres de empresas a mano."""

        key = destination_id.strip().lower()
        return tuple(d for d in self.destinations if d.id != key)

    def ids(self) -> tuple[str, ...]:
        return tuple(d.id for d in self.destinations)


def brand_destination_from_dict(data: Mapping[str, Any]) -> BrandDestination:
    """Arma un `BrandDestination` a partir de un dict plano (fila de BD, JSON de config de
    tenant, archivo local de una instalación self-host). Nunca lee datos reales de una marca
    concreta -- eso vive siempre en la config del usuario, no en este repo.

    Trunca todos los campos de texto a límites defensivos (mismo espíritu que
    `edecan_creative.social._cap_str`): la config de un destino es dato de tenant, no se
    confía en que su tamaño venga acotado por quien la escribió.
    """

    voice_raw = data.get("voice")
    voice_mapping: Mapping[str, Any] = voice_raw if isinstance(voice_raw, Mapping) else {}
    context_bank_key = voice_mapping.get("context_bank_key")
    hints_raw = data.get("account_hints")
    account_hints = tuple(
        _cap(hint, _MAX_ACCOUNT_HINT_CHARS)
        for hint in (hints_raw if isinstance(hints_raw, Sequence) else ())
        if str(hint or "").strip()
    )[:_MAX_ACCOUNT_HINTS]

    return BrandDestination(
        id=str(data.get("id") or ""),
        label=_cap(data.get("label"), _MAX_LABEL_CHARS),
        actor="organization"
        if str(data.get("actor") or "person").strip().lower() == "organization"
        else "person",
        voice=BrandVoice(
            tone=_cap(voice_mapping.get("tone"), _MAX_TONE_CHARS),
            topic_focus=_cap(voice_mapping.get("topic_focus"), _MAX_TOPIC_FOCUS_CHARS),
            mention_rules=_cap(voice_mapping.get("mention_rules"), _MAX_MENTION_RULES_CHARS),
            context_bank_key=(
                _cap(context_bank_key, _MAX_CONTEXT_BANK_KEY_CHARS) if context_bank_key else None
            ),
        ),
        offer_direct_publish_action=bool(data.get("offer_direct_publish_action", False)),
        account_hints=account_hints,
    )


def brand_destination_config_from_list(
    raw: Sequence[Mapping[str, Any]] | None,
) -> BrandDestinationConfig:
    """Arma la config completa de destinos de un tenant a partir de la lista que guardó (o no)
    en su propia configuración.

    Defensivo por diseño: una entrada malformada (id vacío, id repetido, tipo inválido) se
    descarta en vez de tumbar la resolución de destinos de todo el tenant -- mismo criterio
    que ya usa `edecan_creative.social.get_editorial_profile` frente a storage inesperado. Si
    el tenant no definió `personal` explícitamente, se sintetiza uno con valores por defecto
    para que siempre exista (compatibilidad con el comportamiento anterior a los destinos
    configurables).
    """

    destinations: list[BrandDestination] = []
    seen_ids: set[str] = set()
    for item in raw or ():
        if not isinstance(item, Mapping):
            continue
        try:
            destination = brand_destination_from_dict(item)
        except ValueError:
            continue
        if destination.id in seen_ids:
            continue  # el primero gana; ids duplicados no rompen la resolución
        seen_ids.add(destination.id)
        destinations.append(destination)
    if PERSONAL_DESTINATION_ID not in seen_ids:
        destinations.insert(0, default_personal_destination())
    return BrandDestinationConfig(destinations=tuple(destinations))


def editorial_profile_key(platform: str, destination_id: str | None) -> str:
    """Clave de storage del perfil editorial para (plataforma, destino).

    Reemplaza `_LINKEDIN_TARGETS` + `_editorial_profile_key` de
    `apps/api/edecan_api/routers/content_studio.py` (antes fijos a `"personal"`/`"acme"`).
    Mismos valores exactos que ya están guardados hoy, para no romper perfiles existentes:

    - `editorial_profile_key("linkedin", None) == "linkedin"` (clave legacy pre-destinos,
      usada también como fallback de migración -- ver el `legacy` que arma
      `_linkedin_editorial_profile`).
    - `editorial_profile_key("linkedin", "personal") == "linkedin_personal"` (idéntico a hoy).
    - `editorial_profile_key("linkedin", "acme_corp") == "linkedin_acme_corp"` (generaliza lo
      que antes era el literal fijo `"linkedin_acme"`, ahora con el id que el usuario le
      puso a su propio destino de organización).
    """

    if not destination_id:
        return platform
    return f"{platform}_{destination_id.strip().lower()}"


def voice_prompt_block(
    destination: BrandDestination,
    *,
    other_destinations: Sequence[BrandDestination] = (),
) -> str:
    """Bloque de instrucciones de voz para inyectar en el prompt de redacción.

    Reemplaza `_linkedin_target_context` (voz fija "personal" vs. una marca de organización
    concreta) y la lista fija de marcas a evitar que vivía en `_SYSTEM_PROMPT` ("NO menciones
    [marca A], [marca B], [marca C]..."). El guardarraíl de "no mezclar destinos" ahora se
    arma solo a partir de `other_destinations` -- funciona igual para un tenant con cero,
    una o veinte marcas, sin que ningún nombre propio quede hardcodeado en este repo.
    """

    lines = [f"Destino: {destination.label}."]
    if destination.actor == "organization":
        lines.append(
            f"Este destino publica como ORGANIZACIÓN ({destination.label}), no como una "
            "persona. Puedes mencionarla porque el destino ES esta organización."
        )
    else:
        lines.append(
            "Este destino publica como PERSONA. Escribe con criterio propio, no como una "
            "marca ni como una nota de prensa."
        )
    if destination.voice.topic_focus:
        lines.append(f"Enfoque de tema fijo para este destino: {destination.voice.topic_focus}.")
    if destination.voice.tone:
        lines.append(f"Tono: {destination.voice.tone}.")
    if destination.voice.mention_rules:
        lines.append(destination.voice.mention_rules)
    other_labels = [d.label for d in other_destinations if d.id != destination.id]
    if other_labels:
        lines.append(
            "No mezcles destinos: prohibido mencionar "
            + ", ".join(other_labels)
            + " salvo que la idea lo pida literalmente."
        )
    return "\n".join(lines)


def visual_guardrail_line(destination: BrandDestination) -> str:
    """Línea corta para el prompt de generación de imagen.

    Reemplaza `target_rules` dentro de `_content_studio_fydesign_args`
    (`apps/api/edecan_api/routers/content_studio.py`), hoy fijo a "LinkedIn Acme" /
    "LinkedIn personal".
    """

    if destination.actor == "organization":
        extra = f" {destination.voice.topic_focus}." if destination.voice.topic_focus else ""
        return (
            f"Destino visual: {destination.label} (organización).{extra} Puede usar identidad "
            f"de marca del destino porque el destino ES {destination.label}."
        )
    return (
        f"Destino visual: {destination.label} (persona). No debe parecer anuncio corporativo "
        "ni pieza de marca."
    )


__all__ = [
    "DESTINATION_ID_PATTERN",
    "PERSONAL_DESTINATION_ID",
    "BrandActor",
    "BrandDestination",
    "BrandDestinationConfig",
    "BrandVoice",
    "brand_destination_config_from_list",
    "brand_destination_from_dict",
    "default_personal_destination",
    "editorial_profile_key",
    "is_valid_destination_id",
    "visual_guardrail_line",
    "voice_prompt_block",
]
