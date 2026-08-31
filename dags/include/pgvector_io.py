"""pgvector storage and retrieval for the vector arm.

Hand-written SQL rather than a framework, since ``LlamaIndexRetrievalOperator``
cannot read pgvector directly. Chunk size is set so one chunk equals one
document, making top_k directly comparable to the graph agent's query budget.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from airflow.providers.postgres.hooks.postgres import PostgresHook

from include.paths import CHUNK_TABLE, VECTOR_CONN


def _connect(conn_id: str = VECTOR_CONN):
    """Open a connection with the pgvector types registered.

    Picks the psycopg2 vs psycopg3 adapter based on the connection type,
    since providers-postgres 7.x returns psycopg3 and pgvector needs the
    matching adapter.
    """
    conn = PostgresHook(postgres_conn_id=conn_id).get_conn()

    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.commit()

    module = type(conn).__module__
    if module.startswith("psycopg2"):
        from pgvector.psycopg2 import register_vector
    else:
        from pgvector.psycopg import register_vector
    register_vector(conn)
    return conn


def ensure_schema(dim: int, conn_id: str = VECTOR_CONN) -> None:
    """Create the chunk table. Recreates it if the embedding dimension changed."""
    conn = _connect(conn_id)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.atttypmod
                FROM pg_attribute a
                JOIN pg_class c ON c.oid = a.attrelid
                WHERE c.relname = %s AND a.attname = 'embedding'
                """,
                (CHUNK_TABLE,),
            )
            row = cur.fetchone()
            if row and row[0] != dim:
                # Changing output_dimensionality invalidates every stored vector.
                cur.execute(f"DROP TABLE IF EXISTS {CHUNK_TABLE}")
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {CHUNK_TABLE} (
                    chunk_id   TEXT PRIMARY KEY,
                    doc_id     TEXT NOT NULL,
                    doc_type   TEXT,
                    text       TEXT NOT NULL,
                    embedding  vector({dim}) NOT NULL
                )
                """
            )
        conn.commit()
    finally:
        conn.close()


def upsert_chunks(chunks: Sequence[Dict[str, Any]], conn_id: str = VECTOR_CONN) -> int:
    """Idempotent write, so re-running vector_build never duplicates rows."""
    if not chunks:
        return 0
    conn = _connect(conn_id)
    try:
        with conn.cursor() as cur:
            for c in chunks:
                meta = c.get("metadata") or {}
                doc_id = str(meta.get("doc_id") or c.get("id") or "")
                cur.execute(
                    f"""
                    INSERT INTO {CHUNK_TABLE} (chunk_id, doc_id, doc_type, text, embedding)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (chunk_id) DO UPDATE
                      SET doc_id = EXCLUDED.doc_id,
                          doc_type = EXCLUDED.doc_type,
                          text = EXCLUDED.text,
                          embedding = EXCLUDED.embedding
                    """,
                    (
                        c["chunk_id"],
                        doc_id,
                        meta.get("doc_type"),
                        c["text"],
                        c["vector"],
                    ),
                )
        conn.commit()
        return len(chunks)
    finally:
        conn.close()


def create_hnsw_index(conn_id: str = VECTOR_CONN) -> None:
    """Build the ANN index (no-op benefit at this row count, but production-realistic)."""
    conn = _connect(conn_id)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS {CHUNK_TABLE}_hnsw "
                f"ON {CHUNK_TABLE} USING hnsw (embedding vector_cosine_ops)"
            )
        conn.commit()
    finally:
        conn.close()


def top_k(query_vector, k: int = 5, conn_id: str = VECTOR_CONN) -> List[Dict[str, Any]]:
    """Cosine-similarity search. This query is the entire vector arm."""
    conn = _connect(conn_id)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT doc_id, doc_type, text, 1 - (embedding <=> %s::vector) AS score
                FROM {CHUNK_TABLE}
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (query_vector, query_vector, k),
            )
            return [
                {"doc_id": r[0], "doc_type": r[1], "text": r[2], "score": float(r[3])}
                for r in cur.fetchall()
            ]
    finally:
        conn.close()


def stats(conn_id: str = VECTOR_CONN) -> Dict[str, Any]:
    conn = _connect(conn_id)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*), min(vector_dims(embedding)) FROM {CHUNK_TABLE}")
            rows, dim = cur.fetchone()
            return {"table": CHUNK_TABLE, "rows": int(rows or 0), "dim": int(dim or 0)}
    finally:
        conn.close()
