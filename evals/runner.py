"""Run golden cases through the real graph, and write raw rows that every headline recomputes from.

python -m evals.runner --llm oracle        free, must score about 100%
python -m evals.runner --llm constant      free, must score poorly
python -m evals.runner --llm anthropic     paid, stops at --budget-usd
"""

import argparse
import hashlib
import json
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from adjudicate.config import REPO_ROOT, get_settings
from adjudicate.db import TenantPool
from adjudicate.graph.build import PROMPT_HASH, RunOptions, build_graph, thread_config
from adjudicate.llm import LLM, AnthropicLLM
from adjudicate.mcp_server.service import ToolService
from adjudicate.world.generator import AS_OF
from adjudicate.world.seed import MANIFEST
from evals.fake_llm import ConstantLLM, OracleLLM
from evals.graders.deterministic import grade_case, leaked_canaries

GOLDEN = REPO_ROOT / "data" / "golden"
RESULTS = REPO_ROOT / "evals" / "results"
CASE_TIMEOUT_SECONDS = 180


def load_cases(split: str, limit: int | None = None, critical_only: bool = False) -> list[dict]:
    rows = [json.loads(x) for x in (GOLDEN / f"{split}.jsonl").read_text("utf-8").splitlines() if x]
    if critical_only:
        rows = [r for r in rows if "critical" in r["tags"]]
    return rows[:limit] if limit else rows


def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text("utf-8"))


def resolve_gold(case: dict, manifest: dict) -> dict:
    """Turn symbolic gold such as 'duplicate' into record IDs from the scenario manifest."""
    scenario = manifest["scenarios"][f"{case['tenant']}:{case['scenario_id']}"]
    records = scenario["records"]
    expand = lambda keys: sorted({i for k in keys for i in records[k]})  # noqa: E731
    return {
        "account_ref": scenario["account_ref"],
        "required_records": expand(case["required_records"]),
        "acceptable_records": expand(case["acceptable_records"]),
    }


def stamp(llm_name: str, cases: list[dict]) -> dict:
    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=REPO_ROOT
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain", "--", "src", "data", "corpus", "evals/graders"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        ).stdout.strip()
    )
    corpus = hashlib.sha256()
    for path in sorted((REPO_ROOT / "corpus").rglob("*.md")):
        corpus.update(path.read_bytes())
    settings = get_settings()
    return {
        "commit": commit,
        "dirty": dirty,
        "world_hash": load_manifest()["world_hash"],
        "corpus_hash": corpus.hexdigest()[:16],
        "golden_hash": hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest()[:16],
        "prompt_hash": PROMPT_HASH,
        "llm": llm_name,
        "decide_model": settings.decide_model,
        "decide_effort": settings.decide_effort,
        "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def run_case(
    case: dict,
    llm: LLM,
    pool: TenantPool,
    run_id: str,
    rep: int = 0,
    options: RunOptions | None = None,
    manifest: dict | None = None,
) -> dict:
    """One clean trial. Each gets a fresh dispute ID, so draft notes can't affect another case."""
    resolved = resolve_gold(case, manifest or load_manifest())
    tools = ToolService(pool, case["tenant"])
    graph = build_graph(tools, llm, checkpointer=MemorySaver())
    dispute_id = f"{case['case_id']}-{run_id}-{rep}"
    config = thread_config(case["tenant"], dispute_id)
    began = time.perf_counter()
    graph.invoke(
        {
            "dispute_id": dispute_id,
            "tenant_id": case["tenant"],
            "account_ref": resolved["account_ref"],
            "text": case["dispute_text"],
            "dispute_date": case.get("dispute_date", AS_OF.isoformat()),
            "options": asdict(options or RunOptions()),
            "usage": [],
            "node_ms": [],
        },
        config,
    )
    snapshot = graph.get_state(config)
    interrupted = bool(snapshot.next)
    pre_resume = dict(snapshot.values)
    if interrupted:
        # An approving reviewer stands in for a person, to reach END and write the final result.
        graph.invoke(Command(resume={"reviewer": "eval", "action": "acknowledged"}), config)
    state = dict(graph.get_state(config).values)
    latency_ms = int((time.perf_counter() - began) * 1000)
    grade = grade_case(case, resolved, state)
    prompts_blob = [
        state.get("clauses"),
        state.get("evidence"),
        state.get("result"),
        pre_resume.get("text"),
    ]
    grade["leaks"] = leaked_canaries(case["tenant"], prompts_blob)
    return {
        "case_id": case["case_id"],
        "rep": rep,
        "tenant": case["tenant"],
        "tags": case["tags"],
        "grade": grade,
        "usage": state.get("usage", []),
        "node_ms": state.get("node_ms", []),
        "latency_ms": latency_ms,
        "cost_usd": (state.get("result") or {}).get("cost_usd", 0.0),
        "served_models": sorted({u["served_model"] for u in state.get("usage", [])}),
        "decision": state.get("decision"),
        "credit": state.get("credit"),
        "verify_errors": state.get("verify_errors", []),
    }


def make_llm(kind: str, case: dict, manifest: dict) -> LLM:
    if kind == "oracle":
        return OracleLLM(case, resolve_gold(case, manifest)["required_records"])  # pyright: ignore[reportReturnType]
    if kind == "constant":
        return ConstantLLM()  # pyright: ignore[reportReturnType]
    if kind == "anthropic":
        return AnthropicLLM()
    raise ValueError(kind)


def run_suite(
    cases: list[dict],
    kind: str,
    pool: TenantPool,
    reps: int = 1,
    budget_usd: float = 25.0,
    out_dir: Path | None = None,
    options: RunOptions | None = None,
) -> dict[str, Any]:
    run_id = uuid.uuid4().hex[:8]
    out = out_dir or RESULTS / run_id
    out.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()
    meta = stamp(kind, cases) | {"run_id": run_id, "reps": reps, "cases": len(cases)}
    rows: list[dict] = []
    errors: list[dict] = []
    spent = 0.0
    stopped = False
    with (
        (out / "results.jsonl").open("w", encoding="utf-8") as results_file,
        (out / "errors.jsonl").open("w", encoding="utf-8") as errors_file,
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        for rep in range(reps):
            for case in cases:
                if spent >= budget_usd:
                    stopped = True
                    break
                future = executor.submit(
                    run_case,
                    case,
                    make_llm(kind, case, manifest),
                    pool,
                    run_id,
                    rep,
                    options,
                    manifest,
                )
                try:
                    row = future.result(timeout=CASE_TIMEOUT_SECONDS)
                except FutureTimeout:
                    error = {"case_id": case["case_id"], "rep": rep, "error": "timeout"}
                    errors.append(error)
                    errors_file.write(json.dumps(error) + "\n")
                    continue
                except Exception as exc:  # noqa: BLE001  an infra failure must not score as wrong
                    error = {"case_id": case["case_id"], "rep": rep, "error": repr(exc)[:500]}
                    errors.append(error)
                    errors_file.write(json.dumps(error) + "\n")
                    continue
                spent += row["cost_usd"]
                rows.append(row)
                results_file.write(json.dumps(row, default=str) + "\n")
                results_file.flush()
    meta |= {"errors": len(errors), "spent_usd": round(spent, 4), "budget_stopped": stopped}
    (out / "meta.json").write_text(json.dumps(meta, indent=1), "utf-8")
    return {"meta": meta, "rows": rows, "errors": errors, "out": str(out)}


if __name__ == "__main__":
    from evals.report import summarise

    parser = argparse.ArgumentParser()
    parser.add_argument("--split", default="dev")
    parser.add_argument("--llm", default="oracle", choices=["oracle", "constant", "anthropic"])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--critical-only", action="store_true")
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--budget-usd", type=float, default=25.0)
    args = parser.parse_args()
    if args.split == "heldout":
        print("Held-out run. Log it in docs/heldout-log.md: the limit is 3 runs.", date.today())
    suite = run_suite(
        load_cases(args.split, args.limit, args.critical_only),
        args.llm,
        TenantPool(),
        args.reps,
        args.budget_usd,
    )
    summary = summarise(suite["rows"], suite["errors"], suite["meta"])
    Path(suite["out"], "summary.json").write_text(json.dumps(summary, indent=1), "utf-8")
    print(json.dumps(summary, indent=1))
    print("wrote", suite["out"])
