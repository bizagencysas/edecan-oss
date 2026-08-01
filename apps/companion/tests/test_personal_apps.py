"""Pruebas de las integraciones locales con Mail, Contactos y Mensajes."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest
from edecan_companion import actions, personal_apps


def _macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(personal_apps.sys, "platform", "darwin")


def test_osascript_recibe_datos_solo_por_argv(monkeypatch: pytest.MonkeyPatch):
    _macos(monkeypatch)
    peligro = '"; do shell script "touch /tmp/no" --'
    llamada: dict[str, object] = {}

    def fake_run(command, **kwargs):
        llamada["command"] = command
        llamada["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout=b"ok", stderr=b"")

    monkeypatch.setattr(personal_apps.subprocess, "run", fake_run)

    resultado = personal_apps._run_osascript("on run argv\nreturn item 1 of argv\nend run", peligro)

    assert resultado == "ok"
    command = llamada["command"]
    assert isinstance(command, list)
    assert command[-2:] == ["--", peligro]
    assert peligro not in command[2]
    assert llamada["kwargs"] == {
        "capture_output": True,
        "check": False,
        "timeout": personal_apps._OSA_TIMEOUT_SECONDS,
    }


def test_osascript_convierte_permiso_denegado_en_error_util(monkeypatch: pytest.MonkeyPatch):
    _macos(monkeypatch)
    monkeypatch.setattr(
        personal_apps.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout=b"",
            stderr=b"Not authorized to send Apple events. (-1743)",
        ),
    )

    with pytest.raises(personal_apps.PersonalAppError, match="Ajustes"):
        personal_apps._run_osascript("return 1")


def test_osascript_convierte_timeout_en_error_util(monkeypatch: pytest.MonkeyPatch):
    _macos(monkeypatch)

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("/usr/bin/osascript", 35)

    monkeypatch.setattr(personal_apps.subprocess, "run", timeout)

    with pytest.raises(personal_apps.PersonalAppError, match="demasiado"):
        personal_apps._run_osascript("return 1")


def test_records_limpia_separadores_y_completa_campos():
    raw = (
        f"Ana\nTorres{personal_apps._FIELD_SEPARATOR}ana@example.com"
        f"{personal_apps._RECORD_SEPARATOR}Solo nombre"
    )

    assert personal_apps._records(raw, ("name", "email")) == [
        {"name": "Ana Torres", "email": "ana@example.com"},
        {"name": "Solo nombre", "email": ""},
    ]


def test_mail_send_valida_y_pasa_cuerpo_como_argumento(
    monkeypatch: pytest.MonkeyPatch, companion_config
):
    llamada: dict[str, object] = {}

    def fake_osa(script: str, *args: str) -> str:
        llamada["script"] = script
        llamada["args"] = args
        return "ok"

    monkeypatch.setattr(personal_apps, "_run_osascript", fake_osa)
    cuerpo = 'Hola"; do shell script "false'

    resultado = personal_apps.mac_mail_send(
        {"to": "persona@example.com", "subject": "Asunto", "message": cuerpo},
        companion_config,
    )

    assert resultado["sent"] is True
    assert llamada["args"] == ("persona@example.com", "Asunto", cuerpo)
    assert cuerpo not in llamada["script"]


def test_mail_send_rechaza_destinatario_y_cuerpo_invalidos(companion_config):
    with pytest.raises(personal_apps.PersonalAppError, match="correo válido"):
        personal_apps.mac_mail_send(
            {"to": "no-es-correo", "subject": "x", "message": "hola"},
            companion_config,
        )
    with pytest.raises(personal_apps.PersonalAppError, match="cuerpo"):
        personal_apps.mac_mail_send(
            {"to": "persona@example.com", "subject": "x", "message": "  "},
            companion_config,
        )


def test_messages_recent_abre_sqlite_solo_lectura_y_lee_wal(
    monkeypatch: pytest.MonkeyPatch, tmp_path, companion_config
):
    _macos(monkeypatch)
    database = tmp_path / "chat.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        PRAGMA journal_mode = WAL;
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY,
            is_from_me INTEGER,
            handle_id INTEGER,
            text TEXT,
            date INTEGER
        );
        INSERT INTO handle (ROWID, id) VALUES (1, '+15550000000');
        INSERT INTO message (is_from_me, handle_id, text, date)
        VALUES (0, 1, 'Mensaje reciente', 1000000000);
        """
    )
    connection.commit()
    # Mantener la conexión escritora abierta fuerza a que el lector use el
    # WAL. ``immutable=1`` habría ignorado este estado reciente.
    monkeypatch.setattr(personal_apps, "_messages_database_path", lambda: database)

    resultado = personal_apps.mac_messages_recent({"limit": 10}, companion_config)

    connection.close()
    assert resultado["count"] == 1
    assert resultado["messages"][0]["handle"] == "+15550000000"
    assert resultado["messages"][0]["text"] == "Mensaje reciente"


def test_messages_send_usa_argumentos_y_reporta_transporte(
    monkeypatch: pytest.MonkeyPatch, companion_config
):
    llamada: dict[str, object] = {}

    def fake_osa(script: str, *args: str) -> str:
        llamada["script"] = script
        llamada["args"] = args
        return "imessage"

    monkeypatch.setattr(personal_apps, "_run_osascript", fake_osa)
    resultado = personal_apps.mac_messages_send(
        {"to": "+1 555 000 0000", "message": "Hola"},
        companion_config,
    )

    assert resultado == {
        "sent": True,
        "to": "+15550000000",
        "transport": "imessage",
    }
    assert llamada["args"] == ("+15550000000", "Hola")
    assert "Hola" not in llamada["script"]


async def test_execute_redacta_cuerpo_legacy_y_no_muta_params(
    monkeypatch: pytest.MonkeyPatch, companion_config
):
    recibidos: list[dict[str, object]] = []
    originales = {
        "to": "persona@example.com",
        "subject": "Privado",
        "body": "contenido muy privado",
    }

    def handler(params, _config):
        recibidos.append(dict(params))
        return {"sent": True}

    async def approver(_action, params, _config):
        recibidos.append(dict(params))
        return True

    monkeypatch.setitem(actions.ACTIONS, "mac_mail_send", handler)
    resultado = await actions.execute(
        "mac_mail_send",
        originales,
        companion_config,
        approver,
    )

    assert resultado["ok"] is True
    assert originales["body"] == "contenido muy privado"
    assert all("body" not in params for params in recibidos)
    assert all(params["message"] == "contenido muy privado" for params in recibidos)
    entrada = json.loads(companion_config.audit_log_path.read_text(encoding="utf-8"))
    assert entrada["params"]["message"] == (f"<{len('contenido muy privado')} caracteres omitidos>")
    assert "contenido muy privado" not in json.dumps(entrada)


async def test_execute_devuelve_personal_app_error_sin_error_interno(
    monkeypatch: pytest.MonkeyPatch, companion_config
):
    def handler(_params, _config):
        raise personal_apps.PersonalAppError("Falta permiso de Mail.")

    async def approver(_action, _params, _config):
        return True

    monkeypatch.setitem(actions.ACTIONS, "mac_mail_accounts", handler)
    resultado = await actions.execute(
        "mac_mail_accounts",
        {},
        companion_config,
        approver,
    )

    assert resultado == {"ok": False, "error": "Falta permiso de Mail."}


@pytest.mark.skipif(sys.platform != "darwin", reason="osacompile solo existe en macOS")
def test_todos_los_applescripts_compilan(
    monkeypatch: pytest.MonkeyPatch, tmp_path, companion_config
):
    scripts: list[str] = []

    def capture(script: str, *_args: str) -> str:
        scripts.append(script)
        return "imessage"

    monkeypatch.setattr(personal_apps, "_run_osascript", capture)
    personal_apps.mac_mail_accounts({}, companion_config)
    personal_apps.mac_mail_search({"query": "factura"}, companion_config)
    personal_apps.mac_mail_send(
        {"to": "persona@example.com", "message": "hola"},
        companion_config,
    )
    personal_apps.mac_contacts_search({"query": "Ana"}, companion_config)
    personal_apps.mac_messages_send(
        {"to": "+15550000000", "message": "hola"},
        companion_config,
    )

    assert len(scripts) == 5
    for index, script in enumerate(scripts):
        compiled = subprocess.run(
            [
                "/usr/bin/osacompile",
                "-e",
                script,
                "-o",
                str(tmp_path / f"script-{index}.scpt"),
            ],
            capture_output=True,
            check=False,
        )
        assert compiled.returncode == 0, compiled.stderr.decode("utf-8", "replace")
