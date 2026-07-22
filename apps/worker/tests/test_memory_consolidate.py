"""Tests del job `memory_consolidate`: extracción vía LLM + clustering puro-Python."""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta

import edecan_worker.handlers.memory_consolidate as consolidate_module
from edecan_schemas import JobEnvelope
from edecan_worker.handlers.memory_consolidate import (
    _cosine_of_normalized,
    _normalize,
    _parsear_items_extraidos,
    _validar_item_extraido,
    cluster_duplicates,
)
from fakes import FakeRepo, make_deps


async def _use_platform_router_as_tenant_router(deps, monkeypatch) -> None:
    """`Deps.llm_router_for` (bring-your-own, WP-V3-02) ahora resuelve un
    `LLMRouter` REAL a partir de la config guardada del tenant — simular acá
    "el tenant conectó algo" con un vault falso arriesgaría una llamada de
    red real al extraer memorias, que es justo lo que estos tests NO deben
    hacer (usan `deps.llm_router`, en memoria, para poder aserts sin red).
    Este archivo no prueba la resolución bring-your-own en sí — eso ya lo
    cubre `apps/worker/tests/test_llm_por_tenant.py` — así que alcanza con
    monkeypatchear `llm_router_for` para que devuelva directo
    `deps.llm_router` (el fake que cada test ya arma), como si fuera el
    resultado ya resuelto de la config propia del tenant."""

    async def _fake_llm_router_for(tenant_id):
        return deps.llm_router

    monkeypatch.setattr(deps, "llm_router_for", _fake_llm_router_for)


def _unit_vector(angle_deg: float) -> list[float]:
    radians = math.radians(angle_deg)
    return [math.cos(radians), math.sin(radians)]


def test_normalize_produce_norma_unitaria() -> None:
    normalizado = _normalize([3.0, 4.0])
    norma = sum(x * x for x in normalizado) ** 0.5
    assert math.isclose(norma, 1.0, rel_tol=1e-9)


def test_normalize_vector_cero_no_falla() -> None:
    assert _normalize([0.0, 0.0]) == [0.0, 0.0]


def test_cosine_of_normalized_vectores_identicos() -> None:
    v = _normalize([1.0, 2.0, 3.0])
    assert math.isclose(_cosine_of_normalized(v, v), 1.0, rel_tol=1e-9)


def test_cosine_of_normalized_vectores_ortogonales() -> None:
    assert math.isclose(_cosine_of_normalized([1.0, 0.0], [0.0, 1.0]), 0.0, abs_tol=1e-12)


def test_cluster_duplicates_agrupa_cercanos_y_deja_fuera_los_lejanos() -> None:
    items = [
        {"embedding": _unit_vector(0)},  # 0
        {"embedding": _unit_vector(15)},  # 1: a 15° de 0 -> cos(15°) ≈ 0.966 > 0.92
        {"embedding": _unit_vector(90)},  # 2: ortogonal, sin pareja
    ]
    grupos = cluster_duplicates(items)
    assert grupos == [[0, 1]]


def test_cluster_duplicates_transitivo() -> None:
    # 0-1 a 10°, 1-2 a 10° (0-2 a 20°, cos(20°) ≈ 0.94 > 0.92 -> igual quedan
    # unidos aunque no fuera por transitividad; usamos 35° para forzar que
    # SOLO la transitividad los una: cos(35°) ≈ 0.819 < 0.92).
    items = [
        {"embedding": _unit_vector(0)},
        {"embedding": _unit_vector(17)},
        {"embedding": _unit_vector(35)},
    ]
    assert math.cos(math.radians(17)) > 0.92
    assert math.cos(math.radians(35)) < 0.92  # 0 y 2 NO son similares directamente
    grupos = cluster_duplicates(items)
    assert grupos == [[0, 1, 2]]  # pero quedan unidos por transitividad vía 1


def test_cluster_duplicates_sin_grupos() -> None:
    items = [{"embedding": _unit_vector(0)}, {"embedding": _unit_vector(90)}]
    assert cluster_duplicates(items) == []


async def test_handle_funde_duplicados_conservando_el_mas_antiguo_con_importancia_maxima(
    monkeypatch,
) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(consolidate_module, "SqlRepo", lambda session: fake_repo)

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    ahora = datetime.now(UTC)

    antiguo_id = uuid.uuid4()
    nuevo_id = uuid.uuid4()
    distinto_id = uuid.uuid4()

    fake_repo.memory_items[antiguo_id] = {
        "id": antiguo_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "embedding": _unit_vector(0),
        "importance": 0.3,
        "created_at": ahora - timedelta(days=10),
    }
    # "nuevo" es duplicado de "antiguo" (5° de diferencia) y más importante.
    fake_repo.memory_items[nuevo_id] = {
        "id": nuevo_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "embedding": _unit_vector(5),
        "importance": 0.9,
        "created_at": ahora,
    }
    # "distinto" está a 90° de los otros dos: no es duplicado de nadie.
    fake_repo.memory_items[distinto_id] = {
        "id": distinto_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "embedding": _unit_vector(90),
        "importance": 0.5,
        "created_at": ahora,
    }

    deps = make_deps()
    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="memory_consolidate",
        payload={"user_id": str(user_id)},
    )
    await consolidate_module.handle(env, deps)

    # sobrevive el más antiguo...
    assert antiguo_id in fake_repo.memory_items
    # ...pero con la importancia máxima del grupo fundido
    assert fake_repo.memory_items[antiguo_id]["importance"] == 0.9
    # el duplicado más nuevo se eliminó
    assert nuevo_id not in fake_repo.memory_items
    # el ítem sin pareja no se toca
    assert distinto_id in fake_repo.memory_items
    assert fake_repo.memory_items[distinto_id]["importance"] == 0.5


async def test_handle_sin_duplicados_no_borra_ni_actualiza_nada(monkeypatch) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(consolidate_module, "SqlRepo", lambda session: fake_repo)

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    item_id = uuid.uuid4()
    fake_repo.memory_items[item_id] = {
        "id": item_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "embedding": _unit_vector(0),
        "importance": 0.4,
        "created_at": datetime.now(UTC),
    }

    deps = make_deps()
    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="memory_consolidate",
        payload={"user_id": str(user_id)},
    )
    await consolidate_module.handle(env, deps)

    assert fake_repo.memory_items[item_id]["importance"] == 0.4
    assert item_id in fake_repo.memory_items


# ---------------------------------------------------------------------------
# `_parsear_items_extraidos` / `_validar_item_extraido` (fase 1, unidad pura)
# ---------------------------------------------------------------------------


def test_parsear_items_extraidos_json_valido() -> None:
    texto = '[{"kind": "fact", "content": "Vive en Bogotá.", "importance": 0.5}]'
    assert _parsear_items_extraidos(texto) == [
        {"kind": "fact", "content": "Vive en Bogotá.", "importance": 0.5}
    ]


def test_parsear_items_extraidos_despoja_bloque_de_codigo() -> None:
    texto = '```json\n[{"kind": "fact", "content": "x"}]\n```'
    assert _parsear_items_extraidos(texto) == [{"kind": "fact", "content": "x"}]


def test_parsear_items_extraidos_vacio() -> None:
    assert _parsear_items_extraidos("[]") == []


def test_parsear_items_extraidos_json_invalido_no_lanza() -> None:
    assert _parsear_items_extraidos("no soy JSON") == []


def test_parsear_items_extraidos_json_no_es_lista() -> None:
    assert _parsear_items_extraidos('{"kind": "fact"}') == []


def test_validar_item_extraido_valido() -> None:
    item = {"kind": "preference", "content": "Prefiere que le hablen de tú.", "importance": 0.6}
    resultado = _validar_item_extraido(item, fuente_default="conversación 2026-07-08")
    assert resultado == {
        "kind": "preference",
        "content": "Prefiere que le hablen de tú.",
        "importance": 0.6,
        "source": "conversación 2026-07-08",
    }


def test_validar_item_extraido_kind_invalido_se_descarta() -> None:
    item = {"kind": "opinion", "content": "algo"}
    assert _validar_item_extraido(item, fuente_default="x") is None


def test_validar_item_extraido_sin_content_se_descarta() -> None:
    assert _validar_item_extraido({"kind": "fact", "content": "   "}, fuente_default="x") is None
    assert _validar_item_extraido({"kind": "fact"}, fuente_default="x") is None


def test_validar_item_extraido_importance_fuera_de_rango_se_recorta() -> None:
    alto = _validar_item_extraido(
        {"kind": "fact", "content": "x", "importance": 5}, fuente_default="src"
    )
    bajo = _validar_item_extraido(
        {"kind": "fact", "content": "x", "importance": -1}, fuente_default="src"
    )
    assert alto is not None and alto["importance"] == 1.0
    assert bajo is not None and bajo["importance"] == 0.0


def test_validar_item_extraido_source_ausente_usa_default() -> None:
    resultado = _validar_item_extraido(
        {"kind": "fact", "content": "x", "source": "  "}, fuente_default="conversación hoy"
    )
    assert resultado is not None and resultado["source"] == "conversación hoy"


def test_validar_item_extraido_normaliza_reemplazos_sin_duplicados() -> None:
    anterior = str(uuid.uuid4())
    resultado = _validar_item_extraido(
        {
            "kind": "fact",
            "content": "DataCred está aprobada en ambas tiendas.",
            "replaces": [anterior, anterior, 123, ""],
        },
        fuente_default="conversación hoy",
    )
    assert resultado is not None
    assert resultado["replaces"] == [anterior]


# ---------------------------------------------------------------------------
# Fase 1 end-to-end: `handle()` extrae vía LLM antes de deduplicar
# ---------------------------------------------------------------------------


def _agregar_conversacion_con_mensaje(
    fake_repo: FakeRepo, *, tenant_id: uuid.UUID, user_id: uuid.UUID, texto_usuario: str
) -> None:
    conversation_id = uuid.uuid4()
    fake_repo.conversations[conversation_id] = {
        "id": conversation_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "title": "",
        "channel": "web",
    }
    fake_repo.messages.append(
        {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "conversation_id": conversation_id,
            "role": "user",
            "content": {"text": texto_usuario},
            "tool_calls": None,
            "tokens_in": 0,
            "tokens_out": 0,
        }
    )


async def test_handle_extrae_memoria_nueva_del_llm_y_la_inserta_con_embedding(
    monkeypatch,
) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(consolidate_module, "SqlRepo", lambda session: fake_repo)

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    fake_repo.tenants[tenant_id] = {"id": tenant_id, "plan_key": "hosted_pro"}
    _agregar_conversacion_con_mensaje(
        fake_repo,
        tenant_id=tenant_id,
        user_id=user_id,
        texto_usuario="Por cierto, prefiero que me hables de tú siempre.",
    )

    deps = make_deps()
    await _use_platform_router_as_tenant_router(deps, monkeypatch)
    deps.llm_router.provider.reply = (
        '[{"kind": "preference", "content": "Prefiere que le hablen de tú.", '
        '"importance": 0.7, "source": "conversación 2026-07-08"}]'
    )

    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="memory_consolidate",
        payload={"user_id": str(user_id)},
    )
    await consolidate_module.handle(env, deps)

    nuevos = [
        row
        for row in fake_repo.memory_items.values()
        if row["content"] == "Prefiere que le hablen de tú."
    ]
    assert len(nuevos) == 1
    assert nuevos[0]["kind"] == "preference"
    assert nuevos[0]["importance"] == 0.7
    assert nuevos[0]["embedding"] is not None  # embebido en batch por `FakeEmbedder`

    # se resolvió el alias "rapido" (extracción en background, no user-facing)
    assert deps.llm_router.resolved[0][0] == "rapido"

    # el consumo del LLM quedó registrado como uso, igual que `generate_content`
    assert any(evt["kind"] == "llm_tokens" for evt in fake_repo.usage_events)

    # un solo ítem extraído no tiene con qué formar arista
    assert fake_repo.memory_edges == []


async def test_handle_correccion_archiva_memoria_anterior_y_deja_solo_la_nueva_activa(
    monkeypatch,
) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(consolidate_module, "SqlRepo", lambda session: fake_repo)

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    memoria_anterior_id = uuid.uuid4()
    fake_repo.tenants[tenant_id] = {"id": tenant_id, "plan_key": "hosted_pro"}
    fake_repo.memory_items[memoria_anterior_id] = {
        "id": memoria_anterior_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "kind": "fact",
        "content": "DataCred está aprobada en App Store y pendiente en Google Play.",
        "importance": 0.7,
        "source": "importado",
        "embedding": _unit_vector(90),
        "created_at": datetime.now(UTC) - timedelta(days=1),
    }
    _agregar_conversacion_con_mensaje(
        fake_repo,
        tenant_id=tenant_id,
        user_id=user_id,
        texto_usuario="Corrige esto: DataCred ya está aprobada en ambas plataformas.",
    )

    deps = make_deps()
    await _use_platform_router_as_tenant_router(deps, monkeypatch)
    deps.llm_router.provider.reply = (
        '[{"kind":"fact","content":"DataCred está aprobada en App Store y Google Play.",'
        f'"importance":0.95,"replaces":["{memoria_anterior_id}"]}}]'
    )
    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="memory_consolidate",
        payload={"user_id": str(user_id)},
    )

    await consolidate_module.handle(env, deps)

    nuevas = [
        row
        for row in fake_repo.memory_items.values()
        if row.get("content") == "DataCred está aprobada en App Store y Google Play."
    ]
    assert len(nuevas) == 1
    anterior = fake_repo.memory_items[memoria_anterior_id]
    assert anterior["superseded_at"] is not None
    assert anterior["superseded_by"] == nuevas[0]["id"]
    activas = await fake_repo.list_memory_contents(tenant_id=tenant_id, user_id=user_id, limit=50)
    assert memoria_anterior_id not in {row["id"] for row in activas}
    assert nuevas[0]["id"] in {row["id"] for row in activas}


async def test_handle_ignora_id_de_reemplazo_que_no_pertenece_a_memorias_existentes(
    monkeypatch,
) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(consolidate_module, "SqlRepo", lambda session: fake_repo)
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    fake_repo.tenants[tenant_id] = {"id": tenant_id, "plan_key": "hosted_pro"}
    _agregar_conversacion_con_mensaje(
        fake_repo,
        tenant_id=tenant_id,
        user_id=user_id,
        texto_usuario="Corrige mi ciudad.",
    )
    deps = make_deps()
    await _use_platform_router_as_tenant_router(deps, monkeypatch)
    deps.llm_router.provider.reply = (
        f'[{{"kind":"fact","content":"Vive en Caracas.","replaces":["{uuid.uuid4()}"]}}]'
    )
    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="memory_consolidate",
        payload={"user_id": str(user_id)},
    )

    await consolidate_module.handle(env, deps)

    assert any(row.get("content") == "Vive en Caracas." for row in fake_repo.memory_items.values())
    assert all(row.get("superseded_at") is None for row in fake_repo.memory_items.values())


async def test_handle_enlaza_en_memory_edges_los_items_de_un_mismo_lote(monkeypatch) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(consolidate_module, "SqlRepo", lambda session: fake_repo)

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    fake_repo.tenants[tenant_id] = {"id": tenant_id, "plan_key": "hosted_pro"}
    _agregar_conversacion_con_mensaje(
        fake_repo,
        tenant_id=tenant_id,
        user_id=user_id,
        texto_usuario="Vivo en Medellín y prefiero que me hables de tú.",
    )

    deps = make_deps()
    await _use_platform_router_as_tenant_router(deps, monkeypatch)
    deps.llm_router.provider.reply = (
        '[{"kind": "fact", "content": "Vive en Medellín.", "importance": 0.5}, '
        '{"kind": "preference", "content": "Prefiere que le hablen de tú.", "importance": 0.7}]'
    )

    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="memory_consolidate",
        payload={"user_id": str(user_id)},
    )
    await consolidate_module.handle(env, deps)

    assert len(fake_repo.memory_items) == 2
    id_hecho, id_preferencia = (
        row["id"] for row in sorted(fake_repo.memory_items.values(), key=lambda row: row["content"])
    )

    # se enlazan en AMBOS sentidos (`neighbors()` solo resuelve salientes)
    assert len(fake_repo.memory_edges) == 2
    for edge in fake_repo.memory_edges:
        assert edge["tenant_id"] == tenant_id
        assert edge["relation"] == "extraido_junto_con"
        assert {edge["src_id"], edge["dst_id"]} == {id_hecho, id_preferencia}
    pares = {(edge["src_id"], edge["dst_id"]) for edge in fake_repo.memory_edges}
    assert pares == {(id_hecho, id_preferencia), (id_preferencia, id_hecho)}


async def test_handle_no_llama_al_llm_si_no_hay_mensajes_recientes(monkeypatch) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(consolidate_module, "SqlRepo", lambda session: fake_repo)

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    deps = make_deps()

    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="memory_consolidate",
        payload={"user_id": str(user_id)},
    )
    await consolidate_module.handle(env, deps)

    assert deps.llm_router.provider.requests == []
    assert fake_repo.memory_items == {}


async def test_handle_respeta_memoria_desactivada(monkeypatch) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(consolidate_module, "SqlRepo", lambda session: fake_repo)

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    fake_repo.personas[(tenant_id, user_id)] = {"memoria_activada": False}
    _agregar_conversacion_con_mensaje(
        fake_repo, tenant_id=tenant_id, user_id=user_id, texto_usuario="Vivo en Medellín."
    )

    deps = make_deps()
    deps.llm_router.provider.reply = '[{"kind": "fact", "content": "Vive en Medellín."}]'

    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="memory_consolidate",
        payload={"user_id": str(user_id)},
    )
    await consolidate_module.handle(env, deps)

    assert deps.llm_router.provider.requests == []
    assert fake_repo.memory_items == {}


async def test_handle_json_invalido_del_llm_no_tumba_el_job_ni_inserta_nada(monkeypatch) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(consolidate_module, "SqlRepo", lambda session: fake_repo)

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    _agregar_conversacion_con_mensaje(
        fake_repo, tenant_id=tenant_id, user_id=user_id, texto_usuario="hola"
    )

    deps = make_deps()
    deps.llm_router.provider.reply = "esto no es JSON válido"

    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="memory_consolidate",
        payload={"user_id": str(user_id)},
    )
    await consolidate_module.handle(env, deps)  # no debe lanzar

    assert fake_repo.memory_items == {}
