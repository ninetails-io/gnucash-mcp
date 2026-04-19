"""Scheduled transaction tools: recurring templates + instantiation."""

from gnucash_mcp.logging_config import audit_log
from gnucash_mcp.tools._helpers import (
    ScheduledTransactionGuid,
    _json,
    safe_tool,
)


def register(mcp, get_book) -> None:
    """Attach scheduled transaction tools to the FastMCP server."""

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="create", entity_type="scheduled_transaction")
    def create_scheduled_transaction(
        name: str,
        description: str,
        splits: list[dict],
        start_date: str,
        frequency: str,
        end_date: str | None = None,
        enabled: bool = True,
    ) -> str:
        """Create a recurring transaction template.

        Args:
            name: Scheduled transaction name (e.g., "Monthly Rent").
            description: Transaction description at instantiation.
            splits: Same format as create_transaction, e.g.
                ``[{"account": "Expenses:Rent", "amount": "1850.00"}, ...]``.
            start_date: First occurrence (YYYY-MM-DD).
            frequency: "weekly", "biweekly" (2w), "monthly",
                "bimonthly" (2mo), "quarterly" (3mo), or "yearly".
            end_date: Optional last occurrence (YYYY-MM-DD).
            enabled: Active. Default True.
        """
        book = get_book()
        result = book.create_scheduled_transaction(
            name=name,
            description=description,
            splits=splits,
            start_date=start_date,
            frequency=frequency,
            end_date=end_date,
            enabled=enabled,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def list_scheduled_transactions(
        enabled_only: bool = True,
        verbose: bool = False,
    ) -> str:
        """List all scheduled transactions.

        Returns a compact one-line-per-schedule format by default.
        Use verbose=true for full JSON with GUIDs, splits, dates, etc.

        Args:
            enabled_only: If True, only show enabled schedules. Default True.
            verbose: If true, return full JSON details for each scheduled transaction.
        """
        book = get_book()
        result = book.list_scheduled_transactions(
            enabled_only=enabled_only,
            compact=not verbose,
        )
        if verbose:
            return _json(result)
        return result

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def get_upcoming_transactions(
        days: int = 14,
        verbose: bool = False,
    ) -> str:
        """Get scheduled transactions due within a time window.

        This is the "what bills are coming up?" query.

        Args:
            days: Look ahead window in days. Default 14.
            verbose: If true, return full JSON with splits. Default compact one-line format.
        """
        book = get_book()
        result = book.get_upcoming_transactions(days=days, compact=not verbose)
        if verbose:
            return _json(result)
        return result

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="create", entity_type="transaction")
    def create_transaction_from_scheduled(
        guid: ScheduledTransactionGuid,
        transaction_date: str | None = None,
    ) -> str:
        """Create an actual transaction from a scheduled template.

        Args:
            guid: Scheduled transaction GUID (or 8+ char prefix).
            transaction_date: Date for the transaction. Defaults to next occurrence.
        """
        book = get_book()
        result = book.create_transaction_from_scheduled(
            guid=guid,
            transaction_date=transaction_date,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="update", entity_type="scheduled_transaction")
    def update_scheduled_transaction(
        guid: ScheduledTransactionGuid,
        enabled: bool | None = None,
        end_date: str | None = None,
    ) -> str:
        """Update a scheduled transaction.

        Args:
            guid: Scheduled transaction GUID (or 8+ char prefix).
            enabled: Enable or disable.
            end_date: Set end date (empty string to clear).
        """
        book = get_book()
        result = book.update_scheduled_transaction(
            guid=guid,
            enabled=enabled,
            end_date=end_date,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="delete", entity_type="scheduled_transaction")
    def delete_scheduled_transaction(
        guid: ScheduledTransactionGuid,
    ) -> str:
        """Delete a scheduled transaction.

        Does not affect transactions already created from this schedule.

        Args:
            guid: Scheduled transaction GUID (or 8+ char prefix).
        """
        book = get_book()
        result = book.delete_scheduled_transaction(guid=guid)
        return _json(result)
