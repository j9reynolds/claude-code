#!/usr/bin/env python3
"""Read the ops mailbox over IMAP using an app password from the environment.

Used when a source's `adapter` is "imap" — the path for connecting with an
app password instead of riding an already-connected MCP mail connector.

Credentials are read from environment variables named by the config
(`username_env` / `password_env`, default OPS_MAILBOX / OPS_MAIL_APP_PASSWORD).
The password is never echoed, never logged, and never written to the ledger.

  fetch_mail.py --test                      verify login, print nothing secret
  fetch_mail.py --since 2026-08-27T00:00:00Z --limit 25

Output is a JSON object on stdout: {"count": n, "messages": [...]}, each
message carrying the stable `id` the watcher claims in the ledger.
"""

from __future__ import annotations

import argparse
import email
import email.utils
import imaplib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header

DEFAULT_HOST = "outlook.office365.com"
DEFAULT_PORT = 993


def decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def parse_since(raw: str | None) -> datetime:
    """Accept an ISO timestamp, a plain date, or 'Nh'/'Nd' shorthand."""
    now = datetime.now(timezone.utc)
    if not raw:
        return now - timedelta(hours=24)
    text = raw.strip()
    if text.lower().endswith("h") and text[:-1].isdigit():
        return now - timedelta(hours=int(text[:-1]))
    if text.lower().endswith("d") and text[:-1].isdigit():
        return now - timedelta(days=int(text[:-1]))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise SystemExit(f"could not parse --since value: {raw!r}")
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def credentials(args: argparse.Namespace) -> tuple[str, str]:
    user = os.environ.get(args.username_env, "").strip()
    password = os.environ.get(args.password_env, "")
    missing = [name for name, val in ((args.username_env, user), (args.password_env, password)) if not val]
    if missing:
        raise SystemExit(
            "missing environment variable(s): "
            + ", ".join(missing)
            + " — set them before running (see the plugin README for loading the app "
              "password from Windows Credential Manager)"
        )
    return user, password


def connect(args: argparse.Namespace) -> imaplib.IMAP4_SSL:
    user, password = credentials(args)
    try:
        conn = imaplib.IMAP4_SSL(args.host, args.port)
    except OSError as exc:
        raise SystemExit(f"could not reach {args.host}:{args.port} — {exc}")
    try:
        conn.login(user, password)
    except imaplib.IMAP4.error as exc:
        # str(exc) is the server's rejection text; it does not contain the password.
        raise SystemExit(
            f"IMAP login failed for {user}: {exc}\n"
            "If this is a Microsoft 365 mailbox, confirm IMAP is enabled for it and that "
            "the tenant still permits app-password (basic) auth for IMAP — Microsoft has "
            "disabled it broadly in Exchange Online."
        )
    return conn


def fetch(args: argparse.Namespace) -> dict:
    since = parse_since(args.since)
    conn = connect(args)
    try:
        status, _ = conn.select(args.folder, readonly=True)
        if status != "OK":
            raise SystemExit(f"could not open folder {args.folder!r}")

        # IMAP SINCE has date granularity; widen by a day and filter precisely below.
        criterion = (since - timedelta(days=1)).strftime("%d-%b-%Y")
        status, data = conn.search(None, "SINCE", criterion)
        if status != "OK":
            raise SystemExit("IMAP search failed")

        uids = data[0].split()
        messages = []
        for uid in reversed(uids):  # newest first, so --limit keeps the most recent
            if len(messages) >= args.limit:
                break
            status, payload = conn.fetch(uid, "(RFC822.HEADER)")
            if status != "OK" or not payload or not isinstance(payload[0], tuple):
                continue
            msg = email.message_from_bytes(payload[0][1])

            received = email.utils.parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else None
            if received and not received.tzinfo:
                received = received.replace(tzinfo=timezone.utc)
            if received and received < since:
                continue

            message_id = (msg.get("Message-ID") or f"uid:{uid.decode()}").strip("<> ")
            messages.append(
                {
                    "id": f"email:{message_id}",
                    "uid": uid.decode(),
                    "from": decode(msg.get("From")),
                    "to": decode(msg.get("To")),
                    "cc": decode(msg.get("Cc")),
                    "subject": decode(msg.get("Subject")),
                    "date": received.isoformat() if received else None,
                    "in_reply_to": (msg.get("In-Reply-To") or "").strip("<> ") or None,
                }
            )
        messages.sort(key=lambda m: m.get("date") or "")
        return {"count": len(messages), "since": since.isoformat(), "folder": args.folder, "messages": messages}
    finally:
        try:
            conn.close()
        except Exception:
            pass
        conn.logout()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=os.environ.get("OPS_MAIL_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.environ.get("OPS_MAIL_PORT", DEFAULT_PORT)))
    parser.add_argument("--folder", default="INBOX")
    parser.add_argument("--since", help="ISO timestamp, date, or shorthand like 6h / 2d (default: 24h)")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--username-env", dest="username_env", default="OPS_MAILBOX")
    parser.add_argument("--password-env", dest="password_env", default="OPS_MAIL_APP_PASSWORD")
    parser.add_argument("--test", action="store_true", help="verify login and exit")
    args = parser.parse_args()

    if args.test:
        conn = connect(args)
        conn.logout()
        json.dump({"ok": True, "host": args.host, "mailbox": os.environ.get(args.username_env)}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    json.dump(fetch(args), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
