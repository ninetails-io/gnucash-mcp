"""ReportingMixin — balance sheet, spending/income breakdowns, cash flow, debt payoff.

All methods are read-only (no ``book.save()``). debt_payoff_plan
reads apr / minimum_payment / credit_limit slots directly, so it
doesn't depend on AdminMixin's slot tools.

The transaction-scanning reports push date and account-type filters
into indexed SQL via ``_query_filtered_splits``; aggregation stays
in Python so amounts sum as exact Decimals. The ``net_worth``
time-series is a single sweep over post_date-ordered splits with
per-boundary snapshots — O(splits + intervals), not
O(intervals × splits).
"""

import logging
from datetime import date
from decimal import Decimal, InvalidOperation

import piecash

from gnucash_mcp.book._base import _is_voided, _to_decimal
from gnucash_mcp._format import (
    _GROUP_BY_VALUES,
    _PARTIAL_FOOTNOTE,
    _enumerate_periods,
    _format_grouped_tsv,
    _format_number,
    _mark_partial,
    _partial_period_labels,
    _period_label,
)

debug_logger = logging.getLogger("gnucash_mcp.debug")

# Account-type groups shared by the reports' SQL IN() clauses — one
# canonical definition. RECEIVABLE/PAYABLE are included: A/R is an
# asset and A/P a liability; excluding them makes posted invoices
# invisible on balance_sheet and breaks A = L + E by the
# outstanding-invoice amount.
_ASSET_TYPES = frozenset({"ASSET", "BANK", "CASH", "STOCK", "MUTUAL", "RECEIVABLE"})
_LIABILITY_TYPES = frozenset({"LIABILITY", "CREDIT", "PAYABLE"})
_EQUITY_TYPES = frozenset({"EQUITY"})
_NET_INCOME_TYPES = frozenset({"INCOME", "EXPENSE"})
_CASH_TYPES = frozenset({"BANK", "CASH"})


def _format_breakdown_tsv(rows: list[dict], total: Decimal, label_key: str) -> str:
    """Render a category/source breakdown as a compact aligned table.

    Format::

        Business              22,336.90  39.3%
        TOTAL                 56,944.26

    A common leading prefix ("Expenses:") is stripped when every row
    shares it; columns are width-aligned.
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


def _format_grouped_cashflow_tsv(
    *,
    period_labels: list[str],
    inflows: dict[str, Decimal],
    outflows: dict[str, Decimal],
    account_label: str,
    transfers_excluded: int,
    partial_labels: set[str] | None = None,
) -> str:
    """Render a multi-period cash-flow trend as a TSV table.

    Three fixed rows — Inflows, Outflows (positive magnitude, matching
    single-period), and Net (Inflows − Outflows, the build-vs-burn
    signal a trend view exists for). Columns are the sub-periods plus
    Total and Avg. A leading title line names the account scope; a
    trailing note surfaces the ``include_transfers`` escape hatch.
    """
    num_periods = len(period_labels)
    net = {pl: inflows[pl] - outflows[pl] for pl in period_labels}

    def _row(name: str, values: dict[str, Decimal]) -> str:
        cells = [name]
        cells += [f"{values[pl]:.2f}" for pl in period_labels]
        tot = sum(values.values(), Decimal("0"))
        avg = tot / num_periods if num_periods else Decimal("0")
        cells += [f"{tot:.2f}", f"{avg:.2f}"]
        return "\t".join(cells)

    lines = [
        account_label,
        "\t".join([
            "Cash flow",
            *_mark_partial(period_labels, partial_labels),
            "Total", "Avg",
        ]),
        _row("Inflows", inflows),
        _row("Outflows", outflows),
        _row("Net", net),
    ]
    out = "\n".join(lines)
    if partial_labels:
        out += f"\n{_PARTIAL_FOOTNOTE}"
    if transfers_excluded:
        out += (
            f"\n({transfers_excluded} internal transfer txn(s) excluded; "
            f"include_transfers=true to include)"
        )
    return out


def _money_compact(value: Decimal, currency: str = "USD") -> str:
    """Format a monetary amount for compact-mode reports.

    Whole values render without decimals (``"USD 13,091"``), partial
    with two; negatives use leading-minus. ``currency`` is the book
    default mnemonic — a hardcoded ``$`` breaks non-USD books.
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

    Layout::

        Kill order (USD 10,000/mo → debt-free Apr 2030, USD 59,022 interest):
          1. Business Amex    USD 13,091  24.49%  payoff: mo 8   interest: USD 1,125
        YETI at this budget: 1.59x (USD 1 spent costs USD 1.59 in total debt impact)
        Total interest: USD 59,022
        Debt-free: April 2030
    """
    # Leaf names + width-padded columns.
    leaf_names = [d["name"].split(":")[-1] for d in results]
    name_width = max(len(n) for n in leaf_names) if leaf_names else 0
    balance_strs = [
        _money_compact(orig_balances[d["name"]], currency) for d in results
    ]
    balance_width = max(len(b) for b in balance_strs) if balance_strs else 0
    interest_strs = [
        _money_compact(d["interest_paid"], currency) for d in results
    ]
    interest_width = (
        max(len(s) for s in interest_strs) if interest_strs else 0
    )

    payoff_month_name = payoff_date.strftime("%b %Y")
    header = (
        f"Kill order ({_money_compact(monthly_budget, currency)}/mo → "
        f"debt-free {payoff_month_name}, "
        f"{_money_compact(total_interest, currency)} interest):"
    )

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

    # YETI speaks plainly — it's the actionable signal.
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

    def _monthly_conversion_factors(
        self,
        book: piecash.Book,
        start_date: date,
        end_date: date,
    ) -> dict[str, dict[str, Decimal | None]]:
        """``{YYYY-MM: {account_guid: factor}}`` covering the range —
        the FLOW-report valuation quantum (GB-1 ruling, 2026-07-07).

        Flow reports (spending / income / cash_flow) value every
        split at its own MONTH's closing rate, in single-period and
        group_by modes alike. Month is the quantum because it makes
        totals granularity-invariant (quarter/year/single are sums of
        month-valued splits), matches the ``group_by="month"``
        numbers users had already seen before unification, and is a
        recognizable accounting convention (monthly close). Anchors
        clamp to ``end_date`` via ``_enumerate_periods``, so a
        partial final month values at the range end and the
        forecast-price convention (``_anchor_for_as_of``) applies
        through ``_account_conversion_factors`` as everywhere else.

        STOCK reports (balance_sheet, net_worth) are deliberately
        different: they value holdings as of their report date, not
        per flow month.
        """
        return {
            pl: self._account_conversion_factors(book, anchor)
            for pl, anchor in _enumerate_periods(
                start_date, end_date, "month",
            )
        }

    @staticmethod
    def _monthly_factor(
        monthly_factors: dict[str, dict[str, Decimal | None]],
        txn,
        account,
    ) -> Decimal | None:
        """The conversion factor for one split under the monthly
        quantum: its transaction's month, its account. ``None`` (no
        rate on file that month, or a month outside the built range)
        falls back to ``split.value`` in
        ``_split_in_default_currency`` — the same degradation as
        every other missing-rate path.
        """
        month = _period_label(txn.post_date, "month")
        return monthly_factors.get(month, {}).get(account.guid)

    def _grouped_breakdown(
        self,
        book: piecash.Book,
        *,
        start_date: date,
        end_date: date,
        depth: int,
        account_type: str,
        sign: Decimal,
        group_by: str,
        label: str,
    ) -> str:
        """Multi-period breakdown shared by spending/income.

        One indexed pass over the range's splits, bucketed by
        ``(group_account, sub-period)``. ``sign`` flips income's
        stored-negative convention (``-1``) vs spending (``1``); the
        rest is identical to the single-period path — same ``depth``
        grouping, same net-after-aggregation rule (a category whose
        total across ALL periods is ≤ 0 is dropped from the rows but
        still netted into the column totals).
        """
        periods = _enumerate_periods(start_date, end_date, group_by)
        period_labels = [pl for pl, _ in periods]
        # MONTHLY-close valuation regardless of display granularity
        # (GB-1 ruling, 2026-07-07): every split converts at its own
        # month's closing rate, and quarter/year columns are sums of
        # month-valued splits. This is what makes totals
        # granularity-invariant — the same range reports the same
        # grand total whether viewed single-period, by month, by
        # quarter, or by year (locked by TestModeAgreement). Never
        # today's rates against a historical period's quantities.
        monthly_factors = self._monthly_conversion_factors(
            book, start_date, end_date,
        )

        rows = self._query_filtered_splits(
            book,
            start_date=start_date,
            end_date=end_date,
            account_types=frozenset({account_type}),
        )

        # category fullname → {period_label: signed Decimal}
        label_set = set(period_labels)
        totals: dict[str, dict[str, Decimal]] = {}
        for split, txn, account in rows:
            plabel = _period_label(txn.post_date, group_by)
            if plabel not in label_set:
                # _query_filtered_splits bounds post_date to
                # [start, end], so every label is enumerated; a stray
                # one means that invariant broke upstream. Skip with
                # a trace rather than KeyError-ing the whole report.
                debug_logger.warning(
                    f"group_by split outside enumerated periods: "
                    f"{plabel} not in {period_labels[0]}..."
                    f"{period_labels[-1]}"
                )
                continue
            factor = self._monthly_factor(monthly_factors, txn, account)
            amount = sign * self._split_in_default_currency(
                split, account, factor,
            )
            group_account = self._get_account_at_depth(account, depth)
            bucket = totals.setdefault(group_account.fullname, {})
            bucket[plabel] = bucket.get(plabel, Decimal("0")) + amount

        cat_totals = {
            name: sum(per.values(), Decimal("0"))
            for name, per in totals.items()
        }
        displayed_names = sorted(
            (n for n, t in cat_totals.items() if t > 0),
            key=lambda n: cat_totals[n],
            reverse=True,
        )
        excluded = [(n, t) for n, t in cat_totals.items() if t < 0]

        # Column totals sum every category — net-negative ones netted
        # in even though they have no row, so the totals match
        # single-period mode.
        period_totals = {pl: Decimal("0") for pl in period_labels}
        for per in totals.values():
            for pl, v in per.items():
                period_totals[pl] += v
        grand_total = sum(cat_totals.values(), Decimal("0"))

        return _format_grouped_tsv(
            period_labels=period_labels,
            displayed_names=displayed_names,
            totals=totals,
            row_totals=cat_totals,
            period_totals=period_totals,
            grand_total=grand_total,
            excluded=excluded,
            label=label,
            partial_labels=_partial_period_labels(
                start_date, end_date, group_by,
            ),
        )

    def spending_by_category(
        self,
        start_date: date,
        end_date: date,
        depth: int = 1,
        compact: bool = True,
        group_by: str | None = None,
    ) -> dict | str:
        """Get spending breakdown by expense category.

        Internal transfers are NOT filtered (unlike ``cash_flow``):
        every EXPENSE split is real spending by definition.

        Args:
            start_date / end_date: Period bounds (inclusive).
            depth: Grouping depth — ``1`` = top-level buckets
                (``Expenses:Food``), ``2`` adds one level of
                children; values past the tree depth clamp to the
                leaf.
            compact: Aligned text table (default) or the structured
                dict.
            group_by: ``None`` (default) for the single-period
                aggregation above; ``"month"`` / ``"quarter"`` /
                ``"year"`` to split the range into sub-period columns
                and return a multi-period TSV table (always a string;
                ``compact`` is ignored — the table is the output).
        """
        if group_by is not None:
            if group_by not in _GROUP_BY_VALUES:
                raise ValueError(
                    f"Invalid group_by '{group_by}'. Must be one of: "
                    f"{', '.join(_GROUP_BY_VALUES)}."
                )
            with self.open(readonly=True) as book:
                return self._grouped_breakdown(
                    book,
                    start_date=start_date,
                    end_date=end_date,
                    depth=depth,
                    account_type="EXPENSE",
                    sign=Decimal("1"),
                    group_by=group_by,
                    label="Category",
                )

        with self.open(readonly=True) as book:
            rows = self._query_filtered_splits(
                book,
                start_date=start_date,
                end_date=end_date,
                account_types=frozenset({"EXPENSE"}),
            )

            # Monthly-close valuation (GB-1): each split converts
            # at its own month's rate, so this total equals the
            # group_by grand total for the same range — never
            # today's rates against historical quantities.
            monthly_factors = self._monthly_conversion_factors(
                book, start_date, end_date,
            )

            totals: dict[str, Decimal] = {}
            for split, txn, account in rows:
                # Accumulate SIGNED amounts so refunds net against
                # spending within a category — dropping negatives
                # would report GROSS spend.
                amount = self._split_in_default_currency(
                    split, account,
                    self._monthly_factor(monthly_factors, txn, account),
                )
                # ``depth`` (not ``depth - 1``): path[0] is the type
                # root ("Expenses"), so "depth 1 = Expenses:Food"
                # needs path[1]. The off-by-one collapses the whole
                # report to one "Expenses 100%" row.
                group_account = self._get_account_at_depth(
                    account, depth
                )
                account_name = group_account.fullname
                totals[account_name] = totals.get(
                    account_name, Decimal("0")
                ) + amount

            # Net decision AFTER aggregation: a net-refunded category
            # isn't a spend LINE but still belongs in the TOTAL —
            # dropping it makes the total depth-dependent and unties
            # income − spending from net income. Net-negative groups
            # surface explicitly instead of vanishing.
            displayed = {n: a for n, a in totals.items() if a > 0}
            excluded = {n: a for n, a in totals.items() if a < 0}
            total = (
                sum(totals.values()) if totals else Decimal("0")
            )
            categories = []
            for account_name, amount in sorted(
                displayed.items(), key=lambda x: x[1], reverse=True
            ):
                percent = (
                    (amount / total * 100) if total > 0 else Decimal("0")
                )
                categories.append({
                    "account": account_name,
                    "amount": str(amount),
                    "percent": str(percent.quantize(Decimal("0.1"))),
                })
            excluded_rows = [
                {"account": n, "amount": str(a)}
                for n, a in sorted(excluded.items(), key=lambda x: x[1])
            ]

            if compact:
                out = _format_breakdown_tsv(categories, total, "account")
                if excluded_rows:
                    netted = ", ".join(
                        f"{r['account']} {Decimal(r['amount']):,.2f}"
                        for r in excluded_rows
                    )
                    out += (
                        f"\n({len(excluded_rows)} net-refunded "
                        f"netted into TOTAL: {netted})"
                    )
                return out
            # period is an input echo — LLM supplied start/end dates.
            result = {
                "total": str(total),
                "categories": categories,
            }
            if excluded_rows:
                result["net_negative_netted"] = excluded_rows
            return result

    def income_by_source(
        self,
        start_date: date,
        end_date: date,
        depth: int = 1,
        compact: bool = True,
        group_by: str | None = None,
    ) -> dict | str:
        """Get income breakdown by source.

        Mirror of ``spending_by_category`` (same depth semantics,
        same net-after-aggregation rule) with income's sign flipped.

        Args:
            start_date / end_date: Period bounds (inclusive).
            depth: Grouping depth (1 = top-level).
            compact: Aligned text table (default) or structured dict.
            group_by: ``None`` (default) for single-period; ``"month"``
                / ``"quarter"`` / ``"year"`` for a multi-period TSV
                table — see ``spending_by_category``.
        """
        if group_by is not None:
            if group_by not in _GROUP_BY_VALUES:
                raise ValueError(
                    f"Invalid group_by '{group_by}'. Must be one of: "
                    f"{', '.join(_GROUP_BY_VALUES)}."
                )
            with self.open(readonly=True) as book:
                return self._grouped_breakdown(
                    book,
                    start_date=start_date,
                    end_date=end_date,
                    depth=depth,
                    account_type="INCOME",
                    sign=Decimal("-1"),
                    group_by=group_by,
                    label="Source",
                )

        with self.open(readonly=True) as book:
            rows = self._query_filtered_splits(
                book,
                start_date=start_date,
                end_date=end_date,
                account_types=frozenset({"INCOME"}),
            )

            # Monthly-close valuation (GB-1) — see
            # spending_by_category.
            monthly_factors = self._monthly_conversion_factors(
                book, start_date, end_date,
            )

            totals: dict[str, Decimal] = {}
            for split, txn, account in rows:
                # Income is stored negative; flip. Signed
                # accumulation so losses/clawbacks net against gains
                # within a source — see spending_by_category.
                amount = -self._split_in_default_currency(
                    split, account,
                    self._monthly_factor(monthly_factors, txn, account),
                )
                # ``depth`` not ``depth - 1`` — same off-by-one
                # hazard as spending_by_category.
                group_account = self._get_account_at_depth(
                    account, depth
                )
                account_name = group_account.fullname
                totals[account_name] = totals.get(
                    account_name, Decimal("0")
                ) + amount

            # Net decision AFTER aggregation — see
            # spending_by_category.
            displayed = {n: a for n, a in totals.items() if a > 0}
            excluded = {n: a for n, a in totals.items() if a < 0}
            total = (
                sum(totals.values()) if totals else Decimal("0")
            )
            sources = []
            for account_name, amount in sorted(
                displayed.items(), key=lambda x: x[1], reverse=True
            ):
                percent = (
                    (amount / total * 100) if total > 0 else Decimal("0")
                )
                sources.append({
                    "account": account_name,
                    "amount": str(amount),
                    "percent": str(percent.quantize(Decimal("0.1"))),
                })
            excluded_rows = [
                {"account": n, "amount": str(a)}
                for n, a in sorted(excluded.items(), key=lambda x: x[1])
            ]

            if compact:
                out = _format_breakdown_tsv(sources, total, "account")
                if excluded_rows:
                    netted = ", ".join(
                        f"{r['account']} {Decimal(r['amount']):,.2f}"
                        for r in excluded_rows
                    )
                    out += (
                        f"\n({len(excluded_rows)} net-loss "
                        f"netted into TOTAL: {netted})"
                    )
                return out
            # period is an input echo — LLM supplied start/end dates.
            result = {
                "total": str(total),
                "sources": sources,
            }
            if excluded_rows:
                result["net_negative_netted"] = excluded_rows
            return result

    # ── Balance sheet and net worth ──────────────────────────────────

    def balance_sheet(self, as_of_date: date) -> dict:
        """Generate a balance sheet as of a specific date.

        Equity includes a computed **Unrealized gain/loss** line —
        the residual that makes ``A = L + E`` hold by construction.
        Assets render at market value while equity rolls up from raw
        split values (historical cost); the residual captures both
        investment market drift (shares × price vs cost basis) and
        FX translation adjustment on foreign-currency accounts.
        Both are accumulated-OCI items under GAAP; decomposing them
        is a future feature — the single line preserves the identity
        for any mix.

        ``as_of_date`` is the inclusive upper bound; FX rates and
        commodity prices anchor to the same date for a coherent
        historical view.

        Returns:
            Dict with ``assets`` / ``liabilities`` / ``equity``
            sections, each carrying a total and account rows;
            equity already includes the Unrealized line so the
            totals balance.
        """
        # One SQL-filtered pass over every relevant type, bucketed in
        # memory. Net income (retained-earnings-equivalent) rolls
        # into equity below.
        all_types = (
            _ASSET_TYPES | _LIABILITY_TYPES | _EQUITY_TYPES | _NET_INCOME_TYPES
        )
        with self.open(readonly=True) as book:
            # Rates as of the report date — never today's rates
            # against historical quantities.
            factors = self._account_conversion_factors(book, as_of_date)
            rows = self._query_filtered_splits(
                book,
                end_date=as_of_date,
                account_types=all_types,
            )

            # Per-account: type, converted balance, plus quantity /
            # commodity for the "230.76 VTSAX @ 156.23 (USD …)"
            # triplet on non-default-currency rows.
            balances: dict[str, dict] = {}
            net_income = Decimal("0")
            for split, _txn, account in rows:
                # Voided by state, not value: a well-formed void
                # contributes 0 anyway; the corrupted partial-void
                # shape (state='v', non-zero values) must not move
                # the sheet when cash_flow / lots can't see it.
                if _is_voided(split):
                    continue
                # Placeholder accounts are NOT skipped: there is no
                # roll-up in this report, so direct splits on a
                # placeholder — rare but legal — are real money no
                # other row represents. Skipping them would guard a
                # double-count this code never produces; with the
                # balancing-residual equity line it silently deletes
                # the dropped asset instead. Same own-splits rule as
                # ``net_worth`` and ``_compute_net_worth_at``.
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
            # Display rates anchored to the report date, market
            # prices only (see _rates_as_of).
            latest_rates = self._rates_as_of(book, as_of_date)
            # "(via …)" provenance for chained rates.
            rate_via = self._rate_provenance(
                book, as_of_date, default_currency,
            )

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

            # ── Unrealized gain/loss (balancing adjustment) ──────────
            # Computed as the residual
            # assets − liabilities − equity − net_income, so
            # A = L + E holds by construction regardless of the mix
            # of investment drift and FX translation — see the
            # docstring for the decomposition.
            equity_acct_total = (
                sum(i["usd"] for i in equity.values()) if equity else Decimal("0")
            )
            unrealized = (
                assets_total - liabilities_total
                - equity_acct_total - net_income
            )
            equity_total = equity_acct_total + net_income + unrealized

            def format_accounts(accounts_dict: dict[str, dict]) -> list[dict]:
                """Render each account row with the right balance shape.

                Default-currency accounts: ``{"account", "balance"}``.
                Non-currency accounts: ``balance`` is the readable
                triplet ``"230.76 VTSAX @ 156.23 (USD 36,043.66)"``
                and ``default_currency_value`` carries the parseable
                number. Numbers flow through ``_format_number`` so
                responses don't leak Decimal precision noise.
                """
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
                            via = rate_via.get(commodity.guid)
                            via_note = f", {via}" if via else ""
                            balance_str = (
                                f"{qty} {sym} @ {rate} "
                                f"({ccy_mnemonic} {info['usd']:,.2f}"
                                f"{via_note})"
                            )
                        else:
                            # No price — cost basis already
                            # accumulated in info['usd'].
                            balance_str = (
                                f"{qty} {sym} ({ccy_mnemonic} "
                                f"{info['usd']:,.2f}, no price data)"
                            )
                        rows.append({
                            "account": name,
                            "balance": balance_str,
                            "default_currency_value": default_value_rounded,
                        })
                return rows

            # as_of_date is an input echo, kept — cheap and useful
            # when a log is reviewed out of context. A ``balanced``
            # field would be derivable and is deliberately omitted.
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
                    ) + (
                        # Synthetic, signed line — positive =
                        # unrealized gain (see docstring).
                        [{
                            "account": "Unrealized Gain/Loss",
                            "balance": _format_number(unrealized, decimals=2),
                        }]
                        if unrealized != 0 else []
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
            # --- Point-in-time: one filtered SQL query, sum in Python.
            if not start_date or not interval:
                factors = self._account_conversion_factors(book, end_date)
                rows = self._query_filtered_splits(
                    book,
                    end_date=end_date,
                    account_types=nw_types,
                )
                total = Decimal("0")
                for split, _txn, account in rows:
                    if _is_voided(split):
                        continue
                    # Liabilities are stored negative, so a direct
                    # sum gives assets minus liabilities.
                    total += self._split_in_default_currency(
                        split, account, factors.get(account.guid)
                    )
                return {
                    "as_of_date": end_date.isoformat(),
                    "net_worth": str(total),
                }

            # --- Time series: single sweep, per-boundary valuation.
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

            # Interval boundaries up-front; end_date appended if the
            # interval didn't land on it naturally.
            boundaries: list[date] = []
            cursor = start_date
            while cursor <= end_date:
                boundaries.append(cursor)
                cursor += delta
            if not boundaries or boundaries[-1] != end_date:
                boundaries.append(end_date)

            # Per-boundary factors — each historical snapshot values
            # at the rate of its own date, not today's. Costs
            # O(boundaries) factors-builds; correctness over speed.
            factors_by_boundary: dict[date, dict[str, Decimal | None]] = {
                b: self._account_conversion_factors(book, b)
                for b in boundaries
            }

            # Single pass in post_date order. Under per-boundary
            # rates a single running total can't be carried forward;
            # track per-account quantity AND value totals and convert
            # at snapshot time with that boundary's factors. Cost:
            # O(splits + boundaries × accounts_with_splits).
            rows = self._query_filtered_splits(
                book,
                end_date=end_date,
                account_types=nw_types,
                order_by_post_date=True,
            )

            running_qty: dict[str, Decimal] = {}
            running_value: dict[str, Decimal] = {}

            def _snapshot_at(boundary: date) -> Decimal:
                """Net worth at ``boundary`` using that date's
                factors: per-account factor × quantity, cost-basis
                fallback — ``_split_in_default_currency``'s
                disambiguation lifted to account level."""
                factors_here = factors_by_boundary[boundary]
                total = Decimal("0")
                for acct_guid, qty in running_qty.items():
                    factor = factors_here.get(acct_guid)
                    if factor is not None:
                        total += qty * factor
                    else:
                        total += running_value[acct_guid]
                return total

            series: list[dict] = []
            b_idx = 0

            for split, txn, account in rows:
                # Snapshot every boundary STRICTLY BEFORE
                # this split. The strict ``>`` is correct and
                # deliberate: a boundary equal to a split's
                # post_date includes that split in its snapshot
                # (the boundary is "end of day", so a transaction
                # posted that day has happened by then). The
                # cumulative running totals above this loop
                # advance after the snapshot is taken, so the
                # snapshot reflects every prior split AND every
                # split posted on the boundary date — matching
                # the inclusive-end semantics ``_query_filtered_splits``
                # enforces at the SQL boundary. A ``>=`` here
                # would exclude same-day splits, silently breaking
                # the trajectory's tie to ``balance_sheet(as_of)``.
                while (
                    b_idx < len(boundaries)
                    and txn.post_date > boundaries[b_idx]
                ):
                    series.append({
                        "date": boundaries[b_idx].isoformat(),
                        "net_worth": str(_snapshot_at(boundaries[b_idx])),
                    })
                    b_idx += 1
                # Voided filter placed after the boundary advance so
                # a voided split still flushes due snapshots, just
                # never accumulates.
                if _is_voided(split):
                    continue
                acct_guid = account.guid
                running_qty[acct_guid] = (
                    running_qty.get(acct_guid, Decimal("0"))
                    + Decimal(str(split.quantity))
                )
                running_value[acct_guid] = (
                    running_value.get(acct_guid, Decimal("0"))
                    + Decimal(str(split.value))
                )

            # Drain boundaries past the last split — each still uses
            # its own factors.
            while b_idx < len(boundaries):
                series.append({
                    "date": boundaries[b_idx].isoformat(),
                    "net_worth": str(_snapshot_at(boundaries[b_idx])),
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
        include_transfers: bool = False,
        group_by: str | None = None,
    ) -> dict | str:
        """Calculate cash flow (inflows and outflows) for a period.

        Scope: BANK and CASH accounts only. Credit-card movements
        are liability changes and investment movements asset
        rearrangements — not "cash flow" in the bookkeeper's sense.
        An explicit ``account=`` of any type still works.

        By default, internal transfers (transactions with no INCOME
        or EXPENSE leg — savings transfers, wallet shuffles, card
        paydowns) are filtered out so the totals answer "where did
        money come from and go?" rather than echoing same-pocket
        reshuffling. Invoice/bill settlements still count as real
        flow despite having no income/expense split — see
        ``_cashflow_txn_guids``.

        Args:
            start_date / end_date: Period bounds (inclusive).
            account: Optional single-account filter.
            include_transfers: True includes every cash movement —
                useful for reconciling against a bank statement.
            group_by: ``None`` (default) for the single-period dict;
                ``"month"`` / ``"quarter"`` / ``"year"`` to split the
                range into sub-period columns and return an
                Inflows / Outflows / Net trend table (a TSV string).

        Returns:
            ``{account, inflows, outflows}`` plus, when transfers
            were filtered, ``transfers_excluded`` — the count of
            distinct cash-touching transactions skipped (surfaced so
            the LLM can mention the ``include_transfers`` escape
            hatch). With ``group_by``, a multi-period TSV table.
        """
        if group_by is not None and group_by not in _GROUP_BY_VALUES:
            raise ValueError(
                f"Invalid group_by '{group_by}'. Must be one of: "
                f"{', '.join(_GROUP_BY_VALUES)}."
            )
        with self.open(readonly=True) as book:
            # Named-account or all-cash filter; both push to SQL.
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
                account_label = target_account.fullname
            else:
                rows = self._query_filtered_splits(
                    book,
                    start_date=start_date,
                    end_date=end_date,
                    account_types=_CASH_TYPES,
                )
                account_label = "All cash/bank accounts"

            # GUIDs of "real" cash-flow transactions; everything
            # else is a transfer unless include_transfers.
            if not include_transfers:
                cashflow_txn_guids = self._cashflow_txn_guids(
                    book, start_date, end_date
                )
            else:
                cashflow_txn_guids = None  # don't filter

            if group_by is not None:
                return self._grouped_cash_flow(
                    book,
                    rows=rows,
                    start_date=start_date,
                    end_date=end_date,
                    cashflow_txn_guids=cashflow_txn_guids,
                    group_by=group_by,
                    account_label=account_label,
                )

            # Monthly-close valuation (GB-1) — see
            # spending_by_category.
            monthly_factors = self._monthly_conversion_factors(
                book, start_date, end_date,
            )
            inflows = Decimal("0")
            outflows = Decimal("0")
            transfers_excluded: set[str] = set()
            for split, txn, acct in rows:
                # Skip voided splits BEFORE the transfer-vs-real
                # classification so they neither inflate
                # transfers_excluded nor pollute the accumulators.
                if _is_voided(split):
                    continue
                if cashflow_txn_guids is not None \
                        and txn.guid not in cashflow_txn_guids:
                    transfers_excluded.add(txn.guid)
                    continue
                amt = self._split_in_default_currency(
                    split, acct,
                    self._monthly_factor(monthly_factors, txn, acct),
                )
                if amt > 0:
                    inflows += amt
                elif amt < 0:
                    outflows += -amt

            # period (input echo) and net (derivable) are dropped.
            # The canonical fullname is echoed so %short/GUID input
            # still yields a readable name.
            result = {
                "account": account_label,
                "inflows": str(inflows),
                "outflows": str(outflows),
            }
            if transfers_excluded:
                result["transfers_excluded"] = len(transfers_excluded)
            return result

    def _grouped_cash_flow(
        self,
        book: piecash.Book,
        *,
        rows,
        start_date: date,
        end_date: date,
        cashflow_txn_guids: set[str] | None,
        group_by: str,
        account_label: str,
    ) -> str:
        """Bucket the cash-flow splits into per-period inflows/outflows.

        Same classification as the single-period path — voided splits
        skipped, internal transfers excluded unless the caller passed
        ``cashflow_txn_guids=None`` — with each split landing in its
        post_date's sub-period and converting at its MONTH's close
        (the flow-report valuation quantum; see
        ``_monthly_conversion_factors``).
        """
        periods = _enumerate_periods(start_date, end_date, group_by)
        period_labels = [pl for pl, _ in periods]
        # Monthly-close valuation regardless of display granularity
        # (GB-1) — see _monthly_conversion_factors.
        monthly_factors = self._monthly_conversion_factors(
            book, start_date, end_date,
        )

        inflows = {pl: Decimal("0") for pl in period_labels}
        outflows = {pl: Decimal("0") for pl in period_labels}
        transfers_excluded: set[str] = set()
        for split, txn, acct in rows:
            if _is_voided(split):
                continue
            if cashflow_txn_guids is not None \
                    and txn.guid not in cashflow_txn_guids:
                transfers_excluded.add(txn.guid)
                continue
            plabel = _period_label(txn.post_date, group_by)
            if plabel not in inflows:
                # Same invariant guard as _grouped_breakdown: the SQL
                # date bound guarantees containment; don't KeyError
                # the report if it ever breaks.
                debug_logger.warning(
                    f"group_by split outside enumerated periods: "
                    f"{plabel}"
                )
                continue
            amt = self._split_in_default_currency(
                split, acct,
                self._monthly_factor(monthly_factors, txn, acct),
            )
            if amt > 0:
                inflows[plabel] += amt
            elif amt < 0:
                outflows[plabel] += -amt

        return _format_grouped_cashflow_tsv(
            period_labels=period_labels,
            inflows=inflows,
            outflows=outflows,
            account_label=account_label,
            transfers_excluded=len(transfers_excluded),
            partial_labels=_partial_period_labels(
                start_date, end_date, group_by,
            ),
        )

    def _cashflow_txn_guids(
        self,
        book: piecash.Book,
        start_date: date,
        end_date: date,
    ) -> set[str]:
        """Set of transaction GUIDs in the period that count as real
        cash-flow events rather than internal transfers (the
        ``cash_flow`` filter).

        Two qualifying shapes:

        1. A non-voided INCOME or EXPENSE split — ordinary
           earn/spend.
        2. A non-voided **lot-linked** RECEIVABLE/PAYABLE split —
           an invoice/bill settlement. Structurally a transfer, but
           it IS the revenue receipt; an income/expense-only filter
           drops it unless FX drift happens to add a realized-FX
           split, making classification depend on rate movement.
           The lot link is deterministic; manual A/R adjustments
           without a lot stay transfers.

        Routes through ``_query_filtered_splits`` for the inclusive
        bound and template/null-date filters; the voided filter is
        Python-side so a voided leg can't rescue a zombie
        transaction.
        """
        rows = self._query_filtered_splits(
            book,
            start_date=start_date,
            end_date=end_date,
            account_types=_NET_INCOME_TYPES,
        )
        guids = {
            txn.guid for split, txn, _acct in rows
            if not _is_voided(split)
        }
        settlement_rows = self._query_filtered_splits(
            book,
            start_date=start_date,
            end_date=end_date,
            account_types=frozenset({"RECEIVABLE", "PAYABLE"}),
        )
        guids.update(
            txn.guid for split, txn, _acct in settlement_rows
            if not _is_voided(split) and split.lot is not None
        )
        return guids

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
        """Calculate an avalanche-method debt payoff schedule with
        YETI multiplier.

        Auto-discovers CREDIT/LIABILITY accounts with an 'apr' slot.
        YETI (Your Expense's True Impact) answers: "a $1.00 purchase
        will cost you $X.XX by the time your debt is paid off."

        Args:
            monthly_budget: Total monthly amount for all debt payments.
            additional_purchase: Amount to compute YETI for
                (default "1.00").
            compact: Kill-order text table (default — see
                ``_format_debt_payoff_compact`` for the layout) or
                the full structured dict.

        Raises:
            ValueError: no debt accounts found, invalid budget, or
                budget below the sum of minimum payments.
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
            default_currency = self._require_default_currency(book)
            default_currency_mnemonic = default_currency.mnemonic
            debt_types = {"CREDIT", "LIABILITY"}
            debts = []
            # Foreign-currency debts with no FX rate on file can't be
            # valued in the book default; collected here and excluded
            # from the schedule (see the loop guard below).
            excluded_debts: list[str] = []
            # LIABILITY accounts with an APR and balance but no way to
            # estimate a payment (neither minimum_payment nor
            # loan_term_months slot). Omitted from the plan rather than
            # estimated from a guessed term — see the LIABILITY branch.
            unestimable_debts: list[str] = []
            # Debt-typed accounts CARRYING A BALANCE but lacking a
            # usable 'apr' slot — the plan can't order or cost them.
            # Confessed alongside the other two exclusion classes; a
            # silently partial payoff plan reads as a complete one.
            no_apr_debts: list[str] = []
            # Counted so the no-debts error distinguishes "no debt
            # accounts at all" from "they exist but lack the apr
            # slot" — the user's next action differs.
            debt_typed_account_count = 0

            # Defense-in-depth template filter (a template could
            # theoretically carry an apr slot).
            template_guids = self._template_account_guids(book)

            # FX factors so a foreign-currency debt values in the
            # book default instead of raw foreign units. "Now"
            # report → today's rates.
            debt_factors = self._account_conversion_factors(
                book, date.today()
            )

            for account in book.accounts:
                if account.guid in template_guids:
                    continue
                if account.type not in debt_types:
                    continue
                debt_typed_account_count += 1

                # A foreign-currency debt with no FX rate on file can't
                # be valued in the book default: its balance would fall
                # back to raw transaction-currency value while its
                # min_payment / credit_limit slots stay in account-
                # commodity units — mixing units in the payoff math.
                # Exclude it honestly rather than emit a skewed schedule.
                if (
                    account.commodity != default_currency
                    and debt_factors.get(account.guid) is None
                ):
                    excluded_debts.append(account.fullname)
                    continue

                # Materialize slots once — three account[key]
                # accesses each re-walk the slots collection.
                slot_by_name = {s.name: s for s in account.slots}

                # Balance FIRST (book default, today-capped — a
                # payment scheduled next week hasn't reduced today's
                # payoff balance; voided and null-date splits
                # excluded), so a missing APR on a balance-carrying
                # debt is CONFESSED below instead of silently
                # dropped; zero-balance accounts stay silent either
                # way — a closed old card is not a gap in the plan.
                today = date.today()
                balance = Decimal("0")
                for split in account.splits:
                    if _is_voided(split):
                        continue
                    post_date = split.transaction.post_date
                    if post_date is None or post_date > today:
                        continue
                    balance += self._split_in_default_currency(
                        split, account, debt_factors.get(account.guid)
                    )
                balance = -balance  # Convert to positive amount owed

                if balance <= 0:
                    continue  # Skip zero or overpaid balances

                apr_val = slot_by_name.get("apr")
                apr = None
                if apr_val is not None:
                    try:
                        apr_str = str(apr_val.value) if hasattr(apr_val, "value") else str(apr_val)
                        apr = Decimal(apr_str)
                    except InvalidOperation:
                        apr = None
                if apr is None or apr <= 0:
                    no_apr_debts.append(account.fullname)
                    continue

                min_payment = None

                # 1. minimum_payment slot — user-declared contractual
                #    amount wins for both types.
                mp_val = slot_by_name.get("minimum_payment")
                if mp_val is not None:
                    try:
                        mp_str = str(mp_val.value) if hasattr(mp_val, "value") else str(mp_val)
                        min_payment = Decimal(mp_str)
                    except InvalidOperation:
                        pass
                # Slot scalars are in the ACCOUNT's commodity by
                # convention; the plan's math runs in book default —
                # convert with the balance's factor, or a ¥2,000
                # minimum reads as $2,000 and skews the schedule.
                if min_payment is not None:
                    slot_factor = debt_factors.get(account.guid)
                    if slot_factor is not None and slot_factor != 1:
                        min_payment = (
                            min_payment * slot_factor
                        ).quantize(Decimal("0.01"))

                # 2. Type-aware fallback: CREDIT cards charge ~2% of
                #    balance; LIABILITY loans amortize. Applying the
                #    2% rule to a mortgage yields ~3-4× the actual
                #    payment and trips the budget gate on any
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
                        # LIABILITY: amortization formula
                        # PMT = P × r(1+r)^n / ((1+r)^n − 1).
                        # Term comes ONLY from the `loan_term_months`
                        # slot. There is no "mortgage" account type to
                        # key off, and no safe default term: a 30y-vs-5y
                        # guess differs by an order of magnitude, so a
                        # guessed estimate is wrong enough to be worse
                        # than none (a low guess understates the payment;
                        # a high one trips the budget gate). Without the
                        # slot — or an explicit `minimum_payment` above —
                        # omit this debt from the plan and tell the user
                        # what to set, rather than fabricate a figure.
                        term_months = None
                        lt_val = slot_by_name.get("loan_term_months")
                        if lt_val is not None:
                            try:
                                lt_str = (
                                    str(lt_val.value)
                                    if hasattr(lt_val, "value")
                                    else str(lt_val)
                                )
                                parsed = int(Decimal(lt_str))
                                if parsed > 0:
                                    term_months = parsed
                            except (InvalidOperation, ValueError):
                                pass
                        if term_months is None:
                            unestimable_debts.append(account.fullname)
                            continue
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

                credit_limit = None  # account-ccy slot; converted below
                cl_val = slot_by_name.get("credit_limit")
                if cl_val is not None:
                    try:
                        cl_str = str(cl_val.value) if hasattr(cl_val, "value") else str(cl_val)
                        credit_limit = Decimal(cl_str)
                        slot_factor = debt_factors.get(account.guid)
                        if slot_factor is not None and slot_factor != 1:
                            credit_limit = (
                                credit_limit * slot_factor
                            ).quantize(Decimal("0.01"))
                    except InvalidOperation:
                        pass

                debts.append({
                    "name": account.fullname,
                    "balance": balance,
                    "apr": apr,
                    # Pre-computed once — _run_avalanche's loop runs
                    # up to 1200 months per debt.
                    "monthly_rate": apr / Decimal("100") / Decimal("12"),
                    "min_payment": min_payment,
                    "credit_limit": credit_limit,
                })

        # Warning shared by the all-excluded error and the normal-path
        # output, so the reader always learns what was left out.
        excluded_warning = None
        if excluded_debts:
            excluded_warning = (
                f"{len(excluded_debts)} debt(s) excluded — no FX rate on "
                f"file to value in {default_currency_mnemonic}: "
                f"{', '.join(sorted(excluded_debts))}"
            )

        # Debts omitted because their payment can't be estimated without
        # guessing the amortization term — surfaced so the reader knows
        # the plan is partial and exactly what to set to complete it.
        unestimable_warning = None
        if unestimable_debts:
            unestimable_warning = (
                f"{len(unestimable_debts)} debt(s) omitted — no "
                f"'loan_term_months' or 'minimum_payment' slot, so the "
                f"payment can't be estimated without guessing the loan "
                f"term: {', '.join(sorted(unestimable_debts))}. Set "
                f"loan_term_months (or minimum_payment) via "
                f"set_account_slot for payment estimates."
            )

        # Balance-carrying debts invisible to the plan for lack of an
        # APR — the third exclusion class, same confession contract.
        no_apr_warning = None
        if no_apr_debts:
            no_apr_warning = (
                f"{len(no_apr_debts)} balance-carrying debt(s) not in "
                f"the plan — no 'apr' slot (or APR <= 0): "
                f"{', '.join(sorted(no_apr_debts))}. Set 'apr' via "
                f"set_account_slot to include them in the payoff "
                f"order."
            )

        if not debts:
            # Nothing left to plan but some debts were excluded for lack
            # of an FX rate — lead with that actionable cause (distinct
            # from "no debts at all" or "no APR set").
            if excluded_debts:
                msg = (
                    f"No debts could be valued for the payoff plan. "
                    f"{len(excluded_debts)} debt(s) are in a non-default "
                    f"currency with no FX rate on file to value in "
                    f"{default_currency_mnemonic}: "
                    f"{', '.join(sorted(excluded_debts))}. Add a market "
                    f"price (create_price) for each currency"
                )
                if debt_typed_account_count > len(excluded_debts):
                    msg += (
                        ", and set an 'apr' slot on the remaining debt "
                        "account(s)."
                    )
                else:
                    msg += " and retry."
                raise ValueError(msg)
            if debt_typed_account_count == 0:
                raise ValueError(
                    "No CREDIT or LIABILITY accounts found in the "
                    "chart of accounts. Create the debt account(s) "
                    "first via create_account, then set their APR "
                    "via set_account_slot."
                )
            # Distinct from the apr-missing case below: these debts have
            # an APR and balance but no term/payment to estimate from.
            if unestimable_debts:
                raise ValueError(
                    f"{len(unestimable_debts)} loan/liability account(s) "
                    f"have an APR and balance but no way to estimate a "
                    f"minimum payment: "
                    f"{', '.join(sorted(unestimable_debts))}. Set a "
                    f"'loan_term_months' (amortization term) or "
                    f"'minimum_payment' slot on each via "
                    f"set_account_slot."
                )
            raise ValueError(
                f"Found {debt_typed_account_count} CREDIT/LIABILITY "
                f"account(s) but none have an 'apr' slot set "
                f"(or every APR is <= 0, or every balance is "
                f"<= 0). Use set_account_slot to set 'apr' on the "
                f"debt accounts you want included in the payoff "
                f"plan."
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
        warnings: list[str] = []
        if excluded_warning:
            full["excluded"] = sorted(excluded_debts)
            warnings.append(excluded_warning)
        if unestimable_warning:
            full["unestimable"] = sorted(unestimable_debts)
            warnings.append(unestimable_warning)
        if no_apr_warning:
            full["no_apr"] = sorted(no_apr_debts)
            warnings.append(no_apr_warning)
        if warnings:
            full["warnings"] = warnings

        if not compact:
            return full

        compact_out = _format_debt_payoff_compact(
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
        if excluded_warning:
            compact_out += f"\n⚠ {excluded_warning}"
        if unestimable_warning:
            compact_out += f"\n⚠ {unestimable_warning}"
        if no_apr_warning:
            compact_out += f"\n⚠ {no_apr_warning}"
        return compact_out
