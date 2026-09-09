---
description: Run a watch cycle over McLeod and the ops mailbox, then dispatch a handler agent per item
argument-hint: Optional window or scope, e.g. "last 2 hours" or "mailbox only"
---

# Ops Watch Cycle

Run one watch cycle across McLeod and the operations mailbox.

Scope for this cycle: $ARGUMENTS

## What to do

Launch the **ops-activity-watcher** agent with the Agent tool. Pass it:

- the scope above, if any — otherwise it runs from each source's stored cursor
- the instruction to run a full cycle: orient, observe, correlate, route, dispatch, grow the roster, report

The watcher dispatches handler agents itself. Do not dispatch handlers from here, and do not do the freight work yourself — this command's whole job is to start the cycle and relay the result.

## Before launching

Check that the active configuration exists:

- `.claude/mcleod-ops/sources.json`
- `.claude/mcleod-ops/routes.json`

If either is missing, tell the user the cycle will run in `observe` autonomy against the example config, name which file is missing, and point them at `${CLAUDE_PLUGIN_ROOT}/config/` to copy and fill in. Then run the cycle anyway — an observe-only cycle still shows them what is on the board.

## After the cycle

Relay the watcher's report to the user in full, leading with anything under **Needs you now**. If the watcher proposed a new workflow agent, surface that as a decision for the user rather than adopting it silently.
