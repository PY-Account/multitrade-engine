#!/usr/bin/env bash
set -Eeuo pipefail

project_directory="${1:-/opt/multitrade/app}"
cd "${project_directory}"

if [[ ! -f .env ]]; then
  echo "Missing ${project_directory}/.env; deployment stopped."
  exit 1
fi

chmod 600 .env

compose=(docker compose)
if grep -Eq '^DASHBOARD_DOMAIN=[A-Za-z0-9.-]+\.[A-Za-z]{2,}$' .env; then
  compose+=(--profile public-dashboard)
  echo "PUBLIC_DASHBOARD_PROFILE_ENABLED"
else
  echo "PUBLIC_DASHBOARD_PROFILE_DISABLED"
fi

"${compose[@]}" config --quiet
"${compose[@]}" build --pull \
  engine dashboard automation research strategy-lab
"${compose[@]}" run --rm --no-deps engine multitrade doctor
"${compose[@]}" up -d --remove-orphans
"${compose[@]}" ps
