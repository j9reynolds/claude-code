"""Accessorial leakage analysis over the real McLeod export (loads + other_charge +
driver_extra_pay + charge_code dictionary).

Reads the four CSVs produced by mcleod_leakage_extract.sql and reports, over the pulled
365-day window of delivered loads:

  1. Data overview and the rate-confirmation control gap.
  2. Accessorial CUSTOMER-BILLED vs CARRIER-PAID by category, and the margin — the
     realized bill/pay picture (this is hard data, no assumptions).
  3. Detention capture gap: loads whose worst-stop dwell exceeded free time but that
     carry NO customer detention charge — a count (hard) plus an INDICATIVE dollar
     figure at the Rate-Con rate, explicitly flagged because dwell also includes
     legitimate load/unload time and carrier-fault eligibility is unknown here.

Run:  python3 analyze_leakage.py <loads.csv> <othercharges.csv> <carrierpay.csv> <chargecodes.csv>

No customer data is written out; it prints a summary. Amounts are USD.
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict

FREE_MIN = 120          # 2h free (Rate Confirmation)
DET_RATE_HR = 35.0      # carrier detention $/h solo (Rate Confirmation)
LAYOVER_CAP = 150.0     # detention caps at layover (solo)


def clean(v):
    return (v or "").strip().strip('"').strip()


def num(v):
    v = clean(v).replace("$", "").replace(",", "")
    try:
        return float(v)
    except ValueError:
        return 0.0


# ---- charge-code -> category (customer side, keyed by charge_id) --------------------
CODE_CATEGORY = {
    # detention
    "DET": "detention", "DL": "detention", "DU": "detention", "DEP": "detention",
    "DEW": "detention", "DR": "detention", "LDF": "detention",
    # layover
    "LAYO": "layover", "LAYR": "layover", "LYC": "layover",
    # tonu
    "TONU": "tonu",
    # driver assist / labor / load-unload
    "DRA": "driver_assist", "LDA": "driver_assist", "LAB": "driver_assist",
    "UFE": "load_unload", "LUF": "load_unload", "LFE": "load_unload",
    # lumper
    "LMP": "lumper",
    # stopoff
    "SOC": "stopoff", "STP": "stopoff", "XST": "stopoff", "LSF": "stopoff",
}
ACCESSORIAL_CATS = ["detention", "layover", "tonu", "driver_assist", "lumper",
                    "stopoff", "load_unload"]


def carrier_category(text: str):
    t = text.lower()
    # exclude advances / money codes / fees / quick pay / fuel advances
    if any(k in t for k in ("efs", "money code", "advance", "quick pay", "quickpay",
                            "fuel adv", "comchek", "com check", "tcheck", "t-chek")):
        return None
    if "lumper" in t:
        return "lumper"
    if "detention" in t:
        return "detention"
    if "layover" in t:
        return "layover"
    if "truck order not used" in t or "tonu" in t:
        return "tonu"
    if "driver assist" in t or "helper" in t or ("labor" in t) or "assist" in t:
        return "driver_assist"
    if "stop" in t and "stופ" not in t:
        return "stopoff"
    if "unload" in t or "loading" in t:
        return "load_unload"
    return None  # not an accessorial (white glove, misc, etc.)


def parse_dt(v):
    v = clean(v)
    if v in ("", "NULL"):
        return None
    v = v.split(".")[0]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            from datetime import datetime
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    return None


DROP_DWELL_H = 18.0     # a single-stop on-site dwell beyond this is a trailer drop / relay,
                        # not live detention — excluded so drops don't masquerade as detention.


def detention_from_stops(stops_p, det_billed_orders):
    """APPOINTMENT-BASED detention over all stops (Query E / stops.csv):
    per stop, clock = (appointment_early or actual_arrival) + 2h; billable = checkout - clock,
    capped at on-site time and at the $150 layover PER STOP; stops with on-site dwell beyond
    DROP_DWELL_H are treated as drops and skipped. Times are stop-local wall clock (correct
    for a within-stop duration; only a dwell crossing a DST change is off by 1h — negligible).
    McLeod actual times are APPROXIMATE — the POD is authoritative per load."""
    from collections import defaultdict
    per_order_all = defaultdict(float)     # per-stop cap, drops excluded (before eligibility)
    per_order_elig = defaultdict(float)    # + eligibility: exclude carrier-late stops
    drops = late_excluded = 0
    late_dollars = 0.0
    with open(stops_p, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
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
                if onsite_h > DROP_DWELL_H and billable_h > 0:
                    drops += 1
                continue
            dollars = min(billable_h * DET_RATE_HR, LAYOVER_CAP)  # cap per stop
            oid = clean(r.get("order_id"))
            per_order_all[oid] += dollars
            # ELIGIBILITY: if the carrier arrived AFTER its appointment, the delay is
            # carrier-caused -> not owed per the Rate Confirmation. Stops with no
            # appointment stay eligible (clock ran from arrival).
            if appt_late is not None and arr > appt_late:
                late_excluded += 1
                late_dollars += dollars
            else:
                per_order_elig[oid] += dollars

    def summarize(per_order):
        gap = {o: d for o, d in per_order.items() if o not in det_billed_orders}
        return sum(per_order.values()), sum(gap.values()), len(gap)

    ent_all, gap_all, n_all = summarize(per_order_all)     # per-stop cap, pre-eligibility
    ent_el, gap_el, n_el = summarize(per_order_elig)       # eligibility-adjusted (FINAL)
    print("-" * 74)
    print(" DETENTION — APPOINTMENT-BASED, PER-STOP, ELIGIBILITY-ADJUSTED (from stops.csv)")
    print(f"   Rule: 2h free + $150 cap PER STOP; clock = appointment+2h (else arrival+2h).")
    print(f"   Pre-eligibility un-billed:                 ${gap_all:,.0f}   ({n_all:,} loads)")
    print(f"   Carrier-late stops removed (fault):        {late_excluded:,} stops, ${late_dollars:,.0f}")
    print(f"   ELIGIBILITY-ADJUSTED un-billed detention:  ${gap_el:,.0f}   ({n_el:,} loads)")
    print(f"   Eligibility-adjusted TOTAL entitled:       ${ent_el:,.0f}")
    print("   Still uses McLeod times (approximate); signed-doc check is per-event via the")
    print("   POD (pod_reader.py) — some eligible detention will need that proof to bill.")


def main(loads_p, oc_p, cp_p, cc_p, stops_p=None):
    # ---- charge codes ----
    fuel_codes = set()
    with open(cc_p, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if clean(r.get("is_fuel_surcharge")) == "Y":
                fuel_codes.add(clean(r.get("charge_id")))

    # ---- loads ----
    loads = {}
    rc_missing = 0
    with open(loads_p, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            pro = clean(r.get("pro_number"))
            dwell = num(r.get("max_dwell_minutes"))
            rc = clean(r.get("rate_confirmation_sent_date"))
            if rc in ("", "NULL"):
                rc_missing += 1
            loads[pro] = {
                "customer": clean(r.get("customer")),
                "dwell": dwell,
                "late": clean(r.get("any_late_arrival")) == "1",
                "linehaul": num(r.get("linehaul_rate")),
                "carrier_pay": num(r.get("carrier_total_pay")),
            }
    n_loads = len(loads)

    # ---- customer other_charge: accessorial billed by category ----
    cust_billed = defaultdict(float)
    det_billed_orders = set()
    with open(oc_p, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            code = clean(r.get("charge_id"))
            if code in fuel_codes:
                continue
            cat = CODE_CATEGORY.get(code)
            if cat is None:
                continue
            amt = num(r.get("amount"))
            cust_billed[cat] += amt
            if cat == "detention":
                det_billed_orders.add(clean(r.get("order_id")))

    # ---- carrier driver_extra_pay: accessorial paid / deductions by category ----
    carr_paid = defaultdict(float)      # positive amounts (pay to carrier)
    carr_deduct = defaultdict(float)    # negative amounts (deducted from carrier)
    with open(cp_p, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            text = clean(r.get("descr")) + " " + clean(r.get("short_desc"))
            cat = carrier_category(text)
            if cat is None:
                continue
            amt = num(r.get("amount"))
            if amt >= 0:
                carr_paid[cat] += amt
            else:
                carr_deduct[cat] += -amt

    # ---- detention capture gap ----
    long_dwell = [(p, d) for p, d in loads.items() if d["dwell"] > FREE_MIN]
    gap_orders = [(p, d) for p, d in long_dwell if p not in det_billed_orders]
    indic_dollars = 0.0
    for p, d in gap_orders:
        billable_h = (d["dwell"] - FREE_MIN) / 60.0
        indic_dollars += min(billable_h * DET_RATE_HR, LAYOVER_CAP)

    # ---- report ----
    print("=" * 74)
    print(" ACCESSORIAL LEAKAGE — REAL McLeod DATA (delivered loads, 365-day pull)")
    print("=" * 74)
    print(f" Delivered loads analyzed:      {n_loads:,}")
    print(f" Rate-con sent date MISSING on: {rc_missing:,} loads "
          f"({rc_missing/n_loads*100:.1f}%)  <- control-gap the contract requires")
    print("-" * 74)
    print(" ACCESSORIAL BILLED (customer) vs PAID (carrier), by category  [hard data]")
    print(f"   {'category':<14}{'cust billed':>14}{'carrier paid':>14}"
          f"{'deducted':>12}{'margin':>13}")
    tot_bill = tot_pay = tot_ded = 0.0
    for c in ACCESSORIAL_CATS:
        b, p, dd = cust_billed.get(c, 0.0), carr_paid.get(c, 0.0), carr_deduct.get(c, 0.0)
        margin = b - p + dd
        tot_bill += b; tot_pay += p; tot_ded += dd
        print(f"   {c:<14}{b:>14,.2f}{p:>14,.2f}{dd:>12,.2f}{margin:>13,.2f}")
    print(f"   {'TOTAL':<14}{tot_bill:>14,.2f}{tot_pay:>14,.2f}{tot_ded:>12,.2f}"
          f"{tot_bill - tot_pay + tot_ded:>13,.2f}")
    print("-" * 74)
    print(" DETENTION CAPTURE GAP")
    print(f"   Loads with worst-stop dwell > {FREE_MIN} min (beyond free time): {len(long_dwell):,}")
    print(f"   ...of those with NO customer detention charge:                 {len(gap_orders):,}")
    print(f"   Indicative un-billed detention @ ${DET_RATE_HR:.0f}/h capped ${LAYOVER_CAP:.0f}: "
          f"${indic_dollars:,.0f}")
    print("   (INDICATIVE: worst-stop dwell includes legitimate load/unload time and")
    print("    carrier-fault eligibility is unknown here, so treat as an upper bound.)")
    print("=" * 74)

    # top customers by detention gap (for the chat summary; not written to disk)
    by_cust = defaultdict(lambda: [0, 0.0])
    for p, d in gap_orders:
        billable_h = (d["dwell"] - FREE_MIN) / 60.0
        by_cust[loads[p]["customer"]][0] += 1
        by_cust[loads[p]["customer"]][1] += min(billable_h * DET_RATE_HR, LAYOVER_CAP)
    print(" Top 10 customers by indicative un-billed detention:")
    for cust, (cnt, dollars) in sorted(by_cust.items(), key=lambda x: -x[1][1])[:10]:
        print(f"   {cust[:44]:<46} {cnt:>5} loads  ${dollars:>9,.0f}")

    if stops_p:
        detention_from_stops(stops_p, det_billed_orders)


if __name__ == "__main__":
    if len(sys.argv) not in (5, 6):
        print(__doc__)
        print("\nUsage: analyze_leakage.py loads.csv othercharges.csv carrierpay.csv "
              "chargecodes.csv [stops.csv]")
        sys.exit(1)
    main(*sys.argv[1:])
