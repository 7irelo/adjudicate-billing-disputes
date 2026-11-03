"""Per-tenant, effective-dated credit rules. Each one names the clauses that authorise it."""

from datetime import date
from functools import lru_cache
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from adjudicate.config import REPO_ROOT

TENANT_DIR = REPO_ROOT.joinpath("data", "tenants")

Method = Literal[
    "full_line_reversal",
    "duplicate_reversal",
    "post_cancellation_proration",
    "post_cancellation_reversal",
    "reversal_without_consent",
    "bill_shock_over_cap",
    "rerate_difference",
]


class CreditRule(BaseModel):
    rule_id: str
    version: int
    effective_from: date
    effective_to: date | None = None
    authorising_clauses: list[str]
    method: Method
    line_types: list[str] = Field(default_factory=list)
    proration: Literal["calendar_days", "thirty_day"] | None = None
    consent_kind: str | None = None
    cap_cents: int | None = None
    auto_credit_limit_cents: int
    dispute_window_days: int

    def in_force(self, on: date) -> bool:
        return self.effective_from <= on and (self.effective_to is None or on <= self.effective_to)


class TenantPolicy(BaseModel):
    tenant_id: str
    name: str
    customer_type: Literal["consumer", "business"]
    proration: str
    notice: dict[str, Any]
    rules: list[CreditRule]

    def rule(self, rule_id: str, on: date) -> CreditRule | None:
        """The highest version of a rule in force on a date, or None if this tenant has none."""
        versions = [r for r in self.rules if r.rule_id == rule_id and r.in_force(on)]
        return max(versions, key=lambda r: r.version) if versions else None

    def rules_on(self, on: date) -> list[CreditRule]:
        ids = sorted({r.rule_id for r in self.rules})
        return [r for r in (self.rule(rule_id, on) for rule_id in ids) if r is not None]


@lru_cache
def load_policy(tenant_id: str) -> TenantPolicy:
    path = TENANT_DIR.joinpath(f"{tenant_id}.yaml")
    return TenantPolicy.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def all_policies() -> list[TenantPolicy]:
    return [load_policy(p.stem) for p in sorted(TENANT_DIR.glob("*.yaml"))]
