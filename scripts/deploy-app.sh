#!/usr/bin/env bash
# Deploy origin/main as the installed app (docker-compose.app.yml, port 4332).
#
# Keeps a dedicated checkout, separate from any working tree, at the tip of
# origin/main; when that tip moves it rebuilds web/dist and restarts the app.
# Safe to run on a timer: it exits quickly when there is nothing new.
#
#   scripts/deploy-app.sh            deploy if origin/main has moved
#   FORCE=1 scripts/deploy-app.sh    rebuild and restart regardless
#
# Environment:
#   MACRO_ENGINE_APP_DIR   deployment checkout (default ~/deploy/macro-engine)
#   MACRO_ENGINE_APP_REF   ref to deploy (default origin/main)
#   MACRO_ENGINE_REPO_URL  clone URL (default: this repo's origin)
#
# The compose service reads MACRO_ENGINE_HEALTH_TOKEN from $APP_DIR/.env.
set -euo pipefail

# Everything runs inside main() so that updating this very file during the
# checkout cannot change the script mid-run.
main() {
  local app_dir="${MACRO_ENGINE_APP_DIR:-$HOME/deploy/macro-engine}"
  local ref="${MACRO_ENGINE_APP_REF:-origin/main}"
  local repo_url="${MACRO_ENGINE_REPO_URL:-}"
  local here
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  if [ -z "$repo_url" ]; then
    repo_url="$(git -C "$here" remote get-url origin)"
  fi

  if [ ! -d "$app_dir/.git" ]; then
    echo "deploy: cloning $repo_url into $app_dir"
    mkdir -p "$(dirname "$app_dir")"
    git clone --quiet "$repo_url" "$app_dir"
  fi
  cd "$app_dir"

  exec 9>"$app_dir/.git/deploy.lock"
  if ! flock -n 9; then
    echo "deploy: another deploy is running"
    return 0
  fi

  git fetch --quiet --prune origin
  local want have
  want="$(git rev-parse --verify --quiet "$ref^{commit}")" || {
    echo "deploy: $ref not found" >&2
    return 1
  }
  have="$(cat .git/deployed-sha 2>/dev/null || true)"
  if [ "$want" = "$have" ] && [ -z "${FORCE:-}" ]; then
    return 0
  fi
  if ! git cat-file -e "$want:docker-compose.app.yml" 2>/dev/null; then
    echo "deploy: $ref (${want:0:7}) has no docker-compose.app.yml yet; leaving the current deployment"
    return 0
  fi

  echo "deploy: ${have:0:7} -> ${want:0:7} ($ref)"
  git checkout --quiet --force --detach "$want"

  # Build into dist.next and swap, so a failed or in-progress build never
  # replaces the bundle being served. Runs as the container's root, which
  # also owns the previous dist.
  docker run --rm \
    -v "$app_dir/web:/app" \
    -v macro-engine-app_node_modules:/app/node_modules \
    -w /app node:22-alpine sh -euc '
      npm ci --no-audit --no-fund --silent
      rm -rf dist.next
      npx tsc -b
      npx vite build --outDir dist.next --emptyOutDir --logLevel warn
      rm -rf dist.prev
      if [ -d dist ]; then mv dist dist.prev; fi
      mv dist.next dist
      rm -rf dist.prev
    '

  docker compose -f docker-compose.app.yml up -d --force-recreate --remove-orphans app

  local i
  for i in $(seq 1 30); do
    if curl -fsS -o /dev/null http://127.0.0.1:4332/api/day \
      && curl -fsS -o /dev/null http://127.0.0.1:4332/; then
      echo "$want" > .git/deployed-sha
      echo "deploy: ${want:0:7} is live on :4332"
      return 0
    fi
    sleep 1
  done
  echo "deploy: app did not come up on :4332; will retry next run" >&2
  return 1
}

main "$@"
exit $?
