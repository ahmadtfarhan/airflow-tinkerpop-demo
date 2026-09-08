# Airflow + Apache TinkerPop — knowledge graphs for AI

> Airflow Summit 2026 demo. Built on `apache-airflow-providers-apache-tinkerpop`,
> the provider I contributed to Apache Airflow.

Your RAG system can answer *"what tier is this account on?"* It cannot answer
*"if this account is compromised, what is exposed through its network of
linked accounts?"* — that answer is spread across twenty-eight documents, and
finding it means **joining**, not retrieving.

This repo builds a knowledge graph and a vector index from **the same corpus,
in the same Airflow**, and scores both against ground truth we control:

```
crm_corpus_generate ──[crm_corpus]──┬──> kg_build     ──[crm_knowledge_graph]
                                    └──> vector_build ──[crm_vector_index]

rag_benchmark      (manual — scores all three arms, ~430 requests)
rag_showdown_live  (manual — queries both, writes nothing)
```

The asset chain stops at the two builds on purpose — `rag_benchmark` used to
run on every rebuild and silently burned a day's free-tier quota.

| Arm | How it answers |
|---|---|
| **graph** | `AgentOperator` + `HookToolset(GremlinHook)` — the LLM writes Gremlin and traverses, six queries per question |
| **vector** | pgvector cosine top-k → `@task.llm` synthesis |
| **full_context** | the entire corpus in one prompt, no retrieval |

Runs on **Airflow 3.3.1**, one free Google AI Studio key (Gemini generation +
`gemini-embedding-001` embeddings) through `apache-airflow-providers-common-ai`.
No vendor SDK.

## Quickstart

Prerequisites: Docker Desktop, a free [AI Studio API key](https://aistudio.google.com/apikey).

```bash
cp .env.example .env
echo "AIRFLOW_UID=$(id -u)" >> .env
# put your key in .env as GEMINI_API_KEY, and generate your own FERNET_KEY:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

docker compose up --build -d
./setup-demo.sh            # connections, pool, corpus, seed graph
python scripts/demo_doctor.py
```

Open <http://localhost:8080> (`airflow` / `airflow`), unpause the `summit26`
DAGs, and trigger `crm_corpus_generate`. The asset chain builds the graph and
vector index from there; benchmark and live showdown are triggered by hand:

```bash
docker compose exec airflow-worker \
  airflow dags trigger rag_showdown_live --conf '{"question_id": "B3-01"}'
```

## Why the benchmark is honest

Full protocol in [docs/METHODOLOGY.md](docs/METHODOLOGY.md); the short version:

- **One atomic fact per document** — a three-hop answer genuinely requires
  joining 28 documents, not retrieving one.
- **`kg_build` never reads the ground-truth CSV** — the graph is built by an
  LLM reading the same tickets the vector store indexes.
- **The retrieval budget favours the vector arm** — `top_k=25` sees roughly a
  tenth of the corpus in one request; the graph agent pays per query, six of
  them.
- **Vectors win where they should** — outright on B5 (prose the schema
  doesn't model), on cost and latency on B1.
- **A third arm stuffs the whole corpus into context**, the obvious baseline.
- **Gold answers are known by construction** — scoring is deterministic set
  precision/recall/F1, not an LLM judge.
- **Extraction quality is reported, not hidden** — `verify_kg` fails below
  0.95 edge-F1 against gold edges.

## Layout

| Path | What |
|---|---|
| `dags/` | the five demo DAGs plus `crm_reachability` |
| `dags/include/` | schemas, scoring, pgvector I/O, hook/operator subclasses |
| `gremlin-setup/` | deterministic graph, corpus and question generators |
| `scripts/demo_doctor.py` | pre-flight check — run before presenting |
| `scripts/reload_kg.sh` | restore the graph in seconds, zero LLM calls |
| `scripts/archive_data.sh` | stash/restore generated data instead of deleting it |
| `data/seed/` | committed offline-fallback artifacts ([why](data/seed/README.md)) |
| `docs/METHODOLOGY.md` | the evaluation protocol |
| `docs/RUNSHEET.md` | the numbers to quote, and what to do when something goes red |

## Notes for anyone reproducing this

- **TinkerGraph is in-memory.** A hard kill loses the graph; `scripts/reload_kg.sh`
  restores it in seconds from the committed seed. Don't `docker compose down`.
- **The vector store is a separate Postgres** (`vectordb`) from the Airflow
  metadata DB (`postgres:13`, kept for Airflow 3.3 compatibility — swapping
  major versions in place breaks and requires `docker compose down -v`).
- **Pick the model by daily quota, not name.** `gemini-flash-latest` allows
  only 20 requests/day free tier. This repo defaults to `gemini-3.1-flash-lite`
  (15 RPM / 500 RPD). Override with `GEMINI_MODEL`.
- **Gemini free tier is the binding constraint** — every LLM task runs in the
  one-slot `gemini_free` pool, extraction batches ten documents at a time, and
  agent tasks run `durable=True` so retries replay cached steps instead of
  re-spending quota.

## What I learned about my own provider

Six gaps surfaced using it hard for this demo — five in the TinkerPop
provider, one in `common-ai`:

1. **`GremlinOperator` exposes no `bindings` parameter** — parameterised
   traversals are only reachable through the hook.
2. **`GremlinHook.run()` closes the client on every call** — fine for a
   self-healing agent, bad for a bulk loader (100 vertices = 100 handshakes).
3. **`GremlinHook.run()` isn't agent-shaped** — its unannotated optional
   params become permissive `{}` properties an LLM fills with nonsense.
4. **`GremlinHook` can't be called from an async agent context** —
   gremlinpython's synchronous `submit()` drives its own event loop, and
   `HookToolset.call_tool` is `async`. The shim hands it to a worker thread.
5. **A rejected traversal kills the task instead of reaching the model** — the
   driver exception comes straight out of the tool call, so every retry
   writes the same bad query. Needs `ModelRetry`.
6. **A usage limit is a hard task failure** — `UsageLimitExceeded` from
   `AgentOperator` takes the downstream scorer, and the whole sweep, with it.
   `durable=True` at least keeps the replay cache across the failure.

Workarounds: [`dags/include/gremlin_ext.py`](dags/include/gremlin_ext.py)
(1–5), [`dags/include/agent_ext.py`](dags/include/agent_ext.py) (6).

## Contributing

Provider lives in [apache/airflow](https://github.com/apache/airflow) under
`providers/apache/tinkerpop`. Issues and PRs welcome — tag `@ahmadtfarhan`.

Licensed under Apache 2.0.
