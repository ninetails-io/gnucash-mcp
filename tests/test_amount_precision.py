"""Split amounts are stored the way GnuCash stores them.

``xaccSplitSetValue`` converts a value to the transaction currency's
fraction and ``xaccSplitSetAmount`` a quantity to the account's unit
(``xaccAccountGetCommoditySCU``), both rounding half up (libgnucash/
engine/Split.cpp, stable, fetched 2026-09-27). piecash instead stores
a Decimal over its own exponent, so "12" landed as 12/1 and "12.345"
dollars as 12345/1000 — a sub-cent amount desktop can never hold.

Rulings (maintainer, 2026-09-27): money finer than its currency's unit
is refused at input; share quantities round to the account's unit.
"""

import re
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from gnucash_mcp.book import GnuCashBook


def _stored(book_path: Path, description: str, account_name: str):
    """(value_num, value_denom, quantity_num, quantity_denom) of the
    named transaction's split on the named leaf account."""
    with sqlite3.connect(book_path) as conn:
        return conn.execute(
            "SELECT s.value_num, s.value_denom, s.quantity_num, "
            "s.quantity_denom FROM splits s "
            "JOIN transactions t ON t.guid = s.tx_guid "
            "JOIN accounts a ON a.guid = s.account_guid "
            "WHERE t.description = ? AND a.name = ?",
            (description, account_name),
        ).fetchone()


def _groceries(amount: str) -> list[dict]:
    return [
        {"account": "Assets:Checking", "amount": f"-{amount}"},
        {"account": "Expenses:Groceries", "amount": amount},
    ]


class TestMoneyInput:
    def test_sub_cent_dollars_are_refused(self, test_book: Path):
        gc = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match=r"finer precision than USD allows \(2 decimals\)"):
            gc.create_transaction(
                description="Sub-cent", splits=_groceries("12.345"),
                check_duplicates=False,
            )
        assert _stored(test_book, "Sub-cent", "Groceries") is None

    def test_whole_dollars_store_over_the_currency_fraction(
        self, test_book: Path,
    ):
        gc = GnuCashBook(str(test_book))
        gc.create_transaction(
            description="Whole", splits=_groceries("12"),
            check_duplicates=False,
        )
        assert _stored(test_book, "Whole", "Groceries") == (1200, 100, 1200, 100)

    def test_replace_splits_refuses_sub_cent(self, test_book: Path):
        gc = GnuCashBook(str(test_book))
        guid = gc.create_transaction(
            description="Replace me", splits=_groceries("10.00"),
            check_duplicates=False,
        )["guid"]
        with pytest.raises(ValueError, match="finer precision than USD allows"):
            gc.replace_splits(guid=guid, splits=_groceries("10.005"))


class TestShareQuantities:
    def test_shares_round_half_up_to_the_account_unit(
        self, investment_book: Path,
    ):
        """VTSAX's unit is 1/10000; 10.12345 shares rounds half up to
        10.1235, the amount desktop would have stored."""
        gc = GnuCashBook(str(investment_book))
        gc.create_transaction(
            description="Buy VTSAX", check_duplicates=False,
            splits=[
                {"account": "Assets:Investments:VTSAX",
                 "amount": "1250.00", "quantity": "10.12345"},
                {"account": "Assets:Checking", "amount": "-1250.00"},
            ],
        )
        assert _stored(investment_book, "Buy VTSAX", "VTSAX") == (
            125000, 100, 101235, 10000,
        )


class TestVoidRoundTrip:
    def test_unvoid_restores_gnucash_denominators(self, test_book: Path):
        gc = GnuCashBook(str(test_book))
        guid = gc.create_transaction(
            description="Round trip", splits=_groceries("12"),
            check_duplicates=False,
        )["guid"]
        gc.void_transaction(guid, reason="test")
        assert _stored(test_book, "Round trip", "Groceries") == (0, 100, 0, 100)
        gc.unvoid_transaction(guid)
        assert _stored(test_book, "Round trip", "Groceries") == (1200, 100, 1200, 100)
        assert Decimal(
            gc.get_transaction(guid)["splits"][0]["value"]
        ).copy_abs() == Decimal("12")


class TestSplitConstructorChokepoint:
    """Every split is built by ``_new_split``: a ``piecash.Split(``
    anywhere else stores the caller's Decimal exponent again."""

    BOOK_DIR = (
        Path(__file__).resolve().parent.parent / "src" / "gnucash_mcp" / "book"
    )

    def test_no_split_constructed_outside_the_chokepoint(self):
        offenders = [
            f"{path.name}:{lineno}: {line.strip()}"
            for path in sorted(self.BOOK_DIR.glob("*.py"))
            if path.name != "_base.py"
            for lineno, line in enumerate(path.read_text().splitlines(), 1)
            if "piecash.Split(" in line or line.strip().startswith("Split(")
        ]
        assert offenders == []

    def test_the_chokepoint_is_the_one_constructor(self):
        base = (self.BOOK_DIR / "_base.py").read_text()
        # The call, not the docstrings that name it.
        assert len(re.findall(r"piecash\.Split\(\s*\n\s*account=", base)) == 1
        helper = base[base.index("def _new_split"):]
        assert "_split_amounts(" in helper[:helper.index("\ndef ")]


class TestPaymentAmount:
    def test_sub_cent_payment_is_refused(self, business_book: Path):
        """The reply would have said 100.005 while the split stored
        100.01; refused instead, dry run included."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001", account="Income:Sales",
            description="Work", quantity="1", price="500",
        )
        gb.post_invoice(
            invoice_id="000001", post_account="Assets:Accounts Receivable",
        )
        for dry_run in (True, False):
            with pytest.raises(ValueError, match="finer precision than USD"):
                gb.pay_invoice(
                    invoice_id="000001", payment_account="Assets:Checking",
                    amount="100.005", dry_run=dry_run,
                )
        assert gb.get_invoice("000001")["amount_due"] == "500.00"


def test_the_refusal_is_worded_once():
    """Validator, statement lines and balances, and payments all call
    ``_money_precision_error``; a private copy of the rule would drift."""
    book_dir = Path(__file__).resolve().parent.parent / "src" / "gnucash_mcp" / "book"
    text = "\n".join(p.read_text() for p in book_dir.glob("*.py"))
    assert text.count("carries finer precision") == 1
