#!/usr/bin/env bash
set -Eeuo pipefail

project_directory="${1:-/opt/multitrade/app}"
cd "${project_directory}"

if [[ ! -f .env ]]; then
  echo "Missing ${project_directory}/.env; deployment stopped."
  exit 1
fi

chmod 600 .env

export MULTITRADE_BUILD_COMMIT
MULTITRADE_BUILD_COMMIT="$(git rev-parse --verify HEAD)"
if [[ ! "${MULTITRADE_BUILD_COMMIT}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Could not resolve a valid Git build revision."
  exit 1
fi
echo "BUILD_COMMIT=${MULTITRADE_BUILD_COMMIT}"

compose=(docker compose)
if grep -Eq '^DASHBOARD_DOMAIN=[A-Za-z0-9.-]+\.[A-Za-z]{2,}$' .env; then
  compose+=(--profile public-dashboard)
  echo "PUBLIC_DASHBOARD_PROFILE_ENABLED"
else
  echo "PUBLIC_DASHBOARD_PROFILE_DISABLED"
fi

"${compose[@]}" config --quiet
"${compose[@]}" build --pull \
  engine dashboard automation research strategy-lab asset-universe
"${compose[@]}" run --rm --no-deps engine multitrade doctor
"${compose[@]}" up -d --remove-orphans
"${compose[@]}" ps
