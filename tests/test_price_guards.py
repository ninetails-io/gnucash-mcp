"""What makes a price row a price (review C13, C35, SEC-11).

``_check_price`` gates both entry points, ``create_price`` and
``create_prices`` (dry run included).
"""

import time
from datetime import date

import pytest

from gnucash_mcp.book import GnuCashBook

D = date(2026, 9, 1)


class TestPriceMustBePositive:
    @pytest.mark.parametrize("value", ["0", "-1.10", "-0", "0.00"])
    def test_single(self, multi_currency_book, value):
        gb = GnuCashBook(str(multi_currency_book))
        before = gb.net_worth(end_date=date(2026, 9, 30))
        with pytest.raises(ValueError, match="greater than zero"):
            gb.create_price("EUR", "CURRENCY", value, price_date=D)
        # Valuation multiplies a holding by the latest row: a
        # negative one valued 1,000 EUR at −1,100.
        assert gb.net_worth(end_date=date(2026, 9, 30)) == before

    @pytest.mark.parametrize("dry_run", [True, False])
    def test_batch_rejects_the_row(self, multi_currency_book, dry_run):
        gb = GnuCashBook(str(multi_currency_book))
        result = gb.create_prices(
            [
                {"ref": "ok", "commodity": "EUR", "ns": "CURRENCY",
                 "date": D, "value": "1.10"},
                {"ref": "neg", "commodity": "EUR", "ns": "CURRENCY",
                 "date": date(2026, 9, 2), "value": "-1.10"},
            ],
            on_error="skip", dry_run=dry_run,
        )
        rows = {
            line.split("\t")[0]: line
            for line in result["results"].splitlines()[1:]
        }
        assert "rejected" in rows["neg"]
        assert "greater than zero" in rows["neg"]
        assert ("would_create" if dry_run else "created") in rows["ok"]

    @pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
    def test_not_a_number(self, multi_currency_book, value):
        gb = GnuCashBook(str(multi_currency_book))
        with pytest.raises(ValueError):
            gb.create_price("EUR", "CURRENCY", value, price_date=D)

    def test_a_positive_price_still_writes(self, multi_currency_book):
        gb = GnuCashBook(str(multi_currency_book))
        assert gb.create_price(
            "EUR", "CURRENCY", "1.25", price_date=D,
        )["status"] == "created"
        assert gb.create_price(
            "EUR", "CURRENCY", "0.0000613", price_date=date(2026, 9, 2),
        )["status"] == "created"


class TestPriceIsOfOneCommodityInAnother:
    def test_self_price_is_refused(self, multi_currency_book):
        gb = GnuCashBook(str(multi_currency_book))
        with pytest.raises(ValueError, match="always 1"):
            gb.create_price("USD", "CURRENCY", "0.91", price_date=D)
        with pytest.raises(ValueError, match="always 1"):
            gb.create_price(
                "EUR", "CURRENCY", "1", currency="EUR", price_date=D,
            )

    def test_batch_self_price_rejected(self, multi_currency_book):
        gb = GnuCashBook(str(multi_currency_book))
        result = gb.create_prices([
            {"ref": "self", "commodity": "USD", "ns": "CURRENCY",
             "date": D, "value": "1"},
        ], on_error="skip")
        assert "rejected" in result["results"]
        assert "always 1" in result["results"]


class TestAbsurdMagnitudeIsRefusedQuickly:
    @pytest.mark.parametrize("value", ["9e999998", "1e-999998", "1e40"])
    def test_refused_without_the_long_rational(
        self, multi_currency_book, value,
    ):
        """``Fraction("9e999998")`` took 24 seconds."""
        gb = GnuCashBook(str(multi_currency_book))
        started = time.monotonic()
        with pytest.raises(ValueError):
            gb.create_price("EUR", "CURRENCY", value, price_date=D)
        assert time.monotonic() - started < 2


class TestStaleRateRefusalNamesBothCurrencies:
    """The suggested call, followed as written, must fix the refusal.
    It named only ``commodity``, so the price landed against the
    BOOK's currency: a USD-in-USD row on a USD book paying a USD
    invoice from a EUR account, and the retry failed identically."""

    def test_following_the_suggestion_unblocks_the_payment(
        self, business_book,
    ):
        import re
        from datetime import timedelta

        gb = GnuCashBook(str(business_book))
        pay_day = date(2026, 9, 30)
        gb.create_account(
            name="EUR Bank", account_type="BANK", parent="Assets",
            commodity="EUR",
        )
        gb.create_price(
            "EUR", "CURRENCY", "1.10",
            price_date=pay_day - timedelta(days=20),
        )
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001", account="Income:Sales",
            description="Work", quantity="1", price="100.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
            post_date="2026-09-01",
        )

        def pay():
            return gb.pay_invoice(
                invoice_id="000001", payment_account="Assets:EUR Bank",
                amount="100.00", payment_date=pay_day.isoformat(),
            )

        with pytest.raises(ValueError) as refusal:
            pay()
        message = str(refusal.value)
        call = re.search(r"create_price\((.*?)\)", message).group(1)
        args = dict(re.findall(r"(\w+)='([^']*)'", call))
        # Both sides of the pair, and a rate a person can read.
        assert args["commodity"] == "USD" and args["currency"] == "EUR"
        assert "0.909091" in message and "0.9090909" not in message

        gb.create_price(
            args["commodity"], args["namespace"], "0.91",
            currency=args["currency"],
            price_date=date.fromisoformat(args["date"]),
        )
        assert pay()["status"] == "paid"
