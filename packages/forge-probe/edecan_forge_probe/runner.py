"""Orquestador de la fase 0: corre las sondas, paga la cuenta y compone la `ModelCard`.

Este módulo no mide nada por sí mismo. Su trabajo es el que nadie quiere escribir
y sin el cual las mediciones no valen: ejecutar sondas de una en una, llevar la
contabilidad real de tokens y dinero, cortar cuando se acaba el presupuesto o el
reloj, sobrevivir a un Ctrl-C sin perder lo ya medido, y poder retomar mañana lo
que quedó a medias sin volver a pagar por ello.

Cuatro invariantes gobiernan todo lo de abajo:

1. **Secuencial, siempre.** Dos sondas en paralelo comparten la cola del
   proveedor y contaminan `ttft` y `throughput_tps`. La concurrencia aquí no es
   una optimización: es un error de medición.
2. **Nada se inventa.** El compositor de `ModelCard` sólo escribe campos que
   una sonda publicó explícitamente. Lo que no se midió queda en `None` y
   arrastra `Veredicto.SIN_DATO`.
3. **La evidencia manda.** Cada `ProbeResult` se persiste con el modelo y la
   revisión de sonda que lo produjeron. Reanudar sólo reutiliza resultados
   `ok=True` del mismo modelo y la misma revisión: un número medido con otro
   código no es comparable, y un fallo no es una medición.
4. **El razonamiento se paga.** Verificado el 27-07-2026 contra la API real:
   `@cf/moonshotai/kimi-k2.7-code` razona siempre, devuelve el razonamiento en
   `message.reasoning_content` (campo distinto de `message.content`) y lo
   factura a precio de salida. La contabilidad lo cuenta aparte para que la
   sobrecarga de razonamiento sea una métrica y no una sorpresa en la factura.

Precios: Workers AI no los expone en la respuesta, así que entran por parámetro.
Sin precios el runner cuenta tokens y deja el coste en `None` — que es la única
respuesta honesta— y en ese modo `--presupuesto-usd` no se puede hacer cumplir.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import pkgutil
import re
import signal
import subprocess
import time
from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from .modelcard import (
    ArgProfile,
    BenchTask,
    Capability,
    Criterio,
    Latencia,
    ModelCard,
    ProbeResult,
    Reliability,
)

__all__ = [
    "BancoNoDisponible",
    "CargaHumo",
    "Contabilidad",
    "ContextoSonda",
    "DeadlineAgotado",
    "EjecucionCancelada",
    "MODELO_POR_DEFECTO",
    "OpcionesRunner",
    "PresupuestoAgotado",
    "ResultadoCriterio",
    "ResultadoEjecucion",
    "ResultadoHumo",
    "Sonda",
    "Uso",
    "cargar_banco",
    "componer_modelcard",
    "descubrir_sondas",
    "ejecutar",
    "evaluar_criterio",
    "leer_resultados_guardados",
    "prueba_de_humo",
    "verificar_banco",
]

# El modelo objetivo verificado. Se cambia por entorno el día que la cuenta
# tenga acceso a K3: no hay ninguna otra referencia al identificador en el resto
# del paquete.
MODELO_POR_DEFECTO = "@cf/moonshotai/kimi-k2.7-code"
PROVEEDOR_POR_DEFECTO = "workers-ai"

_VAR_MODELO = "FORGE_PROBE_MODEL"
_VAR_CUENTA = "CLOUDFLARE_ACCOUNT_ID"
_VAR_TOKEN = "CLOUDFLARE_API_TOKEN"


def modelo_por_defecto() -> str:
    """Modelo objetivo, con `FORGE_PROBE_MODEL` por encima del valor compilado."""
    return os.environ.get(_VAR_MODELO, "").strip() or MODELO_POR_DEFECTO


# --------------------------------------------------------------------------- #
# Errores de control de flujo
# --------------------------------------------------------------------------- #


class PresupuestoAgotado(RuntimeError):
    """El gasto acumulado alcanzó el presupuesto. Se lanza DENTRO de la sonda.

    Se lanza desde `ContextoSonda.registrar_uso` para que una sonda cara pare en
    la petición siguiente y no al final de un barrido de veinte llamadas.
    """


class DeadlineAgotado(RuntimeError):
    """Se acabó el reloj de pared asignado a la ejecución completa."""


class EjecucionCancelada(RuntimeError):
    """El operador pidió parar (Ctrl-C)."""


class BancoNoDisponible(RuntimeError):
    """No hay módulo de banco de tareas instalado en el paquete."""


class ProveedorNoDisponible(RuntimeError):
    """Falta credencial, cuenta o acceso al modelo."""


# --------------------------------------------------------------------------- #
# Contabilidad
# --------------------------------------------------------------------------- #


class Uso(BaseModel):
    """Consumo de una llamada, tal y como lo devuelve Workers AI.

    `cacheados` es un SUBCONJUNTO de `entrada` (Cloudflare lo publica en
    `usage.prompt_tokens_details.cached_tokens`), y `razonamiento` es un
    subconjunto de `salida` (`message.reasoning_content`). Contarlos como
    sumandos independientes duplicaría la factura.
    """

    entrada: int = Field(default=0, ge=0)
    salida: int = Field(default=0, ge=0)
    cacheados: int = Field(default=0, ge=0)
    razonamiento: int = Field(default=0, ge=0)
    neurons: float = Field(default=0.0, ge=0.0)
    llamadas: int = Field(default=1, ge=0)


class Contabilidad(BaseModel):
    """Gasto acumulado de una ejecución, en tokens y —si hay precios— en dólares."""

    precio_entrada_usd_mtok: float | None = None
    precio_salida_usd_mtok: float | None = None
    precio_cacheado_usd_mtok: float | None = None
    """Entrada servida desde caché de prefijo. Verificado: 0,19 USD/M frente a
    0,95 USD/M de entrada fría. Si no se declara, la entrada cacheada se factura
    al precio de entrada normal y el coste resultante es una COTA SUPERIOR."""

    tokens_entrada: int = 0
    tokens_entrada_cacheados: int = 0
    tokens_salida: int = 0
    tokens_razonamiento: int = 0
    neurons: float = 0.0
    llamadas: int = 0
    por_sonda: dict[str, Uso] = Field(default_factory=dict)

    @property
    def hay_precios(self) -> bool:
        return self.precio_entrada_usd_mtok is not None and self.precio_salida_usd_mtok is not None

    @property
    def coste_es_cota_superior(self) -> bool:
        """`True` si se facturó entrada cacheada a precio de entrada fría."""
        return (
            self.hay_precios
            and self.precio_cacheado_usd_mtok is None
            and self.tokens_entrada_cacheados > 0
        )

    @property
    def coste_usd(self) -> float | None:
        """Gasto acumulado. `None` cuando no se declararon precios."""
        if not self.hay_precios:
            return None
        p_in = self.precio_entrada_usd_mtok or 0.0
        p_out = self.precio_salida_usd_mtok or 0.0
        cacheado = self.precio_cacheado_usd_mtok
        p_cache = cacheado if cacheado is not None else p_in
        frios = max(0, self.tokens_entrada - self.tokens_entrada_cacheados)
        return (
            frios * p_in + self.tokens_entrada_cacheados * p_cache + self.tokens_salida * p_out
        ) / 1_000_000

    @property
    def ratio_razonamiento(self) -> float | None:
        """Tokens de razonamiento / tokens de contenido, agregado.

        Es la sobrecarga que hay que reservar en `max_tokens`: medido, una
        respuesta de dos palabras gastó 65 tokens de salida, ~57 de razonamiento.
        """
        contenido = self.tokens_salida - self.tokens_razonamiento
        if contenido <= 0:
            return None
        return self.tokens_razonamiento / contenido

    def anotar(self, sonda: str, uso: Uso) -> None:
        """Suma un consumo al total y al desglose por sonda."""
        self.tokens_entrada += uso.entrada
        self.tokens_entrada_cacheados += uso.cacheados
        self.tokens_salida += uso.salida
        self.tokens_razonamiento += uso.razonamiento
        self.neurons += uso.neurons
        self.llamadas += uso.llamadas
        previo = self.por_sonda.get(sonda)
        if previo is None:
            self.por_sonda[sonda] = uso.model_copy()
        else:
            self.por_sonda[sonda] = Uso(
                entrada=previo.entrada + uso.entrada,
                salida=previo.salida + uso.salida,
                cacheados=previo.cacheados + uso.cacheados,
                razonamiento=previo.razonamiento + uso.razonamiento,
                neurons=previo.neurons + uso.neurons,
                llamadas=previo.llamadas + uso.llamadas,
            )


# --------------------------------------------------------------------------- #
# Sondas
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class ContextoSonda:
    """Lo que el runner le presta a una sonda mientras corre.

    Una sonda NO abre archivos por su cuenta ni decide si le queda presupuesto:
    pide ambas cosas aquí para que el runner pueda contabilizarlas y cortarlas.
    """

    modelo: str
    proveedor: str
    sonda: str
    directorio_evidencia: Path
    contabilidad: Contabilidad
    cancelacion: asyncio.Event
    presupuesto_usd: float | None = None
    restante_s: float | None = None
    """Segundos hasta el deadline global, o `None` si no hay deadline."""
    opciones: dict[str, Any] = field(default_factory=dict)

    @property
    def cancelado(self) -> bool:
        return self.cancelacion.is_set()

    def registrar_uso(
        self,
        entrada: int = 0,
        salida: int = 0,
        *,
        cacheados: int = 0,
        razonamiento: int = 0,
        neurons: float = 0.0,
        llamadas: int = 1,
    ) -> None:
        """Apunta el consumo de una llamada y corta si el presupuesto se agotó.

        Lanza `PresupuestoAgotado` DESPUÉS de anotar: el token ya se gastó y la
        factura tiene que reflejarlo aunque la sonda muera acto seguido.
        """
        self.contabilidad.anotar(
            self.sonda,
            Uso(
                entrada=entrada,
                salida=salida,
                cacheados=cacheados,
                razonamiento=razonamiento,
                neurons=neurons,
                llamadas=llamadas,
            ),
        )
        gasto = self.contabilidad.coste_usd
        if self.presupuesto_usd is not None and gasto is not None and gasto >= self.presupuesto_usd:
            raise PresupuestoAgotado(
                f"gasto acumulado {gasto:.4f} USD ≥ presupuesto {self.presupuesto_usd:.4f} USD"
            )

    def anotar_respuesta(self, cuerpo: dict[str, Any]) -> Uso:
        """Extrae y registra el consumo de una respuesta cruda de Workers AI.

        Tolera respuestas sin `usage` (devuelve ceros) porque una sonda no debe
        romperse por un campo opcional del proveedor.
        """
        uso = extraer_uso(cuerpo)
        self.registrar_uso(
            entrada=uso.entrada,
            salida=uso.salida,
            cacheados=uso.cacheados,
            razonamiento=uso.razonamiento,
            neurons=uso.neurons,
            llamadas=uso.llamadas,
        )
        return uso

    def guardar_evidencia(self, nombre: str, contenido: Any) -> str:
        """Escribe una traza cruda y devuelve su ruta, para `ProbeResult.evidencia`."""
        destino = self.directorio_evidencia / "trazas" / self.sonda
        destino.mkdir(parents=True, exist_ok=True)
        ruta = destino / nombre
        if isinstance(contenido, bytes):
            ruta.write_bytes(contenido)
        elif isinstance(contenido, str):
            ruta.write_text(contenido, encoding="utf-8")
        else:
            ruta.write_text(
                json.dumps(contenido, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
        return str(ruta)


EjecutorSonda = Callable[[ContextoSonda], Awaitable[ProbeResult]]


@dataclass(slots=True)
class Sonda:
    """Una medición ejecutable, con nombre estable y grupo para `--solo`.

    El nombre es la clave de persistencia: cambiarlo invalida la reanudación de
    ese resultado, que es justo lo que se quiere cuando una sonda cambia de
    significado.
    """

    nombre: str
    ejecutar: EjecutorSonda
    grupo: str = ""
    capability: Capability | None = None
    descripcion: str = ""

    def __post_init__(self) -> None:
        if not self.grupo:
            self.grupo = self.nombre.split(".", 1)[0]


def extraer_uso(cuerpo: Any) -> Uso:
    """Normaliza el `usage` de Workers AI a un `Uso`.

    Los nombres de campo están verificados contra la API real: `prompt_tokens`,
    `completion_tokens`, `prompt_tokens_details.cached_tokens`,
    `completion_tokens_details.reasoning_tokens` y `neurons`.
    """
    if not isinstance(cuerpo, dict):
        return Uso(llamadas=1)
    datos = cuerpo.get("result") if isinstance(cuerpo.get("result"), dict) else cuerpo
    uso = datos.get("usage") if isinstance(datos, dict) else None
    if not isinstance(uso, dict):
        return Uso(llamadas=1)
    detalle_in = uso.get("prompt_tokens_details")
    detalle_out = uso.get("completion_tokens_details")
    cacheados = 0
    if isinstance(detalle_in, dict):
        cacheados = int(detalle_in.get("cached_tokens") or 0)
    razonamiento = 0
    if isinstance(detalle_out, dict):
        razonamiento = int(detalle_out.get("reasoning_tokens") or 0)
    return Uso(
        entrada=int(uso.get("prompt_tokens") or 0),
        salida=int(uso.get("completion_tokens") or 0),
        cacheados=cacheados,
        razonamiento=razonamiento,
        neurons=float(uso.get("neurons") or 0.0),
        llamadas=1,
    )


def descubrir_sondas(modulo: str = "edecan_forge_probe.probes") -> tuple[Sonda, ...]:
    """Carga las sondas registradas, del paquete de sondas y de sus submódulos.

    El contrato de registro es una sola cosa: un módulo expone
    `SONDAS: tuple[Sonda, ...]`. Se busca por convención y no por importación
    directa a propósito —el runner tiene que ser útil y testeable antes de que
    exista una sola sonda real, y el CLI tiene que poder decir «no hay sondas
    instaladas» en vez de reventar con un `ImportError` incomprensible.

    Cada `Sonda` es un adaptador fino: envuelve la sonda real (que tiene su
    propia firma, su propio protocolo de proveedor y su propio presupuesto) y la
    presenta con la forma que el runner sabe orquestar.
    """
    try:
        mod = import_module(modulo)
    except ImportError:
        return ()
    encontradas: list[Sonda] = list(_sondas_de(mod))
    ruta = getattr(mod, "__path__", None)
    if ruta is not None:
        for info in sorted(pkgutil.iter_modules(list(ruta)), key=lambda i: i.name):
            try:
                submodulo = import_module(f"{modulo}.{info.name}")
            except ImportError:
                continue
            encontradas.extend(_sondas_de(submodulo))
    vistas: dict[str, Sonda] = {}
    for sonda in encontradas:
        vistas.setdefault(sonda.nombre, sonda)
    return tuple(vistas.values())


def _sondas_de(mod: Any) -> tuple[Sonda, ...]:
    for atributo in ("SONDAS", "PROBES", "sondas", "probes"):
        crudo = getattr(mod, atributo, None)
        if crudo is None:
            continue
        if callable(crudo) and not isinstance(crudo, (list, tuple, dict)):
            crudo = crudo()
        return _normalizar_sondas(crudo)
    return ()


def _normalizar_sondas(crudo: Any) -> tuple[Sonda, ...]:
    """Acepta lista de `Sonda`, lista de callables o dict nombre -> callable."""
    entradas: list[tuple[str | None, Any]]
    if isinstance(crudo, dict):
        entradas = [(str(k), v) for k, v in crudo.items()]
    elif isinstance(crudo, (list, tuple)):
        entradas = [(None, v) for v in crudo]
    else:
        return ()
    salida: list[Sonda] = []
    for nombre, valor in entradas:
        if isinstance(valor, Sonda):
            salida.append(valor)
            continue
        if not callable(valor):
            continue
        etiqueta = nombre or getattr(valor, "nombre", None) or getattr(valor, "__name__", "sonda")
        salida.append(
            Sonda(
                nombre=str(etiqueta),
                ejecutar=valor,
                capability=getattr(valor, "capability", None),
                descripcion=(valor.__doc__ or "").strip().splitlines()[0] if valor.__doc__ else "",
            )
        )
    return tuple(salida)


def filtrar_sondas(sondas: Sequence[Sonda], solo: Sequence[str]) -> tuple[Sonda, ...]:
    """Aplica `--solo`: coincide por nombre exacto, por grupo o por prefijo."""
    if not solo:
        return tuple(sondas)
    claves = {s.strip().lower() for s in solo if s.strip()}
    if not claves:
        return tuple(sondas)
    return tuple(
        s
        for s in sondas
        if s.nombre.lower() in claves
        or s.grupo.lower() in claves
        or any(s.nombre.lower().startswith(f"{c}.") for c in claves)
    )


# --------------------------------------------------------------------------- #
# Persistencia de evidencia y reanudación
# --------------------------------------------------------------------------- #


class SobreResultado(BaseModel):
    """`ProbeResult` en disco, sellado con lo que lo hace comparable o no."""

    revision_sonda: str
    modelo: str
    proveedor: str
    guardado_en: datetime
    resultado: ProbeResult


def _ruta_resultado(directorio: Path, sonda: str) -> Path:
    seguro = re.sub(r"[^A-Za-z0-9._-]", "_", sonda)
    return directorio / "resultados" / f"{seguro}.json"


def guardar_resultado(
    directorio: Path, resultado: ProbeResult, *, modelo: str, proveedor: str, revision: str
) -> Path:
    """Persiste un `ProbeResult` para que mañana no haya que volver a pagarlo."""
    ruta = _ruta_resultado(directorio, resultado.probe)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    sobre = SobreResultado(
        revision_sonda=revision,
        modelo=modelo,
        proveedor=proveedor,
        guardado_en=datetime.now(UTC),
        resultado=resultado,
    )
    ruta.write_text(sobre.model_dump_json(indent=2), encoding="utf-8")
    return ruta


def leer_resultados_guardados(
    directorio: Path, *, modelo: str | None = None, revision: str | None = None
) -> dict[str, ProbeResult]:
    """Lee la evidencia reutilizable del directorio.

    Descarta, sin ruido pero sin excepciones, lo que no sirve: JSON corrupto,
    resultados de otro modelo, de otra revisión de sonda, o sondas que fallaron
    (un fallo no es una medición y hay que reintentarlo).
    """
    carpeta = directorio / "resultados"
    if not carpeta.is_dir():
        return {}
    encontrados: dict[str, ProbeResult] = {}
    for ruta in sorted(carpeta.glob("*.json")):
        try:
            sobre = SobreResultado.model_validate_json(ruta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if modelo is not None and sobre.modelo != modelo:
            continue
        if revision is not None and sobre.revision_sonda != revision:
            continue
        if not sobre.resultado.ok:
            continue
        encontrados[sobre.resultado.probe] = sobre.resultado
    return encontrados


# --------------------------------------------------------------------------- #
# Composición de la ModelCard
# --------------------------------------------------------------------------- #

# Campos escalares que una sonda puede publicar directamente en `detalle`.
_CAMPOS_DIRECTOS: dict[str, type] = {
    "ventana_anunciada": int,
    "usable_context_tokens": int,
    "max_tools_effective": int,
    "max_schema_bytes": int,
    "throughput_tps": float,
    "prefix_cache": bool,
    "vision": bool,
}


def _reliability_de(valor: Any) -> Reliability | None:
    """Acepta un `Reliability` ya construido o su forma serializada."""
    if isinstance(valor, Reliability):
        return valor
    if isinstance(valor, dict) and "successes" in valor and "trials" in valor:
        try:
            return Reliability(successes=int(valor["successes"]), trials=int(valor["trials"]))
        except (TypeError, ValueError):
            return None
    return None


def _latencia_de(valor: Any) -> Latencia | None:
    if isinstance(valor, Latencia):
        return valor
    if isinstance(valor, dict):
        try:
            return Latencia.model_validate(valor)
        except ValueError:
            return None
    return None


def componer_modelcard(
    resultados: Sequence[ProbeResult],
    *,
    modelo: str,
    proveedor: str,
    revision_sonda: str,
    medido_en: datetime | None = None,
    precio_entrada_usd_mtok: float | None = None,
    precio_salida_usd_mtok: float | None = None,
    notas: Sequence[str] = (),
) -> ModelCard:
    """Convierte una lista de `ProbeResult` en la `ModelCard` de la fase 0.

    El contrato de publicación de una sonda, por orden de precedencia:

    1. `detalle["modelcard"]`: dict con nombres de campo de `ModelCard`. Es la
       vía explícita y la que debería usar toda sonda nueva.
    2. `capability` + el campo natural del resultado: `NATIVE_TOOLS` toma
       `reliability` y necesita `detalle["arg_profile"]`; `STRUCTURED_OUTPUT`
       toma `reliability`; `PREFIX_CACHE` y `VISION` toman `bool(valor)`.
    3. Claves sueltas en `detalle` con el nombre exacto de un campo escalar.

    Un resultado con `ok=False` no aporta NADA: una sonda que no pudo correr no
    ha medido un valor malo, no ha medido. Los conflictos (dos sondas que
    escriben el mismo campo con valores distintos) se anotan en `notas` en vez
    de resolverse en silencio.
    """
    card = ModelCard(
        modelo=modelo,
        proveedor=proveedor,
        medido_en=medido_en or datetime.now(UTC),
        revision_sonda=revision_sonda,
        precio_entrada_usd_mtok=precio_entrada_usd_mtok,
        precio_salida_usd_mtok=precio_salida_usd_mtok,
        resultados=list(resultados),
        notas=list(notas),
    )
    conflictos: list[str] = []

    def fijar(campo: str, valor: Any, origen: str) -> None:
        if valor is None:
            return
        actual = getattr(card, campo)
        if actual is not None and actual != valor:
            conflictos.append(f"{campo}: {actual!r} (previo) vs {valor!r} (de {origen})")
            return
        setattr(card, campo, valor)

    for r in resultados:
        if not r.ok:
            continue
        explicito = r.detalle.get("modelcard")
        if isinstance(explicito, dict):
            for campo, valor in explicito.items():
                if campo not in ModelCard.model_fields:
                    conflictos.append(f"{r.probe} publicó un campo inexistente: {campo!r}")
                    continue
                if campo == "native_tools" and isinstance(valor, dict):
                    for perfil, rel in valor.items():
                        _fijar_perfil(card, perfil, _reliability_de(rel), r.probe, conflictos)
                    continue
                if campo in ("structured_output", "bench_success"):
                    fijar(campo, _reliability_de(valor), r.probe)
                    continue
                if campo == "ttft":
                    fijar(campo, _latencia_de(valor), r.probe)
                    continue
                if campo in _CAMPOS_DIRECTOS:
                    fijar(campo, _coaccionar(valor, _CAMPOS_DIRECTOS[campo]), r.probe)
                    continue
                if campo in ("resultados", "notas", "modelo", "proveedor"):
                    continue
                fijar(campo, valor, r.probe)

        match r.capability:
            case Capability.NATIVE_TOOLS:
                _fijar_perfil(
                    card, r.detalle.get("arg_profile"), r.reliability, r.probe, conflictos
                )
            case Capability.STRUCTURED_OUTPUT:
                fijar("structured_output", r.reliability, r.probe)
            case Capability.PREFIX_CACHE:
                if r.valor is not None:
                    fijar("prefix_cache", bool(r.valor), r.probe)
            case Capability.VISION:
                if r.valor is not None:
                    fijar("vision", bool(r.valor), r.probe)
            case _:
                pass

        for campo, tipo in _CAMPOS_DIRECTOS.items():
            if campo in r.detalle:
                fijar(campo, _coaccionar(r.detalle[campo], tipo), r.probe)
        if "bench_success" in r.detalle:
            fijar("bench_success", _reliability_de(r.detalle["bench_success"]), r.probe)
        if r.latencia is not None and (r.probe.startswith("ttft") or "ttft" in r.detalle):
            fijar("ttft", r.latencia, r.probe)

    if conflictos:
        card.notas.append(
            "Conflictos al componer la tarjeta (se conservó el primer valor): "
            + "; ".join(conflictos)
        )
    return card


def _fijar_perfil(
    card: ModelCard,
    perfil: Any,
    reliability: Reliability | None,
    origen: str,
    conflictos: list[str],
) -> None:
    """Escribe `native_tools[perfil]`, exigiendo un `ArgProfile` reconocible."""
    if reliability is None:
        return
    try:
        clave = ArgProfile(perfil)
    except ValueError:
        conflictos.append(f"{origen} publicó un arg_profile desconocido: {perfil!r}")
        return
    previo = card.native_tools.get(clave)
    if previo is not None and previo != reliability:
        conflictos.append(
            f"native_tools[{clave}]: {previo} (previo) vs {reliability} (de {origen})"
        )
        return
    card.native_tools[clave] = reliability


def _coaccionar(valor: Any, tipo: type) -> Any:
    try:
        if tipo is bool:
            return bool(valor)
        return tipo(valor)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Ejecución
# --------------------------------------------------------------------------- #

MotivoCorte = Literal["presupuesto", "deadline", "cancelacion", "error"]


class OpcionesRunner(BaseModel):
    """Todo lo que decide una ejecución. Serializable para dejarlo en evidencia."""

    modelo: str
    proveedor: str = PROVEEDOR_POR_DEFECTO
    revision_sonda: str
    directorio_evidencia: Path
    solo: tuple[str, ...] = ()
    rehacer: bool = False
    presupuesto_usd: float | None = None
    deadline_s: float | None = None
    timeout_sonda_s: float = 900.0
    precio_entrada_usd_mtok: float | None = None
    precio_salida_usd_mtok: float | None = None
    precio_cacheado_usd_mtok: float | None = None
    parar_en_error: bool = False
    """Por defecto una sonda que revienta no aborta el barrido: su fallo queda
    registrado como `ok=False` y las demás siguen midiendo."""


class ResultadoEjecucion(BaseModel):
    """Qué se midió, qué se reutilizó, qué se quedó fuera y por qué."""

    modelcard: ModelCard
    contabilidad: Contabilidad
    ejecutadas: list[str] = Field(default_factory=list)
    reutilizadas: list[str] = Field(default_factory=list)
    pendientes: list[str] = Field(default_factory=list)
    """Sondas seleccionadas que nunca llegaron a correr por un corte."""
    corte: MotivoCorte | None = None
    motivo_corte: str | None = None
    duracion_s: float = 0.0

    @property
    def completa(self) -> bool:
        return self.corte is None and not self.pendientes


@contextlib.contextmanager
def _capturar_sigint(evento: asyncio.Event) -> Iterator[None]:
    """Convierte el primer Ctrl-C en una parada limpia y el segundo en un aborto.

    La primera señal pone el evento: la sonda en curso se cancela, lo ya medido
    se guarda y el informe sale parcial. La segunda restaura el manejador por
    defecto, de modo que un operador impaciente siempre puede matar el proceso.
    """
    loop = asyncio.get_running_loop()
    pulsaciones = 0
    previo: Any = None

    def _al_recibir() -> None:
        nonlocal pulsaciones
        pulsaciones += 1
        evento.set()
        if pulsaciones >= 2:
            _restaurar()
            raise KeyboardInterrupt

    def _handler_sincrono(_sig: int, _frame: Any) -> None:
        _al_recibir()

    def _restaurar() -> None:
        with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
            if usando_loop:
                loop.remove_signal_handler(signal.SIGINT)
            elif previo is not None:
                signal.signal(signal.SIGINT, previo)

    usando_loop = True
    try:
        loop.add_signal_handler(signal.SIGINT, _al_recibir)
    except (NotImplementedError, RuntimeError, ValueError):
        usando_loop = False
        try:
            previo = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, _handler_sincrono)
        except ValueError:
            # No estamos en el hilo principal (caso típico en tests): sin captura.
            previo = None
    try:
        yield
    finally:
        _restaurar()


async def _correr_sonda(
    sonda: Sonda, ctx: ContextoSonda, cancelacion: asyncio.Event, timeout_s: float | None
) -> tuple[ProbeResult, MotivoCorte | None]:
    """Ejecuta una sonda vigilando el reloj y la cancelación.

    Devuelve el resultado y, si procede, el motivo por el que hay que cortar la
    ejecución entera. Una sonda cancelada o expirada produce `ok=False`: se
    reintentará en la próxima ejecución en vez de contaminar la tarjeta.
    """
    inicio = time.monotonic()
    tarea = asyncio.ensure_future(sonda.ejecutar(ctx))
    espera_cancel = asyncio.ensure_future(cancelacion.wait())
    try:
        hechas, _ = await asyncio.wait(
            {tarea, espera_cancel}, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        if not espera_cancel.done():
            espera_cancel.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await espera_cancel

    duracion = time.monotonic() - inicio

    if tarea in hechas:
        try:
            resultado = tarea.result()
        except PresupuestoAgotado as exc:
            return (
                ProbeResult(
                    probe=sonda.nombre,
                    capability=sonda.capability,
                    ok=False,
                    error=f"presupuesto agotado: {exc}",
                    duracion_s=duracion,
                ),
                "presupuesto",
            )
        except asyncio.CancelledError:
            return (
                ProbeResult(
                    probe=sonda.nombre,
                    capability=sonda.capability,
                    ok=False,
                    error="cancelada",
                    duracion_s=duracion,
                ),
                "cancelacion",
            )
        except Exception as exc:  # noqa: BLE001 - una sonda rota no tumba el barrido
            return (
                ProbeResult(
                    probe=sonda.nombre,
                    capability=sonda.capability,
                    ok=False,
                    error=f"{type(exc).__name__}: {exc}",
                    duracion_s=duracion,
                ),
                None,
            )
        if not resultado.duracion_s:
            resultado = resultado.model_copy(update={"duracion_s": duracion})
        return resultado, None

    # Ni terminó ni hubo excepción: o cancelaron o se acabó el tiempo.
    tarea.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await tarea
    if cancelacion.is_set():
        return (
            ProbeResult(
                probe=sonda.nombre,
                capability=sonda.capability,
                ok=False,
                error="cancelada por el operador",
                duracion_s=duracion,
            ),
            "cancelacion",
        )
    return (
        ProbeResult(
            probe=sonda.nombre,
            capability=sonda.capability,
            ok=False,
            error=f"expiró el tiempo de la sonda ({timeout_s:.0f} s)",
            duracion_s=duracion,
        ),
        "deadline",
    )


async def ejecutar(
    sondas: Sequence[Sonda],
    opciones: OpcionesRunner,
    *,
    cancelacion: asyncio.Event | None = None,
    capturar_sigint: bool = True,
    contexto_extra: dict[str, Any] | None = None,
) -> ResultadoEjecucion:
    """Corre el barrido completo y devuelve la tarjeta compuesta.

    Secuencial a propósito (ver el docstring del módulo). Cada sonda se persiste
    en cuanto termina, así que un corte a mitad —presupuesto, reloj o Ctrl-C—
    deja en disco todo lo que llegó a medirse y la siguiente invocación lo
    reutiliza sin volver a pagarlo.
    """
    inicio = time.monotonic()
    evento = cancelacion or asyncio.Event()
    contabilidad = Contabilidad(
        precio_entrada_usd_mtok=opciones.precio_entrada_usd_mtok,
        precio_salida_usd_mtok=opciones.precio_salida_usd_mtok,
        precio_cacheado_usd_mtok=opciones.precio_cacheado_usd_mtok,
    )
    if opciones.presupuesto_usd is not None and not contabilidad.hay_precios:
        raise ValueError(
            "--presupuesto-usd exige --precio-entrada y --precio-salida: sin precios "
            "el gasto no se puede calcular y el tope no se puede hacer cumplir"
        )

    opciones.directorio_evidencia.mkdir(parents=True, exist_ok=True)
    seleccion = filtrar_sondas(sondas, opciones.solo)
    previos = (
        {}
        if opciones.rehacer
        else leer_resultados_guardados(
            opciones.directorio_evidencia,
            modelo=opciones.modelo,
            revision=opciones.revision_sonda,
        )
    )

    resultados: list[ProbeResult] = []
    ejecutadas: list[str] = []
    reutilizadas: list[str] = []
    pendientes: list[str] = []
    notas: list[str] = []
    corte: MotivoCorte | None = None
    motivo: str | None = None

    if opciones.solo:
        notas.append(f"Ejecución parcial: --solo {','.join(opciones.solo)}")
    if not contabilidad.hay_precios:
        notas.append(
            "Sin precios declarados: se contaron tokens y el coste queda en None "
            "(--precio-entrada / --precio-salida)."
        )

    ctx_stack = _capturar_sigint(evento) if capturar_sigint else contextlib.nullcontext()
    with ctx_stack:
        for indice, sonda in enumerate(seleccion):
            if corte is not None:
                pendientes.append(sonda.nombre)
                continue

            previo = previos.get(sonda.nombre)
            if previo is not None:
                resultados.append(previo)
                reutilizadas.append(sonda.nombre)
                continue

            if evento.is_set():
                corte, motivo = "cancelacion", "cancelado por el operador antes de arrancar"
                pendientes.append(sonda.nombre)
                continue

            restante = None
            if opciones.deadline_s is not None:
                restante = opciones.deadline_s - (time.monotonic() - inicio)
                if restante <= 0:
                    corte, motivo = "deadline", f"se agotaron los {opciones.deadline_s:.0f} s"
                    pendientes.append(sonda.nombre)
                    continue

            gasto = contabilidad.coste_usd
            if (
                opciones.presupuesto_usd is not None
                and gasto is not None
                and gasto >= opciones.presupuesto_usd
            ):
                corte = "presupuesto"
                motivo = f"gasto {gasto:.4f} USD ≥ presupuesto {opciones.presupuesto_usd:.4f} USD"
                pendientes.append(sonda.nombre)
                continue

            ctx = ContextoSonda(
                modelo=opciones.modelo,
                proveedor=opciones.proveedor,
                sonda=sonda.nombre,
                directorio_evidencia=opciones.directorio_evidencia,
                contabilidad=contabilidad,
                cancelacion=evento,
                presupuesto_usd=opciones.presupuesto_usd,
                restante_s=restante,
                opciones=dict(contexto_extra or {}),
            )
            timeout = opciones.timeout_sonda_s
            if restante is not None:
                timeout = min(timeout, restante)

            resultado, corte_sonda = await _correr_sonda(sonda, ctx, evento, timeout)
            resultados.append(resultado)
            ejecutadas.append(sonda.nombre)
            guardar_resultado(
                opciones.directorio_evidencia,
                resultado,
                modelo=opciones.modelo,
                proveedor=opciones.proveedor,
                revision=opciones.revision_sonda,
            )
            if corte_sonda is not None:
                corte, motivo = corte_sonda, resultado.error
            elif not resultado.ok and opciones.parar_en_error:
                corte, motivo = "error", resultado.error
            del indice

    if corte is not None:
        notas.append(
            f"Ejecución INCOMPLETA ({corte}): {motivo}. Sondas sin correr: "
            + (", ".join(pendientes) or "ninguna")
        )
    if contabilidad.coste_es_cota_superior:
        notas.append(
            "Coste calculado como COTA SUPERIOR: se facturó la entrada cacheada a "
            "precio de entrada fría por no declararse --precio-cacheada."
        )

    card = componer_modelcard(
        resultados,
        modelo=opciones.modelo,
        proveedor=opciones.proveedor,
        revision_sonda=opciones.revision_sonda,
        precio_entrada_usd_mtok=opciones.precio_entrada_usd_mtok,
        precio_salida_usd_mtok=opciones.precio_salida_usd_mtok,
        notas=notas,
    )
    return ResultadoEjecucion(
        modelcard=card,
        contabilidad=contabilidad,
        ejecutadas=ejecutadas,
        reutilizadas=reutilizadas,
        pendientes=pendientes,
        corte=corte,
        motivo_corte=motivo,
        duracion_s=time.monotonic() - inicio,
    )


# --------------------------------------------------------------------------- #
# Prueba de humo
# --------------------------------------------------------------------------- #

# Verificado: con `max_tokens` corto la respuesta llega con `content` vacío y se
# cobra igual, porque el razonamiento se come el presupuesto de salida. La sonda
# de humo reserva margen de sobra para no diagnosticar un falso «modelo mudo».
_MAX_TOKENS_HUMO = 256


class CargaHumo(BaseModel):
    """Credenciales y destino de la prueba de humo, resueltos desde el entorno."""

    cuenta: str | None = None
    token_presente: bool = False
    modelo: str = MODELO_POR_DEFECTO
    base_url: str = "https://api.cloudflare.com/client/v4"

    @property
    def url(self) -> str:
        return f"{self.base_url}/accounts/{self.cuenta}/ai/run/{self.modelo}"


class ResultadoHumo(BaseModel):
    """Diagnóstico de la prueba de humo, en lenguaje de operador.

    `codigo` es estable y apto para automatizar: `ok`, `falta_token`,
    `falta_cuenta`, `credencial_invalida`, `sin_acceso`, `modelo_inexistente`,
    `red`, `respuesta_ilegible`.
    """

    ok: bool
    codigo: str
    mensaje: str
    modelo: str
    http_status: int | None = None
    codigo_proveedor: int | None = None
    contenido: str | None = None
    razonamiento_presente: bool | None = None
    uso: Uso | None = None
    latencia_s: float | None = None
    remedio: str | None = None


def cargar_humo(*, modelo: str | None = None, entorno: dict[str, str] | None = None) -> CargaHumo:
    """Resuelve credenciales SIN exponer el token: sólo se publica si está o no."""
    env = entorno if entorno is not None else dict(os.environ)
    return CargaHumo(
        cuenta=(env.get(_VAR_CUENTA) or "").strip() or None,
        token_presente=bool((env.get(_VAR_TOKEN) or "").strip()),
        modelo=modelo or (env.get(_VAR_MODELO) or "").strip() or MODELO_POR_DEFECTO,
    )


async def prueba_de_humo(
    *,
    modelo: str | None = None,
    entorno: dict[str, str] | None = None,
    cliente: httpx.AsyncClient | None = None,
    timeout_s: float = 60.0,
) -> ResultadoHumo:
    """Una sola llamada barata que responde: ¿puedo medir este modelo hoy?

    Distingue los cuatro fallos que se confunden entre sí y cuestan una tarde:
    falta el token, falta la cuenta, la credencial no vale, el modelo no existe
    en el catálogo, o existe pero esta cuenta no tiene acceso (403 con
    `code: 5018`, que es exactamente lo que devuelve K3 aquí).

    Cuando `edecan_forge_probe.providers` está disponible se delega en
    `WorkersAIProvider`, que ya sabe reintentar y redactar el token; lo que se
    añade aquí es la CLASIFICACIÓN, porque `WorkersAIProvider.smoke()` colapsa
    todos los fallos en una cadena y «falta el token» y «la cuenta no tiene el
    modelo» son dos tardes distintas. Si ese módulo no importa, se hace la
    llamada cruda: la prueba de humo es lo primero que ejecuta alguien y no
    puede depender de que el resto del paquete compile.

    Nunca imprime ni devuelve el token, ni siquiera parcialmente.
    """
    env = entorno if entorno is not None else dict(os.environ)
    carga = cargar_humo(modelo=modelo, entorno=env)
    if not carga.token_presente:
        return ResultadoHumo(
            ok=False,
            codigo="falta_token",
            mensaje=f"No hay credencial: la variable {_VAR_TOKEN} no está definida o está vacía.",
            modelo=carga.modelo,
            remedio=f"Exporta {_VAR_TOKEN} (o cárgala desde .env) antes de sondear.",
        )
    if not carga.cuenta:
        return ResultadoHumo(
            ok=False,
            codigo="falta_cuenta",
            mensaje=f"No hay cuenta: la variable {_VAR_CUENTA} no está definida o está vacía.",
            modelo=carga.modelo,
            remedio=f"Exporta {_VAR_CUENTA} con el id de cuenta de Cloudflare.",
        )

    token = (env.get(_VAR_TOKEN) or "").strip()
    delegado = await _humo_con_proveedor(carga, token, cliente=cliente, timeout_s=timeout_s)
    if delegado is not None:
        return delegado

    cuerpo_peticion = {
        "messages": [
            {"role": "system", "content": "Responde con una sola palabra."},
            {"role": "user", "content": "Di: listo"},
        ],
        "max_tokens": _MAX_TOKENS_HUMO,
    }
    propio = cliente is None
    http = cliente or httpx.AsyncClient(timeout=timeout_s)
    inicio = time.monotonic()
    try:
        respuesta = await http.post(
            carga.url,
            headers={"Authorization": f"Bearer {token}"},
            json=cuerpo_peticion,
        )
    except httpx.HTTPError as exc:
        return ResultadoHumo(
            ok=False,
            codigo="red",
            mensaje=f"No se pudo hablar con Workers AI: {type(exc).__name__}.",
            modelo=carga.modelo,
            remedio="Revisa la conectividad y vuelve a intentarlo.",
        )
    finally:
        if propio:
            await http.aclose()
    latencia = time.monotonic() - inicio

    try:
        cuerpo = respuesta.json()
    except ValueError:
        cuerpo = {}
    codigo_proveedor = _primer_codigo_error(cuerpo)
    detalle_proveedor = _primer_mensaje_error(cuerpo)

    if respuesta.status_code >= 400:
        return _diagnostico_http(
            respuesta.status_code, codigo_proveedor, detalle_proveedor, carga.modelo, latencia
        )

    resultado = cuerpo.get("result") if isinstance(cuerpo, dict) else None
    contenido, razonamiento = _extraer_mensaje(resultado)
    uso = extraer_uso(cuerpo)
    if contenido is None and razonamiento is None:
        return ResultadoHumo(
            ok=False,
            codigo="respuesta_ilegible",
            mensaje="El modelo respondió 200 pero sin `message.content` reconocible.",
            modelo=carga.modelo,
            http_status=respuesta.status_code,
            uso=uso,
            latencia_s=latencia,
            remedio="Revisa la forma de la respuesta: puede haber cambiado el esquema.",
        )
    aviso = ""
    if not (contenido or "").strip() and razonamiento:
        aviso = (
            " Aviso: `content` llegó vacío y todo el presupuesto de salida se fue en "
            "`reasoning_content`; sube `max_tokens` en las sondas."
        )
    return ResultadoHumo(
        ok=True,
        codigo="ok",
        mensaje=f"Credencial válida y modelo {carga.modelo} accesible.{aviso}",
        modelo=carga.modelo,
        http_status=respuesta.status_code,
        contenido=(contenido or "").strip() or None,
        razonamiento_presente=bool(razonamiento),
        uso=uso,
        latencia_s=latencia,
    )


async def _humo_con_proveedor(
    carga: CargaHumo, token: str, *, cliente: httpx.AsyncClient | None, timeout_s: float
) -> ResultadoHumo | None:
    """Prueba de humo delegada en `providers.WorkersAIProvider`.

    Devuelve `None` —y no una excepción— cuando el módulo de proveedores no está
    disponible o no tiene la forma esperada, para que el llamante haga la
    llamada cruda. Toda la interacción es por `getattr`: un cambio de forma
    degrada a la ruta directa en vez de romper el comando.
    """
    try:
        modulo = import_module("edecan_forge_probe.providers")
        llm = import_module("edecan_llm.base")
    except ImportError:
        return None
    proveedor_cls = getattr(modulo, "WorkersAIProvider", None)
    peticion_cls = getattr(llm, "CompletionRequest", None)
    mensaje_cls = getattr(llm, "ChatMessage", None)
    if proveedor_cls is None or peticion_cls is None or mensaje_cls is None:
        return None

    try:
        # `env_file=None`: las credenciales ya están resueltas aquí, y dejar que
        # el proveedor releyera el `.env` haría que la variable de entorno del
        # operador no mandara sobre el archivo.
        proveedor = proveedor_cls(
            account_id=carga.cuenta,
            api_token=token,
            model=carga.modelo,
            env_file=None,
            http_client=cliente,
            timeout=timeout_s,
        )
    except TypeError:
        return None

    peticion = peticion_cls(
        model=carga.modelo,
        system="Eres un verificador de conectividad. Responde sin preámbulo.",
        messages=[mensaje_cls(role="user", content="Di: listo")],
        max_tokens=_MAX_TOKENS_HUMO,
        temperature=0.0,
    )
    inicio = time.monotonic()
    try:
        salida = await proveedor.complete(peticion)
    except Exception as exc:  # noqa: BLE001 - se clasifica abajo, no se traga
        return _diagnostico_excepcion(exc, carga.modelo, time.monotonic() - inicio)
    finally:
        with contextlib.suppress(Exception):
            await proveedor.aclose()

    contenido = str(getattr(salida, "text", "") or "")
    razonamiento = str(getattr(salida, "reasoning_content", "") or "")
    uso_crudo = getattr(salida, "usage", None)
    uso = Uso(
        entrada=int(getattr(uso_crudo, "input_tokens", 0) or 0),
        salida=int(getattr(uso_crudo, "output_tokens", 0) or 0),
        cacheados=int(getattr(salida, "cached_tokens", 0) or 0),
        neurons=float(getattr(salida, "neurons", 0.0) or 0.0),
    )
    aviso = ""
    if not contenido.strip() and razonamiento:
        aviso = (
            " Aviso: `content` llegó vacío y todo el presupuesto de salida se fue en "
            "`reasoning_content`; sube `max_tokens` en las sondas."
        )
    return ResultadoHumo(
        ok=bool(contenido.strip()),
        codigo="ok" if contenido.strip() else "respuesta_ilegible",
        mensaje=(
            f"Credencial válida y modelo {carga.modelo} accesible.{aviso}"
            if contenido.strip()
            else "El modelo respondió pero sin contenido utilizable."
        ),
        modelo=carga.modelo,
        http_status=200,
        contenido=contenido.strip() or None,
        razonamiento_presente=bool(razonamiento),
        uso=uso,
        latencia_s=float(getattr(salida, "latencia_s", 0.0) or (time.monotonic() - inicio)),
        remedio=None if contenido.strip() else "Sube `max_tokens`: el razonamiento se lo comió.",
    )


def _diagnostico_excepcion(exc: Exception, modelo: str, latencia: float) -> ResultadoHumo:
    """Clasifica un error del proveedor por `status_code` y por el `code` de Cloudflare.

    El mensaje del proveedor ya viene redactado (sin token). Se comprueba el
    5018 sobre el texto porque es la única señal que separa «tu token no vale»
    de «tu token vale pero esta cuenta no tiene este modelo».
    """
    status = getattr(exc, "status_code", None)
    texto = str(exc)
    if status is None and "Faltan credenciales" in texto:
        return ResultadoHumo(
            ok=False,
            codigo="falta_token",
            mensaje=texto,
            modelo=modelo,
            latencia_s=latencia,
            remedio=f"Define {_VAR_TOKEN} y {_VAR_CUENTA} antes de sondear.",
        )
    if status is None:
        return ResultadoHumo(
            ok=False,
            codigo="red",
            mensaje=f"No se pudo hablar con Workers AI: {type(exc).__name__}: {texto}",
            modelo=modelo,
            latencia_s=latencia,
            remedio="Revisa la conectividad y vuelve a intentarlo.",
        )
    codigo = 5018 if "5018" in texto else None
    return _diagnostico_http(int(status), codigo, texto, modelo, latencia)


def _diagnostico_http(
    status: int, codigo: int | None, detalle: str | None, modelo: str, latencia: float
) -> ResultadoHumo:
    """Traduce un error HTTP de Workers AI a una causa accionable."""
    cola = f" Detalle del proveedor: {detalle}" if detalle else ""
    if status in (401, 403) and codigo == 5018:
        return ResultadoHumo(
            ok=False,
            codigo="sin_acceso",
            mensaje=(
                f"El modelo {modelo} existe pero esta cuenta NO tiene acceso "
                f"(HTTP {status}, code 5018).{cola}"
            ),
            modelo=modelo,
            http_status=status,
            codigo_proveedor=codigo,
            latencia_s=latencia,
            remedio=(
                f"Usa un modelo del catálogo de la cuenta o cambia {_VAR_MODELO}. "
                "Kimi K3 está documentado pero no habilitado aquí."
            ),
        )
    if status in (401, 403):
        return ResultadoHumo(
            ok=False,
            codigo="credencial_invalida",
            mensaje=f"Workers AI rechazó la credencial (HTTP {status}).{cola}",
            modelo=modelo,
            http_status=status,
            codigo_proveedor=codigo,
            latencia_s=latencia,
            remedio=(
                f"Regenera el token con permiso «Workers AI: Read» y actualiza {_VAR_TOKEN}. "
                "Comprueba también que el id de cuenta es el correcto."
            ),
        )
    if status == 404:
        return ResultadoHumo(
            ok=False,
            codigo="modelo_inexistente",
            mensaje=f"El modelo {modelo} no existe en este endpoint (HTTP 404).{cola}",
            modelo=modelo,
            http_status=status,
            codigo_proveedor=codigo,
            latencia_s=latencia,
            remedio=f"Revisa el identificador exacto y ajusta {_VAR_MODELO}.",
        )
    return ResultadoHumo(
        ok=False,
        codigo="red",
        mensaje=f"Workers AI devolvió HTTP {status}.{cola}",
        modelo=modelo,
        http_status=status,
        codigo_proveedor=codigo,
        latencia_s=latencia,
        remedio="Reintenta; si persiste, revisa el estado del servicio.",
    )


def _primer_codigo_error(cuerpo: Any) -> int | None:
    if not isinstance(cuerpo, dict):
        return None
    errores = cuerpo.get("errors")
    if isinstance(errores, list) and errores and isinstance(errores[0], dict):
        valor = errores[0].get("code")
        with contextlib.suppress(TypeError, ValueError):
            return int(valor)
    return None


def _primer_mensaje_error(cuerpo: Any) -> str | None:
    if not isinstance(cuerpo, dict):
        return None
    errores = cuerpo.get("errors")
    if isinstance(errores, list) and errores and isinstance(errores[0], dict):
        mensaje = errores[0].get("message")
        return str(mensaje) if mensaje else None
    return None


def _extraer_mensaje(resultado: Any) -> tuple[str | None, str | None]:
    """Devuelve `(content, reasoning_content)` de una respuesta de chat."""
    if not isinstance(resultado, dict):
        return None, None
    mensaje: Any = None
    opciones = resultado.get("choices")
    if isinstance(opciones, list) and opciones and isinstance(opciones[0], dict):
        mensaje = opciones[0].get("message")
    if not isinstance(mensaje, dict):
        if isinstance(resultado.get("response"), str):
            return resultado["response"], None
        return None, None
    contenido = mensaje.get("content")
    razonamiento = mensaje.get("reasoning_content")
    return (
        contenido if isinstance(contenido, str) else None,
        razonamiento if isinstance(razonamiento, str) else None,
    )


# --------------------------------------------------------------------------- #
# Banco de tareas
# --------------------------------------------------------------------------- #

RAICES_POR_DEFECTO: dict[str, Path] = {
    "edecan": Path(os.environ.get("EDECAN_FORGE_REPO_EDECAN", ".")),
    "acme": Path(os.environ.get("EDECAN_FORGE_REPO_ACME", "../acme")),
}


def cargar_banco(modulo: str | None = None) -> tuple[BenchTask, ...]:
    """Carga el banco de tareas por convención, igual que `descubrir_sondas`.

    Se agregan las tareas de todos los módulos encontrados (`bench.edecan`,
    `bench.acme`, …) y se deduplica por `id`: un banco partido por repo es lo
    natural, pero el runner necesita verlo como una sola lista.
    """
    candidatos = (
        [modulo] if modulo else ["edecan_forge_probe.bench", "edecan_forge_probe.banco", "bench"]
    )
    encontradas: dict[str, BenchTask] = {}
    for nombre in candidatos:
        try:
            mod = import_module(nombre)
        except ImportError:
            continue
        for tarea in _tareas_de(mod):
            encontradas.setdefault(tarea.id, tarea)
        ruta = getattr(mod, "__path__", None)
        if ruta is None:
            continue
        for info in sorted(pkgutil.iter_modules(list(ruta)), key=lambda i: i.name):
            try:
                submodulo = import_module(f"{nombre}.{info.name}")
            except ImportError:
                continue
            for tarea in _tareas_de(submodulo):
                encontradas.setdefault(tarea.id, tarea)
    if encontradas:
        return tuple(encontradas.values())
    raise BancoNoDisponible(
        "no hay banco de tareas: se buscó `BANCO`/`TAREAS` (tuple[BenchTask, ...]) en "
        f"{', '.join(candidatos)} y no apareció"
    )


def _tareas_de(mod: Any) -> list[BenchTask]:
    """Recoge de un módulo cualquier colección de `BenchTask` publicada.

    Se aceptan varios nombres (`BANCO`, `TAREAS`, `TAREAS_ACME`, …) porque el
    banco crece por repo y forzar un único nombre obligaría a tocar este archivo
    cada vez que se añade uno.
    """
    tareas: list[BenchTask] = []
    for atributo in sorted(dir(mod)):
        if not (
            atributo.startswith(("BANCO", "TAREAS", "TASKS"))
            or atributo in ("cargar_banco", "banco", "tareas")
        ):
            continue
        crudo = getattr(mod, atributo, None)
        if callable(crudo) and not isinstance(crudo, (list, tuple)):
            try:
                crudo = crudo()
            except TypeError:
                continue
        if isinstance(crudo, (list, tuple)):
            tareas.extend(t for t in crudo if isinstance(t, BenchTask))
    return tareas


class ResultadoCriterio(BaseModel):
    """Comprobación de un criterio del banco sobre un repo concreto."""

    task_id: str
    indice: int
    kind: str
    descripcion: str
    pasa: bool
    """`True` = el criterio se cumple AHORA MISMO."""
    salida: str = ""
    error: str | None = None

    @property
    def mide_algo(self) -> bool:
        """Un criterio `debe_fallar_antes` que ya pasa hoy no mide nada."""
        return not self.pasa


def evaluar_criterio(
    criterio: Criterio, raiz: Path, *, task_id: str, indice: int
) -> ResultadoCriterio:
    """Ejecuta un criterio contra un repo SIN tocar y dice si se cumple ya.

    `command` se lanza con `shell=False` desde `raiz`: el argv del contrato es
    argv de verdad, no una cadena que alguien acaba interpolando.
    """
    base = ResultadoCriterio(
        task_id=task_id,
        indice=indice,
        kind=criterio.kind,
        descripcion=criterio.descripcion,
        pasa=False,
    )
    if not raiz.is_dir():
        return base.model_copy(update={"error": f"la raíz {raiz} no existe"})
    try:
        match criterio.kind:
            case "command":
                if not criterio.comando:
                    return base.model_copy(update={"error": "criterio 'command' sin `comando`"})
                proceso = subprocess.run(  # noqa: S603 - argv explícito, shell=False
                    criterio.comando,
                    cwd=raiz,
                    capture_output=True,
                    text=True,
                    timeout=criterio.timeout_s,
                    check=False,
                )
                salida = (proceso.stdout or "") + (proceso.stderr or "")
                return base.model_copy(
                    update={"pasa": proceso.returncode == 0, "salida": salida[-4000:]}
                )
            case "file_exists":
                if not criterio.ruta:
                    return base.model_copy(update={"error": "criterio 'file_exists' sin `ruta`"})
                return base.model_copy(update={"pasa": (raiz / criterio.ruta).exists()})
            case "file_contains":
                if not criterio.ruta or not criterio.patron:
                    return base.model_copy(
                        update={"error": "criterio 'file_contains' sin `ruta` o `patron`"}
                    )
                archivo = raiz / criterio.ruta
                if not archivo.is_file():
                    return base.model_copy(update={"pasa": False, "salida": "el archivo no existe"})
                texto = archivo.read_text(encoding="utf-8", errors="replace")
                return base.model_copy(
                    update={"pasa": re.search(criterio.patron, texto) is not None}
                )
            case "diff_touches":
                # `patron` es un regex sobre la lista de archivos tocados; `ruta`
                # es la forma corta y más habitual ("el cambio está en este
                # archivo"). Se acepta cualquiera de las dos, y `ruta` se escapa
                # para que un punto de una extensión no actúe como comodín.
                expresion = criterio.patron or (
                    rf"(^|/){re.escape(criterio.ruta.lstrip('/'))}$" if criterio.ruta else ""
                )
                if not expresion:
                    return base.model_copy(
                        update={"error": "criterio 'diff_touches' sin `patron` ni `ruta`"}
                    )
                proceso = subprocess.run(  # noqa: S603 - argv explícito, shell=False
                    ["git", "status", "--porcelain"],
                    cwd=raiz,
                    capture_output=True,
                    text=True,
                    timeout=criterio.timeout_s,
                    check=False,
                )
                if proceso.returncode != 0:
                    return base.model_copy(update={"error": (proceso.stderr or "")[-2000:]})
                tocados = [
                    linea[3:].strip() for linea in proceso.stdout.splitlines() if len(linea) > 3
                ]
                patron = re.compile(expresion)
                return base.model_copy(
                    update={
                        "pasa": any(patron.search(t) for t in tocados),
                        "salida": "\n".join(tocados[:50]),
                    }
                )
            case _:
                return base.model_copy(update={"error": f"kind desconocido: {criterio.kind}"})
    except subprocess.TimeoutExpired:
        return base.model_copy(update={"error": f"expiró a los {criterio.timeout_s} s"})
    except OSError as exc:
        return base.model_copy(update={"error": f"{type(exc).__name__}: {exc}"})


def verificar_banco(
    tareas: Sequence[BenchTask], raices: dict[str, Path] | None = None
) -> list[ResultadoCriterio]:
    """Comprueba que cada criterio `debe_fallar_antes` FALLA sobre el repo limpio.

    Es la única defensa contra el vicio clásico de un banco: tareas cuyo criterio
    ya se cumple antes de que el agente escriba una línea. Ese banco mide cero y
    da una tasa de éxito falsamente alta.
    """
    mapa = {**RAICES_POR_DEFECTO, **(raices or {})}
    salida: list[ResultadoCriterio] = []
    for tarea in tareas:
        raiz = mapa.get(tarea.repo)
        for indice, criterio in enumerate(tarea.criterios):
            if not criterio.debe_fallar_antes:
                continue
            if raiz is None:
                salida.append(
                    ResultadoCriterio(
                        task_id=tarea.id,
                        indice=indice,
                        kind=criterio.kind,
                        descripcion=criterio.descripcion,
                        pasa=False,
                        error=f"no hay raíz configurada para el repo {tarea.repo!r}",
                    )
                )
                continue
            salida.append(evaluar_criterio(criterio, raiz, task_id=tarea.id, indice=indice))
    return salida
