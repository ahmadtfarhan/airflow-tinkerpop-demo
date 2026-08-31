"""Head-to-head benchmark: graph vs vector vs full-context RAG.

Three arms, one output contract, one scoring function. A full sweep is several
hundred paid requests -- not for live demos, use rag_showdown_live instead.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from airflow.providers.common.ai.toolsets.hook import HookToolset
from airflow.sdk import Param, dag, task

from include.agent_ext import (
    GRAPH_QUERY_BUDGET,
    GRAPH_REQUEST_LIMIT,
    GRAPH_USAGE_LIMITS,
    BudgetedAgentOperator,
)
from include.gremlin_ext import DemoGremlinHook

from include.rag_prompts import GRAPH_SYSTEM_PROMPT, VECTOR_SYSTEM_PROMPT
from include.paths import (
    ALIASES,
    DOCS,
    GREMLIN_CONN,
    LLM_CONN,
    LLM_POOL,
    QUESTIONS,
    RESULTS,
)
from include.schemas import Answer

DOC_MD = f"""
### RAG benchmark

Every arm returns the same `Answer` model and goes through the same scorer.

| arm | how it answers |
|---|---|
| `graph` | `BudgetedAgentOperator` + `HookToolset(DemoGremlinHook)` -- the LLM writes Gremlin |
| `vector` | pgvector top-k -> `@task.llm` synthesis |
| `full_context` | the entire corpus in one prompt, no retrieval |

Budget parity: the graph agent may run **{GRAPH_QUERY_BUDGET} Gremlin queries**
({GRAPH_REQUEST_LIMIT} model requests, one of which writes the answer); the
vector arm sees `top_k` documents in a single request. An agent that exhausts
the budget returns an empty answer and scores as a miss -- it does not fail the
sweep.

Scored on set precision/recall/F1 against gold answers known by construction.
Headline is **macro-F1** across buckets; micro is reported too, and labelled.

**Manual trigger only.** A full sweep is up to ~774 requests, so it is not
asset-scheduled -- otherwise every `kg_build` run would silently launch a
several-hundred-request sweep unasked.

**Do not run this on demo day.** Use `rag_showdown_live`.
"""


@dag(
    dag_id="rag_benchmark",
    # Deliberately not asset-scheduled: an asset-triggered run can't take
    # params, so it would always launch the full several-hundred-request sweep.
    schedule=None,
    start_date=datetime(2026, 8, 1),
    catchup=False,
    max_active_runs=1,
    max_active_tasks=5,
    tags=["summit26", "benchmark", "llm"],
    doc_md=DOC_MD,
    params={
        "question_ids": Param([], type="array",
                              description="Empty means all questions."),
        "top_k_sweep": Param([5, 15, 25, 50], type="array",
                             description="Documents the vector arm may retrieve, per sweep point."),
        "arms": Param(["graph", "vector", "full_context"], type="array"),
        # Backstop against a runaway param (huge top_k_sweep/question_ids), not a quota fit.
        "max_requests": Param(
            2000, type="integer",
            description="Refuse to start if the estimated request count exceeds this. "
                        "Backstop against a runaway sweep, not a quota fit.",
        ),
    },
    default_args={
        "retries": 3,
        "retry_delay": timedelta(seconds=45),
        "retry_exponential_backoff": True,
        "max_retry_delay": timedelta(minutes=3),
    },
)
def rag_benchmark():
    @task
    def load_questions(**context) -> list[dict[str, Any]]:
        """Load the question set and refuse to start if it looks like a runaway sweep."""
        questions = json.loads(QUESTIONS.read_text())
        wanted = context["params"]["question_ids"]
        if wanted:
            questions = [q for q in questions if q["id"] in wanted]

        sweep = context["params"]["top_k_sweep"]
        arms = context["params"]["arms"]
        per_question = (
            (GRAPH_REQUEST_LIMIT if "graph" in arms else 0)
            + (len(sweep) if "vector" in arms else 0)
            + (1 if "full_context" in arms else 0)
        )
        estimate = per_question * len(questions)
        budget = int(context["params"]["max_requests"])
        print(f"{len(questions)} questions x {per_question} requests "
              f"= ~{estimate} generation requests (budget {budget})")
        if estimate > budget:
            raise ValueError(
                f"Estimated {estimate} requests exceeds max_requests={budget}. "
                f"Either narrow question_ids, shorten top_k_sweep (currently "
                f"{sweep}), drop an arm, or raise max_requests if that's intended."
            )
        return questions

    # Each arm gates itself on the `arms` param, returning [] when not wanted.

    # ---------------- graph arm ----------------
    @task
    def graph_prompts(questions: list[dict[str, Any]], **context) -> list[str]:
        if "graph" not in context["params"]["arms"]:
            return []
        return [q["question"] for q in questions]

    # BudgetedAgentOperator (not AgentOperator): turns "ran out of budget" into
    # an empty Answer instead of failing the mapped task. See include/agent_ext.py.
    graph_answers = BudgetedAgentOperator.partial(
        task_id="graph_arm",
        llm_conn_id=LLM_CONN,
        system_prompt=GRAPH_SYSTEM_PROMPT,
        output_type=Answer,
        toolsets=[
            HookToolset(
                DemoGremlinHook(conn_id=GREMLIN_CONN),
                allowed_methods=["run_query", "describe_schema"],
            )
        ],
        # request_limit is the quota knob, not tool_calls_limit -- Gremlin
        # queries are free. See include/agent_ext.py.
        usage_limits=GRAPH_USAGE_LIMITS,
        # retries here is pydantic-ai's ModelRetry count, not Airflow's.
        agent_params={"model_settings": {"temperature": 0.0}, "retries": 3},
        durable=True,
        retries=5,
        pool=LLM_POOL,
    )

    # ---------------- vector arm ----------------
    @task
    def vector_retrieve(questions: list[dict[str, Any]], **context) -> list[dict[str, Any]]:
        """One embedding call per question, then pure SQL."""
        from include.embedding import embed_query
        from include.pgvector_io import top_k as pg_top_k

        if "vector" not in context["params"]["arms"]:
            return []
        out = []
        for q in questions:
            vector = embed_query(q["question"])
            for k in context["params"]["top_k_sweep"]:
                chunks = pg_top_k(vector, k=int(k))
                out.append({"question": q, "top_k": int(k), "chunks": chunks})
        return out

    @task.llm(
        llm_conn_id=LLM_CONN,
        output_type=Answer,
        system_prompt=VECTOR_SYSTEM_PROMPT,
        agent_params={"model_settings": {"temperature": 0.0}},
        pool=LLM_POOL,
    )
    def vector_synth(item: dict[str, Any]) -> str:
        docs = "\n\n".join(
            f"[{c['doc_id']}] {c['text']}" for c in item["chunks"]
        )
        return f"Question: {item['question']['question']}\n\nRetrieved documents:\n{docs}"

    # ---------------- full-context arm ----------------
    @task
    def full_context_inputs(questions: list[dict[str, Any]], **context) -> list[dict[str, Any]]:
        return questions if "full_context" in context["params"]["arms"] else []

    @task.llm(
        llm_conn_id=LLM_CONN,
        output_type=Answer,
        system_prompt=VECTOR_SYSTEM_PROMPT,
        agent_params={"model_settings": {"temperature": 0.0}},
        pool=LLM_POOL,
    )
    def full_context(question: dict[str, Any]) -> str:
        # Built inside the task: the corpus must never travel through XCom.
        docs = json.loads(DOCS.read_text())
        corpus = "\n\n".join(f"[{d['doc_id']}] {d['text']}" for d in docs)
        return f"Question: {question['question']}\n\nAll documents:\n{corpus}"

    # ---------------- scoring ----------------
    # none_failed: a disabled arm is SKIPPED, and all_success would propagate
    # that skip and kill the scorecard.
    @task(trigger_rule="none_failed")
    def score(
        questions: list[dict[str, Any]],
        graph_out: list[Any],
        vector_items: list[dict[str, Any]],
        vector_out: list[Any],
        full_out: list[Any],
        **context,
    ) -> dict[str, Any]:
        from include.scoring import aggregate, markdown_table, score_one

        aliases = json.loads(ALIASES.read_text())
        rows: list[dict[str, Any]] = []
        # A skipped arm resolves to None, not an empty list.
        graph_out = graph_out or []
        vector_items = vector_items or []
        vector_out = vector_out or []
        full_out = full_out or []

        for q, ans in zip(questions, graph_out):
            row = score_one(q, ans, aliases)
            row["arm"] = "graph"
            rows.append(row)

        sweep = context["params"]["top_k_sweep"]
        primary_k = int(sweep[0])
        for item, ans in zip(vector_items, vector_out):
            row = score_one(item["question"], ans, aliases)
            row["arm"] = "vector" if item["top_k"] == primary_k else f"vector@k{item['top_k']}"
            row["top_k"] = item["top_k"]
            rows.append(row)

        for q, ans in zip(questions, full_out):
            row = score_one(q, ans, aliases)
            row["arm"] = "full_context"
            rows.append(row)

        # Merge with any previous run rather than overwriting it; rows are
        # replaced per (arm, question) so re-running an arm refreshes cleanly.
        RESULTS.mkdir(parents=True, exist_ok=True)
        scorecard = RESULTS / "scorecard.json"
        previous: list[dict[str, Any]] = []
        if scorecard.exists():
            try:
                previous = json.loads(scorecard.read_text()).get("rows", [])
            except (json.JSONDecodeError, AttributeError):
                previous = []

        fresh = {(r["arm"], r["question_id"]) for r in rows}
        merged = [r for r in previous if (r["arm"], r["question_id"]) not in fresh] + rows

        summary = aggregate(merged)
        summary["table"] = markdown_table(summary)
        scorecard.write_text(json.dumps({"summary": summary, "rows": merged}, indent=1))
        carried = len(merged) - len(rows)
        print(f"scored {len(rows)} rows this run, carried {carried} from previous runs; "
              f"arms now present: {sorted(summary['arms'])}")
        return summary

    @task(trigger_rule="none_failed")
    def render(summary: dict[str, Any]) -> str:
        """Grouped bar chart, F1 by bucket by arm."""
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        from include.scoring import BUCKET_ORDER

        arms = [a for a in ("graph", "vector", "full_context") if a in summary["arms"]]
        buckets = [b for b in BUCKET_ORDER
                   if any(b in summary["arms"][a]["buckets"] for a in arms)]
        width = 0.8 / max(len(arms), 1)

        fig, ax = plt.subplots(figsize=(9, 4.5))
        for i, arm in enumerate(arms):
            values = [summary["arms"][arm]["buckets"].get(b, {}).get("f1", 0.0)
                      for b in buckets]
            ax.bar([x + i * width for x in range(len(buckets))], values,
                   width=width, label=arm)
        ax.set_xticks([x + width * (len(arms) - 1) / 2 for x in range(len(buckets))])
        ax.set_xticklabels(buckets)
        ax.set_ylim(0, 1.0)
        ax.set_ylabel("F1")
        ax.set_title("Answer quality by question type")
        ax.legend()
        fig.tight_layout()
        out = RESULTS / "scorecard.png"
        fig.savefig(out, dpi=160)
        return str(out)

    questions = load_questions()
    # .output needed here: a classic operator's .partial().expand() returns
    # the MappedOperator itself, not an XComArg.
    graph_out = graph_answers.expand(prompt=graph_prompts(questions)).output
    vector_items = vector_retrieve(questions)
    vector_out = vector_synth.expand(item=vector_items)
    full_out = full_context.expand(question=full_context_inputs(questions))

    summary = score(questions, graph_out, vector_items, vector_out, full_out)
    render(summary)


rag_benchmark()
