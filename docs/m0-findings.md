# M0 findings: domain and corpus

Checked on 2026-11-03. A learner's reading, not legal advice.

| Question | Finding | Status |
|---|---|---|
| Which charter text is current? | The base is Notice 189 (GG 39898, 1 Apr 2016), amended by GG 41613 (2018), GG 42225 and GG 42266 (2019) and GG 48318 (2023). The amendment regulations in **GG 53991 (23 Jan 2026)** add to that list. | Read in the gazette itself. No single consolidated text was found. |
| What does the 2026 amendment do? | Replaces regulation 8A: depletion notices at 50%, 80% and 100%, an opt-in or opt-out for out-of-bundle charges, and no out-of-bundle charges without opt-in. It also covers roll-over and transfers, and repeals 8B. | Read directly. The plan's "billing issues" description was too broad. |
| When does it apply? | "Twelve (12) months after publication", so about **23 Jan 2027**. | Read directly. The exact date is my calculation. |
| Is the VAT rate still 15%? | Yes. The 2025 increases were reversed and Budget 2026 left the rate unchanged. | Secondary sources only. Re-check against SARS. |
| Does CPA s14 apply to businesses? | Section 14(1) excludes transactions between juristic persons. That supports different notice terms for Fynbos Voice. | Secondary sources quoting the Act. Read the Act itself before quoting it. |
| Are the tenant names free? | No South African operator named Baobab Mobile, Karoo Connect or Fynbos Voice turned up. **Bayobab** (MTN's wholesale carrier) is a close spelling of Baobab. | One web search, not a CIPC or ICASA register check. |
| Are real T&Cs available for the benchmark? | Still only Cell C's charter copy and the gazette. No official Vodacom, MTN or Telkom terms were found. | Open. The benchmark narrows, as the plan's M0 exit allows. |

## What this changes

- **The regulation is effective-dated.** Regulation 8A is stored with `effective_from: 2027-01-23`, so it isn't retrieved for any dispute in the 2025-2026 synthetic world. Disputes in this world are decided on tenant terms. The "regulation vs T&Cs" family of golden cases needs either a 2027 world or a verified copy of the rule in force before 2027.
- **The Bayobab near-collision is worth a decision.** "Baobab Mobile" is fictional and the UI says so, but you may prefer a more distant name before publishing.

## Go/no-go

**Go.** The regulatory text is pinned, and the sample cases in `data/golden/dev.jsonl` are decidable from the tenant terms. The narrower real-document benchmark must be stated as a limitation.
