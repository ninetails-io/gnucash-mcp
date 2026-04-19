"""Core tools: book summary, accounts, transactions, search.

Always registered — `_apply_module_filter` forces 'core' into the
enabled set. Kept in its own register() function for consistency
with the other modules so server.py can treat every module the same
way (pure lazy-load orchestration, no hardcoded imports).
"""

from datetime import date
from typing import Annotated

from pydantic import Field

from gnucash_mcp.logging_config import audit_log
from gnucash_mcp.tools._helpers import _json, safe_tool


def register(mcp, get_book) -> None:
    """Attach core tools to the FastMCP server."""

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def get_book_summary() -> str:
        """Get a compact overview of the entire GnuCash book.

        Returns book path, currency, account structure, transaction counts,
        key balances, net worth, commodities, and scheduled transactions
        in a single text response. Use this first to orient yourself.
        """
        book = get_book()
        return book.get_book_summary()

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def list_accounts(
        root: str | None = None,
        verbose: bool = False,
    ) -> str:
        """List all accounts in the GnuCash chart of accounts.

        Returns a compact one-line-per-account format by default.
        Use verbose=true for full JSON with guid, type, commodity, etc.

        Args:
            root: Filter to a subtree (e.g., "Expenses" for expense accounts only).
            verbose: If true, return full JSON details for each account.
        """
        book = get_book()
        result = book.list_accounts(root=root, compact=not verbose)
        if verbose:
            return _json(result)
        return result

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def get_account(name: str) -> str:
        """Get details for a specific account by name.

        Args:
            name: Full account name (e.g., 'Assets:Bank:Checking')
        """
        book = get_book()
        result = book.get_account(name)
        if result is None:
            return _json({"error": f"Account not found: {name}"})
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def get_balance(account_name: str, as_of_date: str | None = None) -> str:
        """Get the balance of an account, optionally as of a specific date.

        Args:
            account_name: Full account name (e.g., 'Assets:Bank:Checking')
            as_of_date: Date in ISO format (YYYY-MM-DD). Defaults to current date.
        """
        book = get_book()
        date_obj = date.fromisoformat(as_of_date) if as_of_date else None
        balance = book.get_balance(account_name, date_obj)
        result = {
            "account": account_name,
            "balance": str(balance),
            "as_of_date": as_of_date if as_of_date else "current",
        }
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def list_transactions(
        account: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 50,
        verbose: bool = False,
    ) -> str:
        """List transactions with optional filters.

        Returns a compact one-line-per-transaction format by default.
        Use verbose=true for full JSON with GUIDs, splits, reconcile state, etc.

        Args:
            account: Filter by account name
            start_date: Start date in ISO format (YYYY-MM-DD)
            end_date: End date in ISO format (YYYY-MM-DD)
            limit: Maximum number of transactions to return (default 50)
            verbose: If true, return full JSON details for each transaction.
        """
        book = get_book()
        start = date.fromisoformat(start_date) if start_date else None
        end = date.fromisoformat(end_date) if end_date else None
        result = book.list_transactions(account, start, end, limit, compact=not verbose)
        if verbose:
            return _json(result)
        return result

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def get_transaction(
        guid: Annotated[str, Field(description="Transaction GUID (32-character hex string, or 8+ char prefix)")],
    ) -> str:
        """Get details for a specific transaction by GUID.

        Args:
            guid: Transaction GUID (32-character hex string, or 8+ char prefix)
        """
        book = get_book()
        result = book.get_transaction(guid)
        if result is None:
            return _json({"error": f"Transaction not found: {guid}"})
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="create", entity_type="transaction")
    def create_transaction(
        description: str,
        splits: list[dict] | None = None,
        transaction_date: str | None = None,
        currency: str | None = None,
        notes: str | None = None,
        check_duplicates: bool = True,
        force_create: bool = False,
        dry_run: bool = False,
    ) -> str:
        """Create a new transaction with splits. Splits must balance to zero.

        Args:
            description: Transaction description
            splits: List of splits. Each split has:
                - 'account' (required): Full account path
                - 'amount' (required): Value in transaction currency
                - 'quantity' (optional): Amount in account's commodity. Required if
                  account commodity differs from transaction currency.
                - 'memo' (optional): Split memo
            transaction_date: Transaction date in ISO format (YYYY-MM-DD). Defaults to today.
            currency: ISO currency code for transaction (e.g., "USD", "EUR").
                      Defaults to book's default currency.
            notes: Transaction notes (optional). Free-text annotation stored
                   separately from description.
            check_duplicates: Run duplicate detection against existing transactions.
            force_create: Create even if HIGH confidence duplicates are found.
            dry_run: Run validation and dupe check, return proposal without writing.
        """
        book = get_book()
        trans_date = date.fromisoformat(transaction_date) if transaction_date else None
        result = book.create_transaction(
            description=description,
            splits=splits,
            trans_date=trans_date,
            currency=currency,
            notes=notes,
            check_duplicates=check_duplicates,
            force_create=force_create,
            dry_run=dry_run,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def search_transactions(query: str, field: str = "description", verbose: bool = False) -> str:
        """Search transactions by description, memo, notes, or amount.

        Returns a compact one-line-per-transaction format by default.
        Use verbose=true for full JSON with GUIDs, splits, reconcile state, etc.

        Args:
            query: Search query string. For amount, supports: exact ("100"), greater (">100"), less ("<100"), range ("100-200")
            field: Field to search: 'description', 'memo', 'notes', or 'amount'
            verbose: If true, return full JSON details for each transaction.
        """
        book = get_book()
        result = book.search_transactions(query, field, compact=not verbose)
        if verbose:
            return _json(result)
        return result

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="create", entity_type="account")
    def create_account(
        name: str,
        account_type: str,
        parent: str | None = None,
        description: str = "",
        placeholder: bool = False,
        commodity: str | None = None,
        commodity_namespace: str = "CURRENCY",
    ) -> str:
        """Create a new account in the chart of accounts.

        Args:
            name: Account name (e.g., "AI Subscriptions")
            account_type: GnuCash account type (ASSET, BANK, CASH, CREDIT, EQUITY, EXPENSE, INCOME, LIABILITY, MUTUAL, STOCK, RECEIVABLE, PAYABLE)
            parent: Full path of parent account (e.g., "Expenses:Online Services").
                If omitted, creates a top-level account at the book root.
            description: Optional description
            placeholder: If true, account is container-only. Default: false
            commodity: Symbol for the account's commodity:
                - For currencies: ISO code (e.g., "USD", "EUR")
                - For investments: Fund/stock symbol (e.g., "VTSAX", "AAPL")
                Defaults to book's default currency.
            commodity_namespace: Namespace of the commodity:
                - "CURRENCY" (default) for currencies
                - "FUND" for mutual funds
                - "NASDAQ", "NYSE", etc. for stocks
                Required when commodity is not a currency.
        """
        book = get_book()
        result = book.create_account(
            name=name,
            account_type=account_type,
            parent=parent,
            description=description,
            placeholder=placeholder,
            commodity=commodity,
            commodity_namespace=commodity_namespace,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="update", entity_type="account")
    def update_account(
        name: str,
        new_name: str | None = None,
        description: str | None = None,
        placeholder: bool | None = None,
        account_type: str | None = None,
    ) -> str:
        """Update an existing account's properties.

        Args:
            name: Full account path to update (e.g., "Expenses:Groceries")
            new_name: New name for the account (just the leaf name, not full path)
            description: New description
            placeholder: New placeholder status (true = container only)
            account_type: New account type (e.g., "CREDIT", "BANK"). Only changes
                within the same debit/credit polarity are allowed — e.g.,
                LIABILITY to CREDIT, ASSET to BANK. Cross-polarity changes
                (e.g., ASSET to LIABILITY) are blocked.
        """
        book = get_book()
        result = book.update_account(
            name=name,
            new_name=new_name,
            description=description,
            placeholder=placeholder,
            account_type=account_type,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="update", entity_type="account")
    def move_account(name: str, new_parent: str) -> str:
        """Move an account to a new parent in the hierarchy.

        Args:
            name: Full account path to move (e.g., "Expenses:Old:Account")
            new_parent: Full path of the new parent account (e.g., "Expenses:New")
        """
        book = get_book()
        result = book.move_account(name=name, new_parent=new_parent)
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="delete", entity_type="account")
    def delete_account(name: str) -> str:
        """Delete an account from the chart of accounts.

        Safeguards prevent deletion if the account has children or transactions.

        Args:
            name: Full account path to delete (e.g., "Expenses:Old Category")
        """
        book = get_book()
        result = book.delete_account(name=name)
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="delete", entity_type="transaction")
    def delete_transaction(
        guid: Annotated[str, Field(description="Transaction GUID (32-character hex string, or 8+ char prefix)")],
        force: bool = False,
    ) -> str:
        """Delete a transaction by GUID.

        Safeguards prevent deletion if the transaction has reconciled splits.
        Use force=true to override.

        Args:
            guid: Transaction GUID (32-character hex string, or 8+ char prefix)
            force: Allow deleting transactions with reconciled splits
        """
        book = get_book()
        result = book.delete_transaction(guid, force=force)
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="update", entity_type="transaction")
    def update_transaction(
        guid: Annotated[str, Field(description="Transaction GUID to update (32-character hex string, or 8+ char prefix)")],
        description: str | None = None,
        transaction_date: str | None = None,
        splits: list[dict] | None = None,
        notes: str | None = None,
        force: bool = False,
    ) -> str:
        """Update an existing transaction.

        Args:
            guid: Transaction GUID to update (32-character hex string, or 8+ char prefix)
            description: New transaction description (optional)
            transaction_date: New date in ISO format YYYY-MM-DD (optional)
            splits: List of split updates with 'account' and 'amount' (optional).
                    Must match existing splits by account name and balance to zero.
                    For cross-currency splits, include 'quantity' (amount in account's commodity).
            notes: New transaction notes (optional). Pass empty string to clear.
            force: Allow modifying transactions with reconciled splits
        """
        book = get_book()
        trans_date = date.fromisoformat(transaction_date) if transaction_date else None
        result = book.update_transaction(
            guid=guid,
            description=description,
            trans_date=trans_date,
            splits=splits,
            notes=notes,
            force=force,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="replace_splits", entity_type="transaction")
    def replace_splits(
        guid: Annotated[str, Field(description="Transaction GUID (32-character hex string, or 8+ char prefix)")],
        splits: list[dict],
        force: bool = False,
    ) -> str:
        """Replace all splits in a transaction with a new set.

        Replace all splits in a transaction with a completely new set.
        The transaction's currency, description, date, and notes are preserved.
        New splits must balance to zero.

        Args:
            guid: Transaction GUID (32-character hex string, or 8+ char prefix)
            splits: Complete new set of splits. Each split needs:
                - 'account' (required): Full account path
                - 'amount' (required): Value in transaction currency
                - 'quantity' (optional): Amount in account's commodity.
                  Required if account commodity differs from transaction currency.
                - 'memo' (optional): Split memo
            force: Allow replacing reconciled splits or splits in lots
        """
        book = get_book()
        result = book.replace_splits(
            guid=guid,
            splits=splits,
            force=force,
        )
        return _json(result)
