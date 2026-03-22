"""
Expanded tests for Week 11 RAG features:
  - Extended data files (normativa_tributaria.json: 50 entries, ley_43_1990.json: 16 entries)
  - FakeSupabaseVectorDB.search_hybrid() in-memory implementation
  - RAGService.search_normativo(hybrid=True/False)
  - Edge cases: empty collections, no FTS matches, single-word queries, n_results limits

Design principles:
  - NEVER calls the HuggingFace API or any external service.
  - Uses an in-memory ExtendedFakeDB injected via pytest fixtures.
  - Each test method is independent (no shared mutable state between tests).

Run:
    pytest tests/test_rag_expanded.py -v
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from app.core.vectordb import NORMATIVA_COLLECTION
from app.services.rag_service import RAGResult, RAGService

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


# Deterministic fake embeddings (same implementation as test_rag.py)


def _text_seed(text: str) -> int:
    digest = hashlib.sha256(text.encode()).digest()
    return int.from_bytes(digest[:4], byteorder="little")


def _fake_embed(text: str) -> list[float]:
    return np.random.default_rng(seed=_text_seed(text)).random(1024).tolist()


# Extended FakeDB with search_hybrid


class ExtendedFakeDB:
    """
    In-memory vector store that adds search_hybrid() on top of the
    standard FakeSupabaseVectorDB functionality.

    FTS is simulated as word-presence matching (case-insensitive, split on whitespace).
    RRF fusion and distance normalisation mirror the production SQL implementation.
    """

    def __init__(self):
        self._store: dict[str, list[dict]] = {}

    # Embedding helpers

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [_fake_embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return _fake_embed(text)

    # Read helpers

    def search(
        self, collection_name: str, query_embedding: list[float], n_results: int
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
            scored.append((1.0 - cos_sim, doc))
        scored.sort(key=lambda x: x[0])
        top = scored[:n_results]
        return {
            "ids": [[d["id"] for _, d in top]],
            "documents": [[d["content"] for _, d in top]],
            "metadatas": [[d["metadata"] for _, d in top]],
            "distances": [[dist for dist, _ in top]],
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
        In-memory hybrid search: word-matching (FTS proxy) + cosine (vector),
        fused with RRF. Distance normalisation matches the SQL implementation.
        """
        docs = self._store.get(collection_name, [])
        if not docs:
            return {
                "ids": [[]],
                "documents": [[]],
                "metadatas": [[]],
                "distances": [[]],
            }

        query_words = set(query_text.lower().split())

        # FTS ranking: count query word hits in document content
        fts_hits = []
        for doc in docs:
            content_lower = doc["content"].lower()
            hit_count = sum(1 for w in query_words if w in content_lower)
            if hit_count > 0:
                fts_hits.append((hit_count, doc["id"]))
        fts_hits.sort(key=lambda x: -x[0])
        fts_ranks = {doc_id: rank + 1 for rank, (_, doc_id) in enumerate(fts_hits)}

        # Vector ranking: cosine similarity
        q = np.array(query_embedding)
        vec_scored = []
        for doc in docs:
            e = np.array(doc["embedding"])
            norm = np.linalg.norm(q) * np.linalg.norm(e)
            cos_sim = float(np.dot(q, e) / (norm + 1e-10))
            vec_scored.append((cos_sim, doc["id"]))
        vec_scored.sort(key=lambda x: -x[0])
        vec_ranks = {doc_id: rank + 1 for rank, (_, doc_id) in enumerate(vec_scored)}

        # RRF fusion
        all_ids = set(fts_ranks) | set(vec_ranks)
        rrf_scores: dict[str, float] = {}
        for doc_id in all_ids:
            score = 0.0
            if doc_id in fts_ranks:
                score += 1.0 / (rrf_k + fts_ranks[doc_id])
            if doc_id in vec_ranks:
                score += 1.0 / (rrf_k + vec_ranks[doc_id])
            rrf_scores[doc_id] = score

        ranked = sorted(rrf_scores.items(), key=lambda x: -x[1])[:n_results]

        id_to_doc = {d["id"]: d for d in docs}
        max_rrf = 2.0 / (rrf_k + 1)
        ids, contents, metas, dists = [], [], [], []
        for doc_id, rrf_score in ranked:
            doc = id_to_doc[doc_id]
            ids.append(doc_id)
            contents.append(doc["content"])
            metas.append(doc["metadata"])
            dist = 2.0 * (1.0 - rrf_score / max_rrf)
            dists.append(max(0.0, min(2.0, dist)))

        return {
            "ids": [ids],
            "documents": [contents],
            "metadatas": [metas],
            "distances": [dists],
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
def ext_db() -> ExtendedFakeDB:
    """Fresh extended in-memory vector store per test."""
    return ExtendedFakeDB()


@pytest.fixture
def rag(ext_db: ExtendedFakeDB) -> RAGService:
    """RAGService wired to ext_db. hf_api_key='' disables the reranker."""
    return RAGService(vectordb=ext_db, hf_api_key="")


def _seed_collection(db: ExtendedFakeDB, n: int = 10) -> list[dict]:
    """Insert n synthetic Norman docs into NORMATIVA_COLLECTION. Returns the inserted docs."""
    topics = [
        ("ET_ART_392", "retención honorarios profesionales 11% contadores"),
        ("ET_ART_383", "retención salarios empleados nómina"),
        ("ET_ART_420", "IVA hecho generador ventas servicios importaciones"),
        ("ET_ART_107", "gastos deducibles relación causalidad utilidad empresa"),
        ("ET_ART_240", "impuesto renta tarifa 35% personas jurídicas"),
        ("ET_ART_206", "rentas exentas trabajo 25% 240 UVT empleados"),
        ("ET_ART_260", "precios transferencia vinculados exterior plena competencia"),
        ("PUC_1105", "cuenta caja menor fondos efectivo disponible"),
        ("ET_ART_365", "retención fuente mecanismo recaudo anticipo"),
        ("ET_ART_714", "firmeza declaración tributaria 3 años prescripción"),
    ]
    topics = topics[:n]
    ids = [t[0] for t in topics]
    texts = [t[1] for t in topics]
    metas = [{"tipo": "normativa", "fuente": "ET"} for _ in topics]
    embs = db.embed_texts(texts)
    db.upsert(NORMATIVA_COLLECTION, ids, texts, embs, metas)
    return [{"id": i, "content": c} for i, c in zip(ids, texts)]


# Class 1: search_hybrid unit tests on ExtendedFakeDB


class TestSearchHybrid:
    def test_returns_expected_dict_keys(self, ext_db: ExtendedFakeDB):
        _seed_collection(ext_db)
        qemb = ext_db.embed_query("retención honorarios")
        result = ext_db.search_hybrid(
            NORMATIVA_COLLECTION, "retención honorarios", qemb, 5
        )
        assert set(result.keys()) == {"ids", "documents", "metadatas", "distances"}

    def test_ids_and_docs_same_length(self, ext_db: ExtendedFakeDB):
        _seed_collection(ext_db)
        qemb = ext_db.embed_query("IVA servicios")
        result = ext_db.search_hybrid(NORMATIVA_COLLECTION, "IVA servicios", qemb, 5)
        assert len(result["ids"][0]) == len(result["documents"][0])
        assert len(result["ids"][0]) == len(result["distances"][0])

    def test_respects_n_results_limit(self, ext_db: ExtendedFakeDB):
        _seed_collection(ext_db, n=10)
        qemb = ext_db.embed_query("renta")
        result = ext_db.search_hybrid(NORMATIVA_COLLECTION, "renta", qemb, 3)
        assert len(result["ids"][0]) <= 3

    def test_empty_collection_returns_empty(self, ext_db: ExtendedFakeDB):
        qemb = ext_db.embed_query("cualquier cosa")
        result = ext_db.search_hybrid(NORMATIVA_COLLECTION, "cualquier cosa", qemb, 5)
        assert result["ids"] == [[]]
        assert result["documents"] == [[]]
        assert result["distances"] == [[]]

    def test_distances_in_valid_range(self, ext_db: ExtendedFakeDB):
        _seed_collection(ext_db)
        qemb = ext_db.embed_query("retención")
        result = ext_db.search_hybrid(NORMATIVA_COLLECTION, "retención", qemb, 5)
        for dist in result["distances"][0]:
            assert 0.0 <= dist <= 2.0, f"Distance {dist} out of [0, 2] range"

    def test_no_fts_match_still_returns_vector_results(self, ext_db: ExtendedFakeDB):
        """Query with no matching words should still produce vector-based results."""
        _seed_collection(ext_db)
        qemb = ext_db.embed_query("xyzzy zork none")  # guaranteed no FTS match
        result = ext_db.search_hybrid(NORMATIVA_COLLECTION, "xyzzy zork none", qemb, 3)
        # Only vec_cte contributes, so we still get up to n_results vector hits
        assert len(result["ids"][0]) > 0

    def test_best_result_has_lowest_distance(self, ext_db: ExtendedFakeDB):
        _seed_collection(ext_db)
        qemb = ext_db.embed_query("honorarios")
        result = ext_db.search_hybrid(NORMATIVA_COLLECTION, "honorarios", qemb, 5)
        dists = result["distances"][0]
        assert dists == sorted(dists), (
            "Hybrid results should be ordered by ascending distance"
        )

    def test_keyword_match_boosts_ranking(self, ext_db: ExtendedFakeDB):
        """A document whose content contains the query words should rank near the top."""
        _seed_collection(ext_db)
        qemb = ext_db.embed_query("honorarios")
        result = ext_db.search_hybrid(NORMATIVA_COLLECTION, "honorarios", qemb, 5)
        top_ids = result["ids"][0]
        # ET_ART_392 contains "honorarios" explicitly
        assert "ET_ART_392" in top_ids


# Class 2: RAGService with hybrid=True/False


class TestRAGServiceHybrid:
    def test_hybrid_true_returns_rag_results(
        self, rag: RAGService, ext_db: ExtendedFakeDB
    ):
        _seed_collection(ext_db)
        results = rag.search_normativo("retención honorarios", n_results=3, hybrid=True)
        assert isinstance(results, list)
        assert all(isinstance(r, RAGResult) for r in results)

    def test_hybrid_false_returns_rag_results(
        self, rag: RAGService, ext_db: ExtendedFakeDB
    ):
        _seed_collection(ext_db)
        results = rag.search_normativo(
            "retención honorarios", n_results=3, hybrid=False
        )
        assert isinstance(results, list)
        assert all(isinstance(r, RAGResult) for r in results)

    def test_n_results_respected_with_hybrid(
        self, rag: RAGService, ext_db: ExtendedFakeDB
    ):
        _seed_collection(ext_db, n=10)
        results = rag.search_normativo("renta", n_results=2, hybrid=True)
        assert len(results) <= 2

    def test_n_results_respected_without_hybrid(
        self, rag: RAGService, ext_db: ExtendedFakeDB
    ):
        _seed_collection(ext_db, n=10)
        results = rag.search_normativo("renta", n_results=2, hybrid=False)
        assert len(results) <= 2

    def test_scores_in_zero_one_range_with_hybrid(
        self, rag: RAGService, ext_db: ExtendedFakeDB
    ):
        _seed_collection(ext_db)
        results = rag.search_normativo("IVA ventas", n_results=5, hybrid=True)
        for r in results:
            assert 0.0 <= r.score <= 1.0, f"Score {r.score} out of [0, 1]"

    def test_scores_in_zero_one_range_without_hybrid(
        self, rag: RAGService, ext_db: ExtendedFakeDB
    ):
        _seed_collection(ext_db)
        results = rag.search_normativo("IVA ventas", n_results=5, hybrid=False)
        for r in results:
            assert 0.0 <= r.score <= 1.0, f"Score {r.score} out of [0, 1]"

    def test_rag_result_schema_with_hybrid(
        self, rag: RAGService, ext_db: ExtendedFakeDB
    ):
        _seed_collection(ext_db)
        results = rag.search_normativo("gastos deducibles", n_results=3, hybrid=True)
        for r in results:
            assert r.doc_id
            assert isinstance(r.content, str) and r.content
            assert isinstance(r.metadata, dict)
            assert isinstance(r.score, float)

    def test_hybrid_default_is_true(self, rag: RAGService, ext_db: ExtendedFakeDB):
        """Calling search_normativo() without hybrid arg should behave as hybrid=True."""
        _seed_collection(ext_db)
        # Should not raise; hybrid calls search_hybrid which ext_db has
        results = rag.search_normativo("retención")
        assert isinstance(results, list)

    def test_empty_collection_returns_empty_list_hybrid(self, rag: RAGService):
        results = rag.search_normativo("cualquier consulta", n_results=5, hybrid=True)
        assert results == []

    def test_fallback_to_vector_when_no_fts_matches(
        self, rag: RAGService, ext_db: ExtendedFakeDB
    ):
        """Hybrid should fall back to vector-only when FTS yields no matches."""
        _seed_collection(ext_db, n=5)
        # Query with no words that appear in any seeded document
        results = rag.search_normativo("zzzxxx qqq abstract", hybrid=True, n_results=3)
        # Should still return results via the vector fallback
        assert len(results) > 0


# Class 3: Edge cases


class TestHybridEdgeCases:
    def test_single_result_collection_hybrid(
        self, ext_db: ExtendedFakeDB, rag: RAGService
    ):
        emb = ext_db.embed_texts(["retención honorarios contadores"])[0]
        ext_db.upsert(
            NORMATIVA_COLLECTION,
            ["only_doc"],
            ["retención honorarios contadores"],
            [emb],
            [{}],
        )
        results = rag.search_normativo("honorarios", n_results=3, hybrid=True)
        assert len(results) == 1

    def test_n_results_larger_than_collection_hybrid(
        self, ext_db: ExtendedFakeDB, rag: RAGService
    ):
        _seed_collection(ext_db, n=3)
        results = rag.search_normativo("renta", n_results=10, hybrid=True)
        assert len(results) <= 3

    def test_search_hybrid_returns_list_not_nested(self, ext_db: ExtendedFakeDB):
        """ids[0] must be a list of strings, not a list of lists."""
        _seed_collection(ext_db)
        qemb = ext_db.embed_query("renta")
        result = ext_db.search_hybrid(NORMATIVA_COLLECTION, "renta", qemb, 3)
        assert isinstance(result["ids"][0], list)
        for item in result["ids"][0]:
            assert isinstance(item, str)

    def test_hybrid_does_not_return_duplicates(self, ext_db: ExtendedFakeDB):
        """A document should not appear twice even if it ranks highly in both FTS and vec."""
        _seed_collection(ext_db, n=5)
        qemb = ext_db.embed_query("retención")
        result = ext_db.search_hybrid(NORMATIVA_COLLECTION, "retención", qemb, 5)
        ids = result["ids"][0]
        assert len(ids) == len(set(ids)), "Duplicate document IDs in hybrid results"

    def test_search_hybrid_with_multiword_query(self, ext_db: ExtendedFakeDB):
        _seed_collection(ext_db)
        qemb = ext_db.embed_query("retención en la fuente honorarios profesionales")
        result = ext_db.search_hybrid(
            NORMATIVA_COLLECTION,
            "retención en la fuente honorarios profesionales",
            qemb,
            3,
        )
        assert len(result["ids"][0]) > 0

    def test_search_normativo_hybrid_fallback_when_method_absent(self):
        """If the DB does not have search_hybrid, search_normativo must fall back silently."""
        from tests.features.test_rag_feature import FakeSupabaseVectorDB  # type: ignore

        plain_db = FakeSupabaseVectorDB()
        rag = RAGService(vectordb=plain_db, hf_api_key="")

        texts = ["retención honorarios", "IVA ventas"]
        embs = plain_db.embed_texts(texts)
        plain_db.upsert(NORMATIVA_COLLECTION, ["d1", "d2"], texts, embs, [{}, {}])

        # hybrid=True but plain_db has no search_hybrid -> falls back to vector
        results = rag.search_normativo("honorarios", n_results=2, hybrid=True)
        assert isinstance(results, list)
        assert len(results) > 0


# Class 4: Data file integrity tests (no DB needed)


class TestDataFileIntegrity:
    def test_normativa_tributaria_has_50_entries(self):
        data = json.loads(
            (DATA_DIR / "normativa_tributaria.json").read_text(encoding="utf-8")
        )
        assert len(data) == 50, f"Expected 50 entries, got {len(data)}"

    def test_ley_43_has_16_entries(self):
        data = json.loads((DATA_DIR / "ley_43_1990.json").read_text(encoding="utf-8"))
        assert len(data) == 16, f"Expected 16 entries, got {len(data)}"

    def test_normativa_no_duplicate_ids(self):
        data = json.loads(
            (DATA_DIR / "normativa_tributaria.json").read_text(encoding="utf-8")
        )
        ids = [e["id"] for e in data]
        assert len(ids) == len(set(ids)), (
            f"Duplicate IDs: {[i for i in ids if ids.count(i) > 1]}"
        )

    def test_ley_43_no_duplicate_ids(self):
        data = json.loads((DATA_DIR / "ley_43_1990.json").read_text(encoding="utf-8"))
        ids = [e["id"] for e in data]
        assert len(ids) == len(set(ids)), (
            f"Duplicate IDs: {[i for i in ids if ids.count(i) > 1]}"
        )

    def test_normativa_all_required_fields_present(self):
        required = {"id", "fuente", "articulo", "titulo", "contenido", "tags"}
        data = json.loads(
            (DATA_DIR / "normativa_tributaria.json").read_text(encoding="utf-8")
        )
        for entry in data:
            missing = required - entry.keys()
            assert not missing, f"Entry {entry.get('id')} missing fields: {missing}"

    def test_ley_43_all_required_fields_present(self):
        required = {"id", "fuente", "articulo", "titulo", "contenido", "tags"}
        data = json.loads((DATA_DIR / "ley_43_1990.json").read_text(encoding="utf-8"))
        for entry in data:
            missing = required - entry.keys()
            assert not missing, f"Entry {entry.get('id')} missing fields: {missing}"

    def test_normativa_all_ids_nonempty_strings(self):
        data = json.loads(
            (DATA_DIR / "normativa_tributaria.json").read_text(encoding="utf-8")
        )
        for entry in data:
            assert isinstance(entry["id"], str) and entry["id"].strip(), (
                f"Empty or non-string ID: {entry['id']!r}"
            )

    def test_ley_43_ids_start_with_ley43(self):
        data = json.loads((DATA_DIR / "ley_43_1990.json").read_text(encoding="utf-8"))
        for entry in data:
            assert entry["id"].startswith("LEY43_"), (
                f"ID {entry['id']!r} does not start with 'LEY43_'"
            )

    def test_normativa_all_contenido_nonempty(self):
        data = json.loads(
            (DATA_DIR / "normativa_tributaria.json").read_text(encoding="utf-8")
        )
        for entry in data:
            assert entry["contenido"].strip(), f"Empty contenido in entry {entry['id']}"

    def test_ley_43_all_contenido_nonempty(self):
        data = json.loads((DATA_DIR / "ley_43_1990.json").read_text(encoding="utf-8"))
        for entry in data:
            assert entry["contenido"].strip(), f"Empty contenido in entry {entry['id']}"

    def test_normativa_all_tags_are_lists(self):
        data = json.loads(
            (DATA_DIR / "normativa_tributaria.json").read_text(encoding="utf-8")
        )
        for entry in data:
            assert isinstance(entry["tags"], list), (
                f"tags field in {entry['id']} is not a list"
            )

    def test_ley_43_fuente_is_consistent(self):
        data = json.loads((DATA_DIR / "ley_43_1990.json").read_text(encoding="utf-8"))
        for entry in data:
            assert entry["fuente"] == "Ley 43 de 1990", (
                f"Unexpected fuente in {entry['id']}: {entry['fuente']!r}"
            )

    def test_pcga_articles_35_to_46_all_present(self):
        data = json.loads((DATA_DIR / "ley_43_1990.json").read_text(encoding="utf-8"))
        ids = {e["id"] for e in data}
        pcga_ids = {f"LEY43_ART_{n}" for n in range(35, 47)}
        assert pcga_ids <= ids, f"Missing PCGA IDs: {pcga_ids - ids}"

    def test_new_et_articles_include_key_articles(self):
        data = json.loads(
            (DATA_DIR / "normativa_tributaria.json").read_text(encoding="utf-8")
        )
        ids = {e["id"] for e in data}
        key_articles = {
            "ET_ART_26",
            "ET_ART_365",
            "ET_ART_420",
            "ET_ART_447",
            "ET_ART_714",
            "ET_ART_206",
            "ET_ART_260",
        }
        assert key_articles <= ids, f"Missing ET articles: {key_articles - ids}"

    def test_puc_json_still_intact(self):
        data = json.loads((DATA_DIR / "puc_accounts.json").read_text(encoding="utf-8"))
        assert len(data) >= 40, (
            f"puc_accounts.json has fewer than 40 entries: {len(data)}"
        )
