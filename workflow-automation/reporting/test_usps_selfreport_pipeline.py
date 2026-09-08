"""Tests for the USPS Self Report pipeline (stdlib .xlsx read/write, extract, full run)."""

import csv
import os
import tempfile
import zipfile

import usps_selfreport_pipeline as P


def _tmp(suffix):
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return path


def _make_raw_xlsx(header, rows, sheet="RTH Raw Data With Reason Codes"):
    """Minimal raw-tab .xlsx (all cells inline strings) for testing the extractor."""
    def esc(s):
        return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    def col(i):
        s = ""; i += 1
        while i:
            i, r = divmod(i - 1, 26); s = chr(ord("A") + r) + s
        return s

    def row_xml(r, vals):
        cs = "".join(
            f'<c r="{col(i)}{r}" t="inlineStr"><is><t xml:space="preserve">{esc(v)}</t></is></c>'
            for i, v in enumerate(vals) if str(v) != "")
        return f'<row r="{r}">{cs}</row>'

    body = [row_xml(1, header)] + [row_xml(i, rv) for i, rv in enumerate(rows, start=2)]
    sheet_xml = ('<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org'
                 '/spreadsheetml/2006/main"><sheetData>' + "".join(body) + "</sheetData></worksheet>")
    wb = ('<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml'
          '/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
          f'<sheets><sheet name="{esc(sheet)[:31]}" sheetId="1" r:id="rId1"/></sheets></workbook>')
    wbrels = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package'
              '/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org'
              '/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
    ct = ('<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument'
          '.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType='
          '"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
    rr = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006'
          '/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument'
          '/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    path = _tmp(".xlsx")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rr)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", wbrels)
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return path


JBH_HEADER = ["SUPPLIER NAME", "Contract ID", "SV Trip ID", "Load ID", "O/D PAIR",
              "Scheduled arrival time", "Actual arrival time", "ON TIME Arrival Y/N",
              "actual dispatch time", "planned dispatch time", "Dispatch on time Y/N",
              "Actual delivery time", "planned delivery time", "ON TIME DELIVERY y/n",
              "Reason 1"]


def _raw_row(lane, otp, disp, otd, reason=""):
    return ["Delta", "0029H", "T1", "L1", lane, "", "", otp, "", "", disp, "", "", otd, reason]


def test_col_to_idx():
    assert P._col_to_idx("A1") == 0 and P._col_to_idx("C7") == 2 and P._col_to_idx("AA2") == 26


def test_col_letter_roundtrip():
    for i in (0, 1, 25, 26, 27, 51, 52, 701):
        assert P._col_to_idx(P._col_letter(i) + "1") == i


def test_extract_from_xlsx_raw_tab():
    path = _make_raw_xlsx(JBH_HEADER, [
        _raw_row("CINCINNATI, OH | DENVER, CO", "Y", "Y", "N", "POSTAL"),
        _raw_row("CINCINNATI, OH | DENVER, CO", "N", "Y", "Y"),
        _raw_row("ATLANTA, GA | AUGUSTA, GA", "Y", "Y", "Order is VOID"),
    ])
    try:
        rows, meta, raw = P.extract_raw_rows(path)
    finally:
        os.remove(path)
    assert meta["source"] == "xlsx" and meta["count"] == 3
    assert rows[0]["lane"] == "CINCINNATI, OH | DENVER, CO"
    assert rows[0]["otp_flag"] == "Y" and rows[0]["otd_flag"] == "N"
    assert rows[0]["reason1"] == "POSTAL"
    assert rows[0]["sv_trip_id"] == "T1"                 # captured for the by-Trip tab
    assert raw["header"] == JBH_HEADER and len(raw["rows"]) == 3   # verbatim raw table


def test_full_run_xlsx_in_xlsx_out_matches_generator():
    raw = _make_raw_xlsx(JBH_HEADER, [
        _raw_row("CINCINNATI, OH | DENVER, CO", "Y", "Y", "Y"),
        _raw_row("CINCINNATI, OH | DENVER, CO", "N", "Y", "N"),
        _raw_row("ATLANTA, GA | AUGUSTA, GA", "Y", "Y", "N"),
    ])
    out = _tmp(".xlsx")
    try:
        report, meta = P.run(raw, "2026-08", "GEGW", out)
        # read the written Overview back with our own reader
        sheets = P.read_xlsx_sheets(out)
        grid = next(iter(sheets.values()))
        # row 0 = title, row 1 = header, row 2+ = data
        assert grid[1][:6] == ["Lane", "Load Count", "OTP", "OT Dispatch", "OTD", "Comments"]
        by_lane = {r[0]: r for r in grid[2:] if r and r[0]}
        # CINCINNATI: 2 loads, OTP 1/2=50% -> stored as 0.5
        cin = by_lane["CINCINNATI, OH | DENVER, CO"]
        assert cin[1] == "2"
        assert abs(float(cin[2]) - 0.5) < 1e-9        # OTP 50%
        assert abs(float(cin[3]) - 1.0) < 1e-9        # dispatch 100%
        # TOTAL row present
        assert "TOTAL" in by_lane
    finally:
        for p in (raw, out, out + ".rawrows.csv"):
            if os.path.exists(p):
                os.remove(p)


def test_workbook_xlsx_has_three_sheets_and_expected_parts():
    rep = {
        "title": "GEGW Performance Overview", "month": "2026-08", "lane_count": 1,
        "lanes": [{"lane": "A, X | B, Y", "load_count": 3, "otp_pct": 67,
                   "ot_dispatch_pct": 100, "otd_pct": 33,
                   "otp_yes": 2, "ot_dispatch_yes": 3, "otd_yes": 1, "comments": "note"}],
        "totals": {"lane": "TOTAL", "load_count": 3, "otp_pct": 67,
                   "ot_dispatch_pct": 100, "otd_pct": 33, "comments": ""},
    }
    rows = [{"lane": "A, X | B, Y", "sv_trip_id": "'T1", "otp_flag": "Y",
             "dispatch_flag": "Y", "otd_flag": "N", "reason1": "", "load_id": "'L1"}]
    raw = {"header": ["O/D PAIR", "ON TIME Arrival Y/N", "Dispatch on time Y/N",
                      "ON TIME DELIVERY y/n"], "rows": [["A, X | B, Y", "Y", "Y", "N"]]}
    out = _tmp(".xlsx")
    try:
        P.build_workbook_xlsx(out, rep, rows, raw, "GEGW")
        with zipfile.ZipFile(out) as z:
            names = set(z.namelist())
        for part in ("[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
                     "xl/_rels/workbook.xml.rels", "xl/styles.xml",
                     "xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml",
                     "xl/worksheets/sheet3.xml"):
            assert part in names, part
        s = P.read_xlsx_sheets(out)
        assert list(s) == ["Overview Summary By Lane", "Overview Summary by Trip",
                           "Raw Data With Reason Codes"]
        # by-Trip TripID cell carries the trip list; raw tab is verbatim
        trip = s["Overview Summary by Trip"]
        assert trip[2][0] == "('T1)" and trip[2][1] == "A, X | B, Y"
        assert s["Raw Data With Reason Codes"][0] == raw["header"]
    finally:
        os.remove(out)


def test_sql_export_layout_prefers_yn_over_numeric_flags():
    """The real SSMS export carries BOTH 'ON TIME Arrival Y/N' (Y/N/V) and 'OTP_Flag'
    (1/0). The Y/N text column must win — the numeric flag must not shadow it."""
    path = _tmp(".csv")
    hdr = ["SUPPLIER NAME", "Contract ID", "SV Trip ID", "Load ID", "O/D PAIR",
           "Scheduled arrival time", "Actual arrival time", "ON TIME Arrival Y/N",
           "actual dispatch time", "planned dispatch time", "Dispatch on time Y/N",
           "Actual delivery time", "planned delivery time", "ON TIME DELIVERY y/n",
           "OTP_Flag", "Dispatch_Flag", "OTD_Flag", "IsVoid"]
    rows = [
        ["D", "'0029H", "T", "L1", "CINCINNATI, OH | DENVER, CO", "", "", "Y", "", "",
         "Y", "", "", "N", "1", "1", "0", "0"],
        ["D", "'0029H", "T", "L2", "CINCINNATI, OH | DENVER, CO", "", "", "N", "", "",
         "Y", "", "", "Y", "0", "1", "1", "0"],
        ["D", "'0029H", "T", "L3", "CHAMPAIGN, IL | PEORIA, IL", "", "", "V", "", "",
         "N", "", "", "V", "", "", "", "1"],       # voided order
    ]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(hdr)
        w.writerows(rows)
    try:
        report, meta = P.run(path, "2026-08", "GEGW", _tmp(".xlsx"))
    finally:
        os.remove(path)
    by = {l["lane"]: l for l in report["lanes"]}
    cin = by["CINCINNATI, OH | DENVER, CO"]
    assert cin["load_count"] == 2
    assert cin["otp_pct"] == 50 and cin["ot_dispatch_pct"] == 100 and cin["otd_pct"] == 50
    void = by["CHAMPAIGN, IL | PEORIA, IL"]
    assert void["load_count"] == 1 and void["otp_pct"] == 0 and void["otd_pct"] == 0


def test_numeric_flag_only_export():
    """If an export carries ONLY numeric 1/0 flags, they are read correctly (1->Y, 0->N)."""
    path = _tmp(".csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["O/D PAIR", "OTP_Flag", "Dispatch_Flag", "OTD_Flag"])
        w.writerow(["A, X | B, Y", "1", "0", "1"])
    try:
        report, _ = P.run(path, "2026-08", "GEGW", _tmp(".xlsx"))
    finally:
        os.remove(path)
    l = report["lanes"][0]
    assert l["otp_pct"] == 100 and l["ot_dispatch_pct"] == 0 and l["otd_pct"] == 100


def test_formatting_merge_freeze_and_yhighlight():
    """Title rows merged; Raw Data freezes the top row and centers/highlights the 3 Y/N cols."""
    path = _tmp(".csv")
    hdr = ["O/D PAIR", "ON TIME Arrival Y/N", "Dispatch on time Y/N", "ON TIME DELIVERY y/n"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(hdr)
        w.writerow(["A, X | B, Y", "Y", "N", "Y"])          # 1 N cell, 2 non-N cells
    out = _tmp(".xlsx")
    try:
        P.run(path, "2026-08", "GEGW", out)
        z = zipfile.ZipFile(out)
        s1 = z.read("xl/worksheets/sheet1.xml").decode()
        s2 = z.read("xl/worksheets/sheet2.xml").decode()
        s3 = z.read("xl/worksheets/sheet3.xml").decode()
        assert 'mergeCell ref="A1:F1"' in s1                # by-Lane title merged
        assert 'mergeCell ref="A1:G1"' in s2                # by-Trip title merged
        assert 'state="frozen"' in s3 and 'ySplit="1"' in s3  # Raw Data top row frozen
        assert s3.count('s="9"') == 1                       # one "N" cell highlighted
        assert s3.count('s="8"') == 2                       # two non-N cells centered
    finally:
        for p in (path, out, out + ".rawrows.csv"):
            if os.path.exists(p):
                os.remove(p)


def test_extract_from_csv():
    path = _tmp(".csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["lane", "otp_flag", "dispatch_flag", "otd_flag"])
        w.writerow(["A, X | B, Y", "Y", "N", "Y"])
    try:
        rows, meta, raw = P.extract_raw_rows(path)
    finally:
        os.remove(path)
    assert meta["source"] == "csv" and len(rows) == 1 and rows[0]["dispatch_flag"] == "N"
    assert raw["header"] == ["lane", "otp_flag", "dispatch_flag", "otd_flag"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
