# Labelling guide

Write labels from the T&Cs and the records, before seeing any model output. **Never use model output as gold.**

## Provenance

Every golden case has a `provenance` field:

| Value | Meaning | May be quoted as a result? |
|---|---|---|
| `assistant_draft` | Drafted by an AI assistant from the T&Cs, to prove the harness works. | **No** |
| `hand_written` | Written or independently re-derived by the project owner. | Yes |

To promote a draft: re-derive its decision and credit by hand without looking at the draft's answer, record your result in the spreadsheet or YAML, resolve any difference, then change the field.

## Decisions

| Decision | Use it when |
|---|---|
| `credit_due` | The records show the charge is wrong under a clause, and a credit rule applies. |
| `charge_valid` | The records show the charge is correct, including when a real error has already been credited or is out of window. |
| `needs_review` | The records or clauses can't settle it, or fraud is suspected. |

Fraud cases are always `needs_review`, on the `fraud` route if the screen flags them. A case the screen misses but the customer denies is `needs_review` on the `human` route.

## Dispute type

Apply the first that fits, as in `data/taxonomy.yaml`: `suspected_fraud`, `cancellation`, `double_billing`, `roaming`, `out_of_bundle`, `international`, `other`. Label from the text only, because that's all the classifier sees.

## Urgency

| Level | Criteria |
|---|---|
| `routine` | A billing question with no time pressure. |
| `soon` | A payment date or small amount in the coming weeks. |
| `urgent` | Suspension threatened, a large amount, or a debit order about to run. |
| `critical` | Service already suspended, debt collection, or fraud still happening. |

## Citations

- `required_clauses`: a correct answer can't be justified without these.
- `acceptable_clauses`: relevant, and citing them isn't wrong.
- `required_records` and `acceptable_records`: symbolic keys from the scenario, expanded to IDs by the manifest. Record gold is known by construction.
- Find clauses by reading the T&Cs, not by looking at retriever output.

## Credit amounts

Work each amount in `data/golden/credit_workings.yaml` or a spreadsheet: inputs, arithmetic and rounding. **Never run the calculator first.** When the two disagree, investigate both. Either can be wrong, and those make good failure stories.

Conventions: amounts are VAT-inclusive integer cents; proration rounds half up per line; the rule version is the one in force at the start of the disputed period; the dispute window runs from the invoice's issue date.

## Quality

Re-label 30 cases at least a week later and report intra-rater agreement. Have someone from a telecom billing team review 20 hard cases if you can. Track minutes per case, and re-plan if it runs above the estimate.
