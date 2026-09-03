# Build Plan (Step 5: Building the Winners)

One section per opportunity: trigger, inputs, logic, guardrails, access required, and a
staged rollout. Everything that moves money or sends external email is designed
**dry-run first -> human-approved -> auto under policy**, never big-bang.

Legend for status:
- **BUILT (dry-run)** — runnable in this repo, no side effects.
- **SPEC** — implementation-ready design; needs access + sign-off to build live.

---

## #1 Accessorial approval triage  —  BUILT (dry-run) + SPEC for live wiring

**Goal.** Replace hand-composed detention/layover/driver-assist/TONU approval emails with
a structured queue that computes the charge, checks it against policy, and either
auto-approves within threshold or routes a one-click decision to a manager.

**Trigger.** New email to the accessorial/management distro matching the request template
(or, better, a "request accessorial" action in McLeod once available).

**Inputs.**
- From the email/POD: PROnumber, accessorial type, appointment time, check-in / check-out.
- From McLeod (system of record, preferred over the email's hand-typed values):
  order, customer, carrier, posted accessorial rates, free-time terms, appointment.

**The engine now encodes the full signed carrier Rate Confirmation, not just the four
accessorials.** It auto-applies every charge whose conditions are met — both accessorials
payable to the carrier and deductions from the carrier — and gates overrides by role.

**Logic (implemented in `reference-implementation/accessorial_rules.py`).**
1. Take normalized `LoadFacts` (times, team-service flag, linehaul, tracking/POD/check-call
   status, and the eligibility flags below). In production these come from McLeod + the POD.
2. **Accessorials payable to carrier:**
   - *Detention* — hours over free time x rate (solo/team), **capped at the layover rate**.
   - *Layover* — flat solo/team.
   - *Driver assist* — only if pre-approved; never auto-applies, always routes to review.
   - *TONU* — flat solo/team.
3. **Deductions from carrier (auto-apply on the trigger):** MacroPoint tracking failure and
   late-service and direct-run (greater of $500 or 20% linehaul), missed/late check-calls
   ($50 each), late POD ($150) and continued delay ($250/day), missing signed rate con
   ($50), exclusive-use violation (100% rate reduction).
4. **Eligibility gates from the Rate Confirmation:** an accessorial is `REJECTED` when the
   carrier was at fault; `NEEDS_REVIEW` when signed facility proof or the revised signed
   rate con is missing; `HELD_PENDING_CUSTOMER` for detention/layover/TONU/deadhead/
   re-consignment until Delta is paid by its customer. Otherwise `APPLIED` if within the
   auto-approve ceiling, else `NEEDS_REVIEW`.
5. Emit a structured assessment (net carrier pay, every line item, its status and basis).

**Permission-gated override.** Every auto-applied charge is overridable **only** by a user
whose role is `MANAGER`, `ADMIN`, or `SUPER_ADMIN`; a regular `USER` attempt is refused. An
override requires a reason note (audit trail) and nets the line to $0. This mirrors the
requirement that only privileged users can waive an auto-applied charge.

**Also produces the customer-bill side.** The same rules drive the customer invoice line
via the markup table in `customer-accessorial-rate-sheet.md` (pending McLeod AR validation).

**Guardrails.**
- **Dry-run default:** the engine computes, classifies, and explains; it sends no email,
  writes nothing to McLeod, and creates no payable or deduction.
- Auto-approve ceiling is configuration, set by your policy, and starts low.
- Fail-safe: missing proof, carrier fault, unpaid customer, inconsistent times, or an amount
  over the ceiling never auto-applies.
- Overrides are role-checked and logged.

**Access required to go live.** McLeod read (orders/stops/rates/appointments/tracking) +
write (create accessorial / deduction / approval); M365 mailbox read for the trigger; the
role/permission map (who is MANAGER/ADMIN/SUPER_ADMIN). The policy numbers are already
captured from the signed Rate Confirmation.

**Rollout.** (a) Dry-run over 2-4 weeks of historical loads; compare the engine's calls to
what managers actually approved; tune the ceiling. (b) Shadow mode: engine posts its
assessment into the thread, humans still click approve. (c) Auto-apply under the agreed
ceiling; everything over it, plus every override, stays a privileged one-click.

---

## #2 Accounting: ACH ingest, intercompany, GL-coded AP  —  SPEC (phased, finance sign-off)

**Goal.** Eliminate the daily/weekly copy-paste across bank portals, Excel, and the
accounting ledger. The accounting system of record is **McLeod's accounting module** (per
the PM); the mined SOP also references **QuickBooks** for the DFS / DFS-EL entities —
confirm which entities post where before scoping (see the discrepancy note in
`discovery-findings.md`). The design below targets whichever ledger is current per entity.

**Phase A — read-only reconciliation (low risk, build first).**
- Pull ACH credits (cleared + pending) from Chase + Huntington via bank feed
  (BAI2/CSV/API), normalize, and replace the hand-maintained Excel "ACH spreadsheet" with
  a generated, sorted, classified ledger view.
- Reconcile against the McLeod accounting module (and QuickBooks where an entity still uses
  it); surface only the exceptions.

**Phase B — proposed-entries queue (human posts).**
- Generate the intercompany journal-entry pairs (the mechanical "duplicate the record,
  flip debit/credit, match the date" step) as *proposed* ledger entries a human reviews and
  posts with one click. Same for ProLease -> accounting invoice export, with import-error
  auto-remediation.
- GL coding for AP suggested from the documented decision tree (company truck / LTB /
  trailer / driver-deduction / recruiting) as a rules table; human confirms.

**Phase C — scheduled posting (only once trusted).**
- Auto-post the most mechanical, lowest-risk pieces (intercompany duplication, ProLease
  export, fixed recurring vendor payments) on schedule, with a daily reconciliation
  report and a hard stop on any mismatch.

**Guardrails.** Two-entity separation preserved; every posting reversible and logged;
dual-control on anything that pays; **database backups must exist first** (currently an
open urgent item). No phase moves money without finance sign-off.

**Access required.** Bank portal feeds (read; later payment initiation), the McLeod
accounting module (cash receipts / AR / AP / GL; plus QuickBooks for any entity still on
it), ProLease export. Finance owner as approver.

---

## #3 Rate-quote productionization  —  SPEC (mostly built internally)

**Goal.** Finish the in-flight "DGL Command Center" quote engine loop.

**What's left (not a rebuild).** Move out of `[TEST]`; fix customer-domain resolution
(depends on #7); deliver the recommendation *in the rep's workflow* (reply-draft or McLeod
panel) instead of a parallel email; capture accept/counter/loss outcomes to close the
learning loop.

**Guardrails.** Recommendation only — the rep still sends the quote. Log every rec vs.
final quote vs. outcome for calibration.

**Access required.** Continue existing Command Center access; CRM write for the domain
map (#7); McLeod lane history (already used).

---

## #4 Load-status / check-call auto-relay  —  SPEC

**Goal.** Convert tracked events into structured, routed notifications; retire hand-typed
status/exception emails.

**Trigger.** Project44 / MacroPoint status events (already live for some customers) +
McLeod milestones.

**Logic.** Map event -> audience (customer / carrier rep / after-hours) -> templated
notification. Exceptions (detention starting, appointment mismatch, "won't load box
truck") open a structured exception with an owner instead of a free-text email.

**Guardrails.** Customer-facing notifications start as drafts for approval; internal
ones can auto-send. Rate-limit and dedupe so tracking noise doesn't spam.

**Access required.** P44 + MacroPoint event feeds, McLeod milestones, M365 send (drafts
first).

---

## #5 Document auto-filing (POD / rate-con / onboarding)  —  SPEC (quick win)

**Goal.** Auto-route operational documents to the right SharePoint pod/customer folder.

**Trigger.** New attachment in the ops mailbox / a watched intake folder.

**Logic.** Parse PROnumber + lane from subject/filename -> resolve McLeod order ->
customer/pod -> file into the correct library with a normalized name; flag unmatched for
a human.

**Guardrails.** Filing only — no money, no external comms. Unmatched -> review queue.
Read-only against McLeod for resolution.

**Access required.** M365 (mailbox + SharePoint write), McLeod read.

---

## #6 Recurring report assembly  —  SPEC (quick win)

**Goal.** Auto-assemble fixed-cadence reports (e.g. USPS monthly; per-salesperson account
health, already in flight as dgl-command-center PR #12).

**Trigger.** Schedule (monthly/weekly).

**Logic.** Pull from McLeod/CRM on the report's template, render, and deliver as a draft
to the sender for a final look.

**Guardrails.** Draft-for-approval before any customer-facing send; internal reports can
auto-send.

**Access required.** McLeod + CRM read, M365 send (drafts first).

---

## #7 CRM hygiene / customer-domain matching  —  SPEC (enabler)

**Goal.** Build and maintain the sender-domain -> customer -> lane-history mapping that
#1, #3, and #6 rely on for correct attribution.

**Logic.** Backfill a domain map from historical email + McLeod customers; flag ambiguous
matches for a human; keep it current as new customers appear. Also normalize the stale
"<Customer> - New Deal" records and dedupe.

**Guardrails.** Suggestions reviewed before merge; no auto-delete of CRM records.

**Access required.** HubSpot read/write, McLeod customer read, M365 read for domain
backfill.

---

## Cross-cutting: access still needed

To move any of these from SPEC to live, the highest-leverage connections to add are:

1. **McLeod (TMS)** — read for #1/#3/#4/#5/#6; write for #1. This is the keystone; it
   unblocks five of seven.
2. **McLeod accounting module + bank feeds** (plus QuickBooks for any entity still on it) —
   for #2 (finance sign-off required).
3. **Project44 + MacroPoint event feeds** — for #4 (partly present already).
4. **Written policies** — accessorial thresholds (#1) and GL coding rules (#2). These are
   decisions only you can make; the automations encode them, they don't invent them.

Tell me which to prioritize and I'll scope the specific connection + a dry-run pilot.
