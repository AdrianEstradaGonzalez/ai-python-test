"""Limitador de caudal (token bucket) para no superar el límite de /v1/notify."""

import asyncio
import time


class TokenBucket:
    """Repone `rate` fichas por segundo hasta `capacity`; cada petición gasta una."""

    def __init__(self, rate: float, capacity: int) -> None:
        self._rate = rate
        self._capacity = float(capacity)
        self._tokens = float(capacity)
        self._updated_at = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        """Suma las fichas generadas desde la última consulta."""
        now = time.monotonic()
        self._tokens = min(self._capacity, self._tokens + (now - self._updated_at) * self._rate)
        self._updated_at = now

    async def acquire(self) -> None:
        """Consume una ficha; si no hay, espera lo justo. El lock reparte las salidas de forma regular."""
        async with self._lock:
            self._refill()
            if self._tokens < 1.0:
                await asyncio.sleep((1.0 - self._tokens) / self._rate)
                self._refill()
            self._tokens -= 1.0
