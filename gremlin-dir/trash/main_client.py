import argparse
import random
import uuid

from gremlin_python.driver.client import Client
from faker import Faker


def generate_data(client, num_users=50, num_edges=100):
    fake = Faker()
    # Step 1: Create users
    user_ids = []
    for _ in range(num_users):
        uid = str(uuid.uuid4())
        name = fake.name().replace("'", "\\'")
        age = random.randint(18, 80)
        # Add a User vertex with properties
        gremlin = (
            f"g.addV('User')"
            f".property('user_id', '{uid}')"
            f".property('name', '{name}')"
            f".property('age', {age})"
        )
        client.submit(gremlin).all().result()
        user_ids.append(uid)

    # Step 2: Create 'knows' edges between random users using anonymous traversals
    for _ in range(num_edges):
        u1, u2 = random.sample(user_ids, 2)
        # Use __.V() for the child traversal to avoid the "not spawned anonymously" error
        gremlin = (
            f"g.V().has('User','user_id','{u1}')"
            f".addE('knows').to(__.V().has('User','user_id','{u2}'))"
        )
        client.submit(gremlin).all().result()

    print(f"Inserted {num_users} User vertices and {num_edges} knows edges.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Populate a Gremlin graph with demo data')
    parser.add_argument('--host', default='ws://localhost:8182/gremlin', help='Gremlin Server URL')
    parser.add_argument('--traversal', default='g', help='Traversal source name')
    parser.add_argument('--users', type=int, default=50, help='Number of User vertices to create')
    parser.add_argument('--edges', type=int, default=100, help='Number of knows edges to create')
    args = parser.parse_args()

    client = Client(url=args.host, traversal_source=args.traversal)
    try:
        generate_data(client, num_users=args.users, num_edges=args.edges)
    finally:
        client.close()
