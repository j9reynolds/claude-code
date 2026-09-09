"""Tests for the accessorial leakage model.

Run with: python3 test_leakage_model.py   (or: python -m pytest test_leakage_model.py)
"""

from datetime import datetime as d

from accessorial_rules import LoadFacts, RateConfirmationPolicy
from leakage_model import (
    CustomerRateSheet, LoadRecord, leakage_for_load, run_leakage,
    required_mcleod_columns,
)

P = RateConfirmationPolicy()
S = CustomerRateSheet()


def test_customer_underbilling_when_detention_not_billed():
    # 4.5h dwell -> 3.0h billable customer @ $49 = $147 (>$75 min). Billed $0 -> loss $147.
    rec = LoadRecord(
        LoadFacts("L1", check_in=d(2026, 3, 2, 8, 0), check_out=d(2026, 3, 2, 12, 30),
                  has_signed_facility_proof=True, has_revised_signed_ratecon=True,
                  customer_has_paid=True),
        actual_customer_billed=0.0)
    ll = leakage_for_load(rec, P, S)
    assert ll.customer_underbilling == 147.0
    assert ll.deduction_under_enforcement == 0.0


def test_deduction_under_enforcement_counts_unbilled_penalties():
    # Tracking failure ($500) + late POD ($150) = $650 expected, none taken -> $650 loss.
    rec = LoadRecord(
        LoadFacts("L2", linehaul_rate=2400.0, tracking_provided=False, pod_late=True),
        actual_deductions_taken=0.0)
    ll = leakage_for_load(rec, P, S)
    assert ll.deduction_under_enforcement == 650.0


def test_deduction_partial_enforcement_only_counts_gap():
    rec = LoadRecord(
        LoadFacts("L3", linehaul_rate=2400.0, tracking_provided=False, pod_late=True),
        actual_deductions_taken=500.0)  # took tracking, missed the POD one
    assert leakage_for_load(rec, P, S).deduction_under_enforcement == 150.0


def test_lumper_underbilling_is_captured():
    # Expected customer bill = lumper cost 246 + 50 handling = 296; billed 238 -> loss 58.
    rec = LoadRecord(
        LoadFacts("L4", has_signed_facility_proof=True, has_revised_signed_ratecon=True,
                  customer_has_paid=True),
        lumper_cost=246.0, actual_customer_billed=238.0)
    assert leakage_for_load(rec, P, S).customer_underbilling == 58.0


def test_carrier_overpayment_flagged():
    # Nothing owed to the carrier, but $200 was paid -> $200 overpayment.
    rec = LoadRecord(LoadFacts("L5"), actual_carrier_paid=200.0)
    assert leakage_for_load(rec, P, S).carrier_overpayment == 200.0


def test_no_leak_when_everything_billed_correctly():
    # Detention billed at the expected customer amount, nothing else in play.
    rec = LoadRecord(
        LoadFacts("L6", check_in=d(2026, 3, 2, 8, 0), check_out=d(2026, 3, 2, 12, 30),
                  has_signed_facility_proof=True, has_revised_signed_ratecon=True,
                  customer_has_paid=True),
        actual_customer_billed=147.0)
    ll = leakage_for_load(rec, P, S)
    assert ll.total == 0.0


def test_carrier_fault_means_no_customer_charge_expected():
    rec = LoadRecord(
        LoadFacts("L7", check_in=d(2026, 3, 2, 8, 0), check_out=d(2026, 3, 2, 14, 0),
                  carrier_at_fault=True),
        actual_customer_billed=0.0)
    assert leakage_for_load(rec, P, S).customer_underbilling == 0.0


def test_overbilling_is_not_negative_leakage():
    # Customer billed MORE than expected -> not counted as leakage (floored at 0).
    rec = LoadRecord(
        LoadFacts("L8", check_in=d(2026, 3, 2, 8, 0), check_out=d(2026, 3, 2, 12, 30),
                  has_signed_facility_proof=True, has_revised_signed_ratecon=True,
                  customer_has_paid=True),
        actual_customer_billed=500.0)
    assert leakage_for_load(rec, P, S).customer_underbilling == 0.0


def test_portfolio_rollup_sums_buckets():
    recs = [
        LoadRecord(LoadFacts("R1", linehaul_rate=2400.0, tracking_provided=False)),  # $500 ded
        LoadRecord(LoadFacts("R2", signed_ratecon_returned=False)),                  # $50 ded
    ]
    rep = run_leakage(recs, P, S)
    assert rep.loads == 2
    assert rep.deduction_under_enforcement == 550.0
    assert rep.total == 550.0


def test_export_schema_lists_actuals_and_facts():
    cols = required_mcleod_columns()
    assert "actual_customer_accessorial_billed" in cols
    assert "actual_deductions_taken" in cols
    assert "stop_check_in" in cols


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} tests passed")


if __name__ == "__main__":
    _run_all()
