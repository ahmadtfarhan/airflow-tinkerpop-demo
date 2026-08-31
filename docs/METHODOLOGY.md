# Evaluation methodology

A "knowledge graphs beat vector RAG" demo is trivially riggable. Scatter the
facts a bit more, retrieve a bit less, pick only multi-hop questions, and the
graph wins by construction. This document is the counter-argument — what was
done to make the comparison survive a hostile question, and where vector RAG
legitimately wins.

## The corpus

215 documents, ~10k tokens, generated deterministically (`SEED = 42`) from a
known CRM graph: 70 accounts, 20 resources, 10 groups, 65 logical relationships.

The generating rule is the one that matters:

> **One atomic fact per document. No document ever states two facts that a
> multi-hop question needs to join.**

Concretely: a relationship note names exactly two accounts and never a resource.
An access email names one account and one resource and never a second account.
A group charter names a group and never its members.

This is not decoration. If "Northwind and Ironbark are the same org" and
"Ironbark can see the Revenue Report" appear in the same ticket, then a top-1
retriever answers a two-hop question correctly and the entire comparison is
theatre. The invariant is asserted in the generator's own tests:

- 0 documents assert more than one edge
- 0 edge documents name an entity type outside that relation's pair
- 0 documents mention an entity name they do not declare

Documents use human names ("Northwind Analytics", not "C059"). That forces a
real entity-normalisation step — which production extraction always has — and
stops the vector arm lexically shortcutting on an id string that appears
verbatim in every relevant chunk.

**50 of the 215 documents state no graph facts at all**: 40 distractors (billing
queries, latency complaints, password resets) and 10 near-misses. The
near-misses are the sharpest test in the corpus — they read like access grants
and retrieve like access grants, but the request was *denied*, so the correct
answer excludes them.

## The questions

43 questions across five buckets, with gold answers computed in pure Python from
the graph:

| Bucket | n | What it needs | Expected winner |
|---|---|---|---|
| B1 attribute lookup | 10 | one document | vector — ties on F1, wins on cost and latency |
| B2 two-hop join | 10 | two documents of different types | graph, narrowly |
| B3 transitive closure | 8 | 6–12 documents joined | graph, decisively |
| B4 global aggregation | 10 | up to the whole corpus | graph, decisively |
| B5 semantic / prose | 5 | text the schema does not model | **vector outright; graph F1 ≈ 0** |

A generation-time validator rejects degenerate questions: an empty gold set
(both arms "win" by saying nothing), a gold set larger than half the universe
(won by saying everything), or a multi-hop question answerable from a single
document. It caught three real defects during development, including that the
headline compliance question originally had an **empty** answer.

B5 exists specifically so the comparison is not a strawman. Graph traversal
cannot answer "which accounts complained about dashboard load times" at all —
there is no schema for it — and the scorecard shows that.

## Fairness controls

1. **Same corpus, byte-identical.** Both arms consume `docs.json` through the
   same `DocumentLoaderOperator` configuration. Critically, `kg_build` never
   opens `crm_edges.csv`: the graph is built by an LLM reading the same tickets
   the vector store indexes.
2. **Same model everywhere** — `google:gemini-3.1-flash-lite` for extraction,
   traversal and synthesis — at `temperature=0.0`.
3. **Same output contract.** Every arm returns the same `Answer` model and goes
   through the same scoring function. No arm gets a friendlier format.
4. **Same prompts, live and rehearsed.** Both DAGs import from
   `include/rag_prompts.py`, so the question answered on stage uses exactly the
   instructions the scorecard was produced with.
5. **The vector store embeds the document text and nothing else.** This one
   was a bug before it was a control. `DocumentLoaderOperator`'s
   `json_text_field` sweeps every non-text key into chunk metadata, and
   LlamaIndex embeds metadata *together with* the text — so the vectors would
   have contained each document's `asserts` (its structured ground-truth
   facts) and its `doc_type`, including the literal strings `near_miss` and
   `distractor_*`. The vector arm would have been able to identify the denied
   requests and the noise documents for free, and nothing in the results would
   have shown it. `vector_build.strip_metadata` reduces metadata to `doc_id`,
   which pgvector needs and which carries no meaning.
6. **A retrieval budget that favours vectors.** `chunk_size=1024` with ~35-word
   documents means **one chunk is one document**, so `top_k` is literally "this
   many documents". The sweep runs k = 5 and 25 — at k=25 the vector arm sees
   roughly a tenth of the corpus. (It ran k=50 originally; each extra sweep
   point costs one request per question, and the free tier allows 500 a day.) The graph agent is capped at
   `UsageLimits(request_limit=7, tool_calls_limit=12)` -- six rounds of Gremlin
   plus the request that writes the answer, and a tool ceiling that sits above
   that rather than under it. The tool ceiling is not a quota control: Gremlin
   queries go to the local server and cost nothing, so setting it *below* the
   request budget saves no quota and merely kills every question that needs one
   more query than it allows. Exhausting the budget is scored as a miss (an
   empty `Answer`), not raised as a task failure, so one greedy question cannot
   take a whole sweep's scorecard down with it.

The most useful result from that sweep is not which arm wins. It is how little
B3 recall moves between k=5 and k=50. Retrieving more does not help when the
answer was never in any single chunk.

## Scoring

Gold answers are known by construction, so scoring is deterministic rather than
judged.

For a gold set `G` and predicted set `P`, after normalising surface forms to
canonical ids:

```
precision = |G ∩ P| / |P|        recall = |G ∩ P| / |G|
F1        = 2PR / (P + R)        exact  = 1 if P == G else 0
```

Counts and single values are scored on exact match. **Numeric answers stay
strict**: the prediction must carry exactly the gold number and no other, so
"6 clusters" agrees with "6" while "about 6", "7", and "between 5 and 15" do
not, and an answer that volunteers a second number scores zero. **Non-numeric
answers are graded on whole-word containment** of every gold component — gold
"Enterprise, UK" is satisfied by "…is on the Enterprise tier in the UK region"
but not by an answer missing either part. Models write prose however firmly the
prompt asks for a bare value, and without this every single-fact question would
have scored zero for every arm — which would have made the easy bucket look
like a graph win when both arms in fact knew the answer. The ordered path
question is scored on exact sequence match plus a sequence-similarity score, so
returning the right accounts in the wrong order does not read as success.

Three deliberate choices:

- **Unresolvable predictions are kept, not dropped.** An invented entity is a
  false positive. Silently discarding names that fail to resolve flatters the
  model.
- **The headline is macro-F1 across buckets, not micro.** Micro lets the easy
  bucket dominate by having as many questions as the hard one. Both are
  reported and both are labelled.
- **No LLM judge for the primary number.** It would add noise and an attack
  surface to the one figure the argument rests on.

## Free-tier budget (measured, from AI Studio's rate-limit dashboard)

Quota varies enormously by model, and the trap is the **daily** cap:

| Model | RPM | RPD | Verdict |
|---|---|---|---|
| Gemini 3.7 Flash (what `gemini-flash-latest` resolves to) | 5 | **20** | unusable — two demo runs exhaust the day |
| Gemini 3 Flash | 5 | 20 | unusable |
| **Gemini 3.1 Flash Lite** | 15 | **500** | what this demo uses |
| Gemini Embedding 1 | 100 | 1000 | fine, with the disk cache |

Two consequences worth planning around:

* **A full `rag_benchmark` sweep is up to 430 requests** — 43 questions x
  (graph 7 + vector one per k-value + full-context 1) at the default two-point
  sweep, and the graph arm only spends all seven on the questions that need
  them. `load_questions` prices the run at that worst case before anything
  executes and refuses to start above `max_requests` (default 450), because
  discovering the cap two hundred requests in costs the rest of the day.
  Single arms are far cheaper (vector ~86, graph ~301), so the run can be
  split across evenings; set `arms` and a disabled arm creates zero task
  instances.
* **Resuming is Airflow-native.** Clear only the FAILED tasks — successful
  task instances are not re-run, so their requests are not re-spent.
* **Embedding is capped daily too.** Building the vector store is 215 requests.
  The content-hash cache means re-runs cost nothing, but regenerating the corpus
  invalidates it.

## What the graph costs

Reported alongside the results, because the graph is not free:

- **Extraction**: ~22 LLM calls over the corpus, in batches of ten.
- **Extraction quality**: `verify_kg` scores extracted edges against the gold
  list and **fails the DAG below 0.95 edge-F1**. This number is load-bearing —
  at 88% recall a six-hop closure compounds into something far worse, and the
  graph arm would lose B3 for reasons that have nothing to do with graphs.
- **Embedding**: 215 chunks, one batched pass.
- **Query time**: the graph agent makes several round trips per question; the
  vector arm makes one embedding call plus one synthesis call.

## Limitations, stated plainly

- **The corpus is small** — ~10k tokens, which is why the full-context arm is
  viable at all. The scaling argument is about complexity, not this corpus:
  graph retrieval is O(answer size), context stuffing is O(corpus size).
- **The data is synthetic**, generated from the graph it is meant to imply.
  Real corpora are messier, and extraction F1 on real text would be lower.
- **One compliance scenario is planted deliberately.** At `SEED = 42` no Churned
  account held Confidential access, and none sits inside the largest linked
  cluster, so the headline question had an empty answer. Two exposures are
  seeded — one direct, one inherited through a group — without touching the
  account-link structure. Documented in `_plant_compliance_scenario`.
- **One model, one embedding model.** Results may differ on other models.
- **Single run.** Answers are generated at `temperature=0.0` but not repeated
  across seeds, so small per-bucket differences should not be over-read.

## Reproducing

```bash
docker compose up --build -d && ./setup-demo.sh
airflow dags trigger crm_corpus_generate                              # deterministic
airflow dags trigger kg_build --conf '{"use_cached_extraction": false}'  # real extraction
airflow dags trigger rag_benchmark
```

Raw per-question results land in `data/results/scorecard.json`; the extraction
report is `data/results/extraction_report.json`.
