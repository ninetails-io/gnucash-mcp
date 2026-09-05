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
from gnucash_mcp._format import (
    _candidate_comparison_tsv,
    _dry_run_summary,
    _format_number,
    _paginate,
    _split_match_verdict,
    _tsv_cell,
)

_debug_logger = logging.getLogger(DEBUG_LOGGER_NAME)

from gnucash_mcp.book._base import (
    _slot_bool,
    _account_to_compact_line,
    _account_to_dict,
    _commodity_quantum,
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


# Shared correspondence thresholds — the ONE definition both
# matchers (the duplicate screen in _collect_create_signals and the
# statement candidate scan in enter_statement) read, so the A/D
# signal semantics can't drift between the two surfaces. The
# ADMISSION rules deliberately differ (documented at each site):
# the book-wide duplicate screen demands >=2 signals; the
# account-scoped statement scan admits the amount signal alone,
# because its narrow universe makes an amount match meaningful —
# and the spec's own monthly-rent case carries only that signal.
_MATCH_AMOUNT_TOLERANCE = Decimal("1.00")
_MATCH_DATE_TIGHT_DAYS = 2

# Statement classification: desc + amount matching at roughly a
# month's remove is the RECURRING-PAYMENT signature (last month's
# rent), not same-event correspondence — such candidates ship as
# evidence but never drive MATCH/AMBIGUOUS (bookkeeper T6, the
# third specimen of the near-match family). Three weeks: beyond
# realistic clearing drift, comfortably inside monthly cadence.
# Biweekly patterns (14d paychecks) stay below it deliberately —
# a slow check clearing two weeks late is still a plausible match,
# and judgment rules those with the evidence in hand.
_RECURRENCE_MIN_DAYS = 21


def _post_date_as_date(transaction) -> date | None:
    """Transaction post_date normalized to a bare date — piecash
    stores it with a neutral-time datetime component."""
    pd = transaction.post_date
    if hasattr(pd, "date") and callable(pd.date):
        return pd.date()
    return pd


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

            # User-declared opt-out: loans, escrow payables, cash
            # wallets — accounts with no statement to reconcile
            # against. Reporting-only: reconcile_account and
            # get_unreconciled_splits still work normally on
            # flagged accounts. The excluded row keeps the account
            # visible to get_reconciliation_status (no silent
            # caps); the dashboard renderer drops it.
            if _slot_bool(account, "no_reconcile") is True:
                results.append({
                    "account": account.fullname,
                    "status": "excluded (no_reconcile)",
                    "days_behind": None,
                    "unreconciled_count": 0,
                    "excluded": True,
                })
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
            unreconciled_value = Decimal("0")
            oldest_unreconciled_date = None
            balance = Decimal("0")
            for s in account.splits:
                # Voided splits are zombies, not reconcilable
                # activity — they must not make an account
                # surface in the reconciliation section.
                if _is_voided(s):
                    continue
                any_splits = True
                balance += s.quantity
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
                    unreconciled_value += s.quantity
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
                        "unreconciled_value": str(unreconciled_value),
                        "commodity": account.commodity.mnemonic,
                        "latest_y_date": latest_y_date.isoformat(),
                        "oldest_unreconciled_date":
                            oldest_unreconciled_date.isoformat(),
                    })
                else:
                    days_behind = (today - latest_y_date).days
                    results.append({
                        "balance_zero": balance == 0,
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
        from gnucash_mcp.book._base import _slot_bool

        node = account
        while node is not None and node.type != "ROOT":
            flag = _slot_bool(node, self._RETIREMENT_SLOT_KEY)
            if flag is not None:
                return flag
            # Absent or unrecognized: keep walking rather than guess.
            node = node.parent
        return any(
            "retirement" in part.lower()
            for part in account.fullname.split(":")
        )

    # Stale-price threshold for the Warnings section — past a month,
    # quotes are likely skewing net-worth and runway numbers.
    _STALE_PRICE_DAYS = 30

    def _overdue_scheduled_warnings(
        self, book: piecash.Book, today: date,
    ) -> list[dict]:
        """Overdue-scheduled entries, most overdue first — each
        ``{days, name, msg}``; ``len()`` still feeds the Scheduled
        line's overdue count.

        Requires SchedulingMixin's helpers (_next_occurrence,
        RECURRENCE_TO_FREQUENCY). When that module isn't loaded,
        the attribute lookup degrades gracefully via getattr and
        this returns []. Shared between the Warnings section and
        the Scheduled line's overdue count so the two surfaces
        agree by construction.
        """
        next_occ_fn = getattr(self, "_next_occurrence", None)
        rec_to_freq = getattr(self, "RECURRENCE_TO_FREQUENCY", None)
        if next_occ_fn is None or rec_to_freq is None:
            return []
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
                            sx.name,
                            f"Overdue scheduled: {sx.name} "
                            f"due {next_occ.isoformat()}",
                        ))
                except Exception:
                    continue
            overdue_entries.sort(
                key=lambda e: e[0], reverse=True,
            )
            return [
                {"days": d, "name": n, "msg": m}
                for d, n, m in overdue_entries
            ]
        except Exception:
            return []

    def _collect_warnings(
        self,
        book: piecash.Book,
        transactions: list,
        accounts: list,
        overdue_scheduled: list[dict] | None = None,
        last_entry_days_behind: int | None = None,
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

        ``overdue_scheduled`` lets get_book_summary pass the list it
        already computed (shared with the Scheduled line); ``None``
        computes it here for direct callers.
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
                # Polymorphic owner + effective side (BusinessMixin
                # chokepoints). The side-keyed finders rendered every
                # overdue voucher and job-attached bill as "Past due
                # invoice: #NNN" — wrong type, no name — while
                # get_outstanding_documents named it correctly
                # (whole-tree review, 1c).
                find_owner = getattr(
                    self, "_find_invoice_owner_by_guid", None,
                )
                effective_owner_type = getattr(
                    self, "_effective_owner_type", None,
                )
                type_labels = getattr(
                    self, "_OWNER_TYPE_TO_RESPONSE_TYPE", {},
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
                        eff_ot = (
                            effective_owner_type(book, inv)
                            if effective_owner_type is not None
                            else inv.owner_type
                        )
                        doc_type = type_labels.get(eff_ot, "invoice")

                        owner = (
                            find_owner(
                                book, inv.owner_type, inv.owner_guid,
                            )
                            if find_owner is not None else None
                        )
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
            stale_entries: list[tuple[int, str, str]] = []
            for commodity in book.commodities:
                if commodity == default_currency:
                    continue
                if commodity.guid not in in_use:
                    continue
                latest = by_commodity_latest.get(commodity.guid)
                if latest is None:
                    stale_entries.append((
                        10**9,  # arbitrary large sort key — top
                        commodity.mnemonic,
                        f"Stale price: {commodity.mnemonic} no price on file",
                    ))
                elif latest < cutoff:
                    days_old = (today - latest).days
                    stale_entries.append((
                        days_old,
                        commodity.mnemonic,
                        f"Stale price: {commodity.mnemonic} "
                        f"last updated {days_old} days ago",
                    ))
            stale_entries.sort(key=lambda e: e[0], reverse=True)
            stale_prices = self._rollup_warnings(
                [m for _, _, m in stale_entries],
                names=[n for _, n, m in stale_entries],
                oldest_days=(
                    None if not stale_entries
                    or stale_entries[0][0] >= 10**9
                    else stale_entries[0][0]
                ),
                noun="commodities",
                aggregate_prefix="Stale prices",
                no_data_count=sum(
                    1 for d, _, _ in stale_entries if d >= 10**9
                ),
                escape_hatch="get_prices / create_prices to refresh",
            )
        except Exception:
            # Per spec: skip failed checks, emit the rest.
            pass

        # ── 5. Overdue scheduled transactions ──
        if overdue_scheduled is None:
            overdue_scheduled = self._overdue_scheduled_warnings(
                book, today,
            )

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

        # Overdue-scheduled rollup: small lists stay itemized;
        # beyond the threshold, one aggregate line carries count,
        # oldest, leading names, and the escape hatch. Framed as
        # homework, not verdict — an overdue schedule means "not
        # entered", which may be unentered activity rather than a
        # missed payment.
        overdue_sched_lines = self._rollup_warnings(
            [e["msg"] for e in overdue_scheduled],
            names=[e["name"] for e in overdue_scheduled],
            oldest_days=(
                overdue_scheduled[0]["days"]
                if overdue_scheduled else None
            ),
            noun="transactions",
            aggregate_prefix="Overdue scheduled",
            escape_hatch=(
                "list_scheduled_transactions for all; verify "
                "entered vs missed"
            ),
        )

        # Staleness linkage: when the book itself is far behind,
        # time-based warnings describe the gap, not events — say so
        # FIRST, where it frames everything below it.
        staleness_note: list[str] = []
        if (
            last_entry_days_behind is not None
            and last_entry_days_behind > self._LAST_ENTRY_WARN_DAYS
            and (overdue_scheduled or overdue_invoices)
        ):
            staleness_note.append(
                f"Book is {last_entry_days_behind} days behind — "
                f"time-based warnings below may reflect unentered "
                f"activity, not missed events"
            )

        return (
            staleness_note
            + integrity
            + backup_health
            + low_cash
            + overdue_invoices
            + overdue_sched_lines
            + stale_prices
        )

    # Itemize-vs-aggregate threshold for the Warnings rollups: at or
    # below this, per-item lines carry more signal than a summary;
    # above it, the list is noise burying the other warnings.
    _WARNING_ROLLUP_THRESHOLD = 3

    @classmethod
    def _rollup_warnings(
        cls,
        messages: list[str],
        *,
        names: list[str],
        oldest_days: int | None,
        noun: str,
        aggregate_prefix: str,
        escape_hatch: str,
        no_data_count: int = 0,
    ) -> list[str]:
        """Collapse a per-item warning list into one aggregate line
        past the threshold (chokepoint shared by the scheduled and
        stale-price rollups — the two collapse rules can't drift).

        The aggregate keeps the decision signal (count, oldest age,
        leading names) and points at the tool that lists the rest;
        per the clearance principle it assigns homework, never a
        verdict.
        """
        if len(messages) <= cls._WARNING_ROLLUP_THRESHOLD:
            return list(messages)
        shown = ", ".join(names[:3])
        more = len(names) - min(3, len(names))
        if more > 0:
            shown += f", +{more} more"
        qualifiers = []
        if oldest_days is not None:
            qualifiers.append(f"oldest {oldest_days} days")
        if no_data_count:
            qualifiers.append(f"{no_data_count} with no price on file")
        qual = f", {'; '.join(qualifiers)}" if qualifiers else ""
        return [
            f"{aggregate_prefix}: {len(messages)} {noun}"
            f"{qual} ({shown}) — {escape_hatch}"
        ]

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

    def _burn_window_days(
        self, transactions: list, days: int | None = None,
    ) -> int:
        """Actual burn-averaging window in days.

        Book-age clamp: ``_RUNWAY_BURN_DAYS`` is a MAX, not a fixed
        denominator — dividing by 180 on a 19-day-old book
        overstates runway ~10×. The 1-day floor avoids
        divide-by-zero. Shared between ``_daily_expense_burn`` (the
        divisor) and the Runway render (the label) so the displayed
        window always matches the math.
        """
        if days is None:
            days = self._RUNWAY_BURN_DAYS
        today = date.today()
        dated = [
            t.post_date for t in transactions
            if t.post_date is not None  # old-book artifact
        ]
        if dated:
            book_age_days = max(1, (today - min(dated)).days)
            days = min(days, book_age_days)
        return days

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
        days = self._burn_window_days(transactions, days)
        today = date.today()
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
        burn_window = self._burn_window_days(
            transactions, self._RUNWAY_BURN_DAYS,
        )

        if daily_burn <= 0:
            return None

        if liquid < 0:
            return {
                "negative_liquid": True,
                "liquid": liquid.quantize(Decimal("1")),
                "daily_burn": daily_burn.quantize(Decimal("1")),
                "burn_window_days": burn_window,
            }

        runway_days = int(liquid / daily_burn)
        return {
            "negative_liquid": False,
            "runway_days": runway_days,
            "liquid": liquid.quantize(Decimal("1")),
            "daily_burn": daily_burn.quantize(Decimal("1")),
            "burn_window_days": burn_window,
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

        The MTD entry additionally carries ``mtd_comparable``:
        the prior month's net over the SAME day window (day 1
        through today's day-of-month, clamped to the prior
        month's length) — a partial month next to full months
        misleads without a like-for-like anchor.

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

        # Prior-month same-day-window accumulator for the MTD
        # comparable (clamped: Jul 30 compares against Jun 30, and
        # Mar 30 against Feb 28).
        prior_idx = len(month_starts) - 2
        prior_cutoff_day = (
            min(today.day, month_ends[prior_idx].day)
            if prior_idx >= 0 else None
        )
        prior_window_net = Decimal("0")

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
            in_prior_window = (
                idx == prior_idx and d.day <= prior_cutoff_day
            )
            for s in txn.splits:
                atype = s.account.type
                if atype not in ("INCOME", "EXPENSE"):
                    continue
                amt = self._split_in_default_currency(
                    s, s.account, factors.get(s.account.guid),
                )
                # INCOME is stored negative (credit-natural) and
                # flips to a positive contribution; EXPENSE is
                # stored positive and subtracts. Both reduce to
                # negating the raw amount.
                contribution = -amt
                nets[idx] += contribution
                if in_prior_window:
                    prior_window_net += contribution
                has_activity = True

        if not has_activity:
            return []

        today_month_start = date(today.year, today.month, 1)
        result: list[dict] = []
        for i in range(len(month_starts) - 1, -1, -1):
            start = month_starts[i]
            entry = {
                "label": start.strftime("%b %Y"),
                "net": nets[i].quantize(Decimal("1")),
                "is_mtd": start == today_month_start,
            }
            if entry["is_mtd"] and prior_idx >= 0:
                prior_start = month_starts[prior_idx]
                entry["mtd_comparable"] = {
                    "label": (
                        f"{prior_start.strftime('%b')} "
                        f"1-{prior_cutoff_day}"
                    ),
                    "net": prior_window_net.quantize(Decimal("1")),
                }
            result.append(entry)
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

    def _classify_reconciliation(self, entry: dict) -> str:
        """One bucket per reconciliation-status row — the SHARED
        classification for the dashboard renderer and
        get_reconciliation_status, so the aggregate counts and the
        drill-down table agree by construction.

        Buckets: ``excluded`` (no_reconcile opt-out), ``never``,
        ``behind`` (stale with pending work OR a carried balance —
        months of silence on a carried balance means missing
        entries, since interest posts monthly), ``dormant`` (stale
        but $0 and fully reconciled: nothing owed, nothing a
        statement could reveal — the bookkeeper's stamped-dormant-
        cards finding), ``current``.
        """
        if entry.get("excluded"):
            return "excluded"
        if entry["status"] == "never reconciled":
            return "never"
        if entry["days_behind"] > self._RECONCILE_WARN_DAYS:
            if (
                entry["unreconciled_count"] == 0
                and entry.get("balance_zero")
            ):
                return "dormant"
            return "behind"
        return "current"

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
        dormant_count = 0
        for entry in reconciliation:
            bucket = self._classify_reconciliation(entry)
            if bucket == "excluded":
                # no_reconcile opt-outs: dashboard-silent by the
                # user's own declaration; get_reconciliation_status
                # is the honesty backstop.
                continue
            if bucket == "never":
                never_count += 1
            elif bucket == "dormant":
                dormant_count += 1
            elif bucket == "behind":
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
                # Materiality: the net unreconciled amount sits
                # beside the split count — a 174-split backlog
                # netting to 40 units is a different chore than
                # one netting to 12,000 (from the outside-model
                # dashboard review, 2026-08-21).
                value_part = ""
                if "unreconciled_value" in entry:
                    amt = Decimal(entry["unreconciled_value"])
                    value_part = (
                        f" / {entry['commodity']} "
                        f"{_format_number(abs(amt))} net"
                    )
                out.append(
                    f"  {leaf}: {n} split{plural}{value_part} "
                    f"unreconciled ({lag_inner}, oldest: {oldest}) ⚠"
                )
            else:
                out.append(
                    f"  {leaf}: {entry['status']} {lag} ⚠"
                )
        if current_count:
            plural = "s" if current_count != 1 else ""
            out.append(f"  {current_count} account{plural} current")
        if dormant_count:
            plural = "s" if dormant_count != 1 else ""
            out.append(
                f"  {dormant_count} account{plural} dormant "
                f"($0, fully reconciled)"
            )
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
        MTD entries get a "(MTD)" suffix plus the prior month's
        net over the same day window — without the like-for-like
        anchor, a partial month reads as a collapse next to full
        months.
        """
        if not monthly:
            return []
        out = ["Monthly net (income - expenses, last 6 months):"]
        for entry in monthly:
            label = entry["label"]
            suffix = ""
            if entry["is_mtd"]:
                label += " (MTD)"
                comparable = entry.get("mtd_comparable")
                if comparable is not None:
                    suffix = (
                        f" (vs {comparable['label']}: "
                        f"{int(comparable['net']):+,})"
                    )
            # Always shows explicit sign + thousands separator;
            # whole dollars (cents would noise up the summary).
            out.append(f"  {label}: {int(entry['net']):+,}{suffix}")
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
        window = runway["burn_window_days"]
        warn = " ⚠" if days < self._RUNWAY_WARN_DAYS else ""
        return [
            f"Runway: {days} days{warn} "
            f"({currency} {liquid:,} liquid / "
            f"{currency} {burn:,}/day burn, "
            f"{window}-day avg)"
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

    def _frequent_accounts(
        self, book, transactions, days: int = 180, top: int = 15,
    ) -> list[str]:
        """The book's working vocabulary: most-posted accounts.

        Short GUIDs went essentially unused in live bookkeeping
        because they were never in the model's context at the
        moment its account vocabulary formed — it paged
        list_accounts once, or guessed paths. Handing the top
        accounts (with their ``%short`` refs, in list_accounts'
        exact line format) to every session at orientation makes
        the compact refs the path of least resistance, and the
        list is book-adaptive: each book surfaces its own chart's
        vocabulary in its own language.

        Placeholders can't receive splits, so posting frequency
        naturally yields only postable accounts; template
        recipes are excluded explicitly.
        """
        cutoff = date.today() - timedelta(days=days)
        template_guids = self._template_account_guids(book)
        counts: dict[str, int] = {}
        by_guid: dict = {}
        for txn in transactions:
            post_date = txn.post_date
            if isinstance(post_date, datetime):
                post_date = post_date.date()
            if post_date is None or post_date < cutoff:
                continue
            if self._is_template_transaction(txn, template_guids):
                continue
            for s in txn.splits:
                g = s.account.guid
                counts[g] = counts.get(g, 0) + 1
                by_guid[g] = s.account
        ranked = sorted(
            counts.items(), key=lambda kv: (-kv[1], by_guid[kv[0]].fullname),
        )[:top]
        if not ranked:
            return []
        short_map = self._account_short_guid_map(book)
        lines = [f"Frequently used accounts (last {days} days):"]
        for g, _n in ranked:
            lines.append(
                f"  {short_map[g]}\t{_account_to_compact_line(by_guid[g])}"
            )
        return lines

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
        explicitly. The "included in ..." note prevents the
        breakout from being double-counted against the Assets /
        Liabilities headline totals, which already fold these in.
        """
        lines: list[str] = []
        if data.receivable_accts:
            inv_n = biz_counts["open_invoices"]
            overdue = biz_counts["overdue_invoices"]
            signal = (
                f" ({inv_n} invoice"
                f"{'s' if inv_n != 1 else ''}, "
                f"{overdue} overdue; included in Assets total)"
            ) if inv_n else " (included in Assets total)"
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
                f"{overdue} overdue; included in Liabilities total)"
            ) if bill_n else " (included in Liabilities total)"
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
        overdue_count: int = 0,
    ) -> list[str]:
        """Render the Transactions count + Scheduled line.

        Scheduled folds in the overdue count and the "due in next
        7 days" stat — turns the dashboard from "what is the
        state" into "what do I need to do next" without a second
        tool call. The overdue bucket must agree with the Warnings
        section (same underlying list) — "10 overdue" in Warnings
        next to a bare "none due in next 7 days" here read as a
        contradiction, since overdue items ARE due. Uses
        ``hasattr`` to skip the upcoming-line render cleanly on
        book classes built without scheduling.
        """
        lines = [f"Transactions: {total_txns}"]
        if enabled_sx > 0:
            line = f"Scheduled: {enabled_sx} recurring"
            if overdue_count > 0:
                line += f", {overdue_count} overdue ⚠"
            if hasattr(self, "_upcoming_within_days"):
                upcoming = self._upcoming_within_days(book, days=7)
                if upcoming["count"] > 0:
                    plural = (
                        "s" if upcoming["count"] != 1 else ""
                    )
                    total_int = int(upcoming["total"])
                    amount_part = f"{currency} {total_int:,}"
                    # Foreign-currency schedules with no market
                    # rate can't join the sum — say so rather than
                    # silently understate the week's bills.
                    if upcoming.get("unrated"):
                        amount_part += (
                            f" + {upcoming['unrated']} foreign "
                            f"w/o rate ⚠"
                        )
                    line += (
                        f", {upcoming['count']} due in next "
                        f"7 days ({amount_part})"
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
            # Whole-book report: every pass below traverses splits, so
            # load the graph once instead of lazily per traversal.
            self._preload_split_graph(book)

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
            # inform how the LLM reads the numbers below. Overdue
            # scheduled computes once here and feeds BOTH the
            # Warnings section and the Scheduled line's overdue
            # count, so the two can't disagree.
            overdue_sched = self._overdue_scheduled_warnings(
                book, date.today(),
            )
            days_behind_for_warnings = (
                (date.today() - last_date).days
                if last_date is not None else None
            )
            warnings = self._collect_warnings(
                book, transactions, accounts,
                overdue_scheduled=overdue_sched,
                last_entry_days_behind=days_behind_for_warnings,
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

            lines.extend(
                self._frequent_accounts(book, transactions)
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
                    overdue_count=len(overdue_sched),
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
        query: str | None = None,
    ) -> dict | str:
        """List all accounts in the chart of accounts.

        Leads with a ``Showing X-Y of Z accounts`` indicator (accounts
        are undated, so no date range). Compact output then emits one
        ``%shortguid<TAB>fullname [ANNOTATION]`` line per account; the
        short GUID is the cheap handle for subsequent calls (tools
        resolve ``%xxxxxxx``, full GUIDs, and paths interchangeably via
        ``_resolve_account``).

        ``query`` is a case-insensitive substring match against the
        full path AND the description — the description matters on
        numbered charts (SKR03 "4930" carries its meaning in the
        description, not the name). Substring, not word match, so it
        is locale-neutral by construction. Composes with ``root``.

        Args:
            root: Optional subtree filter (path, ``%short``, or GUID).
            compact: If False, return a verbose envelope instead.
            limit: Page size (default 50, max 250). 0 = count only.
            offset: 0-indexed first row to return.
            query: Optional case-insensitive substring filter on
                path/description.
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

            needle = query.lower() if query else None
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
                if needle is not None:
                    haystack = (
                        f"{account.fullname}\n"
                        f"{account.description or ''}"
                    ).lower()
                    if needle not in haystack:
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
                raise self._account_not_found_error(book, account_name)

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

    def _proposal_category_primary(
        self, book, splits: list[dict],
    ) -> Decimal | None:
        """Signed max-abs value among the proposal's category
        (non-funding) legs — the direction anchor for the duplicate
        amount signal (R3 P5). None when any ref fails to resolve
        or no category leg exists (transfers) — the signal then
        falls back to magnitude comparison."""
        legs = []
        for sp in splits:
            acct = self._resolve_account(book, sp["account"])
            if acct is None:
                return None
            if acct.type not in self._FUNDING_ACCOUNT_TYPES:
                try:
                    legs.append(_to_decimal(sp["amount"]))
                except ValueError:
                    return None
        if not legs:
            return None
        return max(legs, key=abs)

    def _signal_sweep(
        self, book: piecash.Book,
    ) -> list[tuple["piecash.Transaction", str]]:
        """The full-table traversal every signal collection shares:
        sort ``book.transactions`` recent-first and drop the rows
        that can never be signal sources. Returns ``(transaction,
        lowercased_description)`` pairs.

        Batch surfaces (``create_transactions``, ``enter_statement``)
        call ``_collect_create_signals`` once or twice PER ROW; the
        sweep is the per-call cost that dominates on large books
        (the debug log's p95 7s tail). Compute it once per book
        session and pass it to every ``_collect_create_signals``
        call whose screening happens against the same pre-write
        table state.

        **Capture before any of the batch's transactions are
        committed** — a sweep taken after a write would let the
        just-created rows shadow themselves as duplicates; a sweep
        taken before stays honestly pre-write for every row screened
        against it. The list holds live ORM objects: never let it
        outlive the ``open()`` session that built it.

        Three argument-independent filters live here so they run
        once, not once per call:

        - **Template recipes, not events.** GnuCash persists each
          SX's split template as a real Transaction row whose splits
          post under ``book.root_template``. A user entering a
          mortgage payment for the first time would otherwise always
          see the Mortgage template as a "duplicate" candidate via
          description + date match, even with a stale template
          amount that kills the A signal. Filtering at the sweep
          boundary blocks leakage into every bucket downstream:
          auto-fill, stability, duplicates, and recent-matches.
        - **Voided transactions are not signal sources**: the void-
          and-re-enter workflow makes the voided txn the most recent
          match, and auto-fill would clone its zeroed splits into a
          silent $0 transaction.
        - **Undated rows** can't anchor cadence or duplicate-window
          math.
        """
        from sqlalchemy.orm import selectinload

        from piecash.core.transaction import Transaction

        template_guids = self._template_account_guids(book)
        # Bulk-load every transaction WITH its splits collection in
        # two indexed queries — the voided-source filter below reads
        # ``txn.splits`` for every row, and the lazy per-transaction
        # collection load was one SELECT each: O(book transactions)
        # round-trips per sweep, on exactly the large books this
        # hoist exists for (release-review finding 8, second half).
        # The swept list keeps the surviving rows (and their loaded
        # splits) strongly referenced for the session — the identity
        # map alone holds them weakly.
        loaded = (
            book.session.query(Transaction)
            .options(selectinload(Transaction.splits))
            .all()
        )
        swept = [
            (txn, txn.description.lower())
            for txn in loaded
            if txn.post_date is not None
            and not self._is_template_transaction(txn, template_guids)
            and not any(_is_voided(s) for s in txn.splits)
        ]
        # One sort, descending — recent-first lets the capped
        # buckets in the collector short-circuit.
        swept.sort(key=lambda pair: pair[0].post_date, reverse=True)
        return swept

    def _collect_create_signals(
        self,
        book: piecash.Book,
        description: str,
        trans_date: date,
        proposed_amounts: list[Decimal],
        *,
        proposed_category_primary: Decimal | None = None,
        want_auto_fill: bool,
        want_stability: bool,
        want_duplicates: bool,
        want_recent: bool,
        trans_currency: str | None = None,
        sweep: list[tuple["piecash.Transaction", str]] | None = None,
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
            sweep: A precomputed ``_signal_sweep(book)`` — pass it
                when calling per-row in a batch so the table
                traversal amortizes to one; ``None`` computes a
                fresh sweep (single-call sites).

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

        # Proposed primary = headline amount (max abs split value)
        # for the duplicate amount-signal; zero when the caller
        # passed [] (that branch never runs then). When BOTH sides
        # have a category anchor (proposed_category_primary + the
        # candidate's), the signal compares SIGNED category
        # primaries instead — a +104 refund is not a -104
        # payment's twin, and the direction lives in the category
        # legs because a balanced transaction always carries both
        # signs (bookkeeper R3 P5, blocker-class). Magnitude
        # comparison remains the fallback for transfer-shaped
        # rows with no category leg.
        proposed_primary = max(proposed_amounts) if proposed_amounts else Decimal("0")

        # Local accumulators — each bucket is independent. We finalize
        # them into the returned _CreateSignals after the loop.
        auto_fill_source = None  # piecash.Transaction
        stability_matches: list = []  # list[piecash.Transaction]
        recent_matches: list = []  # list[piecash.Transaction]
        duplicates: list[dict] = []

        # The sorted, pre-filtered traversal — shared across a
        # batch's calls when the caller passes it in (see
        # _signal_sweep for the filters and the pre-write caveat).
        if sweep is None:
            sweep = self._signal_sweep(book)

        for txn, txn_desc_lower in sweep:
            # Empty descriptions carry no signal: "" substring-matches
            # everything, so an empty-description transaction would
            # desc-match every proposal and auto-fill could clone an
            # unrelated transaction instead of raising "no match".
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
                cand_cat_values = [
                    s.value for s in txn.splits
                    if s.account.type
                    not in CoreMixin._FUNDING_ACCOUNT_TYPES
                ]
                cand_cat_primary = (
                    max(cand_cat_values, key=abs)
                    if cand_cat_values else None
                )
                # Amounts only compare within the same currency
                # frame: 188 HKD and 188 CNY are not the same money,
                # and the cross-frame version of a true duplicate
                # has numerically DIFFERENT values — so cross-
                # currency amount matches are coincidence by
                # construction (bookkeeper finding, cur-column
                # round). Cross-currency candidates can still reach
                # MEDIUM on description+date, labeled by the cur
                # column.
                same_frame = (
                    trans_currency is None
                    or txn.currency.mnemonic == trans_currency
                )
                if (
                    same_frame
                    and proposed_category_primary is not None
                    and cand_cat_primary is not None
                ):
                    amount_match = (
                        abs(
                            proposed_category_primary
                            - cand_cat_primary
                        )
                        <= _MATCH_AMOUNT_TOLERANCE
                    )
                else:
                    amount_match = same_frame and (
                        abs(proposed_primary - primary_amount)
                        <= _MATCH_AMOUNT_TOLERANCE
                    )

                # Signal 3: date within ±2 days of trans_date (tighter
                # than the window filter — window is for "worth
                # considering at all").
                date_match = (
                    abs((txn.post_date - trans_date).days)
                    <= _MATCH_DATE_TIGHT_DAYS
                )

                signals = sum([desc_match, amount_match, date_match])
                if signals >= 2:
                    confidence = "HIGH" if signals == 3 else "MEDIUM"
                    signal_str = (
                        ("D" if desc_match else "-")
                        + ("A" if amount_match else "-")
                        + ("D" if date_match else "-")
                    )
                    # Category (non-funding) legs, for the ruling-9
                    # self-contained comparison; all legs when
                    # filtering leaves nothing (transfers), same
                    # fallback as _extract_account_pattern.
                    # SIGNED legs: a refund's inverted categories
                    # must not read exact against the purchase's
                    # (R3 P5).
                    cat_legs = [
                        (s.account.fullname, str(s.value))
                        for s in txn.splits
                        if s.account.type
                        not in CoreMixin._FUNDING_ACCOUNT_TYPES
                    ] or [
                        (s.account.fullname, str(s.value))
                        for s in txn.splits
                    ]
                    # Most-anchored split state: a reconciled
                    # candidate is definitely entered AND tied —
                    # decisive for the duplicate call (bookkeeper
                    # T7: the shared table's state column must not
                    # sit empty on the batch surface).
                    states = {s.reconcile_state for s in txn.splits}
                    txn_state = (
                        "y" if "y" in states
                        else "c" if "c" in states
                        else "f" if "f" in states else "n"
                    )
                    primary_signed = (
                        cand_cat_primary
                        if cand_cat_primary is not None
                        else max(
                            (s.value for s in txn.splits), key=abs,
                        )
                    )
                    duplicates.append({
                        "confidence": confidence,
                        "guid": txn_prefixes[txn.guid],
                        "state": txn_state,
                        # The frame predicate the amount signal
                        # actually used — comparability is
                        # candidate-vs-PROPOSAL currency, not
                        # candidate-vs-book-default (review
                        # finding: 100 EUR vs 100 USD rendered as
                        # a perfect twin). currency_code is always
                        # present so the cur label can name the
                        # frame either way.
                        "same_frame": same_frame,
                        "currency_code": txn.currency.mnemonic,
                        "date": txn.post_date.isoformat(),
                        "description": txn.description,
                        "notes": txn.notes or "",
                        "categories": cat_legs,
                        "amount": str(primary_amount),
                        "primary_signed": str(primary_signed),
                        # Labeling non-default candidates lets the
                        # caller interpret a cross-currency MEDIUM
                        # (desc+date) without a follow-up read —
                        # and explains why equal-looking numbers
                        # did NOT amount-match. Empty = book
                        # default.
                        "currency": (
                            txn.currency.mnemonic
                            if txn.currency != book.default_currency
                            else ""
                        ),
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

            confidence<TAB>guid<TAB>date<TAB>amount<TAB>cur<TAB>description<TAB>signals

        ~40 chars per candidate vs ~120 for list-of-dicts JSON.
        Returns ``""`` for empty input — ``_strip_noise`` drops
        empty-string values, so unconditional assignment is safe.
        The internal list-of-dicts stays rich for
        ``has_high_duplicate``; only the response boundary is TSV.
        """
        return "\n".join(
            f"{d['confidence']}\t{d['guid']}\t{d['date']}\t"
            f"{d['amount']}\t{d.get('currency', '')}\t"
            f"{d['description']}\t{d['signals']}"
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
                raise self._account_not_found_error(book, ref)

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
                "action": split.get("action"),
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

                confidence<TAB>guid<TAB>date<TAB>amount<TAB>cur<TAB>description<TAB>signals

            Confidence is HIGH or MEDIUM; signals is a three-char
            D/A/D code (description / amount / date, dash = no match).

        Raises:
            ValueError: imbalance, <2 splits, unknown account,
                missing cross-currency quantity, or no auto-fill
                match.
        """
        # Dry runs don't need a writable session; all other paths do.
        readonly = dry_run
        # Defaults resolve loudly, explicit inputs echo nothing: when
        # the caller omitted the date, the response says which day
        # "today" resolved to — server, client, and book can sit in
        # different time zones (a UTC-hosted deployment is a day
        # ahead of a Pacific book every evening).
        date_defaulted = trans_date is None
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
            # Splitless means TWO collector calls (auto-fill
            # preflight + duplicate scan) — precompute the sweep so
            # the table traversal happens once. Explicit splits make
            # one call; let it sweep for itself so error paths
            # before pass 2 stay cheap.
            sweep = self._signal_sweep(book) if not splits else None
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
                    sweep=sweep,
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
            # The frame is passed as a mnemonic (currency resolution
            # proper happens below and may CREATE the commodity —
            # mutation stays after the rejection path). A currency
            # not yet in the book matches no existing transaction's
            # frame, which is exactly right.
            frame_code = (
                currency.upper() if currency
                else self._require_default_currency(book).mnemonic
            )
            signals = self._collect_create_signals(
                book,
                description,
                trans_date,
                proposed_amounts,
                proposed_category_primary=(
                    self._proposal_category_primary(book, splits)
                ),
                want_auto_fill=False,
                want_stability=False,
                want_duplicates=check_duplicates,
                want_recent=True,
                trans_currency=frame_code,
                sweep=sweep,
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
                            action=v["action"] or "",
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
            if date_defaulted:
                result["date"] = trans_date.isoformat()
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
        currency (optional ISO code — the row's transaction
        currency, defaulting to the book default),
        splits: [{account, amount, memo (optional),
        quantity (optional)}]}`` — quantity per the
        ``_validate_transaction_splits`` contract (required iff the
        account commodity differs from the row's transaction
        currency). An EMPTY
        splits list is an auto-fill request: the row reproduces the
        most recent matching-description transaction (splits, memos,
        quantities), marked ``auto_filled_from:<guid>`` in the
        results ``reason`` column; no match rejects the row.

        Three phases under one book-open: validate all structurally,
        screen each against existing-book duplicates, then build every
        accepted transaction and ``save()`` once. ``on_error="abort"``
        (default) sinks the whole batch on any structural failure;
        ``"skip"`` keeps the good rows. A HIGH duplicate rejects only
        its own row (``force=True`` overrides).

        Returns a thin envelope: ``results`` TSV (always) and
        ``duplicates`` TSV (only when a match exists; otherwise empty,
        which ``_strip_noise`` drops). Rows without ``currency``
        denominate in the book default; rows with it balance in
        that currency instead (splits on accounts of any OTHER
        commodity carry an explicit ``quantity``). The duplicate
        amount signal compares within the same currency frame only
        (188 HKD is not 188 CNY); a same-description same-date twin
        in another currency can still surface as a labeled,
        non-blocking MEDIUM. ``currency`` cannot combine with an
        auto-fill row (the source transaction's own currency
        governs a reproduction). No intra-batch dedup.
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

            # One table sweep for the whole batch — phase 1's
            # auto-fill preflights and phase 2's per-row duplicate
            # screens all read the same pre-write table state
            # (writes don't start until phase 3), so sharing the
            # sweep is behavior-identical and turns O(rows) table
            # traversals into one. Lazy: an all-explicit batch that
            # aborts in phase 1 never pays for it.
            _sweep_memo: list = []

            def _sweep():
                if not _sweep_memo:
                    _sweep_memo.append(self._signal_sweep(book))
                return _sweep_memo[0]

            # --- Phase 1: structural validation (every row) ---
            prepared = []
            for txn in transactions:
                ref = txn["ref"]
                try:
                    splits = txn["splits"]
                    # Row's transaction currency (the ``cur``
                    # column); absent means the book default.
                    row_currency = default_currency
                    if txn.get("currency"):
                        row_currency = self._find_commodity(
                            book, txn["currency"],
                        )
                        if not row_currency:
                            raise ValueError(
                                f"currency '{txn['currency']}' not "
                                f"found in book — create it first "
                                f"(create_commodity) or record a "
                                f"transaction in it once"
                            )
                        if not splits:
                            # Auto-fill reproduces a source txn in
                            # the SOURCE's currency — silently
                            # re-denominating it would corrupt the
                            # copied amounts.
                            raise ValueError(
                                "cur cannot combine with an "
                                "auto-fill (splitless) row — supply "
                                "explicit splits"
                            )
                    auto_filled_from = None
                    auto_fill_warnings: list[dict] = []
                    if not splits:
                        # A splitless row is an auto-fill request —
                        # same contract as create_transaction with
                        # ``splits`` omitted: reproduce the most
                        # recent matching-description transaction.
                        preflight = self._collect_create_signals(
                            book, txn["description"], txn["date"],
                            proposed_amounts=[],
                            want_auto_fill=True, want_stability=True,
                            want_duplicates=False, want_recent=False,
                            sweep=_sweep(),
                        )
                        if preflight.auto_fill is None:
                            raise ValueError(
                                "no matching transaction to auto-fill "
                                "from — provide explicit splits"
                            )
                        splits, auto_filled_from = preflight.auto_fill
                        auto_fill_warnings = preflight.stability_warnings
                    if len(splits) < 2:
                        raise ValueError("at least 2 splits required")
                    validated = self._validate_transaction_splits(
                        book, splits, row_currency,
                    )
                    for v in validated:
                        if v["account"].placeholder:
                            raise self._placeholder_error(v["account"])
                    prepared.append({
                        "ref": ref,
                        "description": txn["description"],
                        "notes": txn.get("notes") or "",
                        "trans_date": txn["date"],
                        "currency": row_currency,
                        "validated": validated,
                        "proposed_amounts": [
                            abs(_to_decimal(s["amount"])) for s in splits
                        ],
                        "auto_filled_from": auto_filled_from,
                        "auto_fill_warnings": auto_fill_warnings,
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
                p_cat_values = [
                    v["value"] for v in p["validated"]
                    if v["account"].type
                    not in self._FUNDING_ACCOUNT_TYPES
                ]
                p_cat_primary = (
                    max(p_cat_values, key=abs)
                    if p_cat_values else None
                )
                signals = self._collect_create_signals(
                    book, p["description"], p["trans_date"],
                    p["proposed_amounts"],
                    proposed_category_primary=p_cat_primary,
                    want_auto_fill=False, want_stability=False,
                    want_duplicates=True, want_recent=False,
                    trans_currency=p["currency"].mnemonic,
                    sweep=_sweep(),
                )
                dups = signals.duplicates
                if dups:
                    # Proposal-side context for the ruling-9
                    # comparison rows — computed once per row.
                    p_cats = [
                        (v["account"].fullname, str(v["value"]))
                        for v in p["validated"]
                        if v["account"].type
                        not in self._FUNDING_ACCOUNT_TYPES
                    ] or [
                        (v["account"].fullname, str(v["value"]))
                        for v in p["validated"]
                    ]
                    proposal = {
                        "desc": p["description"],
                        "date": p["trans_date"],
                        # SIGNED primary (max-abs split's value) —
                        # the comparison table reads sign as
                        # direction on both surfaces, so a +50
                        # deposit and a -50 payment never render
                        # as a perfect twin (review finding).
                        "amount": (
                            p_cat_primary
                            if p_cat_primary is not None
                            else max(
                                (v["value"] for v in p["validated"]),
                                key=abs,
                            )
                            if p["validated"] else Decimal("0")
                        ),
                        "cats": p_cats,
                    }
                    for d in dups:
                        dup_rows.append((p["ref"], d, proposal))
                # Duplicates arrive HIGH-first, so [0] is the max —
                # the results-table shortcut that saves the
                # two-table join for the common decision.
                max_conf = dups[0]["confidence"] if dups else ""
                if signals.has_high_duplicate and not force:
                    by_ref[p["ref"]] = {
                        "ref": p["ref"], "status": "rejected",
                        "reason": "duplicate_detected",
                        "dup_count": len(dups),
                        "max_confidence": max_conf,
                    }
                else:
                    accepted.append((p, len(dups), max_conf))

            # Cross-commodity implied-rate sanity per accepted row
            # (non-blocking) — surfaced as a side table keyed by ref,
            # so a decimal slip in a bulk import is caught too.
            warn_rows: list = []
            for p, _dc, _mc in accepted:
                for w in p["auto_fill_warnings"]:
                    warn_rows.append((p["ref"], w["message"]))
                for w in self._fx_sanity_warnings(
                    book, p["validated"], p["currency"], p["trans_date"],
                ):
                    warn_rows.append((p["ref"], w["message"]))

            # Auto-fill must be loud in the results: a row that
            # ACCIDENTALLY lost its splits either rejects (no match)
            # or lands here, visibly marked with its source.
            def _fill_marker(p) -> dict:
                if p["auto_filled_from"]:
                    return {
                        "reason": (
                            f"auto_filled_from:"
                            f"{p['auto_filled_from']['guid']}"
                        ),
                    }
                return {}

            # --- Phase 3: build all accepted rows, one save ---
            if dry_run:
                # Ruling 8: a projected-action label must not
                # masquerade as clearance. would_create stays only
                # on candidate-free rows (there it is an honest,
                # verified clearance); any row with >=1 candidate
                # reports review_required. HIGH blocks stay
                # rejected + reason.
                for p, dup_count, max_conf in accepted:
                    by_ref[p["ref"]] = {
                        "ref": p["ref"],
                        "status": (
                            "review_required" if dup_count
                            else "would_create"
                        ),
                        "dup_count": dup_count,
                        "max_confidence": max_conf,
                        **_fill_marker(p),
                    }
                envelope = self._batch_envelope(
                    transactions, by_ref, dup_rows, warn_rows,
                )
                # Ruling 7 upgrades: shared rehearsal header (counts
                # + homework, never a clearance) and the projected
                # per-account effects footer — the rehearsal-
                # completeness principle, minus the tie (batches
                # assert no closing balance).
                n_review = sum(
                    1 for r in by_ref.values()
                    if r["status"] == "review_required"
                )
                n_would = len(accepted) - n_review
                n_rejected = len(transactions) - len(accepted)
                with_cands = sum(
                    1 for r in by_ref.values() if r.get("dup_count")
                )
                if n_review:
                    homework = (
                        f"{n_review} rows are review_required — "
                        f"rule each against the duplicates table "
                        f"before committing."
                    )
                elif with_cands:
                    homework = (
                        f"{with_cands} rows have duplicate "
                        f"candidates — review the duplicates table "
                        f"before committing."
                    )
                else:
                    homework = "No duplicate candidates."
                summary = _dry_run_summary(
                    len(transactions), "rows",
                    [("would_create", n_would),
                     ("review_required", n_review),
                     ("rejected", n_rejected)],
                    homework,
                )
                effects: dict[str, list] = {}
                for p, _dc, _mc in accepted:
                    if _dc:
                        # review_required rows are homework, not a
                        # settled projection — including them would
                        # let the footer masquerade as clearance
                        # (review finding; worst under force).
                        continue
                    for v in p["validated"]:
                        key = v["account"].fullname
                        entry = effects.setdefault(
                            key,
                            [Decimal("0"),
                             v["account"].commodity.mnemonic],
                        )
                        entry[0] += v["quantity"]
                effects_tsv = ""
                if effects:
                    # Deltas are in each account's OWN commodity —
                    # the commodity column keeps a mixed batch
                    # legible under one heading.
                    out = ["account\tdelta\tcommodity"]
                    for name in sorted(effects):
                        delta, mnemonic = effects[name]
                        out.append(
                            f"{_tsv_cell(name)}\t{delta}\t"
                            f"{mnemonic}"
                        )
                    effects_tsv = "\n".join(out)
                return {
                    "summary": summary,
                    **envelope,
                    "effects": effects_tsv,
                }

            built = []
            for p, dup_count, _max_conf in accepted:
                piecash_splits = [
                    piecash.Split(
                        account=v["account"], value=v["value"],
                        quantity=v["quantity"], memo=v["memo"] or "",
                        action=v["action"] or "",
                    )
                    for v in p["validated"]
                ]
                txn_obj = piecash.Transaction(
                    currency=p["currency"],
                    description=p["description"],
                    notes=p["notes"] or None,
                    post_date=p["trans_date"],
                    splits=piecash_splits,
                )
                built.append((p, txn_obj, dup_count, _max_conf))

            # Single flush for the whole batch — per the "don't flush
            # mid-build" rule, every Transaction is fully constructed
            # before the one save.
            book.save()

            all_guids = [t.guid for t in book.transactions]
            for p, txn_obj, dup_count, max_conf in built:
                by_ref[p["ref"]] = {
                    "ref": p["ref"], "status": "created",
                    "txn_guid": _unique_prefix(txn_obj.guid, all_guids),
                    "dup_count": dup_count,
                    "max_confidence": max_conf,
                    **_fill_marker(p),
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
            lines.append(f"{_tsv_cell(ref)}\t{_tsv_cell(message)}")
        return "\n".join(lines)

    @staticmethod
    def _batch_results_to_tsv(rows: list[dict]) -> str:
        """RESULTS table: header + one row per input transaction.
        Blank cells for fields a given status doesn't carry;
        ``dup_count`` of 0 renders as "0", absent renders blank.
        ``max_confidence`` (HIGH/MEDIUM/blank) is the row's top
        duplicate candidate — the shortcut that saves the two-table
        join for the common keep/drop decision; the duplicates
        table remains for the real one."""
        header = (
            "ref\tstatus\ttxn_guid\tdup_count\tmax_confidence\treason"
        )
        lines = [header]
        for r in rows:
            dup = r["dup_count"] if "dup_count" in r else ""
            lines.append(
                f"{r['ref']}\t{r['status']}\t{r.get('txn_guid', '')}\t"
                f"{dup}\t{r.get('max_confidence', '')}\t"
                f"{r.get('reason', '')}"
            )
        return "\n".join(lines)

    @staticmethod
    def _cats_str(cats: list[tuple[str, str]]) -> str:
        """Render category legs as ``account=amount`` pipe-joined.
        The mini-grammar's own separators are escaped inside
        account names so an account containing ``|`` or ``=``
        can't corrupt the cell or flip split_match's reading."""
        def esc(a: str) -> str:
            return (
                a.replace("\\", "\\\\")
                .replace("=", "\\=")
                .replace("|", "\\|")
            )
        return "|".join(f"{esc(a)}={v}" for a, v in sorted(cats))

    @staticmethod
    def _batch_duplicates_to_tsv(dup_rows: list) -> str:
        """DUPLICATES table in the shared ruling-9 comparison shape:
        proposed + existing values AND deltas, category legs,
        split_match — the caller never joins back to its own input.
        Empty string when no row has a match. ``amt_delta`` renders
        blank on cross-currency candidates (the ``cur`` column
        labels them): 188 HKD − 188 CNY is not a number."""
        rows = []
        for ref, d, prop in dup_rows:
            date_old = date.fromisoformat(d["date"])
            cross_frame = not d.get("same_frame", True)
            rows.append({
                "ref": ref,
                "candidate_guid": d["guid"],
                "confidence": d["confidence"],
                "state": d.get("state", ""),
                "date_new": prop["date"].isoformat(),
                "date_old": d["date"],
                "date_delta_days": (date_old - prop["date"]).days,
                "amt_new": str(prop["amount"]),
                "amt_old": d.get("primary_signed", d["amount"]),
                "amt_delta": (
                    "" if cross_frame
                    else str(
                        _to_decimal(
                            d.get("primary_signed", d["amount"])
                        )
                        - prop["amount"]
                    )
                ),
                "cur": (
                    d.get("currency_code", "") if cross_frame
                    else ""
                ),
                "desc_new": prop["desc"],
                "desc_old": d["description"],
                "notes_old": d.get("notes", ""),
                "cat_new": CoreMixin._cats_str(prop["cats"]),
                "cat_old": CoreMixin._cats_str(
                    d.get("categories", [])
                ),
                "split_match": _split_match_verdict(
                    prop["cats"], d.get("categories", []),
                ),
                "signals": d["signals"],
            })
        return _candidate_comparison_tsv(rows)

    # ── Statement entry ────────────────────────────────────────────

    # Statement-native sign transform, keyed by GNCAccountType (never
    # by name — i18n invariant). Asset-class statements print amounts
    # in the split convention already; liability-class statements
    # print charges positive and balances as amount-owed, so both
    # negate. The model transcribes verbatim; the server flips.
    _STATEMENT_SIGNS = {
        "BANK": 1, "CASH": 1, "ASSET": 1,
        "CREDIT": -1, "LIABILITY": -1,
    }

    def enter_statement(
        self,
        account_name: str,
        statement_date: date,
        opening_balance: str,
        closing_balance: str,
        lines: list[dict],
        dry_run: bool = True,
        force_base: bool = False,
        force_duplicates: bool = False,
        show_all: bool = False,
    ) -> dict:
        """One-shot statement entry: enter, claim, and reconcile a
        complete bank/card statement in one atomic open/save.

        Spec: specs/v1.5/ENTER_STATEMENT_SPEC.md. Each line dict is
        ``{ref, date (date), amount (statement-native string),
        description?, notes?, raw?, match?, splits}``.

        ``dry_run=True`` (the default) classifies every line —
        NEW / MATCH / OVERLAP / AMBIGUOUS — against the account's
        existing splits and projects the balance tie; nothing is
        written. ``dry_run=False`` creates NEW rows, claims ``match``
        rows, reconciles every statement-touched split at
        ``statement_date``, and saves once — or refuses wholesale.

        The two force flags are INDEPENDENT safeties (maintainer
        ruling after the round-two review found one flag coaching
        itself into the exact scenario its other half guards):
        ``force_base=True`` clears the opening-balance precondition
        and downgrades the consequent closing-tie failure to a
        recorded discrepancy; ``force_duplicates=True`` disables
        the exact-twin guard on create rows. Forcing the base
        never silently disables duplicate detection, and vice
        versa.
        """
        if not lines:
            raise ValueError(
                "statement has no lines — for a no-activity "
                "statement, reconcile_account covers the balance "
                "tie on its own"
            )
        refs = [ln["ref"] for ln in lines]
        if len(set(refs)) != len(refs):
            raise ValueError(
                "duplicate ref in statement — each line needs a "
                "unique ref"
            )
        opening = _to_decimal(opening_balance)
        closing = _to_decimal(closing_balance)

        with self.open(readonly=dry_run) as book:
            default_currency = self._require_default_currency(book)
            account = self._resolve_account(book, account_name)
            if not account:
                raise self._account_not_found_error(book, account_name)
            sign = self._STATEMENT_SIGNS.get(account.type)
            if sign is None:
                raise ValueError(
                    f"enter_statement needs a balance-carrying "
                    f"statement account (BANK, CASH, ASSET, CREDIT, "
                    f"or LIABILITY); '{account.fullname}' is "
                    f"{account.type}"
                )
            if account.commodity != default_currency:
                raise ValueError(
                    f"'{account.fullname}' is denominated in "
                    f"{account.commodity.mnemonic}, not the book "
                    f"default {default_currency.mnemonic} — "
                    f"foreign-currency statements are a planned "
                    f"follow-up; enter this one via "
                    f"create_transactions + reconcile_account"
                )
            quantum = _commodity_quantum(account.commodity)

            amounts: dict[str, Decimal] = {}
            for ln in lines:
                try:
                    amt = _to_decimal(ln["amount"])
                except ValueError:
                    raise ValueError(
                        f"line {ln['ref']}: amount "
                        f"{ln['amount']!r} is not a decimal"
                    )
                # Sub-quantum precision is a transcription error,
                # not a rounding job — and rounding here would let
                # the self-check gate and the tie compute different
                # sums for the same statement.
                if amt != amt.quantize(quantum):
                    raise ValueError(
                        f"line {ln['ref']}: amount {ln['amount']} "
                        f"carries finer precision than "
                        f"{account.commodity.mnemonic} — re-check "
                        f"the transcription"
                    )
                amounts[ln["ref"]] = amt
            for label, bal in (
                ("opening_balance", opening),
                ("closing_balance", closing),
            ):
                if bal != bal.quantize(quantum):
                    raise ValueError(
                        f"{label} {bal} carries finer precision "
                        f"than {account.commodity.mnemonic} — "
                        f"re-check the transcription"
                    )

            # Self-consistency gate — statement-native signs, before
            # any transform: the statement must not contradict itself.
            line_sum = sum(amounts.values(), Decimal("0"))
            if (opening + line_sum).quantize(quantum) != \
                    closing.quantize(quantum):
                raise ValueError(
                    f"statement does not self-check: opening "
                    f"{opening} + line sum {line_sum} = "
                    f"{opening + line_sum}, but closing is {closing} "
                    f"(difference "
                    f"{closing - (opening + line_sum)}). A missed or "
                    f"doubled line, or a sign slip — re-check the "
                    f"transcription before trusting any "
                    f"classification."
                )

            # Opening precondition: the base the statement lands on.
            reconciled_balance = Decimal("0")
            for s in account.splits:
                if s.reconcile_state == "y":
                    reconciled_balance += s.quantity
            opening_book = (sign * opening).quantize(quantum)
            opening_gap = (
                reconciled_balance.quantize(quantum) - opening_book
            )
            warn_rows: list[tuple[str, str]] = []
            if opening_gap != 0:
                gap_msg = (
                    f"account's reconciled balance "
                    f"{reconciled_balance} does not tie to the "
                    f"statement's opening balance ({opening} as "
                    f"printed → {opening_book} in book convention); "
                    f"difference {opening_gap}. A prior statement "
                    f"may be unentered."
                )
                if dry_run:
                    warn_rows.append(("*", gap_msg))
                elif not force_base:
                    raise ValueError(
                        gap_msg + " Commit refuses on an untied "
                        "base — enter the prior statement first, "
                        "or pass force_base=true to land this one "
                        "anyway (the resulting reconciled state is "
                        "only as good as the base; duplicate "
                        "detection stays on)."
                    )

            split_prefixes = self._split_prefix_map(book)

            # Warm this account's transaction rows in ONE indexed
            # query before the universe filter touches
            # s.transaction.post_date per split (release-review
            # finding 8). The strong reference is load-bearing —
            # see the helper.
            _txn_keepalive = (  # noqa: F841 — keepalive, see helper
                self._preload_account_transactions(book, account)
            )

            # Candidate universe: this account's splits within the
            # match window of any line. Ruling 1: window 31 days —
            # one day wider than the duplicate screen, so a monthly
            # pattern (June 30 "July rent" vs a July 31 line)
            # SURFACES for judgment. Candidate noise is the feature.
            window = timedelta(days=31)
            lo = min(ln["date"] for ln in lines) - window
            hi = max(ln["date"] for ln in lines) + window
            acct_splits = []
            for s in account.splits:
                pd = s.transaction.post_date
                if pd is None or _is_voided(s):
                    continue
                if lo <= pd <= hi:
                    acct_splits.append(s)

            def _book_amount(ln) -> Decimal:
                return (sign * amounts[ln["ref"]]).quantize(quantum)

            def _candidates_for(ln) -> list[dict]:
                """DAD-style scoring against the account's own
                splits. A candidate needs the amount signal alone
                (the universe is narrow enough that an amount match
                is meaningful — and the rent case has ONLY that), or
                desc+date without amount (the fix-the-book-typo
                case)."""
                target = _book_amount(ln)
                probe = (
                    ln.get("description") or ln.get("raw") or ""
                ).lower().strip()
                cands = []
                for s in acct_splits:
                    pd = s.transaction.post_date
                    if abs((pd - ln["date"]).days) > 31:
                        continue
                    amount_match = (
                        abs(s.quantity - target)
                        <= _MATCH_AMOUNT_TOLERANCE
                    )
                    date_match = (
                        abs((pd - ln["date"]).days)
                        <= _MATCH_DATE_TIGHT_DAYS
                    )
                    tdesc = (
                        s.transaction.description or ""
                    ).lower().strip()
                    desc_match = (
                        bool(probe) and bool(tdesc)
                        and (probe in tdesc or tdesc in probe)
                    )
                    if not (
                        amount_match or (desc_match and date_match)
                    ):
                        continue
                    cands.append({
                        "split": s,
                        "signals": (
                            ("D" if desc_match else "-")
                            + ("A" if amount_match else "-")
                            + ("D" if date_match else "-")
                        ),
                        # Exact = the same event, not the monthly
                        # pattern: amount to the quantum AND date
                        # within the tight window. Drives the
                        # OVERLAP class and the commit guard.
                        "exact": (
                            date_match
                            and s.quantity.quantize(quantum)
                            == target
                        ),
                        # Recurring signature: same name, same
                        # amount, a month away — evidence for the
                        # annotation-adaptation case, never a
                        # class driver.
                        "recurring": (
                            desc_match and amount_match
                            and abs((pd - ln["date"]).days)
                            >= _RECURRENCE_MIN_DAYS
                        ),
                    })
                return cands

            if dry_run:
                return self._statement_dry_run(
                    book, account, lines, amounts, sign, quantum,
                    _book_amount, _candidates_for, split_prefixes,
                    reconciled_balance, closing, warn_rows,
                    show_all, force_duplicates,
                )
            return self._statement_commit(
                book, account, lines, amounts, sign, quantum,
                statement_date, _book_amount, _candidates_for,
                split_prefixes, reconciled_balance, closing,
                force_base, force_duplicates, default_currency,
            )

    def _statement_prep_create(
        self, book, account, ln, book_amount, default_currency,
        sweep_fn=None,
    ) -> tuple[list[dict], dict | None]:
        """Validate one would-be-created statement line into resolved
        splits (bank leg synthesized first, counter legs from the
        row or a 2-split auto-fill precedent). Returns
        ``(validated, auto_fill_source | None)``; raises ValueError
        with the row-level problem. ``sweep_fn``, when given, is a
        zero-arg memoized ``_signal_sweep`` supplier — splitless
        lines across one statement then share a single table
        traversal."""
        if not (ln.get("description") or ln.get("raw")):
            raise ValueError(
                "a created line needs a description (or at least "
                "a raw cell to fall back on)"
            )
        bank: dict = {
            "account": account.fullname, "amount": str(book_amount),
        }
        if ln.get("raw"):
            bank["memo"] = ln["raw"]

        counters = ln.get("splits") or []
        src = None
        if not counters:
            probe = ln.get("description") or ln.get("raw") or ""
            sig = self._collect_create_signals(
                book, probe, ln["date"], [],
                want_auto_fill=True, want_stability=False,
                want_duplicates=False, want_recent=False,
                sweep=sweep_fn() if sweep_fn is not None else None,
            )
            if sig.auto_fill is None:
                raise ValueError(
                    "no matching transaction to auto-fill from — "
                    "supply explicit counter-splits"
                )
            filled, src = sig.auto_fill
            if len(filled) != 2:
                raise ValueError(
                    f"auto-fill precedent {src['guid']} has "
                    f"{len(filled)} splits — statement auto-fill "
                    f"only adapts 2-split precedents to the line "
                    f"amount; supply explicit counter-splits"
                )
            # The precedent must have a leg ON the statement account
            # — that's what makes "the other leg is the counter"
            # well-defined. A description match paid from a
            # DIFFERENT account would otherwise pick an arbitrary
            # leg (split iteration order) and could fabricate an
            # inter-bank transfer instead of the expense (review
            # finding).
            on_account = [
                f for f in filled
                if f["account"] == account.fullname
            ]
            if len(on_account) != 1:
                raise ValueError(
                    f"auto-fill precedent {src['guid']} doesn't "
                    f"touch this statement account — supply "
                    f"explicit counter-splits"
                )
            counter = next(
                f for f in filled
                if f["account"] != account.fullname
            )
            if "quantity" in counter:
                raise ValueError(
                    f"auto-fill precedent {src['guid']} has a "
                    f"cross-commodity leg — supply explicit "
                    f"counter-splits"
                )
            counters = [{
                "account": counter["account"],
                "amount": str(-book_amount),
                **(
                    {"memo": counter["memo"]}
                    if counter.get("memo") else {}
                ),
            }]

        validated = self._validate_transaction_splits(
            book, [bank] + counters, default_currency,
        )
        for v in validated:
            if v["account"].placeholder:
                raise self._placeholder_error(v["account"])
        # The statement account's leg is SYNTHESIZED from the line
        # amount; a counter-split resolving back to it would move
        # the account by more than the printed line while the tie
        # — an identity over the inputs — still reports success,
        # and the stray unreconciled split breaks NEXT month's
        # opening gate (review finding).
        for v in validated[1:]:
            if v["account"].guid == account.guid:
                raise ValueError(
                    f"counter-splits must not name the statement "
                    f"account ('{account.fullname}') — its leg is "
                    f"synthesized from the line amount"
                )
        return validated, src

    def _statement_resolve_claim(
        self, book, account, ln, book_amount, quantum,
    ):
        """Resolve and vet one ``match`` cell. Returns
        ``(split, "claim" | "overlap")`` — overlap means the split
        is already reconciled and the row is an idempotent no-op.
        Raises ValueError on any claim that would lie."""
        if ln.get("splits"):
            raise ValueError(
                "a match row claims an existing split — it cannot "
                "also carry counter-splits"
            )
        s = self._find_split(book, ln["match"])
        if s is None:
            raise ValueError(f"match split not found: {ln['match']}")
        if s.account.guid != account.guid:
            raise ValueError(
                f"match split {ln['match']} is on "
                f"'{s.account.fullname}', not the statement account"
            )
        if _is_voided(s):
            raise ValueError(
                f"match split {ln['match']} is voided — voided "
                f"splits cannot be claimed; unvoid_transaction "
                f"first"
            )
        # Exactness has no reconciled-split exemption: a wrong-GUID
        # paste naming a reconciled split of a different amount must
        # diagnose at the row, not silently no-op and surface as a
        # generic tie discrepancy (review finding).
        if s.quantity.quantize(quantum) != book_amount:
            raise ValueError(
                f"match split {ln['match']} has amount "
                f"{s.quantity}, but the line says {ln['amount']} "
                f"as printed ({book_amount} in book convention) — "
                f"wrong split, or fix the book entry first "
                f"(update_transactions), then claim it"
            )
        if s.reconcile_state == "y":
            return s, "overlap"
        return s, "claim"

    def _statement_dry_run(
        self, book, account, lines, amounts, sign, quantum,
        _book_amount, _candidates_for, split_prefixes,
        reconciled_balance, closing, warn_rows, show_all,
        force_duplicates,
    ) -> dict:
        """The rehearsal: run the SAME disposition resolution commit
        runs (the chokepoint), classify every line as evidence, and
        project the balance tie from the resolved dispositions —
        never from the classification (the adversarial round's
        root-cause finding: an approximated projection diverged
        from the landing in both directions)."""
        default_currency = self._require_default_currency(book)
        phase_a = self._statement_dispositions(
            book, account, lines, _book_amount, _candidates_for,
            quantum, default_currency, force_duplicates,
            split_prefixes,
        )
        by_ref = phase_a["by_ref"]
        for ref, msg in phase_a["errors"]:
            warn_rows.append((ref, msg))
        n_refuse = len(phase_a["errors"]) + len(phase_a["guards"])

        counts = {"NEW": 0, "MATCH": 0, "OVERLAP": 0, "AMBIGUOUS": 0}
        line_rows: list[tuple] = []
        cand_rows: list[dict] = []
        projected = reconciled_balance.quantize(quantum)

        for ln in lines:
            cands = _candidates_for(ln)
            unrec = [
                c for c in cands if _is_unreconciled(c["split"])
            ]
            rec = [
                c for c in cands
                if c["split"].reconcile_state == "y"
            ]
            # MATCH/AMBIGUOUS demand adjudication, so the class is
            # gated on MEDIUM+ correspondence (>=2 signals) that
            # is NOT the recurring-payment signature. Weak and
            # recurring candidates still SURFACE as evidence —
            # ruling 1's superset — but classify NEW: three weak
            # lookalikes must not adopt a genuinely new line, and
            # last month's rent is a precedent, not a twin
            # (bookkeeper findings, maiden flight + T6).
            strong = [
                c for c in unrec
                if sum(1 for ch in c["signals"] if ch != "-") >= 2
                and not c["recurring"]
            ]
            if strong:
                cls = "MATCH" if len(strong) == 1 else "AMBIGUOUS"
                listed = unrec + rec
            elif any(c["exact"] for c in rec):
                # OVERLAP means THE SAME EVENT already landed and
                # tied — exact amount, tight date; fuzzier
                # reconciled candidates are evidence for a NEW
                # line, not an overlap.
                cls = "OVERLAP"
                listed = rec
            else:
                cls = "NEW"
                # Weak (LOW) and reconciled fuzzy candidates stay
                # listed: the spec's annotation-adaptation case
                # needs the prior instance's annotation shipped as
                # evidence even when it doesn't drive the class.
                listed = unrec + rec

            # Note, class overrides, and projection all come from
            # the RESOLVED DISPOSITION — what commit will actually
            # do with this row — not from the evidence class.
            d = by_ref[ln["ref"]]
            note = ""
            proposal_cats = None
            if d["kind"] == "claim":
                cls = "MATCH"
                note = (
                    f"will claim "
                    f"{split_prefixes[d['split'].guid]}"
                )
                projected += _book_amount(ln)
            elif d["kind"] == "overlap":
                cls = "OVERLAP"
                note = "match names a reconciled split — no-op"
            elif d["kind"] == "guard":
                # The guard's coaching verbatim — the same text the
                # commit rejection would carry.
                note = d["message"]
                if not d["twin_reconciled"]:
                    # Resolution (claim, or forced create) lands
                    # the amount either way; a reconciled twin
                    # resolves to a no-op.
                    projected += _book_amount(ln)
            elif d["kind"] == "create":
                validated = d["validated"]
                proposal_cats = [
                    (v["account"].fullname, str(v["value"]))
                    for v in validated
                    if v["account"].guid != account.guid
                ]
                if cls == "NEW" and not ln.get("splits"):
                    counter_names = ", ".join(
                        v["account"].fullname
                        for v in validated[1:]
                    )
                    note = (
                        f"would create {_book_amount(ln)} → "
                        f"{counter_names}"
                    )
                    if d["src"]:
                        note += (
                            f" (auto_filled_from:"
                            f"{d['src']['guid']})"
                        )
                projected += _book_amount(ln)
            else:  # error — assume the operator fixes the row and
                # it lands; the caveat counts it either way.
                projected += _book_amount(ln)

            counts[cls] += 1

            # Best-evidence-only display (bookkeeper ruling,
            # 2026-08-24): a line with MEDIUM/HIGH candidates
            # suppresses its LOW amount-coincidences — redundant
            # next to the real evidence, and dense-recurrence
            # cards make the token weight real (19 LOWs on one
            # $4.99 line). A line whose ONLY evidence is LOW keeps
            # it: ruling 1's rent case is load-bearing. The
            # suppression leaves a breadcrumb in the cands column;
            # show_all=true is the escape hatch.
            kept = listed
            n_suppressed = 0
            if not show_all:
                strong_listed = [
                    c for c in listed
                    if sum(1 for ch in c["signals"] if ch != "-")
                    >= 2
                ]
                if strong_listed and len(strong_listed) < len(listed):
                    n_suppressed = len(listed) - len(strong_listed)
                    kept = strong_listed
            cands_cell = str(len(kept))
            if n_suppressed:
                cands_cell += f" (+{n_suppressed} LOW suppressed)"

            for c in kept:
                s = c["split"]
                txn = s.transaction
                cat_old = [
                    (s2.account.fullname, str(s2.value))
                    for s2 in txn.splits
                    if s2.account.guid != account.guid
                ]
                risk = sum(1 for ch in c["signals"] if ch != "-")
                cand_rows.append({
                    "ref": ln["ref"],
                    "candidate_guid": split_prefixes[s.guid],
                    "confidence": {
                        3: "HIGH", 2: "MEDIUM", 1: "LOW",
                    }.get(risk, ""),
                    "state": s.reconcile_state,
                    "date_new": ln["date"].isoformat(),
                    "date_old": txn.post_date.isoformat(),
                    "date_delta_days": (
                        txn.post_date - ln["date"]
                    ).days,
                    "amt_new": str(_book_amount(ln)),
                    "amt_old": str(s.quantity),
                    "amt_delta": str(
                        s.quantity - _book_amount(ln)
                    ),
                    "desc_new": (
                        ln.get("description") or ln.get("raw") or ""
                    ),
                    "desc_old": txn.description or "",
                    "notes_old": txn.notes or "",
                    "memo_old": s.memo or "",
                    "cat_new": (
                        self._cats_str(proposal_cats)
                        if proposal_cats is not None else ""
                    ),
                    "cat_old": self._cats_str(cat_old),
                    "split_match": _split_match_verdict(
                        proposal_cats, cat_old,
                    ),
                    "signals": c["signals"],
                })
            line_rows.append((ln["ref"], cls, cands_cell, note))

        # Summary header via the shared rehearsal renderer — counts
        # are facts and may headline; clearance verdicts over
        # unadjudicated rows may not (the clearance principle,
        # spec §4).
        n_judge = counts["MATCH"] + counts["AMBIGUOUS"]
        if n_judge:
            homework = (
                f"{n_judge} rows need adjudication — rule each "
                f"MATCH/AMBIGUOUS against the candidates table "
                f"before committing."
            )
        elif cand_rows:
            # Verified-empty phrasing must not contradict the
            # visible table (bookkeeper copy quibble + round-two
            # R6: a recurring candidate labels MEDIUM in the
            # confidence column, so "no MEDIUM+ candidates" argued
            # with its own table). State what is true: no row
            # needs adjudication; the listed rows are evidence.
            homework = (
                "No rows need adjudication — the listed candidates "
                "are evidence only (recurring patterns or "
                "already-reconciled)."
            )
        else:
            homework = "No existing-split candidates on any line."
        header = _dry_run_summary(
            len(lines), "lines",
            [(c, counts[c]) for c in
             ("NEW", "MATCH", "OVERLAP", "AMBIGUOUS")],
            homework,
        )

        closing_book = (sign * closing).quantize(quantum)
        projected = projected.quantize(quantum)
        if projected == closing_book:
            tie = (
                f"Projected reconciled balance after commit: "
                f"{projected} — ties to the statement closing "
                f"({closing} as printed)."
            )
        else:
            tie = (
                f"Projected reconciled balance after commit: "
                f"{projected} vs statement closing {closing_book} "
                f"({closing} as printed) — DISCREPANCY "
                f"{closing_book - projected}."
            )
        if n_refuse:
            # Counts the rows the SAME payload would refuse at
            # commit — resolved dispositions, not a warning-count
            # proxy (the old proxy was wrong in both directions:
            # guard hits raised no warning, and under force the
            # opening-gap warning implied a refusal that wouldn't
            # happen). Facts, not clearance.
            tie += (
                f" {n_refuse} row(s) this payload would refuse at "
                f"commit — resolve the notes/warnings first."
            )

        lines_tsv = ["ref\tclass\tcands\tnote"]
        for r in line_rows:
            lines_tsv.append(
                f"{_tsv_cell(r[0])}\t{r[1]}\t{r[2]}\t"
                f"{_tsv_cell(r[3])}"
            )

        return {
            "summary": header,
            "lines": "\n".join(lines_tsv),
            "candidates": _candidate_comparison_tsv(cand_rows),
            "warnings": self._batch_warnings_to_tsv(warn_rows),
            "tie": tie,
        }

    def _statement_dispositions(
        self, book, account, lines, _book_amount, _candidates_for,
        quantum, default_currency, force_duplicates, split_prefixes,
    ) -> dict:
        """Phase A — THE disposition chokepoint both modes run.

        The adversarial round found the rehearsal approximating this
        procedure and drifting from it in five distinct ways; the
        fix is structural: one resolver, two consumers, divergence
        impossible by construction. Claims resolve first (the
        guard's exemption set), then each create row runs the twin
        guard BEFORE counter-split/auto-fill prep (bookkeeper
        signoff, carried item). ``force_duplicates`` disables the
        guard here — for BOTH modes, so a forced rehearsal
        rehearses the forced landing.

        Returns ``{"by_ref": {ref: {"kind": claim|overlap|create|
        guard|error, ...}}, "claims": [(ln, split)], "skipped":
        [(ln, split)], "prepared": [(ln, validated, src)],
        "errors": [(ref, msg)], "guards": [(ref, msg,
        twin_reconciled)]}``. Pure read — no mutation on any path.
        """
        by_ref: dict[str, dict] = {}
        prepared: list[tuple] = []
        claims: list[tuple] = []
        skipped: list[tuple] = []
        errors: list[tuple[str, str]] = []
        guards: list[tuple[str, str, bool]] = []
        claimed_guids: set[str] = set()
        overlap_guids: set[str] = set()

        # Shared table sweep for the statement's splitless create
        # rows — dispositions are a pure read (no mutation on any
        # path), so every auto-fill preflight screens the same
        # pre-write table state. Lazy: all-claims and all-explicit
        # statements never pay for it.
        _sweep_memo: list = []

        def _sweep():
            if not _sweep_memo:
                _sweep_memo.append(self._signal_sweep(book))
            return _sweep_memo[0]

        create_rows = []
        for ln in lines:
            if not ln.get("match"):
                create_rows.append(ln)
                continue
            try:
                s, kind = self._statement_resolve_claim(
                    book, account, ln, _book_amount(ln), quantum,
                )
                if s.guid in claimed_guids or (
                    kind == "overlap" and s.guid in overlap_guids
                ):
                    # One split, one row — reconciled no-ops
                    # included: two lines both no-opping one split
                    # would report two handled lines for one
                    # (review finding).
                    raise ValueError(
                        "split already used by another row in "
                        "this statement"
                    )
                if kind == "overlap":
                    overlap_guids.add(s.guid)
                    skipped.append((ln, s))
                    by_ref[ln["ref"]] = {"kind": "overlap", "split": s}
                else:
                    claimed_guids.add(s.guid)
                    claims.append((ln, s))
                    by_ref[ln["ref"]] = {"kind": "claim", "split": s}
            except ValueError as e:
                errors.append((ln["ref"], str(e)))
                by_ref[ln["ref"]] = {"kind": "error", "message": str(e)}

        def _twin_guard(ln) -> tuple[str, bool] | None:
            """Statement-duplicate guard: a created line that
            exactly matches an unclaimed split on this account
            would double-enter. Unreconciled twin: the tie would
            still hold (only the new split reconciles) — silent.
            Reconciled twin: already landed via a prior statement.
            Splits claimed OR no-op'd by other rows in this
            statement are exempt — their lines are accounted for,
            so an exact-matching create is a genuine second
            charge."""
            for c in _candidates_for(ln):
                s = c["split"]
                if (
                    s.guid in claimed_guids
                    or s.guid in overlap_guids
                    or not c["exact"]
                ):
                    continue
                if _is_unreconciled(s):
                    return (
                        f"unreconciled split "
                        f"{split_prefixes[s.guid]} on this account "
                        f"matches this line exactly (amount + "
                        f"date) and no row claims it — claim it "
                        f"with match={split_prefixes[s.guid]}, or "
                        f"force_duplicates=true to create "
                        f"anyway"
                    ), False
                return (
                    f"reconciled split {split_prefixes[s.guid]} "
                    f"matches this line exactly — it looks landed "
                    f"by a prior statement; keep the line as a "
                    f"match row (match={split_prefixes[s.guid]}, "
                    f"a no-op skip), or force_duplicates=true "
                    f"to create anyway"
                ), True
            return None

        for ln in create_rows:
            if not force_duplicates:
                hit = _twin_guard(ln)
                if hit is not None:
                    msg, twin_reconciled = hit
                    guards.append((ln["ref"], msg, twin_reconciled))
                    by_ref[ln["ref"]] = {
                        "kind": "guard", "message": msg,
                        "twin_reconciled": twin_reconciled,
                    }
                    continue
            try:
                validated, src = self._statement_prep_create(
                    book, account, ln, _book_amount(ln),
                    default_currency, sweep_fn=_sweep,
                )
                prepared.append((ln, validated, src))
                by_ref[ln["ref"]] = {
                    "kind": "create", "validated": validated,
                    "src": src,
                }
            except ValueError as e:
                errors.append((ln["ref"], str(e)))
                by_ref[ln["ref"]] = {"kind": "error", "message": str(e)}

        return {
            "by_ref": by_ref, "claims": claims, "skipped": skipped,
            "prepared": prepared, "errors": errors, "guards": guards,
        }

    def _statement_commit(
        self, book, account, lines, amounts, sign, quantum,
        statement_date, _book_amount, _candidates_for,
        split_prefixes, reconciled_balance, closing, force_base,
        force_duplicates, default_currency,
    ) -> dict:
        """The landing: resolve dispositions (the shared chokepoint),
        check the tie, then — and only then — mutate and save once."""
        phase_a = self._statement_dispositions(
            book, account, lines, _book_amount, _candidates_for,
            quantum, default_currency, force_duplicates,
            split_prefixes,
        )
        prepared = phase_a["prepared"]
        claims = phase_a["claims"]
        skipped = phase_a["skipped"]
        # Guard hits are row errors at commit time.
        errors = phase_a["errors"] + [
            (ref, msg) for ref, msg, _rec in phase_a["guards"]
        ]

        if errors:
            # The row's error rides the note column INLINE — the
            # results table is the primary read, and a rejection
            # whose coaching ("claim it with match=…, or force")
            # lives elsewhere strands the operator at exactly the
            # moment they need the next move (bookkeeper T5b).
            error_by_ref = {}
            for ref, msg in errors:
                error_by_ref.setdefault(ref, msg)
            rows = []
            for ln in lines:
                msg = error_by_ref.get(ln["ref"], "")
                status = "rejected" if msg else "statement_aborted"
                rows.append(
                    f"{_tsv_cell(ln['ref'])}\t{status}\t\t"
                    f"{_tsv_cell(msg)}"
                )
            return {
                "summary": (
                    f"Statement REJECTED — {len(error_by_ref)} row "
                    f"error(s); nothing was written."
                ),
                "results": "ref\tstatus\tguid\tnote\n"
                + "\n".join(rows),
            }

        # The tie, BEFORE any mutation. Claim amounts equal line
        # amounts by the exactness rule, so both dispositions
        # contribute the transformed line amount.
        new_reconciled = reconciled_balance
        for ln, _s in claims:
            new_reconciled += _book_amount(ln)
        for ln, _v, _src in prepared:
            new_reconciled += _book_amount(ln)
        new_reconciled = new_reconciled.quantize(quantum)
        closing_book = (sign * closing).quantize(quantum)
        n_touched = len(prepared) + len(claims)
        cur = account.commodity.mnemonic
        if new_reconciled == closing_book:
            tie = (
                f"Reconciled: {n_touched} splits @ "
                f"{statement_date.isoformat()}; closing balance "
                f"{cur} {new_reconciled} ({closing} as printed) — "
                f"tied."
            )
        elif force_base:
            # Only a forced base can produce a discrepancy — the
            # self-check and opening gates make the unforced tie
            # an identity, and forced duplicates still contribute
            # their own line amounts.
            tie = (
                f"DISCREPANCY {closing_book - new_reconciled}: "
                f"reconciled balance {new_reconciled} vs statement "
                f"closing {closing_book} ({closing} as printed) — "
                f"landed under force_base"
            )
        else:
            raise ValueError(
                f"balance tie failed: the reconciled balance would "
                f"be {new_reconciled}, but the statement closes at "
                f"{closing_book} ({closing} as printed); difference "
                f"{closing_book - new_reconciled}. Nothing was "
                f"written."
            )

        # Audit before-state: the claimed splits' prior annotations
        # and states, for the ENTER formatter's diffs.
        self._stage_audit_before({
            "account": account.fullname,
            "claims": [
                {
                    "guid": s.guid,
                    "state": s.reconcile_state,
                    "memo": s.memo or "",
                    "notes": s.transaction.notes or "",
                    "description": s.transaction.description or "",
                    "date": (
                        s.transaction.post_date.isoformat()
                        if s.transaction.post_date else ""
                    ),
                }
                for _ln, s in claims
            ],
        })

        # Mutate: claims first (annotations + state), then builds.
        rec_dt = datetime.combine(
            statement_date, datetime.min.time()
        )
        for ln, s in claims:
            if ln.get("raw"):
                s.memo = ln["raw"]
            if ln.get("notes"):
                s.transaction.notes = ln["notes"]
            s.reconcile_state = "y"
            s.reconcile_date = rec_dt

        built = []
        for ln, validated, src in prepared:
            piecash_splits = [
                piecash.Split(
                    account=v["account"], value=v["value"],
                    quantity=v["quantity"], memo=v["memo"] or "",
                    action=v["action"] or "",
                )
                for v in validated
            ]
            txn_obj = piecash.Transaction(
                currency=default_currency,
                description=(
                    ln.get("description") or ln.get("raw") or ""
                ),
                notes=ln.get("notes") or None,
                post_date=ln["date"],
                splits=piecash_splits,
            )
            # The bank leg is validated[0] by construction; it is
            # part of the statement, so it lands reconciled.
            piecash_splits[0].reconcile_state = "y"
            piecash_splits[0].reconcile_date = rec_dt
            built.append((ln, txn_obj, src))

        book.save()

        # Post-write verification (round-two finding: the pre-save
        # tie is an arithmetic identity over the operator's own
        # inputs). Read the reconciled balance BACK from the saved
        # splits; a mismatch means the write did not land as
        # computed and must surface loudly, never as a clean
        # summary. Same doctrine as _verify_write for raw SQL.
        readback = Decimal("0")
        for s in account.splits:
            if s.reconcile_state == "y":
                readback += s.quantity
        if readback.quantize(quantum) != new_reconciled:
            raise ValueError(
                f"post-write verification failed: the account's "
                f"reconciled balance reads {readback}, expected "
                f"{new_reconciled}. The statement WAS saved — "
                f"inspect the account before retrying."
            )

        all_guids = [t.guid for t in book.transactions]
        rows = ["ref\tstatus\tguid\tnote"]
        by_ref: dict[str, str] = {}
        for ln, txn_obj, src in built:
            note = (
                f"auto_filled_from:{src['guid']}" if src else ""
            )
            by_ref[ln["ref"]] = (
                f"{ln['ref']}\tcreated\t"
                f"{_unique_prefix(txn_obj.guid, all_guids)}\t{note}"
            )
        for ln, s in claims:
            by_ref[ln["ref"]] = (
                f"{ln['ref']}\tclaimed\t{split_prefixes[s.guid]}\t"
            )
        for ln, s in skipped:
            by_ref[ln["ref"]] = (
                f"{ln['ref']}\tskipped_duplicate\t"
                f"{split_prefixes[s.guid]}\talready reconciled"
            )
        for ln in lines:
            rows.append(by_ref[ln["ref"]])

        return {
            "summary": (
                f"Statement entered on {account.fullname} through "
                f"{statement_date.isoformat()}: {len(built)} "
                f"created, {len(claims)} claimed, {len(skipped)} "
                f"skipped (already reconciled)."
                + (
                    " LANDED UNDER FORCE ("
                    + ", ".join(
                        n for n, on in (
                            ("base", force_base),
                            ("duplicates", force_duplicates),
                        ) if on
                    )
                    + ") — the named guard(s) were bypassed."
                    if (force_base or force_duplicates) else ""
                )
            ),
            "results": "\n".join(rows),
            "new_reconciled_balance": str(new_reconciled),
            "tie": tie,
        }

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

    def _validate_transaction_deletable(
        self, book, transaction, force: bool,
    ) -> int:
        """Shared delete safeguards; returns the reconciled-split
        count (0 when clean).

        - Refuses an invoice's posting transaction: deleting it
          orphans the invoice's posted-state metadata, after which
          the invoice refuses both delete ("posted") and re-post
          ("already posted") — SQL surgery is the only escape.
          unpost_document clears the metadata properly.
        - Refuses reconciled splits unless ``force``.
        """
        from sqlalchemy import text
        posting_for = book.session.execute(
            text("SELECT id FROM invoices WHERE post_txn = :guid"),
            {"guid": transaction.guid},
        ).fetchone()
        if posting_for:
            raise ValueError(
                f"Transaction is the posting record for invoice "
                f"{posting_for[0]}. Use unpost_document first."
            )

        reconciled = [
            s for s in transaction.splits if s.reconcile_state == "y"
        ]
        if reconciled and not force:
            acct_names = ", ".join(s.account.fullname for s in reconciled)
            raise ValueError(
                f"Transaction has reconciled splits in: {acct_names}. "
                f"Deleting will break reconciliation. Use force=true to override."
            )
        return len(reconciled)

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

            reconciled_count = self._validate_transaction_deletable(
                book, transaction, force,
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
            if reconciled_count:
                result["reconciled_splits_affected"] = reconciled_count

            # Delete the transaction
            book.session.delete(transaction)
            book.save()

            return result

    def delete_transactions(
        self, guids: list[str], force: bool = False,
    ) -> dict:
        """Delete several transactions in one book open / one save.

        All-or-nothing: every guid must resolve and pass the same
        safeguards as ``delete_transaction`` (invoice-posting guard,
        reconciled splits vs ``force``) BEFORE anything is deleted —
        validate-then-mutate, so a bad guid mid-list can't leave a
        half-deleted batch.

        Returns:
            ``{status, count, transactions: [{guid, description,
            reconciled_splits_affected?}]}`` — a dict envelope (not a
            bare list) so the response machinery and audit decorator
            see the same shape every write returns.

        Raises:
            ValueError: empty list, duplicate guid, any guid not
                found, or any safeguard failure — nothing deleted.
        """
        if not guids:
            raise ValueError("guids list is empty")

        with self.open(readonly=False) as book:
            resolved: list[tuple] = []
            seen: set[str] = set()
            for ref in guids:
                transaction = self._find_transaction(book, ref)
                if not transaction:
                    raise ValueError(
                        f"Transaction not found: {ref} (nothing deleted)"
                    )
                if transaction.guid in seen:
                    raise ValueError(
                        f"Duplicate guid in list: {ref} (nothing deleted)"
                    )
                seen.add(transaction.guid)
                try:
                    reconciled_count = self._validate_transaction_deletable(
                        book, transaction, force,
                    )
                except ValueError as e:
                    raise ValueError(f"{e} (nothing deleted)")
                resolved.append((transaction, reconciled_count))

            # Composite before-state — the audit formatter renders
            # one block per deleted transaction from this list.
            self._stage_audit_before({
                "transactions": [
                    _transaction_to_dict(t) for t, _ in resolved
                ],
            })

            # Everything captured pre-delete: prefixes need the rows
            # still present, attributes detach after the session
            # closes.
            all_guids = [t.guid for t in book.transactions]
            items = []
            for transaction, reconciled_count in resolved:
                item = {
                    "guid": _unique_prefix(transaction.guid, all_guids),
                    "description": transaction.description,
                }
                if reconciled_count:
                    item["reconciled_splits_affected"] = reconciled_count
                items.append(item)

            for transaction, _ in resolved:
                book.session.delete(transaction)
            book.save()

            return {
                "status": "deleted",
                "count": len(items),
                "transactions": items,
            }

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

    def _update_transactions_broadcast(
        self,
        guids: list[str],
        description: str | None = None,
        trans_date: date | None = None,
        splits: list[dict] | None = None,
        notes: str | None = None,
        force: bool = False,
    ) -> dict:
        """Apply the same field values to every listed transaction.

        All-or-nothing: every guid resolves and passes the voided
        gate before anything mutates, mirroring batch delete. Splits
        are single-transaction territory — a broadcast split edit
        matched "by account" against N different transactions is a
        semantic minefield, so it rejects loudly.
        """
        if not guids:
            raise ValueError("guid list is empty")
        if splits is not None:
            raise ValueError(
                "splits updates are single-transaction only — pass "
                "one guid, or use replace_splits per transaction"
            )
        if description is None and trans_date is None and notes is None:
            raise ValueError(
                "nothing to update — supply description, "
                "transaction_date, and/or notes"
            )
        with self.open(readonly=False) as book:
            transactions = []
            for g in guids:
                txn = self._find_transaction(book, g)
                if not txn:
                    raise ValueError(f"Transaction not found: {g}")
                if any(_is_voided(sp) for sp in txn.splits):
                    raise ValueError(
                        f"Transaction {g} is voided. Use "
                        f"unvoid_transaction first, then update."
                    )
                if trans_date is not None \
                        and _post_date_as_date(txn) != trans_date:
                    self._require_force_for_reconciled(
                        txn, force, "Moving its posting date",
                    )
                transactions.append(txn)

            self._stage_audit_before({
                "transactions": [
                    _transaction_to_dict(t) for t in transactions
                ],
            })

            for txn in transactions:
                if description is not None:
                    txn.description = description
                if notes is not None:
                    txn.notes = notes if notes else None
                if trans_date is not None:
                    txn.post_date = trans_date

            book.save()

            for txn in transactions:
                self._verify_transaction_state(
                    book, txn,
                    expected_description=description,
                    expected_date=trans_date,
                    expected_notes=notes,
                )

            all_guids = [t.guid for t in book.transactions]
            return {
                "status": "updated",
                "count": len(transactions),
                "transactions": [
                    {
                        "guid": _unique_prefix(t.guid, all_guids),
                        "description": t.description,
                    }
                    for t in transactions
                ],
            }

    def update_transactions(
        self,
        updates: list[dict],
        on_error: str = "abort",
        force: bool = False,
    ) -> dict:
        """Per-row transaction updates in one book-open / one save.

        Each entry: ``{guid, description (optional), notes
        (optional), date (optional, datetime.date)}`` — absent keys
        leave the field unchanged (the TSV's empty cells), while an
        explicit ``""`` clears (produced only by the TSV ``clear``
        column — an opt-in per-row declaration, so a sparse batch
        still can never mass-erase by accident).

        ``on_error="abort"`` (default) sinks the batch on any bad
        row; ``"skip"`` keeps the good rows. ``force`` allows date
        moves on transactions with reconciled splits (rejected per
        row otherwise — same gate as ``update_transaction``).
        Returns ``{"results": TSV}`` keyed by the input guid.
        """
        if on_error not in ("abort", "skip"):
            raise ValueError("on_error must be 'abort' or 'skip'")
        keys = [u["guid"] for u in updates]
        if len(set(keys)) != len(keys):
            raise ValueError(
                "duplicate guid in batch — each transaction may "
                "appear once"
            )

        by_key: dict = {}
        with self.open(readonly=False) as book:
            prepared = []
            for u in updates:
                key = u["guid"]
                try:
                    if not any(
                        f in u for f in ("description", "notes", "date")
                    ):
                        raise ValueError(
                            "row changes nothing — every cell empty"
                        )
                    txn = self._find_transaction(book, key)
                    if not txn:
                        raise ValueError(f"Transaction not found: {key}")
                    if any(_is_voided(sp) for sp in txn.splits):
                        raise ValueError(
                            f"Transaction {key} is voided. Use "
                            f"unvoid_transaction first, then update."
                        )
                    if "date" in u \
                            and _post_date_as_date(txn) != u["date"]:
                        self._require_force_for_reconciled(
                            txn, force, "Moving its posting date",
                        )
                    prepared.append((u, txn))
                except (ValueError, KeyError) as e:
                    by_key[key] = {
                        "guid": key, "status": "rejected",
                        "reason": str(e),
                    }

            if by_key and on_error == "abort":
                for u, _txn in prepared:
                    by_key[u["guid"]] = {
                        "guid": u["guid"], "status": "rejected",
                        "reason": "batch_aborted",
                    }
                return self._updates_envelope(updates, by_key)

            self._stage_audit_before({
                "transactions": [
                    _transaction_to_dict(t) for _u, t in prepared
                ],
            })

            for u, txn in prepared:
                if "description" in u:
                    txn.description = u["description"]
                if "notes" in u:
                    txn.notes = u["notes"] or None
                if "date" in u:
                    txn.post_date = u["date"]

            if prepared:
                book.save()

            for u, txn in prepared:
                self._verify_transaction_state(
                    book, txn,
                    expected_description=u.get("description"),
                    expected_date=u.get("date"),
                    expected_notes=u.get("notes"),
                )
                by_key[u["guid"]] = {
                    "guid": u["guid"], "status": "updated",
                    "description": txn.description,
                }

            return self._updates_envelope(updates, by_key)

    @staticmethod
    def _updates_envelope(updates: list[dict], by_key: dict) -> dict:
        lines = ["guid\tstatus\tdescription\treason"]
        for u in updates:
            r = by_key.get(u["guid"], {})
            lines.append(
                f"{u['guid']}\t{r.get('status', '')}\t"
                f"{r.get('description', '')}\t{r.get('reason', '')}"
            )
        return {"results": "\n".join(lines)}

    @staticmethod
    def _require_force_for_reconciled(
        transaction, force: bool, action: str,
    ) -> None:
        """Raise unless ``force`` when the transaction has reconciled
        ('y') splits — the shared gate for every edit that would
        damage reconciliation (split changes AND posting-date moves;
        a date move relocates the transaction across statement
        periods while its splits stay 'y'). ``action`` names the
        change in the error message."""
        if force:
            return
        reconciled = [
            s for s in transaction.splits if s.reconcile_state == "y"
        ]
        if not reconciled:
            return
        acct_names = ", ".join(
            s.account.fullname for s in reconciled
        )
        raise ValueError(
            f"Transaction has reconciled splits in: {acct_names}. "
            f"{action} will break reconciliation. "
            f"Use force=true to override."
        )

    def update_transaction(
        self,
        guid: str | list[str],
        description: str | None = None,
        trans_date: date | None = None,
        splits: list[dict] | None = None,
        notes: str | None = None,
        force: bool = False,
    ) -> dict:
        """Update an existing transaction — or broadcast one change
        to several.

        Args:
            guid: One GUID, or a LIST of them: the supplied field
                values apply to EVERY listed transaction (one book
                open, one save, all-or-nothing). The batch-annotation
                case — same note on 35 related entries — in one call.
                ``splits`` is single-transaction only.
            description / trans_date / notes: Optional; ``notes=""``
                clears.
            splits: Optional split updates matched to existing splits
                by account; cross-currency splits need 'quantity'.
            force: Allow modifying reconciled splits (only checked
                when splits change).

        Returns:
            Thin dict: {guid, date, description, status}; for a
            list, ``{status, count, transactions: [{guid,
            description}]}``.

        Raises:
            ValueError: not found, voided, imbalance, account not in
                transaction, missing quantity, or reconciled without
                force.
        """
        if isinstance(guid, list):
            return self._update_transactions_broadcast(
                guid, description=description, trans_date=trans_date,
                splits=splits, notes=notes, force=force,
            )
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
                self._require_force_for_reconciled(
                    transaction, force, "Modifying",
                )

            # A date move relocates the transaction across statement
            # / reporting periods while its splits stay 'y' — same
            # reconciliation damage as editing the splits, so the
            # same force gate. Same-date is a no-op and passes.
            if trans_date is not None \
                    and _post_date_as_date(transaction) != trans_date:
                self._require_force_for_reconciled(
                    transaction, force, "Moving its posting date",
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

        A new split that reproduces an existing one (same account,
        value, and quantity) is treated as an UNCHANGED leg: it
        keeps the old split's memo (a caller-supplied memo wins)
        and its reconcile state — recategorizing one leg doesn't
        destroy the other leg's provenance or reconciliation.

        Args:
            splits: Complete new set — 'account', 'amount', optional
                'quantity' (cross-currency) and 'memo'.
            force: Required if the replacement would CHANGE a
                reconciled split, or remove splits from lots.
                Unchanged reconciled legs are preserved and need no
                override.

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
                    raise self._account_not_found_error(
                        book, account_name,
                    )
                if account.placeholder:
                    raise self._placeholder_error(account)
                resolved_accounts.append((account, split_data))

            # 4a. Voided transactions are immutable — same
            # rationale as update_transaction; no force override.
            if any(_is_voided(s) for s in transaction.splits):
                raise ValueError(
                    f"Transaction {guid} is voided. Use "
                    f"unvoid_transaction first, then replace splits."
                )

            # 4. Carry-forward snapshot: a new split that reproduces
            # an old one (same account, value, and quantity) is an
            # UNCHANGED leg — recategorizing one side of a
            # transaction must not destroy the other side's
            # provenance memo or knock it out of reconciliation.
            # Greedy one-to-one claim so same-account same-amount
            # twins each match once.
            carryover = [
                {
                    "account_guid": s.account.guid,
                    "value": s.value,
                    "quantity": s.quantity,
                    "memo": s.memo or "",
                    "action": s.action or "",
                    "state": s.reconcile_state,
                    "rdate": s.reconcile_date,
                    "claimed": False,
                }
                for s in transaction.splits
            ]

            def _new_split_quantity(account, split_data):
                """Quantity a new split would carry, or None when a
                required cross-commodity quantity is absent (step 7
                rejects that row; the pre-pass just skips it)."""
                amount = _to_decimal(split_data["amount"])
                if account.commodity == transaction.currency:
                    return amount
                if "quantity" in split_data:
                    return _to_decimal(split_data["quantity"])
                return None

            def _claim(pool, account_guid, value, quantity):
                for c in pool:
                    if (
                        not c["claimed"]
                        and c["account_guid"] == account_guid
                        and c["value"] == value
                        and c["quantity"] == quantity
                    ):
                        c["claimed"] = True
                        return c
                return None

            # Pre-pair on a scratch copy so the force gate applies
            # only to reconciled legs the replacement would CHANGE —
            # an unchanged reconciled leg is preserved verbatim and
            # needs no override.
            scratch = [dict(c) for c in carryover]
            for account, split_data in resolved_accounts:
                quantity = _new_split_quantity(account, split_data)
                if quantity is not None:
                    _claim(
                        scratch, account.guid,
                        _to_decimal(split_data["amount"]), quantity,
                    )
            reconciled_changed = [
                s for s, c in zip(transaction.splits, scratch)
                if s.reconcile_state == "y" and not c["claimed"]
            ]
            if reconciled_changed and not force:
                names = ", ".join(
                    s.account.fullname for s in reconciled_changed
                )
                raise ValueError(
                    f"Transaction has reconciled splits in: {names} "
                    f"that this replacement would change. "
                    f"Use force=true to override."
                )
            if reconciled_changed:
                names = ", ".join(
                    s.account.fullname for s in reconciled_changed
                )
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
                # Unchanged leg: keep its memo and action (caller-
                # supplied values win) and its reconciliation,
                # verbatim.
                match = _claim(carryover, account.guid, amount, quantity)
                new_split = piecash.Split(
                    account=account,
                    value=amount,
                    quantity=quantity,
                    memo=(
                        split_data.get("memo")
                        or (match["memo"] if match else "")
                    ),
                    action=(
                        split_data.get("action")
                        or (match["action"] if match else "")
                    ),
                    transaction=transaction,
                )
                if match and match["state"] in ("y", "c"):
                    new_split.reconcile_state = match["state"]
                    if match["rdate"] is not None:
                        new_split.reconcile_date = match["rdate"]

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
