"""El ciclo completo de `crear_post_linkedin` (`edecan_creative.redaccion`), de punta a punta.

Lo que se prueba aquí es exactamente lo que antes dependía de que el modelo del chat
encadenara cuatro llamadas sin perder los datos en el camino: investigar, entregarle la fuente
al escritor, auditar lo que escribió y entregar el borrador. Cada test fija UN eslabón, con el
proveedor de búsqueda simulado (nunca se toca la red) y un LLM falso que devuelve respuestas
programadas en orden.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import edecan_creative.redaccion as redaccion
from edecan_creative.investigacion import Titular
from edecan_creative.redaccion import CrearPostLinkedInTool

# Pasa `editorial.revisar`: sin primera persona, sin cierre en pregunta, sin plantillas de IA.
#
# Y pasa el PISO DE ENTREGA (`redaccion.MIN_COPY_ENTREGABLE_CHARS`). Antes eran dos frases de
# ~85 caracteres, y esa longitud era mentira: fijaba como "post entregable" algo que en el
# teléfono se ve como una tarjeta rota. Justo esa mentira es la que dejó pasar el fallo real
# (un copy de 118 caracteres que el motor daba por bueno y el job descartaba en silencio), así
# que estos textos ahora miden lo que mide un post de verdad.
_TEXTO_OK = (
    "Un banco mexicano ajustó su tasa de interés para tarjetas de crédito este trimestre. "
    "El cambio no toca el precio de lista, toca quién califica: el cliente que ya arrastra "
    "saldo cae en un tramo más caro sin haber pedido nada nuevo."
)
_TEXTO_OK_2 = (
    "El ajuste de tasas encarece el crédito revolvente para quienes ya arrastran saldo. "
    "La diferencia no aparece en la publicidad, aparece en el estado de cuenta del mes "
    "siguiente, cuando el pago mínimo cubre menos capital que antes."
)
_TITULAR_OK = "Tasas más caras para tarjetas"

# Banco de contexto de un tenant cualquiera: material verificable y PRIVADO. Sirve para
# auditar lo que el post afirma; jamás para publicarse como fuente.
_BANCO = "Opera una consultora de datos desde 2019."
_PERFIL_CON_BANCO = {"voice": "Directo", "context_bank": _BANCO}


class UniqueUploader:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, ctx, *, data: bytes, filename: str, mime: str):  # noqa: ANN001
        file_id = uuid4()
        self.calls.append({"id": file_id, "data": data, "filename": filename, "mime": mime})
        return file_id, filename


class FakeLLM:
    """Doble de `LLMRouter`: devuelve las respuestas programadas en orden, una por llamada.

    En este pipeline el orden por intento es siempre el mismo: escritor -> editor jefe
    (`revisar_calidad`) -> auditor de hechos (`auditar_hechos`).
    """

    def __init__(self, textos: list[str]) -> None:
        self._pendientes = list(textos)
        self.calls: list[tuple] = []

    async def complete(self, alias, flags, request):  # noqa: ANN001
        self.calls.append((alias, flags, request))
        from edecan_llm.base import CompletionResponse, Usage

        texto = self._pendientes.pop(0) if self._pendientes else "{}"
        return CompletionResponse(
            text=texto, usage=Usage(input_tokens=10, output_tokens=10), stop_reason="end"
        )

    def prompt(self, indice: int) -> str:
        return self.calls[indice][2].messages[0].content


def _borrador(texto: str = _TEXTO_OK, fuente: str = "F1") -> str:
    return json.dumps(
        {
            "tema": "Tasas de tarjetas",
            "fuente_principal": fuente,
            "texto": texto,
            "titular_visual": _TITULAR_OK,
            "alt_text": "Una tarjeta de crédito sobre un mostrador.",
        }
    )


def _aprobado(motivo: str = "Tiene un hecho concreto.") -> str:
    """El editor jefe aprueba sin devolver texto propio.

    En modo permisivo (tema pedido) eso significa "conserva el borrador tal cual", que es lo
    que quieren la mayoría de estos tests. Fuera de permisivo es un descarte, igual que en
    Aria (`_revisar_calidad_editorial`: `return post if permisivo else None`) -- para esa
    ruta hay que usar `_pulido`.
    """
    return json.dumps({"publicable": True, "motivo": motivo})


def _pulido(texto: str = _TEXTO_OK, motivo: str = "Ok, con ángulo concreto.") -> str:
    """El editor jefe REESCRIBIENDO, que es su trabajo desde el port del orden de Aria."""
    return json.dumps({"publicable": True, "motivo": motivo, "texto": texto})


def _auditado(texto: str = _TEXTO_OK) -> str:
    return json.dumps({"publicable": True, "problemas": [], "texto": texto})


def _titular(titulo: str = "Un banco mexicano ajusta tasas") -> Titular:
    return Titular(
        titulo=titulo,
        snippet="Reforma · hace 3 h",
        url="https://example.com/nota-1",
        fuente="Reforma",
        fuente_url="https://reforma.com",
        publicado_en="2026-07-28T00:00:00+00:00",
        antiguedad_horas=3.0,
    )


def _fila(config: dict[str, Any]) -> dict[str, Any]:
    return {"config": config, "version": 1}


def _respuestas(
    *, perfil: dict[str, Any] | None = None, agenda_estado: dict[str, Any] | None = None
) -> list[list[dict[str, Any]]]:
    """Las filas que devolverá `FakeSession`, en el orden EXACTO en que la tool consulta.

    Con `destino` ya puesto no se consulta la lista de destinos, así que el orden es: perfil
    del destino, estado de rotación, (el `save` de rotación no lee nada), y ya dentro del
    empaquetado otra lectura del estado más su `save`.
    """
    fila_agenda = [_fila({"agenda_estado": agenda_estado})] if agenda_estado else []
    return [
        [_fila(perfil)] if perfil else [],  # perfil del destino (`linkedin_personal`)
        *([[]] if not perfil else []),  # fallback al perfil legacy solo si el anterior vacío
        fila_agenda,  # get_agenda_state
        [],  # save_agenda_state
        fila_agenda,  # get_agenda_state dentro de empaquetar_borrador_social
        [],  # save_agenda_state final
    ]


def _args(**extra: Any) -> dict[str, Any]:
    args = {"tema": "tasas de tarjetas en México", "destino": "personal", "con_imagen": False}
    args.update(extra)
    return args


# ---------------------------------------------------------------------------
# 1. Con noticia fresca: el ciclo entero en una sola llamada.
# ---------------------------------------------------------------------------


async def test_con_noticia_fresca_entrega_el_borrador_en_una_sola_llamada(
    make_ctx, make_session, monkeypatch
):
    consultas: list[tuple[str, int]] = []

    async def _fake(http, consulta, *, maximo=8, max_dias=3, **kwargs):  # noqa: ANN001
        consultas.append((consulta, max_dias))
        return [_titular()]

    monkeypatch.setattr(redaccion, "titulares_frescos", _fake)
    llm = FakeLLM([_borrador(), _aprobado(), _auditado()])
    ctx = make_ctx(session=make_session(_respuestas()))
    ctx.llm = llm
    uploader = UniqueUploader()

    result = await CrearPostLinkedInTool(uploader=uploader).run(ctx, _args())

    assert result.data["copy"] == _TEXTO_OK
    assert result.data["origen_fuente"] == "noticias_frescas"
    # La fuente viaja SOLA por dentro: ni el modelo tuvo que transportarla, ni se perdió.
    assert result.data["sources"] == [
        {
            "title": "Un banco mexicano ajusta tasas",
            "url": "https://example.com/nota-1",
            "snippet": "Reforma · hace 3 h",
        }
    ]
    assert result.presentation[0]["type"] == "social_draft"
    assert result.presentation[0]["target"] == "personal"
    assert len(uploader.calls) == 2  # markdown + manifiesto, sin imagen

    # El tema del usuario es la consulta, y el escritor recibió la fuente ya resuelta.
    assert consultas[0][0] == "tasas de tarjetas en México"
    prompt_escritor = llm.prompt(0)
    assert "[F1] Un banco mexicano ajusta tasas" in prompt_escritor
    assert "https://example.com/nota-1" in prompt_escritor
    # Escritor -> editor jefe -> auditor, sin que el modelo del chat decida nada.
    assert [llamada[0] for llamada in llm.calls] == ["profundo", "principal", "principal"]


async def test_le_pasa_al_escritor_los_temas_recientes_para_que_no_repita(
    make_ctx, make_session, monkeypatch
):
    async def _fake(http, consulta, **kwargs):  # noqa: ANN001
        return [_titular()]

    monkeypatch.setattr(redaccion, "titulares_frescos", _fake)
    llm = FakeLLM([_borrador(), _aprobado(), _auditado()])
    estado = {
        "territorio_idx": 0,
        "formato_idx": 0,
        "foco_idx": 0,
        "historial": [{"tema": "Comisiones de transferencias", "temas": []}],
    }
    ctx = make_ctx(session=make_session(_respuestas(agenda_estado=estado)))
    ctx.llm = llm

    await CrearPostLinkedInTool(uploader=UniqueUploader()).run(ctx, _args())

    prompt_escritor = llm.prompt(0)
    assert "NO DEBES REPETIR" in prompt_escritor
    assert "Comisiones de transferencias" in prompt_escritor
    # Y la forma argumental de este turno también viene del motor de rotación, no del modelo.
    assert "FORMA EDITORIAL OBLIGATORIA DE ESTE BORRADOR" in prompt_escritor


async def test_sin_tema_pedido_usa_el_territorio_que_toca_en_la_rotacion(
    make_ctx, make_session, monkeypatch
):
    """El caso 'escribe el post de hoy': el territorio y la consulta salen del motor de
    rotación, no de una ocurrencia del modelo."""
    consultas: list[str] = []

    async def _fake(http, consulta, **kwargs):  # noqa: ANN001
        consultas.append(consulta)
        return [_titular()]

    monkeypatch.setattr(redaccion, "titulares_frescos", _fake)
    llm = FakeLLM([_borrador(), _aprobado(), _auditado()])
    ctx = make_ctx(session=make_session(_respuestas()))
    ctx.llm = llm

    args = _args()
    del args["tema"]
    result = await CrearPostLinkedInTool(uploader=UniqueUploader()).run(ctx, args)

    assert result.data["copy"] == _TEXTO_OK
    territorio = redaccion.agenda.TERRITORIOS_POR_DEFECTO[0]
    assert consultas[0].startswith(territorio["query"])
    assert territorio["pilar"] in llm.prompt(0)


async def test_sin_tema_pedido_usa_los_pilares_del_tenant_si_los_configuro(
    make_ctx, make_session, monkeypatch
):
    """Con `content_pillars` configurados, la rotación "sin tema" gira sobre ESOS pilares,
    no sobre el catálogo genérico de `agenda.TERRITORIOS_POR_DEFECTO` -- ver
    `redaccion._territorios_del_perfil`/`agenda.territorios_desde_pilares`.

    Es la causa real detrás de la queja "esto es lo mismo que ya te mandé, no está
    rotando" (Ada, 31-jul-2026): antes, ningún perfil -- ni siquiera el de Acme con
    sus 24 pilares -- llegaba a cambiar el territorio que elegía el motor sin un `tema`
    explícito puesto a mano.

    El pilar de este test ("el mostrador") es una ESCENA, no la "noticia de la semana": no debe
    disparar una búsqueda de titulares (`redaccion._pilar_es_noticia`/`buscar_noticia`), así
    que el banco de contexto real del tenant es lo que sostiene el post -- de ahí que el
    perfil de este test SÍ traiga `context_bank` (sin él, una escena sin noticia y sin banco
    se salta el turno por diseño: "no forzar un post", ver `test_reparar_antes_de_rechazar`).
    Ver el docstring de `_reunir_contexto` para el bug real que esto arregla: antes, CUALQUIER
    pilar (incluida una escena) buscaba con su propia etiqueta como consulta, y si esa
    etiqueta nombraba una marca real con noticias ese día, el motor anclaba el post entero en
    lo primero que encontrara -- tuviera o no relación con el ángulo de la escena.
    """
    llamadas_busqueda: list[str] = []

    async def _fake(http, consulta, **kwargs):  # noqa: ANN001
        llamadas_busqueda.append(consulta)
        return [_titular()]

    monkeypatch.setattr(redaccion, "titulares_frescos", _fake)
    llm = FakeLLM([_borrador(), _aprobado(), _auditado()])
    perfil = {
        "voice": "Directo",
        "content_pillars": ["el mostrador", "pagos a plazos", "noticia de la semana"],
        "context_bank": _BANCO,
    }
    ctx = make_ctx(session=make_session(_respuestas(perfil=perfil)))
    ctx.llm = llm

    args = _args()
    del args["tema"]
    result = await CrearPostLinkedInTool(uploader=UniqueUploader()).run(ctx, args)

    assert result.data["copy"] == _TEXTO_OK
    assert result.data["origen_fuente"] == "banco_de_contexto"
    # "el mostrador" es una escena: nunca dispara una búsqueda de titulares.
    assert llamadas_busqueda == []
    assert "el mostrador" in llm.prompt(0)
    # Nunca el catálogo genérico: sin pilares de tenant sería éste, y no debe aparecer.
    generico = redaccion.agenda.TERRITORIOS_POR_DEFECTO[0]
    assert generico["pilar"] not in llm.prompt(0)


async def test_pilar_noticia_del_tenant_si_busca_titulares_frescos(
    make_ctx, make_session, monkeypatch
):
    """Único caso, dentro de los `content_pillars` de un tenant, que SÍ dispara una búsqueda
    de titulares: el pilar que se nombra a sí mismo "noticia" (`redaccion._pilar_es_noticia`).
    Complementa el test anterior, que fija el caso general (escena -> banco, nunca busca)."""
    consultas: list[str] = []

    async def _fake(http, consulta, **kwargs):  # noqa: ANN001
        consultas.append(consulta)
        return [_titular()]

    monkeypatch.setattr(redaccion, "titulares_frescos", _fake)
    llm = FakeLLM([_borrador(), _aprobado(), _auditado()])
    perfil = {
        "voice": "Directo",
        "content_pillars": ["noticia de la semana", "el mostrador"],
        "context_bank": _BANCO,
    }
    ctx = make_ctx(session=make_session(_respuestas(perfil=perfil)))
    ctx.llm = llm

    args = _args()
    del args["tema"]
    result = await CrearPostLinkedInTool(uploader=UniqueUploader()).run(ctx, args)

    assert result.data["copy"] == _TEXTO_OK
    assert result.data["origen_fuente"] == "noticias_frescas"
    assert consultas[0].startswith("noticia de la semana")


async def test_un_tema_del_usuario_no_quema_el_territorio_de_la_rotacion(
    make_ctx, make_session, monkeypatch
):
    """El reloj de la FORMA avanza siempre (dos posts seguidos no salen con la misma
    arquitectura), pero el del TERRITORIO no: este turno lo mandó el tema del usuario, y
    gastarlo dejaría un hueco permanente en la rotación del feed."""

    async def _fake(http, consulta, **kwargs):  # noqa: ANN001
        return [_titular()]

    monkeypatch.setattr(redaccion, "titulares_frescos", _fake)
    llm = FakeLLM([_borrador(), _aprobado(), _auditado()])
    session = make_session(_respuestas())
    ctx = make_ctx(session=session)
    ctx.llm = llm

    await CrearPostLinkedInTool(uploader=UniqueUploader()).run(ctx, _args())

    guardados = [p for _, p in session.llamadas if p.get("key") == "agenda_estado"]
    estado = json.loads(guardados[0]["value"])
    assert estado["formato_idx"] == 1
    assert estado["territorio_idx"] == 0


# ---------------------------------------------------------------------------
# 2. Sin noticia fresca: la red de seguridad, no la improvisación.
# ---------------------------------------------------------------------------


async def test_sin_noticia_en_la_ventana_pedida_amplia_la_busqueda_antes_de_rendirse(
    make_ctx, make_session, monkeypatch
):
    ventanas: list[int] = []

    async def _fake(http, consulta, *, maximo=8, max_dias=3, **kwargs):  # noqa: ANN001
        ventanas.append(max_dias)
        return [_titular()] if max_dias > 3 else []

    monkeypatch.setattr(redaccion, "titulares_frescos", _fake)
    llm = FakeLLM([_borrador(), _aprobado(), _auditado()])
    ctx = make_ctx(session=make_session(_respuestas()))
    ctx.llm = llm

    result = await CrearPostLinkedInTool(uploader=UniqueUploader()).run(ctx, _args())

    assert ventanas == [3, 14]
    assert result.data["origen_fuente"] == "noticias_ventana_ampliada"
    assert "no la presento como novedad de hoy" in result.content


async def test_sin_noticia_el_auditor_comprueba_contra_el_banco_en_vez_de_vaciar_el_post(
    make_ctx, make_session, monkeypatch
):
    """La red de seguridad, auditada con lo que el escritor de verdad recibió.

    Este escalón se auditaba a ciegas: al escritor se le decía "apóyate ÚNICAMENTE en el
    banco" y al auditor se le entregaba una lista de fuentes VACÍA, con lo cual entraba en su
    modo SIN FUENTES -- el que ordena eliminar cifras, marcas y afirmaciones sobre cómo actúa
    una industria, o sea exactamente el material del banco. Y al editor jefe se le decía "sin
    fuentes citables", que en modo permisivo es justo el motivo de regla dura que lo autoriza
    a rechazar. El respaldo que existe para no escribir nunca sin fuente terminaba vaciando o
    vetando el post que él mismo acababa de pedir: peor que no tenerlo, porque consume el
    turno igual.
    """

    async def _sin_noticias(http, consulta, **kwargs):  # noqa: ANN001
        return []

    monkeypatch.setattr(redaccion, "titulares_frescos", _sin_noticias)
    llm = FakeLLM([_borrador(fuente=""), _aprobado(), _auditado()])
    ctx = make_ctx(session=make_session(_respuestas(perfil=_PERFIL_CON_BANCO)))
    ctx.llm = llm

    result = await CrearPostLinkedInTool(uploader=UniqueUploader()).run(ctx, _args())

    assert result.data["origen_fuente"] == "banco_de_contexto"
    assert "sin ningún evento fechable" in result.content
    assert _BANCO in llm.prompt(0)  # el escritor
    # El auditor comprueba CONTRA el banco, no contra el vacío.
    auditor = llm.prompt(2)
    assert _BANCO in auditor
    assert "SIN FUENTES" not in auditor
    # Y el editor jefe ve que el borrador sí tiene respaldo detrás.
    editor = llm.prompt(1)
    assert _BANCO in editor
    assert "Sin fuentes citables entregadas" not in editor


async def test_el_banco_se_audita_pero_nunca_sale_publicado_como_fuente(
    make_ctx, make_session, monkeypatch
):
    """Verificable no es lo mismo que citable, y por eso son dos listas distintas.

    El banco trae identidad y cicatrices personales del dueño de la cuenta: sostiene la
    auditoría, pero `sources` se escribe al manifiesto y a la card que el usuario comparte.
    Lo que se publica como fuente del post es solo el titular con URL, que además es lo único
    que un lector puede ir a verificar por su cuenta.
    """

    async def _fake(http, consulta, **kwargs):  # noqa: ANN001
        return [_titular()]

    monkeypatch.setattr(redaccion, "titulares_frescos", _fake)
    llm = FakeLLM([_borrador(), _aprobado(), _auditado()])
    ctx = make_ctx(session=make_session(_respuestas(perfil=_PERFIL_CON_BANCO)))
    ctx.llm = llm
    uploader = UniqueUploader()

    result = await CrearPostLinkedInTool(uploader=uploader).run(ctx, _args())

    assert result.data["sources"] == [
        {
            "title": "Un banco mexicano ajusta tasas",
            "url": "https://example.com/nota-1",
            "snippet": "Reforma · hace 3 h",
        }
    ]
    manifiesto = next(c for c in uploader.calls if c["mime"] == "application/json")
    assert _BANCO not in manifiesto["data"].decode("utf-8")
    # Pero el auditor sí lo tuvo delante, junto con la noticia: el banco viaja SIEMPRE en el
    # prompt del escritor, así que auditar solo contra el titular vetaría como invención
    # cualquier hecho propio que el post use bien.
    auditor = llm.prompt(2)
    assert _BANCO in auditor
    assert "Un banco mexicano ajusta tasas" in auditor
    # Y con la etiqueta que le prohíbe al auditor usarlo como material publicable.
    assert "NO citable" in auditor


async def test_en_el_escalon_del_banco_el_auditor_sigue_pudiendo_vetar(
    make_ctx, make_session, monkeypatch
):
    """Darle el banco al auditor no lo ablanda: en este escalón el banco es la ÚNICA verdad
    permitida, y lo que no esté ahí se veta igual.

    Lo que CAMBIÓ con el port del orden de Aria: con un tema pedido por el usuario, el veto
    del auditor ya no quema el intento completo para volver a empezar de cero. Se conserva el
    texto que el editor jefe ya pulió y ancló en la fuente, y se SIGUE ADELANTE -- igual que
    `_finalizar_post` (`linkedin_content.py:1598-1600`). Reintentar el ciclo entero gastaba
    escritor + editor + auditor otra vez, y eso es la mitad de los dos minutos de espera que la
    persona vive como "no funciona". Lo que Edecán añade sobre Aria es el AVISO: allá degrada
    en silencio porque es un cron, acá la persona está mirando.
    """

    async def _sin_noticias(http, consulta, **kwargs):  # noqa: ANN001
        return []

    monkeypatch.setattr(redaccion, "titulares_frescos", _sin_noticias)
    veto = json.dumps({"publicable": False, "problemas": ["La cifra citada no está en el banco."]})
    llm = FakeLLM([_borrador(fuente=""), _aprobado(), veto])
    ctx = make_ctx(session=make_session(_respuestas(perfil=_PERFIL_CON_BANCO)))
    ctx.llm = llm
    uploader = UniqueUploader()

    result = await CrearPostLinkedInTool(uploader=uploader).run(ctx, _args())

    # El borrador SÍ se entrega (data + upload) con el motivo del veto adelante.
    assert isinstance(result.data, dict)
    assert uploader.calls, "el borrador tiene que llegar al almacén, no callarse"
    assert "La cifra citada no está en el banco." in result.content
    assert "verifica los datos antes de publicarlo" in result.content
    # Y NO se gastó un segundo ciclo completo: un solo pase del escritor.
    assert sum(1 for alias, _flags, _req in llm.calls if alias == "profundo") == 1


async def test_sin_banco_configurado_el_auditor_solo_ve_la_noticia(
    make_ctx, make_session, monkeypatch
):
    """El auditor no se ablanda por este arreglo: sin banco, sigue teniendo delante ÚNICAMENTE
    la fuente elegida, y cualquier cifra que no esté ahí sigue siendo una invención."""

    async def _fake(http, consulta, **kwargs):  # noqa: ANN001
        return [_titular()]

    monkeypatch.setattr(redaccion, "titulares_frescos", _fake)
    llm = FakeLLM([_borrador(), _aprobado(), _auditado()])
    ctx = make_ctx(session=make_session(_respuestas()))
    ctx.llm = llm

    await CrearPostLinkedInTool(uploader=UniqueUploader()).run(ctx, _args())

    auditor = llm.prompt(2)
    assert "[F1] Un banco mexicano ajusta tasas" in auditor
    assert "NO citable" not in auditor
    assert "[F2]" not in auditor


async def test_el_banco_de_contexto_viaja_aunque_haya_noticia_fresca(
    make_ctx, make_session, monkeypatch
):
    """El banco no es solo el respaldo: es lo ÚNICO que autoriza a atribuirle un hecho propio
    al autor. Sin él en el prompt, al escritor solo le quedan dos salidas malas -- inventarse
    una anécdota, o escribir sin nada propio."""

    async def _fake(http, consulta, **kwargs):  # noqa: ANN001
        return [_titular()]

    monkeypatch.setattr(redaccion, "titulares_frescos", _fake)
    llm = FakeLLM([_borrador(), _aprobado(), _auditado()])
    ctx = make_ctx(session=make_session(_respuestas(perfil=_PERFIL_CON_BANCO)))
    ctx.llm = llm

    result = await CrearPostLinkedInTool(uploader=UniqueUploader()).run(ctx, _args())

    assert result.data["origen_fuente"] == "noticias_frescas"
    prompt_escritor = llm.prompt(0)
    assert _BANCO in prompt_escritor
    # Y con su regla de uso: material de fondo, jamás autobiografía.
    assert "nunca se convierte en autobiografía" in prompt_escritor
    assert "Voz: Directo" in prompt_escritor


async def test_sin_noticia_y_sin_banco_con_tema_pedido_escribe_tesis_pura(
    make_ctx, make_session, monkeypatch
):
    """El último escalón de la red de seguridad, portado de Aria.

    Antes, sin noticia y sin banco la herramienta respondía "No escribí nada" en segundos, sin
    haber llamado al escritor UNA sola vez -- y eso suena a que el motor no lo intentó. Es el
    segundo candidato fuerte al fallo que se midió: "el lanzamiento reciente de Claude Opus
    4.7" en Google News en español puede devolver cero titulares, y si el perfil personal no
    tiene `context_bank`, el usuario recibía la nada.

    Aria en esa misma situación y en la misma ruta (tema pedido, `_escribir_sobre_tema`,
    `linkedin_content.py:2251-2257`) DEGRADA a tesis pura -- "cero eventos concretos, cero
    cifras" -- y entrega. El auditor ya sabe manejar ese modo con su marcador SIN FUENTES, así
    que la pieza sigue sin poder afirmar ningún hecho fechable: lo que cambia es que sí hay
    post.
    """

    async def _sin_noticias(http, consulta, **kwargs):  # noqa: ANN001
        return []

    monkeypatch.setattr(redaccion, "titulares_frescos", _sin_noticias)
    llm = FakeLLM([_borrador(fuente=""), _aprobado(), _auditado()])
    ctx = make_ctx(session=make_session(_respuestas()))
    ctx.llm = llm
    uploader = UniqueUploader()

    result = await CrearPostLinkedInTool(uploader=uploader).run(ctx, _args())

    assert isinstance(result.data, dict)
    assert result.data["origen_fuente"] == "tesis_sin_fuente"
    assert uploader.calls, "con el tema pedido tiene que salir un borrador, no un mensaje vacío"
    # El escritor recibió la instrucción de escribir criterio, no actualidad.
    escritor = llm.prompt(0)
    assert "TESIS PURA" in escritor
    assert "cero cifras" in escritor
    # Y el usuario se entera de por qué el post no cita a nadie, con qué hacer al respecto.
    assert "criterio puro" in result.content
    assert "context_bank" in result.content or "banco de contexto" in result.content


async def test_sin_noticia_sin_banco_y_sin_tema_pedido_se_salta_el_turno(
    make_ctx, make_session, monkeypatch
):
    """La otra mitad de la regla: el post RECURRENTE sí se salta.

    Sin tema pedido nadie está esperando en el chat, y una tesis al aire sobre un territorio de
    rotación no aporta nada -- aquí sí manda la regla "no forzar un post". La degradación a
    tesis pura es exclusiva de la ruta en la que el usuario pidió el post, igual que en Aria.
    """

    async def _sin_noticias(http, consulta, **kwargs):  # noqa: ANN001
        return []

    monkeypatch.setattr(redaccion, "titulares_frescos", _sin_noticias)
    llm = FakeLLM([])
    ctx = make_ctx(session=make_session(_respuestas()))
    ctx.llm = llm
    uploader = UniqueUploader()

    result = await CrearPostLinkedInTool(uploader=uploader).run(
        ctx, {"destino": "personal", "con_imagen": False}
    )

    # Ningún borrador, y el MOTIVO en código: es lo que permite que quien corre esto en
    # segundo plano le explique el fallo a la persona en su idioma, sin parsear la prosa de
    # `content` (que está escrita para el modelo del chat).
    assert result.data == {"fallo": redaccion.FALLO_SIN_FUENTE}
    assert uploader.calls == []
    # Ni una llamada al modelo: sin fuente no hay nada que escribir.
    assert llm.calls == []
    # Y el mensaje dice QUÉ hacer, no solo que falló.
    assert "context_bank" in result.content
    assert "max_dias" in result.content
    assert "crear_contenido_social" in result.content


# ---------------------------------------------------------------------------
# 3. Los controles corren siempre, y un rechazo se reintenta con el motivo concreto.
# ---------------------------------------------------------------------------


async def test_si_el_auditor_rechaza_reescribe_con_el_problema_y_entrega_la_segunda(
    make_ctx, make_session, monkeypatch
):
    """El reintento con el motivo concreto en el prompt, en la ruta SIN tema pedido.

    Va sin tema a propósito: con un tema pedido (`permisivo`) el veto del auditor ya no quema el
    ciclo, degrada y entrega -- ver `test_en_el_escalon_del_banco_el_auditor_sigue_pudiendo_vetar`.
    En el post recurrente sí se reintenta, y lo que se prueba aquí es que el reintento no repite
    el mismo pedido tal cual: lleva el motivo exacto del rechazo.
    """

    async def _fake(http, consulta, **kwargs):  # noqa: ANN001
        return [_titular()]

    monkeypatch.setattr(redaccion, "titulares_frescos", _fake)
    veto = json.dumps(
        {
            "publicable": False,
            "problemas": ["La tasa mencionada no aparece en ninguna fuente entregada."],
        }
    )
    llm = FakeLLM(
        [
            _borrador(),
            _pulido(_TEXTO_OK),
            veto,
            _borrador(_TEXTO_OK_2),
            _pulido(_TEXTO_OK_2),
            _auditado(_TEXTO_OK_2),
        ]
    )
    ctx = make_ctx(session=make_session(_respuestas()))
    ctx.llm = llm
    uploader = UniqueUploader()

    result = await CrearPostLinkedInTool(uploader=uploader).run(
        ctx, {"destino": "personal", "con_imagen": False}
    )

    assert result.data["copy"] == _TEXTO_OK_2
    assert len(uploader.calls) == 2
    # El reintento no repite el mismo pedido: lleva el motivo exacto del rechazo.
    reintento = llm.prompt(3)
    assert "CORRIGE" in reintento
    assert "La tasa mencionada no aparece en ninguna fuente entregada." in reintento


async def test_si_ninguna_version_pasa_entrega_el_borrador_con_aviso(
    make_ctx, make_session, monkeypatch
):
    """Antes esta prueba fijaba `result.data is None` -- el motor callaba el
    borrador cuando el auditor rechazaba las N veces. Es correcto para el cron
    de Aria ("saltarse un turno protege la cuenta"), pero acá `crear_post_
    linkedin` se invoca como ATAJO del chat: la persona lo pidió, ver el chat
    en blanco tras dos minutos es peor que ver el borrador con un aviso.

    Ahora se entrega el borrador que sí pasó los gates deterministas junto con
    el motivo del rechazo, para que la persona decida.
    """

    async def _fake(http, consulta, **kwargs):  # noqa: ANN001
        return [_titular()]

    monkeypatch.setattr(redaccion, "titulares_frescos", _fake)
    veto = json.dumps({"publicable": False, "problemas": ["El texto no se sostiene."]})
    llm = FakeLLM(
        [
            _borrador(),
            _pulido(),
            veto,
            _borrador(),
            _pulido(),
            veto,
            _borrador(),
            _pulido(),
            veto,
        ]
    )
    ctx = make_ctx(session=make_session(_respuestas()))
    ctx.llm = llm
    uploader = UniqueUploader()

    # Sin tema pedido: es la ruta donde el veto del auditor sí quema el intento y se agotan
    # los tres. Con tema pedido el motor degrada y entrega en el primero.
    result = await CrearPostLinkedInTool(uploader=uploader).run(
        ctx, {"destino": "personal", "con_imagen": False}
    )

    # El borrador SÍ se entrega, con `data` y con el archivo subido.
    assert isinstance(result.data, dict)
    assert uploader.calls, "el borrador tiene que llegar al almacén, no callarse"
    # El content lleva el aviso ADELANTE, para que la persona lo lea antes de
    # publicar; y el motivo del auditor.
    assert "no pasó el control editorial automático" in result.content
    assert "El texto no se sostiene." in result.content
    assert "revísalo con calma antes de publicarlo" in result.content


async def test_re_gate_si_el_auditor_reintroduce_primera_persona_la_repara_y_re_audita(
    make_ctx, make_session, monkeypatch
):
    """La lección crítica de `auditoria.py`, ahora REPARADA en vez de rechazada.

    Éste era el próximo fallo esperando, y es el gemelo exacto del que se arregló antes, un
    paso más adelante en el mismo pipeline: `auditar_hechos` reescribe con un prompt que no
    conoce ninguna regla de estilo, así que meter un "nos" o un "vimos" es lo ESPERADO. El
    código hacía `continue` sin intentar reparar, sin respetar `permisivo` y sin guardar el
    borrador -- era el ÚNICO camino del bucle que no dejaba candidato, así que tres intentos
    muriendo ahí devolvían cero post y el mismo mensaje que Ada acababa de dejar de ver.

    Aria en ese punto (`linkedin_content.py:1601-1605`) repara la primera persona y RE-AUDITA
    el texto reparado contra la fuente. Eso es lo que se prueba aquí: un solo pase del escritor,
    y el post sale.
    """

    async def _fake(http, consulta, **kwargs):  # noqa: ANN001
        return [_titular()]

    monkeypatch.setattr(redaccion, "titulares_frescos", _fake)
    # Largo de post real a propósito: lo que se mide acá es la PRIMERA PERSONA. Con un texto
    # corto saltaría antes el anti-muñón del auditor (`MIN_COPY_ENTREGABLE_CHARS`) y el test
    # dejaría de probar lo suyo.
    con_primera_persona = (
        "Vimos que el ajuste de tasas encarece el crédito para muchos clientes. En la práctica "
        "el pago mínimo cubre menos capital, y el saldo que parecía estable empieza a crecer "
        "solo."
    )
    llm = FakeLLM(
        [
            _borrador(),  # 0: el escritor
            _aprobado(),  # 1: el editor jefe (sin texto -> conserva el borrador)
            _auditado(con_primera_persona),  # 2: el auditor mete "Vimos"
            _TEXTO_OK_2,  # 3: el reparador de primera persona
            _auditado(_TEXTO_OK_2),  # 4: la RE-auditoría del texto reparado
        ]
    )
    ctx = make_ctx(session=make_session(_respuestas()))
    ctx.llm = llm

    result = await CrearPostLinkedInTool(uploader=UniqueUploader()).run(ctx, _args())

    assert result.data["copy"] == _TEXTO_OK_2
    # No se tiró el turno: un único pase del escritor, sin aviso de rescate.
    assert sum(1 for alias, _flags, _req in llm.calls if alias == "profundo") == 1
    assert "no pasó el control editorial automático" not in result.content
    # La llamada 3 es el reparador, y la 4 vuelve a auditar lo que devolvió.
    assert "elimina toda primera persona" in (llm.calls[3][2].system or "").lower()
    assert "auditor factual" in (llm.calls[4][2].system or "").lower()


async def test_cerrar_con_pregunta_ya_no_tumba_el_borrador(
    make_ctx, make_session, monkeypatch
):
    """Cerrar con una pregunta dejó de ser una violación dura.

    Edecán se contradecía consigo mismo: su propio `PROMPT_EDITOR_HUMANIZADOR` y el prompt del
    editor jefe piden explícitamente que el cierre pueda ser "una sola pregunta discutible y
    específica"... y `revisar` rechazaba cualquier texto cuya última línea terminara en "?". El
    escritor recibía instrucciones que garantizaban el rechazo. En Aria esto NO es un gate:
    es una firma de estructura con cooldown (`editorial.firma_estructura`), o sea se prohíbe
    sólo si el post anterior ya cerró igual.
    """

    async def _fake(http, consulta, **kwargs):  # noqa: ANN001
        return [_titular()]

    monkeypatch.setattr(redaccion, "titulares_frescos", _fake)
    con_pregunta = (
        "El ajuste de tasas encarece el crédito revolvente para quien arrastra saldo del mes "
        "anterior. ¿Compensa mantener ese saldo un mes más o conviene liquidarlo aunque duela "
        "el flujo?"
    )
    llm = FakeLLM([_borrador(con_pregunta), _aprobado(), _auditado(con_pregunta)])
    ctx = make_ctx(session=make_session(_respuestas()))
    ctx.llm = llm

    result = await CrearPostLinkedInTool(uploader=UniqueUploader()).run(ctx, _args())

    assert result.data["copy"] == con_pregunta
    assert "no pasó el control editorial automático" not in result.content
    assert sum(1 for alias, _flags, _req in llm.calls if alias == "profundo") == 1


async def test_el_cooldown_estructural_prohibe_repetir_la_forma_del_post_anterior(
    make_ctx, make_session, monkeypatch
):
    """Donde SÍ vive la prohibición de cerrar con pregunta: condicionada al historial.

    Port de `_firma_estructura`/`_instruccion_cooldown_estructura` (Aria,
    `linkedin_content.py:313-364`). El cooldown de `agenda` es de TEMA; esto ataca lo otro, que
    el feed se sienta igual -- tres posts seguidos abriendo con un porcentaje o cerrando con una
    pregunta -- aunque los temas sean distintos.
    """

    async def _fake(http, consulta, **kwargs):  # noqa: ANN001
        return [_titular()]

    monkeypatch.setattr(redaccion, "titulares_frescos", _fake)
    llm = FakeLLM([_borrador(), _aprobado(), _auditado()])
    estado = {
        "historial": [
            {"tema": "Post anterior", "firma": ["cierre_pregunta", "apertura_porcentaje"]}
        ]
    }
    ctx = make_ctx(session=make_session(_respuestas(agenda_estado=estado)))
    ctx.llm = llm

    await CrearPostLinkedInTool(uploader=UniqueUploader()).run(ctx, _args())

    escritor = llm.prompt(0)
    assert "COOLDOWN ESTRUCTURAL OBLIGATORIO" in escritor
    assert "PROHIBIDO cerrar con una pregunta" in escritor
    assert "PROHIBIDO abrir con un porcentaje" in escritor


async def test_la_primera_persona_se_repara_en_vez_de_tirar_el_borrador(
    make_ctx, make_session, monkeypatch
):
    """Antes esta prueba fijaba lo contrario: un borrador con primera persona se
    descartaba sin gastar una llamada, porque "los gates son gratis y el auditor
    cuesta". La cuenta estaba mal hecha. Reparar cuesta UNA llamada; NO reparar
    tira el turno entero (la investigación y las 2-3 llamadas del escritor ya
    gastadas) y le entrega al usuario un "escribí N veces y ninguna pasó".

    Es la pieza que el motor de Aria sí tenía
    (`_reescribir_sin_primera_persona`) y este no: allá el borrador se REPARA,
    acá solo se RECHAZABA. Con un escritor que insiste en "yo/mi/hice" —el fallo
    más común— todos los intentos morían en la misma puerta y el turno terminaba
    sin post.
    """

    async def _fake(http, consulta, **kwargs):  # noqa: ANN001
        return [_titular()]

    monkeypatch.setattr(redaccion, "titulares_frescos", _fake)
    con_primera_persona = "Aprendí que las tasas de tarjetas suben cuando el banco central aprieta."
    llm = FakeLLM(
        [
            # 0: el escritor mete primera persona
            _borrador(con_primera_persona),
            # 1: el editor jefe, que ahora es el primero en pasar y REESCRIBE el borrador.
            #    Su prompt de sistema le prohíbe la primera persona sin excepciones, así que
            #    normalmente es él quien la quita -- una llamada menos que reparar aparte.
            json.dumps({"publicable": True, "motivo": "Ok", "texto": _TEXTO_OK_2}),
            # 2: el auditor sobre el texto ya limpio
            _auditado(_TEXTO_OK_2),
        ]
    )
    ctx = make_ctx(session=make_session(_respuestas()))
    ctx.llm = llm

    result = await CrearPostLinkedInTool(uploader=UniqueUploader()).run(ctx, _args())

    # El post se entrega, con el texto ya reparado y sin aviso de rescate: pasó
    # los controles de verdad, no se colgó de la red de seguridad.
    assert result.data["copy"] == _TEXTO_OK_2
    assert "no pasó el control editorial automático" not in result.content
    # La segunda llamada es el EDITOR JEFE, y va con el prompt humanizador de system: sabe
    # exactamente qué señales se le van a medir después. Antes ese prompt existía en
    # `editorial.py` y no lo importaba NADIE: el editor juzgaba a ciegas.
    sistema_editor = (llm.calls[1][2].system or "").lower()
    assert "editor jefe" in sistema_editor
    assert "señales de escritura generada por ia" in sistema_editor
    assert "el candidato" in llm.prompt(1).lower()
    # Y solo hubo UN pase del escritor: no hizo falta reintentar.
    assert sum(1 for alias, _flags, _req in llm.calls if alias == "profundo") == 1


# ---------------------------------------------------------------------------
# 4. El gate de destino sigue corriendo ANTES de gastar en investigar.
# ---------------------------------------------------------------------------


async def test_pregunta_el_destino_antes_de_investigar_ni_de_escribir(
    make_ctx, make_session, monkeypatch
):
    llamadas: list[str] = []

    async def _fake(http, consulta, **kwargs):  # noqa: ANN001
        llamadas.append(consulta)
        return [_titular()]

    monkeypatch.setattr(redaccion, "titulares_frescos", _fake)
    session = make_session(
        [
            [
                {"platform": "linkedin_personal", "config": {"voice": "v"}, "version": 1},
                {"platform": "linkedin_acme", "config": {"voice": "v"}, "version": 1},
            ]
        ]
    )
    llm = FakeLLM([])
    ctx = make_ctx(session=session)
    ctx.llm = llm

    result = await CrearPostLinkedInTool(uploader=UniqueUploader()).run(
        ctx, {"tema": "tasas", "con_imagen": False}
    )

    bloque = next(b for b in result.presentation if b["type"] == "question")
    assert {opcion["label"] for opcion in bloque["options"]} == {"personal", "acme"}
    # Ni búsqueda ni modelo: el destino define la voz, y averiguarlo después es tirar el turno.
    assert llamadas == []
    assert llm.calls == []
    # Y se le dice que vuelva a ESTA herramienta, no a la que exige el texto ya escrito.
    assert "crear_post_linkedin" in result.content
    assert "No escribas tú el post" in result.content
    # El tema vuelve LITERAL en la instrucción: es el único dato que el modelo tendría que
    # arrastrar de una llamada a la otra, y olvidarlo no falla ruidosamente -- la segunda
    # llamada caería en la rotación editorial y escribiría un post correcto sobre otra cosa.
    assert "'tasas'" in result.content


async def test_sin_tema_la_instruccion_de_reintento_no_le_inventa_uno(
    make_ctx, make_session, monkeypatch
):
    """El caso 'escribe el post de hoy' con dos cuentas: al volver NO debe traerse un
    'tema' fabricado por el modelo, porque el usuario nunca pidió ninguno."""

    async def _fake(http, consulta, **kwargs):  # noqa: ANN001
        return [_titular()]

    monkeypatch.setattr(redaccion, "titulares_frescos", _fake)
    session = make_session(
        [
            [
                {"platform": "linkedin_personal", "config": {"voice": "v"}, "version": 1},
                {"platform": "linkedin_acme", "config": {"voice": "v"}, "version": 1},
            ]
        ]
    )
    ctx = make_ctx(session=session)
    ctx.llm = FakeLLM([])

    result = await CrearPostLinkedInTool(uploader=UniqueUploader()).run(ctx, {"con_imagen": False})

    assert "sin 'tema'" in result.content


async def test_con_un_solo_destino_de_organizacion_escribe_con_su_voz_sin_preguntar(
    make_ctx, make_session, monkeypatch
):
    """Con una sola cuenta no hay nada que preguntar -- pero sí hay un destino que usar.

    Regresión del bug que hacía inútil la promesa entera de la herramienta ("lo redacta con la
    voz de la cuenta correcta"): el gate callaba y encima devolvía el destino en blanco, así
    que el post se escribía con el perfil de `linkedin_personal` (vacío aquí) en vez de con la
    voz de Acme, y la card salía con `target: null`. No fallaba: escribía mal, en silencio.
    """

    async def _fake(http, consulta, **kwargs):  # noqa: ANN001
        return [_titular()]

    monkeypatch.setattr(redaccion, "titulares_frescos", _fake)
    llm = FakeLLM([_borrador(), _aprobado(), _auditado()])
    session = make_session(
        [
            # `destinos_configurados`: una sola fila, y no es la personal.
            [{"platform": "linkedin_acme", "config": {"voice": "Voz de Acme"}, "version": 1}],
            [_fila({"voice": "Voz de Acme"})],  # perfil editorial del destino resuelto
        ]
    )
    ctx = make_ctx(session=session)
    ctx.llm = llm

    args = _args()
    del args["destino"]
    result = await CrearPostLinkedInTool(uploader=UniqueUploader()).run(ctx, args)

    assert result.presentation[0]["type"] == "social_draft"
    assert result.presentation[0]["target"] == "acme"
    # El escritor recibió la voz de Acme, no un perfil sin configurar.
    assert "Voz: Voz de Acme" in llm.prompt(0)


async def test_sin_modelo_disponible_deriva_a_crear_contenido_social(make_ctx, make_session):
    ctx = make_ctx(session=make_session(_respuestas()))  # `make_ctx` deja `llm=None`

    result = await CrearPostLinkedInTool(uploader=UniqueUploader()).run(ctx, _args())

    assert "crear_contenido_social" in result.content
