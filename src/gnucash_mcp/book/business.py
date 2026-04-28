"""BusinessMixin — customers, vendors, billterms, invoices, bills.

Covers the piecash business ledger surface: CRUD for customers and
vendors, customer invoices and vendor bills with entries, posting,
payment, outstanding-balance reporting, and per-vendor spending
summaries.

Depends on shared helpers from BaseGnuCashBook (via MRO):
  - self.open, self._find_account, self._require_default_currency

Several tools require raw-SQL inserts because piecash blocks the
constructors for Billterm, Invoice, and Entry (they are read-only
in the ORM). All raw inserts are paired with `_verify_write` /
`_verify_composite_write` from _base.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

import piecash

from gnucash_mcp.book._base import (
    _to_date,
    _to_decimal,
    _verify_composite_write,
    _verify_write,
)
from gnucash_mcp._format import _apply_limit


def _safe_date_posted(inv):
    """Read ``inv.date_posted`` defensively.

    Returns the datetime (or whatever the ORM gives back) when
    the column holds a real value, or ``None`` when the column
    is NULL, empty, or malformed enough that piecash's
    ``_DateTime`` TypeDecorator raises ``ValueError`` on access.

    The bookkeeper hit this on Alex Chen-Morales's book: a
    freshly auto-id'd bill's ``date_posted`` came back as ``''``
    in SQL. SQLAlchemy's regex-based DATETIME parser raises
    "Couldn't parse datetime string" when reading that — a hard
    crash on a never-posted document. Wrapping the access lets
    every caller treat the document as not-posted gracefully.
    """
    try:
        dp = inv.date_posted
        return dp if dp else None
    except (ValueError, TypeError):
        return None


def _is_invoice_posted(inv) -> bool:
    """True iff ``inv`` has a real datetime in ``date_posted``.

    Single chokepoint for "is this document posted?" across the
    business module. Built on ``_safe_date_posted`` so the
    tolerant semantics (None / "" / unparseable values all read
    as not-posted) apply uniformly.
    """
    return _safe_date_posted(inv) is not None


class BusinessMixin:
    """Customer/vendor/invoice/bill operations."""

    # ── Finders and dict converters ───────────────────────────────

    @staticmethod
    def _find_customer(book, customer_id: str):
        """Find a customer by their human-readable ID (e.g., '000001')."""
        for c in book.customers:
            if c.id == customer_id:
                return c
        return None

    @staticmethod
    def _find_vendor(book, vendor_id: str):
        """Find a vendor by their human-readable ID (e.g., '000001')."""
        for v in book.vendors:
            if v.id == vendor_id:
                return v
        return None

    @staticmethod
    def _find_customer_by_guid(book, guid: str):
        """Find a customer by GUID."""
        for c in book.customers:
            if c.guid == guid:
                return c
        return None

    @staticmethod
    def _find_vendor_by_guid(book, guid: str):
        """Find a vendor by GUID."""
        for v in book.vendors:
            if v.guid == guid:
                return v
        return None

    # Path for the auto-created realized-FX-gain/loss income account.
    # Single credit-natural account: positive balance = net gain, negative
    # = net loss across the period. Named to match GAAP convention
    # ("Foreign Exchange Gain (Loss)") while staying one account for
    # simplicity.
    FX_GAIN_LOSS_PATH = "Income:Foreign Exchange Gain/Loss"

    def _get_or_create_fx_account(self, book):
        """Find or lazily create ``Income:Foreign Exchange Gain/Loss``.

        Created on first cross-currency pay whose post-date rate
        differs from its pay-date rate, so books without foreign-
        currency activity never accumulate an unused account.

        Raises:
            ValueError: If the parent ``Income`` account doesn't exist.
                Caller must create it first — we won't auto-create a
                top-level account.
        """
        fx_acct = self._find_account(book, self.FX_GAIN_LOSS_PATH)
        if fx_acct is not None:
            return fx_acct

        income = self._find_account(book, "Income")
        if income is None:
            raise ValueError(
                "Realized FX gain/loss on cross-currency payment needs "
                "an Income parent account, but none exists. Create "
                "Income (type=INCOME, placeholder) first."
            )

        default_currency = self._require_default_currency(book)
        # Construct — piecash.Account auto-adds to the session via the
        # parent linkage. Don't flush here: the caller is still building
        # the payment transaction, and orphan Split objects already in
        # the session would trip a NOT NULL on splits.tx_guid. The
        # final book.save() at the end of pay_invoice flushes everything
        # together with their now-set tx_guids.
        fx_acct = piecash.Account(
            name="Foreign Exchange Gain/Loss",
            type="INCOME",
            parent=income,
            commodity=default_currency,
            description=(
                "Realized gains and losses from cross-currency "
                "invoice settlements (rate drift between post-date "
                "and pay-date). Auto-created on first use."
            ),
        )
        return fx_acct

    @staticmethod
    def _rate_from_post_transaction(post_txn, invoice_currency):
        """Derive the exchange rate that was applied at invoice-posting.

        Inspects the posting transaction's splits for one whose account
        commodity differs from the invoice currency (the income, expense,
        or A/R side that received the rate). The ratio of |quantity|
        (account commodity) to |value| (transaction currency) is the
        rate that was in force at posting.

        Returns the Decimal rate, or None if no cross-currency split
        is present (same-currency post, or post predates the rate fix).
        """
        for s in post_txn.splits:
            if s.account.commodity == invoice_currency:
                continue
            s_value = abs(Decimal(str(s.value)))
            s_quantity = abs(Decimal(str(s.quantity)))
            if s_value > 0:
                return s_quantity / s_value
        return None

    @staticmethod
    def _find_exchange_rate(book, from_commodity, to_commodity, as_of: date):
        """Look up an exchange rate from book.prices for a cross-currency
        payment: 1 unit of ``from_commodity`` equals how many of
        ``to_commodity`` on or near ``as_of``.

        Prefers prices on or before ``as_of`` (most recent). Falls back to
        the earliest price after ``as_of`` if none exist before. Accepts
        inverse prices (e.g., a USD→EUR price can resolve EUR→USD by
        inversion).

        Skips prices with ``type='transaction'`` — those are auto-
        created at 1.0 by piecash when a cross-currency invoice is
        posted without an explicit rate, and would mask the absence of
        a real user-supplied rate.

        Returns a Decimal rate, or None if no usable (non-transaction)
        price exists in either direction.
        """
        if from_commodity == to_commodity:
            return Decimal("1")

        best_before_direct = None   # (days_before, Decimal rate)
        best_after_direct = None    # (days_after, Decimal rate)
        best_before_inverse = None
        best_after_inverse = None

        for p in book.prices:
            # Skip piecash's auto-created post-invoice default rates.
            if p.type == "transaction":
                continue

            p_date = _to_date(p.date)
            # Direct: p.commodity == from, p.currency == to → rate = p.value
            if p.commodity == from_commodity and p.currency == to_commodity:
                days = (as_of - p_date).days
                rate = Decimal(str(p.value))
                if days >= 0:
                    if best_before_direct is None or days < best_before_direct[0]:
                        best_before_direct = (days, rate)
                else:
                    if best_after_direct is None or -days < best_after_direct[0]:
                        best_after_direct = (-days, rate)
            # Inverse: p.commodity == to, p.currency == from → rate = 1/p.value
            elif p.commodity == to_commodity and p.currency == from_commodity:
                days = (as_of - p_date).days
                if Decimal(str(p.value)) == 0:
                    continue
                rate = Decimal("1") / Decimal(str(p.value))
                if days >= 0:
                    if best_before_inverse is None or days < best_before_inverse[0]:
                        best_before_inverse = (days, rate)
                else:
                    if best_after_inverse is None or -days < best_after_inverse[0]:
                        best_after_inverse = (-days, rate)

        # Priority: before-direct > before-inverse > after-direct > after-inverse
        for candidate in (
            best_before_direct, best_before_inverse,
            best_after_direct, best_after_inverse,
        ):
            if candidate is not None:
                return candidate[1]
        return None

    @staticmethod
    def _find_employee(book, employee_id: str):
        """Find an employee by their human-readable ID (e.g., '000001')."""
        for e in book.employees:
            if e.id == employee_id:
                return e
        return None

    @staticmethod
    def _find_invoice(book, invoice_id: str, owner_type: int | None = None):
        """Find an invoice/bill by human-readable ID.

        Self-heals malformed ``date_posted=''`` values to NULL on
        writable sessions before the ORM query runs. piecash's
        ``_DateTime`` TypeDecorator's regex parser raises
        ``ValueError: Couldn't parse datetime string: ''`` when
        loading a row whose ``date_posted`` is an empty string —
        a hard crash that blocks every subsequent invoice/bill
        operation. The bookkeeper hit this on Alex Chen-Morales's
        book where a freshly auto-id'd bill's ``date_posted``
        landed as ``''`` instead of NULL through some persistence
        path. Coercing to NULL upstream of the query is the only
        reliable fix; the ORM never sees the malformed value.

        piecash doesn't expose a readonly flag, so the heal is
        always attempted; the try/except absorbs the failure when
        the session is readonly. Books that need healing must be
        touched through a write operation at least once; after
        that, the cleanup persists and readonly operations
        succeed too.

        Args:
            book: piecash Book instance.
            invoice_id: Human-readable ID (e.g., '000001').
            owner_type: Filter by owner type (2=customer, 4=vendor).
                        None returns first match of either type.
        """
        from piecash.business.invoice import Invoice
        from sqlalchemy import text

        try:
            book.session.execute(
                text(
                    "UPDATE invoices SET date_posted = NULL "
                    "WHERE date_posted = ''"
                )
            )
            book.session.flush()
        except Exception:
            # Best-effort heal — readonly sessions, locked
            # connections, and other rare failures fall through;
            # never break a lookup over a self-heal attempt.
            pass

        query = book.session.query(Invoice).filter(Invoice.id == invoice_id)
        if owner_type is not None:
            query = query.filter(Invoice.owner_type == owner_type)
        return query.first()

    @staticmethod
    def _address_to_dict(entity) -> dict:
        """Build an address dict from a piecash Customer/Vendor, omitting
        any empty fields and dropping the `fax` field entirely (it's 2026).

        Returns {} when no address data is set — caller should drop the
        whole "address" key rather than emit an empty dict, since
        _strip_noise leaves empty dicts in place.
        """
        fields = {
            "name": entity.addr_name,
            "addr1": entity.addr_addr1,
            "addr2": entity.addr_addr2,
            "addr3": entity.addr_addr3,
            "addr4": entity.addr_addr4,
            "phone": entity.addr_phone,
            "email": entity.addr_email,
        }
        return {k: v for k, v in fields.items() if v}

    @staticmethod
    def _customer_to_dict(customer) -> dict:
        """Convert a piecash Customer to a serializable dict."""
        result = {
            "guid": customer.guid,
            "id": customer.id,
            "name": customer.name,
            "currency": customer.currency.mnemonic if customer.currency else None,
            "notes": customer.notes or "",
            "active": bool(customer.active),
        }
        address = BusinessMixin._address_to_dict(customer)
        if address:
            result["address"] = address
        return result

    @staticmethod
    def _vendor_to_dict(vendor) -> dict:
        """Convert a piecash Vendor to a serializable dict."""
        result = {
            "guid": vendor.guid,
            "id": vendor.id,
            "name": vendor.name,
            "currency": vendor.currency.mnemonic if vendor.currency else None,
            "notes": vendor.notes or "",
            "active": bool(vendor.active),
        }
        address = BusinessMixin._address_to_dict(vendor)
        if address:
            result["address"] = address
        return result

    @staticmethod
    def _employee_to_dict(employee) -> dict:
        """Convert a piecash Employee to a serializable dict.

        Employee's schema has no ``notes`` column (unlike Customer
        and Vendor — see docs/PIECASH_REFERENCE.md), so the response
        shape omits the ``notes`` key. Employee-specific fields
        (``acl`` / ``language`` / ``workday`` / ``rate``) are out of
        scope for the 1.3.0 CRUD surface and are not serialized.
        """
        result = {
            "guid": employee.guid,
            "id": employee.id,
            "name": employee.name,
            "currency": employee.currency.mnemonic if employee.currency else None,
            "active": bool(employee.active),
        }
        address = BusinessMixin._address_to_dict(employee)
        if address:
            result["address"] = address
        return result

    @staticmethod
    def _customer_to_compact_line(customer) -> str:
        """One-line compact: 'id  name  currency'."""
        return f"{customer.id}\t{customer.name}\t{customer.currency.mnemonic}"

    @staticmethod
    def _vendor_to_compact_line(vendor) -> str:
        """One-line compact: 'id  name  currency'."""
        return f"{vendor.id}\t{vendor.name}\t{vendor.currency.mnemonic}"

    @staticmethod
    def _employee_to_compact_line(employee) -> str:
        """One-line compact: 'id  name  currency'."""
        return f"{employee.id}\t{employee.name}\t{employee.currency.mnemonic}"

    @staticmethod
    def _billterm_to_dict(bt) -> dict:
        """Convert a Billterm to a serializable dict."""
        return {
            "guid": bt.guid,
            "name": bt.name,
            "description": bt.description or "",
            "type": bt.type,
            "due_days": bt.duedays,
            "discount_days": bt.discountdays if bt.discountdays else 0,
            "discount": str(bt.discount) if bt.discount else "0",
        }

    @staticmethod
    def _decimal_to_num_denom(value: Decimal) -> tuple[int, int]:
        """Convert a Decimal to numerator/denominator pair.

        Uses the decimal's exponent to determine appropriate denominator.
        E.g., Decimal("25.50") -> (2550, 100)
        """
        sign, digits, exp = value.as_tuple()
        if exp < 0:
            denom = 10 ** (-exp)
            num = int(value * denom)
        else:
            num = int(value)
            denom = 1
        return num, denom

    @staticmethod
    def _invoice_to_dict(invoice, entries=None) -> dict:
        """Convert a piecash Invoice to a serializable dict.

        `owner_type` (numeric 2/4) was redundant with the human-readable
        `type` field and is dropped. `is_posted` is derivable from
        `date_posted` (non-null = posted) and is dropped too.

        Args:
            invoice: piecash Invoice object.
            entries: Optional list of entry dicts. If None, entries are not included.
        """
        result = {
            "guid": invoice.guid,
            "id": invoice.id,
            "type": "bill" if invoice.owner_type == 4 else "invoice",
            "owner_guid": invoice.owner_guid,
            "date_opened": str(invoice.date_opened.date()) if invoice.date_opened else None,
            "date_posted": (
                str(_safe_date_posted(invoice).date())
                if _safe_date_posted(invoice) else None
            ),
            "notes": invoice.notes or "",
            "active": bool(invoice.active),
            "currency": invoice.currency.mnemonic if invoice.currency else None,
        }
        if entries is not None:
            result["entries"] = entries
        return result

    @staticmethod
    def _invoice_to_compact_line(invoice) -> str:
        """One-line compact: 'id  type  owner_id  date_opened  status'."""
        inv_type = "BILL" if invoice.owner_type == 4 else "INV"
        date_str = str(invoice.date_opened.date()) if invoice.date_opened else "n/a"
        status = "posted" if _is_invoice_posted(invoice) else "open"
        return f"{invoice.id}\t{inv_type}\t{date_str}\t{status}"

    @staticmethod
    def _entry_to_dict(entry_row, is_bill: bool = False) -> dict:
        """Convert an entry row (from raw SQL) to a serializable dict.

        Args:
            entry_row: SQLAlchemy row from entries table.
            is_bill: If True, read b_* columns; otherwise read i_* columns.
        """
        q_num = entry_row.quantity_num or 0
        q_denom = entry_row.quantity_denom or 1
        quantity = Decimal(q_num) / Decimal(q_denom)

        if is_bill:
            p_num = entry_row.b_price_num or 0
            p_denom = entry_row.b_price_denom or 1
            acct_guid = entry_row.b_acct
        else:
            p_num = entry_row.i_price_num or 0
            p_denom = entry_row.i_price_denom or 1
            acct_guid = entry_row.i_acct

        price = Decimal(p_num) / Decimal(p_denom)
        total = quantity * price

        # Raw SQL returns date as string; handle both str and datetime
        raw_date = entry_row.date
        if raw_date is None:
            date_str = None
        elif isinstance(raw_date, str):
            date_str = raw_date[:10]
        else:
            date_str = str(raw_date.date())

        return {
            "guid": entry_row.guid,
            "date": date_str,
            "description": entry_row.description or "",
            "account_guid": acct_guid or "",
            "quantity": str(quantity),
            "price": str(price),
            "total": str(total),
        }

    @staticmethod
    def _write_gncinvoice_slot(book, obj_guid: str, invoice_guid: str):
        """Write a gncInvoice FRAME+GUID slot linking an object to an invoice.

        GnuCash stores invoice linkage as a two-row structure:
          Row 1: obj_guid=<parent>, name='gncInvoice', slot_type=9 (FRAME),
                 guid_val=<frame_guid>
          Row 2: obj_guid=<frame_guid>, name='invoice', slot_type=5 (GUID),
                 guid_val=<invoice_guid>

        This is used on both posting transactions and lots to enable
        GnuCash UI navigation from transaction/lot back to the invoice.
        """
        import uuid
        from piecash.kvp import Slot, KVP_Type

        frame_guid = uuid.uuid4().hex
        book.session.execute(
            Slot.__table__.insert().values(
                obj_guid=obj_guid,
                name="gncInvoice",
                slot_type=KVP_Type.KVP_TYPE_FRAME,
                guid_val=frame_guid,
            )
        )
        _verify_composite_write(
            book.session, Slot.__table__,
            {"obj_guid": obj_guid, "name": "gncInvoice"},
            f"gncInvoice frame slot for {obj_guid[:8]}",
        )
        book.session.execute(
            Slot.__table__.insert().values(
                obj_guid=frame_guid,
                name="invoice",
                slot_type=KVP_Type.KVP_TYPE_GUID,
                guid_val=invoice_guid,
            )
        )
        _verify_composite_write(
            book.session, Slot.__table__,
            {"obj_guid": frame_guid, "name": "invoice"},
            f"gncInvoice ref slot for frame {frame_guid[:8]}",
        )

    @staticmethod
    def _write_gdate_slot(book, obj_guid: str, name: str, date_val: date):
        """Write a gdate-typed slot on an object.

        Used for trans-date-due and date-posted slots on invoice
        posting transactions.
        """
        from piecash.kvp import Slot, KVP_Type

        book.session.execute(
            Slot.__table__.insert().values(
                obj_guid=obj_guid,
                name=name,
                slot_type=KVP_Type.KVP_TYPE_GDATE,
                gdate_val=date_val,
            )
        )
        _verify_composite_write(
            book.session, Slot.__table__,
            {"obj_guid": obj_guid, "name": name},
            f"Gdate slot '{name}' for {obj_guid[:8]}",
        )

    @staticmethod
    def _calculate_lot_balance(lot) -> Decimal:
        """Sum of split values in a lot.

        For A/R lots: positive = outstanding receivable.
        For A/P lots: negative = outstanding payable.
        """
        total = Decimal(0)
        for split in lot.splits:
            total += Decimal(str(split.value))
        return total

    @staticmethod
    def _resolve_invoice_due_date(
        book, inv,
    ) -> tuple[date | None, bool]:
        """Resolve an invoice/bill due date through three sources.

        Returns ``(due_date, no_terms_flag)``. ``due_date`` is ``None``
        when the invoice isn't posted yet. ``no_terms_flag`` is True
        when the 30-day default was used (so callers can annotate the
        rendering as approximated).

        Resolution order — first source that resolves wins:

        1. ``trans-date-due`` slot on the posting transaction. Present
           only when the user explicitly passed ``due_date`` to
           ``post_invoice``.
        2. ``Invoice.terms`` reference. Present when the user posted
           with a billterm (e.g., "Net 30"). Read raw via SQL because
           ``inv.terms`` exposes a relationship that's reliable only
           through specific paths; raw SQL matches the pattern used
           elsewhere in this codebase for slot-style reads. The
           billterm's ``duedays`` is added to ``date_posted``.
        3. 30-day default. Anchors the days count to the assumption.
           Callers should annotate any "overdue" rendering as
           approximated when this branch fires.

        This helper exists so the warnings collector and
        ``get_outstanding_invoices`` produce identical due-date math.
        Without it the two were diverging — warnings used the full
        three-step chain, ``get_outstanding_invoices`` had nothing.
        """
        from sqlalchemy import text

        if not _is_invoice_posted(inv):
            return None, False

        txn = inv.post_txn
        if txn is None:
            return None, False

        # Step 1: explicit due-date slot.
        row = book.session.execute(
            text(
                "SELECT gdate_val FROM slots "
                "WHERE obj_guid = :guid "
                "AND name = 'trans-date-due'"
            ),
            {"guid": txn.guid},
        ).first()
        if row and row[0]:
            gdate_val = row[0]
            if isinstance(gdate_val, str):
                return date.fromisoformat(gdate_val[:10]), False
            if isinstance(gdate_val, datetime):
                return gdate_val.date(), False
            return gdate_val, False

        # Step 2: billterm via the raw ``terms`` column.
        try:
            terms_row = book.session.execute(
                text(
                    "SELECT terms FROM invoices "
                    "WHERE guid = :guid"
                ),
                {"guid": inv.guid},
            ).first()
            term_guid = terms_row[0] if terms_row else None
            if term_guid:
                from piecash.business.invoice import Billterm

                bt = (
                    book.session.query(Billterm)
                    .filter_by(guid=term_guid)
                    .first()
                )
                if bt and bt.duedays:
                    posted = inv.date_posted
                    if isinstance(posted, datetime):
                        posted = posted.date()
                    return (
                        posted + timedelta(days=int(bt.duedays)),
                        False,
                    )
        except Exception:
            pass

        # Step 3: 30-day default. Annotate.
        posted = inv.date_posted
        if isinstance(posted, datetime):
            posted = posted.date()
        return posted + timedelta(days=30), True

    def _get_invoice_entries_and_total(self, book, inv):
        """Query entries for an invoice/bill and compute total.

        Returns:
            Tuple of (entries_list, per_account_totals_dict, grand_total).
            per_account_totals maps account_guid -> Decimal total.
        """
        from sqlalchemy import text

        is_bill = inv.owner_type == 4
        if is_bill:
            rows = book.session.execute(
                text("SELECT * FROM entries WHERE bill = :guid"),
                {"guid": inv.guid},
            ).fetchall()
        else:
            rows = book.session.execute(
                text("SELECT * FROM entries WHERE invoice = :guid"),
                {"guid": inv.guid},
            ).fetchall()

        if not rows:
            raise ValueError(
                f"Cannot post: invoice {inv.id} has no entries"
            )

        acct_totals: dict[str, Decimal] = {}
        grand_total = Decimal(0)
        for row in rows:
            q_num = row.quantity_num or 0
            q_denom = row.quantity_denom or 1
            quantity = Decimal(q_num) / Decimal(q_denom)

            if is_bill:
                p_num = row.b_price_num or 0
                p_denom = row.b_price_denom or 1
                acct_guid = row.b_acct
            else:
                p_num = row.i_price_num or 0
                p_denom = row.i_price_denom or 1
                acct_guid = row.i_acct

            price = Decimal(p_num) / Decimal(p_denom)
            entry_total = quantity * price
            grand_total += entry_total
            acct_totals[acct_guid] = acct_totals.get(
                acct_guid, Decimal(0)
            ) + entry_total

        return rows, acct_totals, grand_total

    # ── Customer / Vendor / Billterm CRUD ─────────────────────────

    def _create_business_person(
        self,
        cls,
        *,
        book,
        name: str,
        currency: str | None = None,
        address: dict | None = None,
        **extra_kwargs,
    ) -> dict:
        """Shared create path for Customer / Vendor / Employee.

        Currency resolution (user-specified mnemonic or book default),
        optional Address construction from a dict, entity instantiation,
        ``book.save()``, and the canonical
        ``{guid, id, name, status}`` response — all in one place.

        Class-specific fields are passed via ``**extra_kwargs`` so the
        helper stays agnostic of what each subclass accepts. Customer
        and Vendor take ``notes=""``; Employee has no ``notes`` column
        and rejects the kwarg — see docs/PIECASH_REFERENCE.md for the
        full shape divergence. Callers build their own kwargs dict and
        the helper passes it through unexamined.

        Args:
            cls: ``Customer``, ``Vendor``, or ``Employee`` (piecash).
            book: An open piecash book session (readonly=False).
            name: Entity display name.
            currency: ISO currency code. Defaults to book's default.
            address: Optional dict with keys: name, addr1, addr2,
                addr3, addr4, phone, fax, email. Empty / missing
                fields render as empty strings in the Address record.
            **extra_kwargs: Class-specific fields passed through to
                ``cls(...)``. Use for ``notes`` (Customer/Vendor),
                ``acl`` / ``language`` / ``workday`` / ``rate``
                (Employee), etc.

        Returns:
            ``{"guid": ..., "id": ..., "name": ..., "status": "created"}``
        """
        from piecash.business.person import Address

        if currency:
            currency_obj = None
            for c in book.currencies:
                if c.mnemonic == currency:
                    currency_obj = c
                    break
            if not currency_obj:
                raise ValueError(f"Currency not found: {currency}")
        else:
            currency_obj = self._require_default_currency(book)

        addr = None
        if address:
            addr = Address(
                name=address.get("name", name),
                addr1=address.get("addr1", ""),
                addr2=address.get("addr2", ""),
                addr3=address.get("addr3", ""),
                addr4=address.get("addr4", ""),
                phone=address.get("phone", ""),
                fax=address.get("fax", ""),
                email=address.get("email", ""),
            )

        entity = cls(
            name=name,
            currency=currency_obj,
            address=addr,
            book=book,
            **extra_kwargs,
        )
        book.save()

        return {
            "guid": entity.guid,
            "id": entity.id,
            "name": entity.name,
            "currency": currency_obj.mnemonic,
            "status": "created",
        }

    def create_customer(
        self,
        name: str,
        currency: str | None = None,
        notes: str = "",
        address: dict | None = None,
    ) -> dict:
        """Create a new customer.

        Args:
            name: Customer name.
            currency: ISO currency code. Defaults to book's default currency.
            notes: Optional notes.
            address: Optional address dict with keys: name, addr1, addr2,
                     addr3, addr4, phone, fax, email.

        Returns:
            Dict with guid, id, name, status.
        """
        from piecash.business.person import Customer

        with self.open(readonly=False) as book:
            return self._create_business_person(
                Customer, book=book, name=name,
                currency=currency, address=address, notes=notes,
            )

    def list_customers(
        self,
        active_only: bool = True,
        compact: bool = True,
    ) -> list[dict] | str:
        """List all customers.

        Args:
            active_only: If True, only return active customers.
            compact: If True, return compact one-line-per-customer string.

        Returns:
            Compact string or list of dicts.
        """
        with self.open() as book:
            customers = sorted(book.customers, key=lambda c: c.name)
            if active_only:
                customers = [c for c in customers if c.active]

            if compact:
                lines = [self._customer_to_compact_line(c) for c in customers]
                return "\n".join(lines)
            else:
                return [self._customer_to_dict(c) for c in customers]

    def get_customer(self, customer_id: str) -> dict:
        """Get customer details by ID.

        Args:
            customer_id: Human-readable customer ID (e.g., '000001').

        Returns:
            Dict with full customer details.

        Raises:
            ValueError: If customer not found.
        """
        with self.open() as book:
            customer = self._find_customer(book, customer_id)
            if not customer:
                raise ValueError(f"Customer not found: {customer_id}")
            return self._customer_to_dict(customer)

    def create_vendor(
        self,
        name: str,
        currency: str | None = None,
        notes: str = "",
        address: dict | None = None,
    ) -> dict:
        """Create a new vendor.

        Args:
            name: Vendor name.
            currency: ISO currency code. Defaults to book's default currency.
            notes: Optional notes.
            address: Optional address dict with keys: name, addr1, addr2,
                     addr3, addr4, phone, fax, email.

        Returns:
            Dict with guid, id, name, status.
        """
        from piecash.business.person import Vendor

        with self.open(readonly=False) as book:
            return self._create_business_person(
                Vendor, book=book, name=name,
                currency=currency, address=address, notes=notes,
            )

    def list_vendors(
        self,
        active_only: bool = True,
        compact: bool = True,
    ) -> list[dict] | str:
        """List all vendors.

        Args:
            active_only: If True, only return active vendors.
            compact: If True, return compact one-line-per-vendor string.

        Returns:
            Compact string or list of dicts.
        """
        with self.open() as book:
            vendors = sorted(book.vendors, key=lambda v: v.name)
            if active_only:
                vendors = [v for v in vendors if v.active]

            if compact:
                lines = [self._vendor_to_compact_line(v) for v in vendors]
                return "\n".join(lines)
            else:
                return [self._vendor_to_dict(v) for v in vendors]

    def get_vendor(self, vendor_id: str) -> dict:
        """Get vendor details by ID.

        Args:
            vendor_id: Human-readable vendor ID (e.g., '000001').

        Returns:
            Dict with full vendor details.

        Raises:
            ValueError: If vendor not found.
        """
        with self.open() as book:
            vendor = self._find_vendor(book, vendor_id)
            if not vendor:
                raise ValueError(f"Vendor not found: {vendor_id}")
            return self._vendor_to_dict(vendor)

    def create_employee(
        self,
        name: str,
        currency: str | None = None,
        address: dict | None = None,
    ) -> dict:
        """Create a new employee.

        Employee has no ``notes`` field (unlike Customer and Vendor).
        Employee-specific fields (``acl`` / ``language`` / ``workday``
        / ``rate``) are out of scope for the 1.3.0 release. See
        docs/PIECASH_REFERENCE.md for the full schema shape.

        Args:
            name: Employee name.
            currency: ISO currency code. Defaults to book's default currency.
            address: Optional address dict with keys: name, addr1, addr2,
                     addr3, addr4, phone, fax, email.

        Returns:
            Dict with guid, id, name, status.
        """
        from piecash.business.person import Employee

        with self.open(readonly=False) as book:
            return self._create_business_person(
                Employee, book=book, name=name,
                currency=currency, address=address,
            )

    def list_employees(
        self,
        active_only: bool = True,
        compact: bool = True,
    ) -> list[dict] | str:
        """List all employees.

        Args:
            active_only: If True, only return active employees.
            compact: If True, return compact one-line-per-employee string.

        Returns:
            Compact string or list of dicts.
        """
        with self.open() as book:
            employees = sorted(book.employees, key=lambda e: e.name)
            if active_only:
                employees = [e for e in employees if e.active]

            if compact:
                lines = [self._employee_to_compact_line(e) for e in employees]
                return "\n".join(lines)
            else:
                return [self._employee_to_dict(e) for e in employees]

    def get_employee(self, employee_id: str) -> dict:
        """Get employee details by ID.

        Args:
            employee_id: Human-readable employee ID (e.g., '000001').

        Returns:
            Dict with full employee details.

        Raises:
            ValueError: If employee not found.
        """
        with self.open() as book:
            employee = self._find_employee(book, employee_id)
            if not employee:
                raise ValueError(f"Employee not found: {employee_id}")
            return self._employee_to_dict(employee)

    def create_billterm(
        self,
        name: str,
        due_days: int = 30,
        description: str = "",
        discount_days: int = 0,
        discount_percent: str = "0",
    ) -> dict:
        """Create a new billing term.

        Args:
            name: Billterm name (e.g., 'Net 30').
            due_days: Number of days until payment is due.
            description: Optional description.
            discount_days: Days within which early discount applies.
            discount_percent: Early payment discount percentage.

        Returns:
            Dict with guid, name, due_days, status.
        """
        import uuid
        from piecash.business.invoice import Billterm

        discount = _to_decimal(discount_percent)
        disc_str = str(discount)
        if "." in disc_str:
            decimals = len(disc_str.split(".")[1])
            disc_denom = 10 ** decimals
            disc_num = int(discount * disc_denom)
        else:
            disc_num = int(discount)
            disc_denom = 1

        with self.open(readonly=False) as book:
            bt_guid = uuid.uuid4().hex

            book.session.execute(
                Billterm.__table__.insert().values(
                    guid=bt_guid,
                    name=name,
                    description=description,
                    refcount=0,
                    invisible=0,
                    type="GNC_TERM_TYPE_DAYS",
                    duedays=due_days,
                    discountdays=discount_days,
                    discount_num=disc_num,
                    discount_denom=disc_denom,
                    cutoff=0,
                )
            )
            _verify_write(
                book.session, Billterm.__table__, bt_guid,
                f"Billterm '{name}'",
            )

            book.save()

            return {
                "guid": bt_guid,
                "name": name,
                "due_days": due_days,
                "status": "created",
            }

    def list_billterms(self, compact: bool = True) -> list[dict] | str:
        """List all billing terms.

        Args:
            compact: If True, return compact one-line-per-term string.

        Returns:
            Compact string or list of dicts.
        """
        from piecash.business.invoice import Billterm

        with self.open() as book:
            terms = book.session.query(Billterm).filter(
                Billterm.invisible == 0
            ).order_by(Billterm.name).all()

            if compact:
                lines = []
                for t in terms:
                    lines.append(f"{t.name}\t{t.duedays} days")
                return "\n".join(lines)
            else:
                return [self._billterm_to_dict(t) for t in terms]

    # ── Invoice / Bill creation, posting, payment ─────────────────
    #
    # Customer invoices and vendor bills share the ``invoices`` table,
    # differing only in ``owner_type`` (2 vs 4) and a matched set of
    # label / counter / finder conventions. The config table below
    # captures those conventions; ``_create_business_document`` consumes
    # it and services both create paths from a single implementation.

    _BUSINESS_DOC_CONFIG: dict[int, dict] = {
        2: {  # customer invoice
            "owner_label": "Customer",
            "doc_label": "Invoice",
            "owner_id_key": "customer_id",
            "doc_id_param": "invoice_id",
            "counter_attr": "counter_invoice",
            "find_owner_method": "_find_customer",
        },
        4: {  # vendor bill
            "owner_label": "Vendor",
            "doc_label": "Bill",
            "owner_id_key": "vendor_id",
            "doc_id_param": "bill_id",
            "counter_attr": "counter_bill",
            "find_owner_method": "_find_vendor",
        },
    }

    def _create_business_document(
        self,
        *,
        owner_type: int,
        owner_id: str,
        date_opened: str | None = None,
        notes: str = "",
        currency: str | None = None,
        term: str | None = None,
        doc_id: str | None = None,
    ) -> dict:
        """Shared create path for customer invoice and vendor bill.

        Both hit the ``invoices`` table with the same 18-column insert,
        differing only in ``owner_type`` and the label / counter /
        finder conventions captured in ``_BUSINESS_DOC_CONFIG``. The
        helper opens the book, resolves owner + currency + billterm,
        handles the custom-ID-vs-auto-counter branch, writes the row
        with ``_verify_write``, and returns the canonical response.

        Args:
            owner_type: 2 = customer invoice, 4 = vendor bill.
            owner_id: Customer ID or vendor ID (human-readable '000001').
            date_opened: ISO date; defaults to now.
            notes: Free-text notes.
            currency: ISO currency code; defaults to book default.
            term: Billterm name; optional.
            doc_id: Custom invoice/bill number; auto-generated from the
                relevant counter when omitted.

        Returns:
            ``{guid, id, <owner_id_key>, date_opened, status}``
        """
        import uuid
        from piecash.business.invoice import Billterm, Invoice

        if owner_type not in self._BUSINESS_DOC_CONFIG:
            raise ValueError(f"Unknown owner_type: {owner_type}")
        config = self._BUSINESS_DOC_CONFIG[owner_type]
        find_owner = getattr(self, config["find_owner_method"])

        open_date = (
            datetime.strptime(date_opened, "%Y-%m-%d")
            if date_opened
            else datetime.now()
        )

        with self.open(readonly=False) as book:
            owner = find_owner(book, owner_id)
            if not owner:
                raise ValueError(
                    f"{config['owner_label']} not found: {owner_id}"
                )

            if currency:
                currency_obj = None
                for c in book.currencies:
                    if c.mnemonic == currency:
                        currency_obj = c
                        break
                if not currency_obj:
                    raise ValueError(f"Currency not found: {currency}")
                currency_guid = currency_obj.guid
            else:
                currency_guid = self._require_default_currency(book).guid

            term_guid = None
            if term:
                bt = book.session.query(Billterm).filter(
                    Billterm.name == term, Billterm.invisible == 0
                ).first()
                if not bt:
                    raise ValueError(f"Billterm not found: {term}")
                term_guid = bt.guid

            if doc_id is not None:
                if not doc_id.strip():
                    raise ValueError(
                        f"{config['doc_id_param']} must not be blank"
                    )
                existing = self._find_invoice(
                    book, doc_id, owner_type=owner_type
                )
                if existing:
                    raise ValueError(
                        f"{config['doc_label']} with ID "
                        f"'{doc_id}' already exists"
                    )
            else:
                # Auto-generate the next document ID. The book
                # counter (``counter_invoice`` / ``counter_bill``)
                # SHOULD be the canonical source, but it can drift
                # below the actual MAX(id) — e.g. when historical
                # documents are imported via raw SQL without
                # bumping the counter, or when the file was edited
                # outside the MCP server's lifecycle. The
                # bookkeeper hit this on Alex's synthetic book:
                # 2025 bills sat at IDs 000006 / 000007 but the
                # counter was lower, so a new 2026 ``create_bill``
                # auto-assigned 000006 — colliding with the
                # 2025 row and breaking every subsequent
                # ``post_invoice`` / ``get_outstanding_invoices``
                # lookup that resolved to the wrong record.
                #
                # Fix: take the max of (book counter, actual max
                # numeric ID in the table for this owner_type) and
                # use that + 1. Re-sync the book counter so the
                # next auto-id picks up where this one left off.
                # Non-numeric existing IDs (custom strings the
                # user supplied) are skipped — they're irrelevant
                # to numeric auto-numbering.
                book_counter = getattr(book, config["counter_attr"])
                existing_ids = book.session.query(Invoice.id).filter(
                    Invoice.owner_type == owner_type
                ).all()
                max_numeric = 0
                for (existing_id,) in existing_ids:
                    try:
                        max_numeric = max(max_numeric, int(existing_id))
                    except (ValueError, TypeError):
                        continue
                cnt = max(book_counter, max_numeric) + 1
                setattr(book, config["counter_attr"], cnt)
                doc_id = f"{cnt:06d}"

            inv_guid = uuid.uuid4().hex
            book.session.execute(
                Invoice.__table__.insert().values(
                    guid=inv_guid,
                    id=doc_id,
                    date_opened=open_date,
                    date_posted=None,
                    notes=notes,
                    active=1,
                    currency=currency_guid,
                    owner_type=owner_type,
                    owner_guid=owner.guid,
                    terms=term_guid,
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
            _verify_write(
                book.session, Invoice.__table__, inv_guid,
                f"{config['doc_label']} '{doc_id}'",
            )

            book.save()

            return {
                "guid": inv_guid,
                "id": doc_id,
                config["owner_id_key"]: owner_id,
                "date_opened": str(open_date.date()),
                "status": "created",
            }

    def create_invoice(
        self,
        customer_id: str,
        date_opened: str | None = None,
        notes: str = "",
        currency: str | None = None,
        term: str | None = None,
        invoice_id: str | None = None,
    ) -> dict:
        """Create a customer invoice.

        Args:
            customer_id: Customer ID (e.g., '000001').
            date_opened: Date in ISO format. Defaults to today.
            notes: Optional notes.
            currency: ISO currency code. Defaults to book's default currency.
            term: Billterm name (e.g., 'Net 30'). Optional.
            invoice_id: Custom invoice number. If omitted, auto-generates
                from the book's invoice counter.

        Returns:
            Dict with guid, id, customer_id, status.
        """
        return self._create_business_document(
            owner_type=2,
            owner_id=customer_id,
            date_opened=date_opened,
            notes=notes,
            currency=currency,
            term=term,
            doc_id=invoice_id,
        )

    def create_bill(
        self,
        vendor_id: str,
        date_opened: str | None = None,
        notes: str = "",
        currency: str | None = None,
        term: str | None = None,
        bill_id: str | None = None,
    ) -> dict:
        """Create a vendor bill.

        Args:
            vendor_id: Vendor ID (e.g., '000001').
            date_opened: Date in ISO format. Defaults to today.
            notes: Optional notes.
            currency: ISO currency code. Defaults to book's default currency.
            term: Billterm name (e.g., 'Net 30'). Optional.
            bill_id: Custom bill number. If omitted, auto-generates
                from the book's bill counter.

        Returns:
            Dict with guid, id, vendor_id, status.
        """
        return self._create_business_document(
            owner_type=4,
            owner_id=vendor_id,
            date_opened=date_opened,
            notes=notes,
            currency=currency,
            term=term,
            doc_id=bill_id,
        )

    def add_invoice_entry(
        self,
        invoice_id: str,
        account: str,
        description: str,
        quantity: str,
        price: str,
    ) -> dict:
        """Add a line item entry to a customer invoice.

        Args:
            invoice_id: Invoice ID (e.g., '000001').
            account: Income account full path (e.g., 'Income:Sales').
            description: Line item description.
            quantity: Quantity as string (e.g., '1', '2.5').
            price: Unit price as string (e.g., '100.00').

        Returns:
            Dict with guid, invoice_id, total, status.
        """
        import uuid
        from piecash.business.invoice import Invoice, Entry

        qty = _to_decimal(quantity)
        unit_price = _to_decimal(price)

        with self.open(readonly=False) as book:
            inv = self._find_invoice(book, invoice_id, owner_type=2)
            if not inv:
                raise ValueError(f"Invoice not found: {invoice_id}")
            if inv.owner_type != 2:
                raise ValueError(
                    f"'{invoice_id}' is a vendor bill, not a customer invoice. "
                    f"Use add_bill_entry instead."
                )
            if _is_invoice_posted(inv):
                raise ValueError(
                    f"Invoice '{invoice_id}' is already posted. "
                    f"Cannot add entries to posted invoices."
                )

            acct = self._resolve_account(book, account)
            if not acct:
                raise ValueError(f"Account not found: {account}")

            q_num, q_denom = self._decimal_to_num_denom(qty)
            p_num, p_denom = self._decimal_to_num_denom(unit_price)
            entry_guid = uuid.uuid4().hex

            book.session.execute(
                Entry.__table__.insert().values(
                    guid=entry_guid,
                    date=datetime.now(),
                    date_entered=datetime.now(),
                    description=description,
                    action="",
                    notes="",
                    quantity_num=q_num,
                    quantity_denom=q_denom,
                    i_acct=acct.guid,
                    i_price_num=p_num,
                    i_price_denom=p_denom,
                    i_discount_num=0,
                    i_discount_denom=1,
                    invoice=inv.guid,
                    i_disc_type="",
                    i_disc_how="",
                    i_taxable=0,
                    i_taxincluded=0,
                    i_taxtable=None,
                    b_acct=None,
                    b_price_num=0,
                    b_price_denom=1,
                    bill=None,
                    b_taxable=0,
                    b_taxincluded=0,
                    b_taxtable=None,
                    b_paytype=0,
                    billable=0,
                    billto_type=0,
                    billto_guid=None,
                    order_guid=None,
                )
            )
            _verify_write(
                book.session, Entry.__table__, entry_guid,
                f"Invoice entry '{description}' on invoice {invoice_id}",
            )

            book.save()

            total = qty * unit_price
            return {
                "guid": entry_guid,
                "invoice_id": invoice_id,
                "description": description,
                "quantity": str(qty),
                "price": str(unit_price),
                "total": str(total),
                "status": "created",
            }

    def add_bill_entry(
        self,
        bill_id: str,
        account: str,
        description: str,
        quantity: str,
        price: str,
    ) -> dict:
        """Add a line item entry to a vendor bill.

        Args:
            bill_id: Bill ID (e.g., '000001').
            account: Expense account full path (e.g., 'Expenses:Office Supplies').
            description: Line item description.
            quantity: Quantity as string (e.g., '1', '2.5').
            price: Unit price as string (e.g., '50.00').

        Returns:
            Dict with guid, bill_id, total, status.
        """
        import uuid
        from piecash.business.invoice import Invoice, Entry

        qty = _to_decimal(quantity)
        unit_price = _to_decimal(price)

        with self.open(readonly=False) as book:
            inv = self._find_invoice(book, bill_id, owner_type=4)
            if not inv:
                raise ValueError(f"Bill not found: {bill_id}")
            if inv.owner_type != 4:
                raise ValueError(
                    f"'{bill_id}' is a customer invoice, not a vendor bill. "
                    f"Use add_invoice_entry instead."
                )
            if _is_invoice_posted(inv):
                raise ValueError(
                    f"Bill '{bill_id}' is already posted. "
                    f"Cannot add entries to posted bills."
                )

            acct = self._resolve_account(book, account)
            if not acct:
                raise ValueError(f"Account not found: {account}")

            q_num, q_denom = self._decimal_to_num_denom(qty)
            p_num, p_denom = self._decimal_to_num_denom(unit_price)
            entry_guid = uuid.uuid4().hex

            book.session.execute(
                Entry.__table__.insert().values(
                    guid=entry_guid,
                    date=datetime.now(),
                    date_entered=datetime.now(),
                    description=description,
                    action="",
                    notes="",
                    quantity_num=q_num,
                    quantity_denom=q_denom,
                    i_acct=None,
                    i_price_num=0,
                    i_price_denom=1,
                    i_discount_num=0,
                    i_discount_denom=1,
                    invoice=None,
                    i_disc_type="",
                    i_disc_how="",
                    i_taxable=0,
                    i_taxincluded=0,
                    i_taxtable=None,
                    b_acct=acct.guid,
                    b_price_num=p_num,
                    b_price_denom=p_denom,
                    bill=inv.guid,
                    b_taxable=0,
                    b_taxincluded=0,
                    b_taxtable=None,
                    b_paytype=0,
                    billable=0,
                    billto_type=0,
                    billto_guid=None,
                    order_guid=None,
                )
            )
            _verify_write(
                book.session, Entry.__table__, entry_guid,
                f"Bill entry '{description}' on bill {bill_id}",
            )

            book.save()

            total = qty * unit_price
            return {
                "guid": entry_guid,
                "bill_id": bill_id,
                "description": description,
                "quantity": str(qty),
                "price": str(unit_price),
                "total": str(total),
                "status": "created",
            }

    def list_invoices(
        self,
        owner_type: str | None = None,
        status: str | None = None,
        compact: bool = True,
        limit: int | None = None,
    ) -> list[dict] | str:
        """List invoices and/or bills.

        Args:
            owner_type: Filter by type: 'customer', 'vendor', or None for all.
            status: Filter by status: 'posted', 'open', or None for all.
            compact: If True, return compact one-line-per-invoice string.
            limit: Maximum invoices to return. Defaults to 50, capped at
                   250 server-side. Pre-fix this method dumped every
                   invoice in the book regardless of caller intent.

        Returns:
            Compact string (with optional truncation notice) or list of
            dicts (truncated silently — caller has ``len()``).
        """
        from piecash.business.invoice import Invoice

        with self.open() as book:
            query = book.session.query(Invoice)

            if owner_type == "customer":
                query = query.filter(Invoice.owner_type == 2)
            elif owner_type == "vendor":
                query = query.filter(Invoice.owner_type == 4)

            invoices = query.order_by(Invoice.date_opened.desc()).all()

            if status == "posted":
                invoices = [i for i in invoices if _is_invoice_posted(i)]
            elif status == "open":
                invoices = [i for i in invoices if not _is_invoice_posted(i)]

            invoices, notice = _apply_limit(
                invoices,
                limit=limit,
                entity_name="invoices",
                suggest_narrow=True,
            )

            if compact:
                lines = [self._invoice_to_compact_line(i) for i in invoices]
                if notice:
                    lines.append(notice)
                return "\n".join(lines)
            else:
                return [self._invoice_to_dict(i) for i in invoices]

    def get_invoice(self, invoice_id: str, owner_type: str | None = None) -> dict:
        """Get full details for an invoice or bill, including entries.

        Args:
            invoice_id: Human-readable invoice/bill ID (e.g., '000001').
            owner_type: Filter by type: 'customer' or 'vendor'.
                        Useful when invoice and bill share the same ID.

        Returns:
            Dict with full invoice details and entry list.

        Raises:
            ValueError: If invoice not found.
        """
        from piecash.business.invoice import Invoice, Entry
        from sqlalchemy import text

        ot = None
        if owner_type == "customer":
            ot = 2
        elif owner_type == "vendor":
            ot = 4

        with self.open() as book:
            inv = self._find_invoice(book, invoice_id, owner_type=ot)
            if not inv:
                raise ValueError(f"Invoice/bill not found: {invoice_id}")

            is_bill = inv.owner_type == 4

            # Get entries via raw SQL since ORM relationship doesn't
            # work for vendor bills (bill column is VARCHAR, not FK)
            if is_bill:
                rows = book.session.execute(
                    text("SELECT * FROM entries WHERE bill = :guid"),
                    {"guid": inv.guid},
                ).fetchall()
            else:
                rows = book.session.execute(
                    text("SELECT * FROM entries WHERE invoice = :guid"),
                    {"guid": inv.guid},
                ).fetchall()

            entries = [self._entry_to_dict(r, is_bill=is_bill) for r in rows]

            total = sum(
                Decimal(e["quantity"]) * Decimal(e["price"])
                for e in entries
            )

            owner_name = None
            if is_bill:
                vendor = self._find_vendor_by_guid(book, inv.owner_guid)
                if vendor:
                    owner_name = vendor.name
            else:
                customer = self._find_customer_by_guid(book, inv.owner_guid)
                if customer:
                    owner_name = customer.name

            result = self._invoice_to_dict(inv, entries=entries)
            result["total"] = str(total)
            if owner_name:
                result["owner_name"] = owner_name
            return result

    def post_invoice(
        self,
        invoice_id: str,
        post_account: str,
        post_date: str | None = None,
        due_date: str | None = None,
        description: str | None = None,
        owner_type: str | None = None,
    ) -> dict:
        """Post a customer invoice or vendor bill.

        Posting creates a transaction in the A/R or A/P account, creates
        a lot for payment tracking, and marks the invoice as posted.

        Args:
            invoice_id: Human-readable ID (e.g., '000001').
            post_account: A/R or A/P account path.
            post_date: ISO date (YYYY-MM-DD). Defaults to today.
            due_date: Payment due date (YYYY-MM-DD). Optional.
            description: Description for the posting transaction.
            owner_type: 'customer' or 'vendor' for disambiguation.

        Returns:
            Dict with invoice details, total, transaction_guid, lot_guid.

        Raises:
            ValueError: If invoice not found, already posted, no entries,
                        or invalid account type.
        """
        from piecash.business.invoice import Invoice
        from piecash.core.transaction import Lot

        ot = None
        if owner_type == "customer":
            ot = 2
        elif owner_type == "vendor":
            ot = 4

        parsed_date = (
            date.fromisoformat(post_date) if post_date
            else date.today()
        )

        with self.open(readonly=False) as book:
            # GnuCash uses separate ID sequences for customer
            # invoices (owner_type=2) and vendor bills
            # (owner_type=4) but stores both in the ``invoices``
            # table. IDs collide across sequences (a $5K Emerald
            # Analytics invoice and a $250 Office Depot bill can
            # both be id=000010). Without an owner_type filter,
            # ``_find_invoice`` returns whichever row hits first
            # — the bookkeeper hit this on Alex's book trying to
            # post a vendor bill 000010 and getting back an
            # already-posted customer invoice 000010, raising
            # spurious "already posted".
            #
            # When the caller didn't specify ``owner_type``,
            # disambiguate by reading the post_account type:
            # a RECEIVABLE account can only receive customer
            # invoices, a PAYABLE only vendor bills. The
            # post_account validation later in this method already
            # uses the same predicate; using it upstream for the
            # lookup eliminates the collision class entirely.
            if ot is None:
                pa = self._resolve_account(book, post_account)
                if pa is not None:
                    if pa.type == "RECEIVABLE":
                        ot = 2
                    elif pa.type == "PAYABLE":
                        ot = 4

            inv = self._find_invoice(book, invoice_id, owner_type=ot)
            if not inv:
                raise ValueError(
                    f"Invoice/bill not found: {invoice_id}"
                )

            # Truthy check rather than ``is not None``: piecash's
            # _DateTime TypeDecorator can return falsy non-None
            # values (empty string in some persistence paths) for
            # never-posted documents. The bookkeeper hit this on
            # Alex's book where freshly auto-id'd bill 000008 had
            # ``date_posted=""`` in SQL and post_invoice raised
            # "already posted" on it. ``if inv.date_posted`` treats
            # both None and "" as "not posted"; only a real
            # datetime is truthy. Same fix applied to all other
            # date_posted checks in this module — see also
            # ``add_invoice_entry``, ``add_bill_entry``,
            # ``pay_invoice``, ``list_invoices`` filter,
            # ``_delete_invoice_or_bill``, and the dependency
            # check in ``_invoice_dependency_check``.
            if _is_invoice_posted(inv):
                raise ValueError(
                    f"Invoice {invoice_id} is already posted"
                )

            is_bill = inv.owner_type == 4

            post_acct = self._resolve_account(book, post_account)
            if not post_acct:
                raise ValueError(
                    f"Account not found: {post_account}"
                )
            expected_type = "PAYABLE" if is_bill else "RECEIVABLE"
            if post_acct.type != expected_type:
                raise ValueError(
                    f"Post account must be {expected_type}, "
                    f"got {post_acct.type}"
                )

            _, acct_totals, grand_total = (
                self._get_invoice_entries_and_total(book, inv)
            )

            lot = Lot(
                title=f"Invoice {inv.id}",
                account=post_acct,
                is_closed=0,
            )
            book.session.add(lot)

            # GnuCash UI uses the customer/vendor name, not "Invoice NNNNNN"
            if is_bill:
                owner = self._find_vendor_by_guid(book, inv.owner_guid)
            else:
                owner = self._find_customer_by_guid(
                    book, inv.owner_guid
                )
            owner_name = owner.name if owner else f"Invoice {inv.id}"
            txn_desc = description or owner_name

            parsed_due = (
                date.fromisoformat(due_date) if due_date else None
            )

            # Helper: convert a value in invoice currency to the
            # equivalent quantity in the given account's commodity,
            # using book.prices at parsed_date. Returns the value
            # unchanged when currencies match.
            def _qty_for_split(acct, value_in_invoice_ccy):
                if acct.commodity == inv.currency:
                    return value_in_invoice_ccy
                rate = self._find_exchange_rate(
                    book,
                    from_commodity=inv.currency,
                    to_commodity=acct.commodity,
                    as_of=parsed_date,
                )
                if rate is None:
                    raise ValueError(
                        f"Cross-currency posting requires an exchange "
                        f"rate: invoice currency "
                        f"{inv.currency.mnemonic} differs from "
                        f"account commodity {acct.commodity.mnemonic} "
                        f"({acct.fullname}), and no matching price was "
                        f"found for "
                        f"{inv.currency.mnemonic}/"
                        f"{acct.commodity.mnemonic} on or near "
                        f"{parsed_date}. Add a price with "
                        f"create_price, then retry."
                    )
                return (value_in_invoice_ccy * rate).quantize(
                    Decimal("0.01")
                )

            # Build transaction splits
            # For customer invoice: A/R debit (positive), income credit (negative)
            # For vendor bill: A/P credit (negative), expense debit (positive)
            piecash_splits = []

            if is_bill:
                ar_ap_value = -grand_total
            else:
                ar_ap_value = grand_total
            ar_ap_split = piecash.Split(
                account=post_acct,
                value=ar_ap_value,
                quantity=_qty_for_split(post_acct, ar_ap_value),
                memo="",
                action="Invoice",
                reconcile_date=datetime(1970, 1, 1),
            )
            piecash_splits.append(ar_ap_split)

            for acct_guid, acct_total in acct_totals.items():
                entry_acct = None
                for a in book.accounts:
                    if a.guid == acct_guid:
                        entry_acct = a
                        break
                if not entry_acct:
                    raise ValueError(
                        f"Entry account not found: {acct_guid}"
                    )

                if is_bill:
                    split_value = acct_total
                else:
                    split_value = -acct_total

                piecash_splits.append(
                    piecash.Split(
                        account=entry_acct,
                        value=split_value,
                        quantity=_qty_for_split(entry_acct, split_value),
                        memo="",
                    )
                )

            # num = invoice ID, matching GnuCash UI behavior
            txn = piecash.Transaction(
                currency=inv.currency,
                description=txn_desc,
                post_date=parsed_date,
                num=inv.id,
                splits=piecash_splits,
            )

            ar_ap_split.lot = lot

            inv.date_posted = datetime.combine(
                parsed_date, datetime.min.time()
            )
            inv.post_txn = txn
            inv.post_lot = lot
            inv.post_account = post_acct

            book.flush()

            # Metadata slots matching GnuCash UI behavior
            txn["trans-txn-type"] = "I"
            txn["trans-read-only"] = (
                "Generated from an invoice. "
                "Try unposting the invoice."
            )
            self._write_gncinvoice_slot(
                book, txn.guid, inv.guid
            )
            self._write_gncinvoice_slot(
                book, lot.guid, inv.guid
            )
            if parsed_due:
                self._write_gdate_slot(
                    book, txn.guid, "trans-date-due", parsed_due
                )

            book.save()

            result = {
                "id": inv.id,
                "type": "bill" if is_bill else "invoice",
                "status": "posted",
                "total": str(grand_total),
                "post_date": str(parsed_date),
                "transaction_guid": txn.guid,
                "lot_guid": lot.guid,
                "post_account": post_acct.fullname,
            }

        return result

    def pay_invoice(
        self,
        invoice_id: str,
        payment_account: str,
        amount: str,
        payment_date: str | None = None,
        description: str | None = None,
        owner_type: str | None = None,
    ) -> dict:
        """Record a payment against a posted invoice or bill.

        Creates a payment transaction and assigns the A/R or A/P split
        to the invoice's lot for balance tracking. Partial payments
        are supported.

        ``amount`` is always in the **invoice's currency**. For same-
        currency payments (invoice currency == payment account commodity)
        the payment account is credited/debited with the same amount. For
        cross-currency payments the payment account's quantity is
        computed from the book's price table at ``payment_date`` (most
        recent price on or before the date, falling back to closest
        after). A clear error is raised if no matching price exists.

        Args:
            invoice_id: Human-readable ID (e.g., '000001').
            payment_account: Bank or cash account path.
            amount: Payment amount in the invoice currency (e.g., '500.00').
            payment_date: ISO date (YYYY-MM-DD). Defaults to today.
            description: Description for the payment transaction.
            owner_type: 'customer' or 'vendor' for disambiguation.

        Returns:
            Dict with payment details and remaining balance. For cross-
            currency payments also includes ``exchange_rate`` and
            ``payment_account_amount``.

        Raises:
            ValueError: If invoice not found, not posted, invalid account,
                or cross-currency payment with no exchange rate available.
        """
        ot = None
        if owner_type == "customer":
            ot = 2
        elif owner_type == "vendor":
            ot = 4

        payment_amount = _to_decimal(amount)
        if payment_amount <= 0:
            raise ValueError("Payment amount must be positive")

        parsed_date = (
            date.fromisoformat(payment_date) if payment_date
            else date.today()
        )

        with self.open(readonly=False) as book:
            inv = self._find_invoice(book, invoice_id, owner_type=ot)
            if not inv:
                raise ValueError(
                    f"Invoice/bill not found: {invoice_id}"
                )

            if not _is_invoice_posted(inv):
                raise ValueError(
                    f"Invoice {invoice_id} is not posted — "
                    f"post it before recording payment"
                )

            is_bill = inv.owner_type == 4

            pay_acct = self._resolve_account(book, payment_account)
            if not pay_acct:
                raise ValueError(
                    f"Account not found: {payment_account}"
                )

            post_acct = None
            post_acc_guid = inv.post_acc_guid
            for a in book.accounts:
                if a.guid == post_acc_guid:
                    post_acct = a
                    break
            if not post_acct:
                raise ValueError(
                    f"Post account not found for invoice {invoice_id}"
                )

            lot_guid = inv.post_lot_guid
            lot_obj = None
            for lot in post_acct.lots:
                if lot.guid == lot_guid:
                    lot_obj = lot
                    break
            if not lot_obj:
                raise ValueError(
                    f"Lot not found for invoice {invoice_id}"
                )

            if is_bill:
                owner = self._find_vendor_by_guid(
                    book, inv.owner_guid
                )
            else:
                owner = self._find_customer_by_guid(
                    book, inv.owner_guid
                )
            owner_name = owner.name if owner else ""
            txn_desc = description or owner_name

            # Cross-currency payment: if the payment account's commodity
            # differs from the invoice currency, find the exchange rate
            # from book.prices and compute the payment account's quantity.
            # The transaction currency stays as the invoice currency, so
            # all split ``value``s remain in invoice currency (the
            # transaction balances in EUR for an EUR invoice); the pay
            # account's ``quantity`` reflects the actual USD/GBP/whatever
            # amount deposited or withdrawn.
            exchange_rate = None
            pay_quantity = payment_amount
            if pay_acct.commodity != inv.currency:
                exchange_rate = self._find_exchange_rate(
                    book,
                    from_commodity=inv.currency,
                    to_commodity=pay_acct.commodity,
                    as_of=parsed_date,
                )
                if exchange_rate is None:
                    raise ValueError(
                        f"Cross-currency payment requires an exchange rate: "
                        f"invoice currency {inv.currency.mnemonic} differs "
                        f"from payment account commodity "
                        f"{pay_acct.commodity.mnemonic}, and no matching "
                        f"price was found in the book for "
                        f"{inv.currency.mnemonic}/{pay_acct.commodity.mnemonic} "
                        f"on or near {parsed_date}. Add a price with "
                        f"create_price, then retry."
                    )
                pay_quantity = (payment_amount * exchange_rate).quantize(
                    Decimal("0.01")
                )

            if is_bill:
                # Pay vendor bill: debit A/P (positive), credit bank (negative)
                ar_ap_split = piecash.Split(
                    account=post_acct,
                    value=payment_amount,
                    quantity=payment_amount,
                    memo="",
                    action="Payment",
                )
                bank_split = piecash.Split(
                    account=pay_acct,
                    value=-payment_amount,
                    quantity=-pay_quantity,
                    memo="",
                )
            else:
                # Receive customer payment: credit A/R (negative), debit bank (positive)
                ar_ap_split = piecash.Split(
                    account=post_acct,
                    value=-payment_amount,
                    quantity=-payment_amount,
                    memo="",
                    action="Payment",
                )
                bank_split = piecash.Split(
                    account=pay_acct,
                    value=payment_amount,
                    quantity=pay_quantity,
                    memo="",
                )

            # Realized FX gain/loss: when cross-currency and the rate
            # moved between post-date and pay-date, the USD actually
            # received/spent differs from what was originally recorded
            # as income/expense. That delta is taxable FX gain (or
            # loss); book it to an auto-created Income:FX Gain/Loss
            # account. The split has value=0 in the transaction
            # currency (so EUR/GBP/etc. balance is preserved) but
            # non-zero quantity in the book's default currency. Same
            # account for both directions; sign determines gain vs
            # loss.
            splits = [ar_ap_split, bank_split]
            fx_gain_loss = None
            fx_diff = Decimal("0")
            fx_acct = None
            if exchange_rate is not None:
                rate_at_post = self._rate_from_post_transaction(
                    inv.post_txn, inv.currency
                )
                if rate_at_post is not None:
                    expected_at_post = (
                        payment_amount * rate_at_post
                    ).quantize(Decimal("0.01"))
                    fx_diff = pay_quantity - expected_at_post
                    if abs(fx_diff) >= Decimal("0.01"):
                        fx_acct = self._get_or_create_fx_account(book)
                        # Customer (is_bill=False): we received MORE
                        # USD than income recorded → gain → credit
                        # income account (quantity = -fx_diff for a
                        # gain, +|fx_diff| for a loss).
                        # Vendor bill (is_bill=True): we spent MORE
                        # USD than expense recorded → loss → debit
                        # income account (quantity = +fx_diff for a
                        # loss, -|fx_diff| for a gain).
                        quantity_sign = 1 if is_bill else -1
                        is_loss = (is_bill and fx_diff > 0) or (
                            not is_bill and fx_diff < 0
                        )
                        fx_gain_loss = piecash.Split(
                            account=fx_acct,
                            value=Decimal("0"),
                            quantity=quantity_sign * fx_diff,
                            memo=(
                                f"FX {'loss' if is_loss else 'gain'} "
                                f"on invoice {inv.id}: post-rate "
                                f"{rate_at_post:.4f}, pay-rate "
                                f"{exchange_rate}"
                            ),
                        )
                        splits.append(fx_gain_loss)

            txn = piecash.Transaction(
                currency=inv.currency,
                description=txn_desc,
                post_date=parsed_date,
                num="",
                splits=splits,
            )

            ar_ap_split.lot = lot_obj

            book.flush()

            txn["trans-txn-type"] = "P"

            remaining = self._calculate_lot_balance(lot_obj)
            if remaining == Decimal(0):
                lot_obj.is_closed = -1

            book.save()

            result = {
                "id": inv.id,
                "type": "bill" if is_bill else "invoice",
                "status": "paid",
                "amount_paid": str(payment_amount),
                "remaining_balance": str(abs(remaining)),
                "transaction_guid": txn.guid,
                "payment_account": pay_acct.fullname,
                "payment_date": str(parsed_date),
            }
            if exchange_rate is not None:
                result["exchange_rate"] = str(exchange_rate)
                result["payment_account_amount"] = str(pay_quantity)
                result["invoice_currency"] = inv.currency.mnemonic
                result["payment_account_currency"] = pay_acct.commodity.mnemonic
                if fx_gain_loss is not None:
                    direction = (
                        "loss" if (is_bill and fx_diff > 0)
                        or (not is_bill and fx_diff < 0)
                        else "gain"
                    )
                    result["fx_realized"] = {
                        "amount": str(abs(fx_diff).quantize(Decimal("0.01"))),
                        "direction": direction,
                        "account": fx_acct.fullname,
                    }

        return result

    # ── Delete paths ──────────────────────────────────────────────

    def delete_invoice(self, invoice_id: str) -> dict:
        """Delete an unposted customer invoice.

        Automatically removes associated entries. Posted invoices cannot
        be deleted — void them or issue a credit note instead.

        Args:
            invoice_id: Invoice ID (e.g., '000001' or 'INV-2026-001').

        Returns:
            Dict with id, guid, entries_deleted, status.

        Raises:
            ValueError: If invoice not found or is posted.
        """
        return self._delete_invoice_or_bill(invoice_id, owner_type=2)

    def delete_bill(self, bill_id: str) -> dict:
        """Delete an unposted vendor bill.

        Automatically removes associated entries. Posted bills cannot
        be deleted — void them or issue a credit note instead.

        Args:
            bill_id: Bill ID (e.g., '000001' or 'BILL-2026-001').

        Returns:
            Dict with id, guid, entries_deleted, status.

        Raises:
            ValueError: If bill not found or is posted.
        """
        return self._delete_invoice_or_bill(bill_id, owner_type=4)

    def _delete_invoice_or_bill(self, doc_id: str, owner_type: int) -> dict:
        """Shared implementation for delete_invoice and delete_bill."""
        from sqlalchemy import func, select
        from piecash.business.invoice import Entry, Invoice

        from gnucash_mcp.book._base import _verify_delete

        type_label = "Bill" if owner_type == 4 else "Invoice"
        entry_fk = "bill" if owner_type == 4 else "invoice"
        entry_fk_col = getattr(Entry.__table__.c, entry_fk)

        with self.open(readonly=False) as book:
            inv = self._find_invoice(book, doc_id, owner_type=owner_type)
            if not inv:
                raise ValueError(f"{type_label} not found: {doc_id}")

            if _is_invoice_posted(inv):
                raise ValueError(
                    f"Cannot delete posted {type_label.lower()} '{doc_id}'. "
                    f"Void it or issue a credit note instead."
                )

            inv_guid = inv.guid

            # Entry cleanup — count first so the response can report how
            # many were removed, then delete via SQLAlchemy Core. A column
            # rename of ``entries.invoice`` or ``entries.bill`` in a
            # future GnuCash release surfaces as AttributeError at import.
            entry_count = book.session.execute(
                select(func.count())
                .select_from(Entry.__table__)
                .where(entry_fk_col == inv_guid)
            ).scalar()
            if entry_count:
                book.session.execute(
                    Entry.__table__.delete().where(entry_fk_col == inv_guid)
                )
                _verify_delete(
                    book.session,
                    Entry.__table__,
                    {entry_fk: inv_guid},
                    f"Entries for {type_label.lower()} '{doc_id}'",
                )

            book.session.execute(
                Invoice.__table__.delete().where(
                    Invoice.__table__.c.guid == inv_guid
                )
            )
            _verify_delete(
                book.session,
                Invoice.__table__,
                {"guid": inv_guid},
                f"{type_label} '{doc_id}'",
            )

            book.save()

            return {
                "id": doc_id,
                "guid": inv_guid,
                "type": type_label.lower(),
                "entries_deleted": entry_count,
                "status": "deleted",
            }

    def _delete_business_person(
        self,
        *,
        entity_id: str,
        entity_label: str,
        find_entity_method: str,
        dependency_check=None,
    ) -> dict:
        """Shared delete path for Customer / Vendor / Employee.

        Skeleton:
          1. Open the book (readwrite).
          2. Find the entity via ``find_entity_method``; raise if absent.
          3. Run the caller's ``dependency_check(book, entity_guid)``
             if provided — customer/vendor both block on existing
             documents; Employee may or may not, depending on schema.
             The callback raises ValueError with its own message.
          4. Clean up the ``slots`` table via SQLAlchemy Core +
             ``_verify_delete`` (Customer / Vendor / Employee rows can
             accumulate slots over their lifetime — no ON DELETE
             CASCADE on ``obj_guid``, so we clean up explicitly).
          5. ORM-delete the entity; save.
          6. Return the canonical ``{id, guid, name, type, status}``
             response dict.

        Args:
            entity_id: Human-readable ID ('000001').
            entity_label: "Customer" / "Vendor" / "Employee" — used
                in error messages and the response's ``type`` key
                (lowercased).
            find_entity_method: Name of the finder method on ``self``;
                resolved via ``getattr``.
            dependency_check: Optional callable
                ``(book, entity_guid) -> None`` that raises
                ``ValueError`` if the entity has dependent rows that
                block deletion. When None, the entity is deleted
                unconditionally after slot cleanup.

        Returns:
            ``{id, guid, name, type, status}``
        """
        from piecash.kvp import Slot

        from gnucash_mcp.book._base import _verify_delete

        find_entity = getattr(self, find_entity_method)

        with self.open(readonly=False) as book:
            entity = find_entity(book, entity_id)
            if not entity:
                raise ValueError(f"{entity_label} not found: {entity_id}")

            entity_guid = entity.guid
            entity_name = entity.name

            if dependency_check is not None:
                dependency_check(book, entity_guid)

            # Slot cleanup via SQLAlchemy Core. Slots on business-person
            # rows (notes, tax info, etc.) have no ON DELETE CASCADE
            # on ``obj_guid``, so we clean them explicitly before the
            # ORM delete.
            book.session.execute(
                Slot.__table__.delete().where(
                    Slot.__table__.c.obj_guid == entity_guid
                )
            )
            _verify_delete(
                book.session,
                Slot.__table__,
                {"obj_guid": entity_guid},
                f"Slots for {entity_label.lower()} '{entity_id}'",
            )

            book.session.delete(entity)
            book.save()

            return {
                "id": entity_id,
                "guid": entity_guid,
                "name": entity_name,
                "type": entity_label.lower(),
                "status": "deleted",
            }

    @staticmethod
    def _invoice_dependency_check(
        entity_label: str, owner_type: int, doc_label: str,
    ):
        """Build a dependency_check callback for business-person delete.

        Used by Customer and Vendor delete paths (and potentially
        Employee, if Employees own documents). Returns a closure that
        queries Invoice rows matching the owner_type and raises a
        ValueError with posted/unposted-specific wording if any exist.
        """
        from piecash.business.invoice import Invoice

        def check(book, entity_guid):
            invoices = book.session.query(Invoice).filter(
                Invoice.owner_guid == entity_guid,
                Invoice.owner_type == owner_type,
            ).all()
            if not invoices:
                return
            posted = [i for i in invoices if _is_invoice_posted(i)]
            unposted = [i for i in invoices if not _is_invoice_posted(i)]
            if posted:
                posted_ids = ", ".join(i.id for i in posted)
                raise ValueError(
                    f"Cannot delete {entity_label.lower()} with posted "
                    f"{doc_label}: {posted_ids}. "
                    f"Void them or issue credit notes first."
                )
            unposted_ids = ", ".join(i.id for i in unposted)
            raise ValueError(
                f"Cannot delete {entity_label.lower()} with "
                f"{doc_label}: {unposted_ids}. "
                f"Delete the {doc_label} first."
            )

        return check

    def delete_customer(self, customer_id: str) -> dict:
        """Delete a customer with no invoices.

        Customers with any invoices (posted or unposted) cannot be deleted.
        Delete the invoices first, or void posted ones.

        Args:
            customer_id: Customer ID (e.g., '000001').

        Returns:
            Dict with id, guid, name, status.

        Raises:
            ValueError: If customer not found or has invoices.
        """
        return self._delete_business_person(
            entity_id=customer_id,
            entity_label="Customer",
            find_entity_method="_find_customer",
            dependency_check=self._invoice_dependency_check(
                entity_label="Customer",
                owner_type=2,
                doc_label="invoices",
            ),
        )

    def delete_vendor(self, vendor_id: str) -> dict:
        """Delete a vendor with no bills.

        Vendors with any bills (posted or unposted) cannot be deleted.
        Delete the bills first, or void posted ones.

        Args:
            vendor_id: Vendor ID (e.g., '000001').

        Returns:
            Dict with id, guid, name, status.

        Raises:
            ValueError: If vendor not found or has bills.
        """
        return self._delete_business_person(
            entity_id=vendor_id,
            entity_label="Vendor",
            find_entity_method="_find_vendor",
            dependency_check=self._invoice_dependency_check(
                entity_label="Vendor",
                owner_type=4,
                doc_label="bills",
            ),
        )

    def delete_employee(self, employee_id: str) -> dict:
        """Delete an employee.

        Employees in the 1.3.0 release have no associated documents —
        expense vouchers (``counter_exp_voucher`` / ``owner_type=5``)
        are out of scope. The delete proceeds unconditionally after
        slot cleanup.

        Args:
            employee_id: Employee ID (e.g., '000001').

        Returns:
            Dict with id, guid, name, status.

        Raises:
            ValueError: If employee not found.
        """
        return self._delete_business_person(
            entity_id=employee_id,
            entity_label="Employee",
            find_entity_method="_find_employee",
            # Employees own nothing in 1.3.0 — no dependency check.
        )

    # ── Reporting ────────────────────────────────────────────────

    def get_outstanding_invoices(
        self,
        owner_type: str | None = None,
        customer_id: str | None = None,
        vendor_id: str | None = None,
    ) -> list[dict]:
        """Get all posted invoices/bills with outstanding balances.

        Args:
            owner_type: Filter by 'customer' or 'vendor'. Omit for all.
            customer_id: Filter by specific customer ID.
            vendor_id: Filter by specific vendor ID.

        Returns:
            List of dicts with invoice details and balance info.
        """
        from piecash.business.invoice import Invoice

        with self.open() as book:
            query = book.session.query(Invoice).filter(
                Invoice.date_posted.isnot(None)
            )

            if owner_type == "customer":
                query = query.filter(Invoice.owner_type == 2)
            elif owner_type == "vendor":
                query = query.filter(Invoice.owner_type == 4)

            if customer_id:
                customer = None
                for c in book.customers:
                    if c.id == customer_id:
                        customer = c
                        break
                if not customer:
                    raise ValueError(
                        f"Customer not found: {customer_id}"
                    )
                query = query.filter(
                    Invoice.owner_guid == customer.guid
                )

            if vendor_id:
                vendor = None
                for v in book.vendors:
                    if v.id == vendor_id:
                        vendor = v
                        break
                if not vendor:
                    raise ValueError(
                        f"Vendor not found: {vendor_id}"
                    )
                query = query.filter(
                    Invoice.owner_guid == vendor.guid
                )

            invoices = query.order_by(
                Invoice.date_posted.desc()
            ).all()

            results = []
            for inv in invoices:
                is_bill = inv.owner_type == 4

                post_acc_guid = inv.post_acc_guid
                post_acct = None
                for a in book.accounts:
                    if a.guid == post_acc_guid:
                        post_acct = a
                        break
                if not post_acct:
                    continue

                lot_obj = None
                for lot in post_acct.lots:
                    if lot.guid == inv.post_lot_guid:
                        lot_obj = lot
                        break
                if not lot_obj:
                    continue

                balance = self._calculate_lot_balance(lot_obj)
                if balance == Decimal(0):
                    continue

                try:
                    _, _, grand_total = (
                        self._get_invoice_entries_and_total(book, inv)
                    )
                except ValueError:
                    grand_total = abs(balance)

                amount_paid = grand_total - abs(balance)

                owner_name = None
                if is_bill:
                    v = self._find_vendor_by_guid(
                        book, inv.owner_guid
                    )
                    if v:
                        owner_name = v.name
                else:
                    c = self._find_customer_by_guid(
                        book, inv.owner_guid
                    )
                    if c:
                        owner_name = c.name

                posted_dt = _safe_date_posted(inv)
                results.append({
                    "id": inv.id,
                    "type": "bill" if is_bill else "invoice",
                    "owner_name": owner_name,
                    "date_posted": (
                        str(posted_dt.date()) if posted_dt else None
                    ),
                    "original_amount": str(grand_total),
                    "amount_paid": str(amount_paid),
                    "amount_due": str(abs(balance)),
                })

        return results

    def vendor_spending_report(
        self,
        start_date: str,
        end_date: str,
        vendor_id: str | None = None,
    ) -> dict:
        """Get spending breakdown by vendor for a period.

        Analyzes posted vendor bills to show total billed, paid,
        and outstanding amounts per vendor.

        Args:
            start_date: Start of period (YYYY-MM-DD).
            end_date: End of period (YYYY-MM-DD).
            vendor_id: Optional filter to specific vendor.

        Returns:
            Dict with per-vendor breakdown and grand totals.
        """
        from piecash.business.invoice import Invoice

        parsed_start = date.fromisoformat(start_date)
        parsed_end = date.fromisoformat(end_date)

        with self.open() as book:
            query = book.session.query(Invoice).filter(
                Invoice.owner_type == 4,
                Invoice.date_posted.isnot(None),
            )

            if vendor_id:
                vendor = None
                for v in book.vendors:
                    if v.id == vendor_id:
                        vendor = v
                        break
                if not vendor:
                    raise ValueError(
                        f"Vendor not found: {vendor_id}"
                    )
                query = query.filter(
                    Invoice.owner_guid == vendor.guid
                )

            bills = query.all()

            # Filter by date range. ``_safe_date_posted`` returns
            # None for records where date_posted is missing or
            # malformed (empty-string state) — those drop out.
            filtered_bills = []
            for b in bills:
                posted = _safe_date_posted(b)
                if posted is None:
                    continue
                if parsed_start <= posted.date() <= parsed_end:
                    filtered_bills.append(b)
            bills = filtered_bills

            vendor_data: dict[str, dict] = {}
            for bill in bills:
                v = self._find_vendor_by_guid(
                    book, bill.owner_guid
                )
                v_name = v.name if v else "Unknown"
                v_id = v.id if v else ""

                if v_name not in vendor_data:
                    vendor_data[v_name] = {
                        "vendor_id": v_id,
                        "vendor_name": v_name,
                        "total_billed": Decimal(0),
                        "total_paid": Decimal(0),
                        "outstanding": Decimal(0),
                        "bill_count": 0,
                    }

                try:
                    _, _, total = (
                        self._get_invoice_entries_and_total(
                            book, bill
                        )
                    )
                except ValueError:
                    total = Decimal(0)

                balance = Decimal(0)
                post_acct = None
                for a in book.accounts:
                    if a.guid == bill.post_acc_guid:
                        post_acct = a
                        break
                if post_acct:
                    for lot in post_acct.lots:
                        if lot.guid == bill.post_lot_guid:
                            balance = self._calculate_lot_balance(
                                lot
                            )
                            break

                outstanding = abs(balance)
                paid = total - outstanding

                vendor_data[v_name]["total_billed"] += total
                vendor_data[v_name]["total_paid"] += paid
                vendor_data[v_name]["outstanding"] += outstanding
                vendor_data[v_name]["bill_count"] += 1

            vendors_list = []
            grand_billed = Decimal(0)
            grand_paid = Decimal(0)
            grand_outstanding = Decimal(0)

            for vd in vendor_data.values():
                grand_billed += vd["total_billed"]
                grand_paid += vd["total_paid"]
                grand_outstanding += vd["outstanding"]
                vendors_list.append({
                    "vendor_id": vd["vendor_id"],
                    "vendor_name": vd["vendor_name"],
                    "total_billed": str(vd["total_billed"]),
                    "total_paid": str(vd["total_paid"]),
                    "outstanding": str(vd["outstanding"]),
                    "bill_count": vd["bill_count"],
                })

            vendors_list.sort(
                key=lambda x: Decimal(x["total_billed"]),
                reverse=True,
            )

        return {
            "period": {
                "start": start_date,
                "end": end_date,
            },
            "vendors": vendors_list,
            "totals": {
                "total_billed": str(grand_billed),
                "total_paid": str(grand_paid),
                "outstanding": str(grand_outstanding),
                "vendor_count": len(vendors_list),
                "bill_count": sum(
                    v["bill_count"] for v in vendors_list
                ),
            },
        }
