# McLeod Extract — Connection & Run Guide

Everything needed to pull the **real** accessorial-leakage numbers from McLeod and feed
them into the leakage model. Written for whoever has McLeod / DB access on the Delta
network (IT, a DBA, or the McLeod admin).

## The target

- **Server:** `DB02`
- **Database:** `LME_1720` (McLeod LoadMaster Enterprise, SQL Server)
- **Access needed:** a **read-only** SQL login. The extract issues `SELECT`s only.

## Why this can't run from the Claude session (SQL *or* API)

The Claude session runs in an isolated cloud sandbox whose only outbound path is HTTPS
through a **managed egress proxy that allowlists specific hosts** (the wired-in connectors —
Microsoft 365, HubSpot, etc.). Two things were confirmed from inside the sandbox:

- **Direct SQL to `DB02` is impossible:** `DB02` does not resolve and TCP/1433 has no route.
- **The McLeod REST API is also unreachable:** the egress proxy returns **HTTP 403 (tunnel
  refused) for non-allowlisted hosts** — even McLeod's public domain. So an on-prem *or* a
  public McLeod API endpoint is blocked from this session regardless of credentials.

This is a network/allowlist boundary, not a permission toggle. Two ways forward:

1. **Run the extractor yourself** (SQL or API path below) on a machine that can reach
   McLeod, and send back `loads_365d.csv`.
2. **Add McLeod as a first-party connector** for Claude (the same way M365/HubSpot are
   wired in). That routes through the allowlisted path and is the durable "connect Claude
   to McLeod" answer — worth doing once, since it also unblocks five of the seven backlog
   opportunities.

## Two extract paths (pick whichever your access fits)

- **`mcleod_leakage_extract.sql` + `mcleod_extract.py`** — direct read-only SQL against
  `DB02` / `LME_1720`. Best if you have a read-only DB login / replica.
- **`mcleod_api_extract.py`** — the McLeod **REST API** path. Reads `MCLEOD_API_BASE` and
  `MCLEOD_API_TOKEN` from the environment (no secret in the repo). `--probe` tests
  reachability + auth; `--run` writes the CSV. Use this if API access is easier to grant
  than DB access, or if your McLeod API is internet-facing.

Both paths emit the identical CSV that `leakage_model.py --csv` consumes.

## Files

| File | What it is |
|------|-----------|
| `mcleod_leakage_extract.sql` | Read-only SQL: one row per load delivered in the last 365 days, columns matching the leakage model. Standard LME schema with every site-specific name marked `-- CONFIRM`. |
| `mcleod_extract.py` | Runner: connects (pyodbc or pymssql), runs the SQL, writes `loads_365d.csv` in the exact schema the model reads. `--discover` lists candidate tables first. |
| `../reference-implementation/leakage_model.py` | Consumes the CSV: `python3 leakage_model.py --csv loads_365d.csv` prints the real leakage total + top loss loads. |

## Document-type image numbers (for the signed Rate Confirmation pull)

The signed-rate-con timing comes from McLeod's **image header** table, filtered by the
document's **image type number** — the same number the API image pull uses.

| Document | Image type # | Source |
|----------|:------------:|--------|
| Temporary POD | **4** | Confirmed (PM) |
| Signed Rate Confirmation | **TODO — confirm** | The Accounting-folder document-type list / McLeod image setup. This is the one value the rate-con timing query needs. |
| BOL / final POD | TODO — confirm | same |

I found the SharePoint **Accounting** folder but the image-type-number list didn't surface
by search (likely a screenshot or a sub-doc). Grab the signed-rate-con number from that
list (or from McLeod: **Tools → Image Setup**, or `SELECT img_type, COUNT(*) FROM imghdr
GROUP BY img_type`), and set `@img_signed_ratecon` at the top of the SQL.

## Run it — three steps

```bash
# 0) install a driver on the DB-facing machine (one of):
pip install pyodbc      # + Microsoft ODBC Driver 18 for SQL Server
# or
pip install pymssql

# 1) DISCOVER — confirm table/column names on THIS instance, then fix every -- CONFIRM
python mcleod_extract.py --discover \
  --dsn "DRIVER={ODBC Driver 18 for SQL Server};SERVER=DB02;DATABASE=LME_1720;Trusted_Connection=yes;TrustServerCertificate=yes"

# 2) RUN — write loads_365d.csv
python mcleod_extract.py --run --sql mcleod_leakage_extract.sql --out loads_365d.csv \
  --dsn "DRIVER={ODBC Driver 18 for SQL Server};SERVER=DB02;DATABASE=LME_1720;UID=readonly;PWD=***;TrustServerCertificate=yes"

# 3) COMPUTE — the real leakage number
python3 ../reference-implementation/leakage_model.py --csv loads_365d.csv
```

Then send me `loads_365d.csv` (or connect McLeod via a supported connector) and I'll return
the full breakdown — total leakage, the three buckets, and per-customer / per-accessorial
detail — plus the finalized customer rate sheet from your actuals.

## What the SQL derives vs. what stays unknown

**Derived from McLeod (real):** stop check-in/out and dwell (detention), appointment
windows → on-time, customer accessorial billing (`othercharge`), carrier accessorial pay
and deductions (carrier other charges), signed-rate-con upload time and POD-late (image
header + `@img_*` types), linehaul, customer, carrier.

**Left unknown on purpose:** judgment fields McLeod doesn't store cleanly (carrier-at-fault,
signed facility proof, direct-run, exclusive-use, missed check-calls). The model treats a
blank as the **compliant / no-charge** default, so unknowns **shrink** the estimate rather
than inflate it — the result is a defensible floor. The one place a blank can push the
number up is customer detention under-billing when carrier-fault is unknown; if you want a
stricter floor there, we exclude those loads. Your call once we see the volume.

## Safety

Read-only throughout: the SQL only `SELECT`s, the runner opens the connection `readonly`,
and nothing writes back to McLeod. Run against a read replica if you have one.
