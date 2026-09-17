#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

database_url="${DATABASE_URL:-postgresql+psycopg://postgres:postgres@127.0.0.1:5432/erp_agent}"
encryption_key="${ENCRYPTION_KEY:-MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTIzNDU2Nzg5MDE=}"

(cd backend && DATABASE_URL="$database_url" ENCRYPTION_KEY="$encryption_key" \
  .venv/bin/python -c 'from database import engine; print(engine.connect().exec_driver_sql("SELECT 1").scalar())')
