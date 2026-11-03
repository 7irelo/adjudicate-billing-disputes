# Data card

| Data | Source | Status |
|---|---|---|
| Accounts, CDRs, invoices | `world/generator.py`, seeded. MSISDNs use the `+27 00` prefix. | Synthetic. No real people. |
| Tenant T&Cs | Written for this project. All three operators are fictional. | Drafted by an AI assistant. Owner to review. |
| ICASA extracts | Short quotes from GG 53991, 23 Jan 2026. | Real, in force from about 23 Jan 2027. |
| Golden cases | `data/golden/dev.jsonl`, 100 cases. | `assistant_draft`. Not reportable. |
| Held-out cases | `data/golden/heldout.jsonl`, 50 cases. | `assistant_draft`. Replace with owner-written cases before any reported run. |
| Credit workings | `data/golden/credit_workings.yaml` | Worked before the calculator existed, by the same author as the calculator. Owner to re-derive. |
| Retrieval labels | `qrels.jsonl`, 60 dev and 40 test. `qrels_real.jsonl`, 25 on real documents. | `assistant_draft`. |
| Classifier seed | `data/classifier/seed_train.jsonl`, 71 examples. | Assistant-written, for the serving default only. |
| Classifier test set | `data/classifier/test_human.jsonl` | **Doesn't exist yet.** Must be written by a person before training. |

## Threats to validity

- **A self-authored world.** T&Cs, data and labels share an author, so results may look better than they should.
- **Drafts aren't independent.** Assistant-drafted gold was written from the same reading of the T&Cs that the system is judged on.
- **Synthetic fraud.** Patterns come from a generator, and a screen tuned on them will overfit.
- **Small n.** With 150 cases, intervals are still wide. Say so beside every number.
