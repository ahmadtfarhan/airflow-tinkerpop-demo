"""Live head-to-head: graph vs vector, on one question.

Re-extracts and re-embeds nothing except the question itself -- the graph and
vector store are built beforehand, so this only queries.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from typing import Any

from airflow.providers.common.ai.toolsets.hook import HookToolset
from airflow.sdk import Param, dag, task

from include.agent_ext import GRAPH_USAGE_LIMITS, BudgetedAgentOperator
from include.gremlin_ext import DemoGremlinHook
from include.paths import ALIASES, DOCS, GREMLIN_CONN, LLM_CONN, LLM_POOL, QUESTIONS
from include.rag_prompts import GRAPH_SYSTEM_PROMPT, VECTOR_SYSTEM_PROMPT
from include.schemas import Answer

DOC_MD = """
### Live showdown

Pick one question; both arms answer it; both are scored against the gold
answer. Nothing is extracted or re-embedded -- this only queries.

Default is **B3-01**, the three-hop reachability question that needs roughly
fifteen documents joined together.

If something goes red: **Clear Task**. `durable=True` replays the cached steps.
"""


@dag(
    dag_id="rag_showdown_live",
    schedule=None,
    start_date=datetime(2026, 8, 1),
    catchup=False,
    max_active_runs=1,
    tags=["summit26", "live"],
    doc_md=DOC_MD,
    params={
        "question_id": Param("B3-01", type="string",
                             description="Which benchmark question to run."),
        "top_k": Param(5, type="integer",
                       description="Documents the vector arm may retrieve."),
    },
    default_args={"retries": 3, "retry_delay": timedelta(seconds=45),
                  "retry_exponential_backoff": True,
                  "max_retry_delay": timedelta(minutes=3)},
)
def rag_showdown_live():
    @task
    def pick_question(**context) -> dict[str, Any]:
        qid = context["params"]["question_id"]
        questions = json.loads(QUESTIONS.read_text())
        match = next((q for q in questions if q["id"] == qid), None)
        if match is None:
            raise ValueError(
                f"Unknown question {qid!r}. Available: {[q['id'] for q in questions]}"
            )
        return match

    # BudgetedAgentOperator: an agent that runs out of queries returns its
    # partial answer instead of failing.
    graph_answer = BudgetedAgentOperator(
        task_id="graph_answer",
        prompt="{{ ti.xcom_pull(task_ids='pick_question')['question'] }}",
        llm_conn_id=LLM_CONN,
        system_prompt=GRAPH_SYSTEM_PROMPT,
        output_type=Answer,
        toolsets=[
            HookToolset(
                DemoGremlinHook(conn_id=GREMLIN_CONN),
                allowed_methods=["run_query", "describe_schema"],
            )
        ],
        # Same budget the benchmark used, imported rather than retyped.
        usage_limits=GRAPH_USAGE_LIMITS,
        # retries here is pydantic-ai's, not Airflow's.
        agent_params={"model_settings": {"temperature": 0.0}, "retries": 3},
        durable=True,
        pool=LLM_POOL,
        doc_md="The agent writes its own Gremlin queries; see logs for the traversal.",
    )

    @task
    def vector_retrieve(question: dict[str, Any], **context) -> dict[str, Any]:
        from include.embedding import embed_query
        from include.pgvector_io import top_k as pg_top_k

        k = int(context["params"]["top_k"])
        started = time.monotonic()
        chunks = pg_top_k(embed_query(question["question"]), k=k)

        # Print what retrieval actually returned, so the vector arm's answer is
        # explainable rather than just a number. On B3-01 these come back as
        # five relationship documents -- which name accounts and no resources
        # at all, and that is exactly why it cannot answer.
        import logging
        log = logging.getLogger("airflow.task")
        # doc_type comes from the corpus, not pgvector: strip_metadata keeps it
        # out of the index so retrieval cannot spot "near_miss" or
        # "distractor_*" and identify the traps for free. Here it is only being
        # printed, and it is the most legible part of the output -- five hits
        # all reading "relationship" says why the answer is missing far faster
        # than five text previews do.
        doc_type = {d["doc_id"]: d["doc_type"] for d in json.loads(DOCS.read_text())}
        log.info("RETRIEVED top-%d for: %s", k, question["question"])
        for c in chunks:
            log.info("  %-9s %.3f  %-18s %s", c["doc_id"], c["score"],
                     doc_type.get(c["doc_id"], "?"), c["text"][:78])

        counts: dict[str, int] = {}
        for c in chunks:
            key = doc_type.get(c["doc_id"], "?")
            counts[key] = counts.get(key, 0) + 1
        log.info("BY TYPE  %s", dict(sorted(counts.items(), key=lambda kv: -kv[1])))
        return {
            "question": question,
            "top_k": k,
            "chunks": chunks,
            "retrieve_seconds": round(time.monotonic() - started, 2),
        }

    @task.llm(
        llm_conn_id=LLM_CONN,
        output_type=Answer,
        system_prompt=VECTOR_SYSTEM_PROMPT,
        agent_params={"model_settings": {"temperature": 0.0}},
        pool=LLM_POOL,
    )
    def vector_answer(retrieved: dict[str, Any]) -> str:
        docs = "\n\n".join(f"[{c['doc_id']}] {c['text']}" for c in retrieved["chunks"])
        return (
            f"Question: {retrieved['question']['question']}\n\n"
            f"Retrieved documents:\n{docs}"
        )

    @task
    def score_live(
        question: dict[str, Any],
        graph_out: Any,
        retrieved: dict[str, Any],
        vector_out: Any,
    ) -> dict[str, Any]:
        """Print the gold answer next to both predictions."""
        from include.scoring import score_one

        aliases = json.loads(ALIASES.read_text())
        graph_row = score_one(question, graph_out, aliases)
        vector_row = score_one(question, vector_out, aliases)

        print(f"\nQUESTION  {question['id']}  [{question['bucket']}]")
        print(f"  {question['question']}")
        print(f"\nGOLD      {question['gold_entity_ids'] or question['gold_scalar']}")
        print(f"\nGRAPH     {graph_row['predicted'] or graph_row['predicted_scalar']}")
        print(f"          F1={graph_row['f1']:.2f}  exact={graph_row['exact']:.0f}")
        print(f"\nVECTOR    (top-{retrieved['top_k']}) "
              f"{vector_row['predicted'] or vector_row['predicted_scalar']}")
        print(f"          F1={vector_row['f1']:.2f}  exact={vector_row['exact']:.0f}")
        print(f"\nretrieved documents: {[c['doc_id'] for c in retrieved['chunks']]}")

        return {
            "question_id": question["id"],
            "bucket": question["bucket"],
            "gold": question["gold_entity_ids"] or question["gold_scalar"],
            "graph": {"answer": graph_row["predicted"] or graph_row["predicted_scalar"],
                      "f1": round(graph_row["f1"], 3)},
            "vector": {"answer": vector_row["predicted"] or vector_row["predicted_scalar"],
                       "f1": round(vector_row["f1"], 3),
                       "top_k": retrieved["top_k"]},
        }

    question = pick_question()
    question >> graph_answer
    retrieved = vector_retrieve(question)
    score_live(question, graph_answer.output, retrieved, vector_answer(retrieved))


rag_showdown_live()
