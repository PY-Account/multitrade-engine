#!/usr/bin/env bash
set -Eeuo pipefail

project_directory="${1:-/opt/multitrade/app}"
cd "${project_directory}"

if [[ ! -d .git ]]; then
  echo "Missing Git repository at ${project_directory}."
  exit 1
fi

tracked_changes="$(git status --porcelain --untracked-files=no)"
if [[ -n "${tracked_changes}" ]]; then
  if [[ "${tracked_changes}" == " M config/paper_portfolio.json" ]]; then
    mkdir -p local-backups
    backup_path="local-backups/paper_portfolio.before-update.$(date -u +%Y%m%dT%H%M%SZ).json"
    cp config/paper_portfolio.json "${backup_path}"
    git restore config/paper_portfolio.json
    echo "LOCAL_PORTFOLIO_CONFIG_BACKED_UP=${backup_path}"
  else
    echo "Tracked files have local changes; update stopped."
    git status --short
    exit 1
  fi
fi

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "Tracked files have local changes; update stopped."
  git status --short
  exit 1
fi

if [[ "$(git branch --show-current)" != "main" ]]; then
  echo "Expected the main branch; update stopped."
  exit 1
fi

previous_commit="$(git rev-parse HEAD)"
git fetch --prune origin main

if ! git merge-base --is-ancestor HEAD origin/main; then
  echo "Local main cannot fast-forward to origin/main; update stopped."
  exit 1
fi

git merge --ff-only origin/main
current_commit="$(git rev-parse HEAD)"

if grep -q '\${TRADING_ALPACA_OPTIONS_ACCOUNT_UUID}' config/paper_portfolio.json \
  && ! grep -Eq '^TRADING_ALPACA_OPTIONS_ACCOUNT_UUID=.+$' .env; then
  echo "Missing TRADING_ALPACA_OPTIONS_ACCOUNT_UUID in .env; update stopped."
  echo "Add the dedicated Alpaca Paper options account UUID to .env and rerun."
  exit 1
fi

bash ops/deploy.sh "${project_directory}"

echo "MULTITRADE_UPDATE_OK"
echo "previous_commit=${previous_commit}"
echo "current_commit=${current_commit}"
