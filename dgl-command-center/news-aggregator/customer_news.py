"""DGL Command Center — per-customer news crawler.

For each customer in customers.json, finds news about that company and
writes customer_news.json keyed by McLeod customer id, which the Account
Health email job renders into each account's call-prep block.

Two collection methods, tried in order per customer:

1. Domain feed discovery — probe the customer's own domain for an RSS or
   Atom feed (common paths, then a <link rel="alternate"> scan of the
   homepage). Press-release feeds are the highest-signal source when a
   customer publishes one.
2. Google News fallback — an RSS search query for the exact company name
   (news.google.com/rss/search). Covers the majority of customers whose
   sites have no feed. One extra egress host instead of hundreds.

Crawling is polite by design: robots.txt is honored per domain, at most
a handful of requests per customer per run, and the intended cadence is
one run per day ahead of the Account Health send.

Standard library only. Usage:
    python customer_news.py [--config customers.json]
                            [--output customer_news.json]
                            [--lookback-days 14]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import urllib.error
import urllib.parse
import urllib.robotparser
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

import aggregator

USER_AGENT = "DGLCC-CustomerNews/1.0 (+https://portal.deltagrouplog.com)"
FETCH_TIMEOUT_SECONDS = 20
FEED_PATHS = ["/feed", "/feed/", "/rss", "/rss.xml", "/news/rss.xml",
              "/blog/feed", "/press/feed", "/news/feed"]
FEED_MIME_HINTS = ("rss", "atom", "xml")

log = logging.getLogger("customer-news")


class FeedLinkParser(HTMLParser):
    """Collects <link rel="alternate" type="application/rss+xml|atom+xml"> hrefs."""

    def __init__(self):
        super().__init__()
        self.feed_urls: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        d = dict(attrs)
        rel = (d.get("rel") or "").lower()
        typ = (d.get("type") or "").lower()
        href = d.get("href")
        if href and "alternate" in rel and ("rss" in typ or "atom" in typ):
            self.feed_urls.append(href)


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
        return response.read()


def robots_allows(domain: str, path: str, cache: dict) -> bool:
    """Check robots.txt once per domain; unreachable robots.txt means allow."""
    if domain not in cache:
        parser = urllib.robotparser.RobotFileParser()
        try:
            parser.parse(fetch(f"https://{domain}/robots.txt").decode("utf-8", "replace").splitlines())
        except (urllib.error.URLError, TimeoutError, OSError):
            parser = None
        cache[domain] = parser
    parser = cache[domain]
    if parser is None:
        return True
    return parser.can_fetch(USER_AGENT, f"https://{domain}{path}")


def looks_like_feed(payload: bytes) -> bool:
    head = payload[:512].lstrip().lower()
    return head.startswith(b"<?xml") or b"<rss" in head or b"<feed" in head


def discover_domain_feed(domain: str, robots_cache: dict) -> bytes | None:
    """Return the first parseable feed found on the customer's domain."""
    for path in FEED_PATHS:
        if not robots_allows(domain, path, robots_cache):
            continue
        try:
            payload = fetch(f"https://{domain}{path}")
        except (urllib.error.URLError, TimeoutError, OSError):
            continue
        if looks_like_feed(payload):
            return payload
    # homepage <link rel="alternate"> discovery
    if not robots_allows(domain, "/", robots_cache):
        return None
    try:
        homepage = fetch(f"https://{domain}/")
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    parser = FeedLinkParser()
    try:
        parser.feed(homepage.decode("utf-8", "replace"))
    except Exception:
        return None
    for href in parser.feed_urls[:3]:
        feed_url = urllib.parse.urljoin(f"https://{domain}/", href)
        try:
            payload = fetch(feed_url)
        except (urllib.error.URLError, TimeoutError, OSError):
            continue
        if looks_like_feed(payload):
            return payload
    return None


def google_news_feed(company_name: str) -> bytes | None:
    query = urllib.parse.quote(f'"{company_name}"')
    url = (f"https://news.google.com/rss/search?q={query}"
           "&hl=en-US&gl=US&ceid=US:en")
    try:
        payload = fetch(url)
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    return payload if looks_like_feed(payload) else None


def recent_items(xml_bytes: bytes, source: str, lookback_days: int,
                 max_items: int, now: datetime) -> list[dict]:
    try:
        items = aggregator.parse_feed(xml_bytes, source, {})
    except ET.ParseError:
        return []
    cutoff = now - timedelta(days=lookback_days)
    kept = []
    for item in items:
        published = item.get("published_utc")
        if not published:
            continue
        try:
            when = datetime.fromisoformat(published)
        except ValueError:
            continue
        if when >= cutoff:
            kept.append({
                "title": item["title"],
                "link": item["link"],
                "published_utc": published,
                "source": source,
            })
    kept.sort(key=lambda i: i["published_utc"], reverse=True)
    return kept[:max_items]


def collect_customer(customer: dict, options: dict, robots_cache: dict,
                     now: datetime) -> dict:
    name = customer["name"]
    domain = (customer.get("domain") or "").strip().lower().removeprefix("www.")
    lookback = options.get("lookback_days", 14)
    max_items = options.get("max_items_per_customer", 3)

    method, items = None, []
    if domain:
        payload = discover_domain_feed(domain, robots_cache)
        if payload is not None:
            items = recent_items(payload, domain, lookback, max_items, now)
            if items:
                method = "domain-feed"
    if not items and options.get("google_news_fallback", True):
        payload = google_news_feed(name)
        if payload is not None:
            items = recent_items(payload, "Google News", lookback, max_items, now)
            if items:
                method = "google-news"

    return {"name": name, "domain": domain or None, "method": method, "items": items}


def run(config_path: Path, output_path: Path, lookback_days: int | None) -> int:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    options = dict(config.get("options", {}))
    if lookback_days is not None:
        options["lookback_days"] = lookback_days

    robots_cache: dict = {}
    now = datetime.now(timezone.utc)
    results = {}
    with_news = 0
    for customer in config["customers"]:
        if not customer.get("enabled", True):
            continue
        result = collect_customer(customer, options, robots_cache, now)
        results[customer["customer_id"]] = result
        if result["items"]:
            with_news += 1
        log.info("%s (%s): %s, %d item(s)", result["name"],
                 customer["customer_id"], result["method"] or "no source",
                 len(result["items"]))

    output_path.write_text(
        json.dumps({"generated_utc": now.isoformat(), "customers": results},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    log.info("Customer news: %d of %d customers with recent items",
             with_news, len(results))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="customers.json", type=Path)
    parser.add_argument("--output", default="customer_news.json", type=Path)
    parser.add_argument("--lookback-days", default=None, type=int)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        stream=sys.stdout)
    return run(args.config, args.output, args.lookback_days)


if __name__ == "__main__":
    sys.exit(main())
