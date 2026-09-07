# Recurring Report Assembly (#6) — Framework & Spec

Auto-assemble the fixed-cadence reports staff build by hand today, delivered as a
draft-for-approval (customer-facing) or auto-sent (internal). Low risk: reads only, no
money movement; the only outbound action is an email the sender reviews.

## Status

- **BUILT:** the **Monthly Customer Performance Report** — generator
  (`report_customer_monthly.py`) + production query (`report_customer_monthly.sql`).
  Sample: USPS, Aug 2026 (177 loads, $622.7k revenue, 6.0% margin, 100% on-time, 52
  carriers) — see the findings artifact.
- **Delivery GATED:** email drafting needs **Microsoft 365**, which is currently
  disconnected / needs re-authorization. Until then reports render to file/artifact; wiring
  the M365 draft step is a small add once auth is restored (mirrors the POD-reader injection
  pattern — inject an `outlook_create_draft` callable).

## Report catalog

| Report | Cadence | Audience | Delivery | Status |
|--------|---------|----------|----------|--------|
| Monthly customer performance (USPS, and any customer) | Monthly | Customer contact | Draft → sender approves → send | Built |
| Per-salesperson account health | Weekly | Sales reps | Internal auto-send | In flight internally (Command Center PR #12) — don't duplicate; reuse |
| Weekly carrier scorecard (on-time, tracking, rate-con compliance) | Weekly | Carrier-sales mgmt | Internal auto-send | Spec / future |
| Weekly ops-exceptions digest (rate-con missing, un-billed detention) | Weekly | Ops mgmt | Internal auto-send | Ties to the accessorial pilot; future |

## Architecture

```
 schedule (monthly/weekly cron) ─▶ Runner ─▶ report module (build_report)
                                       │            │ reads DGLIQ (report SQL) — on-network
                                       ▼            ▼
                                   render (HTML/PDF)
                                       │
                            ┌──────────┴───────────┐
                     customer-facing            internal
                     M365 draft to sender       M365 auto-send
                     (approve → send)           (or Slack post)
```

- **Runner:** a small scheduled worker (same host family as the accessorial service, on the
  Delta network with DGLIQ access). One entry per catalog row: `(report_module, params,
  cadence, recipients, delivery_mode)` in config.
- **Report module:** pure `build_report(...) -> dict` + `render(dict) -> html`. Add a report
  by adding a module; no framework change. `report_customer_monthly.py` is the reference.
- **Data source:** the **DGLIQ `DGL_TMS` warehouse** (`tms.Order/Movement/Stop/Customer/
  Carrier`) — the normalized replica, ideal for aggregate reporting (the live connector's
  50-row search cap makes it unsuitable; DGLIQ is the right source).
- **Delivery:** `outlook_create_draft` (customer-facing → sender reviews, then sends) or
  `outlook_send_mail` / Slack (internal). Injected callable so the module stays testable.

## Guardrails

- **Read-only** data; the only write is an email. **Customer-facing reports are drafts** a
  human approves before sending — never auto-sent to a customer. Internal reports may
  auto-send.
- Recipients come from config / `tms.Customer.GroupEmail` + the salesperson, never guessed.
- A report with anomalous numbers (e.g. margin < 0, volume 0, a MoM swing beyond a
  threshold) is held for review instead of sent.
- Every send is logged; a monthly index lists what went out.

## Rollout

1. **Render-only** (now): generate the monthly report to file/artifact for the account owner
   to eyeball. No email.
2. **Draft-on-approve** (after M365 re-auth): create an Outlook draft addressed to the
   customer for the owner to review and send.
3. **Auto-send internal** reports once the draft path is trusted.

## To finish

- Re-authorize the **Microsoft 365** connector (drafting/sending).
- Confirm per-report **recipient lists** and cadence with the owners.
- Stand the runner on the DGLIQ-accessible host (shared with the accessorial service).
- Decide the anomaly-hold thresholds.
