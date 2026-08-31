#!/bin/bash
# Create the Airflow connections and pool the demo DAGs need.
# Idempotent: safe to re-run (setup-demo.sh calls it on every bring-up).
set -uo pipefail

airflow db check --retry 30 --retry-delay 5

add_conn() {
  # $1 = conn_id, rest = args for `airflow connections add`
  local conn_id="$1"; shift
  airflow connections delete "$conn_id" >/dev/null 2>&1 || true
  airflow connections add "$conn_id" "$@"
}

# Gremlin Server.
#   - Service name `gremlin`, NOT host.docker.internal: that hairpins out through
#     the host's published port and does not exist at all on Linux Docker.
#   - `mylogin` / `mysecret` are GremlinHook's no-auth sentinels -- get_client()
#     treats those exact literals as "no credentials".
#   - A ws:// URI yields conn_type="ws", which makes GremlinHook.get_uri() take
#     its non-"gremlin" branch and build ws://gremlin:8182/gremlin. Intended.
add_conn 'gremlin' --conn-uri 'ws://mylogin:mysecret@gremlin:8182/gremlin'

# Gemini via pydantic-ai.
#   PydanticAIVertexHook reads api_key from `extra`, NOT from conn.password.
#   Omitting "vertexai" selects the Generative Language API (AI Studio) --
#   i.e. the free tier -- rather than Vertex AI.
#
#   MODEL PREFIX: pydantic-ai 2.x renamed these. It is `google:` for AI
#   Studio (what we want) and `google-cloud:` for Vertex. The older
#   `google-gla:` / `google-vertex:` names that most docs still show now
#   fail with "Unknown model".
#
#   MODEL: choose carefully -- free-tier quota varies enormously by model,
#   and the trap is a DAILY cap, not a per-minute one.
#     gemini-2.5-flash      retired for new keys, though ListModels still
#                           returns it ("no longer available to new users")
#     gemini-flash-latest   resolves to gemini-3.7-flash: 20 requests PER DAY.
#                           Exhausted by two runs of the demo. Do not use.
#     gemini-3.1-flash-lite what we use. Comfortably handles the demo, and
#                           being a pinned version rather than a floating
#                           alias it also keeps benchmark numbers
#                           attributable.
#   Override with GEMINI_MODEL if your key has different entitlements.
if [ -z "${GEMINI_API_KEY:-}" ]; then
  echo "WARNING: GEMINI_API_KEY is unset; creating gemini_default without a key." >&2
fi
add_conn 'gemini_default' \
  --conn-type 'pydanticai-vertex' \
  --conn-extra "{\"model\": \"google:${GEMINI_MODEL:-gemini-3.1-flash-lite}\", \"api_key\": \"${GEMINI_API_KEY:-}\"}"

# pgvector store (separate Postgres from the Airflow metadata DB).
add_conn 'vectordb' --conn-uri 'postgresql://vector:vector@vectordb:5432/vectordb'

# One slot, shared by every LLM task in every DAG, so concurrent mapped tasks
# cannot outrun the Gemini free-tier requests-per-minute cap.
# max_active_tis_per_dag alone is not enough -- it does not span DAGs.
airflow pools set gemini_free 1 "Gemini free-tier RPM throttle"

echo "Connections and pools ready."
