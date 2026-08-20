# Admin control plane

The dashboard can start selected server-management actions through an
internal Admin Agent. The first supported actions are:

- Guarded GitHub update: pull `origin/main`, rebuild Docker services, run the
  existing doctor checks, and bring the deployment back up.
- Allowlisted `.env` setting edits, such as the dedicated options account UUID
  and Paper execution gates.

The Admin Agent is not exposed to the public internet. It is enabled only when
`ADMIN_AGENT_TOKEN` is present in `.env` and has at least 32 characters.

## One-time setup

On the VPS, create a strong token and add it to `.env`:

```bash
cd /opt/multitrade/app
python -c 'import secrets; print(secrets.token_urlsafe(32))'
nano .env
```

Add:

```bash
ADMIN_AGENT_TOKEN=replace-with-the-generated-token
```

Then run one final terminal update:

```bash
cd /opt/multitrade/app
bash ops/update.sh
```

When `ADMIN_AGENT_TOKEN` is configured, `ops/deploy.sh` automatically enables
the `admin` Compose profile.

## Future updates from the dashboard

Open:

`Management -> Server Update -> Update from GitHub`

The dashboard will show:

- Admin Agent availability.
- Last update state.
- Last update time.
- Exit code or error message.
- A short tail of the update output if the run fails.

## Future settings edits from the dashboard

Open:

`Management -> Server Settings`

The UI can update allowlisted server settings. Secrets are write-only: the
dashboard can replace them, but never displays the existing clear-text value.
After changing `.env`, run `Management -> Server Update` so services restart
with the new values.

## Safety model

- The dashboard never receives raw shell access.
- The Admin Agent accepts only bearer-token authenticated requests.
- The first allowlisted actions are `ops/update.sh` and selected `.env`
  key updates.
- The action is audited in the dashboard event log.
- The service is internal to Docker networking and has no public port mapping.
