"""ReportingMixin — balance sheet, spending/income breakdowns, cash flow, debt payoff.

All methods are read-only (no `book.save()`). The debt_payoff_plan
method reads `apr` / `minimum_payment` / `credit_limit` slots on
CREDIT/LIABILITY accounts via piecash's account[key] shortcut, so
it does not depend on AdminMixin's slot tools.

The five transaction-scanning reports (``spending_by_category``,
``income_by_source``, ``balance_sheet``, ``net_worth``, ``cash_flow``)
push date and account-type filters into indexed SQL via
``_query_filtered_splits``. Aggregation stays in Python so amounts
are summed as exact Decimals (quantity is stored as num/denom integer
pairs that SQLite can't aggregate without lossy float conversion).

The ``net_worth`` time-series uses a single-sweep cumulative sum —
one pass over all relevant splits ordered by post_date, running
total snapshotted at each interval boundary. Reduces a 60-month
series from O(intervals × splits) to O(splits + intervals).

Depends on shared helpers from BaseGnuCashBook:
  - self.open, self._find_account
"""

from datetime import date
from decimal import Decimal, InvalidOperation

import piecash

from gnucash_mcp.book._base import _to_decimal

# Account-type groups used across the reports. Defined at module level
# so the SQL IN() clauses share a single canonical definition rather
# than drifting across methods.
_ASSET_TYPES = frozenset({"ASSET", "BANK", "CASH", "STOCK", "MUTUAL"})
_LIABILITY_TYPES = frozenset({"LIABILITY", "CREDIT"})
_EQUITY_TYPES = frozenset({"EQUITY"})
_NET_INCOME_TYPES = frozenset({"INCOME", "EXPENSE"})
_CASH_TYPES = frozenset({"BANK", "CASH"})


def _money_compact(value: Decimal) -> str:
    """Format a dollar amount for compact-mode reports.

    Whole-dollar values render without decimals (``$13,091``), partial
    values render with two (``$1,125.50``). Negative values use the
    leading-minus convention rather than parens.
    """
    quantized = value.quantize(Decimal("0.01"))
    if quantized == quantized.to_integral_value():
        return f"${int(quantized):,}"
    return f"${quantized:,.2f}"


def _format_debt_payoff_compact(
    *,
    results: list[dict],
    orig_balances: dict,
    total_balance: Decimal,
    total_interest: Decimal,
    total_months: int,
    payoff_date,
    monthly_budget: Decimal,
    yeti_multiplier: Decimal,
    purchase_amount: Decimal,
    true_cost: Decimal,
) -> str:
    """Render the avalanche-payoff plan as a compact text table.

    Layout follows the comms-audit Phase 4A spec:

        Kill order ($10,000/mo → debt-free Apr 2030, $59,022 interest):
          1. Business Amex    $13,091  24.49%  payoff: mo 8   interest: $1,125
          2. Chase Sapphire   $22,127  21.49%  payoff: mo 18  interest: $5,034
          ...
        YETI at this budget: 1.59x ($1 spent costs $1.59 in total debt impact)
        Total interest: $59,022
        Debt-free: April 2030

    Replaces the verbose dict (with multi-line YETI ``explanation`` per
    account) that was the heaviest single response in Abe's audit.
    Verbose mode preserves the dict for programmatic consumers.
    """
    # Account names: leaf-name only when path is unambiguous (saves
    # context vs. echoing "Liabilities:Credit Card:Business Amex" on
    # every row). Width-pads to the widest leaf so columns align.
    leaf_names = [d["name"].split(":")[-1] for d in results]
    name_width = max(len(n) for n in leaf_names) if leaf_names else 0

    # Balance column width — pad to widest balance for alignment.
    balance_strs = [
        _money_compact(orig_balances[d["name"]]) for d in results
    ]
    balance_width = max(len(b) for b in balance_strs) if balance_strs else 0

    # Interest column width — same trick.
    interest_strs = [_money_compact(d["interest_paid"]) for d in results]
    interest_width = (
        max(len(s) for s in interest_strs) if interest_strs else 0
    )

    # Header tells the reader the inputs that drove the schedule.
    payoff_month_name = payoff_date.strftime("%b %Y")
    header = (
        f"Kill order ({_money_compact(monthly_budget)}/mo → "
        f"debt-free {payoff_month_name}, "
        f"{_money_compact(total_interest)} interest):"
    )

    # Body rows.
    lines = [header]
    for i, d in enumerate(results, start=1):
        leaf = leaf_names[i - 1]
        bal = balance_strs[i - 1]
        interest = interest_strs[i - 1]
        apr_str = f"{d['apr'].quantize(Decimal('0.01'))}%"
        lines.append(
            f"  {i}. {leaf:<{name_width}}  "
            f"{bal:>{balance_width}}  "
            f"{apr_str:>6}  "
            f"payoff: mo {d['payoff_month']:<3}  "
            f"interest: {interest:>{interest_width}}"
        )

    # Footer: YETI plus totals. The YETI line speaks plainly because
    # it's the actionable signal — "this purchase costs you X.XX times
    # more than its sticker because of the interest your debt accrues".
    lines.append(
        f"YETI at this budget: {yeti_multiplier}x "
        f"({_money_compact(purchase_amount)} spent costs "
        f"{_money_compact(true_cost)} in total debt impact)"
    )
    lines.append(f"Total interest: {_money_compact(total_interest)}")
    lines.append(f"Debt-free: {payoff_date.strftime('%B %Y')}")
    return "\n".join(lines)


class ReportingMixin:
    """Financial reporting: spending, income, balance sheet, net worth, cash flow, debt."""

    def _get_account_depth(self, account: piecash.Account) -> int:
        """Get the depth of an account in the hierarchy (root = 0)."""
        depth = 0
        current = account
        while current.parent and current.parent.type != "ROOT":
            depth += 1
            current = current.parent
        return depth

    def _get_account_at_depth(
        self, account: piecash.Account, target_depth: int
    ) -> piecash.Account:
        """Get the ancestor of an account at a specific depth."""
        path = [account]
        current = account
        while current.parent and current.parent.type != "ROOT":
            current = current.parent
            path.append(current)
        path.reverse()  # path[0] is top-level, path[-1] is the account

        if target_depth >= len(path):
            return account
        return path[target_depth]

    # ── Cross-commodity conversion helpers ────────────────────────────
    #
    # For reports that aggregate asset balances across accounts with
    # different commodities (e.g. USD Checking + VTSAX shares + EUR
    # Savings), split.quantity lives in each account's own commodity
    # and can't be summed directly. Convert each account's quantity
    # to the book's default currency at the latest user-supplied
    # market price before aggregating. Fallback: split.value (which
    # is in the transaction currency, generally the book default for
    # USD-denominated investment buys) is used when no price is on
    # file — that yields cost basis, which is a reasonable default
    # when no market price has been loaded.

    def _latest_market_rates(
        self, book: piecash.Book
    ) -> dict[str, Decimal]:
        """Return {commodity_guid: Decimal} — latest user-supplied
        price for each non-default-currency commodity in the book
        currency. Skips piecash's auto-created ``type='transaction'``
        prices (they capture the effective rate of a cross-currency
        txn; we want explicit market prices).
        """
        default_currency = self._require_default_currency(book)
        latest: dict[str, tuple] = {}  # guid → (date, Decimal rate)
        for p in book.prices:
            if p.currency != default_currency:
                continue
            if p.type == "transaction":
                continue
            p_date = p.date
            if hasattr(p_date, "date") and callable(p_date.date):
                p_date = p_date.date()
            key = p.commodity.guid
            existing = latest.get(key)
            if existing is None or p_date > existing[0]:
                latest[key] = (p_date, Decimal(str(p.value)))
        return {guid: rate for guid, (_date, rate) in latest.items()}

    def _account_conversion_factors(
        self, book: piecash.Book
    ) -> dict[str, Decimal | None]:
        """Return {account_guid: Decimal factor or None}.

        ``factor * split.quantity = amount in default currency``.
        A factor of 1 means the account is already in the default
        currency. ``None`` means no rate is available — callers
        should fall back to ``split.value`` (transaction-currency
        amount), which correctly reflects cost basis for USD-
        denominated investment buys.
        """
        default_currency = self._require_default_currency(book)
        rates = self._latest_market_rates(book)
        factors: dict[str, Decimal | None] = {}
        for acct in book.accounts:
            if acct.commodity == default_currency:
                factors[acct.guid] = Decimal("1")
            else:
                factors[acct.guid] = rates.get(acct.commodity.guid)
        return factors

    @staticmethod
    def _split_in_default_currency(
        split,
        account,
        factor: Decimal | None,
    ) -> Decimal:
        """Value a single split in the book's default currency.

        Uses ``factor * quantity`` when a factor is available. Falls
        back to ``split.value`` otherwise — correct for STOCK/MUTUAL
        splits whose transaction currency is the book default, and a
        reasonable cost-basis approximation for other cases.
        """
        if factor is not None:
            return Decimal(str(split.quantity)) * factor
        return Decimal(str(split.value))

    # ── SQL-filtered split iterator ───────────────────────────────────

    def _query_filtered_splits(
        self,
        book: piecash.Book,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        account_types: frozenset[str] | set[str] | None = None,
        account_guids: frozenset[str] | set[str] | None = None,
        order_by_post_date: bool = False,
    ):
        """Build an indexed SQL query over ``(Split, Transaction, Account)``
        rows matching the given filters.

        Every filter maps to an indexable WHERE clause against the
        SQLite backing store — one query returns exactly the rows the
        caller needs, replacing the Python-side
        ``for txn in book.transactions: if date_match`` pattern that
        used to touch every row in the book regardless of relevance.

        The query yields ORM objects (not raw num/denom pairs) so
        callers can aggregate ``split.quantity`` / ``split.value`` as
        exact ``Decimal`` values in Python. Aggregating in SQL via
        ``SUM(num * 1.0 / denom)`` would collapse to IEEE-754 floats,
        which is unacceptable for financial arithmetic.

        Null ``post_date`` rows (an old-book artifact) are excluded so
        they don't show up in date-ranged reports.

        Args:
            book: An open piecash session.
            start_date: Include only transactions with
                ``post_date >= start_date``. ``None`` disables the
                lower bound.
            end_date: Include only transactions with
                ``post_date <= end_date``. ``None`` disables the upper
                bound.
            account_types: Restrict to accounts whose
                ``type in account_types`` (e.g., ``_ASSET_TYPES``).
                ``None`` disables the filter.
            account_guids: Restrict to accounts whose ``guid`` is in
                the given set. Used by ``cash_flow`` when filtering to
                a single named account.
            order_by_post_date: When ``True``, rows come back sorted
                ascending by ``post_date``. Required for the
                cumulative-sum trick used by the ``net_worth``
                time-series.

        Returns:
            A SQLAlchemy ``Query`` object the caller can iterate.
        """
        from piecash.core.account import Account
        from piecash.core.transaction import Split, Transaction

        q = (
            book.session.query(Split, Transaction, Account)
            .join(Transaction, Split.transaction_guid == Transaction.guid)
            .join(Account, Split.account_guid == Account.guid)
            .filter(Transaction.post_date.isnot(None))
        )
        if start_date is not None:
            q = q.filter(Transaction.post_date >= start_date)
        if end_date is not None:
            q = q.filter(Transaction.post_date <= end_date)
        if account_types is not None:
            q = q.filter(Account.type.in_(list(account_types)))
        if account_guids is not None:
            q = q.filter(Account.guid.in_(list(account_guids)))
        if order_by_post_date:
            q = q.order_by(Transaction.post_date)
        return q

    # ── Period breakdowns ─────────────────────────────────────────────

    def spending_by_category(
        self,
        start_date: date,
        end_date: date,
        depth: int = 1,
    ) -> dict:
        """Get spending breakdown by expense category.

        Args:
            start_date: Start of period (inclusive).
            end_date: End of period (inclusive).
            depth: Hierarchy depth for grouping (1 = top-level, 2 = subcategories).

        Returns:
            Dict with period, total, and category breakdown.
        """
        with self.open(readonly=True) as book:
            rows = self._query_filtered_splits(
                book,
                start_date=start_date,
                end_date=end_date,
                account_types=frozenset({"EXPENSE"}),
            )

            totals: dict[str, Decimal] = {}
            for split, _txn, account in rows:
                # Expense splits are positive when money is spent.
                amount = split.quantity
                if amount <= 0:
                    continue
                group_account = self._get_account_at_depth(
                    account, depth - 1
                )
                account_name = group_account.fullname
                totals[account_name] = totals.get(
                    account_name, Decimal("0")
                ) + amount

            total = sum(totals.values()) if totals else Decimal("0")
            categories = []
            for account_name, amount in sorted(
                totals.items(), key=lambda x: x[1], reverse=True
            ):
                percent = (
                    (amount / total * 100) if total > 0 else Decimal("0")
                )
                categories.append({
                    "account": account_name,
                    "amount": str(amount),
                    "percent": str(percent.quantize(Decimal("0.1"))),
                })

            # period is an input echo — LLM supplied start/end dates.
            return {
                "total": str(total),
                "categories": categories,
            }

    def income_by_source(
        self,
        start_date: date,
        end_date: date,
        depth: int = 1,
    ) -> dict:
        """Get income breakdown by source.

        Args:
            start_date: Start of period (inclusive).
            end_date: End of period (inclusive).
            depth: Hierarchy depth for grouping (1 = top-level, 2 = subcategories).

        Returns:
            Dict with period, total, and source breakdown.
        """
        with self.open(readonly=True) as book:
            rows = self._query_filtered_splits(
                book,
                start_date=start_date,
                end_date=end_date,
                account_types=frozenset({"INCOME"}),
            )

            totals: dict[str, Decimal] = {}
            for split, _txn, account in rows:
                # Income splits are stored negative (money coming in);
                # flip to positive for the "how much did I earn" view.
                amount = -split.quantity
                if amount <= 0:
                    continue
                group_account = self._get_account_at_depth(
                    account, depth - 1
                )
                account_name = group_account.fullname
                totals[account_name] = totals.get(
                    account_name, Decimal("0")
                ) + amount

            total = sum(totals.values()) if totals else Decimal("0")
            sources = []
            for account_name, amount in sorted(
                totals.items(), key=lambda x: x[1], reverse=True
            ):
                percent = (
                    (amount / total * 100) if total > 0 else Decimal("0")
                )
                sources.append({
                    "account": account_name,
                    "amount": str(amount),
                    "percent": str(percent.quantize(Decimal("0.1"))),
                })

            # period is an input echo — LLM supplied start/end dates.
            return {
                "total": str(total),
                "sources": sources,
            }

    # ── Balance sheet and net worth ──────────────────────────────────

    def balance_sheet(self, as_of_date: date) -> dict:
        """Generate a balance sheet as of a specific date.

        Args:
            as_of_date: Date to calculate balances as of.

        Returns:
            Dict with assets, liabilities, equity sections and totals.
        """
        # Sum splits across every relevant type in one SQL-filtered
        # pass. The old code opened Python loops per account; now we
        # hit the splits table once with an IN() clause and bucket the
        # results in memory. Net income (Income - Expenses) is
        # retained-earnings-equivalent and rolls into equity below.
        all_types = (
            _ASSET_TYPES | _LIABILITY_TYPES | _EQUITY_TYPES | _NET_INCOME_TYPES
        )
        with self.open(readonly=True) as book:
            factors = self._account_conversion_factors(book)
            rows = self._query_filtered_splits(
                book,
                end_date=as_of_date,
                account_types=all_types,
            )

            balances: dict[str, tuple[str, Decimal]] = {}
            net_income = Decimal("0")
            for split, _txn, account in rows:
                # Value the split in the book's default currency so
                # investment shares (VTSAX @ $128, etc.) and foreign-
                # currency holdings contribute their market/USD value
                # to balance-sheet totals rather than raw share counts.
                amt = self._split_in_default_currency(
                    split, account, factors.get(account.guid)
                )
                if account.type in _NET_INCOME_TYPES:
                    # Income is stored negative, expenses positive; net
                    # income = revenues - expenses = -(sum of both).
                    net_income -= amt
                    continue
                key = account.fullname
                current_type, current_bal = balances.get(
                    key, (account.type, Decimal("0"))
                )
                balances[key] = (current_type, current_bal + amt)

            assets: dict[str, Decimal] = {}
            liabilities: dict[str, Decimal] = {}
            equity: dict[str, Decimal] = {}
            for name, (acct_type, balance) in balances.items():
                if balance == 0:
                    continue
                if acct_type in _ASSET_TYPES:
                    assets[name] = balance
                elif acct_type in _LIABILITY_TYPES:
                    # Liabilities stored negative; display positive.
                    liabilities[name] = -balance
                elif acct_type in _EQUITY_TYPES:
                    equity[name] = -balance

            assets_total = sum(assets.values()) if assets else Decimal("0")
            liabilities_total = (
                sum(liabilities.values()) if liabilities else Decimal("0")
            )
            equity_total = (
                (sum(equity.values()) if equity else Decimal("0"))
                + net_income
            )

            def format_accounts(accounts_dict: dict[str, Decimal]) -> list[dict]:
                return [
                    {"account": name, "balance": str(bal)}
                    for name, bal in sorted(accounts_dict.items())
                ]

            # as_of_date is also an input echo, but it's cheap and
            # useful when a log is reviewed out of context. `balanced`
            # is derivable (assets == liabilities + equity); dropped.
            return {
                "as_of_date": as_of_date.isoformat(),
                "assets": {
                    "total": str(assets_total),
                    "accounts": format_accounts(assets),
                },
                "liabilities": {
                    "total": str(liabilities_total),
                    "accounts": format_accounts(liabilities),
                },
                "equity": {
                    "total": str(equity_total),
                    "accounts": format_accounts(equity) + (
                        [{"account": "Retained Earnings", "balance": str(net_income)}]
                        if net_income != 0 else []
                    ),
                },
            }

    def net_worth(
        self,
        end_date: date,
        start_date: date | None = None,
        interval: str | None = None,
    ) -> dict:
        """Calculate net worth (assets minus liabilities).

        Args:
            end_date: Calculate net worth as of this date.
            start_date: If provided with interval, calculate series over time.
            interval: 'month', 'quarter', or 'year' for time series.

        Returns:
            Dict with net worth value or time series.
        """
        from dateutil.relativedelta import relativedelta

        nw_types = _ASSET_TYPES | _LIABILITY_TYPES

        with self.open(readonly=True) as book:
            factors = self._account_conversion_factors(book)

            # --- Point-in-time: one filtered SQL query, sum in Python.
            if not start_date or not interval:
                rows = self._query_filtered_splits(
                    book,
                    end_date=end_date,
                    account_types=nw_types,
                )
                total = Decimal("0")
                for split, _txn, account in rows:
                    # Liabilities are stored negative, so adding the
                    # converted amount directly gives assets minus
                    # liabilities. Investments and foreign-currency
                    # holdings are valued in the book's default
                    # currency via their latest market price.
                    total += self._split_in_default_currency(
                        split, account, factors.get(account.guid)
                    )
                return {
                    "as_of_date": end_date.isoformat(),
                    "net_worth": str(total),
                }

            # --- Time series: single sweep, cumulative sum.
            if interval not in ("month", "quarter", "year"):
                raise ValueError(
                    f"Invalid interval: {interval}. "
                    f"Use 'month', 'quarter', or 'year'"
                )

            delta = {
                "month": relativedelta(months=1),
                "quarter": relativedelta(months=3),
                "year": relativedelta(years=1),
            }[interval]

            # Build the list of interval boundaries up-front. Every
            # boundary gets a net_worth snapshot below; end_date is
            # appended if the interval didn't land on it naturally.
            boundaries: list[date] = []
            cursor = start_date
            while cursor <= end_date:
                boundaries.append(cursor)
                cursor += delta
            if not boundaries or boundaries[-1] != end_date:
                boundaries.append(end_date)

            # Pull every asset/liability split up through end_date in
            # post_date order, then sweep once: as we cross each
            # boundary, snapshot the running total. This replaces what
            # was previously T × N (intervals × accounts × splits) with
            # a single O(splits + intervals) pass.
            rows = self._query_filtered_splits(
                book,
                end_date=end_date,
                account_types=nw_types,
                order_by_post_date=True,
            )

            series: list[dict] = []
            running = Decimal("0")
            b_idx = 0

            for split, txn, account in rows:
                # For each boundary that sits before this split's
                # post_date, the running total is already correct —
                # snapshot it and advance.
                while (
                    b_idx < len(boundaries)
                    and txn.post_date > boundaries[b_idx]
                ):
                    series.append({
                        "date": boundaries[b_idx].isoformat(),
                        "net_worth": str(running),
                    })
                    b_idx += 1
                running += self._split_in_default_currency(
                    split, account, factors.get(account.guid)
                )

            # Drain any boundaries past the last split — they all see
            # the final running total.
            while b_idx < len(boundaries):
                series.append({
                    "date": boundaries[b_idx].isoformat(),
                    "net_worth": str(running),
                })
                b_idx += 1

            return {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "interval": interval,
                "series": series,
            }

    def cash_flow(
        self,
        start_date: date,
        end_date: date,
        account: str | None = None,
    ) -> dict:
        """Calculate cash flow (inflows and outflows) for a period.

        Args:
            start_date: Start of period (inclusive).
            end_date: End of period (inclusive).
            account: Optional account to filter (e.g., specific bank account).

        Returns:
            Dict with inflows, outflows, and net cash flow.
        """
        with self.open(readonly=True) as book:
            # Two filter modes: a named account (one-GUID IN() clause)
            # or the default "all cash/bank accounts" (account-type
            # IN() clause). Both push the filter to SQL.
            if account:
                target_account = self._resolve_account(book, account)
                if not target_account:
                    raise ValueError(f"Account not found: {account}")
                rows = self._query_filtered_splits(
                    book,
                    start_date=start_date,
                    end_date=end_date,
                    account_guids=frozenset({target_account.guid}),
                )
            else:
                rows = self._query_filtered_splits(
                    book,
                    start_date=start_date,
                    end_date=end_date,
                    account_types=_CASH_TYPES,
                )

            factors = self._account_conversion_factors(book)
            inflows = Decimal("0")
            outflows = Decimal("0")
            for split, _txn, acct in rows:
                amt = self._split_in_default_currency(
                    split, acct, factors.get(acct.guid)
                )
                if amt > 0:
                    inflows += amt
                elif amt < 0:
                    outflows += -amt

            # period is input echo (LLM supplied dates), net is derivable
            # (inflows - outflows). Both dropped.
            #
            # Echo the canonical fullname rather than the raw input —
            # so callers passing %short or full-GUID input always see
            # a readable account name in the response.
            return {
                "account": (
                    target_account.fullname if account
                    else "All cash/bank accounts"
                ),
                "inflows": str(inflows),
                "outflows": str(outflows),
            }

    # ── Debt Payoff ───────────────────────────────────────────────

    @staticmethod
    def _run_avalanche(
        debts: list[dict], monthly_budget: Decimal
    ) -> tuple[list[dict], int, Decimal]:
        """Simulate avalanche-method debt payoff month by month.

        Args:
            debts: List of dicts with 'name', 'balance', 'apr', 'min_payment'.
                   Balances should be positive numbers representing amount owed.
            monthly_budget: Total monthly amount available for all debt payments.

        Returns:
            Tuple of (debt_results, total_months, total_interest) where
            debt_results has per-debt payoff details.
        """
        import copy

        working = copy.deepcopy(debts)
        # Sort by APR descending (avalanche order)
        working.sort(key=lambda d: d["apr"], reverse=True)

        for d in working:
            d["interest_paid"] = Decimal("0")
            d["payoff_month"] = None

        month = 0
        max_months = 1200  # 100 years safety cap

        while any(d["balance"] > 0 for d in working) and month < max_months:
            month += 1

            # Step 1: Apply monthly interest to each balance
            for d in working:
                if d["balance"] <= 0:
                    continue
                monthly_rate = d["apr"] / Decimal("100") / Decimal("12")
                interest = (d["balance"] * monthly_rate).quantize(Decimal("0.01"))
                d["balance"] += interest
                d["interest_paid"] += interest

            # Step 2: Pay minimums on each debt
            remaining_budget = monthly_budget
            for d in working:
                if d["balance"] <= 0:
                    continue
                payment = min(d["min_payment"], d["balance"])
                payment = min(payment, remaining_budget)
                d["balance"] -= payment
                remaining_budget -= payment
                if d["balance"] <= 0:
                    d["balance"] = Decimal("0")
                    if d["payoff_month"] is None:
                        d["payoff_month"] = month

            # Step 3: Apply remaining budget to highest-APR debt with balance
            for d in working:
                if remaining_budget <= 0:
                    break
                if d["balance"] <= 0:
                    continue
                extra = min(remaining_budget, d["balance"])
                d["balance"] -= extra
                remaining_budget -= extra
                if d["balance"] <= 0:
                    d["balance"] = Decimal("0")
                    if d["payoff_month"] is None:
                        d["payoff_month"] = month

        total_interest = sum(d["interest_paid"] for d in working)
        return working, month, total_interest

    def debt_payoff_plan(
        self,
        monthly_budget: str,
        additional_purchase: str | None = None,
        compact: bool = True,
    ) -> dict | str:
        """Calculate an avalanche-method debt payoff schedule with YETI multiplier.

        Auto-discovers CREDIT/LIABILITY accounts that have an 'apr' slot set.

        YETI (Your Expense's True Impact) answers: "A $1.00 purchase will cost
        you $X.XX by the time your debt is paid off."

        Args:
            monthly_budget: Total monthly amount available for all debt payments.
            additional_purchase: Dollar amount to calculate YETI for (default "1.00").
            compact: If True (default), return a compact text-table summary
                     suitable for the LLM context window — kill order,
                     totals, YETI all in ~10 lines. Verbose mode returns
                     the full structured dict (the legacy shape) for
                     programmatic consumers.

        Returns:
            If compact: text summary (kill order + YETI line + totals).
            If not compact: dict with the original full structure.

        Raises:
            ValueError: If no debt accounts found, budget invalid, or budget
                        less than sum of minimum payments.
        """
        budget = _to_decimal(monthly_budget)
        if budget <= 0:
            raise ValueError("monthly_budget must be a positive number")

        purchase_amount = (
            _to_decimal(additional_purchase)
            if additional_purchase
            else Decimal("1.00")
        )
        if purchase_amount <= 0:
            raise ValueError("additional_purchase must be a positive number")

        with self.open(readonly=True) as book:
            debt_types = {"CREDIT", "LIABILITY"}
            debts = []

            for account in book.accounts:
                if account.type not in debt_types:
                    continue

                try:
                    apr_val = account["apr"]
                    apr_str = str(apr_val.value) if hasattr(apr_val, "value") else str(apr_val)
                    apr = Decimal(apr_str)
                except (KeyError, InvalidOperation):
                    continue

                if apr <= 0:
                    continue

                # Calculate current balance (negate because liabilities are stored negative)
                balance = Decimal("0")
                for split in account.splits:
                    balance += split.quantity
                balance = -balance  # Convert to positive amount owed

                if balance <= 0:
                    continue  # Skip zero or overpaid balances

                min_payment = None

                # 1. Check minimum_payment slot (user override)
                try:
                    mp_val = account["minimum_payment"]
                    mp_str = str(mp_val.value) if hasattr(mp_val, "value") else str(mp_val)
                    min_payment = Decimal(mp_str)
                except (KeyError, InvalidOperation):
                    pass

                # 2. Calculate from balance: greater of $25 or 2% of balance
                if min_payment is None:
                    two_percent = (balance * Decimal("0.02")).quantize(Decimal("0.01"))
                    min_payment = max(two_percent, Decimal("25"))
                    # If balance is below $25, minimum is the full balance
                    if balance < Decimal("25"):
                        min_payment = balance

                credit_limit = None
                try:
                    cl_val = account["credit_limit"]
                    cl_str = str(cl_val.value) if hasattr(cl_val, "value") else str(cl_val)
                    credit_limit = Decimal(cl_str)
                except (KeyError, InvalidOperation):
                    pass

                debts.append({
                    "name": account.fullname,
                    "balance": balance,
                    "apr": apr,
                    "min_payment": min_payment,
                    "credit_limit": credit_limit,
                })

        if not debts:
            raise ValueError(
                "No debt accounts found with 'apr' slot set. "
                "Use set_account_slot to set APR on your CREDIT/LIABILITY accounts."
            )

        total_minimums = sum(d["min_payment"] for d in debts)
        if budget < total_minimums:
            raise ValueError(
                f"monthly_budget ({monthly_budget}) is less than the sum of minimum "
                f"payments ({total_minimums}). Debt will grow indefinitely."
            )

        total_balance = sum(d["balance"] for d in debts)

        results, total_months, total_interest = self._run_avalanche(debts, budget)
        total_paid = total_balance + total_interest

        from dateutil.relativedelta import relativedelta

        today = date.today()
        payoff_date = today + relativedelta(months=total_months)

        # YETI: Run avalanche twice
        # Run 1 already done (results above). Run 2 adds purchase to highest-APR debt
        debts_with_purchase = []
        for d in debts:
            debts_with_purchase.append(dict(d))
        debts_with_purchase.sort(key=lambda d: d["apr"], reverse=True)
        debts_with_purchase[0]["balance"] += purchase_amount

        _, _, interest_with_purchase = self._run_avalanche(debts_with_purchase, budget)
        total_paid_with_purchase = total_balance + purchase_amount + interest_with_purchase
        true_cost = total_paid_with_purchase - total_paid
        yeti_multiplier = (true_cost / purchase_amount).quantize(Decimal("0.01"))

        results.sort(key=lambda d: d["apr"], reverse=True)
        payoff_order = [d["name"] for d in results]

        orig_balances = {d["name"]: d["balance"] for d in debts}

        debt_details = []
        for d in results:
            detail = {
                "account": d["name"],
                "balance": str(orig_balances[d["name"]].quantize(Decimal("0.01"))),
                "apr": str(d["apr"]),
                "minimum_payment": str(d["min_payment"]),
                "interest_paid": str(d["interest_paid"].quantize(Decimal("0.01"))),
                "payoff_month": d["payoff_month"],
            }
            if d.get("credit_limit") is not None:
                detail["credit_limit"] = str(d["credit_limit"])
            debt_details.append(detail)

        full = {
            "debts": debt_details,
            "payoff_order": payoff_order,
            "total_balance": str(total_balance.quantize(Decimal("0.01"))),
            "total_interest": str(total_interest.quantize(Decimal("0.01"))),
            "total_paid": str(total_paid.quantize(Decimal("0.01"))),
            "payoff_months": total_months,
            "payoff_date": payoff_date.isoformat(),
            "monthly_budget": monthly_budget,
            "yeti": {
                "multiplier": str(yeti_multiplier),
                "purchase_amount": str(purchase_amount),
                "true_cost": str(true_cost.quantize(Decimal("0.01"))),
                "explanation": (
                    f"A ${purchase_amount} purchase will cost you "
                    f"${true_cost.quantize(Decimal('0.01'))} by the time your "
                    f"debt is paid off"
                ),
            },
        }

        if not compact:
            return full

        return _format_debt_payoff_compact(
            results=results,
            orig_balances=orig_balances,
            total_balance=total_balance,
            total_interest=total_interest,
            total_months=total_months,
            payoff_date=payoff_date,
            monthly_budget=budget,
            yeti_multiplier=yeti_multiplier,
            purchase_amount=purchase_amount,
            true_cost=true_cost,
        )
