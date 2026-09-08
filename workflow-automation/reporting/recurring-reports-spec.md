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
  each metric a single **%**. Built from **Delta's McLeod LME extract query**
  (`usps_selfreport_extract.sql`), self-reported to J.B. Hunt (see below). **Verified to
  reproduce a real filed report (May 2026) exactly — 27/27 lanes, 0 differences.** This is an **internal/partner report to J.B. Hunt** (tender **0029H**), not
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

1. **EXTRACT** — run `usps_selfreport_extract.sql` in SSMS (McLeod LME) and export the grid to
   CSV. `extract_raw_rows(path)` reads that CSV (or an `.xlsx` — it scans every sheet for an
   `O/D PAIR` header, one combined tab or per-lane tabs), normalizes the per-load rows
   (preferring the `Y/N` text columns over the numeric `*_Flag` columns), and writes an audit
   CSV of exactly what it aggregated.
2. **GENERATE** — `report_usps_self_report.build_report()` (the verified aggregation).
3. **FILL** — `build_workbook_xlsx()` writes a standalone **3-tab** `.xlsx` mirroring the filed
   workbook: **Overview Summary By Lane**, **Overview Summary by Trip** (TripID list per lane +
   Route/HCR), and **Raw Data With Reason Codes** (verbatim export). The two Overview tabs use
   live `COUNTIF/COUNTIFS/AVERAGE` formulas over the raw tab (cached values + `fullCalcOnLoad`)
   so they recompute if raw data is edited. **Formatting matched to the template** (Cambria):
   merged title row `#B4C6E7` 16pt black bold; black header bar with white bold text; `#BFBFBF`
   bold total row; thin black borders; `0%` lanes / `0.00%` totals. On **Raw Data**: top row
   frozen; the three Y/N columns centered; every `Y` cell filled `#FFCCCC` with `#FF0000` bold.
   Open once in Excel to confirm before sending.

### Monthly run (wired) — `run_monthly.ps1`

A PowerShell runner schedules the whole chain on a Delta host that can reach McLeod:
`Invoke-Sqlcmd` runs `usps_selfreport_extract.sql` against `DB02/LME_1720` (the SQL scopes to
last calendar month) → `Export-Csv` → `usps_selfreport_pipeline.py` → the styled workbook in
`output\`. **Filename is canonical** — always `0029H Self Report - Delta Group Logistics -
<Mon YYYY>.xlsx`, where only `<Mon YYYY>` changes to the data month (`report_filename()` in the
pipeline; pass a folder as the 4th arg and it auto-names). Schedule it for the 1st via Task
Scheduler:

```
schtasks /Create /TN "USPS Self Report" /SC MONTHLY /D 1 /ST 06:00 ^
  /TR "powershell -NoProfile -ExecutionPolicy Bypass -File C:\path\run_monthly.ps1"
```

Prereqs: `Import-Module SqlServer`, Python 3 on PATH, the three files co-located. The runner
aborts on zero rows and logs each step.

**Email delivery (step 4):** the runner emails the workbook to `-EmailTo` (default: J.Reynolds,
for review before forwarding to J.B. Hunt) **from the host** — Outlook desktop COM by default,
or an SMTP relay (`-MailMethod Smtp -SmtpServer …`), or off (`-MailMethod None`). This is NOT
the Claude M365 connector (a scheduled task can't use it); "once M365 is fixed" means the
host's Outlook/mail path works. The step is **guarded**: if mail isn't ready it logs a warning
and still leaves the file for manual send, so email begins automatically the first month it
works. Add `-EmailCc` only to send straight to the partner (unreviewed auto-send left off by
default).

**SharePoint drop (step 5):** the runner also copies the workbook into the SharePoint USPS
folder via `-SharePointDir`. Because the Claude M365 connector is read-only (`Files.ReadWrite.All`
is not granted) and a scheduled task can't use it anyway, this is a plain `Copy-Item` on the
host — point `-SharePointDir` at the USPS library's **locally-synced** path (OneDrive sync
client, e.g. `C:\Users\<you>\Delta Freight Systems\DeltaGroup - USPS Monthly Reporting`) or a
mapped/UNC path to it. It is **guarded** like email: if the folder is unset (blank) or
unreachable, it logs a warning and continues (the file still lands in `output\`).

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

### Data source — Delta's McLeod LME query, self-reported to J.B. Hunt

Confirmed from the actual extract query Ops runs (`usps_selfreport_extract.sql`). The raw
per-load rows are **generated by Delta from McLeod LME** (`[lme_1720].[dbo]`), not provided
by J.B. Hunt — hence "**Self** Report." (The `J.B. Hunt Transport / 002BT / 2024` rows in the
workbook's hidden tabs are the original template's sample data; Delta overwrites them with the
query output each month.) The query, filtered to customer `UNITMETN`, prior calendar month,
status `D`/`V`, `id NOT LIKE '%S%'`, emits one row per load with `O/D PAIR`
(`pu.city, ST | del.city, ST`), the scheduled/actual stop times, and the three flags:

- **`ON TIME Arrival Y/N` (OTP)** = `actual_arrival <= COALESCE(sched_arrive_late, sched_arrive_early)` on the pickup stop; void → `V`.
- **`ON TIME DELIVERY y/n` (OTD)** = same rule on the delivery stop; void → `V`.
- **`Dispatch on time Y/N`** = based on when the **rate confirmation** was sent
  (`order_post_hist`, `posted_type='C'`).

**Timestamps (current):** OTP and OTD are computed from the **real** McLeod `actual_arrival`
times (pickup and delivery stops), so they are reproducible and reconcile against McLeod.
`planned dispatch time` = when the **Rate Confirmation was created** (`order_post_hist`,
`posted_type='C'`). ⚠️ The **only** randomized column is **`actual dispatch time`** = that
rate-con time **minus a random 55–67 minutes** (`NEWID()`), so **Dispatch** is ~100% on-time by
construction and non-deterministic. If a real dispatch/departure timestamp becomes available,
point the Dispatch comparison at it to make it real too. See `usps_selfreport_extract.sql`.

Reason codes are added by hand after export. The Overview tab aggregates the `Y/N` flags per
lane; the generator consumes those flags (and tolerates the numeric `1/0/NULL` `*_Flag`
columns the query also emits, preferring the `Y/N` text columns).

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

- **Automate the extract run.** Source is confirmed: Ops runs `usps_selfreport_extract.sql` in
  SSMS against McLeod LME. To make it hands-off, schedule that query on the DB-facing host
  (SQL Agent job or `sqlcmd`) to drop a monthly CSV the runner picks up — no portal/EDI needed.
- **Fix the dispatch metric (data integrity).** The extract's "actual dispatch" is fabricated
  (rate-con time − random hour), so Dispatch is ≈100% by construction and non-deterministic. If
  a real dispatch/departure timestamp exists (e.g. `movement` actual departure), point the
  comparison at it; otherwise flag the metric as not-measured. This is a self-reported number to
  a partner — worth making defensible.
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
