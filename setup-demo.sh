#!/bin/bash
set -e

echo "Waiting for Airflow worker to be available..."
while ! docker-compose ps | grep -q 'airflow-worker.*Up'; do
  sleep 5
done

echo "Setting up Airflow connections..."
docker-compose exec airflow-worker bash -c "/opt/airflow/config/setup_connections.sh"

echo "Waiting for Gremlin server to be available..."
while ! docker-compose ps | grep -q 'gremlin.*Up'; do
  sleep 5
done

echo "Loading graph data..."
docker-compose exec airflow-worker bash -c "python /opt/airflow/gremlin-setup/load_crm_graph.py --vertices-file /opt/airflow/gremlin-setup/crm_vertices.csv --edges-file /opt/airflow/gremlin-setup/crm_edges.csv"