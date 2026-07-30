"""Evidencia de ejecución del agente del IDE — la prueba, no la afirmación.

Antigravity lo llama "evidencia de ejecución": el agente no dice "listo",
muestra la PRUEBA de que funciona. Este módulo es el recolector: asocia a un
``step_id`` (opaco -- puede ser el ``id`` de un ``PlanStep`` de
``ide_plan.py``, o cualquier otro identificador que use quien integre) las
pruebas de que ese paso se completó, las guarda por referencia y permite
presentarlas juntas.

Tres decisiones de diseño explícitas, porque el encargo las pide razonadas:

1. **Éxito y fallo son evidencia de igual rango -- el fallo es la más útil.**
   Un test en rojo es información accionable ("esto NO funciona, y así se
   ve"), no un evento a esconder. Cada ``Evidencia`` lleva un ``veredicto``
   explícito (``"exito" | "fallo" | "indeterminado"``) calculado a partir de
   HECHOS medibles -- código de salida, código HTTP -- nunca de lo que diga
   el modelo. ``paquete_para_paso`` calcula además un ``veredicto_global``
   por paso: si CUALQUIER evidencia del paso es ``"fallo"``, el paquete
   entero es ``"fallo"``, sin importar cuántas otras salieron en verde --
   nueve comandos en verde no borran el rojo del décimo.

2. **"¿Por qué crees que esto funciona?" se responde con hechos, no con la
   palabra del modelo.** Cada ``Evidencia`` lleva un campo ``motivo``: una
   frase corta generada por ESTE módulo a partir de datos medidos (código de
   salida, línea final de ``pytest``, código de estado HTTP). Nunca es texto
   que el LLM redactó sobre sí mismo -- es la traducción literal de un
   número a una oración. Ese es el punto entero del encargo: que la
   respuesta a esa pregunta salga del recolector, no del agente.

3. **Se guarda por referencia, se presenta lo accionable -- mismo criterio
   que ``config/modelos.yml`` § ``razonamiento.siempre_al_cas_nunca_en_linea``
   aplica al razonamiento del modelo.** Un ``pytest -q`` de un repo de 270k
   líneas puede escupir megabytes de salida. El texto CRUDO (stdout, stderr,
   cuerpo HTTP, bytes de una captura) va a un almacén direccionado por
   contenido (CAS) igual que ``ide_checkpoints.CheckpointStore`` ya hace
   para el contenido "antes" de un archivo -- deduplicado por hash,
   escritura atómica, sin depender de ``packages/forge-kernel`` (este
   paquete está pensado para instalarse solo, ver ``pyproject.toml``). Lo
   que SÍ viaja inline en el manifiesto es el ``resumen``: ya acotado de
   antemano (``ide_verificacion.extraer_resumen`` tapa a
   ``MAX_CHARS_RESUMEN`` = 4000 caracteres; el resumen de HTTP/captura de
   este módulo tiene su propio tope). El journal de una corrida de miles de
   pruebas queda legible; el byte completo sigue ahí para quien lo pida por
   ``leer_blob``.

Integración prevista (no se toca ``ide_workers_agent.py``/``ide_sessions.py``
desde aquí, ver encargo -- lo cablea el humano):

- La herramienta ``ejecutar_comando`` del agente (siete herramientas de
  ``ide_workers_agent.py``) es el productor natural de ``registrar_comando``:
  ya tiene ``stdout``/``stderr``/``exit_code`` a mano tras correr el proceso.
- El bucle ``ide_verificacion.ejecutar_hasta_que_pase`` es el productor
  natural de ``registrar_resultado_comando`` -- PERO ``ResultadoIntento`` no
  expone el ``stdout``/``stderr`` crudo que capturó internamente (los
  descarta tras extraer el resumen). Quien cablee esto tiene dos caminos:
  (a) extender ``ejecutar_intento`` para que también devuelva el texto
  crudo, o (b) que el llamador que ya tiene esos bytes a mano (porque corrió
  el subprocess él mismo antes de invocar el bucle) se los pase aparte a
  ``registrar_resultado_comando``. Este módulo no decide por cuál optar --
  no toca ``ide_verificacion.py`` a propósito, es un archivo nuevo.
- ``registrar_captura`` es el punto de enganche para OJOS 2 propiamente
  dicho (que el agente tome sus propias capturas de pantalla): quien
  construya esa pieza le pasa los bytes PNG/JPEG ya capturados; este módulo
  no sabe tomar screenshots, solo archivarlos como prueba.
- ``registrar_http`` sirve para la respuesta de un endpoint recién creado
  (p. ej. tras levantar el servidor y pegarle un ``curl``/``httpx`` de
  prueba desde ``ejecutar_comando``).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from edecan_companion.ide_verificacion import ResultadoIntento, extraer_resumen

TipoEvidencia = Literal["comando", "captura", "http"]
Veredicto = Literal["exito", "fallo", "indeterminado"]

# --------------------------------------------------------------------------- #
# Topes -- mismo espíritu que ``ide_checkpoints``: nunca reventar por un
# archivo/proceso enorme, siempre acotar y decirlo.
# --------------------------------------------------------------------------- #

MAX_BYTES_BLOB_CRUDO = 64 * 1024 * 1024
"""Tope duro por blob individual (stdout/stderr/cuerpo HTTP/imagen). Por
encima de esto se recorta manteniendo cabeza Y cola (a diferencia de
``ide_verificacion._recortar``, que prioriza la cola porque ahí suele estar
la causa de un fallo de test/compilador -- aquí el contenido es arbitrario
-- build logs, volcados JSON --, así que conservar los dos extremos da más
chance de que sobreviva la parte útil)."""

MAX_CHARS_PREVIEW_HTTP = 4000
"""Tope del preview inline del cuerpo HTTP -- mismo orden de magnitud que
``ide_verificacion.MAX_CHARS_RESUMEN``, por la misma razón: acotado de
antemano para que el manifiesto no crezca sin límite."""

MAX_CHARS_ETIQUETA = 200

TTL_HORAS_POR_DEFECTO = 72
"""Una evidencia vive 3 días por defecto -- igual criterio que
``ide_checkpoints.DEFAULT_TTL_HOURS``: alcanza para la sesión de trabajo que
la generó, no acumula disco indefinidamente en un repo de 270k líneas donde
correr la batería completa de pruebas puede pasar muchas veces al día."""

_CABECERAS_SENSIBLES = frozenset(
    {"authorization", "cookie", "set-cookie", "x-api-key", "proxy-authorization"}
)
"""Cabeceras que NUNCA se guardan en claro -- ``config/modelos.yml`` §
``razonamiento.redaccion_obligatoria`` aplica el mismo criterio al
razonamiento del modelo: la evidencia de un endpoint es exactamente el lugar
donde un token de sesión o una cookie de auth puede colarse sin que nadie lo
note, porque "funcionó" es la señal que todos miran, no la cabecera."""


class EvidenciaError(ValueError):
    """Solicitud de evidencia inválida, o evidencia/blob inexistente."""


def _now_us() -> int:
    return int(time.time() * 1_000_000)


def _digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validar_step_id(step_id: Any) -> str:
    if not isinstance(step_id, str) or not step_id.strip():
        raise EvidenciaError("step_id no puede estar vacío.")
    return step_id.strip()


def _limpiar_etiqueta(etiqueta: str | None) -> str | None:
    if etiqueta is None:
        return None
    if not isinstance(etiqueta, str):
        raise EvidenciaError("etiqueta debe ser texto.")
    limpia = etiqueta.strip()
    if not limpia:
        return None
    return limpia[:MAX_CHARS_ETIQUETA]


def _preparar_blob(data: bytes, max_bytes: int) -> tuple[bytes, bool]:
    """Recorta ``data`` a ``max_bytes`` conservando cabeza y cola (ver
    docstring de ``MAX_BYTES_BLOB_CRUDO``). Devuelve ``(datos, truncado)``."""
    if len(data) <= max_bytes:
        return data, False
    marcador = b"\n...[recortado por tamano -- ver resumen para lo accionable]...\n"
    mitad = max(0, (max_bytes - len(marcador)) // 2)
    cabeza = data[:mitad]
    resto = max_bytes - len(marcador) - mitad
    cola = data[-resto:] if resto > 0 else b""
    return cabeza + marcador + cola, True


def _preview_texto(texto: str, limite: int) -> tuple[str, bool]:
    """Preview acotado de un texto arbitrario (cuerpo HTTP). A diferencia de
    ``ide_verificacion._recortar`` (que prioriza la cola, pensado para
    tracebacks), aquí se prioriza la CABEZA: un cuerpo de error HTTP casi
    siempre trae el mensaje relevante al principio (``{"error": "..."}``),
    no al final de un volcado arbitrario."""
    if len(texto) <= limite:
        return texto, False
    return texto[:limite] + "\n[...recortado...]", True


def _redactar_cabeceras(headers: Mapping[str, str] | None) -> dict[str, str]:
    if not headers:
        return {}
    redactadas: dict[str, str] = {}
    for clave, valor in headers.items():
        if str(clave).strip().casefold() in _CABECERAS_SENSIBLES:
            redactadas[str(clave)] = "«redactado»"
        else:
            redactadas[str(clave)] = str(valor)
    return redactadas


def _veredicto_por_exit_code(exit_code: int | None) -> Veredicto:
    if exit_code == 0:
        return "exito"
    if exit_code is not None:
        return "fallo"
    return "indeterminado"


def _veredicto_por_status_http(status_code: int) -> Veredicto:
    if 200 <= status_code < 400:
        return "exito"
    if 400 <= status_code < 600:
        return "fallo"
    return "indeterminado"


def _motivo_comando(
    *,
    veredicto: Veredicto,
    exit_code: int | None,
    resumen_texto: str,
    duracion_segundos: float | None,
) -> str:
    duracion_txt = f" en {duracion_segundos:.2f}s" if duracion_segundos is not None else ""
    if veredicto == "exito":
        cola = next(
            (linea.strip() for linea in reversed(resumen_texto.splitlines()) if linea.strip()),
            "",
        )
        detalle = f" ({cola})" if cola else ""
        return f"Terminó con código de salida 0{duracion_txt}.{detalle}"
    if veredicto == "fallo":
        extracto = resumen_texto.strip()[:280]
        sufijo = f" {extracto}" if extracto else ""
        return f"Terminó con código de salida {exit_code}{duracion_txt}.{sufijo}"
    return f"No se pudo completar la verificación (código de salida {exit_code}){duracion_txt}."


def _motivo_http(*, veredicto: Veredicto, method: str, url: str, status_code: int) -> str:
    base = f"{method.upper()} {url} respondió {status_code}."
    if veredicto == "fallo":
        return base + " Código de error HTTP."
    if veredicto == "indeterminado":
        return base + " Código fuera del rango 2xx-5xx reconocido como éxito/fallo."
    return base


def _motivo_captura(*, mime_type: str, tamano_bytes: int) -> str:
    return (
        f"Captura de pantalla adjunta ({mime_type}, {tamano_bytes} bytes). "
        "No implica éxito ni fallo por sí sola -- queda a criterio de quien la revise."
    )


@dataclass
class Evidencia:
    """Una prueba concreta asociada a un paso de trabajo.

    ``refs`` mapea un nombre de campo (``"stdout"``, ``"imagen"``,
    ``"cuerpo"``...) al digest sha256 del blob correspondiente en el CAS;
    ``truncado`` dice, por el mismo campo, si ese blob es un recorte del
    original (ver ``MAX_BYTES_BLOB_CRUDO``). ``resumen`` y ``metadata`` son
    SIEMPRE acotados por construcción -- nunca cargan un blob completo.
    """

    id: str
    step_id: str
    kind: TipoEvidencia
    veredicto: Veredicto
    motivo: str
    created_at_us: int
    expires_at_us: int
    resumen: dict[str, Any]
    metadata: dict[str, Any]
    refs: dict[str, str] = field(default_factory=dict)
    truncado: dict[str, bool] = field(default_factory=dict)
    etiqueta: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "version": 1,
            "id": self.id,
            "step_id": self.step_id,
            "kind": self.kind,
            "veredicto": self.veredicto,
            "motivo": self.motivo,
            "created_at_us": self.created_at_us,
            "expires_at_us": self.expires_at_us,
            "resumen": self.resumen,
            "metadata": self.metadata,
            "refs": self.refs,
            "truncado": self.truncado,
            "etiqueta": self.etiqueta,
        }

    @staticmethod
    def from_json(raw: dict[str, Any]) -> Evidencia:
        return Evidencia(
            id=str(raw["id"]),
            step_id=str(raw["step_id"]),
            kind=raw["kind"],
            veredicto=raw["veredicto"],
            motivo=str(raw.get("motivo") or ""),
            created_at_us=int(raw["created_at_us"]),
            expires_at_us=int(raw["expires_at_us"]),
            resumen=dict(raw.get("resumen") or {}),
            metadata=dict(raw.get("metadata") or {}),
            refs=dict(raw.get("refs") or {}),
            truncado=dict(raw.get("truncado") or {}),
            etiqueta=raw.get("etiqueta"),
        )

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "step_id": self.step_id,
            "kind": self.kind,
            "veredicto": self.veredicto,
            "motivo": self.motivo,
            "created_at_us": self.created_at_us,
            "resumen": self.resumen,
            "metadata": self.metadata,
            "refs": dict(self.refs),
            "truncado": dict(self.truncado),
            "etiqueta": self.etiqueta,
        }


class AlmacenEvidencia:
    """Crea, lista y presenta evidencia de ejecución por paso de trabajo.

    Layout en disco bajo ``state_dir / "ide-evidencia"``::

        blobs/<2 hex>/<2 hex>/<64 hex sha256>   # crudo (stdout, imagen, cuerpo HTTP...)
        items/<evidencia_id>.json                # manifiesto de cada evidencia
        tmp/                                      # staging para escrituras atómicas

    Sin SQLite (a diferencia de ``forge-kernel/cas.py``): el volumen esperado
    -- evidencia por paso de un turno de agente -- no justifica una tabla de
    metadatos aparte; ``items/*.json`` ya lleva todo lo que hace falta y
    ``_load_all`` los recorre igual que ``ide_checkpoints._load_all``.
    """

    def __init__(
        self,
        state_dir: Path,
        *,
        ttl_hours: float = TTL_HORAS_POR_DEFECTO,
        max_bytes_blob_crudo: int = MAX_BYTES_BLOB_CRUDO,
    ) -> None:
        self.root = Path(state_dir) / "ide-evidencia"
        self.blobs_dir = self.root / "blobs"
        self.items_dir = self.root / "items"
        self.tmp_dir = self.root / "tmp"
        for directorio in (self.blobs_dir, self.items_dir, self.tmp_dir):
            directorio.mkdir(parents=True, exist_ok=True)
        self.ttl_us = int(ttl_hours * 3600 * 1_000_000)
        self.max_bytes_blob_crudo = max_bytes_blob_crudo

    # --------------------------------------------------------------- #
    # Blob store (CAS) mínimo -- mismo patrón que
    # ``ide_checkpoints.CheckpointStore``: sin sqlite, dedup por hash,
    # escritura atómica vía tempfile + os.replace.
    # --------------------------------------------------------------- #

    def _blob_path(self, digest: str) -> Path:
        return self.blobs_dir / digest[0:2] / digest[2:4] / digest

    def _put_blob(self, data: bytes) -> tuple[str, bool]:
        recortado, truncado = _preparar_blob(data, self.max_bytes_blob_crudo)
        digest = _digest_bytes(recortado)
        destino = self._blob_path(digest)
        if not destino.is_file():
            destino.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=self.tmp_dir, prefix=".blob-")
            tmp_path = Path(tmp_name)
            try:
                with os.fdopen(fd, "wb") as tmp_f:
                    tmp_f.write(recortado)
                os.replace(tmp_path, destino)
            except BaseException:
                tmp_path.unlink(missing_ok=True)
                raise
        return digest, truncado

    def _get_blob(self, digest: str) -> bytes:
        try:
            return self._blob_path(digest).read_bytes()
        except FileNotFoundError as exc:
            raise EvidenciaError(
                f"El blob de evidencia {digest[:12]}… ya no existe (¿se purgó?)."
            ) from exc

    # --------------------------------------------------------------- #
    # Persistencia del manifiesto -- mismo patrón que
    # ``ide_checkpoints._save``/``_load``/``_load_all``.
    # --------------------------------------------------------------- #

    def _item_path(self, evidencia_id: str) -> Path:
        return self.items_dir / f"{evidencia_id}.json"

    def _save(self, item: Evidencia) -> None:
        path = self._item_path(item.id)
        tmp_path = self.tmp_dir / f".item-{uuid.uuid4().hex}.tmp"
        tmp_path.write_text(
            json.dumps(item.to_json(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        try:
            tmp_path.chmod(0o600)
        except OSError:
            pass
        os.replace(tmp_path, path)

    def _load(self, evidencia_id: str) -> Evidencia:
        path = self._item_path(evidencia_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EvidenciaError(f"Evidencia no encontrada: {evidencia_id}.") from exc
        return Evidencia.from_json(raw)

    def _load_all(self) -> list[Evidencia]:
        rows: list[Evidencia] = []
        for archivo in self.items_dir.glob("*.json"):
            try:
                raw = json.loads(archivo.read_text(encoding="utf-8"))
                rows.append(Evidencia.from_json(raw))
            except (OSError, json.JSONDecodeError, KeyError):
                continue
        return rows

    # --------------------------------------------------------------- #
    # Registro -- un método por tipo de evidencia.
    # --------------------------------------------------------------- #

    def registrar_comando(
        self,
        step_id: str,
        *,
        argv: list[str],
        stdout: str,
        stderr: str,
        exit_code: int | None,
        cwd: str | Path | None = None,
        duracion_segundos: float | None = None,
        etiqueta: str | None = None,
    ) -> dict[str, Any]:
        """Registra la evidencia de correr UN comando directamente (el
        camino natural para la herramienta ``ejecutar_comando`` del agente,
        que ya tiene ``stdout``/``stderr``/``exit_code`` en mano tras
        correr el proceso). Para el bucle de ``ide_verificacion``, usar
        ``registrar_resultado_comando``."""

        step_id = _validar_step_id(step_id)
        resumen = extraer_resumen(stdout, stderr).resumen()
        veredicto = _veredicto_por_exit_code(exit_code)
        motivo = _motivo_comando(
            veredicto=veredicto,
            exit_code=exit_code,
            resumen_texto=resumen["texto"],
            duracion_segundos=duracion_segundos,
        )
        digest_stdout, truncado_stdout = self._put_blob(stdout.encode("utf-8", errors="replace"))
        digest_stderr, truncado_stderr = self._put_blob(stderr.encode("utf-8", errors="replace"))
        metadata = {
            "argv": list(argv),
            "cwd": str(cwd) if cwd is not None else None,
            "exit_code": exit_code,
            "duracion_segundos": (
                round(duracion_segundos, 2) if duracion_segundos is not None else None
            ),
        }
        return self._registrar(
            step_id=step_id,
            kind="comando",
            veredicto=veredicto,
            motivo=motivo,
            resumen=resumen,
            metadata=metadata,
            refs={"stdout": digest_stdout, "stderr": digest_stderr},
            truncado={"stdout": truncado_stdout, "stderr": truncado_stderr},
            etiqueta=etiqueta,
        )

    def registrar_resultado_comando(
        self,
        step_id: str,
        resultado: ResultadoIntento,
        *,
        stdout: str,
        stderr: str,
        etiqueta: str | None = None,
    ) -> dict[str, Any]:
        """Registra la evidencia de un intento ya clasificado por
        ``ide_verificacion.ejecutar_intento``/``ejecutar_hasta_que_pase``.

        ``stdout``/``stderr`` deben venir aparte (ver nota de integración en
        el docstring del módulo: ``ResultadoIntento`` no los expone). Un
        intento que ni siquiera pudo ejecutarse (``tipo_falla ==
        "no_se_pudo_ejecutar"`` -- ejecutable ausente, timeout) se registra
        como ``"indeterminado"``, no ``"fallo"``: no es prueba de que el
        código esté roto, es prueba de que la verificación no corrió.
        """

        step_id = _validar_step_id(step_id)
        if resultado.aprobado:
            veredicto: Veredicto = "exito"
        elif resultado.tipo_falla == "fallo_de_verificacion":
            veredicto = "fallo"
        else:
            veredicto = "indeterminado"

        resumen = extraer_resumen(stdout, stderr).resumen()
        motivo = _motivo_comando(
            veredicto=veredicto,
            exit_code=resultado.exit_code,
            resumen_texto=resumen["texto"],
            duracion_segundos=resultado.duracion_segundos,
        )
        digest_stdout, truncado_stdout = self._put_blob(stdout.encode("utf-8", errors="replace"))
        digest_stderr, truncado_stderr = self._put_blob(stderr.encode("utf-8", errors="replace"))
        metadata = {
            "argv": list(resultado.argv),
            "exit_code": resultado.exit_code,
            "tipo_falla": resultado.tipo_falla,
            "motivo_no_ejecutable": resultado.motivo_no_ejecutable,
            "duracion_segundos": round(resultado.duracion_segundos, 2),
            "intento": resultado.intento,
        }
        return self._registrar(
            step_id=step_id,
            kind="comando",
            veredicto=veredicto,
            motivo=motivo,
            resumen=resumen,
            metadata=metadata,
            refs={"stdout": digest_stdout, "stderr": digest_stderr},
            truncado={"stdout": truncado_stdout, "stderr": truncado_stderr},
            etiqueta=etiqueta,
        )

    def registrar_captura(
        self,
        step_id: str,
        *,
        imagen: bytes,
        mime_type: str,
        etiqueta: str | None = None,
    ) -> dict[str, Any]:
        """Archiva una captura de pantalla como evidencia. Este módulo no
        toma capturas ni las valida como imagen decodificable (eso es
        ``ide_imagenes.py``, un problema distinto: ahí importa si el MODELO
        puede verla; aquí solo importa archivar la prueba) -- ``mime_type``
        lo declara quien llama. El veredicto siempre es ``"indeterminado"``:
        una captura por sí sola no prueba éxito ni fallo, es quien la revise
        (persona o auditor posterior) el que la interpreta."""

        step_id = _validar_step_id(step_id)
        if not imagen:
            raise EvidenciaError("La captura no puede estar vacía.")
        digest, truncado = self._put_blob(imagen)
        resumen = {"mime_type": mime_type, "tamano_bytes": len(imagen)}
        motivo = _motivo_captura(mime_type=mime_type, tamano_bytes=len(imagen))
        return self._registrar(
            step_id=step_id,
            kind="captura",
            veredicto="indeterminado",
            motivo=motivo,
            resumen=resumen,
            metadata={"mime_type": mime_type},
            refs={"imagen": digest},
            truncado={"imagen": truncado},
            etiqueta=etiqueta,
        )

    def registrar_http(
        self,
        step_id: str,
        *,
        method: str,
        url: str,
        status_code: int,
        cuerpo: bytes | str,
        headers: Mapping[str, str] | None = None,
        content_type: str | None = None,
        etiqueta: str | None = None,
    ) -> dict[str, Any]:
        """Archiva la respuesta de un endpoint como evidencia (p. ej. tras
        levantar el servidor y pegarle una petición de prueba al endpoint
        que el agente acaba de crear). El veredicto sale del código de
        estado HTTP: 2xx/3xx -> éxito, 4xx/5xx -> fallo, cualquier otro
        (1xx, o un valor fuera de rango que igual se acepta) ->
        indeterminado. Las cabeceras sensibles (``Authorization``,
        ``Cookie``...) se redactan antes de guardarse -- ver
        ``_CABECERAS_SENSIBLES``."""

        step_id = _validar_step_id(step_id)
        cuerpo_bytes = (
            cuerpo.encode("utf-8", errors="replace") if isinstance(cuerpo, str) else cuerpo
        )
        veredicto = _veredicto_por_status_http(status_code)
        motivo = _motivo_http(veredicto=veredicto, method=method, url=url, status_code=status_code)
        texto_decodificado = cuerpo_bytes.decode("utf-8", errors="replace")
        preview, preview_truncado = _preview_texto(texto_decodificado, MAX_CHARS_PREVIEW_HTTP)
        digest, blob_truncado = self._put_blob(cuerpo_bytes)
        resumen = {
            "status_code": status_code,
            "preview": preview,
            "truncado": preview_truncado,
        }
        metadata = {
            "method": method.upper(),
            "url": url,
            "status_code": status_code,
            "content_type": content_type,
            "headers": _redactar_cabeceras(headers),
        }
        return self._registrar(
            step_id=step_id,
            kind="http",
            veredicto=veredicto,
            motivo=motivo,
            resumen=resumen,
            metadata=metadata,
            refs={"cuerpo": digest},
            truncado={"cuerpo": blob_truncado},
            etiqueta=etiqueta,
        )

    def _registrar(
        self,
        *,
        step_id: str,
        kind: TipoEvidencia,
        veredicto: Veredicto,
        motivo: str,
        resumen: dict[str, Any],
        metadata: dict[str, Any],
        refs: dict[str, str],
        truncado: dict[str, bool],
        etiqueta: str | None,
    ) -> dict[str, Any]:
        ahora = _now_us()
        item = Evidencia(
            id=uuid.uuid4().hex,
            step_id=step_id,
            kind=kind,
            veredicto=veredicto,
            motivo=motivo,
            created_at_us=ahora,
            expires_at_us=ahora + self.ttl_us,
            resumen=resumen,
            metadata=metadata,
            refs=refs,
            truncado=truncado,
            etiqueta=_limpiar_etiqueta(etiqueta),
        )
        self._save(item)
        return item.public()

    # --------------------------------------------------------------- #
    # Lectura y presentación conjunta.
    # --------------------------------------------------------------- #

    def obtener(self, evidencia_id: str) -> dict[str, Any]:
        return self._load(evidencia_id).public()

    def leer_blob(self, evidencia_id: str, campo: str) -> bytes:
        """Devuelve el contenido crudo (potencialmente recortado, ver
        ``truncado`` en el manifiesto) de un campo de la evidencia -- p. ej.
        ``leer_blob(id, "stdout")`` para ver la salida completa que el
        ``resumen`` solo extractó."""
        item = self._load(evidencia_id)
        digest = item.refs.get(campo)
        if digest is None:
            raise EvidenciaError(
                f"La evidencia {evidencia_id} no tiene un campo «{campo}»; "
                f"campos disponibles: {sorted(item.refs)}."
            )
        return self._get_blob(digest)

    def listar_para_paso(self, step_id: str) -> list[dict[str, Any]]:
        """Toda la evidencia viva (no vencida) de ``step_id``, en orden
        cronológico -- así se ve la secuencia real de intentos."""
        ahora = _now_us()
        rows = [
            item.public()
            for item in self._load_all()
            if item.step_id == step_id and item.expires_at_us > ahora
        ]
        rows.sort(key=lambda row: row["created_at_us"])
        return rows

    def paquete_para_paso(self, step_id: str) -> dict[str, Any]:
        """Vista conjunta de toda la evidencia de un paso -- la respuesta
        directa a "¿esto se completó de verdad?": el conteo por veredicto y
        el veredicto global (ver punto 1 del docstring del módulo: el fallo
        domina, nunca lo tapa un éxito posterior en la misma lista)."""
        items = self.listar_para_paso(step_id)
        conteo = {"exito": 0, "fallo": 0, "indeterminado": 0}
        for item in items:
            conteo[item["veredicto"]] += 1
        if conteo["fallo"] > 0:
            veredicto_global: Veredicto = "fallo"
        elif conteo["exito"] > 0:
            veredicto_global = "exito"
        else:
            veredicto_global = "indeterminado"
        return {
            "step_id": step_id,
            "items": items,
            "conteo": conteo,
            "veredicto_global": veredicto_global,
        }

    # --------------------------------------------------------------- #
    # Mantenimiento.
    # --------------------------------------------------------------- #

    def descartar_paso(self, step_id: str) -> int:
        """Borra los manifiestos de evidencia de ``step_id`` (no los blobs
        -- ``purgar_vencidas`` se encarga de esos, igual que
        ``ide_checkpoints.discard``/``prune_expired``). Devuelve cuántos se
        borraron."""
        borrados = 0
        for item in self._load_all():
            if item.step_id == step_id:
                self._item_path(item.id).unlink(missing_ok=True)
                borrados += 1
        return borrados

    def purgar_vencidas(self) -> dict[str, Any]:
        """Barre evidencia vencida (por TTL) y los blobs que ya nadie
        referencia -- mismo "marca y barre" que
        ``ide_checkpoints.prune_expired``."""
        ahora = _now_us()
        todas = self._load_all()
        vencidas = [item for item in todas if item.expires_at_us <= ahora]
        for item in vencidas:
            self._item_path(item.id).unlink(missing_ok=True)

        vivos = {
            digest
            for item in todas
            if item.expires_at_us > ahora
            for digest in item.refs.values()
        }
        blobs_borrados = 0
        bytes_liberados = 0
        if self.blobs_dir.is_dir():
            for blob_path in self.blobs_dir.rglob("*"):
                if not blob_path.is_file() or blob_path.name in vivos:
                    continue
                try:
                    bytes_liberados += blob_path.stat().st_size
                    blob_path.unlink()
                    blobs_borrados += 1
                except OSError:
                    continue
        return {
            "evidencias_removidas": len(vencidas),
            "blobs_removidos": blobs_borrados,
            "bytes_liberados": bytes_liberados,
        }
