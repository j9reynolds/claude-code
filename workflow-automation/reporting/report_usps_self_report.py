"""USPS "Self Report" — generator (opportunity #6, recurring reports).

Reproduces the monthly report Delta files to J.B. Hunt for the USPS surface network
(tender 0029H), the workbook titled "<Program> Performance Overview" (Overview Summary
By Lane).

IMPORTANT — data source (confirmed by reading a real filed report, May 2026):
This report is NOT built from McLeod. It is built from the **J.B. Hunt data extract**
("<Program> Raw Data With Reason Codes" tab): one row per load carrying JBH's own
Contract ID (0029H), SV Trip ID, Load ID, O/D PAIR, and JBH's scheduled/planned vs
actual times, from which three **Y / N / "Order is VOID"** on-time flags are set, plus
up to three reason codes. Delta staff fill the reason codes; the Overview tab just
aggregates the flags per lane. So the authoritative on-time numbers are JBH's, and this
generator consumes that raw extract — it does not recompute on-time from McLeod times.
(McLeod can serve as an independent cross-check; see report_usps_self_report.sql.)

Overview layout it reproduces (one row per lane + a TOTAL row):

    Lane                              | Load Count | OTP  | OT Dispatch | OTD  | Comments
    CINCINNATI, OH | DENVER, CO       |    36      | 89%  |    94%      | 69%  | ...

Exact aggregation (matches the workbook's COUNTIF/COUNTIFS formulas):
  * Load Count = every raw row for the lane, INCLUDING "Order is VOID" rows.
  * OTP%  = count(ON TIME Arrival == "Y")  / Load Count      (single percentage)
    OT Dispatch% = count(Dispatch on time == "Y") / Load Count
    OTD%  = count(ON TIME DELIVERY == "Y") / Load Count
    (A VOID row is in the denominator but is never a "Y", so it lowers the lane's %,
    exactly as the sheet does.)
  * TOTAL row % = the UNWEIGHTED mean of the per-lane percentages (full precision),
    which is what the workbook's total row shows — not a load-weighted average.
  * A void-excluded overall figure (JBH's headline S/T/U cells) is also returned as
    `overall_void_excluded` for cross-reference.

Cells display as a single rounded percentage — the report shows "89%", not counts.

Pure / no side effects. Delivery (fill the template, Outlook draft for review) is a
separate, gated step. Verified to reproduce the real May-2026 Overview exactly
(27/27 lanes, 0 differences) from that month's raw-data tab.

Input CSV columns (one row per load; from the JBH raw-data extract). Header names are
matched case-insensitively and tolerate the sheet's exact labels:
  lane            (aka "O/D PAIR")
  otp_flag        (aka "ON TIME Arrival Y/N")
  dispatch_flag   (aka "Dispatch on time Y/N")
  otd_flag        (aka "ON TIME DELIVERY y/n")
  reason1, reason2, reason3   (optional; -> Comments)
  load_id         (optional)

Usage (offline):
  python3 report_usps_self_report.py raw_data.csv 2026-08
"""

from __future__ import annotations

import csv
import sys
from collections import OrderedDict

VOID = "order is void"

# tolerant header aliases -> canonical field
_ALIASES = {
    "lane": "lane", "o/d pair": "lane", "od pair": "lane", "o/d": "lane",
    "otp_flag": "otp", "on time arrival y/n": "otp", "on time arrival": "otp",
    "ot arrival y/n": "otp",
    "dispatch_flag": "disp", "dispatch on time y/n": "disp",
    "dispatch on time": "disp", "ot dispatch y/n": "disp",
    "otd_flag": "otd", "on time delivery y/n": "otd", "on time delivery": "otd",
    "ot delivery y/n": "otd",
    "reason1": "r1", "reason 1": "r1", "reason2": "r2", "reason 2": "r2",
    "reason3": "r3", "reason 3": "r3",
    "load_id": "load_id", "load id": "load_id",
}


def _cl(v):
    return (v or "").strip().strip('"').strip()


def _flag(v):
    """Return 'Y', 'N', or 'VOID' (VOID also for blank/unknown).
    Tolerant of numeric flags: 1 -> Y, 0 -> N (some extracts carry 1/0 columns)."""
    s = _cl(v).lower()
    if s in ("y", "1", "1.0"):
        return "Y"
    if s in ("n", "0", "0.0"):
        return "N"
    return "VOID"                       # "Order is VOID", 'V', blank, or anything else


def _norm_headers(fieldnames):
    out = {}
    for fn in fieldnames or []:
        key = _ALIASES.get(_cl(fn).lower())
        if key:
            out[key] = fn
    return out


class _Lane:
    __slots__ = ("label", "n", "otp", "disp", "otd", "comments")

    def __init__(self, label):
        self.label = label
        self.n = 0
        self.otp = self.disp = self.otd = 0      # count of "Y"
        self.comments = []

    def add(self, otp, disp, otd, reasons):
        self.n += 1
        if otp == "Y":
            self.otp += 1
        if disp == "Y":
            self.disp += 1
        if otd == "Y":
            self.otd += 1
        for r in reasons:
            r = _cl(r)
            if r and r not in self.comments:
                self.comments.append(r)

    def fracs(self):
        return (self.otp / self.n, self.disp / self.n, self.otd / self.n) if self.n else (None, None, None)

    def as_dict(self):
        fo, fd, ft = self.fracs()
        pc = lambda f: None if f is None else round(100 * f)
        return {
            "lane": self.label, "load_count": self.n,
            "otp_pct": pc(fo), "ot_dispatch_pct": pc(fd), "otd_pct": pc(ft),
            "otp_yes": self.otp, "ot_dispatch_yes": self.disp, "otd_yes": self.otd,
            "comments": "; ".join(self.comments),
        }


def build_report(raw_csv, month, program="RTH"):
    """month = 'YYYY-MM' (header only). Returns {title, month, lanes, totals, ...}."""
    lanes = OrderedDict()
    with open(raw_csv, encoding="utf-8-sig") as fh:
        rd = csv.DictReader(fh)
        h = _norm_headers(rd.fieldnames)
        if "lane" not in h:
            raise ValueError(f"input needs a lane / 'O/D PAIR' column; saw {rd.fieldnames}")
        for row in rd:
            lane = _cl(row[h["lane"]])
            if not lane:
                continue
            L = lanes.setdefault(lane, _Lane(lane))
            L.add(_flag(row.get(h.get("otp", ""))),
                  _flag(row.get(h.get("disp", ""))),
                  _flag(row.get(h.get("otd", ""))),
                  [row.get(h.get(k, ""), "") for k in ("r1", "r2", "r3")])

    ordered = sorted(lanes.values(), key=lambda l: l.label)     # report lists lanes A-Z
    dicts = [l.as_dict() for l in ordered]

    # TOTAL row: count = sum; % = unweighted mean of per-lane fractions (workbook behaviour)
    n_total = sum(l.n for l in ordered)
    def mean_pct(idx):
        fs = [l.fracs()[idx] for l in ordered if l.n]
        return round(100 * sum(fs) / len(fs)) if fs else None
    totals = {"lane": "TOTAL", "load_count": n_total,
              "otp_pct": mean_pct(0), "ot_dispatch_pct": mean_pct(1), "otd_pct": mean_pct(2),
              "comments": ""}

    # Load-weighted overall (Y / all-rows-incl-void), for cross-reference only.
    overall = {
        "otp_pct": (round(100 * sum(l.otp for l in ordered) /
                    sum(l.n for l in ordered)) if n_total else None),
        "ot_dispatch_pct": (round(100 * sum(l.disp for l in ordered) /
                            sum(l.n for l in ordered)) if n_total else None),
        "otd_pct": (round(100 * sum(l.otd for l in ordered) /
                    sum(l.n for l in ordered)) if n_total else None),
        "note": "load-weighted incl VOID in denominator",
    }

    return {
        "title": f"{program} Performance Overview",
        "month": month, "lanes": dicts, "totals": totals,
        "overall_load_weighted": overall, "lane_count": len(ordered),
    }


_HEADERS = ("Lane", "Load Count", "OTP", "OT Dispatch", "OTD", "Comments")


def render_rows(rep):
    def cells(d):
        p = lambda v: "" if v is None else f"{v}%"
        return (d["lane"], d["load_count"], p(d["otp_pct"]),
                p(d["ot_dispatch_pct"]), p(d["otd_pct"]), d.get("comments", ""))
    rows = [_HEADERS]
    rows.extend(cells(l) for l in rep["lanes"])
    rows.append(cells(rep["totals"]))
    return rows


def render_text(rep) -> str:
    rows = [tuple(str(c) for c in r) for r in render_rows(rep)]
    w = [max(len(r[i]) for r in rows) for i in range(len(_HEADERS))]
    line = lambda r: "  ".join(c.ljust(w[i]) for i, c in enumerate(r))
    out = [f"{rep['title']} - {rep['month']}  ({rep['lane_count']} lanes)",
           line(rows[0]), "  ".join("-" * x for x in w)]
    out.extend(line(r) for r in rows[1:])
    return "\n".join(out)


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        print(__doc__)
        sys.exit(1)
    prog = sys.argv[3] if len(sys.argv) == 4 else "RTH"
    print(render_text(build_report(sys.argv[1], sys.argv[2], prog)))
