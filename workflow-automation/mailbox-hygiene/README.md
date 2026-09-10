# Mailbox hygiene — Exchange Online duplicate removal

`Remove-DuplicateMessages.ps1` finds mail that exists more than once in an Exchange Online
mailbox, keeps exactly one copy of each, and reports, quarantines, or deletes the rest.

**It changes nothing until you tell it to.** The default `-Action Report` writes a CSV and
exits. That is deliberate: every destructive mode is opt-in, and the safest destructive mode
(`Quarantine`) only *moves* mail into a folder you can inspect and undo by hand.

| File | Purpose |
|------|---------|
| `Remove-DuplicateMessages.ps1` | The script. Report / Quarantine / Delete / Purge. |
| `test_Remove-DuplicateMessages.ps1` | 19 unit tests over the pure logic (key derivation, subject normalization, and the keep/remove plan). No mailbox required. |

---

## Why Graph and not Exchange Online PowerShell

`Search-Mailbox` was retired, and Compliance Search + `New-ComplianceSearchAction -Purge` is
all-or-nothing over a query — it cannot keep one copy and drop the rest. Deduplication needs
per-message control, which means Microsoft Graph.

Graph is also the only path that works on this tenant at all: basic auth is off, so IMAP with
an app password is refused however correct the credential is (`NO AUTHENTICATE failed`), and a
locked-down or cloud host only has HTTPS/443 outbound anyway. See the mailbox-access section
of `../CLAUDE.md` for the full history of that constraint.

---

## What counts as a duplicate

**`-DuplicateKey MessageId` (default)** groups on `internetMessageId` — the RFC822 Message-ID
with the angle brackets stripped and lowercased.

This is the right identity and the choice matters. Graph's own per-message `id` is *mailbox-
and folder-scoped*: the same mail re-keys the instant an Outlook rule files it, so an `id`-keyed
pass sees N distinct messages where there is really one. That is not hypothetical here — two
live pages of the ops mailbox held 25 rows / 19 distinct Message-IDs and 25 rows / 20 distinct,
i.e. 6 and 5 folder-copies of the same mail.

**`-DuplicateKey Content`** groups on a SHA256 of sender + normalized subject (Re:/Fw: stripped)
+ `receivedDateTime` to the second + `bodyPreview`. Use it for copies that were *re-sent* rather
than copied, so each carries its own distinct Message-ID. It is stricter than it looks: a
one-second difference in receive time is not a match.

A message with no usable key is never grouped and never removed.

---

## Scope — read this before using `PerMailbox`

- **`-Scope PerFolder` (default)** — only copies sitting in the *same* folder are duplicates.
  This removes true repeats and never collapses a copy someone filed on purpose.
- **`-Scope PerMailbox`** — every copy across all included folders is one group with a single
  survivor. This is what you want for "the same mail is in six places", but it **will** collapse
  deliberate filed copies. Run `-Action Report` first and read the CSV.

Excluded by default: Drafts, Outbox, Sent Items, Deleted Items, Junk Email, Conversation
History, Sync Issues, and the quarantine folder. Sent Items shares Message-IDs with the Inbox
copies of your own mail by design, so including it invites false positives. Drafts are skipped
outright.

---

## Step by step

### 1. One-time setup (per machine)

```powershell
Install-Module Microsoft.Graph.Authentication -Scope CurrentUser
```

PowerShell 7 is recommended; 5.1 works. The script only needs the auth module — it calls Graph
through `Invoke-MgGraphRequest`, so it does not drag in the full SDK or break when SDK cmdlet
signatures change between versions.

### 2. Confirm the logic before you point it at real mail

```powershell
cd workflow-automation\mailbox-hygiene
pwsh -NoProfile -File .\test_Remove-DuplicateMessages.ps1
```

Expect `19 passed, 0 failed`. No mailbox, no network, no auth.

### 3. Report — always do this first

```powershell
.\Remove-DuplicateMessages.ps1 -Mailbox ops@yourdomain.com -Since 2026-01-01
```

You will be prompted to sign in and consent to `Mail.ReadWrite` (plus `Mail.ReadWrite.Shared`
when `-Mailbox` names someone else's mailbox). The run prints per-folder progress and finishes
with a count, then writes `duplicates-<mailbox>-<timestamp>.csv` next to the script.

**Bound the run with `-Since`.** It maps to a `receivedDateTime` filter and is the cheapest way
to keep the first pass small and the blast radius understood.

### 4. Read the CSV

One row per message, with `Disposition` = `Keep` or `Remove`, plus `GroupKey`, `Folder`,
`Subject`, `From`, `Received`, `InternetMessageId`, `HasAttachments`.

Check three things:
1. Every `GroupKey` has exactly one `Keep`.
2. The `Remove` rows are genuinely the same mail as their `Keep` row — not a reply, not a
   forward with new content.
3. Nothing you meant to keep in a second folder is on the `Remove` list. If it is, you want
   `-Scope PerFolder` (the default) rather than `PerMailbox`.

Sort by `GroupKey` in Excel and the groups read top to bottom.

### 5. Quarantine — the reversible removal

```powershell
.\Remove-DuplicateMessages.ps1 -Mailbox ops@yourdomain.com -Since 2026-01-01 -Action Quarantine
```

Moves every `Remove` copy into a folder called `Duplicates - Quarantine` (created if missing,
rename it with `-QuarantineFolder`). Nothing is deleted; the mailbox looks clean and the copies
are one drag away from being restored. Live with it for a few days.

Add `-WhatIf` to print what would move without moving anything.

### 6. Delete, once you trust the match

```powershell
.\Remove-DuplicateMessages.ps1 -Mailbox ops@yourdomain.com -Since 2026-01-01 -Action Delete
```

Graph delete — the copies land in **Deleted Items** and the user can restore them.

`-Action Purge` goes one step further, deleting them out of Deleted Items into **Recoverable
Items**. It requires `-Force`. Note what that is and is not: still recoverable by the user
(Recover Deleted Items) or an admin for the retention window. Neither mode is an unrecoverable
wipe, and the script does not offer one.

---

## Options

| Parameter | Default | Notes |
|---|---|---|
| `-Mailbox` | signed-in user | Needs `Mail.ReadWrite.Shared` **and** Full Access on the target. |
| `-Since` / `-Before` | unbounded | `receivedDateTime` window. Always set `-Since` on a first run. |
| `-DuplicateKey` | `MessageId` | Or `Content`. |
| `-Scope` | `PerFolder` | Or `PerMailbox`. |
| `-Action` | `Report` | `Quarantine`, `Delete`, `Purge` (needs `-Force`). |
| `-Keep` | `Oldest` | Or `Newest`. Ties break on message id, so the survivor is stable across runs. |
| `-QuarantineFolder` | `Duplicates - Quarantine` | Created on demand. |
| `-IncludeFolders` / `-ExcludeFolders` | see above | Display names. |
| `-MaxMessages` | `50000` | Run-wide cap. If hit, the run **says so** and marks itself truncated. |
| `-ReportPath` | auto-named CSV | |
| `-WhatIf` | — | Standard PowerShell; prints the actions without performing them. |

---

## Things that will bite you

- **A permission failure is not an empty mailbox.** Missing Full Access returns an error, not
  zero results. Never read a failure as a quiet zero.
- **Truncation is loud, not silent.** Hitting `-MaxMessages` sets a truncated flag, warns that
  duplicate groups may be incomplete, and stops scanning further folders — because a partial
  scan can show a group as having one copy when it has three.
- **Graph throttles.** 429/503/504 are retried with exponential backoff, honouring `Retry-After`
  when Graph sends one. A large mailbox will visibly pause; that is the script behaving.
- **Attachments do not make a copy special.** `HasAttachments` is carried into the CSV so you can
  eyeball it, but it does not affect grouping — two copies of the same Message-ID carry the same
  attachments. (Separately: that flag means "has at least one *non-inline* attachment", so a
  `false` never hides a rate con or POD.)
- **Run it against one mailbox at a time.** There is no `-All` switch by design.
