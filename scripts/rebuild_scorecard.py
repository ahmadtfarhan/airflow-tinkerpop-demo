#!/usr/bin/env python3
"""Rebuild the scorecard from XCom, without spending a single API call.

Each arm's answers are already in XCom from the benchmark run, so scoring
is pure local computation over data already on disk.

Run it inside the worker, where Airflow and the include package are importable:

    docker compose exec -T airflow-worker \\
        python /opt/airflow/scripts/rebuild_scorecard.py

    # only specific runs
    ... rebuild_scorecard.py --runs bench_123 smoke4_456

    # see what is available first
    ... rebuild_scorecard.py --list
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

sys.path.insert(0, "/opt/airflow/dags")

from airflow.models.xcom import XComModel  # noqa: E402
from airflow.utils.session import create_session  # noqa: E402

from include.paths import ALIASES, RESULTS  # noqa: E402
from include.scoring import aggregate, markdown_table, score_one  # noqa: E402

DAG_ID = "rag_benchmark"


def load_xcoms(run_ids: list[str] | None) -> dict[str, dict[str, Any]]:
    """Return {run_id: {task_id: value or {map_index: value}}}."""
    out: dict[str, dict[str, Any]] = {}
    with create_session() as session:
        query = session.query(XComModel).filter(
            XComModel.dag_id == DAG_ID, XComModel.key == "return_value"
        )
        if run_ids:
            query = query.filter(XComModel.run_id.in_(run_ids))
        for row in query.all():
            try:
                value = XComModel.deserialize_value(row)
            except Exception:
                continue
            bucket = out.setdefault(row.run_id, {})
            if row.map_index is None or row.map_index < 0:
                bucket[row.task_id] = value
            else:
                bucket.setdefault(row.task_id, {})[row.map_index] = value
    return out


def _ordered(mapped: Any) -> list[Any]:
    """Mapped XComs come back keyed by map_index; restore their order."""
    if isinstance(mapped, dict):
        return [mapped[k] for k in sorted(mapped)]
    return mapped or []


def rows_for_run(run_id: str, data: dict[str, Any], aliases: dict) -> list[dict]:
    questions = data.get("load_questions") or []
    if not questions:
        return []
    rows: list[dict] = []

    # graph_arm maps 1:1 over graph_prompts, which is questions in order.
    for i, answer in enumerate(_ordered(data.get("graph_arm"))):
        if i < len(questions) and answer is not None:
            row = score_one(questions[i], answer, aliases)
            row["arm"], row["run_id"] = "graph", run_id
            rows.append(row)

    # vector_synth maps over vector_retrieve; items carry their own question/top_k.
    items = data.get("vector_retrieve") or []
    primary_k = items[0]["top_k"] if items else None
    for i, answer in enumerate(_ordered(data.get("vector_synth"))):
        if i < len(items) and answer is not None:
            item = items[i]
            row = score_one(item["question"], answer, aliases)
            k = item["top_k"]
            row["arm"] = "vector" if k == primary_k else f"vector@k{k}"
            row["top_k"], row["run_id"] = k, run_id
            rows.append(row)

    # full_context_inputs is questions when the arm is enabled, else empty.
    fc_inputs = data.get("full_context_inputs")
    fc_questions = fc_inputs if isinstance(fc_inputs, list) else questions
    for i, answer in enumerate(_ordered(data.get("full_context"))):
        if i < len(fc_questions) and answer is not None:
            row = score_one(fc_questions[i], answer, aliases)
            row["arm"], row["run_id"] = "full_context", run_id
            rows.append(row)

    return rows


def render_chart(summary: dict) -> str | None:
    try:
        import matplotlib
    except ImportError:
        return None
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from include.scoring import BUCKET_ORDER

    arms = [a for a in ("graph", "vector", "full_context") if a in summary["arms"]]
    buckets = [b for b in BUCKET_ORDER
               if any(b in summary["arms"][a]["buckets"] for a in arms)]
    if not arms or not buckets:
        return None
    width = 0.8 / len(arms)

    fig, ax = plt.subplots(figsize=(9, 4.5))
    for i, arm in enumerate(arms):
        values = [summary["arms"][arm]["buckets"].get(b, {}).get("f1", 0.0) for b in buckets]
        ax.bar([x + i * width for x in range(len(buckets))], values, width=width, label=arm)
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", nargs="*", default=None,
                    help="Run ids to rebuild from. Default: every run found.")
    ap.add_argument("--list", action="store_true",
                    help="Show what each run can contribute, and change nothing.")
    args = ap.parse_args()

    xcoms = load_xcoms(args.runs)
    if not xcoms:
        print("No rag_benchmark XComs found. Nothing to rebuild from.")
        return 1

    aliases = json.loads(ALIASES.read_text())
    by_run = {run: rows_for_run(run, data, aliases) for run, data in sorted(xcoms.items())}

    if args.list:
        print(f"{'run':<52}{'rows':>6}  arms")
        for run, rows in by_run.items():
            arms = sorted({r["arm"] for r in rows})
            print(f"{run:<52}{len(rows):>6}  {', '.join(arms) or '-'}")
        return 0

    # Later runs win per (arm, question): a re-run refreshes rather than duplicates.
    merged: dict[tuple, dict] = {}
    for run, rows in by_run.items():
        for row in rows:
            merged[(row["arm"], row["question_id"])] = row
    rows = list(merged.values())
    if not rows:
        print("Found XComs but could not score any rows.")
        return 1

    summary = aggregate(rows)
    summary["table"] = markdown_table(summary)
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "scorecard.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, indent=1)
    )
    chart = render_chart(summary)

    print(f"Rebuilt {len(rows)} rows from {len(by_run)} run(s) — zero API calls.\n")
    for arm, payload in summary["arms"].items():
        print(f"  {arm:<15} macro-F1 {payload['macro_f1']:.2f}  "
              f"micro-F1 {payload['micro_f1']:.2f}  ({payload['questions']} questions)")
    print(f"\n{summary['table']}")
    print(f"\nwrote {RESULTS / 'scorecard.json'}" + (f" and {chart}" if chart else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
