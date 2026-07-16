"""CoreMixin — the foundation layer: accounts, transactions, book summary.

Every other module presupposes the methods here; `--modules` always
adds 'core' to the enabled set.

Holds get_book_summary, account CRUD + balance, transaction CRUD +
list/get/search/replace_splits, and the duplicate-detection /
auto-fill / split-consistency pipeline behind create_transaction.

Depends on shared helpers from BaseGnuCashBook (via MRO).
SchedulingMixin.create_transaction_from_scheduled calls
self.create_transaction (defined here), resolved via MRO — the one
extracted-to-core dependency in the whole tree.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import piecash

from gnucash_mcp.logging_config import DEBUG_LOGGER_NAME
from gnucash_mcp._format import _paginate

_debug_logger = logging.getLogger(DEBUG_LOGGER_NAME)

from gnucash_mcp.book._base import (
    _account_to_compact_line,
    _account_to_dict,
    _guid_prefix_map,
    _is_market_price,
    _is_unreconciled,
    _is_voided,
    _slot_value_str,
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

    One pass matters: per-signal helpers would each open the book and
    scan the full transaction list (~4 × (open + O(N)) per create).
    Signals are opt-in via ``want_*`` flags so the collector does only
    the work each call needs.

    Attributes:
        auto_fill: ``(splits_list, source_info)`` for the most recent
            description match, or None.
        stability_warnings: Zero or one warnings — populated when
            recent matching-description transactions disagree on the
            categorization pattern.
        duplicates: HIGH/MEDIUM candidates, HIGH first; LOW
            (single-signal) matches are suppressed as noise.
        recent_matches: Up to five recent matching transactions for
            the post-write split-consistency warning. Live ORM
            instances — read them while the session is open.
    """

    auto_fill: tuple[list[dict], dict] | None = None
    stability_warnings: list[dict] = field(default_factory=list)
    duplicates: list[dict] = field(default_factory=list)
    recent_matches: list = field(default_factory=list)

    @property
    def has_high_duplicate(self) -> bool:
        """True iff at least one candidate has confidence ``HIGH``."""
        return any(d["confidence"] == "HIGH" for d in self.duplicates)


@dataclass
class _SummaryData:
    """Categorized account-balance + roll-up data for
    ``get_book_summary``, populated in one walk over ``book.accounts``
    by ``_collect_summary_balance_sheet``.

    Totals are pre-rounded to 2 dp; per-leaf balances stay at native
    precision so renderers can re-round. Internal — no caller outside
    get_book_summary and its renderers should depend on the shape.
    """

    # Account categorization (per-leaf rows the section renderers iterate).
    asset_leaves: list[tuple[str, Decimal, str | None]] = field(default_factory=list)
    credit_cards: list[tuple[str, Decimal]] = field(default_factory=list)
    other_liab_accts: list[tuple[str, Decimal]] = field(default_factory=list)
    receivable_accts: list[tuple[str, Decimal]] = field(default_factory=list)
    payable_accts: list[tuple[str, Decimal]] = field(default_factory=list)

    # Totals (pre-rounded to 2dp).
    assets_total: Decimal = Decimal("0")
    liabilities_total: Decimal = Decimal("0")
    receivables_total: Decimal = Decimal("0")
    payables_total: Decimal = Decimal("0")
    credit_total: Decimal = Decimal("0")
    other_liab_total: Decimal = Decimal("0")

    # Counts.
    total_accounts: int = 0
    income_active: int = 0
    income_total: int = 0
    expense_active: int = 0
    expense_total: int = 0


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

    def _business_summary_counts(self, book) -> dict:
        """Action-signal counts for the get_book_summary business
        lines: open/overdue invoices and bills, active jobs. Returns
        zeros when BusinessMixin isn't loaded.

        The summary's principle: tell the LLM what needs attention,
        not what exists — these counts are actionable; account
        structure is shown elsewhere.
        """
        out = {
            "open_invoices": 0,
            "overdue_invoices": 0,
            "open_bills": 0,
            "overdue_bills": 0,
            "active_jobs": 0,
        }
        calc_lot_balance = getattr(self, "_calculate_lot_balance", None)
        if calc_lot_balance is None:
            return out
        try:
            from piecash.business.invoice import Invoice, Job
        except ImportError:
            return out

        today = date.today()
        resolve_due = getattr(self, "_resolve_invoice_due_date", None)

        # Open = posted with non-zero lot balance, so partial
        # payments and credit notes adjust the counts correctly.
        #
        # Pre-index accounts and lots once — per-invoice SQL lookups
        # plus linear lot scans are an N+1 pattern on a surface that
        # runs on every dashboard call.
        accounts_by_guid = {
            acct.guid: acct for acct in book.accounts
        }
        lots_by_guid: dict[str, object] = {}
        for acct in book.accounts:
            for lot in acct.lots:
                lots_by_guid[lot.guid] = lot

        for inv in book.session.query(Invoice).filter(
            Invoice.date_posted.isnot(None),
        ).all():
            try:
                post_acct = accounts_by_guid.get(inv.post_acc_guid)
                if post_acct is None:
                    continue
                lot_obj = lots_by_guid.get(inv.post_lot_guid)
                if lot_obj is None:
                    continue
                balance = calc_lot_balance(lot_obj)
                if balance == 0:
                    continue
            except Exception:
                # Swallow ORM hiccups so summary signals survive
                # partial corruption; --debug captures the cause.
                _debug_logger.debug(
                    "summary signals: invoice eval failed; skipping",
                    exc_info=True,
                )
                continue

            # Credit notes stay in the OPEN counts but never age
            # into overdue — they're money the business OWES.
            # Matches get_outstanding_invoices.
            is_credit_note = False
            get_is_cn = getattr(self, "_get_is_credit_note", None)
            if get_is_cn is not None:
                try:
                    is_credit_note = bool(get_is_cn(inv))
                except Exception:
                    _debug_logger.debug(
                        "summary signals: credit-note check failed",
                        exc_info=True,
                    )

            is_overdue = False
            if resolve_due is not None and not is_credit_note:
                try:
                    due_date, _ = resolve_due(book, inv)
                    if due_date is not None and due_date < today:
                        is_overdue = True
                except Exception:
                    # Due-date resolution can fail on
                    # corrupt term records; surface in debug log.
                    _debug_logger.debug(
                        "summary signals: due date resolve failed",
                        exc_info=True,
                    )

            if inv.owner_type == 4:  # vendor bill
                out["open_bills"] += 1
                if is_overdue:
                    out["overdue_bills"] += 1
            else:
                # owner_type 2/3/5 render as receivables unless the
                # post account is PAYABLE (vouchers: company owes
                # employees → folded into open_bills).
                if post_acct.type == "PAYABLE":
                    out["open_bills"] += 1
                    if is_overdue:
                        out["overdue_bills"] += 1
                else:
                    out["open_invoices"] += 1
                    if is_overdue:
                        out["overdue_invoices"] += 1

        try:
            out["active_jobs"] = book.session.query(Job).filter(
                Job.active == 1,
            ).count()
        except Exception:
            # The jobs table may not exist on very old books;
            # log and continue.
            _debug_logger.debug(
                "summary signals: active jobs query failed",
                exc_info=True,
            )

        return out

    def _account_reconciliation_status(
        self, book: piecash.Book, accounts: list,
    ) -> list[dict]:
        """Per-account reconciliation freshness for the book summary.

        For each reconcilable account with activity, returns::

            {
              "account": fullname,
              "status": "through YYYY-MM-DD" | "never reconciled",
              "days_behind": int | None,   # None iff never reconciled
              "unreconciled_count": int,
            }

        (A single "N unreconciled" int would include splits that
        can't conceptually be reconciled — large and unactionable.)

        Filtering: BANK/CREDIT/LIABILITY always; ASSET only with
        'y'/'c' history (brokerage cash, escrow — not investment
        positions); placeholder / template / ROOT / no-activity
        accounts excluded. Sorted by fullname; empty list → caller
        omits the section.
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

            # Single pass over splits derives latest_y_date, has_yc
            # (the ASSET gate), any_splits, unreconciled_count, and
            # oldest_unreconciled_date. ``_is_unreconciled`` is the
            # chokepoint shared with get_unreconciled_splits so the
            # dashboard count and the detail tool agree by
            # construction (this surface is as-of-today only; the
            # tool's as_of_date variant is documented at the helper).
            latest_y_date = None
            has_yc = False
            any_splits = False
            unreconciled_count = 0
            oldest_unreconciled_date = None
            for s in account.splits:
                # Voided splits are zombies, not reconcilable
                # activity — they must not make an account
                # surface in the reconciliation section.
                if _is_voided(s):
                    continue
                any_splits = True
                rstate = s.reconcile_state
                if rstate in ("y", "c"):
                    has_yc = True
                if rstate == "y":
                    pd = s.transaction.post_date
                    if pd is not None and (
                        latest_y_date is None or pd > latest_y_date
                    ):
                        latest_y_date = pd
                if _is_unreconciled(s):
                    unreconciled_count += 1
                    pd = s.transaction.post_date
                    # Null post_date (an old-book artifact) still
                    # counts as backlog; it just can't anchor the
                    # oldest-date lag display.
                    if pd is not None and (
                        oldest_unreconciled_date is None
                        or pd < oldest_unreconciled_date
                    ):
                        oldest_unreconciled_date = pd

            # ASSET passes only with reconcilable history (see
            # docstring).
            if account.type == "ASSET" and not has_yc:
                continue

            if not any_splits:
                # No activity at all — not "behind," just unused.
                continue

            if latest_y_date is None:
                results.append({
                    "account": account.fullname,
                    "status": "never reconciled",
                    "days_behind": None,
                    "unreconciled_count": unreconciled_count,
                })
            else:
                # Lag anchors to the OLDEST unreconciled split when
                # there's pending work — the honest scope-of-work
                # signal ("6 years behind", not "4 months since the
                # last reconcile"). Fully-caught-up accounts fall
                # back to latest_y_date staleness.
                if unreconciled_count > 0 \
                        and oldest_unreconciled_date is not None:
                    days_behind = (today - oldest_unreconciled_date).days
                    results.append({
                        "account": account.fullname,
                        "status": f"through {latest_y_date.isoformat()}",
                        "days_behind": days_behind,
                        "unreconciled_count": unreconciled_count,
                        "latest_y_date": latest_y_date.isoformat(),
                        "oldest_unreconciled_date":
                            oldest_unreconciled_date.isoformat(),
                    })
                else:
                    days_behind = (today - latest_y_date).days
                    results.append({
                        "account": account.fullname,
                        "status": f"through {latest_y_date.isoformat()}",
                        "days_behind": days_behind,
                        "unreconciled_count": unreconciled_count,
                        "latest_y_date": latest_y_date.isoformat(),
                    })

        results.sort(key=lambda r: r["account"])
        return results

    # Asset / liability type sets for the net-worth trajectory.
    # RECEIVABLE and PAYABLE are included despite having dedicated
    # dashboard sections — A/R is an asset, A/P a liability, and
    # these buckets must track balance_sheet's or the headline net
    # worth drifts from the canonical balance-sheet identity.
    _NW_ASSET_TYPES = frozenset({"ASSET", "BANK", "CASH", "STOCK", "MUTUAL", "RECEIVABLE"})
    _NW_LIABILITY_TYPES = frozenset({"LIABILITY", "CREDIT", "PAYABLE"})

    def _compute_net_worth_at(
        self,
        book: piecash.Book,
        as_of: date,
        default_currency: piecash.Commodity,
        accounts: list,
    ) -> Decimal:
        """Net worth in book-default currency as of ``as_of``.

        Single source of truth for the summary's net-worth number
        (the trajectory's "now" anchor).

        - **Own-splits-per-account** — no roll-up; a parent's direct
          splits are real money no other row represents. Shared rule
          with ``balance_sheet`` / ``net_worth`` in reporting.py.
        - Skips ROOT and template-subtree accounts only.
        - Asset types convert at the most-recent-rate-on-or-before
          ``as_of`` with cost-basis fallback; liability types convert
          the same way, then negate to a positive magnitude —
          mirroring balance_sheet so foreign-currency debt agrees
          across surfaces (``_market_value`` handles both).
        - RECEIVABLE/PAYABLE are INCLUDED — balance_sheet and
          net_worth count them, so this anchor must too. Their
          dedicated summary sections are presentation, not scope.
        """
        template_guids = self._template_account_guids(book)
        # ``_rates_as_of`` folds now-or-future anchors to date.max
        # via ``_anchor_for_as_of`` — intentional future-dated price
        # forecasts are included in "now" valuations, so this anchor
        # agrees with balance_sheet by construction. Past anchors
        # stay literal for historical reconstruction.
        rates = self._rates_as_of(book, as_of, default_currency)

        assets_total = Decimal("0")
        liabilities_total = Decimal("0")

        for account in accounts:
            if account.type == "ROOT":
                continue
            if account.guid in template_guids:
                continue

            if account.type not in self._NW_ASSET_TYPES \
                    and account.type not in self._NW_LIABILITY_TYPES:
                continue

            balance = self._own_splits_balance(account, as_of=as_of)

            if balance == 0:
                continue

            # Value the signed balance via the same rate map the
            # report tools use (_market_value handles the rate-or-
            # cost-basis fallback), then sort into assets /
            # liabilities; liabilities negate to a positive magnitude.
            converted, _ = self._market_value(
                account, balance,
                book=book,
                rates=rates,
                default_currency=default_currency,
                today=as_of,
            )
            if account.type in self._NW_ASSET_TYPES:
                assets_total += converted
            else:
                liabilities_total += -converted

        return assets_total - liabilities_total

    def _net_worth_trajectory(
        self,
        book: piecash.Book,
        first_date: date | None,
        accounts: list,
    ) -> list[dict]:
        """Five-point net-worth trajectory: 12mo / 6mo / 3mo / 1mo
        ago and now — enough points to show acceleration and recent
        trend breaks at ~30 tokens.

        Anchors before the book's first transaction are dropped
        (emitting "0" would falsely suggest zero net worth before
        the book existed); anchors within the data range that
        predate activity are kept (a flat span is the right answer).

        Returns ``{label, net_worth}`` dicts oldest-first; empty
        list when the book has no transactions → section omitted.
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

    # Budget overspend threshold: variance over +10% (used% ahead of
    # elapsed%) earns ⚠ — breathing room for lumpy household spending.
    _BUDGET_WARN_VARIANCE_PCT = 10

    # Runway ⚠ threshold: under ~two months, a household should be
    # actively concerned about cash position, not just tracking it.
    _RUNWAY_WARN_DAYS = 60

    # Burn-rate averaging window. 180 days smooths billing cycles
    # and seasonality without diluting recent changes; the iteration
    # is gated to splits within the window.
    _RUNWAY_BURN_DAYS = 180

    # Liquid account types for runway: cash and near-cash only.
    # Brokerage positions (STOCK/MUTUAL) are sellable in a day.
    # ASSET-typed accounts are deliberately excluded even in default
    # currency: users code real estate and vehicles as ASSET, and
    # counting them turns a four-month runway into a fictional
    # two-year one. A genuinely cash-equivalent ASSET (HSA, prepaid)
    # can be recategorized as BANK to count; the structural type is
    # the source of truth.
    _RUNWAY_LIQUID_TYPES = frozenset({"BANK", "CASH", "STOCK", "MUTUAL"})

    # Bare slot key per the slot-naming convention (a universal
    # financial concept another tool could plausibly converge on).
    # Set via set_account_slot on the account or any ancestor:
    # "1"/"true"/"yes" marks the subtree as retirement (excluded from
    # runway/low-cash liquidity), "0"/"false"/"no" un-marks it —
    # nearest ancestor wins, so one flag on the "Retirement"
    # placeholder covers every account under it, and a child can
    # opt back in.
    _RETIREMENT_SLOT_KEY = "is_retirement"

    _RETIREMENT_SLOT_TRUE = frozenset({"1", "true", "yes", "y"})
    _RETIREMENT_SLOT_FALSE = frozenset({"0", "false", "no", "n"})

    def _is_in_retirement_subtree(self, account) -> bool:
        """True when the account is penalty-locked retirement money
        that must not count as liquid for runway / low-cash.

        Authoritative signal: the ``is_retirement`` slot on the
        account or its nearest flagged ancestor (see the key comment
        above) — locale- and naming-proof. Fallback when no slot is
        set anywhere on the path: any fullname component containing
        the English word "retirement" (case-insensitive). The
        fallback is English-only by construction — a zh_CN
        ``资产:投资:退休金`` or German ``Altersvorsorge`` is invisible
        to it, which overstates runway on localized books; set the
        slot there. A subtree named "Tax-advantaged" has the same
        gap in any locale.
        """
        from gnucash_mcp.book._base import _slot_value_str

        node = account
        while node is not None and node.type != "ROOT":
            try:
                raw = node[self._RETIREMENT_SLOT_KEY]
            except KeyError:
                raw = None
            if raw is not None:
                val = (_slot_value_str(raw) or "").strip().lower()
                if val in self._RETIREMENT_SLOT_TRUE:
                    return True
                if val in self._RETIREMENT_SLOT_FALSE:
                    return False
                # Unrecognized value: keep walking rather than guess.
            node = node.parent
        return any(
            "retirement" in part.lower()
            for part in account.fullname.split(":")
        )

    # Stale-price threshold for the Warnings section — past a month,
    # quotes are likely skewing net-worth and runway numbers.
    _STALE_PRICE_DAYS = 30

    def _collect_warnings(
        self,
        book: piecash.Book,
        transactions: list,
        accounts: list,
    ) -> list[str]:
        """Collect warnings for the consolidated Warnings section.

        Returns formatted warning strings ordered by category::

            data integrity → backup health → critically low cash →
            overdue invoices/bills → overdue scheduled → stale prices

        Within each category, most-severe first. Operational urgency
        outranks data-quality cleanup, except integrity defects
        (imbalance/orphan) lead because they call every other number
        into question. Reconciliation-behind warnings are NOT
        duplicated here — the Reconciliation section already carries
        that signal with more detail.

        Each per-category collector swallows its own exceptions — a
        failed check in one category never breaks the rest. Category
        specifics are commented at each collector below.
        """
        today = date.today()
        default_currency = self._require_default_currency(book)

        # ── 1. Data integrity: Imbalance / Orphan accounts ──
        # GnuCash auto-creates these; a non-zero balance is a real
        # structural defect. Each account displays in its own
        # currency (the defect's unit), but the cross-account sort
        # key converts to default currency — raw quantities would
        # let a 5 USD defect sort above a 200 CNY one.
        rates_for_sort = self._rates_as_of(
            book, today, default_currency,
        )
        root = book.root_account
        integrity: list[tuple[Decimal, str]] = []
        for account in accounts:
            # Locale-robust: GnuCash localizes the "Imbalance"/"Orphan"
            # leading word, so a structural+catalog match replaces the
            # old English-only prefix check (which never fired on a
            # localized book — the warning silently went dark).
            if not self._is_auto_balancing_account(account, root):
                continue
            name = account.name
            balance = self._own_splits_balance(account)
            if balance != 0:
                acct_commodity = (
                    account.commodity if account.commodity
                    else default_currency
                )
                if acct_commodity.guid == default_currency.guid:
                    sort_magnitude = abs(balance)
                else:
                    rate = rates_for_sort.get(acct_commodity.guid)
                    if rate is None:
                        # No FX on file — surfacing the defect with
                        # an imperfect sort order beats hiding it.
                        sort_magnitude = abs(balance)
                    else:
                        sort_magnitude = abs(balance * rate)
                integrity.append((
                    sort_magnitude,
                    # An auto-balancing (Imbalance/Orphan) account with a
                    # non-zero balance is ambiguous: GnuCash may have parked
                    # an unbalanced remainder, or the user may be holding an
                    # unclassified item on purpose. Flag it for attention
                    # without implying the book is corrupted.
                    f"{name}: {balance} "
                    f"(uncleared suspense balance — review or reclassify)",
                ))
        integrity.sort(key=lambda pair: pair[0], reverse=True)
        integrity = [msg for _, msg in integrity]

        # ── 2. Critically low cash ──
        # Threshold = 1 day of daily burn — scales with actual
        # spending instead of a fixed dollar floor. Skipped when the
        # book has no expense activity.
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
                    if self._is_auto_balancing_account(
                        account, book.root_account
                    ):
                        # A suspense/Imbalance balance isn't spendable
                        # cash — it's surfaced by the integrity section
                        # above. Counting it here fires a bogus
                        # "critically low cash" on a few euros parked
                        # for clarification.
                        continue
                    if account.placeholder:
                        continue
                    if account.guid in template_guids:
                        continue
                    if self._is_in_retirement_subtree(account):
                        continue

                    # "Now" warning: cap at today so a future-
                    # dated deposit can't suppress a real low-cash
                    # alarm (or a future payment fire a premature one).
                    balance_qty = self._own_splits_balance(
                        account, as_of=today,
                    )
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
                # Lowest balance first — most urgent.
                low_cash_entries.sort(key=lambda e: e[0])
                low_cash = [msg for _, msg in low_cash_entries]
        except Exception:
            pass

        # ── 3. Overdue invoices and bills ──
        # Posted, non-zero lot balance, due date past. Requires
        # BusinessMixin's _calculate_lot_balance; gracefully skipped
        # otherwise.
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
                get_is_cn = getattr(
                    self, "_get_is_credit_note", None,
                )
                for inv in book.session.query(Invoice).filter(
                    Invoice.date_posted.isnot(None)
                ).all():
                    try:
                        # Credit notes never age into past-due —
                        # their balance is money the business OWES.
                        # get_outstanding_invoices exempts them too;
                        # the two surfaces must agree.
                        if get_is_cn is not None and get_is_cn(inv):
                            continue

                        # _resolve_invoice_due_date keeps this and
                        # get_outstanding_invoices on identical math;
                        # no_terms flags the 30-day-default branch.
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
                        # With no term set, anchor the count to the
                        # assumption ("past 30-day default") rather
                        # than "overdue", which reads as contractual
                        # and contradicts "(no term set)".
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
            # Template accounts would mark GnuCash's ``template``
            # pseudo-commodity in-use and misfire a permanent
            # "no price on file" warning on desktop-created books.
            template_guids = self._template_account_guids(book)
            in_use: set = set()
            for a in accounts:
                if a.type != "ROOT" and a.guid not in template_guids:
                    in_use.add(a.commodity.guid)

            # One pass over book.prices builds both signals: in-use
            # commodities and latest market-price date. ``in_use.add``
            # runs AFTER the ``_is_market_price`` filter — marking
            # first would tag commodities that only have piecash
            # auto-placeholder prices as in-use and misfire the
            # "no price on file" warning.
            cutoff = today - timedelta(days=self._STALE_PRICE_DAYS)
            by_commodity_latest: dict[str, date] = {}
            for p in book.prices:
                if not _is_market_price(p):
                    continue
                in_use.add(p.commodity.guid)
                p_date = p.date
                if hasattr(p_date, "date") and callable(p_date.date):
                    p_date = p_date.date()
                cguid = p.commodity.guid
                if (
                    cguid not in by_commodity_latest
                    or p_date > by_commodity_latest[cguid]
                ):
                    by_commodity_latest[cguid] = p_date

            # (sort_key, message) — no-price entries sort to the
            # top as most stale.
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
        # Auto-backup chain breaks render next to integrity issues:
        # data loss is the one unrecoverable failure, and the debug
        # log is not a surface anyone reviews routinely.
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
                # No backup file in 30+ days: stale chain even when
                # the last attempt reports fine.
                newest_age = health.get("newest_backup_age_days")
                if newest_age is not None and newest_age >= 30:
                    backup_health.append(
                        f"No backup in {newest_age} days (most recent "
                        f"snapshot is older than 1 month)"
                    )
            except Exception:
                pass

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

        Picks the budget whose period range includes today; ties go
        to the latest start date. None (→ section omitted) when no
        budget covers today.

        Returns ``{name, used_pct, elapsed_pct, variance_pct}``,
        percentages quantized to whole numbers:

        - ``used_pct`` = actuals in budgeted accounts ÷ targets × 100
        - ``elapsed_pct`` = (today − start + 1) ÷ period length × 100
        - ``variance_pct`` = used − elapsed (positive = ahead of pace)

        Actuals come from EXPENSE/INCOME splits in budgeted accounts
        AND their descendants — children roll up to a budgeted
        ancestor, but a separately-budgeted descendant stays on its
        own line so its actuals aren't double-counted (matches
        ``get_budget_report``).
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

        # Targets FX-convert at the period-end rate — raw sums would
        # be apples-to-oranges against default-currency actuals
        # (mirrors get_budget_report).
        factors = self._account_conversion_factors(book, period_end)
        total_budgeted = Decimal("0")
        budgeted_accounts: list = []
        budgeted_account_guids: set[str] = set()
        for ba in budget.amounts:
            ba_amount = Decimal(str(ba.amount))
            factor = factors.get(ba.account.guid)
            if factor is not None:
                ba_amount = ba_amount * factor
            total_budgeted += ba_amount
            budgeted_accounts.append(ba.account)
            budgeted_account_guids.add(ba.account.guid)

        if total_budgeted <= 0:
            return None

        # Roll descendants of budgeted parents into the actuals set;
        # separately-budgeted descendants stay out (see docstring).
        rollup_guids: set[str] = set(budgeted_account_guids)
        for budgeted_acct in budgeted_accounts:
            descendants: set = set()
            self._collect_descendants(budgeted_acct, descendants)
            for desc in descendants:
                if desc.guid in budgeted_account_guids:
                    continue  # separately budgeted — don't roll up
                rollup_guids.add(desc.guid)

        # Single pass over the period's transactions. Each split
        # converts to the book default at period-end-anchored rates
        # — raw quantities from a foreign-currency budgeted account
        # would wildly miscalibrate used_pct, and a historical
        # period must value at its own rates, not today's.
        actuals = Decimal("0")
        for txn in transactions:
            if txn.post_date < period_start or txn.post_date > period_end:
                continue
            for s in txn.splits:
                if s.account.guid not in rollup_guids:
                    continue
                atype = s.account.type
                if atype not in ("EXPENSE", "INCOME"):
                    continue
                amt = self._split_in_default_currency(
                    s, s.account, factors.get(s.account.guid),
                )
                # SIGNED accumulation so contra splits (expense
                # refunds, income clawbacks) net into the headline —
                # same convention as get_budget_report. INCOME is
                # stored negative; flip so revenue counts positive.
                if atype == "EXPENSE":
                    actuals += amt
                elif atype == "INCOME":
                    actuals += -amt

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

        Shared between runway (divisor) and the critically-low-cash
        warning (threshold) so the two agree by construction.

        ``transactions`` is the list get_book_summary materializes
        once and threads through. Returns ``Decimal("0")`` when the
        window has no expense activity. Each split converts to the
        book default currency — raw ``split.value`` would mix
        currencies on books with foreign-currency expenses.
        """
        if days is None:
            days = self._RUNWAY_BURN_DAYS
        today = date.today()
        # Book-age clamp: the window is a MAX, not a fixed
        # denominator — dividing by 180 on a 19-day-old book
        # overstates runway ~10×. The 1-day floor avoids
        # divide-by-zero.
        dated = [
            t.post_date for t in transactions
            if t.post_date is not None  # old-book artifact
        ]
        if dated:
            first_txn_date = min(dated)
            book_age_days = max(1, (today - first_txn_date).days)
            days = min(days, book_age_days)
        window_start = today - timedelta(days=days)
        # "Now" burn signal — anchor factors to today.
        factors = self._account_conversion_factors(book, today)
        expenses = Decimal("0")
        for txn in transactions:
            if txn.post_date is None:  # old-book artifact
                continue
            if txn.post_date < window_start or txn.post_date > today:
                continue
            for s in txn.splits:
                if s.account.type == "EXPENSE":
                    expenses += self._split_in_default_currency(
                        s, s.account, factors.get(s.account.guid),
                    )
        return expenses / Decimal(days)

    def _runway_metrics(
        self,
        book: piecash.Book,
        default_currency: piecash.Commodity,
        transactions: list,
        accounts: list,
    ) -> dict | None:
        """Compute runway: days the household survives on liquid
        assets at current burn rate if income stopped today.

        **Liquid** = balances in ``_RUNWAY_LIQUID_TYPES`` (see that
        constant for the ASSET exclusion), minus anything in a
        Retirement subtree (``_is_in_retirement_subtree`` —
        penalty-locked money isn't runway). Positions value at
        shares × latest price with cost-basis fallback, same as
        net worth.

        **Daily burn** = ``_daily_expense_burn`` over
        ``_RUNWAY_BURN_DAYS`` (book-age clamped).

        Special cases: no expense activity → None (section
        omitted); negative liquid (overdrafts exceed cash) → flag
        dict the caller renders as "0 days ⚠"; otherwise
        ``{runway_days, liquid, daily_burn}``.
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
            if self._is_auto_balancing_account(account, book.root_account):
                # Suspense/Imbalance balances aren't runway liquidity —
                # they're unresolved bookkeeping, not money to live on.
                continue
            if self._is_in_retirement_subtree(account):
                # Penalty-locked money isn't runway — see the helper.
                continue

            # Cap at today — a rent payment dated +10 days must not
            # move runway while net_worth correctly ignores it.
            balance = self._own_splits_balance(account, as_of=today)
            if balance == 0:
                continue

            if account.commodity == default_currency:
                liquid += balance
            else:
                rate = rates.get(account.commodity.guid)
                if rate is not None:
                    liquid += balance * rate
                else:
                    # Cost-basis fallback, same as net worth. The
                    # post_date <= today filter keeps future-dated
                    # entries from inflating liquid.
                    cost_basis = Decimal("0")
                    for split in account.splits:
                        post_date = split.transaction.post_date
                        if hasattr(post_date, "date") and callable(post_date.date):
                            post_date = post_date.date()
                        if post_date > today:
                            continue
                        cost_basis += Decimal(str(split.value))
                    liquid += cost_basis

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
        months, most recent first::

            [{"label": "Apr 2026", "net": Decimal("1247"), "is_mtd": True}, ...]

        Net = INCOME credits − EXPENSE debits; INCOME splits are
        stored negative and sign-flipped. ``is_mtd`` is True only
        for the current (partial) month. Empty list when the window
        has no activity → caller omits the section.

        Each split converts to the book default at the most recent
        market rate — raw ``split.value`` sums mix currencies.
        """
        today = date.today()
        # One factors map applied uniformly — this summary surface
        # deliberately uses today's rates for every month (per-month
        # rates would need net_worth's per-boundary restructure).
        factors = self._account_conversion_factors(book, today)

        # Calendar-month windows, oldest → newest. Plain (year,
        # month) arithmetic keeps core free of dateutil.
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

        # Single pass; bucket index = months-from-window-start.
        for txn in transactions:
            d = txn.post_date
            if d is None:  # old-book artifact
                continue
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
                if atype not in ("INCOME", "EXPENSE"):
                    continue
                amt = self._split_in_default_currency(
                    s, s.account, factors.get(s.account.guid),
                )
                if atype == "INCOME":
                    # INCOME stored negative (credit-natural); flip
                    # to positive contribution to monthly net.
                    nets[idx] += -amt
                else:  # EXPENSE
                    nets[idx] -= amt
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
    def _format_reconciliation_lag(
        days_behind: int, with_parens: bool = True,
    ) -> str:
        """Render a "(N days/months/years behind)" suffix for a
        reconciliation lag.

        Days below 60 (precision near the 45-day warning threshold);
        months to 24 months, using the 30.44-day average so values
        round to the nearest unit rather than floor; years past that
        ("6 years behind" is the planning number; "72 months" reads
        as a typo). ``with_parens=False`` returns the bare phrase
        for callers composing larger strings.
        """
        if days_behind >= 730:  # 24 months in days
            years = round(days_behind / 365.25)
            inner = f"{years} year{'s' if years != 1 else ''} behind"
        elif days_behind >= 60:
            months = round(days_behind / 30.44)
            inner = f"{months} months behind"
        else:
            inner = f"{days_behind} days behind"
        return f"({inner})" if with_parens else inner

    # ── Section renderers ─────────────────────────────────────────────
    #
    # Each ``_render_*`` helper consumes its paired collector's data
    # and returns ``list[str]`` to append to the summary (``[]``
    # omits the section — absence-as-signal). Self-contained, no
    # cross-section state: adding or reordering a section is a
    # one-method change.

    def _render_reconciliation(
        self, reconciliation: list[dict],
    ) -> list[str]:
        """Render the Reconciliation section.

        Three buckets: STALE (> ``_RECONCILE_WARN_DAYS`` behind) —
        rendered individually, the per-account payload can't
        aggregate; CURRENT — collapsed to "<N> accounts current";
        NEVER RECONCILED — collapsed with ⚠. Zero-count collapse
        lines are omitted; empty input omits the section.
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
            # Sub-line: "47 splits unreconciled (6 years behind,
            # oldest: 2020-03-15) ⚠". The split count is the scope
            # of work; the lag anchors to the OLDEST unreconciled
            # split so "behind" measures true scope — a bookkeeper
            # planning around "4 months behind" expects one sitting,
            # not six years of statements.
            n = entry["unreconciled_count"]
            if n > 0 and "oldest_unreconciled_date" in entry:
                plural = "s" if n != 1 else ""
                oldest = entry["oldest_unreconciled_date"]
                lag_inner = self._format_reconciliation_lag(
                    entry["days_behind"], with_parens=False,
                )
                out.append(
                    f"  {leaf}: {n} split{plural} unreconciled "
                    f"({lag_inner}, oldest: {oldest}) ⚠"
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
            # Always shows explicit sign + thousands separator;
            # whole dollars (cents would noise up the summary).
            out.append(f"  {label}: {int(entry['net']):+,}")
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

    # ── Summary collector / section renderers ────────────────────
    #
    # Collector returns the data; renderer turns it into list[str]
    # (or [] to omit). New sections: one method + one lines.extend.

    def _collect_summary_balance_sheet(
        self,
        accounts: list,
        template_guids: set[str],
        latest_prices: dict,
        default_currency,
        today: date,
        book: piecash.Book,
        rate_via: dict[str, str] | None = None,
    ) -> _SummaryData:
        """Single-pass account walker for ``get_book_summary``.

        Walks ``accounts`` once, building the categorized lists and
        running counters the renderers need. Keeping the walk here
        (not inline in ``get_book_summary``) keeps the
        renderer signatures focused on what they consume rather
        than threading 15 collection variables through.

        Returns a ``_SummaryData`` with pre-rounded totals so
        renderers can format directly.
        """
        asset_types = {"ASSET", "BANK", "CASH", "STOCK", "MUTUAL"}
        data = _SummaryData()

        for account in accounts:
            if account.type == "ROOT":
                continue
            if account.guid in template_guids:
                continue
            data.total_accounts += 1

            has_activity = len(account.splits) > 0

            # Own-splits balance in the account's own commodity,
            # today-filtered so the snapshot agrees with trajectory's
            # "now". No roll-up — direct splits on parents are real
            # money no other row represents.
            balance = self._own_splits_balance(account, as_of=today)

            leaf = account.fullname.split(":")[-1]

            if account.type in asset_types:
                if balance != 0:
                    usd_value, note = self._market_value(
                        account, balance,
                        book=book,
                        rates=latest_prices,
                        default_currency=default_currency,
                        today=today,
                        provenance=rate_via,
                    )
                    data.asset_leaves.append((leaf, usd_value, note))
            elif account.type == "CREDIT":
                if balance != 0:
                    # Convert via the shared rate map, then negate
                    # the credit-natural balance — raw quantities
                    # diverge from balance_sheet on foreign debt.
                    usd_value, _ = self._market_value(
                        account, balance,
                        book=book,
                        rates=latest_prices,
                        default_currency=default_currency,
                        today=today,
                    )
                    data.credit_cards.append((leaf, -usd_value))
            elif account.type == "LIABILITY":
                if balance != 0:
                    usd_value, _ = self._market_value(
                        account, balance,
                        book=book,
                        rates=latest_prices,
                        default_currency=default_currency,
                        today=today,
                    )
                    pos_value = -usd_value
                    # Loans and other liabilities are the same TYPE —
                    # GnuCash has no "loan" account type — so we don't
                    # sub-classify by name. The old "loan" substring
                    # was English-only (missed "Darlehen"/"Kredit" and
                    # any rename); one bucket is locale-correct.
                    data.other_liab_accts.append((leaf, pos_value))
            elif account.type == "RECEIVABLE":
                if balance != 0:
                    # A/R is debit-natural: positive balance = owed to us.
                    usd_value, _ = self._market_value(
                        account, balance,
                        book=book,
                        rates=latest_prices,
                        default_currency=default_currency,
                        today=today,
                    )
                    data.receivable_accts.append((leaf, usd_value))
            elif account.type == "PAYABLE":
                if balance != 0:
                    # A/P is credit-natural: negate for "what we owe".
                    usd_value, _ = self._market_value(
                        account, -balance,
                        book=book,
                        rates=latest_prices,
                        default_currency=default_currency,
                        today=today,
                    )
                    data.payable_accts.append((leaf, usd_value))
            elif account.type == "INCOME":
                data.income_total += 1
                if has_activity:
                    data.income_active += 1
            elif account.type == "EXPENSE":
                data.expense_total += 1
                if has_activity:
                    data.expense_active += 1

        # Pre-round all totals once so renderers format directly.
        def _r2(v: Decimal) -> Decimal:
            return v.quantize(Decimal("0.01"))

        data.receivables_total = _r2(
            sum((b for _, b in data.receivable_accts), Decimal("0"))
        )
        data.payables_total = _r2(
            sum((b for _, b in data.payable_accts), Decimal("0"))
        )
        data.assets_total = _r2(
            sum((v for _, v, _ in data.asset_leaves), Decimal("0"))
            + data.receivables_total
        )
        data.credit_total = _r2(
            sum(b for _, b in data.credit_cards)
            if data.credit_cards else Decimal(0)
        )
        data.other_liab_total = _r2(
            sum(b for _, b in data.other_liab_accts)
            if data.other_liab_accts else Decimal(0)
        )
        data.liabilities_total = _r2(
            data.credit_total + data.other_liab_total
            + data.payables_total
        )
        return data

    def _render_book_metadata(
        self,
        currency: str,
        first_date: date | None,
        last_date: date | None,
    ) -> list[str]:
        """Render Book / Currency / Data range / Last entry header.

        ``Last entry`` carries a staleness signal —
        the answer to "let's reconcile" vs "let's enter 200
        transactions first" pivots on it. Four cases keyed on
        ``(today - last_date).days``:

        - ``< 0``  → future-dated (normal for scheduled-txn ahead-of-
          today posting). ``(future-dated, N days ahead)``.
        - ``= 0``  → today.
        - ``= 1``  → yesterday.
        - ``> 1``  → N days behind. ⚠ past ``_LAST_ENTRY_WARN_DAYS``.
        """
        from gnucash_mcp._format import _book_display_name

        lines = [
            f"Book: {_book_display_name(self.book_path)}",
            f"Currency: {currency}",
        ]
        if first_date and last_date:
            lines.append(
                f"Data range: {first_date.isoformat()} "
                f"to {last_date.isoformat()}"
            )
        if last_date is not None:
            today = date.today()
            days_behind = (today - last_date).days
            if days_behind < 0:
                lines.append(
                    f"Last entry: {last_date.isoformat()} "
                    f"(future-dated, {-days_behind} days ahead)"
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
        return lines

    @staticmethod
    def _render_assets_section(
        data: _SummaryData,
        currency: str,
    ) -> list[str]:
        """Render the Assets section: header + per-leaf lines
        sorted by USD value descending.

        Count includes A/R accounts (which roll into
        ``assets_total``) so the headline N agrees with the total;
        per-account A/R detail lives in
        ``_render_receivables_payables``.
        """
        assets_count = len(data.asset_leaves) + len(data.receivable_accts)
        lines = [
            f"Assets: {assets_count} accounts, "
            f"{currency} {data.assets_total}"
        ]
        for name, usd_value, note in sorted(
            data.asset_leaves, key=lambda x: x[1], reverse=True
        ):
            rounded = usd_value.quantize(Decimal("0.01"))
            if note is None:
                lines.append(f"  {name}: {currency} {rounded}")
            else:
                lines.append(
                    f"  {name}: {note} ({currency} {rounded})"
                )
        return lines

    @staticmethod
    def _render_liabilities_section(
        data: _SummaryData,
        currency: str,
    ) -> list[str]:
        """Render Liabilities: header + grouped subtotals + top 3.

        A/P accounts (rolled into ``liabilities_total``) are
        included in the headline count; per-account A/P detail
        lives in ``_render_receivables_payables``.
        """
        liab_count = (
            len(data.credit_cards) + len(data.other_liab_accts)
            + len(data.payable_accts)
        )
        lines = [
            f"Liabilities: {liab_count} accounts, "
            f"{currency} {data.liabilities_total}"
        ]
        if data.credit_cards:
            lines.append(
                f"  Credit cards ({len(data.credit_cards)}): "
                f"{currency} {data.credit_total}"
            )
        if data.other_liab_accts:
            lines.append(
                f"  Loans & other ({len(data.other_liab_accts)}): "
                f"{currency} {data.other_liab_total}"
            )
        all_liab_leaves = (
            data.credit_cards + data.other_liab_accts
        )
        if len(all_liab_leaves) > 1:
            all_liab_leaves.sort(key=lambda x: x[1], reverse=True)
            top_n = all_liab_leaves[:3]
            top_parts = [
                f"{n} {currency} {b.quantize(Decimal('0.01'))}"
                for n, b in top_n
            ]
            lines.append(
                f"  Top {len(top_n)}: {', '.join(top_parts)}"
            )
        return lines

    @staticmethod
    def _render_receivables_payables(
        data: _SummaryData,
        biz_counts: dict,
        currency: str,
    ) -> list[str]:
        """Render Receivables + Payables breakouts.

        Each section is conditional on the breakout having any
        accounts. The action-signal suffix (``N invoice(s),
        M overdue``) lets the LLM see "2 overdue" and ask about
        collections without us having to spell that out
        explicitly.
        """
        lines: list[str] = []
        if data.receivable_accts:
            inv_n = biz_counts["open_invoices"]
            overdue = biz_counts["overdue_invoices"]
            signal = (
                f" ({inv_n} invoice"
                f"{'s' if inv_n != 1 else ''}, "
                f"{overdue} overdue)"
            ) if inv_n else ""
            lines.append(
                f"Receivables: {len(data.receivable_accts)} "
                f"account"
                f"{'s' if len(data.receivable_accts) != 1 else ''}, "
                f"{currency} {data.receivables_total}{signal}"
            )
            for name, bal in sorted(
                data.receivable_accts, key=lambda x: x[1],
                reverse=True,
            ):
                lines.append(
                    f"  {name}: {currency} "
                    f"{bal.quantize(Decimal('0.01'))}"
                )
        if data.payable_accts:
            bill_n = biz_counts["open_bills"]
            overdue = biz_counts["overdue_bills"]
            signal = (
                f" ({bill_n} bill"
                f"{'s' if bill_n != 1 else ''}, "
                f"{overdue} overdue)"
            ) if bill_n else ""
            lines.append(
                f"Payables: {len(data.payable_accts)} "
                f"account"
                f"{'s' if len(data.payable_accts) != 1 else ''}, "
                f"{currency} {data.payables_total}{signal}"
            )
            for name, bal in sorted(
                data.payable_accts, key=lambda x: x[1],
                reverse=True,
            ):
                lines.append(
                    f"  {name}: {currency} "
                    f"{bal.quantize(Decimal('0.01'))}"
                )
        return lines

    def _render_transactions_scheduled(
        self,
        book,
        total_txns: int,
        enabled_sx: int,
        currency: str,
    ) -> list[str]:
        """Render the Transactions count + Scheduled line.

        Scheduled folds in the "due in next 7 days" stat — turns
        the dashboard from "what is the state" into "what do I
        need to do next" without a second tool call. Uses
        ``hasattr`` to skip the upcoming-line render cleanly on
        book classes built without scheduling.
        """
        lines = [f"Transactions: {total_txns}"]
        if enabled_sx > 0:
            line = f"Scheduled: {enabled_sx} recurring"
            if hasattr(self, "_upcoming_within_days"):
                upcoming = self._upcoming_within_days(book, days=7)
                if upcoming["count"] > 0:
                    plural = (
                        "s" if upcoming["count"] != 1 else ""
                    )
                    total_int = int(upcoming["total"])
                    line += (
                        f", {upcoming['count']} due in next "
                        f"7 days ({currency} {total_int:,})"
                    )
                else:
                    line += ", none due in next 7 days"
            lines.append(line)
        return lines

    @staticmethod
    def _render_business_summary(
        n_customers: int,
        n_vendors: int,
        n_employees: int,
        n_budgets: int,
        commodity_mnemonics: list[str],
    ) -> list[str]:
        """Render Business / Budgets / Commodities one-liners.

        Each is conditional: only emitted when there's at least
        one entity to mention (absence-as-signal). Commodities
        always emits — the mnemonic list is the at-a-glance
        confirmation of which currencies the book exercises.
        """
        lines: list[str] = []
        if n_customers or n_vendors or n_employees:
            parts: list[str] = []
            if n_customers:
                parts.append(
                    f"{n_customers} customer"
                    f"{'s' if n_customers != 1 else ''}"
                )
            if n_vendors:
                parts.append(
                    f"{n_vendors} vendor"
                    f"{'s' if n_vendors != 1 else ''}"
                )
            if n_employees:
                parts.append(
                    f"{n_employees} employee"
                    f"{'s' if n_employees != 1 else ''}"
                )
            lines.append(f"Business: {', '.join(parts)}")
        if n_budgets:
            lines.append(f"Budgets: {n_budgets}")
        lines.append(f"Commodities: {', '.join(commodity_mnemonics)}")
        return lines

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

        Instant orientation: structure, balances, warnings, net-worth
        trajectory, runway, budget pace, reconciliation — one call.
        Investment and foreign-commodity accounts value at shares ×
        latest price with a tagged cost-basis fallback.

        Orchestrator only: data collection and rendering live in the
        ``_collect_*`` / ``_render_*`` helpers above; a new section
        is one method plus one ``lines.extend(...)`` call.
        """
        from piecash.budget import Budget
        from piecash.core.transaction import ScheduledTransaction

        with self.open(readonly=True) as book:
            default_currency = self._require_default_currency(book)
            currency = default_currency.mnemonic

            # Balances are as-of-today: future-dated TRANSACTIONS
            # are excluded so Assets/Liabilities agree with the
            # trajectory's "now" anchor. Prices are NOT
            # today-filtered: users write future-dated prices as
            # intentional forecasts, and balance_sheet uses the
            # absolute latest — ``_compute_net_worth_at``
            # special-cases as_of >= today the same way so all
            # three surfaces agree.
            today = date.today()

            # Template accounts (scheduled-transaction scaffolding).
            template_guids = self._template_account_guids(book)

            # Materialize once — this method and its sub-helpers
            # make many passes over book.accounts.
            accounts = list(book.accounts)

            # Future prices included — see the as-of note above.
            latest_prices = self._rates_as_of(book, today)
            # Provenance for intermediate-chain-derived rates,
            # so a synthesized valuation renders "@ rate (via
            # USD)" instead of an unfamiliar opaque number.
            rate_via = self._rate_provenance(book, today, default_currency)

            # Single-pass account walker: categorized lists + totals.
            data = self._collect_summary_balance_sheet(
                accounts=accounts,
                template_guids=template_guids,
                latest_prices=latest_prices,
                default_currency=default_currency,
                today=today,
                book=book,
                rate_via=rate_via,
            )

            # SX template recipes are filtered — a desktop template
            # dated years before the first real entry would stretch
            # the range and inflate counts (this list also feeds
            # _collect_warnings and the burn clamp).
            transactions = [
                t for t in book.transactions
                if not self._is_template_transaction(t, template_guids)
            ]
            total_txns = len(transactions)
            first_date: date | None = None
            last_date: date | None = None
            for txn in transactions:
                d = txn.post_date
                if d is None:  # old-book artifact
                    continue
                if first_date is None or d < first_date:
                    first_date = d
                if last_date is None or d > last_date:
                    last_date = d

            # Cross-mixin stats.
            all_sx = book.session.query(ScheduledTransaction).all()
            enabled_sx = sum(1 for sx in all_sx if sx.enabled)
            n_customers = len(list(book.customers))
            n_vendors = len(list(book.vendors))
            n_employees = len(list(book.employees))
            n_budgets = book.session.query(Budget).count()
            # 'template' namespace = GnuCash's pseudo-commodity for
            # SX template accounts.
            commodity_mnemonics = sorted(set(
                c.mnemonic for c in book.commodities
                if c.namespace.lower() != "template"
            ))
            biz_counts = self._business_summary_counts(book)

            # Section renderers chain in output order — reorder by
            # moving lines, not editing a template.
            lines: list[str] = []
            lines.extend(
                self._render_book_metadata(
                    currency, first_date, last_date,
                )
            )

            # Warnings near the top — integrity/stale-price issues
            # inform how the LLM reads the numbers below.
            warnings = self._collect_warnings(
                book, transactions, accounts,
            )
            if warnings:
                lines.append("Warnings:")
                for msg in warnings:
                    lines.append(f"  ⚠ {msg}")

            lines.append(f"Accounts: {data.total_accounts} total")
            lines.extend(
                self._render_assets_section(data, currency)
            )
            lines.extend(
                self._render_liabilities_section(data, currency)
            )
            lines.extend(
                self._render_receivables_payables(
                    data, biz_counts, currency,
                )
            )

            # Jobs: conditional one-liner pointing at the
            # drill-down tool.
            if biz_counts["active_jobs"] > 0:
                lines.append(
                    f"Jobs: {biz_counts['active_jobs']} active"
                )

            lines.append(
                f"Income: {data.income_active} active "
                f"({data.income_total} total)"
            )
            lines.append(
                f"Expenses: {data.expense_active} active "
                f"({data.expense_total} total)"
            )

            reconciliation = self._account_reconciliation_status(
                book, accounts,
            )
            lines.extend(
                self._render_reconciliation(reconciliation)
            )
            trajectory = self._net_worth_trajectory(
                book, first_date, accounts,
            )
            lines.extend(
                self._render_net_worth_trajectory(
                    trajectory, currency,
                )
            )
            monthly = self._monthly_net_income(
                book, transactions, months=6,
            )
            lines.extend(self._render_monthly_net(monthly))
            runway = self._runway_metrics(
                book, default_currency, transactions, accounts,
            )
            lines.extend(self._render_runway(runway, currency))
            budget = self._budget_headline(book, transactions)
            lines.extend(self._render_budget(budget))

            lines.extend(
                self._render_transactions_scheduled(
                    book, total_txns, enabled_sx, currency,
                )
            )
            lines.extend(
                self._render_business_summary(
                    n_customers, n_vendors, n_employees,
                    n_budgets, commodity_mnemonics,
                )
            )

            return "\n".join(lines)

    def list_accounts(
        self,
        root: str | None = None,
        compact: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> dict | str:
        """List all accounts in the chart of accounts.

        Leads with a ``Showing X-Y of Z accounts`` indicator (accounts
        are undated, so no date range). Compact output then emits one
        ``%shortguid<TAB>fullname [ANNOTATION]`` line per account; the
        short GUID is the cheap handle for subsequent calls (tools
        resolve ``%xxxxxxx``, full GUIDs, and paths interchangeably via
        ``_resolve_account``).

        Args:
            root: Optional subtree filter (path, ``%short``, or GUID).
            compact: If False, return a verbose envelope instead.
            limit: Page size (default 50, max 250). 0 = count only.
            offset: 0-indexed first row to return.
        """
        with self.open(readonly=True) as book:
            # Template accounts are GnuCash internals, not part of
            # the user's chart.
            template_guids = self._template_account_guids(book)

            # Normalize ``root`` to a fullname for the prefix
            # comparisons below.
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

            page, indicator = _paginate(
                filtered,
                offset=offset,
                limit=limit,
                max_cap=self.MAX_LIST_LIMIT,
                entity_name="accounts",
            )

            if compact:
                # Short-guid map spans the whole book so prefixes
                # stay unambiguous against every resolvable account.
                short_map = self._account_short_guid_map(book)
                lines = [indicator]
                lines += [
                    f"{short_map[a.guid]}\t{_account_to_compact_line(a)}"
                    for a in page
                ]
                return "\n".join(lines)
            else:
                return {
                    "showing": indicator,
                    "total": len(filtered),
                    "offset": offset,
                    "count": len(page),
                    "accounts": [_account_to_dict(a) for a in page],
                }

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

            return self._own_splits_balance(account, as_of=as_of_date)

    # Server-side ceiling for list_transactions / search_transactions
    # limits. Caller-supplied limits above this are clamped with a note.
    MAX_LIST_LIMIT = 250

    def list_transactions(
        self,
        account: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 50,
        offset: int = 0,
        compact: bool = True,
    ) -> dict | str:
        """List transactions with optional filters.

        Both modes lead with a ``Showing X-Y of Z transactions`` line
        spanning the full filtered set's date range, so a partial view
        is never silent. ``offset`` pages through; limits above
        ``MAX_LIST_LIMIT`` (250) clamp server-side and flag it.

        Args:
            account: Filter by account ref.
            start_date / end_date: Inclusive date bounds.
            limit: Page size. Capped at 250. ``0`` = count only.
            offset: 0-indexed first row to return.
            compact: One line per transaction (default) or a verbose
                envelope, most recent first.

        Raises:
            ValueError: If specified account not found.
        """
        with self.open(readonly=True) as book:
            # If filtering by account, get transactions through that account's splits
            focus_fullname: str | None = None
            if account:
                acct = self._resolve_account(book, account)
                if not acct:
                    raise ValueError(f"Account not found: {account}")
                # Canonical fullname for register-form rendering —
                # _transaction_to_compact_line compares against
                # split.account.fullname, so a raw %short ref would
                # silently fall through to the multi-split form.
                focus_fullname = acct.fullname
                transactions = {split.transaction for split in acct.splits}
            else:
                # Hide scheduled-transaction template recipes — real
                # Transaction rows in desktop-created books whose
                # splits post under root_template. The account-
                # filtered branch above is safe without the check
                # (``_resolve_account`` never resolves a template
                # account), but this unfiltered path would render a
                # stale "Mortgage Payment" recipe identically to a
                # real event.
                template_guids = self._template_account_guids(book)
                transactions = {
                    t for t in book.transactions
                    if not self._is_template_transaction(
                        t, template_guids
                    )
                }

            # Apply date filters. Null post_date rows (an old-book
            # artifact — see _query.py) sort as date.min: visible in
            # unbounded listings, excluded by any start_date bound.
            filtered = []
            for trans in transactions:
                post_date = trans.post_date or date.min
                if start_date and post_date < start_date:
                    continue
                if end_date and post_date > end_date:
                    continue
                filtered.append(trans)

            # Sort by date descending
            filtered.sort(
                key=lambda t: t.post_date or date.min, reverse=True
            )

            page, indicator = _paginate(
                filtered,
                offset=offset,
                limit=limit,
                max_cap=self.MAX_LIST_LIMIT,
                entity_name="transactions",
                date_key=lambda t: t.post_date,
            )

            if compact:
                # Prefix map spans ALL transactions so emitted
                # prefixes stay valid _resolve_guid keys; cached by
                # book mtime.
                prefixes = self._transaction_prefix_map(book)
                lines = [indicator]
                lines += [
                    _transaction_to_compact_line(
                        t, focus_account=focus_fullname, prefixes=prefixes
                    )
                    for t in page
                ]
                return "\n".join(lines)
            else:
                # Verbose also emits short prefixes — every
                # consuming tool accepts 8+ chars via _resolve_guid.
                txn_prefixes = self._transaction_prefix_map(book)
                split_prefixes = self._split_prefix_map(book)
                lot_prefixes = self._lot_prefix_map(book)
                return {
                    "showing": indicator,
                    "total": len(filtered),
                    "offset": offset,
                    "count": len(page),
                    "transactions": [
                        _transaction_to_dict(
                            t,
                            txn_prefixes=txn_prefixes,
                            split_prefixes=split_prefixes,
                            lot_prefixes=lot_prefixes,
                        )
                        for t in page
                    ],
                }

    def get_transaction(self, guid: str) -> dict | None:
        """Get details for a specific transaction by GUID.

        Returns the transaction dict, or None. Emitted ``guid`` /
        split ``guid`` / ``lot_guid`` fields carry collision-safe
        short prefixes — every tool that takes a GUID accepts an
        8+ char prefix via ``_resolve_guid``.
        """
        with self.open(readonly=True) as book:
            transaction = self._find_transaction(book, guid)
            if not transaction:
                return None
            return _transaction_to_dict(
                transaction,
                txn_prefixes=self._transaction_prefix_map(book),
                split_prefixes=self._split_prefix_map(book),
                lot_prefixes=self._lot_prefix_map(book),
            )

    _FUNDING_ACCOUNT_TYPES = {
        "BANK", "CASH", "ASSET", "CREDIT", "LIABILITY", "EQUITY",
    }

    @staticmethod
    def _extract_account_pattern(accounts) -> frozenset[str]:
        """Extract categorization (non-funding) account names.

        Filters out funding types (BANK, CASH, ASSET, CREDIT,
        LIABILITY, EQUITY) to isolate the expense/income pattern;
        falls back to all accounts when filtering leaves nothing
        (bank-to-bank transfers). Returns a frozenset of fullnames.
        """
        all_names = frozenset(a.fullname for a in accounts)
        categorization = frozenset(
            a.fullname for a in accounts
            if a.type not in CoreMixin._FUNDING_ACCOUNT_TYPES
        )
        return categorization if categorization else all_names

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

        The four signals (auto-fill source, auto-fill stability,
        duplicate candidates, recent matches) each need a full-table
        view; the collector folds them into one sort + one traversal.
        ``want_*`` flags opt into only the work the caller consumes.

        **Must be called before the new transaction is committed** —
        otherwise the just-created transaction shows up as a
        false-positive recent-match / duplicate of itself.

        Args:
            proposed_amounts: Absolute split values for the duplicate
                amount signal; pass ``[]`` when ``want_duplicates``
                is False.
            want_auto_fill: Most recent matching-description splits.
            want_stability: Warn when recent matches disagree on the
                categorization pattern.
            want_duplicates: Score the ±window range on description,
                amount, date; emit HIGH/MEDIUM candidates.
            want_recent: Keep top N matches for the post-write
                split-consistency warning.

        Returns:
            A ``_CreateSignals`` bundle; untracked signals keep
            their defaults.
        """
        today = date.today()
        stability_cutoff = today - timedelta(days=stability_days)
        recent_cutoff = today - timedelta(days=recent_days)
        dup_start = trans_date - timedelta(days=duplicate_window_days)
        dup_end = trans_date + timedelta(days=duplicate_window_days)
        desc_lower = description.lower()

        # Prefix map built once, shared across emitted guids;
        # cached by book mtime.
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

        # Proposed primary = headline amount (max abs split value)
        # for the duplicate amount-signal; zero when the caller
        # passed [] (that branch never runs then).
        proposed_primary = max(proposed_amounts) if proposed_amounts else Decimal("0")

        # Local accumulators — each bucket is independent. We finalize
        # them into the returned _CreateSignals after the loop.
        auto_fill_source = None  # piecash.Transaction
        stability_matches: list = []  # list[piecash.Transaction]
        recent_matches: list = []  # list[piecash.Transaction]
        duplicates: list[dict] = []

        # One sort, one iteration, descending — recent-first lets
        # the capped buckets short-circuit. Null post_date rows sort
        # oldest and are skipped (every bucket does date math).
        sorted_txns = sorted(
            book.transactions,
            key=lambda t: t.post_date or date.min,
            reverse=True,
        )

        for txn in sorted_txns:
            # Template recipes, not events — see the note above.
            if self._is_template_transaction(txn, template_guids):
                continue

            # Voided transactions are not signal sources: the void-
            # and-re-enter workflow makes the voided txn the most
            # recent match, and auto-fill would clone its zeroed
            # splits into a silent $0 transaction. Duplicates /
            # stability skip them too.
            if any(_is_voided(s) for s in txn.splits):
                continue

            # Undated rows can't anchor cadence or duplicate-window
            # math.
            if txn.post_date is None:
                continue

            # Empty descriptions carry no signal: "" substring-matches
            # everything, so an empty-description transaction would
            # desc-match every proposal and auto-fill could clone an
            # unrelated transaction instead of raising "no match".
            txn_desc_lower = txn.description.lower()
            desc_match = (
                bool(desc_lower.strip())
                and bool(txn_desc_lower.strip())
                and (
                    desc_lower in txn_desc_lower
                    or txn_desc_lower in desc_lower
                )
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
                # Comparing any-to-any across every
                # split pair is the trap: on multi-split transactions
                # (paychecks with 10+ deduction splits) it produces
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
        """Render the duplicate-candidates list as a compact TSV
        string (no header — ``create_transaction``'s docstring
        documents the shape)::

            confidence<TAB>guid<TAB>date<TAB>amount<TAB>description<TAB>signals

        ~40 chars per candidate vs ~120 for list-of-dicts JSON.
        Returns ``""`` for empty input — ``_strip_noise`` drops
        empty-string values, so unconditional assignment is safe.
        The internal list-of-dicts stays rich for
        ``has_high_duplicate``; only the response boundary is TSV.
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
        """Validate splits and pre-resolve accounts — before any
        mutation. Single chokepoint for the input-shape rules
        ``create_transaction`` and ``update_transaction`` share:

        - Splits sum to zero (in transaction currency).
        - Every ``account`` ref resolves (path, ``%short``, or GUID).
        - ``quantity`` is implicit (account commodity == transaction
          currency → quantity == value) or explicit and same-signed
          (cross-currency).

        Validate-then-mutate matters: interleaving lets a bad input
        leave the transaction partially mutated if the session
        doesn't roll back cleanly.

        Returns:
            Dicts (input order) with resolved ``account``, Decimal
            ``value`` / ``quantity``, ``memo``, and ``original_ref``
            (raw input, preserved for downstream errors).

        Raises:
            ValueError on imbalance, unknown account, missing
            cross-currency quantity, or sign mismatch.
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
            splits: Each with 'account', 'amount' (transaction
                currency), optional 'quantity' (required when the
                account commodity differs) and 'memo'. If omitted,
                auto-fills from the most recent transaction with a
                matching description.
            trans_date: Defaults to today.
            currency: ISO code; defaults to the book default.
            notes: Free-text annotation stored apart from description.
            check_duplicates: Run duplicate detection. Default True.
            force_create: Create even past a HIGH-confidence duplicate.
            dry_run: Validate and return the proposal without writing.

        Returns:
            Dict with 'guid' and 'status'; may include 'warnings',
            'duplicates', and 'auto_filled_from'. A HIGH duplicate
            without force_create returns 'status': 'rejected'.

            'duplicates', when present, is newline-separated TSV::

                confidence<TAB>guid<TAB>date<TAB>amount<TAB>description<TAB>signals

            Confidence is HIGH or MEDIUM; signals is a three-char
            D/A/D code (description / amount / date, dash = no match).

        Raises:
            ValueError: imbalance, <2 splits, unknown account,
                missing cross-currency quantity, or no auto-fill
                match.
        """
        # Dry runs don't need a writable session; all other paths do.
        readonly = dry_run
        if trans_date is None:
            trans_date = date.today()

        # One book-open for the whole create pipeline — preflight signal
        # gathering, write, and post-write consistency warning all live
        # inside this session.
        with self.open(readonly=readonly) as book:
            # Pass 1 only when splits=None: the auto-fill source is
            # needed before proposed_amounts exist for the duplicate
            # scan. With explicit splits, everything happens in
            # pass 2 — a single scan.
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

            # Sum-to-zero / resolution / cross-currency checks live
            # in the shared validator (paired with
            # update_transaction). Currency resolution stays here —
            # create accepts ``currency=``; update reuses the
            # transaction's.

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

            # A HIGH duplicate short-circuits the write; the
            # rejection always carries at least one candidate.
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

            # Writable sessions may auto-create the currency via ISO
            # fallback; dry_run is readonly and must find an
            # existing one.
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

            # Validator returns pre-resolved accounts + Decimals;
            # the loop below is pure motion against validated data.
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
            # Cross-commodity implied-rate sanity (decimal slips,
            # inverted pairs) — non-blocking, caught at entry.
            warnings.extend(
                self._fx_sanity_warnings(
                    book, validated, trans_currency, trans_date,
                )
            )
            proposed_pattern = self._extract_account_pattern(resolved_accounts)
            # Recent matches were gathered pre-write, so the new txn
            # is automatically absent.
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

            # Short prefix — feeds straight back into guid-accepting
            # tools.
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

    def create_transactions(
        self,
        transactions: list[dict],
        force: bool = False,
        dry_run: bool = False,
        on_error: str = "abort",
    ) -> dict:
        """Create multiple transactions in one atomic save.

        Spec: specs/BATCH_TRANSACTION_ENTRY_SPEC.md. Each entry is
        ``{ref, date (date), description, notes (optional),
        splits: [{account, amount, memo (optional),
        quantity (optional)}]}`` — quantity per the
        ``_validate_transaction_splits`` contract (required iff the
        account commodity differs from the book default).

        Three phases under one book-open: validate all structurally,
        screen each against existing-book duplicates, then build every
        accepted transaction and ``save()`` once. ``on_error="abort"``
        (default) sinks the whole batch on any structural failure;
        ``"skip"`` keeps the good rows. A HIGH duplicate rejects only
        its own row (``force=True`` overrides).

        Returns a thin envelope: ``results`` TSV (always) and
        ``duplicates`` TSV (only when a match exists; otherwise empty,
        which ``_strip_noise`` drops). The transaction currency is
        always the book default (a differently-denominated
        transaction needs ``create_transaction``'s ``currency``
        parameter); splits on non-default-commodity accounts carry
        an explicit ``quantity``. No intra-batch dedup.
        """
        if on_error not in ("abort", "skip"):
            raise ValueError("on_error must be 'abort' or 'skip'")
        refs = [t["ref"] for t in transactions]
        if len(set(refs)) != len(refs):
            raise ValueError(
                "duplicate ref in batch — each ref must be unique"
            )
        readonly = dry_run
        by_ref: dict = {}        # ref -> final result row
        dup_rows: list = []      # (ref, candidate-dict) for the FK table

        with self.open(readonly=readonly) as book:
            default_currency = self._require_default_currency(book)

            # --- Phase 1: structural validation (every row) ---
            prepared = []
            for txn in transactions:
                ref = txn["ref"]
                try:
                    splits = txn["splits"]
                    if len(splits) < 2:
                        raise ValueError("at least 2 splits required")
                    validated = self._validate_transaction_splits(
                        book, splits, default_currency,
                    )
                    for v in validated:
                        if v["account"].placeholder:
                            raise ValueError(
                                f"account '{v['account'].fullname}' is a "
                                f"placeholder and cannot receive splits"
                            )
                    prepared.append({
                        "ref": ref,
                        "description": txn["description"],
                        "notes": txn.get("notes") or "",
                        "trans_date": txn["date"],
                        "validated": validated,
                        "proposed_amounts": [
                            abs(_to_decimal(s["amount"])) for s in splits
                        ],
                    })
                except (ValueError, KeyError) as e:
                    by_ref[ref] = {
                        "ref": ref, "status": "rejected", "reason": str(e),
                    }

            # abort: any structural failure sinks the batch — the valid
            # rows report batch_aborted, nothing is written.
            if by_ref and on_error == "abort":
                for p in prepared:
                    by_ref[p["ref"]] = {
                        "ref": p["ref"], "status": "rejected",
                        "reason": "batch_aborted",
                    }
                return self._batch_envelope(transactions, by_ref, [])

            # --- Phase 2: duplicate screen (against existing book) ---
            accepted = []
            for p in prepared:
                signals = self._collect_create_signals(
                    book, p["description"], p["trans_date"],
                    p["proposed_amounts"],
                    want_auto_fill=False, want_stability=False,
                    want_duplicates=True, want_recent=False,
                )
                dups = signals.duplicates
                for d in dups:
                    dup_rows.append((p["ref"], d))
                if signals.has_high_duplicate and not force:
                    by_ref[p["ref"]] = {
                        "ref": p["ref"], "status": "rejected",
                        "reason": "duplicate_detected", "dup_count": len(dups),
                    }
                else:
                    accepted.append((p, len(dups)))

            # Cross-commodity implied-rate sanity per accepted row
            # (non-blocking) — surfaced as a side table keyed by ref,
            # so a decimal slip in a bulk import is caught too.
            warn_rows: list = []
            for p, _dc in accepted:
                for w in self._fx_sanity_warnings(
                    book, p["validated"], default_currency, p["trans_date"],
                ):
                    warn_rows.append((p["ref"], w["message"]))

            # --- Phase 3: build all accepted rows, one save ---
            if dry_run:
                for p, dup_count in accepted:
                    by_ref[p["ref"]] = {
                        "ref": p["ref"], "status": "would_create",
                        "dup_count": dup_count,
                    }
                return self._batch_envelope(
                    transactions, by_ref, dup_rows, warn_rows,
                )

            built = []
            for p, dup_count in accepted:
                piecash_splits = [
                    piecash.Split(
                        account=v["account"], value=v["value"],
                        quantity=v["quantity"], memo=v["memo"] or "",
                    )
                    for v in p["validated"]
                ]
                txn_obj = piecash.Transaction(
                    currency=default_currency,
                    description=p["description"],
                    notes=p["notes"] or None,
                    post_date=p["trans_date"],
                    splits=piecash_splits,
                )
                built.append((p["ref"], txn_obj, dup_count))

            # Single flush for the whole batch — per the "don't flush
            # mid-build" rule, every Transaction is fully constructed
            # before the one save.
            book.save()

            all_guids = [t.guid for t in book.transactions]
            for ref, txn_obj, dup_count in built:
                by_ref[ref] = {
                    "ref": ref, "status": "created",
                    "txn_guid": _unique_prefix(txn_obj.guid, all_guids),
                    "dup_count": dup_count,
                }

            return self._batch_envelope(
                transactions, by_ref, dup_rows, warn_rows,
            )

    def _batch_envelope(
        self, transactions: list[dict], by_ref: dict, dup_rows: list,
        warn_rows: list | None = None,
    ) -> dict:
        """Assemble the {results, duplicates, warnings} TSV envelope in
        input order. Empty ``duplicates`` / ``warnings`` render as "" so
        _strip_noise drops them — absence of the key means none."""
        rows = [by_ref[t["ref"]] for t in transactions]
        return {
            "results": self._batch_results_to_tsv(rows),
            "duplicates": self._batch_duplicates_to_tsv(dup_rows),
            "warnings": self._batch_warnings_to_tsv(warn_rows or []),
        }

    @staticmethod
    def _batch_warnings_to_tsv(warn_rows: list) -> str:
        """WARNINGS table: ``ref<TAB>message`` per flagged row, FK to
        results. Empty string when nothing was flagged."""
        if not warn_rows:
            return ""
        lines = ["ref\tmessage"]
        for ref, message in warn_rows:
            lines.append(f"{ref}\t{message}")
        return "\n".join(lines)

    @staticmethod
    def _batch_results_to_tsv(rows: list[dict]) -> str:
        """RESULTS table: header + one row per input transaction.
        Blank cells for fields a given status doesn't carry; ``dup_count``
        of 0 renders as "0", absent renders blank."""
        header = "ref\tstatus\ttxn_guid\tdup_count\treason"
        lines = [header]
        for r in rows:
            dup = r["dup_count"] if "dup_count" in r else ""
            lines.append(
                f"{r['ref']}\t{r['status']}\t{r.get('txn_guid', '')}\t"
                f"{dup}\t{r.get('reason', '')}"
            )
        return "\n".join(lines)

    @staticmethod
    def _batch_duplicates_to_tsv(dup_rows: list) -> str:
        """DUPLICATES table: single-entry's column order with ``ref``
        prepended as the FK. Empty string when no row has a match."""
        if not dup_rows:
            return ""
        lines = ["ref\tconfidence\tguid\tdate\tamount\tdescription\tsignals"]
        for ref, d in dup_rows:
            lines.append(
                f"{ref}\t{d['confidence']}\t{d['guid']}\t{d['date']}\t"
                f"{d['amount']}\t{d['description']}\t{d['signals']}"
            )
        return "\n".join(lines)

    def search_transactions(
        self,
        query: str,
        field: str = "description",
        limit: int = 50,
        offset: int = 0,
        compact: bool = True,
    ) -> dict | str:
        """Search transactions by field.

        Pagination mirrors ``list_transactions`` (``Showing X-Y of Z``
        indicator + offset + 250 cap).

        Args:
            query: Search string. For 'amount': exact ("100.00"),
                ">100", "<100", or range "100-200".
            field: 'description', 'memo', 'notes', or 'amount'.
            limit: Page size. Capped at 250. ``0`` = count only.
            offset: 0-indexed first row to return.
            compact: One line per transaction (default) or a verbose
                envelope.

        Raises:
            ValueError: If field is not valid.
        """
        if field not in ("description", "memo", "notes", "amount"):
            raise ValueError(f"Invalid search field: {field}")

        with self.open(readonly=True) as book:
            matched = []

            # Same template-recipe filter as list_transactions: all
            # four field modes would otherwise match SX templates in
            # desktop-created books.
            template_guids = self._template_account_guids(book)
            for transaction in book.transactions:
                if self._is_template_transaction(
                    transaction, template_guids
                ):
                    continue
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

            # Sort by date descending; null post_date (old-book
            # artifact) sorts oldest.
            matched.sort(
                key=lambda t: t.post_date or date.min, reverse=True
            )

            page, indicator = _paginate(
                matched,
                offset=offset,
                limit=limit,
                max_cap=self.MAX_LIST_LIMIT,
                entity_name="transactions",
                date_key=lambda t: t.post_date,
            )

            if compact:
                # Prefix map cached by book mtime.
                prefixes = self._transaction_prefix_map(book)
                lines = [indicator]
                lines += [
                    _transaction_to_compact_line(t, prefixes=prefixes)
                    for t in page
                ]
                return "\n".join(lines)
            else:
                return {
                    "showing": indicator,
                    "total": len(matched),
                    "offset": offset,
                    "count": len(page),
                    "transactions": [
                        _transaction_to_dict(t) for t in page
                    ],
                }

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

    @staticmethod
    def _validate_account_name(name: str) -> None:
        """Validate a user-supplied account name.

        ``:`` is the path separator — a name containing it corrupts
        every downstream ``fullname.split(":")``. Control characters
        round-trip badly through SQLite and the audit log; empty
        names aren't meaningful. Shared chokepoint for create_account
        and update_account's rename branch. Raises ``ValueError``.
        """
        if not name or not name.strip():
            raise ValueError("Account name cannot be empty")
        if ":" in name:
            raise ValueError(
                f"Account name cannot contain ':' (path separator). "
                f"Got: {name!r}. To create a nested account, pass the "
                f"leaf name as ``name`` and the full parent path as "
                f"``parent``."
            )
        if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in name):
            raise ValueError(
                f"Account name contains control characters. "
                f"Got: {name!r}."
            )

    # UTF-8 byte cap for the account "notes" slot — same limit as
    # customer/vendor notes in the business module (kept as a local
    # constant: core must not depend on BusinessMixin, which may not
    # be composed into the book class).
    _ACCOUNT_NOTES_MAX_BYTES = 4096

    @classmethod
    def _validate_account_notes(cls, notes: str) -> None:
        byte_len = len(notes.encode("utf-8"))
        if byte_len > cls._ACCOUNT_NOTES_MAX_BYTES:
            raise ValueError(
                f"notes exceeds {cls._ACCOUNT_NOTES_MAX_BYTES}-byte "
                f"cap ({byte_len} bytes supplied). Shorten the value "
                f"and retry."
            )

    def create_account(
        self,
        name: str,
        account_type: str,
        parent: str | None = None,
        description: str = "",
        placeholder: bool = False,
        commodity: str | None = None,
        commodity_namespace: str = "CURRENCY",
        notes: str = "",
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
            notes: Optional free-text notes, stored in the "notes"
                   slot GnuCash desktop's account editor reads.

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

        # Validate the account name (shared chokepoint with
        # update_account's rename branch).
        self._validate_account_name(name)
        if notes:
            self._validate_account_notes(notes)

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
            if notes:
                new_account["notes"] = notes

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
        notes: str | None = None,
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
            notes: New notes ("notes" slot, shared with GnuCash
                desktop's account editor). Pass "" to clear — the
                slot is deleted, matching a desktop-cleared field.

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

            # Diff-style echo — only changed fields, matching
            # update_transaction.
            changed: dict = {}

            # Check for name conflict if renaming
            if new_name and new_name != account.name:
                # Same validation as create_account — the rename path
                # is a parallel entry point for the same corruption.
                self._validate_account_name(new_name)
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

            if notes is not None:
                self._validate_account_notes(notes)
                try:
                    current_notes = _slot_value_str(account["notes"])
                except KeyError:
                    current_notes = ""
                if notes == "" and current_notes:
                    del account["notes"]
                    changed["notes"] = ""
                elif notes and notes != current_notes:
                    account["notes"] = notes
                    changed["notes"] = notes

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
                # fullname answers "where did it land"; parent makes
                # the move legible without re-parsing the path.
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

            # No short-prefix GUID in the response — post-delete the
            # handle is unaddressable and _resolve_guid would raise
            # on any attempt to use it. fullname + status suffices.
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

            # Refuse to delete an invoice's posting transaction: it
            # orphans the invoice's posted-state metadata, after
            # which the invoice refuses both delete ("posted") and
            # re-post ("already posted") — SQL surgery is the only
            # escape. unpost_invoice clears the metadata properly.
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
        fields landed ("every write is verified" for
        ``update_transaction`` / ``replace_splits``).

        piecash has silently no-op'd setattrs on some slot-backed
        fields; ``session.expire`` bypasses the identity map so we
        read disk, not cache. Raises RuntimeError on mismatch.
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
            if len(actual_splits) != len(expected_splits):
                raise RuntimeError(
                    f"Transaction write verification failed: "
                    f"{len(actual_splits)} splits on disk, "
                    f"expected {len(expected_splits)}"
                )
            # Multiset per account: keying by fullname alone would
            # collapse two splits to the SAME account (legal via
            # replace_splits), leaving the second unverified.
            actual_by_acct: dict[str, list] = {}
            for s in actual_splits:
                actual_by_acct.setdefault(
                    s.account.fullname, []
                ).append(
                    (Decimal(str(s.value)), Decimal(str(s.quantity)))
                )
            for expected in expected_splits:
                # Normalize the input ref (path / %short / GUID) to
                # canonical fullname — post-save splits are keyed by
                # Account.fullname.
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
                bucket = actual_by_acct.get(acct_fullname)
                if not bucket:
                    raise RuntimeError(
                        f"Transaction write verification failed: "
                        f"split for {acct_fullname!r} (input "
                        f"ref {ref!r}) not found post-save"
                    )
                ev = _to_decimal(expected["amount"])
                eq = (
                    _to_decimal(expected["quantity"])
                    if "quantity" in expected else None
                )
                # Consume the first split matching value (and quantity,
                # when the caller specified it).
                match_idx = next(
                    (
                        i for i, (av, aq) in enumerate(bucket)
                        if av == ev and (eq is None or aq == eq)
                    ),
                    None,
                )
                if match_idx is None:
                    # No match — surface a precise diff against the
                    # first remaining entry (the only one in the common
                    # single-split-per-account case).
                    av, aq = bucket[0]
                    if av != ev:
                        raise RuntimeError(
                            f"Transaction write verification failed: "
                            f"split {acct_fullname!r} value on disk is "
                            f"{av}, expected {ev}"
                        )
                    raise RuntimeError(
                        f"Transaction write verification failed: "
                        f"split {acct_fullname!r} quantity on "
                        f"disk is {aq}, expected {eq}"
                    )
                bucket.pop(match_idx)

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
            description / trans_date / notes: Optional; ``notes=""``
                clears.
            splits: Optional split updates matched to existing splits
                by account; cross-currency splits need 'quantity'.
            force: Allow modifying reconciled splits (only checked
                when splits change).

        Returns:
            Thin dict: {guid, date, description, status}.

        Raises:
            ValueError: not found, voided, imbalance, account not in
                transaction, missing quantity, or reconciled without
                force.
        """
        with self.open(readonly=False) as book:
            transaction = self._find_transaction(book, guid)
            if not transaction:
                raise ValueError(f"Transaction not found: {guid}")

            # Voided transactions are immutable: writing into
            # state='v' splits moves balance sums while staying
            # invisible to cash_flow/lots/reconciliation, and a
            # later re-void overwrites the void-former-* slots,
            # destroying the originals. No force override.
            if any(_is_voided(s) for s in transaction.splits):
                raise ValueError(
                    f"Transaction {guid} is voided. Use "
                    f"unvoid_transaction first, then update."
                )

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

            fx_warnings: list[dict] = []

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

                # Shared validator: sum-to-zero, resolution,
                # cross-currency quantity/sign — validate-then-mutate
                # (see _validate_transaction_splits).
                validated = self._validate_transaction_splits(
                    book, splits, trans_currency,
                )
                fx_warnings = self._fx_sanity_warnings(
                    book, validated, trans_currency,
                    trans_date or transaction.post_date,
                )

                # Build a map keyed by resolved-account-fullname so we
                # can match against existing splits' ``account.fullname``.
                split_updates = {
                    v["account"].fullname: v for v in validated
                }
                # Raw input dicts preserved so memo updates (not
                # carried by the validator) still apply.
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

            # Round-trip verify — a piecash silent setattr no-op
            # would otherwise ship a thin response that lies about
            # what's stored.
            self._verify_transaction_state(
                book, transaction,
                expected_description=description,
                expected_date=trans_date,
                expected_notes=notes,
                expected_splits=splits,
            )

            # Thin response: enough for a sanity check; full state
            # via get_transaction. The audit log resolves omitted
            # fields from params (_resolve_entry_field).
            short_guid = _unique_prefix(
                transaction.guid, (t.guid for t in book.transactions)
            )
            result = {
                "guid": short_guid,
                "date": transaction.post_date.isoformat(),
                "description": transaction.description,
                "status": "updated",
            }
            if fx_warnings:
                result["warnings"] = fx_warnings
            return result

    def replace_splits(
        self,
        guid: str,
        splits: list[dict],
        force: bool = False,
    ) -> dict:
        """Replace all splits in a transaction with a new set.

        Currency, description, date, and notes are preserved; the
        new splits must balance to zero.

        Args:
            splits: Complete new set — 'account', 'amount', optional
                'quantity' (cross-currency) and 'memo'.
            force: Required if existing splits are reconciled or
                assigned to lots.

        Returns:
            Thin dict with guid, status, previous_splits (audit
            trail), and any warnings.

        Raises:
            ValueError: not found, voided, imbalance, unknown or
                placeholder account, missing quantity, or
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

            # 2. Previous splits captured pre-delete via the compact
            # serializer — old GUIDs aren't addressable anymore
            # (~50 chars/split vs ~140).
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

            # 4a. Voided transactions are immutable — same
            # rationale as update_transaction; no force override.
            if any(_is_voided(s) for s in transaction.splits):
                raise ValueError(
                    f"Transaction {guid} is voided. Use "
                    f"unvoid_transaction first, then replace splits."
                )

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
            fx_check_splits: list[dict] = []
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

                fx_check_splits.append({
                    "account": account, "value": amount, "quantity": quantity,
                })
                piecash.Split(
                    account=account,
                    value=amount,
                    quantity=quantity,
                    memo=split_data.get("memo", ""),
                    transaction=transaction,
                )

            # Cross-commodity implied-rate sanity (non-blocking) — this
            # path's warnings are plain strings, so emit messages.
            warnings.extend(
                w["message"] for w in self._fx_sanity_warnings(
                    book, fx_check_splits, trans_currency,
                    transaction.post_date,
                )
            )

            # 8. Save
            book.save()

            # 8a. Verify the new splits landed — same invariant as
            # update_transaction, just with the splits-only path.
            self._verify_transaction_state(
                book, transaction, expected_splits=splits,
            )

            # 9. Thin response — previous_splits is the one piece
            # the caller doesn't already have. The audit log falls
            # back to params for the "after" splits.
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
