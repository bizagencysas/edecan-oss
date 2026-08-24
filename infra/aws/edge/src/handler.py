"""Edecán Edge: continuidad serverless, no reemplazo del runtime local."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from typing import Any

import boto3
from botocore.exceptions import ClientError

STATE_TABLE = os.environ["STATE_TABLE"]
JOBS_QUEUE_URL = os.environ["JOBS_QUEUE_URL"]
SHARED_SECRET_ARN = os.environ["SHARED_SECRET_ARN"]
INSTALLATION_NAME = os.environ.get("INSTALLATION_NAME", "edecan-personal")
OFFLINE_MODEL_ID = os.environ.get("OFFLINE_MODEL_ID", "amazon.nova-2-lite-v1:0")
HEARTBEAT_TTL_SECONDS = int(os.environ.get("HEARTBEAT_TTL_SECONDS", "90"))
# El worker de Cloudflare multiplexa a TODOS los dispositivos del dueño contra
# este origen con una sola credencial (su `AWS_ORIGIN_KEY` es el mismo secreto
# compartido) y, cuando lo hace, traslada la autorización del dispositivo a esta
# cabecera. La computadora no pasa por ahí: `configure-local.sh` le escribe el
# `ApiUrl` del stack, así que habla directo con API Gateway y nunca la manda.
# Su presencia es, por lo tanto, la señal de que quien pide no es la computadora
# sino un dispositivo hablando por ella.
DEVICE_PROXY_HEADER = "x-edecan-device-authorization"

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(STATE_TABLE)
sqs = boto3.client("sqs")
secrets = boto3.client("secretsmanager")
bedrock = boto3.client("bedrock-runtime")
_shared_secret: str | None = None


def _response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json; charset=utf-8",
            "cache-control": "no-store",
        },
        "body": json.dumps(body, ensure_ascii=False, default=str),
    }


def _secret() -> str:
    global _shared_secret
    if _shared_secret is None:
        value = secrets.get_secret_value(SecretId=SHARED_SECRET_ARN)
        _shared_secret = value.get("SecretString") or base64.b64decode(
            value["SecretBinary"]
        ).decode()
    return _shared_secret


def _headers(event: dict[str, Any]) -> dict[str, str]:
    return {str(key).lower(): str(value) for key, value in (event.get("headers") or {}).items()}


def _authorized(event: dict[str, Any]) -> bool:
    headers = _headers(event)
    supplied = headers.get("authorization", "")
    if not supplied.lower().startswith("bearer "):
        return False
    return hmac.compare_digest(supplied[7:].strip(), _secret())


def _body(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("El cuerpo debe ser un objeto JSON.")
    return parsed


def _installation_id(event: dict[str, Any], body: dict[str, Any] | None = None) -> str:
    headers = _headers(event)
    value = headers.get("x-edecan-installation") or (body or {}).get("installation_id")
    value = str(value or INSTALLATION_NAME).strip()
    if not value or len(value) > 128:
        raise ValueError("installation_id inválido.")
    return value


def _job_id_desde_idempotencia(installation_id: str, idempotency_key: str) -> str:
    """`job_id` derivado de la clave de idempotencia, pero NO igual a ella.

    Antes el `job_id` ERA la clave que mandaba el cliente
    (`str(uuid.UUID(idempotency_key))`), y `_job_status` filtra solo por
    instalación: nunca por dueño ni por dispositivo. Con las dos cosas juntas,
    quien pudiera nombrar una clave leía el `task` y el `result` de ese trabajo
    -- y una clave de idempotencia no es un secreto: se elige para poder
    reintentar, viaja en cuerpos de petición y termina en logs.

    Derivarla con el secreto compartido conserva lo que la idempotencia necesita
    (misma instalación + misma clave -> mismo trabajo, así un reintento no
    duplica) y le quita lo que sobraba: ahora hace falta el secreto del servidor
    para calcular el identificador.

    NO es todavía una autorización por dispositivo: para eso habría que sellar el
    trabajo al `sub` del token, que el worker ya reenvía en
    `DEVICE_PROXY_HEADER`. Esto cierra el camino barato; el sellado por dueño
    queda pendiente.
    """
    uuid.UUID(idempotency_key)  # valida el formato; lanza ValueError si no lo es
    espacio = uuid.uuid5(uuid.NAMESPACE_URL, f"edecan-job:{_secret()}")
    return str(uuid.uuid5(espacio, f"{installation_id}:{idempotency_key}"))


def _desktop_state(installation_id: str) -> dict[str, Any]:
    result = table.get_item(Key={"pk": f"INSTALLATION#{installation_id}", "sk": "DESKTOP"})
    item = result.get("Item") or {}
    last_seen = int(item.get("last_seen_at") or 0)
    goodbye = int(item.get("goodbye_at") or 0)
    fresh = last_seen > 0 and int(time.time()) - last_seen <= HEARTBEAT_TTL_SECONDS
    # La despedida explícita gana sobre el TTL: la computadora ya avisó que se
    # apagaba y esperar a que caduque su último latido deja hasta
    # HEARTBEAT_TTL_SECONDS encolando trabajo que nadie va a reclamar. Se guarda
    # como fecha y no como bandera para que el latido de la vuelta la deje atrás
    # sola; en un empate de segundo gana la despedida, porque siempre se escribe
    # después del latido con el que cierra.
    announced_goodbye = goodbye > 0 and goodbye >= last_seen
    return {
        "online": fresh and not announced_goodbye,
        "last_seen_at": last_seen or None,
        "goodbye_at": goodbye or None,
        "capabilities": item.get("capabilities") or [],
        "version": item.get("version"),
    }


def _heartbeat(event: dict[str, Any]) -> dict[str, Any]:
    body = _body(event)
    installation_id = _installation_id(event, body)
    now = int(time.time())
    capabilities = body.get("capabilities") or []
    if not isinstance(capabilities, list) or len(capabilities) > 100:
        raise ValueError("capabilities inválidas.")
    table.put_item(
        Item={
            "pk": f"INSTALLATION#{installation_id}",
            "sk": "DESKTOP",
            "last_seen_at": now,
            "expires_at": now + 86400 * 30,
            "capabilities": [str(value)[:80] for value in capabilities],
            "version": str(body.get("version") or "")[:40],
        }
    )
    return _response(200, {"ok": True, "online": True, "server_time": now})


def _conditional_write_failed(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def _goodbye(event: dict[str, Any]) -> dict[str, Any]:
    """Despedida explícita: la computadora avisa que se está apagando.

    HEARTBEAT_TTL_SECONDS no se puede acortar. Ese margen es lo que tolera un
    bache de red, y sin él un hipo de conexión mandaría a la persona al modelo
    de emergencia con la computadora perfectamente viva, que es peor que el bug
    que se está arreglando: hoy espera; así recibiría respuestas malas sin saber
    por qué. Este aviso cierra el punto ciego por el otro lado, marcando ausente
    en el acto en los apagados limpios, y deja el TTL entero como red de
    seguridad para los sucios (batería, kernel panic, cable).

    Autenticación: el ``_authorized`` de ``lambda_handler`` (el secreto
    compartido) MÁS la exigencia de que el aviso venga directo de la
    computadora. El secreto solo no alcanza: el worker de Cloudflare lo tiene
    también, y lo usa para hablar por cualquier dispositivo del dueño que
    presente un token de sesión. Sin la segunda condición, un teléfono
    -- o un token robado -- declara ausente una computadora encendida y deja al
    dueño conversando con el modelo de emergencia, que es exactamente la
    degradación que este endpoint existe para evitar. Declararse ausente es una
    decisión que solo puede tomar la computadora sobre sí misma.

    ``last_seen_at`` es opcional y no es una credencial (``/v1/edge/status`` lo
    publica): es el ``server_time`` que devolvió el último latido, usado como
    token de sesión contra el desorden, no contra un atacante. Cuando viene, la
    despedida solo se aplica a esa sesión, de modo que un aviso demorado o
    reintentado no puede apagar una computadora que ya volvió.
    """

    if _headers(event).get(DEVICE_PROXY_HEADER):
        return _response(403, {"error": "solo_la_computadora_se_despide"})
    body = _body(event)
    installation_id = _installation_id(event, body)
    now = int(time.time())
    values: dict[str, Any] = {":goodbye_at": now}
    raw_last_seen = body.get("last_seen_at")
    if raw_last_seen is None or str(raw_last_seen).strip() == "":
        # Sin token de sesión al menos se exige que la instalación exista, para
        # no crear un item fantasma (sin TTL) desde un id cualquiera.
        condition = "attribute_exists(last_seen_at)"
    else:
        try:
            expected_last_seen = int(str(raw_last_seen).strip())
        except ValueError as exc:
            raise ValueError(
                "last_seen_at inválido: es el server_time del último latido."
            ) from exc
        if expected_last_seen <= 0:
            raise ValueError("last_seen_at inválido.")
        condition = "last_seen_at = :expected_last_seen"
        values[":expected_last_seen"] = expected_last_seen
    try:
        table.update_item(
            Key={"pk": f"INSTALLATION#{installation_id}", "sk": "DESKTOP"},
            UpdateExpression="SET goodbye_at = :goodbye_at",
            ConditionExpression=condition,
            ExpressionAttributeValues=values,
        )
    except ClientError as exc:
        if not _conditional_write_failed(exc):
            raise
        # O esa instalación nunca latió, o ya llegó un latido más nuevo que el
        # de la sesión que se despide. No es un error: la computadora que se
        # está apagando no tiene nada que reintentar y el TTL sigue cubriendo.
        return _response(
            200,
            {
                "ok": True,
                "applied": False,
                "reason": "sin_latido_coincidente",
                "desktop": _desktop_state(installation_id),
                "server_time": now,
            },
        )
    return _response(200, {"ok": True, "applied": True, "online": False, "server_time": now})


def _existing_job_response(installation_id: str, job_id: str) -> dict[str, Any]:
    existing = table.get_item(
        Key={"pk": f"INSTALLATION#{installation_id}", "sk": f"JOB#{job_id}"}
    ).get("Item")
    existing_status = str((existing or {}).get("status") or "enqueuing")
    return _response(
        200 if existing_status in {"done", "error"} else 202,
        {
            "job_id": job_id,
            "status": existing_status,
            "desktop": _desktop_state(installation_id),
            "deduplicated": True,
        },
    )


def _enqueue(event: dict[str, Any]) -> dict[str, Any]:
    body = _body(event)
    installation_id = _installation_id(event, body)
    task = body.get("task")
    if not isinstance(task, dict):
        raise ValueError("task debe ser un objeto.")
    encoded = json.dumps(task, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode()) > 240_000:
        raise ValueError("La tarea es demasiado grande.")
    idempotency_key = str(body.get("idempotency_key") or "").strip()
    if idempotency_key:
        try:
            job_id = _job_id_desde_idempotencia(installation_id, idempotency_key)
        except ValueError as exc:
            raise ValueError("idempotency_key inválida.") from exc
    else:
        job_id = str(uuid.uuid4())
    now = int(time.time())
    state = _desktop_state(installation_id)
    job_key = {
        "pk": f"INSTALLATION#{installation_id}",
        "sk": f"JOB#{job_id}",
    }
    try:
        # La reserva ocurre antes de SQS y es atómica en DynamoDB. Dos Lambdas
        # concurrentes con la misma idempotency_key no pueden ganar ambas.
        table.put_item(
            Item={
                **job_key,
                "status": "enqueuing",
                "created_at": now,
                "updated_at": now,
                "expires_at": now + 86400 * 14,
                "task_json": encoded,
            },
            ConditionExpression="attribute_not_exists(pk) AND attribute_not_exists(sk)",
        )
    except ClientError as exc:
        if not _conditional_write_failed(exc):
            raise
        existing = table.get_item(Key=job_key).get("Item") or {}
        if str(existing.get("status") or "") != "enqueue_error":
            return _existing_job_response(installation_id, job_id)
        try:
            # Un error confirmado de SQS puede reintentarse con la misma
            # identidad. La condición hace que solo un reintento gane y la
            # deduplicación FIFO cubre respuestas ambiguas del envío anterior.
            table.update_item(
                Key=job_key,
                UpdateExpression="SET #status = :status, updated_at = :updated_at",
                ConditionExpression="#status = :expected_status",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":status": "enqueuing",
                    ":expected_status": "enqueue_error",
                    ":updated_at": now,
                },
            )
        except ClientError as retry_exc:
            if _conditional_write_failed(retry_exc):
                return _existing_job_response(installation_id, job_id)
            raise

    try:
        sqs.send_message(
            QueueUrl=JOBS_QUEUE_URL,
            MessageBody=json.dumps(
                {
                    "job_id": job_id,
                    "installation_id": installation_id,
                    "created_at": now,
                    "task": task,
                },
                ensure_ascii=False,
            ),
            MessageAttributes={
                "installation_id": {
                    "DataType": "String",
                    "StringValue": installation_id,
                }
            },
            # La cola FIFO es una segunda barrera ante una caída entre el envío
            # y el update final de DynamoDB.
            MessageGroupId=installation_id,
            MessageDeduplicationId=job_id,
        )
    except Exception:
        table.update_item(
            Key=job_key,
            UpdateExpression=(
                "SET #status = :status, updated_at = :updated_at, "
                "enqueue_failed_at = :updated_at"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":status": "enqueue_error",
                ":updated_at": int(time.time()),
            },
        )
        raise

    table.update_item(
        Key=job_key,
        UpdateExpression="SET #status = :status, updated_at = :updated_at",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":status": "queued",
            ":updated_at": int(time.time()),
        },
    )
    return _response(202, {"job_id": job_id, "status": "queued", "desktop": state})


def _claim(event: dict[str, Any]) -> dict[str, Any]:
    body = _body(event)
    installation_id = _installation_id(event, body)
    max_messages = min(max(int(body.get("max_messages") or 1), 1), 5)
    result = sqs.receive_message(
        QueueUrl=JOBS_QUEUE_URL,
        MaxNumberOfMessages=max_messages,
        WaitTimeSeconds=0,
        VisibilityTimeout=300,
        MessageAttributeNames=["All"],
    )
    jobs: list[dict[str, Any]] = []
    for message in result.get("Messages") or []:
        payload = json.loads(message["Body"])
        if payload.get("installation_id") != installation_id:
            sqs.change_message_visibility(
                QueueUrl=JOBS_QUEUE_URL,
                ReceiptHandle=message["ReceiptHandle"],
                VisibilityTimeout=0,
            )
            continue
        payload["receipt_handle"] = message["ReceiptHandle"]
        jobs.append(payload)
        table.update_item(
            Key={
                "pk": f"INSTALLATION#{installation_id}",
                "sk": f"JOB#{payload['job_id']}",
            },
            UpdateExpression=(
                "SET #status = :status, claimed_at = :claimed_at "
                "ADD claim_attempts :one"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":status": "claimed",
                ":claimed_at": int(time.time()),
                ":one": 1,
            },
        )
    return _response(200, {"jobs": jobs})


def _complete(event: dict[str, Any]) -> dict[str, Any]:
    body = _body(event)
    installation_id = _installation_id(event, body)
    job_id = str(body.get("job_id") or "")
    receipt_handle = str(body.get("receipt_handle") or "")
    if not job_id or not receipt_handle:
        raise ValueError("job_id y receipt_handle son obligatorios.")
    status = str(body.get("status") or "done")
    if status not in {"done", "error"}:
        raise ValueError("status inválido.")
    result_json = json.dumps(
        body.get("result"), ensure_ascii=False, separators=(",", ":"), default=str
    )
    if len(result_json.encode()) > 300_000:
        raise ValueError("El resultado es demasiado grande.")
    now = int(time.time())
    table.update_item(
        Key={"pk": f"INSTALLATION#{installation_id}", "sk": f"JOB#{job_id}"},
        UpdateExpression=(
            "SET #status = :status, completed_at = :completed_at, "
            "result_json = :result_json, expires_at = :expires_at"
        ),
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":status": status,
            ":completed_at": now,
            ":result_json": result_json,
            ":expires_at": now + 86400 * 14,
        },
    )
    sqs.delete_message(QueueUrl=JOBS_QUEUE_URL, ReceiptHandle=receipt_handle)
    return _response(200, {"ok": True, "job_id": job_id, "status": status})


def _job_status(event: dict[str, Any], job_id: str) -> dict[str, Any]:
    installation_id = _installation_id(event)
    item = table.get_item(
        Key={"pk": f"INSTALLATION#{installation_id}", "sk": f"JOB#{job_id}"}
    ).get("Item")
    if not item:
        return _response(404, {"error": "job_not_found"})
    for serialized_field, public_field in (
        ("task_json", "task"),
        ("result_json", "result"),
    ):
        serialized = item.pop(serialized_field, None)
        if serialized is not None:
            item[public_field] = json.loads(str(serialized))
    return _response(200, {"job": item})


def _offline_chat(event: dict[str, Any]) -> dict[str, Any]:
    body = _body(event)
    installation_id = _installation_id(event, body)
    message = str(body.get("message") or "").strip()
    if not message or len(message) > 20_000:
        raise ValueError("message vacío o demasiado largo.")
    desktop = _desktop_state(installation_id)
    if desktop["online"]:
        conversation_id = str(body.get("conversation_id") or "").strip()
        idempotency_key = str(body.get("idempotency_key") or "").strip()
        return _enqueue(
            {
                **event,
                "body": json.dumps(
                    {
                        "installation_id": installation_id,
                        "idempotency_key": idempotency_key or None,
                        "task": {
                            "type": "chat",
                            "message": message,
                            **(
                                {"conversation_id": conversation_id}
                                if conversation_id
                                else {}
                            ),
                        },
                    }
                ),
                "isBase64Encoded": False,
            }
        )
    system = (
        "Eres Edecán en modo de continuidad. La computadora principal está desconectada. "
        "Responde de forma breve, útil y honesta. Puedes conversar, aclarar ideas y preparar "
        "trabajo, pero no afirmes haber accedido a archivos, pantalla ni herramientas locales."
    )
    response = bedrock.converse(
        modelId=OFFLINE_MODEL_ID,
        system=[{"text": system}],
        messages=[{"role": "user", "content": [{"text": message}]}],
        inferenceConfig={"maxTokens": 1200, "temperature": 0.4},
    )
    text = "".join(
        block.get("text", "")
        for block in response.get("output", {}).get("message", {}).get("content", [])
    )
    now = int(time.time())
    try:
        conversation_id = str(uuid.UUID(str(body.get("conversation_id") or uuid.uuid4())))
    except ValueError as exc:
        raise ValueError("conversation_id inválido.") from exc
    message_id = str(uuid.uuid4())
    table.put_item(
        Item={
            "pk": f"INSTALLATION#{installation_id}",
            "sk": f"CHAT#{conversation_id}#{now:010d}#{message_id}",
            "conversation_id": conversation_id,
            "user_message": message,
            "assistant_message": text,
            "model_id": OFFLINE_MODEL_ID,
            "created_at": now,
            "expires_at": now + 86400 * 30,
        }
    )
    # El chat básico se responde de inmediato, pero también queda como trabajo
    # durable. Cuando la computadora vuelva, el cliente residente lo inserta
    # una sola vez en la conversación local original.
    try:
        _enqueue(
            {
                **event,
                "body": json.dumps(
                    {
                        "installation_id": installation_id,
                        "idempotency_key": message_id,
                        "task": {
                            "type": "continuity_sync",
                            "event_id": message_id,
                            "conversation_id": conversation_id,
                            "user_message": message,
                            "assistant_message": text,
                        },
                    }
                ),
                "isBase64Encoded": False,
            }
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "level": "warning",
                    "event": "continuity_sync_enqueue_failed",
                    "type": type(exc).__name__,
                }
            )
        )
    return _response(
        200,
        {
            "mode": "offline_continuity",
            "conversation_id": conversation_id,
            "message": text,
            "model": OFFLINE_MODEL_ID,
            "desktop": desktop,
        },
    )


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    request = event.get("requestContext", {}).get("http", {})
    method = request.get("method", "GET").upper()
    path = request.get("path") or event.get("rawPath") or "/"
    if not _authorized(event):
        return _response(401, {"error": "unauthorized"})
    try:
        if path == "/healthz" and method == "GET":
            return _response(200, {"status": "ok", "service": "edecan-edge"})
        if path == "/v1/edge/status" and method == "GET":
            return _response(200, {"desktop": _desktop_state(_installation_id(event))})
        if path == "/v1/edge/heartbeat" and method == "POST":
            return _heartbeat(event)
        # La despedida vive en esta tabla, después del _authorized() de arriba,
        # para que use la misma credencial que el latido y ni un camino más.
        if path == "/v1/edge/goodbye" and method == "POST":
            return _goodbye(event)
        if path == "/v1/edge/jobs" and method == "POST":
            return _enqueue(event)
        if path == "/v1/edge/jobs/claim" and method == "POST":
            return _claim(event)
        if path == "/v1/edge/jobs/complete" and method == "POST":
            return _complete(event)
        if path.startswith("/v1/edge/jobs/") and method == "GET":
            return _job_status(event, path.rsplit("/", 1)[-1])
        if path == "/v1/edge/chat" and method == "POST":
            return _offline_chat(event)
        return _response(404, {"error": "not_found"})
    except (ValueError, json.JSONDecodeError) as exc:
        return _response(400, {"error": "invalid_request", "detail": str(exc)})
    except Exception as exc:
        error_id = hashlib.sha256(
            f"{time.time_ns()}:{type(exc).__name__}".encode()
        ).hexdigest()[:12]
        print(json.dumps({"level": "error", "error_id": error_id, "type": type(exc).__name__}))
        return _response(500, {"error": "internal_error", "error_id": error_id})
