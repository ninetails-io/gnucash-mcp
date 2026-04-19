"""BudgetsMixin — budget creation, amount targets, and variance reports.

Depends on shared helpers from BaseGnuCashBook:
  - self.open, self._find_account, self._collect_descendants
  - _verify_write, _verify_composite_write (module-level)

piecash blocks the Budget / Recurrence / BudgetAmount constructors
(they are read-only in the ORM), so inserts use raw SQL via the
SQLAlchemy Core API paired with _verify_* round-trip checks.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

import piecash

from gnucash_mcp.book._base import _verify_composite_write, _verify_write


class BudgetsMixin:
    """Budget CRUD + period-aware variance reporting."""

    VALID_BUDGET_PERIOD_TYPES = {"monthly", "quarterly", "weekly"}

    # ── Helpers ───────────────────────────────────────────────────

    def _find_budget(self, book: piecash.Book, name: str):
        """Find a budget by name."""
        from piecash.budget import Budget

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
        from dateutil.relativedelta import relativedelta

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

    def list_budgets(self) -> list[dict]:
        """List all budgets in the book.

        Returns:
            List of budget dicts with guid, name, description,
            num_periods, period_type, and start_date.
        """
        from piecash.budget import Budget

        with self.open(readonly=True) as book:
            budgets = book.session.query(Budget).all()
            return [self._budget_to_dict(b) for b in budgets]

    def get_budget(self, name: str) -> dict | None:
        """Get full details of a budget including all budget amounts.

        Args:
            name: Budget name.

        Returns:
            Dict with budget info and all account/period amounts,
            or None if not found.
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

            result["accounts"] = [
                {
                    "account": acct_name,
                    "periods": periods,
                }
                for acct_name, periods in sorted(accounts.items())
            ]

            return result

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

        from piecash._common import Recurrence
        from piecash.budget import Budget

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

            return {
                "guid": budget_guid,
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
        from piecash.budget import BudgetAmount

        amount_decimal = Decimal(amount)

        with self.open(readonly=False) as book:
            budget = self._find_budget(book, budget_name)
            if not budget:
                raise ValueError(f"Budget not found: {budget_name}")

            acct = self._find_account(book, account)
            if not acct:
                raise ValueError(f"Account not found: {account}")

            periods = self._resolve_periods(budget, period)

            # Convert Decimal to num/denom for direct inserts
            amount_denom = 100
            amount_num = int(amount_decimal * amount_denom)

            for p in periods:
                try:
                    existing = budget.amounts(
                        account=acct, period_num=p
                    )
                    existing.amount = amount_decimal
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

            return {
                "budget": budget_name,
                "account": account,
                "amount": amount,
                "periods_set": periods,
                "status": "updated",
            }

    def get_budget_report(
        self,
        budget_name: str,
        period: int | str | None = None,
        account: str | None = None,
        include_children: bool = True,
    ) -> dict:
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
                filter_acct = self._find_account(book, account)
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
            for ba in budget.amounts:
                if ba.period_num not in report_periods:
                    continue
                acct_name = ba.account.fullname
                if target_accounts is not None and ba.account not in target_accounts:
                    continue
                budgeted[acct_name] = budgeted.get(
                    acct_name, Decimal("0")
                ) + ba.amount

            # Calculate actuals from transactions
            actuals: dict[str, Decimal] = {}
            for transaction in book.transactions:
                if not (first_start <= transaction.post_date <= last_end):
                    continue
                for split in transaction.splits:
                    acct_name = split.account.fullname
                    if acct_name not in budgeted:
                        continue
                    amount = split.quantity
                    if split.account.type == "EXPENSE" and amount > 0:
                        actuals[acct_name] = actuals.get(
                            acct_name, Decimal("0")
                        ) + amount
                    elif split.account.type == "INCOME" and amount < 0:
                        actuals[acct_name] = actuals.get(
                            acct_name, Decimal("0")
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

            return {
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

            result = {
                "name": name,
                "guid": budget.guid,
                "status": "deleted",
            }

            book.session.delete(budget)
            book.save()

            return result
