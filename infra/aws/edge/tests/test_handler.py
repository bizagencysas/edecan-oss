from __future__ import annotations

import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from botocore.exceptions import ClientError

os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("STATE_TABLE", "test-state")
os.environ.setdefault("JOBS_QUEUE_URL", "https://sqs.example.test/jobs")
os.environ.setdefault("SHARED_SECRET_ARN", "test-secret")

from infra.aws.edge.src import handler  # noqa: E402


class FakeTable:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}
        self.lock = threading.Lock()

    def get_item(self, *, Key: dict[str, str]) -> dict[str, Any]:
        item = self.items.get((Key["pk"], Key["sk"]))
        return {"Item": dict(item)} if item is not None else {}

    def put_item(
        self,
        *,
        Item: dict[str, Any],
        ConditionExpression: str | None = None,
    ) -> None:
        key = (Item["pk"], Item["sk"])
        with self.lock:
            if ConditionExpression and key in self.items:
                raise ClientError(
                    {
                        "Error": {
                            "Code": "ConditionalCheckFailedException",
                            "Message": "reserved",
                        }
                    },
                    "PutItem",
                )
            self.items[key] = dict(Item)

    def update_item(
        self,
        *,
        Key: dict[str, str],
        UpdateExpression: str,
        ExpressionAttributeValues: dict[str, Any],
        ExpressionAttributeNames: dict[str, str] | None = None,
        ConditionExpression: str | None = None,
    ) -> None:
        names = ExpressionAttributeNames or {}
        key = (Key["pk"], Key["sk"])
        status_name = names.get("#status")
        if ConditionExpression:
            current = self.items.get(key) or {}
            if ConditionExpression == "attribute_exists(last_seen_at)":
                satisfied = "last_seen_at" in current
            elif ":expected_last_seen" in ExpressionAttributeValues:
                satisfied = current.get("last_seen_at") == (
                    ExpressionAttributeValues[":expected_last_seen"]
                )
            else:
                satisfied = current.get(status_name or "status") == (
                    ExpressionAttributeValues.get(":expected_status")
                )
            if not satisfied:
                # DynamoDB no crea el item cuando la condición falla, y de eso
                # depende que una despedida sin latido previo no deje basura.
                raise ClientError(
                    {
                        "Error": {
                            "Code": "ConditionalCheckFailedException",
                            "Message": "changed",
                        }
                    },
                    "UpdateItem",
                )
        item = self.items.setdefault(key, dict(Key))
        if status_name and ":status" in ExpressionAttributeValues:
            item[status_name] = ExpressionAttributeValues[":status"]
        for placeholder, name in (
            (":claimed_at", "claimed_at"),
            (":completed_at", "completed_at"),
            (":result_json", "result_json"),
            (":expires_at", "expires_at"),
            (":updated_at", "updated_at"),
            (":goodbye_at", "goodbye_at"),
        ):
            if placeholder in ExpressionAttributeValues:
                item[name] = ExpressionAttributeValues[placeholder]
        if ":one" in ExpressionAttributeValues:
            item["claim_attempts"] = int(item.get("claim_attempts") or 0) + int(
                ExpressionAttributeValues[":one"]
            )


class FakeSqs:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.deleted: list[str] = []

    def send_message(self, **kwargs: Any) -> None:
        self.messages.append(
            {
                "Body": kwargs["MessageBody"],
                "ReceiptHandle": f"receipt-{len(self.messages) + 1}",
                "MessageGroupId": kwargs["MessageGroupId"],
                "MessageDeduplicationId": kwargs["MessageDeduplicationId"],
            }
        )

    def receive_message(self, **_kwargs: Any) -> dict[str, Any]:
        return {"Messages": list(self.messages)}

    def change_message_visibility(self, **_kwargs: Any) -> None:
        return None

    def delete_message(self, **kwargs: Any) -> None:
        self.deleted.append(kwargs["ReceiptHandle"])
        self.messages = [
            item for item in self.messages if item["ReceiptHandle"] != kwargs["ReceiptHandle"]
        ]


class FakeBedrock:
    def __init__(self, text: str = "Respuesta básica mientras vuelves.") -> None:
        self.text = text
        self.calls = 0

    def converse(self, **_kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        return {"output": {"message": {"content": [{"text": self.text}]}}}


SHARED_SECRET = "capacidad-de-prueba-suficientemente-larga"


def _event(body: dict[str, Any], *, installation: str = "installation") -> dict[str, Any]:
    return {
        "headers": {"x-edecan-installation": installation},
        "body": json.dumps(body),
        "isBase64Encoded": False,
    }


def _request(
    path: str,
    body: dict[str, Any] | None = None,
    *,
    method: str = "POST",
    installation: str = "installation",
    token: str | None = SHARED_SECRET,
) -> dict[str, Any]:
    """Evento de API Gateway completo, para entrar por ``lambda_handler``."""

    headers = {"x-edecan-installation": installation}
    if token is not None:
        headers["authorization"] = f"Bearer {token}"
    return {
        "requestContext": {"http": {"method": method, "path": path}},
        "headers": headers,
        "body": json.dumps(body or {}),
        "isBase64Encoded": False,
    }


def _edge(monkeypatch) -> tuple[FakeTable, FakeSqs, FakeBedrock]:
    table = FakeTable()
    sqs = FakeSqs()
    bedrock = FakeBedrock()
    monkeypatch.setattr(handler, "table", table)
    monkeypatch.setattr(handler, "sqs", sqs)
    monkeypatch.setattr(handler, "bedrock", bedrock)
    monkeypatch.setattr(handler, "_shared_secret", SHARED_SECRET)
    return table, sqs, bedrock


def _beat(installation: str = "installation") -> int:
    """Late y devuelve el ``server_time`` que la computadora debe recordar."""

    response = handler.lambda_handler(
        _request(
            "/v1/edge/heartbeat",
            {"capabilities": ["chat"], "version": "1.0.0"},
            installation=installation,
        ),
        None,
    )
    assert response["statusCode"] == 200
    return int(json.loads(response["body"])["server_time"])


def test_online_chat_is_idempotent_and_preserves_conversation(monkeypatch) -> None:
    table = FakeTable()
    sqs = FakeSqs()
    installation = "installation"
    table.put_item(
        Item={
            "pk": f"INSTALLATION#{installation}",
            "sk": "DESKTOP",
            "last_seen_at": int(time.time()),
            "capabilities": ["chat"],
        }
    )
    monkeypatch.setattr(handler, "table", table)
    monkeypatch.setattr(handler, "sqs", sqs)
    monkeypatch.setattr(handler, "_shared_secret", SHARED_SECRET)
    idempotency_key = str(uuid.uuid4())
    conversation_id = str(uuid.uuid4())
    event = _event(
        {
            "message": "Hola",
            "conversation_id": conversation_id,
            "idempotency_key": idempotency_key,
        }
    )

    first = handler._offline_chat(event)
    second = handler._offline_chat(event)

    assert first["statusCode"] == 202
    assert second["statusCode"] == 202
    assert json.loads(second["body"])["deduplicated"] is True
    assert len(sqs.messages) == 1
    queued = json.loads(sqs.messages[0]["Body"])
    # Derivado del secreto del servidor, no la clave del cliente: el `job_id`
    # viaja de vuelta y es lo único con lo que se consulta el trabajo.
    assert queued["job_id"] == handler._job_id_desde_idempotencia(
        installation, idempotency_key
    )
    assert queued["task"] == {
        "type": "chat",
        "message": "Hola",
        "conversation_id": conversation_id,
    }

    claimed = handler._claim(_event({"max_messages": 1}))
    claimed_job = json.loads(claimed["body"])["jobs"][0]
    # El `job_id` ya NO es la clave de idempotencia: se deriva con el secreto
    # del servidor, porque `_job_status` filtra solo por instalación y quien
    # pudiera nombrar una clave leía el trabajo entero. Lo que sí tiene que
    # seguir cumpliéndose es que la MISMA clave lleve al MISMO trabajo.
    job_id = claimed_job["job_id"]
    assert job_id != idempotency_key
    assert job_id == handler._job_id_desde_idempotencia(installation, idempotency_key)
    stored = table.items[(f"INSTALLATION#{installation}", f"JOB#{job_id}")]
    assert stored["status"] == "claimed"
    assert stored["claim_attempts"] == 1

    completed = handler._complete(
        _event(
            {
                "job_id": idempotency_key,
                "receipt_handle": claimed_job["receipt_handle"],
                "status": "done",
                "result": {"message": "Listo"},
            }
        )
    )
    assert completed["statusCode"] == 200
    status = handler._job_status(
        {"headers": {"x-edecan-installation": installation}},
        idempotency_key,
    )
    assert json.loads(status["body"])["job"]["result"] == {"message": "Listo"}
    assert not sqs.messages


def test_concurrent_enqueue_reserves_and_sends_only_once(monkeypatch) -> None:
    table = FakeTable()
    sqs = FakeSqs()
    monkeypatch.setattr(handler, "table", table)
    monkeypatch.setattr(handler, "sqs", sqs)
    monkeypatch.setattr(handler, "_shared_secret", SHARED_SECRET)
    idempotency_key = str(uuid.uuid4())
    event = _event(
        {
            "idempotency_key": idempotency_key,
            "task": {"type": "chat", "message": "Una sola vez"},
        }
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _index: handler._enqueue(event), range(2)))

    assert [response["statusCode"] for response in responses] == [202, 202]
    assert len(sqs.messages) == 1
    # La deduplicación de SQS va por el `job_id` derivado, no por la clave cruda.
    # Sigue deduplicando igual porque la derivación es determinista (misma
    # instalación + misma clave -> mismo id), y de paso no publica la clave del
    # cliente en un identificador de infraestructura.
    assert sqs.messages[0]["MessageDeduplicationId"] == handler._job_id_desde_idempotencia(
        "installation", idempotency_key
    )
    assert sqs.messages[0]["MessageGroupId"] == "installation"
    assert sum(
        bool(json.loads(response["body"]).get("deduplicated"))
        for response in responses
    ) == 1


def test_enqueue_failure_can_retry_same_idempotency_key(monkeypatch) -> None:
    table = FakeTable()

    class FailOnceSqs(FakeSqs):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

        def send_message(self, **kwargs: Any) -> None:
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("temporary sqs failure")
            super().send_message(**kwargs)

    sqs = FailOnceSqs()
    monkeypatch.setattr(handler, "table", table)
    monkeypatch.setattr(handler, "sqs", sqs)
    monkeypatch.setattr(handler, "_shared_secret", SHARED_SECRET)
    idempotency_key = str(uuid.uuid4())
    event = _event(
        {
            "idempotency_key": idempotency_key,
            "task": {"type": "chat", "message": "Recuperable"},
        }
    )

    with pytest.raises(RuntimeError, match="temporary"):
        handler._enqueue(event)

    job_id = handler._job_id_desde_idempotencia("installation", idempotency_key)
    assert job_id != idempotency_key
    stored = table.items[("INSTALLATION#installation", f"JOB#{job_id}")]
    assert stored["status"] == "enqueue_error"

    response = handler._enqueue(event)

    assert response["statusCode"] == 202
    assert json.loads(response["body"])["status"] == "queued"
    assert sqs.attempts == 2
    assert len(sqs.messages) == 1


def test_offline_chat_queues_durable_reconciliation(monkeypatch) -> None:
    table = FakeTable()
    sqs = FakeSqs()
    monkeypatch.setattr(handler, "table", table)
    monkeypatch.setattr(handler, "sqs", sqs)
    monkeypatch.setattr(handler, "_shared_secret", SHARED_SECRET)
    monkeypatch.setattr(handler, "bedrock", FakeBedrock())
    conversation_id = str(uuid.uuid4())

    response = handler._offline_chat(
        _event({"message": "Hola offline", "conversation_id": conversation_id})
    )

    assert response["statusCode"] == 200
    payload = json.loads(response["body"])
    assert payload["mode"] == "offline_continuity"
    assert payload["conversation_id"] == conversation_id
    assert len(sqs.messages) == 1
    queued = json.loads(sqs.messages[0]["Body"])
    # `event_id` identifica el MENSAJE que la computadora debe reconciliar;
    # `job_id` identifica el TRABAJO de reconciliarlo. Coincidían solo porque el
    # job_id era la clave de idempotencia, que aquí era el id del mensaje; ahora
    # que el job_id se deriva con el secreto del servidor son cosas distintas, y
    # atarlas en un test volvería a acoplarlas.
    uuid.UUID(queued["task"]["event_id"])
    assert queued["task"] == {
        "type": "continuity_sync",
        "event_id": queued["task"]["event_id"],
        "conversation_id": conversation_id,
        "user_message": "Hola offline",
        "assistant_message": "Respuesta básica mientras vuelves.",
    }



def test_goodbye_marks_the_desktop_absent_and_chat_answers_at_once(monkeypatch) -> None:
    """El punto ciego del TTL: cierra la computadora y pregunta enseguida."""

    _table, sqs, bedrock = _edge(monkeypatch)
    server_time = _beat()
    assert handler._desktop_state("installation")["online"] is True

    farewell = handler.lambda_handler(
        _request("/v1/edge/goodbye", {"last_seen_at": server_time}), None
    )

    assert farewell["statusCode"] == 200
    assert json.loads(farewell["body"])["applied"] is True
    # El TTL sigue intacto: el último latido está fresco y aun así está ausente.
    assert int(time.time()) - server_time < handler.HEARTBEAT_TTL_SECONDS
    assert handler._desktop_state("installation")["online"] is False

    chat = handler.lambda_handler(_request("/v1/edge/chat", {"message": "¿Me lees?"}), None)

    payload = json.loads(chat["body"])
    assert chat["statusCode"] == 200
    assert payload["mode"] == "offline_continuity"
    assert payload["message"] == "Respuesta básica mientras vuelves."
    assert bedrock.calls == 1
    # Nada de "quedó en la cola": lo único encolado es la reconciliación.
    assert [json.loads(item["Body"])["task"]["type"] for item in sqs.messages] == [
        "continuity_sync"
    ]


def test_goodbye_without_a_session_token_still_marks_the_desktop_absent(monkeypatch) -> None:
    """El token es opcional: un cliente que no lo tenga igual puede despedirse."""

    table, _sqs, bedrock = _edge(monkeypatch)
    _beat()

    farewell = handler.lambda_handler(_request("/v1/edge/goodbye", {}), None)

    assert json.loads(farewell["body"])["applied"] is True
    assert table.items[("INSTALLATION#installation", "DESKTOP")]["goodbye_at"] > 0
    assert handler._desktop_state("installation")["online"] is False

    chat = handler.lambda_handler(_request("/v1/edge/chat", {"message": "¿Me lees?"}), None)

    assert json.loads(chat["body"])["mode"] == "offline_continuity"
    assert bedrock.calls == 1


def test_a_goodbye_in_the_same_second_as_the_last_heartbeat_counts(monkeypatch) -> None:
    """Caso real, no teórico: la persona cierra la app justo después de un latido."""

    table, _sqs, _bedrock = _edge(monkeypatch)
    now = int(time.time())
    table.items[("INSTALLATION#installation", "DESKTOP")] = {
        "pk": "INSTALLATION#installation",
        "sk": "DESKTOP",
        "last_seen_at": now,
        "goodbye_at": now,
    }

    # El empate lo gana la despedida: siempre se escribe después del latido con
    # el que cierra, así que exigirla estrictamente posterior reabriría el punto
    # ciego durante ese segundo.
    assert handler._desktop_state("installation")["online"] is False


def test_heartbeat_after_goodbye_revives_the_desktop(monkeypatch) -> None:
    _table, sqs, bedrock = _edge(monkeypatch)
    handler.lambda_handler(_request("/v1/edge/goodbye", {"last_seen_at": _beat()}), None)
    assert handler._desktop_state("installation")["online"] is False

    _beat()

    state = handler._desktop_state("installation")
    assert state["online"] is True
    assert state["goodbye_at"] is None

    chat = handler.lambda_handler(_request("/v1/edge/chat", {"message": "Ya volviste"}), None)

    assert chat["statusCode"] == 202
    assert bedrock.calls == 0
    assert [json.loads(item["Body"])["task"]["type"] for item in sqs.messages] == ["chat"]


def test_ttl_still_covers_the_crashes_that_never_say_goodbye(monkeypatch) -> None:
    table, _sqs, bedrock = _edge(monkeypatch)
    desktop_key = ("INSTALLATION#installation", "DESKTOP")
    _beat()

    # Un bache de red no puede degradar a nadie: sin despedida y con el latido
    # dentro del margen, la computadora sigue viva y el chat sigue siendo suyo.
    table.items[desktop_key]["last_seen_at"] = int(time.time()) - (
        handler.HEARTBEAT_TTL_SECONDS - 5
    )
    assert handler._desktop_state("installation")["online"] is True

    # Caída sucia (batería, kernel panic, cable): nadie alcanzó a despedirse y
    # el TTL sigue siendo la red de seguridad.
    table.items[desktop_key]["last_seen_at"] = int(time.time()) - (
        handler.HEARTBEAT_TTL_SECONDS + 1
    )
    assert handler._desktop_state("installation")["online"] is False

    chat = handler.lambda_handler(_request("/v1/edge/chat", {"message": "Se fue la luz"}), None)

    assert json.loads(chat["body"])["mode"] == "offline_continuity"
    assert bedrock.calls == 1


def test_the_heartbeat_ttl_is_not_shortened_as_a_shortcut() -> None:
    """La despedida cierra el punto ciego; acortar el TTL sería otra cosa.

    Con un TTL corto, un bache de red manda la conversación al modelo de
    emergencia con la computadora encendida: peor que esperar. El resto de la
    suite mide contra la constante, así que bajarla no rompe nada -- este es el
    único test que lo nota.
    """

    assert handler.HEARTBEAT_TTL_SECONDS >= 90


def test_goodbye_without_the_shared_secret_cannot_take_down_a_desktop(monkeypatch) -> None:
    """Sin la capacidad privada nadie puede empujar a otro al modelo débil."""

    table, sqs, bedrock = _edge(monkeypatch)
    server_time = _beat()

    intrusos = [
        handler.lambda_handler(
            _request("/v1/edge/goodbye", {"last_seen_at": server_time}, token="otra-cosa"),
            None,
        ),
        handler.lambda_handler(
            _request("/v1/edge/goodbye", {"last_seen_at": server_time}, token=None),
            None,
        ),
    ]

    assert [response["statusCode"] for response in intrusos] == [401, 401]
    assert {json.loads(response["body"])["error"] for response in intrusos} == {"unauthorized"}
    assert "goodbye_at" not in table.items[("INSTALLATION#installation", "DESKTOP")]
    assert handler._desktop_state("installation")["online"] is True

    chat = handler.lambda_handler(_request("/v1/edge/chat", {"message": "Hola"}), None)

    # El chat del dueño sigue yendo a su computadora, no al modelo de emergencia.
    assert chat["statusCode"] == 202
    assert bedrock.calls == 0
    assert [json.loads(item["Body"])["task"]["type"] for item in sqs.messages] == ["chat"]


def test_a_phone_cannot_declare_the_desktop_absent_through_the_worker(monkeypatch) -> None:
    """El secreto compartido NO es exclusivo de la computadora.

    El worker de Cloudflare lo tiene (`AWS_ORIGIN_KEY`) y reemplaza con él la
    autorización de cualquier dispositivo que traiga un token de sesión válido,
    dejando la del dispositivo en `x-edecan-device-authorization`. Así se ve un
    teléfono pidiendo la despedida: con el secreto puesto por el worker. La
    computadora habla directo con API Gateway y nunca manda esa cabecera.
    """

    table, sqs, bedrock = _edge(monkeypatch)
    server_time = _beat()

    desde_el_telefono = _request("/v1/edge/goodbye", {"last_seen_at": server_time})
    desde_el_telefono["headers"]["x-edecan-device-authorization"] = "Bearer token.del.telefono"

    respuesta = handler.lambda_handler(desde_el_telefono, None)

    assert respuesta["statusCode"] == 403
    assert "goodbye_at" not in table.items[("INSTALLATION#installation", "DESKTOP")]
    assert handler._desktop_state("installation")["online"] is True

    # Lo que importa no es el 403 sino esto: el dueño sigue hablando con su
    # computadora y no con el modelo de emergencia.
    chat = handler.lambda_handler(_request("/v1/edge/chat", {"message": "Hola"}), None)
    assert chat["statusCode"] == 202
    assert bedrock.calls == 0
    assert [json.loads(item["Body"])["task"]["type"] for item in sqs.messages] == ["chat"]

    # Y la computadora, que sí habla directo, se sigue despidiendo igual.
    propia = handler.lambda_handler(
        _request("/v1/edge/goodbye", {"last_seen_at": server_time}), None
    )
    assert json.loads(propia["body"])["applied"] is True
    assert handler._desktop_state("installation")["online"] is False


def test_a_late_goodbye_cannot_take_down_a_desktop_that_came_back(monkeypatch) -> None:
    table, _sqs, _bedrock = _edge(monkeypatch)
    server_time = _beat()

    # Aviso demorado o reintentado de una sesión anterior: la computadora ya
    # volvió y latió de nuevo, así que la despedida llegó vieja.
    tardia = handler.lambda_handler(
        _request("/v1/edge/goodbye", {"last_seen_at": server_time - 5}), None
    )

    payload = json.loads(tardia["body"])
    assert tardia["statusCode"] == 200
    assert payload["applied"] is False
    assert payload["reason"] == "sin_latido_coincidente"
    assert payload["desktop"]["online"] is True
    assert "goodbye_at" not in table.items[("INSTALLATION#installation", "DESKTOP")]


def test_goodbye_never_invents_an_installation(monkeypatch) -> None:
    table, _sqs, _bedrock = _edge(monkeypatch)

    desconocida = handler.lambda_handler(
        _request("/v1/edge/goodbye", {}, installation="jamas-latio"),
        None,
    )

    assert json.loads(desconocida["body"])["applied"] is False
    # Un item sin latido previo tampoco tendría TTL: no se crea.
    assert ("INSTALLATION#jamas-latio", "DESKTOP") not in table.items

    roto = handler.lambda_handler(
        _request("/v1/edge/goodbye", {"last_seen_at": "ayer"}), None
    )

    assert roto["statusCode"] == 400
    assert json.loads(roto["body"])["error"] == "invalid_request"


def test_el_job_id_no_es_la_clave_que_manda_el_cliente(monkeypatch) -> None:
    """Quien nombra una clave de idempotencia no puede leer ese trabajo.

    `_job_status` filtra SOLO por instalación -- nunca por dueño ni por
    dispositivo. Mientras el `job_id` FUE la clave que mandaba el cliente,
    cualquiera que la conociera o la eligiera leía el `task` y el `result`
    completos. Y una clave de idempotencia no es un secreto: se elige para poder
    reintentar, viaja en cuerpos de petición y termina en logs.

    Lo que tiene que seguir cumpliéndose es la idempotencia: misma instalación y
    misma clave -> mismo trabajo, para que un reintento no duplique. Por eso la
    derivación es determinista y no un `uuid4()` nuevo cada vez.
    """
    monkeypatch.setattr(handler, "_shared_secret", SHARED_SECRET)
    clave = "2cc5bdbe-e76e-4411-92fd-0948ae9c5373"

    derivado = handler._job_id_desde_idempotencia("installation", clave)

    assert derivado != clave, "el id sigue siendo la clave del cliente"
    assert derivado == handler._job_id_desde_idempotencia("installation", clave), (
        "la derivación no es determinista: un reintento crearía un trabajo nuevo"
    )
    # Dos instalaciones distintas con la MISMA clave no comparten trabajo: si no,
    # una podría leer el de la otra sabiendo solo la clave.
    assert derivado != handler._job_id_desde_idempotencia("otra-instalacion", clave)
    # Y sin el secreto del servidor el id no se puede calcular.
    monkeypatch.setattr(handler, "_shared_secret", "otro-secreto-suficientemente-largo")
    assert derivado != handler._job_id_desde_idempotencia("installation", clave)
