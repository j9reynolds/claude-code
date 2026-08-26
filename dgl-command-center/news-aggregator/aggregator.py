"""DGL Command Center — logistics news aggregator.

Polls the RSS/Atom feeds listed in feeds.json, normalizes and de-duplicates
the items, applies keyword tags, and writes the result to a rolling JSON
snapshot (news.json). When a SQL Server connection string is configured it
also upserts into dbo.NewsItem on DGLIQ so the portal can serve the feed.

Standard library only, except pyodbc which is imported lazily and required
only when the SQL sink is enabled. Designed to run as a Windows scheduled
task on DGLCC-WRK01 under the svc-dglcc-etl$ gMSA.

Usage:
    python aggregator.py [--config feeds.json] [--output news.json]
                         [--max-age-days 7] [--dry-run]
"""

from __future__ import annotations

import argparse
import email.utils
import hashlib
import html
import json
import logging
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

USER_AGENT = "DGLCC-NewsAggregator/1.0 (+https://portal.deltagrouplog.com)"
FETCH_TIMEOUT_SECONDS = 30
SUMMARY_MAX_CHARS = 500

log = logging.getLogger("news-aggregator")

ATOM_NS = "{http://www.w3.org/2005/Atom}"
TAG_STRIP_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def clean_text(value: str | None) -> str:
    """Strip markup and collapse whitespace from a feed field."""
    if not value:
        return ""
    text = TAG_STRIP_RE.sub(" ", value)
    text = html.unescape(text)
    return WS_RE.sub(" ", text).strip()


def parse_date(value: str | None) -> datetime | None:
    """Parse RFC 822 (RSS) or ISO 8601 (Atom) dates to aware UTC datetimes."""
    if not value:
        return None
    value = value.strip()
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def item_id(source: str, guid: str) -> str:
    return hashlib.sha256(f"{source}|{guid}".encode("utf-8")).hexdigest()[:32]


def apply_tags(text: str, tag_rules: dict[str, list[str]]) -> list[str]:
    """Return the tag names whose keyword lists match the given text."""
    lowered = text.lower()
    tags = []
    for tag, keywords in tag_rules.items():
        if any(kw.lower() in lowered for kw in keywords):
            tags.append(tag)
    return sorted(tags)


def parse_feed(xml_bytes: bytes, source: str, tag_rules: dict[str, list[str]]) -> list[dict]:
    """Parse one RSS 2.0 or Atom document into normalized item dicts."""
    root = ET.fromstring(xml_bytes)
    items: list[dict] = []

    if root.tag == f"{ATOM_NS}feed":
        for entry in root.iter(f"{ATOM_NS}entry"):
            title = clean_text(entry.findtext(f"{ATOM_NS}title"))
            link = ""
            for link_el in entry.iter(f"{ATOM_NS}link"):
                if link_el.get("rel") in (None, "alternate"):
                    link = link_el.get("href", "")
                    break
            guid = entry.findtext(f"{ATOM_NS}id") or link or title
            summary = clean_text(
                entry.findtext(f"{ATOM_NS}summary") or entry.findtext(f"{ATOM_NS}content")
            )
            published = parse_date(
                entry.findtext(f"{ATOM_NS}published") or entry.findtext(f"{ATOM_NS}updated")
            )
            items.append(_build_item(source, guid, title, link, summary, published, tag_rules))
    else:
        for item in root.iter("item"):
            title = clean_text(item.findtext("title"))
            link = (item.findtext("link") or "").strip()
            guid = (item.findtext("guid") or "").strip() or link or title
            summary = clean_text(item.findtext("description"))
            published = parse_date(item.findtext("pubDate"))
            items.append(_build_item(source, guid, title, link, summary, published, tag_rules))

    return [i for i in items if i["title"] and i["link"]]


def _build_item(source, guid, title, link, summary, published, tag_rules) -> dict:
    return {
        "id": item_id(source, guid),
        "source": source,
        "title": title,
        "link": link,
        "summary": summary[:SUMMARY_MAX_CHARS],
        "published_utc": published.isoformat() if published else None,
        "tags": apply_tags(f"{title} {summary}", tag_rules),
    }


def fetch_feed(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
        return response.read()


def load_snapshot(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {item["id"]: item for item in data.get("items", [])}
    except (json.JSONDecodeError, KeyError):
        log.warning("Existing snapshot %s is unreadable; starting fresh", path)
        return {}


def prune(items: dict[str, dict], max_age_days: int, now: datetime) -> dict[str, dict]:
    cutoff = now - timedelta(days=max_age_days)
    kept = {}
    for key, item in items.items():
        published = parse_date(item.get("published_utc"))
        if published is None or published >= cutoff:
            kept[key] = item
    return kept


def upsert_sql(items: list[dict], connection_string: str) -> int:
    """MERGE items into dbo.NewsItem on DGLIQ. Requires pyodbc."""
    import pyodbc  # deferred: only needed when the SQL sink is configured

    merge_sql = """
MERGE dbo.NewsItem AS target
USING (SELECT ? AS ItemId) AS src ON target.ItemId = src.ItemId
WHEN NOT MATCHED THEN
  INSERT (ItemId, Source, Title, Link, Summary, PublishedUtc, Tags, IngestedUtc)
  VALUES (?, ?, ?, ?, ?, ?, ?, SYSUTCDATETIME());
"""
    inserted = 0
    with pyodbc.connect(connection_string) as conn:
        cursor = conn.cursor()
        for item in items:
            cursor.execute(
                merge_sql,
                item["id"], item["id"], item["source"], item["title"], item["link"],
                item["summary"], item["published_utc"], ",".join(item["tags"]),
            )
            inserted += cursor.rowcount if cursor.rowcount > 0 else 0
        conn.commit()
    return inserted


def run(config_path: Path, output_path: Path, max_age_days: int, dry_run: bool) -> int:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    tag_rules = config.get("tags", {})
    now = datetime.now(timezone.utc)

    known = load_snapshot(output_path)
    new_items: list[dict] = []
    failures = 0

    for feed in config["feeds"]:
        if not feed.get("enabled", True):
            continue
        name, url = feed["name"], feed["url"]
        try:
            xml_bytes = fetch_feed(url)
            parsed = parse_feed(xml_bytes, name, tag_rules)
        except (urllib.error.URLError, ET.ParseError, TimeoutError, OSError) as exc:
            failures += 1
            log.error("Feed %s failed: %s", name, exc)
            continue
        fresh = [item for item in parsed if item["id"] not in known]
        for item in fresh:
            known[item["id"]] = item
        new_items.extend(fresh)
        log.info("Feed %s: %d items, %d new", name, len(parsed), len(fresh))

    known = prune(known, max_age_days, now)
    ordered = sorted(
        known.values(),
        key=lambda i: i.get("published_utc") or "",
        reverse=True,
    )

    if dry_run:
        log.info("Dry run: %d new items, %d total after prune", len(new_items), len(ordered))
    else:
        output_path.write_text(
            json.dumps(
                {"generated_utc": now.isoformat(), "items": ordered},
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        connection_string = config.get("sql_connection_string")
        if connection_string and new_items:
            count = upsert_sql(new_items, connection_string)
            log.info("SQL sink: %d rows inserted", count)

    # Non-zero exit only when every feed failed, so one flaky outlet does not
    # page anyone but a dead egress path does.
    enabled = [f for f in config["feeds"] if f.get("enabled", True)]
    return 1 if enabled and failures == len(enabled) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="feeds.json", type=Path)
    parser.add_argument("--output", default="news.json", type=Path)
    parser.add_argument("--max-age-days", default=7, type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )
    return run(args.config, args.output, args.max_age_days, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
