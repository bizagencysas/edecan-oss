"""`backoff calculado`: `compute_backoff_seconds` sigue `min(900, 2**attempt*30)`
(ARCHITECTURE.md §10.11), y el ciclo de reintentos de `_handle_message` respeta
el límite de `MAX_ATTEMPTS` antes de dejar el mensaje para el redrive/DLQ.
"""

from __future__ import annotations

import json
import uuid

import edecan_worker.main as main_module
import pytest
from edecan_schemas import JobEnvelope
from fakes import FakeSQS, make_deps


@pytest.mark.parametrize(
    ("attempt", "esperado"),
    [(0, 30), (1, 60), (2, 120), (3, 240), (4, 480), (5, 900), (6, 900), (10, 900)],
)
def test_compute_backoff_seconds(attempt: int, esperado: int) -> None:
    assert main_module.compute_backoff_seconds(attempt) == esperado


def _envelope(attempt: int) -> JobEnvelope:
    return JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        type="generate_content",
        payload={},
        attempt=attempt,
    )


def _message(env: JobEnvelope) -> dict:
    return {"ReceiptHandle": f"rh-{env.job_id}", "Body": env.model_dump_json()}


async def test_reintenta_con_backoff_y_borra_el_mensaje_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler_falla(env: JobEnvelope, deps) -> None:
        raise RuntimeError("boom")

    monkeypatch.setitem(main_module.HANDLERS, "generate_content", handler_falla)

    deps = make_deps()
    env = _envelope(attempt=2)
    await main_module._handle_message(deps, _message(env))

    sqs: FakeSQS = deps.sqs
    assert len(sqs.sent) == 1
    reenviado = json.loads(sqs.sent[0]["MessageBody"])
    assert reenviado["attempt"] == 3
    assert sqs.sent[0]["DelaySeconds"] == main_module.compute_backoff_seconds(2) == 120
    assert sqs.deleted == [f"rh-{env.job_id}"]


async def test_al_agotar_intentos_no_borra_para_que_el_redrive_lo_mande_a_dlq(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler_falla(env: JobEnvelope, deps) -> None:
        raise RuntimeError("boom")

    monkeypatch.setitem(main_module.HANDLERS, "generate_content", handler_falla)

    deps = make_deps()
    env = _envelope(attempt=main_module.MAX_ATTEMPTS)  # ya agotó los reintentos permitidos
    await main_module._handle_message(deps, _message(env))

    sqs: FakeSQS = deps.sqs
    assert sqs.sent == []
    assert sqs.deleted == []  # se deja visible para que SQS lo redirija a la DLQ


async def test_job_exitoso_borra_el_mensaje_y_no_reencola(monkeypatch: pytest.MonkeyPatch) -> None:
    llamadas = []

    async def handler_ok(env: JobEnvelope, deps) -> None:
        llamadas.append(env)

    monkeypatch.setitem(main_module.HANDLERS, "generate_content", handler_ok)

    deps = make_deps()
    env = _envelope(attempt=0)
    await main_module._handle_message(deps, _message(env))

    assert len(llamadas) == 1
    sqs: FakeSQS = deps.sqs
    assert sqs.deleted == [f"rh-{env.job_id}"]
    assert sqs.sent == []


async def test_mensaje_con_tipo_sin_handler_registrado_se_descarta_sin_reintentar() -> None:
    # `JobEnvelope` valida `type` contra JOB_TYPES al construirse, así que no
    # se puede armar un mensaje con un tipo realmente inventado; simulamos en
    # su lugar el caso "handler no registrado" quitando uno momentáneamente.
    deps = make_deps()
    env = _envelope(attempt=0)
    handler = main_module.HANDLERS.pop("generate_content")
    try:
        await main_module._handle_message(deps, _message(env))
    finally:
        main_module.HANDLERS["generate_content"] = handler

    sqs: FakeSQS = deps.sqs
    assert sqs.deleted == [f"rh-{env.job_id}"]
    assert sqs.sent == []


async def test_mensaje_invalido_se_descarta_sin_reintentar() -> None:
    deps = make_deps()
    message = {"ReceiptHandle": "rh-invalido", "Body": "esto no es JSON"}
    await main_module._handle_message(deps, message)

    sqs: FakeSQS = deps.sqs
    assert sqs.deleted == ["rh-invalido"]
    assert sqs.sent == []


async def test_poll_once_despacha_todos_los_mensajes_recibidos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    procesados = []

    async def handler_ok(env: JobEnvelope, deps) -> None:
        procesados.append(env.job_id)

    monkeypatch.setitem(main_module.HANDLERS, "generate_content", handler_ok)

    deps = make_deps()
    env_a = _envelope(attempt=0)
    env_b = _envelope(attempt=0)
    deps.sqs.to_receive = [_message(env_a), _message(env_b)]

    n = await main_module.poll_once(deps)

    assert n == 2
    assert set(procesados) == {env_a.job_id, env_b.job_id}
    assert set(deps.sqs.deleted) == {f"rh-{env_a.job_id}", f"rh-{env_b.job_id}"}
