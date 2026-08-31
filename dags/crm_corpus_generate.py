"""Generate the corpus, the gold edges and the question set.

Deterministic and LLM-free. Head of the asset chain: downstream DAGs key off
Asset("crm_corpus").
"""

from __future__ import annotations

import json
import sys
from datetime import datetime

from airflow.sdk import Asset, dag, task

from include.paths import CORPUS, GREMLIN_SETUP

DOC_MD = """
### Corpus generation

Turns the deterministic CRM graph into ~215 support tickets, account notes and
access emails that **imply** the graph without ever tabulating it.

The rule that makes the benchmark honest: **one atomic fact per document**. No
document states two facts that a multi-hop question needs to join, so a 3-hop
answer genuinely requires reading ~15 documents rather than retrieving one.

Also emits the gold answer sets, and fails if any question is degenerate.
"""


@dag(
    dag_id="crm_corpus_generate",
    schedule=None,
    start_date=datetime(2026, 8, 1),
    catchup=False,
    max_active_runs=1,
    tags=["summit26", "corpus"],
    doc_md=DOC_MD,
)
def crm_corpus_generate():
    @task
    def gen_graph() -> dict:
        """Write the ground-truth CSVs. This graph never reaches Gremlin."""
        sys.path.insert(0, str(GREMLIN_SETUP))
        from generate_crm_data import build_graph, write_csvs

        vertices, edges = build_graph()
        write_csvs(vertices, edges, GREMLIN_SETUP)
        return {"vertices": len(vertices), "edges": len(edges)}

    @task
    def gen_corpus() -> dict:
        sys.path.insert(0, str(GREMLIN_SETUP))
        from generate_corpus import generate

        return generate(CORPUS)

    @task
    def gen_questions() -> dict:
        sys.path.insert(0, str(GREMLIN_SETUP))
        from build_questions import Graph, build

        entities = json.loads((CORPUS / "entities.json").read_text())
        edges = json.loads((CORPUS / "gold_edges.json").read_text())
        docs = json.loads((CORPUS / "docs.json").read_text())
        questions = build(Graph(entities, edges), docs)
        (CORPUS / "questions.json").write_text(json.dumps(questions, indent=1))
        counts: dict[str, int] = {}
        for q in questions:
            counts[q["bucket"]] = counts.get(q["bucket"], 0) + 1
        return {"total": len(questions), "by_bucket": counts}

    @task(outlets=[Asset("crm_corpus")])
    def validate() -> dict:
        """Gate on degenerate questions (empty gold set, near-universal gold set, or a multi-hop question answerable from one document)."""
        sys.path.insert(0, str(GREMLIN_SETUP))
        from build_questions import validate as validate_questions

        questions = json.loads((CORPUS / "questions.json").read_text())
        docs = json.loads((CORPUS / "docs.json").read_text())
        problems = validate_questions(questions, docs)
        if problems:
            raise ValueError("Degenerate questions:\n  " + "\n  ".join(problems))
        return {"questions": len(questions), "documents": len(docs), "status": "validated"}

    gen_graph() >> gen_corpus() >> gen_questions() >> validate()


crm_corpus_generate()
