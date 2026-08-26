# DGL Command Center — Logistics News Aggregator

A small feed aggregator that gives the Command Center a live industry-news
panel: trucking regulation, rates and capacity, carrier failures, tariffs and
port disruption — the stories dispatch and ops actually act on.

It polls the RSS/Atom feeds of the major freight outlets, normalizes and
de-duplicates the items, tags them by topic, and lands them in a JSON snapshot
and (optionally) a `dbo.NewsItem` table on DGLIQ that the portal reads.

## Why RSS

All of the major logistics outlets still publish full RSS feeds, verified
live on 26 Aug 2026:

| Source | Feed URL | Notes |
|---|---|---|
| FreightWaves | `https://www.freightwaves.com/news/feed` | WordPress feed, full content |
| Transport Topics | `https://www.ttnews.com/rss.xml` | Headlines + summaries |
| Land Line (OOIDA) | `https://landline.media/feed/` | Strong on regulatory/enforcement |
| Supply Chain Dive | `https://www.supplychaindive.com/feeds/news/` | Shipper/supply-chain angle |

`feeds.json` also lists The Loadstar, gCaptain, Overdrive and CCJ as
disabled candidates — flip `enabled` after verifying their feed URLs from the
network the job runs on. This costs nothing, needs no API keys or contracts,
and involves no scraping. Paid alternatives (NewsAPI, Bing News, JOC/S&P) only
make sense later if full-text search or paywalled maritime pricing coverage is
needed.

## How it folds into the Command Center

The pieces map onto infrastructure that already exists in the provisioning
spec — nothing new to build:

1. **Worker tier (DGLCC-WRK01)** — run `aggregator.py` as a scheduled task
   every 15–30 minutes under `svc-dglcc-etl$` (it already has *Log on as a
   batch job*). It joins the nine existing reporting jobs; stagger it off the
   08:00 block. Python is already a supported runtime on the worker tier for
   the McLeod Activity Tracker.
2. **DGLIQ** — apply `schema.sql` to create `dbo.NewsItem` (insert-only
   MERGE, 90-day retention via the existing nightly maintenance job). Set
   `sql_connection_string` in `feeds.json` and install `pyodbc`. Without it,
   the job still writes `news.json`, which the portal can serve as a static
   file for a zero-SQL first iteration.
3. **Portal (DGLCC-APP01)** — add a read-only endpoint to the existing API
   site, e.g. a minimal-API route:

   ```csharp
   app.MapGet("/api/news", async (AppDb db, int hours = 48, string? tag = null) =>
       await db.NewsItems
           .Where(n => n.PublishedUtc >= DateTime.UtcNow.AddHours(-hours))
           .Where(n => tag == null || n.Tags.Contains(tag))
           .OrderByDescending(n => n.PublishedUtc)
           .Take(50)
           .ToListAsync());
   ```

   and a dashboard card that renders title / source / age, filterable by the
   tag chips (`regulatory`, `rates-capacity`, `carriers`, `ports-intl`,
   `disruption`, `tech-tms`).
4. **Egress allowlist (§9 of the infrastructure spec)** — each enabled feed
   host must be added for WRK01: `www.freightwaves.com`, `www.ttnews.com`,
   `landline.media`, `www.supplychaindive.com` (all TLS 1.2, port 443).
5. **Monitoring (§13)** — the job exits non-zero only when *every* feed
   fails (one flaky outlet logs an error but doesn't page). Alert on the
   scheduled task's last result, same as the reporting jobs.

## Running it

```
python aggregator.py --config feeds.json --output news.json --max-age-days 7
python -m unittest test_aggregator        # 7 tests, stdlib only
```

No dependencies unless the SQL sink is enabled (`pip install pyodbc`).

## Folding news into the Sales report emails

The Sales team already gets the automated **Carrier Sales / Dispatch
Report** emails (from `automated_reports@deltagrouplog.com`), so
sales-relevant news rides along with those instead of becoming another
email. `sales_digest.py` renders a compact, email-safe "Market Watch"
fragment (tables + inline styles, no scripts/images) from the aggregator's
snapshot, limited to the tags that change how a load gets quoted or
covered: `rates-capacity`, `ports-intl`, `carriers`, `disruption`
(configurable under `sales_digest` in `feeds.json`).

```
python sales_digest.py --config feeds.json --input news.json \
    --output sales_digest.html --hours 24
```

Wire-up in the report job: run `sales_digest.py` right after
`aggregator.py`, then append the fragment file to the email body it
already builds — the fragment is empty when nothing qualifies, so the
append can be unconditional. Recommended cadence: include it in the
**first run of the day (08:00)** only, so hourly urgent reports stay
short and the news doesn't repeat all day.

## Tagging

`tags` in `feeds.json` is a map of tag → keyword list, matched
case-insensitively against title + summary. Tune it freely — it's config,
not code. Items can carry multiple tags; untagged items still appear in the
main feed.

## Deliberately out of scope (for now)

- **Cross-source near-duplicate collapsing** (two outlets covering the same
  story). The per-source dedupe is exact; fuzzy title clustering is a later
  nicety.
- **Summarization/relevance scoring via the Claude API** — a natural v2:
  batch the day's items and ask for a ranked "what matters to a brokerage"
  digest. Needs an `api.anthropic.com` egress entry and an API key decision,
  so it's split out.
- **Email digest** — trivial to add through the existing
  `smtp.office365.com` relay once the ops team wants it.
