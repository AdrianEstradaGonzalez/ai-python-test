"""Configuración del servicio leída de variables de entorno, con defaults ajustados al proveedor."""

import os
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    """Lee un entero del entorno; si no es válido, usa el default."""
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    """Lee un decimal del entorno; si no es válido, usa el default."""
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    """Valores de configuración agrupados en un único objeto inmutable."""

    # La app comparte red con el proveedor (network_mode: service:provider), por eso es localhost.
    provider_url: str = os.getenv("PROVIDER_URL", "http://localhost:3001")
    provider_api_key: str = os.getenv("PROVIDER_API_KEY", "test-dev-2026")

    # La IA tarda entre 1,5 y 3 s; el timeout de lectura cubre ese peor caso con margen.
    connect_timeout: float = _env_float("PROVIDER_CONNECT_TIMEOUT", 2.0)
    read_timeout: float = _env_float("PROVIDER_READ_TIMEOUT", 10.0)

    # Workers que procesan solicitudes en paralelo (la mayor parte del tiempo esperan a la IA).
    worker_count: int = _env_int("WORKER_COUNT", 50)
    queue_max_size: int = _env_int("QUEUE_MAX_SIZE", 10_000)

    # Intentos contra la IA. El scorecard penaliza pasar de 1,5 llamadas por solicitud,
    # así que solo se reintenta cuando la respuesta no se puede salvar con los guardrails.
    ai_max_attempts: int = _env_int("AI_MAX_ATTEMPTS", 2)

    # /v1/notify acepta 50 peticiones cada 10 s (5 req/s). Nos quedamos algo por debajo.
    notify_rate_rps: float = _env_float("NOTIFY_RATE_RPS", 4.5)
    notify_burst: int = _env_int("NOTIFY_BURST", 5)
    notify_max_attempts: int = _env_int("NOTIFY_MAX_ATTEMPTS", 3)
    notify_backoff: float = _env_float("NOTIFY_BACKOFF", 1.0)


settings = Settings()
