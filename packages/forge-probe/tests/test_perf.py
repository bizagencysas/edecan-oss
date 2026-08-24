"""Tests de la sonda de rendimiento con proveedor falso y reloj falso.

Ni un byte sale a la red: `ProveedorFalso` implementa el `Protocol` estructural
de la sonda y `RelojFalso` sustituye a `time.perf_counter`, así que las latencias
son exactas y las aserciones son igualdades, no rangos. Ese es el punto: si un
test de una sonda de latencia necesita tolerancias, no está midiendo la sonda,
está midiendo la máquina.
"""

from __future__ import annotations

import json
import struct
import zlib
from base64 import b64encode
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from edecan_forge_probe.modelcard import Capability
from edecan_forge_probe.probes.perf import (
    ALFABETO_VISION,
    PRECIO_ENTRADA_CACHEADA_USD_MTOK,
    PRECIO_ENTRADA_USD_MTOK,
    PRECIO_SALIDA_USD_MTOK,
    CapacidadNoSoportada,
    ConfigPerf,
    EventoStream,
    FalloEstructura,
    PeticionPerf,
    Presupuesto,
    PresupuestoAgotado,
    PuenteLLMProvider,
    RespuestaLLM,
    SondaRendimiento,
    TipoEvento,
    UsoLLM,
    clasificar_estructura,
    codigo_vision,
    coste_usd,
    percentil,
    png_con_texto,
    sanear,
    semilla_estable,
    texto_de_tokens,
)
from edecan_llm.base import CompletionRequest, CompletionResponse, StreamChunk, Usage
from edecan_llm.multimodal import image_source

# --------------------------------------------------------------------------- #
# Dobles
# --------------------------------------------------------------------------- #


class RelojFalso:
    """Reloj monótono controlado por el test."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def avanzar(self, dt: float) -> None:
        self.t += dt


class ProveedorFalso:
    """Adaptador de mentira que cumple el `Protocol` de la sonda.

    `guion` describe el stream como `(dt_antes, tipo, texto)`; el proveedor
    avanza el reloj falso justo antes de emitir cada evento, así que la sonda
    observa exactamente los tiempos que el test escribió.
    """

    modelo = "falso/kimi-k2.7-code"

    def __init__(
        self,
        reloj: RelojFalso,
        *,
        guion: list[tuple[float, TipoEvento, str]] | None = None,
        uso: UsoLLM | None = None,
        completar_fn: Callable[[int, PeticionPerf], tuple[RespuestaLLM, float]] | None = None,
        **atributos: Any,
    ) -> None:
        self.reloj = reloj
        self.guion = guion or [
            (0.20, TipoEvento.REASONING, "pensando"),
            (0.30, TipoEvento.CONTENT, "hola"),
            (0.10, TipoEvento.CONTENT, " mundo"),
        ]
        self.uso = uso or UsoLLM(prompt_tokens=100, completion_tokens=41, reasoning_tokens=20)
        self.completar_fn = completar_fn
        self.peticiones: list[PeticionPerf] = []
        for k, v in atributos.items():
            setattr(self, k, v)

    async def completar(self, peticion: PeticionPerf) -> RespuestaLLM:
        indice = len(self.peticiones)
        self.peticiones.append(peticion)
        if self.completar_fn is None:
            self.reloj.avanzar(0.5)
            return RespuestaLLM(content="ALFA", reasoning_content="mmm", uso=self.uso)
        respuesta, dt = self.completar_fn(indice, peticion)
        self.reloj.avanzar(dt)
        return respuesta

    async def transmitir(self, peticion: PeticionPerf) -> AsyncIterator[EventoStream]:
        self.peticiones.append(peticion)
        for dt, tipo, texto in self.guion:
            self.reloj.avanzar(dt)
            yield EventoStream(tipo=tipo, texto=texto)
        yield EventoStream(tipo=TipoEvento.USAGE, uso=self.uso)


CFG_RAPIDA = ConfigPerf(
    muestras_ttft=3,
    tokens_prompt_corto=20,
    tokens_prompt_largo=60,
    tokens_prefijo_cache=40,
    rondas_cache=2,
    muestras_estructurado=4,
    muestras_vision=2,
    escala_vision=3,
    muestras_razonamiento=2,
    longitudes_razonamiento=(24, 200),
    muestras_throughput=2,
)


def informe_valido(i: int = 0) -> str:
    """Un `InformeRevision` que valida contra el esquema."""
    return json.dumps(
        {
            "repo": "edecan",
            "commit": f"c0ffee{i:02d}",
            "resumen": "revisión de la sonda",
            "aprobado": False,
            "hallazgos": [
                {
                    "id": "H1",
                    "severidad": "alta",
                    "ubicacion": {"archivo": "perf.py", "linea": 12, "columna": 4},
                    "etiquetas": ["latencia"],
                    "sugerencia": "medir ttfc aparte",
                },
                {
                    "id": "H2",
                    "severidad": "baja",
                    "ubicacion": {"archivo": "perf.py", "linea": 90},
                    "etiquetas": ["coste"],
                },
            ],
        }
    )


# --------------------------------------------------------------------------- #
# Utilidades puras
# --------------------------------------------------------------------------- #


def test_percentil_interpola_linealmente() -> None:
    assert percentil([1.0], 0.95) == 1.0
    assert percentil([1.0, 2.0, 3.0, 4.0], 0.50) == 2.5
    assert percentil([0.0, 10.0], 0.95) == 9.5


def test_percentil_sin_muestras_falla() -> None:
    with pytest.raises(ValueError, match="percentil"):
        percentil([], 0.5)


def test_coste_usd_cobra_los_cacheados_a_tarifa_barata() -> None:
    """El cacheado es un SUBCONJUNTO del prompt, no un extra: 5x más barato."""
    uso = UsoLLM(prompt_tokens=1_000_000, completion_tokens=0, cached_tokens=1_000_000)
    assert coste_usd(uso) == pytest.approx(PRECIO_ENTRADA_CACHEADA_USD_MTOK)

    frio = UsoLLM(prompt_tokens=1_000_000, completion_tokens=0)
    assert coste_usd(frio) == pytest.approx(PRECIO_ENTRADA_USD_MTOK)
    assert coste_usd(frio) == pytest.approx(coste_usd(uso) * 5.0)


def test_coste_usd_factura_el_razonamiento_como_salida() -> None:
    uso = UsoLLM(prompt_tokens=0, completion_tokens=1_000_000)
    assert coste_usd(uso) == pytest.approx(PRECIO_SALIDA_USD_MTOK)


def test_sanear_borra_credenciales() -> None:
    sucio = "401 Authorization: Bearer sk-abcdef0123456789abcdef y api_key=zzzzzzzzzzzz"
    limpio = sanear(sucio)
    assert "sk-abcdef0123456789abcdef" not in limpio
    assert "zzzzzzzzzzzz" not in limpio
    assert "401" in limpio


def test_semilla_estable_no_depende_del_proceso() -> None:
    assert semilla_estable("corto|low", 3) == semilla_estable("corto|low", 3)
    assert semilla_estable("corto|low", 3) != semilla_estable("corto|low", 4)


def test_texto_de_tokens_es_determinista_y_aproxima_la_longitud() -> None:
    a = texto_de_tokens(500, semilla=7)
    assert a == texto_de_tokens(500, semilla=7)
    assert a != texto_de_tokens(500, semilla=8)
    assert 400 <= len(a) // 4 <= 600


# --------------------------------------------------------------------------- #
# PNG
# --------------------------------------------------------------------------- #


def test_png_con_texto_produce_un_png_decodificable() -> None:
    png = png_con_texto("KX37A", escala=4, margen=2)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    ancho, alto, prof, color = struct.unpack(">IIBB", png[16:26])
    assert (prof, color) == (8, 0)
    assert ancho == (5 * 6 - 1) * 4 + 4
    assert alto == 7 * 4 + 4

    largo_idat = struct.unpack(">I", png[33:37])[0]
    idat = png[41 : 41 + largo_idat]
    crudo = zlib.decompress(idat)
    assert len(crudo) == alto * (ancho + 1)
    # Hay tinta negra: si no, la "imagen" sería un folio en blanco y la sonda de
    # visión estaría midiendo la nada.
    assert b"\x00\x00\x00" in crudo


def test_png_rechaza_caracteres_sin_glifo() -> None:
    with pytest.raises(ValueError, match="carácter sin glifo"):
        png_con_texto("O0")


def test_codigo_vision_usa_solo_el_alfabeto_seguro() -> None:
    codigo = codigo_vision(1)
    assert len(codigo) == 5
    assert set(codigo) <= set(ALFABETO_VISION)
    assert "O" not in ALFABETO_VISION and "0" not in ALFABETO_VISION


# --------------------------------------------------------------------------- #
# Clasificación de salida estructurada
# --------------------------------------------------------------------------- #


def test_clasificar_distingue_los_tres_fallos() -> None:
    assert clasificar_estructura(informe_valido()) is FalloEstructura.OK
    assert clasificar_estructura("") is FalloEstructura.SIN_CONTENIDO
    assert clasificar_estructura("   ") is FalloEstructura.SIN_CONTENIDO
    assert clasificar_estructura("claro, aquí tienes: {") is FalloEstructura.JSON_INVALIDO
    assert clasificar_estructura('{"repo": "edecan"}') is FalloEstructura.ESQUEMA_INVALIDO


def test_esquema_rechaza_enum_fuera_de_rango_y_campos_extra() -> None:
    con_enum_malo = json.loads(informe_valido())
    con_enum_malo["hallazgos"][0]["severidad"] = "urgentisima"
    assert clasificar_estructura(json.dumps(con_enum_malo)) is FalloEstructura.ESQUEMA_INVALIDO

    con_extra = json.loads(informe_valido())
    con_extra["inventado"] = 1
    assert clasificar_estructura(json.dumps(con_extra)) is FalloEstructura.ESQUEMA_INVALIDO

    con_un_hallazgo = json.loads(informe_valido())
    con_un_hallazgo["hallazgos"] = con_un_hallazgo["hallazgos"][:1]
    assert clasificar_estructura(json.dumps(con_un_hallazgo)) is FalloEstructura.ESQUEMA_INVALIDO


def test_el_shim_recupera_json_encercado_pero_no_el_invalido() -> None:
    cercado = f"```json\n{informe_valido()}\n```"
    assert clasificar_estructura(cercado) is FalloEstructura.JSON_INVALIDO
    assert clasificar_estructura(cercado, con_shim=True) is FalloEstructura.OK
    assert clasificar_estructura('{"repo": "x"}', con_shim=True) is FalloEstructura.ESQUEMA_INVALIDO


# --------------------------------------------------------------------------- #
# Presupuesto
# --------------------------------------------------------------------------- #


def test_presupuesto_sin_tope_nunca_reserva() -> None:
    p = Presupuesto(None)
    p.reservar(PeticionPerf(prompt="x" * 10_000_000, max_tokens=100_000))


def test_presupuesto_estima_pesimista_y_corta() -> None:
    p = Presupuesto(max_usd=0.0001)
    caro = PeticionPerf(prompt=texto_de_tokens(50_000), max_tokens=4096)
    with pytest.raises(PresupuestoAgotado):
        p.reservar(caro)


# --------------------------------------------------------------------------- #
# TTFT
# --------------------------------------------------------------------------- #


async def test_ttft_separa_escenarios_y_mide_ttfc_aparte() -> None:
    reloj = RelojFalso()
    prov = ProveedorFalso(
        reloj,
        guion=[
            (0.20, TipoEvento.REASONING, "pensando"),
            (0.30, TipoEvento.CONTENT, "hola"),
        ],
    )
    sonda = SondaRendimiento(prov, config=CFG_RAPIDA, reloj=reloj)

    res = await sonda.ttft()

    assert res.ok and res.capability is Capability.STREAMING
    assert res.latencia is not None
    assert res.latencia.muestras == CFG_RAPIDA.muestras_ttft
    # TTFT es el primer token de CUALQUIER canal: el de razonamiento.
    assert res.latencia.p50 == pytest.approx(0.20)
    assert res.latencia.p95 == pytest.approx(0.20)

    escenarios = res.detalle["escenarios"]
    assert set(escenarios) == {"corto|low", "corto|high", "largo|low", "largo|high"}
    assert res.detalle["escenario_reportado"] == "corto|low"
    # TTFC (primer token visible) llega 0,30 s más tarde: es la pantalla vacía.
    assert escenarios["corto|low"]["ttfc"]["p50"] == pytest.approx(0.50)
    assert escenarios["corto|low"]["sin_content"] == 0
    assert res.detalle["p95_peor"] == pytest.approx(0.20)

    largos = [p for p in prov.peticiones if len(p.prompt) > 200]
    assert len(largos) == 2 * CFG_RAPIDA.muestras_ttft
    assert {p.reasoning_effort for p in prov.peticiones} == {"low", "high"}


async def test_ttft_cuenta_las_respuestas_sin_contenido() -> None:
    """Sólo razonamiento y `content` vacío: el modo de fallo de la K2.7."""
    reloj = RelojFalso()
    prov = ProveedorFalso(reloj, guion=[(0.4, TipoEvento.REASONING, "pensando mucho")])
    sonda = SondaRendimiento(prov, config=CFG_RAPIDA, reloj=reloj)

    res = await sonda.ttft()

    assert res.ok
    assert res.detalle["escenarios"]["corto|low"]["ttfc"] is None
    assert res.detalle["escenarios"]["corto|low"]["sin_content"] == CFG_RAPIDA.muestras_ttft


async def test_ttft_omite_el_esfuerzo_si_el_adaptador_no_lo_acepta() -> None:
    reloj = RelojFalso()
    prov = ProveedorFalso(reloj, acepta_reasoning_effort=False)
    sonda = SondaRendimiento(prov, config=CFG_RAPIDA, reloj=reloj)

    res = await sonda.ttft()

    assert set(res.detalle["escenarios"]) == {"corto|defecto", "largo|defecto"}
    assert res.detalle["acepta_reasoning_effort"] is False
    assert all(p.reasoning_effort is None for p in prov.peticiones)


async def test_ttft_devuelve_ok_false_si_el_proveedor_revienta() -> None:
    class Roto(ProveedorFalso):
        async def transmitir(self, peticion: PeticionPerf) -> AsyncIterator[EventoStream]:
            raise RuntimeError("Authorization: Bearer sk-secreto0123456789abcd caducado")
            yield  # pragma: no cover

    reloj = RelojFalso()
    sonda = SondaRendimiento(Roto(reloj), config=CFG_RAPIDA, reloj=reloj)

    res = await sonda.ttft()

    assert res.ok is False
    assert res.latencia is None
    assert res.error is not None
    assert "sk-secreto0123456789abcd" not in res.error
    assert "caducado" in res.error


# --------------------------------------------------------------------------- #
# Throughput
# --------------------------------------------------------------------------- #


async def test_throughput_mide_solo_la_ventana_sostenida() -> None:
    reloj = RelojFalso()
    prov = ProveedorFalso(
        reloj,
        guion=[
            (5.0, TipoEvento.REASONING, "arranque lento"),
            (10.0, TipoEvento.CONTENT, "texto largo"),
        ],
        uso=UsoLLM(prompt_tokens=50, completion_tokens=901, reasoning_tokens=100),
    )
    sonda = SondaRendimiento(prov, config=CFG_RAPIDA, reloj=reloj)

    res = await sonda.throughput()

    # (901 - 1) tokens en la ventana de 10 s que va del primer al último token.
    # Los 5 s de arranque NO entran: eso es TTFT, no throughput.
    assert res.ok
    assert res.valor == pytest.approx(90.0)
    # Y descontando los 100 tokens de razonamiento quedan 800 visibles en 10 s.
    assert res.detalle["tps_contenido_mediana"] == pytest.approx(80.0)
    assert res.detalle["muestras_por_debajo_del_minimo"] == 0
    assert res.detalle["muestras_validas"] == CFG_RAPIDA.muestras_throughput


async def test_throughput_marca_las_respuestas_demasiado_cortas() -> None:
    reloj = RelojFalso()
    prov = ProveedorFalso(
        reloj,
        guion=[(1.0, TipoEvento.CONTENT, "corto"), (1.0, TipoEvento.CONTENT, "!")],
        uso=UsoLLM(prompt_tokens=10, completion_tokens=11),
    )
    sonda = SondaRendimiento(prov, config=CFG_RAPIDA, reloj=reloj)

    res = await sonda.throughput()

    assert res.detalle["muestras_por_debajo_del_minimo"] == CFG_RAPIDA.muestras_throughput
    assert res.detalle["tokens_minimos_exigidos"] == 800
    assert res.detalle["tps_contenido_mediana"] is None


# --------------------------------------------------------------------------- #
# Caché de prefijo
# --------------------------------------------------------------------------- #


def _cache_fn(
    reloj: RelojFalso, *, cached_2: int, t1: float, t2: float
) -> Callable[[int, PeticionPerf], tuple[RespuestaLLM, float]]:
    """Segunda llamada de cada ronda: `cached_2` tokens cacheados y `t2` s."""

    def fn(indice: int, peticion: PeticionPerf) -> tuple[RespuestaLLM, float]:
        segunda = indice % 2 == 1
        uso = UsoLLM(
            prompt_tokens=200,
            completion_tokens=30,
            cached_tokens=cached_2 if segunda else 0,
        )
        return RespuestaLLM(content="ALFA", uso=uso), (t2 if segunda else t1)

    return fn


async def test_cache_prefijo_exige_las_dos_senales() -> None:
    reloj = RelojFalso()
    prov = ProveedorFalso(reloj, completar_fn=_cache_fn(reloj, cached_2=180, t1=1.0, t2=0.4))
    sonda = SondaRendimiento(prov, config=CFG_RAPIDA, reloj=reloj)

    res = await sonda.cache_prefijo()

    assert res.ok and res.capability is Capability.PREFIX_CACHE
    assert res.detalle["prefix_cache"] is True
    assert res.reliability is not None and res.reliability.mean == 1.0
    ronda = res.detalle["rondas"][0]
    assert ronda["senal_tokens"] is True and ronda["senal_latencia"] is True
    assert ronda["caida_latencia_pct"] == pytest.approx(60.0)
    # Ahorro real de dinero: 180 tokens pasan de 0,95 a 0,19 USD/M.
    esperado = (180 * (PRECIO_ENTRADA_USD_MTOK - PRECIO_ENTRADA_CACHEADA_USD_MTOK)) / 1e6
    assert ronda["coste_sin_cache_usd"] - ronda["coste_real_usd"] == pytest.approx(esperado)
    assert res.valor == pytest.approx(ronda["ahorro_pct"])


async def test_cache_prefijo_falso_si_solo_baja_la_latencia() -> None:
    """Latencia menor sin `cached_tokens` es ruido de red, no caché."""
    reloj = RelojFalso()
    prov = ProveedorFalso(reloj, completar_fn=_cache_fn(reloj, cached_2=0, t1=1.0, t2=0.4))
    sonda = SondaRendimiento(prov, config=CFG_RAPIDA, reloj=reloj)

    res = await sonda.cache_prefijo()

    assert res.detalle["prefix_cache"] is False
    assert res.detalle["rondas_con_senal_latencia"] == CFG_RAPIDA.rondas_cache
    assert res.detalle["rondas_con_senal_tokens"] == 0
    assert res.valor is None


async def test_cache_prefijo_falso_si_solo_hay_cached_tokens() -> None:
    """Contabilidad optimista del proveedor sin ganancia real de tiempo."""
    reloj = RelojFalso()
    prov = ProveedorFalso(reloj, completar_fn=_cache_fn(reloj, cached_2=180, t1=1.0, t2=0.99))
    sonda = SondaRendimiento(prov, config=CFG_RAPIDA, reloj=reloj)

    res = await sonda.cache_prefijo()

    assert res.detalle["prefix_cache"] is False
    assert res.detalle["rondas_con_senal_tokens"] == CFG_RAPIDA.rondas_cache
    assert res.detalle["rondas_con_senal_latencia"] == 0


async def test_cache_prefijo_falso_si_apenas_cachea_una_parte() -> None:
    reloj = RelojFalso()
    prov = ProveedorFalso(reloj, completar_fn=_cache_fn(reloj, cached_2=5, t1=1.0, t2=0.2))
    sonda = SondaRendimiento(prov, config=CFG_RAPIDA, reloj=reloj)

    res = await sonda.cache_prefijo()

    assert res.detalle["prefix_cache"] is False
    assert res.detalle["rondas_con_senal_tokens"] == 0


async def test_cache_prefijo_no_publica_ahorro_si_no_acierta_toda_ronda() -> None:
    """Un descuento medido en una sola ronda no autoriza a diseñar contando con él."""
    reloj = RelojFalso()

    def fn(indice: int, peticion: PeticionPerf) -> tuple[RespuestaLLM, float]:
        primera_ronda = indice < 2
        segunda_llamada = indice % 2 == 1
        cacheados = 180 if (segunda_llamada and primera_ronda) else 0
        uso = UsoLLM(prompt_tokens=200, completion_tokens=30, cached_tokens=cacheados)
        return RespuestaLLM(content="ALFA", uso=uso), 0.4 if segunda_llamada else 1.0

    sonda = SondaRendimiento(ProveedorFalso(reloj, completar_fn=fn), config=CFG_RAPIDA, reloj=reloj)

    res = await sonda.cache_prefijo()

    assert res.detalle["prefix_cache"] is False
    assert res.reliability is not None and res.reliability.successes == 1
    assert res.valor is None
    assert res.detalle["modelcard"] == {"prefix_cache": False}


async def test_cache_prefijo_usa_un_prefijo_distinto_por_ronda() -> None:
    """Si dos rondas compartiesen prefijo, la segunda mediría la caché de la primera."""
    reloj = RelojFalso()
    prov = ProveedorFalso(reloj, completar_fn=_cache_fn(reloj, cached_2=180, t1=1.0, t2=0.4))
    sonda = SondaRendimiento(prov, config=CFG_RAPIDA, reloj=reloj)

    await sonda.cache_prefijo()

    prompts = [p.prompt for p in prov.peticiones]
    assert len(prompts) == 2 * CFG_RAPIDA.rondas_cache
    assert prompts[0][:200] == prompts[1][:200]  # misma ronda, mismo prefijo
    assert prompts[0][:200] != prompts[2][:200]  # otra ronda, otro prefijo


# --------------------------------------------------------------------------- #
# Salida estructurada
# --------------------------------------------------------------------------- #


async def test_salida_estructurada_separa_los_tipos_de_fallo() -> None:
    reloj = RelojFalso()
    respuestas = [
        informe_valido(0),
        "no soy json {",
        '{"repo": "edecan", "hallazgos": []}',
        "",
    ]

    def fn(indice: int, peticion: PeticionPerf) -> tuple[RespuestaLLM, float]:
        return RespuestaLLM(content=respuestas[indice % 4], uso=UsoLLM(prompt_tokens=80)), 0.3

    prov = ProveedorFalso(reloj, completar_fn=fn)
    sonda = SondaRendimiento(prov, config=CFG_RAPIDA, reloj=reloj)

    res = await sonda.salida_estructurada()

    assert res.ok and res.capability is Capability.STRUCTURED_OUTPUT
    nativo = res.detalle["modos"]["nativo"]
    assert nativo["ok_estricto"] == 1
    assert nativo["json_invalido"] == 1
    assert nativo["esquema_invalido"] == 1
    assert nativo["sin_contenido"] == 1
    assert res.detalle["modo_reportado"] == "nativo"
    assert res.reliability is not None and res.reliability.successes == 1
    assert res.valor == pytest.approx(res.reliability.lower_95)
    # El esquema viaja por `response_format` en nativo y por el prompt en prompted.
    nativas = prov.peticiones[: CFG_RAPIDA.muestras_estructurado]
    prompteadas = prov.peticiones[CFG_RAPIDA.muestras_estructurado :]
    assert all(p.esquema_json is not None for p in nativas)
    assert all(p.esquema_json is None and "JSON Schema" in p.prompt for p in prompteadas)


async def test_salida_estructurada_mide_la_ganancia_del_shim() -> None:
    reloj = RelojFalso()

    def fn(indice: int, peticion: PeticionPerf) -> tuple[RespuestaLLM, float]:
        contenido = f"```json\n{informe_valido(indice)}\n```"
        return RespuestaLLM(content=contenido, uso=UsoLLM(prompt_tokens=80)), 0.2

    prov = ProveedorFalso(reloj, completar_fn=fn)
    sonda = SondaRendimiento(prov, config=CFG_RAPIDA, reloj=reloj)

    res = await sonda.salida_estructurada()

    nativo = res.detalle["modos"]["nativo"]
    assert nativo["ok_estricto"] == 0
    assert nativo["json_invalido"] == CFG_RAPIDA.muestras_estructurado
    assert nativo["ok_con_shim"] == CFG_RAPIDA.muestras_estructurado
    assert nativo["ganancia_del_shim"] == CFG_RAPIDA.muestras_estructurado


async def test_salida_estructurada_cae_a_prompted_sin_response_format() -> None:
    reloj = RelojFalso()

    def fn(indice: int, peticion: PeticionPerf) -> tuple[RespuestaLLM, float]:
        return RespuestaLLM(content=informe_valido(indice), uso=UsoLLM(prompt_tokens=80)), 0.2

    prov = ProveedorFalso(reloj, completar_fn=fn, soporta_response_format=False)
    sonda = SondaRendimiento(prov, config=CFG_RAPIDA, reloj=reloj)

    res = await sonda.salida_estructurada()

    assert res.detalle["modo_reportado"] == "prompted"
    assert "omitido" in res.detalle["modos"]["nativo"]
    assert all(p.esquema_json is None for p in prov.peticiones)
    assert res.reliability is not None
    assert res.reliability.successes == CFG_RAPIDA.muestras_estructurado


# --------------------------------------------------------------------------- #
# Sobrecarga de razonamiento
# --------------------------------------------------------------------------- #


async def test_sobrecarga_razonamiento_calcula_el_ratio_por_celda() -> None:
    reloj = RelojFalso()

    def fn(indice: int, peticion: PeticionPerf) -> tuple[RespuestaLLM, float]:
        uso = UsoLLM(prompt_tokens=30, completion_tokens=100, reasoning_tokens=75)
        return RespuestaLLM(content="respuesta", reasoning_content="x" * 90, uso=uso), 0.4

    prov = ProveedorFalso(reloj, completar_fn=fn)
    sonda = SondaRendimiento(prov, config=CFG_RAPIDA, reloj=reloj)

    res = await sonda.sobrecarga_razonamiento()

    assert res.ok and res.capability is Capability.REASONING
    celda = res.detalle["celdas"]["24tok|low"]
    assert celda["ratio_tokens_mediana"] == pytest.approx(3.0)  # 75 pensados / 25 visibles
    assert celda["ratio_caracteres_mediana"] == pytest.approx(10.0)
    assert celda["respuestas_sin_contenido"] == 0
    assert res.valor == pytest.approx(3.0)
    assert set(res.detalle["celdas"]) == {
        "24tok|low",
        "24tok|high",
        "200tok|low",
        "200tok|high",
    }


async def test_sobrecarga_no_estima_tokens_que_el_proveedor_no_reporta() -> None:
    """Sin `reasoning_tokens` el ratio en tokens es `None`, no una estimación."""
    reloj = RelojFalso()

    def fn(indice: int, peticion: PeticionPerf) -> tuple[RespuestaLLM, float]:
        uso = UsoLLM(prompt_tokens=30, completion_tokens=100, reasoning_tokens=None)
        return RespuestaLLM(content="", reasoning_content="x" * 90, uso=uso), 0.4

    prov = ProveedorFalso(reloj, completar_fn=fn)
    sonda = SondaRendimiento(prov, config=CFG_RAPIDA, reloj=reloj)

    res = await sonda.sobrecarga_razonamiento()

    celda = res.detalle["celdas"]["24tok|low"]
    assert celda["ratio_tokens_mediana"] is None
    assert celda["ratio_caracteres_mediana"] is None
    assert celda["respuestas_sin_contenido"] == CFG_RAPIDA.muestras_razonamiento
    assert res.detalle["ratio_en_tokens_disponible"] is False
    assert res.valor is None


# --------------------------------------------------------------------------- #
# Visión
# --------------------------------------------------------------------------- #


async def test_vision_sin_soporte_del_adaptador_no_es_vision_falsa() -> None:
    reloj = RelojFalso()
    prov = ProveedorFalso(reloj, soporta_imagenes=False)
    sonda = SondaRendimiento(prov, config=CFG_RAPIDA, reloj=reloj)

    res = await sonda.vision()

    assert res.ok is False
    assert res.valor is None
    assert res.detalle.get("vision") is None
    assert res.error is not None and "no soporta imágenes" in res.error
    assert prov.peticiones == []


async def test_vision_capacidad_no_soportada_en_caliente() -> None:
    reloj = RelojFalso()

    def fn(indice: int, peticion: PeticionPerf) -> tuple[RespuestaLLM, float]:
        raise CapacidadNoSoportada("este endpoint no acepta bloques de imagen")

    prov = ProveedorFalso(reloj, completar_fn=fn)
    sonda = SondaRendimiento(prov, config=CFG_RAPIDA, reloj=reloj)

    res = await sonda.vision()

    assert res.ok is False
    assert res.detalle["motivo"] == "adaptador_sin_soporte_de_imagen"


async def test_vision_acierta_cuando_el_modelo_lee_el_codigo() -> None:
    reloj = RelojFalso()

    def fn(indice: int, peticion: PeticionPerf) -> tuple[RespuestaLLM, float]:
        assert peticion.imagen_png is not None
        assert peticion.imagen_png[:8] == b"\x89PNG\r\n\x1a\n"
        # El doble "ve" porque conoce la semilla; lo que se prueba es el
        # cableado y el criterio de aceptación, no la vista del modelo.
        return RespuestaLLM(content=f" {codigo_vision(4_100 + indice)} ", uso=UsoLLM()), 0.6

    prov = ProveedorFalso(reloj, completar_fn=fn)
    sonda = SondaRendimiento(prov, config=CFG_RAPIDA, reloj=reloj)

    res = await sonda.vision()

    assert res.ok and res.detalle["vision"] is True
    assert res.reliability is not None
    assert res.reliability.successes == CFG_RAPIDA.muestras_vision


async def test_vision_falla_si_el_modelo_alucina_el_codigo() -> None:
    reloj = RelojFalso()

    def fn(indice: int, peticion: PeticionPerf) -> tuple[RespuestaLLM, float]:
        return RespuestaLLM(content="AAAAA", uso=UsoLLM()), 0.6

    prov = ProveedorFalso(reloj, completar_fn=fn)
    sonda = SondaRendimiento(prov, config=CFG_RAPIDA, reloj=reloj)

    res = await sonda.vision()

    assert res.ok is True  # la sonda sí pudo medir
    assert res.detalle["vision"] is False  # el modelo no leyó
    assert res.detalle["leyo_todas"] is False
    assert res.reliability is not None and res.reliability.successes == 0


async def test_vision_parcial_es_ve_pero_no_siempre() -> None:
    """Leer una de dos ya demuestra que ve; la fiabilidad lo matiza."""
    reloj = RelojFalso()

    def fn(indice: int, peticion: PeticionPerf) -> tuple[RespuestaLLM, float]:
        texto = codigo_vision(4_100) if indice == 0 else "AAAAA"
        return RespuestaLLM(content=texto, uso=UsoLLM()), 0.6

    sonda = SondaRendimiento(ProveedorFalso(reloj, completar_fn=fn), config=CFG_RAPIDA, reloj=reloj)

    res = await sonda.vision()

    assert res.detalle["vision"] is True
    assert res.detalle["leyo_todas"] is False
    assert res.detalle["modelcard"] == {"vision": True}


# --------------------------------------------------------------------------- #
# Presupuesto y evidencia end-to-end
# --------------------------------------------------------------------------- #


async def test_max_usd_corta_la_sonda_y_lo_deja_escrito() -> None:
    reloj = RelojFalso()
    prov = ProveedorFalso(
        reloj,
        guion=[(0.1, TipoEvento.CONTENT, "hola")],
        uso=UsoLLM(prompt_tokens=200_000, completion_tokens=50_000),
    )
    sonda = SondaRendimiento(prov, config=CFG_RAPIDA, reloj=reloj, max_usd=0.25)

    res = await sonda.ttft()

    assert res.detalle["presupuesto_agotado"] is True
    assert sonda.presupuesto.gastado_usd <= 0.25 + coste_usd(prov.uso)
    assert len(prov.peticiones) < 4 * CFG_RAPIDA.muestras_ttft


async def test_evidencia_se_escribe_y_no_lleva_el_prompt_crudo(tmp_path: Any) -> None:
    reloj = RelojFalso()
    prov = ProveedorFalso(reloj)
    sonda = SondaRendimiento(prov, config=CFG_RAPIDA, reloj=reloj, dir_evidencia=tmp_path)

    res = await sonda.ttft()

    assert res.evidencia == [str(tmp_path / "perf.ttft.jsonl")]
    lineas = (tmp_path / "perf.ttft.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lineas) == 4 * CFG_RAPIDA.muestras_ttft
    fila = json.loads(lineas[0])
    assert fila["modo"] == "stream"
    assert set(fila) >= {"prompt_huella", "prompt_chars", "ttft_s", "ttfc_s", "uso", "coste_usd"}
    assert "prompt" not in fila  # 52k tokens por fila harían el archivo inservible
    assert len(fila["prompt_huella"]) == 16


async def test_ejecutar_todo_devuelve_una_sonda_por_capacidad() -> None:
    reloj = RelojFalso()

    def fn(indice: int, peticion: PeticionPerf) -> tuple[RespuestaLLM, float]:
        uso = UsoLLM(prompt_tokens=100, completion_tokens=60, reasoning_tokens=20)
        return RespuestaLLM(content=informe_valido(indice), uso=uso), 0.3

    prov = ProveedorFalso(reloj, completar_fn=fn)
    sonda = SondaRendimiento(prov, config=CFG_RAPIDA, reloj=reloj)

    resultados = await sonda.ejecutar_todo()

    assert [r.probe for r in resultados] == [
        "perf.structured_output",
        "perf.vision",
        "perf.reasoning_overhead",
        "perf.throughput",
        "perf.prefix_cache",
        "perf.ttft",
    ]
    assert all(r.duracion_s >= 0 for r in resultados)


async def test_ninguna_sonda_abre_una_conexion_de_red(monkeypatch: Any) -> None:
    """Guardarraíl del gasto: la suite jamás debe tocar la API de pago."""
    import httpx

    def prohibido(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("una sonda intentó salir a la red durante los tests")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", prohibido)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", prohibido)

    reloj = RelojFalso()
    sonda = SondaRendimiento(ProveedorFalso(reloj), config=CFG_RAPIDA, reloj=reloj)

    resultados = await sonda.ejecutar_todo()

    assert all(r.error is None or "red" not in r.error for r in resultados)


# --------------------------------------------------------------------------- #
# Puente con `LLMProvider`
# --------------------------------------------------------------------------- #


class ProviderComun:
    """Doble con la forma de `edecan_llm.base.LLMProvider`, señales extra incluidas."""

    name = "doble"
    model = "@cf/moonshotai/kimi-k2.7-code"

    def __init__(self, reloj: RelojFalso | None = None) -> None:
        self.peticiones: list[CompletionRequest] = []
        self.reloj = reloj

    def _tic(self, dt: float) -> None:
        if self.reloj is not None:
            self.reloj.avanzar(dt)

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        self.peticiones.append(req)
        self._tic(0.5)
        respuesta = CompletionResponse(
            text="ALFA",
            usage=Usage(input_tokens=1_000, output_tokens=120),
            stop_reason="end",
        )
        # Las señales que el contrato común no modela llegan como atributos
        # sueltos, igual que en `ProbeCompletionResponse`.
        object.__setattr__(respuesta, "reasoning_content", "pensando")
        object.__setattr__(respuesta, "cached_tokens", 800)
        object.__setattr__(respuesta, "reasoning_tokens", 90)
        object.__setattr__(respuesta, "neurons", 42.5)
        return respuesta

    async def stream(self, req: CompletionRequest) -> AsyncIterator[StreamChunk]:
        self.peticiones.append(req)
        razonando = StreamChunk(type="text", text=None)
        object.__setattr__(razonando, "reasoning_text", "hmm")
        self._tic(0.2)
        yield razonando
        self._tic(0.3)
        yield StreamChunk(type="text", text="ALFA")
        final = StreamChunk(type="usage", usage=Usage(input_tokens=1_000, output_tokens=120))
        object.__setattr__(final, "cached_tokens", 800)
        yield final


async def test_puente_traduce_peticion_y_recupera_las_senales_extra() -> None:
    prov = ProviderComun()
    puente = PuenteLLMProvider(prov)

    assert puente.modelo == "@cf/moonshotai/kimi-k2.7-code"
    respuesta = await puente.completar(
        PeticionPerf(prompt="hola", max_tokens=256, reasoning_effort="high")
    )

    assert respuesta.content == "ALFA"
    assert respuesta.reasoning_content == "pensando"
    assert respuesta.uso.prompt_tokens == 1_000
    assert respuesta.uso.cached_tokens == 800
    assert respuesta.uso.reasoning_tokens == 90
    assert respuesta.uso.neurons == 42.5
    assert prov.peticiones[0].metadata["reasoning_effort"] == "high"


async def test_puente_manda_el_esquema_por_response_format() -> None:
    prov = ProviderComun()
    puente = PuenteLLMProvider(prov)

    await puente.completar(PeticionPerf(prompt="x", esquema_json={"type": "object"}))

    extra = prov.peticiones[0].metadata["extra_body"]["response_format"]
    assert extra["type"] == "json_schema"
    assert extra["json_schema"]["strict"] is True
    assert extra["json_schema"]["schema"] == {"type": "object"}


async def test_puente_adjunta_la_imagen_como_bloque_base64() -> None:
    prov = ProviderComun()
    puente = PuenteLLMProvider(prov)
    png = png_con_texto("KX37A", escala=2, margen=1)

    await puente.completar(PeticionPerf(prompt="lee esto", imagen_png=png))

    bloques = prov.peticiones[0].messages[0].content
    assert isinstance(bloques, list)
    assert bloques[0]["type"] == "text"
    assert image_source(bloques[1]) == ("image/png", b64encode(png).decode("ascii"))


async def test_puente_rechaza_la_imagen_si_se_declaro_sin_soporte() -> None:
    puente = PuenteLLMProvider(ProviderComun(), soporta_imagenes=False)
    with pytest.raises(CapacidadNoSoportada):
        await puente.completar(PeticionPerf(prompt="x", imagen_png=b"\x89PNG"))


async def test_puente_separa_razonamiento_y_contenido_en_el_stream() -> None:
    puente = PuenteLLMProvider(ProviderComun())

    eventos = [e async for e in puente.transmitir(PeticionPerf(prompt="hola"))]

    assert [e.tipo for e in eventos] == [
        TipoEvento.REASONING,
        TipoEvento.CONTENT,
        TipoEvento.USAGE,
    ]
    assert eventos[0].texto == "hmm"
    assert eventos[1].texto == "ALFA"
    assert eventos[2].uso is not None and eventos[2].uso.cached_tokens == 800


async def test_puente_deja_none_lo_que_el_proveedor_no_reporta() -> None:
    """Un proveedor sin señales extra no produce ceros inventados."""

    class Pelado(ProviderComun):
        async def complete(self, req: CompletionRequest) -> CompletionResponse:
            self.peticiones.append(req)
            return CompletionResponse(
                text="hola", usage=Usage(input_tokens=5, output_tokens=7), stop_reason="end"
            )

    respuesta = await PuenteLLMProvider(Pelado()).completar(PeticionPerf(prompt="x"))

    assert respuesta.reasoning_content == ""
    assert respuesta.uso.reasoning_tokens is None
    assert respuesta.uso.neurons is None
    assert respuesta.uso.cached_tokens == 0


async def test_sonda_completa_a_traves_del_puente_sin_red(respx_mock: Any) -> None:
    """La sonda entera corre contra el puente; respx deja constancia de cero rutas."""
    reloj = RelojFalso()
    sonda = SondaRendimiento(
        PuenteLLMProvider(ProviderComun(reloj)), config=CFG_RAPIDA, reloj=reloj
    )

    resultados = await sonda.ejecutar_todo()

    assert [r.probe for r in resultados][0] == "perf.structured_output"
    assert [r.probe for r in resultados if not r.ok] == []
    assert respx_mock.calls.call_count == 0


async def test_las_mediciones_llegan_a_la_modelcard() -> None:
    """Sin esto la sonda mediría de balde: el runner no adivina qué campo toca."""
    from edecan_forge_probe.runner import componer_modelcard

    reloj = RelojFalso()
    imagenes = 0

    def fn(indice: int, peticion: PeticionPerf) -> tuple[RespuestaLLM, float]:
        nonlocal imagenes
        if peticion.imagen_png is not None:
            contenido = codigo_vision(4_100 + imagenes)
            imagenes += 1
            return RespuestaLLM(content=contenido, uso=UsoLLM(prompt_tokens=60)), 0.5
        # La segunda llamada de cada ronda de cache llega caliente: menos
        # latencia y `cached_tokens` cubriendo el prefijo.
        if "Pregunta B" in peticion.prompt:
            uso = UsoLLM(prompt_tokens=200, completion_tokens=30, cached_tokens=180)
            return RespuestaLLM(content="BRAVO", uso=uso), 0.4
        if "Pregunta A" in peticion.prompt:
            uso = UsoLLM(prompt_tokens=200, completion_tokens=30)
            return RespuestaLLM(content="ALFA", uso=uso), 1.0
        uso = UsoLLM(prompt_tokens=200, completion_tokens=40, reasoning_tokens=10)
        return RespuestaLLM(content=informe_valido(indice), uso=uso), 0.4

    prov = ProveedorFalso(
        reloj,
        completar_fn=fn,
        guion=[
            (0.2, TipoEvento.REASONING, "pensando"),
            (0.3, TipoEvento.CONTENT, "texto"),
        ],
        uso=UsoLLM(prompt_tokens=100, completion_tokens=901, reasoning_tokens=100),
    )
    sonda = SondaRendimiento(prov, config=CFG_RAPIDA, reloj=reloj)

    card = componer_modelcard(
        await sonda.ejecutar_todo(),
        modelo="@cf/moonshotai/kimi-k2.7-code",
        proveedor="falso",
        revision_sonda="test",
    )

    assert card.ttft is not None and card.ttft.p50 == pytest.approx(0.2)
    # (901-1) tokens en la ventana sostenida de 0,3 s (de 0,2 s a 0,5 s).
    assert card.throughput_tps == pytest.approx(3000.0)
    assert card.prefix_cache is True
    assert card.vision is True
    assert card.structured_output is not None
    assert card.structured_output.successes == CFG_RAPIDA.muestras_estructurado
    assert card.notas == []  # ninguna sonda se pisó con otra
