# Airflow + Apache TinkerPop — knowledge graphs for AI

> Airflow Summit 2026 demo. Built on `apache-airflow-providers-apache-tinkerpop`,
> the provider I contributed to Apache Airflow.

Your RAG system can answer *"what tier is this account on?"*

It cannot answer *"if this account is compromised, what is exposed through its
network of linked accounts?"* — because that answer is not in any one document.
It is spread across twenty-eight of them, and finding it means **joining**, not
retrieving.

This repo builds both indexes from **the same corpus, in the same Airflow**, and
scores them against ground truth we control:

```
crm_corpus_generate ──[crm_corpus]──┬──> kg_build     ──[crm_knowledge_graph]
                                    └──> vector_build ──[crm_vector_index]

rag_benchmark      (manual — scores all three arms, ~430 requests)
rag_showdown_live  (manual — queries both, writes nothing)
```

The asset chain stops at the two builds on purpose. `rag_benchmark` used to be
scheduled on both assets, which meant every graph rebuild silently spent a
day's free-tier quota on a benchmark nobody asked for.

| Arm | How it answers |
|---|---|
| **graph** | `AgentOperator` + `HookToolset(GremlinHook)` — the LLM writes Gremlin and traverses, six queries per question |
| **vector** | pgvector cosine top-k → `@task.llm` synthesis |
| **full_context** | the entire corpus in one prompt, no retrieval |

Runs on **Airflow 3.3.1**. Everything else runs on **one free Google AI Studio key** — Gemini for generation,
`gemini-embedding-001` for embeddings — through the first-party
`apache-airflow-providers-common-ai` provider. No vendor SDK.

## Quickstart

Prerequisites: Docker Desktop, and a free [AI Studio API key](https://aistudio.google.com/apikey).

```bash
cp .env.example .env
echo "AIRFLOW_UID=$(id -u)" >> .env
# put your key in .env as GEMINI_API_KEY, and generate your own FERNET_KEY:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

docker compose up --build -d
./setup-demo.sh            # connections, pool, corpus, seed graph
python scripts/demo_doctor.py
```

Then open <http://localhost:8080> (`airflow` / `airflow`), unpause the
`summit26` DAGs, and trigger `crm_corpus_generate`. The asset chain builds the
knowledge graph and the vector index from there; the benchmark and the live
showdown are triggered by hand.

To run just the head-to-head on one question:

```bash
docker compose exec airflow-worker \
  airflow dags trigger rag_showdown_live --conf '{"question_id": "B3-01"}'
```

## Why the benchmark is honest

A graph-versus-vectors demo is trivially riggable, so the design choices that
stop it being a strawman are worth stating up front. The full argument is in
[docs/METHODOLOGY.md](docs/METHODOLOGY.md); the short version:

- **One atomic fact per document.** A relationship note names two accounts and
  never a resource; an access email names one account and one resource and
  never another account. So a three-hop answer genuinely requires joining 28
  documents instead of retrieving one.
- **`kg_build` never reads the ground-truth CSV.** The graph is built by an LLM
  reading the same tickets the vector store indexes.
- **The retrieval budget favours the vector arm.** One chunk is one document, so
  `top_k=25` means it sees roughly a tenth of the corpus in a single request,
  while the graph agent gets six queries and pays a request for each one.
- **There is a bucket vectors win outright** (B5: prose the schema does not
  model), and one where they tie on quality and win on cost and latency (B1).
- **A third arm stuffs the whole corpus into the context window**, because that
  is the first thing anyone sensible asks about a corpus this size.
- **Gold answers are known by construction**, so scoring is deterministic set
  precision/recall/F1 — not an LLM judge.
- **Extraction quality is reported, not hidden.** `verify_kg` scores the
  extracted graph against the gold edges and fails below 0.95 edge-F1.

## Layout

| Path | What |
|---|---|
| `dags/` | the five demo DAGs plus `crm_reachability` |
| `dags/include/` | schemas, scoring, pgvector I/O, the hook and operator subclasses |
| `gremlin-setup/` | deterministic graph, corpus and question generators |
| `scripts/demo_doctor.py` | pre-flight check — run it before presenting |
| `scripts/reload_kg.sh` | restore the graph in seconds, zero LLM calls |
| `scripts/archive_data.sh` | stash/restore generated data instead of deleting it |
| `data/seed/` | committed offline-fallback artifacts ([why](data/seed/README.md)) |
| `docs/METHODOLOGY.md` | the evaluation protocol |
| `docs/RUNSHEET.md` | the numbers to quote, and what to do when something goes red |

## Notes for anyone reproducing this

- **TinkerGraph is in-memory.** `graphLocation` persists it on a clean shutdown,
  but a hard kill loses the graph. `scripts/reload_kg.sh` restores it in a few
  seconds from the committed seed. This is why the live DAG only ever queries.
- **The vector store is a separate Postgres** (`vectordb`) from the Airflow
  metadata DB, which stays on `postgres:13`. Swapping a Postgres major version
  in place will not start — pg13 data directories are not readable by pg16 —
  and recovering means `docker compose down -v`. Airflow 3.3 still supports 13,
  so the metadata DB is left alone even though the official compose has moved
  to 16.
- **Pick the model by its DAILY quota, not its name.** `gemini-flash-latest`
  resolves to Gemini 3.7 Flash, which allows **20 requests per day** on the free
  tier — two runs of the demo and you are done for the day. This repo defaults
  to `gemini-3.1-flash-lite` (15 RPM / 500 RPD). Override with `GEMINI_MODEL`.
- **The Gemini free tier is the binding constraint.** Every LLM task runs in the
  one-slot `gemini_free` pool, extraction is batched ten documents at a time,
  and the agent tasks run `durable=True` so a retry replays cached steps instead
  of re-spending quota.

## What I learned about my own provider

Using it hard for this demo surfaced six gaps — five in the TinkerPop provider,
one in `common-ai`. The first three are the closing slide and my next PRs; the
last three only appear once an LLM is the one driving the hook:

1. **`GremlinOperator` exposes no `bindings` parameter**, so parameterised
   traversals are only reachable through the hook.
2. **`GremlinHook.run()` closes the client on every call** — great for a
   long-lived agent, which self-heals a dropped socket; bad for a bulk loader,
   where 100 vertices means 100 websocket handshakes.
3. **`GremlinHook.run()` is not agent-shaped.** `HookToolset` builds its tool
   schema by introspecting the signature, and `run`'s three unannotated optional
   parameters become permissive `{}` properties an LLM will happily fill with
   nonsense.
4. **`GremlinHook` cannot be called from an async agent context at all.**
   gremlinpython's synchronous `submit()` drives its own event loop, and
   `HookToolset.call_tool` is `async`, so asyncio refuses outright — in exactly
   the context `HookToolset` exists to create. The shim hands the call to a
   worker thread; the real fix is an async-native hook.
5. **A rejected traversal kills the task instead of reaching the model.** An LLM
   writing Gremlin will write invalid Gremlin, and the driver exception comes
   straight out of the tool call — so the model is never told, and every retry
   writes the same bad query again. `ModelRetry` is what pydantic-ai has for
   this, and the hook has to raise it.
6. **A usage limit is a hard task failure.** `UsageLimitExceeded` out of
   `AgentOperator` fails the task, which in a mapped benchmark takes the
   downstream scorer — and the whole sweep's results — with it. `durable=True`
   keeps its replay cache on failure, so every retry replays into the same wall.

Local workarounds live in [`dags/include/gremlin_ext.py`](dags/include/gremlin_ext.py)
(1–5) and [`dags/include/agent_ext.py`](dags/include/agent_ext.py) (6).

## Contributing

The provider lives in [apache/airflow](https://github.com/apache/airflow) under
`providers/apache/tinkerpop`. Issues and PRs welcome — tag `@ahmadtfarhan`.

Licensed under Apache 2.0.
