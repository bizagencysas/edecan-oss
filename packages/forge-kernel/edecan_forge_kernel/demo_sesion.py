"""Demostración ejecutable del núcleo de Forge — una sesión de juguete, de punta a punta.

`uv run python -m edecan_forge_kernel.demo_sesion`

SIN red y SIN modelo (regla dura del encargo: "prohibido llamar a la red"). Todo lo que este
script hace es orquestar las piezas YA escritas del paquete (`contracts.reduce`, `SqliteJournal`,
`Cas`, `EventBus`) exactamente como lo haría un host real, para que la invariante 2 ("el journal
es la única fuente de verdad; todo lo demás es proyección") deje de ser una frase en un
`docstring` y se vuelva algo que se puede ver pasar.

Qué demuestra, en orden:

  1. Abre una sesión (`session.create` vía `reduce()`) y un workspace de juguete.
  2. Un agente pide una herramienta, se admite (con sus subestados), se despacha, arranca y
     completa — la máquina de estados COMPLETA de `ToolCallState`/`AdmissionSubstate`, escrita
     en el journal real (`SqliteJournal`) a través del canal durable único (`EventBus.publish`).
  3. El resultado "grande" de la herramienta va al CAS (`Cas.poner`), nunca al journal — el
     journal solo transporta referencias (invariante 4).
  4. La salida de proceso (stdout simulado) se emite por el canal EFÍMERO (`EventBus.emit`),
     anclada a un evento durable ya publicado, y se cierra con su propio `CasRef` — ese `CasRef`
     coincide EXACTAMENTE con el que produce `Cas.poner()` sobre los mismos bytes: es la misma
     función de hash en dos módulos distintos, la prueba concreta de que `CasRef` es un tipo
     unificado, no dos ideas parecidas.
  5. Se gasta presupuesto (`Budget`/`BudgetScope`/`Hold`) — simulado LOCALMENTE en este script,
     porque `BudgetAuthority` es solo un `Protocol` en `contracts.py` todavía (ver README,
     sección "qué no hace todavía").
  6. `append_if` con una guardia que se cumple (la proyección está al día) y otra que no
     (`state_hash` no coincide) — la escritura rechazada no deja huella: el `head` del stream no
     avanza.
  7. Se simula la muerte del proceso a mitad de sesión (`journal.close()`) y la reapertura del
     MISMO archivo SQLite. El estado se reconstruye ejecutando `reduce()` de nuevo sobre la
     MISMA secuencia de comandos, desde `KernelState()` vacío — la propiedad que hace esto
     interesante no es que el script se acuerde de los comandos (un host real los recuperaría de
     su propio WAL de comandos o los volvería a pedir; eso es de otro bloque), sino que
     `reduce()` es puro y total: mismos comandos, mismo estado, byte a byte — y ese estado
     reconstruido se contrasta con los eventos que de verdad quedaron en el journal reabierto.
  8. Se verifica la cadena de hashes de punta a punta (`verify_chain`).
  9. Se redacta un blob (`journal.redact`) y se comprueba que la cadena SIGUE verificando
     exactamente igual — la redacción destruye contenido, nunca reescribe historia.

Todo el estado vive en un directorio temporal que se borra solo al terminar: correr este script
dos veces no deja rastro ni colisiona consigo mismo.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from edecan_forge_kernel import (
    Actor,
    AppendResult,
    Budget,
    BudgetScope,
    Cas,
    Command,
    EffectClass,
    EventBus,
    EventDraft,
    Guard,
    Hold,
    KernelState,
    SqliteJournal,
    Stamp,
    StreamFrame,
    reduce,
)

# --------------------------------------------------------------------------------------- #
# Utilidades del demo — nada de esto es API del paquete, es orquestación de host de juguete.
# --------------------------------------------------------------------------------------- #

ID_SEED = b"demo-seed-000000"[:16]
"""16 bytes fijos — el `Stamp` exige exactamente 16. Fijo a propósito: hace que
`SessionRecord.created_at_us` (que sí propaga `stamp.ts_physical`, a diferencia de `Event.id`,
que el journal deriva de SU PROPIA semilla interna — ver `journal.py`, decisión de diseño en su
docstring) sea idéntico entre la ejecución "en vivo" y la reconstrucción por replay del paso 7."""


class _RelojDemo:
    """`ts_physical` monótono de juguete — el host real lee `time.time_ns()`; aquí se fija a
    mano para que dos ejecuciones de este script con la misma secuencia de comandos produzcan
    exactamente los mismos `Stamp`, y por tanto exactamente el mismo `KernelState` reconstruido
    en el paso 7 (reduce es puro: mismo `state`+`cmd`, incluido su `Stamp`, misma `Decision`)."""

    def __init__(self, inicio_us: int) -> None:
        self._us = inicio_us

    def siguiente(self) -> int:
        self._us += 1_000  # +1 ms por comando; solo tiene que ser monótono, no realista.
        return self._us


def _imprimir_titulo(texto: str) -> None:
    print()
    print("=" * 88)
    print(texto)
    print("=" * 88)


def _imprimir_paso(texto: str) -> None:
    print(f"\n--- {texto} ---")


class SesionDemo:
    """Agrupa el estado mutable del script (contador de comandos, lista de comandos ya
    ejecutados para el replay del paso 7, contadores del resumen final) para no pasear media
    docena de variables sueltas por cada función. No es parte de la API pública del paquete."""

    def __init__(self, reloj: _RelojDemo, actor_agente: Actor, actor_kernel: Actor) -> None:
        self.reloj = reloj
        self.actor_agente = actor_agente
        self.actor_kernel = actor_kernel
        self.comandos_ejecutados: list[Command] = []
        self.eventos_journalizados = 0
        self.bytes_efimeros_no_journalizados = 0
        self.blobs_en_cas = 0
        self.blobs_en_journal = 0

    def stamp(self, *, lease_epoch: int = 1) -> Stamp:
        return Stamp(
            ts_physical=self.reloj.siguiente(),
            id_seed=ID_SEED,
            lease_epoch=lease_epoch,
            observed_lamport=0,
        )

    def comando(
        self,
        kind: str,
        args: dict[str, Any],
        *,
        stream_id: str,
        actor: Actor | None = None,
        corr: str,
        causation: str | None = None,
    ) -> Command:
        cmd = Command(
            kind=kind,
            stream_id=stream_id,
            actor=actor or self.actor_agente,
            stamp=self.stamp(),
            correlation_id=corr,
            causation_id=causation,
        )
        cmd = cmd.model_copy(update={"args": args})
        self.comandos_ejecutados.append(cmd)
        return cmd

    def aplicar(self, state: KernelState, cmd: Command) -> tuple[KernelState, tuple]:
        """`reduce()` + verificación de que nunca nos rechazó algo que el guion asume que
        siempre se acepta — si esto lanza, es que el demo dejó de reflejar la máquina de
        estados real, no un fallo del kernel."""
        decision = reduce(state, cmd)
        if decision.rejection is not None:
            raise AssertionError(
                f"comando {cmd.kind!r} rechazado inesperadamente: "
                f"{decision.rejection.code} — {decision.rejection.message}"
            )
        return decision.state, decision.events


async def _publicar(
    bus: EventBus, journal: SqliteJournal, drafts: tuple, *, stream_id: str, lease_epoch: int = 1
) -> AppendResult:
    """Único camino durable de este script — todo append pasa por `EventBus.publish`, nunca por
    `journal.append` directamente (salvo en el paso 6, donde `append_if` con guardia SÍ se llama
    directo porque `EventBus` no expone esa guardia — es del `Journal`, no del bus)."""
    if not drafts:
        return AppendResult(
            accepted=True, from_seq=journal.head(stream_id), to_seq=journal.head(stream_id)
        )
    resultado = await bus.publish(
        list(drafts),
        stream_id=stream_id,
        expected_seq=journal.head(stream_id),
        lease_epoch=lease_epoch,
    )
    if not resultado.accepted:
        raise AssertionError(f"publish rechazado inesperadamente: {resultado.reason}")
    return resultado


class _AdaptadorJournalAsync:
    """`SqliteJournal` es síncrono a propósito (decisión #5 de su docstring: stdlib `sqlite3`,
    sin dependencias nuevas para fingir async sobre I/O local). El `Journal` Protocol de
    `contracts.py` que `EventBus` espera SÍ declara `async def`. Este adaptador de 6 líneas es
    responsabilidad del HOST que conecta ambos — exactamente lo que el docstring de
    `journal.py` (desviación #5) dice que falta y que "este paquete no provee". Vive aquí, en el
    demo, no en el paquete: es orquestación de proceso, no un tipo de dominio."""

    def __init__(self, journal: SqliteJournal) -> None:
        self._journal = journal

    async def append(self, drafts, *, stream_id, expected_seq, lease_epoch):
        return await asyncio.to_thread(
            self._journal.append,
            drafts,
            stream_id=stream_id,
            expected_seq=expected_seq,
            lease_epoch=lease_epoch,
        )

    async def append_if(self, drafts, *, stream_id, expected_seq, lease_epoch, guard):
        return await asyncio.to_thread(
            self._journal.append_if,
            drafts,
            stream_id=stream_id,
            expected_seq=expected_seq,
            lease_epoch=lease_epoch,
            guard=guard,
        )


# --------------------------------------------------------------------------------------- #
# El demo en sí
# --------------------------------------------------------------------------------------- #


async def main() -> None:
    _imprimir_titulo("FORGE KERNEL — sesión de juguete de punta a punta (sin red, sin modelo)")

    with tempfile.TemporaryDirectory(prefix="forge-kernel-demo-") as tmp:
        raiz = Path(tmp)
        ruta_journal = raiz / "session.sqlite3"
        raiz_cas = raiz / "cas"
        raiz_workspace = raiz / "workspace"
        raiz_workspace.mkdir()
        (raiz_workspace / "README.txt").write_text(
            "workspace de juguete — forge-kernel fase 1 no implementa el aislamiento CoW real "
            "(ExecWindow es solo un Protocol, ver README §Qué NO hace todavía); esto es un "
            "directorio cualquiera para que el demo tenga algo que llamar 'workspace'.\n"
        )

        cas = Cas(raiz_cas)
        journal = SqliteJournal(ruta_journal)
        bus = EventBus(_AdaptadorJournalAsync(journal), session_id="demo-session-1")

        reloj = _RelojDemo(inicio_us=1_800_000_000_000_000)
        actor_agente = Actor(kind="agent", id="agent-1", capability_id="cap-shell")
        actor_kernel = Actor(kind="kernel", id="kernel", capability_id=None)
        demo = SesionDemo(reloj, actor_agente, actor_kernel)

        state = KernelState()
        STREAM = "agent-1"

        # ----------------------------------------------------------------------------- #
        # 1. Abrir sesión + workspace
        # ----------------------------------------------------------------------------- #
        _imprimir_paso("1. Abrir sesión y workspace")
        cmd = demo.comando(
            "session.create", {"session_id": "demo-session-1"}, stream_id=STREAM, corr="corr-1"
        )
        state, eventos = demo.aplicar(state, cmd)
        r = await _publicar(bus, journal, eventos, stream_id=STREAM)
        demo.eventos_journalizados += len(eventos)
        print(
            f"session.create -> journal seq {r.from_seq}..{r.to_seq}; workspace en {raiz_workspace}"
        )

        # ----------------------------------------------------------------------------- #
        # 2. Ciclo de vida completo de una tool call: request -> admitted (con subestados)
        #    -> started -> completed
        # ----------------------------------------------------------------------------- #
        _imprimir_paso("2. Un agente pide una herramienta y se admite/despacha/ejecuta")
        CALL_1 = "call-1"
        cmd = demo.comando(
            "tool.call_request",
            {
                "call_id": CALL_1,
                "tool_id": "fs.read_big_report",
                "effect_class": EffectClass.REVERSIBLE,
            },
            stream_id=STREAM,
            corr="corr-2",
        )
        state, eventos = demo.aplicar(state, cmd)
        await _publicar(bus, journal, eventos, stream_id=STREAM)
        demo.eventos_journalizados += len(eventos)
        print(f"tool.call_request({CALL_1}) -> {state.tool_calls[CALL_1].state.value}")

        cmd = demo.comando("tool.call_admit", {"call_id": CALL_1}, stream_id=STREAM, corr="corr-2")
        state, eventos = demo.aplicar(state, cmd)
        await _publicar(bus, journal, eventos, stream_id=STREAM)
        demo.eventos_journalizados += len(eventos)
        print(
            f"tool.call_admit({CALL_1})   -> {state.tool_calls[CALL_1].state.value}"
            f"/{state.tool_calls[CALL_1].admission.value}"
        )

        for destino in ("authorized", "queued", "dispatched"):
            cmd = demo.comando(
                "tool.call_advance_admission",
                {"call_id": CALL_1, "to": destino},
                stream_id=STREAM,
                corr="corr-2",
            )
            state, eventos = demo.aplicar(state, cmd)
            # Subestados dentro de 'admitted' no emiten evento (§1.6): no hay nada que publicar.
            assert eventos == ()
            print(f"  subestado -> {destino} (sin evento — §1.6, presupuesto p99<5ms)")

        cmd = demo.comando("tool.call_start", {"call_id": CALL_1}, stream_id=STREAM, corr="corr-2")
        state, eventos = demo.aplicar(state, cmd)
        await _publicar(bus, journal, eventos, stream_id=STREAM)
        demo.eventos_journalizados += len(eventos)
        evento_started = journal.leer(STREAM, desde_seq=journal.head(STREAM) - 1, limite=1)[0]
        print(
            f"tool.call_start({CALL_1})  -> {state.tool_calls[CALL_1].state.value}; "
            f"event.id={evento_started.id}"
        )

        # ----------------------------------------------------------------------------- #
        # 3. El resultado "grande" de la herramienta va al CAS, nunca al journal
        # ----------------------------------------------------------------------------- #
        _imprimir_paso("3. El resultado grande de la herramienta va al CAS (no al journal)")
        contenido_reporte = "\n".join(
            f"línea {i}: resultado del análisis de la base de datos, fila {i}" for i in range(4000)
        ).encode("utf-8")
        ref_reporte = cas.poner(contenido_reporte, tipo_contenido="text/plain")
        demo.blobs_en_cas += 1
        meta = cas.metadatos(ref_reporte)
        print(f"cas.poner(reporte de {len(contenido_reporte)} B) -> {ref_reporte}")
        print(f"cas.metadatos -> size_bytes={meta.size_bytes} content_type={meta.content_type!r}")
        ref_reporte_2 = cas.poner(contenido_reporte, tipo_contenido="text/plain")
        assert ref_reporte_2 == ref_reporte, "el mismo contenido debe producir el mismo CasRef"
        print(
            "cas.poner() del mismo contenido otra vez -> mismo CasRef, deduplicado (0 bytes nuevos)"
        )
        assert cas.obtener(ref_reporte) == contenido_reporte

        # ----------------------------------------------------------------------------- #
        # 4. Salida efímera de proceso — canal `emit`, JAMÁS toca el journal
        # ----------------------------------------------------------------------------- #
        _imprimir_paso("4. Salida efímera de stdout, anclada y cerrada con su CasRef")
        bus.open_ephemeral_stream(evento_started.id, "proc.stdout")
        lineas_stdout = [
            f"[fs.read_big_report] procesando bloque {i}/20...\n".encode() for i in range(20)
        ]
        acumulado = bytearray()
        for i, chunk in enumerate(lineas_stdout):
            bus.emit(
                StreamFrame(anchor=evento_started.id, channel="proc.stdout", ordinal=i, bytes=chunk)
            )
            acumulado.extend(chunk)
        sello = bus.close_ephemeral_stream(evento_started.id, "proc.stdout")
        print(
            f"stream efímero cerrado: {sello.frames_total} frames, {sello.bytes_total} B, "
            f"{sello.lines_total} líneas -> content_ref={sello.content_ref}"
        )
        # Ninguno de esos frames tocó `events` — la prueba concreta: 0 bytes journalizados por
        # ellos, y el CasRef del sello coincide EXACTO con el que produce Cas.poner() sobre los
        # mismos bytes: misma función de hash, dos módulos distintos (bus.py y cas.py).
        ref_stdout_en_cas = cas.poner(bytes(acumulado), tipo_contenido="text/plain")
        demo.blobs_en_cas += 1
        assert ref_stdout_en_cas == sello.content_ref, (
            "el CasRef del sello del bus debe coincidir con el que produce Cas.poner() sobre "
            "los mismos bytes — misma función hash, dos módulos"
        )
        print(
            f"cas.poner() de los mismos bytes -> {ref_stdout_en_cas} "
            "(idéntico al content_ref del sello)"
        )
        demo.bytes_efimeros_no_journalizados += sello.bytes_total

        cmd = demo.comando(
            "tool.call_complete", {"call_id": CALL_1, "score": 0}, stream_id=STREAM, corr="corr-2"
        )
        state, eventos = demo.aplicar(state, cmd)
        await _publicar(bus, journal, eventos, stream_id=STREAM)
        demo.eventos_journalizados += len(eventos)
        print(f"tool.call_complete({CALL_1}) -> {state.tool_calls[CALL_1].state.value} (terminal)")

        # ----------------------------------------------------------------------------- #
        # 5. Presupuesto — tipos compuestos y simulados LOCALMENTE (no hay BudgetAuthority
        #    real todavía; ver README/pendiente)
        # ----------------------------------------------------------------------------- #
        _imprimir_paso(
            "5. Se gasta presupuesto (simulado localmente — no hay BudgetAuthority real)"
        )
        scope = BudgetScope(kind="invocation", id=CALL_1)
        reservado = Budget(
            usd_micros=50_000, tokens=2_000, wall_ms=30_000, cpu_ms=5_000, bytes_out=0
        )
        hold = Hold(hold_id="hold-1", scope=scope, amount=reservado)
        gastado = Budget(
            usd_micros=31_200,
            tokens=1_180,
            wall_ms=4_200,
            cpu_ms=900,
            bytes_out=len(contenido_reporte),
        )
        print(
            f"hold({hold.hold_id}, {hold.scope.kind}:{hold.scope.id}) reserva "
            f"{hold.amount.usd_micros} usd_micros / {hold.amount.tokens} tokens"
        )
        print(
            f"settle -> gastado real {gastado.usd_micros} usd_micros / {gastado.tokens} tokens / "
            f"{gastado.bytes_out} bytes_out; liberado al padre: "
            f"{reservado.usd_micros - gastado.usd_micros} usd_micros"
        )

        # ----------------------------------------------------------------------------- #
        # 6. append_if — una guardia que se cumple, otra que no
        # ----------------------------------------------------------------------------- #
        _imprimir_paso("6. append_if: una guardia que se cumple y otra que no")
        head_antes_de_guardias = journal.head(STREAM)
        journal.set_projection(
            "budget_ledger",
            "agent-1",
            last_applied_seq=head_antes_de_guardias,
            state_hash="h-budget-v1",
        )
        CALL_2 = "call-2"
        cmd = demo.comando(
            "tool.call_request",
            {"call_id": CALL_2, "tool_id": "net.fetch_status", "effect_class": EffectClass.SAFE},
            stream_id=STREAM,
            corr="corr-3",
        )
        state, eventos = demo.aplicar(state, cmd)
        guardia_ok = Guard(
            projection_name="budget_ledger", key="agent-1", expected_state_hash="h-budget-v1"
        )
        resultado_ok = journal.append_if(
            list(eventos),
            stream_id=STREAM,
            expected_seq=head_antes_de_guardias,
            lease_epoch=1,
            guard=guardia_ok,
        )
        demo.eventos_journalizados += len(eventos)
        print(
            f"append_if con guardia CUMPLIDA -> accepted={resultado_ok.accepted} "
            f"(seq {resultado_ok.from_seq}..{resultado_ok.to_seq})"
        )
        assert resultado_ok.accepted is True

        head_tras_guardia_ok = journal.head(STREAM)
        cmd = demo.comando("tool.call_admit", {"call_id": CALL_2}, stream_id=STREAM, corr="corr-3")
        # OJO: este comando SÍ se ejecuta contra `state` (para dejar el demo en un estado
        # coherente para el paso 7), pero su escritura durable se rechaza a propósito con una
        # guardia que NO coincide — es la mitad del punto 6 que demuestra que un rechazo no dejó
        # huella: el `state` en memoria avanzó (como avanzaría cualquier decisión local antes de
        # confirmarse durable), pero el journal NO.
        state_tentativo, eventos_tentativos = demo.aplicar(state, cmd)
        guardia_mal = Guard(
            projection_name="budget_ledger", key="agent-1", expected_state_hash="h-budget-STALE"
        )
        resultado_mal = journal.append_if(
            list(eventos_tentativos),
            stream_id=STREAM,
            expected_seq=head_tras_guardia_ok,
            lease_epoch=1,
            guard=guardia_mal,
        )
        print(
            f"append_if con guardia ROTA    -> accepted={resultado_mal.accepted} "
            f"reason={resultado_mal.reason!r}"
        )
        assert resultado_mal.accepted is False
        assert journal.head(STREAM) == head_tras_guardia_ok, (
            "un append_if rechazado no debe mover la cabeza"
        )
        # El comando que sí queríamos que "contara" para el estado en memoria del demo no llegó
        # a ser durable — se descarta la rama tentativa y el demo sigue con `state` (sin el
        # admit de CALL_2), que es justo lo que el journal reabierto en el paso 7 confirmará.
        demo.comandos_ejecutados.pop()  # retira el tool.call_admit(call-2) que nunca se journalizó

        # ----------------------------------------------------------------------------- #
        # 7. "Muere" el proceso a mitad de sesión — se reabre el MISMO archivo y se reconstruye
        # ----------------------------------------------------------------------------- #
        _imprimir_paso("7. Se mata el proceso (simulado) y se reanuda desde el journal")
        estado_vivo_antes_de_morir = state
        journal.close()
        print(f"journal.close() — proceso 'muerto'. Archivo en disco: {ruta_journal}")

        journal2 = SqliteJournal(ruta_journal)
        print("SqliteJournal reabierto sobre el MISMO archivo.")

        # Reconstrucción: reduce() es puro y total — reejecutar la MISMA secuencia de comandos
        # desde KernelState() vacío reproduce, byte a byte, el mismo estado. La lista de
        # comandos, en un host real, vendría de su propio WAL de comandos o de re-pedirlos al
        # agente (fuera del alcance de este paquete); aquí vive en memoria del script porque
        # este demo es sobre journal+CAS+bus+reduce puro, no sobre un WAL de comandos.
        estado_reconstruido = KernelState()
        for cmd_previo in demo.comandos_ejecutados:
            decision = reduce(estado_reconstruido, cmd_previo)
            assert decision.rejection is None, (
                f"replay rechazó {cmd_previo.kind}: {decision.rejection}"
            )
            estado_reconstruido = decision.state

        dump_vivo = estado_vivo_antes_de_morir.model_dump(mode="json")
        dump_reconstruido = estado_reconstruido.model_dump(mode="json")
        assert dump_vivo == dump_reconstruido, (
            "el estado reconstruido debe ser idéntico al que había en vivo"
        )
        print(
            f"estado reconstruido por replay de {len(demo.comandos_ejecutados)} comandos "
            f"== estado en vivo: {dump_vivo == dump_reconstruido}"
        )
        print(f"  sesiones: {list(estado_reconstruido.sessions)}")
        print(
            "  tool_calls: "
            + ", ".join(
                f"{cid}={rec.state.value}" for cid, rec in estado_reconstruido.tool_calls.items()
            )
        )

        # Contraste contra lo que de verdad quedó en el journal reabierto (no solo contra la
        # memoria del script): el número de eventos leídos del archivo debe coincidir con lo que
        # este script cree haber publicado con éxito (el `tool.call_admit(call-2)` rechazado en
        # el paso 6 NO debe aparecer aquí).
        eventos_en_disco = journal2.leer(STREAM, limite=1000)
        assert len(eventos_en_disco) == demo.eventos_journalizados
        tipos_en_disco = [e.type for e in eventos_en_disco]
        assert "tool.call_admitted" in tipos_en_disco  # el de CALL_1, sí se journalizó
        print(
            f"journal reabierto: {len(eventos_en_disco)} eventos en '{STREAM}', "
            f"exactamente los {demo.eventos_journalizados} que este script publicó con éxito"
        )

        # ----------------------------------------------------------------------------- #
        # 8. Verificación de la cadena de hashes de punta a punta
        # ----------------------------------------------------------------------------- #
        _imprimir_paso("8. Verificación de la cadena de hashes")
        cadena_ok = journal2.verify_chain(STREAM)
        print(f"verify_chain('{STREAM}') -> {cadena_ok}")
        assert cadena_ok is True

        # ----------------------------------------------------------------------------- #
        # 9. Redacción — el blob se destruye, la cadena sigue verificando igual
        # ----------------------------------------------------------------------------- #
        _imprimir_paso("9. Redacción de un blob citado por un evento — la cadena sigue verificando")
        STREAM_PII = "demo-pii"
        contenido_sensible = b"nombre completo, direccion y telefono capturados durante la sesion"
        ref_pii = journal2.put_blob(contenido_sensible)
        demo.blobs_en_journal += 1
        print(f"journal2.put_blob(dato sensible) -> {ref_pii}")

        draft_pii = EventDraft(
            stream_id=STREAM_PII,
            v=1,
            type="session.created",
            cls="fact",
            actor=demo.actor_agente,
            correlation_id="corr-pii",
            causation_id=None,
            payload_inline=None,
            payload_ref=ref_pii,
            durability="strict",
        )
        resultado_pii = journal2.append(
            [draft_pii], stream_id=STREAM_PII, expected_seq=0, lease_epoch=1
        )
        assert resultado_pii.accepted is True
        demo.eventos_journalizados += 1
        hash_antes_de_redactar = journal2.leer(STREAM_PII)[0].hash

        journal2.redact(ref_pii, reason="solicitud_gdpr", actor_id="human:usuario-demo")
        print(f"journal2.redact({ref_pii}, reason='solicitud_gdpr') — blob destruido")

        from edecan_forge_kernel.journal import BlobRedactedError

        try:
            journal2.get_blob(ref_pii)
        except BlobRedactedError:
            print("journal2.get_blob(ref) tras redact -> BlobRedactedError (esperado)")
        else:
            raise AssertionError("get_blob debería fallar tras redact()")

        hash_despues_de_redactar = journal2.leer(STREAM_PII)[0].hash
        assert hash_despues_de_redactar == hash_antes_de_redactar, "redact() no debe tocar `events`"
        assert journal2.leer(STREAM_PII)[0].payload_ref == ref_pii
        assert journal2.verify_chain(STREAM_PII) is True
        assert journal2.verify_chain(STREAM) is True  # el stream principal, intacto, también
        print(
            "hash del evento antes/después de redactar: idéntico — "
            f"verify_chain('{STREAM_PII}')={journal2.verify_chain(STREAM_PII)}, "
            f"verify_chain('{STREAM}')={journal2.verify_chain(STREAM)}"
        )

        # ----------------------------------------------------------------------------- #
        # Resumen final
        # ----------------------------------------------------------------------------- #
        _imprimir_titulo("RESUMEN")
        total_eventos = journal2.head(STREAM) + journal2.head(STREAM_PII)
        print(f"eventos escritos en el journal:        {total_eventos}")
        print(f"  - stream '{STREAM}':                 {journal2.head(STREAM)}")
        print(f"  - stream '{STREAM_PII}':              {journal2.head(STREAM_PII)}")
        print(f"blobs en Cas (filesystem, cas.py):     {demo.blobs_en_cas}")
        print(f"blobs en el CAS interno del journal:   {demo.blobs_en_journal} (1 redactado)")
        print(
            f"bytes efímeros NO journalizados:       "
            f"{demo.bytes_efimeros_no_journalizados} B (stdout, vía emit())"
        )
        print(f"verify_chain('{STREAM}'):              {journal2.verify_chain(STREAM)}")
        print(f"verify_chain('{STREAM_PII}'):           {journal2.verify_chain(STREAM_PII)}")
        print(f"estado reconstruido == estado en vivo: {dump_vivo == dump_reconstruido}")
        print(
            f"append_if guardia cumplida / rota:     "
            f"{resultado_ok.accepted} / {resultado_mal.accepted}"
        )

        journal2.close()
        cas.close()
        print(f"\n(directorio temporal {raiz} se borra al salir del `with` — nada queda en disco)")


if __name__ == "__main__":
    asyncio.run(main())
