"""Tests for the sales digest renderer. Run with: python -m unittest test_sales_digest"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sales_digest

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def make_item(title, tags, hours_ago, source="FreightWaves"):
    return {
        "id": title[:8],
        "source": source,
        "title": title,
        "link": f"https://example.com/{title.replace(' ', '-')}",
        "summary": "",
        "published_utc": (NOW - timedelta(hours=hours_ago)).isoformat(),
        "tags": tags,
    }


class SelectItemsTests(unittest.TestCase):
    def test_filters_by_tag_and_window(self):
        items = [
            make_item("Diesel jumps 12 cents", ["rates-capacity"], 2),
            make_item("Trade show recap", ["tech-tms"], 3),          # wrong tag
            make_item("Old tariff story", ["ports-intl"], 30),       # too old
            make_item("Carrier declares bankruptcy", ["carriers"], 5),
        ]
        selected = sales_digest.select_items(
            items, sales_digest.DEFAULT_TAGS, hours=24, max_items=8, now=NOW
        )
        self.assertEqual(
            [i["title"] for i in selected],
            ["Diesel jumps 12 cents", "Carrier declares bankruptcy"],
        )

    def test_undated_items_excluded_and_cap_applied(self):
        undated = make_item("No date", ["carriers"], 1)
        undated["published_utc"] = None
        items = [undated] + [
            make_item(f"Story {n}", ["disruption"], n) for n in range(10)
        ]
        selected = sales_digest.select_items(
            items, sales_digest.DEFAULT_TAGS, hours=24, max_items=3, now=NOW
        )
        self.assertEqual(len(selected), 3)
        self.assertEqual(selected[0]["title"], "Story 0")  # newest first


class RenderTests(unittest.TestCase):
    def test_empty_renders_empty(self):
        self.assertEqual(sales_digest.render_html([], sales_digest.DEFAULT_TAGS, NOW), "")

    def test_render_escapes_and_labels(self):
        item = make_item("Rates <up> & away", ["rates-capacity"], 3)
        html_out = sales_digest.render_html([item], sales_digest.DEFAULT_TAGS, NOW)
        self.assertIn("Rates &lt;up&gt; &amp; away", html_out)
        self.assertIn("Rates &amp; Capacity", html_out)
        self.assertIn("3h ago", html_out)
        self.assertIn("Market Watch", html_out)
        self.assertNotIn("<script", html_out)


class RunTests(unittest.TestCase):
    def test_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "feeds.json").write_text(json.dumps({
                "feeds": [],
                "sales_digest": {"tags": ["carriers"], "max_items": 5},
            }))
            (tmp_path / "news.json").write_text(json.dumps({
                "items": [
                    make_item("Fraud ring indicted", ["carriers"], 1),
                    make_item("Ignored story", ["tech-tms"], 1),
                ]
            }))
            out = tmp_path / "digest.html"
            code = sales_digest.run(tmp_path / "feeds.json", tmp_path / "news.json", out, 24)
            self.assertEqual(code, 0)
            content = out.read_text()
            self.assertIn("Fraud ring indicted", content)
            self.assertNotIn("Ignored story", content)

    def test_missing_snapshot_writes_empty_fragment(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "feeds.json").write_text(json.dumps({"feeds": []}))
            out = tmp_path / "digest.html"
            code = sales_digest.run(
                tmp_path / "feeds.json", tmp_path / "missing.json", out, 24
            )
            self.assertEqual(code, 0)
            self.assertEqual(out.read_text(), "")


if __name__ == "__main__":
    unittest.main()
