# Progress against the plan

"Code" means written and tested. It doesn't mean the plan's exit criterion is met, which is stated separately.

**No model has been run.** Every result so far comes from free checks: unit tests, a database, and scripted models. No number here measures decision quality.

| Milestone | Code | Exit criterion |
|---|---|---|
| M0 Domain | Done | Met, with a narrower real-document benchmark. See `m0-findings.md`. |
| M1 Foundation and world | Done | **Met.** The isolation suite passes and goes red on all 7 mutations. |
| M2 Documents and ingestion | Done. Tenant terms are Markdown, not PDFs. | Clauses load and rule bindings pass. Real PDFs parse, and the parse check found a real bug (F-006). A person should still read 30 clauses per document. |
| M3 Skeleton and harness | Done | Oracle and null runs behave. A real cited decision **needs an API key**. |
| M4 Retrieval | Done | **Partly met.** BM25 is measured on 100 draft queries and two real documents. Dense and rerank **have not run**: the embedding job was stopped by Windows running low on memory. |
| M5 Classifier | Done | **Not met.** No generated data, no human test set, no trained encoder. The encoder path has **not been run end to end**: its smoke test was stopped by the same memory shortage. |
| M6 Agent, credit, fraud, MCP | Done | Scripted end-to-end runs pass, and fraud thresholds are tuned on one seed and reported on another. The brief's examples **need a real model**. |
| M7 Evals complete | Done | **Not met.** All 150 cases exist, but as drafts. Judge and calibrator are built and unit-tested, and have never seen a real decision. |
| M8 Observability and CI | Done | See the CI section below. Langfuse is tested against a fake client, never a real account. |
| M9 Serving | Done | Clean start from empty volumes tested with no API key: seed, MCP, API, fraud queue, tenant 404s, review surviving an app restart, demo page. Never run with a real model. |
| M10 Release | README, ADRs, cards, failure log, generated tables | **Not met.** No model results, no fix cycle on real failures, no demo video. `docs/demo-script.md` has the steps. |

## Free results so far

These describe plumbing, on assistant-drafted labels. **None may be quoted as a result.**

| Check | Result |
|---|---|
| Hand-worked credit cases | 37 of 37 after F-001, with one label corrected |
| RLS mutation check | 7 of 7 caught, after fixing F-005 |
| Oracle run | 100 of 100 dev and 50 of 50 held-out. Expected by design: it's fed the answers. |
| Constant model | Fails the gate |
| Fraud screen, synthetic, report seed 23 | Flag rate 100% on PBX burst, IRSF and Wangiri; 0% on low-and-slow, which is built to evade; 0% false escalation on three legitimate twins |

### Retrieval, BM25 only

Authored documents, 60 dev queries:

| Strategy | Recall@5 | 95% CI | Context tokens @5 |
|---|---:|---|---:|
| A fixed 512 | 0.983 | 0.95 to 1.0 | 588 |
| B paragraphs | 0.908 | 0.833 to 0.975 | 363 |
| C clause | 0.850 | 0.758 to 0.925 | 86 |
| D clause + path | 0.858 | 0.767 to 0.933 | 109 |
| E small-to-big | 0.858 | 0.767 to 0.933 | 270 |
| D + query rewriting | 0.942 | 0.875 to 0.992 | 117 |

- A's recall is an artefact. These documents barely exceed one 512-token window, so a chunk is nearly the whole document (F-004).
- Rewriting added 0.083 recall@5 to D (paired 95% CI 0.025 to 0.158), so serving uses D with rewriting. Scored once on 40 test queries: 0.912 (CI 0.825 to 0.988). The same author wrote the rewrite list and the queries, which flatters it.
- D against C: +0.008 (paired CI 0.0 to 0.025). The section path shows no clear benefit.

Real documents, 25 queries, no rewriting:

| Document | A | B | C | D | E |
|---|---:|---:|---:|---:|---:|
| Gazette 53991 (R@5, 14 queries) | 0.714 | 0.714 | 0.571 | 0.571 | 0.571 |
| Cell C charter (R@5, 11 queries) | 1.0 | 0.909 | 0.818 | 0.818 | 0.818 |

**Clause-aware chunking did not win on real documents.** The intervals overlap, so this is inconclusive, but the plan's expected winner has no support here. Clause chunks do cost far less context on the gazette: 312 tokens against 2,531 for A.

## CI

The free job runs lint, types, tests, the mutation check, the oracle and constant gates, and BM25 retrieval. The paid smoke and nightly jobs need an `ANTHROPIC_API_KEY` secret, and there isn't one.

## What was stopped

Windows ran low on memory, so Claude Code stopped two background jobs, and I didn't restart them. Close other work, then run:

```bash
uv sync --all-extras
uv run poe embed            # downloads about 4 GB of BGE-M3, partly cached already
uv run python -m evals.retrieval_eval --split dev --modes bm25,dense,hybrid,rerank
uv run pytest tests/integration/test_encoder_smoke.py
```

## What only you can do

1. **Set `ANTHROPIC_API_KEY`,** with a spend limit, then run `uv run python -m evals.runner --llm anthropic --limit 5 --budget-usd 2`. Nothing has been spent, and `messages.parse` is unproven against the live API. Expect first-run fixes.
2. **Write `data/classifier/test_human.jsonl`,** about 300 disputes in your own words, before training anything.
3. **Re-derive the gold labels.** All 150 golden cases, all 125 retrieval queries and the worked credits are AI drafts, marked `assistant_draft` where the format allows. `meta.reportable` stays false until they're `hand_written`, and README tables stay empty until then.
4. **Review the fictional T&Cs,** and decide whether "Baobab" is too close to Bayobab.
5. **Train the encoders** on a hosted GPU, using `notebooks/train_encoder.ipynb`, after `python -m adjudicate.classify.synth`.
6. **Hand-label about 60 decisions** in `data/judge/labels.jsonl`, before looking at judge output.
7. **Re-label 30 cases** at least a week later, for intra-rater agreement.
8. **Record the demo video,** following `docs/demo-script.md`, and work through real failures once a model has run.

## Deviations from the plan

| Plan | Built | Why |
|---|---|---|
| `credit_workings.xlsx` | `credit_workings.yaml` | Diffable in a public repo, with the same independence from the calculator. |
| Tenant T&Cs as PDFs | Markdown | Real-PDF parsing is tested on real documents instead, which is the harder test. |
| Docling for parsing | pdfplumber | Docling's model stack is heavy for two documents. See `ingest/parse.py`. |
| Fallbacks on, flagged in evals | Fallbacks not requested; a different served model routes to review | Simpler, and it needs a real run to test the beta. |
| Break-even chart | `evals/breakeven.py` gives a table | There are no measured costs to chart yet. |
| Regulation vs T&Cs golden family | Not included | Regulation 8A takes effect in 2027, after this synthetic world. See `m0-findings.md`. |
| Intake-encoder type agreement feature | Classifier top-2 margin | Intake has no LLM type to compare with. |
