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
  each metric as On-Time / measurable-Total / %. This is an **internal/partner report to
  J.B. Hunt** (tender **0029H**), not a customer-facing narrative.
- **BUILT — generic monthly customer performance report.** Generator
  (`report_customer_monthly.py`) + query (`report_customer_monthly.sql`). Reusable for any
  customer that wants a volume/revenue/margin/on-time/accessorials summary. Keep it — it is
  a different artifact from the USPS Self Report.
- **Delivery GATED:** the send/attach step needs **Microsoft 365** (currently
  disconnected / intermittently needs re-authorization). Until then reports render to
  file/console; wiring the Outlook draft is a small add (inject an `outlook_create_draft`
  callable, same pattern as the POD reader).

## The USPS Self Report — what it actually is

| Attribute | Value |
|-----------|-------|
| File today | `0029H Self Report - Delta Group Logistics - <Month YYYY>.xlsb` (macro-enabled Excel) |
| Template title | "J.B. Hunt Transport GEGW Performance Overview" |
| Tender | 0029H |
| Audience | **Internal → J.B. Hunt.** Emailed by J.Reynolds to K.Cash, CC S.Ivankovic & P.Drzewiecki, ~1st of the month for the prior month |
| Home | SharePoint `…/USPS/` (and a working copy in K.Cash's OneDrive) |
| Columns | Lane · Load Count · **OTP** (On-Time Pickup) · **OT Dispatch** (On-Time Dispatch) · **OTD** (On-Time Delivery) · Comments |
| Sub-columns | Each of OTP / OT Dispatch / OTD is On Time · Total · % |
| Rows | One per lane, e.g. `Philadelphia, PA - Phoenix, AZ (89)`; plus a TOTAL |

### Metric definitions the generator uses (confirm with the account owner)

- **OTP** — carrier **arrived** at the origin by the scheduled time (PU `ActualArrival <= sched`).
- **OT Dispatch** — carrier **departed** the origin by the scheduled time (PU `ActualDeparture <= sched`).
- **OTD** — carrier **arrived** at the destination by the scheduled time (SO `ActualArrival <= sched`).
- **Scheduled reference (`sched`)** = `COALESCE(OrigSchedLate, SchedArriveLate)` — the
  ORIGINAL tender commitment when present, else the current appointment. A reschedule must
  not erase a miss.
- **Measurable Total** for a metric = loads on the lane that have BOTH the actual and the
  scheduled timestamp; a load missing either counts in Load Count but not in that metric's
  %, so missing data never inflates or deflates the score.

### `.xlsb` read limitation (recorded)

Graph's file-conversion read (`read_resource`) **cannot open `.xlsb`** — it returns
`VALIDATION_ERROR: MIME type 'application/vnd.ms-excel.sheet.binary.macroenabled.12' is not
allowed` (the allow-list has `.xls` and `.xlsx`, not the binary macro format). The exact
column/row structure above was recovered from the **SharePoint search index** (which
extracts text from the workbook). To verify exact cell layout, formulas, or hidden columns,
**re-save the template once as `.xlsx`** and Graph reads it directly; the generator does not
need this — it produces the data rows from DGLIQ.

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

- **CONFIRM the USPS McLeod customer code** (`report_usps_self_report.sql` assumes
  `UNITMETN`; the GEGW book brokered under J.B. Hunt tender 0029H may sit under a J.B. Hunt
  customer code — verify in McLeod).
- **CONFIRM the lane-number source** (the "(89)" id) — where McLeod stores it (a stop/order
  RefNumber or user field), so the SQL selects the right column; today it maps `pu.RefNumber`.
- **CONFIRM the OT Dispatch scheduled reference** if USPS defines a dispatch cutoff distinct
  from the pickup appointment.
- Optionally re-save the `.xlsb` template as `.xlsx` once so Graph can read exact cell
  layout/formulas (not required for the data — nice for byte-exact formatting).
- Re-authorize the **Microsoft 365** connector (drafting/attaching).
- Stand the runner on the DGLIQ-accessible host (shared with the accessorial service).
- Decide the anomaly-hold thresholds.
