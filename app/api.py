"""Capa HTTP: los tres endpoints del contrato. Los handlers solo leen, escriben en memoria y encolan."""

from fastapi import APIRouter, HTTPException, Request, status

from domain.models import CreatedOut, CreateRequestIn, ProcessOut, RequestStatus, StatusOut
from infrastructure.store import InMemoryStore
from services.pipeline import NotificationPipeline

router = APIRouter()


def _store(request: Request) -> InMemoryStore:
    """Obtiene el almacén creado en el lifespan de la app."""
    return request.app.state.store


def _pipeline(request: Request) -> NotificationPipeline:
    """Obtiene el pipeline creado en el lifespan de la app."""
    return request.app.state.pipeline


@router.post("/v1/requests", response_model=CreatedOut, status_code=status.HTTP_201_CREATED, tags=["Requests"])
async def create_request(payload: CreateRequestIn, request: Request) -> CreatedOut:
    """Registra la petición en lenguaje natural en estado `queued` y devuelve su id."""
    record = _store(request).create(payload.user_input)
    return CreatedOut(id=record.id)


@router.post(
    "/v1/requests/{request_id}/process",
    response_model=ProcessOut,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Requests"],
)
async def process_request(request_id: str, request: Request) -> ProcessOut:
    """Encola la solicitud y responde 202 al instante; la IA (1,5-3 s) trabaja en segundo plano."""
    store = _store(request)
    record = store.get(request_id)
    if record is None:
        raise HTTPException(status_code=404, detail="request not found")

    # Idempotente: si ya está en curso o enviada no se vuelve a encolar.
    if record.status in (RequestStatus.PROCESSING, RequestStatus.SENT):
        return ProcessOut(id=record.id, status=record.status)

    # Una solicitud fallida se puede reprocesar: la IA no es determinista.
    if record.status == RequestStatus.FAILED:
        store.set_status(record, RequestStatus.QUEUED)

    if not _pipeline(request).submit(record.id):
        store.set_status(record, RequestStatus.FAILED, "queue_full")

    return ProcessOut(id=record.id, status=record.status)


@router.get("/v1/requests/{request_id}", response_model=StatusOut, tags=["Requests"])
async def get_request(request_id: str, request: Request) -> StatusOut:
    """Devuelve el estado actual de la solicitud."""
    record = _store(request).get(request_id)
    if record is None:
        raise HTTPException(status_code=404, detail="request not found")
    return StatusOut(id=record.id, status=record.status)
