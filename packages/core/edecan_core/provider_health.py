"""Provider health tracking, circuit breakers y fallback chains (§63, §132, §133).

Rastrea la salud de cada proveedor LLM (latencia, tasa de error, rate limits)
y decide cuándo abrir el circuit breaker para evitar llamar un proveedor
caído repetidamente.

Estados del circuit breaker:
- CLOSED: funcionamiento normal
- OPEN: el proveedor está caído; no se le llama
- HALF_OPEN: se permite un intento de prueba

Uso::

    health = ProviderHealth()
    health.record_success("workers-ai", latency=0.8)
    health.record_failure("workers-ai", error=LLMError("timeout"))
    if health.is_available("workers-ai"):
        ...
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock

logger = logging.getLogger(__name__)

_FAILURE_THRESHOLD = 3
_RECOVERY_SECONDS = 60.0
_SLOW_THRESHOLD_SECONDS = 30.0

ProviderHealthEventSink = Callable[[dict[str, float | str]], None]


@dataclass
class _ProviderState:
    consecutive_failures: int = 0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0
    circuit_open: bool = False
    total_calls: int = 0
    total_failures: int = 0
    total_successes: int = 0
    recent_latencies: list[float] = field(default_factory=list)
    rate_limited_until: float = 0.0


class ProviderHealth:
    """Rastrea salud de proveedores y maneja circuit breakers."""

    def __init__(
        self,
        failure_threshold: int = _FAILURE_THRESHOLD,
        recovery_seconds: float = _RECOVERY_SECONDS,
        event_sink: ProviderHealthEventSink | None = None,
    ) -> None:
        self._states: dict[str, _ProviderState] = {}
        self._lock = Lock()
        self._failure_threshold = failure_threshold
        self._recovery_seconds = recovery_seconds
        self._event_sink = event_sink
        # Historial operativo corto para explicar degradaciones recientes. No
        # guarda el texto de la excepción ni ningún request/response; la
        # persistencia entre reinicios requiere un almacén separado.
        self._events: deque[dict[str, float | str]] = deque(maxlen=200)

    def _get_state(self, provider: str) -> _ProviderState:
        if provider not in self._states:
            self._states[provider] = _ProviderState()
        return self._states[provider]

    def record_success(
        self,
        provider: str,
        latency: float = 0.0,
        *,
        model: str | None = None,
        model_alias: str | None = None,
    ) -> None:
        with self._lock:
            state = self._get_state(provider)
            state.consecutive_failures = 0
            state.circuit_open = False
            state.last_success_time = time.monotonic()
            state.total_calls += 1
            state.total_successes += 1
            self._events.append(
                event := {
                    "provider": provider,
                    "status": "success",
                    "latency": max(0.0, float(latency)),
                    "at": time.time(),
                }
            )
            if model:
                event["model"] = model
            if model_alias:
                event["model_alias"] = model_alias
            if latency > 0:
                state.recent_latencies.append(latency)
                if len(state.recent_latencies) > 50:
                    state.recent_latencies = state.recent_latencies[-50:]
        self._notify_sink(event)

    def record_failure(
        self,
        provider: str,
        error: Exception | None = None,
        *,
        model: str | None = None,
        model_alias: str | None = None,
    ) -> None:
        with self._lock:
            state = self._get_state(provider)
            state.consecutive_failures += 1
            state.last_failure_time = time.monotonic()
            state.total_calls += 1
            state.total_failures += 1
            event = {"provider": provider, "status": "failure", "latency": 0.0, "at": time.time()}
            if model:
                event["model"] = model
            if model_alias:
                event["model_alias"] = model_alias
            self._events.append(event)
            if state.consecutive_failures >= self._failure_threshold:
                state.circuit_open = True
                logger.warning(
                    "Circuit breaker OPEN for provider '%s' after %d consecutive failures",
                    provider,
                    state.consecutive_failures,
                )
        self._notify_sink(event)

    def record_rate_limit(self, provider: str, retry_after_seconds: float = 60.0) -> None:
        with self._lock:
            state = self._get_state(provider)
            state.rate_limited_until = time.monotonic() + retry_after_seconds
            event = {
                "provider": provider,
                "status": "rate_limited",
                "latency": 0.0,
                "at": time.time(),
            }
            self._events.append(event)
            logger.info("Provider '%s' rate-limited for %.1fs", provider, retry_after_seconds)
        self._notify_sink(event)

    def _notify_sink(self, event: dict[str, float | str]) -> None:
        """Entrega una copia a la persistencia sin afectar el circuit breaker."""
        if self._event_sink is None:
            return
        try:
            self._event_sink(dict(event))
        except Exception:  # noqa: BLE001 - telemetría nunca rompe el runtime
            logger.warning("provider_health_event_sink_failed", exc_info=True)

    def is_available(self, provider: str) -> bool:
        with self._lock:
            state = self._get_state(provider)
            if state.rate_limited_until > time.monotonic():
                return False
            if not state.circuit_open:
                return True
            if time.monotonic() - state.last_failure_time > self._recovery_seconds:
                state.circuit_open = False
                logger.info("Circuit breaker HALF_OPEN for provider '%s'", provider)
                return True
            return False

    def avg_latency(self, provider: str) -> float:
        with self._lock:
            state = self._get_state(provider)
            if not state.recent_latencies:
                return 0.0
            return sum(state.recent_latencies) / len(state.recent_latencies)

    def error_rate(self, provider: str) -> float:
        with self._lock:
            state = self._get_state(provider)
            if state.total_calls == 0:
                return 0.0
            return state.total_failures / state.total_calls

    def health_report(self) -> dict[str, dict[str, float | bool | int]]:
        with self._lock:
            return {
                name: {
                    "available": not s.circuit_open and s.rate_limited_until <= time.monotonic(),
                    "error_rate": s.total_failures / s.total_calls if s.total_calls else 0.0,
                    "avg_latency": sum(s.recent_latencies) / len(s.recent_latencies)
                    if s.recent_latencies
                    else 0.0,
                    "total_calls": s.total_calls,
                    "total_successes": s.total_successes,
                    "total_failures": s.total_failures,
                    "consecutive_failures": s.consecutive_failures,
                }
                for name, s in self._states.items()
            }

    def recent_events(self, limit: int = 50) -> list[dict[str, float | str]]:
        """Devuelve eventos agregados recientes, sin excepciones ni payloads."""

        bounded = max(0, min(int(limit), 200))
        if bounded == 0:
            return []
        with self._lock:
            return [dict(event) for event in list(self._events)[-bounded:]][::-1]
