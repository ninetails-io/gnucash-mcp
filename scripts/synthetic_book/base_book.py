"""Helpers shared by the builders: the server's split constructor and
price writer, and ``--chart-only`` mode.

A chart-only book holds commodities, the chart of accounts, and
account slots — nothing dated. It is a fast validity check on a
builder (seconds instead of a minute) and what ``tests/test_demo_bases.py``
exercises. Nothing chart-only is ever committed: the chart is code,
and ``samples/*.gnucash`` is ignored by git.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path


def new_split(account, value, quantity=None, currency=None):
    """A split through the server's one constructor, ``_new_split``:
    the value rounded to the transaction currency's unit, the quantity
    to the account's, and an unreconciled ``reconcile_date`` at the
    epoch as GnuCash stores it (piecash leaves it NULL). ``currency``
    defaults to the book's; every builder transaction currency has two
    decimal places. ``quantity`` defaults to ``value`` (same-commodity
    split), as piecash's did."""
    from gnucash_mcp.book._base import _new_split

    return _new_split(
        account, value, value if quantity is None else quantity,
        currency or account.book.default_currency,
    )


def record_prices(
    out_path: Path,
    rows: list[tuple[str, str, str, date, Decimal, str]],
) -> int:
    """Write ``(mnemonic, namespace, currency, date, value, source)``
    rows through the server's ``create_prices``: GnuCash's neutral
    time of day, one price per pair per day, a source the Price Editor
    recognizes. piecash's ORM binds a price date at the build
    machine's local midnight, so the same build stored a different
    date per timezone. Raises unless every row was written."""
    from gnucash_mcp.book import GnuCashBook

    if not rows:
        return 0
    result = GnuCashBook(str(out_path)).create_prices([
        {"ref": str(i), "commodity": sym, "namespace": ns,
         "currency": cur, "date": when, "value": value,
         "source": source, "price_type": "last"}
        for i, (sym, ns, cur, when, value, source) in enumerate(rows)
    ])
    lines = result["results"].splitlines()
    status = lines[0].split("\t").index("status")
    bad = [ln for ln in lines[1:]
           if ln.split("\t")[status] not in ("created", "updated", "replaced")]
    if bad:
        raise RuntimeError("prices not written:\n  " + "\n  ".join(bad[:10]))
    return len(rows)


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
