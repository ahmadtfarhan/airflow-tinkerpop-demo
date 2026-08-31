#!/usr/bin/env python3
"""Load the extracted knowledge graph into Gremlin from a JSON edge list.

Since TinkerGraph is in-memory, this restores it after a container restart
with zero LLM calls. Uses parameterised queries (bindings, not interpolation).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from gremlin_python.driver.client import Client

REPO = Path(__file__).resolve().parent.parent
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="ws://gremlin:8182/gremlin")
    ap.add_argument("--traversal", default="g")
    ap.add_argument("--edges-file", default=str(REPO / "data" / "seed" / "kg_edges.json"))
    ap.add_argument("--entities-file",
                    default=str(REPO / "data" / "corpus" / "entities.json"))
    ap.add_argument("--no-drop", action="store_true",
                    help="Merge into the existing graph instead of replacing it.")
    args = ap.parse_args()

    edges_path, entities_path = Path(args.edges_file), Path(args.entities_file)
    for path in (edges_path, entities_path):
        if not path.exists():
            print(f"ERROR: {path} not found.", file=sys.stderr)
            return 1

    edges = json.loads(edges_path.read_text())
    entities = json.loads(entities_path.read_text())

    client = Client(args.host, args.traversal)
    try:
        if not args.no_drop:
            client.submit("g.V().drop()").all().result()

        for e in entities.values():
            query, bindings = VERTEX_QUERY, {
                "vlabel": e["label"], "vid": e["id"], "vname": e.get("name", e["id"]),
            }
            for i, (k, v) in enumerate(sorted((e.get("props") or {}).items())):
                query += f".property(pk{i}, pv{i})"
                bindings[f"pk{i}"], bindings[f"pv{i}"] = k, v
            client.submit(message=query, bindings=bindings).all().result()

        for e in edges:
            extra = {k: v for k, v in (e.get("props") or {}).items()
                     if k != "relation_type" and v is not None}
            for src, dst in ((e["a"], e["b"]), (e["b"], e["a"])):
                query, bindings = EDGE_QUERY, {"a": src, "b": dst,
                                               "rt": e["relation_type"]}
                for i, (k, v) in enumerate(sorted(extra.items())):
                    query += f".property(ek{i}, ev{i})"
                    bindings[f"ek{i}"], bindings[f"ev{i}"] = k, v
                client.submit(message=query, bindings=bindings).all().result()

        counts = client.submit("g.V().groupCount().by(label())").all().result()
        print(f"Loaded {len(entities)} vertices and {len(edges)} edges. {counts}")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
