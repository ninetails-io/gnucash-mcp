"""A posted document's total is what it was posted at.

``_posted_total`` reads the posting transaction's split in the
document's lot; ``_document_settlement`` measures paid and due from
it. The total used to be re-derived from the entries on every read,
so anything that changed the derivation after posting moved the
"total" away from the booking and reported the gap as money paid:

* a tax table edited after posting (5% → 10%): a 105 invoice, paid
  105, read ``total 110, amount_paid 110``;
* a document desktop posted with a line discount this server does
  not compute: posted at 90, read ``total 100, amount_paid 10`` with
  an empty payments list.

Pre-release adversarial review 2026-09-30
(``specs/v1.5/testing/ADVERSARIAL_REVIEW_1.5.md``, C10 / BL-14 /
the read half of C2). It is also what makes correcting the tax
rounding safe: a document posted under the old rounding keeps the
total it was booked at.
"""

import sqlite3
from decimal import Decimal

import pytest

from gnucash_mcp.book import GnuCashBook

AR = "Assets:Accounts Receivable"
AP = "Liabilities:Accounts Payable"


def _q(book_path, sql, params=()):
    con = sqlite3.connect(str(book_path))
    try:
        rows = con.execute(sql, params).fetchall()
        con.commit()
        return rows
    finally:
        con.close()


def _tax_table(gb, name, pct):
    try:
        gb.create_account(
            name="GST Payable", account_type="LIABILITY",
            parent="Liabilities",
        )
    except ValueError:
        pass
    gb.create_taxtable(name=name, entries=[{
        "type": "percentage", "amount": pct,
        "account": "Liabilities:GST Payable",
    }])


def _invoice(gb, price="100.00", taxtable=None, customer=True):
    if customer:
        try:
            gb.create_customer(name="Acme Corp")
        except ValueError:
            pass
    inv = gb.create_invoice(customer_id="000001")
    kwargs = {"taxtable": taxtable} if taxtable else {}
    gb.add_invoice_entry(
        invoice_id=inv["id"], account="Income:Sales",
        description="Work", quantity="1", price=price, **kwargs,
    )
    return inv["id"]


def _rebook_posting(book_path, doc_id, new_total):
    """Byte-faithful stand-in for "posted elsewhere at a different
    amount than the entries now compute": rewrite the posting
    transaction's two legs. The state outlives the door that made it
    — desktop posts discounted lines this server totals at full
    price."""
    txn, acct = _q(
        book_path, "SELECT post_txn, post_acc FROM invoices WHERE id = ?",
        (doc_id,),
    )[0]
    cents = int(Decimal(new_total) * 100)
    for guid, account, value in _q(
        book_path,
        "SELECT guid, account_guid, value_num FROM splits WHERE tx_guid = ?",
        (txn,),
    ):
        signed = cents if value > 0 else -cents
        _q(
            book_path,
            "UPDATE splits SET value_num = ?, value_denom = 100, "
            "quantity_num = ?, quantity_denom = 100 WHERE guid = ?",
            (signed, signed, guid),
        )


class TestPostedTotalIsTheBooking:
    def test_tax_table_edit_after_payment_moves_nothing(self, business_book):
        gb = GnuCashBook(str(business_book))
        _tax_table(gb, "T5", "5")
        doc = _invoice(gb, taxtable="T5")
        posted = gb.post_invoice(invoice_id=doc, post_account=AR)
        assert posted["total"] == "105.00"
        gb.pay_invoice(
            invoice_id=doc, payment_account="Assets:Checking",
            amount="105.00",
        )

        gb.update_taxtable("T5", entries=[{
            "type": "percentage", "amount": "10",
            "account": "Liabilities:GST Payable",
        }], force=True)

        got = gb.get_invoice(doc, owner_type="customer")
        assert got["total"] == "105.00"
        assert got["amount_paid"] == "105.00"
        assert got["amount_due"] == "0.00"
        assert got["status"] == "paid"
        # The drift is named, not hidden.
        assert "110.00" in got["total_note"]
        assert "105.00" in got["total_note"]

    def test_amount_paid_is_the_sum_of_the_payments(self, business_book):
        gb = GnuCashBook(str(business_book))
        _tax_table(gb, "T5", "5")
        doc = _invoice(gb, taxtable="T5")
        gb.post_invoice(invoice_id=doc, post_account=AR)
        for amount in ("40.00", "25.00"):
            gb.pay_invoice(
                invoice_id=doc, payment_account="Assets:Checking",
                amount=amount,
            )
        gb.update_taxtable("T5", entries=[{
            "type": "percentage", "amount": "10",
            "account": "Liabilities:GST Payable",
        }], force=True)

        got = gb.get_invoice(doc, owner_type="customer")
        paid = sum(Decimal(p["amount"]) for p in got["payments"])
        assert Decimal(got["amount_paid"]) == paid == Decimal("65.00")
        assert got["amount_due"] == "40.00"

    def test_posted_below_the_entries_reads_unpaid_not_part_paid(
        self, business_book,
    ):
        """Desktop posts a 10%-discounted 100.00 line at 90."""
        gb = GnuCashBook(str(business_book))
        doc = _invoice(gb)
        gb.post_invoice(invoice_id=doc, post_account=AR)
        _rebook_posting(business_book, doc, "90.00")

        got = gb.get_invoice(doc, owner_type="customer")
        assert got["total"] == "90.00"
        assert got["amount_paid"] == "0.00"
        assert got["amount_due"] == "90.00"
        assert got["payments"] == []
        assert got["status"] == "posted"
        assert "100.00" in got["total_note"]

    def test_every_surface_reads_the_posted_amount(self, business_book):
        gb = GnuCashBook(str(business_book))
        doc = _invoice(gb)
        gb.post_invoice(invoice_id=doc, post_account=AR)
        _rebook_posting(business_book, doc, "90.00")

        row = next(
            line for line in gb.list_invoices().splitlines()
            if line.startswith(doc + "\t")
        )
        assert "USD 90" in row and "USD 100" not in row

        outstanding = gb.get_outstanding_invoices(compact=False)
        entry = next(
            d for d in outstanding["invoices"] if d["id"] == doc
        )
        assert Decimal(entry["original_amount"]) == Decimal("90")
        assert Decimal(entry["amount_due"]) == Decimal("90")

        paid = gb.pay_invoice(
            invoice_id=doc, payment_account="Assets:Checking",
            amount="30.00",
        )
        assert paid["total_paid"] == "30.00"
        assert paid["remaining_balance"] == "60.00"
        assert gb.get_invoice(
            doc, owner_type="customer",
        )["amount_paid"] == paid["total_paid"]


class TestPostedTotalDirection:
    """``_posted_total`` is direction-normalized like the settlement:
    a bill and a credit note post to the credit side and still read
    a positive total."""

    def test_bill(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Supplier")
        bill = gb.create_bill(vendor_id="000001")
        gb.add_bill_entry(
            bill_id=bill["id"], account="Expenses:Services",
            description="Hosting", quantity="2", price="75.00",
        )
        gb.post_invoice(
            invoice_id=bill["id"], post_account=AP, owner_type="vendor",
        )
        got = gb.get_invoice(bill["id"], owner_type="vendor")
        assert got["total"] == "150.00"
        assert got["amount_paid"] == "0.00"
        assert got["amount_due"] == "150.00"
        assert "total_note" not in got

    def test_credit_note(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        cn = gb.create_credit_note(owner_id="000001", owner_type="customer")
        gb.add_credit_note_entry(
            credit_note_id=cn["id"], account="Income:Sales",
            description="Refund", quantity="1", price="40.00",
        )
        gb.post_invoice(
            invoice_id=cn["id"], post_account=AR, owner_type="customer",
        )
        got = gb.get_invoice(cn["id"], owner_type="customer")
        assert got["total"] == "40.00"
        assert got["amount_due"] == "40.00"
        assert "total_note" not in got


class TestWhenThePostingCannotBeRead:
    def test_a_draft_totals_its_entries(self, business_book):
        gb = GnuCashBook(str(business_book))
        doc = _invoice(gb, price="123.45")
        got = gb.get_invoice(doc, owner_type="customer")
        assert got["total"] == "123.45"
        assert got["status"] == "open"
        assert "total_note" not in got
        assert "amount_paid" not in got

    def test_a_voided_posting_falls_back_to_the_entries(self, business_book):
        """The voided split is a zeroed zombie, not a total of zero.
        (Voiding a posting is its own defect — review C4a — but books
        that already hold one must keep reading as they did.)"""
        gb = GnuCashBook(str(business_book))
        doc = _invoice(gb)
        posted = gb.post_invoice(invoice_id=doc, post_account=AR)
        gb.void_transaction(posted["transaction_guid"], "test setup")

        got = gb.get_invoice(doc, owner_type="customer")
        assert got["total"] == "100.00"


class TestJobReportReadsTheSettlement:
    def test_billed_is_the_posted_amount(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        job = gb.create_job(
            owner_id="000001", owner_type="customer", name="Project",
        )
        inv = gb.create_invoice(customer_id="000001", job_id=job["id"])
        gb.add_invoice_entry(
            invoice_id=inv["id"], account="Income:Sales",
            description="Work", quantity="1", price="100.00",
        )
        gb.post_invoice(invoice_id=inv["id"], post_account=AR)
        _rebook_posting(business_book, inv["id"], "90.00")
        gb.pay_invoice(
            invoice_id=inv["id"], payment_account="Assets:Checking",
            amount="30.00", owner_type="customer",
        )

        report = gb.get_job_report(job["id"])
        row = next(r for r in report["invoices"] if r["id"] == inv["id"])
        assert Decimal(row["billed"]) == Decimal("90")
        assert Decimal(row["paid"]) == Decimal("30")
        assert Decimal(row["outstanding"]) == Decimal("60")
