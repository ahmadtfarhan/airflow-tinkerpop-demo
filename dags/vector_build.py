"""Build the vector index from the same corpus, into pgvector.

Loads the same documents as kg_build. Chunking is set so one chunk equals one
document, so top_k is directly comparable to the graph agent's query budget.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow.providers.common.ai.operators.document_loader import DocumentLoaderOperator
from airflow.sdk import Asset, dag, task

from include.embedding import EMBED_DIM, PgVectorEmbeddingOperator
from include.paths import DOCS, INDEX_DIR, LLM_CONN, LLM_POOL, VECTOR_CONN

DOC_MD = """
### Vector index build

`DocumentLoaderOperator` -> embed with `gemini-embedding-001` -> **pgvector**.

The loader is configured identically to `kg_build`: same corpus, same parser.

`PgVectorEmbeddingOperator` is a local subclass. The stock
`LlamaIndexEmbeddingOperator` returns every chunk *with its full vector*, which
would push tens of megabytes of float32 through XCom into the metadata DB on
every run. The subclass writes to pgvector and returns counts.
"""


@dag(
    dag_id="vector_build",
    schedule=[Asset("crm_corpus")],
    start_date=datetime(2026, 8, 1),
    catchup=False,
    max_active_runs=1,
    tags=["summit26", "vector", "llm"],
    doc_md=DOC_MD,
    default_args={
        "retries": 5,
        "retry_delay": timedelta(seconds=20),
        "retry_exponential_backoff": True,
        "max_retry_delay": timedelta(minutes=2),
    },
)
def vector_build():
    load_docs = DocumentLoaderOperator(
        task_id="load_docs",
        source_path=str(DOCS),
        json_text_field="text",
        doc_md=(
            "A top-level JSON array. `json_text_field` makes every OTHER key "
            "become chunk metadata -- which is why `strip_metadata` follows."
        ),
    )

    @task
    def strip_metadata(documents: list[dict]) -> list[dict]:
        """Keep only doc_id as metadata; LlamaIndex embeds metadata along with
        text, and the other fields (asserts, doc_type) would leak the answer key."""
        return [
            {"text": d["text"], "metadata": {"doc_id": (d.get("metadata") or {}).get("doc_id", "")}}
            for d in documents
        ]

    clean_docs = strip_metadata(load_docs.output)

    embed_store = PgVectorEmbeddingOperator(
        task_id="embed_store",
        documents=clean_docs,
        llm_conn_id=LLM_CONN,
        vector_conn_id=VECTOR_CONN,
        dim=EMBED_DIM,
        # One chunk == one document.
        chunk_size=1024,
        chunk_overlap=0,
        persist_dir=str(INDEX_DIR),
        pool=LLM_POOL,
    )

    @task
    def create_index() -> dict:
        """Build the HNSW index."""
        from include.pgvector_io import create_hnsw_index

        create_hnsw_index()
        return {"index": "hnsw", "ops": "vector_cosine_ops"}

    @task
    def index_stats() -> dict:
        from include.pgvector_io import stats

        result = stats()
        if result["rows"] == 0:
            raise ValueError("pgvector is empty -- embedding wrote nothing.")
        return result

    embed_store >> create_index() >> index_stats()


vector_build()
