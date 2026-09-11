"""The demo-book continuation's frozen-prefix counter counts ledger
transactions only.

A scheduled transaction's template is a transaction row too, on an
account under GnuCash's template root and dated at the schedule's
start. Since 1.5 the first schedule write converts every pre-1.5
recipe into one, so the continuation's own schedule step added N
template rows dated inside the frozen prefix and tripped the
invariant — the bundle build failed on every run after the
conversion merged. Template rows are not activity; the counter
skips them.
"""

import sys
from datetime import date
from pathlib import Path

import pytest

from gnucash_mcp.book import GnuCashBook

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts" / "synthetic_book"


@pytest.fixture
def continuation():
    sys.path.insert(0, str(SCRIPTS))
    try:
        import continuation as mod
        yield mod
    finally:
        sys.path.remove(str(SCRIPTS))
        sys.modules.pop("continuation", None)


def test_template_rows_are_not_prefix_transactions(test_book: Path, continuation):
    gb = GnuCashBook(str(test_book))
    cutoff = date(2030, 1, 1)
    before = continuation.prefix_txn_count(test_book, cutoff)
    # Two template rows, both dated well inside the prefix.
    for name in ("SX one", "SX two"):
        gb.create_scheduled_transaction(
            name=name, description=name, start_date="2026-01-15",
            frequency="monthly",
            splits=[
                {"account": "Expenses:Groceries", "amount": "10"},
                {"account": "Assets:Checking", "amount": "-10"},
            ],
        )
    assert continuation.prefix_txn_count(test_book, cutoff) == before
    # A real ledger row in the prefix still counts.
    gb.create_transaction(
        description="ledger row",
        splits=[
            {"account": "Expenses:Groceries", "amount": "5"},
            {"account": "Assets:Checking", "amount": "-5"},
        ],
        trans_date=date(2026, 1, 20),
        check_duplicates=False,
    )
    assert continuation.prefix_txn_count(test_book, cutoff) == before + 1
