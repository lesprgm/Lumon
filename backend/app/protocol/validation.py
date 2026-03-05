from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.protocol.enums import ErrorCode
from app.protocol.models import CLIENT_MESSAGE_MODELS, SERVER_MESSAGE_MODELS, CommandEnvelope


class ProtocolValidationError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _validate_against_registry(message: dict[str, Any], registry: dict[str, type]) -> dict[str, Any]:
    try:
        envelope = CommandEnvelope.model_validate(message)
    except ValidationError as exc:
        raise ProtocolValidationError(ErrorCode.BAD_PAYLOAD, f"Malformed envelope: {exc}") from exc

    model = registry.get(envelope.type)
    if model is None:
        raise ProtocolValidationError(ErrorCode.UNKNOWN_COMMAND, f"Unknown message type: {envelope.type}")

    try:
        payload = model.model_validate(envelope.payload)
    except ValidationError as exc:
        raise ProtocolValidationError(ErrorCode.BAD_PAYLOAD, f"Invalid payload for {envelope.type}: {exc}") from exc

    return {"type": envelope.type, "payload": payload.model_dump(mode="json")}


def validate_client_message(message: dict[str, Any]) -> dict[str, Any]:
    return _validate_against_registry(message, CLIENT_MESSAGE_MODELS)


def validate_server_message(message: dict[str, Any]) -> dict[str, Any]:
    return _validate_against_registry(message, SERVER_MESSAGE_MODELS)
