"""Tests for the USPS Self Report generator — the real JBH-extract aggregation.

Run: python3 -m pytest test_report_usps_self_report.py  (or python3 test_report_usps_self_report.py)
"""

import csv
import os
import tempfile

import report_usps_self_report as R

_COLS = ["lane", "otp_flag", "dispatch_flag", "otd_flag", "reason1", "load_id"]


def _csv(rows):
    fd, path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=_COLS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in _COLS})
    return path


def _row(lane="CINCINNATI, OH | DENVER, CO", otp="Y", disp="Y", otd="Y", reason1="", load_id=""):
    return dict(lane=lane, otp_flag=otp, dispatch_flag=disp, otd_flag=otd,
                reason1=reason1, load_id=load_id)


def _build(rows):
    p = _csv(rows)
    try:
        return R.build_report(p, "2026-08")
    finally:
        os.remove(p)


def test_flag_normalisation():
    assert R._flag("Y") == "Y" and R._flag(" y ") == "Y"
    assert R._flag("N") == "N"
    assert R._flag("Order is VOID") == "VOID" and R._flag("") == "VOID" and R._flag("x") == "VOID"


def test_single_percentage_per_metric():
    # 3 loads: OTP 2/3, all dispatch Y, OTD 1/3
    rep = _build([
        _row(otp="Y", disp="Y", otd="Y"),
        _row(otp="Y", disp="Y", otd="N"),
        _row(otp="N", disp="Y", otd="N"),
    ])
    lane = rep["lanes"][0]
    assert lane["load_count"] == 3
    assert lane["otp_pct"] == 67          # round(100*2/3)
    assert lane["ot_dispatch_pct"] == 100
    assert lane["otd_pct"] == 33          # round(100*1/3)


def test_void_counts_in_denominator_not_numerator():
    # 2 loads, 1 VOID -> OTP = 1 Y / 2 = 50% (matches the workbook's per-lane formula)
    rep = _build([
        _row(otp="Y", disp="Y", otd="Y"),
        _row(otp="Order is VOID", disp="Order is VOID", otd="Order is VOID"),
    ])
    lane = rep["lanes"][0]
    assert lane["load_count"] == 2
    assert lane["otp_pct"] == 50 and lane["ot_dispatch_pct"] == 50 and lane["otd_pct"] == 50


def test_all_void_lane_is_zero_percent():
    rep = _build([_row(otp="Order is VOID", disp="Order is VOID", otd="Order is VOID")])
    lane = rep["lanes"][0]
    assert lane["load_count"] == 1
    assert lane["otp_pct"] == 0 and lane["otd_pct"] == 0


def test_lanes_sorted_alphabetically():
    rep = _build([_row(lane="ZZZ, TX | AAA, CA"), _row(lane="AAA, CA | BBB, TX")])
    assert [l["lane"] for l in rep["lanes"]] == ["AAA, CA | BBB, TX", "ZZZ, TX | AAA, CA"]


def test_total_is_unweighted_mean_of_lane_pcts():
    # lane A: 1 load 100% OTP ; lane B: 4 loads 0% OTP
    # unweighted mean = (100+0)/2 = 50  (NOT load-weighted 20)
    rep = _build(
        [_row(lane="A, X | B, Y", otp="Y")] +
        [_row(lane="C, X | D, Y", otp="N") for _ in range(4)]
    )
    assert rep["totals"]["load_count"] == 5
    assert rep["totals"]["otp_pct"] == 50
    assert rep["overall_load_weighted"]["otp_pct"] == 20    # cross-ref weighted figure


def test_comments_from_reason_codes_deduped():
    rep = _build([
        _row(reason1="POSTAL"), _row(reason1="POSTAL"), _row(reason1="Carrier"),
    ])
    assert rep["lanes"][0]["comments"] == "POSTAL; Carrier"


def test_tolerant_headers_match_sheet_labels():
    # use the workbook's exact header labels
    fd, path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["O/D PAIR", "ON TIME Arrival Y/N", "Dispatch on time Y/N",
                    "ON TIME DELIVERY y/n"])
        w.writerow(["CINCINNATI, OH | DENVER, CO", "Y", "N", "Y"])
    try:
        rep = R.build_report(path, "2026-08")
    finally:
        os.remove(path)
    lane = rep["lanes"][0]
    assert lane["otp_pct"] == 100 and lane["ot_dispatch_pct"] == 0 and lane["otd_pct"] == 100


def test_render_rows_shape_and_percent_format():
    rep = _build([_row(otp="Y", disp="N", otd="Y")])
    rows = R.render_rows(rep)
    assert rows[0] == R._HEADERS and len(rows[0]) == 6
    assert rows[1][2] == "100%" and rows[1][3] == "0%"      # single-% cells
    assert rows[-1][0] == "TOTAL"


def test_missing_lane_column_raises():
    fd, path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerow(["foo", "bar"])
    try:
        raised = False
        try:
            R.build_report(path, "2026-08")
        except ValueError:
            raised = True
        assert raised
    finally:
        os.remove(path)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
