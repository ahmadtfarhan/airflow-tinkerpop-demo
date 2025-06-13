from gremlin_python.structure.graph import Graph
from gremlin_python.driver.driver_remote_connection import DriverRemoteConnection

# 1. Create a local Graph & open a remote connection to your Gremlin Server
graph = Graph()
# adjust the URL if your server isn’t on localhost or uses a different port/path
remote_conn = DriverRemoteConnection('ws://localhost:8182/gremlin', 'g')

# 2. Bind a TraversalSource to that remote connection
g = graph.traversal().withRemote(remote_conn)

# 3. Execute g.V() and pull back all vertices as a list
vertices = g.V().valueMap(True)

# 4. Do something with the result
for v in vertices:
    print(v)               # prints each vertex object
# print(f"Total vertices: {len(vertices)}")

# 5. Clean up your connection when done
remote_conn.close()
