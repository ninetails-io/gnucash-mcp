"""Amounts are printed the way GnuCash prints them (review C20).

Two rules from GnuCash 5.12, one per job:

- display (``PrintAmountInternal`` with ``gnc_commodity_print_info``):
  the commodity's own places, all of them for an ISO currency, trailing
  zeros dropped for anything else; never rounding a stored amount,
  half-up where it must round;
- conversion (``convert_amount_at_date``): round to the target
  currency's fraction with ``GNC_HOW_RND_ROUND``, half-even.

A BHD 10.125 balance read 10.13, 10.12, and 10.12 on three surfaces.
"""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import piecash
import pytest

from gnucash_mcp._format import (
    _format_amount,
    _format_converted,
    _format_exact,
    _format_price,
    _round_converted,
)
from gnucash_mcp.book import GnuCashBook
from gnucash_mcp.logging_config import _format_amount as _audit_amount

BHD = SimpleNamespace(mnemonic="BHD", fraction=1000, namespace="CURRENCY")
USD = SimpleNamespace(mnemonic="USD", fraction=100, namespace="CURRENCY")
JPY = SimpleNamespace(mnemonic="JPY", fraction=1, namespace="CURRENCY")
BTC = SimpleNamespace(mnemonic="BTC", fraction=10**8, namespace="CRYPTO")


class TestTheRules:
    def test_an_iso_currency_shows_all_its_places(self):
        assert _format_amount(Decimal("10.125"), BHD) == "10.125"
        assert _format_amount(Decimal("5"), USD) == "5.00"
        assert _format_amount(Decimal("1234567"), JPY, separators=True) == "1,234,567"

    def test_another_commodity_drops_trailing_zeros(self):
        assert _format_amount(Decimal("0.00004321"), BTC) == "0.00004321"
        assert _format_amount(Decimal("10.50000000"), BTC) == "10.5"

    def test_display_rounds_half_up_away_from_zero(self):
        assert _format_amount(Decimal("10.125"), USD) == "10.13"
        assert _format_amount(Decimal("-10.125"), USD) == "-10.13"

    def test_a_conversion_rounds_half_even(self):
        assert _round_converted(Decimal("10.125"), USD) == Decimal("10.12")
        assert _round_converted(Decimal("10.135"), USD) == Decimal("10.14")
        assert _format_converted(Decimal("10.125"), USD) == "10.12"
        assert _format_converted(Decimal("10.1255"), BHD) == "10.126"

    def test_a_price_shows_two_places_past_its_currency(self):
        assert _format_price(Decimal("156.23"), USD) == "156.2300"
        assert _format_price(Decimal("1.2345678"), BHD) == "1.23457"
        assert _format_price(Decimal("150"), JPY) == "150.00"

    def test_an_amount_without_its_commodity_is_never_rounded(self):
        assert _format_exact(Decimal("10.125")) == "10.125"
        assert _format_exact(Decimal("1700")) == "1,700.00"
        assert _audit_amount("10.125") == "10.125"
        assert _audit_amount("-1700.5") == "-1,700.50"


@pytest.fixture
def bhd_book(tmp_path) -> Path:
    path = tmp_path / "bhd.gnucash"
    book = piecash.create_book(str(path), currency="BHD", overwrite=True)
    bhd = book.default_currency
    assets = piecash.Account(
        name="Assets", type="ASSET", commodity=bhd,
        parent=book.root_account, placeholder=True,
    )
    piecash.Account(name="Bank", type="BANK", commodity=bhd, parent=assets)
    piecash.Account(
        name="Opening", type="EQUITY", commodity=bhd,
        parent=book.root_account,
    )
    book.save()
    book.close()
    gb = GnuCashBook(str(path))
    gb.create_transaction(
        description="Opening", trans_date=date(2026, 1, 5),
        splits=[
            {"account": "Assets:Bank", "amount": "10.125"},
            {"account": "Opening", "amount": "-10.125"},
        ],
    )
    return path


class TestEverySurfaceAgrees:
    def test_balance_sheet(self, bhd_book):
        sheet = GnuCashBook(str(bhd_book)).balance_sheet(date(2026, 1, 31))
        assert sheet["assets"]["total"] == "10.125"
        assert sheet["assets"]["accounts"] == [
            {"account": "Assets:Bank", "balance": "10.125"},
        ]

    def test_dashboard(self, bhd_book):
        summary = GnuCashBook(str(bhd_book)).get_book_summary()
        assert "Assets: 1 accounts, BHD 10.125" in summary
        assert "Bank: BHD 10.125" in summary

    def test_net_worth(self, bhd_book):
        nw = GnuCashBook(str(bhd_book)).net_worth(end_date=date(2026, 1, 31))
        assert nw["net_worth"] == "10.125"


# ── The lock ─────────────────────────────────────────────────────────

SRC = Path(__file__).resolve().parent.parent / "src" / "gnucash_mcp"
SCANNED = sorted(
    [*(SRC / "book").glob("*.py"), SRC / "_format.py", SRC / "logging_config.py"]
)
TWO_PLACES = re.compile(
    r"""\.2f\}|,\.2f|decimals=2\b|Decimal\(["']0\.01["']\)"""
)
# Each one is not an amount, with the reason.
ALLOWED = {
    # a percentage, not money
    ('investments.py', '"gain_percent": _format_number(gain_pct, decimals=2),'),
    ('reporting.py', '''apr_str = f"{d['apr'].quantize(Decimal('0.01'))}%"'''),
    # a ratio
    ('reporting.py', 'yeti_multiplier = (true_cost / purchase_amount).quantize(Decimal("0.01"))  # a ratio'),
    # a default the caller always overrides with the currency's unit
    ('reporting.py', 'unit: Decimal = Decimal("0.01"),'),
    # a docstring and a comment describing the old mistake
    ('_base.py', '``Decimal("0.01")`` would silently corrupt non-2-decimal'),
    ('_format.py', 'quantum = Decimal(1).scaleb(-decimals)  # 0.01 for decimals=2'),
    # no lot, no commodity: nothing is settled through it
    ('business.py', 'if lot is not None else Decimal("0.01")'),
}


def test_no_amount_is_formatted_at_a_fixed_two_places():
    found = set()
    for path in SCANNED:
        for line in path.read_text().splitlines():
            if TWO_PLACES.search(line):
                found.add((path.name, line.strip()))
    assert found - ALLOWED == set(), (
        "An amount formatted at two places: print it through "
        "_format_amount / _format_converted / _format_account_amount "
        "(GnuCash's rules), or add it to ALLOWED with the reason it is "
        "not an amount."
    )
