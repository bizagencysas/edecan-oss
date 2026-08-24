"""`edecan_worker.handlers.create_linkedin_post` — las dos costuras que se rompían.

**Costura 1: el CABLE entre la card y la publicación real**, o sea la fila de
`social_drafts` que traduce el `draft_id` del botón a texto+imagen+destino. Esos
tests no corren el handler completo: apuntan a que el id del botón y el id de la
fila sean EL MISMO, y a que una card nunca ofrezca "Aprobar y publicar" sin fila
detrás.

**Costura 2: la INVARIANTE**, que sí corre el handler entero con el motor
sustituido por un doble. Después de que el chat dice "me pongo a escribir", tiene
que llegar SIEMPRE algo: o el post, o una explicación honesta. El fallo real que
la originó fue el silencio -- el trabajo terminaba en `done`, sin mensaje, sin
tarjeta y sin aviso, porque el copy caía en la franja 80-150 donde el motor decía
"listo" y este handler descartaba sin contárselo a nadie.

`FakeSession` local (duplicada a propósito, `ARCHITECTURE.md` §10.1; la de
`tests/fakes.py` es un placeholder sin `execute`, porque hasta ahora ningún
handler le hablaba SQL directo desde este módulo).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest
from edecan_schemas import JobEnvelope
from edecan_worker.handlers import create_linkedin_post as handler
from fakes import FakeRepo, make_deps


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> FakeResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


@dataclass
class FakeSession:
    """`social_drafts` en memoria con el `UNIQUE(tenant_id, draft_id)` de verdad.

    `revienta=True` simula el caso real de esta instalación: el dueño decide
    cuándo aplicar la migración, así que el worker puede correr contra una base
    donde la tabla todavía no existe.
    """

    filas: list[dict[str, Any]] = field(default_factory=list)
    revienta: bool = False

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> FakeResult:
        if self.revienta:
            raise RuntimeError('relation "social_drafts" does not exist')
        valores = dict(params or {})
        assert "INSERT INTO social_drafts" in " ".join(str(stmt).split())
        ya_existe = any(
            f["tenant_id"] == valores["tenant_id"] and f["draft_id"] == valores["draft_id"]
            for f in self.filas
        )
        if ya_existe:  # ON CONFLICT (tenant_id, draft_id) DO NOTHING
            return FakeResult()
        self.filas.append({**valores, "status": "borrador", "published_provider_id": None})
        return FakeResult([{"draft_id": valores["draft_id"]}])


def _deps(session: FakeSession):  # noqa: ANN202
    @asynccontextmanager
    async def factory(tenant_id: uuid.UUID | None):
        # El handler abre la sesión "dueño" (`None`), que bypassa RLS: por eso
        # el `INSERT` lleva el `tenant_id` a mano.
        assert tenant_id is None
        yield session

    return make_deps(session_factory=factory)


def test_card_id_se_deriva_del_file_id_para_poder_cruzarlos_a_ojo() -> None:
    file_id = uuid.uuid4()
    assert handler._card_id(file_id) == f"linkedin-{file_id.hex[:8]}"
    # Sin imagen sigue habiendo id (el post de solo texto también es borrador).
    sin_imagen = handler._card_id(None)
    assert sin_imagen.startswith("linkedin-") and sin_imagen != handler._card_id(None)


async def test_persistir_borrador_guarda_texto_destino_e_imagen_con_el_id_de_la_card() -> None:
    session = FakeSession()
    tenant_id, user_id, file_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    draft_id = await handler._persistir_borrador(
        _deps(session),
        tenant_id=tenant_id,
        user_id=user_id,
        card_id="linkedin-3f9a1c22",
        destino="acme",
        copy_text="El post que el motor escribió.",
        file_id=file_id,
    )

    assert draft_id == "linkedin-3f9a1c22"
    [fila] = session.filas
    assert fila["tenant_id"] == tenant_id
    assert fila["user_id"] == user_id
    assert fila["platform"] == "linkedin"
    assert fila["target"] == "acme"
    assert fila["text"] == "El post que el motor escribió."
    assert fila["image_file_id"] == file_id
    # Nace inerte: publicar es un paso aparte, explícito y confirmado.
    assert fila["status"] == "borrador"
    assert fila["published_provider_id"] is None


@pytest.mark.parametrize("destino", [None, "personal"])
async def test_persistir_borrador_sin_destino_guarda_el_perfil_personal(destino) -> None:  # noqa: ANN001
    """`target` es NOT NULL: `None` significa "perfil personal", nunca "no sé"
    — mandar a la página de una empresa un texto que era para el perfil es el
    error caro y silencioso que esa columna existe para evitar."""
    session = FakeSession()

    await handler._persistir_borrador(
        _deps(session),
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        card_id="linkedin-aaaaaaaa",
        destino=destino,
        copy_text="Un post personal.",
        file_id=None,
    )

    assert session.filas[0]["target"] == "personal"
    assert session.filas[0]["image_file_id"] is None


async def test_persistir_borrador_ante_colision_no_pisa_el_borrador_anterior() -> None:
    """Con el id ya tomado se acuña otro, NO se sobreescribe el texto de la fila
    existente: la card vieja seguiría publicando lo que prometía."""
    session = FakeSession()
    tenant_id = uuid.uuid4()
    await handler._persistir_borrador(
        _deps(session),
        tenant_id=tenant_id,
        user_id=uuid.uuid4(),
        card_id="linkedin-3f9a1c22",
        destino="acme",
        copy_text="El primero.",
        file_id=None,
    )

    segundo = await handler._persistir_borrador(
        _deps(session),
        tenant_id=tenant_id,
        user_id=uuid.uuid4(),
        card_id="linkedin-3f9a1c22",
        destino="acme",
        copy_text="El segundo, con otro texto.",
        file_id=None,
    )

    assert segundo is not None and segundo != "linkedin-3f9a1c22"
    assert len(session.filas) == 2
    assert session.filas[0]["text"] == "El primero."


async def test_persistir_borrador_sin_tabla_no_tumba_el_post() -> None:
    """Si la migración todavía no se aplicó, el dueño no puede quedarse sin su
    post: se devuelve `None` (card sin botón) en vez de propagar el error."""
    draft_id = await handler._persistir_borrador(
        _deps(FakeSession(revienta=True)),
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        card_id="linkedin-3f9a1c22",
        destino="acme",
        copy_text="Un post que igual tiene que llegar.",
        file_id=None,
    )

    assert draft_id is None


def _acciones(nodo: Any) -> list[dict[str, Any]]:
    """Los `accion` de todos los botones de la card, en orden (los botones
    viven dentro del árbol de nodos, no en una lista plana)."""
    encontradas: list[dict[str, Any]] = []
    if isinstance(nodo, dict):
        if nodo.get("nodo") == "boton" and isinstance(nodo.get("accion"), dict):
            encontradas.append(nodo["accion"])
        for valor in nodo.values():
            encontradas.extend(_acciones(valor))
    elif isinstance(nodo, list):
        for item in nodo:
            encontradas.extend(_acciones(item))
    return encontradas


def test_card_de_pagina_lleva_el_boton_con_el_MISMO_id_de_la_fila() -> None:
    card = handler._armar_card(
        "Cuerpo del post.\n\nSegundo párrafo.",
        {"kicker": "Crédito", "headline": "Un titular"},
        "acme",
        None,
        "",
        card_id="linkedin-3f9a1c22",
        draft_id="linkedin-3f9a1c22",
    )

    assert card["card_id"] == "linkedin-3f9a1c22"
    [accion] = _acciones(card)
    assert accion["action"] == "approve_draft"
    # El id que viaja en el botón es el de la fila: si estos dos se separan,
    # el teléfono pide publicar algo que el servidor no encuentra.
    assert accion["draft_id"] == "linkedin-3f9a1c22"


def test_card_sin_borrador_guardado_no_promete_publicar() -> None:
    """Sin fila detrás, el botón "Aprobar y publicar" sería una promesa que no
    se puede cumplir — justo el bug que este trabajo cierra. La card cae a
    Copiar texto, que sí funciona siempre."""
    card = handler._armar_card(
        "Cuerpo del post.",
        {},
        "acme",
        None,
        "",
        card_id="linkedin-3f9a1c22",
        draft_id=None,
    )

    assert [a["action"] for a in _acciones(card)] == ["copy_text"]


# ---------------------------------------------------------------------------
# LA INVARIANTE: después de "me pongo a escribir", SIEMPRE llega algo al chat.
# ---------------------------------------------------------------------------
#
# El fallo, tal como se vivió: el dueño pidió un post, el chat contestó "listo, me pongo a
# escribir -- te llega aquí mismo en un momento", y no llegó NADA. Nunca. El trabajo quedó en
# `done`, sin mensaje, sin tarjeta y sin aviso, y el historial completo de la instalación no
# tenía una sola entrega para esa cuenta. Por dentro, el copy había salido de 118 caracteres:
# suficiente para que el motor lo diera por bueno, insuficiente para que este handler lo
# entregara. El camino de fallo mandaba un push (`work_failed`) y nada más -- un push dice
# "un trabajo necesita atención" y al abrir la app no hay nada que leer.
#
# Estos tests corren el handler ENTERO con el motor sustituido por un doble, porque lo que hay
# que fijar no es una función suelta sino el final del camino: qué recibe la persona.


class _MotorFalso:
    """Doble de `CrearPostLinkedInTool`: devuelve el `ToolResult` que se le programe.

    Se instala sobre `edecan_creative.redaccion.CrearPostLinkedInTool`, que es el nombre que
    el handler importa de forma perezosa dentro de `handle`. El motor real no corre: acá se
    mide lo que hace el handler con cada una de sus salidas posibles.
    """

    resultado: Any = None
    revienta: bool = False
    llamadas: list[dict[str, Any]] = []

    async def run(self, ctx, args):  # noqa: ANN001
        type(self).llamadas.append(dict(args))
        if type(self).revienta:
            raise RuntimeError("el proveedor de inferencia se cayó a mitad")
        return type(self).resultado


def _motor(monkeypatch: pytest.MonkeyPatch, *, resultado=None, revienta: bool = False):  # noqa: ANN202
    """Instala el motor falso y devuelve la clase, para poder inspeccionar sus llamadas."""
    import edecan_creative.redaccion as redaccion

    clase = type("MotorFalso", (_MotorFalso,), {"llamadas": []})
    clase.resultado = resultado
    clase.revienta = revienta
    monkeypatch.setattr(redaccion, "CrearPostLinkedInTool", clase)
    return clase


def _resultado(**data: Any):  # noqa: ANN202
    from edecan_core.tools.base import ToolResult

    # `content` a propósito con la pinta del mensaje INTERNO que el tool le devuelve al modelo
    # del chat: si algún día se cuela tal cual al chat de la persona, estos tests lo cazan.
    return ToolResult(
        content="Escribí el post 3 veces y ninguna versión pasó el control de calidad.",
        data=dict(data) or None,
    )


@dataclass
class _Entorno:
    """Todo lo que hace falta para correr `handle` sin Postgres, SQS ni red."""

    deps: Any
    repo: FakeRepo
    eventos: list[Any]
    tenant_id: uuid.UUID
    user_id: uuid.UUID

    def mensajes_al_usuario(self) -> list[str]:
        return [
            str(fila["content"].get("text") or "")
            for fila in self.repo.messages
            if fila["role"] == "assistant" and isinstance(fila["content"], dict)
        ]


def _entorno(monkeypatch: pytest.MonkeyPatch) -> _Entorno:
    repo = FakeRepo()
    eventos: list[Any] = []

    async def _notify(_deps, event):  # noqa: ANN001
        eventos.append(event)
        # El de verdad devuelve `UniversalNotificationResult`, y `durable` es lo que el
        # handler mira para saber si al menos quedó constancia en la actividad. Devolver
        # `None` acá haría creer que nunca llega nada y dispararía reintentos falsos.
        from edecan_worker.universal_notifications import UniversalNotificationResult

        return UniversalNotificationResult(
            durable=True, duplicate=False, push_enabled=True, pushed=1, push_failed=0
        )

    monkeypatch.setattr(handler, "SqlRepo", lambda session: repo)
    monkeypatch.setattr(handler, "notify_important_event", _notify)
    # `FakeSession` y no una sesión pelada: sin `execute`, `_persistir_borrador` falla siempre
    # y la card degrada a Copiar texto pase lo que pase -- o sea, los tests del botón medirían
    # el fallo de la fixture y no la regla que quieren fijar.
    return _Entorno(
        deps=_deps(FakeSession()),
        repo=repo,
        eventos=eventos,
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )


def _env(entorno: _Entorno, **payload: Any) -> JobEnvelope:
    """Un pedido hecho DESDE EL CHAT, que es el que activa la invariante.

    Lleva `conversation_id` porque el turno del chat que contesta "me pongo a escribir"
    siempre encola el suyo (`routers/conversations.py`): esa es la firma del pedido humano y
    la promesa que hay que cumplir. Los turnos del autopiloto no la traen, y tienen su propio
    helper (`_env_del_autopiloto`) porque su final correcto es OTRO.
    """
    return JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=entorno.tenant_id,
        type="create_linkedin_post",
        payload={
            "user_id": str(entorno.user_id),
            "conversation_id": str(uuid.uuid4()),
            "con_imagen": False,
            **payload,
        },
    )


def _env_del_autopiloto(entorno: _Entorno, **payload: Any) -> JobEnvelope:
    """Un turno sembrado por una automatización: sin conversación y marcado con `origen`.

    Espeja lo que encola `run_automation._delegate_create_linkedin_post`.
    """
    return JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=entorno.tenant_id,
        type="create_linkedin_post",
        payload={
            "user_id": str(entorno.user_id),
            "origen": "automatizacion",
            "con_imagen": False,
            **payload,
        },
    )


def _copy_en_la_franja() -> str:
    """Un copy que pasa el piso VIEJO del motor (80) y no llega al de entrega.

    Se construye a partir del piso real importado, no de un 118 escrito a mano: si mañana
    alguien mueve el número, este test sigue midiendo la franja y no un largo fijo que dejó
    de significar nada.
    """
    from edecan_creative.redaccion import MIN_COPY_ENTREGABLE_CHARS

    corto = "El ajuste de tasas encarece el crédito revolvente y el pago mínimo cubre menos."
    return corto.ljust(MIN_COPY_ENTREGABLE_CHARS - 1, ".")[: MIN_COPY_ENTREGABLE_CHARS - 1]


async def test_un_copy_de_la_franja_no_deja_al_usuario_en_silencio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EL FALLO, REPRODUCIDO. El motor devuelve un copy corto y el handler no lo entrega —
    hasta ahí, correcto. Lo que estaba mal era lo que pasaba después: nada.

    Ahora la persona recibe un mensaje en su chat. No una card rota, no un push mudo: una
    frase que explica que no hubo post y qué puede hacer.
    """
    entorno = _entorno(monkeypatch)
    _motor(monkeypatch, resultado=_resultado(copy=_copy_en_la_franja()))

    await handler.handle(_env(entorno, tema="Acme de Venezuela"), entorno.deps)

    mensajes = entorno.mensajes_al_usuario()
    assert len(mensajes) == 1, "el silencio es el peor final posible: siempre llega algo"
    assert "No pude escribirte el post" in mensajes[0]
    # Y se sabe CUÁL de los pedidos falló, que con varios en vuelta no es un detalle.
    assert "Acme de Venezuela" in mensajes[0]
    # No se guardó ningún borrador: no hay nada que publicar.
    assert entorno.repo.messages[0]["tool_calls"] is None


async def test_el_mensaje_explica_el_motivo_real_sin_jerga_interna(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "No conseguí fuente" y "lo intenté y no me salió" son cosas distintas, y la persona
    tiene derecho a saber cuál fue: una se arregla dándole una noticia concreta y la otra
    pidiendo otro ángulo.

    Y nada de jerga: quien lee esto pidió un post, no está depurando el motor. El `content`
    que devuelve el tool está escrito para el modelo del chat (nombra herramientas y
    parámetros) y no puede salir tal cual por este canal.
    """
    from edecan_creative.redaccion import FALLO_SIN_FUENTE

    entorno = _entorno(monkeypatch)
    _motor(monkeypatch, resultado=_resultado(fallo=FALLO_SIN_FUENTE))

    await handler.handle(_env(entorno, tema="la apertura económica"), entorno.deps)

    [mensaje] = entorno.mensajes_al_usuario()
    assert "fuente" in mensaje
    assert "ángulo más concreto" in mensaje
    for jerga in (
        "crear_post_linkedin",
        "crear_contenido_social",
        "configurar_perfil_social",
        "context_bank",
        "max_dias",
        "control de calidad",
    ):
        assert jerga not in mensaje, f"se coló jerga interna: {jerga}"


async def test_si_el_motor_revienta_tampoco_hay_silencio(monkeypatch: pytest.MonkeyPatch) -> None:
    """Una excepción inesperada también es silencio si se la deja escapar.

    El job se queda sin entregar igual que en el fallo original, y la persona vuelve a quedar
    esperando. Por eso el handler la atrapa, se lo cuenta, y no la re-lanza: reintentar el
    trabajo entero significaría o cinco posts o cinco silencios.
    """
    entorno = _entorno(monkeypatch)
    _motor(monkeypatch, revienta=True)

    await handler.handle(_env(entorno, tema="tasas de tarjetas"), entorno.deps)

    [mensaje] = entorno.mensajes_al_usuario()
    assert "No pude escribirte el post" in mensaje


async def test_el_aviso_va_al_chat_Y_ademas_manda_el_push_a_esa_conversacion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El mensaje es la explicación; el push es el golpecito en el hombro. Hacen falta los dos.

    Antes solo estaba el push, que dice "un trabajo necesita atención" y deja a la persona en
    la pestaña de actividad sin nada que leer. Ahora el push apunta a la MISMA conversación
    donde acaba de quedar la explicación, así tocarlo lleva directo al texto.
    """
    entorno = _entorno(monkeypatch)
    _motor(monkeypatch, resultado=_resultado())

    await handler.handle(_env(entorno), entorno.deps)

    assert entorno.mensajes_al_usuario(), "primero el mensaje"
    [evento] = entorno.eventos
    assert evento.kind == "work_failed"
    conversacion = entorno.repo.messages[0]["conversation_id"]
    assert evento.chat_id == conversacion


async def test_con_un_post_de_verdad_llega_la_card_y_no_el_aviso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El camino feliz sigue igual: card en el chat, borrador guardado y push de contenido.

    Va aquí y no en otro archivo porque es la otra mitad de la invariante -- "siempre llega
    algo" no puede cumplirse a costa de que el post bueno deje de llegar.
    """
    from edecan_creative.redaccion import MIN_COPY_ENTREGABLE_CHARS

    entorno = _entorno(monkeypatch)
    copy = (
        "El ajuste de tasas encarece el crédito revolvente para quien arrastra saldo del mes "
        "anterior, porque el pago mínimo cubre hoy menos capital que antes y el saldo crece."
    )
    assert len(copy) >= MIN_COPY_ENTREGABLE_CHARS
    _motor(
        monkeypatch,
        resultado=_resultado(copy=copy, visual={"kicker": "BANCA", "headline": "Tasas"}),
    )

    await handler.handle(_env(entorno, destino="acme"), entorno.deps)

    [fila] = entorno.repo.messages
    assert fila["content"]["text"].endswith("listo 👇")
    assert fila["content"]["presentation"], "el post viaja en la card, no suelto en el texto"
    [evento] = entorno.eventos
    assert evento.kind == "content_created"


async def test_el_piso_es_el_del_motor_no_uno_propio_de_este_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La causa raíz, fijada como contrato: acá no vive ningún número propio.

    Este archivo declaraba `_MIN_COPY_CHARS = 150` mientras el motor daba por bueno todo lo
    que pasara de 80. Nadie ponía los dos números uno al lado del otro, así que la franja
    entre ellos se tragaba posts enteros sin dejar rastro. Ahora hay UNO SOLO y se importa:
    un texto un carácter por debajo del piso del motor no se entrega, y uno justo en el piso
    sí. Si alguien vuelve a escribir un umbral local distinto, este test se cae.
    """
    from edecan_creative.redaccion import MIN_COPY_ENTREGABLE_CHARS

    justo = "a" * MIN_COPY_ENTREGABLE_CHARS
    entorno = _entorno(monkeypatch)
    _motor(monkeypatch, resultado=_resultado(copy=justo))
    await handler.handle(_env(entorno), entorno.deps)
    assert entorno.repo.messages[0]["content"]["presentation"], "lo que cumple el piso se entrega"

    uno_menos = "a" * (MIN_COPY_ENTREGABLE_CHARS - 1)
    entorno2 = _entorno(monkeypatch)
    _motor(monkeypatch, resultado=_resultado(copy=uno_menos))
    await handler.handle(_env(entorno2), entorno2.deps)
    assert "No pude escribirte el post" in entorno2.mensajes_al_usuario()[0]


# ---------------------------------------------------------------------------
# El BORRADOR SIN AUDITAR: llega, pero avisando y sin el botón que publica solo.
# ---------------------------------------------------------------------------
#
# El motor tiene un camino de rescate: si ningún intento pasó limpio, entrega el mejor
# borrador que existió con un aviso ("esto no pasó el control, revísalo"). Ese aviso vivía
# ÚNICAMENTE en `result.content` -- el canal que este handler ignora a propósito porque ahí el
# tool le habla al modelo del chat. El resultado neto era el peor posible para la página de una
# empresa: llegaba al teléfono un borrador que el auditor de HECHOS había vetado, con botón de
# "Aprobar y publicar" y sin una sola palabra de advertencia. A un toque de publicarse.


_COPY_LARGO = (
    "El ajuste de tasas encarece el crédito revolvente para quien arrastra saldo del mes "
    "anterior, porque el pago mínimo cubre hoy menos capital que antes y el saldo crece."
)
_AVISO = (
    "Ojo: el auditor de hechos no pudo sostener todo el borrador con la fuente (no se "
    "menciona en las fuentes). Lo entrego igual porque lo pediste, pero verifica los datos "
    "antes de publicarlo."
)


async def test_el_aviso_del_motor_se_le_dice_a_la_persona_no_se_pierde(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si el motor avisa que el borrador no salió limpio, ese aviso tiene que LEERSE."""
    entorno = _entorno(monkeypatch)
    _motor(
        monkeypatch,
        resultado=_resultado(copy=_COPY_LARGO, aviso=_AVISO, sin_auditar=True),
    )

    await handler.handle(_env(entorno, destino="acme"), entorno.deps)

    [texto] = entorno.mensajes_al_usuario()
    assert "verifica los datos antes de publicarlo" in texto
    assert entorno.repo.messages[0]["content"]["presentation"], "y el post igual llega"


async def test_un_borrador_que_el_auditor_no_valido_no_se_publica_de_un_toque(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La card de una PÁGINA publica en nombre de una empresa sin que nadie relea el texto.

    Con los hechos sin verificar, ese botón es justo lo que no debe existir: el perfil de esa
    clase de cuenta suele pedir lo contrario ("verificar la fuente antes de afirmar"). Degrada
    a Copiar texto -- no se le quita el post, se le quita el automatismo.
    """
    entorno = _entorno(monkeypatch)
    _motor(
        monkeypatch,
        resultado=_resultado(copy=_COPY_LARGO, aviso=_AVISO, sin_auditar=True),
    )

    await handler.handle(_env(entorno, destino="acme"), entorno.deps)

    [card] = entorno.repo.messages[0]["content"]["presentation"]
    assert [a["action"] for a in _acciones(card)] == ["copy_text"]


async def test_un_borrador_auditado_de_una_pagina_si_conserva_el_boton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El contrapeso del test anterior: sin bandera de "sin auditar", el botón sigue ahí.

    Un guard que se pasa de estricto le quita al dueño el atajo que pidió, y eso también es
    una regresión -- sólo que más silenciosa.
    """
    entorno = _entorno(monkeypatch)
    _motor(monkeypatch, resultado=_resultado(copy=_COPY_LARGO))

    await handler.handle(_env(entorno, destino="acme"), entorno.deps)

    [card] = entorno.repo.messages[0]["content"]["presentation"]
    assert [a["action"] for a in _acciones(card)] == ["approve_draft"]


# ---------------------------------------------------------------------------
# EL AUTOPILOTO no recibe disculpas: nadie le prometió nada.
# ---------------------------------------------------------------------------


async def test_un_turno_del_autopiloto_que_falla_no_le_escribe_al_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La invariante nace de una PROMESA, y al autopiloto nadie le prometió nada.

    Con tres slots al día, escribirle "no pude, dame un ángulo más concreto" cada vez que un
    turno se salta por falta de fuente fresca -- que es comportamiento NORMAL y documentado del
    motor -- le llena el chat de disculpas por posts que nunca pidió. El push genérico
    "Trabajo pendiente" tampoco: no hay conversación ni misión que abrir, y Actividad está
    vacía. Queda el log.
    """
    from edecan_creative.redaccion import FALLO_SIN_FUENTE

    entorno = _entorno(monkeypatch)
    _motor(monkeypatch, resultado=_resultado(fallo=FALLO_SIN_FUENTE))

    await handler.handle(_env_del_autopiloto(entorno, destino="acme"), entorno.deps)

    assert entorno.mensajes_al_usuario() == []
    assert entorno.eventos == []


async def test_el_autopiloto_si_entrega_el_post_cuando_le_sale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lo que NO se puede hacer es callarle también los aciertos: el post sembrado se entrega
    igual que el pedido a mano, en el hilo principal."""
    entorno = _entorno(monkeypatch)
    _motor(monkeypatch, resultado=_resultado(copy=_COPY_LARGO))

    await handler.handle(_env_del_autopiloto(entorno, destino="acme"), entorno.deps)

    assert entorno.repo.messages[0]["content"]["presentation"]
    [evento] = entorno.eventos
    assert evento.kind == "content_created"


async def test_el_mensaje_dice_para_cual_cuenta_era(monkeypatch: pytest.MonkeyPatch) -> None:
    """Con varias cuentas configuradas, "no pude escribirte el post" no dice CUÁL post.

    Y el identificador sale del payload, no de ninguna lista cableada acá: esto corre para
    cualquier instalación.
    """
    entorno = _entorno(monkeypatch)
    _motor(monkeypatch, resultado=_resultado())
    await handler.handle(_env(entorno, destino="acme"), entorno.deps)
    assert "«acme»" in entorno.mensajes_al_usuario()[0]

    entorno2 = _entorno(monkeypatch)
    _motor(monkeypatch, resultado=_resultado())
    await handler.handle(_env(entorno2, destino="personal"), entorno2.deps)
    assert "tu perfil" in entorno2.mensajes_al_usuario()[0]


async def test_un_post_demasiado_largo_dice_que_quedo_largo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El motivo más accionable que existe no puede terminar traducido al menos accionable.

    Cuando el post se escribió entero pero pasó el límite de la plataforma, decir "no me salió
    nada" es falso: sí salió, y lo que hace falta es una orden de una línea ("córtalo").
    """
    from edecan_creative.redaccion import FALLO_COPY_LARGO

    entorno = _entorno(monkeypatch)
    _motor(monkeypatch, resultado=_resultado(fallo=FALLO_COPY_LARGO))

    await handler.handle(_env(entorno, tema="tasas"), entorno.deps)

    [texto] = entorno.mensajes_al_usuario()
    assert "más largo de lo que LinkedIn deja publicar" in texto


# ---------------------------------------------------------------------------
# LA OTRA MITAD DE LA INVARIANTE: entregar también puede fallar.
# ---------------------------------------------------------------------------
#
# El post ya está escrito, la imagen generada y el borrador guardado -- y aun así la persona
# puede terminar sin nada, porque resolver la conversación, armar la card y escribir el mensaje
# son tres operaciones que fallan. Esa mitad corría SIN red mientras el aviso de "no hubo post"
# sí la tenía: el mismo `_resolver_conversacion`, la misma base de datos, y una excepción que
# escapaba de `handle` sin dejar una línea en el chat. Es el síntoma original por otra puerta.


class _RepoQueFallaAlEscribir(FakeRepo):
    """Espeja al `SqlRepo` real salvo que `add_message` revienta las primeras N veces.

    Modela lo que de verdad pasa en esta instalación: Postgres se reinicia, o la laptop se
    duerme a mitad del trabajo más largo del sistema (motor + imagen: entre uno y cuatro
    minutos).
    """

    def __init__(self, fallos: int = 99) -> None:
        super().__init__()
        self.fallos_restantes = fallos

    async def add_message(self, **kwargs: Any) -> dict[str, Any]:
        if self.fallos_restantes > 0:
            self.fallos_restantes -= 1
            raise RuntimeError("server closed the connection unexpectedly")
        return await super().add_message(**kwargs)


def _entorno_con_repo(monkeypatch: pytest.MonkeyPatch, repo: FakeRepo) -> _Entorno:
    entorno = _entorno(monkeypatch)
    monkeypatch.setattr(handler, "SqlRepo", lambda session: repo)
    return _Entorno(
        deps=entorno.deps,
        repo=repo,
        eventos=entorno.eventos,
        tenant_id=entorno.tenant_id,
        user_id=entorno.user_id,
    )


async def test_si_la_card_no_se_puede_escribir_el_post_llega_igual_en_texto_plano(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un post entregado en una burbuja fea es infinitamente mejor que un post que no llega.

    El primer intento (card) falla; el segundo va sin tarjeta contra el hilo principal, y ahí
    va el texto COMPLETO -- para que se pueda copiar aunque no haya botón que lo copie.
    """
    entorno = _entorno_con_repo(monkeypatch, _RepoQueFallaAlEscribir(fallos=1))
    _motor(monkeypatch, resultado=_resultado(copy=_COPY_LARGO))

    await handler.handle(_env(entorno, destino="acme"), entorno.deps)

    [texto] = entorno.mensajes_al_usuario()
    assert _COPY_LARGO in texto, "el post entero, para que se pueda copiar a mano"
    [evento] = entorno.eventos
    assert evento.kind == "content_created"


async def test_si_no_se_pudo_entregar_nada_la_cola_reintenta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cuando NADA le llegó a la persona, tragarse el fallo convierte un corte de treinta
    segundos en silencio permanente: el trabajo termina en `done` y nadie lo vuelve a mirar.

    Ahí -- y sólo ahí, porque no hay nada entregado que se pueda duplicar -- se deja escapar la
    excepción para que la cola reintente con su backoff.
    """
    entorno = _entorno_con_repo(monkeypatch, _RepoQueFallaAlEscribir())
    _motor(monkeypatch, resultado=_resultado(copy=_COPY_LARGO))

    with pytest.raises(RuntimeError):
        await handler.handle(_env(entorno, destino="acme"), entorno.deps)

    assert entorno.repo.messages == []


async def test_agotados_los_reintentos_el_trabajo_termina_en_vez_de_dar_vueltas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reintentar para siempre no es cumplir la invariante: es quemar el motor y la cuenta.

    Con los intentos gastados, el trabajo termina sin lanzar. Queda el `work_failed` en la
    actividad, que es la verdad -- el post existe pero no se pudo poner en el chat.
    """
    entorno = _entorno_con_repo(monkeypatch, _RepoQueFallaAlEscribir())
    _motor(monkeypatch, resultado=_resultado(copy=_COPY_LARGO))
    env = _env(entorno, destino="acme")
    env.attempt = handler._REINTENTOS_DE_COLA_ANTES_DE_RENDIRSE

    await handler.handle(env, entorno.deps)

    assert entorno.repo.messages == []
    [evento] = entorno.eventos
    assert evento.kind == "work_failed", "no se le dice 'contenido listo' a algo que no llegó"


async def test_si_ni_el_aviso_se_pudo_escribir_la_cola_tambien_reintenta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La misma regla del otro lado: no hubo post Y tampoco se pudo explicar por qué.

    El caso típico es la base de datos caída, que tumba los dos canales a la vez. Si además el
    aviso durable falla, no queda rastro de nada en ninguna parte.
    """

    async def _notify_muerto(_deps, _event):  # noqa: ANN001
        from edecan_worker.universal_notifications import UniversalNotificationResult

        return UniversalNotificationResult(
            durable=False, duplicate=False, push_enabled=False, pushed=0, push_failed=0
        )

    entorno = _entorno_con_repo(monkeypatch, _RepoQueFallaAlEscribir())
    monkeypatch.setattr(handler, "notify_important_event", _notify_muerto)
    _motor(monkeypatch, revienta=True)

    with pytest.raises(RuntimeError):
        await handler.handle(_env(entorno), entorno.deps)


# ---------------------------------------------------------------------------
# La imagen: un hipo transitorio no puede costar la foto, y perderla no se calla.
# Fallo real (02-ago-2026): un solo intento fallido de gpt-image-2 -- la misma
# llave respondió perfecto minutos después -- entregó la card pelada y sin una
# palabra; Alex, textual: "no me dio imagen. ¿Por qué?".
# ---------------------------------------------------------------------------

_COPY_LARGO_OK = (
    "El ajuste de tasas encarece el crédito revolvente para quien arrastra saldo del mes "
    "anterior, porque el pago mínimo cubre hoy menos capital que antes y el saldo crece."
)


def _con_imagenes(entorno: _Entorno, monkeypatch: pytest.MonkeyPatch, fallos: int) -> list[int]:
    """Prepara la instalación CON proveedor de imágenes y un generador que falla
    `fallos` veces antes de responder. Devuelve la lista de intentos registrados."""
    entorno.deps.settings.IMAGES_API_KEY = "sk-test"
    monkeypatch.setattr(handler, "_PAUSAS_IMAGEN_SEGUNDOS", (0.0, 0.0))
    intentos: list[int] = []

    async def _generar(settings, copy_text, visual, destino):  # noqa: ANN001
        intentos.append(len(intentos) + 1)
        if len(intentos) <= fallos:
            raise RuntimeError("hipo transitorio del proveedor de imágenes")
        return b"png-de-prueba"

    monkeypatch.setattr(handler, "_generar_imagen", _generar)

    async def _subir(ctx, *, data, filename, mime):  # noqa: ANN001
        return uuid.uuid4(), filename

    import edecan_creative._files as files_mod

    monkeypatch.setattr(files_mod, "subir_archivo", _subir)
    return intentos


async def test_un_hipo_transitorio_de_la_imagen_se_reintenta_y_la_foto_llega(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entorno = _entorno(monkeypatch)
    intentos = _con_imagenes(entorno, monkeypatch, fallos=1)
    _motor(monkeypatch, resultado=_resultado(copy=_COPY_LARGO_OK, visual={"headline": "Tasas"}))

    await handler.handle(_env(entorno, destino="acme", con_imagen=True), entorno.deps)

    assert intentos == [1, 2], "el segundo intento tenía que correr y bastar"
    [fila] = entorno.repo.messages
    assert "La imagen no salió" not in fila["content"]["text"]
    # La card lleva la imagen: el bloque social_draft trae su artifact.
    assert "image" in str(fila["content"]["presentation"]).lower()


async def test_si_la_imagen_muere_todos_los_intentos_se_le_dice_a_la_persona(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entorno = _entorno(monkeypatch)
    intentos = _con_imagenes(entorno, monkeypatch, fallos=99)
    _motor(monkeypatch, resultado=_resultado(copy=_COPY_LARGO_OK, visual={"headline": "Tasas"}))

    await handler.handle(_env(entorno, destino="acme", con_imagen=True), entorno.deps)

    assert len(intentos) == handler._REINTENTOS_IMAGEN
    [fila] = entorno.repo.messages
    # El post LLEGA (texto primero) y la pérdida de la imagen se dice, no se calla.
    assert "La imagen no salió" in fila["content"]["text"]
    assert fila["content"]["presentation"]


async def test_sin_proveedor_de_imagenes_no_hay_aviso_de_imagen_perdida(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """En una instalación SIN llave de imágenes, cada post sin foto es lo normal: el
    aviso sería ruido diario. Solo se avisa cuando la imagen se esperaba de verdad."""
    entorno = _entorno(monkeypatch)
    entorno.deps.settings.IMAGES_API_KEY = None
    _motor(monkeypatch, resultado=_resultado(copy=_COPY_LARGO_OK, visual={"headline": "Tasas"}))

    await handler.handle(_env(entorno, destino="acme", con_imagen=True), entorno.deps)

    [fila] = entorno.repo.messages
    assert "La imagen no salió" not in fila["content"]["text"]
