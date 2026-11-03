# 4. Tenant isolation through Postgres RLS

**Decision.** Row-level security with `FORCE` on every tenant table, connected as a non-owner role without `BYPASSRLS`. `set_config(..., true)` scopes the tenant to one transaction. An unset tenant returns no rows. Composite foreign keys stop cross-tenant references. Tool schemas have no tenant parameter.

**Rejected.** App-level `WHERE tenant_id` fails silently when one is forgotten. One database per tenant isolates well but multiplies migrations for a demo.

**Roles.** `owner` runs migrations and is itself subject to `FORCE`. `admin` has `BYPASSRLS` and is used only for seeding and test fixtures, never on a request path. `app` can write only to `disputes`, `review_items`, and draft credit notes. `checkpoint` sees only its own schema.

**Proof.** `tests/isolation/mutation_check.py` applies seven realistic breakages to a scratch database and requires the suite to fail for each. Dropping a policy alone isn't enough to test: with `FORCE` set, that denies everything rather than leaking, so a widened `USING (true)` policy is tested as well.

**Not covered by RLS, and handled separately.** LangGraph checkpoints live in their own schema and role, with the tenant in `thread_id`. Langfuse traces hold tenant data, so a production deployment would need per-tenant projects or masking. The in-process BM25 cache is keyed on tenant, and its hits are hydrated through RLS.

**Known limit.** The API's resume path checks that the review belongs to the tenant before resuming, but the checkpoint tables themselves aren't row-secured.
