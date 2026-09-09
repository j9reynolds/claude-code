"""POD reader — the authoritative source of carrier check-in / check-out times.

WHY: McLeod's entered stop times are unreliable for billing (verified on order 0169514 —
McLeod 12:15/19:15 vs POD 11:15/18:50). The Rate Confirmation requires check-in/out to be
"clearly documented on the BOL or POD," so a defensible detention charge must read the POD.

HOW IT WIRES INTO THE ENGINE (live, per accessorial event — NOT 26k retroactively):
    1. Fetch the document image, trying 04-Temporary POD first, then 01-BOL/POD:
         img = get_image(orderId=pro, imageType=4)  ->  fall back to  imageType=1
       (`get_image_fn` is injected so this module stays testable and has no MCP dependency.)
    2. Get text: a text-layer PDF -> extract directly; a scanned image -> OCR.
    3. parse_pod_times(text, stop_date) -> (check_in, check_out).
    4. Feed those + the stop's appointment_time into accessorial_rules.evaluate(). If the POD
       times can't be parsed, return None -> the engine routes the accessorial to NEEDS_REVIEW
       (never auto-approve off unreadable proof).

This module is pure/testable: it parses text and orchestrates via an injected fetch/OCR.
Run `python3 pod_reader.py` for the parser demo.
"""

from __future__ import annotations

import re
from datetime import datetime, date, timedelta
from typing import Callable, Optional, Tuple

POD_IMAGE_TYPES = [4, 1]   # 04-Temporary POD preferred, then 01-BOL/POD

# Time patterns seen on DGL PODs / driver texts, most specific first.
#  "Check-In: 1325"  "IN 1147 OUT 1446"  "Arrived 11:15  Departed 18:50"  "In: 09/12 11:15"
_TIME = r"(\d{1,2}):?(\d{2})"
_IN_PATTERNS = [
    r"check[\s\-]?in[:\s]+" + _TIME,
    r"\barr(?:ived|ival)?[:\s]+" + _TIME,
    r"\bin[:\s]+" + _TIME,
    r"\btime\s*in[:\s]+" + _TIME,
]
_OUT_PATTERNS = [
    r"check[\s\-]?out[:\s]+" + _TIME,
    r"\bdep(?:arted|arture)?[:\s]+" + _TIME,
    r"\bout[:\s]+" + _TIME,
    r"\btime\s*out[:\s]+" + _TIME,
]


def _first_time(patterns, text) -> Optional[Tuple[int, int]]:
    low = text.lower()
    for pat in patterns:
        m = re.search(pat, low)
        if m:
            h, mm = int(m.group(1)), int(m.group(2))
            if 0 <= h <= 23 and 0 <= mm <= 59:
                return h, mm
    return None


def parse_pod_times(text: str, stop_date: date) -> Tuple[Optional[datetime], Optional[datetime]]:
    """Extract (check_in, check_out) from POD text, dated to the stop's local date.
    Times are read as the stop's local wall clock (no tz conversion). Returns (None, None)
    if either can't be found — caller must route to human review."""
    ci = _first_time(_IN_PATTERNS, text)
    co = _first_time(_OUT_PATTERNS, text)
    if not ci or not co:
        return None, None
    check_in = datetime(stop_date.year, stop_date.month, stop_date.day, ci[0], ci[1])
    check_out = datetime(stop_date.year, stop_date.month, stop_date.day, co[0], co[1])
    # out before in on the same date -> overnight; roll check_out to the next day.
    if check_out < check_in:
        check_out += timedelta(days=1)
    return check_in, check_out


class OcrUnavailable(RuntimeError):
    """Raised when a scanned document needs OCR but no OCR backend is installed."""


def decode_get_image(result) -> Optional[bytes]:
    """Turn a McLeod get_image result into raw document bytes. The MCP tool returns
    base64 (optionally a data: URI, or a {'base64': ...}/{'data': ...} dict). Returns None
    if empty."""
    import base64
    if result is None:
        return None
    if isinstance(result, (bytes, bytearray)):
        return bytes(result)
    if isinstance(result, dict):
        result = result.get("base64") or result.get("data") or result.get("image") or ""
    s = str(result).strip()
    if not s:
        return None
    if s.startswith("data:"):
        s = s.split(",", 1)[-1]           # strip data:...;base64, prefix
    try:
        return base64.b64decode(s, validate=False)
    except Exception:
        return s.encode("utf-8", "ignore")  # already plain text


def ocr_document(data: bytes) -> str:
    """Extract text from a POD document. PDF -> text layer (pypdf); if the PDF is a scan
    with no text, or the doc is an image, fall back to OCR (pdf2image + pytesseract). Deps
    are imported lazily so this module loads without them; a scan with no OCR backend raises
    OcrUnavailable so the caller routes to human review rather than silently missing times."""
    if data[:4] == b"%PDF":
        text = ""
        try:
            import io, pypdf                                  # lazy
            reader = pypdf.PdfReader(io.BytesIO(data))
            text = "\n".join((p.extract_text() or "") for p in reader.pages)
        except ImportError:
            pass
        if text.strip():
            return text
        # scanned PDF -> rasterize + OCR
        try:
            import pytesseract                                # lazy
            from pdf2image import convert_from_bytes          # lazy
            return "\n".join(pytesseract.image_to_string(img)
                             for img in convert_from_bytes(data))
        except ImportError as e:
            raise OcrUnavailable(
                "scanned POD needs OCR — install pypdf, pdf2image, pytesseract + the "
                "tesseract binary in the production runtime") from e
    if data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n":  # JPEG / PNG
        try:
            import io, pytesseract                            # lazy
            from PIL import Image                              # lazy
            return pytesseract.image_to_string(Image.open(io.BytesIO(data)))
        except ImportError as e:
            raise OcrUnavailable("image POD needs pytesseract + tesseract binary") from e
    return data.decode("utf-8", "ignore")                    # already text


def read_pod_times(pro_number: str, stop_date: date,
                   get_image_fn: Callable[[str, int], object],
                   ocr_fn: Optional[Callable[[bytes], str]] = None
                   ) -> Tuple[Optional[datetime], Optional[datetime]]:
    """Live per-event flow. `get_image_fn(pro, image_type)` returns the McLeod get_image
    result (base64/bytes/dict) — the agent wraps the mcp__dgl-mcp__get_image tool (see
    connector_get_image below). Tries 04-Temporary POD then 01-BOL/POD, decodes, OCRs
    (ocr_document by default), and parses. Returns (check_in, check_out) or (None, None)
    -> NEEDS_REVIEW (unreachable image, unreadable scan, or no times found)."""
    ocr = ocr_fn or ocr_document
    for img_type in POD_IMAGE_TYPES:
        data = decode_get_image(get_image_fn(pro_number, img_type))
        if not data:
            continue
        try:
            text = ocr(data)
        except OcrUnavailable:
            continue                        # can't read this scan -> try the other type / review
        ci, co = parse_pod_times(text, stop_date)
        if ci and co:
            return ci, co
    return None, None


def connector_get_image(mcp_get_image):
    """Adapter: wrap the live MCP tool into the get_image_fn read_pod_times expects.
    `mcp_get_image` is the callable bound to the `mcp__dgl-mcp__get_image` tool. Example:

        from pod_reader import read_pod_times, connector_get_image
        ci, co = read_pod_times("0169514", date(2025, 9, 12),
                                connector_get_image(mcp__dgl_mcp__get_image))

    The MCP tool takes `orderId` (and returns base64 capped at 6MB); the image-type
    preference (04 then 01) is handled by DocumentPower's unbilled->billed fallback, so we
    pass orderId and ignore the type arg here (kept for signature compatibility)."""
    def _fn(pro_number: str, image_type: int):
        return mcp_get_image(orderId=pro_number.strip())
    return _fn


def _demo() -> None:
    # Representative POD texts, including the order 0169514 example (appt 15:00 -> 1h50m).
    samples = [
        ("TS TECH — Check-In: 11:15   Check-Out: 18:50", date(2025, 9, 12)),
        ("BOL 0198448  IN 1147  OUT 1446  seal intact", date(2026, 8, 28)),
        ("Arrived 22:30 Departed 04:15 next day", date(2025, 7, 6)),
        ("no times legible on this scan", date(2025, 1, 1)),
    ]
    print("POD parser demo (check_in / check_out):")
    for text, d in samples:
        ci, co = parse_pod_times(text, d)
        if ci and co:
            mins = (co - ci).total_seconds() / 60
            print(f"  {ci:%Y-%m-%d %H:%M} -> {co:%H:%M}  ({mins:.0f} min on site)  <- {text[:40]!r}")
        else:
            print(f"  UNREADABLE -> NEEDS_REVIEW                         <- {text[:40]!r}")


def _process_file(path: str, stop_date: date) -> None:
    with open(path, "rb") as fh:
        data = fh.read()
    ci, co = parse_pod_times(ocr_document(data), stop_date)
    print(f"{path}: check_in={ci}  check_out={co}"
          + ("" if ci and co else "  -> NEEDS_REVIEW"))


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2:
        d = datetime.strptime(sys.argv[2], "%Y-%m-%d").date() if len(sys.argv) > 2 else date.today()
        _process_file(sys.argv[1], d)
    else:
        _demo()
