"""The bound tenant for a tool call. Tools read this; nothing passes a tenant as an argument."""

from contextvars import ContextVar, Token

_current: ContextVar[str | None] = ContextVar("adjudicate_tenant", default=None)


def bind_tenant(tenant_id: str) -> Token[str | None]:
    return _current.set(tenant_id)


def reset_tenant(token: Token[str | None]) -> None:
    _current.reset(token)


def current_tenant() -> str:
    tenant = _current.get()
    if not tenant:
        raise PermissionError("no tenant bound to this call")
    return tenant
