# Bring the demo up from a cold `docker compose up -d`.  Windows twin of setup-demo.sh.
#   1. wait for Airflow
#   2. create connections + the Gemini throttle pool
#   3. generate the corpus and question set (deterministic, no LLM calls)
#   4. load the seed knowledge graph into Gremlin (no LLM calls)
#
# Note: step 4 loads data/seed/kg_edges.json -- the graph an LLM EXTRACTED from
# the corpus -- not gremlin-setup/crm_edges.csv.  The ground-truth CSV is the
# scoring key and must never reach Gremlin, or the benchmark is rigged.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$geminiKey = ""
if (Test-Path .env) {
  Get-Content .env | ForEach-Object {
    if ($_ -match '^\s*GEMINI_API_KEY\s*=\s*(.*)$') { $geminiKey = $Matches[1].Trim() }
  }
}

Write-Host "Waiting for Airflow worker..."
while (-not (docker compose ps airflow-worker | Select-String -Pattern 'Up|running')) { Start-Sleep -Seconds 5 }

Write-Host "Setting up Airflow connections and pools..."
docker compose exec -T -e "GEMINI_API_KEY=$geminiKey" airflow-worker bash -c "/opt/airflow/config/setup_connections.sh"

# Deterministic and LLM-free, so it is cheap to always regenerate and it
# guarantees the corpus, gold edges and questions match this checkout.
Write-Host "Generating corpus and question set..."
docker compose exec -T airflow-worker python /opt/airflow/gremlin-setup/generate_corpus.py
docker compose exec -T airflow-worker python /opt/airflow/gremlin-setup/build_questions.py

Write-Host "Waiting for Gremlin Server..."
while (-not (docker compose ps gremlin | Select-String -Pattern 'Up|running')) { Start-Sleep -Seconds 5 }

# First run has no seed graph yet -- it comes from a real extraction run.
# Not having one is expected, not an error, so do not take the whole setup down
# with it.
Write-Host "Loading seed knowledge graph..."
$graphReady = $false
if (Test-Path "data/seed/kg_edges.json") {
  docker compose exec -T airflow-worker python /opt/airflow/gremlin-setup/load_kg.py --edges-file /opt/airflow/data/seed/kg_edges.json
  $graphReady = $LASTEXITCODE -eq 0
}

Write-Host ""
Write-Host "Ready.  Airflow UI: http://localhost:8080  (airflow / airflow)"
if (-not $graphReady) {
  Write-Host ""
  Write-Host "NEXT: there is no knowledge graph yet. Build one with"
  Write-Host "  docker compose exec airflow-worker ``"
  Write-Host "    airflow dags trigger kg_build --conf '{\""use_cached_extraction\"": false}'"
}
Write-Host ""
Write-Host "Run python scripts/demo_doctor.py before presenting."
