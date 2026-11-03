# Progress against the plan

Status of each milestone in the build plan. "Code" means written and tested. It doesn't mean the plan's exit criterion is met, which is stated separately.

**No model has been run.** Every result so far comes from free checks: unit tests, a database, and scripted models. No number here measures decision quality.

| Milestone | Code | Exit criterion |
|---|---|---|
| M0 Domain | Done | Met, with a narrower real-document benchmark. See `m0-findings.md`. Owner should read the Act and gazette directly. |
| M1 Foundation and world | Done | **Met.** The isolation suite passes and goes red on all 7 mutations. |
| M2 Documents and ingestion | Done, with Markdown sources, not PDFs | Clauses load and rule bindings pass. The parse check on real PDFs **hasn't run**. |
| M3 Skeleton and harness | Done | Oracle and null runs behave. A real cited decision **needs an API key**. |
| M4 Retrieval | Harness done | **Not met.** BM25 only, 30 draft queries, no test split, no real documents, dense and rerank not run. |
| M5 Classifier | Code done, encoder training untested | **Not met.** No generated data, no human test set, no trained encoder. |
| M6 Agent, credit, fraud, MCP | Done | Scripted end-to-end runs pass. The brief's examples **need a real model**. Thresholds aren't tuned across seeds. |
| M7 Evals complete | Harness done | **Not met.** 40 draft cases of 150, no held-out set, judge and calibrator never run. |
| M8 Observability and CI | Written | **Not met.** Workflows have never run, because nothing is pushed. The Langfuse code path is untested. |
| M9 Serving | Done | Clean start from empty volumes tested with no API key: seed, MCP, API, fraud queue, tenant 404s, review surviving an app restart, demo page. Not yet tested from a fresh clone, or with a real model. |
| M10 Release | README only | **Not met.** No results, no demo video, no fix cycle on real failures. |

## Free results so far

These describe plumbing and are not model results.

| Check | Result |
|---|---|
| Test suite | 269 passed before the last additions. Re-run with `uv run poe test`. |
| RLS mutation check | 7 of 7 caught, after fixing F-005 |
| Hand-worked credit cases | 37 of 37 after F-001, with one label corrected |
| Oracle run, 40 draft cases | 100% decision, citation and credit (run `f4513836`). This is expected by design. |
| Constant model, 40 draft cases | 52.5% accuracy, 0.48 macro-F1, fails the gate |
| TF-IDF classifier on golden text | 87.5% type accuracy on 40 sentences. Trained on 71 assistant-written lines, so not reportable. |
| BM25 retrieval | Recall@5 of 0.83 to 1.0, on draft labels and short documents. See F-004 before reading anything into it. |

## What only you can do

These are the plan's "never cut" items, and they need you, not more code.

1. **Set `ANTHROPIC_API_KEY`,** with a spend limit, then run `uv run python -m evals.runner --llm anthropic --limit 5 --budget-usd 2`. Nothing has been spent, and the `messages.parse` call is unproven against the live API.
2. **Write `data/classifier/test_human.jsonl`,** about 300 disputes in your own words, before training anything.
3. **Re-derive the gold labels.** Every golden case, query and worked credit is an AI draft, marked `assistant_draft`. Re-derive them by hand, grow them to 150 cases with a locked held-out set, and change the provenance only for those you've done.
4. **Review the fictional T&Cs.** The plan calls for your own terms.
5. **Train the encoders** on a hosted GPU, using `notebooks/train_encoder.ipynb`.
6. **Hand-label about 60 decisions** before looking at judge output.

## Known gaps in the code

- The `record_retrieval` oracle in `evals/attribution.py` swaps in the right playbook only, so it can't yet tell classifier failures from record failures.
- The bounded follow-up evidence loop isn't implemented. It's first on the plan's cut list.
- Query rewriting and definition expansion aren't implemented. They're second on the cut list.
- The stage 2 calibrator exists as functions, and nothing collects its features from runs yet.
- The served API uses a frozen "today" (`AS_OF`, 20 June 2026), to match the synthetic world.
- Tenant documents are Markdown. The PDF parser for real documents isn't written.
