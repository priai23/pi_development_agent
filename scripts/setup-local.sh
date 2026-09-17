#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

docker compose -f docker-compose.test.yml up -d --wait postgres

if [[ ! -x backend/.venv/bin/python ]]; then
  python3 -m venv backend/.venv
fi
backend/.venv/bin/python -m pip install --upgrade pip
backend/.venv/bin/pip install -e 'backend/[test]'

if [[ ! -f backend/.env ]]; then
  cp backend/.env.example backend/.env
fi
(
  cd backend
  DATABASE_URL='postgresql+psycopg://postgres:postgres@127.0.0.1:5432/erp_agent' \
  ENCRYPTION_KEY='MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTM0NDU2Nzg5MDE=' \
  .venv/bin/alembic upgrade heads
)
scripts/healthcheck.sh
