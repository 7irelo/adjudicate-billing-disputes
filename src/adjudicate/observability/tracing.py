"""Optional Langfuse tracing: a no-op unless both keys are set, and exporter errors are swallowed."""

import logging
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

log = logging.getLogger(__name__)


@lru_cache
def _client() -> Any | None:
    if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
        return None
    try:
        from langfuse import get_client

        return get_client()
    except Exception:
        log.warning("Langfuse is configured but failed to start; tracing is off", exc_info=True)
        return None


@contextmanager
def span(name: str, state: Mapping[str, Any] | None = None) -> Iterator[None]:
    """A span per graph node, tagged with the tenant. Failures here never reach the workflow."""
    client = _client()
    if client is None:
        yield
        return
    observation = None
    try:
        metadata = {k: state.get(k) for k in ("tenant_id", "dispute_id")} if state else {}
        observation = client.start_observation(name=name, as_type="span", metadata=metadata)
    except Exception:
        log.warning("failed to open span %s", name, exc_info=True)
    try:
        yield
    finally:
        if observation is not None:
            try:
                observation.end()
            except Exception:
                log.warning("failed to close span %s", name, exc_info=True)


def flush() -> None:
    client = _client()
    if client is not None:
        try:
            client.flush()
        except Exception:
            log.warning("failed to flush traces", exc_info=True)
