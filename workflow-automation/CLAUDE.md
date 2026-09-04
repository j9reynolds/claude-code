# Project Memory — Workflow & Automation Program (Delta Group Logistics / DFS)

Directory-scoped memory for the `workflow-automation/` initiative. Read this first when
resuming. It captures what this is, what's built, the hard constraints discovered, and the
open decisions. Everything here is process-level — no PII, no account numbers, no secrets.

## What this is

A program to identify and build automations for **Delta Group Logistics (DGL) / Delta
Freight Systems (DFS)**, a freight brokerage + carrier group (DGL brokerage; DFS
asset/carrier; DFS Equipment Leasing for trailers). Owner/PM: Justin Reynolds.

**System of record:** McLeod (LoadMaster Enterprise / LME) is both the **TMS and the
accounting module** (AR/AP/GL). *Not QuickBooks* per the PM — but the mined back-office SOP
still references "DFS QB"/"DFS EL QB" for the DFS/DFS-EL entities. Treat McLeod as
authoritative; the QuickBooks reference is a **discrepancy to confirm** (legacy, or
entity-specific), not settled.

**Surrounding systems:** Microsoft 365 (Outlook/Teams/SharePoint — email is the real
operational channel), HubSpot CRM, Linear, Chase/Huntington banks, ProLease, Project44 +
MacroPoint (tracking), load boards (Truckstop/DAT/MODE). An internal dev effort is already
live: the **"DGL Command Center"** (in-house TMS, AI quoting, account-health, tracking).

## Guardrails (do not violate)

1. **Process-level only.** No covert employee monitoring; analyze process artifacts, not
   individuals. Committed files name roles, not people; no account numbers; no secrets.
2. **Human-gated money & external comms.** Anything that pays a carrier, bills a customer,
   posts to the ledger, or emails a customer/carrier ships dry-run → human-approved →
   auto-under-policy. Never big-bang.
3. **Never fabricate figures.** No invented dollar amounts (e.g. "Delta lost $X"). Real
   numbers require real data from McLeod.

## McLeod access — via the `dgl-mcp` connector (READ tools; live as of 2026-09-04)

The PM added a first-party MCP connector **`dgl-mcp`** that reaches McLeod (LME_1720). Read
tools available: `search_orders` (cap 50, newest by ordered_date), `get_order`
(header + stops, real appointment + actual times, `otherchargetotal` LUMP), `get_movement`
(carrier, `override_pay_amt`, `rate_confirmation_status`/`_sent_date`), `get_customer`,
`get_carrier` (payee⋈drs_payee), `get_image` (BOL/POD from DocumentPower), `list_comments`,
`resolve_identifier`. WRITE tool `create_comment` exists — **do not call without explicit
authorization.**

Connector limits (why it's not a bulk analytics source):
- `search_orders` capped at 50 rows → cannot page the whole ~tens-of-thousands-of-loads book.
- `get_order` returns `otherchargetotal` as a **lump** (accessorials+fuel mixed), not
  itemized line items — customer-accessorial billing isn't separable from the connector alone.

Confirmed real schema (from connector responses): `orders`(id, customer_id, status
[D=delivered/A/V/P], on_hold, curr_movement_id, freight_charge, otherchargetotal,
total_charge, ordered_date, bill_date, equipment_type_id); `stop`(movement_id, stop_type
[PU/SO], sched_arrive_early/late, actual_arrival/actual_departure, timezone_id — stop-local
wall clock, no tz marker); `movement`(id, order_id, carrier_id, override_pay_amt, target_pay,
max_buy, rate_confirmation_status, rate_confirmation_sent_date); carrier = `payee` ⋈
`drs_payee`.

Direct SQL to DB02 and the McLeod REST API remain unreachable from the sandbox (no route;
egress proxy 403s non-allowlisted hosts) — the connector is the only in-session path.

## Leakage number — path chosen: WHOLE-BOOK BULK EXPORT

DONE (2026-09-04): PM ran the SQL and provided the four CSVs; analysis computed via
`mcleod-extract/analyze_leakage.py` over 26,733 delivered loads. RESULTS (365 days):
- Layover billed BELOW cost = realized loss −$27,545/yr (fix on customer rate sheet).
- Un-billed detention: 8,574 of 9,863 long-dwell loads carry no detention charge →
  ≤ $503k indicative upper bound (~$125k–$200k realistic at 25–40% capture).
- Rate-con NOT recorded on 84.7% (22,633) of loads → risk on $435k carrier accessorials paid.
- Realized accessorial margin overall +$162.7k (billed $597.7k vs paid $435.5k).
Findings artifact (private): https://claude.ai/code/artifact/f06488f3-55d1-47fb-88af-2ad88944b0c0
Full results table in `leakage-analysis.md`; customer-level detail kept OUT of git.

Validation already seen on real order 0197341: actual pickup dwell 16h20m (detention that
caps at $150) vs the rep's hand-typed email times; `rate_confirmation_status`/`_sent_date`
NULL → the contract's "signed rate con returned in real time" gate isn't being recorded (a
control-gap / leakage risk in its own right).

## Pilot status — #1 Accessorial engine (BUILT, dry-run)

Encodes the **full signed carrier Rate Confirmation** (`reference-implementation/`):
- **Accessorials → carrier:** detention (2h free, $35/h solo · $50/h team, **caps at the
  layover rate**), layover ($150/$250 team), driver-assist (pre-approved only, never auto),
  TONU ($150/$250 team).
- **Deductions ← carrier (auto on trigger):** MacroPoint tracking failure & late-service &
  direct-run (greater of $500 or 20% linehaul), missed check-calls ($50 ea), late POD
  ($150) + continued ($250/day), missing signed rate con ($50), exclusive-use (100% rate).
- **Eligibility gates:** carrier-fault → rejected; missing signed facility proof / revised
  signed rate con → needs review; detention/layover/TONU **held until customer pays**.
- **Permission-gated override:** only MANAGER/ADMIN/SUPER_ADMIN may waive an auto-applied
  charge, with a required audit note; USER is refused.
- Pure dry-run, no side effects. **Tests: 20/20** (`test_accessorial_rules.py`).

## Customer markup + rate sheet (`customer-accessorial-rate-sheet.md`)

The Command Center already carries "company guidance" bill/pay pairs (real internal
precedent): **Detention 1.50× · Stopoff 1.48× · TONU 1.39× · Layover 1.33× · Lumper 0.97×
(a flagged LOSS)**. Recommendation: publish a standard sheet at **carrier cost +40%** with
per-type minimums; **fix lumper** (cost + handling, never below cost); split time-based
(percentage) from third-party fees (cost + fixed fee). Numbers are provisional pending
**McLeod AR actuals**.

## Leakage model (`reference-implementation/leakage_model.py`, `leakage-analysis.md`)

Answers "how much did Delta lose over 365 days to un-billed / un-enforced items." Three
buckets: **(1) customer under-billing, (2) carrier deduction under-enforcement, (3) carrier
overpayment (ineligible paid).** Reuses the engine + the customer rate sheet. `--csv` runs
a real McLeod export; unknown judgment fields default to compliant so the result is a
**defensible floor**. **Tests: 10/10.** No real figure computed yet (needs McLeod).

## McLeod extractors (`mcleod-extract/`)

Two read-only paths, both emit the identical CSV `leakage_model.py --csv` consumes:
- **SQL:** `mcleod_leakage_extract.sql` + `mcleod_extract.py` (pyodbc/pymssql; `--discover`
  then `--run`). Standard LME schema; every site-specific name marked `-- CONFIRM`.
- **API:** `mcleod_api_extract.py` (McLeod REST; reads `MCLEOD_API_BASE`/`MCLEOD_API_TOKEN`
  from env — never committed; `--probe` tests reach+auth, `--run` writes CSV).
- **Image type numbers:** temporary POD = **4** (confirmed). **Signed Rate Confirmation # =
  TODO** — the one value the rate-con-timing query needs; get it from the SharePoint
  Accounting-folder doc-type list or McLeod Image Setup.

## Where things live

- **PR #3** (draft): branch `claude/workflow-automation-identification-3shwij` on
  `j9reynolds/claude-code`. CI = Semgrep, green. Subscribed for events.
- **Review artifact:** https://claude.ai/code/artifact/acd9c1c8-553c-4d80-8062-7667f1a63e38
  (note: artifact wake-subscriptions do NOT register in this session; re-read manually).
- File map: `README.md` (index), `discovery-findings.md` (7 patterns), `opportunity-backlog.md`
  (scored/ranked), `build-plan.md` (per-winner specs), `customer-accessorial-rate-sheet.md`,
  `leakage-analysis.md`, `employee-announcement.md`, `reference-implementation/`, `mcleod-extract/`.

## The ranked backlog (build order)

1. **Accessorial approval triage** — the pilot (engine built). 2. Accounting ACH/intercompany
(phase, finance sign-off). 3. Rate-quote productionization (Command Center ~80% done).
4. Load-status auto-relay (P44/MacroPoint). 5. Document auto-filing (quick win). 6. Recurring
report assembly (quick win). 7. CRM hygiene / customer-domain matching (enabler). **McLeod
read access is the keystone — it unblocks #1, #3, #4, #5, #6.**

## Open decisions / next steps (waiting on the user)

1. **Get McLeod data:** run an extractor on-network → send `loads_365d.csv`, OR add McLeod
   as a first-party connector. Then compute the real leakage number + finalize the rate sheet.
2. **Provide the signed-Rate-Confirmation image type number** (counterpart to temp POD = 4).
3. **Provide the role/permission map** (who is MANAGER/ADMIN/SUPER_ADMIN) to wire the override.
4. **Go/no-go on the staff announcement** (`employee-announcement.md`) before anything live.
5. After #1: start quick wins #5/#6 and enabler #7.

## SQL conventions for McLeod (LME_1720) — ALWAYS follow

- **Fully-qualify every table** as `[lme_1720].[dbo].[<table>]` in every query (SSMS
  sessions are not defaulted to the lme_1720 DB context, so bare `dbo.orders` fails).
  Bracket each identifier. This applies to all McLeod SQL going forward, no exceptions.
- **Confirmed schema corrections (use these, not the connector's aliased names):**
  - The carrier on a movement is `movement.override_payee_id` → join `[lme_1720].[dbo].[payee]`
    on `payee.id = mv.override_payee_id`. (The connector surfaces this as `carrier_id`, but
    the real base column is `override_payee_id` — do NOT use `mv.carrier_id` in SQL.)
  - orders → movement: `mv.id = o.curr_movement_id`. orders → customer:
    `cust.id = o.customer_id`. stops: `s.movement_id = o.curr_movement_id`.
- **Confirmed charge tables (from INFORMATION_SCHEMA discovery, 2026-09-04):**
  - CUSTOMER accessorial charges: `other_charge` (order_id, charge_id, descr, amount,
    bill_type, stop_id). Itemized split behind orders.otherchargetotal. (Confirmed by PM.)
  - CARRIER accessorial pay: `driver_extra_pay` (order_id, movement_id, payee_id,
    deduct_code_id [non-null = deduction], descr, short_desc, amount, units — NO
    charge_id, classify by descr). (Confirmed by PM.) `broke_drs_ex_pay` is EMPTY — do
    not use it.
  - Charge-code dictionary: `charge_code` (id, descr, is_fuel_surcharge, glid) — use
    to classify codes and exclude fuel from accessorials.
- The canonical Query A/B/C/D lives in `mcleod-extract/mcleod_leakage_extract.sql`.

## Working conventions

- Commit attribution currently: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
  + `Claude-Session:` trailer (per session directive — may change; follow the latest).
- All PRs draft; auto-watch after creating; keep CI green. Push only validated changes.
- Do not commit secrets/credentials or McLeod tokens. Keep committed docs process-level.
