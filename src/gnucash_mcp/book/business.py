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


def _format_vendor_spending_compact(
    vendors_list: list[dict],
    *,
    grand_billed: Decimal,
    grand_paid: Decimal,
    grand_outstanding: Decimal,
    currency: str = "USD",
) -> str:
    """Render vendor-spending breakdown as a compact aligned text table.

    Format::

        BookkeepingCo  4 bills  USD 1,800 billed  USD 1,800 paid  USD 0 outstanding
        JetBrains      1 bill     USD 289 billed    USD 289 paid  USD 0 outstanding
        TOTAL          5 bills  USD 2,089 billed  USD 2,089 paid  USD 0 outstanding

    Currency prefix flows from the book's default currency. "1 bill" vs
    "N bills" pluralization keeps the line natural to read. Width-padded
    so the four amount columns align across rows.
    """
    if not vendors_list:
        return "No vendor activity in period."

    # Single-pass widths.
    name_width = max(len(v["vendor_name"]) for v in vendors_list)
    bills_strs = [
        f"{v['bill_count']} {'bill' if v['bill_count'] == 1 else 'bills'}"
        for v in vendors_list
    ]
    bills_width = max(len(s) for s in bills_strs)

    def _money(s: str) -> str:
        d = Decimal(s)
        if d == d.to_integral_value():
            return f"{currency} {int(d):,}"
        return f"{currency} {d:,.2f}"

    billed_strs = [_money(v["total_billed"]) for v in vendors_list]
    paid_strs = [_money(v["total_paid"]) for v in vendors_list]
    out_strs = [_money(v["outstanding"]) for v in vendors_list]
    billed_w = max(len(s) for s in billed_strs)
    paid_w = max(len(s) for s in paid_strs)
    out_w = max(len(s) for s in out_strs)

    lines = []
    for v, bills, billed, paid, out in zip(
        vendors_list, bills_strs, billed_strs, paid_strs, out_strs,
    ):
        lines.append(
            f"{v['vendor_name']:<{name_width}}  "
            f"{bills:<{bills_width}}  "
            f"{billed:>{billed_w}} billed  "
            f"{paid:>{paid_w}} paid  "
            f"{out:>{out_w}} outstanding"
        )

    total_bill_count = sum(v["bill_count"] for v in vendors_list)
    total_label = f"{total_bill_count} {'bill' if total_bill_count == 1 else 'bills'}"
    lines.append(
        f"{'TOTAL':<{name_width}}  "
        f"{total_label:<{bills_width}}  "
        f"{_money(str(grand_billed)):>{billed_w}} billed  "
        f"{_money(str(grand_paid)):>{paid_w}} paid  "
        f"{_money(str(grand_outstanding)):>{out_w}} outstanding"
    )
    return "\n".join(lines)


def _format_outstanding_invoices_compact(rows: list[dict]) -> str:
    """Render outstanding invoices/bills as a one-line-per-doc string.

    Format per row:

        000028  Berlin Digital GmbH  EUR 4,200  posted:2026-02-01  due:2026-03-03  55 days past due
        000011  BookkeepingCo (BILL)  USD 450  posted:2026-03-15  due:2026-04-14  13 days past due

    Action columns are the win here: ``due:`` and the days-past-due
    count tell the bookkeeper exactly which invoice is bleeding the
    most days, without forcing a separate calculation. ``(BILL)`` is
    appended to vendor-bill owners so receivables and payables don't
    get confused at a glance.

    When the due date came from the 30-day default (``no_terms`` flag),
    the days count is anchored to the assumption ("days past 30-day
    default") rather than reading as contractual.
    """
    if not rows:
        return ""
    lines = []
    for r in rows:
        owner = r.get("owner_name") or f"#{r['id']}"
        if r.get("type") == "bill":
            owner = f"{owner} (BILL)"
        ccy = r.get("currency") or ""
        # Strip trailing zeros for compact display: "4200.00" → "4,200".
        amount_dec = Decimal(r.get("amount_due") or "0")
        amount_str = f"{int(amount_dec):,}" if amount_dec == int(amount_dec) else f"{amount_dec:,.2f}"
        posted = r.get("date_posted") or "?"
        due = r.get("due_date") or "?"
        days = r.get("days_past_due")
        if days is None:
            days_str = ""
        elif days > 0:
            # Two phrasings — "X days past due" reads as contractual
            # (the user agreed to a date and missed it), "X days past
            # 30-day default" anchors the duration to the assumption
            # we made when no term was set. Pre-fix the templating
            # concatenated "days past " with another "past due" so
            # the contractual case rendered as "days past past due".
            if r.get("no_terms"):
                days_str = f"  {days} days past 30-day default"
            else:
                days_str = f"  {days} days past due"
        elif days == 0:
            days_str = "  due today"
        else:
            days_str = f"  due in {-days} days"
        lines.append(
            f"{r['id']}\t{owner}\t{ccy} {amount_str}\t"
            f"posted:{posted}\tdue:{due}{days_str}"
        )
    return "\n".join(lines)


class BusinessMixin:
    """Customer/vendor/invoice/bill operations."""

    # ── Finders and dict converters ───────────────────────────────

    @staticmethod
    def _find_customer(book, customer_id: str):
        """Find a customer by their human-readable ID (e.g., '000001').

        Uses an indexed ``filter_by`` query rather than scanning
        ``book.customers``. The ORM-backed CallableList iteration
        was a real hot-path cost in business workflows that look up
        the same customer multiple times per write.
        """
        from piecash.business.person import Customer
        return book.session.query(Customer).filter_by(id=customer_id).first()

    @staticmethod
    def _find_vendor(book, vendor_id: str):
        """Find a vendor by their human-readable ID (e.g., '000001')."""
        from piecash.business.person import Vendor
        return book.session.query(Vendor).filter_by(id=vendor_id).first()

    @staticmethod
    def _find_customer_by_guid(book, guid: str):
        """Find a customer by GUID (indexed)."""
        from piecash.business.person import Customer
        return book.session.query(Customer).filter_by(guid=guid).first()

    @staticmethod
    def _find_vendor_by_guid(book, guid: str):
        """Find a vendor by GUID (indexed)."""
        from piecash.business.person import Vendor
        return book.session.query(Vendor).filter_by(guid=guid).first()

    # Path for the auto-created realized-FX-gain/loss income account.
    # Single credit-natural account: positive balance = net gain, negative
    # = net loss across the period. Named to match GAAP convention
    # ("Foreign Exchange Gain (Loss)") while staying one account for
    # simplicity.
    FX_GAIN_LOSS_PATH = "Income:Foreign Exchange Gain/Loss"

    # Substrings that identify a user-named FX gain/loss account on
    # the leaf-name match below. Books in the wild use a wide range
    # of names — "FX Gain Loss", "Currency Translation", "Foreign
    # Exchange Gain/Loss", etc. — and the canonical name above is
    # only one convention among many. Bare ``currency`` is excluded
    # deliberately: too many books have accounts like "Foreign
    # Currency Cash" that aren't gain/loss accounts.
    _FX_NAME_KEYWORDS = (
        "fx",
        "forex",
        "foreign exchange",
        "currency gain",
        "currency loss",
        "exchange gain",
        "exchange loss",
        "currency translation",
    )

    def _get_or_create_fx_account(self, book, fx_account: str | None = None):
        """Find or lazily create the FX-gain/loss account.

        Returns ``(account, notice)`` where ``notice`` is ``None`` in
        the unambiguous cases and a dict describing the ambiguity
        when the fuzzy match found multiple candidates and we fell
        back to the canonical default.

        Resolution order:

        1. **Explicit ``fx_account``** (path, ``%short``, or full
           GUID) — caller wants a specific account. Validated for
           existence and INCOME/EXPENSE type, then used.
        2. **Fuzzy match**, leaf-name substring against
           ``_FX_NAME_KEYWORDS`` over INCOME/EXPENSE accounts:
           - Exactly one match → use it.
           - Zero matches → fall through to the canonical default
             (existing path lookup, then auto-create if absent).
           - More than one match → fall through to the canonical
             default *and* return a notice asking the caller to
             pass ``fx_account`` next time. Don't guess between
             user-created accounts.
        3. **Canonical default**: existing ``Income:Foreign Exchange
           Gain/Loss`` if present, else auto-create it under
           ``Income``.

        Created on first cross-currency pay whose post-date rate
        differs from its pay-date rate, so books without foreign-
        currency activity never accumulate an unused account.

        Raises:
            ValueError: If ``fx_account`` is supplied but doesn't
                exist or isn't INCOME/EXPENSE; or if no fuzzy match
                exists and the parent ``Income`` account is missing.
        """
        # Layer 1: caller-supplied account wins.
        if fx_account is not None:
            acct = self._resolve_account(book, fx_account)
            if acct is None:
                raise ValueError(
                    f"fx_account not found: {fx_account!r}. Pass a "
                    f"full path, %short GUID, or full 32-char GUID "
                    f"of an INCOME or EXPENSE account."
                )
            if acct.type not in {"INCOME", "EXPENSE"}:
                raise ValueError(
                    f"fx_account {acct.fullname!r} is type "
                    f"{acct.type}; must be INCOME or EXPENSE to "
                    f"receive realized FX gain/loss."
                )
            return acct, None

        # Layer 2: fuzzy match by leaf-name substring.
        candidates = []
        for account in book.accounts:
            if account.type not in {"INCOME", "EXPENSE"}:
                continue
            name_lower = account.name.lower()
            if any(kw in name_lower for kw in self._FX_NAME_KEYWORDS):
                candidates.append(account)

        notice = None
        if len(candidates) == 1:
            return candidates[0], None
        if len(candidates) > 1:
            sorted_paths = sorted(c.fullname for c in candidates)
            notice = {
                "type": "ambiguous_fx_account",
                "candidates": sorted_paths,
                "message": (
                    f"Found {len(candidates)} candidate FX accounts "
                    f"({', '.join(sorted_paths)}). Routed to "
                    f"{self.FX_GAIN_LOSS_PATH} as the canonical "
                    f"default; pass fx_account explicitly to "
                    f"disambiguate."
                ),
            }
            # Fall through to canonical default below.

        # Layer 3: canonical default (existing or auto-create).
        fx_acct = self._find_account(book, self.FX_GAIN_LOSS_PATH)
        if fx_acct is not None:
            return fx_acct, notice

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
        return fx_acct, notice

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
        from piecash.business.person import Employee
        return book.session.query(Employee).filter_by(id=employee_id).first()

    @staticmethod
    def _parse_owner_type(owner_type: str | None) -> int | None:
        """Map an owner_type string to its piecash integer code.

        ``None`` → ``None`` (caller wants no filter).
        ``"customer"`` → 2.
        ``"vendor"`` → 4.

        Anything else raises ``ValueError`` with a message that
        names the valid options *and* explicitly calls out
        ``"employee"`` as not yet supported. Employee expense
        vouchers (``owner_type=5`` in piecash) are explicitly out
        of scope for the 1.2.x business module — see the
        ``delete_employee`` docstring's reference to
        ``counter_exp_voucher``. Pre-fix, an LLM passing
        ``owner_type="employee"`` would silently fall through to
        the unfiltered lookup and discover the limitation only
        via a confusing downstream error (e.g. a cross-sequence
        ID-collision message that suggests "customer or vendor"
        without mentioning employee at all). The upfront
        rejection saves the LLM a tool call and frames the
        limitation cleanly.
        """
        if owner_type is None:
            return None
        if owner_type == "customer":
            return 2
        if owner_type == "vendor":
            return 4
        if owner_type == "employee":
            raise ValueError(
                "owner_type='employee' is not yet supported. "
                "Employee expense vouchers are out of scope for "
                "the 1.2.x business module. Use 'customer' or "
                "'vendor'."
            )
        raise ValueError(
            f"Invalid owner_type {owner_type!r}. "
            f"Must be 'customer' or 'vendor'."
        )

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
                        None requires the ID to be unambiguous across
                        types — see Raises.

        Raises:
            ValueError: When ``owner_type=None`` and the ID matches
                both a customer invoice *and* a vendor bill. GnuCash
                runs the two as separate ID sequences sharing one
                ``invoices`` table, so collisions are normal — both
                can legitimately be id ``"000003"``. Pre-fix this
                returned whichever row the query happened to surface
                first, silently routing reads/writes to the wrong
                document. The bookkeeper hit this on a CNY book
                where ``get_invoice("000003")`` returned a customer
                invoice's CNY currency for what was actually a USD
                vendor bill. The error lists candidates with their
                type and currency so the caller can pass
                ``owner_type`` to disambiguate.
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
            return query.filter(Invoice.owner_type == owner_type).first()

        # owner_type=None: caller didn't disambiguate. Pull all
        # matches and fail loud on collision rather than silently
        # picking the first one (which depends on row order /
        # piecash internals — non-deterministic from the caller's
        # perspective).
        matches = query.all()
        if len(matches) <= 1:
            return matches[0] if matches else None

        candidates = []
        for m in matches:
            label = "vendor bill" if m.owner_type == 4 else "customer invoice"
            currency = m.currency.mnemonic if m.currency else "?"
            candidates.append(f"{label} (currency={currency})")
        raise ValueError(
            f"Found {len(matches)} documents with ID {invoice_id!r}: "
            f"{', '.join(candidates)}. Pass owner_type='customer' "
            f"or 'vendor' to disambiguate."
        )

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
        and Vendor — see specs/PIECASH_REFERENCE.md), so the response
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
    def _invoice_to_dict(invoice, entries=None, owner_name: str | None = None) -> dict:
        """Convert a piecash Invoice to a serializable dict.

        ``owner_type`` (numeric 2/4) was redundant with the
        human-readable ``type`` field and is dropped. ``is_posted``
        is derivable from ``date_posted`` (non-null = posted) and
        is dropped too. Phase 3C: ``owner_guid`` (raw 32-char hex)
        is dropped in favor of ``owner_name`` resolved by the caller
        — the same readability swap we did for entry account refs.

        Args:
            invoice: piecash Invoice object.
            entries: Optional list of entry dicts. If None, entries are not included.
            owner_name: Resolved customer/vendor name. The static-method
                shape means we can't query for it here; the caller
                (``get_invoice``) does the lookup once and threads it.
        """
        result = {
            "guid": invoice.guid,
            "id": invoice.id,
            "type": "bill" if invoice.owner_type == 4 else "invoice",
            "owner_name": owner_name,
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

    def _invoice_to_compact_line(self, book, invoice) -> str:
        """One-line compact format with action columns:

            ``id  TYPE  owner_name  CCY total  date_opened  status``

        Pre-Phase-3B this rendered as ``"000027  INV  2026-05-01
        posted"`` — no owner, no amount, useless for scanning. With
        owner and total in place a bookkeeper can scan a hundred
        invoices and immediately spot what's outstanding for whom.

        Currency is shown when present so multi-currency books read
        unambiguously. Bills get the ``BILL`` tag (was already there).
        """
        inv_type = "BILL" if invoice.owner_type == 4 else "INV"
        date_str = (
            str(invoice.date_opened.date())
            if invoice.date_opened
            else "n/a"
        )
        status = "posted" if _is_invoice_posted(invoice) else "open"

        # Owner lookup — vendors and customers share the same
        # ``owner_guid`` namespace (different table, but same handle
        # shape). Picking the right finder by ``owner_type`` matches
        # how every other code path in this file resolves it.
        if invoice.owner_type == 4:
            owner = self._find_vendor_by_guid(book, invoice.owner_guid)
        else:
            owner = self._find_customer_by_guid(book, invoice.owner_guid)
        owner_name = owner.name if owner else "?"

        # Total: sum of (quantity * price) across entries. Falls back
        # to "?" when entries can't be loaded — keeps the row legible
        # even on data-corruption edge cases.
        try:
            _, _, grand_total = self._get_invoice_entries_and_total(
                book, invoice,
            )
            ccy = (
                invoice.currency.mnemonic
                if invoice.currency else ""
            )
            if grand_total == int(grand_total):
                amount_str = f"{ccy} {int(grand_total):,}".strip()
            else:
                amount_str = f"{ccy} {grand_total:,.2f}".strip()
        except Exception:
            amount_str = "?"

        return (
            f"{invoice.id}\t{inv_type}\t{owner_name}\t{amount_str}\t"
            f"{date_str}\t{status}"
        )

    @staticmethod
    def _entry_to_dict(
        entry_row,
        is_bill: bool = False,
        account_paths: dict | None = None,
    ) -> dict:
        """Convert an entry row (from raw SQL) to a serializable dict.

        Args:
            entry_row: SQLAlchemy row from entries table.
            is_bill: If True, read b_* columns; otherwise read i_* columns.
            account_paths: Optional mapping of account-GUID to fullname.
                When provided, the response uses ``account`` (path) in
                place of ``account_guid`` (raw 32-char hex). The caller
                builds this map once per invoice and passes it through;
                this keeps the static-method shape while letting the
                response be readable to humans/LLMs.
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

        result = {
            "guid": entry_row.guid,
            "date": date_str,
            "description": entry_row.description or "",
            "quantity": str(quantity),
            "price": str(price),
            "total": str(total),
        }
        if account_paths is not None and acct_guid:
            # Phase 3C: surface the readable path. Falls back to the
            # raw GUID when a stale entry references a deleted account.
            result["account"] = account_paths.get(
                acct_guid, acct_guid,
            )
        else:
            result["account_guid"] = acct_guid or ""
        return result

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
        and rejects the kwarg — see specs/PIECASH_REFERENCE.md for the
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

    # Address sub-fields piecash exposes on the Address record. Used
    # both to validate caller-supplied dict keys and to iterate during
    # update.
    _ADDRESS_FIELDS = (
        "name", "addr1", "addr2", "addr3", "addr4",
        "phone", "fax", "email",
    )

    def _update_business_person(
        self,
        *,
        book,
        entity,
        entity_label: str,
        name: str | None = None,
        currency: str | None = None,
        notes: str | None = None,
        active: bool | None = None,
        address: dict | None = None,
    ) -> dict:
        """Shared update path for Customer / Vendor / Employee.

        Mutates only the fields the caller supplies; everything else
        is left alone. Diff-style response: returns ``{guid, id,
        status, ...changed_fields}`` so the caller sees exactly what
        landed without echoing the full entity record.

        Address handling:

        - ``address=None`` → don't touch the address record.
        - ``address={...}`` → for each provided sub-field, set it on
          the existing Address record (creating one if the entity
          has no address yet). Missing sub-keys are left unchanged;
          to clear a sub-field, pass an empty string explicitly.

        Args:
            book: Open piecash book session (readonly=False).
            entity: Resolved Customer / Vendor / Employee instance.
            entity_label: ``"Customer"`` / ``"Vendor"`` / ``"Employee"``
                — for error messages.
            name: New display name. ``None`` means no change.
            currency: New ISO currency code. ``None`` means no change;
                pass a new mnemonic to switch (rare — currency on a
                business person is essentially their default trading
                currency, changing it doesn't touch existing
                invoices/bills).
            notes: New notes text. Customer/Vendor only — Employee
                will raise via piecash's natural attribute check.
                Pass empty string to clear.
            active: New active flag. Use ``False`` to deactivate
                (archive without deleting).
            address: Partial address update dict — see above. Keys:
                ``name``, ``addr1``..``addr4``, ``phone``, ``fax``,
                ``email``. Unknown keys raise ``ValueError`` so a
                typo doesn't silently no-op.

        Returns:
            Dict with the entity GUID, id, ``status: "updated"``, and
            the diff of changed top-level fields. Address changes
            render under an ``address`` key showing only the
            sub-fields that actually changed.

        Raises:
            ValueError: If currency unknown, address dict contains
                unknown keys, or no fields were provided to update.
        """
        # Validate address keys upfront — a typo like ``"addresss":
        # "..."`` would silently no-op without this check.
        if address is not None:
            unknown = set(address.keys()) - set(self._ADDRESS_FIELDS)
            if unknown:
                raise ValueError(
                    f"Unknown address field(s): {sorted(unknown)}. "
                    f"Valid keys: {', '.join(self._ADDRESS_FIELDS)}."
                )

        # Stage pre-update state for the audit log. Mirrors the
        # update_account precedent — the audit decorator reads
        # before_state from this thread-local rather than reopening
        # the book.
        before = {
            "id": entity.id,
            "name": entity.name,
            "currency": (
                entity.currency.mnemonic if entity.currency else None
            ),
            "active": bool(entity.active),
        }
        if hasattr(entity, "notes"):
            before["notes"] = entity.notes or ""
        before_addr = self._address_to_dict(entity)
        if before_addr:
            before["address"] = before_addr
        self._stage_audit_before(before)

        changed: dict = {}

        if name is not None and name != entity.name:
            entity.name = name
            changed["name"] = name

        if currency is not None:
            # ``_get_or_create_currency`` auto-loads ISO codes the book
            # hasn't seen before (matches the ``create_price`` fix
            # earlier in this release). Users shouldn't have to
            # pre-load EUR before switching a vendor to EUR.
            try:
                new_currency = self._get_or_create_currency(book, currency)
            except ValueError:
                raise ValueError(f"Currency not found: {currency}")
            if new_currency != entity.currency:
                entity.currency = new_currency
                changed["currency"] = currency

        if notes is not None:
            if not hasattr(entity, "notes"):
                # Employee has no notes column.
                raise ValueError(
                    f"{entity_label} has no notes field — drop "
                    f"``notes=`` from the update call."
                )
            current_notes = entity.notes or ""
            if current_notes != notes:
                entity.notes = notes
                changed["notes"] = notes

        if active is not None and bool(active) != bool(entity.active):
            entity.active = bool(active)
            changed["active"] = bool(active)

        if address is not None:
            # piecash's ``Address`` is a composite view over raw
            # ``addr_*`` columns on the Customer / Vendor / Employee
            # row. Mutation through the composite (``entity.address
            # .addr1 = ...``) doesn't flush the underlying column
            # change — only direct assignment to the raw column
            # attribute (``entity.addr_addr1 = ...``) persists. The
            # ``_address_to_dict`` reader already pulls from the raw
            # columns, so writes go to the same place reads come from.
            addr_changed: dict = {}
            for key in self._ADDRESS_FIELDS:
                if key not in address:
                    continue
                new_val = address[key]
                column = f"addr_{key}"
                if (getattr(entity, column) or "") != new_val:
                    setattr(entity, column, new_val)
                    addr_changed[key] = new_val
            if addr_changed:
                changed["address"] = addr_changed

        if not changed:
            raise ValueError(
                f"No changes supplied — pass at least one field to "
                f"update on {entity_label.lower()} {entity.id!r}."
            )

        book.save()

        return {
            "guid": entity.guid,
            "id": entity.id,
            "status": "updated",
            **changed,
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
        specs/PIECASH_REFERENCE.md for the full schema shape.

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

    def update_customer(
        self,
        customer_id: str,
        name: str | None = None,
        currency: str | None = None,
        notes: str | None = None,
        active: bool | None = None,
        address: dict | None = None,
    ) -> dict:
        """Update an existing customer's mutable fields.

        Mutates only the fields the caller supplies. Existing
        invoices and bills are unaffected — currency change here
        only sets the customer's *default* trading currency for
        future documents, not the currency of historical activity.

        See ``_update_business_person`` for address-merge semantics
        and full kwarg behavior.

        Args:
            customer_id: Human-readable ID (e.g., '000001').
            name: New display name. ``None`` = no change.
            currency: New default ISO currency code. ``None`` = no change.
            notes: New notes. Pass empty string to clear. ``None`` = no change.
            active: ``False`` to deactivate (archive), ``True`` to
                reactivate. ``None`` = no change.
            address: Partial address dict. Sub-fields supplied are
                merged onto the existing address record (or create
                one if absent).

        Returns:
            Dict with guid, id, status, and the diff of changed fields.

        Raises:
            ValueError: If customer not found, currency unknown, or
                no fields supplied to update.
        """
        with self.open(readonly=False) as book:
            customer = self._find_customer(book, customer_id)
            if not customer:
                raise ValueError(f"Customer not found: {customer_id}")
            return self._update_business_person(
                book=book, entity=customer, entity_label="Customer",
                name=name, currency=currency, notes=notes,
                active=active, address=address,
            )

    def update_vendor(
        self,
        vendor_id: str,
        name: str | None = None,
        currency: str | None = None,
        notes: str | None = None,
        active: bool | None = None,
        address: dict | None = None,
    ) -> dict:
        """Update an existing vendor's mutable fields.

        Same semantics as ``update_customer``. See
        ``_update_business_person`` for address-merge details.

        Args:
            vendor_id: Human-readable ID (e.g., '000001').
            name: New display name. ``None`` = no change.
            currency: New default ISO currency code. ``None`` = no change.
            notes: New notes. Pass empty string to clear. ``None`` = no change.
            active: ``False`` to deactivate, ``True`` to reactivate.
            address: Partial address dict.

        Returns:
            Dict with guid, id, status, and changed-field diff.

        Raises:
            ValueError: If vendor not found, currency unknown, or
                no fields supplied.
        """
        with self.open(readonly=False) as book:
            vendor = self._find_vendor(book, vendor_id)
            if not vendor:
                raise ValueError(f"Vendor not found: {vendor_id}")
            return self._update_business_person(
                book=book, entity=vendor, entity_label="Vendor",
                name=name, currency=currency, notes=notes,
                active=active, address=address,
            )

    def update_employee(
        self,
        employee_id: str,
        name: str | None = None,
        currency: str | None = None,
        active: bool | None = None,
        address: dict | None = None,
    ) -> dict:
        """Update an existing employee's mutable fields.

        Employee has no ``notes`` column (unlike Customer / Vendor)
        — the parameter is omitted from the signature accordingly.
        Otherwise identical to ``update_customer``.

        Args:
            employee_id: Human-readable ID (e.g., '000001').
            name: New display name. ``None`` = no change.
            currency: New default ISO currency code. ``None`` = no change.
            active: ``False`` to deactivate, ``True`` to reactivate.
            address: Partial address dict.

        Returns:
            Dict with guid, id, status, and changed-field diff.

        Raises:
            ValueError: If employee not found, currency unknown, or
                no fields supplied.
        """
        with self.open(readonly=False) as book:
            employee = self._find_employee(book, employee_id)
            if not employee:
                raise ValueError(f"Employee not found: {employee_id}")
            return self._update_business_person(
                book=book, entity=employee, entity_label="Employee",
                name=name, currency=currency, notes=None,
                active=active, address=address,
            )

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
            currency: ISO currency code; defaults to the owner's
                currency (set when the customer/vendor was created),
                falling back to the book's default if the owner has
                no currency set.
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
                # Resolution order when currency isn't passed explicitly:
                #   1. Owner's currency (customer/vendor) — every
                #      business document inherits the trading
                #      relationship's currency by default. A USD
                #      vendor's bill should be USD, not the book's
                #      default. This matches GnuCash desktop UI
                #      behavior and the bookkeeper's mental model.
                #   2. Book default — fallback for owners that have
                #      no currency set (shouldn't happen with piecash
                #      owners, but defensive).
                #
                # Pre-fix this fallback used book default unconditionally,
                # which broke cross-currency posting for any book with
                # foreign customers/vendors: bills against a USD vendor
                # on a CNY book got created in CNY, then ``post_invoice``
                # saw inv.currency == account.commodity and skipped the
                # rate-conversion path entirely. $500 was then booked
                # as ¥500.
                owner_currency = getattr(owner, "currency", None)
                if owner_currency is not None:
                    currency_guid = owner_currency.guid
                else:
                    currency_guid = self._require_default_currency(
                        book,
                    ).guid

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
            currency: ISO currency code. Defaults to the customer's
                currency, falling back to the book's default. Pass
                explicitly to override (rare — most invoices are in
                the customer's currency).
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
            currency: ISO currency code. Defaults to the vendor's
                currency, falling back to the book's default. Pass
                explicitly to override (rare — most bills are in the
                vendor's currency).
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

        ot = self._parse_owner_type(owner_type)
        with self.open() as book:
            query = book.session.query(Invoice)

            if ot is not None:
                query = query.filter(Invoice.owner_type == ot)

            invoices = query.order_by(Invoice.date_opened.desc()).all()

            if status == "posted":
                invoices = [i for i in invoices if _is_invoice_posted(i)]
            elif status == "open":
                invoices = [i for i in invoices if not _is_invoice_posted(i)]

            total = len(invoices)
            invoices, notice = _apply_limit(
                invoices,
                limit=limit,
                entity_name="invoices",
                suggest_narrow=True,
            )

            if compact:
                lines = [self._invoice_to_compact_line(book, i) for i in invoices]
                if notice:
                    lines.append(notice)
                return "\n".join(lines)
            else:
                # Verbose path: resolve owner per invoice so each dict
                # carries the readable name. ``owner_guid`` was dropped
                # from the dict shape in Phase 3C.
                results = []
                for i in invoices:
                    if i.owner_type == 4:
                        o = self._find_vendor_by_guid(book, i.owner_guid)
                    else:
                        o = self._find_customer_by_guid(book, i.owner_guid)
                    results.append(
                        self._invoice_to_dict(
                            i, owner_name=o.name if o else None,
                        )
                    )
                # Envelope shape matches ``get_unreconciled_splits`` and
                # ``get_prices`` so verbose-mode callers see truncation
                # signal in the response. The bookkeeper noticed verbose
                # was the odd one out: compact got the [Showing N of M]
                # notice appended, but the dict version had no count /
                # total / notice fields at all. ``count`` = truncated
                # length, ``total`` = full filter set size, ``notice``
                # is the same string compact appends (or None).
                return {
                    "invoices": results,
                    "count": len(results),
                    "total": total,
                    "notice": notice,
                }

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

        ot = self._parse_owner_type(owner_type)

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

            # Phase 3C: build a guid → fullname map once (covers every
            # account referenced by entries on this invoice), then
            # thread it through ``_entry_to_dict`` so each entry shows
            # ``account: "Income:LLC Revenue"`` instead of an opaque
            # 32-char hex GUID. One query, ``account_paths`` shared
            # across all entries.
            account_paths: dict[str, str] = {}
            for a in book.accounts:
                account_paths[a.guid] = a.fullname

            entries = [
                self._entry_to_dict(
                    r, is_bill=is_bill, account_paths=account_paths,
                )
                for r in rows
            ]

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

            result = self._invoice_to_dict(
                inv, entries=entries, owner_name=owner_name,
            )
            result["total"] = str(total)
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

        ot = self._parse_owner_type(owner_type)

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
                entry_acct = book.session.query(
                    piecash.Account
                ).filter_by(guid=acct_guid).first()
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

    def unpost_invoice(
        self,
        invoice_id: str,
        owner_type: str | None = None,
    ) -> dict:
        """Unpost a previously-posted invoice or bill.

        Reverses ``post_invoice`` cleanly: deletes the posting
        transaction and its splits, removes the posting lot, and
        clears the invoice's posted-state metadata
        (``date_posted``, ``post_txn``, ``post_lot``,
        ``post_account``). The invoice returns to "open" state and
        is editable again — entries can be added/changed and the
        invoice can be re-posted.

        Refuses if the invoice has any payments applied (the lot
        contains splits beyond the original A/R posting). Unposting
        a partially-paid invoice would orphan the payment splits
        and corrupt the lot's balance accounting; the caller must
        void payments first.

        Args:
            invoice_id: Human-readable ID (e.g., '000001').
            owner_type: 'customer' or 'vendor' for cross-sequence
                ID disambiguation. Required when the same numeric
                ID exists for both an invoice and a bill.

        Returns:
            ``{"id": "000015", "type": "invoice", "status": "unposted"}``.

        Raises:
            ValueError: If the invoice/bill isn't found, isn't posted,
                or has payments applied.
        """
        ot = self._parse_owner_type(owner_type)

        with self.open(readonly=False) as book:
            inv = self._find_invoice(book, invoice_id, owner_type=ot)
            if not inv:
                raise ValueError(
                    f"Invoice/bill not found: {invoice_id}"
                )

            if not _is_invoice_posted(inv):
                raise ValueError(
                    f"Invoice {invoice_id} is not posted"
                )

            is_bill = inv.owner_type == 4
            doc_label = "Bill" if is_bill else "Invoice"

            # Capture before-state for the audit log: the user wants
            # to see what they unposted ("was posted: 2026-04-01,
            # post_account: Assets:Receivables:..."). Read pre-clear
            # so the values are still set on the ORM object.
            prev_post_date = _safe_date_posted(inv)
            prev_post_account = (
                inv.post_account.fullname if inv.post_account else None
            )
            self._stage_audit_before({
                "id": inv.id,
                "type": "bill" if is_bill else "invoice",
                "date_posted": (
                    str(prev_post_date.date()) if prev_post_date else None
                ),
                "post_account": prev_post_account,
            })

            txn = inv.post_txn
            lot = inv.post_lot

            # Reject when there are *live* (non-voided) payment splits
            # in the lot. The lot starts with one split — the A/R
            # debit/credit from the posting transaction. Each
            # ``pay_invoice`` call adds one more split. A voided
            # transaction in GnuCash preserves its splits with zeroed
            # values for audit-trail purposes, so a naive
            # ``len(lot.splits) > 1`` check counts voided payments as
            # still-applied — which contradicts GnuCash's void
            # semantics ("voided" means "neutralize the economic
            # effect, preserve the record"). Filter to splits that
            # are (a) not part of the posting transaction itself and
            # (b) carry non-zero value.
            posting_txn_guid = txn.guid if txn else None
            real_payment_splits = []
            if lot is not None:
                for s in lot.splits:
                    if (
                        posting_txn_guid is not None
                        and s.transaction_guid == posting_txn_guid
                    ):
                        continue
                    if Decimal(str(s.value)) == 0:
                        continue
                    real_payment_splits.append(s)
            if real_payment_splits:
                raise ValueError(
                    f"{doc_label} {invoice_id} has payments applied. "
                    f"Void payments first, then unpost."
                )

            # Clear the invoice's posted-state pointers BEFORE
            # deleting the underlying transaction/lot. Otherwise the
            # ORM cascade may resurrect the references via the
            # relationships and trip on dangling FKs at flush.
            inv.date_posted = None
            inv.post_txn = None
            inv.post_lot = None
            inv.post_account = None
            book.flush()

            # Delete the posting transaction (which cascades its
            # splits) and the lot (now empty after the inv.post_lot
            # = None nullified the only split-lot link).
            if txn is not None:
                book.session.delete(txn)
            if lot is not None:
                book.session.delete(lot)

            book.save()

            return {
                "id": inv.id,
                "type": "bill" if is_bill else "invoice",
                "status": "unposted",
            }

    def pay_invoice(
        self,
        invoice_id: str,
        payment_account: str,
        amount: str,
        payment_date: str | None = None,
        description: str | None = None,
        owner_type: str | None = None,
        fx_account: str | None = None,
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

        For cross-currency payments where the rate moved between
        post-date and pay-date, a realized FX gain/loss split is
        booked to an income/expense account. Routing rules (highest
        priority first):

        1. ``fx_account`` if supplied — must exist, must be
           INCOME or EXPENSE.
        2. Otherwise, the unique INCOME/EXPENSE account whose leaf
           name matches one of "fx", "forex", "foreign exchange",
           "currency gain/loss", "exchange gain/loss", "currency
           translation".
        3. Otherwise (zero matches OR multiple matches) the canonical
           ``Income:Foreign Exchange Gain/Loss`` account, auto-
           created if not present. If there were multiple
           candidates, the result includes an ``fx_notice`` field
           listing them so the caller can pass ``fx_account``
           explicitly next time.

        Args:
            invoice_id: Human-readable ID (e.g., '000001').
            payment_account: Bank or cash account path.
            amount: Payment amount in the invoice currency (e.g., '500.00').
            payment_date: ISO date (YYYY-MM-DD). Defaults to today.
            description: Description for the payment transaction.
            owner_type: 'customer' or 'vendor' for disambiguation.
            fx_account: Optional account to receive any realized FX
                gain/loss (cross-currency payments only). Accepts a
                full path, ``%short`` GUID, or full 32-char GUID.
                Must be an INCOME or EXPENSE account.

        Returns:
            Dict with payment details and remaining balance. For cross-
            currency payments also includes ``exchange_rate`` and
            ``payment_account_amount``.

        Raises:
            ValueError: If invoice not found, not posted, invalid account,
                cross-currency payment with no exchange rate available,
                or ``fx_account`` is supplied but invalid.
        """
        ot = self._parse_owner_type(owner_type)

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

            post_acc_guid = inv.post_acc_guid
            post_acct = book.session.query(
                piecash.Account
            ).filter_by(guid=post_acc_guid).first()
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
            fx_notice = None
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
                        fx_acct, fx_notice = self._get_or_create_fx_account(
                            book, fx_account=fx_account,
                        )
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
                    if fx_notice is not None:
                        result["fx_notice"] = fx_notice

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
        compact: bool = True,
    ) -> list[dict] | str:
        """Get all posted invoices/bills with outstanding balances.

        Args:
            owner_type: Filter by 'customer' or 'vendor'. Omit for all.
            customer_id: Filter by specific customer ID.
            vendor_id: Filter by specific vendor ID.
            compact: If True (default), return a compact one-line-per-doc
                     string with action columns: due date, days past due,
                     currency, BILL tag, owner. Verbose mode returns the
                     structured list dicts kept for ``pay_invoice``
                     workflows.

        Returns:
            If compact: newline-separated lines, each
                ``"id  owner  CCY amount  posted:YYYY-MM-DD  due:YYYY-MM-DD  N days past due"``,
                ordered most-overdue-first. Bills tagged ``(BILL)``.
                Empty string when nothing is outstanding.
            If not compact: list of dicts (caller pays the wire cost
                in exchange for the full ``original_amount`` /
                ``amount_paid`` / ``amount_due`` breakdown).
        """
        from piecash.business.invoice import Invoice

        today = date.today()
        ot = self._parse_owner_type(owner_type)

        with self.open() as book:
            query = book.session.query(Invoice).filter(
                Invoice.date_posted.isnot(None)
            )

            if ot is not None:
                query = query.filter(Invoice.owner_type == ot)

            if customer_id:
                customer = self._find_customer(book, customer_id)
                if not customer:
                    raise ValueError(
                        f"Customer not found: {customer_id}"
                    )
                query = query.filter(
                    Invoice.owner_guid == customer.guid
                )

            if vendor_id:
                vendor = self._find_vendor(book, vendor_id)
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
                post_acct = book.session.query(
                    piecash.Account
                ).filter_by(guid=post_acc_guid).first()
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
                # Resolve the due date through the same three-step
                # chain the warnings collector uses, so the bookkeeper
                # sees identical numbers in both places.
                due_date, no_terms = self._resolve_invoice_due_date(
                    book, inv,
                )
                days_past_due = (
                    (today - due_date).days
                    if due_date is not None
                    else None
                )
                currency = (
                    inv.currency.mnemonic if inv.currency else None
                )
                results.append({
                    "id": inv.id,
                    "type": "bill" if is_bill else "invoice",
                    "owner_name": owner_name,
                    "currency": currency,
                    "date_posted": (
                        str(posted_dt.date()) if posted_dt else None
                    ),
                    "due_date": (
                        str(due_date) if due_date is not None else None
                    ),
                    "days_past_due": days_past_due,
                    "no_terms": no_terms,
                    "original_amount": str(grand_total),
                    "amount_paid": str(amount_paid),
                    "amount_due": str(abs(balance)),
                })

            # Sort: most overdue first (largest days_past_due), so the
            # bookkeeper sees the urgent receivables / bills at the top.
            results.sort(
                key=lambda r: -(r["days_past_due"] or 0),
            )

            if compact:
                return _format_outstanding_invoices_compact(results)
            return results

    def vendor_spending_report(
        self,
        start_date: str,
        end_date: str,
        vendor_id: str | None = None,
        compact: bool = True,
    ) -> dict | str:
        """Get spending breakdown by vendor for a period.

        Analyzes posted vendor bills to show total billed, paid,
        and outstanding amounts per vendor.

        Args:
            start_date: Start of period (YYYY-MM-DD).
            end_date: End of period (YYYY-MM-DD).
            vendor_id: Optional filter to specific vendor.
            compact: If True (default), return an aligned text table
                     suitable for direct LLM consumption (Phase 4D).
                     Verbose mode returns the structured dict.

        Returns:
            If compact: text table (one line per vendor + TOTAL).
            If not compact: dict with vendor breakdown and grand totals.
            The Phase 4D spec dropped the ``period`` echo (it duplicated
            input the caller already has); verbose mode no longer
            includes it either.
        """
        from piecash.business.invoice import Invoice

        parsed_start = date.fromisoformat(start_date)
        parsed_end = date.fromisoformat(end_date)

        with self.open() as book:
            # Capture default currency for the compact formatter —
            # pre-fix the table emitted ``$`` regardless of book
            # setting.
            default_currency_mnemonic = (
                self._require_default_currency(book).mnemonic
            )

            query = book.session.query(Invoice).filter(
                Invoice.owner_type == 4,
                Invoice.date_posted.isnot(None),
            )

            if vendor_id:
                vendor = self._find_vendor(book, vendor_id)
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
                post_acct = book.session.query(
                    piecash.Account
                ).filter_by(guid=bill.post_acc_guid).first()
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

        full = {
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

        if not compact:
            return full

        return _format_vendor_spending_compact(
            vendors_list,
            grand_billed=grand_billed,
            grand_paid=grand_paid,
            grand_outstanding=grand_outstanding,
            currency=default_currency_mnemonic,
        )
