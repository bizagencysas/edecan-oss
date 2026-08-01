"""Las piezas del motor de LinkedIn de Aria que Edecán no tenía, cableadas y probadas.

Complemento de `test_reparar_antes_de_rechazar.py` (que fija la capa determinista de
`editorial.py`, sin modelo): aquí se prueban las que sí hablan con un modelo -- el fallback a
prosa, el reintento de transporte, la pasada de corrección de delatores y el copy visual que de
verdad llega a la imagen.

Todo el pipeline por intento queda así, en el orden de Aria (`_finalizar_post`,
`linkedin_content.py:1572-1618`): escritor -> editor jefe que REESCRIBE -> auditor de hechos ->
reparador de primera persona -> gates deterministas AL FINAL y blandos en permisivo.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import edecan_creative.redaccion as redaccion
import pytest
from edecan_creative.auditoria import pulir_borrador, reparar_delatores
from edecan_creative.investigacion import Titular
from edecan_creative.redaccion import CrearPostLinkedInTool

# Largo de post real, no de titular: tiene que pasar el piso de entrega del motor
# (`redaccion.MIN_COPY_ENTREGABLE_CHARS`), que es lo único que garantiza que lo que sale de
# aquí se pueda pintar en una tarjeta sin verse roto.
_TEXTO_OK = (
    "Un banco mexicano ajustó su tasa de interés para tarjetas de crédito este trimestre. "
    "El cambio no toca el precio de lista, toca quién califica: el cliente que ya arrastra "
    "saldo cae en un tramo más caro sin haber pedido nada nuevo."
)


class UniqueUploader:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, ctx, *, data: bytes, filename: str, mime: str):  # noqa: ANN001
        file_id = uuid4()
        self.calls.append({"id": file_id, "data": data, "filename": filename, "mime": mime})
        return file_id, filename


class FakeLLM:
    """Doble de `LLMRouter`. `fallos` es el número de primeras llamadas que revientan, para
    poder probar el reintento de transporte."""

    def __init__(self, textos: list[str], *, fallos: int = 0) -> None:
        self._pendientes = list(textos)
        self._fallos = fallos
        self.calls: list[tuple] = []

    async def complete(self, alias, flags, request):  # noqa: ANN001
        self.calls.append((alias, flags, request))
        if self._fallos > 0:
            self._fallos -= 1
            raise RuntimeError("la red del proveedor se cayó")
        from edecan_llm.base import CompletionResponse, Usage

        texto = self._pendientes.pop(0) if self._pendientes else "{}"
        return CompletionResponse(
            text=texto, usage=Usage(input_tokens=10, output_tokens=10), stop_reason="end"
        )

    def prompt(self, indice: int) -> str:
        return self.calls[indice][2].messages[0].content


class FakeImageProvider:
    """Proveedor de imágenes que no toca la red. Hace falta uno REAL (no el
    `StubImageProvider`) porque con el stub `empaquetar_borrador_social` entrega la tarjeta
    offline, que ya trae su propio titular integrado y por lo tanto no pasa por el compositor."""

    async def generate(self, prompt: str, size: str = "1080x1350") -> bytes:
        return b"\x89PNG-falso"


def _titular() -> Titular:
    return Titular(
        titulo="Un banco mexicano ajusta tasas",
        snippet="Reforma · hace 3 h",
        url="https://example.com/nota-1",
        fuente="Reforma",
        fuente_url="https://reforma.com",
        publicado_en="2026-07-28T00:00:00+00:00",
        antiguedad_horas=3.0,
    )


def _fila(config: dict[str, Any]) -> dict[str, Any]:
    return {"config": config, "version": 1}


def _respuestas() -> list[list[dict[str, Any]]]:
    return [[], [], [], [], [], []]


def _args(**extra: Any) -> dict[str, Any]:
    args = {"tema": "tasas de tarjetas en México", "destino": "personal", "con_imagen": False}
    args.update(extra)
    return args


def _aprobado() -> str:
    return json.dumps({"publicable": True, "motivo": "Ok."})


def _auditado(texto: str = _TEXTO_OK) -> str:
    return json.dumps({"publicable": True, "problemas": [], "texto": texto})


@pytest.fixture(autouse=True)
def _una_noticia(monkeypatch):
    async def _fake(http, consulta, **kwargs):  # noqa: ANN001
        return [_titular()]

    monkeypatch.setattr(redaccion, "titulares_frescos", _fake)


# ---------------------------------------------------------------------------
# 0. EL FALLO QUE SE MIDIÓ, de punta a punta.
# ---------------------------------------------------------------------------


class EscritorTerco:
    """Se porta como el escritor real: devuelve SIEMPRE el mismo borrador con las tres
    violaciones a la vez (primera persona + delator de plantilla + cierre en fórmula). El editor
    jefe sí sabe arreglarlo, y el auditor conserva lo que el editor entregó."""

    _MALO = (
        "Aprendí algo mirando el lanzamiento de Opus 4.7. Ahí está el problema real: todos "
        "miden el modelo por el benchmark y ninguno mide lo que cuesta cambiar de proveedor a "
        "mitad de un pipeline ya montado. La pregunta que queda es si deberías migrar."
    )
    _BUENO = (
        "El lanzamiento de Opus 4.7 mueve el costo por tarea, no el puntaje del benchmark. "
        "Quien ya tiene un pipeline montado paga la migración en horas de ingeniería, y esas "
        "horas no aparecen en ninguna tabla comparativa."
    )
    # El reparador de primera persona es LITERAL: quita el pronombre y nada más, así que el
    # delator y la fórmula de cierre siguen ahí. Es justo lo que hacía morir al pipeline viejo,
    # donde el resultado del reparador se medía contra los doce gates completos.
    _SIN_YO = (
        "Hay algo en el lanzamiento de Opus 4.7. Ahí está el problema real: todos miden el "
        "modelo por el benchmark y ninguno mide lo que cuesta cambiar de proveedor a mitad de "
        "un pipeline ya montado. La pregunta que queda es si conviene migrar."
    )

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def complete(self, alias, flags, request):  # noqa: ANN001
        from edecan_llm.base import CompletionResponse, Usage

        self.calls.append((alias, flags, request))
        sistema = (request.system or "").lower()
        if alias in ("profundo", "principal") and "redactor editorial" in sistema:
            texto = json.dumps({"tema": "Opus 4.7", "fuente_principal": "F1", "texto": self._MALO})
        elif "editor jefe" in sistema:
            texto = json.dumps({"publicable": True, "motivo": "ok", "texto": self._BUENO})
        elif "auditor factual" in sistema:
            texto = json.dumps({"publicable": True, "problemas": [], "texto": self._BUENO})
        else:
            texto = self._SIN_YO
        return CompletionResponse(
            text=texto, usage=Usage(input_tokens=1, output_tokens=1), stop_reason="end"
        )


async def test_el_fallo_medido_ahora_entrega_el_borrador(make_ctx, make_session):
    """La regresión que resume todo el port.

    Lo que se midió: pidiendo "un post sobre el lanzamiento reciente de Claude Opus 4.7" y
    respondiendo "personal" al modal de destino, el motor devolvía *"Escribí el post 2 veces y
    ninguna versión pasó el control de calidad (...) Quita la primera persona"* y CERO
    `social_draft`. Eso es lo que Ada vive como "no funciona".

    Y era el comportamiento esperado de aquel diseño: la batería determinista era la PRIMERA
    puerta, corría sobre el texto crudo del escritor con doce reglas a la vez, no consultaba
    `permisivo`, y el único reparador cableado arreglaba una sola de las trece violaciones
    posibles. Con un escritor que mete tres a la vez -- primera persona, un "ahí está" y una
    fórmula de cierre -- ningún intento podía sobrevivir.

    Con el orden de Aria el mismo escritor terco produce un post: el editor jefe reescribe
    ANTES de que ningún gate mecánico vea el texto.
    """
    ctx = make_ctx(session=make_session(_respuestas()))
    ctx.llm = EscritorTerco()
    uploader = UniqueUploader()

    result = await CrearPostLinkedInTool(uploader=uploader).run(
        ctx,
        {
            "tema": "el lanzamiento reciente de Claude Opus 4.7 de Anthropic",
            "destino": "personal",
            "con_imagen": False,
        },
    )

    assert "ninguna versión pasó el control de calidad" not in result.content
    assert isinstance(result.data, dict)
    assert result.data["copy"] == EscritorTerco._BUENO
    assert result.presentation[0]["type"] == "social_draft"
    assert uploader.calls, "tiene que haber artefactos subidos, no un turno en blanco"
    # Y en UN solo pase del escritor: no se gastaron los tres intentos para no entregar nada.
    assert sum(1 for alias, _f, _r in ctx.llm.calls if alias == "profundo") == 1


# ---------------------------------------------------------------------------
# 1. El sobre falló, no el contenido: se acepta el post en PROSA.
# ---------------------------------------------------------------------------


async def test_un_post_en_prosa_no_se_tira_por_el_sobre(make_ctx, make_session):
    """Port de `_post_desde_raw` (Aria, `linkedin_content.py:586-608`), que lo documenta como
    un bug real vivido: "así nunca perdemos un buen post por parsing". Con un modelo pequeño al
    que se le pide un JSON de nueve campos, uno anidado, esto pasa seguido -- y era un post
    perfectamente bueno tirado a la basura por el SOBRE, no por el contenido. Antes quemaba uno
    de los tres intentos con la corrección "No devolviste el JSON pedido".
    """
    prosa = (
        "El ajuste de tasas encarece el crédito revolvente. Quien arrastra saldo paga la "
        "diferencia completa el mes siguiente, sin aviso previo del banco. El costo no cambia "
        "en la publicidad, cambia en la letra que nadie lee antes de firmar."
    )
    llm = FakeLLM([prosa, _aprobado(), _auditado(prosa)])
    ctx = make_ctx(session=make_session(_respuestas()))
    ctx.llm = llm
    uploader = UniqueUploader()

    result = await CrearPostLinkedInTool(uploader=uploader).run(ctx, _args())

    assert result.data["copy"] == prosa
    assert uploader.calls, "el post en prosa tiene que llegar al almacén"
    # Un solo pase del escritor: el intento no se quemó pidiendo el JSON otra vez.
    assert sum(1 for alias, _flags, _req in llm.calls if alias == "profundo") == 1


async def test_una_respuesta_corta_no_se_toma_como_post(make_ctx, make_session):
    """Un fragmento corto no es un post: probablemente sea una disculpa del modelo o una
    pregunta de vuelta. Ahí sí hay que reintentar, no empaquetar eso."""
    llm = FakeLLM(["No puedo ayudarte con eso.", "", ""])
    ctx = make_ctx(session=make_session(_respuestas()))
    ctx.llm = llm

    result = await CrearPostLinkedInTool(uploader=UniqueUploader()).run(ctx, _args())

    # Ningún borrador empaquetado, y el motivo en código para que quien entregue esto en
    # segundo plano pueda explicárselo a la persona sin repetirle jerga interna.
    assert result.data == {"fallo": redaccion.FALLO_NO_PUBLICABLE}
    assert "ninguna versión pasó el control de calidad" in result.content


# ---------------------------------------------------------------------------
# 2. Un fallo de TRANSPORTE no es un fallo editorial.
# ---------------------------------------------------------------------------


async def test_un_fallo_de_red_no_consume_un_intento_editorial(make_ctx, make_session):
    """Adaptación de `_max_con_reintento` (Aria, `linkedin_content.py:635-687`). Antes había
    una sola llamada a `ctx.llm.complete` y cualquier excepción consumía uno de los tres
    intentos editoriales: un timeout del proveedor se le leía al usuario como "el post no pasó
    el control de calidad", que es mentira, y lo mandaba a depurar el pipeline editorial cuando
    el problema había sido la conexión.
    """
    llm = FakeLLM([json.dumps({"tema": "Tasas", "texto": _TEXTO_OK}), _aprobado(), _auditado()])
    llm._fallos = 1  # la primera llamada revienta
    ctx = make_ctx(session=make_session(_respuestas()))
    ctx.llm = llm

    result = await CrearPostLinkedInTool(uploader=UniqueUploader()).run(ctx, _args())

    assert result.data["copy"] == _TEXTO_OK
    # El reintento usa el alias de RESPALDO, no el mismo que acaba de fallar.
    assert [alias for alias, _flags, _req in llm.calls][:2] == ["profundo", "principal"]


# ---------------------------------------------------------------------------
# 3. El editor jefe reescribe (y su reescritura es la que se publica).
# ---------------------------------------------------------------------------


async def test_el_editor_jefe_reescribe_y_su_texto_es_el_que_sale(make_ctx, make_session):
    """La pieza de mayor palanca del port. `revisar_calidad` sólo devolvía `(bool, motivo)`: un
    juez que no puede arreglar nada, así que todo el peso de escribir bien caía en un intento
    del escritor. El editor de Aria (`_revisar_calidad_editorial`) devuelve un borrador NUEVO
    -- texto, tema, ángulo y visual --, y por eso allá el escritor puede entregar algo
    imperfecto y el post sale igual."""
    flojo = "Ahí está el punto: el crédito se encarece y nadie lo dice claramente en la letra."
    pulido = (
        "El ajuste de tasas encarece el crédito revolvente para quien ya arrastra saldo del "
        "mes. La diferencia se paga en el estado de cuenta, no en el anuncio, y el pago mínimo "
        "deja de cubrir lo que cubría antes."
    )
    llm = FakeLLM(
        [
            json.dumps({"tema": "Tasas", "texto": flojo, "titular_visual": "Tasas más caras"}),
            json.dumps(
                {
                    "publicable": True,
                    "motivo": "Le puse el sujeto exacto.",
                    "tema": "Crédito revolvente",
                    "texto": pulido,
                    "visual": {"kicker": "BANCA", "headline": "El crédito se encarece"},
                }
            ),
            _auditado(pulido),
        ]
    )
    ctx = make_ctx(session=make_session(_respuestas()))
    ctx.llm = llm

    result = await CrearPostLinkedInTool(uploader=UniqueUploader()).run(ctx, _args())

    assert result.data["copy"] == pulido
    # El delator del borrador crudo ("ahí está") NO tumbó el turno: llegó al editor, que lo
    # reescribió. Eso es exactamente lo que el orden invertido impedía.
    assert sum(1 for alias, _flags, _req in llm.calls if alias == "profundo") == 1
    assert "no pasó el control editorial automático" not in result.content


async def test_el_editor_jefe_recibe_los_borradores_recientes_con_su_texto(
    make_ctx, make_session
):
    """Port de `_borradores_recientes_contexto` (Aria, `linkedin_content.py:293-307`): el
    bloque que ya existía sólo llevaba TEMAS, así que un post con tema nuevo y molde idéntico
    pasaba limpio. Detectar clones de ARQUITECTURA necesita el texto real."""
    anterior = "El 40% de los bancos ajustó tasas y nadie avisó a los clientes afectados."
    estado = {"historial": [{"tema": "Bancos", "copy": anterior}]}
    llm = FakeLLM(
        [json.dumps({"tema": "Tasas", "texto": _TEXTO_OK}), _aprobado(), _auditado()]
    )
    ctx = make_ctx(
        session=make_session(
            [[], [], [_fila({"agenda_estado": estado})], [], [_fila({"agenda_estado": estado})], []]
        )
    )
    ctx.llm = llm

    await CrearPostLinkedInTool(uploader=UniqueUploader()).run(ctx, _args())

    editor = llm.prompt(1)
    assert "BORRADORES RECIENTES QUE NO DEBES IMITAR" in editor
    assert anterior in editor


# ---------------------------------------------------------------------------
# 4. La pasada de corrección de delatores (`auditoria.reparar_delatores`).
# ---------------------------------------------------------------------------


async def test_reparar_delatores_reescribe_en_vez_de_matar_el_borrador():
    """Port de la segunda pasada de `_revisar_calidad_editorial`
    (`linkedin_content.py:1511-1544`). Los mismos diez patrones que en Aria disparan una
    reescritura de diez segundos, aquí sólo producían rechazo -- y como el único reparador
    cableado era `reescribir_sin_primera_persona`, que ante un texto SIN primera persona
    devuelve el texto INTACTO, el intento se quemaba sin haber intentado nada."""
    llamadas: list[list[dict[str, str]]] = []

    async def _llamar(mensajes):  # noqa: ANN001
        llamadas.append(mensajes)
        return json.dumps({"publicable": True, "texto": _TEXTO_OK})

    reparado = await reparar_delatores(
        {"texto": "Ahí está el problema real de todo esto.", "tema": "Tasas"},
        ["Quita la revelación anunciada con 'ahí está'."],
        "[F1] Un banco ajusta tasas",
        _llamar,
    )

    assert reparado is not None
    assert reparado["texto"] == _TEXTO_OK
    # Se le pasan los mensajes ACCIONABLES, no el nombre de la regla.
    assert "ahí está" in llamadas[0][1]["content"]


async def test_reparar_delatores_devuelve_none_si_la_correccion_sigue_sucia():
    """Colar como "corregido" un texto que el detector va a marcar igual sería peor que no
    reparar: el caller (en permisivo) conserva el texto anterior."""

    async def _llamar(mensajes):  # noqa: ANN001
        return json.dumps({"publicable": True, "texto": "Ahí está el problema real, otra vez."})

    reparado = await reparar_delatores(
        {"texto": "Ahí está el problema real."}, ["Quita 'ahí está'."], "", _llamar
    )

    assert reparado is None


async def test_pulir_borrador_conserva_el_borrador_en_permisivo_si_el_editor_falla():
    """`publicable=false` se IGNORA en permisivo, y un editor que no devuelve texto tampoco
    puede tirar el turno: el usuario pidió el tema y lo revisa antes de publicar."""

    async def _veta(mensajes):  # noqa: ANN001
        return json.dumps({"publicable": False, "motivo": "poco interesante"})

    permisivo = await pulir_borrador({"texto": _TEXTO_OK}, "", _veta, permisivo=True)
    estricto = await pulir_borrador({"texto": _TEXTO_OK}, "", _veta, permisivo=False)

    assert permisivo is not None
    assert permisivo["texto"] == _TEXTO_OK
    assert estricto is None


async def test_pulir_borrador_le_pasa_el_prompt_humanizador_como_system():
    """`PROMPT_EDITOR_HUMANIZADOR` era CÓDIGO MUERTO: estaba escrito casi palabra por palabra en
    `editorial.py` y no lo importaba nadie. El editor jefe corría con un system de cuatro líneas
    que no mencionaba ni un delator, o sea juzgaba a ciegas respecto de lo que el detector le iba
    a medir después."""
    sistemas: list[str] = []

    async def _llamar(mensajes):  # noqa: ANN001
        sistemas.append(mensajes[0]["content"])
        return json.dumps({"publicable": True, "texto": _TEXTO_OK})

    await pulir_borrador({"texto": _TEXTO_OK}, "", _llamar, permisivo=True)

    assert "señales de escritura generada por IA" in sistemas[0]
    assert "una sola pregunta discutible y específica" in sistemas[0]


# ---------------------------------------------------------------------------
# 5. El copy visual que de verdad se monta sobre la foto.
# ---------------------------------------------------------------------------


async def test_el_visual_llega_normalizado_al_compositor_y_al_critico(
    make_ctx, make_session, monkeypatch
):
    """Dos defectos en uno, los que Ada VE en vez de leer:

    - El compositor recibía el `visual` con `strip()` y nada más, así que un `accent`
      inexistente no resaltaba nada y un `support` que repetía el titular salía montado dos
      veces en la misma imagen.
    - El `claim` contra el que el crítico juzga la imagen era `titular_visual`, un campo
      DISTINTO del que se pinta, y `support` no entraba nunca. En Aria
      (`linkedin_content.py:966`) el claim es `headline + '. ' + support` tomados del MISMO
      dict que luego imprime el compositor.
    """
    compuesto: list[dict[str, str]] = []

    def _componer(foto, **kwargs):  # noqa: ANN001
        compuesto.append(kwargs)
        return foto

    claims: list[str] = []

    async def _generar(ctx, provider, *, texto, claim, **kwargs):  # noqa: ANN001
        claims.append(claim)
        return b"\x89PNG-falso", ""

    social = __import__("edecan_creative.social", fromlist=["x"])
    monkeypatch.setattr(social, "componer_titular", _componer)
    monkeypatch.setattr(social, "_generar_imagen_con_critica", _generar)

    llm = FakeLLM(
        [
            json.dumps(
                {
                    "tema": "Crédito revolvente",
                    "texto": _TEXTO_OK,
                    "titular_visual": "Tasas más caras",
                    "visual": {
                        "kicker": "banca · tasas",
                        "headline": "Banca: el crédito se encarece",
                        "accent": "SE ENCARECE",
                        "support": "El crédito se encarece bastante",
                    },
                }
            ),
            _aprobado(),
            _auditado(),
        ]
    )
    ctx = make_ctx(session=make_session(_respuestas()))
    ctx.llm = llm

    await CrearPostLinkedInTool(
        uploader=UniqueUploader(), image_provider=FakeImageProvider()
    ).run(ctx, _args(con_imagen=True))

    assert compuesto, "el compositor tiene que recibir el visual"
    montado = compuesto[0]
    assert montado["kicker"] == "BANCA · TASAS"
    # El prefijo que repetía el kicker se quitó.
    assert montado["headline"] == "El crédito se encarece"
    # El accent quedó con la caja REAL del headline.
    assert montado["accent"] == "se encarece"
    # Y el support que repetía el titular se vació en vez de imprimirse dos veces.
    assert montado["support"] == ""
    # El crítico juzga contra el texto que se monta, no contra `titular_visual`.
    assert claims == ["El crédito se encarece"]


async def test_sin_visual_del_escritor_la_imagen_no_se_queda_sin_titular(
    make_ctx, make_session, monkeypatch
):
    """El octavo fallo esperando: con un `visual` vacío, `_visual_publicable` devolvía None y la
    persona recibía la foto pelada -- sin kicker, sin titular y sin subtítulo -- sin que nada lo
    explicara. Ahora degrada al titular y al tema, como `_normalizar_visual` de Aria."""
    compuesto: list[dict[str, str]] = []

    def _componer(foto, **kwargs):  # noqa: ANN001
        compuesto.append(kwargs)
        return foto

    async def _generar(ctx, provider, *, texto, claim, **kwargs):  # noqa: ANN001
        return b"\x89PNG-falso", ""

    social = __import__("edecan_creative.social", fromlist=["x"])
    monkeypatch.setattr(social, "componer_titular", _componer)
    monkeypatch.setattr(social, "_generar_imagen_con_critica", _generar)

    llm = FakeLLM(
        [
            json.dumps(
                {
                    "tema": "Crédito revolvente",
                    "texto": _TEXTO_OK,
                    "titular_visual": "Tasas más caras",
                    "visual": {},
                }
            ),
            _aprobado(),
            _auditado(),
        ]
    )
    ctx = make_ctx(session=make_session(_respuestas()))
    ctx.llm = llm

    await CrearPostLinkedInTool(
        uploader=UniqueUploader(), image_provider=FakeImageProvider()
    ).run(ctx, _args(con_imagen=True))

    assert compuesto, "aunque el escritor no llene `visual`, algo se monta"
    assert compuesto[0]["headline"] == "Tasas más caras"
    assert compuesto[0]["kicker"] == "CRÉDITO REVOLVENTE"
