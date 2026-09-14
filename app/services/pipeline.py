"""Pipeline de procesamiento: cola + workers que hacen extracción con IA, guardrails y envío."""

import asyncio
import logging
from typing import List, Optional, Set

from config import Settings
from domain.models import Notification, RequestRecord, RequestStatus
from infrastructure.provider_client import ProviderClient
from infrastructure.store import InMemoryStore
from services.guardrails import GuardrailError, parse_ai_response
from services.prompts import build_messages

logger = logging.getLogger("ai-service.pipeline")


class NotificationPipeline:
    """Desacopla la API del trabajo lento: /process encola y los workers lo resuelven en segundo plano."""

    def __init__(self, store: InMemoryStore, client: ProviderClient, settings: Settings) -> None:
        self._store = store
        self._client = client
        self._settings = settings
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=settings.queue_max_size)
        self._workers: List[asyncio.Task] = []
        # Ids en cola o en proceso: evita encolar dos veces la misma solicitud.
        self._inflight: Set[str] = set()

    async def start(self) -> None:
        """Lanza los workers dentro del event loop de la app."""
        self._workers = [asyncio.create_task(self._worker()) for _ in range(self._settings.worker_count)]
        logger.info("Pipeline arrancado con %d workers", self._settings.worker_count)

    async def stop(self) -> None:
        """Cancela los workers y espera a que terminen."""
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []

    def submit(self, request_id: str) -> bool:
        """Encola una solicitud sin bloquear. Devuelve False solo si la cola está llena."""
        if request_id in self._inflight:
            return True
        try:
            self._queue.put_nowait(request_id)
        except asyncio.QueueFull:
            return False
        self._inflight.add(request_id)
        return True

    async def _worker(self) -> None:
        """Bucle de un worker: saca ids de la cola y los procesa sin morir ante errores inesperados."""
        while True:
            request_id = await self._queue.get()
            record = None
            try:
                record = self._store.get(request_id)
                if record is not None:
                    await self._process(record)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Error inesperado procesando %s", request_id)
                if record is not None:
                    self._store.set_status(record, RequestStatus.FAILED, "internal_error")
            finally:
                self._inflight.discard(request_id)
                self._queue.task_done()

    async def _process(self, record: RequestRecord) -> None:
        """Procesa una solicitud de principio a fin: extraer -> validar -> notificar -> estado final."""
        self._store.set_status(record, RequestStatus.PROCESSING)

        notification = await self._extract(record)
        if notification is None:
            return
        record.notification = notification

        outcome = await self._client.notify(notification, trace_id=record.id)
        if outcome.ok:
            record.provider_id = outcome.provider_id
            self._store.set_status(record, RequestStatus.SENT)
        else:
            self._store.set_status(record, RequestStatus.FAILED, outcome.error)

    async def _extract(self, record: RequestRecord) -> Optional[Notification]:
        """Pregunta a la IA y pasa la respuesta por los guardrails, reintentando solo si tiene sentido."""
        messages = build_messages(record.user_input)
        reason = "unknown"

        for attempt in range(1, self._settings.ai_max_attempts + 1):
            content = await self._client.extract(messages)
            if content is None:
                reason = "ai_unavailable"
                continue
            try:
                return parse_ai_response(content, record.user_input)
            except GuardrailError as exc:
                reason = exc.reason
                logger.info("Intento %d de %s descartado: %s", attempt, record.id, reason)
                if not exc.retryable:
                    break

        self._store.set_status(record, RequestStatus.FAILED, f"extraction_failed: {reason}")
        return None
