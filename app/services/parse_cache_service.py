"""DB-backed cache of LlamaParse output.

Replaces the local-disk .parse_cache (ephemeral, per-instance) so a paid
parse survives restarts and is shared across instances. Content-addressed:
key = (sha256 of file bytes, parser mode). Best-effort by contract — every
failure degrades to "cache miss"; a cache problem must never break ingest.

Sessions are opened per call and never held across the parse itself.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.database import SessionLocal
from app.core.logger import get_logger
from app.models.database import ParseCache

logger = get_logger(__name__)

PARSE_CACHE_TTL_DAYS = 7
MAX_CACHED_TEXT_BYTES = 10 * 1024 * 1024


def get_cached_parse(
    content_sha256: str, parser_mode: str, *, session_factory=SessionLocal
) -> Optional[str]:
    try:
        db = session_factory()
    except Exception as err:  # noqa: BLE001 — best-effort cache
        logger.warning("parse_cache: session open failed on read: %s", err)
        return None
    try:
        row = db.get(ParseCache, (content_sha256, parser_mode))
        if row is not None:
            logger.info(
                "parse_cache: hit (hash=%s..., mode=%s)",
                content_sha256[:12],
                parser_mode,
            )
            return row.raw_text
        return None
    except Exception as err:  # noqa: BLE001 — best-effort cache
        logger.warning("parse_cache: read failed: %s", err)
        return None
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass


def store_parse(
    content_sha256: str,
    parser_mode: str,
    raw_text: str,
    *,
    session_factory=SessionLocal,
) -> None:
    if len(raw_text.encode("utf-8")) > MAX_CACHED_TEXT_BYTES:
        logger.info(
            "parse_cache: skipping oversized entry (%d bytes, hash=%s...)",
            len(raw_text.encode("utf-8")),
            content_sha256[:12],
        )
        return
    try:
        db = session_factory()
    except Exception as err:  # noqa: BLE001 — best-effort cache
        logger.warning("parse_cache: session open failed on write: %s", err)
        return
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=PARSE_CACHE_TTL_DAYS)
        db.query(ParseCache).filter(ParseCache.created_at < cutoff).delete(
            synchronize_session=False
        )
        if db.bind.dialect.name == "postgresql":
            db.execute(
                pg_insert(ParseCache)
                .values(
                    content_sha256=content_sha256,
                    parser_mode=parser_mode,
                    raw_text=raw_text,
                    created_at=datetime.now(timezone.utc),
                )
                .on_conflict_do_nothing(
                    index_elements=["content_sha256", "parser_mode"]
                )
            )
        else:
            # sqlite (tests): emulate DO NOTHING
            if db.get(ParseCache, (content_sha256, parser_mode)) is None:
                db.add(
                    ParseCache(
                        content_sha256=content_sha256,
                        parser_mode=parser_mode,
                        raw_text=raw_text,
                        created_at=datetime.now(timezone.utc),
                    )
                )
        db.commit()
    except Exception as err:  # noqa: BLE001 — best-effort cache
        logger.warning("parse_cache: write failed: %s", err)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass
