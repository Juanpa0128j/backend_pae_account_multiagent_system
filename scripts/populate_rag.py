"""
Seed / populate the normativa pgvector collection in Supabase.

Run once before starting the API server (or whenever the source data changes):

    python scripts/populate_rag.py          # skip if already populated
    python scripts/populate_rag.py --force  # re-index even if already populated

What this script does:
  1. Reads data/puc_accounts.json (~41 PUC entries)
  2. Reads data/normativa_tributaria.json (~50 ET articles)
  3. Reads data/ley_43_1990.json (16 Ley 43/1990 PCGA principles)
  4. Reads data/retencion_fuente_2026.json (~39 H&G retention entries 2026)
  5. Reads data/calendario_dian_2026.json (~26 DIAN national deadlines)
  6. Reads data/calendario_bogota_2026.json (~12 Bogotá distrital deadlines)
  7. Reads data/calendario_santa_marta_2026.json (~12 Santa Marta deadlines)
  8. Embeds every entry via BAAI/bge-m3 (HuggingFace Inference API)
  9. Upserts into the `normativa_colombia_v1` collection in Supabase pgvector

Adding a new source: write a builder returning (ids, texts, metas) and append
a tuple ``(label, build_fn)`` to ``RAG_SOURCES`` below. ``main()`` picks it up.

The script is idempotent: without --force it skips collections that already
have content. With --force it deletes all documents and rebuilds from scratch.
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Callable

# Add project root to sys.path so `app.*` imports work
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from app.core.config import get_settings  # noqa: E402
from app.core.vectordb import NORMATIVA_COLLECTION, get_vectordb  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DATA_DIR = PROJECT_ROOT / "data"

BuilderResult = tuple[list[str], list[str], list[dict]]


# Helpers


def load_json(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _build_normativa_like_documents(filename: str, tipo: str) -> BuilderResult:
    """Generic builder for JSONs sharing the normativa schema:
    {id, fuente, articulo, titulo, contenido, tags}.
    """
    entries = load_json(DATA_DIR / filename)
    ids, texts, metas = [], [], []

    for entry in entries:
        doc_id = entry["id"]
        text = (
            f"{entry['articulo']} -- {entry['titulo']}. "
            f"Fuente: {entry['fuente']}. "
            f"{entry['contenido']}"
        )
        meta = {
            "tipo": tipo,
            "id": entry["id"],
            "articulo": entry["articulo"],
            "titulo": entry["titulo"],
            "fuente": entry["fuente"],
            "tags": ", ".join(entry.get("tags", [])),
        }
        ids.append(doc_id)
        texts.append(text)
        metas.append(meta)

    return ids, texts, metas


def build_puc_documents() -> BuilderResult:
    """Return (ids, texts, metadatas) for all PUC accounts."""
    entries = load_json(DATA_DIR / "puc_accounts.json")
    ids, texts, metas = [], [], []

    for entry in entries:
        doc_id = f"puc_{entry['codigo']}"
        text = (
            f"Cuenta PUC {entry['codigo']} - {entry['nombre']}. "
            f"Clase: {entry['clase']}. Grupo: {entry['grupo']}. "
            f"Naturaleza: {entry['naturaleza']}. "
            f"{entry['descripcion']}"
        )
        meta = {
            "tipo": "puc",
            "codigo": entry["codigo"],
            "nombre": entry["nombre"],
            "clase": entry["clase"],
            "grupo": entry["grupo"],
            "naturaleza": entry["naturaleza"],
        }
        ids.append(doc_id)
        texts.append(text)
        metas.append(meta)

    return ids, texts, metas


def build_normativa_documents() -> BuilderResult:
    """Return (ids, texts, metadatas) for all normativa articles."""
    return _build_normativa_like_documents("normativa_tributaria.json", "normativa")


def build_ley43_documents() -> BuilderResult:
    """Return (ids, texts, metadatas) for all Ley 43/1990 PCGA principles."""
    return _build_normativa_like_documents("ley_43_1990.json", "normativa")


def build_retencion_2026_documents() -> BuilderResult:
    """Return (ids, texts, metadatas) for the H&G 2026 retention table."""
    return _build_normativa_like_documents(
        "retencion_fuente_2026.json", "retencion_2026"
    )


def build_calendario_dian_2026_documents() -> BuilderResult:
    """Return (ids, texts, metadatas) for the DIAN 2026 national tax calendar."""
    return _build_normativa_like_documents(
        "calendario_dian_2026.json", "calendario_dian"
    )


def build_calendario_bogota_2026_documents() -> BuilderResult:
    """Return (ids, texts, metadatas) for the Bogotá D.C. 2026 distrital calendar."""
    return _build_normativa_like_documents(
        "calendario_bogota_2026.json", "calendario_bogota"
    )


def build_calendario_santa_marta_2026_documents() -> BuilderResult:
    """Return (ids, texts, metadatas) for the Santa Marta D.T.C.H. 2026 calendar."""
    return _build_normativa_like_documents(
        "calendario_santa_marta_2026.json", "calendario_santa_marta"
    )


# Registry of all RAG sources to index. Adding a new source: append a tuple
# ``(label, build_fn)`` here. ``main()`` iterates this list — no other change
# required.
RAG_SOURCES: list[tuple[str, Callable[[], BuilderResult]]] = [
    ("PUC accounts", build_puc_documents),
    ("Normativa tributaria", build_normativa_documents),
    ("Ley 43/1990 PCGA", build_ley43_documents),
    ("Tabla retención 2026 (H&G)", build_retencion_2026_documents),
    ("Calendario DIAN 2026", build_calendario_dian_2026_documents),
    ("Calendario Bogotá 2026", build_calendario_bogota_2026_documents),
    ("Calendario Santa Marta 2026", build_calendario_santa_marta_2026_documents),
]


def upsert_batch(
    collection_name: str,
    ids: list[str],
    texts: list[str],
    metas: list[dict],
    vectordb,
    batch_size: int = 5,
) -> int:
    """Embed and upsert documents in small batches (respects HF API rate limits).

    Returns the total number of documents inserted.
    """
    total = 0
    for i in range(0, len(ids), batch_size):
        batch_ids = ids[i : i + batch_size]
        batch_texts = texts[i : i + batch_size]
        batch_metas = metas[i : i + batch_size]

        logger.info(
            "  Embedding and upserting batch %d-%d ...", i + 1, i + len(batch_ids)
        )
        embeddings = vectordb.embed_texts(batch_texts)
        vectordb.upsert(
            collection_name, batch_ids, batch_texts, embeddings, batch_metas
        )
        total += len(batch_ids)

    return total


# Main


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Populate the normativa pgvector collection in Supabase."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete and re-create the collection even if it already has data.",
    )
    args = parser.parse_args()

    settings = get_settings()
    if not settings.huggingface_api_key:
        logger.error(
            "HUGGINGFACE_API_KEY is not set. Add it to your .env file and retry."
        )
        sys.exit(1)

    vectordb = get_vectordb()
    existing = vectordb.count(NORMATIVA_COLLECTION)

    if existing > 0 and not args.force:
        logger.info(
            "Collection '%s' already has %d documents. Use --force to re-index.",
            NORMATIVA_COLLECTION,
            existing,
        )
        sys.exit(0)

    if args.force and existing > 0:
        logger.warning("--force: deleting %d existing documents ...", existing)
        vectordb.delete_all(NORMATIVA_COLLECTION)
        logger.info("Collection cleared.")

    total_indexed = 0
    for label, build_fn in RAG_SOURCES:
        logger.info("Indexing %s ...", label)
        ids, texts, metas = build_fn()
        n = upsert_batch(NORMATIVA_COLLECTION, ids, texts, metas, vectordb)
        logger.info("  %d documents indexed.", n)
        total_indexed += n

    total = vectordb.count(NORMATIVA_COLLECTION)
    logger.info("-" * 50)
    logger.info(
        "Population complete. Collection '%s' now has %d documents (indexed %d in this run).",
        NORMATIVA_COLLECTION,
        total,
        total_indexed,
    )


if __name__ == "__main__":
    main()
