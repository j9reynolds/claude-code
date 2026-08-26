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

## Folding news into the Account Health emails

News goes into exactly one email family: **The DGL Command Center -
Account Health** emails (from `DGLCommandCenter@DeltaGroupLog.com`) —
the daily per-salesperson reports and the manager rollup. It does NOT
go into the Carrier Sales / Dispatch Report or other operational
report emails; those stay short and urgent.

`sales_digest.py` renders a compact, email-safe "Market Watch" fragment
(tables + inline styles, no scripts/images) from the aggregator's
snapshot, limited to the tags that matter in customer conversations:
`rates-capacity`, `ports-intl`, `carriers`, `disruption` (configurable
under `sales_digest` in `feeds.json`).

```
python sales_digest.py --config feeds.json --input news.json \
    --output sales_digest.html --hours 24
```

Wire-up in the Account Health job: run `sales_digest.py` right after
`aggregator.py`, then append the fragment file to the email body it
already builds (after the account tables, before the footer) — the
fragment is empty when nothing qualifies, so the append can be
unconditional. The Account Health send is daily, which maps one-to-one
onto the default 24-hour lookback: each item appears exactly once. The
same fragment works for both the per-salesperson emails and the
manager rollup.

## Per-customer news (customer_news.py)

Besides industry news, the Account Health call-prep blocks can carry news
about the customer itself — a plant opening, an acquisition, a product
launch is exactly the opener a save or growth call wants.

`customer_news.py` reads `customers.json` (McLeod customer id, company
name, website domain — source domains from HubSpot's company `domain`
property) and collects recent items per customer, two ways:

1. **Domain feed discovery** — probes the customer's own site for an
   RSS/Atom press feed (common paths, then the homepage's
   `<link rel="alternate">` tags). Highest-signal when it exists.
2. **Google News fallback** — an RSS search for the exact company name
   via `news.google.com/rss/search`. Covers everyone else, and costs one
   egress host instead of hundreds.

```
python customer_news.py --config customers.json --output customer_news.json
```

Output is keyed by McLeod customer id so the Account Health job can join
it straight onto its account rows. Crawling is polite: robots.txt honored
per domain, a handful of requests per customer, one run per day scheduled
ahead of the Account Health send.

Egress note: domain discovery needs outbound 443 to each enabled
customer domain — if the allowlist stays strict, set `"domain": null`
and rely on the Google News fallback (`news.google.com` only).

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
