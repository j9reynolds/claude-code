"""McLeod LME REST API extractor — pulls the leakage fields via the McLeod API and writes
the same CSV the leakage model consumes.

WHY THIS EXISTS (and its limits)
--------------------------------
An alternative to the direct-SQL path (mcleod_leakage_extract.sql) for sites that expose
the McLeod REST API. It needs two things this script does NOT hardcode:

  * MCLEOD_API_BASE   — the API base URL (e.g. https://<your-mcleod-host>/ws/rest)
  * MCLEOD_API_TOKEN  — the McLeod company API token (sent as the Authorization header)

Both come from the environment so no secret is ever written to the repo. If the McLeod API
is on-prem (typical — co-located with the DB server), the Claude sandbox cannot reach it;
run this on a machine on the Delta network. If it is a public HTTPS endpoint, it can run
anywhere with the token.

  export MCLEOD_API_BASE="https://<host>/ws/rest"
  export MCLEOD_API_TOKEN="<company token>"

  python mcleod_api_extract.py --probe                     # test reachability + auth only
  python mcleod_api_extract.py --run --out loads_365d.csv  # pull 365 days -> CSV

SCHEMA CAVEAT: McLeod REST endpoint paths and field names vary by API version and site.
The endpoint paths and JSON keys below are the common LME REST shape; confirm them against
your API docs / a single-record probe and adjust the ENDPOINTS / field maps. Everything to
confirm is marked CONFIRM.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sys

# Column contract shared with the leakage model (leakage_model.CANONICAL_COLUMNS).
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

# CONFIRM these against your McLeod API version.
ENDPOINTS = {
    "orders": "/orders",           # list delivered orders (supports date filter + paging)
    "stops": "/orders/{id}/stops",  # stops for an order (appointment + actual times)
    "othercharge": "/orders/{id}/othercharges",       # customer accessorial billing
    "carriercharge": "/movements/{mid}/othercharges",  # carrier pay / deductions
    "images": "/orders/{id}/images",  # image index; filter by image type number
}
# Image type numbers (temporary POD = 4 confirmed; confirm the rest).
IMG_SIGNED_RATECON = os.environ.get("MCLEOD_IMG_SIGNED_RATECON")  # CONFIRM (set when known)
IMG_TEMP_POD = "4"


def _session():
    try:
        import requests  # type: ignore
    except ImportError:
        sys.exit("This extractor needs `requests` (pip install requests).")
    base = os.environ.get("MCLEOD_API_BASE")
    token = os.environ.get("MCLEOD_API_TOKEN")
    if not base or not token:
        sys.exit("Set MCLEOD_API_BASE and MCLEOD_API_TOKEN in the environment first.")
    s = requests.Session()
    # CONFIRM auth style: McLeod commonly uses `Authorization: Token <token>` with a
    # company header. Adjust to your instance.
    s.headers.update({
        "Authorization": f"Token {token}",
        "Accept": "application/json",
        "X-com.mcleodsoftware.CompanyID": os.environ.get("MCLEOD_COMPANY_ID", ""),  # CONFIRM
    })
    return s, base.rstrip("/")


def probe() -> None:
    """Test reachability + auth without pulling data. Prints a clear pass/fail."""
    s, base = _session()
    url = base + ENDPOINTS["orders"]
    try:
        r = s.get(url, params={"rows": 1}, timeout=15)
    except Exception as e:  # network/DNS/TLS failure -> almost certainly unreachable/on-prem
        print(f"UNREACHABLE: {type(e).__name__}: {e}")
        print("If the McLeod API is on-prem, this host has no route to it. Run on-network.")
        return
    print(f"Reached {url} -> HTTP {r.status_code}")
    if r.status_code == 200:
        print("OK: reachable and authenticated.")
    elif r.status_code in (401, 403):
        print("Reachable, but auth was rejected — check MCLEOD_API_TOKEN / company id / header style.")
    else:
        print(f"Reachable; unexpected status. Body head: {r.text[:200]}")


def _yn(b) -> str:
    return "Y" if b else "N"


def run(out_path: str) -> None:
    s, base = _session()
    since = (dt.date.today() - dt.timedelta(days=365)).isoformat()
    rows = []
    page, page_size = 0, 200
    while True:
        # CONFIRM: order list filter for delivered-since + paging params.
        r = s.get(base + ENDPOINTS["orders"],
                  params={"deliveredSince": since, "start": page * page_size, "rows": page_size},
                  timeout=60)
        r.raise_for_status()
        batch = r.json()
        orders = batch.get("orders", batch if isinstance(batch, list) else [])
        if not orders:
            break
        for o in orders:
            rows.append(_order_to_row(s, base, o))
        if len(orders) < page_size:
            break
        page += 1
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(OUTPUT_COLUMNS)
        w.writerows(rows)
    print(f"Wrote {len(rows)} load rows -> {out_path}")
    print("Next: python3 ../reference-implementation/leakage_model.py --csv " + out_path)


def _order_to_row(s, base, o) -> list:
    """Map one order (+ its sub-resources) to the canonical CSV row. CONFIRM JSON keys."""
    oid = o.get("id")
    mid = o.get("currMovementId") or o.get("movementId")

    stops = _get(s, base, ENDPOINTS["stops"].format(id=oid))
    check_in = min((st.get("actualArrival") for st in stops if st.get("actualArrival")), default="")
    check_out = max((st.get("actualDeparture") for st in stops if st.get("actualDeparture")), default="")
    late = any(st.get("actualArrival") and st.get("schedArriveLate")
               and st["actualArrival"] > st["schedArriveLate"] for st in stops)

    cust_charges = _get(s, base, ENDPOINTS["othercharge"].format(id=oid))
    acc_codes = {"DET", "LAY", "TONU", "STOP", "LUMP"}  # CONFIRM your codes
    cust_billed = sum(float(c.get("amount", 0)) for c in cust_charges
                      if c.get("chargeId") in acc_codes)
    lumper = sum(float(c.get("amount", 0)) for c in cust_charges if c.get("chargeId") == "LUMP")

    carr_charges = _get(s, base, ENDPOINTS["carriercharge"].format(mid=mid)) if mid else []
    carr_paid = sum(float(c.get("amount", 0)) for c in carr_charges if float(c.get("amount", 0)) > 0)
    deductions = sum(-float(c.get("amount", 0)) for c in carr_charges if float(c.get("amount", 0)) < 0)

    ratecon_returned = ""
    if IMG_SIGNED_RATECON:
        imgs = _get(s, base, ENDPOINTS["images"].format(id=oid))
        ratecon_returned = _yn(any(str(im.get("type")) == str(IMG_SIGNED_RATECON) for im in imgs))

    row = {c: "" for c in OUTPUT_COLUMNS}
    row.update(
        pro_number=oid,
        delivered_date=(o.get("deliveredDate") or "")[:10],
        customer=o.get("customerName", ""),          # CONFIRM
        carrier=o.get("carrierName", ""),            # CONFIRM
        linehaul_rate=o.get("freightCharge", ""),    # CONFIRM
        stop_check_in=check_in, stop_check_out=check_out,
        arrived_on_time=_yn(not late),
        revised_signed_ratecon=ratecon_returned,
        signed_ratecon_returned=ratecon_returned,
        lumper_cost=lumper,
        actual_customer_accessorial_billed=cust_billed,
        actual_carrier_accessorial_paid=carr_paid,
        actual_deductions_taken=deductions,
    )
    return [row[c] for c in OUTPUT_COLUMNS]


def _get(s, base, path):
    r = s.get(base + path, timeout=60)
    if r.status_code != 200:
        return []
    j = r.json()
    return j if isinstance(j, list) else j.get("data", j.get("items", []))


def main() -> None:
    ap = argparse.ArgumentParser(description="McLeod LME REST API leakage extractor.")
    ap.add_argument("--probe", action="store_true", help="test reachability + auth only")
    ap.add_argument("--run", action="store_true", help="pull 365 days and write CSV")
    ap.add_argument("--out", default="loads_365d.csv")
    args = ap.parse_args()
    if args.probe:
        probe()
    elif args.run:
        run(args.out)
    else:
        ap.print_help()
        print("\nSet MCLEOD_API_BASE + MCLEOD_API_TOKEN (and MCLEOD_IMG_SIGNED_RATECON when "
              "known), then --probe to test the connection.")


if __name__ == "__main__":
    main()
