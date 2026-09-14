"""Modelos del dominio: contrato de la API, notificación extraída y estado interno de cada solicitud."""

from dataclasses import dataclass
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class RequestStatus(str, Enum):
    """Estados posibles de una solicitud: queued -> processing -> sent | failed."""

    QUEUED = "queued"
    PROCESSING = "processing"
    SENT = "sent"
    FAILED = "failed"


class CreateRequestIn(BaseModel):
    """Cuerpo de POST /v1/requests: el texto libre del usuario."""

    user_input: str = Field(..., min_length=1, max_length=2000)


class CreatedOut(BaseModel):
    """Respuesta de POST /v1/requests."""

    id: str


class ProcessOut(BaseModel):
    """Respuesta de POST /v1/requests/{id}/process."""

    id: str
    status: RequestStatus


class StatusOut(BaseModel):
    """Respuesta de GET /v1/requests/{id}."""

    id: str
    status: RequestStatus


class Notification(BaseModel):
    """Datos estructurados que la IA debe extraer y que se envían a /v1/notify."""

    to: str = Field(..., min_length=3, max_length=320)
    message: str = Field(..., min_length=1, max_length=1000)
    type: Literal["email", "sms"]


@dataclass
class RequestRecord:
    """Estado interno de una solicitud; nunca sale tal cual por la API."""

    id: str
    user_input: str
    status: RequestStatus = RequestStatus.QUEUED
    notification: Optional[Notification] = None
    provider_id: Optional[str] = None
    error: Optional[str] = None
