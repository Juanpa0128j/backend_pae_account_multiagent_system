"""
Tests for the Vector DB and RAG Service layer.

Design principles:
  - NEVER calls the real Gemini Embeddings API.
  - Uses a ChromaDB PersistentClient isolated in pytest's tmp_path (fresh dir per test).
  - Injects fake vectordb and RAGService instances via pytest fixtures (no singleton factories).
  - Each test method is independent (no shared mutable state between tests).

Run:
    pytest tests/test_rag.py -v
"""

import hashlib
from typing import Any
from unittest.mock import MagicMock

import chromadb
import numpy as np
import pytest

from app.core.vectordb import ChromaVectorDB, empresa_collection_name
from app.services.rag_service import RAGResult, RAGService

# ─── Test embedding: deterministic, no API calls ──────────────────────────────


def _text_seed(text: str) -> int:
    """Derive a stable 32-bit RNG seed from text using SHA-256.

    Unlike Python's built-in hash(), SHA-256 is not affected by PYTHONHASHSEED,
    so embeddings are reproducible across processes and CI runs.
    """
    digest = hashlib.sha256(text.encode()).digest()
    return int.from_bytes(digest[:4], byteorder="little")


def _fake_embed_texts(texts: list[str]) -> list[list[float]]:
    """Return reproducible pseudo-random 128-dim embeddings (no API call).

    The embeddings are deterministic per input text, without any shared RNG state
    across tests.  This avoids test order-dependence while keeping outputs stable.
    """
    return [
        np.random.default_rng(seed=_text_seed(t)).random(128).tolist() for t in texts
    ]


def _fake_embed_query(text: str) -> list[float]:
    """Return a deterministic 128-dim embedding for the given query text."""
    return np.random.default_rng(seed=_text_seed(text)).random(128).tolist()


# ─── Fixture: in-memory ChromaVectorDB ───────────────────────────────────────


@pytest.fixture
def in_memory_vectordb(tmp_path) -> ChromaVectorDB:
    """
    ChromaVectorDB backed by a per-test PersistentClient in a temp directory.
    Using tmp_path (pytest built-in) guarantees full isolation between tests:
    each test gets its own empty ChromaDB with no shared state.
    No API calls — embeddings are mocked.
    """
    db = ChromaVectorDB.__new__(ChromaVectorDB)
    # tmp_path is a fresh unique directory created by pytest per test function
    db._client = chromadb.PersistentClient(path=str(tmp_path))

    mock_embeddings = MagicMock()
    mock_embeddings.embed_documents.side_effect = _fake_embed_texts
    mock_embeddings.embed_query.side_effect = _fake_embed_query
    db._embeddings = mock_embeddings

    return db


@pytest.fixture
def rag(in_memory_vectordb: ChromaVectorDB) -> RAGService:
    """RAGService wired to the in-memory vectordb fixture."""
    return RAGService(vectordb=in_memory_vectordb)


# ─── ChromaVectorDB unit tests ────────────────────────────────────────────────


class TestChromaVectorDB:
    def test_get_normativa_collection_creates_collection(self, in_memory_vectordb):
        col = in_memory_vectordb.get_normativa_collection()
        assert col is not None
        assert col.name == "normativa_colombia_v1"

    def test_get_normativa_collection_is_idempotent(self, in_memory_vectordb):
        col1 = in_memory_vectordb.get_normativa_collection()
        col2 = in_memory_vectordb.get_normativa_collection()
        assert col1.name == col2.name

    def test_get_empresa_collection_uses_nit(self, in_memory_vectordb):
        col = in_memory_vectordb.get_empresa_collection("900123456")
        assert "900123456" in col.name

    def test_empresa_collection_name_sanitises_special_chars(self):
        name = empresa_collection_name("900-123.456/7")
        assert "/" not in name
        assert "." not in name
        assert "-" not in name

    def test_embed_texts_returns_correct_count(self, in_memory_vectordb):
        texts = ["cuenta de caja", "retención en la fuente", "IVA descontable"]
        embeddings = in_memory_vectordb.embed_texts(texts)
        assert len(embeddings) == 3

    def test_embed_query_returns_list_of_floats(self, in_memory_vectordb):
        vec = in_memory_vectordb.embed_query("honorarios consultoría")
        assert isinstance(vec, list)
        assert all(isinstance(v, float) for v in vec)

    def test_list_collections_after_creation(self, in_memory_vectordb):
        in_memory_vectordb.get_normativa_collection()
        collections = in_memory_vectordb.list_collections()
        assert "normativa_colombia_v1" in collections

    def test_collection_count_returns_zero_for_unknown(self, in_memory_vectordb):
        count = in_memory_vectordb.collection_count("does_not_exist")
        assert count == 0

    def test_collection_count_increments_after_upsert(self, in_memory_vectordb):
        col = in_memory_vectordb.get_normativa_collection()
        embedding = in_memory_vectordb.embed_texts(["test doc"])[0]
        col.upsert(
            ids=["test_1"],
            documents=["test doc"],
            embeddings=[embedding],
            metadatas=[{"tipo": "test"}],
        )
        assert in_memory_vectordb.collection_count("normativa_colombia_v1") == 1


# ─── RAGService unit tests ────────────────────────────────────────────────────


class TestRAGServiceNormativo:
    def _seed_normativa(self, rag: RAGService, docs: list[dict[str, Any]]) -> None:
        """Insert test documents into the normativa collection."""
        col = rag._db.get_normativa_collection()
        for doc in docs:
            embedding = rag._db.embed_texts([doc["text"]])[0]
            # ChromaDB rejects empty dicts; use None for absent metadata
            meta = doc.get("meta") or None
            col.upsert(
                ids=[doc["id"]],
                documents=[doc["text"]],
                embeddings=[embedding],
                metadatas=[meta] if meta is not None else None,
            )

    def test_search_returns_list(self, rag: RAGService):
        self._seed_normativa(
            rag,
            [
                {
                    "id": "puc_2365",
                    "text": "Retención en la fuente cuenta 2365",
                    "meta": {"tipo": "puc"},
                },
            ],
        )
        results = rag.search_normativo("retención honorarios", n_results=1)
        assert isinstance(results, list)

    def test_search_returns_rag_result_instances(self, rag: RAGService):
        self._seed_normativa(
            rag,
            [
                {
                    "id": "et_art_392",
                    "text": "Honorarios 11% retención Art. 392",
                    "meta": {"tipo": "normativa"},
                },
            ],
        )
        results = rag.search_normativo("honorarios consultoría", n_results=1)
        assert len(results) == 1
        assert isinstance(results[0], RAGResult)

    def test_search_result_has_required_fields(self, rag: RAGService):
        self._seed_normativa(
            rag,
            [
                {
                    "id": "iva_468",
                    "text": "IVA tarifa general 19% Art. 468",
                    "meta": {"articulo": "Art. 468"},
                },
            ],
        )
        result = rag.search_normativo("IVA ventas", n_results=1)[0]
        assert result.doc_id
        assert result.content
        assert isinstance(result.metadata, dict)
        assert 0.0 <= result.score <= 1.0

    def test_search_empty_collection_returns_empty_list(self, rag: RAGService):
        # Don't seed anything
        results = rag.search_normativo("IVA", n_results=5)
        assert results == []

    def test_n_results_clamped_to_collection_size(self, rag: RAGService):
        self._seed_normativa(
            rag,
            [
                {"id": "doc_1", "text": "Caja cuenta 1105", "meta": {}},
                {"id": "doc_2", "text": "Bancos cuenta 1110", "meta": {}},
            ],
        )
        # Ask for 10 but only 2 exist
        results = rag.search_normativo("activos disponibles", n_results=10)
        assert len(results) <= 2

    def test_score_is_between_zero_and_one(self, rag: RAGService):
        self._seed_normativa(
            rag,
            [
                {
                    "id": "score_test",
                    "text": "impuesto de renta personas jurídicas 35%",
                    "meta": {},
                },
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
            nit=self.NIT, text="Comprobante de egreso pago nómina enero"
        )
        results = rag.search_historico(self.NIT, query="", n_results=5)
        assert len(results) >= 1


class TestRAGServiceAddDoc:
    NIT = "800654321"

    def test_add_empresa_doc_returns_string_id(self, rag: RAGService):
        doc_id = rag.add_empresa_doc(
            nit=self.NIT,
            text="Nota crédito proveedor Movistar por devolución servicios",
        )
        assert isinstance(doc_id, str)
        assert len(doc_id) > 0

    def test_add_empresa_doc_increments_count(self, rag: RAGService):
        col = rag._db.get_empresa_collection(self.NIT)
        before = col.count()
        rag.add_empresa_doc(nit=self.NIT, text="RUT proveedor 800654321-4")
        after = col.count()
        assert after == before + 1

    def test_add_empresa_doc_with_explicit_id(self, rag: RAGService):
        explicit_id = "factura_2026_001"
        returned_id = rag.add_empresa_doc(
            nit=self.NIT,
            text="Factura de venta 001",
            doc_id=explicit_id,
        )
        assert returned_id == explicit_id

    def test_add_empresa_doc_stores_metadata(self, rag: RAGService):
        doc_id = rag.add_empresa_doc(
            nit=self.NIT,
            text="Factura electrónica emitida enero 2026",
            metadata={"tipo": "factura", "mes": "enero"},
        )
        col = rag._db.get_empresa_collection(self.NIT)
        stored = col.get(ids=[doc_id], include=["metadatas"])
        meta = stored["metadatas"][0]
        assert meta["tipo"] == "factura"
        assert meta["mes"] == "enero"
        assert meta["nit"] == self.NIT

    def test_add_empty_text_raises_value_error(self, rag: RAGService):
        with pytest.raises(ValueError, match="empty"):
            rag.add_empresa_doc(nit=self.NIT, text="   ")


# ─── RAGResult schema tests ───────────────────────────────────────────────────


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
