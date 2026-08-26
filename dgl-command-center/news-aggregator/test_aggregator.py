"""Tests for the news aggregator's parsing, tagging, dedupe and pruning.

Fixtures mirror the real shapes of the verified sources: WordPress RSS 2.0
(FreightWaves, Land Line), Drupal RSS with RFC 822 dates (Transport Topics),
and a generic Atom feed. Run with: python -m unittest test_aggregator
"""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import aggregator

TAG_RULES = {
    "regulatory": ["FMCSA", "CDL", "emission"],
    "ports-intl": ["tariff", "Panama Canal"],
}

RSS_WORDPRESS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Land Line Media</title>
  <item>
    <title>Time running out for truckers to shape EPA truck emission rule</title>
    <link>https://landline.media/epa-truck-emission-rule/</link>
    <guid isPermaLink="false">https://landline.media/?p=103690</guid>
    <pubDate>Tue, 25 Aug 2026 18:09:17 +0000</pubDate>
    <description><![CDATA[<p>Comment period closes &amp; soon.</p>]]></description>
  </item>
  <item>
    <title>STOP Act puts states on notice over CDL issuance</title>
    <link>https://landline.media/stop-act-cdl/</link>
    <guid>https://landline.media/?p=103675</guid>
    <pubDate>Mon, 24 Aug 2026 19:21:50 +0000</pubDate>
    <description>States face FMCSA scrutiny.</description>
  </item>
  <item>
    <title>Item with no link is dropped</title>
    <pubDate>Mon, 24 Aug 2026 19:21:50 +0000</pubDate>
  </item>
</channel></rss>"""

RSS_NO_GUID = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Transport Topics</title>
  <item>
    <title>Asian shipper pays record $5.3M for Panama Canal access</title>
    <link>https://www.ttnews.com/articles/asian-shipper-panama-canal</link>
    <pubDate>Tue, 25 Aug 26 13:51:43 EDT</pubDate>
    <description>Demand soared after trade routes shifted.</description>
  </item>
</channel></rss>"""

ATOM_FEED = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example Atom</title>
  <entry>
    <id>tag:example.com,2026:entry-1</id>
    <title>New tariff schedule published</title>
    <link rel="alternate" href="https://example.com/tariff-schedule"/>
    <published>2026-08-25T10:00:00Z</published>
    <summary>Cross-border rates change Sept 8.</summary>
  </entry>
</feed>"""


class ParseFeedTests(unittest.TestCase):
    def test_wordpress_rss(self):
        items = aggregator.parse_feed(RSS_WORDPRESS, "Land Line", TAG_RULES)
        self.assertEqual(len(items), 2)  # linkless item dropped
        first = items[0]
        self.assertEqual(first["source"], "Land Line")
        self.assertEqual(first["link"], "https://landline.media/epa-truck-emission-rule/")
        self.assertEqual(first["summary"], "Comment period closes & soon.")
        self.assertEqual(first["published_utc"], "2026-08-25T18:09:17+00:00")
        self.assertEqual(first["tags"], ["regulatory"])
        self.assertEqual(items[1]["tags"], ["regulatory"])

    def test_rss_without_guid_falls_back_to_link(self):
        items = aggregator.parse_feed(RSS_NO_GUID, "Transport Topics", TAG_RULES)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["id"], aggregator.item_id("Transport Topics", item["link"]))
        self.assertEqual(item["tags"], ["ports-intl"])
        # EDT offset normalized to UTC
        self.assertEqual(item["published_utc"], "2026-08-25T17:51:43+00:00")

    def test_atom(self):
        items = aggregator.parse_feed(ATOM_FEED, "Example", TAG_RULES)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["link"], "https://example.com/tariff-schedule")
        self.assertEqual(item["published_utc"], "2026-08-25T10:00:00+00:00")
        self.assertEqual(item["tags"], ["ports-intl"])

    def test_stable_ids_dedupe_across_runs(self):
        run1 = aggregator.parse_feed(RSS_WORDPRESS, "Land Line", TAG_RULES)
        run2 = aggregator.parse_feed(RSS_WORDPRESS, "Land Line", TAG_RULES)
        self.assertEqual([i["id"] for i in run1], [i["id"] for i in run2])


class PruneTests(unittest.TestCase):
    def test_prune_drops_old_keeps_undated(self):
        now = datetime(2026, 8, 26, tzinfo=timezone.utc)
        items = {
            "old": {"id": "old", "published_utc": "2026-08-01T00:00:00+00:00"},
            "new": {"id": "new", "published_utc": "2026-08-25T00:00:00+00:00"},
            "undated": {"id": "undated", "published_utc": None},
        }
        kept = aggregator.prune(items, max_age_days=7, now=now)
        self.assertEqual(set(kept), {"new", "undated"})


class RunTests(unittest.TestCase):
    def test_run_end_to_end_with_stubbed_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = {
                "feeds": [
                    {"name": "Land Line", "url": "https://landline.media/feed/"},
                    {"name": "Broken", "url": "https://broken.example/feed"},
                ],
                "tags": TAG_RULES,
            }
            config_path = tmp_path / "feeds.json"
            config_path.write_text(json.dumps(config))
            output_path = tmp_path / "news.json"

            def fake_fetch(url):
                if "broken" in url:
                    raise OSError("connection refused")
                return RSS_WORDPRESS

            original = aggregator.fetch_feed
            aggregator.fetch_feed = fake_fetch
            try:
                exit_code = aggregator.run(config_path, output_path, 7, dry_run=False)
                # Second run: same items, nothing duplicated
                exit_code_2 = aggregator.run(config_path, output_path, 7, dry_run=False)
            finally:
                aggregator.fetch_feed = original

            self.assertEqual(exit_code, 0)  # one feed failing is not fatal
            self.assertEqual(exit_code_2, 0)
            snapshot = json.loads(output_path.read_text())
            self.assertEqual(len(snapshot["items"]), 2)
            self.assertEqual(
                snapshot["items"][0]["title"],
                "Time running out for truckers to shape EPA truck emission rule",
            )

    def test_run_exits_nonzero_when_all_feeds_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "feeds.json"
            config_path.write_text(json.dumps({
                "feeds": [{"name": "Broken", "url": "https://broken.example/feed"}],
                "tags": {},
            }))

            def fake_fetch(url):
                raise OSError("egress blocked")

            original = aggregator.fetch_feed
            aggregator.fetch_feed = fake_fetch
            try:
                exit_code = aggregator.run(config_path, tmp_path / "news.json", 7, dry_run=False)
            finally:
                aggregator.fetch_feed = original
            self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
