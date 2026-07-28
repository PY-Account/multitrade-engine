# Asset Universe

The Asset Universe expands strategy research beyond indexes and a small set
of mega-cap stocks. It is a read-only research department: it can recommend
symbols and feed them into the Strategy Lab, but it cannot add a symbol to an
executable account watchlist or grant Paper permission.

## Selection pipeline

Each policy in `config/asset_universe.json` can draw candidates from:

- a manually maintained seed list;
- Alpaca's current most-active stock screener; or
- both sources, de-duplicated.

Every candidate must then pass all configured gates:

1. The Alpaca asset catalog identifies it as an active, tradable US equity.
2. Its exchange is allowed.
3. Its latest daily close is at or above the price floor.
4. Its sourced company-size evidence is at or above the configured floor.
5. The company-size evidence is recent enough.
6. Average daily share volume meets the liquidity floor.
7. Average daily dollar volume meets the liquidity floor.
8. If an index restriction is configured, the symbol belongs to at least one
   required, dated index snapshot.
9. Every required index snapshot is recent enough.

The default broad policy uses:

- price at least USD 3;
- company-size evidence at least USD 300 million;
- 20-session average share volume at least 500,000;
- 20-session average dollar volume at least USD 10 million; and
- active Alpaca tradability on NASDAQ, NYSE, ARCA, or AMEX.

These are configurable research floors, not proof that an asset or strategy is
safe or profitable.

## Company-size evidence

Alpaca's asset catalog and stock bars do not supply market capitalization. The
selector therefore fails the company-size gate unless it has one of:

- a dated `asset_references` record with a source URL; or
- SEC company facts, enabled by `TRADING_SEC_USER_AGENT`.

The SEC client first estimates company size as the latest reported common
shares outstanding multiplied by the current screened price. If that fact is
unavailable, it can use reported public float as a conservative lower-bound
size check. The method, date, and SEC source URL are stored with the
evaluation. It is an estimate, not a vendor-grade real-time market cap.

The SEC asks automated clients to declare an organization and contact. Add a
real contact on the VPS only:

```dotenv
TRADING_SEC_USER_AGENT=MultiTrade Research operator@example.com
```

Do not commit the `.env` file. The User-Agent is sent to the SEC but is not
shown in the dashboard or stored in audit reports.

Alternatively, add a dated reference:

```json
{
  "symbol": "EXAMPLE",
  "company_size_usd": "750000000",
  "size_method": "market_cap",
  "as_of": "2026-07-28",
  "source_url": "https://source.example/company/EXAMPLE"
}
```

Stale or absent evidence fails closed.

## Optional S&P 500 or Nasdaq-100 restriction

Exchange and index membership are different facts. A NASDAQ-listed security is
not automatically a Nasdaq-100 constituent. The engine will apply an index
filter only when `index_snapshots` contains a dated and sourced symbol list.

Example:

```json
{
  "index_id": "nasdaq100",
  "label": "Nasdaq-100",
  "as_of": "2026-07-28",
  "source_url": "https://indexes.nasdaqomx.com/Index/Overview/NDX",
  "symbols": ["... reviewed constituent symbols ..."]
}
```

Then set the policy:

```json
"required_index_sets": ["sp500", "nasdaq100"]
```

This means membership in either configured set. The default has no index
restriction, allowing liquid eligible mid-cap and large-cap stocks outside
those indexes to be examined. Constituent snapshots must be reviewed and
updated; the policy rejects stale snapshots.

## Per-strategy assignments

`strategy_assignments` controls which symbols the Strategy Lab tests:

- `account_watchlist`: use the execution account's reviewed watchlist.
- `manual`: use only `manual_symbols`.
- `recommended`: use the latest passing recommendations for `policy_id`.
- `combined`: manual symbols followed by latest recommendations.

Manual research selections are intentional overrides: they can be tested even
when they were not recommended. They still receive no execution permission.
If a recommendation cycle has not completed, an empty recommended selection
falls back to the account watchlist so validation remains operational.

## Separate execution boundary

The account execution boundary remains `config/paper_portfolio.json`:

- `watchlist` is the account-wide set the automation worker can fetch.
- each strategy's `symbols` is its approved subset of that watchlist;
- `paper_execution_allowed` is an independent permission; and
- all global automation, Paper submission, emergency-stop, and risk controls
  still apply.

To promote a researched symbol, an operator must deliberately add it to both
the account `watchlist` and the relevant strategy `symbols`, then separately
review Paper permission. The Asset Universe never performs that edit.

## Operator commands

Run one selection cycle:

```bash
cd /opt/multitrade/app
docker compose run --rm asset-universe multitrade asset-universe --once
```

Inspect service status:

```bash
docker compose --profile public-dashboard ps
docker compose --profile public-dashboard logs --tail=150 asset-universe
```

The authenticated dashboard exposes recommendations, rejected candidates,
gate evidence, policy thresholds, index provenance, and per-strategy
assignments in the **Asset Universe** workspace.
