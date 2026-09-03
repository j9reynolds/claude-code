"""Accessorial approval triage — reference rules engine (dry-run, no side effects).

This module is the safe core of opportunity #1 (see ../build-plan.md). It takes a
normalized accessorial request (detention, layover, driver-assist, TONU) and returns a
structured decision plus a human-readable explanation.

It DELIBERATELY does nothing else. It sends no email, approves nothing in McLeod, creates
no payable, and reads no live system. Wiring it to real inputs/outputs happens later,
behind a human approval gate, per the rollout in build-plan.md.

Design goals:
  * Pure and deterministic: same input -> same output. Trivially unit-testable.
  * Policy is data (AccessorialPolicy), not code. Your written policy sets the numbers.
  * Fail safe: anything ambiguous, inconsistent, or unresolved -> NEEDS_REVIEW, never auto.

Run `python accessorial_rules.py` for a dry-run demo over sample requests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class AccessorialType(str, Enum):
    DETENTION = "detention"          # driver held beyond free time at a stop
    LAYOVER = "layover"              # overnight hold
    DRIVER_ASSIST = "driver_assist"  # driver helped load/unload
    TONU = "tonu"                    # truck ordered not used


class Decision(str, Enum):
    AUTO_APPROVE = "AUTO_APPROVE"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    REJECT_SUGGESTED = "REJECT_SUGGESTED"


@dataclass(frozen=True)
class AccessorialPolicy:
    """Your written policy, as data. Numbers here are placeholders — set them yourself.

    Rates are illustrative; real per-customer / per-carrier rates come from McLeod at
    integration time and override these defaults.
    """
    free_time_hours: float = 2.0            # free time before detention accrues
    detention_rate_per_hour: float = 35.0   # default carrier pay per detention hour
    detention_bill_per_hour: float = 50.0   # default customer bill per detention hour
    rounding: str = "quarter_hour"          # none | quarter_hour | half_hour | hour
    # Auto-approve only when the carrier PAY amount is at/under this ceiling. Start low.
    auto_approve_pay_ceiling: float = 150.0
    # Types that must always be reviewed by a human regardless of amount.
    always_review_types: tuple = (AccessorialType.LAYOVER, AccessorialType.TONU)


@dataclass(frozen=True)
class AccessorialRequest:
    """Normalized request. In production these fields are populated from McLeod + the POD;
    the email's hand-typed values are used only to cross-check."""
    pro_number: str
    acc_type: AccessorialType
    check_in: Optional[datetime] = None
    check_out: Optional[datetime] = None
    appointment: Optional[datetime] = None
    has_pod: bool = False
    customer_resolved: bool = True           # did we map this to a known CRM customer?
    # Optional overrides sourced from McLeod (preferred over policy defaults):
    pay_rate_per_hour: Optional[float] = None
    bill_rate_per_hour: Optional[float] = None
    flat_amount: Optional[float] = None       # for TONU/layover flat charges
    # The rep's hand-computed total, if present, purely for a sanity cross-check:
    claimed_amount: Optional[float] = None


@dataclass
class TriageResult:
    decision: Decision
    pay_amount: float
    bill_amount: float
    billable_hours: float
    reasons: list = field(default_factory=list)

    @property
    def summary(self) -> str:
        head = f"[{self.decision.value}] {self.pro_number_display}"
        money = (f"pay ${self.pay_amount:.2f} / bill ${self.bill_amount:.2f}"
                 f" ({self.billable_hours:.2f} billable h)")
        why = "; ".join(self.reasons) if self.reasons else "no notes"
        return f"{head} — {money} — {why}"

    # set by triage() so summary can show it without threading the request through
    pro_number_display: str = ""


def _round_hours(hours: float, mode: str) -> float:
    if hours <= 0:
        return 0.0
    if mode == "none":
        return hours
    step = {"quarter_hour": 0.25, "half_hour": 0.5, "hour": 1.0}.get(mode, 0.25)
    # round to nearest step
    return round(hours / step) * step


def triage(req: AccessorialRequest, policy: AccessorialPolicy) -> TriageResult:
    """Classify one accessorial request. Pure: no I/O, no side effects."""
    reasons: list[str] = []

    # --- flat-charge types (TONU, or layover billed flat) ---
    if req.flat_amount is not None:
        pay = req.flat_amount
        bill = req.flat_amount  # bill side set from customer terms at integration time
        result = TriageResult(Decision.NEEDS_REVIEW, pay, bill, 0.0, reasons)
        result.pro_number_display = req.pro_number
        if not req.has_pod:
            reasons.append("missing POD")
        if not req.customer_resolved:
            reasons.append("customer unresolved")
        if req.acc_type in policy.always_review_types:
            reasons.append(f"{req.acc_type.value} always reviewed by policy")
        else:
            reasons.append("flat charge — review")
        return result

    # --- time-based types (detention, driver-assist billed hourly) ---
    if req.check_in is None or req.check_out is None:
        result = TriageResult(Decision.NEEDS_REVIEW, 0.0, 0.0, 0.0,
                              ["missing check-in/check-out times"])
        result.pro_number_display = req.pro_number
        return result

    raw_hours = (req.check_out - req.check_in).total_seconds() / 3600.0
    if raw_hours < 0:
        result = TriageResult(Decision.NEEDS_REVIEW, 0.0, 0.0, 0.0,
                              ["check-out precedes check-in (inconsistent times)"])
        result.pro_number_display = req.pro_number
        return result

    over_free = max(0.0, raw_hours - policy.free_time_hours)
    billable = _round_hours(over_free, policy.rounding)

    pay_rate = req.pay_rate_per_hour if req.pay_rate_per_hour is not None \
        else policy.detention_rate_per_hour
    bill_rate = req.bill_rate_per_hour if req.bill_rate_per_hour is not None \
        else policy.detention_bill_per_hour

    pay = round(billable * pay_rate, 2)
    bill = round(billable * bill_rate, 2)

    result = TriageResult(Decision.NEEDS_REVIEW, pay, bill, billable, reasons)
    result.pro_number_display = req.pro_number

    # No billable time beyond free time -> nothing owed.
    if billable <= 0:
        result.decision = Decision.REJECT_SUGGESTED
        reasons.append(f"within free time ({policy.free_time_hours:.2f}h) — no accessorial owed")
        return result

    # Hard blockers that always force human review.
    if not req.has_pod:
        reasons.append("missing POD")
    if not req.customer_resolved:
        reasons.append("customer unresolved")
    if req.acc_type in policy.always_review_types:
        reasons.append(f"{req.acc_type.value} always reviewed by policy")

    # Cross-check the rep's hand-typed total, if provided.
    if req.claimed_amount is not None and abs(req.claimed_amount - pay) > 0.01:
        reasons.append(
            f"claimed ${req.claimed_amount:.2f} != computed ${pay:.2f} — verify times/rate")

    blocked = bool(reasons)

    if not blocked and pay <= policy.auto_approve_pay_ceiling:
        result.decision = Decision.AUTO_APPROVE
        reasons.append(
            f"within auto-approve ceiling (${policy.auto_approve_pay_ceiling:.0f}) "
            f"and inputs consistent")
    else:
        result.decision = Decision.NEEDS_REVIEW
        if pay > policy.auto_approve_pay_ceiling and not any("ceiling" in r for r in reasons):
            reasons.append(
                f"pay ${pay:.2f} over auto-approve ceiling "
                f"${policy.auto_approve_pay_ceiling:.0f}")

    return result


def _demo() -> None:
    """Dry-run demo over representative requests. Prints only; changes nothing."""
    policy = AccessorialPolicy()
    d = datetime  # shorthand
    samples = [
        # Clean small detention -> should auto-approve.
        AccessorialRequest(
            pro_number="0197476", acc_type=AccessorialType.DETENTION,
            check_in=d(2026, 8, 19, 7, 39), check_out=d(2026, 8, 19, 10, 43),
            has_pod=True, customer_resolved=True, claimed_amount=None),
        # Large detention (14h) -> over ceiling -> needs review.
        AccessorialRequest(
            pro_number="0197341", acc_type=AccessorialType.DETENTION,
            check_in=d(2026, 8, 17, 13, 17), check_out=d(2026, 8, 18, 3, 51),
            has_pod=True, customer_resolved=True),
        # Within free time -> reject suggested.
        AccessorialRequest(
            pro_number="0200001", acc_type=AccessorialType.DETENTION,
            check_in=d(2026, 8, 20, 9, 0), check_out=d(2026, 8, 20, 10, 30),
            has_pod=True, customer_resolved=True),
        # Missing POD -> needs review even if small.
        AccessorialRequest(
            pro_number="0200002", acc_type=AccessorialType.DETENTION,
            check_in=d(2026, 8, 20, 8, 0), check_out=d(2026, 8, 20, 11, 0),
            has_pod=False, customer_resolved=True),
        # Layover -> always review by policy.
        AccessorialRequest(
            pro_number="0197977", acc_type=AccessorialType.LAYOVER,
            flat_amount=150.0, has_pod=True, customer_resolved=True),
    ]
    print("DRY RUN — accessorial triage (no emails sent, no approvals posted)\n")
    for r in samples:
        print("  " + triage(r, policy).summary)


if __name__ == "__main__":
    _demo()
