"""The MINOR tier of the 1.5 pre-release adversarial review.

One class per finding, named for it, each with the test that fails on
the code as reviewed. Findings and their cross-examination are in
``specs/v1.5/testing/ADVERSARIAL_REVIEW_1.5.md`` (sections 5, 7, 8).
"""

import sqlite3
from datetime import date, timedelta
from decimal import Decimal

import pytest

from gnucash_mcp.book import GnuCashBook

AR = "Assets:Accounts Receivable"
AP = "Liabilities:Accounts Payable"


def _q(path, sql, params=()):
    con = sqlite3.connect(str(path))
    try:
        rows = con.execute(sql, params).fetchall()
        con.commit()
        return rows
    finally:
        con.close()


class TestC38CreateAccountReturnsAUsableReference:
    def test_the_returned_guid_is_the_form_tools_accept(self, test_book):
        gb = GnuCashBook(str(test_book))
        made = gb.create_account(
            name="Dining", account_type="EXPENSE", parent="Expenses",
        )
        assert made["guid"].startswith("%")
        # It goes straight back in.
        child = gb.create_account(
            name="Lunch", account_type="EXPENSE", parent=made["guid"],
        )
        assert child["fullname"] == "Expenses:Dining:Lunch"
        assert gb.get_balance(made["guid"]) == Decimal(0)


class TestBL22AJobAttachedBillIsABill:
    def test_post_pay_and_unpost_name_it_a_bill(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Supplier Ltd")
        job = gb.create_job(owner_id="000001", owner_type="vendor", name="Refit")
        bill = gb.create_bill(vendor_id="000001", job_id=job["id"])
        gb.add_bill_entry(
            bill_id=bill["id"], account="Expenses:Services",
            description="Labour", quantity="1", price="80.00",
        )
        posted = gb.post_invoice(
            invoice_id=bill["id"], post_account=AP, owner_type="vendor",
        )
        paid = gb.pay_invoice(
            invoice_id=bill["id"], payment_account="Assets:Checking",
            amount="30.00", owner_type="vendor",
        )
        unposted = gb.unpost_invoice(invoice_id=bill["id"], owner_type="vendor")
        assert (posted["type"], paid["type"], unposted["type"]) == (
            "bill", "bill", "bill",
        )


class TestSideFinding7ApplyDateIsTheLinksDate:
    def test_the_response_names_the_transactions_date(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        inv = gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id=inv["id"], account="Income:Sales",
            description="Work", quantity="1", price="100.00",
        )
        gb.post_invoice(invoice_id=inv["id"], post_account=AR,
                        post_date="2026-01-15")
        cn = gb.create_credit_note(owner_id="000001", owner_type="customer")
        gb.add_credit_note_entry(
            credit_note_id=cn["id"], account="Income:Sales",
            description="Refund", quantity="1", price="30.00",
        )
        gb.post_invoice(invoice_id=cn["id"], post_account=AR,
                        post_date="2026-01-20", owner_type="customer")

        applied = gb.apply_credit_note(
            credit_note_id=cn["id"], applies_to_invoice_id=inv["id"],
            owner_type="customer",
        )

        assert applied["apply_date"] == "2026-01-20"
        txn = gb.get_transaction(applied["transaction_guid"])
        assert str(txn["date"])[:10] == "2026-01-20"


class TestC64DueDateReadsTheColumnItsTypeNames:
    def test_a_gdate_row_is_not_read_as_1970(self, business_book):
        """Every slot row carries an epoch in ``timespec_val`` as
        filler; a GDate row read timespec-first was due 1970-01-01."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        inv = gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id=inv["id"], account="Income:Sales",
            description="Work", quantity="1", price="100.00",
        )
        gb.post_invoice(invoice_id=inv["id"], post_account=AR,
                        post_date="2026-01-15")
        _q(
            business_book,
            "update slots set slot_type = 10, gdate_val = '20260214', "
            "timespec_val = '1970-01-01 00:00:00' "
            "where name = 'trans-date-due'",
        )
        row = gb.get_outstanding_invoices(compact=False)["invoices"][0]
        assert row["due_date"] == "2026-02-14"
        assert row["days_past_due"] < 20000


def _schedule(gb, **kw):
    return gb.create_scheduled_transaction(
        name="Rent", description="Rent",
        splits=[
            {"account": "Expenses:Groceries", "amount": "100.00"},
            {"account": "Assets:Checking", "amount": "-100.00"},
        ],
        start_date="2026-01-01", frequency="monthly", **kw,
    )


class TestSideFinding3AnExplicitDateStillStopsAtTheEnd:
    def test_a_date_past_the_schedules_end_is_refused(self, test_book):
        gb = GnuCashBook(str(test_book))
        sx = _schedule(gb, end_date="2026-03-31")
        with pytest.raises(ValueError, match="after 'Rent' ended"):
            gb.create_transaction_from_scheduled(
                sx["guid"], transaction_date="2026-06-01",
            )
        # A date inside the schedule's life still works.
        made = gb.create_transaction_from_scheduled(
            sx["guid"], transaction_date="2026-02-01",
        )
        assert made["status"] == "created"


class TestC27AFailedAdvanceLeavesNothingBehind:
    def test_the_instance_is_taken_back_out_and_the_retry_is_clean(
        self, test_book, monkeypatch,
    ):
        gb = GnuCashBook(str(test_book))
        sx = _schedule(gb)
        before = _q(test_book, "select count(*) from transactions")
        real = type(gb)._upgrade_book_shapes
        calls = {"n": 0}

        def failing(self, book):
            calls["n"] += 1
            raise RuntimeError("disk full")

        monkeypatch.setattr(type(gb), "_upgrade_book_shapes", failing)
        with pytest.raises(RuntimeError) as failure:
            gb.create_transaction_from_scheduled(sx["guid"])
        assert "nothing changed" in str(failure.value)
        assert calls["n"] == 1
        # No orphan instance, and the schedule has not moved.
        assert _q(test_book, "select count(*) from transactions") == before
        assert _q(
            test_book, "select instance_count from schedxactions",
        ) == [(0,)]

        monkeypatch.setattr(type(gb), "_upgrade_book_shapes", real)
        again = gb.create_transaction_from_scheduled(sx["guid"])
        assert again["status"] == "created"
        assert again["transaction_guid"]
        assert again["instance_count"] == 1
