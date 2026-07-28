# Multi-Account Paper Operations

Release 0.13 can supervise multiple Alpaca Paper accounts in one VPS
deployment. This is account isolation inside one application instance, not
distributed or live trading.

## Identity model

Each object in `config/paper_portfolio.json` has three separate identities:

- `account_id`: the internal, stable name used by the audit ledger and
  dashboard.
- `credential_env_prefix`: the prefix of the secret variables in the VPS
  `.env` file.
- `expected_broker_account_id`: the Alpaca Paper account UUID that those
  credentials must return.

When more than one account is enabled, credential prefixes must be unique and
every expected broker account ID is mandatory. A mismatch stops that account's
cycle before state is accepted or an order can be evaluated.

## Adding a second account

Duplicate the complete account object in `config/paper_portfolio.json`, then
change at least:

```json
{
  "account_id": "alpaca-paper-growth",
  "credential_env_prefix": "ALPACA_GROWTH",
  "expected_broker_account_id": "the-paper-account-uuid-from-alpaca",
  "enabled": true
}
```

Configure its watchlist, limits, and every strategy allocation independently.
Do not put either API secret in JSON or Git.

Add the matching secret names only to the VPS `.env`:

```dotenv
ALPACA_GROWTH_API_KEY_ID=
ALPACA_GROWTH_API_SECRET_KEY=
ALPACA_GROWTH_BASE_URL=https://paper-api.alpaca.markets
```

The original single account continues to use `ALPACA_API_KEY_ID`,
`ALPACA_API_SECRET_KEY`, and `ALPACA_BASE_URL`.

Obtain the expected account UUID from the authenticated Alpaca Paper account
response or the Alpaca Paper account interface. Do not guess it and do not use
a live-account identifier.

## Execution controls

The global automation, Paper submission, and emergency-stop variables apply to
the whole VPS. Each strategy allocation still has its own
`paper_execution_allowed` value in each account object. Therefore the same
strategy may be:

- observation-only in one account;
- risk-evaluated dry-run in another; and
- approved for guarded Paper submission in a third.

The 3% per-trade and 10% aggregate ceilings are evaluated independently using
that account's own equity and active reservations. Statistics, signal IDs,
orders, positions, and option packages retain their account ID.

## Failure behavior

Every worker processes enabled accounts sequentially. If one account has an
authentication, data, broker-identity, or runtime failure:

1. Its failure is written to the audit ledger.
2. Remaining accounts still run.
3. Aggregate component health changes to `degraded`.
4. A cycle is `error` only when every configured account fails.

This avoids one broken connector silently stopping all other Paper accounts.
It does not make a failed account safe from broker-side position risk; open
orders and positions must still be inspected directly at Alpaca.

## Validation

Before enabling a second account:

```bash
cd /opt/multitrade/app
docker compose run --rm engine multitrade doctor
docker compose run --rm engine multitrade run --once
docker compose run --rm automation multitrade automate --once
```

The doctor output must show every account with `credentials_ready: true` and
`broker_identity_pinned: true`. Keep all per-strategy Paper permissions false
until both account views show the intended balances and no unmanaged orders or
positions.

## Scaling limit

One Compose deployment and one SQLite audit database remain authoritative.
Do not run a second automation container or a second VPS against the same
accounts. Multi-host operation requires a coordinated database, distributed
account leases, portfolio-wide exposure controls, and cross-instance
idempotency.
