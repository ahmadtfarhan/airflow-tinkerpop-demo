# Seed artifacts — the offline fallback

These files are what make a free-tier live demo survivable. They are **produced
during prep and committed**, so that on demo day the graph and the vector store
can both be restored with zero API calls, and the live DAG only ever *queries*.

| File | Produced by | Restores |
|---|---|---|
| `kg_edges.json` | `kg_build` with `use_cached_extraction=false` | the knowledge graph, via `scripts/reload_kg.sh` |
| `initdb/01_chunks.sql` | `scripts/dump_vectors.sh` | the pgvector store on a cold `compose up` |
| `scorecard.json`, `scorecard.png` | `rag_benchmark` | the results slide |

## These are not committed yet

`kg_edges.json` must come from a **real extraction run** — it is the artifact
that backs the claim "an LLM built this graph by reading the tickets." Seeding
it from `gremlin-setup/crm_edges.csv` would make extraction look perfect and
would make the extraction-F1 number on the results slide a lie.

Produce it on the day you first have a working Gemini key:

```bash
# 1. real extraction, ~22 LLM calls
airflow dags trigger kg_build --conf '{"use_cached_extraction": false}'

# 2. verify_kg must pass its 0.95 edge-F1 gate; read the number, you will
#    quote it on stage
cat data/results/extraction_report.json

# 3. promote the run output to the committed seed
cp data/cache/kg_edges_run.json data/seed/kg_edges.json
git add data/seed/kg_edges.json
```

Until then `kg_build` falls back to the live extraction path automatically, and
`scripts/demo_doctor.py` reports the missing seed as **not ready to present**.
