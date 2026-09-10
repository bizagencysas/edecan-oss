"""Presencia en memoria de streams SSE por conversación (regla de producto del push).

Regla del producto: no se envía push APNs si el dueño está DENTRO del chat de
esa conversación (app abierta en ella); sí cuando está fuera de la app o en
otra pantalla. El aviso in-app de "otra parte de la app" lo entrega el propio
cliente por SSE — este módulo SOLO marca la presencia del stream del chat.

DECISIÓN DE DISEÑO — por qué el worker puede leer este registro:
la API (`uvicorn`) y el worker (loop de SQS) corren en el MISMO proceso
(`edecan_local`, ver `apps/local/edecan_local/runtime.py`). Por eso el handler
`edecan_worker.handlers.notify_important_event` importa el singleton
`presencia` de acá por nombre de módulo, con import perezoso y guardado:
si el módulo no existe (despliegue mínimo del worker sin la API), fall-open y
entrega el push igual. No hay Redis ni SQL involucrado: el registro vive y
muere con el proceso, que es exactamente su semántica (un stream SSE tampoco
sobrevive a un restart de la API).

Thread-safety: todo corre en UN solo event loop. `entrar`/`salir`/`esta_activa`
no contienen `await`, así que entre el chequeo y la mutación no puede
intercalarse otra corrutina — no hay race. Un contador por conversación (en
vez de un set de conexiones) basta: solo importa "hay alguien dentro o no".
"""

from __future__ import annotations

import logging
import time
import uuid

logger = logging.getLogger(__name__)

# F-4: si un `salir` se pierde (generador recolectado sin aclose, bug futuro),
# la entrada suprimía push para esa conversación PARA SIEMPRE (hasta el
# reinicio). Cada entrada guarda su último latido y el barrido de `entrar`
# purga las inactivas tras este tope.
_TTL_SEGUNDOS = 6 * 60 * 60  # 6h sin latido = stream muerto


class RegistroDePresencia:
    """`conversation_id -> (cantidad de streams SSE activos, último latido)`."""

    def __init__(self) -> None:
        self._conexiones: dict[uuid.UUID, list[int, float]] = {}

    def _barrer(self, ahora: float) -> None:
        muertas = [
            cid
            for cid, (_n, ts) in self._conexiones.items()
            if ahora - ts > _TTL_SEGUNDOS
        ]
        for cid in muertas:
            self._conexiones.pop(cid, None)
        if muertas:
            logger.info("presencia: barridas %d entradas sin latido", len(muertas))

    async def entrar(self, conversation_id: uuid.UUID) -> None:
        """Marca un stream SSE más para la conversación."""
        self._barrer(time.monotonic())
        n, _ts = self._conexiones.get(conversation_id, (0, 0.0))
        self._conexiones[conversation_id] = [n + 1, time.monotonic()]

    async def salir(self, conversation_id: uuid.UUID) -> None:
        """Desmarca un stream. No queda en negativo si se llama de más."""
        par = self._conexiones.get(conversation_id)
        if par is None:
            return
        if par[0] <= 1:
            self._conexiones.pop(conversation_id, None)
        else:
            par[0] -= 1
            par[1] = time.monotonic()

    def esta_activa(self, conversation_id: uuid.UUID) -> bool:
        """`True` si hay al menos un stream SSE abierto para la conversación."""
        par = self._conexiones.get(conversation_id)
        if par is None:
            return False
        if time.monotonic() - par[1] > _TTL_SEGUNDOS:
            return False
        return par[0] > 0

    def conexiones(self, conversation_id: uuid.UUID) -> int:
        """Cantidad de streams activos (0 si no hay)."""
        par = self._conexiones.get(conversation_id)
        if par is None:
            return 0
        if time.monotonic() - par[1] > _TTL_SEGUNDOS:
            return 0
        return par[0]


presencia = RegistroDePresencia()

__all__ = ["RegistroDePresencia", "presencia"]