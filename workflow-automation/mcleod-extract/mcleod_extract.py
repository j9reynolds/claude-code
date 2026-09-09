"""McLeod LME extractor — runs the leakage SQL against DB02 / LME_1720 and writes the
CSV that reference-implementation/leakage_model.py consumes.

WHERE THIS RUNS
---------------
NOT in the Claude session sandbox — that environment cannot reach DB02 (an on-prem SQL
Server on Delta's private network; the sandbox only has HTTPS egress). Run this on a
machine that CAN reach DB02: a workstation/server on the Delta network, or a jump box with
a line of sight to the McLeod SQL Server, using a READ-ONLY SQL login.

It only issues SELECTs (see mcleod_leakage_extract.sql). It writes one local CSV.

USAGE
-----
  # 1) confirm the schema names first (fix the -- CONFIRM markers in the .sql)
  python mcleod_extract.py --discover --dsn "DRIVER={ODBC Driver 18 for SQL Server};SERVER=DB02;DATABASE=LME_1720;Trusted_Connection=yes;TrustServerCertificate=yes"

  # 2) run the extract -> loads_365d.csv
  python mcleod_extract.py --run --sql mcleod_leakage_extract.sql --out loads_365d.csv \
         --dsn "DRIVER={ODBC Driver 18 for SQL Server};SERVER=DB02;DATABASE=LME_1720;UID=readonly;PWD=***;TrustServerCertificate=yes"

  # 3) feed the CSV into the leakage model (see mcleod-connection-guide.md)

Driver: needs `pyodbc` + a SQL Server ODBC driver, OR `pymssql`. Both are optional imports
so this file stays inspectable without them. The column order written matches
leakage_model.required_mcleod_columns().
"""

from __future__ import annotations

import argparse
import csv
import sys

# The exact output header the leakage model expects. Kept in sync with
# leakage_model.required_mcleod_columns(). Duplicated here so this script has no import
# dependency on the reference implementation when run standalone on a DB host.
OUTPUT_COLUMNS = [
    "pro_number", "delivered_date", "customer", "carrier", "team_service",
    "linehaul_rate", "stop_check_in", "stop_check_out", "carrier_at_fault",
    "signed_facility_proof", "revised_signed_ratecon", "customer_paid", "layover",
    "tonu", "stopoff_count", "lumper_cost", "driver_assist_preapproved",
    "macropoint_tracking_provided", "arrived_on_time", "direct_run_violation",
    "missed_check_calls_count", "pod_late", "pod_days_late", "signed_ratecon_returned",
    "exclusive_use_violation", "actual_customer_accessorial_billed",
    "actual_carrier_accessorial_paid", "actual_deductions_taken",
]

DISCOVERY_SQL = """
SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
 WHERE TABLE_NAME LIKE '%order%' OR TABLE_NAME LIKE '%stop%'
    OR TABLE_NAME LIKE '%othercharge%' OR TABLE_NAME LIKE '%img%'
    OR TABLE_NAME LIKE '%movement%' OR TABLE_NAME LIKE '%carrier%'
 ORDER BY TABLE_NAME;
"""


def _connect(dsn: str):
    """Open a connection via pyodbc, falling back to pymssql. Imported lazily so this
    file loads fine on a machine without a driver (e.g. the Claude sandbox)."""
    try:
        import pyodbc  # type: ignore
        return pyodbc.connect(dsn, readonly=True, timeout=30)
    except ImportError:
        pass
    try:
        import pymssql  # type: ignore
        # Expect dsn like "SERVER=..;DATABASE=..;UID=..;PWD=.." for the pymssql path.
        kv = dict(p.split("=", 1) for p in dsn.split(";") if "=" in p)
        return pymssql.connect(server=kv.get("SERVER"), user=kv.get("UID"),
                               password=kv.get("PWD"), database=kv.get("DATABASE"))
    except ImportError:
        sys.exit("No SQL driver found. Install `pyodbc` (+ MS ODBC Driver) or `pymssql`.")


def discover(dsn: str) -> None:
    conn = _connect(dsn)
    cur = conn.cursor()
    cur.execute(DISCOVERY_SQL)
    print("Candidate tables (order / stop / othercharge / img / movement / carrier):")
    for row in cur.fetchall():
        print("  ", row[0])
    print("\nNext: for each, inspect columns via INFORMATION_SCHEMA.COLUMNS and fix the "
          "-- CONFIRM markers in mcleod_leakage_extract.sql.")
    conn.close()


def run(dsn: str, sql_path: str, out_path: str) -> None:
    with open(sql_path, "r", encoding="utf-8") as fh:
        sql = fh.read()
    conn = _connect(dsn)
    cur = conn.cursor()
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    if cols != OUTPUT_COLUMNS:
        # Not fatal — but warn loudly so a schema drift is visible before it reaches the model.
        print("WARNING: query columns do not match the expected leakage-model schema.\n"
              f"  got:      {cols}\n  expected: {OUTPUT_COLUMNS}", file=sys.stderr)
    n = 0
    with open(out_path, "w", newline="", encoding="utf-8") as out:
        w = csv.writer(out)
        w.writerow(cols)
        for row in cur.fetchall():
            w.writerow(list(row))
            n += 1
    conn.close()
    print(f"Wrote {n} load rows -> {out_path}")
    print("Next: load it into leakage_model (see mcleod-connection-guide.md).")


def main() -> None:
    ap = argparse.ArgumentParser(description="McLeod LME leakage extractor (read-only).")
    ap.add_argument("--dsn", help="ODBC/pymssql connection string for DB02 / LME_1720")
    ap.add_argument("--discover", action="store_true", help="list candidate tables and exit")
    ap.add_argument("--run", action="store_true", help="run the extract SQL and write CSV")
    ap.add_argument("--sql", default="mcleod_leakage_extract.sql")
    ap.add_argument("--out", default="loads_365d.csv")
    args = ap.parse_args()

    if not (args.discover or args.run):
        ap.print_help()
        print("\nThis extractor talks to an on-prem SQL Server. It cannot run inside the "
              "Claude sandbox (no route to DB02). Run it on a machine on the Delta "
              "network with a read-only McLeod login.")
        return
    if not args.dsn:
        sys.exit("--dsn is required for --discover/--run")
    if args.discover:
        discover(args.dsn)
    if args.run:
        run(args.dsn, args.sql, args.out)


if __name__ == "__main__":
    main()
