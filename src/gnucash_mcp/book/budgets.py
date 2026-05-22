"""BudgetsMixin — budget creation, amount targets, and variance reports.

Depends on shared helpers from BaseGnuCashBook:
  - self.open, self._find_account, self._collect_descendants
  - _verify_write, _verify_composite_write (module-level)

piecash blocks the Budget / Recurrence / BudgetAmount constructors
(they are read-only in the ORM), so inserts use raw SQL via the
SQLAlchemy Core API paired with _verify_* round-trip checks.
"""

from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal

import piecash
from dateutil.relativedelta import relativedelta
from piecash._common import Recurrence
from piecash.budget import Budget, BudgetAmount

from gnucash_mcp.book._base import (
    _to_decimal,
    _unique_prefix,
    _verify_composite_write,
    _verify_write,
)


def _collapse_period_runs(
    periods: dict[int, str], num_periods: int,
) -> str:
    """Render an account's period amounts as a compact run-string.

    Example outputs:
      ``"250/mo (all periods)"``
      ``"300/mo (P0-5,P8-11), 600/mo (P6-7)"``
      ``"100/mo (P0-10), 800/mo (P11)"``

    Groups consecutive periods that share the same amount into runs.
    Most monthly budgets are uniform across all periods, so the common
    case is the simple "all periods" form.
    """
    if not periods:
        return "—"

    # Build a per-period amount list (filling missing periods with "0").
    per_period = [periods.get(i, "0") for i in range(num_periods)]

    # If every period has the same value, the simple form wins.
    unique_amounts = set(per_period)
    if len(unique_amounts) == 1:
        amt = per_period[0]
        return f"{amt}/mo (all periods)"

    # Build runs of consecutive periods that share the same amount.
    runs: list[tuple[str, int, int]] = []
    run_start = 0
    run_amount = per_period[0]
    for i in range(1, num_periods):
        if per_period[i] != run_amount:
            runs.append((run_amount, run_start, i - 1))
            run_start = i
            run_amount = per_period[i]
    runs.append((run_amount, run_start, num_periods - 1))

    # Group runs by amount so "P0-5,P8-11" forms naturally for split
    # patterns (e.g., the same baseline amount in disjoint stretches).
    by_amount: dict[str, list[tuple[int, int]]] = {}
    for amt, lo, hi in runs:
        by_amount.setdefault(amt, []).append((lo, hi))

    # Sort by total span size (largest first) so the dominant amount
    # appears first in the rendering.
    sorted_amounts = sorted(
        by_amount.items(),
        key=lambda kv: -sum(hi - lo + 1 for lo, hi in kv[1]),
    )

    parts = []
    for amt, ranges in sorted_amounts:
        labels = []
        for lo, hi in ranges:
            labels.append(f"P{lo}-{hi}" if lo != hi else f"P{lo}")
        parts.append(f"{amt}/mo ({','.join(labels)})")
    return ", ".join(parts)


def _format_budget_report_compact(report: dict) -> str:
    """Render a budget-report dict as a compact text table.

    Layout per Phase 5B::

        2026 Annual Budget — Period 3 (Apr 2026)
        Account                          Budget   Actual  Remaining  %Used
        Auto:Fuel                           250   199.61      50.39  79.8%
        Business:Contractor Payments      6,200 6,128.00      72.00  98.8%
        Groceries                           450   608.57    -158.57  135.2% ⚠
        Medical                             200 1,488.03  -1,288.03  744.0% ⚠
        TOTAL                             7,971 9,022.90  -1,051.90  113.2% ⚠

    ``⚠`` markers fire on rows where ``percent_used > 110%`` — same
    threshold ``get_book_summary`` uses for the budget headline.
    Strips a common ``Expenses:`` / ``Income:`` prefix from leaf
    names to keep the column readable.
    """
    accounts = report.get("accounts", [])
    totals = report.get("totals", {})
    budget_name = report.get("budget", "?")
    period_info = report.get("period", "")

    header_line = f"{budget_name} — {period_info}"
    if not accounts:
        return f"{header_line}\n(no budgeted accounts)"

    # Strip a common "Expenses:" / "Income:" prefix (same idiom as
    # spending_by_category / income_by_source).
    full_names = [r["account"] for r in accounts]
    common_prefix = ""
    if full_names and ":" in full_names[0]:
        candidate = full_names[0].split(":")[0] + ":"
        if all(n.startswith(candidate) for n in full_names):
            common_prefix = candidate
    leaves = [n[len(common_prefix):] for n in full_names]

    name_width = max(
        max(len(l) for l in leaves), len("Account"), len("TOTAL"),
    )

    def _fmt(value: str) -> str:
        d = Decimal(value)
        # Whole-dollar values render simpler.
        if d == d.to_integral_value():
            return f"{int(d):,}"
        return f"{d:,.2f}"

    budget_strs = [_fmt(r["budgeted"]) for r in accounts]
    actual_strs = [_fmt(r["actual"]) for r in accounts]
    remaining_strs = [_fmt(r["remaining"]) for r in accounts]
    pct_strs = [f"{r['percent_used']}%" for r in accounts]

    total_budget = _fmt(totals.get("budgeted", "0"))
    total_actual = _fmt(totals.get("actual", "0"))
    total_remaining = _fmt(totals.get("remaining", "0"))
    total_pct = f"{totals.get('percent_used', '0')}%"

    budget_w = max(
        max(len(s) for s in budget_strs), len(total_budget), len("Budget"),
    )
    actual_w = max(
        max(len(s) for s in actual_strs), len(total_actual), len("Actual"),
    )
    remaining_w = max(
        max(len(s) for s in remaining_strs),
        len(total_remaining),
        len("Remaining"),
    )
    pct_w = max(
        max(len(s) for s in pct_strs), len(total_pct), len("%Used"),
    )

    def _pct_marker(pct_str: str) -> str:
        # Strip "%" then parse; over-110% earns the warning marker.
        try:
            v = Decimal(pct_str.rstrip("%"))
        except Exception:
            return ""
        return " ⚠" if v > Decimal("110") else ""

    lines = [header_line]
    lines.append(
        f"{'Account':<{name_width}}  "
        f"{'Budget':>{budget_w}}  "
        f"{'Actual':>{actual_w}}  "
        f"{'Remaining':>{remaining_w}}  "
        f"{'%Used':>{pct_w}}"
    )
    for leaf, b, a, rem, pct in zip(
        leaves, budget_strs, actual_strs, remaining_strs, pct_strs,
    ):
        lines.append(
            f"{leaf:<{name_width}}  "
            f"{b:>{budget_w}}  "
            f"{a:>{actual_w}}  "
            f"{rem:>{remaining_w}}  "
            f"{pct:>{pct_w}}{_pct_marker(pct)}"
        )
    lines.append(
        f"{'TOTAL':<{name_width}}  "
        f"{total_budget:>{budget_w}}  "
        f"{total_actual:>{actual_w}}  "
        f"{total_remaining:>{remaining_w}}  "
        f"{total_pct:>{pct_w}}{_pct_marker(total_pct)}"
    )
    return "\n".join(lines)


def _format_get_budget_compact(
    info: dict, account_rows: list[dict],
) -> str:
    """Render a budget as compact text — header + per-account rows.

    Header carries name, period count, and start date. Per-account
    rows use ``_collapse_period_runs`` to fold uniform stretches
    into single labels (the typical 12-cell repeat collapses to one
    "all periods" form).
    """
    num_periods = info.get("num_periods", 12)
    name = info.get("name", "?")
    period_type = info.get("period_type", "")
    start = info.get("start_date", "?")
    header = (
        f"{name}  {num_periods} periods"
        + (f" ({period_type})" if period_type else "")
        + f"  starts:{start}"
    )

    if not account_rows:
        return header + "\n(no account amounts set)"

    name_width = max(len(r["account"]) for r in account_rows)
    lines = [header]
    for row in account_rows:
        acct = row["account"]
        periods = {int(k): v for k, v in row["periods"].items()}
        runs = _collapse_period_runs(periods, num_periods)
        lines.append(f"{acct:<{name_width}}  {runs}")
    return "\n".join(lines)


class BudgetsMixin:
    """Budget CRUD + period-aware variance reporting."""

    VALID_BUDGET_PERIOD_TYPES = {"monthly", "quarterly", "weekly"}

    # ── Helpers ───────────────────────────────────────────────────

    def _find_budget(self, book: piecash.Book, name: str):
        """Find a budget by name."""

        for budget in book.session.query(Budget).all():
            if budget.name == name:
                return budget
        return None

    def _budget_to_dict(self, budget) -> dict:
        """Convert a piecash Budget to a serializable dict."""
        rec = budget.recurrence

        # Reverse-map recurrence to period_type string
        period_type = "monthly"
        if rec.recurrence_period_type == "month" and rec.recurrence_mult == 3:
            period_type = "quarterly"
        elif rec.recurrence_period_type == "week":
            period_type = "weekly"

        start = rec.recurrence_period_start
        if isinstance(start, datetime):
            start = start.date()

        return {
            "guid": budget.guid,
            "name": budget.name,
            "description": budget.description or "",
            "num_periods": budget.num_periods,
            "period_type": period_type,
            "start_date": start.isoformat(),
        }

    def _period_to_date_range(
        self, budget, period_num: int
    ) -> tuple[date, date]:
        """Convert a budget period number to a date range.

        Args:
            budget: piecash Budget object.
            period_num: Zero-indexed period number.

        Returns:
            Tuple of (period_start, period_end) as dates.

        Raises:
            ValueError: If period_num is out of range.
        """
        if period_num < 0 or period_num >= budget.num_periods:
            raise ValueError(
                f"Period {period_num} out of range "
                f"(0-{budget.num_periods - 1})"
            )

        rec = budget.recurrence
        anchor = rec.recurrence_period_start
        if isinstance(anchor, datetime):
            anchor = anchor.date()

        period_type = rec.recurrence_period_type
        mult = rec.recurrence_mult

        if period_type == "month":
            delta = relativedelta(months=mult)
        elif period_type == "week":
            delta = relativedelta(weeks=mult)
        else:
            raise ValueError(f"Unsupported period type: {period_type}")

        start = anchor + delta * period_num
        end = anchor + delta * (period_num + 1) - timedelta(days=1)

        return start, end

    def _current_period(self, budget) -> int | None:
        """Get the current period number based on today's date.

        Returns period number (0-indexed), or None if today is
        outside the budget range.
        """
        today = date.today()
        for p in range(budget.num_periods):
            start, end = self._period_to_date_range(budget, p)
            if start <= today <= end:
                return p
        return None

    @staticmethod
    def _coerce_period_str(period):
        """Coerce a numeric string like '6' to int 6.

        MCP tool calls route string-typed parameters through the XML
        parameter layer as strings even when the schema's anyOf permits
        integers. This normalizer lets callers pass '6' and get the same
        behavior as int 6. Non-digit strings (e.g. 'all', 'q3', 'ytd')
        and non-string values pass through untouched.
        """
        if isinstance(period, str) and period.isdigit():
            return int(period)
        return period

    def _resolve_periods(
        self, budget, period: int | str | None
    ) -> list[int]:
        """Resolve a period specifier to a list of period numbers.

        Args:
            budget: piecash Budget object.
            period: None/"all" for all, int for specific, "q1"-"q4" for quarter.

        Returns:
            List of period numbers.

        Raises:
            ValueError: If period is invalid or out of range.
        """
        period = self._coerce_period_str(period)
        num = budget.num_periods

        if period is None or period == "all":
            return list(range(num))

        if isinstance(period, int):
            if period < 0 or period >= num:
                raise ValueError(
                    f"Period {period} out of range (0-{num - 1})"
                )
            return [period]

        if isinstance(period, str):
            quarter_map = {
                "q1": (0, 3),
                "q2": (3, 6),
                "q3": (6, 9),
                "q4": (9, 12),
            }
            period_lower = period.lower()
            if period_lower in quarter_map:
                start, end = quarter_map[period_lower]
                result = [p for p in range(start, end) if p < num]
                if not result:
                    raise ValueError(
                        f"Quarter {period} has no periods in this budget "
                        f"(budget has {num} periods)"
                    )
                return result

        raise ValueError(
            f"Invalid period: {period}. "
            f"Use None, 'all', an integer, or 'q1'-'q4'."
        )

    # ── CRUD ──────────────────────────────────────────────────────

    def list_budgets(self, compact: bool = True) -> list[dict] | str:
        """List all budgets in the book.

        Args:
            compact: If True (default), return a compact one-line-per-
                     budget string. Verbose mode returns the structured
                     dict list.

        Returns:
            If compact: newline-separated lines of the form
                ``"<name>  <num_periods> periods (<period_type>)  starts:<YYYY-MM-DD>"``.
            If not compact: list of budget dicts.
        """

        with self.open(readonly=True) as book:
            budgets = book.session.query(Budget).all()
            dicts = [self._budget_to_dict(b) for b in budgets]
            if not compact:
                return dicts

            if not dicts:
                return ""
            name_width = max(len(d["name"]) for d in dicts)
            lines = []
            for d in dicts:
                periods = d.get("num_periods", "?")
                ptype = d.get("period_type", "")
                start = d.get("start_date", "?")
                ptype_str = f" ({ptype})" if ptype else ""
                lines.append(
                    f"{d['name']:<{name_width}}  "
                    f"{periods} periods{ptype_str}  starts:{start}"
                )
            return "\n".join(lines)

    def get_budget(
        self, name: str, compact: bool = True,
    ) -> dict | str | None:
        """Get full details of a budget including all budget amounts.

        Args:
            name: Budget name.
            compact: If True (default), return a compact text table
                     that collapses uniform periods (e.g.,
                     ``"250/mo (all periods)"``). Verbose mode returns
                     the structured ``periods`` dict per account.

        Returns:
            If compact: text string with header + one line per account.
            If not compact: dict with full ``periods`` mapping.
            ``None`` if budget not found (matches existing contract).
        """
        with self.open(readonly=True) as book:
            budget = self._find_budget(book, name)
            if not budget:
                return None

            result = self._budget_to_dict(budget)

            accounts: dict[str, dict[int, str]] = {}
            for ba in budget.amounts:
                acct_name = ba.account.fullname
                if acct_name not in accounts:
                    accounts[acct_name] = {}
                accounts[acct_name][ba.period_num] = str(ba.amount)

            account_rows = [
                {"account": acct_name, "periods": periods}
                for acct_name, periods in sorted(accounts.items())
            ]
            result["accounts"] = account_rows

            if not compact:
                return result

            return _format_get_budget_compact(result, account_rows)

    def create_budget(
        self,
        name: str,
        year: int | None = None,
        num_periods: int = 12,
        period_type: str = "monthly",
        description: str = "",
    ) -> dict:
        """Create a new budget.

        Args:
            name: Budget name (e.g., "2026 Budget").
            year: Budget year. Defaults to current year.
            num_periods: Number of periods. Default 12.
            period_type: "monthly" (default), "quarterly", or "weekly".
            description: Optional description.

        Returns:
            Dict with guid, name, and status.

        Raises:
            ValueError: If budget with same name already exists,
                       invalid period_type, or invalid num_periods.
        """
        import uuid


        if period_type not in self.VALID_BUDGET_PERIOD_TYPES:
            raise ValueError(
                f"Invalid period_type: {period_type}. "
                f"Valid types: {', '.join(sorted(self.VALID_BUDGET_PERIOD_TYPES))}"
            )
        if num_periods < 1:
            raise ValueError("num_periods must be at least 1")

        if year is None:
            year = date.today().year

        recurrence_map = {
            "monthly": ("month", 1),
            "quarterly": ("month", 3),
            "weekly": ("week", 1),
        }
        rec_period_type, rec_mult = recurrence_map[period_type]

        with self.open(readonly=False) as book:
            existing = self._find_budget(book, name)
            if existing:
                raise ValueError(f"Budget already exists: {name}")

            budget_guid = uuid.uuid4().hex

            # Direct table inserts — piecash blocks Budget/Recurrence constructors
            book.session.execute(
                Budget.__table__.insert().values(
                    guid=budget_guid,
                    name=name,
                    description=description,
                    num_periods=num_periods,
                )
            )
            _verify_write(
                book.session, Budget.__table__, budget_guid,
                f"Budget '{name}'",
            )

            book.session.execute(
                Recurrence.__table__.insert().values(
                    obj_guid=budget_guid,
                    recurrence_mult=rec_mult,
                    recurrence_period_type=rec_period_type,
                    recurrence_period_start=date(year, 1, 1),
                    recurrence_weekend_adjust="none",
                )
            )
            _verify_composite_write(
                book.session, Recurrence.__table__,
                {"obj_guid": budget_guid},
                f"Recurrence for budget '{name}'",
            )

            book.save()


            all_budget_guids = [
                row[0]
                for row in book.session.query(Budget.guid).all()
            ]
            short_guid = _unique_prefix(budget_guid, all_budget_guids)
            return {
                "guid": short_guid,
                "name": name,
                "status": "created",
            }

    def set_budget_amount(
        self,
        budget_name: str,
        account: str,
        amount: str,
        period: int | str | None = None,
    ) -> dict:
        """Set a budget target for an account.

        Args:
            budget_name: Name of the budget.
            account: Full account path (e.g., "Expenses:Groceries").
            amount: Budget amount as string (e.g., "500.00").
            period: Which period(s) to set:
                - None or "all": All periods (default)
                - Integer 0..N-1: Specific period
                - "q1", "q2", "q3", "q4": All periods in quarter

        Returns:
            Dict with budget, account, amount, periods set, and status.

        Raises:
            ValueError: If budget not found, account not found,
                       or invalid period.
        """

        amount_decimal = _to_decimal(amount)

        with self.open(readonly=False) as book:
            budget = self._find_budget(book, budget_name)
            if not budget:
                raise ValueError(f"Budget not found: {budget_name}")

            acct = self._resolve_account(book, account)
            if not acct:
                raise ValueError(f"Account not found: {account}")

            periods = self._resolve_periods(budget, period)

            # Stage prior amounts (per period) so the audit log can
            # render before/after diffs. Without this, the bookkeeper
            # sees only the new amount and has no way to verify what
            # changed.
            prior_amounts: dict = {}
            for p in periods:
                try:
                    existing = budget.amounts(
                        account=acct, period_num=p
                    )
                    prior_amounts[p] = str(existing.amount)
                except KeyError:
                    prior_amounts[p] = None
            self._stage_audit_before({
                "budget_name": budget_name,
                "account": acct.fullname,
                "prior_amounts": prior_amounts,
            })

            # Quantize to the account commodity's smallest fraction:
            # USD (fraction=100) → 2 decimals, JPY (fraction=1) → 0,
            # BHD (fraction=1000) → 3. Banker's rounding avoids
            # systematic bias on ties. We must apply the same
            # quantization on both the insert AND update branches —
            # piecash's hybrid ``existing.amount`` setter doesn't
            # quantize, so without this the two paths would store
            # different values for the same input.
            amount_denom = acct.commodity.fraction
            quantum = Decimal(1) / Decimal(amount_denom)
            quantized = amount_decimal.quantize(
                quantum, rounding=ROUND_HALF_EVEN,
            )
            amount_num = int(quantized * amount_denom)

            for p in periods:
                try:
                    existing = budget.amounts(
                        account=acct, period_num=p
                    )
                    existing.amount = quantized
                except KeyError:
                    # No existing amount — insert via table (BudgetAmount constructor blocked)
                    book.session.execute(
                        BudgetAmount.__table__.insert().values(
                            budget_guid=budget.guid,
                            account_guid=acct.guid,
                            period_num=p,
                            amount_num=amount_num,
                            amount_denom=amount_denom,
                        )
                    )
                    _verify_composite_write(
                        book.session, BudgetAmount.__table__,
                        {
                            "budget_guid": budget.guid,
                            "account_guid": acct.guid,
                            "period_num": p,
                        },
                        f"BudgetAmount period {p} for '{budget_name}'",
                    )

            book.save()

            # periods_set is computed (e.g., "q1" → [0, 1, 2]) so we keep
            # it. The echoed inputs (budget, account, amount) come from
            # tool params in the audit log.
            return {
                "periods_set": periods,
                "status": "updated",
            }

    def get_budget_report(
        self,
        budget_name: str,
        period: int | str | None = None,
        account: str | None = None,
        include_children: bool = True,
        compact: bool = True,
    ) -> dict | str:
        """Compare actual spending against budget.

        Args:
            budget_name: Name of the budget.
            period: Which period to report:
                - None: Current period based on today's date (default)
                - Integer 0-N: Specific period
                - "ytd": Year to date (all periods up to current)
                - "all": All periods
            account: Optional filter to specific account or parent.
            include_children: If True and account specified, include
                            child accounts. Default True.

        Returns:
            Dict with budget name, period info, account breakdown
            (budgeted, actual, remaining, percent_used), and totals.

        Raises:
            ValueError: If budget not found, invalid period, or
                       account not found.
        """
        with self.open(readonly=True) as book:
            budget = self._find_budget(book, budget_name)
            if not budget:
                raise ValueError(f"Budget not found: {budget_name}")

            period = self._coerce_period_str(period)

            if period is None:
                current = self._current_period(budget)
                if current is None:
                    raise ValueError(
                        "Today's date is outside the budget period range"
                    )
                report_periods = [current]
            elif period == "ytd":
                current = self._current_period(budget)
                if current is None:
                    raise ValueError(
                        "Today's date is outside the budget period range"
                    )
                report_periods = list(range(current + 1))
            elif period == "all":
                report_periods = list(range(budget.num_periods))
            elif isinstance(period, int):
                if period < 0 or period >= budget.num_periods:
                    raise ValueError(
                        f"Period {period} out of range "
                        f"(0-{budget.num_periods - 1})"
                    )
                report_periods = [period]
            else:
                raise ValueError(f"Invalid period: {period}")

            first_start, _ = self._period_to_date_range(
                budget, report_periods[0]
            )
            _, last_end = self._period_to_date_range(
                budget, report_periods[-1]
            )

            if account:
                filter_acct = self._resolve_account(book, account)
                if not filter_acct:
                    raise ValueError(f"Account not found: {account}")

                if include_children:
                    target_accounts = set()
                    self._collect_descendants(filter_acct, target_accounts)
                    target_accounts.add(filter_acct)
                else:
                    target_accounts = {filter_acct}
            else:
                target_accounts = None

            # Gather budgeted amounts
            budgeted: dict[str, Decimal] = {}
            # Keep a handle to each budgeted account for descendant walking.
            budgeted_accounts: dict[str, object] = {}
            for ba in budget.amounts:
                if ba.period_num not in report_periods:
                    continue
                acct_name = ba.account.fullname
                if target_accounts is not None and ba.account not in target_accounts:
                    continue
                budgeted[acct_name] = budgeted.get(
                    acct_name, Decimal("0")
                ) + ba.amount
                budgeted_accounts[acct_name] = ba.account

            # Roll-up map: descendant-account-fullname → nearest-ancestor-
            # fullname that is itself budgeted. Lets budgets set on a
            # placeholder parent (e.g. Expenses:Utilities) sum the actuals
            # from all its non-budgeted children (Electric, Gas, Water...).
            # A child that is itself separately budgeted is NOT rolled up —
            # its actuals stay on its own line to avoid double-counting.
            rollup_map: dict[str, str] = {}
            for acct_name, budgeted_acct in budgeted_accounts.items():
                rollup_map.setdefault(acct_name, acct_name)
                descendants: set = set()
                self._collect_descendants(budgeted_acct, descendants)
                for desc in descendants:
                    if desc.fullname in budgeted:
                        continue  # separately budgeted — don't roll up
                    # If multiple budgeted ancestors cover this descendant
                    # (nested parents), keep the nearest one (the deepest
                    # budgeted ancestor). A simple proxy: prefer the longer
                    # ancestor path.
                    existing = rollup_map.get(desc.fullname)
                    if existing is None or len(acct_name) > len(existing):
                        rollup_map[desc.fullname] = acct_name

            # Cross-currency conversion: when a budgeted account
            # parent has children in non-default-currency commodities
            # (e.g., USD-default book with a EUR ``Expenses:Travel``
            # leaf), summing raw ``split.quantity`` would treat 100
            # EUR + 100 USD as 200 in the parent's row. The conversion
            # helpers live on the unconditionally-composed
            # :class:`CurrencyMixin`, so they're available regardless
            # of which ``--modules`` are enabled.
            factors = self._account_conversion_factors(book)

            # Calculate actuals from transactions
            actuals: dict[str, Decimal] = {}
            for transaction in book.transactions:
                if not (first_start <= transaction.post_date <= last_end):
                    continue
                for split in transaction.splits:
                    acct_name = split.account.fullname
                    rollup_target = rollup_map.get(acct_name)
                    if rollup_target is None:
                        continue
                    amount = self._split_in_default_currency(
                        split, split.account,
                        factors.get(split.account.guid),
                    )
                    if split.account.type == "EXPENSE" and amount > 0:
                        actuals[rollup_target] = actuals.get(
                            rollup_target, Decimal("0")
                        ) + amount
                    elif split.account.type == "INCOME" and amount < 0:
                        actuals[rollup_target] = actuals.get(
                            rollup_target, Decimal("0")
                        ) + (-amount)

            accounts_result = []
            total_budgeted = Decimal("0")
            total_actual = Decimal("0")

            for acct_name in sorted(budgeted.keys()):
                b = budgeted[acct_name]
                a = actuals.get(acct_name, Decimal("0"))
                remaining = b - a
                pct = (
                    (a / b * 100).quantize(Decimal("0.1"))
                    if b > 0
                    else Decimal("0")
                )

                accounts_result.append({
                    "account": acct_name,
                    "budgeted": str(b),
                    "actual": str(a),
                    "remaining": str(remaining),
                    "percent_used": str(pct),
                })

                total_budgeted += b
                total_actual += a

            total_remaining = total_budgeted - total_actual
            total_pct = (
                (total_actual / total_budgeted * 100).quantize(
                    Decimal("0.1")
                )
                if total_budgeted > 0
                else Decimal("0")
            )

            if len(report_periods) == 1:
                p_start, p_end = self._period_to_date_range(
                    budget, report_periods[0]
                )
                period_info = (
                    f"Period {report_periods[0]} "
                    f"({p_start.isoformat()} to {p_end.isoformat()})"
                )
            else:
                period_info = (
                    f"Periods {report_periods[0]}-{report_periods[-1]} "
                    f"({first_start.isoformat()} to {last_end.isoformat()})"
                )

            full = {
                "budget": budget_name,
                "period": period_info,
                "accounts": accounts_result,
                "totals": {
                    "budgeted": str(total_budgeted),
                    "actual": str(total_actual),
                    "remaining": str(total_remaining),
                    "percent_used": str(total_pct),
                },
            }
            if not compact:
                return full
            return _format_budget_report_compact(full)

    def delete_budget(self, name: str) -> dict:
        """Delete a budget.

        Args:
            name: Budget name.

        Returns:
            Dict with name, guid, and status.

        Raises:
            ValueError: If budget not found.
        """
        with self.open(readonly=False) as book:
            budget = self._find_budget(book, name)
            if not budget:
                raise ValueError(f"Budget not found: {name}")


            # Stage budget snapshot for the audit log BEFORE delete.
            # Without this, the audit log shows only "deleted budget X"
            # — the bookkeeper can't tell what amounts/periods were
            # lost. Capture the small set of facts that can't be
            # recovered after the delete: name, num_periods, and how
            # many account-amount rows existed.
            self._stage_audit_before({
                "name": budget.name,
                "num_periods": budget.num_periods,
                "amount_count": len(list(budget.amounts)),
            })

            all_budget_guids = [
                row[0]
                for row in book.session.query(Budget.guid).all()
            ]
            short_guid = _unique_prefix(budget.guid, all_budget_guids)
            result = {
                "name": name,
                "guid": short_guid,
                "status": "deleted",
            }

            book.session.delete(budget)
            book.save()

            return result
