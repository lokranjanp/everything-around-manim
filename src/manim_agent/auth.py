import hashlib
import secrets

from .settings import get_settings


class AuthenticationError(ValueError):
    pass


def hash_api_key(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def authenticate_api_key(value: str | None) -> str:
    if value is None:
        raise AuthenticationError("X-API-Key header is required")
    if not secrets.compare_digest(value, get_settings().api_key):
        raise AuthenticationError("invalid API key")
    return hash_api_key(value)


def validate_idempotency_key(value: str | None) -> str:
    if value is None or not 1 <= len(value) <= 255:
        raise ValueError("Idempotency-Key header is required and must be 1-255 characters")
    return value
