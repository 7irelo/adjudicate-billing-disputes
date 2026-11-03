"""Tenant credentials: demo API keys map to a tenant, then to a short-lived signed token."""

import hashlib
import hmac
import time

import jwt

from adjudicate.config import get_settings

ALGORITHM = "HS256"
AUDIENCE = "adjudicate-mcp"
TENANT_IDS = ("baobab", "karoo", "fynbos")


class AuthError(Exception):
    """Credential is missing, invalid or expired. The message is safe to return to a caller."""


def issue_token(tenant_id: str, ttl_seconds: int | None = None) -> str:
    settings = get_settings()
    now = int(time.time())
    ttl = settings.token_ttl_seconds if ttl_seconds is None else ttl_seconds
    claims = {"sub": tenant_id, "aud": AUDIENCE, "iat": now, "exp": now + ttl}
    return jwt.encode(claims, settings.token_secret, algorithm=ALGORITHM)


def verify_token(token: str) -> str:
    """Return the tenant bound to a token. Rejects tampered, expired or wrong-audience tokens."""
    try:
        claims = jwt.decode(
            token,
            get_settings().token_secret,
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            options={"require": ["sub", "exp", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthError("invalid token") from exc
    return str(claims["sub"])


def demo_api_key(tenant_id: str) -> str:
    """Derive a per-tenant demo key from the server secret, so no key is committed."""
    digest = hmac.new(
        get_settings().token_secret.encode(), f"api-key:{tenant_id}".encode(), hashlib.sha256
    ).hexdigest()
    return f"{tenant_id}.{digest[:32]}"


def tenant_from_api_key(api_key: str) -> str:
    tenant_id = api_key.split(".", 1)[0]
    if tenant_id in TENANT_IDS and hmac.compare_digest(api_key, demo_api_key(tenant_id)):
        return tenant_id
    raise AuthError("invalid API key")
