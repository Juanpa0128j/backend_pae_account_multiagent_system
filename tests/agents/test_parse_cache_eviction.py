"""Tests for .parse_cache age-based eviction."""

from __future__ import annotations

import os
import time
from pathlib import Path

from app.agents import ingest_agent


def _make_cached_file(cache_dir: Path, name: str, age_days: float) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    f = cache_dir / name
    f.write_text("cached")
    old_mtime = time.time() - age_days * 86400
    os.utime(f, (old_mtime, old_mtime))
    return f


def test_prune_deletes_files_older_than_ttl(tmp_path: Path) -> None:
    # Arrange
    cache = tmp_path / ".parse_cache"
    old = _make_cached_file(cache, "old.md", age_days=45)
    fresh = _make_cached_file(cache, "fresh.md", age_days=1)

    # Act
    deleted = ingest_agent._prune_parse_cache(cache, ttl_days=30)

    # Assert
    assert deleted == 1
    assert not old.exists()
    assert fresh.exists()


def test_prune_returns_zero_when_cache_missing(tmp_path: Path) -> None:
    # Arrange
    missing = tmp_path / "missing"

    # Act
    deleted = ingest_agent._prune_parse_cache(missing)

    # Assert
    assert deleted == 0


def test_prune_returns_zero_when_cache_empty(tmp_path: Path) -> None:
    # Arrange
    cache = tmp_path / ".parse_cache"
    cache.mkdir()

    # Act
    deleted = ingest_agent._prune_parse_cache(cache)

    # Assert
    assert deleted == 0


def test_prune_keeps_all_when_all_fresh(tmp_path: Path) -> None:
    # Arrange
    cache = tmp_path / ".parse_cache"
    a = _make_cached_file(cache, "a.md", age_days=5)
    b = _make_cached_file(cache, "b.md", age_days=15)

    # Act
    deleted = ingest_agent._prune_parse_cache(cache, ttl_days=30)

    # Assert
    assert deleted == 0
    assert a.exists()
    assert b.exists()


def test_prune_skips_directories(tmp_path: Path) -> None:
    # Arrange
    cache = tmp_path / ".parse_cache"
    cache.mkdir()
    nested = cache / "nested_dir"
    nested.mkdir()
    old_mtime = time.time() - 60 * 86400
    os.utime(nested, (old_mtime, old_mtime))

    # Act
    deleted = ingest_agent._prune_parse_cache(cache, ttl_days=30)

    # Assert
    assert deleted == 0
    assert nested.exists()


def test_prune_continues_on_unlink_error(tmp_path: Path, monkeypatch) -> None:
    # Arrange — two old files; first unlink raises OSError, second succeeds
    cache = tmp_path / ".parse_cache"
    _make_cached_file(cache, "a.md", age_days=45)
    _make_cached_file(cache, "b.md", age_days=45)

    original_unlink = Path.unlink
    call_count = {"n": 0}

    def _flaky_unlink(self, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise OSError("permission denied")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _flaky_unlink)

    # Act
    deleted = ingest_agent._prune_parse_cache(cache, ttl_days=30)

    # Assert — one deleted, the other left in place due to error
    assert deleted == 1
    survivors = list(cache.iterdir())
    assert len(survivors) == 1
