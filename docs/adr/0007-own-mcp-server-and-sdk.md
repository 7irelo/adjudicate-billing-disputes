# 7. An own MCP server, a thin client, and the Anthropic SDK directly

**Decision.** An MCP server on the official `mcp` SDK, over Streamable HTTP with a bearer tenant token. Graph nodes call tools deterministically through a `Tools` interface, with an in-process implementation for tests and evals. `llm.py` is the only module that calls Anthropic.

**Rejected.** `langchain-anthropic` hides `usage` and stop reasons. Claude's native Citations API can't be combined with structured output, and can't cite database records.

**Honest trade-off.** Evals call `ToolService` in-process, not over HTTP. It's the same code behind both, and contract tests cover the MCP layer, but the overhead of the hop should be measured before any latency claim is made about the served path.
