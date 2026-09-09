"""Monthly customer performance report — generator (opportunity #6, recurring reports).

Assembles a customer's month from the McLeod data: volume, revenue, margin, on-time,
carrier mix, and accessorials billed. Runs off either:
  * the DGLIQ monthly query output (production — report_customer_monthly.sql), or
  * the leakage CSVs (loads + other_charge + charge_code) for offline/sample runs.

Pure/no side effects: it computes and prints/returns a dict. Delivery (email draft) is a
separate, human-approved step (see recurring-reports-spec.md) — this only assembles.

Usage (offline sample):
  python3 report_customer_monthly.py loads.csv othercharges.csv chargecodes.csv \
      "United States Postal Service" 2026-08
"""

from __future__ import annotations

import csv
import sys
from collections import Counter

# accessorial charge_id -> category (fuel/linehaul excluded via the dictionary)
CAT = {"DET": "Detention", "DL": "Detention", "DU": "Detention", "DEP": "Detention",
       "DEW": "Detention", "LAYO": "Layover", "LAYR": "Layover", "LYC": "Layover",
       "TONU": "TONU", "DRA": "Driver assist", "SOC": "Stop-off", "STP": "Stop-off",
       "XST": "Stop-off", "LMP": "Lumper"}


def _cl(v):
    return (v or "").strip().strip('"').strip()


def _num(v):
    try:
        return float(_cl(v).replace("$", "").replace(",", ""))
    except ValueError:
        return 0.0


def build_report(loads_csv, oc_csv, cc_csv, customer_sub, month):
    """month = 'YYYY-MM' against delivered_date. customer_sub = case-sensitive substring of
    the customer name. Returns a metrics dict."""
    fuel = {_cl(r["charge_id"]) for r in csv.DictReader(open(cc_csv, encoding="utf-8-sig"))
            if _cl(r["is_fuel_surcharge"]) == "Y"}
    rows = [r for r in csv.DictReader(open(loads_csv, encoding="utf-8-sig"))
            if customer_sub in _cl(r["customer"]) and _cl(r["delivered_date"]).startswith(month)]
    ids = {_cl(r["pro_number"]) for r in rows}
    n = len(rows)
    if n == 0:
        return {"customer": customer_sub, "month": month, "loads": 0}

    revenue = sum(_num(r["total_charge"]) for r in rows)
    freight = sum(_num(r["linehaul_rate"]) for r in rows)
    carrier_pay = sum(_num(r["carrier_total_pay"]) for r in rows)
    late = sum(1 for r in rows if _cl(r.get("any_late_arrival")) == "1")
    carriers = Counter(_cl(r["carrier"]) for r in rows)

    acc = Counter()
    for r in csv.DictReader(open(oc_csv, encoding="utf-8-sig")):
        if _cl(r["order_id"]) in ids:
            code = _cl(r["charge_id"])
            if code in fuel or code not in CAT:
                continue
            acc[CAT[code]] += _num(r["amount"])

    return {
        "customer": _cl(rows[0]["customer"]), "month": month, "loads": n,
        "revenue": round(revenue, 2), "freight": round(freight, 2),
        "carrier_pay": round(carrier_pay, 2),
        "gross_margin": round(revenue - carrier_pay, 2),
        "margin_pct": round((revenue - carrier_pay) / revenue * 100, 1) if revenue else 0.0,
        "avg_per_load": round(revenue / n, 2),
        "on_time_pct": round((n - late) / n * 100, 1), "late_loads": late,
        "carrier_count": len(carriers),
        "top_carriers": carriers.most_common(5),
        "accessorials": dict(acc), "accessorial_total": round(sum(acc.values()), 2),
    }


def render_text(rep) -> str:
    if not rep.get("loads"):
        return f"{rep['customer']} {rep['month']}: no delivered loads."
    L = [
        f"MONTHLY CUSTOMER REPORT — {rep['customer']} — {rep['month']}",
        f"  Loads delivered:   {rep['loads']:,}",
        f"  Revenue:           ${rep['revenue']:,.0f}  (freight ${rep['freight']:,.0f})",
        f"  Carrier pay:       ${rep['carrier_pay']:,.0f}",
        f"  Gross margin:      ${rep['gross_margin']:,.0f}  ({rep['margin_pct']}%)",
        f"  Avg revenue/load:  ${rep['avg_per_load']:,.0f}",
        f"  On-time delivery:  {rep['on_time_pct']}%  ({rep['late_loads']} late)",
        f"  Carriers used:     {rep['carrier_count']}",
        f"  Accessorials billed: ${rep['accessorial_total']:,.0f}  {rep['accessorials']}",
    ]
    return "\n".join(L)


if __name__ == "__main__":
    if len(sys.argv) != 6:
        print(__doc__)
        sys.exit(1)
    print(render_text(build_report(*sys.argv[1:6])))
