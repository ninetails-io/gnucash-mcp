"""A document's posting transaction is read-only on every path.

The posting transaction is the document's booked state: its A/R or
A/P split sits in the document's lot, and paid and due are measured
from it. GnuCash marks it ``trans-read-only`` and refuses to void it
(``xaccTransVoid``: "Refusing to void a read-only transaction!") or
edit it in the register. Before 1.5 only ``delete_transaction``
refused here. The pre-release adversarial review
(``specs/v1.5/testing/ADVERSARIAL_REVIEW_1.5.md``) found the rest:

* C4a — ``void_transaction`` voided it, and the invoice read "paid"
  in full with an empty payments list;
* C4b — ``replace_splits`` offered ``force=true`` and, forced, took
  the A/R split out of the lot with the same result;
* C4c — ``update_transactions`` re-dated it, leaving the document
  and the ledger with two posting dates.

``_refuse_posting_record`` is the one refusal they all share now.
"""

import sqlite3
from datetime import date

import pytest

from gnucash_mcp.book import GnuCashBook
from tests.conftest import void_posting_record

AR = "Assets:Accounts Receivable"


def _q(book_path, sql, params=()):
    con = sqlite3.connect(str(book_path))
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def _snapshot(book_path, txn_prefix):
    """Everything a guarded path could have changed."""
    like = (txn_prefix + "%",)
    return {
        "txn": _q(
            book_path,
            "SELECT guid, post_date, description, num FROM transactions "
            "WHERE guid LIKE ?", like,
        ),
        "splits": sorted(_q(
            book_path,
            "SELECT account_guid, value_num, quantity_num, "
            "reconcile_state, lot_guid FROM splits WHERE tx_guid LIKE ?",
            like,
        )),
        "slots": sorted(_q(
            book_path,
            "SELECT name, string_val FROM slots WHERE obj_guid LIKE ?",
            like,
        )),
        "invoice": _q(
            book_path,
            "SELECT id, date_posted, post_txn, post_lot FROM invoices",
        ),
    }


@pytest.fixture
def posted(business_book):
    gb = GnuCashBook(str(business_book))
    gb.create_customer(name="Acme Corp")
    gb.create_invoice(customer_id="000001")
    gb.add_invoice_entry(
        invoice_id="000001", account="Income:Sales",
        description="Consulting", quantity="1", price="500.00",
    )
    result = gb.post_invoice(
        invoice_id="000001", post_account=AR, post_date="2026-01-15",
    )
    return gb, business_book, result["transaction_guid"]


NEW_SPLITS = [
    {"account": AR, "amount": "450.00"},
    {"account": "Income:Sales", "amount": "-450.00"},
]

# Every way a transaction can be changed, each aimed at the posting
# record. A new transaction-changing path belongs in this table.
ATTEMPTS = {
    "void": lambda gb, g: gb.void_transaction(g, "oops"),
    "replace_splits": lambda gb, g: gb.replace_splits(g, NEW_SPLITS),
    "replace_splits_forced": lambda gb, g: gb.replace_splits(
        g, NEW_SPLITS, force=True),
    "update_date": lambda gb, g: gb.update_transaction(
        g, trans_date=date(2026, 3, 1)),
    "update_date_forced": lambda gb, g: gb.update_transaction(
        g, trans_date=date(2026, 3, 1), force=True),
    "update_description": lambda gb, g: gb.update_transaction(
        g, description="Renamed"),
    "update_notes": lambda gb, g: gb.update_transaction(g, notes="x"),
    "update_splits": lambda gb, g: gb.update_transaction(
        g, splits={AR: "450.00", "Income:Sales": "-450.00"}, force=True),
    "update_broadcast": lambda gb, g: gb.update_transaction(
        [g], description="Renamed"),
    "delete": lambda gb, g: gb.delete_transaction(g),
    "delete_forced": lambda gb, g: gb.delete_transaction(g, force=True),
    "delete_many": lambda gb, g: gb.delete_transactions([g], force=True),
}


class TestPostingRecordIsReadOnly:
    @pytest.mark.parametrize("attempt", sorted(ATTEMPTS))
    def test_refused_and_nothing_changes(self, posted, attempt):
        gb, path, guid = posted
        before = _snapshot(path, guid)
        doc_before = gb.get_invoice("000001", owner_type="customer")

        with pytest.raises(ValueError) as refusal:
            ATTEMPTS[attempt](gb, guid)

        message = str(refusal.value)
        assert "posting record" in message
        assert "000001" in message
        assert "unpost_document" in message
        # No force door: desktop has none either.
        assert "force" not in message
        assert _snapshot(path, guid) == before
        assert gb.get_invoice("000001", owner_type="customer") == doc_before

    @pytest.mark.parametrize("on_error", ["abort", "skip"])
    def test_batch_update_rejects_the_row(self, posted, on_error):
        gb, path, guid = posted
        before = _snapshot(path, guid)

        result = gb.update_transactions(
            [{"guid": guid, "date": date(2026, 3, 1)}], on_error=on_error,
        )

        assert "rejected" in str(result)
        assert "posting record" in str(result)
        assert _snapshot(path, guid) == before

    def test_the_document_still_reads_owed(self, posted):
        """The review's symptom: after a void the invoice read
        ``paid``, ``amount_paid`` 500, payments []."""
        gb, _, guid = posted
        with pytest.raises(ValueError):
            gb.void_transaction(guid, "oops")
        doc = gb.get_invoice("000001", owner_type="customer")
        assert doc["status"] == "posted"
        assert doc["amount_paid"] == "0.00"
        assert doc["amount_due"] == "500.00"
        assert "000001" in str(gb.get_outstanding_invoices())

    def test_unposting_is_the_way_through(self, posted):
        gb, _, guid = posted
        gb.unpost_invoice(invoice_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001", account="Income:Sales",
            description="More", quantity="1", price="50.00",
        )
        again = gb.post_invoice(
            invoice_id="000001", post_account=AR, post_date="2026-03-01",
        )
        assert again["total"] == "550.00"


class TestOtherTransactionsAreUnaffected:
    def test_a_payment_can_still_be_voided(self, posted):
        """A bounced payment is voided — the documented path — and
        it is not a posting record."""
        gb, _, _ = posted
        paid = gb.pay_invoice(
            invoice_id="000001", payment_account="Assets:Checking",
            amount="500.00", payment_date="2026-01-20",
        )
        result = gb.void_transaction(paid["transaction_guid"], "bounced")
        assert result["status"] == "voided"
        doc = gb.get_invoice("000001", owner_type="customer")
        assert doc["amount_due"] == "500.00"

    def test_an_ordinary_transaction_edits_as_before(self, test_book):
        gb = GnuCashBook(str(test_book))
        made = gb.create_transaction(
            description="Lunch", trans_date=date(2026, 1, 10),
            splits=[
                {"account": "Expenses:Groceries", "amount": "12.00"},
                {"account": "Assets:Checking", "amount": "-12.00"},
            ],
        )
        gb.update_transaction(made["guid"], description="Dinner")
        gb.replace_splits(made["guid"], [
            {"account": "Expenses:Groceries", "amount": "15.00"},
            {"account": "Assets:Checking", "amount": "-15.00"},
        ])
        gb.void_transaction(made["guid"], "mistake")
        # The voided-is-immutable half of the same gate.
        with pytest.raises(ValueError, match="voided"):
            gb.update_transaction(made["guid"], description="Again")
        with pytest.raises(ValueError, match="voided"):
            gb.replace_splits(made["guid"], [
                {"account": "Expenses:Groceries", "amount": "1.00"},
                {"account": "Assets:Checking", "amount": "-1.00"},
            ])


class TestBooksThatAlreadyHoldAVoidedPosting:
    """The state was creatable through 1.4; the recovery paths must
    keep working on it."""

    def _read_only(self, path, guid):
        rows = _q(
            path,
            "SELECT string_val FROM slots WHERE name = 'trans-read-only' "
            "AND obj_guid LIKE ?", (guid + "%",),
        )
        return rows[0][0] if rows else None

    def test_unvoid_restores_the_posting_and_its_read_only_reason(
        self, posted,
    ):
        gb, path, guid = posted
        reason = self._read_only(path, guid)
        assert reason == "Generated from an invoice. Try unposting the invoice."
        void_posting_record(gb, guid)
        assert self._read_only(path, guid) == "Transaction Voided"

        gb.unvoid_transaction(guid)

        # Unvoid used to delete the slot outright, leaving a posting
        # transaction desktop would let the user edit.
        assert self._read_only(path, guid) == reason
        doc = gb.get_invoice("000001", owner_type="customer")
        assert (doc["status"], doc["amount_due"]) == ("posted", "500.00")

    def test_unvoid_of_an_ordinary_transaction_clears_read_only(
        self, test_book,
    ):
        gb = GnuCashBook(str(test_book))
        made = gb.create_transaction(
            description="Lunch", trans_date=date(2026, 1, 10),
            splits=[
                {"account": "Expenses:Groceries", "amount": "12.00"},
                {"account": "Assets:Checking", "amount": "-12.00"},
            ],
        )
        gb.void_transaction(made["guid"], "mistake")
        gb.unvoid_transaction(made["guid"])
        assert self._read_only(test_book, made["guid"]) is None

    def test_unpost_still_works_on_a_voided_posting(self, posted):
        gb, _, guid = posted
        void_posting_record(gb, guid)
        result = gb.unpost_invoice(invoice_id="000001")
        assert result["status"] == "unposted"
