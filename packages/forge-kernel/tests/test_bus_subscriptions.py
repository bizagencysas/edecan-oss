"""Suscripción por patrón y backpressure con histéresis — `bus.py`, §1.4 líneas 1496-1513."""

from __future__ import annotations

from edecan_forge_kernel.bus import (
    LAG_LIVE_THRESHOLD,
    MAX_CATCHUP_ATTEMPTS,
    PatternTrie,
    Subscription,
)

# --------------------------------------------------------------------------------------- #
# Suscripción por patrón
# --------------------------------------------------------------------------------------- #


def test_patron_de_un_segmento_comodin_matchea_todo_el_dominio() -> None:
    trie = PatternTrie()
    trie.add("tool.*", "sub-1")

    assert trie.match("tool.call_requested") == frozenset({"sub-1"})
    assert trie.match("tool.call_completed") == frozenset({"sub-1"})
    assert trie.match("session.created") == frozenset()


def test_patron_exacto_solo_matchea_ese_tipo() -> None:
    trie = PatternTrie()
    trie.add("tool.call_completed", "sub-exacto")
    trie.add("tool.*", "sub-comodin")

    coincidencias = trie.match("tool.call_completed")
    assert coincidencias == frozenset({"sub-exacto", "sub-comodin"})
    assert trie.match("tool.call_failed") == frozenset({"sub-comodin"})


def test_patron_de_tres_segmentos_agent_id_comodin() -> None:
    trie = PatternTrie()
    trie.add("agent.ag-1.spawned", "sub-agente-1")
    trie.add("agent.*.spawned", "sub-cualquiera")

    assert trie.match("agent.ag-1.spawned") == frozenset({"sub-agente-1", "sub-cualquiera"})
    assert trie.match("agent.ag-2.spawned") == frozenset({"sub-cualquiera"})


def test_longitudes_distintas_de_segmentos_nunca_matchean() -> None:
    trie = PatternTrie()
    trie.add("tool.*", "sub-1")
    assert trie.match("tool.call.completed.extra") == frozenset()
    assert trie.match("tool") == frozenset()


def test_unsubscribe_quita_del_trie() -> None:
    trie = PatternTrie()
    trie.add("tool.*", "sub-1")
    trie.remove("tool.*", "sub-1")
    assert trie.match("tool.call_completed") == frozenset()


# --------------------------------------------------------------------------------------- #
# Backpressure con histéresis — ver el docstring de `Subscription` en bus.py para la máquina
# de estados completa (síntesis propia sobre la prosa del documento).
# --------------------------------------------------------------------------------------- #


def test_dos_ventanas_buenas_seguidas_promueven_a_live() -> None:
    sub = Subscription("sub-1", "tool.*", mode="durable_replay")
    assert sub.record_lag_window(10) == "durable_replay"  # racha=1, aún no converge
    assert sub.record_lag_window(5) == "live_lossy"  # racha=2, converge


def test_una_ventana_mala_rompe_la_racha_sin_promover() -> None:
    sub = Subscription("sub-1", "tool.*", mode="durable_replay")
    sub.record_lag_window(10)  # racha=1
    sub.record_lag_window(LAG_LIVE_THRESHOLD)  # >=64, rompe la racha
    assert sub.mode == "durable_replay"
    # Una sola ventana buena tras la racha rota NO promueve — hace falta otra vez DOS seguidas.
    assert sub.record_lag_window(10) == "durable_replay"
    assert sub.record_lag_window(10) == "live_lossy"


def test_histeresis_no_oscila_con_lag_alrededor_del_umbral() -> None:
    """Un móvil en red mala cuyo lag oscila justo alrededor de 64 (63, 65, 63, 65, ...) nunca
    debe promoverse a `live_lossy` de rebote — cada ventana mala rompe la racha entera, así que
    dos consecutivas buenas nunca ocurren en este patrón. Y como cada ventana mala también
    cuenta como intento fallido, tres de ellas lo degradan a `digest` en vez de dejarlo girando
    para siempre en `durable_replay` — exactamente el ciclo infinito que la regla de línea 1496
    existe para evitar (línea 1726: "sin la histéresis, es un ciclo infinito de reconexión")."""
    sub = Subscription("sub-1", "tool.*", mode="durable_replay")
    lags = [63, 65, 63, 65, 63, 65]
    modos = [sub.record_lag_window(lag) for lag in lags]

    assert "live_lossy" not in modos  # nunca se promovió por una racha rota a mitad
    assert modos[-1] == "digest"  # tres ventanas malas (65 en posiciones 1,3,5) lo degradan
    assert modos.count("digest") == 1  # y lo hace UNA vez, no oscila entrando y saliendo


def test_nunca_converge_se_degrada_a_digest_tras_tres_intentos() -> None:
    sub = Subscription("sub-1", "tool.*", mode="durable_replay")
    modos = [sub.record_lag_window(10_000) for _ in range(MAX_CATCHUP_ATTEMPTS)]
    assert modos == ["durable_replay", "durable_replay", "digest"]


def test_digest_es_terminal_salvo_re_subscribe() -> None:
    sub = Subscription("sub-1", "tool.*", mode="durable_replay")
    for _ in range(MAX_CATCHUP_ATTEMPTS):
        sub.record_lag_window(10_000)
    assert sub.mode == "digest"

    sub.force_catchup()  # una desconexión externa NO saca de digest
    assert sub.mode == "digest"

    sub.record_lag_window(0)  # ni una ventana perfecta lo hace
    assert sub.mode == "digest"

    sub.re_subscribe()  # solo una reconexión explícita
    assert sub.mode == "durable_replay"
    assert sub.record_lag_window(0) == "durable_replay"
    assert sub.record_lag_window(0) == "live_lossy"


def test_force_catchup_reinicia_racha_pero_no_perdona_intentos_previos() -> None:
    sub = Subscription("sub-1", "tool.*", mode="durable_replay")
    sub.record_lag_window(10_000)  # intento 1 fallido
    sub.record_lag_window(10_000)  # intento 2 fallido
    sub.mode = "live_lossy"  # simula que convergió por otra vía y luego se desconecta
    sub.force_catchup()
    assert sub.mode == "durable_replay"
    # Un tercer fallo desde aquí agota el presupuesto heredado, no reinicia a 0.
    assert sub.record_lag_window(10_000) == "digest"


def test_live_no_se_reevalua_por_ventana_de_lag() -> None:
    sub = Subscription("sub-1", "tool.*", mode="live_lossy")
    assert sub.record_lag_window(999_999) == "live_lossy"
