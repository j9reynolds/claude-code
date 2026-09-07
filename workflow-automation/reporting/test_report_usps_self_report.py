"""Tests for the USPS GEGW Self Report generator — the on-time / measurable-total rules.

Run: python3 -m pytest test_report_usps_self_report.py   (or python3 test_report_usps_self_report.py)
"""

import csv
import os
import tempfile

import report_usps_self_report as R

_COLS = ["order_id", "lane_number", "pu_city", "pu_state", "pu_sched", "pu_arrival",
         "pu_departure", "so_city", "so_state", "so_sched", "so_arrival", "comment"]


def _csv(rows):
    fd, path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=_COLS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in _COLS})
    return path


def _row(**kw):
    base = dict(order_id="1", lane_number="89", pu_city="Philadelphia", pu_state="PA",
                so_city="Phoenix", so_state="AZ")
    base.update(kw)
    return base


def _build(rows):
    p = _csv(rows)
    try:
        return R.build_report(p, "2026-08")
    finally:
        os.remove(p)


def test_dt_parses_formats_and_blanks():
    assert R._dt("2026-08-12 15:00:00") is not None
    assert R._dt("2026-08-12T15:00:00.123") is not None      # ISO + fractional
    assert R._dt("08/12/2026 15:00") is not None
    assert R._dt("") is None and R._dt("   ") is None and R._dt("garbage") is None


def test_on_time_and_late_arrival():
    rep = _build([
        _row(order_id="1", pu_sched="2026-08-12 15:00", pu_arrival="2026-08-12 14:30",
             pu_departure="2026-08-12 15:00", so_sched="2026-08-13 09:00",
             so_arrival="2026-08-13 08:00"),                      # all on time
        _row(order_id="2", pu_sched="2026-08-12 15:00", pu_arrival="2026-08-12 16:30",
             pu_departure="2026-08-12 17:00", so_sched="2026-08-13 09:00",
             so_arrival="2026-08-13 10:30"),                      # all late
    ])
    lane = rep["lanes"][0]
    assert lane["load_count"] == 2
    assert lane["otp"] == {"on": 1, "total": 2, "pct": 50.0}
    assert lane["ot_dispatch"] == {"on": 1, "total": 2, "pct": 50.0}
    assert lane["otd"] == {"on": 1, "total": 2, "pct": 50.0}


def test_equal_timestamp_counts_on_time():
    rep = _build([_row(pu_sched="2026-08-12 15:00", pu_arrival="2026-08-12 15:00",
                       pu_departure="2026-08-12 15:00", so_sched="2026-08-13 09:00",
                       so_arrival="2026-08-13 09:00")])
    lane = rep["lanes"][0]
    assert lane["otp"]["on"] == 1 and lane["otd"]["on"] == 1 and lane["ot_dispatch"]["on"] == 1


def test_missing_data_excluded_from_total_but_counted_in_loads():
    rep = _build([
        _row(order_id="1", pu_sched="2026-08-12 15:00", pu_arrival="2026-08-12 14:00",
             pu_departure="", so_sched="2026-08-13 09:00", so_arrival=""),   # no depart, no SO arrival
    ])
    lane = rep["lanes"][0]
    assert lane["load_count"] == 1
    assert lane["otp"] == {"on": 1, "total": 1, "pct": 100.0}       # measurable
    assert lane["ot_dispatch"]["total"] == 0 and lane["ot_dispatch"]["pct"] is None
    assert lane["otd"]["total"] == 0 and lane["otd"]["pct"] is None


def test_lane_grouping_and_labels():
    rep = _build([
        _row(order_id="1", lane_number="89"),
        _row(order_id="2", lane_number="89"),
        _row(order_id="3", lane_number="", pu_city="Memphis", pu_state="TN",
             so_city="Dallas", so_state="TX"),
    ])
    labels = [l["lane"] for l in rep["lanes"]]
    assert "Philadelphia, PA - Phoenix, AZ (89)" in labels        # with lane number
    assert "Memphis, TN - Dallas, TX" in labels                   # no lane number -> no parens
    phx = next(l for l in rep["lanes"] if l["lane"].startswith("Philadelphia"))
    assert phx["load_count"] == 2


def test_lanes_sorted_by_volume_desc():
    rep = _build([
        _row(order_id="1", pu_city="A", so_city="B"),
        _row(order_id="2", pu_city="C", so_city="D"),
        _row(order_id="3", pu_city="C", so_city="D"),
    ])
    assert rep["lanes"][0]["lane"].startswith("C,")               # 2-load lane first
    assert rep["lanes"][0]["load_count"] == 2


def test_totals_row_sums_all_lanes():
    rep = _build([
        _row(order_id="1", pu_city="A", so_city="B", pu_sched="2026-08-01 10:00",
             pu_arrival="2026-08-01 09:00", pu_departure="2026-08-01 10:00",
             so_sched="2026-08-02 10:00", so_arrival="2026-08-02 09:00"),
        _row(order_id="2", pu_city="C", so_city="D", pu_sched="2026-08-01 10:00",
             pu_arrival="2026-08-01 11:00", pu_departure="2026-08-01 12:00",
             so_sched="2026-08-02 10:00", so_arrival="2026-08-02 11:00"),
    ])
    t = rep["totals"]
    assert t["lane"] == "TOTAL" and t["load_count"] == 2
    assert t["otp"] == {"on": 1, "total": 2, "pct": 50.0}


def test_comments_deduped_and_joined():
    rep = _build([
        _row(order_id="1", comment="Trailer issue; carrier"),
        _row(order_id="2", comment="Trailer issue; carrier"),     # dup -> collapsed
        _row(order_id="3", comment="Weather delay"),
    ])
    assert rep["lanes"][0]["comments"] == "Trailer issue; carrier; Weather delay"


def test_render_rows_shape_and_header():
    rep = _build([_row(pu_sched="2026-08-12 15:00", pu_arrival="2026-08-12 14:00",
                       pu_departure="2026-08-12 15:00", so_sched="2026-08-13 09:00",
                       so_arrival="2026-08-13 08:00")])
    rows = R.render_rows(rep)
    assert rows[0] == R._HEADERS and len(rows[0]) == 6
    assert rows[-1][0] == "TOTAL"                                 # last row is the total
    assert rows[1][1] == 1                                        # load count cell
    assert rows[1][2] == "1 / 1 / 100%"                           # OTP cell format


def test_empty_input():
    rep = _build([])
    assert rep["lanes"] == [] and rep["totals"]["load_count"] == 0
    assert rep["totals"]["otp"]["pct"] is None


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
