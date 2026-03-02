"""
ChromaDB client — manages two persistent collections:
  - normativa_colombia_v1 : PUC accounts + Estatuto Tributario (read-only for agents)
  - empresa_{nit}_docs    : Company-specific documents (read/write, per-NIT)

Embeddings are generated via Google's gemini-embedding-001 model through
langchain-google-genai (same API key as the chat model).
"""

import logging
from functools import lru_cache

import chromadb
from chromadb import Collection
from chromadb.errors import NotFoundError as ChromaNotFoundError
from langchain_google_genai import GoogleGenerativeAIEmbeddings

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# ─── Collection name constants ───────────────────────────────────────────────
NORMATIVA_COLLECTION = "normativa_colombia_v1"


def empresa_collection_name(nit: str) -> str:
    """Return the per-company collection name for a given NIT."""
    # Sanitise NIT so it's a valid ChromaDB collection name
    safe_nit = "".join(c if c.isalnum() else "_" for c in nit)
    return f"empresa_{safe_nit}_docs"


# ─── ChromaVectorDB ───────────────────────────────────────────────────────────


class ChromaVectorDB:
    """
    Wrapper around a persistent ChromaDB client.

    Usage:
        db = get_vectordb()
        col = db.get_normativa_collection()
    """

    def __init__(self, persist_path: str, api_key: str, embedding_model: str):
        self._client = chromadb.PersistentClient(path=persist_path)
        self._embeddings = GoogleGenerativeAIEmbeddings(
            model=embedding_model,
            google_api_key=api_key,
        )
        logger.info("ChromaDB initialised at '%s'", persist_path)

    # ── Embedding helper ──────────────────────────────────────────────────────

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Return a list of embedding vectors for the given texts."""
        return self._embeddings.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        """Return the embedding vector for a single query string."""
        return self._embeddings.embed_query(text)

    # ── Collection accessors ──────────────────────────────────────────────────

    def get_normativa_collection(self) -> Collection:
        """
        Return (or create) the shared normativa collection.
        This collection is pre-populated by scripts/populate_rag.py.
        """
        return self._client.get_or_create_collection(
            name=NORMATIVA_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )

    def get_empresa_collection(self, nit: str) -> Collection:
        """
        Return (or create) the per-company document collection for *nit*.
        Created lazily when the first document is ingested.
        """
        name = empresa_collection_name(nit)
        return self._client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine", "nit": nit},
        )

    # ── Introspection ─────────────────────────────────────────────────────────

    def list_collections(self) -> list[str]:
        """Return the names of all existing collections."""
        return [col.name for col in self._client.list_collections()]

    def collection_count(self, collection_name: str) -> int:
        """Return the number of documents in a collection, or 0 if it doesn't exist.

        Only silences the "collection not found" case (ChromaDB raises
        ``chromadb.errors.NotFoundError``).  Any other exception (corrupt DB,
        permission error, etc.) is logged and re-raised so callers can
        distinguish an empty collection from a real failure.
        """
        try:
            col = self._client.get_collection(collection_name)
            return col.count()
        except ChromaNotFoundError:
            # Collection does not exist — return 0 rather than raising.
            return 0
        except Exception:
            logger.exception(
                "Unexpected error reading collection '%s'", collection_name
            )
            raise


# ─── Singleton factory ────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def get_vectordb() -> ChromaVectorDB:
    """
    Return the singleton ChromaVectorDB instance.

    The instance is cached after the first call (lru_cache).
    In tests, call get_vectordb.cache_clear() after replacing with a mock.
    """
    settings = get_settings()

    if not settings.gemini_api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. "
            "Add it to your .env file before using the vector DB."
        )

    return ChromaVectorDB(
        persist_path=settings.chroma_persist_path,
        api_key=settings.gemini_api_key,
        embedding_model=settings.gemini_embedding_model,
    )
