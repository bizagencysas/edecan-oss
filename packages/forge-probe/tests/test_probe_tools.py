"""Pruebas de la sonda de tool-calling.

Ninguna prueba abre red: el proveedor es un doble guionado que reproduce cada
modo de fallo real observado en tool-calling. Lo que se verifica aquí no es que
la sonda "funcione", sino que **clasifica bien**: una sonda que cuenta un JSON
roto como texto alterado produce una `ModelCard` que miente, y sobre esa card se
decide si el diseño de la fase 1 se sostiene.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
import respx
from edecan_forge_probe.modelcard import ArgProfile, Capability
from edecan_forge_probe.probes.tools import (
    CasoTool,
    LlamadaCruda,
    ModoFallo,
    Precios,
    RespuestaSonda,
    SondaToolCalling,
    casos_de_perfil,
    casos_de_schema,
    casos_de_seleccion,
    clasificar,
    n_minimo_para,
    techo_lower_95,
    texto_largo,
)
from edecan_llm.base import ToolSpec

# --------------------------------------------------------------------------- #
# Dobles
# --------------------------------------------------------------------------- #

Guion = Callable[[int, CasoTool], RespuestaSonda]


def respuesta_perfecta(caso: CasoTool, **usage: int) -> RespuestaSonda:
    """La respuesta que un modelo impecable devolvería para este caso."""
    return RespuestaSonda(
        llamadas=[
            LlamadaCruda(
                nombre=caso.esperada,
                argumentos_json=json.dumps(caso.argumentos_esperados, ensure_ascii=False),
            )
        ],
        tokens_entrada=usage.get("tokens_entrada", 1_000),
        tokens_salida=usage.get("tokens_salida", 200),
        tokens_cacheados=usage.get("tokens_cacheados", 800),
        tokens_razonamiento=usage.get("tokens_razonamiento", 120),
        neuronas=usage.get("neuronas", 5_000),
    )


class ProveedorGuionado:
    """Proveedor falso: devuelve lo que diga el guion para el caso i-ésimo.

    Verifica de paso que la sonda le pasa el caso que corresponde (mismo prompt y
    mismas herramientas), porque un guion que responde a otra pregunta mediría
    una fantasía.
    """

    def __init__(self, casos: Sequence[CasoTool], guion: Guion) -> None:
        self.casos = list(casos)
        self.guion = guion
        self.llamadas = 0
        self.max_tokens_vistos: list[int] = []
        self.prompts: list[str] = []

    async def invocar(
        self,
        *,
        sistema: str,
        prompt: str,
        herramientas: Sequence[ToolSpec],
        max_tokens: int,
    ) -> RespuestaSonda:
        assert sistema
        caso = self.casos[self.llamadas]
        assert prompt == caso.prompt
        assert [h.name for h in herramientas] == [h.name for h in caso.herramientas]
        self.llamadas += 1
        self.max_tokens_vistos.append(max_tokens)
        self.prompts.append(prompt)
        return self.guion(self.llamadas - 1, caso)


class ProveedorQueRevienta:
    """Simula un fallo de transporte: nunca llega a haber respuesta del modelo."""

    def __init__(self) -> None:
        self.llamadas = 0

    async def invocar(self, **_kwargs: Any) -> RespuestaSonda:
        self.llamadas += 1
        raise TimeoutError("el borde cerró la conexión")


def sonda(
    proveedor: Any,
    tmp_path: Path,
    **kwargs: Any,
) -> SondaToolCalling:
    kwargs.setdefault("perfiles", (ArgProfile.SCALAR,))
    kwargs.setdefault("escalones_herramientas", ())
    kwargs.setdefault("escalones_schema", ())
    kwargs.setdefault("max_usd", 10.0)
    kwargs.setdefault("intentos_por_perfil", 20)
    return SondaToolCalling(proveedor, dir_evidencia=tmp_path / "evidencia", **kwargs)


def lee_evidencia(ruta: str) -> list[dict[str, Any]]:
    return [json.loads(linea) for linea in Path(ruta).read_text("utf-8").splitlines()]


# --------------------------------------------------------------------------- #
# Los casos son lo que dicen ser
# --------------------------------------------------------------------------- #


def test_code_blob_lleva_codigo_real_hostil() -> None:
    """Un `code_blob` sin comillas, llaves, barras ni acentos no mide nada."""
    extensiones: set[str] = set()
    for caso in casos_de_perfil(ArgProfile.CODE_BLOB, 4):
        assert caso.argumentos_esperados is not None
        extensiones.add(caso.argumentos_esperados["path"].rsplit(".", 1)[1])
        viejo = caso.argumentos_esperados["old_text"]
        nuevo = caso.argumentos_esperados["new_text"]
        for bloque in (viejo, nuevo):
            assert 10 <= len(bloque.splitlines()) <= 60
            assert '"' in bloque
            assert "\\" in bloque
            assert "{" in bloque and "}" in bloque
            assert any(c in bloque for c in "áéíóúñ«»")
    assert extensiones == {"py", "ts"}, "el perfil cubre Python y TypeScript"


def test_long_string_supera_2_kib() -> None:
    assert len(texto_largo(0).encode("utf-8")) > 2048
    caso = casos_de_perfil(ArgProfile.LONG_STRING, 1)[0]
    assert caso.argumentos_esperados is not None
    assert len(caso.argumentos_esperados["content"].encode("utf-8")) > 2048


def test_presupuesto_de_salida_reserva_razonamiento() -> None:
    """El razonamiento se factura como salida: pedir `max_tokens` justos es un bug."""
    for perfil in ArgProfile:
        caso = casos_de_perfil(perfil, 1)[0]
        assert caso.max_tokens >= 2_048


def test_exige_veinte_intentos_por_perfil(tmp_path: Path) -> None:
    vacio = ProveedorGuionado([], lambda i, c: RespuestaSonda())
    with pytest.raises(ValueError, match="N >= 20"):
        sonda(vacio, tmp_path, intentos_por_perfil=19)


def test_wilson_impone_un_suelo_de_muestras() -> None:
    """Con pocas muestras, un 0,90 sobre `lower_95` es inalcanzable aun sin fallos."""
    assert techo_lower_95(12) == pytest.approx(0.7576, abs=1e-4)
    assert techo_lower_95(20) == pytest.approx(0.8389, abs=1e-4)
    assert n_minimo_para(0.90) == 35
    assert techo_lower_95(35) >= 0.90
    assert techo_lower_95(34) < 0.90


def test_rechaza_escalones_con_umbral_inalcanzable(tmp_path: Path) -> None:
    """Medir un techo con un criterio imposible daría siempre el mismo veredicto."""
    with pytest.raises(ValueError, match="inalcanzable"):
        sonda(
            ProveedorGuionado([], lambda i, c: RespuestaSonda()),
            tmp_path,
            intentos_por_escalon=12,
        )


def test_los_defaults_alcanzan_el_umbral_del_contrato() -> None:
    """40 y no 20: con 20 intentos perfectos, `code_blob.lower_95 >= 0.90` no se cumple."""
    firma = inspect.signature(SondaToolCalling.__init__)
    assert firma.parameters["intentos_por_perfil"].default == 40
    assert firma.parameters["intentos_por_escalon"].default == 40
    assert techo_lower_95(40) >= 0.90
    # `max_usd` no tiene valor por defecto a propósito: gasta dinero real.
    assert firma.parameters["max_usd"].default is inspect.Parameter.empty


# --------------------------------------------------------------------------- #
# Clasificación: un modo de fallo por prueba
# --------------------------------------------------------------------------- #


def _clasifica(caso: CasoTool, respuesta: RespuestaSonda) -> Any:
    return clasificar(
        respuesta=respuesta,
        herramientas=caso.herramientas,
        esperada=caso.esperada,
        argumentos_esperados=caso.argumentos_esperados,
    )


def test_clasifica_exito() -> None:
    caso = casos_de_perfil(ArgProfile.CODE_BLOB, 1)[0]
    veredicto = _clasifica(caso, respuesta_perfecta(caso))
    assert veredicto.ok
    assert veredicto.modo is None


def test_clasifica_no_llamo() -> None:
    caso = casos_de_perfil(ArgProfile.SCALAR, 1)[0]
    veredicto = _clasifica(caso, RespuestaSonda(contenido="Claro, ahora mismo leo el archivo."))
    assert veredicto.modo is ModoFallo.NO_LLAMO


def test_clasifica_json_invalido() -> None:
    caso = casos_de_perfil(ArgProfile.SCALAR, 1)[0]
    respuesta = RespuestaSonda(
        llamadas=[LlamadaCruda(nombre="read_file", argumentos_json='{"path": "a.py", "offset":')]
    )
    assert _clasifica(caso, respuesta).modo is ModoFallo.JSON_INVALIDO


def test_clasifica_json_que_no_es_objeto() -> None:
    caso = casos_de_perfil(ArgProfile.SCALAR, 1)[0]
    respuesta = RespuestaSonda(
        llamadas=[LlamadaCruda(nombre="read_file", argumentos_json='["apps/api/main.py"]')]
    )
    assert _clasifica(caso, respuesta).modo is ModoFallo.JSON_INVALIDO


def test_clasifica_campo_faltante() -> None:
    caso = casos_de_perfil(ArgProfile.SCALAR, 1)[0]
    args = dict(caso.argumentos_esperados or {})
    args.pop("limit")
    respuesta = RespuestaSonda(
        llamadas=[LlamadaCruda(nombre="read_file", argumentos_json=json.dumps(args))]
    )
    veredicto = _clasifica(caso, respuesta)
    assert veredicto.modo is ModoFallo.CAMPO_FALTANTE
    assert veredicto.campos_faltantes == ("limit",)


def test_clasifica_texto_alterado_por_un_solo_byte() -> None:
    """El caso que rompe `apply_patch` en silencio: JSON impecable, tilde perdida."""
    caso = casos_de_perfil(ArgProfile.CODE_BLOB, 1)[0]
    args = dict(caso.argumentos_esperados or {})
    args["old_text"] = args["old_text"].replace("vacía", "vacia")
    argumentos_json = json.dumps(args, ensure_ascii=False)
    json.loads(argumentos_json)  # el JSON es válido: el fallo NO es de formato
    llamada = LlamadaCruda(nombre="apply_patch", argumentos_json=argumentos_json)
    veredicto = _clasifica(caso, RespuestaSonda(llamadas=[llamada]))
    assert veredicto.modo is ModoFallo.TEXTO_ALTERADO
    assert veredicto.campos_alterados == ("old_text",)


def test_clasifica_texto_alterado_por_reescape_de_barra() -> None:
    caso = casos_de_perfil(ArgProfile.CODE_BLOB, 1)[0]
    args = dict(caso.argumentos_esperados or {})
    args["new_text"] = args["new_text"].replace("\\\\", "\\")
    respuesta = RespuestaSonda(
        llamadas=[
            LlamadaCruda(nombre="apply_patch", argumentos_json=json.dumps(args, ensure_ascii=False))
        ]
    )
    assert _clasifica(caso, respuesta).modo is ModoFallo.TEXTO_ALTERADO


def test_clasifica_herramienta_inexistente() -> None:
    caso = casos_de_perfil(ArgProfile.CODE_BLOB, 1)[0]
    respuesta = RespuestaSonda(
        llamadas=[LlamadaCruda(nombre="apply_patch_v2", argumentos_json="{}")]
    )
    veredicto = _clasifica(caso, respuesta)
    assert veredicto.modo is ModoFallo.HERRAMIENTA_INEXISTENTE
    assert veredicto.herramienta_alucinada is True


def test_clasifica_herramienta_equivocada_pero_existente() -> None:
    caso = casos_de_seleccion(12, 1)[0]
    otra = next(h.name for h in caso.herramientas if h.name != caso.esperada)
    respuesta = RespuestaSonda(llamadas=[LlamadaCruda(nombre=otra, argumentos_json="{}")])
    veredicto = _clasifica(caso, respuesta)
    assert veredicto.modo is ModoFallo.HERRAMIENTA_EQUIVOCADA
    assert veredicto.herramienta_alucinada is False


def test_clasifica_argumento_inventado() -> None:
    caso = casos_de_perfil(ArgProfile.SCALAR, 1)[0]
    args = dict(caso.argumentos_esperados or {})
    args["motivo"] = "porque me pareció razonable"
    respuesta = RespuestaSonda(
        llamadas=[
            LlamadaCruda(nombre="read_file", argumentos_json=json.dumps(args, ensure_ascii=False))
        ]
    )
    veredicto = _clasifica(caso, respuesta)
    assert veredicto.modo is ModoFallo.ARGUMENTO_INVENTADO
    assert veredicto.campos_inventados == ("motivo",)


def test_una_llamada_correcta_entre_ruido_cuenta_como_exito() -> None:
    caso = casos_de_perfil(ArgProfile.SCALAR, 1)[0]
    buena = respuesta_perfecta(caso).llamadas[0]
    respuesta = RespuestaSonda(
        llamadas=[LlamadaCruda(nombre="list_dir", argumentos_json="{}"), buena]
    )
    assert _clasifica(caso, respuesta).ok


def test_perfil_anidado_compara_la_estructura_entera() -> None:
    caso = casos_de_perfil(ArgProfile.NESTED, 1)[0]
    args = json.loads(json.dumps(caso.argumentos_esperados))
    args["plan"]["pasos"][1]["accion"] = "editar"
    respuesta = RespuestaSonda(
        llamadas=[
            LlamadaCruda(nombre="plan_edits", argumentos_json=json.dumps(args, ensure_ascii=False))
        ]
    )
    assert _clasifica(caso, respuesta).modo is ModoFallo.TEXTO_ALTERADO


# --------------------------------------------------------------------------- #
# Series completas
# --------------------------------------------------------------------------- #


async def test_serie_perfecta_sin_tocar_la_red(tmp_path: Path) -> None:
    casos = casos_de_perfil(ArgProfile.SCALAR, 20)
    proveedor = ProveedorGuionado(casos, lambda i, c: respuesta_perfecta(c))
    with respx.mock(assert_all_mocked=True):  # cualquier petición HTTP real explota
        resultados = await sonda(proveedor, tmp_path).ejecutar()

    assert len(resultados) == 1
    resultado = resultados[0]
    assert resultado.probe == "native_tools.scalar"
    assert resultado.capability is Capability.NATIVE_TOOLS
    assert resultado.ok
    assert resultado.reliability is not None
    assert resultado.reliability.successes == 20
    assert resultado.reliability.trials == 20
    assert resultado.reliability.mean == 1.0
    assert resultado.valor == pytest.approx(resultado.reliability.lower_95)
    assert resultado.latencia is not None and resultado.latencia.muestras == 20
    assert resultado.detalle["modos_de_fallo"] == {}
    assert resultado.detalle["tasa_herramienta_alucinada"] == 0.0
    assert resultado.detalle["tasa_argumento_inventado"] == 0.0


async def test_evidencia_cruda_en_disco_es_auditable(tmp_path: Path) -> None:
    casos = casos_de_perfil(ArgProfile.CODE_BLOB, 20)
    proveedor = ProveedorGuionado(casos, lambda i, c: respuesta_perfecta(c))
    resultado = (await sonda(proveedor, tmp_path, perfiles=(ArgProfile.CODE_BLOB,)).ejecutar())[0]

    assert resultado.evidencia, "una medición sin evidencia no entra en la ModelCard"
    filas = lee_evidencia(resultado.evidencia[0])
    assert len(filas) == 20
    primera = filas[0]
    assert primera["caso"] == "code_blob-00"
    assert primera["veredicto"]["ok"] is True
    assert primera["usage"]["tokens_cacheados"] == 800
    assert primera["schema_sha256"] and primera["prompt_sha256"]
    # El texto devuelto se guarda entero: es lo que hay que poder revisar a mano.
    argumentos = json.loads(primera["llamadas"][0]["argumentos_json"])
    assert argumentos["old_text"] == casos[0].argumentos_esperados["old_text"]  # type: ignore[index]


async def test_desglosa_modos_de_fallo_y_tasas(tmp_path: Path) -> None:
    """Cada tercio de la serie falla de una forma distinta y sale desglosado."""
    casos = casos_de_perfil(ArgProfile.SCALAR, 20)

    def guion(i: int, caso: CasoTool) -> RespuestaSonda:
        args = dict(caso.argumentos_esperados or {})
        if i < 4:
            return RespuestaSonda(contenido="lo hago enseguida")
        if i < 8:
            return RespuestaSonda(
                llamadas=[LlamadaCruda(nombre="leer_archivo", argumentos_json="{}")]
            )
        if i < 12:
            return RespuestaSonda(
                llamadas=[LlamadaCruda(nombre="read_file", argumentos_json="{'path': 'a.py'}")]
            )
        if i < 16:
            args["motivo"] = "iniciativa propia"
            return RespuestaSonda(
                llamadas=[
                    LlamadaCruda(
                        nombre="read_file", argumentos_json=json.dumps(args, ensure_ascii=False)
                    )
                ]
            )
        return respuesta_perfecta(caso)

    resultado = (await sonda(ProveedorGuionado(casos, guion), tmp_path).ejecutar())[0]
    assert resultado.detalle["modos_de_fallo"] == {
        "argumento_inventado": 4,
        "herramienta_inexistente": 4,
        "json_invalido": 4,
        "no_llamo": 4,
    }
    assert resultado.detalle["tasa_herramienta_alucinada"] == pytest.approx(0.20)
    assert resultado.detalle["tasa_argumento_inventado"] == pytest.approx(0.20)
    assert resultado.reliability is not None
    assert resultado.reliability.successes == 4
    assert resultado.reliability.lower_95 < 0.90


async def test_mide_la_sobrecarga_de_razonamiento(tmp_path: Path) -> None:
    """El razonamiento se cobra a precio de salida: es una métrica, no un detalle."""
    casos = casos_de_perfil(ArgProfile.SCALAR, 20)
    proveedor = ProveedorGuionado(
        casos,
        lambda i, c: respuesta_perfecta(c, tokens_salida=65, tokens_razonamiento=57),
    )
    resultado = (await sonda(proveedor, tmp_path).ejecutar())[0]
    assert resultado.detalle["tokens_razonamiento"] == 20 * 57
    assert resultado.detalle["sobrecarga_razonamiento"] == pytest.approx(57 / 8)


async def test_error_de_proveedor_no_se_cuenta_como_fallo_del_modelo(tmp_path: Path) -> None:
    proveedor = ProveedorQueRevienta()
    resultado = (await sonda(proveedor, tmp_path).ejecutar())[0]
    assert resultado.ok is False
    assert resultado.reliability is None
    assert resultado.valor is None
    assert resultado.detalle["errores_proveedor"] == 20
    assert resultado.error is not None and "proveedor" in resultado.error
    filas = lee_evidencia(resultado.evidencia[0])
    assert filas[0]["error_proveedor"].startswith("TimeoutError")
    assert filas[0]["veredicto"] is None


# --------------------------------------------------------------------------- #
# Presupuesto
# --------------------------------------------------------------------------- #


async def test_el_presupuesto_corta_y_lo_no_medido_no_aparece(tmp_path: Path) -> None:
    casos = casos_de_perfil(ArgProfile.SCALAR, 20)
    proveedor = ProveedorGuionado(
        casos,
        lambda i, c: respuesta_perfecta(c, tokens_entrada=10_000, tokens_salida=1_000),
    )
    resultados = await sonda(
        proveedor,
        tmp_path,
        max_usd=0.001,
        perfiles=(ArgProfile.SCALAR, ArgProfile.CODE_BLOB),
    ).ejecutar()

    assert proveedor.llamadas == 1, "debe cortar en cuanto el gasto alcanza max_usd"
    assert len(resultados) == 1, "el perfil que no se llegó a medir NO se reporta"
    assert resultados[0].detalle["presupuesto_agotado"] is True
    assert resultados[0].reliability is not None
    assert resultados[0].reliability.trials == 1


def test_precio_de_entrada_cacheada_se_cobra_aparte() -> None:
    precios = Precios()
    caro = precios.coste(entrada=1_000_000, cacheada=0, salida=0)
    barato = precios.coste(entrada=1_000_000, cacheada=1_000_000, salida=0)
    assert caro == pytest.approx(0.95)
    assert barato == pytest.approx(0.19)
    assert precios.coste(entrada=0, cacheada=0, salida=1_000_000) == pytest.approx(4.00)


# --------------------------------------------------------------------------- #
# Techos: herramientas ofrecidas y tamaño de esquema
# --------------------------------------------------------------------------- #


async def test_max_tools_effective_marca_donde_se_derrumba(tmp_path: Path) -> None:
    """Acierta hasta 12 herramientas ofrecidas; a partir de 20 se hunde."""

    def guion(i: int, caso: CasoTool) -> RespuestaSonda:
        if len(caso.herramientas) <= 12:
            return respuesta_perfecta(caso)
        otra = next(h.name for h in caso.herramientas if h.name != caso.esperada)
        return RespuestaSonda(llamadas=[LlamadaCruda(nombre=otra, argumentos_json="{}")])

    casos = [caso for n in (4, 8, 12, 20) for caso in casos_de_seleccion(n, 40)]
    proveedor = ProveedorGuionado(casos, guion)
    resultado = (
        await sonda(
            proveedor,
            tmp_path,
            perfiles=(),
            escalones_herramientas=(4, 8, 12, 20, 32, 48),
            intentos_por_escalon=40,
        ).ejecutar()
    )[0]

    assert resultado.probe == "native_tools.max_tools_effective"
    assert resultado.valor == 12
    assert resultado.detalle["rompio_en"] == 20
    assert resultado.detalle["techo_no_encontrado"] is False
    assert [e["herramientas"] for e in resultado.detalle["escalones"]] == [4, 8, 12, 20]
    assert resultado.detalle["escalones"][-1]["modos_de_fallo"] == {"herramienta_equivocada": 40}
    assert proveedor.llamadas == 160, "no debe seguir gastando en 32 y 48 tras romperse"


async def test_max_tools_effective_vale_cero_si_ya_falla_el_primer_escalon(
    tmp_path: Path,
) -> None:
    """0 es una medición ("ninguna superficie aguanta"); `None` sería no haber medido."""
    casos = casos_de_seleccion(4, 40)
    proveedor = ProveedorGuionado(casos, lambda i, c: RespuestaSonda(contenido="ahora no"))
    resultado = (
        await sonda(
            proveedor,
            tmp_path,
            perfiles=(),
            escalones_herramientas=(4,),
            intentos_por_escalon=40,
        ).ejecutar()
    )[0]
    assert resultado.valor == 0
    assert resultado.detalle["rompio_en"] == 4


async def test_max_schema_bytes_crece_hasta_romperse(tmp_path: Path) -> None:
    limite = 20_000

    def guion(i: int, caso: CasoTool) -> RespuestaSonda:
        bytes_esquema = len(json.dumps(caso.herramientas[0].input_schema).encode("utf-8"))
        if bytes_esquema <= limite:
            return respuesta_perfecta(caso)
        return RespuestaSonda(contenido="el esquema es enorme, ¿qué querías exactamente?")

    casos: list[CasoTool] = []
    for objetivo in (1_024, 4_096, 16_384, 65_536):
        casos.extend(casos_de_schema(objetivo, 40)[0])
    proveedor = ProveedorGuionado(casos, guion)
    resultado = (
        await sonda(
            proveedor,
            tmp_path,
            perfiles=(),
            escalones_schema=(1_024, 4_096, 16_384, 65_536, 262_144),
            intentos_por_escalon=40,
        ).ejecutar()
    )[0]

    assert resultado.probe == "native_tools.max_schema_bytes"
    assert resultado.valor is not None
    assert 16_384 <= resultado.valor <= limite
    assert resultado.detalle["rompio_en"] is not None
    assert resultado.detalle["rompio_en"] > limite
    assert resultado.detalle["escalones"][-1]["modos_de_fallo"] == {"no_llamo": 40}
