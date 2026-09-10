from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from edecan_llm.base import ChatMessage

from edecan_api.chat_context import (
    ChatContextLimits,
    build_contextual_history,
    compactar_historial,
    resumen_llm_hilo_anterior,
)


def _limits(**overrides):
    base = dict(
        enabled=True,
        recent_messages=2,
        max_messages=10,
        max_chars=4_000,
        cross_chat_enabled=True,
        cross_chat_conversations=4,
        cross_chat_messages_per_conversation=3,
        cross_chat_max_chars=4_000,
    )
    base.update(overrides)
    return ChatContextLimits(**base)


def _hilo_sintetico() -> list[ChatMessage]:
    return [
        ChatMessage(
            role="user",
            content="Quiero organizar el contenido de LinkedIn de la empresa.",
        ),
        ChatMessage(
            role="assistant",
            content="Armé un calendario editorial con cuatro posts por semana.",
        ),
        ChatMessage(
            role="user",
            content="Perfecto. Quedó pendiente revisar el copy del post de la tarjeta nueva.",
        ),
        ChatMessage(role="assistant", content="Anotado, lo reviso hoy."),
        ChatMessage(role="user", content="¿Cómo vamos con eso?"),
        ChatMessage(
            role="assistant",
            content="El copy ya está listo, te lo dejo acá abajo.",
        ),
    ]


class _FakeRouter:
    def __init__(
        self,
        texto: str = "Resumen generado por el modelo barato.",
        *,
        falla: bool = False,
    ):
        self.texto = texto
        self.falla = falla
        self.llamadas: list[tuple] = []

    async def complete(self, alias, tenant_flags, req):
        self.llamadas.append((alias, tenant_flags, req))
        if self.falla:
            raise RuntimeError("proveedor caído")
        return SimpleNamespace(text=self.texto)


def test_context_pack_keeps_recent_tail_and_adds_previous_chat_context() -> None:
    previous_chat = uuid.uuid4()
    history = [
        {"role": "user", "content": {"text": "Mensaje antiguo sobre Acme"}},
        {"role": "assistant", "content": {"text": "Respuesta antigua"}},
        {"role": "user", "content": {"text": "Mensaje reciente"}},
        {"role": "assistant", "content": {"text": "Respuesta reciente"}},
    ]
    cross = [
        {
            "conversation_id": previous_chat,
            "conversation_title": "Estrategia Acme",
            "conversation_updated_at": datetime(2026, 7, 28, tzinfo=UTC),
            "role": "user",
            "content": {"text": "Acme ya está aprobada en iOS y Android."},
        }
    ]

    packed = build_contextual_history(
        current_rows=history,
        cross_chat_rows=cross,
        limits=_limits(),
    )

    assert packed[0].role == "system"
    assert "[Resumen del hilo anterior]" in packed[0].content
    assert "Mensaje antiguo sobre Acme" in packed[0].content
    assert "Estrategia Acme" in packed[0].content
    assert "aprobada en iOS y Android" in packed[0].content
    assert [message.content for message in packed[-2:]] == [
        "Mensaje reciente",
        "Respuesta reciente",
    ]


def test_context_pack_can_be_disabled() -> None:
    packed = build_contextual_history(
        current_rows=[
            {"role": "user", "content": {"text": "A"}},
            {"role": "assistant", "content": {"text": "B"}},
            {"role": "user", "content": {"text": "C"}},
        ],
        cross_chat_rows=[
            {
                "conversation_id": uuid.uuid4(),
                "conversation_title": "No debe aparecer",
                "role": "user",
                "content": {"text": "Texto externo"},
            }
        ],
        limits=_limits(enabled=False, recent_messages=2),
    )

    assert [message.content for message in packed] == ["B", "C"]


async def test_compactar_historial_resume_lo_viejo_y_conserva_la_cola() -> None:
    pedido_viejo = ". ".join(f"Frase número {i} del pedido viejo" for i in range(30)) + "."
    respuesta_vieja = ". ".join(f"Detalle {i} de la respuesta vieja" for i in range(30)) + "."
    hilo = [
        ChatMessage(role="user", content=pedido_viejo),
        ChatMessage(role="assistant", content=respuesta_vieja),
        ChatMessage(role="user", content="¿Cómo vamos con eso?"),
        ChatMessage(role="assistant", content="El copy ya está listo, te lo dejo acá abajo."),
    ]
    # El tope alcanza exacto para los DOS últimos mensajes: los dos anteriores
    # quedan "viejos" y deben condensarse en el resumen.
    tope = sum(len(m.content or "") for m in hilo[-2:])

    resumen, recientes = await compactar_historial(hilo, tope)

    assert [m.content for m in recientes] == [m.content for m in hilo[-2:]]
    assert "Usuario:" in resumen
    assert "Edecán:" in resumen
    assert "Frase número 0 del pedido viejo." in resumen
    assert "Frase número 15" not in resumen
    assert "Frase número 29" in resumen
    assert len(resumen) < sum(len(m.content or "") for m in hilo[:-2])


async def test_compactar_historial_hilo_corto_no_resume() -> None:
    hilo = _hilo_sintetico()[:2]
    tope = sum(len(m.content or "") for m in hilo)

    resumen, recientes = await compactar_historial(hilo, tope)

    assert resumen == ""
    assert [m.content for m in recientes] == [m.content for m in hilo]


async def test_compactar_historial_vacio() -> None:
    resumen, recientes = await compactar_historial([], 500)
    assert resumen == ""
    assert recientes == []


async def test_compactar_historial_fallback_preserva_pendientes_cortos() -> None:
    hilo = [
        ChatMessage(
            role="user",
            content=(
                "Necesito que organices todo el contenido. Hay una estrategia muy larga. "
                "Muchos detalles. Más detalles. Y todavía más detalles sobre la marca."
            ),
        ),
        ChatMessage(role="assistant", content="Ok, quedó pendiente el informe de junio."),
        ChatMessage(role="user", content="Hola"),
    ]

    resumen, recientes = await compactar_historial(hilo, len("Hola"))

    assert [m.content for m in recientes] == ["Hola"]
    assert "pendiente el informe de junio" in resumen


async def test_compactar_historial_usa_alias_rapido_y_cachea_una_sola_llamada() -> None:
    router = _FakeRouter()
    hilo = _hilo_sintetico()
    tope = sum(len(m.content or "") for m in hilo[-2:])

    resumen1, _ = await compactar_historial(hilo, tope, llm_router=router)
    resumen2, _ = await compactar_historial(hilo, tope, llm_router=router)

    assert resumen1 == resumen2 == router.texto
    assert len(router.llamadas) == 1
    alias, flags, req = router.llamadas[0]
    assert alias == "rapido"
    assert flags == {}
    assert req.model == "rapido"


async def test_compactar_historial_router_que_falla_degrada_sin_lanzar() -> None:
    router = _FakeRouter(falla=True)
    hilo = _hilo_sintetico()
    # Con tope=60 solo cabe el último mensaje; los cinco anteriores deben
    # resumirse igual, aunque el router esté caído (nunca lanza).
    resumen, recientes = await compactar_historial(hilo, 60, llm_router=router)

    assert [m.content for m in recientes] == [m.content for m in hilo[-1:]]
    assert "Usuario:" in resumen
    assert len(router.llamadas) == 1


async def test_compactar_historial_resumen_llm_nunca_excede_el_limite() -> None:
    router = _FakeRouter(texto="x" * 20_000)
    resumen, _ = await compactar_historial(_hilo_sintetico(), 60, llm_router=router)
    assert len(resumen) <= 4_000


def test_context_pack_resume_el_hilo_largo_y_deja_la_cola_verbatim() -> None:
    history = [
        {"role": "user", "content": {"text": f"Pedido antiguo número {i} ya resuelto."}}
        for i in range(8)
    ]
    history += [
        {"role": "user", "content": {"text": "Mensaje reciente"}},
        {"role": "assistant", "content": {"text": "Respuesta reciente"}},
    ]

    packed = build_contextual_history(
        current_rows=history,
        cross_chat_rows=[],
        limits=_limits(),
    )

    assert packed[0].role == "system"
    assert "[Resumen del hilo anterior]" in packed[0].content
    assert "Pedido antiguo número 0" in packed[0].content
    assert [message.content for message in packed[-2:]] == [
        "Mensaje reciente",
        "Respuesta reciente",
    ]
    assert "Mensaje reciente" not in packed[0].content


# ---------------------------------------------------------------------------
# Inyección del resumen LLM precalculado (`current_summary`) y su brazo async
# (`resumen_llm_hilo_anterior`): el contrato sync de `build_contextual_history`
# no cambia para el resto de los llamadores.
# ---------------------------------------------------------------------------


def _rows_largos(cantidad: int = 6, *, base: str = "detalle viejo") -> list[dict]:
    """Filas tipo repo con textos largos (~1.7k chars c/u) para forzar compactación."""
    return [
        {
            "role": "user" if i % 2 == 0 else "assistant",
            "content": {"text": f"{base} número {i}. " + ("palabras de contexto " * 80)},
        }
        for i in range(cantidad)
    ]


def test_context_pack_inyecta_resumen_precalculado_y_salta_el_determinista() -> None:
    history = [
        {"role": "user", "content": {"text": f"Pedido antiguo {i}"}} for i in range(6)
    ]

    packed = build_contextual_history(
        current_rows=history,
        cross_chat_rows=[],
        limits=_limits(recent_messages=2),
        current_summary="Resumen del modelo barato: quedó pendiente X.",
    )

    assert packed[0].role == "system"
    assert "[Resumen del hilo anterior] Resumen del modelo barato: quedó pendiente X." in (
        packed[0].content
    )
    assert "Pedido antiguo 0" not in packed[0].content
    assert [message.content for message in packed[-2:]] == [
        "Pedido antiguo 4",
        "Pedido antiguo 5",
    ]


def test_context_pack_resumen_precalculado_vacio_usa_recorte_determinista() -> None:
    history = [
        {"role": "user", "content": {"text": f"Pedido antiguo {i}"}} for i in range(6)
    ]

    for resumen in (None, "", "   "):
        packed = build_contextual_history(
            current_rows=history,
            cross_chat_rows=[],
            limits=_limits(recent_messages=2),
            current_summary=resumen,
        )
        assert packed[0].role == "system"
        assert "Pedido antiguo 0" in packed[0].content


async def test_resumen_llm_hilo_anterior_con_router_resume_la_parte_vieja() -> None:
    router = _FakeRouter(texto="Resumen LLM: quedó pendiente el informe de junio.")
    rows = _rows_largos(base="resume una vez viejo")

    resumen = await resumen_llm_hilo_anterior(
        rows, _limits(recent_messages=2), llm_router=router
    )

    assert resumen == router.texto
    assert len(router.llamadas) == 1
    alias, flags, req = router.llamadas[0]
    assert alias == "rapido"
    assert flags == {}
    assert req.model == "rapido"


async def test_resumen_llm_hilo_anterior_hilo_corto_no_llama_al_modelo() -> None:
    router = _FakeRouter()
    # Con recent=2 queda UN solo mensaje viejo: cabe en el tope, no se llama al modelo.
    rows = _rows_largos(3, base="hilo corto no llama")

    resumen = await resumen_llm_hilo_anterior(
        rows, _limits(recent_messages=2), llm_router=router
    )

    assert resumen == ""
    assert router.llamadas == []


async def test_resumen_llm_hilo_anterior_sin_mensajes_viejos_devuelve_vacio() -> None:
    router = _FakeRouter()
    resumen = await resumen_llm_hilo_anterior(
        _rows_largos(2, base="sin mensajes viejos"), _limits(recent_messages=2), llm_router=router
    )
    assert resumen == ""
    assert router.llamadas == []


async def test_resumen_llm_hilo_anterior_sin_router_devuelve_vacio() -> None:
    resumen = await resumen_llm_hilo_anterior(
        _rows_largos(base="sin router"), _limits(recent_messages=2), llm_router=None
    )
    assert resumen == ""


async def test_resumen_llm_hilo_anterior_contexto_deshabilitado_devuelve_vacio() -> None:
    router = _FakeRouter()
    resumen = await resumen_llm_hilo_anterior(
        _rows_largos(base="contexto deshabilitado"),
        _limits(enabled=False, recent_messages=2),
        llm_router=router,
    )
    assert resumen == ""
    assert router.llamadas == []


async def test_resumen_llm_hilo_anterior_router_que_falla_devuelve_vacio() -> None:
    router = _FakeRouter(falla=True)
    resumen = await resumen_llm_hilo_anterior(
        _rows_largos(base="router que falla"), _limits(recent_messages=2), llm_router=router
    )
    assert resumen == ""

def test_historia_larga_viaja_solo_cola_reciente_mas_resumen() -> None:
    """Política de costos (6-sep): una historia de 30 mensajes NO viaja entera
    al turno — solo la cola reciente (20) + el resumen del hilo viejo."""
    rows = [
        {"id": uuid.uuid4(), "role": "assistant" if i % 2 else "user",
         "content": {"text": f"mensaje {i}"},
         "tool_calls": None, "created_at": datetime(2026, 9, 6, tzinfo=UTC)}
        for i in range(30)
    ]
    limits = ChatContextLimits(
        enabled=True,
        recent_messages=20,
        max_messages=200,
        max_chars=80_000,
        cross_chat_enabled=False,
        cross_chat_conversations=0,
        cross_chat_messages_per_conversation=0,
        cross_chat_max_chars=0,
    )
    resumen = "El hilo anterior hablaba de la auditoría de los bots."
    history = build_contextual_history(
        current_rows=rows, cross_chat_rows=[], limits=limits, current_summary=resumen
    )

    assert history[0].role == "system"
    assert "hilo anterior" in history[0].content.lower()
    assert "auditoría" in history[0].content.lower()
    textos_crudos = [m.content for m in history[1:] if m.role in ("user", "assistant")]
    assert len(textos_crudos) == 20
    assert "mensaje 29" in textos_crudos[-1]
    assert "mensaje 0" not in textos_crudos
