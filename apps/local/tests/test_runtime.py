"""`edecan_local.runtime` — orquestación del runner local (`ARCHITECTURE.md`
§12f, WP-V3-05): parseo de flags, secretos locales, entorno fijado ANTES de
importar `edecan_api`, y el `run()` completo con todos los colaboradores
pesados (`ensure_postgres`, `run_migrations`, `worker_loop`) reemplazados
por dobles — solo los servidores uvicorn (API real vía `_import_api_app`
fakeado con una app Starlette trivial, y el object store REAL) y el poll de
`/healthz` corren de verdad, contra loopback.

`_snapshot_environ` (autouse) respalda y restaura `os.environ` COMPLETO
alrededor de cada test: `_apply_env`/`run()` mutan variables de entorno
reales (`EDECAN_LOCAL_MODE`, `DATABASE_URL`, `JWT_SECRET`, ...) y no hay
forma limpia de enumerar todas las que pudieran tocar sin duplicar
`_build_env` acá.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import edecan_local.runtime as rt
import httpx as real_httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route


@pytest.fixture(autouse=True)
def _snapshot_environ() -> Any:
    before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(before)


# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------


def test_parse_args_defaults() -> None:
    args = rt.parse_args([])
    assert args.port == rt.DEFAULT_PORT
    assert args.data_dir == rt.DEFAULT_DATA_DIR
    assert args.no_web is False
    assert args.mobile_access is False
    assert args.macos_permission_status is False
    assert args.macos_capture_check is False


def test_parse_args_overrides() -> None:
    args = rt.parse_args(["--port", "9999", "--data-dir", "/tmp/x", "--no-web", "--mobile-access"])
    assert args.port == 9999
    assert args.data_dir == "/tmp/x"
    assert args.no_web is True
    assert args.mobile_access is True


def test_macos_permission_status_is_ready_outside_macos(monkeypatch) -> None:
    monkeypatch.setattr(rt.sys, "platform", "linux")

    assert rt._macos_permission_status() == {
        "screen_recording": True,
        "accessibility": True,
    }


def test_main_permission_probe_does_not_start_backend(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        rt,
        "_macos_permission_status",
        lambda: {"screen_recording": False, "accessibility": True},
    )

    rt.main(["--macos-permission-status"])

    assert json.loads(capsys.readouterr().out) == {
        "screen_recording": False,
        "accessibility": True,
    }


def test_main_capture_probe_returns_only_metadata(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        rt,
        "_macos_capture_check",
        lambda: {"ok": True, "width": 640, "height": 414, "mime": "image/jpeg"},
    )

    rt.main(["--macos-capture-check"])

    assert json.loads(capsys.readouterr().out) == {
        "ok": True,
        "width": 640,
        "height": 414,
        "mime": "image/jpeg",
    }


# ---------------------------------------------------------------------------
# _ensure_local_secrets
# ---------------------------------------------------------------------------


def test_ensure_local_secrets_genera_y_persiste_con_permisos_0600(tmp_path: Path) -> None:
    from cryptography.fernet import Fernet

    secrets = rt._ensure_local_secrets(tmp_path)

    assert set(secrets) == {"JWT_SECRET", "LOCAL_MASTER_KEY"}
    assert len(secrets["JWT_SECRET"]) >= 32
    Fernet(secrets["LOCAL_MASTER_KEY"].encode("ascii"))  # no debe lanzar

    secrets_path = tmp_path / "secrets.json"
    assert secrets_path.is_file()
    mode = stat.S_IMODE(secrets_path.stat().st_mode)
    assert mode == 0o600


def test_ensure_local_secrets_reutiliza_los_ya_persistidos(tmp_path: Path) -> None:
    primero = rt._ensure_local_secrets(tmp_path)
    segundo = rt._ensure_local_secrets(tmp_path)
    assert primero == segundo


def test_ensure_local_secrets_con_archivo_corrupto_genera_nuevos_sin_reventar(
    tmp_path: Path,
) -> None:
    (tmp_path / "secrets.json").write_text("esto no es JSON", encoding="utf-8")
    secrets = rt._ensure_local_secrets(tmp_path)
    assert secrets["JWT_SECRET"]
    assert secrets["LOCAL_MASTER_KEY"]


# ---------------------------------------------------------------------------
# _resolve_serve_web_dir
# ---------------------------------------------------------------------------


def test_resolve_serve_web_dir_no_web_gana_siempre(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDECAN_WEB_DIR", "/algo")
    assert rt._resolve_serve_web_dir(no_web=True) is None


def test_resolve_serve_web_dir_usa_env_var_si_esta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDECAN_WEB_DIR", "/mi/carpeta/web")
    assert rt._resolve_serve_web_dir(no_web=False) == "/mi/carpeta/web"


def test_resolve_serve_web_dir_sin_nada_devuelve_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EDECAN_WEB_DIR", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert rt._resolve_serve_web_dir(no_web=False) is None


def test_resolve_serve_web_dir_usa_meipass_si_existe_web(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("EDECAN_WEB_DIR", raising=False)
    web_dir = tmp_path / "web"
    web_dir.mkdir()
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert rt._resolve_serve_web_dir(no_web=False) == str(web_dir)


def test_resolve_serve_web_dir_meipass_sin_carpeta_web_devuelve_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("EDECAN_WEB_DIR", raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)  # sin /web adentro

    assert rt._resolve_serve_web_dir(no_web=False) is None


# ---------------------------------------------------------------------------
# _build_env / _apply_env
# ---------------------------------------------------------------------------


def _local_secrets() -> dict[str, str]:
    return {"JWT_SECRET": "jwt-de-prueba", "LOCAL_MASTER_KEY": "master-key-de-prueba"}


def test_build_env_shape_completo_sin_serve_web_dir(tmp_path: Path) -> None:
    env = rt._build_env(
        data_dir=tmp_path,
        port=8765,
        objectstore_port=8767,
        database_url="postgresql+asyncpg://u:p@h/d",
        serve_web_dir=None,
        local_secrets=_local_secrets(),
    )
    assert env == {
        "EDECAN_LOCAL_MODE": "1",
        "DATABASE_URL": "postgresql+asyncpg://u:p@h/d",
        "REDIS_URL": "memory://",
        "QUEUE_PROVIDER": "db",
        "AWS_ENDPOINT_URL": "http://127.0.0.1:8767",
        "AWS_ACCESS_KEY_ID": "local",
        "AWS_SECRET_ACCESS_KEY": "local",
        "S3_BUCKET": "edecan-files",
        "DATA_DIR": str(tmp_path),
        "LOCAL_API_PORT": "8765",
        "JWT_SECRET": "jwt-de-prueba",
        "LOCAL_MASTER_KEY": "master-key-de-prueba",
    }


def test_build_env_incluye_serve_web_dir_cuando_se_pasa(tmp_path: Path) -> None:
    env = rt._build_env(
        data_dir=tmp_path,
        port=8765,
        objectstore_port=8767,
        database_url="postgresql+asyncpg://u:p@h/d",
        serve_web_dir="/ruta/al/web",
        local_secrets=_local_secrets(),
    )
    assert env["SERVE_WEB_DIR"] == "/ruta/al/web"


def test_mobile_public_url_prefiere_override_explicito(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EDECAN_MOBILE_PUBLIC_URL", "https://mi-edecan.example/")
    monkeypatch.setattr(rt, "_primary_lan_ipv4", lambda: "192.168.1.25")
    monkeypatch.setattr(rt.socket, "gethostname", lambda: "ignorado")

    assert rt._mobile_public_url(8765) == "https://mi-edecan.example"


def test_mobile_public_url_lee_url_https_persistida_para_el_relay(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("EDECAN_MOBILE_PUBLIC_URL", raising=False)
    (tmp_path / "remote-access.json").write_text(
        json.dumps({"public_url": "https://equipo-opaco.edecan.example/"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(rt, "_primary_lan_ipv4", lambda: "192.168.1.25")

    assert rt._mobile_public_url(8765, tmp_path) == "https://equipo-opaco.edecan.example"


@pytest.mark.parametrize(
    "public_url",
    (
        "http://edecan.example",
        "https://usuario:clave@edecan.example",
        "https://edecan.example?token=no-va-en-url",
        "https://edecan.example/ruta",
        "https://[",
        "javascript:alert(1)",
    ),
)
def test_mobile_public_url_ignora_configuracion_persistida_insegura(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    public_url: str,
) -> None:
    monkeypatch.delenv("EDECAN_MOBILE_PUBLIC_URL", raising=False)
    (tmp_path / "remote-access.json").write_text(
        json.dumps({"public_url": public_url}), encoding="utf-8"
    )
    monkeypatch.setattr(rt, "_primary_lan_ipv4", lambda: "192.168.1.25")

    assert rt._mobile_public_url(8765, tmp_path) == "http://192.168.1.25:8765"


def test_mobile_public_url_prefiere_ip_privada_que_resuelve_android(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EDECAN_MOBILE_PUBLIC_URL", raising=False)
    monkeypatch.setattr(rt.socket, "gethostname", lambda: "MacBook-Isacc")
    monkeypatch.setattr(rt, "_primary_lan_ipv4", lambda: "192.168.58.105")

    assert rt._mobile_public_url(8765) == "http://192.168.58.105:8765"


def test_mobile_public_url_convierte_hostname_local_en_mdns_si_no_hay_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EDECAN_MOBILE_PUBLIC_URL", raising=False)
    monkeypatch.setattr(rt, "_primary_lan_ipv4", lambda: None)
    monkeypatch.setattr(rt.socket, "gethostname", lambda: "MacBook-Isacc")

    assert rt._mobile_public_url(8765) == "http://MacBook-Isacc.local:8765"


def test_mobile_public_url_conserva_hostname_mdns_existente(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EDECAN_MOBILE_PUBLIC_URL", raising=False)
    monkeypatch.setattr(rt, "_primary_lan_ipv4", lambda: None)
    monkeypatch.setattr(rt.socket, "gethostname", lambda: "edecan.local.")

    assert rt._mobile_public_url(9000) == "http://edecan.local:9000"


def test_apply_env_fija_todas_las_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SQS_QUEUE_URL", raising=False)
    env = rt._build_env(
        data_dir=Path("/tmp/edecan-data"),
        port=8765,
        objectstore_port=8767,
        database_url="postgresql+asyncpg://u:p@h/d",
        serve_web_dir=None,
        local_secrets=_local_secrets(),
    )

    rt._apply_env(env)

    for key, value in env.items():
        assert os.environ[key] == value


def test_apply_env_borra_sqs_queue_url_heredado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SQS_QUEUE_URL", "http://localhost:4566/000/edecan-jobs")
    env = rt._build_env(
        data_dir=Path("/tmp"),
        port=8765,
        objectstore_port=8767,
        database_url="postgresql+asyncpg://u:p@h/d",
        serve_web_dir=None,
        local_secrets=_local_secrets(),
    )

    rt._apply_env(env)

    assert "SQS_QUEUE_URL" not in os.environ


def test_apply_env_respeta_jwt_secret_y_local_master_key_ya_puestos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "ya-estaba-puesto")
    monkeypatch.setenv("LOCAL_MASTER_KEY", "tambien-ya-estaba")
    env = rt._build_env(
        data_dir=Path("/tmp"),
        port=8765,
        objectstore_port=8767,
        database_url="postgresql+asyncpg://u:p@h/d",
        serve_web_dir=None,
        local_secrets=_local_secrets(),
    )

    rt._apply_env(env)

    assert os.environ["JWT_SECRET"] == "ya-estaba-puesto"
    assert os.environ["LOCAL_MASTER_KEY"] == "tambien-ya-estaba"
    # El resto SIEMPRE se pisa (no usa setdefault).
    assert os.environ["DATABASE_URL"] == "postgresql+asyncpg://u:p@h/d"


# ---------------------------------------------------------------------------
# _make_server — sin manejadores de señales propios
# ---------------------------------------------------------------------------


def test_make_server_no_instala_manejadores_de_señales() -> None:
    import signal

    async def _trivial_app(scope: Any, receive: Any, send: Any) -> None:  # pragma: no cover
        pass

    antes = signal.getsignal(signal.SIGTERM)
    server = rt._make_server(_trivial_app, host="127.0.0.1", port=0)

    with server.capture_signals():
        durante = signal.getsignal(signal.SIGTERM)

    despues = signal.getsignal(signal.SIGTERM)
    assert durante == antes
    assert despues == antes
    assert hasattr(server, "should_exit")


# ---------------------------------------------------------------------------
# _run_background — normaliza SystemExit/KeyboardInterrupt crudos (p. ej.
# `uvicorn.Server.startup()` sobre un puerto ocupado hace `sys.exit(...)`)
# ANTES de que `asyncio.Task` los vea; ver su docstring para la explicación
# completa de por qué un `SystemExit` crudo dentro de un
# `asyncio.create_task()` puede escaparse de `asyncio.gather(...,
# return_exceptions=True)` e interrumpir la ejecución de OTRO test bajo
# pytest-asyncio (la fuga de tareas asyncio entre archivos de test que este
# módulo existe para cerrar de raíz).
# ---------------------------------------------------------------------------


async def test_run_background_normaliza_systemexit_a_runtime_error() -> None:
    async def _boom() -> None:
        sys.exit(3)

    with pytest.raises(RuntimeError, match="tarea-de-prueba.*SystemExit.*3") as excinfo:
        await rt._run_background(_boom(), label="tarea-de-prueba")

    # El `SystemExit` original queda encadenado (`raise ... from exc`) para
    # no perder la causa real -- no es solo un mensaje de texto.
    assert isinstance(excinfo.value.__cause__, SystemExit)


async def test_run_background_normaliza_keyboardinterrupt_a_runtime_error() -> None:
    async def _boom() -> None:
        raise KeyboardInterrupt()

    with pytest.raises(RuntimeError, match="tarea-de-prueba.*KeyboardInterrupt"):
        await rt._run_background(_boom(), label="tarea-de-prueba")


async def test_run_background_deja_pasar_excepciones_normales_sin_tocar() -> None:
    async def _boom() -> None:
        raise ValueError("boom normal")

    with pytest.raises(ValueError, match="^boom normal$"):
        await rt._run_background(_boom(), label="tarea-de-prueba")


async def test_run_background_deja_pasar_cancelled_error_sin_tocar() -> None:
    """El apagado normal de `run()` (`should_exit`/`stop_event.set()` +
    `asyncio.gather(..., return_exceptions=True)`) depende de que
    `CancelledError` llegue intacto -- `_run_background` no debe envolverlo
    en `RuntimeError` como a un `SystemExit`."""

    async def _espera_para_siempre() -> None:
        await asyncio.sleep(60)

    task = asyncio.create_task(rt._run_background(_espera_para_siempre(), label="tarea-de-prueba"))
    await asyncio.sleep(0)  # deja que la tarea arranque y quede suspendida en el sleep
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_run_background_camino_feliz_no_hace_nada_especial() -> None:
    calls: list[str] = []

    async def _ok() -> None:
        calls.append("corrió")

    await rt._run_background(_ok(), label="tarea-de-prueba")

    assert calls == ["corrió"]


# ---------------------------------------------------------------------------
# _wait_until_healthy
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def _install_fake_httpx(monkeypatch: pytest.MonkeyPatch, response_fn: Any) -> list[str]:
    calls: list[str] = []

    class _FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _FakeAsyncClient:
            return self

        async def __aexit__(self, *exc_info: object) -> bool:
            return False

        async def get(self, url: str) -> _FakeResponse:
            calls.append(url)
            item = response_fn(len(calls))
            if isinstance(item, Exception):
                raise item
            return item

    fake_module = SimpleNamespace(AsyncClient=_FakeAsyncClient, HTTPError=real_httpx.HTTPError)
    monkeypatch.setitem(sys.modules, "httpx", fake_module)
    return calls


async def test_wait_until_healthy_exito_inmediato(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_httpx(monkeypatch, lambda n: _FakeResponse(200))
    await rt._wait_until_healthy(9999)
    assert len(calls) == 1


async def test_wait_until_healthy_reintenta_hasta_200(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rt, "HEALTHZ_INTERVAL_SECONDS", 0.001)
    calls = _install_fake_httpx(
        monkeypatch, lambda n: _FakeResponse(200) if n >= 3 else _FakeResponse(503)
    )
    await rt._wait_until_healthy(9999)
    assert len(calls) == 3


async def test_wait_until_healthy_tolera_httperror_y_reintenta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rt, "HEALTHZ_INTERVAL_SECONDS", 0.001)
    calls = _install_fake_httpx(
        monkeypatch,
        lambda n: real_httpx.ConnectError("rechazado") if n == 1 else _FakeResponse(200),
    )
    await rt._wait_until_healthy(9999)
    assert len(calls) == 2


async def test_wait_until_healthy_agota_intentos_y_lanza(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rt, "HEALTHZ_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(rt, "HEALTHZ_MAX_ATTEMPTS", 3)
    calls = _install_fake_httpx(monkeypatch, lambda n: _FakeResponse(503))

    with pytest.raises(RuntimeError, match="no respondió 200"):
        await rt._wait_until_healthy(9999)

    assert len(calls) == 3


async def test_wait_until_healthy_corta_si_una_tarea_ya_crasheo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rt, "HEALTHZ_INTERVAL_SECONDS", 0.001)
    _install_fake_httpx(monkeypatch, lambda n: _FakeResponse(503))

    async def _crash() -> None:
        raise RuntimeError("boom de arranque")

    task = asyncio.create_task(_crash())
    await asyncio.sleep(0.01)  # deja que la tarea termine antes de llamar

    with pytest.raises(RuntimeError, match="boom de arranque"):
        await rt._wait_until_healthy(9999, [task])


# ---------------------------------------------------------------------------
# run() — orquestación completa, con los colaboradores pesados fakeados.
# ---------------------------------------------------------------------------

_TEST_PORT = 18841
_TEST_OBJECTSTORE_PORT = _TEST_PORT + rt.OBJECTSTORE_PORT_OFFSET


async def _healthz(request: Any) -> JSONResponse:
    return JSONResponse({"status": "ok"})


_FAKE_API_APP = Starlette(routes=[Route("/healthz", _healthz)])


async def test_run_orquesta_todo_en_orden_y_apaga_limpio(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import edecan_local.migrate as migrate_module
    import edecan_local.pg as pg_module
    import edecan_local.worker_loop as worker_loop_module

    # --- ensure_postgres fake -------------------------------------------------
    ensure_postgres_calls: list[Path] = []

    class _FakePgHandle:
        def __init__(self) -> None:
            self.cleanup_called = False

        def cleanup(self) -> None:
            self.cleanup_called = True

    fake_pg_handle = _FakePgHandle()
    fake_database_url = "postgresql+asyncpg://u:p@h:5432/d"

    async def fake_ensure_postgres(data_dir: Path) -> tuple[str, _FakePgHandle]:
        ensure_postgres_calls.append(data_dir)
        return fake_database_url, fake_pg_handle

    monkeypatch.setattr(pg_module, "ensure_postgres", fake_ensure_postgres)
    # Poll de /healthz más rápido: mantiene el test rápido incluso si los
    # primeros intentos fallan mientras el servidor uvicorn real termina de
    # levantar (el default de producción, 0.5s, sería innecesariamente lento
    # acá). El apagado ya NO depende de ganarle una carrera a este intervalo
    # -- ver el `print` enganchado más abajo.
    monkeypatch.setattr(rt, "HEALTHZ_INTERVAL_SECONDS", 0.02)

    # --- run_migrations fake ---------------------------------------------------
    migrate_calls: list[str] = []
    monkeypatch.setattr(migrate_module, "run_migrations", migrate_calls.append)

    # --- edecan_api fakeado (app Starlette trivial con /healthz) ---------------
    monkeypatch.setattr(rt, "_import_api_app", lambda: _FAKE_API_APP)
    monkeypatch.setattr(rt, "_import_api_settings", lambda: SimpleNamespace(marker="fake-settings"))

    # --- worker_loop fake --------------------------------------------------------
    worker_loop_calls: list[Any] = []

    class _FakeDepsCM:
        def __init__(self, settings: Any) -> None:
            self._settings = settings

        async def __aenter__(self) -> Any:
            return SimpleNamespace(settings=self._settings)

        async def __aexit__(self, *exc_info: object) -> bool:
            return False

    async def fake_run_forever(deps: Any, *, stop_event: asyncio.Event) -> None:
        worker_loop_calls.append(deps)
        await stop_event.wait()

    monkeypatch.setattr(worker_loop_module, "build_local_deps", _FakeDepsCM)
    monkeypatch.setattr(worker_loop_module, "run_forever", fake_run_forever)

    # --- dispara el apagado apenas `run()` imprime la línea de "listo", sin
    # señales reales de SO -------------------------------------------------------
    # NO un sleep fijo: un sleep fijo corre una carrera real contra el arranque
    # de los servidores uvicorn/objectstore reales de más abajo y, bajo carga
    # del sistema, puede ganarle al primer chequeo exitoso de `/healthz` --
    # reproducido una vez corriendo el monorepo completo: `stop_event` quedaba
    # marcado antes de que `_wait_until_healthy` alcanzara a ver el 200, `run()`
    # nunca llegaba a imprimir "EDECAN_LOCAL_READY" y la aserción de más abajo
    # fallaba contra un `capsys` vacío. Enganchar el propio `print` de `rt` (en
    # vez de adivinar un tiempo fijo) asegura que `stop_event` se marca DESPUÉS
    # de que la línea ya está en stdout -- sin ninguna ventana de carrera.
    stop_event = asyncio.Event()
    real_print = print

    def _print_real_y_dispara_apagado(*args: Any, **kwargs: Any) -> None:
        real_print(*args, **kwargs)
        stop_event.set()

    monkeypatch.setattr(rt, "print", _print_real_y_dispara_apagado, raising=False)

    await asyncio.wait_for(
        rt.run(port=_TEST_PORT, data_dir=str(tmp_path), no_web=True, stop_event=stop_event),
        timeout=15,
    )

    # --- orden/contenido ---------------------------------------------------------
    assert ensure_postgres_calls == [tmp_path]
    assert migrate_calls == [fake_database_url]
    assert len(worker_loop_calls) == 1
    assert worker_loop_calls[0].settings.marker == "fake-settings"
    assert fake_pg_handle.cleanup_called is True

    salida = capsys.readouterr().out
    assert f"EDECAN_LOCAL_READY port={_TEST_PORT}" in salida

    # data_dir con permisos 0700 y el bucket-dir del object store creado.
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert (tmp_path / "objects" / rt.S3_BUCKET_NAME).is_dir()


async def test_run_arranca_ollama_tras_settings_y_lo_detiene_en_finally(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """WP-V4-09: `maybe_start_ollama` se llama con `api_settings` (recién
    disponible tras `_import_api_settings`) y `OllamaHandle.stop()` se llama
    en el `finally`, ANTES del `pg_handle.cleanup()` (orden inverso al de
    arranque: Ollama arrancó después de Postgres)."""
    import edecan_local.migrate as migrate_module
    import edecan_local.ollama_supervisor as ollama_supervisor_module
    import edecan_local.pg as pg_module
    import edecan_local.worker_loop as worker_loop_module

    order: list[str] = []

    class _FakePgHandle:
        def cleanup(self) -> None:
            order.append("pg")

    async def fake_ensure_postgres(data_dir: Path) -> tuple[str, _FakePgHandle]:
        return "postgresql+asyncpg://u:p@h:5432/d", _FakePgHandle()

    monkeypatch.setattr(pg_module, "ensure_postgres", fake_ensure_postgres)
    monkeypatch.setattr(rt, "HEALTHZ_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr(migrate_module, "run_migrations", lambda database_url: None)

    fake_settings = SimpleNamespace(marker="fake-settings")
    monkeypatch.setattr(rt, "_import_api_app", lambda: _FAKE_API_APP)
    monkeypatch.setattr(rt, "_import_api_settings", lambda: fake_settings)

    class _FakeOllamaHandle:
        def stop(self) -> None:
            order.append("ollama")

    maybe_start_calls: list[Any] = []

    def fake_maybe_start_ollama(settings: Any) -> _FakeOllamaHandle:
        maybe_start_calls.append(settings)
        return _FakeOllamaHandle()

    monkeypatch.setattr(ollama_supervisor_module, "maybe_start_ollama", fake_maybe_start_ollama)

    class _FakeDepsCM:
        def __init__(self, settings: Any) -> None:
            pass

        async def __aenter__(self) -> Any:
            return SimpleNamespace()

        async def __aexit__(self, *exc_info: object) -> bool:
            return False

    async def fake_run_forever(deps: Any, *, stop_event: asyncio.Event) -> None:
        await stop_event.wait()

    monkeypatch.setattr(worker_loop_module, "build_local_deps", _FakeDepsCM)
    monkeypatch.setattr(worker_loop_module, "run_forever", fake_run_forever)

    # Mismo patrón que en test_run_orquesta_todo_en_orden_y_apaga_limpio:
    # engancha el `print` de "listo" en vez de un sleep fijo, para no correr la
    # misma carrera real contra el arranque de los servidores de más abajo.
    # Este test no depende hoy de ganar esa carrera (no mira stdout), pero
    # comparte el mismo riesgo -- un sleep fijo aquí es la misma trampa latente.
    stop_event = asyncio.Event()
    real_print = print

    def _print_real_y_dispara_apagado(*args: Any, **kwargs: Any) -> None:
        real_print(*args, **kwargs)
        stop_event.set()

    monkeypatch.setattr(rt, "print", _print_real_y_dispara_apagado, raising=False)

    await asyncio.wait_for(
        rt.run(port=_TEST_PORT + 40, data_dir=str(tmp_path), no_web=True, stop_event=stop_event),
        timeout=15,
    )

    assert maybe_start_calls == [fake_settings]
    assert order == ["ollama", "pg"]


async def test_run_detiene_ollama_en_finally_incluso_con_excepcion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Si algo revienta DESPUÉS de arrancar Ollama (acá: el backend nunca
    queda sano) el `finally` sigue llamando `ollama_handle.stop()` -- no
    solo en el camino feliz."""
    import edecan_local.migrate as migrate_module
    import edecan_local.ollama_supervisor as ollama_supervisor_module
    import edecan_local.pg as pg_module
    import edecan_local.worker_loop as worker_loop_module

    order: list[str] = []

    class _FakePgHandle:
        def cleanup(self) -> None:
            order.append("pg")

    async def fake_ensure_postgres(data_dir: Path) -> tuple[str, _FakePgHandle]:
        return "postgresql+asyncpg://u:p@h:5432/d", _FakePgHandle()

    monkeypatch.setattr(pg_module, "ensure_postgres", fake_ensure_postgres)
    monkeypatch.setattr(migrate_module, "run_migrations", lambda database_url: None)

    async def _healthz_nunca_sano(request: Any) -> JSONResponse:
        return JSONResponse({"status": "not-ready"}, status_code=503)

    app_nunca_sano = Starlette(routes=[Route("/healthz", _healthz_nunca_sano)])
    monkeypatch.setattr(rt, "_import_api_app", lambda: app_nunca_sano)
    monkeypatch.setattr(rt, "_import_api_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(rt, "HEALTHZ_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(rt, "HEALTHZ_MAX_ATTEMPTS", 2)

    class _FakeOllamaHandle:
        def stop(self) -> None:
            order.append("ollama")

    monkeypatch.setattr(
        ollama_supervisor_module, "maybe_start_ollama", lambda settings: _FakeOllamaHandle()
    )

    class _FakeDepsCM:
        def __init__(self, settings: Any) -> None:
            pass

        async def __aenter__(self) -> Any:
            return SimpleNamespace()

        async def __aexit__(self, *exc_info: object) -> bool:
            return False

    async def fake_run_forever(deps: Any, *, stop_event: asyncio.Event) -> None:
        await stop_event.wait()

    monkeypatch.setattr(worker_loop_module, "build_local_deps", _FakeDepsCM)
    monkeypatch.setattr(worker_loop_module, "run_forever", fake_run_forever)

    with pytest.raises(RuntimeError, match="no respondió 200"):
        await asyncio.wait_for(
            rt.run(port=_TEST_PORT + 50, data_dir=str(tmp_path), no_web=True),
            timeout=15,
        )

    assert order == ["ollama", "pg"]


async def test_run_normaliza_systemexit_de_uvicorn_por_puerto_ocupado_y_no_deja_tareas_vivas(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regresión de la fuga de tareas asyncio entre archivos de test: si el
    bind del puerto de la API falla (`OSError`, típico bajo contención real
    de CPU/puertos -- ver Traceback A/B de la investigación), `uvicorn.
    Server.startup()` hace `sys.exit(STARTUP_FAILURE)` DENTRO de la tarea de
    fondo (`asyncio.create_task(api_server.serve())`). Sin `_run_background`
    ese `SystemExit` crudo se escapa de la máquina de `asyncio.Task` -- ni
    siquiera `pytest.raises(Exception)` alrededor de la llamada lo habría
    atrapado (`SystemExit` no es una `Exception`) -- y puede interrumpir la
    ejecución de OTRO test por completo bajo pytest-asyncio.

    Este test ocupa el puerto de la API con un socket real ANTES de llamar a
    `run()`, para forzar ese camino exacto contra el `uvicorn.Server` DE
    VERDAD (no un doble), y verifica que `run()` termine con una excepción
    NORMAL y que el `finally` de limpieza (`pg_handle.cleanup`) siga
    corriendo -- exactamente lo que `_run_background` existe para
    garantizar.
    """
    import socket as socket_module

    import edecan_local.migrate as migrate_module
    import edecan_local.pg as pg_module
    import edecan_local.worker_loop as worker_loop_module

    class _FakePgHandle:
        def __init__(self) -> None:
            self.cleanup_called = False

        def cleanup(self) -> None:
            self.cleanup_called = True

    fake_pg_handle = _FakePgHandle()

    async def fake_ensure_postgres(data_dir: Path) -> tuple[str, _FakePgHandle]:
        return "postgresql+asyncpg://u:p@h:5432/d", fake_pg_handle

    monkeypatch.setattr(pg_module, "ensure_postgres", fake_ensure_postgres)
    monkeypatch.setattr(migrate_module, "run_migrations", lambda database_url: None)
    monkeypatch.setattr(rt, "_import_api_app", lambda: _FAKE_API_APP)
    monkeypatch.setattr(rt, "_import_api_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(rt, "HEALTHZ_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(rt, "HEALTHZ_MAX_ATTEMPTS", 30)

    class _FakeDepsCM:
        def __init__(self, settings: Any) -> None:
            pass

        async def __aenter__(self) -> Any:
            return SimpleNamespace()

        async def __aexit__(self, *exc_info: object) -> bool:
            return False

    async def fake_run_forever(deps: Any, *, stop_event: asyncio.Event) -> None:
        await stop_event.wait()

    monkeypatch.setattr(worker_loop_module, "build_local_deps", _FakeDepsCM)
    monkeypatch.setattr(worker_loop_module, "run_forever", fake_run_forever)

    puerto_ocupado = _TEST_PORT + 90
    bloqueador = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_STREAM)
    bloqueador.setsockopt(socket_module.SOL_SOCKET, socket_module.SO_REUSEADDR, 1)
    bloqueador.bind(("127.0.0.1", puerto_ocupado))
    bloqueador.listen(1)
    try:
        with pytest.raises(Exception) as excinfo:  # noqa: PT011 - el punto es "no SystemExit"
            await asyncio.wait_for(
                rt.run(port=puerto_ocupado, data_dir=str(tmp_path), no_web=True),
                timeout=15,
            )

        # El bug de raíz que este test reproduce: sin `_run_background`, acá
        # se escapaba un `SystemExit` crudo -- `pytest.raises(Exception)`
        # NUNCA lo hubiera atrapado (no es una `Exception`).
        assert isinstance(excinfo.value, Exception)

        # El `finally` de `run()` sigue corriendo pase lo que pase.
        assert fake_pg_handle.cleanup_called is True
    finally:
        bloqueador.close()


async def test_run_sin_ollama_arrancado_no_intenta_detenerlo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`maybe_start_ollama` devolviendo `None` (autostart apagado, el caso
    normal hoy) no debe dejar nada que "detener" -- ningún `AttributeError`
    ni llamada fantasma en el `finally`."""
    import edecan_local.migrate as migrate_module
    import edecan_local.ollama_supervisor as ollama_supervisor_module
    import edecan_local.pg as pg_module
    import edecan_local.worker_loop as worker_loop_module

    class _FakePgHandle:
        def __init__(self) -> None:
            self.cleanup_called = False

        def cleanup(self) -> None:
            self.cleanup_called = True

    fake_pg_handle = _FakePgHandle()

    async def fake_ensure_postgres(data_dir: Path) -> tuple[str, _FakePgHandle]:
        return "postgresql+asyncpg://u:p@h:5432/d", fake_pg_handle

    monkeypatch.setattr(pg_module, "ensure_postgres", fake_ensure_postgres)
    monkeypatch.setattr(rt, "HEALTHZ_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr(migrate_module, "run_migrations", lambda database_url: None)
    monkeypatch.setattr(rt, "_import_api_app", lambda: _FAKE_API_APP)
    monkeypatch.setattr(rt, "_import_api_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(ollama_supervisor_module, "maybe_start_ollama", lambda settings: None)

    class _FakeDepsCM:
        def __init__(self, settings: Any) -> None:
            pass

        async def __aenter__(self) -> Any:
            return SimpleNamespace()

        async def __aexit__(self, *exc_info: object) -> bool:
            return False

    async def fake_run_forever(deps: Any, *, stop_event: asyncio.Event) -> None:
        await stop_event.wait()

    monkeypatch.setattr(worker_loop_module, "build_local_deps", _FakeDepsCM)
    monkeypatch.setattr(worker_loop_module, "run_forever", fake_run_forever)

    stop_event = asyncio.Event()

    async def _detener_pronto() -> None:
        await asyncio.sleep(0.2)
        stop_event.set()

    trigger_task = asyncio.create_task(_detener_pronto())
    try:
        # `try/finally` (en vez de un `await trigger_task` suelto después):
        # si `rt.run(...)` lanza por CUALQUIER motivo (bug real, timeout de
        # `wait_for`, o el `SystemExit` crudo que `_run_background` existe
        # para evitar -- ver su docstring), `trigger_task` no debe quedar
        # colgando del event loop de este test más allá de este test (fuga
        # de tareas asyncio entre archivos de test).
        await asyncio.wait_for(
            rt.run(
                port=_TEST_PORT + 60,
                data_dir=str(tmp_path),
                no_web=True,
                stop_event=stop_event,
            ),
            timeout=15,
        )
    finally:
        trigger_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await trigger_task

    assert fake_pg_handle.cleanup_called is True


async def test_run_sirve_healthz_real_y_objectstore_real_mientras_corre(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Complementa el test anterior: mientras `run()` está "arriba" (antes
    de pedirle que pare), `/healthz` y el object store responden de verdad
    por loopback -- no son solo mocks de las funciones que los construyen."""
    import edecan_local.migrate as migrate_module
    import edecan_local.pg as pg_module
    import edecan_local.worker_loop as worker_loop_module

    class _FakePgHandle:
        def cleanup(self) -> None:
            return None

    async def fake_ensure_postgres(data_dir: Path) -> tuple[str, _FakePgHandle]:
        return "postgresql+asyncpg://u:p@h:5432/d", _FakePgHandle()

    monkeypatch.setattr(pg_module, "ensure_postgres", fake_ensure_postgres)
    monkeypatch.setattr(migrate_module, "run_migrations", lambda database_url: None)
    monkeypatch.setattr(rt, "_import_api_app", lambda: _FAKE_API_APP)
    monkeypatch.setattr(rt, "_import_api_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(rt, "HEALTHZ_INTERVAL_SECONDS", 0.02)

    class _FakeDepsCM:
        def __init__(self, settings: Any) -> None:
            pass

        async def __aenter__(self) -> Any:
            return SimpleNamespace()

        async def __aexit__(self, *exc_info: object) -> bool:
            return False

    async def fake_run_forever(deps: Any, *, stop_event: asyncio.Event) -> None:
        await stop_event.wait()

    monkeypatch.setattr(worker_loop_module, "build_local_deps", _FakeDepsCM)
    monkeypatch.setattr(worker_loop_module, "run_forever", fake_run_forever)

    port = _TEST_PORT + 10
    objectstore_port = port + rt.OBJECTSTORE_PORT_OFFSET
    stop_event = asyncio.Event()
    ready_seen: dict[str, Any] = {}

    async def _probar_mientras_corre() -> None:
        # Poll activo (no un sleep fijo): evita depender de cuánto tarde en
        # los hecho el arranque real del runner bajo carga del sistema.
        async with real_httpx.AsyncClient() as client:
            healthz = None
            for _ in range(200):
                try:
                    healthz = await client.get(f"http://127.0.0.1:{port}/healthz")
                    if healthz.status_code == 200:
                        break
                except real_httpx.HTTPError:
                    pass
                await asyncio.sleep(0.02)
            assert healthz is not None and healthz.status_code == 200, "el runner nunca respondió"
            ready_seen["healthz_status"] = healthz.status_code

            put = await client.put(
                f"http://127.0.0.1:{objectstore_port}/{rt.S3_BUCKET_NAME}/k.txt",
                content=b"hola",
            )
            ready_seen["put_status"] = put.status_code
            get = await client.get(f"http://127.0.0.1:{objectstore_port}/{rt.S3_BUCKET_NAME}/k.txt")
            ready_seen["get_body"] = get.content

        stop_event.set()

    prober_task = asyncio.create_task(_probar_mientras_corre())
    try:
        # Mismo criterio que en test_run_sin_ollama_arrancado_no_intenta_
        # detenerlo: `try/finally` para que `prober_task` no quede colgando
        # del event loop de este test si `rt.run(...)` lanza antes de que
        # el prober llegue a marcar `stop_event` él mismo.
        await asyncio.wait_for(
            rt.run(port=port, data_dir=str(tmp_path), no_web=True, stop_event=stop_event),
            timeout=15,
        )
    finally:
        prober_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await prober_task

    assert ready_seen["healthz_status"] == 200
    assert ready_seen["put_status"] == 200
    assert ready_seen["get_body"] == b"hola"


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


def test_main_parsea_flags_y_delega_en_asyncio_run(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}

    def fake_asyncio_run(coro: Any) -> None:
        calls["coro"] = coro
        coro.close()  # evita el warning de "coroutine never awaited"

    run_calls: list[dict[str, Any]] = []

    def fake_run(*, port: int, data_dir: str, no_web: bool, mobile_access: bool) -> Any:
        run_calls.append(
            {
                "port": port,
                "data_dir": data_dir,
                "no_web": no_web,
                "mobile_access": mobile_access,
            }
        )

        async def _noop() -> None:
            return None

        return _noop()

    monkeypatch.setattr(rt.asyncio, "run", fake_asyncio_run)
    monkeypatch.setattr(rt, "run", fake_run)

    rt.main(["--port", "1234", "--data-dir", "/tmp/y", "--no-web", "--mobile-access"])

    assert run_calls == [
        {"port": 1234, "data_dir": "/tmp/y", "no_web": True, "mobile_access": True}
    ]
    assert "coro" in calls
