---
description: Show the ops ledger — open events, recent dispatches, cursors, and unrouted patterns
argument-hint: Optional workflow name to filter by
---

# Ops Status

Report the current state of the ops ledger without running a watch cycle or dispatching anything.

Filter: $ARGUMENTS

## What to do

Run these directly — this is a read-only report, so do not launch agents:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py stats
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py list --open --limit 25
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py list --workflow unrouted --limit 25
```

If a filter was given, add `--workflow <filter>` to the listing.

Read the cursor for each source configured in the active `sources.json` (or the example, if that is all that exists):

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py cursor --source <source-id>
```

## Report

1. **Open events** — anything not in a terminal status, oldest first. Age matters here: an event claimed hours ago and never closed is the finding, so give each one's age.
2. **Recent throughput** — counts by workflow and by status from `stats`.
3. **Unrouted patterns** — grouped by shape, with counts, and how close each is to the `build_threshold` in `routes.json`.
4. **Cursors** — where each source will resume, and how far behind now that is.

If the ledger is empty, say so in a line and note that no cycle has run yet. Do not pad an empty ledger into a report.
