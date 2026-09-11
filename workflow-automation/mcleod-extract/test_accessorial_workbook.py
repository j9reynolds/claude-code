"""Tests for accessorial_workbook: valid multi-tab .xlsx from the shadow-report result."""

import os
import tempfile
import xml.dom.minidom as minidom
import zipfile

import accessorial_shadow_report as R
import accessorial_workbook as W
import test_accessorial_shadow_report as F   # reuse the synthetic fixture


def _report():
    paths = F._fixture()
    try:
        return R.build_shadow_report(*paths, month="2026-08")
    finally:
        F._cleanup(paths)


def test_workbook_is_wellformed_and_complete():
    rep = _report()
    fd, out = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    try:
        W.build_workbook(rep, out)
        with zipfile.ZipFile(out) as z:
            names = set(z.namelist())
            for part in ("[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
                         "xl/_rels/workbook.xml.rels", "xl/styles.xml",
                         "docProps/core.xml", "docProps/app.xml"):
                assert part in names, part
            # five worksheets present and every XML part well-formed
            sheets = [n for n in names if n.startswith("xl/worksheets/sheet")]
            assert len(sheets) == 5, sheets
            for n in names:
                if n.endswith(".xml") or n.endswith(".rels"):
                    minidom.parseString(z.read(n))   # raises if malformed
            wb = z.read("xl/workbook.xml").decode()
            for nm in ("Summary", "Detention by Customer", "Detention by Load",
                       "Accessorial Margin", "Rate-Con Gap"):
                assert f'name="{nm}"' in wb, nm
            blob = b"".join(z.read(n) for n in sheets).decode()
            assert "Accessorial Shadow Report - 2026-08" in blob
            assert "ACME" in blob                 # detention-by-customer row
            assert "L1" in blob                   # rate-con gap + detention-by-load
            assert "GEGW" not in blob
    finally:
        os.remove(out)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
