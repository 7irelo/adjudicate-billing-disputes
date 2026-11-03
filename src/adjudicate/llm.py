"""The only module that calls Anthropic. Records usage, stop reason and the model actually served."""

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

import anthropic
from pydantic import BaseModel

# USD per million tokens: (input, output). Cache reads at 10% of input, 5-minute writes at 125%.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}


class LLMFailure(Exception):
    """A refusal, truncation or unparseable answer. Callers route to review, never to a default."""

    def __init__(self, reason: str, usage: "Usage | None" = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.usage = usage


@dataclass(frozen=True)
class Usage:
    requested_model: str
    served_model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    stop_reason: str | None

    @property
    def cost_usd(self) -> float:
        price_in, price_out = PRICES.get(
            self.served_model, PRICES.get(self.requested_model, (0, 0))
        )
        return (
            self.input_tokens * price_in
            + self.cache_read_tokens * price_in * 0.1
            + self.cache_write_tokens * price_in * 1.25
            + self.output_tokens * price_out
        ) / 1_000_000


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    parsed: T
    usage: Usage


class LLM(Protocol):
    def parse[T: BaseModel](
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: type[T],
        max_tokens: int = 8000,
        effort: str | None = None,
    ) -> LLMResult[T]: ...


def _usage(response: Any, requested: str) -> Usage:
    u = response.usage
    return Usage(
        requested_model=requested,
        served_model=response.model,
        input_tokens=u.input_tokens,
        output_tokens=u.output_tokens,
        cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        stop_reason=response.stop_reason,
    )


class AnthropicLLM:
    """Structured output through `messages.parse`.

    Temperature and forced tool choice return a 400 on these models, so neither is set. Runs can't
    be pinned, which is why evals use repetitions and confidence intervals.
    """

    def __init__(self, client: anthropic.Anthropic | None = None) -> None:
        self.client = client or anthropic.Anthropic()

    def parse[T: BaseModel](
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: type[T],
        max_tokens: int = 8000,
        effort: str | None = None,
    ) -> LLMResult[T]:
        kwargs: dict[str, Any] = {}
        if effort:
            kwargs["output_config"] = {"effort": effort}
        response = self.client.messages.parse(
            model=model,
            max_tokens=max_tokens,
            # The system prompt is stable and holds no tenant data, so it's safe to cache.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_format=schema,
            **kwargs,
        )
        usage = _usage(response, model)
        if response.stop_reason == "refusal":
            raise LLMFailure("refusal", usage)
        if response.stop_reason == "max_tokens":
            raise LLMFailure("truncated", usage)
        parsed = response.parsed_output
        if parsed is None:
            raise LLMFailure("unparseable", usage)
        return LLMResult(parsed=parsed, usage=usage)


@lru_cache
def default_llm() -> AnthropicLLM:
    return AnthropicLLM()
