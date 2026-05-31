"""Tests for write verification helpers.

These tests verify that the _verify_write, _verify_composite_write,
and _verify_delete helpers correctly detect successful and failed
raw SQL writes.
"""

import uuid
from pathlib import Path

import piecash
import pytest

from gnucash_mcp.book import (
    GnuCashBook,
    _verify_composite_write,
    _verify_delete,
    _verify_write,
)


# ── _verify_write tests ──────────────────────────────────────────


class TestVerifyWrite:
    """Tests for _verify_write (single GUID primary key)."""

    def test_passes_after_valid_insert(self, business_book: Path):
        """Verify passes when a row exists with the given GUID."""
        from piecash.business.invoice import Billterm

        book_obj = GnuCashBook(str(business_book))
        with book_obj.open(readonly=False) as book:
            bt_guid = uuid.uuid4().hex
            book.session.execute(
                Billterm.__table__.insert().values(
                    guid=bt_guid,
                    name="Test Term",
                    description="",
                    refcount=0,
                    invisible=0,
                    type="GNC_TERM_TYPE_DAYS",
                    duedays=30,
                    discountdays=0,
                    discount_num=0,
                    discount_denom=1,
                    cutoff=0,
                )
            )
            # Should not raise
            _verify_write(
                book.session, Billterm.__table__, bt_guid,
                "Billterm 'Test Term'",
            )

    def test_raises_for_nonexistent_guid(self, business_book: Path):
        """Verify raises RuntimeError when GUID doesn't exist."""
        from piecash.business.invoice import Billterm

        book_obj = GnuCashBook(str(business_book))
        with book_obj.open(readonly=False) as book:
            fake_guid = uuid.uuid4().hex
            with pytest.raises(RuntimeError, match="Write verification failed"):
                _verify_write(
                    book.session, Billterm.__table__, fake_guid,
                    "Billterm 'Nonexistent'",
                )


# ── _verify_composite_write tests ────────────────────────────────


class TestVerifyCompositeWrite:
    """Tests for _verify_composite_write (composite primary key)."""

    def test_passes_after_valid_insert(self, scheduled_book: Path):
        """Verify passes for a composite-key insert (Recurrence)."""
        from datetime import date

        from piecash._common import Recurrence
        from piecash.budget import Budget

        book_obj = GnuCashBook(str(scheduled_book))
        with book_obj.open(readonly=False) as book:
            budget_guid = uuid.uuid4().hex
            book.session.execute(
                Budget.__table__.insert().values(
                    guid=budget_guid,
                    name="Test Budget",
                    description="",
                    num_periods=12,
                )
            )
            book.session.execute(
                Recurrence.__table__.insert().values(
                    obj_guid=budget_guid,
                    recurrence_mult=1,
                    recurrence_period_type="month",
                    recurrence_period_start=date(2026, 1, 1),
                    recurrence_weekend_adjust="none",
                )
            )
            # Should not raise
            _verify_composite_write(
                book.session, Recurrence.__table__,
                {"obj_guid": budget_guid},
                "Recurrence for test budget",
            )

    def test_raises_for_wrong_conditions(self, scheduled_book: Path):
        """Verify raises when composite key conditions don't match."""
        from piecash._common import Recurrence

        book_obj = GnuCashBook(str(scheduled_book))
        with book_obj.open(readonly=False) as book:
            fake_guid = uuid.uuid4().hex
            with pytest.raises(RuntimeError, match="Write verification failed"):
                _verify_composite_write(
                    book.session, Recurrence.__table__,
                    {"obj_guid": fake_guid},
                    "Recurrence for nonexistent",
                )


# ── _verify_delete tests ─────────────────────────────────────────


class TestVerifyDelete:
    """Tests for _verify_delete (slot deletion)."""

    def test_passes_after_valid_delete(self, scheduled_book: Path):
        """Verify passes when the slot row is successfully deleted."""
        from piecash.kvp import KVP_Type, Slot

        book_obj = GnuCashBook(str(scheduled_book))
        with book_obj.open(readonly=False) as book:
            obj_guid = uuid.uuid4().hex
            book.session.execute(
                Slot.__table__.insert().values(
                    obj_guid=obj_guid,
                    name="test-slot",
                    slot_type=KVP_Type.KVP_TYPE_STRING,
                    string_val="test value",
                )
            )
            # Delete it via SQLAlchemy Core
            book.session.execute(
                Slot.__table__.delete().where(
                    (Slot.__table__.c.obj_guid == obj_guid)
                    & (Slot.__table__.c.name == "test-slot")
                )
            )
            # Should not raise
            _verify_delete(
                book.session,
                Slot.__table__,
                {"obj_guid": obj_guid, "name": "test-slot"},
                "Test slot deletion",
            )

    def test_raises_when_row_still_exists(self, scheduled_book: Path):
        """Verify raises when the row was NOT deleted."""
        from piecash.kvp import KVP_Type, Slot

        book_obj = GnuCashBook(str(scheduled_book))
        with book_obj.open(readonly=False) as book:
            obj_guid = uuid.uuid4().hex
            book.session.execute(
                Slot.__table__.insert().values(
                    obj_guid=obj_guid,
                    name="still-here",
                    slot_type=KVP_Type.KVP_TYPE_STRING,
                    string_val="should not be deleted",
                )
            )
            # Don't delete — verify should fail
            with pytest.raises(RuntimeError, match="Delete verification failed"):
                _verify_delete(
                    book.session,
                    Slot.__table__,
                    {"obj_guid": obj_guid, "name": "still-here"},
                    "Test slot that should still exist",
                )

    def test_passes_for_non_slot_table(self, business_book: Path):
        """Generalized _verify_delete works for any piecash Core table.

        Before this release the helper was hardcoded to ``FROM slots``;
        the generalization was added so Entry / Invoice / etc. deletes
        can pair with verification too.
        """
        from piecash.business.invoice import Invoice

        book_obj = GnuCashBook(str(business_book))
        book_obj.create_customer("Verify Client")
        inv = book_obj.create_invoice(customer_id="000001")
        # v1.3.1: create_invoice no longer surfaces ``guid``;
        # look up via the ORM for direct-SQL deletion below.
        with book_obj.open(readonly=False) as book:
            inv_guid = book.session.query(Invoice).filter_by(
                id=inv["id"],
            ).first().guid

            book.session.execute(
                Invoice.__table__.delete().where(
                    Invoice.__table__.c.guid == inv_guid
                )
            )
            # Should not raise — the invoice is gone
            _verify_delete(
                book.session,
                Invoice.__table__,
                {"guid": inv_guid},
                f"Invoice {inv['id']}",
            )


# ── Integration: existing operations trigger verification ────────


class TestVerificationIntegration:
    """Verify that write operations with verification succeed end-to-end."""

    def test_create_budget_with_verification(self, scheduled_book: Path):
        """Budget creation should succeed with verification enabled."""
        book_obj = GnuCashBook(str(scheduled_book))
        result = book_obj.create_budget("Verified Budget", year=2026)
        assert result["status"] == "created"
        assert result["name"] == "Verified Budget"

    def test_create_scheduled_transaction_with_verification(
        self, scheduled_book: Path
    ):
        """Scheduled transaction creation should succeed with verification."""
        book_obj = GnuCashBook(str(scheduled_book))
        result = book_obj.create_scheduled_transaction(
            name="Verified Rent",
            description="Monthly rent",
            splits=[
                {"account": "Expenses:Rent", "amount": "1500.00"},
                {"account": "Assets:Checking", "amount": "-1500.00"},
            ],
            start_date="2026-03-01",
            frequency="monthly",
        )
        assert result["status"] == "created"
        assert result["name"] == "Verified Rent"

    def test_delete_scheduled_transaction_with_verification(
        self, scheduled_book: Path
    ):
        """Scheduled transaction deletion should succeed with verification."""
        book_obj = GnuCashBook(str(scheduled_book))
        created = book_obj.create_scheduled_transaction(
            name="To Delete",
            description="Will be deleted",
            splits=[
                {"account": "Expenses:Rent", "amount": "500.00"},
                {"account": "Assets:Checking", "amount": "-500.00"},
            ],
            start_date="2026-03-01",
            frequency="monthly",
        )
        result = book_obj.delete_scheduled_transaction(created["guid"])
        assert result["status"] == "deleted"

    def test_create_billterm_with_verification(self, business_book: Path):
        """Billterm creation should succeed with verification."""
        book_obj = GnuCashBook(str(business_book))
        result = book_obj.create_billterm("Net 30", due_days=30)
        assert result["status"] == "created"

    def test_create_invoice_with_verification(self, business_book: Path):
        """Invoice creation should succeed with verification."""
        book_obj = GnuCashBook(str(business_book))
        book_obj.create_customer("Test Client")
        result = book_obj.create_invoice(customer_id="000001")
        assert result["status"] == "created"

    def test_create_bill_with_verification(self, business_book: Path):
        """Bill creation should succeed with verification."""
        book_obj = GnuCashBook(str(business_book))
        book_obj.create_vendor("Test Vendor")
        result = book_obj.create_bill(vendor_id="000001")
        assert result["status"] == "created"

    def test_add_invoice_entry_with_verification(self, business_book: Path):
        """Invoice entry addition should succeed with verification."""
        book_obj = GnuCashBook(str(business_book))
        book_obj.create_customer("Entry Client")
        inv = book_obj.create_invoice(customer_id="000001")
        result = book_obj.add_invoice_entry(
            invoice_id=inv["id"],
            account="Income:Sales",
            description="Test service",
            quantity="1",
            price="100.00",
        )
        assert result["status"] == "created"

    def test_add_bill_entry_with_verification(self, business_book: Path):
        """Bill entry addition should succeed with verification."""
        book_obj = GnuCashBook(str(business_book))
        book_obj.create_vendor("Entry Vendor")
        bill = book_obj.create_bill(vendor_id="000001")
        result = book_obj.add_bill_entry(
            bill_id=bill["id"],
            account="Expenses:Office Supplies",
            description="Test supplies",
            quantity="1",
            price="50.00",
        )
        assert result["status"] == "created"

    def test_post_invoice_with_verification(self, business_book: Path):
        """Invoice posting (slot writes) should succeed with verification."""
        book_obj = GnuCashBook(str(business_book))
        book_obj.create_customer("Post Client")
        inv = book_obj.create_invoice(customer_id="000001")
        book_obj.add_invoice_entry(
            invoice_id=inv["id"],
            account="Income:Sales",
            description="Consulting",
            quantity="10",
            price="150.00",
        )
        result = book_obj.post_invoice(
            invoice_id=inv["id"],
            post_account="Assets:Accounts Receivable",
        )
        assert result["status"] == "posted"
