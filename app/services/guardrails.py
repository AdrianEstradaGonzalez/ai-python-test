"""Guardrails: limpian, reparan y validan la respuesta de la IA antes de enviar nada.

Flujo: texto libre -> bloque JSON -> dict (reparando si hace falta) -> claves normalizadas
-> campos completados y validados contra la petición original -> Notification.
"""

import ast
import json
import re
from typing import Any, Dict, Optional

from pydantic import ValidationError

from domain.models import Notification

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"\+?\d[\d\s-]{7,}\d")

# Nombres alternativos que la IA usa para cada campo.
KEY_ALIASES = {
    "to": {"to", "recipient", "destination", "destinatario", "target", "phone", "email"},
    "message": {"message", "body", "text", "content", "msg", "mensaje"},
    "type": {"type", "channel", "method", "medium", "tipo", "canal"},
}


class GuardrailError(Exception):
    """Respuesta no aprovechable. `retryable` indica si tiene sentido volver a preguntar a la IA."""

    def __init__(self, reason: str, retryable: bool) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


def parse_ai_response(content: Optional[str], user_input: str) -> Notification:
    """Punto de entrada: convierte la respuesta cruda de la IA en una Notification válida o lanza GuardrailError."""
    if not content or not content.strip():
        raise GuardrailError("empty_response", retryable=True)

    block = extract_json_block(content)
    if block is None:
        # Sin JSON: negativa del modelo o texto libre. Otra inferencia puede salir bien.
        raise GuardrailError("no_json_in_response", retryable=True)

    data = load_json_lenient(block)
    if data is None:
        raise GuardrailError("unparseable_json", retryable=True)

    if "error" in {k.lower() for k in data}:
        # El propio modelo dice que faltan datos en la petición: repetir no lo cambiará.
        raise GuardrailError(f"model_reported_{data.get('error')}", retryable=False)

    fields = normalize_keys(data)
    return build_notification(fields, user_input)


def extract_json_block(content: str) -> Optional[str]:
    """Aísla el objeto JSON: quita bloques Markdown y texto alrededor. Si está truncado, devuelve lo que haya."""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL | re.IGNORECASE)
    text = fenced.group(1) if fenced else content

    start = text.find("{")
    if start == -1:
        return None
    end = text.rfind("}")
    return text[start : end + 1] if end > start else text[start:]


def load_json_lenient(block: str) -> Optional[Dict[str, Any]]:
    """Intenta parsear el bloque tal cual y, si falla, aplicando reparaciones de menos a más agresivas."""
    candidates = [block, _repair(block)]
    # Claves sin comillas ({to: "x"}): último recurso porque la regex podría tocar el texto del mensaje.
    candidates.append(re.sub(r'([{,]\s*)([A-Za-z_]\w*)\s*:', r'\1"\2":', candidates[1]))

    for candidate in candidates:
        for loader in (json.loads, ast.literal_eval):  # literal_eval acepta comillas simples, sin ejecutar código
            try:
                data = loader(candidate)
            except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
                continue
            if isinstance(data, dict):
                return data
    return None


def _repair(block: str) -> str:
    """Arregla JSON truncado: quita '...' y comas colgantes, cierra la cadena y las llaves abiertas."""
    text = block.strip()
    text = re.sub(r"(\.\.\.|…)\s*$", "", text).rstrip()
    text = re.sub(r",\s*([}\]])", r"\1", text)
    text = text.rstrip(",").rstrip()
    if text.count('"') % 2 == 1:
        text += '"'
    text += "}" * max(0, text.count("{") - text.count("}"))
    return text


def normalize_keys(data: Dict[str, Any]) -> Dict[str, str]:
    """Pasa las claves a minúsculas, traduce alias y descarta campos extra (confidence, latency_ms...)."""
    fields: Dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(value, (str, int, float)):
            continue
        lowered = str(key).strip().lower()
        for canonical, aliases in KEY_ALIASES.items():
            if lowered in aliases and canonical not in fields:
                fields[canonical] = str(value).strip()
    return fields


def build_notification(fields: Dict[str, str], user_input: str) -> Notification:
    """Completa lo que falta a partir de la petición original, valida coherencia y construye la Notification."""
    to = fields.get("to") or _find_destination(user_input)
    if not to:
        raise GuardrailError("missing_destination", retryable=True)

    # Anti-alucinación: el destinatario tiene que aparecer en lo que escribió el usuario.
    if not _appears_in_input(to, user_input):
        raise GuardrailError("destination_not_in_input", retryable=True)

    # El tipo lo decide el formato del destinatario, no la IA: cubre el `type` ausente y el contradictorio
    # (un teléfono nunca puede salir por email).
    if EMAIL_RE.fullmatch(to):
        notif_type = "email"
    elif _is_phone(to):
        notif_type = "sms"
        to = ("+" if to.startswith("+") else "") + re.sub(r"\D", "", to)
    else:
        raise GuardrailError("invalid_destination_format", retryable=True)

    message = fields.get("message", "").strip()
    if not message:
        raise GuardrailError("missing_message", retryable=True)

    try:
        return Notification(to=to, message=message[:1000], type=notif_type)
    except ValidationError as exc:
        raise GuardrailError(f"schema_validation: {exc.errors()[0]['msg']}", retryable=True) from exc


def _find_destination(user_input: str) -> Optional[str]:
    """Busca un email o teléfono en la petición original (recupera respuestas sin campo `to`)."""
    match = EMAIL_RE.search(user_input) or PHONE_RE.search(user_input)
    return match.group(0).strip() if match else None


def _appears_in_input(to: str, user_input: str) -> bool:
    """Comprueba que el destino está en la petición: emails sin distinguir mayúsculas, teléfonos por dígitos."""
    if "@" in to:
        return to.lower() in user_input.lower()
    digits = re.sub(r"\D", "", to)
    return len(digits) >= 9 and digits in re.sub(r"\D", "", user_input)


def _is_phone(value: str) -> bool:
    """Teléfono plausible: entre 9 y 15 dígitos, admitiendo espacios, guiones y '+' inicial."""
    return bool(re.fullmatch(r"\+?[\d\s-]+", value)) and 9 <= len(re.sub(r"\D", "", value)) <= 15
