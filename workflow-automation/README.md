# Workflow & Automation Discovery Program

This directory holds the output of a process-level discovery pass across Delta Group
Logistics / Delta Freight Systems (DGL/DFS) operational systems, plus scored automation
opportunities and implementation-ready build specs.

## What this is

A repeatable program for finding automation candidates by analyzing the **process
artifacts** your work already leaves behind (tickets, records, emails, documents,
CRM activity) rather than by watching individual employees. Process-level analysis
is lower-risk, legally cleaner, and produces better candidates: the friction you most
want to remove lives in the workarounds people stop using the moment they feel watched.

## Contents

| File | Purpose |
|------|---------|
| `discovery-findings.md` | What the systems mining surfaced (step 3): the recurring, structured, rules-based patterns worth automating, with the evidence behind each. |
| `opportunity-backlog.md` | Scored & ranked backlog (step 4): frequency x time x error-cost vs. build-effort & risk, with a recommended sequence. |
| `build-plan.md` | Implementation specs for the winners (step 5): trigger, data sources, logic, guardrails, access required, and rollout — one section per opportunity. |
| `customer-accessorial-rate-sheet.md` | **Pilot analysis:** should customer accessorials be marked up, and a draft standard rate sheet — built from the Command Center's existing bill/pay guidance and the carrier Rate Confirmation, pending McLeod AR actuals. |
| `leakage-analysis.md` | **365-day loss analysis:** why the real figure needs a McLeod export (not connected), the three loss buckets, the exact export schema required, and an illustrative sample run. The calculator is `reference-implementation/leakage_model.py` (now with a `--csv` runner that reads the McLeod export). |
| `mcleod-extract/` | **Real-data extraction:** read-only SQL against McLeod LME (`DB02` / `LME_1720`) for stop in/out times, appointment windows, accessorial billing/pay, and the signed-rate-con upload time; a Python runner that emits the leakage-model CSV; and a connection/run guide. This session can't reach `DB02` (on-prem); run it on a DB-facing machine. |
| `reference-implementation/` | A **safe, runnable** dry-run engine for the #1 opportunity, now encoding the full carrier **Rate Confirmation** (all auto-applied accessorials + deductions, eligibility gates, and a permission-gated manager override). Sends nothing, moves no money, writes to no system of record. Pure, unit-tested logic (20/20 passing). |
| `employee-announcement.md` | A ready-to-send heads-up to staff. Recommended before any automation goes live. |

## Systems analyzed in this pass

Read-only, aggregate analysis of: Microsoft 365 (Outlook, Teams, SharePoint),
HubSpot CRM, Linear, and the connectors' surfaced metadata. McLeod (TMS + accounting
module), the bank portals (Chase/Huntington), ProLease, Project44, MacroPoint, and the load
boards (Truckstop/DAT/MODE) were **referenced from their process artifacts** but were
not directly connected in this pass — connecting them is part of the access ask in
`build-plan.md`.

## Guardrails this program follows

1. **No covert employee monitoring.** No screen watching, keystroke tracking, or
   per-person productivity profiling. Analysis is at the process level.
2. **Tell staff first.** See `employee-announcement.md`. This is both the ethical
   baseline and what makes the intake survey (step 2 of the program) work.
3. **Human-gated money and external comms.** Any automation that pays a carrier,
   approves an accessorial, posts to the ledger, or emails a customer ships with a
   human approval step and a dry-run mode first.
4. **PII minimization.** These committed files describe roles and processes, not
   individuals, and omit account numbers and internal financial detail.

## Status of step 5 (build)

| Winner | Status in this repo |
|--------|--------------------|
| #1 Accessorial approval triage | **PILOT — engine built & expanded** to the full Rate Confirmation, with permission-gated override (dry-run, no side effects). Customer markup analysis + draft rate sheet delivered. Live wiring gated on McLeod access + the role/permission map. |
| #2 Accounting / ACH / intercompany | Build spec only. Gated on bank + McLeod accounting-module access and finance sign-off (touches money across two legal entities). |
| #3 Rate-quote productionization | Build spec only. Largely already built internally ("DGL Command Center"); spec covers closing the loop. |
| #4 Load-status auto-relay | Build spec only. Leverages P44/MacroPoint already in place. |
| #5 Document auto-filing (POD / rate-con) | Build spec only. Low risk; good early win. |
| #6 Recurring report assembly | Build spec only. Low risk; good early win. |
| #7 CRM hygiene / customer-domain matching | Build spec only. Supports #1 and #3. |

Nothing in this directory sends email, moves money, or writes to a system of record.
