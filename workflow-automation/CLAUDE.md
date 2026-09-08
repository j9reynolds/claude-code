# Project Memory — Workflow & Automation Program (Delta Group Logistics / DFS)

Directory-scoped memory for the `workflow-automation/` initiative. Read this first when
resuming. It captures what this is, what's built, the hard constraints discovered, and the
open decisions. Everything here is process-level — no PII, no account numbers, no secrets.

## What this is

A program to identify and build automations for **Delta Group Logistics (DGL) / Delta
Freight Systems (DFS)**, a freight brokerage + carrier group (DGL brokerage; DFS
asset/carrier; DFS Equipment Leasing for trailers). Owner/PM: Justin Reynolds.

**System of record:** McLeod (LoadMaster Enterprise / LME) is both the **TMS and the
accounting module** (AR/AP/GL). *Not QuickBooks* per the PM — but the mined back-office SOP
still references "DFS QB"/"DFS EL QB" for the DFS/DFS-EL entities. Treat McLeod as
authoritative; the QuickBooks reference is a **discrepancy to confirm** (legacy, or
entity-specific), not settled.

**Surrounding systems:** Microsoft 365 (Outlook/Teams/SharePoint — email is the real
operational channel), HubSpot CRM, Linear, Chase/Huntington banks, ProLease, Project44 +
MacroPoint (tracking), load boards (Truckstop/DAT/MODE). An internal dev effort is already
live: the **"DGL Command Center"** (in-house TMS, AI quoting, account-health, tracking).

## Guardrails (do not violate)

1. **Process-level only.** No covert employee monitoring; analyze process artifacts, not
   individuals. Committed files name roles, not people; no account numbers; no secrets.
2. **Human-gated money & external comms.** Anything that pays a carrier, bills a customer,
   posts to the ledger, or emails a customer/carrier ships dry-run → human-approved →
   auto-under-policy. Never big-bang.
3. **Never fabricate figures.** No invented dollar amounts (e.g. "Delta lost $X"). Real
   numbers require real data from McLeod.

## McLeod access — via the `dgl-mcp` connector (READ tools; caps lifted + deployed 2026-09-08)

The PM added a first-party MCP connector **`dgl-mcp`** that reaches McLeod (LME_1720). Read
tools available: `search_orders` (pageable — see below), `get_order`
(header + stops, real appointment + actual times, `otherchargetotal` LUMP), `get_movement`
(carrier, `override_pay_amt`, `rate_confirmation_status`/`_sent_date`), `get_customer`,
`get_carrier` (payee⋈drs_payee), `get_image` (BOL/POD from DocumentPower), `list_comments`,
`resolve_identifier`, **`mcleod_query`** (arbitrary read-only SELECT). WRITE tool
`create_comment` exists — **do not call without explicit authorization.**

The old blocking limits — `search_orders` hard-capped at 50 rows, and no way to break the
`otherchargetotal` lump apart — are **gone as of 2026-09-08**. `DGL_McLeod_MCP#3` (+ CI in the
same PR, + `#4` fixing `update-mcp.ps1`) is merged to `master`, deployed via `update-mcp.ps1`,
and **verified against live db02**:
- `search_orders` **pages**: `limit` + `offset`, `ORDER BY ordered_date DESC, id DESC`, with
  `has_more`/`next_offset` measured by fetching one row past the page. Verified live: 200 rows
  in one call, `offset:200` continuing with no overlap or gap.
- `get_carrier`, `get_customer`, `list_comments` take `limit` (`TOP (@limit)`).
- **`mcleod_query`** runs a caller-supplied SELECT — aggregates, joins, whole-book scans, and
  the `other_charge` breakdown the lump hides. Values bind via a `parameters` JSON object;
  never concatenate. Response carries `columns`, `rows`, `row_count`, `elapsed_ms`, and a
  `truncated` flag measured by reading one row past the cap.
- **Effective caps: default 200 rows, max 1000.** The deployed `appsettings.json` carries none
  of the new `Mcp:MaxRows`/`DefaultRows`/`QueryTimeoutSeconds` keys (`update-mcp.ps1` preserves
  it), so the in-code fallbacks apply — confirmed live by `max_rows:200`/`max_limit:1000` in a
  real response. Raising them is an appsettings edit, not a code change.
- Read-only rests on three layers: `db_datareader` on lme_1720, `ApplicationIntent=ReadOnly`,
  and a free-form guard. Verified live: `UPDATE orders SET status = 'V'` returned
  `"McLeodSql is read-only: only SELECT statements are permitted."` and never reached db02.
  Every `mcleod_query` call, refused ones included, is audit-logged to the `read_audit` table.

**`other_charge` codes — partially answered (2026-09-08).** A live `mcleod_query` returned
`FSC` = Fuel Surcharge and `STP` = Stop. So fuel and accessorials ARE separable per-code; the
lump was a connector limitation, not a schema one. The full code set is still to be enumerated
— run this and record the result here:
```sql
SELECT charge_id, LTRIM(RTRIM(descr)) AS descr, COUNT(*) AS n, SUM(amount) AS total
FROM other_charge GROUP BY charge_id, LTRIM(RTRIM(descr)) ORDER BY SUM(amount) DESC
```
Expect Detention / TONU / Layover / Lumper to appear as their own codes and map onto
`customer-accessorial-rate-sheet.md` (`STP` ↔ its Stopoff row).

**Operating the connector — gotchas that cost a multi-day outage (2026-09-07/08):**
- The `DGL-McLeodMcp` service **must** log on as `Delta\J.Reynolds` in `DOMAIN\user` form. It
  was set to the UPN `J.Reynolds@DeltaGroupLog.com`; SCM cannot resolve that, so the service
  failed to start with *"The account name is invalid or does not exist, or the password is
  invalid"* and stayed down for ~40h. Symptom chain: service Stopped → nothing on
  `127.0.0.1:8092` → healthy `cloudflared` forwards to a dead origin → **502 at
  `mcp.dglops.com`** → claude.ai reports "Couldn't register with dgl-mcp's sign-in service".
  A 502 there means check the SERVICE first, not Cloudflare/WAF/DNS. It must stay that account
  — the ws API credentials live in that user's Windows Credential Manager.
- Changing service config needs **local admin on the box**. Entra/M365 Global Admin does NOT
  grant it — the box is AD domain-joined; local Administrators holds `Delta\Admins Brongus`,
  `Delta\Admins Brongus L1`, `Delta\Domain Admins`. Check with `whoami /groups`.
- `dotnet test` at the repo root fails (`MSB1003`) on any SDK below 9.0.200 because of the
  `.slnx` solution file — always name the test project. `#4` fixed `update-mcp.ps1` for this;
  the repo also now has CI (`.github/workflows/ci.yml`) running the suite on every push and PR.
- After a redeploy, an already-connected client keeps the OLD tool list until it re-handshakes.
  New *parameters* pass through to the server, but a new *tool* is invisible until reconnect —
  reconnect the connector (or start a fresh session) before concluding a deploy failed.

Confirmed real schema (from connector responses): `orders`(id, customer_id, status
[D=delivered/A/V/P], on_hold, curr_movement_id, freight_charge, otherchargetotal,
total_charge, ordered_date, bill_date, equipment_type_id); `stop`(movement_id, stop_type
[PU/SO], sched_arrive_early/late, actual_arrival/actual_departure, timezone_id — stop-local
wall clock, no tz marker); `movement`(id, order_id, carrier_id, override_pay_amt, target_pay,
max_buy, rate_confirmation_status, rate_confirmation_sent_date); carrier = `payee` ⋈
`drs_payee`.

Direct SQL to DB02 and the McLeod REST API remain unreachable from the sandbox (no route;
egress proxy 403s non-allowlisted hosts) — the connector is the only in-session path.

## Mailbox access — via the Microsoft 365 connector (default since 2026-09-08)

Email is the real operational channel, so how the watcher reaches it is a first-class
constraint, not a detail. **The connector is the only mailbox path that works here.**

- **IMAP with an app password is a dead end on this tenant.** Exchange Online has broadly
  disabled basic auth for IMAP; the app password is refused however correct it is
  (`NO AUTHENTICATE failed. Provided authentication mechanism is not supported`). No amount
  of credential fixing helps. Cloud sessions cannot use it either — outbound 993 is blocked,
  only HTTPS/443 through the egress proxy is reachable. `scripts/fetch_mail.py` and
  `Import-OpsCredential.ps1 -TestImap` remain correct for tenants that still permit basic auth.
- **The M365 connector itself is healthy** — verified live 2026-09-08: `get_me`,
  `outlook_email_search`, `read_resource` all return normally for the signed-in account.
  A mailbox problem is a *config* problem until proven otherwise; probe the connector first.
- **`mcleod-ops` now defaults to `adapter: "microsoft365"`** (was `imap`, which could not work).
  Four things the adapter block encodes, each a real failure mode rather than a preference:
  - **Read-only is enforced by an allowlist.** The connector exposes `outlook_send_mail`,
    `outlook_forward_mail`, `outlook_batch_delete_messages` in the same tool list as its
    search. The watcher calls only what `read_tools` names; `never_call` lists 13 neighbours.
  - **Ledger keys on `internetMessageId`** (RFC822 Message-ID, brackets stripped) — never the
    connector's own `id`, which is mailbox- and folder-scoped. A message moved to Archive
    re-keys under the latter, defeats the claim, and dispatches a second agent at a carrier
    that was already answered. Stripping the brackets also matches what `fetch_mail.py`
    produces, so the ledger survives an adapter switch. **Not hypothetical:** two live pages
    of this mailbox held 25 rows / 19 distinct Message-IDs and 25 rows / 20 distinct — 6 and 5
    duplicate folder-copies respectively, including repeats on one in-flight load. Keyed on the
    connector's `id` those pages would have dispatched 11 redundant agents; keyed on
    `internetMessageId` the ledger refused every one.
  - **Search returns a truncated preview, not a body.** Routes match on body content (pickup
    city, delivery city, rate), so each surviving message needs a second `read_resource` call
    on the returned `uri`. Attachments arrive on that same read — and are decided there, not
    from the search row's `hasAttachments`. **Measured semantics (2026-09-08, three messages
    both ways):** that flag means *"has at least one NON-inline attachment."* Two messages
    carrying only inline signature images reported `false` while the full read returned 6 and
    7 attachments; a report carrying an inline logo plus a real `.xlsx` reported `true`. So a
    `false` flag does **not** hide a rate con or POD — those are non-inline and set it `true`.
    Read from the full list anyway: it is what `attachment_handling` (`read_types`,
    `max_size_mb`) needs, and the flag says nothing about inline content. Low severity; the
    earlier framing of this as a document-intake miss was wrong and is corrected here.
  - **The search caps at 25 results per request** whatever `limit` asks, and reports it
    (`moreResults` / `nextOffset` / `totalResultCount`). Above `max_events_per_cycle` 25 the
    cycle must page by `offset`, or leave the cursor short and report a coverage gap. Same
    doctrine as the McLeod connector, same permanent loss if ignored.
- **Watching a shared/ops mailbox** rather than the signed-in one needs **Full Access** granted
  in the M365 admin center (`Mail.Read.Shared`), and `mailbox` set to that address. Missing
  access returns a permission error, not an empty inbox — never read that as a quiet zero.
  Two filter quirks: with a shared mailbox, free-text `query` and sender/date filters are
  mutually exclusive, and `recipient` is unsupported. A plain date window avoids both.
- **Known coverage gap:** the window filters on `receivedDateTime`, so the cycle sees messages
  as they arrive — not edits, moves, deletes, or Sent Items. A reply a human already sent by
  hand is invisible, so the watcher can route an already-answered thread.
- **Never scope the mailbox query to a folder.** Measured live 2026-09-08: an Inbox-scoped
  search returned **1** message where the unscoped search returned **47**, because Outlook
  rules file most ops mail out of Inbox before anyone reads it. A `folderName` parameter on
  the watch query would silently hide ~98% of the desk. The `folders` key in `sources.json`
  is set to `"*"` as a marker meaning unscoped; it is not a folder name and must not be
  turned into a query parameter.


## First live watch cycle — 2026-09-08, `observe` (the pilot's first real run)

`/ops-watch` ran end to end on the PM's Windows box against the real desk. **105 events
claimed, 6 handlers dispatched, 3 escalated, every cursor advanced, 21m17s.** Nothing was
sent, drafted, or written to McLeod — routes were lowered to `observe` for the first cycles
(they ship at `draft`; raise them back one at a time). This is the first evidence the whole
chain works on live traffic, and it produced findings the desk did not already have.

**Control gaps confirmed with real orders — the rate-con gate is not being recorded.** The
contract's "signed rate con returned in real time" requirement showed up unmet on live loads
in a single cycle: two orders **BOOKED with the rate con not marked sent** (0199620, 0199505),
and on 0199505 the carrier pay ran **$227.50 over max buy** with none of the customer's stated
requirements (fuel, GPS/SensiTech, straps, dock-high) verifiable in McLeod. This is the same
control gap first seen on order 0197341 — no longer a single observation.

**A cargo-security signal the desk had not surfaced.** On one cross-border order (0199276) a
carrier sales rep noticed the tractor was one colour at pickup and a different colour in the
inspection-station photos. Treated as a possible **wrong-truck or double-brokering** signal
pending verification against the rate con and the carrier's insurance. Recording the *pattern*
here because it is the kind of thing the watcher is worth having for: nobody was looking at
pickup photos and inspection photos side by side.

**Rate-quote volume is the dominant inbound shape.** 22 rate-quote messages in ~50 minutes
(load boards, brokers, and the freight-forwarder account), plus 4 USPS auction notices. The
watcher deliberately did **not** propose a new handler agent — it proposed pointing the
existing `rate-quote` route at the **DGL Command Center's** mailbox-intake and
quote-recommendation pipeline, which already handles this shape. Reuse over build; matches
backlog item #3. Needs a real handoff point, not just a route edit. Nothing written to
`routes.json`.

**Do NOT add a blanket ignore rule for the company's own domain.** The cycle flagged 7
internal staff replies that matched no route, and an ignore rule was floated. Rejected:
internal-outbound is exactly where staff **commit money** — an earlier read of the same
mailbox caught two rate commitments to a customer ($2,350 and $2,650) that were internal
replies and would have been discarded by such a rule. If volume needs cutting, ignore named
automated senders, never the company domain. A route that *records* commitments is the right
answer.

**Operational note:** a connector named `mcleod` refused connection during the cycle; the
`dgl-mcp` tools covered McLeod and the cycle completed. `sources.json` names `dgl-mcp`, which
is correct — do not "fix" it to `mcleod`.

## Leakage number — path chosen: WHOLE-BOOK BULK EXPORT (premise now obsolete — PM to confirm)

PM chose the full 365-day, all-customers figure via bulk export (not a connector sample),
because the connector can't page the whole book. **That premise no longer holds** — since
2026-09-08 the connector pages and `mcleod_query` runs arbitrary aggregates, so the 365-day
extract can run through the connector without a dev running SQL on-network. Aggregating in SQL
beats paging 1000 rows at a time. The bulk-export path below still works and is unchanged;
switching is the PM's call, not an automatic consequence. Deliverable: `mcleod-extract/
mcleod_leakage_extract.sql` (now hardened with the connector-confirmed schema; only
`other_charge` codes + the carrier-charge table left to confirm via its discovery block) →
run on DB02 (fastest: the dev who built `dgl-mcp` already has the connection) → CSV →
`leakage_model.py --csv`. Real number NOT yet computed (awaiting the CSV).

Validation already seen on real order 0197341: actual pickup dwell 16h20m (detention that
caps at $150) vs the rep's hand-typed email times; `rate_confirmation_status`/`_sent_date`
NULL → the contract's "signed rate con returned in real time" gate isn't being recorded (a
control-gap / leakage risk in its own right).

## Pilot status — #1 Accessorial engine (BUILT, dry-run)

Encodes the **full signed carrier Rate Confirmation** (`reference-implementation/`):
- **Accessorials → carrier:** detention (2h free, $35/h solo · $50/h team, **caps at the
  layover rate**), layover ($150/$250 team), driver-assist (pre-approved only, never auto),
  TONU ($150/$250 team).
- **Deductions ← carrier (auto on trigger):** MacroPoint tracking failure & late-service &
  direct-run (greater of $500 or 20% linehaul), missed check-calls ($50 ea), late POD
  ($150) + continued ($250/day), missing signed rate con ($50), exclusive-use (100% rate).
- **Eligibility gates:** carrier-fault → rejected; missing signed facility proof / revised
  signed rate con → needs review; detention/layover/TONU **held until customer pays**.
- **Permission-gated override:** only MANAGER/ADMIN/SUPER_ADMIN may waive an auto-applied
  charge, with a required audit note; USER is refused.
- Pure dry-run, no side effects. **Tests: 20/20** (`test_accessorial_rules.py`).

## Customer markup + rate sheet (`customer-accessorial-rate-sheet.md`)

The Command Center already carries "company guidance" bill/pay pairs (real internal
precedent): **Detention 1.50× · Stopoff 1.48× · TONU 1.39× · Layover 1.33× · Lumper 0.97×
(a flagged LOSS)**. Recommendation: publish a standard sheet at **carrier cost +40%** with
per-type minimums; **fix lumper** (cost + handling, never below cost); split time-based
(percentage) from third-party fees (cost + fixed fee). Numbers are provisional pending
**McLeod AR actuals**.

## Leakage model (`reference-implementation/leakage_model.py`, `leakage-analysis.md`)

Answers "how much did Delta lose over 365 days to un-billed / un-enforced items." Three
buckets: **(1) customer under-billing, (2) carrier deduction under-enforcement, (3) carrier
overpayment (ineligible paid).** Reuses the engine + the customer rate sheet. `--csv` runs
a real McLeod export; unknown judgment fields default to compliant so the result is a
**defensible floor**. **Tests: 10/10.** No real figure computed yet (needs McLeod).

## McLeod extractors (`mcleod-extract/`)

Two read-only paths, both emit the identical CSV `leakage_model.py --csv` consumes:
- **SQL:** `mcleod_leakage_extract.sql` + `mcleod_extract.py` (pyodbc/pymssql; `--discover`
  then `--run`). Standard LME schema; every site-specific name marked `-- CONFIRM`.
- **API:** `mcleod_api_extract.py` (McLeod REST; reads `MCLEOD_API_BASE`/`MCLEOD_API_TOKEN`
  from env — never committed; `--probe` tests reach+auth, `--run` writes CSV).
- **Image type numbers:** temporary POD = **4** (confirmed). **Signed Rate Confirmation # =
  TODO** — the one value the rate-con-timing query needs; get it from the SharePoint
  Accounting-folder doc-type list or McLeod Image Setup.

## Where things live

- **PR #3** (draft): branch `claude/workflow-automation-identification-3shwij` on
  `j9reynolds/claude-code`. CI = Semgrep, green. Subscribed for events.
- **PR #7** (MERGED 2026-09-08, merge commit `6f00a76`): branch `claude/m365-bug-fix-pwtvt3`.
  Made the `mcleod-ops` Microsoft 365 mailbox adapter real — see the mailbox-access section
  above. Docs/config only, no executable code; `fetch_mail.py` untouched. CI = Semgrep, green.
- **PR #9** (MERGED 2026-09-08, merge commit `b46b28a`): the `hasAttachments` note above.
  Its description was amended after merge to correct an overstated rationale — the merged
  wording was accurate and unchanged; only the reasoning behind it was wrong.
- **Review artifact:** https://claude.ai/code/artifact/acd9c1c8-553c-4d80-8062-7667f1a63e38
  (note: artifact wake-subscriptions do NOT register in this session; re-read manually).
- File map: `README.md` (index), `discovery-findings.md` (7 patterns), `opportunity-backlog.md`
  (scored/ranked), `build-plan.md` (per-winner specs), `customer-accessorial-rate-sheet.md`,
  `leakage-analysis.md`, `employee-announcement.md`, `reference-implementation/`, `mcleod-extract/`.

## The ranked backlog (build order)

1. **Accessorial approval triage** — the pilot (engine built). 2. Accounting ACH/intercompany
(phase, finance sign-off). 3. Rate-quote productionization (Command Center ~80% done).
4. Load-status auto-relay (P44/MacroPoint). 5. Document auto-filing (quick win). 6. Recurring
report assembly (quick win). 7. CRM hygiene / customer-domain matching (enabler). **McLeod
read access is the keystone — it unblocks #1, #3, #4, #5, #6.**

## Open decisions / next steps (waiting on the user)

1. **Get McLeod data — no longer blocked on access.** The connector now pages and runs
   arbitrary read-only SQL, so the 365-day figure can be aggregated through `mcleod_query`
   instead of waiting on an on-network extractor + `loads_365d.csv`. Next concrete step: run
   the `other_charge` GROUP BY above, record the code list, then compute the real leakage
   number and finalize the rate sheet. The bulk-export path remains available if preferred.
2. **Provide the signed-Rate-Confirmation image type number** (counterpart to temp POD = 4).
3. **Provide the role/permission map** (who is MANAGER/ADMIN/SUPER_ADMIN) to wire the override.
4. **Go/no-go on the staff announcement** (`employee-announcement.md`) before anything live.
5. After #1: start quick wins #5/#6 and enabler #7.
6. **Rate-quote route — wire it to the Command Center pipeline** (the watcher's proposal, see
   the first-live-cycle section). Decide the handoff point; a route edit alone points at a
   pipeline that does not know it is being fed.
7. **Raise routes off `observe` one at a time** as each handler earns trust. All 8 are
   `observe` today; 6 of them ship at `draft`. The restore list is in the
   `_autonomy_override` comment at the top of the live `routes.json`.

## SQL conventions for McLeod (LME_1720) — ALWAYS follow

- **Fully-qualify every table** as `[lme_1720].[dbo].[<table>]` in every query (SSMS
  sessions are not defaulted to the lme_1720 DB context, so bare `dbo.orders` fails).
  Bracket each identifier. This applies to all McLeod SQL going forward, no exceptions.
- **Confirmed schema corrections (use these, not the connector's aliased names):**
  - The carrier on a movement is `movement.override_payee_id` → join `[lme_1720].[dbo].[payee]`
    on `payee.id = mv.override_payee_id`. (The connector surfaces this as `carrier_id`, but
    the real base column is `override_payee_id` — do NOT use `mv.carrier_id` in SQL.)
  - orders → movement: `mv.id = o.curr_movement_id`. orders → customer:
    `cust.id = o.customer_id`. stops: `s.movement_id = o.curr_movement_id`.
- The canonical, corrected Query A/B/C lives in `mcleod-extract/mcleod_leakage_extract.sql`.

## Working conventions

- Commit attribution currently: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
  + `Claude-Session:` trailer (per session directive — may change; follow the latest).
- All PRs draft; auto-watch after creating; keep CI green. Push only validated changes.
- Do not commit secrets/credentials or McLeod tokens. Keep committed docs process-level.
