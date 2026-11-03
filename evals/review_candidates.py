"""Turn resolved reviews into golden-set candidates. A person still decides what becomes gold."""

import argparse
import json
from pathlib import Path

from adjudicate.db import TenantPool
from adjudicate.tenancy.auth import TENANT_IDS

OUT = Path("data/golden/review_candidates.jsonl")


def collect(pool: TenantPool, tenant: str) -> list[dict]:
    with pool.tenant_conn(tenant) as conn:
        rows = conn.execute(
            "SELECT r.review_id, r.queue, r.reasons, r.resolution, d.account_ref, d.text, d.result "
            "FROM review_items r JOIN disputes d USING (tenant_id, dispute_id) "
            "WHERE r.status = 'resolved' ORDER BY r.created_at"
        ).fetchall()
    return [
        {
            "tenant": tenant,
            "review_id": r["review_id"],
            "queue": r["queue"],
            "reasons": r["reasons"],
            "resolution": r["resolution"],
            "account_ref": r["account_ref"],
            "dispute_text": r["text"],
            "model_decision": (r["result"] or {}).get("decision"),
            "provenance": "reviewer_candidate",
        }
        for r in rows
    ]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    pool = TenantPool()
    rows = [row for tenant in TENANT_IDS for row in collect(pool, tenant)]
    args.out.write_text("".join(json.dumps(r, default=str) + "\n" for r in rows), "utf-8")
    print(f"{len(rows)} candidates written to {args.out}")
