"""Cross-commodity split entry: money on both sides, and shares that
exist (review C12, C21).

Both live in ``_validate_transaction_splits``, the one validator every
transaction-writing path shares.
"""

import sqlite3
from datetime import date

import pytest

from gnucash_mcp.book import GnuCashBook

D = date(2026, 1, 7)


def _q(book_path, sql, params=()):
    con = sqlite3.connect(str(book_path))
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


class TestCurrencySplitNeedsBothSides:
    """``quantity * value < 0`` passes any zero: 110 USD arriving in
    a EUR account as 0 EUR was accepted and then showed as a 110
    unrealized loss; 100 EUR arriving for 0 USD appeared from
    nothing."""

    @pytest.mark.parametrize("amount,quantity", [
        ("110.00", "0"), ("0", "100.00"), ("0.00", "-100"),
    ])
    def test_create_refuses(self, multi_currency_book, amount, quantity):
        gb = GnuCashBook(str(multi_currency_book))
        before = _q(multi_currency_book, "SELECT COUNT(*) FROM splits")
        with pytest.raises(ValueError, match="one side is zero"):
            gb.create_transaction(
                description="FX", trans_date=D,
                splits=[
                    {"account": "Assets:Euro Savings", "amount": amount,
                     "quantity": quantity},
                    {"account": "Assets:Checking",
                     "amount": str(-float(amount)) if float(amount) else "0"},
                ],
            )
        assert _q(multi_currency_book, "SELECT COUNT(*) FROM splits") == before

    def test_batch_rejects_the_row_in_dry_run_too(self, multi_currency_book):
        gb = GnuCashBook(str(multi_currency_book))
        rows = [{
            "ref": "z", "date": D, "description": "FX",
            "splits": [
                {"account": "Assets:Euro Savings", "amount": "110.00",
                 "quantity": "0"},
                {"account": "Assets:Checking", "amount": "-110.00"},
            ],
        }]
        for dry_run in (True, False):
            result = gb.create_transactions(rows, dry_run=dry_run)
            assert "rejected" in result["results"]
            assert "one side is zero" in result["results"]

    def test_an_ordinary_fx_transfer_still_writes(self, multi_currency_book):
        gb = GnuCashBook(str(multi_currency_book))
        made = gb.create_transaction(
            description="FX", trans_date=D,
            splits=[
                {"account": "Assets:Euro Savings", "amount": "110.00",
                 "quantity": "100.00"},
                {"account": "Assets:Checking", "amount": "-110.00"},
            ],
        )
        assert made["status"] == "created"


class TestShareQuantityThatRoundsAway:
    def _btc(self, gb):
        gb.create_commodity(
            mnemonic="BTC", fullname="Bitcoin", namespace="CRYPTO",
        )   # default fraction 10000
        gb.create_account(
            name="BTC", account_type="STOCK", parent="Assets",
            commodity="BTC", commodity_namespace="CRYPTO",
        )

    def _buy(self, gb, quantity, amount="2.00"):
        return gb.create_transaction(
            description="Buy", trans_date=D,
            splits=[
                {"account": "Assets:BTC", "amount": amount,
                 "quantity": quantity},
                {"account": "Assets:Checking", "amount": f"-{amount}"},
            ],
        )

    def test_rounding_to_zero_is_refused(self, test_book):
        """2.00 of cost against no coins at all."""
        gb = GnuCashBook(str(test_book))
        self._btc(gb)
        with pytest.raises(ValueError, match="stored as 0"):
            self._buy(gb, "0.00003")
        assert _q(
            test_book,
            "SELECT COUNT(*) FROM splits s JOIN accounts a "
            "ON a.guid = s.account_guid WHERE a.name = 'BTC'",
        ) == [(0,)]

    def test_rounding_is_reported_not_silent(self, test_book):
        gb = GnuCashBook(str(test_book))
        self._btc(gb)
        made = self._buy(gb, "0.12345", amount="5000.00")
        assert made["status"] == "created"
        notes = str(made.get("warnings"))
        assert "0.12345" in notes and "0.1235" in notes
        assert _q(
            test_book,
            "SELECT s.quantity_num, s.quantity_denom FROM splits s "
            "JOIN accounts a ON a.guid = s.account_guid "
            "WHERE a.name = 'BTC'",
        ) == [(1235, 10000)]

    def test_a_whole_unit_quantity_is_quiet(self, test_book):
        gb = GnuCashBook(str(test_book))
        self._btc(gb)
        made = self._buy(gb, "0.1234", amount="5000.00")
        assert "quantity_rounded" not in str(made.get("warnings"))

    def test_a_zero_leg_on_a_share_account_is_still_allowed(self, test_book):
        """The capital-gains split: value, no shares."""
        gb = GnuCashBook(str(test_book))
        self._btc(gb)
        self._buy(gb, "1.0000", amount="100.00")
        made = gb.create_transaction(
            description="Gain", trans_date=D,
            splits=[
                {"account": "Assets:BTC", "amount": "25.00",
                 "quantity": "0"},
                {"account": "Income:Salary", "amount": "-25.00"},
            ],
        )
        assert made["status"] == "created"


class TestRoundedQuantityImpliesNoPrice:
    """Side-finding 9: 0.00005 BTC paid 2.00 for is stored as 0.0001,
    and the stored row implies 20000 for a 40000 coin. A rounded
    split records no price; an exact one still does. Every path that
    builds splits from the shared validator is covered."""

    _btc = TestShareQuantityThatRoundsAway._btc

    def _btc_prices(self, path):
        return _q(
            path,
            "SELECT p.value_num, p.value_denom FROM prices p "
            "JOIN commodities c ON c.guid = p.commodity_guid "
            "WHERE c.mnemonic = 'BTC'",
        )

    def _legs(self, quantity, amount="2.00"):
        return [
            {"account": "Assets:BTC", "amount": amount,
             "quantity": quantity},
            {"account": "Assets:Checking", "amount": f"-{amount}"},
        ]

    def test_create(self, test_book):
        gb = GnuCashBook(str(test_book))
        self._btc(gb)
        made = gb.create_transaction(
            description="Buy", trans_date=D, splits=self._legs("0.00005"),
        )
        assert "quantity_rounded" in str(made.get("warnings"))
        assert self._btc_prices(test_book) == []

    def test_an_exact_quantity_still_records_its_price(self, test_book):
        gb = GnuCashBook(str(test_book))
        self._btc(gb)
        gb.create_transaction(
            description="Buy", trans_date=D, splits=self._legs("0.0001"),
        )
        [(num, denom)] = self._btc_prices(test_book)
        assert num / denom == 20000

    def test_batch(self, test_book):
        gb = GnuCashBook(str(test_book))
        self._btc(gb)
        result = gb.create_transactions([{
            "ref": "a", "date": D, "description": "Buy",
            "splits": self._legs("0.00005"),
        }], dry_run=False)
        assert "created" in result["results"]
        assert self._btc_prices(test_book) == []

    def test_replace_splits(self, test_book):
        gb = GnuCashBook(str(test_book))
        self._btc(gb)
        made = gb.create_transaction(
            description="Buy", trans_date=D,
            splits=self._legs("0.0001"),
        )
        con = sqlite3.connect(str(test_book))
        con.execute("DELETE FROM prices")
        con.commit()
        con.close()
        gb.replace_splits(
            guid=made["guid"], splits=self._legs("0.00015", amount="4.00"),
        )
        assert self._btc_prices(test_book) == []

    def test_update(self, test_book):
        gb = GnuCashBook(str(test_book))
        self._btc(gb)
        made = gb.create_transaction(
            description="Buy", trans_date=D,
            splits=self._legs("0.0001"),
        )
        con = sqlite3.connect(str(test_book))
        con.execute("DELETE FROM prices")
        con.commit()
        con.close()
        gb.update_transaction(
            guid=made["guid"], splits=self._legs("0.00015", amount="4.00"),
        )
        assert self._btc_prices(test_book) == []
