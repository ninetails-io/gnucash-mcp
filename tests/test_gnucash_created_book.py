"""The server on a book GnuCash created, and GnuCash on that book
after the server has written to it.

Every other fixture in the suite is a piecash-made book, whose table
definitions differ from GnuCash's own in small ways. Two findings of
the 1.5 pre-release review existed because of that gap and were found
by reading, not by a test (``specs/v1.5/testing/
ADVERSARIAL_REVIEW_1.5.md``, FC-19; C65 and C66). Here ``gnucash-cli``
creates a fresh SQLite book through the engine
(``fixtures/gnucash_made.py``); the server runs a sweep of its write
paths on it; and ``gnucash-cli`` loads the result again. The last step
is the headless half of "opens cleanly in GnuCash desktop": the SQL
backend loads every row the server wrote, and an engine action on a
server-posted invoice goes through.

Skipped where ``gnucash-cli`` is not installed. The book is never
committed.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from gnucash_mcp.book import GnuCashBook

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE / "fixtures"))
from engine_twin import engine_run, find_gnucash_cli, guid_of  # noqa: E402
from gnucash_made import make_book  # noqa: E402

pytestmark = pytest.mark.skipif(
    find_gnucash_cli() is None, reason="gnucash-cli not on this machine",
)

AR = "Assets:Accounts Receivable"
AP = "Liabilities:Accounts Payable"
CHECKING = "Assets:Checking"
YESTERDAY = date.today() - timedelta(days=1)


@pytest.fixture
def made(test_book, tmp_path) -> Path:
    return make_book(tmp_path / "made-by-gnucash.gnucash", host=test_book)


def _q(path, sql, params=()):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def test_the_book_is_gnucashs_own(made):
    """What makes this fixture worth having: GnuCash's table
    definitions, not piecash's."""
    (ddl,) = _q(
        made, "select sql from sqlite_master where name = 'splits'",
    )[0]
    assert "tx_guid text(32) NOT NULL" in ddl
    gb = GnuCashBook(str(made))
    listing = gb.list_accounts()
    for path in (CHECKING, AR, AP, "Income:Sales", "Expenses:Groceries"):
        assert path in listing


def _sweep(gb: GnuCashBook) -> dict:
    """One call through each family of write path."""
    out = {}
    gb.create_transaction(
        description="Opening balance",
        splits=[
            {"account": CHECKING, "amount": "5000.00"},
            {"account": "Equity:Opening Balances", "amount": "-5000.00"},
        ],
        trans_date=date(2026, 1, 1), check_duplicates=False,
    )
    spent = gb.create_transaction(
        description="Market", notes="weekly shop",
        splits=[
            {"account": "Expenses:Groceries", "amount": "42.50",
             "memo": "card ending 1234"},
            {"account": CHECKING, "amount": "-42.50"},
        ],
        trans_date=YESTERDAY, check_duplicates=False,
    )
    voided = gb.create_transaction(
        description="Entered twice",
        splits=[
            {"account": "Expenses:Groceries", "amount": "9.99"},
            {"account": CHECKING, "amount": "-9.99"},
        ],
        trans_date=YESTERDAY, check_duplicates=False,
    )
    gb.void_transaction(voided["guid"], reason="duplicate")
    gb.unvoid_transaction(voided["guid"])
    gb.delete_transaction(voided["guid"])
    gb.update_transaction(spent["guid"], description="Farmers market")

    made_acct = gb.create_account(
        name="Dining", account_type="EXPENSE", parent="Expenses",
        description="Meals out",
    )
    gb.update_account(made_acct["guid"], hidden=True, notes="seasonal")
    gb.update_account(made_acct["guid"], hidden=False)

    gb.create_customer(name="Acme Corp")
    inv = gb.create_invoice(customer_id="000001")
    gb.add_invoice_entry(
        invoice_id=inv["id"], account="Income:Sales",
        description="Consulting", quantity="2", price="150.00",
    )
    gb.post_invoice(inv["id"], AR, post_date="2026-02-01")
    gb.pay_invoice(
        invoice_id=inv["id"], payment_account=CHECKING, amount="100.00",
        payment_date="2026-02-10",
    )
    cn = gb.create_credit_note(owner_id="000001", owner_type="customer")
    gb.add_credit_note_entry(
        credit_note_id=cn["id"], account="Income:Sales",
        description="Goodwill", quantity="1", price="50.00",
    )
    gb.post_invoice(
        invoice_id=cn["id"], post_account=AR, post_date="2026-02-12",
        owner_type="customer",
    )
    gb.apply_credit_note(
        credit_note_id=cn["id"], applies_to_invoice_id=inv["id"],
        owner_type="customer",
    )
    second = gb.create_invoice(customer_id="000001")
    gb.add_invoice_entry(
        invoice_id=second["id"], account="Income:Sales",
        description="Retainer", quantity="1", price="80.00",
    )
    gb.post_invoice(second["id"], AR, post_date="2026-03-01")
    gb.unpost_invoice(second["id"])
    third = gb.create_invoice(customer_id="000001")
    gb.add_invoice_entry(
        invoice_id=third["id"], account="Income:Sales",
        description="Workshop", quantity="1", price="120.00",
    )
    gb.post_invoice(third["id"], AR, post_date="2026-03-05")
    out["open_invoice"] = third["id"]

    gb.create_vendor(name="Supplier Ltd")
    bill = gb.create_bill(vendor_id="000001")
    gb.add_bill_entry(
        bill_id=bill["id"], account="Expenses:Groceries",
        description="Bulk order", quantity="1", price="60.00",
    )
    gb.post_invoice(
        invoice_id=bill["id"], post_account=AP, post_date="2026-02-03",
        owner_type="vendor",
    )
    gb.pay_invoice(
        invoice_id=bill["id"], payment_account=CHECKING, amount="60.00",
        payment_date="2026-02-20", owner_type="vendor",
    )

    sx = gb.create_scheduled_transaction(
        name="Rent", description="Rent",
        splits=[
            {"account": "Expenses:Groceries", "amount": "900.00"},
            {"account": CHECKING, "amount": "-900.00"},
        ],
        start_date="2026-01-01", frequency="monthly",
    )
    gb.create_transaction_from_scheduled(
        sx["guid"], transaction_date="2026-01-01",
    )

    gb.create_budget(name="Household", year=2026)
    gb.set_budget_amount("Household", "Expenses:Groceries", "400.00")
    gb.set_budget_amount("Household", "Income:Sales", "1000.00")

    # GnuCash saves only the commodities a book uses; the account
    # brings EUR in.
    gb.create_account(
        name="Euro Cash", account_type="BANK", parent="Assets",
        commodity="EUR",
    )
    gb.create_price(
        commodity="EUR", namespace="CURRENCY", value="1.10",
        price_date=YESTERDAY,
    )
    return out


def test_the_server_reads_its_own_writes_on_it(made):
    gb = GnuCashBook(str(made))
    _sweep(gb)
    sheet = gb.balance_sheet(date.today())
    assert Decimal(sheet["assets"]["total"]) == (
        Decimal(sheet["liabilities"]["total"])
        + Decimal(sheet["equity"]["total"])
    )
    # 5000 - 42.50 + 100 - 60 - 900 in checking; 300 - 100 - 50 + 120
    # receivable.
    assert gb.get_balance(CHECKING) == Decimal("4097.50")
    assert gb.get_balance(AR) == Decimal("270.00")
    assert gb.get_balance(AP) == Decimal("0")
    summary = gb.get_book_summary()
    assert "check failed" not in summary
    assert "Acme Corp" in str(gb.get_outstanding_invoices())


def test_gnucash_loads_the_book_after_the_servers_writes(made):
    """The SQL backend loads every row (a row it cannot load fails
    the run), and the engine then pays the invoice the server posted:
    it found the document, its lot and its posting, and wrote to
    them."""
    gb = GnuCashBook(str(made))
    swept = _sweep(gb)
    invoice = guid_of(
        made, "select guid from invoices where id = ? and owner_type = 2",
        swept["open_invoice"],
    )
    checking = guid_of(made, "select guid from accounts where name = 'Checking'")
    results = engine_run(
        made, [f"pay|{invoice}|{checking}|120|1|10|3|2026|engine|"],
    )
    assert results == ["ok"]
    # And the server reads what the engine did with it.
    doc = gb.get_invoice(swept["open_invoice"])
    assert doc["status"] == "paid"
    assert gb.get_balance(AR) == Decimal("150.00")
