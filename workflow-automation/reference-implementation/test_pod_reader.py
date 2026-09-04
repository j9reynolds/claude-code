"""Tests for the POD reader (parse + decode + wired read flow).

OCR of real scanned PDFs needs pypdf/pdf2image/pytesseract + the tesseract binary, which
are production-runtime deps (not in the analysis sandbox). These tests cover everything
that does not need those binaries: time parsing, base64 decode, the plain-text OCR path,
and the full read_pod_times orchestration with an injected get_image.

Run:  python3 test_pod_reader.py   (or: python -m pytest test_pod_reader.py)
"""

import base64
from datetime import date, datetime

from pod_reader import (
    parse_pod_times, decode_get_image, ocr_document, read_pod_times, connector_get_image,
)


def test_parse_appointment_example_0169514():
    ci, co = parse_pod_times("Check-In: 11:15  Check-Out: 18:50", date(2025, 9, 12))
    assert ci == datetime(2025, 9, 12, 11, 15)
    assert co == datetime(2025, 9, 12, 18, 50)


def test_parse_in_out_military():
    ci, co = parse_pod_times("IN 1147 OUT 1446", date(2026, 8, 28))
    assert (ci.hour, ci.minute) == (11, 47)
    assert (co.hour, co.minute) == (14, 46)


def test_parse_overnight_rolls_to_next_day():
    ci, co = parse_pod_times("Arrived 22:30 Departed 04:15", date(2025, 7, 6))
    assert co > ci and co.day == 7


def test_parse_unreadable_returns_none():
    assert parse_pod_times("no times here", date(2025, 1, 1)) == (None, None)


def test_decode_get_image_base64_and_datauri():
    raw = b"Check-In: 08:00 Check-Out: 12:00"
    b64 = base64.b64encode(raw).decode()
    assert decode_get_image(b64) == raw
    assert decode_get_image("data:application/pdf;base64," + b64) == raw
    assert decode_get_image({"base64": b64}) == raw
    assert decode_get_image(None) is None
    assert decode_get_image("") is None


def test_ocr_document_plaintext_passthrough():
    assert "Check-In" in ocr_document(b"Check-In: 09:00 Check-Out: 15:00")


def test_read_pod_times_end_to_end_with_injected_get_image():
    # Injected get_image returns a base64 text "document" for image type 4; simulates the
    # live flow end to end (decode -> ocr_document plaintext -> parse).
    doc = base64.b64encode(b"POD  Check-In: 11:15  Check-Out: 18:50").decode()
    calls = []

    def fake_get_image(pro, image_type):
        calls.append((pro, image_type))
        return doc if image_type == 4 else None

    ci, co = read_pod_times("0169514", date(2025, 9, 12), fake_get_image)
    assert ci == datetime(2025, 9, 12, 11, 15)
    assert co == datetime(2025, 9, 12, 18, 50)
    assert calls[0][1] == 4                      # tried 04-Temporary POD first


def test_read_pod_times_falls_back_to_bol_then_review():
    # type 4 empty -> falls back to type 1; type 1 unreadable -> NEEDS_REVIEW.
    def fake_get_image(pro, image_type):
        return base64.b64encode(b"illegible scan").decode() if image_type == 1 else None
    assert read_pod_times("X", date(2025, 1, 1), fake_get_image) == (None, None)


def test_connector_adapter_passes_orderid():
    seen = {}
    def mcp_get_image(orderId=None):
        seen["orderId"] = orderId
        return base64.b64encode(b"Check-In: 10:00 Check-Out: 13:30").decode()
    fn = connector_get_image(mcp_get_image)
    ci, co = read_pod_times("0169514 ", date(2025, 9, 12), fn)
    assert seen["orderId"] == "0169514"          # trimmed, passed through
    assert (ci.hour, co.hour) == (10, 13)


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} tests passed")


if __name__ == "__main__":
    _run_all()
