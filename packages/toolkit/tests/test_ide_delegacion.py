"""`delegar_al_ide` contra la app Edecán INSTALADA de esta Mac (nivel 1).

No hay mocks: el test lee la capability efímera del proceso `edecan-local`
vivo (el mismo camino que usa la tool dentro del sidecar), abre la sesión
local con `/v1/auth/local` y delega un encargo REAL de solo lectura al IDE.
Si la app no está corriendo en 127.0.0.1:8765 el test FALLA — porque en esta
Mac la app siempre debe estar viva, y un fallo aquí significa exactamente eso.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import respx
from edecan_core import ToolContext
from edecan_toolkit.ide_delegacion import DelegarAlIDETool, _fijar_modo_restringido

_PUERTO = 8765
_BASE = f"http://127.0.0.1:{_PUERTO}"


def _capability_viva() -> str:
    """Capability efímera del proceso sidecar (cambia en cada arranque de la app)."""
    pid_raw = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{_PUERTO}", "-sTCP:LISTEN", "-t"],
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    assert pid_raw, f"no hay ningún proceso escuchando en {_PUERTO}: abre la app Edecán"
    pid = pid_raw.splitlines()[0]
    entorno = subprocess.run(
        ["ps", "eww", str(pid)], capture_output=True, text=True, timeout=10
    ).stdout
    for token in entorno.split():
        if token.startswith("LOCAL_DESKTOP_CAPABILITY="):
            valor = token.split("=", 1)[1].strip()
            assert valor, "capability vacía en el proceso sidecar"
            return valor
    pytest.fail("el proceso sidecar no expone LOCAL_DESKTOP_CAPABILITY")


async def test_delegar_al_ide_completa_un_encargo_real_de_solo_lectura(monkeypatch):
    try:
        salud = subprocess.run(
            ["curl", "-s", "-m", "5", "-o", "/dev/null",
             f"http://127.0.0.1:{_PUERTO}/healthz"],
            timeout=10,
        )
        app_viva = salud.returncode == 0
    except (subprocess.SubprocessError, OSError):
        app_viva = False
    if not app_viva:
        # Integración real: sin la app Edecán corriendo en esta máquina no
        # hay nada que probar (mismo criterio que las integraciones del
        # companion que se saltan sin credenciales — nunca un falso verde).
        pytest.skip("la app Edecán no está corriendo en esta máquina")

    cap = _capability_viva()
    monkeypatch.setenv("LOCAL_DESKTOP_CAPABILITY", cap)

    ctx = ToolContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        session=None,
        settings=SimpleNamespace(LOCAL_API_PORT=_PUERTO),
        llm=None,
        vault=None,
        extras={},
    )
    resultado = await DelegarAlIDETool().run(
        ctx,
        {
            "prompt": (
                "Encargo de SOLO LECTURA: lista los archivos y carpetas de la raíz "
                "del workspace (no edites ni borres NADA, no ejecutes comandos de "
                "escritura). Responde ÚNICAMENTE con el número total de entradas de "
                "la raíz."
            ),
            "modo": "auto",
            "max_espera_segundos": 240,
        },
    )

    assert resultado.data is not None
    assert resultado.data["status"] == "completed", (
        f"el encargo no terminó bien: {resultado.content[:300]}"
    )
    assert resultado.content.strip(), "el resultado vino vacío"
    assert "[IDE completado]" in resultado.content


@respx.mock
async def test_delegar_al_ide_aborta_si_falla_fijar_modo_restringido(make_ctx, monkeypatch):
    """C2: si el PUT del modo restringido falla, la delegación ABORTA — nunca
    sigue en `auto` (autonomía total sin frenos). Sin poll posterior al agente."""
    monkeypatch.setenv("LOCAL_DESKTOP_CAPABILITY", "cap-test")
    respx.get(f"{_BASE}/healthz").mock(return_value=httpx.Response(200))
    respx.post(f"{_BASE}/v1/auth/local").mock(
        return_value=httpx.Response(200, json={"access_token": "tok"})
    )
    respx.get(f"{_BASE}/v1/ide/workspaces").mock(
        return_value=httpx.Response(200, json={"workspaces": [{"id": "ws1"}]})
    )
    respx.post(f"{_BASE}/v1/ide/agents").mock(
        return_value=httpx.Response(200, json={"id": "session-1"})
    )
    modo_route = respx.put(f"{_BASE}/v1/ide/agents/session-1/modo").mock(
        return_value=httpx.Response(500)
    )
    poll_route = respx.get(f"{_BASE}/v1/ide/agents/session-1").mock(
        return_value=httpx.Response(200, json={})
    )

    ctx = make_ctx(settings=SimpleNamespace(LOCAL_API_PORT=_PUERTO))
    resultado = await DelegarAlIDETool().run(
        ctx, {"prompt": "edita el repo", "modo": "manual"}
    )

    assert "Aborté" in resultado.content
    assert modo_route.called
    assert not poll_route.called


@respx.mock
async def test_fijar_modo_restringido_valida_estado_real_y_aborta_si_no_coincide():
    """El PUT es idempotente-confirmatorio: un 2xx no es prueba del freno aplicado.
    Si el GET de releer el modo devuelve algo distinto de lo pedido, se aborta."""
    respx.put(f"{_BASE}/v1/ide/agents/s-1/modo").mock(
        return_value=httpx.Response(200, json={"modo": "plan"})
    )
    respx.get(f"{_BASE}/v1/ide/agents/s-1/modo").mock(
        return_value=httpx.Response(200, json={"modo": "auto"})
    )

    async with httpx.AsyncClient() as client:
        resultado = await _fijar_modo_restringido(client, _BASE, {}, "s-1", "manual")

    assert resultado is not None
    assert "pedí 'manual'" in resultado.content
    assert "auto" in resultado.content


@respx.mock
async def test_fijar_modo_restringido_ok_cuando_estado_real_coincide():
    respx.put(f"{_BASE}/v1/ide/agents/s-1/modo").mock(
        return_value=httpx.Response(200, json={"modo": "manual"})
    )
    respx.get(f"{_BASE}/v1/ide/agents/s-1/modo").mock(
        return_value=httpx.Response(200, json={"modo": "manual"})
    )

    async with httpx.AsyncClient() as client:
        resultado = await _fijar_modo_restringido(client, _BASE, {}, "s-1", "manual")

    assert resultado is None
