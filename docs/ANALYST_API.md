# Read-only Analyst API

The optional Analyst API exposes sanitized operational and research evidence
through the same Caddy HTTPS origin as the dashboard. It is designed for a
trusted analysis client, not for public access, broker control, or strategy
configuration.

## Security boundary

- Disabled by default.
- Separate Bearer token; dashboard passwords and broker credentials are not
  accepted as analyst credentials.
- Token must be at least 32 characters and differ from the dashboard password.
- GET-only routes. Analyst paths reject POST and define no configuration,
  order, allocation, activation, or broker mutation handler.
- Recursive redaction removes credential, password, secret, token, API-key,
  request-ID, and broker-order-ID fields.
- Successful reads create `analyst_api_read` audit events without recording
  the Bearer token.
- Per-client request limit is configurable from 1 to 120 requests per minute.
- Responses use `Cache-Control: no-store` and the dashboard security headers.
- If the audit write fails, the requested data is not returned.

The token must never be placed in a URL, Git, a screenshot, shell history, or
chat. A trusted connector should load it from its own secret store and send it
only in the `Authorization` header.

## Enable on the VPS

Generate a token without printing broker or dashboard secrets:

```bash
openssl rand -hex 32
```

Open `/opt/multitrade/app/.env` and add:

```dotenv
ANALYST_API_ENABLED=true
ANALYST_API_TOKEN=PASTE_THE_NEW_RANDOM_VALUE_HERE
ANALYST_API_REQUESTS_PER_MINUTE=30
```

Then deploy/recreate the dashboard through the normal updater:

```bash
cd /opt/multitrade/app
bash ops/update.sh
```

Do not paste the token into this repository or conversation.

## Routes

- `GET /api/analyst/v1/snapshot`
- `GET /api/analyst/v1/validation-runs`
- `GET /api/analyst/v1/strategies`
- `GET /api/analyst/v1/trades`
- `GET /api/analyst/v1/health`

Every response includes `schema_version=analyst.v1` and `generated_at`.
`limit` is optional and clamped to 1-200.

Example from a trusted client where the token already exists in an environment
variable:

```bash
curl --fail --silent --show-error \
  -H "Authorization: Bearer ${ANALYST_API_TOKEN}" \
  "https://trade.p-y.co.il/api/analyst/v1/validation-runs?limit=20"
```

Avoid running that command on a shared/work computer if its shell history or
process environment is governed by company policy.

## Disable or rotate

Set `ANALYST_API_ENABLED=false` and recreate the dashboard to disable access.
To rotate, generate a new token, replace only `ANALYST_API_TOKEN`, and recreate
the service. Previous tokens stop working immediately after recreation.

The dashboard's existing authenticated browser session remains a separate
path for interactive review. The Bearer API is intended for a later trusted
Codex/MCP connector whose secret storage can supply headers without exposing
the token to the conversation.
