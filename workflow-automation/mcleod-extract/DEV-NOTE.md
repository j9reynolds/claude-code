# Note to the dgl-mcp developer

Hi — you built the `dgl-mcp` connector, so you already have a working read connection to
`LME_1720` on DB02. I need a one-time, **read-only** pull to quantify accessorial revenue
leakage (detention/layover/TONU/etc. we didn't bill customers or didn't deduct/pay carriers)
across the last 365 days. It's all `SELECT`s — nothing is written.

**Please run `mcleod_leakage_extract.sql` and send back CSVs:**

1. **Query A → `loads.csv`** — one row per delivered load (billed in the last 365 days):
   header, carrier + pay, rate-con state, and stop-derived dwell / on-time. Runs as-is.
2. **Query B → `othercharges.csv`** — every itemized other-charge line on those loads, with
   its real charge code. Runs as-is (if `descr` errors, the column is `description`).
3. **Query C → `carriercharges.csv`** — carrier-side charges (accessorial pay + deductions).
   The carrier-charge table name varies by build, so there's a one-line discovery snippet at
   the top of Query C — run it, drop the table name into the template, done. If it's a
   hassle, skip C; A + B still produce most of the number (carrier pay total is in A).

Notes:
- Core table/column names (orders, movement, stop, customer, payee) are already confirmed
  from the connector, so A and B should run without edits.
- I intentionally did **not** hardcode an accessorial code list — Query B returns every code
  and I classify them here, so you don't have to match our taxonomy.
- Scope is `status = 'D'` (delivered) and `bill_date` within 365 days. If you'd rather scope
  by `ordered_date` or include un-billed delivered loads, tell me and I'll adjust.
- CSVs can go wherever is easiest (secure share / attachment). No PII beyond normal
  customer/carrier names and load data; no need to include anything else.

Once I have `loads.csv` + `othercharges.csv` (+ `carriercharges.csv` if easy), I'll return
the leakage total with per-bucket and per-customer breakdowns, and finalize the customer
accessorial rate sheet from the actuals. Thanks!
