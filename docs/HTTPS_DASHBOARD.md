# HTTPS Dashboard Activation

The dashboard remains internal by default. The `caddy` service belongs to the
optional `public-dashboard` Docker Compose profile, so a normal deployment does
not publish ports 80 or 443.

Activate this profile only after completing every prerequisite:

1. Choose a dedicated subdomain, such as `trading.example.com`.
2. Create a DNS `A` record pointing that subdomain to the VPS public IPv4
   address.
3. Confirm ports 80/TCP and 443/TCP are allowed to reach the VPS.
4. Set `DASHBOARD_DOMAIN` in the server's private `.env` file.
5. Confirm the dashboard username and long, unique password are already set.

Start the HTTPS service explicitly:

```bash
cd /opt/multitrade/app
docker compose --profile public-dashboard config --quiet
docker compose --profile public-dashboard pull caddy
docker compose --profile public-dashboard up -d caddy
docker compose --profile public-dashboard ps
docker compose --profile public-dashboard logs --tail=100 caddy
```

Caddy obtains and renews the public TLS certificate automatically and redirects
HTTP to HTTPS. Its certificate state is stored in the persistent `caddy-data`
volume.

Do not expose the dashboard container's port 8080 directly. Browser access must
use the configured HTTPS hostname and the dashboard credentials. The first
public release remains read-only for broker actions and Paper-only. Its
Management workspace can write only audited strategy configuration overrides;
it has no broker-order client and cannot enable live trading.

To stop public access without stopping the engine:

```bash
cd /opt/multitrade/app
docker compose --profile public-dashboard stop caddy
```
