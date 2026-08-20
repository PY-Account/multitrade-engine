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

if docker compose version >/dev/null 2>&1; then
  compose=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  compose=(docker-compose)
else
  echo "Docker Compose is not available; deployment stopped."
  exit 1
fi

public_dashboard_enabled=false
if grep -Eq '^DASHBOARD_DOMAIN=[A-Za-z0-9.-]+\.[A-Za-z]{2,}$' .env; then
  compose+=(--profile public-dashboard)
  public_dashboard_enabled=true
  echo "PUBLIC_DASHBOARD_PROFILE_ENABLED"
else
  echo "PUBLIC_DASHBOARD_PROFILE_DISABLED"
fi

admin_agent_enabled=false
if grep -Eq '^ADMIN_AGENT_TOKEN=.{32,}$' .env; then
  compose+=(--profile admin)
  admin_agent_enabled=true
  echo "ADMIN_AGENT_PROFILE_ENABLED"
else
  echo "ADMIN_AGENT_PROFILE_DISABLED"
fi

running_inside_admin_agent=false
if [[ -n "${ADMIN_AGENT_WORKDIR:-}" && -n "${ADMIN_AGENT_PORT:-}" ]]; then
  running_inside_admin_agent=true
  echo "ADMIN_AGENT_SELF_UPDATE_CONTEXT_DETECTED"
fi

build_services=(
  engine
  dashboard
  automation
  research
  strategy-lab
  asset-universe
  option-evidence
)
if [[ "${admin_agent_enabled}" == true ]]; then
  build_services+=(admin-agent)
fi

runtime_services=(
  engine
  dashboard
  automation
  research
  strategy-lab
  asset-universe
  option-evidence
)
if [[ "${public_dashboard_enabled}" == true ]]; then
  runtime_services+=(caddy)
fi
if [[ "${admin_agent_enabled}" == true && "${running_inside_admin_agent}" == false ]]; then
  runtime_services+=(admin-agent)
fi

"${compose[@]}" config --quiet
"${compose[@]}" build --pull "${build_services[@]}"
"${compose[@]}" run --rm --no-deps engine multitrade doctor
"${compose[@]}" up -d --remove-orphans "${runtime_services[@]}"
if [[ "${admin_agent_enabled}" == true && "${running_inside_admin_agent}" == true ]]; then
  echo "ADMIN_AGENT_SELF_RECREATE_SKIPPED"
  echo "Admin Agent remains on its current container during dashboard-triggered updates to avoid killing the update process."
  echo "If a release specifically changes Admin Agent internals, run: docker compose --profile admin up -d --build --force-recreate admin-agent"
fi
"${compose[@]}" ps
