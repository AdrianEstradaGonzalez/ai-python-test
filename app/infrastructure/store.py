"""Almacén en memoria de las solicitudes."""

import uuid
from typing import Dict, Optional

from domain.models import RequestRecord, RequestStatus


class InMemoryStore:
    """Diccionario id -> RequestRecord. Un único event loop, así que no necesita locks."""

    def __init__(self) -> None:
        self._records: Dict[str, RequestRecord] = {}

    def create(self, user_input: str) -> RequestRecord:
        """Registra una solicitud nueva en estado `queued`."""
        record = RequestRecord(id=uuid.uuid4().hex, user_input=user_input)
        self._records[record.id] = record
        return record

    def get(self, request_id: str) -> Optional[RequestRecord]:
        """Devuelve la solicitud o None si no existe."""
        return self._records.get(request_id)

    def set_status(self, record: RequestRecord, status: RequestStatus, error: Optional[str] = None) -> None:
        """Cambia el estado y guarda el motivo si es un fallo."""
        record.status = status
        record.error = error
