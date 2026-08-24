"""Creación de paquetes sociales listos para revisar y publicar.

La herramienta no publica ni suplanta al usuario. Produce artefactos privados
(copy Markdown, manifiesto JSON y visual PNG opcional) que cruzan el mismo
contrato SSE de archivos que ya consumen web, iOS y Android.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import textwrap
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from edecan_core import (
    BadgeCard,
    BotonCard,
    Tool,
    ToolContext,
    ToolResult,
    construir_card_generica,
)
from edecan_llm.base import ChatMessage, CompletionRequest
from edecan_schemas import (
    ArtifactRef,
    CopyTextAction,
    GenericCardBlock,
    QuestionBlock,
    SaveArtifactAction,
)
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import text

from . import agenda
from ._files import Uploader, subir_archivo
from .auditoria import (
    LlamarModelo,
    auditar_hechos,
    reescribir_sin_primera_persona,
    revisar_calidad,
)
from .editorial import (
    REGLAS_LINKEDIN,
    firma_estructura,
    normalizar,
    normalizar_visual,
    revisar,
)
from .imagen_editorial import componer_titular, componer_titular_organization, generar_con_critica
from .investigacion import titulares_frescos
from .marcas import (
    DESTINATION_ID_PATTERN,
    PERSONAL_DESTINATION_ID,
    editorial_profile_key,
    is_valid_destination_id,
)
from .providers import ImageProvider, StubImageProvider, get_tenant_image_provider
from .tools import _cap_str, _slug

logger = logging.getLogger(__name__)


def _visual_publicable(
    visual: dict | None, *, tema: str = "", titular: str = ""
) -> dict[str, str] | None:
    """El copy visual listo para montar, ya REPARADO: ``None`` sólo si no hay `visual`.

    Dos comportamientos distintos, y la diferencia importa:

    - `visual is None` -> ``None``. Es el camino de `crear_contenido_social`: el modelo del
      chat pasa el copy pero no rellena estos campos, así que no se le monta encima nada que
      no pidió y la foto se entrega tal cual, como siempre.
    - `visual` es un diccionario, aunque venga incompleto o vacío -> se REPARA con
      `editorial.normalizar_visual`, degradando kicker y headline a `titular`/`tema`.

    Ese segundo caso es el que cambió, y era el octavo fallo esperando. Antes, un `visual`
    sin `headline` devolvía ``None`` y la persona recibía la foto pelada: sin kicker, sin
    titular y sin subtítulo, sin que nada explicara por qué. Con un escritor al que se le
    pide un JSON con un objeto ANIDADO de cuatro campos, omitirlo o dejarlo vacío es
    cuestión de tiempo. REFERENCIA no puede quedar así jamás: `_normalizar_visual`
    (`linkedin_content.py:274`) SIEMPRE rellena kicker/headline degradando al tema, y
    `_componer_pieza` vuelve a defenderse encima con `visual.get('kicker') or tema.upper()`.
    El port correcto es esa DEGRADACIÓN, nunca un rechazo.
    """
    if not isinstance(visual, dict):
        return None
    return normalizar_visual(visual, tema=tema, titular=titular)


_EDITORIAL_FIELDS = (
    "purpose",
    "audience",
    "voice",
    "content_pillars",
    "preferred_formats",
    "visual_identity",
    "image_rules",
    "calls_to_action",
    "avoid",
    "notes",
    "context_bank",
    # Mecanismo de cierre y largo, genérico y por-tenant (nunca una marca fija en este
    # repo -- ver `redaccion._con_cierre_garantizado` / `_CAMPOS_PERFIL_EN_PROMPT`):
    # - `closing_url`: si el destino lo configura, se APENDIZA solo, después de todos los
    #   gates, en vez de depender de que el modelo se acuerde de escribirlo. Puerto del
    #   cierre determinista de REFERENCIA (`organization_linkedin_content.py`: "la URL se agrega
    #   DESPUÉS de todos los filtros... queda garantizada aunque el modelo la olvide").
    # - `target_length`: anula el rango de largo por defecto del sistema (170-230
    #   palabras) cuando la cuenta necesita otro (p. ej. una página de marca que publica
    #   piezas cortas de 55 a 140 palabras en vez de ensayos de opinión).
    "closing_url",
    "target_length",
    # Modo del auditor de hechos (`auditoria.auditar_hechos`) para ESTA cuenta -- ver
    # `perfil_autoriza_escenas_ilustrativas` más abajo para el único valor que hace algo y la
    # razón de que sea un campo dedicado y no una lectura de `purpose`/`notes`.
    "fact_check_mode",
    # Postura editorial de ESTA cuenta (ver `perfil_autoriza_stance_polemica` más abajo
    # para el único valor que hace algo). Espejo de `fact_check_mode` en su forma: un
    # interruptor por-tenant que cambia cómo escribe el editor jefe, no una preferencia de
    # contenido. Fuera del chat por la MISMA razón que `fact_check_mode`: el chat escribe
    # en la clave pelada de la plataforma (`"linkedin"`), nunca en la del destino, así que
    # un "activa postura polémica" dicho en el chat lo encendería en el perfil PERSONAL en
    # vez del de la página -- un interruptor que cambia la voz, encendido en la cuenta
    # equivocada y en silencio.
    "editorial_stance",
    # Foco geográfico de las búsquedas de noticias de ESTA cuenta (texto libre, p. ej. el
    # país de una marca de un solo mercado). Lo consume `redaccion._foco_del_perfil`:
    # ancla la CONSULTA de titulares, jamás una región cableada en este repo. Sin él, una
    # cuenta con pilares propios busca con foco neutro. En el allowlist para que el dueño
    # pueda configurarlo por chat (`configurar_perfil_social`), no solo por sembrado.
    "search_focus",
)

# Campos que se GUARDAN como cualquier otro pero que `configurar_perfil_social` (el chat) no
# ofrece ni acepta. Motivo concreto, no purismo: esa herramienta escribe siempre en la clave
# pelada de la plataforma (`"linkedin"`), nunca en la del destino (`"linkedin_<org>"`), así
# que un "activa el modo de escenas ilustrativas" dicho en el chat encendería el permiso en
# el perfil PERSONAL en vez del de la página de la organización -- un interruptor que afloja
# al auditor de hechos, encendido en la cuenta equivocada y en silencio. Las puertas
# correctas son las que sí saben de destinos: `PUT /v1/content/social/profile?target=...` y
# el sembrado por-tenant de la app local.
_CAMPOS_FUERA_DEL_CHAT = frozenset({"fact_check_mode", "editorial_stance"})

# Campos que NO cuentan para decidir si un perfil está "configurado". `fact_check_mode` es un
# interruptor de control, no contenido editorial: un perfil que sólo lo trajera seguiría sin
# voz, sin propósito y sin banco, y darlo por configurado le quitaría el fallback al perfil
# legacy que `get_editorial_profile_for_destination` usa justo para ese caso.
_CAMPOS_SIN_CONTENIDO_EDITORIAL = frozenset({"fact_check_mode", "editorial_stance"})

# La mayoría de campos editoriales son preferencias breves. `context_bank` es
# distinto por diseño: un banco de contexto real (identidad, hechos
# verificables, "cicatrices") que alimenta la escritura sin nunca volverse
# autobiografía -- ver `build_context_bank_prompt_block`. Necesita más espacio
# que un campo de preferencia normal.
_DEFAULT_FIELD_CHAR_LIMIT = 4000
_FIELD_CHAR_LIMITS: dict[str, int] = {
    "context_bank": 16_000,
    # Cortos a propósito: una URL y una frase de largo objetivo, no un párrafo.
    "closing_url": 300,
    "target_length": 300,
}

# Tope defensivo de lo que se inyecta en el prompt de escritura: un banco de
# contexto es dato de tenant, no confiamos en que su tamaño esté acotado por
# el cliente que lo escribió.
_CONTEXT_BANK_PROMPT_LIMIT = 12_000

_CONTEXT_BANK_HEADER_BASE = (
    "BANCO DE CONTEXTO REAL DEL TENANT (nunca se convierte en autobiografía):\n\n"
    "REGLA SAGRADA: cualquier hecho de vida u operación que aparezca en un post tiene que "
    "salir de este banco, tal cual está escrito. Prohibido añadir fechas, nombres, diálogos, "
    'lugares o cifras que el banco no liste, ni "mejorar" la historia.\n\n'
    "CÓMO SE USA: los datos verificables pueden alimentar explicaciones sobre el mundo, sin "
    'narrar de dónde salieron (ej.: "generar una imagen con IA cuesta ~$0.06", nunca "a mí '
    'me cuesta $0.06"). Los hechos personales, la identidad y las cicatrices sirven '
    "únicamente para que el sistema no contradiga la realidad; no son material publicable ni "
    "siquiera cuando el tema se parece. Nunca los cuentes como experiencias en primera "
    'persona ni con "ya lo vi", "lo viví", "vengo de" ni variantes equivalentes.'
)

# Solo aplica al perfil PERSONAL (ver `build_context_bank_prompt_block`, parámetro
# `es_personal`). Existía como parte fija del header para CUALQUIER destino -- el defecto
# real que corrige este split: el banco de contexto de una página de ORGANIZACIÓN (el
# destino ES la marca, p. ej. la propia empresa cuyo perfil es este) necesita justo lo
# contrario -- nombrar sus propios productos y hechos verificados es el punto entero de
# tener un banco --, y esta frase se lo prohibía por accidente al no distinguir los dos
# casos. El perfil personal, en cambio, sí necesita este guardarraíl: sus "proyectos
# personales" no son el destino que se está publicando, y nombrarlos ahí sí sería una fuga
# de identidad que el dueño nunca pidió.
_CONTEXT_BANK_HEADER_SOLO_PERSONAL = (
    "\n\nNUNCA nombres al dueño ni a sus proyectos personales por su nombre real (ni en "
    "primera, ni en tercera persona, ni con posesivos: nada de 'La máquina de X', 'la "
    "empresa de X', 'según X'). El texto se sostiene por su argumento, no por atribuir "
    "identidad. Si sientes que necesitas nombrar a alguien, reescribe la idea como "
    "afirmación directa sobre el mundo."
)


def context_bank_text(profile: Mapping[str, Any]) -> str:
    """El banco de contexto crudo del tenant, sin las instrucciones de redacción.

    Existe porque el banco tiene DOS consumidores con necesidades opuestas:
    `build_context_bank_prompt_block` se lo entrega al escritor con la regla de uso
    pegada arriba, y el auditor de hechos necesita el material pelado para poder
    comprobar contra él (ver `redaccion._material_para_auditar`). Normalizar en un
    solo sitio evita que los dos se separen el día que cambie el shape del perfil.
    """
    raw = profile.get("context_bank")
    return raw.strip() if isinstance(raw, str) else ""


# El ÚNICO valor de `fact_check_mode` que activa algo. Deliberadamente un valor único y
# comparado por IGUALDAD EXACTA -- nunca una expresión regular sobre `purpose`/`notes` en
# prosa libre, que es justo el patrón frágil que ya tienen
# `redaccion._perfil_prohibe_numeros_extensos` y `redaccion._marca_para_una_sola_mencion`:
# cualquier forma de decir "lo mismo" que el regex
# no anticipó deja el modo apagado EN SILENCIO, y para un modo que afloja el auditor
# anti-invención de una cuenta real, apagado-en-silencio es aceptable (fail-closed) pero
# encendido-por-accidente no lo es nunca. Un campo dedicado con un único valor canónico no
# tiene ese hueco: o el tenant escribió EXACTAMENTE esto en `configurar_perfil_social`, o el
# modo está apagado.
_MODO_ESCENAS_ILUSTRATIVAS = "escenas_ilustrativas"


def perfil_autoriza_escenas_ilustrativas(profile: Mapping[str, Any]) -> bool:
    """¿Esta cuenta autorizó explícitamente que `auditoria.auditar_hechos` trate una escena
    anónima y genérica (sin fecha, sin cifra, sin nombre real, sin cita textual) como material
    ilustrativo legítimo -- típicamente tomada de su `context_bank` -- sin exigirle el mismo
    anclaje literal que a una cifra o una fecha?

    El caso real detrás de esto: un perfil puede declarar en su `purpose` que "cada post
    cuenta una escena venezolana concreta (el fiao, la quincena, el alquiler...)" -- una
    escena ilustrativa es exactamente el tipo de contenido para el que existe esa cuenta -- y
    aun así `auditar_hechos` la rechazaba como "desenlace inventado" o "cita no verificable"
    porque su instrucción por defecto es genérica y fail-closed: no distingue una escena
    ILUSTRATIVA que el perfil autorizó de una invención sobre un hecho fechable real.

    Señal DELIBERADAMENTE explícita y verificable, no una adivinanza: se activa si y solo si
    el campo `fact_check_mode` del perfil editorial trae EXACTAMENTE `"escenas_ilustrativas"`
    (ver `_MODO_ESCENAS_ILUSTRATIVAS`) -- nada de inferir la intención leyendo `purpose` con
    una expresión regular. Un perfil que no declaró este campo, que lo dejó vacío, o que puso
    cualquier otro valor -- la enorme mayoría, incluida cualquier cuenta de otro tenant --
    devuelve `False` y se comporta exactamente igual que hoy: fail-closed, sin excepciones.
    """
    return str(profile.get("fact_check_mode") or "").strip().lower() == _MODO_ESCENAS_ILUSTRATIVAS


# El ÚNICO valor de `editorial_stance` que activa algo. Misma filosofía que
# `_MODO_ESCENAS_ILUSTRATIVAS`: un valor único comparado por IGUALDAD EXACTA, nunca una
# adivinanza sobre `purpose`/`voice` en prosa libre. Para una postura que cambia la voz
# del editor jefe, apagado-en-silencio es aceptable (fail-closed) pero encendido-por-
# accidente no lo es nunca.
_MODO_POSTURA_POLEMICA = "polemica"


def perfil_autoriza_stance_polemica(profile: Mapping[str, Any]) -> bool:
    """¿Esta cuenta declaró que sus posts deben TOMAR UN LADO, no describir el paisaje?

    El caso real: el dueño del perfil personal pidió posts que CAUSEN POLÉMICA. Esa
    instrucción vivía sólo como prosa en `purpose`/`avoid`/`notes`, y el escritor la
    ignoraba: entregaba reseñas neutras porque es lo único que pasa los gates sin
    riesgo. Con este interruptor encendido, el editor jefe (`auditoria.pulir_borrador`)
    recibe una instrucción explícita de RECHAZAR (`publicable=false`) el borrador
    cuando es una descripción neutral sin juicio, y de devolverlo sólo cuando el
    `angulo` tome una posición que la mitad del gremio disputaría -- anclada en la
    fuente, nunca inventada (el auditor de hechos sigue intacto; esto no lo afloja).

    Señal DELIBERADAMENTE explícita: se activa si y solo si `editorial_stance` trae
    EXACTAMENTE `"polemica"`. Cualquier otro valor -- la enorme mayoría, incluida la
    página de Acme, que es escena/educativa por diseño -- devuelve `False` y se
    comporta exactamente igual que hoy.
    """
    return str(profile.get("editorial_stance") or "").strip().lower() == _MODO_POSTURA_POLEMICA


def build_context_bank_prompt_block(profile: dict[str, Any], *, es_personal: bool = True) -> str:
    """Arma el bloque de banco de contexto listo para inyectar en el prompt.

    El contenido real (identidad, empresas, cicatrices, etc.) vive por-tenant
    en `config.context_bank` -- nunca en este repo. Esta función solo aplica
    la regla de uso genérica (material de fondo, jamás autobiografía) que
    debe valer para cualquier tenant, no solo para uno en particular. Un
    perfil sin `context_bank` (o vacío) produce cadena vacía: no cambia el
    prompt en nada.

    `es_personal` (default `True`, preserva el comportamiento anterior para cualquier
    llamador que no lo pase) decide si se agrega el guardarraíl de "nunca nombres al dueño
    ni sus proyectos personales" -- ver `_CONTEXT_BANK_HEADER_SOLO_PERSONAL`. Pásalo en
    `False` para el banco de contexto de un destino de ORGANIZACIÓN (una página cuyo
    destino ES la marca): ahí el banco existe justamente para nombrar sus propios productos
    y hechos verificados, y la regla del perfil personal se lo impediría por accidente.
    """

    text_value = context_bank_text(profile)
    if not text_value:
        return ""
    if len(text_value) > _CONTEXT_BANK_PROMPT_LIMIT:
        text_value = text_value[:_CONTEXT_BANK_PROMPT_LIMIT].rstrip() + "\n[...banco truncado...]"
    header = _CONTEXT_BANK_HEADER_BASE + (_CONTEXT_BANK_HEADER_SOLO_PERSONAL if es_personal else "")
    return f"\n\n{header}\n\n{text_value}"


# SQL a nivel de módulo (y no incrustado en cada función) para que
# `tests/test_sql_prepara_en_postgres.py` pueda pedirle a un Postgres REAL que prepare cada
# sentencia. Los tests normales sustituyen la sesión por una falsa que nunca ejecuta SQL, así
# que un error de tipos solo aparecía en producción: fue exactamente lo que pasó con
# `jsonb_build_object(:key, ...)` -- al ser variádica sobre `"any"`, Postgres no puede inferir
# el tipo de un parámetro sin `CAST` y asyncpg (que prepara los statements) revienta con
# `ProgrammingError`, envenenando además el resto de la transacción del turno.
SQL_GET_EDITORIAL_PROFILE = """
    SELECT config, version
    FROM social_editorial_profiles
    WHERE tenant_id = :tenant_id AND user_id = :user_id AND platform = :platform
"""


async def get_editorial_profile(ctx: ToolContext, platform: str = "linkedin") -> dict[str, Any]:
    # Embedders y pruebas pueden sustituir el almacenamiento. Crear una pieza
    # debe degradar a un perfil vacío en vez de tumbar toda la experiencia.
    if ctx.session is None:
        return {"platform": platform, "configured": False, "version": 0}
    result = await ctx.session.execute(
        text(SQL_GET_EDITORIAL_PROFILE),
        {
            "tenant_id": str(ctx.tenant_id),
            "user_id": str(ctx.user_id),
            "platform": platform,
        },
    )
    row = result.mappings().first()
    if row is None:
        return {"platform": platform, "configured": False, "version": 0}
    raw = row.get("config")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    config = dict(raw) if isinstance(raw, dict) else {}
    return {
        "platform": platform,
        "configured": any(
            bool(config.get(field))
            for field in _EDITORIAL_FIELDS
            if field not in _CAMPOS_SIN_CONTENIDO_EDITORIAL
        ),
        "version": int(row.get("version") or 1),
        **config,
    }


async def get_editorial_profile_for_destination(
    ctx: ToolContext, platform: str, destination_id: str | None
) -> dict[str, Any]:
    """Perfil editorial de (plataforma, DESTINO), no de la plataforma pelada.

    Sin esto, `crear_contenido_social` cargaba siempre la clave legacy (`"linkedin"`) aunque
    el tenant ya tuviera `"linkedin_personal"` y `"linkedin_<organizacion>"` guardados por
    separado -- y como la clave legacy es la que quedó de antes de que existieran los
    destinos, suele traer la voz de UNA marca mezclada. Resultado: un post pedido sin marca
    salía con la voz de la organización. Ver `marcas.editorial_profile_key`.

    Sin destino explícito se asume `PERSONAL_DESTINATION_ID`: quien pide "un post de
    LinkedIn" a secas está pidiendo publicar como persona, no como organización.

    Cae al perfil legacy solo si el perfil del destino todavía no está configurado, para no
    dejar sin voz a un tenant que nunca migró sus datos.
    """
    key = editorial_profile_key(platform, destination_id or PERSONAL_DESTINATION_ID)
    profile = await get_editorial_profile(ctx, key)
    if profile.get("configured"):
        return profile
    legacy = await get_editorial_profile(ctx, platform)
    return legacy if legacy.get("configured") else profile


SQL_LISTAR_DESTINOS = """
    SELECT platform, config
    FROM social_editorial_profiles
    WHERE tenant_id = :tenant_id AND user_id = :user_id
      AND platform LIKE :prefijo
"""


async def destinos_configurados(ctx: ToolContext, platform: str) -> list[dict[str, str]]:
    """Destinos que el usuario REALMENTE tiene configurados para `platform`.

    No hay una tabla de registro de destinos (ver `_resolve_destination` en
    `apps/api/edecan_api/routers/content_studio.py`), pero sí uno de facto: configurar un
    destino es exactamente tener su fila de perfil editorial. Así que las opciones que se le
    ofrecen al usuario salen de sus propios datos y nunca de nombres inventados por el
    modelo -- que es la diferencia entre preguntar con criterio y preguntar por preguntar.

    Solo cuentan los perfiles con contenido de verdad (`configured`): una fila vacía no es un
    destino usable y ofrecerla sería ofrecer una opción que no escribe en ninguna voz.

    Devuelve `[]` sin `ctx.session` (embebedores/pruebas) para que el llamador degrade al
    comportamiento de siempre en vez de tumbarse.
    """
    if ctx.session is None:
        return []
    prefijo = f"{platform}_%"
    result = await ctx.session.execute(
        text(SQL_LISTAR_DESTINOS),
        {
            "tenant_id": str(ctx.tenant_id),
            "user_id": str(ctx.user_id),
            "prefijo": prefijo,
        },
    )
    destinos: list[dict[str, str]] = []
    for row in result.mappings().all():
        clave = str(row.get("platform") or "")
        destino_id = clave[len(platform) + 1 :]
        if not destino_id or not is_valid_destination_id(destino_id):
            continue
        raw = row.get("config")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = {}
        config = raw if isinstance(raw, dict) else {}
        if not any(bool(config.get(field)) for field in _EDITORIAL_FIELDS):
            continue
        etiqueta = str(config.get("label") or "").strip()
        destinos.append({"id": destino_id, "label": etiqueta or destino_id})
    # Orden estable: `personal` primero (es el default y el más usado), el resto alfabético.
    destinos.sort(key=lambda d: (d["id"] != PERSONAL_DESTINATION_ID, d["id"]))
    return destinos


SQL_SAVE_EDITORIAL_PROFILE = """
    INSERT INTO social_editorial_profiles (
        id, tenant_id, user_id, platform, config, version, created_at, updated_at
    ) VALUES (
        :id, :tenant_id, :user_id, :platform, CAST(:config AS jsonb),
        :version, now(), now()
    )
    ON CONFLICT (tenant_id, user_id, platform) DO UPDATE
    SET config = EXCLUDED.config,
        version = EXCLUDED.version,
        updated_at = now()
"""


async def save_editorial_profile(
    ctx: ToolContext, platform: str, patch: dict[str, Any]
) -> dict[str, Any]:
    if ctx.session is None:
        raise RuntimeError("Esta instalación no tiene almacenamiento de perfil disponible.")
    current = await get_editorial_profile(ctx, platform)
    # Arranca de TODO lo que ya había en el JSONB (menos los tres campos de solo-lectura que
    # `get_editorial_profile` agrega por su cuenta), no solo de `_EDITORIAL_FIELDS`. Así una
    # clave interna que otro código haya guardado en el mismo `config` -- p. ej.
    # `get_agenda_state`/`save_agenda_state` con el estado de rotación de `edecan_creative.agenda`,
    # que vive fuera de `_EDITORIAL_FIELDS` a propósito porque no es una preferencia editable
    # por chat -- sobrevive a un guardado normal de perfil en vez de desaparecer.
    _meta_keys = {"platform", "configured", "version"}
    config = {key: value for key, value in current.items() if key not in _meta_keys}
    for field in _EDITORIAL_FIELDS:
        config.setdefault(field, "")
    for field in _EDITORIAL_FIELDS:
        if field not in patch:
            continue
        value = patch[field]
        if isinstance(value, list):
            config[field] = [str(item).strip()[:240] for item in value[:20] if str(item).strip()]
        else:
            limit = _FIELD_CHAR_LIMITS.get(field, _DEFAULT_FIELD_CHAR_LIMIT)
            config[field] = str(value or "").strip()[:limit]
    version = int(current.get("version") or 0) + 1
    await ctx.session.execute(
        text(SQL_SAVE_EDITORIAL_PROFILE),
        {
            "id": str(uuid.uuid4()),
            "tenant_id": str(ctx.tenant_id),
            "user_id": str(ctx.user_id),
            "platform": platform,
            "config": json.dumps(config),
            "version": version,
        },
    )
    return {"platform": platform, "configured": True, "version": version, **config}


# ---------------------------------------------------------------------------
# Estado de rotación editorial (`edecan_creative.agenda`): territorio/formato/foco actuales
# más el historial reciente para el cooldown temático. Vive en el MISMO JSONB que el perfil
# editorial (`social_editorial_profiles.config`, ver `get_editorial_profile`/
# `save_editorial_profile` arriba), bajo una clave propia que esas dos funciones nunca tocan
# directamente -- es bookkeeping interno del motor de rotación, no una preferencia que el
# usuario edite por chat, así que no vive en `_EDITORIAL_FIELDS` ni en el `input_schema` de
# `ConfigurarPerfilSocialTool`. `save_editorial_profile` sí lo PRESERVA (ver el comentario en
# esa función) gracias a que arranca su `config` de todo lo que ya había guardado.
# ---------------------------------------------------------------------------

_AGENDA_STATE_FIELD = "agenda_estado"

# Los `CAST(:key AS text)` NO son decorativos: `jsonb_build_object` es variádica sobre `"any"`,
# así que sin el cast Postgres responde `could not determine data type of parameter $N`. No lo
# quites (ver el comentario de `SQL_GET_EDITORIAL_PROFILE` y el test de preparación).
SQL_SAVE_AGENDA_STATE = """
    INSERT INTO social_editorial_profiles (
        id, tenant_id, user_id, platform, config, version, created_at, updated_at
    ) VALUES (
        :id, :tenant_id, :user_id, :platform,
        jsonb_build_object(CAST(:key AS text), CAST(:value AS jsonb)), 1, now(), now()
    )
    ON CONFLICT (tenant_id, user_id, platform) DO UPDATE
    SET config = social_editorial_profiles.config
        || jsonb_build_object(CAST(:key AS text), CAST(:value AS jsonb)),
        updated_at = now()
"""


async def get_agenda_state(ctx: ToolContext, platform: str = "linkedin") -> dict[str, Any]:
    """Estado de rotación de `edecan_creative.agenda` para (tenant, usuario, plataforma).

    Devuelve `agenda.estado_inicial()` si el tenant todavía no tiene rotación guardada (sin
    `ctx.session`, primer uso, o un valor corrupto) -- nunca lanza: un estado inicial es
    exactamente lo que `agenda.siguiente_paso` espera para el primer turno.
    """
    profile = await get_editorial_profile(ctx, platform)
    estado = profile.get(_AGENDA_STATE_FIELD)
    return estado if isinstance(estado, dict) else agenda.estado_inicial()


async def save_agenda_state(ctx: ToolContext, platform: str, estado: Mapping[str, Any]) -> None:
    """Persiste `estado` (el `dict` que devuelven `agenda.siguiente_paso`/`registrar_historial`)
    bajo `_AGENDA_STATE_FIELD`, fusionándolo en el `config` JSONB existente (`||` de Postgres)
    en vez de reemplazar la fila entera -- así nunca pisa un perfil editorial guardado en
    paralelo. Sin `ctx.session` (embebedores/pruebas sin almacenamiento), no hace nada: el
    llamador sigue funcionando con la rotación en memoria del turno actual, igual que
    `get_editorial_profile` degrada a un perfil vacío sin tumbar la herramienta.
    """
    if ctx.session is None:
        return
    await ctx.session.execute(
        text(SQL_SAVE_AGENDA_STATE),
        {
            "id": str(uuid.uuid4()),
            "tenant_id": str(ctx.tenant_id),
            "user_id": str(ctx.user_id),
            "platform": platform,
            "key": _AGENDA_STATE_FIELD,
            "value": json.dumps(dict(estado)),
        },
    )


_FIELD_DESCRIPTIONS: dict[str, str] = {
    "context_bank": (
        "Banco de contexto real de esta persona: identidad, empresas, hechos verificables y "
        "'cicatrices' (errores de los que aprendió). Es material de fondo para que el sistema "
        "no contradiga la realidad -- nunca se convierte en autobiografía dentro de un post."
    ),
    "fact_check_mode": (
        "Modo del auditor de hechos para esta cuenta. Déjalo vacío (comportamiento por "
        "defecto): cada cifra, fecha, cita o nombre propio tiene que venir literalmente de "
        "una fuente citada, sin excepción. El único otro valor que hace algo es EXACTAMENTE "
        "'escenas_ilustrativas' -- ninguna variante ni sinónimo cuenta -- y solo debes "
        "escribirlo cuando el dueño de la cuenta autorizó explícitamente que sus posts "
        "puedan ilustrar el argumento con una escena anónima y genérica (un bodeguero que "
        "fía, quien paga la renta el primero) sin exigirle a esa escena el mismo anclaje "
        "literal que a un hecho verificable. Nunca activa nada más: inventar cifras, citar "
        "textualmente a alguien, atribuirle algo a una persona real y nombrada, inventar una "
        "noticia o afirmar algo falso sobre la empresa siguen prohibidos siempre, con este "
        "modo activo o no."
    ),
}


class ConfigurarPerfilSocialTool(Tool):
    name = "configurar_perfil_social"
    description = (
        "Consulta o cambia la estrategia editorial personal de LinkedIn o X: objetivo, audiencia, "
        "voz, pilares, formatos, identidad visual, reglas para imágenes, llamadas a la acción, "
        "temas prohibidos y banco de contexto real. Si faltan audiencia, objetivo o voz, pregunta "
        "antes de guardar; nunca inventes esas decisiones."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "accion": {"type": "string", "enum": ["ver", "actualizar"]},
            "plataforma": {"type": "string", "enum": ["linkedin", "x"], "default": "linkedin"},
            **{
                field: {
                    "type": ["string", "array"],
                    "items": {"type": "string"},
                    "description": _FIELD_DESCRIPTIONS.get(
                        field, f"Preferencia editorial: {field}."
                    ),
                }
                for field in _EDITORIAL_FIELDS
                if field not in _CAMPOS_FUERA_DEL_CHAT
            },
        },
        "required": ["accion"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        platform = str(args.get("plataforma") or "linkedin").strip().lower()
        if platform not in {"linkedin", "x"}:
            return ToolResult(content="La plataforma debe ser LinkedIn o X.")
        if args.get("accion") == "ver":
            profile = await get_editorial_profile(ctx, platform)
            return ToolResult(
                content=(
                    f"Perfil editorial de {platform} cargado."
                    if profile["configured"]
                    else f"Todavía no hay un perfil editorial de {platform}. Pregunta objetivo, "
                    "audiencia, voz y dirección visual para configurarlo."
                ),
                data=profile,
            )
        patch = {
            field: args[field]
            for field in _EDITORIAL_FIELDS
            if field in args and field not in _CAMPOS_FUERA_DEL_CHAT
        }
        if not patch:
            return ToolResult(
                content="Dime qué quieres cambiar del objetivo, audiencia, voz o dirección visual."
            )
        profile = await save_editorial_profile(ctx, platform, patch)
        return ToolResult(
            content=(
                f"Actualicé tu forma de trabajar {platform}. Se aplicará a las próximas piezas."
            ),
            data=profile,
        )


@dataclass(frozen=True)
class PlatformSpec:
    label: str
    max_chars: int
    image_size: str
    guidance: str


PLATFORMS: dict[str, PlatformSpec] = {
    # 1080x1350 (vertical 4:5), no el 1200x627 apaisado anterior: mismo tamaño
    # que usaba REFERENCIA (`referencia/features/linkedin_content.py`) y el que
    # `imagen_editorial.componer_titular` asume por defecto. Dentro del rango
    # [64, 2048] px por lado que acepta `providers._parse_size`/`_fit_generated_image`
    # sin necesidad de un tamaño de fallback distinto.
    "linkedin": PlatformSpec(
        "LinkedIn", 3000, "1080x1350", "criterio profesional, párrafos breves y una idea útil"
    ),
    "x": PlatformSpec("X", 280, "1600x900", "directo, específico y sin relleno"),
    "instagram": PlatformSpec(
        "Instagram", 2200, "1080x1080", "apertura clara, lectura móvil y cierre accionable"
    ),
    "facebook": PlatformSpec(
        "Facebook", 63206, "1200x630", "natural, conversacional y fácil de compartir"
    ),
    "threads": PlatformSpec("Threads", 500, "1080x1080", "conversacional y conciso"),
    "tiktok": PlatformSpec("TikTok", 2200, "1080x1920", "caption breve con un gancho verificable"),
}

# Plataformas donde el copy NO lleva hashtags por defecto (`REGLAS_LINKEDIN` en
# `editorial.py`: "Sin hashtags y sin emojis por defecto"). El campo `hashtags`
# del schema sigue existiendo para Instagram/TikTok/X/Facebook/Threads, donde sí
# son parte normal del formato -- solo se rechaza explícitamente para estas.
# Mismo criterio (y mismo nombre) que `editorial._PLATAFORMAS_SIN_HASHTAGS`,
# que aplica el gate equivalente sobre el propio texto vía `revisar()`.
_PLATAFORMAS_SIN_HASHTAGS = frozenset({"linkedin"})

# Plataformas con perfil editorial real (`configurar_perfil_social` solo acepta
# estas dos, ver `ConfigurarPerfilSocialTool.input_schema`). Para el resto no
# tiene sentido consultar `get_editorial_profile`: siempre saldría vacío.
_EDITORIAL_PLATFORMS = frozenset({"linkedin", "x"})

_FALTA_COPY = "Necesito el tema y el texto final del post."

# Variante editorial, donde la herramienta acepta una llamada sin texto para resolver el
# destino primero: si el modelo llega aquí es porque ya pasó ese gate, así que hay que cerrarle
# la puerta a volver a preguntar la cuenta -- lo único que queda pendiente es escribir.
#
# Ojo con el alcance: el gate corre para todas las de `_EDITORIAL_PLATFORMS`, pero HOY solo
# LinkedIn puede tener más de un destino -- las claves `<plataforma>_<destino>` que lee
# `destinos_configurados` solo las escribe `content_studio` y ahí el destino se descarta si la
# plataforma no es LinkedIn (`ConfigurarPerfilSocialTool` guarda siempre la clave pelada). Por
# eso la descripción de la herramienta ofrece el sondeo sin texto SOLO en LinkedIn: anunciarlo
# en X sería un viaje de ida y vuelta garantizado a cambio de nada, y además contradiría al
# propio campo 'destino', que dice "Solo aplica a LinkedIn". Si algún día X tiene destinos
# configurables, esas tres frases se mueven juntas.
_FALTA_COPY_CON_DESTINO_RESUELTO = (
    _FALTA_COPY + " El destino ya no es una pregunta abierta (o lo indicaste tú, o este usuario "
    "no tiene más de una cuenta donde publicar): no lo preguntes otra vez. Escribe el post "
    "ahora y vuelve a llamar la herramienta con 'tema' y 'texto'."
)

# Cuánto del copy se guarda en el historial de agenda. Es el insumo de "borradores recientes
# que no debes imitar" del editor jefe, que ve 700 caracteres de cada uno.
_MAX_COPY_EN_HISTORIAL = 1000

_MAX_TOPIC_CHARS = 300
_MAX_COPY_CHARS = 64_000
_MAX_VISUAL_PROMPT_CHARS = 4_000
_MAX_ALT_CHARS = 1_000
_MAX_HASHTAGS = 20


def _clean_hashtags(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for item in raw[:_MAX_HASHTAGS]:
        tag = re.sub(r"[^\wáéíóúüñÁÉÍÓÚÜÑ]", "", str(item).lstrip("#"), flags=re.UNICODE)
        if tag and tag.casefold() not in {existing.casefold() for existing in result}:
            result.append(tag[:80])
    return result


def _split_x_thread(text: str, limit: int = 280) -> list[str]:
    """Divide sin cortar palabras y reserva el sufijo real ``N/N``.

    El cálculo converge según la cantidad de dígitos del total. Así incluso
    un texto directo de 100+ partes nunca produce piezas de 281 caracteres
    al pasar de ``99/99`` a ``100/100``.
    """

    compact = re.sub(r"[ \t]+", " ", text).strip()
    if len(compact) <= limit:
        return [compact]

    def split_payload(payload_limit: int) -> list[str]:
        words = compact.split()
        chunks: list[str] = []
        current = ""
        for original_word in words:
            word = original_word
            candidate = f"{current} {word}".strip()
            if len(candidate) <= payload_limit:
                current = candidate
                continue
            if current:
                chunks.append(current)
            while len(word) > payload_limit:
                chunks.append(word[:payload_limit])
                word = word[payload_limit:]
            current = word
        if current:
            chunks.append(current)
        return chunks

    total_digits = 1
    while True:
        suffix_width = 2 * total_digits + 2  # espacio + N + '/' + N
        chunks = split_payload(max(1, limit - suffix_width))
        required_digits = len(str(len(chunks)))
        if required_digits == total_digits:
            total = len(chunks)
            return [f"{chunk} {index}/{total}" for index, chunk in enumerate(chunks, start=1)]
        total_digits = required_digits


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    names = (
        ("/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/SFNS.ttf")
        if not bold
        else ("/System/Library/Fonts/SFNSDisplay-Bold.otf", "/System/Library/Fonts/SFNS.ttf")
    )
    # Windows no tiene `/usr/share/fonts` ni `/System/Library/Fonts`: sin un candidato
    # propio, `_font` caía directo al bitmap diminuto de `ImageFont.load_default()` en
    # cualquier Windows, degradando la tarjeta social siempre (no es un crash, pero sí
    # una regresión visual real y constante). Arial/Arial Bold vienen preinstaladas en
    # todo Windows de escritorio desde hace décadas, vía `%WINDIR%\Fonts` (con fallback
    # literal a `C:\Windows\Fonts` si la variable de entorno no está definida).
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows"
    fonts_dir = Path(windir) / "Fonts"
    candidates = [
        *names,
        str(fonts_dir / ("arialbd.ttf" if bold else "arial.ttf")),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _offline_social_card(*, headline: str, platform: PlatformSpec, size: str) -> bytes:
    """Visual editorial local: útil incluso con Ollama y sin API de imágenes."""

    try:
        width, height = (int(part) for part in size.lower().split("x", 1))
    except (ValueError, AttributeError):
        width, height = 1080, 1080
    width = max(320, min(width, 2048))
    height = max(320, min(height, 2048))
    image = Image.new("RGB", (width, height), "#0A0A0F")
    draw = ImageDraw.Draw(image)

    # Fondo profundo con una única luz cobalto/teal y una red de memoria sutil.
    for y in range(height):
        progress = y / max(height - 1, 1)
        color = (
            10 + int(18 * progress),
            10 + int(15 * progress),
            15 + int(42 * progress),
        )
        draw.line((0, y, width, y), fill=color)
    accent_y = int(height * 0.14)
    draw.rounded_rectangle(
        (int(width * 0.07), accent_y, int(width * 0.21), accent_y + max(8, height // 110)),
        radius=8,
        fill="#17E0C4",
    )
    for index in range(9):
        x = int(width * (0.62 + (index % 3) * 0.12))
        y = int(height * (0.12 + (index // 3) * 0.10))
        draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill="#17E0C4")
        if index % 3:
            draw.line((x - int(width * 0.12), y, x, y), fill="#3E37F5", width=2)
        if index >= 3:
            draw.line((x, y - int(height * 0.10), x, y), fill="#3E37F5", width=2)

    margin = int(width * 0.07)
    label_font = _font(max(18, width // 45), bold=True)
    draw.text((margin, int(height * 0.21)), platform.label.upper(), font=label_font, fill="#17E0C4")

    clean_headline = " ".join(headline.split())[:180] or "Nueva idea"
    font_size = max(34, min(width // 11, height // 7))
    while font_size >= 28:
        title_font = _font(font_size, bold=True)
        avg_char = max(font_size * 0.55, 1)
        line_width = max(9, int((width - 2 * margin) / avg_char))
        lines = textwrap.wrap(clean_headline, width=line_width)
        bbox = draw.multiline_textbbox(
            (0, 0), "\n".join(lines), font=title_font, spacing=font_size // 3
        )
        if bbox[3] - bbox[1] <= height * 0.48:
            break
        font_size -= 4
    title = "\n".join(lines[:6])
    draw.multiline_text(
        (margin, int(height * 0.32)), title, font=title_font, fill="#E7E7EF", spacing=font_size // 3
    )
    footer_font = _font(max(16, width // 55))
    draw.text(
        (margin, height - int(height * 0.09)),
        "CREADO LOCALMENTE CON EDECAN",
        font=footer_font,
        fill="#77778A",
    )

    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def _tenant_flags(ctx: ToolContext) -> dict[str, Any]:
    """Lee `ctx.extras["flags"]` para que el crítico de imagen (que llama a
    `ctx.llm.complete` por su cuenta) respete el downgrade de modelo por plan
    del tenant. Copia local a propósito -- mismo patrón que
    `edecan_docanalysis._util.tenant_flags`/`edecan_advisory._util.tenant_flags`
    (paquetes hermanos, no se importa entre paquetes, ARCHITECTURE.md §10.1)."""
    extras = ctx.extras if isinstance(ctx.extras, dict) else {}
    flags = extras.get("flags")
    return flags if isinstance(flags, dict) else {}


# Eran 1200 para TODAS las llamadas editoriales, y eso no alcanza para las que tienen que
# devolver el texto completo reescrito: `auditar_hechos` entrega hasta 1600 caracteres de post
# MÁS el array `problemas` dentro del mismo JSON, y `pulir_borrador` entrega además `tema`,
# `angulo`, `promesa_lector` y el objeto `visual`. Un JSON truncado a mitad de `texto` no cierra
# la llave, `_extraer_json` devuelve None y el turno se lee como un veto editorial cuando en
# realidad al modelo le faltó aire -- exactamente el mismo bug que ya obligó a subir
# `_MAX_TOKENS_ESCRITURA` de 1600 a 3000 en `redaccion.py`. REFERENCIA no tiene tope: usa el CLI.
_MAX_TOKENS_EDITORIAL = 2400


def _llamar_modelo_editorial(ctx: ToolContext, *, alias: str = "principal") -> LlamarModelo:
    """Adapta `ctx.llm.complete` a la firma `auditoria.LlamarModelo` (lista de mensajes de
    chat -> texto). `auditoria.py` nunca importa un proveedor concreto (ver su docstring);
    esta es la única pieza de cableado real, mismo criterio que `_generar_imagen_con_critica`
    para el crítico de imagen.

    `alias` existe por un fallo MEDIDO (01-ago-2026, captura del dueño): el motor de
    LinkedIn escribía con "profundo" (GLM 5.2) pero el editor jefe y el auditor -- los
    jueces del gusto -- seguían en "principal" (el modelo del chat, 17B), y un juez más
    débil que el escritor APRUEBA el relleno de nota de prensa que el fuerte produce en
    un mal día ("llega como una actualización clave... continúa redefiniendo..."). REFERENCIA
    no tiene esta asimetría: allá los jueces son del mismo calibre que el escritor. El
    motor de LinkedIn pasa `alias="profundo"`; el default "principal" queda para los
    llamadores baratos (p. ej. `crear_contenido_social`, que empaqueta texto ya escrito)."""

    async def _llamar(mensajes: list[dict[str, str]]) -> str:
        sistema = next((m["content"] for m in mensajes if m.get("role") == "system"), None)
        turnos = [
            ChatMessage(role=m.get("role", "user"), content=m.get("content", ""))
            for m in mensajes
            if m.get("role") != "system"
        ]
        respuesta = await ctx.llm.complete(
            alias,
            _tenant_flags(ctx),
            CompletionRequest(
                model=alias,
                system=sistema,
                messages=turnos,
                max_tokens=_MAX_TOKENS_EDITORIAL,
            ),
        )
        return respuesta.text

    return _llamar


# Cuánto del respaldo no citable ve el editor jefe. Le alcanza para reconocer que el borrador
# se apoya en algo real; el que tiene que comprobarlo frase por frase es `auditar_hechos`, y
# ese recibe el banco con su propio tope (`redaccion._MAX_BANCO_AUDITABLE_CHARS`).
_MAX_RESPALDO_EN_CALIDAD = 1200


def _contexto_calidad(
    *,
    plataforma: str,
    tema: str,
    perfil: Mapping[str, Any],
    fuentes: list[dict[str, str]],
    respaldo_no_citable: str = "",
) -> str:
    """Arma el `contexto` que espera `auditoria.revisar_calidad`: plataforma, tema pedido,
    preferencias del perfil editorial del tenant (si las configuró) y contra qué material se
    sostiene este borrador -- suficiente para que el editor jefe juzgue si el ángulo es
    publicable, sin reconstruir aquí todo el prompt de escritura.

    `respaldo_no_citable` es material verificable que NO es una fuente citable: hoy, el banco
    de contexto real del tenant. Va aparte de `fuentes` porque son dos cosas distintas para
    dos destinos distintos -- las fuentes se publican en el manifiesto, el banco jamás (ver
    `redaccion._material_para_auditar`) -- pero el editor jefe necesita ver las dos para
    juzgar si el texto se sostiene.

    `target_length` viaja en esta misma lista de campos (no en una rama aparte) a propósito:
    sin él, el editor jefe de `redaccion.pulir_borrador` REESCRIBE el texto sin saber que esta
    cuenta declaró un largo propio, y "pulir" para un editor que no conoce el objetivo termina
    recortando -- el defecto real detrás de un post de Acme que pedía 110-140 palabras y
    salió de 37 tras pasar por el editor jefe y el auditor, ninguno de los dos enterado del
    largo que la cuenta quería. El escritor (`redaccion._bloque_perfil`) ya lo recibía; esta
    función era el único eslabón del ciclo que no se lo pasaba a quien reescribe después."""
    partes = [f"Plataforma: {plataforma}.", f"Tema pedido por quien escribe: {tema}."]
    if perfil.get("configured"):
        for campo in ("purpose", "audience", "voice", "avoid", "target_length"):
            valor = perfil.get(campo)
            if isinstance(valor, str) and valor.strip():
                partes.append(f"{campo}: {valor.strip()[:400]}")
        pilares = perfil.get("content_pillars")
        if isinstance(pilares, list) and pilares:
            lista_pilares = ", ".join(str(p) for p in pilares[:8])
            partes.append(f"Pilares de contenido de esta cuenta: {lista_pilares}")
    if fuentes:
        titulos = "; ".join(f["title"] for f in fuentes[:6] if f.get("title"))
        if titulos:
            partes.append(f"Fuentes citables entregadas para este borrador: {titulos}.")
    elif not respaldo_no_citable:
        partes.append("Sin fuentes citables entregadas para este borrador.")
    if respaldo_no_citable:
        # Sin titular citable NO significa sin respaldo. Decirle al editor jefe que este
        # borrador no tiene nada detrás cuando sí se apoya en el banco de contexto real de la
        # cuenta es fabricarle el motivo de REGLA DURA ("no se sostiene en el contexto
        # disponible") que lo autoriza a vetar incluso en modo permisivo -- y ese veto cae
        # justo sobre el escalón que existe para que nunca se escriba sin respaldo.
        partes.append(
            "Respaldo verificable de este borrador, no publicable como cita (banco de "
            f"contexto real de la cuenta): {respaldo_no_citable[:_MAX_RESPALDO_EN_CALIDAD]}"
        )
    return "\n".join(partes)


def _claim_visual(visual: Mapping[str, str] | None, respaldo: str) -> str:
    """La frase que la imagen debe comunicar: el MISMO texto que se monta encima.

    `headline + ". " + support`, igual que REFERENCIA (`linkedin_content.py:966`). Sin `visual`
    (camino de `crear_contenido_social`) cae al titular que pasó el llamador.
    """
    if not visual:
        return respaldo
    partes = [str(visual.get("headline") or "").strip(), str(visual.get("support") or "").strip()]
    return ". ".join(parte for parte in partes if parte) or respaldo


def _bloque_imagen_png(contenido: bytes) -> dict[str, Any]:
    """Bloque de contenido multimodal `{"type": "image", ...}` en el formato común de
    `edecan_llm` -- mismo shape que `edecan_docanalysis.vision._bloque_imagen`. La miniatura
    que le pasa `imagen_editorial.criticar` siempre es PNG (la produce `Image.save(..., "PNG")`
    en `_miniatura`), así que el `media_type` no necesita detectarse."""
    b64 = base64.b64encode(contenido).decode("ascii")
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}}


_SISTEMA_CRITICO_IMAGEN = (
    "Eres un director de arte editorial exigente. Evalúas imágenes generadas antes de que un "
    "humano las vea, respondiendo únicamente con el JSON que te pidan, sin texto adicional."
)


async def _generar_imagen_con_critica(
    ctx: ToolContext,
    provider: ImageProvider,
    *,
    texto: str,
    claim: str,
    plataforma: str,
    tema: str,
    size: str,
    reservar_titular: bool = False,
) -> tuple[bytes | None, str]:
    """Cablea el bucle genera -> critica -> regenera de `imagen_editorial.generar_con_critica`
    con el proveedor de imágenes y el LLM reales del tenant (inyección de dependencia pura del
    módulo, ver su docstring).

    `reservar_titular` controla el parámetro `limpio` del generador: en `False` (por defecto)
    el proveedor entrega la imagen cubriendo el lienzo completo -- así la publicaba
    `crear_contenido_social`, que no monta nada encima. En `True` reserva una franja inferior
    para que el compositor tipográfico monte kicker/headline/support: lo que hace
    `crear_post_linkedin` cuando el escritor entregó el diccionario `visual`. Sin esta bandera,
    reservar el espacio siempre habría dejado una franja vacía en las llamadas donde nadie va
    a montar un titular, que es el defecto que documenta el módulo (`imagen_editorial`)."""

    async def _generar(prompt: str) -> bytes:
        return await provider.generate(prompt, size=size)

    async def _llamar_modelo(imagen_bytes: bytes, pregunta: str) -> str:
        respuesta = await ctx.llm.complete(
            "rapido",
            _tenant_flags(ctx),
            CompletionRequest(
                model="rapido",
                system=_SISTEMA_CRITICO_IMAGEN,
                messages=[
                    ChatMessage(
                        role="user",
                        content=[
                            _bloque_imagen_png(imagen_bytes),
                            {"type": "text", "text": pregunta},
                        ],
                    )
                ],
                max_tokens=768,
            ),
        )
        return respuesta.text

    return await generar_con_critica(
        _generar,
        texto=texto,
        claim=claim,
        llamar_modelo=_llamar_modelo,
        plataforma=plataforma,
        tema=tema,
        limpio=not reservar_titular,
    )


# Nombra TODOS los argumentos que hay que repetir, no solo el nuevo. Decir solo
# "con 'destino' puesto" hacía que el modelo rearmara la llamada desde cero con
# ese único campo y soltara `plataforma`, así que la herramienta lo rechazaba con
# "Plataforma inválida" -- un error que no dice nada de lo que de verdad pasó y
# que la persona ve como un fallo del asistente. Es el mismo defecto de siempre:
# pedirle al modelo que arrastre datos entre llamadas sin decirle cuáles.
_INSTRUCCION_REINTENTO_CON_TEXTO = (
    "cuando conteste, vuelve a llamar 'crear_contenido_social' repitiendo TODO lo que ya "
    "traía la llamada anterior -- 'plataforma' y 'tema' incluidos, no solo lo nuevo -- más "
    "'destino' y el post escrito en la voz de esa cuenta."
)


@dataclass(frozen=True)
class DecisionDeDestino:
    """Qué hacer con el destino antes de escribir: preguntarlo, o darlo ya por resuelto.

    Los dos campos son excluyentes y nunca vienen los dos llenos. Este tipo existe porque
    "no hay nada que preguntar" son en realidad DOS respuestas distintas que antes cabían en
    un mismo `None`: *el usuario tiene una sola cuenta y es ESTA* contra *no tiene ninguna
    configurada*. Colapsarlas perdía el único destino en silencio -- el post se escribía con
    el perfil `<plataforma>_personal` (vacío en un tenant que solo configuró su organización)
    y de ahí caía al legacy, y la card salía con `target: null`, que en el cliente es un
    borrador sin acción de publicar. No fallaba: escribía con la voz equivocada, que es peor,
    porque nadie se entera hasta que ya está publicado.
    """

    # El modal listo para devolver, o `None` si no hay nada que preguntar.
    pregunta: ToolResult | None = None
    # El destino ya resuelto sin preguntar, o `None` si no hay ninguno configurado (ahí el
    # default personal de `get_editorial_profile_for_destination` es la respuesta correcta).
    destino: str | None = None


async def _decidir_destino(
    ctx: ToolContext,
    platform: str,
    *,
    texto_ya_escrito: bool,
    instruccion_reintento: str = _INSTRUCCION_REINTENTO_CON_TEXTO,
) -> DecisionDeDestino:
    """Resuelve el destino, o arma el modal para que lo elija el usuario.

    Esta es la regla que separa "preguntar con criterio" de "preguntar por preguntar". Solo
    hay algo que preguntar cuando el usuario TIENE de verdad más de un destino donde
    publicar; en cualquier otro caso la respuesta ya se conoce y preguntar solo estorba:

    - 2+ destinos configurados -> se pregunta, con SUS nombres reales como opciones.
    - exactamente 1 -> ese es, en silencio, y se DEVUELVE en `destino`. Preguntar algo con una
      sola respuesta posible es el modal decorativo que no queremos; callarlo y además tirar
      la respuesta es el bug que documenta `DecisionDeDestino`.
    - 0 (o sin almacenamiento) -> `destino=None`, en silencio: el default personal de
      `get_editorial_profile_for_destination` es correcto y no hay alternativa que ofrecer.

    Tampoco se pregunta si el modelo ya mandó `destino` (el llamador lo verifica antes): si
    el usuario dijo a dónde va, volver a preguntarlo es no haberlo escuchado.

    Se corta ANTES de escribir el post, no después: un borrador con la voz equivocada es
    trabajo tirado, y peor, es lo que el usuario termina viendo publicado. Por eso el llamador
    evalúa este gate antes de exigir 'texto', y el schema deja llamar la herramienta sin él.

    `texto_ya_escrito` solo cambia cómo se lo explicamos al modelo: si ya gastó el turno
    escribiendo, hay que decirle que ese texto NO se usó -- si no, reenvía el mismo borrador
    con 'destino' puesto y publica en la voz equivocada, que es exactamente lo que este gate
    existe para evitar.

    `instruccion_reintento` es la frase final que le dice al modelo cómo retomar: cambia según
    qué herramienta chocó con el gate (`crear_contenido_social` necesita volver con el texto
    escrito; `crear_post_linkedin` solo con el destino, porque escribe él mismo). Mandarle a
    la herramienta equivocada es mandarlo justo al camino que hoy se le cae.
    """
    destinos = await destinos_configurados(ctx, platform)
    if len(destinos) == 1:
        return DecisionDeDestino(destino=destinos[0]["id"])
    if not destinos:
        return DecisionDeDestino()

    # El modal admite 4 opciones; con más destinos se ofrecen los primeros (personal va de
    # primero por el orden de `destinos_configurados`) y el texto libre cubre el resto, que
    # por eso NO se desactiva nunca aquí.
    # Ni la etiqueta ni el texto que se envía dicen "publicar": esta herramienta
    # NO publica -- su propia descripción lo dice, "no usa APIs de publicación" --
    # y prometerlo engañaba a los dos lados. A la persona, que esperaba que el post
    # apareciera solo en su perfil; y al modelo, que leía "Publícalo" como la tarea
    # y salía a buscar una forma de publicar en vez de entregar el borrador.
    # Lo que la respuesta tiene que transmitir es UNA sola cosa: con qué voz se
    # escribe. Publicar, cuando toca, es un paso aparte y explícito.
    opciones = [
        {
            "label": destino["label"],
            "description": (
                "Con tu voz personal"
                if destino["id"] == PERSONAL_DESTINATION_ID
                else "Con la voz de esta cuenta"
            ),
            "value": f"Escríbelo con la voz de '{destino['id']}'.",
        }
        for destino in destinos[:4]
    ]
    bloque = QuestionBlock(
        question="¿Con la voz de cuál de tus cuentas lo escribo?",
        header="Destino",
        options=opciones,
        multi_select=False,
        allow_free_text=True,
        fallback_text=(
            "¿Con la voz de cuál de tus cuentas lo escribo? Opciones: "
            + ", ".join(o["label"] for o in opciones)
            + "."
        ),
    )
    ids = ", ".join(f"'{d['id']}'" for d in destinos)
    return DecisionDeDestino(
        pregunta=ToolResult(
            content=(
                (
                    "No escribí el post todavía y no usé el texto que mandaste: "
                    if texto_ya_escrito
                    else "No escribí el post todavía: "
                )
                + f"este usuario tiene varias cuentas donde publicar ({ids}) y cada una escribe "
                "en una voz distinta, así que elegir mal arruinaría el texto. La pregunta con "
                "sus cuentas reales YA está en pantalla: no la repitas por tu cuenta, con tus "
                "propias palabras ni con otra herramienta. Termina tu turno aquí, sin texto y "
                "sin suponer una cuenta; " + instruccion_reintento
            ),
            data={"destinos_disponibles": destinos},
            presentation=[bloque.model_dump(mode="json")],
        )
    )


_ENV_SDUI_CARD_PILOTO = "EDECAN_SDUI_CARD_PILOTO"


def _card_piloto_activa() -> bool:
    """Piloto backend-only de Server-Driven UI (paso 5 del plan): APAGADO por
    defecto -- mismo comportamiento de siempre mientras esta env var no esté
    encendida, `social_draft` sigue siendo la única salida de
    `empaquetar_borrador_social`.

    Con `EDECAN_SDUI_CARD_PILOTO=1`, la función TAMBIÉN acuña una
    `GenericCardBlock` de resumen (`_card_resumen_piloto`, más abajo)
    construida enteramente con `construir_card_generica`
    (`edecan_core.cards`), ADEMÁS de `social_draft`, nunca en su lugar --
    es la prueba de que una card nueva se define solo en el backend, sin
    arriesgar el flujo de producción real que ya usa `social_draft`. Mismo
    patrón que `edecan_core.tools.base.confirmaciones_desactivadas`: una sola
    env var, no un interruptor por tool.
    """

    valor = os.getenv(_ENV_SDUI_CARD_PILOTO)
    return valor is not None and valor.strip().lower() not in {"", "0", "false", "no", "off"}


def _card_resumen_piloto(
    *,
    slug: str,
    spec: PlatformSpec,
    headline: str,
    copy: str,
    offline_visual: bool,
    imagen_id: uuid.UUID | None,
    imagen_nombre: str | None,
) -> GenericCardBlock:
    """Arma la card NUEVA del piloto: un resumen del MISMO borrador que
    `social_draft` ya muestra, pero compuesto íntegramente con las 6
    primitivas -- ninguna vista de Swift nueva (`CardGenericaView` ya sabe
    pintar cualquier árbol de este contrato desde el paso 4).

    A propósito NO incluye `approve_draft`: hoy no existe un endpoint que
    publique un borrador solo por `draft_id` (`POST /v1/content/social/publish`
    exige `target`/`text`/`image_file_id` explícitos -- ver la desviación ya
    documentada en `ChatView.swift`, caso `.approveDraft`). Ofrecer ese botón
    aquí simularía una acción que hoy mismo respondería con un error.
    """

    preview = copy.strip()
    if len(preview) > 280:
        preview = preview[:279].rstrip() + "…"

    botones = [BotonCard(accion=CopyTextAction(id="copiar-texto", label="Copiar texto", text=copy))]
    imagen = None
    if imagen_id is not None:
        imagen = ArtifactRef(
            file_id=imagen_id, filename=imagen_nombre or f"{slug}.png", mime="image/png"
        )
        botones.append(
            BotonCard(
                estilo="secundario",
                accion=SaveArtifactAction(
                    id="guardar-imagen", label="Guardar imagen", file_id=imagen_id
                ),
            )
        )

    return construir_card_generica(
        card_id=f"resumen-{slug}",
        fallback_text=f"Borrador de {spec.label}: {headline}"[:1000],
        kicker=spec.label,
        titulo=headline,
        badge=BadgeCard(contenido="Imagen local", tono="advertencia") if offline_visual else None,
        imagen=imagen,
        cuerpo=[preview] if preview else [],
        botones=botones,
    )


async def empaquetar_borrador_social(
    ctx: ToolContext,
    *,
    platform_key: str,
    spec: PlatformSpec,
    topic: str,
    copy: str,
    target: str | None,
    sources: list[dict[str, str]],
    hashtags: list[str],
    headline: str,
    visual_prompt: str,
    alt_text: str,
    con_imagen: bool,
    uploader: Uploader,
    image_provider: ImageProvider | None = None,
    # Diccionario editorial `{kicker, headline, accent, support}` del JSON del
    # escritor. Cuando llega, se compone el titular ENCIMA de la foto base con
    # `imagen_editorial.componer_titular` -- el paso que faltaba para que la
    # imagen de `crear_post_linkedin` se parezca a la que REFERENCIA entrega (foto
    # + kicker + headline serif + accent resaltado + support). Sin `visual`
    # (o cuando lo trae vacío), la imagen se entrega TAL CUAL, que es el
    # comportamiento de `crear_contenido_social` -- el modelo del chat le
    # pasa el copy pero no rellena estos campos, así que no se le monta nada
    # que él no pidió.
    visual: dict[str, str] | None = None,
    # Clave de storage para el estado de rotación (`edecan_creative.agenda`), separada de
    # `platform_key`. Por defecto es el mismo `platform_key` -- comportamiento IDÉNTICO al
    # de siempre para cualquier llamador que no la pase (`crear_contenido_social`,
    # `planificar_contenido_social`). `crear_post_linkedin` SÍ la pasa (el id del DESTINO,
    # ver `marcas.editorial_profile_key`): sin esto, la rotación de una página de
    # organización compartía el MISMO contador de territorio/formato/foco que el perfil
    # personal (las dos guardaban bajo el `platform_key` pelado, "linkedin"), así que el
    # avance de una pisaba la posición de la otra y el round-robin de 24 pilares de una
    # cuenta se desincronizaba con cada post que la OTRA cuenta generaba entremedio.
    agenda_key: str | None = None,
    # El estado de rotación EN MEMORIA del llamador, para NO re-leerlo de la base aquí.
    # Fallo MEDIDO (02-ago-2026): `crear_post_linkedin` avanzaba el reloj y lo guardaba,
    # y esta función re-leía el estado VIEJO para anexarle el historial -- su guardado
    # (el último) pisaba el avance. `territorio_idx` quedó clavado en 2 con 28 posts de
    # historial y el feed repitió la misma escena cinco veces ("alquiler... ya como que
    # aburre", Alex). `None` = re-leer como siempre (llamadores que no avanzan relojes).
    # NOTA para la otra sesión: este parámetro ya se perdió UNA vez en un merge -- si
    # este archivo se restaura desde otra rama, el test
    # `test_el_registro_del_historial_no_pisa_el_avance_del_territorio` lo delata.
    agenda_estado: Mapping[str, Any] | None = None,
    # El copy SIN la línea de cierre garantizada (`redaccion._con_cierre_garantizado`),
    # SOLO para calcular la firma estructural: con `closing_url`, el cierre pegado tapa
    # el `?` final y la firma "cierre_pregunta" queda ciega (hallazgo del panel).
    copy_para_firma: str | None = None,
) -> ToolResult:
    """Convierte un copy YA aprobado en el paquete entregable: cooldown, artefactos y card.

    Todo lo que va después del control de calidad vive aquí y no dentro de una tool, porque
    hay más de un camino legítimo hasta un copy publicable: `crear_contenido_social` recibe
    el texto ya escrito por el modelo del chat, y `crear_post_linkedin`
    (`edecan_creative.redaccion`) lo escribe él mismo en código. Duplicar este tramo haría
    que las dos superficies se separaran con el tiempo; peor, si el segundo camino delegara
    en el primero, el borrador pasaría DOS veces por `auditoria` -- el doble de costo y, como
    el editor no es determinista, la posibilidad real de que la segunda pasada tire un texto
    que la primera acababa de aprobar.

    `copy` debe llegar ya revisado (`editorial.revisar`) y auditado: esta función no juzga el
    texto, solo lo empaqueta. Los hashtags se agregan aquí, DESPUÉS de esos gates, por la
    razón que documenta `editorial._cierre_de_formula` (un cierre fijo agregado antes esconde
    la última línea real del argumento).
    """
    clave_agenda = agenda_key or platform_key
    # Cooldown geográfico/temático (`edecan_creative.agenda`): registra qué temas quedaron
    # en el texto FINAL (después de la auditoría, si corrió) para que
    # `planificar_contenido_social` pueda evitarlos en turnos futuros. El detector de temas
    # es determinista y corre siempre, haya o no LLM disponible para la auditoría.
    # El copy visual que de verdad se monta sobre la foto, ya reparado y completo. Se calcula
    # ANTES del cooldown porque su `headline` entra en la firma de estructura, y antes de
    # generar la imagen porque su `headline`+`support` son el `claim` contra el que se juzga.
    visual_para_montar = _visual_publicable(visual, tema=topic, titular=headline)

    # Cooldown geográfico/temático (`edecan_creative.agenda`): registra qué temas quedaron
    # en el texto FINAL (después de la auditoría, si corrió) para que
    # `planificar_contenido_social` pueda evitarlos en turnos futuros. El detector de temas
    # es determinista y corre siempre, haya o no LLM disponible para la auditoría.
    #
    # `firma` y `copy` son nuevos y sirven al cooldown ESTRUCTURAL portado de REFERENCIA
    # (`editorial.firma_estructura` / `firmas_prohibidas_recientes`): sin ellos no hay forma de
    # saber que los últimos tres posts abrieron todos con un porcentaje o cerraron todos con
    # una pregunta, y el feed se siente igual aunque los temas sean distintos. `copy` además
    # es lo que el editor jefe necesita para detectar clones de ARQUITECTURA, no sólo de tema
    # (`redaccion._bloque_borradores_recientes`).
    # SIN re-leer cuando el llamador ya trae el estado (ver el parámetro `agenda_estado`):
    # la re-lectura es la que pisaba el avance del reloj de rotación con el valor viejo.
    estado_base = (
        dict(agenda_estado)
        if agenda_estado is not None
        else await get_agenda_state(ctx, clave_agenda)
    )
    estado_agenda = agenda.registrar_historial(
        estado_base,
        {
            "temas": agenda.detectar_temas(copy),
            "tema": topic,
            "plataforma": platform_key,
            "copy": copy[:_MAX_COPY_EN_HISTORIAL],
            "firma": sorted(firma_estructura(copy_para_firma or copy, visual_para_montar)),
        },
    )
    await save_agenda_state(ctx, clave_agenda, estado_agenda)

    if hashtags:
        copy = f"{copy.rstrip()}\n\n" + " ".join(f"#{tag}" for tag in hashtags)
    parts = _split_x_thread(copy, spec.max_chars) if platform_key == "x" else [copy]
    if platform_key != "x" and len(copy) > spec.max_chars:
        return ToolResult(
            content=f"El copy excede el límite de {spec.label} ({len(copy)}/{spec.max_chars})."
        )

    manifest = {
        "schema_version": 1,
        "platform": platform_key,
        "platform_label": spec.label,
        "topic": topic,
        "copy": copy,
        "parts": parts,
        "hashtags": hashtags,
        "visual": {
            "headline": headline,
            "prompt": visual_prompt,
            "alt_text": alt_text,
            "size": spec.image_size,
        },
        "publication": {"status": "draft", "requires_human_confirmation": True},
        "sources": sources,
    }
    slug = _slug(f"{platform_key}-{topic}")
    markdown_lines = [f"# {topic}", "", f"Plataforma: {spec.label}", "", "## Copy", ""]
    for index, part in enumerate(parts, start=1):
        if len(parts) > 1:
            markdown_lines.extend([f"### Parte {index}", ""])
        markdown_lines.extend([part, ""])
    if alt_text:
        markdown_lines.extend(["## Texto alternativo", "", alt_text, ""])
    markdown_lines.extend(["Estado: borrador. Requiere revisión humana antes de publicar.", ""])

    artifacts: list[dict[str, str]] = []
    markdown_id, markdown_name = await uploader(
        ctx,
        data="\n".join(markdown_lines).encode("utf-8"),
        filename=f"{slug}.md",
        mime="text/markdown",
    )
    artifacts.append(
        {"file_id": str(markdown_id), "filename": markdown_name, "mime": "text/markdown"}
    )
    json_id, json_name = await uploader(
        ctx,
        data=json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        filename=f"{slug}.json",
        mime="application/json",
    )
    artifacts.append({"file_id": str(json_id), "filename": json_name, "mime": "application/json"})

    offline_visual = False
    visual_warning = ""
    if con_imagen:
        provider = image_provider or await get_tenant_image_provider(ctx)
        offline_visual = isinstance(provider, StubImageProvider)
        if offline_visual:
            image = _offline_social_card(headline=headline, platform=spec, size=spec.image_size)
        else:
            # `imagen_editorial.generar_con_critica` construye su propio prompt a partir del
            # copy REAL (`copy`) y del `claim` (la idea concreta que debe comunicar, aquí el
            # titular visual), en vez de un `visual_prompt` guionado aparte -- deliberado (ver
            # docstring de `construir_prompt_imagen`): un prompt de imagen a distancia del
            # texto verdadero es exactamente el defecto ("Luna, Terra y Sol" ilustrados al
            # pie de la letra) que ese módulo existe para evitar. `visual_prompt` se conserva
            # en el manifiesto como referencia, pero ya no arma el prompt real.
            try:
                image, motivo_rechazo = await _generar_imagen_con_critica(
                    ctx,
                    provider,
                    texto=copy,
                    # El `claim` es la frase contra la que el crítico juzga si la imagen
                    # comunica algo -- su propio eje "MÁS IMPORTANTE de todos". Tiene que ser
                    # EL MISMO texto que se va a montar encima, como en REFERENCIA
                    # (`linkedin_content.py:966`: `claim = headline + '. ' + support` tomados
                    # del mismo dict `visual` que luego imprime el compositor). Antes se
                    # juzgaba contra `titular_visual`, un campo DISTINTO del que se pinta, y
                    # `support` (el subtítulo, la consecuencia) no entraba nunca en el claim:
                    # el crítico aprobaba o rechazaba mirando el objeto equivocado.
                    claim=_claim_visual(visual_para_montar, headline),
                    plataforma=platform_key,
                    tema=topic,
                    size=spec.image_size,
                    # Solo se le pide franja al proveedor cuando SÍ vamos a
                    # montar algo abajo (el escritor entregó `visual`). En caso
                    # contrario, franja vacía = defecto documentado.
                    reservar_titular=visual_para_montar is not None,
                )
            except Exception:
                image, motivo_rechazo = None, "excepción inesperada generando la imagen"
            if image is None:
                # El copy y el manifiesto ya son resultados útiles. Un
                # proveedor externo caído, sin cuota o con un tamaño no
                # admitido no debe hacer perder el paquete completo.
                logger.warning(
                    "El proveedor de imágenes del tenant falló tras varios intentos "
                    "(tenant_id=%s platform=%s); usando una tarjeta editorial local. "
                    "Último motivo: %s",
                    getattr(ctx, "tenant_id", None),
                    platform_key,
                    motivo_rechazo,
                )
                image = _offline_social_card(
                    headline=headline,
                    platform=spec,
                    size=spec.image_size,
                )
                offline_visual = True
                visual_warning = (
                    "El proveedor de imágenes no respondió correctamente. "
                    "Conservé el post y preparé una tarjeta local "
                    "para que no pierdas el trabajo."
                )
            elif motivo_rechazo:
                # Se generó una imagen real pero el crítico automático (o el chequeo barato
                # de píxeles) no la aprobó dentro del máximo de intentos; se entrega la mejor
                # (última) de todas formas, marcada para que un humano la revise antes de
                # publicar -- nunca se descarta el trabajo por esto.
                visual_warning = (
                    "La imagen no pasó completamente la autocrítica editorial automática "
                    f"({motivo_rechazo}); revísala antes de publicar."
                )

        # Compositor tipográfico: SI el escritor entregó el diccionario editorial
        # `visual`, se monta encima de la foto (`kicker` + `headline` serif con
        # `accent` resaltado + `support` en apoyo). Es el mismo layout que REFERENCIA
        # entrega (`features/li_templates/foto.html`) y el trozo que faltaba
        # cablear para que la imagen de `crear_post_linkedin` deje de ser una
        # foto pelada. Se salta cuando no hay `visual` (el chat lo llama con
        # `crear_contenido_social` sin esos campos), en modo offline (la tarjeta
        # local ya trae su propio titular integrado) y si la composición lanza
        # (fuentes no encontradas, foto ilegible): perder el titular montado es
        # cosmético; devolver la foto base ya generada siempre es más útil que
        # tumbar el turno.
        if visual_para_montar and not offline_visual:
            try:
                # `target` es el id de destino ya resuelto (`marcas.BrandDestination.id`,
                # dato de configuración por tenant, NUNCA un literal de tipo — ver el
                # docstring de `edecan_creative.marcas`, que reemplazó a propósito el viejo
                # `Literal["personal", "organization"]` para que este repo no cargue con la marca
                # de un tenant concreto). "organization" aquí es el id que Alex configuró para
                # SU página de organización, igual que cualquier otro tenant configuraría el
                # suyo propio; se compara tal cual porque `BrandDestination` todavía no tiene
                # un campo genérico de "plantilla visual"/logo por destino (pendiente: sería
                # lo correcto para que otro tenant pudiera activar su propio logo sin tocar
                # código, pero está fuera del alcance de esta migración puntual).
                componer = (
                    componer_titular_organization if target == "organization" else componer_titular
                )
                image = componer(
                    image,
                    kicker=visual_para_montar.get("kicker", ""),
                    headline=visual_para_montar.get("headline", ""),
                    accent=visual_para_montar.get("accent", ""),
                    support=visual_para_montar.get("support", ""),
                )
            except Exception:  # noqa: BLE001 - cosmético; nunca tumba el turno
                logger.warning(
                    "componer_titular falló; se entrega la foto base sin titular montado",
                    exc_info=True,
                )

        image_id, image_name = await uploader(
            ctx, data=image, filename=f"{slug}.png", mime="image/png"
        )
        artifacts.append({"file_id": str(image_id), "filename": image_name, "mime": "image/png"})

    mode = "una tarjeta editorial local" if offline_visual else "una imagen del proveedor conectado"
    # Card de chat con el mismo contrato que `SocialContentOut`/
    # `SocialContentDraft` (`POST /v1/content/social`, ver
    # `edecan_schemas.chat.SocialDraftBlock`) para que iOS pueda ofrecer
    # copiar/guardar (o, si `target` no es `personal`, aprobar y publicar
    # de verdad) sin salir del chat. `target` viaja como `None` solo cuando
    # de verdad NO hay un destino LinkedIn conocido: otra plataforma, o un
    # tenant sin ninguna cuenta configurada — el cliente entonces ofrece
    # únicamente las acciones de solo lectura, nunca publicar. Que el chat
    # no mandara 'destino' NO alcanza para llegar aquí en `None`: el gate
    # (`_decidir_destino`) ya lo resolvió, preguntándolo o deduciéndolo.
    presentation = [
        {
            "type": "social_draft",
            "fallback_text": f"Borrador de {spec.label}: {headline}"[:1000],
            "status": "ready",
            "platform": platform_key,
            "target": target,
            "copy": copy,
            "parts": parts,
            "alt_text": alt_text,
            "offline_visual": offline_visual,
            "visual_warning": visual_warning,
            "artifacts": artifacts,
            "requires_human_confirmation": True,
        }
    ]
    if _card_piloto_activa():
        # Piloto backend-only de SDUI (paso 5 del plan): card NUEVA, ADEMÁS de
        # `social_draft`, nunca en su lugar -- ver `_card_piloto_activa`.
        presentation.append(
            _card_resumen_piloto(
                slug=slug,
                spec=spec,
                headline=headline,
                copy=copy,
                offline_visual=offline_visual,
                imagen_id=image_id if con_imagen else None,
                imagen_nombre=image_name if con_imagen else None,
            ).model_dump(mode="json")
        )
    return ToolResult(
        content=(
            f"Creé el paquete para {spec.label}: copy"
            f"{' en ' + str(len(parts)) + ' partes' if len(parts) > 1 else ''}, manifiesto"
            + (f" y {mode}." if len(artifacts) == 3 else ".")
            + " Quedó como borrador privado; no publiqué nada."
        ),
        data={
            "artifacts": artifacts,
            "platform": platform_key,
            "offline_visual": offline_visual,
            # El chat solo necesita los artefactos, pero superficies
            # dedicadas como el Studio creativo móvil necesitan pintar el
            # borrador inmediatamente sin volver a descargar y parsear
            # el manifiesto privado.
            "copy": copy,
            "parts": parts,
            "alt_text": alt_text,
            "visual_warning": visual_warning,
            "sources": sources,
            # La card `social_draft` ya contiene la imagen/copy y decide qué
            # acciones mostrar. En el chat, no vuelvas a listar `post.md`,
            # `manifest.json` y `post.png` como adjuntos sueltos debajo.
            # Las superficies dedicadas siguen recibiendo `artifacts`.
            "suppress_chat_artifacts": True,
            # Kicker/headline/accent/support YA reparados (`visual_para_montar`,
            # el mismo dict que se monta sobre la foto). Antes no se re-exponía,
            # así que una superficie que arma su PROPIA card desde `data` (p. ej.
            # el job `create_linkedin_post` del worker) no tenía el titular/kicker
            # y caía a un genérico "Borrador de LinkedIn". Aditivo: el chat no lo
            # lee (usa la card de `presentation`), así que no rompe nada.
            "visual": dict(visual_para_montar) if visual_para_montar else {},
        },
        presentation=presentation,
    )


class CrearContenidoSocialTool(Tool):
    name = "crear_contenido_social"
    description = (
        "Crea un paquete para una red social compatible: copy final, manifiesto reutilizable "
        "y una imagen original opcional. Entrega "
        "todos los archivos en el chat para abrirlos o compartirlos desde web, iOS y Android. "
        "No publica automáticamente ni usa APIs de publicación. Para textos cortos, puede "
        "convertir contenido largo en un hilo numerado.\n\n"
        "CUÁNDO NO ES ESTA: si el post de LinkedIn hay que ESCRIBIRLO ('hazme un post sobre "
        "X', 'escribe el post de hoy'), usa 'crear_post_linkedin', que investiga, escribe y "
        "audita en una sola llamada. Esta herramienta es para cuando el texto YA existe -- el "
        "usuario te lo dictó -- o la red no es LinkedIn.\n\n"
        "El 'texto' se revisa con gates deterministas antes de aceptarlo (ver el mensaje de "
        "error si falla): si la llamada devuelve una lista de correcciones, vuelve a llamar la "
        "herramienta con el texto corregido -- nunca insistas con el mismo texto sin cambios.\n\n"
        "En LinkedIn la cuenta de destino define la voz del post. Si no sabes en cuál de sus "
        "cuentas va, llama primero SOLO con 'plataforma' y 'tema', SIN 'texto': la herramienta "
        "le muestra al usuario la pregunta con sus cuentas reales si de verdad tiene más de una, "
        "o te pide el texto si no hay nada que preguntar. Escribir el post antes de saber el "
        "destino es trabajo tirado. Esa pregunta la hace esta herramienta, con los datos del "
        "usuario: no preguntes tú por tu cuenta a qué cuenta va el post, ni con texto ni con "
        "otra herramienta.\n\n"
        + REGLAS_LINKEDIN
        + "\n\n(Estas reglas duras rigen siempre que 'plataforma' sea LinkedIn. Para otras redes, "
        "usa la convención natural de cada una -- ver el campo 'guidance' de cada plataforma -- "
        "en particular hashtags y emojis, que ahí sí son normales.)\n\n"
        "Antes de escribir cualquier hecho de vida, empresa o 'cicatriz' personal del autor, "
        "consulta primero 'configurar_perfil_social' (accion='ver') para ver su banco de "
        "contexto real. Nunca inventes ni mejores un hecho que ese banco no liste; sin un banco "
        "configurado, no atribuyas ninguna experiencia personal al autor.\n\n"
        "La app ya muestra el copy completo, la imagen y las acciones en una tarjeta dentro del "
        "chat: tu respuesta final NO debe repetir ni parafrasear el copy entregado, solo "
        "confirmar en una frase que el borrador quedó listo."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "plataforma": {"type": "string", "enum": list(PLATFORMS)},
            "tema": {"type": "string", "description": "Tema o propósito del post."},
            "texto": {
                "type": "string",
                "description": (
                    "Copy final, verdadero y listo para revisión humana. En LinkedIn debe "
                    "cumplir REGLAS_LINKEDIN (ver la descripción de la herramienta): sin "
                    "hashtags, sin emojis, sin primera persona salvo que el perfil editorial la "
                    "autorice, sin plantillas delatoras de IA. Omítelo únicamente en la llamada "
                    "de sondeo de destino (LinkedIn sin 'destino' conocido, ver la descripción "
                    "de la herramienta); en cualquier otra llamada es obligatorio."
                ),
            },
            "titular_visual": {
                "type": "string",
                "description": "Titular breve para la tarjeta local si no hay modelo de imágenes.",
            },
            "visual_prompt": {
                "type": "string",
                "description": (
                    "Descripción visual original, sin pedir texto ilegible dentro de la imagen."
                ),
            },
            "alt_text": {"type": "string", "description": "Descripción accesible de la imagen."},
            "hashtags": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": _MAX_HASHTAGS,
                "description": (
                    "Hashtags del post. PROHIBIDO en LinkedIn (no lo llenes para esa "
                    "plataforma); en Instagram, X, Facebook, Threads y TikTok sí es normal."
                ),
            },
            "destino": {
                "type": "string",
                "pattern": DESTINATION_ID_PATTERN,
                "description": (
                    "Solo aplica a LinkedIn: id del destino de publicación configurado por "
                    "el usuario ('personal' es el perfil personal por defecto; una página "
                    "de organización usa el id que el usuario le haya puesto). Si no lo "
                    "sabes, omite este campo."
                ),
            },
            "fuentes": {
                "type": "array",
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "url": {"type": "string"},
                        "snippet": {"type": "string"},
                    },
                    "required": ["title", "url"],
                },
            },
            "con_imagen": {"type": "boolean", "default": True},
        },
        # 'texto' NO es requerido: el gate de destino corre antes de exigirlo, y dejarlo
        # obligatorio en el schema obligaría al modelo a escribir el post entero solo para
        # enterarse de que la cuenta todavía estaba por decidir.
        "required": ["plataforma", "tema"],
    }

    def __init__(
        self,
        *,
        image_provider: ImageProvider | None = None,
        uploader: Uploader | None = None,
    ) -> None:
        self._image_provider = image_provider
        self._uploader = uploader or subir_archivo

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        platform_key = str(args.get("plataforma") or "").strip().lower()
        spec = PLATFORMS.get(platform_key)
        if spec is None:
            return ToolResult(content=f"Plataforma inválida. Usa una de: {', '.join(PLATFORMS)}.")
        topic = _cap_str(args.get("tema"), _MAX_TOPIC_CHARS)
        copy = _cap_str(args.get("texto"), _MAX_COPY_CHARS)

        destino_raw = str(args.get("destino") or "").strip().lower()
        target = destino_raw if is_valid_destination_id(destino_raw) else None
        es_editorial = platform_key in _EDITORIAL_PLATFORMS

        # Antes de escribir nada: si el destino es genuinamente ambiguo, preguntar sale
        # muchísimo más barato que publicar con la voz equivocada. La decisión NO se le deja
        # al criterio del modelo (que preguntaría de más o de menos): es determinista y solo
        # dispara cuando de verdad hay algo que preguntar -- ver `_decidir_destino`.
        #
        # Va ANTES de exigir tema+texto a propósito: si primero se pidiera el borrador, el
        # destino se preguntaría cuando el post YA está escrito, y la respuesta obliga a
        # reescribirlo entero en otra voz. Un turno completo del modelo tirado, en la cara del
        # usuario. Este gate no necesita el texto para decidir, solo la plataforma.
        if target is None and es_editorial:
            decision = await _decidir_destino(ctx, platform_key, texto_ya_escrito=bool(copy))
            if decision.pregunta is not None:
                return decision.pregunta
            # No preguntar no significa no tener destino: con una sola cuenta configurada,
            # ESA es la cuenta, y perderla aquí escribe el post con el perfil equivocado.
            target = decision.destino

        # Pasado el gate, el destino ya no es una pregunta abierta. Si igual falta el texto,
        # el modelo tiene que salir de aquí sabiendo que lo único pendiente es escribir: sin
        # esa frase el camino "sin texto y sin nada que preguntar" lo deja en el limbo de
        # volver a preguntar por la cuenta, que es justo el bucle que estamos cortando.
        if not topic or not copy:
            return ToolResult(
                content=_FALTA_COPY_CON_DESTINO_RESUELTO if es_editorial else _FALTA_COPY
            )

        hashtags = _clean_hashtags(args.get("hashtags"))
        if hashtags and platform_key in _PLATAFORMAS_SIN_HASHTAGS:
            return ToolResult(
                content=(
                    f"En {spec.label} no se usan hashtags por defecto. Quita el campo "
                    "'hashtags' de la llamada y vuelve a intentarlo sin él."
                )
            )

        sources = [
            {
                "title": _cap_str(item.get("title"), 240),
                "url": _cap_str(item.get("url"), 2000),
                "snippet": _cap_str(item.get("snippet"), 600),
            }
            for item in (args.get("fuentes") or [])[:6]
            if isinstance(item, dict) and item.get("title") and item.get("url")
        ]

        # Perfil editorial: hace falta tanto para el mensaje de rechazo de más abajo (banco de
        # contexto) como para el contexto de `auditoria.revisar_calidad` -- se busca una sola
        # vez aquí en vez de solo cuando hay violaciones, como antes.
        profile = (
            await get_editorial_profile_for_destination(ctx, platform_key, target)
            if es_editorial
            else {}
        )

        # NORMALIZAR ANTES DE REVISAR. Faltaba, y era un rechazo por algo mecánicamente
        # arreglable: `redaccion` sí normalizaba antes de su gate, pero aquí `revisar(copy)`
        # corría sobre el texto crudo, así que un guion largo, un hashtag o un emoji en un
        # texto DICTADO por el usuario le devolvía la herramienta rechazada en vez de
        # arreglárselo. En REFERENCIA es imposible que eso sea motivo de descarte:
        # `_normalizar_texto_post` (`linkedin_content.py:266`) repara antes de cualquier
        # chequeo.
        copy = normalizar(copy, platform_key)

        # Gates deterministas de `editorial.revisar()` sobre el texto CRUDO del modelo, antes de
        # sumarle cualquier cierre fijo (aquí, los hashtags de otras plataformas): el docstring
        # de `editorial._cierre_de_formula` explica por qué el orden importa -- agregar un cierre
        # fijo antes esconde la última línea real del argumento y el gate deja de verla.
        # `banco_es_identidad_privada`: en una página de organización el banco es
        # información pública de la marca, no la identidad privada de una persona
        # (ver `editorial.revisar`). Sin esto, la página no puede nombrarse a sí misma.
        banco_es_privado = target in (None, PERSONAL_DESTINATION_ID)
        violaciones = revisar(
            copy,
            platform_key,
            banco_privado=context_bank_text(profile),
            banco_es_identidad_privada=banco_es_privado,
        )
        if violaciones:
            mensaje = (
                "El texto no pasó el control de calidad editorial. Corrige esto y vuelve a "
                "llamar la herramienta con el texto ya corregido, sin reescribir en silencio:\n"
                + "\n".join(f"- {v}" for v in violaciones)
            )
            if es_editorial and not profile.get("context_bank"):
                mensaje += (
                    "\n\nAdemás: este perfil no tiene un banco de contexto real configurado "
                    "(consúltalo con 'configurar_perfil_social', accion='ver'), así que ningún "
                    "hecho de vida, empresa o cicatriz personal puede atribuirse al autor en "
                    "este post."
                )
            return ToolResult(content=mensaje)

        # Segunda pasada editorial (`edecan_creative.auditoria`, portada del pipeline de REFERENCIA):
        # un editor jefe que puede rechazar un borrador correcto pero plano/genérico, y un
        # auditor anti-invención que verifica cada cifra/fecha/hecho contra `sources`. Solo corre
        # si hay un LLM disponible (`ctx.llm`) -- embebedores/pruebas sin modelo degradan sin
        # auditoría en vez de tumbar la tool, mismo criterio que `get_editorial_profile` sin
        # `ctx.session`.
        if ctx.llm is not None:
            llamar_modelo = _llamar_modelo_editorial(ctx)
            contexto_calidad = _contexto_calidad(
                plataforma=platform_key, tema=topic, perfil=profile, fuentes=sources
            )
            # `permisivo=True`: quien llamó la herramienta (el usuario, o el modelo del chat en
            # su nombre) ya pidió este tema en concreto -- exactamente el caso que
            # `auditoria.revisar_calidad` documenta para desactivar el rechazo por gusto
            # editorial ("poco interesante", "muy amplio"). El veto anti-invención de
            # `auditar_hechos`, más abajo, no tiene ese parámetro: sigue siendo estricto sin
            # excepción sin importar el tema.
            publicable, motivo_calidad = await revisar_calidad(
                copy, contexto_calidad, llamar_modelo, permisivo=True
            )
            if not publicable:
                return ToolResult(
                    content=(
                        "El editor automático descartó este borrador (regla dura, no gusto "
                        f"editorial): {motivo_calidad}\n\nEscribe un ángulo distinto o más "
                        "concreto y vuelve a llamar la herramienta; no insistas con el mismo "
                        "texto sin cambios."
                    )
                )

            texto_auditado, problemas_hechos = await auditar_hechos(
                copy,
                sources,
                llamar_modelo,
                # Señal explícita del perfil, no una adivinanza sobre `copy` -- ver
                # `perfil_autoriza_escenas_ilustrativas`. Perfiles que no lo declararon (la
                # mayoría) siguen exactamente igual de estrictos que antes.
                escenas_ilustrativas_autorizadas=perfil_autoriza_escenas_ilustrativas(profile),
            )
            if not texto_auditado:
                detalle = "\n".join(f"- {p}" for p in problemas_hechos) or (
                    "El auditor no encontró cómo sostener el texto con las fuentes entregadas."
                )
                return ToolResult(
                    content=(
                        "El auditor de hechos descartó este borrador porque no se sostiene con "
                        f"las fuentes entregadas:\n{detalle}\n\nSi el evento es real, "
                        "investígalo primero con 'investigar_noticias_frescas' y vuelve a "
                        "llamar la herramienta con esos titulares en 'fuentes'; si no hay "
                        "fuente real, escribe el tema como idea general sin ese hecho puntual."
                    )
                )

            # RE-GATE OBLIGATORIO: el auditor reescribe el texto, y esa reescritura puede
            # reintroducir una violación de estilo que el gate de arriba ya había limpiado --
            # la lección crítica documentada en `auditoria.py` (aprendida a la mala en el
            # pipeline del que se portó: un borrador reintrodujo un cierre en pregunta porque el
            # auditor factual lo reescribió con un prompt que no conocía esas reglas de estilo).
            # Nunca se confía en que un texto sigue pasando los gates solo porque los pasó antes
            # de esta reescritura.
            #
            # Pero primero se REPARA, no se rechaza: el auditor reescribe con un prompt que no
            # conoce ninguna regla de estilo, así que reintroducir un "nos" o un "creo" es lo
            # esperado, y es mecánico de arreglar. Es el mismo pase que hace REFERENCIA en ese
            # punto exacto (`linkedin_content.py:1601-1605`). Y lo mecánico (guion largo,
            # hashtag, emoji que el auditor haya vuelto a meter) lo limpia `normalizar`.
            texto_auditado = normalizar(texto_auditado, platform_key)
            banco_privado = context_bank_text(profile)
            if revisar(
                texto_auditado,
                platform_key,
                banco_privado=banco_privado,
                banco_es_identidad_privada=banco_es_privado,
            ):
                reparado = await reescribir_sin_primera_persona(
                    texto_auditado, sources, llamar_modelo
                )
                if reparado:
                    texto_auditado = normalizar(reparado, platform_key)
            violaciones_finales = revisar(
                texto_auditado,
                platform_key,
                banco_privado=banco_privado,
                banco_es_identidad_privada=banco_es_privado,
            )
            if violaciones_finales:
                return ToolResult(
                    content=(
                        "La auditoría de hechos reescribió el texto para sostenerlo con las "
                        "fuentes, pero el resultado ya no pasa el control de estilo. Ajusta el "
                        "ángulo o las fuentes y vuelve a intentarlo:\n"
                        + "\n".join(f"- {v}" for v in violaciones_finales)
                    )
                )
            copy = texto_auditado

        return await empaquetar_borrador_social(
            ctx,
            platform_key=platform_key,
            spec=spec,
            topic=topic,
            copy=copy,
            target=target,
            sources=sources,
            hashtags=hashtags,
            headline=_cap_str(args.get("titular_visual"), 180) or topic,
            visual_prompt=_cap_str(args.get("visual_prompt"), _MAX_VISUAL_PROMPT_CHARS) or topic,
            alt_text=_cap_str(args.get("alt_text"), _MAX_ALT_CHARS),
            con_imagen=args.get("con_imagen", True) is not False,
            uploader=self._uploader,
            image_provider=self._image_provider,
        )


class PlanificarContenidoSocialTool(Tool):
    name = "planificar_contenido_social"
    description = (
        "Para contenido RECURRENTE sin un tema explícito del usuario (p. ej. 'escribe el post "
        "de hoy de LinkedIn'): dice qué territorio temático y qué forma argumental tocan en "
        "este turno, para que el feed rote y no se sienta repetitivo (`edecan_creative.agenda`), "
        "y arma la consulta de búsqueda que evita países/temas ya saturados en borradores "
        "recientes. Llama esta herramienta ANTES de escribir el texto y ANTES de "
        "'investigar_noticias_frescas' (usa 'query_sugerida' como consulta). Si el usuario ya "
        "pidió un tema concreto, NO uses esta herramienta: escribe sobre lo que pidió.\n\n"
        "En LinkedIn no hace falta: 'crear_post_linkedin' ya aplica esta rotación por dentro "
        "y entrega el borrador. Esta herramienta queda para X, o para consultar el plan sin "
        "producir un post."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "plataforma": {"type": "string", "enum": ["linkedin", "x"], "default": "linkedin"},
        },
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        platform_key = str(args.get("plataforma") or "linkedin").strip().lower()
        if platform_key not in _EDITORIAL_PLATFORMS:
            return ToolResult(content="La plataforma debe ser LinkedIn o X.")

        estado = await get_agenda_state(ctx, platform_key)
        paso = agenda.siguiente_paso(estado)
        foco = agenda.foco_actual(paso.estado)
        estado_siguiente = agenda.avanzar_foco(paso.estado)
        # El reloj de territorio/formato/foco avanza YA (guía hacia adelante, no una promesa
        # de que el borrador se publicará): mismo criterio documentado en
        # `agenda.siguiente_paso`. El cooldown temático, en cambio, solo se alimenta del texto
        # que de verdad terminó publicable (`CrearContenidoSocialTool.run` llama
        # `agenda.registrar_historial` sobre el copy final), así que un plan que el usuario
        # descarta a mitad de conversación nunca ensucia el historial de temas.
        await save_agenda_state(ctx, platform_key, estado_siguiente)

        recientes = agenda.temas_recientes_de_historial(estado.get("historial", []))
        temas_bloqueados = agenda.temas_en_cooldown(recientes)
        query_sugerida = agenda.query_con_diversidad(
            paso.territorio["query"],
            foco["busqueda"],
            agenda.terminos_a_excluir(temas_bloqueados),
        )

        return ToolResult(
            content=(
                f"Este turno toca el territorio '{paso.territorio['etiqueta']}' con la forma "
                f"'{paso.formato['id']}'. Investiga con 'query_sugerida' en "
                "'investigar_noticias_frescas' antes de escribir el post."
            ),
            data={
                "territorio": dict(paso.territorio),
                "formato": dict(paso.formato),
                "instruccion_formato": paso.instruccion_formato,
                "instruccion_cooldown": agenda.instruccion_cooldown(
                    temas_bloqueados, foco["etiqueta"]
                ),
                "foco_geografico": foco["etiqueta"],
                "query_sugerida": query_sugerida,
                "temas_en_cooldown": temas_bloqueados,
            },
        )


_MAX_CONSULTA_NOTICIAS_CHARS = 500


class InvestigarNoticiasFrescasTool(Tool):
    name = "investigar_noticias_frescas"
    description = (
        "Busca titulares de noticias REALES con fecha verificada (Google News, sin API key), "
        "publicados en los últimos días. Llama esta herramienta ANTES de escribir cualquier "
        "post que mencione un evento, lanzamiento, cifra, despido o hecho puntual: la memoria "
        "del modelo es vieja por definición y solo un evento que aparezca aquí puede citarse "
        "como actualidad. Pasa el resultado ('fuentes') tal cual al campo 'fuentes' de "
        "'crear_contenido_social' -- el auditor de hechos solo acepta lo que venga de ahí. Si "
        "no devuelve nada, trata el tema como una idea general sin ningún evento fechable; "
        "nunca inventes un titular ni una fecha.\n\n"
        "Para escribir un post de LinkedIn NO uses esta herramienta: llama "
        "'crear_post_linkedin', que ya investiga por dentro y no te obliga a transportar los "
        "titulares de una llamada a otra. Esta queda para investigar un tema por sí mismo."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "consulta": {
                "type": "string",
                "description": "Términos de búsqueda de noticias (español o el idioma del tema).",
            },
            "max_dias": {
                "type": "integer",
                "minimum": 1,
                "maximum": 30,
                "default": 3,
                "description": "Ventana de frescura en días.",
            },
            "maximo": {
                "type": "integer",
                "minimum": 1,
                "maximum": 30,
                "default": 8,
                "description": "Máximo de titulares a devolver.",
            },
        },
        "required": ["consulta"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        del ctx  # esta tool no toca tenant/sesión: la búsqueda de noticias es sin credencial.
        consulta = _cap_str(args.get("consulta"), _MAX_CONSULTA_NOTICIAS_CHARS)
        if not consulta:
            return ToolResult(content="Dime qué tema o consulta quieres investigar.")
        max_dias = max(1, min(30, int(args.get("max_dias") or 3)))
        maximo = max(1, min(30, int(args.get("maximo") or 8)))

        async with httpx.AsyncClient() as http:
            titulares = await titulares_frescos(http, consulta, maximo=maximo, max_dias=max_dias)

        if not titulares:
            return ToolResult(
                content=(
                    f"No encontré titulares recientes verificables para '{consulta}' en los "
                    f"últimos {max_dias} días. Trata el tema como una idea general sin ningún "
                    "evento fechable; no inventes una fecha ni un titular."
                ),
                data={"fuentes": []},
            )

        fuentes = [
            {"title": titular.titulo, "url": titular.url, "snippet": titular.snippet}
            for titular in titulares
        ]
        return ToolResult(
            content=(
                f"Encontré {len(fuentes)} titular(es) reciente(s) sobre '{consulta}'. Pásalos "
                "tal cual en 'fuentes' al llamar 'crear_contenido_social'."
            ),
            data={"fuentes": fuentes},
        )


__all__ = [
    "CrearContenidoSocialTool",
    "InvestigarNoticiasFrescasTool",
    "PLATFORMS",
    "PlanificarContenidoSocialTool",
    "build_context_bank_prompt_block",
    "context_bank_text",
    "empaquetar_borrador_social",
    "get_agenda_state",
    "get_editorial_profile",
    "destinos_configurados",
    "get_editorial_profile_for_destination",
    "perfil_autoriza_escenas_ilustrativas",
    "save_agenda_state",
    "save_editorial_profile",
]
