"""USPS Self Report pipeline — extract -> generator -> filled Overview  (opportunity #6).

End-to-end, dependency-free (Python standard library only, so it runs on any Delta host
with no installs — pip is not required):

  1. EXTRACT   read the J.B. Hunt raw-data extract (an .xlsx workbook — the monthly export,
               or the report workbook's "… Raw Data With Reason Codes" tab, or per-lane raw
               tabs) and pull the normalized per-load rows. Also accepts a .csv.
  2. GENERATE  run report_usps_self_report.build_report() — the verified aggregation
               (reproduced the real May 2026 report exactly, 27/27 lanes, 0 differences).
  3. FILL      write a filled **Overview** .xlsx: Lane | Load Count | OTP | OT Dispatch |
               OTD | Comments, one row per lane (A-Z) + a TOTAL row, percentages formatted
               as Excel % cells — ready for the account owner to review and send.

.xlsx here is read and written as zipped XML with the stdlib (zipfile + xml). It targets the
common shapes Excel produces (shared strings, inline strings, plain numbers). Open the
output once in Excel to confirm formatting before the first real send.

CLI:
  python3 usps_selfreport_pipeline.py RAW.xlsx 2026-08 GEGW OUT_overview.xlsx
  python3 usps_selfreport_pipeline.py RAW.csv  2026-08 RTH  OUT_overview.xlsx
"""

from __future__ import annotations

import csv
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

import report_usps_self_report as R

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
}


def _is_reason(h):
    h = (h or "").strip().lower()
    return h.startswith("reason") or "notes required" in h


def extract_raw_rows(path):
    """Read RAW rows from a .xlsx (any sheet carrying an 'O/D PAIR' header) or a .csv.
    Returns (rows, meta) where rows is a list of dicts the generator understands."""
    if path.lower().endswith(".csv"):
        with open(path, encoding="utf-8-sig") as fh:
            rd = csv.DictReader(fh)
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
            for r in rd:
                lane = (r.get(cmap["lane"] or "", "") or "").strip()
                if not lane:
                    continue
                rows.append({
                    "lane": lane,
                    "otp_flag": r.get(cmap["otp"] or "", ""),
                    "dispatch_flag": r.get(cmap["disp"] or "", ""),
                    "otd_flag": r.get(cmap["otd"] or "", ""),
                    "reason1": "; ".join(x for x in (r.get(k, "") for k in reason_keys) if str(x).strip()),
                    "load_id": r.get(cmap["load_id"] or "", ""),
                })
        return rows, {"source": "csv", "sheets": [], "count": len(rows)}

    sheets = read_xlsx_sheets(path)
    out, used = [], []
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
        reason_cols = [i for i, h in enumerate(low) if _is_reason(h)]
        c_load = col("load_id")
        if c_lane is None:
            continue
        n_before = len(out)
        for row in grid[hdr_i + 1:]:
            lane = (row[c_lane] if c_lane < len(row) else "").strip()
            if not lane:
                continue
            g = lambda i: (row[i] if (i is not None and i < len(row)) else "")
            out.append({
                "lane": lane,
                "otp_flag": g(c_otp), "dispatch_flag": g(c_disp), "otd_flag": g(c_otd),
                "reason1": "; ".join(x for x in (g(i) for i in reason_cols) if str(x).strip()),
                "load_id": g(c_load),
            })
        used.append({"sheet": name, "rows": len(out) - n_before})
    return out, {"source": "xlsx", "sheets": used, "count": len(out)}


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


def write_overview_xlsx(report, out_path, sheet_name="Overview Summary By Lane"):
    """Write the filled Overview as a minimal, valid .xlsx (stdlib only).
    Percent cells use builtin number format 9 ('0%'); values stored as fractions."""
    header = ["Lane", "Load Count", "OTP", "OT Dispatch", "OTD", "Comments"]
    body = []
    for l in report["lanes"] + [report["totals"]]:
        body.append([
            l["lane"], l["load_count"],
            (None if l["otp_pct"] is None else l["otp_pct"] / 100.0),
            (None if l["ot_dispatch_pct"] is None else l["ot_dispatch_pct"] / 100.0),
            (None if l["otd_pct"] is None else l["otd_pct"] / 100.0),
            l.get("comments", ""),
        ])
    title = f"{report['title']} — {report['month']}"

    def cell_xml(r, ci, val, is_pct=False, bold_num=False):
        ref = f"{_col_letter(ci)}{r}"
        if val is None or val == "":
            return f'<c r="{ref}"/>'
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            style = ' s="1"' if is_pct else ""
            return f'<c r="{ref}"{style}><v>{val}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{_xml_escape(val)}</t></is></c>'

    rows_xml = []
    # row 1: title
    rows_xml.append(f'<row r="1">{cell_xml(1, 0, title)}</row>')
    # row 2: header
    rows_xml.append('<row r="2">' + "".join(cell_xml(2, i, h) for i, h in enumerate(header)) + "</row>")
    # data rows from row 3
    for ri, rvals in enumerate(body, start=3):
        cells = [
            cell_xml(ri, 0, rvals[0]),
            cell_xml(ri, 1, rvals[1]),
            cell_xml(ri, 2, rvals[2], is_pct=True),
            cell_xml(ri, 3, rvals[3], is_pct=True),
            cell_xml(ri, 4, rvals[4], is_pct=True),
            cell_xml(ri, 5, rvals[5]),
        ]
        rows_xml.append(f'<row r="{ri}">' + "".join(cells) + "</row>")

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>" + "".join(rows_xml) + "</sheetData></worksheet>"
    )
    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="9" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
        "</cellXfs></styleSheet>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{_xml_escape(sheet_name)[:31]}" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    )
    wb_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        "</Relationships>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook_xml)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/styles.xml", styles_xml)
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return out_path


# --------------------------------------------------------------------------- run pipeline
def run(raw_path, month, program, out_xlsx, raw_csv_out=None):
    rows, meta = extract_raw_rows(raw_path)
    if not rows:
        raise SystemExit(f"No raw rows found in {raw_path} (need an 'O/D PAIR' column).")
    # write the normalized raw CSV (audit trail / generator input)
    tmp_csv = raw_csv_out or (out_xlsx + ".rawrows.csv")
    with open(tmp_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["lane", "otp_flag", "dispatch_flag",
                                           "otd_flag", "reason1", "load_id"])
        w.writeheader()
        w.writerows(rows)
    report = R.build_report(tmp_csv, month, program=program)
    write_overview_xlsx(report, out_xlsx)
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
    print(f"filled Overview written: {out}")
