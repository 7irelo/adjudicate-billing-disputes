# Two-minute demo script

Needs `ANTHROPIC_API_KEY` for every beat except fraud. Run `docker compose up --build`, then open `http://localhost:8000/demo/`. Not recorded yet.

| Time | Beat | In the demo |
|---|---|---|
| 0:00 | Double-billing: credit due, citing clause and invoice lines. Open the calculator steps. | baobab / `duplicate_recurring`: "My monthly fee is on my May invoice twice." |
| 0:25 | "Charged after I cancelled": the charge stands because of the notice period, and goes to a human. | baobab / `charged_during_notice`: "I cancelled on 20 May but you still charged me for May." |
| 0:45 | Same dispute, another tenant, different outcome. | `cancellation_mid_month` under baobab, then karoo: "I cancelled on 10 April. Why did I pay the full fee for May?" |
| 1:00 | Ask for the other tenant's data, and fail. | karoo's account under baobab's key returns 404. |
| 1:05 | Fraud: CDR burst, fraud queue, no auto-credit. | baobab / `fraud_pbx_burst`: "I was billed R1,200 for international calls I never made in May." |
| 1:25 | A reviewer resolves one, and the graph resumes. | Review queue tab: refresh, paste the review ID, submit. |
| 1:35 | Langfuse trace: nodes, cost, latency. | Needs Langfuse keys. |
| 1:45 | A PR failing the eval gate. | Use a deliberate-regression branch. |
| 1:55 | Results table, end. | README, once real results exist. |
