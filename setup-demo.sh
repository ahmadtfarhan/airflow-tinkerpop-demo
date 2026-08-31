#!/bin/bash
# Bring the demo up from a cold `docker compose up -d`.
#   1. wait for Airflow
#   2. create connections + the Gemini throttle pool
#   3. load the seed knowledge graph into Gremlin (no LLM calls)
#
# Note: this loads data/seed/kg_edges.json -- the graph an LLM EXTRACTED from
# the corpus -- not gremlin-setup/crm_edges.csv.  The ground-truth CSV is the
# scoring key and must never reach Gremlin, or the benchmark is rigged.
set -euo pipefail
cd "$(dirname "$0")"

[ -f .env ] && set -a && . ./.env && set +a || true

compose() { docker compose "$@"; }

echo "Waiting for Airflow worker..."
until compose ps airflow-worker 2>/dev/null | grep -qE '(Up|running)'; do sleep 5; done

echo "Setting up Airflow connections and pools..."
compose exec -T -e GEMINI_API_KEY="${GEMINI_API_KEY:-}" airflow-worker \
  bash -c "/opt/airflow/config/setup_connections.sh"

# Deterministic and LLM-free, so it is cheap to always regenerate and it
# guarantees the corpus, gold edges and questions match this checkout.
echo "Generating corpus and question set..."
compose exec -T airflow-worker python /opt/airflow/gremlin-setup/generate_corpus.py
compose exec -T airflow-worker python /opt/airflow/gremlin-setup/build_questions.py

echo "Waiting for Gremlin Server..."
until compose ps gremlin 2>/dev/null | grep -qE '(Up|running)'; do sleep 5; done

# First run has no seed graph yet -- it comes from a real extraction run.
# Not having one is expected, not an error, so do not take the whole setup down
# with it.
echo "Loading seed knowledge graph..."
if ./scripts/reload_kg.sh; then
  GRAPH_READY=1
else
  GRAPH_READY=0
fi

echo
echo "Ready.  Airflow UI: http://localhost:8080  (airflow / airflow)"
if [ "$GRAPH_READY" -eq 0 ]; then
  echo
  echo "NEXT: there is no knowledge graph yet. Build one with"
  echo "  docker compose exec airflow-worker \\"
  echo "    airflow dags trigger kg_build --conf '{\"use_cached_extraction\": false}'"
fi
echo
echo "Run python scripts/demo_doctor.py before presenting."
