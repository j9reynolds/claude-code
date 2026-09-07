"""USPS GEGW "Self Report" — generator (opportunity #6, recurring reports).

Reproduces the monthly report Delta files to J.B. Hunt for the USPS GEGW surface
network (tender 0029H) — today hand-built in an .xlsb macro workbook titled
"J.B. Hunt Transport GEGW Performance Overview". It is an INTERNAL/partner report
(to J.B. Hunt), not a customer-facing narrative.

Layout it reproduces (one row per lane, plus a TOTAL row):

    Lane                              | Load Count | OTP           | OT Dispatch   | OTD           | Comments
                                      |            | On Time/Tot/% | On Time/Tot/% | On Time/Tot/% |
    Philadelphia, PA - Phoenix, AZ(89)|     6      |   5 / 6 / 83% |   6 / 6 /100% |   5 / 6 / 83% | Trailer issue; carrier

Three performance metrics, each computed as On-Time / Measurable-Total / %:
  * OTP         On-Time Pickup   — carrier ARRIVED at origin by the scheduled time.
  * OT Dispatch On-Time Dispatch — carrier DEPARTED origin by the scheduled time.
  * OTD         On-Time Delivery — carrier ARRIVED at destination by the scheduled time.

Business rules (see recurring-reports-spec.md — confirm with the account owner):
  * Scheduled reference = the ORIGINAL tender time when present (OrigSchedLate),
    else the current appointment late window (SchedArriveLate). USPS/JBH scores the
    original commitment; a reschedule does not erase a miss. The SQL emits this
    already-coalesced as *_sched.
  * "On time" = actual <= scheduled (arrival for OTP/OTD, departure for Dispatch).
  * "Measurable total" for a metric = loads on the lane that have BOTH the actual and
    the scheduled timestamp. A load missing either is counted in Load Count but not in
    that metric's total, so the % is never inflated or deflated by missing data.

Pure / no side effects: computes and returns a dict; render_text() / render_rows()
format it. Delivery (Outlook draft for the owner to review) is a separate, gated step.

Runs off either:
  * the DGLIQ per-load query output (production — report_usps_self_report.sql), or
  * any CSV with the columns below (offline/sample runs).

Input CSV columns (one row per order/load):
  order_id, lane_number(optional),
  pu_city, pu_state, pu_sched, pu_arrival, pu_departure,
  so_city, so_state, so_sched, so_arrival,
  comment(optional)

Usage (offline):
  python3 report_usps_self_report.py stops.csv 2026-08
"""

from __future__ import annotations

import csv
import sys
from collections import OrderedDict
from datetime import datetime


def _cl(v):
    return (v or "").strip().strip('"').strip()


def _dt(v):
    """Parse a McLeod/SQL datetime; return None if blank/unparseable."""
    s = _cl(v)
    if not s:
        return None
    s = s.replace("T", " ")
    if "." in s:                       # drop fractional seconds
        s = s.split(".", 1)[0]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m/%d/%Y %H:%M:%S",
                "%m/%d/%Y %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


class _Metric:
    """On-Time / Measurable-Total counter for one lane + one metric."""
    __slots__ = ("on", "total")

    def __init__(self):
        self.on = 0
        self.total = 0

    def observe(self, actual, sched):
        if actual is None or sched is None:
            return                      # not measurable — excluded from total
        self.total += 1
        if actual <= sched:
            self.on += 1

    def pct(self):
        return round(100.0 * self.on / self.total, 0) if self.total else None

    def cell(self):
        p = self.pct()
        return f"{self.on} / {self.total} / {'' if p is None else str(int(p)) + '%'}".strip()


class _Lane:
    __slots__ = ("label", "loads", "otp", "disp", "otd", "comments")

    def __init__(self, label):
        self.label = label
        self.loads = 0
        self.otp = _Metric()
        self.disp = _Metric()
        self.otd = _Metric()
        self.comments = []

    def add(self, row):
        self.loads += 1
        pu_arr, pu_dep = _dt(row.get("pu_arrival")), _dt(row.get("pu_departure"))
        pu_sch = _dt(row.get("pu_sched"))
        so_arr, so_sch = _dt(row.get("so_arrival")), _dt(row.get("so_sched"))
        self.otp.observe(pu_arr, pu_sch)
        self.disp.observe(pu_dep, pu_sch)
        self.otd.observe(so_arr, so_sch)
        c = _cl(row.get("comment"))
        if c and c not in self.comments:
            self.comments.append(c)

    def as_dict(self):
        return {
            "lane": self.label, "load_count": self.loads,
            "otp": {"on": self.otp.on, "total": self.otp.total, "pct": self.otp.pct()},
            "ot_dispatch": {"on": self.disp.on, "total": self.disp.total, "pct": self.disp.pct()},
            "otd": {"on": self.otd.on, "total": self.otd.total, "pct": self.otd.pct()},
            "comments": "; ".join(self.comments),
        }


def _lane_label(row):
    base = f"{_cl(row.get('pu_city'))}, {_cl(row.get('pu_state'))} - " \
           f"{_cl(row.get('so_city'))}, {_cl(row.get('so_state'))}"
    ln = _cl(row.get("lane_number"))
    return f"{base} ({ln})" if ln else base


def build_report(stops_csv, month):
    """month = 'YYYY-MM' (informational header only; filter the CSV upstream via SQL).
    Returns {title, month, lanes:[...], totals:{...}}."""
    lanes = OrderedDict()
    with open(stops_csv, encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            label = _lane_label(row)
            if label not in lanes:
                lanes[label] = _Lane(label)
            lanes[label].add(row)

    ordered = sorted(lanes.values(), key=lambda l: (-l.loads, l.label))
    tot = _Lane("TOTAL")
    for l in ordered:
        tot.loads += l.loads
        for m_dst, m_src in ((tot.otp, l.otp), (tot.disp, l.disp), (tot.otd, l.otd)):
            m_dst.on += m_src.on
            m_dst.total += m_src.total

    return {
        "title": "J.B. Hunt Transport GEGW Performance Overview",
        "month": month,
        "lanes": [l.as_dict() for l in ordered],
        "totals": tot.as_dict(),
        "lane_count": len(ordered),
    }


_HEADERS = ("Lane", "Load Count", "OTP (on/tot/%)", "OT Dispatch (on/tot/%)",
            "OTD (on/tot/%)", "Comments")


def render_rows(rep):
    """Return the report as a list of row tuples matching the .xlsb column order."""
    def _cells(d):
        def c(m):
            p = m["pct"]
            return f"{m['on']} / {m['total']} / {'' if p is None else str(int(p)) + '%'}"
        return (d["lane"], d["load_count"], c(d["otp"]), c(d["ot_dispatch"]),
                c(d["otd"]), d["comments"])
    rows = [_HEADERS]
    rows.extend(_cells(l) for l in rep["lanes"])
    rows.append(_cells(rep["totals"]))
    return rows


def render_text(rep) -> str:
    rows = [tuple(str(c) for c in r) for r in render_rows(rep)]
    widths = [max(len(r[i]) for r in rows) for i in range(len(_HEADERS))]
    line = lambda r: "  ".join(c.ljust(widths[i]) for i, c in enumerate(r))
    out = [f"{rep['title']} — {rep['month']}  ({rep['lane_count']} lanes)",
           line(rows[0]), "  ".join("-" * w for w in widths)]
    out.extend(line(r) for r in rows[1:])
    return "\n".join(out)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    print(render_text(build_report(sys.argv[1], sys.argv[2])))
