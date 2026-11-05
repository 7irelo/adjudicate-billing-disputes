# Progress against the plan

"Code" means written and tested. It doesn't mean the plan's exit criterion is met, which is stated separately.

**A real model has now run, on draft labels only.** Nothing here is a reportable result until the labels are the owner's, and `meta.reportable` stays false until then.

| Milestone | Code | Exit criterion |
|---|---|---|
| M0 Domain | Done | Met, with a narrower real-document benchmark. See `m0-findings.md`. |
| M1 Foundation and world | Done | **Met.** The isolation suite passes and goes red on all 7 mutations. |
| M2 Documents and ingestion | Done. Tenant terms are Markdown, not PDFs. | Clauses load and rule bindings pass. Real PDFs parse, and the parse check found a real bug (F-006). A person should still read 30 clauses per document. |
| M3 Skeleton and harness | Done | **Met.** Real decisions cite both clauses and records, and code verifies them. |
| M4 Retrieval | Done | **Met on draft labels.** BM25, dense and hybrid across all five strategies, rerank on the leader, a test split scored once, and two real documents. Needs your labels to be reportable. |
| M5 Classifier | Done | **Not met.** About 3,300 rows are generated, and LLM arms have run on draft text. No human test set, no hand-check, no trained encoder. The encoder path passes end to end on a tiny random model: training, temperature scaling, ONNX export, int8 quantisation and serving. |
| M6 Agent, credit, fraud, MCP | Done | **Met** in-process with a real model, including the brief's two examples. The real-model path over HTTP MCP hasn't run. |
| M7 Evals complete | Done | **Not met.** All 150 cases exist, but as drafts. The judge has never run, since the API credit ran out first. The sweep and held-out runs haven't run either. |
| M8 Observability and CI | Done | See the CI section below. Langfuse is tested against a fake client, never a real account. |
| M9 Serving | Done | Clean start from empty volumes tested with no API key: seed, MCP, API, fraud queue, tenant 404s, review surviving an app restart, demo page. Never run with a real model. |
| M10 Release | README, ADRs, cards, failure log, generated tables | **Not met.** One fix cycle done on real failures (F-007, F-008). No reportable results, no demo video. `docs/demo-script.md` has the steps. |

## Real model runs, on draft labels

**Not reportable.** `claude-opus-5-5`, effort `high`, in-process tools, private Postgres.

| Metric | Full dev run, before F-007 (100 cases) | Partial rerun, after F-007 (first 41 cases) |
|---|---:|---:|
| Decision accuracy (95% CI) | 0.91 (0.85 to 0.96) | 0.976 (0.927 to 1.0) |
| Exact credit match | 0.99 | 1.0 |
| Clause precision / recall | 0.66 / 0.99 | 0.74 / 1.0 |
| Record precision / recall | 0.86 / 1.0 | 0.90 / 1.0 |
| Over-credit | R0 | R0 |
| Under-credit | R2,320 (one case, in human review) | R0 |
| Zero-tolerance failures | 0 | 0 |
| Cases needing a repair call | 38 | 4 |
| Latency p50 / p95 | 12.6 s / 25.3 s | 9.2 s / 17.4 s |
| Cost | $4.42 | $1.16 |
| Majority baseline accuracy | 0.42 | not comparable |

**The two columns aren't comparable.** The rerun covers an earlier, different mix of cases, and it stopped when the API credit ran out. It doesn't include the six partial-consent cases.

What the full run shows:

- Nine of the nine wrong decisions outside G-0009's failed credit are label disagreements (F-008), not clear model errors. They need your ruling.
- Every error was held by a safety layer: nothing wrong was auto-credited, and over-credit was R0.
- Clause precision is low mostly because the model also cites the dispute-window clauses, which is arguably right. That's a labelling call for you too.
- The TF-IDF classifier got 22 of 100 types wrong, and none of the 22 changed a decision. That's early evidence for the plan's question of whether classification earns its place. It isn't an oracle ablation.

### Calibrator, cross-fitted on the 89 decisions the model made

Error stays near 11-13% from 99% coverage down to 19%, so **these features don't predict whether a decision is right.** A 5% risk target leaves only 3 cases automatic. The calibrator is therefore **not installed**, and the gate uses its hard rules alone. Refit it on your labels, because F-008's label disagreements are most of these errors.

### Classifier arms, on 150 assistant-written lines

Smoke test only. Trained on 2,981 generated rows, which cost $0.19 to generate.

| Arm | Type macro-F1 | Accuracy (95% CI) | Critical recall | p50 | USD per 1k |
|---|---:|---:|---:|---:|---:|
| Majority | 0.03 | 0.12 (0.07 to 0.17) | 0.0 | 0 ms | 0 |
| TF-IDF | 0.68 | 0.76 (0.69 to 0.83) | 0.2 | 7 ms | 0 |
| Haiku 5.5 | 0.87 | 0.91 (0.87 to 0.95) | 0.0 | 1.3 s | 0.03 |
| Sonnet 5.5 | 0.90 | 0.93 (0.89 to 0.97) | 0.0 | 1.6 s | 0.67 |
| Opus 5.5 | 0.92 | 0.95 (0.91 to 0.98) | 0.0 | 2.1 s | 1.45 |
| Cascade (TF-IDF, then Haiku) | 0.86 | 0.90 (0.85 to 0.95) | 0.2 | 1.2 s | 0.02 |

Every LLM arm misses `critical` urgency on this text, which should be checked against your human-written set.

## Free results so far

These describe plumbing, on assistant-drafted labels. **None may be quoted as a result.**

| Check | Result |
|---|---|
| Hand-worked credit cases | 37 of 37 after F-001, with one label corrected |
| RLS mutation check | 7 of 7 caught, after fixing F-005 |
| Oracle run | 100 of 100 dev and 50 of 50 held-out. Expected by design: it's fed the answers. |
| Constant model | Fails the gate |
| Fraud screen, synthetic, report seed 23 | Flag rate 100% on PBX burst, IRSF and Wangiri; 0% on low-and-slow, which is built to evade; 0% false escalation on three legitimate twins |

### Retrieval

Authored documents, 60 draft dev queries, recall@5:

| Strategy | BM25 | Dense | Hybrid | Context tokens @5 (dense) |
|---|---:|---:|---:|---:|
| A fixed 512 | 0.983 | 1.0 | 1.0 | 634 |
| B paragraphs | 0.908 | 1.0 | 1.0 | 521 |
| C clause | 0.850 | 0.992 | 0.967 | 111 |
| D clause + path | 0.858 | 0.975 | 0.975 | 142 |
| E small-to-big | 0.858 | 0.975 | 0.975 | 348 |

Strategy D with rewriting, recall@5:

| Mode | Recall@5 | 95% CI | p50 per query (i7-1355U) |
|---|---:|---|---:|
| BM25 | 0.942 | 0.875 to 0.992 | 27 ms |
| Hybrid | 0.992 | 0.975 to 1.0 | 343 ms |
| Hybrid + rerank | 1.0 | 1.0 to 1.0 | 11.0 s |
| Hybrid, on 40 test queries, scored once | 0.963 | 0.90 to 1.0 | 279 ms |
| BM25, on 40 test queries, scored once | 0.912 | 0.825 to 0.988 | 34 ms |

- A's recall is an artefact. These documents barely exceed one 512-token window, so a chunk is nearly the whole document (F-004).
- Rewriting added 0.083 recall@5 to BM25 on D (paired 95% CI 0.025 to 0.158). The same author wrote the rewrite list and the queries, which flatters it.
- D against C on BM25: +0.008 (paired CI 0.0 to 0.025). The section path shows no clear benefit.
- **Serving stays on BM25 with rewriting.** Dense would need torch and a 4 GB model in the demo image, for a gain that doesn't carry over to real documents. Reranking at 11 s per query on this CPU isn't viable.

Real documents, 25 queries, recall@5, no rewriting:

| Document | Mode | A | B | C | D | E |
|---|---|---:|---:|---:|---:|---:|
| Gazette 53991 (14 queries) | BM25 | 0.714 | 0.714 | 0.571 | 0.571 | 0.571 |
| | Dense | 0.5 | 0.643 | 0.5 | 0.5 | 0.5 |
| Cell C charter (11 queries) | BM25 | 1.0 | 0.909 | 0.818 | 0.818 | 0.818 |
| | Dense | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |

**This is the plan's self-authored-world threat, measured.** Dense reached about 0.98 on authored documents but only 0.5 to 0.64 on the long gazette, where BM25 did at least as well. Clause-aware chunking didn't win on real documents either. Intervals are wide (about 0.2 to 0.8 on the gazette), so both are inconclusive, but neither expected winner has support. Clause chunks do cost far less context on the gazette: about 310 tokens against about 2,500 for A.

## CI

**The free job passes on GitHub,** on its first run at `db49910` (run 42015886484). It runs lint, types, tests, the mutation check, the oracle and constant gates, and BM25 retrieval. The paid smoke and nightly jobs need an `ANTHROPIC_API_KEY` secret, and have not run.

The plan asks for red builds on deliberate regression branches. That's still open. The same breakages were tested locally in a throwaway clone instead, and each one failed:

| Deliberate regression | Result |
|---|---|
| Calculator caps instead of netting (the F-001 bug) | 3 worked credit cases fail |
| `verify` stops checking quotes | 1 test fails |
| Fraud route removed | 2 graph tests fail |
| Constant model through the real gate | Gate exits 1, on zero-tolerance failures |
| Seven RLS breakages | All 7 caught by `mutation_check.py` |

## Last checked

A fresh clone with locked default dependencies, on a private Postgres: 522 tests pass (encoder smoke skipped, as it is in CI), lint clean, oracle gate passes. In the main checkout: 7 of 7 mutations caught, and the encoder smoke test passes with the `ml` extra. With records hidden, oracle record recall falls to about 3%.

**`docker compose up` hasn't run on the final code.** The last clean-start pass predates the follow-up loop, rewriting and the calibrator. The fresh-clone Docker test was stopped by low memory, and Docker Desktop's engine has been hung since, so it needs a restart. The port also moved to 55491.

## API key status

Paid runs used `ANTHROPIC_API_KEY` from `C:\Dev\aptiverse\.env`, as the owner asked. Total spend was about $6.30, and **that key's account is now out of credit, so anything else using it will fail until it's topped up.** The key in `aptiverse/api/.env` needs an `anthropic-workspace-id` header, so it wasn't used. No key was written to this repo.

## Docker

Docker Desktop's engine stopped responding during this work and wasn't restarted, because other projects use it. Restart it, close other heavy work, then run from a fresh clone: `docker compose up --build`.

## What only you can do

1. **Paid runs, on hold by the owner's choice to avoid the cost.** To finish them, top up API credit, then run: `evals.runner --llm anthropic` on dev, `evals.judge_calibration`, `evals.attribution`, `evals.sweep`, `evals.calibrate`, and held-out once your labels exist. About $15 (est.).
2. **Write `data/classifier/test_human.jsonl`,** about 300 disputes in your own words, before training anything.
3. **Re-derive the gold labels.** All 150 golden cases, all 125 retrieval queries and the worked credits are AI drafts, marked `assistant_draft` where the format allows. `meta.reportable` stays false until they're `hand_written`, and README tables stay empty until then.
4. **Review the fictional T&Cs,** and decide whether "Baobab" is too close to Bayobab.
5. **Train the encoders** on a hosted GPU, using `notebooks/train_encoder.ipynb`, after writing the human test set. Training data is already generated.
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
