"""Tests for business tools: customers, vendors, billterms, invoices, bills."""

import json
from decimal import Decimal
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
        assert len(result["guid"]) == 32

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
        assert result == ""

    def test_compact_format(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Beta Corp")
        gb.create_customer(name="Alpha Inc")
        result = gb.list_customers()
        lines = result.strip().split("\n")
        assert len(lines) == 2
        # Sorted by name
        assert "Alpha Inc" in lines[0]
        assert "Beta Corp" in lines[1]

    def test_verbose_format(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        result = gb.list_customers(compact=False)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["name"] == "Acme Corp"
        assert "guid" in result[0]
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
        assert len(result["guid"]) == 32

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
        assert result == ""

    def test_compact_format(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Zeta Supply")
        gb.create_vendor(name="Alpha Parts")
        result = gb.list_vendors()
        lines = result.strip().split("\n")
        assert len(lines) == 2
        assert "Alpha Parts" in lines[0]
        assert "Zeta Supply" in lines[1]

    def test_verbose_format(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Office Depot")
        result = gb.list_vendors(compact=False)
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
        assert len(result["guid"]) == 32

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
        docs/PIECASH_REFERENCE.md."""
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
        assert result == ""

    def test_compact_format(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_employee(name="Beta Hire")
        gb.create_employee(name="Alpha Hire")
        result = gb.list_employees()
        lines = result.strip().split("\n")
        assert len(lines) == 2
        # Sorted by name
        assert "Alpha Hire" in lines[0]
        assert "Beta Hire" in lines[1]

    def test_verbose_format(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_employee(name="Jane Smith")
        result = gb.list_employees(compact=False)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["name"] == "Jane Smith"
        assert "guid" in result[0]
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
        assert len(result["guid"]) == 32

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
        result = gb.list_billterms(compact=False)
        assert len(result) == 2
        names = {bt["name"] for bt in result}
        assert names == {"Net 30", "Net 60"}


class TestListBillterms:
    """Tests for list_billterms."""

    def test_empty_list(self, business_book):
        gb = GnuCashBook(str(business_book))
        result = gb.list_billterms()
        assert result == ""

    def test_compact_format(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_billterm(name="Net 30")
        result = gb.list_billterms()
        assert "Net 30" in result
        assert "30 days" in result

    def test_verbose_format(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_billterm(name="Net 30", due_days=30, description="Standard terms")
        result = gb.list_billterms(compact=False)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["name"] == "Net 30"
        assert result[0]["due_days"] == 30
        assert result[0]["description"] == "Standard terms"


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
        assert len(result["guid"]) == 32

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


class TestAddInvoiceEntry:
    """Tests for add_invoice_entry."""

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
        assert result == ""

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
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["type"] == "invoice"

    def test_filter_by_customer_type(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_vendor(name="Office Depot")
        gb.create_invoice(customer_id="000001")
        gb.create_bill(vendor_id="000001")
        result = gb.list_invoices(owner_type="customer", compact=False)
        assert len(result) == 1
        assert result[0]["type"] == "invoice"

    def test_filter_by_vendor_type(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_vendor(name="Office Depot")
        gb.create_invoice(customer_id="000001")
        gb.create_bill(vendor_id="000001")
        result = gb.list_invoices(owner_type="vendor", compact=False)
        assert len(result) == 1
        assert result[0]["type"] == "bill"

    def test_filter_by_status(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        # All invoices are open (not posted)
        result = gb.list_invoices(status="open", compact=False)
        assert len(result) == 1
        result = gb.list_invoices(status="posted", compact=False)
        assert len(result) == 0


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
        assert "guid" in entry
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
        # Should target the customer invoice, not the vendor bill
        result = gb.add_invoice_entry(
            invoice_id="000001", account="Income:Sales",
            description="Consulting", quantity="1", price="100",
        )
        assert result["status"] == "created"
        inv = gb.get_invoice("000001")
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

    def test_get_invoice_with_collision_defaults_to_first(self, business_book):
        """get_invoice without owner_type returns first match."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_vendor(name="Office Depot")
        gb.create_invoice(customer_id="000001")
        gb.create_bill(vendor_id="000001")
        result = gb.get_invoice("000001")
        # Returns whichever was created first (invoice)
        assert result["type"] in ("invoice", "bill")

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

    def test_basic_post(self, business_book):
        gb = GnuCashBook(str(business_book))
        self._setup_invoice(gb)
        result = gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
        )
        assert result["status"] == "posted"
        assert Decimal(result["total"]) == Decimal("500")
        assert len(result["transaction_guid"]) == 32
        assert len(result["lot_guid"]) == 32
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
        txn_guid = result["transaction_guid"]
        lot_guid = result["lot_guid"]

        # Read slots from the database directly
        conn = sqlite3.connect(str(business_book))
        try:
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
        txn_guid = result["transaction_guid"]

        conn = sqlite3.connect(str(business_book))
        try:
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
        txn_guid = result["transaction_guid"]

        conn = sqlite3.connect(str(business_book))
        try:
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


# ============== Outstanding Invoices Tests ==============


class TestGetOutstandingInvoices:
    """Tests for get_outstanding_invoices."""

    def test_empty_when_none_posted(self, business_book):
        gb = GnuCashBook(str(business_book))
        result = gb.get_outstanding_invoices()
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
        result = gb.get_outstanding_invoices()
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
        result = gb.get_outstanding_invoices()
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
        result = gb.get_outstanding_invoices()
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
        result = gb.get_outstanding_invoices(customer_id="000001")
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
        result = gb.get_outstanding_invoices(owner_type="vendor")
        assert len(result) == 1
        assert result[0]["type"] == "bill"


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
        result = gb.vendor_spending_report(
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
        result = gb.vendor_spending_report(
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
        result = gb.vendor_spending_report(
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
        result = gb.vendor_spending_report(
            start_date="2026-01-01",
            end_date="2026-12-31",
            vendor_id="000001",
        )
        assert len(result["vendors"]) == 1
        assert result["vendors"][0]["vendor_name"] == "Office Depot"


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
        outstanding = gb.get_outstanding_invoices()
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
        outstanding = gb.get_outstanding_invoices()
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
        outstanding = gb.get_outstanding_invoices(owner_type="vendor")
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
        outstanding = gb.get_outstanding_invoices(owner_type="vendor")
        assert len(outstanding) == 0
