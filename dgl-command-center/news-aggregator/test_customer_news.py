"""Tests for the per-customer news crawler. Run with: python -m unittest test_customer_news"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import customer_news

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def rss(*items):
    body = "".join(
        f"<item><title>{t}</title><link>{l}</link><pubDate>{d}</pubDate></item>"
        for t, l, d in items
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>{body}</channel></rss>'.encode()


FRESH = (NOW - timedelta(days=2)).strftime("%a, %d %b %Y %H:%M:%S +0000")
STALE = (NOW - timedelta(days=40)).strftime("%a, %d %b %Y %H:%M:%S +0000")

HOMEPAGE_WITH_FEED = b"""<html><head>
<link rel="alternate" type="application/rss+xml" title="News" href="/press/releases.xml">
</head><body>hi</body></html>"""


class FeedLinkParserTests(unittest.TestCase):
    def test_finds_alternate_feed_links(self):
        parser = customer_news.FeedLinkParser()
        parser.feed(HOMEPAGE_WITH_FEED.decode())
        self.assertEqual(parser.feed_urls, ["/press/releases.xml"])

    def test_ignores_stylesheets(self):
        parser = customer_news.FeedLinkParser()
        parser.feed('<link rel="stylesheet" type="text/css" href="/a.css">')
        self.assertEqual(parser.feed_urls, [])


class RecentItemsTests(unittest.TestCase):
    def test_lookback_and_cap(self):
        payload = rss(
            ("New DC opening", "https://x.com/a", FRESH),
            ("Old story", "https://x.com/b", STALE),
        )
        items = customer_news.recent_items(payload, "x.com", 14, 3, NOW)
        self.assertEqual([i["title"] for i in items], ["New DC opening"])

    def test_garbage_returns_empty(self):
        self.assertEqual(customer_news.recent_items(b"not xml <", "x", 14, 3, NOW), [])


class CollectCustomerTests(unittest.TestCase):
    def test_domain_feed_preferred_over_google(self):
        payload = rss(("Press release", "https://acme.com/pr/1", FRESH))
        with mock.patch.object(customer_news, "discover_domain_feed", return_value=payload), \
             mock.patch.object(customer_news, "google_news_feed") as gn:
            result = customer_news.collect_customer(
                {"customer_id": "ACME", "name": "Acme Co", "domain": "www.Acme.com"},
                {}, {}, NOW)
        gn.assert_not_called()
        self.assertEqual(result["method"], "domain-feed")
        self.assertEqual(result["domain"], "acme.com")  # normalized
        self.assertEqual(result["items"][0]["source"], "acme.com")

    def test_google_fallback_when_domain_feed_empty(self):
        gn_payload = rss(("Acme wins award", "https://news.example/1", FRESH))
        with mock.patch.object(customer_news, "discover_domain_feed", return_value=None), \
             mock.patch.object(customer_news, "google_news_feed", return_value=gn_payload):
            result = customer_news.collect_customer(
                {"customer_id": "ACME", "name": "Acme Co", "domain": "acme.com"},
                {}, {}, NOW)
        self.assertEqual(result["method"], "google-news")
        self.assertEqual(result["items"][0]["source"], "Google News")

    def test_fallback_disabled(self):
        with mock.patch.object(customer_news, "discover_domain_feed", return_value=None), \
             mock.patch.object(customer_news, "google_news_feed") as gn:
            result = customer_news.collect_customer(
                {"customer_id": "ACME", "name": "Acme Co", "domain": "acme.com"},
                {"google_news_fallback": False}, {}, NOW)
        gn.assert_not_called()
        self.assertEqual(result["method"], None)
        self.assertEqual(result["items"], [])


class RobotsTests(unittest.TestCase):
    def test_disallow_honored_and_cached(self):
        robots = b"User-agent: *\nDisallow: /press/\n"
        with mock.patch.object(customer_news, "fetch", return_value=robots) as fetched:
            cache = {}
            self.assertFalse(customer_news.robots_allows("acme.com", "/press/feed", cache))
            self.assertTrue(customer_news.robots_allows("acme.com", "/feed", cache))
        self.assertEqual(fetched.call_count, 1)  # robots.txt fetched once per domain

    def test_unreachable_robots_allows(self):
        with mock.patch.object(customer_news, "fetch", side_effect=OSError("down")):
            self.assertTrue(customer_news.robots_allows("acme.com", "/feed", {}))


class RunTests(unittest.TestCase):
    def test_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "customers.json").write_text(json.dumps({
                "options": {"lookback_days": 14},
                "customers": [
                    {"customer_id": "ACME", "name": "Acme Co", "domain": "acme.com"},
                    {"customer_id": "SKIP", "name": "Skipped", "enabled": False},
                ],
            }))
            payload = rss(("Acme expands", "https://acme.com/pr/2", FRESH))
            out = tmp_path / "customer_news.json"
            with mock.patch.object(customer_news, "discover_domain_feed", return_value=payload):
                code = customer_news.run(tmp_path / "customers.json", out, None)
            self.assertEqual(code, 0)
            data = json.loads(out.read_text())
            self.assertIn("ACME", data["customers"])
            self.assertNotIn("SKIP", data["customers"])
            self.assertEqual(data["customers"]["ACME"]["items"][0]["title"], "Acme expands")


if __name__ == "__main__":
    unittest.main()
