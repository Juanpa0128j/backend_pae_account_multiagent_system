#!/usr/bin/env python3
"""Fail when a PR adds an Alembic migration spliced *before* an already-released
revision (its ``down_revision`` points into the base branch's past instead of its
head).

Why this matters: such a migration runs fine on a FRESH database (CI starts from
base, so the whole chain — including the spliced revision — executes). But any
database already past that point (persisted dev volumes, Supabase prod) treats it
as "already applied" and SKIPS it, leaving the schema inconsistent (missing
columns/tables → runtime errors). New migrations must always chain off the base
branch's head (or off another new migration added in the same PR).

This is the static counterpart of the runtime "incremental upgrade" CI job; it
gives a fast, clear error pointing at the offending file.

Usage:
    check_migration_linearity.py [base_ref]      # default: origin/main
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

VERSIONS_DIR = "alembic/versions"

_REV_RE = re.compile(r"^revision[^=\n]*=\s*[\"']([^\"']+)[\"']", re.M)
# down_revision RHS may span multiple lines (merge migrations use a tuple), so
# capture everything up to the next module-level assignment (branch_labels /
# depends_on / another top-level name).
_DOWN_RE = re.compile(
    r"^down_revision[^=\n]*=\s*(.+?)(?=\n(?:branch_labels|depends_on|revision|\w))",
    re.M | re.S,
)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout


def _parse(text: str) -> tuple[str | None, set[str]]:
    """Return (revision, {down_revisions}) parsed from a migration's source."""
    rev_match = _REV_RE.search(text)
    revision = rev_match.group(1) if rev_match else None
    down_match = _DOWN_RE.search(text)
    parents = (
        set(re.findall(r"[\"']([^\"']+)[\"']", down_match.group(1)))
        if down_match
        else set()
    )
    return revision, parents


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "origin/main"

    # 1) Map the base branch's migration chain and find its head(s).
    base_files = [
        f
        for f in _git("ls-tree", "-r", "--name-only", base, "--", VERSIONS_DIR).split()
        if f.endswith(".py") and "__init__" not in f
    ]
    base_revs: set[str] = set()
    base_downs: set[str] = set()
    for f in base_files:
        rev, parents = _parse(_git("show", f"{base}:{f}"))
        if rev:
            base_revs.add(rev)
        base_downs |= parents
    base_heads = base_revs - base_downs  # revisions nobody else points down to

    # 2) New migrations = any in the working tree whose revision is absent from
    #    the base (catches committed AND uncommitted/staged files — useful both in
    #    CI and as a local pre-push gate).
    pr_meta = [
        (str(p), *_parse(p.read_text()))
        for p in sorted(Path(VERSIONS_DIR).glob("*.py"))
        if "__init__" not in p.name
    ]
    pr_meta = [
        (f, rev, parents) for f, rev, parents in pr_meta if rev and rev not in base_revs
    ]
    if not pr_meta:
        print("✅ No new migrations vs base — linearity OK.")
        return 0

    pr_files = [f for f, _, _ in pr_meta]
    pr_revs = {rev for _, rev, _ in pr_meta if rev}
    allowed_parents = base_heads | pr_revs

    errors: list[str] = []
    for f, rev, parents in pr_meta:
        for parent in parents:
            if parent not in allowed_parents:
                errors.append(
                    f"  {f}\n"
                    f"    down_revision '{parent}' is an already-released revision that is NOT a "
                    f"base head {sorted(base_heads)}.\n"
                    f"    This splices the migration mid-history; databases already past '{parent}' "
                    f"(dev volumes, prod) will SKIP it.\n"
                    f"    Fix: re-point down_revision to the base head and rebase."
                )

    if errors:
        print("❌ Migration linearity check FAILED:\n" + "\n".join(errors))
        return 1

    print(
        f"✅ Migration linearity OK: {len(pr_files)} new migration(s) chain off "
        f"base head {sorted(base_heads)}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
