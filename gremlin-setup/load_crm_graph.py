#!/usr/bin/env python3
import argparse
import csv
from gremlin_python.driver.client import Client
from gremlin_python.process.graph_traversal import __  # anonymous traversals


def quote_id(v_id):
    """Wrap string IDs in quotes, leave numerics alone."""
    return f"'{v_id}'" if isinstance(v_id, str) else str(v_id)


def escape_prop(val: str) -> str:
    """Escape single-quotes for embedding in Gremlin strings."""
    return (val or "").replace("'", "\\'")


def parse_kv_props(props_str: str) -> dict:
    """
    Parse "k=v;k=v" into dict. Values are treated as strings unless they look numeric/bool.
    Example: "tier=Pro;strength=3;active=true" -> {"tier":"Pro","strength":3,"active":True}
    """
    props_str = (props_str or "").strip()
    if not props_str:
        return {}

    out = {}
    for kv in props_str.split(";"):
        kv = kv.strip()
        if not kv or "=" not in kv:
            continue
        k, v = kv.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            continue

        # basic type coercion (kept simple for demo)
        vl = v.lower()
        if vl in ("true", "false"):
            out[k] = (vl == "true")
        else:
            # int?
            try:
                out[k] = int(v)
                continue
            except ValueError:
                pass
            # float?
            try:
                out[k] = float(v)
                continue
            except ValueError:
                pass
            out[k] = v

    return out


def gremlin_prop_chain(props: dict) -> str:
    """
    Convert a dict into a Gremlin ".property(k, v).property(...)" chain.
    Strings are quoted/escaped, numbers/bools are unquoted.
    """
    parts = []
    for k, v in props.items():
        k_esc = escape_prop(str(k))
        if isinstance(v, bool):
            v_str = "true" if v else "false"
        elif isinstance(v, (int, float)):
            v_str = str(v)
        else:
            v_str = f"'{escape_prop(str(v))}'"
        parts.append(f".property('{k_esc}', {v_str})")
    return "".join(parts)


def load_graph(host, traversal, vertices_file, edges_file, drop_first=True):
    client = Client(host, traversal_source=traversal)

    # 1) Clear out any existing data
    if drop_first:
        print("⏳ Dropping all vertices…")
        client.submit("g.V().drop()").all().result()

    # 2) Load vertices, caching server-assigned IDs
    print(f"⏳ Loading vertices from {vertices_file}…")
    id_map = {}  # (label, csv_id) -> server_id

    with open(vertices_file, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            csv_id = row["id"]
            label = row.get("label") or row.get("type")  # allow either column name
            name = escape_prop(row.get("name", ""))

            if not label:
                raise ValueError("Vertex row missing 'label' (or 'type')")

            # props from "props" field like "tier=Pro;region=UK"
            extra_props = parse_kv_props(row.get("props", ""))

            # build gremlin: addV(label).property('id', 'C001').property('name','...')...id()
            gremlin = (
                f"g.addV('{escape_prop(label)}')"
                f".property('id', {quote_id(csv_id)})"
                f".property('name', '{name}')"
                f"{gremlin_prop_chain(extra_props)}"
                f".id()"
            )

            server_id = client.submit(gremlin).all().result()[0]
            id_map[(label, csv_id)] = server_id

    # 3) Load edges using cached server IDs
    print(f"⏳ Loading edges from {edges_file}…")
    with open(edges_file, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out_csv_id = row.get("out_id") or row.get("source")
            in_csv_id = row.get("in_id") or row.get("target")
            edge_label = row.get("label") or row.get("type")

            if not out_csv_id or not in_csv_id or not edge_label:
                raise ValueError("Edge row missing required columns (out_id/in_id/label)")

            out_label = row.get("out_label")  # optional (if you want to disambiguate)
            in_label = row.get("in_label")    # optional

            # If labels aren't provided, we resolve by looking up any matching key.
            # For CRM datasets with unique IDs (Cxxx/Rxxx/Gxxx), you can skip labels safely.
            if out_label:
                out_server_id = id_map[(out_label, out_csv_id)]
            else:
                # find first match among known labels
                matches = [sid for (lbl, cid), sid in id_map.items() if cid == out_csv_id]
                if not matches:
                    raise KeyError(f"Could not resolve out_id={out_csv_id}")
                out_server_id = matches[0]

            if in_label:
                in_server_id = id_map[(in_label, in_csv_id)]
            else:
                matches = [sid for (lbl, cid), sid in id_map.items() if cid == in_csv_id]
                if not matches:
                    raise KeyError(f"Could not resolve in_id={in_csv_id}")
                in_server_id = matches[0]

            edge_props = parse_kv_props(row.get("props", ""))
            prop_chain = gremlin_prop_chain(edge_props)

            gremlin = (
                f"g.V({quote_id(out_server_id)})"
                f".addE('{escape_prop(edge_label)}')"
                f".to(__.V({quote_id(in_server_id)}))"
                f"{prop_chain}"
                f".iterate()"
            )
            client.submit(gremlin).all().result()

    print("✅ Finished loading all CRM data.")
    client.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="CRM dataset CSV → Gremlin loader (Python)")
    p.add_argument("--host", default="ws://gremlin:8182/gremlin")
    p.add_argument("--traversal", default="g")
    p.add_argument(
        "--vertices-file",
        default="gremlin-setup/crm/crm_vertices.csv",
        help="CSV with columns [id,label,name,props]",
    )
    p.add_argument(
        "--edges-file",
        default="gremlin-setup/crm/crm_edges.csv",
        help="CSV with columns [out_id,in_id,label,props]",
    )
    p.add_argument(
        "--no-drop",
        action="store_true",
        help="Do not drop existing vertices first",
    )
    args = p.parse_args()

    load_graph(
        host=args.host,
        traversal=args.traversal,
        vertices_file=args.vertices_file,
        edges_file=args.edges_file,
        drop_first=(not args.no_drop),
    )
