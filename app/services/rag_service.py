"""
RAG Service -- high-level interface for retrieval-augmented generation.

Provides two retrieval methods used by the agents:
  - search_normativo  : query the shared PUC + Estatuto Tributario collection
  - search_historico  : query a company's past documents by NIT

And one write method used by the Ingesta agent:
  - add_empresa_doc   : persist a new company document into the vector store

All methods return plain Pydantic models so agents do not depend on
pgvector or SQLAlchemy internals.

Pipeline for search_normativo:
  query -> embed (BGE-M3 HF API) -> pgvector top-10 -> bge-reranker-v2-m3 -> top-N
"""

import logging
import uuid
from functools import lru_cache
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.vectordb import (
    NORMATIVA_COLLECTION,
    SupabaseVectorDB,
    empresa_collection_name,
    get_vectordb,
)

logger = logging.getLogger(__name__)

_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
_RETRIEVAL_CANDIDATES = 10  # Fetch this many from pgvector before reranking


# Result schema


class RAGResult(BaseModel):
    """A single retrieval result returned by the RAG service."""

    doc_id: str = Field(description="Unique identifier of the stored document")
    content: str = Field(description="Text content of the retrieved chunk")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Metadata associated with the chunk (source, articulo, tags, ...)",
    )
    score: float = Field(
        ge=0.0,
        le=1.0,
        description="Cosine similarity score (0 = unrelated, 1 = identical)",
    )


# RAGService


class RAGService:
    """
    High-level retrieval service used by the agent pipeline.

    Wraps Supabase pgvector operations so that agents only deal with
    RAGResult objects, never with raw SQL or vector store internals.
    """

    def __init__(self, vectordb: SupabaseVectorDB, hf_api_key: str = ""):
        self._db = vectordb
        self._hf_api_key = hf_api_key

    # Read: normativa

    def search_normativo(
        self,
        query: str,
        n_results: int = 5,
        hybrid: bool = True,
    ) -> list[RAGResult]:
        """
        Search the normativa collection (PUC + Estatuto Tributario).

        Pipeline:
          query -> embed -> retrieval (vector or hybrid) -> bge-reranker-v2-m3 -> top-n

        Args:
            query:    Natural language query string.
            n_results: Number of final results to return after reranking.
            hybrid:   When True (default), use hybrid BM25+vector search with RRF.
                      Requires migration d4e5f6a7b8c9_add_fts_column to be applied.
                      Falls back to pure vector search if search_hybrid() is unavailable
                      or if no FTS matches exist for the query.

        The reranker step is bypassed gracefully if the HF API is unavailable.
        """
        total = self._db.count(NORMATIVA_COLLECTION)
        if total == 0:
            logger.warning(
                "Normativa collection is empty. "
                "Run `python scripts/populate_rag.py` to seed it."
            )
            return []

        candidates = min(max(n_results, _RETRIEVAL_CANDIDATES), total)
        query_embedding = self._db.embed_query(query)

        if hybrid and hasattr(self._db, "search_hybrid"):
            raw = self._db.search_hybrid(
                NORMATIVA_COLLECTION, query, query_embedding, candidates
            )
            # Fall back to pure vector if hybrid returned nothing (e.g., FTS had zero hits)
            if not raw["ids"][0]:
                raw = self._db.search(NORMATIVA_COLLECTION, query_embedding, candidates)
        else:
            raw = self._db.search(NORMATIVA_COLLECTION, query_embedding, candidates)

        results = _parse_search_results(raw)
        return self.rerank(query, results, top_n=n_results)

    # Read: historico

    def search_historico(
        self,
        nit_proveedor: str,
        query: str = "",
        n_results: int = 3,
    ) -> list[RAGResult]:
        """
        Search the company-specific document collection for *nit_proveedor*.

        Args:
            nit_proveedor: NIT of the company whose documents to search.
            query:         Semantic query string; if empty, returns recent docs.
            n_results:     Maximum results.

        Returns:
            List of RAGResult or empty list if no documents found.
        """
        collection = empresa_collection_name(nit_proveedor)
        total = self._db.count(collection)
        if total == 0:
            logger.debug("No documents found for NIT '%s'.", nit_proveedor)
            return []

        k = min(n_results, total)
        if query:
            query_embedding = self._db.embed_query(query)
            raw = self._db.search(collection, query_embedding, k)
        else:
            raw = self._db.get_recent(collection, k)

        return _parse_search_results(raw)

    # Write: empresa_docs

    def add_empresa_doc(
        self,
        nit: str,
        text: str,
        metadata: dict[str, Any] | None = None,
        doc_id: str | None = None,
    ) -> str:
        """
        Add a company document to the per-NIT vector collection.

        Args:
            nit:      Company NIT (collection identifier).
            text:     Full text content of the document.
            metadata: Arbitrary key-value metadata (tipo, fecha, proveedor, ...).
            doc_id:   Optional explicit ID; auto-generated (UUID4) if omitted.

        Returns:
            The doc_id of the stored document.
        """
        if not text or not text.strip():
            raise ValueError("Document text must not be empty.")

        doc_id = doc_id or str(uuid.uuid4())
        meta = {"nit": nit, **(metadata or {})}
        embedding = self._db.embed_texts([text])[0]
        collection = empresa_collection_name(nit)
        self._db.upsert(collection, [doc_id], [text], [embedding], [meta])

        logger.debug("Stored empresa doc '%s' for NIT '%s'.", doc_id, nit)
        return doc_id

    # Reranker

    def rerank(
        self,
        query: str,
        docs: list[RAGResult],
        top_n: int = 3,
    ) -> list[RAGResult]:
        """
        Rerank *docs* using bge-reranker-v2-m3 via HF Inference API.

        Falls back to returning the top-N by original vector score if:
          - No HF API key configured
          - HF API call fails for any reason
        """
        if not docs:
            return []
        top_n = min(top_n, len(docs))

        if not self._hf_api_key:
            return docs[:top_n]

        try:
            pairs = [[query, doc.content] for doc in docs]
            resp = httpx.post(
                f"https://router.huggingface.co/hf-inference/models/{_RERANKER_MODEL}/pipeline/text-ranking",
                headers={
                    "Authorization": f"Bearer {self._hf_api_key}",
                    "Content-Type": "application/json",
                },
                json={"inputs": pairs},
                timeout=30.0,
            )
            resp.raise_for_status()
            scores_raw = resp.json()

            # Response format: {"scores": [float, float, ...]}
            if isinstance(scores_raw, dict) and "scores" in scores_raw:
                scores: list[float] = [float(s) for s in scores_raw["scores"]]
            elif isinstance(scores_raw, list):
                scores = [
                    float(s) if not isinstance(s, dict) else float(s.get("score", 0.0))
                    for s in scores_raw
                ]
            else:
                raise ValueError(
                    f"Unexpected reranker response format: {type(scores_raw)}"
                )

            ranked = sorted(zip(scores, docs), key=lambda x: x[0], reverse=True)
            return [doc for _, doc in ranked[:top_n]]

        except Exception as exc:
            logger.warning(
                "Reranker (%s) failed -- falling back to top-%d by vector score. Error: %s",
                _RERANKER_MODEL,
                top_n,
                exc,
            )
            return docs[:top_n]


# Private helpers


def _parse_search_results(results: dict) -> list[RAGResult]:
    """Convert an internal search result dict into a list of RAGResult.

    The input dict has keys: ids, documents, metadatas, distances.
    Distances are cosine distances in [0, 2] (0=identical, 2=opposite).
    Score = 1 - distance/2  =>  in [0, 1].
    """
    output: list[RAGResult] = []

    ids = results.get("ids", [[]])[0]
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    for doc_id, content, meta, dist in zip(ids, docs, metas, distances):
        score = min(1.0, max(0.0, 1.0 - (float(dist) / 2.0)))
        output.append(
            RAGResult(
                doc_id=doc_id,
                content=content or "",
                metadata=meta or {},
                score=round(score, 4),
            )
        )

    return output


# Singleton factory


@lru_cache(maxsize=1)
def get_rag_service() -> RAGService:
    """
    Return the singleton RAGService.

    In tests, use:
        from app.services.rag_service import get_rag_service
        get_rag_service.cache_clear()
    to reset between test cases.
    """
    settings = get_settings()
    return RAGService(
        vectordb=get_vectordb(),
        hf_api_key=settings.huggingface_api_key,
    )
