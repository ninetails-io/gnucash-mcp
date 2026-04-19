"""ReportingMixin — balance sheet, spending/income breakdowns, cash flow, debt payoff.

All methods are read-only (no `book.save()`). The debt_payoff_plan
method reads `apr` / `minimum_payment` / `credit_limit` slots on
CREDIT/LIABILITY accounts via piecash's account[key] shortcut, so
it does not depend on AdminMixin's slot tools.

Depends on shared helpers from BaseGnuCashBook:
  - self.open, self._find_account
"""

from datetime import date
from decimal import Decimal, InvalidOperation

import piecash


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
            totals: dict[str, Decimal] = {}

            for transaction in book.transactions:
                if not (start_date <= transaction.post_date <= end_date):
                    continue

                for split in transaction.splits:
                    if split.account.type != "EXPENSE":
                        continue

                    group_account = self._get_account_at_depth(
                        split.account, depth - 1
                    )
                    account_name = group_account.fullname

                    # Expense splits are positive when money is spent
                    amount = split.quantity
                    if amount > 0:
                        totals[account_name] = totals.get(
                            account_name, Decimal("0")
                        ) + amount

            total = sum(totals.values())
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
            totals: dict[str, Decimal] = {}

            for transaction in book.transactions:
                if not (start_date <= transaction.post_date <= end_date):
                    continue

                for split in transaction.splits:
                    if split.account.type != "INCOME":
                        continue

                    group_account = self._get_account_at_depth(
                        split.account, depth - 1
                    )
                    account_name = group_account.fullname

                    # Income splits are negative (money coming in)
                    amount = -split.quantity
                    if amount > 0:
                        totals[account_name] = totals.get(
                            account_name, Decimal("0")
                        ) + amount

            total = sum(totals.values())
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

    def balance_sheet(self, as_of_date: date) -> dict:
        """Generate a balance sheet as of a specific date.

        Args:
            as_of_date: Date to calculate balances as of.

        Returns:
            Dict with assets, liabilities, equity sections and totals.
        """
        with self.open(readonly=True) as book:
            assets: dict[str, Decimal] = {}
            liabilities: dict[str, Decimal] = {}
            equity: dict[str, Decimal] = {}

            asset_types = {"ASSET", "BANK", "CASH", "STOCK", "MUTUAL"}
            liability_types = {"LIABILITY", "CREDIT"}
            equity_types = {"EQUITY"}

            for account in book.accounts:
                if account.type == "ROOT":
                    continue

                balance = Decimal("0")
                for split in account.splits:
                    if split.transaction.post_date <= as_of_date:
                        balance += split.quantity

                if balance == 0:
                    continue

                if account.type in asset_types:
                    assets[account.fullname] = balance
                elif account.type in liability_types:
                    # Liabilities are stored as negative, show as positive
                    liabilities[account.fullname] = -balance
                elif account.type in equity_types:
                    equity[account.fullname] = -balance

            # Net income (Income - Expenses) rolls into equity
            net_income = Decimal("0")
            for account in book.accounts:
                if account.type in ("INCOME", "EXPENSE"):
                    for split in account.splits:
                        if split.transaction.post_date <= as_of_date:
                            net_income -= split.quantity  # Income negative, expense positive

            assets_total = sum(assets.values())
            liabilities_total = sum(liabilities.values())
            equity_total = sum(equity.values()) + net_income

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

        def calc_net_worth_at(book: piecash.Book, at_date: date) -> Decimal:
            """Calculate net worth at a specific date."""
            asset_types = {"ASSET", "BANK", "CASH", "STOCK", "MUTUAL"}
            liability_types = {"LIABILITY", "CREDIT"}

            total = Decimal("0")
            for account in book.accounts:
                if account.type in asset_types:
                    for split in account.splits:
                        if split.transaction.post_date <= at_date:
                            total += split.quantity
                elif account.type in liability_types:
                    for split in account.splits:
                        if split.transaction.post_date <= at_date:
                            total += split.quantity  # Already negative

            return total

        with self.open(readonly=True) as book:
            # Point-in-time calculation
            if not start_date or not interval:
                nw = calc_net_worth_at(book, end_date)
                return {
                    "as_of_date": end_date.isoformat(),
                    "net_worth": str(nw),
                }

            # Time series calculation
            if interval not in ("month", "quarter", "year"):
                raise ValueError(f"Invalid interval: {interval}. Use 'month', 'quarter', or 'year'")

            delta = {
                "month": relativedelta(months=1),
                "quarter": relativedelta(months=3),
                "year": relativedelta(years=1),
            }[interval]

            series = []
            current = start_date
            while current <= end_date:
                nw = calc_net_worth_at(book, current)
                series.append({
                    "date": current.isoformat(),
                    "net_worth": str(nw),
                })
                current += delta

            # Always include end_date if not already included
            if series and series[-1]["date"] != end_date.isoformat():
                nw = calc_net_worth_at(book, end_date)
                series.append({
                    "date": end_date.isoformat(),
                    "net_worth": str(nw),
                })

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
            if account:
                target_account = self._find_account(book, account)
                if not target_account:
                    raise ValueError(f"Account not found: {account}")
                accounts_to_check = [target_account]
            else:
                cash_types = {"BANK", "CASH"}
                accounts_to_check = [
                    a for a in book.accounts if a.type in cash_types
                ]

            inflows = Decimal("0")
            outflows = Decimal("0")

            for acc in accounts_to_check:
                for split in acc.splits:
                    if not (start_date <= split.transaction.post_date <= end_date):
                        continue

                    if split.quantity > 0:
                        inflows += split.quantity
                    else:
                        outflows += -split.quantity

            # period is input echo (LLM supplied dates), net is derivable
            # (inflows - outflows). Both dropped.
            return {
                "account": account if account else "All cash/bank accounts",
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
    ) -> dict:
        """Calculate an avalanche-method debt payoff schedule with YETI multiplier.

        Auto-discovers CREDIT/LIABILITY accounts that have an 'apr' slot set.

        YETI (Your Expense's True Impact) answers: "A $1.00 purchase will cost
        you $X.XX by the time your debt is paid off."

        Args:
            monthly_budget: Total monthly amount available for all debt payments.
            additional_purchase: Dollar amount to calculate YETI for (default "1.00").

        Returns:
            Dict with payoff schedule, totals, and YETI multiplier.

        Raises:
            ValueError: If no debt accounts found, budget invalid, or budget
                        less than sum of minimum payments.
        """
        budget = Decimal(monthly_budget)
        if budget <= 0:
            raise ValueError("monthly_budget must be a positive number")

        purchase_amount = Decimal(additional_purchase) if additional_purchase else Decimal("1.00")
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

        return {
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
