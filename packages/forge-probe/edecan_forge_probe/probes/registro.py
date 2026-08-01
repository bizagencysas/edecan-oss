"""Registro de sondas: el adaptador entre las sondas y el runner.

Las tres sondas de la fase 0 —contexto, tool-calling y rendimiento— se
escribieron en paralelo y cada una declaró su propio protocolo de proveedor
(`ProveedorContexto`, `ProveedorHerramientas`, `ProveedorPerf`) y su propia
contabilidad de gasto (`Contador`, `Precios`, `Presupuesto`). Este módulo es el
único sitio del paquete que conoce los tres a la vez y los presenta al runner
con la forma que sabe orquestar: una tupla `SONDAS: tuple[Sonda, ...]`.

Dos decisiones que importan:

1. **Una sola contabilidad.** Todo el tráfico pasa por `_ProveedorContabilizado`,
   que envuelve el `LLMProvider` y apunta cada llamada en la `Contabilidad` del
   runner vía `ContextoSonda.registrar_uso`. Así `--presupuesto-usd` es un tope
   GLOBAL de verdad, y no tres topes locales que se ignoran entre sí. Los
   `max_usd` internos de cada sonda quedan puestos en infinito a propósito: si
   dos capas cortan por dinero, el informe no puede decir cuál cortó.

2. **Una `Sonda` por serie.** `SondaToolCalling.ejecutar()` devuelve una lista y
   `Sonda.ejecutar` devuelve uno solo. En vez de tocar el contrato o de aplastar
   seis mediciones en un `ProbeResult` con un `detalle` gigante, se construye una
   `SondaToolCalling` por serie (un perfil, o sólo el barrido de herramientas, o
   sólo el de esquema). El efecto secundario es el bueno: cada serie se persiste
   y se reanuda por separado, así que un corte por presupuesto en `long_string`
   no obliga a volver a pagar `scalar`.

El proveedor se elige por `OpcionesRunner.proveedor`: `workers-ai` (el objetivo)
u `ollama` (el arnés local, sin coste, para demostrar que el andamiaje funciona
de punta a punta sin gastar un céntimo).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

from edecan_llm.base import (
    ChatMessage,
    CompletionRequest,
    CompletionResponse,
    LLMProvider,
    StreamChunk,
    ToolSpec,
)

from ..modelcard import ArgProfile, Capability, ProbeResult
from ..runner import ContextoSonda, Sonda
from . import context as sonda_context
from . import perf as sonda_perf
from . import tools as sonda_tools

__all__ = [
    "SONDAS",
    "AdaptadorHerramientas",
    "construir_proveedor",
    "sondas_de_la_fase_0",
]

# Sin tope interno: el único que corta es el del runner (ver docstring).
_SIN_TOPE_USD = float("inf")

_MINIMO_TOOLS = max(20, sonda_tools.n_minimo_para(sonda_tools.UMBRAL_SELECCION))
"""Muestras mínimas que admite una serie de tool-calling.

Se CALCULA, no se escribe a mano, porque es aritmética del umbral y no una
preferencia: con N intentos el límite inferior de Wilson tiene un techo, y por
debajo de ese N el umbral sería inalcanzable incluso con un modelo perfecto —el
`FALLA` resultante sería del intervalo de confianza, no del modelo. Hoy son 35
para el umbral 0,90; si el umbral cambia, este número lo sigue solo."""


# --------------------------------------------------------------------------- #
# Contabilidad unificada
# --------------------------------------------------------------------------- #


class _ProveedorContabilizado(LLMProvider):
    """Envuelve un `LLMProvider` y apunta cada llamada en la contabilidad global.

    Es el punto donde convergen las tres sondas: `AdaptadorLLMProvider`
    (contexto), `PuenteLLMProvider` (rendimiento) y `AdaptadorHerramientas`
    (tool-calling) hablan todos con un `LLMProvider`, así que envolver éste basta
    para que el gasto de las tres caiga en el mismo sitio.

    `registrar_uso` lanza `PresupuestoAgotado` DESPUÉS de anotar: el token ya se
    gastó y la factura tiene que reflejarlo aunque la sonda muera acto seguido.
    """

    name = "contabilizado"

    def __init__(self, interno: LLMProvider, ctx: ContextoSonda) -> None:
        self._interno = interno
        self._ctx = ctx
        self.model = str(getattr(interno, "model", "") or ctx.modelo)
        self.name = str(getattr(interno, "name", "contabilizado"))

    def __getattr__(self, nombre: str) -> Any:
        # Las sondas leen banderas opcionales del proveedor (`soporta_imagenes`,
        # `acepta_reasoning_effort`, …) con getattr. Delegar aquí evita que el
        # envoltorio las esconda y cambie el comportamiento medido.
        return getattr(self._interno, nombre)

    async def aclose(self) -> None:
        cerrar = getattr(self._interno, "aclose", None)
        if cerrar is not None:
            await cerrar()

    def _anotar(self, fuente: Any) -> None:
        uso = getattr(fuente, "usage", None)
        self._ctx.registrar_uso(
            entrada=int(getattr(uso, "input_tokens", 0) or 0),
            salida=int(getattr(uso, "output_tokens", 0) or 0),
            cacheados=int(getattr(fuente, "cached_tokens", None) or 0),
            razonamiento=int(getattr(fuente, "reasoning_tokens", None) or 0),
            neurons=float(getattr(fuente, "neurons", None) or 0.0),
        )

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        respuesta = await self._interno.complete(req)
        self._anotar(respuesta)
        return respuesta

    async def stream(self, req: CompletionRequest) -> AsyncIterator[StreamChunk]:
        async for trozo in self._interno.stream(req):
            if getattr(trozo, "usage", None) is not None:
                self._anotar(trozo)
            yield trozo


# --------------------------------------------------------------------------- #
# Adaptador de tool-calling
# --------------------------------------------------------------------------- #


class AdaptadorHerramientas:
    """Traduce un `LLMProvider` al `ProveedorHerramientas` de `probes/tools.py`.

    Es la pieza que faltaba para poder correr la sonda de tool-calling contra un
    proveedor real. Lo delicado es UNA cosa: `argumentos_json` tiene que ser el
    string **crudo** que emitió el modelo. Si el adaptador lo reserializara desde
    el dict ya parseado, `ModoFallo.json_invalido` dejaría de ser observable y la
    sonda estaría midiendo el parser del adaptador en vez de al modelo.

    Por eso se prefiere `tool_calls_crudos` (que `ProbeCompletionResponse` expone
    con `function.arguments` todavía como string) y sólo se cae a reserializar
    `tool_calls` cuando el proveedor no publica los crudos — caso en el que se
    deja constancia en `error` de que el JSON inválido es indetectable.
    """

    def __init__(self, proveedor: Any, *, modelo: str | None = None) -> None:
        self._proveedor = proveedor
        self.modelo = modelo or str(getattr(proveedor, "model", "") or "desconocido")

    @staticmethod
    def _crudas(respuesta: Any) -> list[sonda_tools.LlamadaCruda]:
        crudos = getattr(respuesta, "tool_calls_crudos", None)
        if crudos:
            salida: list[sonda_tools.LlamadaCruda] = []
            for llamada in crudos:
                funcion = llamada.get("function") if isinstance(llamada, dict) else None
                if not isinstance(funcion, dict):
                    continue
                argumentos = funcion.get("arguments")
                salida.append(
                    sonda_tools.LlamadaCruda(
                        nombre=str(funcion.get("name") or ""),
                        argumentos_json=(
                            argumentos
                            if isinstance(argumentos, str)
                            else json.dumps(argumentos, ensure_ascii=False)
                        ),
                    )
                )
            return salida
        return [
            sonda_tools.LlamadaCruda(
                nombre=tc.name,
                argumentos_json=json.dumps(tc.arguments, ensure_ascii=False),
            )
            for tc in getattr(respuesta, "tool_calls", []) or []
        ]

    async def invocar(
        self,
        *,
        sistema: str,
        prompt: str,
        herramientas: Sequence[ToolSpec],
        max_tokens: int,
    ) -> sonda_tools.RespuestaSonda:
        peticion = CompletionRequest(
            model=self.modelo,
            system=sistema,
            messages=[ChatMessage(role="user", content=prompt)],
            tools=list(herramientas),
            max_tokens=max_tokens,
            temperature=0.0,
        )
        try:
            respuesta = await self._proveedor.complete(peticion)
        except sonda_tools.PresupuestoAgotado:
            raise
        except Exception as exc:  # noqa: BLE001 - un fallo de transporte NO es del modelo
            if type(exc).__name__ == "PresupuestoAgotado":
                raise
            return sonda_tools.RespuestaSonda(
                error=sonda_perf.sanear(f"{type(exc).__name__}: {exc}")
            )

        usage = getattr(respuesta, "usage", None)
        sin_crudos = not getattr(respuesta, "tool_calls_crudos", None) and bool(
            getattr(respuesta, "tool_calls", None)
        )
        return sonda_tools.RespuestaSonda(
            llamadas=self._crudas(respuesta),
            contenido=str(getattr(respuesta, "text", "") or ""),
            razonamiento=str(getattr(respuesta, "reasoning_content", "") or ""),
            tokens_entrada=int(getattr(usage, "input_tokens", 0) or 0),
            tokens_salida=int(getattr(usage, "output_tokens", 0) or 0),
            tokens_cacheados=int(getattr(respuesta, "cached_tokens", None) or 0),
            tokens_razonamiento=int(getattr(respuesta, "reasoning_tokens", None) or 0),
            neuronas=getattr(respuesta, "neurons", None),
            error=(
                "el proveedor no publica `tool_calls_crudos`: el JSON inválido "
                "es indetectable en esta pasada"
                if sin_crudos
                else None
            ),
        )


# --------------------------------------------------------------------------- #
# Construcción del proveedor
# --------------------------------------------------------------------------- #


def construir_proveedor(ctx: ContextoSonda) -> LLMProvider:
    """Crea el `LLMProvider` de la ejecución, ya contabilizado.

    Se importa `providers` aquí dentro y no arriba a propósito: `descubrir_sondas`
    importa este módulo para leer `SONDAS`, y ese import no puede depender de que
    haya credenciales ni de que `httpx` pueda abrir un cliente.
    """
    from .. import providers

    explicito = ctx.opciones.get("proveedor_llm")
    if explicito is not None:
        return _ProveedorContabilizado(explicito, ctx)

    match ctx.proveedor:
        case "ollama":
            interno: LLMProvider = providers.OllamaProbeAdapter(model=ctx.modelo or None)
        case "workers-ai":
            interno = providers.WorkersAIProvider(model=ctx.modelo or None)
        case otro:
            raise ValueError(
                f"proveedor desconocido: {otro!r}. La fase 0 conoce 'workers-ai' y 'ollama'."
            )
    return _ProveedorContabilizado(interno, ctx)


def _n(ctx: ContextoSonda, defecto: int) -> int:
    """Número de muestras pedido por el operador (`--n`), o el del encargo."""
    crudo = ctx.opciones.get("n")
    if crudo is None:
        return defecto
    try:
        return max(1, int(crudo))
    except (TypeError, ValueError):
        return defecto


def _evidencia(ctx: ContextoSonda) -> Path:
    destino = ctx.directorio_evidencia / "trazas" / ctx.sonda
    destino.mkdir(parents=True, exist_ok=True)
    return destino


async def _cerrar(proveedor: Any) -> None:
    """Cierra el proveedor si llegó a construirse.

    `None` es un caso real: si `construir_proveedor` falla, el `finally` corre
    igual y no debe convertir un error de configuración legible en un
    `AttributeError` sin relación.
    """
    if proveedor is None:
        return
    cerrar = getattr(proveedor, "aclose", None)
    if cerrar is not None:
        await cerrar()


# --------------------------------------------------------------------------- #
# Sondas
# --------------------------------------------------------------------------- #


def _fallo(ctx: ContextoSonda, exc: BaseException) -> ProbeResult:
    """Un fallo de arranque sale como `ok=False`, nunca como medición mala."""
    return ProbeResult(
        probe=ctx.sonda,
        ok=False,
        error=sonda_perf.sanear(f"{type(exc).__name__}: {exc}"),
    )


async def _ejecutar_contexto(ctx: ContextoSonda) -> ProbeResult:
    """Contexto ÚTIL: hasta dónde recuerda de verdad, no lo que anuncia el catálogo."""
    proveedor: Any = None
    try:
        proveedor = construir_proveedor(ctx)
        profundidades = ctx.opciones.get("profundidades") or sonda_context.PROFUNDIDADES_FASE_0
        return await sonda_context.sondar_contexto(
            sonda_context.AdaptadorLLMProvider(proveedor, modelo=ctx.modelo),
            profundidades=tuple(profundidades),
            intentos=_n(ctx, sonda_context.INTENTOS_POR_CONFIG),
            max_usd=_SIN_TOPE_USD,
            dir_evidencia=ctx.directorio_evidencia / "contexto",
        )
    except Exception as exc:  # noqa: BLE001 - se reporta, no se traga
        return _fallo(ctx, exc)
    finally:
        await _cerrar(proveedor)


def _sonda_tools(nombre: str, **kwargs: Any) -> Sonda:
    """Fabrica una `Sonda` que corre UNA serie de tool-calling.

    `SondaToolCalling.ejecutar()` devuelve una lista; con una sola serie
    configurada esa lista tiene exactamente un elemento, que es lo que el runner
    espera. Si alguna vez trajera más de uno sería un cambio de comportamiento de
    la sonda y hay que verlo, así que se comprueba en vez de coger `[0]`.
    """

    async def ejecutar(ctx: ContextoSonda) -> ProbeResult:
        n = _n(ctx, 40)
        if n < _MINIMO_TOOLS:
            return ProbeResult(
                probe=ctx.sonda,
                capability=Capability.NATIVE_TOOLS,
                ok=False,
                error=(
                    f"--n {n} está por debajo del mínimo de {_MINIMO_TOOLS} intentos que "
                    f"exige la sonda de tool-calling: con {n} muestras el límite inferior "
                    f"de Wilson no pasa de {sonda_tools.techo_lower_95(n):.3f}, así que el "
                    f"umbral {sonda_tools.UMBRAL_SELECCION:.2f} sería inalcanzable incluso "
                    "con un modelo perfecto y el veredicto sería aritmética, no medición"
                ),
            )
        proveedor: Any = None
        try:
            proveedor = construir_proveedor(ctx)
            sonda = sonda_tools.SondaToolCalling(
                AdaptadorHerramientas(proveedor, modelo=ctx.modelo),
                dir_evidencia=_evidencia(ctx),
                max_usd=_SIN_TOPE_USD,
                intentos_por_perfil=n,
                intentos_por_escalon=n,
                **kwargs,
            )
            resultados = await sonda.ejecutar()
        except Exception as exc:  # noqa: BLE001 - se reporta, no se traga
            return _fallo(ctx, exc)
        finally:
            await _cerrar(proveedor)
        if len(resultados) != 1:
            return ProbeResult(
                probe=ctx.sonda,
                ok=False,
                error=(
                    f"la serie devolvió {len(resultados)} resultados y se esperaba 1: "
                    "la configuración de `SondaToolCalling` cambió de significado"
                ),
            )
        return resultados[0]

    return Sonda(
        nombre=nombre,
        ejecutar=ejecutar,
        grupo="tools",
        capability=Capability.NATIVE_TOOLS,
    )


def _sonda_perf(nombre: str, metodo: str, capability: Capability | None = None) -> Sonda:
    """Fabrica una `Sonda` a partir de un método de `SondaRendimiento`."""

    async def ejecutar(ctx: ContextoSonda) -> ProbeResult:
        proveedor: Any = None
        try:
            proveedor = construir_proveedor(ctx)
            n = _n(ctx, 0)
            config = sonda_perf.ConfigPerf()
            if ctx.opciones.get("n") is not None:
                config = sonda_perf.ConfigPerf(
                    muestras_ttft=n,
                    muestras_throughput=n,
                    muestras_estructurado=n,
                    muestras_vision=n,
                    muestras_razonamiento=n,
                    rondas_cache=max(2, n),
                )
            sonda = sonda_perf.SondaRendimiento(
                sonda_perf.PuenteLLMProvider(proveedor, modelo=ctx.modelo),
                config=config,
                max_usd=None,
                dir_evidencia=_evidencia(ctx),
            )
            return await getattr(sonda, metodo)()
        except Exception as exc:  # noqa: BLE001 - se reporta, no se traga
            return _fallo(ctx, exc)
        finally:
            await _cerrar(proveedor)

    return Sonda(nombre=nombre, ejecutar=ejecutar, grupo="perf", capability=capability)


def sondas_de_la_fase_0() -> tuple[Sonda, ...]:
    """Las sondas de la fase 0, en orden de coste creciente.

    El orden importa: si el presupuesto se agota, se agota en el barrido de
    contexto —que es el que cuesta decenas de dólares— y no antes de haber
    medido estructura, visión y tool-calling, que cuestan céntimos y responden
    preguntas de diseño igual de grandes.
    """
    return (
        _sonda_perf("perf.structured_output", "salida_estructurada", Capability.STRUCTURED_OUTPUT),
        _sonda_perf("perf.vision", "vision", Capability.VISION),
        _sonda_perf("perf.reasoning_overhead", "sobrecarga_razonamiento", Capability.REASONING),
        _sonda_perf("perf.throughput", "throughput", Capability.STREAMING),
        _sonda_perf("perf.prefix_cache", "cache_prefijo", Capability.PREFIX_CACHE),
        _sonda_perf("perf.ttft", "ttft", Capability.STREAMING),
        *(
            _sonda_tools(
                f"native_tools.{perfil.value}",
                perfiles=(perfil,),
                escalones_herramientas=(),
                escalones_schema=(),
            )
            for perfil in ArgProfile
        ),
        _sonda_tools("native_tools.max_tools_effective", perfiles=(), escalones_schema=()),
        _sonda_tools("native_tools.max_schema_bytes", perfiles=(), escalones_herramientas=()),
        Sonda(
            nombre=sonda_context.NOMBRE_SONDA,
            ejecutar=_ejecutar_contexto,
            grupo="context",
            descripcion="Contexto útil medido por profundidad, no el anunciado.",
        ),
    )


SONDAS: tuple[Sonda, ...] = sondas_de_la_fase_0()
