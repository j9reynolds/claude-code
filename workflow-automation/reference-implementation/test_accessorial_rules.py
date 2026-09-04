"""Tests for the Rate Confirmation policy engine.

Run with: python -m pytest test_accessorial_rules.py
      or:  python3 test_accessorial_rules.py   (plain assert runner)
"""

from datetime import datetime as d

from accessorial_rules import (
    ChargeType, Direction, LoadFacts, RateConfirmationPolicy, Role, Status,
    evaluate, override_line_item, override_line_item as _ov, PermissionError_,
)

P = RateConfirmationPolicy()


def _first(assessment, ct):
    return next(i for i in assessment.items if i.charge_type is ct)


def _has(assessment, ct):
    return any(i.charge_type is ct for i in assessment.items)


# ---- Detention ----

def test_clean_small_detention_applies_when_customer_paid():
    f = LoadFacts("A1", check_in=d(2026, 8, 19, 7, 39), check_out=d(2026, 8, 19, 10, 43),
                  has_signed_facility_proof=True, has_revised_signed_ratecon=True,
                  customer_has_paid=True)
    it = _first(evaluate(f, P), ChargeType.DETENTION)
    assert it.status is Status.APPLIED
    assert it.direction is Direction.PAYABLE
    assert it.amount <= P.auto_approve_pay_ceiling


def test_detention_caps_at_layover():
    # ~14.5h dwell -> would be ~12.5h * $35 = ~$437, but caps at $150 solo layover.
    f = LoadFacts("A2", check_in=d(2026, 8, 17, 13, 17), check_out=d(2026, 8, 18, 3, 51),
                  has_signed_facility_proof=True, has_revised_signed_ratecon=True,
                  customer_has_paid=True)
    it = _first(evaluate(f, P), ChargeType.DETENTION)
    assert it.amount == P.layover_solo
    assert "CAPPED" in it.basis


def test_detention_team_rate_and_cap():
    f = LoadFacts("A3", team_service=True, check_in=d(2026, 8, 17, 13, 0),
                  check_out=d(2026, 8, 18, 3, 0), has_signed_facility_proof=True,
                  has_revised_signed_ratecon=True, customer_has_paid=True)
    it = _first(evaluate(f, P), ChargeType.DETENTION)
    assert it.amount == P.layover_team  # caps at team layover $250


def test_detention_within_free_time_rejected():
    f = LoadFacts("A4", check_in=d(2026, 8, 20, 9, 0), check_out=d(2026, 8, 20, 10, 30),
                  has_signed_facility_proof=True, has_revised_signed_ratecon=True,
                  customer_has_paid=True)
    it = _first(evaluate(f, P), ChargeType.DETENTION)
    assert it.status is Status.REJECTED
    assert it.amount == 0.0


def test_detention_held_until_customer_pays():
    f = LoadFacts("A5", check_in=d(2026, 8, 20, 8, 0), check_out=d(2026, 8, 20, 11, 30),
                  has_signed_facility_proof=True, has_revised_signed_ratecon=True,
                  customer_has_paid=False)
    it = _first(evaluate(f, P), ChargeType.DETENTION)
    assert it.status is Status.HELD_PENDING_CUSTOMER


def test_detention_missing_proof_needs_review():
    f = LoadFacts("A6", check_in=d(2026, 8, 20, 8, 0), check_out=d(2026, 8, 20, 11, 30),
                  has_signed_facility_proof=False, has_revised_signed_ratecon=True,
                  customer_has_paid=True)
    it = _first(evaluate(f, P), ChargeType.DETENTION)
    assert it.status is Status.NEEDS_REVIEW
    assert "signed facility proof" in it.basis


def test_carrier_fault_rejects_accessorial():
    f = LoadFacts("A7", check_in=d(2026, 8, 20, 8, 0), check_out=d(2026, 8, 20, 12, 0),
                  carrier_at_fault=True, has_signed_facility_proof=True,
                  has_revised_signed_ratecon=True, customer_has_paid=True)
    it = _first(evaluate(f, P), ChargeType.DETENTION)
    assert it.status is Status.REJECTED


def test_inconsistent_times_flagged():
    f = LoadFacts("A8", check_in=d(2026, 8, 20, 12, 0), check_out=d(2026, 8, 20, 9, 0),
                  has_signed_facility_proof=True, has_revised_signed_ratecon=True,
                  customer_has_paid=True)
    it = _first(evaluate(f, P), ChargeType.DETENTION)
    assert it.status is Status.NEEDS_REVIEW
    assert "precedes" in it.basis


# ---- Other accessorials ----

def test_tonu_team_over_ceiling_needs_review():
    f = LoadFacts("B1", team_service=True, tonu_claimed=True,
                  has_signed_facility_proof=True, has_revised_signed_ratecon=True,
                  customer_has_paid=True)
    it = _first(evaluate(f, P), ChargeType.TONU)
    assert it.amount == P.tonu_team          # $250
    assert it.status is Status.NEEDS_REVIEW  # over the $150 ceiling


def test_driver_assist_only_when_preapproved_and_never_auto():
    with_pre = evaluate(LoadFacts("B2", driver_assist_preapproved=True), P)
    assert _first(with_pre, ChargeType.DRIVER_ASSIST).status is Status.NEEDS_REVIEW
    without = evaluate(LoadFacts("B3", driver_assist_preapproved=False), P)
    assert not _has(without, ChargeType.DRIVER_ASSIST)


# ---- Deductions ----

def test_tracking_failure_uses_greater_of_flat_or_pct():
    f = LoadFacts("C1", linehaul_rate=3000.0, tracking_provided=False)
    it = _first(evaluate(f, P), ChargeType.TRACKING_FAILURE)
    assert it.direction is Direction.DEDUCTION
    assert it.amount == 600.0  # 20% of 3000 > $500
    assert it.status is Status.APPLIED


def test_tracking_failure_floor_applies_on_small_linehaul():
    f = LoadFacts("C2", linehaul_rate=1000.0, tracking_provided=False)
    assert _first(evaluate(f, P), ChargeType.TRACKING_FAILURE).amount == 500.0


def test_missed_check_calls_multiply():
    f = LoadFacts("C3", missed_check_calls=3)
    assert _first(evaluate(f, P), ChargeType.CHECK_CALL_MISSED).amount == 150.0


def test_pod_late_and_continued_are_separate():
    f = LoadFacts("C4", pod_late=True, pod_days_late=2)
    a = evaluate(f, P)
    assert _first(a, ChargeType.POD_LATE).amount == P.pod_late_deduction
    assert _first(a, ChargeType.POD_CONTINUED).amount == 500.0  # 2 * $250


def test_exclusive_use_is_full_rate_reduction():
    f = LoadFacts("C5", linehaul_rate=2200.0, exclusive_use_violation=True)
    assert _first(evaluate(f, P), ChargeType.EXCLUSIVE_USE_VIOLATION).amount == 2200.0


def test_missing_signed_ratecon_deduction():
    f = LoadFacts("C6", signed_ratecon_returned=False)
    assert _first(evaluate(f, P), ChargeType.MISSING_SIGNED_RATECON).amount == 50.0


def test_net_pay_signs_payable_positive_deduction_negative():
    # A layover payable ($150, customer paid) minus a $50 missing-ratecon deduction = $100.
    f = LoadFacts("C7", layover_claimed=True, has_signed_facility_proof=True,
                  has_revised_signed_ratecon=True, customer_has_paid=True,
                  signed_ratecon_returned=False)
    assert evaluate(f, P).net_carrier_pay() == 100.0


# ---- Permission-gated override ----

def test_user_cannot_override():
    f = LoadFacts("D1", tracking_provided=False, linehaul_rate=1000.0)
    it = _first(evaluate(f, P), ChargeType.TRACKING_FAILURE)
    try:
        override_line_item(it, Role.USER, "carrier says glitch")
        assert False, "USER override should have raised"
    except PermissionError_:
        pass
    assert it.status is Status.APPLIED  # unchanged


def test_manager_can_override_and_it_zeros_out():
    f = LoadFacts("D2", tracking_provided=False, linehaul_rate=1000.0)
    it = _first(evaluate(f, P), ChargeType.TRACKING_FAILURE)
    override_line_item(it, Role.MANAGER, "one-time waiver; ELD trail provided")
    assert it.status is Status.OVERRIDDEN
    assert it.override_by is Role.MANAGER
    assert it.signed_amount == 0.0


def test_override_requires_a_note():
    f = LoadFacts("D3", tracking_provided=False, linehaul_rate=1000.0)
    it = _first(evaluate(f, P), ChargeType.TRACKING_FAILURE)
    try:
        override_line_item(it, Role.ADMIN, "   ")
        assert False, "empty note should have raised"
    except ValueError:
        pass


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} tests passed")


if __name__ == "__main__":
    _run_all()
