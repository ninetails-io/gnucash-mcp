"""MCP server definition for GnuCash."""

import json
import os
from datetime import date

from mcp.server.fastmcp import FastMCP

from gnucash_mcp.book import GnuCashBook

# Create FastMCP server
mcp = FastMCP("gnucash-mcp")

# Global book instance - initialized on first use
_book: GnuCashBook | None = None


def get_book() -> GnuCashBook:
    """Get or create the GnuCashBook instance."""
    global _book
    if _book is None:
        path = os.environ.get("GNUCASH_BOOK_PATH")
        if not path:
            raise ValueError("GNUCASH_BOOK_PATH environment variable not set")
        _book = GnuCashBook(path)
    return _book


# ============== Tools ==============


@mcp.tool()
def list_accounts() -> str:
    """List all accounts in the GnuCash chart of accounts."""
    book = get_book()
    result = book.list_accounts()
    return json.dumps(result, indent=2)


@mcp.tool()
def get_account(name: str) -> str:
    """Get details for a specific account by name.

    Args:
        name: Full account name (e.g., 'Assets:Bank:Checking')
    """
    book = get_book()
    result = book.get_account(name)
    if result is None:
        return json.dumps({"error": f"Account not found: {name}"})
    return json.dumps(result, indent=2)


@mcp.tool()
def get_balance(account_name: str, as_of_date: str | None = None) -> str:
    """Get the balance of an account, optionally as of a specific date.

    Args:
        account_name: Full account name (e.g., 'Assets:Bank:Checking')
        as_of_date: Date in ISO format (YYYY-MM-DD). Defaults to current date.
    """
    book = get_book()
    try:
        date_obj = date.fromisoformat(as_of_date) if as_of_date else None
        balance = book.get_balance(account_name, date_obj)
        result = {
            "account": account_name,
            "balance": str(balance),
            "as_of_date": as_of_date if as_of_date else "current",
        }
        return json.dumps(result, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def list_transactions(
    account: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 50,
) -> str:
    """List transactions with optional filters.

    Args:
        account: Filter by account name
        start_date: Start date in ISO format (YYYY-MM-DD)
        end_date: End date in ISO format (YYYY-MM-DD)
        limit: Maximum number of transactions to return (default 50)
    """
    book = get_book()
    try:
        start = date.fromisoformat(start_date) if start_date else None
        end = date.fromisoformat(end_date) if end_date else None
        result = book.list_transactions(account, start, end, limit)
        return json.dumps(result, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_transaction(guid: str) -> str:
    """Get details for a specific transaction by GUID.

    Args:
        guid: Transaction GUID (32-character hex string)
    """
    book = get_book()
    result = book.get_transaction(guid)
    if result is None:
        return json.dumps({"error": f"Transaction not found: {guid}"})
    return json.dumps(result, indent=2)


@mcp.tool()
def create_transaction(
    description: str,
    splits: list[dict],
    transaction_date: str | None = None,
) -> str:
    """Create a new transaction with splits. Splits must balance to zero.

    Args:
        description: Transaction description
        splits: List of splits. Each split has 'account' (name) and 'amount' (string)
        transaction_date: Transaction date in ISO format (YYYY-MM-DD). Defaults to today.
    """
    book = get_book()
    try:
        trans_date = date.fromisoformat(transaction_date) if transaction_date else None
        guid = book.create_transaction(
            description=description,
            splits=splits,
            trans_date=trans_date,
        )
        return json.dumps({"guid": guid, "status": "created"}, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def search_transactions(query: str, field: str = "description") -> str:
    """Search transactions by description, memo, or amount.

    Args:
        query: Search query string. For amount, supports: exact ("100"), greater (">100"), less ("<100"), range ("100-200")
        field: Field to search: 'description', 'memo', or 'amount'
    """
    book = get_book()
    try:
        result = book.search_transactions(query, field)
        return json.dumps(result, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def create_account(
    name: str,
    account_type: str,
    parent: str,
    description: str = "",
    placeholder: bool = False,
) -> str:
    """Create a new account in the chart of accounts.

    Args:
        name: Account name (e.g., "AI Subscriptions")
        account_type: GnuCash account type (ASSET, BANK, CASH, CREDIT, EQUITY, EXPENSE, INCOME, LIABILITY, MUTUAL, STOCK)
        parent: Full path of parent account (e.g., "Expenses:Online Services")
        description: Optional description
        placeholder: If true, account is container-only. Default: false
    """
    book = get_book()
    try:
        result = book.create_account(
            name=name,
            account_type=account_type,
            parent=parent,
            description=description,
            placeholder=placeholder,
        )
        return json.dumps(result, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def delete_transaction(guid: str) -> str:
    """Delete a transaction by GUID.

    Args:
        guid: Transaction GUID (32-character hex string)
    """
    book = get_book()
    try:
        result = book.delete_transaction(guid)
        return json.dumps(result, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def update_transaction(
    guid: str,
    description: str | None = None,
    transaction_date: str | None = None,
    splits: list[dict] | None = None,
) -> str:
    """Update an existing transaction.

    Args:
        guid: Transaction GUID to update
        description: New transaction description (optional)
        transaction_date: New date in ISO format YYYY-MM-DD (optional)
        splits: List of split updates with 'account' and 'amount' (optional).
                Must match existing splits by account name and balance to zero.
    """
    book = get_book()
    try:
        trans_date = date.fromisoformat(transaction_date) if transaction_date else None
        result = book.update_transaction(
            guid=guid,
            description=description,
            trans_date=trans_date,
            splits=splits,
        )
        return json.dumps(result, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)})


# ============== Resources ==============


@mcp.resource("gnucash://accounts")
def accounts_resource() -> str:
    """Full chart of accounts from the GnuCash book."""
    book = get_book()
    accounts = book.list_accounts()
    return json.dumps(accounts, indent=2)


# ============== Main ==============


def main() -> None:
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
