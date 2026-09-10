"""Presencia SSE por conversación — registro en memoria y envoltura del stream."""

from __future__ import annotations

import uuid

import pytest

from edecan_api.presencia import RegistroDePresencia


async def _chunks(*pieces: str):
    for piece in pieces:
        yield piece


async def test_entrar_y_salir_marcan_la_presencia() -> None:
    registro = RegistroDePresencia()
    cid = uuid.uuid4()
    assert not registro.esta_activa(cid)
    assert registro.conexiones(cid) == 0

    await registro.entrar(cid)
    assert registro.esta_activa(cid)
    assert registro.conexiones(cid) == 1

    await registro.salir(cid)
    assert not registro.esta_activa(cid)
    assert registro.conexiones(cid) == 0


async def test_contador_soporta_varios_streams_simultaneos() -> None:
    registro = RegistroDePresencia()
    cid = uuid.uuid4()

    await registro.entrar(cid)
    await registro.entrar(cid)
    assert registro.conexiones(cid) == 2

    await registro.salir(cid)
    assert registro.esta_activa(cid)
    await registro.salir(cid)
    assert not registro.esta_activa(cid)


async def test_salir_sin_entrar_no_deja_negativos() -> None:
    registro = RegistroDePresencia()
    cid = uuid.uuid4()

    await registro.salir(cid)

    assert registro.conexiones(cid) == 0
    assert not registro.esta_activa(cid)


async def test_stream_del_chat_marca_presencia_mientras_se_consume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import edecan_api.routers.conversations as conversations_module

    registro = RegistroDePresencia()
    monkeypatch.setattr(conversations_module, "presencia", registro)
    cid = uuid.uuid4()
    stream = conversations_module._stream_con_presencia(_chunks("a", "b"), cid)

    assert not registro.esta_activa(cid)
    assert await anext(stream) == "a"
    assert registro.esta_activa(cid)
    assert await anext(stream) == "b"
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert not registro.esta_activa(cid)


async def test_desregistra_presencia_si_el_cliente_aborta_el_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import edecan_api.routers.conversations as conversations_module

    registro = RegistroDePresencia()
    monkeypatch.setattr(conversations_module, "presencia", registro)
    cid = uuid.uuid4()
    stream = conversations_module._stream_con_presencia(_chunks("a", "b", "c"), cid)

    await anext(stream)
    assert registro.esta_activa(cid)

    await stream.aclose()  # mismo camino que una cancelación del transporte

    assert not registro.esta_activa(cid)
    assert registro.conexiones(cid) == 0

async def test_entrada_sin_latido_expira_y_no_suprime_push_para_siempre(monkeypatch):
    """F-4: si un `salir` se pierde, la entrada NO debe suprimir push para
    siempre: tras el TTL, `esta_activa`/`conexiones` dicen inactivo y el
    barrido de `entrar` la purga."""

    from edecan_api.presencia import _TTL_SEGUNDOS, RegistroDePresencia

    registro = RegistroDePresencia()
    conversacion = uuid.uuid4()

    await registro.entrar(conversacion)
    assert registro.esta_activa(conversacion)
    assert registro.conexiones(conversacion) == 1

    # Envejezco la entrada tocando directamente su timestamp (más simple y
    # determinista que monkeypatch del reloj global).
    par = registro._conexiones[conversacion]
    par[1] = par[1] - _TTL_SEGUNDOS - 1

    # Salto el reloj más allá del TTL: activa pasa a falsa (fall-open).
    assert not registro.esta_activa(conversacion)
    assert registro.conexiones(conversacion) == 0

    # El barrido de entrar() purga la entrada muerta.
    otra = uuid.uuid4()
    await registro.entrar(otra)
    assert conversacion not in registro._conexiones
    assert registro.esta_activa(otra)
