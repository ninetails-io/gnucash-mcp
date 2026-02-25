"""Tests for business tools: customers, vendors, billterms."""

import json
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
