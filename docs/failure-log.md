# Failure log

Only real failures go in, written when they're found. No staged examples, nothing reconstructed from memory.

## F-001: Prior credits were capped, not netted

Found: 2026-11-03, first run of the calculator against `data/golden/credit_workings.yaml`, before any commit.

Symptom: 3 of 37 worked cases disagreed. C29 (bill shock with a prior credit) and C34 (re-rate with a prior credit) came out too high. C34 gave 8000 cents against 7000 worked by hand.

Layer: credit, and one label.

Root cause: The calculator credited `min(entitlement, charged - prior)`. Clause 9.1 ("a credit never exceeds the amount charged less any credit already issued") is a ceiling, and the code treated the ceiling as the whole netting rule. On C34, line L1 was overcharged R30.00 with R10.00 already credited, and the ceiling of R90.00 never bit, so the customer would get R30.00 on top of the R10.00. That credits the same money twice, which the plan forbids.

The disagreement also exposed a wrong label. C17 was worked as `min(20000, 30000-25000) = 5000`, the same mistake made by hand. A customer entitled to R200.00 who has already received R250.00 is owed nothing further.

Fix: `credit = max(0, entitlement - prior)` per line. C17 corrected to 0, with a comment pointing here.

Before / after: 34 of 37 worked cases passing, then all 37. (Worked cases only. Not a measure of model performance.)

Rejected fixes: Changing C29 and C34 to match the calculator, because the arithmetic on paper was right for those two. Treating C17 as a calculator bug, because keeping 5000 would have required the double-credit behaviour.

Regression test: C17, C29 and C34 stay in the worked set. The property test bounds each line's credit by `charged - prior`, and it passed during this bug, so it can't catch this class of error. Only independent worked amounts did.

What it taught me: A ceiling and a netting rule look alike until a partial prior credit separates them. The property test passed throughout, which is the argument for independent gold.

## F-002: A month in the text was read as the disputed period

Found: 2026-11-03, the first oracle run (run `1e8025ba`, not committed), on G-0007, G-0008 and G-0009.

Symptom: The oracle answered `credit_due` with the right rule and got `verify_failed`: `['IL-B000043', 'IL-B000044'] are not in evidence set`. Credit came out as R0 against R598.00, R199.00 and R2,320.00 expected.

Layer: record retrieval.

Root cause: Intake took "I cancelled in March" to mean the disputed period was March, so the playbook fetched only March's invoice. The charges in dispute were in April and May, so the correct lines were never in evidence. A real model would have had no way to see them and might well have said the charge stood.

Fix: Cancellation evidence now always runs through the latest billed month, starting from the named month. The rule and clause version still come from the start of that window.

Before / after: Oracle decision accuracy on the 40 draft cases went from 92.5% (run `1e8025ba`) to 100% (run `f4513836`), together with F-003. Oracle runs test plumbing and say nothing about model quality.

Rejected fixes: Always using the full lookback, because that moves the start of the period back past the 2026 terms and selects the wrong edition for G-0012. Asking the model for the period, because the model would then be choosing the scope of the money.

Regression test: G-0007, G-0008 and G-0009 stay in the golden set.

What it taught me: Running gold answers through the graph finds plumbing bugs for free, before a paid run could blame the model for them.

## F-003: The legitimate heavy caller looked like an auto-dialler

Found: 2026-11-03, the same oracle run, on G-0027, G-0028 and G-0029.

Symptom: Three bill-shock cases went to the fraud queue on `sequential_numbers`. False escalation was 3 of 35 non-fraud cases.

Layer: harness (a scenario bug). The screen behaved as designed.

Root cause: The scenario helper gave each of 30 calls the next number in a range. That is the exact pattern the signal looks for, so the "legitimate" customer wasn't.

Fix: The helper now rotates among three contacts. The screen's thresholds didn't change.

Rejected fixes: Raising the sequential-run threshold, which would have tuned the screen to its own generator to hide a data bug.

Regression test: The three cases stay in the golden set, tagged as near twins.

What it taught me: A false positive can be a bad test. Find out which it is before changing the detector.

## F-004: Fixed-size chunks "won" by returning the whole document

Found: 2026-11-03, the first BM25 retrieval run on 30 draft queries.

Symptom: Strategy A (fixed 512 tokens) scored recall@5 of 1.0 with a CI of [1.0, 1.0], and nDCG@10 of 1.045. An nDCG above 1 is impossible, which is what prompted the check.

Layer: harness.

Root cause: Two separate bugs. The authored T&Cs are shorter than 512 tokens, so A's single chunk spans the whole document and overlaps every gold clause. The metric rewarded this because nothing measured how much text came back. Separately, nDCG counted several gold clauses found in one chunk as if each sat at rank 1.

Fix: nDCG spreads tied gold clauses over successive ranks. A new `context_tokens@5` metric is reported beside recall.

Rejected fixes: Reporting A as the winner, which is what the raw table said. Dropping A from the table, because it's the plan's baseline and the plan expects it to be compared.

Regression test: `tests/unit/test_retrieval_metrics.py`.

What it taught me: A perfect score is a reason to check the metric. It also shows these authored documents are too short to test fixed-size chunking at all, so the comparison needs the real documents the plan calls for.

## F-005: The catalog guard couldn't see the table it exists to catch

Found: 2026-11-03, the first run of `tests/isolation/mutation_check.py`.

Symptom: 6 of 7 mutations were caught. `new table with tenant_id and no RLS` survived, with the isolation suite passing (pytest exit 0).

Layer: isolation (the test, not the policies).

Root cause: The guard listed tenant tables through `information_schema.columns`, which only shows tables the current role has privileges on. The guard connects as the admin role, and the mutated table was granted only to the app role, so the guard never saw it. A careless migration could have shipped exactly that: a tenant table readable by the app, with no RLS, and a green suite.

Fix: The guard now reads `pg_class` and `pg_attribute`, which aren't filtered by privilege.

Before / after: 6 of 7 mutations caught, then 7 of 7.

Rejected fixes: Granting the admin role access in the mutation so the old query saw the table. That would have made the check pass while leaving the blind spot.

Regression test: The mutation stays in `mutation_check.py`, which runs in CI.

What it taught me: A guard that had never failed was wrong in the one case it was written for. The plan's line about tests that have never failed turned out to be literal.

## F-006: Footnotes were parsed as clauses in a real gazette

Found: 2026-11-04, `parse_check` on Government Gazette 53991, the first real document through the parser.

Symptom: The clause sample included lines like "17 Rain Initial Submission 2024 Page 2 Para 1.4". Numbered footnotes looked just like numbered clauses.

Layer: ingestion.

Root cause: The splitter treated any line starting with a number as a new clause. The authored T&Cs have no footnotes, so every authored-document test passed.

Fix: A footnote pattern in `ingest/parse.py`, checked before splitting.

Rejected fixes: Requiring a dotted clause number, which would drop top-level clauses such as "6 Short title and Commencement".

Regression test: `test_footnotes_are_not_clauses`.

What it taught me: This is the plan's self-authored-world threat in miniature. My own documents could never have produced this bug, and the first real one did on its first read.

## F-007: The number audit rejected numbers the prompt had supplied

Found: 2026-11-04, the first full dev run on `claude-opus-5-5` (run `616b6863`, 100 draft cases, $4.42).

Symptom: 38 of 100 cases needed a repair call, which is 38 extra paid calls. G-0009 still failed after repair, with `rationale states numbers not found in tool output: ['20']`, so a R2,320.00 credit went uncomputed. It went to human review, as designed.

Layer: citation (`verify`).

Root cause: The audit allowed numbers from tool output and clauses only. The "20" was the day in `2026-06-20`, the dispute date, which the prompt gave the model but no tool returned. The customer's own figures and rule windows were rejected in the same way.

Fix: `verify_decision` takes the prompt's context: dispute date, period, rule windows and dispute text.

Rejected fixes: Dropping the audit, because a made-up number must still fail, and a test now checks that. Telling the model not to mention dates, which treats the symptom and makes rationales worse.

Regression test: `test_number_audit_accepts_what_the_prompt_supplied` and `test_number_audit_still_rejects_a_number_from_nowhere`.

Before / after: Repairs went from 38 in 100 cases to 4 in 41 (a partial rerun, `47ae7841`, because the API credit ran out). G-0009 now gets its R2,320.00 credit. The rerun covers the first 41 cases only, so it isn't a like-for-like accuracy comparison.

What it taught me: This failure mode was invisible to the scripted oracle, whose rationale contains no numbers. Only a real model showed that the check meant to catch invented numbers was punishing accurate ones.

## F-008: Nine disagreements that are really about the labels, left unresolved

Found: 2026-11-04, the same run.

Symptom: G-0041 to G-0046 (consent granted mid-period): gold says `needs_review`, the model says `credit_due`. G-0006, G-0054 and G-0055 (duplicates out of window): gold says `charge_valid`, the model says `needs_review`.

Layer: label.

Root cause: Both readings are defensible. On partial consent, clause 3.1 needs an opt-in "active for the whole service period", which the model reasonably reads as failing, while clause 3.2 only reverses a charge "with no active opt-in". The calculator raises `AmbiguousFacts`, so no money moved and all six went to a person. On late duplicates, clause 2.2 refuses a credit but mentions a manual review, which the model chose.

Fix: None. The plan says never to use model output as gold, so these need the owner's ruling, or clearer T&Cs. The labels and clauses are unchanged.

What it taught me: The safety layers held even where the labels are arguable. Decision accuracy on these cases measures my drafting as much as the model.
