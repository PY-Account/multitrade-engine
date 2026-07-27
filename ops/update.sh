#!/usr/bin/env bash
set -Eeuo pipefail

project_directory="${1:-/opt/multitrade/app}"
cd "${project_directory}"

if [[ ! -d .git ]]; then
  echo "Missing Git repository at ${project_directory}."
  exit 1
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

bash ops/deploy.sh "${project_directory}"

echo "MULTITRADE_UPDATE_OK"
echo "previous_commit=${previous_commit}"
echo "current_commit=${current_commit}"
