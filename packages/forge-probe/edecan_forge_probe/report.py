"""Informe de la fase 0: `modelcard.json` y un Markdown que se lee sin ayuda.

El informe tiene un solo trabajo y no es enseñar números: es hacer **imposible
malinterpretar el veredicto**. Un NO-GO no dice «el modelo es malo», dice «el
diseño de la fase 1, tal y como está escrito, no se sostiene sobre este modelo,
y aquí está exactamente qué bloque hay que rediseñar antes de escribirlo».

Por eso el documento se ordena al revés de lo habitual: primero la decisión,
después el rediseño que esa decisión obliga, y sólo al final las mediciones que
la sostienen. Quien sólo lee la primera pantalla ya tiene lo que necesita.

Las consecuencias de cada umbral están ancladas a `docs/arquitectura-forge.md`:

- bloque 3, *Context Engine y memoria de largo plazo*;
- bloque 4, *Tool ABI, sistema de plugins y MCP*;
- bloque 6, *Agent Runtime, planificación y multi-agente*.

`SIN_DATO` se presenta igual de rojo que `FALLA` a propósito. Un umbral que
nadie midió no es un umbral aprobado, y el sesgo natural de quien lee una tabla
es tratar el hueco como si fuera un aprobado.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .modelcard import (
    UMBRALES_FASE_0,
    ArgProfile,
    EvaluacionUmbral,
    ModelCard,
    ProbeResult,
    Reliability,
    Umbral,
    Veredicto,
)
from .runner import Contabilidad, ResultadoEjecucion

__all__ = [
    "REDISENOS",
    "Rediseno",
    "escribir_informe",
    "escribir_modelcard",
    "render_markdown",
]


class Rediseno(BaseModel):
    """Qué hay que cambiar en el diseño si un umbral concreto no se cumple."""

    bloque: int
    titulo: str
    consecuencia: str
    """Qué deja de ser cierto en el diseño escrito."""
    accion: str
    """Qué se hace en su lugar, en imperativo y sin adjetivos."""


REDISENOS: dict[str, Rediseno] = {
    "usable_context_tokens": Rediseno(
        bloque=3,
        titulo="Context Engine y memoria de largo plazo",
        consecuencia=(
            "El Context Engine está escrito asumiendo que se puede montar una ventana "
            "grande y confiar en que el modelo la recuerde entera. Si el contexto útil "
            "medido está por debajo del umbral, esa suposición es falsa: el modelo "
            "seguirá aceptando el prompt y respondiendo con seguridad sobre material "
            "que ya no recuerda, que es el modo de fallo más caro que existe porque no "
            "levanta ningún error."
        ),
        accion=(
            "Convertir el contexto en un recurso presupuestado, no en un cajón: fijar el "
            "techo de ventana al valor medido y no al anunciado; obligar a recuperación "
            "selectiva (índice + fragmentos) en lugar de volcado de archivos; y hacer que "
            "toda tarea larga se resuelva por relevo con estado explícito —un resumen "
            "estructurado que sobreviva al cambio de ventana— en vez de por acumulación. "
            "El presupuesto de contexto pasa a ser parte del contrato de la tarea."
        ),
    ),
    "native_tools.code_blob.lower_95": Rediseno(
        bloque=4,
        titulo="Tool ABI, sistema de plugins y MCP",
        consecuencia=(
            "El ABI de herramientas da por hecho que se puede meter un parche completo "
            "dentro de un argumento JSON de una llamada nativa. Es lo que hace "
            "`apply_patch` en cada edición. Si la fiabilidad con `code_blob` no llega al "
            "umbral, cada edición del sistema tiene esa probabilidad de salir con el "
            "código truncado, con las comillas mal escapadas o con la indentación rota."
        ),
        accion=(
            "Sacar el código del argumento: la herramienta recibe un identificador y el "
            "cuerpo viaja por un canal aparte (bloque delimitado en el mensaje, o "
            "escritura en dos fases contenido-luego-commit). Reducir el tamaño de la "
            "unidad de edición —hunks pequeños en vez de archivos— y hacer obligatoria la "
            "verificación sintáctica post-escritura con reintento acotado. Si además hay "
            "que renunciar a herramientas nativas, el fallback de herramientas por prompt "
            "deja de ser opcional y pasa a ser un camino de primera clase con su propia "
            "medición."
        ),
    ),
    "throughput_tps": Rediseno(
        bloque=6,
        titulo="Agent Runtime, planificación y multi-agente",
        consecuencia=(
            "El runtime de agentes está pensado para bucles largos con muchos turnos. Por "
            "debajo del umbral de tokens por segundo, una misión de decenas de turnos deja "
            "de ser interactiva y el coste de oportunidad de cada turno desperdiciado se "
            "dispara."
        ),
        accion=(
            "Bajar el número de turnos por tarea: planificación previa explícita, lotes de "
            "ediciones por turno en vez de una por turno, y paralelismo entre sub-agentes "
            "independientes en lugar de un único bucle secuencial. Recortar la longitud de "
            "las salidas: el razonamiento se factura y se paga en latencia, así que hay que "
            "acotarlo donde el parámetro lo permita."
        ),
    ),
    "ttft_p95_s": Rediseno(
        bloque=6,
        titulo="Agent Runtime, planificación y multi-agente",
        consecuencia=(
            "Con el tiempo hasta el primer token por encima del techo, cada turno arranca "
            "con una pausa muerta. En un bucle de agente ese coste se multiplica por el "
            "número de turnos y se lo come entero el usuario que mira la pantalla."
        ),
        accion=(
            "Hacer que el primer token deje de estar en el camino crítico: streaming visible "
            "desde el primer byte, trabajo especulativo mientras se espera (lecturas e "
            "indexado adelantados), y menos turnos por tarea. Revisar también la estabilidad "
            "del prefijo: un prompt cuyo prefijo cambia en cada turno pierde la caché y paga "
            "el arranque completo cada vez."
        ),
    ),
    "bench_success_rate": Rediseno(
        bloque=6,
        titulo="Agent Runtime, planificación y multi-agente",
        consecuencia=(
            "Es el umbral que más manda. Si el modelo no resuelve la mayoría de las tareas "
            "reales del banco sin ayuda, la autonomía por defecto que asume el diseño no es "
            "defendible: el sistema propondrá cambios equivocados con la misma confianza con "
            "la que propone los correctos."
        ),
        accion=(
            "Mover el punto de control: la unidad de trabajo autónoma pasa a ser la sub-tarea "
            "corta con criterio ejecutable y aprobación humana entre etapas, no la misión "
            "completa. Los jueces automáticos arrancan como consultivos, no vinculantes. Y el "
            "banco se convierte en la puerta de entrada de cada cambio del runtime: sin "
            "mejora medida sobre el banco, no se acepta el cambio."
        ),
    ),
    "max_tools_effective": Rediseno(
        bloque=4,
        titulo="Tool ABI, sistema de plugins y MCP",
        consecuencia=(
            "No es bloqueante, pero fija el techo de la superficie del ABI. Si el número de "
            "herramientas que se pueden ofrecer sin que se derrumbe la selección es bajo, un "
            "catálogo de plugins abierto es una fuente de errores de selección, no una "
            "funcionalidad."
        ),
        accion=(
            "Ofrecer herramientas por perfil y por fase de la tarea en vez de todas a la vez: "
            "un catálogo pequeño y activo, con conmutación explícita de conjunto. El registro "
            "de plugins sigue siendo abierto; lo que se acota es cuántos entran en el prompt "
            "a la vez."
        ),
    ),
}

_ETIQUETA: dict[Veredicto, str] = {
    Veredicto.PASA: "PASA",
    Veredicto.JUSTO: "JUSTO (riesgo)",
    Veredicto.FALLA: "FALLA",
    Veredicto.SIN_DATO: "SIN DATO",
}


# --------------------------------------------------------------------------- #
# Salidas en disco
# --------------------------------------------------------------------------- #


def escribir_modelcard(card: ModelCard, ruta: Path) -> Path:
    """Serializa la tarjeta. Es la salida que consume el resto del sistema."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(card.model_dump_json(indent=2), encoding="utf-8")
    return ruta


def escribir_informe(
    card: ModelCard,
    directorio: Path,
    *,
    contabilidad: Contabilidad | None = None,
    ejecucion: ResultadoEjecucion | None = None,
    umbrales: tuple[Umbral, ...] = UMBRALES_FASE_0,
) -> tuple[Path, Path]:
    """Escribe `modelcard.json` e `informe.md`. Devuelve ambas rutas."""
    directorio.mkdir(parents=True, exist_ok=True)
    ruta_json = escribir_modelcard(card, directorio / "modelcard.json")
    ruta_md = directorio / "informe.md"
    ruta_md.write_text(
        render_markdown(card, contabilidad=contabilidad, ejecucion=ejecucion, umbrales=umbrales),
        encoding="utf-8",
    )
    return ruta_json, ruta_md


# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #


def render_markdown(
    card: ModelCard,
    *,
    contabilidad: Contabilidad | None = None,
    ejecucion: ResultadoEjecucion | None = None,
    umbrales: tuple[Umbral, ...] = UMBRALES_FASE_0,
) -> str:
    """Compone el informe completo en Markdown."""
    evaluaciones = card.evaluar_umbrales(umbrales)
    go = not any(e.bloquea for e in evaluaciones)
    partes = [
        _banner(go, evaluaciones),
        _seccion_identidad(card, ejecucion),
        _seccion_rediseno(go, evaluaciones),
        _tabla_umbrales(evaluaciones),
        _seccion_contexto(card),
        _seccion_tools(card),
        _seccion_razonamiento(card, contabilidad),
        _seccion_coste(card, contabilidad),
        _seccion_evidencia(card),
        _seccion_notas(card),
    ]
    return "\n\n".join(p.strip() for p in partes if p and p.strip()) + "\n"


def _banner(go: bool, evaluaciones: Sequence[EvaluacionUmbral]) -> str:
    palabra = "G O" if go else "N O - G O"
    marco = "+" + "-" * 58 + "+"
    centrado = f"|{palabra.center(58)}|"
    bloqueantes = [e for e in evaluaciones if e.bloquea]
    if go:
        resumen = (
            "Ningún umbral bloqueante falla ni quedó sin medir. El diseño de la fase 1 "
            "se sostiene sobre este modelo **con las mediciones de esta tarjeta**, no en "
            "general: cualquier cambio de modelo, de versión o de proveedor invalida el "
            "veredicto y obliga a volver a sondear."
        )
    else:
        causas = ", ".join(f"`{e.umbral.clave}` ({_ETIQUETA[e.veredicto]})" for e in bloqueantes)
        resumen = (
            f"Bloquean {len(bloqueantes)} umbral(es): {causas}.\n\n"
            "Esto **no** dice que el modelo sea malo. Dice que los bloques 3, 4 y 6 del "
            "diseño, tal y como están escritos hoy, descansan sobre una suposición que la "
            "medición no respalda. La sección siguiente dice cuál y qué se hace en su lugar."
        )
    return f"# Veredicto de la fase 0\n\n```\n{marco}\n{centrado}\n{marco}\n```\n\n{resumen}"


def _seccion_identidad(card: ModelCard, ejecucion: ResultadoEjecucion | None) -> str:
    filas = [
        ("Modelo", f"`{card.modelo}`"),
        ("Proveedor", card.proveedor),
        ("Medido en", card.medido_en.isoformat()),
        ("Revisión de sonda", f"`{card.revision_sonda}`"),
    ]
    if card.ventana_anunciada is not None or card.usable_context_tokens is not None:
        anunciada = _num(card.ventana_anunciada)
        util = _num(card.usable_context_tokens)
        filas.append(("Ventana anunciada", f"{anunciada} tokens"))
        filas.append(("Contexto útil medido", f"{util} tokens"))
        if card.ventana_anunciada and card.usable_context_tokens:
            frac = card.usable_context_tokens / card.ventana_anunciada
            filas.append(("Útil / anunciada", f"{frac:.0%}"))
    if ejecucion is not None:
        filas.append(("Sondas ejecutadas", ", ".join(ejecucion.ejecutadas) or "ninguna"))
        if ejecucion.reutilizadas:
            filas.append(("Reutilizadas de disco", ", ".join(ejecucion.reutilizadas)))
        if ejecucion.pendientes:
            filas.append(("Sin correr", ", ".join(ejecucion.pendientes)))
        if ejecucion.corte:
            filas.append(("Corte", f"**{ejecucion.corte}** — {ejecucion.motivo_corte or ''}"))
    cuerpo = "\n".join(f"| {k} | {v} |" for k, v in filas)
    aviso = ""
    if ejecucion is not None and not ejecucion.completa:
        aviso = (
            "\n\n> La ejecución quedó INCOMPLETA. Los umbrales sin medir aparecen como "
            "`SIN DATO` y bloquean igual que un fallo: es lo correcto, no un defecto del "
            "informe."
        )
    return f"## Qué se midió\n\n| | |\n| --- | --- |\n{cuerpo}{aviso}"


def _seccion_rediseno(go: bool, evaluaciones: Sequence[EvaluacionUmbral]) -> str:
    """La sección que justifica que exista la fase 0."""
    dudosos = (Veredicto.FALLA, Veredicto.SIN_DATO, Veredicto.JUSTO)
    problemas = [e for e in evaluaciones if e.veredicto in dudosos]
    if not problemas:
        return (
            "## Qué habría que rediseñar\n\n"
            "Nada. Todos los umbrales pasan con margen. Conviene, aun así, volver a correr "
            "el barrido cuando cambie la versión del modelo: la tarjeta es una foto, no una "
            "propiedad del proveedor."
        )
    encabezado = (
        "## Qué habría que rediseñar\n\n"
        "Una línea por umbral no aprobado, anclada al bloque de "
        "`docs/arquitectura-forge.md` que deja de ser válido."
        if not go
        else "## Riesgos a vigilar\n\nNingún umbral bloquea, pero estos quedaron sin margen o "
        "sin medir. Se documenta el rediseño que obligarían si empeoran."
    )
    trozos = [encabezado]
    orden = {Veredicto.FALLA: 0, Veredicto.SIN_DATO: 1, Veredicto.JUSTO: 2}
    for ev in sorted(problemas, key=lambda e: (orden.get(e.veredicto, 9), e.umbral.clave)):
        red = REDISENOS.get(ev.umbral.clave)
        titulo = f"### `{ev.umbral.clave}` — {_ETIQUETA[ev.veredicto]}"
        if not ev.umbral.bloqueante:
            titulo += " *(no bloqueante)*"
        cuerpo = [
            titulo,
            f"**Medido:** {_valor(ev.valor)} · **Umbral:** {_texto_umbral(ev.umbral)}",
            f"*{ev.umbral.descripcion}*",
        ]
        if ev.veredicto is Veredicto.SIN_DATO:
            cuerpo.append(
                "**Sin dato.** No hay medición, así que no hay veredicto: corre la sonda "
                "correspondiente antes de decidir nada. Un hueco nunca se interpreta como "
                "un aprobado."
            )
        if red is not None:
            cuerpo.append(f"**Bloque {red.bloque} — {red.titulo}.** {red.consecuencia}")
            cuerpo.append(f"**Qué se hace en su lugar:** {red.accion}")
        else:
            cuerpo.append(
                "No hay consecuencia de diseño registrada para este umbral: añádela a "
                "`REDISENOS` en `report.py` antes de tomar una decisión con él."
            )
        trozos.append("\n\n".join(cuerpo))
    return "\n\n".join(trozos)


def _tabla_umbrales(evaluaciones: Sequence[EvaluacionUmbral]) -> str:
    filas = [
        "| Umbral | Medido | Criterio | Veredicto | Bloquea |",
        "| --- | --- | --- | --- | --- |",
    ]
    for ev in evaluaciones:
        filas.append(
            f"| `{ev.umbral.clave}` | {_valor(ev.valor)} | {_texto_umbral(ev.umbral)} "
            f"| {_ETIQUETA[ev.veredicto]} | {'sí' if ev.bloquea else 'no'} |"
        )
    return "## Umbrales de la fase 0\n\n" + "\n".join(filas)


def _seccion_contexto(card: ModelCard) -> str:
    """Curva de contexto útil: la tesis entera de la fase 0 en una tabla."""
    curva = _buscar_curva(card.resultados)
    cabecera = "## Curva de contexto útil\n\n"
    tesis = (
        "El contexto anunciado no es el contexto útil. Esta curva mide a qué profundidad "
        "el modelo todavía recupera y razona sobre lo que se le metió.\n"
    )
    if not curva:
        return (
            cabecera
            + tesis
            + "\n**Sin datos.** Ninguna sonda publicó `detalle['curva']`. Sin esta curva el "
            "techo de ventana del bloque 3 es una suposición."
        )
    filas = [
        "| Profundidad (tokens) | Aciertos | Media | Límite inferior 95 % | |",
        "| ---: | ---: | ---: | ---: | :--- |",
    ]
    for punto in curva:
        barra = "#" * int(round(punto["lower_95"] * 20))
        aciertos = (
            f"{punto['successes']}/{punto['trials']}" if punto.get("trials") is not None else "—"
        )
        media = f"{punto['mean']:.2f}" if punto.get("mean") is not None else "—"
        filas.append(
            f"| {punto['tokens']:,} | {aciertos} | {media} | {punto['lower_95']:.2f} "
            f"| `{barra:<20}` |"
        )
    nota = ""
    if card.usable_context_tokens is not None:
        nota = (
            f"\n\nContexto útil declarado por la sonda: **{card.usable_context_tokens:,} "
            "tokens** (última profundidad cuyo límite inferior al 95 % sigue por encima del "
            "criterio de la sonda)."
        )
    return cabecera + tesis + "\n" + "\n".join(filas) + nota


def _seccion_tools(card: ModelCard) -> str:
    cabecera = "## Tool-calling por perfil de argumento\n\n"
    razon = (
        'La fiabilidad del tool-calling no es un número: acertar con `{"path": "a.py"}` no '
        "predice acertar con 40 líneas de código dentro de un campo JSON. El segundo caso es "
        "`apply_patch`, y es el que decide.\n"
    )
    if not card.native_tools:
        return (
            cabecera
            + razon
            + "\n**Sin datos.** No se midió ningún perfil. El umbral `native_tools.code_blob"
            ".lower_95` queda SIN DATO y bloquea."
        )
    filas = [
        "| Perfil | n | Media | Límite inferior 95 % | vs. 0,90 |",
        "| --- | ---: | ---: | ---: | :--- |",
    ]
    for perfil in ArgProfile:
        rel = card.native_tools.get(perfil)
        if rel is None:
            filas.append(f"| `{perfil.value}` | — | — | — | sin datos |")
            continue
        estado = "pasa" if rel.lower_95 >= 0.90 else "FALLA"
        marca = " ← el que decide" if perfil is ArgProfile.CODE_BLOB else ""
        filas.append(
            f"| `{perfil.value}` | {rel.trials} | {rel.mean:.2f} | {rel.lower_95:.2f} "
            f"| {estado}{marca} |"
        )
    extras = []
    if card.structured_output is not None:
        extras.append(f"- Salida estructurada: {card.structured_output}")
    if card.max_tools_effective is not None:
        extras.append(f"- Herramientas efectivas antes del derrumbe: {card.max_tools_effective}")
    if card.max_schema_bytes is not None:
        extras.append(f"- Esquema máximo aceptado: {card.max_schema_bytes:,} bytes")
    cola = ("\n\n" + "\n".join(extras)) if extras else ""
    return cabecera + razon + "\n" + "\n".join(filas) + cola


def _seccion_razonamiento(card: ModelCard, contabilidad: Contabilidad | None) -> str:
    """Sobrecarga de razonamiento: se factura a precio de salida y no se ve."""
    cabecera = "## Sobrecarga de razonamiento\n\n"
    explicacion = (
        "El razonamiento va en `message.reasoning_content`, aparte de `message.content`, "
        "consume presupuesto de salida y se factura a precio de salida. Con `max_tokens` "
        "corto la respuesta llega con el contenido VACÍO y se cobra igual: es un modo de "
        "fallo de primera clase, no una curiosidad.\n"
    )
    filas: list[str] = []
    if contabilidad is not None and contabilidad.tokens_salida:
        ratio = contabilidad.ratio_razonamiento
        filas.append(
            f"- Agregado de la ejecución: {contabilidad.tokens_razonamiento:,} tokens de "
            f"razonamiento sobre {contabilidad.tokens_salida:,} de salida"
            + (f" (ratio razonamiento/contenido {ratio:.2f})." if ratio is not None else ".")
        )
        if contabilidad.tokens_razonamiento == 0:
            filas.append(
                "- El proveedor no desglosó `reasoning_tokens`: el ratio no se puede medir "
                "desde la factura y hay que obtenerlo comparando longitudes por respuesta."
            )
    for r in card.resultados:
        for clave in ("ratio_razonamiento", "razonamiento_por_longitud", "reasoning_effort"):
            if clave in r.detalle:
                filas.append(f"- `{r.probe}` → `{clave}`: {_compacto(r.detalle[clave])}")
    if not filas:
        return (
            cabecera
            + explicacion
            + "\n**Sin datos.** Ninguna sonda ni la contabilidad reportaron tokens de "
            "razonamiento. Toda estimación de coste de salida es, por tanto, una cota "
            "inferior."
        )
    return cabecera + explicacion + "\n" + "\n".join(filas)


def _seccion_coste(card: ModelCard, contabilidad: Contabilidad | None) -> str:
    cabecera = "## Coste\n\n"
    if contabilidad is None:
        return ""
    lineas = [
        f"- Llamadas: {contabilidad.llamadas:,}",
        f"- Tokens de entrada: {contabilidad.tokens_entrada:,} "
        f"(cacheados {contabilidad.tokens_entrada_cacheados:,})",
        f"- Tokens de salida: {contabilidad.tokens_salida:,}",
    ]
    if contabilidad.neurons:
        lineas.append(f"- Neuronas facturadas: {contabilidad.neurons:,.2f}")
    coste = contabilidad.coste_usd
    if coste is None:
        lineas.append(
            "- **Coste: sin calcular.** No se declararon precios "
            "(`--precio-entrada` / `--precio-salida`), así que sólo hay tokens. Inventar "
            "una tarifa aquí sería inventar una medición."
        )
    else:
        sufijo = " (cota superior)" if contabilidad.coste_es_cota_superior else ""
        # Los precios se leen de la contabilidad y no de la tarjeta: son los que
        # de verdad produjeron el número de arriba.
        entrada = contabilidad.precio_entrada_usd_mtok or card.precio_entrada_usd_mtok
        salida = contabilidad.precio_salida_usd_mtok or card.precio_salida_usd_mtok
        cacheada = contabilidad.precio_cacheado_usd_mtok
        lineas.append(f"- **Coste de este barrido: {coste:.4f} USD{sufijo}**")
        lineas.append(
            f"- Precios usados: entrada {entrada} USD/M, salida {salida} USD/M, "
            f"entrada cacheada {cacheada if cacheada is not None else 'no declarada'} USD/M"
        )
    if contabilidad.tokens_entrada:
        frac = contabilidad.tokens_entrada_cacheados / contabilidad.tokens_entrada
        lineas.append(
            f"- Acierto de caché de prefijo: {frac:.0%}. La entrada cacheada cuesta varias "
            "veces menos que la fría, así que la estabilidad del prefijo del prompt es una "
            "decisión económica, no de estilo."
        )
    if contabilidad.por_sonda:
        lineas.append("")
        lineas.append("| Sonda | Llamadas | Entrada | Cacheados | Salida | Razonamiento |")
        lineas.append("| --- | ---: | ---: | ---: | ---: | ---: |")
        for nombre, uso in contabilidad.por_sonda.items():
            lineas.append(
                f"| `{nombre}` | {uso.llamadas} | {uso.entrada:,} | {uso.cacheados:,} "
                f"| {uso.salida:,} | {uso.razonamiento:,} |"
            )
    return cabecera + "\n".join(lineas)


def _seccion_evidencia(card: ModelCard) -> str:
    if not card.resultados:
        return ""
    filas = [
        "| Sonda | Estado | Valor | Duración | Evidencia |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for r in card.resultados:
        estado = "ok" if r.ok else f"FALLÓ — {r.error or 'sin detalle'}"
        if r.valor is not None:
            valor = _valor(r.valor)
        else:
            valor = str(r.reliability) if r.reliability else "—"
        pruebas = ", ".join(f"`{e}`" for e in r.evidencia) or "—"
        filas.append(f"| `{r.probe}` | {estado} | {valor} | {r.duracion_s:.1f} s | {pruebas} |")
    return (
        "## Evidencia\n\nCada número de arriba se audita hasta la petición real por estas "
        "trazas. Una medición sin evidencia no entra en la tarjeta.\n\n" + "\n".join(filas)
    )


def _seccion_notas(card: ModelCard) -> str:
    if not card.notas:
        return ""
    return "## Notas\n\n" + "\n".join(f"- {n}" for n in card.notas)


# --------------------------------------------------------------------------- #
# Utilidades de presentación
# --------------------------------------------------------------------------- #


def _buscar_curva(resultados: Sequence[ProbeResult]) -> list[dict[str, Any]]:
    """Normaliza la curva de contexto publicada por la sonda correspondiente.

    Se aceptan dos formas por punto: `{tokens, successes, trials}` —de la que se
    deriva el intervalo de Wilson igual que en el resto del sistema— o
    `{tokens, lower_95}` si la sonda ya lo calculó. Se ignoran los puntos que no
    encajen en ninguna en vez de adivinar qué querían decir.
    """
    for r in resultados:
        crudo = r.detalle.get("curva")
        if not isinstance(crudo, (list, tuple)) or not crudo:
            continue
        puntos: list[dict[str, Any]] = []
        for item in crudo:
            if not isinstance(item, dict):
                continue
            tokens = item.get("tokens", item.get("profundidad_tokens"))
            if tokens is None:
                continue
            successes = item.get("successes", item.get("aciertos"))
            trials = item.get("trials", item.get("intentos"))
            if successes is not None and trials:
                rel = Reliability(successes=int(successes), trials=int(trials))
                puntos.append(
                    {
                        "tokens": int(tokens),
                        "successes": rel.successes,
                        "trials": rel.trials,
                        "mean": rel.mean,
                        "lower_95": rel.lower_95,
                    }
                )
            elif item.get("lower_95") is not None:
                puntos.append(
                    {
                        "tokens": int(tokens),
                        "successes": None,
                        "trials": None,
                        "mean": item.get("mean"),
                        "lower_95": float(item["lower_95"]),
                    }
                )
        if puntos:
            return sorted(puntos, key=lambda p: p["tokens"])
    return []


def _texto_umbral(umbral: Umbral) -> str:
    if umbral.minimo is not None:
        return f"≥ {_num(umbral.minimo)}"
    if umbral.maximo is not None:
        return f"≤ {_num(umbral.maximo)}"
    return "—"


def _valor(valor: float | None) -> str:
    if valor is None:
        return "**sin medir**"
    return _num(valor)


def _num(valor: float | int | None) -> str:
    if valor is None:
        return "—"
    if isinstance(valor, int) or float(valor).is_integer():
        return f"{int(valor):,}"
    return f"{valor:.3f}".rstrip("0").rstrip(".")


def _compacto(valor: Any) -> str:
    if isinstance(valor, (dict, list)):
        return f"`{json.dumps(valor, ensure_ascii=False, default=str)}`"
    return f"`{valor}`"
