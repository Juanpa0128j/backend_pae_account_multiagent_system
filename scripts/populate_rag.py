"""
Seed / populate the normativa pgvector collection in Supabase.

Run once before starting the API server (or whenever the source data changes):

    python scripts/populate_rag.py          # skip if already populated
    python scripts/populate_rag.py --force  # re-index even if already populated

What this script does:
  1. Reads data/puc_accounts.json (~41 PUC entries)
  2. Reads data/normativa_tributaria.json (~50 ET articles)
  3. Reads data/ley_43_1990.json (16 Ley 43/1990 PCGA principles)
  4. Embeds every entry via BAAI/bge-m3 (HuggingFace Inference API)
  5. Upserts into the `normativa_colombia_v1` collection in Supabase pgvector

The script is idempotent: without --force it skips collections that already
have content. With --force it deletes all documents and rebuilds from scratch.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

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


# Helpers


def load_json(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def build_puc_documents() -> tuple[list[str], list[str], list[dict]]:
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


def build_normativa_documents() -> tuple[list[str], list[str], list[dict]]:
    """Return (ids, texts, metadatas) for all normativa articles."""
    entries = load_json(DATA_DIR / "normativa_tributaria.json")
    ids, texts, metas = [], [], []

    for entry in entries:
        doc_id = entry["id"]
        text = (
            f"{entry['articulo']} -- {entry['titulo']}. "
            f"Fuente: {entry['fuente']}. "
            f"{entry['contenido']}"
        )
        meta = {
            "tipo": "normativa",
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


def build_ley43_documents() -> tuple[list[str], list[str], list[dict]]:
    """Return (ids, texts, metadatas) for all Ley 43/1990 PCGA principles."""
    entries = load_json(DATA_DIR / "ley_43_1990.json")
    ids, texts, metas = [], [], []

    for entry in entries:
        doc_id = entry["id"]
        text = (
            f"{entry['articulo']} -- {entry['titulo']}. "
            f"Fuente: {entry['fuente']}. "
            f"{entry['contenido']}"
        )
        meta = {
            "tipo": "normativa",
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

    # PUC accounts
    logger.info("Indexing PUC accounts ...")
    puc_ids, puc_texts, puc_metas = build_puc_documents()
    n_puc = upsert_batch(NORMATIVA_COLLECTION, puc_ids, puc_texts, puc_metas, vectordb)
    logger.info("  %d PUC accounts indexed.", n_puc)

    # Normativa articles
    logger.info("Indexing normativa tributaria ...")
    norm_ids, norm_texts, norm_metas = build_normativa_documents()
    n_norm = upsert_batch(
        NORMATIVA_COLLECTION, norm_ids, norm_texts, norm_metas, vectordb
    )
    logger.info("  %d normativa articles indexed.", n_norm)

    # Ley 43/1990 PCGA principles
    logger.info("Indexing Ley 43/1990 (PCGA) ...")
    ley43_ids, ley43_texts, ley43_metas = build_ley43_documents()
    n_ley43 = upsert_batch(
        NORMATIVA_COLLECTION, ley43_ids, ley43_texts, ley43_metas, vectordb
    )
    logger.info("  %d Ley 43/1990 principles indexed.", n_ley43)

    total = vectordb.count(NORMATIVA_COLLECTION)
    logger.info("-" * 50)
    logger.info(
        "Population complete. Collection '%s' now has %d documents.",
        NORMATIVA_COLLECTION,
        total,
    )


if __name__ == "__main__":
    main()
