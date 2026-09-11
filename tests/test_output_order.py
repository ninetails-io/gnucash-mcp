"""Output order comes from the data, never from the storage engine.

SQLite hands rows back in insertion order and InnoDB in primary-key
order. Any renderer that iterated ``transaction.splits`` or a query
result without sorting listed the same book differently on MySQL
than on the file — first seen on the 1.5.0 MariaDB loop, where every
figure agreed and only the order moved. These tests build the rows
in the WRONG order on purpose and check the rendered order is the
rule's, not the insert's.
"""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import piecash
import pytest

from gnucash_mcp.book import BookSource, GnuCashBook
from gnucash_mcp.book._base import (
    _ordered_splits,
    _transaction_to_compact_line,
    _transaction_to_dict,
    _txn_sort_key,
)


@pytest.fixture
def credit_first_book(tmp_path: Path) -> Path:
    """Two same-day transactions, each inserted credit leg first.

    GnuCash's own commit order (``xaccTransSortSplits``) is debits
    first; the insert order here is the opposite so that a renderer
    reading storage order fails visibly.
    """
    path = tmp_path / "order.gnucash"
    book = piecash.create_book(
        sqlite_file=str(path), currency="USD", keep_foreign_keys=False,
    )
    usd = book.default_currency
    root = book.root_account
    assets = piecash.Account(
        name="Assets", type="ASSET", parent=root, commodity=usd,
        placeholder=1,
    )
    checking = piecash.Account(
        name="Checking", type="BANK", parent=assets, commodity=usd,
    )
    expenses = piecash.Account(
        name="Expenses", type="EXPENSE", parent=root, commodity=usd,
        placeholder=1,
    )
    dining = piecash.Account(
        name="Dining", type="EXPENSE", parent=expenses, commodity=usd,
    )
    fuel = piecash.Account(
        name="Auto Fuel", type="EXPENSE", parent=expenses, commodity=usd,
    )
    book.flush()
    # Entered second, listed first: newer enter_date on the same day.
    piecash.Transaction(
        currency=usd, description="Entered first",
        post_date=date(2026, 3, 1),
        enter_date=datetime(2026, 3, 1, 9, 0, 0),
        splits=[
            piecash.Split(account=checking, value=Decimal("-30")),
            piecash.Split(account=fuel, value=Decimal("20")),
            piecash.Split(account=dining, value=Decimal("10")),
        ],
    )
    piecash.Transaction(
        currency=usd, description="Entered second",
        post_date=date(2026, 3, 1),
        enter_date=datetime(2026, 3, 1, 17, 0, 0),
        splits=[
            piecash.Split(account=checking, value=Decimal("-5")),
            piecash.Split(account=dining, value=Decimal("5")),
        ],
    )
    book.save()
    book.close()
    return path


class TestSplitOrder:
    def test_debits_first_then_account_path_then_guid(self, credit_first_book):
        book = GnuCashBook(BookSource.from_path(credit_first_book))
        with book.open(readonly=True) as b:
            txn = b.transactions(description="Entered first")
            ordered = _ordered_splits(txn)
            names = [s.account.fullname for s in ordered]
            # Storage order was Checking, Auto Fuel, Dining.
            assert names == [
                "Expenses:Auto Fuel", "Expenses:Dining", "Assets:Checking",
            ]
            assert [s.value < 0 for s in ordered] == [False, False, True]

    def test_dict_and_compact_line_read_the_rule(self, credit_first_book):
        """The two renderers every transaction surface goes through."""
        book = GnuCashBook(BookSource.from_path(credit_first_book))
        with book.open(readonly=True) as b:
            txn = b.transactions(description="Entered second")
            as_dict = _transaction_to_dict(txn)
            assert [s["account"] for s in as_dict["splits"]] == [
                "Expenses:Dining", "Assets:Checking",
            ]
            line = _transaction_to_compact_line(txn)
            assert line.index("Expenses:Dining") < line.index("Assets:Checking")

    def test_get_transaction_and_listing_agree(self, credit_first_book):
        book = GnuCashBook(BookSource.from_path(credit_first_book))
        listing = book.list_transactions(compact=False)["transactions"]
        one = book.get_transaction(listing[0]["guid"])
        assert one["splits"] == listing[0]["splits"]


class TestTransactionOrder:
    def test_same_day_rows_list_by_entry_time(self, credit_first_book):
        book = GnuCashBook(BookSource.from_path(credit_first_book))
        rows = book.list_transactions(compact=False)["transactions"]
        assert [r["description"] for r in rows] == [
            "Entered second", "Entered first",
        ]

    def test_search_uses_the_same_key(self, credit_first_book):
        book = GnuCashBook(BookSource.from_path(credit_first_book))
        rows = book.search_transactions("Entered", compact=False)["transactions"]
        assert [r["description"] for r in rows] == [
            "Entered second", "Entered first",
        ]

    def test_key_falls_back_past_missing_dates(self):
        class T:
            post_date = None
            enter_date = None
            guid = "abc"

        assert _txn_sort_key(T()) == (date.min, datetime.min, "abc")
