"""The smallest useful thing: run a Gremlin query from an Airflow DAG.

    DAG triggers task
      -> hook opens a Gremlin connection
      -> query executes on the graph backend
      -> results and logs come back to Airflow

That is the whole provider in four lines of DAG code, and it is deliberately the
first thing in this repo worth showing. Everything else here -- LLM extraction,
agents writing their own traversals, a RAG benchmark -- is built on exactly this.

Two tasks, showing the two ways in:

* ``count_customers`` uses ``GremlinOperator``. One statement, no Python. This
  is the path most people want and the one the provider exists for.
* ``customers_by_tier`` uses ``GremlinHook`` inside ``@task``, because it does
  something with the result afterwards. Reach for this when the query is a
  means rather than the end.

Neither needs an LLM, a vector store, or any of the rest of it. If the graph is
loaded, this runs.
"""

from __future__ import annotations

from datetime import datetime

from airflow.providers.apache.tinkerpop.hooks.gremlin import GremlinHook
from airflow.providers.apache.tinkerpop.operators.gremlin import GremlinOperator
from airflow.sdk import dag, task

from include.paths import GREMLIN_CONN

DOC_MD = """
### Hello, Gremlin

The minimum: an Airflow task running a query against a graph backend.

```
DAG triggers task -> hook opens a connection -> query runs -> results in the log
```

`count_customers` is the operator: give it a `gremlin_conn_id` and a `query`.
Its result goes to **XCom**, not the log — click the XCom tab to see `[70]`.

`customers_by_tier` is the hook, for when the result feeds the next line of
Python — and it prints, so the answer is in the log.

Requires only the `gremlin` connection and a loaded graph.
"""


@dag(
    dag_id="gremlin_hello",
    schedule=None,
    start_date=datetime(2026, 8, 1),
    catchup=False,
    max_active_runs=1,
    tags=["summit26", "provider", "intro"],
    doc_md=DOC_MD,
)
def gremlin_hello():
    # The operator: query in, result out. No Python at all.
    #
    # Note when demoing: the result lands in XCom ([70]) but NOT in the task
    # log, so this task's log shows only the connection line. Click the XCom
    # tab, or use the hook below if you want the answer in the log. That is
    # finding number seven for the provider -- an operator whose whole job is
    # returning a value should say what it returned.
    count_customers = GremlinOperator(
        task_id="count_customers",
        gremlin_conn_id=GREMLIN_CONN,
        query="g.V().hasLabel('Customer').count()",
    )

    @task
    def customers_by_tier() -> dict[str, int]:
        """The hook, for when you want to do something with the result.

        ``GremlinHook.run()`` opens the connection, submits, and closes it
        again -- so this is the whole round trip in one line.
        """
        hook = GremlinHook(conn_id=GREMLIN_CONN)
        rows = hook.run("g.V().hasLabel('Customer').groupCount().by('tier')")

        # groupCount returns a single-element list holding the map.
        tiers = dict(rows[0]) if rows else {}
        for tier, n in sorted(tiers.items(), key=lambda kv: -kv[1]):
            print(f"  {tier:<12} {n}")
        return tiers

    count_customers >> customers_by_tier()


gremlin_hello()
