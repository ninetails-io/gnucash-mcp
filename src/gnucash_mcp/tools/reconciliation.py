"""Reconciliation tools: split state, unreconciled splits, account reconciliation, void/unvoid."""

from datetime import date
from typing import Annotated

from pydantic import Field

from gnucash_mcp.logging_config import audit_log
from gnucash_mcp.tools._helpers import (
    SplitGuid,
    TransactionGuid,
    _json,
    _parse_iso_date,
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
        rec_date = _parse_iso_date(reconcile_date)
        result = book.set_reconcile_state(
            split_guid=split_guid,
            state=state,
            reconcile_date=rec_date,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def get_reconciliation_status(
        verbose: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> str:
        """Per-account reconciliation table behind the dashboard's
        aggregate counts — answers "WHICH accounts are never
        reconciled / dormant / behind?"

        One line per reconcilable account with activity, bucketed
        exactly as the dashboard classifies them: ``behind``
        (most-behind first, with pending-split counts), ``never``,
        ``current``, ``dormant`` ($0, fully reconciled, idle), and
        ``excluded`` (opted out via the account's ``no_reconcile``
        slot — the right setting for loans, escrow payables, and
        other statement-less accounts: set_account_slot(account,
        "no_reconcile", "1"). Reporting-only; reconcile tools still
        work on excluded accounts).

        Args:
            verbose: Full JSON rows instead of compact TSV lines.
            limit: Page size (default 50, max 250). 0 = count only.
            offset: 0-indexed first row to return.
        """
        book = get_book()
        result = book.get_reconciliation_status(
            compact=not verbose, limit=limit, offset=offset,
        )
        if verbose:
            return _json(result)
        return result

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def get_unreconciled_splits(
        account: str,
        as_of_date: str | None = None,
        verbose: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> str:
        """Get unreconciled splits for an account.

        Leads with a ``Showing X-Y of Z splits`` indicator, then one
        line per split, then a summary footer reflecting the **full**
        unreconciled set (so the headline is honest even when individual
        lines are clipped). Page with ``offset``; ``limit=0`` returns
        the count only.

        Use verbose=true for full JSON with split GUIDs, amounts, totals,
        and the ``showing`` indicator as a structured field.

        Args:
            account: Account ref: full path (e.g. 'Assets:Bank:Checking'), %short GUID, or full 32-char GUID
            as_of_date: Only include splits on or before this date (YYYY-MM-DD)
            verbose: If true, return full JSON details. Default compact one-line format.
            limit: Page size (default 50, max 250). 0 = count only.
            offset: 0-indexed first row to return (default 0).
        """
        book = get_book()
        date_obj = _parse_iso_date(as_of_date)
        result = book.get_unreconciled_splits(
            account_name=account,
            as_of_date=date_obj,
            compact=not verbose,
            limit=limit,
            offset=offset,
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
        split_guids: Annotated[
            list[str] | None,
            Field(
                description=(
                    "List of SPLIT GUIDs to reconcile (8+ char prefixes "
                    "accepted). These are NOT transaction GUIDs — get "
                    "them from get_unreconciled_splits. Required for "
                    "targeted mode; omit when using reconcile_all=true."
                ),
                default=None,
            ),
        ] = None,
        reconcile_all: Annotated[
            bool,
            Field(
                description=(
                    "When true, reconcile every unreconciled split on "
                    "the account dated on or before through_date "
                    "(default: statement_date). Avoids the ~300-token "
                    "GUID round-trip for statement workflows — enter "
                    "several months of transactions, then reconcile "
                    "each statement with just its date and balance. "
                    "Mutually exclusive with split_guids."
                ),
                default=False,
            ),
        ] = False,
        through_date: Annotated[
            str | None,
            Field(
                description=(
                    "Upper-date bound for reconcile_all (YYYY-MM-DD); "
                    "only splits with post_date <= through_date are "
                    "swept. DEFAULTS TO statement_date — pass a later "
                    "date explicitly to widen the sweep past the "
                    "statement."
                ),
                default=None,
            ),
        ] = None,
        except_guids: Annotated[
            list[str] | None,
            Field(
                description=(
                    "Optional list of split GUID prefixes to "
                    "exclude from the bulk reconcile. Useful for "
                    "\"everything on the statement except this "
                    "pending ACH\" — 2 tokens vs the 100+ a "
                    "full split_guids listing would cost. Only "
                    "valid with reconcile_all=true; prefixes "
                    "that don't resolve are silently ignored."
                ),
                default=None,
            ),
        ] = None,
    ) -> str:
        """Reconcile splits against a statement balance.

        SIGN CONVENTION: statement_balance is the ACCOUNT's balance
        in GnuCash's signs — for liability accounts (credit cards)
        a $5,000 owed balance is "-5000", not "5000".

        Two modes:

        - **Targeted** (`split_guids=[...]`): reconcile exactly the
          listed splits (SPLIT guids from get_unreconciled_splits,
          not transaction guids). Use when statement and book
          disagree and you need to pick a subset.
        - **Bulk** (`reconcile_all=true`): reconcile every
          unreconciled split dated on or before through_date
          (default: statement_date — a statement reconciliation is
          bounded by the statement). One call, no GUID round-trip.

        Both modes verify the resulting reconciled balance ties to
        statement_balance before mutating; mismatch rejects with
        the discrepancy amount.

        TYPICAL STATEMENT FLOW (credit card or bank): batch-enter
        the statement's transactions (create_transactions), then
        reconcile_all with the statement's closing date and balance.
        Multi-month catch-up: enter all months in one batch, then
        one reconcile_all per statement, oldest first — the
        through_date default keeps each sweep inside its own
        statement.

        Args:
            account: Account ref: full path (e.g. 'Assets:Bank:Checking'), %short GUID, or full 32-char GUID
            statement_date: Statement ending date (YYYY-MM-DD)
            statement_balance: Expected balance from statement (as string, e.g., '1234.56')
            split_guids: List of split GUIDs to reconcile (targeted mode). Omit for bulk mode.
            reconcile_all: When true, reconcile all unreconciled splits up to through_date.
            through_date: Date filter for bulk mode (YYYY-MM-DD); defaults to statement_date.
        """
        book = get_book()
        stmt_date = date.fromisoformat(statement_date)
        through = _parse_iso_date(through_date)
        result = book.reconcile_account(
            account_name=account,
            statement_date=stmt_date,
            statement_balance=statement_balance,
            split_guids=split_guids,
            reconcile_all=reconcile_all,
            through_date=through,
            except_guids=except_guids,
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
