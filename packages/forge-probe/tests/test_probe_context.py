"""Pruebas de la sonda de contexto útil.

Ninguna toca la red: el proveedor es un doble determinista que *lee de verdad*
el prompt (parsea las agujas que la sonda insertó) y degrada a partir de una
profundidad configurable. Así lo que se comprueba no es que la sonda devuelva un
número, sino que **detecta la degradación en el punto correcto**.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
from edecan_forge_probe.modelcard import ProbeResult, Reliability
from edecan_forge_probe.probes.context import (
    CHARS_POR_TOKEN,
    INTENTOS_MINIMOS_UMBRAL,
    AdaptadorLLMProvider,
    Caso,
    Contador,
    CorpusRelleno,
    Insercion,
    PeticionSonda,
    Precios,
    RespuestaSonda,
    TipoPrueba,
    _insertar,
    _usa_biblioteca,
    calcular_contexto_util,
    construir_caso_aguja,
    construir_caso_multisalto,
    construir_caso_restriccion,
    estimar_tokens,
    evaluar_respuesta,
    sondar_contexto,
)
from edecan_llm.base import ChatMessage, CompletionRequest, CompletionResponse, Usage

# --------------------------------------------------------------------------- #
# Doble de proveedor
# --------------------------------------------------------------------------- #

_RE_SALA = re.compile(r"sala ([A-Z]+)\?")
_RE_REGLA = re.compile(r"NUNCA uses la biblioteca `(\w+)`\. Usa siempre `(\w+)`")


class ProveedorFalso:
    """Lector perfecto hasta cierta profundidad; a partir de ahí, se rompe.

    `limites` da la profundidad (en tokens estimados) hasta la que cada prueba
    sigue funcionando. Permite simular lo que se observa en modelos reales: el
    multi-salto se cae antes que la aguja simple.
    """

    def __init__(
        self,
        limites: dict[TipoPrueba, int],
        *,
        tolerancia_tokens: int = 600,
        fraccion_cacheada: float = 0.5,
    ) -> None:
        self.limites = limites
        self.tolerancia = tolerancia_tokens
        self.fraccion_cacheada = fraccion_cacheada
        self.llamadas: list[PeticionSonda] = []

    def _prueba(self, texto: str) -> TipoPrueba:
        if "[FORGE-SALTO-A]" in texto:
            return TipoPrueba.MULTI_SALTO
        if "[FORGE-AGUJA]" in texto:
            return TipoPrueba.AGUJA
        return TipoPrueba.RESTRICCION

    def _responder(self, texto: str, prueba: TipoPrueba, degradado: bool) -> str:
        if prueba is TipoPrueba.AGUJA:
            if degradado:
                return "No encuentro ese dato en el material aportado."
            sala = _RE_SALA.search(texto)
            assert sala, "la pregunta de la aguja debe nombrar la sala"
            m = re.search(
                rf"\[FORGE-AGUJA\] El codigo de continuidad de la sala {sala.group(1)} "
                r"es ([A-Z]{2}-\d{4})\.",
                texto,
            )
            assert m, "la aguja debe estar en el prompt"
            return m.group(1)

        if prueba is TipoPrueba.MULTI_SALTO:
            sala = _RE_SALA.search(texto)
            assert sala, "la pregunta del multi-salto debe nombrar la sala"
            a = re.search(
                rf"\[FORGE-SALTO-A\] La sala {sala.group(1)} tiene asignado el turno "
                r"([A-Z]{2}-\d{4})\.",
                texto,
            )
            assert a, "el hecho A debe estar en el prompt"
            correcto = re.search(
                rf"\[FORGE-SALTO-B\] El turno {a.group(1)} lo cubre ([^.\n]+)\.", texto
            )
            assert correcto, "el hecho B debe estar en el prompt"
            if not degradado:
                return correcto.group(1)
            # Degradación realista: se queda con un distractor, no con un "no sé".
            for otro in re.finditer(r"\[FORGE-SALTO-B\] El turno \S+ lo cubre ([^.\n]+)\.", texto):
                if otro.group(1) != correcto.group(1):
                    return otro.group(1)
            return "No he podido determinarlo."

        regla = _RE_REGLA.search(texto)
        assert regla, "la regla debe estar al principio del prompt"
        prohibida, permitida = regla.group(1), regla.group(2)
        if degradado:
            return f"import {prohibida}\n{prohibida}.hacer_algo()"
        return f"import {permitida}\n{permitida}.hacer_algo()  # la otra está prohibida"

    async def completar(self, peticion: PeticionSonda) -> RespuestaSonda:
        self.llamadas.append(peticion)
        texto = peticion.usuario
        prueba = self._prueba(texto)
        tokens = estimar_tokens(texto)
        degradado = tokens > self.limites[prueba] + self.tolerancia
        contenido = self._responder(texto, prueba, degradado)
        prompt_tokens = tokens + estimar_tokens(peticion.system)
        razonamiento = "el usuario pregunta por un dato del material" * 2
        return RespuestaSonda(
            contenido=contenido,
            razonamiento=razonamiento,
            prompt_tokens=prompt_tokens,
            completion_tokens=estimar_tokens(contenido) + estimar_tokens(razonamiento),
            cached_tokens=int(prompt_tokens * self.fraccion_cacheada),
            neurons=float(prompt_tokens) / 100.0,
            latencia_s=0.5 + prompt_tokens / 100_000,
        )


class ProveedorQueRevienta:
    """Falla siempre. Un error de red no es un fallo de memoria del modelo."""

    def __init__(self) -> None:
        self.llamadas = 0

    async def completar(self, peticion: PeticionSonda) -> RespuestaSonda:
        self.llamadas += 1
        raise RuntimeError("522 origin down")


# --------------------------------------------------------------------------- #
# Corpus de relleno de juguete (repo git real, temporal, sin red)
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def repo_juguete(tmp_path_factory: pytest.TempPathFactory) -> Path:
    raiz = tmp_path_factory.mktemp("repo-juguete")
    subprocess.run(["git", "init", "-q", str(raiz)], check=True)
    for i in range(3):
        cuerpo = "\n".join(
            f"def funcion_{i}_{j}(x: int) -> int:\n    # relleno determinista\n    return x + {j}"
            for j in range(1200)
        )
        (raiz / f"modulo_{i}.py").write_text(cuerpo, encoding="utf-8")
    (raiz / "README.md").write_text("no es codigo, no debe entrar\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(raiz), "add", "-A"], check=True)
    return raiz


@pytest.fixture
def corpus(repo_juguete: Path) -> CorpusRelleno:
    return CorpusRelleno(repo_juguete)


PROFUNDIDADES_TEST = (1_000, 4_000, 8_000, 16_000)


async def _sondar(
    proveedor: Any,
    corpus: CorpusRelleno,
    tmp_path: Path,
    **kwargs: Any,
) -> ProbeResult:
    return await sondar_contexto(
        proveedor,
        raiz_repo=corpus._raiz,  # noqa: SLF001 - el corpus ya está construido sobre ella
        corpus=corpus,
        profundidades=kwargs.pop("profundidades", PROFUNDIDADES_TEST),
        intentos=kwargs.pop("intentos", 8),
        max_usd=kwargs.pop("max_usd", 1_000.0),
        dir_evidencia=tmp_path / "evidencia",
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# Corpus
# --------------------------------------------------------------------------- #


def test_corpus_usa_codigo_real_y_es_determinista(corpus: CorpusRelleno) -> None:
    texto = corpus.texto(20_000)
    assert len(texto) == 20_000
    assert "def funcion_" in texto
    assert "# ==== modulo_0.py ====" in texto
    assert "README" not in texto, "el relleno debe ser código, no documentación"
    assert corpus.texto(20_000) == texto


def test_corpus_cicla_cuando_el_repo_no_da_para_la_profundidad(corpus: CorpusRelleno) -> None:
    grande = corpus.texto(600_000)
    assert len(grande) == 600_000
    manifiesto = corpus.manifiesto()
    assert manifiesto["archivos_concatenados"] > manifiesto["archivos_candidatos"]
    assert manifiesto["orden_concatenacion"][:3] == [
        "modulo_0.py",
        "modulo_1.py",
        "modulo_2.py",
    ]
    assert len(manifiesto["sha256_corpus"]) == 64


# --------------------------------------------------------------------------- #
# Construcción de casos
# --------------------------------------------------------------------------- #


def test_insertar_respeta_la_fraccion_pedida() -> None:
    relleno = "\n".join(f"linea {i}" for i in range(1000))
    ins = [Insercion(fraccion=0.1, linea="A"), Insercion(fraccion=0.9, linea="B")]
    texto = _insertar(relleno, ins)
    assert texto.index("\nA\n") < texto.index("\nB\n")
    for i in ins:
        real = texto.index(f"\n{i.linea}\n") / len(texto)
        assert abs(real - i.fraccion) < 0.02


def test_aguja_pregunta_por_lo_que_inserto(corpus: CorpusRelleno) -> None:
    caso = construir_caso_aguja(corpus.texto(4_000), 1_000, 0.5, 0)
    assert caso.esperado["codigo"] in caso.usuario
    assert caso.esperado["sala"] in caso.pregunta
    assert caso.usuario.rstrip().endswith(caso.pregunta)


def test_multisalto_necesita_los_dos_hechos(corpus: CorpusRelleno) -> None:
    caso = construir_caso_multisalto(corpus.texto(8_000), 2_000, (0.1, 0.9), 0)
    assert caso.esperado["turno"] in caso.usuario
    assert caso.esperado["operador"] in caso.usuario
    assert caso.esperado["distractores"], "sin distractores el acierto por azar es trivial"
    # El nombre correcto no aparece junto a la sala: hay que encadenar A y B.
    assert f"La sala {caso.esperado['sala']} tiene asignado el turno" in caso.usuario
    assert caso.esperado["operador"] not in caso.pregunta


def test_restriccion_va_al_principio_y_la_pregunta_invita_a_violarla(
    corpus: CorpusRelleno,
) -> None:
    caso = construir_caso_restriccion(corpus.texto(8_000), 2_000, 0, 0)
    assert caso.usuario.startswith("[FORGE-REGLA")
    assert caso.esperado["prohibida"] in caso.pregunta
    assert caso.esperado["permitida"] not in caso.pregunta


def test_los_casos_varian_entre_intentos(corpus: CorpusRelleno) -> None:
    relleno = corpus.texto(4_000)
    codigos = {construir_caso_aguja(relleno, 1_000, 0.5, i).esperado["codigo"] for i in range(8)}
    assert len(codigos) == 8, "repetir la misma aguja mide esa aguja, no la profundidad"


def test_resumen_de_caso_no_arrastra_el_relleno(corpus: CorpusRelleno) -> None:
    caso = construir_caso_aguja(corpus.texto(40_000), 10_000, 0.5, 0)
    resumen = caso.resumen()
    assert "usuario" not in resumen
    assert resumen["usuario_chars"] > 40_000
    assert len(resumen["usuario_sha256"]) == 64
    json.dumps(resumen)  # tiene que ser serializable tal cual


# --------------------------------------------------------------------------- #
# Veredicto
# --------------------------------------------------------------------------- #


def _caso(prueba: TipoPrueba, esperado: dict[str, Any]) -> Caso:
    return Caso(
        prueba=prueba,
        etiqueta="x",
        profundidad_tokens=1_000,
        usuario="",
        pregunta="",
        inserciones=[],
        esperado=esperado,
    )


def test_aguja_acierta_pese_al_formato() -> None:
    caso = _caso(TipoPrueba.AGUJA, {"codigo": "QX-7731"})
    assert evaluar_respuesta(caso, "El código es **qx 7731**.")[0]
    assert not evaluar_respuesta(caso, "No lo encuentro.")[0]


def test_multisalto_falla_si_cuela_un_distractor() -> None:
    caso = _caso(
        TipoPrueba.MULTI_SALTO,
        {"operador": "Ines Barrera", "distractores": ["Tomas Quiroga"]},
    )
    assert evaluar_respuesta(caso, "Ines Barrera")[0]
    ok, motivo = evaluar_respuesta(caso, "Ines Barrera o quizá Tomas Quiroga")
    assert not ok and "distractores" in motivo
    assert not evaluar_respuesta(caso, "Tomas Quiroga")[0]


def test_restriccion_distingue_usar_de_nombrar() -> None:
    caso = _caso(TipoPrueba.RESTRICCION, {"prohibida": "requests", "permitida": "httpx"})
    assert evaluar_respuesta(caso, "import httpx\nhttpx.get(url)")[0]
    assert evaluar_respuesta(caso, "No puedo usar requests porque lo prohibiste; usa httpx.")[0], (
        "mencionar la prohibida para rechazarla es un acierto"
    )
    assert not evaluar_respuesta(caso, "import requests\nrequests.get(url)")[0]
    assert not evaluar_respuesta(caso, "Aquí tienes un ejemplo.")[0]


def test_usa_biblioteca_no_confunde_subcadenas() -> None:
    assert _usa_biblioteca("import yaml", "yaml")
    assert _usa_biblioteca("from yaml import safe_load", "yaml")
    assert _usa_biblioteca("yaml.safe_load(f)", "yaml")
    assert not _usa_biblioteca("import ruamel.yaml_compat", "yaml")
    assert not _usa_biblioteca("el paquete yaml está vetado", "yaml")


# --------------------------------------------------------------------------- #
# Agregación y umbral
# --------------------------------------------------------------------------- #


def test_contexto_util_es_la_mayor_profundidad_que_pasa() -> None:
    curva = [
        {"profundidad": 4_000, "todas_pasan": True},
        {"profundidad": 16_000, "todas_pasan": True},
        {"profundidad": 48_000, "todas_pasan": False},
    ]
    assert calcular_contexto_util(curva) == 16_000


def test_contexto_util_es_cero_si_falla_hasta_la_profundidad_minima() -> None:
    curva = [{"profundidad": 4_000, "todas_pasan": False}]
    assert calcular_contexto_util(curva) == 0, "medir 'falla ya en 4k' no es 'sin dato'"
    assert calcular_contexto_util([]) is None


def test_veintiun_aciertos_de_veintiuno_no_llegan_al_umbral() -> None:
    """El tamaño de muestra no es decorativo: fija qué se puede afirmar."""
    assert Reliability(successes=21, trials=21).lower_95 < 0.85
    minimo = INTENTOS_MINIMOS_UMBRAL
    assert Reliability(successes=minimo, trials=minimo).lower_95 >= 0.85


# --------------------------------------------------------------------------- #
# La sonda de punta a punta
# --------------------------------------------------------------------------- #


async def test_detecta_la_degradacion_en_el_punto_correcto(
    corpus: CorpusRelleno, tmp_path: Path
) -> None:
    """El doble lee bien hasta 8k y se rompe después: la sonda debe decir 8k."""
    proveedor = ProveedorFalso(dict.fromkeys(TipoPrueba, 8_000))
    res = await _sondar(proveedor, corpus, tmp_path)

    assert res.ok
    assert res.valor == 8_000
    assert res.detalle["usable_context_tokens"] == 8_000
    curva = {p["profundidad"]: p for p in res.detalle["curva"]}
    assert list(curva) == list(PROFUNDIDADES_TEST)
    assert curva[8_000]["todas_pasan"] is True
    assert curva[16_000]["todas_pasan"] is False
    for prueba in TipoPrueba:
        assert curva[16_000]["pruebas"][prueba.value]["exitos"] == 0


async def test_el_multisalto_marca_el_techo_aunque_la_aguja_aguante(
    corpus: CorpusRelleno, tmp_path: Path
) -> None:
    """El caso realista: la recuperación literal sobrevive y el salto no.

    Sin la prueba de multi-salto se declararían 16k de contexto útil cuando el
    modelo ya no puede encadenar dos hechos a 8k. Ese es justo el error que la
    fase 0 existe para no cometer.
    """
    proveedor = ProveedorFalso(
        {
            TipoPrueba.AGUJA: 16_000,
            TipoPrueba.RESTRICCION: 16_000,
            TipoPrueba.MULTI_SALTO: 4_000,
        }
    )
    res = await _sondar(proveedor, corpus, tmp_path)

    assert res.valor == 4_000
    curva = {p["profundidad"]: p for p in res.detalle["curva"]}
    assert curva[8_000]["pruebas"][TipoPrueba.AGUJA.value]["pasa"] is True
    assert curva[8_000]["pruebas"][TipoPrueba.MULTI_SALTO.value]["pasa"] is False
    assert curva[8_000]["todas_pasan"] is False


async def test_la_restriccion_puede_ser_la_que_falla(corpus: CorpusRelleno, tmp_path: Path) -> None:
    proveedor = ProveedorFalso(
        {
            TipoPrueba.AGUJA: 16_000,
            TipoPrueba.MULTI_SALTO: 16_000,
            TipoPrueba.RESTRICCION: 4_000,
        }
    )
    res = await _sondar(proveedor, corpus, tmp_path)
    assert res.valor == 4_000
    curva = {p["profundidad"]: p for p in res.detalle["curva"]}
    assert curva[8_000]["pruebas"][TipoPrueba.RESTRICCION.value]["exitos"] == 0


async def test_cada_configuracion_recibe_al_menos_cinco_intentos(
    corpus: CorpusRelleno, tmp_path: Path
) -> None:
    proveedor = ProveedorFalso(dict.fromkeys(TipoPrueba, 100_000))
    res = await _sondar(proveedor, corpus, tmp_path, profundidades=(1_000,))
    por_config = res.detalle["curva"][0]["por_configuracion"]
    assert len(por_config) == 9, "3 posiciones + 3 pares + 3 restricciones"
    assert all(v["intentos"] >= 5 for v in por_config.values())
    assert all(
        res.detalle["curva"][0]["pruebas"][t.value]["intentos"] >= INTENTOS_MINIMOS_UMBRAL
        for t in TipoPrueba
    )


async def test_para_al_agotar_el_presupuesto_y_lo_deja_por_escrito(
    corpus: CorpusRelleno, tmp_path: Path
) -> None:
    proveedor = ProveedorFalso(dict.fromkeys(TipoPrueba, 100_000))
    res = await _sondar(proveedor, corpus, tmp_path, max_usd=0.02)

    assert res.detalle["truncado_por_presupuesto"] is True
    assert res.detalle["es_cota_inferior"] is True
    assert res.detalle["coste_usd"] <= 0.02
    assert res.detalle["profundidad_maxima_medida"] < max(PROFUNDIDADES_TEST)
    assert res.detalle["profundidades_medidas"] != list(PROFUNDIDADES_TEST)


async def test_presupuesto_cero_no_hace_ni_una_llamada(
    corpus: CorpusRelleno, tmp_path: Path
) -> None:
    proveedor = ProveedorFalso(dict.fromkeys(TipoPrueba, 100_000))
    res = await _sondar(proveedor, corpus, tmp_path, max_usd=0.0)
    assert proveedor.llamadas == []
    assert res.ok is False
    assert res.valor is None
    assert res.error


async def test_los_errores_no_se_cuentan_como_fallos_de_memoria(
    corpus: CorpusRelleno, tmp_path: Path
) -> None:
    res = await _sondar(
        ProveedorQueRevienta(), corpus, tmp_path, profundidades=(1_000,), intentos=2
    )
    assert res.ok is False
    assert res.valor is None, "no se midió nada: no se puede afirmar contexto útil"
    assert res.detalle["curva"] == []
    assert len(res.detalle["errores"]) == 18
    assert "522 origin down" in res.detalle["errores"][0]["error"]


async def test_registra_cache_razonamiento_y_neuronas(
    corpus: CorpusRelleno, tmp_path: Path
) -> None:
    proveedor = ProveedorFalso(dict.fromkeys(TipoPrueba, 100_000), fraccion_cacheada=0.5)
    res = await _sondar(proveedor, corpus, tmp_path, profundidades=(1_000,), intentos=2)

    assert res.detalle["tokens"]["fraccion_cacheada"] == pytest.approx(0.5, abs=0.01)
    assert res.detalle["neurons"] > 0
    assert res.detalle["razonamiento"]["chars_razonamiento"] > 0
    assert res.detalle["razonamiento"]["ratio_razonamiento_contenido"] > 0
    assert res.latencia is not None
    assert res.latencia.muestras == 18
    assert res.latencia.p95 >= res.latencia.p50


async def test_deja_evidencia_auditable(corpus: CorpusRelleno, tmp_path: Path) -> None:
    proveedor = ProveedorFalso(dict.fromkeys(TipoPrueba, 100_000))
    res = await _sondar(proveedor, corpus, tmp_path, profundidades=(1_000,), intentos=2)

    assert res.evidencia
    manifiesto = json.loads(Path(res.evidencia[0]).read_text(encoding="utf-8"))
    assert manifiesto["corpus"]["orden_concatenacion"]
    assert manifiesto["config"]["chars_por_token"] == CHARS_POR_TOKEN

    indice = json.loads(Path(res.evidencia[1]).read_text(encoding="utf-8"))
    assert len(indice["llamadas"]) == 18

    llamada = json.loads(Path(res.evidencia[2]).read_text(encoding="utf-8"))
    assert llamada["veredicto"]["exito"] is True
    assert llamada["respuesta"]["contenido"]
    assert llamada["peticion"]["esperado"]
    assert "usuario" not in llamada["peticion"], "por defecto no se vuelca el relleno entero"
    # El veredicto tiene que poder recalcularse desde el archivo, sin la sonda.
    assert llamada["peticion"]["esperado"] and llamada["peticion"]["usuario_sha256"]


async def test_puede_volcar_el_prompt_entero_si_se_pide(
    corpus: CorpusRelleno, tmp_path: Path
) -> None:
    proveedor = ProveedorFalso(dict.fromkeys(TipoPrueba, 100_000))
    res = await _sondar(
        proveedor,
        corpus,
        tmp_path,
        profundidades=(1_000,),
        intentos=1,
        guardar_prompt_completo=True,
    )
    llamada = json.loads(Path(res.evidencia[2]).read_text(encoding="utf-8"))
    assert len(llamada["peticion"]["usuario"]) > 3_000


async def test_declara_que_la_profundidad_es_una_estimacion(
    corpus: CorpusRelleno, tmp_path: Path
) -> None:
    proveedor = ProveedorFalso(dict.fromkeys(TipoPrueba, 100_000))
    res = await _sondar(proveedor, corpus, tmp_path, profundidades=(1_000,), intentos=1)
    assert "no un tokenizador exacto" in res.detalle["estimacion_tokens"].lower()
    assert res.detalle["chars_por_token"] == CHARS_POR_TOKEN
    assert res.detalle["ventana_anunciada"] == 262_144


async def test_el_relleno_pedido_coincide_con_la_profundidad_nominal(
    corpus: CorpusRelleno, tmp_path: Path
) -> None:
    proveedor = ProveedorFalso(dict.fromkeys(TipoPrueba, 100_000))
    await _sondar(proveedor, corpus, tmp_path, profundidades=(4_000,), intentos=1)
    tokens = [estimar_tokens(p.usuario) for p in proveedor.llamadas]
    assert all(4_000 <= t <= 4_200 for t in tokens), tokens


# --------------------------------------------------------------------------- #
# Presupuesto
# --------------------------------------------------------------------------- #


def test_el_contador_cobra_la_entrada_cacheada_cinco_veces_menos() -> None:
    contador = Contador(Precios(), max_usd=10.0)
    frio = contador.anotar(
        RespuestaSonda(contenido="x", prompt_tokens=1_000_000, completion_tokens=0)
    )
    contador2 = Contador(Precios(), max_usd=10.0)
    caliente = contador2.anotar(
        RespuestaSonda(
            contenido="x", prompt_tokens=1_000_000, cached_tokens=1_000_000, completion_tokens=0
        )
    )
    assert frio == pytest.approx(0.95)
    assert caliente == pytest.approx(0.19)


def test_la_estimacion_previa_es_pesimista() -> None:
    contador = Contador(Precios(), max_usd=0.001)
    assert not contador.cabe(prompt_tokens=1_000_000, max_tokens=512)
    assert contador.cabe(prompt_tokens=100, max_tokens=8)


# --------------------------------------------------------------------------- #
# Puente con `edecan_llm.LLMProvider`
# --------------------------------------------------------------------------- #


class _RespuestaRica(CompletionResponse):
    cached_tokens: int | None = None
    reasoning_content: str = ""
    neurons: float | None = None
    latencia_s: float = 0.0
    raw_usage: dict[str, Any] = {}


class _ProviderEspia:
    """Imita la superficie de `LLMProvider.complete` que usa el adaptador."""

    model = "@cf/moonshotai/kimi-k2.7-code"

    def __init__(self, respuesta: CompletionResponse) -> None:
        self.respuesta = respuesta
        self.peticiones: list[CompletionRequest] = []

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        self.peticiones.append(req)
        return self.respuesta


async def test_adaptador_traduce_los_extras_de_la_fase_0() -> None:
    espia = _ProviderEspia(
        _RespuestaRica(
            text="QX-7731",
            usage=Usage(input_tokens=9_000, output_tokens=65),
            stop_reason="end",
            cached_tokens=7_000,
            reasoning_content="voy a buscar el codigo",
            neurons=12.5,
            latencia_s=1.25,
            raw_usage={"neurons": 12.5},
        )
    )
    adaptador = AdaptadorLLMProvider(espia)
    resp = await adaptador.completar(PeticionSonda(system="S", usuario="U", max_tokens=512))

    assert resp.contenido == "QX-7731"
    assert resp.razonamiento == "voy a buscar el codigo"
    assert (resp.prompt_tokens, resp.completion_tokens, resp.cached_tokens) == (9_000, 65, 7_000)
    assert resp.neurons == 12.5
    assert resp.latencia_s == 1.25
    assert resp.crudo["raw_usage"] == {"neurons": 12.5}

    req = espia.peticiones[0]
    assert req.model == "@cf/moonshotai/kimi-k2.7-code"
    assert req.system == "S"
    assert req.messages == [ChatMessage(role="user", content="U")]


async def test_adaptador_sobrevive_a_un_completionresponse_pelado() -> None:
    """Un proveedor sin los extras no debe romper la sonda; solo no aportarlos."""
    espia = _ProviderEspia(
        CompletionResponse(
            text="hola", usage=Usage(input_tokens=10, output_tokens=2), stop_reason="end"
        )
    )
    resp = await AdaptadorLLMProvider(espia).completar(PeticionSonda(system="S", usuario="U"))
    assert resp.contenido == "hola"
    assert resp.razonamiento == ""
    assert resp.cached_tokens == 0
    assert resp.neurons is None
    assert resp.latencia_s > 0.0


# --------------------------------------------------------------------------- #
# Integración: nunca por defecto, y no basta con tener token
# --------------------------------------------------------------------------- #


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("FORGE_PROBE_INTEGRACION") != "1",
    reason="toca la API real y cuesta dinero; requiere FORGE_PROBE_INTEGRACION=1",
)
async def test_integracion_una_sola_profundidad_barata(tmp_path: Path) -> None:
    proveedores = pytest.importorskip("edecan_forge_probe.providers")
    res = await sondar_contexto(
        AdaptadorLLMProvider(proveedores.WorkersAIProvider()),
        profundidades=(4_000,),
        intentos=5,
        max_usd=0.10,
        dir_evidencia=tmp_path / "evidencia",
    )
    assert res.detalle["llamadas"] > 0
