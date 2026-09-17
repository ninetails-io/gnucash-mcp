"""Helpers for the builders' ``--chart-only`` mode.

A chart-only book holds commodities, the chart of accounts, and
account slots — nothing dated. It is a fast validity check on a
builder (seconds instead of a minute) and what ``tests/test_demo_bases.py``
exercises. Nothing chart-only is ever committed: the chart is code,
and ``samples/*.gnucash`` is ignored by git.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def vacuum(path: Path) -> int:
    """Rewrite the SQLite file without freed pages; return its size."""
    con = sqlite3.connect(str(path))
    try:
        con.execute("DELETE FROM gnclock")
        con.commit()
        con.execute("VACUUM")
    finally:
        con.close()
    return path.stat().st_size
