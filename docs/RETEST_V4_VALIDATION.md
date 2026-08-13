# Retest v4 validation commands

Use these commands on the Hostinger VPS after updating the app to a version
that contains `confirmed_breakout_retest_v4`.

```bash
cd /opt/multitrade/app
bash ops/update.sh
```

Check that the updated version is loaded:

```bash
docker compose run --rm --no-deps engine multitrade doctor
```

Run the focused higher-timeframe validation:

```bash
docker compose run --rm --no-deps engine multitrade accelerated-validation \
  --workers 2 \
  --timeframes 1Day,4Hour \
  --optimize \
  --force-all \
  --max-candidates 80
```

If Alpaca rate-limits or the run is too heavy, use the safer daily-only
version:

```bash
docker compose run --rm --no-deps engine multitrade accelerated-validation \
  --workers 1 \
  --timeframes 1Day \
  --optimize \
  --force-all \
  --max-candidates 40
```

After the run finishes, open the dashboard and download:

```text
Management -> Data Export -> Download full analyst snapshot
```

Upload `multitrade-analyst-snapshot.json` to Codex for review.

Important: Strategy Lab now evaluates each strategy using its configured
allocation timeframe. A Retest strategy configured as `1Day` should therefore
produce `1Day` evidence instead of being silently tested on `5Min`.
