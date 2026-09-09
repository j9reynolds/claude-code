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
| `leakage-analysis.md` | **365-day loss analysis:** the real results (layover −$27.5k realized loss; eligibility-adjusted un-billed detention ~$531k; rate-con control gap on $435k paid), the method, and the export schema. Calculator: `reference-implementation/leakage_model.py`. |
| `deployment-spec.md` | **Production deployment spec:** how to take the accessorial engine live against McLeod — architecture, runtime/deps, McLeod access scopes, per-event flow, shadow→assisted→auto rollout, guardrails, and the go-live checklist. |
| `mcleod-extract/` | **Real-data extraction:** read-only SQL against McLeod LME (`DB02` / `LME_1720`) for stop in/out times, appointment windows, accessorial billing/pay, and the signed-rate-con upload time; a Python runner that emits the leakage-model CSV; and a connection/run guide. This session can't reach `DB02` (on-prem); run it on a DB-facing machine. |
| `reporting/` | **Recurring reports (#6):** the real **USPS "Self Report"** Justin files monthly to J.B. Hunt (tender 0029H). End-to-end **pipeline** `usps_selfreport_pipeline.py` (stdlib-only — reads/writes `.xlsx` as zipped XML, no installs): **extract** (`usps_selfreport_extract.sql`, Delta's real McLeod LME query, run in SSMS → CSV) → **generate** (`report_usps_self_report.py`) → **fill** a standalone **3-tab** `.xlsx` mirroring the filed workbook (Overview Summary By Lane · Overview Summary by Trip · Raw Data With Reason Codes) with live COUNTIF/COUNTIFS formulas, cached values, and formatting (shaded headers, borders, %-cells, column widths). Formatting matched to the template (Cambria; #B4C6E7 merged 16pt title; black header bar; #BFBFBF total; frozen Raw Data top row; Y/N columns centered with every "N" filled #FFCCCC/#FF0000 bold; document properties Title/Subject/Tags + Company set). Scheduled monthly via `run_monthly.ps1` (Invoke-Sqlcmd → CSV → pipeline → email + SharePoint drop). Verified end-to-end against the real May 2026 report: **27/27 lanes, 0 differences**. Tests 12+10. (The extract's "dispatch" metric is synthetic by design — rate-con time minus a random hour — so it's ~100% by construction; kept randomized per Justin.) Plus a reusable monthly customer-performance report (`report_customer_monthly.py`) and the framework spec. **Delivery is LIVE host-side** (Task Scheduler → classic Outlook COM email + OneDrive-synced SharePoint copy); the Claude M365 connector stays read-only. |
| `reference-implementation/` | A **safe, runnable** dry-run engine for the #1 opportunity: the full carrier **Rate Confirmation** (accessorials + deductions, eligibility gates, permission-gated manager override, **appointment-based detention**), the **leakage model**, and **`pod_reader.py`** (reads carrier check-in/out from the POD — the authoritative source, per-event). Sends nothing, moves no money, writes to no system of record. Pure, unit-tested (21/21 + 10/10). |
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
