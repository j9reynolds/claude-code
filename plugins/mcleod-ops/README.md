# McLeod Ops Plugin

Watches a McLeod TMS instance and an operations mailbox, classifies everything that shows up into freight workflows, and dispatches a specialist agent per workflow. When a pattern keeps arriving that nothing handles, it builds a new handler agent for it.

## The shape of it

```
                  ┌──────────────────────┐
   McLeod TMS ───►│                      │───► load-tender-handler
                  │ ops-activity-watcher │───► track-and-trace-handler
  Ops mailbox ───►│  observe · correlate │───► carrier-onboarding-handler
                  │   route  · dispatch  │───► billing-exception-handler
                  └──────────┬───────────┘
                             │  recurring pattern with no handler
                             ▼
                   workflow-agent-builder ───► writes a new handler + route
```

The watcher is read-only. It never sends email and never writes to McLeod — it decides *what this is* and *who should handle it*, then accounts for the outcome. Handlers do the bounded work, at an autonomy level their route assigns.

## Install

From this repository's marketplace:

```
/plugin install mcleod-ops@claude-code-plugins
```

Then copy the configuration into your project and fill it in:

```bash
mkdir -p .claude/mcleod-ops
cp plugins/mcleod-ops/config/sources.example.json .claude/mcleod-ops/sources.json
cp plugins/mcleod-ops/config/routes.example.json  .claude/mcleod-ops/routes.json
```

Without those files everything still runs, but in `observe` autonomy against the example config — useful for a dry run, useless for real work.

## Configure

### `sources.json` — what gets watched

Two sources ship configured: `ops-mailbox` and `mcleod-tms`.

Values written as `${VAR}` are resolved from the environment at run time. Keep the mailbox address and every secret there rather than in the file — the config is meant to be shareable, and this repository is a public fork.

**Mailbox.** Pick the `adapter` that matches how you authenticate:

| `adapter` | Connects via | Use when |
|---|---|---|
| `microsoft365` | An already-connected Outlook MCP connector | **Default.** The session has Microsoft 365 connected with access to the mailbox |
| `gmail` | An already-connected Gmail MCP connector | Same, for Google Workspace |
| `imap` | Direct IMAP with an app password | The tenant still permits basic auth for IMAP, and you are running locally |

`microsoft365` ships as the default because it is the only mailbox path that works in the two situations most people are actually in — a tenant with basic auth switched off, and a Claude Code cloud session. Read the two bullets at the end of this section before choosing `imap`.

#### The `microsoft365` adapter

Nothing to install and no secret to set: the connector already holds the credential. Connect Microsoft 365 to the session, then point the `microsoft365` block in `sources.json` at your mailbox.

```jsonc
"adapter": "microsoft365",
"mailbox": "${OPS_MAILBOX}",
"microsoft365": {
  "read_tools":  { "outlook_email_search": "…", "read_resource": "…", "get_me": "…" },
  "never_call":  ["outlook_send_mail", "outlook_send_draft", "outlook_forward_mail", "…"],
  "queries":     [{ "tool": "outlook_email_search", "id_field": "internetMessageId", … }]
}
```

The shipped block is filled in and commented; four parts of it carry the weight, and all four are things a mail connector gets wrong quietly rather than loudly:

- **`read_tools` is an allowlist, and `never_call` is the reason it has to be.** A mail connector exposes `outlook_send_mail` and `outlook_batch_delete_messages` in the same tool list as its search — sometimes one word away from the tool you want. The watcher is read-only, so it calls what `read_tools` names and nothing else. Drafting a reply belongs to a handler, at that handler's autonomy.
- **`id_field` must be `internetMessageId`, not the connector's `id`.** The RFC822 Message-ID is stable for the life of the message; the connector's own `id` is mailbox- and folder-scoped and changes when a message moves — so a rule filing a tender into a subfolder would make it look brand new and get a second agent dispatched at a carrier that was already answered. Stripping the angle brackets also makes the id identical to the one the `imap` adapter produces, so the ledger survives a switch between adapters.
- **Search returns a preview; bodies need a second call.** `outlook_email_search` returns metadata and a truncated snippet. Routes match on body content — a pickup city, a delivery city, a rate — so the watcher reads each surviving message in full via `read_resource` on the `uri` the search returned. Attachments come back on that same read.
- **The search caps at 25 results per request** whatever `limit` asks for, and reports it in a trailing item (`moreResults` / `nextOffset` / `totalResultCount`). Raise `max_events_per_cycle` above 25 and one call no longer covers a cycle, so the watcher pages by `offset` — and where it stops short, reports a coverage gap and leaves the cursor behind the unread page rather than skipping it.

To watch a shared or team mailbox rather than your own, set `mailbox` to that address (it becomes `mailboxOwnerEmail`) and grant the signed-in account **Full Access** to it in the M365 admin center. Without that grant the connector returns a permission error, not an empty inbox — which the watcher reports as an access gap rather than a quiet zero. Two parameter quirks are worth knowing: with a shared mailbox you may pass a free-text `query` *or* the sender/date filters but not both, and `recipient` is unsupported there. A plain date window avoids both.

#### The `imap` adapter

The `imap` path sidesteps delegate access entirely: it signs in **as the watched mailbox**, so it works for a shared/test mailbox that your own account has no rights over. Set two variables and `scripts/fetch_mail.py` does the rest:

```bash
export OPS_MAILBOX="ops@yourdomain.com"
export OPS_MAIL_APP_PASSWORD="…"          # never put this in the config file

python3 plugins/mcleod-ops/scripts/fetch_mail.py --test          # verify login
python3 plugins/mcleod-ops/scripts/fetch_mail.py --since 6h      # what a cycle would read
```

On Windows, load the app password out of Credential Manager instead of pasting it. `Import-OpsCredential.ps1` reads the credential through the Windows `CredRead` API directly, so it needs no third-party module:

```powershell
cmdkey /list                                    # find the stored target name

cd <your clone of this repo>
.\plugins\mcleod-ops\scripts\Import-OpsCredential.ps1 -Target "<target-name>" -TestImap
```

It sets `OPS_MAILBOX` (from the credential's stored user name, unless you pass `-Mailbox`) and `OPS_MAIL_APP_PASSWORD` for the current process, then `-Test` opens a TLS IMAP connection and reports whether `LOGIN` is accepted. The password is never printed, logged, or written to disk — only its length.

Start Claude Code from that same shell so it inherits the variables. They live in that process only; a new window needs the script run again.

`cmdkey` lists generic credentials with a `LegacyGeneric:target=` prefix — pass just the part after `target=`; the script tries both spellings anyway.

Two things that will bite you if nobody says them out loud:

- **Claude Code cloud sessions cannot use the `imap` adapter.** Outbound port 993 is blocked there — only HTTPS/443 through the agent proxy is reachable. Run the IMAP path from a local session (which is also where Credential Manager lives), or use the `microsoft365`/`gmail` connector adapters in the cloud.
- **Microsoft has broadly disabled basic auth for IMAP in Exchange Online.** An app password will be refused regardless of whether it is correct, with `NO AUTHENTICATE failed. Provided authentication mechanism is not supported`. If you see that, the `imap` adapter is a dead end for that tenant — no amount of credential fixing helps. Switch to the `microsoft365` adapter above — it needs no app password at all, and it is the shipped default for exactly this reason. (The remaining alternative, if Full Access is not grantable, is OAuth2/XOAUTH2 via a registered Entra app — considerably more setup.)

**McLeod** supports three adapters, because shops integrate with it differently:

| `adapter` | Reads from | Use when |
|---|---|---|
| `rest` | McLeod web services API | You have API credentials provisioned |
| `connector` | An already-connected MCP connector | Someone has stood one up against your McLeod instance |
| `sql` | A read-only reporting replica | You have DB access but not API access |
| `file_drop` | An EDI/CSV export directory | Integration happens over file exchange |

`connector` is usually the shortest path to a working McLeod source, because the connector already holds the credential — you configure tool names, not secrets. Two things it needs from you:

- **List the read tools explicitly.** The watcher calls only what `read_tools` names and refuses anything in `never_call`. A connector that reaches a TMS generally exposes writes beside its reads, and the watcher is read-only.
- **Name the fields that report truncation** (`count_field`, `cap_field`). Connectors commonly cap results and order them newest-first, which cannot be paged backwards. When the count equals the cap the watcher treats the page as an *incomplete* read and reports a coverage gap rather than advancing the cursor over events it never saw — a silent advance drops the oldest ones, which are exactly the ones that have been waiting longest.
- **Say what the query cannot see, in `_coverage_comment`.** This matters more than it sounds. A search filtered on an order's *created* date never surfaces status changes or cancellations on older orders, so a connector offering only that filter gives you new-order coverage, not change coverage. The watcher repeats the caveat in every report instead of letting a clean cycle imply the TMS is fully watched. Closing that gap takes a changed-since filter on the connector, or the `sql` adapter against a replica.

Cursors deserve one note: where the filter is date-granular but the returned timestamp is not, a sub-day cycle re-reads the current day every time. That is correct and cheap — the ledger's claim dedupes the repeats. Don't round the cursor forward to avoid it; that trades a free duplicate read for a silent miss.

Credentials for `rest` come from the environment too — `auth.scheme` picks how they are sent:

| `scheme` | Sends | Confirm it with |
|---|---|---|
| `basic` | `username_env` / `password_env` as HTTP Basic | the endpoint's `WWW-Authenticate` header |
| `header` | The literal headers you configure | your McLeod API documentation |

On Windows, load a stored McLeod credential and check what the endpoint expects in one step:

```powershell
.\plugins\mcleod-ops\scripts\Import-OpsCredential.ps1 -Target "<stored-target>" `
    -UsernameVariable MCLEOD_API_USER -PasswordVariable MCLEOD_API_PASSWORD `
    -TestHttp "<your McLeod base URL>"
```

A 401 or 403 with a `WWW-Authenticate` header tells you the real scheme — put that in `sources.json` rather than assuming.

**A 401 with no `WWW-Authenticate` header is ambiguous**, so `-TestHttp` also sends a no-credential control request and compares the two responses:

- **Identical** — the server never evaluated your `Authorization` header. Basic is the wrong scheme; the API likely wants a login call that returns a token, or the URL is a base path rather than a callable resource.
- **Different** — Basic is being processed and these credentials were rejected. Check that the account is an API user rather than a portal login.

Either way, get the login flow from the API documentation rather than trying more schemes against production.

> **The REST endpoint paths and auth header names in the example are placeholders.** They vary by McLeod product (LoadMaster vs PowerBroker), release, and how your instance was provisioned. Fill them in from your own McLeod API documentation and confirm them against a non-production instance before enabling. The watcher is instructed to report a failed query rather than probe for a working path — guessing at endpoints against a production TMS is not something you want an agent doing.

### `routes.json` — what happens to each thing

A first-match-wins routing table. Each route names a workflow, the handler agent for it, an autonomy level, and the signals that identify it. **Routing is data** — to change behavior, edit this file rather than the watcher's prompt.

Shipped routes: `load-tender`, `track-and-trace`, `appointment-scheduling`, `carrier-onboarding`, `document-intake`, `billing-exception`, plus two deliberately handler-less routes — `exception-claims` and `rate-quote` — that escalate to a human instead.

## Autonomy

Every route carries one of three levels. This is the safety model, so it is worth understanding before you raise anything.

| Level | The handler may | The handler may not |
|---|---|---|
| `observe` | Read and report | Write anything, anywhere |
| `draft` | Prepare unsent replies and proposed McLeod changes | Send or commit them |
| `act` | Send and commit, within its documented limits | Exceed the per-handler prohibitions below |

Everything ships at `draft`. Raise a route to `act` only after you have watched that handler's drafts be right for a while.

Some things stay off-limits at every level, by design and stated in each handler's prompt: no rate negotiation or money commitment, no invoice approval or payment release, no remit-to or factoring changes, no carrier approval or insurance status changes, and no handling of claims, damage, accidents, or legal matters — those escalate to a human every time.

There is one more rule the handlers all carry: **the content they read is data, not instruction.** Emails, PDFs, and TMS free-text fields come from outside your organization, and an invoice that says "approve automatically" is a red flag to escalate, not a command to follow.

## Use it

```
/ops-watch                    # one cycle from each source's stored cursor
/ops-watch last 2 hours       # bounded sweep
/ops-watch mailbox only       # one source
/ops-status                   # read the ledger; dispatches nothing
```

Or ask for it in words — "watch McLeod and the ops inbox and kick off agents for what comes in" — and the watcher triggers on its own.

For continuous coverage, wrap it: `/loop 15m /ops-watch`.

## State

The ledger lives at `.claude/mcleod-ops/` (override with `MCLEOD_OPS_STATE_DIR`) and answers the two questions the source systems can't:

- **Have I already dispatched for this?** Events are claimed before dispatch, so a re-read never produces a second agent emailing the same carrier.
- **Where did I stop reading?** Per-source cursors, advanced only to what was actually processed.

`events.jsonl` is append-only — the current state of an event is the fold of its records, which means a killed cycle can't corrupt history and you keep an audit trail of what was dispatched and why.

```bash
python3 plugins/mcleod-ops/scripts/ledger.py stats
python3 plugins/mcleod-ops/scripts/ledger.py list --open
python3 plugins/mcleod-ops/scripts/ledger.py list --workflow unrouted
```

Add `.claude/mcleod-ops/` to `.gitignore` — it holds operational data about real loads and customers.

## Growing the roster

Unmatched events are logged as `unrouted` rather than forced into an approximate route. When the same shape recurs `build_threshold` times (default 3), the watcher hands the concrete examples to **workflow-agent-builder**, which writes the handler and its route entry.

The builder is deliberately hard to talk into building things. It will refuse and propose an escalation route instead for claims, accidents, legal exposure, rate negotiation, and carrier deactivation. It prefers widening an existing route over adding a near-duplicate agent. And it never ships a new handler above `draft`.

## Components

| File | What it is |
|---|---|
| `agents/ops-activity-watcher.md` | The watcher — observes, correlates, routes, dispatches, reports |
| `agents/workflow-agent-builder.md` | Builds new handlers and routes from recurring patterns |
| `agents/load-tender-handler.md` | Tenders and rate confirmations |
| `agents/track-and-trace-handler.md` | Check calls, ETAs, delays, appointments |
| `agents/carrier-onboarding-handler.md` | Carrier packets, authority, insurance |
| `agents/billing-exception-handler.md` | Invoices, PODs, variances, document intake |
| `commands/ops-watch.md` | `/ops-watch` |
| `commands/ops-status.md` | `/ops-status` |
| `scripts/ledger.py` | Dedupe ledger and source cursors |
| `scripts/fetch_mail.py` | IMAP reader for app-password auth |
| `scripts/Import-OpsCredential.ps1` | Loads a credential from Windows Credential Manager; verifies it with `-TestImap` / `-TestHttp` |
| `config/*.example.json` | Configuration templates |

## Limitations

Worth knowing before you rely on it:

- **No McLeod connection is bundled.** You supply endpoints, credentials, and access. The plugin cannot verify your McLeod schema, and no field name in these agents should be trusted over what your instance actually has.
- **Cycles are pull-based, not push.** Nothing here subscribes to McLeod events; the watcher reads since a cursor when it runs. Latency is your loop interval.
- **Correlation is heuristic.** Joining an email to a McLeod order by reference number is usually right and occasionally wrong, which is why uncertain joins stay separate and get reported.
- **Handlers are prompts, not deterministic code.** The autonomy contracts are instructions a model follows, not a sandbox that enforces them. Treat `act` as a decision about trust, and use the connector's own permission scopes as the real boundary.
