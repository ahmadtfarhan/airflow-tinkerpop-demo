#!/usr/bin/env python3
import argparse, csv
from gremlin_python.driver.client import Client
from gremlin_python.process.graph_traversal import __     # for anonymous traversals

def quote_id(v_id):
    """Wrap string IDs in quotes, leave numerics alone."""
    return f"'{v_id}'" if isinstance(v_id, str) else str(v_id)

def escape_prop(val: str) -> str:
    """Escape single‐quotes for embedding in Gremlin strings."""
    return val.replace("'", "\\'")

def load_graph(host, traversal, nodes_file, edges_file, network_file):
    client = Client(host, traversal_source=traversal)

    # 1. Clear out any existing data
    print("⏳ Dropping all vertices…")
    client.submit("g.V().drop()").all().result()

    # 2. Load all nodes, caching their server‐assigned ID under the *raw* name
    print(f"⏳ Loading nodes from {nodes_file}…")
    id_map = {}  # (label, raw_name) -> v_id
    with open(nodes_file, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            raw_name = row['node']
            label    = row['type']
            esc_name = escape_prop(raw_name)

            gremlin = (
                f"g.addV('{label}')"
                f".property('name','{esc_name}')"
                f".id()"
            )
            v_id = client.submit(gremlin).all().result()[0]
            # store under the un‐escaped CSV value
            id_map[(label, raw_name)] = v_id

    # 3. Hero→Comic edges by ID
    print(f"⏳ Loading hero–comic edges from {edges_file}…")
    with open(edges_file, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            hero_name  = row['hero']
            comic_name = row['comic']
            h_id = id_map[('hero',  hero_name)]
            c_id = id_map[('comic', comic_name)]

            gremlin = (
                f"g.V({quote_id(h_id)})"
                f".addE('appears_in')"
                f".to(__.V({quote_id(c_id)}))"
                f".iterate()"
            )
            client.submit(gremlin).all().result()

    # 4. Hero–Hero network edges by ID
    print(f"⏳ Loading hero–hero edges from {network_file}…")
    with open(network_file, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            h1_id = id_map[('hero', row['hero1'])]
            h2_id = id_map[('hero', row['hero2'])]
            gremlin = (
                f"g.V({quote_id(h1_id)})"
                f".addE('interacts')"
                f".to(__.V({quote_id(h2_id)}))"
                f".iterate()"
            )
            client.submit(gremlin).all().result()

    print("✅ Finished loading all data.")
    client.close()

if __name__ == '__main__':
    p = argparse.ArgumentParser(description="Optimized CSV → Gremlin loader")
    p.add_argument('--host',        default='ws://localhost:8182/gremlin')
    p.add_argument('--traversal',   default='g')
    p.add_argument('--nodes-file',  default='nodes_tiny.csv',
                   help='CSV with columns [node,type]')
    p.add_argument('--edges-file',  default='edges_tiny.csv',
                   help='CSV with columns [hero,comic]')
    p.add_argument('--network-file',default='hero-network_tiny.csv',
                   help='CSV with columns [hero1,hero2]')
    args = p.parse_args()

    load_graph(
        host=args.host,
        traversal=args.traversal,
        nodes_file=args.nodes_file,
        edges_file=args.edges_file,
        network_file=args.network_file
    )
