"""Act 1: what's actually in the graph?

Before any traversal cleverness, look at the raw data. Every vertex and every
edge, exactly as Gremlin returns them -- pulled with ``elementMap()`` and
printed one row per line so the shape of the graph is legible straight from
the task logs, no XCom clicking required.

Three vertex tasks (one per label, so the logs read as three short tables
instead of one long shuffled one) plus one edge task. Nothing is filtered,
joined, or interpreted -- that starts in Act 2.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from airflow.providers.apache.tinkerpop.hooks.gremlin import GremlinHook
from airflow.sdk import dag, task

from include.paths import GREMLIN_CONN

DOC_MD = """
### Raw graph dump

> Act 1: look at the data before you traverse it.

Pulls every vertex (`elementMap()`, one task per label: Customer, Resource,
Group) and every edge (`RELATED_TO`, with its `relation_type`), and prints
each element as one pretty-printed JSON line in the task log.

No filtering, no aggregation -- this is the raw material every later DAG in
this repo builds on.
"""

VERTEX_LABELS = ["Customer", "Resource", "Group"]


def _clean(value: Any) -> Any:
    """elementMap() keys T.id/T.label (and nested endpoint vertices on edges)
    as enum members, which json.dumps rejects as dict keys; stringify them
    recursively for clean JSON.
    """
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def _dump(hook: GremlinHook, query: str, heading: str) -> list[dict[str, Any]]:
    rows = [_clean(row) for row in hook.run(query)]
    print(f"{heading}  ({len(rows)} row(s))")
    print("-" * len(f"{heading}  ({len(rows)} row(s))"))
    for row in rows:
        print(json.dumps(row, indent=2, default=str))
    print()
    return rows


@dag(
    dag_id="graph_dump_raw",
    schedule=None,
    start_date=datetime(2026, 8, 1),
    catchup=False,
    max_active_runs=1,
    tags=["summit26", "graph", "act1"],
    doc_md=DOC_MD,
)
def graph_dump_raw():
    @task
    def dump_vertices(label: str) -> list[dict[str, Any]]:
        hook = GremlinHook(conn_id=GREMLIN_CONN)
        return _dump(
            hook,
            f"g.V().hasLabel('{label}').elementMap()",
            f"{label} vertices",
        )

    @task
    def dump_edges() -> list[dict[str, Any]]:
        hook = GremlinHook(conn_id=GREMLIN_CONN)
        return _dump(
            hook,
            "g.E().hasLabel('RELATED_TO').elementMap()",
            "RELATED_TO edges",
        )

    dump_vertices.expand(label=VERTEX_LABELS)
    dump_edges()


graph_dump_raw()
