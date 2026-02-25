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

    def test_invalid_vendor(self, business_book):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="Vendor not found"):
            gb.create_bill(vendor_id="999999")


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
        assert result["is_posted"] is False
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
