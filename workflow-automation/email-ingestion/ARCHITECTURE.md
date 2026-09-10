# JR_Test mailbox ingestion — architecture review

**Status:** revised 2026-09-10 after reading `j9reynolds/dgl-command-center` and
`j9reynolds/email-agent`. The first draft of this document recommended building a new
.NET ingestion service. **That recommendation is withdrawn** — it was written before I
had access to the existing pipeline, and it would have duplicated working production
code. What follows replaces it.

---

## 1. The correction

The pipeline this request describes **already exists and mostly works.** `DGL_Command_Center`
carries a complete Graph-based intake chain, and `Remove-IngestedJrTestMail.ps1` already
performs batched `permanentDelete` against `JR_Test@DeltaGroupLog.com`.

More importantly, the existing deletion tool made a **better safety decision than the one I
proposed**. Its candidate list comes from the database — `intake.Request` rows — never from a
mailbox listing:

> *a message not yet ingested is untouchable by construction*

That is a stronger guarantee than my proposed read-then-verify-then-delete loop, because it
removes the failure mode rather than checking for it. A mailbox-listing-driven tool (which is
what `../mailbox-hygiene/Remove-DuplicateMessages.ps1` is) can always be wrong about whether
something was ingested. A DB-driven one cannot.

**Recommendation: keep the existing architecture. Fix the one thing that is actually broken.**

---

## 2. Root cause of the duplicates

**`internetMessageId` is never captured anywhere in the intake pipeline.**

Every dedupe key in both databases is the Microsoft Graph per-message `id`:

| Object | Unique key |
|---|---|
| `intake.MailScanStaging` | `UX_MailScanStaging_Msg (MessageId, MailboxAddress)` |
| `intake.Request` | `UX_Request_Msg (MessageId, MailboxAddress)` |
| `audit.EmailAudit` (older EmailAgent DB) | `UX_EmailAudit_GraphMsg (GraphMessageId, MailboxAddress)` |

The Graph `id` is **mailbox- and folder-scoped**. Two copies of one email — the same mail
delivered twice, or one copy filed into a second folder — carry two different Graph ids, so
every one of these constraints sees two distinct messages. Exact-duplicate identity is
invisible to the pipeline by construction.

The enumerator confirms it. `etl/Backfill-MailScan.ps1:158`:

```
/mailFolders/Inbox/messages?$select=id,conversationId,subject,from,receivedDateTime,bodyPreview
```

No `internetMessageId`. A repo-wide search for the term returns nothing.

`intake.vw_MailScanSurvivor` does dedupe, but at **conversation** granularity, and only over
unprocessed rows. It collapses a thread; it does not recognise two copies of one message.
When `ConversationId` differs or is NULL (which happened live on 2026-09-01), even that
degrades to the sender + normalised-subject fallback.

So: **duplicates are not a mailbox hygiene problem. They are a missing column.** A cleanup
script that deletes copies out of the mailbox treats the symptom and leaves the duplicate
*records* in the database, which is where they actually cause harm.

---

## 3. Requirements against what exists

| Req | Status | Notes |
|---|---|---|
| 1. Keep exactly one copy | **Partly satisfied** | `vw_MailScanSurvivor` keeps rank 1 per conversation. Exact duplicates are not detected at all (§2). |
| 1. Retain the **most recent** | **Already correct** | `ORDER BY ReceivedDateTimeUtc DESC, MailScanStagingId DESC` — newest already wins. No change needed. |
| 2. Permanent deletion | **Mechanism exists, blocked on a grant** | `Remove-IngestedJrTestMail.ps1` already calls `POST /messages/{id}/permanentDelete` in `$batch` of 20. It refuses to run because the app holds `Mail.Read` only (as of 2026-08-25). |
| 2. Not in Deleted Items | **Satisfied** | `permanentDelete` bypasses it; `-SoftDelete` is the opt-in halfway step. |
| 2. Not in Recoverable Items / Purges | **Not achievable by code** | See §4. |
| 3. Mailbox as queue, not archive | **Design already agreed** | The 2026-08-25 doc proposes an Exchange retention policy on JR_Test as the sustainable fix. That is the right answer and needs no code. |
| 4. Architecture review | See §5 | |
| 5. Idempotency | **Largely satisfied** | See §6. |
| 6. Production design | Existing pipeline + §7 changes | |

---

## 4. "Not in Purges" is a configuration outcome, not an API one

Graph's `permanentDelete` (GA April 2025) is the strongest primitive available, and its own
documentation states it *"permanently deletes a message and places it in the Purges folder."*
The existing script's header says the same thing. **No API call erases an item outright.**

What governs whether it stays there is mailbox configuration:

| Setting | Required value |
|---|---|
| `SingleItemRecoveryEnabled` | `$false` |
| `RetainDeletedItemsFor` | `0` (max 30 days) |
| `LitigationHoldEnabled` | `$false` |
| Purview retention policy covering the mailbox | excluded |

With those set and no hold, items are removed by the Managed Folder Assistant, which runs
roughly daily. **The honest guarantee is: gone from the mailbox immediately, beyond user
recovery immediately, fully erased within ~24 hours.** Anything promising instant erasure in
Exchange Online is wrong.

Note the tension with the quota problem that motivated the original script: Recoverable Items
has its own quota, so purge retention has to be short for deletion to actually reclaim space.

---

## 5. Technology evaluation (as requested)

| Technology | Verdict |
|---|---|
| **Exchange Online PowerShell** | **Setup and retention only.** `Search-Mailbox` is retired; the remaining bulk path, `New-ComplianceSearchAction -Purge`, is all-or-nothing over a query and caps at 10 items per mailbox per search. Unusable for per-message work — but it is the only way to set §4's retention config and the `ApplicationAccessPolicy`. |
| **Microsoft Graph REST** | **Correct engine, already in use.** `permanentDelete`, `$batch` (20/request), `$select`, client-credential app auth. The existing scripts already do this well, including token refresh at 45 minutes and 404-as-already-gone. |
| **Graph PowerShell SDK** | **Not needed.** The existing code calls Graph over raw `Invoke-RestMethod`, which avoids SDK version churn entirely. Adopting the SDK now would be churn for its own sake. |
| **EWS** | **Excluded.** Microsoft begins blocking EWS for non-Microsoft apps on **1 October 2026** — three weeks away — with permanent removal on **1 April 2027**. |
| **Hybrid** | **This is what exists**, and it is right: EXO PowerShell for tenant configuration, Graph REST for runtime. |

### On the runtime host

My earlier recommendation of a new .NET 8 worker service is **withdrawn**. The existing
PowerShell + Windows Task Scheduler arrangement is deliberate and well-reasoned — capture was
moved onto Task Scheduler precisely because the Claude desktop scheduler dropped 23 days of
ingest silently, and the split between capture (must never miss) and classification (a missed
run only creates a backlog) is a sound piece of design. Rewriting that in .NET would risk a
regression in the one stage where a miss means permanent data loss, to gain nothing the
current design lacks.

---

## 6. Idempotency and races, assessed against the real pipeline

The existing design already has the properties Requirement 5 asks for, and gets them the same
way I would have: **the database is the source of truth, and deletion is driven from it.**

| Concern | How the existing design handles it |
|---|---|
| Double ingestion | Unique indexes on `(MessageId, MailboxAddress)` at both staging and Request; `IF NOT EXISTS` guards; `usp_RegisterEmail` returns `DUPLICATE`. Sound — but keyed on the wrong identity (§2), so two *copies* both pass. |
| Reprocessing handled mail | `ProcessedAt` watermark plus resumable cursor. |
| Deleting un-ingested mail | **Structurally impossible** — no `intake.Request` row, no candidacy. The strongest control in the system. |
| Race between cleanup and ingestion | Cleanup reads a committed DB watermark, so it can only ever lag ingestion, never lead it. My "two processes on one mailbox" warning does not apply here: only one process *writes* the mailbox, and it acts solely on what the other has already committed. |

**The one gap:** because duplicate copies are not recognised as duplicates, each copy gets its
own `Request` row (a non-survivor becomes `RequestTypeCode='Other'` via the bulk pass). The
mailbox is cleaned up correctly; the *database* keeps the duplicate records. Fixing §2 fixes
this at the source.

---

## 7. Recommended changes

Five items. Three are code, two are IT actions, and the IT actions are on the critical path.

**C1 — capture the identity (`etl/Backfill-MailScan.ps1:158`)**
Add `internetMessageId` to the `$select` and persist it into staging. One token in the URL
plus the staging insert. This is the whole root-cause fix.

**C2 — schema migration (`sql/45_schema_v39_internet_message_id.sql`)**
Add `InternetMessageId NVARCHAR(300) NULL` to `intake.MailScanStaging` and `intake.Request`,
with a non-unique index. Deliberately **case-insensitive collation** — RFC 5322 Message-IDs
are not Graph ids, and the `email-agent` repo already documents this exact distinction. Do
**not** add a unique constraint in this migration: existing rows are all NULL and existing
duplicates would fail it.

**C3 — exact-duplicate dedupe ahead of conversation dedupe**
Collapse on `InternetMessageId` first, newest wins (matching the existing
`ORDER BY ReceivedDateTimeUtc DESC` convention), then let the existing conversation dedupe run
on the survivors. Once a full retention window has populated the column, add the filtered
unique index `WHERE InternetMessageId IS NOT NULL`.

**IT-1 — grant `Mail.ReadWrite` (Application) with `ApplicationAccessPolicy`**
Blocks Requirement 2 entirely; the script refuses to run without it. The request text is
already written in `docs/2026-08-25-jrtest-backfill-and-cleanup.md`, including the two gotchas
found live: `PolicyScopeGroupId` needs a mail-enabled security group, and the real proof of
scoping is a `Denied` on a non-scoped mailbox, not a `Granted` on the target.
**First action: confirm whether this was granted in the two weeks since.**

**IT-2 — retention configuration**
§4's settings for the Purges requirement, plus the 30-day JR_Test retention policy the
2026-08-25 doc already recommends as the sustainable "not an archive" fix.

### What to do with `mailbox-hygiene/Remove-DuplicateMessages.ps1`

**Do not use it on JR_Test.** It reads candidates from a mailbox listing, which is the
opposite of the DB-driven direction the existing tooling correctly chose, and it would delete
copies whose duplicate records remain in the database. It stays useful as an ad-hoc tool for a
human's own mailbox; it has no role in this pipeline.

---

## 7a. Implementation status

C1-C3 are implemented in **[dgl-command-center#69](https://github.com/j9reynolds/dgl-command-center/pull/69)**
(draft, branch `claude/internet-message-id-dedupe`):

- `sql/45_schema_v39_internet_message_id.sql` - the column, the exact-duplicate collapse
  ahead of conversation dedup, and `intake.vw_MailScanDuplicate` for measuring the rate.
- `etl/Backfill-MailScan.ps1` - `internetMessageId` in the `$select`, persisted, with a
  column probe so capture keeps running on a box behind on migrations.
- `tests/BackfillMailScan.InternetMessageId.Tests.ps1` - 26 assertions, database-free.
- `analysis/jrtest_duplicate_rate_2026-09-10.sql` - read-only; answers open item 2 below
  and measures the real duplicate rate before anything is deleted.

Deliberately excluded: `intake.Request.InternetMessageId` (nothing would populate it yet)
and any unique constraint on the new column (existing rows are NULL and existing duplicates
would fail it). IT-1 and IT-2 remain open and are not code.

## 8. Rollout

1. Confirm the `Mail.ReadWrite` grant (IT-1) — everything else is theatre without it.
2. Apply C1 + C2 and let capture run one full cycle. `InternetMessageId` populates going
   forward; historical rows stay NULL, which the filtered index tolerates.
3. Report only: how many staging rows share an `InternetMessageId`? That number is the real
   duplicate rate, measured rather than assumed, and it decides whether C3 is urgent.
4. Apply C3, verify the survivor count drops by roughly that number.
5. Apply IT-2, then run `Remove-IngestedJrTestMail.ps1 -Execute`.
6. Verify `Get-MailboxFolderStatistics -FolderScope RecoverableItems` drains within ~24h.

---

## 9. Open items

1. **Has `Mail.ReadWrite` been granted since 2026-08-25?** Cannot be checked from here.
2. **Two pipelines, one mailbox.** The older `EmailAgent` database (`audit` schema) and the
   newer `DGL_Command_Center.intake` pipeline both target JR_Test. Is the older one still
   live? If so, that genuinely *is* two processes on one mailbox, and §2's fix is needed in
   both. If it is retired, say so and the question closes.
3. **Records retention.** If a permanently deleted email is a rate confirmation or POD, the
   database becomes the archive of record and inherits the retention obligation. A decision
   for a person, not a script.
