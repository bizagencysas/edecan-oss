"""CLI de la fase 0 de Forge: `uv run python -m edecan_forge_probe`.

Cuatro subcomandos, en el orden en que se usan:

    humo      Una llamada barata que responde «¿puedo medir hoy?». Es LO PRIMERO
              que se ejecuta: separa «falta el token», «el modelo no existe» y
              «existe pero esta cuenta no tiene acceso», que son tres tardes
              distintas si se confunden.
    sondear   Corre el barrido con presupuesto, deadline y reanudación.
    informe   Recompone `modelcard.json` e `informe.md` desde la evidencia en
              disco, sin gastar un token.
    banco     `--listar` enseña las tareas; `--verificar` comprueba que cada
              criterio FALLA hoy sobre el repo limpio.

Códigos de salida, pensados para encadenar en un script:

    0  todo bien (y, en `sondear`/`informe`, veredicto GO)
    1  error de entorno o de ejecución
    2  uso incorrecto de la línea de órdenes (lo pone argparse)
    3  veredicto NO-GO
    4  el banco tiene criterios que ya se cumplen hoy: no mide nada
    5  falta credencial o cuenta
    6  el modelo no existe o esta cuenta no tiene acceso

El token nunca se imprime, ni entero ni parcial, ni en los mensajes de error.

Nota de instalación: `packages/forge-probe` ya es miembro del workspace de uv, así
que desde la raíz del monorepo basta con

    uv sync --all-packages
    uv run python -m edecan_forge_probe humo

`uv sync` a secas sincroniza sólo el paquete raíz, que no depende de ningún
miembro: hace falta `--all-packages` para que los miembros queden instalados.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from .modelcard import UMBRALES_FASE_0, BenchTask, ModelCard
from .report import escribir_informe
from .runner import (
    MODELO_POR_DEFECTO,
    PROVEEDOR_POR_DEFECTO,
    RAICES_POR_DEFECTO,
    BancoNoDisponible,
    Contabilidad,
    OpcionesRunner,
    ResultadoEjecucion,
    cargar_banco,
    componer_modelcard,
    descubrir_sondas,
    ejecutar,
    leer_resultados_guardados,
    modelo_por_defecto,
    prueba_de_humo,
    verificar_banco,
)

EVIDENCIA_POR_DEFECTO = Path(".forge-probe")

CODIGO_OK = 0
CODIGO_ERROR = 1
CODIGO_NO_GO = 3
CODIGO_BANCO_INUTIL = 4
CODIGO_SIN_CREDENCIAL = 5
CODIGO_SIN_ACCESO = 6

_CODIGOS_HUMO = {
    "falta_token": CODIGO_SIN_CREDENCIAL,
    "falta_cuenta": CODIGO_SIN_CREDENCIAL,
    "credencial_invalida": CODIGO_SIN_CREDENCIAL,
    "sin_acceso": CODIGO_SIN_ACCESO,
    "modelo_inexistente": CODIGO_SIN_ACCESO,
}


# --------------------------------------------------------------------------- #
# Entorno
# --------------------------------------------------------------------------- #


def cargar_dotenv(ruta: Path, *, entorno: dict[str, str] | None = None) -> list[str]:
    """Carga un `.env` sin pisar lo que ya está exportado. Devuelve las claves leídas.

    Se implementa a mano —seis líneas— en vez de añadir una dependencia sólo para
    esto. Nunca se registra ni se imprime el VALOR de ninguna clave.
    """
    env = entorno if entorno is not None else os.environ
    if not ruta.is_file():
        return []
    leidas: list[str] = []
    for linea in ruta.read_text(encoding="utf-8", errors="replace").splitlines():
        limpia = linea.strip()
        if not limpia or limpia.startswith("#") or "=" not in limpia:
            continue
        clave, _, valor = limpia.partition("=")
        clave = clave.removeprefix("export ").strip()
        valor = valor.strip().strip('"').strip("'")
        if not clave or clave in env:
            continue
        env[clave] = valor
        leidas.append(clave)
    return leidas


def buscar_dotenv(desde: Path | None = None) -> Path | None:
    """Sube directorios buscando un `.env`. Devuelve `None` si no hay."""
    actual = (desde or Path.cwd()).resolve()
    for candidato in [actual, *actual.parents]:
        ruta = candidato / ".env"
        if ruta.is_file():
            return ruta
    return None


def huella_de_las_sondas() -> str:
    """Huella del código de sonda: 12 hex del hash de los fuentes de `probes/`.

    Existe porque `+sucio` NO es una huella. El runner reutiliza la evidencia en
    disco cuando la revisión coincide, y con un marcador binario de suciedad dos
    estados distintos del árbol producen la misma cadena: se edita una sonda, se
    vuelve a lanzar, y se reusan en silencio los resultados de ANTES del cambio.

    Ocurrió de verdad: se corrigió un caso ambiguo de la sonda de tool-calling y
    la siguiente ejecución devolvió los mismos 14 fallos, porque la revisión no
    había cambiado. Una medición silenciosamente obsoleta es peor que ninguna —
    parece un dato.

    Con la huella del código fuente, editar cualquier sonda invalida su propia
    evidencia automáticamente, sin depender de que nadie se acuerde de subir un
    número de versión a mano.
    """
    directorio = Path(__file__).resolve().parent / "probes"
    hasher = hashlib.blake2b(digest_size=6)
    for ruta in sorted(directorio.glob("*.py")):
        hasher.update(ruta.name.encode("utf-8"))
        hasher.update(ruta.read_bytes())
    return hasher.hexdigest()


def revision_por_defecto() -> str:
    """Identifica el código de sonda que produjo los números.

    Sin esto dos `ModelCard` no son comparables, así que el valor por defecto es
    el commit real, con sufijo `+sucio` si el árbol tiene cambios sin confirmar
    —una medición sobre código sin confirmar no es reproducible y hay que verlo
    en la tarjeta— **y la huella de los fuentes de las sondas**, que es lo que de
    verdad decide si la evidencia guardada sigue valiendo.
    """
    raiz = Path(__file__).resolve().parents[3]
    try:
        commit = subprocess.run(  # noqa: S603 - argv fijo
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=raiz,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if commit.returncode != 0:
            return f"desconocida.{huella_de_las_sondas()}"
        revision = commit.stdout.strip() or "desconocida"
        estado = subprocess.run(  # noqa: S603 - argv fijo
            ["git", "status", "--porcelain"],
            cwd=raiz,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if estado.returncode == 0 and estado.stdout.strip():
            revision += "+sucio"
        return f"{revision}.{huella_de_las_sondas()}"
    except (OSError, subprocess.SubprocessError):
        return f"desconocida.{huella_de_las_sondas()}"


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #


def _añadir_precios(sub: argparse.ArgumentParser) -> None:
    sub.add_argument(
        "--precio-entrada",
        type=float,
        default=None,
        metavar="USD_M",
        help=(
            "USD por millón de tokens de entrada. Workers AI no lo expone en la respuesta, "
            "así que entra a mano. Verificado el 27-07-2026 para kimi-k2.7-code: 0.95. "
            "Sin este dato el coste queda en None."
        ),
    )
    sub.add_argument(
        "--precio-salida",
        type=float,
        default=None,
        metavar="USD_M",
        help="USD por millón de tokens de salida. Verificado para kimi-k2.7-code: 4.00.",
    )
    sub.add_argument(
        "--precio-cacheada",
        type=float,
        default=None,
        metavar="USD_M",
        help=(
            "USD por millón de tokens de entrada servidos desde caché de prefijo. "
            "Verificado para kimi-k2.7-code: 0.19. Si se omite, la entrada cacheada se "
            "factura al precio de entrada fría y el coste sale como cota superior."
        ),
    )


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m edecan_forge_probe",
        description=(
            "Fase 0 de Forge: mide de qué es capaz de verdad el modelo antes de escribir "
            "el sistema encima."
        ),
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Archivo .env a cargar. Por defecto se busca hacia arriba desde el cwd.",
    )
    parser.add_argument(
        "--sin-env", action="store_true", help="No cargar ningún .env; usar sólo el entorno."
    )
    subs = parser.add_subparsers(dest="comando", required=True)

    humo = subs.add_parser(
        "humo",
        help="Valida credencial y modelo con UNA llamada barata. Ejecútalo primero.",
    )
    humo.add_argument("--modelo", default=None, help=f"Por defecto {MODELO_POR_DEFECTO}.")
    humo.add_argument("--timeout-s", type=float, default=60.0)
    humo.add_argument("--json", action="store_true", help="Salida legible por máquina.")

    sondear = subs.add_parser("sondear", help="Corre el barrido de sondas y compone la ModelCard.")
    sondear.add_argument("--modelo", default=None)
    sondear.add_argument("--proveedor", default=PROVEEDOR_POR_DEFECTO)
    sondear.add_argument(
        "--revision",
        default=None,
        help="Revisión del código de sonda. Por defecto, el commit actual del repo.",
    )
    sondear.add_argument("--evidencia", type=Path, default=EVIDENCIA_POR_DEFECTO)
    sondear.add_argument("--salida", type=Path, default=None, help="Dónde dejar el informe.")
    sondear.add_argument(
        "--solo",
        default="",
        metavar="a,b",
        help="Ejecución parcial: nombres de sonda o grupos separados por comas.",
    )
    sondear.add_argument(
        "--rehacer",
        action="store_true",
        help="Ignora la evidencia en disco y vuelve a medir (y a pagar) todo.",
    )
    sondear.add_argument(
        "--presupuesto-usd",
        type=float,
        default=None,
        help="Tope de gasto. Exige --precio-entrada y --precio-salida para poder aplicarse.",
    )
    sondear.add_argument(
        "--n",
        type=int,
        default=None,
        metavar="MUESTRAS",
        help=(
            "Recorta el número de muestras por serie para una pasada barata. Las series de "
            "tool-calling se SALTAN por debajo de 20 intentos: con menos, el límite inferior "
            "de Wilson es tan ancho que la medición no decidiría nada."
        ),
    )
    sondear.add_argument("--deadline-s", type=float, default=None, help="Reloj de pared total.")
    sondear.add_argument("--timeout-sonda-s", type=float, default=900.0)
    sondear.add_argument(
        "--parar-en-error",
        action="store_true",
        help="Aborta el barrido en cuanto una sonda falla, en vez de seguir midiendo.",
    )
    sondear.add_argument("--json", action="store_true")
    _añadir_precios(sondear)

    informe = subs.add_parser(
        "informe", help="Recompone la ModelCard y el informe desde la evidencia. No gasta tokens."
    )
    informe.add_argument("--evidencia", type=Path, default=EVIDENCIA_POR_DEFECTO)
    informe.add_argument("--salida", type=Path, default=None)
    informe.add_argument("--modelo", default=None)
    informe.add_argument("--proveedor", default=PROVEEDOR_POR_DEFECTO)
    informe.add_argument("--revision", default=None)
    informe.add_argument(
        "--cualquier-revision",
        action="store_true",
        help=(
            "Acepta evidencia de cualquier revisión de sonda. Los números dejan de ser comparables."
        ),
    )
    informe.add_argument("--json", action="store_true")
    _añadir_precios(informe)

    banco = subs.add_parser("banco", help="Banco de tareas reales con criterio ejecutable.")
    grupo = banco.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--listar", action="store_true", help="Enseña las tareas del banco.")
    grupo.add_argument(
        "--verificar",
        action="store_true",
        help="Comprueba que cada criterio `debe_fallar_antes` FALLA hoy sobre el repo limpio.",
    )
    banco.add_argument("--raiz-edecan", type=Path, default=None)
    banco.add_argument("--raiz-acme", type=Path, default=None)
    banco.add_argument("--json", action="store_true")
    return parser


# --------------------------------------------------------------------------- #
# Subcomandos
# --------------------------------------------------------------------------- #


def _imprimir(texto: str, flujo: TextIO | None = None) -> None:
    """Imprime resolviendo el flujo en cada llamada.

    Fijar `sys.stdout` como valor por defecto lo congelaría en el momento del
    import, que es justo lo que rompe la captura de salida en las pruebas y
    cualquier redirección hecha después de importar el módulo.
    """
    print(texto, file=flujo if flujo is not None else sys.stdout)


def cmd_humo(args: argparse.Namespace) -> int:
    resultado = asyncio.run(prueba_de_humo(modelo=args.modelo, timeout_s=args.timeout_s))
    if args.json:
        _imprimir(resultado.model_dump_json(indent=2))
    else:
        marca = "OK" if resultado.ok else "FALLO"
        _imprimir(f"[{marca}] {resultado.mensaje}")
        _imprimir(f"  modelo: {resultado.modelo}")
        if resultado.http_status is not None:
            codigo = f" (code {resultado.codigo_proveedor})" if resultado.codigo_proveedor else ""
            _imprimir(f"  http: {resultado.http_status}{codigo}")
        if resultado.latencia_s is not None:
            _imprimir(f"  latencia: {resultado.latencia_s:.2f} s")
        if resultado.uso is not None:
            u = resultado.uso
            _imprimir(
                f"  tokens: entrada {u.entrada} (cacheados {u.cacheados}), salida {u.salida} "
                f"(razonamiento {u.razonamiento}), neuronas {u.neurons:g}"
            )
        if resultado.contenido:
            _imprimir(f"  respuesta: {resultado.contenido!r}")
        if resultado.remedio:
            _imprimir(f"  qué hacer: {resultado.remedio}")
    if resultado.ok:
        return CODIGO_OK
    return _CODIGOS_HUMO.get(resultado.codigo, CODIGO_ERROR)


def cmd_sondear(args: argparse.Namespace) -> int:
    modelo = args.modelo or modelo_por_defecto()
    revision = args.revision or revision_por_defecto()
    sondas = descubrir_sondas()
    if not sondas:
        _imprimir(
            "No hay sondas instaladas: falta `edecan_forge_probe.probes` exponiendo "
            "`SONDAS`. Sin sondas no hay mediciones y una ModelCard vacía no es una "
            "ModelCard: es un formulario en blanco.",
            sys.stderr,
        )
        return CODIGO_ERROR

    opciones = OpcionesRunner(
        modelo=modelo,
        proveedor=args.proveedor,
        revision_sonda=revision,
        directorio_evidencia=args.evidencia,
        solo=tuple(s for s in args.solo.split(",") if s.strip()),
        rehacer=args.rehacer,
        presupuesto_usd=args.presupuesto_usd,
        deadline_s=args.deadline_s,
        timeout_sonda_s=args.timeout_sonda_s,
        precio_entrada_usd_mtok=args.precio_entrada,
        precio_salida_usd_mtok=args.precio_salida,
        precio_cacheado_usd_mtok=args.precio_cacheada,
        parar_en_error=args.parar_en_error,
    )
    extra: dict[str, object] = {}
    if args.n is not None:
        extra["n"] = args.n
    try:
        resultado = asyncio.run(ejecutar(sondas, opciones, contexto_extra=extra))
    except ValueError as exc:
        _imprimir(str(exc), sys.stderr)
        return CODIGO_ERROR
    except KeyboardInterrupt:
        _imprimir("Abortado a la fuerza: la evidencia ya escrita sigue en disco.", sys.stderr)
        return CODIGO_ERROR

    (args.evidencia / "contabilidad.json").write_text(
        resultado.contabilidad.model_dump_json(indent=2), encoding="utf-8"
    )
    salida = args.salida or args.evidencia
    ruta_json, ruta_md = escribir_informe(
        resultado.modelcard,
        salida,
        contabilidad=resultado.contabilidad,
        ejecucion=resultado,
    )
    if args.json:
        _imprimir(resultado.model_dump_json(indent=2))
    else:
        _resumen_humano(resultado.modelcard, resultado, ruta_json, ruta_md)
    return CODIGO_OK if resultado.modelcard.go() else CODIGO_NO_GO


def cmd_informe(args: argparse.Namespace) -> int:
    modelo = args.modelo or modelo_por_defecto()
    revision = args.revision or revision_por_defecto()
    guardados = leer_resultados_guardados(
        args.evidencia,
        modelo=modelo,
        revision=None if args.cualquier_revision else revision,
    )
    if not guardados:
        _imprimir(
            f"No hay evidencia reutilizable en {args.evidencia} para el modelo {modelo} "
            f"y la revisión {revision}. Corre `sondear` primero, o pasa "
            "--cualquier-revision si de verdad aceptas mezclar revisiones.",
            sys.stderr,
        )
        return CODIGO_ERROR

    contabilidad = None
    ruta_conta = args.evidencia / "contabilidad.json"
    if ruta_conta.is_file():
        try:
            contabilidad = Contabilidad.model_validate_json(ruta_conta.read_text(encoding="utf-8"))
        except ValueError:
            contabilidad = None
    if contabilidad is not None and args.precio_entrada is not None:
        contabilidad = contabilidad.model_copy(
            update={
                "precio_entrada_usd_mtok": args.precio_entrada,
                "precio_salida_usd_mtok": args.precio_salida,
                "precio_cacheado_usd_mtok": args.precio_cacheada,
            }
        )

    card = componer_modelcard(
        [guardados[k] for k in sorted(guardados)],
        modelo=modelo,
        proveedor=args.proveedor,
        revision_sonda=revision,
        precio_entrada_usd_mtok=args.precio_entrada
        or (contabilidad.precio_entrada_usd_mtok if contabilidad else None),
        precio_salida_usd_mtok=args.precio_salida
        or (contabilidad.precio_salida_usd_mtok if contabilidad else None),
        notas=["Informe recompuesto desde evidencia en disco; no se hizo ninguna llamada."],
    )
    salida = args.salida or args.evidencia
    ruta_json, ruta_md = escribir_informe(card, salida, contabilidad=contabilidad)
    if args.json:
        _imprimir(card.model_dump_json(indent=2))
    else:
        _resumen_humano(card, None, ruta_json, ruta_md)
    return CODIGO_OK if card.go() else CODIGO_NO_GO


def cmd_banco(args: argparse.Namespace) -> int:
    try:
        tareas = cargar_banco()
    except BancoNoDisponible as exc:
        _imprimir(str(exc), sys.stderr)
        return CODIGO_ERROR
    if args.listar:
        return _listar_banco(tareas, como_json=args.json)
    raices = dict(RAICES_POR_DEFECTO)
    if args.raiz_edecan:
        raices["edecan"] = args.raiz_edecan
    if args.raiz_acme:
        raices["acme"] = args.raiz_acme
    resultados = verificar_banco(tareas, raices)
    ya_pasan = [r for r in resultados if r.pasa]
    rotos = [r for r in resultados if r.error]
    if args.json:
        _imprimir(json.dumps([r.model_dump() for r in resultados], ensure_ascii=False, indent=2))
    else:
        _imprimir(f"Criterios comprobados: {len(resultados)}")
        for r in resultados:
            if r.pasa:
                estado = "YA PASA (no mide nada)"
            elif r.error:
                estado = f"NO SE PUDO COMPROBAR — {r.error}"
            else:
                estado = "falla hoy (correcto)"
            _imprimir(f"  [{r.task_id} #{r.indice}] {r.kind}: {estado}")
            if r.pasa or r.error:
                _imprimir(f"      {r.descripcion}")
        if rotos:
            _imprimir(
                f"\n{len(rotos)} criterio(s) no se pudieron comprobar. Un criterio que no se "
                "ejecuta no es un criterio."
            )
        if ya_pasan:
            _imprimir(
                f"\n{len(ya_pasan)} criterio(s) YA se cumplen sobre el repo limpio. Esas "
                "tareas no miden nada: inflan la tasa de éxito del banco sin que el agente "
                "haga nada. Reescríbelas antes de usar el banco para decidir."
            )
        else:
            _imprimir("\nTodos los criterios fallan hoy: el banco mide algo.")
    if ya_pasan or rotos:
        return CODIGO_BANCO_INUTIL
    return CODIGO_OK


def _listar_banco(tareas: Sequence[BenchTask], *, como_json: bool) -> int:
    if como_json:
        _imprimir(json.dumps([t.model_dump() for t in tareas], ensure_ascii=False, indent=2))
        return CODIGO_OK
    if not tareas:
        _imprimir("El banco está vacío.")
        return CODIGO_OK
    _imprimir(f"{len(tareas)} tarea(s) en el banco:\n")
    for t in tareas:
        lenguajes = ", ".join(t.lenguajes) or "—"
        _imprimir(f"  {t.id}  [{t.repo}/{t.clase}]  {t.titulo}")
        _imprimir(
            f"      lenguajes: {lenguajes} · criterios: {len(t.criterios)} · "
            f"presupuesto: {t.presupuesto_usd:.2f} USD · máx. turnos: {t.max_turnos}"
        )
    return CODIGO_OK


def _resumen_humano(
    card: ModelCard,
    ejecucion: ResultadoEjecucion | None,
    ruta_json: Path,
    ruta_md: Path,
) -> None:
    """Imprime el veredicto en la terminal. La versión larga está en el Markdown."""
    evaluaciones = card.evaluar_umbrales(UMBRALES_FASE_0)
    go = card.go()
    _imprimir("")
    _imprimir("=" * 60)
    _imprimir(("VEREDICTO: GO" if go else "VEREDICTO: NO-GO").center(60))
    _imprimir("=" * 60)
    for ev in evaluaciones:
        marca = "sí" if ev.bloquea else "  "
        valor = "sin medir" if ev.valor is None else f"{ev.valor:g}"
        _imprimir(f" {marca} {ev.veredicto.value.upper():<9} {ev.umbral.clave:<34} {valor}")
    if ejecucion is not None and ejecucion.corte:
        _imprimir(f"\nEjecución cortada por {ejecucion.corte}: {ejecucion.motivo_corte}")
    if ejecucion is not None:
        coste = ejecucion.contabilidad.coste_usd
        gasto = "sin precios declarados" if coste is None else f"{coste:.4f} USD"
        _imprimir(
            f"\nGasto: {gasto} · entrada {ejecucion.contabilidad.tokens_entrada:,} "
            f"· salida {ejecucion.contabilidad.tokens_salida:,}"
        )
    if not go:
        _imprimir(
            "\nNO-GO no significa que el modelo sea malo: significa que hay que rediseñar "
            "los bloques afectados antes de escribirlos. El informe dice cuáles y cómo."
        )
    _imprimir(f"\n  {ruta_json}\n  {ruta_md}")


# --------------------------------------------------------------------------- #
# Entrada
# --------------------------------------------------------------------------- #


def main(argv: Sequence[str] | None = None) -> int:
    parser = construir_parser()
    args = parser.parse_args(argv)
    if not args.sin_env:
        ruta = args.env_file or buscar_dotenv()
        if ruta is not None:
            cargar_dotenv(ruta)
    match args.comando:
        case "humo":
            return cmd_humo(args)
        case "sondear":
            return cmd_sondear(args)
        case "informe":
            return cmd_informe(args)
        case "banco":
            return cmd_banco(args)
        case _:  # pragma: no cover - argparse ya lo impide
            parser.error(f"subcomando desconocido: {args.comando}")
            return CODIGO_ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
