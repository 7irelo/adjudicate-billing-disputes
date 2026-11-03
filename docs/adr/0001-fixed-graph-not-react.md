# 1. A fixed LangGraph workflow, not a ReAct agent

**Decision.** A fixed graph with one evidence playbook per dispute type. The model makes one decision, and code calls every tool.

**Rejected.** A free-form ReAct agent gives up a predictable, auditable path on a money decision. Plain Python would mean rebuilding checkpointing and `interrupt()`.

**What would change this.** Disputes that need open-ended investigation, where a fixed playbook visibly misses evidence in failure attribution.
