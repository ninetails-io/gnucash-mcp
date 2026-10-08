"""_upgrade_book_shapes: one write, every pre-1.5 private shape in
the book converted to GnuCash's own — schedule recipes, invoice
links, budget signs — posting nothing. Three converters, one caller,
so a write in any of the three modules converts the others' shapes
too, and the release note can say one thing."""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import piecash
import pytest
from sqlalchemy import text

from gnucash_mcp.book import GnuCashBook
from tests.test_scheduled import _make_legacy, _rent


@pytest.fixture
def mixed_book(tmp_path: Path) -> Path:
    """USD book with one account of every kind the three converters
    touch, one legacy schedule, one legacy invoice link, and an
    un-stamped budget with an income target stored the old way."""
    path = tmp_path / "shapes.gnucash"
    book = piecash.create_book(str(path), currency="USD", overwrite=True)
    root, usd = book.root_account, book.default_currency
    assets = piecash.Account(name="Assets", type="ASSET", parent=root, commodity=usd, placeholder=True)
    checking = piecash.Account(name="Checking", type="BANK", parent=assets, commodity=usd)
    piecash.Account(name="Accounts Receivable", type="RECEIVABLE", parent=assets, commodity=usd)
    income = piecash.Account(name="Income", type="INCOME", parent=root, commodity=usd, placeholder=True)
    piecash.Account(name="Sales", type="INCOME", parent=income, commodity=usd)
    expenses = piecash.Account(name="Expenses", type="EXPENSE", parent=root, commodity=usd, placeholder=True)
    piecash.Account(name="Rent", type="EXPENSE", parent=expenses, commodity=usd)
    book.save(); book.close()

    gb = GnuCashBook(str(path))
    # legacy schedule
    gb.create_scheduled_transaction(
        name="Rent", description="Rent",
        splits=[{"account": "Expenses:Rent", "amount": "10.00"},
                {"account": "Assets:Checking", "amount": "-10.00"}],
        start_date=(date.today() + timedelta(days=5)).isoformat(), frequency="monthly",
    )
    # legacy invoice link
    c = gb.create_customer(name="Shapes Co")
    inv = gb.create_invoice(customer_id=c["id"])
    gb.add_invoice_entry(inv["id"], description="Work", quantity="1", price="100.00",
                         account="Income:Sales")
    gb.post_invoice(inv["id"], post_account="Assets:Accounts Receivable", post_date="2026-09-01")
    # un-stamped budget with an income target stored as a magnitude
    gb.create_budget(name="B", num_periods=12, period_type="monthly", start_date="2026-01-01")
    gb.set_budget_amount("B", "Income:Sales", "5000", period=0)
    # Engineer all three legacy states LAST — every write above is
    # now a sweep and would convert anything legacy-ized earlier.
    _make_legacy(path, "Rent")
    with gb.open(readonly=False) as b:
        b.session.execute(text("UPDATE slots SET name = 'invoice' WHERE name = 'gncInvoice/invoice-guid'"))
        b.session.execute(text("DELETE FROM slots WHERE name LIKE 'features%'"))
        b.session.execute(text(
            "UPDATE budget_amounts SET amount_num = ABS(amount_num) WHERE account_guid = "
            "(SELECT guid FROM accounts WHERE name = 'Sales')"
        ))
        b.save()
    return path


def _legacy_counts(path):
    gb = GnuCashBook(str(path))
    with gb.open(readonly=True) as book:
        s = book.session
        return {
            "splits_json": s.execute(text("SELECT COUNT(*) FROM slots WHERE name = 'splits-json'")).scalar(),
            "old_links": s.execute(text("SELECT COUNT(*) FROM slots WHERE name = 'invoice' AND slot_type = 5")).scalar(),
            "stamped": s.execute(text("SELECT COUNT(*) FROM slots WHERE name = 'features/Use natural signs in budget amounts'")).scalar(),
            "sales_num": s.execute(text(
                "SELECT amount_num FROM budget_amounts WHERE account_guid = "
                "(SELECT guid FROM accounts WHERE name = 'Sales')")).scalar(),
            "txns": s.execute(text("SELECT COUNT(*) FROM transactions")).scalar(),
        }


class TestUpgradeBookShapes:
    def test_fixture_is_legacy_everywhere(self, mixed_book):
        c = _legacy_counts(mixed_book)
        assert (c["splits_json"], c["old_links"], c["stamped"]) == (1, 2, 0)
        assert c["sales_num"] > 0

    def test_a_budget_write_converts_the_other_modules_too(self, mixed_book):
        gb = GnuCashBook(str(mixed_book))
        before = _legacy_counts(mixed_book)
        r = gb.set_budget_amount("B", "Expenses:Rent", "300", period=0)
        assert r["templates_migrated"] == 1
        assert r["invoice_links_migrated"] == 2
        assert r["book_stamped"] == "Use natural signs in budget amounts"
        assert r["book_scrubbed"] is True
        after = _legacy_counts(mixed_book)
        assert (after["splits_json"], after["old_links"], after["stamped"]) == (0, 0, 1)
        assert after["sales_num"] < 0          # natural sign now
        assert after["txns"] == before["txns"] + 1  # the template txn; nothing posted
        # Idempotent.
        r2 = gb.set_budget_amount("B", "Expenses:Rent", "310", period=0)
        assert not any(k in r2 for k in ("templates_migrated", "invoice_links_migrated", "book_stamped", "book_scrubbed"))

    def test_a_schedule_write_converts_the_other_modules_too(self, mixed_book):
        gb = GnuCashBook(str(mixed_book))
        row = gb.list_scheduled_transactions(compact=False)["scheduled_transactions"][0]
        r = gb.update_scheduled_transaction(row["guid"])
        assert r["templates_migrated"] == 1 and r["invoice_links_migrated"] == 2
        assert r["book_stamped"] and r["book_scrubbed"]
        assert _legacy_counts(mixed_book)["old_links"] == 0

    def test_a_business_write_converts_the_other_modules_too(self, mixed_book):
        gb = GnuCashBook(str(mixed_book))
        inv_id = gb.get_outstanding_invoices(compact=False)["invoices"][0]["id"]
        r = gb.pay_invoice(inv_id, payment_account="Assets:Checking", amount="100.00",
                           payment_date="2026-09-02")
        assert r["templates_migrated"] == 1 and r["invoice_links_migrated"] == 2
        assert _legacy_counts(mixed_book)["splits_json"] == 0

    def test_reads_convert_nothing(self, mixed_book):
        gb = GnuCashBook(str(mixed_book))
        gb.get_book_summary(); gb.list_scheduled_transactions(compact=False)
        gb.get_budget_report("B", period=0); gb.get_outstanding_invoices()
        c = _legacy_counts(mixed_book)
        assert (c["splits_json"], c["old_links"], c["stamped"]) == (1, 2, 0)

    def test_audit_renderer_covers_every_key(self):
        from gnucash_mcp.logging_config import _shape_upgrade_lines
        lines = _shape_upgrade_lines({
            "templates_migrated": 8, "invoice_links_migrated": 108,
            "book_stamped": "Use natural signs in budget amounts", "book_scrubbed": True,
        })
        joined = "\n".join(lines)
        assert "8 schedule recipes" in joined and "108 invoice links" in joined
        assert "book stamped" in joined and "scrubbed to natural sign" in joined
        assert _shape_upgrade_lines({}) == []
