"""USPS Self Report pipeline — extract -> generator -> filled Overview  (opportunity #6).

End-to-end, dependency-free (Python standard library only, so it runs on any Delta host
with no installs — pip is not required):

  1. EXTRACT   read the J.B. Hunt raw-data extract (an .xlsx workbook — the monthly export,
               or the report workbook's "… Raw Data With Reason Codes" tab, or per-lane raw
               tabs) and pull the normalized per-load rows. Also accepts a .csv.
  2. GENERATE  run report_usps_self_report.build_report() — the verified aggregation
               (reproduced the real May 2026 report exactly, 27/27 lanes, 0 differences).
  3. FILL      write a standalone 3-tab **.xlsx** mirroring the filed workbook:
                 * "Overview Summary By Lane"  — Lane | Load Count | OTP | OT Dispatch | OTD | Comments
                 * "Overview Summary by Trip"  — TripID | Lane | Load Count | OTP | OT Dispatch | OTD | Route/HCR
                 * "Raw Data With Reason Codes" — the verbatim per-load export
               The two Overview tabs use live COUNTIF/COUNTIFS/AVERAGE formulas over the raw
               tab (cached values + fullCalcOnLoad), so they recompute if the raw data is
               edited — with formatting (bold shaded headers, thin borders, % number formats,
               column widths, bold totals). Open once in Excel to confirm before sending.

.xlsx here is read and written as zipped XML with the stdlib (zipfile + xml). It targets the
common shapes Excel produces (shared strings, inline strings, plain numbers). Open the
output once in Excel to confirm formatting before the first real send.

CLI (4th arg is an output file OR a folder — a folder auto-names the file canonically
"0029H Self Report - Delta Group Logistics - <Mon YYYY>.xlsx", where <Mon YYYY> is the data
month):
  python3 usps_selfreport_pipeline.py RAW.csv 2026-03 RTH  ./output          # -> ...Mar 2026.xlsx
  python3 usps_selfreport_pipeline.py RAW.csv 2026-03 RTH  MyReport.xlsx      # explicit name
"""

from __future__ import annotations

import csv
import os
import re
import sys
import zipfile
from datetime import datetime
from xml.etree import ElementTree as ET

import report_usps_self_report as R

# Canonical output name — only the "<Mon YYYY>" (the data month) ever changes.
FILENAME_PREFIX = "0029H Self Report - Delta Group Logistics - "


def report_filename(month):
    """'2026-03' -> '0029H Self Report - Delta Group Logistics - Mar 2026.xlsx'."""
    return FILENAME_PREFIX + datetime.strptime(month, "%Y-%m").strftime("%b %Y") + ".xlsx"


def _resolve_out(out, month):
    """If `out` is a directory (or lacks an .xlsx name), write the canonically-named file
    inside it; otherwise use `out` verbatim."""
    if out.lower().endswith(".xlsx") and not os.path.isdir(out):
        return out
    os.makedirs(out, exist_ok=True)
    return os.path.join(out, report_filename(month))

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


# --------------------------------------------------------------------------- read .xlsx
def _col_to_idx(ref):
    """'C7' -> 2 (0-based column index)."""
    letters = re.match(r"[A-Z]+", ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def _shared_strings(z):
    try:
        xml = z.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(xml)
    out = []
    for si in root.findall("m:si", _NS):
        # concatenate all <t> (handles rich-text runs)
        out.append("".join(t.text or "" for t in si.iter("{%s}t" % _NS["m"])))
    return out


def _sheet_targets(z):
    """Return [(sheet_name, worksheet_path)] in workbook order."""
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels_xml = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rid_to_target = {}
    for rel in rels_xml:
        rid_to_target[rel.get("Id")] = rel.get("Target")
    out = []
    for sh in wb.find("m:sheets", _NS):
        name = sh.get("name")
        rid = sh.get("{%s}id" % _NS["r"])
        tgt = rid_to_target.get(rid, "")
        if tgt and not tgt.startswith("/"):
            tgt = "xl/" + tgt.lstrip("/")
        out.append((name, tgt))
    return out


def read_xlsx_sheets(path):
    """path -> {sheet_name: [ [cell, cell, ...], ... ]}  (cells as strings)."""
    with zipfile.ZipFile(path) as z:
        strings = _shared_strings(z)
        result = {}
        for name, tgt in _sheet_targets(z):
            try:
                ws = ET.fromstring(z.read(tgt))
            except KeyError:
                continue
            rows = []
            data = ws.find("m:sheetData", _NS)
            if data is None:
                result[name] = rows
                continue
            for row in data.findall("m:row", _NS):
                cells = []
                for c in row.findall("m:c", _NS):
                    idx = _col_to_idx(c.get("r", "A1"))
                    t = c.get("t")
                    if t == "s":                                   # shared string
                        v = c.find("m:v", _NS)
                        val = strings[int(v.text)] if v is not None and v.text else ""
                    elif t == "inlineStr":
                        val = "".join(x.text or "" for x in c.iter("{%s}t" % _NS["m"]))
                    else:                                          # number / bool / str
                        v = c.find("m:v", _NS)
                        val = v.text if v is not None else ""
                    while len(cells) <= idx:
                        cells.append("")
                    cells[idx] = val or ""
                rows.append(cells)
            result[name] = rows
    return result


# --------------------------------------------------------------------------- extract raw
_RAW_HEADER_KEY = "o/d pair"
# priority-ordered aliases (lowercased). The human Y/N text columns win over numeric
# *_Flag columns, which the extract may also carry, so 1/0 flags never shadow Y/N.
_ALIAS = {
    "lane": ("o/d pair", "od pair", "lane"),
    "otp": ("on time arrival y/n", "ot arrival y/n", "otp_flag"),
    "disp": ("dispatch on time y/n", "ot dispatch y/n", "dispatch_flag"),
    "otd": ("on time delivery y/n", "ot delivery y/n", "otd_flag"),
    "load_id": ("load id", "load_id"),
    "trip": ("sv trip id", "tripid", "trip id", "sv trip"),
}


def _is_reason(h):
    h = (h or "").strip().lower()
    return h.startswith("reason") or "notes required" in h


def extract_raw_rows(path):
    """Read RAW rows from a .xlsx (any sheet carrying an 'O/D PAIR' header) or a .csv.
    Returns (rows, meta) where rows is a list of dicts the generator understands."""
    if path.lower().endswith((".csv", ".tsv", ".txt")):
        with open(path, encoding="utf-8-sig") as fh:
            first = fh.readline()
            fh.seek(0)
            delim = "\t" if first.count("\t") > first.count(",") else ","
            rd = csv.DictReader(fh, delimiter=delim)
            fields = rd.fieldnames or []
            low = [(f or "").strip().lower() for f in fields]

            def pick(field):                # first alias present wins (priority order)
                for a in _ALIAS[field]:
                    if a in low:
                        return fields[low.index(a)]
                return None

            cmap = {k: pick(k) for k in _ALIAS}
            reason_keys = [f for f in fields if _is_reason(f)]
            rows = []
            raw_rows = []                     # verbatim, for the Raw Data tab
            for r in rd:
                lane = (r.get(cmap["lane"] or "", "") or "").strip()
                if not lane:
                    continue
                rows.append({
                    "lane": lane,
                    "sv_trip_id": r.get(cmap["trip"] or "", ""),
                    "otp_flag": r.get(cmap["otp"] or "", ""),
                    "dispatch_flag": r.get(cmap["disp"] or "", ""),
                    "otd_flag": r.get(cmap["otd"] or "", ""),
                    "reason1": "; ".join(x for x in (r.get(k, "") for k in reason_keys) if str(x).strip()),
                    "load_id": r.get(cmap["load_id"] or "", ""),
                })
                raw_rows.append([r.get(f, "") for f in fields])
        return (rows, {"source": "csv", "sheets": [], "count": len(rows)},
                {"header": fields, "rows": raw_rows})

    sheets = read_xlsx_sheets(path)
    out, used = [], []
    raw_header, raw_rows = [], []             # verbatim, for the Raw Data tab
    for name, grid in sheets.items():
        # find the header row containing O/D PAIR
        hdr_i = next((i for i, row in enumerate(grid)
                      if any(_RAW_HEADER_KEY in (c or "").lower() for c in row)), None)
        if hdr_i is None:
            continue
        header = [(_c or "").strip() for _c in grid[hdr_i]]
        low = [h.lower() for h in header]

        def col(field):                    # first alias present wins (priority order)
            for a in _ALIAS[field]:
                if a in low:
                    return low.index(a)
            return None

        c_lane = col("lane")
        c_otp = col("otp")
        c_disp = col("disp")
        c_otd = col("otd")
        c_trip = col("trip")
        reason_cols = [i for i, h in enumerate(low) if _is_reason(h)]
        c_load = col("load_id")
        if c_lane is None:
            continue
        if not raw_header:
            raw_header = header
        n_before = len(out)
        for row in grid[hdr_i + 1:]:
            lane = (row[c_lane] if c_lane < len(row) else "").strip()
            if not lane:
                continue
            g = lambda i: (row[i] if (i is not None and i < len(row)) else "")
            out.append({
                "lane": lane,
                "sv_trip_id": g(c_trip),
                "otp_flag": g(c_otp), "dispatch_flag": g(c_disp), "otd_flag": g(c_otd),
                "reason1": "; ".join(x for x in (g(i) for i in reason_cols) if str(x).strip()),
                "load_id": g(c_load),
            })
            raw_rows.append([(row[i] if i < len(row) else "") for i in range(len(raw_header))])
        used.append({"sheet": name, "rows": len(out) - n_before})
    return (out, {"source": "xlsx", "sheets": used, "count": len(out)},
            {"header": raw_header, "rows": raw_rows})


# --------------------------------------------------------------------------- write .xlsx
def _xml_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _col_letter(idx):
    s = ""
    idx += 1
    while idx:
        idx, r = divmod(idx - 1, 26)
        s = chr(ord("A") + r) + s
    return s


RAW_SHEET = "Raw Data With Reason Codes"     # the Overview/by-Trip formulas reference this

# Palette matched to the filed template (Cambria; black title/header bars with white bold
# text; #BFBFBF bold total row; black thin borders; whole-% on lanes, 0.00% on totals).
# style indices: 0 default, 1 title, 2 header, 3 data, 4 data%, 5 total, 6 total%, 7 raw-data
_STYLES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<fonts count="5">'
    '<font><sz val="11"/><color rgb="FF000000"/><name val="Cambria"/></font>'                       # 0 body black
    '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Cambria"/></font>'                    # 1 white bold (header)
    '<font><b/><sz val="11"/><color rgb="FF000000"/><name val="Cambria"/></font>'                    # 2 black bold (total)
    '<font><b/><sz val="16"/><color rgb="FF000000"/><name val="Cambria"/></font>'                    # 3 title 16pt black bold
    '<font><b/><sz val="11"/><color rgb="FFFF0000"/><name val="Cambria"/></font></fonts>'            # 4 red bold ("Y")
    '<fills count="6">'
    '<fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FF000000"/><bgColor indexed="64"/></patternFill></fill>'   # 2 black
    '<fill><patternFill patternType="solid"><fgColor rgb="FFBFBFBF"/><bgColor indexed="64"/></patternFill></fill>'   # 3 gray
    '<fill><patternFill patternType="solid"><fgColor rgb="FFB4C6E7"/><bgColor indexed="64"/></patternFill></fill>'   # 4 title blue
    '<fill><patternFill patternType="solid"><fgColor rgb="FFFFCCCC"/><bgColor indexed="64"/></patternFill></fill>'   # 5 Y pink
    '</fills>'
    '<borders count="2"><border/>'
    '<border><left style="thin"><color rgb="FF000000"/></left><right style="thin"><color rgb="FF000000"/></right>'
    '<top style="thin"><color rgb="FF000000"/></top><bottom style="thin"><color rgb="FF000000"/></bottom><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="10">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'                                                   # 0
    '<xf numFmtId="0" fontId="3" fillId="4" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'  # 1 title (#B4C6E7, 16pt black bold, merged)
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center"/></xf>'  # 2 header
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1"/>'                     # 3 data
    '<xf numFmtId="9" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyFont="1" applyBorder="1"/>'  # 4 data %
    '<xf numFmtId="0" fontId="2" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>'       # 5 total (gray, black bold)
    '<xf numFmtId="10" fontId="2" fillId="3" borderId="1" xfId="0" applyNumberFormat="1" applyFont="1" applyFill="1" applyBorder="1"/>'  # 6 total %
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1"/>'                     # 7 raw data (left)
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center"/></xf>'  # 8 raw Y/N centered
    '<xf numFmtId="0" fontId="4" fillId="5" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center"/></xf>'  # 9 raw "N" (pink fill, red bold, centered)
    '</cellXfs></styleSheet>'
)


def _cx(col, row, *, s=0, v=None, f=None, text=None):
    """Emit one <c>. col is 1-based. f=formula (+ optional cached v), text=inline string, v=number."""
    ref = f"{_col_letter(col - 1)}{row}"
    sa = f' s="{s}"' if s else ""
    if f is not None:
        cached = "" if v is None else f"<v>{v}</v>"
        return f'<c r="{ref}"{sa}><f>{_xml_escape(f)}</f>{cached}</c>'
    if text is not None and text != "":
        return f'<c r="{ref}"{sa} t="inlineStr"><is><t xml:space="preserve">{_xml_escape(text)}</t></is></c>'
    if v is not None:
        return f'<c r="{ref}"{sa}><v>{v}</v></c>'
    return f'<c r="{ref}"{sa}/>'


_FREEZE_TOP = ('<sheetViews><sheetView workbookViewId="0">'
               '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
               '<selection pane="bottomLeft" activeCell="A2" sqref="A2"/>'
               '</sheetView></sheetViews>')


def _sheet_xml(rows_xml, cols_xml="", sheetviews="", mergecells=""):
    # element order per schema: sheetViews, cols, sheetData, mergeCells
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            + sheetviews + cols_xml + "<sheetData>" + "".join(rows_xml) + "</sheetData>"
            + mergecells + "</worksheet>")


def _cols_xml(widths):
    parts = "".join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>'
                    for i, w in enumerate(widths, start=1))
    return f"<cols>{parts}</cols>"


def _raw_col_letters(raw_header):
    """Locate lane/otp/disp/otd columns in the raw header -> spreadsheet column letters,
    so the Overview formulas point at the right columns whatever the export order."""
    low = [(h or "").strip().lower() for h in raw_header]

    def find(field):
        for a in _ALIAS[field]:
            if a in low:
                return _col_letter(low.index(a))
        return None
    return (find("lane") or "E", find("otp") or "H", find("disp") or "K", find("otd") or "N")


# Document properties written into the workbook, shown in Excel's File > Info / Properties
# panel. Title, Subject and Tags (keywords) all carry the report name; Company is the end
# customer. These are fixed for this report and do not depend on the data month.
_DOC_TITLE   = "0029H Self Report - Delta Group Logistics"   # -> Title, Subject, Tags
_DOC_COMPANY = "U.S. Postal Service"                          # -> Company


def build_workbook_xlsx(out_path, report, rows, raw_table, program):
    """Standalone 3-tab workbook (stdlib only): Overview by Lane, Overview by Trip,
    Raw Data — with live COUNTIF/COUNTIFS formulas over the raw tab, cached values, and
    formatting (bold headers, fill, borders, % number formats, column widths)."""
    lanes = report["lanes"]
    Lc, Hc, Kc, Nc = _raw_col_letters(raw_table.get("header") or [])
    RS = f"'{RAW_SHEET}'"

    # trip-id list per lane (unique, in first-seen order), formatted "('a,'b,...)"
    trips = {}
    for r in rows:
        t = str(r.get("sv_trip_id", "")).strip()
        if not t:
            continue
        trips.setdefault(r["lane"], [])
        if t not in trips[r["lane"]]:
            trips[r["lane"]].append(t)

    def frac(l, k):
        return (l[k] / l["load_count"]) if l["load_count"] else None

    # ---- Sheet 1: Overview Summary By Lane ----
    t1 = _cx(1, 1, s=1, text="RTH Performance Overview (by Lane)") + "".join(
        _cx(c, 1, s=1) for c in range(2, 7))            # black title bar across A1:F1
    #                                                     ^ hardcoded verbatim to match the template
    s1 = [f'<row r="1">{t1}</row>']
    hdr1 = ["Lane", "Load Count", "OTP", "OT Dispatch", "OTD", "Comments"]
    s1.append('<row r="2">' + "".join(_cx(i + 1, 2, s=2, text=h) for i, h in enumerate(hdr1)) + "</row>")
    first = 3
    for ri, l in enumerate(lanes, start=first):
        A = f"A{ri}"
        cif = f"COUNTIF({RS}!{Lc}:{Lc},{A})"
        c = [
            _cx(1, ri, s=3, text=l["lane"]),
            _cx(2, ri, s=3, f=cif, v=l["load_count"]),
            _cx(3, ri, s=4, f=f'IFERROR(COUNTIFS({RS}!{Lc}:{Lc},{A},{RS}!{Hc}:{Hc},"Y")/{cif},"")', v=frac(l, "otp_yes")),
            _cx(4, ri, s=4, f=f'IFERROR(COUNTIFS({RS}!{Lc}:{Lc},{A},{RS}!{Kc}:{Kc},"Y")/{cif},"")', v=frac(l, "ot_dispatch_yes")),
            _cx(5, ri, s=4, f=f'IFERROR(COUNTIFS({RS}!{Lc}:{Lc},{A},{RS}!{Nc}:{Nc},"Y")/{cif},"")', v=frac(l, "otd_yes")),
            _cx(6, ri, s=3, text=l.get("comments", "")),
        ]
        s1.append(f'<row r="{ri}">' + "".join(c) + "</row>")
    last = first + len(lanes) - 1
    tr = last + 1
    mean = lambda k: (sum(frac(l, k) for l in lanes) / len(lanes)) if lanes else None
    s1.append(f'<row r="{tr}">' + "".join([
        _cx(1, tr, s=5, text="TOTAL"),
        _cx(2, tr, s=5, f=f"SUM(B{first}:B{last})", v=report["totals"]["load_count"]),
        _cx(3, tr, s=6, f=f"AVERAGE(C{first}:C{last})", v=mean("otp_yes")),
        _cx(4, tr, s=6, f=f"AVERAGE(D{first}:D{last})", v=mean("ot_dispatch_yes")),
        _cx(5, tr, s=6, f=f"AVERAGE(E{first}:E{last})", v=mean("otd_yes")),
        _cx(6, tr, s=5),
    ]) + "</row>")
    sheet1 = _sheet_xml(s1, _cols_xml([34, 11, 8, 12, 8, 34]),
                        mergecells='<mergeCells count="1"><mergeCell ref="A1:F1"/></mergeCells>')

    # ---- Sheet 2: Overview Summary by Trip ----
    t2 = _cx(1, 1, s=1, text="RTH Performance Overview (by TripID)") + "".join(
        _cx(c, 1, s=1) for c in range(2, 8))            # black title bar across A1:G1
    #                                                     ^ hardcoded verbatim to match the template
    s2 = [f'<row r="1">{t2}</row>']
    hdr2 = ["TripID", "Lane", "Load Count", "OTP", "OT Dispatch", "OTD", "Route/HCR"]
    s2.append('<row r="2">' + "".join(_cx(i + 1, 2, s=2, text=h) for i, h in enumerate(hdr2)) + "</row>")
    for ri, l in enumerate(lanes, start=first):
        B = f"B{ri}"
        cif = f"COUNTIF({RS}!{Lc}:{Lc},{B})"
        tlist = "(" + ",".join(trips.get(l["lane"], [])) + ")" if trips.get(l["lane"]) else ""
        c = [
            _cx(1, ri, s=3, text=tlist),
            _cx(2, ri, s=3, text=l["lane"]),
            _cx(3, ri, s=3, f=cif, v=l["load_count"]),
            _cx(4, ri, s=4, f=f'IFERROR(COUNTIFS({RS}!{Lc}:{Lc},{B},{RS}!{Hc}:{Hc},"Y")/{cif},"")', v=frac(l, "otp_yes")),
            _cx(5, ri, s=4, f=f'IFERROR(COUNTIFS({RS}!{Lc}:{Lc},{B},{RS}!{Kc}:{Kc},"Y")/{cif},"")', v=frac(l, "ot_dispatch_yes")),
            _cx(6, ri, s=4, f=f'IFERROR(COUNTIFS({RS}!{Lc}:{Lc},{B},{RS}!{Nc}:{Nc},"Y")/{cif},"")', v=frac(l, "otd_yes")),
            _cx(7, ri, s=3),
        ]
        s2.append(f'<row r="{ri}">' + "".join(c) + "</row>")
    s2.append(f'<row r="{tr}">' + "".join([
        _cx(1, tr, s=5, text="GRAND TOTALS"),
        _cx(2, tr, s=5),
        _cx(3, tr, s=5, f=f"SUM(C{first}:C{last})", v=report["totals"]["load_count"]),
        _cx(4, tr, s=6, f=f"AVERAGE(D{first}:D{last})", v=mean("otp_yes")),
        _cx(5, tr, s=6, f=f"AVERAGE(E{first}:E{last})", v=mean("ot_dispatch_yes")),
        _cx(6, tr, s=6, f=f"AVERAGE(F{first}:F{last})", v=mean("otd_yes")),
        _cx(7, tr, s=5),
    ]) + "</row>")
    sheet2 = _sheet_xml(s2, _cols_xml([26, 34, 11, 8, 12, 8, 12]),
                        mergecells='<mergeCells count="1"><mergeCell ref="A1:G1"/></mergeCells>')

    # ---- Sheet 3: Raw Data With Reason Codes (verbatim export) ----
    rh = raw_table.get("header") or ["lane", "otp_flag", "dispatch_flag", "otd_flag", "reason1", "load_id"]
    # the three on-time Y/N columns -> centered; any cell that equals "N" -> pink fill/red bold
    _YN = {"on time arrival y/n", "dispatch on time y/n", "on time delivery y/n"}
    yn_cols = {i for i, h in enumerate(rh) if (h or "").strip().lower() in _YN}
    s3 = ['<row r="1">' + "".join(_cx(i + 1, 1, s=2, text=h) for i, h in enumerate(rh)) + "</row>"]
    for ri, rvals in enumerate(raw_table.get("rows") or [], start=2):
        cells = []
        for i, v in enumerate(rvals):
            txt = "" if v is None else str(v)
            if i in yn_cols:
                st = 9 if txt.strip() == "N" else 8          # 9 = pink/red bold "N", 8 = centered
            else:
                st = 7
            cells.append(_cx(i + 1, ri, s=st, text=txt))
        s3.append(f'<row r="{ri}">' + "".join(cells) + "</row>")
    sheet3 = _sheet_xml(s3, _cols_xml([16] * max(1, len(rh))), sheetviews=_FREEZE_TOP)

    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets>'
        '<sheet name="Overview Summary By Lane" sheetId="1" r:id="rId1"/>'
        '<sheet name="Overview Summary by Trip" sheetId="2" r:id="rId2"/>'
        f'<sheet name="{_xml_escape(RAW_SHEET)[:31]}" sheetId="3" r:id="rId3"/>'
        '</sheets><calcPr calcId="0" fullCalcOnLoad="1"/></workbook>'
    )
    wb_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet3.xml"/>'
        '<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        "</Relationships>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/worksheets/sheet3.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
        "</Relationships>"
    )
    # docProps/core.xml -> Title, Subject, Tags(keywords); docProps/app.xml -> Company.
    core_props = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties '
        'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:dcmitype="http://purl.org/dc/dcmitype/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f'<dc:title>{_xml_escape(_DOC_TITLE)}</dc:title>'
        f'<dc:subject>{_xml_escape(_DOC_TITLE)}</dc:subject>'
        f'<cp:keywords>{_xml_escape(_DOC_TITLE)}</cp:keywords>'
        '</cp:coreProperties>'
    )
    app_props = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties '
        'xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        '<Application>Microsoft Excel</Application>'
        f'<Company>{_xml_escape(_DOC_COMPANY)}</Company>'
        '</Properties>'
    )
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("docProps/core.xml", core_props)
        z.writestr("docProps/app.xml", app_props)
        z.writestr("xl/workbook.xml", workbook_xml)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/styles.xml", _STYLES_XML)
        z.writestr("xl/worksheets/sheet1.xml", sheet1)
        z.writestr("xl/worksheets/sheet2.xml", sheet2)
        z.writestr("xl/worksheets/sheet3.xml", sheet3)
    return out_path


# --------------------------------------------------------------------------- run pipeline
def run(raw_path, month, program, out_xlsx, raw_csv_out=None):
    rows, meta, raw_table = extract_raw_rows(raw_path)
    if not rows:
        raise SystemExit(f"No raw rows found in {raw_path} (need an 'O/D PAIR' column).")
    # If a directory (or non-.xlsx) is given, name the file canonically for the data month.
    out_xlsx = _resolve_out(out_xlsx, month)
    meta["output"] = out_xlsx
    # write the normalized raw CSV (audit trail / generator input)
    tmp_csv = raw_csv_out or (out_xlsx + ".rawrows.csv")
    with open(tmp_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["lane", "otp_flag", "dispatch_flag",
                                           "otd_flag", "reason1", "load_id"],
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    report = R.build_report(tmp_csv, month, program=program)
    build_workbook_xlsx(out_xlsx, report, rows, raw_table, program)
    return report, meta


if __name__ == "__main__":
    if len(sys.argv) != 5:
        print(__doc__)
        sys.exit(1)
    raw, month, program, out = sys.argv[1:5]
    rep, meta = run(raw, month, program, out)
    print(R.render_text(rep))
    print(f"\nextract: {meta['count']} raw rows from {meta['source']} "
          f"{[s['sheet'] + ':' + str(s['rows']) for s in meta['sheets']] or ''}")
    print(f"workbook written (3 tabs: by Lane, by Trip, Raw Data): {meta['output']}")
