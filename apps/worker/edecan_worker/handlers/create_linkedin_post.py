"""Handler del job `create_linkedin_post`.

El post de LinkedIn se genera en SEGUNDO PLANO (no inline en el chat): corre el
motor real de Edecán para el COPY (escritor "profundo"/nemotron + editor jefe +
auditor de hechos + gates), genera la imagen con gpt-image-2 como director de
arte (`_generar_imagen`, config de PLATAFORMA `IMAGES_API_KEY`/`IMAGES_MODEL`)
y le monta el titular con `imagen_editorial.componer_titular` /
`componer_titular_organization` (logo + gradiente Aurora para páginas), y entrega el
post como CARD + PUSH en la conversación PRINCIPAL (is_main), igual que el hilo
de avisos de REFERENCIA.

Diseño de la imagen (por qué NO se usa `con_imagen=True` del tool): el tool, con
imagen, mete la foto por `generar_con_critica` sobre el proveedor BYO del tenant
(genera → juzga con visión → regenera). Ese lazo de crítica falla de forma
consistente en el worker y degrada a la tarjeta editorial offline (sin foto).
REFERENCIA tampoco usa ese lazo acá: pasa el POST COMPLETO a gpt-image-2 y compone
después. Así que este handler pide al tool solo el copy (`con_imagen=False`) —
que ahora SÍ expone `data["visual"]` (kicker/headline/accent/support ya
reparados) — y hace la foto él mismo: gpt-image-2 directo + el compositor
tipográfico. Robusto y fiel a REFERENCIA. (Nota histórica: una versión anterior de
este archivo decía "Flux.1 schnell" en varios comentarios; ese diseño ya no
existe y la palabra "Flux" en un reporte de bug casi siempre significa que la
imagen salió por el OTRO camino -- el proveedor BYO del tenant vía el agente.)

Bug histórico ya corregido: antes se pasaba `con_imagen=False` PERO se componía
con un `visual` que SIEMPRE salía vacío (el tool no lo exponía) → foto cruda sin
título; y la card se armaba sin botones. Hoy `visual` viene poblado y la card
lleva Copiar/Guardar.

Segundo bug histórico ya corregido (el que más dolía): la card de una página
pintaba "Aprobar y publicar" y el teléfono respondía con un error, porque no
había NADA que tradujera el `draft_id` del botón a texto+imagen+destino. Ahora
este handler guarda el borrador en `social_drafts` (`_persistir_borrador`)
ANTES de armar la tarjeta, con el MISMO id (`_card_id`) que viaja en el botón,
y `POST /v1/content/social/drafts/{draft_id}/publish` lo publica de verdad. Si
por lo que sea el borrador no se pudo guardar, la card sale con Copiar texto en
vez del botón que no podría cumplir.

Tercer bug histórico, el que dejó a una cuenta entera sin una sola entrega:
el chat contestaba "me pongo a escribir tu post" y no llegaba NADA. El trabajo
terminaba en `done`, sin mensaje, sin tarjeta y sin aviso. Dos causas, las dos
cerradas acá y en `redaccion`: (1) el motor daba por bueno un texto de 80
caracteres y este handler exigía 150 por su cuenta, así que todo lo que caía en
esa franja moría en silencio -- ahora el piso es UNO SOLO y se importa del motor,
que es quien lo garantiza; (2) cuando de verdad no hay post, este handler mandaba
únicamente un push, que no explica nada.

LA INVARIANTE, que manda sobre cualquier otra cosa de este archivo: **después de
que el chat dijo "me pongo a escribir", SIEMPRE tiene que llegar algo.** O el post,
o una explicación honesta de por qué no se pudo, escrita en la conversación. Nunca
silencio: quien pidió el post no puede quedarse sin saber si esperar, si repetirlo
o si el asistente se murió. Ver `_avisar_que_no_hubo_post`.

La invariante tiene DOS mitades, y la segunda costó otro repaso: no basta con
cubrir "el motor no dio post". La mitad que ENTREGA -- resolver la conversación,
armar la card, escribir el mensaje -- también puede fallar, y su fallo es el mismo
silencio con el agravante de que el post ya está escrito y pagado. Por eso hay red
en las dos, con este reparto:

  - falla el MOTOR  -> se explica en el chat (`_avisar_que_no_hubo_post`)
  - falla la CARD   -> se entrega el post en texto plano (`_entregar_sin_card`)
  - no llegó NADA   -> se deja que la cola reintente (`_rendirse_o_reintentar`),
                       porque no hay nada entregado que se pueda duplicar
  - nadie lo pidió  -> el autopiloto no recibe disculpas en el chat, sólo el aviso
                       (`_lo_pidio_una_persona`)

Contrato del payload (todo opcional salvo lo marcado):
  conversation_id: str | None  -> si falta, cae al chat principal (is_main). Su
                                  presencia es además la firma del pedido humano
  user_id:         str         -> OBLIGATORIO (JobEnvelope no lo trae)
  tema:            str | None  -> tema/slot; su presencia activa `permisivo`
  destino:         str | None  -> "personal" | "<org>" (p.ej. "organization") | None
  con_imagen:      bool        -> default True
  origen:          str | None  -> "automatizacion" cuando lo dispara el autopiloto
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import uuid

import httpx
from edecan_core.notifications import ImportantNotificationEvent
from sqlalchemy import text as sql_text

from ..deps import Deps
from ..repo import SqlRepo
from ..universal_notifications import notify_important_event

logger = logging.getLogger(__name__)

# gpt-image-2 (portado de REFERENCIA): quality "medium" -- ni low ni high (Alex:
# "no debe quemar dinero").
_IMAGE_QUALITY = "medium"

# CUADRADO de punta a punta -- antes se pedía vertical 1024x1536 y se
# componía a 1080x1350 (4:5), pero la card server-driven de este handler manda
# `aspecto="cuadrada"` (ver `_IMAGEN_ASPECTO_CARD` / `_armar_card`, y
# `ImagenNode.aspecto` -> `proporcion` 1:1 en `CardGenericaView.ImagenNodoView`
# del lado de iOS): montar una foto 4:5 dentro de un marco 1:1 con
# `contentMode: .fill` la recorta arriba y abajo, y ahí vive el titular. Alex,
# corrección del 31-jul: "haz que la imagen se vea en 500x500 para poder verla
# bien, así se ve encogida en la app". Generar y componer cuadrado de punta a
# punta cierra el recorte en la FUENTE en vez de esperar que la card lo
# disimule.
#
# Las dos mitades (lo que se le pide al proveedor de imágenes, y el lienzo del
# compositor tipográfico de `imagen_editorial.componer_titular*`) se derivan
# del MISMO lado configurable a propósito: si un tenant cambia uno sin el
# otro, vuelve el desajuste de proporción que causó este bug. Cada uno tiene
# su propia env var porque son cosas distintas -- el proveedor solo acepta
# ciertos tamaños fijos ("1024x1024", no cualquier número), el lienzo final sí
# puede ser cualquier lado -- pero comparten el mismo valor por defecto.
_IMAGE_LADO = int(os.getenv("EDECAN_LINKEDIN_IMG_LADO", "1024"))
_IMAGE_SIZE = f"{_IMAGE_LADO}x{_IMAGE_LADO}"
_COMPOSE_LADO = int(os.getenv("EDECAN_LINKEDIN_COMPOSE_LADO", "1080"))

# El `aspecto` que esta card manda a iOS para su imagen (ver `_armar_card`).
# Constante nombrada -- no un `"cuadrada"` suelto en medio de la función --
# para que quede junto a los tamaños de arriba que la hacen cierta: si algún
# día esta imagen deja de ser cuadrada, los tres valores se revisan juntos.
_IMAGEN_ASPECTO_CARD = "cuadrada"


async def handle(env, deps: Deps) -> None:  # noqa: ANN001 (JobEnvelope; ver handlers/__init__)
    if env.tenant_id is None:
        logger.warning("create_linkedin_post sin tenant_id; se descarta.")
        return
    tenant_id = env.tenant_id
    payload = env.payload or {}
    user_id_raw = str(payload.get("user_id") or "").strip()
    if not user_id_raw:
        logger.warning("create_linkedin_post sin user_id en el payload; se descarta.")
        return
    user_id = uuid.UUID(user_id_raw)
    tema = payload.get("tema") or None
    destino = payload.get("destino") or None
    con_imagen = payload.get("con_imagen", True) is not False

    settings = deps.settings

    copy_text = ""
    visual: dict[str, str] = {}
    file_id: uuid.UUID | None = None
    filename = ""
    aviso = ""
    sin_auditar = False
    # Por qué falló, en código estable, para poder decírselo a la persona en su
    # idioma más abajo (`_detalle_por_fallo`). Vacío = todavía no falló nada.
    fallo = ""
    # Copia local del piso del motor. Existe porque el import que lo trae es perezoso y vive
    # DENTRO del `try`: si el paquete faltara, el chequeo de más abajo reventaría con
    # `NameError` fuera de toda red, que es otra vez silencio. En 0 no afloja nada -- sin motor
    # `copy_text` queda vacío y el chequeo falla por ahí, que es lo correcto.
    piso_de_entrega = 0
    # El `try` envuelve TODO lo que produce el post: resolver el router de modelos, los
    # imports perezosos, el motor y la imagen. Los imports estaban fuera hasta que se hizo
    # notar la asimetría: `handlers/__init__` registra este handler "defensivo porque importa
    # `edecan_creative`, que puede no estar en un despliegue mínimo", pero ese registro
    # defensivo sólo cubre el import a nivel de módulo -- y acá los imports son perezosos, o
    # sea que reventaban DENTRO de `handle`, donde no había red. Un `ModuleNotFoundError` ahí
    # es exactamente el mismo silencio que el bug original, por una puerta lateral.
    #
    # Una excepción inesperada (el proveedor de imágenes tirando algo que no es HTTP, el
    # almacén de archivos caído) también es silencio si se la deja escapar. No se re-lanza en
    # el acto: primero se le explica a la persona; sólo si NI ESO se pudo entregar se deja que
    # la cola reintente (ver el final de este handler). Si ya había copy cuando reventó (falló
    # solo la imagen), el flujo sigue de largo y se entrega el post sin foto.
    try:
        from edecan_core.tools.base import ToolContext
        from edecan_creative._files import subir_archivo

        # El piso de entrega NO se declara acá: se importa del motor, que es quien lo
        # garantiza (ver `redaccion.MIN_COPY_ENTREGABLE_CHARS`). Este archivo tenía su
        # propia constante privada de 150 mientras el motor daba por bueno todo lo que
        # pasara de 80, y esa discrepancia -- dos números que nadie ponía uno al lado
        # del otro -- es la causa exacta del fallo que este handler ahora cierra: un
        # copy de 118 caracteres salía del motor como "listo" y moría aquí en silencio.
        # Con un solo número, importado, la discrepancia deja de ser posible por
        # descuido: si alguien lo mueve, se mueve para los dos.
        from edecan_creative.redaccion import MIN_COPY_ENTREGABLE_CHARS, CrearPostLinkedInTool

        piso_de_entrega = MIN_COPY_ENTREGABLE_CHARS
        llm_router = await deps.llm_router_for(tenant_id)
        async with deps.session_factory(None) as session:
            ctx = ToolContext(
                tenant_id=tenant_id,
                user_id=user_id,
                session=session,
                settings=settings,
                llm=llm_router,
                vault=deps.vault,
                extras={"flags": _plan_flags(deps, tenant_id)},
            )
            args: dict[str, object] = {"plataforma": "linkedin", "con_imagen": False}
            if tema:
                args["tema"] = tema
            if destino:
                args["destino"] = destino
            # Quién pidió el post decide si el editor jefe puede saltarse el turno. El
            # autopiloto SÍ puede (nadie está esperando; "no forzar un post" protege la
            # cuenta). Una persona que lo pidió, NO: ahí saltarse el turno se ve como que
            # el asistente no hizo nada. Sin esta línea el motor solo miraba `tema`, así que
            # "escríbeme un post para la página" sin nombrar tema caía del lado del cron y
            # terminaba entregando el texto crudo marcado "Sin revisar".
            #
            # La afirmación va en positivo y la hace quien SABE (este handler tiene el
            # `origen` y el `conversation_id`); el motor es fail-closed. Ver
            # `redaccion._lo_pidio_una_persona`.
            if _lo_pidio_una_persona(payload):
                args["lo_pidio_una_persona"] = True
            result = await CrearPostLinkedInTool().run(ctx, args)
            data = result.data if isinstance(result.data, dict) else {}
            # SOLO `data["copy"]` (el post empaquetado por el motor). NUNCA caer a
            # `result.content`: en los caminos de fallo del tool (`_no_publicable`,
            # `_sin_fuente`, `_sin_modelo`) ese `content` es un mensaje INTERNO de
            # diagnóstico ("Escribí el post 3 veces y ninguna pasó... devuelve SOLO
            # el objeto JSON...") que NO es un post -- entregarlo como "Borrador de
            # LinkedIn" con botón Copiar es un bug que ya se vio. Lo que sí se lee
            # de ahí es `data["fallo"]`: el motivo en código, que es lo que permite
            # explicarle el fallo a la persona sin repetirle jerga interna.
            copy_text = str(data.get("copy") or "").strip()
            fallo = str(data.get("fallo") or "")
            # El aviso de "esto no salió limpio" y la bandera de "el auditor de HECHOS no
            # validó este texto". Viajan en `data` justamente porque `content` no se lee acá.
            # Antes el aviso vivía sólo en `content` y por este camino se perdía entero: a la
            # página de una empresa le llegaba un borrador vetado por el auditor, con botón de
            # publicar y sin una palabra de advertencia. Ver `_armar_card`.
            aviso = str(data.get("aviso") or "").strip()
            sin_auditar = bool(data.get("sin_auditar"))
            v = data.get("visual")
            if isinstance(v, dict):
                visual = {
                    k: str(v.get(k) or "") for k in ("kicker", "headline", "accent", "support")
                }

            # Imagen: gpt-image-2 directo + compositor (con el `visual` YA poblado). Se hace
            # DENTRO del bloque de sesión para reusar `ctx` en `subir_archivo`, que
            # commitea la fila `files` en su PROPIA transacción (para que la card
            # resuelva la imagen a mitad de stream).
            if con_imagen and copy_text and len(copy_text) >= piso_de_entrega:
                img_png = await _generar_imagen_con_reintentos(settings, copy_text, visual, destino)
                if img_png:
                    file_id, filename = await subir_archivo(
                        ctx,
                        data=img_png,
                        filename=f"linkedin-{uuid.uuid4().hex[:8]}.png",
                        mime="image/png",
                    )
    except Exception:
        logger.exception(
            "create_linkedin_post: el trabajo se cayó antes de entregar (destino=%s).", destino
        )
        fallo = fallo or _FALLO_ERROR_INTERNO

    if not copy_text or len(copy_text) < piso_de_entrega:
        logger.warning(
            "create_linkedin_post: el motor no devolvió un post publicable "
            "(%d chars, mínimo %d, destino=%s, fallo=%s).",
            len(copy_text),
            piso_de_entrega,
            destino,
            fallo or "sin código",
        )
        llego_algo = await _avisar_que_no_hubo_post(
            deps,
            tenant_id=tenant_id,
            user_id=user_id,
            job_id=env.job_id,
            payload=payload,
            tema=tema,
            destino=destino,
            fallo=fallo,
        )
        _rendirse_o_reintentar(env, llego_algo, "no hubo post y tampoco se pudo avisar")
        return

    # --- 2) Borrador persistido -> card -> chat PRINCIPAL (is_main) + push ---
    # El borrador se guarda ANTES de armar la card, y con el MISMO id: es lo
    # único que le da sentido al botón "Aprobar y publicar". El teléfono manda
    # solo ese id (jamás el texto de vuelta, ver `ChatView.publicarBorrador`) y
    # el endpoint lo traduce a texto+imagen+destino. Sin fila, el botón promete
    # y no cumple -- que es exactamente el bug que este handler cierra.
    card_id = _card_id(file_id)
    draft_id = await _persistir_borrador(
        deps,
        tenant_id=tenant_id,
        user_id=user_id,
        card_id=card_id,
        destino=destino,
        copy_text=copy_text,
        file_id=file_id,
    )

    # ENTREGAR TAMBIÉN PUEDE FALLAR, y su fallo es el silencio EXACTO del bug original -- con
    # el agravante de que acá el post ya se escribió, la imagen ya se generó y la fila del
    # borrador ya está guardada. Este bloque corría desnudo mientras el aviso de "no hubo
    # post" sí estaba protegido: el mismo `_resolver_conversacion`, la misma base de datos, y
    # una red puesta sólo de un lado. Cualquier excepción aquí (base que se cayó un segundo,
    # una card que no valida) escapaba de `handle` y el turno terminaba sin una línea.
    conversation_id: uuid.UUID | None = None
    entregado = False
    try:
        async with deps.session_factory(None) as session:
            repo = SqlRepo(session)
            conversation_id = await _resolver_conversacion(
                repo, tenant_id=tenant_id, user_id=user_id, payload=payload
            )

            card_json = _armar_card(
                copy_text,
                visual,
                destino,
                file_id,
                filename,
                # `draft_id or card_id`: si hubo colisión de id, la fila quedó con
                # OTRO id -- la card tiene que llevar el que de verdad se guardó.
                card_id=draft_id or card_id,
                draft_id=draft_id,
                sin_auditar=sin_auditar,
            )
            tool_call_id = f"linkedin-{uuid.uuid4().hex[:12]}"
            evento_tool_end = {
                "type": "tool_end",
                "tool_call_id": tool_call_id,
                "name": "crear_post_linkedin",
                "result_preview": copy_text[:200],
                "blocks_version": 1,
                "blocks": [card_json],
            }
            await repo.add_message(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                role="assistant",
                content={
                    "text": _intro_del_post(
                        destino,
                        aviso,
                        # Solo cuenta como "perdida" si esta instalación SÍ genera
                        # imágenes (hay llave): en un despliegue sin proveedor, cada
                        # post sin foto es lo normal y el aviso sería ruido diario.
                        imagen_perdida=(
                            con_imagen
                            and file_id is None
                            and bool(getattr(settings, "IMAGES_API_KEY", None))
                        ),
                    ),
                    "presentation": [card_json],
                },
                tool_calls=[evento_tool_end],
            )
        # FUERA del `async with` a propósito: el commit ocurre al salir del bloque, así que
        # marcarlo adentro daría por entregado un mensaje que la transacción todavía puede
        # perder -- y entonces el segundo intento no correría y el turno quedaría mudo.
        entregado = True
    except Exception:
        logger.exception(
            "create_linkedin_post: el post estaba escrito y falló la ENTREGA (destino=%s).",
            destino,
        )
        # Segundo intento, sin card y contra el hilo principal: el fallo más probable acá es
        # de datos (un nodo que no valida), y el texto plano sí pasa. Que la persona reciba el
        # post en una burbuja fea es infinitamente mejor que no recibirlo.
        entregado, conversation_id = await _entregar_sin_card(
            deps, tenant_id=tenant_id, user_id=user_id, copy_text=copy_text, aviso=aviso
        )

    # --- 3) Push (durable + deduped) ----------------------------------------
    # `work_failed` cuando el post no se pudo poner en el chat: decir "contenido listo" de
    # algo que la persona no tiene en ninguna parte es peor que no decir nada, porque abre la
    # app a buscar lo que no está.
    await notify_important_event(
        deps,
        ImportantNotificationEvent(
            tenant_id=tenant_id,
            user_id=user_id,
            kind="content_created" if entregado else "work_failed",
            event_id=env.job_id,
            chat_id=conversation_id,
        ),
    )
    # A propósito NO se cuenta el push como entrega en este camino: acá el post EXISTE, y un
    # push que dice "un trabajo necesita atención" sobre un post escrito que no está en
    # ningún lado es el golpecito mudo que este archivo entero viene a erradicar. Si no se
    # entregó, se deja que la cola reintente mientras le queden intentos.
    _rendirse_o_reintentar(env, entregado, "el post se escribió pero no se pudo entregar")


def _intro_del_post(destino, aviso: str, *, imagen_perdida: bool = False) -> str:  # noqa: ANN001
    """La frase corta que acompaña a la card.

    El cuerpo COMPLETO del post ya va DENTRO de la card (nodo `cuerpo`). Si este texto
    repitiera el copy, iOS lo pinta DOS veces (una burbuja suelta + la card) -- el dueño lo
    reportó como incómodo. Por eso es un intro corto: el post vive en la card, donde están los
    botones.

    Y si el motor mandó un aviso ("esto no pasó limpio, verifica antes de publicar"), va AQUÍ.
    Ese aviso existía desde antes pero sólo dentro de `resultado.content`, que este handler
    ignora a propósito, así que por este camino no llegaba nunca: la persona recibía "tu
    borrador está listo 👇" sobre un texto que el auditor de hechos había vetado.

    `imagen_perdida` es la otra mitad de la honestidad (02-ago-2026): la imagen se pidió,
    la instalación SÍ tiene proveedor, y aun con reintentos no salió. Callarlo deja a la
    persona mirando una card pelada y preguntándose por qué -- pasó, textual: "no me dio
    imagen. ¿Por qué?". Decirlo cuesta una línea.
    """
    es_personal = destino in (None, "personal")
    intro = (
        "Tu borrador de LinkedIn está listo 👇"
        if es_personal
        else "Tu borrador para la página está listo 👇"
    )
    if aviso:
        intro = f"{intro}\n\n⚠️ {aviso}"
    if imagen_perdida:
        intro = (
            f"{intro}\n\n🖼️ La imagen no salió en este intento (el proveedor falló varias "
            "veces seguidas); el texto quedó listo. Pídeme el post de nuevo si la quieres "
            "con foto."
        )
    return intro


async def _entregar_sin_card(
    deps: Deps,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    copy_text: str,
    aviso: str,
) -> tuple[bool, uuid.UUID | None]:
    """Último intento de entregar un post que YA está escrito, sin tarjeta y en el hilo
    principal.

    Existe porque las dos mitades de este handler fallan por motivos distintos: la que escribe
    falla por el modelo, y la que entrega falla por la base de datos o por un nodo de card que
    no valida (`construir_card_generica` documenta que NO atrapa `ValidationError`). Si el
    fallo fue de la card, el texto plano pasa; si fue de la base, tampoco pasa esto -- y ahí ya
    manda el reintento de la cola (`_rendirse_o_reintentar`).

    Devuelve `(entregado, conversacion)`; nunca lanza, porque es la red de la red.
    """
    encabezado = (
        "Te escribí el post, pero se me trabó la tarjeta. Te lo dejo tal cual, para que lo copies:"
    )
    cuerpo = f"{encabezado}\n\n{copy_text}"
    if aviso:
        cuerpo = f"{cuerpo}\n\n⚠️ {aviso}"
    try:
        async with deps.session_factory(None) as session:
            repo = SqlRepo(session)
            conv = await repo.resolve_main_conversation(tenant_id=tenant_id, user_id=user_id)
            conversation_id = uuid.UUID(str(conv["id"]))
            await repo.add_message(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                role="assistant",
                content={"text": cuerpo},
            )
            return True, conversation_id
    except Exception:
        logger.warning(
            "create_linkedin_post: tampoco se pudo entregar el post en texto plano.",
            exc_info=True,
        )
        return False, None


# Cuántos reintentos de la cola se dejan correr antes de rendirse. La cola reintenta hasta
# `MAX_ATTEMPTS = 5` con backoff exponencial (30s, 60s, 120s...); acá se usa un número MENOR a
# propósito, porque cada reintento vuelve a correr el motor entero -- y la cuenta la paga el
# dueño. Dos reintentos son ~90 segundos: suficiente para que un corte momentáneo de base de
# datos se cure solo, y poco para no tener a nadie esperando.
_REINTENTOS_DE_COLA_ANTES_DE_RENDIRSE = 2


def _rendirse_o_reintentar(env, llego_algo: bool, motivo: str) -> None:  # noqa: ANN001 (JobEnvelope)
    """Si NADA le llegó a la persona, deja que la cola reintente mientras le queden intentos.

    Es el complemento honesto de tragarse las excepciones. Tragarlas es lo correcto cuando ya
    se le explicó a la persona lo que pasó: re-lanzar ahí le mandaría cinco veces el mismo
    mensaje. Pero cuando no se pudo entregar NI el post NI la explicación -- el caso típico es
    la base de datos caída, que tumba los dos canales a la vez -- terminar en `done` convierte
    un corte de treinta segundos en silencio permanente, que es justo el bug que este archivo
    existe para cerrar. Ahí sí vale re-lanzar: no hay nada entregado que se pueda duplicar.

    Se rinde (y termina en `done`) cuando se agotan los reintentos que valen la pena, para no
    dejar el trabajo dando vueltas ni quemar el motor cinco veces.
    """
    if llego_algo:
        return
    if getattr(env, "attempt", 0) < _REINTENTOS_DE_COLA_ANTES_DE_RENDIRSE:
        raise RuntimeError(
            f"create_linkedin_post: {motivo}; nada le llegó a la persona. Se deja que la cola "
            "reintente."
        )
    logger.error(
        "create_linkedin_post: %s y se agotaron los reintentos (attempt=%s). El trabajo termina "
        "sin entrega.",
        motivo,
        getattr(env, "attempt", 0),
    )


async def _resolver_conversacion(
    repo,  # noqa: ANN001 (Repo; el mismo protocolo que usa el resto del worker)
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: dict,
) -> uuid.UUID:
    """La conversación donde se entrega el resultado: la del pedido, o la principal.

    Vive aparte porque la usan los DOS finales del handler -- el que entrega el post y el
    que explica por qué no hubo post. Cuando estaba escrita en línea dentro del camino
    feliz, el camino de fallo no tenía adónde escribir y por eso se conformaba con un push
    mudo; separarla es lo que hace que decirle la verdad a la persona cueste una línea.
    """
    conv_id_raw = str(payload.get("conversation_id") or "").strip()
    conv = None
    if conv_id_raw:
        conv = await repo.get_conversation(
            tenant_id=tenant_id, conversation_id=uuid.UUID(conv_id_raw)
        )
    if conv is None:
        conv = await repo.resolve_main_conversation(tenant_id=tenant_id, user_id=user_id)
    return uuid.UUID(str(conv["id"]))


# Fallo que no viene del motor sino de este handler: algo reventó de forma inesperada.
# No es un código de `redaccion`, y no puede serlo -- el motor ni se enteró.
_FALLO_ERROR_INTERNO = "error_interno"

# `payload["origen"]` cuando el trabajo lo disparó una automatización y no una persona. Lo
# pone `run_automation._delegate_create_linkedin_post`. Ver `_lo_pidio_una_persona`.
_ORIGEN_AUTOMATIZACION = "automatizacion"

# Qué se le dice a la PERSONA cuando no hubo post, según el motivo. Reglas de este texto,
# porque se leen en el chat y no en un log:
#   - Sin jerga: ni nombres de herramientas, ni de parámetros, ni de funciones. Quien lee
#     esto pidió un post, no está depurando el motor.
#   - Siempre con una salida concreta. "No pude" a secas deja a la persona sin saber si
#     esperar, repetir el pedido o darse por vencida; que es justo lo que se sintió el día
#     que esto no existía y el chat se quedó mudo.
#   - Sin culpar al usuario y sin prometer un reintento automático que nadie va a hacer.
_DETALLE_SIN_FUENTE = (
    "No conseguí ninguna fuente reciente y verificable que sostenga ese tema, y prefiero "
    "no escribirte un post con datos que no puedo comprobar. Si me lo pides con un ángulo "
    "más concreto, o con la noticia puntual que quieres comentar, lo saco de una."
)
_DETALLE_NO_PUBLICABLE = (
    "Lo intenté varias veces y ninguna versión quedó lo bastante sólida como para "
    "entregártela, así que no te mando un borrador a medias. Dame un ángulo más concreto "
    "(una decisión, a quién le cambia algo, un dato verificable) y lo vuelvo a escribir."
)
_DETALLE_SIN_MODELO = (
    "No pude escribirlo porque ahora mismo no tengo disponible el modelo que redacta. "
    "Vuelve a pedírmelo en un rato; si sigue sin funcionar, lo reviso yo y te aviso."
)
_DETALLE_ERROR_INTERNO = (
    "Se me cayó el proceso a mitad de camino y no quedó nada guardado. Pídemelo otra vez "
    "y lo vuelvo a intentar."
)
_DETALLE_COPY_LARGO = (
    "Lo escribí entero, pero me quedó más largo de lo que LinkedIn deja publicar de una sola "
    "vez. Dime si prefieres que lo resuma o que lo parta en dos y te lo mando enseguida."
)
_MENSAJE_SIN_POST_GENERICO = (
    "No me salió nada que valiera la pena entregarte, y prefiero decírtelo a dejarte "
    "esperando. Pídemelo otra vez y, si puedes, dame un ángulo más concreto."
)


def _detalle_por_fallo() -> dict[str, str]:
    """Código de fallo -> qué se le dice a la persona.

    Las CLAVES se importan del motor en vez de escribirse a mano acá. Es la misma lección
    que el piso de entrega: dos capas repitiendo el mismo literal es una discrepancia
    esperando a pasar, y cuando pasa, el síntoma es que el mensaje se cae al genérico y la
    persona no se entera de por qué. Import perezoso y con red por la regla de la casa
    (`ARCHITECTURE.md` §10.1): `edecan_worker` no puede exigir un paquete hermano en tiempo
    de import, y sobre todo este mensaje es el ÚLTIMO recurso -- que no exista un paquete
    jamás puede ser el motivo de que el chat se quede mudo otra vez.
    """
    try:
        from edecan_creative.redaccion import (
            FALLO_COPY_LARGO,
            FALLO_NO_PUBLICABLE,
            FALLO_SIN_FUENTE,
            FALLO_SIN_MODELO,
        )
    except Exception:  # pragma: no cover - solo si falta el paquete hermano
        logger.warning(
            "create_linkedin_post: no pude leer los códigos de fallo del motor; el aviso sale "
            "genérico.",
            exc_info=True,
        )
        return {_FALLO_ERROR_INTERNO: _DETALLE_ERROR_INTERNO}
    return {
        FALLO_SIN_FUENTE: _DETALLE_SIN_FUENTE,
        FALLO_NO_PUBLICABLE: _DETALLE_NO_PUBLICABLE,
        FALLO_SIN_MODELO: _DETALLE_SIN_MODELO,
        FALLO_COPY_LARGO: _DETALLE_COPY_LARGO,
        _FALLO_ERROR_INTERNO: _DETALLE_ERROR_INTERNO,
    }


def _cual_post(tema, destino) -> str:  # noqa: ANN001 (str | None los dos; vienen del payload)
    """Cómo se nombra el post que no salió: por su tema y por la cuenta a la que iba.

    Con tres slots al día y varios pedidos en vuelta, "no pude escribirte el post" a secas no
    dice CUÁL no pude ni PARA DÓNDE era, y quien lo lee se queda esperando el que sí venía. El
    identificador del destino se muestra tal cual lo configuró el dueño: acá no hay ningún
    nombre de cuenta cableado, y resolver la etiqueta bonita costaría otra consulta a la base
    justo en el camino que ya viene de fallar.
    """
    tema_limpio = " ".join(str(tema or "").split())[:120]
    destino_limpio = " ".join(str(destino or "").split())[:60]
    es_personal = destino_limpio.lower() in ("", "personal")
    cuenta = "de LinkedIn para tu perfil" if es_personal else f"para la página «{destino_limpio}»"
    return f"el post {cuenta} sobre «{tema_limpio}»" if tema_limpio else f"el post {cuenta}"


def _mensaje_sin_post(fallo: str, tema, destino) -> str:  # noqa: ANN001 (str | None; del payload)
    """El aviso honesto que se escribe en el chat cuando no hubo post."""
    detalle = _detalle_por_fallo().get(fallo, _MENSAJE_SIN_POST_GENERICO)
    return f"No pude escribirte {_cual_post(tema, destino)}. {detalle}"


async def _avisar_que_no_hubo_post(
    deps: Deps,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    job_id: uuid.UUID,
    payload: dict,
    tema,  # noqa: ANN001 (str | None; el tema del payload)
    destino,  # noqa: ANN001 (str | None; el destino del payload)
    fallo: str,
) -> bool:
    """LA INVARIANTE DE ESTE HANDLER: después de "me pongo a escribir", SIEMPRE llega algo.

    O el post, o esta explicación. Nunca silencio -- el silencio es el peor final posible,
    porque la persona no sabe si esperar, si repetir el pedido o si el asistente se murió, y
    es exactamente lo que se vivió: el chat prometía el post, el trabajo terminaba en `done`
    y no aparecía ni la tarjeta ni una línea.

    Antes este camino mandaba SOLO `work_failed`, que es un push. Un push es un golpecito en
    el hombro, no una explicación: dice "un trabajo necesita atención" y al abrir la app no
    hay nada que leer. Por eso ahora se escribe primero el mensaje en el chat -- que es donde
    la persona hizo el pedido y donde va a ir a buscar la respuesta -- y el push va después,
    apuntando a esa misma conversación.

    ## PERO sólo si alguien lo pidió (`_lo_pidio_una_persona`)

    La invariante nace de una PROMESA: el chat dijo "me pongo a escribir". Donde no hubo
    promesa no hay nada que cumplir, y escribir igual hace daño -- el autopiloto tiene tres
    turnos al día, y un turno que se salta por falta de fuente fresca es un comportamiento
    NORMAL y documentado del motor ("no forzar un post"). Mandarle por eso tres mensajes
    diarios diciendo "dame un ángulo más concreto y lo vuelvo a escribir" es pedirle un ángulo
    por algo que nunca pidió, y a la tercera semana el chat es un basurero de disculpas.

    El push genérico `work_failed` ("Trabajo pendiente") tampoco sirve acá: viaja con
    `route: activity` y SIN `chat_id`, así que el teléfono abre la grilla vacía de Actividad
    y no hay ningún trabajo que revisar. Un skip esperado no es un trabajo pendiente. Se
    deja el log; no se empuja al teléfono.

    Nada de lo que pase aquí puede volver a tumbar el turno en silencio: si el mensaje no se
    puede escribir, se manda el push igual; si el push falla, ya quedó el mensaje. Cada
    excepción se traga a propósito y se registra, porque este es el último recurso y no tiene
    a quién delegarle el fallo.

    Devuelve si algo le llegó de verdad a la persona -- mensaje escrito o evento durable en la
    actividad. Ese `False` es lo único que distingue "ya le expliqué" de "no pude ni
    explicarle", y de esa distinción depende que la cola reintente (`_rendirse_o_reintentar`).
    """
    conversation_id: uuid.UUID | None = None
    mensaje_escrito = False
    if _lo_pidio_una_persona(payload):
        try:
            async with deps.session_factory(None) as session:
                repo = SqlRepo(session)
                conversation_id = await _resolver_conversacion(
                    repo, tenant_id=tenant_id, user_id=user_id, payload=payload
                )
                await repo.add_message(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    role="assistant",
                    content={"text": _mensaje_sin_post(fallo, tema, destino)},
                )
                mensaje_escrito = True
        except Exception:
            logger.warning(
                "create_linkedin_post: no se pudo escribir el aviso en el chat; queda el push.",
                exc_info=True,
            )
    else:
        logger.info(
            "create_linkedin_post: turno del autopiloto sin post (destino=%s, fallo=%s). No se "
            "escribe en el chat ni se manda push: nadie lo pidió; no hay nada que abrir.",
            destino,
            fallo or "sin código",
        )
        return True

    durable = False
    try:
        resultado = await notify_important_event(
            deps,
            ImportantNotificationEvent(
                tenant_id=tenant_id,
                user_id=user_id,
                kind="work_failed",
                event_id=job_id,
                # Con `chat_id` el push trae `deeplink` (`edecan://chat/...`) y tocarlo abre
                # justo la conversación donde acaba de quedar la explicación. Sin él, el push
                # deja a la persona en la pestaña de actividad, mirando un evento genérico y
                # sin el texto que sí escribimos.
                chat_id=conversation_id,
            ),
        )
        durable = bool(getattr(resultado, "durable", False))
    except Exception:
        logger.warning("create_linkedin_post: no se pudo avisar el fallo.", exc_info=True)

    return mensaje_escrito or durable


def _lo_pidio_una_persona(payload: dict) -> bool:
    """¿Hay alguien esperando una respuesta en un chat, o esto lo disparó el autopiloto?

    El `conversation_id` es la firma del pedido humano: el turno del chat que contesta "me
    pongo a escribir" encola SU conversación (`routers/conversations.py`), y la delegación de
    una automatización (`run_automation._delegate_create_linkedin_post`) no encola ninguna
    -- porque no hay ninguna. `origen` es la señal explícita para que esto no dependa de leer
    una ausencia: si mañana el autopiloto empieza a mandar conversación para entregar ahí sus
    posts, el silencio correcto de sus fallos no se convierte por accidente en tres disculpas
    diarias.
    """
    if str(payload.get("origen") or "").strip() == _ORIGEN_AUTOMATIZACION:
        return False
    return bool(str(payload.get("conversation_id") or "").strip())


def _card_id(file_id: uuid.UUID | None) -> str:
    """El id que comparten la CARD y su fila de `social_drafts`.

    Se deriva del `file_id` de la imagen (cuando hay) para que el id del
    borrador y el de la foto se puedan cruzar a ojo en la base de datos
    mirando una sola card. Es texto corto a propósito: viaja dentro del botón
    hasta el teléfono (`ApproveDraftAction.draft_id`) y vuelve en la URL del
    endpoint de publicar."""
    return f"linkedin-{file_id.hex[:8] if file_id else uuid.uuid4().hex[:8]}"


async def _persistir_borrador(
    deps: Deps,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    card_id: str,
    destino,  # noqa: ANN001 (str | None; el destino crudo del payload)
    copy_text: str,
    file_id: uuid.UUID | None,
) -> str | None:
    """Guarda el borrador en `social_drafts` y devuelve el `draft_id` guardado.

    Devuelve `None` si no se pudo guardar; entonces la card sale SIN el botón
    de publicar (ver `_armar_card`). Es deliberado: una tarjeta que ofrece
    "Aprobar y publicar" apuntando a una fila que no existe es peor que una
    tarjeta que solo deja copiar -- promete y falla justo cuando el dueño ya
    decidió publicar.

    El borrador del PERFIL personal también se guarda, aunque su card solo
    ofrezca Copiar/Guardar: la fila es el registro de lo que se escribió (con
    su destino y su imagen), no un permiso. Quién puede publicar sin manos lo
    sigue decidiendo la card, y publicar sigue exigiendo un acto explícito de
    la persona.

    ## Transacción PROPIA (no la del mensaje), y por qué

    Esta escritura NO va dentro del bloque que inserta el mensaje del chat: si
    la tabla todavía no existe (la migración `0029_social_drafts_tz` la aplica
    el dueño cuando quiere, no el arranque del worker), el `INSERT` reventaría
    y dejaría la transacción abortada en Postgres -- y con ella se perdería
    TAMBIÉN la card. O sea: por querer añadir el botón nuevo, el dueño se
    quedaría sin el post que hoy sí le llega. Con sesión aparte + `try`, lo
    peor que pasa es volver al comportamiento de antes (card con Copiar).

    ## Colisión de id

    `ON CONFLICT DO NOTHING` sobre `uq_social_drafts_tenant_id_draft_id`: si
    dos borradores del mismo tenant acuñan el mismo id corto, NO se pisa el
    texto del anterior (publicar el post equivocado es el peor final posible
    de este camino) -- se reintenta una vez con un id nuevo y más largo.
    """

    from edecan_creative.marcas import PERSONAL_DESTINATION_ID

    # `target` es NOT NULL: `None` significa "perfil personal", nunca "no sé".
    target = str(destino or PERSONAL_DESTINATION_ID).strip().lower()
    params = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "platform": "linkedin",
        "target": target,
        "text": copy_text,
        "image_file_id": file_id,
    }
    insert = sql_text(
        "INSERT INTO social_drafts ("
        "  tenant_id, user_id, draft_id, platform, target, text, image_file_id"
        ") VALUES ("
        "  :tenant_id, :user_id, :draft_id, :platform, :target, :text, :image_file_id"
        ") ON CONFLICT (tenant_id, draft_id) DO NOTHING "
        "RETURNING draft_id"
    )
    try:
        async with deps.session_factory(None) as session:
            for intento, candidato in enumerate((card_id, f"linkedin-{uuid.uuid4().hex[:12]}")):
                fila = (
                    (await session.execute(insert, {**params, "draft_id": candidato}))
                    .mappings()
                    .first()
                )
                if fila is not None:
                    return str(fila["draft_id"])
                logger.warning(
                    "create_linkedin_post: el draft_id %s ya existe para este tenant "
                    "(intento %d); no piso el borrador anterior.",
                    candidato,
                    intento + 1,
                )
    except Exception:
        logger.warning(
            "create_linkedin_post: no se pudo guardar el borrador en social_drafts "
            "(tenant_id=%s). La card sale sin el botón de publicar.",
            tenant_id,
            exc_info=True,
        )
    return None


def _plan_flags(deps: Deps, tenant_id: uuid.UUID) -> dict:
    # Best-effort: los flags del plan modulan el downgrade de modelo. Si no se
    # pueden resolver acá, el router usa el alias tal cual (profundo->nemotron).
    return {}


# Cuántas veces se intenta la imagen, y cuánto se espera entre intentos. Tres, con pausa
# creciente, por un fallo real (02-ago-2026): un solo hipo transitorio de la API de
# imágenes -- la MISMA llave y el MISMO modelo respondieron perfecto minutos después --
# dejó un post entregado sin foto y sin explicación. gpt-image-2 tarda ~40s por intento,
# así que el peor caso agrega ~2 minutos a un trabajo que ya es de minutos: barato al lado
# de una card muda sin imagen.
_REINTENTOS_IMAGEN = 3
_PAUSAS_IMAGEN_SEGUNDOS = (5.0, 20.0)


async def _generar_imagen_con_reintentos(
    settings, copy_text: str, visual: dict, destino
) -> bytes | None:
    """`_generar_imagen` con red de reintentos; devuelve `None` solo tras agotarlos.

    Cada fallo se loguea con su número de intento (nunca en silencio), y el `None` final
    es el que hace que `_intro_del_post` le DIGA a la persona que la imagen no salió --
    la mitad honesta del arreglo: reintentar cubre el hipo transitorio, avisar cubre el
    día en que ni los reintentos alcancen."""
    for intento in range(_REINTENTOS_IMAGEN):
        try:
            img = await _generar_imagen(settings, copy_text, visual, destino)
        except Exception:
            logger.warning(
                "create_linkedin_post: falló la imagen (intento %d/%d).",
                intento + 1,
                _REINTENTOS_IMAGEN,
                exc_info=True,
            )
            img = None
        if img:
            return img
        if img is None and not getattr(settings, "IMAGES_API_KEY", None):
            # Sin llave no hay nada que reintentar: `_generar_imagen` ya lo logueó.
            return None
        if intento < len(_PAUSAS_IMAGEN_SEGUNDOS):
            await asyncio.sleep(_PAUSAS_IMAGEN_SEGUNDOS[intento])
    return None


async def _generar_imagen(settings, copy_text: str, visual: dict, destino) -> bytes | None:
    """gpt-image-2 como DIRECTOR DE ARTE (portado de REFERENCIA `_generar_imagen_libre`,
    features/linkedin_content.py): se le pasa el POST COMPLETO y ÉL decide la forma
    -- metáfora, escena, infografía con logos reales -- que MEJOR representa la idea,
    en vez de que otro LLM pre-guione una escena genérica. El compositor monta el
    titular encima después. Sin crítico de visión (primer intento; con el prompt de
    dirección de arte gpt-image-2 acierta de una).

    `componer_titular_organization` (logo D + gradiente Aurora) si el destino es la
    página; `componer_titular` si es el perfil personal. El `visual` (kicker/
    headline/accent/support) llega poblado desde `data["visual"]` del tool.

    CUADRADA de punta a punta (`_IMAGE_LADO`/`_COMPOSE_LADO`): la card manda
    `aspecto="cuadrada"` (`_IMAGEN_ASPECTO_CARD`), así que la foto que se le
    pide al proveedor y el lienzo donde se monta el titular tienen que serlo
    también -- si no, vuelve el recorte que reportó Alex ("se ve encogida
    en la app")."""
    key = getattr(settings, "IMAGES_API_KEY", None)
    if not key:
        # En voz alta, nunca en silencio: sin esta línea, "no configuré la llave" y "el
        # proveedor falló" eran indistinguibles -- el post salía sin foto y nadie sabía
        # por qué, que es la clase de misterio que ya costó una mañana de depuración.
        logger.warning(
            "create_linkedin_post: no hay IMAGES_API_KEY configurada; el post sale sin imagen."
        )
        return None
    base_url = (getattr(settings, "IMAGES_BASE_URL", None) or "https://api.openai.com/v1").rstrip(
        "/"
    )
    model = getattr(settings, "IMAGES_MODEL", None) or "gpt-image-2"
    prompt = _prompt_arte(copy_text, visual)
    # gpt-image-2 es potente pero lento (~40s) -> timeout amplio.
    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.post(
            f"{base_url}/images/generations",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": model,
                "prompt": prompt,
                "size": _IMAGE_SIZE,
                "quality": _IMAGE_QUALITY,
            },
        )
        r.raise_for_status()
        data = r.json().get("data") or []
        b64 = data[0].get("b64_json") if data else None
    if not b64:
        return None
    base = base64.b64decode(b64)
    # Compositor tipográfico (import perezoso; self-contained, solo PIL).
    from edecan_creative.imagen_editorial import componer_titular, componer_titular_organization

    componer = componer_titular_organization if destino == "organization" else componer_titular
    return componer(
        base,
        kicker=visual.get("kicker", ""),
        headline=visual.get("headline", ""),
        accent=visual.get("accent", ""),
        support=visual.get("support", ""),
        # CUADRADO (ver `_COMPOSE_LADO` arriba), no el 1080x1350 (4:5) de
        # antes -- `componer_titular`/`componer_titular_organization` se ponen de
        # acuerdo con lo que pide la card (`_IMAGEN_ASPECTO_CARD`) porque sus
        # `width`/`height` son parámetros libres, no un default cableado acá;
        # el default de esas funciones (1080x1350) sigue intacto para OTROS
        # llamadores (`social.py::empaquetar_borrador_social`, que compone
        # sin pasar `width`/`height` y no se toca en este cambio).
        width=_COMPOSE_LADO,
        height=_COMPOSE_LADO,
    )


def _prompt_arte(texto: str, visual: dict) -> str:
    """Prompt de DIRECCIÓN DE ARTE portado de REFERENCIA (`features/linkedin_content.py
    ::_generar_imagen_libre`): NO se pre-guiona la escena con otro LLM -- se le pasa
    el POST COMPLETO a gpt-image-2 y ÉL decide la forma que MEJOR representa ESTA idea
    concreta (no un mood genérico intercambiable), con libertad creativa y logos reales
    si son verdad. Reserva el 38% inferior para el titular que el compositor monta."""
    claim = str(visual.get("headline") or "").strip()
    apoyo = str(visual.get("support") or "").strip()
    idea = (
        (
            f"La idea concreta que la imagen debe comunicar: {claim}. "
            + (f"El dato que la sostiene: {apoyo}. " if apoyo else "")
            + "PRUEBA: si esta misma "
            "imagen serviría igual para un titular completamente distinto, está mal, hazla "
            "específica a esta idea. "
        )
        if claim
        else ""
    )
    return (
        "Eres el director de arte de una revista de negocios. Este es el post de LinkedIn "
        f'(en español) que la imagen debe acompañar:\n\n"{texto[:2000]}"\n\n'
        + idea
        + "Crea la imagen que MEJOR represente esta idea concreta, no un mood genérico "
        "intercambiable. Tienes libertad creativa para decidir la forma: una escena, un "
        "objeto, una comparación visual, una ilustración editorial o una composición de "
        "datos. Elige UNA idea visual fuerte, no intentes explicar el post completo dentro "
        "de la imagen. La escena debe ocupar TODO el lienzo vertical, de borde a borde. El "
        "38% inferior sigue siendo parte visible del mismo entorno, con textura y detalle "
        "real, aunque sin otro sujeto protagonista, porque ahí se montará el titular con un "
        "degradado oscuro. PROHIBIDO terminar la escena a media altura, agregar un panel "
        "vacío o dejar una gran superficie sin contenido. NO escribas titulares, párrafos, "
        "cifras ni interfaces legibles dentro de la imagen base; el compositor añadirá todo "
        "el texto después. Logos y marcas reales: libres de usar, con el nombre/logo que "
        "corresponda, siempre que lo que muestren sea VERDAD (la empresa que el post menciona, "
        "su competencia real); usa como máximo UN logo protagonista. La única línea roja es no "
        "inventar una asociación falsa (no pongas el logo de un medio real como si ellos "
        "hubieran publicado esto). Si el post involucra a una persona real, famosa y nombrada, "
        "NO fabriques su rostro fotorrealista exacto: represéntala con un estilo ilustrado o "
        "sin mostrar su cara. Cualquier otra persona es genérica y anónima. Sin marcas de agua."
    )


def _armar_card(
    copy_text: str,
    visual: dict,
    destino,
    file_id,
    filename: str,
    *,
    card_id: str,
    draft_id: str | None,
    sin_auditar: bool = False,
) -> dict:
    """`draft_id=None` significa "el borrador no quedó guardado" (ver
    `_persistir_borrador`): entonces la card de una página cae a Copiar texto
    en vez de ofrecer "Aprobar y publicar". Regla de la casa: no se pinta un
    botón que no puede cumplir.

    `sin_auditar=True` (el motor entregó un borrador que el auditor de HECHOS vetó o
    ni llegó a mirar) hace exactamente lo mismo, y por una razón más seria: el botón
    de la página publica de un toque, en nombre de una empresa, sin que nadie vuelva a
    leer el texto. Un borrador cuyos hechos nadie verificó no puede estar a un toque de
    salir publicado -- y el perfil de esa clase de cuenta suele pedir justo lo contrario
    ("verificar la fuente antes de afirmar"). Copiar texto sigue disponible: no se le
    quita nada, se le quita el automatismo."""

    from edecan_core.cards import BotonCard, construir_card_generica
    from edecan_schemas import (
        ApproveDraftAction,
        ArtifactRef,
        CopyTextAction,
        SaveArtifactAction,
    )

    imagen = None
    if file_id is not None:
        imagen = ArtifactRef(file_id=file_id, filename=filename, mime="image/png")
    titulo = visual.get("headline") or "Borrador de LinkedIn"
    cuerpo = tuple(p for p in copy_text.split("\n\n") if p.strip())[:6]

    # Perfil personal (destino None o "personal"): copiar texto + guardar imagen
    # a mano, NUNCA publicar (regla de `SocialDraftBlock`). Página de organización
    # (p. ej. "organization"): botón privilegiado "Aprobar y publicar".
    es_personal = destino in (None, "personal")
    botones: list[BotonCard] = []
    if es_personal or draft_id is None or sin_auditar:
        botones.append(
            BotonCard(
                accion=CopyTextAction(id="copiar-texto", label="Copiar texto", text=copy_text)
            )
        )
        if file_id is not None:
            botones.append(
                BotonCard(
                    estilo="secundario",
                    accion=SaveArtifactAction(
                        id="guardar-imagen", label="Guardar imagen", file_id=file_id
                    ),
                )
            )
    else:
        botones.append(
            BotonCard(
                accion=ApproveDraftAction(
                    id="aprobar-post", label="Aprobar y publicar", draft_id=draft_id
                )
            )
        )

    return construir_card_generica(
        card_id=card_id,
        fallback_text=copy_text[:280] or "Borrador de LinkedIn listo",
        kicker=visual.get("kicker") or None,
        titulo=titulo,
        imagen=imagen,
        # `"cuadrada"` -- SIN esto, `construir_card_generica` cae a su
        # default ("panoramica", 16:9) y una foto cuadrada montada ahí sale
        # recortada arriba y abajo con `contentMode: .fill` (justo el "se ve
        # encogida" que reportó Alex). Ver `_IMAGE_LADO`/`_COMPOSE_LADO` más
        # arriba: la imagen que compone este handler es cuadrada de verdad,
        # así que el aspecto que la card promete tiene que serlo también.
        imagen_aspecto=_IMAGEN_ASPECTO_CARD,
        cuerpo=cuerpo,
        botones=botones,
    ).model_dump(mode="json")
