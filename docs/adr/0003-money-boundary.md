# 3. The money boundary

**Decision.** `Decision` has no money field. The model proposes a rule. The rule's predicate selects the lines, and the calculator computes the amount in integer cents. The customer message gets its figure from a template.

**Rejected.** LLM arithmetic. A calculator tool the model calls with chosen inputs, which still lets it steer the amount.

**Where the model still has influence, stated plainly.** It chooses *which* rule to propose, and it can do that wrongly. `verify` limits this: the rule must be in force for this tenant and date, one of its authorising clauses must be cited, and its predicate must match lines. A wrong rule whose predicate also matches can still produce a wrong amount, which the exact-credit metric is there to catch.

**Netting.** `credit = max(0, entitlement - prior credits)` per line. See F-001 in `docs/failure-log.md`.

**Rule versions.** The version in force at the start of the disputed period applies, so May is decided under May's terms.
