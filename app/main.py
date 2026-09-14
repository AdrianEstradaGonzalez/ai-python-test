"""Punto de entrada: crea las piezas, las conecta y registra los endpoints."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from api import router
from config import settings
from infrastructure.provider_client import ProviderClient
from infrastructure.store import InMemoryStore
from services.pipeline import NotificationPipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s | %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # evita una línea de log por cada llamada al proveedor
logger = logging.getLogger("ai-service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Arranca almacén, cliente y pipeline al iniciar la app y los cierra al apagarla."""
    app.state.store = InMemoryStore()
    app.state.client = ProviderClient(settings)
    await app.state.client.start()
    app.state.pipeline = NotificationPipeline(app.state.store, app.state.client, settings)
    await app.state.pipeline.start()
    logger.info("Servicio listo. Proveedor en %s", settings.provider_url)
    try:
        yield
    finally:
        await app.state.pipeline.stop()
        await app.state.client.aclose()


app = FastAPI(title="Notification Service (Technical Test)", lifespan=lifespan)
app.include_router(router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Red de seguridad: registra cualquier error no previsto y responde con JSON controlado."""
    logger.exception("Error no controlado en %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"error": "internal_error"})
