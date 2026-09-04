# Production Deployment Spec — Accessorial Automation (#1)

How to take the accessorial engine from the dry-run reference implementation in this repo to
a live, human-gated production service against McLeod (LME_1720). Written for the engineer
who stands it up.

**Scope:** detention / layover / driver-assist / TONU accessorials and the Rate
Confirmation deductions, computed per load, POD-verified, and either auto-applied under a
policy ceiling or routed to a one-click human decision. Nothing writes to McLeod until
Phase 2, and nothing auto-writes until Phase 3.

---

## 1. What already exists (in this repo)

| Component | File | Role |
|-----------|------|------|
| Policy engine | `reference-implementation/accessorial_rules.py` | Encodes the full Rate Confirmation: appointment-based detention (caps at layover), layover, driver-assist, TONU, all deductions, eligibility gates, permission-gated override. Pure, 21 tests. |
| POD reader | `reference-implementation/pod_reader.py` | Live fetch (`connector_get_image`) → decode → OCR (`ocr_document`) → parse in/out. 9 tests. |
| Customer-side | `reference-implementation/leakage_model.py` + `customer-accessorial-rate-sheet.md` | Customer bill via the rate sheet; leakage math. |
| Data access | `mcleod-extract/*.sql` | Confirmed McLeod schema + read queries. |

The engine is **pure and side-effect-free by design.** Production is the thin I/O shell
around it: triggers in, McLeod + POD reads, the engine call, and gated writes out.

---

## 2. Architecture

```
 trigger ─▶ Orchestrator ─▶ gather LoadFacts (McLeod read) ─▶ POD read (get_image+OCR)
                               │                                        │
                               ▼                                        ▼
                        accessorial_rules.evaluate() ◀── appointment + POD in/out
                               │
             ┌─────────────────┼───────────────────────────┐
             ▼                 ▼                            ▼
        APPLIED           NEEDS_REVIEW / HELD           REJECTED
     (≤ ceiling)          → review queue + UI          (logged, no action)
             │                 │ (MANAGER/ADMIN one-click)
             ▼                 ▼
        write McLeod (Phase 3)   write McLeod on approval (Phase 2)
        other_charge (customer bill) + driver_extra_pay (carrier pay)
             │
             ▼
        audit log + daily digest
```

**Components to build (the shell):**
1. **Trigger** — one of: (a) a McLeod status change to Delivered (poll `orders` on a
   schedule, or a McLeod event/webhook if available); (b) an inbound accessorial-request
   email to the mgmt distro (M365) parsed into a load id. Start with (a) — it's complete
   and needs no email parsing.
2. **Orchestrator** — a stateless worker: for each candidate load, gather facts, read the
   POD, call the engine, route the result. Idempotent per `(order_id, stop, charge_type)`.
3. **Review queue + UI** — a lightweight web app (or a McLeod-adjacent screen) listing
   `NEEDS_REVIEW`/`HELD` items with the engine's basis; a MANAGER/ADMIN one-click
   Approve / Override(reason) / Reject. This is the human gate.
4. **Writer** — the only component with McLeod write access; applies approved line items to
   `other_charge` (customer) and `driver_extra_pay` (carrier). Disabled in Phase 1.
5. **Audit log + digest** — append-only record of every evaluation, decision, override
   (with actor + reason), and write; a daily email digest of auto-applied items.

---

## 3. Runtime & dependencies

- **Language/runtime:** Python 3.11+. The engine is stdlib-only; the shell adds the deps below.
- **Python packages:** `pyodbc` (or `pymssql`) for McLeod SQL; `requests` if using the McLeod
  REST API; `pypdf` (PDF text layer); `pdf2image` + `pytesseract` (scanned-POD OCR);
  a web framework for the queue UI (FastAPI/Flask).
- **System packages:** the **Tesseract OCR binary** and **Poppler** (`pdftoppm`, for
  `pdf2image`). These are the two the analysis sandbox lacked — they are required in the
  production image for scanned PODs. Bake them into the container.
- **Hosting:** a container on a host with **network line-of-sight to DB02 / the McLeod app
  server** (same VPC/VLAN or via a jump/VPN). The Claude cloud sandbox cannot reach DB02;
  this service must run on the Delta network. A small always-on worker + the queue UI.
- **Config store:** environment/secret manager for the McLeod connection string / API token
  (never in the repo); a versioned policy config file (section 8).

---

## 4. Access & scopes required (the ask to IT / McLeod admin)

**McLeod READ (service account):** `orders`, `movement`, `stop`, `other_charge`,
`driver_extra_pay`, `charge_code`, `customer`, `payee`, and the imaging endpoint
(`get_image` / DocumentPower) for image types **04-Temporary POD** and **01-BOL/POD**.

**McLeod WRITE (Phase 2+, separate least-privilege account):**
- create/update `other_charge` rows (customer accessorial billing), and
- create/update `driver_extra_pay` rows (carrier accessorial pay),
- scoped to accessorial charge codes only (DET/DL/DU, LAYO/LAYR/LYC, TONU, DRA, SOC/STP/XST,
  LMP…) — not linehaul, not GL postings.

**Role/permission map:** the identities that hold **MANAGER / ADMIN / SUPER_ADMIN** (the only
roles the engine lets override an auto-applied charge). Provide as a group/role list the UI
authenticates against (SSO/M365 groups preferred).

**Written policy sign-off:** the accessorial rate numbers, the auto-approve ceiling, and the
per-stop-vs-per-load detention cap (currently **per stop**, 2h free per stop) — section 8.

**Prerequisite:** database backups must exist for the McLeod store before any write phase
(this was an open urgent item). No writes ship on an un-backed-up production DB.

---

## 5. Per-event data flow (what the orchestrator does)

1. **Select** a delivered load not yet assessed (`orders.status='D'`, idempotency key unseen).
2. **Gather `LoadFacts`** from McLeod: customer, carrier (`movement.override_payee_id`),
   linehaul, team flag (unknown → false), per-stop `sched_arrive_early` (appointment) +
   `actual_arrival/departure`, tracking status, rate-con state, existing `other_charge` /
   `driver_extra_pay` (to avoid double-billing).
3. **Read the POD** for check-in/out via `pod_reader.read_pod_times(pro, stop_date,
   connector_get_image(get_image))` — 04 then 01. Unreadable → the item is forced to
   `NEEDS_REVIEW` (never assessed off McLeod's unreliable stop times).
4. **Evaluate**: build `LoadFacts` with the **appointment** and the **POD** in/out, call
   `evaluate(facts, policy)`. Detention clock = appointment + 2h (else arrival + 2h), per
   stop, cap at layover.
5. **Route** each line item by status: `APPLIED` (≤ ceiling, all gates passed) → auto path
   (Phase 3) ; `NEEDS_REVIEW`/`HELD_PENDING_CUSTOMER` → review queue ; `REJECTED` → log only.
6. **Compute the customer bill** for payable accessorials from the rate sheet, alongside the
   carrier pay.
7. **Write** (Phase 2 on approval / Phase 3 auto): create the `other_charge` (customer) and
   `driver_extra_pay` (carrier) rows; record the audit entry.

---

## 6. Rollout phases (with go/no-go gates)

**Phase 0 — Shadow (2–4 weeks), NO writes.** Run the orchestrator read-only over live
delivered loads. Every evaluation is logged; nothing is written or emailed to anyone but the
internal team. **Gate to Phase 1:** on a manager-reviewed sample of ≥200 items, the engine's
call matches the manager's decision ≥95% of the time; POD read success rate and the
`NEEDS_REVIEW` rate are understood; the detention cap and ceiling are tuned.

**Phase 1 — Assisted / one-click (writes on approval only).** The review queue goes live for
carrier-sales + a manager. Every accessorial — including small ones — requires a human click;
the engine pre-fills the amount, basis, and POD reference. **Gate to Phase 2:** approval
throughput is healthy, override reasons show no systematic engine error, zero mis-writes.

**Phase 2 — Auto under a low ceiling.** `APPLIED` items at/under the auto-approve ceiling
(start low, e.g. $75, then raise) write automatically; everything else stays one-click. Daily
digest to managers to spot-check auto-applied items; any override triggers a review of the
rule. **Gate to Phase 3:** auto-applied items hold up on audit; dispute rate flat or down.

**Phase 3 — Steady state.** Ceiling raised to the agreed level; the manual accessorial email
workflow is retired. Continuous monitoring per section 7.

At every gate the previous phase's kill switch (section 7) stays available.

---

## 7. Guardrails, safety, observability

- **Human-gated money.** No auto-write above the ceiling, ever. Driver-assist is always
  human (pre-approval only). `HELD_PENDING_CUSTOMER` never pays a carrier before Delta is
  paid. Missing/unreadable POD, carrier fault, or missing signed rate con → `NEEDS_REVIEW`.
- **Permission-gated override, audited.** Only MANAGER/ADMIN/SUPER_ADMIN may waive/adjust an
  auto-applied charge; every override stores actor + reason (the engine already enforces the
  reason). A regular user cannot.
- **Kill switch.** One config flag returns the whole service to Phase 0 (read-only) instantly;
  a per-charge-type flag disables one accessorial.
- **Idempotency & double-bill guard.** Key on `(order_id, stop_id, charge_type)`; before any
  write, re-check McLeod for an existing matching `other_charge`/`driver_extra_pay` row.
- **Reconciliation.** Nightly job compares what the service wrote vs what's in McLeod;
  any drift halts auto-writes and alerts.
- **Drift monitors.** Track POD-read success %, `NEEDS_REVIEW` %, auto-approve %, override %,
  and dispute/chargeback rate; alert on step changes.
- **Audit log.** Append-only, immutable, queryable: every evaluation input hash, decision,
  actor, and write id.

---

## 8. Configuration (versioned, signed off)

A single versioned config, not code, holds the policy so changes are reviewable:

- **Rate Confirmation (carrier):** detention $35/h solo · $50/h team, **2h free per stop**,
  caps at layover; layover $150/$250; TONU $150/$250; deduction amounts.
- **Detention cap:** **per stop** (current decision), 2h free applied at each stop.
- **Customer rate sheet:** from `customer-accessorial-rate-sheet.md` (layover $225/$375, TONU
  $225/$350, detention $50/$70 h min $75, lumper cost+$50, …).
- **Auto-approve ceiling:** start $75; raise by phase.
- **Image type numbers:** 04 = Temporary POD, 01 = BOL/POD (confirm the full list once).
- **Role map:** MANAGER/ADMIN/SUPER_ADMIN identities.
- **Time handling:** stop times are **stop-local wall clock** — no tz conversion for a
  within-stop duration; flag (don't silently miscompute) a dwell that crosses a DST change.

---

## 9. Testing, CI, and validation

- Keep the engine's unit tests green in CI (currently 40/40). Add shell tests: McLeod
  read mappers, the writer (against a McLeod **test/sandbox** company, never production),
  and POD OCR against a fixture set of real (redacted) PODs including scans.
- **Pre-write validation:** before each write, re-run the engine on freshly-read facts and
  assert the amount matches the approved amount (guards against stale data).
- **Backfill:** do **not** retro-bill 365 days automatically. Surface historical un-billed
  detention (the ~$531k eligibility-adjusted estimate) as a review worklist for humans to
  action selectively where within the customer contract and still billable.

---

## 10. Rollback

Any phase reverts to the prior phase via config with no redeploy. A bad write is reversed in
McLeod (the writer keeps the created row ids); auto-writes halt on the first reconciliation
mismatch. The manual email workflow is not decommissioned until Phase 3 is stable, so falling
back to it is always possible.

---

## 11. Go-live checklist

- [ ] Service account with McLeod **read** scopes (section 4).
- [ ] Least-privilege **write** account, accessorial charge codes only (Phase 2).
- [ ] McLeod **test company** available for writer tests.
- [ ] Role map (MANAGER/ADMIN/SUPER_ADMIN) wired to SSO/M365 groups.
- [ ] Container image with **Tesseract + Poppler** + Python deps; host with **DB02 line of
      sight**.
- [ ] Policy config signed off (rates, ceiling, per-stop cap).
- [ ] **Database backups** confirmed for the McLeod store.
- [ ] Staff heads-up sent (`employee-announcement.md`) before Phase 1.
- [ ] Audit log + daily digest live.
- [ ] Kill switch + reconciliation job tested.

---

## Open dependencies (blockers to schedule)

1. McLeod **write** scopes + a **test company** for the writer.
2. The **role/permission map**.
3. **Database backups** in place (open urgent item).
4. Policy **sign-off** (ceiling, per-stop cap already chosen).
5. A **host on the Delta network** with DB02 access and the OCR binaries.
