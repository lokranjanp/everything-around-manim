import hashlib
import secrets

from fastapi import Header, HTTPException, status

from .settings import get_settings


def hash_api_key(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def require_api_key(x_api_key: str = Header(alias="X-API-Key")) -> str:
    configured = get_settings().api_key
    if not secrets.compare_digest(x_api_key, configured):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid API key")
    return hash_api_key(x_api_key)


def require_idempotency_key(
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
) -> str:
    return idempotency_key

