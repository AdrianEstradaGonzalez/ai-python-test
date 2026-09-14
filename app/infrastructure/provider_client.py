"""Cliente HTTP del proveedor: motor de IA (/v1/ai/extract) y notificaciones (/v1/notify)."""

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Dict, List, Optional

import httpx

from config import Settings
from domain.models import Notification
from infrastructure.rate_limiter import TokenBucket

logger = logging.getLogger("ai-service.provider")


@dataclass
class NotifyOutcome:
    """Resultado final de un envío, con los reintentos ya agotados."""

    ok: bool
    provider_id: Optional[str] = None
    error: Optional[str] = None


class ProviderClient:
    """Envuelve un único httpx.AsyncClient (pool de conexiones) compartido por toda la app."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Optional[httpx.AsyncClient] = None
        # El límite de caudal solo aplica a /v1/notify; la IA no tiene rate limit.
        self._notify_limiter = TokenBucket(settings.notify_rate_rps, settings.notify_burst)

    async def start(self) -> None:
        """Crea el cliente HTTP al arrancar la app."""
        self._client = httpx.AsyncClient(
            base_url=self._settings.provider_url,
            headers={"X-API-Key": self._settings.provider_api_key},
            timeout=httpx.Timeout(self._settings.read_timeout, connect=self._settings.connect_timeout),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=100),
        )

    async def aclose(self) -> None:
        """Cierra el pool de conexiones al apagar la app."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def extract(self, messages: List[Dict[str, str]]) -> Optional[str]:
        """Llama una vez a la IA y devuelve el texto de la respuesta, o None si la llamada falla.

        No reintenta aquí: cada llamada cuenta como intento de inferencia, así que
        decidir si merece la pena repetir es cosa del pipeline.
        """
        try:
            response = await self._client.post("/v1/ai/extract", json={"messages": messages})
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            logger.warning("Fallo llamando a la IA: %r", exc)
            return None

    async def notify(self, notification: Notification, trace_id: str) -> NotifyOutcome:
        """Envía la notificación respetando el rate limit y reintentando fallos transitorios."""
        error = "unknown"
        for attempt in range(1, self._settings.notify_max_attempts + 1):
            await self._notify_limiter.acquire()
            try:
                response = await self._client.post(
                    "/v1/notify",
                    json=notification.model_dump(),
                    params={"trace_id": trace_id},
                )
            except httpx.HTTPError as exc:
                error = f"network: {exc!r}"
            else:
                if response.status_code < 300:
                    return NotifyOutcome(ok=True, provider_id=response.json().get("provider_id"))
                error = f"http_{response.status_code}"
                # Un 4xx distinto de 429 es un fallo nuestro (payload, API key): reintentar no lo arregla.
                if response.status_code < 500 and response.status_code != 429:
                    break

            if attempt < self._settings.notify_max_attempts:
                # Backoff exponencial con jitter para que los reintentos no choquen entre sí.
                await asyncio.sleep(random.uniform(0, self._settings.notify_backoff * 2 ** (attempt - 1)))

        return NotifyOutcome(ok=False, error=error)
