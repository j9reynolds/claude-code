# Recurring Report Assembly (#6) — Framework & Spec

Auto-assemble the fixed-cadence reports staff build by hand today, delivered as a
draft-for-approval (customer-facing) or auto-sent/attached (internal). Low risk: reads
only, no money movement; the only outbound action is an email/attachment the sender reviews.

## Status

- **BUILT — USPS GEGW "Self Report" (the real one Justin files monthly).** Generator
  (`report_usps_self_report.py`, 10/10 tests) + production query
  (`report_usps_self_report.sql`). Reproduces the `.xlsb` template titled
  **"J.B. Hunt Transport GEGW Performance Overview"** exactly:
  **Lane | Load Count | OTP | OT Dispatch | OTD | Comments**, one row per lane + a TOTAL,
  each metric a single **%**. Built from the **J.B. Hunt raw-data extract** (see below), not
  McLeod. **Verified to reproduce a real filed report (May 2026) exactly — 27/27 lanes, 0
  differences.** This is an **internal/partner report to J.B. Hunt** (tender **0029H**), not
  a customer-facing narrative.
- **BUILT — generic monthly customer performance report.** Generator
  (`report_customer_monthly.py`) + query (`report_customer_monthly.sql`). Reusable for any
  customer that wants a volume/revenue/margin/on-time/accessorials summary. Keep it — it is
  a different artifact from the USPS Self Report.
- **Delivery GATED:** the send/attach step needs **Microsoft 365** (currently
  disconnected / intermittently needs re-authorization). Until then reports render to
  file/console; wiring the Outlook draft is a small add (inject an `outlook_create_draft`
  callable, same pattern as the POD reader).

## Pipeline — extract → generator → filled Overview (BUILT, dependency-free)

`usps_selfreport_pipeline.py` ties the three steps together with **only the Python standard
library** (no `openpyxl`/`pandas`; `.xlsx` is read and written as zipped XML), so it runs on
any Delta host with no installs:

1. **EXTRACT** — `extract_raw_rows(path)` reads the J.B. Hunt raw extract. From an `.xlsx` it
   scans every sheet, finds any carrying an `O/D PAIR` header (one combined tab, or per-lane
   tabs), and pulls the normalized per-load rows; it also accepts a `.csv`. Writes an audit
   CSV of exactly what it aggregated.
2. **GENERATE** — `report_usps_self_report.build_report()` (the verified aggregation).
3. **FILL** — `write_overview_xlsx()` writes a filled **Overview** `.xlsx` (Lane | Load Count
   | OTP | OT Dispatch | OTD | Comments, per-lane A–Z + TOTAL), percentages as real Excel `%`
   cells — ready for the owner to review and send.

```
python3 usps_selfreport_pipeline.py RAW.xlsx 2026-08 GEGW OUT_overview.xlsx
python3 usps_selfreport_pipeline.py RAW.csv  2026-05 RTH  OUT_overview.xlsx
```

**Verified end-to-end on real data:** run against May 2026's raw tab, the pipeline
(extract → generate → write `.xlsx` → read back) reproduces the filed Overview with **0
differences** across all 27 lanes. Tests: `test_usps_selfreport_pipeline.py` (6) +
`test_report_usps_self_report.py` (10). Open the output once in Excel to confirm formatting
before the first real send.

## The USPS Self Report — what it actually is

| Attribute | Value |
|-----------|-------|
| File today | `0029H Self Report - Delta Group Logistics - <Month YYYY>.xlsb` (macro-enabled Excel) |
| Template title | "J.B. Hunt Transport GEGW Performance Overview" |
| Tender | 0029H |
| Audience | **Internal → J.B. Hunt.** Emailed by J.Reynolds to K.Cash, CC S.Ivankovic & P.Drzewiecki, ~1st of the month for the prior month |
| Home | SharePoint `…/USPS/` (and a working copy in K.Cash's OneDrive) |
| Columns | Lane · Load Count · **OTP** (On-Time Pickup/Arrival) · **OT Dispatch** · **OTD** (On-Time Delivery) · Comments |
| Metric cell | A single rounded **percentage** (e.g. `89%`) — %Y over the lane's loads |
| Lane format | `CITY, ST \| CITY, ST` (uppercase, pipe-separated), sorted A–Z; plus a TOTAL |

### Data source — J.B. Hunt extract, NOT McLeod (confirmed by reading a filed report)

Reading a real filed report (May 2026, opened as `.xlsx`) settled the method. The workbook
has three sheets: an **Overview Summary By Lane** (the report body), an Overview by Trip,
and a **"… Raw Data With Reason Codes"** tab that is a **J.B. Hunt data extract** — one row
per load with JBH's Contract ID (`0029H`), SV Trip ID, Load ID, `O/D PAIR`, and JBH's
scheduled/planned vs actual times, from which three flags are set: `ON TIME Arrival Y/N`,
`Dispatch on time Y/N`, `ON TIME DELIVERY y/n` (or `Order is VOID`), plus up to three reason
codes. The Overview tab just aggregates those flags per lane. **So the authoritative on-time
numbers are JBH's, and the generator consumes the raw extract — it does not recompute on-time
from McLeod.**

Exact aggregation the generator reproduces (matches the workbook's `COUNTIF/COUNTIFS`):

- **Load Count** = every raw row for the lane, INCLUDING `Order is VOID` rows.
- **OTP% / OT Dispatch% / OTD%** = `count(flag = "Y") / Load Count`, one rounded percentage
  per cell (the report shows `89%`, not counts). A VOID row is in the denominator but never a
  "Y", so it lowers the lane's %, exactly as the sheet does.
- **TOTAL row %** = the **unweighted mean of the per-lane percentages** (full precision) —
  the workbook's total is an average of lanes, not load-weighted. (`overall_load_weighted`
  is also returned for cross-reference, as is JBH's void-excluded headline.)

**Verified: the generator reproduces the filed May 2026 Overview exactly — 27/27 lanes, 0
differences — from that month's raw-data tab.** McLeod is only an optional independent
cross-check (`report_usps_self_report.sql`): map JBH Load ID → McLeod order and compare
Delta's own times to JBH's to flag disagreements.

### `.xlsb` read limitation (recorded)

Graph's file-conversion read (`read_resource`) **cannot open `.xlsb`** — it returns
`VALIDATION_ERROR: MIME type 'application/vnd.ms-excel.sheet.binary.macroenabled.12' is not
allowed` (the allow-list has `.xls` and `.xlsx`, not the binary macro format). **Re-save the
`.xlsb` as `.xlsx` once** and Graph reads every cell (that is how the May report was read).
Feed the generator the `Raw Data With Reason Codes` tab (as CSV) and it reproduces the
Overview.

### Output packaging

The generator fills the **data rows**. Because the deliverable is a macro `.xlsb` template,
final packaging stays a human/gated step and has two options:
1. **Assisted (now):** generator emits the rows (CSV / console / a `.xlsx` the owner pastes
   into the `.xlsb` template), owner reviews and sends. No fabricated numbers — every cell
   traces to a delivered load in DGLIQ.
2. **Full (later):** write the rows straight into an `.xlsx` copy of the template and attach
   it to an Outlook draft addressed to K.Cash — owner reviews and sends.

## Report catalog

| Report | Cadence | Audience | Delivery | Status |
|--------|---------|----------|----------|--------|
| **USPS GEGW Self Report (0029H)** | Monthly | **Internal → J.B. Hunt** (K.Cash; CC S.Ivankovic, P.Drzewiecki) | Fill `.xlsb`/`.xlsx` rows → owner reviews → send | **Built** |
| Monthly customer performance (any customer) | Monthly | Customer contact | Draft → sender approves → send | Built |
| Per-salesperson account health | Weekly | Sales reps | Internal auto-send | In flight internally (Command Center PR #12) — don't duplicate; reuse |
| Weekly carrier scorecard (on-time, tracking, rate-con compliance) | Weekly | Carrier-sales mgmt | Internal auto-send | Spec / future |
| Weekly ops-exceptions digest (rate-con missing, un-billed detention) | Weekly | Ops mgmt | Internal auto-send | Ties to the accessorial pilot; future |

## Architecture

```
 schedule (monthly/weekly cron) ─▶ Runner ─▶ report module (build_report)
                                       │            │ reads DGLIQ (report SQL) — on-network
                                       ▼            ▼
                                   render (rows / HTML / xlsx)
                                       │
                            ┌──────────┴───────────┐
                     customer-facing            internal / partner
                     M365 draft to sender       fill template + M365 draft to sender
                     (approve → send)           (approve → send)
```

- **Runner:** a small scheduled worker (same host family as the accessorial service, on the
  Delta network with DGLIQ access). One entry per catalog row: `(report_module, params,
  cadence, recipients, delivery_mode)` in config.
- **Report module:** pure `build_report(...) -> dict` + `render_*`. Add a report by adding a
  module; no framework change. `report_usps_self_report.py` and `report_customer_monthly.py`
  are the two references.
- **Data source:** the **DGLIQ `DGL_TMS` warehouse** (`tms.Order/Movement/Stop/Customer/
  Carrier`) — the normalized replica, ideal for aggregate reporting (the live connector's
  50-row search cap makes it unsuitable; DGLIQ is the right source).
- **Delivery:** `outlook_create_draft` (owner reviews, then sends) or `outlook_send_mail` /
  Slack (fully internal). Injected callable so the module stays testable.

## Guardrails

- **Read-only** data; the only write is an email/attachment. **Every send is a draft a human
  approves** — including the USPS Self Report, since it goes to a partner (J.B. Hunt).
- Recipients come from config (USPS: K.Cash, CC S.Ivankovic/P.Drzewiecki), never guessed.
- A report with anomalous numbers (e.g. a lane at 0% on time, volume 0, a MoM swing beyond a
  threshold) is held for review instead of auto-packaged.
- No fabricated cells — every value traces to a delivered load in DGLIQ.
- Every send is logged; a monthly index lists what went out.

## Rollout

1. **Render-only** (now): generate the report rows to file/console for the owner to eyeball.
   No email.
2. **Assisted packaging** (now, no M365 needed): emit an `.xlsx`/CSV the owner pastes into
   the `.xlsb` template and sends.
3. **Draft-on-approve** (after M365 re-auth): create an Outlook draft to the recipients with
   the filled attachment for the owner to review and send.

## To finish

- **Automate the raw-data extract.** The generator is proven against a real month; the open
  question is where the JBH raw data comes from each month (a J.B. Hunt portal export, an
  emailed file, or an EDI/API feed) so the runner can fetch it instead of a human pasting it.
- **Reason codes stay human.** The `Comments` / reason columns are judgement (e.g. "POSTAL",
  "Carrier", "Trailer issue") — the generator carries through whatever is supplied; it does
  not invent them.
- **Confirm the program label** per book (the sample read was `RTH`; the USPS/JBH GEGW book is
  `GEGW`) — passed as the third arg / `program=`.
- Re-save any `.xlsb` you want me to read as `.xlsx` (Graph cannot open `.xlsb`).
- Re-authorize the **Microsoft 365** connector (drafting/attaching).
- Stand the runner on the accessible host; decide the anomaly-hold thresholds.
- Optional: run `report_usps_self_report.sql` as a McLeod cross-check to flag loads where
  Delta's own times disagree with the JBH extract.
