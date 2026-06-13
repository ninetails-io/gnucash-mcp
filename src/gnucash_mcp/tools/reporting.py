"""Reporting tools: spending, income, balance sheet, net worth, cash flow, debt payoff."""

from datetime import date

from gnucash_mcp.logging_config import audit_log
from gnucash_mcp.tools._helpers import _json, _parse_iso_date, safe_tool


def register(mcp, get_book) -> None:
    """Attach reporting tools to the FastMCP server."""

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def spending_by_category(
        start_date: str,
        end_date: str,
        depth: int = 1,
        verbose: bool = False,
    ) -> str:
        """Get spending breakdown by expense category for a period.

        Returns a compact aligned text table by default. Use verbose=true
        for the full structured dict (programmatic consumers, plotting).

        Args:
            start_date: Start of period (YYYY-MM-DD)
            end_date: End of period (YYYY-MM-DD)
            depth: Hierarchy depth for grouping (1 = top-level categories, 2 = subcategories)
            verbose: If true, return the structured dict.
        """
        book = get_book()
        result = book.spending_by_category(
            start_date=date.fromisoformat(start_date),
            end_date=date.fromisoformat(end_date),
            depth=depth,
            compact=not verbose,
        )
        if verbose:
            return _json(result)
        return result

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def income_by_source(
        start_date: str,
        end_date: str,
        depth: int = 1,
        verbose: bool = False,
    ) -> str:
        """Get income breakdown by source for a period.

        Returns a compact aligned text table by default. Use verbose=true
        for the full structured dict.

        Args:
            start_date: Start of period (YYYY-MM-DD)
            end_date: End of period (YYYY-MM-DD)
            depth: Hierarchy depth for grouping (1 = top-level categories, 2 = subcategories)
            verbose: If true, return the structured dict.
        """
        book = get_book()
        result = book.income_by_source(
            start_date=date.fromisoformat(start_date),
            end_date=date.fromisoformat(end_date),
            depth=depth,
            compact=not verbose,
        )
        if verbose:
            return _json(result)
        return result

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def balance_sheet(as_of_date: str | None = None) -> str:
        """Generate a balance sheet as of a specific date.

        Shows assets, liabilities, and equity with account breakdowns.
        A = L + E holds by construction; non-zero unrealized P&L
        appears as a synthetic equity row.

        Args:
            as_of_date: Date in ISO format (YYYY-MM-DD). Defaults to
                today — matching ``get_book_summary``'s implicit
                cutoff so cross-tool comparisons agree without
                threading the same date into both calls. Pass an
                explicit date for historical snapshots.
        """
        book = get_book()
        # Distinguish "not provided" (None → today) from "provided
        # but empty/garbage" (raise). An empty-string
        # ``as_of_date=""`` silently falling back to today is a
        # caller bug that produces silently wrong-dated reports;
        # the strict-kwargs pattern
        # extends to the value, not just the parameter name.
        if as_of_date is None:
            d = date.today()
        else:
            d = date.fromisoformat(as_of_date)
        result = book.balance_sheet(as_of_date=d)
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def net_worth(
        end_date: str,
        start_date: str | None = None,
        interval: str | None = None,
    ) -> str:
        """Calculate net worth (assets minus liabilities).

        Can calculate a single point-in-time value or a time series.

        Args:
            end_date: Calculate net worth as of this date (YYYY-MM-DD)
            start_date: Optional start date for time series (YYYY-MM-DD)
            interval: Optional interval for time series: 'month', 'quarter', or 'year'
        """
        book = get_book()
        result = book.net_worth(
            end_date=date.fromisoformat(end_date),
            start_date=_parse_iso_date(start_date),
            interval=interval,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def cash_flow(
        start_date: str,
        end_date: str,
        account: str | None = None,
        include_transfers: bool = False,
    ) -> str:
        """Calculate cash flow (inflows and outflows) for a period.

        Scope is BANK and CASH accounts by default. Credit-card and
        investment movements are not cash flow (they're liability /
        asset changes — use balance_sheet). An explicit ``account=``
        of any type works but the default scope is narrow.

        Internal transfers (transactions with no INCOME or EXPENSE
        leg — transfer to savings, currency wallet shuffle, paying
        a credit card from checking) are filtered by default. The
        default totals answer "where did money come from and where
        did it go?" rather than "every debit and credit." Pass
        ``include_transfers=true`` for the gross flow (e.g. for
        reconciling against a bank statement).

        Args:
            start_date: Start of period (YYYY-MM-DD)
            end_date: End of period (YYYY-MM-DD)
            account: Optional specific account to analyze (defaults
                to all cash/bank accounts)
            include_transfers: When False (default), filter internal
                transfers. When True, include every cash/bank
                movement regardless of category.
        """
        book = get_book()
        result = book.cash_flow(
            start_date=date.fromisoformat(start_date),
            end_date=date.fromisoformat(end_date),
            account=account,
            include_transfers=include_transfers,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def debt_payoff_plan(
        monthly_budget: str,
        additional_purchase: str | None = None,
        verbose: bool = False,
    ) -> str:
        """Calculate an avalanche-method debt payoff schedule with YETI multiplier.

        Auto-discovers CREDIT/LIABILITY accounts that have an 'apr' slot set.
        Set APRs via set_account_slot (e.g., set_account_slot("Liabilities:Visa", "apr", "23.49")).

        Returns a compact text summary by default — kill order with
        balances/APRs/payoff months, YETI line, totals, debt-free date.
        Use verbose=true for the full structured dict (per-account
        ``interest_paid`` / ``credit_limit`` / ``minimum_payment``,
        plus the structured ``yeti`` block) suitable for programmatic
        consumers.

        YETI (Your Expense's True Impact) shows the true cost of a purchase when
        carrying debt: "A $1.00 purchase will cost you $1.68 by the time your
        debt is paid off."

        Args:
            monthly_budget: Total monthly amount available for all debt payments combined
            additional_purchase: Dollar amount to calculate YETI for (default "1.00")
            verbose: If true, return the full structured dict instead
                     of the compact text summary.
        """
        book = get_book()
        result = book.debt_payoff_plan(
            monthly_budget=monthly_budget,
            additional_purchase=additional_purchase,
            compact=not verbose,
        )
        if verbose:
            return _json(result)
        return result
