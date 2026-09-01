"""Scheduled transaction tools: recurring templates + instantiation."""

from gnucash_mcp.logging_config import audit_log
from gnucash_mcp.tools._helpers import (
    ScheduledTransactionGuid,
    SplitInput,
    _json,
    _splits_to_dicts,
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
        splits: list[SplitInput],
        start_date: str,
        frequency: str,
        end_date: str | None = None,
        enabled: bool = True,
        notes: str | None = None,
        currency: str | None = None,
    ) -> str:
        """Create a recurring transaction template.

        Args:
            name: Scheduled transaction name (e.g., "Monthly Rent").
            description: Transaction description at instantiation.
            splits: List of split dicts, e.g.
                ``[{"account": "Expenses:Rent", "amount": "1850.00"}, ...]``.
                ``amount`` / ``quantity`` must be decimal strings;
                ``quantity`` is required when an account's commodity
                differs from the template's transaction currency.
            start_date: First occurrence (YYYY-MM-DD).
            frequency: "weekly", "biweekly" (2w), "monthly",
                "bimonthly" (2mo), "quarterly" (3mo), or "yearly".
            end_date: Optional last occurrence (YYYY-MM-DD).
            enabled: Active. Default True.
            notes: Transaction notes applied to every instantiated
                transaction (what the payment is — visible in
                GnuCash's double-line register view).
            currency: ISO code denominating every instantiated
                transaction; defaults to the book default. Use when
                no leg is in the book currency (a USD-to-USD card
                payment scheduled inside a CNY book) so amounts are
                the foreign currency's own numbers. Not updatable
                after creation — delete and recreate to change it.
        """
        book = get_book()
        result = book.create_scheduled_transaction(
            name=name,
            description=description,
            splits=_splits_to_dicts(splits),
            start_date=start_date,
            frequency=frequency,
            end_date=end_date,
            enabled=enabled,
            notes=notes,
            currency=currency,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def list_scheduled_transactions(
        enabled_only: bool = True,
        verbose: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> str:
        """List all scheduled transactions.

        Leads with a ``Showing X-Y of Z scheduled transactions`` line,
        then a compact one-line-per-schedule format by default. Page with
        ``offset``; ``limit=0`` returns the count only. Use verbose=true
        for structured JSON with GUIDs, splits, dates, etc.

        Args:
            enabled_only: If True, only show enabled schedules. Default True.
            verbose: If false (default), compact text output — optimized
                for reading and token efficiency. If true, structured
                JSON, for when you need machine-readable fields rather
                than a report.
            limit: Page size (default 50, max 250). 0 = count only.
            offset: 0-indexed first row to return (default 0).
        """
        book = get_book()
        result = book.list_scheduled_transactions(
            enabled_only=enabled_only,
            compact=not verbose,
            limit=limit,
            offset=offset,
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
        limit: int = 50,
        offset: int = 0,
    ) -> str:
        """Get scheduled transactions due within a time window.

        This is the "what bills are coming up?" query. Leads with a
        ``Showing X-Y of Z upcoming transactions (date range)`` line,
        soonest first. Page with ``offset``; ``limit=0`` returns the
        count only.

        Args:
            days: Look ahead window in days. Default 14.
            verbose: If false (default), compact text output — optimized
                for reading and token efficiency. If true, structured
                JSON, for when you need machine-readable fields rather
                than a report.
            limit: Page size (default 50, max 250). 0 = count only.
            offset: 0-indexed first row to return (default 0).
        """
        book = get_book()
        result = book.get_upcoming_transactions(
            days=days, compact=not verbose, limit=limit, offset=offset
        )
        if verbose:
            return _json(result)
        return result

    @mcp.tool()
    @safe_tool
    @audit_log(
        classification="write", operation="create_from_scheduled",
        entity_type="transaction",
    )
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
        notes: str | None = None,
    ) -> str:
        """Update a scheduled transaction.

        Args:
            guid: Scheduled transaction GUID (or 8+ char prefix).
            enabled: Enable or disable.
            end_date: Three-state field for the schedule's end date.

                - Omit (or pass ``null``): leave unchanged.
                - Pass ``"YYYY-MM-DD"``: set to that date.
                - Pass ``""`` (empty string): clear the existing
                  end date back to "no end" (open-ended schedule).

                The empty-string sentinel exists because MCP tool
                schemas don't easily express "set to null" as a
                distinct value from "no change supplied" — both
                arrive as Python ``None``. Empty-string is the
                explicit "clear it" signal.
            notes: Instantiation notes applied to transactions
                created from this schedule going forward (existing
                transactions untouched). Same three-state
                convention as end_date: text to set, ``""`` to
                clear, omit to leave unchanged.
        """
        book = get_book()
        result = book.update_scheduled_transaction(
            guid=guid,
            enabled=enabled,
            end_date=end_date,
            notes=notes,
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
