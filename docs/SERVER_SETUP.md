# Hostinger Server Setup

This runbook is for a clean Hostinger KVM 2 VPS running Ubuntu 24.04 LTS.
It keeps the initial system Paper-only and exposes no application port.

## What the first deployment runs

- One Alpaca Paper heartbeat service.
- One authenticated, read-only monitoring service.
- One persistent Docker volume for the temporary SQLite audit database.
- Separate health checks for the engine and monitoring service.
- Rotating local container logs.

It does not publish the dashboard to the internet, submit orders, or contain
live credentials.

Public HTTPS dashboard activation is a separate, opt-in procedure documented
in [`HTTPS_DASHBOARD.md`](HTTPS_DASHBOARD.md). A normal `docker compose up`
does not start the public reverse proxy.

## Secret-handling rules

- Never paste a root password, private SSH key, or Alpaca secret into chat.
- Never commit `.env`.
- Enter secrets only in the server's `.env` file.
- Keep `TRADING_ENABLE_PAPER_ORDERS=false`.
- Use Alpaca Paper credentials only.
- Use a unique dashboard password containing at least 16 characters.

## Deployment flow

The workstation pushes code to a private Git repository over HTTPS. The VPS
pulls a versioned copy of that repository. Hostinger's browser terminal is used
for the initial bootstrap because the workstation's Zscaler path blocks SSH.

All VPS actions are performed manually in Hostinger's browser terminal. The
project does not configure or use Hostinger MCP, modify workstation certificate
settings, or attempt to bypass the workstation's security controls.

The interactive steps are intentionally kept separate:

1. Create the private repository.
2. Push the locally tested code.
3. Bootstrap Docker on the VPS.
4. give the VPS read-only repository access.
5. Clone the repository into `/opt/multitrade/app`.
6. Create the private `.env`.
7. Run `ops/deploy.sh`.

Each step must be verified before continuing. Repository authentication details
will be chosen only after confirming which Git provider account will be used.

## Post-deployment checks

```bash
cd /opt/multitrade/app
docker compose ps
docker compose logs --tail=100 engine
docker compose logs --tail=100 dashboard
docker compose exec engine multitrade healthcheck
docker compose exec dashboard multitrade dashboard-healthcheck
```

Expected conditions:

- The engine and dashboard containers are `running` and become `healthy`.
- Logs show `"environment": "paper"` and `"status": "ok"`.
- The Paper order switch remains false.
- No ports are published by the Compose project.

## Recovery

If the heartbeat fails, do not enable order submission. Inspect:

```bash
cd /opt/multitrade/app
docker compose ps
docker compose logs --tail=200 engine
docker compose logs --tail=200 dashboard
docker compose run --rm --no-deps engine multitrade doctor
```

The engine uses `restart: unless-stopped`, so Docker restarts it after a process
failure or server reboot. A stale or failed heartbeat marks the container
unhealthy for monitoring.
