"""Build the knowledge graph from the corpus with an LLM, then load it into Gremlin.

Never reads the ground-truth edges (crm_edges.csv) -- only Asset("crm_corpus").
Extraction is mapped over batches of documents rather than the whole corpus at once.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from airflow.providers.apache.tinkerpop.operators.gremlin import GremlinOperator
from airflow.sdk import Asset, Param, dag, task

from include.schemas import ExtractedTriples
from include.paths import (
    ALIASES,
    CACHE,
    CORPUS,
    DOCS,
    ENTITIES,
    GOLD_EDGES,
    GREMLIN_CONN,
    LLM_CONN,
    LLM_POOL,
    SEED_KG_EDGES,
)

BATCH_SIZE = 10
EXTRACTION_F1_GATE = 0.95

SYSTEM_PROMPT = """\
You extract a knowledge graph from CRM support documents.

Return ONLY relationships and attributes that the text explicitly states.

Critical rules:
- A DENIED, refused, rejected or withdrawn access request is NOT an ACCESS
  relationship. Return no triple for it.
- Two accounts described as linked, related, connected, referred, part of the
  same organisation or sharing a buying committee => CUSTOMER_LINK.
- An account granted access to a resource => ACCESS.
- An account joining or belonging to a group => MEMBER.
- A group granted access to a resource at group level => GRANTS.
- Many documents (billing queries, latency complaints, password resets,
  onboarding notes) state no relationship at all. Returning an empty list for
  those is correct and expected. Do not invent relationships to fill the schema.
- Use entity names exactly as they appear in the document.
"""

DOC_MD = """
### Knowledge graph build

An LLM reads the corpus in batches of 10 and emits typed triples; the triples
are normalised to canonical ids in plain Python (no second LLM pass), checked
against the gold edge list, and loaded into Gremlin with parameterised queries.

**This DAG never reads `crm_edges.csv`.** Its only input is the corpus.

`use_cached_extraction` (default **true**) replays the committed extraction, so
a demo-day run costs zero API calls.
"""


@dag(
    dag_id="kg_build",
    schedule=[Asset("crm_corpus")],
    start_date=datetime(2026, 8, 1),
    catchup=False,
    max_active_runs=1,
    max_active_tasks=1,
    tags=["summit26", "graph", "llm"],
    doc_md=DOC_MD,
    params={
        "use_cached_extraction": Param(
            True,
            type="boolean",
            description="Replay data/seed/kg_edges.json instead of calling the LLM.",
        )
    },
    default_args={
        "retries": 5,
        "retry_delay": timedelta(seconds=20),
        "retry_exponential_backoff": True,
        "max_retry_delay": timedelta(minutes=2),
    },
)
def kg_build():
    @task.branch
    def decide_extraction(**context) -> str:
        """Choose the extraction path before spending anything on it (must be a branch, not a downstream switch, or the LLM calls run anyway)."""
        if context["params"]["use_cached_extraction"] and SEED_KG_EDGES.exists():
            return "use_seed"
        return "batch_docs"

    @task
    def use_seed() -> dict[str, Any]:
        """Replay the committed extraction. Zero LLM calls."""
        edges = json.loads(SEED_KG_EDGES.read_text())
        return {"edges": edges, "source": "seed", "count": len(edges)}

    @task
    def batch_docs() -> list[dict[str, Any]]:
        """Batch documents and key the cache by content hash."""
        docs = json.loads(DOCS.read_text())
        batches = []
        for i in range(0, len(docs), BATCH_SIZE):
            chunk = docs[i:i + BATCH_SIZE]
            payload = "\n\n".join(f"[{d['doc_id']}] {d['text']}" for d in chunk)
            batches.append(
                {
                    "batch_id": f"BATCH-{i // BATCH_SIZE:03d}",
                    "doc_ids": [d["doc_id"] for d in chunk],
                    "payload": payload,
                    "hash": hashlib.sha256(payload.encode()).hexdigest()[:16],
                }
            )
        return batches

    # output_type must be a real class at module scope for XCom deserialization.
    @task.llm(
        llm_conn_id=LLM_CONN,
        output_type=ExtractedTriples,
        system_prompt=SYSTEM_PROMPT,
        pool=LLM_POOL,
        agent_params={"model_settings": {"temperature": 0.0}},
    )
    def extract(batch: dict[str, Any]) -> str:
        return (
            "Extract every relationship and entity attribute stated in these "
            "documents.\n\n" + batch["payload"]
        )

    @task
    def normalize(extractions: list[Any], batches: list[dict[str, Any]]) -> dict[str, Any]:
        """Resolve surface names to canonical ids and dedupe. No LLM here."""
        from include.scoring import normalize as norm

        aliases = json.loads(ALIASES.read_text())
        entities = json.loads(ENTITIES.read_text())
        label = {k: v["label"] for k, v in entities.items()}

        allowed = {
            "CUSTOMER_LINK": ("Customer", "Customer"),
            "ACCESS": ("Customer", "Resource"),
            "MEMBER": ("Customer", "Group"),
            "GRANTS": ("Group", "Resource"),
        }

        edges: dict[tuple, dict[str, Any]] = {}
        dropped = 0
        for item in extractions:
            payload = item if isinstance(item, dict) else item.model_dump()
            for t in payload.get("triples", []):
                a = norm([t["subject"]], aliases)[0]
                b = norm([t["obj"]], aliases)[0]
                rel = t["relation"]
                if a not in label or b not in label:
                    dropped += 1
                    continue
                want = allowed[rel]
                if (label[a], label[b]) == want[::-1]:
                    a, b = b, a
                if (label[a], label[b]) != want:
                    dropped += 1  # relation impossible between these labels
                    continue
                key = (rel, a, b) if rel != "CUSTOMER_LINK" else (rel, *sorted((a, b)))
                props = {"relation_type": rel}
                if t.get("permission"):
                    props["permission"] = t["permission"]
                edges.setdefault(key, {"relation_type": rel, "a": key[1], "b": key[2],
                                       "props": props})

        result = sorted(edges.values(), key=lambda e: (e["relation_type"], e["a"], e["b"]))
        CACHE.mkdir(parents=True, exist_ok=True)
        (CACHE / "kg_edges_run.json").write_text(json.dumps(result, indent=1))
        return {"edges": result, "source": "llm", "count": len(result),
                "dropped": dropped, "batches": len(batches)}

    @task(trigger_rule="none_failed_min_one_success")
    def choose_edges(from_llm: dict[str, Any] | None,
                     from_seed: dict[str, Any] | None) -> dict[str, Any]:
        """Join the two branches. Exactly one of them will have run."""
        chosen = from_seed or from_llm
        if not chosen or not chosen.get("edges"):
            raise ValueError(
                "Neither extraction branch produced edges. If "
                "use_cached_extraction is true, data/seed/kg_edges.json must exist."
            )
        edges = chosen["edges"]
        return {
            "edges": edges,
            "source": chosen.get("source", "seed" if from_seed else "llm"),
            "count": chosen.get("count", len(edges)),
        }

    @task
    def verify_kg(chosen: dict[str, Any]) -> dict[str, Any]:
        """Score the extracted graph against the gold edges, and gate on it."""
        gold = json.loads(GOLD_EDGES.read_text())
        gold_keys = {
            (e["relation_type"], *(sorted((e["a"], e["b"]))
                                   if e["relation_type"] == "CUSTOMER_LINK"
                                   else (e["a"], e["b"])))
            for e in gold
        }
        pred_keys = {
            (e["relation_type"], *(sorted((e["a"], e["b"]))
                                   if e["relation_type"] == "CUSTOMER_LINK"
                                   else (e["a"], e["b"])))
            for e in chosen["edges"]
        }
        tp = len(gold_keys & pred_keys)
        precision = tp / len(pred_keys) if pred_keys else 0.0
        recall = tp / len(gold_keys) if gold_keys else 0.0
        f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
        report = {
            "source": chosen["source"],
            "gold_edges": len(gold_keys),
            "extracted_edges": len(pred_keys),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "missed": sorted(str(k) for k in list(gold_keys - pred_keys)[:10]),
            "spurious": sorted(str(k) for k in list(pred_keys - gold_keys)[:10]),
        }
        (CORPUS.parent / "results").mkdir(parents=True, exist_ok=True)
        (CORPUS.parent / "results" / "extraction_report.json").write_text(
            json.dumps(report, indent=1)
        )
        if f1 < EXTRACTION_F1_GATE:
            raise ValueError(
                f"Extraction edge-F1 {f1:.3f} is below the {EXTRACTION_F1_GATE} gate. "
                f"Missed: {report['missed']} Spurious: {report['spurious']}"
            )
        return report

    reset_graph = GremlinOperator(
        task_id="reset_graph",
        gremlin_conn_id=GREMLIN_CONN,
        query="g.V().drop()",
        doc_md="Drops all vertices and edges.",
    )

    @task
    def load_graph(chosen: dict[str, Any]) -> dict[str, Any]:
        """Load vertices and edges over a single connection, with bindings."""
        from include.gremlin_ext import DemoGremlinHook, edge_statements, vertex_statements

        entities = json.loads(ENTITIES.read_text())
        used = {e["a"] for e in chosen["edges"]} | {e["b"] for e in chosen["edges"]}
        # Loads every entity, including ones with no edges.
        payload = list(entities.values())

        hook = DemoGremlinHook(conn_id=GREMLIN_CONN)
        n_v = hook.run_many(vertex_statements(payload))
        n_e = hook.run_many(edge_statements(chosen["edges"]))
        return {"vertices": n_v, "edge_statements": n_e, "connected_entities": len(used)}

    graph_stats = GremlinOperator(
        task_id="graph_stats",
        gremlin_conn_id=GREMLIN_CONN,
        query="g.V().groupCount().by(label())",
        doc_md="Vertex counts by label.",
    )

    decision = decide_extraction()
    seeded = use_seed()
    batches = batch_docs()
    extracted = normalize(extract.expand(batch=batches), batches)

    decision >> [seeded, batches]

    chosen = choose_edges(extracted, seeded)
    verify_kg(chosen) >> reset_graph >> load_graph(chosen) >> graph_stats


kg_build()
