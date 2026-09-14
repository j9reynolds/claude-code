"""Styled review workbook for the accessorial shadow report (stdlib only).

Turns the structured result from `accessorial_shadow_report.build_shadow_report` into a
multi-tab .xlsx a human reviews. No third-party libraries: the workbook is written as
zipped OOXML (zipfile + hand-built XML), the same install-free approach used by the USPS
report so it runs on any Delta host.

Tabs:
  Summary               - headline numbers for all four buckets
  Detention by Customer - eligibility-adjusted un-billed detention per customer + TOTAL
  Detention by Load     - the same gap per order id
  Accessorial Margin    - customer billed vs carrier paid/deducted by category + TOTAL
  Rate-Con Gap          - delivered loads with no signed rate-con recorded

Read-only artifact: it reports, it never bills or pays.
"""

from __future__ import annotations

import zipfile


def _esc(v):
    return (str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _col(i):
    """0-based column index -> A, B, ... AA."""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


# cellXfs style indices (see _STYLES): 0 default, 1 title, 2 header, 3 text, 4 currency,
# 5 total-text, 6 total-currency
_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<numFmts count="1"><numFmt numFmtId="164" formatCode="&quot;$&quot;#,##0"/></numFmts>'
    '<fonts count="4">'
    '<font><sz val="11"/><name val="Calibri"/><color rgb="FF000000"/></font>'
    '<font><sz val="11"/><b/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>'
    '<font><sz val="11"/><b/><color rgb="FF000000"/><name val="Calibri"/></font>'
    '<font><sz val="14"/><b/><color rgb="FF000000"/><name val="Calibri"/></font>'
    '</fonts>'
    '<fills count="5">'
    '<fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FF305496"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFBFBFBF"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFB4C6E7"/></patternFill></fill>'
    '</fills>'
    '<borders count="2">'
    '<border><left/><right/><top/><bottom/><diagonal/></border>'
    '<border><left style="thin"><color rgb="FF000000"/></left>'
    '<right style="thin"><color rgb="FF000000"/></right>'
    '<top style="thin"><color rgb="FF000000"/></top>'
    '<bottom style="thin"><color rgb="FF000000"/></bottom><diagonal/></border>'
    '</borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="7">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="3" fillId="4" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center"/></xf>'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"/>'
    '<xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyNumberFormat="1"/>'
    '<xf numFmtId="0" fontId="2" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>'
    '<xf numFmtId="164" fontId="2" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyNumberFormat="1"/>'
    '</cellXfs>'
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    '</styleSheet>'
)

TITLE, HEADER, TEXT, CUR, TOT, TOTCUR = 1, 2, 3, 4, 5, 6


def _cell(col, row, value, style, numeric):
    ref = f"{_col(col)}{row}"
    if numeric:
        return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{_esc(value)}</t></is></c>'


def _sheet(rows, widths=None, merge_first_cols=0):
    """rows: list of list of (value, style, numeric). Returns worksheet XML."""
    cols_xml = ""
    if widths:
        cols_xml = "<cols>" + "".join(
            f'<col min="{i+1}" max="{i+1}" width="{w}" customWidth="1"/>'
            for i, w in enumerate(widths)) + "</cols>"
    body = []
    for ri, row in enumerate(rows, start=1):
        cells = "".join(_cell(ci, ri, v, s, n) for ci, (v, s, n) in enumerate(row))
        body.append(f'<row r="{ri}">{cells}</row>')
    merge = ""
    if merge_first_cols > 1:
        merge = (f'<mergeCells count="1"><mergeCell ref="A1:{_col(merge_first_cols-1)}1"/>'
                 f'</mergeCells>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        + cols_xml + "<sheetData>" + "".join(body) + "</sheetData>" + merge + "</worksheet>"
    )


def _dollars(x):
    return (round(x), None, True)


def build_workbook(report, out_path):
    """Write the shadow-report workbook to out_path. Returns out_path."""
    m = report["month"]
    o = report["overview"]
    d = report["detention"]

    # ---- Summary ----
    summ = [
        [(f"Accessorial Shadow Report - {m}", TITLE, False), ("", TITLE, False),
         ("", TITLE, False), ("", TITLE, False)],
        [("Metric", HEADER, False), ("Value", HEADER, False),
         ("Loads", HEADER, False), ("Notes", HEADER, False)],
        [("Delivered loads", TEXT, False), (o["loads"], TEXT, True),
         ("", TEXT, False), ("prior calendar month", TEXT, False)],
        [("A. Un-billed detention (eligible)", TEXT, False), _dollars(d["elig_gap"]),
         (d["elig_loads"], TEXT, True), ("appt-based, per-stop $150 cap, eligibility-adj", TEXT, False)],
        [("   pre-eligibility", TEXT, False), _dollars(d["pre_elig_gap"]),
         (d["pre_elig_loads"], TEXT, True), ("before removing carrier-late stops", TEXT, False)],
        [("D. Rate-con gap", TEXT, False), (f"{o['rc_missing']:,} loads", TEXT, False),
         (o["rc_missing"], TEXT, True), (f"{o['rc_missing_pct']:.1f}% of delivered", TEXT, False)],
        [("Read-only", TEXT, False), ("no writes / no money moves", TEXT, False),
         ("", TEXT, False), ("human reviews before any bill", TEXT, False)],
    ]

    # ---- Detention by Customer ----
    detc = [[(f"Un-billed detention by customer - {m} (eligibility-adjusted)", TITLE, False),
             ("", TITLE, False), ("", TITLE, False)],
            [("Customer", HEADER, False), ("Loads", HEADER, False), ("Un-billed $", HEADER, False)]]
    for r in d["by_customer"]:
        detc.append([(r["customer"], TEXT, False), (r["loads"], TEXT, True), _dollars(r["dollars"])])
    detc.append([("TOTAL", TOT, False),
                 (sum(r["loads"] for r in d["by_customer"]), TOT, True),
                 (round(d["elig_gap"]), TOTCUR, True)])

    # ---- Detention by Load ----
    detl = [[(f"Un-billed detention by load - {m}", TITLE, False), ("", TITLE, False)],
            [("Order (PRO)", HEADER, False), ("Un-billed $", HEADER, False)]]
    for oid, dollars in sorted(d["by_order"].items(), key=lambda x: -x[1]):
        detl.append([(oid, TEXT, False), _dollars(dollars)])

    # ---- Accessorial Margin ----
    marg = [[(f"Accessorial margin by category - {m}", TITLE, False), ("", TITLE, False),
             ("", TITLE, False), ("", TITLE, False), ("", TITLE, False)],
            [("Category", HEADER, False), ("Cust billed", HEADER, False),
             ("Carrier paid", HEADER, False), ("Deducted", HEADER, False), ("Margin", HEADER, False)]]
    for c in report["categories"]:
        is_tot = c["category"] == "TOTAL"
        t, tc = (TOT, TOTCUR) if is_tot else (TEXT, CUR)
        marg.append([(c["category"], t, False), (round(c["billed"]), tc, True),
                     (round(c["paid"]), tc, True), (round(c["deducted"]), tc, True),
                     (round(c["margin"]), tc, True)])

    # ---- Rate-Con Gap ----
    rc = [[(f"Rate-con control gap - {m}  ({o['rc_missing']:,} loads, {o['rc_missing_pct']:.1f}%)",
            TITLE, False)],
          [("Order (PRO) - no signed rate-con recorded", HEADER, False)]]
    for pro in report["ratecon_gap"]["loads"]:
        rc.append([(pro, TEXT, False)])

    sheets = [
        ("Summary", _sheet(summ, widths=[34, 16, 10, 40], merge_first_cols=4)),
        ("Detention by Customer", _sheet(detc, widths=[46, 10, 14], merge_first_cols=3)),
        ("Detention by Load", _sheet(detl, widths=[18, 14], merge_first_cols=2)),
        ("Accessorial Margin", _sheet(marg, widths=[16, 14, 14, 12, 14], merge_first_cols=5)),
        ("Rate-Con Gap", _sheet(rc, widths=[44])),
    ]

    sheet_tags = "".join(
        f'<sheet name="{_esc(nm)[:31]}" sheetId="{i}" r:id="rId{i}"/>'
        for i, (nm, _) in enumerate(sheets, start=1))
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets>{sheet_tags}</sheets></workbook>'
    )
    wb_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(f'<Relationship Id="rId{i}" '
                  'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                  f'Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets) + 1))
        + f'<Relationship Id="rId{len(sheets)+1}" '
          'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
          'Target="styles.xml"/></Relationships>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
                  'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                  for i in range(1, len(sheets) + 1))
        + '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        '</Types>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
        '</Relationships>'
    )
    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:title>Accessorial Shadow Report - {_esc(m)}</dc:title>'
        '<dc:subject>Read-only accessorial leakage review</dc:subject></cp:coreProperties>'
    )
    app = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
        '<Application>Microsoft Excel</Application><Company>Delta Group Logistics</Company></Properties>'
    )

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("docProps/core.xml", core)
        z.writestr("docProps/app.xml", app)
        z.writestr("xl/workbook.xml", workbook_xml)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/styles.xml", _STYLES)
        for i, (_, xml) in enumerate(sheets, start=1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", xml)
    return out_path
