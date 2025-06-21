import argparse

from gremlin_python.driver.client import Client


def clear_graph(client):
    # Drop all vertices (automatically removes incident edges)
    drop_query = "g.V().drop()"
    result = client.submit(drop_query)
    # Wait for completion
    result.all().result()
    print("Graph cleared: all vertices and edges have been removed.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Clear all data from a Gremlin graph')
    parser.add_argument('--host', default='ws://localhost:8182/gremlin', help='Gremlin Server URL')
    parser.add_argument('--traversal', default='g', help='Traversal source name')
    args = parser.parse_args()

    client = Client(url=args.host, traversal_source=args.traversal)
    try:
        clear_graph(client)
    finally:
        client.close()
