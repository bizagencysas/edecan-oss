"""Tests de los adaptadores de proveedor de la fase 0.

REGLA DURA: aquí no se toca la red. El token de Workers AI existe de verdad en
`.env` y cada llamada cuesta dinero real, así que todo va con `respx` y todo
proveedor se construye con `env_file=None` para que el `.env` del repo no se
lea jamás desde la suite. El único test que sale a internet está marcado
`integration` y se salta salvo que `FORGE_PROBE_INTEGRACION=1` esté puesta —
tener token NO es condición suficiente.
"""

from __future__ import annotations

import json
import os

import httpx
import pytest
import respx
from edecan_forge_probe.providers import (
    CredencialInvalidaError,
    FalloTransitorioError,
    LimiteDeTasaError,
    OllamaProbeAdapter,
    PeticionInvalidaError,
    PresupuestoAgotadoError,
    ProveedorInalcanzableError,
    TiempoAgotadoError,
    WorkersAIProvider,
    cargar_dotenv,
    herramienta_a_workers_ai,
    herramienta_desde_workers_ai,
    mensajes_a_workers_ai,
    tool_call_a_workers_ai,
    tool_call_desde_workers_ai,
)
from edecan_llm.base import ChatMessage, CompletionRequest, ToolCall, ToolSpec

CUENTA = "cuenta-de-prueba"
TOKEN = "token-de-prueba-no-real"
MODELO = "@cf/moonshotai/kimi-k2.7-code"
URL = f"https://api.cloudflare.com/client/v4/accounts/{CUENTA}/ai/run/{MODELO}"

CODIGO_MULTILINEA = (
    "def aplicar(ruta: str) -> None:\n"
    '    """Comentario con acentos: ñ, á, cañón."""\n'
    "    with open(ruta) as fh:\n"
    "        datos = fh.read()\n"
    "    if '\"' in datos:\n"
    "        raise ValueError('comilla \\\\ escapada')\n"
)


class DormirFalso:
    """Sustituye a `asyncio.sleep` y anota cuánto se habría dormido."""

    def __init__(self) -> None:
        self.esperas: list[float] = []

    async def __call__(self, segundos: float) -> None:
        self.esperas.append(segundos)


def hacer_proveedor(**extra: object) -> WorkersAIProvider:
    """Proveedor aislado: sin `.env`, sin backoff real, sin jitter aleatorio."""
    opciones: dict[str, object] = {
        "account_id": CUENTA,
        "api_token": TOKEN,
        "model": MODELO,
        "env_file": None,
        "backoff_base_s": 0.0,
        "sleeper": DormirFalso(),
    }
    opciones.update(extra)
    return WorkersAIProvider(**opciones)  # type: ignore[arg-type]


def peticion(**extra: object) -> CompletionRequest:
    base: dict[str, object] = {
        "model": MODELO,
        "messages": [ChatMessage(role="user", content="hola")],
        "max_tokens": 256,
    }
    base.update(extra)
    return CompletionRequest(**base)  # type: ignore[arg-type]


def cuerpo_ok(
    *,
    contenido: str = "Estoy respondiendo. Soy Kimi.",
    razonamiento: str = "El usuario pide dos frases.",
    tool_calls: list[dict] | None = None,
    usage: dict | None = None,
) -> dict:
    mensaje: dict = {"role": "assistant", "content": contenido}
    if razonamiento:
        mensaje["reasoning_content"] = razonamiento
    if tool_calls is not None:
        mensaje["tool_calls"] = tool_calls
    return {
        "choices": [
            {
                "index": 0,
                "message": mensaje,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
        "usage": usage
        if usage is not None
        else {
            "prompt_tokens": 1200,
            "completion_tokens": 65,
            "total_tokens": 1265,
            "prompt_tokens_details": {"cached_tokens": 1024},
            "completion_tokens_details": {"reasoning_tokens": 57},
            "neurons": 431.5,
        },
    }


# --------------------------------------------------------------------------- #
# Camino feliz y extracción de señales
# --------------------------------------------------------------------------- #


@respx.mock
async def test_complete_exito_extrae_todas_las_senales() -> None:
    ruta = respx.post(URL).mock(return_value=httpx.Response(200, json=cuerpo_ok()))
    proveedor = hacer_proveedor()

    salida = await proveedor.complete(peticion())

    assert ruta.call_count == 1
    assert salida.text == "Estoy respondiendo. Soy Kimi."
    # El razonamiento NUNCA se mezcla con el contenido: son dos presupuestos.
    assert salida.reasoning_content == "El usuario pide dos frases."
    assert salida.reasoning_content not in salida.text
    assert salida.usage.input_tokens == 1200
    assert salida.usage.output_tokens == 65
    assert salida.reasoning_tokens == 57
    assert salida.neurons == pytest.approx(431.5)
    assert salida.intentos == 1
    assert salida.stop_reason == "end"
    assert salida.raw_usage["total_tokens"] == 1265

    enviado = json.loads(ruta.calls[0].request.content)
    assert enviado["messages"] == [{"role": "user", "content": "hola"}]
    assert enviado["stream"] is False
    assert ruta.calls[0].request.headers["authorization"] == f"Bearer {TOKEN}"


@respx.mock
async def test_cached_tokens_se_parsea_y_su_ausencia_es_none() -> None:
    """`cached_tokens` es la señal de cache de prefijo: 0 y "no reportado" difieren."""
    respx.post(URL).mock(
        side_effect=[
            httpx.Response(200, json=cuerpo_ok()),
            httpx.Response(
                200,
                json=cuerpo_ok(
                    usage={
                        "prompt_tokens": 1200,
                        "completion_tokens": 65,
                        "prompt_tokens_details": {"cached_tokens": 0},
                    }
                ),
            ),
            httpx.Response(
                200, json=cuerpo_ok(usage={"prompt_tokens": 1200, "completion_tokens": 65})
            ),
        ]
    )
    proveedor = hacer_proveedor()

    assert (await proveedor.complete(peticion())).cached_tokens == 1024
    assert (await proveedor.complete(peticion())).cached_tokens == 0
    # Sin `prompt_tokens_details` no hay medición: None, no 0.
    sin_dato = await proveedor.complete(peticion())
    assert sin_dato.cached_tokens is None
    assert sin_dato.neurons is None


@respx.mock
async def test_complete_acepta_el_sobre_result_de_cloudflare() -> None:
    respx.post(URL).mock(
        return_value=httpx.Response(
            200, json={"success": True, "errors": [], "result": cuerpo_ok()}
        )
    )
    salida = await hacer_proveedor().complete(peticion())
    assert salida.text == "Estoy respondiendo. Soy Kimi."
    assert salida.cached_tokens == 1024


@respx.mock
async def test_reasoning_effort_y_herramientas_viajan_en_el_cuerpo() -> None:
    ruta = respx.post(URL).mock(return_value=httpx.Response(200, json=cuerpo_ok()))
    herramienta = ToolSpec(
        name="apply_patch",
        description="Aplica un parche",
        input_schema={"type": "object", "properties": {"diff": {"type": "string"}}},
    )

    await hacer_proveedor().complete(
        peticion(tools=[herramienta], metadata={"reasoning_effort": "low"})
    )

    enviado = json.loads(ruta.calls[0].request.content)
    assert enviado["reasoning_effort"] == "low"
    assert enviado["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "apply_patch",
                "description": "Aplica un parche",
                "parameters": {"type": "object", "properties": {"diff": {"type": "string"}}},
            },
        }
    ]


# --------------------------------------------------------------------------- #
# Traducción de herramientas y tool_calls
# --------------------------------------------------------------------------- #


def test_herramienta_ida_y_vuelta() -> None:
    spec = ToolSpec(
        name="write_file",
        description="Escribe un archivo",
        input_schema={"type": "object", "properties": {"contenido": {"type": "string"}}},
    )
    assert herramienta_desde_workers_ai(herramienta_a_workers_ai(spec)) == spec


def test_tool_call_con_bloque_de_codigo_multilinea() -> None:
    """El caso duro de `ArgProfile.CODE_BLOB`: código dentro de un string JSON."""
    llamada = ToolCall(
        id="call_1", name="write_file", arguments={"ruta": "a.py", "contenido": CODIGO_MULTILINEA}
    )
    cable = tool_call_a_workers_ai(llamada)

    # `arguments` es un STRING, no un objeto: es lo que exige la API.
    assert isinstance(cable["function"]["arguments"], str)
    assert "\n" not in cable["function"]["arguments"]  # los saltos van escapados
    assert "ñ" in cable["function"]["arguments"]  # sin ensure_ascii: no se hincha a \uXXXX

    vuelta = tool_call_desde_workers_ai(cable)
    assert vuelta == llamada
    assert vuelta.arguments["contenido"] == CODIGO_MULTILINEA


@respx.mock
async def test_complete_traduce_tool_calls_con_codigo_y_guarda_el_crudo() -> None:
    crudo = {
        "id": "call_abc",
        "type": "function",
        "function": {
            "name": "write_file",
            "arguments": json.dumps(
                {"ruta": "apps/x.py", "contenido": CODIGO_MULTILINEA}, ensure_ascii=False
            ),
        },
    }
    respx.post(URL).mock(
        return_value=httpx.Response(200, json=cuerpo_ok(contenido="", tool_calls=[crudo]))
    )

    salida = await hacer_proveedor().complete(peticion())

    assert salida.stop_reason == "tool_use"
    assert len(salida.tool_calls) == 1
    assert salida.tool_calls[0].name == "write_file"
    assert salida.tool_calls[0].arguments["contenido"] == CODIGO_MULTILINEA
    # El crudo se conserva para distinguir "no llamó" de "llamó con JSON roto".
    assert salida.tool_calls_crudos == [crudo]


@respx.mock
async def test_tool_call_con_json_invalido_deja_rastro() -> None:
    crudo = {
        "id": "call_roto",
        "function": {"name": "write_file", "arguments": '{"contenido": "def f(:\n  pass"'},
    }
    respx.post(URL).mock(
        return_value=httpx.Response(200, json=cuerpo_ok(contenido="", tool_calls=[crudo]))
    )

    salida = await hacer_proveedor().complete(peticion())

    assert salida.tool_calls[0].arguments == {}
    assert salida.tool_calls_crudos[0]["function"]["arguments"].startswith('{"contenido"')


def test_mensajes_traducen_bloques_anthropic_a_estilo_openai() -> None:
    req = CompletionRequest(
        model=MODELO,
        system="sistema",
        messages=[
            ChatMessage(role="user", content=[{"type": "text", "text": "arregla esto"}]),
            ChatMessage(
                role="assistant",
                content=[
                    {"type": "text", "text": "voy"},
                    {"type": "tool_use", "id": "t1", "name": "leer", "input": {"ruta": "a.py"}},
                    {"type": "tool_use", "id": "t2", "name": "leer", "input": {"ruta": "b.py"}},
                ],
            ),
            ChatMessage(
                role="tool",
                content=[
                    {"type": "tool_result", "tool_use_id": "t1", "content": "contenido a"},
                    {"type": "tool_result", "tool_use_id": "t2", "content": "contenido b"},
                ],
            ),
        ],
    )

    mensajes = mensajes_a_workers_ai(req)

    assert mensajes[0] == {"role": "system", "content": "sistema"}
    assert mensajes[1] == {"role": "user", "content": [{"type": "text", "text": "arregla esto"}]}
    assert mensajes[2]["role"] == "assistant"
    assert [tc["id"] for tc in mensajes[2]["tool_calls"]] == ["t1", "t2"]
    assert json.loads(mensajes[2]["tool_calls"][0]["function"]["arguments"]) == {"ruta": "a.py"}
    # Cada tool_result es su propio mensaje: leer solo el primero perdería el resto.
    assert mensajes[3] == {"role": "tool", "tool_call_id": "t1", "content": "contenido a"}
    assert mensajes[4] == {"role": "tool", "tool_call_id": "t2", "content": "contenido b"}


# --------------------------------------------------------------------------- #
# Errores, reintentos y presupuesto
# --------------------------------------------------------------------------- #


@respx.mock
async def test_429_reintenta_y_acaba_bien() -> None:
    ruta = respx.post(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "0"}, json={"errors": [{"code": 10000}]}),
            httpx.Response(200, json=cuerpo_ok()),
        ]
    )
    dormir = DormirFalso()
    proveedor = hacer_proveedor(sleeper=dormir)

    salida = await proveedor.complete(peticion())

    assert ruta.call_count == 2
    assert salida.intentos == 2
    assert len(dormir.esperas) == 1


@respx.mock
async def test_429_persistente_agota_los_cuatro_intentos_y_expone_retry_after() -> None:
    ruta = respx.post(URL).mock(
        return_value=httpx.Response(429, headers={"retry-after": "7"}, json={"errors": []})
    )
    dormir = DormirFalso()
    proveedor = hacer_proveedor(sleeper=dormir, deadline_s=10_000.0)

    with pytest.raises(LimiteDeTasaError) as info:
        await proveedor.complete(peticion())

    assert ruta.call_count == 4
    assert info.value.intentos == 4
    assert info.value.retry_after == 7.0
    # `Retry-After` actúa como suelo del backoff, aunque el backoff base sea 0.
    assert dormir.esperas == [7.0, 7.0, 7.0]


@respx.mock
async def test_5xx_es_transitorio_y_se_reintenta() -> None:
    ruta = respx.post(URL).mock(return_value=httpx.Response(503, text="upstream caído"))
    proveedor = hacer_proveedor()

    with pytest.raises(FalloTransitorioError) as info:
        await proveedor.complete(peticion())

    assert ruta.call_count == 4
    assert info.value.status_code == 503


@respx.mock
async def test_401_no_se_reintenta() -> None:
    ruta = respx.post(URL).mock(
        return_value=httpx.Response(401, json={"success": False, "errors": [{"code": 10000}]})
    )

    with pytest.raises(CredencialInvalidaError):
        await hacer_proveedor().complete(peticion())

    assert ruta.call_count == 1


@respx.mock
async def test_403_5018_dice_que_el_modelo_no_esta_en_la_cuenta() -> None:
    """Es el caso real de Kimi K3 en esta cuenta: el `code` tiene que sobrevivir."""
    url_k3 = f"https://api.cloudflare.com/client/v4/accounts/{CUENTA}/ai/run/@cf/moonshotai/kimi-k3"
    ruta = respx.post(url_k3).mock(
        return_value=httpx.Response(
            403, json={"success": False, "errors": [{"code": 5018, "message": "no autorizado"}]}
        )
    )

    with pytest.raises(CredencialInvalidaError) as info:
        await hacer_proveedor(model="@cf/moonshotai/kimi-k3").complete(peticion())

    assert ruta.call_count == 1
    assert "5018" in str(info.value)


@respx.mock
async def test_400_es_peticion_invalida_y_no_se_reintenta() -> None:
    ruta = respx.post(URL).mock(return_value=httpx.Response(400, text="max_tokens inválido"))

    with pytest.raises(PeticionInvalidaError):
        await hacer_proveedor().complete(peticion())

    assert ruta.call_count == 1


@respx.mock
async def test_200_con_success_false_no_cuenta_como_exito() -> None:
    respx.post(URL).mock(
        return_value=httpx.Response(
            200, json={"success": False, "errors": [{"code": 7000}], "result": None}
        )
    )
    with pytest.raises(PeticionInvalidaError):
        await hacer_proveedor().complete(peticion())


@respx.mock
async def test_timeout_no_se_reintenta_y_es_su_propio_error() -> None:
    ruta = respx.post(URL).mock(side_effect=httpx.ReadTimeout("se acabó el tiempo"))

    with pytest.raises(TiempoAgotadoError):
        await hacer_proveedor().complete(peticion())

    assert ruta.call_count == 1


@respx.mock
async def test_error_de_conexion_es_proveedor_inalcanzable() -> None:
    respx.post(URL).mock(side_effect=httpx.ConnectError("sin ruta al host"))

    with pytest.raises(ProveedorInalcanzableError):
        await hacer_proveedor().complete(peticion())


async def test_deadline_absoluto_corta_antes_de_salir_a_la_red() -> None:
    proveedor = hacer_proveedor()
    with pytest.raises(PresupuestoAgotadoError):
        await proveedor.complete(peticion(metadata={"deadline_s": 0.0}))


@respx.mock
async def test_el_reintento_que_no_cabe_en_el_deadline_no_se_duerme() -> None:
    respx.post(URL).mock(return_value=httpx.Response(429, headers={"retry-after": "600"}))
    dormir = DormirFalso()
    proveedor = hacer_proveedor(sleeper=dormir, deadline_s=5.0)

    with pytest.raises(PresupuestoAgotadoError):
        await proveedor.complete(peticion())

    assert dormir.esperas == []


async def test_sin_credenciales_falla_sin_tocar_la_red() -> None:
    proveedor = WorkersAIProvider(account_id="", api_token="", env_file=None)
    with pytest.raises(CredencialInvalidaError) as info:
        await proveedor.complete(peticion())
    assert "CLOUDFLARE_API_TOKEN" in str(info.value)


@respx.mock
async def test_el_token_nunca_aparece_en_un_mensaje_de_error() -> None:
    respx.post(URL).mock(return_value=httpx.Response(400, text=f"token rechazado: {TOKEN}"))

    with pytest.raises(PeticionInvalidaError) as info:
        await hacer_proveedor().complete(peticion())

    assert TOKEN not in str(info.value)
    assert "***" in str(info.value)


def test_repr_no_filtra_el_token() -> None:
    assert TOKEN not in repr(hacer_proveedor())


# --------------------------------------------------------------------------- #
# Streaming
# --------------------------------------------------------------------------- #


def sse(*eventos: dict | str) -> bytes:
    lineas = [
        f"data: {e if isinstance(e, str) else json.dumps(e, ensure_ascii=False)}\n\n"
        for e in eventos
    ]
    return "".join(lineas).encode("utf-8")


@respx.mock
async def test_stream_separa_razonamiento_contenido_uso_y_tool_calls() -> None:
    argumentos = json.dumps({"contenido": CODIGO_MULTILINEA}, ensure_ascii=False)
    mitad = len(argumentos) // 2
    respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=sse(
                {"choices": [{"delta": {"reasoning_content": "pensando..."}}]},
                {"choices": [{"delta": {"content": "Hola"}}]},
                {"choices": [{"delta": {"content": " mundo"}}]},
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_z",
                                        "function": {
                                            "name": "write_file",
                                            "arguments": argumentos[:mitad],
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {"index": 0, "function": {"arguments": argumentos[mitad:]}}
                                ]
                            }
                        }
                    ]
                },
                {
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 900,
                        "completion_tokens": 120,
                        "prompt_tokens_details": {"cached_tokens": 768},
                        "completion_tokens_details": {"reasoning_tokens": 80},
                        "neurons": 12.5,
                    },
                },
                "[DONE]",
            ),
        )
    )
    ruta = respx.calls

    trozos = [t async for t in hacer_proveedor().stream(peticion())]

    # Un consumidor ingenuo concatena `text` y NO se come el razonamiento.
    assert "".join(t.text or "" for t in trozos) == "Hola mundo"
    assert [t.reasoning_text for t in trozos if t.reasoning_text] == ["pensando..."]

    uso = next(t for t in trozos if t.type == "usage")
    assert uso.usage is not None and uso.usage.output_tokens == 120
    assert uso.cached_tokens == 768
    assert uso.reasoning_tokens == 80
    assert uso.neurons == pytest.approx(12.5)

    llamada = next(t for t in trozos if t.type == "tool_call")
    assert llamada.tool_call is not None
    assert llamada.tool_call.id == "call_z"
    # Solo la concatenación de los deltas es JSON válido.
    assert llamada.tool_call.arguments["contenido"] == CODIGO_MULTILINEA

    assert trozos[-1].type == "stop"
    enviado = json.loads(ruta[0].request.content)
    assert enviado["stream"] is True
    assert enviado["stream_options"] == {"include_usage": True}


@respx.mock
async def test_stream_reintenta_un_429_antes_del_primer_trozo() -> None:
    ruta = respx.post(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "0"}, text="{}"),
            httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=sse({"choices": [{"delta": {"content": "ok"}}]}, "[DONE]"),
            ),
        ]
    )
    dormir = DormirFalso()

    trozos = [t async for t in hacer_proveedor(sleeper=dormir).stream(peticion())]

    assert ruta.call_count == 2
    assert len(dormir.esperas) == 1
    assert "".join(t.text or "" for t in trozos) == "ok"


@respx.mock
async def test_stream_401_no_se_reintenta() -> None:
    ruta = respx.post(URL).mock(return_value=httpx.Response(401, text="no autorizado"))

    with pytest.raises(CredencialInvalidaError):
        _ = [t async for t in hacer_proveedor().stream(peticion())]

    assert ruta.call_count == 1


# --------------------------------------------------------------------------- #
# smoke()
# --------------------------------------------------------------------------- #


@respx.mock
async def test_smoke_ok() -> None:
    ruta = respx.post(URL).mock(return_value=httpx.Response(200, json=cuerpo_ok()))

    resultado = await hacer_proveedor().smoke()

    assert resultado.ok is True
    assert resultado.modelo == MODELO
    assert resultado.texto == "Estoy respondiendo. Soy Kimi."
    assert resultado.cached_tokens == 1024
    assert resultado.latencia_s >= 0.0
    enviado = json.loads(ruta.calls[0].request.content)
    # Presupuesto holgado a propósito: el razonamiento come de `max_tokens` y
    # con 32 el `content` vuelve vacío (medido contra la API real).
    assert enviado["max_tokens"] >= 128


@respx.mock
async def test_smoke_informa_del_fallo_en_vez_de_estallar() -> None:
    respx.post(URL).mock(return_value=httpx.Response(401, text=f"token malo {TOKEN}"))

    resultado = await hacer_proveedor().smoke()

    assert resultado.ok is False
    assert resultado.error is not None
    assert TOKEN not in resultado.error


@respx.mock
async def test_smoke_marca_ok_false_si_el_contenido_llego_vacio() -> None:
    """El modo de fallo real: `max_tokens` corto, razonamiento se lo come todo."""
    respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            json=cuerpo_ok(
                contenido="",
                razonamiento="pensando mucho",
                usage={
                    "prompt_tokens": 30,
                    "completion_tokens": 32,
                    "completion_tokens_details": {"reasoning_tokens": 32},
                },
            ),
        )
    )

    resultado = await hacer_proveedor().smoke()

    assert resultado.ok is False
    assert resultado.completion_tokens == 32


# --------------------------------------------------------------------------- #
# .env
# --------------------------------------------------------------------------- #


def test_cargar_dotenv_no_toca_os_environ(tmp_path) -> None:
    archivo = tmp_path / ".env"
    archivo.write_text(
        "# comentario\n"
        "export CLOUDFLARE_ACCOUNT_ID=abc123\n"
        'CLOUDFLARE_API_TOKEN="entre-comillas"\n'
        "FORGE_PROBE_MODEL='@cf/moonshotai/kimi-k3'\n"
        "\n"
        "linea sin igual\n",
        encoding="utf-8",
    )

    valores = cargar_dotenv(archivo)

    assert valores["CLOUDFLARE_ACCOUNT_ID"] == "abc123"
    assert valores["CLOUDFLARE_API_TOKEN"] == "entre-comillas"
    assert valores["FORGE_PROBE_MODEL"] == "@cf/moonshotai/kimi-k3"
    assert (
        "CLOUDFLARE_ACCOUNT_ID" not in os.environ or os.environ["CLOUDFLARE_ACCOUNT_ID"] != "abc123"
    )


def test_cargar_dotenv_con_archivo_inexistente(tmp_path) -> None:
    assert cargar_dotenv(tmp_path / "no-existe.env") == {}


def test_el_modelo_sale_del_entorno_para_poder_saltar_a_k3(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "de-entorno")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
    monkeypatch.setenv("FORGE_PROBE_MODEL", "@cf/moonshotai/kimi-k3")

    proveedor = WorkersAIProvider(env_file=tmp_path / "no-existe.env")

    assert proveedor.model == "@cf/moonshotai/kimi-k3"
    assert proveedor.account_id == "de-entorno"


def test_modelo_por_defecto_es_el_unico_con_acceso_confirmado(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("FORGE_PROBE_MODEL", raising=False)
    proveedor = WorkersAIProvider(
        account_id="a", api_token="t", env_file=tmp_path / "no-existe.env"
    )
    assert proveedor.model == "@cf/moonshotai/kimi-k2.7-code"


# --------------------------------------------------------------------------- #
# Ollama
# --------------------------------------------------------------------------- #

URL_OLLAMA = "http://localhost:11434/api/chat"


@respx.mock
async def test_ollama_probe_adapter_completa_y_no_inventa_senales(tmp_path) -> None:
    ruta = respx.post(URL_OLLAMA).mock(
        return_value=httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": "vivo",
                    "tool_calls": [{"function": {"name": "leer", "arguments": {"ruta": "a.py"}}}],
                },
                "prompt_eval_count": 40,
                "eval_count": 9,
                "done": True,
            },
        )
    )
    adaptador = OllamaProbeAdapter(model="qwen3:8b", env_file=tmp_path / "no-existe.env")

    salida = await adaptador.complete(peticion(model=""))

    assert ruta.call_count == 1
    assert json.loads(ruta.calls[0].request.content)["model"] == "qwen3:8b"
    assert salida.text == "vivo"
    assert salida.usage.input_tokens == 40
    assert salida.usage.output_tokens == 9
    # Ollama no reporta cache de prefijo, razonamiento separado ni neuronas:
    # None significa "no medido", nunca 0.
    assert salida.cached_tokens is None
    assert salida.reasoning_tokens is None
    assert salida.neurons is None
    assert salida.reasoning_content == ""
    assert salida.tool_calls[0].arguments == {"ruta": "a.py"}
    await adaptador.aclose()


@respx.mock
async def test_ollama_probe_adapter_streaming(tmp_path) -> None:
    respx.post(URL_OLLAMA).mock(
        return_value=httpx.Response(
            200,
            content=(
                json.dumps({"message": {"content": "ho"}, "done": False})
                + "\n"
                + json.dumps({"message": {"content": "la"}, "done": False})
                + "\n"
                + json.dumps({"message": {"content": ""}, "done": True, "eval_count": 2})
                + "\n"
            ).encode("utf-8"),
        )
    )
    adaptador = OllamaProbeAdapter(model="qwen3:8b", env_file=tmp_path / "no-existe.env")

    trozos = [t async for t in adaptador.stream(peticion(model=""))]

    assert "".join(t.text or "" for t in trozos) == "hola"
    assert trozos[-1].type == "stop"
    assert all(t.cached_tokens is None for t in trozos)
    await adaptador.aclose()


@respx.mock
async def test_ollama_probe_adapter_smoke(tmp_path) -> None:
    respx.post(URL_OLLAMA).mock(
        return_value=httpx.Response(
            200,
            json={
                "message": {"content": "Vivo. Soy qwen3."},
                "prompt_eval_count": 10,
                "eval_count": 6,
                "done": True,
            },
        )
    )
    adaptador = OllamaProbeAdapter(model="qwen3:8b", env_file=tmp_path / "no-existe.env")

    resultado = await adaptador.smoke()

    assert resultado.ok is True
    assert resultado.proveedor == "ollama"
    assert resultado.texto == "Vivo. Soy qwen3."
    assert resultado.cached_tokens is None
    await adaptador.aclose()


# --------------------------------------------------------------------------- #
# Integración real (gasta dinero: apagada por defecto)
# --------------------------------------------------------------------------- #


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("FORGE_PROBE_INTEGRACION") != "1",
    reason="Sale a la red y cuesta dinero real. Poner FORGE_PROBE_INTEGRACION=1 para correrlo.",
)
async def test_smoke_contra_la_api_real() -> None:
    proveedor = WorkersAIProvider()
    try:
        resultado = await proveedor.smoke()
    finally:
        await proveedor.aclose()
    assert resultado.ok, resultado.error
    assert resultado.latencia_s > 0.0
