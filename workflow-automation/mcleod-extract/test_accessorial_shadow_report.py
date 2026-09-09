"""Offline tests for accessorial_shadow_report (synthetic CSVs; no McLeod needed)."""

import csv
import os
import tempfile

import accessorial_shadow_report as R

LOADS_HDR = ["pro_number", "delivered_date", "customer_id", "customer", "curr_movement_id",
             "override_payee_id", "carrier", "linehaul_rate", "otherchargetotal",
             "total_charge", "carrier_total_pay", "rate_confirmation_status",
             "rate_confirmation_sent_date", "equipment_type_id", "stop_check_in",
             "stop_check_out", "max_dwell_minutes", "any_late_arrival", "stop_count"]
OC_HDR = ["order_id", "charge_id", "descr", "bill_type", "amount", "rate", "units", "stop_id"]
CP_HDR = ["order_id", "movement_id", "payee_id", "deduct_code_id", "descr", "short_desc",
          "amount", "rate", "units", "transaction_date"]
CC_HDR = ["charge_id", "descr", "is_fuel_surcharge", "glid"]
STOPS_HDR = ["order_id", "stop_type", "order_sequence", "appointment_early", "appointment_late",
             "actual_arrival", "actual_departure", "appt_required"]


def _w(hdr, rows):
    fd, path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(hdr)
        for r in rows:
            w.writerow([r.get(h, "") for h in hdr])
    return path


def _fixture():
    loads = _w(LOADS_HDR, [
        {"pro_number": "L1", "customer": "ACME", "linehaul_rate": "1000",
         "carrier_total_pay": "800", "rate_confirmation_sent_date": ""},            # rc MISSING
        {"pro_number": "L2", "customer": "ACME", "linehaul_rate": "1200",
         "carrier_total_pay": "900", "rate_confirmation_sent_date": "2026-08-05 10:00:00"},
    ])
    oc = _w(OC_HDR, [
        {"order_id": "L1", "charge_id": "LMP", "descr": "Lumper", "amount": "100"},
        {"order_id": "L2", "charge_id": "DET", "descr": "Detention", "amount": "150"},  # L2 billed detention
    ])
    cp = _w(CP_HDR, [
        {"order_id": "L1", "descr": "Lumper", "amount": "120"},                     # paid > billed -> neg margin
        {"order_id": "L2", "descr": "Detention", "deduct_code_id": "X", "amount": "-50"},  # deduction
    ])
    cc = _w(CC_HDR, [
        {"charge_id": "FSC", "descr": "Fuel Surcharge", "is_fuel_surcharge": "Y"},
        {"charge_id": "DET", "descr": "Detention", "is_fuel_surcharge": "N"},
        {"charge_id": "LMP", "descr": "Lumper", "is_fuel_surcharge": "N"},
    ])
    stops = _w(STOPS_HDR, [
        # L1 stop1: eligible detention 1.5h -> $52.50 (arrival == appt, not late)
        {"order_id": "L1", "order_sequence": "1", "appointment_early": "2026-08-01 08:00:00",
         "appointment_late": "2026-08-01 08:00:00", "actual_arrival": "2026-08-01 08:00:00",
         "actual_departure": "2026-08-01 11:30:00"},
        # L1 stop2: carrier LATE (arr 13:00 > appt 12:00) -> $35, pre-elig only, excluded from eligible
        {"order_id": "L1", "order_sequence": "2", "appointment_early": "2026-08-01 12:00:00",
         "appointment_late": "2026-08-01 12:00:00", "actual_arrival": "2026-08-01 13:00:00",
         "actual_departure": "2026-08-01 15:00:00"},
        # L2 stop: detention exists but L2 already billed detention -> excluded from the gap
        {"order_id": "L2", "order_sequence": "1", "appointment_early": "2026-08-02 08:00:00",
         "appointment_late": "2026-08-02 08:00:00", "actual_arrival": "2026-08-02 08:00:00",
         "actual_departure": "2026-08-02 11:30:00"},
    ])
    return loads, oc, cp, cc, stops


def _run():
    paths = _fixture()
    try:
        return R.build_shadow_report(*paths, month="2026-08"), paths
    finally:
        pass  # cleaned by caller


def _cleanup(paths):
    for p in paths:
        if os.path.exists(p):
            os.remove(p)


def _approx(a, b, tol=0.01):
    return abs(a - b) <= tol


def test_overview_and_ratecon_gap():
    rep, paths = _run()
    try:
        assert rep["overview"]["loads"] == 2
        assert rep["overview"]["rc_missing"] == 1
        assert _approx(rep["overview"]["rc_missing_pct"], 50.0)
        assert rep["ratecon_gap"]["missing"] == 1 and rep["ratecon_gap"]["loads"] == ["L1"]
    finally:
        _cleanup(paths)


def test_detention_gap_eligibility_and_billed_exclusion():
    rep, paths = _run()
    try:
        d = rep["detention"]
        # L1: eligible stop $52.50; carrier-late stop $35 counts pre-elig only
        assert _approx(d["pre_elig_gap"], 87.5) and d["pre_elig_loads"] == 1
        assert d["late_excluded_stops"] == 1 and _approx(d["late_dollars"], 35.0)
        assert _approx(d["elig_gap"], 52.5) and d["elig_loads"] == 1
        # L2 is excluded from the gap because it already carries a detention charge
        assert "L2" not in d["by_order"] and "L1" in d["by_order"]
        assert d["by_customer"][0]["customer"] == "ACME"
        assert d["by_customer"][0]["loads"] == 1 and _approx(d["by_customer"][0]["dollars"], 52.5)
    finally:
        _cleanup(paths)


def test_category_margin_and_negative_bucket():
    rep, paths = _run()
    try:
        cats = {c["category"]: c for c in rep["categories"]}
        assert _approx(cats["lumper"]["billed"], 100) and _approx(cats["lumper"]["paid"], 120)
        assert _approx(cats["lumper"]["margin"], -20)                 # paid > billed
        assert _approx(cats["detention"]["billed"], 150)
        assert _approx(cats["detention"]["deducted"], 50)
        assert _approx(cats["detention"]["margin"], 200)             # 150 - 0 + 50
        assert _approx(cats["TOTAL"]["margin"], 180)                 # -20 + 200
        negs = {c["category"] for c in rep["negative_margin"]}
        assert negs == {"lumper"}
    finally:
        _cleanup(paths)


def test_render_text_smoke():
    rep, paths = _run()
    try:
        txt = R.render_text(rep)
        assert "ACCESSORIAL SHADOW REPORT - 2026-08" in txt
        assert "RATE-CON CONTROL GAP" in txt
        assert "GEGW" not in txt
    finally:
        _cleanup(paths)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
