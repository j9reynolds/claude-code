# Handoff — JR_Test mailbox ingestion, 2026-09-10

State at the end of the cloud session. Everything below is committed; nothing is in flight.
The remaining work needs the DGLIQ box and cannot be done from a cloud container.

---

## Shipped and merged

| PR | What |
|---|---|
| [dgl-command-center#69](https://github.com/j9reynolds/dgl-command-center/pull/69) | **schema v39** — `InternetMessageId` capture + exact-duplicate collapse. Merge commit `5f91f72`. |
| [claude-code#17](https://github.com/j9reynolds/claude-code/pull/17) | `mailbox-hygiene/` script + `email-ingestion/ARCHITECTURE.md`. Merge commit `ea0f392`. |
| [claude-code#18](https://github.com/j9reynolds/claude-code/pull/18) | Intake-queue warning on that script. Merge commit `59d2906`. |

**Merged is not deployed.** See the blocker below.

## The finding that drove all of it

`internetMessageId` was captured **nowhere** in the intake pipeline. Every dedupe key was the
Graph per-message id — `UX_MailScanStaging_Msg`, `UX_Request_Msg`, `UX_EmailAudit_GraphMsg` —
and that id is mailbox- and folder-scoped, so two copies of one email carry two ids and pass
every uniqueness guard. `vw_MailScanSurvivor` collapses *threads*, not copies.

So duplicates were never a mailbox-hygiene problem. They were a missing column. Deleting copies
out of the mailbox leaves the duplicate *records* in the database, which is where they matter.

Two decisions in v39 worth remembering:
- The column is **CI, not BIN2**. Graph ids are case-sensitive base64 (v26); RFC 5322 msg-id is
  case-insensitive, and BIN2 would split one Message-ID into several keys.
- The NULL fallback is **deliberate and is not the v34 bug**. A row with no Message-ID cannot be
  *proven* a duplicate, so it gets its own partition and survives. Historical rows staged before
  v39 are all NULL and are therefore never retroactively collapsed.

## BLOCKER — the box is out of sync with `master`

`D:\Project Folder\DGL_Command_Center` **is not a git repository**, though `CLAUDE.md:139` says
it is the local clone:

```
PS D:\Project Folder\DGL_Command_Center> git pull
fatal: not a git repository (or any of the parent directories): .git
PS> sqlcmd ... -i sql\45_schema_v39_internet_message_id.sql
Sqlcmd: 'sql\45_schema_v39_internet_message_id.sql': Invalid filename.
```

If that directory is what Task Scheduler runs `etl\Backfill-MailScan.ps1` from, then **no merge
has ever deployed itself** — the `$select` change is not on the box either. Diagnose before
repairing; the answer changes the fix.

```powershell
Test-Path "D:\Project Folder\DGL_Command_Center\.git"
Get-ChildItem "D:\Project Folder\DGL_Command_Center" -Force | Select-Object -First 20 Name
Get-ChildItem "D:\Project Folder\DGL_Command_Center\sql" -ErrorAction SilentlyContinue | Select-Object -Last 4 Name
```

| Result | Meaning |
|---|---|
| `sql\` has `44_schema_v38_*` but no `.git` | file-copy deployment, not a clone |
| no `sql\` at all | wrong machine or path; find the clone |
| `.git` present but pull still fails | `.git` is damaged |

Locate the real clone without a full-disk crawl:

```powershell
Get-ChildItem D:\ -Directory -Recurse -Depth 3 -Force -ErrorAction SilentlyContinue |
  Where-Object { Test-Path (Join-Path $_.FullName "sql\01_DGL_Command_Center_Schema_v1.sql") } |
  Select-Object -ExpandProperty FullName
```

## Remaining work, in order

1. **Repair the working copy** per the diagnosis above, so `etl/Backfill-MailScan.ps1` on the box
   is the merged version. Without this, step 2 is inert.
2. **Apply the migration.** Keep the `-I` — sqlcmd initialises `QUOTED_IDENTIFIER` OFF and this
   migration creates a filtered index. Idempotent; safe to re-run.
   ```powershell
   sqlcmd -S DGLIQ\DGLIQ -d DGL_Command_Center -I -i sql\45_schema_v39_internet_message_id.sql
   ```
   Expect three PRINTs ending `sql/45_schema_v39_internet_message_id.sql complete`.
3. **Run the tests.** `Invoke-Pester -Path tests\` — 26 assertions, database-free. They were
   verified through an equivalent plain-PowerShell harness because PSGallery is blocked from the
   cloud container; **they have never actually run under Pester.**
4. **Let capture run once**, then measure:
   ```
   analysis/jrtest_duplicate_rate_2026-09-10.sql
   ```
   Read-only. Gives the real duplicate rate and answers whether the older `EmailAgent` database
   is still writing to JR_Test alongside `intake` — it is ONLINE on the same instance, which is
   not the same as active. If both are live, that is two writers on one mailbox and v39 is needed
   in both schemas before deletion is turned up.
5. **Only then** consider the filtered unique index on `InternetMessageId`, and
   `intake.Request.InternetMessageId` (deliberately not added — nothing would populate it yet).

## Not code — still open

- **`Mail.ReadWrite` grant** blocks permanent deletion entirely; `Remove-IngestedJrTestMail.ps1`
  decodes the token and refuses without it. Request text is in
  `dgl-command-center/docs/2026-08-25-jrtest-backfill-and-cleanup.md`, including the two gotchas
  found live: `PolicyScopeGroupId` needs a mail-enabled security group, and the real proof of
  scoping is a `Denied` on a non-scoped mailbox, not a `Granted` on the target.
- **"Not in Purges" is configuration, not an API call.** Graph `permanentDelete` places items in
  Purges by design. Keeping it empty needs `SingleItemRecoveryEnabled $false`,
  `RetainDeletedItemsFor 0`, no holds, no Purview policy — then the Managed Folder Assistant
  sweeps roughly daily. Honest guarantee: gone from the mailbox instantly, beyond user recovery
  instantly, fully erased within ~24h.
- **Records retention.** If a permanently deleted email is a rate confirmation or POD, the
  database becomes the archive of record and inherits the obligation. A decision for a person.
- **ProjectMemory not updated** — the `dgl-mcp` connector is read-only, so this session could not
  write a `pm.Session` row the way dgl-command-center#66 did.
