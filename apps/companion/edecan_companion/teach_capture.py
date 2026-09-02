"""Puente desde una captura de pasos (p. ej. `navegar_web_interactivo`) hacia
"enseñar una tarea" (product design): publica un paso ESTRUCTURADO en una
sesión abierta vía `POST /v1/skills/teach/{id}/step`.

El companion corre en la máquina del usuario y NO lleva credenciales de la API
embebidas: la base URL y el token Bearer se leen de las variables de entorno
`EDECAN_API_URL` (default `http://127.0.0.1:8000`) y `EDECAN_API_TOKEN`. Sin
token, la petición sale sin cabecera de autorización (y la API la rechazará
con 401) — nunca se fabrica una sesión ni un paso en local.

`registrar_paso_teach` es el contrato único para que cualquier captura (pasos
de navegador, o los que registre la UI del IDE) alimente la MISMA sesión de
enseñanza que compila `POST /v1/skills/teach/{id}/finish` en un draft.

`capturar_paso_navegacion` es la capa de dominio por encima de ese contrato:
convierte UNA acción del navegador (`navegar_web_interactivo`) en un paso
estructurado `{action, selector, decision, input, output}` y lo persiste en
la sesión. Su única responsabilidad extra —y por la que existe— es **no
fabricar éxito**: si la acción del navegador falló, el paso se registra como
DECISIÓN (la falla vive en `decision`), nunca como una «Acción» que nunca
ocurrió.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

_API_URL_DEFECTO = "http://127.0.0.1:8000"
_TIMEOUT_SEGUNDOS = 20.0


def _base_url() -> str:
    return os.environ.get("EDECAN_API_URL", _API_URL_DEFECTO).rstrip("/")


def _token() -> str | None:
    token = os.environ.get("EDECAN_API_TOKEN", "").strip()
    return token or None


async def registrar_paso_teach(
    session_id: str,
    *,
    accion: str = "",
    selector: str = "",
    decision: str = "",
    input: str = "",
    output: str = "",
) -> dict[str, Any]:
    """Añade un paso estructurado a la sesión de enseñanza `session_id`.

    Publica `{accion, selector, decision, input, output}` a
    `POST /v1/skills/teach/{session_id}/step` (la clave `accion` se acepta tal
    cual; `edecan_api.routers.skills.TeachStepIn` también admite `action`). La
    API devuelve la sesión con `pasos` ya acumulados.

    Lanza `httpx.HTTPError` si la API no está disponible o rechaza la petición
    (p. ej. 401 sin token, 404 sesión inexistente/finalizada) — el llamador
    decide cómo degradar; este helper nunca inventa un éxito.
    """
    body: dict[str, str] = {
        "accion": accion,
        "selector": selector,
        "decision": decision,
        "input": input,
        "output": output,
    }
    headers = {"Authorization": f"Bearer {_token()}"} if _token() else {}
    async with httpx.AsyncClient(timeout=_TIMEOUT_SEGUNDOS) as http:
        response = await http.post(
            f"{_base_url()}/v1/skills/teach/{session_id}/step", json=body, headers=headers
        )
        response.raise_for_status()
        return response.json()


async def capturar_paso_navegacion(
    session_id: str,
    *,
    accion: str = "",
    url: str = "",
    selector: str = "",
    decision: str = "",
    output: str = "",
) -> dict[str, Any]:
    """Registra UNA acción del navegador como paso estructurado de la sesión.

    El puente de dominio desde `navegar_web_interactivo` hacia la sesión de
    enseñanza: recibe la acción intentada (`accion`), la URL destino, el
    selector CSS y lo observado (`decision`/`output`) y lo persiste vía
    `registrar_paso_teach` como un paso `{action, selector, decision, input,
    output}` — la URL viaja en `input` (el input de una acción de navegador
    es, precisamente, la URL).

    **No fabrica éxito** (`AGENTS.md` §13.1). El estado de la acción lo
    expresa `accion`:

    - Acción REALIZADA: `accion` no vacío → se registra el paso con
      `action=accion`, más `selector`/`decision`/`output` como notas.
    - Acción FALLIDA: el llamador deja `accion=""` y pone la causa en
      `decision` → el paso se registra como DECISIÓN (`action=""`), nunca
      como un `action` de éxito que no ocurrió. Así el SKILL.md compilado
      (`_contenido_desde_pasos` del router) jamás muestra una «Acción» falsa.
    - Ni acción ni decisión: no hay NADA real que registrar — se lanza
      `ValueError` en vez de publicar un paso vacío que simularía un éxito.

    Persiste igual que `registrar_paso_teach`: lanza `httpx.HTTPError` si la
    API rechaza la petición; nunca inventa una sesión ni un paso en local.
    """
    accion = (accion or "").strip()
    url = (url or "").strip()
    selector = (selector or "").strip()
    decision = (decision or "").strip()
    output = (output or "").strip()

    if not accion and not decision:
        raise ValueError(
            "capturar_paso_navegacion: no hay acción ni decisión que registrar — "
            "rehuso publicar un paso vacío (sería fabricar un éxito)."
        )

    if not accion:
        # La acción del navegador falló: se captura como DECISIÓN, nunca como
        # un `action` de éxito que nunca ocurrió.
        return await registrar_paso_teach(
            session_id,
            accion="",
            selector="",
            decision=decision,
            input=url,
            output=output,
        )

    return await registrar_paso_teach(
        session_id,
        accion=accion,
        selector=selector,
        decision=decision,
        input=url,
        output=output,
    )
