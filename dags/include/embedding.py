"""Gemini embeddings, a disk cache, and an operator subclass that writes vectors
to pgvector instead of pushing them through XCom.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, List, Optional

from airflow.providers.common.ai.operators.llamaindex_embedding import (
    LlamaIndexEmbeddingOperator,
)
from airflow.sdk import BaseHook

from include.paths import EMBED_CACHE, LLM_CONN, VECTOR_CONN
from include.pgvector_io import ensure_schema, upsert_chunks

EMBED_MODEL = "models/gemini-embedding-001"
EMBED_DIM = 768


def _api_key(conn_id: str = LLM_CONN) -> str:
    """Pull the AI Studio key out of the connection's ``extra`` field (not ``conn.password``)."""
    extra = BaseHook.get_connection(conn_id).extra_dejson or {}
    key = extra.get("api_key") or ""
    if not key:
        raise ValueError(
            f"Connection {conn_id!r} has no api_key in its extra. "
            'Expected {"model": "google:gemini-3.1-flash-lite", "api_key": "..."}'
        )
    return key


# Free-tier limit is 100 embed requests/min, and GoogleGenAIEmbedding issues
# one request per text regardless of embed_batch_size. Stay under it.
EMBED_RPM = 90


def build_embed_model(conn_id: str = LLM_CONN, dim: int = EMBED_DIM):
    """A LlamaIndex BaseEmbedding backed by Gemini, rate-limited and cached."""
    from llama_index.embeddings.google_genai import GoogleGenAIEmbedding

    class ThrottledCachedEmbedding(GoogleGenAIEmbedding):
        """Rate-limits to EMBED_RPM and memoises every vector on disk."""

        _calls: list = []

        def _throttle(self) -> None:
            now = time.monotonic()
            self._calls[:] = [t for t in self._calls if now - t < 60.0]
            if len(self._calls) >= EMBED_RPM:
                wait = 60.0 - (now - self._calls[0]) + 0.5
                if wait > 0:
                    time.sleep(wait)
                    now = time.monotonic()
                    self._calls[:] = [t for t in self._calls if now - t < 60.0]
            self._calls.append(time.monotonic())

        def _get_text_embeddings(self, texts: list[str]) -> list[list[float]]:
            out: list[list[float]] = []
            for text in texts:
                cached = _cache_get(text, dim)
                if cached is not None:
                    out.append(cached)
                    continue
                self._throttle()
                vector = super()._get_text_embedding(text)
                _cache_put(text, dim, vector)
                out.append(vector)
            return out

        def _get_text_embedding(self, text: str) -> list[float]:
            return self._get_text_embeddings([text])[0]

    return ThrottledCachedEmbedding(
        model_name=EMBED_MODEL,
        api_key=_api_key(conn_id),
        embedding_config={"output_dimensionality": dim},
        embed_batch_size=1,
    )


def _cache_path(text: str, dim: int) -> "object":
    digest = hashlib.sha256(f"{EMBED_MODEL}:{dim}:{text}".encode()).hexdigest()
    return EMBED_CACHE / f"{digest}.json"


def _cache_get(text: str, dim: int):
    path = _cache_path(text, dim)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return None
    return None


def _cache_put(text: str, dim: int, vector) -> None:
    EMBED_CACHE.mkdir(parents=True, exist_ok=True)
    _cache_path(text, dim).write_text(json.dumps(vector))


def embed_query(text: str, conn_id: str = LLM_CONN, dim: int = EMBED_DIM) -> List[float]:
    """Embed one string, memoised on disk."""
    cached = _cache_get(text, dim)
    if cached is not None:
        return cached
    vector = build_embed_model(conn_id, dim).get_query_embedding(text)
    _cache_put(text, dim, vector)
    return vector


class PgVectorEmbeddingOperator(LlamaIndexEmbeddingOperator):
    """Chunk and embed as usual, then persist to pgvector and return counts only.

    :param llm_conn_id: connection carrying the Gemini API key.
    :param vector_conn_id: connection for the pgvector Postgres.
    :param dim: embedding dimensionality.
    """

    def __init__(
        self,
        *,
        vector_conn_id: str = VECTOR_CONN,
        dim: int = EMBED_DIM,
        llm_conn_id: Optional[str] = LLM_CONN,
        **kwargs: Any,
    ) -> None:
        super().__init__(llm_conn_id=llm_conn_id, **kwargs)
        self.vector_conn_id = vector_conn_id
        self.dim = dim

    def _resolve_embed_model(self):
        if self.embed_model is not None and not isinstance(self.embed_model, str):
            return self.embed_model
        return build_embed_model(self.llm_conn_id or LLM_CONN, self.dim)

    def execute(self, context) -> Dict[str, Any]:
        result = super().execute(context)
        chunks = result.get("chunks") or []

        rows: List[Dict[str, Any]] = []
        for i, chunk in enumerate(chunks):
            vector = chunk.get("vector") or chunk.get("embedding")
            if vector is None:
                continue
            meta = chunk.get("metadata") or {}
            doc_id = str(meta.get("doc_id") or f"CHUNK-{i:05d}")
            rows.append(
                {
                    "chunk_id": f"{doc_id}#{i}",
                    "text": chunk.get("text", ""),
                    "vector": vector,
                    "metadata": meta,
                }
            )

        if not rows:
            raise ValueError(
                "Embedding produced no vectors. Check the Gemini key and that the "
                "documents payload is a non-empty list of {text, metadata} dicts."
            )

        dim = len(rows[0]["vector"])
        ensure_schema(dim, self.vector_conn_id)
        written = upsert_chunks(rows, self.vector_conn_id)
        self.log.info("wrote %d chunks to pgvector (dim=%d)", written, dim)

        return {"chunk_count": written, "dim": dim, "table": "crm_chunks"}
