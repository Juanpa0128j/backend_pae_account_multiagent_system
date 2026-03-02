"""
RAG Service — high-level interface for retrieval-augmented generation.

Provides two retrieval methods used by the agents:
  - search_normativo  : query the shared PUC + Estatuto Tributario collection
  - search_historico  : query a company's past documents by NIT

And one write method used by the Ingesta agent:
  - add_empresa_doc   : persist a new company document into the vector store

All methods return plain Pydantic models so agents don't depend on
ChromaDB internals.
"""

import logging
import uuid
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, Field

from app.core.vectordb import ChromaVectorDB, get_vectordb

logger = logging.getLogger(__name__)


# ─── Result schema ────────────────────────────────────────────────────────────

class RAGResult(BaseModel):
    """A single retrieval result returned by the RAG service."""

    doc_id: str = Field(description="Unique identifier of the stored document")
    content: str = Field(description="Text content of the retrieved chunk")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Metadata associated with the chunk (source, articulo, tags, …)"
    )
    score: float = Field(
        ge=0.0, le=1.0,
        description="Cosine similarity score (0 = unrelated, 1 = identical)"
    )


# ─── RAGService ───────────────────────────────────────────────────────────────

class RAGService:
    """
    High-level retrieval service used by the agent pipeline.

    Wraps ChromaDB operations so that agents only deal with RAGResult
    objects, never with raw ChromaDB dicts.
    """

    def __init__(self, vectordb: ChromaVectorDB):
        self._db = vectordb

    # ── Read: normativa ───────────────────────────────────────────────────────

    def search_normativo(
        self,
        query: str,
        n_results: int = 5,
    ) -> list[RAGResult]:
        """
        Search the normativa collection (PUC + Estatuto Tributario).

        Args:
            query:     Natural-language query, e.g. "retención en la fuente honorarios".
            n_results: Maximum number of results to return.

        Returns:
            List of RAGResult sorted by descending similarity.
        """
        collection = self._db.get_normativa_collection()
        total_docs = collection.count()

        if total_docs == 0:
            logger.warning(
                "Normativa collection is empty. "
                "Run `python scripts/populate_rag.py` to seed it."
            )
            return []

        # Clamp n_results to what's actually in the collection
        k = min(n_results, total_docs)

        query_embedding = self._db.embed_query(query)
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=k,
            include=["documents", "metadatas", "distances"],
        )

        return self._parse_query_results(results)

    # ── Read: historico ───────────────────────────────────────────────────────

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
        collection = self._db.get_empresa_collection(nit_proveedor)
        total_docs = collection.count()

        if total_docs == 0:
            logger.debug("No documents found for NIT '%s'.", nit_proveedor)
            return []

        k = min(n_results, total_docs)

        if query:
            query_embedding = self._db.embed_query(query)
            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=k,
                include=["documents", "metadatas", "distances"],
            )
        else:
            # No query — return the most recently added documents
            raw = collection.get(
                limit=k,
                include=["documents", "metadatas"],
            )
            # Convert get() format → query() format for unified parsing
            results = {
                "ids": [raw["ids"]],
                "documents": [raw["documents"]],
                "metadatas": [raw["metadatas"]],
                "distances": [[0.0] * len(raw["ids"])],  # 0 distance = "perfect"
            }

        return self._parse_query_results(results)

    # ── Write: empresa_docs ───────────────────────────────────────────────────

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
            metadata: Arbitrary key-value metadata (tipo, fecha, proveedor, …).
            doc_id:   Optional explicit ID; auto-generated (UUID4) if omitted.

        Returns:
            The doc_id of the stored document.
        """
        if not text or not text.strip():
            raise ValueError("Document text must not be empty.")

        doc_id = doc_id or str(uuid.uuid4())
        meta = {"nit": nit, **(metadata or {})}
        embedding = self._db.embed_texts([text])[0]

        collection = self._db.get_empresa_collection(nit)
        collection.add(
            ids=[doc_id],
            documents=[text],
            embeddings=[embedding],
            metadatas=[meta],
        )

        logger.debug("Stored empresa doc '%s' for NIT '%s'.", doc_id, nit)
        return doc_id

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _parse_query_results(results: dict) -> list[RAGResult]:
        """Convert a raw ChromaDB query result dict into a list of RAGResult."""
        output: list[RAGResult] = []

        ids = results.get("ids", [[]])[0]
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        for doc_id, content, meta, dist in zip(ids, docs, metas, distances):
            # ChromaDB returns cosine *distance* (0 = identical, 2 = opposite).
            # Convert to a similarity score in [0, 1].
            score = min(1.0, max(0.0, 1.0 - (dist / 2.0)))
            output.append(
                RAGResult(
                    doc_id=doc_id,
                    content=content or "",
                    metadata=meta or {},
                    score=round(score, 4),
                )
            )

        return output


# ─── Singleton factory ────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_rag_service() -> RAGService:
    """
    Return the singleton RAGService.

    In tests, use:
        from app.services.rag_service import get_rag_service
        get_rag_service.cache_clear()
    to reset between test cases.
    """
    return RAGService(vectordb=get_vectordb())
