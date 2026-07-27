#!/usr/bin/env bash
set -Eeuo pipefail

project_directory="${1:-/opt/multitrade/app}"
cd "${project_directory}"

if [[ ! -f .env ]]; then
  echo "Missing ${project_directory}/.env; deployment stopped."
  exit 1
fi

chmod 600 .env
docker compose config --quiet
docker compose build --pull
docker compose run --rm --no-deps engine multitrade doctor
docker compose up -d --remove-orphans
docker compose ps
