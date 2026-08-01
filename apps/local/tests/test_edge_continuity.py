from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from edecan_local import edge_continuity
from edecan_local.edge_continuity import (
    CONFIG_FILENAME,
    GOODBYE_DEADLINE_SECONDS,
    GOODBYE_PATH,
    GOODBYE_SETTLE_SECONDS,
    SECRET_FILENAME,
    EdgeContinuityClient,
    EdgeContinuityConfig,
    EdgeContinuityConfigurationError,
    EdgeContinuityRequestError,
    EdgeContinuityState,
    LocalChatTaskHandler,
    load_edge_continuity_config,
    run_edge_continuity,
)


def _write_config(data_dir: Path, *, base_url: str = "https://edge.example.test") -> None:
    (data_dir / CONFIG_FILENAME).write_text(
        json.dumps(
            {
                "enabled": True,
                "base_url": base_url,
                "installation_id": "personal-installation",
                "heartbeat_seconds": 10,
                "claim_seconds": 0.5,
            }
        ),
        encoding="utf-8",
    )
    (data_dir / SECRET_FILENAME).write_text("s" * 48, encoding="utf-8")


def test_edge_configuration_is_opt_in_and_secret_stays_separate(tmp_path: Path) -> None:
    assert load_edge_continuity_config(tmp_path, environ={}) is None
    _write_config(tmp_path)

    config = load_edge_continuity_config(tmp_path, environ={})

    assert config is not None
    assert config.base_url == "https://edge.example.test"
    assert config.installation_id == "personal-installation"
    assert config.shared_secret == "s" * 48
    if os.name == "posix":
        assert (tmp_path / SECRET_FILENAME).stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "base_url",
    [
        "http://edge.example.test",
        "https://user:pass@edge.example.test",
        "https://edge.example.test/path/../admin",
        "https://edge.example.test?secret=value",
    ],
)
def test_edge_configuration_rejects_unsafe_origins(tmp_path: Path, base_url: str) -> None:
    _write_config(tmp_path, base_url=base_url)
    with pytest.raises(EdgeContinuityConfigurationError):
        load_edge_continuity_config(tmp_path, environ={})


@pytest.mark.asyncio
async def test_local_chat_handler_uses_real_local_sse_contract_and_job_idempotency() -> None:
    seen: list[tuple[str, str, str | None]] = []
    conversation_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                request.method,
                request.url.path,
                request.headers.get("idempotency-key"),
            )
        )
        if request.url.path == "/v1/auth/local":
            assert request.headers["x-edecan-desktop-capability"] == "desktop-capability"
            return httpx.Response(200, json={"access_token": "local-access"})
        if request.url.path == "/v1/conversations":
            return httpx.Response(201, json={"id": conversation_id})
        if request.url.path == f"/v1/conversations/{conversation_id}/messages":
            assert request.headers["authorization"] == "Bearer local-access"
            return httpx.Response(
                200,
                text=(
                    'event: message.delta\ndata: {"type":"text_delta","text":"Hola "}\n\n'
                    'event: message.delta\ndata: {"type":"text_delta","text":"desde local"}\n\n'
                    'event: message.done\ndata: {"type":"done","usage":{}}\n\n'
                ),
                headers={"content-type": "text/event-stream"},
            )
        if request.url.path == "/v1/continuity/sync":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "deduplicated": False,
                    "conversation_id": conversation_id,
                },
            )
        raise AssertionError(f"Request inesperado: {request.method} {request.url}")

    local = LocalChatTaskHandler(
        api_base_url="http://127.0.0.1:8765",
        desktop_capability="desktop-capability",
        transport=httpx.MockTransport(handler),
    )
    try:
        result = await local({"type": "chat", "message": "Hola"}, job_id)
    finally:
        await local.aclose()

    assert result["message"] == "Hola desde local"
    assert result["conversation_id"] == conversation_id
    assert (
        "POST",
        f"/v1/conversations/{conversation_id}/messages",
        job_id,
    ) in seen

    sync_job_id = str(uuid.uuid4())
    sync_local = LocalChatTaskHandler(
        api_base_url="http://127.0.0.1:8765",
        desktop_capability="desktop-capability",
        transport=httpx.MockTransport(handler),
    )
    try:
        sync_result = await sync_local(
            {
                "type": "continuity_sync",
                "event_id": sync_job_id,
                "conversation_id": conversation_id,
                "user_message": "Pregunta offline",
                "assistant_message": "Respuesta offline",
            },
            sync_job_id,
        )
    finally:
        await sync_local.aclose()
    assert sync_result["ok"] is True


@pytest.mark.asyncio
async def test_runner_heartbeats_claims_and_acks_a_job() -> None:
    stop_event = asyncio.Event()
    state = EdgeContinuityState()
    job_id = str(uuid.uuid4())
    requests: list[tuple[str, dict[str, Any]]] = []

    async def transport_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        requests.append((request.url.path, body))
        assert request.headers["authorization"] == f"Bearer {'k' * 48}"
        assert request.headers["x-edecan-installation"] == "test-installation"
        if request.url.path == "/v1/edge/heartbeat":
            return httpx.Response(200, json={"ok": True, "online": True, "server_time": 1_700_000})
        if request.url.path == "/v1/edge/jobs/claim":
            claimed_before = any(path == "/v1/edge/jobs/complete" for path, _ in requests)
            return httpx.Response(
                200,
                json={
                    "jobs": []
                    if claimed_before
                    else [
                        {
                            "job_id": job_id,
                            "receipt_handle": "receipt",
                            "task": {"type": "chat", "message": "Hola"},
                        }
                    ]
                },
            )
        if request.url.path == "/v1/edge/jobs/complete":
            stop_event.set()
            return httpx.Response(200, json={"ok": True})
        if request.url.path == GOODBYE_PATH:
            return httpx.Response(200, json={"applied": True})
        raise AssertionError(f"Ruta inesperada: {request.url.path}")

    async def task_handler(task: dict[str, Any], claimed_job_id: str) -> dict[str, Any]:
        assert claimed_job_id == job_id
        assert task == {"type": "chat", "message": "Hola"}
        return {"message": "Hecho"}

    await asyncio.wait_for(
        run_edge_continuity(
            EdgeContinuityConfig(
                base_url="https://edge.example.test",
                installation_id="test-installation",
                shared_secret="k" * 48,
                heartbeat_seconds=10,
                claim_seconds=0.5,
            ),
            stop_event=stop_event,
            state=state,
            version="test",
            capabilities=["chat"],
            task_handler=task_handler,
            transport=httpx.MockTransport(transport_handler),
        ),
        timeout=3,
    )

    complete = next(body for path, body in requests if path == "/v1/edge/jobs/complete")
    assert complete == {
        "job_id": job_id,
        "receipt_handle": "receipt",
        "status": "done",
        "result": {"message": "Hecho"},
    }
    assert any(path == "/v1/edge/heartbeat" for path, _ in requests)
    assert state.last_heartbeat_at is not None
    # `connected` queda en False porque este apagado limpio ya despidió a la
    # computadora ante el edge (ver el test de la despedida más abajo).
    assert state.connected is False
    assert state.completed_jobs == 1
    assert state.failed_jobs == 0


def _test_config() -> EdgeContinuityConfig:
    return EdgeContinuityConfig(
        base_url="https://edge.example.test",
        installation_id="test-installation",
        shared_secret="k" * 48,
        heartbeat_seconds=10,
        claim_seconds=0.5,
    )


@pytest.mark.asyncio
async def test_clean_shutdown_says_goodbye_with_the_same_credentials_as_the_heartbeat() -> None:
    """Cierra el punto ciego del TTL: el edge se entera al instante, no en 90s."""

    stop_event = asyncio.Event()
    state = EdgeContinuityState()
    requests: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    async def transport_handler(request: httpx.Request) -> httpx.Response:
        requests.append(
            (
                request.url.path,
                json.loads(request.content or b"{}"),
                {
                    "authorization": request.headers.get("authorization", ""),
                    "x-edecan-installation": request.headers.get("x-edecan-installation", ""),
                },
            )
        )
        if request.url.path == "/v1/edge/heartbeat":
            # Apagado limpio: la persona cierra la app justo después de que el
            # edge la dio por viva con este `server_time`.
            stop_event.set()
            return httpx.Response(200, json={"ok": True, "online": True, "server_time": 1_700_000})
        if request.url.path == "/v1/edge/jobs/claim":
            return httpx.Response(200, json={"jobs": []})
        if request.url.path == GOODBYE_PATH:
            return httpx.Response(200, json={"applied": True})
        raise AssertionError(f"Ruta inesperada: {request.url.path}")

    async def task_handler(_task: dict[str, Any], _job_id: str) -> dict[str, Any]:
        raise AssertionError("No debería ejecutarse ningún trabajo.")

    await asyncio.wait_for(
        run_edge_continuity(
            _test_config(),
            stop_event=stop_event,
            state=state,
            version="test",
            capabilities=["chat"],
            task_handler=task_handler,
            transport=httpx.MockTransport(transport_handler),
        ),
        timeout=5,
    )

    goodbyes = [entry for entry in requests if entry[0] == GOODBYE_PATH]
    # Exactamente una: el aviso no puede duplicarse aunque el apagado pase por
    # varios caminos (señal, cierre de la ventana, fin de los loops).
    assert len(goodbyes) == 1
    _path, body, headers = goodbyes[0]
    # Cita el latido que el edge registró; sin eso el edge no puede distinguir
    # esta despedida de una vieja y no la aplicaría.
    assert body == {"last_seen_at": 1_700_000}
    assert headers["authorization"] == f"Bearer {'k' * 48}"
    assert headers["x-edecan-installation"] == "test-installation"
    assert state.connected is False
    # Ningún latido después de la despedida: uno posterior revive la
    # computadora en el edge y devolvería el punto ciego que esto cierra.
    paths = [path for path, _body, _headers in requests]
    assert "/v1/edge/heartbeat" not in paths[paths.index(GOODBYE_PATH) :]


@pytest.mark.asyncio
async def test_a_heartbeat_still_in_flight_lands_before_the_goodbye() -> None:
    """Si el latido aterrizara después, el edge revive la computadora y vuelve el punto ciego."""

    stop_event = asyncio.Event()
    state = EdgeContinuityState()
    # Orden REAL de llegada al edge, no de salida desde la computadora.
    arrivals: list[str] = []
    goodbye_body: dict[str, Any] = {}

    async def transport_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/edge/heartbeat":
            # El latido sigue en vuelo cuando la persona cierra la app.
            await asyncio.sleep(0.15)
            arrivals.append("latido")
            return httpx.Response(200, json={"ok": True, "server_time": 1_700_010})
        if request.url.path == "/v1/edge/jobs/claim":
            stop_event.set()
            return httpx.Response(200, json={"jobs": []})
        if request.url.path == GOODBYE_PATH:
            arrivals.append("despedida")
            goodbye_body.update(json.loads(request.content or b"{}"))
            return httpx.Response(200, json={"applied": True})
        raise AssertionError(f"Ruta inesperada: {request.url.path}")

    async def task_handler(_task: dict[str, Any], _job_id: str) -> dict[str, Any]:
        raise AssertionError("No debería ejecutarse ningún trabajo.")

    await asyncio.wait_for(
        run_edge_continuity(
            _test_config(),
            stop_event=stop_event,
            state=state,
            version="test",
            capabilities=["chat"],
            task_handler=task_handler,
            transport=httpx.MockTransport(transport_handler),
        ),
        timeout=5,
    )

    # La despedida esperó a ese latido y cita su `server_time`; sin esperarlo
    # no habría tenido ninguno que citar.
    assert arrivals == ["latido", "despedida"]
    assert goodbye_body == {"last_seen_at": 1_700_010}


@pytest.mark.asyncio
async def test_a_slow_heartbeat_does_not_cancel_the_goodbye() -> None:
    """El caso que devolvía el bug: la red mala hace lento al latido.

    Un latido lento es justo la señal de que la red se está cayendo, o sea el
    momento en que el aviso más falta hace. Esperarlo dentro del mismo tope que
    el envío hacía que la computadora se apagara sin avisar nada y la persona
    volviera a recibir "quedó en la cola" durante el TTL entero.
    """

    stop_event = asyncio.Event()
    state = EdgeContinuityState()
    arrivals: list[str] = []
    beats = 0

    async def transport_handler(request: httpx.Request) -> httpx.Response:
        nonlocal beats
        if request.url.path == "/v1/edge/heartbeat":
            beats += 1
            if beats == 1:
                arrivals.append("latido")
                return httpx.Response(200, json={"ok": True, "server_time": 1_700_000})
            # Segundo latido: sale, y mientras sigue en vuelo la persona cierra
            # la app. Tarda más que la espera cortés (Lambda fría, wifi malo).
            stop_event.set()
            await asyncio.sleep(GOODBYE_SETTLE_SECONDS * 3)
            arrivals.append("latido-lento")
            return httpx.Response(200, json={"ok": True, "server_time": 1_700_030})
        if request.url.path == "/v1/edge/jobs/claim":
            return httpx.Response(200, json={"jobs": []})
        if request.url.path == GOODBYE_PATH:
            arrivals.append("despedida")
            return httpx.Response(200, json={"applied": True})
        raise AssertionError(f"Ruta inesperada: {request.url.path}")

    async def task_handler(_task: dict[str, Any], _job_id: str) -> dict[str, Any]:
        raise AssertionError("No debería ejecutarse ningún trabajo.")

    await asyncio.wait_for(
        run_edge_continuity(
            # Latidos seguidos para llegar rápido al segundo, que es el que se
            # cuelga; el intervalo real no cambia nada de lo que se prueba acá.
            EdgeContinuityConfig(
                base_url="https://edge.example.test",
                installation_id="test-installation",
                shared_secret="k" * 48,
                heartbeat_seconds=0.05,
                claim_seconds=0.5,
            ),
            stop_event=stop_event,
            state=state,
            version="test",
            capabilities=["chat"],
            task_handler=task_handler,
            transport=httpx.MockTransport(transport_handler),
        ),
        timeout=10,
    )

    # El aviso salió sin esperar al latido colgado, citando el último latido que
    # el edge sí llegó a aceptar.
    assert "despedida" in arrivals
    assert arrivals.index("despedida") < arrivals.index("latido-lento")
    # Y sigue siendo UNO solo: el latido lento no dispara un segundo aviso.
    assert arrivals.count("despedida") == 1


@pytest.mark.asyncio
async def test_an_unreachable_edge_neither_blocks_nor_delays_the_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Una app que tarda en cerrarse es peor que un edge que tarda 90s en enterarse."""

    # El tope real es corto de fábrica; acá se acorta para no dormir en CI.
    assert GOODBYE_DEADLINE_SECONDS <= 3.0
    monkeypatch.setattr(edge_continuity, "GOODBYE_DEADLINE_SECONDS", 0.2)

    stop_event = asyncio.Event()
    state = EdgeContinuityState()
    reached_edge = asyncio.Event()

    async def transport_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/edge/heartbeat":
            stop_event.set()
            return httpx.Response(200, json={"ok": True, "server_time": 1_700_000})
        if request.url.path == "/v1/edge/jobs/claim":
            return httpx.Response(200, json={"jobs": []})
        if request.url.path == GOODBYE_PATH:
            # AWS acepta la conexión y nunca contesta: el peor caso para el
            # cierre, porque no hay error que corte la espera.
            reached_edge.set()
            await asyncio.sleep(300)
        raise AssertionError(f"Ruta inesperada: {request.url.path}")

    async def task_handler(_task: dict[str, Any], _job_id: str) -> dict[str, Any]:
        raise AssertionError("No debería ejecutarse ningún trabajo.")

    started = time.monotonic()
    await asyncio.wait_for(
        run_edge_continuity(
            _test_config(),
            stop_event=stop_event,
            state=state,
            version="test",
            capabilities=["chat"],
            task_handler=task_handler,
            transport=httpx.MockTransport(transport_handler),
        ),
        timeout=5,
    )
    elapsed = time.monotonic() - started

    assert reached_edge.is_set()
    # El apagado terminó por su cuenta, sin esperar al edge colgado.
    assert elapsed < 1.0


@pytest.mark.asyncio
async def test_disabled_continuity_never_says_goodbye(tmp_path: Path) -> None:
    """Sin configuración activa no hay cliente que pueda avisar nada."""

    _write_config(tmp_path)
    (tmp_path / CONFIG_FILENAME).write_text(
        json.dumps(
            {
                "enabled": False,
                "base_url": "https://edge.example.test",
                "installation_id": "personal-installation",
            }
        ),
        encoding="utf-8",
    )
    requests: list[str] = []

    async def transport_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(200, json={"ok": True})

    # Mismo guard que `edecan_local.runtime.run()`: la continuidad (y con ella
    # la despedida) solo arranca cuando el cargador devuelve configuración.
    config = load_edge_continuity_config(tmp_path, environ={})
    assert config is None
    if config is not None:  # pragma: no cover - documenta el guard real
        await run_edge_continuity(
            config,
            stop_event=asyncio.Event(),
            state=EdgeContinuityState(),
            version="test",
            capabilities=["chat"],
            task_handler=lambda *_args: asyncio.sleep(0, {}),
            transport=httpx.MockTransport(transport_handler),
        )

    assert requests == []
    assert load_edge_continuity_config(tmp_path, environ={"EDECAN_EDGE_ENABLED": "0"}) is None


@pytest.mark.asyncio
async def test_the_goodbye_is_sent_once_even_if_the_shutdown_arrives_twice() -> None:
    goodbyes = 0

    async def transport_handler(request: httpx.Request) -> httpx.Response:
        nonlocal goodbyes
        if request.url.path == "/v1/edge/heartbeat":
            return httpx.Response(200, json={"ok": True, "server_time": 1_700_000})
        assert request.url.path == GOODBYE_PATH
        goodbyes += 1
        return httpx.Response(200, json={"applied": True})

    async with EdgeContinuityClient(
        _test_config(), transport=httpx.MockTransport(transport_handler)
    ) as client:
        await client.heartbeat(capabilities=["chat"], version="test")
        first, second = await asyncio.gather(
            client.announce_goodbye(),
            client.announce_goodbye(),
        )

    assert first == {"applied": True}
    assert second is None
    assert goodbyes == 1


@pytest.mark.asyncio
async def test_without_an_accepted_heartbeat_there_is_nothing_to_say_goodbye_to() -> None:
    """El edge nunca dio por viva a esta computadora, así que no hay TTL que acortar."""

    async def transport_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/edge/heartbeat":
            return httpx.Response(503, json={"error": "edge_unavailable"})
        raise AssertionError(f"Ruta inesperada: {request.url.path}")

    async with EdgeContinuityClient(
        _test_config(), transport=httpx.MockTransport(transport_handler)
    ) as client:
        with pytest.raises(EdgeContinuityRequestError):
            await client.heartbeat(capabilities=["chat"], version="test")

        assert await client.announce_goodbye() is None
