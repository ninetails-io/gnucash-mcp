"""CoreMixin — the foundation layer: accounts, transactions, book summary.

Every other module (reconciliation, reporting, budgets, scheduling,
investments, business, admin) presupposes the methods here. `--modules`
always adds 'core' to the enabled set.

Holds:
  - get_book_summary (one-shot orientation: accounts, balances, net
    worth, txn count, commodities, scheduled)
  - Account CRUD + list/get/balance
  - Transaction CRUD + list/get/search/replace_splits
  - The duplicate-detection / auto-fill / split-consistency pipeline
    behind create_transaction

Depends on shared helpers from BaseGnuCashBook (via MRO):
  - self.open, self.book_path
  - self._find_account, self._find_transaction, self._find_split
  - self._find_commodity, self._require_default_currency,
    self._get_or_create_currency, self._resolve_guid,
    self._collect_descendants

SchedulingMixin.create_transaction_from_scheduled calls
self.create_transaction (defined here), resolved via MRO — that is
the one extracted-to-core dependency in the whole tree.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import piecash

from gnucash_mcp.book._base import (
    _account_to_compact_line,
    _account_to_dict,
    _guid_prefix_map,
    _is_market_price,
    _split_to_compact_dict,
    _split_to_dict,
    _to_decimal,
    _transaction_to_compact_line,
    _transaction_to_dict,
    _unique_prefix,
)


@dataclass
class _CreateSignals:
    """Signals gathered in a single pass over ``book.transactions`` for
    the ``create_transaction`` preflight and post-write checks.

    Before this was consolidated, ``create_transaction`` made four
    separate helper calls — each opening the book, scanning the full
    transaction list, and producing one signal. That's ``~4 × (open +
    O(N))`` per create. On a 10k-txn book the four opens alone cost
    80–150 ms, plus ~40k iterations for the scans.

    Collecting everything in one pass turns the hot path into a single
    book-open and a single sort + O(N) traversal. Signals are opt-in
    via ``want_*`` flags so the collector does only the work each call
    actually needs (e.g., skip duplicate detection when
    ``check_duplicates=False``).

    Attributes:
        auto_fill: ``(splits_list, source_info)`` for the most recent
            description match, or ``None`` when ``want_auto_fill`` was
            False or no match was found. ``source_info`` carries the
            source transaction's short guid prefix, description, and
            date — enough for the LLM to follow up via
            ``get_transaction``.
        stability_warnings: Zero or one warning dicts. Populated only
            when recent matching-description transactions disagree on
            the categorization account pattern — meaning auto-fill is
            drawing from inconsistent history.
        duplicates: HIGH- and MEDIUM-confidence duplicate candidates,
            sorted HIGH first. LOW-confidence (single-signal) matches
            are suppressed as noise.
        recent_matches: Up to five most-recent matching-description
            piecash.Transaction objects for the post-write split-
            consistency warning. Live ORM instances — caller must read
            them while the session is open.
    """

    auto_fill: tuple[list[dict], dict] | None = None
    stability_warnings: list[dict] = field(default_factory=list)
    duplicates: list[dict] = field(default_factory=list)
    recent_matches: list = field(default_factory=list)

    @property
    def has_high_duplicate(self) -> bool:
        """True iff at least one candidate has confidence ``HIGH``."""
        return any(d["confidence"] == "HIGH" for d in self.duplicates)


class CoreMixin:
    """Accounts, transactions, and the book-summary view. Always loaded."""

    # Account types where bank-statement reconciliation is meaningful.
    # ASSET is added conditionally on a per-account basis when the
    # account has any cleared/reconciled history (brokerage cash,
    # escrow, prepaid accounts the user actually reconciles).
    _RECONCILABLE_TYPES = frozenset({"BANK", "CREDIT", "LIABILITY"})

    # Reconciliation freshness threshold. Accounts whose last
    # reconciled date is more than this many days behind today (or
    # which have never been reconciled despite having transactions)
    # earn a warning marker. Matches monthly statement cycles plus a
    # ~2-week grace period.
    _RECONCILE_WARN_DAYS = 45

    # "Last transaction" staleness threshold for the book-summary
    # warning. Beyond this many days since the most recent
    # transaction post_date, the dashboard's "Last entry" line
    # earns a ⚠. Tuned for typical entry cadences: weekly /
    # bi-weekly payroll and monthly bills will both stay current
    # under 14 days; longer gaps usually mean catch-up work is
    # pending before reconciliation makes sense.
    _LAST_ENTRY_WARN_DAYS = 14

    def _account_reconciliation_status(
        self, book: piecash.Book, accounts: list,
    ) -> list[dict]:
        """Per-account reconciliation freshness for the book summary.

        For each reconcilable account with transaction activity,
        returns a dict with::

            {
              "account": fullname,
              "status": "through YYYY-MM-DD" | "never reconciled",
              "days_behind": int | None,   # None iff "never reconciled"
            }

        The single-int "N unreconciled" count this replaces was
        operationally useless: it included income/expense/equity
        splits that conceptually can't be reconciled, so the number
        was misleadingly large and gave the LLM no actionable signal
        about which accounts had drifted from reality.

        Filtering rules:

        - Always include: BANK, CREDIT, LIABILITY (the canonical
          reconcilable types — bank accounts, credit cards, loans
          with monthly statements).
        - Conditionally include: ASSET, but only when the account
          has any 'y' or 'c' history. Catches brokerage cash, escrow,
          and prepaid accounts the user reconciles, while skipping
          investment positions and other ASSET accounts where
          reconciliation doesn't apply.
        - Always exclude: placeholder accounts, template-subtree
          accounts (scheduled-transaction scaffolding), the ROOT,
          and any account with no transaction activity at all (an
          unused account isn't "behind on reconciliation" — it
          simply hasn't been used).

        Results are sorted by fullname for deterministic output.
        Empty list = no reconcilable activity in the book; the
        caller should omit the Reconciliation section entirely
        rather than emit an empty header.
        """
        template_guids = self._template_account_guids(book)
        today = date.today()

        results: list[dict] = []
        for account in accounts:
            if account.type == "ROOT":
                continue
            if account.guid in template_guids:
                continue
            if account.placeholder:
                continue

            if account.type not in self._RECONCILABLE_TYPES \
                    and account.type != "ASSET":
                continue

            # Single pass over splits — derive everything we need:
            #   - latest_y_date (most recent reconciled split)
            #   - has_yc (any 'y' or 'c' for the ASSET gate)
            #   - any_splits (used vs. unused account)
            # Pre-fix this method walked ``account.splits`` twice:
            # once for the ASSET-passes-only-with-yc check, once
            # for the latest-y_date scan, and (in some branches)
            # a third time for the unreconciled count. One sweep
            # collects everything; the count itself can't be
            # computed up front because it depends on
            # latest_y_date, but we capture all the inputs in the
            # single pass and run the count after.
            latest_y_date = None
            has_yc = False
            any_splits = False
            for s in account.splits:
                any_splits = True
                rstate = s.reconcile_state
                if rstate in ("y", "c"):
                    has_yc = True
                if rstate == "y":
                    pd = s.transaction.post_date
                    if latest_y_date is None or pd > latest_y_date:
                        latest_y_date = pd

            # ASSET passes only when it has reconcilable history.
            # Investment positions / real estate / vehicles carry
            # no 'y' or 'c' splits and rightly skip.
            if account.type == "ASSET" and not has_yc:
                continue

            if not any_splits:
                # No activity at all — not "behind," just unused.
                continue

            # Count unreconciled splits past the last 'y' date (or
            # all of them when never reconciled). This becomes the
            # "47 splits unreconciled since DATE" payload — the LLM
            # uses the count to plan the reconciliation pass: 12
            # splits is a single sitting, 400 is "let's narrow by
            # month." 'c' (cleared) splits count as unreconciled
            # for this purpose; they're not finalized.
            if latest_y_date is None:
                unreconciled_count = sum(
                    1 for s in account.splits
                    if s.reconcile_state != "y"
                )
                results.append({
                    "account": account.fullname,
                    "status": "never reconciled",
                    "days_behind": None,
                    "unreconciled_count": unreconciled_count,
                })
            else:
                days_behind = (today - latest_y_date).days
                unreconciled_count = sum(
                    1 for s in account.splits
                    if s.reconcile_state != "y"
                    and s.transaction.post_date > latest_y_date
                )
                results.append({
                    "account": account.fullname,
                    "status": f"through {latest_y_date.isoformat()}",
                    "days_behind": days_behind,
                    "unreconciled_count": unreconciled_count,
                    "latest_y_date": latest_y_date.isoformat(),
                })

        results.sort(key=lambda r: r["account"])
        return results

    # Asset-side and liability-side type sets used by the net-worth
    # computation. Mirrors the existing in-summary breakdown — the
    # asset section iterates these types into per-leaf rows; the
    # liability section iterates the liability set. Receivables and
    # payables are intentionally excluded from net worth — they live
    # in their own dedicated sections of the summary and aren't part
    # of the assets_total − liabilities_total convention.
    _NW_ASSET_TYPES = frozenset({"ASSET", "BANK", "CASH", "STOCK", "MUTUAL"})
    _NW_LIABILITY_TYPES = frozenset({"LIABILITY", "CREDIT"})

    def _compute_net_worth_at(
        self,
        book: piecash.Book,
        as_of: date,
        default_currency: piecash.Commodity,
        accounts: list,
    ) -> Decimal:
        """Net worth in book-default currency as of ``as_of``.

        Single source of truth for the net-worth number the summary
        displays. Trajectory's "now" anchor and the (former) bottom-
        line "Net worth:" line both agreed-by-construction when both
        existed; the bottom-line has since been retired in favor of
        the trajectory's "now", but this helper preserves the
        semantics so the user's reference number stays the same.

        Algorithm mirrors the per-leaf breakdown elsewhere in
        ``get_book_summary``:

        - **Leaf-only iteration.** Parents with children are skipped
          (their balances are already represented by their children).
          Matches the per-leaf display structure of the assets and
          liabilities sections.
        - **Skips:** ROOT, template-subtree accounts, placeholders.
        - **Asset accounts** (ASSET / BANK / CASH / STOCK / MUTUAL):
          balance × most-recent-rate-on-or-before-``as_of``. If the
          commodity has no price by ``as_of``, fall back to cost
          basis (sum of split.value, which is in transaction
          currency = book default for typical USD-denominated buys).
          That fallback is the same one the assets-section
          ``_market_value`` helper uses; preserves user-expected
          numbers for unpriced foreign holdings.
        - **Liability accounts** (LIABILITY / CREDIT): subtract the
          raw balance from net worth. Liabilities are stored as
          negative balances; ``-balance`` gives the positive
          liability magnitude that's then subtracted. No conversion —
          matches the existing bottom-line behavior, which assumes
          liabilities denominated in default currency (the common
          case for personal books).

        Receivables and payables: excluded from the result because
        they have their own sections in the summary and aren't part
        of the canonical assets_total − liabilities_total formula.
        """
        template_guids = self._template_account_guids(book)
        # "Now" anchors (as_of >= today) use the absolute latest price
        # on file — including any future-dated forecasts the bookkeeper
        # has deliberately written. Past anchors filter to prices
        # observed by the anchor date (historical reconstruction).
        # See the comment in get_book_summary's inline price loop for
        # the rationale; both paths converge on this behavior so the
        # "now" anchor agrees with balance_sheet by construction.
        if as_of >= date.today():
            rates = self._rates_as_of(book)
        else:
            rates = self._rates_as_of(book, as_of, default_currency)

        # is_leaf: an account with no children. Compute the parent
        # set once and check membership per account.
        parent_guids: set[str] = set()
        for a in accounts:
            if a.parent and a.parent.type != "ROOT":
                parent_guids.add(a.parent.guid)

        assets_total = Decimal("0")
        liabilities_total = Decimal("0")

        for account in accounts:
            if account.type == "ROOT":
                continue
            if account.guid in template_guids:
                continue
            if account.placeholder:
                continue
            if account.guid in parent_guids:
                continue

            if account.type not in self._NW_ASSET_TYPES \
                    and account.type not in self._NW_LIABILITY_TYPES:
                continue

            balance = Decimal("0")
            for split in account.splits:
                if split.transaction.post_date <= as_of:
                    balance += split.quantity

            if account.type in self._NW_ASSET_TYPES:
                if balance == 0:
                    continue
                if account.commodity == default_currency:
                    assets_total += balance
                else:
                    rate = rates.get(account.commodity.guid)
                    if rate is not None:
                        assets_total += balance * rate
                    else:
                        # Cost-basis fallback: split values are in
                        # transaction currency (= book default for
                        # typical purchases of foreign-commodity
                        # assets). Approximation but the same one
                        # the existing summary uses.
                        cost_basis = Decimal("0")
                        for split in account.splits:
                            if split.transaction.post_date <= as_of:
                                cost_basis += Decimal(str(split.value))
                        assets_total += cost_basis
            else:
                # Liability bucket. Negate to get positive magnitude;
                # subtract from net worth via liabilities_total.
                liabilities_total += -balance

        return assets_total - liabilities_total

    def _net_worth_trajectory(
        self,
        book: piecash.Book,
        first_date: date | None,
        accounts: list,
    ) -> list[dict]:
        """Five-point net-worth trajectory: 12mo / 6mo / 3mo / 1mo
        ago and now. Implements GET_BOOK_SUMMARY_SPEC §2.

        A slope number alone (one signal, lossy) hides acceleration
        and recent breaks; a chart is too expensive in tokens. Five
        data points is the spec's sweet spot — costs ~30 tokens,
        lets the LLM see whether recent months broke the trend.

        Anchors before the book's first transaction date are
        dropped (the book didn't exist then; emitting "0" would
        falsely suggest zero net worth a year ago when the user
        simply hadn't started the book yet). Anchors within the
        data range that predate any transaction activity are kept
        — the spec calls a flat trajectory through that span the
        right answer; the LLM draws correct conclusions from it.

        Returns a list of ``{label, net_worth}`` dicts ordered
        oldest-first (matches the natural left-to-right reading of
        the rendered output). Empty list when the book has no
        transactions at all → caller omits the section entirely.
        """
        if first_date is None:
            return []

        from dateutil.relativedelta import relativedelta

        today = date.today()
        # (months_ago, label) — labels right-padded to 8 chars so
        # the rendered values column visually aligns even with the
        # uneven label widths the spec example uses.
        candidates: list[tuple[int, str]] = [
            (12, "12mo ago"),
            (6, " 6mo ago"),
            (3, " 3mo ago"),
            (1, " 1mo ago"),
            (0, "     now"),
        ]
        anchors: list[tuple[date, str]] = []
        for months_ago, label in candidates:
            anchor_date = (
                today if months_ago == 0
                else today - relativedelta(months=months_ago)
            )
            if anchor_date >= first_date:
                anchors.append((anchor_date, label))

        if not anchors:
            return []

        default_currency = self._require_default_currency(book)

        return [
            {
                "label": label,
                "net_worth": self._compute_net_worth_at(
                    book, anchor_date, default_currency, accounts,
                ).quantize(Decimal("1")),
            }
            for anchor_date, label in anchors
        ]

    # Budget overspend warning threshold. Variance over +10% (used%
    # ahead of elapsed%) earns a ⚠ marker — under that, the user
    # is "on pace" or close enough; the threshold gives some
    # breathing room for the lumpy spending patterns most household
    # budgets exhibit.
    _BUDGET_WARN_VARIANCE_PCT = 10

    # Runway warning threshold. < 60 days earns a ⚠ marker — that's
    # roughly two months, the window where a household should be
    # actively concerned about cash position rather than just
    # tracking it.
    _RUNWAY_WARN_DAYS = 60

    # Days in the burn-rate averaging window. 180 days smooths over
    # monthly billing cycles and seasonal variance without diluting
    # recent changes. The spec also calls this out as bounded
    # compute — the iteration is gated to splits within the window.
    _RUNWAY_BURN_DAYS = 180

    # Liquid account types for runway computation. Cash and near-cash
    # only — real estate, vehicles, and other fixed assets aren't
    # runway even if they're wealth.
    #
    # The spec originally proposed including ASSET-typed accounts
    # whose commodity is the book default ("cash-equivalent ASSET")
    # as a heuristic for catching brokerage cash and escrow. In
    # practice GnuCash's ASSET type is structurally for fixed assets
    # — users code real estate, vehicles, and similar wealth as
    # ASSET in default currency, and that heuristic over-counts.
    # The bookkeeper hit this on Alex's book: a USD-default condo
    # ($473K) and vehicle ($28K) added $501K of "liquid" that Alex
    # cannot use to make payroll next week. 768 days of runway
    # ("Alex is fine for two years") vs. 116 days ("Alex has four
    # months to collect receivables or restructure") is a very
    # different conversation.
    #
    # Cleaner rule, observed across actual user books: BANK, CASH,
    # STOCK, MUTUAL. Brokerage positions (STOCK/MUTUAL) ARE liquid
    # — they're sellable in a day at market price. Real fixed
    # assets (ASSET-typed) are not. Users who legitimately have a
    # cash-equivalent ASSET (HSA, prepaid USD) can recategorize it
    # as BANK and it will count; the structural type is honored as
    # the source of truth.
    _RUNWAY_LIQUID_TYPES = frozenset({"BANK", "CASH", "STOCK", "MUTUAL"})

    @staticmethod
    def _is_in_retirement_subtree(account) -> bool:
        """True if any path component of the account's fullname
        contains "retirement" (case-insensitive).

        Heuristic for excluding retirement accounts (IRA, 401k,
        403b, pension) from the runway liquid pool. Users typically
        organize these under a "Retirement" placeholder parent —
        ``Assets:Investments:Retirement:401k`` and similar — which
        gives the runway calculation a structural signal that's
        more reliable than guessing from the account's own name
        ("401k" alone could be ambiguous if the user has a
        retirement-themed expense account, etc.).

        Caveats: a user who names the subtree "Tax-advantaged" or
        "IRA Holdings" without the word "Retirement" gets their
        retirement balance counted as liquid. That's documented in
        the runway docstring; the long-term semantic answer is a
        slot-based ``is_retirement`` flag the user explicitly sets.
        """
        return any(
            "retirement" in part.lower()
            for part in account.fullname.split(":")
        )

    # Stale-price threshold. Prices older than this in days surface
    # in the Warnings section. Matches the cadence at which most
    # users would expect to refresh quotes for active investment
    # holdings; commodities with no price update in over a month
    # are likely producing inaccurate net-worth and runway numbers.
    _STALE_PRICE_DAYS = 30

    def _collect_warnings(
        self,
        book: piecash.Book,
        transactions: list,
        accounts: list,
    ) -> list[str]:
        """Collect warnings for the consolidated Warnings section.
        Implements GET_BOOK_SUMMARY_SPEC §5.

        Returns a list of formatted warning strings ready for
        rendering, ordered by category::

            data integrity → critically low cash → overdue
            invoices/bills → overdue scheduled → stale prices

        Within each category, most-severe / most-overdue first.
        The category ordering puts operational urgency (cash
        flow signals) above data-quality concerns: a near-empty
        bank account or unpaid receivable is the conversation
        Robin needs to have today; stale prices are next-week
        cleanup.

        Coverage:

        - **Integrity** — non-zero balance on any
          ``Imbalance-{ccy}`` / ``Orphan-{ccy}`` account. GnuCash
          auto-creates these when a transaction can't balance or
          when accounts are deleted with their splits orphaned.
          Non-zero balance there is a real structural defect.
        - **Critically low cash** — non-placeholder, non-retirement
          BANK / CASH accounts with positive balance below 1 day of
          daily burn (``_daily_expense_burn``). Catches accounts
          that can't cover tomorrow's expenses on their own; scales
          with the user's actual spending rather than a fixed
          dollar threshold (a $100 floor is "average person" and
          wrong for users on either end of the spectrum). When the
          book has no expense activity (no burn signal), this check
          is skipped — no benchmark to compare against.
        - **Overdue invoices / bills** — posted invoices/bills with
          a non-zero lot balance whose due date is in the past.
          Due date resolution: ``trans-date-due`` slot first, then
          the invoice's ``terms`` reference, then a ``date_posted +
          30 days`` default. When the default fires, the warning
          renders ``N days past 30-day default ... (no term set)``
          to anchor the days count to its assumption rather than
          claiming a contractual due date was missed. Requires
          BusinessMixin to be loaded for the lot-balance helper;
          gracefully skipped otherwise.
        - **Overdue scheduled** — enabled scheduled transactions
          whose next occurrence is in the past. Uses the
          SchedulingMixin's ``_next_occurrence`` helper when
          present; gracefully skipped otherwise.
        - **Stale prices** — non-default commodities in active use
          (referenced by some account or price record) whose latest
          non-``transaction`` price is more than
          ``_STALE_PRICE_DAYS`` days old, or that have no price on
          file. Includes ISO currencies — a stale FX rate cascades
          into wrong receivables totals on multi-currency books.

        Reconciliation-behind warnings are intentionally NOT
        duplicated here — the dedicated Reconciliation section
        already surfaces stale per-account state with detail. The
        spec lists it as a Warnings category, but emitting both
        creates redundant signals; the principle elsewhere in this
        summary (don't repeat information that another section
        already conveys) takes precedence.

        Each per-category collector swallows its own exceptions
        per spec — a failed check in one category never breaks
        the rest of the section.
        """
        today = date.today()
        default_currency = self._require_default_currency(book)

        # ── 1. Data integrity: Imbalance / Orphan accounts ──
        integrity: list[str] = []
        for account in accounts:
            if account.type == "ROOT":
                continue
            name = account.name
            if not (name.startswith("Imbalance-") or name.startswith("Orphan-")):
                continue
            balance = Decimal("0")
            for split in account.splits:
                balance += split.quantity
            if balance != 0:
                integrity.append(
                    f"{name}: {balance} (data integrity issue)"
                )
        # Sort by absolute magnitude descending — biggest defects
        # first within the integrity bucket.
        integrity.sort(
            key=lambda msg: abs(
                Decimal(msg.split(":")[1].split("(")[0].strip())
            ),
            reverse=True,
        )

        # ── 2. Critically low cash ──
        # Per-account threshold: positive balance below 1 day of
        # daily burn (in book default currency). Scales with the
        # user's actual spending rather than an "average person"
        # fixed dollar floor. Skipped when the book has no expense
        # activity (no daily-burn signal to compare against).
        low_cash: list[str] = []
        try:
            daily_burn = self._daily_expense_burn(book, transactions)
            if daily_burn > 0:
                template_guids = self._template_account_guids(book)
                rates = self._rates_as_of(
                    book, today, default_currency,
                )
                low_cash_entries: list[tuple[Decimal, str]] = []
                for account in accounts:
                    if account.type not in ("BANK", "CASH"):
                        continue
                    if account.placeholder:
                        continue
                    if account.guid in template_guids:
                        continue
                    if self._is_in_retirement_subtree(account):
                        continue

                    balance_qty = Decimal("0")
                    for split in account.splits:
                        balance_qty += split.quantity
                    if balance_qty <= 0:
                        # Zero = unused, not low. Negative = overdraft,
                        # captured separately by runway's
                        # negative_liquid path.
                        continue

                    # Convert to default currency for the threshold
                    # comparison. Without a rate, skip rather than
                    # invent a number.
                    if account.commodity == default_currency:
                        balance_default = balance_qty
                    else:
                        rate = rates.get(account.commodity.guid)
                        if rate is None:
                            continue
                        balance_default = balance_qty * rate

                    if balance_default >= daily_burn:
                        continue

                    leaf = account.fullname.split(":")[-1]
                    amount_str = f"{int(balance_default):,}"
                    low_cash_entries.append((
                        balance_default,
                        f"Critically low cash: {leaf} at "
                        f"{default_currency.mnemonic} {amount_str} "
                        f"(under 1 day of burn)",
                    ))
                # Lowest balance first within the bucket — those are
                # the most urgent.
                low_cash_entries.sort(key=lambda e: e[0])
                low_cash = [msg for _, msg in low_cash_entries]
        except Exception:
            pass

        # ── 3. Overdue invoices and bills ──
        # Each posted invoice/bill with non-zero lot balance whose
        # due date is in the past. Due date is read from the
        # ``trans-date-due`` slot on the posting transaction; when
        # absent, falls back to date_posted + 30 days and annotates
        # the warning so the bookkeeper knows the duration is
        # approximated. Requires BusinessMixin's
        # _calculate_lot_balance helper; gracefully skipped when
        # the business module isn't loaded.
        overdue_invoices: list[str] = []
        calc_lot_balance = getattr(self, "_calculate_lot_balance", None)
        if calc_lot_balance is not None:
            try:
                from piecash.business.invoice import Invoice
                from sqlalchemy import text
                find_customer_by_guid = getattr(
                    self, "_find_customer_by_guid", None,
                )
                find_vendor_by_guid = getattr(
                    self, "_find_vendor_by_guid", None,
                )
                overdue_inv_entries: list[tuple[int, str]] = []
                for inv in book.session.query(Invoice).filter(
                    Invoice.date_posted.isnot(None)
                ).all():
                    try:
                        # Three-step due-date resolution lives on
                        # ``BusinessMixin`` (``_resolve_invoice_due_date``)
                        # so the warnings collector and
                        # ``get_outstanding_invoices`` produce identical
                        # math. ``no_terms`` is True when the helper
                        # fell through to the 30-day default; we
                        # annotate the rendered line accordingly.
                        resolve_due = getattr(
                            self, "_resolve_invoice_due_date", None,
                        )
                        if resolve_due is None:
                            continue
                        due_date, no_terms = resolve_due(book, inv)
                        if due_date is None or due_date >= today:
                            continue

                        lot = inv.post_lot
                        if lot is None:
                            continue
                        balance = calc_lot_balance(lot)
                        if balance == 0:
                            continue

                        days_overdue = (today - due_date).days
                        is_bill = (inv.owner_type == 4)
                        doc_type = "bill" if is_bill else "invoice"

                        if is_bill and find_vendor_by_guid is not None:
                            owner = find_vendor_by_guid(
                                book, inv.owner_guid,
                            )
                        elif (
                            not is_bill
                            and find_customer_by_guid is not None
                        ):
                            owner = find_customer_by_guid(
                                book, inv.owner_guid,
                            )
                        else:
                            owner = None
                        owner_name = (
                            owner.name if owner
                            else f"#{inv.id}"
                        )

                        currency = (
                            inv.currency.mnemonic
                            if inv.currency
                            else default_currency.mnemonic
                        )
                        amount_str = f"{int(abs(balance)):,}"
                        # When no term and no explicit due_date were
                        # set, anchor the days count to the assumption
                        # that produced it ("days past 30-day default")
                        # rather than to "overdue" — which reads as
                        # contractual and contradicts "(no term set)".
                        # Same number, honest framing: the bookkeeper
                        # sees the invoice has been unpaid past a
                        # reasonable default AND that no term was
                        # specified, with no implication that a
                        # contractual due date was missed.
                        if no_terms:
                            msg = (
                                f"Past due {doc_type}: {owner_name} "
                                f"{days_overdue} days past 30-day "
                                f"default, {currency} {amount_str} "
                                f"(no term set)"
                            )
                        else:
                            msg = (
                                f"Past due {doc_type}: {owner_name} "
                                f"{days_overdue} days overdue, "
                                f"{currency} {amount_str}"
                            )
                        overdue_inv_entries.append(
                            (days_overdue, msg),
                        )
                    except Exception:
                        continue
                overdue_inv_entries.sort(reverse=True)
                overdue_invoices = [
                    msg for _, msg in overdue_inv_entries
                ]
            except Exception:
                pass

        # ── 4. Stale prices ──
        stale_prices: list[str] = []
        try:
            in_use: set = set()
            for a in accounts:
                if a.type != "ROOT":
                    in_use.add(a.commodity.guid)

            # Single pass over book.prices builds both signals we
            # need: in-use commodities (every priced commodity is
            # in-use even if no account holds it) and the latest
            # market-price date per commodity. Pre-fix the method
            # iterated ``book.prices`` twice — once for ``in_use``,
            # once for ``by_commodity_latest`` — paying the ORM
            # hydration cost twice on a book with hundreds of prices.
            cutoff = today - timedelta(days=self._STALE_PRICE_DAYS)
            by_commodity_latest: dict[str, date] = {}
            for p in book.prices:
                in_use.add(p.commodity.guid)
                if not _is_market_price(p):
                    continue
                p_date = p.date
                if hasattr(p_date, "date") and callable(p_date.date):
                    p_date = p_date.date()
                cguid = p.commodity.guid
                if (
                    cguid not in by_commodity_latest
                    or p_date > by_commodity_latest[cguid]
                ):
                    by_commodity_latest[cguid] = p_date

            # Track (sort_key, message) so we can order most-stale
            # first regardless of whether the commodity has a price
            # at all (None entries sort to the top).
            stale_entries: list[tuple[int, str]] = []
            for commodity in book.commodities:
                if commodity == default_currency:
                    continue
                if commodity.guid not in in_use:
                    continue
                latest = by_commodity_latest.get(commodity.guid)
                if latest is None:
                    stale_entries.append((
                        10**9,  # arbitrary large sort key — top
                        f"Stale price: {commodity.mnemonic} no price on file",
                    ))
                elif latest < cutoff:
                    days_old = (today - latest).days
                    stale_entries.append((
                        days_old,
                        f"Stale price: {commodity.mnemonic} "
                        f"last updated {days_old} days ago",
                    ))
            stale_entries.sort(reverse=True)
            stale_prices = [msg for _, msg in stale_entries]
        except Exception:
            # Per spec: skip failed checks, emit the rest.
            pass

        # ── 5. Overdue scheduled transactions ──
        # Requires SchedulingMixin's helpers (_next_occurrence,
        # RECURRENCE_TO_FREQUENCY). When that module isn't loaded,
        # the attribute lookup degrades gracefully via getattr.
        overdue_scheduled: list[str] = []
        next_occ_fn = getattr(self, "_next_occurrence", None)
        rec_to_freq = getattr(self, "RECURRENCE_TO_FREQUENCY", None)
        if next_occ_fn is not None and rec_to_freq is not None:
            try:
                from piecash.core.transaction import ScheduledTransaction
                overdue_entries: list[tuple[int, str]] = []
                for sx in book.session.query(ScheduledTransaction).all():
                    if not sx.enabled:
                        continue
                    try:
                        rec = sx.recurrence
                        key = (
                            rec.recurrence_period_type,
                            rec.recurrence_mult,
                        )
                        frequency = rec_to_freq.get(key)
                        if not frequency:
                            continue
                        start = sx.start_date
                        if isinstance(start, datetime):
                            start = start.date()
                        end = sx.end_date
                        if isinstance(end, datetime):
                            end = end.date()
                        last = sx.last_occur
                        if isinstance(last, datetime):
                            last = last.date()
                        # Search relative to "yesterday" so today's
                        # occurrence isn't classified as overdue
                        # before the user has a chance to enter it.
                        next_occ = next_occ_fn(
                            start, frequency,
                            after=start - timedelta(days=1),
                            end_date=end, last_occur=last,
                        )
                        if next_occ and next_occ < today:
                            days_overdue = (today - next_occ).days
                            overdue_entries.append((
                                days_overdue,
                                f"Overdue scheduled: {sx.name} "
                                f"due {next_occ.isoformat()}",
                            ))
                    except Exception:
                        continue
                overdue_entries.sort(reverse=True)
                overdue_scheduled = [msg for _, msg in overdue_entries]
            except Exception:
                pass

        # ── 6. Backup health ──
        # Surface auto-backup chain breaks. The single failure mode
        # this server most fears is data loss; an auto-backup that has
        # been silently failing for weeks turns into "you have no
        # recovery option" the day the book corrupts. Pre-fix, the
        # debug log was the only place this surfaced — and the
        # bookkeeper doesn't read debug logs. We render the warning
        # right next to integrity issues because backup health is
        # itself a data-safety concern.
        backup_health: list[str] = []
        get_health = getattr(self, "get_backup_health", None)
        if get_health is not None:
            try:
                health = get_health()
                attempt = health.get("last_attempt")
                if attempt and attempt.get("status") == "failed":
                    age = today - attempt["at"].date()
                    age_str = (
                        f"{age.days} day{'s' if age.days != 1 else ''} ago"
                        if age.days >= 1 else "today"
                    )
                    reason = attempt.get("reason") or "unknown"
                    backup_health.append(
                        f"Auto-backup failing: {reason} "
                        f"(last attempt {age_str})"
                    )
                # No backup file in 30+ days: chain is stale even if
                # the attempt status is fine. Could be that nothing
                # has been due (well-spaced backups + recent prune)
                # or the directory was emptied externally.
                newest_age = health.get("newest_backup_age_days")
                if newest_age is not None and newest_age >= 30:
                    backup_health.append(
                        f"No backup in {newest_age} days (most recent "
                        f"snapshot is older than 1 month)"
                    )
            except Exception:
                pass

        # Integrity tier (imbalance/orphan) leads — actual data
        # corruption that calls every other number into question.
        # The remaining categories follow in operational urgency.
        return (
            integrity
            + backup_health
            + low_cash
            + overdue_invoices
            + overdue_scheduled
            + stale_prices
        )

    def _budget_headline(
        self,
        book: piecash.Book,
        transactions: list,
    ) -> dict | None:
        """One-line headline for the budget covering today, if any.
        Implements GET_BOOK_SUMMARY_SPEC §6.

        Surface logic: pick the budget whose period range includes
        today; if multiple match, prefer the one with the latest
        start date (= most recently effective). Books with no
        budgets — or no budget covering today — get None and the
        caller omits the section.

        piecash's Budget rows don't carry a ``last_modified``
        timestamp the spec's "most recently updated" wording
        suggested; the fallback the spec calls out — "use the
        budget whose period range includes the current date" — is
        what's implemented. This is the right behavior for the
        common case anyway: the user cares about the budget
        currently being lived inside.

        Returns ``{name, used_pct, elapsed_pct, variance_pct}``
        with percentages as Decimals (already quantized to whole
        numbers; caller renders).

        - ``used_pct`` = sum of actuals in budgeted accounts ÷
          sum of budget targets × 100
        - ``elapsed_pct`` = (today − period_start + 1) ÷
          (period_end − period_start + 1) × 100
        - ``variance_pct`` = used_pct − elapsed_pct (positive =
          spending ahead of pace; caller renders + sign and ⚠
          marker at the configured threshold).

        Actuals come straight from EXPENSE / INCOME splits in the
        budgeted accounts themselves (no parent rollup). The full
        budget report — which does roll children up to budgeted
        ancestors — is a separate tool the LLM can call for
        category-level detail. The headline trades that detail for
        a single-line summary the LLM can reference proactively
        ("you're 11% over pace; want me to identify which
        categories are driving it?").
        """
        from piecash.budget import Budget

        budgets = book.session.query(Budget).all()
        if not budgets:
            return None

        from dateutil.relativedelta import relativedelta

        today = date.today()
        candidate = None
        for b in budgets:
            rec = b.recurrence
            period_start = rec.recurrence_period_start
            if isinstance(period_start, datetime):
                period_start = period_start.date()

            period_type = rec.recurrence_period_type
            mult = rec.recurrence_mult
            num_periods = b.num_periods
            if period_type == "month":
                period_end = (
                    period_start
                    + relativedelta(months=mult * num_periods)
                    - timedelta(days=1)
                )
            elif period_type == "week":
                period_end = (
                    period_start
                    + timedelta(weeks=mult * num_periods)
                    - timedelta(days=1)
                )
            else:
                # Unknown recurrence type — skip.
                continue

            if period_start <= today <= period_end:
                if candidate is None or period_start > candidate["start"]:
                    candidate = {
                        "budget": b,
                        "start": period_start,
                        "end": period_end,
                    }

        if candidate is None:
            return None

        budget = candidate["budget"]
        period_start = candidate["start"]
        period_end = candidate["end"]

        # Sum budget targets across all (account, period) pairs.
        # BudgetAmount.amount is a Decimal already.
        total_budgeted = Decimal("0")
        budgeted_account_guids: set[str] = set()
        for ba in budget.amounts:
            total_budgeted += Decimal(str(ba.amount))
            budgeted_account_guids.add(ba.account.guid)

        if total_budgeted <= 0:
            return None

        # Actuals: iterate transactions in the budget's date range
        # once, accumulating EXPENSE positives and INCOME absolute-
        # value flows for splits in budgeted accounts. INCOME is
        # stored negative; flip to a positive contribution to match
        # the spend-vs-target framing.
        actuals = Decimal("0")
        for txn in transactions:
            if txn.post_date < period_start or txn.post_date > period_end:
                continue
            for s in txn.splits:
                if s.account.guid not in budgeted_account_guids:
                    continue
                if s.account.type == "EXPENSE" and s.quantity > 0:
                    actuals += s.quantity
                elif s.account.type == "INCOME" and s.quantity < 0:
                    actuals += -s.quantity

        # Period progression.
        total_days = (period_end - period_start).days + 1
        elapsed_days = (today - period_start).days + 1
        elapsed_days = max(0, min(elapsed_days, total_days))

        elapsed_pct = (
            Decimal(elapsed_days) / Decimal(total_days) * Decimal(100)
        ).quantize(Decimal("1"))
        used_pct = (
            actuals / total_budgeted * Decimal(100)
        ).quantize(Decimal("1"))
        variance_pct = used_pct - elapsed_pct

        return {
            "name": budget.name,
            "used_pct": used_pct,
            "elapsed_pct": elapsed_pct,
            "variance_pct": variance_pct,
        }

    def _daily_expense_burn(
        self,
        book: piecash.Book,
        transactions: list,
        days: int | None = None,
    ) -> Decimal:
        """Average daily EXPENSE outflow over the last ``days`` days.

        Shared between the runway calculation (used to compute days
        of cash on hand) and the critically-low-cash warning (used
        to set a relative threshold "less than 1 day of burn").
        Both want the same number — extracting the helper guarantees
        they agree.

        ``transactions`` is the pre-materialized list ``get_book_summary``
        builds once and threads through every sub-helper that walks
        post-date — same list, no per-helper re-fetch.

        Returns ``Decimal("0")`` when no expense activity in window
        — caller treats that as "no daily-burn signal."
        """
        if days is None:
            days = self._RUNWAY_BURN_DAYS
        today = date.today()
        window_start = today - timedelta(days=days)
        expenses = Decimal("0")
        for txn in transactions:
            if txn.post_date < window_start or txn.post_date > today:
                continue
            for s in txn.splits:
                if s.account.type == "EXPENSE":
                    expenses += Decimal(str(s.value))
        return expenses / Decimal(days)

    def _runway_metrics(
        self,
        book: piecash.Book,
        default_currency: piecash.Commodity,
        transactions: list,
        accounts: list,
    ) -> dict | None:
        """Compute runway: how many days the household could survive
        on current liquid assets at current burn rate if income
        stopped today. Implements GET_BOOK_SUMMARY_SPEC §4.

        Returns ``None`` when there's no expense activity in the
        window (no daily-burn signal → no runway to compute → caller
        omits the section). Otherwise returns a dict the caller
        renders.

        **Liquid assets** = sum of balances in
        ``_RUNWAY_LIQUID_TYPES`` (BANK + CASH + STOCK + MUTUAL),
        with two exclusions layered on top:

        1. ASSET-typed accounts. Structurally for fixed assets
           (real estate, vehicles) in observed user practice,
           even when in default currency.
        2. Any account in a "Retirement" subtree (any ancestor
           with "retirement" in its name, case-insensitive).
           IRA / 401k / 403b balances share BANK / STOCK / MUTUAL
           types with truly liquid accounts but carry
           early-withdrawal penalties — not really runway. See
           ``_is_in_retirement_subtree``.

        STOCK and MUTUAL positions value at
        ``shares × latest_price`` using the same date-aware rate
        helper net worth uses. When no price is on file, fall back
        to cost basis (sum of split.value, in transaction currency)
        — same fallback as net worth. Foreign-currency BANK/CASH
        accounts likewise convert at latest rate, with cost-basis
        fallback for the unpriced case.

        **Daily burn** = sum of expense splits over the last
        ``_RUNWAY_BURN_DAYS`` days, divided by that window size.
        Uses ``split.value`` (transaction currency) — for typical
        books where expense accounts share the book default
        currency, accurate; multi-currency expenses sum raw without
        per-day FX conversion (rare in personal bookkeeping).

        Special cases:
        - ``daily_burn <= 0`` (no expense data): return None →
          omit the section.
        - ``liquid_assets < 0`` (overdrafts exceed positive cash
          positions): return a flag dict; caller renders
          "0 days — liquid position is negative ⚠".
        - Otherwise: return ``{runway_days, liquid, daily_burn}``;
          caller renders the days line with optional ⚠ at <60.
        """
        today = date.today()
        template_guids = self._template_account_guids(book)
        rates = self._rates_as_of(book, today, default_currency)

        # --- Liquid assets pass over book.accounts ---
        liquid = Decimal("0")
        for account in accounts:
            if account.type == "ROOT":
                continue
            if account.guid in template_guids:
                continue
            if account.placeholder:
                continue
            if account.type not in self._RUNWAY_LIQUID_TYPES:
                continue
            if self._is_in_retirement_subtree(account):
                # Retirement accounts (IRA, 401k, 403b, pension, etc.)
                # share BANK / STOCK / MUTUAL types with truly liquid
                # accounts but carry early-withdrawal penalties that
                # disqualify them from "if income stops today" runway.
                # The bookkeeper hit this on Alex's book: a $13,716
                # 401k under Assets:Investments:Retirement was being
                # counted as liquid, inflating runway from ~95 days
                # to 124. Filtering by ancestor-named-Retirement is
                # the structural-intent heuristic — fragile if a user
                # names the subtree "Tax-advantaged" instead, but
                # clean enough for the standard naming convention.
                continue

            balance = Decimal("0")
            for split in account.splits:
                balance += split.quantity
            if balance == 0:
                continue

            if account.commodity == default_currency:
                liquid += balance
            else:
                rate = rates.get(account.commodity.guid)
                if rate is not None:
                    liquid += balance * rate
                else:
                    # Cost-basis fallback: sum split.value
                    # (transaction currency = book default for
                    # typical buys of foreign-commodity holdings).
                    # Same fallback the assets section uses, and
                    # the same one _compute_net_worth_at uses for
                    # consistency. Filter by ``post_date <= today``
                    # so a future-dated entry doesn't inflate
                    # runway's liquid count today (today's API only
                    # asks "as of now"; the filter is defensive in
                    # case a future caller passes an ``as_of``).
                    cost_basis = Decimal("0")
                    for split in account.splits:
                        post_date = split.transaction.post_date
                        if hasattr(post_date, "date") and callable(post_date.date):
                            post_date = post_date.date()
                        if post_date > today:
                            continue
                        cost_basis += Decimal(str(split.value))
                    liquid += cost_basis

        # Daily burn comes from the shared helper so the warnings
        # section's "less than 1 day of burn" threshold and runway's
        # divisor-of-liquid agree by construction.
        daily_burn = self._daily_expense_burn(
            book, transactions, days=self._RUNWAY_BURN_DAYS,
        )

        if daily_burn <= 0:
            return None

        if liquid < 0:
            return {
                "negative_liquid": True,
                "liquid": liquid.quantize(Decimal("1")),
                "daily_burn": daily_burn.quantize(Decimal("1")),
            }

        runway_days = int(liquid / daily_burn)
        return {
            "negative_liquid": False,
            "runway_days": runway_days,
            "liquid": liquid.quantize(Decimal("1")),
            "daily_burn": daily_burn.quantize(Decimal("1")),
        }

    def _monthly_net_income(
        self,
        book: piecash.Book,
        transactions: list,
        months: int = 6,
    ) -> list[dict]:
        """Per-month net income for the last ``months`` calendar
        months. Implements GET_BOOK_SUMMARY_SPEC §3.

        Net = INCOME credits − EXPENSE debits. INCOME splits are
        stored negative (the credit side of the double-entry
        bookkeeping convention) so they're sign-flipped to a positive
        contribution; EXPENSE splits are stored positive and subtract.

        Returns a list of dicts ordered **most recent month first**::

            [
              {"label": "Apr 2026", "net": Decimal("1247"), "is_mtd": True},
              {"label": "Mar 2026", "net": Decimal("890"),  "is_mtd": False},
              ...
            ]

        ``is_mtd`` is True only for the current calendar month
        (which is, by definition, partial). Callers render that as
        a "(MTD)" suffix on the label.

        Returns an empty list when the window contains no income or
        expense activity at all — the caller should omit the section
        entirely rather than emit six "+0" lines that say nothing.

        Multi-currency caveat: ``split.value`` is in transaction
        currency, not account commodity. For typical books where
        income/expense accounts share the book default currency
        (and transactions are recorded in that currency), the sum is
        accurate. Cross-currency income/expense activity sums raw
        without per-month FX conversion — flagged as a refinement
        target in the spec; rare in practice for personal/household
        books.
        """
        today = date.today()

        # Build the calendar-month windows, oldest → newest. Plain
        # arithmetic on (year, month) avoids a dateutil dependency
        # at this layer; the budgets/scheduling mixins already pull
        # in relativedelta for their own needs but core stays light.
        month_starts: list[date] = []
        cursor = date(today.year, today.month, 1)
        for _ in range(months):
            month_starts.append(cursor)
            if cursor.month == 1:
                cursor = date(cursor.year - 1, 12, 1)
            else:
                cursor = date(cursor.year, cursor.month - 1, 1)
        month_starts.reverse()

        month_ends: list[date] = []
        for i, start in enumerate(month_starts):
            if i + 1 < len(month_starts):
                nxt = month_starts[i + 1]
                month_ends.append(
                    date(nxt.year, nxt.month, 1) - timedelta(days=1)
                )
            else:
                if start.month == 12:
                    month_ends.append(date(start.year, 12, 31))
                else:
                    month_ends.append(
                        date(start.year, start.month + 1, 1) - timedelta(days=1)
                    )

        nets = [Decimal("0") for _ in month_starts]
        window_start = month_starts[0]
        window_end = month_ends[-1]
        has_activity = False

        # Single pass over the materialized transactions list. Index
        # math: the bucket for a transaction is
        # (year_delta * 12 + month_delta) from the window start.
        # O(transactions); the date-range gate short-circuits
        # transactions outside the window.
        for txn in transactions:
            d = txn.post_date
            if d < window_start or d > window_end:
                continue
            idx = (
                (d.year - window_start.year) * 12
                + (d.month - window_start.month)
            )
            if idx < 0 or idx >= len(nets):
                continue
            for s in txn.splits:
                atype = s.account.type
                if atype == "INCOME":
                    nets[idx] += -Decimal(str(s.value))
                    has_activity = True
                elif atype == "EXPENSE":
                    nets[idx] -= Decimal(str(s.value))
                    has_activity = True

        if not has_activity:
            return []

        today_month_start = date(today.year, today.month, 1)
        result: list[dict] = []
        for i in range(len(month_starts) - 1, -1, -1):
            start = month_starts[i]
            result.append({
                "label": start.strftime("%b %Y"),
                "net": nets[i].quantize(Decimal("1")),
                "is_mtd": start == today_month_start,
            })
        return result

    @staticmethod
    def _format_monthly_net(net: Decimal) -> str:
        """Render a monthly net value as ``+1,247`` / ``-234`` / ``+0``.

        Always shows an explicit sign; thousands separator. Whole
        dollars (the spec's example output is whole-number; cents
        would noise up the summary view without adding signal).
        """
        return f"{int(net):+,}"


    @staticmethod
    def _format_reconciliation_lag(days_behind: int) -> str:
        """Render a parenthesized "(N months behind)" / "(N days
        behind)" suffix for a reconciliation status warning.

        Months scale once we're past 60 days because that's how users
        think about reconciliation lag — "two months behind" reads
        more naturally than "67 days behind." Below 60 days we stay
        in days for precision; the warning threshold itself is 45
        days, so the days-form covers the 45–59 window.

        The month-count uses 30.44 days as the average month length
        (365.25 / 12, accounting for leap years) so 91 days reads
        as "3 months" and 60 days reads as "2 months" without the
        off-by-one nudge that ``// 30`` produced (90 → 3 vs 91 → 3,
        but 30 → 1 vs 60 → 2 was sharp; reasonable enough most of
        the time but humans round to nearest unit, not floor).
        """
        if days_behind >= 60:
            months = round(days_behind / 30.44)
            return f"({months} months behind)"
        return f"({days_behind} days behind)"

    # ── Section renderers ─────────────────────────────────────────────
    #
    # Each ``_render_*`` helper consumes the data produced by its
    # paired ``_collect_*`` / ``_*_metrics`` / ``_*_headline`` /
    # ``_*_trajectory`` method and returns a ``list[str]`` of lines
    # to append to the summary (or ``[]`` to omit the section
    # entirely — absence-as-signal, per the spec).
    #
    # Pre-extraction the render block was one long sequence inside
    # ``get_book_summary``. Pulling each section into its own helper
    # mirrors the data layer's decomposition and makes adding or
    # modifying a section a one-method change instead of surgery
    # through the render block. Each helper is self-contained — no
    # cross-section state — so the rendering order in
    # ``get_book_summary`` becomes a one-glance read.

    @staticmethod
    def _render_warnings(warnings: list[str]) -> list[str]:
        """Render the Warnings section.

        Section is omitted when there are no warnings — the spec
        explicitly calls out not printing "Warnings: none." Empty
        list signals "section absent" to the caller.
        """
        if not warnings:
            return []
        out = ["Warnings:"]
        for msg in warnings:
            out.append(f"  ⚠ {msg}")
        return out

    def _render_reconciliation(
        self, reconciliation: list[dict],
    ) -> list[str]:
        """Render the Reconciliation section.

        Three buckets per the data spec:

        1. **STALE** (reconciled but > ``_RECONCILE_WARN_DAYS``
           behind) — render individually with the through-date and
           a "(N days/months behind) ⚠" lag suffix. Per-account
           payload (how stale, scope of work) can't be aggregated.
        2. **CURRENT** (reconciled within window) — collapse into
           "<N> accounts current". Identical-across-accounts
           payload compresses to a count.
        3. **NEVER RECONCILED** (activity but no 'y' splits) —
           collapse into "<N> account(s) never reconciled ⚠".

        Each collapse line omitted when its count is zero
        (absence-as-signal). Section omitted entirely when
        reconciliation is empty.
        """
        if not reconciliation:
            return []
        stale: list[dict] = []
        current_count = 0
        never_count = 0
        for entry in reconciliation:
            if entry["status"] == "never reconciled":
                never_count += 1
            elif entry["days_behind"] > self._RECONCILE_WARN_DAYS:
                stale.append(entry)
            else:
                current_count += 1

        out = ["Reconciliation:"]
        for entry in stale:
            leaf = entry["account"].split(":")[-1]
            lag = self._format_reconciliation_lag(entry["days_behind"])
            # Sub-line shape: "47 splits unreconciled since
            # 2025-12-30 (4 months behind) ⚠". The split count
            # tells the LLM the *scope* of the reconciliation
            # work — 12 splits is one sitting; 400 needs a
            # month-by-month strategy.
            n = entry["unreconciled_count"]
            if n > 0 and "latest_y_date" in entry:
                plural = "s" if n != 1 else ""
                since = entry["latest_y_date"]
                out.append(
                    f"  {leaf}: {n} split{plural} "
                    f"unreconciled since {since} {lag} ⚠"
                )
            else:
                out.append(
                    f"  {leaf}: {entry['status']} {lag} ⚠"
                )
        if current_count:
            plural = "s" if current_count != 1 else ""
            out.append(f"  {current_count} account{plural} current")
        if never_count:
            plural = "s" if never_count != 1 else ""
            out.append(
                f"  {never_count} account{plural} never reconciled ⚠"
            )
        return out

    @staticmethod
    def _render_net_worth_trajectory(
        trajectory: list[dict], currency: str,
    ) -> list[str]:
        """Render the Net worth trajectory section.

        Surfaces acceleration and trend breaks that a single
        net-worth number can't. Empty trajectory = book has no
        transactions or every anchor predates the data range →
        omit the section.
        """
        if not trajectory:
            return []
        out = ["Net worth trajectory:"]
        for entry in trajectory:
            out.append(
                f"  {entry['label']}: {currency} "
                f"{int(entry['net_worth']):,}"
            )
        return out

    def _render_monthly_net(self, monthly: list[dict]) -> list[str]:
        """Render the Monthly net (last 6 months) section.

        Surfaces seasonality and recent anomalies. Empty list = no
        income/expense activity in the window → omit the section.
        MTD entries get a "(MTD)" suffix on the label.
        """
        if not monthly:
            return []
        out = ["Monthly net (last 6 months):"]
        for entry in monthly:
            label = entry["label"]
            if entry["is_mtd"]:
                label += " (MTD)"
            out.append(
                f"  {label}: {self._format_monthly_net(entry['net'])}"
            )
        return out

    def _render_runway(
        self, runway: dict | None, currency: str,
    ) -> list[str]:
        """Render the Runway line.

        Liquid assets / daily burn → days. The single most
        actionable personal-finance number that doesn't appear on
        standard financial statements. ``None`` = no expense data in
        the burn window → omit section. ``negative_liquid`` flag
        renders the special 0-days-with-warning line.
        """
        if runway is None:
            return []
        if runway.get("negative_liquid"):
            return ["Runway: 0 days — liquid position is negative ⚠"]
        days = runway["runway_days"]
        liquid = int(runway["liquid"])
        burn = int(runway["daily_burn"])
        warn = " ⚠" if days < self._RUNWAY_WARN_DAYS else ""
        return [
            f"Runway: {days} days{warn} "
            f"({currency} {liquid:,} liquid / "
            f"{currency} {burn:,}/day burn)"
        ]

    def _render_budget(self, budget: dict | None) -> list[str]:
        """Render the Budget headline line.

        One line for the budget covering today. ``None`` = no
        budget exists or none covers today → omit. Variance
        over ``_BUDGET_WARN_VARIANCE_PCT`` earns ⚠ (spending
        ahead of pace).
        """
        if budget is None:
            return []
        used = int(budget["used_pct"])
        elapsed = int(budget["elapsed_pct"])
        variance = int(budget["variance_pct"])
        if variance > 0:
            variance_str = f"(+{variance}% over pace)"
        elif variance < 0:
            variance_str = f"({-variance}% under pace)"
        else:
            variance_str = "(on pace)"
        warn = (
            " ⚠" if variance > self._BUDGET_WARN_VARIANCE_PCT else ""
        )
        return [
            f"Budget ({budget['name']}): "
            f"{used}% used / {elapsed}% elapsed "
            f"{variance_str}{warn}"
        ]

    def get_book_summary(self) -> str:
        """Return a compact text summary of the entire book.

        Provides instant orientation: account structure, transaction volume,
        key balances, commodities, and scheduled transactions — all in one call.

        Investment accounts (STOCK, MUTUAL, and any other account whose
        commodity differs from the book's default currency) are valued at
        ``shares × latest_price`` from book.prices. When no price is on
        file, cost basis (sum of split values in the transaction currency)
        is used as a fallback and the line is tagged accordingly.

        Returns:
            Pre-formatted text summary string.
        """
        from piecash.budget import Budget
        from piecash.core.transaction import ScheduledTransaction

        with self.open(readonly=True) as book:
            default_currency = self._require_default_currency(book)
            currency = default_currency.mnemonic

            # Balances are computed as-of-today: future-dated
            # transactions are excluded so the displayed Assets /
            # Liabilities totals agree with trajectory's "now" by
            # construction. Without this filter, future-dated
            # transactions in the book would skew the current
            # snapshot — bookkeeper hit this on Alex's book where
            # 34 days of data past today produced a $2,906 gap
            # between Assets-Liabilities and trajectory.
            #
            # Prices are NOT today-filtered. The bookkeeper writes
            # future-dated yfinance close prices intentionally as
            # forecasts the displays should track; balance_sheet
            # uses the absolute latest, and this summary now
            # matches by construction. ``_compute_net_worth_at``
            # (above) special-cases ``as_of >= today`` to use the
            # same all-prices lookup so the trajectory "now"
            # anchor agrees here too.
            today = date.today()

            # Identify template accounts (scheduled-transaction scaffolding).
            # Shared helper on BaseGnuCashBook walks the whole subtree; the
            # old inline version only captured root_template + direct
            # children, which worked because create_scheduled_transaction
            # creates flat templates — but tolerates deeper nesting now.
            template_guids = self._template_account_guids(book)

            # Materialize the account list once. CODE_REVIEW noted 7-10
            # passes over ``book.accounts`` between this method and the
            # sub-helpers it calls; threading the in-memory list collapses
            # each pass from "hydrate the ORM collection then iterate" to
            # "iterate the already-hydrated Python list."
            accounts = list(book.accounts)

            # --- Collect parent GUIDs (placeholder containers) ---
            parent_guids = set()
            for account in accounts:
                if account.parent and account.parent.type != "ROOT":
                    parent_guids.add(account.parent.guid)

            # --- Latest-price lookup for non-default-currency commodities ---
            # Use the same shared helper balance_sheet uses, so the
            # two surfaces agree on which price is "current" for
            # every commodity — including the bookkeeper's
            # intentional future-dated yfinance forecast entries.
            # ``_rates_as_of(book)`` (no upper bound) already excludes
            # piecash auto-created ``type='transaction'`` prices
            # (cross-currency placeholders, not market quotes).
            latest_prices: dict[str, Decimal] = self._rates_as_of(book)

            # --- Account stats ---
            asset_types = {"ASSET", "BANK", "CASH", "STOCK", "MUTUAL"}

            # Assets: (leaf_name, usd_value, note) for non-placeholder leaf accounts
            asset_leaves: list[tuple[str, Decimal, str | None]] = []
            # Liabilities: (leaf_name, positive_balance) grouped by category
            credit_cards: list[tuple[str, Decimal]] = []
            loan_accts: list[tuple[str, Decimal]] = []
            other_liab_accts: list[tuple[str, Decimal]] = []
            # Receivables / Payables (separate sections, per Abe's spec)
            receivable_accts: list[tuple[str, Decimal]] = []
            payable_accts: list[tuple[str, Decimal]] = []

            income_active = 0
            income_total = 0
            expense_active = 0
            expense_total = 0
            total_accounts = 0

            for account in accounts:
                if account.type == "ROOT":
                    continue
                if account.guid in template_guids:
                    continue
                total_accounts += 1

                has_activity = len(account.splits) > 0
                is_leaf = account.guid not in parent_guids

                # Calculate balance in the account's own commodity.
                # Date filter excludes future-dated transactions so
                # trajectory's "now" anchor agrees with the
                # displayed Assets / Liabilities totals.
                balance = Decimal("0")
                for split in account.splits:
                    if split.transaction.post_date <= today:
                        balance += split.quantity

                leaf = account.fullname.split(":")[-1]

                if account.type in asset_types:
                    if is_leaf and balance != 0:
                        usd_value, note = self._market_value(
                            account, balance,
                            rates=latest_prices,
                            default_currency=default_currency,
                            today=today,
                        )
                        asset_leaves.append((leaf, usd_value, note))
                elif account.type == "CREDIT":
                    if is_leaf:
                        credit_cards.append((leaf, -balance))
                elif account.type == "LIABILITY":
                    if is_leaf:
                        neg_balance = -balance
                        if "loan" in account.fullname.lower():
                            loan_accts.append((leaf, neg_balance))
                        else:
                            other_liab_accts.append((leaf, neg_balance))
                elif account.type == "RECEIVABLE":
                    if is_leaf and balance != 0:
                        # A/R is debit-natural: positive balance = owed to us.
                        usd_value, _ = self._market_value(
                            account, balance,
                            rates=latest_prices,
                            default_currency=default_currency,
                            today=today,
                        )
                        receivable_accts.append((leaf, usd_value))
                elif account.type == "PAYABLE":
                    if is_leaf and balance != 0:
                        # A/P is credit-natural: negate for "what we owe".
                        usd_value, _ = self._market_value(
                            account, -balance,
                            rates=latest_prices,
                            default_currency=default_currency,
                            today=today,
                        )
                        payable_accts.append((leaf, usd_value))
                elif account.type == "INCOME":
                    income_total += 1
                    if has_activity:
                        income_active += 1
                elif account.type == "EXPENSE":
                    expense_total += 1
                    if has_activity:
                        expense_active += 1

            # Compute totals from leaf accounts
            def _r2(v: Decimal) -> Decimal:
                return v.quantize(Decimal("0.01"))

            assets_total = _r2(
                sum((v for _, v, _ in asset_leaves), Decimal("0"))
            )
            credit_total = _r2(sum(b for _, b in credit_cards) if credit_cards else Decimal(0))
            loan_total = _r2(sum(b for _, b in loan_accts) if loan_accts else Decimal(0))
            other_liab_total = _r2(sum(b for _, b in other_liab_accts) if other_liab_accts else Decimal(0))
            liabilities_total = _r2(credit_total + loan_total + other_liab_total)
            receivables_total = _r2(
                sum((b for _, b in receivable_accts), Decimal("0"))
            )
            payables_total = _r2(
                sum((b for _, b in payable_accts), Decimal("0"))
            )
            net_worth = _r2(assets_total - liabilities_total)

            # All liability leaves sorted by balance descending for top-N
            all_liab_leaves = credit_cards + loan_accts + other_liab_accts
            all_liab_leaves.sort(key=lambda x: x[1], reverse=True)

            # --- Transaction stats ---
            # Per-split unreconciled counting was dropped — the old
            # "Transactions: N (M unreconciled)" suffix included
            # income/expense/equity splits that can't be reconciled,
            # so the count was operationally useless. The new
            # Reconciliation section below (per-account, per-status)
            # is the actionable replacement.
            transactions = list(book.transactions)
            total_txns = len(transactions)
            first_date = None
            last_date = None

            for txn in transactions:
                d = txn.post_date
                if first_date is None or d < first_date:
                    first_date = d
                if last_date is None or d > last_date:
                    last_date = d

            # --- Scheduled transactions ---
            all_sx = book.session.query(ScheduledTransaction).all()
            enabled_sx = sum(1 for sx in all_sx if sx.enabled)

            # --- Business entities ---
            n_customers = len(list(book.customers))
            n_vendors = len(list(book.vendors))
            n_employees = len(list(book.employees))

            # --- Budgets ---
            n_budgets = book.session.query(Budget).count()

            # --- Commodities ---
            commodity_mnemonics = sorted(set(
                c.mnemonic for c in book.commodities
            ))

            # --- Build output ---
            lines = []
            lines.append(f"Book: {self.book_path}")
            lines.append(f"Currency: {currency}")

            if first_date and last_date:
                lines.append(f"Data range: {first_date.isoformat()} to {last_date.isoformat()}")

            # Last entry: how stale are the books? The data range
            # tells the LLM what's covered; this line tells it
            # whether the books are caught up (entered through
            # yesterday) or whether there's a backlog of
            # transactions to enter before reconciliation makes
            # sense. The bookkeeper's framing: "let's reconcile"
            # vs. "let's enter 200 transactions first" — the
            # answer pivots on this number.
            #
            # Four cases keyed on (today − last_date).days:
            #   < 0  — future-dated. Normal for scheduled-txn
            #          instantiation that posts ahead of time.
            #          NOT a "behind" signal; render as
            #          "(future-dated, N days ahead)".
            #   = 0  — today.
            #   = 1  — yesterday.
            #   > 1  — N days behind. ⚠ past
            #          _LAST_ENTRY_WARN_DAYS (14) — catch-up is
            #          usually pending before reconciliation.
            if last_date is not None:
                today = date.today()
                days_behind = (today - last_date).days
                if days_behind < 0:
                    days_ahead = -days_behind
                    lines.append(
                        f"Last entry: {last_date.isoformat()} "
                        f"(future-dated, {days_ahead} days ahead)"
                    )
                elif days_behind == 0:
                    lines.append(
                        f"Last entry: {last_date.isoformat()} (today)"
                    )
                elif days_behind == 1:
                    lines.append(
                        f"Last entry: {last_date.isoformat()} (yesterday)"
                    )
                else:
                    warn = (
                        " ⚠"
                        if days_behind > self._LAST_ENTRY_WARN_DAYS
                        else ""
                    )
                    lines.append(
                        f"Last entry: {last_date.isoformat()} "
                        f"({days_behind} days behind){warn}"
                    )

            # Warnings: scan-first section. Lives near the top of
            # the output (right after book metadata) because if
            # there's data integrity trouble or stale prices
            # informing the rest of the summary, the LLM should see
            # that BEFORE reading numbers that depend on them.
            warnings = self._collect_warnings(book, transactions, accounts)
            lines.extend(self._render_warnings(warnings))

            lines.append(f"Accounts: {total_accounts} total")

            # Assets section — leaf accounts with USD-valued balances
            lines.append(f"Assets: {len(asset_leaves)} accounts, {currency} {assets_total}")
            for name, usd_value, note in sorted(
                asset_leaves, key=lambda x: x[1], reverse=True
            ):
                if note is None:
                    lines.append(f"  {name}: {currency} {_r2(usd_value)}")
                else:
                    lines.append(
                        f"  {name}: {note} ({currency} {_r2(usd_value)})"
                    )

            # Liabilities section — grouped subtotals + top 3
            liab_count = len(credit_cards) + len(loan_accts) + len(other_liab_accts)
            lines.append(f"Liabilities: {liab_count} accounts, {currency} {liabilities_total}")
            if credit_cards:
                lines.append(f"  Credit cards ({len(credit_cards)}): {currency} {credit_total}")
            if loan_accts:
                lines.append(f"  Loans ({len(loan_accts)}): {currency} {loan_total}")
            if other_liab_accts:
                lines.append(f"  Other ({len(other_liab_accts)}): {currency} {other_liab_total}")
            if len(all_liab_leaves) > 1:
                top_n = all_liab_leaves[:3]
                top_parts = [f"{n} {currency} {_r2(b)}" for n, b in top_n]
                lines.append(f"  Top {len(top_n)}: {', '.join(top_parts)}")

            # Receivables / Payables — only if non-zero
            if receivable_accts:
                lines.append(
                    f"Receivables: {len(receivable_accts)} account"
                    f"{'s' if len(receivable_accts) != 1 else ''}, "
                    f"{currency} {receivables_total}"
                )
                for name, bal in sorted(receivable_accts, key=lambda x: x[1], reverse=True):
                    lines.append(f"  {name}: {currency} {_r2(bal)}")
            if payable_accts:
                lines.append(
                    f"Payables: {len(payable_accts)} account"
                    f"{'s' if len(payable_accts) != 1 else ''}, "
                    f"{currency} {payables_total}"
                )
                for name, bal in sorted(payable_accts, key=lambda x: x[1], reverse=True):
                    lines.append(f"  {name}: {currency} {_r2(bal)}")

            lines.append(f"Income: {income_active} active ({income_total} total)")
            lines.append(f"Expenses: {expense_active} active ({expense_total} total)")

            # Reconciliation, trajectory, monthly net, runway, budget:
            # each section's data collector lives in its own method;
            # the matching ``_render_*`` helper renders the section
            # (or returns ``[]`` to omit it — absence-as-signal). See
            # the helpers for the per-section spec and warning
            # thresholds.
            reconciliation = self._account_reconciliation_status(
                book, accounts,
            )
            lines.extend(self._render_reconciliation(reconciliation))

            trajectory = self._net_worth_trajectory(
                book, first_date, accounts,
            )
            lines.extend(
                self._render_net_worth_trajectory(trajectory, currency)
            )

            monthly = self._monthly_net_income(book, transactions, months=6)
            lines.extend(self._render_monthly_net(monthly))

            runway = self._runway_metrics(
                book, default_currency, transactions, accounts,
            )
            lines.extend(self._render_runway(runway, currency))

            budget = self._budget_headline(book, transactions)
            lines.extend(self._render_budget(budget))

            lines.append(f"Transactions: {total_txns}")

            if enabled_sx > 0:
                # Roll the "due in next 7 days" stat into the
                # Scheduled line so the LLM sees the immediate
                # to-do list at orientation time, no second tool
                # call needed. The bookkeeper's framing: the
                # dashboard answers "what is the state"; this
                # turns it into "what do I need to do next."
                #
                # ``_upcoming_within_days`` lives on
                # SchedulingMixin; ``hasattr`` lets a book class
                # built without scheduling skip the upcoming-line
                # render cleanly. Cross-mixin call avoided in
                # favor of opportunistic inclusion.
                line = f"Scheduled: {enabled_sx} recurring"
                if hasattr(self, "_upcoming_within_days"):
                    upcoming = self._upcoming_within_days(
                        book, days=7,
                    )
                    if upcoming["count"] > 0:
                        plural = (
                            "s" if upcoming["count"] != 1 else ""
                        )
                        # Whole-currency-unit total — this is a
                        # planning number, not an accounting line.
                        total_int = int(upcoming["total"])
                        line += (
                            f", {upcoming['count']} due in next "
                            f"7 days ({currency} {total_int:,})"
                        )
                    else:
                        line += ", none due in next 7 days"
                lines.append(line)

            # Business + budgets — one line each, only if present
            if n_customers or n_vendors or n_employees:
                parts = []
                if n_customers:
                    parts.append(f"{n_customers} customer"
                                 f"{'s' if n_customers != 1 else ''}")
                if n_vendors:
                    parts.append(f"{n_vendors} vendor"
                                 f"{'s' if n_vendors != 1 else ''}")
                if n_employees:
                    parts.append(f"{n_employees} employee"
                                 f"{'s' if n_employees != 1 else ''}")
                lines.append(f"Business: {', '.join(parts)}")
            if n_budgets:
                lines.append(f"Budgets: {n_budgets}")

            lines.append(f"Commodities: {', '.join(commodity_mnemonics)}")

            # Bottom-line "Net worth: USD X" line removed. The
            # trajectory section's "now" anchor is now the
            # authoritative net-worth number, computed via
            # _compute_net_worth_at — the user sees one number,
            # by construction matching the per-leaf
            # assets_total − liabilities_total semantics that
            # used to render here.

            return "\n".join(lines)

    def list_accounts(
        self,
        root: str | None = None,
        compact: bool = True,
    ) -> list[dict] | str:
        """List all accounts in the chart of accounts.

        Compact output emits one ``%shortguid<TAB>fullname [ANNOTATION]``
        line per account. The short GUID is the LLM's compact handle
        for re-referencing the account in subsequent tool calls — much
        cheaper than re-quoting a long path like
        ``"Assets:Current Assets:Savings Account"`` every time. Tools
        that accept an account reference resolve ``%xxxxxxx``, full
        GUIDs, and paths interchangeably via
        :meth:`BaseGnuCashBook._resolve_account`.

        Args:
            root: Optional root account path to filter to a subtree.
                  E.g., "Expenses" returns only Expenses and descendants.
            compact: If True (default), return a compact newline-separated
                     string with one line per account. If False, return
                     the full list of account dicts.

        Returns:
            If compact: newline-separated string. Each line is
                ``"%shortguid<TAB>fullname [ANNOTATION]"``.
            If not compact: flat list of account dicts with full paths
                and full GUIDs.
        """
        with self.open(readonly=True) as book:
            # Hide scheduled-transaction template accounts — they live
            # under book.root_template as real Account rows (piecash
            # surfaces them in book.accounts), but they're GnuCash
            # internals, not part of the user's chart of accounts.
            template_guids = self._template_account_guids(book)

            # ``root`` accepts a path, ``%short`` GUID, or full GUID.
            # Normalize to a fullname for the prefix comparisons below.
            if root is not None and (
                root.startswith(self._SHORT_ACCOUNT_GUID_PREFIX) or len(root) == 32
            ):
                resolved_root = self._resolve_account(book, root)
                root = resolved_root.fullname if resolved_root else root

            filtered = []
            for account in book.accounts:
                if account.type == "ROOT":
                    continue
                if account.guid in template_guids:
                    continue
                if root is not None:
                    fn = account.fullname
                    if fn != root and not fn.startswith(root + ":"):
                        continue
                filtered.append(account)

            filtered.sort(key=lambda a: a.fullname)

            if compact:
                # Build the short-guid map across the *whole* book so
                # prefixes are unambiguous against every resolvable
                # account, not just the (possibly filtered) subset.
                short_map = self._account_short_guid_map(book)
                lines = [
                    f"{short_map[a.guid]}\t{_account_to_compact_line(a)}"
                    for a in filtered
                ]
                return "\n".join(lines)
            else:
                return [_account_to_dict(a) for a in filtered]

    def get_account(self, name: str) -> dict | None:
        """Get details for a specific account by full name.

        Args:
            name: Full account path (e.g., 'Assets:Bank:Checking').

        Returns:
            Account dict if found, None otherwise.
        """
        with self.open(readonly=True) as book:
            account = self._resolve_account(book, name)
            if account:
                return _account_to_dict(account)
            return None

    def get_balance(self, account_name: str, as_of_date: date | None = None) -> Decimal:
        """Get balance for an account as of a specific date.

        Returns raw GnuCash balance (accounting sign convention).

        Defaults to today, so future-dated transactions (scheduled
        payments, accrued interest, mid-month bills already entered)
        are excluded. To project a balance forward — including future
        entries — pass an explicit ``as_of_date`` past today.

        Args:
            account_name: Full account path.
            as_of_date: Date to calculate balance as of. Defaults to
                today's date.

        Returns:
            Account balance as Decimal.

        Raises:
            ValueError: If account not found.
        """
        if as_of_date is None:
            as_of_date = date.today()
        with self.open(readonly=True) as book:
            account = self._resolve_account(book, account_name)
            if not account:
                raise ValueError(f"Account not found: {account_name}")

            balance = Decimal("0")
            for split in account.splits:
                if split.transaction.post_date <= as_of_date:
                    balance += split.quantity

            return balance

    # Server-side ceiling for list_transactions / search_transactions
    # limits. Caller-supplied limits above this are clamped with a note.
    MAX_LIST_LIMIT = 250

    def list_transactions(
        self,
        account: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 50,
        compact: bool = True,
    ) -> list[dict] | str:
        """List transactions with optional filters.

        When the unfiltered result set exceeds ``limit``, compact output
        appends a truncation notice so callers can tell their data is
        incomplete. Limits above ``MAX_LIST_LIMIT`` (250) are clamped
        server-side and flagged in the notice.

        Args:
            account: Filter by account full name.
            start_date: Filter transactions on or after this date.
            end_date: Filter transactions on or before this date.
            limit: Maximum number of transactions to return. Capped at 250.
            compact: If True (default), return a compact newline-separated
                     string with one line per transaction. If False, return
                     the full list of transaction dicts.

        Returns:
            If compact: newline-separated string of transaction lines, with
                a ``[Showing N of M ...]`` notice appended when truncated.
            If not compact: list of transaction dicts, most recent first
                (truncated silently — callers have the list length).

        Raises:
            ValueError: If specified account not found.
        """
        capped = limit > self.MAX_LIST_LIMIT
        effective_limit = min(limit, self.MAX_LIST_LIMIT)

        with self.open(readonly=True) as book:
            # If filtering by account, get transactions through that account's splits
            focus_fullname: str | None = None
            if account:
                acct = self._resolve_account(book, account)
                if not acct:
                    raise ValueError(f"Account not found: {account}")
                # Capture the canonical fullname for register-form
                # rendering. _transaction_to_compact_line compares the
                # focus to ``split.account.fullname``, so passing the
                # raw input (which may be a ``%short`` GUID) would
                # silently fall through to the multi-split form.
                focus_fullname = acct.fullname
                transactions = {split.transaction for split in acct.splits}
            else:
                transactions = set(book.transactions)

            # Apply date filters
            filtered = []
            for trans in transactions:
                if start_date and trans.post_date < start_date:
                    continue
                if end_date and trans.post_date > end_date:
                    continue
                filtered.append(trans)

            # Sort by date descending
            filtered.sort(key=lambda t: t.post_date, reverse=True)

            total_matched = len(filtered)
            # Apply limit
            filtered = filtered[:effective_limit]

            if compact:
                # Build collision-safe prefix map across ALL transactions in
                # the book (not just the filtered batch) so emitted prefixes
                # remain valid _resolve_guid lookup keys against the full table.
                # Cached on BaseGnuCashBook by book mtime — repeated calls
                # against an unchanged book skip the iterate-sort-build pass.
                prefixes = self._transaction_prefix_map(book)
                lines = [
                    _transaction_to_compact_line(
                        t, focus_account=focus_fullname, prefixes=prefixes
                    )
                    for t in filtered
                ]
                notice = self._truncation_notice(
                    total=total_matched,
                    shown=len(filtered),
                    effective_limit=effective_limit,
                    capped=capped,
                    suggest_narrow=True,
                )
                if notice:
                    lines.append(notice)
                return "\n".join(lines)
            else:
                return [_transaction_to_dict(t) for t in filtered]

    @staticmethod
    def _truncation_notice(
        total: int,
        shown: int,
        effective_limit: int,
        capped: bool,
        suggest_narrow: bool = True,
    ) -> str | None:
        """Build a truncation-notice line, or return None if no notice needed.

        Emits one of:
          - "[Limit capped at 250 — narrow your criteria for larger datasets]"
            when the caller's limit exceeded MAX_LIST_LIMIT AND results fit.
          - "[Showing N of M transactions — use start_date/end_date to narrow,
             or set limit= higher]" when results were truncated (capped or not).
          - None when total <= shown (everything fit).
        """
        if total <= shown:
            if capped:
                return (
                    f"[Limit capped at {effective_limit} — results fit under "
                    f"the cap]"
                )
            return None
        if capped:
            return (
                f"[Showing {shown} of {total} transactions — limit was capped "
                f"at {effective_limit}; narrow your criteria for complete "
                f"results]"
            )
        hint = (
            "use start_date/end_date to narrow, or set limit= higher"
            if suggest_narrow else "set limit= higher"
        )
        return f"[Showing {shown} of {total} transactions — {hint}]"

    def get_transaction(self, guid: str) -> dict | None:
        """Get details for a specific transaction by GUID.

        Args:
            guid: Transaction GUID (32-character hex string).

        Returns:
            Transaction dict if found, None otherwise.
        """
        with self.open(readonly=True) as book:
            transaction = self._find_transaction(book, guid)
            if transaction:
                return _transaction_to_dict(transaction)
            return None

    _FUNDING_ACCOUNT_TYPES = {
        "BANK", "CASH", "ASSET", "CREDIT", "LIABILITY", "EQUITY",
    }

    @staticmethod
    def _extract_account_pattern(accounts) -> frozenset[str]:
        """Extract categorization (non-funding) account names.

        Filters out funding account types (BANK, CASH, ASSET, CREDIT,
        LIABILITY, EQUITY) to isolate expense/income categorization.
        Falls back to all accounts if filtering leaves nothing
        (e.g., bank-to-bank transfers).

        Args:
            accounts: Iterable of piecash Account objects.

        Returns:
            frozenset of account fullnames representing the pattern.
        """
        all_names = frozenset(a.fullname for a in accounts)
        categorization = frozenset(
            a.fullname for a in accounts
            if a.type not in CoreMixin._FUNDING_ACCOUNT_TYPES
        )
        return categorization if categorization else all_names

    def _find_recent_description_matches(
        self,
        book,
        description: str,
        limit: int = 5,
        days: int = 90,
    ) -> list:
        """Find recent transactions with matching descriptions.

        Uses bidirectional case-insensitive substring matching
        (same logic as _auto_fill_splits and _find_duplicates).

        Args:
            book: Open piecash book (readonly).
            description: Description to match.
            limit: Maximum matches to return.
            days: How far back to search.

        Returns:
            List of piecash Transaction objects, most recent first.
        """
        desc_lower = description.lower()
        cutoff = date.today() - timedelta(days=days)
        matches = []

        sorted_txns = sorted(
            book.transactions, key=lambda t: t.post_date, reverse=True
        )
        for txn in sorted_txns:
            if txn.post_date < cutoff:
                break
            txn_desc_lower = txn.description.lower()
            if desc_lower in txn_desc_lower or txn_desc_lower in desc_lower:
                matches.append(txn)
                if len(matches) >= limit:
                    break

        return matches

    def _collect_create_signals(
        self,
        book: piecash.Book,
        description: str,
        trans_date: date,
        proposed_amounts: list[Decimal],
        *,
        want_auto_fill: bool,
        want_stability: bool,
        want_duplicates: bool,
        want_recent: bool,
        duplicate_window_days: int = 30,
        stability_days: int = 90,
        stability_limit: int = 5,
        recent_days: int = 30,
        recent_limit: int = 5,
    ) -> "_CreateSignals":
        """Gather every signal ``create_transaction`` might need in a
        single pass over ``book.transactions``.

        The four original helpers (``_auto_fill_splits``,
        ``_check_auto_fill_stability``, ``_find_duplicates``, and the
        post-write ``_find_recent_description_matches``) each opened the
        book and did its own full-table scan. This collector folds all
        of them into one sort + one traversal, classifying each
        transaction into whichever signal bucket(s) it matches.

        Callers supply ``want_*`` flags so the collector only does work
        the caller will actually consume — e.g., auto-fill has no
        meaning when the caller already provided splits, and duplicate
        detection is skipped when ``check_duplicates`` is False.

        Must be called before the new transaction is committed to the
        book; otherwise the just-created transaction shows up as a
        false-positive recent-match / duplicate of itself.

        Args:
            book: An open piecash book session.
            description: The proposed transaction description.
            trans_date: The proposed transaction date (for the
                duplicate-window filter).
            proposed_amounts: Absolute values of the proposed splits
                (for the duplicate amount-signal check). Unused when
                ``want_duplicates`` is False; pass ``[]`` in that case.
            want_auto_fill: Find the most recent matching-description
                transaction and extract its splits for auto-fill.
            want_stability: Compare account patterns across the recent
                matching history; warn when they disagree (auto-fill
                would draw from inconsistent data).
            want_duplicates: Score transactions in the ±``window``
                range on description, amount, and date; emit HIGH /
                MEDIUM candidates.
            want_recent: Keep the top N matching-description
                transactions for the post-write split-consistency
                warning.
            duplicate_window_days: ±N day window for duplicate search.
            stability_days: Lookback horizon for stability signal.
            stability_limit: Cap on how many recent matches the
                stability check inspects.
            recent_days: Lookback horizon for post-write recent-match.
            recent_limit: Cap on post-write recent matches.

        Returns:
            A ``_CreateSignals`` bundle. Untracked signals remain at
            their default (None / empty list).
        """
        today = date.today()
        stability_cutoff = today - timedelta(days=stability_days)
        recent_cutoff = today - timedelta(days=recent_days)
        dup_start = trans_date - timedelta(days=duplicate_window_days)
        dup_end = trans_date + timedelta(days=duplicate_window_days)
        desc_lower = description.lower()

        # Short-guid prefix map built once, shared across all emitted
        # guids (auto-fill source, duplicates). Caller never sees the
        # raw 32-char guid — prefixes flow straight into tool responses.
        # Cached on BaseGnuCashBook by book mtime.
        emitting_guids = want_auto_fill or want_duplicates
        txn_prefixes = (
            self._transaction_prefix_map(book)
            if emitting_guids
            else {}
        )

        # Template-account GUID set — used to skip scheduled-transaction
        # template transactions. GnuCash persists each SX's split
        # template as a real Transaction row whose splits post to
        # accounts under book.root_template. Those are recipes, not
        # events — a user entering a mortgage payment for the first
        # time would otherwise always see the Mortgage template as a
        # "duplicate" candidate via description + date match, even
        # with a stale template amount that kills the A signal.
        # Filtering at the iteration boundary blocks leakage into
        # every bucket the collector fills: auto-fill, stability,
        # duplicates, and recent-matches.
        template_guids = self._template_account_guids(book)

        # Proposed primary — the headline amount (max abs split value)
        # used by the duplicate amount-signal. Computed once; when
        # ``want_duplicates`` is False the caller passed ``[]`` and
        # this stays zero (harmless — the amount-signal branch never
        # runs on that code path).
        proposed_primary = max(proposed_amounts) if proposed_amounts else Decimal("0")

        # Local accumulators — each bucket is independent. We finalize
        # them into the returned _CreateSignals after the loop.
        auto_fill_source = None  # piecash.Transaction
        stability_matches: list = []  # list[piecash.Transaction]
        recent_matches: list = []  # list[piecash.Transaction]
        duplicates: list[dict] = []

        # One sort, one iteration. Descending by post_date so auto-fill
        # and the "recent" / "stability" buckets fill from most-recent
        # outward — their caps short-circuit the rest.
        sorted_txns = sorted(
            book.transactions, key=lambda t: t.post_date, reverse=True
        )

        for txn in sorted_txns:
            # Skip scheduled-transaction template rows — their splits
            # post to Template Accounts, they have no bearing on the
            # user's chart, and they'd otherwise fire D-D matches on
            # every near-cadence description (mortgage, HOA, auto
            # loans, etc.), training the user to ignore the
            # duplicate warning.
            if template_guids and any(
                s.account.guid in template_guids for s in txn.splits
            ):
                continue

            txn_desc_lower = txn.description.lower()
            desc_match = (
                desc_lower in txn_desc_lower
                or txn_desc_lower in desc_lower
            )

            # --- Auto-fill: first description match wins ---
            if want_auto_fill and auto_fill_source is None and desc_match:
                auto_fill_source = txn

            # --- Stability: recent matches within horizon, capped ---
            if (
                want_stability
                and desc_match
                and txn.post_date >= stability_cutoff
                and len(stability_matches) < stability_limit
            ):
                stability_matches.append(txn)

            # --- Recent: for post-write split-consistency warning ---
            if (
                want_recent
                and desc_match
                and txn.post_date >= recent_cutoff
                and len(recent_matches) < recent_limit
            ):
                recent_matches.append(txn)

            # --- Duplicates: multi-signal check within date window ---
            if (
                want_duplicates
                and dup_start <= txn.post_date <= dup_end
            ):
                # Signal 2: proposed PRIMARY amount (max abs split
                # value) within ±$1.00 of candidate's primary amount.
                #
                # Earlier iterations compared any-to-any across every
                # split pair. On multi-split transactions (paychecks
                # with 10+ deduction splits) that produced
                # false-positive MEDIUM matches whenever a tiny
                # deduction happened to land within ±$1 of a
                # candidate's amount — e.g. a paycheck-vs-coffee-shop
                # match because some $5-ish union/medicare deduction
                # sat near the coffee's $5.67 total. "Primary" means
                # the headline number a human reading the register
                # uses to recognize a transaction; matching on that
                # kills the noise without losing real duplicates
                # (paycheck-vs-paycheck still matches on gross).
                primary_amount = max(abs(s.value) for s in txn.splits)
                amount_match = (
                    abs(proposed_primary - primary_amount) <= Decimal("1.00")
                )

                # Signal 3: date within ±2 days of trans_date (tighter
                # than the window filter — window is for "worth
                # considering at all").
                date_match = abs((txn.post_date - trans_date).days) <= 2

                signals = sum([desc_match, amount_match, date_match])
                if signals >= 2:
                    confidence = "HIGH" if signals == 3 else "MEDIUM"
                    signal_str = (
                        ("D" if desc_match else "-")
                        + ("A" if amount_match else "-")
                        + ("D" if date_match else "-")
                    )
                    duplicates.append({
                        "confidence": confidence,
                        "guid": txn_prefixes[txn.guid],
                        "date": txn.post_date.isoformat(),
                        "description": txn.description,
                        "amount": str(primary_amount),
                        "signals": signal_str,
                    })

        # --- Finalize the bundle ---

        # Auto-fill: build (splits_list, source_info) if we found one.
        auto_fill: tuple[list[dict], dict] | None = None
        if auto_fill_source is not None:
            filled_splits = []
            for s in auto_fill_source.splits:
                split_dict = {
                    "account": s.account.fullname,
                    "amount": str(s.value),
                }
                if s.quantity != s.value:
                    split_dict["quantity"] = str(s.quantity)
                if s.memo:
                    split_dict["memo"] = s.memo
                filled_splits.append(split_dict)
            source_info = {
                "guid": txn_prefixes[auto_fill_source.guid],
                "description": auto_fill_source.description,
                "date": auto_fill_source.post_date.isoformat(),
            }
            auto_fill = (filled_splits, source_info)

        # Stability: if 2+ recent matches disagree on pattern, warn.
        stability_warnings: list[dict] = []
        if want_stability and len(stability_matches) >= 2:
            patterns = [
                self._extract_account_pattern([s.account for s in t.splits])
                for t in stability_matches
            ]
            first_pattern = patterns[0]
            different = sum(1 for p in patterns[1:] if p != first_pattern)
            if different > 0:
                stability_warnings.append({
                    "type": "auto_fill_unstable",
                    "message": (
                        f"Recent '{description}' transactions use different "
                        f"account patterns. Auto-filled from most recent "
                        f"({stability_matches[0].post_date.isoformat()}), but "
                        f"{different} of {len(stability_matches)} recent "
                        f"matches used different categorization."
                    ),
                })

        # Duplicates: sort HIGH first.
        order = {"HIGH": 0, "MEDIUM": 1}
        duplicates.sort(key=lambda c: order[c["confidence"]])

        return _CreateSignals(
            auto_fill=auto_fill,
            stability_warnings=stability_warnings,
            duplicates=duplicates,
            recent_matches=recent_matches,
        )

    @staticmethod
    def _duplicates_to_tsv(duplicates: list[dict]) -> str:
        """Render the duplicate-candidates list as a compact TSV string.

        Each duplicate becomes one tab-separated line in this column
        order (no header — documented in ``create_transaction``'s
        docstring so the LLM knows the shape without paying for a
        header row every call)::

            confidence<TAB>guid<TAB>date<TAB>amount<TAB>description<TAB>signals

        A list-of-dicts JSON response to the bookkeeper was
        ~120 chars per candidate; the TSV form is closer to 40. The
        rejection path emits two or three candidates typically, and
        the savings compound when the LLM retries a mis-hit.

        Returns ``""`` for empty input. ``_strip_noise`` in the
        response serializer drops empty-string values, so callers can
        unconditionally assign ``result["duplicates"] = _duplicates_to_tsv(...)``
        without worrying about an empty-key leak into the output.

        The internal ``_CreateSignals.duplicates`` list-of-dicts is
        kept rich so ``has_high_duplicate`` (and any future callers
        that need to reason about matches) can still read structured
        fields — only the response boundary renders TSV.
        """
        return "\n".join(
            f"{d['confidence']}\t{d['guid']}\t{d['date']}\t"
            f"{d['amount']}\t{d['description']}\t{d['signals']}"
            for d in duplicates
        )

    @staticmethod
    def _generate_warnings(
        trans_date: date,
        splits: list[dict],
        accounts: list,
    ) -> list[dict]:
        """Generate warnings for unusual but valid transaction attributes.

        Args:
            trans_date: Transaction date.
            splits: Original split dicts with 'amount' keys.
            accounts: Resolved piecash account objects, same order as splits.

        Returns:
            List of warning dicts with 'type' and 'message' keys.
        """
        warnings = []
        today = date.today()

        if trans_date > today:
            warnings.append({
                "type": "future_date",
                "message": f"Transaction date {trans_date.isoformat()} is in the future",
            })

        days_old = (today - trans_date).days
        if days_old > 365:
            warnings.append({
                "type": "old_date",
                "message": (
                    f"Transaction date {trans_date.isoformat()} "
                    f"is {days_old} days in the past"
                ),
            })

        for split_data, account in zip(splits, accounts):
            amount = _to_decimal(split_data["amount"])
            if account.type == "EXPENSE" and amount < 0:
                warnings.append({
                    "type": "negative_expense",
                    "message": (
                        f"Negative amount ({amount}) to expense account "
                        f"'{account.fullname}'"
                    ),
                })
            elif account.type == "INCOME" and amount > 0:
                warnings.append({
                    "type": "positive_income",
                    "message": (
                        f"Positive amount ({amount}) to income account "
                        f"'{account.fullname}'"
                    ),
                })

        return warnings

    def _validate_transaction_splits(
        self,
        book: piecash.Book,
        splits: list[dict],
        trans_currency: piecash.Commodity,
    ) -> list[dict]:
        """Validate splits and pre-resolve accounts — before any mutation.

        Single chokepoint for the input-shape rules that both
        ``create_transaction`` and ``update_transaction`` enforce:

        - Splits sum to zero (in transaction currency).
        - Every ``account`` reference resolves to a real Account
          (accepts path, ``%short``, or full GUID).
        - For each split, the ``quantity`` is either implicit
          (account commodity == transaction currency, in which case
          ``quantity == value``) or explicit (cross-currency, caller
          must supply ``quantity`` with the same sign as ``value``).

        Pre-extraction, ``update_transaction`` interleaved these
        checks with the mutation loop — the first split would have
        ``split.value`` reassigned BEFORE the sibling split's
        cross-currency quantity was validated, so a bad-input
        update could leave the transaction in a partial state if
        the session didn't rollback cleanly. Validating everything
        up-front makes the subsequent mutation pass effectively
        infallible.

        Args:
            book: Open piecash book.
            splits: Input splits (each with ``account``, ``amount``,
                optionally ``quantity`` and ``memo``).
            trans_currency: piecash Commodity to validate sign-and-
                quantity rules against. For create this is the
                user-supplied or default currency; for update this
                is the existing transaction's currency (update does
                not allow changing currency).

        Returns:
            List of dicts (same order as input) with keys
            ``account`` (resolved piecash Account), ``value``
            (Decimal), ``quantity`` (Decimal), ``memo`` (string or
            ``None``), ``original_ref`` (raw input ref — preserved
            for error messages downstream).

        Raises:
            ValueError on imbalance, account not found, cross-
            currency split missing ``quantity``, or value/quantity
            sign mismatch.
        """
        total = Decimal("0")
        for split in splits:
            total += _to_decimal(split["amount"])
        if total != Decimal("0"):
            raise ValueError(f"Splits do not balance: total is {total}")

        resolved: list[dict] = []
        for split in splits:
            ref = split["account"]
            account = self._resolve_account(book, ref)
            if not account:
                raise ValueError(f"Account not found: {ref}")

            value = _to_decimal(split["amount"])
            if account.commodity == trans_currency:
                quantity = value
            elif "quantity" in split:
                quantity = _to_decimal(split["quantity"])
                if quantity * value < 0:
                    raise ValueError(
                        f"Split for '{ref}': quantity and value "
                        f"must have same sign "
                        f"(got value={value}, quantity={quantity})"
                    )
            else:
                raise ValueError(
                    f"Split for '{ref}' requires 'quantity' "
                    f"because account commodity "
                    f"({account.commodity.mnemonic}) differs from "
                    f"transaction currency ({trans_currency.mnemonic})"
                )

            resolved.append({
                "account": account,
                "value": value,
                "quantity": quantity,
                "memo": split.get("memo"),
                "original_ref": ref,
            })

        return resolved

    def create_transaction(
        self,
        description: str,
        splits: list[dict] | None = None,
        trans_date: date | None = None,
        currency: str | None = None,
        notes: str | None = None,
        check_duplicates: bool = True,
        force_create: bool = False,
        dry_run: bool = False,
    ) -> dict:
        """Create a new transaction with splits.

        Args:
            description: Transaction description.
            splits: List of splits, each with:
                - 'account' (required): Full account path.
                - 'amount' (required): Value in transaction currency.
                - 'quantity' (optional): Amount in account's commodity.
                  Required if account commodity differs from transaction currency.
                - 'memo' (optional): Split memo.
                If omitted or empty, auto-fills from the most recent
                transaction with a matching description.
            trans_date: Transaction date. Defaults to today.
            currency: ISO currency code for the transaction (e.g., "USD", "EUR").
                      Defaults to book's default currency.
            notes: Transaction notes (optional). Free-text annotation
                   stored separately from the description.
            check_duplicates: Run duplicate detection. Default True.
            force_create: Create even if HIGH confidence duplicates found.
            dry_run: Validate and return proposal without writing.

        Returns:
            Dict with 'guid' and 'status' keys. May include 'warnings',
            'duplicates', and 'auto_filled_from'. If a HIGH duplicate is
            found and force_create is False, returns 'status': 'rejected'
            instead. In dry_run mode, returns 'dry_run': True with
            proposed transaction.

            The 'duplicates' field, when present, is a newline-separated
            TSV string (not a list of dicts) with columns::

                confidence<TAB>guid<TAB>date<TAB>amount<TAB>description<TAB>signals

            Confidence is ``HIGH`` or ``MEDIUM``. Signals is a
            three-char code (D/A/D for description / amount / date,
            dash for no match). See ``_duplicates_to_tsv``.

        Raises:
            ValueError: If splits don't balance, fewer than 2 splits,
                       accounts don't exist, cross-currency splits
                       missing quantity, or no match found for auto-fill.
        """
        # Dry runs don't need a writable session; all other paths do.
        readonly = dry_run
        if trans_date is None:
            trans_date = date.today()

        # One book-open for the whole create pipeline — preflight signal
        # gathering, write, and post-write consistency warning all live
        # inside this session.
        with self.open(readonly=readonly) as book:
            # --- Preflight pass 1: auto-fill + stability (if needed) ---
            #
            # When splits=None, we need the auto-fill source before we
            # can compute proposed_amounts for the duplicate scan. So
            # this first pass requests only auto-fill + stability, then
            # the second pass runs duplicates + recent with the now-known
            # amounts. When splits are provided up front, we skip pass 1
            # and do everything in pass 2 — a single scan.
            auto_filled_from = None
            auto_fill_warnings: list[dict] = []
            if not splits:
                preflight = self._collect_create_signals(
                    book,
                    description,
                    trans_date,
                    proposed_amounts=[],
                    want_auto_fill=True,
                    want_stability=True,
                    want_duplicates=False,
                    want_recent=False,
                )
                if preflight.auto_fill is None:
                    raise ValueError(
                        "No matching transaction found for auto-fill. "
                        "Provide explicit splits."
                    )
                splits, auto_filled_from = preflight.auto_fill
                auto_fill_warnings = preflight.stability_warnings

            if len(splits) < 2:
                raise ValueError("Transaction must have at least 2 splits")

            # Sum-to-zero / account-resolution / cross-currency
            # sign-and-quantity checks live in the shared validator
            # paired with update_transaction. Splits sum is computed
            # with ``_to_decimal`` (str-routed) so a float that slipped
            # past the pydantic boundary decimalizes via shortest-repr
            # rather than embedding IEEE-754 epsilon in the sum.
            #
            # Currency resolution still happens here (create accepts
            # ``currency=`` user input; update reuses the existing
            # transaction's currency) so the validator gets the right
            # ``trans_currency`` to compare account commodities against.

            proposed_amounts = [abs(_to_decimal(s["amount"])) for s in splits]

            # --- Preflight pass 2: duplicates + recent matches ---
            signals = self._collect_create_signals(
                book,
                description,
                trans_date,
                proposed_amounts,
                want_auto_fill=False,
                want_stability=False,
                want_duplicates=check_duplicates,
                want_recent=True,
            )
            duplicates = signals.duplicates

            # HIGH-confidence duplicate short-circuits the write. The
            # rejection always carries at least one candidate, so
            # rendering TSV directly is safe (no empty-string case).
            if (
                signals.has_high_duplicate
                and not force_create
                and not dry_run
            ):
                return {
                    "status": "rejected",
                    "reason": "duplicate_detected",
                    "duplicates": self._duplicates_to_tsv(duplicates),
                }

            # --- Validate accounts and build piecash splits ---
            # Currency resolution: writable sessions may auto-create the
            # currency via ISO fallback; dry_run uses readonly and must
            # find an existing one (or default).
            if currency is None:
                trans_currency = self._require_default_currency(book)
            elif readonly:
                trans_currency = self._find_commodity(book, currency)
                if not trans_currency:
                    raise ValueError(
                        f"Currency '{currency}' not found in book. "
                        f"Dry run cannot create new currencies."
                    )
            else:
                trans_currency = self._get_or_create_currency(book, currency)

            # Shared validator: sum-to-zero, account resolution,
            # cross-currency quantity/sign. Returns pre-resolved
            # piecash Accounts + Decimal value/quantity per split,
            # so the placeholder check and Split construction below
            # are pure motion against validated data.
            validated = self._validate_transaction_splits(
                book, splits, trans_currency,
            )

            piecash_splits = []
            resolved_accounts = []
            for v in validated:
                account = v["account"]
                if account.placeholder:
                    children_hint = ", ".join(
                        c.fullname for c in account.children
                    )
                    raise ValueError(
                        f"Account '{account.fullname}' is a placeholder and "
                        f"cannot receive transactions. "
                        f"Use one of: {children_hint}"
                    )

                resolved_accounts.append(account)

                # Don't construct piecash.Split objects during dry_run —
                # adding to the session would stage a write even if we
                # never call book.save().
                if not readonly:
                    piecash_splits.append(
                        piecash.Split(
                            account=account,
                            value=v["value"],
                            quantity=v["quantity"],
                            memo=v["memo"] or "",
                        )
                    )

            # --- Warnings (shared by dry_run and write paths) ---
            warnings = self._generate_warnings(
                trans_date, splits, resolved_accounts
            )
            proposed_pattern = self._extract_account_pattern(resolved_accounts)
            # The collector gathered recent matches before we wrote, so
            # the just-created txn is automatically absent — no post-
            # write exclusion needed.
            if signals.recent_matches:
                recent_accts = [
                    s.account for s in signals.recent_matches[0].splits
                ]
                recent_pattern = self._extract_account_pattern(recent_accts)
                if proposed_pattern != recent_pattern:
                    warnings.append({
                        "type": "split_consistency",
                        "message": (
                            f"Recent '{signals.recent_matches[0].description}' "
                            f"transactions used "
                            f"{', '.join(sorted(recent_pattern))}, but this "
                            f"transaction uses "
                            f"{', '.join(sorted(proposed_pattern))}."
                        ),
                    })
            warnings.extend(auto_fill_warnings)

            # --- Dry run branches out here with the proposal ---
            if dry_run:
                result: dict = {
                    "dry_run": True,
                    "warnings": warnings,
                    "duplicates": self._duplicates_to_tsv(duplicates),
                }
                if auto_filled_from:
                    result["auto_filled_from"] = auto_filled_from
                return result

            # --- Commit the write ---
            transaction = piecash.Transaction(
                currency=trans_currency,
                description=description,
                notes=notes,
                post_date=trans_date,
                splits=piecash_splits,
            )
            book.save()

            # Emit a collision-safe short guid prefix so the LLM can feed
            # it straight back into guid-accepting tools without spending
            # tokens on the full 32-char string.
            short_guid = _unique_prefix(
                transaction.guid, (t.guid for t in book.transactions)
            )
            result = {"guid": short_guid, "status": "created"}
            if warnings:
                result["warnings"] = warnings
            if duplicates:
                result["duplicates"] = self._duplicates_to_tsv(duplicates)
            if auto_filled_from:
                result["auto_filled_from"] = auto_filled_from
            return result

    def search_transactions(
        self,
        query: str,
        field: str = "description",
        limit: int = 50,
        compact: bool = True,
    ) -> list[dict] | str:
        """Search transactions by field.

        Truncation behavior mirrors ``list_transactions``: compact mode
        appends a notice when matches exceed ``limit``; limits above
        ``MAX_LIST_LIMIT`` (250) are clamped.

        Args:
            query: Search string. For 'amount' field, supports:
                   - Exact: "100.00"
                   - Greater than: ">100"
                   - Less than: "<100"
                   - Range: "100-200"
            field: Field to search: 'description', 'memo', 'notes',
                   or 'amount'.
            limit: Maximum number of transactions to return. Capped at 250.
            compact: If True (default), return a compact newline-separated
                     string with one line per transaction. If False, return
                     the full list of transaction dicts.

        Returns:
            If compact: newline-separated string of transaction lines, with
                a ``[Showing N of M ...]`` notice appended when truncated.
            If not compact: list of matching transaction dicts.

        Raises:
            ValueError: If field is not valid.
        """
        if field not in ("description", "memo", "notes", "amount"):
            raise ValueError(f"Invalid search field: {field}")

        capped = limit > self.MAX_LIST_LIMIT
        effective_limit = min(limit, self.MAX_LIST_LIMIT)

        with self.open(readonly=True) as book:
            matched = []

            for transaction in book.transactions:
                if field == "description":
                    if query.lower() in transaction.description.lower():
                        matched.append(transaction)

                elif field == "notes":
                    if transaction.notes and query.lower() in transaction.notes.lower():
                        matched.append(transaction)

                elif field == "memo":
                    for split in transaction.splits:
                        if split.memo and query.lower() in split.memo.lower():
                            matched.append(transaction)
                            break

                elif field == "amount":
                    if self._match_amount(transaction, query):
                        matched.append(transaction)

            # Sort by date descending
            matched.sort(key=lambda t: t.post_date, reverse=True)

            total_matched = len(matched)
            matched = matched[:effective_limit]

            if compact:
                # Collision-safe prefix map over the whole transactions
                # table — cached on BaseGnuCashBook by book mtime.
                prefixes = self._transaction_prefix_map(book)
                lines = [
                    _transaction_to_compact_line(t, prefixes=prefixes)
                    for t in matched
                ]
                notice = self._truncation_notice(
                    total=total_matched,
                    shown=len(matched),
                    effective_limit=effective_limit,
                    capped=capped,
                    suggest_narrow=False,  # no date-range hint on search
                )
                if notice:
                    lines.append(notice)
                return "\n".join(lines)
            else:
                return [_transaction_to_dict(t) for t in matched]

    def _match_amount(self, transaction: piecash.Transaction, query: str) -> bool:
        """Check if any split amount matches the query.

        Args:
            transaction: Transaction to check.
            query: Amount query (exact, >N, <N, or N-M range).

        Returns:
            True if any split matches.

        Raises:
            ValueError: If the amount query is malformed.
        """
        # Get absolute values of all splits
        amounts = [abs(split.value) for split in transaction.splits]

        # Parse query
        query = query.strip()

        try:
            # Greater than: >100
            if query.startswith(">"):
                threshold = Decimal(query[1:])
                return any(amt > threshold for amt in amounts)

            # Less than: <100
            if query.startswith("<"):
                threshold = Decimal(query[1:])
                return any(amt < threshold for amt in amounts)

            # Range: 100-200
            if "-" in query and not query.startswith("-"):
                parts = query.split("-")
                if len(parts) == 2:
                    low = Decimal(parts[0])
                    high = Decimal(parts[1])
                    return any(low <= amt <= high for amt in amounts)

            # Exact match
            target = Decimal(query)
            return any(amt == target for amt in amounts)

        except InvalidOperation as e:
            raise ValueError(f"Invalid amount query '{query}': {e}") from e

    # Valid GnuCash account types
    VALID_ACCOUNT_TYPES = {
        "ASSET",
        "BANK",
        "CASH",
        "CREDIT",
        "EQUITY",
        "EXPENSE",
        "INCOME",
        "LIABILITY",
        "MUTUAL",
        "PAYABLE",
        "RECEIVABLE",
        "STOCK",
    }

    def create_account(
        self,
        name: str,
        account_type: str,
        parent: str | None = None,
        description: str = "",
        placeholder: bool = False,
        commodity: str | None = None,
        commodity_namespace: str = "CURRENCY",
    ) -> dict:
        """Create a new account in the chart of accounts.

        Args:
            name: Account name (e.g., "AI Subscriptions").
            account_type: GnuCash account type (ASSET, EXPENSE, etc.).
            parent: Full path of parent account (e.g., "Expenses:Online Services").
                    If omitted, creates a top-level account at the book root.
            description: Optional description.
            placeholder: If True, account is container-only. Default False.
            commodity: ISO currency code (e.g., "USD", "EUR") or commodity mnemonic.
                       Defaults to book's default currency.
            commodity_namespace: Commodity namespace for non-currency commodities.
                                Default "CURRENCY".

        Returns:
            Dict with guid, fullname, and status. Includes a warning if
            created at root level.

        Raises:
            ValueError: If parent not found, invalid type, duplicate name,
                       or invalid commodity.
        """
        # Validate account type
        if account_type.upper() not in self.VALID_ACCOUNT_TYPES:
            raise ValueError(
                f"Invalid account type: {account_type}. "
                f"Valid types: {', '.join(sorted(self.VALID_ACCOUNT_TYPES))}"
            )

        with self.open(readonly=False) as book:
            # Determine parent account
            is_root_level = parent is None
            if is_root_level:
                parent_account = book.root_account
                parent_label = "root"
            else:
                parent_account = self._resolve_account(book, parent)
                if not parent_account:
                    raise ValueError(f"Parent account not found: {parent}")
                parent_label = parent

            # Check for duplicate - same name under same parent
            for child in parent_account.children:
                if child.name == name:
                    raise ValueError(
                        f"Account '{name}' already exists under '{parent_label}'"
                    )

            # Determine commodity
            if commodity is None:
                account_commodity = self._require_default_currency(book)
            elif commodity_namespace == "CURRENCY":
                account_commodity = self._get_or_create_currency(book, commodity)
            else:
                account_commodity = self._find_commodity(
                    book, commodity, commodity_namespace
                )
                if not account_commodity:
                    raise ValueError(
                        f"Commodity not found: {commodity_namespace}:{commodity}"
                    )

            # Create the account
            new_account = piecash.Account(
                name=name,
                type=account_type.upper(),
                parent=parent_account,
                commodity=account_commodity,
                description=description,
                placeholder=placeholder,
            )

            book.save()

            short_guid = _unique_prefix(
                new_account.guid, (a.guid for a in book.accounts)
            )
            result = {
                "guid": short_guid,
                "fullname": new_account.fullname,
                "status": "created",
            }
            if is_root_level:
                result["warning"] = (
                    "Account created at root level, outside the standard "
                    "account hierarchy (Assets, Liabilities, Equity, Income, "
                    "Expenses). This may affect reports and balance sheet "
                    "calculations."
                )
            return result

    # Polarity groups for account type change validation.
    # Types within the same group can be freely converted.
    _ACCOUNT_TYPE_POLARITY = {
        "ASSET": "debit_asset",
        "BANK": "debit_asset",
        "CASH": "debit_asset",
        "RECEIVABLE": "debit_asset",
        "LIABILITY": "credit_liability",
        "CREDIT": "credit_liability",
        "PAYABLE": "credit_liability",
        "INCOME": "credit_income",
        "EXPENSE": "debit_expense",
        "EQUITY": "credit_equity",
        "STOCK": "debit_investment",
        "MUTUAL": "debit_investment",
    }

    def update_account(
        self,
        name: str,
        new_name: str | None = None,
        description: str | None = None,
        placeholder: bool | None = None,
        account_type: str | None = None,
    ) -> dict:
        """Update an existing account's properties.

        Args:
            name: Full account path to update (e.g., "Expenses:Groceries").
            new_name: New name for the account (just the name, not full path).
            description: New description.
            placeholder: New placeholder status.
            account_type: New account type (e.g., "CREDIT", "BANK"). Only
                changes within the same debit/credit polarity family are
                allowed (e.g., LIABILITY to CREDIT, ASSET to BANK).

        Returns:
            Dict with updated account details.

        Raises:
            ValueError: If account not found, new name conflicts, or type
                change would flip debit/credit polarity.
        """
        with self.open(readonly=False) as book:
            account = self._resolve_account(book, name)
            if not account:
                raise ValueError(f"Account not found: {name}")

            # Stage pre-mutation state for the audit log so the decorator
            # doesn't have to reopen the book to capture it.
            self._stage_audit_before(_account_to_dict(account))

            # Track which fields the caller actually changed. Echoing
            # only the diff (vs. the entire account record) keeps the
            # write response small and tells the caller exactly what
            # landed — matches the ``update_transaction`` precedent.
            changed: dict = {}

            # Check for name conflict if renaming
            if new_name and new_name != account.name:
                if account.parent:
                    for sibling in account.parent.children:
                        if sibling.name == new_name and sibling.guid != account.guid:
                            raise ValueError(
                                f"Account '{new_name}' already exists under "
                                f"'{account.parent.fullname}'"
                            )
                account.name = new_name
                changed["name"] = new_name

            if description is not None and description != account.description:
                account.description = description
                changed["description"] = description

            if placeholder is not None and bool(placeholder) != bool(account.placeholder):
                account.placeholder = placeholder
                changed["placeholder"] = bool(placeholder)

            if account_type is not None:
                new_type = account_type.upper()
                old_type = account.type

                if new_type not in self.VALID_ACCOUNT_TYPES:
                    raise ValueError(
                        f"Invalid account type: {new_type}. "
                        f"Valid types: {', '.join(sorted(self.VALID_ACCOUNT_TYPES))}"
                    )

                if new_type != old_type:
                    old_polarity = self._ACCOUNT_TYPE_POLARITY.get(old_type)
                    new_polarity = self._ACCOUNT_TYPE_POLARITY.get(new_type)

                    if old_polarity != new_polarity:
                        raise ValueError(
                            f"Cannot change account type from {old_type} to "
                            f"{new_type} — this would flip the debit/credit "
                            f"polarity and corrupt existing transaction balances."
                        )

                    account.type = new_type
                    changed["type"] = new_type

            book.save()

            return {
                "guid": self._account_short_guid(book, account),
                **changed,
                "status": "updated",
            }

    def move_account(self, name: str, new_parent: str) -> dict:
        """Move an account to a new parent in the hierarchy.

        Args:
            name: Full account path to move (e.g., "Expenses:Old:Account").
            new_parent: Full path of the new parent account.

        Returns:
            Dict with updated account details including new fullname.

        Raises:
            ValueError: If account or parent not found, or would create cycle.
        """
        with self.open(readonly=False) as book:
            account = self._resolve_account(book, name)
            if not account:
                raise ValueError(f"Account not found: {name}")

            # Stage pre-move state (fullname derives old parent for log).
            self._stage_audit_before(_account_to_dict(account))

            new_parent_account = self._resolve_account(book, new_parent)
            if not new_parent_account:
                raise ValueError(f"Parent account not found: {new_parent}")

            # Check for circular reference (can't move to self or descendant)
            check = new_parent_account
            while check:
                if check.guid == account.guid:
                    raise ValueError(
                        f"Cannot move account under itself or its descendants"
                    )
                check = check.parent

            # Check for name conflict in new location
            for sibling in new_parent_account.children:
                if sibling.name == account.name:
                    raise ValueError(
                        f"Account '{account.name}' already exists under '{new_parent}'"
                    )

            account.parent = new_parent_account

            book.save()

            return {
                "guid": self._account_short_guid(book, account),
                # Both ``fullname`` (the new path) and ``parent`` (the
                # destination) are useful here: ``fullname`` answers
                # "where did it land?" and ``parent`` makes the move
                # legible without re-parsing the path.
                "fullname": account.fullname,
                "parent": account.parent.fullname,
                "status": "moved",
            }

    def delete_account(self, name: str) -> dict:
        """Delete an account from the chart of accounts.

        Args:
            name: Full account path to delete.

        Returns:
            Dict with deleted account info and status.

        Raises:
            ValueError: If account not found, has children, or has transactions.
        """
        with self.open(readonly=False) as book:
            account = self._resolve_account(book, name)
            if not account:
                raise ValueError(f"Account not found: {name}")

            # Safeguard: Check for children
            if account.children:
                child_names = [c.name for c in account.children]
                raise ValueError(
                    f"Cannot delete account with children: {', '.join(child_names)}"
                )

            # Safeguard: Check for transactions (splits)
            if account.splits:
                raise ValueError(
                    f"Cannot delete account with {len(account.splits)} transaction(s). "
                    f"Move or delete transactions first."
                )

            # Stage pre-delete state for the audit log.
            self._stage_audit_before(_account_to_dict(account))

            # Capture info before deletion. Pre-fix the response
            # included a short-prefix GUID computed against
            # ``book.accounts`` BEFORE the delete — but the LLM
            # would receive a handle pointing at a row that no
            # longer exists. ``_resolve_guid`` would then raise
            # "No account" on any subsequent attempt to use it.
            # Returning ``fullname`` and ``status="deleted"`` is
            # enough for the audit-log human reader and the LLM to
            # confirm what was deleted; the short GUID was always
            # unaddressable post-delete and just invited misuse.
            result = {
                "fullname": account.fullname,
                "status": "deleted",
            }

            book.session.delete(account)
            book.save()

            return result

    def delete_transaction(self, guid: str, force: bool = False) -> dict:
        """Delete a transaction by GUID.

        Args:
            guid: Transaction GUID (32-character hex string).
            force: If True, allow deleting transactions with reconciled splits.

        Returns:
            Dict with guid, description, and status.

        Raises:
            ValueError: If transaction not found, or has reconciled splits
                       and force is False.
        """
        with self.open(readonly=False) as book:
            transaction = self._find_transaction(book, guid)
            if not transaction:
                raise ValueError(f"Transaction not found: {guid}")

            # Reject if this transaction is the posting record for
            # an invoice or bill. Deleting it directly orphans the
            # invoice's posted-state metadata (date_posted, post_txn,
            # post_lot, post_acc fields all reference objects that
            # no longer exist) — the invoice then refuses both
            # delete ("posted") and re-post ("already posted") and
            # the only escape is SQL surgery. Force the caller
            # through unpost_invoice, which clears the metadata as
            # part of removing the transaction.
            from sqlalchemy import text
            posting_for = book.session.execute(
                text("SELECT id FROM invoices WHERE post_txn = :guid"),
                {"guid": transaction.guid},
            ).fetchone()
            if posting_for:
                raise ValueError(
                    f"Transaction is the posting record for invoice "
                    f"{posting_for[0]}. Use unpost_invoice first."
                )

            # Check for reconciled splits
            reconciled = [
                s for s in transaction.splits if s.reconcile_state == "y"
            ]
            if reconciled and not force:
                acct_names = ", ".join(s.account.fullname for s in reconciled)
                raise ValueError(
                    f"Transaction has reconciled splits in: {acct_names}. "
                    f"Deleting will break reconciliation. Use force=true to override."
                )

            # Stage pre-delete state for the audit log.
            self._stage_audit_before(_transaction_to_dict(transaction))

            # Short guid computed pre-delete (target still in the table).
            short_guid = _unique_prefix(
                transaction.guid, (t.guid for t in book.transactions)
            )
            result = {
                "guid": short_guid,
                "description": transaction.description,
                "status": "deleted",
            }
            if reconciled:
                result["reconciled_splits_affected"] = len(reconciled)

            # Delete the transaction
            book.session.delete(transaction)
            book.save()

            return result

    def _verify_transaction_state(
        self,
        book,
        transaction,
        *,
        expected_description: str | None = None,
        expected_date: date | None = None,
        expected_notes: str | None = None,
        expected_splits: list[dict] | None = None,
    ) -> None:
        """Re-load the transaction from disk and verify expected
        fields landed. Closes the gap CLAUDE.md's "Every write is
        verified" invariant calls out for ``update_transaction`` and
        ``replace_splits``.

        Pre-fix, both methods called ``book.save()`` and trusted the
        result. piecash has historically silently no-op'd setattrs
        on some slot-backed fields; without this round-trip the
        thin response could lie about what's stored. Bypasses the
        ORM identity map via ``session.expire`` so we read what's
        actually on disk, not what we just put in the cache.

        Raises RuntimeError on any mismatch.
        """
        book.session.expire(transaction)

        if expected_description is not None:
            actual_desc = transaction.description
            if actual_desc != expected_description:
                raise RuntimeError(
                    f"Transaction write verification failed: "
                    f"description on disk is {actual_desc!r}, "
                    f"expected {expected_description!r}"
                )

        if expected_date is not None:
            actual_date = transaction.post_date
            if hasattr(actual_date, "date") and callable(actual_date.date):
                actual_date = actual_date.date()
            if actual_date != expected_date:
                raise RuntimeError(
                    f"Transaction write verification failed: "
                    f"post_date on disk is {actual_date}, "
                    f"expected {expected_date}"
                )

        if expected_notes is not None:
            actual_notes = transaction.notes or ""
            wanted = expected_notes if expected_notes else ""
            if actual_notes != wanted:
                raise RuntimeError(
                    f"Transaction write verification failed: "
                    f"notes on disk is {actual_notes!r}, "
                    f"expected {wanted!r}"
                )

        if expected_splits is not None:
            actual_splits = list(transaction.splits)
            actual_by_acct = {}
            for s in actual_splits:
                actual_by_acct[s.account.fullname] = (
                    Decimal(str(s.value)),
                    Decimal(str(s.quantity)),
                    s.memo or "",
                )
            if len(actual_splits) != len(expected_splits):
                raise RuntimeError(
                    f"Transaction write verification failed: "
                    f"{len(actual_splits)} splits on disk, "
                    f"expected {len(expected_splits)}"
                )
            for expected in expected_splits:
                # Normalize the input account ref to canonical
                # fullname before lookup. The book methods accept
                # full path, ``%short`` GUID, or full 32-char GUID;
                # post-save splits are keyed by ``Account.fullname``.
                # Pre-fix this comparison was string-vs-string against
                # the raw input, so a shortcut input like ``%77b59dd``
                # raised a false "split not found post-save" RuntimeError
                # even though the write had landed correctly.
                # (Bookkeeper finding from PR #75 review.)
                ref = expected["account"]
                resolved = self._resolve_account(book, ref)
                if resolved is None:
                    raise RuntimeError(
                        f"Transaction write verification failed: "
                        f"could not resolve split account ref "
                        f"{ref!r} (resolution returned None — the "
                        f"account may have been deleted between "
                        f"save and verify)"
                    )
                acct_fullname = resolved.fullname
                if acct_fullname not in actual_by_acct:
                    raise RuntimeError(
                        f"Transaction write verification failed: "
                        f"split for {acct_fullname!r} (input "
                        f"ref {ref!r}) not found post-save"
                    )
                actual_value, actual_qty, actual_memo = (
                    actual_by_acct[acct_fullname]
                )
                ev = _to_decimal(expected["amount"])
                if actual_value != ev:
                    raise RuntimeError(
                        f"Transaction write verification failed: "
                        f"split {acct_fullname!r} value on disk is "
                        f"{actual_value}, expected {ev}"
                    )
                if "quantity" in expected:
                    eq = _to_decimal(expected["quantity"])
                    if actual_qty != eq:
                        raise RuntimeError(
                            f"Transaction write verification failed: "
                            f"split {acct_fullname!r} quantity on "
                            f"disk is {actual_qty}, expected {eq}"
                        )

    def update_transaction(
        self,
        guid: str,
        description: str | None = None,
        trans_date: date | None = None,
        splits: list[dict] | None = None,
        notes: str | None = None,
        force: bool = False,
    ) -> dict:
        """Update an existing transaction.

        Args:
            guid: Transaction GUID to update.
            description: New description (optional).
            trans_date: New transaction date (optional).
            splits: List of split updates with 'account', 'amount', and
                    optionally 'quantity' (optional). Must match existing
                    splits by account name. For cross-currency splits,
                    'quantity' is required when the account commodity differs
                    from the transaction currency.
            notes: New transaction notes (optional). Pass empty string
                   to clear existing notes.
            force: If True, allow modifying transactions with reconciled
                   splits. Only checked when splits are being updated.

        Returns:
            Dict with updated transaction details.

        Raises:
            ValueError: If transaction not found, splits don't balance,
                       account not found in splits, cross-currency split
                       missing quantity, or has reconciled splits and
                       force is False.
        """
        with self.open(readonly=False) as book:
            transaction = self._find_transaction(book, guid)
            if not transaction:
                raise ValueError(f"Transaction not found: {guid}")

            # Check for reconciled splits when modifying splits
            if splits is not None:
                reconciled = [
                    s for s in transaction.splits if s.reconcile_state == "y"
                ]
                if reconciled and not force:
                    acct_names = ", ".join(
                        s.account.fullname for s in reconciled
                    )
                    raise ValueError(
                        f"Transaction has reconciled splits in: {acct_names}. "
                        f"Modifying will break reconciliation. "
                        f"Use force=true to override."
                    )

            # Stage pre-update state for the audit log.
            self._stage_audit_before(_transaction_to_dict(transaction))

            # Update description if provided
            if description is not None:
                transaction.description = description

            # Update notes if provided
            if notes is not None:
                transaction.notes = notes if notes else None

            # Update date if provided
            if trans_date is not None:
                transaction.post_date = trans_date

            # Update splits if provided
            if splits is not None:
                trans_currency = transaction.currency

                # Validate everything up-front via the shared validator:
                # sum-to-zero, account resolution (path / %short / full
                # GUID), cross-currency quantity/sign. Pre-extraction
                # the validation interleaved with mutation — the first
                # split's ``value`` was reassigned before the sibling's
                # cross-currency quantity was checked, so a bad-input
                # update could leave the transaction in a partial state
                # if the session didn't rollback cleanly. Now the
                # mutation pass below is effectively infallible.
                validated = self._validate_transaction_splits(
                    book, splits, trans_currency,
                )

                # Build a map keyed by resolved-account-fullname so we
                # can match against existing splits' ``account.fullname``.
                split_updates = {
                    v["account"].fullname: v for v in validated
                }
                # Preserve raw input dicts so ``memo`` updates (which
                # the validator doesn't carry) still apply.
                raw_by_fullname = {
                    v["account"].fullname: raw
                    for v, raw in zip(validated, splits)
                }

                # Update existing splits — pure mutation, validated above.
                for split in transaction.splits:
                    account_name = split.account.fullname
                    if account_name in split_updates:
                        v = split_updates[account_name]
                        split.value = v["value"]
                        split.quantity = v["quantity"]
                        raw = raw_by_fullname[account_name]
                        if "memo" in raw:
                            split.memo = raw["memo"]
                        del split_updates[account_name]

                # Check if all provided accounts were found in the txn.
                if split_updates:
                    missing = list(split_updates.keys())[0]
                    raise ValueError(f"Account not found in transaction: {missing}")

            book.save()

            # Verify the write landed — re-read the transaction from
            # disk and compare each field we tried to set. Honors the
            # ``Every write is verified`` invariant CLAUDE.md spells
            # out; pre-fix, ``update_transaction`` skipped this and a
            # piecash silent setattr no-op would have shipped a thin
            # response that lied about what was stored.
            self._verify_transaction_state(
                book, transaction,
                expected_description=description,
                expected_date=trans_date,
                expected_notes=notes,
                expected_splits=splits,
            )

            # Thin response — the LLM submitted the changes, so the only
            # fields worth echoing are enough for a quick sanity check
            # (guid + currently-stored description/date). If they want
            # the full post-update state they can call get_transaction.
            # The audit log resolves splits/description/date from params
            # when absent from after_state (see _resolve_entry_field).
            short_guid = _unique_prefix(
                transaction.guid, (t.guid for t in book.transactions)
            )
            return {
                "guid": short_guid,
                "date": transaction.post_date.isoformat(),
                "description": transaction.description,
                "status": "updated",
            }

    def replace_splits(
        self,
        guid: str,
        splits: list[dict],
        force: bool = False,
    ) -> dict:
        """Replace all splits in a transaction with a new set.

        Replace all splits in a transaction with a completely new set.
        The transaction's currency, description, date, and notes are preserved.
        New splits must balance to zero.

        Args:
            guid: Transaction GUID.
            splits: Complete new set of splits. Each split needs:
                - 'account' (required): Full account path
                - 'amount' (required): Value in transaction currency
                - 'quantity' (optional): Amount in account's commodity.
                  Required if account commodity differs from transaction currency.
                - 'memo' (optional): Split memo
            force: Required if existing splits are reconciled ('y') or
                   assigned to lots.

        Returns:
            Dict with updated transaction details, previous splits for audit
            trail, status, and any warnings.

        Raises:
            ValueError: If transaction not found, splits don't balance,
                       account not found, placeholder account used,
                       cross-currency split missing quantity, or has
                       reconciled/lot splits without force.
        """
        # Validate split count upfront
        if len(splits) < 2:
            raise ValueError("At least 2 splits required")

        # Validate balance upfront. _to_decimal guards against float input
        # slipping past the pydantic boundary (see tools/_helpers.SplitInput).
        total = sum((_to_decimal(s["amount"]) for s in splits), Decimal("0"))
        if total != Decimal("0"):
            raise ValueError(f"Splits do not balance: total is {total}")

        with self.open(readonly=False) as book:
            warnings = []

            # 1. Find transaction
            transaction = self._find_transaction(book, guid)
            if not transaction:
                raise ValueError(f"Transaction not found: {guid}")

            # 2. Capture previous splits for audit trail (before deletion).
            # Use the compact serializer — old-split GUIDs are not
            # addressable anymore, quantity/memo/reconcile_state only
            # emit when non-default. ~50 chars/split vs. ~140.
            previous_splits = [
                _split_to_compact_dict(s) for s in transaction.splits
            ]

            # Stage pre-replace state for the audit log (description/date
            # aren't changing but the REPLACE_SPLITS formatter wants them).
            self._stage_audit_before(_transaction_to_dict(transaction))

            # 3. Resolve and validate all accounts upfront
            resolved_accounts = []
            for split_data in splits:
                account_name = split_data["account"]
                account = self._resolve_account(book, account_name)
                if not account:
                    raise ValueError(f"Account not found: {account_name}")
                if account.placeholder:
                    raise ValueError(
                        f"Cannot use placeholder account: {account_name}"
                    )
                resolved_accounts.append((account, split_data))

            # 4. Check reconciled splits
            reconciled = [
                s for s in transaction.splits if s.reconcile_state == "y"
            ]
            if reconciled and not force:
                names = ", ".join(s.account.fullname for s in reconciled)
                raise ValueError(
                    f"Transaction has reconciled splits in: {names}. "
                    f"Use force=true to override."
                )
            if reconciled:
                names = ", ".join(s.account.fullname for s in reconciled)
                warnings.append(f"Replaced reconciled splits in: {names}")

            # 5. Check lot assignments
            in_lots = [s for s in transaction.splits if s.lot is not None]
            if in_lots and not force:
                names = ", ".join(s.account.fullname for s in in_lots)
                raise ValueError(
                    f"Transaction has splits in lots: {names}. "
                    f"Use force=true to override."
                )
            if in_lots:
                lot_info = ", ".join(
                    f"{s.lot.title} ({s.account.fullname})" for s in in_lots
                )
                warnings.append(
                    f"Removed splits from lots: {lot_info}. "
                    f"Cost basis tracking affected."
                )

            # 6. Delete existing splits
            for split in list(transaction.splits):
                book.delete(split)

            # 7. Create new splits
            trans_currency = transaction.currency
            for account, split_data in resolved_accounts:
                amount = _to_decimal(split_data["amount"])

                # Determine quantity
                if account.commodity == trans_currency:
                    quantity = amount
                elif "quantity" in split_data:
                    quantity = _to_decimal(split_data["quantity"])
                    if quantity * amount < 0:
                        raise ValueError(
                            f"Split for '{account.fullname}': quantity and "
                            f"value must have same sign "
                            f"(got value={amount}, quantity={quantity})"
                        )
                else:
                    raise ValueError(
                        f"Split for '{account.fullname}' requires 'quantity' "
                        f"because account commodity "
                        f"({account.commodity.mnemonic}) differs from "
                        f"transaction currency ({trans_currency.mnemonic})"
                    )

                piecash.Split(
                    account=account,
                    value=amount,
                    quantity=quantity,
                    memo=split_data.get("memo", ""),
                    transaction=transaction,
                )

            # 8. Save
            book.save()

            # 8a. Verify the new splits landed — same invariant as
            # update_transaction, just with the splits-only path.
            self._verify_transaction_state(
                book, transaction, expected_splits=splits,
            )

            # 9. Build thin response.
            # - `splits` echo dropped (LLM just submitted them).
            # - description/date/currency/notes don't change on a splits
            #   replace, so no reason to re-send them.
            # - previous_splits is the one piece the LLM doesn't already
            #   have — kept so callers can diff / undo / confirm.
            # - Audit log falls back to params for the "after" splits
            #   (see logging_config._resolve_entry_field).
            short_guid = _unique_prefix(
                transaction.guid, (t.guid for t in book.transactions)
            )
            result = {
                "guid": short_guid,
                "status": "splits_replaced",
                "previous_splits": previous_splits,
            }
            if warnings:
                result["warnings"] = warnings

            return result

    # Reconciliation / reporting / budgets / scheduling / lots methods moved to their mixins.
