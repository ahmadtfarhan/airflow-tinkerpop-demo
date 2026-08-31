#!/bin/bash
# Reload the extracted knowledge graph into Gremlin from the committed seed.
# Zero LLM calls, ~5s.
#
# TinkerGraph is in-memory, so this is the recovery path if the gremlin
# container is ever restarted mid-demo. It is also what setup-demo.sh uses to
# put a graph in place on a cold start.
set -euo pipefail
cd "$(dirname "$0")/.."

SEED="data/seed/kg_edges.json"

if [ ! -f "$SEED" ]; then
  cat >&2 <<'MSG'
No seed knowledge graph at data/seed/kg_edges.json.

That file is produced by a real extraction run and then committed -- it is
deliberately not generated from the ground-truth CSV, because that would make
the extraction-quality number the benchmark reports untrue.

To create it:

  docker compose exec airflow-worker \
    airflow dags trigger kg_build --conf '{"use_cached_extraction": false}'

  # once it succeeds:
  cp data/cache/kg_edges_run.json data/seed/kg_edges.json

See data/seed/README.md.
MSG
  exit 2
fi

docker compose exec -T airflow-worker \
  python /opt/airflow/gremlin-setup/load_kg.py --edges-file "/opt/airflow/$SEED"
