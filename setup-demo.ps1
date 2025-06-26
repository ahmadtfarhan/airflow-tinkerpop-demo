#!/usr/bin/env pwsh
# Stop on any error
$ErrorActionPreference = 'Stop'

Write-Host "Waiting for Airflow worker to be available..."
while (-not (docker-compose ps | Select-String -Quiet 'airflow-worker.*Up')) {
    Start-Sleep -Seconds 5
}

Write-Host "Setting up Airflow connections..."
docker-compose exec airflow-worker bash -c "/opt/airflow/config/setup_connections.sh"

Write-Host "Waiting for Gremlin server to be available..."
while (-not (docker-compose ps | Select-String -Quiet 'gremlin.*Up')) {
    Start-Sleep -Seconds 5
}

Write-Host "Loading graph data..."
docker-compose exec airflow-worker bash -c "python /opt/airflow/gremlin-setup/movie.py --vertices-file /opt/airflow/gremlin-setup/vertices.csv --edges-file /opt/airflow/gremlin-setup/edges.csv"
