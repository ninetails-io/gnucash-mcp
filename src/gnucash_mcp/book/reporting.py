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
from gnucash_mcp._format import _format_number

# Account-type groups used across the reports. Defined at module level
# so the SQL IN() clauses share a single canonical definition rather
# than drifting across methods.
_ASSET_TYPES = frozenset({"ASSET", "BANK", "CASH", "STOCK", "MUTUAL"})
_LIABILITY_TYPES = frozenset({"LIABILITY", "CREDIT"})
_EQUITY_TYPES = frozenset({"EQUITY"})
_NET_INCOME_TYPES = frozenset({"INCOME", "EXPENSE"})
_CASH_TYPES = frozenset({"BANK", "CASH"})


def _format_breakdown_tsv(rows: list[dict], total: Decimal, label_key: str) -> str:
    """Render a category/source breakdown as a compact aligned table.

    Format::

        Business              22,336.90  39.3%
        Taxes                  9,479.04  16.7%
        ...
        TOTAL                 56,944.26

    Account names render as their leaf component when unambiguous —
    spending breakdowns at depth=1 typically yield "Expenses:Business",
    "Expenses:Taxes", etc., which all share the "Expenses:" prefix and
    read better with the prefix stripped. Width-padded so the amount /
    percent columns align.
    """
    if not rows:
        return f"TOTAL  {total:,.2f}"

    # Strip a common leading "Expenses:" / "Income:" prefix when every
    # row shares it. This keeps the leaf-name column readable without
    # losing information for mixed-source breakdowns (where the prefix
    # is heterogeneous and we should keep the full path).
    full_names = [r[label_key] for r in rows]
    common_prefix = ""
    if full_names and ":" in full_names[0]:
        candidate = full_names[0].split(":")[0] + ":"
        if all(n.startswith(candidate) for n in full_names):
            common_prefix = candidate
    leaves = [n[len(common_prefix):] for n in full_names]

    name_width = max(len(n) for n in leaves) if leaves else 0
    amount_strs = [f"{Decimal(r['amount']):,.2f}" for r in rows]
    amount_width = max(len(a) for a in amount_strs) if amount_strs else 0

    lines = []
    for leaf, row, amt_str in zip(leaves, rows, amount_strs):
        percent = row.get("percent", "0")
        lines.append(
            f"{leaf:<{name_width}}  {amt_str:>{amount_width}}  {percent}%"
        )
    lines.append(
        f"{'TOTAL':<{name_width}}  {total:>{amount_width},.2f}"
    )
    return "\n".join(lines)


def _money_compact(value: Decimal, currency: str = "USD") -> str:
    """Format a monetary amount for compact-mode reports.

    Whole-currency-unit values render without decimals
    (``"USD 13,091"``), partial values render with two
    (``"USD 1,125.50"``). Negative values use leading-minus rather
    than parens.

    The ``currency`` argument carries the book's default currency
    mnemonic (``"USD"``, ``"CNY"``, ``"EUR"``, etc.) — matches
    ``get_book_summary``'s ``"USD 6700.00"`` rendering style and
    works for non-USD books out of the box. Pre-fix this helper
    hardcoded ``$`` and broke as soon as the bookkeeper pointed it
    at a CNY-default book.
    """
    quantized = value.quantize(Decimal("0.01"))
    if quantized == quantized.to_integral_value():
        return f"{currency} {int(quantized):,}"
    return f"{currency} {quantized:,.2f}"


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
    currency: str = "USD",
) -> str:
    """Render the avalanche-payoff plan as a compact text table.

    Currency prefix flows from the book's default currency — works
    for USD, CNY, EUR, anything piecash represents. Layout::

        Kill order (USD 10,000/mo → debt-free Apr 2030, USD 59,022 interest):
          1. Business Amex    USD 13,091  24.49%  payoff: mo 8   interest: USD 1,125
          2. Chase Sapphire   USD 22,127  21.49%  payoff: mo 18  interest: USD 5,034
          ...
        YETI at this budget: 1.59x (USD 1 spent costs USD 1.59 in total debt impact)
        Total interest: USD 59,022
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
        _money_compact(orig_balances[d["name"]], currency) for d in results
    ]
    balance_width = max(len(b) for b in balance_strs) if balance_strs else 0

    # Interest column width — same trick.
    interest_strs = [
        _money_compact(d["interest_paid"], currency) for d in results
    ]
    interest_width = (
        max(len(s) for s in interest_strs) if interest_strs else 0
    )

    # Header tells the reader the inputs that drove the schedule.
    payoff_month_name = payoff_date.strftime("%b %Y")
    header = (
        f"Kill order ({_money_compact(monthly_budget, currency)}/mo → "
        f"debt-free {payoff_month_name}, "
        f"{_money_compact(total_interest, currency)} interest):"
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
        f"({_money_compact(purchase_amount, currency)} spent costs "
        f"{_money_compact(true_cost, currency)} in total debt impact)"
    )
    lines.append(
        f"Total interest: {_money_compact(total_interest, currency)}"
    )
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

    # ── SQL-filtered split iterator ───────────────────────────────────

    # ── Period breakdowns ─────────────────────────────────────────────

    def spending_by_category(
        self,
        start_date: date,
        end_date: date,
        depth: int = 1,
        compact: bool = True,
    ) -> dict | str:
        """Get spending breakdown by expense category.

        Args:
            start_date: Start of period (inclusive).
            end_date: End of period (inclusive).
            depth: Hierarchy depth for grouping (1 = top-level, 2 = subcategories).
            compact: If True (default), return an aligned text table
                     suitable for direct LLM consumption (Phase 4C).
                     Verbose mode returns the structured dict.

        Returns:
            If compact: string with one line per category plus a TOTAL.
            If not compact: dict with ``total`` and ``categories`` list.
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

            if compact:
                return _format_breakdown_tsv(categories, total, "account")
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
        compact: bool = True,
    ) -> dict | str:
        """Get income breakdown by source.

        Args:
            start_date: Start of period (inclusive).
            end_date: End of period (inclusive).
            depth: Hierarchy depth for grouping (1 = top-level, 2 = subcategories).
            compact: If True (default), return an aligned text table
                     suitable for direct LLM consumption (Phase 4C).
                     Verbose mode returns the structured dict.

        Returns:
            If compact: string with one line per source plus a TOTAL.
            If not compact: dict with ``total`` and ``sources`` list.
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

            if compact:
                return _format_breakdown_tsv(sources, total, "account")
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

            # Track per-account: (acct_type, USD-converted balance,
            # commodity-quantity, account ref). The quantity / commodity
            # bits are needed to render the "230.7600 VTSAX @ 156.23
            # (USD 36,043.66)" triplet for non-default-currency accounts
            # (Phase 4B). Currency-default accounts ignore that detail.
            balances: dict[str, dict] = {}
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
                if key not in balances:
                    balances[key] = {
                        "type": account.type,
                        "usd": Decimal("0"),
                        "quantity": Decimal("0"),
                        "commodity": account.commodity,
                    }
                balances[key]["usd"] += amt
                balances[key]["quantity"] += split.quantity

            default_currency = self._require_default_currency(book)
            # Latest market rates keyed by commodity guid — same data
            # ``_market_value`` in get_book_summary uses for the per-
            # account display. ``_rates_as_of(book)`` (no date filter)
            # already excludes piecash auto-created ``type='transaction'``
            # prices.
            latest_rates = self._rates_as_of(book)

            assets: dict[str, dict] = {}
            liabilities: dict[str, dict] = {}
            equity: dict[str, dict] = {}
            for name, info in balances.items():
                bal = info["usd"]
                if bal == 0:
                    continue
                if info["type"] in _ASSET_TYPES:
                    assets[name] = info
                elif info["type"] in _LIABILITY_TYPES:
                    info["usd"] = -bal  # display positive
                    info["quantity"] = -info["quantity"]
                    liabilities[name] = info
                elif info["type"] in _EQUITY_TYPES:
                    info["usd"] = -bal
                    info["quantity"] = -info["quantity"]
                    equity[name] = info

            assets_total = (
                sum(i["usd"] for i in assets.values())
                if assets else Decimal("0")
            )
            liabilities_total = (
                sum(i["usd"] for i in liabilities.values())
                if liabilities else Decimal("0")
            )
            equity_total = (
                (sum(i["usd"] for i in equity.values()) if equity else Decimal("0"))
                + net_income
            )

            def format_accounts(accounts_dict: dict[str, dict]) -> list[dict]:
                """Render each account row with the right balance shape.

                Currency-default accounts:
                  ``{"account": name, "balance": "5234.56"}``.
                  No ``usd_value`` — it would just repeat ``balance``.

                Non-currency accounts (STOCK / MUTUAL / FUND): the
                ``balance`` field shows the human-readable triplet
                ``"230.7600 VTSAX @ 156.23 (USD 36,043.66)"`` — same
                format ``get_book_summary`` uses — and ``usd_value``
                carries the parseable number rounded to 2 decimals so
                programmatic callers don't have to parse the triplet.

                All numeric outputs flow through ``_format_number``
                (currency-style: 2 decimals always padded). Pre-fix
                the per-account values leaked Decimal precision noise
                (e.g. ``"612011.489832"``) into responses.
                """
                # Default-currency mnemonic for the triplet rendering.
                # Pre-fix this hardcoded "USD"; on a CNY-default book
                # that lied about the currency on every investment row.
                ccy_mnemonic = default_currency.mnemonic
                rows = []
                for name, info in sorted(accounts_dict.items()):
                    commodity = info["commodity"]
                    default_value_rounded = _format_number(
                        info["usd"], decimals=2
                    )
                    if commodity == default_currency:
                        rows.append({
                            "account": name,
                            "balance": default_value_rounded,
                        })
                    else:
                        rate = latest_rates.get(commodity.guid)
                        sym = commodity.mnemonic
                        qty = info["quantity"]
                        if rate is not None:
                            balance_str = (
                                f"{qty} {sym} @ {rate} "
                                f"({ccy_mnemonic} {info['usd']:,.2f})"
                            )
                        else:
                            # No price on file — fall back to cost basis
                            # already accumulated in ``info['usd']``.
                            balance_str = (
                                f"{qty} {sym} ({ccy_mnemonic} "
                                f"{info['usd']:,.2f}, no price data)"
                            )
                        # ``default_currency_value`` carries the
                        # parseable amount in the book's default
                        # currency. Pre-fix this field was named
                        # ``usd_value`` — a lie on non-USD books.
                        # Renamed to reflect the actual semantics.
                        rows.append({
                            "account": name,
                            "balance": balance_str,
                            "default_currency_value": default_value_rounded,
                        })
                return rows

            # as_of_date is also an input echo, but it's cheap and
            # useful when a log is reviewed out of context. `balanced`
            # ``balanced`` is derivable (assets == liabilities + equity)
            # and was dropped in an earlier audit pass. Rollup totals
            # flow through ``_format_number`` (2 decimals, currency
            # style) so the response no longer leaks Decimal precision
            # noise like ``"612011.489832"``.
            return {
                "as_of_date": as_of_date.isoformat(),
                "assets": {
                    "total": _format_number(assets_total, decimals=2),
                    "accounts": format_accounts(assets),
                },
                "liabilities": {
                    "total": _format_number(liabilities_total, decimals=2),
                    "accounts": format_accounts(liabilities),
                },
                "equity": {
                    "total": _format_number(equity_total, decimals=2),
                    "accounts": format_accounts(equity) + (
                        [{
                            "account": "Retained Earnings",
                            "balance": _format_number(net_income, decimals=2),
                        }]
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

            # Step 1: Apply monthly interest to each balance. Rate is
            # pre-computed per debt in ``debt_payoff_plan`` (apr/100/12);
            # this loop runs up to 1200 × N times and recomputing the
            # division each iteration was pure waste.
            for d in working:
                if d["balance"] <= 0:
                    continue
                interest = (d["balance"] * d["monthly_rate"]).quantize(Decimal("0.01"))
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
            If compact: text summary, e.g.::

                Kill order ($10,000/mo → debt-free Apr 2030, $59,022 interest):
                  1. Business Amex    $13,091  24.49%  payoff: mo 8   interest: $1,125
                  2. Chase Sapphire   $22,127  21.49%  payoff: mo 18  interest: $5,034
                  ...
                YETI at this budget: 1.59x ($1 spent costs $1.59 in total debt impact)
                Total interest: $59,022
                Debt-free: April 2030

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
            # Capture the book's default currency for the compact
            # formatter — pre-fix this method emitted ``$`` regardless
            # of book setting, breaking on non-USD books.
            default_currency_mnemonic = (
                self._require_default_currency(book).mnemonic
            )
            debt_types = {"CREDIT", "LIABILITY"}
            debts = []

            # Template accounts (scheduled-transaction
            # scaffolding) inherit type=CREDIT/LIABILITY from
            # their parent in the user's chart and could
            # technically carry an ``apr`` slot if anyone managed
            # to set one — pathological but possible. Filter
            # upfront so the avalanche schedule never includes
            # template balances. Defense-in-depth, not a bug fix.
            template_guids = self._template_account_guids(book)

            for account in book.accounts:
                if account.guid in template_guids:
                    continue
                if account.type not in debt_types:
                    continue

                # Materialize ``account.slots`` into a dict once. Pre-fix,
                # each ``account[key]`` access (apr, minimum_payment,
                # credit_limit — three per account) went through piecash's
                # slot-helper path, hitting the slots collection
                # independently per key. One iteration + three dict gets
                # is cheaper.
                slot_by_name = {s.name: s for s in account.slots}

                apr_val = slot_by_name.get("apr")
                if apr_val is None:
                    continue
                try:
                    apr_str = str(apr_val.value) if hasattr(apr_val, "value") else str(apr_val)
                    apr = Decimal(apr_str)
                except InvalidOperation:
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

                # 1. Check minimum_payment slot (user override).
                #    Wins for both CREDIT and LIABILITY — the user has
                #    declared the contractual amount.
                mp_val = slot_by_name.get("minimum_payment")
                if mp_val is not None:
                    try:
                        mp_str = str(mp_val.value) if hasattr(mp_val, "value") else str(mp_val)
                        min_payment = Decimal(mp_str)
                    except InvalidOperation:
                        pass

                # 2. Type-aware fallback. Credit cards and amortizing
                #    loans have very different minimum-payment shapes:
                #    CREDIT cards charge ~2% of balance (revolving,
                #    no fixed term), while LIABILITY loans are
                #    contractually fixed payments derived from
                #    principal × rate × term (mortgage, auto, student).
                #    Applying the 2% rule to a mortgage produces a
                #    minimum that's ~3-4× the actual payment, which
                #    makes the budget-vs-minimums gate trip on any
                #    realistic household budget.
                if min_payment is None:
                    if account.type == "CREDIT":
                        two_percent = (
                            balance * Decimal("0.02")
                        ).quantize(Decimal("0.01"))
                        min_payment = max(two_percent, Decimal("25"))
                        if balance < Decimal("25"):
                            min_payment = balance
                    else:
                        # LIABILITY: standard amortization formula.
                        # PMT = P × r(1+r)^n / ((1+r)^n − 1)
                        # Term defaults: 30 years if "mortgage" appears
                        # anywhere in the account path (Liabilities:
                        # Mortgage, Loans:Mortgage:Principal, etc.),
                        # else 5 years (auto, personal, etc.). Users
                        # with non-standard terms should set the
                        # minimum_payment slot explicitly.
                        is_mortgage = "mortgage" in account.fullname.lower()
                        term_months = 360 if is_mortgage else 60
                        monthly_rate = (
                            apr / Decimal("100") / Decimal("12")
                        )
                        factor = (Decimal("1") + monthly_rate) ** term_months
                        min_payment = (
                            balance * monthly_rate * factor / (factor - Decimal("1"))
                        ).quantize(Decimal("0.01"))
                        # Cap at balance for tiny remainders where the
                        # formula could over-shoot a near-paid-off loan.
                        if min_payment > balance:
                            min_payment = balance

                credit_limit = None
                cl_val = slot_by_name.get("credit_limit")
                if cl_val is not None:
                    try:
                        cl_str = str(cl_val.value) if hasattr(cl_val, "value") else str(cl_val)
                        credit_limit = Decimal(cl_str)
                    except InvalidOperation:
                        pass

                debts.append({
                    "name": account.fullname,
                    "balance": balance,
                    "apr": apr,
                    # Pre-compute monthly rate once per debt. _run_avalanche's
                    # inner loop iterates up to 1200 months, and pre-fix
                    # recomputed apr/100/12 every iteration for every debt
                    # that still had a balance.
                    "monthly_rate": apr / Decimal("100") / Decimal("12"),
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
                    f"A {default_currency_mnemonic} {purchase_amount} purchase "
                    f"will cost you {default_currency_mnemonic} "
                    f"{true_cost.quantize(Decimal('0.01'))} by the time your "
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
            currency=default_currency_mnemonic,
        )
