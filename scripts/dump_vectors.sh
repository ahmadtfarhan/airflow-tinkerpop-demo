#!/bin/bash
# Dump the pgvector table so a cold `docker compose up` restores a populated
# vector store with zero embedding API calls.  Written into the vectordb
# container's initdb.d, which only runs on a fresh volume.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/seed/initdb
docker compose exec -T vectordb \
  pg_dump -U vector -d vectordb --table=crm_chunks --data-only --column-inserts \
  > data/seed/initdb/02_chunks.sql
cat > data/seed/initdb/01_extension.sql <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
SQL
echo "Wrote data/seed/initdb/ ($(wc -l < data/seed/initdb/02_chunks.sql) lines)"
