#!/usr/bin/env bash
# Validates that all required environment variables are present and non-empty.
# Usage:  ./scripts/validate-env.sh [env-file]
#
# Exit codes:
#   0 — all required vars set
#   1 — one or more required vars missing

set -euo pipefail

ENV_FILE="${1:-.env}"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a; source "$ENV_FILE"; set +a
fi

REQUIRED_VARS=(
  DATABASE_URL
  SYNC_DATABASE_URL
  REDIS_URL
  CELERY_BROKER_URL
  CELERY_RESULT_BACKEND
  GFW_API_TOKEN
  GFW_API_KEY
  AISSTREAM_API_KEY
)

MISSING=()

for var in "${REQUIRED_VARS[@]}"; do
  value="${!var:-}"
  if [[ -z "$value" ]] || [[ "$value" == REPLACE_WITH_* ]] || [[ "$value" == your_* ]]; then
    MISSING+=("$var")
  fi
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
  echo ""
  echo "  [validate-env] ERROR: the following required variables are not set:"
  echo ""
  for var in "${MISSING[@]}"; do
    echo "    - $var"
  done
  echo ""
  echo "  Copy .env.dev to .env and fill in real values, or set them in your shell."
  echo ""
  exit 1
fi

echo "  [validate-env] OK — all required environment variables are set."
