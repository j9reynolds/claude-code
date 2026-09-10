# EmailAgent mailbox ingestion pipeline — architecture recommendation

**Status:** proposed, not built. Supersedes the standalone duplicate-removal script
(`../mailbox-hygiene/`) for the `JR_Test@DeltaGroupLog.com` workflow.
**Date:** 2026-09-10

---

## 1. Requirement 1 — confirming what the current script does

Asked directly: **yes, the existing logic already keeps exactly one copy and removes every
other copy in the group.** That is not the gap.

`New-RemovalPlan` emits exactly one `Keep` row per duplicate group and a `Remove` row for
every other member, and the test suite asserts the invariant that no message is ever both
(`test_Remove-DuplicateMessages.ps1`, "no message is both kept and removed").

Three things about it *are* wrong for this workflow:

| | Current | Required |
|---|---|---|
| Survivor | `-Keep Oldest` **by default** (`Newest` exists and is tested, but is not the default) | most recent, always |
| Disposal | `Delete` → Deleted Items; `Purge` → Recoverable Items | permanently deleted |
| Shape | a standalone, human-run cleanup pass | one stage of an automated ingestion pipeline |

The third is the real problem, and it is not fixable by editing the deletion logic.

---

## 2. Core finding — this should not be a deduplication script

**Duplicate cleanup must not be a separate pass over the mailbox. It must be a selection step
inside the ingestion transaction.**

The moment there are two independent processes touching `JR_Test` — a cleanup job deleting
copies and EmailAgent ingesting what is left — Requirement 5's race conditions are not edge
cases, they are the normal operating mode:

- Cleanup deletes an older copy that EmailAgent has already read but not yet committed.
- EmailAgent ingests a copy that cleanup is concurrently deleting; the delete wins; the row
  points at a message that no longer exists.
- A second copy arrives *between* the cleanup pass and the ingestion pass and is ingested as
  a distinct email.
- Cleanup runs while EmailAgent is mid-batch and both pick a different "most recent" copy
  because a third copy landed in between.

No amount of locking between two jobs fixes this cleanly, because the shared state is a
mailbox — a system with no transactions, no compare-and-swap, and eventual consistency on
its own indexes.

**The fix is structural: one process owns the mailbox.** Deduplication becomes "which copy of
this group do I ingest" — a decision made in memory, at the moment of ingestion — and the
older copies are simply deleted alongside the canonical one after ingestion commits. They are
never ingested and never separately hunted.

That collapses four failure modes into zero and removes an entire moving part.

---

## 3. Hard constraints — what Exchange Online will and will not do

### "Permanently deleted" has a specific and limited meaning

Requirement 2 asks that duplicates not remain in Deleted Items, Recoverable Items, or Purges.
**The API cannot deliver the third one. Only mailbox configuration can, and even then not
instantly.**

Microsoft Graph's `permanentDelete` action (GA since April 2025) is the strongest deletion
primitive available, and its own documentation states it *"permanently deletes a message and
places it in the Purges folder."* There is no Graph call that erases an item outright.

What actually governs whether it stays there:

| Setting | Effect | Required value |
|---|---|---|
| `SingleItemRecoveryEnabled` | when `$true`, purged items are retained in `Recoverable Items\Purges` | `$false` |
| `RetainDeletedItemsFor` | dwell time in `Recoverable Items\Deletions` before the Managed Folder Assistant removes them | `0` (max 30 days) |
| `LitigationHoldEnabled` | any hold pins everything, overriding both of the above | `$false` |
| Purview retention policy covering the mailbox | same — a hold by another name | mailbox excluded |
| In-Place Hold / eDiscovery hold | same | none |

With SIR off, no holds, and `RetainDeletedItemsFor 0`, items are removed by the Managed Folder
Assistant, which runs roughly daily. **So the honest guarantee is: gone from the user-visible
mailbox immediately, beyond user recovery immediately, fully erased within ~24 hours.** An
architecture that claims instant erasure would be lying.

This is a configuration task, not a code task, and it must be done and *verified* before the
pipeline is pointed at a real mailbox.

### Records-retention flag (needs a human decision, not a technical one)

If a permanently deleted email is a rate confirmation, POD, or customer instruction, it is a
**business record**. Deleting it from Exchange means the Command Center database and its blob
store become the archive of record, and they inherit every retention and legal-hold obligation
the mailbox used to carry. That is a defensible design — it is how a queue mailbox is supposed
to work — but it must be a decision someone makes on purpose, in writing, before go-live.
It also means attachments must be captured during ingestion (see §6, step c); losing an
attachment is losing the contract.

---

## 4. Technology evaluation

| Technology | Verdict | Reasoning |
|---|---|---|
| **Exchange Online PowerShell** | **Not for runtime. Required for setup.** | `Search-Mailbox` is retired. The only bulk deletion path left is `New-ComplianceSearch` + `New-ComplianceSearchAction -Purge`, which is all-or-nothing over a query and **caps at 10 items per mailbox per search** — unusable for per-message work. It remains the *only* way to configure SIR, retention, holds, and application access policy, so it is essential, just not in the hot path. |
| **Microsoft Graph REST API (direct HTTP)** | **Recommended engine.** | Per-message control, `permanentDelete`, `$batch` (20 ops/request), `$select` to keep payloads small, app-only auth, and mailbox-scoped access policy. Highest throughput, lowest overhead per message, no deprecation horizon. |
| **Microsoft Graph PowerShell SDK** | **Ops tooling only.** | A wrapper over the same REST surface, so it inherits the capability but adds cmdlet marshalling overhead, version churn, and a runtime with poor async and concurrency. Right for the read-only audit report and one-off admin tasks. Wrong for a continuously running queue processor. |
| **EWS** | **Excluded — effectively dead.** | Microsoft begins **blocking EWS from non-Microsoft apps on 1 October 2026** unless the tenant configures an AppID allow-list with `EWSEnabled=$true`, with permanent removal on **1 April 2027**. That is three weeks and seven months away respectively. EWS genuinely had the better primitives here — `DeleteItem` with `HardDelete`, 100-item batches, streaming notifications — and none of that is worth building on a platform with a hard stop. |
| **Hybrid** | **This is the recommendation.** | EXO PowerShell for one-time configuration and compliance verification; Graph REST for the runtime pipeline; Graph PowerShell SDK for audit reporting. Each tool used where it is actually the best one. |

### Runtime host: .NET 8 worker service, on the Command Center host

Not PowerShell. The decisive argument is operational, not linguistic: **Delta already runs
exactly this shape of thing.** `DGL-McLeodMcp` is a .NET Windows service on that box, with
`appsettings.json`, a CI pipeline, and an `update-mcp.ps1` deploy script — and the team has
already paid the tuition on its failure modes (the service-account outage documented in
`../CLAUDE.md`). A second service following the same pattern inherits that knowledge, that
deployment path, and that monitoring.

A PowerShell scheduled task would also need to solve leasing, retry with backoff, structured
logging, connection pooling, and graceful shutdown — all of which the .NET worker/hosted-service
model gives for free.

### Polling, not webhooks — for v1

Graph change notifications would cut latency to seconds, but cost a public HTTPS endpoint,
subscription renewal every ~3 days, validation-token handling, and a reconciliation poller
anyway (notifications are best-effort and can be missed). A queue mailbox tolerates a minute.

**Recommendation: poll every 30–60s, and revisit webhooks only if measured latency becomes a
business problem.** Note also that delta query is the *wrong* tool here: delta shines for a
folder you read repeatedly and never empty. This folder empties itself, so a plain filtered
enumeration is simpler and cheaper.

---

## 5. Idempotency model

Three mechanisms, layered. The first is the one that actually matters.

**1. A database uniqueness constraint on `InternetMessageId` is the guarantee.**
Not application logic, not a "have I seen this?" check — those are races waiting to happen.
`UNIQUE (InternetMessageId)` on the ingestion table means a double-ingest is rejected by the
database engine under concurrency, at any interleaving. Everything else is optimization.

`internetMessageId` is the right key for the same reason the watcher ledger uses it: it is the
RFC822 Message-ID, stable across folder moves, whereas Graph's per-message `id` is mailbox- and
folder-scoped and re-keys the moment anything moves the item.

**2. Ingest-then-delete ordering, never the reverse.**
The database commit is the point of no return. Delete only after it. This makes the pipeline
**at-least-once**, and the failure mode is deliberately chosen: if the delete fails after a
successful commit, the next cycle sees the message again, the unique constraint rejects
re-ingestion, and the delete is retried. A duplicate *delete attempt* is harmless. A lost
email is not. Never invert this to save a round trip.

**3. A durable ledger that outlives the message.**
Ledger rows are kept with `Status = Deleted` for a retention window (90 days suggested), so a
re-delivered copy of an already-processed Message-ID is recognised and dropped rather than
re-ingested. Without this, deleting the mailbox copy also deletes the memory of it.

**Single-writer lease.** Exactly one worker instance owns the mailbox at a time, enforced by a
lease row (or `sp_getapplock`) in SQL with a timeout. This is cheap and removes a whole class
of problem. The throughput ceiling here is Graph throttling per mailbox, not compute, so there
is nothing to gain from parallel workers on one mailbox.

**Stability window.** Only consider messages with `receivedDateTime < now − 2 minutes`. This
prevents acting on a duplicate group that is still arriving (distribution list plus direct
send land seconds apart) and sidesteps Graph's index eventual-consistency.

---

## 6. The transactional unit

There is no distributed transaction between Exchange Online and SQL Server, and no way to
create one. The design therefore makes **the SQL commit the only atomic step** and treats
everything on the Exchange side as a retryable effect of it.

Per cycle:

```
0.  acquire mailbox lease (single writer)          -- else exit quietly
1.  enumerate Inbox, receivedDateTime < now-2min,
    $select=id,internetMessageId,receivedDateTime,subject,from,hasAttachments
    $orderby=receivedDateTime asc, paged
2.  group in memory by internetMessageId
    (fallback: content hash, for resends with distinct Message-IDs)
3.  for each group:
      canonical := max(receivedDateTime)            -- most recent wins
      a. ledger upsert -> Claimed  (UNIQUE on InternetMessageId)
         if already Ingested or Deleted -> skip to (e)   -- already handled
      b. fetch canonical body + attachments
      c. BEGIN TX
           insert into Command Center ingestion table
           ledger.Status := Ingested, IngestedAtUtc := now
         COMMIT                                     -- point of no return
      d. verify: re-read the committed row by InternetMessageId
      e. permanentDelete EVERY message id in the group
         (canonical + all older copies) via $batch, 20 per request
      f. ledger.Status := Deleted, DeletedAtUtc := now
4.  repeat until no eligible messages
5.  release lease
```

Note what step (e) does: the older duplicates are deleted **here**, as part of the same unit,
having never been ingested and never touched by a separate job. That is the whole redesign.

Step (d) is Requirement 3's "successful ingestion confirmation from EmailAgent" made concrete —
deletion is gated on a positive read-back of committed state, not on the absence of an
exception.

---

## 7. State model

```sql
CREATE TABLE dbo.EmailIngestLedger (
    InternetMessageId  NVARCHAR(400)  NOT NULL PRIMARY KEY,   -- brackets stripped, lowercased
    ContentHash        CHAR(64)       NULL,                   -- SHA256 fallback key
    GraphMessageId     NVARCHAR(512)  NULL,                   -- canonical copy, folder-scoped
    DuplicateCount     INT            NOT NULL DEFAULT 1,
    ReceivedUtc        DATETIME2(3)   NOT NULL,
    Status             VARCHAR(16)    NOT NULL,   -- Claimed|Ingested|Deleted|Failed|Quarantined
    ClaimedBy          NVARCHAR(128)  NULL,
    ClaimedAtUtc       DATETIME2(3)   NULL,
    IngestedAtUtc      DATETIME2(3)   NULL,
    DeletedAtUtc       DATETIME2(3)   NULL,
    AttemptCount       INT            NOT NULL DEFAULT 0,
    LastError          NVARCHAR(2000) NULL,
    CommandCenterId    BIGINT         NULL                    -- FK to the ingested record
);
CREATE INDEX IX_Ledger_Status_Received ON dbo.EmailIngestLedger (Status, ReceivedUtc);
CREATE INDEX IX_Ledger_ContentHash     ON dbo.EmailIngestLedger (ContentHash) WHERE ContentHash IS NOT NULL;
```

`Status` is a state machine with exactly one legal path forward and no way back to `Claimed`:

```
Claimed -> Ingested -> Deleted          (happy path)
Claimed -> Failed -> Quarantined        (poison message)
Claimed -> (lease expiry) -> Claimed    (crash recovery; safe because ingest is idempotent)
```

---

## 8. Failure handling and recovery

| Failure | Behaviour |
|---|---|
| Crash between (a) and (c) | Row stays `Claimed` with a stale lease. Reclaimed after the lease timeout and reprocessed; the unique constraint makes that safe. |
| Crash between (c) and (e) | Row is `Ingested`, message still in mailbox. Next cycle sees it, skips ingestion at (a), proceeds straight to delete. **This is the designed path, not an error.** |
| Ingestion throws (parse, schema, bad attachment) | `AttemptCount++`, `Status = Failed`, exponential backoff. |
| `AttemptCount` exceeds threshold (suggest 5) | `Status = Quarantined`; **move the message to a `_Quarantine` folder — never delete it** — and alert. Directly serves "no accidental deletion of emails not yet ingested". |
| Graph 429 / 503 | Honour `Retry-After`, exponential backoff, and stop the cycle rather than hammering; the next cycle resumes. |
| Delete fails after commit | Logged at warning, retried next cycle. Not an incident. |
| SQL unavailable | Cycle aborts before any deletion. Nothing is ever deleted while the database is unreachable. |
| Mailbox unreachable / auth failure | Cycle aborts. Alert if it persists past N cycles. |

**Recovery drills worth rehearsing before go-live:** kill the worker mid-batch and confirm no
double-ingest; revoke the app's access mid-cycle and confirm nothing is deleted; point it at a
mailbox with a hold and confirm it detects and refuses.

---

## 9. Logging and audit

- **Structured logs**, one event per stage transition, correlated by `InternetMessageId`.
- **No PII in logs.** Message-ID, internal ids, counts, timings, error classes — never subject,
  body, sender, or recipient. The program guardrail already requires this, and a log that
  survives the email it describes is an unmanaged copy of the record.
- **Audit table** `EmailIngestAudit` recording every permanent deletion: Message-ID, group
  size, canonical id, actor (app id), timestamp. This is the only durable evidence that a
  deletion was authorised and preceded by an ingestion — worth having if anyone ever asks
  where an email went.
- **Daily audit report**: processed, deduped (groups collapsed, copies removed), quarantined,
  failed, mean latency, plus a reconciliation count of mailbox items remaining. The existing
  monthly-report pattern in `../reporting/` is the model.
- **Alert on:** quarantine depth > 0, cycle failures > 3 consecutive, mailbox item count
  trending up (the queue is not draining), `Recoverable Items` non-empty (retention config has
  drifted).

---

## 10. Security

1. **Entra app registration with `Mail.ReadWrite` (Application), scoped by
   `New-ApplicationAccessPolicy` to `JR_Test@DeltaGroupLog.com` alone.** This is the single
   most important control in the design. An unscoped `Mail.ReadWrite` application permission
   grants read/write to **every mailbox in the tenant**, and this app's whole purpose is
   permanent deletion. Scope it, then verify with `Test-ApplicationAccessPolicy`.
2. **Certificate authentication, not a client secret.** Cert in the Windows certificate store
   under the service account, or Azure Key Vault. No credential in `appsettings.json`.
3. **Least-privilege database access** — `EXECUTE` on the ingestion stored procedures only; no
   `db_datareader` over the whole Command Center database.
4. **Service account** following the `DGL-McLeodMcp` precedent: `DOMAIN\user` form, documented,
   with the credential lifecycle owned by a named person.
5. **Attachment handling** — size cap, content-type allow-list, and antivirus scan before the
   file is written anywhere. The mailbox is an untrusted input; anything arriving from outside
   Delta is hostile until proven otherwise.
6. **A kill switch** — a config flag that stops deletion (ingest and quarantine only) without
   redeploying, so a suspected fault can be contained in seconds.

---

## 11. Mailbox configuration (one-time, EXO PowerShell, before go-live)

```powershell
Connect-ExchangeOnline

# Required for Requirement 2 - without these, "permanent" deletion lands in Purges and stays.
Set-Mailbox JR_Test@DeltaGroupLog.com -SingleItemRecoveryEnabled $false
Set-Mailbox JR_Test@DeltaGroupLog.com -RetainDeletedItemsFor 0
Set-Mailbox JR_Test@DeltaGroupLog.com -LitigationHoldEnabled $false

# VERIFY, do not assume. Any hold overrides everything above.
Get-Mailbox JR_Test@DeltaGroupLog.com |
  Format-List *Hold*, *SingleItem*, *RetainDeleted*, *Retention*, ArchiveStatus
Get-RetentionCompliancePolicy | Where-Object { $_.Enabled } |
  Format-List Name, ExchangeLocation

# The queue must not be pre-sorted out from under the pipeline.
Get-InboxRule -Mailbox JR_Test@DeltaGroupLog.com     # expect none

# Confirm the dumpster is actually empty once running.
Get-MailboxFolderStatistics JR_Test@DeltaGroupLog.com -FolderScope RecoverableItems |
  Format-Table Name, ItemsInFolder

# Scope the app to this mailbox ONLY.
New-ApplicationAccessPolicy -AppId <app-id> `
  -PolicyScopeGroupId JR_Test@DeltaGroupLog.com -AccessRight RestrictAccess `
  -Description "EmailAgent ingestion pipeline - JR_Test queue only"
Test-ApplicationAccessPolicy -Identity JR_Test@DeltaGroupLog.com -AppId <app-id>
```

Also: **disable the archive mailbox** on `JR_Test`. Auto-expanding archive will move items out
from under the pipeline, and a queue mailbox has no use for one.

---

## 12. Rollout

The program's existing doctrine — dry-run, then human-approved, then automatic — applies, and
matters more here than anywhere else in the program, because this is the first component whose
mistakes are unrecoverable.

| Phase | Deletion behaviour | Exit criteria |
|---|---|---|
| 1. Shadow | none; ledger written, nothing ingested or deleted | duplicate detection matches manual inspection over a full week |
| 2. Ingest-only | none; messages ingested and left in place | zero double-ingests; Command Center records verified correct |
| 3. Soft delete | move to `_Processed` folder instead of deleting | one week with no message needed back; `_Processed` reviewed and empty of surprises |
| 4. Permanent | `permanentDelete` | steady state |

Phase 3 is the important one and the cheapest insurance in the plan: it is a full dress
rehearsal of the real pipeline where every mistake is reversible with a drag of the mouse.
Do not skip it.

---

## 13. Open decisions

1. **Does EmailAgent already exist, and what is its interface?** The design above assumes the
   pipeline *is* the ingester. If EmailAgent is an existing component that reads the mailbox
   itself, this becomes an integration problem with a different answer, and two processes on
   one mailbox is exactly the shape §2 warns about.
2. **Runtime host** — .NET 8 worker service on the Command Center host is the recommendation.
3. **Re-delivered Message-ID** — if the same Message-ID arrives again weeks later, is that a
   duplicate to drop, or a resend to re-ingest? Default proposed: drop.
4. **Records retention sign-off** — see §3.
5. **Target schema** in the Command Center database for ingested email.
