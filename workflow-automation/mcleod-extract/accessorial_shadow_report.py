"""Accessorial SHADOW REPORT generator (opportunity #1).

Read-only "money left on the table" report over one month of delivered McLeod loads.
Consumes the five CSVs produced by `mcleod_accessorial_monthly.sql`
(loads / othercharges / carrierpay / chargecodes / stops) and returns a STRUCTURED
result (per-category, per-customer, per-load) that a workbook can render and a human
reviews. It reports only — it never bills, pays, or writes anything.

Four buckets:
  A. Un-billed customer DETENTION — appointment-based, per-stop $150 cap, eligibility
     adjusted (carrier-late stops removed), minus loads already carrying a detention
     charge. This is the headline leakage number.
  B. Accessorial MARGIN by category — customer-billed vs carrier-paid vs deducted
     (hard data from the charges themselves).
  C. NEGATIVE-margin categories — where Delta paid the carrier an accessorial it did
     not bill the customer (a subset view of B, surfaced for action).
  D. Rate-con CONTROL GAP — delivered loads with no rate_confirmation_sent_date.

The classification maps, constants, and helpers are imported from `analyze_leakage`
(the 365-day analysis) so the monthly report and that analysis never diverge. McLeod
actual times are approximate; the per-load defensible figure still needs the POD
(`reference-implementation/pod_reader.py`) — the detention numbers here are the
population estimate for review, explicitly flagged.

Usage:
  python3 accessorial_shadow_report.py loads.csv othercharges.csv carrierpay.csv \\
      chargecodes.csv stops.csv 2026-08
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict

import analyze_leakage as A   # same directory; reuse its constants + classifiers

FREE_MIN = A.FREE_MIN
DET_RATE_HR = A.DET_RATE_HR
LAYOVER_CAP = A.LAYOVER_CAP
DROP_DWELL_H = A.DROP_DWELL_H
clean, num, parse_dt = A.clean, A.num, A.parse_dt


def _read(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def _fuel_codes(chargecodes_rows):
    return {clean(r.get("charge_id")) for r in chargecodes_rows
            if clean(r.get("is_fuel_surcharge")) == "Y"}


def _loads(loads_rows):
    out = {}
    for r in loads_rows:
        pro = clean(r.get("pro_number"))
        if not pro:
            continue
        rc = clean(r.get("rate_confirmation_sent_date"))
        out[pro] = {
            "customer": clean(r.get("customer")),
            "dwell": num(r.get("max_dwell_minutes")),
            "linehaul": num(r.get("linehaul_rate")),
            "carrier_pay": num(r.get("carrier_total_pay")),
            "rc_missing": rc in ("", "NULL"),
        }
    return out


def _customer_billed(oc_rows, fuel_codes):
    """Customer accessorial billed by category; also the set of orders billed detention."""
    billed = defaultdict(float)
    det_billed_orders = set()
    for r in oc_rows:
        code = clean(r.get("charge_id"))
        if code in fuel_codes:
            continue
        cat = A.CODE_CATEGORY.get(code)
        if cat is None:
            continue
        billed[cat] += num(r.get("amount"))
        if cat == "detention":
            det_billed_orders.add(clean(r.get("order_id")))
    return billed, det_billed_orders


def _carrier_paid_deducted(cp_rows):
    paid = defaultdict(float)      # positive amounts paid TO the carrier
    deducted = defaultdict(float)  # amounts deducted FROM the carrier (stored positive here)
    for r in cp_rows:
        cat = A.carrier_category(clean(r.get("descr")) + " " + clean(r.get("short_desc")))
        if cat is None:
            continue
        amt = num(r.get("amount"))
        if amt >= 0:
            paid[cat] += amt
        else:
            deducted[cat] += -amt
    return paid, deducted


def _detention_gap(stops_rows, det_billed_orders, loads):
    """Appointment-based, per-stop-capped, eligibility-adjusted un-billed detention.
    Mirrors analyze_leakage.detention_from_stops but RETURNS structure instead of printing."""
    per_order_all = defaultdict(float)    # pre-eligibility (per-stop cap, drops excluded)
    per_order_elig = defaultdict(float)   # + carrier-late stops removed
    late_excluded = 0
    late_dollars = 0.0
    for r in stops_rows:
        arr, dep = parse_dt(r.get("actual_arrival")), parse_dt(r.get("actual_departure"))
        if not arr or not dep or dep <= arr:
            continue
        appt = parse_dt(r.get("appointment_early"))
        appt_late = parse_dt(r.get("appointment_late")) or appt
        ref = appt or arr
        onsite_h = (dep - arr).total_seconds() / 3600.0
        billable_h = max(0.0, min((dep - ref).total_seconds() / 3600.0 - FREE_MIN / 60.0,
                                  onsite_h))
        if billable_h <= 0 or onsite_h > DROP_DWELL_H:
            continue
        dollars = min(billable_h * DET_RATE_HR, LAYOVER_CAP)   # cap per stop
        oid = clean(r.get("order_id"))
        per_order_all[oid] += dollars
        if appt_late is not None and arr > appt_late:          # carrier-fault -> not owed
            late_excluded += 1
            late_dollars += dollars
        else:
            per_order_elig[oid] += dollars

    def gap(per_order):
        g = {o: d for o, d in per_order.items() if o not in det_billed_orders}
        return g, sum(g.values())

    gap_all, gap_all_total = gap(per_order_all)
    gap_elig, gap_elig_total = gap(per_order_elig)

    by_customer = defaultdict(lambda: [0, 0.0])
    for oid, dollars in gap_elig.items():
        cust = (loads.get(oid) or {}).get("customer", "(unknown)")
        by_customer[cust][0] += 1
        by_customer[cust][1] += dollars
    by_customer_rows = sorted(
        ({"customer": c, "loads": n, "dollars": d} for c, (n, d) in by_customer.items()),
        key=lambda x: -x["dollars"])

    return {
        "pre_elig_gap": gap_all_total, "pre_elig_loads": len(gap_all),
        "late_excluded_stops": late_excluded, "late_dollars": late_dollars,
        "elig_gap": gap_elig_total, "elig_loads": len(gap_elig),
        "elig_entitled": sum(per_order_elig.values()),
        "by_customer": by_customer_rows,
        "by_order": dict(gap_elig),
    }


def build_shadow_report(loads_csv, oc_csv, cp_csv, cc_csv, stops_csv, month):
    """Return the structured shadow-report result dict (no side effects)."""
    fuel = _fuel_codes(_read(cc_csv))
    loads = _loads(_read(loads_csv))
    billed, det_billed_orders = _customer_billed(_read(oc_csv), fuel)
    paid, deducted = _carrier_paid_deducted(_read(cp_csv))
    detention = _detention_gap(_read(stops_csv), det_billed_orders, loads)

    categories = []
    tot = {"billed": 0.0, "paid": 0.0, "deducted": 0.0, "margin": 0.0}
    for cat in A.ACCESSORIAL_CATS:
        b, p, d = billed.get(cat, 0.0), paid.get(cat, 0.0), deducted.get(cat, 0.0)
        m = b - p + d
        categories.append({"category": cat, "billed": b, "paid": p, "deducted": d, "margin": m})
        tot["billed"] += b; tot["paid"] += p; tot["deducted"] += d; tot["margin"] += m
    categories.append({"category": "TOTAL", **tot})

    negative = [c for c in categories
                if c["category"] != "TOTAL" and c["margin"] < 0]

    n_loads = len(loads)
    rc_missing = sum(1 for d in loads.values() if d["rc_missing"])
    ratecon_loads = sorted(p for p, d in loads.items() if d["rc_missing"])

    return {
        "month": month,
        "overview": {
            "loads": n_loads,
            "rc_missing": rc_missing,
            "rc_missing_pct": (100.0 * rc_missing / n_loads) if n_loads else 0.0,
        },
        "categories": categories,           # bucket B
        "negative_margin": negative,        # bucket C
        "detention": detention,             # bucket A
        "ratecon_gap": {                    # bucket D
            "missing": rc_missing,
            "pct": (100.0 * rc_missing / n_loads) if n_loads else 0.0,
            "loads": ratecon_loads,
        },
    }


def render_text(rep) -> str:
    o = rep["overview"]
    L = []
    L.append("=" * 74)
    L.append(f" ACCESSORIAL SHADOW REPORT - {rep['month']}  (delivered loads, prior month)")
    L.append("=" * 74)
    L.append(f" Delivered loads:            {o['loads']:,}")
    L.append(f" Rate-con date MISSING on:   {o['rc_missing']:,} loads ({o['rc_missing_pct']:.1f}%)")
    L.append("-" * 74)
    L.append(" A. UN-BILLED DETENTION (appointment-based, per-stop $150 cap, eligibility-adj.)")
    d = rep["detention"]
    L.append(f"   Pre-eligibility un-billed:   ${d['pre_elig_gap']:,.0f}  ({d['pre_elig_loads']:,} loads)")
    L.append(f"   Carrier-late removed:        {d['late_excluded_stops']:,} stops, ${d['late_dollars']:,.0f}")
    L.append(f"   ELIGIBLE un-billed:          ${d['elig_gap']:,.0f}  ({d['elig_loads']:,} loads)")
    for row in d["by_customer"][:10]:
        L.append(f"     {row['customer'][:44]:<46}{row['loads']:>5} loads  ${row['dollars']:>9,.0f}")
    L.append("-" * 74)
    L.append(" B. ACCESSORIAL MARGIN by category (customer billed vs carrier paid/deducted)")
    L.append(f"   {'category':<14}{'billed':>13}{'paid':>13}{'deducted':>12}{'margin':>13}")
    for c in rep["categories"]:
        L.append(f"   {c['category']:<14}{c['billed']:>13,.2f}{c['paid']:>13,.2f}"
                 f"{c['deducted']:>12,.2f}{c['margin']:>13,.2f}")
    if rep["negative_margin"]:
        L.append(" C. NEGATIVE-margin categories (paid the carrier more than billed the customer):")
        for c in rep["negative_margin"]:
            L.append(f"     {c['category']:<14} margin ${c['margin']:,.2f}")
    L.append("-" * 74)
    L.append(f" D. RATE-CON CONTROL GAP: {rep['ratecon_gap']['missing']:,} loads "
             f"({rep['ratecon_gap']['pct']:.1f}%) with no signed rate-con recorded.")
    L.append("=" * 74)
    L.append(" Detention uses McLeod approximate times — the per-load billable figure needs")
    L.append(" the POD (pod_reader.py). This report is READ-ONLY; a human reviews before any bill.")
    return "\n".join(L)


if __name__ == "__main__":
    if len(sys.argv) not in (7, 8):
        print(__doc__)
        print("\nUsage: accessorial_shadow_report.py loads.csv othercharges.csv carrierpay.csv "
              "chargecodes.csv stops.csv YYYY-MM [out.xlsx]")
        sys.exit(1)
    rep = build_shadow_report(*sys.argv[1:6], month=sys.argv[6])
    print(render_text(rep))
    if len(sys.argv) == 8:
        import accessorial_workbook
        accessorial_workbook.build_workbook(rep, sys.argv[7])
        print(f"\nworkbook written: {sys.argv[7]}")
