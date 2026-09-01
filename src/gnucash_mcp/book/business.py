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

import logging
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import piecash

from gnucash_mcp.book._base import (
    _commodity_quantum,
    _slot_value_str,
    _to_decimal,
    _unique_prefix,
    _verify_composite_write,
    _verify_write,
)
from gnucash_mcp._format import (
    _GROUP_BY_VALUES,
    _enumerate_periods,
    _format_grouped_tsv,
    _paginate,
    _partial_period_labels,
    _period_label,
)


debug_logger = logging.getLogger("gnucash_mcp.debug")


class _PlannedAccount:
    """Stand-in for an account a rehearsal WOULD create.

    The FX/discount resolvers auto-create their canonical account on
    first use; a ``pay_invoice`` dry run must not (no piecash objects
    during dry_run — the batch precedent). Carries exactly what the
    rehearsal path reads; ``planned`` lets the response mark it.
    """

    placeholder = False
    planned = True

    def __init__(self, fullname: str, commodity, type: str):
        self.fullname = fullname
        self.commodity = commodity
        self.type = type


def _safe_invoice_date(inv, attr: str):
    """Read an invoice datetime column defensively.

    Returns the datetime when the named column holds a real value;
    ``None`` when it's NULL, empty, or malformed enough that
    piecash's ``_DateTime`` TypeDecorator raises ``ValueError``.

    The piecash hazard: a freshly auto-id'd bill's ``date_posted``
    can come back as ``''`` in SQL, and SQLAlchemy's regex-based
    DATETIME parser hard-crashes reading it. Same failure mode for
    ``date_opened`` and any other ``_DateTime`` column; one helper,
    parameterized on the attribute name, covers every caller.
    """
    try:
        value = getattr(inv, attr)
        return value if value else None
    except (ValueError, TypeError):
        return None


def _is_invoice_posted(inv) -> bool:
    """True iff ``inv`` has a real datetime in ``date_posted``.

    The single "is this document posted?" chokepoint. Built on
    ``_safe_invoice_date``, so None / "" / unparseable all read
    as not-posted.
    """
    return _safe_invoice_date(inv, "date_posted") is not None


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

    Currency prefix is the book's default; the amount columns are
    width-aligned across rows.
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
    """Render outstanding invoices/bills/credit-notes as a
    one-line-per-doc string.

    Format per row:

        000028  Berlin Digital GmbH  EUR 4,200  posted:2026-02-01  due:2026-03-03  55 days past due

    ``(BILL)`` tags vendor bills so receivables and payables read
    apart at a glance. ``(CN)`` marks credit notes — their amount is
    unsettled credit available to apply or refund, not money owed,
    so the due column reads "credit available" instead of an aging
    count. When the due date came from the 30-day default
    (``no_terms``), the days count says "past 30-day default" so it
    doesn't read as contractual.
    """
    if not rows:
        return ""
    lines = []
    for r in rows:
        owner = r.get("owner_name") or f"#{r['id']}"
        is_credit_note = r.get("is_credit_note", False)
        # (CN) wins over (BILL): the credit-note tag changes how
        # the whole amount column reads.
        if is_credit_note:
            owner = f"{owner} (CN)"
        elif r.get("type") == "bill":
            owner = f"{owner} (BILL)"
        # Job annotation appends after any (CN)/(BILL) tag:
        # ``Customer (CN) (job:JOB-001)``.
        job_id = r.get("job_id")
        if job_id:
            owner = f"{owner} (job:{job_id})"
        ccy = r.get("currency") or ""
        # Strip trailing zeros for compact display: "4200.00" → "4,200".
        amount_dec = Decimal(r.get("amount_due") or "0")
        amount_str = f"{int(amount_dec):,}" if amount_dec == int(amount_dec) else f"{amount_dec:,.2f}"
        posted = r.get("date_posted") or "?"
        # Credit notes have no due date — they sit as available
        # credit until applied or refunded.
        if is_credit_note:
            action_str = "  credit available"
            lines.append(
                f"{r['id']}\t{owner}\t{ccy} {amount_str}\t"
                f"posted:{posted}{action_str}"
            )
            continue
        # Overpaid doc: the counterparty holds a credit, so the
        # aging-clock columns would invite double-collection.
        # Surface the direction explicitly instead.
        if r.get("overpaid"):
            lines.append(
                f"{r['id']}\t{owner}\t{ccy} {amount_str}\t"
                f"posted:{posted}  OVERPAID — credit balance"
            )
            continue
        due = r.get("due_date") or "?"
        days = r.get("days_past_due")
        if days is None:
            days_str = ""
        elif days > 0:
            # "past due" reads as contractual; "past 30-day default"
            # anchors to the assumption when no term was set.
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

        Indexed ``filter_by`` query, not a ``book.customers`` scan —
        a real hot-path cost in workflows that look up the same
        customer repeatedly.
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

    @staticmethod
    def _find_employee_by_guid(book, guid: str):
        """Find an employee by GUID (indexed)."""
        from piecash.business.person import Employee
        return book.session.query(Employee).filter_by(guid=guid).first()

    @staticmethod
    def _find_invoice_owner_by_guid(book, owner_type: int, guid: str):
        """Find any invoice/bill/voucher owner by their (type, guid)
        pair. ``invoices.owner_guid`` is a generic FK — the row
        points at the customer/vendor/employee/job table depending
        on ``owner_type``; this helper picks the right table.

        For owner_type=3 (Job), chases through the Job to the
        underlying customer/vendor — the counterparty a bookkeeper
        cares about. Callers that need the Job itself should use
        ``_find_job_by_guid``.
        """
        if owner_type == 2:
            return BusinessMixin._find_customer_by_guid(book, guid)
        if owner_type == 4:
            return BusinessMixin._find_vendor_by_guid(book, guid)
        if owner_type == 5:
            return BusinessMixin._find_employee_by_guid(book, guid)
        if owner_type == 3:
            job = BusinessMixin._find_job_by_guid(book, guid)
            if job is None:
                return None
            return BusinessMixin._find_invoice_owner_by_guid(
                book, job.owner_type, job.owner_guid,
            )
        return None

    # Path for the auto-created realized-FX-gain/loss income account.
    # Single credit-natural account: positive balance = net gain, negative
    # = net loss across the period. Named to match GAAP convention
    # ("Foreign Exchange Gain (Loss)") while staying one account for
    # simplicity.
    FX_GAIN_LOSS_PATH = "Income:Foreign Exchange Gain/Loss"

    # Substrings that identify a user-named FX gain/loss account on
    # the leaf-name match. Books in the wild use many conventions
    # ("FX Gain Loss", "Currency Translation", …). Bare ``currency``
    # is excluded deliberately — accounts like "Foreign Currency
    # Cash" aren't gain/loss accounts.
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

    # ── Early-payment discount account convention ────────────────────
    #
    # Standard small-business GAAP routing: a discount given to a
    # customer is an expense (the cost of getting paid faster); a
    # discount taken on a supplier bill is income (a cost reduction).
    # Larger businesses sometimes prefer contra-revenue / contra-
    # expense treatment — the ``discount_account`` parameter on
    # pay_invoice overrides the default routing.
    SALES_DISCOUNTS_PATH = "Expenses:Sales Discounts"
    PURCHASE_DISCOUNTS_PATH = "Income:Purchase Discounts Taken"

    _SALES_DISCOUNT_NAME_KEYWORDS = (
        "sales discount",
        "sales discounts",
        "discounts given",
        "customer discount",
        "customer discounts",
    )
    _PURCHASE_DISCOUNT_NAME_KEYWORDS = (
        "purchase discount",
        "purchase discounts",
        "discounts taken",
        "vendor discount",
        "vendor discounts",
    )

    # ── Designated-account KVP slots (Layer 0 of the resolvers) ──────
    #
    # Once the FX / discount account is first resolved or created, its
    # GUID is stored in a book-global slot on the root account. Every
    # later resolution reads that GUID directly (Layer 0) — immune to
    # both account renames and the book's locale, because the name no
    # longer participates once the slot is populated. A stale or missing
    # slot falls through to the lower layers, which re-populate it on the
    # next create, so the designation self-heals.
    #
    # Path-style ``gnc-mcp/<role>`` keys (hierarchical sub-slots in the
    # KVP store) per the slot-naming convention. One account per role,
    # each denominated in the book default currency, so a single slot on
    # the root account suffices — no per-source-account/per-commodity
    # path is needed.
    _FX_ACCOUNT_SLOT_KEY = "gnc-mcp/fx-gain-loss-acct"
    _SALES_DISCOUNT_SLOT_KEY = "gnc-mcp/sales-discount-acct"
    _PURCHASE_DISCOUNT_SLOT_KEY = "gnc-mcp/purchase-discount-acct"

    def _resolve_designated_account(self, book, slot_key):
        """Layer 0: resolve a designated FX/discount account from its
        root-account KVP slot.

        Returns the Account iff the slot holds a GUID that still
        resolves to an INCOME/EXPENSE account in the book default
        currency. A missing, empty, or stale slot (the account was
        deleted, retyped, or re-denominated) returns ``None`` — the
        caller falls through to the lower layers and re-writes the slot
        on the next create, so the designation self-heals.

        GUID-based, hence locale- and rename-proof: the account can be
        renamed or re-parented freely after first use and still resolve.
        """
        try:
            raw = book.root_account[slot_key]
        except KeyError:
            return None
        guid = _slot_value_str(raw)
        if not guid:
            return None
        acct = self._resolve_account(book, guid)
        if acct is None:
            return None
        if acct.type not in {"INCOME", "EXPENSE"}:
            return None
        if acct.commodity != self._require_default_currency(book):
            return None
        # A placeholder can't receive postings (GnuCash's UI treats
        # them as non-postable organizers); a designation that became
        # a placeholder falls through and re-resolves.
        if acct.placeholder:
            return None
        return acct

    def _store_designated_account(self, book, slot_key, account) -> None:
        """Persist ``account``'s GUID to a root-account KVP slot so
        future resolutions hit Layer 0 (a direct, locale-/rename-proof
        GUID lookup) instead of re-deriving the account by name.

        Called once the FX/discount account is definitively resolved
        (found at its canonical path or freshly created). piecash
        assigns ``guid`` at flush, not construction, and we cannot
        flush mid-payment-build (orphan splits would trip a NOT NULL on
        ``splits.tx_guid``); so a freshly created account is still
        guid-less here. Assign the canonical guid now — piecash uses it
        at INSERT — and the slot persists with the caller's final
        ``book.save()``.
        """
        if account.guid is None:
            import uuid
            account.guid = uuid.uuid4().hex
        book.root_account[slot_key] = account.guid

    def _get_or_create_fx_account(
        self, book, fx_account: str | None = None,
        dry_run: bool = False,
    ):
        """Find or lazily create the FX-gain/loss account.

        Returns ``(account, notice)``; ``notice`` is ``None`` except
        when the fuzzy match found multiple candidates and fell back
        to the canonical default.

        ``dry_run=True`` resolves without side effects: no account
        construction (a would-be create returns a
        ``_PlannedAccount``) and no designation-slot writes (the
        self-heal happens on the real run instead).

        Resolution order:

        0. **Stored designation** — a GUID written to the
           ``gnc-mcp/fx-gain-loss-acct`` root slot on a prior resolve/
           create. Consulted only when no explicit ``fx_account`` is
           given; GUID-based, so it is immune to a rename of the FX
           account and to the book's locale. The primary path after
           first use.
        1. **Explicit ``fx_account``** (path, ``%short``, or full
           GUID) — validated for existence and INCOME/EXPENSE type.
           A per-call override; wins over the stored designation and
           is not itself persisted.
        2. **Fuzzy match**, leaf-name substring against
           ``_FX_NAME_KEYWORDS`` over INCOME/EXPENSE accounts:
           exactly one match → use it; zero → fall through; more
           than one → fall through *and* return a notice asking the
           caller to pass ``fx_account``. Don't guess between
           user-created accounts.
        3. **Canonical default**: existing ``Income:Foreign Exchange
           Gain/Loss``, else auto-create it under the top-level INCOME
           account resolved *by type* — locale-robust, so it works on
           a book whose income root is "Erträge"/"Ingresos"/… (and
           survives a user rename), instead of throwing on a missing
           English "Income". Falls back to creating a top-level INCOME
           account only if the book has none at all.

        Raises:
            ValueError: ``fx_account`` supplied but missing, not
                INCOME/EXPENSE, or denominated in a non-default
                currency.
        """
        default_currency = self._require_default_currency(book)

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
            # Realized FX gain/loss is a reporting-currency figure;
            # the gain is booked as a default-currency quantity. A
            # non-default-commodity FX account would record it in the
            # wrong commodity (a $42 gain becoming €42). Reject rather
            # than silently corrupt.
            if acct.commodity != default_currency:
                raise ValueError(
                    f"fx_account {acct.fullname!r} is denominated in "
                    f"{acct.commodity.mnemonic}; the realized FX "
                    f"gain/loss account must be in the book default "
                    f"currency ({default_currency.mnemonic})."
                )
            if acct.placeholder:
                raise ValueError(
                    f"fx_account {acct.fullname!r} is a placeholder; "
                    f"placeholders organize the tree and cannot "
                    f"receive postings. Pass a leaf INCOME/EXPENSE "
                    f"account."
                )
            return acct, None

        # Layer 0: a stored designation wins for the no-explicit-arg
        # case (the usual pay_invoice path). GUID-based, so it survives
        # a rename of the FX account and works on any locale.
        slotted = self._resolve_designated_account(
            book, self._FX_ACCOUNT_SLOT_KEY
        )
        if slotted is not None:
            return slotted, None

        # Layer 2: fuzzy match by leaf-name substring, plus exact
        # match against the leaf THIS BOOK's machinery generates (the
        # book's inferred-locale translation and the English default)
        # — a stale slot on a German book would otherwise miss
        # "Realisierter Gewinn/Verlust" and collide at Layer 3.
        # Deliberately NOT all 47 shipped localizations: an English
        # book where the user happens to have a German-named account
        # is a coincidence, not this machinery re-finding its own
        # name, and must not be silently adopted — let alone
        # permanently designated. Only default-currency,
        # non-placeholder candidates qualify — see the commodity
        # rationale on the explicit-account path above.
        own_fx_leaf = self._locale_account_name(
            "fx_gain_loss", "Foreign Exchange Gain/Loss",
            self._infer_book_locale(book),
        )
        own_fx_leaves = {
            own_fx_leaf.lower(),
            "foreign exchange gain/loss",
        }
        template_guids = self._template_account_guids(book)
        candidates = []
        for account in book.accounts:
            if account.guid in template_guids:
                continue
            if account.type not in {"INCOME", "EXPENSE"}:
                continue
            if account.commodity != default_currency:
                continue
            if account.placeholder:
                continue
            name_lower = account.name.lower()
            if (
                name_lower in own_fx_leaves
                or any(kw in name_lower for kw in self._FX_NAME_KEYWORDS)
            ):
                candidates.append(account)

        if len(candidates) == 1:
            only = candidates[0]
            # A fuzzy keyword match stays per-call (don't permanently
            # designate a guess), but an EXACT match on the leaf this
            # book's machinery generates is the resolver re-finding
            # its own account — designate it so the next call hits
            # Layer 0 and the healed slot survives renames.
            if only.name.lower() in own_fx_leaves and not dry_run:
                self._store_designated_account(
                    book, self._FX_ACCOUNT_SLOT_KEY, only
                )
            return only, None

        # Multi-candidate ambiguity: fall through to the canonical
        # default and report where the split actually went. The
        # message is built AFTER resolution — on a localized book the
        # destination is the localized leaf under the type-resolved
        # income root, not the English canonical path.
        ambiguous_paths = (
            sorted(c.fullname for c in candidates)
            if len(candidates) > 1 else None
        )

        def _ambiguity_notice(destination: str) -> dict | None:
            if ambiguous_paths is None:
                return None
            return {
                "type": "ambiguous_fx_account",
                "candidates": ambiguous_paths,
                "message": (
                    f"Found {len(ambiguous_paths)} candidate FX "
                    f"accounts ({', '.join(ambiguous_paths)}). Routed "
                    f"to {destination} as the default; pass fx_account "
                    f"explicitly to disambiguate."
                ),
            }

        # Layer 3: canonical default (existing or auto-create). An
        # existing canonical account is adopted only if it passes the
        # same gates Layers 0 and 1 enforce — adopting (and slotting)
        # e.g. a EUR-denominated account here would book a
        # default-currency delta as EUR (silent corruption) and store
        # a designation Layer 0 rejects on every later call.
        fx_acct = self._find_account(book, self.FX_GAIN_LOSS_PATH)
        if (
            fx_acct is not None
            and fx_acct.type in {"INCOME", "EXPENSE"}
            and fx_acct.commodity == default_currency
            and not fx_acct.placeholder
        ):
            # Lock in the designation so the next call hits Layer 0
            # directly — even on a book that already had this account.
            if not dry_run:
                self._store_designated_account(
                    book, self._FX_ACCOUNT_SLOT_KEY, fx_acct
                )
            return fx_acct, _ambiguity_notice(fx_acct.fullname)

        # Resolve the parent by TYPE, not the English name "Income".
        # On a localized book the income root is "Erträge"/"Ingresos"/…,
        # so _find_account(book, "Income") returns None and the old
        # code threw here — the first cross-currency payment failing.
        # Fall back to creating a top-level INCOME account only if the
        # book genuinely has none.
        income, parent_notice = self._top_level_account_of_type(
            book, "INCOME"
        )
        income_fullname = income.fullname if income is not None else "Income"
        if income is None and not dry_run:
            income = piecash.Account(
                name="Income",
                type="INCOME",
                parent=book.root_account,
                commodity=default_currency,
                placeholder=1,
            )

        # Localized leaf (§6.3) — already resolved for the Layer-2
        # exact-match set above. Cosmetic only: the slot write below
        # makes the next resolve GUID-based, so the localized name
        # never has to be matched again.
        fx_leaf = own_fx_leaf

        # Sibling-collision check BEFORE constructing: if the intended
        # leaf already exists under this parent, adopt it when it
        # passes the gates — otherwise raise a clear error instead of
        # letting book.save() fail with piecash's opaque "two children
        # with the same name". That failure never persisted anything,
        # so the slot never self-healed and every retry failed
        # identically — a permanently wedged cross-currency pay path.
        existing = next(
            (c for c in income.children if c.name == fx_leaf), None
        ) if income is not None else None
        if existing is not None:
            if (
                existing.type in {"INCOME", "EXPENSE"}
                and existing.commodity == default_currency
                and not existing.placeholder
                and existing.guid not in template_guids
            ):
                if not dry_run:
                    self._store_designated_account(
                        book, self._FX_ACCOUNT_SLOT_KEY, existing
                    )
                adopted_notice = _ambiguity_notice(existing.fullname)
                return existing, (
                    adopted_notice if adopted_notice is not None
                    else parent_notice
                )
            raise ValueError(
                f"An account named {fx_leaf!r} already exists under "
                f"{income.name!r} but cannot receive realized FX "
                f"gain/loss (type {existing.type}, denominated "
                f"{existing.commodity.mnemonic}"
                f"{', placeholder' if existing.placeholder else ''}; "
                f"needs a non-placeholder INCOME/EXPENSE account in "
                f"the book default currency "
                f"{default_currency.mnemonic}). Pass fx_account "
                f"explicitly to choose a different account."
            )

        # Rehearsal: report the planned account instead of creating.
        if dry_run:
            planned = _PlannedAccount(
                f"{income_fullname}:{fx_leaf}",
                default_currency, "INCOME",
            )
            notice = _ambiguity_notice(planned.fullname)
            return planned, notice if notice is not None else parent_notice

        # Construct — piecash.Account auto-adds to the session via the
        # parent linkage. Don't flush here: the caller is still building
        # the payment transaction, and orphan Split objects already in
        # the session would trip a NOT NULL on splits.tx_guid. The
        # final book.save() at the end of pay_invoice flushes everything
        # together with their now-set tx_guids.
        fx_acct = piecash.Account(
            name=fx_leaf,
            type="INCOME",
            parent=income,
            commodity=default_currency,
            description=(
                "Realized gains and losses from cross-currency "
                "invoice settlements (rate drift between post-date "
                "and pay-date). Auto-created on first use."
            ),
        )
        self._store_designated_account(
            book, self._FX_ACCOUNT_SLOT_KEY, fx_acct
        )
        notice = _ambiguity_notice(fx_acct.fullname)
        return fx_acct, notice if notice is not None else parent_notice

    def _get_or_create_discount_account(
        self, book, owner_type_is_bill: bool,
        discount_account: str | None = None,
        dry_run: bool = False,
    ):
        """Find or lazily create the early-payment-discount account.

        Direct parallel to ``_get_or_create_fx_account`` — same
        four-layer resolution (stored designation > explicit > fuzzy
        match > canonical default with auto-create), the same
        ``(account, notice)`` return shape, and the same
        ``dry_run`` contract (no creates, no slot writes; a would-be
        create returns a ``_PlannedAccount``). The Layer-0 slot is
        per-side, so the sales and purchase designations are
        independent.

        ``owner_type_is_bill`` selects the side: True → vendor-bill
        payment (or customer credit-note refund), discount is INCOME
        (Purchase Discounts Taken); False → customer invoice
        payment, discount is EXPENSE (Sales Discounts).
        """
        if owner_type_is_bill:
            canonical_path = self.PURCHASE_DISCOUNTS_PATH
            canonical_type = "INCOME"
            canonical_parent_path = "Income"
            keywords = self._PURCHASE_DISCOUNT_NAME_KEYWORDS
            side_label = "purchase discounts taken"
            slot_key = self._PURCHASE_DISCOUNT_SLOT_KEY
            concept = "purchase_discount"
        else:
            canonical_path = self.SALES_DISCOUNTS_PATH
            canonical_type = "EXPENSE"
            canonical_parent_path = "Expenses"
            keywords = self._SALES_DISCOUNT_NAME_KEYWORDS
            side_label = "sales discounts"
            slot_key = self._SALES_DISCOUNT_SLOT_KEY
            concept = "sales_discount"

        # Layer 1: caller-supplied account wins.
        if discount_account is not None:
            acct = self._resolve_account(book, discount_account)
            if acct is None:
                raise ValueError(
                    f"discount_account not found: {discount_account!r}. "
                    f"Pass a full path, %short GUID, or full 32-char "
                    f"GUID of an INCOME or EXPENSE account."
                )
            if acct.type not in {"INCOME", "EXPENSE"}:
                raise ValueError(
                    f"discount_account {acct.fullname!r} is type "
                    f"{acct.type}; must be INCOME or EXPENSE to "
                    f"receive an early-payment-discount split."
                )
            if acct.placeholder:
                raise ValueError(
                    f"discount_account {acct.fullname!r} is a "
                    f"placeholder; placeholders organize the tree "
                    f"and cannot receive postings. Pass a leaf "
                    f"INCOME/EXPENSE account."
                )
            return acct, None

        # Layer 0: a stored designation wins for the no-explicit-arg
        # case. Per-side slot, GUID-based — rename- and locale-proof.
        slotted = self._resolve_designated_account(book, slot_key)
        if slotted is not None:
            return slotted, None

        # Layer 2: fuzzy match by leaf-name substring. Placeholders
        # are excluded — they can't receive postings.
        template_guids = self._template_account_guids(book)
        candidates = []
        for account in book.accounts:
            if account.guid in template_guids:
                continue
            if account.type not in {"INCOME", "EXPENSE"}:
                continue
            if account.placeholder:
                continue
            name_lower = account.name.lower()
            if any(kw in name_lower for kw in keywords):
                candidates.append(account)

        if len(candidates) == 1:
            return candidates[0], None

        # Multi-candidate ambiguity: fall through to the canonical
        # default; the message names the ACTUAL destination, built
        # after resolution (parallel to the FX resolver).
        ambiguous_paths = (
            sorted(c.fullname for c in candidates)
            if len(candidates) > 1 else None
        )

        def _ambiguity_notice(destination: str) -> dict | None:
            if ambiguous_paths is None:
                return None
            return {
                "type": "ambiguous_discount_account",
                "candidates": ambiguous_paths,
                "message": (
                    f"Found {len(ambiguous_paths)} candidate "
                    f"{side_label} accounts "
                    f"({', '.join(ambiguous_paths)}). Routed to "
                    f"{destination} as the default; pass "
                    f"discount_account explicitly to disambiguate."
                ),
            }

        # Layer 3: canonical default (existing or auto-create). Adopt
        # only a type-valid account — the discount side deliberately
        # has no commodity gate (the discount split converts via
        # _convert), so type is the one invariant to enforce.
        disc_acct = self._find_account(book, canonical_path)
        if (
            disc_acct is not None
            and disc_acct.type in {"INCOME", "EXPENSE"}
            and not disc_acct.placeholder
        ):
            if not dry_run:
                self._store_designated_account(book, slot_key, disc_acct)
            return disc_acct, _ambiguity_notice(disc_acct.fullname)

        # Battery ruling 4(b): past this point the resolver CREATES.
        # On a non-English-locale book, silently creating the English
        # default misstates the ledger twice over — an English leaf
        # in a localized chart, and (on the sales side) an EXPENSE
        # account where the textbook treatment is contra-revenue.
        # Refuse with the fix in hand; existing accounts remain
        # adoptable through the explicit/slot/fuzzy/canonical layers
        # above. Ruling 4(a) — resolve the default by ROLE — is the
        # destination that lifts this refusal.
        locale = self._infer_book_locale(book)
        if locale is not None and locale != "en":
            raise ValueError(
                f"No {side_label} account is designated on this "
                f"book, and auto-creating the English default "
                f"({canonical_path!r}) on a book whose chart reads "
                f"as locale {locale!r} would misstate the ledger. "
                f"Pass discount_account with the account that "
                f"should absorb the discount (path, %short GUID, "
                f"or full 32-char GUID of a non-placeholder INCOME "
                f"or EXPENSE account)."
            )

        # Resolve the parent by TYPE (INCOME/EXPENSE), not the English
        # name "Income"/"Expenses" — locale-robust and rename-proof.
        # Fall back to creating the top-level account only if the book
        # has none of that type.
        parent, parent_notice = self._top_level_account_of_type(
            book, canonical_type
        )
        default_currency = self._require_default_currency(book)
        parent_fullname = (
            parent.fullname if parent is not None
            else canonical_parent_path
        )
        if parent is None and not dry_run:
            parent = piecash.Account(
                name=canonical_parent_path,
                type=canonical_type,
                parent=book.root_account,
                commodity=default_currency,
                placeholder=1,
            )

        # Don't flush here; the caller is still building the payment
        # transaction. Same rationale as the FX-account auto-create.
        # Localized leaf on a non-English book (§6.3); falls back to the
        # English canonical leaf when no translation exists for the
        # concept (the discount concepts have none shipped yet).
        leaf_name = self._locale_account_name(
            concept, canonical_path.split(":")[-1],
            self._infer_book_locale(book),
        )

        # Sibling-collision check BEFORE constructing — same wedge as
        # the FX resolver: an unadoptable same-named child would make
        # book.save() fail with piecash's opaque duplicate-children
        # error on every retry. Matters today for an English book
        # whose canonical account exists with a non-INCOME/EXPENSE
        # type, and future-proofs the day discount leaf translations
        # ship.
        existing = next(
            (c for c in parent.children if c.name == leaf_name), None
        ) if parent is not None else None
        if existing is not None:
            if (
                existing.type in {"INCOME", "EXPENSE"}
                and not existing.placeholder
                and existing.guid not in template_guids
            ):
                if not dry_run:
                    self._store_designated_account(
                        book, slot_key, existing
                    )
                adopted_notice = _ambiguity_notice(existing.fullname)
                return existing, (
                    adopted_notice if adopted_notice is not None
                    else parent_notice
                )
            raise ValueError(
                f"An account named {leaf_name!r} already exists under "
                f"{parent.name!r} but is "
                f"{'a placeholder' if existing.placeholder else f'type {existing.type}'}; "
                f"the {side_label} account must be a non-placeholder "
                f"INCOME or EXPENSE account. Pass discount_account "
                f"explicitly to choose a different account."
            )
        # Rehearsal: report the planned account instead of creating.
        if dry_run:
            planned = _PlannedAccount(
                f"{parent_fullname}:{leaf_name}",
                default_currency, canonical_type,
            )
            notice = _ambiguity_notice(planned.fullname)
            return planned, (
                notice if notice is not None else parent_notice
            )

        disc_acct = piecash.Account(
            name=leaf_name,
            type=canonical_type,
            parent=parent,
            commodity=default_currency,
            description=(
                f"Early-payment discounts {'taken on supplier bills' if owner_type_is_bill else 'given to customers'}, "
                f"booked when pay_document is called with "
                f"apply_discount=True and the term + window + amount "
                f"validation passes. Auto-created on first use."
            ),
        )
        self._store_designated_account(book, slot_key, disc_acct)
        notice = _ambiguity_notice(disc_acct.fullname)
        return disc_acct, notice if notice is not None else parent_notice

    def _get_invoice_billterm(self, book, inv):
        """Return the piecash Billterm linked to the invoice, or None.

        Reads the ``terms`` column via raw SQL — the ORM
        ``inv.terms`` relationship is reliable only through specific
        access paths (same pattern as ``_resolve_invoice_due_date``).
        """
        from sqlalchemy import text
        from piecash.business.invoice import Billterm

        terms_row = book.session.execute(
            text("SELECT terms FROM invoices WHERE guid = :guid"),
            {"guid": inv.guid},
        ).first()
        term_guid = terms_row[0] if terms_row else None
        if not term_guid:
            return None
        return (
            book.session.query(Billterm)
            .filter_by(guid=term_guid)
            .first()
        )

    def _compute_discount_summary(self, book, inv) -> dict | None:
        """Return discount summary for an invoice, or None when not
        applicable.

        Surfaced by ``get_invoice`` verbose mode and referenced by
        ``pay_invoice`` validation. Pure read. None when the invoice
        isn't posted, has no billterm, or the billterm has no
        discount configured.

        Returned dict shape::

            {
                "discount_days": int,
                "discount_percent": Decimal,
                "expected_discount": Decimal,  # in invoice currency
                "eligible_until": date,
                "currency": str,
            }

        ``expected_discount`` is computed off the pre-tax
        ``subtotal`` deliberately: tax is collected on behalf of the
        authority at the gross rate; discounting it would short the
        remittance.
        """
        if not _is_invoice_posted(inv):
            return None
        bt = self._get_invoice_billterm(book, inv)
        if bt is None:
            return None
        discount_days = int(bt.discountdays) if bt.discountdays else 0
        discount_pct = Decimal(str(bt.discount)) if bt.discount else Decimal("0")
        if discount_days <= 0 or discount_pct <= 0:
            return None

        # Use date_opened as the discount-window anchor — that's the
        # invoice issuance date (what's printed on the invoice the
        # customer received). date_posted is when it hit A/R, often
        # the same day but not guaranteed; the customer's discount
        # eligibility is measured from when they got the invoice.
        anchor = inv.date_opened
        if isinstance(anchor, datetime):
            anchor = anchor.date()
        eligible_until = anchor + timedelta(days=discount_days)

        # Pre-tax principal for discount calculation. Catch the rare
        # corrupted-entries case the same way other callers do.
        try:
            totals = self._get_invoice_entries_and_total(book, inv)
            subtotal = totals["subtotal"]
        except (ValueError, KeyError):
            return None

        expected = (subtotal * discount_pct / Decimal(100)).quantize(
            _commodity_quantum(inv.currency)
        )

        return {
            "discount_days": discount_days,
            "discount_percent": discount_pct,
            "expected_discount": expected,
            "eligible_until": eligible_until,
            "currency": inv.currency.mnemonic,
        }

    @staticmethod
    def _rate_from_post_transaction(post_txn, target_commodity):
        """Derive the exchange rate from post_txn currency to
        ``target_commodity`` from the post transaction's own splits:
        the ratio |quantity| / |value| on a split whose account
        commodity matches the target is the rate in force at posting.

        The explicit target matters: taking the FIRST
        non-invoice-currency split would, in a multi-cross-currency
        post (EUR invoice with USD income + GBP A/R), silently pick
        whichever split comes first.

        Returns the Decimal rate, or None if no matching split is
        present (caller falls back to the price table at post-date).
        """
        for s in post_txn.splits:
            if s.account.commodity != target_commodity:
                continue
            s_value = abs(Decimal(str(s.value)))
            s_quantity = abs(Decimal(str(s.quantity)))
            if s_value > 0:
                return s_quantity / s_value
        return None

    def _compute_fx_gain_loss(
        self,
        book,
        *,
        inv,
        is_bill: bool,
        pay_acct,
        payment_amount: Decimal,
        pay_quantity: Decimal,
        parsed_date: date,
        exchange_rate: Decimal,
        fx_account: str | None,
        default_currency,
        discount_amount: Decimal = Decimal("0"),
        discount_quantity: Decimal | None = None,
        discount_commodity=None,
        dry_run: bool = False,
    ) -> dict | None:
        """Compute the realized FX gain/loss for a cross-currency payment.

        ``dry_run=True`` computes identically but constructs no Split
        (``"split"`` is None) and resolves the FX account without
        side effects; ``"quantity"`` / ``"memo"`` carry what the
        split would hold, for rehearsal rendering.

        When ``pay_invoice`` settles a foreign-currency invoice and
        the rate moved between post-date and pay-date, the amount
        actually received/spent differs from what was recorded at
        posting. This helper computes that delta, converts it to the
        book default currency, and prepares the split
        ``pay_invoice`` appends to the payment transaction.

        Caller invariants: ``exchange_rate`` is non-None (the
        cross-currency path is already taken); ``pay_quantity`` and
        ``payment_amount`` are already quantized; ``inv.post_txn``
        exists.

        The discount leg (``discount_amount`` / ``discount_quantity``
        / ``discount_commodity``) carries the same drift as the
        payment leg — ``discount × (pay_rate − post_rate)`` is
        realized FX too; leaving it unbooked puts assets ≠ equity by
        that amount on any cross-currency discount settlement.

        Returns:
            ``None`` when no FX split should be booked: no
            rate-at-post available (post txn had no matching split
            and no price on the post date — the payment still
            records; the delta just isn't surfaced), or the delta is
            below the default currency's smallest unit. Otherwise::

                {
                    "split": piecash.Split,     # ready to append
                    "fx_diff_default": Decimal, # signed, default ccy
                    "fx_acct": piecash.Account,
                    "fx_notice": str | None,    # multi-candidate fallback
                }

            Sign convention on ``fx_diff_default``: positive means
            the pay-side commodity received more units than expected
            at the post-date rate — a gain for customer invoices, a
            loss for vendor bills. The Split's ``quantity`` sign
            flips so the account is credited (gain) or debited
            (loss) conventionally.
        """
        # Rate at posting: prefer the post transaction's own splits
        # (if the price table was re-quoted after posting, realized
        # gain must still use the original posting rate); fall back
        # to the price table at post-date when the post txn has no
        # split in ``pay_acct.commodity`` (third-currency pay account).
        rate_at_post = self._rate_from_post_transaction(
            inv.post_txn, pay_acct.commodity,
        )
        if rate_at_post is None:
            post_date_obj = inv.post_txn.post_date
            if hasattr(post_date_obj, "date") and callable(
                post_date_obj.date
            ):
                post_date_obj = post_date_obj.date()
            rate_at_post = self._find_exchange_rate(
                book,
                from_commodity=inv.currency,
                to_commodity=pay_acct.commodity,
                as_of=post_date_obj,
            )
        if rate_at_post is None:
            return None

        # ``expected_at_post`` is in pay-account commodity (we'll
        # subtract pay_quantity from it). Quantize to that commodity's
        # smallest fraction.
        expected_at_post = (
            payment_amount * rate_at_post
        ).quantize(_commodity_quantum(pay_acct.commodity))
        fx_diff_pay = pay_quantity - expected_at_post

        # Convert to book default before booking: the FX account's
        # commodity is the default, so a quantity in any other
        # commodity (book=USD, invoice=EUR, pay=GBP: a £5 gain)
        # would render as "$5" — silently wrong.
        if pay_acct.commodity != default_currency:
            pay_to_default_rate = self._find_exchange_rate(
                book,
                from_commodity=pay_acct.commodity,
                to_commodity=default_currency,
                as_of=parsed_date,
            )
            if pay_to_default_rate is None:
                # Missing third-currency rate: skip the FX booking
                # gracefully (mirror the rate_at_post branch above).
                # Raising here would block the entire payment over
                # an unsurfaced gain/loss split — worse than
                # omitting it.
                return None
            fx_diff_default = (
                fx_diff_pay * pay_to_default_rate
            ).quantize(_commodity_quantum(default_currency))
        else:
            fx_diff_default = fx_diff_pay

        # Discount leg: booked at the pay-date rate while the A/R it
        # relieved was carried at the post-date rate — the difference
        # is realized FX. Skipped when the discount is in the invoice
        # currency (no drift) or a needed rate is missing.
        if (
            discount_amount > 0
            and discount_quantity is not None
            and discount_commodity is not None
            and discount_commodity != inv.currency
        ):
            rate_at_post_disc = self._rate_from_post_transaction(
                inv.post_txn, discount_commodity,
            )
            if rate_at_post_disc is None:
                post_date_obj = inv.post_txn.post_date
                if hasattr(post_date_obj, "date") and callable(
                    post_date_obj.date
                ):
                    post_date_obj = post_date_obj.date()
                rate_at_post_disc = self._find_exchange_rate(
                    book,
                    from_commodity=inv.currency,
                    to_commodity=discount_commodity,
                    as_of=post_date_obj,
                )
            if rate_at_post_disc is not None:
                expected_disc_at_post = (
                    discount_amount * rate_at_post_disc
                ).quantize(_commodity_quantum(discount_commodity))
                diff_disc = discount_quantity - expected_disc_at_post
                if discount_commodity != default_currency:
                    disc_to_default = self._find_exchange_rate(
                        book,
                        from_commodity=discount_commodity,
                        to_commodity=default_currency,
                        as_of=parsed_date,
                    )
                    if disc_to_default is not None:
                        fx_diff_default += (
                            diff_disc * disc_to_default
                        ).quantize(_commodity_quantum(default_currency))
                else:
                    fx_diff_default += diff_disc

        # Skip booking the FX split when the realized delta is below
        # the smallest representable unit in the FX account's
        # commodity (e.g., $0.01 for USD, ¥0 for JPY).
        if abs(fx_diff_default) < _commodity_quantum(default_currency):
            return None

        fx_acct, fx_notice = self._get_or_create_fx_account(
            book, fx_account=fx_account, dry_run=dry_run,
        )

        # Customer (is_bill=False): received more → gain → credit
        # income (quantity = -fx_diff for gain, +|fx_diff| for loss).
        # Vendor bill (is_bill=True): spent more → loss → debit income
        # (quantity = +fx_diff for loss, -|fx_diff| for gain).
        quantity_sign = 1 if is_bill else -1
        is_loss = (
            (is_bill and fx_diff_default > 0)
            or (not is_bill and fx_diff_default < 0)
        )
        fx_quantity = quantity_sign * fx_diff_default
        fx_memo = (
            f"FX {'loss' if is_loss else 'gain'} on invoice "
            f"{inv.id}: post-rate {rate_at_post:.4f}, pay-rate "
            f"{exchange_rate}"
        )
        split = None
        if not dry_run:
            split = piecash.Split(
                account=fx_acct,
                value=Decimal("0"),
                quantity=fx_quantity,
                memo=fx_memo,
                action="Payment",
            )
        return {
            "split": split,
            "fx_diff_default": fx_diff_default,
            "fx_acct": fx_acct,
            "fx_notice": fx_notice,
            "quantity": fx_quantity,
            "memo": fx_memo,
        }

    @staticmethod
    def _find_employee(book, employee_id: str):
        """Find an employee by their human-readable ID (e.g., '000001')."""
        from piecash.business.person import Employee
        return book.session.query(Employee).filter_by(id=employee_id).first()

    @staticmethod
    def _find_job(book, job_id: str):
        """Find a job by its human-readable ID (e.g., '000001').

        Unlike invoices, job IDs are unambiguous across owner types
        — a single ``counter_job`` advances for customer and vendor
        jobs alike.
        """
        from piecash.business.invoice import Job
        return book.session.query(Job).filter_by(id=job_id).first()

    @staticmethod
    def _find_taxtable(book, name: str):
        """Find a Taxtable by exact name.

        Taxtable names are unique book-wide — no per-jurisdiction
        namespace. Returns None if not found; callers raise.
        """
        from piecash.business.tax import Taxtable
        return book.session.query(Taxtable).filter_by(name=name).first()

    @staticmethod
    def _find_taxtable_by_guid(book, guid: str):
        """Find a Taxtable by full GUID. Caller passes a 32-char hex
        string (resolved via :meth:`_resolve_guid` upstream when the
        input was a short prefix). Returns None if not found.
        """
        from piecash.business.tax import Taxtable
        return book.session.query(Taxtable).filter_by(guid=guid).first()

    @staticmethod
    def _find_job_by_guid(book, guid: str):
        """Find a job by GUID. Used to resolve invoice→job links
        where the invoice's ``owner_guid`` (with owner_type=3)
        points at the job."""
        from piecash.business.invoice import Job
        return book.session.query(Job).filter_by(guid=guid).first()

    @staticmethod
    def _parse_owner_type(owner_type: str | None) -> int | None:
        """Map an owner_type string to its piecash integer code.

        ``None`` → ``None`` (no filter); ``"customer"`` → 2;
        ``"vendor"`` → 4; ``"employee"`` → 5 (the third counterparty
        type in piecash's polymorphic invoice table). Anything else
        raises ``ValueError`` naming the valid options.
        """
        if owner_type is None:
            return None
        if owner_type == "customer":
            return 2
        if owner_type == "vendor":
            return 4
        if owner_type == "employee":
            return 5
        raise ValueError(
            f"Invalid owner_type {owner_type!r}. "
            f"Must be 'customer', 'vendor', or 'employee'."
        )

    @staticmethod
    def _find_invoice(book, invoice_id: str, owner_type: int | None = None):
        """Find an invoice/bill by human-readable ID.

        Self-heals malformed ``date_posted=''`` values to NULL
        before the ORM query runs: piecash's ``_DateTime``
        TypeDecorator hard-crashes loading a row whose
        ``date_posted`` is an empty string (a state some persistence
        paths leave on auto-id'd bills), blocking every subsequent
        invoice operation. piecash exposes no readonly flag, so the
        heal is always attempted and the try/except absorbs the
        failure on readonly sessions — one write operation heals the
        book permanently.

        Args:
            book: piecash Book instance.
            invoice_id: Human-readable ID (e.g., '000001').
            owner_type: Filter by owner type (2=customer, 4=vendor).
                        None requires the ID to be unambiguous —
                        see Raises.

        Raises:
            ValueError: When ``owner_type=None`` and the ID matches
                multiple documents. Customer and vendor sequences
                share one ``invoices`` table, so collisions are
                normal; returning whichever row surfaces first would
                silently route writes to the wrong document. The
                error lists candidates so the caller can pass
                ``owner_type``.
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
            # An owner_type=2/4 lookup must match BOTH direct
            # documents AND job-attached ones (owner_type=3 whose
            # Job carries the requested owner_type) — without the
            # job-GUID subquery, every owner_type-filtered tool
            # fails "not found" on job-attached invoices. Employee
            # (owner_type=5) is exempt: piecash jobs are
            # customer/vendor only.
            from sqlalchemy import and_, or_
            from piecash.business.invoice import Job
            if owner_type in (2, 4):
                job_guid_sq = book.session.query(Job.guid).filter(
                    Job.owner_type == owner_type
                ).subquery()
                return query.filter(or_(
                    Invoice.owner_type == owner_type,
                    and_(
                        Invoice.owner_type == 3,
                        Invoice.owner_guid.in_(job_guid_sq),
                    ),
                )).first()
            return query.filter(Invoice.owner_type == owner_type).first()

        # owner_type=None: pull all matches and fail loud on
        # collision rather than returning a row-order-dependent pick.
        matches = query.all()
        if len(matches) <= 1:
            return matches[0] if matches else None

        candidates = []
        # Collisions can include vouchers — label all three types.
        _COLLISION_LABELS = {
            2: "customer invoice",
            4: "vendor bill",
            5: "employee voucher",
        }
        for m in matches:
            label = _COLLISION_LABELS.get(
                m.owner_type, f"unknown owner_type={m.owner_type}",
            )
            currency = m.currency.mnemonic if m.currency else "?"
            candidates.append(f"{label} (currency={currency})")
        raise ValueError(
            f"Found {len(matches)} documents with ID {invoice_id!r}: "
            f"{', '.join(candidates)}. Pass owner_type='customer', "
            f"'vendor', or 'employee' to disambiguate."
        )

    @staticmethod
    def _address_to_dict(entity) -> dict:
        """Build an address dict from a piecash Customer/Vendor,
        omitting empty fields and dropping ``fax`` entirely.

        Returns {} when no address data is set — caller should drop
        the whole "address" key rather than emit an empty dict.

        ``fax`` is write-only by design: the create/update paths
        accept and store it, but it never round-trips back through
        this serializer.
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
        """Convert a piecash Customer to a serializable dict.

        Business-object GUIDs are omitted from responses: every
        consumer addresses these entities by human-readable ``id``,
        so the 32-char GUID is dead weight. The same rule covers
        vendor, employee, job, billterm, taxtable, invoice, and
        entry response shapes.
        """
        result = {
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
        """Convert a piecash Vendor to a serializable dict.

        ``guid`` omitted — see _customer_to_dict for the rationale.
        """
        result = {
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

        Employee's schema has no ``notes`` column (unlike Customer/
        Vendor), so the response omits the key. Employee-specific
        fields (``acl``/``language``/``workday``/``rate``) are not
        serialized. ``guid`` omitted — see _customer_to_dict.
        """
        result = {
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
    def _job_to_dict(job, owner_name: str | None = None) -> dict:
        """Convert a piecash Job to a serializable dict.

        The stored ``owner_type`` (2/4) is surfaced as the
        human-readable string for symmetry with other owner-typed
        responses. ``owner_name`` is resolved by the caller (a
        staticmethod can't query the session). ``guid`` omitted.
        """
        return {
            "id": job.id,
            "name": job.name,
            "reference": job.reference or "",
            "active": bool(job.active),
            "owner_type": (
                "customer" if job.owner_type == 2 else "vendor"
            ),
            "owner_name": owner_name,
        }

    @staticmethod
    def _job_to_compact_line(job, owner_name: str | None = None) -> str:
        """One-line compact: ``id  name  CUSTOMER/VENDOR  owner_name``.

        The owner tag is what a bookkeeper scans for — customer jobs
        are revenue-side, vendor jobs cost-side. Active state isn't
        shown; list_jobs filters by active_only upstream.
        """
        owner_tag = (
            "CUSTOMER" if job.owner_type == 2 else "VENDOR"
        )
        owner_str = owner_name or "?"
        ref_part = f"  ref:{job.reference}" if job.reference else ""
        return (
            f"{job.id}\t{job.name}\t{owner_tag}\t{owner_str}{ref_part}"
        )

    @staticmethod
    def _billterm_to_dict(bt) -> dict:
        """Convert a Billterm to a serializable dict.

        ``guid`` omitted — billterms are addressed by ``name``.
        """
        return {
            "name": bt.name,
            "description": bt.description or "",
            "type": bt.type,
            "due_days": bt.duedays,
            "discount_days": bt.discountdays if bt.discountdays else 0,
            "discount": str(bt.discount) if bt.discount else "0",
        }

    @staticmethod
    def _taxtable_entry_to_dict(
        tte,
        account_paths: dict | None = None,
    ) -> dict:
        """Serialize a TaxtableEntry to a dict.

        When ``account_paths`` (``{account_guid: fullname}``) is
        given, the entry's ``account`` field is the resolved path;
        otherwise the raw GUID appears as ``account_guid``. Same
        pattern as ``_entry_to_dict``.
        """
        result = {
            "id": tte.id,
            "type": tte.type,
            "amount": str(tte.amount),
        }
        if account_paths is not None:
            result["account"] = account_paths.get(
                tte.account_guid, tte.account_guid,
            )
        else:
            result["account_guid"] = tte.account_guid
        return result

    @staticmethod
    def _taxtable_to_dict(
        tt,
        account_paths: dict | None = None,
        refcount: int | None = None,
    ) -> dict:
        """Serialize a Taxtable to a dict.

        ``account_paths`` is pre-built by the caller (one query
        rather than N). ``refcount``, when given, replaces the
        stored ``Taxtable.refcount`` column in the response — the
        stored value tracks GnuCash desktop's bookkeeping; the
        SQL-computed value is authoritative for lifecycle checks.
        """
        entries = [
            BusinessMixin._taxtable_entry_to_dict(
                e, account_paths=account_paths,
            )
            for e in tt.entries
        ]
        # ``guid`` omitted — taxtables are addressed by ``name``.
        return {
            "name": tt.name,
            "refcount": (
                refcount if refcount is not None else tt.refcount
            ),
            "entries": entries,
        }

    @staticmethod
    def _decimal_to_num_denom(value: Decimal) -> tuple[int, int]:
        """Convert a Decimal to numerator/denominator pair.

        E.g., ``Decimal("25.50")`` → ``(2550, 100)``.

        Normalizes through ``Decimal(str(value))`` first: a
        scientific-notation Decimal (``1.5E-3``) reports the
        scientific exponent in ``as_tuple().exp``, not the printed
        decimal places. Unreachable from current call sites, but
        the normalization keeps the helper safe in isolation.
        """
        value = Decimal(str(value))
        sign, digits, exp = value.as_tuple()
        if exp < 0:
            denom = 10 ** (-exp)
            num = int(value * denom)
        else:
            num = int(value)
            denom = 1
        return num, denom

    @staticmethod
    def _invoice_to_dict(
        invoice,
        entries=None,
        owner_name: str | None = None,
        applies_to: dict | None = None,
        job: dict | None = None,
        tax_summary: dict | None = None,
    ) -> dict:
        """Convert a piecash Invoice to a serializable dict.

        Dropped fields: numeric ``owner_type`` (redundant with
        ``type``), ``is_posted`` (derivable from ``date_posted``),
        ``owner_guid`` (replaced by caller-resolved ``owner_name``),
        ``guid``.

        Conditional keys — emitted only when set, so normal
        documents keep their original shape: ``is_credit_note`` /
        ``applies_to`` (credit notes), ``job`` (job-attached
        invoices), ``entries``, ``tax_summary``.

        For job-attached invoices the ``type`` field stays semantic
        (invoice/bill per the job's underlying customer/vendor); the
        ``job`` kwarg carries the caller's resolution (a staticmethod
        can't query the session), and ``owner_name`` is the
        underlying counterparty via ``_find_invoice_owner_by_guid``.
        """
        if invoice.owner_type == 3 and job is not None:
            # job['owner_type'] is the string form from the caller.
            type_field = (
                "invoice" if job.get("owner_type") == "customer"
                else "bill"
            )
        else:
            type_field = BusinessMixin._OWNER_TYPE_TO_RESPONSE_TYPE.get(
                invoice.owner_type, "invoice",
            )

        # ``guid`` omitted — invoices are addressed by ``id``.
        result = {
            "id": invoice.id,
            "type": type_field,
            "owner_name": owner_name,
            "date_opened": (
                str(_safe_invoice_date(invoice, "date_opened").date())
                if _safe_invoice_date(invoice, "date_opened") else None
            ),
            "date_posted": (
                str(_safe_invoice_date(invoice, "date_posted").date())
                if _safe_invoice_date(invoice, "date_posted") else None
            ),
            "notes": invoice.notes or "",
            "active": bool(invoice.active),
            "currency": invoice.currency.mnemonic if invoice.currency else None,
        }
        # ``applies_to`` further requires the link to be set —
        # floating credit notes are unusual but valid.
        if BusinessMixin._get_is_credit_note(invoice):
            # ``type`` must agree with the delete/unpost responses,
            # which already report credit notes as their own type —
            # owner_type alone can't see the slot.
            result["type"] = "credit_note"
            result["is_credit_note"] = True
            if applies_to:
                result["applies_to"] = applies_to
        if job is not None:
            result["job"] = {"id": job["id"], "name": job["name"]}
        if entries is not None:
            result["entries"] = entries
        if tax_summary is not None:
            result["tax_summary"] = tax_summary
        return result

    def _invoice_to_compact_line(self, book, invoice) -> str:
        """One-line compact format with action columns:

            ``id  TYPE  owner_name  CCY total  date_opened  status``

        Owner and total are what make a hundred-invoice list
        scannable; currency keeps multi-currency books unambiguous.
        """
        # Type tag: INV/BILL/VCHR per owner_type, "(CN)" suffix for
        # credit notes (greppable, and preserves the owner-side info
        # a bare CN tag would hide). Job-attached invoices tag by
        # the underlying side — one Job lookup via
        # ``_resolve_owner_type_and_job`` feeds both the tag and the
        # job annotation.
        effective_ot, job = self._resolve_owner_type_and_job(
            book, invoice,
        )
        inv_type = self._OWNER_TYPE_TO_COMPACT_TAG.get(
            effective_ot, "INV"
        )
        if self._get_is_credit_note(invoice):
            inv_type = f"{inv_type} (CN)"
        opened = _safe_invoice_date(invoice, "date_opened")
        date_str = (
            str(opened.date()) if opened else "n/a"
        )
        status = "posted" if _is_invoice_posted(invoice) else "open"

        # ``_find_invoice_owner_by_guid`` dispatches on owner_type,
        # chasing owner_type=3 through the Job to the counterparty.
        owner = self._find_invoice_owner_by_guid(
            book, invoice.owner_type, invoice.owner_guid,
        )
        owner_name = owner.name if owner else "?"

        # Job ID (not name) keeps the owner column narrow; the
        # name surfaces in get_job if needed.
        if job is not None:
            owner_name = f"{owner_name} (job:{job.id})"

        # Total: sum of (quantity * price) across entries. Falls back
        # to "?" when entries can't be loaded — keeps the row legible
        # even on data-corruption edge cases.
        try:
            grand_total = self._get_invoice_entries_and_total(
                book, invoice,
            )["grand_total"]
            ccy = (
                invoice.currency.mnemonic
                if invoice.currency else ""
            )
            if grand_total == int(grand_total):
                amount_str = f"{ccy} {int(grand_total):,}".strip()
            else:
                amount_str = f"{ccy} {grand_total:,.2f}".strip()
        except (ValueError, AttributeError, TypeError):
            # Limited to the predictable shapes "?" is right for —
            # a bare ``except Exception`` would swallow programming
            # errors (KeyError/NameError) silently too.
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
        taxtable_names: dict | None = None,
    ) -> dict:
        """Convert an entry row (from raw SQL) to a serializable dict.

        ``is_bill`` selects the ``b_*`` vs ``i_*`` column group.
        ``account_paths`` / ``taxtable_names`` are caller-built
        GUID→name maps (one query per invoice, not per entry); when
        present the response carries readable names, otherwise raw
        GUIDs. Tax fields are emitted only when the entry is
        taxable, so non-tax entries keep their original shape.
        """
        q_num = entry_row.quantity_num or 0
        q_denom = entry_row.quantity_denom or 1
        quantity = Decimal(q_num) / Decimal(q_denom)

        if is_bill:
            p_num = entry_row.b_price_num or 0
            p_denom = entry_row.b_price_denom or 1
            acct_guid = entry_row.b_acct
            taxable = bool(entry_row.b_taxable)
            tax_included = bool(entry_row.b_taxincluded)
            taxtable_guid = entry_row.b_taxtable
        else:
            p_num = entry_row.i_price_num or 0
            p_denom = entry_row.i_price_denom or 1
            acct_guid = entry_row.i_acct
            taxable = bool(entry_row.i_taxable)
            tax_included = bool(entry_row.i_taxincluded)
            taxtable_guid = entry_row.i_taxtable

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

        # ``guid`` omitted — entries have no standalone tool surface.
        result = {
            "date": date_str,
            "description": entry_row.description or "",
            "quantity": str(quantity),
            "price": str(price),
            "total": str(total),
        }
        # Conditional keys — plain entries keep their shape.
        if entry_row.notes:
            result["notes"] = entry_row.notes
        if entry_row.action:
            result["action"] = entry_row.action

        if account_paths is not None and acct_guid:
            # Surface the readable path. Falls back to the
            # raw GUID when a stale entry references a deleted account.
            result["account"] = account_paths.get(
                acct_guid, acct_guid,
            )
        else:
            result["account_guid"] = acct_guid or ""

        if taxable:
            result["taxable"] = True
            result["tax_included"] = tax_included
            if taxtable_guid:
                if taxtable_names is not None:
                    result["taxtable"] = taxtable_names.get(
                        taxtable_guid, taxtable_guid,
                    )
                else:
                    result["taxtable_guid"] = taxtable_guid
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
        """Sum of split values in a lot, skipping voided splits.

        A/R lots: positive = outstanding receivable. A/P lots:
        negative = outstanding payable.

        Voided splits (``reconcile_state == 'v'``) are GnuCash's
        zombie audit-trail records — zeroed but preserved. Skipping
        them matches the void-aware treatment everywhere else;
        summing them would be right only by accident, and
        inconsistent with callers that count splits.
        """
        total = Decimal(0)
        for split in lot.splits:
            if split.reconcile_state == "v":
                continue
            total += Decimal(str(split.value))
        return total

    @staticmethod
    def _calculate_lot_quantity(lot) -> Decimal:
        """Sum of split QUANTITIES in a lot, skipping voided splits.

        Companion to :meth:`_calculate_lot_balance` (which sums
        invoice-currency ``value``). Quantities are in the post
        account's commodity — what the receivable/payable account
        actually carries, and what a settlement must relieve exactly.
        """
        total = Decimal(0)
        for split in lot.splits:
            if split.reconcile_state == "v":
                continue
            total += Decimal(str(split.quantity))
        return total

    @staticmethod
    def _resolve_invoice_due_date(
        book, inv,
    ) -> tuple[date | None, bool]:
        """Resolve an invoice/bill due date through three sources.

        Returns ``(due_date, no_terms_flag)``; ``due_date`` is None
        when the invoice isn't posted. ``no_terms_flag`` is True
        when the 30-day default was used, so callers annotate the
        rendering as approximated.

        Resolution order — first source that resolves wins:

        1. ``trans-date-due`` slot on the posting transaction
           (present when the user passed ``due_date`` explicitly).
        2. ``Invoice.terms`` billterm — ``duedays`` added to
           ``date_posted``. Read raw via SQL; the ORM relationship
           is unreliable through some access paths.
        3. 30-day default.

        Single chokepoint so the warnings collector and
        ``get_outstanding_invoices`` produce identical due-date math.
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
                # GnuCash GDATE columns return a compact ``YYYYMMDD``
                # string; other paths yield ISO ``YYYY-MM-DD``. Python
                # 3.11+ ``date.fromisoformat`` accepts both, but 3.10
                # (a supported target) rejects the compact form and
                # raises — which the warnings collector's broad
                # ``except`` then swallows, silently dropping the
                # overdue warning. Normalize to digits first.
                digits = gdate_val.strip().replace("-", "")[:8]
                if len(digits) == 8 and digits.isdigit():
                    return (
                        date(int(digits[:4]), int(digits[4:6]),
                             int(digits[6:8])),
                        False,
                    )
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
        """Query entries for an invoice/bill and compute totals,
        absorbing per-line tax math (via ``_compute_entry_tax``).

        Aggregates revenue/expense AND tax-payable amounts into one
        ``acct_totals`` dict so ``post_invoice`` emits one split per
        account without knowing about tax.

        Returns:
            Dict with keys:

            - ``rows``: raw SQL rows from the entries table.
            - ``acct_totals``: ``{account_guid: Decimal}`` covering
              revenue/expense AND tax-payable accounts together.
            - ``grand_total``: gross customer-facing total
              (includes tax).
            - ``subtotal``: sum of per-line pretax amounts.
            - ``tax_breakdown``: ``{account_guid: Decimal}`` —
              tax-only portion, for display surfaces.
            - ``tax_by_taxtable``: ``{taxtable_guid: Decimal}`` —
              tax by source taxtable; zero-tax taxtables absent.

        Raises:
            ValueError: when the invoice has no entries.
        """
        from sqlalchemy import text
        from piecash.business.tax import Taxtable

        is_bill = self._is_bill_side(self._effective_owner_type(book, inv))
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

        # Taxtable resolution is cached so an invoice with many
        # lines sharing the same taxtable hits SQL once per
        # distinct taxtable, not once per line.
        taxtable_cache: dict[str, list[dict]] = {}

        def _resolve_taxtable_entries(taxtable_guid):
            if not taxtable_guid:
                return []
            if taxtable_guid in taxtable_cache:
                return taxtable_cache[taxtable_guid]
            tt = book.session.query(Taxtable).filter_by(
                guid=taxtable_guid,
            ).first()
            if not tt:
                # Defensive: ``i_taxtable``/``b_taxtable`` points at
                # a missing row. Entry creation validates this; if
                # we land here the book is mid-corruption — treat
                # as no-tax rather than crash the math seam.
                taxtable_cache[taxtable_guid] = []
                return []
            resolved = [
                {
                    "type": e.type,
                    "amount": e.amount,
                    "account_guid": e.account_guid,
                }
                for e in tt.entries
            ]
            taxtable_cache[taxtable_guid] = resolved
            return resolved

        quantum = _commodity_quantum(inv.currency)
        acct_totals: dict[str, Decimal] = {}
        tax_breakdown: dict[str, Decimal] = {}
        tax_by_taxtable: dict[str, Decimal] = {}
        grand_total = Decimal(0)
        subtotal = Decimal(0)

        for row in rows:
            q_num = row.quantity_num or 0
            q_denom = row.quantity_denom or 1
            quantity = Decimal(q_num) / Decimal(q_denom)

            if is_bill:
                p_num = row.b_price_num or 0
                p_denom = row.b_price_denom or 1
                acct_guid = row.b_acct
                taxable = bool(row.b_taxable)
                tax_included = bool(row.b_taxincluded)
                taxtable_guid = row.b_taxtable
            else:
                p_num = row.i_price_num or 0
                p_denom = row.i_price_denom or 1
                acct_guid = row.i_acct
                taxable = bool(row.i_taxable)
                tax_included = bool(row.i_taxincluded)
                taxtable_guid = row.i_taxtable

            price = Decimal(p_num) / Decimal(p_denom)
            taxtable_entries = _resolve_taxtable_entries(taxtable_guid)

            tax_result = self._compute_entry_tax(
                quantity=quantity,
                price=price,
                taxable=taxable,
                tax_included=tax_included,
                taxtable_entries=taxtable_entries,
                quantum=quantum,
            )

            # Revenue/expense account gets the pretax portion.
            acct_totals[acct_guid] = (
                acct_totals.get(acct_guid, Decimal(0))
                + tax_result["pretax"]
            )
            # Tax-payable accounts get their per-entry components
            # (same-account composites already collapse upstream).
            for tax_acct, tax_amount in (
                tax_result["tax_by_acct"].items()
            ):
                acct_totals[tax_acct] = (
                    acct_totals.get(tax_acct, Decimal(0))
                    + tax_amount
                )
                tax_breakdown[tax_acct] = (
                    tax_breakdown.get(tax_acct, Decimal(0))
                    + tax_amount
                )

            # Per-taxtable rollup for display surfaces.
            if taxtable_guid and tax_result["tax_total"] != 0:
                tax_by_taxtable[taxtable_guid] = (
                    tax_by_taxtable.get(taxtable_guid, Decimal(0))
                    + tax_result["tax_total"]
                )

            grand_total += tax_result["gross"]
            subtotal += tax_result["pretax"]

        return {
            "rows": rows,
            "acct_totals": acct_totals,
            "grand_total": grand_total,
            "subtotal": subtotal,
            "tax_breakdown": tax_breakdown,
            "tax_by_taxtable": tax_by_taxtable,
        }

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

        Currency resolution (explicit mnemonic or book default),
        optional Address construction, entity instantiation, save,
        and the canonical create response — in one place.

        Class-specific fields pass through ``**extra_kwargs``
        unexamined: Customer and Vendor take ``notes=""``; Employee
        has no ``notes`` column and rejects the kwarg.

        Returns:
            ``{"id": ..., "name": ..., "currency": ..., "status": "created"}``
        """
        from piecash.business.person import Address

        # Cap free-text byte lengths up front.
        notes_kwarg = extra_kwargs.get("notes")
        self._validate_business_freetext(
            notes=notes_kwarg, address=address,
        )

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

        # Business-object ``guid`` is omitted from write
        # responses — ``id`` is the working handle.
        return {
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

    # Free-text caps on business-entity fields — same shape as the
    # caps on ``void_transaction(reason)`` and
    # ``set_account_slot(value)``. Notes can be paragraphs; address
    # sub-fields are one-liners; the bounds reflect that.
    _NOTES_MAX_BYTES = 4 * 1024
    _ADDRESS_FIELD_MAX_BYTES = 1024

    @classmethod
    def _validate_business_freetext(
        cls,
        *,
        notes: str | None = None,
        address: dict | None = None,
    ) -> None:
        """Validate notes / address sub-field byte lengths before
        they reach the ORM. UTF-8 byte length, not characters — the
        backing store is SQLite TEXT. Raises ValueError naming the
        offending field; empty/missing values pass through.
        """
        if notes is not None:
            byte_len = len(notes.encode("utf-8"))
            if byte_len > cls._NOTES_MAX_BYTES:
                raise ValueError(
                    f"notes exceeds {cls._NOTES_MAX_BYTES}-byte cap "
                    f"({byte_len} bytes supplied). Shorten the value "
                    f"and retry."
                )
        if address:
            for key in cls._ADDRESS_FIELDS:
                value = address.get(key)
                if value is None:
                    continue
                if not isinstance(value, str):
                    raise ValueError(
                        f"address.{key} must be a string (got "
                        f"{type(value).__name__})."
                    )
                byte_len = len(value.encode("utf-8"))
                if byte_len > cls._ADDRESS_FIELD_MAX_BYTES:
                    raise ValueError(
                        f"address.{key} exceeds "
                        f"{cls._ADDRESS_FIELD_MAX_BYTES}-byte cap "
                        f"({byte_len} bytes supplied). Shorten and "
                        f"retry."
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

        Mutates only the fields the caller supplies. Diff-style
        response: ``{id, status, ...changed_fields}`` — no full
        entity echo; address changes show only the sub-fields that
        actually changed.

        Address handling: ``address=None`` leaves the record
        untouched; ``address={...}`` merges the supplied sub-fields
        onto the existing record (creating one if absent). Missing
        sub-keys are unchanged; **to clear a sub-field, pass an
        empty string explicitly**. Unknown keys raise so a typo
        doesn't silently no-op.

        Other semantics: ``currency`` sets the default trading
        currency only (existing documents untouched); ``notes`` is
        Customer/Vendor only (Employee has no notes column);
        ``active=False`` archives without deleting.

        Raises:
            ValueError: Currency unknown, unknown address keys,
                notes on Employee, or no fields supplied.
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

        # Stage audit before-state — the decorator reads it from
        # the thread-local rather than reopening the book.
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
            # ``_get_or_create_currency`` auto-loads unseen ISO codes
            # (same convention as ``create_price``).
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
            # Cap notes byte length up front.
            self._validate_business_freetext(notes=notes)
            current_notes = entity.notes or ""
            if current_notes != notes:
                entity.notes = notes
                changed["notes"] = notes

        if active is not None and bool(active) != bool(entity.active):
            entity.active = bool(active)
            changed["active"] = bool(active)

        if address is not None:
            # Cap address-field byte lengths up front.
            self._validate_business_freetext(address=address)
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

        # ``guid`` omitted — see _create_business_person.
        return {
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
            Dict with id, name, currency, status.
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
        limit: int = 50,
        offset: int = 0,
    ) -> dict | str:
        """List all customers.

        Leads with a ``Showing X-Y of Z customers`` indicator; page
        with ``offset``.

        Args:
            active_only: If True, only return active customers.
            compact: If True, return compact one-line-per-customer string.
            limit: Page size (default 50, max 250). 0 = count only.
            offset: 0-indexed first row to return.

        Returns:
            Compact string or verbose envelope.
        """
        with self.open() as book:
            customers = sorted(book.customers, key=lambda c: c.name)
            if active_only:
                customers = [c for c in customers if c.active]

            page, indicator = _paginate(
                customers, offset=offset, limit=limit,
                entity_name="customers",
            )
            if compact:
                lines = [indicator]
                lines += [self._customer_to_compact_line(c) for c in page]
                return "\n".join(lines)
            else:
                return {
                    "showing": indicator,
                    "total": len(customers),
                    "offset": offset,
                    "count": len(page),
                    "customers": [self._customer_to_dict(c) for c in page],
                }

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
            Dict with id, name, currency, status.
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
        limit: int = 50,
        offset: int = 0,
    ) -> dict | str:
        """List all vendors.

        Leads with a ``Showing X-Y of Z vendors`` indicator; page with
        ``offset``.

        Args:
            active_only: If True, only return active vendors.
            compact: If True, return compact one-line-per-vendor string.
            limit: Page size (default 50, max 250). 0 = count only.
            offset: 0-indexed first row to return.

        Returns:
            Compact string or verbose envelope.
        """
        with self.open() as book:
            vendors = sorted(book.vendors, key=lambda v: v.name)
            if active_only:
                vendors = [v for v in vendors if v.active]

            page, indicator = _paginate(
                vendors, offset=offset, limit=limit, entity_name="vendors",
            )
            if compact:
                lines = [indicator]
                lines += [self._vendor_to_compact_line(v) for v in page]
                return "\n".join(lines)
            else:
                return {
                    "showing": indicator,
                    "total": len(vendors),
                    "offset": offset,
                    "count": len(page),
                    "vendors": [self._vendor_to_dict(v) for v in page],
                }

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

        Employee has no ``notes`` field (unlike Customer/Vendor);
        ``acl``/``language``/``workday``/``rate`` are not exposed.

        Args:
            name: Employee name.
            currency: ISO currency code. Defaults to book's default currency.
            address: Optional address dict with keys: name, addr1, addr2,
                     addr3, addr4, phone, fax, email.

        Returns:
            Dict with id, name, currency, status.
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
        limit: int = 50,
        offset: int = 0,
    ) -> dict | str:
        """List all employees.

        Leads with a ``Showing X-Y of Z employees`` indicator; page
        with ``offset``.

        Args:
            active_only: If True, only return active employees.
            compact: If True, return compact one-line-per-employee string.
            limit: Page size (default 50, max 250). 0 = count only.
            offset: 0-indexed first row to return.

        Returns:
            Compact string or verbose envelope.
        """
        with self.open() as book:
            employees = sorted(book.employees, key=lambda e: e.name)
            if active_only:
                employees = [e for e in employees if e.active]

            page, indicator = _paginate(
                employees, offset=offset, limit=limit,
                entity_name="employees",
            )
            if compact:
                lines = [indicator]
                lines += [self._employee_to_compact_line(e) for e in page]
                return "\n".join(lines)
            else:
                return {
                    "showing": indicator,
                    "total": len(employees),
                    "offset": offset,
                    "count": len(page),
                    "employees": [self._employee_to_dict(e) for e in page],
                }

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

        ``None`` = no change on every parameter; pass an empty
        string to clear ``notes``. Currency change sets the
        customer's *default* trading currency for future documents
        only. See ``_update_business_person`` for address-merge
        semantics, the diff-style response, and failure modes.
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

        Same semantics as ``update_customer``; see
        ``_update_business_person`` for details.
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

        Employee has no ``notes`` column, so the parameter is
        omitted. Otherwise identical to ``update_customer``.
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
            Dict with name, due_days, status.
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

            # ``guid`` omitted — billterms addressed by ``name``.
            return {
                "name": name,
                "due_days": due_days,
                "status": "created",
            }

    def list_billterms(
        self, compact: bool = True, limit: int = 50, offset: int = 0,
    ) -> dict | str:
        """List all billing terms.

        Leads with a ``Showing X-Y of Z billterms`` indicator; page
        with ``offset``.

        Args:
            compact: If True, return compact one-line-per-term string.
            limit: Page size (default 50, max 250). 0 = count only.
            offset: 0-indexed first row to return.

        Returns:
            Compact string or verbose envelope.
        """
        from piecash.business.invoice import Billterm

        with self.open() as book:
            terms = book.session.query(Billterm).filter(
                Billterm.invisible == 0
            ).order_by(Billterm.name).all()

            page, indicator = _paginate(
                terms, offset=offset, limit=limit, entity_name="billterms",
            )
            if compact:
                lines = [indicator]
                lines += [f"{t.name}\t{t.duedays} days" for t in page]
                return "\n".join(lines)
            else:
                return {
                    "showing": indicator,
                    "total": len(terms),
                    "offset": offset,
                    "count": len(page),
                    "billterms": [self._billterm_to_dict(t) for t in page],
                }

    # ── Taxtable CRUD ─────────────────────────────────────────────
    #
    # Taxtables route sales-tax math on document line entries. Each
    # ``TaxtableEntry`` contributes either a percentage rate (5.00 =
    # 5%) or a flat value routed to a specific GL account; a
    # multi-entry taxtable (GST 5% + PST 7%) produces N tax splits
    # per line at posting (math in ``_compute_entry_tax`` /
    # ``_get_invoice_entries_and_total``; ``_add_entry`` wires
    # entries to taxtables).
    #
    # **Refcount discipline.** GnuCash desktop maintains
    # ``Taxtable.refcount``; piecash does not. We bump it manually
    # on entry create/delete for desktop interop, but every
    # lifecycle check here uses ``_compute_taxtable_refcount`` — an
    # indexed COUNT over ``entries``, authoritative regardless of
    # the stored column. Voided invoices still pin their taxtables
    # (their entry rows persist for audit trail).

    @staticmethod
    def _compute_taxtable_refcount(book, taxtable_guid: str) -> int:
        """Count Entry rows referencing this taxtable via
        ``i_taxtable`` or ``b_taxtable``. Authoritative for delete
        guards and update warnings — independent of the stored
        ``Taxtable.refcount`` column which is maintained for desktop
        interop but not used for our own logic.
        """
        from sqlalchemy import text
        row = book.session.execute(
            text(
                "SELECT COUNT(*) FROM entries "
                "WHERE i_taxtable = :guid OR b_taxtable = :guid"
            ),
            {"guid": taxtable_guid},
        ).fetchone()
        return int(row[0]) if row else 0

    @staticmethod
    def _taxtable_entry_summary(e) -> str:
        """One-token compact summary of a taxtable entry.

        ``5%→GST Payable`` for percentage entries, ``$5→Eco Fee``
        for flat-value. The arrow makes the routing direction
        visually unmistakable.
        """
        acct_name = e.account.name if e.account else "?"
        if e.type == "percentage":
            return f"{e.amount}%→{acct_name}"
        return f"${e.amount}→{acct_name}"

    def _validate_taxtable_entries(
        self, book, entries: list[dict],
    ) -> list[dict]:
        """Validate a list of taxtable-entry dicts and resolve their
        accounts. Returns piecash-ready ``{type, amount, account}``
        records (``amount`` as Decimal, ``account`` as the piecash
        object). Raises ValueError naming the failing entry's index.

        Validations:
          - non-empty list
          - ``type`` in ``{"value", "percentage"}``
          - amount parses to Decimal, > 0; percentage < 100 (rates
            are percent, not fraction — >=100% is almost certainly
            user error)
          - account resolves and is ASSET or LIABILITY (sales-tax
            payable / input-tax-credit receivable; anything else
            books tax into the wrong section)
          - all entries share one commodity (one taxtable per
            jurisdiction/commodity is the supported model)
        """
        if not entries:
            raise ValueError(
                "Taxtable must have at least one entry"
            )

        resolved: list[dict] = []
        commodities_seen: set[str] = set()

        for i, e in enumerate(entries):
            type_val = e.get("type")
            if type_val not in ("value", "percentage"):
                raise ValueError(
                    f"Entry {i}: type must be 'value' or "
                    f"'percentage', got {type_val!r}"
                )

            try:
                amount = _to_decimal(str(e.get("amount", "")))
            except Exception:
                raise ValueError(
                    f"Entry {i}: amount {e.get('amount')!r} not a "
                    f"valid decimal"
                )
            if amount <= 0:
                raise ValueError(
                    f"Entry {i}: amount must be > 0, got {amount}"
                )
            if (
                type_val == "percentage"
                and amount >= Decimal("100")
            ):
                raise ValueError(
                    f"Entry {i}: percentage rate {amount} >= 100 "
                    f"is almost certainly user error. Rates are "
                    f"expressed as a percentage (5.0 for 5%, not "
                    f"0.05). If you genuinely want a rate this "
                    f"large, file an issue."
                )

            account_ref = e.get("account")
            if not account_ref:
                raise ValueError(f"Entry {i}: account is required")
            acct = self._resolve_account(book, account_ref)
            if not acct:
                raise ValueError(
                    f"Entry {i}: account not found: {account_ref!r}"
                )
            if acct.type not in ("ASSET", "LIABILITY"):
                raise ValueError(
                    f"Entry {i}: account {acct.fullname!r} has type "
                    f"{acct.type!r}; taxtable entries must reference "
                    f"ASSET (input-tax-credit receivable) or "
                    f"LIABILITY (output sales-tax payable) accounts. "
                    f"Got something else — verify the account "
                    f"hierarchy."
                )
            commodities_seen.add(acct.commodity.guid)
            resolved.append({
                "type": type_val,
                "amount": amount,
                "account": acct,
            })

        if len(commodities_seen) > 1:
            raise ValueError(
                f"Taxtable entries must all reference accounts in "
                f"the same commodity; got accounts in "
                f"{len(commodities_seen)} different commodities. "
                f"Multi-currency taxtables are rejected — split "
                f"into one taxtable per jurisdiction/commodity."
            )

        return resolved

    @staticmethod
    def _compute_entry_tax(
        quantity: Decimal,
        price: Decimal,
        taxable: bool,
        tax_included: bool,
        taxtable_entries: list[dict],
        quantum: Decimal,
    ) -> dict:
        """Per-line tax math. Pure function; no book access.

        Caller resolves the taxtable to a list of
        ``{type, amount, account_guid}`` dicts before invoking
        (``type`` is ``'value'`` or ``'percentage'``; ``amount``
        is ``Decimal``; ``account_guid`` is the FK to wherever the
        tax component routes).

        Four quadrants of behavior:

        1. ``taxable=False``: no tax. ``pretax = Q × P``,
           ``tax_total = 0``, ``tax_by_acct = {}``, ``gross = Q × P``.

        2. ``taxable=True, tax_included=False`` (tax-exclusive):
           Line value IS pre-tax; tax adds on top. For each entry,
           percentage entries contribute ``pretax × rate / 100``,
           value entries contribute their flat amount. Each is
           quantized independently per the per-line rounding policy
           (auditable line-by-line, matches GnuCash desktop).

        3. ``taxable=True, tax_included=True``, all-percentage
           taxtable: Line value is gross. Pretax extracted via
           ``pretax = gross / (1 + Σ rate / 100)``. Per-entry tax
           computed from extracted pretax.

        4. ``taxable=True, tax_included=True``, mixed value +
           percentage: Pretax extracted via
           ``pretax = (gross − Σ value) / (1 + Σ rate / 100)``.
           Value entries contribute their flat amount unchanged;
           percentage entries contribute ``pretax × rate / 100``.

        **Rounding residual policy** (Quadrants 3/4): after
        independent per-entry quantization, ``gross == pretax +
        Σ tax`` may differ by at most one quantum. The residual is
        applied to the largest-rate percentage entry (or the first
        value entry as fallback for all-value tax-inclusive — an
        edge case that's algebraically degenerate but harmless).
        Done this way to keep the dominant tax authority's bucket
        carrying the rounding noise rather than smearing it across
        all entries.

        Args:
            taxable: whether this line has a taxtable applied.
            tax_included: whether ``Q × P`` is gross (tax-inclusive)
                or pre-tax (tax-exclusive).
            taxtable_entries: resolved ``{type, amount, account_guid}``
                dicts.
            quantum: smallest unit of the invoice currency (from
                ``_commodity_quantum``).

        Returns:
            ``{pretax, tax_total, tax_by_acct, gross}`` — all
            ``Decimal`` values; ``tax_by_acct`` is
            ``{account_guid: Decimal}`` with one entry per distinct
            payable account (composite taxtables routing to the
            same account collapse to one entry by sum).
        """
        line_value = quantity * price

        if not taxable or not taxtable_entries:
            # Quadrant 1, or defensive no-entries fallback (the
            # caller should have validated; behave as no-tax).
            qv = line_value.quantize(quantum)
            return {
                "pretax": qv,
                "tax_total": Decimal(0),
                "tax_by_acct": {},
                "gross": qv,
            }

        sum_values = sum(
            (
                e["amount"]
                for e in taxtable_entries
                if e["type"] == "value"
            ),
            Decimal(0),
        )
        sum_rates = sum(
            (
                e["amount"]
                for e in taxtable_entries
                if e["type"] == "percentage"
            ),
            Decimal(0),
        )
        rate_factor = sum_rates / Decimal(100)

        if tax_included:
            # Quadrants 3/4: extract pretax from gross.
            gross = line_value.quantize(quantum)
            if rate_factor == 0:
                # All-value tax-inclusive: pretax = gross − values.
                pretax = (gross - sum_values).quantize(quantum)
            else:
                pretax = (
                    (gross - sum_values)
                    / (Decimal(1) + rate_factor)
                ).quantize(quantum)
        else:
            # Quadrant 2: line value IS pretax.
            pretax = line_value.quantize(quantum)
            gross = None  # computed after tax_total

        tax_by_acct: dict[str, Decimal] = {}
        for e in taxtable_entries:
            acct_guid = e["account_guid"]
            if e["type"] == "percentage":
                tax_e = (
                    pretax * e["amount"] / Decimal(100)
                ).quantize(quantum)
            else:
                tax_e = e["amount"].quantize(quantum)
            tax_by_acct[acct_guid] = (
                tax_by_acct.get(acct_guid, Decimal(0)) + tax_e
            )

        tax_total = sum(tax_by_acct.values(), Decimal(0))

        if tax_included:
            # Residual adjustment: enforce gross = pretax + tax_total
            # exactly. The residual is at most ±1 quantum from the
            # independent per-entry rounding.
            residual = gross - pretax - tax_total
            if residual != 0:
                # Find largest-rate percentage entry; fall back to
                # first value entry if no percentage entries exist.
                target_acct = None
                largest_rate = Decimal(0)
                for e in taxtable_entries:
                    if (
                        e["type"] == "percentage"
                        and e["amount"] > largest_rate
                    ):
                        largest_rate = e["amount"]
                        target_acct = e["account_guid"]
                if target_acct is None:
                    target_acct = (
                        taxtable_entries[0]["account_guid"]
                    )
                tax_by_acct[target_acct] = (
                    tax_by_acct[target_acct] + residual
                )
                tax_total = tax_total + residual
        else:
            gross = pretax + tax_total

        return {
            "pretax": pretax,
            "tax_total": tax_total,
            "tax_by_acct": tax_by_acct,
            "gross": gross,
        }

    def create_taxtable(
        self,
        name: str,
        entries: list[dict],
    ) -> dict:
        """Create a new tax table.

        Entries contribute either a percentage rate
        (``type='percentage'``, ``amount=5.00`` = 5%) or a flat
        value, each routed to an ASSET or LIABILITY account; at
        posting each entry produces its own split. Validation rules
        (account types, same-commodity constraint, amount bounds)
        live on ``_validate_taxtable_entries``.

        Args:
            name: Taxtable name. Must be unique within the book.
            entries: List of ``{type, amount, account}`` dicts.

        Returns:
            ``{name, entry_count, entries, status='created'}``.

        Raises:
            ValueError: duplicate name or any validation failure.
        """
        from piecash.business.tax import Taxtable, TaxtableEntry

        with self.open(readonly=False) as book:
            existing = self._find_taxtable(book, name)
            if existing:
                raise ValueError(
                    f"Taxtable {name!r} already exists "
                    f"(guid={existing.guid[:8]})"
                )

            resolved = self._validate_taxtable_entries(book, entries)

            tt = Taxtable(
                name=name,
                entries=[
                    TaxtableEntry(
                        type=r["type"],
                        amount=r["amount"],
                        account=r["account"],
                    )
                    for r in resolved
                ],
            )
            book.session.add(tt)
            book.flush()

            # Capture before-close to avoid DetachedInstanceError
            # on attribute access after ``book.save()`` exits the
            # session.
            tt_guid = tt.guid
            tt_name = tt.name
            account_paths = {
                r["account"].guid: r["account"].fullname
                for r in resolved
            }
            entry_dicts = [
                self._taxtable_entry_to_dict(
                    e, account_paths=account_paths,
                )
                for e in tt.entries
            ]

            book.save()

            # ``guid`` omitted — taxtables addressed by ``name``.
            return {
                "name": tt_name,
                "entry_count": len(entry_dicts),
                "entries": entry_dicts,
                "status": "created",
            }

    def list_taxtables(
        self, compact: bool = True, limit: int = 50, offset: int = 0,
    ) -> dict | str:
        """List all tax tables.

        Leads with a ``Showing X-Y of Z taxtables`` indicator; page
        with ``offset``. Compact (default): one line per taxtable,
        ``name<TAB>N entries: 5%→GST Payable, 7%→PST Payable``.
        Verbose: envelope of full dicts with resolved account paths
        and computed refcount.
        """
        from piecash.business.tax import Taxtable

        with self.open() as book:
            tables = book.session.query(Taxtable).order_by(
                Taxtable.name,
            ).all()

            page, indicator = _paginate(
                tables, offset=offset, limit=limit,
                entity_name="taxtables",
            )
            if compact:
                lines = [indicator]
                for tt in page:
                    summary = ", ".join(
                        self._taxtable_entry_summary(e)
                        for e in tt.entries
                    )
                    n = len(tt.entries)
                    suffix = "entry" if n == 1 else "entries"
                    lines.append(
                        f"{tt.name}\t{n} {suffix}: {summary}"
                    )
                return "\n".join(lines)

            return {
                "showing": indicator,
                "total": len(tables),
                "offset": offset,
                "count": len(page),
                "taxtables": [
                    self._taxtable_to_dict(
                        tt,
                        account_paths={
                            e.account_guid: e.account.fullname
                            for e in tt.entries
                        },
                        refcount=self._compute_taxtable_refcount(
                            book, tt.guid,
                        ),
                    )
                    for tt in page
                ],
            }

    def get_taxtable(self, name: str) -> dict:
        """Full details for a tax table.

        Returns ``{name, refcount, entries: [...]}`` with resolved
        account paths; ``refcount`` is the SQL-computed count of
        Entry rows referencing this taxtable.

        Raises:
            ValueError: taxtable not found.
        """
        with self.open() as book:
            tt = self._find_taxtable(book, name)
            if not tt:
                raise ValueError(f"Taxtable not found: {name!r}")
            return self._taxtable_to_dict(
                tt,
                account_paths={
                    e.account_guid: e.account.fullname
                    for e in tt.entries
                },
                refcount=self._compute_taxtable_refcount(
                    book, tt.guid,
                ),
            )

    def update_taxtable(
        self,
        name: str,
        new_name: str | None = None,
        entries: list[dict] | None = None,
        force: bool = False,
    ) -> dict:
        """Update a tax table's name and/or entries.

        Diff-style response: only changed fields are returned.

        **Entry replacement on an in-use taxtable rewrites the
        displayed tax math live**: posted documents keep their
        stored splits, but their displayed totals recompute from
        the new entries. When the SQL-computed refcount > 0
        (voided invoices count too), replacing entries requires
        ``force=True``; prefer creating a new taxtable for future
        documents instead.

        Args:
            name: Current taxtable name.
            new_name: New name; rejected on collision.
            entries: Replacement list, validated like
                ``create_taxtable``. Old entries delete via the
                relation's ``delete-orphan`` cascade.
            force: Required to replace entries when refcount > 0.

        Raises:
            ValueError: not found; no fields supplied; name
                collision; validation failure; in-use without force.
        """
        if new_name is None and entries is None:
            raise ValueError(
                "update_taxtable requires at least one of "
                "new_name or entries."
            )

        from piecash.business.tax import TaxtableEntry

        with self.open(readonly=False) as book:
            tt = self._find_taxtable(book, name)
            if not tt:
                raise ValueError(f"Taxtable not found: {name!r}")
            tt_guid = tt.guid

            # Capture before-state for the audit log (entries
            # snapshot serialized eagerly so detach doesn't bite
            # the formatter at write time).
            before_entries = [
                self._taxtable_entry_to_dict(
                    e,
                    account_paths={
                        e.account_guid: e.account.fullname,
                    },
                )
                for e in tt.entries
            ]
            self._stage_audit_before({
                "name": tt.name,
                "entries": before_entries,
            })

            changed: dict = {}

            if new_name is not None and new_name != tt.name:
                collision = self._find_taxtable(book, new_name)
                if collision and collision.guid != tt.guid:
                    raise ValueError(
                        f"Taxtable {new_name!r} already exists "
                        f"(guid={collision.guid[:8]})"
                    )
                changed["name"] = {
                    "before": tt.name, "after": new_name,
                }
                tt.name = new_name

            if entries is not None:
                refcount = self._compute_taxtable_refcount(
                    book, tt_guid,
                )
                if refcount > 0 and not force:
                    raise ValueError(
                        f"Taxtable {name!r} is referenced by "
                        f"{refcount} entries. Replacing its "
                        f"entries rewrites the tax math everywhere "
                        f"the table is read LIVE: already-posted "
                        f"documents keep their original splits, "
                        f"but their DISPLAYED totals recompute "
                        f"from the new entries — outstanding lists "
                        f"would show phantom amount_paid/amount_due "
                        f"(recomputed total vs the lot's real "
                        f"balance). Prefer creating a NEW taxtable "
                        f"for future documents; pass ``force=True`` "
                        f"only if you accept that historical-"
                        f"display skew."
                    )
                resolved = self._validate_taxtable_entries(
                    book, entries,
                )
                # Replacing via slice assignment relies on the
                # entries relation's ``cascade="all, delete-orphan"``
                # to remove the displaced rows. The new TaxtableEntry
                # objects auto-register with the taxtable via the
                # back_populates relation; no explicit session.add()
                # needed.
                tt.entries[:] = [
                    TaxtableEntry(
                        type=r["type"],
                        amount=r["amount"],
                        account=r["account"],
                    )
                    for r in resolved
                ]
                # Flush so the new entries' ``account_guid`` FK and
                # autoincrement ``id`` columns are populated before
                # we serialize the after-state. Pre-flush,
                # ``e.account_guid`` is None on the new entries and
                # ``_taxtable_entry_to_dict`` would drop the resolved
                # ``account`` path. Matches the explicit flush in
                # ``create_taxtable``.
                book.flush()
                after_entries = [
                    self._taxtable_entry_to_dict(
                        e,
                        account_paths={
                            r["account"].guid: r["account"].fullname
                            for r in resolved
                        },
                    )
                    for e in tt.entries
                ]
                changed["entries"] = {
                    "before": before_entries,
                    "after": after_entries,
                }

            book.save()

            if not changed:
                # ``guid`` omitted.
                return {
                    "name": name,
                    "status": "unchanged",
                }

            return {
                "guid": tt_guid,
                "name": new_name if new_name else name,
                "status": "updated",
                "changed": changed,
            }

    def delete_taxtable(self, name: str) -> dict:
        """Delete a tax table.

        Refuses when the SQL-computed refcount > 0 (voided invoices
        still pin their taxtables — see the section header). Child
        TaxtableEntry rows clean up via the delete-orphan cascade.

        Raises:
            ValueError: taxtable not found, or refcount > 0.
        """
        from piecash.business.tax import Taxtable

        with self.open(readonly=False) as book:
            tt = self._find_taxtable(book, name)
            if not tt:
                raise ValueError(f"Taxtable not found: {name!r}")
            tt_guid = tt.guid

            refcount = self._compute_taxtable_refcount(book, tt_guid)
            if refcount > 0:
                raise ValueError(
                    f"Cannot delete taxtable {name!r}: {refcount} "
                    f"entries reference it. Remove or re-assign "
                    f"those entries first. (Note: voided invoices "
                    f"still pin their taxtables — voided entry "
                    f"rows persist for audit-trail purposes.)"
                )

            # Capture serializable snapshot for the audit log
            # before delete (entries are detached after save).
            self._stage_audit_before({
                "name": name,
                "entries": [
                    self._taxtable_entry_to_dict(
                        e,
                        account_paths={
                            e.account_guid: e.account.fullname,
                        },
                    )
                    for e in tt.entries
                ],
            })

            book.session.delete(tt)
            book.save()

            from gnucash_mcp.book._base import _verify_delete
            _verify_delete(
                book.session, Taxtable.__table__,
                {"guid": tt_guid},
                f"Taxtable {name!r}",
            )

            return {
                "guid": tt_guid,
                "name": name,
                "status": "deleted",
            }

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
        5: {  # employee expense voucher
            "owner_label": "Employee",
            "doc_label": "Voucher",
            "owner_id_key": "employee_id",
            "doc_id_param": "voucher_id",
            "counter_attr": "counter_exp_voucher",
            "find_owner_method": "_find_employee",
        },
    }

    # ── Owner-type label helpers ─────────────────────────────────
    # Three counterparty types — binary "is_bill = owner_type == 4"
    # idioms mislabel vouchers; these maps keep dispatch three-way.

    # Response ``type`` field — lowercase, used by the audit log
    # decorator to swap entity_type for the post/pay/unpost
    # polymorphism.
    _OWNER_TYPE_TO_RESPONSE_TYPE = {2: "invoice", 4: "bill", 5: "voucher"}

    # Compact-line type tag (short, all-caps). Invoices stay "INV"
    # for backward compat with existing render tests; bills /
    # vouchers get explicit tags.
    _OWNER_TYPE_TO_COMPACT_TAG = {2: "INV", 4: "BILL", 5: "VCHR"}

    # Audit log entity_type — matches the (entity_type, operation)
    # dispatch table in logging_config.py.
    _OWNER_TYPE_TO_AUDIT_ENTITY = {
        2: "invoice",
        4: "bill",
        5: "voucher",
    }

    @staticmethod
    def _is_bill_side(owner_type: int) -> bool:
        """Bills (4) and vouchers (5) both represent "company owes
        someone" semantics — same posting direction, same entry
        column group (``b_*``), same allowed account types.

        For job-attached invoices (owner_type=3), pass the job's
        owner_type instead — see ``_effective_owner_type``.
        """
        return owner_type in (4, 5)

    @staticmethod
    def _effective_owner_type(book, invoice) -> int:
        """Resolve a document's "semantic" owner_type.

        Direct documents return owner_type unchanged (2/4/5);
        job-attached invoices (owner_type=3) chase through the Job
        to its underlying 2/4 — the side every posting-direction /
        validation / label check actually cares about. Falls back
        to the raw owner_type on a dangling owner_guid.

        Callers that also need the Job should use
        ``_resolve_owner_type_and_job`` (single query, both outputs).
        """
        if invoice.owner_type != 3:
            return invoice.owner_type
        job = BusinessMixin._find_job_by_guid(book, invoice.owner_guid)
        if job is None:
            return invoice.owner_type
        return job.owner_type

    @staticmethod
    def _resolve_owner_type_and_job(book, invoice):
        """Single-query variant of ``_effective_owner_type`` that
        also returns the linked Job.

        Returns ``(effective_owner_type, job_or_none)`` — Job is
        None for direct documents and on dangling owner_guid.
        Callers needing both should use this rather than two
        side-by-side lookups chasing the same Job row.
        """
        if invoice.owner_type != 3:
            return invoice.owner_type, None
        job = BusinessMixin._find_job_by_guid(book, invoice.owner_guid)
        if job is None:
            return invoice.owner_type, None
        return job.owner_type, job

    # Title-case document label for errors / status / audit entries.
    _OWNER_TYPE_TO_DOC_LABEL = {2: "Invoice", 4: "Bill", 5: "Voucher"}

    @staticmethod
    def _doc_label_for(owner_type: int | None) -> str:
        """Title-case label naming the document type — used in
        error messages and status strings. ``None`` falls back to
        the generic ``"Document"`` since we don't know what kind
        the caller meant; the three known types map cleanly."""
        if owner_type is None:
            return "Document"
        return BusinessMixin._OWNER_TYPE_TO_DOC_LABEL.get(
            owner_type, "Document"
        )

    # ── Credit-note slot helpers ─────────────────────────────────
    #
    # GnuCash stores the credit-note flag in slots, not as a
    # column: KVP key ``credit-note``, integer value ``1`` for
    # credit notes, slot absent for normal documents. The flag
    # is owner-type-agnostic — a customer invoice or vendor bill
    # with ``credit-note=1`` is the credit-note form of that
    # document, with reversed posting direction at post time.
    #
    # We add a second slot, ``gnc-mcp/applies-to-invoice``, that
    # links a credit note back to its source document. GnuCash
    # desktop doesn't track this linkage — its Process Payment
    # dialog handles credit-vs-invoice netting through user
    # selection, not stored references. The ``gnc-mcp/`` prefix
    # signals this is an MCP-server extension (per our slot
    # convention: bare keys for universal concepts, namespaced
    # for tool-specific state). Stored value is the source
    # invoice's 32-char GUID; displayed as the human-readable
    # ``{id, type}`` pair via ``_resolve_applies_to``.

    _CREDIT_NOTE_SLOT_KEY = "credit-note"
    _APPLIES_TO_SLOT_KEY = "gnc-mcp/applies-to-invoice"

    @staticmethod
    def _get_is_credit_note(invoice) -> bool:
        """Read the GnuCash ``credit-note`` slot. True iff value=1."""
        from gnucash_mcp.book._base import _slot_bool
        return _slot_bool(
            invoice, BusinessMixin._CREDIT_NOTE_SLOT_KEY,
        ) is True

    @staticmethod
    def _set_is_credit_note(invoice, value: bool = True) -> None:
        """Set or clear the GnuCash ``credit-note`` slot.

        Stores integer ``1`` for credit notes (GnuCash convention);
        clearing removes the slot entirely so the absence-means-False
        invariant holds for both desktop and MCP readers.
        """
        if value:
            invoice[BusinessMixin._CREDIT_NOTE_SLOT_KEY] = 1
        else:
            try:
                del invoice[BusinessMixin._CREDIT_NOTE_SLOT_KEY]
            except KeyError:
                pass

    @staticmethod
    def _get_applies_to_invoice_guid(invoice) -> str | None:
        """Read the source-invoice GUID this credit note applies
        to, if set. Returns None when no source is linked."""
        try:
            raw = invoice[BusinessMixin._APPLIES_TO_SLOT_KEY]
        except KeyError:
            return None
        val = _slot_value_str(raw)
        return val if val else None

    @staticmethod
    def _set_applies_to_invoice_guid(invoice, source_guid: str) -> None:
        """Link this credit note to a source invoice by GUID. The
        stored value is the canonical 32-char hex GUID; display
        helpers resolve it back to the human-readable ID."""
        invoice[BusinessMixin._APPLIES_TO_SLOT_KEY] = source_guid

    @staticmethod
    def _resolve_applies_to(book, invoice) -> dict | None:
        """Resolve the applies-to slot to a ``{id, type}`` dict
        suitable for display in tool responses. Returns None when:

        - no source is linked (normal credit note that floats),
        - the slot points at a GUID that doesn't exist anymore
          (dangling reference; surface as absent rather than
          crash the response).
        """
        guid = BusinessMixin._get_applies_to_invoice_guid(invoice)
        if not guid:
            return None
        from piecash.business.invoice import Invoice
        source = book.session.query(Invoice).filter_by(guid=guid).first()
        if not source:
            return None
        return {
            "id": source.id,
            "type": BusinessMixin._OWNER_TYPE_TO_RESPONSE_TYPE.get(
                source.owner_type, "invoice"
            ),
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
        extra_slots: dict | None = None,
        job_id: str | None = None,
    ) -> dict:
        """Shared create path for invoices, bills, and vouchers.

        All three hit the ``invoices`` table with the same insert,
        differing only in the label / counter / finder conventions
        in ``_BUSINESS_DOC_CONFIG``. Resolves owner + currency +
        billterm, handles custom-ID vs auto-counter, writes with
        ``_verify_write``.

        Currency resolution when ``currency`` is omitted: the
        owner's trading currency first, book default as fallback —
        a USD vendor's bill should be USD even on a CNY book.

        Returns:
            ``{id, <owner_id_key>, date_opened, status}``
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

            # Job linkage: the job must exist, belong to the named
            # owner, and match the owner_type. On success the insert
            # uses owner_type=3 / owner_guid=job.guid (GnuCash's
            # polymorphic owner encoding); the customer/vendor
            # relationship is preserved through the Job row.
            job_obj = None
            if job_id:
                if owner_type == 5:
                    raise ValueError(
                        "Employee vouchers cannot be grouped under "
                        "a job — piecash's job model is "
                        "customer/vendor only."
                    )
                job_obj = self._find_job(book, job_id)
                if not job_obj:
                    raise ValueError(f"Job not found: {job_id}")
                if job_obj.owner_type != owner_type:
                    expected = (
                        "customer" if owner_type == 2 else "vendor"
                    )
                    got = (
                        "customer" if job_obj.owner_type == 2
                        else "vendor"
                    )
                    raise ValueError(
                        f"Job {job_id!r} is a {got} job; this "
                        f"is a {expected} document. Customer "
                        f"invoices can only be grouped under "
                        f"customer jobs, vendor bills under "
                        f"vendor jobs."
                    )
                if job_obj.owner_guid != owner.guid:
                    job_owner = self._find_invoice_owner_by_guid(
                        book, job_obj.owner_type, job_obj.owner_guid,
                    )
                    raise ValueError(
                        f"Job {job_id!r} belongs to "
                        f"{(job_owner.id if job_owner else '?')!r}, "
                        f"not {owner_id!r}. The job and the "
                        f"document must reference the same "
                        f"customer/vendor."
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
                # Owner currency first, book default as defensive
                # fallback (see docstring). Falling back to the book
                # default unconditionally breaks cross-currency
                # posting: a USD vendor's bill on a CNY book would be
                # created in CNY, post_invoice would see matching
                # commodities and skip rate conversion — $500 booked
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
                # counter SHOULD be canonical, but it can drift below
                # the table's actual MAX(id) (raw-SQL imports,
                # out-of-band edits); trusting it alone auto-assigns
                # a colliding ID that routes every subsequent lookup
                # to the wrong record. Use max(counter, max numeric
                # id) + 1 and re-sync the counter. Non-numeric custom
                # IDs are skipped.
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
                    # When linked to a job, the invoice's
                    # polymorphic owner pointer routes to the
                    # Job row (owner_type=3) — the job carries
                    # the underlying customer/vendor reference.
                    owner_type=3 if job_obj else owner_type,
                    owner_guid=(
                        job_obj.guid if job_obj else owner.guid
                    ),
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

            # Apply caller-supplied slots BEFORE save so slot writes
            # and the row insert land in one transaction — preserves
            # the "one book open per write" invariant.
            if extra_slots:
                new_inv = book.session.query(Invoice).filter_by(
                    guid=inv_guid,
                ).first()
                for key, value in extra_slots.items():
                    new_inv[key] = value

            book.save()

            # ``guid`` omitted — invoices/bills/vouchers/
            # credit notes addressed by ``id``.
            return {
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
        job_id: str | None = None,
    ) -> dict:
        """Create a customer invoice.

        Args:
            customer_id: Customer ID (e.g., '000001').
            date_opened: ISO date. Defaults to today.
            notes: Optional notes.
            currency: ISO code. Defaults to the customer's currency,
                then the book default — see
                ``_create_business_document``.
            term: Billterm name (e.g., 'Net 30'). Optional.
            invoice_id: Custom invoice number; auto-generated when
                omitted.
            job_id: Optional Job to group this invoice under. Must
                be a customer-job belonging to the same customer.

        Returns:
            Dict with id, customer_id, date_opened, status.
        """
        return self._create_business_document(
            owner_type=2,
            owner_id=customer_id,
            date_opened=date_opened,
            notes=notes,
            currency=currency,
            term=term,
            doc_id=invoice_id,
            job_id=job_id,
        )

    def create_bill(
        self,
        vendor_id: str,
        date_opened: str | None = None,
        notes: str = "",
        currency: str | None = None,
        term: str | None = None,
        bill_id: str | None = None,
        job_id: str | None = None,
    ) -> dict:
        """Create a vendor bill.

        Args:
            vendor_id: Vendor ID (e.g., '000001').
            date_opened: ISO date. Defaults to today.
            notes: Optional notes.
            currency: ISO code. Defaults to the vendor's currency,
                then the book default.
            term: Billterm name. Optional.
            bill_id: Custom bill number; auto-generated when omitted.
            job_id: Optional vendor-job to group this bill under.

        Returns:
            Dict with id, vendor_id, date_opened, status.
        """
        return self._create_business_document(
            owner_type=4,
            owner_id=vendor_id,
            date_opened=date_opened,
            notes=notes,
            currency=currency,
            term=term,
            doc_id=bill_id,
            job_id=job_id,
        )

    def create_voucher(
        self,
        employee_id: str,
        date_opened: str | None = None,
        notes: str = "",
        currency: str | None = None,
        term: str | None = None,
        voucher_id: str | None = None,
    ) -> dict:
        """Create an employee expense voucher.

        A voucher is the document an employee submits for
        reimbursement; it posts as a liability like a vendor bill
        and ``pay_invoice`` settles it.

        Args:
            employee_id: Employee ID (e.g., '000001').
            date_opened: ISO date. Defaults to today.
            notes: Optional notes.
            currency: ISO code. Defaults to the employee's currency,
                then the book default.
            term: Billterm name. Optional.
            voucher_id: Custom number; auto-generated when omitted.

        Returns:
            Dict with id, employee_id, date_opened, status.
        """
        return self._create_business_document(
            owner_type=5,
            owner_id=employee_id,
            date_opened=date_opened,
            notes=notes,
            currency=currency,
            term=term,
            doc_id=voucher_id,
        )

    # ── Credit-note resolution ─────────────────────────────────
    #
    # Credit notes share the ``invoices`` table with their
    # non-credit twins; the ``credit-note`` slot is the only
    # differentiator. This helper centralizes lookup + validation.

    def _resolve_credit_note(
        self, book, credit_note_id: str, owner_type: str | None = None,
    ):
        """Find a credit note by ID, validating the document IS
        flagged as a credit note.

        ``owner_type`` disambiguates ID collisions; None lets
        ``_find_invoice`` resolve or raise its collision error.
        Employee owner_type raises — credit notes are
        customer/vendor only (see ``create_credit_note``).

        Raises:
            ValueError: not found, not a credit note, or ambiguous.
        """
        int_ot = (
            self._parse_owner_type(owner_type)
            if owner_type else None
        )
        if int_ot == 5:
            # The create path rejects employee credit notes;
            # surface the same constraint on lookup.
            raise ValueError(
                "Credit notes are not supported for employees. "
                "Use unpost_document + edit on the original voucher "
                "to amend an employee reimbursement."
            )
        inv = self._find_invoice(
            book, credit_note_id, owner_type=int_ot,
        )
        if not inv:
            raise ValueError(
                f"Credit note not found: {credit_note_id}"
            )
        if not self._get_is_credit_note(inv):
            # Regular invoice/bill/voucher with this ID: name the
            # right tool so the LLM course-corrects in one hop.
            # Three-way — a binary branch would misname voucher tools.
            _DOC_TYPES = {
                2: "invoice", 4: "bill", 5: "voucher",
            }
            doc_type = _DOC_TYPES.get(inv.owner_type, "invoice")
            raise ValueError(
                f"{self._doc_label_for(inv.owner_type)} "
                f"{credit_note_id} is not a credit note. Use "
                f"add_document_entry / delete_document with "
                f"document_type='{doc_type}' for the regular "
                f"document, or check the ID."
            )
        return inv

    def create_credit_note(
        self,
        owner_id: str,
        owner_type: str,
        applies_to_invoice_id: str | None = None,
        date_opened: str | None = None,
        notes: str = "",
        currency: str | None = None,
        term: str | None = None,
        credit_note_id: str | None = None,
    ) -> dict:
        """Create a credit note against a customer invoice or
        vendor bill.

        A credit note is the audit-trail-preserving way to reverse
        part or all of a posted invoice. At post time the posting
        direction reverses: customer credit notes debit Income and
        credit A/R; vendor credit notes debit A/P and credit
        Expense.

        **Linking**: ``applies_to_invoice_id`` ties the credit note
        to its source (stored as the ``gnc-mcp/applies-to-invoice``
        slot, surfaced as ``applies_to={id, type}``). The source
        must exist, belong to the same owner, not itself be a
        credit note, and match any explicit ``currency``; when
        ``currency`` is omitted the source's is inherited. Omit the
        link for floating credit notes applied later via
        ``apply_credit_note``.

        **Employees excluded**: GnuCash desktop has no UI for
        employee credit notes; creating them here would produce
        documents the desktop can't view. Use ``unpost_document`` +
        edit instead.

        Args:
            owner_id: Customer or vendor ID.
            owner_type: 'customer' or 'vendor'.
            credit_note_id: Custom ID; auto-generates from the
                invoice/bill counter (GnuCash shares the sequence
                with credit notes).

        Returns:
            Create response plus ``is_credit_note=True`` and
            ``applies_to`` when linked.

        Raises:
            ValueError: invalid/employee owner_type, or any
                source-link validation failure.
        """
        int_owner_type = self._parse_owner_type(owner_type)
        if int_owner_type == 5:
            raise ValueError(
                "Credit notes are not supported for employees. "
                "GnuCash desktop has no UI for employee credit "
                "notes; supporting them at the MCP layer would "
                "create documents desktop users can't view or "
                "edit. Use unpost_document + edit on the original "
                "voucher to amend an employee reimbursement."
            )
        if int_owner_type not in (2, 4):
            # Defensive — _parse_owner_type would only return 2,
            # 4, or 5; this guards against a future addition.
            raise ValueError(
                f"Credit notes require owner_type 'customer' or "
                f"'vendor', got {owner_type!r}."
            )

        # Always set the credit-note flag.
        extra_slots: dict = {self._CREDIT_NOTE_SLOT_KEY: 1}
        applies_to_dict: dict | None = None

        # Source validation is a read-only lookup — no conflict
        # with the create path's separate write session.
        if applies_to_invoice_id:
            with self.open(readonly=True) as book:
                source = self._find_invoice(
                    book, applies_to_invoice_id,
                    owner_type=int_owner_type,
                )
                if not source:
                    raise ValueError(
                        f"Source "
                        f"{self._doc_label_for(int_owner_type).lower()} "
                        f"not found: {applies_to_invoice_id}"
                    )
                # Cross-owner check — credit note for customer A
                # cannot apply to customer B's invoice.
                source_owner = self._find_invoice_owner_by_guid(
                    book, source.owner_type, source.owner_guid,
                )
                if source_owner is None:
                    raise ValueError(
                        f"Source {applies_to_invoice_id} has a "
                        f"dangling owner reference — can't verify "
                        f"it belongs to {owner_id}."
                    )
                # Compare effective type AND id — ids alone are
                # per-type sequences, so customer 000005 vs vendor
                # 000005 would false-pass an id-only check.
                if (self._effective_owner_type(book, source)
                        != int_owner_type
                        or source_owner.id != owner_id):
                    raise ValueError(
                        f"Source "
                        f"{self._doc_label_for(int_owner_type).lower()} "
                        f"{applies_to_invoice_id} belongs to "
                        f"{source_owner.id!r}, not {owner_id!r}. "
                        f"Credit notes must apply to a document "
                        f"from the same customer/vendor."
                    )
                # Chaining credit notes is semantically meaningless
                # — a credit reverses a posted document.
                if self._get_is_credit_note(source):
                    raise ValueError(
                        f"Source {applies_to_invoice_id} is "
                        f"itself a credit note. Link credit "
                        f"notes to regular invoices or bills, "
                        f"not to other credit notes."
                    )
                source_currency_mnemonic = (
                    source.currency.mnemonic
                    if source.currency else None
                )
                source_guid = source.guid

            # Explicit currency must match the source's; omitted
            # currency inherits it.
            if currency and source_currency_mnemonic and currency != source_currency_mnemonic:
                raise ValueError(
                    f"Credit note currency {currency!r} doesn't "
                    f"match source {applies_to_invoice_id}'s "
                    f"currency {source_currency_mnemonic!r}. "
                    f"Netting a credit against an invoice in a "
                    f"different currency would create an FX "
                    f"adjustment GnuCash doesn't track here."
                )
            if currency is None and source_currency_mnemonic:
                currency = source_currency_mnemonic

            extra_slots[self._APPLIES_TO_SLOT_KEY] = source_guid
            applies_to_dict = {
                "id": applies_to_invoice_id,
                "type": self._OWNER_TYPE_TO_RESPONSE_TYPE.get(
                    int_owner_type, "invoice",
                ),
            }

        result = self._create_business_document(
            owner_type=int_owner_type,
            owner_id=owner_id,
            date_opened=date_opened,
            notes=notes,
            currency=currency,
            term=term,
            doc_id=credit_note_id,
            extra_slots=extra_slots,
        )

        # Augment with credit-note keys; the customer_id/vendor_id
        # key from _create_business_document is kept.
        result["is_credit_note"] = True
        if applies_to_dict:
            result["applies_to"] = applies_to_dict
        return result

    def add_credit_note_entry(
        self,
        credit_note_id: str,
        account: str,
        description: str,
        quantity: str,
        price: str,
        owner_type: str | None = None,
        taxtable: str | None = None,
        tax_included: bool = False,
        notes: str = "",
        action: str = "",
    ) -> dict:
        """Add a line item to a credit note.

        Validates the target IS a credit note, then delegates to
        ``_add_entry`` — the entry shape is identical to the
        corresponding regular document; only post-time direction
        differs. ``price`` stays positive; the credit-note flag
        inverts posting. Account types per side: INCOME for
        customer credit notes, EXPENSE/ASSET for vendor.

        Raises:
            ValueError: not found, not a credit note, wrong account
                type, or already posted.
        """
        with self.open(readonly=True) as book:
            inv = self._resolve_credit_note(
                book, credit_note_id, owner_type=owner_type,
            )
            resolved_owner_type = inv.owner_type

        result = self._add_entry(
            owner_type=resolved_owner_type,
            doc_id=credit_note_id,
            account=account,
            description=description,
            quantity=quantity,
            price=price,
            taxtable=taxtable,
            tax_included=tax_included,
            notes=notes,
            action=action,
        )
        # Re-key invoice_id/bill_id → credit_note_id for clarity.
        legacy_key = (
            "invoice_id" if resolved_owner_type == 2 else "bill_id"
        )
        if legacy_key in result:
            result["credit_note_id"] = result.pop(legacy_key)
        return result

    def delete_credit_note(
        self,
        credit_note_id: str,
        owner_type: str | None = None,
    ) -> dict:
        """Delete an unposted credit note.

        Validates the target IS a credit note, then delegates to
        ``_delete_invoice_or_bill``. Posted credit notes must be
        unposted first via ``unpost_document``.

        Raises:
            ValueError: not found, not a credit note, or posted.
        """
        with self.open(readonly=True) as book:
            inv = self._resolve_credit_note(
                book, credit_note_id, owner_type=owner_type,
            )
            resolved_owner_type = inv.owner_type

        result = self._delete_invoice_or_bill(
            credit_note_id, owner_type=resolved_owner_type,
        )
        # Re-key type: the slot, not owner_type, is the truth here.
        result["type"] = "credit_note"
        return result

    # owner_type → per-document config table for ``_add_entry``.
    # Same dispatch idiom as ``_BUSINESS_DOC_CONFIG`` for create —
    # one row per document kind, the helper below stays generic.
    _ENTRY_CONFIG: dict[int, dict] = {
        2: {  # customer invoice
            "label": "Invoice",
            "id_param": "invoice_id",
            "twin_method": "add_document_entry with document_type='bill'",
            "twin_label": "vendor bill",
            "allowed_types": frozenset({"INCOME"}),
            "type_error_msg": (
                "Invoice entry account must be INCOME (got {got} on "
                "'{path}'). Customer invoices recognize revenue; "
                "non-INCOME accounts produce valid-looking "
                "transactions with broken posting math."
            ),
        },
        4: {  # vendor bill
            "label": "Bill",
            "id_param": "bill_id",
            "twin_method": "add_document_entry with document_type='invoice'",
            "twin_label": "customer invoice",
            "allowed_types": frozenset({"EXPENSE", "ASSET"}),
            "type_error_msg": (
                "Bill entry account must be EXPENSE or ASSET (got "
                "{got} on '{path}'). EXPENSE for normal line items, "
                "ASSET for inventory purchases that capitalize."
            ),
        },
        5: {  # employee expense voucher
            # Voucher entries route through the SAME ``b_*`` columns
            # and ``bill`` FK as vendor bills — GnuCash's schema
            # collapses "company owes someone" semantics into one
            # column group regardless of whether the someone is a
            # vendor or an employee. Only owner_type on the parent
            # row distinguishes them.
            "label": "Voucher",
            "id_param": "voucher_id",
            "twin_method": "add_document_entry with document_type='invoice'",
            "twin_label": "customer invoice",
            "allowed_types": frozenset({"EXPENSE", "ASSET"}),
            "type_error_msg": (
                "Voucher entry account must be EXPENSE or ASSET "
                "(got {got} on '{path}'). EXPENSE for normal "
                "reimbursable expenses (meals, supplies, travel); "
                "ASSET for employee-purchased inventory that "
                "capitalizes."
            ),
        },
    }

    def _add_entry(
        self,
        owner_type: int,
        doc_id: str,
        account: str,
        description: str,
        quantity: str,
        price: str,
        taxtable: str | None = None,
        tax_included: bool = False,
        notes: str = "",
        action: str = "",
    ) -> dict:
        """Write a line-item entry on an invoice, bill, voucher, or
        credit note.

        ``owner_type`` (2/4/5) selects the per-doc config in
        ``_ENTRY_CONFIG``: which entries-table column side gets the
        price/account, the allowed account types, and the response
        id key.

        ``notes`` is per-line free text (same 4096-byte cap as
        customer/vendor notes); ``action`` is GnuCash's line-type
        label (desktop suggests "Hours", "Material", "Project").

        Taxtable wire-up: ``taxtable`` marks the entry taxable and
        writes the resolved GUID to ``i_taxtable``/``b_taxtable``;
        ``tax_included`` flags whether the price is pre-tax (False)
        or gross (True) for the posting math.
        ``tax_included=True`` without ``taxtable`` raises — a
        silent no-op would drop the caller's signal that they
        thought tax was enabled.

        Side effect: the taxtable's stored ``refcount`` is bumped
        for desktop interop (our checks use SQL-computed counts).
        """
        import uuid
        from piecash.business.invoice import Entry

        if tax_included and not taxtable:
            raise ValueError(
                "tax_included=True requires a taxtable. Specify "
                "which taxtable applies, or omit tax_included."
            )
        self._validate_business_freetext(notes=notes)

        cfg = self._ENTRY_CONFIG[owner_type]
        # Bills (4) and vouchers (5) share the ``b_*`` / ``bill``
        # column group — see the note on _ENTRY_CONFIG[5].
        is_bill_side = owner_type in (4, 5)
        qty = _to_decimal(quantity)
        unit_price = _to_decimal(price)

        with self.open(readonly=False) as book:
            inv = self._find_invoice(
                book, doc_id, owner_type=owner_type,
            )
            if not inv:
                raise ValueError(
                    f"{cfg['label']} not found: {doc_id}"
                )
            # _find_invoice already matches job-attached documents
            # for the requested side, so a mismatch here is a true
            # caller mistake (e.g. an invoice ID passed to
            # add_bill_entry).
            effective_owner_type = self._effective_owner_type(
                book, inv,
            )
            if effective_owner_type != owner_type:
                raise ValueError(
                    f"'{doc_id}' is a {cfg['twin_label']}, not a "
                    f"{cfg['label'].lower()}. Use "
                    f"{cfg['twin_method']} instead."
                )
            if _is_invoice_posted(inv):
                raise ValueError(
                    f"{cfg['label']} '{doc_id}' is already posted. "
                    f"Cannot add entries to posted "
                    f"{cfg['label'].lower()}s."
                )

            acct = self._resolve_account(book, account)
            if not acct:
                raise ValueError(f"Account not found: {account}")
            if acct.type not in cfg["allowed_types"]:
                raise ValueError(
                    cfg["type_error_msg"].format(
                        got=acct.type, path=account,
                    )
                )

            q_num, q_denom = self._decimal_to_num_denom(qty)
            p_num, p_denom = self._decimal_to_num_denom(unit_price)
            entry_guid = uuid.uuid4().hex

            # Resolve the taxtable upfront so a missing-taxtable
            # error fires before any write; kept for the refcount
            # bump below.
            tt_obj = None
            if taxtable:
                tt_obj = self._find_taxtable(book, taxtable)
                if not tt_obj:
                    raise ValueError(
                        f"Taxtable not found: {taxtable!r}"
                    )

            taxable_int = 1 if taxtable else 0
            taxincl_int = 1 if tax_included else 0
            tt_guid = tt_obj.guid if tt_obj else None

            # Parallel ``i_*`` / ``b_*`` column groups: exactly one
            # side carries values, the other stays zeroed/NULL. Tax
            # fields land on the same side as the price/account.
            if is_bill_side:
                values = dict(
                    i_acct=None, i_price_num=0, i_price_denom=1,
                    invoice=None,
                    b_acct=acct.guid, b_price_num=p_num,
                    b_price_denom=p_denom, bill=inv.guid,
                    i_taxable=0, i_taxincluded=0, i_taxtable=None,
                    b_taxable=taxable_int,
                    b_taxincluded=taxincl_int,
                    b_taxtable=tt_guid,
                )
            else:
                values = dict(
                    i_acct=acct.guid, i_price_num=p_num,
                    i_price_denom=p_denom, invoice=inv.guid,
                    b_acct=None, b_price_num=0, b_price_denom=1,
                    bill=None,
                    i_taxable=taxable_int,
                    i_taxincluded=taxincl_int,
                    i_taxtable=tt_guid,
                    b_taxable=0, b_taxincluded=0, b_taxtable=None,
                )

            # Entry date follows the DOCUMENT's opened date, not the
            # wall clock — GnuCash desktop's default, and what a
            # backdated invoice's lines should carry. Anchored at
            # GnuCash's neutral time (10:59) as an explicit-UTC
            # datetime so piecash's _DateTime local→UTC conversion
            # is a no-op: a naive local timestamp here stored as
            # next-day UTC for evening-western users (battery bug 4
            # — every entry dated tomorrow) and prior-day for
            # eastern ones. date_entered stays a true "when was
            # this typed" timestamp.
            opened_dt = _safe_invoice_date(inv, "date_opened")
            entry_date = datetime.combine(
                opened_dt.date() if opened_dt else date.today(),
                time(10, 59),
                tzinfo=timezone.utc,
            )
            book.session.execute(
                Entry.__table__.insert().values(
                    guid=entry_guid,
                    date=entry_date,
                    date_entered=datetime.now(),
                    description=description,
                    action=action,
                    notes=notes,
                    quantity_num=q_num,
                    quantity_denom=q_denom,
                    i_discount_num=0,
                    i_discount_denom=1,
                    i_disc_type="",
                    i_disc_how="",
                    b_paytype=0,
                    billable=0,
                    billto_type=0,
                    billto_guid=None,
                    order_guid=None,
                    **values,
                )
            )
            _verify_write(
                book.session, Entry.__table__, entry_guid,
                f"{cfg['label']} entry '{description}' on "
                f"{cfg['label'].lower()} {doc_id}",
            )

            # Keep the stored refcount in sync for GnuCash desktop;
            # our own lifecycle checks use SQL-computed counts.
            if tt_obj is not None:
                tt_obj.refcount = (tt_obj.refcount or 0) + 1

            book.save()

            total = qty * unit_price
            # Entry ``guid`` omitted — no standalone tool surface.
            result = {
                cfg["id_param"]: doc_id,
                "description": description,
                "quantity": str(qty),
                "price": str(unit_price),
                "total": str(total),
                "status": "created",
            }
            # Conditional keys — plain entries keep their shape.
            if notes:
                result["notes"] = notes
            if action:
                result["action"] = action
            return result

    def add_invoice_entry(
        self,
        invoice_id: str,
        account: str,
        description: str,
        quantity: str,
        price: str,
        taxtable: str | None = None,
        tax_included: bool = False,
        notes: str = "",
        action: str = "",
    ) -> dict:
        """Add a line item entry to a customer invoice.

        Args:
            invoice_id: Invoice ID (e.g., '000001').
            account: Income account full path (e.g., 'Income:Sales').
            description: Line item description.
            quantity: Quantity as string (e.g., '1', '2.5').
            price: Unit price as string (e.g., '100.00').
            taxtable, tax_included, notes, action: See ``_add_entry``.

        Returns:
            Dict with guid, invoice_id, total, status.
        """
        return self._add_entry(
            owner_type=2,
            doc_id=invoice_id,
            account=account,
            description=description,
            quantity=quantity,
            price=price,
            taxtable=taxtable,
            tax_included=tax_included,
            notes=notes,
            action=action,
        )

    def add_bill_entry(
        self,
        bill_id: str,
        account: str,
        description: str,
        quantity: str,
        price: str,
        taxtable: str | None = None,
        tax_included: bool = False,
        notes: str = "",
        action: str = "",
    ) -> dict:
        """Add a line item entry to a vendor bill.

        Args:
            bill_id: Bill ID (e.g., '000001').
            account: Expense account full path (e.g., 'Expenses:Office Supplies').
            description: Line item description.
            quantity: Quantity as string (e.g., '1', '2.5').
            price: Unit price as string (e.g., '50.00').
            taxtable, tax_included, notes, action: See ``_add_entry``.

        Returns:
            Dict with bill_id, total, status.
        """
        return self._add_entry(
            owner_type=4,
            doc_id=bill_id,
            account=account,
            description=description,
            quantity=quantity,
            price=price,
            taxtable=taxtable,
            tax_included=tax_included,
            notes=notes,
            action=action,
        )

    def add_voucher_entry(
        self,
        voucher_id: str,
        account: str,
        description: str,
        quantity: str,
        price: str,
        taxtable: str | None = None,
        tax_included: bool = False,
        notes: str = "",
        action: str = "",
    ) -> dict:
        """Add a line item entry to an employee expense voucher.

        Storage uses the same ``b_*`` column group as vendor bills
        — see ``_add_entry``.

        Args:
            voucher_id: Voucher ID (e.g., '000001').
            account: EXPENSE or ASSET account path (e.g.,
                'Expenses:Meals & Entertainment').
            description: Line item description.
            quantity: Quantity as string (e.g., '1').
            price: Unit price as string (e.g., '42.50').
            taxtable, tax_included, notes, action: See ``_add_entry``.

        Returns:
            Dict with voucher_id, total, status.
        """
        return self._add_entry(
            owner_type=5,
            doc_id=voucher_id,
            account=account,
            description=description,
            quantity=quantity,
            price=price,
            taxtable=taxtable,
            tax_included=tax_included,
            notes=notes,
            action=action,
        )

    def list_invoices(
        self,
        owner_type: str | None = None,
        status: str | None = None,
        compact: bool = True,
        limit: int | None = None,
        job_id: str | None = None,
        offset: int = 0,
        doc_type: str | None = None,
    ) -> dict | str:
        """List invoices and/or bills.

        Leads with a ``Showing X-Y of Z invoices (date range)``
        indicator over the full filter set; page with ``offset``.

        Args:
            owner_type: 'customer', 'vendor', or None for all.
            status: 'posted', 'open', or None for all.
            compact: If True, one line per invoice.
            limit: Page size (default 50, max 250). 0 = count only.
            job_id: Filter to invoices grouped under a specific job.
            offset: 0-indexed first row to return.

        Returns:
            Compact string (indicator + rows) or the verbose envelope
            ``{invoices, showing, total, offset, count}``.
        """
        from piecash.business.invoice import Invoice

        ot = self._parse_owner_type(owner_type)
        with self.open() as book:
            query = book.session.query(Invoice)

            # job_id trumps owner_type in SQL: job-attached invoices
            # are owner_type=3 regardless of side, so the filter
            # becomes owner_guid == job.guid (cross-checked against
            # an explicit owner_type when both were passed).
            if job_id:
                job = self._find_job(book, job_id)
                if not job:
                    raise ValueError(f"Job not found: {job_id}")
                if ot is not None and ot != job.owner_type:
                    expected = (
                        "customer" if job.owner_type == 2 else "vendor"
                    )
                    raise ValueError(
                        f"Job {job_id!r} is a {expected} job; "
                        f"owner_type={owner_type!r} doesn't match. "
                        f"Drop the owner_type filter or pass a "
                        f"matching job_id."
                    )
                query = query.filter(
                    Invoice.owner_type == 3,
                    Invoice.owner_guid == job.guid,
                )
            elif ot is not None:
                query = query.filter(Invoice.owner_type == ot)

            invoices = query.order_by(Invoice.date_opened.desc()).all()

            # Document-kind filter (consolidated surface): resolved
            # per row — credit notes are a slot flag, not a column,
            # and vouchers are the employee owner side.
            if doc_type is not None:
                def _kind(inv) -> str:
                    eot = self._effective_owner_type(book, inv)
                    if self._get_is_credit_note(inv):
                        return "credit_note"
                    if eot == 5:
                        return "voucher"
                    return "bill" if eot == 4 else "invoice"
                invoices = [i for i in invoices if _kind(i) == doc_type]

            if status == "posted":
                invoices = [i for i in invoices if _is_invoice_posted(i)]
            elif status == "open":
                invoices = [i for i in invoices if not _is_invoice_posted(i)]

            total = len(invoices)
            # The indicator noun follows the doc_type filter — a
            # voucher listing saying "0 of 0 invoices" reads as the
            # wrong tool answering (bookkeeper-pass finding,
            # 2026-08-30). Unfiltered listings span all four kinds,
            # so the generic noun is the honest one there.
            entity_noun = (
                f"{doc_type}s" if doc_type is not None else "documents"
            )
            page, indicator = _paginate(
                invoices,
                offset=offset,
                limit=limit,
                entity_name=entity_noun,
                date_key=lambda i: i.date_opened,
            )
            invoices = page

            if compact:
                lines = [indicator]
                lines += [self._invoice_to_compact_line(book, i) for i in invoices]
                return "\n".join(lines)
            else:
                # Verbose path resolves owner names per invoice.
                # Jobs are preloaded in one query (not N+1) and
                # looked up in memory per row.
                from piecash.business.invoice import Job
                job_attached_guids = [
                    i.owner_guid for i in invoices
                    if i.owner_type == 3 and i.owner_guid
                ]
                jobs_by_guid: dict = {}
                if job_attached_guids:
                    for j in book.session.query(Job).filter(
                        Job.guid.in_(set(job_attached_guids))
                    ).all():
                        jobs_by_guid[j.guid] = j

                results = []
                for i in invoices:
                    o = self._find_invoice_owner_by_guid(
                        book, i.owner_type, i.owner_guid,
                    )
                    j_dict = None
                    if i.owner_type == 3:
                        j_obj = jobs_by_guid.get(i.owner_guid)
                        if j_obj is not None:
                            j_dict = self._job_to_dict(j_obj)
                    results.append(
                        self._invoice_to_dict(
                            i,
                            owner_name=o.name if o else None,
                            job=j_dict,
                        )
                    )
                # Envelope matches the other list tools: ``count`` =
                # page length, ``total`` = full filter-set size,
                # ``showing`` = the indicator compact leads with.
                return {
                    "showing": indicator,
                    "total": total,
                    "offset": offset,
                    "count": len(results),
                    "invoices": results,
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
                raise ValueError(f"Document not found: {invoice_id}")

            is_bill = self._is_bill_side(self._effective_owner_type(book, inv))

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

            # One guid→fullname map shared across entries — readable
            # ``account`` paths instead of 32-char GUIDs.
            account_paths: dict[str, str] = {}
            for a in book.accounts:
                account_paths[a.guid] = a.fullname

            # Taxtable names: query only the GUIDs this invoice's
            # entries reference; skip entirely when none are
            # tax-bearing. Same preload pattern as list_invoices.
            if is_bill:
                needed_tt_guids = {
                    r.b_taxtable for r in rows if r.b_taxtable
                }
            else:
                needed_tt_guids = {
                    r.i_taxtable for r in rows if r.i_taxtable
                }
            taxtable_names: dict[str, str] = {}
            if needed_tt_guids:
                from piecash.business.tax import Taxtable
                for tt in book.session.query(Taxtable).filter(
                    Taxtable.guid.in_(needed_tt_guids),
                ).all():
                    taxtable_names[tt.guid] = tt.name

            entries = [
                self._entry_to_dict(
                    r,
                    is_bill=is_bill,
                    account_paths=account_paths,
                    taxtable_names=taxtable_names,
                )
                for r in rows
            ]

            # Tax summary via the posting-math seam so displayed
            # numbers match what posting produces. None when no
            # entries are tax-bearing (original response shape).
            tax_summary = None
            if rows:
                totals = self._get_invoice_entries_and_total(
                    book, inv,
                )
                if totals["tax_breakdown"]:
                    tax_summary = {
                        "subtotal": str(totals["subtotal"]),
                        "tax_total": str(
                            sum(
                                totals["tax_breakdown"].values(),
                                Decimal(0),
                            )
                        ),
                        "by_taxtable": {
                            taxtable_names.get(g, g): str(v)
                            for g, v in totals[
                                "tax_by_taxtable"
                            ].items()
                        },
                        "by_account": {
                            account_paths.get(g, g): str(v)
                            for g, v in totals[
                                "tax_breakdown"
                            ].items()
                        },
                        "total": str(totals["grand_total"]),
                    }
                # Customer-facing total is the seam's gross grand_total.
                total = totals["grand_total"]
            else:
                total = Decimal(0)

            # Three-way owner lookup — a binary is_bill check would
            # return None for vouchers.
            owner = self._find_invoice_owner_by_guid(
                book, inv.owner_type, inv.owner_guid,
            )
            owner_name = owner.name if owner else None

            # applies_to renders the GUID slot as {id, type}; only
            # emitted when the credit-note flag is set.
            applies_to = self._resolve_applies_to(book, inv)
            # The job dict feeds both the type field and the
            # surface ``job: {id, name}`` in _invoice_to_dict.
            job_dict = None
            if inv.owner_type == 3:
                job_obj = self._find_job_by_guid(
                    book, inv.owner_guid,
                )
                if job_obj is not None:
                    job_dict = self._job_to_dict(job_obj)
            result = self._invoice_to_dict(
                inv,
                entries=entries,
                owner_name=owner_name,
                applies_to=applies_to,
                job=job_dict,
                tax_summary=tax_summary,
            )
            result["total"] = str(total)

            # Forward signal: surface available (or just-expired)
            # early-payment discount so get_invoice is actionable
            # ("save $X by paying before Y") without the caller
            # reading the billterm and doing the math. Within the
            # window → ``discount_available``; past it →
            # ``discount_expired`` (same fields, for audit readers).
            disc_summary = self._compute_discount_summary(book, inv)
            if disc_summary is not None:
                today = date.today()
                block_key = (
                    "discount_available"
                    if today <= disc_summary["eligible_until"]
                    else "discount_expired"
                )
                result[block_key] = {
                    "amount": str(disc_summary["expected_discount"]),
                    "currency": disc_summary["currency"],
                    "percent": str(disc_summary["discount_percent"]),
                    "eligible_until": (
                        disc_summary["eligible_until"].isoformat()
                    ),
                }
            return result

    def _convert_invoice_amount(
        self,
        book,
        *,
        amount: Decimal,
        invoice_currency,
        target_commodity,
        as_of: date,
        context: str,
        force: bool = False,
    ) -> tuple[Decimal, Decimal | None, dict | None]:
        """Convert ``amount`` (in invoice currency) to the target
        commodity, quantized to that commodity's smallest fraction.

        Shared chokepoint for the cross-currency math behind
        ``post_invoice`` and ``pay_invoice`` — one helper, two
        callers, same error shape.

        **FX freshness guard.** GnuCash etches the exchange rate at
        post/pay time; ``create_price`` can't update an
        already-posted document. So this chokepoint refuses a rate
        more than ``GNUCASH_FX_GUARD_DAYS`` (default 7) from
        ``as_of`` unless ``force=True``. Together with the 90-day
        staleness cap in ``_find_exchange_rate`` that gives three
        bands: ≤7 proceed, 7–90 refuse-but-forceable, >90 hard
        error (no rate at all).

        Args:
            as_of: Date for the rate lookup (post or payment date).
            context: ``"posting"`` or ``"payment"`` — embedded in
                errors so the LLM sees which operation needs the
                rate.
            force: Apply a stale (7–90 day) rate anyway; its
                details come back in ``stale_meta``.

        Returns:
            ``(quantized_amount, rate, stale_meta)``. ``rate`` is
            None for same-currency (``amount`` returned untouched).
            ``stale_meta`` is None unless ``force`` overrode the
            guard — then it's the ``fx_stale`` dict.

        Raises:
            ValueError: no rate on file within the staleness cap.
            StaleFXRateError: rate in the 7–90 band and ``force``
                is False; carries structured ``fx_detail``.
        """
        if target_commodity == invoice_currency:
            return amount, None, None

        from gnucash_mcp.book._base import StaleFXRateError
        from gnucash_mcp.book._currency import (
            _fx_guard_days,
            _fx_staleness_days,
        )

        aged = self._find_exchange_rate_aged(
            book,
            from_commodity=invoice_currency,
            to_commodity=target_commodity,
            as_of=as_of,
        )
        if aged is None:
            cap = _fx_staleness_days()
            staleness_note = (
                f" within ±{cap} days"
                if cap > 0 else ""
            )
            raise ValueError(
                f"Cross-currency {context} requires an exchange "
                f"rate: invoice currency "
                f"{invoice_currency.mnemonic} differs from "
                f"target commodity {target_commodity.mnemonic}, "
                f"and no matching price was found in the book "
                f"for {invoice_currency.mnemonic}/"
                f"{target_commodity.mnemonic}{staleness_note} of "
                f"{as_of}. Add a price with create_price, then "
                f"retry. (Override the staleness window via "
                f"GNUCASH_FX_STALENESS_DAYS env var; 0 disables.)"
            )

        rate, age_days, price_date = aged

        # FX freshness guard. The 90-day cap already excluded
        # anything further out, so age_days here is <= cap; this
        # catches the 7..90 day "stale but usable" band.
        guard = _fx_guard_days()
        stale_meta = None
        if guard > 0 and age_days > guard:
            if not force:
                raise StaleFXRateError(
                    f"{invoice_currency.mnemonic}/"
                    f"{target_commodity.mnemonic} rate is {age_days} "
                    f"days from the {context} date ({as_of}); last "
                    f"quoted {price_date.isoformat()} at {rate}. This "
                    f"rate is locked at {context} time and cannot be "
                    f"updated retroactively. Either run create_price("
                    f"commodity='{invoice_currency.mnemonic}', "
                    f"value='...') to add a rate near {as_of}, or pass "
                    f"force=true to proceed with the stale rate.",
                    {
                        "currency": invoice_currency.mnemonic,
                        "rate": str(rate),
                        "rate_date": price_date.isoformat(),
                        "age_days": age_days,
                    },
                )
            stale_meta = {
                "currency": invoice_currency.mnemonic,
                "rate_used": str(rate),
                "rate_date": price_date.isoformat(),
                "age_days": age_days,
                "forced": True,
            }

        return (
            (amount * rate).quantize(
                _commodity_quantum(target_commodity)
            ),
            rate,
            stale_meta,
        )

    def post_invoice(
        self,
        invoice_id: str,
        post_account: str,
        post_date: str | None = None,
        due_date: str | None = None,
        description: str | None = None,
        owner_type: str | None = None,
        force: bool = False,
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
            owner_type: 'customer', 'vendor', or 'employee' (vouchers)
                for disambiguation.

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
            # Customer-invoice and vendor-bill ID sequences collide
            # in the shared ``invoices`` table — without an
            # owner_type filter, posting bill 000010 can fetch an
            # already-posted invoice 000010 and raise a spurious
            # "already posted". When the caller didn't pass
            # owner_type, infer it from the post account's type:
            # RECEIVABLE → customer invoice, PAYABLE → vendor bill
            # (the same predicate the later validation uses).
            inferred = False
            if ot is None:
                pa = self._resolve_account(book, post_account)
                if pa is not None:
                    if pa.type == "RECEIVABLE":
                        ot = 2
                    elif pa.type == "PAYABLE":
                        ot = 4
                        inferred = True

            inv = self._find_invoice(book, invoice_id, owner_type=ot)
            if not inv and inferred:
                # PAYABLE inference can't distinguish vendor bills
                # from employee vouchers — both post to A/P. Retry
                # the voucher side before declaring not-found (an
                # EXPLICIT owner_type never falls through here).
                inv = self._find_invoice(book, invoice_id, owner_type=5)
            if not inv:
                raise ValueError(
                    f"Document not found: {invoice_id}. If the ID "
                    f"is shared across document types, pass "
                    f"owner_type ('customer', 'vendor', or "
                    f"'employee' for vouchers) explicitly."
                )

            # ``_is_invoice_posted`` treats None/"" as not-posted —
            # see _safe_invoice_date for the piecash hazard.
            if _is_invoice_posted(inv):
                raise ValueError(
                    f"{self._doc_label_for(inv.owner_type)} "
                    f"{invoice_id} is already posted"
                )

            is_bill = self._is_bill_side(self._effective_owner_type(book, inv))

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

            # Battery ruling 1 (sunset shipped v1.5.0): a receivable
            # held in a different commodity than its document freezes
            # the balance at post-date FX and drifts from the
            # document's true value every day after. This was a
            # warning through v1.4.4; desktop GnuCash refuses the
            # pairing outright, and so do we now. Historical
            # mismatched postings from the warning era still exist in
            # real books — downstream guards (apply_credit_note,
            # pay_invoice FX settlement) keep handling those.
            if inv.currency_guid != post_acct.commodity.guid:
                side = "A/P" if is_bill else "A/R"
                raise ValueError(
                    f"Cannot post a {inv.currency.mnemonic} document "
                    f"to {post_acct.commodity.mnemonic}-denominated "
                    f"{post_acct.fullname!r}: the "
                    f"{side} balance would freeze at post-date FX "
                    f"and drift from the document's true value. "
                    f"Correct practice is one {side} account per "
                    f"document currency — post to (or create) a "
                    f"{inv.currency.mnemonic}-denominated "
                    f"{side} account instead."
                )

            totals = self._get_invoice_entries_and_total(book, inv)
            acct_totals = totals["acct_totals"]
            grand_total = totals["grand_total"]

            lot = Lot(
                title=f"Invoice {inv.id}",
                account=post_acct,
                is_closed=0,
            )
            # Lot auto-registers via the account back-pop —
            # an explicit session.add would be redundant (see
            # the matching note in investments.py).

            # GnuCash UI uses the customer/vendor name, not "Invoice NNNNNN"
            if is_bill:
                owner = self._find_vendor_by_guid(book, inv.owner_guid)
            else:
                owner = self._find_customer_by_guid(
                    book, inv.owner_guid
                )
            owner_name = owner.name if owner else f"Invoice {inv.id}"
            # description=None falls back to the owner name (GnuCash
            # UI convention); an explicit "" deliberately blanks the
            # field — ``or owner_name`` would collapse "" into the
            # fallback. Same pattern as pay_invoice.
            txn_desc = description if description is not None else owner_name

            parsed_due = (
                date.fromisoformat(due_date) if due_date else None
            )

            # Converts invoice-currency values to each account's
            # commodity via the shared ``_convert_invoice_amount``
            # chokepoint; collects stale_meta so the response can
            # surface ``fx_stale`` on forced overrides.
            fx_stale_overrides: list[dict] = []

            def _qty_for_split(acct, value_in_invoice_ccy):
                qty, _rate, stale_meta = self._convert_invoice_amount(
                    book,
                    amount=value_in_invoice_ccy,
                    invoice_currency=inv.currency,
                    target_commodity=acct.commodity,
                    as_of=parsed_date,
                    context="posting",
                    force=force,
                )
                if stale_meta is not None:
                    fx_stale_overrides.append(stale_meta)
                return qty

            # Build transaction splits
            # For customer invoice: A/R debit (positive), income credit (negative)
            # For vendor bill: A/P credit (negative), expense debit (positive)
            # For credit notes: posting direction reverses — customer
            # credit note credits A/R (reduces receivable) and debits
            # Income (reverses recognized revenue); vendor credit note
            # debits A/P (reduces payable) and credits Expense (reverses
            # recognized expense). Expressed as XOR: a credit note flips
            # whichever side it was on, so customer-credit-note behaves
            # like vendor-bill posting and vice-versa. ``effective_is_bill``
            # captures this — the rest of the math is unchanged.
            is_credit_note = self._get_is_credit_note(inv)
            effective_is_bill = is_bill ^ is_credit_note
            piecash_splits = []

            if effective_is_bill:
                ar_ap_value = -grand_total
            else:
                ar_ap_value = grand_total
            # Desktop vocabulary on EVERY leg ("Invoice" / "Credit
            # Note"), not just A/R: a leg left with action="" gets
            # auto-stamped "Buy"/"Sell" by piecash when its account
            # commodity differs from the transaction currency —
            # brokerage vocabulary on a client bill. Forward-only:
            # existing transactions are never rewritten to conform.
            doc_action = (
                "Credit Note" if is_credit_note else "Invoice"
            )
            ar_ap_split = piecash.Split(
                account=post_acct,
                value=ar_ap_value,
                quantity=_qty_for_split(post_acct, ar_ap_value),
                memo="",
                action=doc_action,
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

                if effective_is_bill:
                    split_value = acct_total
                else:
                    split_value = -acct_total

                piecash_splits.append(
                    piecash.Split(
                        account=entry_acct,
                        value=split_value,
                        quantity=_qty_for_split(entry_acct, split_value),
                        memo="",
                        action=doc_action,
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
                # Credit notes surface type='credit_note' so the
                # audit decorator swaps entity_type; the owner-type
                # label wins otherwise.
                "type": (
                    "credit_note"
                    if is_credit_note
                    else self._OWNER_TYPE_TO_RESPONSE_TYPE.get(
                        inv.owner_type, "invoice"
                    )
                ),
                "status": "posted",
                "total": str(grand_total),
                "post_date": str(parsed_date),
                # GUIDs emitted as short prefixes — consumers pass
                # them back through _resolve_guid (8+ chars accepted).
                "transaction_guid": _unique_prefix(
                    txn.guid,
                    self._transaction_prefix_map(book).keys(),
                ),
                "lot_guid": _unique_prefix(
                    lot.guid,
                    self._lot_prefix_map(book).keys(),
                ),
                "post_account": post_acct.fullname,
            }
            # Surface the worst-aged forced override (if any) as a
            # single fx_stale block — the common case is one foreign
            # currency, so there is usually exactly one.
            if fx_stale_overrides:
                result["fx_stale"] = max(
                    fx_stale_overrides, key=lambda m: m["age_days"]
                )
        return result

    def unpost_invoice(
        self,
        invoice_id: str,
        owner_type: str | None = None,
    ) -> dict:
        """Unpost a previously-posted document (invoice, bill,
        voucher, or credit note).

        Reverses ``post_invoice``: deletes the posting transaction
        and lot, clears the posted-state metadata. The document
        returns to "open" — editable and re-postable.

        Refuses if the invoice has live payments applied —
        unposting a partially-paid invoice would orphan the payment
        splits and corrupt the lot's balance. Void payments first.

        Args:
            invoice_id: Human-readable ID (e.g., '000001').
            owner_type: 'customer' or 'vendor' for cross-sequence
                ID disambiguation.

        Returns:
            ``{"id": "000015", "type": "invoice", "status": "unposted"}``.

        Raises:
            ValueError: not found, not posted, or payments applied.
        """
        ot = self._parse_owner_type(owner_type)

        with self.open(readonly=False) as book:
            inv = self._find_invoice(book, invoice_id, owner_type=ot)
            if not inv:
                raise ValueError(
                    f"Document not found: {invoice_id}"
                )

            if not _is_invoice_posted(inv):
                raise ValueError(
                    f"{self._doc_label_for(inv.owner_type)} "
                    f"{invoice_id} is not posted"
                )

            is_bill = self._is_bill_side(self._effective_owner_type(book, inv))
            # Three-way label dispatch — a binary "Bill if
            # is_bill else Invoice" mislabels vouchers as
            # bills.
            doc_label = self._doc_label_for(inv.owner_type)

            # Audit before-state, read pre-clear while the values
            # are still set ("was posted 2026-04-01 to Assets:...").
            prev_post_date = _safe_invoice_date(inv, "date_posted")
            prev_post_account = (
                inv.post_account.fullname if inv.post_account else None
            )
            self._stage_audit_before({
                "id": inv.id,
                "type": (
                    "credit_note"
                    if self._get_is_credit_note(inv)
                    else self._OWNER_TYPE_TO_RESPONSE_TYPE.get(
                        inv.owner_type, "invoice"
                    )
                ),
                "date_posted": (
                    str(prev_post_date.date()) if prev_post_date else None
                ),
                "post_account": prev_post_account,
            })

            txn = inv.post_txn
            lot = inv.post_lot

            # Only *live* payment splits block unposting. The lot
            # always holds the posting split; each pay_invoice adds
            # one more. Voided payments keep zeroed splits for audit
            # trail, so a naive ``len(lot.splits) > 1`` would count
            # them as still-applied — filter to non-posting,
            # non-zero-value splits.
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

            # Clear posted-state pointers BEFORE deleting the
            # transaction/lot — the ORM cascade can otherwise
            # resurrect references and trip dangling FKs at flush.
            inv.date_posted = None
            inv.post_txn = None
            inv.post_lot = None
            inv.post_account = None
            book.flush()

            # Read the credit-note flag BEFORE deleting: slot reads
            # can flake ("Multiple rows returned with uselist=False")
            # while lots/transactions are being deleted in the same
            # session.
            is_credit_note = self._get_is_credit_note(inv)
            inv_id_snapshot = inv.id
            owner_type_snapshot = inv.owner_type
            inv_guid_snapshot = inv.guid

            # The transaction delete cascades its splits; the lot is
            # empty now that the posted-state pointers are cleared.
            if txn is not None:
                book.session.delete(txn)
            if lot is not None:
                book.session.delete(lot)

            if is_credit_note:
                # Deleting the posting transaction sweeps the
                # invoice's OWN slots along with the txn's: the
                # txn carries a ``gncInvoice`` GUID slot, and
                # piecash's overlapping slot-hierarchy relationship
                # treats every slot on the referenced invoice as
                # that slot's child frame, cascading them away.
                # Restore the identity flag or this document comes
                # back from unpost as a plain invoice.
                from sqlalchemy import text
                book.flush()
                book.session.execute(text(
                    "INSERT INTO slots (obj_guid, name, slot_type, "
                    "int64_val) VALUES (:g, 'credit-note', 1, 1)"
                ), {"g": inv_guid_snapshot})
                restored = book.session.execute(text(
                    "SELECT COUNT(*) FROM slots WHERE obj_guid = :g "
                    "AND name = 'credit-note'"
                ), {"g": inv_guid_snapshot}).scalar()
                if restored != 1:
                    raise RuntimeError(
                        f"credit-note flag restore failed for "
                        f"{inv_id_snapshot} (rows: {restored})"
                    )

            book.save()

            return {
                "id": inv_id_snapshot,
                "type": (
                    "credit_note"
                    if is_credit_note
                    else self._OWNER_TYPE_TO_RESPONSE_TYPE.get(
                        owner_type_snapshot, "invoice"
                    )
                ),
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
        apply_discount: bool = False,
        discount_account: str | None = None,
        force: bool = False,
        memo: str = "",
        dry_run: bool = False,
    ) -> dict:
        """Record a payment against a posted invoice or bill.

        Creates a payment transaction and assigns the A/R or A/P
        split to the invoice's lot. Partial payments are supported.

        ``dry_run=True`` is a full rehearsal: every validation,
        conversion, discount precondition, and FX computation runs
        identically (readonly session, nothing persists, no piecash
        objects constructed), returning the proposed splits,
        remaining balance after, and any FX/discount treatment. An
        account the real run would auto-create (FX gain/loss,
        discounts) is reported under ``would_create_accounts``
        rather than created. A dry run that succeeds is a payment
        that will book — same inputs, same code path.

        ``memo`` lands on the bank-account split (check number, wire
        reference). The A/R//A/P split keeps its ``action="Payment"``
        convention, and server-authored memos (FX drift, discount)
        are untouched.

        ``amount`` is always in the **invoice's currency**. For
        cross-currency payments the pay account's quantity comes
        from the book's price table at ``payment_date`` (via
        ``_convert_invoice_amount``, which also enforces the FX
        freshness guard), and any post→pay rate drift books a
        realized FX gain/loss split — account routing per
        ``_get_or_create_fx_account``, override via ``fx_account``.

        ``apply_discount=True`` settles using the billterm's
        early-payment discount: the shortfall (remaining balance −
        amount) must match the expected discount on pre-tax
        principal (``_compute_discount_summary``), within the
        window. Explicit opt-in; each failed precondition rejects
        with a specific error rather than downgrading to a partial
        payment. ``discount_account`` overrides the routed account
        (resolution mirrors ``fx_account``).

        Returns:
            Payment details and remaining balance. ``status`` is
            ``"paid"`` when the lot settles to zero, ``"partial"``
            when a balance remains (``"would_pay"`` on dry runs).
            Plus ``exchange_rate`` / ``payment_account_amount`` /
            ``fx_realized`` on cross-currency, and a ``discount``
            block when a discount was booked.

        Raises:
            ValueError: not found, not posted, invalid account, no
                exchange rate, invalid ``fx_account``, or any
                ``apply_discount`` precondition failure (no terms,
                no discount, outside window, amount mismatch,
                overpayment, credit-note target).
        """
        ot = self._parse_owner_type(owner_type)

        payment_amount = _to_decimal(amount)
        if payment_amount <= 0:
            raise ValueError("Payment amount must be positive")

        parsed_date = (
            date.fromisoformat(payment_date) if payment_date
            else date.today()
        )

        with self.open(readonly=dry_run) as book:
            inv = self._find_invoice(book, invoice_id, owner_type=ot)
            if not inv:
                raise ValueError(
                    f"Document not found: {invoice_id}"
                )

            if not _is_invoice_posted(inv):
                raise ValueError(
                    f"{self._doc_label_for(inv.owner_type)} "
                    f"{invoice_id} is not posted — "
                    f"post it before recording payment"
                )

            is_bill = self._is_bill_side(self._effective_owner_type(book, inv))

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

            # Refuse to pay against a voided posting transaction:
            # voided splits read as zero, so the lot balance is 0
            # and the lot would auto-close on save with the new
            # payment split attached to it. Require unvoid (or
            # unpost + re-post) first.
            if inv.post_txn is not None:
                voided_post = all(
                    s.reconcile_state == "v"
                    for s in inv.post_txn.splits
                )
                if voided_post:
                    raise ValueError(
                        f"Cannot pay invoice {invoice_id}: its "
                        f"posting transaction has been voided. "
                        f"Unvoid the posting transaction first "
                        f"(or unpost the invoice and re-post), "
                        f"then retry payment."
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
            # description: None → owner name; explicit "" blanks the
            # field deliberately. Same pattern as post_invoice.
            txn_desc = description if description is not None else owner_name

            # Cross-currency: the transaction currency stays the
            # invoice currency (values balance in EUR for a EUR
            # invoice); each account's quantity reflects its own
            # commodity. BOTH legs convert — converting only the
            # bank side would liquidate a USD A/R holding a EUR
            # invoice in EUR-as-USD on payment. Rate lookup +
            # quantization route through _convert_invoice_amount;
            # pay also consumes the rate (it feeds
            # _compute_fx_gain_loss below) and collects stale_meta
            # for the fx_stale response block.
            fx_stale_overrides: list[dict] = []

            def _convert(amount, target_commodity):
                qty, rate, stale_meta = self._convert_invoice_amount(
                    book,
                    amount=amount,
                    invoice_currency=inv.currency,
                    target_commodity=target_commodity,
                    as_of=parsed_date,
                    context="payment",
                    force=force,
                )
                if stale_meta is not None:
                    fx_stale_overrides.append(stale_meta)
                return qty, rate

            pay_quantity, exchange_rate = _convert(
                payment_amount, pay_acct.commodity,
            )
            post_quantity, _post_rate = _convert(
                payment_amount, post_acct.commodity,
            )

            # A tiny exchange rate can quantize the converted
            # quantity to zero — a "successful" payment recording
            # nothing on the bank side. Refuse.
            if pay_quantity == 0 or post_quantity == 0:
                raise ValueError(
                    f"Cross-currency payment quantizes to zero in "
                    f"the target commodity (payment_amount="
                    f"{payment_amount} {inv.currency.mnemonic}, "
                    f"pay_quantity={pay_quantity} "
                    f"{pay_acct.commodity.mnemonic}, post_quantity="
                    f"{post_quantity} {post_acct.commodity.mnemonic}). "
                    f"Likely an extreme exchange rate; check the "
                    f"price table for {inv.currency.mnemonic}/"
                    f"{pay_acct.commodity.mnemonic}."
                )

            # Credit-note refund reverses direction: paying a
            # customer credit note SENDS cash. Same XOR trick as
            # post_invoice.
            is_credit_note = self._get_is_credit_note(inv)
            effective_is_bill = is_bill ^ is_credit_note

            # ── Overpayment guard ─────────────────────────────────
            # The lot balance is signed: positive for A/R invoices,
            # negative for A/P bills, flipped for credit notes.
            # Normalize to "amount still owed" via effective_is_bill
            # and reject any payment beyond it. Without this guard
            # nothing compares amount to remaining: an overpayment
            # drives the lot negative, lot-close (== 0 exactly) never
            # fires, and downstream abs() calls invert the sign — a
            # customer who overpaid by $1,000 renders as still OWING
            # $1,000 in the collections list.
            remaining_before_pay = self._calculate_lot_balance(lot_obj)
            if effective_is_bill:
                remaining_before_pay = -remaining_before_pay
            if payment_amount > remaining_before_pay:
                doc_label = self._doc_label_for(inv.owner_type)
                raise ValueError(
                    f"Payment of {payment_amount} "
                    f"{inv.currency.mnemonic} exceeds the "
                    f"outstanding balance of {remaining_before_pay} "
                    f"{inv.currency.mnemonic} on {doc_label} "
                    f"{invoice_id}. Pay at most the outstanding "
                    f"balance. To record a genuine overpayment, pay "
                    f"the outstanding balance and book the excess as "
                    f"a credit note (create_document with "
                    f"document_type='credit_note') so it shows "
                    f"as credit owed to the counterparty rather than "
                    f"a phantom receivable."
                )

            # ── Early-payment discount validation ────────────────
            # Five rejection cases, each with a distinct error.
            # Explicit opt-in: apply_discount=True either honors
            # all invariants and books the discount split, or
            # rejects.
            discount_split: piecash.Split | None = None
            discount_booked = False
            discount_acct = None
            discount_notice = None
            discount_amount_invoice_ccy = Decimal("0")
            disc_quantity: Decimal | None = None
            disc_value_sign = 1
            if apply_discount:
                if is_credit_note:
                    raise ValueError(
                        "Discounts cannot be applied to credit note "
                        "settlements. A credit note is a reversal, "
                        "not a payment; discounting it has no "
                        "accounting meaning."
                    )
                disc_summary = self._compute_discount_summary(book, inv)
                if disc_summary is None:
                    # Two reasons _compute_discount_summary returns
                    # None: no billterm linked, or billterm has no
                    # discount. Distinguish for the caller.
                    bt = self._get_invoice_billterm(book, inv)
                    if bt is None:
                        raise ValueError(
                            f"apply_discount=True on invoice "
                            f"{invoice_id} which has no billterm "
                            f"linked. Set terms at invoice creation "
                            f"(via create_document's ``term`` "
                            f"parameter), repost, then retry."
                        )
                    raise ValueError(
                        f"apply_discount=True on invoice "
                        f"{invoice_id} whose billterm has no early-"
                        f"payment discount configured "
                        f"(discount_days={int(bt.discountdays) if bt.discountdays else 0}, "
                        f"discount_percent={Decimal(str(bt.discount)) if bt.discount else 0}). "
                        f"Recreate the billterm with both "
                        f"discount_days and discount_percent > 0 to "
                        f"enable early-payment discounts on invoices "
                        f"that use it."
                    )

                eligible_until = disc_summary["eligible_until"]
                if parsed_date > eligible_until:
                    anchor = inv.date_opened
                    if isinstance(anchor, datetime):
                        anchor = anchor.date()
                    raise ValueError(
                        f"Payment date {parsed_date.isoformat()} is "
                        f"beyond the billterm discount window "
                        f"({disc_summary['discount_days']} days "
                        f"from {anchor.isoformat()}; deadline was "
                        f"{eligible_until.isoformat()}). Pay without "
                        f"apply_discount for a normal late payment, "
                        f"or issue a credit note to formally write "
                        f"off the would-be discount amount with an "
                        f"audit trail."
                    )

                # Principal = CURRENT remaining lot balance, not the
                # original grand_total — so the closing payment of a
                # multi-pay invoice discount-settles naturally
                # (remaining 500, pay 480, shortfall 20 = expected).
                remaining_before = abs(
                    self._calculate_lot_balance(lot_obj)
                )
                shortfall = remaining_before - payment_amount
                expected = disc_summary["expected_discount"]
                quantum = _commodity_quantum(inv.currency)

                if shortfall < 0:
                    raise ValueError(
                        f"apply_discount=True with payment_amount "
                        f"{payment_amount} {inv.currency.mnemonic} "
                        f"exceeds the outstanding lot balance "
                        f"{remaining_before} {inv.currency.mnemonic}. "
                        f"Overpayment cannot be discount-settled."
                    )

                if abs(shortfall - expected) > quantum:
                    raise ValueError(
                        f"apply_discount=True but the payment "
                        f"shortfall doesn't match the expected "
                        f"discount. Outstanding balance: "
                        f"{remaining_before} {inv.currency.mnemonic}; "
                        f"amount: {payment_amount}; shortfall: "
                        f"{shortfall}; expected discount on pre-tax "
                        f"principal: {expected} "
                        f"{inv.currency.mnemonic} "
                        f"({disc_summary['discount_percent']}%). "
                        f"Either adjust amount to "
                        f"{(remaining_before - expected).quantize(quantum)} "
                        f"to take the full discount, or pay without "
                        f"apply_discount for a partial payment."
                    )

                # Book the ACTUAL shortfall, not the computed
                # expectation: the tolerance above admits a 1-quantum
                # mismatch, and booking ``expected`` would leave the
                # splits off by that quantum — an opaque
                # GncImbalanceError after validation blessed the input.
                expected = shortfall.quantize(quantum)

                # Validation passed — resolve the discount account
                # and build the split.
                discount_acct, discount_notice = (
                    self._get_or_create_discount_account(
                        book,
                        owner_type_is_bill=effective_is_bill,
                        discount_account=discount_account,
                        dry_run=dry_run,
                    )
                )
                disc_quantity, _disc_rate = _convert(
                    expected, discount_acct.commodity,
                )
                # Customer payment: discount is EXPENSE (debit), +value
                # Vendor bill payment: discount is INCOME (credit), -value
                disc_value_sign = -1 if effective_is_bill else 1
                discount_booked = True
                if not dry_run:
                    discount_split = piecash.Split(
                        account=discount_acct,
                        value=disc_value_sign * expected,
                        quantity=disc_value_sign * disc_quantity,
                        memo="Early-payment discount",
                        action="Payment",
                    )
                discount_amount_invoice_ccy = expected

                # With a discount the A/R side absorbs the FULL
                # remaining balance; the discount split makes up the
                # difference so the transaction balances.
                full_settle_amount = remaining_before
                full_settle_post_qty, _ = _convert(
                    full_settle_amount, post_acct.commodity,
                )
            else:
                full_settle_amount = payment_amount
                full_settle_post_qty = post_quantity

            # When the post account's commodity differs from the
            # invoice currency, relieve the lot at the rate it is
            # CARRIED at (full settle → remaining quantity exactly;
            # partial → pro-rata), never the pay-date rate —
            # pay-date relief leaves the drift as a permanent
            # phantom A/R on a settled invoice while the same drift
            # is also booked as the explicit FX split.
            if (
                post_acct.commodity != inv.currency
                and remaining_before_pay > 0
            ):
                lot_qty_remaining = self._calculate_lot_quantity(
                    lot_obj
                )
                qty_owed = (
                    -lot_qty_remaining if effective_is_bill
                    else lot_qty_remaining
                )
                if full_settle_amount == remaining_before_pay:
                    full_settle_post_qty = qty_owed
                else:
                    full_settle_post_qty = (
                        qty_owed * full_settle_amount
                        / remaining_before_pay
                    ).quantize(_commodity_quantum(post_acct.commodity))

            # Sign-factored split data: a bill payment (or customer
            # credit-note refund) debits A/P and credits bank; the
            # customer receipt is the exact mirror. ONE set of
            # values feeds both the rehearsal's proposed-splits
            # table and the real Split construction below — the
            # rehearsal cannot diverge from the booking.
            sgn = 1 if effective_is_bill else -1
            proposed: list[dict] = [
                {
                    "account": post_acct.fullname,
                    "value": sgn * full_settle_amount,
                    "quantity": sgn * full_settle_post_qty,
                    "memo": "",
                    "action": "Payment",
                },
                {
                    "account": pay_acct.fullname,
                    "value": -sgn * payment_amount,
                    "quantity": -sgn * pay_quantity,
                    "memo": memo,
                },
            ]
            if discount_booked:
                proposed.append({
                    "account": discount_acct.fullname,
                    "value": disc_value_sign * discount_amount_invoice_ccy,
                    "quantity": disc_value_sign * disc_quantity,
                    "memo": "Early-payment discount",
                })

            # Realized FX gain/loss on post→pay rate drift, factored
            # into _compute_fx_gain_loss (the four sign quadrants
            # are unit-testable there). The split is value=0 in the
            # transaction currency, non-zero quantity in the FX
            # account's commodity (book default). One account both
            # directions; sign decides gain vs loss.
            fx_result: dict | None = None
            default_currency = self._require_default_currency(book)
            if exchange_rate is not None:
                # Direction follows THIS payment's effective_is_bill
                # — a customer credit-note refund behaves like a
                # bill payment (cash leaving on the bank side).
                fx_result = self._compute_fx_gain_loss(
                    book,
                    inv=inv,
                    is_bill=effective_is_bill,
                    pay_acct=pay_acct,
                    payment_amount=payment_amount,
                    pay_quantity=pay_quantity,
                    parsed_date=parsed_date,
                    exchange_rate=exchange_rate,
                    fx_account=fx_account,
                    default_currency=default_currency,
                    discount_amount=discount_amount_invoice_ccy,
                    discount_quantity=(
                        disc_quantity if discount_booked else None
                    ),
                    discount_commodity=(
                        discount_acct.commodity
                        if discount_acct is not None else None
                    ),
                    dry_run=dry_run,
                )
                if fx_result is not None:
                    proposed.append({
                        "account": fx_result["fx_acct"].fullname,
                        "value": Decimal("0"),
                        "quantity": fx_result["quantity"],
                        "memo": fx_result["memo"],
                    })

            # Shared result tail — identical blocks on the rehearsal
            # and the booked response, built from the same locals.
            def _attach_extras(result: dict) -> None:
                if exchange_rate is not None:
                    result["exchange_rate"] = str(exchange_rate)
                    result["payment_account_amount"] = str(pay_quantity)
                    result["invoice_currency"] = inv.currency.mnemonic
                    result["payment_account_currency"] = (
                        pay_acct.commodity.mnemonic
                    )
                    if fx_result is not None:
                        fx_diff_default = fx_result["fx_diff_default"]
                        # Label follows effective_is_bill (the
                        # direction the split was booked with) —
                        # keyed on is_bill, an FX loss on a customer
                        # credit-note refund would be labeled "gain".
                        direction = (
                            "loss"
                            if (effective_is_bill and fx_diff_default > 0)
                            or (
                                not effective_is_bill
                                and fx_diff_default < 0
                            )
                            else "gain"
                        )
                        result["fx_realized"] = {
                            # In the book default — the FX account's
                            # commodity.
                            "amount": str(
                                abs(fx_diff_default).quantize(
                                    _commodity_quantum(default_currency)
                                )
                            ),
                            "currency": default_currency.mnemonic,
                            "direction": direction,
                            "account": fx_result["fx_acct"].fullname,
                        }
                        if fx_result["fx_notice"] is not None:
                            result["fx_notice"] = fx_result["fx_notice"]
                if discount_booked:
                    # ``account`` is the canonical path — the resolver
                    # may have picked an account the caller didn't pass.
                    result["discount"] = {
                        "amount": str(discount_amount_invoice_ccy),
                        "currency": inv.currency.mnemonic,
                        "account": discount_acct.fullname,
                    }
                    if discount_notice is not None:
                        result["discount_notice"] = discount_notice
                # Surface the worst-aged forced FX override (if any).
                if fx_stale_overrides:
                    result["fx_stale"] = max(
                        fx_stale_overrides, key=lambda m: m["age_days"]
                    )

            doc_type = (
                "credit_note"
                if is_credit_note
                else self._OWNER_TYPE_TO_RESPONSE_TYPE.get(
                    inv.owner_type, "invoice"
                )
            )

            # ── Rehearsal exit: everything above ran; nothing books.
            # Projected remaining derives from the same directional
            # balance the overpayment guard used (invoice currency,
            # positive = still owed).
            if dry_run:
                remaining_after = (
                    remaining_before_pay - full_settle_amount
                )
                result = {
                    "dry_run": True,
                    "id": inv.id,
                    "type": doc_type,
                    "status": "would_pay",
                    "amount": str(payment_amount),
                    "remaining_balance_after": str(remaining_after),
                    "would_close_lot": remaining_after == Decimal(0),
                    "payment_account": pay_acct.fullname,
                    "payment_date": str(parsed_date),
                    "proposed_splits": [
                        {
                            k: (str(v) if isinstance(v, Decimal) else v)
                            for k, v in row.items()
                        }
                        for row in proposed
                    ],
                }
                would_create = sorted(
                    a.fullname
                    for a in (
                        discount_acct,
                        fx_result["fx_acct"] if fx_result else None,
                    )
                    if a is not None and getattr(a, "planned", False)
                )
                if would_create:
                    result["would_create_accounts"] = would_create
                _attach_extras(result)
                return result

            ar_ap_split = piecash.Split(
                account=post_acct,
                value=proposed[0]["value"],
                quantity=proposed[0]["quantity"],
                memo="",
                action="Payment",
            )
            bank_split = piecash.Split(
                account=pay_acct,
                value=proposed[1]["value"],
                quantity=proposed[1]["quantity"],
                memo=memo,
                action="Payment",
            )
            splits = [ar_ap_split, bank_split]
            if discount_split is not None:
                splits.append(discount_split)
            if fx_result is not None:
                splits.append(fx_result["split"])

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

            # Direction-normalized: positive = still owed. Never
            # abs() a lot balance — if some other path left it
            # negative, a credit must surface as negative rather
            # than masquerade as money owed.
            remaining_directional = (
                -remaining if effective_is_bill else remaining
            )

            result = {
                "id": inv.id,
                "type": doc_type,
                # "paid" only when the lot is settled — a partial
                # payment reporting "paid" invites the caller to
                # stop collecting with a balance still open.
                "status": (
                    "partial" if remaining_directional > 0 else "paid"
                ),
                "amount_paid": str(payment_amount),
                "remaining_balance": str(remaining_directional),
                # Transaction GUID emitted as a short prefix —
                # consumers (e.g. get_transaction lookup) accept
                # 8+ char prefixes via _resolve_guid.
                "transaction_guid": _unique_prefix(
                    txn.guid,
                    self._transaction_prefix_map(book).keys(),
                ),
                "payment_account": pay_acct.fullname,
                "payment_date": str(parsed_date),
            }
            _attach_extras(result)

        return result

    def apply_credit_note(
        self,
        credit_note_id: str,
        applies_to_invoice_id: str,
        amount: str | None = None,
        apply_date: str | None = None,
        owner_type: str | None = None,
    ) -> dict:
        """Net a posted credit note against a posted invoice/bill
        from the same owner. No cash moves — the credit transfers
        between lots on the same A/R or A/P account.

        This is GnuCash desktop's "Process Payment with both
        checked" workflow as a dedicated tool. ``pay_invoice``
        handles the cash-refund case.

        Booking shape: two splits on the post account, summing to
        zero — one settles the credit note's lot, the other reduces
        the target's. Customer side debits the CN lot / credits the
        target lot; vendor side is symmetric.

        Validation: both posted; same owner; same currency; same
        post account; target not itself a credit note; ``amount``
        (when given) within the lesser of the two remaining
        balances. Defaults to applying as much as possible.

        Returns:
            Dict with ``credit_note_id``, ``applies_to_invoice_id``,
            ``amount_applied``, ``credit_note_remaining``,
            ``target_remaining``, ``transaction_guid``,
            ``apply_date``, ``status='applied'``.

        Raises:
            ValueError: any validation failure, or ``amount``
                quantizing to zero in the post-account commodity.
        """
        from datetime import datetime as _dt
        # piecash Transaction.post_date wants a ``date`` object, not
        # a ``datetime`` — passing a datetime raises GncValidationError.
        parsed_date = (
            _dt.strptime(apply_date, "%Y-%m-%d").date()
            if apply_date else _dt.now().date()
        )

        with self.open(readonly=False) as book:
            # Resolve the credit note (validates it IS a credit note).
            cn = self._resolve_credit_note(
                book, credit_note_id, owner_type=owner_type,
            )
            # Resolve the target — use the credit note's owner_type
            # to disambiguate (they must match anyway).
            target = self._find_invoice(
                book, applies_to_invoice_id,
                owner_type=cn.owner_type,
            )
            if not target:
                raise ValueError(
                    f"Target {self._doc_label_for(cn.owner_type).lower()} "
                    f"not found: {applies_to_invoice_id}"
                )

            # Target must NOT be a credit note itself — netting
            # credits against credits is semantically meaningless.
            if self._get_is_credit_note(target):
                raise ValueError(
                    f"Target {applies_to_invoice_id} is itself a "
                    f"credit note. Apply credit notes to regular "
                    f"invoices/bills, not to each other."
                )

            # Both must be posted (no lots to net otherwise).
            if not _is_invoice_posted(cn):
                raise ValueError(
                    f"Credit note {credit_note_id} is not posted "
                    f"— post it before applying."
                )
            if not _is_invoice_posted(target):
                raise ValueError(
                    f"Target {applies_to_invoice_id} is not "
                    f"posted — post it before applying credits."
                )

            # Same owner check — on the EFFECTIVE counterparty, not
            # the raw owner row. A job-attached invoice's owner_guid
            # points at the Job, so a raw comparison refuses a
            # legitimate netting while the error text (built from
            # the job-chasing helper) names the same customer on
            # both sides.
            cn_eff_ot, cn_job = self._resolve_owner_type_and_job(
                book, cn,
            )
            cn_eff_guid = cn_job.owner_guid if cn_job else cn.owner_guid
            t_eff_ot, t_job = self._resolve_owner_type_and_job(
                book, target,
            )
            t_eff_guid = t_job.owner_guid if t_job else target.owner_guid
            if (cn_eff_ot, cn_eff_guid) != (t_eff_ot, t_eff_guid):
                cn_owner = self._find_invoice_owner_by_guid(
                    book, cn_eff_ot, cn_eff_guid,
                )
                target_owner = self._find_invoice_owner_by_guid(
                    book, t_eff_ot, t_eff_guid,
                )
                # Owner IDs are per-type sequences: customer 000005
                # and vendor 000005 are different entities with the
                # same ID string. Without the type words this error
                # can read "belongs to '000005' but target belongs
                # to '000005'" — a contradiction.
                _kind = {2: "customer", 4: "vendor", 5: "employee"}
                raise ValueError(
                    f"Credit note belongs to "
                    f"{_kind.get(cn_eff_ot, 'owner')} "
                    f"{(cn_owner.id if cn_owner else '?')!r}"
                    f"{f' ({cn_owner.name})' if cn_owner else ''} "
                    f"but target belongs to "
                    f"{_kind.get(t_eff_ot, 'owner')} "
                    f"{(target_owner.id if target_owner else '?')!r}"
                    f"{f' ({target_owner.name})' if target_owner else ''}. "
                    f"Credit notes can only net against documents "
                    f"from the same customer/vendor."
                )

            # Same currency check.
            if cn.currency_guid != target.currency_guid:
                raise ValueError(
                    f"Credit note currency "
                    f"{cn.currency.mnemonic!r} doesn't match "
                    f"target currency {target.currency.mnemonic!r}. "
                    f"Cross-currency netting would create an FX "
                    f"adjustment outside GnuCash's tracking."
                )

            # Same A/R or A/P account.
            if cn.post_acc_guid != target.post_acc_guid:
                raise ValueError(
                    f"Credit note posted to "
                    f"{cn.post_account.fullname!r} but target "
                    f"posted to {target.post_account.fullname!r}. "
                    f"Both must use the same A/R or A/P account "
                    f"to net."
                )

            post_acct = cn.post_account

            # The netting transaction is in the post account's
            # commodity, so the document currency must match it.
            # post_invoice refuses this pairing since v1.5.0
            # (battery ruling 1), but books that posted under the
            # warning-era permissiveness still carry mismatched
            # lots — this guard is what protects them, so it stays.
            if cn.currency_guid != post_acct.commodity.guid:
                raise ValueError(
                    f"Cross-currency apply not supported: credit "
                    f"note currency {cn.currency.mnemonic!r} "
                    f"differs from post account commodity "
                    f"{post_acct.commodity.mnemonic!r}. The "
                    f"netting transaction must be in the post "
                    f"account's commodity. Most books have "
                    f"per-currency A/R accounts; check that the "
                    f"credit note was posted to the right one."
                )

            # Resolve lots by GUID — same pattern as pay_invoice.
            cn_lot = next(
                (l for l in post_acct.lots if l.guid == cn.post_lot_guid),
                None,
            )
            target_lot = next(
                (l for l in post_acct.lots if l.guid == target.post_lot_guid),
                None,
            )
            if cn_lot is None or target_lot is None:
                raise ValueError(
                    f"Could not resolve lots for credit note "
                    f"or target — book may be inconsistent."
                )

            # Signed lot balances (A/R positive, A/P negative);
            # abs() gives the available amount on each side.
            cn_remaining = abs(self._calculate_lot_balance(cn_lot))
            target_remaining = abs(
                self._calculate_lot_balance(target_lot)
            )

            if cn_remaining == 0:
                raise ValueError(
                    f"Credit note {credit_note_id} has no "
                    f"remaining balance to apply (already fully "
                    f"netted or refunded)."
                )
            if target_remaining == 0:
                raise ValueError(
                    f"Target {applies_to_invoice_id} has no "
                    f"remaining balance — nothing to apply credit "
                    f"against."
                )

            max_apply = min(cn_remaining, target_remaining)
            if amount is None:
                apply_amount = max_apply
            else:
                apply_amount = _to_decimal(amount)
                if apply_amount <= 0:
                    raise ValueError(
                        f"Apply amount must be positive, got "
                        f"{apply_amount}."
                    )
                if apply_amount > max_apply:
                    raise ValueError(
                        f"Apply amount {apply_amount} exceeds the "
                        f"smaller of credit-note remaining "
                        f"({cn_remaining}) and target remaining "
                        f"({target_remaining}). Use {max_apply} "
                        f"or less."
                    )

            # Sub-quantum amounts ("0.001" on a 0.01-quantum
            # account) would produce a no-op netting transaction
            # that reports success. Same guard shape as pay_invoice.
            quantum_pre = _commodity_quantum(post_acct.commodity)
            apply_amount = apply_amount.quantize(quantum_pre)
            if apply_amount == 0:
                raise ValueError(
                    f"Apply amount quantizes to zero in "
                    f"{post_acct.commodity.mnemonic} "
                    f"(quantum={quantum_pre}). Pass an amount "
                    f"at or above the account's smallest "
                    f"divisible unit."
                )

            # Customer side: +apply on the CN lot (settles toward
            # zero), −apply on the target lot. Vendor side symmetric.
            is_bill_side = self._is_bill_side(cn_eff_ot)
            if is_bill_side:
                cn_split_value = -apply_amount
                target_split_value = apply_amount
            else:
                cn_split_value = apply_amount
                target_split_value = -apply_amount

            # Quantize to the post-account's commodity.
            quantum = _commodity_quantum(post_acct.commodity)
            cn_split_value = cn_split_value.quantize(quantum)
            target_split_value = target_split_value.quantize(quantum)

            txn_desc = (
                f"Credit applied: {credit_note_id} → "
                f"{applies_to_invoice_id}"
            )
            cn_split = piecash.Split(
                account=post_acct,
                value=cn_split_value,
                quantity=cn_split_value,
                memo=f"Net against {applies_to_invoice_id}",
                action="Payment",
            )
            target_split = piecash.Split(
                account=post_acct,
                value=target_split_value,
                quantity=target_split_value,
                memo=f"Credit from {credit_note_id}",
                action="Payment",
            )

            txn = piecash.Transaction(
                currency=post_acct.commodity,
                description=txn_desc,
                post_date=parsed_date,
                num="",
                splits=[cn_split, target_split],
            )

            # Assign splits to the respective lots — this is what
            # tells GnuCash the lots are being settled.
            cn_split.lot = cn_lot
            target_split.lot = target_lot

            book.flush()

            # Payment-type marker (GnuCash UI convention).
            txn["trans-txn-type"] = "P"

            # Close whichever lots reached zero.
            new_cn_remaining = abs(
                self._calculate_lot_balance(cn_lot)
            )
            new_target_remaining = abs(
                self._calculate_lot_balance(target_lot)
            )
            if new_cn_remaining == 0:
                cn_lot.is_closed = -1
            if new_target_remaining == 0:
                target_lot.is_closed = -1

            book.save()

            # Quantize response amounts to the post-account
            # commodity — "$100.00", not "$100".
            result = {
                "credit_note_id": credit_note_id,
                "applies_to_invoice_id": applies_to_invoice_id,
                "amount_applied": str(apply_amount.quantize(quantum)),
                "credit_note_remaining": str(
                    new_cn_remaining.quantize(quantum)
                ),
                "target_remaining": str(
                    new_target_remaining.quantize(quantum)
                ),
                # Short prefix — see post_invoice.
                "transaction_guid": _unique_prefix(
                    txn.guid,
                    self._transaction_prefix_map(book).keys(),
                ),
                "apply_date": str(parsed_date),
                "status": "applied",
            }
            # The stored applies-to link is provenance, not a
            # constraint — netting against whatever's open next is
            # the normal flow. When the applied target diverges
            # from the link, say so in the moment (and in the
            # audit log) rather than leaving it discoverable later.
            linked = self._resolve_applies_to(book, cn)
            if linked and linked["id"] != target.id:
                result["note"] = (
                    f"applied to {target.id} (credit note "
                    f"references {linked['id']})"
                )
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

    def delete_voucher(self, voucher_id: str) -> dict:
        """Delete an unposted employee expense voucher.

        Automatically removes associated entries. Posted vouchers
        cannot be deleted — unpost first via ``unpost_document``,
        then delete.

        Args:
            voucher_id: Voucher ID (e.g., '000001').

        Returns:
            Dict with id, guid, entries_deleted, status.

        Raises:
            ValueError: If voucher not found or is posted.
        """
        return self._delete_invoice_or_bill(voucher_id, owner_type=5)

    def _delete_invoice_or_bill(self, doc_id: str, owner_type: int) -> dict:
        """Shared implementation for delete_invoice / delete_bill /
        delete_voucher.

        owner_type 2 (invoice) uses the ``invoice`` entry FK; 4
        (bill) and 5 (voucher) both use the ``bill`` entry FK —
        GnuCash's schema collapses bill-side semantics into one
        column group.
        """
        from sqlalchemy import func, select
        from piecash.business.invoice import Entry, Invoice

        from gnucash_mcp.book._base import _verify_delete

        _TYPE_LABELS = {2: "Invoice", 4: "Bill", 5: "Voucher"}
        type_label = _TYPE_LABELS[owner_type]
        # Bills (4) and vouchers (5) both write to the ``bill`` FK
        # column on entries — see ``_add_entry`` is_bill_side.
        entry_fk = "invoice" if owner_type == 2 else "bill"
        entry_fk_col = getattr(Entry.__table__.c, entry_fk)

        with self.open(readonly=False) as book:
            inv = self._find_invoice(book, doc_id, owner_type=owner_type)
            if not inv:
                raise ValueError(f"{type_label} not found: {doc_id}")

            if _is_invoice_posted(inv):
                raise ValueError(
                    f"Cannot delete posted {type_label.lower()} '{doc_id}'. "
                    f"Unpost it first (unpost_document), or issue a "
                    f"credit note (create_document with "
                    f"document_type='credit_note') to reverse it."
                )

            inv_guid = inv.guid

            # Count first so the response reports how many entries
            # were removed; then delete via Core.
            entry_count = book.session.execute(
                select(func.count())
                .select_from(Entry.__table__)
                .where(entry_fk_col == inv_guid)
            ).scalar()
            if entry_count:
                # Decrement stored Taxtable.refcount per-table before
                # deleting tax-bearing entries (desktop interop only;
                # our logic uses live SQL counts). Both i_/b_ columns
                # checked defensively.
                from sqlalchemy import text
                tax_refs = book.session.execute(
                    text(
                        "SELECT taxtable_guid, COUNT(*) AS n FROM ("
                        "  SELECT i_taxtable AS taxtable_guid FROM entries "
                        f"  WHERE {entry_fk} = :guid AND i_taxtable IS NOT NULL "
                        "  UNION ALL "
                        "  SELECT b_taxtable AS taxtable_guid FROM entries "
                        f"  WHERE {entry_fk} = :guid AND b_taxtable IS NOT NULL "
                        ") AS refs GROUP BY taxtable_guid"
                    ),
                    {"guid": inv_guid},
                ).fetchall()
                for ref in tax_refs:
                    # Deliberately NOT paired with a _verify_*: the
                    # stored refcount has no in-process consumer
                    # (checks use _compute_taxtable_refcount's live
                    # COUNT), and MAX(0, …) makes a miss a harmless
                    # no-op. The write-verification contract test
                    # scans Table.__table__ DML, not raw text() —
                    # this site is deliberately outside its scope.
                    book.session.execute(
                        text(
                            "UPDATE taxtables "
                            "SET refcount = MAX(0, refcount - :n) "
                            "WHERE guid = :guid"
                        ),
                        {"n": ref.n, "guid": ref.taxtable_guid},
                    )

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

            # Slots (credit-note flag, applies-to link, date slots)
            # have no ON DELETE CASCADE on obj_guid — clean up
            # explicitly, same as _delete_business_person.
            from piecash.kvp import Slot

            book.session.execute(
                Slot.__table__.delete().where(
                    Slot.__table__.c.obj_guid == inv_guid
                )
            )
            _verify_delete(
                book.session,
                Slot.__table__,
                {"obj_guid": inv_guid},
                f"Slots for {type_label.lower()} '{doc_id}'",
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

        Runs the caller's ``dependency_check(book, entity_guid)``
        (raises ValueError to block deletion), cleans up the
        ``slots`` table explicitly (business-person rows accumulate
        slots and there's no ON DELETE CASCADE on ``obj_guid``),
        then ORM-deletes the entity.

        Args:
            entity_label: "Customer" / "Vendor" / "Employee" — error
                messages and the response ``type`` key (lowercased).
            find_entity_method: Finder method name on ``self``.
            dependency_check: Optional; None deletes unconditionally.

        Returns:
            ``{id, name, type, status}``
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

            # ``guid`` omitted from delete responses too.
            return {
                "id": entity_id,
                "name": entity_name,
                "type": entity_label.lower(),
                "status": "deleted",
            }

    @staticmethod
    def _invoice_dependency_check(
        entity_label: str, owner_type: int, doc_label: str,
    ):
        """Build a dependency_check callback for business-person
        delete: raises ValueError (posted/unposted-specific wording)
        if any Invoice rows match the owner_type.
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
            Dict with id, name, type, status.

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
            Dict with id, name, type, status.

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
            Dict with id, name, type, status.

        Raises:
            ValueError: If employee not found.
        """
        return self._delete_business_person(
            entity_id=employee_id,
            entity_label="Employee",
            find_entity_method="_find_employee",
            # Employees own nothing in 1.3.0 — no dependency check.
        )

    # ── Job CRUD ─────────────────────────────────────────────────
    #
    # Jobs are a customer/vendor-level grouping over invoices or
    # bills. Three things make them simpler than the other
    # business entities we've built:
    #
    # 1. The ``piecash.Job(name, owner, reference, active)``
    #    constructor is OPEN — unlike Invoice / Customer / Vendor
    #    / Employee, which we wrestled into raw SQL inserts. We
    #    use the ORM directly here.
    # 2. The constructor auto-assigns the 6-digit ID via
    #    ``on_book_add`` calling ``_assign_id`` (advances
    #    ``counter_job``). No manual counter handling.
    # 3. Jobs have no posted state — only ``active`` (1) /
    #    ``inactive`` (0). No post / unpost / pay tools. The
    #    financial lifecycle remains on the linked invoices.
    #
    # Employee jobs are deliberately not supported — piecash's
    # ``PersonType`` map only has ``Customer: 2`` and
    # ``Vendor: 4``; passing an Employee to ``Job()`` would
    # raise ``KeyError``. Same desktop-parity principle as
    # credit notes (no GnuCash UI for them).

    def create_job(
        self,
        owner_id: str,
        owner_type: str,
        name: str,
        reference: str = "",
    ) -> dict:
        """Create a job for a customer or vendor.

        A job groups documents from one counterparty under a
        project-level container — organizational only, no posted
        state. Employees are not supported (see the Job CRUD
        section header).

        Args:
            owner_id: Customer or vendor ID (e.g., '000001').
            owner_type: 'customer' or 'vendor'.
            name: Human-readable job name (e.g., 'API Rewrite').
            reference: Optional reference string (PO number, etc.).

        Raises:
            ValueError: invalid/employee owner_type, or owner not
                found.
        """
        from piecash.business.invoice import Job

        int_owner_type = self._parse_owner_type(owner_type)
        if int_owner_type == 5:
            raise ValueError(
                "Jobs are not supported for employees. GnuCash "
                "desktop has no UI for employee jobs (piecash's "
                "PersonType map has only Customer and Vendor), "
                "so creating one here would produce a document "
                "desktop users can't view or edit. Job-track "
                "employee work via vouchers' notes field instead."
            )
        if int_owner_type not in (2, 4):
            raise ValueError(
                f"Jobs require owner_type 'customer' or 'vendor', "
                f"got {owner_type!r}."
            )

        find_owner = (
            self._find_customer
            if int_owner_type == 2
            else self._find_vendor
        )
        with self.open(readonly=False) as book:
            owner = find_owner(book, owner_id)
            if not owner:
                label = (
                    "Customer" if int_owner_type == 2 else "Vendor"
                )
                raise ValueError(f"{label} not found: {owner_id}")

            # The Job constructor handles owner_type/owner_guid and
            # auto-id (advances counter_job); piecash adds it to the
            # book via the owner relationship — no session.add needed.
            job = Job(
                name=name,
                owner=owner,
                reference=reference,
                active=1,
            )

            book.save()

            # Row-landed check, consistent with the module's
            # raw-SQL verification posture.
            from gnucash_mcp.book._base import _verify_write
            _verify_write(
                book.session, Job.__table__, job.guid,
                f"Job '{name}'",
            )

            # ``guid`` omitted — jobs addressed by ``id``.
            return {
                "id": job.id,
                "name": name,
                "reference": reference,
                "active": True,
                "owner_type": owner_type,
                "status": "created",
            }

    def list_jobs(
        self,
        owner_type: str | None = None,
        owner_id: str | None = None,
        active_only: bool = True,
        compact: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> dict | str:
        """List jobs, optionally filtered by owner_type and/or
        owner_id.

        Leads with a ``Showing X-Y of Z jobs`` indicator; page with
        ``offset``.

        Args:
            owner_type: 'customer' or 'vendor'; omit for all.
            owner_id: Requires owner_type (customer and vendor IDs
                share a sequence space).
            active_only: If True (default), exclude inactive jobs.
            compact: One line per job (default) or verbose envelope.
            limit: Page size (default 50, max 250). 0 = count only.
            offset: 0-indexed first row to return.
        """
        from piecash.business.invoice import Job

        int_owner_type = (
            self._parse_owner_type(owner_type) if owner_type else None
        )
        if int_owner_type == 5:
            # Surface the no-employee-jobs constraint at the filter
            # boundary too, rather than silently returning [].
            raise ValueError(
                "Jobs are not supported for employees; "
                "owner_type='employee' has no jobs to list."
            )

        if owner_id and not owner_type:
            raise ValueError(
                "owner_id requires owner_type to be set — "
                "customer and vendor IDs share a sequence space, "
                "so disambiguation is required."
            )

        with self.open() as book:
            query = book.session.query(Job)
            if int_owner_type is not None:
                query = query.filter(Job.owner_type == int_owner_type)
            if active_only:
                query = query.filter(Job.active == 1)
            if owner_id:
                find_owner = (
                    self._find_customer
                    if int_owner_type == 2
                    else self._find_vendor
                )
                owner = find_owner(book, owner_id)
                if not owner:
                    label = (
                        "Customer" if int_owner_type == 2
                        else "Vendor"
                    )
                    raise ValueError(
                        f"{label} not found: {owner_id}"
                    )
                query = query.filter(Job.owner_guid == owner.guid)

            jobs = sorted(query.all(), key=lambda j: j.id)

            page, indicator = _paginate(
                jobs, offset=offset, limit=limit, entity_name="jobs",
            )
            if compact:
                lines = [indicator]
                for job in page:
                    owner = self._find_invoice_owner_by_guid(
                        book, job.owner_type, job.owner_guid,
                    )
                    lines.append(
                        self._job_to_compact_line(
                            job, owner_name=(
                                owner.name if owner else None
                            ),
                        )
                    )
                return "\n".join(lines)

            results = []
            for job in page:
                owner = self._find_invoice_owner_by_guid(
                    book, job.owner_type, job.owner_guid,
                )
                results.append(
                    self._job_to_dict(
                        job,
                        owner_name=owner.name if owner else None,
                    )
                )
            return {
                "showing": indicator,
                "total": len(jobs),
                "offset": offset,
                "count": len(page),
                "jobs": results,
            }

    def get_job(self, job_id: str) -> dict:
        """Get a job's details by ID.

        Returns:
            Dict with guid, id, name, reference, active,
            owner_type, owner_name, plus a ``linked_invoices``
            summary (count + IDs) so the caller can see what's
            attached without a separate ``list_invoices`` call.

        Raises:
            ValueError: If job not found.
        """
        from piecash.business.invoice import Invoice, Job
        with self.open() as book:
            job = self._find_job(book, job_id)
            if not job:
                raise ValueError(f"Job not found: {job_id}")
            owner = self._find_invoice_owner_by_guid(
                book, job.owner_type, job.owner_guid,
            )
            result = self._job_to_dict(
                job, owner_name=owner.name if owner else None,
            )
            # owner_type=3 + owner_guid=job.guid is the polymorphic
            # link. Sorted by ID for stable output.
            linked = book.session.query(Invoice).filter(
                Invoice.owner_type == 3,
                Invoice.owner_guid == job.guid,
            ).order_by(Invoice.id).all()
            result["linked_invoices"] = {
                "count": len(linked),
                "ids": [inv.id for inv in linked],
            }
            return result

    def update_job(
        self,
        job_id: str,
        name: str | None = None,
        reference: str | None = None,
        active: bool | None = None,
    ) -> dict:
        """Update a job's name, reference, or active state.

        Any subset of fields; unspecified fields unchanged.
        Diff-style response (changed fields only), same convention
        as ``update_account``.

        Raises:
            ValueError: If job not found or no fields supplied.
        """
        if name is None and reference is None and active is None:
            raise ValueError(
                "update_job requires at least one of name, "
                "reference, or active."
            )
        with self.open(readonly=False) as book:
            job = self._find_job(book, job_id)
            if not job:
                raise ValueError(f"Job not found: {job_id}")
            # Capture before-state for audit log staging.
            before = {
                "id": job.id,
                "name": job.name,
                "reference": job.reference or "",
                "active": bool(job.active),
            }
            self._stage_audit_before(before)

            changed = {}
            if name is not None and name != job.name:
                job.name = name
                changed["name"] = name
            if reference is not None and reference != (job.reference or ""):
                job.reference = reference
                changed["reference"] = reference
            if active is not None and bool(job.active) != active:
                job.active = 1 if active else 0
                changed["active"] = active

            book.save()

            # ``guid`` omitted from update responses.
            return {
                "id": job.id,
                "status": "updated",
                **changed,
            }

    def delete_job(self, job_id: str, force: bool = False) -> dict:
        """Delete a job.

        Refuses by default when documents are linked. ``force=True``
        re-parents every linked invoice back to the underlying
        customer/vendor (rewriting owner_type/owner_guid) before
        deleting, preserving invoice history.

        Returns:
            Dict with id, name, reparented_count, status.

        Raises:
            ValueError: not found, or linked documents without force.
        """
        from piecash.business.invoice import Invoice, Job
        with self.open(readonly=False) as book:
            job = self._find_job(book, job_id)
            if not job:
                raise ValueError(f"Job not found: {job_id}")
            job_name = job.name
            job_guid = job.guid
            underlying_owner_type = job.owner_type
            underlying_owner_guid = job.owner_guid

            linked = book.session.query(Invoice).filter(
                Invoice.owner_type == 3,
                Invoice.owner_guid == job.guid,
            ).all()

            reparented_count = 0
            if linked:
                if not force:
                    raise ValueError(
                        f"Job '{job_id}' has {len(linked)} linked "
                        f"invoice(s)/bill(s). Pass force=True to "
                        f"re-parent them back to the underlying "
                        f"customer/vendor before deleting the "
                        f"job, or unlink/delete the documents "
                        f"first."
                    )
                # Re-parent each linked invoice to the job's
                # underlying owner — history preserved, indirection
                # removed.
                for inv in linked:
                    inv.owner_type = underlying_owner_type
                    inv.owner_guid = underlying_owner_guid
                    reparented_count += 1

            book.session.delete(job)
            book.save()

            from gnucash_mcp.book._base import _verify_delete
            _verify_delete(
                book.session, Job.__table__, {"guid": job_guid},
                f"Job '{job_id}'",
            )

            # ``guid`` omitted from delete response.
            return {
                "id": job_id,
                "name": job_name,
                "reparented_count": reparented_count,
                "status": "deleted",
            }

    # ── Reporting ────────────────────────────────────────────────

    def get_outstanding_invoices(
        self,
        owner_type: str | None = None,
        customer_id: str | None = None,
        vendor_id: str | None = None,
        compact: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> dict | str:
        """Get all posted invoices/bills with outstanding balances.

        Leads with a ``Showing X-Y of Z invoices (date range)``
        indicator, most-overdue first; page with ``offset``.

        Args:
            owner_type: 'customer' or 'vendor'; omit for all.
            customer_id: Filter by specific customer ID.
            vendor_id: Filter by specific vendor ID.
            compact: One line per doc with action columns (due
                date, days past due, (BILL)/(CN) tags),
                most-overdue first. Verbose returns the envelope
                with the full original_amount / amount_paid /
                amount_due breakdown per doc.
            limit: Page size (default 50, max 250). 0 = count only.
            offset: 0-indexed first row to return.
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
                # One Job query feeds both the job annotation and
                # the effective side (which drives the lot-balance
                # direction below).
                is_credit_note = self._get_is_credit_note(inv)
                effective_ot, job_for_inv = (
                    self._resolve_owner_type_and_job(book, inv)
                )
                job_id_field = (
                    job_for_inv.id if job_for_inv else None
                )
                is_bill = self._is_bill_side(effective_ot)

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

                # Direction-normalize the signed lot balance to
                # "amount still owed" (credit notes: credit still
                # available). NEGATIVE means overpaid — never abs()
                # it, or an overpaid invoice renders as money owed
                # and invites double-collection.
                amount_due = -balance if (is_bill ^ is_credit_note) else balance
                overpaid = amount_due < 0 and not is_credit_note

                try:
                    grand_total = self._get_invoice_entries_and_total(
                        book, inv,
                    )["grand_total"]
                except ValueError:
                    grand_total = max(amount_due, Decimal("0"))

                # Signed arithmetic keeps amount_paid honest in the
                # overpaid case: grand 3500, due −1000 → paid 4500
                # (an abs() derivation would show paid 2500).
                amount_paid = grand_total - amount_due

                # Polymorphic owner dispatch — the direct finders
                # return None on job-attached invoices (owner_guid
                # points at a Job).
                owner = self._find_invoice_owner_by_guid(
                    book, inv.owner_type, inv.owner_guid,
                )
                owner_name = owner.name if owner else None

                posted_dt = _safe_invoice_date(inv, "date_posted")
                # Same three-step due-date chain as the warnings
                # collector. No aging clock on credit notes (nothing
                # past due about money the business owes) or
                # overpaid docs (nothing left to collect).
                due_date, no_terms = self._resolve_invoice_due_date(
                    book, inv,
                )
                days_past_due = (
                    (today - due_date).days
                    if due_date is not None
                    and not is_credit_note
                    and not overpaid
                    else None
                )
                currency = (
                    inv.currency.mnemonic if inv.currency else None
                )
                # type agrees with get_document / delete / unpost:
                # credit notes are their own type everywhere
                # (battery bug 6 — verbose mode here was the one
                # surface still tagging them by owner type). The
                # redundant is_credit_note key stays for callers
                # already keyed on it. No due_date either — credit
                # is available, not owed by a date.
                row = {
                    "id": inv.id,
                    "type": (
                        "credit_note" if is_credit_note
                        else self._OWNER_TYPE_TO_RESPONSE_TYPE.get(
                            effective_ot, "invoice"
                        )
                    ),
                    "is_credit_note": is_credit_note,
                    "owner_name": owner_name,
                    "currency": currency,
                    "date_posted": (
                        str(posted_dt.date()) if posted_dt else None
                    ),
                    "due_date": (
                        str(due_date)
                        if due_date is not None and not is_credit_note
                        else None
                    ),
                    "days_past_due": days_past_due,
                    "no_terms": False if is_credit_note else no_terms,
                    "original_amount": str(grand_total),
                    "amount_paid": str(amount_paid),
                    "amount_due": str(amount_due),
                    "job_id": job_id_field,
                }
                if overpaid:
                    row["overpaid"] = True
                results.append(row)

            # Most overdue first — urgent receivables at the top.
            results.sort(
                key=lambda r: -(r["days_past_due"] or 0),
            )

            page, indicator = _paginate(
                results,
                offset=offset,
                limit=limit,
                entity_name="documents",
                date_key=lambda r: r["date_posted"],
            )
            if compact:
                body = _format_outstanding_invoices_compact(page)
                return f"{indicator}\n{body}" if body else indicator
            return {
                "showing": indicator,
                "total": len(results),
                "offset": offset,
                "count": len(page),
                "invoices": page,
            }

    def get_job_report(self, job_id: str) -> dict:
        """Per-job summary: billed / paid / outstanding totals
        across all linked invoices, plus the per-invoice breakdown.

        **Currency**: a job's invoices can span currencies, so
        totals come back as ``totals_by_currency`` — ``{ccy:
        {billed, paid, outstanding}}`` — even for single-currency
        jobs (a consistent shape beats branching on cardinality).

        **Posted vs unposted**: both included. Posted invoices use
        their lot balance; drafts contribute face value as billed
        and outstanding with paid=0 — a job's pipeline includes
        drafts.

        Returns:
            ``{job_id, job_name, owner_type, owner_name,
            linked_invoices_count, posted_count, open_count,
            totals_by_currency, invoices}``.

        Raises:
            ValueError: If job not found.
        """
        from piecash.business.invoice import Invoice
        with self.open() as book:
            job = self._find_job(book, job_id)
            if not job:
                raise ValueError(f"Job not found: {job_id}")
            owner = self._find_invoice_owner_by_guid(
                book, job.owner_type, job.owner_guid,
            )
            owner_name = owner.name if owner else None
            owner_type = (
                "customer" if job.owner_type == 2 else "vendor"
            )
            # Lot-balance direction follows the job's side; credit
            # notes flip per-invoice below.
            job_is_bill = self._is_bill_side(job.owner_type)

            invoices = book.session.query(Invoice).filter(
                Invoice.owner_type == 3,
                Invoice.owner_guid == job.guid,
            ).order_by(Invoice.date_opened).all()

            totals_by_currency: dict[str, dict[str, Decimal]] = {}
            invoice_rows = []
            posted_count = 0
            open_count = 0

            for inv in invoices:
                ccy = inv.currency.mnemonic if inv.currency else "?"
                # Face value falls back to 0 on entry-load failure
                # rather than aborting the whole report.
                try:
                    billed = self._get_invoice_entries_and_total(
                        book, inv,
                    )["grand_total"]
                except (ValueError, AttributeError):
                    billed = Decimal("0")

                if _is_invoice_posted(inv):
                    posted_count += 1
                    # paid = billed − outstanding from the lot.
                    # Direction-normalized, NOT abs()'d: an overpaid
                    # invoice carries NEGATIVE outstanding, keeping
                    # ``paid`` honest.
                    post_acct_guid = inv.post_acc_guid
                    post_acct = book.session.query(
                        piecash.Account
                    ).filter_by(guid=post_acct_guid).first()
                    lot_obj = None
                    if post_acct:
                        for lot in post_acct.lots:
                            if lot.guid == inv.post_lot_guid:
                                lot_obj = lot
                                break
                    if lot_obj:
                        lot_balance = self._calculate_lot_balance(
                            lot_obj
                        )
                        flip = job_is_bill ^ self._get_is_credit_note(
                            inv
                        )
                        outstanding = (
                            -lot_balance if flip else lot_balance
                        )
                        paid = billed - outstanding
                    else:
                        # Lot missing despite posted state — treat
                        # as fully owed rather than crash.
                        outstanding = billed
                        paid = Decimal("0")
                    status = "posted"
                else:
                    open_count += 1
                    paid = Decimal("0")
                    outstanding = billed
                    status = "open"

                posted_dt = _safe_invoice_date(inv, "date_posted")
                opened_dt = _safe_invoice_date(inv, "date_opened")
                invoice_rows.append({
                    "id": inv.id,
                    "status": status,
                    "currency": ccy,
                    "billed": str(billed),
                    "paid": str(paid),
                    "outstanding": str(outstanding),
                    "date_opened": (
                        str(opened_dt.date()) if opened_dt else None
                    ),
                    "date_posted": (
                        str(posted_dt.date()) if posted_dt else None
                    ),
                })

                bucket = totals_by_currency.setdefault(ccy, {
                    "billed": Decimal("0"),
                    "paid": Decimal("0"),
                    "outstanding": Decimal("0"),
                })
                bucket["billed"] += billed
                bucket["paid"] += paid
                bucket["outstanding"] += outstanding

            # Stringify Decimal totals for JSON-friendly output.
            return {
                "job_id": job.id,
                "job_name": job.name,
                "owner_type": owner_type,
                "owner_name": owner_name,
                "linked_invoices_count": len(invoices),
                "posted_count": posted_count,
                "open_count": open_count,
                "totals_by_currency": {
                    ccy: {k: str(v) for k, v in bucket.items()}
                    for ccy, bucket in totals_by_currency.items()
                },
                "invoices": invoice_rows,
            }

    def _posting_split_in_default(
        self, book, split, default_currency,
    ) -> tuple[Decimal, bool]:
        """Value one posting/payment split in the book's default
        currency, trusting the ledger.

        A split stores ``quantity`` in its account's commodity and
        ``value`` in its transaction's currency. Whichever of those
        is the book default IS the base-currency amount — the rate
        was applied when the row was written, so re-deriving it from
        face value is unnecessary and re-deriving it at a *report-time*
        rate is wrong (it drifts with the rate). Only a split whose
        account AND transaction are both non-default has no stored
        base-currency amount; there a live conversion is required, and
        it uses the rate AS OF THE POSTING DATE (what was booked), not
        a report-period rate.

        Returns ``(amount, converted_ok)``; ``converted_ok`` is False
        only when a rate was required and none is on file.
        """
        if split.account.commodity == default_currency:
            return Decimal(str(split.quantity)), True
        txn_currency = split.transaction.currency
        if txn_currency == default_currency:
            return Decimal(str(split.value)), True
        # Both-foreign: no base-currency amount is stored. Convert the
        # transaction-currency value at the posting-date rate.
        rate = self._cross_rate(
            book, txn_currency, default_currency,
            as_of=split.transaction.post_date,
        )
        if rate is None:
            return Decimal(str(split.value)), False
        quantum = _commodity_quantum(default_currency)
        return (Decimal(str(split.value)) * rate).quantize(quantum), True

    def _bill_amounts_in_default(
        self, book, bill, default_currency,
    ) -> tuple[Decimal, Decimal, Decimal, str | None]:
        """Billed / paid / outstanding for one bill, in the book's
        default currency, read from the posting ledger.

        Sums the posting transaction's contra (non-A/P) splits for
        the billed total and the post-lot balance for outstanding —
        both valued via :meth:`_posting_split_in_default`, so a
        foreign-currency bill posted against a default-currency A/P
        account needs no rate at all: the CNY amounts computed at
        posting time are read straight off the ledger. ``paid`` is
        the difference. This is the same principle as the contra-split
        fix: report what the book contains, not a re-derivation of
        what it should contain.

        Returns ``(billed, paid, outstanding, unconverted_ccy)``.
        ``unconverted_ccy`` is the bill currency's mnemonic when a
        split could not be valued (no rate on file) — None on
        success; the amounts are then in that raw foreign currency
        and the caller routes them to the per-currency bucket.
        """
        converted_ok = True

        # Total billed = magnitude of the contra (non-A/P) side of
        # the posting transaction.
        billed = Decimal(0)
        txn = bill.post_txn
        if txn is not None:
            for s in txn.splits:
                if s.reconcile_state == "v":
                    continue
                if s.account.guid == bill.post_acc_guid:
                    continue
                amt, ok = self._posting_split_in_default(
                    book, s, default_currency,
                )
                converted_ok = converted_ok and ok
                billed += amt
        billed = abs(billed)

        # Outstanding = magnitude of the post-lot balance.
        outstanding = Decimal(0)
        post_acct = book.session.query(
            piecash.Account
        ).filter_by(guid=bill.post_acc_guid).first()
        if post_acct:
            for lot in post_acct.lots:
                if lot.guid != bill.post_lot_guid:
                    continue
                for s in lot.splits:
                    if s.reconcile_state == "v":
                        continue
                    amt, ok = self._posting_split_in_default(
                        book, s, default_currency,
                    )
                    converted_ok = converted_ok and ok
                    outstanding += amt
                break
        outstanding = abs(outstanding)

        paid = billed - outstanding
        unconverted = None if converted_ok else bill.currency.mnemonic
        return billed, paid, outstanding, unconverted

    def _grouped_vendor_spending(
        self,
        book,
        *,
        bills,
        start_date: date,
        end_date: date,
        group_by: str,
        default_currency,
    ) -> str:
        """Bucket total-billed per vendor into sub-period columns.

        Each bill lands in its ``date_posted`` sub-period, billed in
        the book default read off the posting ledger (see
        :meth:`_bill_amounts_in_default`). Bills that can't be valued
        (foreign A/P account, no FX rate on file) are excluded from
        the converted totals and surfaced in a trailing warning,
        exactly as the single-period report handles them.
        """
        periods = _enumerate_periods(start_date, end_date, group_by)
        period_labels = [pl for pl, _ in periods]
        label_set = set(period_labels)

        totals: dict[str, dict[str, Decimal]] = {}
        unconverted: dict[str, dict] = {}
        for bill in bills:
            posted = _safe_invoice_date(bill, "date_posted")
            if posted is None:
                continue
            plabel = _period_label(posted.date(), group_by)
            if plabel not in label_set:
                # The caller filters bills to [start, end]; a stray
                # label means that invariant broke upstream. Skip
                # rather than KeyError the period-totals pass below —
                # and leave a trace, matching the sibling guards in
                # reporting.py: a silently dropped bill is a vendor
                # total that's short with nothing to debug.
                debug_logger.warning(
                    f"group_by bill outside enumerated periods: "
                    f"{plabel}"
                )
                continue
            total, _paid, _out, unconv = self._bill_amounts_in_default(
                book, bill, default_currency,
            )

            if unconv is not None:
                u = unconverted.setdefault(
                    unconv,
                    {"total_billed": Decimal(0), "bill_count": 0},
                )
                u["total_billed"] += total
                u["bill_count"] += 1
                continue

            v = self._find_vendor_by_guid(book, bill.owner_guid)
            v_name = v.name if v else "Unknown"
            bucket = totals.setdefault(v_name, {})
            bucket[plabel] = bucket.get(plabel, Decimal(0)) + total

        row_totals = {
            name: sum(per.values(), Decimal(0))
            for name, per in totals.items()
        }
        # Billed totals are non-negative, so there is no net-negative
        # exclusion here (unlike the category breakdowns).
        displayed_names = sorted(
            row_totals, key=lambda n: row_totals[n], reverse=True,
        )
        period_totals = {pl: Decimal(0) for pl in period_labels}
        for per in totals.values():
            for pl, v in per.items():
                period_totals[pl] += v
        grand_total = sum(row_totals.values(), Decimal(0))

        out = _format_grouped_tsv(
            period_labels=period_labels,
            displayed_names=displayed_names,
            totals=totals,
            row_totals=row_totals,
            period_totals=period_totals,
            grand_total=grand_total,
            excluded=[],
            label="Vendor",
            partial_labels=_partial_period_labels(
                start_date, end_date, group_by,
            ),
        )
        if unconverted:
            mnem = default_currency.mnemonic
            for ccy, d in unconverted.items():
                out += (
                    f"\n⚠ {d['bill_count']} bill(s) in {ccy} excluded from "
                    f"{mnem} totals — no exchange rate on file "
                    f"(raw {ccy}: billed {d['total_billed']})"
                )
        return out

    def vendor_spending_report(
        self,
        start_date: str,
        end_date: str,
        vendor_id: str | None = None,
        compact: bool = True,
        group_by: str | None = None,
    ) -> dict | str:
        """Get spending breakdown by vendor for a period.

        Analyzes posted vendor bills: total billed, paid, and
        outstanding per vendor, converted to the book's default
        currency at period-end rates.

        Args:
            start_date / end_date: Period bounds (YYYY-MM-DD).
            vendor_id: Optional filter to one vendor.
            compact: Aligned text table with TOTAL row (default),
                or the structured dict.
            group_by: ``None`` (default) for the single-period
                billed/paid/outstanding view; ``"month"`` /
                ``"quarter"`` / ``"year"`` to split the range into
                sub-period columns of **total billed** per vendor and
                return a multi-period TSV table — surfaces per-vendor
                spend trends (a spike at one vendor in one month).
        """
        from piecash.business.invoice import Invoice

        if group_by is not None and group_by not in _GROUP_BY_VALUES:
            raise ValueError(
                f"Invalid group_by '{group_by}'. Must be one of: "
                f"{', '.join(_GROUP_BY_VALUES)}."
            )

        parsed_start = date.fromisoformat(start_date)
        parsed_end = date.fromisoformat(end_date)

        with self.open() as book:
            # Capture default currency for the compact formatter —
            # a hardcoded ``$`` breaks on non-USD books.
            default_currency = self._require_default_currency(book)
            default_currency_mnemonic = default_currency.mnemonic

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

            # ``_safe_invoice_date`` returns None for missing or
            # malformed date_posted; those rows drop out.
            filtered_bills = []
            for b in bills:
                posted = _safe_invoice_date(b, "date_posted")
                if posted is None:
                    continue
                if parsed_start <= posted.date() <= parsed_end:
                    filtered_bills.append(b)
            bills = filtered_bills

            if group_by is not None:
                return self._grouped_vendor_spending(
                    book,
                    bills=bills,
                    start_date=parsed_start,
                    end_date=parsed_end,
                    group_by=group_by,
                    default_currency=default_currency,
                )

            vendor_data: dict[str, dict] = {}
            # Bills that can't be valued in the book default (foreign
            # A/P account with no FX rate on file) are EXCLUDED from
            # default-currency totals (folding raw foreign units
            # corrupts the sum); tracked per currency and surfaced
            # as a warning.
            unconverted: dict[str, dict] = {}
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

                # Billed/paid/outstanding read off the posting ledger
                # in the book default — for a foreign-currency bill
                # against a default-currency A/P account this is the
                # rate-at-posting amount, never today's drifted rate.
                total, paid, outstanding, unconv = (
                    self._bill_amounts_in_default(
                        book, bill, default_currency,
                    )
                )

                if unconv is not None:
                    u = unconverted.setdefault(
                        unconv,
                        {
                            "total_billed": Decimal(0),
                            "total_paid": Decimal(0),
                            "outstanding": Decimal(0),
                            "bill_count": 0,
                        },
                    )
                    u["total_billed"] += total
                    u["total_paid"] += paid
                    u["outstanding"] += outstanding
                    u["bill_count"] += 1
                else:
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

        # Warn (once per currency) about bills excluded from the
        # default-currency totals for lack of a rate, naming the raw
        # amount so the reader knows the magnitude of what's missing.
        warnings = [
            f"{d['bill_count']} bill(s) in {mn} excluded from "
            f"{default_currency_mnemonic} totals — no exchange rate on "
            f"file (raw {mn}: billed {d['total_billed']}, outstanding "
            f"{d['outstanding']})"
            for mn, d in unconverted.items()
        ]

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
        if unconverted:
            full["unconverted"] = {
                mn: {
                    "total_billed": str(d["total_billed"]),
                    "total_paid": str(d["total_paid"]),
                    "outstanding": str(d["outstanding"]),
                    "bill_count": d["bill_count"],
                }
                for mn, d in unconverted.items()
            }
            full["warnings"] = warnings

        if not compact:
            return full

        out = _format_vendor_spending_compact(
            vendors_list,
            grand_billed=grand_billed,
            grand_paid=grand_paid,
            grand_outstanding=grand_outstanding,
            currency=default_currency_mnemonic,
        )
        if warnings:
            out += "\n" + "\n".join(f"⚠ {w}" for w in warnings)
        return out
