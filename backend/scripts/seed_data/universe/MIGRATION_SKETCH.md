# Universe schema migration — sketch

Tables to create when Stream 1 day 1 begins. Lives on `Phase2Base` (the new
Postgres schema), not the legacy SQLite/Postgres bases.

## Tables

### `company`

Companies are the analytical unit. One company → one or more listings.

```sql
CREATE TABLE company (
    company_id      TEXT PRIMARY KEY,             -- 'tsmc', 'alibaba', 'tokyo_electron'
    display_name    TEXT NOT NULL,                -- 'Taiwan Semiconductor'
    legal_name      TEXT,
    hq_country      TEXT,                         -- 'TW', 'CN', 'US', 'JP', 'KR'
    fiscal_year_end TEXT,                         -- 'Dec', 'Mar', 'Jun', 'Sep'
    filings_source  TEXT,                         -- 'sec_10k' | 'sec_20f' | 'hkex' | 'tdnet' | 'mops' | 'dart'
    website         TEXT,
    summary         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### `listing`

Tradeable instruments. A company can have multiple listings (BABA, 9988.HK).

```sql
CREATE TABLE listing (
    ticker          TEXT PRIMARY KEY,             -- 'BABA', '9988.HK', '2330.TW'
    company_id      TEXT NOT NULL REFERENCES company(company_id) ON DELETE CASCADE,
    exchange        TEXT NOT NULL,                -- 'NYSE', 'NASDAQ', 'TWSE', 'HKEX', 'JPX', 'KOSPI', 'SSE', 'SZSE', 'Euronext', 'LSE', 'ASX', 'SIX', 'Xetra', 'TSX'
    currency        TEXT NOT NULL,                -- 'USD', 'TWD', 'HKD', 'JPY', 'KRW', 'CNY', 'EUR', 'GBP', 'AUD', 'CHF', 'CAD'
    is_primary      BOOLEAN NOT NULL DEFAULT false,
    listed_at       DATE,                          -- listing date if known
    delisted_at     DATE,                          -- NULL if active
    status          TEXT NOT NULL DEFAULT 'active', -- 'active' | 'pre_ipo' | 'recent_ipo' | 'delisted' | 'acquired'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_listing_company ON listing(company_id);
CREATE INDEX idx_listing_status  ON listing(status);
-- partial unique: each company has at most one primary listing
CREATE UNIQUE INDEX idx_listing_primary ON listing(company_id) WHERE is_primary = true;
```

### `universe_group`

The thesis groups (`ai_compute_design`, `ai_materials_critical_minerals`, …).
Defined in code/JSON, mirrored to DB so foreign keys work.

```sql
CREATE TABLE universe_group (
    group_id        TEXT PRIMARY KEY,             -- 'ai_compute_design'
    display_name    TEXT NOT NULL,                -- 'AI Compute — Designers'
    description     TEXT,
    layer           TEXT,                         -- 'compute' | 'infra' | 'hosting' | 'energy' | 'materials' | 'software' | 'consumer' | 'industrial' | 'emerging' | 'index'
    sort_order      INT NOT NULL DEFAULT 999,
    is_index        BOOLEAN NOT NULL DEFAULT false  -- true for auto-fetched index baselines (SPX, SMH, ...)
);
```

### `universe_group_member`

Which tickers belong to which groups. Many-to-many. `weight` lets the
heatmap sort by AI-thesis-relevance.

```sql
CREATE TABLE universe_group_member (
    group_id        TEXT NOT NULL REFERENCES universe_group(group_id) ON DELETE CASCADE,
    ticker          TEXT NOT NULL REFERENCES listing(ticker) ON DELETE CASCADE,
    is_primary      BOOLEAN NOT NULL DEFAULT false,  -- the group this ticker is "most" associated with
    weight          REAL NOT NULL DEFAULT 1.0,        -- 0.0–1.0; pure-play = 1.0, adjacent = 0.3
    notes           TEXT,
    PRIMARY KEY (group_id, ticker)
);

CREATE INDEX idx_universe_group_member_ticker ON universe_group_member(ticker);
-- partial unique: each ticker has at most one is_primary=true row
CREATE UNIQUE INDEX idx_universe_group_member_primary
    ON universe_group_member(ticker) WHERE is_primary = true;
```

### `pre_ipo_watch`

Private companies tracked as metadata-only watch entries. Status flips to
`recent_ipo` and a `listing` row is inserted when the IPO happens.

```sql
CREATE TABLE pre_ipo_watch (
    id                   TEXT PRIMARY KEY,        -- 'zhipu_ai', 'openai', ...
    display_name         TEXT NOT NULL,
    country              TEXT,
    category             TEXT,                    -- 'model_developer' | 'ai_chip' | 'autonomous_driving' | 'data_platform' | ...
    summary              TEXT,
    filings_status       TEXT,                    -- free-form status string
    expected_listing     TEXT,                    -- '2026-Q1 (estimated)' / 'TBD'
    expected_exchange    TEXT,
    last_round_payload   JSONB,                   -- {date, amount_usd, valuation_usd, lead}
    tags                 TEXT[],
    groups               TEXT[],                  -- pre-IPO companies still belong to thesis groups
    post_ipo_ticker      TEXT,                    -- set when listing happens
    company_id           TEXT REFERENCES company(company_id),  -- NULL pre-IPO; set after listing
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### `user_universe_group`

Group-level subscription per user. The user's universe = union of subscribed
groups + their manual ticker adds.

```sql
CREATE TABLE user_universe_group (
    user_id         UUID NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    group_id        TEXT NOT NULL REFERENCES universe_group(group_id) ON DELETE CASCADE,
    subscribed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    display_order   INT,
    PRIMARY KEY (user_id, group_id)
);
```

### `user_universe_ticker`

Per-user manual additions (and removals — tombstone pattern). When a user
adds an unusual name, this is where it lives in addition to (or instead of)
group membership.

```sql
CREATE TABLE user_universe_ticker (
    user_id         UUID NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    ticker          TEXT NOT NULL REFERENCES listing(ticker) ON DELETE CASCADE,
    added_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    source          TEXT NOT NULL,                -- 'manual' | 'preset:<group_id>' | 'auto_promoted'
    is_pinned       BOOLEAN NOT NULL DEFAULT false,
    notes           TEXT,
    PRIMARY KEY (user_id, ticker)
);

CREATE INDEX idx_user_universe_ticker_user ON user_universe_ticker(user_id);
```

## Auto-promotion flow

```
POST /api/v1/me/universe/add  { "ticker": "9988.HK" }
  ↓
1. Check listing — if exists, just INSERT into user_universe_ticker. Done.
2. Not found → call yfinance.Ticker(ticker).info to verify existence + fetch
   {longName, exchange, currency, sector, industry, country}.
3. Heuristic company_id: slugify longName, fall back to ticker if collision.
4. Probe yfinance prices — fetch 5d daily; if empty, reject (likely bad ticker).
5. Begin TX:
     UPSERT company
     INSERT listing (status='active')
     INSERT user_universe_ticker (source='manual')
   COMMIT.
6. Enqueue background job: backfill 10y daily prices + 60d intraday +
   trigger SEC EDGAR XBRL probe (if hq_country='US') to fill T2 fundamentals.
7. Return {status: 'added', backfill: 'in_progress', estimated_completion: '~2 min'}.
```

## Effective-universe query (for any feature that needs "user's tickers")

```sql
-- Returns the union of: user's group subscriptions + user's manual tickers
SELECT DISTINCT l.ticker, l.company_id, c.display_name, c.hq_country
FROM listing l
JOIN company c USING (company_id)
LEFT JOIN universe_group_member ugm USING (ticker)
LEFT JOIN user_universe_group uug ON uug.group_id = ugm.group_id AND uug.user_id = $1
LEFT JOIN user_universe_ticker uut ON uut.ticker = l.ticker AND uut.user_id = $1
WHERE l.status = 'active'
  AND (uug.user_id IS NOT NULL OR uut.user_id IS NOT NULL);
```

Probably wrap in a Postgres view `user_effective_universe(user_id)` so
Pillar A queries don't have to repeat this JOIN.

## Index baseline auto-fetch

Separate cron module `backend/app/services/universe/index_fetchers.py`:

| Group | Source | Refresh |
|---|---|---|
| `index_smh` | iShares CSV `https://www.ishares.com/us/products/239705/ishares-semiconductor-etf` | weekly |
| `index_spx` | SSGA SPDR S&P 500 holdings CSV | weekly |
| `index_twse` | TWSE OpenAPI BWIBBU_d (top 50) + manual top 200 | weekly |
| `index_nikkei225` | Nikkei composition page | weekly |
| `index_kospi200` | KRX OpenAPI | weekly |

Each fetcher:
1. Pull list of (ticker, weight_in_index)
2. UPSERT into `listing` (auto-promote unknown tickers)
3. DELETE-then-INSERT into `universe_group_member` for that group_id with
   `weight = ETF_weight` (analyst meaning here is "index weight," not
   thesis-relevance — separate semantic from curated groups; may rename
   the column later or split into `member_weight` vs `index_weight`).

## Migration order

```
0005_universe_company_listing.py     # company + listing tables
0006_universe_groups.py              # universe_group + member + pre_ipo_watch
0007_user_universe.py                # user_universe_group + user_universe_ticker
0008_user_universe_view.py           # the user_effective_universe view
```

## Seed loader

`backend/scripts/seed_universe.py`:

1. Load `broad_universe_seed_v1.csv`. For each row:
   - Skip header + `__INDEX__*` placeholders.
   - Resolve `company_id` from ticker (slug heuristic; manual override file
     for dual-listings to share company_id).
   - UPSERT `company` and `listing`.
   - UPSERT `universe_group_member`.
2. Load `pre_ipo_watchlist_v1.json` → UPSERT `pre_ipo_watch` rows.
3. UPSERT `universe_group` from a code-side dict (display_name, layer,
   sort_order — derived from group_id).
4. Run validation:
   - Every ticker has exactly one `is_primary=true` group membership.
   - Every company has exactly one `is_primary=true` listing.
   - No orphan group members (group_id exists in `universe_group`).
5. Print summary: N companies, N listings, N group memberships, N pre-IPO.
