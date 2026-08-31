# Run sheet — 25 minutes

Hard stop at **21:00** for Q&A. The single live moment is at **12:30**, exactly
halfway: late enough that the room has context, early enough to leave eight
minutes of recoverable material after it.

## Ten minutes before

```bash
python scripts/demo_doctor.py
```

Must print `SAFE TO PRESENT`. If the graph check fails: `./scripts/reload_kg.sh`
(five seconds, no LLM calls). If the Gemini check reports 429, swap to the
backup key before you walk on — not during.

**Do not `docker compose down`.** TinkerGraph is in-memory.

Pre-open these tabs, in this order, and never type a URL on stage:

1. Assets view
2. `kg_build` grid — latest run, green
3. `rag_showdown_live` trigger form, `question_id` already set to `B3-01`
4. `crm_reachability` (spare)

Browser at 125–150% zoom, light theme, bookmark bar hidden, 1280×720.

## The show

| Time | Screen | Beat |
|---|---|---|
| 00:00 | Slide 1 | "I wrote the TinkerPop provider for Airflow. Today I want to show you what I built with it." |
| 00:30 | Slide 2 | Two questions. **Pause after the second. Do not answer it.** |
| 01:30 | Slide 3 | The structural argument: the answer needs a join, not a lookup |
| 03:00 | Slide 4 | Provider journey — **2.5 min, hard**. Compress this first if behind |
| 05:00 | Airflow **Assets view** | "Same corpus. Two indexes. One orchestrator." |
| 08:00 | `kg_build` grid | 22 mapped tasks; click one, show the `ExtractedTriples` XCom. Say: *"it never saw the ground truth"* |
| 09:00 | Slide 7 | pgvector SQL. 30 seconds |
| 09:30 | Slide 8 | Methodology. **Fast and confident** — this is the skeptics' slide |
| 11:00 | Slide 9 | Scorecard. Walk B1→B5. Be explicit where vectors win. Land on B3 |
| **12:30** | **Trigger `rag_showdown_live`** | See below |
| 14:30 | Slide 11 | The traversal + "at k=50 it still cannot answer" |
| 16:00 | Slide 12 | The honest slide. **Do not skip to save time** |
| 18:00 | Slide 13 | **Six** provider gaps in two groups. Lead with the async one |
| 20:00 | Slide 14 | Resources, QR |
| 21:00 | — | Q&A |

Buffers: the 14:30, 16:00 and 18:00 blocks can each shed 30 seconds.

## The live trigger (12:30–14:30)

Trigger with config `{"question_id": "B3-01"}`. Four tasks, ~25–45 s, ~8 API
calls. You have two minutes budgeted for a forty-five second task.

While it runs, narrate — do not watch in silence:

> "It's picking the question… the graph agent is calling `describe_schema`
> first, then writing its own Gremlin… meanwhile the vector arm embeds the
> question and pulls its top five documents…"

Then, in order:

1. Open `graph_answer` logs → **show the Gremlin the agent wrote itself.** This
   is the moment of the talk.
2. Open `score_live` logs → gold answer, both predictions, both F1s.

### Fallback ladder

| If | Do |
|---|---|
| It's slow | Keep talking. You budgeted 2 min for a 45 s task |
| A task goes red | **Clear Task.** Say: *"durable execution — it replays the cached steps rather than re-calling the model."* Rehearse this line |
| Still red at 14:00 | *"Let me show you the one I ran this morning."* Click the previous successful run **in the same grid** — one click, same screen |
| Airflow or network is gone | Play the 60 s recording from the deck: *"…and here's one I ran earlier"* |

Never debug on stage.

## Numbers to have in your head

| | |
|---|---|
| Corpus | 215 documents, ~10k tokens |
| Graph | 70 accounts, 20 resources, 10 groups, 65 relationships |
| Clusters | **6**, sized 19 / 4 / 2 / 2 / 2 / 2 |
| Largest cluster | 19 accounts, **diameter 7** |
| Hub | Northwind Analytics (C059), 6 direct links |
| B3-01 answer | **7 resources**, assembled from **28 of 215 documents** |
| Compliance | **2 of 5** Churned accounts still hold Confidential access |
| Extraction | **22** LLM calls to *build*; **edge-F1 was 1.00**, gate is 0.95 |
| Why gate at 0.95 | errors compound per hop: 0.88^6 = 46% of six-hop chains survive |
| Demo-day extraction cost | **zero** — `kg_build` replays the committed seed |
| Retrieval budget | vector sweeps k = 5/15/25/50; graph agent **12 Gremlin queries** (13 model requests) |

## Questions you will get

**"Did the LLM see the ground truth?"**
No. `kg_build` reads only `docs.json`. `crm_edges.csv` is the scoring key and is
not opened anywhere in that DAG.

**"Why not put the whole corpus in the context window?"**
We did — that's the third arm. It beats vector RAG on the easy buckets, still
degrades on six-hop chaining, and costs 20–40× per query. This corpus is 10k
tokens; yours is 35 million. Graph retrieval is O(answer size); context
stuffing is O(corpus size).

**"Isn't top-k=5 unfairly low?"**
The benchmark sweeps k = 5, 15, 25 and 50. And on B4-05 at **k = 50** — fifty
of 215 documents, roughly a quarter of the corpus — and the vector arm returned
*"The provided documents do not contain information about any Churned accounts
that hold access to a Confidential resource."* Ten times the retrieval budget,
identical failure. It is not a budget problem: the answer is not in any document,
it is in the relationships between them.

**"What did extraction cost you in accuracy?"**
Quote the edge-F1 from `data/results/extraction_report.json`. The DAG fails
below 0.95, because at 88% recall a six-hop closure compounds into nonsense.

**"Would you use a graph for everything?"**
No — B5 is on the scorecard precisely because vectors win it outright. Hybrid.
