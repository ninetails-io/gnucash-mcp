"""Business tools: customers, vendors, billterms, invoices, bills.

Registered only when the 'business' module is enabled via --modules.
"""

import json

from gnucash_mcp.logging_config import audit_log
from gnucash_mcp.tools._helpers import _gate_owner_type, _json, safe_tool


def register(mcp, get_book) -> None:
    """Attach business tools to the FastMCP server."""

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="create", entity_type="customer")
    def create_customer(
        name: str,
        currency: str | None = None,
        notes: str = "",
        address: dict | None = None,
    ) -> str:
        """Create a new customer.

        Args:
            name: Customer name (e.g., "Acme Corp").
            currency: ISO currency code (e.g., "USD", "EUR").
                      Defaults to book's default currency.
            notes: Optional notes.
            address: Optional address with keys: name, addr1, addr2,
                     addr3, addr4, phone, fax, email.
        """
        book = get_book()
        result = book.create_customer(
            name=name, currency=currency, notes=notes, address=address,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def list_customers(
        active_only: bool = True,
        verbose: bool = False,
    ) -> str:
        """List all customers.

        Returns a compact one-line-per-customer format by default.
        Use verbose=true for full JSON with guid, address, notes, etc.

        Args:
            active_only: If True, only show active customers. Default True.
            verbose: If true, return full JSON details for each customer.
        """
        book = get_book()
        result = book.list_customers(active_only=active_only, compact=not verbose)
        if verbose:
            return json.dumps(result, indent=2)
        return result

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def get_customer(
        id: str,
    ) -> str:
        """Get details for a specific customer by ID.

        Args:
            id: Customer ID (e.g., "000001"). This is the human-readable
                ID shown in GnuCash, not the internal GUID.
        """
        book = get_book()
        result = book.get_customer(customer_id=id)
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="create", entity_type="vendor")
    def create_vendor(
        name: str,
        currency: str | None = None,
        notes: str = "",
        address: dict | None = None,
    ) -> str:
        """Create a new vendor.

        Args:
            name: Vendor name (e.g., "Office Depot").
            currency: ISO currency code (e.g., "USD", "EUR").
                      Defaults to book's default currency.
            notes: Optional notes.
            address: Optional address with keys: name, addr1, addr2,
                     addr3, addr4, phone, fax, email.
        """
        book = get_book()
        result = book.create_vendor(
            name=name, currency=currency, notes=notes, address=address,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def list_vendors(
        active_only: bool = True,
        verbose: bool = False,
    ) -> str:
        """List all vendors.

        Returns a compact one-line-per-vendor format by default.
        Use verbose=true for full JSON with guid, address, notes, etc.

        Args:
            active_only: If True, only show active vendors. Default True.
            verbose: If true, return full JSON details for each vendor.
        """
        book = get_book()
        result = book.list_vendors(active_only=active_only, compact=not verbose)
        if verbose:
            return json.dumps(result, indent=2)
        return result

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def get_vendor(
        id: str,
    ) -> str:
        """Get details for a specific vendor by ID.

        Args:
            id: Vendor ID (e.g., "000001"). This is the human-readable
                ID shown in GnuCash, not the internal GUID.
        """
        book = get_book()
        result = book.get_vendor(vendor_id=id)
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="create", entity_type="employee")
    def create_employee(
        name: str,
        currency: str | None = None,
        address: dict | None = None,
    ) -> str:
        """Create a new employee.

        Employee has no ``notes`` field (unlike Customer and Vendor).
        Address shape is identical.

        Args:
            name: Employee name (e.g., "Jane Smith").
            currency: ISO currency code (e.g., "USD", "EUR").
                      Defaults to book's default currency.
            address: Optional address with keys: name, addr1, addr2,
                     addr3, addr4, phone, fax, email.
        """
        book = get_book()
        result = book.create_employee(
            name=name, currency=currency, address=address,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def list_employees(
        active_only: bool = True,
        verbose: bool = False,
    ) -> str:
        """List all employees.

        Returns a compact one-line-per-employee format by default.
        Use verbose=true for full JSON with guid, address, etc.

        Args:
            active_only: If True, only show active employees. Default True.
            verbose: If true, return full JSON details for each employee.
        """
        book = get_book()
        result = book.list_employees(active_only=active_only, compact=not verbose)
        if verbose:
            return json.dumps(result, indent=2)
        return result

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def get_employee(
        id: str,
    ) -> str:
        """Get details for a specific employee by ID.

        Args:
            id: Employee ID (e.g., "000001"). This is the human-readable
                ID shown in GnuCash, not the internal GUID.
        """
        book = get_book()
        result = book.get_employee(employee_id=id)
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="update", entity_type="customer")
    def update_customer(
        id: str,
        name: str | None = None,
        currency: str | None = None,
        notes: str | None = None,
        active: bool | None = None,
        address: dict | None = None,
    ) -> str:
        """Update an existing customer.

        Mutates only the fields supplied; everything else stays.
        Existing invoices/bills are not retroactively changed —
        ``currency`` here is the customer's *default* trading
        currency for future documents.

        Args:
            id: Customer ID (e.g., "000001").
            name: New display name.
            currency: New default ISO currency code (e.g., "EUR").
            notes: New notes. Pass "" to clear.
            active: ``false`` to deactivate (archive without
                deleting); ``true`` to reactivate.
            address: Partial address dict — keys ``name``,
                ``addr1``..``addr4``, ``phone``, ``fax``, ``email``.
                Merges onto the existing address (creating one if
                absent). To clear a sub-field, pass an empty
                string explicitly for that key.
        """
        book = get_book()
        result = book.update_customer(
            customer_id=id, name=name, currency=currency,
            notes=notes, active=active, address=address,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="update", entity_type="vendor")
    def update_vendor(
        id: str,
        name: str | None = None,
        currency: str | None = None,
        notes: str | None = None,
        active: bool | None = None,
        address: dict | None = None,
    ) -> str:
        """Update an existing vendor.

        Same semantics as ``update_customer``.

        Args:
            id: Vendor ID (e.g., "000001").
            name: New display name.
            currency: New default ISO currency code.
            notes: New notes. Pass "" to clear.
            active: ``false`` to deactivate; ``true`` to reactivate.
            address: Partial address dict (see ``update_customer``).
        """
        book = get_book()
        result = book.update_vendor(
            vendor_id=id, name=name, currency=currency,
            notes=notes, active=active, address=address,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="update", entity_type="employee")
    def update_employee(
        id: str,
        name: str | None = None,
        currency: str | None = None,
        active: bool | None = None,
        address: dict | None = None,
    ) -> str:
        """Update an existing employee.

        Employee has no ``notes`` column — parameter omitted
        accordingly. Otherwise identical to ``update_customer``.

        Args:
            id: Employee ID (e.g., "000001").
            name: New display name.
            currency: New default ISO currency code.
            active: ``false`` to deactivate; ``true`` to reactivate.
            address: Partial address dict (see ``update_customer``).
        """
        book = get_book()
        result = book.update_employee(
            employee_id=id, name=name, currency=currency,
            active=active, address=address,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="create", entity_type="billterm")
    def create_billterm(
        name: str,
        due_days: int = 30,
        description: str = "",
        discount_days: int = 0,
        discount_percent: str = "0",
    ) -> str:
        """Create a new billing term.

        Args:
            name: Billterm name (e.g., "Net 30").
            due_days: Number of days until payment is due. Default 30.
            description: Optional description.
            discount_days: Days within which early discount applies.
            discount_percent: Early payment discount percentage (e.g., "2" for 2%).
        """
        book = get_book()
        result = book.create_billterm(
            name=name, due_days=due_days, description=description,
            discount_days=discount_days, discount_percent=discount_percent,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def list_billterms(
        verbose: bool = False,
    ) -> str:
        """List all billing terms.

        Returns a compact one-line-per-term format by default.
        Use verbose=true for full JSON with guid, discount details, etc.

        Args:
            verbose: If true, return full JSON details for each billing term.
        """
        book = get_book()
        result = book.list_billterms(compact=not verbose)
        if verbose:
            return json.dumps(result, indent=2)
        return result

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="create", entity_type="invoice")
    def create_invoice(
        customer_id: str,
        date_opened: str | None = None,
        notes: str = "",
        currency: str | None = None,
        term: str | None = None,
        invoice_id: str | None = None,
    ) -> str:
        """Create a customer invoice.

        Args:
            customer_id: Customer ID (e.g., "000001").
            date_opened: Date in ISO format (YYYY-MM-DD). Defaults to today.
            notes: Optional notes.
            currency: ISO currency code. Defaults to the customer's
                currency, falling back to the book's default. Pass
                explicitly to override.
            term: Billterm name (e.g., "Net 30"). Optional.
            invoice_id: Custom invoice number (e.g., "INV-2026-001"). If omitted,
                auto-generates from the book's invoice counter.
        """
        book = get_book()
        result = book.create_invoice(
            customer_id=customer_id, date_opened=date_opened,
            notes=notes, currency=currency, term=term,
            invoice_id=invoice_id,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="create", entity_type="bill")
    def create_bill(
        vendor_id: str,
        date_opened: str | None = None,
        notes: str = "",
        currency: str | None = None,
        term: str | None = None,
        bill_id: str | None = None,
    ) -> str:
        """Create a vendor bill.

        Args:
            vendor_id: Vendor ID (e.g., "000001").
            date_opened: Date in ISO format (YYYY-MM-DD). Defaults to today.
            notes: Optional notes.
            currency: ISO currency code. Defaults to the vendor's
                currency, falling back to the book's default. Pass
                explicitly to override.
            term: Billterm name (e.g., "Net 30"). Optional.
            bill_id: Custom bill number (e.g., "BILL-2026-001"). If omitted,
                auto-generates from the book's bill counter.
        """
        book = get_book()
        result = book.create_bill(
            vendor_id=vendor_id, date_opened=date_opened,
            notes=notes, currency=currency, term=term,
            bill_id=bill_id,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="create", entity_type="entry")
    def add_invoice_entry(
        invoice_id: str,
        account: str,
        description: str,
        quantity: str,
        price: str,
    ) -> str:
        """Add a line item to a customer invoice.

        The invoice must not be posted yet. Entries represent individual
        goods or services being billed.

        Args:
            invoice_id: Invoice ID (e.g., "000001").
            account: Income account path (e.g., "Income:Sales").
            description: Line item description.
            quantity: Quantity as decimal string (e.g., "1", "2.5").
            price: Unit price as decimal string (e.g., "100.00").
        """
        book = get_book()
        result = book.add_invoice_entry(
            invoice_id=invoice_id, account=account,
            description=description, quantity=quantity, price=price,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="create", entity_type="entry")
    def add_bill_entry(
        bill_id: str,
        account: str,
        description: str,
        quantity: str,
        price: str,
    ) -> str:
        """Add a line item to a vendor bill.

        The bill must not be posted yet. Entries represent individual
        goods or services being billed.

        Args:
            bill_id: Bill ID (e.g., "000001").
            account: Expense account path (e.g., "Expenses:Office Supplies").
            description: Line item description.
            quantity: Quantity as decimal string (e.g., "1", "2.5").
            price: Unit price as decimal string (e.g., "50.00").
        """
        book = get_book()
        result = book.add_bill_entry(
            bill_id=bill_id, account=account,
            description=description, quantity=quantity, price=price,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="create", entity_type="voucher")
    def create_voucher(
        employee_id: str,
        date_opened: str | None = None,
        notes: str = "",
        currency: str | None = None,
        term: str | None = None,
        voucher_id: str | None = None,
    ) -> str:
        """Create an employee expense voucher.

        A voucher is the document an employee submits for
        reimbursement of out-of-pocket business expenses. It
        behaves like a vendor bill: post creates the obligation
        (debit expense accounts, credit A/P), pay settles it from
        a cash account.

        Args:
            employee_id: Employee ID (e.g., "000001").
            date_opened: Date in ISO format (YYYY-MM-DD). Defaults to today.
            notes: Optional notes.
            currency: ISO currency code. Defaults to the employee's
                currency, falling back to the book's default.
            term: Billterm name (e.g., "Net 30"). Optional —
                vouchers rarely use payment terms.
            voucher_id: Custom voucher number. If omitted,
                auto-generates from the book's voucher counter.
        """
        book = get_book()
        result = book.create_voucher(
            employee_id=employee_id, date_opened=date_opened,
            notes=notes, currency=currency, term=term,
            voucher_id=voucher_id,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="create", entity_type="entry")
    def add_voucher_entry(
        voucher_id: str,
        account: str,
        description: str,
        quantity: str,
        price: str,
    ) -> str:
        """Add a line item to an employee expense voucher.

        The voucher must not be posted yet. Each entry is typically
        a separate expense category (meals, supplies, travel).
        Account must be EXPENSE or ASSET.

        Args:
            voucher_id: Voucher ID (e.g., "000001").
            account: Expense account path (e.g.,
                "Expenses:Meals & Entertainment").
            description: Line item description.
            quantity: Quantity as decimal string (e.g., "1").
            price: Unit price as decimal string (e.g., "42.50").
        """
        book = get_book()
        result = book.add_voucher_entry(
            voucher_id=voucher_id, account=account,
            description=description, quantity=quantity, price=price,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="delete", entity_type="voucher")
    def delete_voucher(voucher_id: str) -> str:
        """Delete an unposted employee expense voucher.

        Automatically removes associated entries. Posted vouchers
        cannot be deleted — unpost first via ``unpost_invoice``,
        then delete.

        Args:
            voucher_id: Voucher ID (e.g., "000001").
        """
        book = get_book()
        result = book.delete_voucher(voucher_id=voucher_id)
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="create", entity_type="credit_note")
    def create_credit_note(
        owner_id: str,
        owner_type: str,
        applies_to_invoice_id: str | None = None,
        date_opened: str | None = None,
        notes: str = "",
        currency: str | None = None,
        term: str | None = None,
        credit_note_id: str | None = None,
    ) -> str:
        """Create a credit note against a customer invoice or
        vendor bill.

        A credit note reverses part or all of a posted invoice
        while preserving the original posting in the audit trail.
        At post time, posting direction reverses: customer credit
        notes debit Income / credit A/R (reducing receivables);
        vendor credit notes debit A/P / credit Expense (reducing
        payables). Settled either by refund (``pay_invoice``) or
        by netting against an outstanding invoice
        (``apply_credit_note``).

        Use ``add_credit_note_entry`` to add line items, then
        ``post_invoice`` to post.

        Args:
            owner_id: Customer or vendor ID (e.g., "000001").
            owner_type: "customer" or "vendor". Employees are not
                supported (GnuCash desktop has no UI for employee
                credit notes; use unpost_invoice + edit on the
                voucher to amend an employee reimbursement).
            applies_to_invoice_id: Optional source invoice / bill
                ID. Must belong to the same owner and use the
                same currency. Highly recommended for audit
                trail. Can be omitted for floating credit notes
                that will be applied later.
            date_opened: ISO date (YYYY-MM-DD). Defaults to today.
            notes: Free-text notes (e.g., reason for the credit).
            currency: ISO currency code. Inherited from source
                invoice when applies_to_invoice_id is given;
                otherwise from owner's currency or book default.
            term: Billterm name. Rarely used for credit notes.
            credit_note_id: Custom ID. Auto-generated from the
                shared invoice/bill counter when omitted.
        """
        owner_type = _gate_owner_type(owner_type)
        book = get_book()
        result = book.create_credit_note(
            owner_id=owner_id,
            owner_type=owner_type,
            applies_to_invoice_id=applies_to_invoice_id,
            date_opened=date_opened,
            notes=notes,
            currency=currency,
            term=term,
            credit_note_id=credit_note_id,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="create", entity_type="entry")
    def add_credit_note_entry(
        credit_note_id: str,
        account: str,
        description: str,
        quantity: str,
        price: str,
        owner_type: str | None = None,
    ) -> str:
        """Add a line item to a credit note.

        Mirrors ``add_invoice_entry`` / ``add_bill_entry`` but
        validates the target is in fact a credit note (the slot
        flag is the gate). Account type rules match the
        non-credit twin: INCOME for customer credit notes,
        EXPENSE/ASSET for vendor credit notes. Prices stay
        positive — the credit-note flag inverts posting direction
        at post time, not at entry-add time.

        Args:
            credit_note_id: Credit note ID (e.g., "000032").
            account: Account path appropriate for the owner type
                (INCOME for customer, EXPENSE/ASSET for vendor).
            description: Line item description.
            quantity: Quantity as decimal string.
            price: Unit price as decimal string.
            owner_type: Optional "customer" or "vendor"
                disambiguator for ID collisions. Usually omitted.
        """
        owner_type = _gate_owner_type(owner_type)
        book = get_book()
        result = book.add_credit_note_entry(
            credit_note_id=credit_note_id,
            account=account,
            description=description,
            quantity=quantity,
            price=price,
            owner_type=owner_type,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="apply", entity_type="credit_note")
    def apply_credit_note(
        credit_note_id: str,
        applies_to_invoice_id: str,
        amount: str | None = None,
        apply_date: str | None = None,
        owner_type: str | None = None,
    ) -> str:
        """Net a posted credit note against a posted invoice or
        bill from the same owner. No cash moves — the credit
        balance transfers between lots on the same A/R or A/P
        account.

        This is the most common credit-note settlement path: the
        bookkeeper issues a credit note against an overcharge,
        then nets it against the next invoice from that customer
        (or applies it to an outstanding bill on the vendor side).
        Use ``pay_invoice`` instead when the credit note will be
        settled by sending or receiving cash.

        Args:
            credit_note_id: The credit note to apply (must be
                posted).
            applies_to_invoice_id: The target invoice/bill (must
                be posted, same owner, same currency, same A/R
                or A/P post account).
            amount: Decimal-string amount to apply, in the
                document currency. Defaults to ``min(credit_note_
                remaining, target_remaining)`` — apply as much
                as possible.
            apply_date: ISO date for the netting transaction.
                Defaults to today.
            owner_type: Optional 'customer' or 'vendor'
                disambiguator for ID collisions.
        """
        owner_type = _gate_owner_type(owner_type)
        book = get_book()
        result = book.apply_credit_note(
            credit_note_id=credit_note_id,
            applies_to_invoice_id=applies_to_invoice_id,
            amount=amount,
            apply_date=apply_date,
            owner_type=owner_type,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="delete", entity_type="credit_note")
    def delete_credit_note(
        credit_note_id: str,
        owner_type: str | None = None,
    ) -> str:
        """Delete an unposted credit note.

        Validates the target is a credit note before deletion.
        Posted credit notes cannot be deleted — unpost first via
        ``unpost_invoice``, then delete.

        Args:
            credit_note_id: Credit note ID.
            owner_type: Optional "customer" or "vendor"
                disambiguator for ID collisions.
        """
        owner_type = _gate_owner_type(owner_type)
        book = get_book()
        result = book.delete_credit_note(
            credit_note_id=credit_note_id,
            owner_type=owner_type,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def list_invoices(
        owner_type: str | None = None,
        status: str | None = None,
        verbose: bool = False,
        limit: int = 50,
    ) -> str:
        """List invoices and/or vendor bills.

        Returns a compact one-line-per-invoice format by default.
        Use verbose=true for full JSON with GUIDs, dates, notes, etc.

        Args:
            owner_type: Filter by type: "customer" for invoices,
                        "vendor" for bills, or omit for all.
            status: Filter by status: "posted" or "open", or omit for all.
            verbose: If true, return full JSON details.
            limit: Maximum invoices to return. Defaults to 50, capped
                   at 250. Compact output appends a truncation notice
                   when results are clipped.
        """
        owner_type = _gate_owner_type(owner_type)
        book = get_book()
        result = book.list_invoices(
            owner_type=owner_type,
            status=status,
            compact=not verbose,
            limit=limit,
        )
        if verbose:
            return json.dumps(result, indent=2)
        return result

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def get_invoice(
        id: str,
        owner_type: str | None = None,
    ) -> str:
        """Get full details for an invoice or bill, including line items.

        Works for both customer invoices and vendor bills. Returns all
        entries with quantities, prices, and totals.

        Args:
            id: Invoice or bill ID (e.g., "000001"). This is the
                human-readable ID, not the internal GUID.
            owner_type: Filter by type: "customer" for invoices,
                        "vendor" for bills. Useful when an invoice and
                        bill share the same ID (independent counters).
        """
        owner_type = _gate_owner_type(owner_type)
        book = get_book()
        result = book.get_invoice(invoice_id=id, owner_type=owner_type)
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="post", entity_type="invoice")
    def post_invoice(
        id: str,
        post_account: str,
        post_date: str | None = None,
        due_date: str | None = None,
        description: str | None = None,
        owner_type: str | None = None,
    ) -> str:
        """Post a customer invoice or vendor bill.

        Posting creates a transaction in the A/R or A/P account and makes
        the invoice official. Once posted, entries cannot be added.

        Args:
            id: Invoice or bill ID (e.g., "000001").
            post_account: A/R or A/P account path (e.g., "Assets:Accounts Receivable").
            post_date: Date in ISO format (YYYY-MM-DD). Defaults to today.
            due_date: Payment due date (YYYY-MM-DD). Optional.
            description: Description for the posting transaction. Optional.
            owner_type: "customer" or "vendor" for disambiguation when IDs collide.
        """
        owner_type = _gate_owner_type(owner_type)
        book = get_book()
        result = book.post_invoice(
            invoice_id=id,
            post_account=post_account,
            post_date=post_date,
            due_date=due_date,
            description=description,
            owner_type=owner_type,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="unpost", entity_type="invoice")
    def unpost_invoice(
        id: str,
        owner_type: str | None = None,
    ) -> str:
        """Reverse a posted invoice or bill.

        Deletes the posting transaction and lot, and clears the
        invoice's posted-state metadata. The invoice returns to
        "open" state and can be edited or re-posted. Refuses if
        the invoice has any payments applied — void payments first,
        then unpost.

        Args:
            id: Invoice or bill ID (e.g., "000001").
            owner_type: "customer" or "vendor" for disambiguation
                when IDs collide.
        """
        owner_type = _gate_owner_type(owner_type)
        book = get_book()
        result = book.unpost_invoice(
            invoice_id=id,
            owner_type=owner_type,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="pay", entity_type="invoice")
    def pay_invoice(
        id: str,
        payment_account: str,
        amount: str,
        payment_date: str | None = None,
        description: str | None = None,
        owner_type: str | None = None,
        fx_account: str | None = None,
    ) -> str:
        """Record a payment against a posted invoice or bill.

        Creates a payment transaction from the specified bank/cash account
        to the invoice's A/R or A/P account. Partial payments are supported.

        For cross-currency payments where the rate moved between
        post-date and pay-date, a realized FX gain/loss split is
        booked. Pass ``fx_account`` to control routing; otherwise
        the server picks the unique INCOME/EXPENSE account whose
        leaf name matches "fx", "forex", "foreign exchange",
        "currency gain/loss", "exchange gain/loss", or "currency
        translation". When zero or multiple match, the canonical
        ``Income:Foreign Exchange Gain/Loss`` is used (auto-created
        if absent), and an ``fx_notice`` is returned listing
        ambiguous candidates so you can pass ``fx_account``
        explicitly next time.

        Args:
            id: Invoice or bill ID (e.g., "000001").
            payment_account: Bank or cash account for payment (e.g., "Assets:Checking").
            amount: Payment amount as decimal string (e.g., "500.00").
            payment_date: Payment date (YYYY-MM-DD). Defaults to today.
            description: Description for the payment transaction. Optional.
            owner_type: "customer" or "vendor" for disambiguation.
            fx_account: Optional INCOME or EXPENSE account to receive
                realized FX gain/loss (cross-currency payments only).
                Accepts a full path, %short GUID, or full 32-char GUID.
        """
        owner_type = _gate_owner_type(owner_type)
        book = get_book()
        result = book.pay_invoice(
            invoice_id=id,
            payment_account=payment_account,
            amount=amount,
            payment_date=payment_date,
            description=description,
            owner_type=owner_type,
            fx_account=fx_account,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="delete", entity_type="invoice")
    def delete_invoice(invoice_id: str) -> str:
        """Delete an unposted customer invoice.

        Automatically removes associated entries (line items). Posted invoices
        cannot be deleted — void them or issue a credit note instead.

        Args:
            invoice_id: Invoice ID (e.g., "000001" or "INV-2026-001").
        """
        book = get_book()
        result = book.delete_invoice(invoice_id=invoice_id)
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="delete", entity_type="bill")
    def delete_bill(bill_id: str) -> str:
        """Delete an unposted vendor bill.

        Automatically removes associated entries (line items). Posted bills
        cannot be deleted — void them or issue a credit note instead.

        Args:
            bill_id: Bill ID (e.g., "000001" or "BILL-2026-001").
        """
        book = get_book()
        result = book.delete_bill(bill_id=bill_id)
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="delete", entity_type="customer")
    def delete_customer(customer_id: str) -> str:
        """Delete a customer with no invoices.

        Customers with any invoices (posted or unposted) cannot be deleted.
        Delete the invoices first, then delete the customer.

        Args:
            customer_id: Customer ID (e.g., "000001").
        """
        book = get_book()
        result = book.delete_customer(customer_id=customer_id)
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="delete", entity_type="vendor")
    def delete_vendor(vendor_id: str) -> str:
        """Delete a vendor with no bills.

        Vendors with any bills (posted or unposted) cannot be deleted.
        Delete the bills first, then delete the vendor.

        Args:
            vendor_id: Vendor ID (e.g., "000001").
        """
        book = get_book()
        result = book.delete_vendor(vendor_id=vendor_id)
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="delete", entity_type="employee")
    def delete_employee(employee_id: str) -> str:
        """Delete an employee.

        Employees in the 1.3.0 release have no associated documents;
        the delete proceeds unconditionally after slot cleanup.

        Args:
            employee_id: Employee ID (e.g., "000001").
        """
        book = get_book()
        result = book.delete_employee(employee_id=employee_id)
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def get_outstanding_invoices(
        owner_type: str | None = None,
        customer_id: str | None = None,
        vendor_id: str | None = None,
        verbose: bool = False,
    ) -> str:
        """Get all posted invoices/bills with outstanding balances.

        Returns a compact one-line-per-doc format by default with action
        columns (due date, days past due, currency, BILL tag, owner).
        Sorted most-overdue-first so the bookkeeper sees the urgent
        items at the top.

        Use verbose=true for full JSON with ``original_amount`` /
        ``amount_paid`` / ``amount_due`` breakdown — the shape
        ``pay_invoice`` workflows expect.

        Args:
            owner_type: Filter by "customer" or "vendor". Omit for all.
            customer_id: Filter by specific customer ID.
            vendor_id: Filter by specific vendor ID.
            verbose: If true, return full JSON details.
        """
        owner_type = _gate_owner_type(owner_type)
        # When Business isn't loaded, vendor_id is also a vendor-only
        # surface — reject it the same way an explicit
        # owner_type='vendor' is rejected. _gate_owner_type already
        # handled owner_type; vendor_id needs its own check.
        if vendor_id is not None:
            from gnucash_mcp.server import is_module_enabled
            if not is_module_enabled("business"):
                raise ValueError(
                    "vendor_id filtering requires the Business module. "
                    "Restart with --modules=...,Business to access "
                    "vendor bills."
                )
        book = get_book()
        result = book.get_outstanding_invoices(
            owner_type=owner_type,
            customer_id=customer_id,
            vendor_id=vendor_id,
            compact=not verbose,
        )
        if verbose:
            return _json(result)
        return result

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def vendor_spending_report(
        start_date: str,
        end_date: str,
        vendor_id: str | None = None,
        verbose: bool = False,
    ) -> str:
        """Get spending breakdown by vendor for a period.

        Analyzes posted vendor bills to show total billed, total paid,
        and outstanding amounts per vendor.

        Returns a compact aligned text table by default. Use verbose=true
        for the full structured dict (programmatic consumers).

        Args:
            start_date: Start of period (YYYY-MM-DD).
            end_date: End of period (YYYY-MM-DD).
            vendor_id: Optional filter to a specific vendor.
            verbose: If true, return the structured dict.
        """
        book = get_book()
        result = book.vendor_spending_report(
            start_date=start_date,
            end_date=end_date,
            vendor_id=vendor_id,
            compact=not verbose,
        )
        if verbose:
            return _json(result)
        return result
