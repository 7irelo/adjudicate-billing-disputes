"""`poe dispute --tenant baobab --scenario duplicate_recurring "My May fee is on the bill twice"`."""

import argparse
import json
import uuid

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from adjudicate.db import TenantPool
from adjudicate.graph.build import build_graph, thread_config
from adjudicate.llm import default_llm
from adjudicate.mcp_server.service import ToolService
from adjudicate.world.generator import AS_OF
from adjudicate.world.seed import MANIFEST


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("text")
    parser.add_argument("--tenant", required=True, choices=["baobab", "karoo", "fynbos"])
    parser.add_argument("--account", help="an account reference")
    parser.add_argument("--scenario", help="use this scenario's account from the world manifest")
    args = parser.parse_args()
    account = args.account
    if args.scenario:
        manifest = json.loads(MANIFEST.read_text("utf-8"))
        account = manifest["scenarios"][f"{args.tenant}:{args.scenario}"]["account_ref"]
    if not account:
        parser.error("give --account or --scenario")

    tools = ToolService(TenantPool(), args.tenant)
    graph = build_graph(tools, default_llm(), checkpointer=MemorySaver())
    dispute_id = f"D-{uuid.uuid4().hex[:8]}"
    config = thread_config(args.tenant, dispute_id)
    graph.invoke(
        {
            "dispute_id": dispute_id,
            "tenant_id": args.tenant,
            "account_ref": account,
            "text": args.text,
            "dispute_date": AS_OF.isoformat(),
            "options": {},
            "usage": [],
            "node_ms": [],
        },
        config,
    )
    state = graph.get_state(config)
    if state.next:
        print("Paused for review:", json.dumps(state.tasks[0].interrupts[0].value, indent=1))
        graph.invoke(Command(resume={"reviewer": "cli", "action": "acknowledged"}), config)
    print(json.dumps(graph.get_state(config).values["result"], indent=1))


if __name__ == "__main__":
    main()
