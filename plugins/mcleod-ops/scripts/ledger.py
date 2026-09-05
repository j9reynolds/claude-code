#!/usr/bin/env python3
"""Append-only ledger for McLeod/email activity events.

The watcher uses this to answer two questions it cannot answer from the
source systems themselves:

  1. "Have I already dispatched an agent for this event?"  -> `claim`
  2. "Where did I stop reading this source last time?"     -> `cursor`

Storage is a JSONL file plus a cursor file under the state directory
(default `$CLAUDE_PROJECT_DIR/.claude/mcleod-ops/`, override with
`MCLEOD_OPS_STATE_DIR`). Records are appended, never rewritten; the
current view of an event is the fold of its records in file order. That
keeps a crashed or killed watcher from corrupting history, and leaves an
audit trail of what was dispatched and why.

All commands print a single JSON object on stdout.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

TERMINAL_STATUSES = {"done", "failed", "skipped", "escalated"}
STATUSES = {"claimed", "dispatched", "done", "failed", "skipped", "escalated"}


def state_dir() -> Path:
    override = os.environ.get("MCLEOD_OPS_STATE_DIR")
    if override:
        root = Path(override)
    else:
        project = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        root = Path(project) / ".claude" / "mcleod-ops"
    root.mkdir(parents=True, exist_ok=True)
    return root


def events_path() -> Path:
    return state_dir() / "events.jsonl"


def cursors_path() -> Path:
    return state_dir() / "cursors.json"


@contextmanager
def locked():
    """Serialize readers and writers across concurrently running agents."""
    lock = state_dir() / ".lock"
    with open(lock, "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def append(record: dict) -> None:
    with open(events_path(), "a") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def fold() -> dict[str, dict]:
    """Replay the log into the current state of every event."""
    events: dict[str, dict] = {}
    path = events_path()
    if not path.exists():
        return events
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # a torn final write; the next append repairs the file
            event_id = record.get("id")
            if not event_id:
                continue
            current = events.setdefault(event_id, {"id": event_id, "history": []})
            for key, value in record.items():
                if key in ("history", "op"):
                    continue
                # first_seen is set once at claim; a retry must not reset the clock
                # that tells an operator how long this event has been open.
                if key == "first_seen" and current.get("first_seen"):
                    continue
                if value is not None:
                    current[key] = value
            current["history"].append(
                {"op": record.get("op"), "at": record.get("at"), "status": record.get("status")}
            )
    return events


def out(payload: dict, code: int = 0) -> int:
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return code


def cmd_claim(args: argparse.Namespace) -> int:
    """Reserve an event for dispatch. Idempotent: only the first caller wins."""
    with locked():
        existing = fold().get(args.id)
        if existing and not (args.retry and existing.get("status") == "failed"):
            return out({"claimed": False, "reason": "already-known", "event": existing})
        record = {
            "op": "claim",
            "id": args.id,
            "at": now(),
            "first_seen": now(),
            "status": "claimed",
            "source": args.source,
            "workflow": args.workflow,
            "summary": args.summary,
            "subject_ref": args.subject_ref,
            "autonomy": args.autonomy,
        }
        append(record)
        return out({"claimed": True, "event": record})


def cmd_update(args: argparse.Namespace) -> int:
    with locked():
        events = fold()
        if args.id not in events:
            return out({"ok": False, "reason": "unknown-event", "id": args.id}, code=1)
        record = {
            "op": "update",
            "id": args.id,
            "at": now(),
            "status": args.status,
            "agent": args.agent,
            "note": args.note,
            "result": args.result,
            "workflow": args.workflow,
        }
        append(record)
        return out({"ok": True, "event": fold()[args.id]})


def cmd_get(args: argparse.Namespace) -> int:
    with locked():
        event = fold().get(args.id)
    if not event:
        return out({"found": False, "id": args.id}, code=1)
    return out({"found": True, "event": event})


def cmd_list(args: argparse.Namespace) -> int:
    with locked():
        events = list(fold().values())
    if args.status:
        events = [e for e in events if e.get("status") == args.status]
    if args.open:
        events = [e for e in events if e.get("status") not in TERMINAL_STATUSES]
    if args.workflow:
        events = [e for e in events if e.get("workflow") == args.workflow]
    # Open work is triaged oldest-first (the stalest event is the problem);
    # everything else reads as a feed, newest-first.
    events.sort(key=lambda e: e.get("first_seen") or e.get("at", ""), reverse=not args.open)
    if args.limit:
        events = events[: args.limit]
    if not args.verbose:
        for event in events:
            event.pop("history", None)
    return out({"count": len(events), "events": events})


def cmd_stats(_: argparse.Namespace) -> int:
    with locked():
        events = list(fold().values())
    by_status: dict[str, int] = {}
    by_workflow: dict[str, int] = {}
    for event in events:
        by_status[event.get("status", "unknown")] = by_status.get(event.get("status", "unknown"), 0) + 1
        workflow = event.get("workflow", "unrouted")
        by_workflow[workflow] = by_workflow.get(workflow, 0) + 1
    return out(
        {
            "total": len(events),
            "by_status": by_status,
            "by_workflow": by_workflow,
            "open": sum(1 for e in events if e.get("status") not in TERMINAL_STATUSES),
            "state_dir": str(state_dir()),
        }
    )


def cmd_cursor(args: argparse.Namespace) -> int:
    with locked():
        path = cursors_path()
        cursors = json.loads(path.read_text()) if path.exists() else {}
        if args.value is None:
            return out({"source": args.source, "cursor": cursors.get(args.source)})
        cursors[args.source] = {"value": args.value, "at": now()}
        path.write_text(json.dumps(cursors, indent=2, sort_keys=True) + "\n")
        return out({"source": args.source, "cursor": cursors[args.source], "written": True})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    claim = sub.add_parser("claim", help="reserve an event for dispatch (idempotent)")
    claim.add_argument("--id", required=True, help="stable event id, e.g. email:<message-id> or mcleod:order:12345:status")
    claim.add_argument("--source", required=True)
    claim.add_argument("--workflow", required=True)
    claim.add_argument("--summary")
    claim.add_argument("--subject-ref", dest="subject_ref", help="order/movement/carrier id this event is about")
    claim.add_argument("--autonomy", choices=["observe", "draft", "act"], default="draft")
    claim.add_argument("--retry", action="store_true", help="allow re-claiming an event that previously failed")
    claim.set_defaults(func=cmd_claim)

    update = sub.add_parser("update", help="record progress or outcome for a claimed event")
    update.add_argument("--id", required=True)
    update.add_argument("--status", choices=sorted(STATUSES))
    update.add_argument("--agent")
    update.add_argument("--note")
    update.add_argument("--result")
    update.add_argument("--workflow")
    update.set_defaults(func=cmd_update)

    get = sub.add_parser("get", help="show one event")
    get.add_argument("--id", required=True)
    get.set_defaults(func=cmd_get)

    listing = sub.add_parser("list", help="list events")
    listing.add_argument("--status", choices=sorted(STATUSES))
    listing.add_argument("--open", action="store_true", help="only events not in a terminal status")
    listing.add_argument("--workflow")
    listing.add_argument("--limit", type=int, default=50)
    listing.add_argument("--verbose", action="store_true", help="include per-event history")
    listing.set_defaults(func=cmd_list)

    stats = sub.add_parser("stats", help="summary counts")
    stats.set_defaults(func=cmd_stats)

    cursor = sub.add_parser("cursor", help="get or set the high-water mark for a source")
    cursor.add_argument("--source", required=True)
    cursor.add_argument("--value", help="omit to read the current cursor")
    cursor.set_defaults(func=cmd_cursor)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
