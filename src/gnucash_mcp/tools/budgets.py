"""Budget tools: CRUD + targets + variance reports."""

from gnucash_mcp.logging_config import audit_log
from gnucash_mcp.tools._helpers import _json, safe_tool


def register(mcp, get_book) -> None:
    """Attach budget tools to the FastMCP server."""

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def list_budgets(verbose: bool = False) -> str:
        """List all budgets in the book.

        Returns a compact one-line-per-budget format by default. Use
        verbose=true for the full JSON list.

        Args:
            verbose: If true, return the full JSON list.
        """
        book = get_book()
        result = book.list_budgets(compact=not verbose)
        if verbose:
            return _json(result)
        return result

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def get_budget(name: str, verbose: bool = False) -> str:
        """Get full details of a budget including all budget amounts.

        Returns a compact text table by default — collapses uniform
        periods (e.g., ``"250/mo (all periods)"``) so the typical
        12-cell repeat doesn't dominate the response. Use verbose=true
        for the full structured ``periods`` dict per account.

        Args:
            name: Budget name.
            verbose: If true, return the full structured dict.
        """
        book = get_book()
        result = book.get_budget(name=name, compact=not verbose)
        if result is None:
            return _json({"error": f"Budget not found: {name}"})
        if verbose:
            return _json(result)
        return result

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="create", entity_type="budget")
    def create_budget(
        name: str,
        year: int | None = None,
        num_periods: int = 12,
        period_type: str = "monthly",
        description: str = "",
        start_date: str | None = None,
    ) -> str:
        """Create a new budget.

        Args:
            name: Budget name (e.g., "2026 Budget").
            year: Budget year. Defaults to current year. Ignored when
                ``start_date`` is provided.
            num_periods: Number of periods. Default 12 (monthly for a year).
            period_type: Period length:
                - "monthly" (default)
                - "quarterly"
                - "weekly"
            description: Optional description.
            start_date: Optional ISO date (YYYY-MM-DD) when the
                budget's first period begins. When omitted, falls
                back to January 1 of ``year``. Use this to author
                a historical budget for comparison against past
                actuals (e.g. ``start_date="2024-01-01"``) or to
                start mid-year.
        """
        book = get_book()
        result = book.create_budget(
            name=name,
            year=year,
            num_periods=num_periods,
            period_type=period_type,
            description=description,
            start_date=start_date,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="update", entity_type="budget")
    def set_budget_amount(
        budget_name: str,
        account: str,
        amount: str,
        period: int | str | None = None,
    ) -> str:
        """Set a budget target for an account.

        Args:
            budget_name: Name of the budget.
            account: Account ref: full path (e.g., "Expenses:Groceries"), %short GUID, or full 32-char GUID.
            amount: Monthly budget amount as string (e.g., "500.00").
            period: Which period(s) to set:
                - None or "all": Set same amount for all periods (default)
                - Integer 0-11: Set specific period (0 = January for yearly budget)
                - "q1", "q2", "q3", "q4": Set all periods in quarter
        """
        book = get_book()
        result = book.set_budget_amount(
            budget_name=budget_name,
            account=account,
            amount=amount,
            period=period,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def get_budget_report(
        budget_name: str,
        period: int | str | None = None,
        account: str | None = None,
        include_children: bool = True,
        verbose: bool = False,
    ) -> str:
        """Compare actual spending against budget.

        Returns a compact text table by default with ⚠ markers on
        categories exceeding budget. Use verbose=true for the full
        structured dict.

        Args:
            budget_name: Name of the budget.
            period: Which period to report:
                - None: Current period based on today's date (default)
                - Integer 0-11: Specific period
                - "ytd": Year to date (all periods up to current)
                - "all": All periods
            account: Optional filter to specific account or parent account.
            include_children: If True and account specified, include child accounts.
            verbose: If true, return the structured dict.
        """
        book = get_book()
        result = book.get_budget_report(
            budget_name=budget_name,
            period=period,
            account=account,
            include_children=include_children,
            compact=not verbose,
        )
        if verbose:
            return _json(result)
        return result

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="delete", entity_type="budget")
    def delete_budget(name: str) -> str:
        """Delete a budget.

        Args:
            name: Budget name.
        """
        book = get_book()
        result = book.delete_budget(name=name)
        return _json(result)
