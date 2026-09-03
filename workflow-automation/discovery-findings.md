# Discovery Findings (Step 3: Systems Mining)

Process-level patterns found by mining Microsoft 365 (Outlook/Teams/SharePoint),
HubSpot CRM, and Linear. Each finding names the pattern, the evidence, why it is a good
automation candidate, and the toil it represents. Evidence is described at the process
level; individual names and account numbers are intentionally omitted.

## Operating context

DGL/DFS is a freight brokerage + carrier group (DGL brokerage, DFS asset/carrier, DFS
Equipment Leasing for trailers). **McLeod** is the TMS *and* the accounting system of
record — AR/AP/GL run in **McLeod's accounting module** (confirmed by the PM). Surrounding
it: Microsoft 365 (email is the primary operational channel), HubSpot CRM, Chase +
Huntington bank portals, ProLease (trailer rentals), Project44 + MacroPoint (tracking),
and load boards (Truckstop, DAT, MODE IQ). An
internal software program is already underway (in-house TMS, "DGL Command Center",
tracking portals, P44 push, encrypted rate cons) — visible in Linear and in automated
"[TEST]" quote emails already flowing.

The single most important structural fact: **email is where operational workflows
actually run.** Approvals, exceptions, status, and handoffs move as threaded messages to
shared distros, not as structured records. That is exactly where repetitive, rules-based
toil accumulates.

---

## Finding 1 — Accessorial approval-by-email (detention, layover, driver-assist, TONU)

**Pattern.** Carrier sales reps email a management distro for every accessorial charge.
Each message follows a near-identical template: PROnumber + lane, appointment time,
check-in/check-out times, a hand-computed hours x rate total, POD attached, and a
"please advise / please approve" ask. Approvals come back as free-text replies.

**Evidence.** In a ~4-week window, a *single* carrier sales rep sent 15+ such requests
(detention at shipper, detention at receiver, layover, driver-assist). The carrier-sales
distribution list carries ~20 reps, so the true volume is a large multiple of that.
Multiple threads show "Good morning, following up on this" chase messages — i.e.,
approvals stall and reps spend time re-poking.

**Why it automates well.** Highly structured inputs, a hand-done arithmetic step
(time-in/time-out -> billable hours x posted rate), and a decision that is mostly a
policy threshold. The times and PODs it needs already exist in McLeod / the POD.

**Toil.** Per request: rep composes email, does the math, attaches POD, waits, chases;
a manager reads, re-derives, replies. Multiply by ~hundreds/month. Also a margin leak:
detention that should be billed to the customer and/or paid to the carrier gets missed or
delayed, and stalled approvals strain carrier relationships.

---

## Finding 2 — Accounting: daily/weekly/monthly manual data movement

**Pattern.** A documented back-office role runs a large, brittle, copy-paste routine
across bank portals, Excel, McLeod (accounting module), and ProLease.

> **Discrepancy to confirm.** The PM confirms the accounting system of record is
> **McLeod's accounting module, not QuickBooks.** The back-office SOP that was mined,
> however, describes entering records into "DFS QB" / "DFS EL QB" (QuickBooks) for the
> DFS and DFS Equipment Leasing entities. Best read: DGL runs on McLeod accounting while
> the DFS/DFS-EL entities still use (or recently used) QuickBooks — or the SOP is legacy.
> Confirm which entities post where before scoping #2; the automation targets whatever is
> current. References below reflect the SOP as written.

**Evidence.** A role SOP document details, among others:
- **Daily:** log into two bank portals, pull ACH credits (cleared + pending), copy/paste
  into an Excel "ACH spreadsheet", sort largest-to-smallest, re-key classifications;
  export CSVs from saved report views; review positive-pay screens.
- **Weekly (Mon):** post intercompany deposits in McLeod, then hand-create matching
  duplicate journal entries in QuickBooks for the other entity (copy an existing record,
  change date + both debit/credit amounts, color-code the spreadsheet cell when done).
- **Weekly (Thu):** pay contractors/carriers/vendors; manually pick the correct GL
  account per invoice from a memorized decision tree (company truck vs LTB vs trailer
  vs driver-deduction vs recruiting).
- **Weekly:** enter every credit-card charge into two QuickBooks files by hand.
- **Monthly:** export ProLease trailer-rental invoices to QuickBooks, fixing import
  errors item-by-item; schedule ACH collections; pay a fixed list of recurring vendors
  (gas, water, dental, security) from emailed bills; match toll bills to trucks.

**Why it automates well.** Almost every step is deterministic data movement between
systems that have APIs or structured exports (bank BAI/CSV, McLeod accounting, and
QuickBooks where still used). The GL
decision tree is a rules table. The intercompany duplication is mechanical.

**Toil.** This is the single largest concentration of manual hours found, and the
highest error/reconciliation risk (manual journal entries across two legal entities,
hand-keyed amounts, color-coded spreadsheet as the source of truth).

---

## Finding 3 — Rate quoting (partly automated already)

**Pattern.** Inbound customer quote requests -> a rate recommendation (bill/pay,
detention/stopoff accessorials, market vs. DGL-history reconciliation).

**Evidence.** "[TEST] The DGL Command Center - Quote Recommendation" emails are already
being generated automatically, including handling of disagreeing signals (live market
feed vs. McLeod lane history), unresolved-customer detection ("sender not in
crm.CustomerDomain; no name match"), and equipment-band fallbacks. This is an in-flight
internal build, currently in TEST.

**Why it matters here.** It is ~80% built. The automation opportunity is finishing the
loop (productionize out of TEST, resolve the customer-domain matching gap that Finding 7
addresses, and route the recommendation to the quoting rep in-workflow rather than as a
separate email).

---

## Finding 4 — Load status / check-call / exception relay by email

**Pattern.** Reps and after-hours staff manually relay load status and exceptions
between carrier, customer, and internal teams: "driver on site, they say they don't load
box trucks — can you check?", "truck loaded, BOL + seal attached, good to go?", "unit on
site for pickup".

**Evidence.** A steady stream of short status/exception emails to shared distros,
including after-hours handoffs, interleaved with the accessorial threads.

**Why it automates well.** Tracking is *already* partly automated (Project44 push live for
two customers, MacroPoint integration in progress). The manual relay is the gap: turning
tracked events + exception types into structured, routed notifications instead of
hand-typed emails.

---

## Finding 5 — Document filing: PODs, rate cons, carrier onboarding packets

**Pattern.** Operational documents are named by a convention (PROnumber + lane, e.g.
`0198613 IL-PA`) and filed by hand into SharePoint "POD#/RateApprovals" sites; carrier
onboarding master agreements and independent-contractor packets are dropped into
per-pod folders.

**Evidence.** SharePoint holds POD/rate-approval libraries and onboarding PDFs organized
per pod; the PROnumber+lane naming is consistent across the email corpus and the files.

**Why it automates well.** Deterministic parse (PROnumber from subject/filename ->
McLeod order -> correct pod/customer folder). Low risk (filing, not money or comms).

---

## Finding 6 — Recurring report assembly

**Pattern.** Periodic reports are assembled and emailed by hand (e.g. a monthly USPS
report to a customer contact; per-salesperson "Account Health" emails).

**Evidence.** A "USPS Monthly Report - Aug 2026" email with attachment; a Linear issue
already in progress to build "per-salesperson Account Health emails" (dgl-command-center
PR #12) — confirming this is recognized internally as automatable.

**Why it automates well.** Fixed cadence, fixed template, data pulled from McLeod/CRM.
Low risk. Good trust-building early win.

---

## Finding 7 — CRM hygiene & customer-domain matching

**Pattern.** HubSpot deals are stale and generically named ("<Customer> - New Deal",
last-modified months back); the quoting automation (Finding 3) explicitly fails to
resolve some senders to a CRM customer ("sender not in crm.CustomerDomain; no name
match").

**Evidence.** Deal records last modified 1-2+ months ago in early stages; the TEST quote
emails surface unresolved-customer cases.

**Why it matters.** This is the connective tissue for Findings 1, 3, and 6: a clean
customer-domain -> customer -> lane-history mapping is what lets quoting, accessorial
billing, and reporting attribute correctly and automatically.

---

## What was NOT found (and why that is useful)

- **No shared team chat culture in Slack for ops.** The Slack workspace exists but ops
  work does not run there; it runs in Outlook/Teams email + distros. Automations should
  meet people in **email + McLeod**, not a chat tool they don't use for this.
- **No structured accessorial or approval records.** These decisions live only in email
  threads, which is why they are invisible to reporting and ripe for a structured system.
- **Zero database backups noted** for the production TMS data platform (an urgent Linear
  item). Not an "automation opportunity" per se, but flagged: automating on top of an
  un-backed-up production store raises the stakes on getting backups first.
