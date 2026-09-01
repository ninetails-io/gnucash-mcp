"""Business tools: customers, vendors, billterms, invoices, bills.

Registered only when the 'business' module is enabled via --modules.
"""

from gnucash_mcp.logging_config import audit_log
from gnucash_mcp.tools._helpers import (
    DocumentType,
    PartyType,
    _gate_document_type,
    _gate_party_type,
    BusinessAddressInput,
    BusinessNotes,
    BusinessNotesOptional,
    _gate_owner_type,
    _json,
    _resolve_id_alias,
    safe_tool,
)


def register(mcp, get_book) -> None:
    """Attach business tools to the FastMCP server."""

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="create", entity_type="customer")
    def create_party(
        party_type: PartyType,
        name: str,
        currency: str | None = None,
        notes: BusinessNotes = "",
        address: BusinessAddressInput | None = None,
    ) -> str:
        """Create a customer, vendor, or employee.

        Additive: each call creates a fresh party — names are NOT
        checked for duplicates, so list_parties first when unsure.
        Returns the assigned ID (e.g., "000001"), the handle every
        later call wants. ID counters are PER TYPE: customer 000001
        and vendor 000001 are different parties, which is why
        party_type is required everywhere.

        Args:
            party_type: "customer" (pays you), "vendor" (you pay),
                or "employee" (expense-voucher workflows).
            name: Party name (e.g., "Acme Corp", "Jane Smith").
            currency: ISO currency code (e.g., "USD", "EUR").
                Defaults to book's default currency.
            notes: Optional notes (max 4096 characters). Employees
                have no notes field — rejected, not ignored.
            address: Optional address with keys: name, addr1, addr2,
                addr3, addr4, phone, fax, email. Each sub-field
                capped at 1024 characters.
        """
        party_type = _gate_party_type(party_type)
        book = get_book()
        addr = address.model_dump() if address else None
        if party_type == "employee":
            if notes:
                raise ValueError(
                    "Employees have no notes field in GnuCash. "
                    "Omit notes, or keep employee context elsewhere."
                )
            result = book.create_employee(
                name=name, currency=currency, address=addr,
            )
        elif party_type == "vendor":
            result = book.create_vendor(
                name=name, currency=currency, notes=notes,
                address=addr,
            )
        else:
            result = book.create_customer(
                name=name, currency=currency, notes=notes,
                address=addr,
            )
        result["type"] = party_type
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def list_parties(
        party_type: PartyType | None = None,
        active_only: bool = True,
        verbose: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> str:
        """List customers, vendors, and/or employees.

        Leads with a ``Showing X-Y of Z`` line per type, then a
        compact one-line-per-party format by default. Page with
        ``offset``; ``limit=0`` returns the count only. Use
        verbose=true for full JSON with guid, address, notes, etc.

        Args:
            party_type: "customer", "vendor", or "employee". Omit
                for all three (sections in that order).
            active_only: If True, only show active parties. Default True.
            verbose: If false (default), compact text output — optimized
                for reading and token efficiency. If true, structured
                JSON, for when you need machine-readable fields rather
                than a report.
            limit: Page size per type (default 50, max 250). 0 = count only.
            offset: 0-indexed first row to return (default 0).
        """
        party_type = _gate_party_type(party_type)
        book = get_book()
        routes = {
            "customer": book.list_customers,
            "vendor": book.list_vendors,
            "employee": book.list_employees,
        }
        kinds = [party_type] if party_type else list(routes)
        results = {
            k: routes[k](
                active_only=active_only, compact=not verbose,
                limit=limit, offset=offset,
            )
            for k in kinds
        }
        if verbose:
            return _json(
                results[party_type] if party_type
                else {f"{k}s": v for k, v in results.items()}
            )
        if party_type:
            return results[party_type]
        return "\n\n".join(
            f"{k.upper()}S:\n{v}" for k, v in results.items()
        )

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def get_party(
        party_type: PartyType,
        id: str,
    ) -> str:
        """Get details for one customer, vendor, or employee by ID.

        Args:
            party_type: "customer", "vendor", or "employee" —
                required because ID counters collide across types
                (customer 000001 ≠ vendor 000001).
            id: Party ID (e.g., "000001"). This is the human-readable
                ID shown in GnuCash, not the internal GUID.
        """
        party_type = _gate_party_type(party_type)
        book = get_book()
        if party_type == "employee":
            result = book.get_employee(employee_id=id)
        elif party_type == "vendor":
            result = book.get_vendor(vendor_id=id)
        else:
            result = book.get_customer(customer_id=id)
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="update", entity_type="customer")
    def update_party(
        party_type: PartyType,
        id: str,
        name: str | None = None,
        currency: str | None = None,
        notes: BusinessNotesOptional = None,
        active: bool | None = None,
        address: BusinessAddressInput | None = None,
    ) -> str:
        """Update a customer's, vendor's, or employee's mutable fields.

        ``None`` = no change on every parameter; pass an empty
        string to clear ``notes``. Returns a diff-style response
        with the changed fields only.

        Args:
            party_type: "customer", "vendor", or "employee" (ID
                counters collide across types — always required).
            id: Party ID (e.g., "000001").
            name: New display name.
            currency: New ISO currency code (future documents only).
            notes: New notes; "" clears. Employees have no notes
                field — rejected, not ignored.
            active: Set active/inactive (inactive parties hide from
                default listings but keep their history).
            address: Full replacement address (see create_party).
        """
        party_type = _gate_party_type(party_type)
        book = get_book()
        addr = address.model_dump() if address else None
        if party_type == "employee":
            if notes is not None:
                raise ValueError(
                    "Employees have no notes field in GnuCash — "
                    "the notes parameter cannot be updated for "
                    "party_type='employee'."
                )
            result = book.update_employee(
                employee_id=id, name=name, currency=currency,
                active=active, address=addr,
            )
        elif party_type == "vendor":
            result = book.update_vendor(
                vendor_id=id, name=name, currency=currency,
                notes=notes, active=active, address=addr,
            )
        else:
            result = book.update_customer(
                customer_id=id, name=name, currency=currency,
                notes=notes, active=active, address=addr,
            )
        result["type"] = party_type
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="delete", entity_type="customer")
    def delete_party(
        party_type: PartyType,
        id: str,
    ) -> str:
        """Delete a customer, vendor, or employee.

        Blocked while the party has documents (invoices, bills,
        vouchers, credit notes) — the audit trail outranks tidiness.
        Prefer update_party(active=false) to retire a party while
        keeping its history.

        Args:
            party_type: "customer", "vendor", or "employee" (ID
                counters collide across types — always required).
            id: Party ID (e.g., "000001").
        """
        party_type = _gate_party_type(party_type)
        book = get_book()
        if party_type == "employee":
            result = book.delete_employee(employee_id=id)
        elif party_type == "vendor":
            result = book.delete_vendor(vendor_id=id)
        else:
            result = book.delete_customer(customer_id=id)
        result["type"] = party_type
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
        limit: int = 50,
        offset: int = 0,
    ) -> str:
        """List all billing terms.

        Leads with a ``Showing X-Y of Z billterms`` line, then a compact
        one-line-per-term format by default. Page with ``offset``;
        ``limit=0`` returns the count only. Use verbose=true for full
        JSON with guid, discount details, etc.

        Args:
            verbose: If false (default), compact text output — optimized
                for reading and token efficiency. If true, structured
                JSON, for when you need machine-readable fields rather
                than a report.
            limit: Page size (default 50, max 250). 0 = count only.
            offset: 0-indexed first row to return (default 0).
        """
        book = get_book()
        result = book.list_billterms(
            compact=not verbose, limit=limit, offset=offset
        )
        if verbose:
            return _json(result)
        return result

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="create", entity_type="taxtable")
    def create_taxtable(
        name: str,
        entries: list[dict],
    ) -> str:
        """Create a new sales-tax table.

        A taxtable holds one or more entries. Each entry contributes
        either a percentage rate or a flat-value surcharge routed to
        a specific GL account (ASSET for input-tax credit, LIABILITY
        for output sales tax payable). Multi-entry composites (e.g.,
        GST 5% + PST 7%) produce multiple tax splits per line at
        posting time.

        Args:
            name: Taxtable name, unique within the book
                (e.g., "CA Sales 7.25%", "BC GST+PST").
            entries: List of {type, amount, account} dicts.
                ``type``: "value" or "percentage".
                ``amount``: positive decimal as string. Percentages
                are the rate ("5.00" = 5%, not "0.05").
                ``account``: account path, %short-guid, or full GUID.
                Must be ASSET or LIABILITY type. All entries on a
                single taxtable must reference accounts in the same
                commodity.

        Example:
            create_taxtable(
                name="BC GST+PST",
                entries=[
                    {"type": "percentage", "amount": "5.00",
                     "account": "Liabilities:GST Payable"},
                    {"type": "percentage", "amount": "7.00",
                     "account": "Liabilities:PST Payable"},
                ],
            )
        """
        book = get_book()
        result = book.create_taxtable(name=name, entries=entries)
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def list_taxtables(
        name: str | None = None,
        verbose: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> str:
        """List all sales-tax tables, or get one by name.

        Leads with a ``Showing X-Y of Z taxtables`` line. Compact format
        (default): one line per taxtable with name, entry count, and
        per-entry rate→account routing. Page with ``offset``; ``limit=0``
        returns the count only. Verbose: structured JSON with resolved account
        paths and refcount.

        Args:
            name: Tax table name for a single-table detail lookup
                (entries, rates, account routing, refcount). All
                other parameters are ignored.
            verbose: If false (default), compact text output — optimized
                for reading and token efficiency. If true, structured
                JSON, for when you need machine-readable fields rather
                than a report.
            limit: Page size (default 50, max 250). 0 = count only.
            offset: 0-indexed first row to return (default 0).
        """
        book = get_book()
        if name is not None:
            return _json(book.get_taxtable(name=name))
        result = book.list_taxtables(
            compact=not verbose, limit=limit, offset=offset
        )
        if verbose:
            return _json(result)
        return result

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="update", entity_type="taxtable")
    def update_taxtable(
        name: str,
        new_name: str | None = None,
        entries: list[dict] | None = None,
        force: bool = False,
    ) -> str:
        """Update a sales-tax table's name and/or entries.

        Diff-style response: only changed fields are returned.

        **Entry replacement on a taxtable that's already in use** is
        destructive to FUTURE entries' tax math. Existing posted
        invoices retain their original splits (splits are stored,
        not derived), but new entries on any document will use the
        replacement entries' rates. When refcount > 0 and
        ``entries`` is given, ``force=True`` is required to proceed.

        Args:
            name: Current taxtable name.
            new_name: New name (optional).
            entries: Replacement entry list (optional). Same shape
                and validation as ``create_taxtable``.
            force: Required to replace entries when the taxtable
                is already referenced by document entries.
        """
        book = get_book()
        result = book.update_taxtable(
            name=name,
            new_name=new_name,
            entries=entries,
            force=force,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="delete", entity_type="taxtable")
    def delete_taxtable(name: str) -> str:
        """Delete a sales-tax table.

        Refuses when any Entry row references the taxtable (computed
        via SQL on the entries table). Voided invoices still pin
        their taxtables — voided entry rows persist for audit-trail
        purposes. Remove or re-assign referencing entries first.

        Args:
            name: Taxtable name.
        """
        book = get_book()
        result = book.delete_taxtable(name=name)
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="create", entity_type="invoice")
    def create_document(
        document_type: DocumentType,
        owner_id: str,
        party_type: PartyType | None = None,
        date_opened: str | None = None,
        notes: BusinessNotes = "",
        currency: str | None = None,
        term: str | None = None,
        id: str | None = None,
        job_id: str | None = None,
        applies_to_id: str | None = None,
    ) -> str:
        """Create a customer invoice, vendor bill, employee expense
        voucher, or credit note.

        The owner side derives from the document type — invoice →
        customer, bill → vendor, voucher → employee. Credit notes
        exist on both sides, so they alone require ``party_type``
        ("customer" or "vendor"). After creating, add line items
        with add_document_entry, then post_document to put it on
        the books.

        Args:
            document_type: "invoice", "bill", "voucher", or
                "credit_note".
            owner_id: The owning party's ID (customer ID for
                invoices, vendor ID for bills, employee ID for
                vouchers). ID counters are per type.
            party_type: Required for credit notes only ("customer"
                or "vendor" — which side the credit belongs to).
                Derived from document_type otherwise.
            date_opened: ISO date. Defaults to today (echoed in the
                response).
            notes: Optional notes (max 4096 characters).
            currency: ISO code. Defaults to the owner's currency,
                then the book default.
            term: Billterm name (e.g., "Net 30"). Optional.
            id: Custom document number; auto-generated when omitted.
            job_id: Optional Job to group under (invoices and bills;
                must belong to the same owner).
            applies_to_id: Credit notes only — the invoice/bill this
                credit note reverses. The link is PROVENANCE, not a
                constraint: apply_credit_note can net the credit
                against any open document from the same owner (its
                response notes the divergence when the applied
                target differs from this link).
        """
        document_type = _gate_document_type(document_type)
        book = get_book()
        if document_type == "credit_note":
            if party_type is None:
                raise ValueError(
                    "Credit notes exist on both sides — pass "
                    "party_type='customer' (reduces a receivable) "
                    "or party_type='vendor' (reduces a payable)."
                )
            party_type = _gate_party_type(party_type)
            if party_type == "employee":
                raise ValueError(
                    "party_type='employee' is not valid for credit "
                    "notes — use 'customer' or 'vendor'."
                )
            result = book.create_credit_note(
                owner_id=owner_id, owner_type=party_type,
                applies_to_invoice_id=applies_to_id,
                date_opened=date_opened, notes=notes,
                currency=currency, term=term, credit_note_id=id,
            )
        elif document_type == "bill":
            result = book.create_bill(
                vendor_id=owner_id, date_opened=date_opened,
                notes=notes, currency=currency, term=term,
                bill_id=id, job_id=job_id,
            )
        elif document_type == "voucher":
            result = book.create_voucher(
                employee_id=owner_id, date_opened=date_opened,
                notes=notes, currency=currency, term=term,
                voucher_id=id,
            )
        else:
            result = book.create_invoice(
                customer_id=owner_id, date_opened=date_opened,
                notes=notes, currency=currency, term=term,
                invoice_id=id, job_id=job_id,
            )
        result["type"] = document_type
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="create", entity_type="entry")
    def add_document_entry(
        document_type: DocumentType,
        id: str,
        account: str,
        description: str,
        quantity: str,
        price: str,
        party_type: PartyType | None = None,
        taxtable: str | None = None,
        tax_included: bool = False,
        notes: BusinessNotes = "",
        action: str = "",
    ) -> str:
        """Add a line item to a customer invoice, vendor bill,
        employee voucher, or credit note.

        Only unposted documents accept entries — unpost_document
        first to amend a posted one. Amounts are decimal strings;
        the line total is quantity × price (plus tax when a
        taxtable is attached).

        Args:
            document_type: "invoice", "bill", "voucher", or
                "credit_note".
            id: Document ID (e.g., "000001").
            account: Income account for invoices / credit notes;
                expense account for bills and vouchers. Full path,
                %short GUID, or full GUID.
            description: Line item description.
            quantity: Quantity as a decimal string (e.g., "3").
            price: Unit price as a decimal string (e.g., "125.00").
            party_type: Credit notes only — disambiguates when a
                customer and vendor credit note share an ID.
            taxtable: Tax table name to apply. Optional.
            tax_included: Whether price already includes tax.
            notes: Optional entry notes.
            action: Optional entry action label (e.g., "Hours").
        """
        document_type = _gate_document_type(document_type)
        book = get_book()
        if document_type == "credit_note":
            result = book.add_credit_note_entry(
                credit_note_id=id, account=account,
                description=description, quantity=quantity,
                price=price, owner_type=party_type,
                taxtable=taxtable, tax_included=tax_included,
                notes=notes, action=action,
            )
        elif document_type == "bill":
            result = book.add_bill_entry(
                bill_id=id, account=account,
                description=description, quantity=quantity,
                price=price, taxtable=taxtable,
                tax_included=tax_included, notes=notes,
                action=action,
            )
        elif document_type == "voucher":
            result = book.add_voucher_entry(
                voucher_id=id, account=account,
                description=description, quantity=quantity,
                price=price, taxtable=taxtable,
                tax_included=tax_included, notes=notes,
                action=action,
            )
        else:
            result = book.add_invoice_entry(
                invoice_id=id, account=account,
                description=description, quantity=quantity,
                price=price, taxtable=taxtable,
                tax_included=tax_included, notes=notes,
                action=action,
            )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="delete", entity_type="invoice")
    def delete_document(
        document_type: DocumentType,
        id: str,
        party_type: PartyType | None = None,
    ) -> str:
        """Delete an UNPOSTED customer invoice, vendor bill,
        employee voucher, or credit note.

        Posted documents are on the books — unpost_document first
        (payments block unposting; the audit trail outranks
        tidiness).

        Args:
            document_type: "invoice", "bill", "voucher", or
                "credit_note".
            id: Document ID (e.g., "000001").
            party_type: Owner side, credit notes only — pass it when
                a credit note's ID collides with a document of the
                same ID on the other side (ID counters are per
                type).
        """
        document_type = _gate_document_type(document_type)
        # The typed species imply their side; only credit notes
        # exist on both sides and can collide. Refuse a meaningless
        # party_type loudly rather than silently ignoring it.
        if party_type is not None and document_type != "credit_note":
            raise ValueError(
                f"party_type only applies to "
                f"document_type='credit_note' (a "
                f"{document_type}'s owner side is implied by its "
                f"type). Omit party_type."
            )
        book = get_book()
        if document_type == "credit_note":
            result = book.delete_credit_note(
                credit_note_id=id,
                owner_type=_gate_owner_type(party_type)
                if party_type else None,
            )
        elif document_type == "bill":
            result = book.delete_bill(bill_id=id)
        elif document_type == "voucher":
            result = book.delete_voucher(voucher_id=id)
        else:
            result = book.delete_invoice(invoice_id=id)
        result["type"] = document_type
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
        Use ``pay_document`` instead when the credit note will be
        settled by sending or receiving cash.

        Args:
            credit_note_id: The credit note to apply (must be
                posted).
            applies_to_invoice_id: The target invoice/bill (must
                be posted, same owner, same currency, same A/R
                or A/P post account). Need not be the document
                the credit note was created against — that link
                is provenance, and the response notes the
                divergence when this target differs from it.
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
    @audit_log(classification="read")
    def list_documents(
        document_type: DocumentType | None = None,
        party_type: PartyType | None = None,
        status: str | None = None,
        verbose: bool = False,
        limit: int = 50,
        job_id: str | None = None,
        offset: int = 0,
    ) -> str:
        """List customer invoices, vendor bills, employee vouchers,
        and credit notes.

        Leads with a ``Showing X-Y of Z invoices (date range)`` line,
        then a compact one-line-per-invoice format by default. Page with
        ``offset``; ``limit=0`` returns the count only. Use verbose=true
        for structured JSON with GUIDs, dates, notes, etc.

        Status vocabulary (shared by every invoice/bill tool):
        **open** = created and editable, not yet booked to A/R//A/P —
        not payable. **posted** = booked to A/R//A/P with a lot
        tracking its balance — payable. **paid** = posted with a zero
        remaining balance (lot closed). **outstanding** = posted with
        a remaining balance — the unpaid subset; get it directly from
        ``get_outstanding_documents`` rather than deriving it here.
        The ``status`` filter below covers document state
        (open/posted) only; settlement state lives on the lot.

        Args:
            status: Filter by status: "posted" or "open", or omit for all.
            document_type: Filter to one document kind
                ("invoice", "bill", "voucher", "credit_note").
                Omit for all.
            party_type: Filter by owner side ("customer",
                "vendor"). Omit for all.
            verbose: If false (default), compact text output — optimized
                for reading and token efficiency. If true, structured
                JSON, for when you need machine-readable fields rather
                than a report.
            limit: Page size (default 50, max 250). 0 = count only.
            job_id: Filter to invoices grouped under a specific
                job — useful for the "what's part of this
                engagement?" listing pattern.
            offset: 0-indexed first row to return (default 0).
        """
        owner_type = party_type if party_type else {
            "invoice": "customer", "bill": "vendor",
            "voucher": "employee",
        }.get(_gate_document_type(document_type) if document_type else None)
        owner_type = _gate_owner_type(owner_type)
        book = get_book()
        result = book.list_invoices(
            doc_type=document_type,
            owner_type=owner_type,
            status=status,
            compact=not verbose,
            limit=limit,
            job_id=job_id,
            offset=offset,
        )
        if verbose:
            return _json(result)
        return result

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def get_document(
        id: str,
        document_type: DocumentType | None = None,
        party_type: PartyType | None = None,
    ) -> str:
        """Get full details for a customer invoice, vendor bill,
        employee voucher, or credit note, including line items.

        Returns all entries with quantities, prices, and totals;
        the response's ``type`` field names the document kind.

        Status vocabulary: open = editable, not yet booked; posted =
        on the books, payable; paid = remaining balance zero. The
        full definitions live on ``list_documents``; the unpaid list
        is ``get_outstanding_documents``.

        Args:
            id: Document ID (e.g., "000001"). This is the
                human-readable ID, not the internal GUID.
            document_type: "invoice", "bill", "voucher", or
                "credit_note" — disambiguates when IDs collide
                across per-type counters.
            party_type: Owner side ("customer"/"vendor") — needed
                only for credit notes, which exist on both sides.
        """
        owner_type = party_type if party_type else {
            "invoice": "customer", "bill": "vendor",
            "voucher": "employee",
        }.get(_gate_document_type(document_type) if document_type else None)
        owner_type = _gate_owner_type(owner_type)
        book = get_book()
        result = book.get_invoice(invoice_id=id, owner_type=owner_type)
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="post", entity_type="invoice")
    def post_document(
        id: str,
        post_account: str,
        post_date: str | None = None,
        due_date: str | None = None,
        description: str | None = None,
        document_type: DocumentType | None = None,
        party_type: PartyType | None = None,
        force: bool = False,
    ) -> str:
        """Post a customer invoice, vendor bill, employee voucher,
        or credit note to A/R or A/P.

        Posting creates a transaction in the A/R or A/P account and makes
        the invoice official. Once posted, entries cannot be added.

        For a foreign-currency document, the exchange rate is etched
        at posting and cannot be updated retroactively. If the latest
        price for the invoice currency is more than 7 days from the
        post date (the ``GNUCASH_FX_GUARD_DAYS`` window), posting is
        refused with a ``stale_fx_rate`` error — run ``create_price``
        for a rate near the post date, then retry, or pass
        ``force=True`` to post with the stale rate (recorded in the
        response and audit log as ``fx_stale``/"forced"). A rate
        beyond the 90-day staleness cap cannot be forced.

        Args:
            id: Document ID (e.g., "000001").
            post_account: A/R or A/P account path (e.g., "Assets:Accounts Receivable").
            post_date: Date in ISO format (YYYY-MM-DD). Defaults to today.
            due_date: Payment due date (YYYY-MM-DD). Optional.
            description: Description for the posting transaction. Optional.
            document_type: "invoice", "bill", "voucher", or
                "credit_note" — disambiguates when IDs collide.
            party_type: Owner side, credit notes only.
            force: Override the stale-FX-rate guard and post with a
                7–90 day stale rate. Default False.
        """
        owner_type = party_type if party_type else {
            "invoice": "customer", "bill": "vendor",
            "voucher": "employee",
        }.get(_gate_document_type(document_type) if document_type else None)
        owner_type = _gate_owner_type(owner_type)
        book = get_book()
        result = book.post_invoice(
            invoice_id=id,
            post_account=post_account,
            post_date=post_date,
            due_date=due_date,
            description=description,
            owner_type=owner_type,
            force=force,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="unpost", entity_type="invoice")
    def unpost_document(
        id: str,
        document_type: DocumentType | None = None,
        party_type: PartyType | None = None,
    ) -> str:
        """Reverse a posted customer invoice, vendor bill, employee
        voucher, or credit note (each keeps its type through the
        round-trip).

        Deletes the posting transaction and lot, and clears the
        invoice's posted-state metadata. The invoice returns to
        "open" state and can be edited or re-posted. Refuses if
        the invoice has any payments applied — void payments first,
        then unpost.

        Args:
            id: Document ID (e.g., "000001").
            document_type: "invoice", "bill", "voucher", or
                "credit_note" — disambiguates when IDs collide.
            party_type: Owner side, credit notes only.
        """
        owner_type = party_type if party_type else {
            "invoice": "customer", "bill": "vendor",
            "voucher": "employee",
        }.get(_gate_document_type(document_type) if document_type else None)
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
    def pay_document(
        id: str,
        payment_account: str,
        amount: str,
        payment_date: str | None = None,
        description: str | None = None,
        document_type: DocumentType | None = None,
        party_type: PartyType | None = None,
        fx_account: str | None = None,
        apply_discount: bool = False,
        discount_account: str | None = None,
        force: bool = False,
        memo: str = "",
        dry_run: bool = False,
    ) -> str:
        """Record a payment against a posted customer invoice,
        vendor bill, employee voucher, or credit note.

        Creates a payment transaction from the specified bank/cash account
        to the document's A/R or A/P account. Partial payments are supported.

        ``dry_run=true`` rehearses the payment without booking it:
        the full validation, conversion, discount, and FX pipeline
        runs and the response shows the proposed splits, the
        remaining balance after, whether the invoice would settle in
        full, and any account the real call would auto-create. Same
        inputs, same code path — a rehearsal that succeeds is a
        payment that will book. Recommended before complex payments
        (cross-currency, discounts, credit-note refunds).

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

        For invoices with early-payment-discount terms (e.g.,
        "2/10 Net 30" = 2% off if paid within 10 days), pass
        ``apply_discount=True`` to settle via discount. The tool
        validates that the invoice has discount terms, the payment
        date is within the discount window, and the shortfall
        matches the expected discount on pre-tax principal. Each
        failure mode rejects with a specific error rather than
        silently downgrading to a partial payment. ``discount_account``
        controls routing the same way ``fx_account`` does
        (auto-resolves to ``Expenses:Sales Discounts`` for customer
        payments, ``Income:Purchase Discounts Taken`` for vendor
        bill payments).

        Args:
            id: Document ID (e.g., "000001").
            payment_account: Bank or cash account for payment (e.g., "Assets:Checking").
            amount: Payment amount as decimal string (e.g., "500.00").
            payment_date: Payment date (YYYY-MM-DD). Defaults to today.
            description: Description for the payment transaction. Optional.
            document_type: "invoice", "bill", "voucher", or
                "credit_note" — disambiguates when IDs collide.
            party_type: Owner side, credit notes only.
            fx_account: Optional INCOME or EXPENSE account to receive
                realized FX gain/loss (cross-currency payments only).
                Accepts a full path, %short GUID, or full 32-char GUID.
            apply_discount: When True, treat this payment as the
                final settlement and absorb the early-payment
                discount from the invoice's billterm. Default False
                — explicit opt-in. Hard-rejects on credit notes
                (refunds don't take discounts).
            discount_account: Optional INCOME or EXPENSE account to
                receive the discount split. Auto-resolves when
                omitted. Accepts full path, %short GUID, or full
                32-char GUID.
            force: Override the stale-FX-rate guard. A cross-currency
                payment etches the rate at pay time; if the latest
                price is 7–90 days from the payment date the payment
                is refused with ``stale_fx_rate`` unless ``force=True``
                (the override is recorded as ``fx_stale``/"forced").
                A rate beyond the 90-day cap cannot be forced.
            memo: Optional memo for the bank-account split (e.g.,
                check number or wire reference). ``description``
                names the whole transaction; ``memo`` annotates the
                cash movement.
            dry_run: When True, rehearse without writing — returns
                the proposed splits and projected outcome instead of
                booking. Default False.

        Returns:
            ``status`` is ``"paid"`` when the document settles to
            zero, ``"partial"`` when a balance remains, and
            ``"would_pay"`` on dry runs — plus the amount paid,
            remaining balance, and transaction reference.
        """
        owner_type = party_type if party_type else {
            "invoice": "customer", "bill": "vendor",
            "voucher": "employee",
        }.get(_gate_document_type(document_type) if document_type else None)
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
            apply_discount=apply_discount,
            discount_account=discount_account,
            force=force,
            memo=memo,
            dry_run=dry_run,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="create", entity_type="job")
    def create_job(
        owner_id: str,
        owner_type: str,
        name: str,
        reference: str = "",
    ) -> str:
        """Create a job for a customer or vendor.

        A job groups invoices (or bills) from one counterparty
        under a project-level container. Useful when a single
        customer has multiple distinct engagements (e.g., 'API
        Rewrite' and 'Q3 Maintenance') that should be reported
        on separately even though invoices flow to the same A/R.

        Args:
            owner_id: Customer or vendor ID (e.g., "000001").
            owner_type: "customer" or "vendor". Employees are
                not supported (no GnuCash desktop UI for
                employee jobs).
            name: Human-readable job name (e.g., "API Rewrite").
            reference: Optional reference string (PO number,
                project code).
        """
        owner_type = _gate_owner_type(owner_type)
        book = get_book()
        result = book.create_job(
            owner_id=owner_id,
            owner_type=owner_type,
            name=name,
            reference=reference,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def list_jobs(
        id: str | None = None,
        owner_type: str | None = None,
        owner_id: str | None = None,
        active_only: bool = True,
        verbose: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> str:
        """List jobs, optionally filtered.

        Leads with a ``Showing X-Y of Z jobs`` line; page with
        ``offset``, or pass ``limit=0`` for the count only.

        Pass ``id`` for one job's full details (name, owner,
        active state, linked invoice/bill IDs) — the exact-lookup
        mode that replaced ``get_job``.

        Args:
            id: Job ID for a single-job detail lookup (e.g.,
                "000001"). All other filters are ignored.
            owner_type: Filter by "customer" or "vendor". Omit
                for all.
            owner_id: Filter by specific customer or vendor ID
                (requires owner_type).
            active_only: If True (default), exclude inactive jobs.
            verbose: If false (default), compact text output — optimized
                for reading and token efficiency. If true, structured
                JSON, for when you need machine-readable fields rather
                than a report.
            limit: Page size (default 50, max 250). 0 = count only.
            offset: 0-indexed first row to return (default 0).
        """
        book = get_book()
        if id is not None:
            return _json(book.get_job(job_id=id))
        owner_type = _gate_owner_type(owner_type)
        result = book.list_jobs(
            owner_type=owner_type,
            owner_id=owner_id,
            active_only=active_only,
            compact=not verbose,
            limit=limit,
            offset=offset,
        )
        # Match the other list_* tools' verbose pattern: all
        # list_* verbose returns route through
        # _json so the response shape is uniform; a
        # ``json.dumps(indent=2)`` form adds 40-60% bloat from
        # indentation and skips the ``_strip_noise`` pass.
        if verbose:
            return _json(result)
        return result

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="update", entity_type="job")
    def update_job(
        job_id: str,
        name: str | None = None,
        reference: str | None = None,
        active: bool | None = None,
    ) -> str:
        """Update a job's name, reference, or active state.

        Any subset of fields can be passed; unspecified fields
        are left unchanged. Returns a diff-style response.

        Args:
            job_id: Job ID.
            name: New name (optional).
            reference: New reference (optional).
            active: New active flag — pass False to deactivate a
                completed job without deleting it (preserves
                history).
        """
        book = get_book()
        result = book.update_job(
            job_id=job_id,
            name=name,
            reference=reference,
            active=active,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def get_job_report(job_id: str) -> str:
        """Per-job summary: billed / paid / outstanding totals
        across all linked invoices, plus the per-invoice
        breakdown.

        Totals are returned as ``totals_by_currency`` (a dict
        keyed by ISO currency code) so the same shape works
        whether the job's invoices share a currency or span
        multiple. Both posted and unposted (draft) invoices are
        included — drafts contribute their face value as
        ``billed`` + ``outstanding`` with ``paid=0``, so the
        report shows the full pipeline.

        Args:
            job_id: Job ID (e.g., "000001").
        """
        book = get_book()
        result = book.get_job_report(job_id=job_id)
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="delete", entity_type="job")
    def delete_job(job_id: str, force: bool = False) -> str:
        """Delete a job.

        Refuses by default when invoices/bills are linked to the
        job (data-loss prevention). ``force=True`` re-parents
        every linked invoice back to its underlying customer or
        vendor before deleting the job row, preserving invoice
        history. Use ``update_job(active=False)`` instead if you
        want to keep the job in place but mark the project done.

        Args:
            job_id: Job ID.
            force: If True, re-parent linked invoices instead of
                refusing. Default False.
        """
        book = get_book()
        result = book.delete_job(job_id=job_id, force=force)
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def get_outstanding_documents(
        party_type: PartyType | None = None,
        customer_id: str | None = None,
        vendor_id: str | None = None,
        verbose: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> str:
        """Get all posted customer invoices, vendor bills, employee
        vouchers, and credit notes with outstanding balances.

        This is the authoritative unpaid list: outstanding = posted
        with a remaining balance > 0. One call answers "what is
        actually unpaid?" — no need to combine ``list_documents`` and
        ``get_document`` (full status vocabulary on ``list_documents``).

        Leads with a ``Showing X-Y of Z invoices (date range)`` line,
        then a compact one-line-per-doc format by default with action
        columns (due date, days past due, currency, BILL tag, owner).
        Sorted most-overdue-first so the bookkeeper sees the urgent
        items at the top. Page with ``offset``; ``limit=0`` returns the
        count only.

        Use verbose=true for structured JSON with ``original_amount`` /
        ``amount_paid`` / ``amount_due`` breakdown — the shape
        ``pay_document`` workflows expect.

        Args:
            party_type: Filter by "customer" or "vendor". Omit for all.
            customer_id: Filter by specific customer ID.
            vendor_id: Filter by specific vendor ID.
            verbose: If false (default), compact text output — optimized
                for reading and token efficiency. If true, structured
                JSON, for when you need machine-readable fields rather
                than a report.
            limit: Page size (default 50, max 250). 0 = count only.
            offset: 0-indexed first row to return (default 0).
        """
        owner_type = _gate_owner_type(party_type)
        # When Business isn't loaded, vendor_id is also a vendor-only
        # surface — reject it the same way an explicit
        # owner_type='vendor' is rejected. _gate_owner_type already
        # handled owner_type; vendor_id needs its own check.
        if vendor_id is not None:
            from gnucash_mcp.server import is_module_enabled
            # Check the leaf (``business_complete``) rather than the
            # ``business`` group alias, so a user who explicitly
            # picked the vendor-side carve-out also gets vendor_id
            # filtering. See _gate_owner_type for the rationale.
            if not is_module_enabled("business_complete"):
                raise ValueError(
                    "vendor_id filtering requires the business module. "
                    "Restart with --modules=business (or add "
                    "business_complete to your current selection) to "
                    "access vendor bills."
                )
        book = get_book()
        result = book.get_outstanding_invoices(
            owner_type=owner_type,
            customer_id=customer_id,
            vendor_id=vendor_id,
            compact=not verbose,
            limit=limit,
            offset=offset,
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
        group_by: str | None = None,
    ) -> str:
        """Get spending breakdown by vendor for a period.

        Analyzes posted vendor bills to show total billed, total paid,
        and outstanding amounts per vendor.

        Returns a compact aligned text table by default. Use verbose=true
        for the structured dict (programmatic consumers).

        Args:
            start_date: Start of period (YYYY-MM-DD).
            end_date: End of period (YYYY-MM-DD).
            vendor_id: Optional filter to a specific vendor.
            verbose: If false (default), compact text output — optimized
                for reading and token efficiency. If true, structured
                JSON, for when you need machine-readable fields rather
                than a report.
            group_by: Optional "month", "quarter", or "year" — split the
                range into sub-period columns of total billed per vendor
                and return a multi-period TSV table. Overrides verbose.
        """
        book = get_book()
        result = book.vendor_spending_report(
            start_date=start_date,
            end_date=end_date,
            vendor_id=vendor_id,
            compact=not verbose,
            group_by=group_by,
        )
        if group_by:
            return result
        if verbose:
            return _json(result)
        return result
