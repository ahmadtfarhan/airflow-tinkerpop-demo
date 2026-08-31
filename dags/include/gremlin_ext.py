"""A thin GremlinHook subclass with an agent-friendly, narrowly-typed query
surface, a schema-description helper, and a batching loader for bulk writes.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable, List, Sequence, Tuple, TypeVar

from airflow.providers.apache.tinkerpop.hooks.gremlin import GremlinHook
from gremlin_python.driver.protocol import GremlinServerError
from pydantic_ai.exceptions import ModelRetry

T = TypeVar("T")


def _off_event_loop(fn: Callable[[], T]) -> T:
    """Run a blocking gremlinpython call on a worker thread.

    gremlinpython's synchronous ``Client.submit()`` drives its own event loop
    internally, which raises ``RuntimeError: Cannot run the event loop while
    another loop is running`` if called directly from async pydantic-ai tool
    dispatch. A worker thread gives it a clean loop to run in.
    """
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(fn).result()


class DemoGremlinHook(GremlinHook):
    """GremlinHook with an agent-friendly surface and a batching loader."""

    def run_query(self, query: str) -> list:
        """Run a read-only Gremlin traversal and return the results.

        :param query: A Gremlin traversal starting with ``g.``, for example
            ``g.V().hasLabel('Customer').has('id','C059').values('tier')``.
            Vertices carry an ``id`` property ('C001', 'R005', 'G003'); all
            edges have the label ``RELATED_TO`` and are distinguished by their
            ``relation_type`` property.
        """
        # Log the query and a summary of what came back, at INFO.
        #
        # The provider's LoggingToolset logs tool ARGS at debug and the result
        # not at all, so at default log level the task shows "Tool run_query
        # returned in 0.03s" and nothing about what was asked or answered.
        # This is the one place that sees both, and on stage it is the whole
        # point: the Gremlin here is what the model wrote by itself.
        self.log.info("GREMLIN  %s", query)
        try:
            rows = _off_event_loop(lambda: self.run(query))
            preview = ", ".join(str(r) for r in rows[:5])
            self.log.info(
                "RESULT   %d row(s)%s", len(rows),
                f": {preview}" + (" ..." if len(rows) > 5 else "") if rows else "",
            )
            return rows
        except GremlinServerError as exc:
            # ModelRetry sends the error back to the model as a tool result it can
            # correct. Only server-side query errors are caught here; connection
            # failures still raise and go through the normal Airflow retry.
            raise ModelRetry(
                f"The Gremlin server rejected that traversal: {exc}\n"
                f"Rewrite it and try again. Common causes: a step that does not "
                f"exist on this server (use TextP.containing('x') for substring "
                f"matching, not textContains), `__` chained onto an existing "
                f"traversal rather than starting an anonymous one, or a property "
                f"name the schema does not have."
            ) from exc

    def describe_schema(self) -> str:
        """Return the graph schema: vertex labels, their properties, and the relation types.

        Call this once before writing traversals. It costs one round trip and
        saves guessing at property names.
        """
        labels = _off_event_loop(
            lambda: self.run("g.V().groupCount().by(label())"))
        relations = _off_event_loop(
            lambda: self.run("g.E().values('relation_type').groupCount()"))
        return (
            "Vertex labels and counts: {labels}\n"
            "All edges have label RELATED_TO. Counts by relation_type: {relations}\n"
            "\n"
            "Vertex properties:\n"
            "  Customer: id, name, region (UK|EU|US|MENA), tier (Free|Pro|Enterprise), "
            "status (Active|Churned|Prospect)\n"
            "  Resource: id, name, resource_type, sensitivity (Public|Internal|Confidential)\n"
            "  Group:    id, name, role (Admin|Analyst|Viewer|Support)\n"
            "\n"
            "Edge relation_type values:\n"
            "  CUSTOMER_LINK  Customer <-> Customer (stored both ways)\n"
            "  ACCESS         Customer <-> Resource, property: permission\n"
            "  MEMBER         Customer <-> Group\n"
            "  GRANTS         Group <-> Resource, property: permission\n"
            "\n"
            "Because edges are stored in both directions, traverse with bothE()/otherV() "
            "and use simplePath() inside repeat() to avoid cycles."
        ).format(labels=labels, relations=relations)

    def run_many(
        self,
        statements: Sequence[Tuple[str, dict]],
        chunk_log_every: int = 25,
    ) -> int:
        """Execute many parameterised statements over ONE client connection.

        :param statements: ``(query, bindings)`` pairs. Bindings keep values out
            of the query string, which is both safer and lets the server cache
            the compiled traversal.
        :param chunk_log_every: Emit a progress line every N statements.
        :return: The number of statements executed.
        """
        client = self.get_conn()
        try:
            for i, (query, bindings) in enumerate(statements, start=1):
                client.submit(message=query, bindings=bindings).all().result()
                if chunk_log_every and i % chunk_log_every == 0:
                    self.log.info("executed %d statements", i)
            return len(statements)
        finally:
            # run() would normally close the client; bypassed here, so close it ourselves.
            client.close()
            self.client = None


# Parameterised loaders: values travel as bindings, never interpolated into the query string.
VERTEX_QUERY = (
    "g.V().has(vlabel, 'id', vid).fold()"
    ".coalesce(unfold(), addV(vlabel).property('id', vid))"
    ".property('name', vname)"
)

EDGE_QUERY = (
    "g.V().has('id', a).as('a').V().has('id', b).as('b')"
    ".coalesce("
    "  __.select('a').outE('RELATED_TO').where(inV().as('b')).has('relation_type', rt),"
    "  __.addE('RELATED_TO').from('a').to('b').property('relation_type', rt)"
    ")"
)


def vertex_statements(entities: Iterable[dict]) -> List[Tuple[str, dict]]:
    """Upsert statements for vertices, with their properties as bindings."""
    out: List[Tuple[str, dict]] = []
    for e in entities:
        query = VERTEX_QUERY
        bindings: dict[str, Any] = {
            "vlabel": e["label"],
            "vid": e["id"],
            "vname": e.get("name", e["id"]),
        }
        for i, (k, v) in enumerate(sorted((e.get("props") or {}).items())):
            query += f".property(pk{i}, pv{i})"
            bindings[f"pk{i}"] = k
            bindings[f"pv{i}"] = v
        out.append((query, bindings))
    return out


def edge_statements(edges: Iterable[dict]) -> List[Tuple[str, dict]]:
    """Upsert statements for edges, written both ways to match the source graph."""
    out: List[Tuple[str, dict]] = []
    for e in edges:
        a, b, rt = e["a"], e["b"], e["relation_type"]
        extra = {k: v for k, v in (e.get("props") or {}).items()
                 if k != "relation_type" and v is not None}
        for src, dst in ((a, b), (b, a)):
            query = EDGE_QUERY
            bindings: dict[str, Any] = {"a": src, "b": dst, "rt": rt}
            for i, (k, v) in enumerate(sorted(extra.items())):
                query += f".property(ek{i}, ev{i})"
                bindings[f"ek{i}"] = k
                bindings[f"ev{i}"] = v
            out.append((query, bindings))
    return out
