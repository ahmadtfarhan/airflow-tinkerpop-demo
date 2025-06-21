#!/usr/bin/env python3
import argparse, csv
from gremlin_python.driver.client import Client
from gremlin_python.process.graph_traversal import __     # for anonymous traversals

def quote_id(v_id):
    """Wrap string IDs in quotes, leave numerics alone."""
    return f"'{v_id}'" if isinstance(v_id, str) else str(v_id)

def escape_prop(val: str) -> str:
    """Escape single-quotes for embedding in Gremlin strings."""
    return val.replace("'", "\\'")

def load_graph(host, traversal, vertices_file, edges_file):
    client = Client(host, traversal_source=traversal)

    # 1. Clear out any existing data
    print("⏳ Dropping all vertices…")
    client.submit("g.V().drop()").all().result()

    # 2. Load all vertices, caching their server-assigned ID
    print(f"⏳ Loading vertices from {vertices_file}…")
    id_map = {}  # (type, id) -> v_id
    with open(vertices_file, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            v_id = row['id']
            label = row['type']
            name = escape_prop(row['name'])
            year = int(row['year'])

            gremlin = (
                f"g.addV('{label}')"
                f".property('id', {quote_id(v_id)})"
                f".property('name', '{name}')"
                f".property('year', {year})"
                f".id()"
            )
            server_id = client.submit(gremlin).all().result()[0]
            # Store under the CSV id and type
            id_map[(label, v_id)] = server_id

    # 3. Actor-Movie edges by ID
    print(f"⏳ Loading actor-movie edges from {edges_file}…")
    with open(edges_file, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            actor_id = row['source']
            movie_id = row['target']
            role = escape_prop(row['role'])
            a_id = id_map[('actor', actor_id)]
            m_id = id_map[('movie', movie_id)]

            gremlin = (
                f"g.V({quote_id(a_id)})"
                f".addE('plays')"
                f".to(__.V({quote_id(m_id)}))"
                f".property('role', '{role}')"
                f".iterate()"
            )
            client.submit(gremlin).all().result()

    print("✅ Finished loading all data.")
    client.close()

if __name__ == '__main__':
    p = argparse.ArgumentParser(description="Movie dataset CSV → Gremlin loader")
    p.add_argument('--host', default='ws://gremlin:8182/gremlin')
    p.add_argument('--traversal', default='g')
    p.add_argument('--vertices-file', default='gremlin-setup/vertices.csv',
                   help='CSV with columns [id,type,name,year]')
    p.add_argument('--edges-file', default='gremlin-setup/edges.csv',
                   help='CSV with columns [source,target,role]')
    args = p.parse_args()

    load_graph(
        host=args.host,
        traversal=args.traversal,
        vertices_file=args.vertices_file,
        edges_file=args.edges_file
    )