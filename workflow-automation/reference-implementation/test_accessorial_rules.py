"""Tests for the accessorial triage rules engine.

Run with: python -m pytest test_accessorial_rules.py
      or:  python test_accessorial_rules.py   (falls back to a plain assert runner)
"""

from datetime import datetime as d

from accessorial_rules import (
    AccessorialPolicy,
    AccessorialRequest,
    AccessorialType,
    Decision,
    triage,
)

POLICY = AccessorialPolicy()


def test_small_clean_detention_auto_approves():
    # ~3.07h raw - 2h free = ~1.07h -> rounds to 1.0h * $35 = $35, under ceiling.
    req = AccessorialRequest(
        pro_number="A1", acc_type=AccessorialType.DETENTION,
        check_in=d(2026, 8, 19, 7, 39), check_out=d(2026, 8, 19, 10, 43),
        has_pod=True, customer_resolved=True)
    res = triage(req, POLICY)
    assert res.decision is Decision.AUTO_APPROVE
    assert res.pay_amount <= POLICY.auto_approve_pay_ceiling
    assert res.billable_hours > 0


def test_large_detention_over_ceiling_needs_review():
    # ~14.5h raw -> ~12.5h billable * $35 = ~$437 -> over $150 ceiling.
    req = AccessorialRequest(
        pro_number="A2", acc_type=AccessorialType.DETENTION,
        check_in=d(2026, 8, 17, 13, 17), check_out=d(2026, 8, 18, 3, 51),
        has_pod=True, customer_resolved=True)
    res = triage(req, POLICY)
    assert res.decision is Decision.NEEDS_REVIEW
    assert res.pay_amount > POLICY.auto_approve_pay_ceiling


def test_within_free_time_is_rejected():
    req = AccessorialRequest(
        pro_number="A3", acc_type=AccessorialType.DETENTION,
        check_in=d(2026, 8, 20, 9, 0), check_out=d(2026, 8, 20, 10, 30),
        has_pod=True, customer_resolved=True)
    res = triage(req, POLICY)
    assert res.decision is Decision.REJECT_SUGGESTED
    assert res.billable_hours == 0.0
    assert res.pay_amount == 0.0


def test_missing_pod_forces_review():
    req = AccessorialRequest(
        pro_number="A4", acc_type=AccessorialType.DETENTION,
        check_in=d(2026, 8, 20, 8, 0), check_out=d(2026, 8, 20, 11, 0),
        has_pod=False, customer_resolved=True)
    res = triage(req, POLICY)
    assert res.decision is Decision.NEEDS_REVIEW
    assert any("POD" in r for r in res.reasons)


def test_unresolved_customer_forces_review():
    req = AccessorialRequest(
        pro_number="A5", acc_type=AccessorialType.DETENTION,
        check_in=d(2026, 8, 20, 8, 0), check_out=d(2026, 8, 20, 11, 0),
        has_pod=True, customer_resolved=False)
    res = triage(req, POLICY)
    assert res.decision is Decision.NEEDS_REVIEW
    assert any("customer unresolved" in r for r in res.reasons)


def test_layover_always_reviewed():
    req = AccessorialRequest(
        pro_number="A6", acc_type=AccessorialType.LAYOVER,
        flat_amount=150.0, has_pod=True, customer_resolved=True)
    res = triage(req, POLICY)
    assert res.decision is Decision.NEEDS_REVIEW


def test_inconsistent_times_flagged():
    req = AccessorialRequest(
        pro_number="A7", acc_type=AccessorialType.DETENTION,
        check_in=d(2026, 8, 20, 12, 0), check_out=d(2026, 8, 20, 9, 0),
        has_pod=True, customer_resolved=True)
    res = triage(req, POLICY)
    assert res.decision is Decision.NEEDS_REVIEW
    assert any("precedes" in r for r in res.reasons)


def test_claimed_amount_mismatch_flags_review():
    # Clean small detention that would auto-approve, but rep's claimed total is wrong.
    req = AccessorialRequest(
        pro_number="A8", acc_type=AccessorialType.DETENTION,
        check_in=d(2026, 8, 19, 7, 39), check_out=d(2026, 8, 19, 10, 43),
        has_pod=True, customer_resolved=True, claimed_amount=999.0)
    res = triage(req, POLICY)
    assert res.decision is Decision.NEEDS_REVIEW
    assert any("claimed" in r for r in res.reasons)


def test_missing_times_needs_review():
    req = AccessorialRequest(
        pro_number="A9", acc_type=AccessorialType.DETENTION,
        has_pod=True, customer_resolved=True)
    res = triage(req, POLICY)
    assert res.decision is Decision.NEEDS_REVIEW
    assert any("times" in r for r in res.reasons)


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"  ok  {fn.__name__}")
    print(f"\n{passed}/{len(fns)} tests passed")


if __name__ == "__main__":
    _run_all()
