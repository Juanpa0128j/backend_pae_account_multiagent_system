"""Helpers for NIT normalization and validation."""

from __future__ import annotations


def normalize_nit(value: str) -> str:
    """Normalize a Colombian NIT and ensure it is not empty.

    Rules:
    - Strip surrounding spaces.
    - Remove inner spaces and dots.
    - Keep alphanumeric chars and hyphen for DV format compatibility.
    """
    if value is None:
        raise ValueError("NIT cannot be null")

    cleaned = str(value).strip().replace(" ", "").replace(".", "")
    if not cleaned:
        raise ValueError("NIT cannot be empty")

    return "".join(ch for ch in cleaned if ch.isalnum() or ch == "-")


def normalize_optional_nit(value: str | None) -> str | None:
    """Normalize NIT if provided; return None for None/blank inputs."""
    if value is None:
        return None

    raw = str(value).strip()
    if not raw:
        return None

    return normalize_nit(raw)
