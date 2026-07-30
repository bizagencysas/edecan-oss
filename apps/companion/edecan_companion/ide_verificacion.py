"""El bucle de verificación que no se rinde: ejecutar, leer el error, repetir.

El disparador de este archivo es la diferencia entre "el agente escribió
código" y "el código funciona". Hoy ``WorkersIDEAgent`` (``ide_workers_agent.py``)
expone ``ejecutar_comando`` como una herramienta más entre siete: el modelo
puede correr ``pytest`` una vez y seguir de largo aunque falle, porque nada lo
obliga a mirar el resultado y reintentar. Este módulo es la pieza que sí se
queda hasta que el comando de verificación pasa -- o hasta que hay una razón
concreta para parar y decirlo, no seguir intentando a ciegas.

Este módulo es autónomo, como ``ide_costos.py``: no depende de
``ide_sessions.py`` ni de ``ide_workers_agent.py``, y estos tampoco dependen
de él todavía. La integración real -- exponer ``ejecutar_hasta_que_pase`` (o
un intento por llamada) como herramienta del agente, y decidir quién escribe
la función ``arreglar`` -- la hace quien cablee esta pieza; ver el docstring
de ``ejecutar_hasta_que_pase`` para el contrato exacto que espera.

Piezas de este archivo:

1. ``ejecutar_intento``: corre el comando UNA vez y devuelve un
   ``ResultadoIntento`` con la clasificación de la falla (si la hay) y un
   resumen accionable del error, nunca la salida cruda completa.
2. ``ejecutar_hasta_que_pase``: el bucle en sí. Orquesta intentos sucesivos,
   invoca ``arreglar`` entre uno y otro, y decide cuándo parar.
3. ``detectar_comando_de_verificacion``: adivina el comando (pytest, npm
   test, tsc, make) inspeccionando el proyecto, para no exigir que alguien
   lo tipee siempre.

Criterio de parada y detección de bucle
----------------------------------------
``ide_costos.py`` YA resuelve "¿esto es un patrón que se repite sin avanzar?"
(``analizar_tarea(...).bucles``, pensado originalmente para tramos de
herramienta dentro de un turno de agente). Este módulo NO escribe un segundo
detector: arma, por cada intento, un evento con la MISMA forma que
``ide_costos`` ya sabe leer (``type: "tool"`` con texto ``"Usando
verificar."`` seguido de un evento ``type: "command"`` cuyo texto es la firma
normalizada del resultado) y le pregunta a ``analizar_tarea`` si el patrón
de los últimos intentos ya cuenta como bucle. Dos intentos consecutivos con
la MISMA firma alcanzan (``min_repeticiones_bucle=2``): "el mismo error dos
veces seguidas" es exactamente el caso que el pedido describe, no las tres
repeticiones que ``ide_costos`` usa por defecto para herramientas sueltas
dentro de un turno (ahí 2 repeticiones son un flujo normal de "leo, corrijo,
releo"; aquí NO -- si ``arreglar`` corrió y el error no cambió un ápice, no
hubo progreso real).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from edecan_companion.ide_costos import analizar_tarea

# Tope de caracteres del resumen que se le devuelve al agente para que
# arregle el error. Un pytest que falla puede escupir miles de líneas; nada
# de eso cabe (ni sirve) en el prompt siguiente. 4000 caracteres alcanzan
# para varias líneas de traceback + el resumen de pytest, sin inflar el
# contexto del modelo con ruido.
MAX_CHARS_RESUMEN = 4000
# Cuando no se reconoce ningún formato conocido (pytest/tsc/eslint/make), la
# última parte de la salida es, en la práctica, donde vive el error real --
# la mayoría de las herramientas de build/test terminan con el fallo, no
# empiezan con él.
LINEAS_COLA_DE_RESPALDO = 60

MAX_INTENTOS_POR_DEFECTO = 5
TIMEOUT_SEGUNDOS_POR_DEFECTO = 300
MIN_REPETICIONES_ERROR_IDENTICO = 2

_INTERPRETES_SHELL = {
    "bash",
    "dash",
    "fish",
    "sh",
    "zsh",
    "cmd.exe",
    "powershell",
    "powershell.exe",
    "pwsh",
}

# Patrones que delatan que el proceso SÍ arrancó pero no pudo completar la
# verificación por falta de una dependencia -- muy distinto de "el test
# falló": aquí el arreglo es instalar/importar algo, no corregir lógica.
#
# Cada patrón captura QUÉ falta en el grupo `falta` siempre que el mensaje lo
# diga. Antes eran literales sin captura y se devolvía `group(0)`, o sea la
# ETIQUETA del error y no su contenido: a `yaml` y a `requests` les salía la
# misma cadena "ModuleNotFoundError". Con el resumen usándose como FIRMA (ver
# `_firma_de_resultado`) eso convierte a dos problemas distintos en el mismo, y
# cualquier cosa que aprenda por firma -- `ide_autocritica` guarda la lección de
# un error bajo la suya -- termina sirviendo el arreglo de un proyecto en otro
# donde falta un paquete completamente distinto.
#
# El orden es de MÁS a MENOS específico y no es cosmético: `ModuleNotFoundError`
# aparece en la misma línea que `No module named 'yaml'`, así que si el genérico
# fuera primero ganaría él y el nombre del módulo se perdería otra vez.
_PATRONES_DEPENDENCIA_FALTANTE = (
    re.compile(r"No module named ['\"]?(?P<falta>[\w.\-]+)", re.IGNORECASE),
    re.compile(r"Cannot find module ['\"]?(?P<falta>[\w./@\-]+)", re.IGNORECASE),
    # zsh dice `zsh: command not found: rg` (el que falta va DESPUÉS) y bash dice
    # `bash: rg: command not found` (va ANTES). El de zsh se prueba primero: si
    # fuera al revés, sobre la línea de zsh engancharía "zsh" -- el nombre del
    # shell -- como si fuera el programa que falta.
    re.compile(r"command not found:\s+(?P<falta>[\w.\-]+)"),
    re.compile(r"(?P<falta>[\w.\-]+): command not found"),
    re.compile(r"ENOENT.*no such file or directory,? \w+ ['\"](?P<falta>[^'\"]+)", re.IGNORECASE),
    re.compile(r"'(?P<falta>[\w.\-]+)' is not recognized as an internal or external command"),
    # Sin nombre que capturar: quedan como respaldo para CLASIFICAR el fallo
    # (es falta de dependencia, no lógica rota). `_detectar_dependencia_faltante`
    # les devuelve la línea completa, que sí distingue un caso de otro.
    re.compile(r"ModuleNotFoundError", re.IGNORECASE),
    re.compile(r"MODULE_NOT_FOUND"),
    re.compile(r"ImportError", re.IGNORECASE),
    re.compile(r"is not recognized as an internal or external command"),
    re.compile(r"command not found"),
    re.compile(r"ENOENT.*no such file or directory", re.IGNORECASE),
)

# Formato de pytest con -q (o por defecto): la sección final ya es el resumen
# accionable que pytest arma para humanos -- reusarlo es mejor que reinventar
# un parser de tracebacks.
_PATRON_PYTEST_RESUMEN = re.compile(
    r"=+ short test summary info =+\n(?P<cuerpo>.*?)(?:\n=+[^\n]*=+\s*$|\Z)",
    re.DOTALL,
)
_PATRON_PYTEST_LINEA_FINAL = re.compile(r"=+ (?P<linea>\d+ (?:failed|error)[^=]*) =+")
_PATRON_PYTEST_FAILED = re.compile(r"^(?:FAILED|ERROR) .+$", re.MULTILINE)

# Errores estilo compilador: "archivo:línea:columna: mensaje" (tsc, eslint,
# muchos linters de Node). La columna es opcional porque no todas las
# herramientas la imprimen.
_PATRON_ERROR_COMPILADOR = re.compile(
    r"^(?P<archivo>[^\s:][^:\n]*\.[a-zA-Z0-9]+):(?P<linea>\d+)(?::(?P<columna>\d+))?"
    r"\s*-?\s*(?:error|Error|ERROR)\b.*$",
    re.MULTILINE,
)


def _texto_para_analisis(stdout: str, stderr: str) -> str:
    """Combina stdout/stderr en un único texto para buscar patrones.

    El orden importa poco para los regex (todos anclan a inicio de línea con
    ``re.MULTILINE``), pero sí importa para la cola de respaldo: se prioriza
    stderr al final porque las herramientas de compilación/lint casi siempre
    imprimen el error último ahí, y es lo que un vistazo humano miraría
    primero.
    """
    partes = [p for p in (stdout.strip(), stderr.strip()) if p]
    return "\n".join(partes)


def _detectar_dependencia_faltante(texto: str) -> str | None:
    """Qué dependencia falta, o `None` si el fallo no es por eso.

    Devuelve el NOMBRE de lo que falta cuando el mensaje lo dice (`yaml`, `rg`);
    si el patrón que enganchó no lo trae, devuelve la línea entera en vez de la
    etiqueta del error. La diferencia importa porque este valor viaja a la firma
    del intento: "ModuleNotFoundError" a secas es la misma cadena para todos los
    paquetes del mundo, y todo lo que indexe por firma trataría dos problemas
    distintos como el mismo.
    """
    for patron in _PATRONES_DEPENDENCIA_FALTANTE:
        match = patron.search(texto)
        if not match:
            continue
        if "falta" in match.groupdict() and match.group("falta"):
            return match.group("falta").strip()
        # Sin captura: la línea completa. Menos limpia que un nombre, pero
        # conserva lo que distingue este fallo de otro.
        inicio = texto.rfind("\n", 0, match.start()) + 1
        fin = texto.find("\n", match.end())
        linea = texto[inicio : fin if fin != -1 else len(texto)].strip()
        return linea or match.group(0).strip()
    return None


@dataclass(frozen=True)
class ResumenError:
    """El error reducido a lo accionable: qué falló y, si se pudo, dónde."""

    lineas_clave: tuple[str, ...]
    archivos_involucrados: tuple[str, ...]
    texto: str
    truncado: bool
    fuente: str  # "pytest" | "compilador" | "cola_de_respaldo" | "vacio"

    def resumen(self) -> dict[str, Any]:
        return {
            "lineas_clave": list(self.lineas_clave),
            "archivos_involucrados": list(self.archivos_involucrados),
            "texto": self.texto,
            "truncado": self.truncado,
            "fuente": self.fuente,
        }


def _recortar(texto: str, limite: int = MAX_CHARS_RESUMEN) -> tuple[str, bool]:
    if len(texto) <= limite:
        return texto, False
    # Se corta por el FINAL del bloque relevante, no por el principio: en
    # todos los formatos que este módulo reconoce, lo último es lo más
    # cercano a "la causa concreta" (el resumen de pytest, la última línea
    # de error del compilador). Cortar por el principio dejaría justo afuera
    # la parte que el agente necesita para arreglar algo.
    return "[...recortado...]\n" + texto[-limite:], True


def _archivos_de(lineas: Sequence[str]) -> tuple[str, ...]:
    vistos: list[str] = []
    patron_ruta = re.compile(r"([./\w-]+\.[a-zA-Z0-9]{1,5})(?=[:\s]|$)")
    for linea in lineas:
        for match in patron_ruta.finditer(linea):
            ruta = match.group(1)
            if ruta not in vistos:
                vistos.append(ruta)
    return tuple(vistos)


def extraer_resumen(stdout: str, stderr: str) -> ResumenError:
    """Reduce una salida (potencialmente enorme) al fragmento accionable.

    Prueba, en orden, los formatos que se sabe reconocer -- pytest primero
    porque es el más común en este monorepo y el más estructurado -- y cae a
    una cola de respaldo cuando nada calza. Nunca devuelve la salida cruda
    completa: eso es justamente lo que este módulo existe para evitar.
    """
    texto = _texto_para_analisis(stdout, stderr)
    if not texto:
        return ResumenError((), (), "", False, "vacio")

    match_pytest = _PATRON_PYTEST_RESUMEN.search(texto)
    if match_pytest:
        cuerpo = match_pytest.group("cuerpo").strip()
        lineas = tuple(
            linea for linea in cuerpo.splitlines() if linea.strip()
        ) or tuple(_PATRON_PYTEST_FAILED.findall(texto))
        linea_final_match = _PATRON_PYTEST_LINEA_FINAL.search(texto)
        piezas = list(lineas)
        if linea_final_match:
            piezas.append(linea_final_match.group("linea").strip())
        texto_resumen, truncado = _recortar("\n".join(piezas))
        return ResumenError(lineas, _archivos_de(lineas), texto_resumen, truncado, "pytest")

    encontrados_compilador = _PATRON_ERROR_COMPILADOR.findall(texto)
    lineas_compilador = tuple(
        m.group(0) for m in _PATRON_ERROR_COMPILADOR.finditer(texto)
    )
    if encontrados_compilador:
        texto_resumen, truncado = _recortar("\n".join(lineas_compilador))
        return ResumenError(
            lineas_compilador,
            _archivos_de(lineas_compilador),
            texto_resumen,
            truncado,
            "compilador",
        )

    lineas_todas = texto.splitlines()
    cola = tuple(lineas_todas[-LINEAS_COLA_DE_RESPALDO:])
    texto_resumen, truncado_cola = _recortar("\n".join(cola))
    truncado = truncado_cola or len(lineas_todas) > LINEAS_COLA_DE_RESPALDO
    return ResumenError(cola, _archivos_de(cola), texto_resumen, truncado, "cola_de_respaldo")


def _firma_de_resultado(exit_code: int | None, resumen: ResumenError) -> str:
    """Firma corta y estable para comparar "es el mismo error de antes".

    Usa el resumen YA reducido (no la salida cruda): dos corridas de un
    mismo pytest fallido pueden diferir en tiempos ("in 0.42s" vs "in
    0.39s") sin que eso sea un error distinto. El resumen de pytest excluye
    esa línea de tiempos del cuerpo de fallos; para el resto de fuentes, se
    usan las líneas clave (que tampoco suelen traer tiempos).
    """
    cuerpo = "\n".join(resumen.lineas_clave) or resumen.texto
    return f"exit={exit_code}:{cuerpo}"


@dataclass(frozen=True)
class ResultadoIntento:
    """Lo que produjo UN intento de correr el comando de verificación."""

    intento: int
    argv: tuple[str, ...]
    aprobado: bool
    exit_code: int | None
    tipo_falla: str | None  # None si aprobado; si no: "fallo_de_verificacion"/"no_se_pudo_ejecutar"
    motivo_no_ejecutable: str | None
    resumen_error: ResumenError | None
    firma: str | None
    duracion_segundos: float

    def resumen(self) -> dict[str, Any]:
        return {
            "intento": self.intento,
            "argv": list(self.argv),
            "aprobado": self.aprobado,
            "exit_code": self.exit_code,
            "tipo_falla": self.tipo_falla,
            "motivo_no_ejecutable": self.motivo_no_ejecutable,
            "resumen_error": self.resumen_error.resumen() if self.resumen_error else None,
            "duracion_segundos": round(self.duracion_segundos, 2),
        }


def ejecutar_intento(
    argv: Sequence[str],
    *,
    cwd: str | Path,
    intento: int = 1,
    timeout_segundos: int = TIMEOUT_SEGUNDOS_POR_DEFECTO,
) -> ResultadoIntento:
    """Corre el comando de verificación UNA vez y clasifica el resultado.

    No usa shell (misma razón que ``ide_workers_agent._run_command``: nada
    de interpolación oculta) y distingue, antes de intentar siquiera lanzar
    el proceso, un ejecutable que no existe -- ese es un "no se pudo
    ejecutar" inmediato, no un fallo de verificación con exit code.
    """
    if not argv:
        raise ValueError("argv no puede estar vacío.")
    argv = list(argv)
    nombre_ejecutable = Path(argv[0]).name.casefold()
    if nombre_ejecutable in _INTERPRETES_SHELL:
        raise ValueError(
            "El bucle de verificación no ejecuta intérpretes de shell; "
            "pasa el programa y sus argumentos por separado."
        )

    inicio = time.monotonic()
    ejecutable = argv[0] if os.path.isabs(argv[0]) else shutil.which(argv[0])
    if not ejecutable:
        return ResultadoIntento(
            intento=intento,
            argv=tuple(argv),
            aprobado=False,
            exit_code=None,
            tipo_falla="no_se_pudo_ejecutar",
            motivo_no_ejecutable=f"No se encontró el ejecutable '{argv[0]}' en el PATH.",
            resumen_error=None,
            firma=f"no_se_pudo_ejecutar:{argv[0]}",
            duracion_segundos=time.monotonic() - inicio,
        )

    try:
        proceso = subprocess.run(
            [ejecutable, *argv[1:]],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_segundos,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        resumen = extraer_resumen(stdout, stderr)
        return ResultadoIntento(
            intento=intento,
            argv=tuple(argv),
            aprobado=False,
            exit_code=None,
            tipo_falla="no_se_pudo_ejecutar",
            motivo_no_ejecutable=f"El comando superó el límite de {timeout_segundos}s.",
            resumen_error=resumen,
            firma="no_se_pudo_ejecutar:timeout",
            duracion_segundos=time.monotonic() - inicio,
        )
    except OSError as exc:
        return ResultadoIntento(
            intento=intento,
            argv=tuple(argv),
            aprobado=False,
            exit_code=None,
            tipo_falla="no_se_pudo_ejecutar",
            motivo_no_ejecutable=f"No se pudo lanzar el proceso: {exc}",
            resumen_error=None,
            firma=f"no_se_pudo_ejecutar:{exc}",
            duracion_segundos=time.monotonic() - inicio,
        )

    duracion = time.monotonic() - inicio
    if proceso.returncode == 0:
        return ResultadoIntento(
            intento=intento,
            argv=tuple(argv),
            aprobado=True,
            exit_code=0,
            tipo_falla=None,
            motivo_no_ejecutable=None,
            resumen_error=None,
            firma=None,
            duracion_segundos=duracion,
        )

    texto_combinado = _texto_para_analisis(proceso.stdout, proceso.stderr)
    dependencia = _detectar_dependencia_faltante(texto_combinado)
    resumen = extraer_resumen(proceso.stdout, proceso.stderr)
    if dependencia:
        return ResultadoIntento(
            intento=intento,
            argv=tuple(argv),
            aprobado=False,
            exit_code=proceso.returncode,
            tipo_falla="no_se_pudo_ejecutar",
            motivo_no_ejecutable=f"Dependencia faltante detectada: {dependencia}",
            resumen_error=resumen,
            firma=f"no_se_pudo_ejecutar:dependencia:{dependencia}",
            duracion_segundos=duracion,
        )

    return ResultadoIntento(
        intento=intento,
        argv=tuple(argv),
        aprobado=False,
        exit_code=proceso.returncode,
        tipo_falla="fallo_de_verificacion",
        motivo_no_ejecutable=None,
        resumen_error=resumen,
        firma=_firma_de_resultado(proceso.returncode, resumen),
        duracion_segundos=duracion,
    )


@dataclass(frozen=True)
class ResultadoBucle:
    """El resultado completo de correr el bucle hasta pasar o hasta parar."""

    aprobado: bool
    intentos: tuple[ResultadoIntento, ...]
    detenido_por: str
    mensaje: str

    def resumen(self) -> dict[str, Any]:
        return {
            "aprobado": self.aprobado,
            "intentos": [item.resumen() for item in self.intentos],
            "total_intentos": len(self.intentos),
            "detenido_por": self.detenido_por,
            "mensaje": self.mensaje,
        }


def _eventos_de_intentos(intentos: Sequence[ResultadoIntento]) -> list[dict[str, Any]]:
    """Traduce los intentos al formato de evento que ``ide_costos`` ya sabe leer.

    Reutiliza ``analizar_tarea`` en vez de escribir un segundo detector de
    bucle: un evento ``tool`` ("Usando verificar.") por intento, seguido de
    un evento ``command`` cuyo texto es la firma del resultado -- exactamente
    la forma que ``ide_costos._detalle_de_tramo`` ya sabe extraer como
    "detalle" de la herramienta.
    """
    eventos: list[dict[str, Any]] = []
    cursor = 0
    for item in intentos:
        cursor += 1
        eventos.append({"cursor": cursor, "type": "tool", "text": "Usando verificar."})
        cursor += 1
        eventos.append(
            {"cursor": cursor, "type": "command", "text": item.firma or f"aprobado:{item.intento}"}
        )
    return eventos


def _hay_error_identico_consecutivo(
    intentos: Sequence[ResultadoIntento], *, min_repeticiones: int
) -> bool:
    """¿Los últimos ``min_repeticiones`` intentos fallaron con la misma firma?

    Delegado a ``ide_costos.analizar_tarea`` (ver docstring del módulo): se
    arma la secuencia completa de eventos y se pregunta si el bucle detectado
    que TERMINA en el último intento tiene, al menos, ``min_repeticiones``
    repeticiones de período 1 (la misma firma en seco, sin alternar con
    otra) -- el patrón exacto de "arreglé y el error no cambió nada".
    """
    if len(intentos) < min_repeticiones:
        return False
    eventos = _eventos_de_intentos(intentos)
    bucles = analizar_tarea(eventos, min_repeticiones_bucle=min_repeticiones).bucles
    ultimo_indice = len(intentos)
    return any(
        bucle.periodo == 1
        and bucle.indice_fin == ultimo_indice
        and bucle.repeticiones_del_ciclo >= min_repeticiones
        for bucle in bucles
    )


def ejecutar_hasta_que_pase(
    argv: Sequence[str],
    *,
    cwd: str | Path,
    arreglar: Callable[[ResultadoIntento], None] | None = None,
    max_intentos: int = MAX_INTENTOS_POR_DEFECTO,
    timeout_segundos: int = TIMEOUT_SEGUNDOS_POR_DEFECTO,
    min_repeticiones_error_identico: int = MIN_REPETICIONES_ERROR_IDENTICO,
) -> ResultadoBucle:
    """El bucle completo: ejecutar, y si falla, dar oportunidad de arreglar y repetir.

    Parámetros
    ----------
    argv:
        El comando de verificación, ya tokenizado (nunca una cadena de shell).
    cwd:
        Directorio de trabajo donde correr el comando.
    arreglar:
        Callback que el INTEGRADOR conecta al agente real: recibe el
        ``ResultadoIntento`` que acaba de fallar y hace lo que haga falta
        (típicamente, dejar que el modelo lea ``resultado_error.resumen()``
        y edite archivos) antes de que este bucle vuelva a correr el
        comando. Este módulo no sabe nada de LLMs ni de sesiones: solo llama
        a esta función entre intentos y vuelve a verificar.

        Si es ``None`` (no hay forma de arreglar nada), el bucle corre UN
        solo intento y devuelve el resultado tal cual -- reintentar sin que
        nada cambie en el disco solo repetiría el mismo error.
    max_intentos:
        Tope duro. Sin esto, un ``arreglar`` que sigue produciendo errores
        DISTINTOS entre sí (así que nunca dispara la detección de error
        repetido) correría toda la noche.
    min_repeticiones_error_identico:
        Cuántas veces seguidas tiene que repetirse la MISMA firma de error
        para declarar que el agente está atascado. 2 por defecto: dos
        intentos con exactamente el mismo error significa que ``arreglar``
        no cambió nada que le importara al comando de verificación.
    """
    if max_intentos < 1:
        raise ValueError("max_intentos debe ser al menos 1.")

    intentos: list[ResultadoIntento] = []
    resultado = ejecutar_intento(argv, cwd=cwd, intento=1, timeout_segundos=timeout_segundos)
    intentos.append(resultado)

    if resultado.aprobado:
        return ResultadoBucle(
            aprobado=True,
            intentos=tuple(intentos),
            detenido_por="aprobado",
            mensaje=f"La verificación pasó en el intento {resultado.intento}.",
        )

    if arreglar is None:
        return ResultadoBucle(
            aprobado=False,
            intentos=tuple(intentos),
            detenido_por="sin_funcion_de_arreglo",
            mensaje=(
                "La verificación falló y no hay forma de intentar un arreglo "
                "(no se recibió una función `arreglar`); se detiene tras un solo intento."
            ),
        )

    while len(intentos) < max_intentos:
        arreglar(intentos[-1])
        siguiente_intento = len(intentos) + 1
        resultado = ejecutar_intento(
            argv, cwd=cwd, intento=siguiente_intento, timeout_segundos=timeout_segundos
        )
        intentos.append(resultado)

        if resultado.aprobado:
            return ResultadoBucle(
                aprobado=True,
                intentos=tuple(intentos),
                detenido_por="aprobado",
                mensaje=f"La verificación pasó en el intento {resultado.intento}.",
            )

        if _hay_error_identico_consecutivo(
            intentos, min_repeticiones=min_repeticiones_error_identico
        ):
            return ResultadoBucle(
                aprobado=False,
                intentos=tuple(intentos),
                detenido_por="error_repetido",
                mensaje=(
                    f"El mismo error se repitió {min_repeticiones_error_identico} veces "
                    "seguidas sin ningún cambio; el agente está atascado. Se detiene el "
                    "bucle en vez de seguir intentando lo mismo."
                ),
            )

    return ResultadoBucle(
        aprobado=False,
        intentos=tuple(intentos),
        detenido_por="tope_de_intentos",
        mensaje=f"Se alcanzó el tope de {max_intentos} intento(s) sin que la verificación pasara.",
    )


# --------------------------------------------------------------------- #
# Detección del comando de verificación del proyecto.
# --------------------------------------------------------------------- #

_MARCADORES_PYTEST = ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini")


def _pyproject_tiene_pytest(ruta: Path) -> bool:
    try:
        contenido = ruta.read_text(encoding="utf-8")
    except OSError:
        return False
    return "[tool.pytest" in contenido or "pytest" in contenido


def _script_npm_real(scripts: dict[str, Any], clave: str) -> bool:
    """``npm init`` deja un script "test" placeholder que nunca hay que correr."""
    valor = scripts.get(clave)
    if not isinstance(valor, str) or not valor.strip():
        return False
    return "Error: no test specified" not in valor


def detectar_comando_de_verificacion(
    directorio: str | Path, *, interprete_python: str = "python"
) -> list[str] | None:
    """Adivina el comando de verificación mirando archivos típicos del proyecto.

    Orden de preferencia (de más a menos específico/rápido de confirmar):

    1. ``package.json`` con un script ``"test"`` real (no el placeholder que
       deja ``npm init``) -> ``npm test``.
    2. Señales de pytest (``pyproject.toml`` con sección ``[tool.pytest...]``,
       o presencia de ``pytest.ini``/``setup.cfg``/``tox.ini``) -> ``python -m
       pytest -q``. ``interprete_python`` deja que quien integre esto pase el
       intérprete correcto (p. ej. el de un venv del proyecto) en vez de
       depender del ``python`` del PATH, que puede no ser el que corresponde.
    3. ``Makefile`` con un target ``test:`` -> ``make test``; si no hay
       ``test:`` pero sí ``build:`` -> ``make build``.
    4. ``tsconfig.json`` sin script de test de npm -> ``npx tsc --noEmit``.
    5. ``package.json`` con un script ``"build"`` real, si nada de lo
       anterior aplicó -> ``npm run build``.

    Devuelve ``None`` si nada de esto se reconoce: mejor pedir el comando
    explícito que adivinar mal y correr algo destructivo o sin sentido.
    """
    directorio = Path(directorio)

    package_json = directorio / "package.json"
    scripts: dict[str, Any] = {}
    if package_json.is_file():
        try:
            datos = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            datos = {}
        if isinstance(datos, dict) and isinstance(datos.get("scripts"), dict):
            scripts = datos["scripts"]
        if _script_npm_real(scripts, "test"):
            return ["npm", "test"]

    for nombre in _MARCADORES_PYTEST:
        ruta = directorio / nombre
        if not ruta.is_file():
            continue
        if nombre == "pyproject.toml":
            if _pyproject_tiene_pytest(ruta):
                return [interprete_python, "-m", "pytest", "-q"]
            continue
        return [interprete_python, "-m", "pytest", "-q"]

    makefile = directorio / "Makefile"
    if makefile.is_file():
        try:
            contenido = makefile.read_text(encoding="utf-8")
        except OSError:
            contenido = ""
        if re.search(r"^test:", contenido, re.MULTILINE):
            return ["make", "test"]
        if re.search(r"^build:", contenido, re.MULTILINE):
            return ["make", "build"]

    if (directorio / "tsconfig.json").is_file():
        return ["npx", "tsc", "--noEmit"]

    if _script_npm_real(scripts, "build"):
        return ["npm", "run", "build"]

    return None
