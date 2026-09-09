"""Accessorial leakage model — quantifies money Delta lost to un-billed / un-enforced
Rate Confirmation items (dry-run, no side effects).

WHAT THIS ANSWERS
-----------------
"Over the last 365 days, how much did Delta lose because accessorials/penalties weren't
billed to the customer and/or weren't paid/deducted to/from the carrier?"

It computes, per load, the gap between what the Rate Confirmation + customer rate sheet
ENTITLE Delta to, and what ACTUALLY happened, across three buckets:

  1. CUSTOMER UNDER-BILLING  — accessorials that occurred but were billed to the customer
     below the standard rate, or not at all.
  2. DEDUCTION UNDER-ENFORCEMENT — carrier penalties the Rate Confirmation allows
     (tracking failure, late/continued POD, missed check-calls, missing signed rate con,
     etc.) that were never charged back to the carrier — money Delta should have kept.
  3. CARRIER OVERPAYMENT — accessorials paid to the carrier while ineligible under the
     contract (carrier at fault, no signed proof, or customer never paid) — money Delta
     should not have paid out.

  Total leakage = (1) + (2) + (3).

THE DATA IT NEEDS (and does not yet have)
-----------------------------------------
The authoritative inputs live in McLeod (AR revenue by accessorial code + load-level
operational facts). Until a McLeod export is provided, this runs on clearly-labeled
SAMPLE data to demonstrate the mechanics. See `required_mcleod_columns()` for the exact
export schema, and `leakage-analysis.md` for how to produce it.

  >>> The printed SAMPLE total is illustrative only. It is NOT Delta's actual loss. <<<
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from accessorial_rules import (
    ChargeType, Direction, LoadFacts, RateConfirmationPolicy, Status, evaluate,
)


# --------------------------------------------------------------------------------------
# Customer rate sheet (from customer-accessorial-rate-sheet.md — provisional until
# validated against McLeod AR). Customer-side pricing for the SAME events.
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class CustomerRateSheet:
    detention_rate_solo: float = 49.0      # cost +40%
    detention_rate_team: float = 70.0
    detention_free_hours: float = 1.5      # tighter than carrier free time (2h)
    detention_min: float = 75.0
    layover_solo: float = 225.0            # cost +50%
    layover_team: float = 375.0
    tonu_solo: float = 225.0
    tonu_team: float = 350.0
    stopoff_each: float = 95.0             # min; matches current engine
    lumper_handling_fee: float = 50.0      # bill = carrier lumper cost + handling; never below cost


# --------------------------------------------------------------------------------------
# A load record = operational facts (LoadFacts) + what ACTUALLY got billed/paid/deducted.
# One row of the McLeod export maps to one of these.
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class LoadRecord:
    facts: LoadFacts
    delivered_on: Optional[datetime] = None

    # Third-party accessorials not in the carrier Rate Con but customer-billable:
    stopoff_count: int = 0
    lumper_cost: float = 0.0               # what Delta paid the facility/carrier for lumper

    # What ACTUALLY happened in the books (from McLeod AR / settlement):
    actual_customer_billed: float = 0.0    # total accessorial $ billed to the customer
    actual_carrier_paid: float = 0.0       # total accessorial $ paid to the carrier
    actual_deductions_taken: float = 0.0   # total penalty $ actually charged back to carrier


# --------------------------------------------------------------------------------------
# Expected values
# --------------------------------------------------------------------------------------

def _expected_carrier(assessment) -> tuple:
    """Sum expected carrier PAYABLE accessorials (that are owed, i.e. not rejected) and
    expected DEDUCTIONS from an engine assessment. Returns (payable, deductions)."""
    payable = 0.0
    deductions = 0.0
    for it in assessment.items:
        if it.status is Status.REJECTED:
            continue
        if it.direction is Direction.PAYABLE:
            payable += it.amount
        else:
            deductions += it.amount
    return round(payable, 2), round(deductions, 2)


def _expected_customer_bill(rec: LoadRecord, policy: RateConfirmationPolicy,
                            sheet: CustomerRateSheet) -> float:
    """What the customer SHOULD have been billed for accessorials on this load, per the
    standard rate sheet — only for accessorials that actually occurred and are owed."""
    f = rec.facts
    if f.carrier_at_fault:
        return 0.0  # not the customer's charge when the carrier caused it
    total = 0.0
    team = f.team_service

    # Detention (customer free time is tighter; caps at customer layover)
    if f.check_in and f.check_out:
        hrs = (f.check_out - f.check_in).total_seconds() / 3600.0
        billable = max(0.0, hrs - sheet.detention_free_hours)
        if billable > 0:
            rate = sheet.detention_rate_team if team else sheet.detention_rate_solo
            cap = sheet.layover_team if team else sheet.layover_solo
            amt = min(max(billable * rate, sheet.detention_min), cap)
            total += amt

    if f.layover_claimed:
        total += sheet.layover_team if team else sheet.layover_solo
    if f.tonu_claimed:
        total += sheet.tonu_team if team else sheet.tonu_solo
    if rec.stopoff_count > 0:
        total += rec.stopoff_count * sheet.stopoff_each
    if rec.lumper_cost > 0:
        total += rec.lumper_cost + sheet.lumper_handling_fee  # never below cost

    return round(total, 2)


# --------------------------------------------------------------------------------------
# Per-load leakage
# --------------------------------------------------------------------------------------

@dataclass
class LoadLeakage:
    pro_number: str
    customer_underbilling: float
    deduction_under_enforcement: float
    carrier_overpayment: float

    @property
    def total(self) -> float:
        return round(self.customer_underbilling
                     + self.deduction_under_enforcement
                     + self.carrier_overpayment, 2)


def leakage_for_load(rec: LoadRecord, policy: RateConfirmationPolicy,
                     sheet: CustomerRateSheet) -> LoadLeakage:
    assessment = evaluate(rec.facts, policy)
    exp_payable, exp_deductions = _expected_carrier(assessment)
    exp_customer = _expected_customer_bill(rec, policy, sheet)

    # 1. Customer under-billing: owed but not billed (never negative — over-billing is not
    #    "leakage" for this analysis).
    customer_underbilling = max(0.0, round(exp_customer - rec.actual_customer_billed, 2))

    # 2. Deductions the contract allows but that were never charged back.
    deduction_under = max(0.0, round(exp_deductions - rec.actual_deductions_taken, 2))

    # 3. Carrier overpayment: paid more than the eligible payable (e.g. paid an accessorial
    #    that should have been rejected/held).
    carrier_over = max(0.0, round(rec.actual_carrier_paid - exp_payable, 2))

    return LoadLeakage(rec.facts.pro_number, customer_underbilling,
                       deduction_under, carrier_over)


# --------------------------------------------------------------------------------------
# Portfolio roll-up
# --------------------------------------------------------------------------------------

@dataclass
class LeakageReport:
    loads: int = 0
    customer_underbilling: float = 0.0
    deduction_under_enforcement: float = 0.0
    carrier_overpayment: float = 0.0
    per_load: list = field(default_factory=list)

    @property
    def total(self) -> float:
        return round(self.customer_underbilling
                     + self.deduction_under_enforcement
                     + self.carrier_overpayment, 2)

    def render(self) -> str:
        L = [
            f"Loads analyzed:                {self.loads}",
            f"1. Customer under-billing:     ${self.customer_underbilling:>12,.2f}",
            f"2. Deductions un-enforced:     ${self.deduction_under_enforcement:>12,.2f}",
            f"3. Carrier overpayment:        ${self.carrier_overpayment:>12,.2f}",
            f"   {'-'*40}",
            f"   TOTAL LEAKAGE:              ${self.total:>12,.2f}",
        ]
        return "\n".join(L)


def run_leakage(records: list, policy: RateConfirmationPolicy,
                sheet: CustomerRateSheet) -> LeakageReport:
    rep = LeakageReport()
    for rec in records:
        ll = leakage_for_load(rec, policy, sheet)
        rep.loads += 1
        rep.customer_underbilling += ll.customer_underbilling
        rep.deduction_under_enforcement += ll.deduction_under_enforcement
        rep.carrier_overpayment += ll.carrier_overpayment
        rep.per_load.append(ll)
    rep.customer_underbilling = round(rep.customer_underbilling, 2)
    rep.deduction_under_enforcement = round(rep.deduction_under_enforcement, 2)
    rep.carrier_overpayment = round(rep.carrier_overpayment, 2)
    return rep


# --------------------------------------------------------------------------------------
# The McLeod export schema this model needs to run on real data
# --------------------------------------------------------------------------------------

#: Canonical CSV column contract shared with mcleod-extract/. One row per load delivered
#: in the trailing 365 days. Y/N columns carry "Y"/"N" (blank = unknown -> handled below).
CANONICAL_COLUMNS = [
    # identity / scope
    "pro_number", "delivered_date", "customer", "carrier", "team_service",
    "linehaul_rate",
    # operational facts (what happened at the stops)
    "stop_check_in", "stop_check_out", "carrier_at_fault",
    "signed_facility_proof", "revised_signed_ratecon",
    "customer_paid", "layover", "tonu", "stopoff_count",
    "lumper_cost", "driver_assist_preapproved",
    # penalty triggers
    "macropoint_tracking_provided", "arrived_on_time",
    "direct_run_violation", "missed_check_calls_count",
    "pod_late", "pod_days_late", "signed_ratecon_returned",
    "exclusive_use_violation",
    # actuals from AR / settlement (what was really billed/paid/deducted)
    "actual_customer_accessorial_billed", "actual_carrier_accessorial_paid",
    "actual_deductions_taken",
]


def required_mcleod_columns() -> list:
    """The McLeod export schema this model runs on. See CANONICAL_COLUMNS."""
    return list(CANONICAL_COLUMNS)


# --------------------------------------------------------------------------------------
# Load the real McLeod export (CSV) -> LoadRecords
# --------------------------------------------------------------------------------------

def _yn(v: str, default: bool) -> bool:
    """Parse a Y/N cell. Blank/unknown -> `default` (chosen per-field to keep the result a
    conservative floor, not an inflated headline)."""
    s = (v or "").strip().upper()
    if s in ("Y", "YES", "TRUE", "1"):
        return True
    if s in ("N", "NO", "FALSE", "0"):
        return False
    return default


def _dt(v: str):
    s = (v or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
                "%m/%d/%Y %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _num(v: str) -> float:
    s = (v or "").strip().replace("$", "").replace(",", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def load_records_from_csv(path: str) -> list:
    """Read a McLeod export (CANONICAL_COLUMNS) into LoadRecords. Blank Y/N cells fall to
    conservative defaults: penalties assume compliance (no charge) and accessorials assume
    the carrier was not at fault only where a charge event is already evidenced."""
    import csv as _csv
    recs = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for r in _csv.DictReader(fh):
            facts = LoadFacts(
                pro_number=(r.get("pro_number") or "").strip(),
                team_service=_yn(r.get("team_service"), False),
                linehaul_rate=_num(r.get("linehaul_rate")),
                check_in=_dt(r.get("stop_check_in")),
                check_out=_dt(r.get("stop_check_out")),
                carrier_at_fault=_yn(r.get("carrier_at_fault"), False),
                has_signed_facility_proof=_yn(r.get("signed_facility_proof"), False),
                has_revised_signed_ratecon=_yn(r.get("revised_signed_ratecon"), False),
                customer_has_paid=_yn(r.get("customer_paid"), False),
                driver_assist_preapproved=_yn(r.get("driver_assist_preapproved"), False),
                layover_claimed=_yn(r.get("layover"), False),
                tonu_claimed=_yn(r.get("tonu"), False),
                tracking_provided=_yn(r.get("macropoint_tracking_provided"), True),
                arrived_on_time=_yn(r.get("arrived_on_time"), True),
                direct_run_violation=_yn(r.get("direct_run_violation"), False),
                missed_check_calls=int(_num(r.get("missed_check_calls_count"))),
                pod_late=_yn(r.get("pod_late"), False),
                pod_days_late=int(_num(r.get("pod_days_late"))),
                signed_ratecon_returned=_yn(r.get("signed_ratecon_returned"), True),
                exclusive_use_violation=_yn(r.get("exclusive_use_violation"), False),
            )
            recs.append(LoadRecord(
                facts=facts,
                delivered_on=_dt(r.get("delivered_date")),
                stopoff_count=int(_num(r.get("stopoff_count"))),
                lumper_cost=_num(r.get("lumper_cost")),
                actual_customer_billed=_num(r.get("actual_customer_accessorial_billed")),
                actual_carrier_paid=_num(r.get("actual_carrier_accessorial_paid")),
                actual_deductions_taken=_num(r.get("actual_deductions_taken")),
            ))
    return recs


# --------------------------------------------------------------------------------------
# SAMPLE data — ILLUSTRATIVE ONLY. Not Delta's actuals. Demonstrates the calculator.
# --------------------------------------------------------------------------------------

def _sample_records() -> list:
    d = datetime
    return [
        # Detention occurred, customer never billed, no deductions in play.
        LoadRecord(
            LoadFacts("SAMPLE-1", check_in=d(2026, 3, 2, 8, 0), check_out=d(2026, 3, 2, 12, 30),
                      has_signed_facility_proof=True, has_revised_signed_ratecon=True,
                      customer_has_paid=True),
            actual_customer_billed=0.0, actual_carrier_paid=87.50, actual_deductions_taken=0.0),
        # Tracking failure + late POD, never charged back to the carrier.
        LoadRecord(
            LoadFacts("SAMPLE-2", linehaul_rate=2400.0, tracking_provided=False, pod_late=True),
            actual_customer_billed=0.0, actual_carrier_paid=0.0, actual_deductions_taken=0.0),
        # Lumper billed at cost (no handling) — the known company-wide leak.
        LoadRecord(
            LoadFacts("SAMPLE-3", has_signed_facility_proof=True, has_revised_signed_ratecon=True,
                      customer_has_paid=True),
            lumper_cost=246.0, actual_customer_billed=238.0, actual_carrier_paid=246.0,
            actual_deductions_taken=0.0),
        # Missing signed rate con — $50 deduction never taken.
        LoadRecord(
            LoadFacts("SAMPLE-4", signed_ratecon_returned=False),
            actual_deductions_taken=0.0),
        # TONU on a team load, billed short to the customer.
        LoadRecord(
            LoadFacts("SAMPLE-5", team_service=True, tonu_claimed=True,
                      has_signed_facility_proof=True, has_revised_signed_ratecon=True,
                      customer_has_paid=True),
            actual_customer_billed=150.0, actual_carrier_paid=250.0, actual_deductions_taken=0.0),
    ]


def _report(records: list, header: str) -> None:
    policy = RateConfirmationPolicy()
    sheet = CustomerRateSheet()
    rep = run_leakage(records, policy, sheet)
    print("=" * 66)
    print(f" {header}")
    print("=" * 66)
    print(rep.render())
    print("-" * 66)
    top = sorted(rep.per_load, key=lambda l: l.total, reverse=True)[:15]
    print(f" Top {len(top)} loads by leakage:")
    for ll in top:
        print(f"   {ll.pro_number:<12}  underbill ${ll.customer_underbilling:>8.2f}  "
              f"un-enforced ${ll.deduction_under_enforcement:>8.2f}  "
              f"overpay ${ll.carrier_overpayment:>7.2f}  = ${ll.total:>8.2f}")
    print("=" * 66)


def _demo() -> None:
    _report(_sample_records(),
            "ACCESSORIAL LEAKAGE — SAMPLE DATA (ILLUSTRATIVE, NOT DELTA ACTUALS)")
    print(" To run on real data, provide a McLeod export with these columns:")
    for c in required_mcleod_columns():
        print(f"   - {c}")
    print(" ...then:  python3 leakage_model.py --csv loads_365d.csv")
    print("=" * 66)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Accessorial leakage model.")
    ap.add_argument("--csv", help="McLeod export CSV (canonical columns). Omit for the sample demo.")
    args = ap.parse_args()
    if args.csv:
        recs = load_records_from_csv(args.csv)
        _report(recs, f"ACCESSORIAL LEAKAGE — {len(recs)} loads from {args.csv}")
    else:
        _demo()


if __name__ == "__main__":
    main()
