from __future__ import annotations

from edecan_forge_kernel.contracts import CacheKey, IdempotencyKey


def test_cache_key_es_determinista_sobre_el_mismo_contenido() -> None:
    a = CacheKey.derive("fs.read_file", "digest-abc", ("ws_content:a/b.py",))
    b = CacheKey.derive("fs.read_file", "digest-abc", ("ws_content:a/b.py",))
    assert a == b


def test_cache_key_cambia_si_el_scope_resuelto_cambia() -> None:
    """El alcance por CONTENIDO, no por `workspace.head` — §4.1.1, línea 3103: la caché de
    lecturas se invalida solo cuando cambia el contenido de esa ruta, no cuando se mueve la
    cabeza del workspace por un fichero ajeno."""
    a = CacheKey.derive("fs.read_file", "digest-abc", ("ws_content:hash-1",))
    b = CacheKey.derive("fs.read_file", "digest-abc", ("ws_content:hash-2",))
    assert a != b


def test_idempotency_key_es_determinista() -> None:
    a = IdempotencyKey.derive(agent_id="ag-1", turn_seq=3, tool_call_id="call-9", args_digest="d1")
    b = IdempotencyKey.derive(agent_id="ag-1", turn_seq=3, tool_call_id="call-9", args_digest="d1")
    assert a == b


def test_idempotency_key_no_depende_del_attempt() -> None:
    """§5.3, línea 4398: reintentar la MISMA invocación debe dar la MISMA clave. No hay
    parámetro `attempt` en `IdempotencyKey.derive` a propósito — no hay forma de pasarlo."""
    llamada_1 = IdempotencyKey.derive(
        agent_id="ag-1", turn_seq=1, tool_call_id="call-1", args_digest="d"
    )
    llamada_2 = IdempotencyKey.derive(
        agent_id="ag-1", turn_seq=1, tool_call_id="call-1", args_digest="d"
    )
    assert llamada_1 == llamada_2


def test_cache_key_e_idempotency_key_son_tipos_distintos_aunque_compartan_forma() -> None:
    cache = CacheKey.derive("t", "d", ())
    idem = IdempotencyKey.derive(agent_id="a", turn_seq=0, tool_call_id="c", args_digest="d")
    assert type(cache) is not type(idem)
    # Mismos "materiales" de entrada producen claves distintas: las fórmulas de derivación
    # difieren (una es de contenido, la otra de efecto), así que no hay colisión accidental
    # entre los dos espacios de nombres.
    assert cache.value != idem.value
