# Accessorial Leakage — 365-Day Loss Analysis

**Question:** how much did Delta lose over the last 365 days because accessorials and other
Rate Confirmation items weren't billed to the customer and/or weren't paid/deducted
to/from the carrier?

## Straight answer on the number

**I cannot give you the real dollar figure yet, and I will not invent one.** The two inputs
this calculation requires both live in **McLeod**, which is not connected to this session:

1. **What you actually billed customers** for accessorials over 365 days (AR revenue by
   accessorial code).
2. **The load-level facts** that determine what *should* have been billed and deducted —
   detention check-in/out times, MacroPoint tracking status, POD timeliness, signed-doc
   status, missing rate cons, etc.

I searched SharePoint and email for an existing McLeod accessorial/revenue export; none
exists in the connected systems. The only accessorial numbers anywhere are the Command
Center's **generic company-wide assumptions** (e.g. detention bill ~$148 / pay ~$99), which
are planning defaults, not a record of what was billed. Extrapolating a company-wide annual
loss from those would be a fabricated number, and a fabricated "Delta lost $X" is exactly
the kind of figure that does damage in a leadership deck.

## What I built instead: the calculator that produces the real number

`reference-implementation/leakage_model.py` computes the loss precisely from a McLeod
export. It reuses the Rate Confirmation engine and adds the customer rate sheet, then for
each load measures the gap between entitlement and reality across **three buckets**:

| # | Bucket | What it captures |
|---|--------|------------------|
| 1 | **Customer under-billing** | Accessorials that occurred but were billed below the standard rate — or not at all. Includes the known lumper leak. |
| 2 | **Deduction under-enforcement** | Carrier penalties the Rate Confirmation allows (tracking failure, late/continued POD, missed check-calls, missing signed rate con, direct-run, exclusive-use) that were never charged back — money Delta should have kept. |
| 3 | **Carrier overpayment** | Accessorials paid to the carrier while ineligible (carrier at fault, no signed proof, or customer never paid) — money Delta should not have paid. |

**Total leakage = 1 + 2 + 3.** It rolls up across all loads and breaks the loss down by
bucket (and can be extended to per-customer and per-accessorial-type once real data is in).

The model is unit-tested (10/10) and pure — it reads a dataset and returns numbers; it
changes nothing.

### Illustrative run (SAMPLE DATA — not Delta's actuals)

Running the model on five synthetic sample loads, purely to show it works end to end:

```
1. Customer under-billing:     $      405.00
2. Deductions un-enforced:     $      700.00
3. Carrier overpayment:        $      246.00
   TOTAL LEAKAGE:              $    1,351.00   (5 sample loads)
```

**These are invented loads.** They demonstrate the mechanics; they say nothing about
Delta's real exposure. The real number comes from running the same model on your McLeod
365-day export.

## What I need from you to produce the real figure

One McLeod report: **one row per load delivered in the trailing 365 days**, with these
columns (the model's `required_mcleod_columns()` lists them verbatim):

- **Identity / scope:** pro_number, delivered_date, customer, carrier, team_service, linehaul_rate
- **Operational facts:** stop_check_in, stop_check_out, carrier_at_fault, signed_facility_proof,
  revised_signed_ratecon, customer_paid, layover, tonu, stopoff_count, lumper_cost,
  driver_assist_preapproved
- **Penalty triggers:** macropoint_tracking_provided, arrived_on_time, direct_run_violation,
  missed_check_calls_count, pod_late, pod_days_late, signed_ratecon_returned,
  exclusive_use_violation
- **Actuals (from AR / settlement):** actual_customer_accessorial_billed,
  actual_carrier_accessorial_paid, actual_deductions_taken

Not every column will exist cleanly in McLeod (some — like "carrier_at_fault" or
"signed_facility_proof" — may need a proxy or may simply be unknown on older loads). That's
fine and important: **where a fact is unknown, the model treats the item conservatively so
the result is a defensible floor, not an inflated headline.** Connect McLeod (read) or drop
me the export as CSV and I'll return the real, sourced number with the per-bucket and
per-customer breakdown.

## Why this is worth doing even before the number lands

Two of the three buckets are pure margin you're contractually entitled to and simply not
capturing: **un-enforced carrier deductions** (bucket 2) and the **lumper underbilling**
your own tool already flags. Those don't require winning a customer negotiation — they
require the process the accessorial engine automates. The leakage number quantifies the
prize; the engine is how you collect it going forward.
