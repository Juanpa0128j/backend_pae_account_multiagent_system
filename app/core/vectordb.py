"""
Supabase pgvector client — manages vector document storage using PostgreSQL + pgvector.

Collections are implemented as a virtual partition on the `vector_documents` table,
differentiated by the `collection_name` column:
  - normativa_colombia_v1 : PUC accounts + Estatuto Tributario (read-only for agents)
  - empresa_{nit}_docs    : Company-specific documents (read/write, per-NIT)

Embeddings are generated via the HuggingFace Inference API (BAAI/bge-m3):
  - 1024 dimensions, up to 8192 tokens, 100+ languages
  - Zero RAM / zero disk -- fully API-based

Search modes:
  - search()        : Pure vector (cosine similarity via pgvector HNSW index)
  - search_hybrid() : Hybrid BM25+vector fused with Reciprocal Rank Fusion (RRF)
                      Requires migration d4e5f6a7b8c9_add_fts_column to be applied.
"""

import json
import logging
from functools import lru_cache

from huggingface_hub import InferenceClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Collection name constants
NORMATIVA_COLLECTION = "normativa_colombia_v1"
_EMBEDDING_MODEL = "BAAI/bge-m3"
EMBEDDING_DIM = 1024


def empresa_collection_name(nit: str) -> str:
    """Return the per-company collection name for a given NIT."""
    safe_nit = "".join(c if c.isalnum() else "_" for c in nit)
    return f"empresa_{safe_nit}_docs"


class SupabaseVectorDB:
    """
    Vector store backed by Supabase (PostgreSQL + pgvector).

    All documents are stored in the `vector_documents` table, partitioned
    by `collection_name`. Embeddings are generated via the HuggingFace
    Inference API (BAAI/bge-m3).

    Usage:
        db = get_vectordb()
        db.upsert(NORMATIVA_COLLECTION, ids, texts, embeddings, metas)
    """

    def __init__(self, database_url: str, hf_api_key: str):
        self._engine = create_engine(
            database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )
        self._hf = InferenceClient(token=hf_api_key)
        logger.info("SupabaseVectorDB initialised (model: %s)", _EMBEDDING_MODEL)

    # Embedding helpers

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Return 1024-dim embedding vectors for each text via HF API."""
        raw = self._hf.feature_extraction(texts, model=_EMBEDDING_MODEL)
        # HF API may return numpy arrays or nested lists -- normalise to list[list[float]]
        if hasattr(raw, "tolist"):
            raw = raw.tolist()
        result = []
        for row in raw:
            if hasattr(row, "tolist"):
                row = row.tolist()
            result.append([float(v) for v in row])
        return result

    def embed_query(self, text: str) -> list[float]:
        """Return a single 1024-dim embedding vector for the given query."""
        return self.embed_texts([text])[0]

    # Read

    def search(
        self,
        collection_name: str,
        query_embedding: list[float],
        n_results: int,
    ) -> dict:
        """
        Cosine similarity search within a collection.

        Returns a dict with keys: ids, documents, metadatas, distances.
        Distances are cosine distances in [0, 2] (0 = identical), matching
        ChromaDB convention so _parse_search_results() stays unchanged.
        """
        vec_str = _vec_to_str(query_embedding)
        sql = text("""
            SELECT id, content, metadata,
                   embedding <=> CAST(:vec AS vector) AS cosine_dist
            FROM vector_documents
            WHERE collection_name = :collection
            ORDER BY embedding <=> CAST(:vec AS vector)
            LIMIT :k
        """)
        with Session(self._engine) as session:
            rows = session.execute(
                sql,
                {"vec": vec_str, "collection": collection_name, "k": n_results},
            ).fetchall()

        ids, docs, metas, dists = [], [], [], []
        for row in rows:
            ids.append(str(row[0]))
            docs.append(row[1] or "")
            metas.append(row[2] or {})
            dists.append(float(row[3]))

        return {
            "ids": [ids],
            "documents": [docs],
            "metadatas": [metas],
            "distances": [dists],
        }

    def search_hybrid(
        self,
        collection_name: str,
        query_text: str,
        query_embedding: list[float],
        n_results: int,
        rrf_k: int = 60,
    ) -> dict:
        """
        Hybrid retrieval: full-text search (tsvector) + vector cosine similarity
        fused with Reciprocal Rank Fusion (RRF).

        RRF score = 1/(rrf_k + fts_rank) + 1/(rrf_k + vec_rank)

        Both lists independently retrieve up to n_results * 2 candidates before
        fusion, so the final n_results are drawn from a wider pool.

        The returned ``distances`` are normalised into [0, 2] using the formula:
            dist = 2 * (1 - rrf_score / MAX_RRF)
        where MAX_RRF = 2 / (rrf_k + 1).  This ensures _parse_search_results()
        produces scores in [0, 1] (score = 1 - dist/2 = rrf_score / MAX_RRF).

        Requires migration d4e5f6a7b8c9_add_fts_column to be applied (adds the
        GENERATED tsvector column and its GIN index).

        Falls back automatically to pure vector search when no FTS matches exist.
        """
        pool = n_results * 2  # wider candidate pool for fusion
        vec_str = _vec_to_str(query_embedding)

        sql = text("""
            WITH
            fts_cte AS (
                SELECT id,
                       ROW_NUMBER() OVER (
                           ORDER BY ts_rank_cd(content_tsv,
                                               plainto_tsquery('spanish', :query_text)) DESC
                       ) AS rank
                FROM vector_documents
                WHERE collection_name = :collection
                  AND content_tsv @@ plainto_tsquery('spanish', :query_text)
                LIMIT :pool
            ),
            vec_cte AS (
                SELECT id,
                       ROW_NUMBER() OVER (
                           ORDER BY embedding <=> CAST(:vec AS vector)
                       ) AS rank
                FROM vector_documents
                WHERE collection_name = :collection
                ORDER BY embedding <=> CAST(:vec AS vector)
                LIMIT :pool
            ),
            rrf_cte AS (
                SELECT
                    COALESCE(f.id, v.id) AS id,
                    COALESCE(1.0 / (:rrf_k + f.rank), 0.0)
                    + COALESCE(1.0 / (:rrf_k + v.rank), 0.0) AS rrf_score
                FROM fts_cte f
                FULL OUTER JOIN vec_cte v ON f.id = v.id
            )
            SELECT d.id, d.content, d.metadata, r.rrf_score
            FROM rrf_cte r
            JOIN vector_documents d ON d.id = r.id
            ORDER BY r.rrf_score DESC
            LIMIT :n_results
        """)

        with Session(self._engine) as session:
            rows = session.execute(
                sql,
                {
                    "query_text": query_text,
                    "vec": vec_str,
                    "collection": collection_name,
                    "pool": pool,
                    "rrf_k": rrf_k,
                    "n_results": n_results,
                },
            ).fetchall()

        if not rows:
            return {
                "ids": [[]],
                "documents": [[]],
                "metadatas": [[]],
                "distances": [[]],
            }

        max_rrf = 2.0 / (rrf_k + 1)
        ids, docs, metas, dists = [], [], [], []
        for row in rows:
            ids.append(str(row[0]))
            docs.append(row[1] or "")
            metas.append(row[2] or {})
            rrf_score = float(row[3])
            # Map rrf_score → distance in [0, 2] for _parse_search_results compatibility
            dist = 2.0 * (1.0 - rrf_score / max_rrf)
            dists.append(max(0.0, min(2.0, dist)))

        return {
            "ids": [ids],
            "documents": [docs],
            "metadatas": [metas],
            "distances": [dists],
        }

    def get_recent(self, collection_name: str, limit: int) -> dict:
        """Return the most recently added documents (no semantic search)."""
        sql = text("""
            SELECT id, content, metadata
            FROM vector_documents
            WHERE collection_name = :collection
            ORDER BY created_at DESC
            LIMIT :k
        """)
        with Session(self._engine) as session:
            rows = session.execute(
                sql, {"collection": collection_name, "k": limit}
            ).fetchall()

        ids, docs, metas = [], [], []
        for row in rows:
            ids.append(str(row[0]))
            docs.append(row[1] or "")
            metas.append(row[2] or {})

        return {
            "ids": [ids],
            "documents": [docs],
            "metadatas": [metas],
            "distances": [[0.0] * len(ids)],
        }

    def count(self, collection_name: str) -> int:
        """Return the number of documents in a collection."""
        sql = text(
            "SELECT COUNT(*) FROM vector_documents WHERE collection_name = :collection"
        )
        with Session(self._engine) as session:
            result = session.execute(sql, {"collection": collection_name}).scalar()
        return int(result or 0)

    # Write

    def upsert(
        self,
        collection_name: str,
        ids: list[str],
        texts: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict],
    ) -> None:
        """Insert or update documents. ON CONFLICT (collection_name, id) updates document fields."""
        sql = text("""
            INSERT INTO vector_documents
                (id, collection_name, content, embedding, metadata)
            VALUES
                (:id, :collection, :content, CAST(:vec AS vector), CAST(:meta AS jsonb))
            ON CONFLICT (collection_name, id) DO UPDATE SET
                content   = EXCLUDED.content,
                embedding = EXCLUDED.embedding,
                metadata  = EXCLUDED.metadata
        """)
        with Session(self._engine) as session:
            for doc_id, content, emb, meta in zip(ids, texts, embeddings, metadatas):
                session.execute(
                    sql,
                    {
                        "id": doc_id,
                        "collection": collection_name,
                        "content": content,
                        "vec": _vec_to_str(emb),
                        "meta": json.dumps(meta),
                    },
                )
            session.commit()

    def add(
        self,
        collection_name: str,
        ids: list[str],
        texts: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict],
    ) -> None:
        """Insert new documents. Raises IntegrityError on duplicate id."""
        sql = text("""
            INSERT INTO vector_documents
                (id, collection_name, content, embedding, metadata)
            VALUES
                (:id, :collection, :content, CAST(:vec AS vector), CAST(:meta AS jsonb))
        """)
        with Session(self._engine) as session:
            for doc_id, content, emb, meta in zip(ids, texts, embeddings, metadatas):
                session.execute(
                    sql,
                    {
                        "id": doc_id,
                        "collection": collection_name,
                        "content": content,
                        "vec": _vec_to_str(emb),
                        "meta": json.dumps(meta),
                    },
                )
            session.commit()

    def delete_all(self, collection_name: str) -> int:
        """Delete all documents in a collection. Returns count of deleted rows."""
        sql = text("DELETE FROM vector_documents WHERE collection_name = :collection")
        with Session(self._engine) as session:
            result = session.execute(sql, {"collection": collection_name})
            session.commit()
            return result.rowcount


def _vec_to_str(vec: list[float]) -> str:
    """Convert a float list to the pgvector literal format '[a,b,c,...]'."""
    return "[" + ",".join(str(v) for v in vec) + "]"


@lru_cache(maxsize=1)
def get_vectordb() -> SupabaseVectorDB:
    """
    Return the singleton SupabaseVectorDB instance.
    In tests, call get_vectordb.cache_clear() after patching.
    """
    settings = get_settings()

    if not settings.huggingface_api_key:
        raise RuntimeError(
            "HUGGINGFACE_API_KEY is not set. "
            "Add it to your .env file before using the vector DB."
        )

    return SupabaseVectorDB(
        database_url=settings.database_url,
        hf_api_key=settings.huggingface_api_key,
    )
