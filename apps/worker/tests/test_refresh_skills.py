"""`refresh_skills`: re-importación semanal idempotente de skills de catálogos.

Cubre (ver el docstring del handler):
- fail-open entre fuentes (una ok + una rota -> sigue y retorna limpio);
- upsert idempotente delegado a `edecan_skills.store.insert_skill` (mismo
  `nombre` -> mismo slug -> ON CONFLICT actualiza, jamás duplica);
- fallback al dueño cuando no hay tenants con skills;
- parse del tarball con topes de tamaño;
- registro defensivo del job type en `HANDLERS`/`JOB_TYPES`.
"""

from __future__ import annotations

import inspect
import io
import tarfile
import uuid
from contextlib import asynccontextmanager
from typing import Any

import edecan_worker.handlers.refresh_skills as refresh
import pytest
from edecan_schemas import JOB_TYPES, JobEnvelope
from edecan_worker.handlers import HANDLERS
from fakes import make_deps

TENANT_A = uuid.uuid4()
USER_A = uuid.uuid4()


# ---------------------------------------------------------------------------
# Fakes: tarball, streaming HTTP, sesión de DB
# ---------------------------------------------------------------------------


def _tarball_con_skills(
    skills: list[tuple[str, str, str]],
    *,
    extras: list[tuple[str, bytes]] | None = None,
) -> bytes:
    """Arma en memoria un tar.gz estilo `agent-plugins/plugins/*/skills/*/SKILL.md`.

    `skills`: (carpeta, nombre, descripcion). `extras`: (nombre_de_miembro,
    contenido) para miembros extra (p. ej. un README o un path inválido).
    """
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tf:
        for carpeta, nombre, descripcion in skills:
            contenido = (
                f"---\nname: {nombre}\ndescription: {descripcion}\n---\n# Skill\n"
            ).encode()
            info = tarfile.TarInfo(
                name=f"agent-plugins/plugins/{carpeta}/skills/{nombre}/SKILL.md"
            )
            info.size = len(contenido)
            tf.addfile(info, io.BytesIO(contenido))
        for nombre_miembro, contenido in extras or []:
            info = tarfile.TarInfo(name=nombre_miembro)
            info.size = len(contenido)
            tf.addfile(info, io.BytesIO(contenido))
    return buffer.getvalue()


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _FakeResult:
        return self

    def all(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._rows]


class _FakeSession:
    def __init__(self, tenants: list[tuple[uuid.UUID, uuid.UUID]]) -> None:
        self._tenants = tenants
        self.queries: list[str] = []

    async def execute(self, statement: Any, params: Any = None) -> _FakeResult:
        self.queries.append(str(statement))
        return _FakeResult(
            [{"tenant_id": str(t), "user_id": str(u)} for t, u in self._tenants]
        )

    async def commit(self) -> None:
        pass


@asynccontextmanager
async def _session_factory_con_tenants(
    tenants: list[tuple[uuid.UUID, uuid.UUID]] | None = None,
):
    yield _FakeSession(tenants or [])


def _deps(tenants: list[tuple[uuid.UUID, uuid.UUID]] | None = None) -> Any:
    def session_factory(tenant_id: Any):
        return _session_factory_con_tenants(tenants)

    return make_deps(session_factory=session_factory)


def _env() -> JobEnvelope:
    return JobEnvelope(
        job_id=uuid.uuid4(), tenant_id=None, type="refresh_skills", payload={}
    )


class _FakeStreamResponse:
    def __init__(self, trozos: list[bytes]) -> None:
        self._trozos = trozos

    async def __aenter__(self) -> _FakeStreamResponse:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    def raise_for_status(self) -> None:
        pass

    async def aiter_bytes(self):
        for trozo in self._trozos:
            yield trozo


class _FakeHttp:
    """httpx.AsyncClient falso: `stream` devuelve los trozos dados."""

    def __init__(self, trozos: list[bytes]) -> None:
        self._trozos = trozos
        self.solicitudes: list[tuple[str, str]] = []

    def stream(self, method: str, url: str) -> _FakeStreamResponse:
        self.solicitudes.append((method, url))
        return _FakeStreamResponse(self._trozos)


# ---------------------------------------------------------------------------
# Registro del job type
# ---------------------------------------------------------------------------


def test_registro_del_job_type() -> None:
    assert "refresh_skills" in JOB_TYPES
    assert "refresh_skills" in HANDLERS
    assert inspect.iscoroutinefunction(HANDLERS["refresh_skills"])


# ---------------------------------------------------------------------------
# Fail-open entre fuentes + upsert delegado a insert_skill
# ---------------------------------------------------------------------------


async def test_fuente_ok_y_fuente_rota_sigue(monkeypatch: pytest.MonkeyPatch) -> None:
    skills_ok = [
        {
            "nombre": "alpha",
            "descripcion": "primera",
            "contenido": "# alpha",
            "source": "https://catalogo-ok",
        },
        {
            "nombre": "beta",
            "descripcion": "segunda",
            "contenido": "# beta",
            "source": "https://catalogo-ok",
        },
    ]

    async def fetch_ok(http: Any) -> list[dict[str, str]]:
        return list(skills_ok)

    async def fetch_rota(http: Any) -> list[dict[str, str]]:
        raise RuntimeError("red caída")

    monkeypatch.setattr(
        refresh, "_FUENTES", (("ok", fetch_ok), ("rota", fetch_rota))
    )

    insertadas: list[dict[str, Any]] = []

    async def fake_insert_skill(session: Any, **kwargs: Any) -> dict[str, Any]:
        insertadas.append(dict(kwargs))
        return {"slug": kwargs["nombre"]}

    monkeypatch.setattr(refresh, "insert_skill", fake_insert_skill)

    await refresh.handle(_env(), _deps([(TENANT_A, USER_A)]))

    assert len(insertadas) == 2
    nombres = {i["nombre"] for i in insertadas}
    assert nombres == {"alpha", "beta"}
    for i in insertadas:
        assert i["tenant_id"] == TENANT_A
        assert i["user_id"] == USER_A
        assert i["source"] == "https://catalogo-ok"


async def test_reinstala_sin_duplicar_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dos pasadas del job mandan el MISMO (nombre, source) por tenant: el
    upsert de `insert_skill` (`UNIQUE(tenant_id, slug)`) actualiza la fila
    existente en vez de insertar una duplicada."""
    skill = {
        "nombre": "alpha",
        "descripcion": "d",
        "contenido": "# alpha",
        "source": "https://github.com/awslabs/agent-plugins",
    }

    async def fetch(http: Any) -> list[dict[str, str]]:
        return [dict(skill)]

    monkeypatch.setattr(refresh, "_FUENTES", (("aws", fetch),))
    insertadas: list[dict[str, Any]] = []

    async def fake_insert_skill(session: Any, **kwargs: Any) -> dict[str, Any]:
        insertadas.append(dict(kwargs))
        return {"slug": kwargs["nombre"]}

    monkeypatch.setattr(refresh, "insert_skill", fake_insert_skill)

    await refresh.handle(_env(), _deps([(TENANT_A, USER_A)]))
    await refresh.handle(_env(), _deps([(TENANT_A, USER_A)]))

    assert len(insertadas) == 2
    primera, segunda = insertadas
    # Mismo nombre -> mismo slug -> ON CONFLICT DO UPDATE (no duplica fila).
    assert primera["nombre"] == segunda["nombre"] == "alpha"
    assert primera["source"] == segunda["source"]
    assert primera["tenant_id"] == segunda["tenant_id"] == TENANT_A


async def test_sin_tenants_con_skills_no_inventa_un_destino(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fetch(http: Any) -> list[dict[str, str]]:
        return [{"nombre": "alpha", "descripcion": "", "contenido": "# x", "source": "s"}]

    monkeypatch.setattr(refresh, "_FUENTES", (("aws", fetch),))
    insertadas: list[dict[str, Any]] = []

    async def fake_insert_skill(session: Any, **kwargs: Any) -> dict[str, Any]:
        insertadas.append(dict(kwargs))
        return {"slug": kwargs["nombre"]}

    monkeypatch.setattr(refresh, "insert_skill", fake_insert_skill)

    await refresh.handle(_env(), _deps([]))  # ningún tenant con skills

    assert insertadas == []


async def test_job_nunca_lanza_aunque_todo_falle(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fetch_rota(http: Any) -> list[dict[str, str]]:
        raise RuntimeError("red caída")

    monkeypatch.setattr(refresh, "_FUENTES", (("rota", fetch_rota),))

    async def session_factory_que_explota(tenant_id: Any):
        raise RuntimeError("Postgres caído")
        yield  # pragma: no cover - inalcanzable, mantiene la firma de CM async

    deps = make_deps(session_factory=session_factory_que_explota)
    await refresh.handle(_env(), deps)  # no debe lanzar


async def test_handle_absorbe_un_fallo_global(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refrescar_rota(deps: Any) -> None:
        raise RuntimeError("fallo global")

    monkeypatch.setattr(refresh, "_refrescar", refrescar_rota)
    await refresh.handle(_env(), _deps())  # no debe lanzar


# ---------------------------------------------------------------------------
# Parse del tarball + topes
# ---------------------------------------------------------------------------


def test_parsear_tarball_extrae_skills_y_frontmatter() -> None:
    tarball = _tarball_con_skills(
        [
            ("p1", "alpha", "hace alpha"),
            ("p2", "beta", "hace beta"),
        ],
        extras=[
            ("agent-plugins/README.md", b"no es skill"),
            ("agent-plugins/plugins/p1/README.md", b"tampoco"),
            # Path con `plugins` fuera del nivel esperado: se ignora.
            ("agent-plugins/docs/plugins/p1/skills/fuera/SKILL.md", b"---\nname: fuera\n---\n"),
        ],
    )

    skills = refresh._parsear_tarball(tarball)

    assert [s["nombre"] for s in skills] == ["alpha", "beta"]
    assert [s["descripcion"] for s in skills] == ["hace alpha", "hace beta"]
    assert all(s["contenido"].startswith("---") for s in skills)


def test_parsear_tarball_nombre_fallback_a_la_carpeta() -> None:
    contenido = b"# sin frontmatter\n"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tf:
        info = tarfile.TarInfo(name="agent-plugins/plugins/p1/skills/mi-skill/SKILL.md")
        info.size = len(contenido)
        tf.addfile(info, io.BytesIO(contenido))

    skills = refresh._parsear_tarball(buffer.getvalue())

    assert len(skills) == 1
    assert skills[0]["nombre"] == "mi-skill"


def test_parsear_tarball_salta_miembros_gigantes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(refresh, "_SKILL_MAX_BYTES", 100)
    grande = b"x" * 200
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tf:
        for nombre in ("gigante", "chico"):
            contenido = b"---\nname: ok\n---\n" if nombre == "chico" else grande
            info = tarfile.TarInfo(name=f"agent-plugins/plugins/p1/skills/{nombre}/SKILL.md")
            info.size = len(contenido)
            tf.addfile(info, io.BytesIO(contenido))

    skills = refresh._parsear_tarball(buffer.getvalue())

    assert [s["nombre"] for s in skills] == ["ok"]


async def test_descargar_con_tope_corta_descargas_gigantes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(refresh, "_TARBALL_MAX_BYTES", 100)
    http = _FakeHttp([b"x" * 60, b"y" * 60])

    with pytest.raises(RuntimeError, match="excede el tope"):
        await refresh._descargar_con_tope(http, "https://ejemplo/tarball")  # type: ignore[arg-type]


async def test_fetch_catalogo_aws_parsea_y_sella_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tarball = _tarball_con_skills([("p1", "alpha", "hace alpha")])
    http = _FakeHttp([tarball[:100], tarball[100:]])

    skills = await refresh._fetch_catalogo_aws(http)  # type: ignore[arg-type]

    assert [s["nombre"] for s in skills] == ["alpha"]
    assert all(s["source"] == refresh.FUENTE_AWS for s in skills)
    assert http.solicitudes == [("GET", refresh.TARBALL_AWS)]
