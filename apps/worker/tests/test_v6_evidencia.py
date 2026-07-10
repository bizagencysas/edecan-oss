"""Barrido dedicado del patrón de bug (b) de v5 (evidencia legal/cumplimiento vs
rollback de sesión) sobre los 5 handlers de `apps/worker/edecan_worker/handlers/`
que este paquete de trabajo (WP-V6-03) tenía en su lista: `ingest_file.py`,
`send_reminder.py`, `sync_connector.py`, `generate_content.py`,
`memory_consolidate.py`.

Ver `docs/cumplimiento/barrido-evidencia-v6.md` para la tabla completa
archivo→veredicto→evidencia. La pregunta que pedía el paquete de trabajo era:
"¿hay efecto EXTERNO irreversible (red) seguido de marca durable que un
`raise` posterior deshaga, causando doble efecto en el retry?" (el mismo
mecanismo de `campaigns.handle`, el hallazgo real de este WP). Resumen:
**ninguno de los 5 handlers tiene ese patrón** — por razones distintas en
cada caso, todas verificadas leyendo el código real (no solo grep):

- `send_reminder.py`: YA separa correctamente el efecto externo arriesgado
  (push nativo, que puede fallar) de la escritura durable (mensaje +
  `reminders.status='sent'`) — el push corre DESPUÉS de que la sesión que
  escribió eso ya comiteó (fuera de cualquier `async with`), envuelto en su
  PROPIO `try/except` que nunca deja escapar la excepción. Es exactamente el
  patrón "durabilidad primero, efecto externo arriesgado después, fuera de
  la sesión" que este WP quiere en todos lados — ya existía antes de este
  barrido. Cubierto exhaustivamente por
  `test_send_reminder.py::test_send_reminder_channel_mobile_push_falla_no_revienta_el_job`
  (verifica que `status == "sent"` y el mensaje sobreviven aunque el push
  lance) — no se repite aquí.
- `sync_connector.py`: cada fila se refresca dentro de su PROPIO
  `try/except` (`_refresh_one`), que nunca deja escapar una excepción hacia
  `handle()` — así que el fallo de UN token nunca puede tumbar la sesión
  compartida por el resto del lote. Cubierto por
  `test_sync_connector.py::test_sync_connector_un_fallo_no_detiene_los_demas`
  — no se repite aquí.
- `ingest_file.py`/`generate_content.py`: no hay NINGÚN efecto externo
  irreversible de por medio (el LLM/embeddings son cómputo puro
  recalculable, no un SMS/llamada real a un tercero) — si algo falla a
  mitad de camino, la sesión completa hace rollback (nada se escribió antes
  de forma "durable" que sobreviva parcialmente) y un reintento del job
  entero vuelve a recalcular todo desde cero, limpio e idéntico. Este
  archivo SÍ agrega tests nuevos (`test_ingest_file_fallo_de_embeddings_a_
  mitad_del_lote_propaga_sin_swallow`/
  `test_generate_content_fallo_del_llm_propaga_sin_escribir_nada`) que
  verifican la mitad de esa garantía que SÍ es observable con `FakeRepo`
  (que no modela transacciones): que la excepción se propaga LIMPIA fuera de
  `handle()` sin ser tragada a medias por algún `try/except` interno — es
  justo esa propagación la que dispara el rollback real de
  `edecan_db.session.get_session` en producción (`ARCHITECTURE.md` §10.3).
- `memory_consolidate.py`: fases 1 y 3 (extracción por LLM, perfil vivo) YA
  están cada una envuelta en su propio `try/except Exception` que nunca
  propaga — mismo espíritu que `sync_connector.py`. Este archivo agrega
  `test_memory_consolidate_fallo_del_llm_en_fase_1_no_tumba_el_job` (nueva:
  los tests existentes de `test_memory_consolidate.py` cubren JSON
  malformado del LLM, que ya lo atrapa `_parsear_items_extraidos` con su
  propio try/except MÁS INTERNO — no una excepción real del proveedor, que
  es lo que ejercita el try/except MÁS EXTERNO de `_extraer_memorias_nuevas`,
  el que de verdad importa para este barrido). Hallazgo MENOR, fuera de
  alcance, documentado pero NO corregido: la fase 2 (deduplicación, entre
  las fases 1 y 3) NO tiene su propio `try/except` — un fallo de Postgres ahí
  (p. ej. `repo.delete_memory_items`) SÍ se propagaría y haría rollback de
  toda la sesión del job, incluido lo que la fase 1 ya extrajo. No calza en
  el patrón que este WP corrige (fase 2 no tiene NINGÚN efecto externo/red,
  es dedup puro-Postgres) — la única consecuencia de ese rollback es que un
  reintento del job vuelve a recalcular la fase 1 desde cero (mismo cómputo,
  determinista salvo por la respuesta del LLM) y a deduplicar de nuevo, sin
  ningún efecto duplicado hacia el exterior ni pérdida de evidencia legal —
  así que no amerita el mismo tratamiento que `campaigns.handle`.

Mismas convenciones que el resto de `apps/worker/tests/`: `fakes.FakeRepo`/
`fakes.make_deps` (`ARCHITECTURE.md` §10.1), cada handler monkeypatchea su
`SqlRepo` importado para usar `FakeRepo`.
"""

from __future__ import annotations

import uuid

import edecan_worker.handlers.generate_content as generate_content_module
import edecan_worker.handlers.ingest_file as ingest_file_module
import edecan_worker.handlers.memory_consolidate as consolidate_module
import pytest
from edecan_schemas import JobEnvelope
from edecan_worker.handlers.ingest_file import EMBEDDING_BATCH_SIZE, chunk_text
from fakes import FakeRepo, make_deps


async def _use_platform_router_as_tenant_router(deps, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mismo helper que `test_generate_content.py`/`test_profile_consolidation.py`:
    `Deps.llm_router_for` (bring-your-own) resuelve directo al router fake de
    plataforma en vez de intentar una resolución real (que arriesgaría red)."""

    async def _fake_llm_router_for(tenant_id):
        return deps.llm_router

    monkeypatch.setattr(deps, "llm_router_for", _fake_llm_router_for)


# ---------------------------------------------------------------------------
# ingest_file.py — un fallo de embeddings a mitad del lote propaga limpio.
# ---------------------------------------------------------------------------


async def test_ingest_file_fallo_de_embeddings_a_mitad_del_lote_propaga_sin_swallow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Texto lo bastante largo como para producir MÁS de `EMBEDDING_BATCH_SIZE`
    (32) trozos -- dos llamadas a `embedder.embed()`. La 2ª lanza. `handle()`
    debe propagar esa excepción SIN atraparla (ver docstring del módulo): es
    justo esa propagación la que, en producción, dispara el rollback real de
    `get_session` -- si algo aquí la "tragara" en silencio, el archivo
    quedaría con SOLO el primer lote de chunks (ni 'ready' ni 'error'), un
    estado a medias que ningún reintento futuro repararía (no hay ningún
    re-chequeo de "archivo con chunks parciales")."""
    fake_repo = FakeRepo()
    monkeypatch.setattr(ingest_file_module, "SqlRepo", lambda session: fake_repo)

    tenant_id, file_id = uuid.uuid4(), uuid.uuid4()
    s3_key = "tenants/t1/files/f1/grande.txt"
    fake_repo.files[file_id] = {
        "id": file_id,
        "tenant_id": tenant_id,
        "s3_key": s3_key,
        "filename": "grande.txt",
        "mime": "text/plain",
        "size_bytes": 0,
        "status": "uploaded",
    }

    deps = make_deps()
    # >= 33 trozos de ~1200 chars (paso 1000) -> 2 lotes de embeddings.
    parrafo = ("Contenido de prueba para trocear en muchos pedazos. " * 700).strip()
    contenido = parrafo.encode("utf-8")
    esperado = chunk_text(parrafo)
    assert len(esperado) > EMBEDDING_BATCH_SIZE  # confirma que sí habrá 2+ lotes
    deps.s3.put(deps.settings.S3_BUCKET, s3_key, contenido)

    llamadas = {"n": 0}
    embed_original = deps.embedder.embed

    async def embed_que_falla_en_el_2do_lote(textos: list[str]) -> list[list[float]]:
        llamadas["n"] += 1
        if llamadas["n"] == 2:
            raise RuntimeError("blip de red en el proveedor de embeddings")
        return await embed_original(textos)

    monkeypatch.setattr(deps.embedder, "embed", embed_que_falla_en_el_2do_lote)

    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="ingest_file",
        payload={"file_id": str(file_id)},
    )
    with pytest.raises(RuntimeError, match="blip de red"):
        await ingest_file_module.handle(env, deps)

    assert llamadas["n"] == 2  # sí llegó al 2º lote antes de fallar
    # `files.status` NUNCA llegó a marcarse 'ready' -- en Postgres real esto
    # (más los chunks del 1er lote) se habría revertido entero por el
    # rollback de la sesión; `FakeRepo` no modela eso (no hay rollback), así
    # que el status acá se queda en su valor inicial 'uploaded' -- lo
    # importante para este test es que NUNCA llegó a 'ready' pese al 1er
    # lote haber "funcionado".
    assert fake_repo.files[file_id]["status"] == "uploaded"


# ---------------------------------------------------------------------------
# generate_content.py — un fallo del LLM propaga sin escribir nada antes.
# ---------------------------------------------------------------------------


async def test_generate_content_fallo_del_llm_propaga_sin_escribir_nada(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(generate_content_module, "SqlRepo", lambda session: fake_repo)

    tenant_id = uuid.uuid4()
    fake_repo.tenants[tenant_id] = {"id": tenant_id, "plan_key": "hosted_pro"}
    conversation_id = uuid.uuid4()
    fake_repo.conversations[conversation_id] = {
        "id": conversation_id,
        "tenant_id": tenant_id,
        "user_id": uuid.uuid4(),
        "title": "",
        "channel": "web",
    }

    deps = make_deps()
    await _use_platform_router_as_tenant_router(deps, monkeypatch)

    async def complete_que_falla(request):
        raise RuntimeError("el proveedor LLM devolvió 500")

    monkeypatch.setattr(deps.llm_router.provider, "complete", complete_que_falla)

    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="generate_content",
        payload={"conversation_id": str(conversation_id), "brief": "Escribe algo"},
    )
    with pytest.raises(RuntimeError, match="500"):
        await generate_content_module.handle(env, deps)

    # No hay NADA que proteger acá: `repo.add_message`/`add_usage_event` son
    # las ÚLTIMAS operaciones del handler, después de la llamada al LLM que
    # falló -- nunca llegan a ejecutarse. Confirma que este handler no tiene
    # el patrón "escribe evidencia y LUEGO falla" en absoluto (ver docstring
    # del módulo).
    assert fake_repo.messages == []
    assert fake_repo.usage_events == []


# ---------------------------------------------------------------------------
# memory_consolidate.py — fase 1 (extracción LLM) nunca tumba el job.
# ---------------------------------------------------------------------------


async def test_memory_consolidate_fallo_del_llm_en_fase_1_no_tumba_el_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A diferencia de `test_memory_consolidate.py::
    test_handle_json_invalido_del_llm_no_tumba_el_job_ni_inserta_nada` (que
    ejercita el try/except MÁS INTERNO de `_parsear_items_extraidos`, ante
    una respuesta del LLM sintácticamente inválida), este test hace que el
    proveedor LLM lance una excepción REAL (p. ej. un 500) -- ejercitando el
    try/except MÁS EXTERNO de `_extraer_memorias_nuevas`, que es el que
    protege contra un fallo de RED real, el ángulo que le importa a este
    barrido (WP-V6-03)."""
    fake_repo = FakeRepo()
    monkeypatch.setattr(consolidate_module, "SqlRepo", lambda session: fake_repo)

    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    fake_repo.tenants[tenant_id] = {"id": tenant_id, "plan_key": "hosted_pro"}
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
            "content": {"text": "Prefiero que me hables de tú, y trabajo en una agencia."},
            "tool_calls": None,
            "tokens_in": 0,
            "tokens_out": 0,
        }
    )
    # Sin memoria previa: la fase 3 (perfil vivo) hace un `return` temprano
    # ("nada de qué construir un perfil") ANTES de tocar `session.execute`
    # directo -- así este test puede usar el `session_factory` placeholder
    # por defecto de `make_deps()` sin necesitar el `FakeSession` con SQL
    # real que sí usa `test_profile_consolidation.py` (ver su docstring).

    deps = make_deps()
    await _use_platform_router_as_tenant_router(deps, monkeypatch)

    async def complete_que_falla(request):
        raise RuntimeError("el proveedor LLM devolvió 500")

    monkeypatch.setattr(deps.llm_router.provider, "complete", complete_que_falla)

    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="memory_consolidate",
        payload={"user_id": str(user_id)},
    )
    await consolidate_module.handle(env, deps)  # NO debe lanzar

    assert fake_repo.memory_items == {}  # nada se extrajo (fase 1 falló, best-effort)
