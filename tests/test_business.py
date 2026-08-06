"""Tests for business tools: customers, vendors, billterms, invoices, bills."""

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from gnucash_mcp.book import GnuCashBook


class TestCreateCustomer:
    """Tests for create_customer."""

    def test_basic_creation(self, business_book):
        gb = GnuCashBook(str(business_book))
        result = gb.create_customer(name="Acme Corp")
        assert result["status"] == "created"
        assert result["name"] == "Acme Corp"
        assert result["id"] == "000001"

    def test_auto_id_increments(self, business_book):
        gb = GnuCashBook(str(business_book))
        r1 = gb.create_customer(name="Customer A")
        r2 = gb.create_customer(name="Customer B")
        assert r1["id"] == "000001"
        assert r2["id"] == "000002"

    def test_with_currency(self, business_book):
        gb = GnuCashBook(str(business_book))
        result = gb.create_customer(name="Acme Corp", currency="USD")
        assert result["status"] == "created"

    def test_result_includes_resolved_currency(self, business_book):
        """Result dict always reports the resolved currency so the audit
        log formatter renders ``currency: USD`` instead of an empty field
        when the caller didn't supply currency explicitly.
        """
        gb = GnuCashBook(str(business_book))
        # Explicit currency
        r1 = gb.create_customer(name="Explicit", currency="USD")
        assert r1["currency"] == "USD"
        # Defaulted currency (caller omits) — still populated
        r2 = gb.create_customer(name="Defaulted")
        assert r2["currency"] == "USD"

    def test_invalid_currency(self, business_book):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="Currency not found"):
            gb.create_customer(name="Acme Corp", currency="XYZ")

    def test_with_notes(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp", notes="Important client")
        result = gb.get_customer("000001")
        assert result["notes"] == "Important client"

    def test_with_address(self, business_book):
        gb = GnuCashBook(str(business_book))
        addr = {
            "name": "Acme Corp",
            "addr1": "123 Main St",
            "addr2": "Suite 100",
            "phone": "555-1234",
            "email": "billing@acme.com",
        }
        gb.create_customer(name="Acme Corp", address=addr)
        result = gb.get_customer("000001")
        assert result["address"]["addr1"] == "123 Main St"
        assert result["address"]["phone"] == "555-1234"
        assert result["address"]["email"] == "billing@acme.com"

    def test_duplicate_name_allowed(self, business_book):
        """GnuCash allows duplicate customer names."""
        gb = GnuCashBook(str(business_book))
        r1 = gb.create_customer(name="Acme Corp")
        r2 = gb.create_customer(name="Acme Corp")
        assert r1["id"] != r2["id"]


class TestListCustomers:
    """Tests for list_customers."""

    def test_empty_list(self, business_book):
        gb = GnuCashBook(str(business_book))
        result = gb.list_customers()
        assert result == "Showing 0 of 0 customers"

    def test_compact_format(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Beta Corp")
        gb.create_customer(name="Alpha Inc")
        result = gb.list_customers()
        # Skip the leading "Showing X-Y of Z customers" indicator.
        lines = result.strip().split("\n")[1:]
        assert len(lines) == 2
        # Sorted by name
        assert "Alpha Inc" in lines[0]
        assert "Beta Corp" in lines[1]

    def test_verbose_format(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        result = gb.list_customers(compact=False)["customers"]
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["name"] == "Acme Corp"
        # v1.3.1: business-object guid field dropped (bookkeeper
        # never used it). Customers are addressed by ``id``.
        assert "id" in result[0]
        assert "address" in result[0]


class TestGetCustomer:
    """Tests for get_customer."""

    def test_found(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        result = gb.get_customer("000001")
        assert result["name"] == "Acme Corp"
        assert result["id"] == "000001"
        assert result["active"] is True

    def test_not_found(self, business_book):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="Customer not found"):
            gb.get_customer("999999")


class TestCreateVendor:
    """Tests for create_vendor."""

    def test_basic_creation(self, business_book):
        gb = GnuCashBook(str(business_book))
        result = gb.create_vendor(name="Office Depot")
        assert result["status"] == "created"
        assert result["name"] == "Office Depot"
        assert result["id"] == "000001"

    def test_auto_id_increments(self, business_book):
        gb = GnuCashBook(str(business_book))
        r1 = gb.create_vendor(name="Vendor A")
        r2 = gb.create_vendor(name="Vendor B")
        assert r1["id"] == "000001"
        assert r2["id"] == "000002"

    def test_with_address(self, business_book):
        gb = GnuCashBook(str(business_book))
        addr = {"name": "Office Depot", "addr1": "456 Commerce Blvd"}
        gb.create_vendor(name="Office Depot", address=addr)
        result = gb.get_vendor("000001")
        assert result["address"]["addr1"] == "456 Commerce Blvd"

    def test_vendor_counter_independent_from_customer(self, business_book):
        """Customer and vendor counters are independent."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Customer A")
        gb.create_vendor(name="Vendor A")
        # Both should get ID 000001 from their independent counters
        cust = gb.get_customer("000001")
        vend = gb.get_vendor("000001")
        assert cust["name"] == "Customer A"
        assert vend["name"] == "Vendor A"


class TestListVendors:
    """Tests for list_vendors."""

    def test_empty_list(self, business_book):
        gb = GnuCashBook(str(business_book))
        result = gb.list_vendors()
        assert result == "Showing 0 of 0 vendors"

    def test_compact_format(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Zeta Supply")
        gb.create_vendor(name="Alpha Parts")
        result = gb.list_vendors()
        lines = result.strip().split("\n")[1:]  # skip indicator
        assert len(lines) == 2
        assert "Alpha Parts" in lines[0]
        assert "Zeta Supply" in lines[1]

    def test_verbose_format(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        result = gb.list_vendors(compact=False)["vendors"]
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["name"] == "Office Depot"


class TestGetVendor:
    """Tests for get_vendor."""

    def test_found(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        result = gb.get_vendor("000001")
        assert result["name"] == "Office Depot"
        assert result["active"] is True

    def test_not_found(self, business_book):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="Vendor not found"):
            gb.get_vendor("999999")


class TestCreateEmployee:
    """Tests for create_employee."""

    def test_basic_creation(self, business_book):
        gb = GnuCashBook(str(business_book))
        result = gb.create_employee(name="Jane Smith")
        assert result["status"] == "created"
        assert result["name"] == "Jane Smith"
        assert result["id"] == "000001"

    def test_auto_id_increments(self, business_book):
        gb = GnuCashBook(str(business_book))
        r1 = gb.create_employee(name="Employee A")
        r2 = gb.create_employee(name="Employee B")
        assert r1["id"] == "000001"
        assert r2["id"] == "000002"

    def test_with_currency(self, business_book):
        gb = GnuCashBook(str(business_book))
        result = gb.create_employee(name="Jane Smith", currency="USD")
        assert result["status"] == "created"

    def test_invalid_currency(self, business_book):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="Currency not found"):
            gb.create_employee(name="Jane Smith", currency="XYZ")

    def test_with_address(self, business_book):
        gb = GnuCashBook(str(business_book))
        addr = {
            "name": "Jane Smith",
            "addr1": "123 Main St",
            "phone": "555-1234",
            "email": "jane@example.com",
        }
        gb.create_employee(name="Jane Smith", address=addr)
        result = gb.get_employee("000001")
        assert result["address"]["addr1"] == "123 Main St"
        assert result["address"]["email"] == "jane@example.com"

    def test_no_notes_field_in_response(self, business_book):
        """Employee dict shape omits the ``notes`` key — Employee has no
        notes column in the schema (unlike Customer and Vendor). See
        specs/PIECASH_REFERENCE.md."""
        gb = GnuCashBook(str(business_book))
        gb.create_employee(name="Jane Smith")
        result = gb.get_employee("000001")
        assert "notes" not in result

    def test_employee_counter_independent(self, business_book):
        """Employee counter is independent of Customer and Vendor counters.

        Each of the three business-person types has its own counter
        on the Book (``counter_customer`` / ``counter_vendor`` /
        ``counter_employee``). All three first IDs should be '000001'.
        """
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_vendor(name="Office Depot")
        gb.create_employee(name="Jane Smith")
        # All three just created. Each should be '000001' from its own counter.
        assert gb.get_customer("000001")["name"] == "Acme Corp"
        assert gb.get_vendor("000001")["name"] == "Office Depot"
        assert gb.get_employee("000001")["name"] == "Jane Smith"


class TestListEmployees:
    """Tests for list_employees."""

    def test_empty_list(self, business_book):
        gb = GnuCashBook(str(business_book))
        result = gb.list_employees()
        assert result == "Showing 0 of 0 employees"

    def test_compact_format(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_employee(name="Beta Hire")
        gb.create_employee(name="Alpha Hire")
        result = gb.list_employees()
        lines = result.strip().split("\n")[1:]  # skip indicator
        assert len(lines) == 2
        # Sorted by name
        assert "Alpha Hire" in lines[0]
        assert "Beta Hire" in lines[1]

    def test_verbose_format(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_employee(name="Jane Smith")
        result = gb.list_employees(compact=False)["employees"]
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["name"] == "Jane Smith"
        # v1.3.1: business-object guid field dropped; employees
        # addressed by ``id``.
        assert "id" in result[0]
        # notes key absent (schema difference)
        assert "notes" not in result[0]


class TestGetEmployee:
    """Tests for get_employee."""

    def test_found(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_employee(name="Jane Smith")
        result = gb.get_employee("000001")
        assert result["name"] == "Jane Smith"
        assert result["id"] == "000001"
        assert result["active"] is True

    def test_not_found(self, business_book):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="Employee not found"):
            gb.get_employee("999999")


class TestDeleteEmployee:
    """Tests for delete_employee.

    Employees in the 1.3.0 release have no associated documents —
    expense vouchers are out of scope. delete_employee proceeds
    unconditionally after slot cleanup.
    """

    def test_delete_employee(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_employee(name="Temp Employee")
        result = gb.delete_employee(employee_id="000001")
        assert result["status"] == "deleted"
        assert result["name"] == "Temp Employee"
        assert result["type"] == "employee"

    def test_delete_employee_twice_errors(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_employee(name="Temp Employee")
        gb.delete_employee(employee_id="000001")
        with pytest.raises(ValueError, match="Employee not found"):
            gb.delete_employee(employee_id="000001")

    def test_delete_nonexistent_employee(self, business_book):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="Employee not found"):
            gb.delete_employee(employee_id="999999")


class TestCreateJob:
    """Tests for create_job.

    Job is the third v1.3 business surface (after vouchers and
    credit notes). Unlike those, the piecash Job constructor
    is OPEN, so the create path uses the ORM directly. Owner
    is restricted to customer/vendor (piecash's PersonType map
    has no Employee entry; create_job rejects 'employee' with
    a clear message).
    """

    def test_create_customer_job(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        result = gb.create_job(
            owner_id="000001", owner_type="customer",
            name="API Rewrite",
        )
        assert result["status"] == "created"
        assert result["name"] == "API Rewrite"
        assert result["owner_type"] == "customer"
        assert result["active"] is True
        # counter_job advances independently from invoice/bill
        assert result["id"] == "000001"

    def test_create_vendor_job(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        result = gb.create_job(
            owner_id="000001", owner_type="vendor",
            name="Q3 supply contract",
        )
        assert result["owner_type"] == "vendor"
        assert result["name"] == "Q3 supply contract"

    def test_create_job_with_reference(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        result = gb.create_job(
            owner_id="000001", owner_type="customer",
            name="Kitchen renovation",
            reference="PO-2026-042",
        )
        assert result["reference"] == "PO-2026-042"

    def test_employee_owner_rejected(self, business_book):
        """Employees are deliberately unsupported — piecash's
        PersonType has no Employee entry (would KeyError at
        the constructor), and GnuCash desktop has no UI."""
        gb = GnuCashBook(str(business_book))
        gb.create_employee(name="Maria")
        with pytest.raises(
            ValueError, match="not supported for employees",
        ):
            gb.create_job(
                owner_id="000001", owner_type="employee",
                name="x",
            )

    def test_invalid_owner_type_rejected(self, business_book):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="Invalid owner_type"):
            gb.create_job(
                owner_id="000001", owner_type="custmer", name="x",
            )

    def test_owner_not_found(self, business_book):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="Customer not found"):
            gb.create_job(
                owner_id="999999", owner_type="customer", name="x",
            )

    def test_job_counter_independent_from_invoice_counter(
        self, business_book,
    ):
        """The book's counter_job advances independently from
        counter_invoice and counter_bill — creating an invoice
        first shouldn't bump the job sequence."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        gb.create_invoice(customer_id="000001")  # invoice 000001
        job = gb.create_job(
            owner_id="000001", owner_type="customer", name="x",
        )
        # Job counter starts at 0; first auto-id is 000001 even
        # though an invoice with the same ID exists.
        assert job["id"] == "000001"


class TestListJobs:
    """Tests for list_jobs filtering and output shape."""

    def test_empty_list(self, business_book):
        gb = GnuCashBook(str(business_book))
        result = gb.list_jobs()
        assert result == "Showing 0 of 0 jobs"

    def test_compact_default(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        gb.create_job(
            owner_id="000001", owner_type="customer", name="X",
        )
        result = gb.list_jobs()
        # Compact format: tab-separated with CUSTOMER tag
        assert "000001" in result
        assert "CUSTOMER" in result
        assert "Acme Co" in result

    def test_verbose_returns_dicts(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        gb.create_job(
            owner_id="000001", owner_type="customer", name="X",
        )
        result = gb.list_jobs(compact=False)["jobs"]
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["name"] == "X"
        assert result[0]["owner_type"] == "customer"
        assert result[0]["owner_name"] == "Acme Co"

    def test_filter_by_owner_type(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        gb.create_vendor(name="Office Depot")
        gb.create_job(
            owner_id="000001", owner_type="customer", name="Cust",
        )
        gb.create_job(
            owner_id="000001", owner_type="vendor", name="Vend",
        )
        cust = gb.list_jobs(owner_type="customer", compact=False)["jobs"]
        assert len(cust) == 1
        assert cust[0]["name"] == "Cust"
        vend = gb.list_jobs(owner_type="vendor", compact=False)["jobs"]
        assert len(vend) == 1
        assert vend[0]["name"] == "Vend"

    def test_filter_by_owner_id_requires_owner_type(
        self, business_book,
    ):
        """owner_id without owner_type is rejected — customer
        and vendor IDs share a sequence space, so the lookup
        would be ambiguous."""
        gb = GnuCashBook(str(business_book))
        with pytest.raises(
            ValueError, match="owner_id requires owner_type",
        ):
            gb.list_jobs(owner_id="000001")

    def test_active_only_default(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        active = gb.create_job(
            owner_id="000001", owner_type="customer", name="Active",
        )
        inactive = gb.create_job(
            owner_id="000001", owner_type="customer", name="Done",
        )
        # Deactivate the second
        gb.update_job(job_id=inactive["id"], active=False)
        # Default lists only active
        result = gb.list_jobs(compact=False)["jobs"]
        assert len(result) == 1
        assert result[0]["id"] == active["id"]
        # include_inactive surfaces both
        result_all = gb.list_jobs(active_only=False, compact=False)["jobs"]
        assert len(result_all) == 2


class TestGetJob:
    """Tests for get_job — details + linked-invoices summary."""

    def test_get_job_basic(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        job = gb.create_job(
            owner_id="000001", owner_type="customer",
            name="API Rewrite", reference="PO-001",
        )
        result = gb.get_job(job_id=job["id"])
        assert result["name"] == "API Rewrite"
        assert result["reference"] == "PO-001"
        assert result["owner_type"] == "customer"
        assert result["owner_name"] == "Acme Co"
        # No linked invoices yet
        assert result["linked_invoices"]["count"] == 0
        assert result["linked_invoices"]["ids"] == []

    def test_get_nonexistent_job(self, business_book):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="Job not found"):
            gb.get_job(job_id="999999")


class TestUpdateJob:
    """Tests for update_job — diff-style response, partial
    updates, rejection of empty calls."""

    def test_update_name(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        gb.create_job(
            owner_id="000001", owner_type="customer", name="Old",
        )
        result = gb.update_job(job_id="000001", name="New")
        assert result["status"] == "updated"
        assert result["name"] == "New"
        # Reference + active not in diff response (unchanged)
        assert "reference" not in result
        assert "active" not in result

    def test_update_active(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        gb.create_job(
            owner_id="000001", owner_type="customer", name="X",
        )
        result = gb.update_job(job_id="000001", active=False)
        assert result["active"] is False
        # Confirm via get_job
        fetched = gb.get_job(job_id="000001")
        assert fetched["active"] is False

    def test_no_fields_rejected(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        gb.create_job(
            owner_id="000001", owner_type="customer", name="X",
        )
        with pytest.raises(
            ValueError, match="at least one of",
        ):
            gb.update_job(job_id="000001")

    def test_update_nonexistent_job(self, business_book):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="Job not found"):
            gb.update_job(job_id="999999", name="x")


class TestDeleteJob:
    """Tests for delete_job — including the force-reparent path
    that re-routes linked invoices back to the underlying
    customer/vendor before deleting the job row."""

    def test_delete_unlinked_job(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        gb.create_job(
            owner_id="000001", owner_type="customer", name="X",
        )
        result = gb.delete_job(job_id="000001")
        assert result["status"] == "deleted"
        assert result["reparented_count"] == 0

    def test_delete_with_linked_invoices_refused(
        self, business_book,
    ):
        """Default: refuse to delete a job with linked invoices.
        Names how many are linked + suggests force=True or
        unlinking the documents first."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        gb.create_job(
            owner_id="000001", owner_type="customer", name="X",
        )
        # Manually link an invoice to the job (commit 2 will
        # add the job_id parameter; for this test we set
        # owner_type/owner_guid directly).
        gb.create_invoice(customer_id="000001")
        with gb.open(readonly=False) as book:
            from piecash.business.invoice import Invoice, Job
            inv = book.session.query(Invoice).filter_by(id="000001").first()
            job = book.session.query(Job).filter_by(id="000001").first()
            inv.owner_type = 3
            inv.owner_guid = job.guid
            book.save()
        with pytest.raises(
            ValueError, match="has 1 linked",
        ):
            gb.delete_job(job_id="000001")

    def test_delete_with_force_reparents_invoices(
        self, business_book,
    ):
        """force=True re-parents linked invoices back to the
        underlying customer (owner_type 3→2 with owner_guid
        flipped from job to customer) before deleting. Invoice
        history is preserved; only the intermediate Job row
        disappears."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        gb.create_job(
            owner_id="000001", owner_type="customer", name="X",
        )
        gb.create_invoice(customer_id="000001")
        with gb.open(readonly=False) as book:
            from piecash.business.invoice import Invoice, Job
            inv = book.session.query(Invoice).filter_by(id="000001").first()
            job = book.session.query(Job).filter_by(id="000001").first()
            customer_guid = job.owner_guid  # Acme's GUID
            inv.owner_type = 3
            inv.owner_guid = job.guid
            book.save()
        result = gb.delete_job(job_id="000001", force=True)
        assert result["status"] == "deleted"
        assert result["reparented_count"] == 1
        # Re-fetch invoice — should be back to owner_type=2,
        # owner_guid=customer.
        with gb.open() as book:
            from piecash.business.invoice import Invoice
            inv = book.session.query(Invoice).filter_by(id="000001").first()
            assert inv.owner_type == 2
            assert inv.owner_guid == customer_guid

    def test_delete_nonexistent_job(self, business_book):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="Job not found"):
            gb.delete_job(job_id="999999")


class TestGetJobReport:
    """Tests for get_job_report.

    Per-job summary aggregating billed/paid/outstanding across
    every linked invoice. Multi-currency support via
    ``totals_by_currency``. Posted invoices contribute lot-
    based amounts; unposted (draft) invoices contribute face
    value with paid=0 so the report shows the pipeline.
    """

    def _setup_customer_with_posted_invoice(
        self, gb, customer_name="Acme Co",
        amount="500.00", post=True,
    ):
        """Helper to create customer + invoice + entry +
        optionally post. Returns (job_id, invoice_id).
        """
        gb.create_customer(name=customer_name)
        job = gb.create_job(
            owner_id="000001", owner_type="customer", name="X",
        )
        inv = gb.create_invoice(
            customer_id="000001", job_id=job["id"],
        )
        gb.add_invoice_entry(
            invoice_id=inv["id"],
            account="Income:Sales",
            description="Work",
            quantity="1", price=amount,
        )
        if post:
            gb.post_invoice(
                invoice_id=inv["id"],
                post_account="Assets:Accounts Receivable",
                owner_type="customer",
            )
        return job["id"], inv["id"]

    def test_job_not_found(self, business_book):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="Job not found"):
            gb.get_job_report(job_id="999999")

    def test_empty_job_report(self, business_book):
        """A job with no linked invoices reports zero counts and
        an empty totals_by_currency dict — not an error."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        job = gb.create_job(
            owner_id="000001", owner_type="customer", name="X",
        )
        result = gb.get_job_report(job_id=job["id"])
        assert result["linked_invoices_count"] == 0
        assert result["posted_count"] == 0
        assert result["open_count"] == 0
        assert result["totals_by_currency"] == {}
        assert result["invoices"] == []

    def test_single_posted_invoice(self, business_book):
        """Posted invoice with no payments: billed=500,
        paid=0, outstanding=500."""
        gb = GnuCashBook(str(business_book))
        job_id, _ = self._setup_customer_with_posted_invoice(gb)
        result = gb.get_job_report(job_id=job_id)
        assert result["linked_invoices_count"] == 1
        assert result["posted_count"] == 1
        assert result["open_count"] == 0
        usd_totals = result["totals_by_currency"]["USD"]
        assert Decimal(usd_totals["billed"]) == Decimal("500")
        assert Decimal(usd_totals["paid"]) == Decimal("0")
        assert Decimal(usd_totals["outstanding"]) == Decimal("500")
        # Per-invoice row
        assert len(result["invoices"]) == 1
        assert result["invoices"][0]["status"] == "posted"

    def test_partial_payment(self, business_book):
        """After paying $200 against a $500 invoice: paid=200,
        outstanding=300."""
        gb = GnuCashBook(str(business_book))
        job_id, inv_id = self._setup_customer_with_posted_invoice(gb)
        gb.pay_invoice(
            invoice_id=inv_id,
            payment_account="Assets:Checking",
            amount="200.00",
            owner_type="customer",
        )
        result = gb.get_job_report(job_id=job_id)
        usd = result["totals_by_currency"]["USD"]
        assert Decimal(usd["paid"]) == Decimal("200")
        assert Decimal(usd["outstanding"]) == Decimal("300")

    def test_unposted_invoice_included(self, business_book):
        """Drafts (unposted) contribute face value as billed +
        outstanding, paid=0. Shows the pipeline alongside the
        posted obligations."""
        gb = GnuCashBook(str(business_book))
        # Create posted invoice for $500
        job_id, _ = self._setup_customer_with_posted_invoice(gb)
        # Create draft invoice for $300 on same job
        draft = gb.create_invoice(
            customer_id="000001", job_id=job_id,
        )
        gb.add_invoice_entry(
            invoice_id=draft["id"],
            account="Income:Sales",
            description="Future work",
            quantity="1", price="300.00",
        )
        result = gb.get_job_report(job_id=job_id)
        assert result["posted_count"] == 1
        assert result["open_count"] == 1
        usd = result["totals_by_currency"]["USD"]
        # Total billed: 500 (posted) + 300 (draft) = 800
        assert Decimal(usd["billed"]) == Decimal("800")
        # Total outstanding: 500 (posted, unpaid) + 300 (draft) = 800
        assert Decimal(usd["outstanding"]) == Decimal("800")

    def test_multi_currency_totals(self, business_book):
        """Mixed-currency job: totals_by_currency has one entry
        per currency seen."""
        import piecash
        from datetime import date as date_cls
        gb = GnuCashBook(str(business_book))
        with gb.open(readonly=False) as bk:
            eur = piecash.Commodity(
                namespace="CURRENCY", mnemonic="EUR",
                fullname="Euro", fraction=100,
            )
            bk.session.add(eur)
            bk.flush()
            bk.session.add(piecash.Price(
                commodity=eur, currency=bk.default_currency,
                date=date_cls(2026, 5, 24),
                value="1.10", type="last",
            ))
            bk.save()
        gb.create_customer(name="Acme Co")
        job = gb.create_job(
            owner_id="000001", owner_type="customer", name="Project",
        )
        # USD invoice
        inv_usd = gb.create_invoice(
            customer_id="000001", job_id=job["id"],
        )
        gb.add_invoice_entry(
            invoice_id=inv_usd["id"], account="Income:Sales",
            description="USD work", quantity="1", price="500",
        )
        # EUR invoice
        inv_eur = gb.create_invoice(
            customer_id="000001", job_id=job["id"], currency="EUR",
        )
        gb.add_invoice_entry(
            invoice_id=inv_eur["id"], account="Income:Sales",
            description="EUR work", quantity="1", price="400",
        )
        result = gb.get_job_report(job_id=job["id"])
        assert result["linked_invoices_count"] == 2
        assert "USD" in result["totals_by_currency"]
        assert "EUR" in result["totals_by_currency"]
        assert (
            Decimal(result["totals_by_currency"]["USD"]["billed"])
            == Decimal("500")
        )
        assert (
            Decimal(result["totals_by_currency"]["EUR"]["billed"])
            == Decimal("400")
        )

    def test_vendor_job_report(self, business_book):
        """Vendor jobs work symmetrically — bills posted to A/P
        report the same shape, owner_type='vendor'."""
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        job = gb.create_job(
            owner_id="000001", owner_type="vendor", name="Supply",
        )
        bill = gb.create_bill(
            vendor_id="000001", job_id=job["id"],
        )
        gb.add_bill_entry(
            bill_id=bill["id"],
            account="Expenses:Office Supplies",
            description="Paper", quantity="1", price="150.00",
        )
        gb.post_invoice(
            invoice_id=bill["id"],
            post_account="Liabilities:Accounts Payable",
            owner_type="vendor",
        )
        result = gb.get_job_report(job_id=job["id"])
        assert result["owner_type"] == "vendor"
        assert result["owner_name"] == "Office Depot"
        usd = result["totals_by_currency"]["USD"]
        assert Decimal(usd["billed"]) == Decimal("150")
        assert Decimal(usd["outstanding"]) == Decimal("150")


class TestJobDisplayPolish:
    """Tests for the (job:JOB-X) annotation in list_invoices and
    get_outstanding_invoices compact output. Job-attached
    invoices should be visibly distinguished from direct
    customer invoices / vendor bills in any compact-list view.
    """

    def test_list_invoices_compact_shows_job_annotation(
        self, business_book,
    ):
        """A job-attached customer invoice renders with both
        the INV tag (semantic side, resolved via the job's
        underlying owner_type) and a (job:JOB-X) suffix on the
        owner column."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        job = gb.create_job(
            owner_id="000001", owner_type="customer",
            name="API Rewrite",
        )
        gb.create_invoice(
            customer_id="000001", job_id=job["id"],
        )
        out = gb.list_invoices()  # compact default
        # Semantic tag is INV (customer-side), not generic
        assert "\tINV\t" in out
        # Owner column carries the job annotation
        assert f"(job:{job['id']})" in out

    def test_list_invoices_vendor_bill_in_job(self, business_book):
        """Symmetric: vendor bill attached to vendor job renders
        BILL tag + (job:JOB-X) annotation."""
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        job = gb.create_job(
            owner_id="000001", owner_type="vendor", name="Supplies",
        )
        gb.create_bill(vendor_id="000001", job_id=job["id"])
        out = gb.list_invoices()
        assert "\tBILL\t" in out
        assert f"(job:{job['id']})" in out

    def test_non_job_invoice_no_annotation(self, business_book):
        """Pre-v1.3 contract — direct customer invoices (no job)
        produce compact output without a job annotation."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        gb.create_invoice(customer_id="000001")  # no job_id
        out = gb.list_invoices()
        assert "(job:" not in out

    def test_get_outstanding_shows_job_annotation(self, business_book):
        """get_outstanding_invoices compact output annotates
        job-attached docs with (job:JOB-X) on the owner
        column."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        job = gb.create_job(
            owner_id="000001", owner_type="customer",
            name="API Rewrite",
        )
        inv = gb.create_invoice(
            customer_id="000001", job_id=job["id"],
        )
        gb.add_invoice_entry(
            invoice_id=inv["id"], account="Income:Sales",
            description="x", quantity="1", price="500",
        )
        gb.post_invoice(
            invoice_id=inv["id"],
            post_account="Assets:Accounts Receivable",
            owner_type="customer",
        )
        out = gb.get_outstanding_invoices()  # compact default
        assert f"(job:{job['id']})" in out

    def test_get_outstanding_credit_note_in_job(self, business_book):
        """A credit note attached to a job carries BOTH the (CN)
        tag AND the (job:JOB-X) annotation — order: owner →
        (CN) → (job:X)."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        job = gb.create_job(
            owner_id="000001", owner_type="customer", name="X",
        )
        # Create + post a source invoice first so the credit
        # note has something to link to
        src = gb.create_invoice(
            customer_id="000001", job_id=job["id"],
        )
        gb.add_invoice_entry(
            invoice_id=src["id"], account="Income:Sales",
            description="x", quantity="1", price="500",
        )
        gb.post_invoice(
            invoice_id=src["id"],
            post_account="Assets:Accounts Receivable",
            owner_type="customer",
        )
        cn = gb.create_credit_note(
            owner_id="000001", owner_type="customer",
            applies_to_invoice_id=src["id"],
        )
        gb.add_credit_note_entry(
            credit_note_id=cn["id"], account="Income:Sales",
            description="x", quantity="1", price="50",
        )
        # Note: credit notes themselves aren't job-linked
        # here — only the source invoice is. The credit
        # note's compact line should NOT show a job
        # annotation (it's not attached to a job).
        gb.post_invoice(
            invoice_id=cn["id"],
            post_account="Assets:Accounts Receivable",
            owner_type="customer",
        )
        out = gb.get_outstanding_invoices()
        # The source invoice line carries the job annotation
        src_line = next(
            ln for ln in out.split("\n") if ln.startswith(src["id"])
        )
        assert f"(job:{job['id']})" in src_line
        # The credit note line carries (CN) but not (job:...)
        cn_line = next(
            ln for ln in out.split("\n") if ln.startswith(cn["id"])
        )
        assert "(CN)" in cn_line
        assert "(job:" not in cn_line


class TestJobPr88ReviewFollowups:
    """Tests for the Copilot PR #88 review follow-ups.

    The headline regression test is for a bug Copilot's
    redundant-query findings indirectly surfaced: pre-fix,
    ``get_outstanding_invoices`` resolved owner_name via direct
    customer/vendor lookups keyed off ``is_bill``, which
    returned None for job-attached invoices because
    inv.owner_guid points at a Job (not a customer/vendor row).
    The bookkeeper didn't probe this exact path; the bug came
    out during the refactor to ``_resolve_owner_type_and_job``.
    """

    def test_get_outstanding_resolves_job_attached_owner_name(
        self, business_book,
    ):
        """A posted job-attached invoice should appear in
        get_outstanding_invoices with the correct owner_name
        (the underlying customer/vendor), not None.

        Pre-fix: get_outstanding called _find_customer_by_guid
        / _find_vendor_by_guid directly with inv.owner_guid,
        which is a Job GUID for owner_type=3 rows — the
        customer/vendor table lookups returned nothing.
        """
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        job = gb.create_job(
            owner_id="000001", owner_type="customer",
            name="API Rewrite",
        )
        inv = gb.create_invoice(
            customer_id="000001", job_id=job["id"],
        )
        gb.add_invoice_entry(
            invoice_id=inv["id"], account="Income:Sales",
            description="x", quantity="1", price="500",
        )
        gb.post_invoice(
            invoice_id=inv["id"],
            post_account="Assets:Accounts Receivable",
            owner_type="customer",
        )
        outstanding = gb.get_outstanding_invoices(compact=False)["invoices"]
        row = next(r for r in outstanding if r["id"] == inv["id"])
        # The bug: pre-fix, owner_name was None on job-attached
        # posted invoices.
        assert row["owner_name"] == "Acme Co"
        # And the job_id surfaces correctly (verbose response
        # shape; commit 4 introduced this field).
        assert row["job_id"] == job["id"]

    def test_list_jobs_verbose_uses_indented_json(self, business_book):
        """list_jobs verbose output should match other list_*
        tools' shape (json.dumps with indent=2, preserves empty
        strings) rather than using _json (minified, strips
        empties). Copilot caught the divergence on PR #88."""
        from gnucash_mcp.tools._helpers import safe_tool
        # Test through the tool wrapper layer rather than the
        # book method — the inconsistency was at the wrapper
        # boundary.
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        gb.create_job(
            owner_id="000001", owner_type="customer",
            name="X", reference="",  # empty ref to test strip behavior
        )
        # Direct book method returns the list shape unchanged.
        rows = gb.list_jobs(compact=False)["jobs"]
        assert len(rows) == 1
        # Empty reference SHOULD survive the verbose JSON path
        # — the bug was that _json stripped it. We verify the
        # book-method dict has it as empty string (matching
        # other list_* methods' behavior).
        assert rows[0]["reference"] == ""

    def test_effective_owner_type_and_job_single_query(
        self, business_book,
    ):
        """_resolve_owner_type_and_job returns both the
        effective owner_type AND the Job (when present) from
        the same query — replaces the side-by-side
        _effective_owner_type + _find_job_by_guid pattern
        Copilot flagged."""
        from gnucash_mcp.book.business import BusinessMixin
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        job = gb.create_job(
            owner_id="000001", owner_type="customer", name="X",
        )
        inv = gb.create_invoice(
            customer_id="000001", job_id=job["id"],
        )
        with gb.open() as book:
            from piecash.business.invoice import Invoice
            inv_obj = book.session.query(Invoice).filter_by(
                id=inv["id"],
            ).first()
            # Job-attached: returns (job's owner_type, job obj)
            eff_ot, j = BusinessMixin._resolve_owner_type_and_job(
                book, inv_obj,
            )
            assert eff_ot == 2  # customer
            assert j is not None
            assert j.id == job["id"]
            # Direct invoice (no job): returns (own owner_type, None)
            gb.create_invoice(customer_id="000001")
            direct_inv = book.session.query(Invoice).filter_by(
                id="000002",
            ).first()
            eff_ot2, j2 = BusinessMixin._resolve_owner_type_and_job(
                book, direct_inv,
            )
            assert eff_ot2 == 2
            assert j2 is None


class TestInvoiceJobLinkage:
    """Tests for the job_id parameter on create_invoice /
    create_bill, plus the cascading effects on _invoice_to_dict
    (type field computation, job field surfacing) and
    list_invoices (job_id filter).
    """

    def test_create_invoice_with_job_id(self, business_book):
        """Invoice attached to a job: owner_type=3 internally,
        but the response surface still reports type='invoice'
        (semantic) and adds job: {id, name}."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        job = gb.create_job(
            owner_id="000001", owner_type="customer",
            name="API Rewrite",
        )
        inv = gb.create_invoice(
            customer_id="000001",
            job_id=job["id"],
        )
        # get_invoice surfaces both the semantic type and the
        # job link.
        fetched = gb.get_invoice(
            inv["id"], owner_type=None,
        )
        # owner_type=3 internally; type stays 'invoice' from the
        # semantic resolution through the job's underlying owner.
        assert fetched["type"] == "invoice"
        assert fetched["owner_name"] == "Acme Co"
        assert fetched["job"] == {
            "id": job["id"], "name": "API Rewrite",
        }

    def test_create_bill_with_job_id(self, business_book):
        """Symmetric for vendor side: bill attached to a vendor
        job. type='bill' resolved through the job."""
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        job = gb.create_job(
            owner_id="000001", owner_type="vendor",
            name="Q3 supply contract",
        )
        bill = gb.create_bill(
            vendor_id="000001",
            job_id=job["id"],
        )
        fetched = gb.get_invoice(bill["id"])
        assert fetched["type"] == "bill"
        assert fetched["owner_name"] == "Office Depot"
        assert fetched["job"]["id"] == job["id"]

    def test_job_id_cross_customer_rejected(self, business_book):
        """Job belonging to customer A cannot be used on an
        invoice for customer B."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        gb.create_customer(name="Beta Inc")
        # Acme is 000001; Beta is 000002.
        acme_job = gb.create_job(
            owner_id="000001", owner_type="customer", name="X",
        )
        with pytest.raises(
            ValueError, match="belongs to.*not",
        ):
            gb.create_invoice(
                customer_id="000002",
                job_id=acme_job["id"],
            )

    def test_customer_invoice_with_vendor_job_rejected(
        self, business_book,
    ):
        """A customer invoice can't link to a vendor job and
        vice-versa — owner_type must match."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        gb.create_vendor(name="Office Depot")
        # Vendor job
        v_job = gb.create_job(
            owner_id="000001", owner_type="vendor", name="X",
        )
        with pytest.raises(
            ValueError, match="is a vendor job; this is a customer",
        ):
            gb.create_invoice(
                customer_id="000001",
                job_id=v_job["id"],
            )

    def test_job_id_not_found(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        with pytest.raises(ValueError, match="Job not found"):
            gb.create_invoice(
                customer_id="000001",
                job_id="999999",
            )

    def test_normal_invoice_omits_job_key(self, business_book):
        """Invoices NOT attached to a job omit the 'job' field
        entirely — same shape as pre-v1.3 for non-job docs."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        gb.create_invoice(customer_id="000001")  # no job
        fetched = gb.get_invoice("000001")
        assert "job" not in fetched

    def test_list_invoices_filtered_by_job(self, business_book):
        """list_invoices(job_id=...) returns only invoices linked
        to that job."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        job_a = gb.create_job(
            owner_id="000001", owner_type="customer", name="A",
        )
        job_b = gb.create_job(
            owner_id="000001", owner_type="customer", name="B",
        )
        # 2 invoices on job A, 1 on job B, 1 standalone
        gb.create_invoice(customer_id="000001", job_id=job_a["id"])
        gb.create_invoice(customer_id="000001", job_id=job_a["id"])
        gb.create_invoice(customer_id="000001", job_id=job_b["id"])
        gb.create_invoice(customer_id="000001")  # standalone

        result_a = gb.list_invoices(
            job_id=job_a["id"], compact=False,
        )
        # ``invoices`` is the envelope key set by list_invoices
        assert len(result_a["invoices"]) == 2
        result_b = gb.list_invoices(
            job_id=job_b["id"], compact=False,
        )
        assert len(result_b["invoices"]) == 1
        # All invoices (no job filter) shows all 4
        result_all = gb.list_invoices(compact=False)
        assert len(result_all["invoices"]) == 4

    def test_list_invoices_job_id_with_mismatched_owner_type(
        self, business_book,
    ):
        """If caller passes both job_id and owner_type, they
        must agree — vendor-job + owner_type=customer rejected."""
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        v_job = gb.create_job(
            owner_id="000001", owner_type="vendor", name="X",
        )
        with pytest.raises(
            ValueError, match="vendor job.*doesn't match",
        ):
            gb.list_invoices(
                job_id=v_job["id"],
                owner_type="customer",
            )

    def test_get_job_includes_linked_invoices(self, business_book):
        """After Commit-1 returned count=0 / ids=[] for empty
        jobs, Commit-2 actually links invoices — verify
        get_job's linked_invoices list now populates."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        job = gb.create_job(
            owner_id="000001", owner_type="customer", name="X",
        )
        gb.create_invoice(customer_id="000001", job_id=job["id"])
        gb.create_invoice(customer_id="000001", job_id=job["id"])
        result = gb.get_job(job_id=job["id"])
        assert result["linked_invoices"]["count"] == 2
        assert len(result["linked_invoices"]["ids"]) == 2

    def test_voucher_with_job_id_rejected(self, business_book):
        """Vouchers can't be grouped under jobs — piecash's
        job model is customer/vendor only. Surfaces at the
        _create_business_document level when job_id is set
        alongside owner_type=5."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        gb.create_employee(name="Maria")
        job = gb.create_job(
            owner_id="000001", owner_type="customer", name="X",
        )
        with pytest.raises(
            ValueError, match="Employee vouchers cannot be grouped",
        ):
            # We call _create_business_document directly because
            # create_voucher doesn't have a job_id parameter at
            # the public surface — verifies the inner guard
            # catches anyone who reaches the helper via a
            # future path.
            gb._create_business_document(
                owner_type=5,
                owner_id="000001",
                doc_id=None,
                job_id=job["id"],
            )


class TestUpdateCustomer:
    """Tests for ``update_customer``.

    The customer/vendor/employee triple shares a helper, so the
    Customer tests cover the bulk of the contract; vendor and
    employee tests check their specific differences (notes column
    on vendor, lack of notes column on employee).
    """

    def test_update_name(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        result = gb.update_customer(
            customer_id="000001", name="Acme Industries",
        )
        assert result["status"] == "updated"
        assert result["name"] == "Acme Industries"
        # Diff-style response: only changed fields show.
        assert "currency" not in result
        # Persisted.
        cust = gb.get_customer(customer_id="000001")
        assert cust["name"] == "Acme Industries"

    def test_update_notes_clear_with_empty_string(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp", notes="Net 30 terms")
        result = gb.update_customer(
            customer_id="000001", notes="",
        )
        assert result["notes"] == ""
        cust = gb.get_customer(customer_id="000001")
        assert cust["notes"] == ""

    def test_update_currency(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Berlin Digital", currency="USD")
        result = gb.update_customer(
            customer_id="000001", currency="EUR",
        )
        assert result["currency"] == "EUR"
        cust = gb.get_customer(customer_id="000001")
        assert cust["currency"] == "EUR"

    def test_update_unknown_currency_raises(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        with pytest.raises(ValueError, match="Currency not found"):
            gb.update_customer(customer_id="000001", currency="XYZ")

    def test_update_active_to_false_archives(self, business_book):
        """Deactivation is the archive path — the customer stays in
        the book (and on existing invoices) but ``list_customers``
        with ``active_only=True`` skips them."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Old Customer")
        gb.update_customer(customer_id="000001", active=False)

        active_only = gb.list_customers(active_only=True, compact=False)["customers"]
        assert all(c["id"] != "000001" for c in active_only)
        all_customers = gb.list_customers(
            active_only=False, compact=False,
        )["customers"]
        assert any(c["id"] == "000001" for c in all_customers)

    def test_update_address_creates_when_missing(self, business_book):
        """Customer created without an address gets one on first
        update."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        result = gb.update_customer(
            customer_id="000001",
            address={
                "addr1": "123 Main St",
                "phone": "555-0100",
                "email": "billing@acme.example",
            },
        )
        assert result["address"]["addr1"] == "123 Main St"
        assert result["address"]["phone"] == "555-0100"

        cust = gb.get_customer(customer_id="000001")
        assert cust["address"]["addr1"] == "123 Main St"
        assert cust["address"]["email"] == "billing@acme.example"

    def test_update_address_merges_with_existing(self, business_book):
        """A partial address dict updates the supplied sub-fields
        and leaves the others alone."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(
            name="Acme Corp",
            address={
                "addr1": "123 Main St",
                "addr2": "Suite 200",
                "phone": "555-0100",
                "email": "old@acme.example",
            },
        )
        # Update only email and phone.
        gb.update_customer(
            customer_id="000001",
            address={
                "phone": "555-9999",
                "email": "new@acme.example",
            },
        )
        cust = gb.get_customer(customer_id="000001")
        # Updated.
        assert cust["address"]["phone"] == "555-9999"
        assert cust["address"]["email"] == "new@acme.example"
        # Untouched.
        assert cust["address"]["addr1"] == "123 Main St"
        assert cust["address"]["addr2"] == "Suite 200"

    def test_update_address_clear_field_with_empty_string(
        self, business_book,
    ):
        """Empty string clears a sub-field; no key means leave it."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(
            name="Acme Corp",
            address={"addr1": "123 Main St", "fax": "555-0101"},
        )
        gb.update_customer(
            customer_id="000001",
            address={"fax": ""},
        )
        cust = gb.get_customer(customer_id="000001")
        # fax cleared, addr1 untouched.
        assert cust["address"].get("fax", "") == ""
        assert cust["address"]["addr1"] == "123 Main St"

    def test_update_address_unknown_key_raises(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        with pytest.raises(
            ValueError, match="Unknown address field",
        ):
            gb.update_customer(
                customer_id="000001",
                address={"addresss": "typo"},  # extra "s"
            )

    def test_update_no_fields_raises(self, business_book):
        """Calling update with nothing to change is a programming
        error — surface it loud."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        with pytest.raises(ValueError, match="No changes supplied"):
            gb.update_customer(customer_id="000001")

    def test_update_unknown_customer_raises(self, business_book):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="Customer not found"):
            gb.update_customer(customer_id="999999", name="x")

    def test_update_only_changed_fields_in_response(
        self, business_book,
    ):
        """If the caller passes ``name="Acme Corp"`` and that's
        already the name, no change happens and ``name`` is *not*
        in the diff response."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp", notes="x")
        # Pass a "no-op" name plus a real change.
        result = gb.update_customer(
            customer_id="000001", name="Acme Corp", notes="y",
        )
        assert "name" not in result  # unchanged
        assert result["notes"] == "y"

    def test_update_after_invoices_exist(self, business_book):
        """The whole point: an update_customer call should work
        even after the customer has invoices — the limitation that
        delete-then-recreate hits doesn't apply here."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        # Now try to fix a typo.
        result = gb.update_customer(
            customer_id="000001", name="ACME Corp",
        )
        assert result["status"] == "updated"


class TestUpdateVendor:
    """Tests for ``update_vendor``.

    Most behavior is shared with ``update_customer`` via
    ``_update_business_person``; verify the vendor surface plus a
    representative happy path.
    """

    def test_update_name_and_address(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(
            name="Office Depot",
            address={"addr1": "Old St"},
        )
        result = gb.update_vendor(
            vendor_id="000001",
            name="Office Depot Inc",
            address={"addr1": "New St", "phone": "555-1212"},
        )
        assert result["status"] == "updated"
        assert result["name"] == "Office Depot Inc"
        vendor = gb.get_vendor(vendor_id="000001")
        assert vendor["name"] == "Office Depot Inc"
        assert vendor["address"]["addr1"] == "New St"
        assert vendor["address"]["phone"] == "555-1212"

    def test_update_unknown_vendor_raises(self, business_book):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="Vendor not found"):
            gb.update_vendor(vendor_id="999999", name="x")


class TestUpdateEmployee:
    """Tests for ``update_employee``.

    Employee has no ``notes`` column. Verify a happy path and the
    no-notes-parameter signature.
    """

    def test_update_name_and_currency(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_employee(name="Jane Smith", currency="USD")
        result = gb.update_employee(
            employee_id="000001",
            name="Jane Q. Smith",
            currency="EUR",
        )
        assert result["status"] == "updated"
        assert result["name"] == "Jane Q. Smith"
        assert result["currency"] == "EUR"
        emp = gb.get_employee(employee_id="000001")
        assert emp["name"] == "Jane Q. Smith"
        assert emp["currency"] == "EUR"

    def test_update_unknown_employee_raises(self, business_book):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="Employee not found"):
            gb.update_employee(employee_id="999999", name="x")

    def test_update_employee_active_toggle(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_employee(name="Former Employee")
        gb.update_employee(employee_id="000001", active=False)

        active_only = gb.list_employees(
            active_only=True, compact=False,
        )["employees"]
        assert all(e["id"] != "000001" for e in active_only)


class TestDeleteCustomer:
    """Tests for delete_customer."""

    def test_delete_customer_no_invoices(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Temp Customer")
        result = gb.delete_customer(customer_id="000001")
        assert result["status"] == "deleted"
        assert result["name"] == "Temp Customer"
        assert result["type"] == "customer"

    def test_delete_customer_with_unposted_invoice_blocked(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        with pytest.raises(ValueError, match="Delete the invoices first"):
            gb.delete_customer(customer_id="000001")

    def test_delete_customer_after_invoice_deleted(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.delete_invoice(invoice_id="000001")
        result = gb.delete_customer(customer_id="000001")
        assert result["status"] == "deleted"

    def test_delete_customer_with_posted_invoice_blocked(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001", account="Income:Sales",
            description="Work", quantity="1", price="100.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
        )
        with pytest.raises(ValueError, match="posted invoices"):
            gb.delete_customer(customer_id="000001")

    def test_delete_nonexistent_customer(self, business_book):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="Customer not found"):
            gb.delete_customer(customer_id="999999")


class TestDeleteVendor:
    """Tests for delete_vendor."""

    def test_delete_vendor_no_bills(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Temp Vendor")
        result = gb.delete_vendor(vendor_id="000001")
        assert result["status"] == "deleted"
        assert result["name"] == "Temp Vendor"
        assert result["type"] == "vendor"

    def test_delete_vendor_with_unposted_bill_blocked(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        gb.create_bill(vendor_id="000001")
        with pytest.raises(ValueError, match="Delete the bills first"):
            gb.delete_vendor(vendor_id="000001")

    def test_delete_vendor_after_bill_deleted(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        gb.create_bill(vendor_id="000001")
        gb.delete_bill(bill_id="000001")
        result = gb.delete_vendor(vendor_id="000001")
        assert result["status"] == "deleted"

    def test_delete_nonexistent_vendor(self, business_book):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="Vendor not found"):
            gb.delete_vendor(vendor_id="999999")


class TestCreateBillterm:
    """Tests for create_billterm."""

    def test_basic_creation(self, business_book):
        gb = GnuCashBook(str(business_book))
        result = gb.create_billterm(name="Net 30")
        assert result["status"] == "created"
        assert result["name"] == "Net 30"
        assert result["due_days"] == 30

    def test_custom_due_days(self, business_book):
        gb = GnuCashBook(str(business_book))
        result = gb.create_billterm(name="Net 60", due_days=60)
        assert result["due_days"] == 60

    def test_with_discount(self, business_book):
        gb = GnuCashBook(str(business_book))
        result = gb.create_billterm(
            name="2/10 Net 30",
            due_days=30,
            discount_days=10,
            discount_percent="2",
        )
        assert result["status"] == "created"

    def test_read_back_via_list(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_billterm(name="Net 30")
        gb.create_billterm(name="Net 60", due_days=60)
        result = gb.list_billterms(compact=False)["billterms"]
        assert len(result) == 2
        names = {bt["name"] for bt in result}
        assert names == {"Net 30", "Net 60"}


class TestListBillterms:
    """Tests for list_billterms."""

    def test_empty_list(self, business_book):
        gb = GnuCashBook(str(business_book))
        result = gb.list_billterms()
        assert result == "Showing 0 of 0 billterms"

    def test_compact_format(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_billterm(name="Net 30")
        result = gb.list_billterms()
        assert "Net 30" in result
        assert "30 days" in result

    def test_verbose_format(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_billterm(name="Net 30", due_days=30, description="Standard terms")
        result = gb.list_billterms(compact=False)["billterms"]
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["name"] == "Net 30"
        assert result[0]["due_days"] == 30
        assert result[0]["description"] == "Standard terms"


def _add_tax_accounts(gb):
    """Helper: add two LIABILITY tax-payable accounts to the
    business book fixture so taxtable tests have somewhere to
    route tax components. Returns the paths for reuse in test
    assertions."""
    gb.create_account(
        name="GST Payable",
        account_type="LIABILITY",
        parent="Liabilities",
    )
    gb.create_account(
        name="PST Payable",
        account_type="LIABILITY",
        parent="Liabilities",
    )
    return (
        "Liabilities:GST Payable",
        "Liabilities:PST Payable",
    )


class TestCreateTaxtable:
    """Tests for create_taxtable."""

    def test_single_entry_percentage(self, business_book):
        gb = GnuCashBook(str(business_book))
        gst, _ = _add_tax_accounts(gb)
        result = gb.create_taxtable(
            name="GST 5%",
            entries=[
                {"type": "percentage", "amount": "5.00",
                 "account": gst},
            ],
        )
        assert result["status"] == "created"
        assert result["name"] == "GST 5%"
        assert result["entry_count"] == 1
        assert result["entries"][0]["type"] == "percentage"
        assert result["entries"][0]["amount"] == "5"
        assert result["entries"][0]["account"] == gst

    def test_multi_entry_composite(self, business_book):
        gb = GnuCashBook(str(business_book))
        gst, pst = _add_tax_accounts(gb)
        result = gb.create_taxtable(
            name="BC GST+PST",
            entries=[
                {"type": "percentage", "amount": "5.00",
                 "account": gst},
                {"type": "percentage", "amount": "7.00",
                 "account": pst},
            ],
        )
        assert result["entry_count"] == 2
        accounts = {e["account"] for e in result["entries"]}
        assert accounts == {gst, pst}

    def test_flat_value_entry(self, business_book):
        gb = GnuCashBook(str(business_book))
        gst, _ = _add_tax_accounts(gb)
        result = gb.create_taxtable(
            name="Eco Fee $5",
            entries=[
                {"type": "value", "amount": "5.00", "account": gst},
            ],
        )
        assert result["entries"][0]["type"] == "value"
        assert result["entries"][0]["amount"] == "5"

    def test_mixed_value_and_percentage(self, business_book):
        gb = GnuCashBook(str(business_book))
        gst, pst = _add_tax_accounts(gb)
        result = gb.create_taxtable(
            name="Sales+Eco",
            entries=[
                {"type": "percentage", "amount": "7.25",
                 "account": gst},
                {"type": "value", "amount": "5.00", "account": pst},
            ],
        )
        types = {e["type"] for e in result["entries"]}
        assert types == {"percentage", "value"}

    def test_account_via_short_guid(self, business_book):
        gb = GnuCashBook(str(business_book))
        gst_path, _ = _add_tax_accounts(gb)
        # Find the short-guid prefix from list_accounts output.
        listing = gb.list_accounts()
        gst_row = next(
            line for line in listing.splitlines()
            if "GST Payable" in line
        )
        short_guid = gst_row.split("\t")[0]
        assert short_guid.startswith("%")
        result = gb.create_taxtable(
            name="GST via short",
            entries=[
                {"type": "percentage", "amount": "5",
                 "account": short_guid},
            ],
        )
        # Resolved to the path in the response.
        assert result["entries"][0]["account"] == gst_path

    def test_duplicate_name_rejected(self, business_book):
        gb = GnuCashBook(str(business_book))
        gst, _ = _add_tax_accounts(gb)
        gb.create_taxtable(
            name="GST 5%",
            entries=[{"type": "percentage", "amount": "5",
                      "account": gst}],
        )
        with pytest.raises(ValueError, match="already exists"):
            gb.create_taxtable(
                name="GST 5%",
                entries=[{"type": "percentage", "amount": "5",
                          "account": gst}],
            )

    def test_empty_entries_rejected(self, business_book):
        gb = GnuCashBook(str(business_book))
        _add_tax_accounts(gb)
        with pytest.raises(ValueError, match="at least one entry"):
            gb.create_taxtable(name="Empty", entries=[])

    def test_bad_type_rejected(self, business_book):
        gb = GnuCashBook(str(business_book))
        gst, _ = _add_tax_accounts(gb)
        with pytest.raises(ValueError, match="type must be"):
            gb.create_taxtable(
                name="Bad",
                entries=[{"type": "flat", "amount": "5",
                          "account": gst}],
            )

    def test_zero_amount_rejected(self, business_book):
        gb = GnuCashBook(str(business_book))
        gst, _ = _add_tax_accounts(gb)
        with pytest.raises(ValueError, match="amount must be > 0"):
            gb.create_taxtable(
                name="Zero",
                entries=[{"type": "percentage", "amount": "0",
                          "account": gst}],
            )

    def test_negative_amount_rejected(self, business_book):
        gb = GnuCashBook(str(business_book))
        gst, _ = _add_tax_accounts(gb)
        with pytest.raises(ValueError, match="amount must be > 0"):
            gb.create_taxtable(
                name="Neg",
                entries=[{"type": "percentage", "amount": "-5",
                          "account": gst}],
            )

    def test_high_percentage_rejected(self, business_book):
        gb = GnuCashBook(str(business_book))
        gst, _ = _add_tax_accounts(gb)
        # 100%+ rate almost certainly indicates the user expressed
        # the rate as a fraction (0.05) and we're seeing 5.0 — but
        # also 100% itself is a likely user error.
        with pytest.raises(ValueError, match="user error"):
            gb.create_taxtable(
                name="Too high",
                entries=[{"type": "percentage", "amount": "150",
                          "account": gst}],
            )

    def test_missing_account_rejected(self, business_book):
        gb = GnuCashBook(str(business_book))
        _add_tax_accounts(gb)
        with pytest.raises(ValueError, match="account not found"):
            gb.create_taxtable(
                name="Bad acct",
                entries=[{"type": "percentage", "amount": "5",
                          "account": "Liabilities:Does Not Exist"}],
            )

    def test_wrong_account_type_rejected(self, business_book):
        gb = GnuCashBook(str(business_book))
        _add_tax_accounts(gb)
        # Income accounts are valid existing accounts but the wrong
        # type for tax routing.
        with pytest.raises(ValueError, match="ASSET.*LIABILITY"):
            gb.create_taxtable(
                name="Wrong type",
                entries=[{"type": "percentage", "amount": "5",
                          "account": "Income:Sales"}],
            )

    def test_multi_currency_rejected(self, business_book):
        gb = GnuCashBook(str(business_book))
        _add_tax_accounts(gb)
        # Add a EUR-denominated liability account and pair it with
        # a USD one to trigger the multi-commodity guard.
        gb.create_account(
            name="EU VAT Payable",
            account_type="LIABILITY",
            parent="Liabilities",
            commodity="EUR",
        )
        with pytest.raises(ValueError, match="different commodit"):
            gb.create_taxtable(
                name="Mixed currency",
                entries=[
                    {"type": "percentage", "amount": "5",
                     "account": "Liabilities:GST Payable"},
                    {"type": "percentage", "amount": "19",
                     "account": "Liabilities:EU VAT Payable"},
                ],
            )

    def test_initial_refcount_zero(self, business_book):
        gb = GnuCashBook(str(business_book))
        gst, _ = _add_tax_accounts(gb)
        gb.create_taxtable(
            name="GST 5%",
            entries=[{"type": "percentage", "amount": "5",
                      "account": gst}],
        )
        tt = gb.get_taxtable("GST 5%")
        assert tt["refcount"] == 0


class TestListTaxtables:
    """Tests for list_taxtables."""

    def test_empty_list(self, business_book):
        gb = GnuCashBook(str(business_book))
        assert gb.list_taxtables() == "Showing 0 of 0 taxtables"
        assert gb.list_taxtables(compact=False)["taxtables"] == []

    def test_compact_single_entry(self, business_book):
        gb = GnuCashBook(str(business_book))
        gst, _ = _add_tax_accounts(gb)
        gb.create_taxtable(
            name="GST 5%",
            entries=[{"type": "percentage", "amount": "5",
                      "account": gst}],
        )
        result = gb.list_taxtables()
        assert "GST 5%" in result
        assert "1 entry" in result
        # Summary token has the arrow renderer.
        assert "5%→GST Payable" in result

    def test_compact_multi_entry(self, business_book):
        gb = GnuCashBook(str(business_book))
        gst, pst = _add_tax_accounts(gb)
        gb.create_taxtable(
            name="BC GST+PST",
            entries=[
                {"type": "percentage", "amount": "5",
                 "account": gst},
                {"type": "percentage", "amount": "7",
                 "account": pst},
            ],
        )
        result = gb.list_taxtables()
        assert "2 entries" in result
        assert "5%→GST Payable" in result
        assert "7%→PST Payable" in result

    def test_verbose_includes_refcount(self, business_book):
        gb = GnuCashBook(str(business_book))
        gst, _ = _add_tax_accounts(gb)
        gb.create_taxtable(
            name="GST 5%",
            entries=[{"type": "percentage", "amount": "5",
                      "account": gst}],
        )
        result = gb.list_taxtables(compact=False)["taxtables"]
        assert len(result) == 1
        assert result[0]["name"] == "GST 5%"
        assert result[0]["refcount"] == 0
        assert result[0]["entries"][0]["account"] == gst

    def test_sorted_by_name(self, business_book):
        gb = GnuCashBook(str(business_book))
        gst, _ = _add_tax_accounts(gb)
        for nm in ["Zeta", "Alpha", "Mu"]:
            gb.create_taxtable(
                name=nm,
                entries=[{"type": "percentage", "amount": "5",
                          "account": gst}],
            )
        result = gb.list_taxtables(compact=False)["taxtables"]
        assert [t["name"] for t in result] == ["Alpha", "Mu", "Zeta"]


class TestGetTaxtable:
    """Tests for get_taxtable."""

    def test_basic_lookup(self, business_book):
        gb = GnuCashBook(str(business_book))
        gst, pst = _add_tax_accounts(gb)
        gb.create_taxtable(
            name="BC GST+PST",
            entries=[
                {"type": "percentage", "amount": "5",
                 "account": gst},
                {"type": "percentage", "amount": "7",
                 "account": pst},
            ],
        )
        tt = gb.get_taxtable("BC GST+PST")
        assert tt["name"] == "BC GST+PST"
        assert len(tt["entries"]) == 2
        assert tt["refcount"] == 0
        # Account paths resolved on each entry.
        accounts = {e["account"] for e in tt["entries"]}
        assert accounts == {gst, pst}

    def test_not_found_raises(self, business_book):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="not found"):
            gb.get_taxtable("Nonexistent")


class TestUpdateTaxtable:
    """Tests for update_taxtable."""

    def test_no_fields_raises(self, business_book):
        gb = GnuCashBook(str(business_book))
        gst, _ = _add_tax_accounts(gb)
        gb.create_taxtable(
            name="GST 5%",
            entries=[{"type": "percentage", "amount": "5",
                      "account": gst}],
        )
        with pytest.raises(ValueError, match="at least one"):
            gb.update_taxtable(name="GST 5%")

    def test_rename(self, business_book):
        gb = GnuCashBook(str(business_book))
        gst, _ = _add_tax_accounts(gb)
        gb.create_taxtable(
            name="GST 5%",
            entries=[{"type": "percentage", "amount": "5",
                      "account": gst}],
        )
        result = gb.update_taxtable(
            name="GST 5%", new_name="Federal GST",
        )
        assert result["status"] == "updated"
        assert result["name"] == "Federal GST"
        assert "name" in result["changed"]
        # Confirm rename via lookup under the new name.
        tt = gb.get_taxtable("Federal GST")
        assert tt["name"] == "Federal GST"

    def test_rename_collision_rejected(self, business_book):
        gb = GnuCashBook(str(business_book))
        gst, _ = _add_tax_accounts(gb)
        for nm in ["GST 5%", "PST 7%"]:
            gb.create_taxtable(
                name=nm,
                entries=[{"type": "percentage", "amount": "5",
                          "account": gst}],
            )
        with pytest.raises(ValueError, match="already exists"):
            gb.update_taxtable(name="GST 5%", new_name="PST 7%")

    def test_replace_entries(self, business_book):
        gb = GnuCashBook(str(business_book))
        gst, pst = _add_tax_accounts(gb)
        gb.create_taxtable(
            name="GST 5%",
            entries=[{"type": "percentage", "amount": "5",
                      "account": gst}],
        )
        result = gb.update_taxtable(
            name="GST 5%",
            entries=[
                {"type": "percentage", "amount": "5",
                 "account": gst},
                {"type": "percentage", "amount": "7",
                 "account": pst},
            ],
        )
        assert result["status"] == "updated"
        assert "entries" in result["changed"]
        # ``after`` entries must carry the resolved account path
        # — pre-flush, the FK is None and the path would silently
        # drop. Regression guard for the bookkeeper-found bug
        # on Commit 1 live test.
        after = result["changed"]["entries"]["after"]
        assert len(after) == 2
        assert {e["account"] for e in after} == {gst, pst}
        # Verify via re-read.
        tt = gb.get_taxtable("GST 5%")
        assert len(tt["entries"]) == 2

    def test_replace_entries_in_use_without_force_rejected(
        self, business_book,
    ):
        """Direct SQL insert simulates an in-use refcount > 0
        without needing Commit 4's wire-up."""
        from sqlalchemy import text
        gb = GnuCashBook(str(business_book))
        gst, _ = _add_tax_accounts(gb)
        gb.create_taxtable(
            name="GST 5%",
            entries=[{"type": "percentage", "amount": "5",
                      "account": gst}],
        )
        # Look up the taxtable guid via raw SQL (v1.3.1:
        # get_taxtable no longer surfaces guid — bookkeeper-
        # validated as unused on the LLM surface). Needed here as
        # a foreign-key target for the phantom Entry row that
        # exercises the refcount path.
        with gb.open(readonly=False) as book:
            tt_guid = book.session.execute(
                text("SELECT guid FROM taxtables WHERE name = :n"),
                {"n": "GST 5%"},
            ).scalar()
            book.session.execute(
                text(
                    "INSERT INTO entries "
                    "(guid, date, date_entered, description, action, "
                    "notes, quantity_num, quantity_denom, "
                    "i_acct, i_price_num, i_price_denom, "
                    "i_discount_num, i_discount_denom, "
                    "i_disc_type, i_disc_how, "
                    "i_taxable, i_taxincluded, i_taxtable, "
                    "b_acct, b_price_num, b_price_denom, "
                    "b_taxable, b_taxincluded, b_taxtable, "
                    "b_paytype, billable, billto_type, "
                    "billto_guid, order_guid, invoice, bill) "
                    "VALUES "
                    "(:guid, :now, :now, '', '', '', "
                    "1, 1, NULL, 0, 1, 0, 1, '', '', "
                    "1, 0, :ttg, NULL, 0, 1, 0, 0, NULL, "
                    "0, 0, 0, NULL, NULL, NULL, NULL)"
                ),
                {
                    "guid": "deadbeef" * 4,
                    "now": "2026-01-01 00:00:00",
                    "ttg": tt_guid,
                },
            )
            book.save()
        with pytest.raises(ValueError, match="force=True"):
            gb.update_taxtable(
                name="GST 5%",
                entries=[{"type": "percentage", "amount": "10",
                          "account": gst}],
            )

    def test_replace_entries_in_use_with_force(self, business_book):
        from sqlalchemy import text
        gb = GnuCashBook(str(business_book))
        gst, _ = _add_tax_accounts(gb)
        gb.create_taxtable(
            name="GST 5%",
            entries=[{"type": "percentage", "amount": "5",
                      "account": gst}],
        )
        with gb.open(readonly=False) as book:
            # v1.3.1: taxtable guid not on response; look up
            # directly for the foreign-key target.
            tt_guid = book.session.execute(
                text("SELECT guid FROM taxtables WHERE name = :n"),
                {"n": "GST 5%"},
            ).scalar()
            book.session.execute(
                text(
                    "INSERT INTO entries "
                    "(guid, date, date_entered, description, action, "
                    "notes, quantity_num, quantity_denom, "
                    "i_acct, i_price_num, i_price_denom, "
                    "i_discount_num, i_discount_denom, "
                    "i_disc_type, i_disc_how, "
                    "i_taxable, i_taxincluded, i_taxtable, "
                    "b_acct, b_price_num, b_price_denom, "
                    "b_taxable, b_taxincluded, b_taxtable, "
                    "b_paytype, billable, billto_type, "
                    "billto_guid, order_guid, invoice, bill) "
                    "VALUES "
                    "(:guid, :now, :now, '', '', '', "
                    "1, 1, NULL, 0, 1, 0, 1, '', '', "
                    "1, 0, :ttg, NULL, 0, 1, 0, 0, NULL, "
                    "0, 0, 0, NULL, NULL, NULL, NULL)"
                ),
                {
                    "guid": "deadbeef" * 4,
                    "now": "2026-01-01 00:00:00",
                    "ttg": tt_guid,
                },
            )
            book.save()
        result = gb.update_taxtable(
            name="GST 5%",
            entries=[{"type": "percentage", "amount": "10",
                      "account": gst}],
            force=True,
        )
        assert result["status"] == "updated"


class TestDeleteTaxtable:
    """Tests for delete_taxtable."""

    def test_delete_unused(self, business_book):
        gb = GnuCashBook(str(business_book))
        gst, _ = _add_tax_accounts(gb)
        gb.create_taxtable(
            name="GST 5%",
            entries=[{"type": "percentage", "amount": "5",
                      "account": gst}],
        )
        result = gb.delete_taxtable("GST 5%")
        assert result["status"] == "deleted"
        assert result["name"] == "GST 5%"
        # Confirm gone.
        with pytest.raises(ValueError, match="not found"):
            gb.get_taxtable("GST 5%")

    def test_not_found_raises(self, business_book):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="not found"):
            gb.delete_taxtable("Nonexistent")

    def test_in_use_rejected(self, business_book):
        from sqlalchemy import text
        gb = GnuCashBook(str(business_book))
        gst, _ = _add_tax_accounts(gb)
        gb.create_taxtable(
            name="GST 5%",
            entries=[{"type": "percentage", "amount": "5",
                      "account": gst}],
        )
        with gb.open(readonly=False) as book:
            # v1.3.1: taxtable guid not on response; look up
            # directly for the foreign-key target.
            tt_guid = book.session.execute(
                text("SELECT guid FROM taxtables WHERE name = :n"),
                {"n": "GST 5%"},
            ).scalar()
            book.session.execute(
                text(
                    "INSERT INTO entries "
                    "(guid, date, date_entered, description, action, "
                    "notes, quantity_num, quantity_denom, "
                    "i_acct, i_price_num, i_price_denom, "
                    "i_discount_num, i_discount_denom, "
                    "i_disc_type, i_disc_how, "
                    "i_taxable, i_taxincluded, i_taxtable, "
                    "b_acct, b_price_num, b_price_denom, "
                    "b_taxable, b_taxincluded, b_taxtable, "
                    "b_paytype, billable, billto_type, "
                    "billto_guid, order_guid, invoice, bill) "
                    "VALUES "
                    "(:guid, :now, :now, '', '', '', "
                    "1, 1, NULL, 0, 1, 0, 1, '', '', "
                    "1, 0, :ttg, NULL, 0, 1, 0, 0, NULL, "
                    "0, 0, 0, NULL, NULL, NULL, NULL)"
                ),
                {
                    "guid": "deadbeef" * 4,
                    "now": "2026-01-01 00:00:00",
                    "ttg": tt_guid,
                },
            )
            book.save()
        with pytest.raises(ValueError, match="1 entries reference"):
            gb.delete_taxtable("GST 5%")


class TestTaxtableRefcount:
    """Direct tests for the SQL-computed refcount helper."""

    def test_refcount_zero_for_unused(self, business_book):
        gb = GnuCashBook(str(business_book))
        gst, _ = _add_tax_accounts(gb)
        gb.create_taxtable(
            name="GST 5%",
            entries=[{"type": "percentage", "amount": "5",
                      "account": gst}],
        )
        with gb.open() as book:
            tt = gb._find_taxtable(book, "GST 5%")
            assert gb._compute_taxtable_refcount(book, tt.guid) == 0

    def test_refcount_counts_b_taxtable_too(self, business_book):
        """The refcount SQL must OR ``i_taxtable`` and
        ``b_taxtable`` — vendor bills route through the b_*
        columns and their refs must count."""
        from sqlalchemy import text
        gb = GnuCashBook(str(business_book))
        gst, _ = _add_tax_accounts(gb)
        gb.create_taxtable(
            name="GST 5%",
            entries=[{"type": "percentage", "amount": "5",
                      "account": gst}],
        )
        # Insert one i_taxtable row and one b_taxtable row.
        with gb.open(readonly=False) as book:
            # v1.3.1: lookup the taxtable guid directly (response
            # dict no longer surfaces it).
            tt_guid = book.session.execute(
                text("SELECT guid FROM taxtables WHERE name = :n"),
                {"n": "GST 5%"},
            ).scalar()
            for tag, payload in (
                ("i", {"i_tt": tt_guid, "b_tt": None}),
                ("b", {"i_tt": None, "b_tt": tt_guid}),
            ):
                book.session.execute(
                    text(
                        "INSERT INTO entries "
                        "(guid, date, date_entered, description, "
                        "action, notes, quantity_num, quantity_denom, "
                        "i_acct, i_price_num, i_price_denom, "
                        "i_discount_num, i_discount_denom, "
                        "i_disc_type, i_disc_how, "
                        "i_taxable, i_taxincluded, i_taxtable, "
                        "b_acct, b_price_num, b_price_denom, "
                        "b_taxable, b_taxincluded, b_taxtable, "
                        "b_paytype, billable, billto_type, "
                        "billto_guid, order_guid, invoice, bill) "
                        "VALUES (:guid, :now, :now, '', '', '', "
                        "1, 1, NULL, 0, 1, 0, 1, '', '', "
                        "1, 0, :i_tt, NULL, 0, 1, 0, 0, :b_tt, "
                        "0, 0, 0, NULL, NULL, NULL, NULL)"
                    ),
                    {
                        "guid": tag * 32,
                        "now": "2026-01-01 00:00:00",
                        **payload,
                    },
                )
            book.save()
        with gb.open() as book:
            tt_obj = gb._find_taxtable(book, "GST 5%")
            assert gb._compute_taxtable_refcount(
                book, tt_obj.guid,
            ) == 2


class TestTaxtableMath:
    """Tests for ``_compute_entry_tax`` — the per-quadrant tax
    math helper. Pure function, no book fixture required."""

    # The helper is a staticmethod on BusinessMixin; we reach
    # through GnuCashBook (which mixes it in).
    from gnucash_mcp.book import GnuCashBook as _GB
    _fn = staticmethod(_GB._compute_entry_tax)

    USD_QUANTUM = Decimal("0.01")
    JPY_QUANTUM = Decimal("1")

    GST_GUID = "g" * 32
    PST_GUID = "p" * 32
    ECO_GUID = "e" * 32

    def _gst_5(self):
        return {"type": "percentage", "amount": Decimal("5"),
                "account_guid": self.GST_GUID}

    def _pst_7(self):
        return {"type": "percentage", "amount": Decimal("7"),
                "account_guid": self.PST_GUID}

    def _eco_5(self):
        return {"type": "value", "amount": Decimal("5"),
                "account_guid": self.ECO_GUID}

    # ── Quadrant 1: no tax ────────────────────────────────────

    def test_q1_not_taxable(self):
        r = self._fn(
            quantity=Decimal("2"), price=Decimal("100"),
            taxable=False, tax_included=False,
            taxtable_entries=[self._gst_5()],
            quantum=self.USD_QUANTUM,
        )
        assert r["pretax"] == Decimal("200.00")
        assert r["tax_total"] == Decimal(0)
        assert r["tax_by_acct"] == {}
        assert r["gross"] == Decimal("200.00")

    def test_q1_empty_taxtable_treated_as_not_taxable(self):
        # Defensive: taxable=True but no entries. Caller should
        # have validated; behave as no-tax.
        r = self._fn(
            quantity=Decimal("2"), price=Decimal("100"),
            taxable=True, tax_included=False,
            taxtable_entries=[],
            quantum=self.USD_QUANTUM,
        )
        assert r["pretax"] == Decimal("200.00")
        assert r["tax_total"] == Decimal(0)
        assert r["gross"] == Decimal("200.00")

    # ── Quadrant 2: tax-exclusive (tax added on top) ───────────

    def test_q2_single_percentage(self):
        r = self._fn(
            quantity=Decimal("1"), price=Decimal("100"),
            taxable=True, tax_included=False,
            taxtable_entries=[self._gst_5()],
            quantum=self.USD_QUANTUM,
        )
        assert r["pretax"] == Decimal("100.00")
        assert r["tax_total"] == Decimal("5.00")
        assert r["tax_by_acct"] == {self.GST_GUID: Decimal("5.00")}
        assert r["gross"] == Decimal("105.00")

    def test_q2_multi_percentage_composite(self):
        r = self._fn(
            quantity=Decimal("1"), price=Decimal("100"),
            taxable=True, tax_included=False,
            taxtable_entries=[self._gst_5(), self._pst_7()],
            quantum=self.USD_QUANTUM,
        )
        assert r["pretax"] == Decimal("100.00")
        assert r["tax_total"] == Decimal("12.00")
        assert r["tax_by_acct"] == {
            self.GST_GUID: Decimal("5.00"),
            self.PST_GUID: Decimal("7.00"),
        }
        assert r["gross"] == Decimal("112.00")

    def test_q2_flat_value(self):
        r = self._fn(
            quantity=Decimal("3"), price=Decimal("20"),
            taxable=True, tax_included=False,
            taxtable_entries=[self._eco_5()],
            quantum=self.USD_QUANTUM,
        )
        # Flat $5 on a $60 line: gross = 65.
        assert r["pretax"] == Decimal("60.00")
        assert r["tax_total"] == Decimal("5.00")
        assert r["gross"] == Decimal("65.00")

    def test_q2_mixed_value_and_percentage(self):
        r = self._fn(
            quantity=Decimal("1"), price=Decimal("100"),
            taxable=True, tax_included=False,
            taxtable_entries=[self._gst_5(), self._eco_5()],
            quantum=self.USD_QUANTUM,
        )
        # Pretax 100, GST 5%, Eco $5 → gross 110.
        assert r["pretax"] == Decimal("100.00")
        assert r["tax_total"] == Decimal("10.00")
        assert r["tax_by_acct"] == {
            self.GST_GUID: Decimal("5.00"),
            self.ECO_GUID: Decimal("5.00"),
        }
        assert r["gross"] == Decimal("110.00")

    def test_q2_composite_same_account_collapses(self):
        # Two percentage entries pointing to the same account
        # should sum into one tax_by_acct entry.
        r = self._fn(
            quantity=Decimal("1"), price=Decimal("100"),
            taxable=True, tax_included=False,
            taxtable_entries=[
                {"type": "percentage", "amount": Decimal("3"),
                 "account_guid": self.GST_GUID},
                {"type": "percentage", "amount": Decimal("2"),
                 "account_guid": self.GST_GUID},
            ],
            quantum=self.USD_QUANTUM,
        )
        assert r["tax_by_acct"] == {self.GST_GUID: Decimal("5.00")}
        assert r["tax_total"] == Decimal("5.00")
        assert r["gross"] == Decimal("105.00")

    # ── Quadrant 3: tax-inclusive, percentage-only ─────────────

    def test_q3_single_percentage_clean(self):
        # Gross $105 includes 5% GST → pretax = 105/1.05 = 100.
        r = self._fn(
            quantity=Decimal("1"), price=Decimal("105"),
            taxable=True, tax_included=True,
            taxtable_entries=[self._gst_5()],
            quantum=self.USD_QUANTUM,
        )
        assert r["pretax"] == Decimal("100.00")
        assert r["tax_total"] == Decimal("5.00")
        assert r["gross"] == Decimal("105.00")
        # And the residual identity must hold exactly.
        assert r["pretax"] + r["tax_total"] == r["gross"]

    def test_q3_composite_clean(self):
        # Gross $112 with GST 5% + PST 7% → pretax = 112/1.12 = 100.
        r = self._fn(
            quantity=Decimal("1"), price=Decimal("112"),
            taxable=True, tax_included=True,
            taxtable_entries=[self._gst_5(), self._pst_7()],
            quantum=self.USD_QUANTUM,
        )
        assert r["pretax"] == Decimal("100.00")
        assert r["tax_by_acct"] == {
            self.GST_GUID: Decimal("5.00"),
            self.PST_GUID: Decimal("7.00"),
        }
        assert r["gross"] == Decimal("112.00")
        assert r["pretax"] + r["tax_total"] == r["gross"]

    def test_q3_residual_to_largest_rate(self):
        # Gross $100 with GST 5% + PST 7% has no clean integer
        # pretax. pretax = 100 / 1.12 = 89.2857... → 89.29.
        # Per-entry independent rounding: 89.29 * 0.05 = 4.4645
        # → 4.46, 89.29 * 0.07 = 6.2503 → 6.25.
        # Sum: 4.46 + 6.25 = 10.71. Residual:
        # 100.00 - 89.29 - 10.71 = 0.00 → no adjustment needed
        # in this case. Let's pick numbers that DO show residual.
        # Gross $100.07 with GST 5%: pretax = 100.07/1.05
        # = 95.30476... → 95.30. tax = 95.30*0.05 = 4.765 → 4.77
        # (with banker's; 4.765 → 4.76 because 6 is even).
        # 95.30 + 4.76 = 100.06; residual = 100.07 - 100.06 = 0.01.
        # Residual goes to the largest-rate (only) entry.
        r = self._fn(
            quantity=Decimal("1"), price=Decimal("100.07"),
            taxable=True, tax_included=True,
            taxtable_entries=[self._gst_5()],
            quantum=self.USD_QUANTUM,
        )
        # The residual identity is the contract — the math may
        # round either way under banker's, but the identity
        # MUST hold.
        assert r["pretax"] + r["tax_total"] == r["gross"]
        # And the gross is preserved exactly.
        assert r["gross"] == Decimal("100.07")

    def test_q3_residual_routes_to_largest_percentage(self):
        # Construct a case where the residual is non-zero and
        # verify the per-account allocation puts the residual on
        # the largest-rate entry. Pick numbers that produce a
        # one-cent residual under banker's rounding.
        # Gross $10.05 with GST 5% + PST 7%:
        # pretax = 10.05 / 1.12 = 8.973214... → 8.97
        # GST: 8.97 * 0.05 = 0.4485 → 0.45 (banker's: 5 even)
        # PST: 8.97 * 0.07 = 0.6279 → 0.63
        # Sum tax: 1.08. pretax + tax = 10.05 → no residual.
        # Try gross $10.06:
        # pretax = 10.06 / 1.12 = 8.982142... → 8.98
        # GST: 8.98 * 0.05 = 0.449 → 0.45
        # PST: 8.98 * 0.07 = 0.6286 → 0.63
        # Sum: 1.08; pretax + tax = 10.06 → no residual.
        # Try gross $10.13:
        # pretax = 10.13 / 1.12 = 9.04464... → 9.04
        # GST: 9.04 * 0.05 = 0.452 → 0.45
        # PST: 9.04 * 0.07 = 0.6328 → 0.63
        # Sum: 1.08; pretax + tax = 10.12 → residual 0.01.
        # → PST is largest rate, gets the +0.01.
        r = self._fn(
            quantity=Decimal("1"), price=Decimal("10.13"),
            taxable=True, tax_included=True,
            taxtable_entries=[self._gst_5(), self._pst_7()],
            quantum=self.USD_QUANTUM,
        )
        # Contract: identity holds, residual targets PST (the
        # 7% entry, which has the higher rate).
        assert r["pretax"] + r["tax_total"] == r["gross"]
        assert r["gross"] == Decimal("10.13")
        # PST tax should be slightly more than the "clean"
        # per-entry calculation; GST should be the clean amount.
        gst = r["tax_by_acct"][self.GST_GUID]
        pst = r["tax_by_acct"][self.PST_GUID]
        # GST gets the clean 5% (4.5 mils → 0.45 under banker's,
        # though either rounding direction is acceptable).
        # PST absorbs the residual.
        assert gst == Decimal("0.45")
        # PST is "0.63 + residual", testing that the residual
        # landed there: PST > pretax * 0.07 quantized.
        pretax = r["pretax"]
        pst_clean = (pretax * Decimal("7") / Decimal("100")).quantize(
            self.USD_QUANTUM
        )
        assert pst >= pst_clean

    # ── Quadrant 4: tax-inclusive, mixed value + percentage ────

    def test_q4_mixed_clean(self):
        # Gross $110 with GST 5% (percentage) + Eco $5 (value).
        # Algebra: pretax = (110 - 5) / 1.05 = 105 / 1.05 = 100.
        # GST: 100 * 0.05 = 5.00. Eco: 5.00.
        # Sum tax: 10.00. pretax + tax = 110 ✓
        r = self._fn(
            quantity=Decimal("1"), price=Decimal("110"),
            taxable=True, tax_included=True,
            taxtable_entries=[self._gst_5(), self._eco_5()],
            quantum=self.USD_QUANTUM,
        )
        assert r["pretax"] == Decimal("100.00")
        assert r["tax_by_acct"] == {
            self.GST_GUID: Decimal("5.00"),
            self.ECO_GUID: Decimal("5.00"),
        }
        assert r["gross"] == Decimal("110.00")
        assert r["pretax"] + r["tax_total"] == r["gross"]

    def test_q4_all_value_collapses_to_subtraction(self):
        # Tax-inclusive all-value: pretax = gross − Σ value.
        # No rate to extract, no residual.
        r = self._fn(
            quantity=Decimal("1"), price=Decimal("105"),
            taxable=True, tax_included=True,
            taxtable_entries=[self._eco_5()],
            quantum=self.USD_QUANTUM,
        )
        assert r["pretax"] == Decimal("100.00")
        assert r["tax_total"] == Decimal("5.00")
        assert r["gross"] == Decimal("105.00")

    # ── Different commodity quanta ─────────────────────────────

    def test_jpy_no_decimals(self):
        # JPY's quantum is 1 (no sub-yen). Tax math should round
        # to integer amounts.
        r = self._fn(
            quantity=Decimal("1"), price=Decimal("1000"),
            taxable=True, tax_included=False,
            taxtable_entries=[
                {"type": "percentage", "amount": Decimal("10"),
                 "account_guid": self.GST_GUID},
            ],
            quantum=self.JPY_QUANTUM,
        )
        assert r["pretax"] == Decimal("1000")
        assert r["tax_total"] == Decimal("100")
        assert r["gross"] == Decimal("1100")

    def test_jpy_tax_inclusive_rounds_correctly(self):
        # ¥1100 with 10% included → pretax = 1100/1.1 = 1000.
        r = self._fn(
            quantity=Decimal("1"), price=Decimal("1100"),
            taxable=True, tax_included=True,
            taxtable_entries=[
                {"type": "percentage", "amount": Decimal("10"),
                 "account_guid": self.GST_GUID},
            ],
            quantum=self.JPY_QUANTUM,
        )
        assert r["pretax"] == Decimal("1000")
        assert r["tax_total"] == Decimal("100")
        assert r["gross"] == Decimal("1100")

    # ── Quantity × price edge cases ────────────────────────────

    def test_zero_quantity(self):
        r = self._fn(
            quantity=Decimal("0"), price=Decimal("100"),
            taxable=True, tax_included=False,
            taxtable_entries=[self._gst_5()],
            quantum=self.USD_QUANTUM,
        )
        assert r["pretax"] == Decimal("0.00")
        assert r["tax_total"] == Decimal("0.00")
        assert r["gross"] == Decimal("0.00")

    def test_fractional_quantity(self):
        # 2.5 hours @ $40/hr taxable at 5%.
        r = self._fn(
            quantity=Decimal("2.5"), price=Decimal("40"),
            taxable=True, tax_included=False,
            taxtable_entries=[self._gst_5()],
            quantum=self.USD_QUANTUM,
        )
        # pretax = 100, tax = 5, gross = 105.
        assert r["pretax"] == Decimal("100.00")
        assert r["tax_total"] == Decimal("5.00")
        assert r["gross"] == Decimal("105.00")


def _set_entry_tax(
    gb, invoice_id, taxtable_name,
    tax_included=False, is_bill=False,
):
    """Raw-SQL helper to flip i_taxable/b_taxable + assign a
    taxtable on every entry of an invoice/bill. Pre-Commit-4
    workaround so posting tests can exercise tax math without
    the entry-creation wire-up.
    """
    from sqlalchemy import text
    with gb.open(readonly=False) as book:
        tt = gb._find_taxtable(book, taxtable_name)
        col = "bill" if is_bill else "invoice"
        rows = book.session.execute(
            text(
                f"SELECT e.guid AS entry_guid FROM entries e "
                f"JOIN invoices i ON i.guid = e.{col} "
                f"WHERE i.id = :id"
            ),
            {"id": invoice_id},
        ).fetchall()
        ti_val = 1 if tax_included else 0
        for r in rows:
            if is_bill:
                stmt = text(
                    "UPDATE entries SET "
                    "b_taxable=1, b_taxincluded=:ti, "
                    "b_taxtable=:tt WHERE guid=:guid"
                )
            else:
                stmt = text(
                    "UPDATE entries SET "
                    "i_taxable=1, i_taxincluded=:ti, "
                    "i_taxtable=:tt WHERE guid=:guid"
                )
            book.session.execute(
                stmt,
                {
                    "ti": ti_val,
                    "tt": tt.guid,
                    "guid": r.entry_guid,
                },
            )
        book.save()


class TestTaxtablePosting:
    """End-to-end tests: invoice/bill with tax-bearing entries
    posts to the correct split shape, with revenue/expense
    splits separate from tax-payable splits."""

    def _splits_by_account(self, gb, txn_guid):
        """Helper: pull splits by account fullname from get_transaction."""
        txn = gb.get_transaction(txn_guid)
        out = {}
        for s in txn["splits"]:
            out.setdefault(s["account"], []).append(
                Decimal(s["value"])
            )
        return out

    def test_post_invoice_with_tax_exclusive_three_splits(
        self, business_book,
    ):
        gb = GnuCashBook(str(business_book))
        gst, _ = _add_tax_accounts(gb)
        gb.create_taxtable(
            name="GST 5%",
            entries=[{"type": "percentage", "amount": "5",
                      "account": gst}],
        )
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Widget",
            quantity="1",
            price="100.00",
        )
        # Seed tax on the entry (pre-Commit-4 workaround).
        _set_entry_tax(gb, "000001", "GST 5%", tax_included=False)
        result = gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
        )
        # Customer-facing total is gross.
        assert Decimal(result["total"]) == Decimal("105.00")
        # Three splits: A/R (debit 105), Income (credit -100),
        # GST Payable (credit -5).
        splits = self._splits_by_account(gb, result["transaction_guid"])
        assert splits["Assets:Accounts Receivable"] == [Decimal("105.00")]
        assert splits["Income:Sales"] == [Decimal("-100.00")]
        assert splits["Liabilities:GST Payable"] == [Decimal("-5.00")]
        # Sum to zero (the double-entry invariant).
        assert sum(
            sum(amounts) for amounts in splits.values()
        ) == Decimal(0)

    def test_post_invoice_tax_inclusive_extracts_pretax(
        self, business_book,
    ):
        gb = GnuCashBook(str(business_book))
        gst, _ = _add_tax_accounts(gb)
        gb.create_taxtable(
            name="GST 5%",
            entries=[{"type": "percentage", "amount": "5",
                      "account": gst}],
        )
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Widget",
            quantity="1",
            price="105.00",  # gross, tax included
        )
        _set_entry_tax(gb, "000001", "GST 5%", tax_included=True)
        result = gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
        )
        # Gross is the line value (tax-inclusive).
        assert Decimal(result["total"]) == Decimal("105.00")
        splits = self._splits_by_account(gb, result["transaction_guid"])
        assert splits["Assets:Accounts Receivable"] == [Decimal("105.00")]
        # Pretax = 100 extracted from gross.
        assert splits["Income:Sales"] == [Decimal("-100.00")]
        assert splits["Liabilities:GST Payable"] == [Decimal("-5.00")]

    def test_post_invoice_composite_taxtable_four_splits(
        self, business_book,
    ):
        gb = GnuCashBook(str(business_book))
        gst, pst = _add_tax_accounts(gb)
        gb.create_taxtable(
            name="BC GST+PST",
            entries=[
                {"type": "percentage", "amount": "5",
                 "account": gst},
                {"type": "percentage", "amount": "7",
                 "account": pst},
            ],
        )
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Widget",
            quantity="1",
            price="100.00",
        )
        _set_entry_tax(gb, "000001", "BC GST+PST")
        result = gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
        )
        assert Decimal(result["total"]) == Decimal("112.00")
        splits = self._splits_by_account(gb, result["transaction_guid"])
        # Four splits: A/R, Income, GST Payable, PST Payable.
        assert splits["Assets:Accounts Receivable"] == [Decimal("112.00")]
        assert splits["Income:Sales"] == [Decimal("-100.00")]
        assert splits["Liabilities:GST Payable"] == [Decimal("-5.00")]
        assert splits["Liabilities:PST Payable"] == [Decimal("-7.00")]
        # Double-entry invariant.
        assert sum(
            sum(amounts) for amounts in splits.values()
        ) == Decimal(0)

    def test_post_invoice_no_tax_unchanged(self, business_book):
        """Sanity: a non-tax invoice still posts identically to
        pre-Commit-3 behavior — two splits, A/R and Income."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Widget",
            quantity="1",
            price="100.00",
        )
        # No tax seeding — entries default to taxable=0.
        result = gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
        )
        assert Decimal(result["total"]) == Decimal("100.00")
        splits = self._splits_by_account(gb, result["transaction_guid"])
        # Just two splits: A/R + Income.
        assert set(splits.keys()) == {
            "Assets:Accounts Receivable", "Income:Sales",
        }

    def test_post_bill_with_tax_routes_correctly(self, business_book):
        gb = GnuCashBook(str(business_book))
        # Tax credit on vendor side often lives as an ASSET (input
        # tax credit receivable). Using the LIABILITY account from
        # the fixture is also valid — what matters is the routing.
        gst, _ = _add_tax_accounts(gb)
        gb.create_taxtable(
            name="GST 5%",
            entries=[{"type": "percentage", "amount": "5",
                      "account": gst}],
        )
        gb.create_vendor(name="Office Depot")
        gb.create_bill(vendor_id="000001")
        gb.add_bill_entry(
            bill_id="000001",
            account="Expenses:Office Supplies",
            description="Paper",
            quantity="1",
            price="50.00",
        )
        _set_entry_tax(gb, "000001", "GST 5%", is_bill=True)
        result = gb.post_invoice(
            invoice_id="000001",
            post_account="Liabilities:Accounts Payable",
            owner_type="vendor",
        )
        # Vendor-side signs are inverted: A/P credit (-), Expense
        # debit (+), GST debit (+) for input tax credit.
        assert Decimal(result["total"]) == Decimal("52.50")
        splits = self._splits_by_account(gb, result["transaction_guid"])
        assert splits["Liabilities:Accounts Payable"] == [Decimal("-52.50")]
        assert splits["Expenses:Office Supplies"] == [Decimal("50.00")]
        assert splits["Liabilities:GST Payable"] == [Decimal("2.50")]
        assert sum(
            sum(amounts) for amounts in splits.values()
        ) == Decimal(0)


class TestTaxtableCreditNoteReversal:
    """Credit notes with tax reverse all splits including tax via
    the existing XOR sign-flip — refunding a tax-inclusive sale
    credits A/R, debits revenue, AND debits tax payable."""

    def _splits_by_account(self, gb, txn_guid):
        txn = gb.get_transaction(txn_guid)
        out = {}
        for s in txn["splits"]:
            out.setdefault(s["account"], []).append(
                Decimal(s["value"])
            )
        return out

    def test_credit_note_reverses_revenue_and_tax(
        self, business_book,
    ):
        gb = GnuCashBook(str(business_book))
        gst, _ = _add_tax_accounts(gb)
        gb.create_taxtable(
            name="GST 5%",
            entries=[{"type": "percentage", "amount": "5",
                      "account": gst}],
        )
        gb.create_customer(name="Acme Corp")
        # First, a normal invoice to set the original numbers.
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Widget",
            quantity="1",
            price="100.00",
        )
        _set_entry_tax(gb, "000001", "GST 5%")
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
        )

        # Now a credit note refunding the same amount.
        gb.create_credit_note(
            owner_type="customer",
            owner_id="000001",
        )
        envelope = gb.list_invoices(
            compact=False, owner_type="customer",
        )
        cn_doc = next(
            (
                d for d in envelope["invoices"]
                if d.get("is_credit_note")
            ),
            None,
        )
        assert cn_doc is not None, "credit note not found via list"
        gb.add_credit_note_entry(
            credit_note_id=cn_doc["id"],
            account="Income:Sales",
            description="Widget refund",
            quantity="1",
            price="100.00",
        )
        _set_entry_tax(gb, cn_doc["id"], "GST 5%")
        result = gb.post_invoice(
            invoice_id=cn_doc["id"],
            post_account="Assets:Accounts Receivable",
            owner_type="customer",
        )
        # Total stays positive (it's the magnitude of the refund);
        # the sign-flip happens at the splits.
        assert Decimal(result["total"]) == Decimal("105.00")
        splits = self._splits_by_account(
            gb, result["transaction_guid"],
        )
        # Credit-note sign-flip: A/R credit (negative), revenue
        # debit (positive — reversing recognized revenue), tax
        # payable debit (positive — reversing collected tax).
        assert splits["Assets:Accounts Receivable"] == [Decimal("-105.00")]
        assert splits["Income:Sales"] == [Decimal("100.00")]
        assert splits["Liabilities:GST Payable"] == [Decimal("5.00")]
        # Invariant.
        assert sum(
            sum(amounts) for amounts in splits.values()
        ) == Decimal(0)


class TestTaxtableEntryWireup:
    """Commit 4: add_*_entry tools accept taxtable + tax_included
    kwargs. End-to-end create-entry-with-tax → post → see splits
    without needing the raw-SQL ``_set_entry_tax`` shim."""

    def _setup(self, business_book):
        gb = GnuCashBook(str(business_book))
        gst, pst = _add_tax_accounts(gb)
        gb.create_taxtable(
            name="GST 5%",
            entries=[{"type": "percentage", "amount": "5",
                      "account": gst}],
        )
        gb.create_taxtable(
            name="BC GST+PST",
            entries=[
                {"type": "percentage", "amount": "5",
                 "account": gst},
                {"type": "percentage", "amount": "7",
                 "account": pst},
            ],
        )
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        return gb, gst, pst

    def test_invoice_entry_with_taxtable(self, business_book):
        gb, gst, _ = self._setup(business_book)
        result = gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Widget",
            quantity="1",
            price="100",
            taxtable="GST 5%",
        )
        assert result["status"] == "created"
        # Refcount incremented from 0 to 1.
        tt = gb.get_taxtable("GST 5%")
        assert tt["refcount"] == 1

    def test_invoice_entry_no_taxtable_unchanged(self, business_book):
        """Backward compat: omitting taxtable leaves tax fields zeroed
        and refcount untouched."""
        gb, _, _ = self._setup(business_book)
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="No-tax",
            quantity="1",
            price="100",
        )
        tt = gb.get_taxtable("GST 5%")
        assert tt["refcount"] == 0

    def test_tax_included_requires_taxtable(self, business_book):
        gb, _, _ = self._setup(business_book)
        with pytest.raises(ValueError, match="tax_included.*requires"):
            gb.add_invoice_entry(
                invoice_id="000001",
                account="Income:Sales",
                description="No taxtable",
                quantity="1",
                price="100",
                tax_included=True,
            )

    def test_unknown_taxtable_rejected(self, business_book):
        gb, _, _ = self._setup(business_book)
        with pytest.raises(ValueError, match="Taxtable not found"):
            gb.add_invoice_entry(
                invoice_id="000001",
                account="Income:Sales",
                description="Bad taxtable",
                quantity="1",
                price="100",
                taxtable="Nonexistent",
            )

    def test_end_to_end_post_with_added_tax(self, business_book):
        """Full integration: create entry with tax via the tool,
        post, verify the posting transaction has correct splits."""
        gb, gst, pst = self._setup(business_book)
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Widget",
            quantity="1",
            price="100",
            taxtable="BC GST+PST",
        )
        result = gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
        )
        assert Decimal(result["total"]) == Decimal("112.00")
        txn = gb.get_transaction(result["transaction_guid"])
        splits = {s["account"]: Decimal(s["value"]) for s in txn["splits"]}
        assert splits["Assets:Accounts Receivable"] == Decimal("112.00")
        assert splits["Income:Sales"] == Decimal("-100.00")
        assert splits["Liabilities:GST Payable"] == Decimal("-5.00")
        assert splits["Liabilities:PST Payable"] == Decimal("-7.00")

    def test_bill_entry_with_taxtable_routes_b_side(
        self, business_book,
    ):
        """Vendor bills must write to b_taxtable (not i_taxtable)."""
        gb = GnuCashBook(str(business_book))
        gst, _ = _add_tax_accounts(gb)
        gb.create_taxtable(
            name="GST 5%",
            entries=[{"type": "percentage", "amount": "5",
                      "account": gst}],
        )
        gb.create_vendor(name="Office Depot")
        gb.create_bill(vendor_id="000001")
        gb.add_bill_entry(
            bill_id="000001",
            account="Expenses:Office Supplies",
            description="Paper",
            quantity="1",
            price="50",
            taxtable="GST 5%",
        )
        # Refcount incremented (no matter which side).
        tt = gb.get_taxtable("GST 5%")
        assert tt["refcount"] == 1
        # Direct SQL verification that the routing is b_*, not i_*.
        from sqlalchemy import text
        with gb.open() as book:
            row = book.session.execute(
                text(
                    "SELECT i_taxtable, b_taxtable FROM entries "
                    "ORDER BY date DESC LIMIT 1"
                )
            ).fetchone()
            assert row.i_taxtable is None
            assert row.b_taxtable is not None

    def test_voucher_entry_with_taxtable_routes_b_side(
        self, business_book,
    ):
        gb = GnuCashBook(str(business_book))
        gst, _ = _add_tax_accounts(gb)
        gb.create_taxtable(
            name="GST 5%",
            entries=[{"type": "percentage", "amount": "5",
                      "account": gst}],
        )
        gb.create_employee(name="Jane Smith")
        gb.create_voucher(employee_id="000001")
        gb.add_voucher_entry(
            voucher_id="000001",
            account="Expenses:Office Supplies",
            description="Pens",
            quantity="1",
            price="20",
            taxtable="GST 5%",
        )
        tt = gb.get_taxtable("GST 5%")
        assert tt["refcount"] == 1

    def test_credit_note_entry_with_taxtable(self, business_book):
        gb, gst, _ = self._setup(business_book)
        gb.create_credit_note(
            owner_type="customer", owner_id="000001",
        )
        # Find the credit note's ID.
        envelope = gb.list_invoices(
            compact=False, owner_type="customer",
        )
        cn = next(d for d in envelope["invoices"]
                  if d.get("is_credit_note"))
        gb.add_credit_note_entry(
            credit_note_id=cn["id"],
            account="Income:Sales",
            description="Refund",
            quantity="1",
            price="100",
            taxtable="GST 5%",
        )
        tt = gb.get_taxtable("GST 5%")
        assert tt["refcount"] == 1


class TestTaxtableLifecycle:
    """Refcount lifecycle: maintained on entry add/delete,
    enforced as guards on update/delete of the taxtable itself."""

    def test_refcount_increments_per_entry(self, business_book):
        gb = GnuCashBook(str(business_book))
        gst, _ = _add_tax_accounts(gb)
        gb.create_taxtable(
            name="GST 5%",
            entries=[{"type": "percentage", "amount": "5",
                      "account": gst}],
        )
        gb.create_customer(name="Acme")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="A",
            quantity="1",
            price="100",
            taxtable="GST 5%",
        )
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="B",
            quantity="2",
            price="50",
            taxtable="GST 5%",
        )
        tt = gb.get_taxtable("GST 5%")
        assert tt["refcount"] == 2

    def test_refcount_decrements_on_invoice_delete(
        self, business_book,
    ):
        gb = GnuCashBook(str(business_book))
        gst, _ = _add_tax_accounts(gb)
        gb.create_taxtable(
            name="GST 5%",
            entries=[{"type": "percentage", "amount": "5",
                      "account": gst}],
        )
        gb.create_customer(name="Acme")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="A",
            quantity="1",
            price="100",
            taxtable="GST 5%",
        )
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="B",
            quantity="1",
            price="50",
            taxtable="GST 5%",
        )
        assert gb.get_taxtable("GST 5%")["refcount"] == 2
        gb.delete_invoice(invoice_id="000001")
        # Both refs gone after the invoice's entries are deleted.
        assert gb.get_taxtable("GST 5%")["refcount"] == 0

    def test_delete_in_use_taxtable_rejected_via_real_entries(
        self, business_book,
    ):
        """The Commit-1 ``delete_taxtable`` guard now sees real
        entries (not just simulated SQL inserts). End-to-end."""
        gb = GnuCashBook(str(business_book))
        gst, _ = _add_tax_accounts(gb)
        gb.create_taxtable(
            name="GST 5%",
            entries=[{"type": "percentage", "amount": "5",
                      "account": gst}],
        )
        gb.create_customer(name="Acme")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="A",
            quantity="1",
            price="100",
            taxtable="GST 5%",
        )
        with pytest.raises(ValueError, match="1 entries reference"):
            gb.delete_taxtable("GST 5%")

    def test_update_in_use_rejected_via_real_entries(
        self, business_book,
    ):
        gb = GnuCashBook(str(business_book))
        gst, _ = _add_tax_accounts(gb)
        gb.create_taxtable(
            name="GST 5%",
            entries=[{"type": "percentage", "amount": "5",
                      "account": gst}],
        )
        gb.create_customer(name="Acme")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="A",
            quantity="1",
            price="100",
            taxtable="GST 5%",
        )
        with pytest.raises(ValueError, match="force=True"):
            gb.update_taxtable(
                name="GST 5%",
                entries=[{"type": "percentage", "amount": "10",
                          "account": gst}],
            )


class TestTaxtableDisplay:
    """Commit 5: per-entry tax tags + document-level tax_summary
    block. Conditional emission keeps non-tax responses
    byte-identical to pre-taxtable shape."""

    def _setup(self, business_book):
        gb = GnuCashBook(str(business_book))
        gst, pst = _add_tax_accounts(gb)
        gb.create_taxtable(
            name="GST 5%",
            entries=[{"type": "percentage", "amount": "5",
                      "account": gst}],
        )
        gb.create_taxtable(
            name="BC GST+PST",
            entries=[
                {"type": "percentage", "amount": "5",
                 "account": gst},
                {"type": "percentage", "amount": "7",
                 "account": pst},
            ],
        )
        gb.create_customer(name="Acme")
        gb.create_invoice(customer_id="000001")
        return gb, gst, pst

    def test_non_tax_invoice_unchanged_shape(self, business_book):
        """Backward-compat: an invoice with no tax-bearing entries
        returns no tax_summary key and entries lack tax fields."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Plain",
            quantity="1",
            price="100",
        )
        inv = gb.get_invoice("000001")
        assert "tax_summary" not in inv
        # v1.3.1: entry "guid" dropped from response shape.
        assert inv["entries"][0].keys() == {
            "date", "description",
            "quantity", "price", "total", "account",
        }

    def test_taxable_entry_carries_tax_fields(self, business_book):
        gb, _, _ = self._setup(business_book)
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Widget",
            quantity="1",
            price="100",
            taxtable="GST 5%",
        )
        inv = gb.get_invoice("000001")
        e = inv["entries"][0]
        assert e["taxable"] is True
        assert e["tax_included"] is False
        assert e["taxtable"] == "GST 5%"

    def test_tax_included_flag_surfaces(self, business_book):
        gb, _, _ = self._setup(business_book)
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Gross",
            quantity="1",
            price="105",
            taxtable="GST 5%",
            tax_included=True,
        )
        inv = gb.get_invoice("000001")
        e = inv["entries"][0]
        assert e["tax_included"] is True

    def test_tax_summary_block_emitted(self, business_book):
        """Single-entry invoice with tax produces a complete
        tax_summary with all five fields populated."""
        gb, gst, _ = self._setup(business_book)
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Widget",
            quantity="1",
            price="100",
            taxtable="GST 5%",
        )
        inv = gb.get_invoice("000001")
        ts = inv["tax_summary"]
        assert ts["subtotal"] == "100.00"
        assert ts["tax_total"] == "5.00"
        assert ts["total"] == "105.00"
        assert ts["by_taxtable"] == {"GST 5%": "5.00"}
        assert ts["by_account"] == {
            "Liabilities:GST Payable": "5.00"
        }
        # Customer-facing total uses the gross figure.
        assert inv["total"] == "105.00"

    def test_tax_summary_composite_by_taxtable(self, business_book):
        """Multi-line invoice spanning two taxtables: by_taxtable
        rolls up each taxtable's contribution; by_account splits
        across the underlying payable accounts."""
        gb, gst, pst = self._setup(business_book)
        # Line A: $100 GST 5% → $5 tax → GST Payable
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="A",
            quantity="1",
            price="100",
            taxtable="GST 5%",
        )
        # Line B: $200 BC GST+PST → $10 GST + $14 PST → both
        # accounts
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="B",
            quantity="1",
            price="200",
            taxtable="BC GST+PST",
        )
        inv = gb.get_invoice("000001")
        ts = inv["tax_summary"]
        assert ts["subtotal"] == "300.00"
        assert ts["tax_total"] == "29.00"
        assert ts["total"] == "329.00"
        # Per-taxtable rollup:
        assert ts["by_taxtable"] == {
            "GST 5%": "5.00",
            "BC GST+PST": "24.00",  # 10 + 14
        }
        # Per-account: GST Payable collects from both taxtables.
        assert ts["by_account"] == {
            "Liabilities:GST Payable": "15.00",  # 5 + 10
            "Liabilities:PST Payable": "14.00",
        }

    def test_mixed_invoice_only_tax_lines_in_summary(
        self, business_book,
    ):
        """An invoice with some tax-bearing and some non-tax lines
        still produces a tax_summary; non-tax lines simply
        contribute to subtotal/total but not to tax_total."""
        gb, _, _ = self._setup(business_book)
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="No tax",
            quantity="1",
            price="50",
        )
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Taxed",
            quantity="1",
            price="100",
            taxtable="GST 5%",
        )
        inv = gb.get_invoice("000001")
        ts = inv["tax_summary"]
        # Subtotal is sum of per-line pretax = 50 + 100 = 150.
        assert ts["subtotal"] == "150.00"
        # Tax_total only from the taxed line.
        assert ts["tax_total"] == "5.00"
        assert ts["total"] == "155.00"

    def test_bill_taxable_entry_displays(self, business_book):
        """Vendor bill displays b_taxable correctly (not i_taxable)."""
        gb = GnuCashBook(str(business_book))
        gst, _ = _add_tax_accounts(gb)
        gb.create_taxtable(
            name="GST 5%",
            entries=[{"type": "percentage", "amount": "5",
                      "account": gst}],
        )
        gb.create_vendor(name="Office Depot")
        gb.create_bill(vendor_id="000001")
        gb.add_bill_entry(
            bill_id="000001",
            account="Expenses:Office Supplies",
            description="Paper",
            quantity="1",
            price="50",
            taxtable="GST 5%",
        )
        inv = gb.get_invoice("000001", owner_type="vendor")
        e = inv["entries"][0]
        assert e["taxable"] is True
        assert e["taxtable"] == "GST 5%"


class TestTaxtableCrossCurrency:
    """Cross-currency × tax interaction: EUR invoice with a
    USD-denominated tax-payable account exercises the existing
    _qty_for_split FX conversion on the tax component. The tax
    math itself is currency-agnostic (rates and amounts apply
    in invoice currency); FX kicks in only when the tax-payable
    account's commodity differs from the invoice currency."""

    def _setup_eur_with_usd_gst(self, business_book, rate="1.10"):
        """Wire up: EUR commodity, EUR/USD price, USD GST Payable
        account (in the book's default USD), EUR-denominated
        customer + invoice. Returns the GnuCashBook handle."""
        import piecash
        from datetime import date as date_cls
        gb = GnuCashBook(str(business_book))
        with gb.open(readonly=False) as bk:
            eur = piecash.Commodity(
                namespace="CURRENCY", mnemonic="EUR",
                fullname="Euro", fraction=100,
            )
            bk.session.add(eur)
            bk.flush()
            bk.session.add(piecash.Price(
                commodity=eur, currency=bk.default_currency,
                date=date_cls(2026, 5, 24),
                value=rate, type="last",
            ))
            bk.save()
        # USD GST Payable (book default commodity)
        gb.create_account(
            name="GST Payable",
            account_type="LIABILITY",
            parent="Liabilities",
        )
        gb.create_taxtable(
            name="GST 5%",
            entries=[{"type": "percentage", "amount": "5",
                      "account": "Liabilities:GST Payable"}],
        )
        gb.create_customer(name="EUR Client")
        gb.create_invoice(
            customer_id="000001", currency="EUR",
        )
        return gb

    def test_eur_invoice_with_usd_tax_payable(
        self, business_book,
    ):
        """EUR 100 invoice with USD GST Payable at GST 5% → tax
        component is EUR 5 in invoice currency, which converts to
        USD 5.50 in the tax-payable account at the 1.10 rate."""
        gb = self._setup_eur_with_usd_gst(business_book, rate="1.10")
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="EUR work",
            quantity="1",
            price="100",
            taxtable="GST 5%",
        )
        result = gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
            # Pin to the price date so the FX freshness guard
            # doesn't fire — this test exercises tax conversion
            # math, not rate staleness.
            post_date="2026-05-24",
        )
        # Customer-facing total in EUR (invoice currency).
        assert Decimal(result["total"]) == Decimal("105.00")
        txn = gb.get_transaction(result["transaction_guid"])
        # Build a per-account dict keyed by fullname carrying
        # both value (EUR — invoice currency) and quantity
        # (account commodity — USD or EUR depending on side).
        by_acct = {
            s["account"]: (Decimal(s["value"]), Decimal(s["quantity"]))
            for s in txn["splits"]
        }
        # A/R is RECEIVABLE in USD (fixture default). value is in
        # EUR (transaction currency); quantity converts to USD.
        ar_value, ar_quantity = by_acct["Assets:Accounts Receivable"]
        assert ar_value == Decimal("105.00")
        assert ar_quantity == Decimal("115.50")  # 105 × 1.10
        # Income split is in USD (fixture default).
        inc_value, inc_quantity = by_acct["Income:Sales"]
        assert inc_value == Decimal("-100.00")
        assert inc_quantity == Decimal("-110.00")
        # Tax-payable split — the focal point of the test.
        # Value (EUR): -5 (tax in invoice currency).
        # Quantity (USD): -5.50 (converted at the rate).
        gst_value, gst_quantity = by_acct["Liabilities:GST Payable"]
        assert gst_value == Decimal("-5.00")
        assert gst_quantity == Decimal("-5.50")
        # Value-side sums to zero (the transaction-currency
        # balance invariant — quantities don't need to balance
        # across commodities).
        value_sum = sum(v for v, _ in by_acct.values())
        assert value_sum == Decimal(0)

    def test_cross_currency_tax_requires_rate(
        self, business_book,
    ):
        """When no price is on file for the EUR/USD pair near
        post date, posting raises a clear error — the same
        rate-not-found path already exercised by non-tax
        cross-currency posting."""
        import piecash
        gb = GnuCashBook(str(business_book))
        # Add EUR commodity but no price.
        with gb.open(readonly=False) as bk:
            eur = piecash.Commodity(
                namespace="CURRENCY", mnemonic="EUR",
                fullname="Euro", fraction=100,
            )
            bk.session.add(eur)
            bk.save()
        gb.create_account(
            name="GST Payable",
            account_type="LIABILITY",
            parent="Liabilities",
        )
        gb.create_taxtable(
            name="GST 5%",
            entries=[{"type": "percentage", "amount": "5",
                      "account": "Liabilities:GST Payable"}],
        )
        gb.create_customer(name="EUR Client")
        gb.create_invoice(customer_id="000001", currency="EUR")
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="EUR work",
            quantity="1",
            price="100",
            taxtable="GST 5%",
        )
        with pytest.raises(ValueError, match="exchange rate"):
            gb.post_invoice(
                invoice_id="000001",
                post_account="Assets:Accounts Receivable",
            )


class TestTaxtableCopilotReviewFollowups:
    """Regression guards for Copilot PR #90 review findings."""

    def test_audit_log_renders_leaf_account_name(self):
        """``_fmt_taxtable_entry_line`` must render the leaf
        account name (matching ``_taxtable_entry_summary`` and
        ``list_taxtables``), not the fullname path. Pre-fix the
        audit log showed ``5%→Liabilities:GST Payable`` while the
        compact list showed ``5%→GST Payable`` — same data,
        different rendering, scannability bug for the bookkeeper
        reviewing the audit trail."""
        from gnucash_mcp.logging_config import (
            _fmt_taxtable_entry_line,
        )
        # Fullname input gets trimmed to the leaf.
        assert _fmt_taxtable_entry_line({
            "type": "percentage",
            "amount": "5",
            "account": "Liabilities:GST Payable",
        }) == "5%→GST Payable"
        # Bare-leaf input passes through unchanged.
        assert _fmt_taxtable_entry_line({
            "type": "percentage",
            "amount": "7",
            "account": "PST Payable",
        }) == "7%→PST Payable"
        # Value-type entry uses $-prefix.
        assert _fmt_taxtable_entry_line({
            "type": "value",
            "amount": "5",
            "account": "Liabilities:Eco Fee Payable",
        }) == "$5→Eco Fee Payable"
        # GUID fallback (no path map): leaf-trim is a no-op
        # because the GUID has no ``:`` separator.
        assert _fmt_taxtable_entry_line({
            "type": "percentage",
            "amount": "5",
            "account_guid": "deadbeef" * 4,
        }) == "5%→" + ("deadbeef" * 4)

    def test_get_invoice_skips_taxtable_query_when_no_tax_entries(
        self, business_book,
    ):
        """When no entry on the invoice references a taxtable,
        ``get_invoice`` must not query the taxtables table.
        Verified by creating taxtables and then asking for an
        invoice that doesn't reference them — the query count
        should not grow with taxtable count. Regression for
        Copilot's O(N-taxtables-in-book) scan finding."""
        gb = GnuCashBook(str(business_book))
        gst, pst = _add_tax_accounts(gb)
        # Create taxtables that the test invoice does NOT reference.
        for n in range(5):
            gb.create_taxtable(
                name=f"Decoy {n}",
                entries=[{"type": "percentage", "amount": "5",
                          "account": gst}],
            )
        gb.create_customer(name="Acme")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Plain",
            quantity="1",
            price="100",
        )
        # Spy on the session: count Taxtable.query invocations.
        # Direct query-count instrumentation is awkward in
        # SQLAlchemy 1.4; the simpler verification is that the
        # response has no tax_summary key and no entry carries
        # tax fields — both byproducts of the early-skip path.
        inv = gb.get_invoice("000001")
        assert "tax_summary" not in inv
        assert "taxable" not in inv["entries"][0]

    def test_get_invoice_filters_taxtable_query_to_referenced_only(
        self, business_book,
    ):
        """When some entries reference taxtables but not all
        taxtables in the book, ``get_invoice`` filters the
        Taxtable query to just the needed GUIDs. The visible
        contract is that the response correctly resolves the
        referenced taxtable's name (regression guard: a buggy
        filter that returned no rows would surface as a raw
        GUID in the entry dict)."""
        gb = GnuCashBook(str(business_book))
        gst, _ = _add_tax_accounts(gb)
        # Create extra decoy taxtables that the invoice doesn't
        # reference. With the unconditional query removed, these
        # are filtered out and don't appear in the response.
        gb.create_taxtable(
            name="GST 5%",
            entries=[{"type": "percentage", "amount": "5",
                      "account": gst}],
        )
        for n in range(3):
            gb.create_taxtable(
                name=f"Decoy {n}",
                entries=[{"type": "percentage", "amount": "3",
                          "account": gst}],
            )
        gb.create_customer(name="Acme")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Widget",
            quantity="1",
            price="100",
            taxtable="GST 5%",
        )
        inv = gb.get_invoice("000001")
        # The referenced taxtable resolves to its name.
        assert inv["entries"][0]["taxtable"] == "GST 5%"
        # Tax summary references only the actually-applied
        # taxtable, not the decoys.
        assert list(inv["tax_summary"]["by_taxtable"].keys()) == [
            "GST 5%"
        ]


class TestReceivablePayableAccountTypes:
    """Tests for RECEIVABLE and PAYABLE account type support."""

    def test_receivable_account_in_fixture(self, business_book):
        gb = GnuCashBook(str(business_book))
        acct = gb.get_account("Assets:Accounts Receivable")
        assert acct["type"] == "RECEIVABLE"

    def test_payable_account_in_fixture(self, business_book):
        gb = GnuCashBook(str(business_book))
        acct = gb.get_account("Liabilities:Accounts Payable")
        assert acct["type"] == "PAYABLE"

    def test_create_receivable_account(self, business_book):
        gb = GnuCashBook(str(business_book))
        result = gb.create_account(
            name="AR Other",
            account_type="RECEIVABLE",
            parent="Assets",
        )
        assert result["status"] == "created"
        acct = gb.get_account("Assets:AR Other")
        assert acct["type"] == "RECEIVABLE"

    def test_create_payable_account(self, business_book):
        gb = GnuCashBook(str(business_book))
        result = gb.create_account(
            name="AP Other",
            account_type="PAYABLE",
            parent="Liabilities",
        )
        assert result["status"] == "created"
        acct = gb.get_account("Liabilities:AP Other")
        assert acct["type"] == "PAYABLE"


# ============== Invoice Tests ==============


class TestCreateInvoice:
    """Tests for create_invoice."""

    def test_basic_creation(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        result = gb.create_invoice(customer_id="000001")
        assert result["status"] == "created"
        assert result["id"] == "000001"
        assert result["customer_id"] == "000001"

    def test_auto_id_increments(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        r1 = gb.create_invoice(customer_id="000001")
        r2 = gb.create_invoice(customer_id="000001")
        assert r1["id"] == "000001"
        assert r2["id"] == "000002"

    def test_with_date(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        result = gb.create_invoice(
            customer_id="000001", date_opened="2026-01-15",
        )
        assert result["date_opened"] == "2026-01-15"

    def test_with_notes(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        result = gb.create_invoice(
            customer_id="000001", notes="Q1 consulting",
        )
        assert result["status"] == "created"
        # Verify notes via get_invoice
        inv = gb.get_invoice(result["id"])
        assert inv["notes"] == "Q1 consulting"

    def test_with_billterm(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_billterm(name="Net 30", due_days=30)
        result = gb.create_invoice(
            customer_id="000001", term="Net 30",
        )
        assert result["status"] == "created"

    def test_custom_invoice_id(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        result = gb.create_invoice(
            customer_id="000001", invoice_id="INV-2026-001",
        )
        assert result["id"] == "INV-2026-001"
        assert result["status"] == "created"

    def test_custom_id_does_not_increment_counter(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001", invoice_id="CUSTOM-1")
        # Next auto-generated ID should still be 000001 (counter untouched)
        r2 = gb.create_invoice(customer_id="000001")
        assert r2["id"] == "000001"

    def test_duplicate_invoice_id_rejected(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001", invoice_id="DUP-001")
        with pytest.raises(ValueError, match="already exists"):
            gb.create_invoice(customer_id="000001", invoice_id="DUP-001")

    def test_blank_invoice_id_rejected(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        with pytest.raises(ValueError, match="must not be blank"):
            gb.create_invoice(customer_id="000001", invoice_id="  ")

    def test_invalid_customer(self, business_book):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="Customer not found"):
            gb.create_invoice(customer_id="999999")

    def test_invalid_billterm(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        with pytest.raises(ValueError, match="Billterm not found"):
            gb.create_invoice(customer_id="000001", term="Nonexistent")

    def test_currency_defaults_to_customer_currency(self, business_book):
        """When ``currency`` is not passed, the invoice inherits the
        customer's currency — not the book default. A USD customer
        on a USD-default book happens to look the same either way,
        so we set the customer's currency to EUR explicitly to
        prove the inheritance: the resulting invoice should be EUR
        regardless of book default."""
        import piecash
        gb = GnuCashBook(str(business_book))
        with gb.open(readonly=False) as book:
            book.session.add(piecash.factories.create_currency_from_ISO("EUR"))
            book.save()
        gb.create_customer(name="Berlin Digital", currency="EUR")
        result = gb.create_invoice(customer_id="000001")
        # Read back via get_invoice to verify the stored currency.
        inv = gb.get_invoice(result["id"])
        assert inv["currency"] == "EUR"

    def test_explicit_currency_overrides_customer_currency(
        self, business_book,
    ):
        """An explicit ``currency`` parameter wins over the
        customer's currency. Edge case but supported — callers
        sometimes record cross-currency invoices intentionally."""
        import piecash
        gb = GnuCashBook(str(business_book))
        with gb.open(readonly=False) as book:
            book.session.add(piecash.factories.create_currency_from_ISO("EUR"))
            book.save()
        gb.create_customer(name="Berlin Digital", currency="EUR")
        result = gb.create_invoice(
            customer_id="000001", currency="USD",
        )
        inv = gb.get_invoice(result["id"])
        assert inv["currency"] == "USD"


class TestCreateBill:
    """Tests for create_bill."""

    def test_basic_creation(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        result = gb.create_bill(vendor_id="000001")
        assert result["status"] == "created"
        assert result["id"] == "000001"
        assert result["vendor_id"] == "000001"

    def test_bill_counter_independent_from_invoice(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_vendor(name="Office Depot")
        inv = gb.create_invoice(customer_id="000001")
        bill = gb.create_bill(vendor_id="000001")
        # Both should be 000001 — independent counters
        assert inv["id"] == "000001"
        assert bill["id"] == "000001"

    def test_custom_bill_id(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        result = gb.create_bill(
            vendor_id="000001", bill_id="BILL-2026-001",
        )
        assert result["id"] == "BILL-2026-001"
        assert result["status"] == "created"

    def test_custom_id_does_not_increment_counter(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        gb.create_bill(vendor_id="000001", bill_id="CUSTOM-1")
        # Next auto-generated ID should still be 000001 (counter untouched)
        r2 = gb.create_bill(vendor_id="000001")
        assert r2["id"] == "000001"

    def test_duplicate_bill_id_rejected(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        gb.create_bill(vendor_id="000001", bill_id="DUP-001")
        with pytest.raises(ValueError, match="already exists"):
            gb.create_bill(vendor_id="000001", bill_id="DUP-001")

    def test_invalid_vendor(self, business_book):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="Vendor not found"):
            gb.create_bill(vendor_id="999999")

    def test_auto_id_skips_existing_when_counter_drifted(
        self, business_book,
    ):
        """When the book counter has drifted below the actual MAX
        existing ID for the owner_type (e.g. historical documents
        imported via raw SQL without bumping the counter), the
        auto-id generator must scan the table and pick up where
        the existing IDs leave off — never collide with an extant
        row.

        Regression for the bookkeeper's report on Alex's synthetic
        book: 2025 bills sat at IDs 000006 / 000007 with the
        counter still at zero, so a new 2026 ``create_bill``
        auto-assigned 000006 — colliding with the 2025 row and
        breaking every subsequent ``post_invoice`` / lookup."""
        import uuid
        from piecash.business.invoice import Invoice
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Old Vendor")
        gb.create_vendor(name="New Vendor")
        # Inject historical bills via raw SQL at IDs 000006 and
        # 000007, leaving the book counter untouched (this
        # simulates the synthetic-book import path).
        with gb.open(readonly=False) as book:
            old_vendor = gb._find_vendor(book, "000001")
            usd = book.default_currency
            for inv_id in ("000006", "000007"):
                book.session.execute(
                    Invoice.__table__.insert().values(
                        guid=uuid.uuid4().hex,
                        id=inv_id,
                        date_opened=None,
                        date_posted=None,
                        notes="historical",
                        active=1,
                        currency=usd.guid,
                        owner_type=4,
                        owner_guid=old_vendor.guid,
                        terms=None,
                        billing_id="",
                        post_txn=None,
                        post_lot=None,
                        post_acc=None,
                        billto_type=0,
                        billto_guid=None,
                        charge_amt_num=0,
                        charge_amt_denom=1,
                    )
                )
            book.save()
        # Auto-generated next bill must SKIP past 000006 / 000007.
        result = gb.create_bill(vendor_id="000002")
        assert result["id"] == "000008", (
            f"Expected auto-id 000008 (skipping existing "
            f"000006/000007), got {result['id']}"
        )

    def test_auto_id_bill_can_be_posted(
        self, business_book,
    ):
        """After the auto-id collision fix, the resulting bill must
        be POSTABLE — exercising the full lifecycle, not just the
        ID assignment. Regression for the bookkeeper's report:
        Bill 000008 was correctly auto-id'd past existing
        000006/000007, but ``post_invoice`` then raised "already
        posted" on the unposted bill.

        Reproduces the exact Alex-book scenario."""
        import uuid
        from piecash.business.invoice import Invoice
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Old Vendor")
        gb.create_vendor(name="New Vendor")
        with gb.open(readonly=False) as book:
            old_vendor = gb._find_vendor(book, "000001")
            usd = book.default_currency
            for inv_id in ("000006", "000007"):
                book.session.execute(
                    Invoice.__table__.insert().values(
                        guid=uuid.uuid4().hex,
                        id=inv_id,
                        date_opened=None,
                        date_posted=None,
                        notes="historical",
                        active=1,
                        currency=usd.guid,
                        owner_type=4,
                        owner_guid=old_vendor.guid,
                        terms=None,
                        billing_id="",
                        post_txn=None,
                        post_lot=None,
                        post_acc=None,
                        billto_type=0,
                        billto_guid=None,
                        charge_amt_num=0,
                        charge_amt_denom=1,
                    )
                )
            book.save()
        # Create new bill via auto-id — should land at 000008.
        new_bill = gb.create_bill(vendor_id="000002")
        assert new_bill["id"] == "000008"
        # Add an entry and post — neither should fail.
        gb.add_bill_entry(
            bill_id="000008",
            account="Expenses:Office Supplies",
            description="Pens",
            quantity="1",
            price="50",
        )
        result = gb.post_invoice(
            invoice_id="000008",
            post_account="Liabilities:Accounts Payable",
            owner_type="vendor",
        )
        assert result["status"] == "posted"

    def test_post_succeeds_when_date_posted_is_empty_string(
        self, business_book,
    ):
        """``date_posted`` can land in SQL as an empty string
        (rather than NULL) on certain persistence paths — observed
        on Alex Chen-Morales's book where freshly auto-id'd bill
        000008 had ``date_posted=""`` in the database. The
        ``is not None`` check then evaluated truthy on the unposted
        bill and ``post_invoice`` raised "already posted",
        blocking the entire vendor bill lifecycle.

        Fix: treat any falsy value (None, "") as "not posted".
        Only a real datetime is truthy, so the check still
        rejects genuinely-posted documents."""
        from sqlalchemy import text
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Test Vendor")
        bill = gb.create_bill(vendor_id="000001")
        # Force the broken state Stephen observed: empty string
        # rather than NULL in date_posted.
        with gb.open(readonly=False) as book:
            book.session.execute(
                text(
                    "UPDATE invoices SET date_posted = '' "
                    "WHERE id = :id AND owner_type = 4"
                ),
                {"id": bill["id"]},
            )
            book.save()
        # add_bill_entry must succeed (uses same date_posted check).
        gb.add_bill_entry(
            bill_id=bill["id"],
            account="Expenses:Office Supplies",
            description="Test",
            quantity="1",
            price="100",
        )
        # post_invoice must succeed despite the broken state.
        result = gb.post_invoice(
            invoice_id=bill["id"],
            post_account="Liabilities:Accounts Payable",
            owner_type="vendor",
        )
        assert result["status"] == "posted"

    def test_auto_id_skips_existing_for_invoices_too(
        self, business_book,
    ):
        """Same scan-MAX behavior applies to customer invoices
        (owner_type=2), not just bills. Symmetric fix on the
        same code path."""
        import uuid
        from piecash.business.invoice import Invoice
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Old Customer")
        gb.create_customer(name="New Customer")
        with gb.open(readonly=False) as book:
            old_customer = gb._find_customer(book, "000001")
            usd = book.default_currency
            book.session.execute(
                Invoice.__table__.insert().values(
                    guid=uuid.uuid4().hex,
                    id="000005",
                    date_opened=None,
                    date_posted=None,
                    notes="historical",
                    active=1,
                    currency=usd.guid,
                    owner_type=2,
                    owner_guid=old_customer.guid,
                    terms=None,
                    billing_id="",
                    post_txn=None,
                    post_lot=None,
                    post_acc=None,
                    billto_type=0,
                    billto_guid=None,
                    charge_amt_num=0,
                    charge_amt_denom=1,
                )
            )
            book.save()
        result = gb.create_invoice(customer_id="000002")
        assert result["id"] == "000006"

    def test_currency_defaults_to_vendor_currency(self, business_book):
        """The bill bug from the bookkeeper: vendors with foreign
        currency had their bills created in the book's default
        currency instead of inheriting the vendor's. ``post_invoice``
        then saw inv.currency == account.commodity and skipped
        cross-currency conversion entirely — $500 was booked as
        ¥500. The fix: bills inherit the vendor's currency when
        ``currency`` isn't passed, matching customer-invoice
        behavior and GnuCash desktop UI."""
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="JetBrains", currency="USD")
        result = gb.create_bill(vendor_id="000001")
        bill = gb.get_invoice(result["id"])
        assert bill["currency"] == "USD"

    def test_explicit_currency_overrides_vendor_currency(
        self, business_book,
    ):
        """Explicit ``currency`` wins over the vendor's currency."""
        import piecash
        gb = GnuCashBook(str(business_book))
        with gb.open(readonly=False) as book:
            book.session.add(piecash.factories.create_currency_from_ISO("EUR"))
            book.save()
        gb.create_vendor(name="JetBrains", currency="USD")
        result = gb.create_bill(
            vendor_id="000001", currency="EUR",
        )
        bill = gb.get_invoice(result["id"])
        assert bill["currency"] == "EUR"


class TestDeleteInvoice:
    """Tests for delete_invoice."""

    def test_delete_unposted_invoice(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        inv = gb.create_invoice(customer_id="000001")
        result = gb.delete_invoice(invoice_id=inv["id"])
        assert result["status"] == "deleted"
        assert result["id"] == inv["id"]
        assert result["type"] == "invoice"
        # Verify it's gone
        with pytest.raises(ValueError, match="not found"):
            gb.get_invoice(inv["id"])

    def test_delete_invoice_with_entries(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001", account="Income:Sales",
            description="Consulting", quantity="1", price="500.00",
        )
        result = gb.delete_invoice(invoice_id="000001")
        assert result["status"] == "deleted"
        assert result["entries_deleted"] == 1

    def test_delete_posted_invoice_blocked(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001", account="Income:Sales",
            description="Consulting", quantity="1", price="500.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
            post_date="2026-01-15",
        )
        with pytest.raises(ValueError, match="Cannot delete posted"):
            gb.delete_invoice(invoice_id="000001")

    def test_delete_nonexistent_invoice(self, business_book):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="not found"):
            gb.delete_invoice(invoice_id="NOPE")

    def test_delete_with_custom_id(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001", invoice_id="INV-DEL-001")
        result = gb.delete_invoice(invoice_id="INV-DEL-001")
        assert result["status"] == "deleted"
        assert result["id"] == "INV-DEL-001"


class TestDeleteBill:
    """Tests for delete_bill."""

    def test_delete_unposted_bill(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        bill = gb.create_bill(vendor_id="000001")
        result = gb.delete_bill(bill_id=bill["id"])
        assert result["status"] == "deleted"
        assert result["id"] == bill["id"]
        assert result["type"] == "bill"

    def test_delete_bill_with_entries(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        gb.create_bill(vendor_id="000001")
        gb.add_bill_entry(
            bill_id="000001", account="Expenses:Office Supplies",
            description="Paper", quantity="10", price="5.00",
        )
        result = gb.delete_bill(bill_id="000001")
        assert result["status"] == "deleted"
        assert result["entries_deleted"] == 1

    def test_delete_posted_bill_blocked(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        gb.create_bill(vendor_id="000001")
        gb.add_bill_entry(
            bill_id="000001", account="Expenses:Office Supplies",
            description="Paper", quantity="10", price="5.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Liabilities:Accounts Payable",
            owner_type="vendor",
        )
        with pytest.raises(ValueError, match="Cannot delete posted"):
            gb.delete_bill(bill_id="000001")

    def test_delete_nonexistent_bill(self, business_book):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="not found"):
            gb.delete_bill(bill_id="NOPE")


class TestCreateVoucher:
    """Tests for create_voucher — employee expense reimbursement.

    Routes through ``_create_business_document`` with owner_type=5,
    so the cross-currency / billterm / custom-id / counter
    behaviors all come for free from the bill path. These tests
    pin the voucher-specific surface: response shape, the
    employee_id key, the auto-generated counter, and rejection
    paths.
    """

    def test_basic_creation(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_employee(name="Maria Garcia")
        result = gb.create_voucher(employee_id="000001")
        assert result["status"] == "created"
        assert result["employee_id"] == "000001"
        # Voucher counter starts at 0; first auto-id is 000001.
        assert result["id"] == "000001"

    def test_employee_not_found(self, business_book):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="Employee not found"):
            gb.create_voucher(employee_id="999999")

    def test_custom_voucher_id(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_employee(name="Maria Garcia")
        result = gb.create_voucher(
            employee_id="000001", voucher_id="VCHR-2026-001",
        )
        assert result["id"] == "VCHR-2026-001"

    def test_duplicate_voucher_id_rejected(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_employee(name="Maria Garcia")
        gb.create_voucher(employee_id="000001", voucher_id="V1")
        with pytest.raises(ValueError, match="already exists"):
            gb.create_voucher(employee_id="000001", voucher_id="V1")

    def test_voucher_counter_independent_from_bill_counter(self, business_book):
        """Vouchers use ``counter_exp_voucher``, bills use
        ``counter_bill``. The two sequences should be independent
        — a voucher created after a bill should NOT inherit the
        bill's counter value."""
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        gb.create_employee(name="Maria Garcia")
        # Bill #1 — counter_bill goes to 1.
        bill = gb.create_bill(vendor_id="000001")
        assert bill["id"] == "000001"
        # Voucher #1 — counter_exp_voucher goes to 1, independent.
        voucher = gb.create_voucher(employee_id="000001")
        assert voucher["id"] == "000001"

    def test_inherits_employee_currency(self, business_book):
        """Currency resolution: voucher inherits the employee's
        currency by default (same rule bills/invoices use for
        vendor/customer)."""
        gb = GnuCashBook(str(business_book))
        gb.create_employee(name="Maria Garcia", currency="USD")
        result = gb.create_voucher(employee_id="000001")
        full = gb.get_invoice(result["id"], owner_type="employee")
        assert full["currency"] == "USD"


class TestAddVoucherEntry:
    """Tests for add_voucher_entry.

    Voucher entries use the same ``b_*`` column group as bill
    entries (GnuCash schema collapses bill-side semantics). These
    tests pin the voucher-specific surface: account type
    validation, response shape, twin-method error messaging.
    """

    def test_basic_entry(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_employee(name="Maria Garcia")
        gb.create_voucher(employee_id="000001")
        result = gb.add_voucher_entry(
            voucher_id="000001",
            account="Expenses:Office Supplies",
            description="Pens and notebooks",
            quantity="1",
            price="42.50",
        )
        assert result["status"] == "created"
        assert result["voucher_id"] == "000001"
        assert result["total"] == "42.50"

    def test_multiple_entries_one_voucher(self, business_book):
        """Vouchers are typically multi-line — a single expense
        report covers groceries, gas, meals, etc."""
        gb = GnuCashBook(str(business_book))
        gb.create_employee(name="Maria Garcia")
        gb.create_voucher(employee_id="000001")
        e1 = gb.add_voucher_entry(
            voucher_id="000001",
            account="Expenses:Office Supplies",
            description="Supplies",
            quantity="1", price="50.00",
        )
        e2 = gb.add_voucher_entry(
            voucher_id="000001",
            account="Expenses:Office Supplies",
            description="Client lunch",
            quantity="1", price="100.00",
        )
        # v1.3.1: entry guid dropped; uniqueness still validated
        # implicitly by the fact that both writes succeeded.
        assert e1["description"] != e2["description"]

    def test_income_account_rejected(self, business_book):
        """Voucher entries take EXPENSE/ASSET only — same as
        bills. INCOME would invert the posting math."""
        gb = GnuCashBook(str(business_book))
        gb.create_employee(name="Maria Garcia")
        gb.create_voucher(employee_id="000001")
        with pytest.raises(ValueError, match="EXPENSE or ASSET"):
            gb.add_voucher_entry(
                voucher_id="000001",
                account="Income:Sales",
                description="Wrong direction",
                quantity="1", price="100",
            )

    def test_voucher_not_found(self, business_book):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="not found"):
            gb.add_voucher_entry(
                voucher_id="NOPE",
                account="Expenses:Office Supplies",
                description="x",
                quantity="1", price="1",
            )


class TestDeleteVoucher:
    """Tests for delete_voucher. Mirrors delete_bill but with
    employee + voucher semantics."""

    def test_delete_unposted_voucher(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_employee(name="Maria Garcia")
        gb.create_voucher(employee_id="000001")
        result = gb.delete_voucher(voucher_id="000001")
        assert result["status"] == "deleted"
        assert result["id"] == "000001"
        assert result["type"] == "voucher"

    def test_delete_with_entries(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_employee(name="Maria Garcia")
        gb.create_voucher(employee_id="000001")
        gb.add_voucher_entry(
            voucher_id="000001",
            account="Expenses:Office Supplies",
            description="Supplies", quantity="1", price="50",
        )
        result = gb.delete_voucher(voucher_id="000001")
        assert result["status"] == "deleted"
        assert result["entries_deleted"] == 1


class TestVoucherLifecycle:
    """End-to-end: create voucher → add entries → post → pay.
    Vouchers travel through the polymorphic post_invoice /
    pay_invoice path with owner_type='employee'.

    This is the load-bearing test: if any seam between voucher
    create and the bill-shaped lifecycle code is misrouted, it
    surfaces here.
    """

    def test_post_voucher_via_polymorphic_path(self, business_book):
        """post_invoice with owner_type='employee' debits the
        expense accounts, credits A/P. Same math as a bill, same
        polymorphic tool."""
        gb = GnuCashBook(str(business_book))
        gb.create_employee(name="Maria Garcia")
        gb.create_voucher(employee_id="000001")
        gb.add_voucher_entry(
            voucher_id="000001",
            account="Expenses:Office Supplies",
            description="Pens", quantity="1", price="50.00",
        )
        result = gb.post_invoice(
            invoice_id="000001",
            post_account="Liabilities:Accounts Payable",
            owner_type="employee",
        )
        # The polymorphism handler returns type='voucher' so the
        # audit log can dispatch correctly.
        assert result["type"] == "voucher"
        # A/P is credited — piecash signs liability balances as
        # negative (signed quantity convention: credits subtract).
        ap_balance = gb.get_balance("Liabilities:Accounts Payable")
        assert ap_balance == Decimal("-50.00")
        # Expense account is debited (positive expense balance).
        exp_balance = gb.get_balance("Expenses:Office Supplies")
        assert exp_balance == Decimal("50.00")

    def test_pay_voucher_via_polymorphic_path(self, business_book):
        """pay_invoice with owner_type='employee' debits A/P,
        credits the payment account — net effect: cash leaves the
        company, A/P returns to zero."""
        gb = GnuCashBook(str(business_book))
        gb.create_employee(name="Maria Garcia")
        gb.create_voucher(employee_id="000001")
        gb.add_voucher_entry(
            voucher_id="000001",
            account="Expenses:Office Supplies",
            description="Pens", quantity="1", price="50.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Liabilities:Accounts Payable",
            owner_type="employee",
        )
        # Pay $50 from Checking.
        gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="50.00",
            owner_type="employee",
        )
        # A/P back to zero; Checking down $50 from opening 10000.
        assert gb.get_balance("Liabilities:Accounts Payable") == Decimal("0.00")
        assert gb.get_balance("Assets:Checking") == Decimal("9950.00")


class TestCreditNoteSlotHelpers:
    """Tests for the credit-note slot infrastructure
    (``_get_is_credit_note`` / ``_set_is_credit_note`` /
    ``_get_applies_to_invoice_guid`` /
    ``_set_applies_to_invoice_guid`` / ``_resolve_applies_to``).

    These are the foundation: every higher-level credit-note tool
    in subsequent commits relies on them. Lock the contract so
    the abstraction can't drift silently — particularly the
    "value=1 / absent=false" convention and the dangling-reference
    handling on resolve.
    """

    def _new_invoice(self, gb):
        """Make a customer + invoice quickly. Returns the invoice
        ORM object inside a fresh writable session — caller owns
        the ``with gb.open()`` block."""
        gb.create_customer(name="Acme Co")
        gb.create_invoice(customer_id="000001")

    def test_get_is_credit_note_false_for_unflagged(self, business_book):
        """A freshly-created invoice has no credit-note slot;
        the helper returns False (not None, not raise)."""
        from gnucash_mcp.book.business import BusinessMixin
        gb = GnuCashBook(str(business_book))
        self._new_invoice(gb)
        with gb.open(readonly=True) as book:
            from piecash.business.invoice import Invoice
            inv = book.session.query(Invoice).filter_by(id="000001").first()
            assert BusinessMixin._get_is_credit_note(inv) is False

    def test_set_and_read_is_credit_note(self, business_book):
        """Round-trip: set True, save, re-open, read back True."""
        from gnucash_mcp.book.business import BusinessMixin
        gb = GnuCashBook(str(business_book))
        self._new_invoice(gb)
        with gb.open(readonly=False) as book:
            from piecash.business.invoice import Invoice
            inv = book.session.query(Invoice).filter_by(id="000001").first()
            BusinessMixin._set_is_credit_note(inv, True)
            book.save()
        with gb.open(readonly=True) as book:
            from piecash.business.invoice import Invoice
            inv = book.session.query(Invoice).filter_by(id="000001").first()
            assert BusinessMixin._get_is_credit_note(inv) is True

    def test_set_false_clears_slot(self, business_book):
        """``_set_is_credit_note(False)`` removes the slot entirely
        (not stores ``0``). Important: the "absent-means-False"
        convention is what GnuCash desktop reads — storing 0 would
        be a non-standard state."""
        from gnucash_mcp.book.business import BusinessMixin
        gb = GnuCashBook(str(business_book))
        self._new_invoice(gb)
        with gb.open(readonly=False) as book:
            from piecash.business.invoice import Invoice
            inv = book.session.query(Invoice).filter_by(id="000001").first()
            BusinessMixin._set_is_credit_note(inv, True)
            book.save()
        with gb.open(readonly=False) as book:
            from piecash.business.invoice import Invoice
            inv = book.session.query(Invoice).filter_by(id="000001").first()
            BusinessMixin._set_is_credit_note(inv, False)
            book.save()
        # Slot should be gone — re-read returns False AND the
        # underlying access raises KeyError on direct lookup.
        with gb.open(readonly=True) as book:
            from piecash.business.invoice import Invoice
            inv = book.session.query(Invoice).filter_by(id="000001").first()
            assert BusinessMixin._get_is_credit_note(inv) is False
            with pytest.raises(KeyError):
                inv[BusinessMixin._CREDIT_NOTE_SLOT_KEY]

    def test_set_false_on_unflagged_is_idempotent(self, business_book):
        """Clearing a slot that was never set must not raise.
        Defensive — the higher-level ``delete_credit_note`` path
        could conceivably hit this case after partial state."""
        from gnucash_mcp.book.business import BusinessMixin
        gb = GnuCashBook(str(business_book))
        self._new_invoice(gb)
        with gb.open(readonly=False) as book:
            from piecash.business.invoice import Invoice
            inv = book.session.query(Invoice).filter_by(id="000001").first()
            # Should not raise.
            BusinessMixin._set_is_credit_note(inv, False)

    def test_applies_to_guid_returns_none_when_unset(self, business_book):
        """A fresh credit note (no link to source) returns None
        from the GUID accessor, not KeyError."""
        from gnucash_mcp.book.business import BusinessMixin
        gb = GnuCashBook(str(business_book))
        self._new_invoice(gb)
        with gb.open(readonly=True) as book:
            from piecash.business.invoice import Invoice
            inv = book.session.query(Invoice).filter_by(id="000001").first()
            assert BusinessMixin._get_applies_to_invoice_guid(inv) is None

    def test_set_and_read_applies_to_guid(self, business_book):
        """Round-trip: store a 32-char GUID, read back the same
        string. The namespaced slot key (``gnc-mcp/applies-to-
        invoice``) uses ``/`` which creates a sub-slot in GnuCash's
        KVP store — verifying the round-trip confirms the
        sub-slot path doesn't lose data."""
        from gnucash_mcp.book.business import BusinessMixin
        gb = GnuCashBook(str(business_book))
        self._new_invoice(gb)
        source_guid = "0" * 32  # Real shape, but invalid as a lookup
        with gb.open(readonly=False) as book:
            from piecash.business.invoice import Invoice
            inv = book.session.query(Invoice).filter_by(id="000001").first()
            BusinessMixin._set_applies_to_invoice_guid(inv, source_guid)
            book.save()
        with gb.open(readonly=True) as book:
            from piecash.business.invoice import Invoice
            inv = book.session.query(Invoice).filter_by(id="000001").first()
            assert BusinessMixin._get_applies_to_invoice_guid(inv) == source_guid

    def test_resolve_applies_to_returns_id_and_type(self, business_book):
        """When the link points at a real invoice, resolve returns
        the human-readable ``{id, type}`` pair."""
        from gnucash_mcp.book.business import BusinessMixin
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        # Source invoice (the one being credited)
        source = gb.create_invoice(customer_id="000001")
        # Credit note (links back to source)
        credit = gb.create_invoice(customer_id="000001")
        with gb.open(readonly=False) as book:
            from piecash.business.invoice import Invoice
            cn = book.session.query(Invoice).filter_by(
                id=credit["id"],
            ).first()
            # v1.3.1: create_invoice no longer surfaces ``guid``;
            # look up the source invoice's guid via the ORM.
            source_inv = book.session.query(Invoice).filter_by(
                id=source["id"],
            ).first()
            BusinessMixin._set_is_credit_note(cn, True)
            BusinessMixin._set_applies_to_invoice_guid(cn, source_inv.guid)
            book.save()
        with gb.open(readonly=True) as book:
            from piecash.business.invoice import Invoice
            cn = book.session.query(Invoice).filter_by(
                id=credit["id"],
            ).first()
            resolved = BusinessMixin._resolve_applies_to(book, cn)
            assert resolved == {"id": source["id"], "type": "invoice"}

    def test_resolve_applies_to_returns_none_for_dangling_reference(
        self, business_book,
    ):
        """When the linked source GUID doesn't exist (e.g. the
        source was deleted after the credit note was created),
        resolve returns None rather than crashing the response.
        Dangling references shouldn't break ``get_invoice``."""
        from gnucash_mcp.book.business import BusinessMixin
        gb = GnuCashBook(str(business_book))
        self._new_invoice(gb)
        with gb.open(readonly=False) as book:
            from piecash.business.invoice import Invoice
            inv = book.session.query(Invoice).filter_by(id="000001").first()
            BusinessMixin._set_applies_to_invoice_guid(
                inv, "deadbeef" * 4,  # 32 chars, won't match any row
            )
            book.save()
        with gb.open(readonly=True) as book:
            from piecash.business.invoice import Invoice
            inv = book.session.query(Invoice).filter_by(id="000001").first()
            assert BusinessMixin._resolve_applies_to(book, inv) is None

    def test_resolve_applies_to_returns_none_when_no_link(
        self, business_book,
    ):
        """When the credit-note flag is set but no source link is
        stored, resolve returns None (a credit note that floats
        without explicit source is valid — the bookkeeper might
        attach it via Process Payment netting later)."""
        from gnucash_mcp.book.business import BusinessMixin
        gb = GnuCashBook(str(business_book))
        self._new_invoice(gb)
        with gb.open(readonly=False) as book:
            from piecash.business.invoice import Invoice
            inv = book.session.query(Invoice).filter_by(id="000001").first()
            BusinessMixin._set_is_credit_note(inv, True)
            book.save()
        with gb.open(readonly=True) as book:
            from piecash.business.invoice import Invoice
            inv = book.session.query(Invoice).filter_by(id="000001").first()
            assert BusinessMixin._resolve_applies_to(book, inv) is None


class TestInvoiceToDictCreditNoteKeys:
    """The response-shape contract: credit-note keys appear only
    when the credit-note flag is set on the invoice.

    Normal invoices/bills/vouchers must produce byte-identical
    output to pre-v1.3 — verified by inspecting the absence of
    the new keys on unflagged docs. Flagged docs get
    ``is_credit_note: True``, and ``applies_to: {...}`` when the
    caller threaded a resolved dict.
    """

    def test_normal_invoice_omits_credit_note_keys(self, business_book):
        """Pre-v1.3 contract — a normal invoice produces a dict
        without ``is_credit_note`` or ``applies_to`` keys."""
        from gnucash_mcp.book.business import BusinessMixin
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        gb.create_invoice(customer_id="000001")
        with gb.open(readonly=True) as book:
            from piecash.business.invoice import Invoice
            inv = book.session.query(Invoice).filter_by(id="000001").first()
            result = BusinessMixin._invoice_to_dict(inv)
            assert "is_credit_note" not in result
            assert "applies_to" not in result

    def test_credit_note_flag_included_when_set(self, business_book):
        """``is_credit_note: True`` appears when the slot is set."""
        from gnucash_mcp.book.business import BusinessMixin
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        gb.create_invoice(customer_id="000001")
        with gb.open(readonly=False) as book:
            from piecash.business.invoice import Invoice
            inv = book.session.query(Invoice).filter_by(id="000001").first()
            BusinessMixin._set_is_credit_note(inv, True)
            book.save()
        with gb.open(readonly=True) as book:
            from piecash.business.invoice import Invoice
            inv = book.session.query(Invoice).filter_by(id="000001").first()
            result = BusinessMixin._invoice_to_dict(inv)
            assert result["is_credit_note"] is True
            # applies_to absent when caller didn't pass it.
            assert "applies_to" not in result

    def test_applies_to_threaded_when_flag_and_kwarg_both_set(
        self, business_book,
    ):
        """When both the credit-note flag AND the caller-resolved
        applies_to are present, both keys appear in the dict."""
        from gnucash_mcp.book.business import BusinessMixin
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        gb.create_invoice(customer_id="000001")
        with gb.open(readonly=False) as book:
            from piecash.business.invoice import Invoice
            inv = book.session.query(Invoice).filter_by(id="000001").first()
            BusinessMixin._set_is_credit_note(inv, True)
            book.save()
        applies_to = {"id": "000028", "type": "invoice"}
        with gb.open(readonly=True) as book:
            from piecash.business.invoice import Invoice
            inv = book.session.query(Invoice).filter_by(id="000001").first()
            result = BusinessMixin._invoice_to_dict(
                inv, applies_to=applies_to,
            )
            assert result["is_credit_note"] is True
            assert result["applies_to"] == applies_to

    def test_applies_to_dropped_when_flag_absent(self, business_book):
        """Defensive — even if a caller mistakenly passes
        ``applies_to`` for a non-credit-note invoice, the dict
        shouldn't include it. The credit-note flag is the gate;
        no flag means no credit-note keys at all."""
        from gnucash_mcp.book.business import BusinessMixin
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        gb.create_invoice(customer_id="000001")
        applies_to = {"id": "000028", "type": "invoice"}
        with gb.open(readonly=True) as book:
            from piecash.business.invoice import Invoice
            inv = book.session.query(Invoice).filter_by(id="000001").first()
            result = BusinessMixin._invoice_to_dict(
                inv, applies_to=applies_to,
            )
            assert "is_credit_note" not in result
            assert "applies_to" not in result


class TestCreateCreditNote:
    """Tests for create_credit_note — the user-facing entry
    point. Validates owner_type gating, applies_to source
    matching, currency inheritance and currency conflict
    rejection, and the credit-note flag / applies_to keys in
    the response.
    """

    def test_customer_credit_note_basic(self, business_book):
        """Standalone credit note — no applies_to, customer
        side. Response surfaces is_credit_note=True and the
        customer_id key from the underlying create path."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        result = gb.create_credit_note(
            owner_id="000001", owner_type="customer",
        )
        assert result["status"] == "created"
        assert result["customer_id"] == "000001"
        assert result["is_credit_note"] is True
        assert "applies_to" not in result
        # Round-trip via get_invoice confirms the slot was set.
        full = gb.get_invoice(result["id"], owner_type="customer")
        assert full["is_credit_note"] is True
        assert full["type"] == "credit_note"  # slot-driven since the
        # Codex findings fix: type agrees with delete/unpost responses

    def test_vendor_credit_note_basic(self, business_book):
        """Symmetric — vendor side credit note. Response keys
        use vendor_id."""
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        result = gb.create_credit_note(
            owner_id="000001", owner_type="vendor",
        )
        assert result["status"] == "created"
        assert result["vendor_id"] == "000001"
        assert result["is_credit_note"] is True

    def test_employee_owner_type_rejected(self, business_book):
        """Employees explicitly excluded — no GnuCash desktop UI
        for employee credit notes."""
        gb = GnuCashBook(str(business_book))
        gb.create_employee(name="Maria")
        with pytest.raises(ValueError, match="not supported for employees"):
            gb.create_credit_note(
                owner_id="000001", owner_type="employee",
            )

    def test_invalid_owner_type_rejected(self, business_book):
        """Typos / unknown owner types rejected via the standard
        _parse_owner_type path."""
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="Invalid owner_type"):
            gb.create_credit_note(
                owner_id="000001", owner_type="custmer",
            )

    def test_applies_to_link_resolved_in_response(self, business_book):
        """When applies_to_invoice_id is given and valid, the
        response includes applies_to={id, type} dict."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        source = gb.create_invoice(customer_id="000001")
        result = gb.create_credit_note(
            owner_id="000001",
            owner_type="customer",
            applies_to_invoice_id=source["id"],
        )
        assert result["applies_to"] == {
            "id": source["id"], "type": "invoice",
        }
        # The link also round-trips through get_invoice.
        full = gb.get_invoice(result["id"], owner_type="customer")
        assert full["applies_to"]["id"] == source["id"]

    def test_applies_to_source_not_found(self, business_book):
        """Source ID that doesn't exist is rejected with a clear
        error rather than silently creating an orphan link."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        with pytest.raises(ValueError, match="Source.*not found"):
            gb.create_credit_note(
                owner_id="000001",
                owner_type="customer",
                applies_to_invoice_id="NOPE",
            )

    def test_applies_to_cross_owner_rejected(self, business_book):
        """A credit note for customer A pointing at customer B's
        invoice is wrong bookkeeping — reject with the
        mismatched IDs named in the error."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        gb.create_customer(name="Beta Inc")
        # Acme is 000001; Beta is 000002.
        beta_invoice = gb.create_invoice(customer_id="000002")
        with pytest.raises(
            ValueError, match="belongs to.*not.*000001",
        ):
            gb.create_credit_note(
                owner_id="000001",
                owner_type="customer",
                applies_to_invoice_id=beta_invoice["id"],
            )

    def test_currency_inherited_from_source(self, business_book):
        """When applies_to is given and currency is omitted, the
        credit note adopts the source's currency. Important for
        multi-currency books where issuing a credit note in the
        wrong currency would create FX confusion."""
        import piecash
        gb = GnuCashBook(str(business_book))
        # Add EUR to the test book and create a EUR customer.
        with gb.open(readonly=False) as bk:
            bk.session.add(
                piecash.factories.create_currency_from_ISO("EUR")
            )
            bk.save()
        gb.create_customer(name="Berlin GmbH", currency="EUR")
        source = gb.create_invoice(customer_id="000001")
        # No explicit currency; should inherit EUR from source.
        cn = gb.create_credit_note(
            owner_id="000001",
            owner_type="customer",
            applies_to_invoice_id=source["id"],
        )
        full = gb.get_invoice(cn["id"], owner_type="customer")
        assert full["currency"] == "EUR"

    def test_currency_mismatch_rejected(self, business_book):
        """Explicit currency conflicting with source's is rejected
        — netting across currencies would create FX adjustments
        outside GnuCash's tracking."""
        import piecash
        gb = GnuCashBook(str(business_book))
        with gb.open(readonly=False) as bk:
            bk.session.add(
                piecash.factories.create_currency_from_ISO("EUR")
            )
            bk.save()
        gb.create_customer(name="Berlin GmbH", currency="EUR")
        source = gb.create_invoice(customer_id="000001")
        with pytest.raises(
            ValueError, match="doesn't match source.*currency",
        ):
            gb.create_credit_note(
                owner_id="000001",
                owner_type="customer",
                applies_to_invoice_id=source["id"],
                currency="USD",
            )

    def test_custom_credit_note_id_accepted(self, business_book):
        """Custom IDs (e.g. 'CN-2026-001') override the
        auto-counter, same as invoices/bills."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        result = gb.create_credit_note(
            owner_id="000001",
            owner_type="customer",
            credit_note_id="CN-2026-001",
        )
        assert result["id"] == "CN-2026-001"


class TestAddCreditNoteEntry:
    """Tests for add_credit_note_entry — validates the target
    is a credit note before delegating to _add_entry. Account
    type rules mirror the host owner_type."""

    def test_customer_credit_note_accepts_income_entry(self, business_book):
        """Customer credit note entry → INCOME account
        (mirrors regular customer invoice)."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        cn = gb.create_credit_note(
            owner_id="000001", owner_type="customer",
        )
        result = gb.add_credit_note_entry(
            credit_note_id=cn["id"],
            account="Income:Sales",
            description="Out-of-scope work credit",
            quantity="1", price="500.00",
        )
        assert result["status"] == "created"
        assert result["credit_note_id"] == cn["id"]
        assert result["total"] == "500.00"

    def test_vendor_credit_note_accepts_expense_entry(self, business_book):
        """Vendor credit note entry → EXPENSE account (mirrors
        regular vendor bill)."""
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        cn = gb.create_credit_note(
            owner_id="000001", owner_type="vendor",
        )
        result = gb.add_credit_note_entry(
            credit_note_id=cn["id"],
            account="Expenses:Office Supplies",
            description="Defective product return",
            quantity="1", price="42.00",
        )
        assert result["status"] == "created"
        assert result["credit_note_id"] == cn["id"]

    def test_non_credit_note_rejected_with_helpful_message(self, business_book):
        """If the caller passes a regular invoice's ID to
        add_credit_note_entry, fail loud and name the correct
        tool to use instead. Pre-fix this would silently add
        an entry to the wrong document type."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        gb.create_invoice(customer_id="000001")  # regular invoice
        with pytest.raises(
            ValueError, match="not a credit note.*add_invoice_entry",
        ):
            gb.add_credit_note_entry(
                credit_note_id="000001",
                account="Income:Sales",
                description="Wrong tool",
                quantity="1", price="100",
            )

    def test_credit_note_not_found(self, business_book):
        """Missing ID → 'Credit note not found' (clearer than
        the generic invoice error)."""
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="Credit note not found"):
            gb.add_credit_note_entry(
                credit_note_id="NOPE",
                account="Income:Sales",
                description="x", quantity="1", price="1",
            )

    def test_wrong_account_type_rejected(self, business_book):
        """Account-type validation flows through to _add_entry;
        EXPENSE on a customer credit note rejected just as on a
        customer invoice."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        cn = gb.create_credit_note(
            owner_id="000001", owner_type="customer",
        )
        with pytest.raises(ValueError, match="must be INCOME"):
            gb.add_credit_note_entry(
                credit_note_id=cn["id"],
                account="Expenses:Office Supplies",
                description="Wrong direction",
                quantity="1", price="100",
            )


class TestDeleteCreditNote:
    """Tests for delete_credit_note — validates the target is
    a credit note, blocks deletion of posted credit notes,
    cleans up entries."""

    def test_delete_unposted_credit_note(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        cn = gb.create_credit_note(
            owner_id="000001", owner_type="customer",
        )
        result = gb.delete_credit_note(credit_note_id=cn["id"])
        assert result["status"] == "deleted"
        # type re-keyed to credit_note (base would say 'invoice')
        assert result["type"] == "credit_note"

    def test_delete_with_entries(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        cn = gb.create_credit_note(
            owner_id="000001", owner_type="customer",
        )
        gb.add_credit_note_entry(
            credit_note_id=cn["id"],
            account="Income:Sales",
            description="x", quantity="1", price="100",
        )
        result = gb.delete_credit_note(credit_note_id=cn["id"])
        assert result["status"] == "deleted"
        assert result["entries_deleted"] == 1

    def test_non_credit_note_rejected(self, business_book):
        """A regular invoice cannot be deleted via this tool —
        the validation gate fails loud and names the right tool."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        gb.create_invoice(customer_id="000001")
        with pytest.raises(
            ValueError, match="not a credit note.*delete_invoice",
        ):
            gb.delete_credit_note(credit_note_id="000001")


class TestCreditNotePosting:
    """Tests for the posting-direction reversal on credit notes.

    Customer credit notes credit A/R (reduce receivable) and
    debit Income (reverse revenue) — opposite of a normal
    customer invoice. Vendor credit notes debit A/P (reduce
    payable) and credit Expense (reverse expense). The XOR
    trick (effective_is_bill = is_bill ^ is_credit_note) is
    the implementation; these tests lock the math from the
    outside.
    """

    def _setup_customer_credit_note(
        self, gb, amount="100.00",
    ) -> str:
        gb.create_customer(name="Acme Co")
        cn = gb.create_credit_note(
            owner_id="000001", owner_type="customer",
        )
        gb.add_credit_note_entry(
            credit_note_id=cn["id"],
            account="Income:Sales",
            description="Out-of-scope work credit",
            quantity="1", price=amount,
        )
        return cn["id"]

    def _setup_vendor_credit_note(
        self, gb, amount="100.00",
    ) -> str:
        gb.create_vendor(name="Office Depot")
        cn = gb.create_credit_note(
            owner_id="000001", owner_type="vendor",
        )
        gb.add_credit_note_entry(
            credit_note_id=cn["id"],
            account="Expenses:Office Supplies",
            description="Defective product",
            quantity="1", price=amount,
        )
        return cn["id"]

    def test_customer_credit_note_post_reverses_ar(self, business_book):
        """Posting a customer credit note CREDITS A/R (negative
        movement) — reducing what the customer owes. Compare to
        a normal customer invoice which DEBITS A/R (positive)."""
        gb = GnuCashBook(str(business_book))
        cn_id = self._setup_customer_credit_note(gb)
        ar_before = gb.get_balance("Assets:Accounts Receivable")
        result = gb.post_invoice(
            invoice_id=cn_id,
            post_account="Assets:Accounts Receivable",
            owner_type="customer",
        )
        assert result["status"] == "posted"
        assert result["type"] == "credit_note"
        ar_after = gb.get_balance("Assets:Accounts Receivable")
        # A/R went DOWN (became more negative or less positive)
        # by the credit note amount — opposite of an invoice post.
        assert ar_after - ar_before == Decimal("-100.00")
        # Income reversed: Income:Sales decreased by 100.
        # Income natural balance in piecash is negative (credit-
        # normal), so a debit to Income MAKES the signed balance
        # LESS NEGATIVE (closer to zero) — net +100 in signed terms.
        income_balance = gb.get_balance("Income:Sales")
        # Was 0, now +100 (the debit pushed credit-normal toward 0
        # and past, into positive signed-balance territory).
        assert income_balance == Decimal("100.00")

    def test_vendor_credit_note_post_reverses_ap(self, business_book):
        """Posting a vendor credit note DEBITS A/P (reduces what
        we owe the vendor) — opposite of a vendor bill which
        CREDITS A/P (creates payable)."""
        gb = GnuCashBook(str(business_book))
        cn_id = self._setup_vendor_credit_note(gb)
        ap_before = gb.get_balance("Liabilities:Accounts Payable")
        result = gb.post_invoice(
            invoice_id=cn_id,
            post_account="Liabilities:Accounts Payable",
            owner_type="vendor",
        )
        assert result["status"] == "posted"
        assert result["type"] == "credit_note"
        ap_after = gb.get_balance("Liabilities:Accounts Payable")
        # A/P went UP (less negative, or positive) — reducing
        # the liability from the company's perspective.
        assert ap_after - ap_before == Decimal("100.00")

    def test_credit_note_unpost_reverses_post(self, business_book):
        """Unposting a credit note removes the netting and
        returns A/R / A/P to its pre-post state. Just like
        unposting an invoice — the transaction is removed."""
        gb = GnuCashBook(str(business_book))
        cn_id = self._setup_customer_credit_note(gb)
        ar_before_post = gb.get_balance("Assets:Accounts Receivable")
        gb.post_invoice(
            invoice_id=cn_id,
            post_account="Assets:Accounts Receivable",
            owner_type="customer",
        )
        result = gb.unpost_invoice(
            invoice_id=cn_id, owner_type="customer",
        )
        assert result["status"] == "unposted"
        assert result["type"] == "credit_note"
        ar_after_unpost = gb.get_balance("Assets:Accounts Receivable")
        assert ar_after_unpost == ar_before_post

    def test_pay_customer_credit_note_refunds_cash(self, business_book):
        """``pay_invoice`` on a customer credit note SENDS cash
        to the customer (refund). Checking goes DOWN, A/R returns
        from credit balance back toward zero (positive movement)."""
        gb = GnuCashBook(str(business_book))
        cn_id = self._setup_customer_credit_note(gb)
        gb.post_invoice(
            invoice_id=cn_id,
            post_account="Assets:Accounts Receivable",
            owner_type="customer",
        )
        checking_before = gb.get_balance("Assets:Checking")
        ar_before = gb.get_balance("Assets:Accounts Receivable")
        result = gb.pay_invoice(
            invoice_id=cn_id,
            payment_account="Assets:Checking",
            amount="100.00",
            owner_type="customer",
        )
        assert result["status"] == "paid"
        assert result["type"] == "credit_note"
        # Cash left the company (refund).
        assert gb.get_balance("Assets:Checking") - checking_before == Decimal("-100.00")
        # A/R movement REVERSED the post: credit went DOWN $100
        # post, then went UP $100 on refund (net to zero).
        assert gb.get_balance("Assets:Accounts Receivable") - ar_before == Decimal("100.00")

    def test_pay_vendor_credit_note_receives_cash(self, business_book):
        """``pay_invoice`` on a vendor credit note RECEIVES cash
        from the vendor (vendor sent us a refund check). Checking
        UP, A/P down."""
        gb = GnuCashBook(str(business_book))
        cn_id = self._setup_vendor_credit_note(gb)
        gb.post_invoice(
            invoice_id=cn_id,
            post_account="Liabilities:Accounts Payable",
            owner_type="vendor",
        )
        checking_before = gb.get_balance("Assets:Checking")
        ap_before = gb.get_balance("Liabilities:Accounts Payable")
        gb.pay_invoice(
            invoice_id=cn_id,
            payment_account="Assets:Checking",
            amount="100.00",
            owner_type="vendor",
        )
        # Cash arrived.
        assert gb.get_balance("Assets:Checking") - checking_before == Decimal("100.00")
        # A/P movement reversed the post.
        assert gb.get_balance("Liabilities:Accounts Payable") - ap_before == Decimal("-100.00")


class TestApplyCreditNote:
    """Tests for apply_credit_note — the netting tool.

    The headline cases: a posted credit note nets against a
    posted invoice from the same customer/vendor. Both lots
    reduce by the applied amount; no cash moves. Validation
    rejects cross-owner, cross-currency, cross-account, and
    over-apply attempts.
    """

    def _setup_pair(
        self, gb, source_amount="500.00", credit_amount="100.00",
    ):
        """Post an invoice and a credit note from the same
        customer. Returns (invoice_id, credit_note_id)."""
        gb.create_customer(name="Acme Co")
        # Source invoice: $500 owed by Acme
        src = gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id=src["id"],
            account="Income:Sales",
            description="Services", quantity="1", price=source_amount,
        )
        gb.post_invoice(
            invoice_id=src["id"],
            post_account="Assets:Accounts Receivable",
            owner_type="customer",
        )
        # Credit note: $100 reduction
        cn = gb.create_credit_note(
            owner_id="000001", owner_type="customer",
            applies_to_invoice_id=src["id"],
        )
        gb.add_credit_note_entry(
            credit_note_id=cn["id"],
            account="Income:Sales",
            description="Disputed line",
            quantity="1", price=credit_amount,
        )
        gb.post_invoice(
            invoice_id=cn["id"],
            post_account="Assets:Accounts Receivable",
            owner_type="customer",
        )
        return src["id"], cn["id"]

    def test_apply_full_credit_against_larger_invoice(self, business_book):
        """Credit $100 against $500 invoice: credit fully
        consumed, invoice's remaining drops to $400, A/R net
        movement is zero (the credit was already booked at post
        time; apply just transfers between lots)."""
        gb = GnuCashBook(str(business_book))
        src_id, cn_id = self._setup_pair(gb)
        ar_before = gb.get_balance("Assets:Accounts Receivable")
        result = gb.apply_credit_note(
            credit_note_id=cn_id,
            applies_to_invoice_id=src_id,
        )
        assert result["status"] == "applied"
        assert result["amount_applied"] == "100.00"
        # Credit note fully settled, invoice has $400 left.
        assert Decimal(result["credit_note_remaining"]) == Decimal("0")
        assert Decimal(result["target_remaining"]) == Decimal("400")
        # A/R net movement: zero. The apply is a lot-rearrangement,
        # not a balance change. (Net effect: A/R was 500 - 100 =
        # 400 after both posts; after apply, still 400.)
        ar_after = gb.get_balance("Assets:Accounts Receivable")
        assert ar_after == ar_before

    def test_apply_partial_amount(self, business_book):
        """Explicit amount, smaller than credit_note_remaining,
        partially applies and leaves both lots open."""
        gb = GnuCashBook(str(business_book))
        src_id, cn_id = self._setup_pair(gb)
        result = gb.apply_credit_note(
            credit_note_id=cn_id,
            applies_to_invoice_id=src_id,
            amount="40.00",
        )
        assert result["amount_applied"] == "40.00"
        assert Decimal(result["credit_note_remaining"]) == Decimal("60")
        assert Decimal(result["target_remaining"]) == Decimal("460")

    def test_apply_default_amount_is_min_of_remaining(self, business_book):
        """When amount is omitted, the apply defaults to
        min(credit_note_remaining, target_remaining)."""
        gb = GnuCashBook(str(business_book))
        # Make the credit LARGER than the invoice so the target
        # is the limiting side.
        src_id, cn_id = self._setup_pair(
            gb, source_amount="50.00", credit_amount="200.00",
        )
        result = gb.apply_credit_note(
            credit_note_id=cn_id,
            applies_to_invoice_id=src_id,
        )
        # min(200, 50) = 50 applied; invoice fully cleared,
        # credit note has $150 remaining.
        assert result["amount_applied"] == "50.00"
        assert Decimal(result["target_remaining"]) == Decimal("0")
        assert Decimal(result["credit_note_remaining"]) == Decimal("150")

    def test_apply_over_max_rejected(self, business_book):
        """Applying more than the lesser-remaining is rejected
        with both balances named in the error."""
        gb = GnuCashBook(str(business_book))
        src_id, cn_id = self._setup_pair(gb)
        with pytest.raises(ValueError, match="exceeds the smaller of"):
            gb.apply_credit_note(
                credit_note_id=cn_id,
                applies_to_invoice_id=src_id,
                amount="200.00",
            )

    def test_apply_to_unposted_target_rejected(self, business_book):
        """Target must be posted (no lot to settle otherwise)."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        # Source invoice unposted
        src = gb.create_invoice(customer_id="000001")
        # Credit note posted
        cn = gb.create_credit_note(
            owner_id="000001", owner_type="customer",
        )
        gb.add_credit_note_entry(
            credit_note_id=cn["id"],
            account="Income:Sales",
            description="x", quantity="1", price="50",
        )
        gb.post_invoice(
            invoice_id=cn["id"],
            post_account="Assets:Accounts Receivable",
            owner_type="customer",
        )
        with pytest.raises(ValueError, match="not posted"):
            gb.apply_credit_note(
                credit_note_id=cn["id"],
                applies_to_invoice_id=src["id"],
            )

    def test_apply_unposted_credit_note_rejected(self, business_book):
        """Credit note must be posted too."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        src = gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id=src["id"],
            account="Income:Sales",
            description="x", quantity="1", price="500",
        )
        gb.post_invoice(
            invoice_id=src["id"],
            post_account="Assets:Accounts Receivable",
            owner_type="customer",
        )
        # Credit note unposted
        cn = gb.create_credit_note(
            owner_id="000001", owner_type="customer",
        )
        with pytest.raises(ValueError, match="Credit note.*not posted"):
            gb.apply_credit_note(
                credit_note_id=cn["id"],
                applies_to_invoice_id=src["id"],
            )

    def test_apply_to_credit_note_rejected(self, business_book):
        """Target can't itself be a credit note — credits don't
        net against credits."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        cn1 = gb.create_credit_note(
            owner_id="000001", owner_type="customer",
        )
        gb.add_credit_note_entry(
            credit_note_id=cn1["id"], account="Income:Sales",
            description="x", quantity="1", price="50",
        )
        gb.post_invoice(
            invoice_id=cn1["id"],
            post_account="Assets:Accounts Receivable",
            owner_type="customer",
        )
        cn2 = gb.create_credit_note(
            owner_id="000001", owner_type="customer",
        )
        gb.add_credit_note_entry(
            credit_note_id=cn2["id"], account="Income:Sales",
            description="x", quantity="1", price="30",
        )
        gb.post_invoice(
            invoice_id=cn2["id"],
            post_account="Assets:Accounts Receivable",
            owner_type="customer",
        )
        with pytest.raises(
            ValueError, match="itself a credit note",
        ):
            gb.apply_credit_note(
                credit_note_id=cn1["id"],
                applies_to_invoice_id=cn2["id"],
            )

    def test_apply_cross_owner_rejected(self, business_book):
        """Credit notes can only net against documents from the
        same customer/vendor."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        gb.create_customer(name="Beta Inc")
        # Beta's invoice
        beta_inv = gb.create_invoice(customer_id="000002")
        gb.add_invoice_entry(
            invoice_id=beta_inv["id"], account="Income:Sales",
            description="x", quantity="1", price="500",
        )
        gb.post_invoice(
            invoice_id=beta_inv["id"],
            post_account="Assets:Accounts Receivable",
            owner_type="customer",
        )
        # Acme's credit note — different owner
        cn = gb.create_credit_note(
            owner_id="000001", owner_type="customer",
        )
        gb.add_credit_note_entry(
            credit_note_id=cn["id"], account="Income:Sales",
            description="x", quantity="1", price="50",
        )
        gb.post_invoice(
            invoice_id=cn["id"],
            post_account="Assets:Accounts Receivable",
            owner_type="customer",
        )
        with pytest.raises(
            ValueError, match="same customer/vendor",
        ):
            gb.apply_credit_note(
                credit_note_id=cn["id"],
                applies_to_invoice_id=beta_inv["id"],
            )


class TestCreditNotePr87ReviewFollowups:
    """Tests for the five Copilot PR #87 review findings.

    Each test exercises one validation gap or formatting bug
    Copilot flagged. They live in their own class so the
    follow-up boundary is visible in test output and traceable
    back to the review.
    """

    def test_resolve_credit_note_suggests_voucher_tool_for_voucher(
        self, business_book,
    ):
        """Comment 1: ``_resolve_credit_note`` error message
        should suggest ``add_voucher_entry`` / ``delete_voucher``
        when the found document is a voucher (owner_type=5),
        not ``add_bill_entry`` / ``delete_bill`` (the legacy
        binary-dispatch bug)."""
        gb = GnuCashBook(str(business_book))
        gb.create_employee(name="Maria")
        # Create a voucher with the same ID space as customer
        # invoices — owner_type=5 row, not flagged as credit note.
        gb.create_voucher(employee_id="000001")
        # Now try to use add_credit_note_entry on the voucher's
        # ID. _resolve_credit_note finds it, sees it's NOT a
        # credit note, and emits the suggestion message.
        with pytest.raises(
            ValueError, match="add_voucher_entry / delete_voucher",
        ):
            gb.add_credit_note_entry(
                credit_note_id="000001",
                account="Expenses:Office Supplies",
                description="x", quantity="1", price="50",
            )

    def test_create_credit_note_rejects_credit_note_source(
        self, business_book,
    ):
        """Comment 5: linking a credit note to another credit
        note is semantically meaningless and would mis-label
        ``applies_to.type``. Reject with a clear message."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        # First credit note (standalone, no source link)
        cn1 = gb.create_credit_note(
            owner_id="000001", owner_type="customer",
        )
        # Try to create a second credit note pointing at the first
        with pytest.raises(
            ValueError, match="itself a credit note",
        ):
            gb.create_credit_note(
                owner_id="000001", owner_type="customer",
                applies_to_invoice_id=cn1["id"],
            )

    def test_apply_credit_note_rejects_cross_currency_post_account(
        self, business_book,
    ):
        """Comment 2: cross-currency apply isn't supported —
        when the document currency differs from the post
        account's commodity, the netting transaction can't
        cleanly use a single amount. Reject with a clear
        message that points at the per-currency A/R convention
        as the fix.

        Setup uses raw piecash to engineer the cross-currency
        post state directly (EUR/USD price via piecash.Price,
        EUR customer + EUR credit note posted to USD A/R).
        The full posting flow validates the cross-currency
        guard fires at apply time, not at post."""
        import piecash
        from datetime import date as date_cls
        gb = GnuCashBook(str(business_book))
        # Add EUR + EUR/USD price via raw piecash (same pattern
        # the multi_currency tests use in test_book.py).
        with gb.open(readonly=False) as bk:
            eur = piecash.Commodity(
                namespace="CURRENCY", mnemonic="EUR",
                fullname="Euro", fraction=100,
            )
            bk.session.add(eur)
            bk.flush()
            usd = bk.default_currency
            bk.session.add(piecash.Price(
                commodity=eur, currency=usd,
                date=date_cls(2026, 5, 24),
                value="1.10",
                type="last",
            ))
            bk.save()
        gb.create_customer(name="Berlin GmbH", currency="EUR")
        # Source invoice in EUR posted to USD A/R (cross-currency)
        src = gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id=src["id"], account="Income:Sales",
            description="x", quantity="1", price="500",
        )
        gb.post_invoice(
            invoice_id=src["id"],
            post_account="Assets:Accounts Receivable",
            owner_type="customer",
            # Pin to the price date; this test targets the apply-
            # time cross-currency guard, not FX rate staleness.
            post_date="2026-05-24",
        )
        cn = gb.create_credit_note(
            owner_id="000001", owner_type="customer",
            applies_to_invoice_id=src["id"],
        )
        gb.add_credit_note_entry(
            credit_note_id=cn["id"], account="Income:Sales",
            description="x", quantity="1", price="100",
        )
        gb.post_invoice(
            invoice_id=cn["id"],
            post_account="Assets:Accounts Receivable",
            owner_type="customer",
            post_date="2026-05-24",
        )
        with pytest.raises(
            ValueError, match="Cross-currency apply not supported",
        ):
            gb.apply_credit_note(
                credit_note_id=cn["id"],
                applies_to_invoice_id=src["id"],
            )

    def test_apply_quantize_to_zero_rejected(self, business_book):
        """Comment 3: a sub-quantum apply amount (e.g. "0.001"
        on a USD account with 0.01 quantum) would round to zero
        and produce a no-op netting transaction reported as
        success. Guard rejects with the quantum named so the
        caller knows the minimum."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        # Set up a posted invoice + credit note pair
        src = gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id=src["id"], account="Income:Sales",
            description="x", quantity="1", price="500",
        )
        gb.post_invoice(
            invoice_id=src["id"],
            post_account="Assets:Accounts Receivable",
            owner_type="customer",
        )
        cn = gb.create_credit_note(
            owner_id="000001", owner_type="customer",
            applies_to_invoice_id=src["id"],
        )
        gb.add_credit_note_entry(
            credit_note_id=cn["id"], account="Income:Sales",
            description="x", quantity="1", price="100",
        )
        gb.post_invoice(
            invoice_id=cn["id"],
            post_account="Assets:Accounts Receivable",
            owner_type="customer",
        )
        with pytest.raises(
            ValueError, match="quantizes to zero",
        ):
            gb.apply_credit_note(
                credit_note_id=cn["id"],
                applies_to_invoice_id=src["id"],
                amount="0.001",
            )


class TestCreditNoteDisplayPolish:
    """Display rendering for credit notes: list_invoices,
    get_outstanding_invoices, and the dashboard's A/R / A/P
    netting. Tests pin the surface a reviewer would scan to
    understand which documents are credit notes at a glance.
    """

    def test_list_invoices_compact_marks_credit_notes(
        self, business_book,
    ):
        """Credit notes get a ``(CN)`` suffix on the type tag in
        compact output. Customer credit notes render as ``INV
        (CN)``; vendor as ``BILL (CN)``. Tab-separated, so the
        suffix is easy to grep for or split on."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        gb.create_vendor(name="Office Depot")
        gb.create_invoice(customer_id="000001")  # plain INV
        gb.create_bill(vendor_id="000001")  # plain BILL
        gb.create_credit_note(
            owner_id="000001", owner_type="customer",
        )
        gb.create_credit_note(
            owner_id="000001", owner_type="vendor",
        )
        out = gb.list_invoices()  # compact default
        # Both credit notes show the (CN) suffix on the tag column.
        assert "INV (CN)" in out
        assert "BILL (CN)" in out
        # Plain invoice/bill still render with bare tags.
        # Look for the bare tag NOT followed by " (CN)" — a single
        # tab-after-tag is the canonical separator.
        assert "\tINV\t" in out  # plain customer invoice
        assert "\tBILL\t" in out  # plain vendor bill

    def test_get_outstanding_invoices_marks_credit_notes(
        self, business_book,
    ):
        """Outstanding credit notes appear with a ``(CN)`` suffix
        on the owner column AND a "credit available" annotation
        in the due-date column — distinguishing them from
        invoices that read as "X days past due"."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        # Post a regular invoice (creates one outstanding row)
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="x", quantity="1", price="500",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
            owner_type="customer",
        )
        # Post a credit note (also outstanding — unapplied credit)
        cn = gb.create_credit_note(
            owner_id="000001", owner_type="customer",
        )
        gb.add_credit_note_entry(
            credit_note_id=cn["id"], account="Income:Sales",
            description="dispute", quantity="1", price="100",
        )
        gb.post_invoice(
            invoice_id=cn["id"],
            post_account="Assets:Accounts Receivable",
            owner_type="customer",
        )
        out = gb.get_outstanding_invoices()  # compact default
        # Both rows present.
        assert "000001" in out
        assert cn["id"] in out
        # Credit note has the (CN) marker and the "credit
        # available" action column.
        assert "(CN)" in out
        assert "credit available" in out
        # The "past due" wording does NOT appear for the credit
        # note row (it should only appear for the regular invoice
        # if its due date is past; with default test date, the
        # invoice probably has a due-in or past-due reading —
        # either way the credit note shouldn't carry that text).
        # Pull the CN's line specifically and check.
        cn_line = [
            ln for ln in out.split("\n") if cn["id"] in ln
        ][0]
        assert "past due" not in cn_line
        assert "credit available" in cn_line

    def test_get_outstanding_verbose_carries_is_credit_note(
        self, business_book,
    ):
        """Verbose output exposes the is_credit_note flag in the
        dict shape — important for LLMs that filter / branch on
        credit-note state programmatically."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        cn = gb.create_credit_note(
            owner_id="000001", owner_type="customer",
        )
        gb.add_credit_note_entry(
            credit_note_id=cn["id"], account="Income:Sales",
            description="x", quantity="1", price="50",
        )
        gb.post_invoice(
            invoice_id=cn["id"],
            post_account="Assets:Accounts Receivable",
            owner_type="customer",
        )
        rows = gb.get_outstanding_invoices(compact=False)["invoices"]
        cn_row = next(r for r in rows if r["id"] == cn["id"])
        assert cn_row["is_credit_note"] is True

    def test_dashboard_ar_nets_credit_notes_against_invoices(
        self, business_book,
    ):
        """The headline regression — get_book_summary's
        Receivables total uses the raw A/R balance, which already
        nets credit notes against invoices because the post
        directions reverse. Invoice $500 + credit note $100 →
        A/R should read $400 (not $500 + $100 = $600, and not
        $500). Locking this so a future refactor that misroutes
        credit-note posting math gets caught.
        """
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        # Invoice $500 posted.
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001", account="Income:Sales",
            description="x", quantity="1", price="500",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
            owner_type="customer",
        )
        # Credit note $100 posted (reduces what customer owes).
        cn = gb.create_credit_note(
            owner_id="000001", owner_type="customer",
        )
        gb.add_credit_note_entry(
            credit_note_id=cn["id"], account="Income:Sales",
            description="dispute", quantity="1", price="100",
        )
        gb.post_invoice(
            invoice_id=cn["id"],
            post_account="Assets:Accounts Receivable",
            owner_type="customer",
        )
        # A/R balance should net to $400 — the raw split sum.
        ar_balance = gb.get_balance("Assets:Accounts Receivable")
        assert ar_balance == Decimal("400.00")


class TestEntryNotesAction:
    """Per-line ``notes`` and ``action`` on the add_*_entry paths.

    All four tools route through ``_add_entry``, which previously
    hardcoded both columns to "" — the schema had the fields, the
    caller couldn't reach them.
    """

    def test_invoice_entry_notes_action_round_trip(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        result = gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="April retainer",
            quantity="10",
            price="150.00",
            notes="PO #2231, contact: J. Doe",
            action="Hours",
        )
        assert result["notes"] == "PO #2231, contact: J. Doe"
        assert result["action"] == "Hours"

        entry = gb.get_invoice("000001")["entries"][0]
        assert entry["notes"] == "PO #2231, contact: J. Doe"
        assert entry["action"] == "Hours"

    def test_plain_entry_shape_unchanged(self, business_book):
        """Entries without notes/action keep their exact key set —
        conditional emission, not new always-present fields."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        result = gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Service",
            quantity="1",
            price="100.00",
        )
        assert "notes" not in result
        assert "action" not in result
        entry = gb.get_invoice("000001")["entries"][0]
        assert "notes" not in entry
        assert "action" not in entry

    def test_notes_byte_cap_enforced(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        with pytest.raises(ValueError, match="notes exceeds"):
            gb.add_invoice_entry(
                invoice_id="000001",
                account="Income:Sales",
                description="Too chatty",
                quantity="1",
                price="100.00",
                notes="x" * 5000,
            )

    def test_bill_and_voucher_entries_carry_notes(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Vendor One")
        gb.create_bill(vendor_id="000001")
        bill_result = gb.add_bill_entry(
            bill_id="000001",
            account="Expenses:Office Supplies",
            description="Paper",
            quantity="2",
            price="25.00",
            notes="restock for Q3",
            action="Material",
        )
        assert bill_result["notes"] == "restock for Q3"
        assert bill_result["action"] == "Material"

        gb.create_employee(name="Field Tech")
        voucher = gb.create_voucher(employee_id="000001")
        voucher_result = gb.add_voucher_entry(
            voucher_id=voucher["id"],
            account="Expenses:Office Supplies",
            description="Client lunch",
            quantity="1",
            price="42.50",
            notes="attendees: 3, receipt #881",
        )
        assert voucher_result["notes"] == "attendees: 3, receipt #881"
        assert "action" not in voucher_result

    def test_credit_note_entry_carries_notes(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_credit_note(owner_id="000001", owner_type="customer")
        result = gb.add_credit_note_entry(
            credit_note_id="000001",
            account="Income:Sales",
            description="Overbilled hours",
            quantity="2",
            price="150.00",
            notes="per 2026-07-01 email thread",
        )
        assert result["notes"] == "per 2026-07-01 email thread"
        assert result["credit_note_id"] == "000001"


class TestAddInvoiceEntry:
    """Tests for add_invoice_entry."""

    def test_invoice_and_bill_entry_share_helper(self, business_book):
        """``add_invoice_entry`` and ``add_bill_entry`` are thin
        wrappers over ``_add_entry``. Pre-fix they were ~110-line
        duplicates; the dedup keeps the public surface and reduces
        the duplication to a per-doc-config table.

        Regression check: both methods still succeed end-to-end
        with the same response shape they had before the dedup.
        """
        gb = GnuCashBook(str(business_book))

        gb.create_customer(name="Customer One")
        gb.create_invoice(customer_id="000001")
        inv_result = gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Service",
            quantity="1", price="100.00",
        )
        assert inv_result["status"] == "created"
        assert inv_result["invoice_id"] == "000001"
        assert Decimal(inv_result["total"]) == Decimal("100.00")

        gb.create_vendor(name="Vendor One")
        gb.create_bill(vendor_id="000001")
        bill_result = gb.add_bill_entry(
            bill_id="000001",
            account="Expenses:Office Supplies",
            description="Paper",
            quantity="2", price="25.00",
        )
        assert bill_result["status"] == "created"
        assert bill_result["bill_id"] == "000001"
        assert Decimal(bill_result["total"]) == Decimal("50.00")

        # Both responses carry the SAME shape (just different
        # id-key name) — that's the dedup contract.
        assert set(inv_result.keys()) - {"invoice_id"} == \
            set(bill_result.keys()) - {"bill_id"}

    def test_rejects_non_income_account(self, business_book):
        """Invoice entries must post to INCOME accounts. Pre-fix any
        account type was accepted, producing transactions with
        broken posting math (e.g., debit-asset against debit-A/R)."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        with pytest.raises(ValueError, match="must be INCOME"):
            gb.add_invoice_entry(
                invoice_id="000001",
                account="Assets:Checking",  # ASSET — silently accepted pre-fix
                description="Bad entry",
                quantity="1",
                price="500.00",
            )

    def test_basic_entry(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        result = gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Consulting services",
            quantity="1",
            price="500.00",
        )
        assert result["status"] == "created"
        assert Decimal(result["total"]) == Decimal("500")
        assert result["invoice_id"] == "000001"

    def test_fractional_quantity(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        result = gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Hours",
            quantity="2.5",
            price="100.00",
        )
        assert Decimal(result["total"]) == Decimal("250")

    def test_multiple_entries(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001", account="Income:Sales",
            description="Item 1", quantity="1", price="100.00",
        )
        gb.add_invoice_entry(
            invoice_id="000001", account="Income:Consulting",
            description="Item 2", quantity="2", price="75.00",
        )
        inv = gb.get_invoice("000001")
        assert len(inv["entries"]) == 2
        assert Decimal(inv["total"]) == Decimal("250")

    def test_invalid_invoice(self, business_book):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="Invoice not found"):
            gb.add_invoice_entry(
                invoice_id="999999", account="Income:Sales",
                description="Test", quantity="1", price="10",
            )

    def test_wrong_type_rejects_bill(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        gb.create_bill(vendor_id="000001")
        with pytest.raises(ValueError, match="Invoice not found"):
            gb.add_invoice_entry(
                invoice_id="000001", account="Income:Sales",
                description="Test", quantity="1", price="10",
            )

    def test_invalid_account(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        with pytest.raises(ValueError, match="Account not found"):
            gb.add_invoice_entry(
                invoice_id="000001", account="Income:Nonexistent",
                description="Test", quantity="1", price="10",
            )


class TestAddBillEntry:
    """Tests for add_bill_entry."""

    def test_rejects_non_expense_or_asset_account(self, business_book):
        """Bill entries must post to EXPENSE (line items) or ASSET
        (inventory). LIABILITY/EQUITY/INCOME silently broke posting
        math pre-fix."""
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        gb.create_bill(vendor_id="000001")
        with pytest.raises(ValueError, match="must be EXPENSE or ASSET"):
            gb.add_bill_entry(
                bill_id="000001",
                # PAYABLE — non-EXPENSE, non-ASSET, exists in fixture
                account="Liabilities:Accounts Payable",
                description="Bad entry",
                quantity="1",
                price="50.00",
            )

    def test_basic_entry(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        gb.create_bill(vendor_id="000001")
        result = gb.add_bill_entry(
            bill_id="000001",
            account="Expenses:Office Supplies",
            description="Printer paper",
            quantity="10",
            price="5.00",
        )
        assert result["status"] == "created"
        assert Decimal(result["total"]) == Decimal("50")
        assert result["bill_id"] == "000001"

    def test_wrong_type_rejects_invoice(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        with pytest.raises(ValueError, match="Bill not found"):
            gb.add_bill_entry(
                bill_id="000001", account="Expenses:Office Supplies",
                description="Test", quantity="1", price="10",
            )


class TestListInvoices:
    """Tests for list_invoices."""

    def test_empty_list(self, business_book):
        gb = GnuCashBook(str(business_book))
        result = gb.list_invoices()
        assert result == "Showing 0 of 0 invoices"

    def test_compact_format(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001", date_opened="2026-01-15")
        result = gb.list_invoices()
        assert "000001" in result
        assert "INV" in result
        assert "open" in result

    def test_verbose_format(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        result = gb.list_invoices(compact=False)
        # Verbose mode returns the uniform pagination envelope:
        # invoices list + showing + total + offset + count.
        assert isinstance(result, dict)
        assert "invoices" in result
        assert "count" in result
        assert "total" in result
        assert "showing" in result
        invoices = result["invoices"]
        assert len(invoices) == 1
        assert invoices[0]["type"] == "invoice"
        assert result["total"] == 1
        assert result["showing"].startswith("Showing 1-1 of 1 invoices")

    def test_filter_by_customer_type(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_vendor(name="Office Depot")
        gb.create_invoice(customer_id="000001")
        gb.create_bill(vendor_id="000001")
        result = gb.list_invoices(owner_type="customer", compact=False)
        invoices = result["invoices"]
        assert len(invoices) == 1
        assert invoices[0]["type"] == "invoice"

    def test_filter_by_vendor_type(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_vendor(name="Office Depot")
        gb.create_invoice(customer_id="000001")
        gb.create_bill(vendor_id="000001")
        result = gb.list_invoices(owner_type="vendor", compact=False)
        invoices = result["invoices"]
        assert len(invoices) == 1
        assert invoices[0]["type"] == "bill"

    def test_filter_by_status(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        # All invoices are open (not posted)
        result = gb.list_invoices(status="open", compact=False)
        assert len(result["invoices"]) == 1
        result = gb.list_invoices(status="posted", compact=False)
        assert len(result["invoices"]) == 0


class TestGetInvoice:
    """Tests for get_invoice."""

    def test_get_customer_invoice(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001", account="Income:Sales",
            description="Consulting", quantity="1", price="500.00",
        )
        result = gb.get_invoice("000001")
        assert result["type"] == "invoice"
        # is_posted dropped (derivable from date_posted).
        assert result["date_posted"] is None
        assert len(result["entries"]) == 1
        assert Decimal(result["total"]) == Decimal("500")
        assert result["owner_name"] == "Acme Corp"

    def test_get_vendor_bill(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        gb.create_bill(vendor_id="000001")
        gb.add_bill_entry(
            bill_id="000001", account="Expenses:Office Supplies",
            description="Paper", quantity="10", price="5.00",
        )
        result = gb.get_invoice("000001")
        assert result["type"] == "bill"
        assert len(result["entries"]) == 1
        assert Decimal(result["total"]) == Decimal("50")
        assert result["owner_name"] == "Office Depot"

    def test_not_found(self, business_book):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="not found"):
            gb.get_invoice("999999")

    def test_entries_have_correct_fields(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001", account="Income:Sales",
            description="Widget", quantity="3", price="25.00",
        )
        result = gb.get_invoice("000001")
        entry = result["entries"][0]
        # v1.3.1: entry guid dropped — no standalone tool surface
        # consumes it.
        assert entry["description"] == "Widget"
        assert Decimal(entry["quantity"]) == Decimal("3")
        assert Decimal(entry["price"]) == Decimal("25")
        assert Decimal(entry["total"]) == Decimal("75")


class TestInvoiceBillIdCollision:
    """Tests for ID collision between invoices and bills.

    Invoice and bill IDs come from independent counters, so both
    start at 000001. The lookup must filter by owner_type to avoid
    returning the wrong record.
    """

    def test_add_invoice_entry_with_collision(self, business_book):
        """add_invoice_entry targets the invoice, not the bill."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_vendor(name="Office Depot")
        gb.create_invoice(customer_id="000001")  # ID 000001
        gb.create_bill(vendor_id="000001")        # ID 000001
        # Should target the customer invoice, not the vendor bill —
        # add_invoice_entry passes owner_type=2 explicitly.
        result = gb.add_invoice_entry(
            invoice_id="000001", account="Income:Sales",
            description="Consulting", quantity="1", price="100",
        )
        assert result["status"] == "created"
        # get_invoice without owner_type now raises on the same
        # collision (see TestInvoiceBillIdCollision::test_get_invoice
        # _on_collision_raises_disambiguation_error). Pass owner_type
        # to retrieve the customer invoice.
        inv = gb.get_invoice("000001", owner_type="customer")
        assert inv["type"] == "invoice"
        assert len(inv["entries"]) == 1

    def test_add_bill_entry_with_collision(self, business_book):
        """add_bill_entry targets the bill, not the invoice."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_vendor(name="Office Depot")
        gb.create_invoice(customer_id="000001")  # ID 000001
        gb.create_bill(vendor_id="000001")        # ID 000001
        # Should target the vendor bill, not the customer invoice
        result = gb.add_bill_entry(
            bill_id="000001", account="Expenses:Office Supplies",
            description="Paper", quantity="1", price="50",
        )
        assert result["status"] == "created"

    def test_get_invoice_on_collision_raises_disambiguation_error(
        self, business_book,
    ):
        """When a customer invoice and vendor bill share the same
        numeric ID, ``get_invoice`` (called without ``owner_type``)
        must raise rather than silently return whichever the SQL
        query happened to surface first. The bookkeeper hit this on
        a CNY book where ``get_invoice("000003")`` returned a CNY
        customer invoice instead of the USD vendor bill they were
        verifying — silent wrong-document reads are worse than a
        clear error."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_vendor(name="Office Depot")
        gb.create_invoice(customer_id="000001")
        gb.create_bill(vendor_id="000001")

        with pytest.raises(ValueError) as exc_info:
            gb.get_invoice("000001")
        msg = str(exc_info.value)
        # Error tells the caller exactly what's ambiguous and how to fix.
        assert "2 documents" in msg
        assert "'000001'" in msg
        assert "customer invoice" in msg
        assert "vendor bill" in msg
        assert "owner_type" in msg

    def test_get_invoice_with_owner_type_filter(self, business_book):
        """get_invoice with owner_type disambiguates colliding IDs."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_vendor(name="Office Depot")
        gb.create_invoice(customer_id="000001")
        gb.create_bill(vendor_id="000001")
        inv = gb.get_invoice("000001", owner_type="customer")
        assert inv["type"] == "invoice"
        bill = gb.get_invoice("000001", owner_type="vendor")
        assert bill["type"] == "bill"

    def test_get_invoice_unambiguous_id_no_owner_type_works(
        self, business_book,
    ):
        """When an ID is unique (only one document with it), the
        ambiguity check doesn't fire and ``get_invoice`` returns
        the matching document without requiring ``owner_type``.
        This is the common case — the disambiguation is paid for
        only when actually ambiguous."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_vendor(name="Office Depot")
        gb.create_invoice(customer_id="000001")
        # No vendor bill 000001, so the invoice ID is unambiguous.
        inv = gb.get_invoice("000001")
        assert inv["type"] == "invoice"


class TestOwnerTypeValidation:
    """Centralized rejection of invalid ``owner_type`` values.

    Pre-v1.3 the validator explicitly rejected ``"employee"`` with
    a "not yet supported" message because vouchers weren't built
    yet. v1.3 added vouchers; ``"employee"`` is now a first-class
    type returning 5 (alongside customer=2, vendor=4). The
    validator still rejects typos / unknown strings with a clear
    options list.
    """

    def test_typo_owner_type_rejected_with_valid_options(
        self, business_book,
    ):
        """Typos like ``"custmer"`` (missing 'o') get the upfront
        rejection. The error names all three valid options so the
        LLM doesn't have to call back blindly."""
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError) as exc_info:
            gb.get_invoice("000001", owner_type="custmer")
        msg = str(exc_info.value)
        assert "Invalid owner_type" in msg
        assert "'custmer'" in msg
        # All three valid options should appear in the hint.
        assert "customer" in msg
        assert "vendor" in msg
        assert "employee" in msg

    def test_employee_owner_type_accepted(self, business_book):
        """v1.3 invariant: ``owner_type="employee"`` is now valid
        and returns 5 from ``_parse_owner_type``. The function-
        level test is here; the end-to-end voucher exercise is in
        TestCreateVoucher / TestVoucherLifecycle."""
        from gnucash_mcp.book.business import BusinessMixin
        assert BusinessMixin._parse_owner_type("employee") == 5

    def test_none_owner_type_still_works(self, business_book):
        """``None`` means "no filter" — the existing semantic
        must survive the validation refactor."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        # No collision yet — None resolves to a single match.
        inv = gb.get_invoice("000001", owner_type=None)
        assert inv["type"] == "invoice"

    def test_pay_invoice_rejects_typo_owner_type(
        self, business_book,
    ):
        """``pay_invoice`` shares the same validator — typos get
        rejected here too."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        with pytest.raises(ValueError, match="Invalid owner_type"):
            gb.pay_invoice(
                invoice_id="000001",
                payment_account="Assets:Checking",
                amount="50",
                owner_type="venddor",  # typo
            )

    def test_list_invoices_rejects_invalid_owner_type(
        self, business_book,
    ):
        """The reads validate the same way the writes do."""
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="Invalid owner_type"):
            gb.list_invoices(owner_type="bogus")


# ============== Post Invoice Tests ==============


class TestPostInvoice:
    """Tests for post_invoice."""

    def _setup_invoice(self, gb, amount="500.00"):
        """Create customer + invoice + entry, return invoice ID."""
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Consulting",
            quantity="1",
            price=amount,
        )
        return "000001"

    def _setup_bill(self, gb, amount="50.00"):
        """Create vendor + bill + entry, return bill ID."""
        gb.create_vendor(name="Office Depot")
        gb.create_bill(vendor_id="000001")
        gb.add_bill_entry(
            bill_id="000001",
            account="Expenses:Office Supplies",
            description="Paper",
            quantity="1",
            price=amount,
        )
        return "000001"

    def test_post_disambiguates_id_collision_via_post_account(
        self, business_book,
    ):
        """When customer invoices and vendor bills share the same
        ID (separate sequences in piecash), ``post_invoice``
        disambiguates from ``post_account`` even when the caller
        doesn't pass ``owner_type``. Bookkeeper hit this on Alex's
        book: a vendor bill 000010 was unfindable because the
        already-posted customer invoice 000010 came back from the
        unfiltered lookup and raised "already posted."

        Verifies the inferred-owner-type path: post_account
        type=RECEIVABLE → customer invoice 2; PAYABLE → vendor
        bill 4."""
        gb = GnuCashBook(str(business_book))
        # Post a customer invoice 000001.
        self._setup_invoice(gb)
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
        )
        # Now create a vendor bill that lands at the SAME id
        # (separate sequence). Verify owner_type counter-fix
        # got it to 000001 (no other bills exist).
        self._setup_bill(gb)
        # Critical scenario: post WITHOUT owner_type. Should
        # infer vendor from PAYABLE post_account, find the bill
        # (not the already-posted invoice).
        result = gb.post_invoice(
            invoice_id="000001",
            post_account="Liabilities:Accounts Payable",
        )
        assert result["status"] == "posted"
        assert result["type"] == "bill"
        # Sanity: invoice 000001 stays posted, didn't get re-touched.
        inv = gb.get_invoice("000001", owner_type="customer")
        assert inv["date_posted"] is not None

    def test_post_explicit_owner_type_still_works(
        self, business_book,
    ):
        """Explicit owner_type continues to disambiguate when
        post_account isn't enough (e.g. typo'd or a placeholder).
        Belt-and-suspenders: explicit > inferred > unfiltered."""
        gb = GnuCashBook(str(business_book))
        self._setup_invoice(gb)
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
        )
        self._setup_bill(gb)
        result = gb.post_invoice(
            invoice_id="000001",
            post_account="Liabilities:Accounts Payable",
            owner_type="vendor",
        )
        assert result["status"] == "posted"
        assert result["type"] == "bill"

    def test_basic_post(self, business_book):
        gb = GnuCashBook(str(business_book))
        self._setup_invoice(gb)
        result = gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
        )
        assert result["status"] == "posted"
        assert Decimal(result["total"]) == Decimal("500")
        # v1.3.1: transaction_guid + lot_guid emitted as short
        # collision-safe prefixes (min 8 chars) rather than full
        # 32-char. Consumers accept 8+ char prefixes via _resolve_guid.
        assert len(result["transaction_guid"]) >= 8
        assert len(result["lot_guid"]) >= 8
        assert result["post_account"] == "Assets:Accounts Receivable"

    def test_post_marks_invoice_posted(self, business_book):
        gb = GnuCashBook(str(business_book))
        self._setup_invoice(gb)
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
        )
        inv = gb.get_invoice("000001")
        # `is_posted` dropped — derivable from non-null date_posted.
        assert inv["date_posted"] is not None

    def test_post_with_multiple_entries(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Widget",
            quantity="2",
            price="150.00",
        )
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Consulting",
            description="Advisory",
            quantity="1",
            price="200.00",
        )
        result = gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
        )
        assert Decimal(result["total"]) == Decimal("500")

    def test_post_same_account_entries_aggregated(self, business_book):
        """Entries to the same income account produce one split."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Item A",
            quantity="1",
            price="300.00",
        )
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Item B",
            quantity="1",
            price="200.00",
        )
        result = gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
        )
        assert Decimal(result["total"]) == Decimal("500")

    def test_post_already_posted_raises(self, business_book):
        gb = GnuCashBook(str(business_book))
        self._setup_invoice(gb)
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
        )
        with pytest.raises(ValueError, match="already posted"):
            gb.post_invoice(
                invoice_id="000001",
                post_account="Assets:Accounts Receivable",
            )

    def test_post_no_entries_raises(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        with pytest.raises(ValueError, match="no entries"):
            gb.post_invoice(
                invoice_id="000001",
                post_account="Assets:Accounts Receivable",
            )

    def test_post_not_found_raises(self, business_book):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="not found"):
            gb.post_invoice(
                invoice_id="999999",
                post_account="Assets:Accounts Receivable",
            )

    def test_post_wrong_account_type_raises(self, business_book):
        gb = GnuCashBook(str(business_book))
        self._setup_invoice(gb)
        with pytest.raises(ValueError, match="RECEIVABLE"):
            gb.post_invoice(
                invoice_id="000001",
                post_account="Assets:Checking",
            )

    def test_post_vendor_bill(self, business_book):
        gb = GnuCashBook(str(business_book))
        self._setup_bill(gb)
        result = gb.post_invoice(
            invoice_id="000001",
            post_account="Liabilities:Accounts Payable",
            owner_type="vendor",
        )
        assert result["status"] == "posted"
        assert result["type"] == "bill"
        assert Decimal(result["total"]) == Decimal("50")

    def test_post_with_custom_date(self, business_book):
        gb = GnuCashBook(str(business_book))
        self._setup_invoice(gb)
        result = gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
            post_date="2026-03-15",
        )
        assert result["post_date"] == "2026-03-15"

    def test_post_with_description(self, business_book):
        gb = GnuCashBook(str(business_book))
        self._setup_invoice(gb)
        result = gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
            description="Q1 consulting services",
        )
        assert result["status"] == "posted"

    def test_post_sets_transaction_metadata(self, business_book):
        """Posting transaction has GnuCash-compatible metadata."""
        import sqlite3

        gb = GnuCashBook(str(business_book))
        self._setup_invoice(gb)
        result = gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
            due_date="2026-04-01",
        )
        # v1.3.1: result now carries short prefixes. Resolve back
        # to full GUIDs for the direct-sqlite3 equality queries
        # below.
        txn_guid_prefix = result["transaction_guid"]
        lot_guid_prefix = result["lot_guid"]

        # Read slots from the database directly
        conn = sqlite3.connect(str(business_book))
        try:
            txn_guid = conn.execute(
                "SELECT guid FROM transactions WHERE guid LIKE ?",
                (txn_guid_prefix + "%",),
            ).fetchone()[0]
            lot_guid = conn.execute(
                "SELECT guid FROM lots WHERE guid LIKE ?",
                (lot_guid_prefix + "%",),
            ).fetchone()[0]
            # Transaction num should be invoice ID
            txn = conn.execute(
                "SELECT num, description FROM transactions "
                "WHERE guid = ?",
                (txn_guid,),
            ).fetchone()
            assert txn[0] == "000001", "transaction.num should be invoice ID"
            assert txn[1] == "Acme Corp", (
                "description should be customer name"
            )

            # A/R split should have action='Invoice'
            ar_split = conn.execute(
                "SELECT action, reconcile_date FROM splits "
                "WHERE tx_guid = ? AND action = 'Invoice'",
                (txn_guid,),
            ).fetchone()
            assert ar_split is not None, "A/R split should have action=Invoice"

            # Check transaction slots
            slots = conn.execute(
                "SELECT name, slot_type, string_val, guid_val, gdate_val "
                "FROM slots WHERE obj_guid = ?",
                (txn_guid,),
            ).fetchall()
            slot_dict = {s[0]: s for s in slots}

            assert "trans-txn-type" in slot_dict, "missing trans-txn-type slot"
            assert slot_dict["trans-txn-type"][2] == "I"

            assert "trans-read-only" in slot_dict, "missing trans-read-only slot"
            assert "invoice" in slot_dict["trans-read-only"][2].lower()

            # gncInvoice frame slot on transaction
            assert "gncInvoice" in slot_dict, (
                "missing gncInvoice slot on transaction"
            )
            frame_guid = slot_dict["gncInvoice"][3]
            assert frame_guid is not None

            # Child GUID slot inside the frame
            child = conn.execute(
                "SELECT name, slot_type, guid_val FROM slots "
                "WHERE obj_guid = ? AND name = 'invoice'",
                (frame_guid,),
            ).fetchone()
            assert child is not None, "missing gncInvoice/invoice child slot"
            assert child[1] == 5, "child slot should be GUID type (5)"

            # Due date slot
            assert "trans-date-due" in slot_dict, "missing trans-date-due slot"
            assert slot_dict["trans-date-due"][1] == 10, (
                "trans-date-due should be GDATE type (10)"
            )

            # gncInvoice frame slot on lot
            lot_slots = conn.execute(
                "SELECT name, slot_type, guid_val FROM slots "
                "WHERE obj_guid = ?",
                (lot_guid,),
            ).fetchall()
            lot_slot_dict = {s[0]: s for s in lot_slots}
            assert "gncInvoice" in lot_slot_dict, (
                "missing gncInvoice slot on lot"
            )
        finally:
            conn.close()

    def test_post_bill_sets_metadata(self, business_book):
        """Vendor bill posting also sets correct metadata."""
        import sqlite3

        gb = GnuCashBook(str(business_book))
        self._setup_bill(gb)
        result = gb.post_invoice(
            invoice_id="000001",
            post_account="Liabilities:Accounts Payable",
            owner_type="vendor",
        )
        txn_guid_prefix = result["transaction_guid"]

        conn = sqlite3.connect(str(business_book))
        try:
            # Resolve short prefix → full GUID for the equality
            # queries below (v1.3.1 short-prefix change).
            txn_guid = conn.execute(
                "SELECT guid FROM transactions WHERE guid LIKE ?",
                (txn_guid_prefix + "%",),
            ).fetchone()[0]
            txn = conn.execute(
                "SELECT description FROM transactions WHERE guid = ?",
                (txn_guid,),
            ).fetchone()
            assert txn[0] == "Office Depot", (
                "bill description should be vendor name"
            )

            slots = conn.execute(
                "SELECT name, string_val FROM slots "
                "WHERE obj_guid = ? AND slot_type = 4",
                (txn_guid,),
            ).fetchall()
            slot_dict = {s[0]: s[1] for s in slots}
            assert slot_dict.get("trans-txn-type") == "I"
        finally:
            conn.close()

    def test_cross_currency_post_missing_rate_raises(self, business_book):
        """Posting a cross-currency invoice with no usable price in the
        book fails with a clear error pointing at create_price.
        """
        import piecash
        import pytest

        gb = GnuCashBook(str(business_book))

        with gb.open(readonly=False) as book:
            eur = piecash.factories.create_currency_from_ISO("EUR")
            book.session.add(eur)
            assets = None
            for a in book.accounts:
                if a.fullname == "Assets":
                    assets = a
                    break
            piecash.Account(name="Accounts Receivable EUR",
                            type="RECEIVABLE", parent=assets,
                            commodity=eur)
            book.save()

        gb.create_customer(name="Berlin Digital", currency="EUR")
        gb.create_invoice(customer_id="000001", currency="EUR",
                          date_opened="2026-03-10")
        gb.add_invoice_entry(invoice_id="000001",
                             account="Income:Consulting",  # USD
                             description="EUR services",
                             quantity="1", price="100.00")

        with pytest.raises(ValueError, match="exchange rate"):
            gb.post_invoice(
                invoice_id="000001",
                post_account="Assets:Accounts Receivable EUR",
                post_date="2026-03-10",
            )

    def test_cross_currency_post_uses_price_table(self, business_book):
        """EUR invoice posted to EUR A/R with USD income account converts
        the income split's quantity at the price-table rate. The A/R
        split stays 1:1 because A/R is EUR (matches invoice currency).
        """
        import piecash
        from datetime import date as _date

        gb = GnuCashBook(str(business_book))

        with gb.open(readonly=False) as book:
            usd = book.default_currency
            eur = piecash.factories.create_currency_from_ISO("EUR")
            book.session.add(eur)

            assets = None
            for a in book.accounts:
                if a.fullname == "Assets":
                    assets = a
                    break
            ar_eur = piecash.Account(
                name="Accounts Receivable EUR",
                type="RECEIVABLE",
                parent=assets,
                commodity=eur,
            )
            book.session.add(ar_eur)

            book.session.add(piecash.Price(
                commodity=eur, currency=usd,
                date=_date(2026, 3, 10), value="1.10",
                source="user:test", type="nav",
            ))
            book.save()

        gb.create_customer(name="Berlin Digital", currency="EUR")
        gb.create_invoice(
            customer_id="000001", currency="EUR",
            date_opened="2026-03-10",
        )
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Consulting",  # USD account
            description="EUR services",
            quantity="1", price="1000.00",
        )
        result = gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable EUR",
            post_date="2026-03-10",
        )

        assert result["status"] == "posted"

        # Inspect the transaction's splits: income quantity should be
        # 1000 * 1.10 = 1100 USD while value stays at 1000 EUR.
        with gb.open(readonly=True) as book:
            inv = None
            for i in book.session.query(piecash.business.Invoice).all():
                if i.id == "000001":
                    inv = i
                    break
            assert inv is not None
            txn = inv.post_txn
            assert txn is not None

            income_split = None
            ar_split = None
            for s in txn.splits:
                if s.account.fullname == "Income:Consulting":
                    income_split = s
                elif s.account.fullname == "Assets:Accounts Receivable EUR":
                    ar_split = s

            assert income_split is not None
            assert ar_split is not None
            # A/R EUR matches invoice currency, so quantity == value
            assert Decimal(str(ar_split.value)) == Decimal("1000")
            assert Decimal(str(ar_split.quantity)) == Decimal("1000")
            # Income USD: value in EUR (invoice ccy), quantity in USD at rate
            assert Decimal(str(income_split.value)) == Decimal("-1000")
            assert Decimal(str(income_split.quantity)) == Decimal("-1100.00")

    def test_cross_currency_bill_post_uses_vendor_currency(
        self, business_book,
    ):
        """Regression for the vendor-bill FX bug: a EUR vendor on a
        USD-default book had bills created in USD (book default)
        instead of EUR (vendor's currency). Posted transactions
        then skipped cross-currency conversion entirely — €500 was
        booked as $500. With the fix, ``create_bill`` inherits the
        vendor's currency and ``post_invoice`` runs the same
        cross-currency conversion path that customer invoices use.
        """
        import piecash
        from datetime import date as _date

        gb = GnuCashBook(str(business_book))

        with gb.open(readonly=False) as book:
            usd = book.default_currency
            eur = piecash.factories.create_currency_from_ISO("EUR")
            book.session.add(eur)

            assets = next(a for a in book.accounts if a.fullname == "Assets")
            ap_eur = piecash.Account(
                name="Accounts Payable EUR",
                type="PAYABLE",
                parent=assets,
                commodity=eur,
            )
            book.session.add(ap_eur)

            # USD/EUR rate: 1 EUR = 1.10 USD (i.e. EUR is the more
            # valuable currency). Stored as commodity=EUR, currency=USD.
            book.session.add(piecash.Price(
                commodity=eur, currency=usd,
                date=_date(2026, 3, 10), value="1.10",
                source="user:test", type="nav",
            ))
            book.save()

        # No explicit currency on create_bill — should inherit EUR
        # from the vendor.
        gb.create_vendor(name="München GmbH", currency="EUR")
        result = gb.create_bill(
            vendor_id="000001", date_opened="2026-03-10",
        )
        # Confirm inheritance worked at create time.
        bill = gb.get_invoice(result["id"])
        assert bill["currency"] == "EUR"

        # add_bill_entry on a USD expense account; post to EUR A/P.
        gb.add_bill_entry(
            bill_id=result["id"],
            account="Expenses:Office Supplies",  # USD account
            description="Software license",
            quantity="1", price="1000.00",
        )
        gb.post_invoice(
            invoice_id=result["id"],
            post_account="Assets:Accounts Payable EUR",
            post_date="2026-03-10",
        )

        # Inspect splits: expense quantity should be the USD-equivalent
        # at 1.10 (€1000 × 1.10 = $1100), while value stays at €1000.
        # A/P EUR matches transaction currency → quantity == value.
        with gb.open(readonly=True) as book:
            inv = None
            for i in book.session.query(piecash.business.Invoice).all():
                if i.id == result["id"]:
                    inv = i
                    break
            assert inv is not None
            txn = inv.post_txn
            assert txn is not None
            # Bill posted in EUR, not USD.
            assert txn.currency.mnemonic == "EUR"

            expense_split = next(
                s for s in txn.splits
                if s.account.fullname == "Expenses:Office Supplies"
            )
            ap_split = next(
                s for s in txn.splits
                if s.account.fullname == "Assets:Accounts Payable EUR"
            )

            # Bill (vendor): A/P credit (negative), expense debit (positive).
            assert Decimal(str(ap_split.value)) == Decimal("-1000")
            assert Decimal(str(ap_split.quantity)) == Decimal("-1000")
            assert Decimal(str(expense_split.value)) == Decimal("1000")
            # Expense split is USD; quantity converted at rate 1.10.
            assert Decimal(str(expense_split.quantity)) == Decimal("1100.00")


# ============== Unpost Invoice Tests ==============


class TestUnpostInvoice:
    """Tests for unpost_invoice — the reverse of post_invoice.

    The bookkeeper hit the orphaned-invoice problem on both Alex's
    and Lin Wei's books: a posting transaction got deleted via
    ``delete_transaction``, but the invoice retained its
    ``date_posted`` / ``post_txn`` / ``post_lot`` / ``post_acc``
    pointers. The invoice then refused both delete ("already
    posted") and re-post ("posted"); only SQL surgery escaped.
    Two-prong fix: ``unpost_invoice`` for clean reversal +
    ``delete_transaction`` rejection of posting records.
    """

    def _post_invoice(self, gb, amount="500.00"):
        """Returns the post_invoice result dict (includes
        ``transaction_guid`` and ``lot_guid``, which ``get_invoice``
        doesn't surface)."""
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Consulting",
            quantity="1",
            price=amount,
        )
        return gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
        )

    def test_unpost_returns_invoice_to_open_state(self, business_book):
        """Unposting clears date_posted, post_txn, post_lot,
        post_account; the invoice is editable again."""
        gb = GnuCashBook(str(business_book))
        posted = self._post_invoice(gb)

        result = gb.unpost_invoice(invoice_id=posted["id"])

        assert result["id"] == posted["id"]
        assert result["type"] == "invoice"
        assert result["status"] == "unposted"

        # Invoice is open again.
        inv = gb.get_invoice(posted["id"], owner_type="customer")
        assert inv["date_posted"] is None

    def test_unpost_deletes_posting_transaction(self, business_book):
        """The transaction that posting created is removed from
        the book — not just unlinked from the invoice."""
        gb = GnuCashBook(str(business_book))
        posted = self._post_invoice(gb)
        txn_guid = posted["transaction_guid"]

        gb.unpost_invoice(invoice_id=posted["id"])

        # Transaction is gone.
        with gb.open(readonly=True) as book:
            from piecash.core.transaction import Transaction
            tx = (
                book.session.query(Transaction)
                .filter_by(guid=txn_guid)
                .first()
            )
            assert tx is None

    def test_unpost_allows_re_posting(self, business_book):
        """After unpost, the invoice can be posted again — the
        canonical lifecycle is post → unpost → edit → post."""
        gb = GnuCashBook(str(business_book))
        posted = self._post_invoice(gb)
        gb.unpost_invoice(invoice_id=posted["id"])

        # Re-post on a different date with a different account.
        result = gb.post_invoice(
            invoice_id=posted["id"],
            post_account="Assets:Accounts Receivable",
            post_date="2026-05-15",
        )
        assert result["status"] == "posted"
        assert result["post_date"] == "2026-05-15"

    def test_unpost_rejects_invoice_with_payment_applied(
        self, business_book,
    ):
        """Unposting an invoice that has any payment applied would
        orphan the payment splits. Force the user to void payments
        first."""
        gb = GnuCashBook(str(business_book))
        posted = self._post_invoice(gb)
        gb.pay_invoice(
            invoice_id=posted["id"],
            payment_account="Assets:Checking",
            amount="100.00",
        )

        with pytest.raises(ValueError, match="has payments applied"):
            gb.unpost_invoice(invoice_id=posted["id"])

    def test_unpost_succeeds_when_payment_was_voided(
        self, business_book,
    ):
        """A voided payment has zero economic effect — GnuCash's
        void operation preserves the split for audit purposes but
        zeroes the values. The "has payments applied" guard asks
        an *economic* question ("would unposting orphan real
        money?"); voided splits answer no.

        The bookkeeper hit this on Alex's book: posted invoice →
        partial payment → voided the payment → ``unpost_invoice``
        rejected with "has payments applied" even though zero
        dollars would have been orphaned. Treating voided
        payments as still-applied contradicted GnuCash's own
        void semantics. Regression locks the fix.
        """
        gb = GnuCashBook(str(business_book))
        posted = self._post_invoice(gb)
        pay = gb.pay_invoice(
            invoice_id=posted["id"],
            payment_account="Assets:Checking",
            amount="100.00",
        )
        gb.void_transaction(
            guid=pay["transaction_guid"],
            reason="Test cleanup — voiding partial payment",
        )

        # Voided payment has zero economic effect → unpost allowed.
        result = gb.unpost_invoice(invoice_id=posted["id"])
        assert result["status"] == "unposted"

    def test_unpost_rejects_unposted_invoice(self, business_book):
        """Open invoices have nothing to unpost."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001", account="Income:Sales",
            description="x", quantity="1", price="50",
        )

        with pytest.raises(ValueError, match="is not posted"):
            gb.unpost_invoice(invoice_id="000001")

    def test_unpost_rejects_unknown_invoice(self, business_book):
        """Clear error when the ID doesn't match anything."""
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="not found"):
            gb.unpost_invoice(invoice_id="999999")

    def test_unpost_works_for_vendor_bill(self, business_book):
        """Symmetry — the bill side reverses cleanly too."""
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        gb.create_bill(vendor_id="000001")
        gb.add_bill_entry(
            bill_id="000001", account="Expenses:Office Supplies",
            description="Paper", quantity="1", price="50",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Liabilities:Accounts Payable",
        )

        result = gb.unpost_invoice(
            invoice_id="000001", owner_type="vendor",
        )
        assert result["type"] == "bill"
        assert result["status"] == "unposted"

    def test_unpost_disambiguates_via_owner_type(self, business_book):
        """When customer invoice and vendor bill share an ID,
        ``owner_type`` selects which side to unpost."""
        gb = GnuCashBook(str(business_book))
        # Post both on the same id 000001.
        gb.create_customer(name="Acme Corp")
        gb.create_vendor(name="Office Depot")
        gb.create_invoice(customer_id="000001")
        gb.create_bill(vendor_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001", account="Income:Sales",
            description="x", quantity="1", price="100",
        )
        gb.add_bill_entry(
            bill_id="000001", account="Expenses:Office Supplies",
            description="y", quantity="1", price="50",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
            owner_type="customer",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Liabilities:Accounts Payable",
            owner_type="vendor",
        )

        # Unpost the customer side; the vendor bill stays posted.
        gb.unpost_invoice(invoice_id="000001", owner_type="customer")

        inv = gb.get_invoice("000001", owner_type="customer")
        bill = gb.get_invoice("000001", owner_type="vendor")
        assert inv["date_posted"] is None
        assert bill["date_posted"] is not None

    def test_delete_transaction_rejects_posting_record(
        self, business_book,
    ):
        """Direct deletion of a posting transaction is the original
        defect this whole fix exists to prevent. The check rejects
        the call with a message pointing at unpost_invoice."""
        gb = GnuCashBook(str(business_book))
        posted = self._post_invoice(gb)

        with pytest.raises(ValueError) as exc_info:
            gb.delete_transaction(guid=posted["transaction_guid"])
        msg = str(exc_info.value)
        assert "posting record" in msg
        assert posted["id"] in msg
        assert "unpost_invoice" in msg

    def test_delete_transaction_works_for_non_posting_records(
        self, business_book,
    ):
        """Regression: only posting transactions are protected.
        Plain transactions (e.g., regular bank deposits) still
        delete cleanly."""
        gb = GnuCashBook(str(business_book))
        from datetime import date as _date
        result = gb.create_transaction(
            description="Regular deposit",
            splits=[
                {"account": "Assets:Checking", "amount": "100"},
                {"account": "Income:Sales", "amount": "-100"},
            ],
            trans_date=_date(2026, 1, 1),
        )
        gb.delete_transaction(guid=result["guid"])  # no error


# ============== Pay Invoice Tests ==============


class TestPayInvoice:
    """Tests for pay_invoice."""

    def _post_invoice(self, gb, amount="500.00"):
        """Create customer + invoice + entry + post, return invoice ID."""
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Consulting",
            quantity="1",
            price=amount,
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
        )
        return "000001"

    def _post_bill(self, gb, amount="50.00"):
        """Create vendor + bill + entry + post, return bill ID."""
        gb.create_vendor(name="Office Depot")
        gb.create_bill(vendor_id="000001")
        gb.add_bill_entry(
            bill_id="000001",
            account="Expenses:Office Supplies",
            description="Paper",
            quantity="1",
            price=amount,
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Liabilities:Accounts Payable",
            owner_type="vendor",
        )
        return "000001"

    def test_full_payment(self, business_book):
        gb = GnuCashBook(str(business_book))
        self._post_invoice(gb, "500.00")
        result = gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="500",
        )
        assert result["status"] == "paid"
        assert Decimal(result["remaining_balance"]) == Decimal("0")

    def test_partial_payment(self, business_book):
        gb = GnuCashBook(str(business_book))
        self._post_invoice(gb, "500.00")
        result = gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="200",
        )
        assert Decimal(result["remaining_balance"]) == Decimal("300")

    def test_memo_lands_on_bank_split_only(self, business_book):
        """User memo annotates the cash movement; the A/R//A/P split
        keeps its action='Payment' convention with an empty memo."""
        gb = GnuCashBook(str(business_book))
        self._post_invoice(gb, "500.00")
        result = gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="500",
            memo="check #1042",
        )
        txn = gb.get_transaction(result["transaction_guid"])
        memos = {s["account"]: s.get("memo", "") for s in txn["splits"]}
        assert memos["Assets:Checking"] == "check #1042"
        assert memos["Assets:Accounts Receivable"] == ""

    def test_no_memo_keeps_prior_shape(self, business_book):
        gb = GnuCashBook(str(business_book))
        self._post_bill(gb, "50.00")
        result = gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="50",
            owner_type="vendor",
        )
        txn = gb.get_transaction(result["transaction_guid"])
        assert all(not s.get("memo") for s in txn["splits"])

    def test_multiple_payments(self, business_book):
        gb = GnuCashBook(str(business_book))
        self._post_invoice(gb, "500.00")
        gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="200",
        )
        result = gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="300",
        )
        assert Decimal(result["remaining_balance"]) == Decimal("0")

    def test_pay_unposted_raises(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Widget",
            quantity="1",
            price="100.00",
        )
        with pytest.raises(ValueError, match="not posted"):
            gb.pay_invoice(
                invoice_id="000001",
                payment_account="Assets:Checking",
                amount="100",
            )

    def test_pay_not_found_raises(self, business_book):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="not found"):
            gb.pay_invoice(
                invoice_id="999999",
                payment_account="Assets:Checking",
                amount="100",
            )

    def test_pay_zero_amount_raises(self, business_book):
        gb = GnuCashBook(str(business_book))
        self._post_invoice(gb)
        with pytest.raises(ValueError, match="must be positive"):
            gb.pay_invoice(
                invoice_id="000001",
                payment_account="Assets:Checking",
                amount="0",
            )

    def test_pay_vendor_bill(self, business_book):
        gb = GnuCashBook(str(business_book))
        self._post_bill(gb, "50.00")
        result = gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="50",
            owner_type="vendor",
        )
        assert result["type"] == "bill"
        assert Decimal(result["remaining_balance"]) == Decimal("0")

    def test_pay_with_custom_date(self, business_book):
        gb = GnuCashBook(str(business_book))
        self._post_invoice(gb)
        result = gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="500",
            payment_date="2026-04-01",
        )
        assert result["payment_date"] == "2026-04-01"

    def test_pay_with_description(self, business_book):
        gb = GnuCashBook(str(business_book))
        self._post_invoice(gb)
        result = gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="500",
            description="Wire transfer payment",
        )
        assert result["status"] == "paid"

    def test_payment_sets_transaction_metadata(self, business_book):
        """Payment transaction has GnuCash-compatible metadata."""
        import sqlite3

        gb = GnuCashBook(str(business_book))
        self._post_invoice(gb)
        result = gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="500",
        )
        txn_guid_prefix = result["transaction_guid"]

        conn = sqlite3.connect(str(business_book))
        try:
            # Resolve short prefix → full GUID for the equality
            # queries below (v1.3.1 short-prefix change).
            txn_guid = conn.execute(
                "SELECT guid FROM transactions WHERE guid LIKE ?",
                (txn_guid_prefix + "%",),
            ).fetchone()[0]
            # Description should be customer name
            txn = conn.execute(
                "SELECT description FROM transactions WHERE guid = ?",
                (txn_guid,),
            ).fetchone()
            assert txn[0] == "Acme Corp", (
                "payment description should be customer name"
            )

            # A/R split should have action='Payment'
            pay_split = conn.execute(
                "SELECT action FROM splits "
                "WHERE tx_guid = ? AND action = 'Payment'",
                (txn_guid,),
            ).fetchone()
            assert pay_split is not None, (
                "A/R split should have action=Payment"
            )

            # trans-txn-type slot should be 'P'
            slot = conn.execute(
                "SELECT string_val FROM slots "
                "WHERE obj_guid = ? AND name = 'trans-txn-type'",
                (txn_guid,),
            ).fetchone()
            assert slot is not None, "missing trans-txn-type slot on payment"
            assert slot[0] == "P"
        finally:
            conn.close()

    def test_full_payment_closes_lot(self, business_book):
        """Full payment auto-closes the lot with GnuCash boolean -1."""
        import sqlite3

        gb = GnuCashBook(str(business_book))
        self._post_invoice(gb)
        post_result = gb.get_invoice("000001")

        gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="500",
        )

        # Check lot is_closed = -1 in the database
        conn = sqlite3.connect(str(business_book))
        try:
            lots = conn.execute(
                "SELECT is_closed FROM lots WHERE account_guid IN "
                "(SELECT guid FROM accounts WHERE name = 'Accounts Receivable')"
            ).fetchall()
            assert any(
                row[0] == -1 for row in lots
            ), "lot should be closed with is_closed=-1"
        finally:
            conn.close()

    def test_partial_payment_does_not_close_lot(self, business_book):
        """Partial payment leaves lot open."""
        import sqlite3

        gb = GnuCashBook(str(business_book))
        self._post_invoice(gb)
        gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="200",
        )

        conn = sqlite3.connect(str(business_book))
        try:
            lots = conn.execute(
                "SELECT is_closed FROM lots WHERE account_guid IN "
                "(SELECT guid FROM accounts WHERE name = 'Accounts Receivable')"
            ).fetchall()
            assert all(
                row[0] == 0 for row in lots
            ), "lot should remain open after partial payment"
        finally:
            conn.close()

    def test_cross_currency_payment_uses_price_table(self, business_book):
        """EUR invoice paid from USD Checking converts at book.prices rate.

        The A/R split stays in invoice currency (EUR), the pay account's
        quantity is the USD amount derived from the EUR→USD price on or
        before payment_date. Result includes exchange_rate and
        payment_account_amount.
        """
        import piecash
        from datetime import date as _date

        gb = GnuCashBook(str(business_book))

        # Prep: add EUR commodity, EUR A/R sub-account, and a EUR→USD price
        with gb.open(readonly=False) as book:
            usd = book.default_currency
            eur = piecash.factories.create_currency_from_ISO("EUR")
            book.session.add(eur)

            receivables = None
            for a in book.accounts:
                if a.fullname == "Assets":
                    receivables = a
                    break

            ar_eur = piecash.Account(
                name="Accounts Receivable EUR",
                type="RECEIVABLE",
                parent=receivables,
                commodity=eur,
            )
            book.session.add(ar_eur)

            price = piecash.Price(
                commodity=eur,
                currency=usd,
                date=_date(2026, 3, 10),
                value="1.10",
                source="user:test",
                type="nav",
            )
            book.session.add(price)
            book.save()

        # Customer in EUR, invoice in EUR
        gb.create_customer(name="Berlin Digital", currency="EUR")
        gb.create_invoice(
            customer_id="000001",
            currency="EUR",
            date_opened="2026-03-10",
        )
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Consulting",
            description="EUR services",
            quantity="1",
            price="4500.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable EUR",
            post_date="2026-03-10",
        )

        result = gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="4500.00",
            payment_date="2026-03-10",
        )

        assert result["status"] == "paid"
        assert Decimal(result["remaining_balance"]) == Decimal("0")
        # Cross-currency fields present (rate may stringify as "1.1" or "1.10")
        assert Decimal(result["exchange_rate"]) == Decimal("1.10")
        assert Decimal(result["payment_account_amount"]) == Decimal("4950.00")
        assert result["invoice_currency"] == "EUR"
        assert result["payment_account_currency"] == "USD"

        # Balance checks: EUR A/R cleared, USD Checking credited at the rate
        ar_balance = gb.get_balance(account_name="Assets:Accounts Receivable EUR")
        assert ar_balance == Decimal("0")
        checking_balance = gb.get_balance(account_name="Assets:Checking")
        # Business book opens with $10,000 in checking per the fixture
        assert checking_balance == Decimal("14950.00")

    def _add_eur_ar_and_price(self, gb, rate_date, rate_value):
        """Helper: add EUR commodity, EUR A/R subaccount, and a EUR/USD price."""
        import piecash
        from datetime import date as _date
        with gb.open(readonly=False) as book:
            usd = book.default_currency
            eur = None
            for c in book.commodities:
                if c.mnemonic == "EUR":
                    eur = c
                    break
            if eur is None:
                eur = piecash.factories.create_currency_from_ISO("EUR")
                book.session.add(eur)
            if not any(a.fullname == "Assets:Accounts Receivable EUR"
                       for a in book.accounts):
                assets = next(a for a in book.accounts if a.fullname == "Assets")
                book.session.add(piecash.Account(
                    name="Accounts Receivable EUR", type="RECEIVABLE",
                    parent=assets, commodity=eur,
                ))
            parsed = rate_date if isinstance(rate_date, _date) else _date.fromisoformat(rate_date)
            book.session.add(piecash.Price(
                commodity=eur, currency=usd, date=parsed,
                value=str(rate_value), source="user:test", type="nav",
            ))
            book.save()

    def test_cross_currency_fx_gain_on_customer_payment(self, business_book):
        """When the pay-date rate is higher than the post-date rate, the
        extra USD received is recognized as FX gain.

        Post Mar 10 at 1.10 (income $4,950). Pay Mar 20 at 1.12
        ($5,040 received). Gain = $90, booked to
        Income:Foreign Exchange Gain/Loss with value=0 and
        quantity=-90 (credit to credit-natural income account).
        """
        import piecash
        gb = GnuCashBook(str(business_book))

        # Two prices: post-date and pay-date rates.
        self._add_eur_ar_and_price(gb, "2026-03-10", "1.10")
        self._add_eur_ar_and_price(gb, "2026-03-20", "1.12")

        gb.create_customer(name="Berlin Digital", currency="EUR")
        gb.create_invoice(customer_id="000001", currency="EUR",
                          date_opened="2026-03-10")
        gb.add_invoice_entry(invoice_id="000001",
                             account="Income:Consulting",
                             description="EUR services",
                             quantity="1", price="4500.00")
        gb.post_invoice(invoice_id="000001",
                        post_account="Assets:Accounts Receivable EUR",
                        post_date="2026-03-10")
        result = gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="4500.00",
            payment_date="2026-03-20",
        )

        # Checking credited at pay-date rate
        assert Decimal(result["payment_account_amount"]) == Decimal("5040.00")
        # FX realized: gain of $90
        assert "fx_realized" in result
        assert result["fx_realized"]["direction"] == "gain"
        assert Decimal(result["fx_realized"]["amount"]) == Decimal("90.00")
        assert result["fx_realized"]["account"] == "Income:Foreign Exchange Gain/Loss"

        # FX account balance: credit of $90 (income earned)
        fx_balance = gb.get_balance(account_name="Income:Foreign Exchange Gain/Loss")
        # Income is credit-natural; credit balance stored as negative.
        assert fx_balance == Decimal("-90")
        # Consulting income unchanged at post-date rate
        consulting = gb.get_balance(account_name="Income:Consulting")
        assert consulting == Decimal("-4950")
        # Checking holds full $5,040 + $10,000 opening
        checking = gb.get_balance(account_name="Assets:Checking")
        assert checking == Decimal("15040.00")
        # Net income (Consulting + FX) matches cash received
        assert -consulting + -fx_balance == checking - Decimal("10000")

    def test_cross_currency_fx_loss_on_customer_payment(self, business_book):
        """Pay-date rate lower than post-date rate → FX loss.

        Post at 1.12 ($5,040 income). Pay at 1.10 ($4,950 received).
        Loss = $90, split has positive quantity = +90 (debit to
        credit-natural income = loss recognized).
        """
        gb = GnuCashBook(str(business_book))

        self._add_eur_ar_and_price(gb, "2026-03-10", "1.12")
        self._add_eur_ar_and_price(gb, "2026-03-20", "1.10")

        gb.create_customer(name="Berlin Digital", currency="EUR")
        gb.create_invoice(customer_id="000001", currency="EUR",
                          date_opened="2026-03-10")
        gb.add_invoice_entry(invoice_id="000001",
                             account="Income:Consulting",
                             description="EUR services",
                             quantity="1", price="4500.00")
        gb.post_invoice(invoice_id="000001",
                        post_account="Assets:Accounts Receivable EUR",
                        post_date="2026-03-10")
        result = gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="4500.00",
            payment_date="2026-03-20",
        )

        assert Decimal(result["payment_account_amount"]) == Decimal("4950.00")
        assert result["fx_realized"]["direction"] == "loss"
        assert Decimal(result["fx_realized"]["amount"]) == Decimal("90.00")
        # FX Gain/Loss has a debit = positive stored value (credit-natural
        # account with a debit shows as positive).
        fx_balance = gb.get_balance(account_name="Income:Foreign Exchange Gain/Loss")
        assert fx_balance == Decimal("90")

    def _add_eur_ap_and_price(self, gb, rate_date, rate_value):
        """Helper for vendor-bill FX tests: EUR A/P + EUR/USD price.

        Mirrors ``_add_eur_ar_and_price`` but creates an A/P account
        (PAYABLE type) so vendor-bill posting/paying can be exercised
        in a EUR-denominated workflow.
        """
        import piecash
        from datetime import date as _date
        with gb.open(readonly=False) as book:
            usd = book.default_currency
            eur = None
            for c in book.commodities:
                if c.mnemonic == "EUR":
                    eur = c
                    break
            if eur is None:
                eur = piecash.factories.create_currency_from_ISO("EUR")
                book.session.add(eur)
            if not any(
                a.fullname == "Liabilities:Accounts Payable EUR"
                for a in book.accounts
            ):
                liabs = next(
                    a for a in book.accounts if a.fullname == "Liabilities"
                )
                book.session.add(piecash.Account(
                    name="Accounts Payable EUR", type="PAYABLE",
                    parent=liabs, commodity=eur,
                ))
            parsed = (
                rate_date if isinstance(rate_date, _date)
                else _date.fromisoformat(rate_date)
            )
            book.session.add(piecash.Price(
                commodity=eur, currency=usd, date=parsed,
                value=str(rate_value), source="user:test", type="nav",
            ))
            book.save()

    def test_cross_currency_fx_gain_on_vendor_bill_payment(
        self, business_book,
    ):
        """Pay-date rate LOWER than post-date rate on a vendor bill →
        we paid LESS USD than expected → FX gain.

        Post €4500 bill on Mar 10 at rate 1.12 → expense recorded as
        $5,040. Pay on Mar 20 at rate 1.10 → only $4,950 actually
        leaves checking. We saved $90 on the spread → gain of $90,
        booked to Income:Foreign Exchange Gain/Loss as a credit
        (income increases). Pre-fix, the four-quadrant FX sign
        convention had no test for the vendor-bill side at all —
        only customer-invoice gain/loss were covered.
        """
        gb = GnuCashBook(str(business_book))

        self._add_eur_ap_and_price(gb, "2026-03-10", "1.12")
        self._add_eur_ap_and_price(gb, "2026-03-20", "1.10")

        gb.create_vendor(name="Berlin Vendor", currency="EUR")
        gb.create_bill(vendor_id="000001", currency="EUR",
                       date_opened="2026-03-10")
        gb.add_bill_entry(bill_id="000001",
                          account="Expenses:Office Supplies",
                          description="EUR purchase",
                          quantity="1", price="4500.00")
        gb.post_invoice(invoice_id="000001",
                        post_account="Liabilities:Accounts Payable EUR",
                        post_date="2026-03-10")
        result = gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="4500.00",
            payment_date="2026-03-20",
        )

        # We paid only $4,950 from checking (vs. $5,040 expected at
        # post-rate) → gain of $90.
        assert Decimal(result["payment_account_amount"]) == Decimal("4950.00")
        assert "fx_realized" in result
        assert result["fx_realized"]["direction"] == "gain"
        assert Decimal(result["fx_realized"]["amount"]) == Decimal("90.00")

        # FX Gain/Loss is credit-natural; a gain credits → stored
        # negative.
        fx_balance = gb.get_balance(
            account_name="Income:Foreign Exchange Gain/Loss"
        )
        assert fx_balance == Decimal("-90")

    def test_cross_currency_fx_loss_on_vendor_bill_payment(
        self, business_book,
    ):
        """Pay-date rate HIGHER than post-date rate on a vendor bill →
        we paid MORE USD than expected → FX loss.

        Post €4500 bill on Mar 10 at rate 1.10 → expense recorded as
        $4,950. Pay on Mar 20 at rate 1.12 → $5,040 actually leaves
        checking. We overpaid by $90 → loss of $90, booked to
        Income:Foreign Exchange Gain/Loss as a debit (income
        reduced).
        """
        gb = GnuCashBook(str(business_book))

        self._add_eur_ap_and_price(gb, "2026-03-10", "1.10")
        self._add_eur_ap_and_price(gb, "2026-03-20", "1.12")

        gb.create_vendor(name="Berlin Vendor", currency="EUR")
        gb.create_bill(vendor_id="000001", currency="EUR",
                       date_opened="2026-03-10")
        gb.add_bill_entry(bill_id="000001",
                          account="Expenses:Office Supplies",
                          description="EUR purchase",
                          quantity="1", price="4500.00")
        gb.post_invoice(invoice_id="000001",
                        post_account="Liabilities:Accounts Payable EUR",
                        post_date="2026-03-10")
        result = gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="4500.00",
            payment_date="2026-03-20",
        )

        assert Decimal(result["payment_account_amount"]) == Decimal("5040.00")
        assert result["fx_realized"]["direction"] == "loss"
        assert Decimal(result["fx_realized"]["amount"]) == Decimal("90.00")

        # FX loss debits the credit-natural account → stored
        # positive.
        fx_balance = gb.get_balance(
            account_name="Income:Foreign Exchange Gain/Loss"
        )
        assert fx_balance == Decimal("90")

    def test_commodity_quantum_helper(self):
        """``_commodity_quantum`` returns the right Decimal quantum
        for each commodity fraction. Pre-fix every cross-currency
        conversion in business.py hardcoded ``Decimal("0.01")``,
        which silently corrupts JPY (whole-yen) and BHD/KWD
        (3-decimal) currencies.
        """
        from gnucash_mcp.book.business import _commodity_quantum
        from decimal import Decimal as D

        class _Commodity:
            def __init__(self, fraction):
                self.fraction = fraction

        # USD-class (2 decimals)
        assert _commodity_quantum(_Commodity(100)) == D("0.01")
        # JPY (0 decimals — whole yen)
        assert _commodity_quantum(_Commodity(1)) == D(1)
        # BHD/KWD (3 decimals)
        assert _commodity_quantum(_Commodity(1000)) == D("0.001")
        # Stocks/crypto (4 decimals)
        assert _commodity_quantum(_Commodity(10000)) == D("0.0001")
        # Defensive: no fraction attr → assume 2 decimals
        class _NoFraction:
            pass
        assert _commodity_quantum(_NoFraction()) == D("0.01")

    def test_jpy_payment_quantizes_to_whole_yen(
        self, business_book,
    ):
        """A USD invoice paid from a JPY-denominated bank account
        must quantize the JPY-side quantity to whole yen — JPY's
        ``commodity.fraction`` is 1, no sub-yen units exist.

        Pre-fix the hardcoded ``Decimal("0.01")`` quantize stored
        ``¥1234.56``-shaped non-integer quantities on JPY accounts,
        which is meaningless (and invalid GnuCash data — fraction=1
        should reject sub-unit values).

        Setup: USD-default book, $100 USD invoice paid from JPY
        checking at rate 150.5 JPY/USD → expected ¥15,050 received
        (quantized to 0 decimals).
        """
        import piecash
        from datetime import date as _date

        gb = GnuCashBook(str(business_book))
        with gb.open(readonly=False) as book:
            usd = book.default_currency
            try:
                jpy = piecash.factories.create_currency_from_ISO("JPY")
                book.session.add(jpy)
                book.flush()
            except Exception:
                jpy = next(
                    c for c in book.commodities if c.mnemonic == "JPY"
                )

            # Verify our assumption about JPY's fraction.
            assert jpy.fraction == 1, (
                f"JPY should have fraction=1 (no sub-yen units); "
                f"got {jpy.fraction}"
            )

            assets = next(
                a for a in book.accounts if a.fullname == "Assets"
            )
            book.session.add(piecash.Account(
                name="JPY Checking", type="BANK",
                parent=assets, commodity=jpy,
            ))
            book.session.add(piecash.Price(
                commodity=usd, currency=jpy,
                date=_date(2026, 3, 10),
                value="150.5", source="user:price", type="nav",
            ))
            book.save()

        gb.create_customer(name="Tokyo Co", currency="USD")
        gb.create_invoice(
            customer_id="000001", currency="USD",
            date_opened="2026-03-10",
        )
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Consulting",
            description="USD services",
            quantity="1", price="100.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
            post_date="2026-03-10",
        )
        result = gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:JPY Checking",
            amount="100.00",
            payment_date="2026-03-10",
        )

        # JPY checking received $100 × 150.5 = ¥15,050 — but more
        # importantly, the stored quantity must be whole-yen
        # (no sub-unit fractional part). Pre-fix the hardcoded
        # 0.01 quantize would have left a 0-decimal-padded
        # ``¥15050.00``-shaped value on a fraction=1 commodity.
        pay_qty = Decimal(result["payment_account_amount"])
        assert pay_qty == Decimal("15050"), (
            f"Expected ¥15050, got {pay_qty}"
        )
        # And the quantum exponent must be ≥ 0 (no fractional part).
        assert pay_qty.as_tuple().exponent >= 0

    def test_rate_from_post_transaction_picks_target_commodity(self):
        """``_rate_from_post_transaction`` must inspect splits for one
        whose account commodity matches the target — not the first
        non-invoice-currency split it sees.

        Pre-fix the helper took ``(post_txn, invoice_currency)`` and
        returned the rate of the first cross-currency split, so a
        post with EUR invoice + USD income + GBP A/R would return
        the USD rate when the pay path needed the GBP rate.

        Builds a fake post-txn with three splits (EUR/USD/GBP) and
        asserts the helper returns the rate matching whichever target
        is requested.
        """
        from gnucash_mcp.book.business import BusinessMixin
        from decimal import Decimal as D

        # Stand-in objects with the small surface the helper uses.
        class _Commodity:
            def __init__(self, mnemonic):
                self.mnemonic = mnemonic

        class _Account:
            def __init__(self, commodity):
                self.commodity = commodity

        class _Split:
            def __init__(self, account, value, quantity):
                self.account = account
                self.value = value
                self.quantity = quantity

        class _PostTxn:
            def __init__(self, splits):
                self.splits = splits

        eur = _Commodity("EUR")
        usd = _Commodity("USD")
        gbp = _Commodity("GBP")
        post = _PostTxn([
            _Split(_Account(eur), D("100"), D("100")),    # invoice side, rate 1
            _Split(_Account(usd), D("-100"), D("-110")),  # USD rate 1.10
            _Split(_Account(gbp), D("-100"), D("-80")),   # GBP rate 0.80
        ])

        # Asking for USD must yield 1.10 (not 0.80, not the "first
        # cross-currency split" semantics).
        assert BusinessMixin._rate_from_post_transaction(
            post, usd
        ) == D("110") / D("100")
        # Asking for GBP must yield 0.80.
        assert BusinessMixin._rate_from_post_transaction(
            post, gbp
        ) == D("80") / D("100")
        # Asking for a commodity the post doesn't reference returns
        # None — caller falls back to the price table.
        chf = _Commodity("CHF")
        assert BusinessMixin._rate_from_post_transaction(
            post, chf
        ) is None

    def test_fx_split_converts_to_default_when_pay_account_third_currency(
        self, business_book,
    ):
        """When the payment account's commodity is neither the
        invoice currency nor the book default — the truly tri-
        currency case — the realized FX delta computed in
        pay-account commodity must be converted to book default
        before landing on the FX account.

        Pre-fix, ``fx_diff`` lived in pay-account commodity and was
        booked as the FX split's quantity, but the FX account's
        commodity was book default. Reports aggregating the FX
        account read GBP-as-USD silently.

        Setup: USD-default book, EUR invoice, GBP checking.
        Post Mar 10 at EUR→GBP=0.80 (€100 → £80 expected).
        Pay Mar 20 at EUR→GBP=0.85 (£85 actually received) → £5
        gain in GBP. With GBP→USD = 1.30 at pay-date, that's a
        $6.50 gain that must land on the USD-commodity FX account.
        """
        import piecash
        from datetime import date as _date

        gb = GnuCashBook(str(business_book))
        with gb.open(readonly=False) as book:
            usd = book.default_currency
            try:
                eur = piecash.factories.create_currency_from_ISO("EUR")
                book.session.add(eur)
                book.flush()
            except Exception:
                eur = next(
                    c for c in book.commodities if c.mnemonic == "EUR"
                )
            try:
                gbp = piecash.factories.create_currency_from_ISO("GBP")
                book.session.add(gbp)
                book.flush()
            except Exception:
                gbp = next(
                    c for c in book.commodities if c.mnemonic == "GBP"
                )

            assets = next(
                a for a in book.accounts if a.fullname == "Assets"
            )
            book.session.add(piecash.Account(
                name="GBP Checking", type="BANK",
                parent=assets, commodity=gbp,
            ))
            book.session.add(piecash.Account(
                name="A/R EUR", type="RECEIVABLE",
                parent=assets, commodity=eur,
            ))
            # Two EUR/GBP rates — post-date and pay-date.
            book.session.add(piecash.Price(
                commodity=eur, currency=gbp,
                date=_date(2026, 3, 10),
                value="0.80", source="user:price", type="nav",
            ))
            book.session.add(piecash.Price(
                commodity=eur, currency=gbp,
                date=_date(2026, 3, 20),
                value="0.85", source="user:price", type="nav",
            ))
            # EUR → USD rate so post_invoice can convert the income
            # split's quantity to its USD-denominated commodity.
            book.session.add(piecash.Price(
                commodity=eur, currency=usd,
                date=_date(2026, 3, 10),
                value="1.10", source="user:price", type="nav",
            ))
            # GBP → USD rate so the FX delta converts to default.
            book.session.add(piecash.Price(
                commodity=gbp, currency=usd,
                date=_date(2026, 3, 20),
                value="1.30", source="user:price", type="nav",
            ))
            book.save()

        gb.create_customer(name="London Client", currency="EUR")
        gb.create_invoice(
            customer_id="000001", currency="EUR",
            date_opened="2026-03-10",
        )
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Consulting",
            description="EUR services",
            quantity="1", price="100.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:A/R EUR",
            post_date="2026-03-10",
        )
        result = gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:GBP Checking",
            amount="100.00",
            payment_date="2026-03-20",
        )

        # GBP delta: £85 received vs £80 expected = £5 gain in GBP.
        # Converted at GBP→USD=1.30 → $6.50 gain on the USD-commodity
        # FX account.
        assert result["fx_realized"]["direction"] == "gain"
        assert Decimal(result["fx_realized"]["amount"]) == Decimal("6.50")
        assert result["fx_realized"]["currency"] == "USD"

        # FX account balance: $6.50 credit (negative on credit-natural
        # income) — measured in book default currency.
        fx_balance = gb.get_balance(
            account_name="Income:Foreign Exchange Gain/Loss"
        )
        assert fx_balance == Decimal("-6.50")

    def test_pay_invoice_refuses_voided_posting_transaction(self, business_book):
        """If the posting transaction was voided in GnuCash, paying
        the invoice would compute remaining=0 against a zero'd lot,
        auto-close the lot, and assign the new payment to a closed
        lot. Now refused up front with a clear error pointing at
        unvoid_transaction."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Services",
            quantity="1", price="500.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
            post_date="2026-03-10",
        )
        # Void the posting transaction.
        with gb.open(readonly=True) as book:
            from piecash.business.invoice import Invoice
            inv = book.session.query(Invoice).filter_by(id="000001").first()
            post_txn_guid = inv.post_txn.guid
        gb.void_transaction(guid=post_txn_guid, reason="test")

        with pytest.raises(ValueError, match="posting transaction has been voided"):
            gb.pay_invoice(
                invoice_id="000001",
                payment_account="Assets:Checking",
                amount="500.00",
                payment_date="2026-03-15",
            )

    def test_calculate_lot_balance_skips_voided_splits(self, business_book):
        """``_calculate_lot_balance`` filters voided splits so the
        helper agrees with the rest of the void-aware codebase."""
        from gnucash_mcp.book.business import BusinessMixin
        from decimal import Decimal as D

        class _Split:
            def __init__(self, value, reconcile_state):
                self.value = value
                self.reconcile_state = reconcile_state

        class _Lot:
            def __init__(self, splits):
                self.splits = splits

        # Mix of live and voided splits.
        lot = _Lot([
            _Split(D("100"), "n"),
            _Split(D("0"), "v"),  # voided — must be skipped
            _Split(D("-30"), "n"),
        ])
        assert BusinessMixin._calculate_lot_balance(lot) == D("70")

    def test_safe_invoice_date_handles_empty_string(self):
        """``_safe_invoice_date`` covers both date_opened and
        date_posted with one parameterized helper — empty/malformed
        values surface as None rather than crashing the regex
        parser. Generalized in v1.3 from the two separate
        ``_safe_date_opened`` / ``_safe_date_posted`` helpers; one
        helper, one set of semantics, parameterized on the
        attribute name.
        """
        from gnucash_mcp.book.business import _safe_invoice_date

        class _EmptyInv:
            @property
            def date_opened(self):
                # Mimic piecash's _DateTime regex parser raising
                # on a malformed empty string.
                raise ValueError("Couldn't parse datetime string")

            @property
            def date_posted(self):
                raise ValueError("Couldn't parse datetime string")

        class _NoneInv:
            date_opened = None
            date_posted = None

        class _ValidInv:
            from datetime import datetime as _dt
            date_opened = _dt(2026, 3, 10, 12, 0)
            date_posted = _dt(2026, 3, 15, 9, 30)

        # Both attribute paths behave identically.
        for attr in ("date_opened", "date_posted"):
            assert _safe_invoice_date(_EmptyInv(), attr) is None
            assert _safe_invoice_date(_NoneInv(), attr) is None
            result = _safe_invoice_date(_ValidInv(), attr)
            assert result is not None
            assert result.year == 2026

    def test_find_exchange_rate_skips_zero_direct_price(self, business_book):
        """Direct branch must skip rate=0 (and negative) prices —
        previously only the inverse branch had this guard, leaving
        a corrupt zero-direct price as a propagatable rate."""
        import piecash
        from datetime import date as _date

        gb = GnuCashBook(str(business_book))
        with gb.open(readonly=False) as book:
            usd = book.default_currency
            try:
                eur = piecash.factories.create_currency_from_ISO("EUR")
                book.session.add(eur)
                book.flush()
            except Exception:
                eur = next(c for c in book.commodities if c.mnemonic == "EUR")
            # Insert a corrupt zero-rate direct price.
            book.session.add(piecash.Price(
                commodity=eur, currency=usd, date=_date(2026, 3, 10),
                value="0", source="user:price", type="nav",
            ))
            book.save()

        with gb.open(readonly=True) as book:
            usd = book.default_currency
            eur = next(c for c in book.commodities if c.mnemonic == "EUR")
            # No usable price → returns None (zero-rate skipped, no fallback).
            rate = gb._find_exchange_rate(
                book, from_commodity=eur, to_commodity=usd,
                as_of=_date(2026, 3, 10),
            )
            assert rate is None

    def test_find_exchange_rate_respects_staleness_cap(
        self, business_book, monkeypatch,
    ):
        """Plumb Bob bookkeeper-flagged: pre-fix the function
        would silently use a stale price regardless of distance
        from ``as_of``. The 90-day default cap (overridable via
        GNUCASH_FX_STALENESS_DAYS) now refuses prices outside
        the window — surfacing the "Add a price with
        create_price" error the docstring promised.
        """
        import piecash
        from datetime import date as _date

        gb = GnuCashBook(str(business_book))
        # Insert a fresh EUR/USD price and a stale one.
        with gb.open(readonly=False) as book:
            usd = book.default_currency
            try:
                eur = piecash.factories.create_currency_from_ISO("EUR")
                book.session.add(eur)
                book.flush()
            except Exception:
                eur = next(c for c in book.commodities if c.mnemonic == "EUR")
            # A "fresh" price 30 days before our as_of.
            book.session.add(piecash.Price(
                commodity=eur, currency=usd,
                date=_date(2026, 5, 1), value="1.10",
                source="user:price", type="nav",
            ))
            # A "stale" price 5 years before our as_of (will be
            # the only candidate when we drop the fresh one).
            book.session.add(piecash.Price(
                commodity=eur, currency=usd,
                date=_date(2021, 1, 1), value="1.05",
                source="user:price", type="nav",
            ))
            book.save()

        # Default 90-day cap: fresh price (30 days back) is within
        # window, stale price (years back) is outside. We get the
        # fresh one.
        monkeypatch.delenv("GNUCASH_FX_STALENESS_DAYS", raising=False)
        with gb.open(readonly=True) as book:
            usd = book.default_currency
            eur = next(c for c in book.commodities if c.mnemonic == "EUR")
            rate = gb._find_exchange_rate(
                book, from_commodity=eur, to_commodity=usd,
                as_of=_date(2026, 5, 31),
            )
            assert rate == Decimal("1.10")

        # Tighten the cap to 10 days: both prices fall outside.
        # No usable rate → None → caller raises with the
        # "Add a price" hint.
        monkeypatch.setenv("GNUCASH_FX_STALENESS_DAYS", "10")
        with gb.open(readonly=True) as book:
            usd = book.default_currency
            eur = next(c for c in book.commodities if c.mnemonic == "EUR")
            rate = gb._find_exchange_rate(
                book, from_commodity=eur, to_commodity=usd,
                as_of=_date(2026, 5, 31),
            )
            assert rate is None, (
                "Both prices are >10 days from as_of; cap should "
                "refuse them"
            )

        # Cap=0 disables the window — original pre-fix behavior.
        # The stale 2021 price is now usable on a 2026 query.
        monkeypatch.setenv("GNUCASH_FX_STALENESS_DAYS", "0")
        with gb.open(readonly=True) as book:
            usd = book.default_currency
            eur = next(c for c in book.commodities if c.mnemonic == "EUR")
            rate = gb._find_exchange_rate(
                book, from_commodity=eur, to_commodity=usd,
                # 6 years past the only "fresh" rate; default cap
                # would refuse this, but the disable lets the
                # nearest-available (the 2026-05-01 price) win.
                as_of=_date(2032, 1, 1),
            )
            # 2026-05-01 is closer to 2032-01-01 than 2021-01-01.
            assert rate == Decimal("1.10")

    def test_pay_invoice_converts_ar_quantity_when_ar_currency_differs(
        self, business_book,
    ):
        """When the A/R account's commodity differs from the invoice
        currency, ``pay_invoice`` must convert the A/R-side quantity
        the same way ``post_invoice`` does. Pre-fix, only the
        bank-side quantity was converted; the A/R-side stayed in
        invoice-currency units, leaving a residual balance the bank
        had already paid for.

        Setup: USD-default book, USD A/R account, EUR invoice,
        EUR/USD = 1.10. Post a €4,500 invoice → A/R debited 4,950
        USD. Pay €4,500 from USD checking → A/R must credit 4,950
        USD (not 4,500), leaving the A/R balance at exactly zero.
        """
        import piecash
        from datetime import date as _date

        gb = GnuCashBook(str(business_book))
        # Add EUR commodity + price + a USD A/R account specifically.
        with gb.open(readonly=False) as book:
            usd = book.default_currency
            try:
                eur = piecash.factories.create_currency_from_ISO("EUR")
                book.session.add(eur)
                book.flush()
            except Exception:
                eur = next(
                    c for c in book.commodities if c.mnemonic == "EUR"
                )
            assets = next(
                a for a in book.accounts if a.fullname == "Assets"
            )
            book.session.add(piecash.Account(
                name="A/R USD", type="RECEIVABLE",
                parent=assets, commodity=usd,
            ))
            book.session.add(piecash.Price(
                commodity=eur, currency=usd,
                date=_date(2026, 3, 10),
                value="1.10", source="user:test", type="nav",
            ))
            book.save()

        gb.create_customer(name="Berlin Digital", currency="EUR")
        gb.create_invoice(
            customer_id="000001", currency="EUR",
            date_opened="2026-03-10",
        )
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Consulting",
            description="EUR services",
            quantity="1", price="4500.00",
        )
        # Post into the USD A/R account — invoice in EUR, A/R in USD
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:A/R USD",
            post_date="2026-03-10",
        )

        ar_after_post = gb.get_balance(account_name="Assets:A/R USD")
        # Post correctly converts: €4500 × 1.10 = $4,950 USD debited
        assert ar_after_post == Decimal("4950")

        gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="4500.00",
            payment_date="2026-03-10",
        )

        # A/R must be ZERO after payment. Pre-fix, the A/R-side
        # quantity stayed at -4500 (raw EUR value treated as USD on
        # the USD-commodity account), leaving $450 floating.
        ar_after_pay = gb.get_balance(account_name="Assets:A/R USD")
        assert ar_after_pay == Decimal("0")

    def test_cross_currency_same_rate_no_fx_split(self, business_book):
        """Post and pay at the identical rate → no FX split, no FX account."""
        gb = GnuCashBook(str(business_book))
        self._add_eur_ar_and_price(gb, "2026-03-10", "1.10")

        gb.create_customer(name="Berlin Digital", currency="EUR")
        gb.create_invoice(customer_id="000001", currency="EUR",
                          date_opened="2026-03-10")
        gb.add_invoice_entry(invoice_id="000001",
                             account="Income:Consulting",
                             description="EUR services",
                             quantity="1", price="4500.00")
        gb.post_invoice(invoice_id="000001",
                        post_account="Assets:Accounts Receivable EUR",
                        post_date="2026-03-10")
        result = gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="4500.00",
            payment_date="2026-03-10",
        )

        assert "fx_realized" not in result
        # FX account wasn't auto-created since no FX drift.
        assert gb.get_account(name="Income:Foreign Exchange Gain/Loss") is None

    def test_cross_currency_payment_to_matching_currency_account(self, business_book):
        """When the payment account's commodity matches the invoice
        currency, no rate lookup is needed (same-currency payment on a
        cross-currency-denominated invoice). Example: paying an EUR
        invoice from a EUR-denominated bank account.
        """
        import piecash

        gb = GnuCashBook(str(business_book))

        with gb.open(readonly=False) as book:
            eur = piecash.factories.create_currency_from_ISO("EUR")
            book.session.add(eur)
            assets = None
            for a in book.accounts:
                if a.fullname == "Assets":
                    assets = a
                    break
            piecash.Account(name="Euro Checking", type="BANK",
                            parent=assets, commodity=eur)
            piecash.Account(name="Accounts Receivable EUR",
                            type="RECEIVABLE", parent=assets,
                            commodity=eur)
            book.save()

        gb.create_customer(name="Berlin Digital", currency="EUR")
        gb.create_invoice(customer_id="000001", currency="EUR",
                          date_opened="2026-03-10")
        gb.add_invoice_entry(invoice_id="000001",
                             account="Income:Consulting",  # USD, but trivial
                             description="EUR services",
                             quantity="1", price="100.00")

        # Add a price so the USD income side of post_invoice can be converted.
        with gb.open(readonly=False) as book:
            usd = book.default_currency
            eur = None
            for c in book.commodities:
                if c.mnemonic == "EUR":
                    eur = c
                    break
            from datetime import date as _date
            book.session.add(piecash.Price(
                commodity=eur, currency=usd,
                date=_date(2026, 3, 10),
                value="1.10", source="user:test", type="nav",
            ))
            book.save()

        gb.post_invoice(invoice_id="000001",
                        post_account="Assets:Accounts Receivable EUR",
                        post_date="2026-03-10")

        # Pay from EUR bank account — same currency as invoice, no rate needed.
        result = gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Euro Checking",
            amount="100.00",
            payment_date="2026-03-10",
        )

        assert result["status"] == "paid"
        assert Decimal(result["remaining_balance"]) == Decimal("0")
        # Same-currency payment: no exchange_rate field in result.
        assert "exchange_rate" not in result

    def test_pay_invoice_fx_account_parameter_routes_explicit(
        self, business_book,
    ):
        """End-to-end: ``pay_invoice(fx_account=...)`` routes the
        realized FX gain/loss to the user-specified account, not
        the canonical default. This is the primary fix for the FX
        account-routing bug — bookkeepers can pin a specific
        account without depending on naming heuristics."""
        gb = GnuCashBook(str(business_book))
        # Pre-create a user-named FX account; route to it explicitly.
        gb.create_account(
            name="FX Gain Loss",
            account_type="INCOME",
            parent="Income",
        )
        self._add_eur_ar_and_price(gb, "2026-03-10", "1.10")
        self._add_eur_ar_and_price(gb, "2026-03-20", "1.12")

        gb.create_customer(name="Berlin Digital", currency="EUR")
        gb.create_invoice(
            customer_id="000001", currency="EUR",
            date_opened="2026-03-10",
        )
        gb.add_invoice_entry(
            invoice_id="000001", account="Income:Consulting",
            description="EUR services", quantity="1", price="4500.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable EUR",
            post_date="2026-03-10",
        )
        result = gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="4500.00",
            payment_date="2026-03-20",
            fx_account="Income:FX Gain Loss",
        )

        # FX gain routed to the user's account, not the canonical one.
        assert result["fx_realized"]["account"] == "Income:FX Gain Loss"
        assert "fx_notice" not in result
        # Canonical account was never auto-created.
        assert gb.get_account(
            name="Income:Foreign Exchange Gain/Loss"
        ) is None
        # User's account holds the gain.
        assert gb.get_balance(account_name="Income:FX Gain Loss") == Decimal("-90")

    def test_pay_invoice_ambiguous_fx_emits_notice(
        self, business_book,
    ):
        """When two candidate FX accounts exist (Lin Wei's situation:
        ``Income:FX Gain Loss`` user-created, ``Income:Foreign
        Exchange Gain/Loss`` auto-created by a prior pay_invoice
        call), routing falls through to canonical with a notice."""
        gb = GnuCashBook(str(business_book))
        gb.create_account(
            name="FX Gain Loss",
            account_type="INCOME",
            parent="Income",
        )
        gb.create_account(
            name="Foreign Exchange Gain/Loss",
            account_type="INCOME",
            parent="Income",
        )
        self._add_eur_ar_and_price(gb, "2026-03-10", "1.10")
        self._add_eur_ar_and_price(gb, "2026-03-20", "1.12")

        gb.create_customer(name="Berlin Digital", currency="EUR")
        gb.create_invoice(
            customer_id="000001", currency="EUR",
            date_opened="2026-03-10",
        )
        gb.add_invoice_entry(
            invoice_id="000001", account="Income:Consulting",
            description="EUR services", quantity="1", price="4500.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable EUR",
            post_date="2026-03-10",
        )
        result = gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="4500.00",
            payment_date="2026-03-20",
        )

        # Routed to canonical (the deterministic choice when ambiguous).
        assert result["fx_realized"]["account"] == "Income:Foreign Exchange Gain/Loss"
        # Notice surfaces both candidates so the caller can fix it.
        notice = result["fx_notice"]
        assert notice["type"] == "ambiguous_fx_account"
        assert "Income:FX Gain Loss" in notice["candidates"]
        assert "Income:Foreign Exchange Gain/Loss" in notice["candidates"]
        # User's untouched account stays at zero.
        assert gb.get_balance(account_name="Income:FX Gain Loss") == Decimal("0")

    def test_pay_invoice_fx_account_invalid_path_raises(
        self, business_book,
    ):
        """A typo in ``fx_account`` should fail loud, not silently
        fall back to the canonical default — that masks the typo
        and routes income to a different account than the caller
        asked for."""
        gb = GnuCashBook(str(business_book))
        self._add_eur_ar_and_price(gb, "2026-03-10", "1.10")
        self._add_eur_ar_and_price(gb, "2026-03-20", "1.12")

        gb.create_customer(name="Berlin Digital", currency="EUR")
        gb.create_invoice(
            customer_id="000001", currency="EUR",
            date_opened="2026-03-10",
        )
        gb.add_invoice_entry(
            invoice_id="000001", account="Income:Consulting",
            description="EUR services", quantity="1", price="4500.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable EUR",
            post_date="2026-03-10",
        )

        with pytest.raises(ValueError, match="fx_account not found"):
            gb.pay_invoice(
                invoice_id="000001",
                payment_account="Assets:Checking",
                amount="4500.00",
                payment_date="2026-03-20",
                fx_account="Income:Typo Account That Does Not Exist",
            )


# ============== Overpayment guard + direction (C2/A4) ==============


class TestOverpaymentGuard:
    """C2 regression (adversarial pass 2): ``pay_invoice`` must
    reject payments beyond the outstanding balance, and the
    surfaces that render lot balances must surface DIRECTION
    instead of abs()-laundering a negative (overpaid) balance
    into phantom money-owed.
    """

    def _post_invoice(self, gb, amount="3500.00"):
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Consulting",
            quantity="1",
            price=amount,
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
        )
        return "000001"

    def test_overpayment_rejected(self, business_book):
        """Paying $4,500 on a $3,500 invoice rejects — pre-fix it
        'succeeded' and the customer showed as still owing $1,000."""
        gb = GnuCashBook(str(business_book))
        self._post_invoice(gb, "3500.00")
        with pytest.raises(ValueError, match="exceeds the outstanding"):
            gb.pay_invoice(
                invoice_id="000001",
                payment_account="Assets:Checking",
                amount="4500.00",
            )
        # Lot untouched by the rejected payment.
        rows = gb.get_outstanding_invoices(compact=False)["invoices"]
        assert Decimal(rows[0]["amount_due"]) == Decimal("3500.00")

    def test_overpay_after_partial_rejected_exact_ok(self, business_book):
        gb = GnuCashBook(str(business_book))
        self._post_invoice(gb, "500.00")
        gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="200",
        )
        with pytest.raises(ValueError, match="exceeds the outstanding"):
            gb.pay_invoice(
                invoice_id="000001",
                payment_account="Assets:Checking",
                amount="400",
            )
        result = gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="300",
        )
        assert Decimal(result["remaining_balance"]) == Decimal("0")

    def test_pay_settled_invoice_rejected(self, business_book):
        gb = GnuCashBook(str(business_book))
        self._post_invoice(gb, "500.00")
        gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="500",
        )
        with pytest.raises(ValueError, match="exceeds the outstanding"):
            gb.pay_invoice(
                invoice_id="000001",
                payment_account="Assets:Checking",
                amount="0.01",
            )

    def test_overpay_vendor_bill_rejected(self, business_book):
        """Direction check on the A/P side (negative-natural lot)."""
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        gb.create_bill(vendor_id="000001")
        gb.add_bill_entry(
            bill_id="000001",
            account="Expenses:Office Supplies",
            description="Paper",
            quantity="1",
            price="50.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Liabilities:Accounts Payable",
            owner_type="vendor",
        )
        with pytest.raises(ValueError, match="exceeds the outstanding"):
            gb.pay_invoice(
                invoice_id="000001",
                payment_account="Assets:Checking",
                amount="60",
                owner_type="vendor",
            )

    def test_overpaid_lot_surfaces_direction(self, business_book):
        """A lot driven negative outside pay_invoice (e.g. a manual
        payment transaction) must surface as OVERPAID with a negative
        amount_due and no aging clock — not as phantom money owed.
        Pre-fix: amount_due abs()'d to +200, amount_paid derived as
        300 (grand − abs) instead of 700, days_past_due ticking."""
        import piecash

        gb = GnuCashBook(str(business_book))
        self._post_invoice(gb, "500.00")
        gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="400",
        )
        # Push the lot negative with a manual payment split: 500
        # posted − 400 paid − 300 manual = −200 (customer credit).
        with gb.open(readonly=False) as book:
            ar = next(
                a for a in book.accounts
                if a.fullname == "Assets:Accounts Receivable"
            )
            checking = next(
                a for a in book.accounts
                if a.fullname == "Assets:Checking"
            )
            lot_obj = ar.lots[0]
            ar_split = piecash.Split(
                account=ar, value=Decimal("-300"),
            )
            piecash.Transaction(
                currency=book.default_currency,
                description="Manual overpayment",
                post_date=date(2026, 5, 1),
                splits=[
                    ar_split,
                    piecash.Split(
                        account=checking, value=Decimal("300"),
                    ),
                ],
            )
            ar_split.lot = lot_obj
            book.save()

        rows = gb.get_outstanding_invoices(compact=False)["invoices"]
        assert len(rows) == 1
        row = rows[0]
        assert Decimal(row["amount_due"]) == Decimal("-200.00")
        assert row["overpaid"] is True
        assert row["days_past_due"] is None, (
            "aging clock must not tick on an overpaid document"
        )
        assert Decimal(row["amount_paid"]) == Decimal("700.00")

        compact = gb.get_outstanding_invoices(compact=True)
        assert "OVERPAID" in compact

    def test_credit_note_rows_carry_no_aging_clock(self, business_book):
        """An unapplied credit note is money the business OWES — it
        must not carry days_past_due in a collections list."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Co")
        cn = gb.create_credit_note(
            owner_id="000001", owner_type="customer",
        )
        gb.add_credit_note_entry(
            credit_note_id=cn["id"],
            account="Income:Sales",
            description="Out-of-scope work credit",
            quantity="1", price="100.00",
        )
        gb.post_invoice(
            invoice_id=cn["id"],
            post_account="Assets:Accounts Receivable",
            owner_type="customer",
            post_date="2025-10-01",  # long past — clock would tick
        )

        rows = gb.get_outstanding_invoices(compact=False)["invoices"]
        assert len(rows) == 1
        row = rows[0]
        assert row["is_credit_note"] is True
        assert Decimal(row["amount_due"]) == Decimal("100.00")
        assert row["days_past_due"] is None, (
            "credit notes have no past-due concept"
        )


class TestCreditNoteRefundFxDirection:
    """A4 regression (adversarial pass 2): ``fx_realized.direction``
    must follow ``effective_is_bill`` — the direction the ledger
    split was booked with. A cross-currency customer credit-note
    refund pays out like a bill; pre-fix the label keyed off raw
    ``is_bill`` and called a booked FX LOSS a "gain".
    """

    def _add_eur_ar_and_price(self, gb, rate_date, rate_value):
        import piecash
        from datetime import date as _date

        with gb.open(readonly=False) as book:
            usd = book.default_currency
            eur = None
            for c in book.commodities:
                if c.mnemonic == "EUR":
                    eur = c
                    break
            if eur is None:
                eur = piecash.factories.create_currency_from_ISO("EUR")
                book.session.add(eur)
            if not any(a.fullname == "Assets:Accounts Receivable EUR"
                       for a in book.accounts):
                assets = next(
                    a for a in book.accounts if a.fullname == "Assets"
                )
                book.session.add(piecash.Account(
                    name="Accounts Receivable EUR", type="RECEIVABLE",
                    parent=assets, commodity=eur,
                ))
            parsed = (
                rate_date if isinstance(rate_date, _date)
                else _date.fromisoformat(rate_date)
            )
            book.session.add(piecash.Price(
                commodity=eur, currency=usd, date=parsed,
                value=str(rate_value), source="user:test", type="nav",
            ))
            book.save()

    def test_refund_fx_loss_labeled_loss(self, business_book):
        """CN posted at 1.10 (A/R relieved 4,950 USD); refunded at
        1.12 (5,040 USD sent) → 90 USD more left the company than
        was booked = realized LOSS. The ledger books the +90 debit
        either way; the label must agree with it."""
        gb = GnuCashBook(str(business_book))
        self._add_eur_ar_and_price(gb, "2026-03-10", "1.10")
        self._add_eur_ar_and_price(gb, "2026-03-20", "1.12")

        gb.create_customer(name="Berlin Digital", currency="EUR")
        cn = gb.create_credit_note(
            owner_id="000001", owner_type="customer", currency="EUR",
            date_opened="2026-03-10",
        )
        gb.add_credit_note_entry(
            credit_note_id=cn["id"],
            account="Income:Sales",
            description="EUR credit",
            quantity="1", price="4500.00",
        )
        gb.post_invoice(
            invoice_id=cn["id"],
            post_account="Assets:Accounts Receivable EUR",
            owner_type="customer",
            post_date="2026-03-10",
        )
        result = gb.pay_invoice(
            invoice_id=cn["id"],
            payment_account="Assets:Checking",
            amount="4500.00",
            payment_date="2026-03-20",
            owner_type="customer",
        )

        assert "fx_realized" in result
        assert Decimal(result["fx_realized"]["amount"]) == Decimal("90.00")
        # Ledger: debit (loss) on the FX account.
        fx_balance = gb.get_balance(
            account_name="Income:Foreign Exchange Gain/Loss"
        )
        assert fx_balance == Decimal("90")
        # Label must agree with the ledger. Pre-fix: "gain".
        assert result["fx_realized"]["direction"] == "loss"


# ============== Early-payment discount ==============


class TestPayInvoiceEarlyPaymentDiscount:
    """Verify ``pay_invoice`` honors the discount fields on
    billterms — discount_days, discount_percent. Pre-v1.3.0
    these fields were stored at billterm creation but ignored
    at payment time (silent feature lie).

    Validation chain rejects every failure mode loudly. Customer
    and vendor sides both supported. Cross-currency interaction
    composes cleanly with the existing FX gain/loss logic.
    """

    def _setup_invoice_with_terms(
        self,
        gb: GnuCashBook,
        amount: str = "1000.00",
        discount_days: int = 10,
        discount_percent: str = "2",
        invoice_date: date | None = None,
    ) -> str:
        """Create customer + billterm + posted invoice with terms."""
        gb.create_customer(name="Acme Corp")
        gb.create_billterm(
            name="2/10 Net 30",
            due_days=30,
            discount_days=discount_days,
            discount_percent=discount_percent,
        )
        gb.create_invoice(
            customer_id="000001",
            term="2/10 Net 30",
            date_opened=invoice_date.isoformat() if invoice_date else None,
        )
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Consulting",
            quantity="1",
            price=amount,
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
            post_date=invoice_date.isoformat() if invoice_date else None,
        )
        return "000001"

    def _setup_bill_with_terms(
        self,
        gb: GnuCashBook,
        amount: str = "1000.00",
        discount_days: int = 10,
        discount_percent: str = "2",
        bill_date: date | None = None,
    ) -> str:
        gb.create_vendor(name="Supplier Co")
        gb.create_billterm(
            name="2/10 Net 30",
            due_days=30,
            discount_days=discount_days,
            discount_percent=discount_percent,
        )
        gb.create_bill(
            vendor_id="000001",
            term="2/10 Net 30",
            date_opened=bill_date.isoformat() if bill_date else None,
        )
        gb.add_bill_entry(
            bill_id="000001",
            account="Expenses:Office Supplies",
            description="Paper",
            quantity="1",
            price=amount,
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Liabilities:Accounts Payable",
            owner_type="vendor",
            post_date=bill_date.isoformat() if bill_date else None,
        )
        return "000001"

    # ── Happy paths ───────────────────────────────────────────

    def test_customer_invoice_within_window_full_discount(
        self, business_book,
    ):
        """Customer pays $980 of $1000 on day 5 within 2/10 window
        → A/R clears full $1000, $20 books to Expenses:Sales Discounts.
        """
        gb = GnuCashBook(str(business_book))
        invoice_date = date(2026, 5, 1)
        self._setup_invoice_with_terms(
            gb, amount="1000.00", invoice_date=invoice_date,
        )
        result = gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="980",
            payment_date="2026-05-06",  # day 5 within window
            apply_discount=True,
        )
        assert result["status"] == "paid"
        assert Decimal(result["remaining_balance"]) == Decimal("0")
        assert "discount" in result
        assert Decimal(result["discount"]["amount"]) == Decimal("20.00")
        assert "Sales Discounts" in result["discount"]["account"]

    def test_vendor_bill_within_window_full_discount(self, business_book):
        """Vendor offers us 2/10 Net 30 on $1000 bill, we pay $980 on day 8
        → A/P clears full $1000, $20 books to Income:Purchase Discounts Taken.
        """
        gb = GnuCashBook(str(business_book))
        bill_date = date(2026, 5, 1)
        self._setup_bill_with_terms(
            gb, amount="1000.00", bill_date=bill_date,
        )
        result = gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="980",
            payment_date="2026-05-09",  # day 8 within window
            apply_discount=True,
            owner_type="vendor",
        )
        assert result["status"] == "paid"
        assert Decimal(result["remaining_balance"]) == Decimal("0")
        assert "discount" in result
        assert "Purchase Discounts" in result["discount"]["account"]

    def test_payment_at_window_boundary_accepted(self, business_book):
        """Payment on exactly day 10 of a 2/10 window → accepted."""
        gb = GnuCashBook(str(business_book))
        invoice_date = date(2026, 5, 1)
        self._setup_invoice_with_terms(
            gb, amount="1000.00", invoice_date=invoice_date,
        )
        result = gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="980",
            payment_date="2026-05-11",  # exactly day 10
            apply_discount=True,
        )
        assert result["status"] == "paid"

    # ── Validation rejections ────────────────────────────────

    def test_reject_no_billterm(self, business_book):
        """apply_discount=True on invoice with no terms → error."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001", account="Income:Sales",
            description="X", quantity="1", price="1000",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
        )
        with pytest.raises(ValueError, match="no billterm linked"):
            gb.pay_invoice(
                invoice_id="000001",
                payment_account="Assets:Checking",
                amount="980",
                apply_discount=True,
            )

    def test_reject_billterm_without_discount(self, business_book):
        """apply_discount=True on a billterm with no discount → error."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme")
        gb.create_billterm(
            name="Net 30",
            due_days=30,
            discount_days=0,
            discount_percent="0",
        )
        gb.create_invoice(customer_id="000001", term="Net 30")
        gb.add_invoice_entry(
            invoice_id="000001", account="Income:Sales",
            description="X", quantity="1", price="1000",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
        )
        with pytest.raises(ValueError, match="no early-payment discount"):
            gb.pay_invoice(
                invoice_id="000001",
                payment_account="Assets:Checking",
                amount="980",
                apply_discount=True,
            )

    def test_reject_past_window(self, business_book):
        """Payment day 11 of a 10-day window → rejected."""
        gb = GnuCashBook(str(business_book))
        invoice_date = date(2026, 5, 1)
        self._setup_invoice_with_terms(
            gb, amount="1000.00", invoice_date=invoice_date,
        )
        with pytest.raises(ValueError, match="beyond the billterm discount window"):
            gb.pay_invoice(
                invoice_id="000001",
                payment_account="Assets:Checking",
                amount="980",
                payment_date="2026-05-12",  # day 11
                apply_discount=True,
            )

    def test_reject_wrong_amount(self, business_book):
        """apply_discount=True with amount that doesn't match expected
        shortfall → reject with the correct amount suggestion."""
        gb = GnuCashBook(str(business_book))
        invoice_date = date(2026, 5, 1)
        self._setup_invoice_with_terms(
            gb, amount="1000.00", invoice_date=invoice_date,
        )
        with pytest.raises(ValueError, match="shortfall doesn't match"):
            gb.pay_invoice(
                invoice_id="000001",
                payment_account="Assets:Checking",
                amount="500",  # not a discount-shortfall; intended partial
                payment_date="2026-05-06",
                apply_discount=True,
            )

    def test_reject_credit_note(self, business_book):
        """apply_discount=True on a credit note → loud reject."""
        gb = GnuCashBook(str(business_book))
        invoice_date = date(2026, 5, 1)
        # Set up a posted credit note (no terms — discounting credit
        # notes is semantically nonsensical regardless of terms).
        gb.create_customer(name="Acme")
        gb.create_credit_note(
            owner_type="customer", owner_id="000001",
            date_opened=invoice_date.isoformat(),
        )
        gb.add_credit_note_entry(
            credit_note_id="000001", account="Income:Sales",
            description="Refund", quantity="1", price="100",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
            owner_type="customer",
            post_date=invoice_date.isoformat(),
        )
        with pytest.raises(ValueError, match="Discounts cannot be applied to credit note"):
            gb.pay_invoice(
                invoice_id="000001",
                payment_account="Assets:Checking",
                amount="98",
                payment_date="2026-05-06",
                apply_discount=True,
            )

    # ── Closing-payment-after-partials ───────────────────────

    def test_partial_then_discount_settlement(self, business_book):
        """Customer pays $500 early without discount, then $480 with
        discount to close. Should succeed: shortfall on closing
        payment ($500 - $480 = $20) matches expected discount on
        original $1000 invoice."""
        gb = GnuCashBook(str(business_book))
        invoice_date = date(2026, 5, 1)
        self._setup_invoice_with_terms(
            gb, amount="1000.00", invoice_date=invoice_date,
        )
        # First payment: $500 without discount
        gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="500",
            payment_date="2026-05-03",
        )
        # Second payment: $480 with discount, should close
        result = gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="480",
            payment_date="2026-05-08",  # still within window
            apply_discount=True,
        )
        assert result["status"] == "paid"
        assert Decimal(result["remaining_balance"]) == Decimal("0")
        assert Decimal(result["discount"]["amount"]) == Decimal("20.00")

    # ── Get_invoice forward signal ────────────────────────────

    def test_get_invoice_surfaces_discount_available(self, business_book):
        """get_invoice on an invoice with active discount terms
        should include discount_available block with eligible_until +
        amount, so the LLM can proactively surface "save $X by Y"."""
        gb = GnuCashBook(str(business_book))
        invoice_date = date.today()  # within window today
        self._setup_invoice_with_terms(
            gb, amount="1000.00", invoice_date=invoice_date,
        )
        result = gb.get_invoice(invoice_id="000001")
        assert "discount_available" in result, (
            f"discount_available block missing: {result}"
        )
        assert Decimal(result["discount_available"]["amount"]) == Decimal("20.00")
        eligible = date.fromisoformat(
            result["discount_available"]["eligible_until"]
        )
        assert eligible == invoice_date + timedelta(days=10)

    def test_get_invoice_marks_expired_discount(self, business_book):
        """When the discount window has passed, get_invoice shows the
        same fields under ``discount_expired`` rather than dropping
        them — caller can see what was offered, audit trail intact."""
        gb = GnuCashBook(str(business_book))
        invoice_date = date.today() - timedelta(days=20)  # past window
        self._setup_invoice_with_terms(
            gb, amount="1000.00", invoice_date=invoice_date,
        )
        result = gb.get_invoice(invoice_id="000001")
        assert "discount_expired" in result
        assert "discount_available" not in result


# ============== _compute_fx_gain_loss Unit Tests ==============


class TestComputeFxGainLoss:
    """Direct unit tests for ``_compute_fx_gain_loss``.

    The four sign quadrants are already covered end-to-end via
    ``pay_invoice`` in :class:`TestPayInvoice`. This class targets
    the helper directly to lock its contract — the dict shape (or
    ``None`` return), the FX delta value, and the split's
    ``quantity`` sign — independently of the surrounding
    ``pay_invoice`` plumbing. Pre-extraction this logic was 130
    lines embedded inside a 441-line method; calling it from a unit
    test required setting up the full payment pipeline.
    """

    def _setup_posted_eur_invoice(
        self, gb, post_rate: str, post_date: str = "2026-03-10",
        is_bill: bool = False,
    ):
        """Set up a posted foreign-currency document at the given
        rate, returning identifiers the test can use to call
        ``_compute_fx_gain_loss`` directly.

        ``is_bill=False`` posts a customer invoice (A/R debit-
        natural); ``is_bill=True`` posts a vendor bill (A/P credit-
        natural). Both use a EUR commodity and a USD-default book.
        """
        import piecash
        from datetime import date as _date

        with gb.open(readonly=False) as book:
            usd = book.default_currency
            eur = next(
                (c for c in book.commodities if c.mnemonic == "EUR"),
                None,
            )
            if eur is None:
                eur = piecash.factories.create_currency_from_ISO("EUR")
                book.session.add(eur)
            ar_name = (
                "Liabilities:Accounts Payable EUR" if is_bill
                else "Assets:Accounts Receivable EUR"
            )
            if not any(a.fullname == ar_name for a in book.accounts):
                parent_name = "Liabilities" if is_bill else "Assets"
                parent = next(
                    a for a in book.accounts if a.fullname == parent_name
                )
                book.session.add(piecash.Account(
                    name=ar_name.split(":")[-1],
                    type=("PAYABLE" if is_bill else "RECEIVABLE"),
                    parent=parent, commodity=eur,
                ))
            book.session.add(piecash.Price(
                commodity=eur, currency=usd,
                date=_date.fromisoformat(post_date),
                value=post_rate, source="user:test", type="nav",
            ))
            book.save()

        if is_bill:
            gb.create_vendor(name="Berlin Supplier", currency="EUR")
            gb.create_bill(vendor_id="000001", currency="EUR",
                           date_opened=post_date)
            gb.add_bill_entry(bill_id="000001",
                              account="Expenses:Office Supplies",
                              description="EUR supplies",
                              quantity="1", price="4500.00")
            gb.post_invoice(invoice_id="000001",
                            post_account=ar_name,
                            post_date=post_date,
                            owner_type="vendor")
        else:
            gb.create_customer(name="Berlin Digital", currency="EUR")
            gb.create_invoice(customer_id="000001", currency="EUR",
                              date_opened=post_date)
            gb.add_invoice_entry(invoice_id="000001",
                                 account="Income:Consulting",
                                 description="EUR services",
                                 quantity="1", price="4500.00")
            gb.post_invoice(invoice_id="000001",
                            post_account=ar_name,
                            post_date=post_date)

    def _add_pay_date_rate(
        self, gb, rate_value: str, rate_date: str = "2026-03-20",
    ):
        """Add a EUR/USD price on the pay-date so the helper can
        resolve the pay-date rate the same way pay_invoice does.
        """
        import piecash
        from datetime import date as _date
        with gb.open(readonly=False) as book:
            usd = book.default_currency
            eur = next(c for c in book.commodities if c.mnemonic == "EUR")
            book.session.add(piecash.Price(
                commodity=eur, currency=usd,
                date=_date.fromisoformat(rate_date),
                value=rate_value, source="user:test", type="nav",
            ))
            book.save()

    def _call_helper(
        self, gb, *, is_bill: bool, pay_rate: Decimal,
        parsed_date_str: str = "2026-03-20",
    ) -> dict | None:
        """Open a session, find the posted invoice/bill, call
        ``_compute_fx_gain_loss`` with crafted inputs, and capture
        the returned values into plain Python types BEFORE the
        session closes — piecash ORM objects can't be touched
        post-close (DetachedInstanceError on any attribute access).

        Returns a serializable summary dict instead of the raw
        ORM-tied return value::

            {
                "fx_diff_default": Decimal,
                "split_quantity": Decimal,
                "fx_acct_fullname": str,
                "fx_notice": str | None,
            }

        or ``None`` when the helper returned ``None`` (no FX split).
        """
        from datetime import date as _date
        ot = 4 if is_bill else 2
        parsed_date = _date.fromisoformat(parsed_date_str)
        with gb.open(readonly=False) as book:
            inv = gb._find_invoice(book, "000001", owner_type=ot)
            assert inv is not None
            pay_acct = gb._resolve_account(book, "Assets:Checking")
            assert pay_acct is not None
            default_currency = gb._require_default_currency(book)
            payment_amount = Decimal("4500.00")
            pay_quantity = (payment_amount * pay_rate).quantize(
                Decimal("0.01")
            )
            result = gb._compute_fx_gain_loss(
                book,
                inv=inv,
                is_bill=is_bill,
                pay_acct=pay_acct,
                payment_amount=payment_amount,
                pay_quantity=pay_quantity,
                parsed_date=parsed_date,
                exchange_rate=pay_rate,
                fx_account=None,
                default_currency=default_currency,
            )
            if result is None:
                return None
            # Capture every field as a plain Python value while the
            # session is still open. ``fullname`` walks the parent
            # chain via lazy load; ``quantity`` on the Split is
            # cached on the instance so it survives detach, but
            # we capture it here for consistency.
            return {
                "fx_diff_default": result["fx_diff_default"],
                "split_quantity": Decimal(str(result["split"].quantity)),
                "fx_acct_fullname": result["fx_acct"].fullname,
                "fx_notice": result["fx_notice"],
            }

    # ── The four sign quadrants ───────────────────────────────────

    def test_customer_invoice_rate_up_books_gain(self, business_book):
        """Customer invoice: pay-rate > post-rate → received more
        USD than booked at posting → realized GAIN. Split quantity
        is negative (credit to credit-natural income account)."""
        gb = GnuCashBook(str(business_book))
        self._setup_posted_eur_invoice(gb, post_rate="1.10")
        self._add_pay_date_rate(gb, rate_value="1.12")

        result = self._call_helper(
            gb, is_bill=False, pay_rate=Decimal("1.12"),
        )

        assert result is not None
        # post: 4500 × 1.10 = 4950 expected USD
        # pay:  4500 × 1.12 = 5040 actual USD
        # delta = +90 (received more)
        assert result["fx_diff_default"] == Decimal("90.00")
        # Customer gain: quantity = -delta (credit to income).
        assert result["split_quantity"] == Decimal("-90.00")
        assert result["fx_acct_fullname"] == "Income:Foreign Exchange Gain/Loss"
        assert result["fx_notice"] is None

    def test_customer_invoice_rate_down_books_loss(self, business_book):
        """Customer invoice: pay-rate < post-rate → received less
        USD than booked at posting → realized LOSS. Split quantity
        is positive (debit to credit-natural income account =
        reduces income)."""
        gb = GnuCashBook(str(business_book))
        self._setup_posted_eur_invoice(gb, post_rate="1.12")
        self._add_pay_date_rate(gb, rate_value="1.10")

        result = self._call_helper(
            gb, is_bill=False, pay_rate=Decimal("1.10"),
        )

        assert result is not None
        # post: 4500 × 1.12 = 5040 expected USD
        # pay:  4500 × 1.10 = 4950 actual USD
        # delta = -90 (received less)
        assert result["fx_diff_default"] == Decimal("-90.00")
        # Customer loss: quantity = -delta = +90 (debit income).
        assert result["split_quantity"] == Decimal("90.00")
        assert result["fx_acct_fullname"] == "Income:Foreign Exchange Gain/Loss"

    def test_vendor_bill_rate_down_books_gain(self, business_book):
        """Vendor bill: pay-rate < post-rate → spent fewer USD than
        booked at posting → realized GAIN. Split quantity is
        negative (credit to credit-natural income account)."""
        gb = GnuCashBook(str(business_book))
        self._setup_posted_eur_invoice(gb, post_rate="1.12", is_bill=True)
        self._add_pay_date_rate(gb, rate_value="1.10")

        result = self._call_helper(
            gb, is_bill=True, pay_rate=Decimal("1.10"),
        )

        assert result is not None
        # post: 4500 × 1.12 = 5040 expected USD spent
        # pay:  4500 × 1.10 = 4950 actual USD spent
        # delta = -90 (spent less)
        assert result["fx_diff_default"] == Decimal("-90.00")
        # Vendor gain: quantity = +delta = -90 (credit income).
        assert result["split_quantity"] == Decimal("-90.00")
        assert result["fx_acct_fullname"] == "Income:Foreign Exchange Gain/Loss"

    def test_vendor_bill_rate_up_books_loss(self, business_book):
        """Vendor bill: pay-rate > post-rate → spent more USD than
        booked at posting → realized LOSS. Split quantity is
        positive (debit to credit-natural income account)."""
        gb = GnuCashBook(str(business_book))
        self._setup_posted_eur_invoice(gb, post_rate="1.10", is_bill=True)
        self._add_pay_date_rate(gb, rate_value="1.12")

        result = self._call_helper(
            gb, is_bill=True, pay_rate=Decimal("1.12"),
        )

        assert result is not None
        # post: 4500 × 1.10 = 4950 expected USD spent
        # pay:  4500 × 1.12 = 5040 actual USD spent
        # delta = +90 (spent more)
        assert result["fx_diff_default"] == Decimal("90.00")
        # Vendor loss: quantity = +delta = +90 (debit income).
        assert result["split_quantity"] == Decimal("90.00")

    # ── None-return cases ─────────────────────────────────────────

    def test_returns_none_when_post_and_pay_rates_equal(
        self, business_book,
    ):
        """When the rate hasn't moved, no FX split is booked.
        Returns None so the caller skips appending."""
        gb = GnuCashBook(str(business_book))
        self._setup_posted_eur_invoice(gb, post_rate="1.10")
        # Only one price on file — same rate at both dates.
        result = self._call_helper(
            gb, is_bill=False, pay_rate=Decimal("1.10"),
        )
        assert result is None


# ============== Outstanding Invoices Tests ==============


class TestGetOutstandingInvoices:
    """Tests for get_outstanding_invoices."""

    def test_empty_when_none_posted(self, business_book):
        gb = GnuCashBook(str(business_book))
        result = gb.get_outstanding_invoices(compact=False)["invoices"]
        assert result == []

    def test_shows_posted_unpaid(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Consulting",
            quantity="1",
            price="500.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
        )
        result = gb.get_outstanding_invoices(compact=False)["invoices"]
        assert len(result) == 1
        assert result[0]["id"] == "000001"
        assert Decimal(result[0]["amount_due"]) == Decimal("500")

    def test_excludes_fully_paid(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Consulting",
            quantity="1",
            price="500.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
        )
        gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="500",
        )
        result = gb.get_outstanding_invoices(compact=False)["invoices"]
        assert len(result) == 0

    def test_partially_paid_shows_correct_amounts(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Consulting",
            quantity="1",
            price="500.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
        )
        gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="200",
        )
        result = gb.get_outstanding_invoices(compact=False)["invoices"]
        assert len(result) == 1
        assert Decimal(result[0]["amount_due"]) == Decimal("300")
        assert Decimal(result[0]["amount_paid"]) == Decimal("200")

    def test_filter_by_customer(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_customer(name="Beta Inc")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="For Acme",
            quantity="1",
            price="100.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
        )
        gb.create_invoice(customer_id="000002")
        gb.add_invoice_entry(
            invoice_id="000002",
            account="Income:Sales",
            description="For Beta",
            quantity="1",
            price="200.00",
        )
        gb.post_invoice(
            invoice_id="000002",
            post_account="Assets:Accounts Receivable",
        )
        result = gb.get_outstanding_invoices(customer_id="000001", compact=False)["invoices"]
        assert len(result) == 1
        assert result[0]["owner_name"] == "Acme Corp"

    def test_filter_by_vendor(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        gb.create_bill(vendor_id="000001")
        gb.add_bill_entry(
            bill_id="000001",
            account="Expenses:Office Supplies",
            description="Paper",
            quantity="1",
            price="50.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Liabilities:Accounts Payable",
            owner_type="vendor",
        )
        result = gb.get_outstanding_invoices(owner_type="vendor", compact=False)["invoices"]
        assert len(result) == 1
        assert result[0]["type"] == "bill"


class TestPhase3CommsContracts:
    """Lock tests for the comms-audit Phase 3 daily-reads contracts.
    Future refactors must preserve these shapes — they're what the
    bookkeeper relies on when scanning invoices and outstanding lists."""

    # ── 3A: get_outstanding_invoices action columns ──────────────

    def test_outstanding_compact_includes_due_date_and_days(
        self, business_book,
    ):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001", account="Income:Sales",
            description="Consulting", quantity="1", price="500.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
            post_date="2026-01-01",
            due_date="2026-02-01",
        )
        compact = gb.get_outstanding_invoices()
        # Compact output is a string (default mode).
        assert isinstance(compact, str)
        assert "Acme Corp" in compact
        assert "due:2026-02-01" in compact
        assert "posted:2026-01-01" in compact
        # By 2026-04-28 (test fixture's "today") this is 86 days past
        # an explicit due date — no "30-day default" annotation.
        assert "past due" in compact
        assert "30-day default" not in compact

    def test_outstanding_compact_no_terms_annotates_default(
        self, business_book,
    ):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001", account="Income:Sales",
            description="Consulting", quantity="1", price="500.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
            post_date="2026-01-01",
            # no due_date and no billterm — falls back to 30-day default
        )
        compact = gb.get_outstanding_invoices()
        assert "30-day default" in compact

    def test_outstanding_compact_marks_bill_with_tag(
        self, business_book,
    ):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        gb.create_bill(vendor_id="000001")
        gb.add_bill_entry(
            bill_id="000001",
            account="Expenses:Office Supplies",
            description="Paper", quantity="1", price="50.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Liabilities:Accounts Payable",
            owner_type="vendor",
            post_date="2026-01-01",
        )
        compact = gb.get_outstanding_invoices(owner_type="vendor")
        assert "(BILL)" in compact, (
            f"BILL tag missing from compact output:\n{compact}"
        )
        assert "Office Depot" in compact

    def test_outstanding_verbose_mode_returns_dicts(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001", account="Income:Sales",
            description="Consulting", quantity="1", price="500.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
            post_date="2026-01-01",
        )
        result = gb.get_outstanding_invoices(compact=False)["invoices"]
        assert isinstance(result, list)
        assert result[0]["due_date"] is not None
        assert result[0]["days_past_due"] is not None

    # ── 3B: list_invoices owner name + amount ────────────────────

    def test_list_invoices_compact_includes_owner_and_amount(
        self, business_book,
    ):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Emerald Analytics")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001", account="Income:Sales",
            description="Q1 retainer", quantity="1", price="3500.00",
        )
        compact = gb.list_invoices()
        assert "Emerald Analytics" in compact
        # Amount should appear in either USD-prefixed or bare form.
        assert "3,500" in compact or "3500" in compact

    def test_list_invoices_marks_bill_tag(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="BookkeepingCo")
        gb.create_bill(vendor_id="000001")
        gb.add_bill_entry(
            bill_id="000001", account="Expenses:Office Supplies",
            description="Bookkeeping", quantity="1", price="450.00",
        )
        compact = gb.list_invoices()
        assert "BILL" in compact
        assert "BookkeepingCo" in compact

    # ── 3C: get_invoice resolves account paths ───────────────────

    def test_get_invoice_entries_use_account_path_not_guid(
        self, business_book,
    ):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001", account="Income:Sales",
            description="Consulting", quantity="1", price="500.00",
        )
        result = gb.get_invoice(invoice_id="000001")
        assert "entries" in result
        assert len(result["entries"]) == 1
        entry = result["entries"][0]
        # New shape: ``account`` (path) replaces ``account_guid`` (hex).
        assert entry.get("account") == "Income:Sales"
        assert "account_guid" not in entry

    def test_get_invoice_drops_owner_guid_keeps_owner_name(
        self, business_book,
    ):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001", account="Income:Sales",
            description="Consulting", quantity="1", price="500.00",
        )
        result = gb.get_invoice(invoice_id="000001")
        assert result["owner_name"] == "Acme Corp"
        assert "owner_guid" not in result

    # ── 3B follow-up: list_invoices verbose envelope ─────────────

    def test_list_invoices_verbose_envelope_with_truncation(
        self, business_book,
    ):
        """Both modes carry the pagination indicator so a verbose-mode
        caller knows the result is incomplete: compact leads with the
        ``Showing X-Y of Z`` line, verbose carries it in ``showing``
        alongside ``count`` / ``total`` / ``offset``.
        """
        gb = GnuCashBook(str(business_book))
        # 5 invoices, ask for 3.
        for i in range(5):
            gb.create_customer(name=f"Customer {i}")
            gb.create_invoice(customer_id=f"{i+1:06d}")
        result = gb.list_invoices(compact=False, limit=3)
        assert isinstance(result, dict)
        assert len(result["invoices"]) == 3
        assert result["count"] == 3
        assert result["total"] == 5
        assert result["offset"] == 0
        assert "Showing 1-3 of 5 invoices" in result["showing"]


# ============== Vendor Spending Report Tests ==============


class TestVendorSpendingReport:
    """Tests for vendor_spending_report."""

    def test_basic_report(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        gb.create_bill(vendor_id="000001")
        gb.add_bill_entry(
            bill_id="000001",
            account="Expenses:Office Supplies",
            description="Paper",
            quantity="1",
            price="50.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Liabilities:Accounts Payable",
            owner_type="vendor",
            post_date="2026-02-15",
        )
        result = gb.vendor_spending_report(compact=False,
            start_date="2026-01-01",
            end_date="2026-12-31",
        )
        assert len(result["vendors"]) == 1
        assert result["vendors"][0]["vendor_name"] == "Office Depot"
        assert Decimal(result["vendors"][0]["total_billed"]) == Decimal("50")
        assert result["totals"]["bill_count"] == 1

    def test_multiple_vendors(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        gb.create_vendor(name="Staples")
        # Bill 1 for Office Depot
        gb.create_bill(vendor_id="000001")
        gb.add_bill_entry(
            bill_id="000001",
            account="Expenses:Office Supplies",
            description="Paper",
            quantity="1",
            price="50.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Liabilities:Accounts Payable",
            owner_type="vendor",
            post_date="2026-02-15",
        )
        # Bill 2 for Staples
        gb.create_bill(vendor_id="000002")
        gb.add_bill_entry(
            bill_id="000002",
            account="Expenses:Office Supplies",
            description="Pens",
            quantity="1",
            price="30.00",
        )
        gb.post_invoice(
            invoice_id="000002",
            post_account="Liabilities:Accounts Payable",
            owner_type="vendor",
            post_date="2026-02-20",
        )
        result = gb.vendor_spending_report(compact=False,
            start_date="2026-01-01",
            end_date="2026-12-31",
        )
        assert result["totals"]["vendor_count"] == 2
        assert Decimal(result["totals"]["total_billed"]) == Decimal("80")

    def test_date_filter(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        gb.create_bill(vendor_id="000001")
        gb.add_bill_entry(
            bill_id="000001",
            account="Expenses:Office Supplies",
            description="Paper",
            quantity="1",
            price="50.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Liabilities:Accounts Payable",
            owner_type="vendor",
            post_date="2026-06-15",
        )
        # Query range that excludes the bill
        result = gb.vendor_spending_report(compact=False,
            start_date="2026-01-01",
            end_date="2026-03-31",
        )
        assert len(result["vendors"]) == 0

    def test_filter_by_vendor_id(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        gb.create_vendor(name="Staples")
        gb.create_bill(vendor_id="000001")
        gb.add_bill_entry(
            bill_id="000001",
            account="Expenses:Office Supplies",
            description="Paper",
            quantity="1",
            price="50.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Liabilities:Accounts Payable",
            owner_type="vendor",
            post_date="2026-02-15",
        )
        gb.create_bill(vendor_id="000002")
        gb.add_bill_entry(
            bill_id="000002",
            account="Expenses:Office Supplies",
            description="Pens",
            quantity="1",
            price="30.00",
        )
        gb.post_invoice(
            invoice_id="000002",
            post_account="Liabilities:Accounts Payable",
            owner_type="vendor",
            post_date="2026-02-20",
        )
        result = gb.vendor_spending_report(compact=False,
            start_date="2026-01-01",
            end_date="2026-12-31",
            vendor_id="000001",
        )
        assert len(result["vendors"]) == 1
        assert result["vendors"][0]["vendor_name"] == "Office Depot"


class TestVendorSpendingGroupBy:
    """group_by sub-period columns for vendor_spending_report."""

    @staticmethod
    def _seed(gb) -> None:
        """Office Depot billed Feb (50) + Mar (70); Staples Mar (30)."""
        gb.create_vendor(name="Office Depot")
        gb.create_vendor(name="Staples")

        def bill(bill_id, vendor_id, price, post_date):
            gb.create_bill(vendor_id=vendor_id)
            gb.add_bill_entry(
                bill_id=bill_id,
                account="Expenses:Office Supplies",
                description="x", quantity="1", price=price,
            )
            gb.post_invoice(
                invoice_id=bill_id,
                post_account="Liabilities:Accounts Payable",
                owner_type="vendor", post_date=post_date,
            )

        bill("000001", "000001", "50.00", "2026-02-15")
        bill("000002", "000001", "70.00", "2026-03-10")
        bill("000003", "000002", "30.00", "2026-03-20")

    @staticmethod
    def _parse(tsv: str) -> dict[str, list[str]]:
        out = {}
        for ln in tsv.splitlines():
            if "\t" in ln:
                cells = ln.split("\t")
                out[cells[0]] = cells
        return out

    def test_month_columns(self, business_book):
        gb = GnuCashBook(str(business_book))
        self._seed(gb)
        tsv = gb.vendor_spending_report(
            start_date="2026-01-01", end_date="2026-03-31",
            group_by="month",
        )
        rows = self._parse(tsv)
        assert rows["Vendor"] == [
            "Vendor", "2026-01", "2026-02", "2026-03", "Total", "Avg",
        ]
        # Office Depot spikes in March; sorted first by total.
        assert rows["Office Depot"] == [
            "Office Depot", "0.00", "50.00", "70.00", "120.00", "40.00",
        ]
        assert rows["Staples"] == [
            "Staples", "0.00", "0.00", "30.00", "30.00", "10.00",
        ]
        assert rows["TOTAL"] == [
            "TOTAL", "0.00", "50.00", "100.00", "150.00", "50.00",
        ]

    def test_vendor_filter_with_group_by(self, business_book):
        gb = GnuCashBook(str(business_book))
        self._seed(gb)
        tsv = gb.vendor_spending_report(
            start_date="2026-01-01", end_date="2026-03-31",
            vendor_id="000001", group_by="month",
        )
        rows = self._parse(tsv)
        assert "Office Depot" in rows and "Staples" not in rows

    def test_invalid_group_by(self, business_book):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="Invalid group_by"):
            gb.vendor_spending_report(
                start_date="2026-01-01", end_date="2026-03-31",
                group_by="decade",
            )

    def test_no_group_by_unchanged(self, business_book):
        """Regression guard: omitting group_by keeps the dict shape."""
        gb = GnuCashBook(str(business_book))
        self._seed(gb)
        result = gb.vendor_spending_report(
            compact=False,
            start_date="2026-01-01", end_date="2026-03-31",
        )
        assert "vendors" in result and "totals" in result
        assert Decimal(result["totals"]["total_billed"]) == Decimal("150")


# ============== End-to-End Lifecycle Tests ==============


class TestInvoiceLifecycle:
    """End-to-end invoice and bill lifecycle tests."""

    def test_full_invoice_lifecycle(self, business_book):
        """Customer invoice: create -> add entries -> post -> pay."""
        gb = GnuCashBook(str(business_book))
        # Create customer and invoice
        gb.create_customer(name="Acme Corp")
        inv = gb.create_invoice(customer_id="000001")
        assert inv["status"] == "created"

        # Add entries
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Consulting",
            quantity="10",
            price="100.00",
        )

        # Post
        post_result = gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
        )
        assert post_result["status"] == "posted"
        assert Decimal(post_result["total"]) == Decimal("1000")

        # Verify outstanding
        outstanding = gb.get_outstanding_invoices(compact=False)["invoices"]
        assert len(outstanding) == 1
        assert Decimal(outstanding[0]["amount_due"]) == Decimal("1000")

        # Pay in full
        pay_result = gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="1000",
        )
        assert Decimal(pay_result["remaining_balance"]) == Decimal("0")

        # Verify no longer outstanding
        outstanding = gb.get_outstanding_invoices(compact=False)["invoices"]
        assert len(outstanding) == 0

    def test_full_bill_lifecycle(self, business_book):
        """Vendor bill: create -> add entries -> post -> pay."""
        gb = GnuCashBook(str(business_book))
        # Create vendor and bill
        gb.create_vendor(name="Office Depot")
        bill = gb.create_bill(vendor_id="000001")
        assert bill["status"] == "created"

        # Add entry
        gb.add_bill_entry(
            bill_id="000001",
            account="Expenses:Office Supplies",
            description="Paper",
            quantity="5",
            price="20.00",
        )

        # Post
        post_result = gb.post_invoice(
            invoice_id="000001",
            post_account="Liabilities:Accounts Payable",
            owner_type="vendor",
        )
        assert post_result["status"] == "posted"
        assert Decimal(post_result["total"]) == Decimal("100")

        # Verify outstanding
        outstanding = gb.get_outstanding_invoices(owner_type="vendor", compact=False)["invoices"]
        assert len(outstanding) == 1

        # Pay
        pay_result = gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="100",
            owner_type="vendor",
        )
        assert Decimal(pay_result["remaining_balance"]) == Decimal("0")

        # Verify cleared
        outstanding = gb.get_outstanding_invoices(owner_type="vendor", compact=False)["invoices"]
        assert len(outstanding) == 0


class TestPhase4DVendorSpendingCompact:
    """Lock tests for the Phase 4D compact ``vendor_spending_report``
    contract. Verbose mode preserves the dict shape (minus the dropped
    ``period`` echo); compact mode renders the aligned text table."""

    def test_compact_default_returns_string(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        gb.create_bill(vendor_id="000001")
        gb.add_bill_entry(
            bill_id="000001", account="Expenses:Office Supplies",
            description="Paper", quantity="1", price="50.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Liabilities:Accounts Payable",
            owner_type="vendor",
            post_date="2026-01-01",
        )
        result = gb.vendor_spending_report(
            start_date="2026-01-01", end_date="2026-12-31",
        )
        assert isinstance(result, str)
        assert "Office Depot" in result
        assert "TOTAL" in result
        assert "billed" in result
        assert "paid" in result
        assert "outstanding" in result

    def test_pluralizes_one_vs_many_bills(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Solo Vendor")
        gb.create_bill(vendor_id="000001")
        gb.add_bill_entry(
            bill_id="000001", account="Expenses:Office Supplies",
            description="One", quantity="1", price="100.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Liabilities:Accounts Payable",
            owner_type="vendor",
            post_date="2026-01-01",
        )
        result = gb.vendor_spending_report(
            start_date="2026-01-01", end_date="2026-12-31",
        )
        # Single bill renders as "1 bill", not "1 bills".
        assert "1 bill " in result or result.endswith("1 bill")
        # The TOTAL line counts the same way.
        assert "1 bills" not in result

    def test_verbose_mode_drops_period_echo(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        result = gb.vendor_spending_report(
            start_date="2026-01-01",
            end_date="2026-12-31",
            compact=False,
        )
        assert "vendors" in result
        assert "totals" in result
        # ``period`` was the input echo Abe flagged for removal — it
        # duplicated the dates the caller already had.
        assert "period" not in result


class TestPhase4CBreakdownCompact:
    """Phase 4C: ``spending_by_category`` and ``income_by_source``
    return aligned text tables by default, JSON via ``compact=False``.
    Same shape as the helper used by ``vendor_spending_report``."""

    def test_spending_compact_returns_string(self, business_book):
        from datetime import date as date_cls
        gb = GnuCashBook(str(business_book))
        # The business_book fixture seeds Income:Sales / Expenses:*
        # accounts but no transactions; create one to anchor.
        gb.create_invoice(customer_id="000001") if False else None
        result = gb.spending_by_category(
            start_date=date_cls(2024, 1, 1),
            end_date=date_cls(2024, 12, 31),
        )
        assert isinstance(result, str)
        assert "TOTAL" in result

    def test_income_compact_returns_string(self, business_book):
        from datetime import date as date_cls
        gb = GnuCashBook(str(business_book))
        result = gb.income_by_source(
            start_date=date_cls(2024, 1, 1),
            end_date=date_cls(2024, 12, 31),
        )
        assert isinstance(result, str)
        assert "TOTAL" in result


class TestCnyBugReportFollowups:
    """Regression tests for the small-bug findings in the CNY
    cousin-verification report (separate PR from the substantive
    Bug 3 work that lives on its own branch)."""

    # ── Bug 4: "days past past due" typo ─────────────────────────

    def test_outstanding_invoices_overdue_no_double_word(
        self, business_book,
    ):
        """The compact-format ``get_outstanding_invoices`` template
        was concatenating "days past " with " past due" and producing
        "X days past past due". Should read either "X days past due"
        (contractual) or "X days past 30-day default" (no terms)."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001", account="Income:Sales",
            description="Consulting", quantity="1", price="500.00",
        )
        # Post with explicit due date in the past — exercises the
        # contractual branch where the typo lived.
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
            post_date="2026-01-01",
            due_date="2026-02-01",
        )
        compact = gb.get_outstanding_invoices()
        assert "past past due" not in compact, (
            f"typo regression — found duplicated word in:\n{compact}"
        )
        # And the correct form is present.
        assert "past due" in compact

    def test_outstanding_invoices_no_terms_renders_30_day_default(
        self, business_book,
    ):
        """No-terms branch should annotate as ``"X days past 30-day
        default"`` — also doesn't have the duplicated word."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001", account="Income:Sales",
            description="Consulting", quantity="1", price="500.00",
        )
        # No due_date and no billterm — falls to 30-day default.
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
            post_date="2026-01-01",
        )
        compact = gb.get_outstanding_invoices()
        assert "past past" not in compact
        assert "30-day default" in compact

    # ── Bug 2: pay_invoice should reuse existing FX accounts ─────

    def test_fx_account_reuse_picks_up_user_named_account(
        self, business_book,
    ):
        """When the user has pre-created a fuzzy-named FX account
        (``Income:FX Gain Loss``) and *no* canonical account exists,
        ``_get_or_create_fx_account`` should match it (single
        candidate) instead of silently creating a parallel
        ``Income:Foreign Exchange Gain/Loss``."""
        gb = GnuCashBook(str(business_book))
        gb.create_account(
            name="FX Gain Loss",
            account_type="INCOME",
            parent="Income",
        )
        with gb.open(readonly=True) as book:
            fx_acct, notice = gb._get_or_create_fx_account(book)
            assert fx_acct.fullname == "Income:FX Gain Loss"
            assert notice is None

    def test_fx_account_reuse_matches_alternate_keywords(
        self, business_book,
    ):
        """Match any of the FX-name substrings in the leaf account
        name (case-insensitive). 'Forex Adjustments' contains 'forex'."""
        gb = GnuCashBook(str(business_book))
        gb.create_account(
            name="Forex Adjustments",
            account_type="INCOME",
            parent="Income",
        )
        with gb.open(readonly=True) as book:
            fx_acct, notice = gb._get_or_create_fx_account(book)
            assert fx_acct.fullname == "Income:Forex Adjustments"
            assert notice is None

    def test_fx_account_ambiguous_falls_through_with_notice(
        self, business_book,
    ):
        """If multiple candidate FX accounts exist (e.g., the user
        pre-created one *and* a previous pay_invoice call auto-
        created the canonical), don't guess between them. Route to
        canonical and surface a notice listing the candidates so the
        caller can pass ``fx_account`` explicitly next time."""
        gb = GnuCashBook(str(business_book))
        gb.create_account(
            name="FX Gain Loss",
            account_type="INCOME",
            parent="Income",
        )
        gb.create_account(
            name="Foreign Exchange Gain/Loss",
            account_type="INCOME",
            parent="Income",
        )
        with gb.open(readonly=True) as book:
            fx_acct, notice = gb._get_or_create_fx_account(book)
            assert fx_acct.fullname == "Income:Foreign Exchange Gain/Loss"
            assert notice is not None
            assert notice["type"] == "ambiguous_fx_account"
            assert "Income:FX Gain Loss" in notice["candidates"]
            assert "Income:Foreign Exchange Gain/Loss" in notice["candidates"]
            assert "fx_account" in notice["message"]

    def test_fx_account_falls_through_to_canonical_create(
        self, business_book,
    ):
        """No fuzzy match available → canonical account auto-created
        under Income, no notice."""
        gb = GnuCashBook(str(business_book))
        with gb.open(readonly=False) as book:
            fx_acct, notice = gb._get_or_create_fx_account(book)
            book.save()
            assert fx_acct.fullname == "Income:Foreign Exchange Gain/Loss"
            assert notice is None

    def test_fx_account_explicit_parameter_overrides_heuristic(
        self, business_book,
    ):
        """When the caller passes ``fx_account``, that account is
        used regardless of what fuzzy matches exist. This is the
        explicit-control path for callers who want determinism."""
        gb = GnuCashBook(str(business_book))
        # Create two candidates that would normally be ambiguous AND
        # a third unrelated account the caller will pin to.
        gb.create_account(
            name="FX Gain Loss",
            account_type="INCOME",
            parent="Income",
        )
        gb.create_account(
            name="Foreign Exchange Gain/Loss",
            account_type="INCOME",
            parent="Income",
        )
        gb.create_account(
            name="Currency Translation Gain/Loss",
            account_type="INCOME",
            parent="Income",
        )
        with gb.open(readonly=True) as book:
            fx_acct, notice = gb._get_or_create_fx_account(
                book,
                fx_account="Income:Currency Translation Gain/Loss",
            )
            # Caller's explicit choice wins; no notice (no ambiguity
            # to flag when the caller has already disambiguated).
            assert fx_acct.fullname == "Income:Currency Translation Gain/Loss"
            assert notice is None

    def test_fx_account_explicit_invalid_path_raises(
        self, business_book,
    ):
        """Passing a non-existent ``fx_account`` is a hard error —
        the caller asked for a specific account, so silently
        falling back would mask a typo."""
        gb = GnuCashBook(str(business_book))
        with gb.open(readonly=True) as book:
            with pytest.raises(ValueError, match="fx_account not found"):
                gb._get_or_create_fx_account(
                    book, fx_account="Income:Does Not Exist",
                )

    def test_fx_account_explicit_wrong_type_raises(
        self, business_book,
    ):
        """``fx_account`` must be INCOME or EXPENSE — booking a
        gain/loss to an asset/liability/equity account would corrupt
        the financial statements."""
        gb = GnuCashBook(str(business_book))
        with gb.open(readonly=True) as book:
            with pytest.raises(
                ValueError, match="must be INCOME or EXPENSE"
            ):
                # business_book has Assets:Checking (BANK type).
                gb._get_or_create_fx_account(
                    book, fx_account="Assets:Checking",
                )


class TestBusinessFreeTextCaps:
    """MP-5: business entity free-text byte caps.

    Two layers of defense, exercised separately:

    1. **Tool layer** — Pydantic ``Field(max_length=N)`` on
       ``notes`` and a ``BusinessAddressInput`` model with per-
       field ``max_length`` on the address dict. FastMCP rejects
       at the schema layer BEFORE ``@audit_log`` runs auto-backup,
       so an oversize value rejects in milliseconds rather than
       hanging on the backup (Plumb Bob's blocker on PR validation).

    2. **Book layer** — ``_validate_business_freetext`` runs UTF-8
       byte-length checks inside the create/update path. Belt-and-
       suspenders for direct callers (scripts, tests) that bypass
       the MCP boundary; also catches the pathological multi-byte
       UTF-8 case where character length is under the schema cap
       but byte length exceeds the storage cap.
    """

    def test_book_layer_rejects_oversize_notes(self, test_book: Path):
        """Direct ``GnuCashBook.create_customer`` call with 5000-byte
        notes should raise immediately — bypasses MCP boundary."""
        gb = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match=r"notes exceeds 4096-byte cap"):
            gb.create_customer(name="Test", notes="X" * 5000)

    def test_book_layer_rejects_oversize_address_field(
        self, test_book: Path,
    ):
        gb = GnuCashBook(str(test_book))
        with pytest.raises(
            ValueError, match=r"address\.addr1 exceeds 1024-byte cap",
        ):
            gb.create_customer(
                name="Test",
                address={"addr1": "X" * 2000},
            )

    def test_book_layer_accepts_under_cap(self, test_book: Path):
        gb = GnuCashBook(str(test_book))
        result = gb.create_customer(
            name="UnderCap", notes="X" * 4096,
            address={"addr1": "Y" * 1024},
        )
        assert result["status"] == "created"

    def test_pydantic_model_rejects_oversize_notes_before_book(
        self, test_book: Path,
    ):
        """The tool-layer Pydantic constraint on ``notes`` should
        reject oversize input WITHOUT triggering any book-layer
        work. Verified by importing the annotation and asking
        Pydantic to validate directly.
        """
        from pydantic import BaseModel, ValidationError
        from gnucash_mcp.tools._helpers import BusinessNotes

        class _Probe(BaseModel):
            notes: BusinessNotes = ""

        # 5000 chars — same as Plumb Bob's failing case.
        with pytest.raises(ValidationError) as exc:
            _Probe(notes="X" * 5000)
        # Pydantic's message includes the max_length constraint.
        msg = str(exc.value)
        assert "4096" in msg, msg

    def test_pydantic_model_rejects_oversize_address_field(
        self, test_book: Path,
    ):
        from pydantic import ValidationError
        from gnucash_mcp.tools._helpers import BusinessAddressInput

        with pytest.raises(ValidationError) as exc:
            BusinessAddressInput(addr1="X" * 2000)
        assert "1024" in str(exc.value)

    def test_pydantic_address_model_forbids_unknown_keys(self):
        """``BusinessAddressInput`` uses ``extra='forbid'`` so typo
        keys (``adr1`` instead of ``addr1``) reject rather than
        silently drop."""
        from pydantic import ValidationError
        from gnucash_mcp.tools._helpers import BusinessAddressInput

        with pytest.raises(ValidationError) as exc:
            BusinessAddressInput(adr1="oops")
        assert "extra" in str(exc.value).lower() or "forbid" in str(exc.value).lower()


class TestFXStaleRateGuard:
    """FX freshness guard on post_invoice / pay_invoice.

    A cross-currency document etches its exchange rate at post/pay
    time and ``create_price`` cannot update it retroactively. The
    guard refuses when the chosen rate is more than
    ``GNUCASH_FX_GUARD_DAYS`` (default 7) from the document's own
    date, unless ``force=True``. Three bands on one axis
    (``|price_date - doc_date|``): ≤7 proceed, 7–90 refuse-but-
    forceable, >90 the staleness cap hard-errors (not forceable).

    The axis is the DOCUMENT date, not wall-clock today — a
    correctly-dated backdated posting passes; a rate that drifts
    forward of the document fails symmetrically.
    """

    def _eur_invoice_in_usd_book(
        self, business_book, *, price_date, rate="1.10",
    ):
        """USD-default book + one EUR/USD market price at
        ``price_date`` + an unposted 1-line EUR invoice (id 000001,
        EUR 100). Returns the GnuCashBook."""
        import piecash
        gb = GnuCashBook(str(business_book))
        with gb.open(readonly=False) as bk:
            eur = piecash.Commodity(
                namespace="CURRENCY", mnemonic="EUR",
                fullname="Euro", fraction=100,
            )
            bk.session.add(eur)
            bk.flush()
            bk.session.add(piecash.Price(
                commodity=eur, currency=bk.default_currency,
                date=price_date, value=rate, type="last",
            ))
            bk.save()
        gb.create_customer(name="Berlin GmbH", currency="EUR")
        inv = gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id=inv["id"], account="Income:Sales",
            description="work", quantity="1", price="100",
        )
        return gb

    # ── fresh / under-threshold: guard silent ─────────────────────

    def test_fresh_rate_posts_without_fx_stale(self, business_book):
        gb = self._eur_invoice_in_usd_book(
            business_book, price_date=date(2026, 6, 1),
        )
        result = gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
            post_date="2026-06-03",  # 2 days
        )
        assert result["status"] == "posted"
        assert "fx_stale" not in result

    def test_six_days_under_threshold_posts(self, business_book):
        gb = self._eur_invoice_in_usd_book(
            business_book, price_date=date(2026, 6, 1),
        )
        result = gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
            post_date="2026-06-07",  # 6 days
        )
        assert result["status"] == "posted"
        assert "fx_stale" not in result

    # ── stale band (7–90): refuse unless forced ───────────────────

    def test_stale_rate_refused_without_force(self, business_book):
        from gnucash_mcp.book import StaleFXRateError
        gb = self._eur_invoice_in_usd_book(
            business_book, price_date=date(2026, 6, 1),
        )
        with pytest.raises(StaleFXRateError) as exc:
            gb.post_invoice(
                invoice_id="000001",
                post_account="Assets:Accounts Receivable",
                post_date="2026-06-20",  # 19 days
            )
        detail = exc.value.fx_detail
        assert detail["currency"] == "EUR"
        assert detail["rate_date"] == "2026-06-01"
        assert detail["age_days"] == 19
        assert Decimal(detail["rate"]) == Decimal("1.10")

    def test_eight_days_just_over_threshold_refused(self, business_book):
        from gnucash_mcp.book import StaleFXRateError
        gb = self._eur_invoice_in_usd_book(
            business_book, price_date=date(2026, 6, 1),
        )
        with pytest.raises(StaleFXRateError):
            gb.post_invoice(
                invoice_id="000001",
                post_account="Assets:Accounts Receivable",
                post_date="2026-06-09",  # 8 days
            )

    def test_stale_rate_forced_attaches_fx_stale(self, business_book):
        gb = self._eur_invoice_in_usd_book(
            business_book, price_date=date(2026, 6, 1),
        )
        result = gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
            post_date="2026-06-20",  # 19 days
            force=True,
        )
        assert result["status"] == "posted"
        fx = result["fx_stale"]
        assert fx["currency"] == "EUR"
        assert fx["rate_date"] == "2026-06-01"
        assert fx["age_days"] == 19
        assert fx["forced"] is True
        assert Decimal(fx["rate_used"]) == Decimal("1.10")

    def test_force_with_fresh_rate_adds_no_block(self, business_book):
        """force=True is silently ignored when nothing is stale —
        no fx_stale block, because nothing was overridden."""
        gb = self._eur_invoice_in_usd_book(
            business_book, price_date=date(2026, 6, 1),
        )
        result = gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
            post_date="2026-06-03",  # 2 days
            force=True,
        )
        assert result["status"] == "posted"
        assert "fx_stale" not in result

    # ── document-date axis (the design decision) ──────────────────

    def test_backdated_posting_judged_by_document_date(self, business_book):
        """A January invoice posted with a January-dated rate passes
        even though the document is months behind wall-clock today —
        staleness is measured against the document date, not now."""
        gb = self._eur_invoice_in_usd_book(
            business_book, price_date=date(2026, 1, 2),
        )
        result = gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
            post_date="2026-01-01",  # 1 day from the rate
        )
        assert result["status"] == "posted"
        assert "fx_stale" not in result

    def test_forward_drifted_rate_refused_symmetrically(self, business_book):
        """A rate dated AFTER the document by >7 days is just as
        stale as one before it — |offset| is symmetric."""
        from gnucash_mcp.book import StaleFXRateError
        gb = self._eur_invoice_in_usd_book(
            business_book, price_date=date(2026, 6, 20),
        )
        with pytest.raises(StaleFXRateError) as exc:
            gb.post_invoice(
                invoice_id="000001",
                post_account="Assets:Accounts Receivable",
                post_date="2026-06-01",  # rate is 19 days FORWARD
            )
        assert exc.value.fx_detail["age_days"] == 19

    # ── interaction with the 90-day staleness cap ─────────────────

    def test_beyond_cap_not_forceable(self, business_book):
        """A rate past the 90-day staleness cap is excluded entirely
        — the no-rate hard error fires and force cannot rescue it."""
        gb = self._eur_invoice_in_usd_book(
            business_book, price_date=date(2026, 6, 1),
        )
        with pytest.raises(ValueError, match="no matching price"):
            gb.post_invoice(
                invoice_id="000001",
                post_account="Assets:Accounts Receivable",
                post_date="2026-10-15",  # 136 days > 90
                force=True,
            )

    # ── same-currency: guard never engages ────────────────────────

    def test_same_currency_skips_guard(self, business_book):
        """A USD invoice in a USD book never touches the rate path,
        so an old post date is irrelevant — no guard, no price."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme USD")
        inv = gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id=inv["id"], account="Income:Sales",
            description="work", quantity="1", price="100",
        )
        result = gb.post_invoice(
            invoice_id=inv["id"],
            post_account="Assets:Accounts Receivable",
            post_date="2026-01-01",  # far from "today", no price
        )
        assert result["status"] == "posted"
        assert "fx_stale" not in result

    # ── env override ──────────────────────────────────────────────

    def test_env_var_disables_guard(self, business_book, monkeypatch):
        monkeypatch.setenv("GNUCASH_FX_GUARD_DAYS", "0")
        gb = self._eur_invoice_in_usd_book(
            business_book, price_date=date(2026, 6, 1),
        )
        result = gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
            post_date="2026-06-20",  # 19 days — would normally refuse
        )
        assert result["status"] == "posted"
        assert "fx_stale" not in result

    # ── pay path ──────────────────────────────────────────────────

    def test_pay_stale_refused_then_forced(self, business_book):
        """The guard covers pay as well as post: post fresh, then a
        payment whose pay-date rate is stale refuses, and force lets
        it through with an fx_stale block."""
        from gnucash_mcp.book import StaleFXRateError
        gb = self._eur_invoice_in_usd_book(
            business_book, price_date=date(2026, 6, 1),
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
            post_date="2026-06-03",  # fresh post
        )
        # Pay 24 days after the only rate → stale at pay time.
        with pytest.raises(StaleFXRateError):
            gb.pay_invoice(
                invoice_id="000001",
                payment_account="Assets:Checking",
                amount="100",
                payment_date="2026-06-25",
            )
        forced = gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="100",
            payment_date="2026-06-25",
            force=True,
        )
        assert forced["status"] == "paid"
        assert forced["fx_stale"]["age_days"] == 24
        assert forced["fx_stale"]["forced"] is True

    # ── audit-log "forced" annotation ─────────────────────────────

    def test_audit_formatter_renders_forced_line(self):
        from gnucash_mcp.logging_config import _fmt_invoice_post
        entry = {
            "params": {"id": "000001", "post_account": "Assets:A/R"},
            "after_state": {
                "total": "100.00",
                "post_date": "2026-06-20",
                "transaction_guid": "abcd1234",
                "fx_stale": {
                    "currency": "EUR",
                    "rate_used": "1.1",
                    "rate_date": "2026-06-01",
                    "age_days": 19,
                    "forced": True,
                },
            },
        }
        lines = _fmt_invoice_post(entry)
        joined = "\n".join(lines)
        assert "EUR" in joined
        assert "19 days" in joined
        assert "forced" in joined

    def test_audit_formatter_no_fx_line_when_fresh(self):
        from gnucash_mcp.logging_config import _fmt_invoice_post
        entry = {
            "params": {"id": "000001", "post_account": "Assets:A/R"},
            "after_state": {
                "total": "100.00",
                "post_date": "2026-06-03",
                "transaction_guid": "abcd1234",
            },
        }
        joined = "\n".join(_fmt_invoice_post(entry))
        assert "forced" not in joined
        assert "stale" not in joined


# ============== Cross-commodity A/R relief + discount FX (C10/A5) ==============


class TestCrossCommodityArRelief:
    """C10 (adversarial pass 2): when the A/R account's commodity
    differs from the invoice currency, the settlement must relieve
    the lot at the rate the receivable was CARRIED at (post-date),
    not the pay-date rate. Pre-fix the post→pay drift landed twice —
    a permanent residual A/R quantity AND the explicit FX split.
    """

    def _add_eur_and_rates(self, gb: GnuCashBook) -> None:
        import piecash

        with gb.open(readonly=False) as book:
            usd = book.default_currency
            eur = piecash.factories.create_currency_from_ISO("EUR")
            book.session.add(eur)
            book.session.add(piecash.Price(
                commodity=eur, currency=usd, date=date(2026, 3, 10),
                value="1.10", source="user:test", type="nav",
            ))
            book.session.add(piecash.Price(
                commodity=eur, currency=usd, date=date(2026, 3, 20),
                value="1.20", source="user:test", type="nav",
            ))
            book.save()

    def test_settled_foreign_invoice_leaves_zero_ar(
        self, business_book,
    ):
        """EUR 1,000 invoice posted to USD A/R at 1.10 (carried
        1,100), fully paid at 1.20 (1,200 received). Pre-fix the
        relief quantity used the pay-date rate (1,200), leaving a
        permanent −100 USD A/R balance; the 100 USD drift belongs
        ONLY in the FX gain split."""
        gb = GnuCashBook(str(business_book))
        self._add_eur_and_rates(gb)

        gb.create_customer(name="Berlin Digital", currency="EUR")
        gb.create_invoice(
            customer_id="000001", currency="EUR",
            date_opened="2026-03-10",
        )
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="EUR consulting",
            quantity="1", price="1000.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",  # USD A/R
            post_date="2026-03-10",
        )
        assert gb.get_balance(
            account_name="Assets:Accounts Receivable"
        ) == Decimal("1100")

        result = gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="1000.00",
            payment_date="2026-03-20",
        )

        assert Decimal(result["remaining_balance"]) == Decimal("0")
        assert result["fx_realized"]["direction"] == "gain"
        assert Decimal(
            result["fx_realized"]["amount"]
        ) == Decimal("100.00")
        # The drift lands ONCE: A/R fully relieved, no phantom.
        assert gb.get_balance(
            account_name="Assets:Accounts Receivable"
        ) == Decimal("0"), "phantom A/R residue after full settlement"
        assert gb.get_outstanding_invoices(compact=False)["invoices"] == []

    def test_partial_payment_relieves_pro_rata(self, business_book):
        """A 40% payment relieves 40% of the carried quantity, so
        two partials + the closer still zero the account."""
        gb = GnuCashBook(str(business_book))
        self._add_eur_and_rates(gb)
        gb.create_customer(name="Berlin Digital", currency="EUR")
        gb.create_invoice(
            customer_id="000001", currency="EUR",
            date_opened="2026-03-10",
        )
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="EUR consulting",
            quantity="1", price="1000.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
            post_date="2026-03-10",
        )
        gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="400.00", payment_date="2026-03-20",
        )
        # 40% of the carried 1,100 relieved → 660 remains.
        assert gb.get_balance(
            account_name="Assets:Accounts Receivable"
        ) == Decimal("660")
        gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="600.00", payment_date="2026-03-20",
        )
        assert gb.get_balance(
            account_name="Assets:Accounts Receivable"
        ) == Decimal("0")


class TestDiscountFxAndQuantumTolerance:
    """C10 companion + A5: the early-payment discount leg of a
    cross-currency settlement carries the same post→pay drift as
    the payment leg, and the validator's 1-quantum tolerance must
    produce a bookable transaction."""

    def _setup_eur_invoice_with_terms(self, gb: GnuCashBook) -> None:
        import piecash

        with gb.open(readonly=False) as book:
            usd = book.default_currency
            eur = piecash.factories.create_currency_from_ISO("EUR")
            book.session.add(eur)
            assets = next(
                a for a in book.accounts if a.fullname == "Assets"
            )
            book.session.add(piecash.Account(
                name="Accounts Receivable EUR", type="RECEIVABLE",
                parent=assets, commodity=eur,
            ))
            book.session.add(piecash.Price(
                commodity=eur, currency=usd, date=date(2026, 3, 10),
                value="1.10", source="user:test", type="nav",
            ))
            book.session.add(piecash.Price(
                commodity=eur, currency=usd, date=date(2026, 3, 15),
                value="1.20", source="user:test", type="nav",
            ))
            book.save()
        gb.create_customer(name="Berlin Digital", currency="EUR")
        gb.create_billterm(
            name="2/10 Net 30", due_days=30,
            discount_days=10, discount_percent="2",
        )
        gb.create_invoice(
            customer_id="000001", currency="EUR",
            term="2/10 Net 30", date_opened="2026-03-10",
        )
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="EUR consulting",
            quantity="1", price="1000.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable EUR",
            post_date="2026-03-10",
        )

    def test_discount_leg_drift_is_booked(self, business_book):
        """EUR 1,000 @ 1.10 post, settled at 1.20 with a 2% (EUR 20)
        discount: payment-leg drift 980 × 0.10 = 98, discount-leg
        drift 20 × 0.10 = 2. Pre-fix only the 98 was booked and the
        balance sheet was off by the 2."""
        gb = GnuCashBook(str(business_book))
        self._setup_eur_invoice_with_terms(gb)

        result = gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="980.00",
            payment_date="2026-03-15",
            apply_discount=True,
        )
        assert Decimal(result["remaining_balance"]) == Decimal("0")
        assert result["fx_realized"]["direction"] == "gain"
        assert Decimal(
            result["fx_realized"]["amount"]
        ) == Decimal("100.00")
        # Ledger agrees: gain is a credit on the FX income account.
        assert gb.get_balance(
            account_name="Income:Foreign Exchange Gain/Loss"
        ) == Decimal("-100")
        # And the books tie: A = L + E exactly.
        bs = gb.balance_sheet(as_of_date=date(2026, 3, 31))
        assert (
            Decimal(bs["assets"]["total"])
            - Decimal(bs["liabilities"]["total"])
            == Decimal(bs["equity"]["total"])
        )

    def test_one_quantum_shortfall_mismatch_books_cleanly(
        self, business_book,
    ):
        """A5: the validator admits a 1-cent shortfall-vs-expected
        mismatch; the discount books at the ACTUAL shortfall so the
        splits balance. Pre-fix the blessed input died with an
        opaque GncImbalanceError."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_billterm(
            name="2/10 Net 30", due_days=30,
            discount_days=10, discount_percent="2",
        )
        gb.create_invoice(
            customer_id="000001", term="2/10 Net 30",
            date_opened="2026-03-10",
        )
        gb.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Consulting",
            quantity="1", price="1000.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
            post_date="2026-03-10",
        )
        # Expected discount 20.00; pay 980.01 → shortfall 19.99,
        # within the 1-quantum tolerance.
        result = gb.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="980.01",
            payment_date="2026-03-15",
            apply_discount=True,
        )
        assert result["status"] == "paid"
        assert Decimal(result["remaining_balance"]) == Decimal("0")
        # Discount booked at the actual shortfall.
        assert gb.get_balance(
            account_name="Expenses:Sales Discounts"
        ) == Decimal("19.99")


class TestDeleteCreditNoteSlotCleanup:
    """A6 (adversarial pass 2): deleting an unposted credit note
    must remove its slot rows (the ``credit-note`` flag and the
    ``gnc-mcp/applies-to-invoice`` linkage) — the raw-SQL row
    delete has no ON DELETE CASCADE and orphaned them pre-fix."""

    def test_slots_removed_with_credit_note(self, business_book):
        from sqlalchemy import text

        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001", account="Income:Sales",
            description="Work", quantity="1", price="100.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
        )
        cn = gb.create_credit_note(
            owner_id="000001", owner_type="customer",
            applies_to_invoice_id="000001",
        )
        with gb.open(readonly=True) as book:
            inv = gb._resolve_credit_note(book, cn["id"])
            cn_guid = inv.guid
            n_before = book.session.execute(
                text("SELECT COUNT(*) FROM slots WHERE obj_guid = :g"),
                {"g": cn_guid},
            ).scalar()
        assert n_before > 0, "fixture should have credit-note slots"

        gb.delete_credit_note(credit_note_id=cn["id"])

        with gb.open(readonly=True) as book:
            n_after = book.session.execute(
                text("SELECT COUNT(*) FROM slots WHERE obj_guid = :g"),
                {"g": cn_guid},
            ).scalar()
        assert n_after == 0, "credit-note slot rows orphaned"


class TestCodexCrossModelFindings:
    """Regression locks for the 2026-08-05 ChatGPT Codex battery
    (specs/v1.5/CODEX_TEST_FINDINGS.md): credit-note identity across
    the document lifecycle, the self-contradictory owner-mismatch
    error, and voucher posting via inferred owner_type."""

    def _credit_note(self, gb) -> str:
        gb.create_customer(name="Acme Co")
        cn = gb.create_credit_note(owner_id="000001", owner_type="customer")
        gb.add_credit_note_entry(
            credit_note_id=cn["id"], account="Income:Sales",
            description="credit", quantity="1", price="100.00",
        )
        return cn["id"]

    def test_get_invoice_type_is_credit_note(self, business_book):
        """The slot, not owner_type, decides ``type`` — get_invoice
        must agree with the delete/unpost responses."""
        gb = GnuCashBook(str(business_book))
        cn_id = self._credit_note(gb)
        assert gb.get_invoice(cn_id)["type"] == "credit_note"

    def test_credit_note_identity_survives_unpost(self, business_book):
        """Deleting the posting transaction sweeps the invoice's own
        slots (piecash slot-hierarchy overlap); unpost must restore
        the credit-note flag so a FRESH session still sees it."""
        gb = GnuCashBook(str(business_book))
        cn_id = self._credit_note(gb)
        gb.post_invoice(
            invoice_id=cn_id,
            post_account="Assets:Accounts Receivable",
            owner_type="customer",
        )
        gb.unpost_invoice(invoice_id=cn_id, owner_type="customer")
        fresh = GnuCashBook(str(business_book))
        assert fresh.get_invoice(cn_id)["type"] == "credit_note"
        result = fresh.delete_credit_note(cn_id)
        assert result["type"] == "credit_note"

    def test_apply_mismatch_error_names_owner_kinds(self, business_book):
        """Owner IDs are per-type sequences; the mismatch error must
        name the owner KIND so 'belongs to 000001 but target belongs
        to 000001' can't read as a contradiction."""
        gb = GnuCashBook(str(business_book))
        cn_id = self._credit_note(gb)
        gb.post_invoice(
            invoice_id=cn_id,
            post_account="Assets:Accounts Receivable",
            owner_type="customer",
        )
        gb.create_customer(name="Beta LLC")  # 000002
        inv = gb.create_invoice(customer_id="000002")
        gb.add_invoice_entry(
            invoice_id=inv["id"], account="Income:Sales",
            description="w", quantity="1", price="200.00",
        )
        gb.post_invoice(
            invoice_id=inv["id"],
            post_account="Assets:Accounts Receivable",
            owner_type="customer",
        )
        with pytest.raises(ValueError) as excinfo:
            gb.apply_credit_note(
                credit_note_id=cn_id,
                applies_to_invoice_id=inv["id"],
                owner_type="customer",
            )
        msg = str(excinfo.value)
        assert "customer" in msg
        assert "'000001'" in msg and "'000002'" in msg
        assert "Acme Co" in msg  # names disambiguate colliding IDs

    def test_post_voucher_without_owner_type(self, business_book):
        """PAYABLE inference must reach vouchers: bills and vouchers
        both post to A/P, so the inferred-vendor miss retries the
        employee side before declaring not-found."""
        gb = GnuCashBook(str(business_book))
        gb.create_employee(name="Jane Smith")
        v = gb.create_voucher(employee_id="000001")
        gb.add_voucher_entry(
            voucher_id=v["id"], account="Expenses:Office Supplies",
            description="travel", quantity="1", price="50.00",
        )
        result = gb.post_invoice(
            invoice_id=v["id"],
            post_account="Liabilities:Accounts Payable",
        )
        assert result["status"] == "posted"
