"""Filesystem layout, in one place.

/opt/airflow/data is a bind mount of ./data, shared by every container.
"""

from __future__ import annotations

import os
from pathlib import Path

DATA = Path(os.environ.get("DEMO_DATA_DIR", "/opt/airflow/data"))

CORPUS = DATA / "corpus"
DOCS = CORPUS / "docs.json"
ENTITIES = CORPUS / "entities.json"
ALIASES = CORPUS / "aliases.json"
GOLD_EDGES = CORPUS / "gold_edges.json"
QUESTIONS = CORPUS / "questions.json"

# Offline-fallback artifacts: graph and vector store can be restored with zero API calls.
SEED = DATA / "seed"
SEED_KG_EDGES = SEED / "kg_edges.json"

RESULTS = DATA / "results"
CACHE = DATA / "cache"
EMBED_CACHE = CACHE / "embeddings"
INDEX_DIR = DATA / "index" / "crm_index"

GREMLIN_SETUP = Path("/opt/airflow/gremlin-setup")

GREMLIN_CONN = "gremlin"
LLM_CONN = "gemini_default"
VECTOR_CONN = "vectordb"
LLM_POOL = "gemini_free"

CHUNK_TABLE = "crm_chunks"
