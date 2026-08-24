"""Cliente residente para la continuidad privada de Edecán.

La app maestra sigue siendo la fuente de verdad. Este módulo únicamente:

* publica un heartbeat autenticado en el edge personal;
* avisa a ese mismo edge, con la misma autenticación, cuando la computadora se
  apaga limpio, para que el respaldo conteste de una vez en vez de encolar un
  trabajo que ya nadie va a ejecutar;
* reclama trabajos que quedaron en la cola mientras el teléfono no podía
  alcanzar el origen local;
* ejecuta esos trabajos contra la API local real (misma memoria, herramientas
  y proveedor LLM);
* confirma el resultado para que el edge pueda entregarlo al cliente.

La configuración es deliberadamente local. Ningún endpoint, installation id o
secreto tiene un valor por defecto que pueda terminar publicado en el repo.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

CONFIG_FILENAME = "edge-continuity.json"
SECRET_FILENAME = "edge-continuity.secret"
MAX_CONFIG_BYTES = 16_384
MIN_SHARED_SECRET_LENGTH = 20
MAX_RESULT_BYTES = 280_000
DEFAULT_HEARTBEAT_SECONDS = 30.0
DEFAULT_CLAIM_SECONDS = 2.0
MAX_BACKOFF_SECONDS = 60.0
# Contrato con `infra/aws/edge`: al apagarse limpio, la computadora avisa que
# se va autenticándose IGUAL que el latido (bearer de la capacidad privada más
# la cabecera de instalación) y citando el `server_time` del último latido
# aceptado. Sin ese aviso el edge da por viva a la computadora hasta que vence
# su TTL de latidos, y en ese punto ciego encola el mensaje en vez de
# contestarlo -- justo cuando el respaldo hace falta: la persona cierra la
# computadora, sale y pregunta algo enseguida.
GOODBYE_PATH = "/v1/edge/goodbye"
# La despedida corre mientras la app se está cerrando, así que NO hereda el
# timeout del latido (15s, que ahí sí puede reintentar en el próximo ciclo) ni
# reintenta: una app que tarda en cerrarse es peor que un edge que tarda su TTL
# en enterarse. `GOODBYE_DEADLINE_SECONDS` es el tope duro del ENVÍO, incluso si
# el transporte HTTP no respeta el suyo, y `GOODBYE_SETTLE_SECONDS` el de la
# espera previa al latido en vuelo. Van separados a propósito: son dos esperas
# distintas y una sola bolsa dejaba que un latido lento se comiera el aviso
# entero (ver `_say_goodbye_safely`).
GOODBYE_CONNECT_TIMEOUT_SECONDS = 1.0
GOODBYE_HTTP_TIMEOUT_SECONDS = 1.5
GOODBYE_SETTLE_SECONDS = 1.0
GOODBYE_DEADLINE_SECONDS = 2.0
_INSTALLATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class EdgeContinuityConfigurationError(ValueError):
    """Configuración privada ausente o insegura."""


class EdgeContinuityRequestError(RuntimeError):
    """El edge respondió con un error público y acotado."""

    def __init__(self, code: str, *, status_code: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class EdgeContinuityConfig:
    base_url: str
    installation_id: str
    shared_secret: str
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS
    claim_seconds: float = DEFAULT_CLAIM_SECONDS


@dataclass(slots=True)
class EdgeContinuityState:
    """Estado público seguro. Nunca contiene URL, installation id ni secreto."""

    configured: bool = False
    enabled: bool = False
    connected: bool = False
    last_heartbeat_at: str | None = None
    last_claim_at: str | None = None
    last_job_at: str | None = None
    last_error: str | None = None
    completed_jobs: int = 0
    failed_jobs: int = 0

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise EdgeContinuityConfigurationError("enabled inválido")


def _bounded_interval(value: object, *, default: float, minimum: float, maximum: float) -> float:
    if value is None or value == "":
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise EdgeContinuityConfigurationError("intervalo inválido") from exc
    if not minimum <= parsed <= maximum:
        raise EdgeContinuityConfigurationError("intervalo fuera de rango")
    return parsed


def _validated_base_url(value: object) -> str:
    raw = str(value or "").strip().rstrip("/")
    try:
        parsed = urlsplit(raw)
    except ValueError as exc:
        raise EdgeContinuityConfigurationError("base_url inválida") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise EdgeContinuityConfigurationError(
            "base_url debe ser un origen HTTPS sin credenciales, query ni fragmento"
        )
    # API Gateway puede incluir un stage como único prefijo. Se prohíben
    # segmentos ambiguos y traversal, no un stage explícito.
    if any(segment in {"", ".", ".."} for segment in parsed.path.split("/")[1:]):
        if parsed.path not in {"", "/"}:
            raise EdgeContinuityConfigurationError("base_url contiene una ruta inválida")
    return raw


def _validated_installation_id(value: object) -> str:
    installation_id = str(value or "").strip()
    if not _INSTALLATION_ID_RE.fullmatch(installation_id):
        raise EdgeContinuityConfigurationError("installation_id inválido")
    return installation_id


def _read_private_text(path: Path, *, max_bytes: int) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size > max_bytes:
            return None
        # El contenido sigue siendo válido en Windows, donde chmod no tiene el
        # mismo significado. En POSIX endurecemos cualquier archivo heredado.
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        logger.warning("No se pudo leer la configuración privada %s.", path, exc_info=True)
        return None


def load_edge_continuity_config(
    data_dir: Path,
    *,
    environ: dict[str, str] | None = None,
) -> EdgeContinuityConfig | None:
    """Carga la configuración privada sin aceptar defaults del repositorio.

    Variables de entorno tienen precedencia y permiten despliegues
    administrados. La app instalada usa ``edge-continuity.json`` para valores
    no secretos y ``edge-continuity.secret`` (0600) para la capacidad
    compartida. Si no hay configuración, la continuidad queda apagada sin
    afectar el runtime local.
    """

    env = os.environ if environ is None else environ
    config_path = data_dir / CONFIG_FILENAME
    file_payload: dict[str, Any] = {}
    raw_config = _read_private_text(config_path, max_bytes=MAX_CONFIG_BYTES)
    if raw_config:
        try:
            parsed = json.loads(raw_config)
        except json.JSONDecodeError as exc:
            raise EdgeContinuityConfigurationError(
                "edge-continuity.json no es JSON válido"
            ) from exc
        if not isinstance(parsed, dict):
            raise EdgeContinuityConfigurationError("edge-continuity.json debe ser un objeto")
        file_payload = parsed

    present = bool(file_payload) or any(
        env.get(name)
        for name in (
            "EDECAN_EDGE_BASE_URL",
            "EDECAN_EDGE_INSTALLATION_ID",
            "EDECAN_EDGE_SHARED_SECRET",
        )
    )
    if not present:
        return None

    enabled = _parse_bool(
        env.get("EDECAN_EDGE_ENABLED", file_payload.get("enabled")),
        default=True,
    )
    if not enabled:
        return None

    shared_secret = (
        env.get("EDECAN_EDGE_SHARED_SECRET")
        or _read_private_text(data_dir / SECRET_FILENAME, max_bytes=8_192)
        or ""
    ).strip()
    if len(shared_secret) < MIN_SHARED_SECRET_LENGTH:
        raise EdgeContinuityConfigurationError(
            f"{SECRET_FILENAME} no existe o no contiene una capacidad válida"
        )

    return EdgeContinuityConfig(
        base_url=_validated_base_url(
            env.get("EDECAN_EDGE_BASE_URL", file_payload.get("base_url"))
        ),
        installation_id=_validated_installation_id(
            env.get("EDECAN_EDGE_INSTALLATION_ID", file_payload.get("installation_id"))
        ),
        shared_secret=shared_secret,
        heartbeat_seconds=_bounded_interval(
            env.get("EDECAN_EDGE_HEARTBEAT_SECONDS", file_payload.get("heartbeat_seconds")),
            default=DEFAULT_HEARTBEAT_SECONDS,
            minimum=10.0,
            maximum=300.0,
        ),
        claim_seconds=_bounded_interval(
            env.get("EDECAN_EDGE_CLAIM_SECONDS", file_payload.get("claim_seconds")),
            default=DEFAULT_CLAIM_SECONDS,
            minimum=0.5,
            maximum=60.0,
        ),
    )


class EdgeContinuityClient:
    """Cliente HTTP mínimo del contrato definido en ``infra/aws/edge``."""

    def __init__(
        self,
        config: EdgeContinuityConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        # La despedida es de un solo disparo por cliente: el apagado puede
        # llegar por varios caminos y un segundo aviso solo agregaría demora.
        self._goodbye_announced = False
        self._last_seen_at: int | None = None
        self._client = httpx.AsyncClient(
            base_url=f"{config.base_url}/",
            transport=transport,
            timeout=httpx.Timeout(15.0, connect=5.0),
            follow_redirects=False,
            headers={
                "authorization": f"Bearer {config.shared_secret}",
                "x-edecan-installation": config.installation_id,
                "user-agent": "edecan-local-continuity/1",
                "accept": "application/json",
            },
        )

    async def __aenter__(self) -> EdgeContinuityClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> dict[str, Any]:
        # `timeout=None` explícito significa "sin límite" en httpx, no "usa el
        # del cliente"; por eso el parámetro solo viaja cuando hay uno propio.
        overrides: dict[str, Any] = {"timeout": timeout} if timeout is not None else {}
        try:
            response = await self._client.request(
                method, path.lstrip("/"), json=json_body, **overrides
            )
        except httpx.TimeoutException as exc:
            raise EdgeContinuityRequestError("edge_timeout") from exc
        except httpx.HTTPError as exc:
            raise EdgeContinuityRequestError("edge_unreachable") from exc
        if response.status_code in {301, 302, 303, 307, 308}:
            raise EdgeContinuityRequestError(
                "edge_redirect_rejected", status_code=response.status_code
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise EdgeContinuityRequestError(
                "edge_invalid_response", status_code=response.status_code
            ) from exc
        if not isinstance(payload, dict):
            raise EdgeContinuityRequestError(
                "edge_invalid_response", status_code=response.status_code
            )
        if response.is_error:
            raw_code = payload.get("error")
            code = str(raw_code)[:80] if raw_code else f"edge_http_{response.status_code}"
            raise EdgeContinuityRequestError(code, status_code=response.status_code)
        return payload

    async def heartbeat(self, *, capabilities: list[str], version: str) -> dict[str, Any]:
        payload = await self._request(
            "POST",
            "/v1/edge/heartbeat",
            json_body={"capabilities": capabilities, "version": version},
        )
        # El edge devuelve el `server_time` con el que registró este latido, y
        # la despedida tiene que citarlo (ver `announce_goodbye`).
        try:
            server_time = int(payload.get("server_time"))
        except (TypeError, ValueError):
            server_time = 0
        if server_time > 0:
            self._last_seen_at = server_time
        return payload

    async def announce_goodbye(self) -> dict[str, Any] | None:
        """Avisa al edge que esta computadora se está apagando.

        Se autentica exactamente igual que el latido, porque es el mismo
        cliente: bearer de la capacidad privada y cabecera de instalación. Cita
        el `server_time` del último latido aceptado, así el edge puede aplicar
        la despedida solo si sigue siendo el latido vigente -- una despedida
        demorada o repetida no puede apagar una computadora que ya volvió.

        Devuelve ``None`` cuando no hay nada que despedir: ningún latido llegó
        a ser aceptado (el edge nunca creyó viva a esta computadora) o el aviso
        ya se intentó en este cliente.
        """

        if self._goodbye_announced or self._last_seen_at is None:
            return None
        # Se marca ANTES del primer await: sin puntos de suspensión entre la
        # comprobación y la marca, dos caminos de apagado concurrentes no
        # pueden mandar ambos el aviso.
        self._goodbye_announced = True
        return await self._request(
            "POST",
            GOODBYE_PATH,
            json_body={"last_seen_at": self._last_seen_at},
            timeout=httpx.Timeout(
                GOODBYE_HTTP_TIMEOUT_SECONDS,
                connect=GOODBYE_CONNECT_TIMEOUT_SECONDS,
            ),
        )

    async def claim(self, *, max_messages: int = 1) -> list[dict[str, Any]]:
        payload = await self._request(
            "POST",
            "/v1/edge/jobs/claim",
            json_body={"max_messages": max(1, min(max_messages, 5))},
        )
        jobs = payload.get("jobs")
        if not isinstance(jobs, list):
            raise EdgeContinuityRequestError("edge_invalid_jobs")
        return [job for job in jobs if isinstance(job, dict)]

    async def complete(
        self,
        *,
        job_id: str,
        receipt_handle: str,
        status: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        encoded = json.dumps(result, ensure_ascii=False, default=str).encode()
        if len(encoded) > MAX_RESULT_BYTES:
            result = {
                "error": "El resultado local excedió el tamaño permitido.",
                "truncated": True,
            }
            status = "error"
        return await self._request(
            "POST",
            "/v1/edge/jobs/complete",
            json_body={
                "job_id": job_id,
                "receipt_handle": receipt_handle,
                "status": status,
                "result": result,
            },
        )

    async def status(self) -> dict[str, Any]:
        return await self._request("GET", "/v1/edge/status")


def _safe_job(job: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    job_id = str(job.get("job_id") or "")
    receipt_handle = str(job.get("receipt_handle") or "")
    task = job.get("task")
    try:
        uuid.UUID(job_id)
    except (ValueError, TypeError) as exc:
        raise EdgeContinuityRequestError("edge_invalid_job_id") from exc
    if not receipt_handle or len(receipt_handle) > 16_384 or not isinstance(task, dict):
        raise EdgeContinuityRequestError("edge_invalid_job")
    return job_id, receipt_handle, task


async def _wait_or_stop(stop_event: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def _send_goodbye(
    client: EdgeContinuityClient,
    *,
    state: EdgeContinuityState,
) -> None:
    payload = await client.announce_goodbye()
    if payload is None:
        return
    state.connected = False
    if payload.get("applied") is False:
        # El edge conserva la última palabra: si ya recibió un latido más nuevo
        # no apaga nada, y esta instalación cae al TTL de siempre.
        logger.info("El edge no aplicó la despedida: %s.", payload.get("reason"))
        return
    logger.info("El edge quedó avisado de que esta computadora se apaga.")


async def _say_goodbye_safely(
    client: EdgeContinuityClient,
    *,
    state: EdgeContinuityState,
    heartbeat_settled: asyncio.Event,
) -> None:
    """Despide a la computadora sin que el apagado dependa de ello.

    Un solo intento y topes duros para cada espera (``GOODBYE_SETTLE_SECONDS``
    para el latido en vuelo, ``GOODBYE_DEADLINE_SECONDS`` para el envío): cerrar
    la app no puede quedar colgado esperando a AWS. Si el aviso no llega, el TTL
    de latidos del edge sigue siendo la red de seguridad, igual que en un corte
    de luz.
    """

    try:
        # Dejar aterrizar el latido en vuelo es lo IDEAL, porque si llegara
        # después su `put_item` borra la despedida y revive la computadora en el
        # edge. Pero NO es condición para avisar, y por eso tiene su propio tope
        # en vez de comerse el del envío: mandar igual nunca sale peor que
        # callarse. Si ese latido llega después, el edge queda como si no
        # hubiéramos avisado (el TTL de siempre); si llegó antes, la despedida
        # cita un `server_time` viejo y el edge contesta `applied: false`, otra
        # vez el TTL de siempre; y si no llega nunca -- el caso REAL cuando
        # tarda, porque la red se está cayendo -- la despedida es lo único que
        # cierra el punto ciego. Con una sola bolsa de tiempo, un latido lento
        # cancelaba el aviso entero justo cuando más falta hacía.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(heartbeat_settled.wait(), timeout=GOODBYE_SETTLE_SECONDS)
        await asyncio.wait_for(
            _send_goodbye(client, state=state),
            timeout=GOODBYE_DEADLINE_SECONDS,
        )
    except EdgeContinuityRequestError as exc:
        logger.info("El edge no recibió la despedida: %s.", exc.code)
    except Exception:
        # Nada que ocurra acá puede impedir ni demorar el cierre de la app.
        logger.info("La despedida al edge no pudo completarse.", exc_info=True)


async def run_edge_continuity(
    config: EdgeContinuityConfig,
    *,
    stop_event: asyncio.Event,
    state: EdgeContinuityState,
    version: str,
    capabilities: list[str],
    task_handler: Callable[[dict[str, Any], str], Awaitable[dict[str, Any]]],
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Mantiene heartbeat y cola activos hasta el apagado del runtime.

    El apagado limpio (``stop_event``) despide a la computadora ante el edge;
    un corte de luz no avisa nada y para eso está el TTL de latidos.
    """

    state.configured = True
    state.enabled = True

    heartbeat_settled = asyncio.Event()

    async with EdgeContinuityClient(config, transport=transport) as client:
        async def heartbeat_loop() -> None:
            backoff = 1.0
            try:
                while not stop_event.is_set():
                    try:
                        await client.heartbeat(capabilities=capabilities, version=version)
                        state.connected = True
                        state.last_heartbeat_at = _now_iso()
                        state.last_error = None
                        backoff = 1.0
                        await _wait_or_stop(stop_event, config.heartbeat_seconds)
                    except EdgeContinuityRequestError as exc:
                        state.connected = False
                        state.last_error = exc.code
                        logger.warning("Heartbeat de continuidad falló: %s.", exc.code)
                        await _wait_or_stop(stop_event, backoff)
                        backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
            finally:
                # Recién acá no puede salir ningún latido más; es la señal que
                # la despedida espera para no ser revivida por uno en vuelo.
                heartbeat_settled.set()

        async def claim_loop() -> None:
            backoff = 1.0
            while not stop_event.is_set():
                try:
                    jobs = await client.claim(max_messages=1)
                    state.last_claim_at = _now_iso()
                    state.connected = True
                    state.last_error = None
                    backoff = 1.0
                    if not jobs:
                        await _wait_or_stop(stop_event, config.claim_seconds)
                        continue
                    for raw_job in jobs:
                        if stop_event.is_set():
                            return
                        try:
                            job_id, receipt_handle, task = _safe_job(raw_job)
                        except EdgeContinuityRequestError as exc:
                            state.failed_jobs += 1
                            state.last_error = exc.code
                            logger.warning("El edge entregó un trabajo inválido: %s.", exc.code)
                            continue

                        try:
                            result = await task_handler(task, job_id)
                            completion_status = "done"
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            # El detalle queda en logs locales; el resultado
                            # remoto nunca expone stack, rutas ni credenciales.
                            logger.exception("Falló el trabajo privado %s.", job_id)
                            result = {"error": "No pude completar el trabajo en la computadora."}
                            completion_status = "error"

                        await client.complete(
                            job_id=job_id,
                            receipt_handle=receipt_handle,
                            status=completion_status,
                            result=result,
                        )
                        state.last_job_at = _now_iso()
                        if completion_status == "done":
                            state.completed_jobs += 1
                        else:
                            state.failed_jobs += 1
                except EdgeContinuityRequestError as exc:
                    state.connected = False
                    state.last_error = exc.code
                    logger.warning("Cola de continuidad no disponible: %s.", exc.code)
                    await _wait_or_stop(stop_event, backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)

        async def goodbye_when_stopped() -> None:
            await stop_event.wait()
            await _say_goodbye_safely(client, state=state, heartbeat_settled=heartbeat_settled)

        # La despedida arranca en cuanto se pide el apagado, no después de que
        # los loops terminen de desarmarse: el punto ciego que cierra son los
        # primeros segundos. Corriendo en paralelo tampoco suma nada al tiempo
        # que la app tarda en cerrarse.
        goodbye = asyncio.create_task(goodbye_when_stopped(), name="edecan-edge-goodbye")
        try:
            await asyncio.gather(heartbeat_loop(), claim_loop())
        finally:
            if not stop_event.is_set():
                # La continuidad terminó sin que nadie pidiera apagar (un loop
                # se cayó con la app encendida): no hay despedida que dar, y
                # que dejen de llegar latidos ya es señal suficiente.
                goodbye.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await goodbye


def _events_from_sse(body: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    event_name: str | None = None
    data_lines: list[str] = []

    def flush() -> None:
        nonlocal event_name, data_lines
        if not event_name or not data_lines:
            event_name = None
            data_lines = []
            return
        try:
            data = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            data = {"type": "error", "message": "Respuesta local inválida."}
        if isinstance(data, dict):
            events.append({"event": event_name, "data": data})
        event_name = None
        data_lines = []

    for line in body.splitlines():
        if not line:
            flush()
        elif line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    flush()
    return events


class LocalChatTaskHandler:
    """Ejecuta trabajos de chat contra el mismo API local que usa la UI."""

    def __init__(
        self,
        *,
        api_base_url: str,
        desktop_capability: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._desktop_capability = desktop_capability
        self._client = httpx.AsyncClient(
            base_url=f"{api_base_url.rstrip('/')}/",
            transport=transport,
            timeout=httpx.Timeout(300.0, connect=5.0),
            follow_redirects=False,
        )
        self._access_token: str | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _authenticate(self) -> str:
        response = await self._client.post(
            "v1/auth/local",
            headers={"x-edecan-desktop-capability": self._desktop_capability},
        )
        if response.status_code != 200:
            raise EdgeContinuityRequestError("local_auth_failed")
        payload = response.json()
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            raise EdgeContinuityRequestError("local_auth_invalid")
        self._access_token = token
        return token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> httpx.Response:
        token = self._access_token or await self._authenticate()
        headers = {"authorization": f"Bearer {token}"}
        if idempotency_key:
            headers["idempotency-key"] = idempotency_key
        response = await self._client.request(
            method,
            path.lstrip("/"),
            json=json_body,
            headers=headers,
        )
        if response.status_code == 401:
            token = await self._authenticate()
            headers["authorization"] = f"Bearer {token}"
            response = await self._client.request(
                method,
                path.lstrip("/"),
                json=json_body,
                headers=headers,
            )
        return response

    async def _new_conversation(self) -> str:
        response = await self._request(
            "POST",
            "/v1/conversations",
            json_body={"channel": "api"},
        )
        if response.status_code != 201:
            raise EdgeContinuityRequestError("local_conversation_create_failed")
        payload = response.json()
        conversation_id = payload.get("id") if isinstance(payload, dict) else None
        try:
            return str(uuid.UUID(str(conversation_id)))
        except (TypeError, ValueError) as exc:
            raise EdgeContinuityRequestError("local_conversation_invalid") from exc

    async def __call__(self, task: dict[str, Any], job_id: str) -> dict[str, Any]:
        if task.get("type") == "continuity_sync":
            try:
                event_id = str(uuid.UUID(str(task.get("event_id"))))
                conversation_id = str(uuid.UUID(str(task.get("conversation_id"))))
            except (TypeError, ValueError) as exc:
                raise EdgeContinuityRequestError("invalid_continuity_sync") from exc
            user_message = str(task.get("user_message") or "").strip()
            assistant_message = str(task.get("assistant_message") or "").strip()
            if (
                not user_message
                or len(user_message) > 20_000
                or not assistant_message
                or len(assistant_message) > 50_000
            ):
                raise EdgeContinuityRequestError("invalid_continuity_sync")
            response = await self._request(
                "POST",
                "/v1/continuity/sync",
                json_body={
                    "event_id": event_id,
                    "conversation_id": conversation_id,
                    "user_message": user_message,
                    "assistant_message": assistant_message,
                },
                idempotency_key=job_id,
            )
            if response.status_code != 200:
                raise EdgeContinuityRequestError(
                    f"local_continuity_sync_http_{response.status_code}"
                )
            payload = response.json()
            if not isinstance(payload, dict):
                raise EdgeContinuityRequestError("local_continuity_sync_invalid")
            return payload

        if task.get("type") != "chat":
            raise EdgeContinuityRequestError("unsupported_edge_task")
        text = str(task.get("message") or "").strip()
        if not text or len(text) > 50_000:
            raise EdgeContinuityRequestError("invalid_chat_task")

        requested_id = task.get("conversation_id")
        try:
            conversation_id = str(uuid.UUID(str(requested_id))) if requested_id else None
        except (TypeError, ValueError):
            conversation_id = None
        if conversation_id is None:
            conversation_id = await self._new_conversation()

        response = await self._request(
            "POST",
            f"/v1/conversations/{conversation_id}/messages",
            json_body={"text": text, "attachments": []},
            idempotency_key=job_id,
        )
        if response.status_code == 404:
            conversation_id = await self._new_conversation()
            response = await self._request(
                "POST",
                f"/v1/conversations/{conversation_id}/messages",
                json_body={"text": text, "attachments": []},
                idempotency_key=job_id,
            )
        if response.status_code != 200:
            raise EdgeContinuityRequestError(f"local_chat_http_{response.status_code}")

        events = _events_from_sse(response.text)
        deltas = [
            str(event["data"].get("text") or "")
            for event in events
            if event["event"] == "message.delta"
        ]
        errors = [
            str(event["data"].get("message") or "")
            for event in events
            if event["event"] == "error"
        ]
        message = "".join(deltas).strip()
        if errors and not message:
            raise EdgeContinuityRequestError("local_chat_failed")
        if not any(event["event"] == "message.done" for event in events):
            raise EdgeContinuityRequestError("local_chat_incomplete")
        return {
            "conversation_id": conversation_id,
            "message": message,
            "events": events,
        }
