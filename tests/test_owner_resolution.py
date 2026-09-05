"""Regression locks for the document-owner-resolution family.

Whole-tree review, 2026-09-04, class 1: ``invoices.owner_guid`` is a
polymorphic pointer — on a job-attached document it names the Job,
on a voucher the Employee. Seven sites keyed on the owner side by
hand (``owner_type == 4``, ``_find_vendor_by_guid(book, inv.owner_guid)``)
and each silently mishandled vouchers and job-attached documents:

- post/pay descriptions defaulted to ``Invoice NNN`` or the EMPTY
  string (1a, 1b);
- the dashboard's overdue warning mislabeled the document type and
  dropped the counterparty name (1c);
- ``list_invoices`` / ``get_outstanding_invoices`` /
  ``vendor_spending_report`` owner filters dropped the documents
  outright (1d, 1e, 1f);
- ``delete_customer`` succeeded with a job and a POSTED job-attached
  invoice on the books, and ``delete_employee`` never checked
  vouchers at all (1g).

Every site now routes through ``_find_invoice_owner_by_guid`` (name)
or ``_document_owner_clause`` (SQL filter). The structural test at
the bottom keeps them there.
"""

from __future__ import annotations

import inspect
from decimal import Decimal
from pathlib import Path

import pytest

from gnucash_mcp.book import GnuCashBook


def _voucher_book(business_book: Path, post_date: str = "2026-08-01"):
    """Employee + posted voucher; returns (gb, voucher_id)."""
    gb = GnuCashBook(str(business_book))
    gb.create_employee(name="Maria Garcia")
    v = gb.create_voucher(employee_id="000001", date_opened=post_date)
    gb.add_voucher_entry(
        voucher_id=v["id"], account="Expenses:Office Supplies",
        description="Pens", quantity="1", price="50.00",
    )
    gb.post_invoice(
        invoice_id=v["id"], post_account="Liabilities:Accounts Payable",
        post_date=post_date, owner_type="employee",
    )
    return gb, v["id"]


def _job_invoice_book(business_book: Path):
    """Customer + job + posted job-attached invoice; returns
    (gb, invoice_id, job_id)."""
    gb = GnuCashBook(str(business_book))
    gb.create_customer(name="Acme Corp")
    job = gb.create_job(
        owner_id="000001", owner_type="customer", name="API Rewrite",
    )
    inv = gb.create_invoice(
        customer_id="000001", job_id=job["id"], date_opened="2026-07-01",
    )
    gb.add_invoice_entry(
        invoice_id=inv["id"], account="Income:Sales",
        description="Work", quantity="1", price="500.00",
    )
    gb.post_invoice(
        invoice_id=inv["id"], post_account="Assets:Accounts Receivable",
        post_date="2026-07-01", owner_type="customer",
    )
    return gb, inv["id"], job["id"]


class TestPostAndPayDescriptions:
    """1a / 1b — the counterparty name reaches the ledger."""

    def test_voucher_post_and_pay_name_the_employee(self, business_book):
        gb, vid = _voucher_book(business_book)
        with gb.open(readonly=True) as b:
            post_desc = {t.description for t in b.transactions
                         if t.num == vid}
        assert post_desc == {"Maria Garcia"}
        paid = gb.pay_invoice(
            invoice_id=vid, payment_account="Assets:Checking",
            amount="50.00", payment_date="2026-08-05",
            owner_type="employee",
        )
        pay_txn = gb.get_transaction(paid["transaction_guid"])
        # Pre-fix: "" — the vendor finder was handed an employee guid.
        assert pay_txn["description"] == "Maria Garcia"

    def test_job_attached_post_and_pay_name_the_customer(
        self, business_book,
    ):
        gb, iid, _job = _job_invoice_book(business_book)
        with gb.open(readonly=True) as b:
            post_desc = {t.description for t in b.transactions
                         if t.num == iid}
        assert post_desc == {"Acme Corp"}
        paid = gb.pay_invoice(
            invoice_id=iid, payment_account="Assets:Checking",
            amount="200.00", payment_date="2026-07-15",
            owner_type="customer",
        )
        assert gb.get_transaction(paid["transaction_guid"])["description"] \
            == "Acme Corp"

    def test_explicit_empty_description_still_blanks(self, business_book):
        """The ``description=""`` deliberate-blank contract survives."""
        gb, vid = _voucher_book(business_book)
        paid = gb.pay_invoice(
            invoice_id=vid, payment_account="Assets:Checking",
            amount="50.00", description="", owner_type="employee",
        )
        assert gb.get_transaction(paid["transaction_guid"])["description"] \
            == ""


class TestOwnerFiltersAreJobAware:
    """1d / 1e / 1f — job-attached documents survive owner filters."""

    def test_list_invoices_owner_type_includes_job_attached(
        self, business_book,
    ):
        gb, iid, _ = _job_invoice_book(business_book)
        ids = [i["id"] for i in
               gb.list_invoices(owner_type="customer", compact=False)["invoices"]]
        assert iid in ids
        vendor_ids = [i["id"] for i in
                      gb.list_invoices(owner_type="vendor", compact=False)["invoices"]]
        assert iid not in vendor_ids

    def test_outstanding_owner_type_and_customer_id_include_job_attached(
        self, business_book,
    ):
        gb, iid, _ = _job_invoice_book(business_book)
        by_type = gb.get_outstanding_invoices(owner_type="customer", compact=False)
        assert [r["id"] for r in by_type["invoices"]] == [iid]
        by_id = gb.get_outstanding_invoices(customer_id="000001", compact=False)
        assert [r["id"] for r in by_id["invoices"]] == [iid]
        assert by_id["invoices"][0]["owner_name"] == "Acme Corp"
        # A different customer's filter must not pick it up.
        gb.create_customer(name="Other Co")
        assert gb.get_outstanding_invoices(
            customer_id="000002", compact=False,
        )["invoices"] == []

    def test_vendor_spending_report_includes_job_attached_bill(
        self, business_book,
    ):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        job = gb.create_job(
            owner_id="000001", owner_type="vendor", name="Fit-out",
        )
        bill = gb.create_bill(
            vendor_id="000001", job_id=job["id"], date_opened="2026-06-01",
        )
        gb.add_bill_entry(
            bill_id=bill["id"], account="Expenses:Office Supplies",
            description="Desks", quantity="2", price="150.00",
        )
        gb.post_invoice(
            invoice_id=bill["id"], post_account="Liabilities:Accounts Payable",
            post_date="2026-06-01", owner_type="vendor",
        )
        for kwargs in ({}, {"vendor_id": "000001"}):
            rep = gb.vendor_spending_report(
                "2026-06-01", "2026-06-30", compact=False, **kwargs,
            )
            assert rep["totals"]["bill_count"] == 1, kwargs
            assert rep["vendors"][0]["vendor_name"] == "Office Depot"
            assert Decimal(rep["vendors"][0]["total_billed"]) == Decimal("300")
        grouped = gb.vendor_spending_report(
            "2026-06-01", "2026-06-30", group_by="month",
        )
        assert "Office Depot" in grouped and "300.00" in grouped


class TestDeleteGuardsAreJobAndVoucherAware:
    """1g — no party can be deleted from under its documents or jobs."""

    def test_delete_customer_refuses_with_posted_job_attached_invoice(
        self, business_book,
    ):
        gb, _iid, _ = _job_invoice_book(business_book)
        with pytest.raises(ValueError, match="posted invoices"):
            gb.delete_customer("000001")

    def test_delete_customer_refuses_with_bare_job(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_job(owner_id="000001", owner_type="customer", name="J")
        with pytest.raises(ValueError, match="with jobs: 000001"):
            gb.delete_customer("000001")
        gb.delete_job("000001")
        assert gb.delete_customer("000001")["status"] == "deleted"

    def test_delete_vendor_refuses_with_bare_job(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        gb.create_job(owner_id="000001", owner_type="vendor", name="J")
        with pytest.raises(ValueError, match="with jobs"):
            gb.delete_vendor("000001")

    def test_delete_refusal_names_every_blocker_at_once(
        self, business_book,
    ):
        """A customer with a posted job-attached invoice AND the job
        hears about both in one refusal — raising on the first guard
        masked the job until the invoice was voided (bookkeeper,
        PR #172)."""
        gb, _iid, _ = _job_invoice_book(business_book)
        with pytest.raises(ValueError) as exc:
            gb.delete_customer("000001")
        msg = str(exc.value)
        assert "posted invoices: 000001" in msg
        assert "jobs: 000001" in msg
        assert "delete_job" in msg

    def test_delete_employee_refusal_offers_no_credit_note(
        self, business_book,
    ):
        """Credit notes are customer/vendor instruments; the employee
        refusal must not point at a remedy that does not exist."""
        gb, _vid = _voucher_book(business_book)
        with pytest.raises(ValueError) as exc:
            gb.delete_employee("000001")
        assert "credit note" not in str(exc.value)
        assert "Void the vouchers first" in str(exc.value)

    def test_delete_employee_refuses_with_voucher(self, business_book):
        gb, vid = _voucher_book(business_book)
        with pytest.raises(ValueError, match="posted vouchers"):
            gb.delete_employee("000001")
        gb.create_employee(name="Temp")
        gb.create_voucher(employee_id="000002")
        with pytest.raises(ValueError, match="Delete the vouchers first"):
            gb.delete_employee("000002")


class TestDashboardOverdueWarning:
    """1c — the Warnings section agrees with get_outstanding_documents."""

    def test_overdue_voucher_labeled_and_named(self, business_book):
        gb, _vid = _voucher_book(business_book, post_date="2026-01-01")
        warnings = [
            ln for ln in gb.get_book_summary().splitlines()
            if "Past due" in ln
        ]
        assert len(warnings) == 1
        assert "Past due voucher: Maria Garcia" in warnings[0]
        assert "#000001" not in warnings[0]


class TestOwnerResolutionChokepoints:
    """Structural lock: the seven sites stay on the chokepoints."""

    def test_no_side_keyed_owner_finders_at_the_fixed_sites(self):
        from gnucash_mcp.book import business, core

        for method in (
            business.BusinessMixin.post_invoice,
            business.BusinessMixin.pay_invoice,
            business.BusinessMixin.vendor_spending_report,
            business.BusinessMixin._grouped_vendor_spending,
            core.CoreMixin._collect_warnings,
        ):
            src = inspect.getsource(method)
            assert "_find_vendor_by_guid" not in src, method.__name__
            assert "_find_customer_by_guid" not in src, method.__name__
            assert "_find_invoice_owner_by_guid" in src, method.__name__

    def test_owner_filters_route_through_the_clause(self):
        from gnucash_mcp.book import business

        for method in (
            business.BusinessMixin._find_invoice,
            business.BusinessMixin.list_invoices,
            business.BusinessMixin.get_outstanding_invoices,
            business.BusinessMixin.vendor_spending_report,
            business.BusinessMixin._invoice_dependency_check,
        ):
            assert "_document_owner_clause" in inspect.getsource(method), \
                method.__name__
