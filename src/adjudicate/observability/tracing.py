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
        dispute = metadata.get("dispute_id")
        # A trace ID seeded from the dispute keeps nodes together, even across a resume.
        context = {"trace_id": client.create_trace_id(seed=dispute)} if dispute else None
        observation = client.start_observation(
            trace_context=context, name=name, as_type="span", metadata=metadata
        )
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


def trace_id_for(dispute_id: str) -> str | None:
    client = _client()
    if client is None:
        return None
    try:
        return client.create_trace_id(seed=dispute_id)
    except Exception:
        log.warning("failed to derive a trace ID", exc_info=True)
        return None


def record_generation(
    dispute_id: str,
    tenant_id: str,
    usage: Mapping[str, Any],
    cost_usd: float,
    prompt_hash: str,
) -> None:
    """One LLM call, with token counts and a cost computed from recorded usage."""
    client = _client()
    if client is None:
        return
    try:
        generation = client.start_observation(
            trace_context={"trace_id": client.create_trace_id(seed=dispute_id)},
            name="decide.llm",
            as_type="generation",
            model=usage.get("served_model"),
            metadata={
                "tenant_id": tenant_id,
                "prompt_hash": prompt_hash,
                "requested_model": usage.get("requested_model"),
                "stop_reason": usage.get("stop_reason"),
            },
            usage_details={
                "input": usage.get("input_tokens", 0),
                "output": usage.get("output_tokens", 0),
                "cache_read_input_tokens": usage.get("cache_read_tokens", 0),
                "cache_creation_input_tokens": usage.get("cache_write_tokens", 0),
            },
            cost_details={"total": cost_usd},
        )
        generation.end()
    except Exception:
        log.warning("failed to record a generation", exc_info=True)


def push_score(dispute_id: str, name: str, value: float) -> None:
    """Attach a grader result to a trace, so a failing eval case links straight to it."""
    client = _client()
    if client is None:
        return
    try:
        client.create_score(
            name=name, value=value, trace_id=client.create_trace_id(seed=dispute_id)
        )
    except Exception:
        log.warning("failed to push score %s", name, exc_info=True)
