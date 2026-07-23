"""`edecan_local.pg.ensure_postgres` — modo avanzado (`EDECAN_DATABASE_URL`)
y modo embebido (`pgserver`) (ARCHITECTURE.md §12f, WP-V3-05).

Offline por defecto: `pgserver` se fakea vía `sys.modules` (mismo truco que
`test_run_campaign_step.py` usa para `edecan_premium` — ver docstring de
`objectstore.py`/`test_objectstore.py` para más contexto de este patrón en
el repo) y `_create_vector_extension` se monkeypatchea directo (necesita
asyncpg contra un Postgres real, fuera de alcance de estos tests). El único
test que SÍ arranca un Postgres embebido real está marcado
`@pytest.mark.integration` y se salta si `pgserver` no está disponible (por
ejemplo en una arquitectura sin wheel publicado) — si corre, el `finally` garantiza
`handle.cleanup()` siempre, incluso si una aserción falla (regla dura del
repo: nunca dejar nada corriendo huérfano).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import edecan_local.pg as pg_module
import pytest
from edecan_local.pg import ensure_postgres


@pytest.fixture(autouse=True)
def _sin_pgserver_instalado_de_verdad(monkeypatch: pytest.MonkeyPatch) -> None:
    """Por defecto, ningún test de este módulo debe tocar el paquete
    `pgserver` real (si por casualidad estuviera instalado en el entorno) --
    cada test que SÍ necesita un `pgserver` fake lo instala explícito."""
    monkeypatch.delenv("EDECAN_DATABASE_URL", raising=False)


# ---------------------------------------------------------------------------
# Conversión de URI/DSN
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("postgresql://u:p@h:5432/d", "postgresql+asyncpg://u:p@h:5432/d"),
        ("postgres://u:p@h:5432/d", "postgresql+asyncpg://u:p@h:5432/d"),
        ("postgresql+asyncpg://u:p@h:5432/d", "postgresql+asyncpg://u:p@h:5432/d"),
    ],
)
def test_to_asyncpg_url_convierte_esquemas_conocidos(uri: str, expected: str) -> None:
    assert pg_module._to_asyncpg_url(uri) == expected


def test_to_asyncpg_url_esquema_inesperado_lanza_runtime_error() -> None:
    with pytest.raises(RuntimeError):
        pg_module._to_asyncpg_url("mysql://u:p@h/d")


def test_to_asyncpg_dsn_es_el_inverso() -> None:
    assert pg_module._to_asyncpg_dsn("postgresql+asyncpg://u:p@h:5432/d") == (
        "postgresql://u:p@h:5432/d"
    )


# ---------------------------------------------------------------------------
# Modo avanzado: EDECAN_DATABASE_URL
# ---------------------------------------------------------------------------


async def test_ensure_postgres_modo_avanzado_usa_edecan_database_url_tal_cual(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("EDECAN_DATABASE_URL", "postgresql+asyncpg://u:p@remoto:5432/d")

    database_url, handle = await ensure_postgres(tmp_path)

    assert database_url == "postgresql+asyncpg://u:p@remoto:5432/d"
    handle.cleanup()  # no-op: este proceso no es dueño de ese Postgres.


async def test_ensure_postgres_modo_avanzado_no_toca_pgserver_ni_data_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("EDECAN_DATABASE_URL", "postgresql+asyncpg://u:p@remoto:5432/d")
    # Si `ensure_postgres` intentara importar pgserver en modo avanzado, esto
    # forzaría un ImportError y el test fallaría con ese traceback en vez de
    # con el mensaje claro esperado -- confirma que ni se intenta.
    monkeypatch.setitem(sys.modules, "pgserver", None)

    database_url, _handle = await ensure_postgres(tmp_path)

    assert database_url == "postgresql+asyncpg://u:p@remoto:5432/d"
    assert not (tmp_path / "pg").exists()


# ---------------------------------------------------------------------------
# Modo embebido (pgserver fakeado)
# ---------------------------------------------------------------------------


async def test_ensure_postgres_sin_pgserver_instalado_lanza_runtime_error_claro(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setitem(sys.modules, "pgserver", None)  # fuerza ImportError determinista

    with pytest.raises(RuntimeError) as exc_info:
        await ensure_postgres(tmp_path)

    mensaje = str(exc_info.value)
    assert "pgserver" in mensaje
    assert "wheel publicado" in mensaje
    assert "EDECAN_DATABASE_URL" in mensaje


async def test_ensure_postgres_modo_embebido_arranca_pgserver_y_convierte_uri(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class _FakeServer:
        """`get_uri()` como MÉTODO, no un atributo `.uri` -- así es la forma
        real de `pgserver.PostgresServer` (0.1.4, verificado empíricamente
        arrancando el paquete real, WP-V7-11): un fake con `.uri` como
        atributo deja pasar en silencio exactamente el `AttributeError` que
        ese desajuste causó en producción."""

        def __init__(self) -> None:
            self._uri = "postgresql://postgres@/postgres?host=/tmp/algun-socket"
            self.cleanup_called = False

        def get_uri(self) -> str:
            return self._uri

        def cleanup(self) -> None:
            self.cleanup_called = True

    fake_server = _FakeServer()
    get_server_calls: list[str] = []

    def fake_get_server(path: str) -> _FakeServer:
        get_server_calls.append(path)
        return fake_server

    monkeypatch.setitem(sys.modules, "pgserver", SimpleNamespace(get_server=fake_get_server))

    extension_calls: list[str] = []

    async def fake_create_vector_extension(database_url: str) -> None:
        extension_calls.append(database_url)

    monkeypatch.setattr(pg_module, "_create_vector_extension", fake_create_vector_extension)

    database_url, handle = await ensure_postgres(tmp_path)

    assert database_url == "postgresql+asyncpg://postgres@/postgres?host=/tmp/algun-socket"
    assert get_server_calls == [str(tmp_path / "pg")]
    assert (tmp_path / "pg").is_dir()
    assert extension_calls == [database_url]

    handle.cleanup()
    assert fake_server.cleanup_called is True


async def test_ensure_postgres_modo_embebido_nunca_lee_un_atributo_uri(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regresión dedicada del bug real (WP-V7-11, visto en vivo corriendo
    `python -m edecan_local` contra el paquete `pgserver` 0.1.4 instalado):
    `ensure_postgres` accedía a `server.uri` (atributo), pero
    `pgserver.PostgresServer` real solo expone `get_uri()` (método) -- el
    primer arranque real terminaba en `AttributeError: 'PostgresServer'
    object has no attribute 'uri'`, invisible para el resto de tests de este
    archivo porque sus fakes SÍ definían `.uri`. Este fake, a propósito, NO
    define `.uri` en absoluto (ni como atributo ni como método) -- cualquier
    intento de leerlo revienta con `AttributeError` de inmediato, así que
    este test solo puede pasar si el código de producción usa `get_uri()`."""

    class _FakeServerSinAtributoUri:
        def get_uri(self) -> str:
            return "postgresql://postgres@/postgres?host=/tmp/sin-atributo-uri"

        def cleanup(self) -> None:
            return None

    monkeypatch.setitem(
        sys.modules,
        "pgserver",
        SimpleNamespace(get_server=lambda path: _FakeServerSinAtributoUri()),
    )

    async def fake_create_vector_extension(database_url: str) -> None:
        return None

    monkeypatch.setattr(pg_module, "_create_vector_extension", fake_create_vector_extension)

    database_url, handle = await ensure_postgres(tmp_path)

    assert database_url == "postgresql+asyncpg://postgres@/postgres?host=/tmp/sin-atributo-uri"
    handle.cleanup()


async def test_ensure_postgres_data_dir_con_tilde_se_expande(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    class _FakeServer:
        """`get_uri()` como método -- ver el comentario equivalente en
        `test_ensure_postgres_modo_embebido_arranca_pgserver_y_convierte_uri`."""

        def get_uri(self) -> str:
            return "postgresql://postgres@/postgres?host=/tmp/x"

        def cleanup(self) -> None:
            return None

    monkeypatch.setitem(
        sys.modules, "pgserver", SimpleNamespace(get_server=lambda path: _FakeServer())
    )

    async def fake_create_vector_extension(database_url: str) -> None:
        return None

    monkeypatch.setattr(pg_module, "_create_vector_extension", fake_create_vector_extension)

    _database_url, handle = await ensure_postgres(Path("~"))

    assert (tmp_path / "pg").is_dir()
    handle.cleanup()


async def test_ensure_postgres_data_dir_con_espacio_usa_alternativo_sin_espacio(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regresión dedicada (2026-07-09, ver HOTFIXES_PENDIENTES.md): un
    `data_dir` con un espacio (el caso REAL garantizado en macOS —
    `app.path().app_data_dir()` de Tauri resuelve a `~/Library/Application
    Support/<bundle-id>/data`, "Application Support" siempre tiene ese
    espacio) hace que `pgserver` arranque Postgres con `pg_ctl ... -o "-k
    <socket_dir>"`, y ese `-o` NO es quote-aware — revienta con `postgres:
    invalid argument`. Verificado en vivo corriendo el binario empaquetado
    real contra esa ruta exacta. `ensure_postgres` debe redirigir a un
    directorio alternativo sin espacios, nunca pasarle la ruta natural
    (con espacio) a `pgserver.get_server`."""
    monkeypatch.setenv("HOME", str(tmp_path))

    class _FakeServer:
        def get_uri(self) -> str:
            return "postgresql://postgres@/postgres?host=/tmp/con-espacio"

        def cleanup(self) -> None:
            return None

    get_server_calls: list[str] = []

    def fake_get_server(path: str) -> _FakeServer:
        get_server_calls.append(path)
        return _FakeServer()

    monkeypatch.setitem(sys.modules, "pgserver", SimpleNamespace(get_server=fake_get_server))

    async def fake_create_vector_extension(database_url: str) -> None:
        return None

    monkeypatch.setattr(pg_module, "_create_vector_extension", fake_create_vector_extension)

    data_dir_con_espacio = (
        tmp_path / "Library" / "Application Support" / "cc.edecan.desktop" / "data"
    )

    _database_url, handle = await ensure_postgres(data_dir_con_espacio)

    assert len(get_server_calls) == 1
    ruta_usada = get_server_calls[0]
    assert " " not in ruta_usada, f"pgserver.get_server recibió ruta con espacio: {ruta_usada!r}"
    assert str(tmp_path / ".edecan-pg") in ruta_usada
    handle.cleanup()

    # Determinístico: el mismo data_dir siempre produce el mismo alternativo
    # (no depende de aleatoriedad ni del PID del proceso).
    _database_url_2, handle_2 = await ensure_postgres(data_dir_con_espacio)
    assert get_server_calls[1] == ruta_usada
    handle_2.cleanup()


def test_safe_pg_data_dir_deja_rutas_sin_espacio_tal_cual(tmp_path: Path) -> None:
    sin_espacio = tmp_path / "edecan" / "data"
    assert pg_module._safe_pg_data_dir(sin_espacio) == sin_espacio / "pg"


def test_embedded_handle_cleanup_nunca_lanza_aunque_pgserver_falle() -> None:
    class _FakeServerRoto:
        def cleanup(self) -> None:
            raise RuntimeError("pgserver ya estaba muerto")

    handle = pg_module._EmbeddedHandle(_FakeServerRoto())
    handle.cleanup()  # no debe propagar la excepción


def test_runtime_postgres_es_inutil_si_desaparecio_el_vector(tmp_path: Path) -> None:
    postgres = tmp_path / "pginstall" / "bin" / "postgres"
    postgres.parent.mkdir(parents=True)
    postgres.touch()

    assert pg_module._postgres_runtime_is_usable(postgres) is False

    vector = tmp_path / "pginstall" / "lib" / "postgresql" / "vector.dylib"
    vector.parent.mkdir(parents=True)
    vector.touch()
    assert pg_module._postgres_runtime_is_usable(postgres) is True


def test_recupera_postgres_huerfano_de_runtime_temporal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pgdata = tmp_path / "pgdata"
    pgdata.mkdir()
    pid = 43127
    (pgdata / "postmaster.pid").write_text(f"{pid}\n", encoding="utf-8")
    missing_postgres = tmp_path / "_MEI_eliminado" / "pginstall" / "bin" / "postgres"

    class _FakeProcess:
        terminated = False
        waited = False

        def is_running(self) -> bool:
            return True

        def cmdline(self) -> list[str]:
            return [str(missing_postgres), "-D", str(pgdata)]

        def exe(self) -> str:
            return ""

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, timeout: int) -> None:
            assert timeout == 5
            self.waited = True

        def kill(self) -> None:  # pragma: no cover - solo si no termina a tiempo
            raise AssertionError("no debía necesitar SIGKILL")

    process = _FakeProcess()

    class _FakePsutil:
        class Error(Exception):
            pass

        class TimeoutExpired(Error):
            pass

        @staticmethod
        def Process(requested_pid: int) -> _FakeProcess:
            assert requested_pid == pid
            return process

    monkeypatch.setitem(sys.modules, "psutil", _FakePsutil)

    assert pg_module._recover_orphaned_embedded_postgres(pgdata) is True
    assert process.terminated is True
    assert process.waited is True


def test_no_toca_postgres_con_runtime_completo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pgdata = tmp_path / "pgdata"
    pgdata.mkdir()
    pid = 43128
    (pgdata / "postmaster.pid").write_text(f"{pid}\n", encoding="utf-8")
    postgres = tmp_path / "runtime" / "pginstall" / "bin" / "postgres"
    postgres.parent.mkdir(parents=True)
    postgres.touch()
    vector = postgres.parent.parent / "lib" / "postgresql" / "vector.dylib"
    vector.parent.mkdir(parents=True)
    vector.touch()

    class _FakeProcess:
        def is_running(self) -> bool:
            return True

        def cmdline(self) -> list[str]:
            return [str(postgres), "-D", str(pgdata)]

        def exe(self) -> str:
            return str(postgres)

        def terminate(self) -> None:
            raise AssertionError("no debe terminar un runtime completo")

    process = _FakeProcess()

    class _FakePsutil:
        class Error(Exception):
            pass

        class TimeoutExpired(Error):
            pass

        @staticmethod
        def Process(requested_pid: int) -> _FakeProcess:
            assert requested_pid == pid
            return process

    monkeypatch.setitem(sys.modules, "psutil", _FakePsutil)

    assert pg_module._recover_orphaned_embedded_postgres(pgdata) is False


def test_noop_handle_cleanup_no_hace_nada() -> None:
    pg_module._NoopHandle().cleanup()


# ---------------------------------------------------------------------------
# Integration: pgserver real (se salta si no hay wheel para la plataforma).
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_ensure_postgres_embebido_real_con_pgserver(tmp_path: Path) -> None:
    pytest.importorskip("pgserver")

    database_url, handle = await ensure_postgres(tmp_path)
    try:
        import asyncpg

        dsn = pg_module._to_asyncpg_dsn(database_url)
        conn = await asyncpg.connect(dsn)
        try:
            assert await conn.fetchval("SELECT 1") == 1
            extname = await conn.fetchval(
                "SELECT extname FROM pg_extension WHERE extname = 'vector'"
            )
            assert extname == "vector"
        finally:
            await conn.close()
    finally:
        handle.cleanup()
