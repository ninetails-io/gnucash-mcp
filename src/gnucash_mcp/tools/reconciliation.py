"""Reconciliation tools: split state, unreconciled splits, account reconciliation, void/unvoid."""

from datetime import date
from typing import Annotated

from pydantic import Field

from gnucash_mcp.logging_config import audit_log
from gnucash_mcp.tools._helpers import (
    SplitGuid,
    TransactionGuid,
    _json,
    safe_tool,
)


def register(mcp, get_book) -> None:
    """Attach reconciliation tools to the FastMCP server."""

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="set_state", entity_type="split")
    def set_reconcile_state(
        split_guid: SplitGuid,
        state: str,
        reconcile_date: str | None = None,
    ) -> str:
        """Set the reconciliation state for a split.

        Args:
            split_guid: GUID of the split to update (32-character hex string, or 8+ char prefix)
            state: New reconcile state: 'n' (new), 'c' (cleared), 'y' (reconciled)
            reconcile_date: Date in ISO format (YYYY-MM-DD). Required for 'y', defaults to today.
        """
        book = get_book()
        rec_date = date.fromisoformat(reconcile_date) if reconcile_date else None
        result = book.set_reconcile_state(
            split_guid=split_guid,
            state=state,
            reconcile_date=rec_date,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def get_unreconciled_splits(
        account: str,
        as_of_date: str | None = None,
        verbose: bool = False,
        limit: int = 50,
    ) -> str:
        """Get unreconciled splits for an account.

        Returns up to ``limit`` splits in a compact one-line-per-split
        format by default with a summary footer reflecting the **full**
        unreconciled set (so the headline is honest even when individual
        lines are clipped).

        Use verbose=true for full JSON with split GUIDs, amounts, totals,
        and the truncation notice as a structured field.

        Args:
            account: Account ref: full path (e.g. 'Assets:Bank:Checking'), %short GUID, or full 32-char GUID
            as_of_date: Only include splits on or before this date (YYYY-MM-DD)
            verbose: If true, return full JSON details. Default compact one-line format.
            limit: Maximum splits to return. Defaults to 50, capped at 250.
        """
        book = get_book()
        date_obj = date.fromisoformat(as_of_date) if as_of_date else None
        result = book.get_unreconciled_splits(
            account_name=account,
            as_of_date=date_obj,
            compact=not verbose,
            limit=limit,
        )
        if verbose:
            return _json(result)
        return result

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="reconcile", entity_type="split")
    def reconcile_account(
        account: str,
        statement_date: str,
        statement_balance: str,
        split_guids: Annotated[list[str], Field(description="List of split GUIDs to mark as reconciled (8+ char prefixes accepted)")],
    ) -> str:
        """Reconcile multiple splits against a statement balance.

        Marks all specified splits as reconciled if the resulting balance matches
        the statement balance. This is an atomic operation - either all splits are
        reconciled or none are.

        Args:
            account: Account ref: full path (e.g. 'Assets:Bank:Checking'), %short GUID, or full 32-char GUID
            statement_date: Statement ending date (YYYY-MM-DD)
            statement_balance: Expected balance from statement (as string, e.g., '1234.56')
            split_guids: List of split GUIDs to mark as reconciled (8+ char prefixes accepted)
        """
        book = get_book()
        stmt_date = date.fromisoformat(statement_date)
        result = book.reconcile_account(
            account_name=account,
            statement_date=stmt_date,
            statement_balance=statement_balance,
            split_guids=split_guids,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="void", entity_type="transaction")
    def void_transaction(
        guid: TransactionGuid,
        reason: str,
    ) -> str:
        """Void a transaction (proper accounting void, not delete).

        Voiding preserves the transaction for audit purposes but zeroes out
        all split values. Use this instead of delete when you need to maintain
        an audit trail.

        Args:
            guid: Transaction GUID to void (32-character hex string, or 8+ char prefix)
            reason: Reason for voiding (required for audit trail)
        """
        book = get_book()
        result = book.void_transaction(guid=guid, reason=reason)
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="unvoid", entity_type="transaction")
    def unvoid_transaction(
        guid: TransactionGuid,
    ) -> str:
        """Restore a voided transaction.

        Restores original split values and removes void markers.

        Args:
            guid: Transaction GUID to unvoid (32-character hex string, or 8+ char prefix)
        """
        book = get_book()
        result = book.unvoid_transaction(guid=guid)
        return _json(result)
