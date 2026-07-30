"""Adaptadores locales y estructurados para las apps personales de macOS.

Estas acciones viven en el companion porque Mail, Messages y Contacts solo
existen en la computadora de la persona. No usan credenciales web ni copian
datos al repositorio: cada consulta se ejecuta en el momento, dentro del
proceso local auditado por :mod:`edecan_companion.actions`.

Los valores proporcionados por el usuario se pasan a ``osascript`` como
argumentos de ``run argv``. Nunca se interpolan dentro del AppleScript, lo
que evita que un nombre, asunto o cuerpo pueda convertirse en código.
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

from edecan_companion.config import CompanionConfig

_OSA_TIMEOUT_SECONDS = 35
_FIELD_SEPARATOR = "\x1f"
_RECORD_SEPARATOR = "\x1e"
_MAX_RESULTS = 25
_MAX_MESSAGE_CHARS = 4_000
_MAX_MAIL_BODY_CHARS = 50_000
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class PersonalAppError(ValueError):
    """Error esperado y seguro de devolver al usuario."""


def _require_macos() -> None:
    if sys.platform != "darwin":
        raise PersonalAppError(
            "Las apps Mail, Mensajes y Contactos solo están disponibles en la Mac enlazada."
        )


def _bounded_int(value: Any, *, default: int = 10) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, _MAX_RESULTS))


def _run_osascript(script: str, *args: str) -> str:
    _require_macos()
    try:
        completed = subprocess.run(
            ["/usr/bin/osascript", "-e", script, "--", *args],
            capture_output=True,
            check=False,
            timeout=_OSA_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise PersonalAppError("La app de macOS tardó demasiado en responder.") from exc
    except OSError as exc:
        raise PersonalAppError(
            f"No se pudo abrir el puente de automatización de macOS: {exc}"
        ) from exc

    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        lowered = detail.casefold()
        if any(
            marker in lowered
            for marker in ("not authorized", "not authorised", "-1743", "no autorizado")
        ):
            raise PersonalAppError(
                "macOS todavía no autorizó esta app. Abre Edecán → Ajustes → "
                "Permisos de esta computadora y permite la app solicitada."
            )
        raise PersonalAppError(
            "macOS no pudo completar la operación" + (f": {detail[:240]}" if detail else ".")
        )
    return completed.stdout.decode("utf-8", "replace")


def _records(raw: str, fields: tuple[str, ...]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for record in raw.split(_RECORD_SEPARATOR):
        if not record.strip():
            continue
        values = record.split(_FIELD_SEPARATOR)
        if len(values) < len(fields):
            values.extend("" for _ in range(len(fields) - len(values)))
        output.append(
            {
                field: values[index].replace("\r", " ").replace("\n", " ").strip()
                for index, field in enumerate(fields)
            }
        )
    return output


def mac_mail_accounts(_params: dict[str, Any], _config: CompanionConfig) -> dict[str, Any]:
    script = r"""
on cleanField(valueText)
    set valueText to valueText as text
    set AppleScript's text item delimiters to character id 31
    set parts to text items of valueText
    set AppleScript's text item delimiters to " "
    set valueText to parts as text
    set AppleScript's text item delimiters to character id 30
    set parts to text items of valueText
    set AppleScript's text item delimiters to " "
    set valueText to parts as text
    set AppleScript's text item delimiters to ""
    return valueText
end cleanField

tell application "Mail"
    set output to ""
    repeat with accountItem in accounts
        set accountName to my cleanField(name of accountItem)
        set addresses to ""
        try
            set addresses to my cleanField(email addresses of accountItem as text)
        end try
        set output to output & accountName & character id 31 & addresses & character id 30
    end repeat
    return output
end tell
"""
    accounts = _records(_run_osascript(script), ("name", "addresses"))
    return {"accounts": accounts, "count": len(accounts)}


def mac_mail_search(params: dict[str, Any], _config: CompanionConfig) -> dict[str, Any]:
    query = str(params.get("query") or "").strip()
    if not query:
        raise PersonalAppError("Falta el texto que quieres buscar en Mail.")
    limit = _bounded_int(params.get("limit"))
    script = r"""
on cleanField(valueText)
    set valueText to valueText as text
    set AppleScript's text item delimiters to {character id 10, character id 13, ¬
        character id 31, character id 30}
    set parts to text items of valueText
    set AppleScript's text item delimiters to " "
    set valueText to parts as text
    set AppleScript's text item delimiters to ""
    return valueText
end cleanField

on run argv
    set wanted to item 1 of argv
    set maxItems to (item 2 of argv) as integer
    tell application "Mail"
        set output to ""
        set foundCount to 0
        repeat with accountItem in accounts
            if foundCount ≥ maxItems then exit repeat
            try
                set inboxBox to mailbox "INBOX" of accountItem
                set matchingMessages to messages of inboxBox whose ¬
                    subject contains wanted or sender contains wanted
                repeat with messageItem in matchingMessages
                    if foundCount ≥ maxItems then exit repeat
                    set output to output & my cleanField(name of accountItem) & character id 31
                    set output to output & my cleanField(sender of messageItem) & character id 31
                    set output to output & my cleanField(subject of messageItem) & character id 31
                    set output to output & my cleanField(date received of messageItem) & ¬
                        character id 30
                    set foundCount to foundCount + 1
                end repeat
            end try
        end repeat
        return output
    end tell
end run
"""
    messages = _records(
        _run_osascript(script, query, str(limit)),
        ("account", "sender", "subject", "received_at"),
    )
    return {"messages": messages[:limit], "count": min(len(messages), limit)}


def mac_mail_send(params: dict[str, Any], _config: CompanionConfig) -> dict[str, Any]:
    recipient = str(params.get("to") or "").strip()
    subject = str(params.get("subject") or "").strip() or "(sin asunto)"
    # ``message`` es la clave canónica porque la bitácora del companion la
    # redacta. ``body`` se conserva como alias para companions anteriores.
    body = str(params.get("message") or params.get("body") or "").strip()
    if not _EMAIL_RE.match(recipient):
        raise PersonalAppError("El destinatario no parece un correo válido.")
    if not body:
        raise PersonalAppError("El correo necesita un cuerpo no vacío.")
    if len(body) > _MAX_MAIL_BODY_CHARS:
        raise PersonalAppError("El cuerpo del correo es demasiado largo.")
    script = r"""
on run argv
    set recipientAddress to item 1 of argv
    set subjectText to item 2 of argv
    set bodyText to item 3 of argv
    tell application "Mail"
        set outgoingMessage to make new outgoing message with properties ¬
            {subject:subjectText, content:bodyText, visible:false}
        tell outgoingMessage
            make new to recipient at end of to recipients with properties {address:recipientAddress}
            send
        end tell
    end tell
    return "ok"
end run
"""
    _run_osascript(script, recipient, subject, body)
    return {"sent": True, "to": recipient, "subject": subject}


def mac_contacts_search(params: dict[str, Any], _config: CompanionConfig) -> dict[str, Any]:
    query = str(params.get("query") or "").strip()
    if not query:
        raise PersonalAppError("Falta el nombre que quieres buscar en Contactos.")
    limit = _bounded_int(params.get("limit"), default=8)
    script = r"""
on cleanField(valueText)
    set valueText to valueText as text
    set AppleScript's text item delimiters to {character id 10, character id 13, ¬
        character id 31, character id 30}
    set parts to text items of valueText
    set AppleScript's text item delimiters to " "
    set valueText to parts as text
    set AppleScript's text item delimiters to ""
    return valueText
end cleanField

on run argv
    set wanted to item 1 of argv
    set maxItems to (item 2 of argv) as integer
    tell application "Contacts"
        set output to ""
        set foundCount to 0
        repeat with personItem in (people whose name contains wanted)
            if foundCount ≥ maxItems then exit repeat
            set personName to my cleanField(name of personItem)
            set phoneValues to ""
            repeat with phoneItem in phones of personItem
                if phoneValues is not "" then set phoneValues to phoneValues & ", "
                set phoneValues to phoneValues & my cleanField(value of phoneItem)
            end repeat
            set emailValues to ""
            repeat with emailItem in emails of personItem
                if emailValues is not "" then set emailValues to emailValues & ", "
                set emailValues to emailValues & my cleanField(value of emailItem)
            end repeat
            set output to output & personName & character id 31 & phoneValues & ¬
                character id 31 & emailValues & character id 30
            set foundCount to foundCount + 1
        end repeat
        return output
    end tell
end run
"""
    contacts = _records(
        _run_osascript(script, query, str(limit)),
        ("name", "phones", "emails"),
    )
    return {"contacts": contacts[:limit], "count": min(len(contacts), limit)}


def mac_messages_recent(params: dict[str, Any], _config: CompanionConfig) -> dict[str, Any]:
    _require_macos()
    limit = _bounded_int(params.get("limit"))
    database = _messages_database_path()
    if not database.is_file():
        raise PersonalAppError("No se encontró la base local de Mensajes en esta cuenta de macOS.")
    try:
        connection = sqlite3.connect(
            # ``mode=ro`` impide escrituras y, a diferencia de
            # ``immutable=1``, permite que SQLite lea el WAL activo de
            # Messages. Sin esto los mensajes más recientes podían faltar.
            f"file:{database}?mode=ro",
            uri=True,
            timeout=5,
        )
        try:
            connection.execute("PRAGMA query_only = ON")
            rows = connection.execute(
                """
                SELECT m.is_from_me, COALESCE(h.id, ''), m.text,
                       datetime((m.date / 1000000000) + 978307200, 'unixepoch', 'localtime')
                FROM message AS m
                LEFT JOIN handle AS h ON m.handle_id = h.ROWID
                WHERE m.text IS NOT NULL AND trim(m.text) <> ''
                ORDER BY m.date DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise PersonalAppError(
            "No se pudo leer Mensajes. Autoriza Acceso completo al disco para Edecán."
        ) from exc

    messages = [
        {
            "from_me": bool(from_me),
            "handle": str(handle or ""),
            "text": str(text or "")[:_MAX_MESSAGE_CHARS],
            "sent_at": str(sent_at or ""),
        }
        for from_me, handle, text, sent_at in rows
    ]
    return {"messages": messages, "count": len(messages)}


def mac_messages_send(params: dict[str, Any], _config: CompanionConfig) -> dict[str, Any]:
    recipient = "".join(str(params.get("to") or "").split())
    body = str(params.get("message") or params.get("body") or "").strip()
    if not recipient or not body:
        raise PersonalAppError("El mensaje necesita un destinatario y un texto.")
    if len(body) > _MAX_MESSAGE_CHARS:
        raise PersonalAppError("El mensaje es demasiado largo.")
    script = r"""
on run argv
    set recipientAddress to item 1 of argv
    set bodyText to item 2 of argv
    tell application "Messages"
        try
            send bodyText to buddy recipientAddress of (first service whose service type = iMessage)
            return "imessage"
        on error
            set smsService to first service whose service type = SMS
            send bodyText to buddy recipientAddress of smsService
            return "sms"
        end try
    end tell
end run
"""
    transport = _run_osascript(script, recipient, body).strip()
    return {"sent": True, "to": recipient, "transport": transport or "messages"}


def _messages_database_path() -> Path:
    """Ruta aislada en una función para poder probarla sin tocar el Home real."""

    return Path.home() / "Library" / "Messages" / "chat.db"


PERSONAL_APP_ACTIONS = {
    "mac_mail_accounts": mac_mail_accounts,
    "mac_mail_search": mac_mail_search,
    "mac_mail_send": mac_mail_send,
    "mac_contacts_search": mac_contacts_search,
    "mac_messages_recent": mac_messages_recent,
    "mac_messages_send": mac_messages_send,
}
