"""DGL Command Center — sales digest renderer for the news aggregator.

Turns the aggregator's news.json snapshot into a compact "Market Watch"
HTML fragment for "The DGL Command Center - Account Health" emails (the
daily per-salesperson reports and the manager rollup) — and only those.
The Account Health job appends the fragment to the email body it already
builds, so news rides along with the report the sales team already reads
instead of adding another email to their inbox.

Only sales-relevant tags are included (configurable via the
"sales_digest" section of feeds.json): rate and capacity moves, tariffs
and cross-border changes, carrier failures and fraud, and disruptions —
the stories that change how a load gets quoted or covered today.

Usage:
    python sales_digest.py [--config feeds.json] [--input news.json]
                           [--output sales_digest.html] [--hours 24]

Prints nothing and writes an empty file when no qualifying items exist,
so the report job can append the fragment unconditionally.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_TAGS = ["rates-capacity", "ports-intl", "carriers", "disruption"]
DEFAULT_MAX_ITEMS = 8

TAG_LABELS = {
    "rates-capacity": "Rates & Capacity",
    "ports-intl": "Tariffs & Cross-Border",
    "carriers": "Carrier Watch",
    "disruption": "Disruptions",
    "regulatory": "Regulatory",
    "tech-tms": "Tech & TMS",
}


def select_items(
    items: list[dict],
    tags: list[str],
    hours: int,
    max_items: int,
    now: datetime,
) -> list[dict]:
    """Pick the newest sales-relevant items inside the lookback window."""
    cutoff = now - timedelta(hours=hours)
    selected = []
    for item in items:
        if not set(item.get("tags", [])) & set(tags):
            continue
        published = item.get("published_utc")
        if published:
            try:
                when = datetime.fromisoformat(published)
            except ValueError:
                continue
            if when < cutoff:
                continue
        else:
            continue  # undated items stay on the dashboard, not in email
        selected.append(item)
    selected.sort(key=lambda i: i["published_utc"], reverse=True)
    return selected[:max_items]


def render_html(items: list[dict], tags: list[str], now: datetime) -> str:
    """Render an email-safe fragment: tables and inline styles only."""
    if not items:
        return ""

    rows = []
    for item in items:
        label = next(
            (TAG_LABELS.get(t, t) for t in tags if t in item.get("tags", [])),
            "",
        )
        when = datetime.fromisoformat(item["published_utc"])
        age_hours = max(0, int((now - when).total_seconds() // 3600))
        age = f"{age_hours}h ago" if age_hours < 24 else f"{age_hours // 24}d ago"
        rows.append(
            '<tr>'
            '<td style="padding:4px 10px 4px 0;white-space:nowrap;color:#5b7794;'
            f'font-size:12px;vertical-align:top;">{html.escape(label)}</td>'
            '<td style="padding:4px 0;font-size:13px;">'
            f'<a href="{html.escape(item["link"], quote=True)}" '
            'style="color:#34557a;text-decoration:none;">'
            f'{html.escape(item["title"])}</a>'
            f' <span style="color:#6b839a;font-size:11px;">'
            f'&mdash; {html.escape(item["source"])}, {age}</span>'
            "</td></tr>"
        )

    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" '
        'style="margin-top:16px;border-top:2px solid #34557a;width:100%;'
        'font-family:Segoe UI,Arial,sans-serif;">'
        '<tr><td colspan="2" style="padding:8px 0 2px 0;font-size:13px;'
        'font-weight:bold;color:#24405e;">Market Watch &mdash; industry news '
        "for Sales</td></tr>"
        + "".join(rows)
        + "</table>"
    )


def run(config_path: Path, input_path: Path, output_path: Path, hours: int) -> int:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    digest_config = config.get("sales_digest", {})
    tags = digest_config.get("tags", DEFAULT_TAGS)
    max_items = digest_config.get("max_items", DEFAULT_MAX_ITEMS)

    if not input_path.exists():
        output_path.write_text("", encoding="utf-8")
        return 0

    snapshot = json.loads(input_path.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    items = select_items(snapshot.get("items", []), tags, hours, max_items, now)
    output_path.write_text(render_html(items, tags, now), encoding="utf-8")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="feeds.json", type=Path)
    parser.add_argument("--input", default="news.json", type=Path)
    parser.add_argument("--output", default="sales_digest.html", type=Path)
    parser.add_argument("--hours", default=24, type=int)
    args = parser.parse_args()
    return run(args.config, args.input, args.output, args.hours)


if __name__ == "__main__":
    sys.exit(main())
