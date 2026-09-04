"""Rate Confirmation policy engine — reference implementation (dry-run, no side effects).

This is the pilot core of opportunity #1. It encodes Delta Group Logistics' carrier
Rate Confirmation: it evaluates a load's facts and AUTO-APPLIES every charge whose
conditions are met — both accessorials payable TO the carrier (detention, layover,
driver-assist, TONU) and deductions FROM the carrier (tracking failure, late service,
direct-run, missed check-calls, late POD, missing signed rate con, exclusive-use).

Two things the Rate Confirmation requires, encoded here:
  * Eligibility gates on accessorials — not carrier's fault, signed facility proof, a
    revised & signed rate con, and (for detention/layover/TONU/deadhead/re-consignment)
    Delta being paid by its customer first. Anything unmet is HELD or flagged, never
    silently paid.
  * Permission-gated override — an auto-applied charge can only be waived or adjusted by
    a user whose role is MANAGER, ADMIN, or SUPER_ADMIN. A regular USER cannot override.

It DELIBERATELY has no side effects: it computes, classifies, and explains. It sends no
email, writes nothing to McLeod, and creates no payable or deduction. Live wiring happens
later behind these same gates. Run `python3 accessorial_rules.py` for a dry-run demo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional


# --------------------------------------------------------------------------------------
# Roles & permissions
# --------------------------------------------------------------------------------------

class Role(str, Enum):
    USER = "USER"
    MANAGER = "MANAGER"
    ADMIN = "ADMIN"
    SUPER_ADMIN = "SUPER_ADMIN"


# Roles permitted to override an auto-applied charge (waive/adjust).
OVERRIDE_ROLES = frozenset({Role.MANAGER, Role.ADMIN, Role.SUPER_ADMIN})


class Direction(str, Enum):
    PAYABLE = "PAYABLE"      # Delta pays the carrier (accessorial earned)
    DEDUCTION = "DEDUCTION"  # Delta deducts from the carrier (penalty)


class Status(str, Enum):
    APPLIED = "APPLIED"                       # condition met, amount stands
    NEEDS_REVIEW = "NEEDS_REVIEW"             # required proof/consistency missing
    HELD_PENDING_CUSTOMER = "HELD_PENDING_CUSTOMER"  # owed, but not until customer pays
    REJECTED = "REJECTED"                     # not owed under the contract
    OVERRIDDEN = "OVERRIDDEN"                 # waived/adjusted by an authorized role


class ChargeType(str, Enum):
    # Accessorials payable to carrier
    DETENTION = "DETENTION"
    LAYOVER = "LAYOVER"
    DRIVER_ASSIST = "DRIVER_ASSIST"
    TONU = "TONU"
    # Deductions from carrier
    TRACKING_FAILURE = "TRACKING_FAILURE"        # MacroPoint not provided
    SERVICE_LATE = "SERVICE_LATE"                # late arrival vs appointment
    DIRECT_RUN = "DIRECT_RUN"                    # unauthorized stops/delays
    CHECK_CALL_MISSED = "CHECK_CALL_MISSED"      # missed/late required event
    POD_LATE = "POD_LATE"                        # POD not in within 1h of unload
    POD_CONTINUED = "POD_CONTINUED"              # continued POD delay, per day
    MISSING_SIGNED_RATECON = "MISSING_SIGNED_RATECON"
    EXCLUSIVE_USE_VIOLATION = "EXCLUSIVE_USE_VIOLATION"


# --------------------------------------------------------------------------------------
# Policy (the Rate Confirmation, as data)
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class RateConfirmationPolicy:
    """Delta Group Logistics carrier Rate Confirmation terms. Solo / team pairs match the
    signed document. Real per-load linehaul comes from McLeod at integration time."""
    # Detention
    detention_free_hours: float = 2.0
    detention_rate_solo: float = 35.0
    detention_rate_team: float = 50.0
    detention_rounding: str = "quarter_hour"   # none | quarter_hour | half_hour | hour
    # Layover (also the detention cap)
    layover_solo: float = 150.0
    layover_team: float = 250.0
    # TONU
    tonu_solo: float = 150.0
    tonu_team: float = 250.0
    # Deductions / penalties
    penalty_flat: float = 500.0                # tracking / service / direct-run floor
    penalty_pct_of_linehaul: float = 0.20      # ...or 20% of linehaul, whichever greater
    check_call_missed_each: float = 50.0
    pod_late_deduction: float = 150.0
    pod_continued_per_day: float = 250.0
    missing_ratecon_deduction: float = 50.0
    # Auto-approve ceiling for accessorials that otherwise pass all gates. Start low.
    auto_approve_pay_ceiling: float = 150.0


# --------------------------------------------------------------------------------------
# Load facts (what actually happened on the load)
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class LoadFacts:
    pro_number: str
    team_service: bool = False
    linehaul_rate: float = 0.0

    # Detention / dwell. check_in/check_out are the POD-documented times (authoritative;
    # McLeod's entered actual times are unreliable). appointment_time is the scheduled
    # appointment (McLeod sched_arrive_early) — when present, the detention clock starts
    # appointment + free time, regardless of an early arrival; else arrival + free time.
    check_in: Optional[datetime] = None
    check_out: Optional[datetime] = None
    appointment_time: Optional[datetime] = None

    # Accessorial eligibility gates (Rate Confirmation section 6)
    carrier_at_fault: bool = False             # was the delay/cancellation the carrier's fault?
    has_signed_facility_proof: bool = False    # signed doc from the facility
    has_revised_signed_ratecon: bool = False   # revised & signed rate con obtained in real time
    customer_has_paid: bool = False            # Delta received full customer payment

    # Discrete accessorial events
    driver_assist_preapproved: bool = False    # driver assist only if pre-approved
    layover_claimed: bool = False              # carrier claims a layover
    tonu_claimed: bool = False                 # truck ordered not used

    # Deduction triggers
    tracking_provided: bool = True             # MacroPoint active & uninterrupted
    arrived_on_time: bool = True               # met appointment windows
    direct_run_violation: bool = False         # unauthorized stops/delays
    missed_check_calls: int = 0                # count of missed/late required events
    pod_late: bool = False                     # POD not submitted within 1h of unload
    pod_days_late: int = 0                     # continued POD delay, in days
    signed_ratecon_returned: bool = True       # carrier returned a signed rate con
    exclusive_use_violation: bool = False      # shared/again-used vehicle


# --------------------------------------------------------------------------------------
# Line item + assessment
# --------------------------------------------------------------------------------------

@dataclass
class LineItem:
    charge_type: ChargeType
    direction: Direction
    amount: float
    status: Status
    basis: str
    override_note: Optional[str] = None
    override_by: Optional[Role] = None

    @property
    def signed_amount(self) -> float:
        """+ pays the carrier, - deducts from the carrier. Overridden items net to 0."""
        if self.status is Status.OVERRIDDEN:
            return 0.0
        s = 1.0 if self.direction is Direction.PAYABLE else -1.0
        return round(s * self.amount, 2)

    def render(self) -> str:
        arrow = "→carrier" if self.direction is Direction.PAYABLE else "←deduct "
        tag = self.status.value
        line = f"[{tag:>22}] {self.charge_type.value:<24} {arrow} ${self.amount:>8.2f}  {self.basis}"
        if self.status is Status.OVERRIDDEN:
            line += f"\n{'':>26} ↳ overridden by {self.override_by.value}: {self.override_note}"
        return line


@dataclass
class Assessment:
    pro_number: str
    items: list = field(default_factory=list)

    def net_carrier_pay(self) -> float:
        return round(sum(i.signed_amount for i in self.items), 2)

    def render(self) -> str:
        head = f"PRO {self.pro_number} — {len(self.items)} auto-applied line item(s)"
        body = "\n".join("  " + i.render() for i in self.items) if self.items else "  (none)"
        net = self.net_carrier_pay()
        return f"{head}\n{body}\n  {'':>22}  NET to carrier (excl. held/review): ${net:.2f}"


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------

def _round_hours(hours: float, mode: str) -> float:
    if hours <= 0:
        return 0.0
    if mode == "none":
        return hours
    step = {"quarter_hour": 0.25, "half_hour": 0.5, "hour": 1.0}.get(mode, 0.25)
    return round(hours / step) * step


def _accessorial_gate(f: LoadFacts) -> Optional[Status]:
    """Shared eligibility gate for payable accessorials. Returns a non-APPLIED status when
    the Rate Confirmation's conditions are not met, else None (meaning: passes the gate)."""
    if f.carrier_at_fault:
        return Status.REJECTED  # not payable when the carrier caused the delay/cancellation
    if not (f.has_signed_facility_proof and f.has_revised_signed_ratecon):
        return Status.NEEDS_REVIEW  # missing signed proof / revised signed rate con
    return None


def _penalty_amount(policy: RateConfirmationPolicy, linehaul: float) -> float:
    return round(max(policy.penalty_flat, policy.penalty_pct_of_linehaul * linehaul), 2)


# --------------------------------------------------------------------------------------
# The engine
# --------------------------------------------------------------------------------------

def evaluate(f: LoadFacts, policy: RateConfirmationPolicy) -> Assessment:
    """Evaluate a load against the Rate Confirmation. Pure: no I/O, no side effects."""
    items: list[LineItem] = []
    layover_cap = policy.layover_team if f.team_service else policy.layover_solo
    det_rate = policy.detention_rate_team if f.team_service else policy.detention_rate_solo
    svc = "team" if f.team_service else "solo"

    # ---- Accessorials payable to carrier ----

    # Detention (caps at the layover charge)
    if f.check_in and f.check_out:
        onsite = (f.check_out - f.check_in).total_seconds() / 3600.0
        # Detention clock: appointment + free time when an appointment exists (even if the
        # carrier arrived early); otherwise arrival + free time. Never exceeds on-site time.
        free_ref = f.appointment_time or f.check_in
        detention_start = free_ref + timedelta(hours=policy.detention_free_hours)
        raw = (f.check_out - detention_start).total_seconds() / 3600.0
        clock = "appointment" if f.appointment_time else "arrival"
        if onsite < 0:
            items.append(LineItem(ChargeType.DETENTION, Direction.PAYABLE, 0.0,
                                  Status.NEEDS_REVIEW,
                                  "check-out precedes check-in (inconsistent times)"))
        else:
            billable = _round_hours(max(0.0, min(raw, onsite)), policy.detention_rounding)
            uncapped = round(billable * det_rate, 2)
            amount = min(uncapped, layover_cap)
            capped = amount < uncapped
            basis = (f"{billable:.2f}h past {clock}+{policy.detention_free_hours:.0f}h x "
                     f"${det_rate:.0f}/h ({svc})"
                     + (f" — CAPPED at layover ${layover_cap:.0f}" if capped else ""))
            if billable <= 0:
                items.append(LineItem(ChargeType.DETENTION, Direction.PAYABLE, 0.0,
                                      Status.REJECTED,
                                      f"within {clock}+{policy.detention_free_hours:.0f}h free — none owed"))
            else:
                gate = _accessorial_gate(f)
                if gate is not None:
                    items.append(LineItem(ChargeType.DETENTION, Direction.PAYABLE, amount,
                                          gate, basis + _gate_note(f, gate)))
                elif not f.customer_has_paid:
                    items.append(LineItem(ChargeType.DETENTION, Direction.PAYABLE, amount,
                                          Status.HELD_PENDING_CUSTOMER,
                                          basis + " — held until customer pays"))
                else:
                    status = (Status.APPLIED if amount <= policy.auto_approve_pay_ceiling
                              else Status.NEEDS_REVIEW)
                    note = "" if status is Status.APPLIED else \
                        f" — ${amount:.2f} over auto-approve ceiling ${policy.auto_approve_pay_ceiling:.0f}"
                    items.append(LineItem(ChargeType.DETENTION, Direction.PAYABLE, amount,
                                          status, basis + note))

    # Layover
    if f.layover_claimed:
        amount = layover_cap  # layover flat = layover rate
        _append_gated_accessorial(items, ChargeType.LAYOVER, amount, f,
                                   f"layover flat ${amount:.0f} ({svc})", policy)

    # Driver assist — only if pre-approved; never auto-applies otherwise
    if f.driver_assist_preapproved:
        items.append(LineItem(ChargeType.DRIVER_ASSIST, Direction.PAYABLE, 0.0,
                              Status.NEEDS_REVIEW,
                              "driver assist pre-approved — enter negotiated amount for review"))

    # TONU
    if f.tonu_claimed:
        amount = policy.tonu_team if f.team_service else policy.tonu_solo
        _append_gated_accessorial(items, ChargeType.TONU, amount, f,
                                   f"TONU flat ${amount:.0f} ({svc})", policy)

    # ---- Deductions from carrier (auto-apply on the trigger) ----

    if not f.tracking_provided:
        items.append(LineItem(ChargeType.TRACKING_FAILURE, Direction.DEDUCTION,
                              _penalty_amount(policy, f.linehaul_rate), Status.APPLIED,
                              f"MacroPoint not provided — greater of ${policy.penalty_flat:.0f} "
                              f"or {policy.penalty_pct_of_linehaul:.0%} linehaul"))
    if not f.arrived_on_time:
        items.append(LineItem(ChargeType.SERVICE_LATE, Direction.DEDUCTION,
                              _penalty_amount(policy, f.linehaul_rate), Status.APPLIED,
                              "late vs appointment — greater of $500 or 20% linehaul"))
    if f.direct_run_violation:
        items.append(LineItem(ChargeType.DIRECT_RUN, Direction.DEDUCTION,
                              _penalty_amount(policy, f.linehaul_rate), Status.APPLIED,
                              "unauthorized stops/delays — greater of $500 or 20% linehaul"))
    if f.missed_check_calls > 0:
        amt = round(f.missed_check_calls * policy.check_call_missed_each, 2)
        items.append(LineItem(ChargeType.CHECK_CALL_MISSED, Direction.DEDUCTION, amt,
                              Status.APPLIED,
                              f"{f.missed_check_calls} missed/late event(s) x "
                              f"${policy.check_call_missed_each:.0f}"))
    if f.pod_late:
        items.append(LineItem(ChargeType.POD_LATE, Direction.DEDUCTION,
                              policy.pod_late_deduction, Status.APPLIED,
                              "POD not submitted within 1h of unload"))
    if f.pod_days_late > 0:
        amt = round(f.pod_days_late * policy.pod_continued_per_day, 2)
        items.append(LineItem(ChargeType.POD_CONTINUED, Direction.DEDUCTION, amt,
                              Status.APPLIED,
                              f"{f.pod_days_late} day(s) continued POD delay x "
                              f"${policy.pod_continued_per_day:.0f}/day"))
    if not f.signed_ratecon_returned:
        items.append(LineItem(ChargeType.MISSING_SIGNED_RATECON, Direction.DEDUCTION,
                              policy.missing_ratecon_deduction, Status.APPLIED,
                              "no signed rate confirmation on file"))
    if f.exclusive_use_violation:
        items.append(LineItem(ChargeType.EXCLUSIVE_USE_VIOLATION, Direction.DEDUCTION,
                              round(f.linehaul_rate, 2), Status.APPLIED,
                              "exclusive-use violation — 100% rate reduction"))

    return Assessment(f.pro_number, items)


def _gate_note(f: LoadFacts, gate: Status) -> str:
    if gate is Status.REJECTED:
        return " — carrier at fault, not owed"
    missing = []
    if not f.has_signed_facility_proof:
        missing.append("signed facility proof")
    if not f.has_revised_signed_ratecon:
        missing.append("revised signed rate con")
    return " — missing " + " + ".join(missing) if missing else ""


def _append_gated_accessorial(items, charge_type, amount, f, basis, policy):
    gate = _accessorial_gate(f)
    if gate is not None:
        items.append(LineItem(charge_type, Direction.PAYABLE, amount, gate,
                              basis + _gate_note(f, gate)))
    elif not f.customer_has_paid:
        items.append(LineItem(charge_type, Direction.PAYABLE, amount,
                              Status.HELD_PENDING_CUSTOMER, basis + " — held until customer pays"))
    else:
        status = (Status.APPLIED if amount <= policy.auto_approve_pay_ceiling
                  else Status.NEEDS_REVIEW)
        items.append(LineItem(charge_type, Direction.PAYABLE, amount, status, basis))


# --------------------------------------------------------------------------------------
# Permission-gated override
# --------------------------------------------------------------------------------------

class PermissionError_(Exception):
    """Raised when a role without override permission attempts to override a charge."""


def override_line_item(item: LineItem, actor_role: Role, note: str) -> LineItem:
    """Waive/adjust an auto-applied charge. Only MANAGER / ADMIN / SUPER_ADMIN may do so.

    Returns the same item mutated to OVERRIDDEN (nets to $0). A regular USER attempt
    raises PermissionError_ and changes nothing."""
    if actor_role not in OVERRIDE_ROLES:
        raise PermissionError_(
            f"{actor_role.value} may not override auto-applied charges "
            f"(requires MANAGER, ADMIN, or SUPER_ADMIN)")
    if not note or not note.strip():
        raise ValueError("override requires a reason note (audit trail)")
    item.status = Status.OVERRIDDEN
    item.override_by = actor_role
    item.override_note = note.strip()
    return item


# --------------------------------------------------------------------------------------
# Dry-run demo
# --------------------------------------------------------------------------------------

def _demo() -> None:
    policy = RateConfirmationPolicy()
    d = datetime
    print("DRY RUN — Rate Confirmation policy engine (nothing sent, nothing posted)\n")

    # Load A: clean small detention, all proof in hand, customer paid -> auto-applies.
    a = LoadFacts(pro_number="0197476", check_in=d(2026, 8, 19, 7, 39),
                  check_out=d(2026, 8, 19, 10, 43), has_signed_facility_proof=True,
                  has_revised_signed_ratecon=True, customer_has_paid=True)
    print(evaluate(a, policy).render(), "\n")

    # Load B: long detention (caps at layover), proof present but customer not paid yet.
    b = LoadFacts(pro_number="0197341", check_in=d(2026, 8, 17, 13, 17),
                  check_out=d(2026, 8, 18, 3, 51), has_signed_facility_proof=True,
                  has_revised_signed_ratecon=True, customer_has_paid=False)
    print(evaluate(b, policy).render(), "\n")

    # Load C: detention claimed but no signed proof -> needs review; plus tracking failure
    # and a late POD auto-deduct against the carrier.
    c = LoadFacts(pro_number="0200002", team_service=False, linehaul_rate=2400.0,
                  check_in=d(2026, 8, 20, 8, 0), check_out=d(2026, 8, 20, 12, 30),
                  has_signed_facility_proof=False, has_revised_signed_ratecon=False,
                  tracking_provided=False, pod_late=True, missed_check_calls=2)
    print(evaluate(c, policy).render(), "\n")

    # Load D: TONU on a team load, all gates passed -> over ceiling -> needs review.
    d_load = LoadFacts(pro_number="0200050", team_service=True, tonu_claimed=True,
                       has_signed_facility_proof=True, has_revised_signed_ratecon=True,
                       customer_has_paid=True)
    assessment = evaluate(d_load, policy)
    print(assessment.render())

    # Manager overrides the tracking-failure deduction on Load C (audit-logged).
    print("\n  -- override attempt on Load C tracking-failure deduction --")
    c_assess = evaluate(c, policy)
    track = next(i for i in c_assess.items if i.charge_type is ChargeType.TRACKING_FAILURE)
    try:
        override_line_item(track, Role.USER, "carrier says GPS glitched")
    except PermissionError_ as e:
        print(f"  USER blocked: {e}")
    override_line_item(track, Role.MANAGER, "one-time waiver; carrier provided ELD trail")
    print("  " + track.render())


if __name__ == "__main__":
    _demo()
