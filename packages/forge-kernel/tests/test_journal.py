from __future__ import annotations

from pathlib import Path

import pytest
from edecan_forge_kernel.contracts import (
    Actor,
    CasRef,
    EventDraft,
    Guard,
    PayloadInlineError,
    SessionCreatedPayload,
    emit,
)
from edecan_forge_kernel.journal import (
    GENESIS_HASH,
    ChainVerificationError,
    FencedOut,
    SqliteJournal,
)


@pytest.fixture
def journal(tmp_path: Path) -> SqliteJournal:
    j = SqliteJournal(tmp_path / "sesion.sqlite3")
    yield j
    j.close()


def _draft_session_created(actor: Actor, *, stream_id: str, session_id: str) -> EventDraft:
    return emit(
        "session.created",
        actor=actor,
        stream_id=stream_id,
        correlation_id="corr-1",
        causation_id=None,
        payload=SessionCreatedPayload(session_id=session_id),
    )


# --------------------------------------------------------------------------------------- #
# Contigüidad de seq por stream
# --------------------------------------------------------------------------------------- #


def test_seq_contiguo_desde_1_por_stream(journal: SqliteJournal, actor_agente: Actor) -> None:
    d1 = _draft_session_created(actor_agente, stream_id="s1", session_id="s1")
    d2 = _draft_session_created(actor_agente, stream_id="s1", session_id="s1")
    r1 = journal.append([d1], stream_id="s1", expected_seq=0, lease_epoch=1)
    assert r1.accepted is True
    assert (r1.from_seq, r1.to_seq) == (1, 1)
    r2 = journal.append([d2], stream_id="s1", expected_seq=1, lease_epoch=1)
    assert (r2.from_seq, r2.to_seq) == (2, 2)
    assert journal.head("s1") == 2

    eventos = journal.leer("s1")
    assert [e.seq for e in eventos] == [1, 2]


def test_seq_es_independiente_entre_streams(journal: SqliteJournal, actor_agente: Actor) -> None:
    da = _draft_session_created(actor_agente, stream_id="a", session_id="sa")
    db = _draft_session_created(actor_agente, stream_id="b", session_id="sb")
    journal.append([da], stream_id="a", expected_seq=0, lease_epoch=1)
    journal.append([db], stream_id="b", expected_seq=0, lease_epoch=1)
    assert journal.head("a") == 1
    assert journal.head("b") == 1


def test_append_de_lote_asigna_seq_consecutivos(
    journal: SqliteJournal, actor_agente: Actor
) -> None:
    drafts = [
        _draft_session_created(actor_agente, stream_id="s1", session_id=f"s{i}") for i in range(3)
    ]
    resultado = journal.append(drafts, stream_id="s1", expected_seq=0, lease_epoch=1)
    assert (resultado.from_seq, resultado.to_seq) == (1, 3)
    assert [e.seq for e in journal.leer("s1")] == [1, 2, 3]


# --------------------------------------------------------------------------------------- #
# CAS optimista bajo conflicto
# --------------------------------------------------------------------------------------- #


def test_append_rechaza_expected_seq_desactualizado(
    journal: SqliteJournal, actor_agente: Actor
) -> None:
    d1 = _draft_session_created(actor_agente, stream_id="s1", session_id="s1")
    journal.append([d1], stream_id="s1", expected_seq=0, lease_epoch=1)

    d2 = _draft_session_created(actor_agente, stream_id="s1", session_id="s1")
    resultado = journal.append(
        [d2], stream_id="s1", expected_seq=0, lease_epoch=1
    )  # ya no es 0, es 1
    assert resultado.accepted is False
    assert resultado.reason is not None
    assert "seq_conflict" in resultado.reason
    # el conflicto no debe haber escrito nada
    assert journal.head("s1") == 1


# --------------------------------------------------------------------------------------- #
# `append_if` rechaza cuando la guardia no se cumple
# --------------------------------------------------------------------------------------- #


def test_append_if_rechaza_sin_proyeccion_registrada(
    journal: SqliteJournal, actor_agente: Actor
) -> None:
    d1 = _draft_session_created(actor_agente, stream_id="s1", session_id="s1")
    guard = Guard(
        projection_name="efectos", key="deploy:prod:api", expected_state_hash="b2b:" + "0" * 64
    )
    resultado = journal.append_if([d1], stream_id="s1", expected_seq=0, lease_epoch=1, guard=guard)
    assert resultado.accepted is False
    assert "guard_failed" in (resultado.reason or "")
    assert journal.head("s1") == 0


def test_append_if_rechaza_proyeccion_desactualizada(
    journal: SqliteJournal, actor_agente: Actor
) -> None:
    # la cabeza real de "s1" es 0, pero la proyección dice haber aplicado hasta 5
    journal.set_projection("efectos", "deploy:prod:api", last_applied_seq=5, state_hash="h1")
    guard = Guard(projection_name="efectos", key="deploy:prod:api", expected_state_hash="h1")
    d1 = _draft_session_created(actor_agente, stream_id="s1", session_id="s1")
    resultado = journal.append_if([d1], stream_id="s1", expected_seq=0, lease_epoch=1, guard=guard)
    assert resultado.accepted is False
    assert "desactualizada" in (resultado.reason or "")


def test_append_if_rechaza_state_hash_no_coincide(
    journal: SqliteJournal, actor_agente: Actor
) -> None:
    journal.set_projection("efectos", "deploy:prod:api", last_applied_seq=0, state_hash="h_real")
    guard = Guard(
        projection_name="efectos", key="deploy:prod:api", expected_state_hash="h_esperado"
    )
    d1 = _draft_session_created(actor_agente, stream_id="s1", session_id="s1")
    resultado = journal.append_if([d1], stream_id="s1", expected_seq=0, lease_epoch=1, guard=guard)
    assert resultado.accepted is False


def test_append_if_acepta_cuando_la_guardia_se_cumple(
    journal: SqliteJournal, actor_agente: Actor
) -> None:
    journal.set_projection("efectos", "deploy:prod:api", last_applied_seq=0, state_hash="h1")
    guard = Guard(projection_name="efectos", key="deploy:prod:api", expected_state_hash="h1")
    d1 = _draft_session_created(actor_agente, stream_id="s1", session_id="s1")
    resultado = journal.append_if([d1], stream_id="s1", expected_seq=0, lease_epoch=1, guard=guard)
    assert resultado.accepted is True
    assert journal.head("s1") == 1


# --------------------------------------------------------------------------------------- #
# `FencedOut` con epoch viejo
# --------------------------------------------------------------------------------------- #


def test_epoch_obsoleto_levanta_fenced_out(journal: SqliteJournal, actor_agente: Actor) -> None:
    d1 = _draft_session_created(actor_agente, stream_id="s1", session_id="s1")
    journal.append([d1], stream_id="s1", expected_seq=0, lease_epoch=5)

    d2 = _draft_session_created(actor_agente, stream_id="s1", session_id="s1")
    with pytest.raises(FencedOut):
        journal.append([d2], stream_id="s1", expected_seq=1, lease_epoch=2)

    # el intento fenceado no debió escribir nada
    assert journal.head("s1") == 1


def test_epoch_igual_o_mayor_no_es_fenceado(journal: SqliteJournal, actor_agente: Actor) -> None:
    d1 = _draft_session_created(actor_agente, stream_id="s1", session_id="s1")
    journal.append([d1], stream_id="s1", expected_seq=0, lease_epoch=5)
    d2 = _draft_session_created(actor_agente, stream_id="s1", session_id="s1")
    resultado = journal.append([d2], stream_id="s1", expected_seq=1, lease_epoch=5)
    assert resultado.accepted is True
    d3 = _draft_session_created(actor_agente, stream_id="s1", session_id="s1")
    resultado2 = journal.append([d3], stream_id="s1", expected_seq=2, lease_epoch=9)
    assert resultado2.accepted is True


# --------------------------------------------------------------------------------------- #
# Verificación de la cadena completa
# --------------------------------------------------------------------------------------- #


def test_verify_chain_de_stream_vacio_es_true(journal: SqliteJournal) -> None:
    assert journal.verify_chain("nunca-escrito") is True


def test_verify_chain_valida_tras_varios_appends(
    journal: SqliteJournal, actor_agente: Actor
) -> None:
    for i in range(5):
        d = _draft_session_created(actor_agente, stream_id="s1", session_id=f"s{i}")
        journal.append([d], stream_id="s1", expected_seq=i, lease_epoch=1)
    assert journal.verify_chain("s1") is True

    eventos = journal.leer("s1")
    assert eventos[0].prev_hash == GENESIS_HASH
    for anterior, siguiente in zip(eventos, eventos[1:], strict=False):
        assert siguiente.prev_hash == anterior.hash


# --------------------------------------------------------------------------------------- #
# Detección de manipulación de un evento intermedio
# --------------------------------------------------------------------------------------- #


def test_verify_chain_detecta_manipulacion(journal: SqliteJournal, actor_agente: Actor) -> None:
    for i in range(3):
        d = _draft_session_created(actor_agente, stream_id="s1", session_id=f"s{i}")
        journal.append([d], stream_id="s1", expected_seq=i, lease_epoch=1)
    assert journal.verify_chain("s1") is True

    # manipulación directa por debajo del API público, simulando un ataque al archivo
    conn = journal._conn  # noqa: SLF001 - acceso deliberado para el test de manipulación
    conn.execute(
        "UPDATE events SET correlation_id = 'manipulado' WHERE stream_id = 's1' AND seq = 2"
    )

    with pytest.raises(ChainVerificationError) as excinfo:
        journal.verify_chain("s1")
    assert excinfo.value.seq == 2


def test_verify_chain_detecta_prev_hash_roto(journal: SqliteJournal, actor_agente: Actor) -> None:
    for i in range(3):
        d = _draft_session_created(actor_agente, stream_id="s1", session_id=f"s{i}")
        journal.append([d], stream_id="s1", expected_seq=i, lease_epoch=1)

    conn = journal._conn  # noqa: SLF001
    conn.execute(
        "UPDATE events SET prev_hash = 'b2b:' || substr(hex(randomblob(32)), 1, 64) "
        "WHERE stream_id = 's1' AND seq = 3"
    )

    with pytest.raises(ChainVerificationError):
        journal.verify_chain("s1")


# --------------------------------------------------------------------------------------- #
# Rechazo de payload con texto libre
# --------------------------------------------------------------------------------------- #


def test_event_draft_rechaza_payload_con_texto_libre(actor_agente: Actor) -> None:
    # `EventDraft` valida vía `@model_validator`, así que pydantic envuelve el
    # `PayloadInlineError` original en un `ValidationError` — el mensaje original sigue ahí.
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="texto libre"):
        EventDraft(
            stream_id="s1",
            v=1,
            type="session.created",
            cls="fact",
            actor=actor_agente,
            correlation_id="corr-1",
            causation_id=None,
            payload_inline={"session_id": "hola mundo, esto es texto libre"},
            durability="strict",
        )


def test_validate_payload_inline_rechaza_texto_libre_directamente() -> None:
    """La misma regla, comprobada contra la función de validación tal cual la usa
    `Journal._construir_y_escribir` como defensa en profundidad antes de escribir."""
    with pytest.raises(PayloadInlineError, match="texto libre"):
        from edecan_forge_kernel.contracts import validate_payload_inline

        validate_payload_inline({"session_id": "hola mundo, esto es texto libre"})


# --------------------------------------------------------------------------------------- #
# Redacción que no rompe la cadena
# --------------------------------------------------------------------------------------- #


def test_redact_destruye_el_blob_pero_la_cadena_sigue_verificando(
    journal: SqliteJournal, actor_agente: Actor
) -> None:
    contenido = b"informacion personal sensible"
    ref = journal.put_blob(contenido)
    assert journal.get_blob(ref) == contenido

    draft = EventDraft(
        stream_id="s1",
        v=1,
        type="session.created",
        cls="fact",
        actor=actor_agente,
        correlation_id="corr-1",
        causation_id=None,
        payload_inline=None,
        payload_ref=ref,
        durability="strict",
    )
    resultado = journal.append([draft], stream_id="s1", expected_seq=0, lease_epoch=1)
    assert resultado.accepted is True
    assert journal.verify_chain("s1") is True

    hash_antes = journal.leer("s1")[0].hash

    journal.redact(ref, reason="solicitud_gdpr", actor_id="human:usuario-demo")

    from edecan_forge_kernel.journal import BlobRedactedError

    with pytest.raises(BlobRedactedError):
        journal.get_blob(ref)

    # el hash sobrevive y la cadena sigue verificando igual que antes de redactar
    assert journal.leer("s1")[0].hash == hash_antes
    assert journal.leer("s1")[0].payload_ref == ref
    assert journal.verify_chain("s1") is True


def test_redact_de_blob_inexistente_falla(journal: SqliteJournal) -> None:
    from edecan_forge_kernel.journal import BlobNotFoundError

    ref = CasRef.from_bytes(b"nunca-guardado")
    with pytest.raises(BlobNotFoundError):
        journal.redact(ref, reason="solicitud_gdpr", actor_id="human:usuario-demo")


# --------------------------------------------------------------------------------------- #
# `leer_todos` — cursor cruzando streams
# --------------------------------------------------------------------------------------- #


def test_leer_todos_cruza_streams_por_cursor(journal: SqliteJournal, actor_agente: Actor) -> None:
    da = _draft_session_created(actor_agente, stream_id="a", session_id="sa")
    db = _draft_session_created(actor_agente, stream_id="b", session_id="sb")
    journal.append([da], stream_id="a", expected_seq=0, lease_epoch=1)
    journal.append([db], stream_id="b", expected_seq=0, lease_epoch=1)

    primeros, cursor = journal.leer_todos(desde_cursor=0, limite=1)
    assert len(primeros) == 1
    resto, cursor2 = journal.leer_todos(desde_cursor=cursor, limite=10)
    assert len(resto) == 1
    assert {e.stream_id for e in primeros + resto} == {"a", "b"}

    vacio, cursor3 = journal.leer_todos(desde_cursor=cursor2, limite=10)
    assert vacio == []
    assert cursor3 == cursor2


def test_append_reune_todos_los_drafts_del_mismo_stream(
    journal: SqliteJournal, actor_agente: Actor
) -> None:
    otro = _draft_session_created(actor_agente, stream_id="otro-stream", session_id="x")
    with pytest.raises(ValueError, match="stream_id"):
        journal.append([otro], stream_id="s1", expected_seq=0, lease_epoch=1)
