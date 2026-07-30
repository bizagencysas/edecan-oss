"""Pruebas del registro de sondas: el pegamento entre las sondas y el runner.

Lo que se comprueba aquí no es que las sondas midan bien —eso lo cubren sus
propias suites— sino las tres cosas que sólo se rompen al integrarlas:

1. que el runner las DESCUBRA (antes de este módulo, `descubrir_sondas()`
   devolvía `()` y `sondear` salía con código 1);
2. que el nombre de cada `Sonda` coincida con el `probe` del `ProbeResult` que
   produce, porque la reanudación persiste por uno y busca por el otro: si
   divergen, la evidencia se guarda y no se reutiliza nunca, y cada ejecución
   vuelve a pagar todo;
3. que TODO el gasto pase por la contabilidad del runner, para que
   `--presupuesto-usd` sea un tope global de verdad y no tres topes locales.

Ni un solo test sale a la red: el proveedor se inyecta por
`ctx.opciones["proveedor_llm"]`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from edecan_forge_probe.modelcard import ArgProfile, Capability, ProbeResult
from edecan_forge_probe.probes import SONDAS, registro
from edecan_forge_probe.probes import tools as sonda_tools
from edecan_forge_probe.providers import ProbeCompletionResponse
from edecan_forge_probe.runner import (
    Contabilidad,
    ContextoSonda,
    PresupuestoAgotado,
    componer_modelcard,
    descubrir_sondas,
    filtrar_sondas,
)
from edecan_llm.base import (
    ChatMessage,
    CompletionRequest,
    LLMProvider,
    StreamChunk,
    ToolSpec,
    Usage,
)

# --------------------------------------------------------------------------- #
# Dobles
# --------------------------------------------------------------------------- #


class ProveedorFalso(LLMProvider):
    """`LLMProvider` en memoria. Devuelve lo que se le diga y cuenta llamadas."""

    name = "falso"

    def __init__(
        self,
        *,
        texto: str = "listo",
        tool_calls_crudos: list[dict[str, Any]] | None = None,
        entrada: int = 100,
        salida: int = 20,
        cacheados: int | None = 40,
        razonamiento: int | None = 12,
        neurons: float | None = 3.5,
        excepcion: Exception | None = None,
    ) -> None:
        self.model = "modelo-falso"
        self.peticiones: list[CompletionRequest] = []
        self._texto = texto
        self._crudos = tool_calls_crudos or []
        self._entrada = entrada
        self._salida = salida
        self._cacheados = cacheados
        self._razonamiento = razonamiento
        self._neurons = neurons
        self._excepcion = excepcion
        self.cerrado = False

    async def aclose(self) -> None:
        self.cerrado = True

    async def complete(self, req: CompletionRequest) -> ProbeCompletionResponse:
        self.peticiones.append(req)
        if self._excepcion is not None:
            raise self._excepcion
        return ProbeCompletionResponse(
            text=self._texto,
            usage=Usage(input_tokens=self._entrada, output_tokens=self._salida),
            stop_reason="end",
            cached_tokens=self._cacheados,
            reasoning_tokens=self._razonamiento,
            reasoning_content="pensando",
            neurons=self._neurons,
            tool_calls_crudos=self._crudos,
        )

    async def stream(self, req: CompletionRequest) -> AsyncIterator[StreamChunk]:
        self.peticiones.append(req)
        yield StreamChunk(type="text", text=self._texto)
        yield StreamChunk(
            type="usage",
            usage=Usage(input_tokens=self._entrada, output_tokens=self._salida),
        )


def contexto(
    tmp_path: Path,
    proveedor: LLMProvider | None = None,
    *,
    sonda: str = "prueba",
    presupuesto: float | None = None,
    precios: bool = True,
    **opciones: Any,
) -> ContextoSonda:
    conta = Contabilidad(
        precio_entrada_usd_mtok=0.95 if precios else None,
        precio_salida_usd_mtok=4.00 if precios else None,
        precio_cacheado_usd_mtok=0.19 if precios else None,
    )
    extra: dict[str, Any] = dict(opciones)
    if proveedor is not None:
        extra["proveedor_llm"] = proveedor
    return ContextoSonda(
        modelo="modelo-falso",
        proveedor="ollama",
        sonda=sonda,
        directorio_evidencia=tmp_path,
        contabilidad=conta,
        cancelacion=asyncio.Event(),
        presupuesto_usd=presupuesto,
        opciones=extra,
    )


# --------------------------------------------------------------------------- #
# Descubrimiento
# --------------------------------------------------------------------------- #


def test_el_runner_descubre_las_sondas() -> None:
    """Sin esto, `sondear` sale con código 1 diciendo «no hay sondas instaladas»."""
    descubiertas = descubrir_sondas()
    assert descubiertas, "descubrir_sondas() devolvió vacío: el registro no se está viendo"
    assert {s.nombre for s in descubiertas} == {s.nombre for s in SONDAS}


def test_los_nombres_de_sonda_no_se_repiten() -> None:
    nombres = [s.nombre for s in SONDAS]
    assert len(nombres) == len(set(nombres))


def test_hay_una_sonda_por_perfil_de_argumento() -> None:
    """`native_tools` se mide POR perfil: un número agregado no acciona nada."""
    nombres = {s.nombre for s in SONDAS}
    for perfil in ArgProfile:
        assert f"native_tools.{perfil.value}" in nombres


def test_solo_filtra_por_grupo() -> None:
    """`--solo tools,perf` tiene que seleccionar las series pese a llamarse `native_tools.*`."""
    seleccion = {s.nombre for s in filtrar_sondas(SONDAS, ("tools", "perf"))}
    assert "native_tools.code_blob" in seleccion
    assert "perf.ttft" in seleccion
    assert "context" not in seleccion


def test_los_grupos_son_los_tres_del_cli() -> None:
    assert {s.grupo for s in SONDAS} == {"perf", "tools", "context"}


# --------------------------------------------------------------------------- #
# Nombre de sonda == clave de persistencia
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("nombre", "metodo"),
    [
        ("perf.ttft", "ttft"),
        ("perf.throughput", "throughput"),
        ("perf.prefix_cache", "cache_prefijo"),
        ("perf.structured_output", "salida_estructurada"),
        ("perf.reasoning_overhead", "sobrecarga_razonamiento"),
        ("perf.vision", "vision"),
    ],
)
async def test_el_nombre_de_la_sonda_coincide_con_el_probe_publicado(
    tmp_path: Path, nombre: str, metodo: str
) -> None:
    """`guardar_resultado` indexa por `ProbeResult.probe` y el runner busca por
    `Sonda.nombre`. Si divergen, la reanudación no reutiliza NADA y cada
    ejecución vuelve a pagar el barrido entero sin que nada avise."""
    sonda = registro._sonda_perf(nombre, metodo)
    resultado = await sonda.ejecutar(contexto(tmp_path, ProveedorFalso(), sonda=nombre))
    assert isinstance(resultado, ProbeResult)
    assert resultado.probe == nombre


async def test_el_nombre_de_las_series_de_tools_coincide_con_su_probe(tmp_path: Path) -> None:
    sonda = registro._sonda_tools(
        "native_tools.scalar",
        perfiles=(ArgProfile.SCALAR,),
        escalones_herramientas=(),
        escalones_schema=(),
    )
    ctx = contexto(
        tmp_path, ProveedorFalso(), sonda="native_tools.scalar", n=registro._MINIMO_TOOLS
    )
    resultado = await sonda.ejecutar(ctx)
    assert resultado.probe == "native_tools.scalar"


async def test_la_sonda_de_contexto_publica_el_probe_esperado(tmp_path: Path) -> None:
    sonda = next(s for s in SONDAS if s.grupo == "context")
    ctx = contexto(tmp_path, ProveedorFalso(), sonda=sonda.nombre, n=1, profundidades=(200,))
    resultado = await sonda.ejecutar(ctx)
    assert resultado.probe == sonda.nombre


# --------------------------------------------------------------------------- #
# Contabilidad única
# --------------------------------------------------------------------------- #


async def test_el_gasto_de_cualquier_sonda_cae_en_la_contabilidad_del_runner(
    tmp_path: Path,
) -> None:
    """Las tres sondas traían su propio contador; el tope global sólo significa
    algo si todas apuntan en el mismo sitio."""
    proveedor = ProveedorFalso(entrada=1_000, salida=200, cacheados=400, razonamiento=50)
    ctx = contexto(tmp_path, proveedor, sonda="perf.structured_output")
    await registro._sonda_perf("perf.structured_output", "salida_estructurada").ejecutar(ctx)

    assert ctx.contabilidad.tokens_entrada > 0
    assert ctx.contabilidad.tokens_salida > 0
    assert ctx.contabilidad.tokens_entrada_cacheados > 0
    assert ctx.contabilidad.coste_usd is not None and ctx.contabilidad.coste_usd > 0


async def test_la_entrada_cacheada_se_factura_a_su_precio(tmp_path: Path) -> None:
    """0,19 vs 0,95 USD/MTok: si el envoltorio no propaga `cached_tokens`, el
    coste sale hasta 5x más caro y la decisión económica de la fase 1 cambia."""
    proveedor = ProveedorFalso(entrada=1_000_000, salida=0, cacheados=1_000_000, razonamiento=0)
    ctx = contexto(tmp_path, proveedor)
    envoltorio = registro.construir_proveedor(ctx)
    await envoltorio.complete(
        CompletionRequest(model="m", messages=[ChatMessage(role="user", content="hola")])
    )
    assert ctx.contabilidad.tokens_entrada_cacheados == 1_000_000
    assert ctx.contabilidad.coste_usd == pytest.approx(0.19, rel=1e-6)


async def test_el_presupuesto_global_corta_la_sonda(tmp_path: Path) -> None:
    proveedor = ProveedorFalso(entrada=1_000_000, salida=1_000_000, cacheados=0)
    ctx = contexto(tmp_path, proveedor, presupuesto=0.01)
    envoltorio = registro.construir_proveedor(ctx)
    with pytest.raises(PresupuestoAgotado):
        await envoltorio.complete(
            CompletionRequest(model="m", messages=[ChatMessage(role="user", content="hola")])
        )
    # Se anota ANTES de lanzar: el token ya se gastó y la factura lo refleja.
    assert ctx.contabilidad.tokens_entrada == 1_000_000


async def test_el_envoltorio_no_esconde_las_banderas_del_proveedor(tmp_path: Path) -> None:
    """Las sondas leen `soporta_imagenes` y compañía con getattr; si el
    envoltorio las tapa, la medición cambia sin que nadie lo pida."""
    proveedor = ProveedorFalso()
    proveedor.soporta_imagenes = False  # type: ignore[attr-defined]
    envoltorio = registro.construir_proveedor(contexto(tmp_path, proveedor))
    assert envoltorio.soporta_imagenes is False


async def test_el_streaming_tambien_se_contabiliza(tmp_path: Path) -> None:
    proveedor = ProveedorFalso(entrada=500, salida=300)
    ctx = contexto(tmp_path, proveedor)
    envoltorio = registro.construir_proveedor(ctx)
    async for _ in envoltorio.stream(
        CompletionRequest(model="m", messages=[ChatMessage(role="user", content="hola")])
    ):
        pass
    assert ctx.contabilidad.tokens_entrada == 500
    assert ctx.contabilidad.tokens_salida == 300


# --------------------------------------------------------------------------- #
# Adaptador de tool-calling
# --------------------------------------------------------------------------- #


HERRAMIENTA = ToolSpec(
    name="apply_patch",
    description="Aplica un parche.",
    input_schema={"type": "object", "properties": {"patch": {"type": "string"}}},
)


async def test_el_adaptador_conserva_los_argumentos_crudos() -> None:
    """Si el adaptador reserializara desde el dict ya parseado, `json_invalido`
    dejaría de ser observable y la sonda mediría el parser, no al modelo."""
    crudo = '{"patch": "def f():\\n    return 1"}'
    adaptador = registro.AdaptadorHerramientas(
        ProveedorFalso(
            tool_calls_crudos=[{"function": {"name": "apply_patch", "arguments": crudo}}]
        )
    )
    respuesta = await adaptador.invocar(
        sistema="s", prompt="p", herramientas=[HERRAMIENTA], max_tokens=256
    )
    assert respuesta.llamadas[0].argumentos_json == crudo
    assert respuesta.llamadas[0].nombre == "apply_patch"


async def test_el_adaptador_propaga_las_tres_senales_de_la_fase_0() -> None:
    adaptador = registro.AdaptadorHerramientas(
        ProveedorFalso(entrada=900, salida=120, cacheados=300, razonamiento=80, neurons=7.25)
    )
    r = await adaptador.invocar(sistema="s", prompt="p", herramientas=[HERRAMIENTA], max_tokens=256)
    assert (r.tokens_cacheados, r.tokens_razonamiento, r.neuronas) == (300, 80, 7.25)
    assert r.razonamiento == "pensando"


async def test_un_fallo_de_transporte_no_se_cuenta_como_fallo_del_modelo() -> None:
    """`RespuestaSonda.error` saca el intento del denominador; contarlo como
    fallo de tool-calling falsearía la fiabilidad hacia abajo."""
    adaptador = registro.AdaptadorHerramientas(ProveedorFalso(excepcion=RuntimeError("502")))
    r = await adaptador.invocar(sistema="s", prompt="p", herramientas=[HERRAMIENTA], max_tokens=256)
    assert r.error is not None and "502" in r.error
    assert r.llamadas == []


async def test_el_adaptador_pasa_las_herramientas_al_proveedor() -> None:
    proveedor = ProveedorFalso()
    await registro.AdaptadorHerramientas(proveedor).invocar(
        sistema="s", prompt="p", herramientas=[HERRAMIENTA], max_tokens=64
    )
    peticion = proveedor.peticiones[0]
    assert [t.name for t in peticion.tools] == ["apply_patch"]
    assert peticion.max_tokens == 64
    assert peticion.temperature == 0.0


# --------------------------------------------------------------------------- #
# `--n`
# --------------------------------------------------------------------------- #


def test_el_minimo_de_tool_calling_se_deriva_del_umbral() -> None:
    """No es una preferencia: con menos muestras el umbral es inalcanzable incluso
    con un modelo perfecto, así que el `FALLA` sería del intervalo de confianza."""
    assert registro._MINIMO_TOOLS == 35
    assert sonda_tools.techo_lower_95(registro._MINIMO_TOOLS) >= sonda_tools.UMBRAL_SELECCION
    assert sonda_tools.techo_lower_95(registro._MINIMO_TOOLS - 1) < sonda_tools.UMBRAL_SELECCION


async def test_n_por_debajo_del_minimo_no_publica_tool_calling(tmp_path: Path) -> None:
    """Con N por debajo del mínimo el límite inferior de Wilson es tan ancho que el
    número no decide nada: es preferible `ok=False` que una fiabilidad que nadie
    puede contrastar con el umbral."""
    sonda = registro._sonda_tools(
        "native_tools.scalar",
        perfiles=(ArgProfile.SCALAR,),
        escalones_herramientas=(),
        escalones_schema=(),
    )
    ctx = contexto(tmp_path, ProveedorFalso(), sonda="native_tools.scalar", n=3)
    resultado = await sonda.ejecutar(ctx)
    assert resultado.ok is False
    assert str(registro._MINIMO_TOOLS) in (resultado.error or "")
    assert ctx.contabilidad.llamadas == 0, "no debe gastarse ni una llamada"


async def test_n_recorta_las_muestras_de_rendimiento(tmp_path: Path) -> None:
    proveedor = ProveedorFalso()
    sonda = registro._sonda_perf("perf.vision", "vision", Capability.VISION)
    ctx = contexto(tmp_path, proveedor, sonda="perf.vision", n=2)
    await sonda.ejecutar(ctx)
    assert len(proveedor.peticiones) == 2


# --------------------------------------------------------------------------- #
# Robustez
# --------------------------------------------------------------------------- #


async def test_un_fallo_al_construir_el_proveedor_sale_como_ok_falso(tmp_path: Path) -> None:
    """Una sonda que revienta no debe tumbar el barrido ni, sobre todo,
    aparecer en la tarjeta como una medición mala."""
    ctx = contexto(tmp_path, sonda="perf.ttft")
    ctx.proveedor = "inventado"
    resultado = await registro._sonda_perf("perf.ttft", "ttft").ejecutar(ctx)
    assert resultado.ok is False
    assert "inventado" in (resultado.error or "")


async def test_el_proveedor_se_cierra_siempre(tmp_path: Path) -> None:
    proveedor = ProveedorFalso()
    await registro._sonda_perf("perf.vision", "vision").ejecutar(
        contexto(tmp_path, proveedor, sonda="perf.vision", n=1)
    )
    assert proveedor.cerrado is True


def test_construir_proveedor_rechaza_un_nombre_desconocido(tmp_path: Path) -> None:
    ctx = contexto(tmp_path)
    ctx.proveedor = "openai"
    with pytest.raises(ValueError, match="proveedor desconocido"):
        registro.construir_proveedor(ctx)


# --------------------------------------------------------------------------- #
# La medición tiene que LLEGAR a la tarjeta
# --------------------------------------------------------------------------- #


async def test_las_series_de_tool_calling_llegan_a_la_modelcard(tmp_path: Path) -> None:
    """El fallo que esto vigila es silencioso y caro: la sonda mide, se paga la
    factura y el `ProbeResult` no publica la clave que el runner necesita, así
    que el umbral se queda en SIN_DATO para siempre y nadie se entera.

    `Capability.NATIVE_TOOLS` por sí sola no dice A QUÉ perfil pertenece la
    fiabilidad; hace falta `detalle["arg_profile"]` (o `detalle["modelcard"]`).
    """
    resultados = []
    for nombre, kwargs in (
        (
            "native_tools.code_blob",
            {
                "perfiles": (ArgProfile.CODE_BLOB,),
                "escalones_herramientas": (),
                "escalones_schema": (),
            },
        ),
        ("native_tools.max_tools_effective", {"perfiles": (), "escalones_schema": ()}),
        ("native_tools.max_schema_bytes", {"perfiles": (), "escalones_herramientas": ()}),
    ):
        sonda = registro._sonda_tools(nombre, **kwargs)
        ctx = contexto(
            tmp_path / nombre,
            ProveedorFalso(),
            sonda=nombre,
            n=registro._MINIMO_TOOLS,
        )
        (tmp_path / nombre).mkdir(parents=True, exist_ok=True)
        resultados.append(await sonda.ejecutar(ctx))

    card = componer_modelcard(resultados, modelo="m", proveedor="ollama", revision_sonda="rev")
    assert ArgProfile.CODE_BLOB in card.native_tools, (
        "la fiabilidad de code_blob se midió y no llegó a la tarjeta"
    )
    assert card.max_tools_effective is not None
    assert card.max_schema_bytes is not None
    assert card._lectura("native_tools.code_blob.lower_95") is not None


async def test_las_sondas_de_rendimiento_llegan_a_la_modelcard(tmp_path: Path) -> None:
    resultados = [
        await registro._sonda_perf(nombre, metodo).ejecutar(
            contexto(tmp_path, ProveedorFalso(), sonda=nombre, n=3)
        )
        for nombre, metodo in (
            ("perf.ttft", "ttft"),
            ("perf.vision", "vision"),
            ("perf.structured_output", "salida_estructurada"),
        )
    ]
    card = componer_modelcard(resultados, modelo="m", proveedor="ollama", revision_sonda="rev")
    assert card.ttft is not None, "el TTFT medido no llegó a la tarjeta"
    assert card.vision is not None
    assert card.structured_output is not None
