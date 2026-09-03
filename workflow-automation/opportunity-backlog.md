# Automation Opportunity Backlog (Step 4: Scoring & Ranking)

## Scoring model

Each opportunity is scored on a 1-5 scale across five dimensions:

- **Frequency** — how often the manual work happens (5 = many times/day).
- **Time** — minutes of human effort per occurrence, end to end (5 = long/multi-person).
- **Error cost** — impact when it goes wrong: missed margin, misbilling, ledger errors,
  relationship damage (5 = money/compliance risk).
- **Build effort** — how hard to build & integrate (5 = easy; *higher is better*).
- **Risk** — operational/financial/legal risk of the automation acting wrongly
  (5 = low risk; *higher is better*).

**Value = Frequency + Time + Error cost.** **Feasibility = Build effort + Risk.**
Priority is Value weighted by Feasibility; ties broken by dependency order and by
"quick win to build trust."

## Ranked table

| # | Opportunity | Freq | Time | Err | Build | Risk | Value | Feas. | Priority |
|---|-------------|:----:|:----:|:---:|:-----:|:----:|:-----:|:-----:|:--------:|
| 1 | Accessorial approval triage (detention/layover/assist/TONU) | 5 | 4 | 5 | 3 | 3 | **14** | 6 | **1 — Do first** |
| 2 | Accounting: ACH ingest, intercompany, GL-coded AP | 4 | 5 | 5 | 2 | 2 | **14** | 4 | 2 — Phase it |
| 3 | Rate-quote productionization (finish Command Center) | 5 | 3 | 4 | 4 | 3 | **12** | 7 | 3 |
| 4 | Load-status / check-call auto-relay | 5 | 3 | 3 | 3 | 4 | **11** | 7 | 4 |
| 5 | Document auto-filing (POD / rate-con / onboarding) | 5 | 2 | 2 | 4 | 5 | **9** | 9 | 5 — Quick win |
| 6 | Recurring report assembly (USPS monthly, account health) | 3 | 3 | 3 | 4 | 5 | **9** | 9 | 6 — Quick win |
| 7 | CRM hygiene / customer-domain matching | 4 | 2 | 3 | 3 | 4 | **9** | 7 | 7 — Enabler |

## The recommended sequence (and why it is not just "highest score first")

Opportunities **#1 and #2 tie on raw value (14)**, but they are very different bets:

- **#1 (accessorial approvals)** is high-value *and* buildable *and* moderate-risk. It is
  the clear lead. It also creates the first structured record of accessorial decisions,
  which is reportable margin data you don't have today.
- **#2 (accounting)** is equally valuable but the **hardest and riskiest** thing on the
  list — it moves money across two legal entities and writes to the ledger. It must be
  **phased**, starting read-only (ingest + reconciliation + a proposed-entries queue a
  human posts), never big-bang.

So the build order optimizes for **early, safe, trust-building wins in parallel with the
high-value lead**:

1. **Week 1-2 — Quick wins #5 and #6.** Document auto-filing and recurring reports.
   Low risk, low effort, immediately visible. These prove the program delivers and
   season the McLeod/SharePoint/CRM integrations you'll reuse everywhere else.
2. **Week 2-6 — Lead #1.** Accessorial approval triage, dry-run first (the reference
   engine in this repo), then behind a one-click human approval, then auto-approve under
   a policy threshold. Requires your written accessorial policy + McLeod access.
3. **Parallel — Enabler #7.** Customer-domain matching, because #1, #3, and #6 all need
   clean customer attribution.
4. **Week 4-8 — #3 and #4.** Finish the Command Center quote loop and convert tracked P44/
   MacroPoint events into structured status notifications (retire manual relay emails).
5. **Week 6+ — #2, phased.** Accounting, read-only reconciliation first, then a
   human-posted proposed-entries queue, then (only once trusted) scheduled posting for
   the most mechanical pieces (intercompany duplication, ProLease export).

## Estimated payback (order-of-magnitude, to validate with the intake survey)

These are deliberately rough and should be confirmed by asking the teams (step 2 of the
program). They exist to sequence work, not to promise numbers.

- **#1 Accessorial:** if ~150-300 requests/month at ~10 min of combined rep+manager time
  each, plus recovered/มmissed detention billing, this is the largest *fast* win.
- **#2 Accounting:** the largest *total* hour sink, but slowest to safely capture.
- **#5/#6 Quick wins:** small hour savings, outsized value as low-risk proof and as
  reusable integration plumbing.

## Dependencies at a glance

- #1, #3, #4, #6 all depend on **McLeod read access** (orders, stops, times, rates).
- #1 and #2 depend on a **written policy** (accessorial thresholds; GL coding rules).
- #3 and #6 depend on **#7** (customer-domain matching) for correct attribution.
- #2 depends on **bank + QuickBooks access** and finance sign-off, and on **database
  backups existing first** (currently an open urgent item).
