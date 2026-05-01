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

        active_only = gb.list_customers(active_only=True, compact=False)
        assert all(c["id"] != "000001" for c in active_only)
        all_customers = gb.list_customers(
            active_only=False, compact=False,
        )
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
        )
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
        # Verbose mode returns the envelope shape that matches
        # get_unreconciled_splits / get_prices: invoices list +
        # count + total + notice.
        assert isinstance(result, dict)
        assert "invoices" in result
        assert "count" in result
        assert "total" in result
        assert "notice" in result
        invoices = result["invoices"]
        assert len(invoices) == 1
        assert invoices[0]["type"] == "invoice"
        assert result["total"] == 1
        assert result["notice"] is None  # nothing truncated

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

    The bookkeeper hit this on a session where an LLM passed
    ``owner_type="employee"``. Pre-fix, the value silently fell
    through to no-filter and the LLM saw a confusing
    cross-sequence ID-collision error suggesting "customer or
    vendor" — never explaining that "employee" is the actual
    problem. Upfront validation saves the LLM a tool call and
    frames the limitation cleanly.
    """

    def test_employee_owner_type_rejected_with_clear_message(
        self, business_book,
    ):
        """The headline scenario: ``owner_type="employee"`` is
        explicitly out of scope for the 1.2.x business module
        (employee expense vouchers are a 1.3 thing). Reject
        upfront with a message that says so."""
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError) as exc_info:
            gb.get_invoice("000001", owner_type="employee")
        msg = str(exc_info.value)
        assert "employee" in msg.lower()
        assert "not yet supported" in msg
        # Hint at the valid options so the LLM doesn't have to
        # call back blindly.
        assert "customer" in msg
        assert "vendor" in msg

    def test_typo_owner_type_rejected_with_valid_options(
        self, business_book,
    ):
        """Typos like ``"custmer"`` (missing 'o') get the same
        upfront rejection. Pre-fix they silently fell through to
        no-filter."""
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError) as exc_info:
            gb.get_invoice("000001", owner_type="custmer")
        msg = str(exc_info.value)
        assert "Invalid owner_type" in msg
        assert "'custmer'" in msg
        assert "customer" in msg
        assert "vendor" in msg

    def test_none_owner_type_still_works(self, business_book):
        """``None`` means "no filter" — the existing semantic
        must survive the validation refactor."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        # No collision yet — None resolves to a single match.
        inv = gb.get_invoice("000001", owner_type=None)
        assert inv["type"] == "invoice"

    def test_post_invoice_rejects_employee_owner_type(
        self, business_book,
    ):
        """All four entrypoints share the same validator; verify
        ``post_invoice`` specifically since the bookkeeper's
        report mentioned posting an invoice with employee
        owner_type."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001", account="Income:Sales",
            description="x", quantity="1", price="100",
        )
        with pytest.raises(ValueError, match="not yet supported"):
            gb.post_invoice(
                invoice_id="000001",
                post_account="Assets:Accounts Receivable",
                owner_type="employee",
            )

    def test_pay_invoice_rejects_typo_owner_type(
        self, business_book,
    ):
        """Symmetry: ``pay_invoice`` validates too."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        with pytest.raises(ValueError, match="Invalid owner_type"):
            gb.pay_invoice(
                invoice_id="000001",
                payment_account="Assets:Checking",
                amount="50",
                owner_type="venddor",  # typo
            )

    def test_unpost_invoice_rejects_employee(self, business_book):
        """Symmetry: ``unpost_invoice`` (added in this same
        patch) validates too."""
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="not yet supported"):
            gb.unpost_invoice(
                invoice_id="000001", owner_type="employee",
            )

    def test_list_invoices_rejects_invalid_owner_type(
        self, business_book,
    ):
        """The reads validate the same way the writes do."""
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="Invalid owner_type"):
            gb.list_invoices(owner_type="bogus")

    def test_get_outstanding_invoices_rejects_invalid_owner_type(
        self, business_book,
    ):
        """The other read with owner_type also validates."""
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="not yet supported"):
            gb.get_outstanding_invoices(owner_type="employee")


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


# ============== Outstanding Invoices Tests ==============


class TestGetOutstandingInvoices:
    """Tests for get_outstanding_invoices."""

    def test_empty_when_none_posted(self, business_book):
        gb = GnuCashBook(str(business_book))
        result = gb.get_outstanding_invoices(compact=False)
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
        result = gb.get_outstanding_invoices(compact=False)
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
        result = gb.get_outstanding_invoices(compact=False)
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
        result = gb.get_outstanding_invoices(compact=False)
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
        result = gb.get_outstanding_invoices(customer_id="000001", compact=False)
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
        result = gb.get_outstanding_invoices(owner_type="vendor", compact=False)
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
        result = gb.get_outstanding_invoices(compact=False)
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
        """Bookkeeper-flagged inconsistency: compact mode appended a
        ``[Showing N of M]`` notice when truncated, but verbose mode
        returned just a bare list with no truncation signal.
        Now both modes carry ``count`` / ``total`` / ``notice`` so a
        verbose-mode caller knows the result is incomplete.
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
        assert result["notice"] is not None
        assert "Showing 3 of 5" in result["notice"]
        assert "invoices" in result["notice"]


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
        outstanding = gb.get_outstanding_invoices(compact=False)
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
        outstanding = gb.get_outstanding_invoices(compact=False)
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
        outstanding = gb.get_outstanding_invoices(owner_type="vendor", compact=False)
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
        outstanding = gb.get_outstanding_invoices(owner_type="vendor", compact=False)
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
