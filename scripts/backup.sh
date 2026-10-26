#!/usr/bin/env bash
# Dump the PostgreSQL database to backups/. Run from the project directory; schedule with cron.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && . ./.env; set +a
mkdir -p backups
out="backups/tablecall-$(date +%Y%m%d-%H%M%S).sql.gz"
docker compose exec -T db pg_dump -U "${POSTGRES_USER:-tablecall}" "${POSTGRES_DB:-tablecall}" | gzip > "$out"
echo "Wrote $out"
# Keep the 14 most recent dumps.
ls -1t backups/tablecall-*.sql.gz | tail -n +15 | xargs -r rm --
