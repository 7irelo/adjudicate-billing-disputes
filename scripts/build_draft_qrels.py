"""Extend data/retrieval/qrels.jsonl to the plan's 100 queries: 60 dev and 40 test.

Gold clauses were chosen by reading the T&Cs, not by looking at retriever output. All of them are
`assistant_draft`. Test queries are for scoring a winner once, after choosing on dev.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
B, K, F = "baobab-tcs-2026", "karoo-tcs-2025", "fynbos-msa-2025"
TENANT = {B: "baobab", K: "karoo", F: "fynbos"}

# document, query, gold clauses (section:number)
DEV = [
    (B, "charged the fee twice in one month", ["billing:2.3"]),
    (
        B,
        "is it too late to complain about an invoice from a while ago",
        ["definitions:1.2", "billing:2.2"],
    ),
    (B, "you billed me more per minute than you should have", ["billing:2.4"]),
    (
        B,
        "is there a limit on how much usage you can charge me before warning me",
        ["spend_protection:4.1", "spend_protection:4.2"],
    ),
    (B, "when does my cancellation actually take effect", ["definitions:1.3", "cancellation:5.1"]),
    (B, "do I pay while I'm serving my notice", ["cancellation:5.2"]),
    (B, "will I get back the days after my contract ended", ["cancellation:5.3"]),
    (B, "do you tell me when my minutes are finished", ["out_of_bundle:3.3"]),
    (B, "is my disputed amount payable during a fraud investigation", ["fraud:8.2"]),
    (B, "how big a credit can be approved without a person", ["credits:9.2"]),
    (K, "my usage was high but I'm told there is no cap", ["spend_protection:4.1"]),
    (K, "how are days counted when you prorate", ["definitions:1.1"]),
    (K, "you still billed me for the month after I left", ["cancellation:5.3"]),
    (K, "how many days do I have to raise a dispute", ["definitions:1.2", "billing:2.2"]),
    (K, "billed above the rate on the day of my call", ["billing:2.4"]),
    (K, "roaming was on my bill but I never switched it on", ["roaming:6.2"]),
    (K, "do I need to ask for international calling to be enabled", ["international:7.1"]),
    (K, "who looks at suspected fraud", ["fraud:8.1"]),
    (K, "I was charged after my bundle ended and never chose that option", ["out_of_bundle:3.2"]),
    (K, "can I take this further if I disagree with the result", ["credits:9.3"]),
    (F, "are we liable for calls if our equipment was compromised", ["fraud:8.1"]),
    (F, "who decides liability for a suspected PBX compromise", ["fraud:8.2"]),
    (F, "how long do we have to make a billing claim", ["definitions:1.2", "billing:2.2"]),
    (F, "the rate on our calls is above what was contracted", ["billing:2.4"]),
    (F, "do we get days back after termination", ["cancellation:5.3"]),
    (F, "does our agreement cover international trunk calls", ["international:7.1"]),
    (F, "who is a customer under this agreement", ["definitions:1.1"]),
    (F, "largest credit approved without a commercial manager", ["credits:9.2"]),
    (F, "where do we escalate a claim outcome", ["credits:9.3"]),
    (F, "charges up to our termination date, are they still due", ["cancellation:5.2"]),
]
TEST = [
    (B, "two lines for the same service in one period", ["billing:2.3"]),
    (B, "I want to query a charge, what's the deadline", ["definitions:1.2", "billing:2.2"]),
    (B, "you charged more than the guide said at the time", ["billing:2.4", "international:7.2"]),
    (B, "extra charges after my bundle with no opt in", ["out_of_bundle:3.1", "out_of_bundle:3.2"]),
    (
        B,
        "bill shock amount and whether you warned me",
        ["spend_protection:4.1", "spend_protection:4.3"],
    ),
    (B, "still paying after leaving, want the remaining days credited", ["cancellation:5.3"]),
    (B, "roaming I didn't turn on", ["roaming:6.2"]),
    (B, "are overseas calls from my line my responsibility", ["international:7.2"]),
    (B, "these calls were not made by me", ["fraud:8.1"]),
    (B, "you already gave me a partial credit on this", ["credits:9.1"]),
    (B, "unhappy with your answer, can I go to the regulator", ["credits:9.3"]),
    (B, "my invoice period, what is a billing cycle", ["definitions:1.1"]),
    (K, "charged twice for one service period", ["billing:2.3"]),
    (K, "I cancelled mid-month, can you prorate", ["cancellation:5.2"]),
    (K, "the notice I have to give to cancel", ["cancellation:5.1"]),
    (K, "last day of notice", ["definitions:1.3"]),
    (K, "what does Keep Talking mean for my bill", ["out_of_bundle:3.1"]),
    (K, "charged over my bundle with no Keep Talking", ["out_of_bundle:3.2"]),
    (K, "I turned roaming on, is it payable", ["roaming:6.1"]),
    (K, "calls overseas when calling abroad is enabled", ["international:7.1"]),
    (K, "someone hacked my number", ["fraud:8.1"]),
    (K, "limit for automatic credits", ["credits:9.2"]),
    (K, "no refund because usage was high", ["spend_protection:4.1"]),
    (K, "dispute after thirty days", ["billing:2.2", "definitions:1.2"]),
    (F, "the fee is on our invoice twice", ["billing:2.3"]),
    (F, "written notice to end the service", ["cancellation:5.1"]),
    (F, "30th day after our notice", ["definitions:1.3"]),
    (F, "usage above allowance without opting in", ["out_of_bundle:3.1"]),
    (F, "there's a roaming line on our bill", ["roaming:6.1"]),
    (F, "our spend was unusually high, do we get relief", ["spend_protection:4.1"]),
    (F, "international calls on the PBX are billed how", ["international:7.1"]),
    (F, "the security of our extensions is whose job", ["fraud:8.1"]),
    (F, "fraud desk review before any credit", ["fraud:8.2"]),
    (F, "a credit can't be more than what was charged", ["credits:9.1"]),
    (F, "past the claim period, is any credit possible", ["billing:2.2"]),
    (F, "contract rate overcharge, get the difference back", ["billing:2.4"]),
    (B, "credit above a thousand rand needs who", ["credits:9.2"]),
    (K, "charged above tariff, get the overcharge back", ["billing:2.4"]),
    (K, "I want to escalate this outcome", ["credits:9.3"]),
    (F, "what's left to pay when we end the agreement", ["cancellation:5.2"]),
]


def row(qid: str, split: str, document: str, query: str, gold: list[str]) -> str:
    return json.dumps(
        {
            "qid": qid,
            "split": split,
            "tenant": TENANT[document],
            "as_of": "2026-05-01",
            "query": query,
            "gold_clauses": [f"{document}:{g}" for g in gold],
            "provenance": "assistant_draft",
        },
        ensure_ascii=False,
    )


if __name__ == "__main__":
    path = ROOT / "data" / "retrieval" / "qrels.jsonl"
    existing = [
        x for x in path.read_text("utf-8").splitlines() if x and int(json.loads(x)["qid"][1:]) <= 30
    ]
    extra = [row(f"Q{31 + i:03d}", "dev", *item) for i, item in enumerate(DEV)]
    held = [row(f"Q{101 + i:03d}", "test", *item) for i, item in enumerate(TEST)]
    path.write_text("\n".join(existing + extra + held) + "\n", "utf-8", newline="\n")
    print("dev", len(existing) + len(extra), "test", len(held))
