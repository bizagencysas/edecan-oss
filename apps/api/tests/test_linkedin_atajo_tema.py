"""El atajo de LinkedIn entiende el tema COMO LO ESCRIBE la gente, y los turnos de
seguimiento no se escapan al agente.

Regresión del fallo real que el dueño describió como "Edecán tiene 2 lenguajes":

- "créame un post de linkedin sobre: X" (con dos puntos, que es como él lo escribe) perdía
  el tema entero -- `\\s+` exigía un espacio pegado a "sobre" -- y el motor caía a su
  rotación editorial: post de OTRA cosa, imagen de OTRA cosa.
- "créame un post de linkedin sobre la multa de la SIC a Rappi" extraía "la SIC a Rappi":
  con "sobre" y "de" en la misma alternación, ganaba el conector que apareciera primero en
  la frase, no el más específico.
- Un mensaje suelto "sobre: X" (el seguimiento natural del pedido) no disparaba NINGUNA de
  las ramas deterministas y caía al agente genérico -- el único camino donde un LLM libre
  decide qué tool llamar --, de donde salían los posts con otro redactor y otra imagen.
- La card de destino permite texto libre, pero solo se entendía la frase exacta del botón:
  contestar "organization" a mano también caía al agente.

Estos tests fijan las cuatro correcciones. Son unitarios a propósito (funciones puras del
router): el viaje completo por SSE ya lo cubren `test_conversations.py` y
`test_pregunta_pendiente.py`.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from edecan_llm.base import ChatMessage

from edecan_api.routers.conversations import (
    _destino_de_respuesta_libre,
    _destino_del_pedido_en_historial,
    _es_pedido_directo_de_post_linkedin,
    _es_seguimiento_de_tema_linkedin,
    _extraer_tema_de_post_linkedin,
    _extraer_tema_del_historial_linkedin,
    _tema_de_seguimiento,
)

# ---------------------------------------------------------------------------
# Extracción de tema del propio pedido
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mensaje", "tema"),
    [
        # El caso EXACTO del reporte: dos puntos tras "sobre".
        (
            "créame un post de linkedin sobre: la multa de la SIC a Rappi",
            "la multa de la SIC a Rappi",
        ),
        # El conector fuerte gana aunque un "de" aparezca antes en la frase.
        (
            "créame un post de linkedin sobre la multa de la SIC a Rappi",
            "la multa de la SIC a Rappi",
        ),
        (
            "créame un post de linkedin sobre la inteligencia artificial",
            "la inteligencia artificial",
        ),
        (
            "post de linkedin sobre el tema: tasas de interés en Colombia",
            "tasas de interés en Colombia",
        ),
        (
            "hazme una publicación de linkedin acerca de los despidos en tech",
            "los despidos en tech",
        ),
        # Comillas alrededor del tema no viajan dentro del tema.
        ("créame un post de linkedin sobre 'el cierre de fondos'", "el cierre de fondos"),
        # El conector débil ("de X") sigue funcionando cuando no hay fuerte.
        (
            "créame un post para linkedin del aumento del salario mínimo",
            "aumento del salario mínimo",
        ),
        # Sin tema -> None (el motor rota su calendario, nunca inventa).
        ("créame un post de linkedin", None),
    ],
)
def test_extraer_tema_del_pedido(mensaje: str, tema: str | None) -> None:
    assert _es_pedido_directo_de_post_linkedin(mensaje)
    assert _extraer_tema_de_post_linkedin(mensaje) == tema


# ---------------------------------------------------------------------------
# Seguimiento "sobre: X" como mensaje suelto
# ---------------------------------------------------------------------------


def _historial(*mensajes: tuple[str, str]) -> list[ChatMessage]:
    return [ChatMessage(role=rol, content=texto) for rol, texto in mensajes]


@pytest.mark.parametrize(
    ("mensaje", "tema"),
    [
        ("sobre: la multa de la SIC a Rappi", "la multa de la SIC a Rappi"),
        ("sobre la ronda de Duppla", "la ronda de Duppla"),
        ("Acerca de: los aranceles nuevos.", "los aranceles nuevos"),
        ("tema: crédito en Venezuela", "crédito en Venezuela"),
        # Una pregunta no es un tema.
        ("¿sobre qué lo escribiste?", None),
        # Un mensaje que solo EMPIEZA con "sobre" pero sigue conversando tampoco
        # debe robarse el turno si trae interrogación.
        ("sobre eso, ¿qué opinas tú?", None),
        # "sobre todo" es muletilla adverbial, no un encargo de tema.
        ("Sobre todo me gustó el final del post", None),
        # Anáfora conversacional (hallazgo del panel): se refiere a lo ya hablado,
        # no encarga un tema nuevo. Sin esta exclusión, la frase entera se volvía
        # el "tema" y el tema real del pedido se perdía.
        ("Sobre eso, ponlo en mi personal", None),
        ("sobre esto que hablamos, mejor mañana", None),
        ("Sobre lo de la reunión, ya confirmé", None),
    ],
)
def test_tema_de_seguimiento(mensaje: str, tema: str | None) -> None:
    assert _tema_de_seguimiento(mensaje) == tema


def test_seguimiento_exige_pedido_reciente() -> None:
    con_pedido = _historial(
        ("user", "créame un post de linkedin"),
        ("assistant", "¿Con la voz de cuál de tus cuentas lo escribo?"),
    )
    assert (
        _es_seguimiento_de_tema_linkedin("sobre: la multa de la SIC a Rappi", con_pedido)
        == "la multa de la SIC a Rappi"
    )

    sin_pedido = _historial(
        ("user", "¿cómo estuvo el clima hoy?"),
        ("assistant", "Soleado."),
    )
    assert _es_seguimiento_de_tema_linkedin("sobre: la multa de la SIC a Rappi", sin_pedido) is None


def test_seguimiento_no_mira_pedidos_viejos() -> None:
    # El pedido quedó a más de `_VENTANA_SEGUIMIENTO_MENSAJES` (=2) mensajes de usuario
    # de distancia: "sobre X" ya no es un seguimiento, es una frase suelta. Hallazgo del
    # panel: con ventana 4, "Sobre el tema de mañana, no voy a poder ir a la reunión"
    # tres mensajes después de un pedido resuelto generaba un post espurio.
    viejo = _historial(
        ("user", "créame un post de linkedin"),
        ("user", "gracias"),
        ("user", "agenda una llamada con Pedro"),
    )
    assert (
        _es_seguimiento_de_tema_linkedin(
            "Sobre el tema de mañana, no voy a poder ir a la reunión", viejo
        )
        is None
    )


def test_seguimiento_ignora_mensajes_de_boton_en_la_ventana() -> None:
    # El flujo más normal: pedido → botón de la card ("Escríbelo con la voz de...") →
    # "sobre: X". El mensaje del botón lo escribe el botón, no la persona: no puede
    # consumir la ventana de recencia y dejar el pedido fuera de alcance.
    historia = _historial(
        ("user", "créame un post de linkedin"),
        ("assistant", "¿Con la voz de cuál de tus cuentas lo escribo?"),
        ("user", "Escríbelo con la voz de 'organization'."),
        ("assistant", "Listo, me pongo a escribir tu post."),
        ("user", "mejor espera"),
    )
    assert (
        _es_seguimiento_de_tema_linkedin("sobre: la multa de la SIC", historia)
        == "la multa de la SIC"
    )


def test_historial_recupera_tema_de_seguimiento_y_de_pedido() -> None:
    # Caso 1: el tema vive en el pedido original (con los dos puntos del reporte).
    historia = _historial(
        ("user", "créame un post de linkedin sobre: la multa de la SIC a Rappi"),
        ("assistant", "¿Con la voz de cuál de tus cuentas lo escribo?"),
    )
    assert _extraer_tema_del_historial_linkedin(historia) == "la multa de la SIC a Rappi"

    # Caso 2: el pedido no traía tema; llegó después como seguimiento suelto.
    historia = _historial(
        ("user", "créame un post de linkedin"),
        ("assistant", "¿Con la voz de cuál de tus cuentas lo escribo?"),
        ("user", "sobre: el cierre de fondos en LatAm"),
        ("assistant", "Listo, me pongo a escribir tu post."),
    )
    assert _extraer_tema_del_historial_linkedin(historia) == "el cierre de fondos en LatAm"


def test_historial_no_resucita_seguimientos_viejos_sin_pedido_cerca() -> None:
    # Hallazgo del panel: un "sobre: X" perdido en la conversación, SIN ningún pedido de
    # post cerca, no puede resucitar como tema del post de hoy. Solo cuenta un
    # seguimiento que en su momento fue seguimiento de verdad.
    historia = _historial(
        ("user", "sobre: mi cita con el dentista de la próxima semana"),
        ("assistant", "Anotado."),
        ("user", "¿cómo va el clima en Bogotá?"),
        ("assistant", "Soleado."),
        ("user", "hazme algo bonito para mi cuenta profesional"),
        ("assistant", "¿Con la voz de cuál de tus cuentas lo escribo?"),
    )
    assert _extraer_tema_del_historial_linkedin(historia) is None


def test_destino_del_pedido_en_historial() -> None:
    historia = _historial(
        ("user", "créame un post de Acme"),
        ("assistant", "Voy con eso."),
    )
    assert _destino_del_pedido_en_historial(historia) == "organization"
    assert _destino_del_pedido_en_historial(_historial(("user", "hola"))) is None


# ---------------------------------------------------------------------------
# Respuesta LIBRE a la card de destino
# ---------------------------------------------------------------------------


class _CtxSinSesion:
    session = None


def _fila_con_pregunta_de(nombre: str) -> dict[str, Any]:
    return {
        "role": "assistant",
        "tool_calls": [
            {
                "type": "tool_end",
                "tool_call_id": f"call-{uuid.uuid4().hex[:6]}",
                "name": nombre,
                "result_preview": "…",
                "blocks": [
                    {
                        "schema_version": 1,
                        "type": "question",
                        "question": "¿Con la voz de cuál de tus cuentas lo escribo?",
                        "header": "Destino",
                        "options": [{"label": "Personal"}, {"label": "Acme"}],
                        "multi_select": False,
                        "allow_free_text": True,
                    }
                ],
            }
        ],
    }


@pytest.mark.anyio
async def test_destino_libre_solo_con_card_abierta() -> None:
    ctx = _CtxSinSesion()
    filas_con_card = [_fila_con_pregunta_de("crear_post_linkedin")]

    # "organization" y "en mi perfil personal" se entienden sin tocar el botón
    # (resueltos por `_destino_desde_el_texto`, sin necesidad de sesión).
    assert await _destino_de_respuesta_libre(ctx, "organization", filas_con_card) == "organization"
    assert (
        await _destino_de_respuesta_libre(ctx, "en mi perfil personal", filas_con_card)
        == "personal"
    )

    # Sin card abierta, el mismo mensaje NO es una respuesta de destino.
    assert await _destino_de_respuesta_libre(ctx, "organization", []) is None
    otra_tool = [_fila_con_pregunta_de("configurar_credencial")]
    assert await _destino_de_respuesta_libre(ctx, "organization", otra_tool) is None

    # Un mensaje largo es otra conversación aunque nombre la cuenta de pasada.
    parrafo = (
        "oye, aparte de lo de organization, " + "recuérdame revisar el contrato de mañana " * 3
    )
    assert await _destino_de_respuesta_libre(ctx, parrafo, filas_con_card) is None


@pytest.mark.anyio
async def test_destino_libre_exige_palabra_completa(monkeypatch: pytest.MonkeyPatch) -> None:
    # Hallazgo del panel: con substring plano, un destino de nombre corto ("ana")
    # matcheaba dentro de "mañana" y el post salía por la cuenta equivocada.
    import edecan_creative.social as social_module

    async def _destinos_falsos(ctx: object, platform: str) -> list[dict[str, str]]:
        return [{"id": "ana", "label": "Ana"}, {"id": "personal", "label": "Personal"}]

    monkeypatch.setattr(social_module, "destinos_configurados", _destinos_falsos)

    class _CtxConSesion:
        session = object()

    ctx = _CtxConSesion()
    filas = [_fila_con_pregunta_de("crear_post_linkedin")]
    assert await _destino_de_respuesta_libre(ctx, "que quede para mañana", filas) is None
    assert await _destino_de_respuesta_libre(ctx, "ponlo en la de Ana", filas) == "ana"
