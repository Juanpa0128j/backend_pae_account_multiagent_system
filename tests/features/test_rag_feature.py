"""
Tests for the Vector DB and RAG Service layer.

Design principles:
  - NEVER calls the HuggingFace API or any external service.
  - Uses an in-memory FakeSupabaseVectorDB injected via pytest fixtures.
  - Each test method is independent (no shared mutable state between tests).

Run:
    pytest tests/test_rag.py -v
"""

import hashlib
from datetime import datetime
from typing import Any

import numpy as np
import pytest

from app.core.vectordb import NORMATIVA_COLLECTION, empresa_collection_name
from app.services.rag_service import RAGResult, RAGService

# Deterministic fake embeddings: no API calls


def _text_seed(text: str) -> int:
    """Derive a stable 32-bit RNG seed from text using SHA-256."""
    digest = hashlib.sha256(text.encode()).digest()
    return int.from_bytes(digest[:4], byteorder="little")


def _fake_embed(text: str) -> list[float]:
    """Return a deterministic 1024-dim embedding (no API call)."""
    return np.random.default_rng(seed=_text_seed(text)).random(1024).tolist()


# FakeSupabaseVectorDB


class FakeSupabaseVectorDB:
    """
    In-memory drop-in for SupabaseVectorDB. No database, no API calls.
    Each instance has fully isolated state suitable for pytest fixtures.
    """

    def __init__(self):
        # { collection_name: list of {"id", "content", "embedding", "metadata", "created_at"} }
        self._store: dict[str, list[dict]] = {}

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [_fake_embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return _fake_embed(text)

    def search(
        self,
        collection_name: str,
        query_embedding: list[float],
        n_results: int,
    ) -> dict:
        docs = self._store.get(collection_name, [])
        if not docs:
            return {
                "ids": [[]],
                "documents": [[]],
                "metadatas": [[]],
                "distances": [[]],
            }

        q = np.array(query_embedding)
        scored = []
        for doc in docs:
            e = np.array(doc["embedding"])
            norm = np.linalg.norm(q) * np.linalg.norm(e)
            cos_sim = float(np.dot(q, e) / (norm + 1e-10))
            cos_dist = 1.0 - cos_sim  # in [0, 2] for unit vectors
            scored.append((cos_dist, doc))

        scored.sort(key=lambda x: x[0])
        top = scored[:n_results]

        return {
            "ids": [[d["id"] for _, d in top]],
            "documents": [[d["content"] for _, d in top]],
            "metadatas": [[d["metadata"] for _, d in top]],
            "distances": [[dist for dist, _ in top]],
        }

    def get_recent(self, collection_name: str, limit: int) -> dict:
        docs = sorted(
            self._store.get(collection_name, []),
            key=lambda d: d["created_at"],
            reverse=True,
        )[:limit]
        return {
            "ids": [[d["id"] for d in docs]],
            "documents": [[d["content"] for d in docs]],
            "metadatas": [[d["metadata"] for d in docs]],
            "distances": [[0.0] * len(docs)],
        }

    def count(self, collection_name: str) -> int:
        return len(self._store.get(collection_name, []))

    def upsert(
        self,
        collection_name: str,
        ids: list[str],
        texts: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict],
    ) -> None:
        store = self._store.setdefault(collection_name, [])
        existing = {d["id"]: i for i, d in enumerate(store)}
        for doc_id, content, emb, meta in zip(ids, texts, embeddings, metadatas):
            entry = {
                "id": doc_id,
                "content": content,
                "embedding": emb,
                "metadata": meta,
                "created_at": datetime.now(),
            }
            if doc_id in existing:
                store[existing[doc_id]] = entry
            else:
                existing[doc_id] = len(store)
                store.append(entry)

    def add(
        self,
        collection_name: str,
        ids: list[str],
        texts: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict],
    ) -> None:
        store = self._store.setdefault(collection_name, [])
        for doc_id, content, emb, meta in zip(ids, texts, embeddings, metadatas):
            store.append(
                {
                    "id": doc_id,
                    "content": content,
                    "embedding": emb,
                    "metadata": meta,
                    "created_at": datetime.now(),
                }
            )

    def delete_all(self, collection_name: str) -> int:
        count = len(self._store.get(collection_name, []))
        self._store[collection_name] = []
        return count


# Fixtures


@pytest.fixture
def fake_db() -> FakeSupabaseVectorDB:
    """Fresh in-memory vector store per test (no shared state)."""
    return FakeSupabaseVectorDB()


@pytest.fixture
def rag(fake_db: FakeSupabaseVectorDB) -> RAGService:
    """RAGService wired to fake_db. hf_api_key='' disables the reranker."""
    return RAGService(vectordb=fake_db, hf_api_key="")


# FakeSupabaseVectorDB unit tests


class TestSupabaseVectorDB:
    def test_embed_texts_returns_correct_count(self, fake_db: FakeSupabaseVectorDB):
        texts = ["cuenta de caja", "retencion en la fuente", "IVA descontable"]
        embeddings = fake_db.embed_texts(texts)
        assert len(embeddings) == 3

    def test_embed_texts_returns_correct_dimension(self, fake_db: FakeSupabaseVectorDB):
        vecs = fake_db.embed_texts(["honorarios consultoria"])
        assert len(vecs[0]) == 1024

    def test_embed_query_returns_list_of_floats(self, fake_db: FakeSupabaseVectorDB):
        vec = fake_db.embed_query("honorarios consultoria")
        assert isinstance(vec, list)
        assert all(isinstance(v, float) for v in vec)

    def test_embed_is_deterministic(self, fake_db: FakeSupabaseVectorDB):
        v1 = fake_db.embed_query("texto de prueba")
        v2 = fake_db.embed_query("texto de prueba")
        assert v1 == v2

    def test_count_returns_zero_for_unknown_collection(
        self, fake_db: FakeSupabaseVectorDB
    ):
        assert fake_db.count("does_not_exist") == 0

    def test_count_increments_after_upsert(self, fake_db: FakeSupabaseVectorDB):
        emb = fake_db.embed_texts(["test doc"])[0]
        fake_db.upsert(
            NORMATIVA_COLLECTION, ["test_1"], ["test doc"], [emb], [{"tipo": "test"}]
        )
        assert fake_db.count(NORMATIVA_COLLECTION) == 1

    def test_upsert_is_idempotent(self, fake_db: FakeSupabaseVectorDB):
        emb = fake_db.embed_texts(["same doc"])[0]
        fake_db.upsert(NORMATIVA_COLLECTION, ["doc_1"], ["same doc"], [emb], [{}])
        fake_db.upsert(NORMATIVA_COLLECTION, ["doc_1"], ["same doc"], [emb], [{}])
        assert fake_db.count(NORMATIVA_COLLECTION) == 1

    def test_delete_all_clears_collection(self, fake_db: FakeSupabaseVectorDB):
        emb = fake_db.embed_texts(["doc"])[0]
        fake_db.upsert(NORMATIVA_COLLECTION, ["id1"], ["doc"], [emb], [{}])
        fake_db.delete_all(NORMATIVA_COLLECTION)
        assert fake_db.count(NORMATIVA_COLLECTION) == 0

    def test_empresa_collection_name_sanitises_special_chars(self):
        name = empresa_collection_name("900-123.456/7")
        assert "/" not in name
        assert "." not in name
        assert "-" not in name

    def test_search_returns_compatible_dict(self, fake_db: FakeSupabaseVectorDB):
        emb = fake_db.embed_texts(["retencion proveedor"])[0]
        fake_db.upsert(
            NORMATIVA_COLLECTION, ["doc_1"], ["retencion proveedor"], [emb], [{}]
        )
        q_emb = fake_db.embed_query("retencion")
        result = fake_db.search(NORMATIVA_COLLECTION, q_emb, 1)
        assert "ids" in result and "documents" in result
        assert "metadatas" in result and "distances" in result
        assert len(result["ids"][0]) == 1


# RAGService unit tests


class TestRAGServiceNormativo:
    def _seed(self, db: FakeSupabaseVectorDB, docs: list[dict[str, Any]]) -> None:
        """Insert test documents into the normativa collection."""
        for doc in docs:
            emb = db.embed_texts([doc["text"]])[0]
            db.upsert(
                NORMATIVA_COLLECTION,
                [doc["id"]],
                [doc["text"]],
                [emb],
                [doc.get("meta") or {}],
            )

    def test_search_returns_list(self, rag: RAGService, fake_db: FakeSupabaseVectorDB):
        self._seed(
            fake_db,
            [
                {
                    "id": "puc_2365",
                    "text": "Retencion en la fuente cuenta 2365",
                    "meta": {"tipo": "puc"},
                }
            ],
        )
        results = rag.search_normativo("retencion honorarios", n_results=1)
        assert isinstance(results, list)

    def test_search_returns_rag_result_instances(
        self, rag: RAGService, fake_db: FakeSupabaseVectorDB
    ):
        self._seed(
            fake_db,
            [
                {
                    "id": "et_art_392",
                    "text": "Honorarios 11% retencion Art. 392",
                    "meta": {"tipo": "normativa"},
                }
            ],
        )
        results = rag.search_normativo("honorarios consultoria", n_results=1)
        assert len(results) == 1
        assert isinstance(results[0], RAGResult)

    def test_search_result_has_required_fields(
        self, rag: RAGService, fake_db: FakeSupabaseVectorDB
    ):
        self._seed(
            fake_db,
            [
                {
                    "id": "iva_468",
                    "text": "IVA tarifa general 19% Art. 468",
                    "meta": {"articulo": "Art. 468"},
                }
            ],
        )
        result = rag.search_normativo("IVA ventas", n_results=1)[0]
        assert result.doc_id
        assert result.content
        assert isinstance(result.metadata, dict)
        assert 0.0 <= result.score <= 1.0

    def test_search_empty_collection_returns_empty_list(self, rag: RAGService):
        results = rag.search_normativo("IVA", n_results=5)
        assert results == []

    def test_n_results_clamped_to_collection_size(
        self, rag: RAGService, fake_db: FakeSupabaseVectorDB
    ):
        self._seed(
            fake_db,
            [
                {"id": "doc_1", "text": "Caja cuenta 1105", "meta": {}},
                {"id": "doc_2", "text": "Bancos cuenta 1110", "meta": {}},
            ],
        )
        results = rag.search_normativo("activos disponibles", n_results=10)
        assert len(results) <= 2

    def test_score_is_between_zero_and_one(
        self, rag: RAGService, fake_db: FakeSupabaseVectorDB
    ):
        self._seed(
            fake_db,
            [
                {
                    "id": "renta",
                    "text": "impuesto de renta personas juridicas 35%",
                    "meta": {},
                }
            ],
        )
        result = rag.search_normativo("tarifa renta sociedades", n_results=1)[0]
        assert 0.0 <= result.score <= 1.0


class TestRAGServiceHistorico:
    NIT = "900123456"

    def test_search_historico_empty_returns_empty_list(self, rag: RAGService):
        results = rag.search_historico(self.NIT, query="factura servicios")
        assert results == []

    def test_add_and_search_historico(self, rag: RAGService):
        rag.add_empresa_doc(
            nit=self.NIT,
            text="Factura #1001 proveedor Claro Colombia por servicios de internet",
            metadata={"tipo": "factura", "proveedor": "Claro", "fecha": "2026-01-15"},
        )
        results = rag.search_historico(self.NIT, query="factura Claro internet")
        assert len(results) >= 1

    def test_search_historico_filters_by_nit(self, rag: RAGService):
        """Documents stored under NIT_A must NOT appear in NIT_B searches."""
        nit_a, nit_b = "111111111", "999999999"
        rag.add_empresa_doc(nit=nit_a, text="Extracto bancario NIT A enero 2026")
        results_b = rag.search_historico(nit_b, query="extracto bancario")
        assert results_b == []

    def test_search_historico_no_query_returns_docs(self, rag: RAGService):
        rag.add_empresa_doc(
            nit=self.NIT, text="Comprobante de egreso pago nomina enero"
        )
        results = rag.search_historico(self.NIT, query="", n_results=5)
        assert len(results) >= 1


class TestRAGServiceAddDoc:
    NIT = "800654321"

    def test_add_empresa_doc_returns_string_id(self, rag: RAGService):
        doc_id = rag.add_empresa_doc(
            nit=self.NIT,
            text="Nota credito proveedor Movistar por devolucion servicios",
        )
        assert isinstance(doc_id, str)
        assert len(doc_id) > 0

    def test_add_empresa_doc_increments_count(
        self, rag: RAGService, fake_db: FakeSupabaseVectorDB
    ):
        coll = empresa_collection_name(self.NIT)
        before = fake_db.count(coll)
        rag.add_empresa_doc(nit=self.NIT, text="RUT proveedor 800654321-4")
        after = fake_db.count(coll)
        assert after == before + 1

    def test_add_empresa_doc_with_explicit_id(self, rag: RAGService):
        explicit_id = "factura_2026_001"
        returned_id = rag.add_empresa_doc(
            nit=self.NIT,
            text="Factura de venta 001",
            doc_id=explicit_id,
        )
        assert returned_id == explicit_id

    def test_add_empresa_doc_stores_metadata(
        self, rag: RAGService, fake_db: FakeSupabaseVectorDB
    ):
        doc_id = rag.add_empresa_doc(
            nit=self.NIT,
            text="Factura electronica emitida enero 2026",
            metadata={"tipo": "factura", "mes": "enero"},
        )
        coll = empresa_collection_name(self.NIT)
        stored = next(d for d in fake_db._store[coll] if d["id"] == doc_id)
        assert stored["metadata"]["tipo"] == "factura"
        assert stored["metadata"]["mes"] == "enero"
        assert stored["metadata"]["nit"] == self.NIT

    def test_add_empty_text_raises_value_error(self, rag: RAGService):
        with pytest.raises(ValueError, match="empty"):
            rag.add_empresa_doc(nit=self.NIT, text="   ")


# RAGResult schema tests


class TestRAGResultSchema:
    def test_valid_rag_result(self):
        r = RAGResult(
            doc_id="abc123",
            content="Texto del documento",
            metadata={"tipo": "puc"},
            score=0.87,
        )
        assert r.doc_id == "abc123"
        assert r.score == 0.87

    def test_default_metadata_is_empty_dict(self):
        r = RAGResult(doc_id="x", content="texto", score=0.5)
        assert r.metadata == {}

    def test_score_rejects_values_above_one(self):
        with pytest.raises(Exception):
            RAGResult(doc_id="x", content="texto", score=1.5)

    def test_score_rejects_negative_values(self):
        with pytest.raises(Exception):
            RAGResult(doc_id="x", content="texto", score=-0.1)

    def test_rag_result_is_serialisable(self):
        r = RAGResult(doc_id="y", content="algo", metadata={"k": "v"}, score=0.3)
        as_dict = r.model_dump()
        assert as_dict["doc_id"] == "y"
        assert as_dict["score"] == 0.3


# Reranker tests (no HF API -- uses fallback path)


class TestReranker:
    def test_rerank_without_client_returns_top_n(self, rag: RAGService):
        """Without HF key, rerank() returns the first top_n preserving order."""
        docs = [
            RAGResult(doc_id=f"id_{i}", content=f"doc {i}", score=float(i) / 10)
            for i in range(5)
        ]
        result = rag.rerank("query", docs, top_n=3)
        assert len(result) == 3
        assert result == docs[:3]

    def test_rerank_empty_list_returns_empty(self, rag: RAGService):
        assert rag.rerank("query", [], top_n=3) == []

    def test_rerank_top_n_clamped_to_doc_count(self, rag: RAGService):
        docs = [RAGResult(doc_id="a", content="x", score=0.5)]
        result = rag.rerank("query", docs, top_n=10)
        assert len(result) == 1
